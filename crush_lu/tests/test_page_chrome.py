"""Page chrome: drawer preferences/help, drill-down top bar, notch + status bar.

UX review Wave 2 · WP10, findings 8-10, 8-12 and 8-16.

* 8-10 — for members on mobile the navbar (language + theme) and the footer
  (legal links) are hidden, so the drawer gains a *Preferences* group
  (language switcher + Light/Dark/System control) and a *Help & legal* group.
  Logout stays last.
* 8-12 — the drill-down pages set ``mobile_page_title`` (top bar shows Back +
  title), use the shared ``components/page_header.html`` (one h1 scale, h1
  kept for screen readers on mobile), drop their doubled inner gutters, and
  account settings no longer shows a mobile "Back to Dashboard" pill.
* 8-16 — the top bar grows by the notch inset instead of squeezing its
  content, and dark mode gets its own ``theme-color``.

JS behaviour (theme choice, status-bar colour, locked pages) is pinned in
``test_page_chrome_playwright.py``.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase

from crush_lu import context_processors
from crush_lu.models import CrushSiteConfig
from crush_lu.tests.test_profile_edit_connect_card import _make_member

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "crush_lu" / "templates" / "crush_lu"


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(html):
    parser = _TagCollector()
    parser.feed(html)
    return parser.tags


def _drawer(html):
    start = html.index('<div x-data="mobileDrawer">')
    return html[start:]


def _mobile_title(html):
    match = re.search(r'<meta name="mobile-page-title" content="([^"]*)">', html)
    return match.group(1)


def _h1s(html):
    return [a for t, a in _tags(html) if t == "h1"]


class DrawerPreferencesAndHelpTests(TestCase):
    def setUp(self):
        cache.clear()
        context_processors._site_config_cache["config"] = None
        self.user = _make_member("chrome@example.com")
        self.client.force_login(self.user)

    def tearDown(self):
        context_processors._site_config_cache["config"] = None

    def _get(self, path="/en/notifications/"):
        response = self.client.get(path, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_drawer_has_preferences_group(self):
        drawer = _drawer(self._get())
        self.assertIn(">Preferences</span>", drawer)
        # The language switcher, with its own ids (the navbar menu has one too).
        self.assertIn('id="language-drawer"', drawer)
        self.assertIn("lang-select-auto-submit", drawer)
        # Light / Dark / System control on the shared theme component.
        tags = _tags(drawer)
        choice = [a for t, a in tags if a.get("x-data") == "themeChoice"]
        self.assertEqual(len(choice), 1)
        self.assertEqual(
            choice[0]["data-locked-label"], "This experience is always in night mode"
        )
        clicks = [a.get("@click") for t, a in tags if t == "button"]
        for handler in ("chooseLight", "chooseDark", "chooseSystem"):
            self.assertIn(handler, clicks)
        options = [a for t, a in tags if a.get("@click", "").startswith("choose")]
        for option in options:
            self.assertEqual(option["x-bind:aria-disabled"], "isLocked")
            self.assertIn("x-bind:aria-pressed", option)

    def test_language_ids_stay_unique_on_the_page(self):
        html = self._get()
        ids = [a["id"] for t, a in _tags(html) if "id" in a]
        dupes = {i for i in ids if ids.count(i) > 1}
        self.assertFalse(dupes & {"language-mobile", "language-drawer"}, dupes)
        self.assertIn("language-mobile", ids)
        self.assertIn("language-drawer", ids)

    def test_drawer_has_help_and_legal_links_and_logout_last(self):
        drawer = _drawer(self._get())
        self.assertIn(">Help & legal</span>", drawer)
        hrefs = [a.get("href") for t, a in _tags(drawer) if t == "a"]
        for path in (
            "/en/support/",
            "/en/privacy-policy/",
            "/en/terms-of-service/",
            "/en/data-deletion/",
            "/en/changelog/",
        ):
            self.assertIn(path, hrefs)
        self.assertTrue(hrefs[-1].endswith("/logout/"), hrefs[-1])
        # WhatsApp only when enabled (like the floating button).
        self.assertFalse(any(h.startswith("https://wa.me/") for h in hrefs))

    def test_whatsapp_link_when_enabled(self):
        config = CrushSiteConfig.get_config()
        config.whatsapp_enabled = True
        config.whatsapp_number = "352000000"
        config.save()
        context_processors._site_config_cache["config"] = None
        drawer = _drawer(self._get())
        links = [a for t, a in _tags(drawer) if t == "a"]
        wa = [a for a in links if a.get("href") == "https://wa.me/352000000"]
        self.assertEqual(len(wa), 1)
        self.assertEqual(wa[0]["rel"], "noopener noreferrer")
        self.assertIn("WhatsApp support", drawer)

    def test_drawer_groups_are_translated(self):
        drawer = _drawer(self._get("/de/notifications/"))
        self.assertIn("Hilfe & Rechtliches", drawer)
        self.assertIn(">Hell</button>", drawer)
        self.assertIn(">Dunkel</button>", drawer)
        drawer = _drawer(self._get("/fr/notifications/"))
        self.assertIn("Aide et mentions légales", drawer)
        self.assertIn(">Sombre</button>", drawer)


class DrillDownPageChromeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_member("drill@example.com")
        self.client.force_login(self.user)

    def _get(self, path):
        response = self.client.get(path, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200, path)
        return response.content.decode()

    def test_drill_down_pages_show_back_and_title(self):
        for path, title in (
            ("/en/notifications/", "Notifications"),
            ("/en/settings/blocked/", "Blocked members"),
            ("/en/account/gdpr/", "Data Management"),
            ("/en/account/delete-profile/", "Delete Crush.lu Profile"),
            ("/en/data-deletion/", "Data Deletion"),
            ("/en/account/settings/", "Settings"),
        ):
            # No subTest: pytest would report a failing subtest as PASSED.
            html = self._get(path)
            self.assertEqual(_mobile_title(html), title, path)
            h1s = _h1s(html)
            self.assertEqual(len(h1s), 1, path)
            classes = h1s[0]["class"].split()
            # One scale; hidden on mobile where the top bar shows the title.
            self.assertIn("text-3xl", classes, path)
            self.assertIn("max-lg:sr-only", classes, path)

    def test_hidden_header_leaves_no_mobile_gap(self):
        # The wrapper's spacing only matters where its h1 is visible.
        tags = _tags(self._get("/en/account/gdpr/"))
        idx = next(i for i, (t, _a) in enumerate(tags) if t == "h1")
        wrapper = tags[idx - 1][1]["class"].split()
        self.assertEqual(wrapper, ["mb-8", "max-lg:mb-0"])
        # With a subtitle the header stays visible, so it keeps its spacing.
        tags = _tags(self._get("/en/notifications/"))
        idx = next(i for i, (t, _a) in enumerate(tags) if t == "h1")
        self.assertNotIn("max-lg:mb-0", tags[idx - 1][1]["class"])

    def test_german_top_bar_titles(self):
        self.assertEqual(
            _mobile_title(self._get("/de/settings/blocked/")), "Blockierte Mitglieder"
        )
        self.assertEqual(
            _mobile_title(self._get("/de/notifications/")), "Benachrichtigungen"
        )

    def test_guest_keeps_a_visible_h1_on_the_public_page(self):
        self.client.logout()
        html = self._get("/en/data-deletion/")
        classes = _h1s(html)[0]["class"].split()
        self.assertNotIn("max-lg:sr-only", classes)

    def test_pages_use_the_container_gutter(self):
        for name in (
            "moderation/blocked_members.html",
            "gdpr_data_management.html",
            "delete_crushlu_profile_confirm.html",
            "data_deletion.html",
        ):
            source = (TEMPLATES / name).read_text(encoding="utf-8")
            block = source[source.index("{% block content %}") :][:400]
            self.assertNotIn("section-container", block, name)
            self.assertNotRegex(block, r'class="[^"]*\bpx-4\b', name)

    def test_account_settings_back_pill_is_desktop_only(self):
        html = self._get("/en/account/settings/")
        pill = [
            a
            for t, a in _tags(html)
            if t == "div" and "text-center mt-6" in a.get("class", "")
        ]
        self.assertEqual(len(pill), 1)
        self.assertIn("max-lg:hidden", pill[0]["class"].split())


class TopBarAndThemeColorTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_top_bar_grows_by_the_safe_area_inset(self):
        for path in (
            REPO_ROOT / "tailwind-src" / "crush_lu" / "tailwind-input.css",
            REPO_ROOT / "crush_lu" / "static" / "crush_lu" / "css" / "tailwind.css",
        ):
            css = path.read_text(encoding="utf-8")
            rules = re.findall(r"\.top-bar-mobile\s*\{([^}]*)\}", css)
            heights = [
                re.sub(r"\s+", "", m)
                for r in rules
                for m in re.findall(r"(?<![-\w])height:\s*([^;]+)", r)
            ]
            self.assertEqual(
                heights, ["calc(48px+env(safe-area-inset-top,0px))"], path.name
            )

    def test_dark_theme_color_meta(self):
        html = self.client.get("/en/", HTTP_HOST="crush.lu").content.decode()
        metas = [
            a for t, a in _tags(html) if t == "meta" and a.get("name") == "theme-color"
        ]
        self.assertEqual(
            [(m.get("media"), m["content"]) for m in metas],
            [("(prefers-color-scheme: dark)", "#0f172a"), (None, "#9B59B6")],
        )

    def test_theme_manager_manages_the_status_bar_colour(self):
        js = (
            REPO_ROOT / "crush_lu" / "static" / "crush_lu" / "js" / "theme-manager.js"
        ).read_text(encoding="utf-8")
        self.assertIn('meta[name="theme-color"]', js)
        self.assertIn('dark: "#0f172a"', js)
        self.assertIn('light: "#9B59B6"', js)
