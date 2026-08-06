"""
Regression tests for /accounts/signup/ across domains.

Two separate concerns are covered here:

1. Template resolution. allauth's SignupView renders the app-directories-
   resolved "account/signup.html". Because `crush_lu` is listed before
   `entreprinder` in INSTALLED_APPS (for other, legitimate account/ template
   overrides), a naive crush_lu/templates/account/signup.html would shadow
   other domains' signup pages.

2. Who may register at all. `base_patterns` mounts allauth on all nine
   domains, so every platform exposes /accounts/signup/ whether or not signup
   is a feature there. Registration is therefore gated in
   `azureproject.adapters` by an allowlist, not by URL surgery -- removing the
   route would break LOGIN_URL reversing and @login_required on those domains.
   The unmaintained brochure sites were collecting bot registrations, each one
   sending a confirmation email from the tenant and degrading its sending
   reputation.
"""

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, RequestFactory, TestCase, override_settings

from azureproject.adapters import (
    MultiDomainAccountAdapter,
    MultiDomainSocialAccountAdapter,
)


@override_settings(
    ALLOWED_HOSTS=[
        "crush.lu",
        ".crush.lu",
        ".azurewebsites.net",
        "entreprinder.lu",
        "www.entreprinder.lu",
        "vinsdelux.com",
        "tableau.lu",
        "arborist.lu",
        "power-up.lu",
        "powerup.lu",
        "portal.powerup.lu",
        "delegations.lu",
        "localhost",
        "127.0.0.1",
    ]
)
class SignupDomainRoutingTest(TestCase):

    def test_crush_signup_redirects_to_consent_capturing_view(self):
        """crush.lu's /accounts/signup/ must redirect to the canonical, consent-capturing /signup/."""
        Site.objects.update_or_create(domain="crush.lu", defaults={"name": "Crush.lu"})
        response = Client(HTTP_HOST="crush.lu").get("/accounts/signup/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/signup/"))

    def test_entreprinder_signup_form_is_not_rendered(self):
        """entreprinder.lu is unmaintained: its signup form must not be served."""
        Site.objects.update_or_create(
            domain="entreprinder.lu", defaults={"name": "Entreprinder"}
        )
        response = Client(HTTP_HOST="entreprinder.lu").get("/accounts/signup/")
        self.assertNotIn("id_company", response.content.decode())

    def test_entreprinder_signup_post_creates_no_user(self):
        """The property that actually matters: a POST must not register anyone."""
        Site.objects.update_or_create(
            domain="entreprinder.lu", defaults={"name": "Entreprinder"}
        )
        Client(HTTP_HOST="entreprinder.lu").post(
            "/accounts/signup/",
            {
                "email": "bot@example.com",
                "password1": "sT7!qz2Lm9xW",
                "password2": "sT7!qz2Lm9xW",
            },
        )
        self.assertFalse(
            get_user_model().objects.filter(email="bot@example.com").exists()
        )


@override_settings(
    ALLOWED_HOSTS=[
        "crush.lu",
        ".crush.lu",
        ".azurewebsites.net",
        "entreprinder.lu",
        "www.entreprinder.lu",
        "vinsdelux.com",
        "tableau.lu",
        "arborist.lu",
        "power-up.lu",
        "powerup.lu",
        "portal.powerup.lu",
        "delegations.lu",
        "localhost",
        "127.0.0.1",
    ]
)
class SignupAllowlistTest(TestCase):

    """Unit-level coverage of the domain allowlist in azureproject.adapters."""

    def setUp(self):
        self.factory = RequestFactory()
        self.account_adapter = MultiDomainAccountAdapter()
        self.social_adapter = MultiDomainSocialAccountAdapter()

    def _request(self, host):
        return self.factory.get("/accounts/signup/", HTTP_HOST=host)

    def test_form_signup_open_on_crush_and_its_subdomains(self):
        for host in ("crush.lu", "www.crush.lu", "test.crush.lu", "crush-staging.azurewebsites.net"):
            with self.subTest(host=host):
                self.assertTrue(
                    self.account_adapter.is_open_for_signup(self._request(host))
                )


    def test_form_signup_closed_on_every_other_domain(self):
        closed = (
            "entreprinder.lu",
            "www.entreprinder.lu",
            "vinsdelux.com",
            "tableau.lu",
            "arborist.lu",
            "power-up.lu",
            "powerup.lu",
            "portal.powerup.lu",
            "delegations.lu",
        )
        for host in closed:
            with self.subTest(host=host):
                self.assertFalse(
                    self.account_adapter.is_open_for_signup(self._request(host))
                )

    def test_social_signup_stays_open_for_delegations(self):
        """delegations.lu has no signup form but onboards via Microsoft OAuth."""
        self.assertTrue(
            self.social_adapter.is_open_for_signup(
                self._request("delegations.lu"), sociallogin=None
            )
        )

    def test_social_signup_closed_on_unmaintained_domains(self):
        for host in ("entreprinder.lu", "vinsdelux.com", "power-up.lu"):
            with self.subTest(host=host):
                self.assertFalse(
                    self.social_adapter.is_open_for_signup(
                        self._request(host), sociallogin=None
                    )
                )

    def test_signup_refused_when_host_is_missing(self):
        """A request with no resolvable host must not be a way in."""
        self.assertFalse(self.account_adapter.is_open_for_signup(None))
        self.assertFalse(self.social_adapter.is_open_for_signup(None, sociallogin=None))
