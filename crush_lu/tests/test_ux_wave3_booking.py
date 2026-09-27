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
from django.contrib.messages import get_messages
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
        # Instead the button itself is disabled until a slot is picked, via
        # a bare-name getter — the CSP-friendly Alpine build can't evaluate
        # a negation expression like `!hasSelection` inside x-bind (Codex
        # review round 1, PR #1070): that silently drops the `disabled`
        # attribute and lets Confirm submit before anything is selected.
        button_tag_start = content.rindex("<button", 0, confirm_idx)
        button_tag_end = content.index(">", confirm_idx)
        button_tag = content[button_tag_start:button_tag_end]
        self.assertIn(':disabled="hasNoSelection"', button_tag)
        self.assertNotIn(':disabled="!', button_tag)

    def test_slot_radios_carry_start_end_as_their_value(self):
        """[Codex review round 1] The hidden start_at/end_at fields are only
        populated by Alpine's x-bind, so a no-JS submit posted them empty
        and confirm_booking always rejected with "Missing slot information".
        The radio's own value must carry the slot's start/end so the server
        can resolve a slot even without JavaScript."""
        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        content = resp.content.decode()
        self.assertRegex(
            content,
            r'<input[^>]*name="slot_choice"[^>]*'
            r'value="\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^"]*\|'
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
        )

    def test_only_one_radiogroup_per_coach_block(self):
        """[Codex review round 1] The disclosure's extra slots used to sit in
        a second `role="radiogroup"`, even though they share `name`
        "slot_choice" with the first ten and are natively one mutually
        exclusive set — telling a screen-reader user they'd entered a new
        group when picking one silently cleared the other grid's selection."""
        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        content = resp.content.decode()
        self.assertEqual(content.count('role="radiogroup"'), 1)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class BookingConfirmNoJsFallbackTests(BookingBase):
    """[Codex review round 1, PR #1070] confirm_booking used to require
    `start_at`/`end_at` in POST, but those hidden fields are only populated
    by Alpine's x-bind — a plain form submit (JS disabled, or Alpine failed
    to load) always posted them empty and every booking attempt bounced
    with "Missing slot information", regardless of which radio was picked.
    """

    def _confirm_url(self):
        return f"/en/book/{self.submission.booking_token}/confirm/"

    def test_confirm_booking_resolves_slot_from_slot_choice_without_js(self):
        """POSTing only coach_id + slot_choice (the "start|end" value the
        radio itself carries — see includes/_booking_slot_option.html) must
        still book the slot, exactly as if Alpine had populated the hidden
        start_at/end_at fields."""
        from crush_lu.models import ScreeningSlot
        from crush_lu.services.slot_generator import bookable_slots

        slot = bookable_slots(self.coach, days=14)[0]
        slot_choice = "{}|{}".format(
            slot["start_at"].isoformat(), slot["end_at"].isoformat()
        )

        resp = self.client.post(
            self._confirm_url(),
            {"coach_id": self.coach.id, "slot_choice": slot_choice},
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(resp.status_code, 302)
        booked = ScreeningSlot.objects.filter(
            submission=self.submission, status="booked"
        ).first()
        self.assertIsNotNone(booked)
        self.assertEqual(booked.start_at, slot["start_at"])
        self.assertEqual(booked.end_at, slot["end_at"])

    def test_confirm_booking_still_errors_with_no_slot_information_at_all(self):
        resp = self.client.post(
            self._confirm_url(),
            {"coach_id": self.coach.id},
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(resp.status_code, 302)
        messages = list(get_messages(resp.wsgi_request))
        self.assertTrue(any("Missing slot information" in str(m) for m in messages))


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

    def test_cancel_button_is_a_real_submit_control_for_no_js_visitors(self):
        """[Codex review round 1] The visible "Cancel booking" control used
        to be `type="button"` while the actual submit button stayed hidden
        behind `x-cloak` — a visitor without JavaScript had no way to cancel
        at all. The visible control must progressively enhance a working
        submit: `type="submit"` so a no-JS click cancels directly, with the
        Alpine click handler only preventing the default once JS is up."""
        self._book()

        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        content = resp.content.decode()
        idx = content.index('x-show="isInitial"')
        tag_start = content.rindex("<button", 0, idx)
        tag_end = content.index(">", idx)
        button_tag = content[tag_start:tag_end]
        self.assertIn('type="submit"', button_tag)
        self.assertIn('@click.prevent="showConfirm"', button_tag)

    def test_cancel_booking_is_wrapped_in_a_confirm_step(self):
        """Cancel used to be a bare submit button. It must now route through
        the makeConfirm-based component, not submit on the first click."""
        self._book()

        resp = self.client.get(self._page_url(), HTTP_HOST="crush.lu")

        content = resp.content.decode()
        self.assertIn('x-data="bookingCancelConfirm"', content)
        self.assertIn("showConfirm", content)


class BookingCalendarLinkTests(BookingBase):
    """Deliberately does NOT override ROOT_URLCONF: HTTP_HOST=crush.lu alone
    must resolve the correct per-domain urlconf via DomainURLRoutingMiddleware,
    the way a real crush.lu request does. A bare `reverse()` for the .ics
    link would resolve against the default ROOT_URLCONF instead (where
    crush_lu is mounted under /crush/) and 404 on a real request while still
    passing here if ROOT_URLCONF were overridden."""

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
