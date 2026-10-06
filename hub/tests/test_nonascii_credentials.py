"""Non-ASCII credentials must be rejected, not crash (SEC-API-07).

``secrets.compare_digest(str, str)`` raises ``TypeError`` for non-ASCII text.
These cover the hub bearer authenticator (the first default DRF authenticator,
so it sees every /hub/ request) and the WhatsApp webhook (X-Hub-Signature-256
header and the hub.verify_token query parameter).
"""

import hashlib
import hmac

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db

API_HOST = "api.crush.lu"
KEY = "hub-admin-key-for-tests"
INBOX_PATH = "/hub/whatsapp/inbox"
WEBHOOK_PATH = "/api/webhooks/whatsapp/"
APP_SECRET = "app-secret-for-tests"
VERIFY_TOKEN = "verify-token-for-tests"


@pytest.fixture(autouse=True)
def _clear_cache():
    # DRF throttles and ratelimits share the cache across tests.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def staff_user():
    return get_user_model().objects.create_superuser(
        username="hub-admin", email="hub-admin@example.com", password="x"
    )


@override_settings(ADMIN_API_KEY=KEY)
def test_hub_non_ascii_bearer_is_401_not_500():
    resp = Client(HTTP_HOST=API_HOST).get(INBOX_PATH, HTTP_AUTHORIZATION="Bearer é")
    assert resp.status_code == 401


@override_settings(ADMIN_API_KEY=KEY)
def test_hub_authenticator_returns_none_for_non_ascii():
    from django.test import RequestFactory
    from rest_framework.request import Request

    from hub.authentication import AdminApiKeyAuthentication

    request = Request(RequestFactory().get("/x", HTTP_AUTHORIZATION="Bearer é"))
    assert AdminApiKeyAuthentication().authenticate(request) is None


@override_settings(ADMIN_API_KEY=KEY)
def test_hub_correct_bearer_still_authenticates(staff_user):
    resp = Client(HTTP_HOST=API_HOST).get(
        INBOX_PATH, HTTP_AUTHORIZATION=f"Bearer {KEY}"
    )
    assert resp.status_code == 200


@override_settings(META_WHATSAPP_APP_SECRET=APP_SECRET)
def test_webhook_non_ascii_signature_is_403_not_500():
    resp = Client(HTTP_HOST=API_HOST).post(
        WEBHOOK_PATH,
        data="{}",
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256="sha256=é",
    )
    assert resp.status_code == 403


@override_settings(META_WHATSAPP_APP_SECRET=APP_SECRET)
def test_webhook_correct_signature_still_accepted():
    body = b'{"entry": []}'
    sig = (
        "sha256="
        + hmac.new(APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    )
    resp = Client(HTTP_HOST=API_HOST).post(
        WEBHOOK_PATH,
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=sig,
    )
    assert resp.status_code == 200


@override_settings(META_WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN)
def test_webhook_non_ascii_verify_token_is_403_not_500():
    resp = Client(HTTP_HOST=API_HOST).get(
        WEBHOOK_PATH,
        {
            "hub.mode": "subscribe",
            "hub.verify_token": "é",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 403


@override_settings(META_WHATSAPP_VERIFY_TOKEN=VERIFY_TOKEN)
def test_webhook_correct_verify_token_still_works():
    resp = Client(HTTP_HOST=API_HOST).get(
        WEBHOOK_PATH,
        {
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 200
    assert resp.content == b"abc"
