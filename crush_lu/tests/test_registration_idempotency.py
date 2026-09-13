"""Registration retries, including real concurrent PostgreSQL requests.

SQLite cannot exercise SELECT FOR UPDATE. The PostgreSQL cases deliberately
hold both requests after the early duplicate check and before the event lock,
so their success cannot come from one request finishing before the other starts.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.db import IntegrityError, connection, connections
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from crush_lu.forms import EventRegistrationForm
from crush_lu.models import (
    CrushProfile,
    EventRegistration,
    EventRegistrationPreference,
    MeetupEvent,
    UserDataConsent,
)


class RegistrationFixtures:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = self.member("registrant")
        self.confirmation = self.enterContext(
            patch("crush_lu.views_events.send_event_registration_confirmation")
        )
        self.payment_pending = self.enterContext(
            patch("crush_lu.views_events.send_event_payment_pending_notification")
        )
        self.waitlist = self.enterContext(
            patch("crush_lu.views_events.send_event_waitlist_notification")
        )

    def member(self, name):
        user = get_user_model().objects.create_user(
            username=name, email=f"{name}@example.com", password="test-password"
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1990, 1, 1),
            gender="M",
            location="Luxembourg",
            event_languages=["en"],
        )
        return user

    def event(self, **overrides):
        values = {
            "title": "Registration retry",
            "description": "Concurrency regression",
            "event_type": "mixer",
            "date_time": timezone.now() + timedelta(days=7),
            "registration_deadline": timezone.now() + timedelta(days=5),
            "location": "Luxembourg",
            "address": "1 Test Street",
            "max_participants": 1,
            "profile_requirement": "none",
            "is_published": True,
            "has_food_component": True,
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def client_for(self, user=None):
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(user or self.user)
        return client

    def register(self, client, event, **payload):
        return client.post(
            f"/en/events/{event.pk}/register/",
            {
                "preferred_age_min": "25",
                "preferred_age_max": "40",
                "dietary_restrictions": "Original answer",
                **payload,
            },
        )

    def assert_notifications(self, confirmed=0, pending=0, waitlist=0):
        self.assertEqual(self.confirmation.call_count, confirmed)
        self.assertEqual(self.payment_pending.call_count, pending)
        self.assertEqual(self.waitlist.call_count, waitlist)


class RegistrationRetryTests(RegistrationFixtures, TestCase):
    def test_repeated_application_preserves_preferences_and_queue_position(self):
        event = self.event(event_type="speed_dating", registration_mode="curated")
        client = self.client_for()
        first = self.register(client, event)
        registration = EventRegistration.objects.get(event=event, user=self.user)
        original_time = registration.registered_at

        retry = self.register(
            client, event, preferred_age_min="30", dietary_restrictions="Retry answer"
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(retry.status_code, 302)
        registration.refresh_from_db()
        self.assertEqual(registration.status, "applied")
        self.assertEqual(registration.registered_at, original_time)
        self.assertEqual(registration.dietary_restrictions, "Original answer")
        preference = EventRegistrationPreference.objects.get(registration=registration)
        self.assertEqual(preference.preferred_age_min, 25)
        self.assertEqual(EventRegistration.objects.filter(event=event).count(), 1)
        self.assertEqual(event.get_confirmed_count(), 0)
        self.assert_notifications()

    def test_registration_appearing_while_waiting_for_event_lock_is_reused(self):
        event = self.event()
        client = self.client_for()
        manager = MeetupEvent.objects
        original_lock = manager.select_for_update
        winner = None

        def other_request_finishes(*args, **kwargs):
            nonlocal winner
            winner = EventRegistration.objects.create(
                event=event,
                user=self.user,
                status="confirmed",
                dietary_restrictions="Winning answer",
            )
            return original_lock(*args, **kwargs)

        with patch.object(manager, "select_for_update", other_request_finishes):
            response = self.register(client, event)

        self.assertEqual(response.status_code, 302)
        winner.refresh_from_db()
        self.assertEqual(winner.status, "confirmed")
        self.assertEqual(winner.dietary_restrictions, "Winning answer")
        self.assertEqual(EventRegistration.objects.filter(event=event).count(), 1)
        self.assertEqual(event.get_confirmed_count(), 1)
        self.assert_notifications()

    def test_competing_insert_rolls_back_savepoint_and_returns_winner(self):
        event = self.event()
        client = self.client_for()
        original_form_save = EventRegistrationForm.save
        winner = None

        def insert_before_savepoint(form, *args, **kwargs):
            # Represent a writer that doesn't lock MeetupEvent. Persist its row
            # before the attempted insert's savepoint, then let the real unique
            # event/member constraint fail (rather than mocking IntegrityError).
            nonlocal winner
            winner = EventRegistration.objects.create(
                event=event, user=self.user, status="confirmed"
            )
            return original_form_save(form, *args, **kwargs)

        with patch.object(EventRegistrationForm, "save", insert_before_savepoint):
            response = self.register(client, event)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventRegistration.objects.get(event=event).pk, winner.pk)
        self.assertEqual(event.get_confirmed_count(), 1)
        notices = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertEqual(notices, ["You are already registered for this event."])
        self.assert_notifications()

    def test_integrity_error_without_a_winner_is_not_silenced(self):
        event = self.event()
        client = self.client_for()
        with patch.object(
            EventRegistration, "save", side_effect=IntegrityError("other")
        ):
            with self.assertRaisesMessage(IntegrityError, "other"):
                self.register(client, event)
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())
        self.assert_notifications()

    def test_competing_insert_does_not_change_gender_or_create_a_profile(self):
        for has_profile in (True, False):
            with self.subTest(has_profile=has_profile):
                cache.clear()
                if has_profile:
                    CrushProfile.objects.filter(user=self.user).update(gender="")
                else:
                    CrushProfile.objects.filter(user=self.user).delete()
                event = self.event(
                    max_participants_m=1, max_participants_f=1, max_participants_nb=0
                )
                self.assertTrue(event.gender_limits_active)
                client = self.client_for()
                original_form_save = EventRegistrationForm.save

                def insert_before_savepoint(form, *args, **kwargs):
                    EventRegistration.objects.create(
                        event=event, user=self.user, status="confirmed"
                    )
                    return original_form_save(form, *args, **kwargs)

                with patch.object(
                    EventRegistrationForm, "save", insert_before_savepoint
                ):
                    response = self.register(
                        client, event, gender="F", age_confirmation="on"
                    )

                self.assertEqual(response.status_code, 302)
                if has_profile:
                    self.assertEqual(
                        CrushProfile.objects.get(user=self.user).gender, ""
                    )
                else:
                    self.assertFalse(
                        CrushProfile.objects.filter(user=self.user).exists()
                    )
                self.assertEqual(
                    EventRegistration.objects.filter(event=event).count(), 1
                )
                self.assert_notifications()

    def test_unrelated_integrity_failure_is_not_hidden_by_existing_winner(self):
        event = self.event()
        client = self.client_for()
        original_form_save = EventRegistrationForm.save

        def invalid_insert_with_winner(form, *args, **kwargs):
            EventRegistration.objects.create(
                event=event, user=self.user, status="confirmed"
            )
            registration = original_form_save(form, *args, **kwargs)
            # A real NOT NULL failure takes precedence over the duplicate key.
            # Finding a winner must not turn that unrelated error into success.
            registration.dietary_restrictions = None
            return registration

        with patch.object(EventRegistrationForm, "save", invalid_insert_with_winner):
            with self.assertRaises(IntegrityError):
                self.register(client, event)
        self.assert_notifications()

    def test_successful_gender_selection_checks_pool_before_persisting(self):
        CrushProfile.objects.filter(user=self.user).update(gender="")
        event = self.event(
            max_participants=10,
            max_participants_m=0,
            max_participants_f=10,
            max_participants_nb=0,
        )
        response = self.register(self.client_for(), event, gender="M")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventRegistration.objects.get(event=event).status, "waitlist")
        self.assertEqual(CrushProfile.objects.get(user=self.user).gender, "M")
        self.assertEqual(event.get_confirmed_count(), 0)
        self.assert_notifications(waitlist=1)

    def test_successful_registration_creates_profile_after_winning(self):
        CrushProfile.objects.filter(user=self.user).delete()
        event = self.event(
            max_participants_m=1, max_participants_f=1, max_participants_nb=0
        )
        self.assertTrue(event.gender_limits_active)
        response = self.register(
            self.client_for(), event, gender="F", age_confirmation="on"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventRegistration.objects.get(event=event).status, "confirmed")
        self.assertEqual(CrushProfile.objects.get(user=self.user).gender, "F")
        self.assertEqual(event.get_confirmed_count(), 1)
        self.assert_notifications(confirmed=1)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class ConcurrentRegistrationTests(RegistrationFixtures, TransactionTestCase):
    def concurrent_posts(self, event, *, users=None):
        clients = [self.client_for(user) for user in (users or [self.user, self.user])]
        barrier = Barrier(2, timeout=15)
        manager = MeetupEvent.objects
        original_lock = manager.select_for_update

        def synchronized_lock(*args, **kwargs):
            # Both HTTP requests have already passed the early registration
            # check. The actual SELECT FOR UPDATE runs after this barrier.
            barrier.wait()
            return original_lock(*args, **kwargs)

        def post(client):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout TO '20s'")
                return self.register(client, event)
            finally:
                connections.close_all()

        with patch.object(manager, "select_for_update", synchronized_lock):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(post, client) for client in clients]
                responses = [future.result(timeout=30) for future in futures]
        self.assertEqual([response.status_code for response in responses], [302, 302])
        return responses

    def test_same_member_takes_one_free_seat_and_receives_one_confirmation(self):
        event = self.event()
        self.concurrent_posts(event)
        registration = EventRegistration.objects.get(event=event)
        self.assertEqual(registration.status, "confirmed")
        self.assertEqual(event.get_confirmed_count(), 1)
        self.assert_notifications(confirmed=1)

    def test_same_member_holds_one_paid_seat_and_receives_one_payment_notice(self):
        event = self.event(registration_fee=Decimal("10.00"))
        self.concurrent_posts(event)
        registration = EventRegistration.objects.get(event=event)
        self.assertEqual(registration.status, "pending")
        self.assertFalse(registration.payment_confirmed)
        self.assertEqual(event.get_confirmed_count(), 1)
        self.assert_notifications(pending=1)

    def test_same_member_joins_waitlist_once(self):
        event = self.event()
        EventRegistration.objects.create(
            event=event, user=self.member("existing"), status="confirmed"
        )
        self.concurrent_posts(event)
        self.assertEqual(
            EventRegistration.objects.get(event=event, user=self.user).status,
            "waitlist",
        )
        self.assertEqual(event.get_confirmed_count(), 1)
        self.assertEqual(event.get_waitlist_count(), 1)
        self.assert_notifications(waitlist=1)

    def test_cancelled_registration_is_reactivated_only_once(self):
        event = self.event()
        original = EventRegistration.objects.create(
            event=event, user=self.user, status="cancelled"
        )
        self.concurrent_posts(event)
        registration = EventRegistration.objects.get(event=event)
        self.assertEqual(registration.pk, original.pk)
        self.assertEqual(registration.status, "confirmed")
        self.assertEqual(event.get_confirmed_count(), 1)
        self.assert_notifications(confirmed=1)

    def test_curated_application_has_one_preference_and_no_seat(self):
        event = self.event(event_type="speed_dating", registration_mode="curated")
        self.concurrent_posts(event)
        registration = EventRegistration.objects.get(event=event)
        self.assertEqual(registration.status, "applied")
        self.assertEqual(
            EventRegistrationPreference.objects.filter(
                registration=registration
            ).count(),
            1,
        )
        self.assertEqual(event.get_confirmed_count(), 0)
        self.assert_notifications()

    def test_two_members_competing_for_last_seat_keep_capacity(self):
        event = self.event()
        self.concurrent_posts(event, users=[self.user, self.member("second")])
        self.assertEqual(
            sorted(
                EventRegistration.objects.filter(event=event).values_list(
                    "status", flat=True
                )
            ),
            ["confirmed", "waitlist"],
        )
        self.assertEqual(event.get_confirmed_count(), 1)
        self.assert_notifications(confirmed=1, waitlist=1)
