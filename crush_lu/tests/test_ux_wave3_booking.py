"""Tests for UX Wave 3 · WP5 (finding 3-13) — screening-call self-booking.

Covers the new .ics download endpoint / Google Calendar link on the
"You're booked" card, and the switch from one-form-per-slot instant-submit
to a selectable-then-confirm form with a "show more" disclosure for slots
past the first ten. All of these fail against `origin/main` (the old
template has no `download_booking_ics` route, no radio inputs, and slices
slots to exactly the first ten with plain, unlinked "+N more" text).
"""

from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class BookingBase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def setUp(self):
        from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
        from crush_lu.models.profiles import UserDataConsent

        cache.clear()

        user = User.objects.create_user(
            username="member@example.com",
            email="member@example.com",
            password="pass12345",
            first_name="Member",
        )
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
        self.profile = CrushProfile.objects.create(
            user=user,
            show_full_name=False,
            gender="M",
            location="Luxembourg",
            verification_status="pending",
            photo_1="users/1/photos/a.jpg",
        )

        coach_user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="pass12345",
            first_name="Coach",
        )
        self.coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            hybrid_features_enabled=True,
            working_mode="hybrid",
            # A single long daily window is enough to generate well over
            # ten 30-minute slots for "today's" weekday within the 14-day
            # horizon, so the "show more" disclosure has something to show.
            availability_windows=[
                {"day": name, "start": "00:00", "end": "23:30", "label": ""}
                for name in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            ],
        )

        self.submission = ProfileSubmission.objects.create(
            profile=self.profile,
            status="pending",
            coach=self.coach,
            booking_token=uuid4(),
            booking_token_expires_at=timezone.now() + timedelta(days=7),
        )

    def _page_url(self):
        return f"/en/book/{self.submission.booking_token}/"

    def _ics_url(self):
        return f"/en/book/{self.submission.booking_token}/ics/"


@override_settings(**CRUSH_LU_URL_SETTINGS)
class BookingSlotDisplayTests(BookingBase):
    def test_more_than_ten_slots_are_all_rendered_not_just_the_first_ten(self):
        """The old template sliced to `:10` and only printed "+N more" as
        text. All slots must now be in the DOM (the extra ones inside the
        disclosure), addressable by data-start, or a keyboard/AT user can
        never reach a time past the tenth."""
        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertGreater(content.count('data-start="'), 10)

    def test_slots_are_selectable_radio_cards_not_instant_submit_buttons(self):
        """Each slot used to be its own `<button type="submit">` — one
        accidental tap booked a call. Slots must now be radio inputs that
        feed a single sticky confirm button instead."""
        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        content = resp.content.decode()
        self.assertIn('type="radio" name="slot_choice"', content)
        self.assertIn('x-data="bookingSlotPicker"', content)

    def test_show_more_disclosure_present_when_over_ten_slots(self):
        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        content = resp.content.decode()
        self.assertIn("toggleShowAll", content)

    def test_confirm_button_is_always_rendered_for_no_js_visitors(self):
        """[UX Wave 3 · WP5 finding] The sticky Confirm button used to be
        `x-show="hasSelection" x-cloak`, so a visitor with JS disabled (or a
        failed Alpine parse) could pick a radio slot but the submit control
        never appeared at all — there was no way to book. The button must
        now render unconditionally and only be disabled, via Alpine, until a
        slot is selected."""
        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        content = resp.content.decode()
        # The submit button's own markup must not be gated by x-show/x-cloak
        # (a no-JS visitor gets a real, always-present submit control).
        confirm_idx = content.index("btn-crush-solid w-full shadow-lg")
        # Look at the enclosing wrapper (~200 chars back) for the old gate.
        wrapper = content[max(0, confirm_idx - 250) : confirm_idx]
        self.assertNotIn('x-show="hasSelection"', wrapper)
        self.assertNotIn("x-cloak", wrapper)
        # Instead the button itself is disabled until a slot is picked.
        button_tag_start = content.rindex("<button", 0, confirm_idx)
        button_tag_end = content.index(">", confirm_idx)
        button_tag = content[button_tag_start:button_tag_end]
        self.assertIn(':disabled="!hasSelection"', button_tag)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class BookingCancelConfirmTests(BookingBase):
    def _book(self):
        from crush_lu.models import ScreeningSlot

        start_at = timezone.now() + timedelta(days=1)
        return ScreeningSlot.objects.create(
            coach=self.coach,
            submission=self.submission,
            status="booked",
            start_at=start_at,
            end_at=start_at + timedelta(minutes=30),
        )

    def test_cancel_booking_is_wrapped_in_a_confirm_step(self):
        """Cancel used to be a bare submit button. It must now route through
        the makeConfirm-based component, not submit on the first click."""
        self._book()

        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        content = resp.content.decode()
        self.assertIn('x-data="bookingCancelConfirm"', content)
        self.assertIn("showConfirm", content)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class BookingCalendarLinkTests(BookingBase):
    def _book(self):
        from crush_lu.models import ScreeningSlot

        start_at = timezone.now() + timedelta(days=1)
        return ScreeningSlot.objects.create(
            coach=self.coach,
            submission=self.submission,
            status="booked",
            start_at=start_at,
            end_at=start_at + timedelta(minutes=30),
        )

    def test_booked_card_links_to_ics_download_and_google_calendar(self):
        self._book()

        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["ics_download_url"], self._ics_url())
        self.assertIn(
            "https://calendar.google.com/calendar/render?",
            resp.context["google_calendar_url"],
        )
        content = resp.content.decode()
        self.assertIn(self._ics_url(), content)

    def test_ics_download_returns_calendar_file_for_the_booked_slot(self):
        self._book()

        resp = self.client.get(self._ics_url(), HTTP_HOST="crush.lu")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/calendar", resp["Content-Type"])
        self.assertIn(
            'attachment; filename="crush-screening-call.ics"',
            resp["Content-Disposition"],
        )
        body = resp.content.decode()
        self.assertIn("BEGIN:VEVENT", body)

    def test_ics_download_404s_without_an_active_booking(self):
        resp = self.client.get(self._ics_url(), HTTP_HOST="crush.lu")

        self.assertEqual(resp.status_code, 404)

    def test_book_screening_page_omits_calendar_links_without_a_booking(self):
        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        self.assertNotIn("ics_download_url", resp.context)
