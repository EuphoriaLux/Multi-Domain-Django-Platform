"""Tests for the WhatsApp support inbox on crush.lu.

Members' messages to the main Crush.lu number (and any number no active coach
owns), read and answered by every active coach at /coach/whatsapp/support/.
Covers the alert fan-out, what the inbox lists, access, read state, replies
going out from the number the member wrote to, and the media proxy.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import Notification
from crush_lu.services import coach_whatsapp
from crush_lu.tests.test_coach_whatsapp_inbox import (
    COACH_NUMBER_ID,
    MAIN_NUMBER_ID,
    MEMBER_WA,
    _image_inbound,
    _inbound,
    _make_coach,
    _make_member,
    _make_user,
    _post_webhook,
    _webhook_payload,
    graph_response,
    meta_settings,
)
from hub.models import WhatsAppMessage
from hub.whatsapp_service import main_number_messages

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

FORMER_COACH_NUMBER_ID = "4000"
OTHER_MEMBER_WA = "352621000009"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _support_bells(**filters):
    return Notification.objects.filter(
        notification_type=coach_whatsapp.SUPPORT_NOTIFICATION_TYPE, **filters
    )


def _client_for(user):
    client = Client()
    client.force_login(user)
    return client


# --- Webhook alert -----------------------------------------------------------


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_main_number_message_alerts_every_active_coach(mock_push):
    anna = _make_coach()
    ben = _make_coach("coach_ben", number_id="")
    gone = _make_coach("coach_gone", number_id="")
    gone.is_active = False
    gone.save(update_fields=["is_active"])
    _make_member()

    _post_webhook(_webhook_payload(number_id=MAIN_NUMBER_ID))

    bells = _support_bells()
    assert {b.user_id for b in bells} == {anna.user_id, ben.user_id}
    for bell in bells:
        assert "Bea" in bell.body
        assert "Hello coach" not in bell.body  # never the message text
        assert "/coach/whatsapp/support/" in bell.link_url
    assert mock_push.call_count == 2
    assert {c.args[0] for c in mock_push.call_args_list} == {anna, ben}
    for call in mock_push.call_args_list:
        assert "Hello coach" not in call.kwargs["body"]


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_coach_number_message_is_no_support_alert(mock_push):
    _make_coach()
    _make_coach("coach_ben", number_id="")

    _post_webhook(_webhook_payload(number_id=COACH_NUMBER_ID))

    assert not _support_bells().exists()
    mock_push.assert_called_once()  # the owning coach only


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_support_burst_keeps_one_bell_per_coach_and_throttles_push(mock_push):
    coach = _make_coach()
    for n in range(3):
        _post_webhook(_webhook_payload(wa_id=f"wamid.S{n}", number_id=MAIN_NUMBER_ID))

    assert _support_bells(user=coach.user, read_at__isnull=True).count() == 1
    assert mock_push.call_count == 1


@meta_settings
@patch(
    "crush_lu.coach_notifications.notify_coach_whatsapp_message",
    side_effect=[RuntimeError("push down"), None],
)
def test_one_failing_push_does_not_stop_the_others(mock_push):
    _make_coach()
    _make_coach("coach_ben", number_id="")

    resp = _post_webhook(_webhook_payload(number_id=MAIN_NUMBER_ID))

    assert resp.status_code == 200
    assert mock_push.call_count == 2
    assert _support_bells().count() == 2


# --- What the inbox lists ----------------------------------------------------


@meta_settings
def test_inbox_lists_support_chats_not_coach_chats():
    coach = _make_coach()
    _inbound(number_id=COACH_NUMBER_ID, text="for the coach")
    _inbound(number_id=MAIN_NUMBER_ID, frm=MEMBER_WA, text="for support")
    _inbound(number_id="9999", frm=OTHER_MEMBER_WA, text="not mapped yet")
    _inbound(number_id=MAIN_NUMBER_ID, frm="erased", text="")

    resp = _client_for(coach.user).get(reverse("crush_lu:support_whatsapp_inbox"))

    assert resp.status_code == 200
    assert {c.last_text for c in resp.context["conversations"]} == {
        "for support",
        "not mapped yet",
    }
    assert b"WhatsApp support" in resp.content


@meta_settings
def test_inbox_names_the_coach_who_replied_last():
    coach = _make_coach(first_name="Anna")
    _inbound(number_id=MAIN_NUMBER_ID, received_at=timezone.now() - timedelta(hours=1))
    WhatsAppMessage.objects.create(
        user=coach.user, recipient=MEMBER_WA, text="On it", business_phone_number_id=""
    )

    [conv] = coach_whatsapp.conversations_for(coach_whatsapp.SUPPORT)

    assert conv.last_is_outbound
    assert conv.last_text == "On it"
    assert conv.last_sender == "Anna"


@meta_settings
def test_thread_shows_main_number_sends_but_not_coach_replies():
    coach = _make_coach(first_name="Anna")
    sender = _make_user("campaigner")
    _inbound(number_id=MAIN_NUMBER_ID, received_at=timezone.now() - timedelta(hours=2))
    WhatsAppMessage.objects.create(
        user=sender,
        recipient=f"+{MEMBER_WA}",
        template_name="crush_event_invite",
        language="en",
    )
    WhatsAppMessage.objects.create(
        user=coach.user,
        recipient=MEMBER_WA,
        text="coach's own chat",
        business_phone_number_id=COACH_NUMBER_ID,
    )

    items = coach_whatsapp.thread(coach_whatsapp.SUPPORT, MEMBER_WA)

    texts = [item.text for item in items]
    assert "crush_event_invite" in texts
    assert "coach's own chat" not in texts


# --- Access --------------------------------------------------------------------


@meta_settings
def test_plain_member_is_redirected():
    row = _inbound(number_id=MAIN_NUMBER_ID)
    client = _client_for(_make_user("plain"))

    assert client.get(reverse("crush_lu:support_whatsapp_inbox")).status_code == 302
    resp = client.get(reverse("crush_lu:support_whatsapp_thread", args=[row.pk]))
    assert resp.status_code == 302
    resp = client.get(reverse("crush_lu:support_whatsapp_media", args=[row.pk]))
    assert resp.status_code == 302


@meta_settings
def test_deactivated_coach_is_redirected():
    coach = _make_coach(number_id="")
    coach.is_active = False
    coach.save(update_fields=["is_active"])

    resp = _client_for(coach.user).get(reverse("crush_lu:support_whatsapp_inbox"))

    assert resp.status_code == 302


@meta_settings
def test_any_active_coach_and_a_superuser_can_open_it():
    coach_without_number = _make_coach(number_id="")
    admin = _make_user("boss", is_superuser=True, is_staff=True)
    row = _inbound(number_id=MAIN_NUMBER_ID)

    for user in (coach_without_number.user, admin):
        client = _client_for(user)
        resp = client.get(reverse("crush_lu:support_whatsapp_thread", args=[row.pk]))
        assert resp.status_code == 200
        assert resp.context["read_only"] is False


@meta_settings
def test_support_and_coach_conversations_stay_apart():
    coach = _make_coach()
    coach_row = _inbound(number_id=COACH_NUMBER_ID)
    support_row = _inbound(number_id=MAIN_NUMBER_ID, frm=OTHER_MEMBER_WA)
    client = _client_for(coach.user)

    resp = client.get(reverse("crush_lu:support_whatsapp_thread", args=[coach_row.pk]))
    assert resp.status_code == 404
    resp = client.get(reverse("crush_lu:support_whatsapp_media", args=[coach_row.pk]))
    assert resp.status_code == 404
    resp = client.get(reverse("crush_lu:coach_whatsapp_thread", args=[support_row.pk]))
    assert resp.status_code == 404


# --- Read state ----------------------------------------------------------------


@meta_settings
@patch("crush_lu.coach_notifications.notify_coach_whatsapp_message")
def test_opening_a_thread_marks_it_read_and_clears_everyones_bell(mock_push):
    anna = _make_coach()
    ben = _make_coach("coach_ben", number_id="")
    _post_webhook(_webhook_payload(number_id=MAIN_NUMBER_ID))
    row = coach_whatsapp.inbound_for(coach_whatsapp.SUPPORT).get()
    assert _support_bells(read_at__isnull=True).count() == 2

    resp = _client_for(anna.user).get(
        reverse("crush_lu:support_whatsapp_thread", args=[row.pk])
    )

    assert resp.status_code == 200
    row.refresh_from_db()
    assert row.is_read is True
    assert not _support_bells(read_at__isnull=True).exists()
    assert _support_bells(user=ben.user).exists()  # read, not deleted


# --- Replies -------------------------------------------------------------------


@meta_settings
def test_htmx_reply_goes_out_from_the_main_number():
    coach = _make_coach(first_name="Anna")
    row = _inbound(number_id=MAIN_NUMBER_ID)
    client = _client_for(coach.user)

    with patch(
        "hub.whatsapp_service.requests.post",
        return_value=graph_response(body={"messages": [{"id": "wamid.R"}]}),
    ) as mock_post:
        resp = client.post(
            reverse("crush_lu:support_whatsapp_thread", args=[row.pk]),
            {"message": "Happy to help"},
            HTTP_HX_REQUEST="true",
        )

    assert resp.status_code == 200
    assert resp["HX-Trigger"] == "connection-message-sent"
    assert b"Anna" in resp.content
    assert f"/{MAIN_NUMBER_ID}/messages" in mock_post.call_args.args[0]
    sent = WhatsAppMessage.objects.get()
    assert sent.business_phone_number_id == ""
    assert list(main_number_messages()) == [sent]
    assert sent.recipient == MEMBER_WA
    assert sent.user == coach.user

    resp = client.get(reverse("crush_lu:support_whatsapp_messages", args=[row.pk]))
    assert b"Happy to help" in resp.content
    assert b"Anna" in resp.content


@meta_settings
def test_reply_goes_out_from_the_number_the_member_wrote_to():
    former = _make_coach("coach_gone", number_id=FORMER_COACH_NUMBER_ID)
    former.is_active = False
    former.save(update_fields=["is_active"])
    helper = _make_coach("coach_ben", number_id="")
    row = _inbound(number_id=FORMER_COACH_NUMBER_ID)
    client = _client_for(helper.user)

    resp = client.get(reverse("crush_lu:support_whatsapp_thread", args=[row.pk]))
    assert "coach number" in resp.context["wrote_to_label"]

    with patch(
        "hub.whatsapp_service.requests.post",
        return_value=graph_response(body={"messages": [{"id": "wamid.R2"}]}),
    ) as mock_post:
        client.post(
            reverse("crush_lu:support_whatsapp_thread", args=[row.pk]),
            {"message": "We're here"},
            HTTP_HX_REQUEST="true",
        )

    assert f"/{FORMER_COACH_NUMBER_ID}/messages" in mock_post.call_args.args[0]
    assert WhatsAppMessage.objects.get().business_phone_number_id == (
        FORMER_COACH_NUMBER_ID
    )


@meta_settings
def test_closed_window_shows_inline_error_and_sends_nothing():
    coach = _make_coach()
    row = _inbound(
        number_id=MAIN_NUMBER_ID, received_at=timezone.now() - timedelta(days=2)
    )

    with patch("hub.whatsapp_service.requests.post") as mock_post:
        resp = _client_for(coach.user).post(
            reverse("crush_lu:support_whatsapp_thread", args=[row.pk]),
            {"message": "Hello?"},
            HTTP_HX_REQUEST="true",
        )

    assert resp["HX-Retarget"] == "#chat-compose-error"
    mock_post.assert_not_called()


@meta_settings
def test_meta_refusal_shows_inline_error():
    coach = _make_coach()
    row = _inbound(number_id=MAIN_NUMBER_ID)

    with patch(
        "hub.whatsapp_service.requests.post",
        return_value=graph_response(
            ok=False, status_code=400, body={"error": {"code": 131047}}
        ),
    ):
        resp = _client_for(coach.user).post(
            reverse("crush_lu:support_whatsapp_thread", args=[row.pk]),
            {"message": "Hello"},
            HTTP_HX_REQUEST="true",
        )

    assert resp["HX-Retarget"] == "#chat-compose-error"
    assert WhatsAppMessage.objects.get().status == WhatsAppMessage.Status.FAILED


# --- Media -----------------------------------------------------------------------


@meta_settings
def test_support_media_is_streamed_from_meta():
    coach = _make_coach(number_id="")
    row = _image_inbound()
    row.business_phone_number_id = MAIN_NUMBER_ID
    row.save(update_fields=["business_phone_number_id"])
    lookup = graph_response(
        body={
            "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1",
            "mime_type": "image/jpeg",
            "file_size": 3,
        }
    )

    with patch(
        "crush_lu.services.coach_whatsapp.requests.get",
        side_effect=[lookup, graph_response(content=b"JPG")],
    ):
        resp = _client_for(coach.user).get(
            reverse("crush_lu:support_whatsapp_media", args=[row.pk])
        )

    assert resp.status_code == 200
    assert resp.content == b"JPG"
    assert resp["Content-Disposition"] == "inline"


# --- Navigation ------------------------------------------------------------------


def _nav_context(user):
    from django.contrib.sessions.backends.db import SessionStore

    from crush_lu.context_processors import crush_user_context

    request = RequestFactory().get("/en/", HTTP_HOST="crush.lu")
    request.user = user
    request.session = SessionStore()
    return crush_user_context(request)


@meta_settings
def test_nav_shows_support_unread_to_coaches():
    coach = _make_coach()  # owns COACH_NUMBER_ID
    _inbound(number_id=MAIN_NUMBER_ID)
    _inbound(number_id=COACH_NUMBER_ID, frm=OTHER_MEMBER_WA)

    context = _nav_context(coach.user)

    assert context["support_whatsapp_enabled"] is True
    assert context["support_whatsapp_unread"] == 1
    assert context["coach_whatsapp_unread"] == 1


@override_settings(META_WHATSAPP_ACCESS_TOKEN="", META_PHONE_NUMBER_ID="")
def test_nav_hides_support_without_whatsapp():
    coach = _make_coach(number_id="")

    assert "support_whatsapp_enabled" not in _nav_context(coach.user)
