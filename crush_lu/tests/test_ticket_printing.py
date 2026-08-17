"""
Unit and integration tests for Crush.lu check-in thermal ticket printing (PEC 80 / ESC/POS / RawBT).
"""

import base64
from datetime import timedelta
from django.contrib.auth.models import User
from django.core.signing import Signer
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushCoach, CrushProfile, EventRegistration, MeetupEvent
from crush_lu.services.ticket_printer import (
    build_checkin_ticket_base64,
    build_checkin_ticket_bytes,
    build_checkin_ticket_directives,
    preview_checkin_ticket_text,
)
from power_up.atmos.printing.escpos import CODEPAGE_CP858, INIT


class TestTicketPrinter(TestCase):
    """Test ticket formatting, ESC/POS byte encoding, and text preview."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="candidate1@test.lu",
            email="candidate1@test.lu",
            first_name="Max",
            last_name="Mustermann",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            gender="M",
            date_of_birth=timezone.now().date() - timedelta(days=365 * 28),
        )
        self.event = MeetupEvent.objects.create(
            title="Speed Dating 25-35 @ Urban Bar",
            date_time=timezone.now() + timedelta(hours=2),
            registration_deadline=timezone.now() + timedelta(hours=1),
            duration_minutes=120,
            max_participants=20,
            is_published=True,
        )
        self.registration = EventRegistration.objects.create(
            user=self.user,
            event=self.event,
            status="confirmed",
        )

    def test_directives_with_full_profile(self):
        directives = build_checkin_ticket_directives(
            registration=self.registration,
            event=self.event,
            table_number=4,
            seat_label="A",
        )
        self.assertTrue(len(directives) > 10)
        # Check text representation
        plain_text = preview_checkin_ticket_text(
            registration=self.registration,
            event=self.event,
            table_number=4,
            seat_label="A",
        )
        self.assertIn("CRUSH.LU", plain_text)
        self.assertIn("SPEED DATING", plain_text)
        self.assertIn("MAX", plain_text)
        self.assertIn("TABLE 4", plain_text)
        self.assertIn("DATING RECEIPT", plain_text)
        self.assertIn("CRUSH COACH", plain_text)
        self.assertIn("MYSTERY RADAR", plain_text)

    def test_directives_fallback_without_registration(self):
        plain_text = preview_checkin_ticket_text(
            registration=None,
            event=None,
            table_number=None,
        )
        self.assertIn("CRUSH.LU", plain_text)
        self.assertIn("GUEST", plain_text)
        self.assertIn("WELCOME", plain_text)

    def test_bytes_encoding_and_cp858(self):
        payload_bytes = build_checkin_ticket_bytes(
            registration=self.registration,
            event=self.event,
            table_number=3,
        )
        self.assertTrue(payload_bytes.startswith(INIT + CODEPAGE_CP858))
        self.assertTrue(len(payload_bytes) > 100)

    def test_base64_serialization(self):
        b64 = build_checkin_ticket_base64(
            registration=self.registration,
            event=self.event,
            table_number=3,
        )
        decoded = base64.b64decode(b64)
        self.assertTrue(decoded.startswith(INIT + CODEPAGE_CP858))

    def test_event_lobby_qr_url(self):
        plain_text = preview_checkin_ticket_text(
            registration=self.registration,
            event=self.event,
            table_number=3,
        )
        expected_url = f"https://crush.lu/events/{self.event.id}/lobby/"
        self.assertIn(expected_url, plain_text)

    def test_privacy_aware_attendee_fallback(self):
        anon_user = User.objects.create_user(
            username="secret_email@test.lu",
            email="secret_email@test.lu",
            first_name="",
        )
        anon_reg = EventRegistration.objects.create(
            user=anon_user,
            event=self.event,
            status="confirmed",
        )
        # Unauthenticated / scanner scan must NOT leak the email address
        plain_anon = preview_checkin_ticket_text(
            registration=anon_reg,
            event=self.event,
            table_number=1,
            coach_authenticated=False,
        )
        self.assertNotIn("secret_email@test.lu", plain_anon)
        self.assertIn("ATTENDEE", plain_anon)

        # Coach authenticated call uses username prefix
        plain_coach = preview_checkin_ticket_text(
            registration=anon_reg,
            event=self.event,
            table_number=1,
            coach_authenticated=True,
        )
        self.assertIn("SECRET_EMAIL", plain_coach)

    def test_timezone_conversion_for_event_date(self):
        import zoneinfo
        from datetime import datetime
        lux_tz = zoneinfo.ZoneInfo("Europe/Luxembourg")
        # Fixed datetime in winter (UTC+1)
        fixed_dt = datetime(2026, 12, 1, 18, 30, tzinfo=zoneinfo.ZoneInfo("UTC"))
        event = MeetupEvent.objects.create(
            title="Winter Speed Dating",
            date_time=fixed_dt,
            registration_deadline=fixed_dt - timedelta(hours=2),
            duration_minutes=120,
            max_participants=20,
            is_published=True,
        )
        plain_text = preview_checkin_ticket_text(
            registration=self.registration,
            event=event,
            table_number=1,
        )
    def test_mystery_radar_with_attendee_clues_and_checkboxes(self):
        from crush_lu.models import Interest

        interest_books, _ = Interest.objects.get_or_create(
            slug="reading", defaults={"label": "Reading", "category": "arts"}
        )
        interest_gaming, _ = Interest.objects.get_or_create(
            slug="video-games", defaults={"label": "Video games", "category": "games"}
        )

        self.profile.interests_new.add(interest_books, interest_gaming)

        other_user = User.objects.create_user(
            username="candidate2@test.lu",
            email="candidate2@test.lu",
            first_name="Pos",
        )
        other_profile = CrushProfile.objects.create(
            user=other_user,
            gender="F",
            date_of_birth=timezone.now().date() - timedelta(days=365 * 35),
            event_vibe="at_the_bar",
        )
        other_profile.interests_new.add(interest_books)

        other_reg = EventRegistration.objects.create(
            user=other_user,
            event=self.event,
            status="confirmed",
        )

        plain_text = preview_checkin_ticket_text(
            registration=self.registration,
            event=self.event,
            table_number=2,
        )
        self.assertIn("MYSTERY RADAR", plain_text)
        self.assertIn("[   ]", plain_text)
        self.assertIn(f"(#{other_reg.id})", plain_text)


class TestCheckinPrintingAPI(TestCase):
    """Integration test for check-in endpoints returning print payloads."""

    def setUp(self):
        self.coach_user = User.objects.create_user(
            username="coach@crush.lu",
            email="coach@crush.lu",
            first_name="Coach",
            password="secretpassword",
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            is_active=True,
        )
        self.attendee_user = User.objects.create_user(
            username="alex@test.lu",
            email="alex@test.lu",
            first_name="Alex",
        )
        self.profile = CrushProfile.objects.create(
            user=self.attendee_user,
            gender="F",
            date_of_birth=timezone.now().date() - timedelta(days=365 * 26),
        )
        self.event = MeetupEvent.objects.create(
            title="Speed Dating 25-35",
            date_time=timezone.now() + timedelta(hours=1),
            registration_deadline=timezone.now() + timedelta(minutes=30),
            duration_minutes=120,
            max_participants=20,
            is_published=True,
        )
        self.registration = EventRegistration.objects.create(
            user=self.attendee_user,
            event=self.event,
            status="confirmed",
        )
        signer = Signer()
        self.token = signer.sign(f"{self.registration.id}:{self.event.id}")

    def test_checkin_api_returns_print_payload(self):
        self.client.force_login(self.coach_user)
        url = f"/api/events/checkin/{self.registration.id}/{self.token}/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("print_payload_base64", data)
        self.assertTrue(len(data["print_payload_base64"]) > 50)

    def test_rescan_already_checked_in_returns_print_payload(self):
        self.registration.status = "attended"
        self.registration.checked_in_at = timezone.now()
        self.registration.save()

        self.client.force_login(self.coach_user)
        url = f"/api/events/checkin/{self.registration.id}/{self.token}/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data.get("already_checked_in"))
        self.assertIn("print_payload_base64", data)
        self.assertTrue(len(data["print_payload_base64"]) > 50)

    def test_promote_from_waitlist_returns_print_payload(self):
        self.registration.status = "waitlist"
        self.registration.save()

        self.client.force_login(self.coach_user)
        url = f"/api/events/{self.event.id}/promote/{self.registration.id}/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(data.get("promoted"))
        self.assertIn("print_payload_base64", data)
        self.assertTrue(len(data["print_payload_base64"]) > 50)

    def test_reprint_ticket_api(self):
        # Anonymous fails (redirects to login)
        reprint_url = f"/api/events/{self.event.id}/print-ticket/{self.registration.id}/"
        anon_resp = self.client.get(reprint_url)
        self.assertEqual(anon_resp.status_code, 302)

        # Authenticated coach succeeds
        self.client.force_login(self.coach_user)
        resp = self.client.get(reprint_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("registration_id"), self.registration.id)
        self.assertIn("print_payload_base64", data)
        self.assertTrue(len(data["print_payload_base64"]) > 50)

    def test_test_ticket_api(self):
        test_ticket_url = f"/api/events/{self.event.id}/test-ticket/"
        self.client.force_login(self.coach_user)
        resp = self.client.get(test_ticket_url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("event_id"), self.event.id)
        self.assertIn("print_payload_base64", data)
        self.assertTrue(len(data["print_payload_base64"]) > 50)
