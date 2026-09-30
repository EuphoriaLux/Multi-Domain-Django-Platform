"""WP11a css-weight: page-only CSS ships in per-feature stylesheets.

``tailwind.css`` is loaded on every page, so the journey/gift/reward rules and
the public marketing-page rules live in ``journey.css`` / ``marketing.css`` and
are linked only by the pages that use them.
"""

import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from crush_lu.models import JourneyGift

HOST = {"HTTP_HOST": "crush.lu"}
CSS_DIR = Path(settings.BASE_DIR) / "crush_lu" / "static" / "crush_lu" / "css"
JOURNEY_LINK = "crush_lu/css/journey.css"
MARKETING_LINK = "crush_lu/css/marketing.css"


class FeatureStylesheetFilesTests(TestCase):
    def test_built_files_exist_and_split_the_rules(self):
        base = (CSS_DIR / "tailwind.css").read_text(encoding="utf-8")
        journey = (CSS_DIR / "journey.css").read_text(encoding="utf-8")
        marketing = (CSS_DIR / "marketing.css").read_text(encoding="utf-8")
        # Journey-only rules moved out of the global bundle.
        self.assertIn(".journey-container-content", journey)
        self.assertNotIn(".journey-container-content", base)
        self.assertIn(".gift-container", journey)
        self.assertNotIn(".gift-container", base)
        # Marketing-page rules moved out too.
        self.assertIn(".how-it-works-hero", marketing)
        self.assertNotIn(".how-it-works-hero", base)
        # The global bundle keeps genuinely shared component rules.
        self.assertIn(".btn-primary", base)

    def test_feature_sources_reference_the_main_input(self):
        src = Path(settings.BASE_DIR) / "tailwind-src" / "crush_lu" / "features"
        for name in ("journey", "marketing"):
            text = (src / f"{name}.css").read_text(encoding="utf-8")
            self.assertIn('@reference "../tailwind-input.css";', text)


class FeatureStylesheetLinkTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_marketing_pages_link_marketing_css_only(self):
        for path in ("/en/about/", "/en/how-it-works/", "/en/crush-coach/"):
            html = self.client.get(path, **HOST).content.decode()
            self.assertIn(MARKETING_LINK, html, path)
            self.assertNotIn(JOURNEY_LINK, html, path)

    def test_home_links_neither_feature_stylesheet(self):
        html = self.client.get("/en/", **HOST).content.decode()
        self.assertNotIn(MARKETING_LINK, html)
        self.assertNotIn(JOURNEY_LINK, html)

    def test_gift_landing_links_journey_css(self):
        sender = get_user_model().objects.create_user(
            username="css-split-sender", email="s@example.com", password="x"
        )
        gift = JourneyGift.objects.create(
            sender=sender,
            recipient_name="Lea",
            sender_message="Hi",
            date_first_met=datetime.date(2025, 5, 5),
            location_first_met="Luxembourg",
        )
        html = self.client.get(
            f"/en/journey/gift/{gift.gift_code}/", **HOST
        ).content.decode()
        self.assertIn(JOURNEY_LINK, html)
        self.assertNotIn(MARKETING_LINK, html)
