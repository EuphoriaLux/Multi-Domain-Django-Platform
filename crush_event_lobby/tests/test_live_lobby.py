from datetime import date, timedelta
import json

import pytest
from allauth.socialaccount.models import SocialAccount
from asgiref.sync import async_to_sync
from asgiref.testing import ApplicationCommunicator
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.test import Client
from django.core.signing import Signer
from django.urls import reverse
from django.utils import timezone

from crush_event_lobby.models import (
    EventLobbyConsent,
    EventLobbyParticipation,
    EventMeetSignal,
)
from crush_event_lobby.services import (
    LobbyAccessError,
    active_lobby_card,
    evaluate_participations_after_onboarding,
    ensure_participation,
    list_live_participants,
    lobby_entry_url,
    lobby_state,
    send_meet_signal,
)
from crush_lu.models import (
    CrushConnectMembership,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    UserBlock,
    UserDataConsent,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("azureproject.urls_crush"),
]

User = get_user_model()


@pytest.fixture(autouse=True)
def enable_lobby(settings):
    settings.CRUSH_EVENT_LOBBY_ENABLED = True


def make_event(*, title="Lobby Night", ended=False):
    now = timezone.now()
    start = now - timedelta(hours=3 if ended else 1)
    return MeetupEvent.objects.create(
        title=title,
        description="Private lobby test",
        event_type="mixer",
        date_time=start,
        duration_minutes=120,
        location="Luxembourg",
        address="1 Test Street",
        max_participants=20,
        registration_deadline=now - timedelta(days=1),
        is_published=True,
    )


def make_member(
    username,
    *,
    luxid=True,
    onboarded=True,
    excluded=False,
    photo=True,
    consent=True,
):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        first_name=username.title(),
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1992, 2, 2),
        gender="NB",
        location="Luxembourg",
        photo_1="tests/lobby.gif" if photo else "",
        is_approved=True,
        is_active=True,
    )
    CrushConnectMembership.objects.create(
        user=user,
        onboarded_at=timezone.now() if onboarded else None,
        excluded_by_coach=excluded,
        photo_share_consent=True,
    )
    if luxid:
        SocialAccount.objects.create(user=user, provider="luxid", uid=username)
    if consent:
        EventLobbyConsent.objects.create(user=user, version=1)
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def attend_and_join(user, event):
    EventRegistration.objects.create(
        user=user,
        event=event,
        status="attended",
        checked_in_at=timezone.now(),
    )
    return ensure_participation(event, user)


def error_code(callable_):
    with pytest.raises(LobbyAccessError) as exc_info:
        callable_()
    return exc_info.value.code


def checkin_url(registration):
    token = Signer().sign(f"{registration.pk}:{registration.event_id}")
    return f"/api/events/checkin/{registration.pk}/{token}/"


def test_real_qr_checkin_enrolls_eligible_member(
    client,
    django_capture_on_commit_callbacks,
):
    event = make_event()
    member = make_member("checkin-member")
    registration = EventRegistration.objects.create(
        user=member,
        event=event,
        status="confirmed",
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(checkin_url(registration))

    assert response.status_code == 200
    registration.refresh_from_db()
    assert registration.status == "attended"
    participation = EventLobbyParticipation.objects.get(
        event=event,
        user=member,
    )
    assert participation.event_registration == registration
    assert participation.eligibility_source == EventLobbyParticipation.SOURCE_CHECKIN


def test_qr_checkin_retry_does_not_duplicate_lobby_participation(
    client,
    django_capture_on_commit_callbacks,
):
    event = make_event()
    member = make_member("checkin-retry")
    registration = EventRegistration.objects.create(
        user=member,
        event=event,
        status="confirmed",
    )
    url = checkin_url(registration)

    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(url).status_code == 200
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(url)

    assert response.status_code == 200
    assert response.json()["already_checked_in"] is True
    assert EventLobbyParticipation.objects.filter(event=event, user=member).count() == 1


def test_lobby_failure_never_rolls_back_checkin(
    client,
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    event = make_event()
    member = make_member("checkin-failure")
    registration = EventRegistration.objects.create(
        user=member,
        event=event,
        status="confirmed",
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("simulated lobby failure")

    monkeypatch.setattr(
        "crush_event_lobby.services.evaluate_participation_after_checkin",
        fail,
    )
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(checkin_url(registration))

    assert response.status_code == 200
    assert response.json()["success"] is True
    registration.refresh_from_db()
    assert registration.status == "attended"


def test_non_connect_guest_checks_in_without_lobby_participation(
    client,
    django_capture_on_commit_callbacks,
):
    event = make_event()
    guest = User.objects.create_user(username="ordinary-guest")
    registration = EventRegistration.objects.create(
        user=guest,
        event=event,
        status="confirmed",
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(checkin_url(registration))

    assert response.status_code == 200
    assert not EventLobbyParticipation.objects.filter(event=event, user=guest).exists()


def test_checked_in_member_sees_lobby_entry_on_ticket_and_event_detail(client):
    event = make_event()
    member = make_member("lobby-entry")
    EventRegistration.objects.create(
        user=member,
        event=event,
        status="attended",
        checked_in_at=timezone.now(),
    )
    expected_url = reverse(
        "crush_lu:event_lobby:lobby",
        kwargs={"event_id": event.pk},
    )
    assert lobby_entry_url(event, member) == expected_url
    client.force_login(member)

    ticket = client.get(reverse("crush_lu:event_ticket", args=[event.pk]))
    detail = client.get(reverse("crush_lu:event_detail", args=[event.pk]))

    assert ticket.status_code == 200
    assert detail.status_code == 200
    assert expected_url in ticket.content.decode()
    assert expected_url in detail.content.decode()
    assert "Enter live lobby" in ticket.content.decode()
    assert "Enter live lobby" in detail.content.decode()


def test_lobby_entry_is_hidden_from_non_connect_guest(client):
    event = make_event()
    guest = User.objects.create_user(username="hidden-lobby-guest")
    UserDataConsent.objects.filter(user=guest).update(crushlu_consent_given=True)
    EventRegistration.objects.create(
        user=guest,
        event=event,
        status="attended",
        checked_in_at=timezone.now(),
    )
    client.force_login(guest)

    ticket = client.get(reverse("crush_lu:event_ticket", args=[event.pk]))
    detail = client.get(reverse("crush_lu:event_detail", args=[event.pk]))

    assert ticket.status_code == 200
    assert detail.status_code == 200
    assert "Enter live lobby" not in ticket.content.decode()
    assert "Enter live lobby" not in detail.content.decode()


def test_active_lobby_card_appears_on_connect_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    event = make_event()
    member = make_member("hub-lobby")
    EventRegistration.objects.create(
        user=member,
        event=event,
        status="attended",
        checked_in_at=timezone.now(),
    )
    client.force_login(member)

    response = client.get(reverse("crush_lu:crush_connect_hub"))

    assert response.status_code == 200
    card = response.context["active_event_lobby"]
    assert card["event_title"] == event.title
    assert card["url"] == reverse(
        "crush_lu:event_lobby:lobby",
        kwargs={"event_id": event.pk},
    )
    body = response.content.decode()
    assert "Live Event Lobby" in body
    assert "You're checked in" in body


def test_active_lobby_card_is_hidden_from_non_connect_guest():
    event = make_event()
    guest = User.objects.create_user(username="hub-hidden-guest")
    EventRegistration.objects.create(
        user=guest,
        event=event,
        status="attended",
        checked_in_at=timezone.now(),
    )

    assert active_lobby_card(guest) is None


def test_finishing_onboarding_joins_current_attended_event():
    event = make_event()
    member = make_member("mid-event-onboarding", onboarded=False)
    EventRegistration.objects.create(
        user=member,
        event=event,
        status="attended",
        checked_in_at=timezone.now(),
    )
    membership = member.crush_connect_membership
    membership.onboarded_at = timezone.now()
    membership.save(update_fields=["onboarded_at"])

    participations = evaluate_participations_after_onboarding(member)

    assert len(participations) == 1
    participation = EventLobbyParticipation.objects.get(event=event, user=member)
    assert participation.eligibility_source == EventLobbyParticipation.SOURCE_ONBOARDING


def test_signal_realtime_hint_is_private_and_identity_free(
    django_capture_on_commit_callbacks,
    monkeypatch,
):
    event = make_event()
    sender = make_member("realtime-sender")
    recipient = make_member("realtime-recipient")
    attend_and_join(sender, event)
    recipient_participation = attend_and_join(recipient, event)
    calls = []

    def record(event_id, reason, *, user_ids=None):
        calls.append((event_id, reason, user_ids))

    monkeypatch.setattr("crush_event_lobby.services.broadcast_lobby_refresh", record)
    with django_capture_on_commit_callbacks(execute=True):
        send_meet_signal(event, sender, recipient_participation.opaque_handle)

    assert calls == [(event.pk, "incoming_signal", [recipient.pk])]
    assert sender.first_name not in repr(calls)

    calls.clear()
    sender_participation = EventLobbyParticipation.objects.get(
        event=event,
        user=sender,
    )
    with django_capture_on_commit_callbacks(execute=True):
        result = send_meet_signal(
            event,
            recipient,
            sender_participation.opaque_handle,
        )

    assert result.mutual is True
    assert calls == [(event.pk, "mutual_revealed", [recipient.pk, sender.pk])]


@pytest.mark.django_db(transaction=True)
def test_event_lobby_consumer_accepts_member_and_sanitizes_broadcast():
    from crush_event_lobby.consumers import EventLobbyConsumer

    event = make_event()
    member = make_member("socket-member")
    attend_and_join(member, event)

    async def scenario():
        communicator = ApplicationCommunicator(
            EventLobbyConsumer.as_asgi(),
            {
                "type": "websocket",
                "path": f"/ws/event-lobby/{event.pk}/",
                "headers": [],
                "query_string": b"",
                "subprotocols": [],
                "user": member,
                "url_route": {"kwargs": {"event_id": event.pk}},
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        assert (await communicator.receive_output())["type"] == "websocket.accept"
        await get_channel_layer().group_send(
            f"event_lobby_{event.pk}",
            {
                "type": "lobby.refresh",
                "reason": "participant_joined",
                "first_name": "Must not leak",
                "user_id": 999,
            },
        )
        output = await communicator.receive_output()
        assert output["type"] == "websocket.send"
        message = json.loads(output["text"])
        assert message == {
            "type": "event_lobby.refresh",
            "reason": "participant_joined",
        }
        await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait()

    async_to_sync(scenario)()


@pytest.mark.django_db(transaction=True)
def test_event_lobby_consumer_rejects_non_participant():
    from crush_event_lobby.consumers import EventLobbyConsumer

    event = make_event()
    outsider = make_member("socket-outsider")

    async def scenario():
        communicator = ApplicationCommunicator(
            EventLobbyConsumer.as_asgi(),
            {
                "type": "websocket",
                "path": f"/ws/event-lobby/{event.pk}/",
                "headers": [],
                "query_string": b"",
                "subprotocols": [],
                "user": outsider,
                "url_route": {"kwargs": {"event_id": event.pk}},
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        assert (await communicator.receive_output())["type"] == "websocket.close"
        await communicator.wait()

    async_to_sync(scenario)()


def test_lobby_page_loads_realtime_and_polling_contract(client):
    event = make_event()
    member = make_member("realtime-page")
    attend_and_join(member, event)
    client.force_login(member)

    response = client.get(
        reverse("crush_lu:event_lobby:lobby", kwargs={"event_id": event.pk})
    )

    assert response.status_code == 200
    body = response.content.decode()
    assert "data-event-lobby" in body
    assert reverse("crush_lu:event_lobby:state", kwargs={"event_id": event.pk}) in body
    assert (
        reverse("crush_lu:event_lobby:participants", kwargs={"event_id": event.pk})
        in body
    )
    assert "crush_event_lobby/event-lobby.js" in body


def test_mutual_signal_flow_is_anonymous_until_reciprocal():
    event = make_event()
    alex = make_member("alex")
    blair = make_member("blair")
    attend_and_join(alex, event)
    blair_participation = attend_and_join(blair, event)

    before = list_live_participants(event, alex)
    assert before == [
        {
            "handle": str(blair_participation.opaque_handle),
            "photo_url": reverse(
                "crush_lu:event_lobby:participant_photo",
                kwargs={
                    "event_id": event.pk,
                    "handle": blair_participation.opaque_handle,
                },
            ),
            "signal_sent": False,
            "mutual": False,
        }
    ]

    first = send_meet_signal(event, alex, blair_participation.opaque_handle)
    assert first.created is True
    assert first.mutual is False
    assert first.first_name is None
    assert lobby_state(event, blair)["incoming_signal_count"] == 1
    assert "first_name" not in list_live_participants(event, alex)[0]

    alex_participation = EventLobbyParticipation.objects.get(event=event, user=alex)
    reciprocal = send_meet_signal(event, blair, alex_participation.opaque_handle)
    assert reciprocal.mutual is True
    assert reciprocal.first_name == "Alex"
    revealed = list_live_participants(event, alex)[0]
    assert revealed["mutual"] is True
    assert revealed["first_name"] == "Blair"
    assert (
        EventMeetSignal.objects.filter(
            event=event, mutual_revealed_at__isnull=False
        ).count()
        == 2
    )


@pytest.mark.parametrize(
    ("member_kwargs", "remove_consent"),
    [
        ({"luxid": False}, False),
        ({"onboarded": False}, False),
        ({"excluded": True}, False),
        ({"photo": False}, False),
        ({}, True),
    ],
)
def test_each_connect_eligibility_gate_denies_participation(
    member_kwargs, remove_consent
):
    event = make_event()
    member = make_member("gated", **member_kwargs)
    if remove_consent:
        member.event_lobby_consent.delete()
    EventRegistration.objects.create(user=member, event=event, status="attended")

    code = error_code(lambda: ensure_participation(event, member))
    assert code in {"not_available", "consent_required"}
    assert not EventLobbyParticipation.objects.filter(user=member).exists()


def test_registered_but_not_attended_cannot_access_or_infer_roster(client):
    event = make_event()
    viewer = make_member("viewer")
    visible = make_member("visible")
    EventRegistration.objects.create(user=viewer, event=event, status="confirmed")
    attend_and_join(visible, event)
    client.force_login(viewer)

    response = client.get(
        reverse("crush_lu:event_lobby:participants", kwargs={"event_id": event.pk})
    )
    assert response.status_code == 404
    assert "Visible" not in response.content.decode()
    assert response.json() == {"detail": "not_available"}


def test_http_roster_uses_opaque_handle_and_contains_no_identity(client):
    event = make_event()
    alex = make_member("alex_http")
    blair = make_member("blair_http")
    attend_and_join(alex, event)
    blair_participation = attend_and_join(blair, event)
    client.force_login(alex)

    response = client.get(
        reverse("crush_lu:event_lobby:participants", kwargs={"event_id": event.pk})
    )
    assert response.status_code == 200
    cache_control = response["Cache-Control"]
    assert "private" in cache_control
    assert "no-store" in cache_control
    assert "max-age=0" in cache_control
    body = response.json()
    assert body["participants"][0]["handle"] == str(blair_participation.opaque_handle)
    assert "first_name" not in body["participants"][0]
    assert "user_id" not in body["participants"][0]
    assert "Blair_Http" not in response.content.decode()


def test_opaque_handle_cannot_be_replayed_for_another_event(client):
    event_one = make_event(title="First")
    event_two = make_event(title="Second")
    viewer = make_member("handle_viewer")
    target = make_member("handle_target")
    attend_and_join(viewer, event_one)
    target_participation = attend_and_join(target, event_one)
    attend_and_join(viewer, event_two)
    client.force_login(viewer)

    response = client.get(
        reverse(
            "crush_lu:event_lobby:participant_photo",
            kwargs={
                "event_id": event_two.pk,
                "handle": target_participation.opaque_handle,
            },
        )
    )
    assert response.status_code == 404


def test_three_signal_quota_is_immutable_and_duplicates_are_idempotent():
    event = make_event()
    sender = make_member("sender")
    attend_and_join(sender, event)
    targets = []
    for index in range(4):
        target = make_member(f"target_{index}")
        targets.append(attend_and_join(target, event))

    for target in targets[:3]:
        result = send_meet_signal(event, sender, target.opaque_handle)
        assert result.created is True
    duplicate = send_meet_signal(event, sender, targets[0].opaque_handle)
    assert duplicate.created is False
    assert duplicate.remaining == 0
    assert EventMeetSignal.objects.filter(event=event, sender=sender).count() == 3

    assert (
        error_code(lambda: send_meet_signal(event, sender, targets[3].opaque_handle))
        == "signal_limit_reached"
    )
    assert EventMeetSignal.objects.filter(event=event, sender=sender).count() == 3


def test_blocked_pair_is_invisible_and_cannot_signal():
    event = make_event()
    alex = make_member("blocked_alex")
    blair = make_member("blocked_blair")
    attend_and_join(alex, event)
    blair_participation = attend_and_join(blair, event)
    UserBlock.objects.create(blocker=alex, blocked=blair)

    assert list_live_participants(event, alex) == []
    assert (
        error_code(
            lambda: send_meet_signal(event, alex, blair_participation.opaque_handle)
        )
        == "not_available"
    )


def test_exact_event_end_rejects_new_participation_and_signals():
    ended_event = make_event(ended=True)
    member = make_member("late")
    EventRegistration.objects.create(user=member, event=ended_event, status="attended")
    assert (
        error_code(lambda: ensure_participation(ended_event, member)) == "not_available"
    )


def test_signal_endpoint_requires_csrf():
    event = make_event()
    sender = make_member("csrf_sender")
    target = make_member("csrf_target")
    attend_and_join(sender, event)
    target_participation = attend_and_join(target, event)
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(sender)

    response = csrf_client.post(
        reverse(
            "crush_lu:event_lobby:signal",
            kwargs={
                "event_id": event.pk,
                "handle": target_participation.opaque_handle,
            },
        )
    )
    assert response.status_code == 403
    assert not EventMeetSignal.objects.filter(event=event).exists()
