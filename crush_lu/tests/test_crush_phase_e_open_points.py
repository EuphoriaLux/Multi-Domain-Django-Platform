""""My Crush!" Phase E — the three open points from §11.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md

- **O12 / option 3** — unclaimed pool leads reach the SLA-tracked coach inbox.
  A member at a ``profile_requirement="none"`` event can declare a crush that
  routes to nobody; the lead was visible only on the connections page's
  *Pending* tab, which is not the default tab and carries no clock. The
  promised 48h call had no one attached to it.
- **O13** — ``recipient_coach`` backfill for leads declared before Phase D,
  as a re-runnable command rather than a data migration.
- **O14** — the reminder sweep's claim-then-send restructure, so a push
  helper's device-health writes survive a delivery failure instead of being
  rolled back with the reminder stamp.

Run with: pytest crush_lu/tests/test_crush_phase_e_open_points.py -v
"""
import json
from datetime import date, timedelta
from io import StringIO
from unittest import mock

import requests

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventConnection,
    MeetupEvent,
    ProfileSubmission,
    UserDataConsent,
)
from crush_lu.models.profiles import CoachPushSubscription
from crush_lu.services import crush_leads
from crush_lu.services.crush_leads import REMINDER_AFTER, sweep_lead_reminders

User = get_user_model()

# Real-shaped Web Push key material: a 65-byte uncompressed P-256 point and a
# 16-byte auth secret, base64url. `"k"`/`"a"` placeholders are now rejected by
# `subscription_key_fault` before the send, so a test using them would exercise
# the key-fault path while claiming to test transport handling.
VALID_P256DH = "BAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4fICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8"
VALID_AUTH = "AAECAwQFBgcICQoLDA0ODw"

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


def _make_event(title="Phase E Event"):
    return MeetupEvent.objects.create(
        title=title,
        description="Event for Phase E tests",
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
    # The consent middleware turns an unconsented request into a redirect, so
    # a test asserting "the row is absent" would otherwise pass vacuously.
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    client.force_login(user)


def _queue(coach):
    """The coach inbox as the view builds it, not as a template renders it."""
    client = Client()
    _login(client, coach.user)
    response = client.get(reverse("crush_lu:coach_action_queue"))
    assert response.status_code == 200
    return response


def _pool_ids(response):
    return [
        i["url_kwargs"]["connection_id"]
        for i in response.context["items"]
        if i["kind"] == "crush_pool"
    ]


def _lead_ids(response):
    return [
        i["url_kwargs"]["connection_id"]
        for i in response.context["items"]
        if i["kind"] == "crush_lead"
    ]


# ---------------------------------------------------------------------------
# O12 / option 3 — the unclaimed pool reaches the inbox
# ---------------------------------------------------------------------------


class TestPoolQueryset:
    """`crush_leads_in_pool()` is the queryset the inbox section rests on.
    Its contract is narrower than "everything without a coach": it is the
    *open, workable, ownerless* set."""

    def _pool_lead(self, suffix="a", **kwargs):
        requester, _ = _make_user(f"pe_pool_req_{suffix}@example.com", "M")
        recipient, _ = _make_user(f"pe_pool_rec_{suffix}@example.com", "F")
        return _lead(requester, recipient, _make_event(), **kwargs)

    def test_an_unrouted_open_lead_is_in_the_pool(self):
        lead = self._pool_lead()
        assert lead.assigned_coach_id is None
        assert list(EventConnection.objects.crush_leads_in_pool()) == [lead]

    def test_a_lead_routed_to_a_deactivated_coach_is_in_the_pool(self):
        """Non-null owner, but nobody is doing the work — the column reads as
        claimed while the lead is in fact abandoned."""
        coach = _make_coach("pe_pool_dead@example.com", is_active=False)
        lead = self._pool_lead("b", coach=coach)
        assert lead.assigned_coach_id is not None
        assert list(EventConnection.objects.crush_leads_in_pool()) == [lead]

    def test_a_claimed_lead_is_not_in_the_pool(self):
        coach = _make_coach("pe_pool_live@example.com")
        self._pool_lead("c", coach=coach)
        assert list(EventConnection.objects.crush_leads_in_pool()) == []

    def test_a_closed_lead_is_not_in_the_pool(self):
        self._pool_lead("d", status="declined")
        assert list(EventConnection.objects.crush_leads_in_pool()) == []

    def test_a_completed_call_leaves_the_pool(self):
        """The pool is unclaimed *work*, not unclaimed rows. A lead whose call
        is done needs no one to pick it up."""
        self._pool_lead("e", coach_call_completed_at=timezone.now())
        assert list(EventConnection.objects.crush_leads_in_pool()) == []

    def test_a_legacy_connection_is_never_in_the_pool(self):
        """Legacy rows have no coach and no call — without the flow filter the
        pool would fill with every historical connection request ever made."""
        self._pool_lead("f", flow=EventConnection.FLOW_LEGACY)
        assert list(EventConnection.objects.crush_leads_in_pool()) == []

    def test_the_pool_is_ordered_oldest_first(self):
        first = self._pool_lead("g")
        second = self._pool_lead("h")
        EventConnection.objects.filter(pk=first.pk).update(
            requested_at=timezone.now() - timedelta(days=1)
        )
        assert [lead.pk for lead in EventConnection.objects.crush_leads_in_pool()] == [
            first.pk,
            second.pk,
        ]


class TestPoolInTheInbox:
    """The queryset only matters if the inbox shows it, and shows it as
    *someone else's* — a pool row that reads like a routed one invites a coach
    to assume it is handled."""

    def _setup(self):
        coach = _make_coach("pe_inbox_coach@example.com")
        requester, _ = _make_user("pe_inbox_req@example.com", "M")
        recipient, _ = _make_user("pe_inbox_rec@example.com", "F")
        lead = _lead(requester, recipient, _make_event("Inbox Event"))
        return coach, lead

    def test_an_unclaimed_lead_appears_in_every_active_coachs_inbox(self):
        coach, lead = self._setup()
        other = _make_coach("pe_inbox_other@example.com")

        for c in (coach, other):
            assert _pool_ids(_queue(c)) == [lead.pk]

    def test_it_is_counted_separately_from_the_coachs_own_calls(self):
        """`crush_lead` is "mine"; folding the pool into it would tell a coach
        they have work they do not own."""
        coach, _lead = self._setup()

        response = _queue(coach)

        assert response.context["counts"]["crush_pool"] == 1
        assert response.context["counts"]["crush_lead"] == 0

    def test_it_carries_the_leads_own_sla_deadline(self):
        coach, lead = self._setup()

        item = next(
            i for i in _queue(coach).context["items"] if i["kind"] == "crush_pool"
        )

        assert item["deadline"] == lead.call_by

    def test_an_overdue_pool_lead_is_flagged_breached(self):
        coach, lead = self._setup()
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - EventConnection.CRUSH_LEAD_CALL_SLA
            - timedelta(hours=1)
        )

        item = next(
            i for i in _queue(coach).context["items"] if i["kind"] == "crush_pool"
        )

        assert item["sla_state"] == "breach"

    def test_the_requester_note_never_reaches_the_pool_row(self):
        """The note stays shut until a coach owns the lead — that is the whole
        privacy contract of the flow, and the pool is the one surface where a
        coach sees a lead that is not theirs."""
        coach, lead = self._setup()
        EventConnection.objects.filter(pk=lead.pk).update(
            requester_note="I have wanted to say this all night"
        )

        response = _queue(coach)

        item = next(i for i in response.context["items"] if i["kind"] == "crush_pool")
        assert "wanted to say this" not in item["subtitle"]
        assert b"wanted to say this" not in response.content

    def test_the_subtitle_distinguishes_never_routed_from_coach_inactive(self):
        """Different follow-up: one is a routing gap, the other is a coach who
        left with leads still on them."""
        coach, unrouted = self._setup()
        dead = _make_coach("pe_inbox_dead@example.com", is_active=False)
        requester, _ = _make_user("pe_inbox_req2@example.com", "M")
        recipient, _ = _make_user("pe_inbox_rec2@example.com", "F")
        orphaned = _lead(requester, recipient, _make_event("Orphan Event"), dead)

        items = {
            i["url_kwargs"]["connection_id"]: i["subtitle"]
            for i in _queue(coach).context["items"]
            if i["kind"] == "crush_pool"
        }

        assert "Unassigned" in items[unrouted.pk]
        assert "inactive" in items[orphaned.pk]

    def test_a_pool_lead_does_not_outrank_the_coachs_own_work(self):
        """Deliberately not priority-boosted. A lead nobody owns must not push
        past a call this coach personally committed to at the same urgency."""
        coach, pool_lead = self._setup()
        mine_req, mine_p = _make_user("pe_prio_req@example.com", "M")
        mine_rec, _ = _make_user("pe_prio_rec@example.com", "F")
        mine_p.assigned_coach = coach
        mine_p.save(update_fields=["assigned_coach"])
        mine = _lead(mine_req, mine_rec, _make_event("Mine Event"), coach)

        items = _queue(coach).context["items"]
        by_id = {i["url_kwargs"]["connection_id"]: i for i in items}

        # Same SLA state (both just declared), so the pool row must not sort
        # ahead of the owned one on priority alone.
        assert by_id[pool_lead.pk]["sla_state"] == by_id[mine.pk]["sla_state"]
        assert by_id[pool_lead.pk]["priority"] == by_id[mine.pk]["priority"]

    def test_a_claimed_lead_leaves_the_pool_and_joins_its_owners_queue(self):
        """The whole point of surfacing it: a coach claims it and it stops
        being everyone's problem."""
        coach, lead = self._setup()
        other = _make_coach("pe_claim_other@example.com")

        lead.assigned_coach = coach
        lead.save(update_fields=["assigned_coach"])

        mine = _queue(coach)
        assert _pool_ids(mine) == []
        assert _lead_ids(mine) == [lead.pk]
        assert _pool_ids(_queue(other)) == []


# ---------------------------------------------------------------------------
# O13 — the recipient_coach backfill command
# ---------------------------------------------------------------------------


class TestRecipientCoachBackfill:
    """A reconciliation command, not a data migration: it calls the real
    `assign_recipient_coach()` so the two-tier recipient rule cannot drift
    into a second copy."""

    def _pair(self, suffix, routed_coach, recipient_coach=None, submission_coach=None):
        requester, req_p = _make_user(f"pe_bf_req_{suffix}@example.com", "M")
        recipient, rec_p = _make_user(f"pe_bf_rec_{suffix}@example.com", "F")
        req_p.assigned_coach = routed_coach
        req_p.save(update_fields=["assigned_coach"])
        if recipient_coach is not None:
            rec_p.assigned_coach = recipient_coach
            rec_p.save(update_fields=["assigned_coach"])
        if submission_coach is not None:
            ProfileSubmission.objects.create(
                profile=rec_p, coach=submission_coach, status="approved"
            )
        lead = _lead(requester, recipient, _make_event(), routed_coach)
        return lead

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_crush_recipient_coaches", *args, stdout=out)
        return out.getvalue()

    def test_it_assigns_the_recipients_own_coach(self):
        routed = _make_coach("pe_bf_routed@example.com")
        theirs = _make_coach("pe_bf_theirs@example.com")
        lead = self._pair("a", routed, recipient_coach=theirs)

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id == theirs.pk

    def test_an_approved_submission_coach_wins_over_the_permanent_one(self):
        """Tier order, asserted here rather than assumed — it is the half of
        the rule a re-derivation would most likely get backwards."""
        routed = _make_coach("pe_bf_routed_b@example.com")
        permanent = _make_coach("pe_bf_perm@example.com")
        reviewer = _make_coach("pe_bf_reviewer@example.com")
        lead = self._pair(
            "b", routed, recipient_coach=permanent, submission_coach=reviewer
        )

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id == reviewer.pk

    def test_the_routed_coach_is_never_assigned_to_both_halves(self):
        """`None` here is the *correct* answer, not a miss: one person covers
        both halves, and a self-referential co-coach would disable the routed
        coach's own recipient-answer controls."""
        routed = _make_coach("pe_bf_solo@example.com")
        lead = self._pair("c", routed, recipient_coach=routed)

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None

    def test_an_inactive_recipient_coach_is_not_assigned(self):
        routed = _make_coach("pe_bf_routed_d@example.com")
        gone = _make_coach("pe_bf_gone@example.com", is_active=False)
        lead = self._pair("d", routed, recipient_coach=gone)

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None

    def test_a_recipient_with_no_coach_is_left_null(self):
        routed = _make_coach("pe_bf_routed_e@example.com")
        lead = self._pair("e", routed)

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None

    def test_dry_run_writes_nothing_but_reports_what_it_would_do(self):
        routed = _make_coach("pe_bf_routed_f@example.com")
        theirs = _make_coach("pe_bf_theirs_f@example.com")
        lead = self._pair("f", routed, recipient_coach=theirs)

        output = self._run("--dry-run")

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None
        assert "pe_bf_theirs_f@example.com" in output
        assert "Dry run" in output

    def test_it_is_idempotent(self):
        """It runs right after a deploy, and someone will run it twice."""
        routed = _make_coach("pe_bf_routed_g@example.com")
        theirs = _make_coach("pe_bf_theirs_g@example.com")
        lead = self._pair("g", routed, recipient_coach=theirs)

        self._run()
        lead.refresh_from_db()
        first = lead.recipient_coach_id

        second_output = self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id == first
        assert "No crush leads are missing" in second_output

    def test_it_does_not_touch_an_already_answered_lead(self):
        """The answer is recorded; a co-coach named now would own a task that
        no longer exists — the outreach queue filters on exactly this field."""
        routed = _make_coach("pe_bf_routed_h@example.com")
        theirs = _make_coach("pe_bf_theirs_h@example.com")
        lead = self._pair("h", routed, recipient_coach=theirs)
        EventConnection.objects.filter(pk=lead.pk).update(
            recipient_response=EventConnection.RECIPIENT_RESPONSE_DECLINED
        )

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None

    def test_it_does_not_touch_a_legacy_connection(self):
        routed = _make_coach("pe_bf_routed_i@example.com")
        theirs = _make_coach("pe_bf_theirs_i@example.com")
        lead = self._pair("i", routed, recipient_coach=theirs)
        EventConnection.objects.filter(pk=lead.pk).update(
            flow=EventConnection.FLOW_LEGACY
        )

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None

    def test_it_does_not_touch_a_closed_lead(self):
        routed = _make_coach("pe_bf_routed_j@example.com")
        theirs = _make_coach("pe_bf_theirs_j@example.com")
        lead = self._pair("j", routed, recipient_coach=theirs)
        EventConnection.objects.filter(pk=lead.pk).update(status="shared")

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None


# ---------------------------------------------------------------------------
# O14 — claim, then send, then release
# ---------------------------------------------------------------------------


class TestClaimThenSend:
    """The sweep used to stamp `reminder_sent_at` and push inside one
    `atomic()`, raising on failure so the stamp rolled back. That also rolled
    back the push helper's own writes — `mark_failure()` and the 410
    `delete()` — so a permanently dead endpoint never reached its
    five-failure auto-delete and was retried hourly forever."""

    def _due_lead(self):
        coach = _make_coach("pe_sweep_coach@example.com")
        requester, req_p = _make_user("pe_sweep_req@example.com", "M")
        recipient, _ = _make_user("pe_sweep_rec@example.com", "F")
        req_p.assigned_coach = coach
        req_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event(), coach)
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(hours=1)
        )
        lead.refresh_from_db()
        return coach, lead

    def _subscription(self, coach):
        return CoachPushSubscription.objects.create(
            coach=coach,
            endpoint="https://push.example.com/dead-endpoint",
            p256dh_key=VALID_P256DH,
            auth_key=VALID_AUTH,
        )

    def test_device_health_writes_survive_a_delivery_failure(self):
        """This is the whole point of O14. Under the old shape this assertion
        failed while every other sweep test still passed."""
        coach, _lead = self._due_lead()
        subscription = self._subscription(coach)

        def notify(c, l):
            # Exactly what `send_coach_push_notification` does: record the
            # device's failure, then *return* the zero-success result rather
            # than raising.
            subscription.mark_failure()
            return {"success": 0, "failed": 1, "total": 1}

        result = sweep_lead_reminders(notify=notify)

        subscription.refresh_from_db()
        assert subscription.failure_count == 1
        assert result["failed"] == 1

    def test_a_failed_delivery_still_releases_the_claim(self):
        """The guarantee the restructure had to keep: the coach is reminded on
        a later sweep rather than the reminder being swallowed."""
        _coach, lead = self._due_lead()

        sweep_lead_reminders(notify=lambda c, l: {"success": 0, "failed": 1, "total": 1})

        lead.refresh_from_db()
        assert lead.reminder_sent_at is None

    def test_a_raising_notifier_also_releases_the_claim(self):
        """The regression the restructure could have introduced. Committing
        the claim first means an exception no longer rolls it back, so the
        send has to be wrapped in its own handler — without it this lead would
        never be retried."""
        _coach, lead = self._due_lead()

        def notify(c, l):
            raise RuntimeError("push endpoint exploded")

        result = sweep_lead_reminders(notify=notify)

        lead.refresh_from_db()
        assert lead.reminder_sent_at is None
        assert result["failed"] == 1
        assert result["sent"] == 0

    def test_the_stamp_is_written_before_the_send(self):
        """Ordering guard: the stamp goes down *before* the push, which is
        what lets a second overlapping sweep find the row already claimed.

        It asserts the ordering, not the commit. Every test here shares one
        connection inside one transaction, so a stamp written in an
        uncommitted savepoint looks identical to a committed one from in
        here — the pre-O14 shape passes this test too. That the claim is
        genuinely *committed* before the network call is the staging check
        "two overlapping timer deliveries send exactly one reminder"."""
        _coach, lead = self._due_lead()
        seen = {}

        def notify(c, l):
            seen["stamp"] = EventConnection.objects.get(pk=lead.pk).reminder_sent_at
            return {"success": 1, "failed": 0, "total": 1}

        sweep_lead_reminders(notify=notify)

        assert seen["stamp"] is not None

    def test_the_release_only_clears_the_stamp_it_wrote(self):
        """The release is `filter(pk=..., reminder_sent_at=<our stamp>)`, not a
        blind blank. If the row moved on while the push was in flight it is no
        longer ours to clear — an unguarded UPDATE would wipe someone else's
        stamp and re-arm a reminder for a lead that no longer needs one."""
        _coach, lead = self._due_lead()
        someone_else = timezone.now() + timedelta(minutes=5)

        def notify(c, l):
            # Stands in for whatever else touched the row between our claim
            # and our release. The value differs from the stamp we wrote, so
            # the guard must not match.
            EventConnection.objects.filter(pk=lead.pk).update(
                reminder_sent_at=someone_else
            )
            return {"success": 0, "failed": 1, "total": 1}

        sweep_lead_reminders(notify=notify)

        lead.refresh_from_db()
        assert lead.reminder_sent_at == someone_else

    def test_a_successful_delivery_keeps_the_claim(self):
        """Positive control — the release path must not fire on success."""
        _coach, lead = self._due_lead()

        result = sweep_lead_reminders(
            notify=lambda c, l: {"success": 1, "failed": 0, "total": 1}
        )

        lead.refresh_from_db()
        assert lead.reminder_sent_at is not None
        assert result["sent"] == 1
        assert result["failed"] == 0


# ---------------------------------------------------------------------------
# Translation prep
# ---------------------------------------------------------------------------


def test_recipient_response_labels_are_translatable():
    """Unwrapped, `makemessages` never sees these and they stay English in DE
    and FR however complete the catalogues are. Asserting on the label object
    rather than a rendered string because nothing displays them yet — the
    point is that the first template that does will be translatable."""
    from django.utils.functional import Promise

    labels = [label for _value, label in EventConnection.RECIPIENT_RESPONSE_CHOICES]
    assert labels, "the choice list must not be empty"
    assert all(isinstance(label, Promise) for label in labels)


# ---------------------------------------------------------------------------
# Codex round 1 on #690 — three P2 findings
# ---------------------------------------------------------------------------


class TestCoachActionsRacingTheSend:
    """P2: the claim-then-send restructure releases the row lock *before* the
    push. The coach action handlers lock the same row, so they used to queue
    behind the reminder transaction; now they do not, and a coach can handle
    the lead during a slow multi-device push."""

    def _due_lead(self):
        coach = _make_coach("pe_race_coach@example.com")
        requester, req_p = _make_user("pe_race_req@example.com", "M")
        recipient, _ = _make_user("pe_race_rec@example.com", "F")
        req_p.assigned_coach = coach
        req_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event(), coach)
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(hours=1)
        )
        lead.refresh_from_db()
        return coach, lead

    def _sweep_with_action_during_send(self, action):
        """Run a sweep in which ``action`` lands after the claim commits and
        before the push — the window the pre-send re-check covers. Acting
        before the sweep would just drop the lead from the candidate scan and
        prove nothing."""
        lead_holder = {}
        sent = []

        def notify(coach, lead):
            sent.append(lead.pk)
            return {"success": 1, "failed": 0, "total": 1}

        _coach, lead = self._due_lead()
        lead_holder["lead"] = lead

        # The action fires from inside the re-check itself: that is the only
        # point that is provably after the claim commit and before the send.
        original = crush_leads._claim_still_owed

        def racing_check(lead_id, stamp, coach_id):
            action(lead)
            return original(lead_id, stamp, coach_id)

        with mock.patch.object(crush_leads, "_claim_still_owed", racing_check):
            result = crush_leads.sweep_lead_reminders(notify=notify)
        return lead, result, sent

    def test_a_call_scheduled_during_the_send_cancels_the_reminder(self):
        lead, result, sent = self._sweep_with_action_during_send(
            lambda l: EventConnection.objects.filter(pk=l.pk).update(
                coach_call_scheduled_at=timezone.now()
            )
        )

        assert sent == [], "a coach who just scheduled the call must not be reminded"
        assert result["sent"] == 0
        lead.refresh_from_db()
        # The claim is handed back, so the row looks untouched by the sweep.
        assert lead.reminder_sent_at is None

    def test_a_lead_declined_during_the_send_cancels_the_reminder(self):
        _lead_row, result, sent = self._sweep_with_action_during_send(
            lambda l: EventConnection.objects.filter(pk=l.pk).update(
                status="declined"
            )
        )

        assert sent == []
        assert result["sent"] == 0

    def test_a_coach_deactivated_during_the_send_cancels_the_reminder(self):
        def deactivate(lead):
            coach = lead.assigned_coach
            coach.is_active = False
            coach.save(update_fields=["is_active"])

        _lead_row, result, sent = self._sweep_with_action_during_send(deactivate)

        assert sent == []
        assert result["sent"] == 0

    def test_an_untouched_lead_is_still_sent(self):
        """Positive control — the re-check must not swallow the normal path."""
        lead, result, sent = self._sweep_with_action_during_send(lambda l: None)

        assert sent == [lead.pk]
        assert result["sent"] == 1
        lead.refresh_from_db()
        assert lead.reminder_sent_at is not None

    def test_a_cancelled_send_is_not_counted_as_a_failure(self):
        """A coach handling the lead is the system working, not a delivery
        problem. Counting it as `failed` would make a healthy sweep look like a
        push outage in the logs."""
        _row, result, _sent = self._sweep_with_action_during_send(
            lambda l: EventConnection.objects.filter(pk=l.pk).update(
                coach_call_completed_at=timezone.now()
            )
        )

        assert result["failed"] == 0
        assert result["sent"] == 0


class TestBackfillRechecksUnderTheLock:
    """P2: the command materialises its candidates and then loops. Against a
    live site a lead can be claimed, answered or closed in between, and the
    stale instance still carries the old `assigned_coach` — which is the value
    `assign_recipient_coach()` compares against."""

    def _pair(self, suffix, routed_coach, recipient_coach=None):
        requester, req_p = _make_user(f"pe_bfr_req_{suffix}@example.com", "M")
        recipient, rec_p = _make_user(f"pe_bfr_rec_{suffix}@example.com", "F")
        req_p.assigned_coach = routed_coach
        req_p.save(update_fields=["assigned_coach"])
        if recipient_coach is not None:
            rec_p.assigned_coach = recipient_coach
            rec_p.save(update_fields=["assigned_coach"])
        return _lead(requester, recipient, _make_event(), routed_coach)

    def _run_racing(self, action):
        """Fire ``action`` between the candidate scan and the per-row re-read.

        `_candidates()` is called once by `handle` for the scan and again per
        row for the locked re-read, so the *second* call is the window. Firing
        on the first call would land the write before the scan instead — the
        lead would simply drop out of the candidate list, and the test would
        pass without the re-read existing at all. An earlier version of this
        helper did exactly that and three of these four tests were vacuous.
        """
        out = StringIO()
        from crush_lu.management.commands import (
            backfill_crush_recipient_coaches as cmd_module,
        )

        original = cmd_module.Command._candidates
        calls = {"n": 0}

        def counting(self):
            calls["n"] += 1
            if calls["n"] == 2:
                action()
            return original(self)

        with mock.patch.object(cmd_module.Command, "_candidates", counting):
            call_command("backfill_crush_recipient_coaches", stdout=out)
        assert calls["n"] >= 2, (
            "the command never re-read a row — the race window under test "
            "does not exist"
        )
        return out.getvalue()

    def test_a_lead_claimed_by_the_recipients_coach_is_not_self_assigned(self):
        """The invariant `assign_recipient_coach()` protects: one person never
        covers both halves via `recipient_coach`. A stale `assigned_coach` is
        exactly how that check gets the wrong answer."""
        pool_coach = _make_coach("pe_bfr_pool@example.com")
        theirs = _make_coach("pe_bfr_theirs@example.com")
        lead = self._pair("a", pool_coach, recipient_coach=theirs)

        # `theirs` claims the lead after the scan: they are now the routed
        # coach, so there is no hand-off left to record.
        self._run_racing(
            lambda: EventConnection.objects.filter(pk=lead.pk).update(
                assigned_coach=theirs
            )
        )

        lead.refresh_from_db()
        assert lead.assigned_coach_id == theirs.pk
        assert lead.recipient_coach_id is None

    def test_a_lead_answered_since_the_scan_is_skipped(self):
        routed = _make_coach("pe_bfr_routed_b@example.com")
        theirs = _make_coach("pe_bfr_theirs_b@example.com")
        lead = self._pair("b", routed, recipient_coach=theirs)

        output = self._run_racing(
            lambda: EventConnection.objects.filter(pk=lead.pk).update(
                recipient_response=EventConnection.RECIPIENT_RESPONSE_DECLINED
            )
        )

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None
        assert "changed since the scan" in output

    def test_a_lead_closed_since_the_scan_is_skipped(self):
        routed = _make_coach("pe_bfr_routed_c@example.com")
        theirs = _make_coach("pe_bfr_theirs_c@example.com")
        lead = self._pair("c", routed, recipient_coach=theirs)

        self._run_racing(
            lambda: EventConnection.objects.filter(pk=lead.pk).update(status="shared")
        )

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None

    def test_an_unchanged_lead_is_still_assigned(self):
        """Positive control — the re-read must not swallow the normal path."""
        routed = _make_coach("pe_bfr_routed_d@example.com")
        theirs = _make_coach("pe_bfr_theirs_d@example.com")
        lead = self._pair("d", routed, recipient_coach=theirs)

        self._run_racing(lambda: None)

        lead.refresh_from_db()
        assert lead.recipient_coach_id == theirs.pk


class TestMutualCountIsScopedToOwnedCalls:
    """P2: `counts.mutual_crush` is rendered *under* the owned `crush_lead`
    total, so counting pool mutuals produced a strip that contradicts itself."""

    def _mutual_pool_pair(self, coach):
        a, _ = _make_user("pe_mut_a@example.com", "M")
        b, _ = _make_user("pe_mut_b@example.com", "F")
        event = _make_event("Mutual Pool Event")
        # Reciprocal declarations, neither routed — both sit in the pool.
        forward = _lead(a, b, event)
        _lead(b, a, event)
        return forward

    def test_a_mutual_pool_lead_is_not_counted_under_owned_calls(self):
        coach = _make_coach("pe_mut_coach@example.com")
        self._mutual_pool_pair(coach)

        counts = _queue(coach).context["counts"]

        # The contradiction Codex named: "0 Crush calls / N mutual".
        assert counts["crush_lead"] == 0
        assert counts["mutual_crush"] == 0
        assert counts["crush_pool"] == 2

    def test_the_pool_row_still_shows_its_mutual_badge(self):
        """Scoping the *count* must not hide the signal on the row — a coach
        triaging the pool wants to know both members declared."""
        coach = _make_coach("pe_mut_coach2@example.com")
        self._mutual_pool_pair(coach)

        items = [
            i for i in _queue(coach).context["items"] if i["kind"] == "crush_pool"
        ]

        assert items
        assert all(i["is_mutual_crush"] for i in items)

    def test_an_owned_mutual_call_is_still_counted(self):
        """Positive control — the scoping must not zero out the real case."""
        coach = _make_coach("pe_mut_coach3@example.com")
        a, a_p = _make_user("pe_mut_c@example.com", "M")
        b, _ = _make_user("pe_mut_d@example.com", "F")
        a_p.assigned_coach = coach
        a_p.save(update_fields=["assigned_coach"])
        event = _make_event("Mutual Owned Event")
        _lead(a, b, event, coach)
        _lead(b, a, event)

        counts = _queue(coach).context["counts"]

        assert counts["crush_lead"] == 1
        assert counts["mutual_crush"] == 1


# ---------------------------------------------------------------------------
# Codex round 2 on #690 — three more P2 findings
# ---------------------------------------------------------------------------


class TestAClaimIsNeverStranded:
    """P2: once the claim commits, every exit from the loop has to deliver or
    hand it back. The re-check and the release both run under the outer
    handler, so a transient database error in either used to leave
    `reminder_sent_at` populated with nothing sent — and since
    `reminder_candidates` filters on that column being null, the lead was then
    excluded from every future sweep. One member's reminder, lost silently and
    permanently."""

    def _due_lead(self):
        coach = _make_coach("pe_strand_coach@example.com")
        requester, req_p = _make_user("pe_strand_req@example.com", "M")
        recipient, _ = _make_user("pe_strand_rec@example.com", "F")
        req_p.assigned_coach = coach
        req_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event(), coach)
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(hours=1)
        )
        lead.refresh_from_db()
        return coach, lead

    def test_a_raising_recheck_still_releases_the_claim(self):
        _coach, lead = self._due_lead()

        def boom(lead_id, stamp, coach_id):
            raise RuntimeError("transient database error")

        with mock.patch.object(crush_leads, "_claim_still_owed", boom):
            result = crush_leads.sweep_lead_reminders(
                notify=lambda c, l: {"success": 1, "failed": 0, "total": 1}
            )

        lead.refresh_from_db()
        assert lead.reminder_sent_at is None, (
            "a stranded stamp excludes this lead from every future sweep"
        )
        assert result["failed"] == 1

    def test_a_raising_release_does_not_abort_the_rest_of_the_batch(self):
        """The recovery attempt is best-effort and must not mask the original
        error or take the other leads down with it."""
        _coach, _lead_row = self._due_lead()

        def boom(lead_id, stamp, coach_id):
            raise RuntimeError("transient database error")

        def boom_release(lead_id, stamp):
            raise RuntimeError("still unwell")

        with mock.patch.object(crush_leads, "_claim_still_owed", boom), \
                mock.patch.object(crush_leads, "_release_claim", boom_release):
            result = crush_leads.sweep_lead_reminders(
                notify=lambda c, l: {"success": 1, "failed": 0, "total": 1}
            )

        # It gave up on this lead rather than propagating.
        assert result["failed"] == 1
        assert result["sent"] == 0

    def test_a_delivered_reminder_keeps_its_stamp(self):
        """Positive control — the recovery path must not clear the record of a
        reminder that actually went out."""
        _coach, lead = self._due_lead()

        result = crush_leads.sweep_lead_reminders(
            notify=lambda c, l: {"success": 1, "failed": 0, "total": 1}
        )

        lead.refresh_from_db()
        assert lead.reminder_sent_at is not None
        assert result["sent"] == 1


class TestTheReminderGoesToTheCurrentOwner:
    """P2: `_claim_still_owed` required *an* active owner, not *the* captured
    one. A reassignment between claim and send passed that test while
    `notify()` still held the old coach — so the reminder, which names the
    crusher, went to someone who no longer owns the lead, and the stamp was
    consumed so the real owner never got one."""

    def _due_lead(self):
        coach = _make_coach("pe_owner_coach@example.com")
        requester, req_p = _make_user("pe_owner_req@example.com", "M")
        recipient, _ = _make_user("pe_owner_rec@example.com", "F")
        req_p.assigned_coach = coach
        req_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event(), coach)
        EventConnection.objects.filter(pk=lead.pk).update(
            requested_at=timezone.now() - REMINDER_AFTER - timedelta(hours=1)
        )
        lead.refresh_from_db()
        return coach, lead

    def test_a_reassignment_mid_send_cancels_rather_than_misdelivers(self):
        _old_coach, lead = self._due_lead()
        new_coach = _make_coach("pe_owner_new@example.com")
        notified = []

        original = crush_leads._claim_still_owed

        def racing_check(lead_id, stamp, coach_id):
            EventConnection.objects.filter(pk=lead_id).update(
                assigned_coach=new_coach
            )
            return original(lead_id, stamp, coach_id)

        def notify(coach, l):
            notified.append(coach.pk)
            return {"success": 1, "failed": 0, "total": 1}

        with mock.patch.object(crush_leads, "_claim_still_owed", racing_check):
            result = crush_leads.sweep_lead_reminders(notify=notify)

        # The old coach must not be told about a lead they no longer own —
        # the payload names the crusher on the premise that the recipient is
        # the routed coach.
        assert notified == []
        assert result["sent"] == 0
        lead.refresh_from_db()
        # ...and the claim is handed back, so the *new* owner is reminded on
        # the next sweep rather than the stamp being consumed by the old one.
        assert lead.reminder_sent_at is None
        assert lead.assigned_coach_id == new_coach.pk

    def test_the_unchanged_owner_is_still_notified(self):
        """Positive control — pinning the owner must not block the normal
        path."""
        coach, _lead_row = self._due_lead()
        notified = []

        crush_leads.sweep_lead_reminders(
            notify=lambda c, l: (
                notified.append(c.pk), {"success": 1, "failed": 0, "total": 1}
            )[1]
        )

        assert notified == [coach.pk]


class TestBackfillLeavesPoolLeadsAlone:
    """P2, and an interaction between this PR's own two features: the backfill
    could stamp `recipient_coach = X` on an *ownerless* lead, the new pool
    section then invites X to claim it, and `crush_start_review` sets
    `assigned_coach` without touching `recipient_coach` — leaving X on both
    halves of a lead, which is the invariant `assign_recipient_coach()` exists
    to protect."""

    def _pool_lead(self, suffix, recipient_coach=None):
        requester, _ = _make_user(f"pe_pool_bf_req_{suffix}@example.com", "M")
        recipient, rec_p = _make_user(f"pe_pool_bf_rec_{suffix}@example.com", "F")
        if recipient_coach is not None:
            rec_p.assigned_coach = recipient_coach
            rec_p.save(update_fields=["assigned_coach"])
        return _lead(requester, recipient, _make_event())

    def _run(self):
        out = StringIO()
        call_command("backfill_crush_recipient_coaches", stdout=out)
        return out.getvalue()

    def test_an_unrouted_pool_lead_is_not_given_a_recipient_coach(self):
        theirs = _make_coach("pe_pool_bf_theirs@example.com")
        lead = self._pool_lead("a", recipient_coach=theirs)

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None

    def test_a_lead_whose_coach_went_inactive_is_not_given_one_either(self):
        """Same reasoning: it is back in the pool, so its next routed coach is
        not yet known."""
        gone = _make_coach("pe_pool_bf_gone@example.com", is_active=False)
        theirs = _make_coach("pe_pool_bf_theirs_b@example.com")
        requester, _ = _make_user("pe_pool_bf_req_b@example.com", "M")
        recipient, rec_p = _make_user("pe_pool_bf_rec_b@example.com", "F")
        rec_p.assigned_coach = theirs
        rec_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event(), gone)

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id is None

    def test_claiming_that_pool_lead_leaves_one_coach_on_one_half(self):
        """The end state the exclusion protects: the recipient's own coach
        claims the pool lead and ends up routed-only, not routed *and*
        co-coach."""
        theirs = _make_coach("pe_pool_bf_claimer@example.com")
        lead = self._pool_lead("c", recipient_coach=theirs)
        self._run()

        client = Client()
        _login(client, theirs.user)
        client.post(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            ),
            {"action": "crush_start_review"},
        )

        lead.refresh_from_db()
        assert lead.assigned_coach_id == theirs.pk
        assert lead.recipient_coach_id is None, (
            "one coach must not hold both halves"
        )
        assert lead.has_active_recipient_coach is False

    def test_a_routed_lead_is_still_backfilled(self):
        """Positive control — the exclusion must not empty the command."""
        routed = _make_coach("pe_pool_bf_routed@example.com")
        theirs = _make_coach("pe_pool_bf_theirs_d@example.com")
        requester, req_p = _make_user("pe_pool_bf_req_d@example.com", "M")
        recipient, rec_p = _make_user("pe_pool_bf_rec_d@example.com", "F")
        req_p.assigned_coach = routed
        req_p.save(update_fields=["assigned_coach"])
        rec_p.assigned_coach = theirs
        rec_p.save(update_fields=["assigned_coach"])
        lead = _lead(requester, recipient, _make_event(), routed)

        self._run()

        lead.refresh_from_db()
        assert lead.recipient_coach_id == theirs.pk


# ---------------------------------------------------------------------------
# Codex round 3 on #690
# ---------------------------------------------------------------------------


class TestTransportFailuresCountAgainstDeviceHealth:
    """P2: `webpush()` raises `WebPushException` for protocol errors, but a hung
    endpoint surfaces as a *timeout*, which took the helper's catch-all branch
    — and that branch recorded no device health at all. The exact device O14
    exists to clean up therefore stayed at `failure_count == 0` forever, never
    auto-deleted, burning its full timeout out of every hourly sweep."""

    def _coach_with_device(self, name="pe_health@example.com"):
        coach = _make_coach(name)
        subscription = CoachPushSubscription.objects.create(
            coach=coach,
            endpoint="https://push.example.com/hung",
            p256dh_key=VALID_P256DH,
            auth_key=VALID_AUTH,
        )
        return coach, subscription

    def _send(self, coach, exc):
        from crush_lu import coach_notifications

        with mock.patch.object(
            coach_notifications, "webpush", side_effect=exc
        ), mock.patch.object(
            coach_notifications.settings, "VAPID_PRIVATE_KEY", "x", create=True
        ), mock.patch.object(
            coach_notifications.settings, "VAPID_PUBLIC_KEY", "y", create=True
        ), mock.patch.object(
            coach_notifications.settings,
            "VAPID_ADMIN_EMAIL",
            "a@example.com",
            create=True,
        ):
            return coach_notifications.send_coach_push_notification(
                coach, "title", "body"
            )

    def test_a_transport_timeout_marks_the_device(self):
        coach, subscription = self._coach_with_device()

        # The real exception type, not a stand-in: `pywebpush` hands `timeout`
        # to `requests`, so a hung endpoint raises `ReadTimeout`. An earlier
        # version of this test used the builtin `TimeoutError`, which nothing
        # in the stack actually raises — it passed against a catch-all branch
        # and would have gone on passing after the branch was narrowed.
        result = self._send(coach, requests.exceptions.ReadTimeout("no response"))

        subscription.refresh_from_db()
        assert subscription.failure_count == 1
        assert result["failed"] == 1
        assert result["success"] == 0

    def test_a_refused_connection_marks_the_device(self):
        coach, subscription = self._coach_with_device("pe_health_e@example.com")

        self._send(coach, requests.exceptions.ConnectionError("refused"))

        subscription.refresh_from_db()
        assert subscription.failure_count == 1

    def test_five_consecutive_timeouts_delete_the_device(self):
        """The auto-delete O14 is meant to restore. Without device health on
        this path the endpoint lived forever."""
        coach, subscription = self._coach_with_device("pe_health_b@example.com")

        for _ in range(5):
            self._send(coach, requests.exceptions.ReadTimeout("no response"))

        assert not CoachPushSubscription.objects.filter(pk=subscription.pk).exists()

    def test_the_result_still_reads_as_a_delivery_failure(self):
        """The sweep keys off `total > 0 and success == 0` to release its
        claim — marking device health must not change that shape."""
        coach, _subscription = self._coach_with_device("pe_health_c@example.com")

        result = self._send(coach, requests.exceptions.ReadTimeout("no response"))

        assert result["total"] > 0
        assert result["success"] == 0

    def test_a_global_failure_does_not_blame_the_device(self):
        """A malformed — but non-empty — VAPID key makes `webpush()` raise the
        same decoding error for *every* subscription. Counting that against
        device health would blame each endpoint for a global fault, and after
        five sweeps delete every coach's devices. The presence checks catch an
        unset key; they cannot catch an invalid one."""
        coach, subscription = self._coach_with_device("pe_health_d@example.com")

        result = self._send(coach, ValueError("Could not deserialize key data"))

        subscription.refresh_from_db()
        assert subscription.failure_count == 0
        # Still a delivery failure, so the sweep releases its claim and retries
        # once someone fixes the key.
        assert result["failed"] == 1
        assert result["success"] == 0

    def test_a_global_failure_never_deletes_devices(self):
        """The consequence that made this worth splitting: a config typo must
        not become unrecoverable data loss."""
        coach, subscription = self._coach_with_device("pe_health_f@example.com")

        for _ in range(6):
            self._send(coach, ValueError("Could not deserialize key data"))

        assert CoachPushSubscription.objects.filter(pk=subscription.pk).exists()


class TestClaimingALeadYouCoCoach:
    """P2: a Phase D lead routed to A with `recipient_coach = B` returns to the
    pool when A is deactivated — and B sees it there like every other coach. B
    claiming it used to leave B on both halves, the state
    `assign_recipient_coach()` refuses to create."""

    def _orphaned_lead(self, suffix="a"):
        gone = _make_coach(f"pe_both_gone_{suffix}@example.com", is_active=False)
        cocoach = _make_coach(f"pe_both_co_{suffix}@example.com")
        requester, _ = _make_user(f"pe_both_req_{suffix}@example.com", "M")
        recipient, _ = _make_user(f"pe_both_rec_{suffix}@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), gone)
        lead.recipient_coach = cocoach
        lead.save(update_fields=["recipient_coach"])
        return cocoach, lead

    def _claim(self, coach, lead):
        client = Client()
        _login(client, coach.user)
        return client.post(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            ),
            {"action": "crush_start_review"},
        )

    def test_the_lead_reaches_its_co_coachs_pool(self):
        """Premise check — without this the rest is unreachable."""
        cocoach, lead = self._orphaned_lead()

        assert _pool_ids(_queue(cocoach)) == [lead.pk]

    def test_claiming_it_clears_the_co_coach_side(self):
        cocoach, lead = self._orphaned_lead("b")

        self._claim(cocoach, lead)

        lead.refresh_from_db()
        assert lead.assigned_coach_id == cocoach.pk
        assert lead.recipient_coach_id is None
        assert lead.has_active_recipient_coach is False

    def test_the_clearing_is_recorded_in_the_audit_trail(self):
        """A silently vanishing `recipient_coach` reads like a lost write."""
        cocoach, lead = self._orphaned_lead("c")

        self._claim(cocoach, lead)

        lead.refresh_from_db()
        types = [a["type"] for a in lead.system_actions]
        assert "crush_recipient_coach_cleared_on_claim" in types
        assert "crush_start_review" in types

    def test_the_coach_does_not_also_get_an_outreach_task(self):
        """The visible symptom: one lead appearing twice in one inbox, once as
        a call and once as a task addressed to yourself."""
        cocoach, lead = self._orphaned_lead("d")

        self._claim(cocoach, lead)

        items = _queue(cocoach).context["items"]
        kinds = [
            i["kind"]
            for i in items
            if i["url_kwargs"].get("connection_id") == lead.pk
        ]
        assert kinds == ["crush_lead"]

    def test_a_different_coach_claiming_leaves_the_co_coach_in_place(self):
        """Positive control — the clearing must be scoped to the claimer, not
        applied to every claim."""
        cocoach, lead = self._orphaned_lead("e")
        someone_else = _make_coach("pe_both_other@example.com")

        self._claim(someone_else, lead)

        lead.refresh_from_db()
        assert lead.assigned_coach_id == someone_else.pk
        assert lead.recipient_coach_id == cocoach.pk


class TestAnInactiveOwnerLeadCanActuallyBeClaimed:
    """The pool section surfaces leads whose routed coach was deactivated and
    links them to the review page — but `crush_start_review`'s guard was a bare
    null-owner check, so those rows refused the claim. Surfacing a lead and then
    refusing to let anyone take it is a dead end, not a recovery path."""

    def _orphaned_lead(self, suffix="a", status="pending"):
        gone = _make_coach(f"pe_reclaim_gone_{suffix}@example.com", is_active=False)
        requester, _ = _make_user(f"pe_reclaim_req_{suffix}@example.com", "M")
        recipient, _ = _make_user(f"pe_reclaim_rec_{suffix}@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), gone, status=status)
        return gone, lead

    def _claim(self, coach, lead):
        client = Client()
        _login(client, coach.user)
        return client.post(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            ),
            {"action": "crush_start_review"},
            follow=True,
        )

    def test_a_coach_can_adopt_a_stranded_lead(self):
        _gone, lead = self._orphaned_lead()
        rescuer = _make_coach("pe_reclaim_rescuer@example.com")

        self._claim(rescuer, lead)

        lead.refresh_from_db()
        assert lead.assigned_coach_id == rescuer.pk
        assert lead.status == "coach_reviewing"

    def test_an_actively_owned_lead_still_cannot_be_taken(self):
        """Positive control on the other side — relaxing the guard must not
        open ownership takeover of a live lead."""
        owner = _make_coach("pe_reclaim_owner@example.com")
        requester, _ = _make_user("pe_reclaim_req_b@example.com", "M")
        recipient, _ = _make_user("pe_reclaim_rec_b@example.com", "F")
        lead = _lead(requester, recipient, _make_event(), owner)
        thief = _make_coach("pe_reclaim_thief@example.com")

        self._claim(thief, lead)

        lead.refresh_from_db()
        assert lead.assigned_coach_id == owner.pk
        assert lead.status == "pending"

    def test_a_closed_stranded_lead_is_not_claimable(self):
        """`_stale_owner_recoverable`'s rule: on a closed crush lead there is
        no work to recover, so the fallthrough would be pure disclosure."""
        _gone, lead = self._orphaned_lead("c", status="declined")
        rescuer = _make_coach("pe_reclaim_rescuer_c@example.com")

        self._claim(rescuer, lead)

        lead.refresh_from_db()
        assert lead.assigned_coach_id != rescuer.pk


class TestTheClaimAuditAppendUsesTheLockedRow:
    """P2: the `claim` branch validated the *locked* row but mutated the
    instance loaded at request entry. `log_system_action` is a read-modify-write
    of a JSON column, so appending from that stale instance would drop any entry
    committed in between. Assigning a scalar was harmless — which is exactly why
    this block did not need the locked row until an audit append was added."""

    def _lead_owned_by_nobody_with_cocoach(self):
        cocoach = _make_coach("pe_audit_co@example.com")
        requester, _ = _make_user("pe_audit_req@example.com", "M")
        recipient, _ = _make_user("pe_audit_rec@example.com", "F")
        lead = _lead(requester, recipient, _make_event())
        lead.recipient_coach = cocoach
        lead.save(update_fields=["recipient_coach"])
        return cocoach, lead

    def test_a_concurrent_audit_entry_is_not_clobbered(self):
        cocoach, lead = self._lead_owned_by_nobody_with_cocoach()

        client = Client()
        _login(client, cocoach.user)

        # Land a committed audit entry *after* the view's request-entry read
        # and *before* it takes the row lock. `_claim_blocked` is called twice
        # — once on the pre-transaction instance, once on the locked row — so
        # firing on the **first** call lands squarely in that window.
        #
        # Firing on the second call would be wrong, not merely different: that
        # point is already past `select_for_update`, so on Postgres the
        # competing writer would block until this transaction commits and the
        # interleaving could never occur. It only "works" on SQLite, where the
        # lock is a no-op — the exact shape of vacuous race test that shipped
        # twice on #683.
        from crush_lu import views_coach

        original = views_coach._claim_blocked
        fired = {"n": 0}

        def racing(conn):
            fired["n"] += 1
            if fired["n"] == 1:
                other = EventConnection.objects.get(pk=lead.pk)
                other.log_system_action("concurrent_entry", actor="someone_else")
                other.save(update_fields=["system_actions"])
            return original(conn)

        with mock.patch.object(views_coach, "_claim_blocked", racing):
            client.post(
                reverse(
                    "crush_lu:coach_connection_review",
                    kwargs={"connection_id": lead.pk},
                ),
                {"action": "claim"},
            )

        lead.refresh_from_db()
        types = [a["type"] for a in lead.system_actions]
        assert "crush_recipient_coach_cleared_on_claim" in types
        assert "concurrent_entry" in types, (
            "the append-only trail lost an entry committed before the lock"
        )

    def test_the_claim_still_works_without_a_race(self):
        """Positive control."""
        cocoach, lead = self._lead_owned_by_nobody_with_cocoach()

        client = Client()
        _login(client, cocoach.user)
        client.post(
            reverse(
                "crush_lu:coach_connection_review",
                kwargs={"connection_id": lead.pk},
            ),
            {"action": "claim"},
        )

        lead.refresh_from_db()
        assert lead.assigned_coach_id == cocoach.pk
        assert lead.recipient_coach_id is None


class TestUnusableKeyMaterialCountsAgainstTheDevice:
    """P2, and the round-4 fix overshooting: splitting global faults out of the
    device-health branch also excluded *device-specific* key faults, because
    `pywebpush` reports those by exception type too — `p256dh="%%%"` surfaces as
    a bare `IndexError`. Such a subscription can never work, so skipping
    `mark_failure()` left it enabled forever, retried every sweep, never
    reaching the five-failure cleanup: the exact slow drain O14 removed."""

    def _device(self, name, p256dh=VALID_P256DH, auth=VALID_AUTH):
        coach = _make_coach(name)
        return coach, CoachPushSubscription.objects.create(
            coach=coach,
            endpoint="https://push.example.com/x",
            p256dh_key=p256dh,
            auth_key=auth,
        )

    def _send(self, coach):
        from crush_lu import coach_notifications

        with mock.patch.object(
            coach_notifications.settings, "VAPID_PRIVATE_KEY", "x", create=True
        ), mock.patch.object(
            coach_notifications.settings, "VAPID_PUBLIC_KEY", "y", create=True
        ), mock.patch.object(
            coach_notifications.settings,
            "VAPID_ADMIN_EMAIL",
            "a@example.com",
            create=True,
        ):
            return coach_notifications.send_coach_push_notification(
                coach, "title", "body"
            )

    def test_garbage_p256dh_is_blamed_on_the_device(self):
        """The specific value Codex named. It raises `IndexError` from inside
        the encryption code — neither a `WebPushException` nor a transport
        error — so exception-type sorting put it in the "not the device's
        fault" branch."""
        coach, subscription = self._device("pe_keys_a@example.com", p256dh="%%%")

        result = self._send(coach)

        subscription.refresh_from_db()
        assert subscription.failure_count == 1
        assert result["failed"] == 1

    def test_garbage_auth_is_blamed_on_the_device(self):
        coach, subscription = self._device("pe_keys_c@example.com", auth="%%%")

        self._send(coach)

        subscription.refresh_from_db()
        assert subscription.failure_count == 1

    def test_decodable_material_is_left_to_the_send(self):
        """The guard is deliberately narrow. A short-but-decodable p256dh
        already raises `WebPushException`, which already marks device health —
        so catching it here would be redundant, and the length rule needed to
        catch it would reject stored rows that a real send might handle. That
        over-reach was caught by a pre-existing test, not by review."""
        from crush_lu.coach_notifications import subscription_key_fault

        assert subscription_key_fault("AAAA", VALID_AUTH) is None
        assert subscription_key_fault("k0", "a0") is None

    def test_empty_material_is_blamed_on_the_device(self):
        coach, subscription = self._device("pe_keys_e@example.com", p256dh="")

        self._send(coach)

        subscription.refresh_from_db()
        assert subscription.failure_count == 1

    def test_five_sweeps_clean_up_an_unusable_device(self):
        """The cleanup that was unreachable — the point of the finding."""
        coach, subscription = self._device("pe_keys_d@example.com", p256dh="%%%")

        for _ in range(5):
            self._send(coach)

        assert not CoachPushSubscription.objects.filter(pk=subscription.pk).exists()

    def test_valid_material_is_not_short_circuited(self):
        """Positive control — the guard must not swallow usable devices. With
        valid keys the send reaches the network and fails there, which is the
        transport path, not the key path."""
        from crush_lu.coach_notifications import subscription_key_fault

        assert subscription_key_fault(VALID_P256DH, VALID_AUTH) is None


class TestSubscribeRejectsUnusableKeys:
    """Present is not usable. The endpoint checked only that the fields exist,
    so a buggy client could store material that can never encrypt a push."""

    def _post(self, coach, p256dh, auth):
        client = Client()
        _login(client, coach.user)
        return client.post(
            reverse("api_coach_subscribe_push"),
            data=json.dumps(
                {
                    "endpoint": "https://push.example.com/new",
                    "keys": {"p256dh": p256dh, "auth": auth},
                }
            ),
            content_type="application/json",
        )

    def test_garbage_keys_are_rejected(self):
        coach = _make_coach("pe_sub_a@example.com")

        response = self._post(coach, "%%%", VALID_AUTH)

        assert response.status_code == 400
        assert not CoachPushSubscription.objects.filter(coach=coach).exists()

    def test_valid_keys_are_accepted(self):
        """Positive control — the check must not reject real subscriptions."""
        coach = _make_coach("pe_sub_b@example.com")

        response = self._post(coach, VALID_P256DH, VALID_AUTH)

        assert response.status_code == 200
        assert CoachPushSubscription.objects.filter(coach=coach).exists()
