"""Retire the /account/settings/ monolith (UX Wave 4 · WP9b, finding 8-08).

PR 2 of 3 for decision D. The account drill-down
(/profile/edit/?section=account[&sub=...]) is the one settings surface:

* /account/settings/ is a permanent (301) redirect to it for every signed-in
  user; anonymous visitors still hit the login gate.
* No template or view links the old URL name any more (it no longer exists).
* Old #anchors are mapped client-side to the matching sub-section (see the
  Playwright tests in test_ux_wave4_settings_retire_playwright.py).
* An account stuck in ``deletion_in_progress`` without a CrushProfile still
  reaches the Danger Zone and sees the Delete action, so it can finish.
"""

from html.parser import HTMLParser
from pathlib import Path

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from crush_lu.models.profiles import UserDataConsent
from crush_lu.tests.test_ux_wave4_settings_parity import _make_profile, _make_user

HOST = "crush.lu"
REPO_ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "/en/profile/edit/?section=account"
DANGER = ACCOUNT + "&sub=danger"
DELETE_PROFILE = "/en/account/delete-profile/"


class _Hrefs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.append(dict(attrs).get("href") or "")


def _hrefs(response):
    parser = _Hrefs()
    parser.feed(response.content.decode())
    return parser.hrefs


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class LegacySettingsRedirectTests(TestCase):
    def setUp(self):
        cache.clear()

    def _login(self, email, **profile):
        user = _make_user(email)
        _make_profile(user, **profile)
        self.client.force_login(user)
        return user

    def test_old_url_is_a_permanent_redirect_to_the_drill_down(self):
        self._login("approved@example.com")
        for lang in ("en", "de", "fr"):
            response = self.client.get(f"/{lang}/account/settings/", HTTP_HOST=HOST)
            self.assertEqual(response.status_code, 301, lang)
            self.assertEqual(
                response["Location"], f"/{lang}/profile/edit/?section=account", lang
            )

    def test_redirect_is_open_to_every_signed_in_user(self):
        cases = {
            "noprofile@example.com": None,
            "pending@example.com": dict(
                is_approved=False, verification_status="pending"
            ),
            "rejected@example.com": dict(
                is_approved=False, verification_status="rejected"
            ),
        }
        for email, profile in cases.items():
            user = _make_user(email)
            if profile:
                _make_profile(user, **profile)
            self.client.force_login(user)
            response = self.client.get("/en/account/settings/", HTTP_HOST=HOST)
            self.assertEqual(response.status_code, 301, email)
            self.assertEqual(response["Location"], ACCOUNT, email)
            # The target serves them too (WP9a opened section=account).
            landing = self.client.get(ACCOUNT, HTTP_HOST=HOST)
            self.assertEqual(landing.status_code, 200, email)

    def test_anonymous_visitor_still_hits_the_login_gate(self):
        response = self.client.get("/en/account/settings/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
        self.assertIn("next=", response["Location"])

    def test_bare_allauth_account_pages_point_at_the_drill_down(self):
        self._login("allauth@example.com")
        response = self.client.get("/accounts/", HTTP_HOST=HOST)
        self.assertEqual(response["Location"], "/profile/edit/?section=account")
        response = self.client.get("/accounts/email/", HTTP_HOST=HOST)
        self.assertEqual(
            response["Location"], "/profile/edit/?section=account&sub=settings"
        )

    def test_old_url_names_are_gone(self):
        for name in ("crush_lu:account_settings", "crush_lu:update_email_preferences"):
            with self.assertRaises(NoReverseMatch):
                reverse(name, urlconf="azureproject.urls_crush")

    def test_no_source_file_references_the_old_url_name(self):
        offenders = []
        for folder in ("crush_lu", "azureproject", "core"):
            for path in (REPO_ROOT / folder).rglob("*"):
                if path.suffix not in (".py", ".html", ".js", ".txt"):
                    continue
                if "tests" in path.parts or path.name.endswith(".min.js"):
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for needle in (
                    "crush_lu:account_settings'",
                    'crush_lu:account_settings"',
                    "crush_lu:update_email_preferences",
                ):
                    if needle in text:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: {needle}")
        self.assertEqual(offenders, [])
        self.assertFalse(
            (REPO_ROOT / "crush_lu/templates/crush_lu/account_settings.html").exists()
        )

    def test_rendered_pages_link_the_drill_down_not_the_old_page(self):
        self._login("links@example.com")
        for path in (
            "/en/dashboard/",
            ACCOUNT,
            DANGER,
            "/en/account/take-a-break/",
            DELETE_PROFILE,
            "/en/account/set-password/",
        ):
            response = self.client.get(path, HTTP_HOST=HOST, follow=True)
            hrefs = _hrefs(response)
            self.assertFalse(
                [h for h in hrefs if "/account/settings/" in h and "whatsapp" not in h],
                path,
            )
        dashboard = _hrefs(self.client.get("/en/dashboard/", HTTP_HOST=HOST))
        self.assertIn(ACCOUNT, dashboard)
        cancel = _hrefs(self.client.get("/en/account/take-a-break/", HTTP_HOST=HOST))
        self.assertIn(DANGER, cancel)

    def test_post_action_redirects_land_in_the_drill_down(self):
        user = _make_user("noprofile2@example.com")
        self.client.force_login(user)
        response = self.client.get("/en/account/take-a-break/", HTTP_HOST=HOST)
        self.assertEqual(response["Location"], DANGER)
        response = self.client.get("/en/account/set-password/", HTTP_HOST=HOST)
        self.assertEqual(response["Location"], ACCOUNT + "&sub=settings")

    def test_email_footer_links_the_notification_settings(self):
        from crush_lu.email_helpers import get_email_base_urls

        user = _make_user("mail@example.com")
        _make_profile(user, preferred_language="de")
        request = RequestFactory().get("/", HTTP_HOST=HOST)
        urls = get_email_base_urls(user, request)
        self.assertTrue(
            urls["settings_url"].endswith(
                "/de/profile/edit/?section=account&sub=notifications"
            ),
            urls["settings_url"],
        )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class StuckDeletionDrillDownTests(TestCase):
    """deletion_in_progress without a profile keeps the Delete action (WP9)."""

    def setUp(self):
        cache.clear()
        self.user = _make_user("stuck@example.com")
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=False,
            crushlu_banned=True,
            crushlu_ban_reason="deletion_in_progress",
        )
        self.client.force_login(self.user)

    def test_danger_zone_shows_the_delete_action(self):
        response = self.client.get(DANGER, HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertIn(DELETE_PROFILE, _hrefs(response))
        # Pausing needs a profile: still hidden.
        self.assertNotIn("/en/account/take-a-break/", _hrefs(response))

    def test_overview_is_reachable_and_leads_to_the_danger_zone(self):
        response = self.client.get(ACCOUNT, HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertIn(DANGER, _hrefs(response))

    def test_other_routes_stay_banned(self):
        for path in (
            ACCOUNT + "&sub=settings",
            ACCOUNT + "&sub=notifications",
            "/en/profile/edit/",
            "/en/account/settings/",
            "/en/events/",
        ):
            response = self.client.get(path, HTTP_HOST=HOST)
            self.assertEqual(response.status_code, 302, path)
            self.assertIn("/account/banned/", response["Location"], path)

    def test_profileless_account_without_a_stuck_deletion_has_no_delete(self):
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True, crushlu_banned=False, crushlu_ban_reason=""
        )
        response = self.client.get(DANGER, HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(DELETE_PROFILE, _hrefs(response))

    def test_member_with_profile_keeps_delete_and_pause(self):
        other = _make_user("withprofile@example.com")
        _make_profile(other)
        self.client.force_login(other)
        hrefs = _hrefs(self.client.get(DANGER, HTTP_HOST=HOST))
        self.assertIn(DELETE_PROFILE, hrefs)
        self.assertIn("/en/account/take-a-break/", hrefs)
