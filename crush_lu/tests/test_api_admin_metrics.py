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
            REMINDERS_URL,
            HTTP_AUTHORIZATION="Bearer not-the-key",
        )
        self.assertEqual(resp.status_code, 401)

    def test_get_method_not_allowed(self):
        resp = self.client.get(
            REMINDERS_URL,
            HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 405)

    def test_valid_post_runs_command(self):
        with mock.patch("crush_lu.api_admin_metrics.call_command") as mock_call:
            resp = self.client.post(
                REMINDERS_URL,
                HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
            )
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "ok")
        # Profile reminders first, then verification reminders in the time left.
        self.assertEqual(
            [c.args[0] for c in mock_call.call_args_list],
            ["send_profile_reminders", "send_verification_reminders"],
        )
        self.assertLessEqual(mock_call.call_args_list[1].kwargs["time_budget"], 55)

    def test_verification_reminders_skipped_without_time_left(self):
        clock = [0.0]

        def slow_profile_sweep(name, **kwargs):
            clock[0] += 40.0  # the profile reminders used 40 of the 55 seconds

        with mock.patch(
            "crush_lu.api_admin_metrics.call_command", side_effect=slow_profile_sweep
        ) as mock_call, mock.patch(
            "crush_lu.api_admin_metrics.time.monotonic", side_effect=lambda: clock[0]
        ):
            resp = self.client.post(
                REMINDERS_URL,
                HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
            )
        self.assertEqual(resp.status_code, 202)
        mock_call.assert_called_once()

    def test_verification_reminder_failure_does_not_fail_sweep(self):
        def fake(name, **kwargs):
            if name == "send_verification_reminders":
                raise RuntimeError("boom")

        with mock.patch("crush_lu.api_admin_metrics.call_command", side_effect=fake):
            resp = self.client.post(
                REMINDERS_URL,
                HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
            )
        self.assertEqual(resp.status_code, 202)


@override_settings(**CRUSH_URLS)
class GdprRetentionEndpointTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="crush.lu")

    def test_missing_bearer_unauthorized(self):
        resp = self.client.post(GDPR_URL)
        self.assertEqual(resp.status_code, 401)

    def test_wrong_bearer_unauthorized(self):
        resp = self.client.post(
            GDPR_URL,
            HTTP_AUTHORIZATION="Bearer not-the-key",
        )
        self.assertEqual(resp.status_code, 401)

    def test_get_method_not_allowed(self):
        resp = self.client.get(
            GDPR_URL,
            HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
        )
        self.assertEqual(resp.status_code, 405)

    def test_valid_post_applies_retention(self):
        with mock.patch("crush_lu.api_admin_metrics.call_command") as mock_call:
            resp = self.client.post(
                GDPR_URL,
                HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
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
                GDPR_URL,
                HTTP_AUTHORIZATION=f"Bearer {API_KEY}",
            )
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["error"], "command_error")
