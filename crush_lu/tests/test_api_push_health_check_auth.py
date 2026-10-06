"""Auth for POST /api/push/health-check/ (SEC-API-08).

The shared secret used to be compared with ``!=`` (not constant time). It is now
compared with ``secrets.compare_digest`` on UTF-8 bytes, which also keeps a
non-ASCII header from raising. The endpoint stays dark when the env token is
unset and keeps using HEALTH_CHECK_SECRET_TOKEN (not ADMIN_API_KEY).
"""

import os
import secrets
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase

PATH = "/api/push/health-check/"
TOKEN = "health-check-secret-for-tests"


class PushHealthCheckAuthTests(TestCase):
    def setUp(self):
        cache.clear()  # @ratelimit counters live in the cache
        self.client = Client(HTTP_HOST="crush.lu")

    def _post(self, authorization=None):
        extra = {}
        if authorization is not None:
            extra["HTTP_AUTHORIZATION"] = authorization
        with mock.patch("crush_lu.api_push.call_command") as call_command:
            response = self.client.post(PATH, **extra)
        return response, call_command

    def test_correct_token_runs_the_check(self):
        with mock.patch.dict("os.environ", {"HEALTH_CHECK_SECRET_TOKEN": TOKEN}):
            response, call_command = self._post(f"Bearer {TOKEN}")
        self.assertEqual(response.status_code, 200)
        call_command.assert_called_once()
        self.assertEqual(
            call_command.call_args.args[0], "check_push_subscription_health"
        )

    def test_secret_is_compared_in_constant_time_on_bytes(self):
        real = secrets.compare_digest
        with mock.patch.dict(
            "os.environ", {"HEALTH_CHECK_SECRET_TOKEN": TOKEN}
        ), mock.patch("secrets.compare_digest", wraps=real) as compare:
            response, _ = self._post(f"Bearer {TOKEN}")
        self.assertEqual(response.status_code, 200)
        compare.assert_called_once()
        for operand in compare.call_args.args:
            self.assertIsInstance(operand, bytes)

    def test_wrong_token_is_401(self):
        with mock.patch.dict("os.environ", {"HEALTH_CHECK_SECRET_TOKEN": TOKEN}):
            response, call_command = self._post("Bearer wrong")
        self.assertEqual(response.status_code, 401)
        call_command.assert_not_called()

    def test_missing_header_is_401(self):
        with mock.patch.dict("os.environ", {"HEALTH_CHECK_SECRET_TOKEN": TOKEN}):
            response, call_command = self._post()
        self.assertEqual(response.status_code, 401)
        call_command.assert_not_called()

    def test_non_ascii_token_is_401_not_500(self):
        with mock.patch.dict("os.environ", {"HEALTH_CHECK_SECRET_TOKEN": TOKEN}):
            response, call_command = self._post("Bearer é中")
        self.assertEqual(response.status_code, 401)
        call_command.assert_not_called()

    def test_non_ascii_secret_in_env_still_matches(self):
        secret = "sécret"
        with mock.patch.dict("os.environ", {"HEALTH_CHECK_SECRET_TOKEN": secret}):
            response, _ = self._post(f"Bearer {secret}")
        self.assertEqual(response.status_code, 200)

    def test_unset_env_token_is_dark(self):
        env = {k: v for k, v in os.environ.items() if k != "HEALTH_CHECK_SECRET_TOKEN"}
        with mock.patch.dict("os.environ", env, clear=True):
            response, call_command = self._post("Bearer ")
        self.assertEqual(response.status_code, 401)
        call_command.assert_not_called()
