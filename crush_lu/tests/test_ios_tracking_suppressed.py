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
