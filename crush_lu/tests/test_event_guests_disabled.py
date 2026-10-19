"""Event guests are switched off site-wide (Tom, 2026-10-17).

A guest took a seat the capacity checks never counted, so the feature is
dropped until it is rebuilt: with ``EVENT_GUESTS_ENABLED`` off, no sign-up
form offers a guest even on an event whose ``allow_plus_ones`` is still set,
a posted guest is ignored, and the registration admin shows a stored guest
read-only. Decision: ai-memory-hub/runbooks/crush-task-management.md
(Epic 17, Audit A10).

The setting is read from the environment, so every test pins its own value.
"""

from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.contrib.admin.utils import flatten_fieldsets
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from crush_lu.admin.events import EventRegistrationAdmin
from crush_lu.forms import EventRegistrationForm
from crush_lu.models import EventRegistration, MeetupEvent

GUEST_FIELDS = ("bringing_guest", "guest_name")


@override_settings(EVENT_GUESTS_ENABLED=False)
class EventGuestsSwitchTests(TestCase):
    def setUp(self):
        cache.clear()
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

    def test_guests_off_when_switched_off(self):
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


@override_settings(EVENT_GUESTS_ENABLED=False)
class EventRegistrationAdminGuestSwitchTests(TestCase):
    """Staff cannot add a guest in the admin while guests are off."""

    def setUp(self):
        cache.clear()
        self.admin = EventRegistrationAdmin(EventRegistration, AdminSite())
        staff = get_user_model().objects.create_superuser(
            username="coach@example.com",
            email="coach@example.com",
            password="pw",
        )
        self.request = RequestFactory().get("/admin/")
        self.request.user = staff
        member = get_user_model().objects.create_user(
            username="member@example.com",
            email="member@example.com",
            password="pw",
        )
        event = MeetupEvent.objects.create(
            title="Mixer",
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
        # A guest stored before the switch went off.
        self.registration = EventRegistration.objects.create(
            event=event,
            user=member,
            status="confirmed",
            bringing_guest=True,
            guest_name="Alex",
        )

    def test_guest_fields_are_read_only_on_add_and_change(self):
        for obj, change in ((None, False), (self.registration, True)):
            with self.subTest(change=change):
                readonly = self.admin.get_readonly_fields(self.request, obj)
                form = self.admin.get_form(self.request, obj, change=change)
                for name in GUEST_FIELDS:
                    self.assertIn(name, readonly)
                    # Not a form field, so a posted value is never saved.
                    self.assertNotIn(name, form.base_fields)

    def test_stored_guest_stays_visible(self):
        shown = flatten_fieldsets(
            self.admin.get_fieldsets(self.request, self.registration)
        )
        for name in GUEST_FIELDS:
            self.assertIn(name, shown)

    @override_settings(EVENT_GUESTS_ENABLED=True)
    def test_switch_makes_the_guest_fields_editable_again(self):
        form = self.admin.get_form(self.request, self.registration, change=True)
        for name in GUEST_FIELDS:
            self.assertNotIn(
                name, self.admin.get_readonly_fields(self.request, self.registration)
            )
            self.assertIn(name, form.base_fields)
