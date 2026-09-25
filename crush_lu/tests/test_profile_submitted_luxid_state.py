"""
/profile-submitted/ must tell the truth about LuxID (UX review finding 3-01).

  - The green "LuxID connected — verification is processing" banner renders
    only for a member who actually linked LuxID. It used to key on an empty
    ``luxid_connect_url``, which is also empty when LuxID simply isn't
    configured on the site (no SocialApp) or the lookup failed — so members
    who never touched LuxID were told it was connected.
    Only a *pending* profile is "processing": a rejected member who linked
    LuxID renders the same page and must not be told that either.
  - When LuxID is unavailable for the member (not linked AND nothing to
    connect), the options partial collapses the LuxID card to a one-line note
    and "Come to an event" is the single highlighted path, under a section
    heading that still fits one path (no skipped heading level).
  - The partial's behaviour only changes through explicit include flags, so an
    include that passes none keeps the two-card layout.
"""

import re
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings

from allauth.socialaccount.models import SocialAccount, SocialApp

from crush_lu.models import CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}

PROFILE_SUBMITTED_PATH = "/en/profile-submitted/"

BANNER_TEXT = "LuxID connected"
PROCESSING_TEXT = "Your profile verification is processing."
COLLAPSED_HEADING = "How verification works"
UNAVAILABLE_TEXT = "Not available right now — come to an event to get verified instead."

LUXID_CARD = 'data-verification-option="luxid"'
LUXID_COLLAPSED = 'data-verification-option="luxid-unavailable"'
EVENT_CARD = 'data-verification-option="event"'


def _event_card_tag(html):
    """The opening tag of the "Come to an event" card."""
    match = re.search(r"<div " + re.escape(EVENT_CARD) + r'[^>]*class="([^"]*)"', html)
    assert match, "event option card not rendered"
    return match.group(1)


def _headings(html):
    """(level, text) of every h1-h6 on the page, in document order."""
    return [
        (int(level), re.sub(r"<[^>]+>|\s+", " ", text).strip())
        for level, text in re.findall(r"<h([1-6])\b[^>]*>(.*?)</h\1>", html, re.S)
    ]


def _assert_no_skipped_heading_level(testcase, html):
    """Going deeper, the page content's outline (its h2 through the options
    partial's Premium teaser) may only step down one level at a time. The
    shell's own headings (install prompt, cookie dialog) are left out."""
    start = html.index("<h2")
    headings = _headings(html[start : html.index("Discover Premium", start)])
    testcase.assertTrue(headings, "no headings rendered")
    for (prev_level, prev_text), (level, text) in zip(headings, headings[1:]):
        testcase.assertLessEqual(
            level,
            prev_level + 1,
            f"heading outline skips a level: h{prev_level} {prev_text!r} -> "
            f"h{level} {text!r}",
        )


def _options_html(html):
    """The verification-options grid and everything after it."""
    first = html.find("data-verification-option=")
    assert first != -1, "verification options not rendered"
    return html[first:]


class _SiteMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class TestProfileSubmittedLuxidBanner(_SiteMixin, TestCase):
    """The page-level banner + the collapsed LuxID card, through the view."""

    def setUp(self):
        # Every test viewer ends up as the same pk and shares @ratelimit keys.
        cache.clear()
        self.user = User.objects.create_user(
            username="pending-luxid-banner@example.com",
            email="pending-luxid-banner@example.com",
            password="pass123",
            first_name="Marc",
        )
        # No ProfileSubmission: the free (event / LuxID) verification path.
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1992, 4, 10),
            gender="M",
            location="Luxembourg City",
            is_approved=False,
            verification_status="pending",
            is_active=True,
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        self.client = Client()
        self.client.force_login(self.user)

    def _get(self, path=PROFILE_SUBMITTED_PATH):
        return self.client.get(path, HTTP_HOST="crush.lu")

    def _configure_luxid_app(self):
        app = SocialApp.objects.create(
            provider="luxid", name="LuxID", client_id="test", secret="test"
        )
        # Which Site is "current" differs by runner (pytest forces SITE_ID=1);
        # bind every Site so the connect URL resolves either way.
        app.sites.set(Site.objects.all())
        return app

    def _link_luxid(self):
        SocialAccount.objects.create(
            user=self.user, provider="luxid", uid="lux-banner-1"
        )

    # -- LuxID not linked, not configured -------------------------------------

    def test_member_without_luxid_is_not_told_luxid_is_connected(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_luxid_account"])
        self.assertIsNone(response.context["luxid_connect_url"])
        self.assertNotContains(response, BANNER_TEXT)
        self.assertNotContains(response, PROCESSING_TEXT)

    def test_unavailable_luxid_collapses_the_card(self):
        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["luxid_unavailable"])
        html = response.content.decode()
        options = _options_html(html)
        # No promoted LuxID card, no "two ways" framing, no badge.
        self.assertNotIn(LUXID_CARD, html)
        self.assertNotIn("Two ways to get verified", html)
        self.assertNotIn("Recommended", options)
        self.assertNotIn("Connect LuxID", options)
        # A one-line note remains, after the event card.
        self.assertIn(LUXID_COLLAPSED, html)
        self.assertLess(html.index(EVENT_CARD), html.index(LUXID_COLLAPSED))
        self.assertIn(UNAVAILABLE_TEXT, options)

    def test_collapsed_state_keeps_a_section_heading(self):
        """Dropping "Two ways to get verified" must not leave the event card's
        h4 hanging straight under the page h2."""
        html = self._get().content.decode()

        self.assertIn((3, COLLAPSED_HEADING), _headings(html))
        self.assertLess(html.index(COLLAPSED_HEADING), html.index(EVENT_CARD))
        _assert_no_skipped_heading_level(self, html)

    def test_collapsed_heading_frames_the_premium_submission_state(self):
        """On the Premium coach-review states there is no "now get verified"
        hero above the options, so the collapsed card still needs a heading."""
        ProfileSubmission.objects.create(profile=self.profile, status="pending")

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["submission"])
        self.assertTrue(response.context["luxid_unavailable"])
        html = response.content.decode()
        self.assertIn((3, COLLAPSED_HEADING), _headings(html))
        self.assertLess(html.index(COLLAPSED_HEADING), html.index(EVENT_CARD))
        self.assertNotIn("Two ways to get verified", html)
        _assert_no_skipped_heading_level(self, html)

    def test_unavailable_luxid_highlights_the_event_path(self):
        html = self._get().content.decode()

        event_classes = _event_card_tag(html).split()
        self.assertIn("border-2", event_classes)
        self.assertIn("border-crush-purple/40", event_classes)
        self.assertIn("dark:border-purple-500/50", event_classes)
        # Single column: the event card is the only full card.
        self.assertIn('class="grid grid-cols-1 gap-4"', html)

    def test_collapsed_state_renders_translated_in_german(self):
        response = self._get("/de/profile-submitted/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "LuxID verbunden")
        self.assertContains(response, LUXID_COLLAPSED)
        self.assertContains(response, "Verifizierung mit LuxID")
        self.assertContains(response, "So funktioniert die Verifizierung")

    # -- LuxID available, not linked ------------------------------------------

    def test_available_luxid_keeps_the_promoted_card(self):
        self._configure_luxid_app()

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["luxid_unavailable"])
        self.assertTrue(response.context["luxid_connect_url"])
        self.assertNotContains(response, BANNER_TEXT)
        html = response.content.decode()
        self.assertIn(LUXID_CARD, html)
        self.assertNotIn(LUXID_COLLAPSED, html)
        self.assertIn("Two ways to get verified", html)
        self.assertNotIn(COLLAPSED_HEADING, html)
        self.assertIn("Connect LuxID", html)
        self.assertNotIn("border-2", _event_card_tag(html).split())

    # -- LuxID linked, still pending ------------------------------------------

    def test_pending_member_with_luxid_sees_the_banner(self):
        """The edge state the banner exists for: LuxID is linked but the lazy
        fix-up could not verify the member, so the page renders pending."""
        self._link_luxid()

        with patch(
            "crush_lu.signals._execute_luxid_direct_verify",
            side_effect=Exception("verify service down"),
        ):
            with self.assertLogs("crush_lu.views", level="ERROR"):
                response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_luxid_account"])
        self.assertEqual(response.context["profile"].verification_status, "pending")
        self.assertFalse(response.context["luxid_unavailable"])
        self.assertContains(response, PROCESSING_TEXT)
        html = response.content.decode()
        options = _options_html(html)
        # The LuxID card agrees with the banner instead of calling LuxID
        # "not available".
        self.assertIn(LUXID_CARD, html)
        self.assertNotIn(LUXID_COLLAPSED, html)
        self.assertIn(BANNER_TEXT, options)
        self.assertNotIn(UNAVAILABLE_TEXT, options)

    def test_rejected_member_with_luxid_is_not_told_verification_is_processing(
        self,
    ):
        """A rejected profile is not redirected and the lazy fix-up skips it,
        so the page renders — but nothing about it is "processing"."""
        self._link_luxid()
        self.profile.verification_status = "rejected"
        self.profile.save(update_fields=["verification_status"])

        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_luxid_account"])
        self.assertEqual(response.context["profile"].verification_status, "rejected")
        self.assertNotContains(response, PROCESSING_TEXT)

    def test_verified_member_with_luxid_never_sees_the_banner(self):
        """Verified members are sent to the dashboard before anything renders."""
        self._link_luxid()
        self.profile.verification_status = "verified"
        self.profile.is_approved = True
        self.profile.save(update_fields=["verification_status", "is_approved"])

        response = self._get()

        self.assertEqual(response.status_code, 302)
        self.assertIn("dashboard", response["Location"])


@override_settings(**CRUSH_LU_URL_SETTINGS)
class TestVerificationOptionsFlags(_SiteMixin, TestCase):
    """The partial changes only through explicit include flags."""

    PARTIAL = "crush_lu/partials/_verification_options.html"

    def test_no_flags_keeps_the_two_card_layout(self):
        # What any include that passes neither flag gets: unchanged markup.
        html = render_to_string(self.PARTIAL, {})

        self.assertIn(LUXID_CARD, html)
        self.assertNotIn(LUXID_COLLAPSED, html)
        self.assertIn("Two ways to get verified", html)
        self.assertNotIn(COLLAPSED_HEADING, html)
        self.assertIn(UNAVAILABLE_TEXT, html)
        self.assertNotIn(BANNER_TEXT, html)
        self.assertNotIn("border-2", _event_card_tag(html).split())

    def test_collapse_flag_collapses_luxid(self):
        html = render_to_string(self.PARTIAL, {"collapse_luxid": True})

        self.assertNotIn(LUXID_CARD, html)
        self.assertIn(LUXID_COLLAPSED, html)
        self.assertNotIn("Two ways to get verified", html)
        self.assertIn((3, COLLAPSED_HEADING), _headings(html))
        self.assertIn("border-2", _event_card_tag(html).split())

    def test_connected_flag_marks_the_luxid_card_connected(self):
        html = render_to_string(self.PARTIAL, {"luxid_connected": True})

        self.assertIn(LUXID_CARD, html)
        self.assertIn(BANNER_TEXT, html)
        self.assertNotIn(UNAVAILABLE_TEXT, html)
        self.assertNotIn("Connect LuxID", html)

    def test_connect_url_still_wins_over_the_connected_flag(self):
        html = render_to_string(
            self.PARTIAL,
            {
                "luxid_connect_url": "/accounts/luxid/login/?process=connect",
                "luxid_connected": False,
            },
        )

        self.assertIn("/accounts/luxid/login/?process=connect", html)
        self.assertIn("Connect LuxID", html)
