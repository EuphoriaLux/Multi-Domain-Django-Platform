"""Populated migration check: preserve legacy history and unknown assignment times."""

from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_assignment_timestamp_migration_preserves_populated_history():
    previous = ("crush_lu", "0263_premium_recovery_resolution")
    current = ("crush_lu", "0264_connectcyclecard_generated_at")
    executor = MigrationExecutor(connection)
    restore_targets = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([previous])
        apps = executor.loader.project_state([previous]).apps
        User = apps.get_model("auth", "User")
        Session = apps.get_model("crush_lu", "ConnectWeekSession")
        Card = apps.get_model("crush_lu", "ConnectCycleCard")
        Request = apps.get_model("crush_lu", "ConnectWeeklyRequest")
        Exclusion = apps.get_model("crush_lu", "ConnectPairExclusion")
        viewer = User.objects.create(username="migration_viewer")
        target = User.objects.create(username="migration_target")
        session = Session.objects.create(user=viewer, status="completed")
        generated_date = timezone.localdate() - timedelta(days=60)
        answers = {"guesses": {"1": True}, "gate_align": 1}
        card = Card.objects.create(
            session=session,
            target_user=target,
            day_number=1,
            card_index=1,
            generated_date=generated_date,
            answers_json=answers,
            is_completed=True,
            is_expired=True,
            completed_at=timezone.now() - timedelta(days=60),
        )
        request = Request.objects.create(
            session=session,
            requester=viewer,
            recipient=target,
            target_card=card,
            status="expired",
            expires_at=timezone.now() - timedelta(days=59),
        )
        exclusion = Exclusion.objects.create(
            user_a=viewer, user_b=target, reason="request_expired"
        )
        card_snapshot = Card.objects.values().get(pk=card.pk)
        request_snapshot = Request.objects.values().get(pk=request.pk)
        exclusion_snapshot = Exclusion.objects.values().get(pk=exclusion.pk)
        session_snapshot = Session.objects.values().get(pk=session.pk)

        executor = MigrationExecutor(connection)
        executor.migrate([current])
        apps = executor.loader.project_state([current]).apps
        Card = apps.get_model("crush_lu", "ConnectCycleCard")
        persisted = Card.objects.values().get(pk=card.pk)
        assert persisted.pop("generated_at") is None
        assert persisted == card_snapshot
        assert (
            apps.get_model("crush_lu", "ConnectWeeklyRequest")
            .objects.values()
            .get(pk=request.pk)
            == request_snapshot
        )
        assert (
            apps.get_model("crush_lu", "ConnectPairExclusion")
            .objects.values()
            .get(pk=exclusion.pk)
            == exclusion_snapshot
        )
        assert (
            apps.get_model("crush_lu", "ConnectWeekSession")
            .objects.values()
            .get(pk=session.pk)
            == session_snapshot
        )

        Session = apps.get_model("crush_lu", "ConnectWeekSession")
        next_session = Session.objects.create(user_id=viewer.pk)
        before = timezone.now()
        new_card = Card.objects.create(
            session=next_session,
            target_user_id=target.pk,
            day_number=1,
            card_index=1,
            generated_date=timezone.localdate(),
        )
        assert before <= new_card.generated_at <= timezone.now()
    finally:
        MigrationExecutor(connection).migrate(restore_targets)
