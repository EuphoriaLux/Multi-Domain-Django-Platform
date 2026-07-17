from datetime import date, timedelta

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from crush_event_lobby.models import (
    EventLobbyConsent,
    EventLobbyParticipation,
    EventMeetSignal,
)
from crush_event_lobby.services import (
    LobbyAccessError,
    ensure_participation,
    list_live_participants,
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
