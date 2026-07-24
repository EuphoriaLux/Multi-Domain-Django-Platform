"""
"My Crush!" Phase D — coach UI & notifications tests.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md

Covers (Phase D scope):
- reciprocal crush leads stay independent: start_review/approve/claim never
  write through to the other coach's lead (§5 coach workflow)
- mutual-crush priority flagging, without disclosing the other note
- the 24h untouched-lead reminder sweep + its idempotency (§6/O8, §13)
- the recipient-side co-coach task: routing, constrained actions, the full
  different-coach path to consent, and what it must never render (§5)
- coach_member_overview redacts crush rows from non-routed coaches

Run with: pytest crush_lu/tests/test_crush_leads_phase_d.py -v
"""
from datetime import date, timedelta

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
from crush_lu.services.crush_leads import (
    REMINDER_AFTER,
    reminder_candidates,
    sweep_lead_reminders,
)

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
    return CrushCoach.objects.create(
        user=user, bio="Test coach", is_active=is_active
    )


def _make_event(title="Phase D Event"):
    return MeetupEvent.objects.create(
        title=title,
        description="Event for Phase D tests",
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
    # The crush.lu consent middleware blocks every non-exempt view, so a bare
    # force_login silently turns POSTs into redirects — and a test asserting
    # "nothing changed" would then pass without exercising anything.
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    client.force_login(user)


class TestReciprocalLeadsStayIndependent:
    """§5: reciprocal leads route on their own requesters, so writing through
    to the reverse row would hijack another coach's lead."""

    def _pair(self):
        coach_a = _make_coach("indep_a@example.com")
        coach_b = _make_coach("indep_b@example.com")
        user_a, _ = _make_user("indep_ua@example.com", "M")
        user_b, _ = _make_user("indep_ub@example.com", "F")
        event = _make_event()
        lead_ab = _lead(user_a, user_b, event, coach_a, status="accepted")
        lead_ba = _lead(user_b, user_a, event, coach_b, status="accepted")
        return coach_a, coach_b, lead_ab, lead_ba

    def _post(self, client, lead, action):
        return client.post(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            ),
            {"action": action},
        )

    def test_start_review_leaves_the_other_lead_untouched(self, client):
        coach_a, coach_b, lead_ab, lead_ba = self._pair()
        _login(client, coach_a.user)

        self._post(client, lead_ab, "start_review")

        lead_ba.refresh_from_db()
        assert lead_ba.status == "accepted"
        assert lead_ba.assigned_coach == coach_b

    def test_approve_leaves_the_other_lead_untouched(self, client):
        coach_a, coach_b, lead_ab, lead_ba = self._pair()
        _login(client, coach_a.user)

        self._post(client, lead_ab, "approve")

        lead_ab.refresh_from_db()
        lead_ba.refresh_from_db()
        assert lead_ab.status == "coach_approved"
        # The other coach's lead must not be promoted into a consent-open
        # status before that coach has made their own call.
        assert lead_ba.status == "accepted"
        assert lead_ba.assigned_coach == coach_b
        assert lead_ba.coach_approved_at is None
        assert lead_ba.coach_notes == ""

    def test_claim_leaves_the_other_lead_untouched(self, client):
        coach_a, coach_b, lead_ab, lead_ba = self._pair()
        lead_ab.assigned_coach = None
        lead_ab.save(update_fields=["assigned_coach"])
        _login(client, coach_a.user)

        self._post(client, lead_ab, "claim")

        lead_ab.refresh_from_db()
        lead_ba.refresh_from_db()
        assert lead_ab.assigned_coach == coach_a
        assert lead_ba.assigned_coach == coach_b

    def test_legacy_pairs_still_couple(self, client):
        """The bypass is crush-specific — legacy behaviour is unchanged."""
        coach = _make_coach("legacy_c@example.com")
        user_a, _ = _make_user("legacy_ua@example.com", "M")
        user_b, _ = _make_user("legacy_ub@example.com", "F")
        event = _make_event()
        fwd = _lead(
            user_a, user_b, event, coach,
            status="accepted", flow=EventConnection.FLOW_LEGACY,
        )
        rev = _lead(
            user_b, user_a, event, coach,
            status="accepted", flow=EventConnection.FLOW_LEGACY,
        )
        _login(client, coach.user)

        self._post(client, fwd, "start_review")

        rev.refresh_from_db()
        assert rev.status == "coach_reviewing"


class TestMutualCrushFlagging:
    """§5: mutual pairs are flagged to both coaches, never merged."""

    def test_annotation_flags_only_live_reciprocal_crush_leads(self):
        coach = _make_coach("mut_c@example.com")
        user_a, _ = _make_user("mut_ua@example.com", "M")
        user_b, _ = _make_user("mut_ub@example.com", "F")
        user_c, _ = _make_user("mut_uc@example.com", "F")
        event = _make_event()
        mutual = _lead(user_a, user_b, event, coach)
        _lead(user_b, user_a, event, coach)
        one_way = _lead(user_a, user_c, event, coach)

        flags = {
            c.pk: c.is_mutual_crush_annotated
            for c in EventConnection.objects.annotate_is_mutual_crush()
        }

        assert flags[mutual.pk] is True
        assert flags[one_way.pk] is False

    def test_declined_reciprocal_is_not_a_mutual_crush(self):
        coach = _make_coach("mut_d@example.com")
        user_a, _ = _make_user("mut_da@example.com", "M")
        user_b, _ = _make_user("mut_db@example.com", "F")
        event = _make_event()
        lead = _lead(user_a, user_b, event, coach)
        _lead(user_b, user_a, event, coach, status="declined")

        flagged = EventConnection.objects.annotate_is_mutual_crush().get(pk=lead.pk)

        assert flagged.is_mutual_crush_annotated is False

    def test_legacy_reciprocal_is_not_a_mutual_crush(self):
        coach = _make_coach("mut_l@example.com")
        user_a, _ = _make_user("mut_la@example.com", "M")
        user_b, _ = _make_user("mut_lb@example.com", "F")
        event = _make_event()
        lead = _lead(user_a, user_b, event, coach)
        _lead(user_b, user_a, event, coach, flow=EventConnection.FLOW_LEGACY)

        flagged = EventConnection.objects.annotate_is_mutual_crush().get(pk=lead.pk)

        assert flagged.is_mutual_crush_annotated is False

    def test_queue_prioritises_and_labels_a_mutual_pair(self, client):
        coach = _make_coach("mut_q@example.com")
        # The reciprocal lead routes on its own requester, so in the spec's
        # case it belongs to a different coach — this coach sees exactly one
        # mutual lead, flagged, plus one ordinary one-way lead.
        other_coach = _make_coach("mut_q2@example.com")
        user_a, _ = _make_user("mut_qa@example.com", "M")
        user_b, _ = _make_user("mut_qb@example.com", "F")
        user_c, _ = _make_user("mut_qc@example.com", "F")
        event = _make_event()
        mutual = _lead(user_a, user_b, event, coach)
        _lead(user_b, user_a, event, other_coach)
        one_way = _lead(user_a, user_c, event, coach)
        _login(client, coach.user)

        response = client.get(reverse("crush_lu:coach_action_queue"))

        assert response.status_code == 200
        assert response.context["counts"]["mutual_crush"] == 1
        by_id = {
            i["url_kwargs"]["connection_id"]: i
            for i in response.context["items"]
            if i["kind"] == "crush_lead"
        }
        # Same SLA state, but the mutual pair sorts ahead on priority.
        assert by_id[mutual.pk]["priority"] < by_id[one_way.pk]["priority"]
        assert by_id[mutual.pk]["is_mutual_crush"] is True


class TestReminderSweep:
    """§6/O8 + §13: the 24h untouched-lead reminder, and its idempotency."""

    _seq = 0

    def _overdue_lead(self, coach=None, **kwargs):
        # Unique per call: several tests build more than one lead, and
        # auth_user.email is unique.
        TestReminderSweep._seq += 1
        n = TestReminderSweep._seq
        coach = coach or _make_coach(f"rem_c{n}@example.com")
        requester, _ = _make_user(f"rem_r{n}@example.com", "M")
        recipient, _ = _make_user(f"rem_p{n}@example.com", "F")
        event = _make_event()
        lead = _lead(requester, recipient, event, coach, **kwargs)
        # requested_at is auto_now_add, so age it explicitly.
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(minutes=5)
        )
        lead.refresh_from_db()
        return coach, lead

    def test_overdue_lead_is_a_candidate_and_gets_reminded(self):
        coach, lead = self._overdue_lead()
        calls = []

        result = sweep_lead_reminders(notify=lambda c, l: calls.append((c, l)))

        lead.refresh_from_db()
        assert (result["sent"], result["failed"]) == (1, 0)
        assert calls == [(coach, lead)] or calls[0][1].pk == lead.pk
        assert lead.reminder_sent_at is not None

    def test_second_delivery_sends_nothing(self):
        """Two timer deliveries produce exactly one reminder."""
        _, lead = self._overdue_lead()
        calls = []
        notify = lambda c, l: calls.append(l.pk)  # noqa: E731

        sweep_lead_reminders(notify=notify)
        second = sweep_lead_reminders(notify=notify)

        assert second["sent"] == 0
        assert calls == [lead.pk]

    def test_a_scheduled_or_completed_call_is_never_swept(self):
        _, scheduled = self._overdue_lead(
            coach_call_scheduled_at=timezone.now()
        )
        assert not reminder_candidates().filter(pk=scheduled.pk).exists()

        _, completed = self._overdue_lead(
            coach_call_completed_at=timezone.now(),
        )
        assert not reminder_candidates().filter(pk=completed.pk).exists()

    def test_a_declined_lead_is_never_swept(self):
        """A member block or coach decline flips the lead to `declined`."""
        _, lead = self._overdue_lead(status="declined")
        assert not reminder_candidates().filter(pk=lead.pk).exists()

    def test_a_young_lead_is_not_yet_due(self):
        coach = _make_coach("rem_young@example.com")
        requester, _ = _make_user("rem_ya@example.com", "M")
        recipient, _ = _make_user("rem_yb@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), coach)
        assert not reminder_candidates().filter(pk=lead.pk).exists()

    def test_a_legacy_row_is_never_swept(self):
        _, lead = self._overdue_lead(flow=EventConnection.FLOW_LEGACY)
        assert not reminder_candidates().filter(pk=lead.pk).exists()

    def test_an_unrouted_pool_lead_is_never_swept(self):
        """Nobody to remind — it waits for triage instead."""
        requester, _ = _make_user("rem_pa@example.com", "M")
        recipient, _ = _make_user("rem_pb@example.com", "F")
        lead = _lead(requester, recipient, _make_event())
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(minutes=5)
        )
        assert not reminder_candidates().filter(pk=lead.pk).exists()

    def test_a_failed_notification_leaves_the_lead_eligible(self):
        """The stamp and the send share a savepoint, so a failure rolls the
        stamp back rather than silently swallowing the reminder."""
        _, lead = self._overdue_lead()

        def boom(coach, connection):
            raise RuntimeError("push down")

        result = sweep_lead_reminders(notify=boom)

        lead.refresh_from_db()
        assert (result["sent"], result["failed"]) == (0, 1)
        assert lead.reminder_sent_at is None
        assert reminder_candidates().filter(pk=lead.pk).exists()


class TestCoCoachOutreachTask:
    """§5: the recipient's coach gets a defined, constrained work item."""

    def _routed_pair(self, same_coach=False):
        routed = _make_coach("cc_routed@example.com")
        cocoach = routed if same_coach else _make_coach("cc_other@example.com")
        requester, req_profile = _make_user("cc_req@example.com", "M")
        recipient, rec_profile = _make_user("cc_rec@example.com", "F")
        req_profile.assigned_coach = routed
        req_profile.save(update_fields=["assigned_coach"])
        rec_profile.assigned_coach = cocoach
        rec_profile.save(update_fields=["assigned_coach"])
        event = _make_event()
        lead = _lead(requester, recipient, event, flow=EventConnection.FLOW_CRUSH)
        lead.assign_coach()
        lead.refresh_from_db()
        return routed, cocoach, lead

    def _url(self, lead):
        return reverse(
            "crush_lu:coach_crush_outreach_task",
            kwargs={"connection_id": lead.pk},
        )

    def test_routing_sets_the_recipient_coach(self):
        routed, cocoach, lead = self._routed_pair()
        assert lead.assigned_coach == routed
        assert lead.recipient_coach == cocoach

    def test_no_cocoach_task_when_both_sides_share_a_coach(self):
        """One person covers both halves — nothing to hand off."""
        _, _, lead = self._routed_pair(same_coach=True)
        assert lead.recipient_coach is None

    def test_the_task_never_names_the_crusher_or_their_note(self):
        _, cocoach, lead = self._routed_pair()
        lead.requester_note = "The private crush note."
        lead.save(update_fields=["requester_note"])
        client = Client()
        _login(client, cocoach.user)

        response = client.get(self._url(lead))

        assert response.status_code == 200
        assert b"The private crush note." not in response.content
        assert lead.requester.username.encode() not in response.content
        # ...but the recipient half it *is* scoped to does render.
        assert lead.recipient.username.encode() in response.content

    def test_an_unrelated_coach_gets_a_404(self):
        _, _, lead = self._routed_pair()
        stranger = _make_coach("cc_stranger@example.com")
        client = Client()
        _login(client, stranger.user)

        assert client.get(self._url(lead)).status_code == 404

    def test_the_routed_coach_cannot_use_the_cocoach_surface(self):
        """It is the recipient's coach's surface, not a second door in."""
        routed, _, lead = self._routed_pair()
        client = Client()
        _login(client, routed.user)

        assert client.get(self._url(lead)).status_code == 404

    def test_recording_outreach_is_audited_and_idempotent(self):
        _, cocoach, lead = self._routed_pair()
        client = Client()
        _login(client, cocoach.user)

        client.post(self._url(lead), {"action": "record_outreach"})
        lead.refresh_from_db()
        first = lead.recipient_outreach_at
        client.post(self._url(lead), {"action": "record_outreach"})
        lead.refresh_from_db()

        assert first is not None
        assert lead.recipient_outreach_at == first
        types = [a["type"] for a in lead.system_actions]
        assert types.count("recipient_outreach") == 1
        assert lead.system_actions[0]["actor"] == f"cocoach:{cocoach.user.username}"

    def test_consent_reaches_the_lead_and_is_audited(self):
        """The §7 share action reads recipient_consents_to_share, so the
        co-coach's recorded answer has to land there."""
        _, cocoach, lead = self._routed_pair()
        client = Client()
        _login(client, cocoach.user)

        client.post(self._url(lead), {"action": "record_consent"})

        lead.refresh_from_db()
        assert lead.recipient_response == (
            EventConnection.RECIPIENT_RESPONSE_CONSENTED
        )
        assert lead.recipient_consents_to_share is True
        assert lead.recipient_response_at is not None
        assert lead.status != "declined"
        # An answer implies the outreach happened, so it is back-filled and
        # audited as inferred — both entries are expected here.
        assert [a["type"] for a in lead.system_actions] == [
            "recipient_consent",
            "recipient_outreach",
        ]

    def test_decline_ends_the_lead(self):
        _, cocoach, lead = self._routed_pair()
        client = Client()
        _login(client, cocoach.user)

        client.post(self._url(lead), {"action": "record_decline"})

        lead.refresh_from_db()
        assert lead.recipient_response == (
            EventConnection.RECIPIENT_RESPONSE_DECLINED
        )
        assert lead.recipient_consents_to_share is False
        assert lead.status == "declined"

    def test_an_answer_is_final(self):
        _, cocoach, lead = self._routed_pair()
        client = Client()
        _login(client, cocoach.user)

        client.post(self._url(lead), {"action": "record_consent"})
        client.post(self._url(lead), {"action": "record_decline"})

        lead.refresh_from_db()
        assert lead.recipient_response == (
            EventConnection.RECIPIENT_RESPONSE_CONSENTED
        )
        assert lead.status != "declined"
        # ...and the rejected second POST adds nothing: only the accepted
        # answer plus its inferred outreach are audited.
        assert [a["type"] for a in lead.system_actions] == [
            "recipient_consent",
            "recipient_outreach",
        ]

    def test_a_rejected_correction_says_so_instead_of_claiming_success(self):
        """A stale page or double-click must not tell the coach their
        correction landed — the routed coach acts on this answer."""
        _, cocoach, lead = self._routed_pair()
        client = Client()
        _login(client, cocoach.user)
        client.post(self._url(lead), {"action": "record_consent"})

        response = client.post(
            self._url(lead), {"action": "record_decline"}, follow=True
        )

        notes = [str(m) for m in response.context["messages"]]
        assert any("already recorded" in m for m in notes)
        assert not any("Decline recorded" in m for m in notes)

    def test_the_task_appears_in_the_cocoach_inbox_only(self, client):
        routed, cocoach, lead = self._routed_pair()

        cocoach_client = Client()
        _login(cocoach_client, cocoach.user)
        cocoach_queue = cocoach_client.get(reverse("crush_lu:coach_action_queue"))

        routed_client = Client()
        _login(routed_client, routed.user)
        routed_queue = routed_client.get(reverse("crush_lu:coach_action_queue"))

        assert cocoach_queue.context["counts"]["crush_outreach"] == 1
        assert routed_queue.context["counts"]["crush_outreach"] == 0
        # The routed coach sees the lead itself instead.
        assert routed_queue.context["counts"]["crush_lead"] == 1
        entry = next(
            i
            for i in cocoach_queue.context["items"]
            if i["kind"] == "crush_outreach"
        )
        # Links to the constrained surface, never to the lead.
        assert entry["url_name"] == "crush_lu:coach_crush_outreach_task"

    def test_an_answered_task_leaves_the_inbox(self):
        _, cocoach, lead = self._routed_pair()
        client = Client()
        _login(client, cocoach.user)
        client.post(self._url(lead), {"action": "record_consent"})

        response = client.get(reverse("crush_lu:coach_action_queue"))

        assert response.context["counts"]["crush_outreach"] == 0

    def test_full_different_coach_path_reaches_consent_on_both_halves(self):
        """§13: the different-coach path completes — co-coach records the
        recipient's consent without ever opening the lead, and the routed
        coach's own view still owns the requester half."""
        routed, cocoach, lead = self._routed_pair()

        cocoach_client = Client()
        _login(cocoach_client, cocoach.user)
        cocoach_client.post(self._url(lead), {"action": "record_outreach"})
        cocoach_client.post(self._url(lead), {"action": "record_consent"})

        lead.refresh_from_db()
        lead.requester_consents_to_share = True
        lead.status = "coach_approved"
        lead.save(
            update_fields=["requester_consents_to_share", "status"]
        )
        lead.refresh_from_db()

        assert lead.recipient_consents_to_share is True
        # Both halves recorded, coach approved — the routed coach can share.
        assert lead.can_share_contacts is True
        # And the co-coach never gained access to the lead review view.
        assert cocoach_client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        ).status_code in (302, 404)


class TestMemberOverviewRedaction:
    """§7: an unrelated coach must not learn the crusher's identity from a
    member record."""

    def _lead_for_overview(self):
        routed = _make_coach("mo_routed@example.com")
        crusher, _ = _make_user("mo_crusher@example.com", "M")
        target, _ = _make_user("mo_target@example.com", "F")
        lead = _lead(crusher, target, _make_event(), routed)
        return routed, crusher, target, lead

    def _url(self, member):
        return reverse(
            "crush_lu:coach_member_overview", kwargs={"user_id": member.pk}
        )

    def test_unrelated_coach_sees_no_crush_row_on_the_recipient(self, client):
        _, crusher, target, _ = self._lead_for_overview()
        stranger = _make_coach("mo_stranger@example.com")
        _login(client, stranger.user)

        response = client.get(self._url(target))

        assert response.status_code == 200
        assert crusher.username.encode() not in response.content

    def test_routed_coach_still_sees_their_own_lead(self, client):
        routed, crusher, target, _ = self._lead_for_overview()
        _login(client, routed.user)

        response = client.get(self._url(target))

        assert response.status_code == 200
        assert crusher.username.encode() in response.content

    def test_a_shared_lead_is_a_completed_introduction_and_stays_visible(
        self, client
    ):
        _, crusher, target, lead = self._lead_for_overview()
        lead.status = "shared"
        lead.save(update_fields=["status"])
        stranger = _make_coach("mo_stranger2@example.com")
        _login(client, stranger.user)

        response = client.get(self._url(target))

        assert crusher.username.encode() in response.content

    def test_legacy_rows_are_unaffected(self, client):
        routed = _make_coach("mo_legacy@example.com")
        requester, _ = _make_user("mo_lreq@example.com", "M")
        target, _ = _make_user("mo_ltar@example.com", "F")
        _lead(
            requester, target, _make_event(), routed,
            flow=EventConnection.FLOW_LEGACY,
        )
        stranger = _make_coach("mo_lstranger@example.com")
        _login(client, stranger.user)

        response = client.get(self._url(target))

        assert requester.username.encode() in response.content


class TestReminderCommand:
    """The management command is what a dev/ops shell reaches for; the Azure
    timer drives the same sweep through the admin endpoint."""

    def _overdue(self):
        coach = _make_coach("cmd_c@example.com")
        requester, _ = _make_user("cmd_r@example.com", "M")
        recipient, _ = _make_user("cmd_p@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), coach)
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(minutes=5)
        )
        lead.refresh_from_db()
        return lead

    def test_dry_run_reports_without_recording(self):
        from io import StringIO

        from django.core.management import call_command

        lead = self._overdue()
        out = StringIO()

        call_command("send_crush_lead_reminders", "--dry-run", stdout=out)

        lead.refresh_from_db()
        assert f"lead #{lead.pk}" in out.getvalue()
        assert "Dry run" in out.getvalue()
        assert lead.reminder_sent_at is None

    def test_dry_run_is_quiet_when_nothing_is_due(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("send_crush_lead_reminders", "--dry-run", stdout=out)
        assert "No crush leads are due" in out.getvalue()


class TestCodexRound1Fixes:
    """Findings from the Codex review of this PR."""

    # --- P1: the global coach connection list leaked crush leads ---

    def _listed_lead(self):
        routed = _make_coach("cx_routed@example.com")
        crusher, _ = _make_user("cx_crusher@example.com", "M")
        target, _ = _make_user("cx_target@example.com", "F")
        lead = _lead(
            crusher, target, _make_event(), routed,
            requester_note="Codex round note.",
        )
        return routed, crusher, lead

    def _list_url(self):
        return reverse("crush_lu:coach_connections")

    def test_unrelated_coach_sees_no_crush_row_on_the_connections_page(self):
        """The routed-coach queue and the detail guard are moot if this page
        still renders every lead with its note."""
        _, crusher, _ = self._listed_lead()
        stranger = _make_coach("cx_stranger@example.com")
        client = Client()
        _login(client, stranger.user)

        response = client.get(self._list_url(), {"status": "all"})

        assert response.status_code == 200
        assert b"Codex round note." not in response.content
        assert crusher.username.encode() not in response.content

    def test_routed_coach_still_sees_their_lead_and_note(self):
        routed, crusher, _ = self._listed_lead()
        client = Client()
        _login(client, routed.user)

        response = client.get(self._list_url(), {"status": "all"})

        assert b"Codex round note." in response.content

    # --- P2: an unrouted pool lead exposed its note on the detail page ---

    def test_pool_lead_withholds_the_note_on_both_surfaces(self):
        triager = _make_coach("cx_triager@example.com")
        crusher, _ = _make_user("cx_pa@example.com", "M")
        target, _ = _make_user("cx_pb@example.com", "F")
        lead = _lead(
            crusher, target, _make_event(), requester_note="Pool note."
        )
        assert lead.assigned_coach is None
        client = Client()
        _login(client, triager.user)

        listing = client.get(self._list_url(), {"status": "all"})
        review = client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        )

        # Listed and openable so it stays claimable...
        assert listing.status_code == 200
        assert review.status_code == 200
        # ...but the note is shut on both until a coach owns it.
        assert b"Pool note." not in listing.content
        assert b"Pool note." not in review.content

    # --- P1: a failed push must not consume the reminder ---

    def _overdue_lead(self):
        coach = _make_coach("cx_rem@example.com")
        requester, _ = _make_user("cx_remr@example.com", "M")
        recipient, _ = _make_user("cx_remp@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), coach)
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(minutes=5)
        )
        lead.refresh_from_db()
        return lead

    def test_a_push_that_reports_failure_without_raising_is_a_failure(self):
        """send_coach_push_notification swallows delivery errors and reports
        them in its return value — the sweep has to read it, or the lead is
        stamped and never reminded again."""
        lead = self._overdue_lead()

        result = sweep_lead_reminders(
            notify=lambda c, ln: {"success": 0, "failed": 2, "total": 2}
        )

        lead.refresh_from_db()
        assert (result["sent"], result["failed"]) == (0, 1)
        assert lead.reminder_sent_at is None
        assert reminder_candidates().filter(pk=lead.pk).exists()

    def test_a_coach_with_no_opted_in_device_is_not_retried_forever(self):
        """total == 0 is an opt-out, not a delivery failure."""
        lead = self._overdue_lead()

        result = sweep_lead_reminders(
            notify=lambda c, ln: {"success": 0, "failed": 0, "total": 0}
        )

        lead.refresh_from_db()
        assert (result["sent"], result["failed"]) == (1, 0)
        assert lead.reminder_sent_at is not None

    def test_a_partially_delivered_push_counts_as_sent(self):
        lead = self._overdue_lead()

        result = sweep_lead_reminders(
            notify=lambda c, ln: {"success": 1, "failed": 1, "total": 2}
        )

        lead.refresh_from_db()
        assert (result["sent"], result["failed"]) == (1, 0)
        assert lead.reminder_sent_at is not None

    # --- P1: reminders must respect the per-device preference ---

    def test_reminder_targets_only_opted_in_devices(self, monkeypatch):
        """A device where the coach muted call reminders must not receive
        this — the body puts the requester's name on a lock screen."""
        from crush_lu import coach_notifications
        from crush_lu.models import CoachPushSubscription

        lead = self._overdue_lead()
        coach = lead.assigned_coach
        opted_in = CoachPushSubscription.objects.create(
            coach=coach,
            endpoint="https://push.example/opted-in",
            p256dh_key="k1",
            auth_key="a1",
            enabled=True,
            notify_screening_reminders=True,
        )
        CoachPushSubscription.objects.create(
            coach=coach,
            endpoint="https://push.example/muted",
            p256dh_key="k2",
            auth_key="a2",
            enabled=True,
            notify_screening_reminders=False,
        )

        captured = {}

        def fake_send(coach, title, body, url="/", tag="", subscriptions=None, **kw):
            captured["endpoints"] = sorted(s.endpoint for s in subscriptions)
            return {"success": 1, "failed": 0, "total": 1}

        monkeypatch.setattr(
            coach_notifications, "send_coach_push_notification", fake_send
        )

        coach_notifications.notify_coach_crush_lead_reminder(coach, lead)

        assert captured["endpoints"] == [opted_in.endpoint]

    # --- P1: co-coach writes after the lead closes ---

    def _cocoach_lead(self):
        routed = _make_coach("cx_cc_routed@example.com")
        cocoach = _make_coach("cx_cc_other@example.com")
        requester, req_profile = _make_user("cx_ccreq@example.com", "M")
        recipient, rec_profile = _make_user("cx_ccrec@example.com", "F")
        req_profile.assigned_coach = routed
        req_profile.save(update_fields=["assigned_coach"])
        rec_profile.assigned_coach = cocoach
        rec_profile.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event())
        lead.assign_coach()
        lead.refresh_from_db()
        return cocoach, lead

    def _task_url(self, lead):
        return reverse(
            "crush_lu:coach_crush_outreach_task",
            kwargs={"connection_id": lead.pk},
        )

    def test_a_closed_lead_rejects_a_direct_consent_post(self):
        """A member block or a routed-coach decline closes the lead; a
        bookmarked POST must not resurrect it and tell the routed coach to
        introduce a cancelled pair."""
        cocoach, lead = self._cocoach_lead()
        lead.status = "declined"
        lead.save(update_fields=["status"])
        client = Client()
        _login(client, cocoach.user)

        client.post(self._task_url(lead), {"action": "record_consent"})

        lead.refresh_from_db()
        assert lead.recipient_response is None
        assert lead.recipient_consents_to_share is False
        assert lead.system_actions == []

    def test_a_closed_lead_rejects_a_direct_outreach_post(self):
        cocoach, lead = self._cocoach_lead()
        lead.status = "declined"
        lead.save(update_fields=["status"])
        client = Client()
        _login(client, cocoach.user)

        client.post(self._task_url(lead), {"action": "record_outreach"})

        lead.refresh_from_db()
        assert lead.recipient_outreach_at is None

    def test_a_closed_lead_hides_its_actions(self):
        cocoach, lead = self._cocoach_lead()
        lead.status = "declined"
        lead.save(update_fields=["status"])
        client = Client()
        _login(client, cocoach.user)

        response = client.get(self._task_url(lead))

        assert response.status_code == 200
        assert response.context["lead_open"] is False
        assert b"They said yes" not in response.content

    # --- UI: the outreach controls must be visible in light mode ---

    def test_outreach_controls_use_canonical_button_classes(self):
        cocoach, lead = self._cocoach_lead()
        client = Client()
        _login(client, cocoach.user)

        content = client.get(self._task_url(lead)).content

        # `.btn-crush-secondary` is not a sanctioned variant (STYLE.md) and
        # renders white-on-white on this light card.
        assert b"btn-crush-secondary" not in content
        assert b"btn-crush-solid" in content
        assert b"btn-crush-outline" in content


class TestCrushLeadWorkspace:
    """Codex round 2 on this PR: a routed coach could see a lead but never
    work it, so nothing ever left the queue."""

    def _lead(self, status="pending"):
        coach = _make_coach("ws_coach@example.com")
        requester, _ = _make_user("ws_req@example.com", "M")
        recipient, _ = _make_user("ws_rec@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), coach, status=status)
        return coach, lead

    def _url(self, lead):
        return reverse(
            "crush_lu:coach_connection_review", kwargs={"connection_id": lead.pk}
        )

    def test_a_pending_lead_can_enter_review(self):
        """`pending` is the normal starting state — the legacy control only
        fires from `accepted`, which a crush lead never reaches."""
        coach, lead = self._lead()
        client = Client()
        _login(client, coach.user)

        client.post(self._url(lead), {"action": "crush_start_review"})

        lead.refresh_from_db()
        assert lead.status == "coach_reviewing"
        assert [a["type"] for a in lead.system_actions] == ["crush_start_review"]

    def test_completing_the_call_removes_the_lead_from_the_queue(self):
        """`coach_call_completed_at` is what open_crush_leads() keys off — if
        nothing writes it, a worked lead is queued and reminded forever."""
        coach, lead = self._lead(status="coach_reviewing")
        client = Client()
        _login(client, coach.user)
        assert EventConnection.objects.open_crush_leads().filter(pk=lead.pk).exists()

        client.post(
            self._url(lead),
            {"action": "crush_complete_call", "call_outcome": "completed"},
        )

        lead.refresh_from_db()
        assert lead.coach_call_completed_at is not None
        assert lead.call_outcome == "completed"
        assert not EventConnection.objects.open_crush_leads().filter(
            pk=lead.pk
        ).exists()

    def test_an_unreachable_call_keeps_the_lead_open_and_rearms_the_reminder(self):
        coach, lead = self._lead(status="coach_reviewing")
        lead.reminder_sent_at = timezone.now()
        lead.save(update_fields=["reminder_sent_at"])
        client = Client()
        _login(client, coach.user)

        client.post(
            self._url(lead),
            {"action": "crush_complete_call", "call_outcome": "no_answer"},
        )

        lead.refresh_from_db()
        assert lead.coach_call_completed_at is None
        assert lead.reminder_sent_at is None
        assert EventConnection.objects.open_crush_leads().filter(pk=lead.pk).exists()

    def test_an_invalid_outcome_is_rejected(self):
        coach, lead = self._lead(status="coach_reviewing")
        client = Client()
        _login(client, coach.user)

        client.post(
            self._url(lead),
            {"action": "crush_complete_call", "call_outcome": "nonsense"},
        )

        lead.refresh_from_db()
        assert lead.call_outcome is None

    def test_scheduling_a_call_records_the_time(self):
        coach, lead = self._lead(status="coach_reviewing")
        client = Client()
        _login(client, coach.user)

        client.post(
            self._url(lead),
            {"action": "crush_schedule_call", "scheduled_at": "2026-08-01T14:30"},
        )

        lead.refresh_from_db()
        assert lead.coach_call_scheduled_at is not None
        # The form posts a naive local datetime; it is localised on save and
        # stored as UTC, so compare in local time.
        local = timezone.localtime(lead.coach_call_scheduled_at)
        assert (local.hour, local.minute) == (14, 30)

    def test_the_coach_records_requester_consent(self):
        """The member-side consent form is closed for crush leads, so the
        routed coach is the only one who can record it."""
        coach, lead = self._lead(status="coach_reviewing")
        client = Client()
        _login(client, coach.user)

        client.post(
            self._url(lead), {"action": "crush_record_consent", "consent": "yes"}
        )

        lead.refresh_from_db()
        assert lead.requester_consents_to_share is True

    def test_share_is_refused_until_both_sides_consented(self):
        coach, lead = self._lead(status="coach_approved")
        lead.requester_consents_to_share = True
        lead.save(update_fields=["requester_consents_to_share"])
        client = Client()
        _login(client, coach.user)

        client.post(self._url(lead), {"action": "crush_share"})

        lead.refresh_from_db()
        assert lead.status == "coach_approved"

    def test_the_full_workflow_reaches_shared_through_the_ui(self):
        """§13: the advertised flow must complete without a test reaching
        into the model — the earlier version set these fields directly and
        masked the fact that no control existed."""
        coach, lead = self._lead()
        client = Client()
        _login(client, coach.user)

        client.post(self._url(lead), {"action": "crush_start_review"})
        client.post(
            self._url(lead),
            {"action": "crush_complete_call", "call_outcome": "completed"},
        )
        client.post(
            self._url(lead), {"action": "crush_record_consent", "consent": "yes"}
        )
        # The recipient half arrives via the co-coach task; approve is the
        # existing legacy control and works for crush rows unchanged.
        lead.refresh_from_db()
        lead.recipient_consents_to_share = True
        lead.save(update_fields=["recipient_consents_to_share"])
        client.post(self._url(lead), {"action": "approve"})
        client.post(self._url(lead), {"action": "crush_share"})

        lead.refresh_from_db()
        assert lead.status == "shared"
        assert lead.shared_at is not None
        types = [a["type"] for a in lead.system_actions]
        assert "crush_start_review" in types
        assert "crush_call_logged" in types
        assert "crush_requester_consent" in types
        assert "crush_shared" in types

    def test_a_legacy_row_rejects_the_crush_actions(self):
        coach = _make_coach("ws_legacy@example.com")
        requester, _ = _make_user("ws_lreq@example.com", "M")
        recipient, _ = _make_user("ws_lrec@example.com", "F")
        legacy = _lead(
            requester, recipient, _make_event(), coach,
            status="pending", flow=EventConnection.FLOW_LEGACY,
        )
        client = Client()
        _login(client, coach.user)

        client.post(self._url(legacy), {"action": "crush_start_review"})

        legacy.refresh_from_db()
        assert legacy.status == "pending"


class TestStaleCoachRecovery:
    """A lead routed to a coach who is later deactivated must not be
    stranded: that coach is barred from every coach view."""

    def _stranded(self):
        gone = _make_coach("stale_gone@example.com")
        active = _make_coach("stale_active@example.com")
        requester, _ = _make_user("stale_req@example.com", "M")
        recipient, _ = _make_user("stale_rec@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), gone)
        gone.is_active = False
        gone.save(update_fields=["is_active"])
        return active, lead

    def test_another_coach_can_see_and_open_a_stranded_lead(self):
        active, lead = self._stranded()
        client = Client()
        _login(client, active.user)

        listing = client.get(
            reverse("crush_lu:coach_connections"), {"status": "all"}
        )
        review = client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        )

        assert listing.status_code == 200
        assert review.status_code == 200
        assert review.context["lead_needs_claim"] is True

    def test_another_coach_can_claim_and_then_work_it(self):
        active, lead = self._stranded()
        client = Client()
        _login(client, active.user)
        url = reverse(
            "crush_lu:coach_connection_review", kwargs={"connection_id": lead.pk}
        )

        client.post(url, {"action": "claim"})
        client.post(url, {"action": "crush_start_review"})

        lead.refresh_from_db()
        assert lead.assigned_coach == active
        assert lead.status == "coach_reviewing"

    def test_an_actively_routed_lead_still_404s_for_others(self):
        """The relaxation is only for *inactive* coaches."""
        routed = _make_coach("stale_routed@example.com")
        other = _make_coach("stale_other@example.com")
        requester, _ = _make_user("stale_ra@example.com", "M")
        recipient, _ = _make_user("stale_rb@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), routed)
        client = Client()
        _login(client, other.user)

        response = client.get(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            )
        )

        assert response.status_code == 404


class TestCodexRound2Robustness:
    """The remaining P2s from the same round."""

    def test_an_answer_without_outreach_still_records_outreach(self):
        """The buttons allow answering before "Mark done", and the task then
        leaves the inbox — the SLA must not claim no outreach happened."""
        routed = _make_coach("rb_routed@example.com")
        cocoach = _make_coach("rb_cocoach@example.com")
        requester, req_p = _make_user("rb_req@example.com", "M")
        recipient, rec_p = _make_user("rb_rec@example.com", "F")
        req_p.assigned_coach = routed
        req_p.save(update_fields=["assigned_coach"])
        rec_p.assigned_coach = cocoach
        rec_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event())
        lead.assign_coach()
        lead.refresh_from_db()
        client = Client()
        _login(client, cocoach.user)

        client.post(
            reverse(
                "crush_lu:coach_crush_outreach_task",
                kwargs={"connection_id": lead.pk},
            ),
            {"action": "record_consent"},
        )

        lead.refresh_from_db()
        assert lead.recipient_response is not None
        assert lead.recipient_outreach_at is not None
        types = [a["type"] for a in lead.system_actions]
        assert "recipient_outreach" in types
        assert "recipient_consent" in types

    def test_missing_vapid_config_is_a_failure_not_an_opt_out(self):
        """A config error has the same zero-total shape as "nobody opted in",
        so without the flag every reminder due during the outage is
        permanently consumed."""
        coach = _make_coach("rb_vapid@example.com")
        requester, _ = _make_user("rb_va@example.com", "M")
        recipient, _ = _make_user("rb_vb@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), coach)
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(minutes=5)
        )

        result = sweep_lead_reminders(
            notify=lambda c, ln: {
                "success": 0, "failed": 0, "total": 0, "misconfigured": True
            }
        )

        lead.refresh_from_db()
        assert (result["sent"], result["failed"]) == (0, 1)
        assert lead.reminder_sent_at is None
        assert reminder_candidates().filter(pk=lead.pk).exists()

    def test_the_sweep_is_bounded_and_reports_truncation(self):
        """One stalled endpoint must not hold every row lock for the whole
        run, and a cap must never read as "swept everything"."""
        coach = _make_coach("rb_batch@example.com")
        event = _make_event()
        for i in range(3):
            requester, _ = _make_user(f"rb_ba{i}@example.com", "M")
            recipient, _ = _make_user(f"rb_bb{i}@example.com", "F")
            lead = _lead(requester, recipient, event, coach)
            EventConnection.objects.filter(pk=lead.pk).update(
                requested_at=timezone.now() - REMINDER_AFTER - timedelta(minutes=5)
            )

        result = sweep_lead_reminders(notify=lambda c, ln: None, limit=2)

        assert result["sent"] == 2
        assert result["truncated"] is True
        # The remainder is still eligible for the next run.
        assert reminder_candidates().count() == 1

    def test_a_declined_reciprocal_is_not_shown_as_a_mutual_match(self):
        """The legacy flow-blind annotation would tell the coach the
        recipient had declared a crush, after that lead closed."""
        coach = _make_coach("rb_mut@example.com")
        user_a, _ = _make_user("rb_ma@example.com", "M")
        user_b, _ = _make_user("rb_mb@example.com", "F")
        event = _make_event()
        lead = _lead(user_a, user_b, event, coach)
        _lead(user_b, user_a, event, coach, status="declined")
        client = Client()
        _login(client, coach.user)

        response = client.get(
            reverse("crush_lu:coach_connections"), {"status": "all"}
        )

        row = next(
            c for c in response.context["page_obj"].object_list if c.pk == lead.pk
        )
        assert row.is_mutual_annotated is False

    def test_a_live_reciprocal_crush_is_still_shown_as_mutual(self):
        coach = _make_coach("rb_mut2@example.com")
        user_a, _ = _make_user("rb_m2a@example.com", "M")
        user_b, _ = _make_user("rb_m2b@example.com", "F")
        event = _make_event()
        lead = _lead(user_a, user_b, event, coach)
        _lead(user_b, user_a, event, coach)
        client = Client()
        _login(client, coach.user)

        response = client.get(
            reverse("crush_lu:coach_connections"), {"status": "all"}
        )

        row = next(
            c for c in response.context["page_obj"].object_list if c.pk == lead.pk
        )
        assert row.is_mutual_annotated is True
