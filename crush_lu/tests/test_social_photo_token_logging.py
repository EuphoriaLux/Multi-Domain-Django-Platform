"""Facebook access tokens must never reach the application log (SEC-10).

The Graph photo requests carry the live user access token in the query string,
and ``requests`` embeds the full URL in HTTPError / ConnectionError text. The
except blocks around those calls log the exception, so the token used to land
in Log Analytics. These tests pin that the logged text is token-free while the
return values stay exactly as before.
"""

from types import SimpleNamespace
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase

from crush_lu import signals, social_photos

TOKEN = "EAAB_SECRET_TOKEN_123"
GRAPH_URL = (
    "https://graph.facebook.com/v24.0/1/picture?width=720&height=720"
    f"&redirect=false&access_token={TOKEN}"
)


def _http_error():
    resp = requests.Response()
    resp.status_code = 400
    resp.reason = "Bad Request"
    resp.url = GRAPH_URL
    return resp  # raise_for_status() embeds resp.url in the HTTPError text


def _connection_error(*args, **kwargs):
    raise requests.ConnectionError(
        f"HTTPSConnectionPool(host='graph.facebook.com', port=443): Max retries "
        f"exceeded with url: /v24.0/1/picture?width=720&access_token={TOKEN} "
        f"(Caused by NewConnectionError('refused'))"
    )


def _fb_account():
    return SimpleNamespace(
        id=987,
        user_id=1,
        provider="facebook",
        extra_data={"id": "1", "picture": {"data": {"url": "https://x/fallback.jpg"}}},
    )


def _fb_token():
    return SimpleNamespace(token=TOKEN, expires_at=None)


class SafeExceptionTextTests(SimpleTestCase):
    def test_http_error_has_class_and_status_but_no_token(self):
        try:
            _http_error().raise_for_status()
        except requests.HTTPError as exc:
            self.assertIn(TOKEN, str(exc))  # premise: the raw text leaks
            text = social_photos._safe_exception_text(exc)
        self.assertNotIn(TOKEN, text)
        self.assertIn("HTTPError", text)
        self.assertIn("400", text)

    def test_connection_error_is_redacted(self):
        try:
            _connection_error()
        except requests.ConnectionError as exc:
            text = social_photos._safe_exception_text(exc)
        self.assertNotIn(TOKEN, text)
        self.assertIn("ConnectionError", text)

    def test_plain_exception(self):
        self.assertEqual(
            social_photos._safe_exception_text(ValueError("boom")), "ValueError"
        )


class FacebookTokenLogLeakTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _assert_clean(self, cm):
        joined = "\n".join(cm.output)
        self.assertNotIn(TOKEN, joined)
        self.assertNotIn("access_token=EAAB", joined)

    # --- signals.get_high_res_facebook_photo_url -------------------------
    def test_signals_http_error(self):
        self._patch(signals.requests, "get", return_value=_http_error())
        with self.assertLogs(signals.logger, level="WARNING") as cm:
            result = signals.get_high_res_facebook_photo_url("1", access_token=TOKEN)
        self.assertIsNone(result)
        self._assert_clean(cm)

    def test_signals_connection_error(self):
        self._patch(signals.requests, "get", side_effect=_connection_error)
        with self.assertLogs(signals.logger, level="WARNING") as cm:
            result = signals.get_high_res_facebook_photo_url("1", access_token=TOKEN)
        self.assertIsNone(result)
        self._assert_clean(cm)

    def test_signals_success_unchanged(self):
        ok = mock.Mock()
        ok.json.return_value = {"data": {"url": "https://cdn/p.jpg"}}
        get = self._patch(signals.requests, "get", return_value=ok)
        result = signals.get_high_res_facebook_photo_url("1", access_token=TOKEN)
        self.assertEqual(result, "https://cdn/p.jpg")
        # The request itself is deliberately unchanged.
        self.assertIn(f"access_token={TOKEN}", get.call_args.args[0])

    # --- social_photos.refresh_social_photo_cache ------------------------
    def test_refresh_http_error(self):
        self._patch(social_photos.requests, "get", return_value=_http_error())
        with self.assertLogs(social_photos.logger, level="WARNING") as cm:
            result = social_photos.refresh_social_photo_cache(
                _fb_account(), token=_fb_token(), force=True
            )
        self.assertFalse(result)
        self._assert_clean(cm)

    def test_refresh_connection_error(self):
        self._patch(social_photos.requests, "get", side_effect=_connection_error)
        with self.assertLogs(social_photos.logger, level="WARNING") as cm:
            result = social_photos.refresh_social_photo_cache(
                _fb_account(), token=_fb_token(), force=True
            )
        self.assertFalse(result)
        self._assert_clean(cm)

    # --- social_photos.get_facebook_photo_url ----------------------------
    def test_get_facebook_photo_url_http_error_falls_back(self):
        self._patch(social_photos, "_get_token_for_account", return_value=_fb_token())
        self._patch(social_photos.requests, "get", return_value=_http_error())
        with self.assertLogs(social_photos.logger, level="WARNING") as cm:
            result = social_photos.get_facebook_photo_url(_fb_account())
        self.assertEqual(result, "https://x/fallback.jpg")
        self._assert_clean(cm)

    def test_get_facebook_photo_url_connection_error_falls_back(self):
        self._patch(social_photos, "_get_token_for_account", return_value=_fb_token())
        self._patch(social_photos.requests, "get", side_effect=_connection_error)
        with self.assertLogs(social_photos.logger, level="WARNING") as cm:
            result = social_photos.get_facebook_photo_url(_fb_account())
        self.assertEqual(result, "https://x/fallback.jpg")
        self._assert_clean(cm)
