"""PostgreSQL-only proof that simultaneous expiry/permanent writers cannot downgrade."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection, connections, transaction

from crush_lu.models.crush_connect_cycle import ConnectPairExclusion
from crush_lu.tests.test_connect_week_experience import _make_cycle_user


@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="Requires a test PostgreSQL database"
)
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
