"""Tests for inbound WhatsApp capture + the hub support inbox.

Covers the webhook half (signature-verified inbound storage, idempotency, and
that status callbacks still work alongside inbound) and the inbox API half
(list/filter/unread-count + mark-read, admin-only).
"""

import hashlib
import hmac
import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from hub.models import WhatsAppInboundMessage, WhatsAppMessage

pytestmark = pytest.mark.django_db

CRUSH_HOST = "crush.lu"
WEBHOOK_PATH = "/api/webhooks/whatsapp/"
APP_SECRET = "test-app-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


def _post_webhook(client, payload: dict, *, secret_ok: bool = True):
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body) if secret_ok else "sha256=deadbeef"
    return client.post(
        WEBHOOK_PATH,
        data=body,
        content_type="application/json",
        HTTP_HOST=CRUSH_HOST,
        HTTP_X_HUB_SIGNATURE_256=sig,
    )


def _inbound_payload(wa_id="wamid.IN1", frm="352621000001", body="Who is this?"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [
                                {"profile": {"name": "Alice"}, "wa_id": frm}
                            ],
                            "messages": [
                                {
                                    "from": frm,
                                    "id": wa_id,
                                    "timestamp": "1718960000",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def admin_user(db):
    return get_user_model().objects.create_user(
        username="admin@example.com",
        email="admin@example.com",
        password="adminpass123",
        is_staff=True,
    )


@pytest.fixture
def plain_user(db):
    return get_user_model().objects.create_user(
        username="user@example.com",
        email="user@example.com",
        password="userpass123",
    )


# --- Webhook: inbound capture ----------------------------------------------


@override_settings(META_WHATSAPP_APP_SECRET=APP_SECRET)
def test_inbound_text_is_stored():
    resp = _post_webhook(Client(), _inbound_payload(body="Hello there"))
    assert resp.status_code == 200

    msg = WhatsAppInboundMessage.objects.get(wa_message_id="wamid.IN1")
    assert msg.from_number == "352621000001"
    assert msg.contact_name == "Alice"
    assert msg.message_type == "text"
    assert msg.text == "Hello there"
    assert msg.is_read is False
    assert msg.payload["id"] == "wamid.IN1"


@override_settings(META_WHATSAPP_APP_SECRET=APP_SECRET)
def test_inbound_is_idempotent_on_retry():
    client = Client()
    payload = _inbound_payload()
    assert _post_webhook(client, payload).status_code == 200
    # Meta re-POSTs the same event until it gets a 200; must not duplicate.
    assert _post_webhook(client, payload).status_code == 200
    assert WhatsAppInboundMessage.objects.filter(wa_message_id="wamid.IN1").count() == 1


@override_settings(META_WHATSAPP_APP_SECRET=APP_SECRET)
def test_invalid_signature_stores_nothing():
    resp = _post_webhook(Client(), _inbound_payload(), secret_ok=False)
    assert resp.status_code == 403
    assert WhatsAppInboundMessage.objects.count() == 0


@override_settings(META_WHATSAPP_APP_SECRET=APP_SECRET)
def test_statuses_and_inbound_coexist(admin_user):
    """A status callback for an outbound message and an inbound reply in the
    same payload are both processed."""
    out = WhatsAppMessage.objects.create(
        user=admin_user,
        wa_message_id="wamid.OUT1",
        recipient="352621000001",
        template_name="hello_world",
        language="en_US",
        status=WhatsAppMessage.Status.SENT,
        status_history=[{"status": "sent", "timestamp": "x"}],
    )
    payload = _inbound_payload()
    payload["entry"][0]["changes"][0]["value"]["statuses"] = [
        {"id": "wamid.OUT1", "status": "delivered", "timestamp": "1718960001"}
    ]

    assert _post_webhook(Client(), payload).status_code == 200

    out.refresh_from_db()
    assert out.status == WhatsAppMessage.Status.DELIVERED
    assert WhatsAppInboundMessage.objects.filter(wa_message_id="wamid.IN1").exists()


@override_settings(META_WHATSAPP_APP_SECRET=APP_SECRET)
def test_non_text_inbound_keeps_payload_and_blank_text():
    payload = _inbound_payload(wa_id="wamid.IMG1")
    msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    msg.pop("text")
    msg["type"] = "image"
    msg["image"] = {"id": "media-123", "mime_type": "image/jpeg"}

    assert _post_webhook(Client(), payload).status_code == 200
    stored = WhatsAppInboundMessage.objects.get(wa_message_id="wamid.IMG1")
    assert stored.message_type == "image"
    assert stored.text == ""
    assert stored.payload["image"]["id"] == "media-123"


# --- Inbox API -------------------------------------------------------------


def _make_inbound(wa_id, frm="352621000001", read=False):
    from django.utils import timezone

    return WhatsAppInboundMessage.objects.create(
        wa_message_id=wa_id,
        from_number=frm,
        contact_name="Alice",
        message_type="text",
        text="hi",
        received_at=timezone.now(),
        is_read=read,
    )


def test_inbox_requires_admin(plain_user):
    client = APIClient()
    client.force_authenticate(user=plain_user)
    resp = client.get("/hub/whatsapp/inbox", HTTP_HOST=CRUSH_HOST)
    assert resp.status_code == 403


def test_inbox_lists_with_unread_count(admin_user):
    _make_inbound("wamid.A", read=True)
    _make_inbound("wamid.B", read=False)
    _make_inbound("wamid.C", read=False)

    client = APIClient()
    client.force_authenticate(user=admin_user)
    resp = client.get("/hub/whatsapp/inbox", HTTP_HOST=CRUSH_HOST)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 3
    assert data["unread_count"] == 2


def test_inbox_unread_filter(admin_user):
    _make_inbound("wamid.A", read=True)
    _make_inbound("wamid.B", read=False)

    client = APIClient()
    client.force_authenticate(user=admin_user)
    resp = client.get("/hub/whatsapp/inbox?unread=1", HTTP_HOST=CRUSH_HOST)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["wa_message_id"] == "wamid.B"


def test_mark_read_by_ids(admin_user):
    a = _make_inbound("wamid.A")
    b = _make_inbound("wamid.B")

    client = APIClient()
    client.force_authenticate(user=admin_user)
    resp = client.post(
        "/hub/whatsapp/inbox/read",
        {"ids": [a.id]},
        format="json",
        HTTP_HOST=CRUSH_HOST,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.is_read is True
    assert b.is_read is False


def test_mark_read_all(admin_user):
    _make_inbound("wamid.A")
    _make_inbound("wamid.B")

    client = APIClient()
    client.force_authenticate(user=admin_user)
    resp = client.post(
        "/hub/whatsapp/inbox/read",
        {"all": True},
        format="json",
        HTTP_HOST=CRUSH_HOST,
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2
    assert WhatsAppInboundMessage.objects.filter(is_read=False).count() == 0


def test_mark_read_requires_ids_or_all(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    resp = client.post(
        "/hub/whatsapp/inbox/read", {}, format="json", HTTP_HOST=CRUSH_HOST
    )
    assert resp.status_code == 400
