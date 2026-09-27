"""
UX Wave 3 · WP12 "connect-moments" — findings 6-03, 6-06, 6-08.

Follows ``test_connect_week_experience.py`` / ``test_connect_chat_flows.py``'s
setup pattern (event-verified Cycle users, ``_login_eligible``, literal
``/en/crush-connect/...`` URLs — testserver -> crush.lu is the pytest
default, no ``HTTP_HOST`` override needed). Every test below fails on
``origin/main`` (09592a63) before this branch's changes.
"""

import pytest
from django.utils import timezone

from crush_lu.models.crush_connect_cycle import ConnectChatMessage, ConnectWeekSession
from crush_lu.services.connect_cycle import CYCLE_LENGTH_DAYS, week_timeline_state
from crush_lu.tests.test_connect_chat_flows import _make_open_chat
from crush_lu.tests.test_connect_week_experience import (
    WEEK_HOME_URL,
    WEEK_REVIEW_URL,
    _answer_all,
    _make_cycle_user,
    _reviewable_session_with_card,
    _seed_cycle_pool,
)
from crush_lu.tests.test_crush_connect import _login_eligible


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()


# ---------------------------------------------------------------------------
# 6-03 — week_timeline_state()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_timeline_state_active_session_is_step_1_discover():
    me = _make_cycle_user("tl_active")
    session = ConnectWeekSession.objects.create(user=me)

    state = week_timeline_state(session)

    assert state["step"] == 1
    assert state["next_kind"] == "opens"
    assert state["next_at"] == (
        timezone.localtime(session.started_at).date()
        + __import__("datetime").timedelta(days=CYCLE_LENGTH_DAYS)
    )


@pytest.mark.django_db
def test_timeline_state_review_open_no_request_is_step_2():
    me = _make_cycle_user("tl_review")
    session = ConnectWeekSession.objects.create(user=me)
    session.open_weekly_review()

    state = week_timeline_state(session)

    assert state["step"] == 2
    assert state["next_kind"] == "closes"
    assert state["next_at"] == session.review_expires_at


@pytest.mark.django_db
def test_timeline_state_pending_request_is_step_3_waiting():
    me = _make_cycle_user("tl_pending")
    target = _make_cycle_user("tl_pending_target")
    from crush_lu.services.connect_cycle import send_weekly_request

    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)

    state = week_timeline_state(session, sent_request=req)

    assert state["step"] == 3
    assert state["next_kind"] == "waiting"
    assert state["next_at"] == req.expires_at


@pytest.mark.django_db
def test_timeline_state_accepted_request_is_step_4_chat():
    me, target, _chat = _make_open_chat()
    session = ConnectWeekSession.objects.filter(user=me).order_by("-started_at").first()
    sent_request = session.weekly_requests.first()

    state = week_timeline_state(session, sent_request=sent_request)

    assert state["step"] == 4
    assert state["next_at"] is None


@pytest.mark.django_db
def test_timeline_state_coffee_plan_advances_to_step_5():
    """Once a request is accepted AND a coffee plan exists on the opened
    chat, the timeline must advance past "Chat" — otherwise "Coffee" (the
    5th step the template already renders) can never be reached."""
    from crush_lu.services.connect_chat import propose_venue

    me, target, chat = _make_open_chat()
    session = ConnectWeekSession.objects.filter(user=me).order_by("-started_at").first()
    sent_request = session.weekly_requests.first()
    propose_venue(
        chat,
        me,
        venue_location_id=None,
        custom_venue_name="Café de Paris",
        proposed_date=(
            timezone.localdate() + __import__("datetime").timedelta(days=1)
        ).isoformat(),
        proposed_time_slot="Evening",
    )

    state = week_timeline_state(session, sent_request=sent_request)

    assert state["step"] == 5
    assert state["next_at"] is None


@pytest.mark.django_db
def test_timeline_state_cancelled_coffee_plan_stays_step_4():
    from crush_lu.models.crush_connect_cycle import ConnectCoffeeDate
    from crush_lu.services.connect_chat import propose_venue

    me, target, chat = _make_open_chat()
    session = ConnectWeekSession.objects.filter(user=me).order_by("-started_at").first()
    sent_request = session.weekly_requests.first()
    coffee_date = propose_venue(
        chat,
        me,
        venue_location_id=None,
        custom_venue_name="Café de Paris",
        proposed_date=(
            timezone.localdate() + __import__("datetime").timedelta(days=1)
        ).isoformat(),
        proposed_time_slot="Evening",
    )
    coffee_date.status = ConnectCoffeeDate.Status.CANCELLED
    coffee_date.save(update_fields=["status"])

    state = week_timeline_state(session, sent_request=sent_request)

    assert state["step"] == 4


@pytest.mark.django_db
def test_timeline_state_completed_session_is_none_not_step_1():
    """A COMPLETED session must not fall through to the ACTIVE branch: that
    would report "Review opens <date>" with a date already in the past
    (started_at is old and the review window has long closed). There is no
    single correct step to report instead (a session completes on its
    review-window timeout whether or not a request was ever sent), so the
    timeline is hidden entirely — the caller/template already guard on a
    falsy ``timeline``."""
    me = _make_cycle_user("tl_completed")
    session = ConnectWeekSession.objects.create(
        user=me,
        started_at=timezone.now() - __import__("datetime").timedelta(days=10),
        status=ConnectWeekSession.Status.COMPLETED,
    )

    assert week_timeline_state(session) is None


@pytest.mark.django_db
def test_timeline_state_expired_session_is_none_not_step_1():
    me = _make_cycle_user("tl_expired")
    session = ConnectWeekSession.objects.create(
        user=me,
        started_at=timezone.now() - __import__("datetime").timedelta(days=10),
        status=ConnectWeekSession.Status.EXPIRED,
    )

    assert week_timeline_state(session) is None


@pytest.mark.django_db
def test_hub_omits_timeline_for_a_completed_session(client, settings):
    """End-to-end: the hub card renders nothing for the timeline block once
    the member's latest session has completed, instead of the misleading
    'Review opens <past date>' regression this finding was about."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("tl_hub_completed")
    ConnectWeekSession.objects.create(
        user=me,
        started_at=timezone.now() - __import__("datetime").timedelta(days=10),
        status=ConnectWeekSession.Status.COMPLETED,
        completed_at=timezone.now() - __import__("datetime").timedelta(days=9),
    )
    _login_eligible(client, me)

    body = client.get("/en/crush-connect/home/").content.decode()

    assert "connect-week-timeline" not in body


# ---------------------------------------------------------------------------
# 6-03 — week_home renders the timeline and the private-guesses sentence
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_week_home_renders_connect_week_timeline(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("tl_home")
    _seed_cycle_pool(me, n=3)
    _login_eligible(client, me)

    body = client.get(WEEK_HOME_URL).content.decode()

    assert "connect-week-timeline" in body
    assert "Discover" in body
    assert "Coffee" in body


@pytest.mark.django_db
def test_daily_card_explains_guesses_stay_private(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("tl_privacy")
    _seed_cycle_pool(me, n=3)
    _login_eligible(client, me)

    body = client.get(WEEK_HOME_URL).content.decode()

    assert "Your guesses stay private" in body


# ---------------------------------------------------------------------------
# 6-06 — review grid: always-visible photos, single confirm dialog
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_review_card_photo_is_not_behind_a_details_disclosure(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("rv_photo")
    target = _make_cycle_user("rv_photo_target")
    session, card = _reviewable_session_with_card(me, target)
    _answer_all(card)
    _login_eligible(client, me)

    body = client.get(WEEK_REVIEW_URL).content.decode()

    # The card is an <article>, not a <details> — the photo renders
    # unconditionally instead of behind a <summary> disclosure.
    assert "<article" in body
    assert 'x-data="connectReviewChoice"' in body


@pytest.mark.django_db
def test_review_card_answers_wrap_instead_of_truncating(client, settings):
    """A single truncated line reduces a typical gate-question to an
    ellipsis on this card's narrow half-width grid cell, so the member can
    no longer tell which question their displayed Yes/No answer belongs
    to — line-clamp (2 lines) plus a title= fallback replaces it."""
    from crush_lu.tests.test_connect_week_experience import _set_gate_questions

    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("rv_wrap")
    target = _make_cycle_user("rv_wrap_target")
    _set_gate_questions(target)
    session, card = _reviewable_session_with_card(me, target)
    _answer_all(card)
    _login_eligible(client, me)

    body = client.get(WEEK_REVIEW_URL).content.decode()

    assert '<li class="line-clamp-2"' in body
    assert 'class="truncate">' not in body.split("<article", 1)[1]


@pytest.mark.django_db
def test_review_choose_dialog_has_a_confirm_message_fallback(client, settings):
    """Some WebViews lack ``HTMLDialogElement.showModal``: openDialog() then
    falls back to the global confirm helper (window.crushConfirm), which
    needs the question text as a data attribute since it can't read it out
    of the (never-opened) native <dialog>. Without this attribute a member
    on such a browser could never send the weekly request at all."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("rv_fallback")
    target = _make_cycle_user("rv_fallback_target")
    session, card = _reviewable_session_with_card(me, target)
    _answer_all(card)
    _login_eligible(client, me)

    body = client.get(WEEK_REVIEW_URL).content.decode()

    assert "data-confirm-message=" in body
    assert "Send your one weekly request to" in body


@pytest.mark.django_db
def test_review_choose_dialog_renders_once_per_card_no_duplicate_suggested_badge(
    client, settings
):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("rv_dup")
    target = _make_cycle_user("rv_dup_target")
    session, card = _reviewable_session_with_card(me, target)
    _answer_all(card)
    session.compatibility_highlight_user = target
    session.save(update_fields=["compatibility_highlight_user"])
    _login_eligible(client, me)

    body = client.get(WEEK_REVIEW_URL).content.decode()

    # "Suggested" badge text appears exactly once per card now (the photo
    # overlay only) instead of also duplicated in a <summary> line.
    assert body.count("Suggested") == 1


# ---------------------------------------------------------------------------
# 6-08 — chat_detail: expiry chip, hidden older button, full-height class
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_chat_detail_shows_expiry_chip_in_header(client):
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()

    assert "connect-chat-expiry-chip" in body
    assert "Closes" in body


@pytest.mark.django_db
def test_chat_send_response_refreshes_the_expiry_label(client):
    """Every send rolls chat.expires_at 7 days forward — the send response
    must carry the refreshed label, or the header chip (which only reads
    the page-load value) keeps showing the deadline from before this send,
    eventually one already in the past."""
    from datetime import timedelta

    from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat

    me, target, chat = _make_open_chat()
    ConnectTemporaryChat.objects.filter(pk=chat.pk).update(
        expires_at=timezone.now() + timedelta(hours=1)
    )
    _login_eligible(client, me)

    resp = client.post(
        f"/en/crush-connect/week/chats/{chat.pk}/send/",
        {"message": "Hello!"},
        HTTP_ACCEPT="application/json",
    )
    data = resp.json()
    chat.refresh_from_db()

    assert data["closes_label"]
    assert "Closes" in data["closes_label"]
    # The label reflects the just-extended (~7 day) deadline, not the ~1h
    # one the chat had immediately before this send.
    assert (chat.expires_at - timezone.now()) > timedelta(days=6)
    assert data["expires_at"] == chat.expires_at.isoformat()


@pytest.mark.django_db
def test_chat_poll_response_refreshes_the_expiry_label(client):
    """The OTHER participant's sends also roll expires_at forward, so the
    poll (not just this participant's own send) must reflect the current
    deadline too — otherwise only the sender's own next send would ever
    refresh the chip, and the recipient would see a stale one."""
    from datetime import timedelta

    from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat
    from crush_lu.services.connect_chat import send_message

    me, target, chat = _make_open_chat()
    send_message(chat, target, "Hi there")
    chat.refresh_from_db()
    stale_label_deadline = chat.expires_at
    # Simulate the OTHER participant sending again after this page's own load.
    ConnectTemporaryChat.objects.filter(pk=chat.pk).update(
        expires_at=timezone.now() + timedelta(days=6, hours=23)
    )
    _login_eligible(client, me)

    resp = client.get(f"/en/crush-connect/week/chats/{chat.pk}/messages/")
    data = resp.json()
    chat.refresh_from_db()

    assert data["closes_label"]
    assert chat.expires_at != stale_label_deadline
    assert data["expires_at"] == chat.expires_at.isoformat()


@pytest.mark.django_db
def test_chat_detail_hides_older_button_under_50_messages(client):
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()

    assert "data-older hidden" in body


@pytest.mark.django_db
def test_chat_detail_hides_older_button_at_exactly_50_messages(client):
    """Exactly 50 total messages fills the page-load cap but leaves nothing
    older to load — the button must stay hidden (off-by-one regression)."""
    me, target, chat = _make_open_chat()
    ConnectChatMessage.objects.bulk_create(
        [
            ConnectChatMessage(chat=chat, sender=me, message=f"msg {i}")
            for i in range(50)
        ]
    )
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()

    assert "data-older hidden" in body


@pytest.mark.django_db
def test_chat_detail_sets_full_height_body_class(client):
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()

    assert '<body class="connect-chat"' in body


@pytest.mark.django_db
def test_chat_detail_hides_connect_subnav(client):
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()

    assert "connect-local-nav" not in body


def test_connect_thread_height_is_a_bounded_clamp_not_a_fixed_offset():
    """A static ``calc(100dvh - Npx)`` assumes constant chrome above the
    thread, but the pinned coffee section (or the full venue-picker form
    when no plan exists yet) varies in height — a guessed offset either
    wastes space or pushes the composer below the fold.

    A ``flex:1; min-height:0`` chain through every ancestor (main, the
    connect-shell, the page's own .connect-chat wrapper, the thread's
    wrap) was tried and reverted: when the venue-picker form alone is
    taller than the viewport, browsers do not reliably grow the page's
    own scrollable area to match a flex child's unmet ``min-height``
    floor — verified in the mobile emulator, the composer painted
    outside its flex box, invisible and unreachable, not merely below a
    scrollable fold. The thread instead keeps an always-bounded height
    clamp regardless of what's above it, so the page around it stays in
    normal flow and can scroll like any other page."""
    from pathlib import Path

    import crush_lu

    css_path = (
        Path(crush_lu.__file__).resolve().parent
        / "static"
        / "crush_lu"
        / "css"
        / "connect-mobile.css"
    )
    css = css_path.read_text(encoding="utf-8")

    assert "calc(100dvh" not in css
    assert "body.connect-chat #main-content" not in css
    assert "body.connect-chat .connect-thread { height: clamp(" in css


@pytest.mark.django_db
def test_venue_picker_uses_text_input_not_time(client):
    """A ``type="time"`` input can only hold HH:MM — it silently blanks any
    value it can't parse, so a free-form ``proposed_time_slot`` like
    "Evening" (the model explicitly allows this) would be erased the moment
    the "Change the plan" form is rendered and re-saved."""
    me, target, chat = _make_open_chat()
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()

    assert 'name="proposed_time_slot"' in body
    assert 'type="time" name="proposed_time_slot"' not in body
    assert 'type="text" name="proposed_time_slot"' in body


@pytest.mark.django_db
def test_venue_picker_preserves_free_form_time_on_edit(client):
    from crush_lu.services.connect_chat import propose_venue

    me, target, chat = _make_open_chat()
    propose_venue(
        chat,
        me,
        venue_location_id=None,
        custom_venue_name="Café de Paris",
        proposed_date=(
            timezone.localdate() + __import__("datetime").timedelta(days=1)
        ).isoformat(),
        proposed_time_slot="Evening",
    )
    _login_eligible(client, me)

    body = client.get(f"/en/crush-connect/week/chats/{chat.pk}/").content.decode()

    # The old type="time" input would render this as an empty value instead.
    assert 'value="Evening"' in body
