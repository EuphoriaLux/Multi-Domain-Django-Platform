"""Marketing privacy/safety claims must match the shipped product.

UX Wave 2 finding 1-02: How It Works, About and the footer promised photo
blurring, event-only connections, "100% verified profiles" and coach review of
every profile. The shipped product serves photos (unblurred) only behind login
and never publicly or to crawlers; Crush Connect only surfaces members who
opted in (``photo_share_consent``). The photo endpoints check neither matches
nor shared events (Codex #1045), and /api/quiz/photo/<user_id>/ does not even
require the viewer to be verified, so the copy may promise only "never public,
account needed". Members are verified instantly via LuxID or in person at an
event, with no coach review.
Some event audiences admit unverified profiles, so the copy must not promise
that everyone is verified before meeting anyone.

Spec: ai-memory-hub/specs/2026-08-24-crush-connect-repo-product-doc.md
"""

from unittest import mock

from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase

HOST = "crush.lu"

STALE_CLAIMS = [
    "Blur photos until mutual interest",
    "No public profiles browsing",
    "Event-based connections only",
    "Verified Profiles",
    "All profiles are reviewed by our Crush Coaches",
    "blur your photos",
    "Every profile is personally verified",
    "Every member verified before they join",
    # Not enforced for every event audience (profile_requirement).
    "before meeting anyone",
    "before you meet anyone",
    "before they meet anyone",
    # Codex #1045: views_media.can_view_profile_photo serves any approved
    # member's photo to any other approved member; it never checks a Connect
    # match or a shared event, so the copy must not promise that it does.
    "only shown to the few verified members matched with you",
    "only fellow attendees see it",
    "fellow event attendees see your photo",
    "only your Connect matches",
    # views_quiz.quiz_display_photo is only @login_required: it never checks
    # that the viewer is verified, so this round-1 wording overclaimed too.
    "nly verified members can see your photos",
]

STALE_TRANSLATED_CLAIMS = [
    "Nur verifizierte Mitglieder können deine Fotos sehen",
    "Teilnehmenden deiner Veranstaltungen",
    "seuls les membres vérifiés peuvent voir vos photos",
    "Seuls les membres vérifiés peuvent voir vos photos",
    "seuls vos matchs Connect",
]

PHOTO_CLAIM = (
    "Your photos are never public or shown to search engines — you need a "
    "Crush.lu account to see them, and Crush Connect asks before showing your "
    "photo to your matches."
)

ABOUT_PHOTO_CLAIM = (
    "No public browsing: your photos are never public or shown to search "
    "engines, you need a Crush.lu account to see them, and Crush Connect asks "
    "before showing your photo to your matches."
)

FOOTER_CLAIM = (
    "Your privacy matters. Members are verified instantly with LuxID or in "
    "person at an event."
)


class MarketingClaimsTests(TestCase):
    def setUp(self):
        cache.clear()

    def _get(self, path):
        # The footer only promises LuxID when the LuxID button is offered
        # (WP3 R12), so these claim checks render with LuxID configured.
        with mock.patch(
            "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.list_apps",
            return_value=[mock.Mock()],
        ):
            response = self.client.get(path, HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200, path)
        return response.content.decode()

    def assert_no_stale_claims(self, html, path):
        for claim in STALE_CLAIMS:
            # The About press cards quote RTL Today verbatim ("coach-verified
            # profiles"); a press quote is not our claim, so it is not listed.
            self.assertFalse(claim in html, f"{path} still claims {claim!r}")

    def test_how_it_works_privacy_claims_match_product(self):
        html = self._get("/en/how-it-works/")
        self.assert_no_stale_claims(html, "/en/how-it-works/")
        self.assertNotIn(">100%<", html)
        self.assertIn("Verified with LuxID or in person", html)
        self.assertIn(PHOTO_CLAIM, html)
        self.assertIn("No public search or profile browsing", html)
        self.assertIn("Contact details are only shared when you both agree", html)
        self.assertIn(FOOTER_CLAIM, html)

    def test_about_privacy_claims_match_product(self):
        html = self._get("/en/about/")
        self.assert_no_stale_claims(html, "/en/about/")
        self.assertIn(ABOUT_PHOTO_CLAIM, html)
        self.assertIn(
            "Crush Connect only matches verified members — real people only", html
        )
        self.assertIn(
            "Members are verified instantly with LuxID or in person at an event. "
            "Every event",
            html,
        )
        self.assertIn("dating platform with verified profiles and real events", html)
        self.assertIn(FOOTER_CLAIM, html)

    def test_profile_edit_partial_shares_footer_claim(self):
        html = render_to_string("crush_lu/partials/edit_about_crushlu.html", {})
        self.assertIn(FOOTER_CLAIM, html)
        self.assertNotIn("reviewed by our Crush Coaches", html)

    def test_rewritten_claims_are_translated(self):
        de = self._get("/de/how-it-works/")
        self.assertIn("Verifiziert per LuxID oder persönlich", de)
        self.assertIn(
            "Deine Fotos sind nie öffentlich und werden nie Suchmaschinen "
            "gezeigt – um sie zu sehen, braucht man ein Crush.lu-Konto, und Crush "
            "Connect fragt dich, bevor dein Foto deinen Matches gezeigt wird.",
            de,
        )
        self.assertIn("Kontaktdaten werden nur geteilt, wenn ihr beide zustimmt", de)
        self.assertIn("Mitglieder werden sofort mit LuxID oder persönlich", de)
        self.assertNotIn("unscharf", de)

        fr = self._get("/fr/about/")
        self.assertIn(
            "Aucune navigation publique : vos photos ne sont jamais publiques ni "
            "montrées aux moteurs de recherche, il faut un compte Crush.lu pour "
            "les voir, et Crush Connect vous demande votre accord avant de "
            "montrer votre photo à vos matchs.",
            fr,
        )
        self.assertIn("Crush Connect ne propose que des membres vérifiés", fr)
        self.assertIn("Les membres sont vérifiés instantanément avec LuxID", fr)
        self.assertNotIn("floutez", fr)
        self.assertNotIn("examinés par nos Crush", fr)

    def test_photo_claims_translated_on_both_pages(self):
        pages = {
            "/de/how-it-works/": "Deine Fotos sind nie öffentlich",
            "/de/about/": "Kein öffentliches Durchstöbern: Deine Fotos sind nie "
            "öffentlich und werden nie Suchmaschinen gezeigt, man braucht ein "
            "Crush.lu-Konto, um sie zu sehen, und Crush Connect fragt dich",
            "/fr/how-it-works/": "Vos photos ne sont jamais publiques ni montrées "
            "aux moteurs de recherche — il faut un compte Crush.lu pour les voir, "
            "et Crush Connect vous demande votre accord",
            "/fr/about/": "il faut un compte Crush.lu pour les voir",
        }
        for path, expected in pages.items():
            html = self._get(path)
            self.assertIn(expected, html, path)
            self.assert_no_stale_claims(html, path)
            for claim in STALE_TRANSLATED_CLAIMS:
                self.assertNotIn(claim, html, f"{path} still claims {claim!r}")
