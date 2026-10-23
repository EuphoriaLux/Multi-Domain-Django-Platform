"""Tests for the coach WhatsApp inbox (coach numbers on the Cloud API).

Covers webhook routing by receiving number, the coach alert (bell + push),
the shared admin inbox leaving coach chats out, per-number and free-form
sends, the 24-hour reply window, inbox access rules, and the media proxy.
"""

import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from crush_lu.models import CrushCoach, CrushProfile, Notification, UserDataConsent
from crush_lu.services import coach_whatsapp
from hub.models import ErasedPhoneNumber, WhatsAppInboundMessage, WhatsAppMessage
from hub.whatsapp_service import (
    main_number_messages,
    send_whatsapp_template,
    send_whatsapp_text,
    shared_inbox_messages,
)

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]
User = get_user_model()

APP_SECRET = "test-app-secret"
MAIN_NUMBER_ID = "1000"
COACH_NUMBER_ID = "2000"
OTHER_COACH_NUMBER_ID = "3000"
MEMBER_WA = "352621000001"

meta_settings = override_settings(
    META_WHATSAPP_APP_SECRET=APP_SECRET,
    META_WHATSAPP_ACCESS_TOKEN="test-token",
    META_PHONE_NUMBER_ID=MAIN_NUMBER_ID,
    META_WABA_ID="waba",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _make_user(username, **extra):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="pass12345",
        first_name=extra.pop("first_name", username.title()),
        **extra,
    )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    return user


def _make_coach(username="coach_anna", number_id=COACH_NUMBER_ID, **extra):
    return CrushCoach.objects.create(
        user=_make_user(username, **extra),
        phone_number="+352691000000",
        whatsapp_phone_number_id=number_id,
    )


def _make_member(username="member_bea", phone="+352 621 000 001"):
    user = _make_user(username, first_name="Bea", last_name="Muller")
    return CrushProfile.objects.create(
        user=user,
        date_of_birth="1995-01-01",
        gender="F",
        location="Luxembourg",
        phone_number=phone,
        phone_verified=True,
    )


def _inbound(number_id=COACH_NUMBER_ID, frm=MEMBER_WA, text="Hi coach", **extra):
    defaults = {
        "wa_message_id": f"wamid.{WhatsAppInboundMessage.objects.count() + 1}",
        "from_number": frm,
        "contact_name": "Bea WA",
        "message_type": "text",
        "text": text,
        "payload": {},
        "received_at": timezone.now(),
        "business_phone_number_id": number_id,
    }
    defaults.update(extra)
    return WhatsAppInboundMessage.objects.create(**defaults)


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _post_webhook(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    return Client().post(
        "/api/webhooks/whatsapp/",
        data=body,
        content_type="application/json",
        HTTP_HOST="crush.lu",
        HTTP_X_HUB_SIGNATURE_256=_sign(body),
    )


def _webhook_payload(wa_id="wamid.IN1", number_id=COACH_NUMBER_ID, frm=MEMBER_WA):
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
                            "metadata": {
                                "display_phone_number": "352691000000",
                                "phone_number_id": number_id,
                            },
                            "contacts": [{"profile": {"name": "Bea"}, "wa_id": frm}],
                            "messages": [
                                {
                                    "from": frm,
                                    "id": wa_id,
                                    "timestamp": str(int(timezone.now().timestamp())),
                                    "type": "text",
                                    "text": {"body": "Hello coach"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def graph_response(ok=True, status_code=200, body=None, content=b"{}"):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.content = content
    resp.json.return_value = body if body is not None else {}
    return resp


# --- Webhook routing and coach alert ---------------------------------------


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_webhook_stores_receiving_number_and_alerts_coach(mock_push):
    coach = _make_coach()
    _make_member()

    assert _post_webhook(_webhook_payload()).status_code == 200

    row = WhatsAppInboundMessage.objects.get(wa_message_id="wamid.IN1")
    assert row.business_phone_number_id == COACH_NUMBER_ID
    bell = Notification.objects.get(user=coach.user)
    assert bell.notification_type == coach_whatsapp.NOTIFICATION_TYPE
    assert "Bea" in bell.body
    assert "Hello coach" not in bell.body  # never the message text
    assert bell.link_url.endswith(f"/coach/whatsapp/{row.pk}/")
    mock_push.assert_called_once()
    assert "Hello coach" not in mock_push.call_args.kwargs["body"]


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_webhook_retry_alerts_once(mock_push):
    coach = _make_coach()
    _post_webhook(_webhook_payload())
    _post_webhook(_webhook_payload())  # Meta re-delivers the same message

    assert WhatsAppInboundMessage.objects.count() == 1
    assert Notification.objects.filter(user=coach.user).count() == 1
    assert mock_push.call_count == 1


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_burst_keeps_one_unread_bell_and_throttles_push(mock_push):
    coach = _make_coach()
    for n in range(3):
        _post_webhook(_webhook_payload(wa_id=f"wamid.B{n}"))

    assert (
        Notification.objects.filter(user=coach.user, read_at__isnull=True).count() == 1
    )
    assert mock_push.call_count == 1


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_main_and_unmapped_numbers_alert_no_coach(mock_push):
    _make_coach()
    _post_webhook(_webhook_payload(wa_id="wamid.M", number_id=MAIN_NUMBER_ID))
    _post_webhook(_webhook_payload(wa_id="wamid.U", number_id="9999"))

    assert WhatsAppInboundMessage.objects.count() == 2
    assert not Notification.objects.exists()
    mock_push.assert_not_called()


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_deactivated_coach_number_falls_back_to_shared_inbox(mock_push):
    coach = _make_coach()
    coach.is_active = False
    coach.save(update_fields=["is_active"])

    _post_webhook(_webhook_payload())

    row = WhatsAppInboundMessage.objects.get()
    assert not Notification.objects.exists()
    mock_push.assert_not_called()
    # Nobody can open the deactivated coach's inbox, so the shared one shows it.
    assert list(shared_inbox_messages()) == [row]


@meta_settings
def test_webhook_answers_200_when_the_alert_fails():
    _make_coach()
    with patch("crush_lu.services.coach_whatsapp.on_inbound", side_effect=RuntimeError):
        assert _post_webhook(_webhook_payload()).status_code == 200
    assert WhatsAppInboundMessage.objects.count() == 1


# --- Shared admin inbox -----------------------------------------------------


@meta_settings
def test_hub_inbox_leaves_out_coach_chats():
    _make_coach()
    _inbound(number_id=COACH_NUMBER_ID, text="for the coach")
    _inbound(number_id=MAIN_NUMBER_ID, text="for support")
    _inbound(number_id="", text="legacy row")
    _inbound(number_id="9999", text="not mapped yet")
    admin = User.objects.create_user(username="admin", password="x", is_staff=True)
    client = APIClient()
    client.force_authenticate(admin)

    resp = client.get("/hub/whatsapp/inbox", HTTP_HOST="crush.lu")

    assert resp.status_code == 200
    texts = {item["text"] for item in resp.json()["items"]}
    assert texts == {"for support", "legacy row", "not mapped yet"}
    assert resp.json()["unread_count"] == 3

    client.post(
        "/hub/whatsapp/inbox/read",
        {"all": True},
        format="json",
        HTTP_HOST="crush.lu",
    )
    assert WhatsAppInboundMessage.objects.get(text="for the coach").is_read is False


def test_outreach_views_leave_out_coach_replies():
    sender = _make_user("sender")
    campaign = WhatsAppMessage.objects.create(
        user=sender, recipient=MEMBER_WA, template_name="event", language="en"
    )
    WhatsAppMessage.objects.create(
        user=sender,
        recipient=MEMBER_WA,
        text="coach reply",
        business_phone_number_id=COACH_NUMBER_ID,
    )
    assert list(main_number_messages()) == [campaign]


# --- Sends ------------------------------------------------------------------


@meta_settings
def test_text_reply_goes_out_from_the_coach_number():
    sender = _make_user("sender")
    with patch(
        "hub.whatsapp_service.requests.post",
        return_value=graph_response(body={"messages": [{"id": "wamid.OUT"}]}),
    ) as mock_post:
        msg = send_whatsapp_text(
            sender=sender,
            recipient=MEMBER_WA,
            text="See you Friday",
            phone_number_id=COACH_NUMBER_ID,
        )

    assert mock_post.call_args.args[0].endswith(f"/{COACH_NUMBER_ID}/messages")
    payload = mock_post.call_args.kwargs["json"]
    assert payload["type"] == "text"
    assert payload["text"] == {"body": "See you Friday", "preview_url": False}
    assert msg.status == WhatsAppMessage.Status.SENT
    assert msg.business_phone_number_id == COACH_NUMBER_ID
    assert msg.text == "See you Friday"


@meta_settings
def test_template_send_defaults_to_the_main_number():
    sender = _make_user("sender")
    with patch(
        "hub.whatsapp_service.requests.post",
        return_value=graph_response(body={"messages": [{"id": "wamid.T"}]}),
    ) as mock_post:
        send_whatsapp_template(
            sender=sender,
            recipient=MEMBER_WA,
            template_name="hello_world",
            language="en_US",
            parameters={},
        )
    assert mock_post.call_args.args[0].endswith(f"/{MAIN_NUMBER_ID}/messages")


@meta_settings
def test_reply_outside_window_is_recorded_failed():
    sender = _make_user("sender")
    body = {"error": {"code": 131047, "message": "Re-engagement message"}}
    with patch(
        "hub.whatsapp_service.requests.post",
        return_value=graph_response(ok=False, status_code=400, body=body),
    ):
        msg = send_whatsapp_text(
            sender=sender,
            recipient=MEMBER_WA,
            text="late",
            phone_number_id=COACH_NUMBER_ID,
        )
    assert msg.status == WhatsAppMessage.Status.FAILED
    assert msg.status_history[-1]["error_code"] == 131047


@meta_settings
def test_text_reply_to_erased_number_is_blanked():
    sender = _make_user("sender")
    ErasedPhoneNumber.record(["+352621999999"])
    with patch(
        "hub.whatsapp_service.requests.post",
        return_value=graph_response(body={"messages": [{"id": "wamid.E"}]}),
    ):
        msg = send_whatsapp_text(
            sender=sender,
            recipient="352621999999",
            text="private words",
            phone_number_id=COACH_NUMBER_ID,
        )
    msg.refresh_from_db()
    assert msg.recipient == ""
    assert msg.text == ""


# --- Service: conversations, window, member matching ------------------------


def test_member_matched_on_formatted_number():
    coach = _make_coach()
    _make_member(phone="+352 621 000 001")
    _inbound(frm=MEMBER_WA)

    [conv] = coach_whatsapp.conversations_for(coach)
    assert conv.profile is not None
    assert conv.display_name == "Bea Muller"


def test_non_member_shows_whatsapp_name():
    coach = _make_coach()
    _inbound(frm="33600000000", contact_name="Stranger")

    [conv] = coach_whatsapp.conversations_for(coach)
    assert conv.profile is None
    assert conv.display_name == "Stranger"


def test_erased_sender_never_listed():
    coach = _make_coach()
    _inbound(frm="erased", contact_name="", text="")
    assert coach_whatsapp.conversations_for(coach) == []


def test_reply_window_edges():
    now = timezone.now()
    assert coach_whatsapp.window_open(now - timedelta(hours=23, minutes=59), now)
    assert not coach_whatsapp.window_open(now - timedelta(hours=24, minutes=1), now)


@meta_settings
def test_send_reply_refused_once_window_closed():
    coach = _make_coach()
    _inbound(received_at=timezone.now() - timedelta(hours=25))
    [conv] = coach_whatsapp.conversations_for(coach)

    with patch("hub.whatsapp_service.requests.post") as mock_post:
        with pytest.raises(coach_whatsapp.CoachWhatsAppError):
            coach_whatsapp.send_reply(coach, conv, "too late", coach.user)
    mock_post.assert_not_called()


# --- Views: access and replies ---------------------------------------------


def test_non_coach_is_redirected():
    client = Client()
    client.force_login(_make_user("plain"))
    resp = client.get(reverse("crush_lu:coach_whatsapp_inbox"))
    assert resp.status_code == 302


def test_coach_sees_only_own_conversations():
    coach = _make_coach()
    other = _make_coach("coach_ben", number_id=OTHER_COACH_NUMBER_ID)
    _inbound(text="mine")
    theirs = _inbound(number_id=OTHER_COACH_NUMBER_ID, frm="352621000002")
    client = Client()
    client.force_login(coach.user)

    resp = client.get(reverse("crush_lu:coach_whatsapp_inbox"))
    assert resp.status_code == 200
    assert [c.last_text for c in resp.context["conversations"]] == ["mine"]

    resp = client.get(reverse("crush_lu:coach_whatsapp_thread", args=[theirs.pk]))
    assert resp.status_code == 404
    resp = client.get(reverse("crush_lu:coach_whatsapp_media", args=[theirs.pk]))
    assert resp.status_code == 404
    assert other.whatsapp_phone_number_id == OTHER_COACH_NUMBER_ID


def test_opening_a_thread_marks_it_read():
    coach = _make_coach()
    row = _inbound()
    client = Client()
    client.force_login(coach.user)

    resp = client.get(reverse("crush_lu:coach_whatsapp_thread", args=[row.pk]))

    assert resp.status_code == 200
    row.refresh_from_db()
    assert row.is_read is True


def test_superuser_reads_another_coach_thread_read_only():
    _make_coach()
    admin_coach = _make_coach("coach_admin", number_id="", is_superuser=True)
    row = _inbound()
    client = Client()
    client.force_login(admin_coach.user)

    resp = client.get(reverse("crush_lu:coach_whatsapp_thread", args=[row.pk]))
    assert resp.status_code == 200
    assert resp.context["read_only"] is True
    row.refresh_from_db()
    assert row.is_read is False  # the coach's unread state is left alone

    resp = client.post(
        reverse("crush_lu:coach_whatsapp_thread", args=[row.pk]),
        {"message": "hello"},
    )
    assert resp.status_code == 404


@meta_settings
def test_htmx_reply_sends_from_coach_number():
    coach = _make_coach()
    row = _inbound()
    client = Client()
    client.force_login(coach.user)

    with patch(
        "hub.whatsapp_service.requests.post",
        return_value=graph_response(body={"messages": [{"id": "wamid.R"}]}),
    ):
        resp = client.post(
            reverse("crush_lu:coach_whatsapp_thread", args=[row.pk]),
            {"message": "Happy to help"},
            HTTP_HX_REQUEST="true",
        )

    assert resp.status_code == 200
    assert resp["HX-Trigger"] == "connection-message-sent"
    sent = WhatsAppMessage.objects.get()
    assert sent.business_phone_number_id == COACH_NUMBER_ID
    assert sent.recipient == MEMBER_WA
    assert sent.user == coach.user

    resp = client.get(reverse("crush_lu:coach_whatsapp_messages", args=[row.pk]))
    assert b"Happy to help" in resp.content


@meta_settings
def test_htmx_reply_with_closed_window_shows_inline_error():
    coach = _make_coach()
    row = _inbound(received_at=timezone.now() - timedelta(days=2))
    client = Client()
    client.force_login(coach.user)

    with patch("hub.whatsapp_service.requests.post") as mock_post:
        resp = client.post(
            reverse("crush_lu:coach_whatsapp_thread", args=[row.pk]),
            {"message": "Hello?"},
            HTTP_HX_REQUEST="true",
        )

    assert resp["HX-Retarget"] == "#chat-compose-error"
    mock_post.assert_not_called()
    assert not WhatsAppMessage.objects.exists()


# --- Media proxy -------------------------------------------------------------


def _image_inbound(mime="image/jpeg"):
    return _inbound(
        message_type="image",
        text="",
        payload={"type": "image", "image": {"id": "MEDIA1", "mime_type": mime}},
    )


@meta_settings
def test_media_is_streamed_from_meta():
    coach = _make_coach()
    row = _image_inbound()
    client = Client()
    client.force_login(coach.user)
    lookup = graph_response(
        body={
            "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1",
            "mime_type": "image/jpeg",
            "file_size": 3,
        }
    )
    download = graph_response(content=b"JPG")

    with patch(
        "crush_lu.services.coach_whatsapp.requests.get",
        side_effect=[lookup, download],
    ) as mock_get:
        resp = client.get(reverse("crush_lu:coach_whatsapp_media", args=[row.pk]))

    assert resp.status_code == 200
    assert resp.content == b"JPG"
    assert resp["Content-Disposition"] == "inline"
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert mock_get.call_args_list[0].args[0].endswith("/MEDIA1")


@meta_settings
def test_media_token_never_sent_to_an_unexpected_host():
    coach = _make_coach()
    row = _image_inbound()
    client = Client()
    client.force_login(coach.user)
    lookup = graph_response(body={"url": "https://evil.example/x", "file_size": 3})

    with patch(
        "crush_lu.services.coach_whatsapp.requests.get", side_effect=[lookup]
    ) as mock_get:
        resp = client.get(reverse("crush_lu:coach_whatsapp_media", args=[row.pk]))

    assert resp.status_code == 404
    assert mock_get.call_count == 1


@meta_settings
def test_unsafe_media_type_is_a_download():
    coach = _make_coach()
    row = _inbound(
        message_type="document",
        text="",
        payload={
            "type": "document",
            "document": {"id": "DOC1", "mime_type": "text/html"},
        },
    )
    client = Client()
    client.force_login(coach.user)
    lookup = graph_response(
        body={
            "url": "https://lookaside.fbsbx.com/doc",
            "mime_type": "text/html",
            "file_size": 5,
        }
    )
    with patch(
        "crush_lu.services.coach_whatsapp.requests.get",
        side_effect=[lookup, graph_response(content=b"<b>x</b>")],
    ):
        resp = client.get(reverse("crush_lu:coach_whatsapp_media", args=[row.pk]))

    assert resp.status_code == 200
    assert resp["Content-Disposition"] == "attachment"


# --- Erasure -----------------------------------------------------------------


def test_outbound_anonymiser_blanks_reply_text():
    from crush_lu.views_account import _anonymize_whatsapp_outbound

    sender = _make_user("sender")
    msg = WhatsAppMessage.objects.create(
        user=sender,
        recipient=MEMBER_WA,
        text="personal reply",
        business_phone_number_id=COACH_NUMBER_ID,
    )
    _anonymize_whatsapp_outbound({MEMBER_WA})
    msg.refresh_from_db()
    assert msg.recipient == ""
    assert msg.text == ""
