"""Marketing privacy/safety claims must match the shipped product.

UX Wave 2 finding 1-02: How It Works, About and the footer promised photo
blurring, event-only connections, "100% verified profiles" and coach review of
every profile. The shipped product serves photos (unblurred) to any logged-in
verified member, never publicly; Crush Connect only surfaces members who opted
in (``photo_share_consent``). The media endpoint does not check matches or
shared events, so the copy must not claim it does (Codex #1045). It verifies instantly via LuxID or in person at an event, with no coach review.
Some event audiences admit unverified profiles, so the copy must not promise
that everyone is verified before meeting anyone.

Spec: ai-memory-hub/specs/2026-08-24-crush-connect-repo-product-doc.md
"""

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
]

PHOTO_CLAIM = (
    "Only verified members can see your photos, never the public or search "
    "engines. Crush Connect asks before showing your photo to your matches."
)

FOOTER_CLAIM = (
    "Your privacy matters. Members are verified instantly with LuxID or in "
    "person at an event."
)


class MarketingClaimsTests(TestCase):
    def setUp(self):
        cache.clear()

    def _get(self, path):
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
        self.assertIn(
            "No public browsing: only verified members can see your photos, and "
            "Crush Connect asks before showing your photo to your matches.",
            html,
        )
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
        self.assertIn("Nur verifizierte Mitglieder können deine Fotos sehen", de)
        self.assertIn("Crush Connect fragt dich, bevor dein Foto", de)
        self.assertNotIn("Teilnehmenden deiner Veranstaltungen", de)
        self.assertIn("Kontaktdaten werden nur geteilt, wenn ihr beide zustimmt", de)
        self.assertIn("Mitglieder werden sofort mit LuxID oder persönlich", de)
        self.assertNotIn("unscharf", de)

        fr = self._get("/fr/about/")
        self.assertIn(
            "Aucune navigation publique : seuls les membres vérifiés peuvent voir",
            fr,
        )
        self.assertIn("vous demande votre accord avant de montrer votre photo", fr)
        self.assertNotIn("seuls vos matchs Connect", fr)
        self.assertIn("Crush Connect ne propose que des membres vérifiés", fr)
        self.assertIn("Les membres sont vérifiés instantanément avec LuxID", fr)
        self.assertNotIn("floutez", fr)
        self.assertNotIn("examinés par nos Crush", fr)
