"""Event guests are switched off site-wide (Tom, 2026-10-17; audit A10).

A guest took a seat the capacity checks never counted, so the feature is
dropped until it is rebuilt: with ``EVENT_GUESTS_ENABLED`` off, no sign-up
form offers a guest even on an event whose ``allow_plus_ones`` is still set,
and a posted guest is ignored.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.forms import EventRegistrationForm
from crush_lu.models import MeetupEvent


class EventGuestsSwitchTests(TestCase):
    def setUp(self):
        self.event = MeetupEvent.objects.create(
            title="Mixer with plus-ones",
            description="Guests allowed on the event",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            allow_plus_ones=True,
        )

    def test_guests_off_by_default(self):
        self.assertFalse(self.event.guests_open)
        form = EventRegistrationForm(event=self.event)
        self.assertNotIn("bringing_guest", form.fields)
        self.assertNotIn("guest_name", form.fields)

    def test_posted_guest_is_ignored(self):
        form = EventRegistrationForm(
            data={"bringing_guest": "on", "guest_name": "Someone"}, event=self.event
        )
        form.is_valid()
        self.assertNotIn("bringing_guest", form.cleaned_data)
        self.assertNotIn("guest_name", form.cleaned_data)

    @override_settings(EVENT_GUESTS_ENABLED=True)
    def test_switch_brings_the_fields_back_where_the_event_allows_them(self):
        self.assertTrue(self.event.guests_open)
        self.assertIn("bringing_guest", EventRegistrationForm(event=self.event).fields)
        self.event.allow_plus_ones = False
        self.assertNotIn(
            "bringing_guest", EventRegistrationForm(event=self.event).fields
        )
