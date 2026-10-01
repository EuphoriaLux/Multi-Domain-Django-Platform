"""App Review 5.1.2: the native iOS shell must load no tracking tags or consent banner."""

from django.template import Context, Template
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase

from crush_lu.mobile_auth import stash_mobile_handoff


def _render(request):
    tpl = Template(
        "{% load analytics %}{% analytics_head %}{% analytics_body %}"
        '{% ga4_event "x" %}{% fb_event "Lead" %}{% appinsights_head %}{% appinsights_event "e" %}'
    )
    return tpl.render(
        Context(
            {
                "request": request,
                "GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-TEST",
                "FACEBOOK_PIXEL_ID": "12345",
                "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=00000000-0000-0000-0000-000000000000",
            }
        )
    )


class IosTrackingSuppressedTests(TestCase):
    def test_ios_app_emits_no_tracking_tags(self):
        request = RequestFactory().get("/", HTTP_X_CRUSH_CLIENT="ios-app")
        self.assertEqual(_render(request).strip(), "")

    def test_ios_user_agent_emits_no_tracking_tags(self):
        request = RequestFactory().get("/", HTTP_USER_AGENT="CrushLUApp/1.0")
        self.assertEqual(_render(request).strip(), "")

    def test_regular_browser_still_gets_tags(self):
        request = RequestFactory().get("/")
        out = _render(request)
        self.assertIn("googletagmanager.com", out)
        self.assertIn("fbevents.js", out)

    def _request_with_handoff(self, platform):
        request = RequestFactory().get("/accounts/login/")
        request.session = SessionStore()
        stash_mobile_handoff(request, platform, "crushlu://auth")
        return request

    def test_ios_auth_sheet_emits_no_tracking_tags(self):
        self.assertEqual(_render(self._request_with_handoff("ios")).strip(), "")

    def test_android_auth_sheet_is_not_treated_as_ios(self):
        self.assertIn(
            "googletagmanager.com", _render(self._request_with_handoff("android"))
        )

    def test_context_flag_covers_native_shell_and_ios_sheet(self):
        from crush_lu.ios_app_utils import is_ios_tracking_suppressed

        native = RequestFactory().get("/", HTTP_X_CRUSH_CLIENT="ios-app")
        self.assertTrue(is_ios_tracking_suppressed(native))
        self.assertTrue(is_ios_tracking_suppressed(self._request_with_handoff("ios")))
        self.assertFalse(
            is_ios_tracking_suppressed(self._request_with_handoff("android"))
        )

    def test_page_omits_consent_ui_in_ios_sheet_but_not_in_browser(self):
        from django.core.cache import cache

        cache.clear()
        url = "/en/login/"
        plain = self.client.get(url, HTTP_HOST="crush.lu")
        self.assertContains(plain, "cookie-consent-banner")
        session = self.client.session
        stash_mobile_handoff(
            type("R", (), {"session": session})(), "ios", "crushlu://auth"
        )
        session.save()
        sheet = self.client.get(url, HTTP_HOST="crush.lu")
        self.assertNotContains(sheet, "cookie-consent-banner")
        self.assertNotContains(sheet, "data-cookie-settings")
        self.assertContains(sheet, "ios-tracking-cleanup.js")
        self.assertNotContains(plain, "ios-tracking-cleanup.js")
