import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from crush_lu.models import IOSAppDevice, IOSNativeAuthCode
from crush_lu.notification_service import NotificationService, NotificationType

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("azureproject.urls_crush"),
]

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="ios-user",
        email="ios-user@example.com",
        password="test-pass-123",
    )


def test_apple_app_site_association_exposes_universal_links(client, settings):
    settings.IOS_APP_TEAM_ID = "C5XDPB2G33"
    settings.IOS_APP_BUNDLE_ID = "lu.crush.app"

    response = client.get("/.well-known/apple-app-site-association")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    payload = response.json()
    details = payload["applinks"]["details"][0]
    assert details["appID"] == "C5XDPB2G33.lu.crush.app"
    assert "NOT /api/*" in details["paths"]
    assert "*" in details["paths"]
    assert payload["webcredentials"]["apps"] == ["C5XDPB2G33.lu.crush.app"]


def test_ios_config_returns_store_safe_defaults(client, settings):
    settings.IOS_APP_STORE_URL = ""
    settings.IOS_NATIVE_COMMERCE_ENABLED = False
    settings.IOS_APNS_KEY_ID = ""
    settings.IOS_APNS_PRIVATE_KEY = ""
    settings.IOS_APNS_PRIVATE_KEY_BASE64 = ""

    response = client.get("/api/mobile/ios/config/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["app"]["bundleId"] == "lu.crush.app"
    assert payload["features"]["nativeCommerceEnabled"] is False
    assert payload["features"]["nativePushEnabled"] is False
    assert payload["urls"]["accountDeletion"] == "/en/account/gdpr/?source=ios_app"


def test_ios_auth_handoff_and_completion_are_one_time(client, user, settings):
    settings.IOS_AUTH_REDIRECT_URIS = ["crushlu://auth"]
    client.force_login(user)

    response = client.get(
        "/api/mobile/ios/auth/handoff/",
        {"redirect_uri": "crushlu://auth"},
    )

    assert response.status_code == 302
    redirect_uri = urlparse(response.headers["Location"])
    assert redirect_uri.scheme == "crushlu"
    query = parse_qs(redirect_uri.query)
    assert query["code"][0]
    assert query["complete_url"][0].endswith(f"/api/mobile/ios/auth/complete/{query['code'][0]}/")
    assert IOSNativeAuthCode.objects.count() == 1

    client.logout()
    complete_path = urlparse(query["complete_url"][0]).path
    complete_response = client.get(complete_path)

    assert complete_response.status_code == 302
    assert complete_response.headers["Location"] == "/en/dashboard/?source=ios_app"
    assert client.session["_auth_user_id"] == str(user.id)

    replay_response = client.get(complete_path)
    assert replay_response.status_code == 400


def test_ios_device_registration_preferences_and_unregister(client, user):
    response = client.post(
        "/api/mobile/ios/devices/register/",
        data=json.dumps({"deviceToken": "token"}),
        content_type="application/json",
    )
    assert response.status_code == 302

    client.force_login(user)
    payload = {
        "deviceToken": "abcdef123456",
        "deviceId": "device-1",
        "environment": "sandbox",
        "bundleId": "lu.crush.app",
        "appVersion": "1.0.0",
        "appBuild": "1",
        "deviceName": "iPhone",
        "systemVersion": "18.0",
    }
    response = client.post(
        "/api/mobile/ios/devices/register/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_USER_AGENT="CrushLUApp/1.0.0",
    )

    assert response.status_code == 200
    device = IOSAppDevice.objects.get(user=user)
    assert device.device_token == "abcdef123456"
    assert device.environment == "sandbox"
    assert device.enabled is True

    response = client.post(
        "/api/mobile/ios/devices/preferences/",
        data=json.dumps(
            {
                "deviceId": "device-1",
                "preferences": {
                    "newMessages": False,
                    "eventReminders": True,
                    "newConnections": False,
                    "profileUpdates": True,
                },
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    device.refresh_from_db()
    assert device.notify_new_messages is False
    assert device.notify_new_connections is False
    assert device.notify_profile_updates is True

    response = client.post(
        "/api/mobile/ios/devices/unregister/",
        data=json.dumps({"deviceToken": "abcdef123456"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    device.refresh_from_db()
    assert device.enabled is False


@pytest.mark.django_db
def test_notification_service_fans_out_to_ios_push(user):
    IOSAppDevice.objects.create(
        user=user,
        device_token="abcdef123456",
        device_id="device-1",
        enabled=True,
        notify_profile_updates=True,
    )

    with patch("crush_lu.email_helpers.can_send_email", return_value=False), patch(
        "crush_lu.ios_push.send_native_push_notification",
        return_value={"success": 1, "failed": 0, "total": 1},
    ) as send_native:
        result = NotificationService.notify(
            user=user,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={},
        )

    assert result.push_attempted is True
    assert result.push_success_count == 1
    send_native.assert_called_once()
    assert send_native.call_args.kwargs["preference_key"] == "profile_updates"
