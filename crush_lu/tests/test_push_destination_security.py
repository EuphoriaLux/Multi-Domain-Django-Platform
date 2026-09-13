"""Push endpoint input and transport must not become arbitrary outbound HTTP."""

from unittest.mock import patch

import pytest
import requests
from django.contrib.auth.models import User
from django.core.cache import cache

from crush_lu.models import CoachPushSubscription, CrushCoach, PushSubscription
from crush_lu.services.push_endpoints import (
    InvalidPushEndpoint,
    PushSession,
    push_transport,
    validate_push_endpoint,
)

KEYS = {
    "p256dh": "BAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4fICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8",
    "auth": "AAECAwQFBgcICQoLDA0ODw",
}
VALID_ENDPOINT = "https://fcm.googleapis.com/fcm/send/example-token"


@pytest.mark.django_db
@pytest.mark.parametrize("coach_api", [False, True])
def test_subscription_rejects_private_destination(client, coach_api):
    cache.clear()
    user = User.objects.create_user("push-security", password="test-password")
    if coach_api:
        CrushCoach.objects.create(user=user, is_active=True)
    client.force_login(user)
    path = "/api/coach/push/subscribe/" if coach_api else "/api/push/subscribe/"
    response = client.post(
        path,
        {"endpoint": "https://127.0.0.1/private", "keys": KEYS},
        content_type="application/json",
        HTTP_HOST="crush.lu",
    )
    assert response.status_code == 400
    assert not PushSubscription.objects.exists()
    assert not CoachPushSubscription.objects.exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        VALID_ENDPOINT,
        "https://fcm.googleapis.com:443/fcm/send/token",
        "https://updates.push.services.mozilla.com/wpush/v2/token",
        "https://web.push.apple.com/QToken",
        "https://region.web.push.apple.com/QToken",
        "https://wns2-bl2p.notify.windows.com/w/?token=opaque%2Bchannel",
    ],
)
def test_supported_provider_endpoints(endpoint):
    validate_push_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        None,
        42,
        {},
        [],
        "",
        "https://127.0.0.1/push",
        "https://[::1]/push",
        "https://169.254.169.254/metadata",
        "https://10.0.0.1/push",
        "https://localhost/push",
        "https://attacker.example/push",
        "http://fcm.googleapis.com/fcm/send/token",
        "https://fcm.googleapis.com:8080/fcm/send/token",
        "https://fcm.googleapis.com:bad/fcm/send/token",
        "https://user:password@fcm.googleapis.com/fcm/send/token",
        "https://fcm.googleapis.com@attacker.example/push",
        "https://fcm.googleapis.com.attacker.example/push",
        "https://evilpush.apple.com/push",
        "https://evilnotify.windows.com/push",
        "https://web.push.apple.com.attacker.example/push",
        "https://fcm.googleapis.com./push",
        "https://fcm.googleapis.com/push#fragment",
        "https://fcm.googleapis.com/push#",
        " https://fcm.googleapis.com/push",
        "https://fcm.googleapis.com\n/push",
        "https://fcm.googleapis.com/\tpush",
        "https://fcm.googleapis.com\\@attacker.example/push",
        "https://[invalid/push",
    ],
)
def test_invalid_destinations_are_rejected(endpoint):
    with pytest.raises(InvalidPushEndpoint):
        validate_push_endpoint(endpoint)


@pytest.fixture
def subscriber(db):
    cache.clear()
    user = User.objects.create_user("push-guard", password="test-password")
    coach = CrushCoach.objects.create(user=user, is_active=True)
    return user, coach


@pytest.mark.parametrize("coach_api", [False, True])
@pytest.mark.parametrize("operation", ["create", "fingerprint", "refresh"])
def test_safe_subscription_and_rotation(client, subscriber, coach_api, operation):
    user, coach = subscriber
    model = CoachPushSubscription if coach_api else PushSubscription
    owner = {"coach": coach} if coach_api else {"user": user}
    old = None
    if operation != "create":
        # Legacy unsafe rows can recover by rotating to a valid provider URL.
        old = model.objects.create(
            **owner,
            endpoint="https://legacy.invalid/push",
            p256dh_key=KEYS["p256dh"],
            auth_key=KEYS["auth"],
            device_fingerprint="same-device",
        )
    client.force_login(user)
    path = "/api/coach/push/subscribe/" if coach_api else "/api/push/subscribe/"
    payload = {
        "endpoint": VALID_ENDPOINT,
        "keys": KEYS,
        "deviceFingerprint": "same-device",
    }
    if operation == "refresh":
        path = "/api/push/refresh-subscription/"
        payload = {"oldEndpoint": old.endpoint, "subscription": payload}
    response = client.post(
        path, payload, content_type="application/json", HTTP_HOST="crush.lu"
    )
    assert response.status_code == 200
    assert model.objects.count() == 1
    assert model.objects.get().endpoint == VALID_ENDPOINT
    if old:
        assert model.objects.get().pk == old.pk


@pytest.mark.parametrize("coach_api", [False, True])
@pytest.mark.parametrize("operation", ["fingerprint", "refresh", "refresh_existing"])
def test_rotation_cannot_replace_keys_or_destination_with_unsafe_input(
    client, subscriber, coach_api, operation
):
    user, coach = subscriber
    model = CoachPushSubscription if coach_api else PushSubscription
    owner = {"coach": coach} if coach_api else {"user": user}
    unsafe = "https://127.0.0.1/internal"
    original_endpoint = unsafe if operation == "refresh_existing" else VALID_ENDPOINT
    old = model.objects.create(
        **owner,
        endpoint=original_endpoint,
        p256dh_key=KEYS["p256dh"],
        auth_key=KEYS["auth"],
        device_fingerprint="same-device",
        failure_count=2,
    )
    client.force_login(user)
    path = "/api/coach/push/subscribe/" if coach_api else "/api/push/subscribe/"
    payload = {
        "endpoint": unsafe,
        "keys": {"p256dh": "updated", "auth": "updated"},
        "deviceFingerprint": "same-device",
    }
    if operation.startswith("refresh"):
        path = "/api/push/refresh-subscription/"
        payload = {"subscription": payload}
        if operation == "refresh":
            payload["oldEndpoint"] = old.endpoint
    response = client.post(
        path, payload, content_type="application/json", HTTP_HOST="crush.lu"
    )
    assert response.status_code == 400
    old.refresh_from_db()
    assert old.endpoint == original_endpoint
    assert old.p256dh_key == KEYS["p256dh"]
    assert old.auth_key == KEYS["auth"]
    assert old.failure_count == 2


@pytest.mark.parametrize("coach_api", [False, True])
@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("valid", [False, True])
def test_all_senders_validate_stored_rows(subscriber, settings, coach_api, bulk, valid):
    from crush_lu import coach_notifications, push_notifications

    settings.VAPID_PRIVATE_KEY = "test-private"
    settings.VAPID_PUBLIC_KEY = "test-public"
    settings.VAPID_ADMIN_EMAIL = "push-test@example.com"
    user, coach = subscriber
    model = CoachPushSubscription if coach_api else PushSubscription
    owner = {"coach": coach} if coach_api else {"user": user}
    sub = model.objects.create(
        **owner,
        endpoint=VALID_ENDPOINT if valid else "https://127.0.0.1/private",
        p256dh_key=KEYS["p256dh"],
        auth_key=KEYS["auth"],
        enabled=True,
    )
    module = coach_notifications if coach_api else push_notifications
    if coach_api:
        sender = (
            module.send_coach_push_notification
            if bulk
            else module.send_coach_push_to_subscription
        )
    else:
        sender = (
            module.send_push_notification if bulk else module.send_push_to_subscription
        )
    target = (coach if coach_api else user) if bulk else sub
    with patch.object(module, "webpush") as transport:
        result = sender(target, "Test", "Body")
    assert bool(result["success"]) is valid
    assert transport.call_count == int(valid)
    if valid:
        assert isinstance(transport.call_args.kwargs["requests_session"], PushSession)
        assert transport.call_args.kwargs["requests_session"].trust_env is False


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_transport_does_not_follow_provider_redirect(status):
    response = requests.Response()
    response.request = requests.Request("POST", VALID_ENDPOINT).prepare()
    response.status_code = status
    response.headers["Location"] = "https://127.0.0.1/private"
    response._content = b""
    with PushSession() as session, patch(
        "requests.adapters.HTTPAdapter.send", return_value=response
    ) as network:
        result = session.post(
            VALID_ENDPOINT, data=b"encrypted", timeout=1, allow_redirects=True
        )
    assert result.status_code == status
    assert network.call_count == 1
    assert network.call_args.args[0].url == VALID_ENDPOINT
    assert result.history == []


def test_transport_revalidates_before_any_network_access():
    with PushSession() as session, patch(
        "requests.adapters.HTTPAdapter.send"
    ) as network:
        with pytest.raises(InvalidPushEndpoint):
            session.post("https://localhost/private", timeout=1)
    network.assert_not_called()


@pytest.mark.parametrize("status", [201, 307])
def test_real_pywebpush_encrypts_and_rejects_redirects(status):
    """Bypass conftest's pywebpush mock; retain the actual HTTP request path."""
    import base64
    import importlib.metadata
    import importlib.util

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import Vapid

    source = importlib.metadata.distribution("pywebpush").locate_file(
        "pywebpush/__init__.py"
    )
    spec = importlib.util.spec_from_file_location("real_pywebpush", source)
    library = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(library)
    public_key = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    subscription = {
        "endpoint": VALID_ENDPOINT,
        "keys": {
            "p256dh": base64.urlsafe_b64encode(public_key).decode().rstrip("="),
            "auth": KEYS["auth"],
        },
    }
    vapid = Vapid()
    vapid.generate_keys()
    response = requests.Response()
    response.request = requests.Request("POST", VALID_ENDPOINT).prepare()
    response.status_code = status
    response._content = b""
    response.headers["Location"] = "https://127.0.0.1/private"
    with push_transport(VALID_ENDPOINT) as session, patch(
        "requests.adapters.HTTPAdapter.send", return_value=response
    ) as network:
        kwargs = dict(
            subscription_info=subscription,
            data="test notification",
            vapid_private_key=vapid,
            vapid_claims={"sub": "mailto:push-test@example.com"},
            requests_session=session,
            timeout=1,
        )
        if status == 201:
            assert library.webpush(**kwargs).status_code == 201
        else:
            with pytest.raises(library.WebPushException):
                library.webpush(**kwargs)
    assert network.call_count == 1
    assert network.call_args.args[0].url == VALID_ENDPOINT
    assert network.call_args.args[0].body
