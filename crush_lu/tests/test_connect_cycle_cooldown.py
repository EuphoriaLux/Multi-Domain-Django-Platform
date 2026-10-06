"""Rolling eligibility with immutable card/request history and permanent safety."""

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.utils import timezone

from crush_lu.models.crush_connect_cycle import (
    ConnectCycleCard,
    ConnectPairExclusion,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.services.connect_cycle import (
    can_send_weekly_request,
    get_cycle_eligible_pool,
    get_or_create_todays_cards,
    respond_to_weekly_request,
    send_weekly_request,
    sync_request_state,
)
from crush_lu.tests.test_crush_connect import _set_gate_questions
from crush_lu.tests.test_connect_week_experience import (
    _make_cycle_user,
    _reviewable_session_with_card,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def pair(settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    viewer = _make_cycle_user("cooldown_viewer")
    target = _make_cycle_user("cooldown_target", gender="F")
    _set_gate_questions(viewer)
    _set_gate_questions(target)
    return viewer, target


def _old_card(viewer, target, **kwargs):
    session = ConnectWeekSession.objects.create(
        user=viewer, status=ConnectWeekSession.Status.COMPLETED
    )
    return ConnectCycleCard.objects.create(
        session=session,
        target_user=target,
        day_number=1,
        card_index=1,
        generated_date=timezone.localdate() - timedelta(days=40),
        **kwargs,
    )


def _expired_request(viewer, target, expires_at):
    session = ConnectWeekSession.objects.create(
        user=viewer, status=ConnectWeekSession.Status.COMPLETED
    )
    request = ConnectWeeklyRequest.objects.create(
        session=session,
        requester=viewer,
        recipient=target,
        status=ConnectWeeklyRequest.Status.EXPIRED,
        expires_at=expires_at,
    )
    ConnectWeeklyRequest.objects.filter(pk=request.pk).update(
        sent_at=expires_at - timedelta(hours=24)
    )
    exclusion, _ = ConnectPairExclusion.exclude_pair(
        viewer, target, ConnectPairExclusion.Reason.REQUEST_EXPIRED
    )
    return request, exclusion


@pytest.mark.parametrize(
    "completed,expired", [(False, False), (True, False), (False, True)]
)
@pytest.mark.parametrize("offset,eligible", [(-1, False), (0, True), (1, True)])
def test_card_cooldown_exact_boundary(
    pair, monkeypatch, completed, expired, offset, eligible
):
    viewer, target = pair
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    card = _old_card(
        viewer,
        target,
        generated_at=now - timedelta(days=30, microseconds=offset),
        is_completed=completed,
        is_expired=expired,
    )
    assert (target in get_cycle_eligible_pool(viewer)) is eligible
    card.refresh_from_db()
    assert card.is_completed is completed
    assert card.is_expired is expired
    assert not ConnectPairExclusion.objects.exists()


@pytest.mark.parametrize("status", ["active", "review_open"])
def test_current_session_never_repeats_old_target(pair, status):
    viewer, target = pair
    card = _old_card(viewer, target, generated_at=timezone.now() - timedelta(days=60))
    ConnectWeekSession.objects.filter(pk=card.session_id).update(status=status)
    assert target not in get_cycle_eligible_pool(viewer)
    if status == "active":
        session = card.session
        session.status = status
        session.current_day_number = 2
        assert get_or_create_todays_cards(session) == []
    assert card.session.cards.count() == 1


def test_latest_presentation_restarts_cooldown(pair):
    viewer, target = pair
    _old_card(viewer, target, generated_at=timezone.now() - timedelta(days=60))
    assert target in get_cycle_eligible_pool(viewer)
    _old_card(viewer, target, generated_at=timezone.now() - timedelta(days=1))
    assert target not in get_cycle_eligible_pool(viewer)
    assert ConnectCycleCard.objects.count() == 2


@pytest.mark.parametrize("local_hour", [0, 23])
@pytest.mark.parametrize("month", [4, 11])
def test_legacy_card_excludes_entire_cutoff_date(pair, monkeypatch, local_hour, month):
    viewer, target = pair
    # Across both Europe/Luxembourg DST transitions, using elapsed time.
    now = datetime(2026, month, 3, local_hour, 30, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(timezone, "now", lambda: now)
    for user in pair:
        type(user).objects.filter(pk=user.pk).update(last_login=now)
    cutoff_date = timezone.localdate(now - timedelta(days=30))
    card = _old_card(viewer, target, generated_at=None)
    ConnectCycleCard.objects.filter(pk=card.pk).update(generated_date=cutoff_date)
    assert target not in get_cycle_eligible_pool(viewer)
    ConnectCycleCard.objects.filter(pk=card.pk).update(
        generated_date=cutoff_date - timedelta(days=1)
    )
    assert target in get_cycle_eligible_pool(viewer)
    card.refresh_from_db()
    assert card.generated_at is None


def test_new_card_has_assignment_timestamp_and_history_survives(pair):
    viewer, target = pair
    old = _old_card(viewer, target, generated_at=timezone.now() - timedelta(days=40))
    session = ConnectWeekSession.objects.create(user=viewer)
    before = timezone.now()
    cards = get_or_create_todays_cards(session)
    after = timezone.now()
    assert len(cards) == 1
    assert cards[0].target_user == target
    assert before <= cards[0].generated_at <= after
    assert get_or_create_todays_cards(session)[0].pk == cards[0].pk
    assert ConnectCycleCard.objects.filter(pk=old.pk).exists()


@pytest.mark.parametrize("offset,excluded", [(-1, True), (0, False), (1, False)])
def test_expired_request_cooldown_exact_boundary_and_symmetry(
    pair, monkeypatch, offset, excluded
):
    viewer, target = pair
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    request, exclusion = _expired_request(
        viewer, target, now - timedelta(days=30, microseconds=offset)
    )
    for a, b in [(viewer, target), (target, viewer)]:
        assert ConnectPairExclusion.are_excluded(a, b) is excluded
        assert (b not in get_cycle_eligible_pool(a)) is excluded
    assert ConnectWeeklyRequest.objects.filter(pk=request.pk).exists()
    assert ConnectPairExclusion.objects.filter(pk=exclusion.pk).exists()


def test_late_lazy_sync_uses_expiry_deadline_not_sync_time(pair):
    viewer, target = pair
    session = ConnectWeekSession.objects.create(user=viewer, status="completed")
    request = ConnectWeeklyRequest.objects.create(
        session=session,
        requester=viewer,
        recipient=target,
        expires_at=timezone.now() - timedelta(days=31),
    )
    ConnectWeeklyRequest.objects.filter(pk=request.pk).update(
        sent_at=request.expires_at - timedelta(hours=24)
    )
    sync_request_state(request)
    assert request.status == ConnectWeeklyRequest.Status.EXPIRED
    assert not ConnectPairExclusion.are_excluded(viewer, target)
    assert target in get_cycle_eligible_pool(viewer)
    assert ConnectPairExclusion.objects.count() == 1


def test_repeated_expiry_uses_latest_deadline(pair):
    viewer, target = pair
    _, exclusion = _expired_request(viewer, target, timezone.now() - timedelta(days=60))
    recent, same = _expired_request(viewer, target, timezone.now() - timedelta(days=1))
    assert same.pk == exclusion.pk
    assert ConnectPairExclusion.are_excluded(viewer, target)
    ConnectWeeklyRequest.objects.filter(pk=recent.pk).update(
        expires_at=timezone.now() - timedelta(days=31),
        sent_at=timezone.now() - timedelta(days=32),
    )
    assert not ConnectPairExclusion.are_excluded(viewer, target)


@pytest.mark.parametrize(
    "reason",
    [
        "request_declined",
        "member_blocked",
        "coach_excluded",
        "cycle_completed",
        "legacy_unknown",
    ],
)
@pytest.mark.parametrize("expiry_first", [True, False])
def test_permanent_exclusion_dominates_expiry_in_either_order(
    pair, reason, expiry_first
):
    viewer, target = pair
    if expiry_first:
        _, exclusion = _expired_request(
            viewer, target, timezone.now() - timedelta(days=60)
        )
        ConnectPairExclusion.exclude_pair(target, viewer, reason)
    else:
        exclusion, _ = ConnectPairExclusion.exclude_pair(viewer, target, reason)
        _expired_request(target, viewer, timezone.now() - timedelta(days=60))
    exclusion.refresh_from_db()
    assert exclusion.reason == reason
    assert ConnectPairExclusion.are_excluded(target, viewer)
    assert target not in get_cycle_eligible_pool(viewer)
    assert viewer not in get_cycle_eligible_pool(target)
    assert ConnectPairExclusion.objects.count() == 1


def test_historical_decline_hidden_by_expiry_reason_remains_permanent(pair):
    viewer, target = pair
    _expired_request(viewer, target, timezone.now() - timedelta(days=60))
    session = ConnectWeekSession.objects.create(user=target, status="completed")
    ConnectWeeklyRequest.objects.create(
        session=session, requester=target, recipient=viewer, status="declined"
    )
    assert ConnectPairExclusion.are_excluded(viewer, target)
    assert target not in get_cycle_eligible_pool(viewer)


def test_orphan_expiry_exclusion_fails_closed(pair):
    viewer, target = pair
    exclusion, _ = ConnectPairExclusion.exclude_pair(viewer, target, "request_expired")
    ConnectPairExclusion.objects.filter(pk=exclusion.pk).update(
        created_at=timezone.now() - timedelta(days=60)
    )
    assert ConnectPairExclusion.are_excluded(viewer, target)


@pytest.mark.parametrize("status", ["pending", "accepted"])
def test_pending_and_accepted_still_excluded_in_both_directions_after_card_cooldown(
    pair, status
):
    viewer, target = pair
    card = _old_card(viewer, target, generated_at=timezone.now() - timedelta(days=60))
    ConnectWeeklyRequest.objects.create(
        session=card.session, requester=viewer, recipient=target, status=status
    )
    assert target not in get_cycle_eligible_pool(viewer)
    assert viewer not in get_cycle_eligible_pool(target)


@pytest.mark.parametrize("reverse", [False, True])
def test_block_still_excludes_pair_after_temporary_cooldowns(pair, reverse):
    from crush_lu.services.blocking import apply_block

    viewer, target = pair
    _expired_request(viewer, target, timezone.now() - timedelta(days=60))
    _old_card(viewer, target, generated_at=timezone.now() - timedelta(days=60))
    apply_block(*(reversed(pair) if reverse else pair))
    assert target not in get_cycle_eligible_pool(viewer)
    assert viewer not in get_cycle_eligible_pool(target)


def test_reeligible_pair_can_send_new_request_then_decline_is_permanent(pair):
    viewer, target = pair
    _, history = _expired_request(viewer, target, timezone.now() - timedelta(days=31))
    session, _ = _reviewable_session_with_card(viewer, target)
    assert can_send_weekly_request(session, viewer, target) == (True, "ok")
    request = send_weekly_request(session, viewer, target)
    respond_to_weekly_request(request, accept=False)
    history.refresh_from_db()
    assert history.reason == ConnectPairExclusion.Reason.REQUEST_DECLINED
    assert ConnectPairExclusion.are_excluded(viewer, target)


@pytest.mark.parametrize("month,day", [(4, 1), (11, 1)])
def test_month_rollover_and_dst_do_not_reset_elapsed_card_cooldown(
    pair, monkeypatch, month, day
):
    from zoneinfo import ZoneInfo

    viewer, target = pair
    now = datetime(2026, month, day, 0, 30, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(timezone, "now", lambda: now)
    for user in pair:
        type(user).objects.filter(pk=user.pk).update(last_login=now)
    # Store the exact boundary with a local timezone representation.
    boundary = (now - timedelta(days=30)).astimezone(ZoneInfo("Europe/Luxembourg"))
    card = _old_card(viewer, target, generated_at=boundary)
    assert target in get_cycle_eligible_pool(viewer)
    ConnectCycleCard.objects.filter(pk=card.pk).update(
        generated_at=now - timedelta(days=29)
    )
    assert target not in get_cycle_eligible_pool(viewer)


@pytest.mark.parametrize("offset,allowed", [(-1, False), (0, True), (1, True)])
def test_request_submission_matches_expiry_cooldown_boundary_in_both_directions(
    pair, monkeypatch, offset, allowed
):
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    _expired_request(*pair, now - timedelta(days=30, microseconds=offset))
    for requester, recipient in [pair, tuple(reversed(pair))]:
        session, _ = _reviewable_session_with_card(requester, recipient)
        assert can_send_weekly_request(session, requester, recipient) == (
            (True, "ok") if allowed else (False, "excluded")
        )


@pytest.mark.parametrize("status", ["pending", "accepted", "declined"])
@pytest.mark.parametrize("reverse", [False, True])
def test_stale_review_cannot_send_duplicate_or_declined_pair(pair, status, reverse):
    viewer, target = pair
    old = ConnectWeekSession.objects.create(user=viewer, status="completed")
    ConnectWeeklyRequest.objects.create(
        session=old, requester=viewer, recipient=target, status=status
    )
    requester, recipient = tuple(reversed(pair)) if reverse else pair
    session, _ = _reviewable_session_with_card(requester, recipient)
    assert can_send_weekly_request(session, requester, recipient) == (False, "excluded")
    with pytest.raises(ValueError, match="excluded"):
        send_weekly_request(session, requester, recipient)
    assert not session.weekly_requests.exists()
    assert recipient not in get_cycle_eligible_pool(requester)


def test_spent_session_never_regains_its_request_after_expiry_cooldown(pair):
    viewer, target = pair
    request, _ = _expired_request(viewer, target, timezone.now() - timedelta(days=31))
    request.session.open_weekly_review()
    ConnectCycleCard.objects.create(
        session=request.session,
        target_user=target,
        day_number=1,
        card_index=1,
        generated_date=timezone.localdate(),
        is_completed=True,
    )
    assert can_send_weekly_request(request.session, viewer, target) == (
        False,
        "already_sent",
    )


@pytest.mark.parametrize(
    "ambiguity", ["row_predates_expiry", "responded", "invalid_deadline"]
)
def test_ambiguous_expiry_provenance_fails_closed(pair, ambiguity):
    viewer, target = pair
    request, exclusion = _expired_request(
        viewer, target, timezone.now() - timedelta(days=60)
    )
    if ambiguity == "row_predates_expiry":
        ConnectPairExclusion.objects.filter(pk=exclusion.pk).update(
            created_at=request.expires_at - timedelta(days=1)
        )
    elif ambiguity == "responded":
        ConnectWeeklyRequest.objects.filter(pk=request.pk).update(
            responded_at=request.expires_at
        )
    else:
        ConnectWeeklyRequest.objects.filter(pk=request.pk).update(
            sent_at=request.expires_at + timedelta(days=1)
        )
    assert ConnectPairExclusion.are_excluded(viewer, target)
    assert target not in get_cycle_eligible_pool(viewer)
    session, _ = _reviewable_session_with_card(viewer, target)
    assert can_send_weekly_request(session, viewer, target) == (False, "excluded")


@pytest.mark.parametrize(
    "gate",
    [
        "user_inactive",
        "profile_inactive",
        "paused",
        "unverified",
        "consent",
        "photo",
        "old_login",
        "coach_excluded",
        "gender",
        "age",
        "coach_pair",
        "connection",
    ],
)
def test_rollover_preserves_every_existing_candidate_gate(pair, gate):
    from crush_lu.models import CrushCoach, EventConnection
    from crush_lu.tests.test_crush_connect import _make_event

    viewer, target = pair
    _old_card(viewer, target, generated_at=timezone.now() - timedelta(days=60))
    _expired_request(viewer, target, timezone.now() - timedelta(days=60))
    assert target in get_cycle_eligible_pool(viewer)
    profile = target.crushprofile
    membership = target.crush_connect_membership
    if gate == "user_inactive":
        target.is_active = False
        target.save(update_fields=["is_active"])
    elif gate == "profile_inactive":
        profile.is_active = False
        profile.save(update_fields=["is_active"])
    elif gate == "paused":
        membership.paused_at = timezone.now()
        membership.save(update_fields=["paused_at"])
    elif gate == "unverified":
        profile.verification_status = "unverified"
        profile.is_approved = False
        profile.save(update_fields=["verification_status", "is_approved"])
    elif gate == "consent":
        membership.photo_share_consent = False
        membership.save(update_fields=["photo_share_consent"])
    elif gate == "photo":
        profile.photo_1 = ""
        profile.save(update_fields=["photo_1"])
    elif gate == "old_login":
        target.last_login = timezone.now() - timedelta(days=31)
        target.save(update_fields=["last_login"])
    elif gate == "coach_excluded":
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
    elif gate == "gender":
        membership.preferred_genders = ["F"]
        membership.save(update_fields=["preferred_genders"])
    elif gate == "age":
        membership.preferred_age_min = 90
        membership.save(update_fields=["preferred_age_min"])
    elif gate == "coach_pair":
        coach = CrushCoach.objects.create(user=target, is_active=True)
        viewer.crushprofile.assigned_coach = coach
        viewer.crushprofile.save(update_fields=["assigned_coach"])
    else:
        EventConnection.objects.create(
            event=_make_event(), requester=viewer, recipient=target
        )
    assert target not in get_cycle_eligible_pool(viewer)


def test_permanent_promotion_sql_is_conditioned_on_temporary_reason(pair):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    viewer, target = pair
    _expired_request(viewer, target, timezone.now() - timedelta(days=60))
    with CaptureQueriesContext(connection) as queries:
        ConnectPairExclusion.exclude_pair(target, viewer, "member_blocked")
    updates = [query["sql"] for query in queries if query["sql"].startswith("UPDATE")]
    assert len(updates) == 1
    assert "request_expired" in updates[0].split("WHERE", 1)[1]
    assert "member_blocked" in updates[0].split("WHERE", 1)[0]


def test_legacy_cutoff_uses_platform_timezone_even_when_runtime_zone_differs(
    pair, monkeypatch
):
    viewer, target = pair
    now = datetime(2026, 10, 6, 22, 30, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(timezone, "now", lambda: now)
    for user in pair:
        type(user).objects.filter(pk=user.pk).update(last_login=now)
    cutoff = timezone.localdate(
        now - timedelta(days=30), timezone.get_default_timezone()
    )
    card = _old_card(viewer, target, generated_at=None)
    ConnectCycleCard.objects.filter(pk=card.pk).update(generated_date=cutoff)
    with timezone.override("Pacific/Auckland"):
        assert target not in get_cycle_eligible_pool(viewer)


def test_database_still_rejects_repeated_target_within_session(pair):
    from django.db import IntegrityError, transaction

    card = _old_card(*pair, generated_at=timezone.now() - timedelta(days=60))
    with pytest.raises(IntegrityError), transaction.atomic():
        ConnectCycleCard.objects.create(
            session=card.session,
            target_user=pair[1],
            day_number=2,
            card_index=2,
            generated_date=timezone.localdate(),
        )
    assert card.session.cards.count() == 1
