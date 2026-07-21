"""Tests for the profile-reminders and GDPR-retention admin API endpoints.

Covers Bearer auth, the method guard, and that a valid POST delegates to the
right management command (mirrors the weekly-KPIs / rotate-questions wrapper
pattern).

Run with: pytest crush_lu/tests/test_api_admin_metrics.py -v
"""
from unittest import mock

from django.test import Client, TestCase, override_settings

API_KEY = "test-admin-api-key"
CRUSH_URLS = {"ROOT_URLCONF": "azureproject.urls_crush", "ADMIN_API_KEY": API_KEY}

REMINDERS_URL = "/api/admin/profile-reminders/"
GDPR_URL = "/api/admin/gdpr-retention/"


@override_settings(**CRUSH_URLS)
class ProfileRemindersEndpointTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="crush.lu")

    def test_missing_bearer_unauthorized(self):
        resp = self.client.post(REMINDERS_URL)
        self.assertEqual(resp.status_code, 401)

    def test_wrong_bearer_unauthorized(self):
        resp = self.client.post(
            REMINDERS_URL, HTTP_AUTHORIZATION="Bearer not-the-key",
        )
        self.assertEqual(resp.status_code, 401)

    def test_get_method_not_allowed(self):
        resp = self.client.get(
            REMINDERS_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 405)

    def test_valid_post_runs_command(self):
        with mock.patch(
            "crush_lu.api_admin_metrics.call_command"
        ) as mock_call:
            resp = self.client.post(
                REMINDERS_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
            )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "ok")
        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args[0][0], "send_profile_reminders")


@override_settings(**CRUSH_URLS)
class GdprRetentionEndpointTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="crush.lu")

    def test_missing_bearer_unauthorized(self):
        resp = self.client.post(GDPR_URL)
        self.assertEqual(resp.status_code, 401)

    def test_wrong_bearer_unauthorized(self):
        resp = self.client.post(
            GDPR_URL, HTTP_AUTHORIZATION="Bearer not-the-key",
        )
        self.assertEqual(resp.status_code, 401)

    def test_get_method_not_allowed(self):
        resp = self.client.get(
            GDPR_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 405)

    def test_valid_post_applies_retention(self):
        with mock.patch(
            "crush_lu.api_admin_metrics.call_command"
        ) as mock_call:
            resp = self.client.post(
                GDPR_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
            )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "ok")
        mock_call.assert_called_once()
        args, kwargs = mock_call.call_args
        self.assertEqual(args[0], "gdpr_retention_cleanup")
        # The endpoint must force --apply; the command alone is dry-run.
        self.assertTrue(kwargs["apply"])

    def test_command_error_returns_500(self):
        from django.core.management import CommandError

        with mock.patch(
            "crush_lu.api_admin_metrics.call_command",
            side_effect=CommandError("boom"),
        ):
            resp = self.client.post(
                GDPR_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
            )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["error"], "command_error")
