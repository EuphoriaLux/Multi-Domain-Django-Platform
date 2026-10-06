"""PostgreSQL-only concurrency proofs for Connect Week pair writes.

SQLite ignores row locks, so these only run against a test PostgreSQL database.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from crush_lu.models.crush_connect_cycle import (
    ConnectPairExclusion,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.services.connect_cycle import send_weekly_request, sync_request_state
from crush_lu.tests.test_connect_week_experience import (
    _make_cycle_user,
    _reviewable_session_with_card,
)
from crush_lu.tests.test_crush_connect import _set_gate_questions

requires_postgres = pytest.mark.skipif(
    connection.vendor != "postgresql", reason="Requires a test PostgreSQL database"
)


@requires_postgres
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("preexisting", [False, True])
@pytest.mark.parametrize(
    "reason", ["request_declined", "member_blocked", "coach_excluded"]
)
def test_concurrent_expiry_and_permanent_exclusion_preserve_canonical_pair(
    preexisting, reason
):
    viewer = _make_cycle_user("concurrent_viewer")
    target = _make_cycle_user("concurrent_target")
    if preexisting:
        ConnectPairExclusion.exclude_pair(viewer, target, "request_expired")
    barrier = Barrier(2, timeout=10)

    def exclude(first, second, exclusion_reason):
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '20s'")
                    cursor.execute("SET LOCAL lock_timeout = '10s'")
                barrier.wait()
                ConnectPairExclusion.exclude_pair(first, second, exclusion_reason)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(exclude, viewer, target, "request_expired"),
            executor.submit(exclude, target, viewer, reason),
        ]
        for future in futures:
            future.result(timeout=30)
    exclusion = ConnectPairExclusion.objects.get()
    assert exclusion.reason == reason
    assert (exclusion.user_a_id, exclusion.user_b_id) == tuple(
        sorted((viewer.pk, target.pk))
    )
    assert ConnectPairExclusion.are_excluded(viewer, target)


def _launched_pair(settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_cycle_user("concurrent_member")
    other = _make_cycle_user("concurrent_other", gender="F")
    _set_gate_questions(member)
    _set_gate_questions(other)
    return member, other


def _set_session_timeouts():
    with connection.cursor() as cursor:
        cursor.execute("SET statement_timeout = '20s'")
        cursor.execute("SET lock_timeout = '10s'")


def _send_outcome(session, requester, recipient):
    try:
        send_weekly_request(session, requester, recipient)
    except ValueError as exc:
        return str(exc)
    return "sent"


def _wait_for_lock_waiter(timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_locks WHERE NOT granted AND pid IN "
                "(SELECT pid FROM pg_stat_activity "
                "WHERE datname = current_database())"
            )
            if cursor.fetchone()[0]:
                return
        time.sleep(0.05)
    raise AssertionError("send never waited on the held request lock")


@requires_postgres
@pytest.mark.django_db(transaction=True)
def test_reciprocal_sends_create_exactly_one_request(settings):
    member, other = _launched_pair(settings)
    sessions = {
        member.pk: _reviewable_session_with_card(member, other)[0],
        other.pk: _reviewable_session_with_card(other, member)[0],
    }
    barrier = Barrier(2, timeout=10)

    def send(requester, recipient):
        try:
            _set_session_timeouts()
            barrier.wait()
            return _send_outcome(sessions[requester.pk], requester, recipient)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(send, member, other),
            executor.submit(send, other, member),
        ]
        outcomes = sorted(future.result(timeout=30) for future in futures)
    assert outcomes == ["excluded", "sent"]
    assert ConnectWeeklyRequest.objects.count() == 1


@requires_postgres
@pytest.mark.django_db(transaction=True)
def test_send_waits_for_concurrent_overdue_sync_without_deadlock(settings):
    """The send's pair lock is held while its pair sync waits on an overdue
    request that a concurrent sync already locked. That sync's deferred FK
    check on the new exclusion row takes FOR KEY SHARE on both users at COMMIT,
    so a FOR UPDATE pair lock would deadlock with it."""
    member, other = _launched_pair(settings)
    other_session = ConnectWeekSession.objects.create(
        user=other, status=ConnectWeekSession.Status.COMPLETED
    )
    expires_at = timezone.now() - timedelta(hours=1)
    overdue = ConnectWeeklyRequest.objects.create(
        session=other_session,
        requester=other,
        recipient=member,
        status=ConnectWeeklyRequest.Status.PENDING,
        expires_at=expires_at,
    )
    ConnectWeeklyRequest.objects.filter(pk=overdue.pk).update(
        sent_at=expires_at - timedelta(hours=24)
    )
    session, _ = _reviewable_session_with_card(member, other)
    synced, release = Event(), Event()

    def sync_and_hold():
        try:
            _set_session_timeouts()
            with transaction.atomic():
                sync_request_state(ConnectWeeklyRequest.objects.get(pk=overdue.pk))
                synced.set()
                release.wait(timeout=10)
            return "ok"
        finally:
            connections.close_all()

    def send():
        try:
            _set_session_timeouts()
            return _send_outcome(session, member, other)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        holder = executor.submit(sync_and_hold)
        try:
            assert synced.wait(timeout=10)
            sender = executor.submit(send)
            _wait_for_lock_waiter()
        finally:
            release.set()
        assert holder.result(timeout=30) == "ok"
        assert sender.result(timeout=30) == "excluded"
    overdue.refresh_from_db()
    assert overdue.status == ConnectWeeklyRequest.Status.EXPIRED
    assert ConnectWeeklyRequest.objects.count() == 1
