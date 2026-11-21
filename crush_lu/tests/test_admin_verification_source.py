"""crush-admin: tell LuxID-verified members from event-verified ones.

Method column on the profile list plus "LuxID linked" and "Attended an
event" filters (a LuxID-verified member who later attends keeps
verification_method="luxid", so attendance needs its own filter).
"""

from datetime import date, timedelta

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from crush_lu.admin.filters import AttendedEventFilter, LuxIDLinkedFilter
from crush_lu.admin.profiles import CrushProfileAdmin
from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent

User = get_user_model()


def make(name, method="", luxid=False, attended=False, event=None):
    user = User.objects.create_user(username=name, email=f"{name}@example.com")
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1990, 1, 1),
        verification_status="verified" if method else "pending",
        verification_method=method,
    )
    if luxid:
        SocialAccount.objects.create(user=user, provider="luxid", uid=f"lux-{name}")
    if attended:
        EventRegistration.objects.create(event=event, user=user, status="attended")
    return profile


class VerificationSourceAdminTests(TestCase):
    def setUp(self):
        self.event = MeetupEvent.objects.create(
            title="Mixer",
            description="x",
            event_type="mixer",
            date_time=timezone.now() - timedelta(days=2),
            location="Luxembourg",
            address="x",
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(days=3),
            registration_fee=0,
            is_published=True,
        )
        self.luxid_then_event = make(
            "a", "luxid", luxid=True, attended=True, event=self.event
        )
        self.luxid_only = make("b", "luxid", luxid=True)
        self.coach = make("c", "coach_event", attended=True, event=self.event)
        self.pending = make("d")

    def run_filter(self, cls, value):
        f = cls(None, {cls.parameter_name: [value]}, CrushProfile, CrushProfileAdmin)
        self.assertEqual(f.value(), value)
        return set(f.queryset(None, CrushProfile.objects.all()))

    def test_method_column_and_filters_registered(self):
        self.assertIn("verification_method", CrushProfileAdmin.list_display)
        self.assertIn(LuxIDLinkedFilter, CrushProfileAdmin.list_filter)
        self.assertIn(AttendedEventFilter, CrushProfileAdmin.list_filter)

    def test_luxid_linked(self):
        self.assertEqual(
            self.run_filter(LuxIDLinkedFilter, "yes"),
            {self.luxid_then_event, self.luxid_only},
        )
        self.assertEqual(
            self.run_filter(LuxIDLinkedFilter, "no"), {self.coach, self.pending}
        )

    def test_attended_event_covers_luxid_members_who_came_later(self):
        self.assertEqual(
            self.run_filter(AttendedEventFilter, "yes"),
            {self.luxid_then_event, self.coach},
        )
        self.assertEqual(
            self.run_filter(AttendedEventFilter, "no"),
            {self.luxid_only, self.pending},
        )
        # Attending does not rewrite how the member was first verified.
        self.luxid_then_event.refresh_from_db()
        self.assertEqual(self.luxid_then_event.verification_method, "luxid")

    def test_changelist_renders_with_both_filters(self):
        admin_user = User.objects.create_superuser("boss", "boss@example.com", "pw")
        self.client.force_login(admin_user)
        response = self.client.get(
            "/crush-admin/crush_lu/crushprofile/?luxid_linked=yes&attended_event=yes",
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cl"].result_count, 1)
