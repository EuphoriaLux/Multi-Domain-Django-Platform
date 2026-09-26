"""Marketing privacy/safety claims must match the shipped product.

UX Wave 2 finding 1-02: How It Works, About and the footer promised photo
blurring, event-only connections, "100% verified profiles" and coach review of
every profile. The shipped product shows a clear photo to Crush Connect matches
after opt-in (``photo_share_consent``) and verifies instantly via LuxID or in
person at an event, with no coach review.

Spec: ai-memory-hub/specs/2026-08-24-crush-connect-repo-product-doc.md
"""

from django.core.cache import cache
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
]

FOOTER_CLAIM = (
    "Every member is verified before meeting anyone: instantly with LuxID or "
    "in person at an event."
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
        self.assertIn("Verified before you meet anyone", html)
        self.assertIn(
            "Your photo is only shown to the few verified members matched with "
            "you, and only after you opt in",
            html,
        )
        self.assertIn("No public search or profile browsing", html)
        self.assertIn("Contact details are only shared when you both agree", html)
        self.assertIn(FOOTER_CLAIM, html)

    def test_about_privacy_claims_match_product(self):
        html = self._get("/en/about/")
        self.assert_no_stale_claims(html, "/en/about/")
        self.assertIn(
            "No public browsing: your photo is only shown to the verified "
            "members matched with you, after you opt in.",
            html,
        )
        self.assertIn(
            "Every member verified before they meet anyone — real people only", html
        )
        self.assertIn("Every member is verified before meeting anyone.", html)
        self.assertIn("dating platform with verified profiles and real events", html)
        self.assertIn(FOOTER_CLAIM, html)

    def test_rewritten_claims_are_translated(self):
        de = self._get("/de/how-it-works/")
        self.assertIn("Verifiziert, bevor du jemanden triffst", de)
        self.assertIn("Kontaktdaten werden nur geteilt, wenn ihr beide zustimmt", de)
        self.assertIn("sofort mit LuxID oder persönlich", de)
        self.assertNotIn("unscharf", de)

        fr = self._get("/fr/about/")
        self.assertIn("Aucune navigation publique", fr)
        self.assertIn("Chaque membre est vérifié avant toute rencontre", fr)
        self.assertNotIn("floutez", fr)
        self.assertNotIn("examinés par nos Crush", fr)
