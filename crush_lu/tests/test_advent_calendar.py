"""
Advent Calendar date gating and page rendering.

The date checks convert "now" into the calendar's own timezone. They used to
import a timezone package that Django 6 no longer installs, so every advent
view fell into its broad ``except`` and redirected home.
"""

from datetime import date, datetime, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from crush_lu.models import (
    AdventCalendar,
    AdventDoor,
    AdventDoorContent,
    JourneyConfiguration,
    QRCodeToken,
    SpecialUserExperience,
)
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

NOW = "crush_lu.models.advent.timezone.now"


def utc(*args):
    return datetime(*args, tzinfo=dt_timezone.utc)


def make_calendar(year=2024, **kwargs):
    return AdventCalendar(
        year=year,
        start_date=date(year, 12, 1),
        end_date=date(year, 12, 24),
        **kwargs,
    )


class AdventCalendarDateTests(TestCase):
    def test_december_date(self):
        calendar = make_calendar()
        with patch(NOW, return_value=utc(2024, 12, 5, 12, 0)):
            self.assertTrue(calendar.is_december())
            self.assertEqual(calendar.get_current_day(), 5)
            self.assertEqual(calendar.get_available_doors(), [1, 2, 3, 4, 5])

    def test_non_december_date(self):
        calendar = make_calendar()
        with patch(NOW, return_value=utc(2024, 11, 15, 12, 0)):
            self.assertFalse(calendar.is_december())
            self.assertIsNone(calendar.get_current_day())
            self.assertFalse(calendar.is_door_available(1))
            self.assertEqual(calendar.get_available_doors(), [])

    def test_december_of_another_year(self):
        calendar = make_calendar(year=2024)
        with patch(NOW, return_value=utc(2023, 12, 5, 12, 0)):
            self.assertFalse(calendar.is_december())
            self.assertIsNone(calendar.get_current_day())
            self.assertFalse(calendar.is_door_available(1))

    def test_uses_calendar_timezone_not_utc(self):
        # 23:30 UTC on Nov 30 is already 00:30 on Dec 1 in Luxembourg.
        calendar = make_calendar()
        with patch(NOW, return_value=utc(2024, 11, 30, 23, 30)):
            self.assertTrue(calendar.is_december())
            self.assertEqual(calendar.get_current_day(), 1)
            self.assertTrue(calendar.is_door_available(1))

    def test_door_not_yet_available(self):
        calendar = make_calendar()
        with patch(NOW, return_value=utc(2024, 12, 5, 12, 0)):
            self.assertTrue(calendar.is_door_available(5))
            self.assertTrue(calendar.is_door_available(3))
            self.assertFalse(calendar.is_door_available(6))
            self.assertFalse(calendar.is_door_available(24))

    def test_strict_mode_opens_only_todays_door(self):
        calendar = make_calendar(allow_catch_up=False)
        with patch(NOW, return_value=utc(2024, 12, 5, 12, 0)):
            self.assertTrue(calendar.is_door_available(5))
            self.assertFalse(calendar.is_door_available(3))

    def test_out_of_range_doors_never_available(self):
        calendar = make_calendar()
        with patch(NOW, return_value=utc(2024, 12, 24, 12, 0)):
            self.assertFalse(calendar.is_door_available(0))
            self.assertFalse(calendar.is_door_available(25))


class AdventCalendarViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="advent@example.com",
            email="advent@example.com",
            password="testpass123",
            first_name="Marie",
            last_name="Dupont",
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        EmailAddress.objects.create(
            user=self.user, email=self.user.email, verified=True, primary=True
        )
        # linked_user is what grants access; the matching names keep the
        # test valid while the views still match by name.
        experience = SpecialUserExperience.objects.create(
            first_name="Marie",
            last_name="Dupont",
            linked_user=self.user,
            is_active=True,
        )
        journey = JourneyConfiguration.objects.create(
            special_experience=experience,
            journey_type="advent_calendar",
            is_active=True,
            journey_name="Marie's Advent Calendar",
        )
        self.calendar = AdventCalendar.objects.create(
            journey=journey,
            year=2024,
            start_date=date(2024, 12, 1),
            end_date=date(2024, 12, 24),
        )
        for number in (1, 2, 3):
            AdventDoor.objects.create(calendar=self.calendar, door_number=number)
        self.client.force_login(self.user)

    def test_calendar_renders_in_december(self):
        with patch(NOW, return_value=utc(2024, 12, 2, 12, 0)):
            response = self.client.get("/en/advent/", HTTP_HOST="crush.lu")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/advent/calendar.html")
        self.assertEqual(response.context["current_day"], 2)
        available = [
            d["door_number"] for d in response.context["doors"] if d["is_available"]
        ]
        self.assertEqual(available, [1, 2])

    def test_calendar_locked_outside_december(self):
        with patch(NOW, return_value=utc(2024, 11, 15, 12, 0)):
            response = self.client.get("/en/advent/", HTTP_HOST="crush.lu")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/advent/calendar_locked.html")


class CreateAdventCalendarCommandTests(TestCase):
    def test_creates_calendar_doors_and_qr_tokens(self):
        user = User.objects.create_user(
            username="marie@example.com",
            email="marie@example.com",
            password="testpass123",
            first_name="Marie",
            last_name="Dupont",
        )

        call_command(
            "create_advent_calendar",
            "--first-name",
            "Marie",
            "--last-name",
            "Dupont",
            "--year",
            "2026",
            "--generate-qr",
            stdout=StringIO(),
        )

        calendar = AdventCalendar.objects.get(
            journey__special_experience__first_name="Marie"
        )
        self.assertEqual(calendar.year, 2026)
        self.assertEqual(calendar.start_date, date(2026, 12, 1))
        self.assertEqual(calendar.end_date, date(2026, 12, 24))
        self.assertEqual(calendar.timezone_name, "Europe/Luxembourg")
        self.assertEqual(calendar.doors.count(), 24)
        self.assertEqual(
            AdventDoorContent.objects.filter(door__calendar=calendar).count(), 24
        )
        # 4 "required" + 4 "bonus" doors in DEFAULT_DOOR_CONFIG.
        self.assertEqual(
            QRCodeToken.objects.filter(door__calendar=calendar, user=user).count(), 8
        )
