"""Privacy-safe member surfaces for elastic curated speed-dating groups.

Curated sign-ups are applications, not seat claims. The public event page may
show coarse overall interest and configured group options, but never the
organiser-only gender/preference pool or an exact shortage. Direct events keep
their established capacity, gender-pool and waitlist behaviour.

Paths are literal because the Crush host middleware replaces the active
urlconf; ``reverse()`` would build a path for the default host instead.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    UserDataConsent,
)

User = get_user_model()


class CuratedGroupSurfaceTests(TestCase):
    def setUp(self):
        # The event registration endpoints are user-rate-limited in a cache
        # that outlives SQLite TestCase rollbacks. Clear it even though these
        # tests render only GETs, so their shared user IDs cannot inherit debt.
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.viewer = self._member("viewer@example.com", gender="F")
        self.client.login(username=self.viewer.username, password="testpass123")

        self.curated = self._event(
            "Elastic Curated Night",
            registration_mode="curated",
            registration_fee=Decimal("15.00"),
            group_size=6,
            planned_groups=2,
            max_participants_m=9,
            max_participants_f=9,
            max_participants_nb=0,
        )
        self.direct = self._event(
            "Direct Night",
            registration_mode="direct",
            max_participants_m=9,
            max_participants_f=9,
            max_participants_nb=0,
        )

    def _event(self, title, **overrides):
        values = {
            "title": title,
            "description": "Group surface test",
            "event_type": "speed_dating",
            "date_time": timezone.now() + timedelta(days=7),
            "location": "Luxembourg",
            "address": "123 Test Street",
            "max_participants": 18,
            "registration_deadline": timezone.now() + timedelta(days=5),
            "is_published": True,
            "profile_requirement": "none",
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def _member(self, email, gender="M"):
        user = User.objects.create_user(
            username=email,
            email=email,
            password="testpass123",
            first_name=email.split("@", 1)[0],
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location="Luxembourg",
            event_languages=["en"],
        )
        return user

    def _applications(self, count):
        for index in range(count):
            user = self._member(
                f"applicant-{index}@example.com",
                gender="F" if index == count - 1 else "M",
            )
            EventRegistration.objects.create(
                event=self.curated,
                user=user,
                status="applied",
            )

    def _detail(self, event, language="en"):
        return self.client.get(f"/{language}/events/{event.pk}/")

    def test_curated_page_uses_coarse_group_outlook_and_application_copy(self):
        self._applications(6)

        response = self._detail(self.curated)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["gender_pool_availability"], [])
        outlook = response.context["curated_group_outlook"]
        self.assertEqual(
            set(outlook),
            {
                "interest_state",
                "group_size",
                "planned_groups",
                "max_groups",
                "parallel_groups_possible",
            },
        )
        self.assertEqual(outlook["interest_state"], "exploring")
        self.assertEqual(outlook["group_size"], 6)
        self.assertEqual(outlook["planned_groups"], 2)
        self.assertNotIn("by_pool", outlook)

        self.assertContains(response, "Flexible groups")
        self.assertContains(response, "Apply for This Event")
        self.assertContains(response, "Applying does not reserve a place.")
        self.assertContains(
            response,
            "If selected for a viable provisional group, you will be invited to pay.",
        )
        self.assertContains(
            response,
            "Groups are finalized before the first round and stay together for the whole evening.",
        )
        self.assertContains(
            response,
            "Every finalized group is scheduled to give each participant at least five mutually compatible mini-dates, with seven as the goal.",
        )
        self.assertNotContains(response, "Register for This Event")
        self.assertNotContains(response, "Spots remaining:")
        self.assertNotContains(response, "Men:")
        self.assertNotContains(response, "Women:")

    def test_organiser_only_pool_and_exact_shortage_cannot_reach_template(self):
        aggregate = {
            "applications": 1234567,
            "group_size": 6,
            "max_groups": 3,
            "planned_groups": 2,
            "groups_unlocked": 1,
            "next_group_at": 2345678,
            "first_timers": 3456789,
            "certified": 4567890,
            "by_pool": {"men": 5678901, "women": 6789012},
        }

        with patch.object(MeetupEvent, "get_application_pool", return_value=aggregate):
            response = self._detail(self.curated)

        outlook = response.context["curated_group_outlook"]
        self.assertEqual(outlook["interest_state"], "exploring")
        self.assertEqual(outlook["max_groups"], 3)
        for private_value in (
            "1234567",
            "2345678",
            "3456789",
            "4567890",
            "5678901",
            "6789012",
        ):
            self.assertNotContains(response, private_value)
        self.assertNotIn("applications", outlook)
        self.assertNotIn("next_group_at", outlook)
        self.assertNotIn("by_pool", outlook)

    def test_direct_mode_keeps_capacity_pool_and_registration_copy(self):
        response = self._detail(self.direct)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["curated_group_outlook"])
        self.assertTrue(response.context["gender_pool_availability"])
        self.assertContains(response, "Capacity")
        self.assertContains(response, "Max participants: 18")
        self.assertContains(response, "Register for This Event")
        self.assertNotContains(response, "Flexible groups")
        self.assertNotContains(response, "Apply for This Event")

        structured_data = json.loads(response.context["event_jsonld"])
        self.assertEqual(structured_data["remainingAttendeeCapacity"], 18)
        self.assertEqual(
            structured_data["offers"]["availability"],
            "https://schema.org/InStock",
        )

    def test_incomplete_curated_configuration_has_safe_fallback(self):
        event = self._event(
            "Configuration Pending",
            registration_mode="curated",
            group_size=None,
            planned_groups=None,
        )

        response = self._detail(event)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["curated_group_outlook"]["group_size"])
        self.assertContains(
            response,
            "The group size and number of groups will be set after applications are reviewed.",
        )
        self.assertNotContains(response, "None")

    def test_paid_applicant_sees_selection_then_payment_and_can_withdraw(self):
        EventRegistration.objects.create(
            event=self.curated,
            user=self.viewer,
            status="applied",
        )

        response = self._detail(self.curated)

        self.assertContains(response, "Your application is in!")
        self.assertContains(
            response,
            "will invite you to pay only if you are selected for a viable provisional group",
        )
        self.assertContains(response, "Selection is not guaranteed.")
        self.assertContains(response, "Withdraw Application")
        self.assertNotContains(response, "Cancel Registration")
        self.assertNotContains(response, "js-sumup-checkout-detail")

    def test_curated_structured_data_does_not_publish_stale_seats(self):
        for index in range(self.curated.max_participants):
            user = self._member(f"selected-{index}@example.com")
            EventRegistration.objects.create(
                event=self.curated,
                user=user,
                status="confirmed",
            )

        response = self._detail(self.curated)
        structured_data = json.loads(response.context["event_jsonld"])

        self.assertNotIn("remainingAttendeeCapacity", structured_data)
        self.assertEqual(
            structured_data["offers"]["availability"],
            "https://schema.org/InStock",
        )

    def test_member_contract_is_localized_in_formal_fr_and_informal_de(self):
        expectations = {
            "de": (
                "Flexible Gruppen",
                "Deine Bewerbung reserviert keinen Platz.",
                "Für diese Veranstaltung bewerben",
                "mindestens fünf gegenseitig passende Mini-Dates pro Person",
            ),
            "fr": (
                "Groupes flexibles",
                "Votre candidature ne réserve pas de place.",
                "Postuler à cet événement",
                "au moins cinq mini-rencontres mutuellement compatibles",
            ),
        }

        for language, phrases in expectations.items():
            with self.subTest(language=language):
                response = self._detail(self.curated, language=language)
                self.assertEqual(response.status_code, 200)
                for phrase in phrases:
                    self.assertContains(response, phrase)
