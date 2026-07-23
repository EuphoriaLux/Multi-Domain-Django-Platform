import json
from unittest.mock import patch, MagicMock
import pytest
from django.contrib.auth.models import User
from crush_lu.models import AndroidAppDevice
from crush_lu.android_push import _derive_project_id_from_email
from crush_lu.notification_service import NotificationService, NotificationType


@pytest.mark.parametrize(
    "email,expected",
    [
        # Legitimate Google-managed service-account emails.
        ("fcm-sa@my-project.iam.gserviceaccount.com", "my-project"),
        ("sa@crush-123456.iam.gserviceaccount.com", "crush-123456"),
        # Suffix appears mid-string but the domain does not end with it:
        # must NOT be treated as a Google-managed account (CodeQL #188/#189).
        ("sa@x.iam.gserviceaccount.com.evil.tld", None),
        ("sa@iam.gserviceaccount.com", ""),  # endswith but empty project id -> None
        # Malformed / non-Google inputs.
        ("sa@example.com", None),
        ("not-an-email", None),
        ("", None),
        (None, None),
    ],
)
def test_derive_project_id_from_email(email, expected):
    result = _derive_project_id_from_email(email)
    if expected == "":
        # Empty extracted id is normalised to None.
        assert result is None
    else:
        assert result == expected


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", email="test@example.com", password="password")


@pytest.fixture
def client(user):
    from django.test import Client
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_android_device_api_journey(client, user):
    # 1. Register a new device
    payload = {
        "registrationToken": "fcm-token-123",
        "deviceId": "android-device-1",
        "packageName": "lu.crush.app",
        "appVersion": "1.0.2",
        "appBuild": "3",
        "deviceName": "Google Pixel 8",
        "systemVersion": "Android 14",
    }
    response = client.post(
        "/api/mobile/android/devices/register/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_USER_AGENT="CrushLUAndroid/1.0.2",
    )

    assert response.status_code == 200
    assert AndroidAppDevice.objects.filter(user=user).count() == 1
    device = AndroidAppDevice.objects.get(user=user)
    assert device.registration_token == "fcm-token-123"
    assert device.device_id == "android-device-1"
    assert device.enabled is True

    # 2. List devices
    response = client.get("/api/mobile/android/devices/")
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["success"] is True
    assert len(data["devices"]) == 1
    assert data["devices"][0]["deviceId"] == "android-device-1"

    # 3. Update preferences
    response = client.post(
        "/api/mobile/android/devices/preferences/",
        data=json.dumps(
            {
                "deviceId": "android-device-1",
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

    # 4. Unregister device
    response = client.post(
        "/api/mobile/android/devices/unregister/",
        data=json.dumps({"registrationToken": "fcm-token-123"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    device.refresh_from_db()
    assert device.enabled is False


@pytest.mark.django_db
def test_notification_service_fans_out_to_android_push(user):
    AndroidAppDevice.objects.create(
        user=user,
        registration_token="fcm-token-123",
        device_id="android-device-1",
        enabled=True,
        notify_profile_updates=True,
    )

    with patch("crush_lu.email_helpers.can_send_email", return_value=False), patch(
        "crush_lu.android_push.send_native_android_push_notification",
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


@pytest.mark.django_db
@patch("requests.post")
def test_android_push_success_and_failure(mock_post, user):
    device = AndroidAppDevice.objects.create(
        user=user,
        registration_token="fcm-token-123",
        device_id="android-device-1",
        enabled=True,
        notify_profile_updates=True,
    )

    # Mock get_fcm_credentials to return mock credentials and project ID
    mock_credentials = MagicMock()
    mock_credentials.token = "mock-token"

    with patch("crush_lu.android_push.get_fcm_credentials", return_value=(mock_credentials, "test-project")):
        # Case A: Success (200 OK)
        mock_response_ok = MagicMock()
        mock_response_ok.status_code = 200
        mock_post.return_value = mock_response_ok

        from crush_lu.android_push import send_native_android_push_notification
        res = send_native_android_push_notification(
            user, "Test Title", "Test Body", preference_key="profile_updates"
        )
        assert res["success"] == 1
        assert res["failed"] == 0

        # Case B: Unregistered / Expired Token (404 Not Found)
        mock_response_fail = MagicMock()
        mock_response_fail.status_code = 404
        mock_response_fail.text = "UNREGISTERED"
        mock_post.return_value = mock_response_fail

        res = send_native_android_push_notification(
            user, "Test Title", "Test Body", preference_key="profile_updates"
        )
        assert res["success"] == 0
        assert res["failed"] == 1

        device.refresh_from_db()
        assert device.failure_count == 1
