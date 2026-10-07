"""Registration must close by the event start (issue #1198)."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import EventRegistration
from crush_lu.tests.test_registration_idempotency import RegistrationFixtures


class DeadlineVersusStartTests(RegistrationFixtures, TestCase):
    def test_clean_rejects_deadline_after_start(self):
        event = self.event(
            date_time=timezone.now() + timedelta(days=2),
            registration_deadline=timezone.now() + timedelta(days=5),
        )
        with self.assertRaises(ValidationError) as ctx:
            event.full_clean()
        self.assertIn("registration_deadline", ctx.exception.message_dict)

    def test_clean_accepts_deadline_before_start(self):
        event = self.event()
        try:
            event.full_clean()
        except ValidationError as exc:
            self.assertNotIn("registration_deadline", exc.message_dict)

    def test_started_event_is_not_accepting_despite_future_deadline(self):
        event = self.event(
            date_time=timezone.now() - timedelta(hours=3),
            registration_deadline=timezone.now() + timedelta(days=5),
        )
        self.assertFalse(event.is_registration_accepting)
        self.assertFalse(event.is_registration_open)

    def test_upcoming_event_is_still_accepting(self):
        event = self.event()
        self.assertTrue(event.is_registration_accepting)
        self.assertTrue(event.is_registration_open)

    def test_register_post_for_started_event_creates_nothing(self):
        event = self.event(
            date_time=timezone.now() - timedelta(hours=3),
            registration_deadline=timezone.now() + timedelta(days=5),
        )
        self.register(self.client_for(), event)
        self.assertFalse(
            EventRegistration.objects.filter(event=event, user=self.user).exists()
        )
