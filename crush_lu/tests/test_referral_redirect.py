"""
Tests for the /r/<code>/ referral redirect endpoint.

Run with: pytest crush_lu/tests/test_referral_redirect.py -v
"""
from django.test import TestCase, Client


class ReferralRedirectMethodTests(TestCase):
    """
    Regression: /r/<code>/ must be GET-only.

    Production bug 2026-04-11: messenger link-unfurlers (and scanners) were
    sending POST requests to shared referral URLs. The view had no method
    restriction, so Django's CSRF middleware ran validation on the POST, failed,
    and logged ERROR-level CSRF-failure lines for every hit. Enforcing
    @require_GET means POSTs return 405 before CSRF middleware is reached.
    """

    def setUp(self):
        self.client = Client()

    def test_post_returns_405(self):
        response = self.client.post('/r/ANYCODE00/', HTTP_HOST='crush.lu')
        self.assertEqual(response.status_code, 405)

    def test_get_still_works(self):
        # Unknown code still redirects to signup (no referral captured).
        response = self.client.get('/r/UNKNOWNC/', HTTP_HOST='crush.lu')
        self.assertEqual(response.status_code, 302)
        self.assertIn('signup', response['Location'])
