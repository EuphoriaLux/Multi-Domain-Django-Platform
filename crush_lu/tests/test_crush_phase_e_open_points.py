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
from datetime import date, timedelta
from io import StringIO

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
            p256dh_key="k",
            auth_key="a",
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
