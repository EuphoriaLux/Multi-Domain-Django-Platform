"""Tests for the event email lifecycle admin API endpoints.

Covers Bearer auth, the method guard, and that a valid POST delegates to the
right management command (mirrors the wrapper pattern tested in
``test_api_admin_metrics``).

These three endpoints exist because ``send_event_reminders``,
``send_event_recaps`` and ``send_event_feedback_requests`` previously had no
scheduler of any kind — no Function timer, no cron, no endpoint.

Run with: pytest crush_lu/tests/test_api_admin_events.py -v
"""

from unittest import mock

from django.test import Client, TestCase, override_settings

API_KEY = "test-admin-api-key"
CRUSH_URLS = {"ROOT_URLCONF": "azureproject.urls_crush", "ADMIN_API_KEY": API_KEY}

REMINDERS_URL = "/api/admin/event-reminders/"
RECAPS_URL = "/api/admin/event-recaps/"
FEEDBACK_URL = "/api/admin/event-feedback/"


class _EndpointContract:
    """Shared contract: auth, method guard, and the delegated command name."""

    url = None
    command = None

    def setUp(self):
        self.client = Client(HTTP_HOST="crush.lu")

    def test_missing_bearer_unauthorized(self):
        self.assertEqual(self.client.post(self.url).status_code, 401)

    def test_wrong_bearer_unauthorized(self):
        resp = self.client.post(self.url, HTTP_AUTHORIZATION="Bearer not-the-key")
        self.assertEqual(resp.status_code, 401)

    def test_get_method_not_allowed(self):
        resp = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {API_KEY}")
        self.assertEqual(resp.status_code, 405)

    def test_valid_post_runs_command(self):
        with mock.patch("crush_lu.api_admin_events.call_command") as mock_call:
            resp = self.client.post(self.url, HTTP_AUTHORIZATION=f"Bearer {API_KEY}")
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["status"], "ok")
        mock_call.assert_called_once()
        self.assertEqual(mock_call.call_args[0][0], self.command)

    def test_command_error_is_not_echoed_to_caller(self):
        from django.core.management import CommandError

        with mock.patch(
            "crush_lu.api_admin_events.call_command",
            side_effect=CommandError("/secret/internal/path exploded"),
        ):
            resp = self.client.post(self.url, HTTP_AUTHORIZATION=f"Bearer {API_KEY}")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json(), {"error": "command_error"})
        self.assertNotIn("secret", resp.content.decode())


@override_settings(**CRUSH_URLS)
class EventRemindersEndpointTests(_EndpointContract, TestCase):
    url = REMINDERS_URL
    command = "send_event_reminders"


@override_settings(**CRUSH_URLS)
class EventRecapsEndpointTests(_EndpointContract, TestCase):
    url = RECAPS_URL
    command = "send_event_recaps"


@override_settings(**CRUSH_URLS)
class EventFeedbackEndpointTests(_EndpointContract, TestCase):
    url = FEEDBACK_URL
    command = "send_event_feedback_requests"


@override_settings(**CRUSH_URLS)
class EndpointsAreLanguageNeutralTests(TestCase):
    """The Function App hardcodes these paths, so they must resolve without a
    language prefix and must NOT be reachable under one."""

    def setUp(self):
        self.client = Client(HTTP_HOST="crush.lu")

    def test_paths_resolve_unprefixed(self):
        from django.urls import resolve

        for url, name in (
            (REMINDERS_URL, "api_admin_event_reminders"),
            (RECAPS_URL, "api_admin_event_recaps"),
            (FEEDBACK_URL, "api_admin_event_feedback"),
        ):
            with self.subTest(url=url):
                self.assertEqual(resolve(url).url_name, name)

    def test_language_prefixed_path_is_not_the_endpoint(self):
        # A /fr/ prefixed call must not reach the sweep; if it 202s, the route
        # drifted inside i18n_patterns and the Function's hardcoded path is
        # no longer the contract.
        with mock.patch("crush_lu.api_admin_events.call_command") as mock_call:
            resp = self.client.post(
                "/fr" + RECAPS_URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}"
            )
        self.assertNotEqual(resp.status_code, 202)
        mock_call.assert_not_called()
