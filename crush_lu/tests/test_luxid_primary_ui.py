"""
LuxID is the promoted, primary path — UI ordering guarantees.

  - Front door (/login/, /signup/): the LuxID button renders BEFORE the other
    social providers, regardless of the order allauth returns them in, and
    carries the "Recommended" badge.
  - Step 5 "Get verified" (partials/_verification_options.html): the LuxID
    card is the first/primary option, ahead of "Come to an event".
"""

from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings

from allauth.socialaccount.models import SocialApp

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class _SiteMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class FrontDoorProviderOrderTests(_SiteMixin, TestCase):
    """LuxID must lead the provider list on the unified auth page."""

    def setUp(self):
        site = Site.objects.get(id=1)
        # Create google first so the natural queryset order would put LuxID
        # AFTER it — the template must still float LuxID to the top.
        google = SocialApp.objects.create(
            provider="google", name="Google", client_id="g", secret="g"
        )
        google.sites.add(site)
        luxid = SocialApp.objects.create(
            provider="luxid", name="LuxID", client_id="l", secret="l"
        )
        luxid.sites.add(site)
        self.client = Client()

    def _assert_luxid_first(self, html):
        self.assertIn('data-provider="LuxID"', html)
        self.assertIn('data-provider="Google"', html)
        self.assertLess(
            html.index('data-provider="LuxID"'),
            html.index('data-provider="Google"'),
            "LuxID button should render before Google",
        )
        self.assertIn("Recommended", html)

    def test_login_page_renders_luxid_first(self):
        # follow=True clears the LocaleMiddleware /login/ -> /en/login/ hop.
        resp = self.client.get("/login/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_luxid_first(resp.content.decode())

    def test_signup_page_renders_luxid_first(self):
        resp = self.client.get("/signup/", follow=True)
        self.assertEqual(resp.status_code, 200)
        self._assert_luxid_first(resp.content.decode())


@override_settings(**CRUSH_LU_URL_SETTINGS)
class VerificationOptionsOrderTests(_SiteMixin, TestCase):
    """Step 5 partial lists LuxID as the recommended, first option."""

    def test_luxid_card_precedes_event_card(self):
        html = render_to_string(
            "crush_lu/partials/_verification_options.html",
            {"luxid_connect_url": "/accounts/luxid/login/?process=connect"},
        )
        self.assertIn("Verify with LuxID", html)
        self.assertIn("Come to an event", html)
        self.assertLess(
            html.index("Verify with LuxID"),
            html.index("Come to an event"),
            "LuxID card should be the first verification option",
        )
        # The LuxID card carries the Recommended badge.
        self.assertLess(html.index("Recommended"), html.index("Come to an event"))

    def test_luxid_connect_button_present_when_url_supplied(self):
        html = render_to_string(
            "crush_lu/partials/_verification_options.html",
            {"luxid_connect_url": "/accounts/luxid/login/?process=connect"},
        )
        self.assertIn("/accounts/luxid/login/?process=connect", html)
        self.assertIn("Connect LuxID", html)
