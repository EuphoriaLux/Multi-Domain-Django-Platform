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
        assert result == {"sent": 1, "failed": 0}
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
        assert result == {"sent": 0, "failed": 1}
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
        assert [a["type"] for a in lead.system_actions] == ["recipient_consent"]

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
        # ...and only the real write is audited.
        assert [a["type"] for a in lead.system_actions] == ["recipient_consent"]

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
