"""
Tests for the 7-Day Connect Cycle experience (Epic 13 / Task 13.2):
daily card generation, day rollover/expiry, the 24h "Deine Connect-Woche"
review + compatibility highlight, the one-or-none weekly request, and the
recipient inbox.

Builds on the models/gating covered by ``test_crush_connect_cycle_models.py``
and reuses the ``CrushConnectMembership`` / gate-question fixtures from
``test_crush_connect.py`` (see that module's docstring for the cross-import
convention already used by the other ``test_crush_connect_*`` suites).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from crush_lu.models.crush_connect_cycle import (
    ConnectCycleCard,
    ConnectPairExclusion,
    ConnectTemporaryChat,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.services.connect_cycle import (
    can_send_weekly_request,
    get_cycle_eligible_pool,
    get_or_create_active_session,
    get_or_create_todays_cards,
    get_pending_inbox,
    record_card_answer,
    respond_to_weekly_request,
    send_weekly_request,
    sync_request_state,
    sync_session_state,
)
from crush_lu.tests.test_crush_connect import (
    CONNECT_TEASER_URL,
    _login_eligible,
    _make_user,
    _mark_attended,
    _set_gate_questions,
)

WEEK_HOME_URL = "/en/crush-connect/week/"
WEEK_REVIEW_URL = "/en/crush-connect/week/review/"
WEEK_INBOX_URL = "/en/crush-connect/week/inbox/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cycle_user(username, **kwargs):
    """An event-verified, non-Premium member — the beta's Cycle-eligible
    population. ``cycle_access_open`` deliberately widens beyond Premium
    (see ``connect_phase.cycle_access_open``'s docstring)."""
    kwargs.setdefault("premium", False)
    user = _make_user(username=username, **kwargs)
    _mark_attended(user)
    return user


def _seed_cycle_pool(me, n=5, gender="F"):
    out = []
    for i in range(n):
        u = _make_cycle_user(
            f"cyc_target_{i:02d}", gender=gender, preferred_genders=["M"]
        )
        _set_gate_questions(u)
        out.append(u)
    return out


def _reviewable_session_with_card(me, target):
    """A session already in its 24h review window, with one completed card
    for ``target`` — the state ``connect_week_request_send`` acts on."""
    session = ConnectWeekSession.objects.create(user=me)
    session.open_weekly_review()
    card = ConnectCycleCard.objects.create(
        session=session,
        day_number=1,
        card_index=1,
        target_user=target,
        generated_date=timezone.localdate(),
        is_completed=True,
        completed_at=timezone.now(),
    )
    return session, card


def _answer_all(card):
    membership = card.target_user.crush_connect_membership
    guesses = {gq.question_id: True for gq in membership.active_gate_questions}
    return record_card_answer(card, guesses)


# ---------------------------------------------------------------------------
# Eligible pool
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pool_empty_without_cycle_access(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    # LuxID only, no event attendance — in the candidate pool but not
    # cycle-eligible in beta (see ConnectPhaseGatingTests).
    me = _make_user(username="luxid_only", premium=False)
    assert list(get_cycle_eligible_pool(me)) == []


@pytest.mark.django_db
def test_pool_includes_event_verified_target_with_gate_questions(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target", gender="F")
    _set_gate_questions(target)
    assert target in get_cycle_eligible_pool(me)


@pytest.mark.django_db
def test_pool_excludes_target_without_gate_questions(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")  # never picked 3 gate questions
    assert target not in get_cycle_eligible_pool(me)


@pytest.mark.django_db
def test_pool_excludes_previously_carded_target(settings):
    """Cross-cycle freshness: a target already surfaced in ANY of the user's
    past sessions never reappears — enforced dynamically, not via a
    permanent ConnectPairExclusion (see the service module docstring)."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    _set_gate_questions(target)
    old_session = ConnectWeekSession.objects.create(user=me)
    ConnectCycleCard.objects.create(
        session=old_session,
        day_number=1,
        card_index=1,
        target_user=target,
        generated_date=timezone.localdate(),
    )
    assert target not in get_cycle_eligible_pool(me)


@pytest.mark.django_db
def test_pool_excludes_pair_exclusion(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    _set_gate_questions(target)
    ConnectPairExclusion.exclude_pair(
        me, target, reason=ConnectPairExclusion.Reason.REQUEST_DECLINED
    )
    assert target not in get_cycle_eligible_pool(me)


@pytest.mark.django_db
def test_pool_excludes_reverse_direction_pending_and_accepted_requesters(settings):
    """Cross-cycle freshness is bidirectional: if `sender` already carded and
    sent `me` a request, `me` must not later draw `sender` as a "fresh"
    stranger card in `me`'s own cycle — `me` never carded `sender`, so the
    forward `already_carded_ids` check alone would miss this."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    pending_sender = _make_cycle_user("pending_sender")
    accepted_sender = _make_cycle_user("accepted_sender")
    # Both must have gate questions, or they'd already be excluded from me's
    # pool by the GATE_QUESTION_COUNT filter regardless of this fix.
    _set_gate_questions(pending_sender)
    _set_gate_questions(accepted_sender)

    session1, _card1 = _reviewable_session_with_card(pending_sender, me)
    send_weekly_request(session1, pending_sender, me)

    session2, _card2 = _reviewable_session_with_card(accepted_sender, me)
    req2 = send_weekly_request(session2, accepted_sender, me)
    respond_to_weekly_request(req2, accept=True)

    pool = get_cycle_eligible_pool(me)
    assert pending_sender not in pool
    assert accepted_sender not in pool


# ---------------------------------------------------------------------------
# Session lifecycle + daily cards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_or_create_active_session_is_idempotent():
    me = _make_cycle_user("me")
    s1 = get_or_create_active_session(me)
    s2 = get_or_create_active_session(me)
    assert s1.pk == s2.pk


@pytest.mark.django_db
def test_todays_cards_generated_and_idempotent(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    _seed_cycle_pool(me, n=5)
    session = get_or_create_active_session(me)

    cards_1 = get_or_create_todays_cards(session)
    cards_2 = get_or_create_todays_cards(session)

    assert len(cards_1) == 3
    assert [c.pk for c in cards_1] == [c.pk for c in cards_2]


@pytest.mark.django_db
def test_todays_cards_fewer_when_pool_smaller_than_three(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    _seed_cycle_pool(me, n=2)
    session = get_or_create_active_session(me)
    cards = get_or_create_todays_cards(session)
    assert len(cards) == 2


@pytest.mark.django_db
def test_sync_session_state_advances_day_and_expires_missed_cards(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    _seed_cycle_pool(me, n=5)
    session = get_or_create_active_session(me)
    cards = get_or_create_todays_cards(session)
    assert session.current_day_number == 1

    # 2 full days elapse without answering — day 1's cards expire, unreachable.
    ConnectWeekSession.objects.filter(pk=session.pk).update(
        started_at=timezone.now() - timedelta(days=2)
    )
    session.refresh_from_db()
    session = sync_session_state(session)

    assert session.current_day_number == 3
    for card in cards:
        card.refresh_from_db()
        assert card.is_expired is True


@pytest.mark.django_db
def test_sync_session_state_opens_review_after_day_seven(settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    _seed_cycle_pool(me, n=3)
    session = get_or_create_active_session(me)
    cards = get_or_create_todays_cards(session)
    for card in cards:
        _answer_all(card)

    ConnectWeekSession.objects.filter(pk=session.pk).update(
        started_at=timezone.now() - timedelta(days=7)
    )
    session.refresh_from_db()
    session = sync_session_state(session)

    assert session.status == ConnectWeekSession.Status.REVIEW_OPEN
    assert session.is_review_active
    assert session.compatibility_highlight_user_id in [c.target_user_id for c in cards]


@pytest.mark.django_db
def test_sync_session_state_closes_review_after_24h():
    me = _make_cycle_user("me")
    session = ConnectWeekSession.objects.create(user=me)
    session.open_weekly_review()
    ConnectWeekSession.objects.filter(pk=session.pk).update(
        review_expires_at=timezone.now() - timedelta(minutes=1)
    )
    session.refresh_from_db()

    session = sync_session_state(session)
    assert session.status == ConnectWeekSession.Status.COMPLETED


@pytest.mark.django_db
def test_sync_session_state_is_idempotent_for_terminal_sessions():
    me = _make_cycle_user("me")
    session = ConnectWeekSession.objects.create(
        user=me, status=ConnectWeekSession.Status.COMPLETED
    )
    returned = sync_session_state(session)
    assert returned.status == ConnectWeekSession.Status.COMPLETED


# ---------------------------------------------------------------------------
# One-or-none weekly request
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_can_send_weekly_request_requires_review_open():
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session = ConnectWeekSession.objects.create(user=me)  # still ACTIVE
    ConnectCycleCard.objects.create(
        session=session,
        day_number=1,
        card_index=1,
        target_user=target,
        generated_date=timezone.localdate(),
        is_completed=True,
    )
    assert can_send_weekly_request(session, me, target) == (False, "review_closed")


@pytest.mark.django_db
def test_send_weekly_request_requires_completed_card():
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session = ConnectWeekSession.objects.create(user=me)
    session.open_weekly_review()
    ConnectCycleCard.objects.create(
        session=session,
        day_number=1,
        card_index=1,
        target_user=target,
        generated_date=timezone.localdate(),
        is_completed=False,
    )
    with pytest.raises(ValueError, match="not_in_review"):
        send_weekly_request(session, me, target)


@pytest.mark.django_db
def test_can_send_weekly_request_rejects_ineligible_recipient():
    """A completed card is an immutable snapshot — the target may have lost
    catalogue eligibility (here: coach-excluded) since it was generated.
    Mirrors can_send_spark's recipient re-check."""
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, _card = _reviewable_session_with_card(me, target)
    target.crush_connect_membership.excluded_by_coach = True
    target.crush_connect_membership.save(update_fields=["excluded_by_coach"])

    assert can_send_weekly_request(session, me, target) == (
        False,
        "recipient_unavailable",
    )
    with pytest.raises(ValueError, match="recipient_unavailable"):
        send_weekly_request(session, me, target)


@pytest.mark.django_db
def test_send_weekly_request_enforces_one_or_none():
    me = _make_cycle_user("me")
    t1 = _make_cycle_user("t1")
    t2 = _make_cycle_user("t2")
    session = ConnectWeekSession.objects.create(user=me)
    session.open_weekly_review()
    ConnectCycleCard.objects.create(
        session=session,
        day_number=1,
        card_index=1,
        target_user=t1,
        generated_date=timezone.localdate(),
        is_completed=True,
    )
    ConnectCycleCard.objects.create(
        session=session,
        day_number=1,
        card_index=2,
        target_user=t2,
        generated_date=timezone.localdate(),
        is_completed=True,
    )

    req = send_weekly_request(session, me, t1)
    assert req.status == ConnectWeeklyRequest.Status.PENDING

    with pytest.raises(ValueError, match="already_sent"):
        send_weekly_request(session, me, t2)


@pytest.mark.django_db
def test_respond_accept_creates_chat():
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)

    updated = respond_to_weekly_request(req, accept=True)

    assert updated.status == ConnectWeeklyRequest.Status.ACCEPTED
    chat = ConnectTemporaryChat.objects.get(request=req)
    assert {chat.participant_1_id, chat.participant_2_id} == {me.pk, target.pk}


@pytest.mark.django_db
def test_respond_accept_leaves_pending_when_requester_lost_eligibility():
    """An accept must NOT fall through to the decline branch when the
    requester lost eligibility since sending (coach exclusion here) — that
    would permanently exclude the pair over what's often a recoverable,
    transient state. Mirrors respond_to_spark's discipline."""
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    me.crush_connect_membership.excluded_by_coach = True
    me.crush_connect_membership.save(update_fields=["excluded_by_coach"])

    updated = respond_to_weekly_request(req, accept=True)

    assert updated.status == ConnectWeeklyRequest.Status.PENDING
    assert not ConnectTemporaryChat.objects.filter(request=req).exists()
    assert not ConnectPairExclusion.are_excluded(me, target)


@pytest.mark.django_db
def test_respond_decline_creates_permanent_pair_exclusion():
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)

    updated = respond_to_weekly_request(req, accept=False)

    assert updated.status == ConnectWeeklyRequest.Status.DECLINED
    assert ConnectPairExclusion.are_excluded(me, target)
    assert not ConnectTemporaryChat.objects.filter(request=req).exists()


@pytest.mark.django_db
def test_sync_request_state_expires_stale_pending_request():
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    ConnectWeeklyRequest.objects.filter(pk=req.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    req.refresh_from_db()

    updated = sync_request_state(req)

    assert updated.status == ConnectWeeklyRequest.Status.EXPIRED
    assert ConnectPairExclusion.are_excluded(me, target)


@pytest.mark.django_db
def test_get_pending_inbox_excludes_resolved_and_blocked():
    from crush_lu.models import UserBlock

    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)

    assert list(get_pending_inbox(target)) == [req]

    respond_to_weekly_request(req, accept=True)
    assert list(get_pending_inbox(target)) == []

    # A second, blocked sender's pending request never appears in the inbox.
    blocked_sender = _make_cycle_user("blocked_sender")
    session2, _card2 = _reviewable_session_with_card(blocked_sender, target)
    req2 = send_weekly_request(session2, blocked_sender, target)
    UserBlock.objects.create(blocker=target, blocked=blocked_sender)
    assert req2 not in get_pending_inbox(target)


@pytest.mark.django_db
def test_get_pending_inbox_excludes_ineligible_requester():
    """A pending request from a requester who has since lost catalogue
    eligibility (coach-excluded here) must not be offered for accept —
    mirrors is_sender_eligible filtering in crush_connect_sparks_received."""
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, _card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    assert list(get_pending_inbox(target)) == [req]

    me.crush_connect_membership.excluded_by_coach = True
    me.crush_connect_membership.save(update_fields=["excluded_by_coach"])

    assert list(get_pending_inbox(target)) == []


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_week_home_requires_login(client):
    resp = client.get(WEEK_HOME_URL)
    assert resp.status_code in (302, 301)


@pytest.mark.django_db
def test_week_home_redirects_luxid_only_candidate_to_teaser(client, settings):
    """A LuxID-only candidate has no Cycle access even in beta —
    cycle_access_open widens to event-verified members, not to everyone in
    the candidate pool."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="luxid_only", premium=False)
    _login_eligible(client, me)

    resp = client.get(WEEK_HOME_URL)

    assert resp.status_code in (302, 301)
    assert CONNECT_TEASER_URL in resp.url


@pytest.mark.django_db
def test_week_home_renders_cards_for_event_verified_member(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    _seed_cycle_pool(me, n=3)
    _login_eligible(client, me)

    resp = client.get(WEEK_HOME_URL)

    assert resp.status_code == 200
    assert ConnectWeekSession.objects.filter(user=me).exists()
    assert ConnectCycleCard.objects.filter(session__user=me).count() == 3


@pytest.mark.django_db
def test_week_card_answer_view_records_and_redirects(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    _seed_cycle_pool(me, n=3)
    _login_eligible(client, me)
    client.get(WEEK_HOME_URL)  # triggers session + card generation
    card = ConnectCycleCard.objects.filter(session__user=me).first()
    membership = card.target_user.crush_connect_membership
    data = {f"answer_{gq.question_id}": "yes" for gq in membership.active_gate_questions}

    resp = client.post(f"/en/crush-connect/week/card/{card.pk}/answer/", data)

    assert resp.status_code in (302, 301)
    card.refresh_from_db()
    assert card.is_completed is True


@pytest.mark.django_db
def test_week_review_redirects_home_while_active(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    _seed_cycle_pool(me, n=3)
    _login_eligible(client, me)
    client.get(WEEK_HOME_URL)

    resp = client.get(WEEK_REVIEW_URL)

    assert resp.status_code in (302, 301)
    assert WEEK_HOME_URL in resp.url


@pytest.mark.django_db
def test_week_review_renders_highlight_after_day_seven(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    _seed_cycle_pool(me, n=3)
    session = get_or_create_active_session(me)
    for card in get_or_create_todays_cards(session):
        _answer_all(card)
    ConnectWeekSession.objects.filter(pk=session.pk).update(
        started_at=timezone.now() - timedelta(days=7)
    )
    _login_eligible(client, me)

    resp = client.get(WEEK_REVIEW_URL)

    assert resp.status_code == 200
    assert "Passt besonders gut zu dir" in resp.content.decode()


@pytest.mark.django_db
def test_week_review_shows_stored_guess_not_targets_current_questions(client, settings):
    """The review must render the question text the member actually
    answered (from the card's stored answers_json), not the target's LIVE
    gate questions — those may have been re-picked since. Also verifies the
    guess (Yes/No) is shown, and the review no longer needs the target's
    live CrushConnectMembership.active_gate_questions at all."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    original_questions = _set_gate_questions(target, answers=[True, True, True])
    session, card = _reviewable_session_with_card(me, target)
    record_card_answer(card, {q.pk: True for q in original_questions})

    # Target re-picks their 3 gate questions after being answered.
    from crush_lu.models import ConnectQuestion, MemberGateQuestion

    replacement_questions = list(
        ConnectQuestion.objects.filter(is_active=True)
        .exclude(pk__in=[q.pk for q in original_questions])[:3]
    )
    assert len(replacement_questions) == 3, "fixture needs >=6 active questions"
    membership = target.crush_connect_membership
    membership.gate_questions.all().delete()
    for position, q in enumerate(replacement_questions, start=1):
        MemberGateQuestion.objects.create(
            membership=membership, question=q, position=position, owner_answer=True
        )

    _login_eligible(client, me)
    resp = client.get(WEEK_REVIEW_URL)
    body = resp.content.decode()

    assert resp.status_code == 200
    for q in original_questions:
        assert q.text in body
    for q in replacement_questions:
        assert q.text not in body


@pytest.mark.django_db
def test_week_review_view_syncs_stale_sent_request(client, settings):
    """The requester's own review page is the one page they reliably visit —
    it must flip a stale PENDING request to EXPIRED (and write the
    ConnectPairExclusion) rather than showing "waiting" forever."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    ConnectWeeklyRequest.objects.filter(pk=req.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    _login_eligible(client, me)

    resp = client.get(WEEK_REVIEW_URL)

    assert resp.status_code == 200
    req.refresh_from_db()
    assert req.status == ConnectWeeklyRequest.Status.EXPIRED
    assert ConnectPairExclusion.are_excluded(me, target)
    assert "expired without a response" in resp.content.decode()


@pytest.mark.django_db
def test_week_request_send_view_creates_request(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, card = _reviewable_session_with_card(me, target)
    _login_eligible(client, me)

    resp = client.post(f"/en/crush-connect/week/review/{card.pk}/request/")

    assert resp.status_code in (302, 301)
    assert ConnectWeeklyRequest.objects.filter(session=session, recipient=target).exists()


@pytest.mark.django_db
def test_week_inbox_reachable_by_luxid_only_recipient(client, settings):
    """Correction: the inbox is NOT gated by cycle_access_open — the trust
    table lets a LuxID-only member accept and chat even though they can't
    browse or send from the Cycle themselves."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    recipient = _make_user(username="luxid_only", premium=False)
    session, card = _reviewable_session_with_card(me, recipient)
    send_weekly_request(session, me, recipient)

    _login_eligible(client, recipient)
    resp = client.get(WEEK_INBOX_URL)

    assert resp.status_code == 200
    assert me.first_name in resp.content.decode()


@pytest.mark.django_db
def test_week_request_respond_accept_view(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    _login_eligible(client, target)

    resp = client.post(
        f"/en/crush-connect/week/inbox/{req.pk}/respond/", {"action": "accept"}
    )

    assert resp.status_code in (302, 301)
    req.refresh_from_db()
    assert req.status == ConnectWeeklyRequest.Status.ACCEPTED


@pytest.mark.django_db
def test_week_request_respond_gated_by_candidate_access_open(client, settings):
    """connect_week_request_respond must check candidate_access_open() like
    connect_week_inbox does (its docstring claims the same gate) — not just
    is_catalogue_eligible, which alone says nothing about whether the phase
    is even open yet."""
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_cycle_user("me")
    target = _make_cycle_user("target")
    session, card = _reviewable_session_with_card(me, target)
    req = send_weekly_request(session, me, target)
    _login_eligible(client, target)

    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    settings.CRUSH_CONNECT_LAUNCHED = False

    resp = client.post(
        f"/en/crush-connect/week/inbox/{req.pk}/respond/", {"action": "accept"}
    )

    assert resp.status_code in (302, 301)
    assert CONNECT_TEASER_URL in resp.url
    req.refresh_from_db()
    assert req.status == ConnectWeeklyRequest.Status.PENDING
