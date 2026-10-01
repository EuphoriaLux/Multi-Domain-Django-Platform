"""UX Wave 5 . WP11b: less JavaScript per page.

* 1-14/8-17: push-notifications.js and pwa-install.js load only where the push
  prompt / install banner render (or on the notification settings), not on
  every page.
* 3-14: create_profile no longer ships Firebase + intl-tel-input + the phone
  verification module: its phone widget moved to /onboarding/phone/, so the
  page only shows a verified number (or a link to that step).
* R10: the Alpine bundles ship source maps; the source/minified switch follows
  settings.DEBUG alone (not INTERNAL_IPS); the djangojs catalogs regenerate
  without picking up minified bundles.
"""

import json
import re
import sys
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration
from crush_lu.tests.test_profile_edit_connect_card import _make_member
from crush_lu.tests.test_ux_wave3_dashboard import _make_event
from crush_lu.tests.test_ux_wave3_profile_wizard import _grant_consent

ALPINE_DIR = (
    Path(settings.BASE_DIR) / "crush_lu" / "static" / "crush_lu" / "js" / "alpine"
)
BUNDLES = ["core", "coach", "quiz", "journey", "connect"]
NATIVE = {"HTTP_X_CRUSH_CLIENT": "ios-app"}
SCRIPT_SRC_RE = re.compile(r'<script[^>]*\bsrc="([^"]+)"')


def _srcs(html):
    return SCRIPT_SRC_RE.findall(html)


def _count(html, name):
    return sum(name in src for src in _srcs(html))


class DeferredPushAndInstallScriptTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def _get(self, path="/en/events/", **extra):
        response = self.client.get(path, **extra)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _booked_member(self, username):
        user = _make_member(username)
        EventRegistration.objects.create(
            user=user, event=_make_event("Weight event"), status="confirmed"
        )
        return user

    def test_anonymous_visitor_gets_neither_script(self):
        html = self._get("/en/")
        self.assertEqual(_count(html, "push-notifications.js"), 0)
        self.assertEqual(_count(html, "pwa-install.js"), 0)
        # Service worker registration and the update handler stay everywhere.
        self.assertEqual(_count(html, "sw-register.js"), 1)
        self.assertEqual(_count(html, "pwa-update.js"), 1)

    def test_approved_member_without_booking_gets_install_only(self):
        self.client.force_login(_make_member("nobooking@example.com"))
        html = self._get()
        self.assertEqual(_count(html, "pwa-install.js"), 1)
        self.assertIn('id="pwa-install-banner"', html)
        self.assertEqual(_count(html, "push-notifications.js"), 0)

    def test_member_with_booking_gets_both_scripts_once(self):
        self.client.force_login(self._booked_member("booked@example.com"))
        html = self._get()
        self.assertEqual(_count(html, "push-notifications.js"), 1)
        self.assertEqual(_count(html, "pwa-install.js"), 1)

    def test_native_shell_gets_neither_script(self):
        self.client.force_login(self._booked_member("native@example.com"))
        html = self._get(**NATIVE)
        self.assertEqual(_count(html, "push-notifications.js"), 0)
        self.assertEqual(_count(html, "pwa-install.js"), 0)

    def test_notification_settings_load_push_script_without_booking(self):
        self.client.force_login(_make_member("settings@example.com"))
        html = self._get("/en/profile/edit/?section=account&sub=notifications")
        self.assertEqual(_count(html, "push-notifications.js"), 1)
        self.assertIn('x-data="pushPreferences"', html)

    def test_other_profile_edit_views_do_not_load_push_script(self):
        # Without a booking the push bundle must load only where
        # pushPreferences exists: not on the section index, photos, privacy
        # or the native-app notification notice.
        self.client.force_login(_make_member("elsewhere@example.com"))
        counts = {
            path: _count(self._get(path, **extra), "push-notifications.js")
            for path, extra in (
                ("/en/profile/edit/", {}),
                ("/en/profile/edit/?section=photos", {}),
                ("/en/profile/edit/?section=privacy", {}),
                ("/en/profile/edit/?section=account&sub=settings", {}),
                ("/en/profile/edit/?section=account&sub=notifications", NATIVE),
            )
        }
        self.assertEqual(set(counts.values()), {0}, counts)

    def test_notification_settings_load_push_script_once_when_eligible(self):
        self.client.force_login(self._booked_member("both@example.com"))
        html = self._get("/en/profile/edit/?section=account&sub=notifications")
        self.assertEqual(_count(html, "push-notifications.js"), 1)


class CreateProfileNoPhoneDependenciesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        user = get_user_model().objects.create_user(
            username="wizard@example.com",
            email="wizard@example.com",
            password="pass-pass-pass",
            first_name="Robin",
        )
        _grant_consent(user)
        self.client.login(username="wizard@example.com", password="pass-pass-pass")
        CrushProfile.objects.create(
            user=user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
            coach_intro_seen_at=timezone.now(),
        )

    def test_no_firebase_or_intl_tel_input_on_create_profile(self):
        response = self.client.get("/en/create-profile/", follow=True)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('name="date_of_birth"', html)
        for needle in ("firebasejs", "intl-tel-input", "phone-verification.js"):
            self.assertNotIn(needle, html)
        self.assertNotIn("new PhoneVerification", html)


class AssetsDevModeTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(DEBUG=True, INTERNAL_IPS=[])
    def test_debug_serves_sources_even_outside_internal_ips(self):
        html = self.client.get("/en/", HTTP_HOST="crush.lu").content.decode()
        self.assertEqual(_count(html, "alpine/core.js?v="), 1)
        self.assertEqual(_count(html, "core.min.js"), 0)

    @override_settings(DEBUG=False, INTERNAL_IPS=["127.0.0.1"])
    def test_minified_without_debug_even_inside_internal_ips(self):
        html = self.client.get("/en/", HTTP_HOST="crush.lu").content.decode()
        self.assertEqual(_count(html, "alpine/core.min.js"), 1)
        self.assertEqual(_count(html, "alpine/core.js?v="), 0)


class SourceMapTests(SimpleTestCase):
    def test_every_bundle_links_a_valid_committed_map(self):
        for bundle in BUNDLES:
            minified = (ALPINE_DIR / f"{bundle}.min.js").read_text(encoding="utf-8")
            self.assertTrue(
                minified.rstrip().endswith(f"//# sourceMappingURL={bundle}.min.js.map"),
                bundle,
            )
            data = json.loads(
                (ALPINE_DIR / f"{bundle}.min.js.map").read_text(encoding="utf-8")
            )
            self.assertEqual(data["version"], 3)
            self.assertTrue(
                any(s.endswith(f"{bundle}.js") for s in data["sources"]), bundle
            )
            self.assertTrue(data.get("sourcesContent"), bundle)

    def test_build_script_and_deploy_workflow_carry_the_maps(self):
        package = json.loads(
            (Path(settings.BASE_DIR) / "package.json").read_text(encoding="utf-8")
        )
        self.assertIn("--sourcemap=linked", package["scripts"]["build:js"])
        workflow = (
            Path(settings.BASE_DIR)
            / ".github"
            / "workflows"
            / "deploy-azure-app-service-optimized.yml"
        ).read_text(encoding="utf-8")
        for bundle in BUNDLES:
            self.assertGreaterEqual(workflow.count(f"{bundle}.min.js.map"), 3, bundle)


class JsMakemessagesTests(SimpleTestCase):
    def test_command_ignores_minified_and_vendor_and_covers_en_de_fr(self):
        sys.path.insert(0, str(Path(settings.BASE_DIR) / "scripts" / "i18n"))
        try:
            import makemessages_js
        finally:
            sys.path.pop(0)
        cmd = makemessages_js.build_command()
        self.assertEqual(cmd[cmd.index("-d") + 1], "djangojs")
        self.assertEqual(
            [cmd[i + 1] for i, a in enumerate(cmd) if a == "-l"], ["en", "de", "fr"]
        )
        ignores = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--ignore"]
        self.assertIn("*.min.js", ignores)
        self.assertIn("vendor/*", ignores)
        # It runs from the app dir, where locale/ lives.
        self.assertEqual(makemessages_js.APP_DIR.name, "crush_lu")
        self.assertTrue((makemessages_js.APP_DIR / "locale").is_dir())
