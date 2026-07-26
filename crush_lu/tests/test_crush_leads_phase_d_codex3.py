""""My Crush!" Phase D — Codex round-3 review findings.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md

Nine findings on commit ``a389767``, all against the routed-coach workspace
and the reminder sweep added earlier in the phase. The through-line is that
Phase D shipped the *queue* and the *SLA* before the controls they key off,
so several of these are the second half of a fix rather than a new one:

- the recipient's answer had no writer when there is no live co-coach, so
  ``can_share_contacts`` could never become true (P1, a hard deadlock);
- a deactivated ``recipient_coach`` left the same hole behind a non-null
  column (P1);
- workspace writes did not require ownership, so a pool lead could be worked
  by anyone (P1);
- a decline was not terminal against the legacy ``approve`` handler;
- correcting a completed call did not withdraw the completion;
- a pre-``shared`` crush lead could still accumulate coach messages;
- routed-coach audit appends were unlocked;
- the sweep re-checked only the reminder stamp under the row lock;
- the sweep's batch cap did not fit the caller's time budget.

Run with: pytest crush_lu/tests/test_crush_leads_phase_d_codex3.py -v
"""
from datetime import date, timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventConnection,
    MeetupEvent,
    UserDataConsent,
)
from crush_lu.services import crush_leads
from crush_lu.services.crush_leads import REMINDER_AFTER, sweep_lead_reminders

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


def _make_user(username, gender="M"):
    user = User.objects.create_user(
        username=username, email=username, password="testpass123"
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender=gender,
        location="Luxembourg",
        is_approved=True,
        is_active=True,
    )
    return user, profile


def _make_coach(username, is_active=True):
    user = User.objects.create_user(
        username=username, email=username, password="coachpass123"
    )
    return CrushCoach.objects.create(user=user, bio="Test coach", is_active=is_active)


def _make_event(title="Codex R3 Event"):
    return MeetupEvent.objects.create(
        title=title,
        description="Event for Codex round-3 tests",
        event_type="mixer",
        date_time=timezone.now() - timedelta(hours=2),
        location="Luxembourg",
        address="1 Test Street",
        max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=3),
        is_published=True,
    )


def _lead(requester, recipient, event, coach=None, **kwargs):
    kwargs.setdefault("flow", EventConnection.FLOW_CRUSH)
    lead = EventConnection.objects.create(
        requester=requester, recipient=recipient, event=event, **kwargs
    )
    if coach is not None:
        lead.assigned_coach = coach
        lead.save(update_fields=["assigned_coach"])
    return lead


def _login(client, user):
    # The consent middleware turns an unconsented POST into a redirect, so a
    # test asserting "nothing changed" would otherwise pass vacuously.
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    client.force_login(user)


def _review_url(lead):
    return reverse(
        "crush_lu:coach_connection_review", kwargs={"connection_id": lead.pk}
    )


def _notes(response):
    return [str(m) for m in response.context["messages"]]


class TestRecipientAnswerHasAWriter:
    """P1: with no live co-coach nothing could write
    `recipient_consents_to_share`, so `can_share_contacts` stayed false and
    the lead could never reach `shared` — a hard deadlock, not a nuisance."""

    def _same_coach_lead(self, coach_active=True):
        """Both halves on one coach: `assign_recipient_coach` returns None."""
        coach = _make_coach("r3_solo@example.com", is_active=coach_active)
        requester, req_p = _make_user("r3_solo_req@example.com", "M")
        recipient, rec_p = _make_user("r3_solo_rec@example.com", "F")
        for profile in (req_p, rec_p):
            profile.assigned_coach = coach
            profile.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event())
        lead.assign_coach()
        lead.assign_recipient_coach()
        lead.refresh_from_db()
        return coach, lead

    def test_no_cocoach_means_the_routed_coach_records_the_answer(self):
        coach, lead = self._same_coach_lead()
        assert lead.recipient_coach is None
        assert lead.has_active_recipient_coach is False

        client = Client()
        _login(client, coach.user)
        client.post(_review_url(lead), {"action": "crush_start_review"})
        client.post(
            _review_url(lead),
            {"action": "crush_record_recipient_consent", "consent": "yes"},
        )

        lead.refresh_from_db()
        assert lead.recipient_consents_to_share is True
        assert lead.recipient_response == EventConnection.RECIPIENT_RESPONSE_CONSENTED

    def test_the_lead_can_actually_reach_shared_without_a_cocoach(self):
        """The deadlock itself: every step through the UI, ending at
        `shared`. Before the fix this could not terminate at all."""
        coach, lead = self._same_coach_lead()
        client = Client()
        _login(client, coach.user)
        url = _review_url(lead)

        client.post(url, {"action": "crush_start_review"})
        client.post(url, {"action": "crush_complete_call", "call_outcome": "completed"})
        client.post(url, {"action": "crush_record_consent", "consent": "yes"})
        client.post(
            url, {"action": "crush_record_recipient_consent", "consent": "yes"}
        )
        client.post(url, {"action": "approve"})
        client.post(url, {"action": "crush_share"})

        lead.refresh_from_db()
        assert lead.status == "shared"
        assert lead.shared_at is not None

    def test_a_decline_recorded_this_way_still_ends_the_lead(self):
        coach, lead = self._same_coach_lead()
        client = Client()
        _login(client, coach.user)
        client.post(_review_url(lead), {"action": "crush_start_review"})

        client.post(
            _review_url(lead),
            {"action": "crush_record_recipient_consent", "consent": "no"},
        )

        lead.refresh_from_db()
        assert lead.status == "declined"
        assert lead.recipient_consents_to_share is False

    def test_the_answer_backfills_outreach_like_the_cocoach_surface_does(self):
        coach, lead = self._same_coach_lead()
        client = Client()
        _login(client, coach.user)
        client.post(_review_url(lead), {"action": "crush_start_review"})

        client.post(
            _review_url(lead),
            {"action": "crush_record_recipient_consent", "consent": "yes"},
        )

        lead.refresh_from_db()
        assert lead.recipient_outreach_at is not None
        inferred = [
            a for a in lead.system_actions if a["type"] == "recipient_outreach"
        ]
        # Audited as inferred, so the trail still distinguishes a back-fill
        # from a coach explicitly recording that they made contact.
        assert inferred and inferred[0]["details"].get("inferred") is True

    def test_the_answer_is_final_here_too(self):
        coach, lead = self._same_coach_lead()
        client = Client()
        _login(client, coach.user)
        client.post(_review_url(lead), {"action": "crush_start_review"})
        client.post(
            _review_url(lead),
            {"action": "crush_record_recipient_consent", "consent": "yes"},
        )

        response = client.post(
            _review_url(lead),
            {"action": "crush_record_recipient_consent", "consent": "no"},
            follow=True,
        )

        lead.refresh_from_db()
        assert lead.recipient_consents_to_share is True
        assert lead.status != "declined"
        assert any("already recorded" in m for m in _notes(response))

    def test_the_routed_coach_cannot_answer_over_a_live_cocoach(self):
        """When a co-coach exists the answer is theirs to record — this
        control must not become a way around the recipient's own coach."""
        routed = _make_coach("r3_routed@example.com")
        cocoach = _make_coach("r3_cocoach@example.com")
        requester, req_p = _make_user("r3_req@example.com", "M")
        recipient, rec_p = _make_user("r3_rec@example.com", "F")
        req_p.assigned_coach = routed
        req_p.save(update_fields=["assigned_coach"])
        rec_p.assigned_coach = cocoach
        rec_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event())
        lead.assign_coach()
        lead.refresh_from_db()
        assert lead.recipient_coach == cocoach

        client = Client()
        _login(client, routed.user)
        client.post(_review_url(lead), {"action": "crush_start_review"})
        response = client.post(
            _review_url(lead),
            {"action": "crush_record_recipient_consent", "consent": "yes"},
            follow=True,
        )

        lead.refresh_from_db()
        assert lead.recipient_consents_to_share is False
        assert any("their answer" in m or "own coach" in m for m in _notes(response))


class TestDeactivatedRecipientCoachDoesNotStrandTheLead:
    """P1: `coach_required` bars a deactivated co-coach from every view while
    the exact-owner filter hid the task from everyone else, so the recipient's
    answer had no route at all — the same deadlock behind a non-null column."""

    def _lead_with_stale_cocoach(self):
        routed = _make_coach("r3_stale_routed@example.com")
        cocoach = _make_coach("r3_stale_co@example.com")
        requester, req_p = _make_user("r3_stale_req@example.com", "M")
        recipient, rec_p = _make_user("r3_stale_rec@example.com", "F")
        req_p.assigned_coach = routed
        req_p.save(update_fields=["assigned_coach"])
        rec_p.assigned_coach = cocoach
        rec_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event())
        lead.assign_coach()
        lead.refresh_from_db()
        assert lead.recipient_coach == cocoach
        # The co-coach leaves after routing.
        cocoach.is_active = False
        cocoach.save(update_fields=["is_active"])
        lead.refresh_from_db()
        return routed, lead

    def test_a_stale_cocoach_reads_as_no_cocoach(self):
        _, lead = self._lead_with_stale_cocoach()
        assert lead.recipient_coach is not None
        assert lead.has_active_recipient_coach is False

    def test_the_routed_coach_picks_up_the_stranded_answer(self):
        routed, lead = self._lead_with_stale_cocoach()
        client = Client()
        _login(client, routed.user)
        client.post(_review_url(lead), {"action": "crush_start_review"})

        client.post(
            _review_url(lead),
            {"action": "crush_record_recipient_consent", "consent": "yes"},
        )

        lead.refresh_from_db()
        assert lead.recipient_consents_to_share is True

    def test_the_control_is_offered_on_the_page(self):
        routed, lead = self._lead_with_stale_cocoach()
        client = Client()
        _login(client, routed.user)
        client.post(_review_url(lead), {"action": "crush_start_review"})

        body = client.get(_review_url(lead)).content.decode()

        assert "crush_record_recipient_consent" in body


class TestWorkspaceWritesRequireOwnership:
    """P1: the outer guard only rejects a lead routed to *someone else*, so on
    an unrouted pool lead every workspace write was open to any active coach."""

    def _pool_lead(self):
        owner = _make_coach("r3_pool_owner@example.com")
        stranger = _make_coach("r3_pool_stranger@example.com")
        requester, _ = _make_user("r3_pool_req@example.com", "M")
        recipient, _ = _make_user("r3_pool_rec@example.com", "F")
        lead = _lead(requester, recipient, _make_event())
        assert lead.assigned_coach is None
        return owner, stranger, lead

    @pytest.mark.parametrize(
        "payload",
        [
            {"action": "crush_complete_call", "call_outcome": "completed"},
            {"action": "crush_record_consent", "consent": "yes"},
            {"action": "crush_share"},
            {"action": "crush_schedule_call", "scheduled_at": "2030-01-01T10:00"},
        ],
    )
    def test_an_unclaimed_lead_refuses_every_workspace_write(self, payload):
        _, stranger, lead = self._pool_lead()
        client = Client()
        _login(client, stranger.user)

        response = client.post(_review_url(lead), payload, follow=True)

        lead.refresh_from_db()
        assert lead.coach_call_completed_at is None
        assert lead.coach_call_scheduled_at is None
        assert lead.requester_consents_to_share is False
        assert lead.status != "shared"
        assert lead.system_actions == []
        assert any("Claim" in m for m in _notes(response))

    def test_claiming_by_starting_review_is_still_allowed(self):
        """The triage path stays open — that was the intentional part."""
        _, stranger, lead = self._pool_lead()
        client = Client()
        _login(client, stranger.user)

        client.post(_review_url(lead), {"action": "crush_start_review"})

        lead.refresh_from_db()
        assert lead.assigned_coach == stranger
        assert lead.status == "coach_reviewing"

    def test_the_page_does_not_offer_controls_it_will_refuse(self):
        _, stranger, lead = self._pool_lead()
        client = Client()
        _login(client, stranger.user)

        body = client.get(_review_url(lead)).content.decode()

        assert "crush_complete_call" not in body
        assert "crush_share" not in body


class TestDeclinesAreTerminal:
    """P2: `approve` set `coach_approved` unconditionally, so a stale page
    could reopen a lead the co-coach had just declined."""

    def _declined_lead(self):
        routed = _make_coach("r3_term_routed@example.com")
        requester, req_p = _make_user("r3_term_req@example.com", "M")
        recipient, _ = _make_user("r3_term_rec@example.com", "F")
        req_p.assigned_coach = routed
        req_p.save(update_fields=["assigned_coach"])
        lead = _lead(
            requester,
            recipient,
            _make_event(),
            routed,
            status="declined",
            recipient_response=EventConnection.RECIPIENT_RESPONSE_DECLINED,
        )
        return routed, lead

    def test_approve_cannot_reopen_a_declined_crush_lead(self):
        routed, lead = self._declined_lead()
        client = Client()
        _login(client, routed.user)

        response = client.post(_review_url(lead), {"action": "approve"}, follow=True)

        lead.refresh_from_db()
        assert lead.status == "declined"
        assert lead.coach_approved_at is None
        assert any("closed" in m for m in _notes(response))

    def test_a_legacy_declined_row_keeps_its_old_behaviour(self):
        """The guard is crush-only — legacy rows are not in this flow and
        were not part of the finding."""
        routed, lead = self._declined_lead()
        lead.flow = EventConnection.FLOW_LEGACY
        lead.save(update_fields=["flow"])
        client = Client()
        _login(client, routed.user)

        client.post(_review_url(lead), {"action": "approve"})

        lead.refresh_from_db()
        assert lead.status == "coach_approved"

    def test_the_workspace_also_refuses_a_closed_lead(self):
        routed, lead = self._declined_lead()
        client = Client()
        _login(client, routed.user)

        response = client.post(
            _review_url(lead),
            {"action": "crush_record_consent", "consent": "yes"},
            follow=True,
        )

        lead.refresh_from_db()
        assert lead.requester_consents_to_share is False
        assert any("closed" in m for m in _notes(response))


class TestCorrectingACompletedCall:
    """P2: re-logging a non-completed outcome re-armed the reminder but left
    `coach_call_completed_at` set, so the lead stayed out of
    `open_crush_leads()` and the re-armed reminder could never fire."""

    def _reviewing_lead(self):
        coach = _make_coach("r3_call_coach@example.com")
        requester, req_p = _make_user("r3_call_req@example.com", "M")
        recipient, _ = _make_user("r3_call_rec@example.com", "F")
        req_p.assigned_coach = coach
        req_p.save(update_fields=["assigned_coach"])
        lead = _lead(
            requester, recipient, _make_event(), coach, status="coach_reviewing"
        )
        return coach, lead

    def test_a_non_completed_correction_withdraws_the_completion(self):
        coach, lead = self._reviewing_lead()
        client = Client()
        _login(client, coach.user)
        client.post(
            _review_url(lead),
            {"action": "crush_complete_call", "call_outcome": "completed"},
        )
        lead.refresh_from_db()
        assert lead.coach_call_completed_at is not None

        client.post(
            _review_url(lead),
            {"action": "crush_complete_call", "call_outcome": "no_answer"},
        )

        lead.refresh_from_db()
        assert lead.call_outcome == "no_answer"
        assert lead.coach_call_completed_at is None

    def test_the_corrected_lead_comes_back_onto_the_queue(self):
        """The point of the correction: a lead logged completed by mistake
        has to become workable again, not vanish with a re-armed reminder."""
        coach, lead = self._reviewing_lead()
        client = Client()
        _login(client, coach.user)
        client.post(
            _review_url(lead),
            {"action": "crush_complete_call", "call_outcome": "completed"},
        )
        assert not EventConnection.objects.crush_leads_for_coach(coach).exists()

        client.post(
            _review_url(lead),
            {"action": "crush_complete_call", "call_outcome": "unreachable"},
        )

        assert EventConnection.objects.crush_leads_for_coach(coach).filter(
            pk=lead.pk
        ).exists()

    def test_completing_still_closes_the_sla(self):
        coach, lead = self._reviewing_lead()
        client = Client()
        _login(client, coach.user)

        client.post(
            _review_url(lead),
            {"action": "crush_complete_call", "call_outcome": "completed"},
        )

        lead.refresh_from_db()
        assert lead.coach_call_completed_at is not None
        assert not EventConnection.objects.crush_leads_for_coach(coach).exists()


class TestNoMessagesBeforeTheIntroduction:
    """P2: non-`crush_` actions fell through to `send_message`, so a coach
    could write thread rows while the pair still knew nothing of each other."""

    def _lead(self, status="coach_reviewing"):
        coach = _make_coach("r3_msg_coach@example.com")
        requester, req_p = _make_user("r3_msg_req@example.com", "M")
        recipient, _ = _make_user("r3_msg_rec@example.com", "F")
        req_p.assigned_coach = coach
        req_p.save(update_fields=["assigned_coach"])
        return coach, _lead(
            requester, recipient, _make_event(), coach, status=status
        )

    def test_a_pre_shared_crush_lead_refuses_a_coach_message(self):
        coach, lead = self._lead()
        client = Client()
        _login(client, coach.user)

        response = client.post(
            _review_url(lead),
            {"action": "send_message", "message": "Hello there"},
            follow=True,
        )

        assert lead.messages.count() == 0
        assert any("introduction" in m for m in _notes(response))

    def test_the_form_is_not_rendered_before_the_introduction(self):
        coach, lead = self._lead()
        client = Client()
        _login(client, coach.user)

        body = client.get(_review_url(lead)).content.decode()

        assert 'value="send_message"' not in body

    def test_a_shared_lead_opens_the_thread(self):
        coach, lead = self._lead(status="shared")
        client = Client()
        _login(client, coach.user)

        client.post(
            _review_url(lead), {"action": "send_message", "message": "Introduced!"}
        )

        assert lead.messages.count() == 1

    def test_a_legacy_row_is_unaffected(self):
        coach, lead = self._lead()
        lead.flow = EventConnection.FLOW_LEGACY
        lead.save(update_fields=["flow"])
        client = Client()
        _login(client, coach.user)

        client.post(
            _review_url(lead), {"action": "send_message", "message": "Legacy note"}
        )

        assert lead.messages.count() == 1


class TestRoutedCoachAuditIsLocked:
    """P2: the workspace appended to `system_actions` from the instance loaded
    at request entry, so a concurrently committed co-coach entry was lost."""

    def test_a_concurrent_cocoach_entry_survives_a_workspace_write(self):
        routed = _make_coach("r3_audit_routed@example.com")
        cocoach = _make_coach("r3_audit_co@example.com")
        requester, req_p = _make_user("r3_audit_req@example.com", "M")
        recipient, rec_p = _make_user("r3_audit_rec@example.com", "F")
        req_p.assigned_coach = routed
        req_p.save(update_fields=["assigned_coach"])
        rec_p.assigned_coach = cocoach
        rec_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event(), status="coach_reviewing")
        lead.assign_coach()
        lead.refresh_from_db()

        # The instance this request read at entry, *before* the co-coach's
        # append — the only window in which the lock does any work. Mutating
        # the row before the POST would not exercise it: the entry read would
        # already carry the co-coach's entry.
        stale = EventConnection.objects.select_related(
            "requester", "recipient", "event", "assigned_coach"
        ).get(pk=lead.pk)
        lead.log_system_action("recipient_outreach", actor="cocoach:other")
        lead.save(update_fields=["system_actions"])
        assert stale.system_actions == []

        client = Client()
        _login(client, routed.user)
        with mock.patch(
            "crush_lu.views_coach.get_object_or_404", return_value=stale
        ):
            client.post(
                _review_url(lead), {"action": "crush_record_consent", "consent": "yes"}
            )

        lead.refresh_from_db()
        types = [a["type"] for a in lead.system_actions]
        # Both survive: the append-only trail is the point, and the business
        # field landing without its audit row defeats it.
        assert "recipient_outreach" in types
        assert "crush_requester_consent" in types
        assert lead.requester_consents_to_share is True


class TestSweepRechecksEligibilityUnderTheLock:
    """P2: only `reminder_sent_at` was revalidated on the locked row, so a
    call scheduled or completed since the scan still drew a reminder."""

    def _due_lead(self, coach=None):
        coach = coach or _make_coach("r3_sweep_coach@example.com")
        requester, req_p = _make_user("r3_sweep_req@example.com", "M")
        recipient, _ = _make_user("r3_sweep_rec@example.com", "F")
        req_p.assigned_coach = coach
        req_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event(), coach)
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(hours=1)
        )
        lead.refresh_from_db()
        return coach, lead

    def _sweep_racing(self, mutate):
        """Run a sweep in which ``mutate`` lands *after* the candidate scan
        and *before* any row lock — the only window the in-lock re-check
        covers. Mutating before the sweep would just drop the lead from
        ``reminder_candidates()`` and prove nothing about the re-check."""
        sent = []

        def notify(coach, lead):
            sent.append(lead.pk)
            return {"success": 1, "failed": 0, "total": 1}

        class _Scanned:
            """Stands in for the candidate queryset, firing the concurrent
            write at the moment the sweep finishes reading its ids."""

            def __init__(self, queryset):
                self._queryset = queryset

            def values_list(self, *args, **kwargs):
                ids = list(self._queryset.values_list(*args, **kwargs))
                mutate()
                return ids

        real = crush_leads.reminder_candidates
        with mock.patch.object(
            crush_leads,
            "reminder_candidates",
            lambda now=None: _Scanned(real(now)),
        ):
            return sweep_lead_reminders(notify=notify), sent

    def test_a_call_scheduled_since_the_scan_cancels_the_reminder(self):
        _, lead = self._due_lead()

        result, sent = self._sweep_racing(
            lambda: EventConnection.objects.filter(pk=lead.pk).update(
                coach_call_scheduled_at=timezone.now()
            )
        )

        assert sent == []
        assert result["sent"] == 0
        lead.refresh_from_db()
        # ...and the stamp is not consumed, so nothing is silently swallowed.
        assert lead.reminder_sent_at is None

    def test_a_lead_declined_since_the_scan_is_not_reminded(self):
        _, lead = self._due_lead()

        result, sent = self._sweep_racing(
            lambda: EventConnection.objects.filter(pk=lead.pk).update(
                status="declined"
            )
        )

        assert sent == []
        assert result["sent"] == 0

    def test_a_still_eligible_lead_is_reminded(self):
        """Positive control — the re-check must not swallow the normal path."""
        _, lead = self._due_lead()

        result, sent = self._sweep_racing(lambda: None)

        assert sent == [lead.pk]
        assert result["sent"] == 1
        lead.refresh_from_db()
        assert lead.reminder_sent_at is not None


class TestSweepFitsTheCallersTimeBudget:
    """P2: 200 leads x serial pushes at up to 10s each does not fit the Azure
    Function's 60s timeout, so the count cap alone left most of a batch late."""

    def _due_leads(self, count):
        coach = _make_coach("r3_budget_coach@example.com")
        event = _make_event()
        leads = []
        for i in range(count):
            requester, req_p = _make_user(f"r3_budget_req{i}@example.com", "M")
            recipient, _ = _make_user(f"r3_budget_rec{i}@example.com", "F")
            req_p.assigned_coach = coach
            req_p.save(update_fields=["assigned_coach"])
            lead = _lead(requester, recipient, event, coach)
            EventConnection.objects.filter(pk=lead.pk).update(
                requested_at=timezone.now() - REMINDER_AFTER - timedelta(hours=1)
            )
            leads.append(lead)
        return coach, leads

    def test_the_sweep_stops_when_the_budget_is_spent(self):
        _, leads = self._due_leads(4)
        ticks = iter([0, 0, 10, 20, 30, 40, 50, 60, 70])
        sent = []

        def notify(coach, lead):
            sent.append(lead.pk)
            return {"success": 1, "failed": 0, "total": 1}

        result = sweep_lead_reminders(
            notify=notify, time_budget=25, clock=lambda: next(ticks)
        )

        assert len(sent) < len(leads)
        assert result["truncated"] is True

    def test_the_leads_it_skipped_stay_queued(self):
        """Out of time is not out of work — the rest must remain eligible."""
        _, leads = self._due_leads(4)
        ticks = iter([0, 0, 10, 20, 30, 40, 50, 60, 70])

        sweep_lead_reminders(
            notify=lambda c, l: {"success": 1, "failed": 0, "total": 1},
            time_budget=25,
            clock=lambda: next(ticks),
        )

        unsent = [
            lead
            for lead in leads
            if EventConnection.objects.get(pk=lead.pk).reminder_sent_at is None
        ]
        assert unsent, "the truncated remainder must stay eligible"

    def test_a_sweep_inside_its_budget_is_not_truncated(self):
        _, leads = self._due_leads(3)

        result = sweep_lead_reminders(
            notify=lambda c, l: {"success": 1, "failed": 0, "total": 1},
            clock=lambda: 0,
        )

        assert result["sent"] == len(leads)
        assert result["truncated"] is False
