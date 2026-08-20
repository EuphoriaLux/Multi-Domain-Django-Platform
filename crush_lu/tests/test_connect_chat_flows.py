"""
Tests for the Connect Cycle temp chat, venue picker, post-meeting
confirmation loop, and 1-click block (Epic 13 follow-up to Task 13.2 /
PR #886).

Builds on ``test_crush_connect_cycle_models.py`` (model-level lifecycle) and
follows ``test_connect_week_experience.py``'s setup pattern (event-verified
Cycle users, ``_login_eligible``, literal ``/en/crush-connect/...`` URLs —
testserver -> crush.lu is the pytest default, no ``pytest.mark.urls`` or
``HTTP_HOST`` override needed).
"""

import itertools
from datetime import timedelta

import pytest
from django.utils import timezone

from crush_lu.models import UserBlock
from crush_lu.models.crush_connect_cycle import (
    ConnectChatMessage,
    ConnectCoffeeDate,
    ConnectPairExclusion,
    ConnectReport,
    ConnectTemporaryChat,
    ConnectWeekSession,
)
from crush_lu.services.connect_chat import (
    CHAT_MESSAGE_MAX_LENGTH,
    accept_venue,
    block_chat_partner,
    cancel_venue,
    confirm_meeting,
    propose_venue,
    send_message,
    sync_chat_state,
)
from crush_lu.services.connect_cycle import (
    respond_to_weekly_request,
    send_weekly_request,
)
from crush_lu.tests.test_connect_week_experience import (
    WEEK_HOME_URL,
    WEEK_INBOX_URL,
    _make_cycle_user,
    _reviewable_session_with_card,
)
from crush_lu.tests.test_crush_connect import _login_eligible, _make_user
from hub.models import Location

CHATS_URL = "/en/crush-connect/week/chats/"

_open_chat_counter = itertools.count()


def _make_open_chat(me=None, target=None):
    """An ACTIVE ConnectTemporaryChat opened via a real mutual accept —
    exactly the hook #886's ``respond_to_weekly_request`` provides.

    Default usernames are unique per call (a counter suffix) so a test that
    needs two independent chats — e.g. comparing a blocked chat against a
    naturally-closed one — can call this twice without a username/email
    collision.
    """
    n = next(_open_chat_counter)
    me = me or _make_cycle_user(f"me{n}")
    target = target or _make_cycle_user(f"target{n}")
    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    updated = respond_to_weekly_request(req, accept=True)
    chat = ConnectTemporaryChat.objects.get(request=updated)
    return me, target, chat


def _make_location(**kwargs):
    kwargs.setdefault("name", "Café de Paris")
    kwargs.setdefault("address", "Place d'Armes 1")
    kwargs.setdefault("city", "Luxembourg")
    kwargs.setdefault("max_capacity", 50)
    kwargs.setdefault("partnership_stage", Location.PartnershipStage.ACTIVE)
    return Location.objects.create(**kwargs)


# ---------------------------------------------------------------------------
# Messages — service layer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_send_message_rolls_inactivity_window_forward():
    me, target, chat = _make_open_chat()
    ConnectTemporaryChat.objects.filter(pk=chat.pk).update(
        expires_at=timezone.now() + timedelta(hours=1)
    )
    chat.refresh_from_db()

    send_message(chat, me, "Hi! Coffee this week?")
    chat.refresh_from_db()

    assert chat.expires_at > timezone.now() + timedelta(days=6)


@pytest.mark.django_db
def test_send_message_rejects_non_participant():
    me, target, chat = _make_open_chat()
    stranger = _make_cycle_user("stranger")

    with pytest.raises(ValueError, match="not_participant"):
        send_message(chat, stranger, "hi")


@pytest.mark.django_db
def test_send_message_rejects_blank_body():
    me, target, chat = _make_open_chat()

    with pytest.raises(ValueError, match="empty_message"):
        send_message(chat, me, "   ")


@pytest.mark.django_db
def test_send_message_truncates_over_max_length():
    me, target, chat = _make_open_chat()

    msg = send_message(chat, me, "x" * (CHAT_MESSAGE_MAX_LENGTH + 500))

    assert len(msg.message) == CHAT_MESSAGE_MAX_LENGTH


@pytest.mark.django_db
def test_send_message_rejects_on_expired_chat():
    """The 7-day inactivity timer is enforced server-side: sending into a
    chat whose expires_at already passed must be refused, not silently
    accepted into a chat sync_chat_state is about to close anyway."""
    me, target, chat = _make_open_chat()
    ConnectTemporaryChat.objects.filter(pk=chat.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    chat.refresh_from_db()

    with pytest.raises(ValueError, match="chat_closed"):
        send_message(chat, me, "still here?")

    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.CLOSED
    assert chat.close_reason == ConnectTemporaryChat.CloseReason.INACTIVITY_TIMEOUT
    assert not ConnectChatMessage.objects.filter(chat=chat).exists()


@pytest.mark.django_db
def test_sync_chat_state_closes_on_block_from_any_surface():
    """A block placed from a completely different surface (not this chat's
    own block flow) must still close the chat on next read."""
    me, target, chat = _make_open_chat()
    UserBlock.objects.create(blocker=me, blocked=target)

    synced = sync_chat_state(chat)

    assert synced.status == ConnectTemporaryChat.Status.BLOCKED
    assert synced.close_reason == ConnectTemporaryChat.CloseReason.BLOCKED


# ---------------------------------------------------------------------------
# Venue picker — service layer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_propose_venue_creates_coffee_date_and_schedules_chat():
    me, target, chat = _make_open_chat()
    location = _make_location()
    tomorrow = timezone.localdate() + timedelta(days=1)

    coffee_date = propose_venue(
        chat,
        me,
        venue_location_id=location.pk,
        proposed_date=tomorrow.isoformat(),
        proposed_time_slot="18:30",
    )

    assert coffee_date.status == ConnectCoffeeDate.Status.PROPOSED
    assert coffee_date.venue_location_id == location.pk
    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.MEETING_SCHEDULED


@pytest.mark.django_db
def test_propose_venue_requires_a_venue():
    me, target, chat = _make_open_chat()

    with pytest.raises(ValueError, match="missing_venue"):
        propose_venue(
            chat,
            me,
            proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
        )


@pytest.mark.django_db
def test_propose_venue_custom_fallback():
    me, target, chat = _make_open_chat()

    coffee_date = propose_venue(
        chat,
        me,
        custom_venue_name="Our usual spot",
        custom_venue_address="1 Rue Test",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
    )

    assert coffee_date.venue_location is None
    assert coffee_date.custom_venue_name == "Our usual spot"


@pytest.mark.django_db
def test_accept_venue_rejects_self_accept():
    me, target, chat = _make_open_chat()
    coffee_date = propose_venue(
        chat,
        me,
        custom_venue_name="Spot",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
    )

    with pytest.raises(ValueError, match="cannot_self_accept"):
        accept_venue(coffee_date, me)


@pytest.mark.django_db
def test_accept_venue_by_counterpart():
    me, target, chat = _make_open_chat()
    coffee_date = propose_venue(
        chat,
        me,
        custom_venue_name="Spot",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
    )

    accept_venue(coffee_date, target)
    coffee_date.refresh_from_db()

    assert coffee_date.status == ConnectCoffeeDate.Status.ACCEPTED


@pytest.mark.django_db
def test_propose_venue_editing_an_accepted_plan_reschedules_and_clears_confirmations():
    me, target, chat = _make_open_chat()
    coffee_date = propose_venue(
        chat,
        me,
        custom_venue_name="Spot",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
    )
    accept_venue(coffee_date, target)
    coffee_date.confirm_by_user(me)

    propose_venue(
        chat,
        target,
        custom_venue_name="New spot",
        proposed_date=(timezone.localdate() + timedelta(days=2)).isoformat(),
    )
    coffee_date.refresh_from_db()

    assert coffee_date.status == ConnectCoffeeDate.Status.RESCHEDULED
    assert coffee_date.participant_1_confirmed_at is None
    assert coffee_date.participant_2_confirmed_at is None


@pytest.mark.django_db
def test_cancel_venue_blocked_once_confirmed_by_both():
    me, target, chat = _make_open_chat()
    coffee_date = propose_venue(
        chat,
        me,
        custom_venue_name="Spot",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
    )
    accept_venue(coffee_date, target)
    coffee_date.confirm_by_user(me)
    coffee_date.confirm_by_user(target)

    with pytest.raises(ValueError, match="already_confirmed"):
        cancel_venue(coffee_date, me)


# ---------------------------------------------------------------------------
# Post-meeting confirmation loop
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_confirm_meeting_requires_accepted_plan():
    me, target, chat = _make_open_chat()
    coffee_date = propose_venue(
        chat,
        me,
        custom_venue_name="Spot",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
    )  # still PROPOSED, never accepted

    with pytest.raises(ValueError, match="not_accepted"):
        confirm_meeting(chat, coffee_date, me)


@pytest.mark.django_db
def test_confirm_meeting_extends_chat_only_once_both_confirm():
    """Matches the model's actual behaviour (verified against
    test_crush_connect_cycle_models.py): the +3-day chat extension fires
    only once BOTH participants confirm — not on the first, "partial"
    confirmation."""
    me, target, chat = _make_open_chat()
    coffee_date = propose_venue(
        chat,
        me,
        custom_venue_name="Spot",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
    )
    accept_venue(coffee_date, target)

    confirm_meeting(chat, coffee_date, me)
    coffee_date.refresh_from_db()
    assert coffee_date.status == ConnectCoffeeDate.Status.ACCEPTED  # not yet both
    assert coffee_date.participant_1_confirmed_at is not None

    coffee_date, chat = confirm_meeting(chat, coffee_date, target)

    assert coffee_date.status == ConnectCoffeeDate.Status.CONFIRMED_BY_BOTH
    assert chat.status == ConnectTemporaryChat.Status.MEETING_CONFIRMED
    # +3 days from NOW (not from the original 7-day inactivity window, which
    # may have been further out than that if confirmation happens early).
    assert abs((chat.expires_at - timezone.now()) - timedelta(days=3)) < timedelta(
        minutes=1
    )


@pytest.mark.django_db
def test_confirm_meeting_rejects_repost_after_both_confirmed():
    """Guards the model against a re-POST exploit: calling confirm_by_user
    again after CONFIRMED_BY_BOTH would otherwise re-run the both-confirmed
    branch and push expires_at another 3 days out indefinitely."""
    me, target, chat = _make_open_chat()
    coffee_date = propose_venue(
        chat,
        me,
        custom_venue_name="Spot",
        proposed_date=(timezone.localdate() + timedelta(days=1)).isoformat(),
    )
    accept_venue(coffee_date, target)
    confirm_meeting(chat, coffee_date, me)
    coffee_date, chat = confirm_meeting(chat, coffee_date, target)
    expires_after_both = chat.expires_at

    with pytest.raises(ValueError, match="already_confirmed"):
        confirm_meeting(chat, coffee_date, me)

    chat.refresh_from_db()
    assert chat.expires_at == expires_after_both


# ---------------------------------------------------------------------------
# 1-click block
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_block_chat_partner_excludes_closes_and_blocks_platform_wide():
    me, target, chat = _make_open_chat()

    partner, report = block_chat_partner(chat, me)

    assert partner == target
    assert report is None
    assert ConnectPairExclusion.are_excluded(me, target)
    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.BLOCKED
    assert chat.close_reason == ConnectTemporaryChat.CloseReason.BLOCKED
    assert UserBlock.objects.filter(blocker=me, blocked=target).exists()


@pytest.mark.django_db
def test_block_chat_partner_optional_coach_escalation_creates_connect_report():
    me, target, chat = _make_open_chat()

    _partner, report = block_chat_partner(
        chat, me, escalate=True, details="Made me uncomfortable."
    )

    assert report is not None
    assert report.reporter == me
    assert report.reported_user == target
    assert report.status == ConnectReport.Status.PENDING
    assert "uncomfortable" in report.details


@pytest.mark.django_db
def test_block_chat_partner_rejects_non_participant():
    me, target, chat = _make_open_chat()
    stranger = _make_cycle_user("stranger")

    with pytest.raises(ValueError, match="not_participant"):
        block_chat_partner(chat, stranger)


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_chats_list_requires_login(client):
    resp = client.get(CHATS_URL)
    assert resp.status_code in (302, 301)


@pytest.mark.django_db
def test_accept_redirects_recipient_straight_into_the_chat(client):
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    _login_eligible(client, target)

    resp = client.post(
        f"/en/crush-connect/week/inbox/{req.pk}/respond/", {"action": "accept"}
    )

    assert resp.status_code in (302, 301)
    chat = ConnectTemporaryChat.objects.get(request=req)
    assert resp.url.endswith(f"/chats/{chat.pk}/")


@pytest.mark.django_db
def test_chat_detail_is_404_for_a_non_participant(client):
    me, target, chat = _make_open_chat()
    stranger = _make_cycle_user("stranger")
    _login_eligible(client, stranger)

    resp = client.get(f"/en/crush-connect/week/chats/{chat.pk}/")

    assert resp.status_code == 404


@pytest.mark.django_db
def test_chat_send_view_posts_message(client):
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    resp = client.post(
        f"/en/crush-connect/week/chats/{chat.pk}/send/", {"message": "Hello!"}
    )

    assert resp.status_code in (302, 301)
    assert ConnectChatMessage.objects.filter(
        chat=chat, sender=me, message="Hello!"
    ).exists()


@pytest.mark.django_db
def test_chat_venue_and_confirm_view_flow(client):
    me, target, chat = _make_open_chat()
    location = _make_location()
    _login_eligible(client, me)

    resp = client.post(
        f"/en/crush-connect/week/chats/{chat.pk}/venue/",
        {
            "venue_location_id": str(location.pk),
            "proposed_date": (timezone.localdate() + timedelta(days=1)).isoformat(),
            "proposed_time_slot": "18:30",
        },
    )
    assert resp.status_code in (302, 301)
    coffee_date = ConnectCoffeeDate.objects.get(chat=chat)
    assert coffee_date.status == ConnectCoffeeDate.Status.PROPOSED

    _login_eligible(client, target)
    resp = client.post(
        f"/en/crush-connect/week/chats/{chat.pk}/venue/respond/", {"action": "accept"}
    )
    assert resp.status_code in (302, 301)
    coffee_date.refresh_from_db()
    assert coffee_date.status == ConnectCoffeeDate.Status.ACCEPTED

    resp = client.post(f"/en/crush-connect/week/chats/{chat.pk}/confirm/")
    assert resp.status_code in (302, 301)
    coffee_date.refresh_from_db()
    assert coffee_date.participant_2_confirmed_at is not None

    _login_eligible(client, me)
    resp = client.post(f"/en/crush-connect/week/chats/{chat.pk}/confirm/")
    assert resp.status_code in (302, 301)
    coffee_date.refresh_from_db()
    assert coffee_date.status == ConnectCoffeeDate.Status.CONFIRMED_BY_BOTH


@pytest.mark.django_db
def test_chat_block_view_ends_chat_and_stops_further_sends(client):
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    resp = client.post(f"/en/crush-connect/week/chats/{chat.pk}/block/")

    assert resp.status_code in (302, 301)
    chat.refresh_from_db()
    assert chat.status == ConnectTemporaryChat.Status.BLOCKED
    assert ConnectPairExclusion.are_excluded(me, target)

    resp = client.post(
        f"/en/crush-connect/week/chats/{chat.pk}/send/", {"message": "still there?"}
    )
    assert resp.status_code in (302, 301)
    assert not ConnectChatMessage.objects.filter(
        chat=chat, message="still there?"
    ).exists()


@pytest.mark.django_db
def test_blocked_chat_reads_identically_to_a_naturally_closed_chat(client):
    """Silent-block contract (matches the platform-wide block copy and the
    weekly-request silent-decline discipline): the non-blocking participant
    must see the exact same "conversation has ended" state whether the chat
    closed from inactivity or because they were blocked — never a hint that
    they specifically were blocked. Also pins that the Safety Options panel
    (which names "block") disappears once a chat isn't open, for both."""
    me, target, blocked_chat = _make_open_chat()
    block_chat_partner(blocked_chat, me)  # me blocks target

    other_me, other_target, closed_chat = _make_open_chat()
    ConnectTemporaryChat.objects.filter(pk=closed_chat.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    _login_eligible(client, target)
    blocked_resp = client.get(f"/en/crush-connect/week/chats/{blocked_chat.pk}/")
    blocked_html = blocked_resp.content.decode()

    _login_eligible(client, other_target)
    closed_resp = client.get(f"/en/crush-connect/week/chats/{closed_chat.pk}/")
    closed_html = closed_resp.content.decode()

    assert blocked_resp.status_code == 200
    assert closed_resp.status_code == 200
    assert "This conversation has ended." in blocked_html
    assert "This conversation has ended." in closed_html
    # The Safety Options panel (which names "block") only renders for an
    # open chat — gone from both once the chat is terminal, so there's
    # nothing in the page that could tip off the non-blocking side.
    assert "Safety options" not in blocked_html
    assert "Safety options" not in closed_html
    assert "Block this member" not in blocked_html
    assert "Block this member" not in closed_html


@pytest.mark.django_db
def test_chats_list_shows_the_same_label_for_blocked_and_closed_chats(client):
    me, target, blocked_chat = _make_open_chat()
    block_chat_partner(blocked_chat, me)

    other_me, other_target, closed_chat = _make_open_chat()
    ConnectTemporaryChat.objects.filter(pk=closed_chat.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    _login_eligible(client, target)
    blocked_list_html = client.get(CHATS_URL).content.decode()

    _login_eligible(client, other_target)
    closed_list_html = client.get(CHATS_URL).content.decode()

    assert "Closed" in blocked_list_html
    assert "Closed" in closed_list_html
    assert "Ended" not in blocked_list_html
    assert "Ended" not in closed_list_html


# ---------------------------------------------------------------------------
# Discoverability: "Your chats" entry point back into an open chat
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_week_home_links_to_chats_when_one_is_open(client, settings):
    """Once the accept-redirect's tab is closed, connect_week_home is the
    event-verified member's only reachable path back into an open chat —
    week_review.html's "Open chat" link disappears once the session
    completes and a new cycle starts."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me, target, chat = _make_open_chat()
    # _make_open_chat's session went through the 24h review window (that's
    # how it got a completed card to send the request from), so it's still
    # REVIEW_OPEN and connect_week_home would redirect straight to the
    # review page — settle it so home renders normally for THIS assertion,
    # mirroring "last week's chat is still open, this week's cycle hasn't
    # started yet".
    ConnectWeekSession.objects.filter(user=me).update(
        status=ConnectWeekSession.Status.COMPLETED, is_review_open=False
    )
    _login_eligible(client, me)

    resp = client.get(WEEK_HOME_URL)

    assert "Your chats" in resp.content.decode()


@pytest.mark.django_db
def test_week_home_has_no_chats_link_without_an_open_chat(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("solo")
    _login_eligible(client, me)

    resp = client.get(WEEK_HOME_URL)

    assert "Your chats" not in resp.content.decode()


@pytest.mark.django_db
def test_week_inbox_links_to_chats_for_a_luxid_only_recipient(client, settings):
    """A LuxID-only recipient can accept and chat (see week_inbox's own
    docstring) but never has cycle_access_open, so connect_week_home's
    "Your chats" link is unreachable to them — the inbox must carry its own
    entry point back into an already-open chat."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me2")
    recipient = _make_user(username="luxid_only2", premium=False)
    session, card = _reviewable_session_with_card(me, recipient)
    req = send_weekly_request(session, me, recipient)
    respond_to_weekly_request(req, accept=True)
    _login_eligible(client, recipient)

    resp = client.get(WEEK_INBOX_URL)

    assert "Your chats" in resp.content.decode()
