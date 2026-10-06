"""Non-ASCII credentials must be rejected, not crash (SEC-API-07).

``secrets.compare_digest(str, str)`` raises ``TypeError`` for non-ASCII text,
and WSGI decodes header bytes as latin-1, so ``Authorization: Bearer \\xe9``
used to turn every ``/api/admin/*`` request into an unauthenticated 500.
"""

from unittest import mock

from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings

from crush_lu.api_admin_auth import authenticate_admin_request

KEY = "admin-key-for-tests"
PATH = "/api/admin/weekly-kpis/"


@override_settings(ADMIN_API_KEY=KEY)
class AdminAuthNonAsciiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_non_ascii_bearer_is_401_not_500(self):
        response = self.client.post(PATH, HTTP_AUTHORIZATION="Bearer é")
        self.assertEqual(response.status_code, 401)

    def test_wrong_ascii_bearer_is_401(self):
        response = self.client.post(PATH, HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(response.status_code, 401)

    def test_helper_returns_false_for_non_ascii(self):
        request = RequestFactory().post(PATH, HTTP_AUTHORIZATION="Bearer é中")
        self.assertIs(authenticate_admin_request(request), False)

    def test_correct_bearer_still_authenticates(self):
        request = RequestFactory().post(PATH, HTTP_AUTHORIZATION=f"Bearer {KEY}")
        self.assertIs(authenticate_admin_request(request), True)

    def test_correct_bearer_reaches_the_endpoint(self):
        with mock.patch("crush_lu.api_admin_metrics.call_command"):
            response = self.client.post(PATH, HTTP_AUTHORIZATION=f"Bearer {KEY}")
        self.assertNotEqual(response.status_code, 401)
        self.assertLess(response.status_code, 500)
