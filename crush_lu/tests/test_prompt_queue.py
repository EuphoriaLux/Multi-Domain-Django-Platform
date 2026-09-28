"""UX review Wave 2 · WP8: one prompt at a time, above the tab bar.

* 8-01 — the install banner, cookie sheet, push prompt and flash messages
  are coordinated by ``Alpine.store("prompts")`` (cookie > messages >
  install > push); bottom-anchored prompts clear the mobile tab bar
  (``.prompt-above-nav``) and the install banner is a fixed overlay.
* 8-02 — no install banner (nor push prompt) inside the native app shells;
  the install button has an accessible name, the dismiss button is 44px and
  the banner title is no longer an ``<h6>``.
* 8-09 — the push settings cards give up "Checking…" after 3 s with a
  Try Again state, and native shells say notifications are managed in the app.

The runtime behaviour is covered by ``test_prompt_queue_playwright.py``.
"""

import re
from pathlib import Path

from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase

from crush_lu.tests.test_profile_edit_connect_card import _make_member

REPO_ROOT = Path(__file__).resolve().parents[2]
NATIVE = {"HTTP_X_CRUSH_CLIENT": "ios-app"}
INSTALL_BUTTON_RE = re.compile(r'<button id="pwa-install-button"[^>]*>')
DISMISS_BUTTON_RE = re.compile(r'<button id="pwa-dismiss-button"[^>]*>')


def _install_banner(html):
    start = html.index('id="pwa-install-banner"')
    return html[start : html.index('id="pwa-install-success"', start)]


class InstallBannerMarkupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def _get(self, path="/en/", **extra):
        response = self.client.get(path, **extra)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_install_button_has_accessible_name_and_44px_dismiss(self):
        html = self._get()
        button = INSTALL_BUTTON_RE.search(html).group(0)
        self.assertIn('aria-label="Install Crush.lu App"', button)
        dismiss = DISMISS_BUTTON_RE.search(html).group(0)
        self.assertIn("w-11", dismiss)
        self.assertIn("h-11", dismiss)

    def test_banner_title_is_not_a_heading(self):
        html = self._get()
        banner = html[html.index('x-data="pwaInstallBanner"') :]
        banner = banner[: banner.index('id="pwa-install-success"')]
        self.assertNotIn("<h6", banner)
        self.assertIn(">Install Crush.lu App</p>", banner)

    def test_banner_is_a_fixed_overlay_queued_by_the_store(self):
        html = self._get()
        tag = html[html.rindex("<div", 0, html.index('id="pwa-install-banner"')) :]
        tag = tag[: tag.index(">")]
        self.assertIn('x-show="visible"', tag)
        self.assertIn("fixed", tag.split())
        self.assertIn("prompt-above-nav", tag)

    def test_ios_guide_is_cloaked_until_alpine_boots(self):
        html = self._get()
        start = html.index('x-show="showGuide"')
        tag = html[html.rindex("<div", 0, start) : html.index(">", start)]
        self.assertIn("x-cloak", tag.split())

    def test_fr_install_label_has_no_emoji(self):
        html = self._get("/fr/")
        button = INSTALL_BUTTON_RE.search(html).group(0)
        self.assertIn('aria-label="Installer l\'application Crush.lu"', button)
        self.assertNotIn("📲", _install_banner(html))

    def test_native_shell_gets_no_install_banner(self):
        html = self._get(**NATIVE)
        self.assertNotIn('id="pwa-install-banner"', html)
        self.assertNotIn('x-data="pwaInstallBanner"', html)
        self.assertRegex(html, r"<html [^>]*data-native-app")

    def test_browser_keeps_the_install_banner(self):
        html = self._get()
        self.assertIn('id="pwa-install-banner"', html)
        self.assertNotRegex(html, r"<html [^>]*data-native-app")


class MemberPromptTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_member("prompts@example.com")
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def _get(self, path, **extra):
        response = self.client.get(path, **extra)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_push_prompt_sits_above_the_nav_and_is_queued(self):
        html = self._get("/en/account/settings/")
        start = html.index('x-data="pushActivationPrompt"')
        tag = html[start : html.index(">", start)]
        self.assertIn('x-show="visible"', tag)
        self.assertIn("prompt-above-nav", tag)
        self.assertIn("Alpine.store('prompts').isActive('push')", html)

    def test_native_shell_gets_no_push_prompt(self):
        html = self._get("/en/account/settings/", **NATIVE)
        self.assertNotIn('x-data="pushActivationPrompt"', html)

    def test_cookie_sheet_clears_the_tab_bar_on_crush(self):
        html = self._get("/en/account/settings/")
        start = html.index('id="cookie-consent-banner"')
        tag = html[html.rindex("<div", 0, start) : html.index(">", start)]
        self.assertIn("prompt-above-nav", tag)
        self.assertIn("cookie-banner-toggle", html)

    def test_account_settings_push_card_has_timeout_state(self):
        html = self._get("/en/account/settings/")
        self.assertEqual(html.count('x-if="showCheckTimedOut"'), 1)
        self.assertIn("We couldn't check notification support", html)
        self.assertIn('@click="retryStatusCheck"', html)

    def test_edit_profile_push_card_has_timeout_state(self):
        html = self._get("/en/profile/edit/?section=account&sub=notifications")
        self.assertEqual(html.count('x-if="showCheckTimedOut"'), 1)

    def test_native_shell_says_notifications_are_managed_in_the_app(self):
        for path in (
            "/en/account/settings/",
            "/en/profile/edit/?section=account&sub=notifications",
        ):
            # No subTest: pytest without pytest-subtests drops its failures.
            html = self._get(path, **NATIVE)
            self.assertIn("Notifications are managed in the app", html, path)
            self.assertNotIn('x-data="pushPreferences"', html, path)
            self.assertNotIn("Checking notification support", html, path)

    def test_native_notice_translated(self):
        html = self._get("/de/account/settings/", **NATIVE)
        self.assertIn("Benachrichtigungen werden in der App verwaltet", html)
        html = self._get("/fr/account/settings/", **NATIVE)
        self.assertIn("Les notifications sont gérées dans l'application", html)


class OtherSitesCookieSheetTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_other_sites_keep_the_plain_bottom_sheet(self):
        response = Client(HTTP_HOST="vinsdelux.com").get("/", follow=True)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        start = html.index('id="cookie-consent-banner"')
        tag = html[html.rindex("<div", 0, start) : html.index(">", start)]
        self.assertNotIn("prompt-above-nav", tag)


class PromptQueueSourceTests(SimpleTestCase):
    def test_store_orders_cookie_messages_install_push(self):
        js = (REPO_ROOT / "crush_lu/static/crush_lu/js/alpine/core.js").read_text()
        self.assertIn('Alpine.store("prompts"', js)
        self.assertIn('["cookie", "messages", "install", "push"]', js)
        self.assertIn('Alpine.data("flashMessage"', js)
        self.assertIn("PUSH_CHECK_TIMEOUT_MS = 3000", js)

    def test_flash_messages_register_with_the_store(self):
        base = (REPO_ROOT / "crush_lu/templates/crush_lu/base.html").read_text()
        self.assertIn('x-data="flashMessage"', base)

    def test_built_css_lifts_prompts_above_the_tab_bar(self):
        css = (REPO_ROOT / "crush_lu/static/crush_lu/css/tailwind.css").read_text()
        self.assertRegex(
            css,
            r"\.bottom-nav\)\s*\.prompt-above-nav\s*\{\s*bottom:\s*calc\("
            r"var\(--bottom-nav-height\)\s*\+\s*env\(safe-area-inset-bottom",
        )
