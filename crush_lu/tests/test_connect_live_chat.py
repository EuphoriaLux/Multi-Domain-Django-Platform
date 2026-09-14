from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from crush_lu.models import ConnectChatMessage, ConnectTemporaryChat
from crush_lu.services.connect_chat import (
    block_chat_partner,
    send_message,
    sync_chat_state,
)
from crush_lu.services.connect_summary import get_connect_summary
from crush_lu.tests.test_connect_chat_flows import CHATS_URL, _make_open_chat
from crush_lu.tests.test_crush_connect import _login_eligible, _make_user

pytestmark = pytest.mark.django_db


def test_retry_returns_same_message_without_extending_expiry():
    me, _, chat = _make_open_chat()
    submission = uuid4()
    first = send_message(chat, me, "Hello", submission)
    chat.refresh_from_db()
    expiry = chat.expires_at
    second = send_message(chat, me, "Hello", submission)
    chat.refresh_from_db()
    assert first.pk == second.pk
    assert chat.messages.count() == 1
    assert chat.expires_at == expiry


def test_incremental_reads_and_ack_are_scoped_and_do_not_extend_chat(client):
    me, peer, chat = _make_open_chat()
    incoming = send_message(chat, peer, "Hello")
    outgoing = send_message(chat, me, "Hi")
    chat.refresh_from_db()
    expiry = chat.expires_at
    _login_eligible(client, me)
    url = f"{CHATS_URL}{chat.pk}/"
    response = client.get(url + "messages/", {"after": incoming.pk})
    assert response.status_code == 200
    assert [row["id"] for row in response.json()["messages"]] == [outgoing.pk]
    assert set(response.json()["messages"][0]) == {"id", "text", "mine", "sent_at"}
    incoming.refresh_from_db()
    assert incoming.read_at is None
    assert get_connect_summary(me)["unread_chats"] == 1
    client.post(url + "read/", {"message_ids": [incoming.pk, outgoing.pk]})
    incoming.refresh_from_db()
    outgoing.refresh_from_db()
    assert incoming.read_at is not None
    assert outgoing.read_at is None
    assert get_connect_summary(me)["unread_chats"] == 0
    chat.refresh_from_db()
    assert chat.expires_at == expiry


def test_history_is_bounded_and_cursor_retrieves_older_messages(client):
    me, peer, chat = _make_open_chat()
    ConnectChatMessage.objects.bulk_create(
        [ConnectChatMessage(chat=chat, sender=peer, message=str(i)) for i in range(65)]
    )
    _login_eligible(client, me)
    url = f"{CHATS_URL}{chat.pk}/messages/"
    recent = client.get(url).json()
    assert len(recent["messages"]) == 50
    assert recent["has_older"]
    older = client.get(url, {"before": recent["messages"][0]["id"]}).json()
    assert len(older["messages"]) == 15
    assert not older["has_older"]
    assert client.get(url, {"after": "garbage"}).status_code == 400


@pytest.mark.parametrize("blocked", [True, False])
def test_terminal_chat_preserves_history_but_rejects_ack_and_send(client, blocked):
    me, peer, chat = _make_open_chat()
    message = send_message(chat, peer, "Before")
    _login_eligible(client, me)
    if blocked:
        block_chat_partner(chat, peer)
    else:
        ConnectTemporaryChat.objects.filter(pk=chat.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    url = f"{CHATS_URL}{chat.pk}/"
    history = client.get(url + "messages/")
    assert history.status_code == 200
    assert history.json()["is_open"] is False
    assert [row["id"] for row in history.json()["messages"]] == [message.pk]
    assert client.post(url + "read/", {"message_ids": [message.pk]}).status_code == 410
    assert (
        client.post(
            url + "send/", {"message": "After"}, HTTP_ACCEPT="application/json"
        ).status_code
        == 409
    )
    assert chat.messages.count() == 1


@pytest.mark.parametrize("blocked", [True, False])
def test_terminal_chat_can_load_more_than_fifty_messages(client, blocked):
    me, peer, chat = _make_open_chat()
    ConnectChatMessage.objects.bulk_create(
        [ConnectChatMessage(chat=chat, sender=peer, message=str(i)) for i in range(65)]
    )
    if blocked:
        block_chat_partner(chat, peer)
    else:
        ConnectTemporaryChat.objects.filter(pk=chat.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    _login_eligible(client, me)
    url = f"{CHATS_URL}{chat.pk}/messages/"
    recent = client.get(url).json()
    older = client.get(url, {"before": recent["messages"][0]["id"]}).json()
    assert len(recent["messages"]) == 50
    assert len(older["messages"]) == 15
    assert recent["is_open"] is older["is_open"] is False
    assert not older["has_older"]


def test_stale_poll_does_not_close_chat_after_concurrent_expiry_extension():
    from unittest.mock import patch

    _, _, chat = _make_open_chat()
    stale_expiry = timezone.now() - timedelta(seconds=1)
    ConnectTemporaryChat.objects.filter(pk=chat.pk).update(expires_at=stale_expiry)
    chat.refresh_from_db()
    extended = timezone.now() + timedelta(days=7)
    ConnectTemporaryChat.objects.filter(pk=chat.pk).update(expires_at=extended)
    manager = ConnectTemporaryChat.objects
    with patch.object(
        manager, "select_for_update", wraps=manager.select_for_update
    ) as lock:
        synced = sync_chat_state(chat)
    lock.assert_called_once()
    assert synced.status == ConnectTemporaryChat.Status.ACTIVE
    assert synced.expires_at == extended
    synced.refresh_from_db()
    assert synced.status == ConnectTemporaryChat.Status.ACTIVE


@pytest.mark.parametrize("participant", ["viewer", "peer"])
@pytest.mark.parametrize("unavailable", ["excluded", "inactive", "membership_removed"])
def test_unavailable_participants_cannot_expose_list_previews(
    client, participant, unavailable
):
    me, peer, chat = _make_open_chat()
    send_message(chat, peer, "Private preview must be hidden")
    member = me if participant == "viewer" else peer
    if unavailable == "excluded":
        membership = member.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
    elif unavailable == "inactive":
        member.crushprofile.is_active = False
        member.crushprofile.save(update_fields=["is_active"])
    else:
        member.crush_connect_membership.delete()
    _login_eligible(client, me)
    response = client.get(CHATS_URL)
    assert b"Private preview must be hidden" not in response.content
    assert client.get(f"{CHATS_URL}{chat.pk}/messages/").status_code == 404


def test_stranger_cannot_read_or_ack(client):
    _, _, chat = _make_open_chat()
    stranger = _make_user(username="stranger")
    _login_eligible(client, stranger)
    url = f"{CHATS_URL}{chat.pk}/"
    assert client.get(url + "messages/").status_code == 404
    assert client.post(url + "read/").status_code == 404


def test_async_send_is_idempotent_and_normal_form_still_redirects(client):
    me, _, chat = _make_open_chat()
    _login_eligible(client, me)
    url = f"{CHATS_URL}{chat.pk}/send/"
    payload = {"message": "Hello", "client_submission_id": str(uuid4())}
    first = client.post(url, payload, HTTP_ACCEPT="application/json")
    retry = client.post(url, payload, HTTP_ACCEPT="application/json")
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    assert chat.messages.count() == 1
    assert client.post(url, {"message": "Normal form"}).status_code == 302


@pytest.mark.parametrize("state", ["paused", "excluded", "not_onboarded"])
def test_changed_participation_rechecked_without_breaking_paused_chats(client, state):
    me, peer, chat = _make_open_chat()
    membership = peer.crush_connect_membership
    if state == "paused":
        membership.paused_at = timezone.now()
    elif state == "excluded":
        membership.excluded_by_coach = True
    else:
        membership.onboarded_at = None
    membership.save()
    _login_eligible(client, me)
    response = client.get(f"{CHATS_URL}{chat.pk}/messages/")
    assert response.status_code == (200 if state == "paused" else 404)
