"""App Review 5.1.2: the native iOS shell must load no tracking tags or consent banner."""

from django.template import Context, Template
from django.test import RequestFactory, TestCase


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
