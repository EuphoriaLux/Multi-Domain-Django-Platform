from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model

from crush_lu.models import IOSNativeAuthCode

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("azureproject.urls_crush"),
]

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="android-user",
        email="android-user@example.com",
        password="test-pass-123",
    )


def test_android_config_returns_play_safe_defaults(client, settings):
    settings.ANDROID_NATIVE_COMMERCE_ENABLED = False
    settings.ANDROID_PLAY_STORE_URL = ""

    response = client.get("/api/mobile/android/config/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["app"]["packageName"] == "lu.crush.app"
    assert payload["features"]["nativeCommerceEnabled"] is False
    assert payload["urls"]["accountDeletion"] == "/en/account/gdpr/?source=android_app"


def test_android_auth_handoff_and_completion_are_one_time(client, user, settings):
    settings.ANDROID_AUTH_REDIRECT_URIS = ["crushlu://auth"]
    client.force_login(user)

    response = client.get(
        "/api/mobile/android/auth/handoff/",
        {"redirect_uri": "crushlu://auth"},
    )

    assert response.status_code == 302
    redirect_uri = urlparse(response.headers["Location"])
    assert redirect_uri.scheme == "crushlu"
    query = parse_qs(redirect_uri.query)
    assert query["code"][0]
    assert query["complete_url"][0].endswith(
        f"/api/mobile/android/auth/complete/{query['code'][0]}/"
    )
    assert IOSNativeAuthCode.objects.count() == 1

    client.logout()
    complete_path = urlparse(query["complete_url"][0]).path
    complete_response = client.get(complete_path)

    assert complete_response.status_code == 302
    assert complete_response.headers["Location"] == "/en/dashboard/?source=android_app"
    assert client.session["_auth_user_id"] == str(user.id)

    replay_response = client.get(complete_path)
    assert replay_response.status_code == 400


def test_android_auth_handoff_accepts_local_flavor_scheme(client, user, settings):
    """The CRUSH_ENV=local flavor calls back on crushlulocal://auth. Local dev
    settings auto-allow that URI (WEBSITE_HOSTNAME unset), and the redirect
    class must emit the scheme — a missing allowed_schemes entry raises
    DisallowedRedirect even when the URI allowlist is extended."""
    from django.conf import settings as live_settings

    # Import-time default: local dev auto-allows the local flavor's URI.
    assert "crushlulocal://auth" in live_settings.ANDROID_AUTH_REDIRECT_URIS

    client.force_login(user)
    response = client.get(
        "/api/mobile/android/auth/handoff/",
        {"redirect_uri": "crushlulocal://auth"},
    )

    assert response.status_code == 302
    redirect_uri = urlparse(response.headers["Location"])
    assert redirect_uri.scheme == "crushlulocal"
    assert parse_qs(redirect_uri.query)["code"][0]


def test_assetlinks_includes_android_app_when_fingerprint_configured(client, settings):
    settings.ANDROID_APP_PACKAGE = "lu.crush.app"
    settings.ANDROID_APP_SHA256_CERT_FINGERPRINTS = [
        "AA:BB:CC:DD:EE:FF",
    ]

    response = client.get("/.well-known/assetlinks.json")

    assert response.status_code == 200
    payload = response.json()
    android_target = payload[1]["target"]
    assert android_target["namespace"] == "android_app"
    assert android_target["package_name"] == "lu.crush.app"
    assert android_target["sha256_cert_fingerprints"] == ["AA:BB:CC:DD:EE:FF"]


def test_android_native_request_suppresses_commerce(rf, settings):
    from crush_lu.context_processors import crush_user_context

    settings.ANDROID_NATIVE_COMMERCE_ENABLED = False
    request = rf.get("/en/dashboard/?source=android_app")
    request.user = type("AnonymousUser", (), {"is_authenticated": False})()

    context = crush_user_context(request)

    assert context["is_android_native_app"] is True
    assert context["suppress_native_commerce"] is True
