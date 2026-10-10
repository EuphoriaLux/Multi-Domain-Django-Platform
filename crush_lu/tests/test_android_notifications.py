import json
import threading
import time
from datetime import timedelta
from unittest.mock import patch, MagicMock
import google.auth.exceptions
import pytest
from django.contrib.auth.models import User
from crush_lu import android_push
from crush_lu.models import AndroidAppDevice
from crush_lu.android_push import _derive_project_id_from_email
from crush_lu.notification_service import NotificationService, NotificationType


@pytest.fixture(autouse=True)
def reset_fcm_credentials_cache():
    # The credentials cache lives for the whole process, so without this the
    # test that happened to run first in an xdist worker would decide whether
    # the next one refreshes.
    android_push._fcm_credentials = None
    yield
    android_push._fcm_credentials = None


@pytest.mark.parametrize(
    "email,expected",
    [
        # Legitimate Google-managed service-account emails.
        ("fcm-sa@my-project.iam.gserviceaccount.com", "my-project"),
        ("sa@crush-123456.iam.gserviceaccount.com", "crush-123456"),
        # Suffix appears mid-string but the domain does not end with it:
        # must NOT be treated as a Google-managed account (CodeQL #188/#189).
        ("sa@x.iam.gserviceaccount.com.evil.tld", None),
        # Domain is exactly the suffix -> endswith True, empty id normalised to None.
        ("sa@.iam.gserviceaccount.com", ""),
        # Leading dot missing -> does not end with the suffix -> None (early exit).
        ("sa@iam.gserviceaccount.com", None),
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
    from crush_lu.models.profiles import UserDataConsent

    user = User.objects.create_user(username="testuser", email="test@example.com", password="password")
    # Device list/register/preferences are behind the consent gate (#1217).
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


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


@pytest.fixture(scope="module")
def service_account_private_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def fcm_credentials(service_account_private_key):
    """Real google-auth service-account credentials, so a refresh runs
    google-auth's own token-endpoint code against the patched transport."""
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": "fcm@test-project.iam.gserviceaccount.com",
            "private_key": service_account_private_key,
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        scopes=android_push.FCM_SCOPES,
    )
    with patch(
        "crush_lu.android_push.get_fcm_credentials",
        return_value=(credentials, "test-project"),
    ) as loader:
        yield loader


def _token_response(access_token="fresh-token"):
    response = MagicMock()
    response.status = 200
    response.data = json.dumps(
        {"access_token": access_token, "expires_in": 3600}
    ).encode()
    return response


@pytest.fixture
def token_endpoint():
    """The HTTP transport every google-auth token refresh goes through."""
    transport = MagicMock(return_value=_token_response())
    with patch("google.auth.transport.requests.Request", return_value=transport):
        yield transport


@pytest.fixture
def fcm_send():
    with patch("requests.post", return_value=MagicMock(status_code=200)) as post:
        yield post


@pytest.fixture
def android_device(user):
    return AndroidAppDevice.objects.create(
        user=user,
        registration_token="fcm-token-123",
        device_id="android-device-1",
        enabled=True,
    )


@pytest.mark.django_db
def test_android_token_refresh_gets_bounded_timeout(
    settings, user, android_device, fcm_credentials, token_endpoint, fcm_send
):
    # google-auth's requests transport would otherwise wait up to 120s.
    settings.CRUSH_PUSH_TOKEN_REFRESH_TIMEOUT_SECONDS = 3.0
    settings.CRUSH_PUSH_FANOUT_BUDGET_SECONDS = 3600.0

    res = android_push.send_native_android_push_notification(user, "Title", "Body")

    assert res["success"] == 1
    token_endpoint.assert_called_once()
    call = token_endpoint.call_args
    assert call.kwargs["url"] == "https://oauth2.googleapis.com/token"
    assert 0 < call.kwargs["timeout"] <= 3.0
    headers = fcm_send.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer fresh-token"


@pytest.mark.django_db
def test_android_token_refresh_is_clamped_to_the_fanout_budget(
    settings, user, android_device, fcm_credentials, token_endpoint, fcm_send
):
    settings.CRUSH_PUSH_TOKEN_REFRESH_TIMEOUT_SECONDS = 30.0
    settings.CRUSH_PUSH_FANOUT_BUDGET_SECONDS = 2.0

    android_push.send_native_android_push_notification(user, "Title", "Body")

    assert 0 < token_endpoint.call_args.kwargs["timeout"] <= 2.0


@pytest.mark.django_db
def test_android_valid_cached_token_is_not_refreshed(
    user, android_device, fcm_credentials, token_endpoint, fcm_send
):
    first = android_push.send_native_android_push_notification(user, "One", "Body")
    second = android_push.send_native_android_push_notification(user, "Two", "Body")

    assert first["success"] == second["success"] == 1
    token_endpoint.assert_called_once()
    # Built once and reused, not rebuilt with a blank token on every call.
    fcm_credentials.assert_called_once()
    assert [c.kwargs["headers"]["Authorization"] for c in fcm_send.call_args_list] == [
        "Bearer fresh-token",
        "Bearer fresh-token",
    ]

    # Once the cached token nears expiry, the next send refreshes it.
    credentials = fcm_credentials.return_value[0]
    credentials.expiry -= timedelta(hours=1)
    android_push.send_native_android_push_notification(user, "Three", "Body")
    assert token_endpoint.call_count == 2


@pytest.mark.django_db
def test_android_token_refresh_failure_returns_failure_dict(
    user, android_device, fcm_credentials, token_endpoint, fcm_send
):
    token_endpoint.side_effect = google.auth.exceptions.TransportError(
        "token endpoint unreachable"
    )

    with patch.object(android_push.logger, "error") as log_error:
        res = android_push.send_native_android_push_notification(user, "Title", "Body")

    assert res == {"success": 0, "failed": 1, "total": 1}
    log_error.assert_called_once()
    assert "Error refreshing Google OAuth2 token for FCM" in log_error.call_args.args[0]
    fcm_send.assert_not_called()

    # The failure must not stick: the next notification tries again.
    token_endpoint.side_effect = None
    res = android_push.send_native_android_push_notification(user, "Title", "Body")
    assert res["success"] == 1
    assert token_endpoint.call_count == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,body",
    [
        (503, {"error": "backend_error"}),
        # Retried by google-auth on the body alone, whatever the status.
        (400, {"error": "temporarily_unavailable"}),
    ],
)
def test_android_retryable_token_error_is_not_retried_with_backoff(
    status, body, user, android_device, fcm_credentials, token_endpoint, fcm_send
):
    # google-auth would sleep 1s then 2s between retries in its own loop,
    # where the deadline cannot see the clock: one attempt, then fail.
    error = MagicMock()
    error.status = status
    error.data = json.dumps(body).encode()
    token_endpoint.return_value = error

    with patch("google.auth._exponential_backoff.time.sleep") as backoff_sleep:
        res = android_push.send_native_android_push_notification(user, "Title", "Body")

    assert res == {"success": 0, "failed": 1, "total": 1}
    token_endpoint.assert_called_once()
    backoff_sleep.assert_not_called()
    fcm_send.assert_not_called()


@pytest.mark.django_db
def test_android_token_rejected_by_fcm_is_refreshed_on_the_next_send(
    user, android_device, fcm_credentials, token_endpoint, fcm_send
):
    token_endpoint.side_effect = [
        _token_response("revoked-token"),
        _token_response("new-token"),
    ]
    # Revoked server-side before its recorded expiry: still "valid" locally.
    fcm_send.return_value = MagicMock(
        status_code=401, text='{"error": {"status": "UNAUTHENTICATED"}}'
    )

    res = android_push.send_native_android_push_notification(user, "One", "Body")
    assert res["failed"] == 1

    fcm_send.return_value = MagicMock(status_code=200)
    res = android_push.send_native_android_push_notification(user, "Two", "Body")

    assert res["success"] == 1
    assert token_endpoint.call_count == 2
    headers = fcm_send.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer new-token"
    # The rejection was our token, not the device's.
    android_device.refresh_from_db()
    assert android_device.failure_count == 0


def test_discard_fcm_token_keeps_a_token_another_thread_already_replaced(
    fcm_credentials, token_endpoint
):
    deadline = time.monotonic() + 30
    assert android_push._get_fcm_access_token(deadline)[0] == "fresh-token"
    credentials = fcm_credentials.return_value[0]

    android_push._discard_fcm_token("stale-token", deadline)
    assert credentials.token == "fresh-token"

    android_push._discard_fcm_token("fresh-token", deadline)
    assert credentials.token is None
    assert not credentials.valid


@pytest.mark.django_db
def test_android_push_does_not_wait_past_budget_for_a_concurrent_refresh(
    settings, user, android_device, fcm_credentials, token_endpoint, fcm_send
):
    settings.CRUSH_PUSH_FANOUT_BUDGET_SECONDS = 0.2

    # Another request holds the lock mid-refresh.
    with android_push._fcm_credentials_lock:
        res = android_push.send_native_android_push_notification(user, "Title", "Body")

    assert res == {"success": 0, "failed": 1, "total": 1}
    token_endpoint.assert_not_called()
    fcm_send.assert_not_called()


def test_concurrent_callers_share_one_token_refresh(fcm_credentials, token_endpoint):
    # Slow enough that, without the lock, every thread would see an invalid
    # token and start its own refresh.
    def slow_token_endpoint(*args, **kwargs):
        time.sleep(0.05)
        return _token_response()

    token_endpoint.side_effect = slow_token_endpoint
    deadline = time.monotonic() + 30
    results = []

    def fetch_token():
        results.append(android_push._get_fcm_access_token(deadline))

    threads = [threading.Thread(target=fetch_token) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [("fresh-token", "test-project")] * 8
    token_endpoint.assert_called_once()


def test_deadline_request_refuses_to_start_past_its_deadline():
    with patch("google.auth.transport.requests.Request") as transport_class:
        request = android_push._DeadlineRequest(time.monotonic() - 1)
        with pytest.raises(google.auth.exceptions.TransportError):
            request(method="POST", url="https://oauth2.googleapis.com/token")

    transport_class.return_value.assert_not_called()
