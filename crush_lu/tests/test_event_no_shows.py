"""
Recording no-shows for every event type: the MeetupEvent admin action and the
button on the coach event page.

Only the QuizEvent admin could record no-shows, so mixers and speed dating
kept theirs as seat holders: event 27, a free mixer, still had 14
never-checked-in rows ``confirmed`` a day after it ended (prod, 2026-10-11).
Both new surfaces share ``services.no_shows`` with the quiz action; these
tests pin what they change, what they leave alone and which events they
refuse.

Requests use literal paths on the crush.lu host, so its middleware picks the
urlconf as in production (AGENTS.md: `reverse()` resolves against the default
urlconf, not the host's).
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sites.models import Site
from django.core import mail
from django.core.cache import cache
from django.db.models.query import QuerySet
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    UserDataConsent,
)
from crush_lu.models.events import CuratedEventGroup, CuratedEventGroupMembership
from crush_lu.models.quiz import QuizEvent
from crush_lu.services import no_shows
from crush_lu.services.no_shows import candidate_fingerprint

User = get_user_model()

HOST = "crush.lu"
EVENTS = "/crush-admin/crush_lu/meetupevent/"
QUIZZES = "/crush-admin/crush_lu/quizevent/"
ACTION = "mark_unattended_as_no_show"


def coach_event_page(event):
    return f"/en/coach/events/{event.pk}/"


def coach_no_shows(event):
    return f"/en/coach/events/{event.pk}/no-shows/"


def make_event(title, start, **fields):
    fields = {
        "description": "No-show fixture event",
        "event_type": "mixer",
        "duration_minutes": 120,
        "location": "Luxembourg",
        "address": "1 Test Street",
        "max_participants": 20,
        "registration_deadline": start - timedelta(hours=1),
        "is_published": True,
        **fields,
    }
    return MeetupEvent.objects.create(title=title, date_time=start, **fields)


class NoShowFixturesMixin:
    def setUp(self):
        # Recycled SQLite primary keys share one rate-limit counter.
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.client = Client(HTTP_HOST=HOST)
        self.now = timezone.now()
        self._users = 0

    def _register(self, event, status, **fields):
        self._users += 1
        user = User.objects.create_user(
            username=f"member{self._users}",
            email=f"member{self._users}@example.com",
            password="pw12345678",
        )
        return EventRegistration.objects.create(
            event=event, user=user, status=status, **fields
        )

    def _attended(self, event):
        return self._register(
            event, "attended", checked_in_at=event.date_time + timedelta(minutes=5)
        )

    def _status(self, registration):
        registration.refresh_from_db()
        return registration.status

    def _messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def _curated_event(self):
        return make_event(
            "Curated evening",
            self.now - timedelta(days=1),
            event_type="speed_dating",
            registration_mode="curated",
            group_size=6,
            planned_groups=1,
            registration_fee=Decimal("15.00"),
            profile_requirement="none",
        )

    def _curated_member(self, event, status, **fields):
        # A curated seat may only be granted through the audited bulk confirm,
        # so the row is born an application and flipped underneath the model.
        registration = self._register(event, "applied")
        EventRegistration.objects.filter(pk=registration.pk).update(
            status=status, **fields
        )
        registration.refresh_from_db()
        return registration

    def _provisional_group(self, event, registrations):
        # Same construction as test_curated_group_reserve_notifications: build
        # the roster as a draft, then flip the status underneath the model.
        group = CuratedEventGroup.objects.create(
            event=event,
            generation=1,
            group_number=1,
            seed="no-show-test",
            policy_version="reciprocal-graph-v1",
        )
        for position, registration in enumerate(registrations, start=1):
            CuratedEventGroupMembership.objects.create(
                event=event,
                group=group,
                registration=registration,
                position=position,
            )
        CuratedEventGroup.objects.filter(pk=group.pk).update(
            status=CuratedEventGroup.STATUS_PROVISIONAL,
            provisional_at=self.now,
        )
        return group

    def _google_holder(self, event):
        registration = self._register(event, "confirmed")
        profile = CrushProfile.objects.create(
            user=registration.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
        )
        # Set underneath the model so setup fires no wallet refresh itself.
        CrushProfile.objects.filter(pk=profile.pk).update(
            google_wallet_object_id=f"google-obj-{registration.pk}"
        )
        return registration


class MeetupEventNoShowActionTests(NoShowFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.superuser = User.objects.create_superuser(
            username="no_show_admin",
            email="no-show-admin@example.com",
            password="pw12345678",
        )
        self.client.force_login(self.superuser)

    def _run(self, *events):
        response = self.client.post(
            EVENTS,
            {"action": ACTION, "_selected_action": [event.pk for event in events]},
        )
        self.assertEqual(response.status_code, 302)
        return self._messages(response)

    def test_ended_mixer_marks_unchecked_in_seat_holders_as_no_show(self):
        event = make_event("Ended mixer", self.now - timedelta(days=1))
        attended = self._attended(event)
        confirmed = self._register(event, "confirmed")
        # A cash-at-the-door seat that never arrived is a no-show too.
        pending = self._register(event, "pending")
        cancelled = self._register(event, "cancelled")
        waitlist = self._register(event, "waitlist")
        applied = self._register(event, "applied")
        # Scanned at the door, then edited back to "confirmed": they were there.
        scanned = self._register(
            event, "confirmed", checked_in_at=event.date_time + timedelta(minutes=9)
        )

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "no_show")
        self.assertEqual(self._status(pending), "no_show")
        self.assertEqual(self._status(attended), "attended")
        self.assertEqual(self._status(cancelled), "cancelled")
        self.assertEqual(self._status(waitlist), "waitlist")
        self.assertEqual(self._status(applied), "applied")
        self.assertEqual(self._status(scanned), "confirmed")
        self.assertIn(f"'{event}': marked 2 registration(s) as No Show.", messages)

    def test_no_show_does_not_promote_the_waitlist(self):
        # Full event: marking the no-show frees a seat on paper, and a
        # cancellation of the same seat would hand it to the waitlist.
        event = make_event(
            "Full mixer", self.now - timedelta(days=1), max_participants=2
        )
        self._attended(event)
        no_show = self._register(event, "confirmed")
        waiting = self._register(event, "waitlist")

        # Promotion runs on commit, which a TestCase never reaches on its own.
        with self.captureOnCommitCallbacks(execute=True):
            self._run(event)

        self.assertEqual(self._status(no_show), "no_show")
        self.assertEqual(self._status(waiting), "waitlist")
        self.assertFalse(
            EventRegistration.objects.filter(
                event=event, waitlist_promoted_at__isnull=False
            ).exists()
        )
        self.assertEqual(mail.outbox, [])

    def test_future_event_is_refused(self):
        event = make_event("Next mixer", self.now + timedelta(days=3))
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(
            f"'{event}' skipped: its check-in window has not closed yet.", messages
        )

    def test_live_event_is_refused(self):
        # Started, not ended: late arrivals can still check in.
        event = make_event("Live mixer", self.now - timedelta(minutes=30))
        self._attended(event)
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(
            f"'{event}' skipped: its check-in window has not closed yet.", messages
        )

    def test_just_ended_event_is_refused_while_the_door_still_admits(self):
        # Ended an hour ago, but the QR check-in accepts a scan until 12 hours
        # after the start and refuses a no_show row: a straggler marked now
        # would be turned away at the door.
        event = make_event("Just ended mixer", self.now - timedelta(hours=3))
        self.assertLess(event.end_time, self.now)
        self._attended(event)
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(
            f"'{event}' skipped: its check-in window has not closed yet.", messages
        )

    def test_cancelled_event_is_refused(self):
        # Cancelling leaves registrations "confirmed"; nobody missed anything.
        event = make_event(
            "Cancelled mixer", self.now - timedelta(days=1), is_cancelled=True
        )
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(
            f"'{event}' skipped: it was cancelled, so nobody missed it.",
            messages,
        )

    def test_event_without_any_check_in_is_refused(self):
        # The scanner was never used, so the whole roster would read absent.
        event = make_event("Unscanned mixer", self.now - timedelta(days=1))
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(
            f"'{event}' skipped: nobody was checked in, so attendance "
            "was never recorded.",
            messages,
        )

    def test_mixed_selection_marks_only_the_eligible_event(self):
        ended = make_event(
            "Ended speed dating",
            self.now - timedelta(days=2),
            event_type="speed_dating",
        )
        self._attended(ended)
        missed = self._register(ended, "confirmed")
        upcoming = make_event("Upcoming mixer", self.now + timedelta(days=1))
        booked = self._register(upcoming, "confirmed")

        self._run(ended, upcoming)

        self.assertEqual(self._status(missed), "no_show")
        self.assertEqual(self._status(booked), "confirmed")

    def test_member_still_in_a_provisional_curated_group_is_refused(self):
        # save() would degrade the group and retire its checkouts; .update()
        # would not, so the group workflow has to settle it first.
        event = self._curated_event()
        attended = self._curated_member(
            event, "attended", checked_in_at=event.date_time
        )
        missing = self._curated_member(event, "confirmed")
        group = self._provisional_group(event, [attended, missing])

        messages = self._run(event)

        self.assertEqual(self._status(missing), "confirmed")
        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_PROVISIONAL)
        self.assertIn(
            f"'{event}' skipped: some of these members are still in an open "
            "curated group; settle it through the group workflow first.",
            messages,
        )

    def test_quiz_action_refuses_an_open_curated_group_too(self):
        # The quiz action keeps no time guard, but a quiz can sit on a curated
        # speed-dating event and must not bypass its group either.
        event = self._curated_event()
        attended = self._curated_member(
            event, "attended", checked_in_at=event.date_time
        )
        missing = self._curated_member(event, "confirmed")
        self._provisional_group(event, [attended, missing])
        quiz = QuizEvent.objects.create(event=event, created_by=self.superuser)

        response = self.client.post(
            QUIZZES, {"action": ACTION, "_selected_action": [quiz.pk]}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._status(missing), "confirmed")
        self.assertIn(
            f"'{event}' skipped: some of these members are still in an open "
            "curated group; settle it through the group workflow first.",
            self._messages(response),
        )

    def test_released_curated_membership_does_not_block(self):
        event = self._curated_event()
        attended = self._curated_member(
            event, "attended", checked_in_at=event.date_time
        )
        missing = self._curated_member(event, "confirmed")
        self._provisional_group(event, [attended, missing])
        CuratedEventGroupMembership.objects.filter(registration=missing).update(
            released_at=self.now
        )

        self._run(event)

        self.assertEqual(self._status(missing), "no_show")

    @override_settings(WALLET_GOOGLE_BULK_UPDATE_LIMIT=1)
    def test_events_past_the_google_cap_are_deferred_not_stranded(self):
        # Google cards have no poll, and a no_show row is out of reach of any
        # rerun, so a row that would overflow the shared PATCH cap is left
        # untouched for the next run instead of being flipped with a stale card.
        first = make_event("First mixer", self.now - timedelta(days=2))
        second = make_event("Second mixer", self.now - timedelta(days=1))
        for event in (first, second):
            self._attended(event)
        first_missing = self._google_holder(first)
        second_missing = self._google_holder(second)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ), mock.patch(
            "crush_lu.wallet.google_api.refresh_google_wallet_objects"
        ) as refresh_google:
            messages = self._run(first, second)

            self.assertEqual(self._status(first_missing), "no_show")
            self.assertEqual(self._status(second_missing), "confirmed")
            self.assertEqual(refresh_google.call_count, 1)
            self.assertEqual(len(refresh_google.call_args.args[0]), 1)
            self.assertIn(
                f"'{second}': 1 registration(s) not changed yet — the Google "
                "Wallet refresh limit was reached. Run the action again to "
                "record them.",
                messages,
            )

            self._run(first, second)

        self.assertEqual(self._status(second_missing), "no_show")
        self.assertEqual(refresh_google.call_count, 2)

    @override_settings(WALLET_GOOGLE_BULK_UPDATE_LIMIT=1)
    def test_a_single_event_over_the_google_cap_is_split_across_runs(self):
        # No event is exempt, the first included: within one event the cap is
        # spent row by row, and a row without a Google card costs nothing.
        event = make_event("Big mixer", self.now - timedelta(days=1))
        self._attended(event)
        first = self._google_holder(event)
        second = self._google_holder(event)
        no_card = self._register(event, "confirmed")

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ), mock.patch(
            "crush_lu.wallet.google_api.refresh_google_wallet_objects"
        ) as refresh_google:
            messages = self._run(event)

            self.assertEqual(self._status(first), "no_show")
            self.assertEqual(self._status(no_card), "no_show")
            self.assertEqual(self._status(second), "confirmed")
            self.assertEqual(len(refresh_google.call_args.args[0]), 1)
            self.assertIn(f"'{event}': marked 2 registration(s) as No Show.", messages)
            self.assertIn(
                f"'{event}': 1 registration(s) not changed yet — the Google "
                "Wallet refresh limit was reached. Run the action again to "
                "record them.",
                messages,
            )

            self._run(event)

        self.assertEqual(self._status(second), "no_show")
        self.assertEqual(
            [p.user_id for p in refresh_google.call_args.args[0]], [second.user_id]
        )


class CoachEventNoShowTests(NoShowFixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.coach_user = User.objects.create_user(
            username="no_show_coach",
            email="no-show-coach@example.com",
            password="pw12345678",
        )
        UserDataConsent.objects.update_or_create(
            user=self.coach_user, defaults={"crushlu_consent_given": True}
        )
        CrushCoach.objects.create(user=self.coach_user, is_active=True)
        self.client.force_login(self.coach_user)

    def _confirm(self, event, shown):
        """POST what the page sends: the exact candidate list it showed."""
        return self.client.post(
            coach_no_shows(event),
            {"candidates": candidate_fingerprint(r.pk for r in shown)},
        )

    @override_settings(WALLET_GOOGLE_BULK_UPDATE_LIMIT=1)
    def test_a_press_over_the_google_cap_leaves_the_rest_for_the_next(self):
        event = make_event("Busy mixer", self.now - timedelta(days=1))
        self._attended(event)
        first = self._google_holder(event)
        second = self._google_holder(event)

        with mock.patch(
            "crush_lu.wallet.passkit_service.refresh_ticket_serials"
        ), mock.patch("crush_lu.wallet.google_api.refresh_google_wallet_objects"):
            response = self._confirm(event, [first, second])
            self.assertEqual(self._status(first), "no_show")
            self.assertEqual(self._status(second), "confirmed")
            self.assertIn(
                "1 more member is still to be marked. Press the button again "
                "to finish.",
                self._messages(response),
            )
            # The card offers the rest on reload, and the next press takes it.
            page = self.client.get(coach_event_page(event))
            self.assertContains(page, "Yes, mark 1 member as no-show")
            self._confirm(event, [second])

        self.assertEqual(self._status(second), "no_show")

    def test_a_list_changed_since_the_page_loaded_is_refused(self):
        # An offline PWA replays a queued POST up to 24 hours later, and a
        # second coach or an admin can change the roster meanwhile: the view
        # marks only the list that was confirmed, or nothing.
        event = make_event("Stale mixer", self.now - timedelta(days=1))
        self._attended(event)
        shown = self._register(event, "confirmed")
        restored = self._register(event, "cancelled")
        EventRegistration.objects.filter(pk=restored.pk).update(status="confirmed")

        response = self._confirm(event, [shown])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._status(shown), "confirmed")
        self.assertEqual(self._status(restored), "confirmed")
        self.assertIn(
            "The list of members changed since the page was loaded, so nothing "
            "was recorded. Check it and try again.",
            self._messages(response),
        )

    def test_marking_schedules_the_google_indexing_ping(self):
        # Leaving the seat-holding statuses changes the page's capacity, and
        # .update() fires none of the signals that would have said so.
        event = make_event("Indexed mixer", self.now - timedelta(days=1))
        self._attended(event)
        missing = self._register(event, "confirmed")

        with mock.patch(
            "crush_lu.signals._schedule_event_indexing_notification"
        ) as schedule:
            self._confirm(event, [missing])

        self.assertEqual(self._status(missing), "no_show")
        schedule.assert_called_once_with(event.pk, skip_if_ineligible=True)

    def test_coach_marks_an_ended_events_no_shows(self):
        event = make_event("Coach mixer", self.now - timedelta(days=1))
        attended = self._attended(event)
        confirmed = self._register(event, "confirmed")
        pending = self._register(event, "pending")
        waiting = self._register(event, "waitlist")

        response = self._confirm(event, [confirmed, pending])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], coach_event_page(event))
        self.assertEqual(self._status(confirmed), "no_show")
        self.assertEqual(self._status(pending), "no_show")
        self.assertEqual(self._status(attended), "attended")
        self.assertEqual(self._status(waiting), "waitlist")
        self.assertIn("Marked 2 registrations as no-show.", self._messages(response))

    def test_coach_is_refused_while_the_check_in_window_is_open(self):
        event = make_event("Tonight's mixer", self.now - timedelta(hours=3))
        self._attended(event)
        confirmed = self._register(event, "confirmed")

        response = self.client.post(coach_no_shows(event))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(
            "No-shows were not recorded: its check-in window has not closed yet.",
            self._messages(response),
        )

    def test_member_who_is_not_a_coach_changes_nothing(self):
        event = make_event("Member mixer", self.now - timedelta(days=1))
        self._attended(event)
        confirmed = self._register(event, "confirmed")
        member = User.objects.create_user(
            username="plain_member",
            email="plain-member@example.com",
            password="pw12345678",
        )
        self.client.force_login(member)

        response = self.client.post(coach_no_shows(event))

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response["Location"], coach_event_page(event))
        self.assertEqual(self._status(confirmed), "confirmed")

    def test_get_is_not_allowed(self):
        event = make_event("Get mixer", self.now - timedelta(days=1))
        self._attended(event)
        confirmed = self._register(event, "confirmed")

        response = self.client.get(coach_no_shows(event))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(self._status(confirmed), "confirmed")

    def test_event_page_offers_the_button_once_check_in_has_closed(self):
        event = make_event("Offered mixer", self.now - timedelta(days=1))
        self._attended(event)
        missing = self._register(event, "confirmed")

        response = self.client.get(coach_event_page(event))

        self.assertContains(response, "data-no-show-panel")
        self.assertContains(response, f'action="{coach_no_shows(event)}"')
        self.assertContains(response, "Yes, mark 1 member as no-show")
        self.assertContains(
            response, f'name="candidates" value="{candidate_fingerprint([missing.pk])}"'
        )

    def test_event_page_hides_the_button_while_check_in_is_open(self):
        event = make_event("Open door mixer", self.now - timedelta(hours=3))
        self._attended(event)
        self._register(event, "confirmed")

        response = self.client.get(coach_event_page(event))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-no-show-panel")

    def test_event_page_hides_the_button_when_everyone_came(self):
        event = make_event("Full house mixer", self.now - timedelta(days=1))
        self._attended(event)
        self._attended(event)

        response = self.client.get(coach_event_page(event))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-no-show-panel")


class NoShowServiceTests(NoShowFixturesMixin, TestCase):
    """The helper itself, below the callers' own (unlocked) checks."""

    def test_locks_the_event_then_its_registrations_before_the_curated_check(
        self,
    ):
        # mark_provisional() and lock() take the event, then its registrations
        # by pk. The helper must take the same order and re-check the curated
        # groups only once both are held. SQLite drops FOR UPDATE, so the order
        # is recorded here rather than proven by a passing suite.
        event = make_event("Locked mixer", self.now - timedelta(days=1))
        self._attended(event)
        self._register(event, "confirmed")
        calls = []
        original_lock = QuerySet.select_for_update
        original_check = no_shows.curated_group_refusal

        def recording_lock(queryset, *args, **kwargs):
            calls.append(queryset.model.__name__)
            return original_lock(queryset, *args, **kwargs)

        def recording_check(checked_event):
            calls.append("curated_group_refusal")
            return original_check(checked_event)

        with mock.patch.object(
            QuerySet, "select_for_update", recording_lock
        ), mock.patch.object(no_shows, "curated_group_refusal", recording_check):
            no_shows.mark_unattended_no_show([event], context="test")

        self.assertEqual(
            calls[:3], ["MeetupEvent", "EventRegistration", "curated_group_refusal"]
        )

    def test_the_locked_recheck_refuses_a_group_certified_meanwhile(self):
        # The callers' check ran before the locks; a group that became
        # provisional since is caught here, and nothing changes.
        event = self._curated_event()
        attended = self._curated_member(
            event, "attended", checked_in_at=event.date_time
        )
        missing = self._curated_member(event, "confirmed")
        self._provisional_group(event, [attended, missing])

        [result] = no_shows.mark_unattended_no_show([event], context="test")

        self.assertEqual(result.updated, 0)
        self.assertIsNotNone(result.refusal)
        self.assertEqual(self._status(missing), "confirmed")

    def test_only_ids_marks_nothing_outside_the_confirmed_list(self):
        # The coach page passes the list it showed: a row restored after the
        # check is not in it, so it is never marked.
        event = make_event("Bound mixer", self.now - timedelta(days=1))
        self._attended(event)
        shown = self._register(event, "confirmed")
        restored = self._register(event, "confirmed")

        [result] = no_shows.mark_unattended_no_show(
            [event], context="test", only_ids=[shown.pk]
        )

        self.assertEqual(result.updated, 1)
        self.assertEqual(self._status(shown), "no_show")
        self.assertEqual(self._status(restored), "confirmed")
