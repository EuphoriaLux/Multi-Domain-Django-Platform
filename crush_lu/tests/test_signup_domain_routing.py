"""
Regression tests for /accounts/signup/ routing across domains.

Context: allauth's SignupView renders the app-directories-resolved
"account/signup.html" template. Because `crush_lu` is listed before
`entreprinder` in INSTALLED_APPS (for other, legitimate account/ template
overrides), a naive crush_lu/templates/account/signup.html would shadow
entreprinder's real signup form on every domain, not just crush.lu.
"""
from django.test import TestCase, Client
from django.contrib.sites.models import Site


class SignupDomainRoutingTest(TestCase):
    def test_entreprinder_gets_its_own_signup_form(self):
        """entreprinder.lu must render its own form, not crush's redirect stub."""
        Site.objects.update_or_create(domain='entreprinder.lu', defaults={'name': 'Entreprinder'})
        response = Client(HTTP_HOST='entreprinder.lu').get('/accounts/signup/')
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('id_company', body)
        self.assertNotIn('meta http-equiv="refresh"', body)

    def test_crush_signup_redirects_to_consent_capturing_view(self):
        """crush.lu's /accounts/signup/ must redirect to the canonical, consent-capturing /signup/."""
        Site.objects.update_or_create(domain='crush.lu', defaults={'name': 'Crush.lu'})
        response = Client(HTTP_HOST='crush.lu').get('/accounts/signup/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith('/signup/'))
