"""The Google Business Profile review ask in the post-event emails.

Google approved API access for the Crush.lu listing on 2026-09-11, but the
highest-value use of it needs no API call at all: asking attendees for a public
review. Three things must hold, and only tests keep them true:

1. **A deployment that is not production asks nobody.** `CRUSH_GOOGLE_REVIEW_URL`
   is empty by default. Staging runs an isolated database, so its attendee rows
   describe events those people never went to — pointing them at the live
   listing would be soliciting reviews of an experience that did not happen.
2. **Only attendees are asked.** The gate is `status == "attended"`, the status
   `views_checkin` writes in the same save as `checked_in_at`. A no-show, a
   waitlisted member or a cancelled registration must never see the link.
3. **Nobody is filtered by sentiment.** Google's Business Profile policy forbids
   selectively soliciting positive reviews. There is no rating threshold, and
   the copy itself stays neutral — the word "honest" in the string is doing
   deliberate work.

Run with: pytest crush_lu/tests/test_google_review_ask.py -v
"""

from datetime import date, datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

User = get_user_model()

REVIEW_URL = "https://search.google.com/local/writereview?placeid=TESTPLACEID"


class _AttendeeFixture(TestCase):
    """One finished event with one attendee, the shape both senders expect."""

    def setUp(self):
        from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent

        yesterday = (timezone.localtime() - timedelta(days=1)).date()
        start_local = timezone.make_aware(
            datetime.combine(yesterday, time(19, 0)),
            timezone.get_current_timezone(),
        )
        self.event = MeetupEvent.objects.create(
            title="Review Ask Test",
            description="Yesterday",
            event_type="mixer",
            date_time=start_local,
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=start_local - timedelta(hours=1),
            is_published=True,
        )
        self.user = User.objects.create_user(
            username="g@example.com",
            email="g@example.com",
            password="testpass123",
            first_name="Gaby",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
        )
        self.reg = EventRegistration.objects.create(
            event=self.event,
            user=self.user,
            status="attended",
            checked_in_at=timezone.now() - timedelta(hours=20),
        )

    def _render(self, sender_name):
        """Send one email through a mocked transport and return its HTML body."""
        from crush_lu import email_helpers

        sender = getattr(email_helpers, sender_name)
        with mock.patch.object(
            email_helpers, "send_domain_email", return_value=1
        ) as send:
            sender(self.reg, request=None)
        self.assertTrue(send.called, f"{sender_name} sent nothing")
        return send.call_args.kwargs["html_message"]


class ReviewAskIsOffByDefaultTests(_AttendeeFixture):
    def test_setting_is_empty_out_of_the_box(self):
        from django.conf import settings

        self.assertEqual(
            getattr(settings, "CRUSH_GOOGLE_REVIEW_URL", ""),
            "",
            "the review ask must be opt-in per deployment, never a default",
        )

    def test_recap_renders_no_review_block_when_unset(self):
        html = self._render("send_event_recap")
        self.assertNotIn("writereview", html)
        self.assertNotIn("Leave a Google review", html)

    def test_feedback_renders_no_review_block_when_unset(self):
        html = self._render("send_event_feedback_request")
        self.assertNotIn("writereview", html)
        self.assertNotIn("Leave a Google review", html)

    @override_settings(CRUSH_GOOGLE_REVIEW_URL="   ")
    def test_whitespace_only_setting_counts_as_unset(self):
        from crush_lu.services.google_business_profile import get_review_url

        self.assertEqual(get_review_url(), "")
        self.assertNotIn("writereview", self._render("send_event_recap"))


@override_settings(CRUSH_GOOGLE_REVIEW_URL=REVIEW_URL)
class ReviewAskReachesAttendeesTests(_AttendeeFixture):
    def test_recap_carries_the_link(self):
        html = self._render("send_event_recap")
        self.assertIn(REVIEW_URL, html)
        self.assertIn("Leave a Google review", html)

    def test_feedback_carries_the_link(self):
        html = self._render("send_event_feedback_request")
        self.assertIn(REVIEW_URL, html)
        self.assertIn("Leave a Google review", html)

    def test_copy_does_not_steer_towards_positive_reviews(self):
        """Selectively soliciting positive reviews violates Google policy.

        A future edit that reintroduces "if you enjoyed it" phrasing is the
        realistic way this breaks, so assert on the neutral wording rather than
        only on the absence of a rating branch.
        """
        html = self._render("send_event_recap")
        self.assertIn("Honest reviews", html)
        for steering in ("if you enjoyed", "If you enjoyed", "if it was good"):
            self.assertNotIn(steering, html)

    def test_the_policy_note_itself_never_reaches_the_reader(self):
        """The note explaining *why* the copy is neutral sits next to the copy.

        Django's ``{# ... #}`` is single-line only — a multi-line one is emitted
        as visible text, which is how an internal beta note once leaked onto the
        Connect catalogue. Both blocks therefore use ``{% comment %}``, and both
        emails are checked here rather than trusting the repo-wide hygiene test
        to keep covering this particular file.
        """
        for sender in ("send_event_recap", "send_event_feedback_request"):
            with self.subTest(sender=sender):
                html = self._render(sender)
                self.assertNotIn("Business Profile policy", html)
                self.assertNotIn("Neutral wording on purpose", html)


@override_settings(CRUSH_GOOGLE_REVIEW_URL=REVIEW_URL)
class ReviewAskSkipsNonAttendeesTests(_AttendeeFixture):
    def test_helper_returns_empty_for_every_non_attended_status(self):
        from crush_lu.email_helpers import google_review_url_for

        for status in ("confirmed", "waitlist", "pending", "cancelled", "no_show"):
            with self.subTest(status=status):
                self.reg.status = status
                self.assertEqual(
                    google_review_url_for(self.reg),
                    "",
                    f"a registration in state {status!r} did not attend",
                )

    def test_helper_returns_the_url_for_an_attendee(self):
        from crush_lu.email_helpers import google_review_url_for

        self.assertEqual(google_review_url_for(self.reg), REVIEW_URL)

    def test_recap_to_a_no_show_has_no_review_block(self):
        """The sweeps filter on status='attended', but a resend from the admin
        or a future caller need not — so the gate lives in the helper and is
        proven here against the rendered email, not against the queryset."""
        from crush_lu.models import EventRegistration

        self.reg.status = "confirmed"
        self.reg.checked_in_at = None
        self.reg.save(update_fields=["status", "checked_in_at"])
        self.reg.refresh_from_db()
        self.assertEqual(
            EventRegistration.objects.get(pk=self.reg.pk).status, "confirmed"
        )

        html = self._render("send_event_recap")
        self.assertNotIn(REVIEW_URL, html)
        self.assertNotIn("Leave a Google review", html)

    def test_feedback_to_a_no_show_has_no_review_block(self):
        self.reg.status = "confirmed"
        self.reg.checked_in_at = None
        self.reg.save(update_fields=["status", "checked_in_at"])

        html = self._render("send_event_feedback_request")
        self.assertNotIn(REVIEW_URL, html)


@override_settings(CRUSH_GOOGLE_REVIEW_URL=REVIEW_URL)
class ReviewAskIsTranslatedTests(_AttendeeFixture):
    """Proves the strings reached the compiled catalogues, not just the .po."""

    def _render_in(self, lang, sender_name="send_event_recap"):
        self.profile.preferred_language = lang
        self.profile.save(update_fields=["preferred_language"])
        return self._render(sender_name)

    def test_french_attendee_gets_french_cta(self):
        html = self._render_in("fr")
        self.assertIn(REVIEW_URL, html)
        self.assertIn("Laisser un avis Google", html)

    def test_german_attendee_gets_german_cta(self):
        html = self._render_in("de")
        self.assertIn(REVIEW_URL, html)
        self.assertIn("Google-Bewertung schreiben", html)

    def test_english_attendee_gets_english_cta(self):
        html = self._render_in("en")
        self.assertIn("Leave a Google review", html)


class ReviewUrlSurvivesASlotSwapTests(TestCase):
    """The production value must be derived, never configured per slot.

    An App Service setting defined on **one slot only is exchanged on swap, not
    kept** — `infra/resources.bicep` states this, and `CRUSH_GOOGLE_REVIEW_URL`
    is not in `slotConfigNames`. So "set it on production only" would hand the
    live URL to staging at the first swap after release and leave production on
    the empty default, silently. `DJANGO_ENV` *is* slot-sticky, which is why
    `production.py` keys off it — the same belt `GOOGLE_INDEXING_DOMAIN` wears.

    Read as source rather than imported: `production.py` cannot be imported
    under the test settings, and the realistic regression is someone
    "simplifying" the expression back to a plain ``os.environ.get``.
    """

    def _production_block(self):
        from pathlib import Path

        import azureproject

        source = (
            Path(azureproject.__file__).resolve().parent / "production.py"
        ).read_text(encoding="utf-8")
        marker = "CRUSH_GOOGLE_REVIEW_URL = ("
        self.assertIn(
            marker,
            source,
            "production.py must assign CRUSH_GOOGLE_REVIEW_URL conditionally",
        )
        start = source.index(marker)
        return source[start : start + 500]

    def test_production_value_is_gated_on_the_slot_sticky_env(self):
        self.assertIn('DJANGO_ENV == "production"', self._production_block())

    def test_settings_default_stays_empty(self):
        """The base settings default is the safety net for every other context —
        local dev, CI, management commands run outside the App Service."""
        from pathlib import Path

        import azureproject

        source = (
            Path(azureproject.__file__).resolve().parent / "settings.py"
        ).read_text(encoding="utf-8")
        self.assertIn('CRUSH_GOOGLE_REVIEW_URL = os.getenv("CRUSH_GOOGLE_REVIEW_URL", "")', source)
