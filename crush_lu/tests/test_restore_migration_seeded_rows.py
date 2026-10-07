"""The migration-catalogue restore must not resurrect rows whose user is gone.

A flushing (``transaction=True``) module empties ``auth.User`` along with the
seeded catalogues, but the snapshot taken by ``_restore_migration_seeded_rows``
holds only ``crush_lu`` rows. Replaying a ``CrushCoach`` without its user made
the teardown ``check_constraints()`` fail with an FK error. These tests drive
the replay helper directly, so they need no Playwright browser.
"""

from django.contrib.auth import get_user_model
from django.core import serializers
from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import CrushCoach
from crush_lu.tests.conftest import _replay_snapshot

User = get_user_model()


class ReplaySnapshotOrphanTests(TestCase):
    def _coach_snapshot(self, username):
        user = User.objects.create_user(username=username, email=username, password="x")
        coach = CrushCoach.objects.create(user=user, is_active=True)
        return user, coach, serializers.serialize("json", [coach])

    def _replay(self, snapshot):
        # Same shape as the fixture: deferred checks, then a real check.
        with transaction.atomic(), connection.constraint_checks_disabled():
            skipped = _replay_snapshot(snapshot)
        connection.check_constraints()
        return skipped

    def test_row_whose_user_still_exists_is_restored(self):
        user, coach, snapshot = self._coach_snapshot("kept@example.com")
        CrushCoach.objects.filter(pk=coach.pk).delete()

        skipped = self._replay(snapshot)

        self.assertEqual(skipped, [])
        self.assertTrue(CrushCoach.objects.filter(pk=coach.pk, user=user).exists())

    def test_row_whose_user_is_gone_is_skipped_without_fk_error(self):
        user, coach, snapshot = self._coach_snapshot("gone@example.com")
        User.objects.filter(pk=user.pk).delete()  # cascades the coach too
        self.assertFalse(CrushCoach.objects.filter(pk=coach.pk).exists())

        skipped = self._replay(snapshot)  # would raise IntegrityError before

        self.assertEqual(skipped, [("crush_lu.CrushCoach", coach.pk)])
        self.assertFalse(CrushCoach.objects.filter(pk=coach.pk).exists())

    def test_row_with_a_gone_many_to_many_target_is_skipped(self):
        from crush_lu.models import MeetupEvent

        user = User.objects.create_user(
            username="invitee@example.com", email="invitee@example.com", password="x"
        )
        event = MeetupEvent.objects.create(
            title="Private",
            description="d",
            event_type="meetup",
            date_time=timezone.now(),
            registration_deadline=timezone.now(),
            location="x",
            address="x",
            canton="LU",
            duration_minutes=60,
        )
        event.invited_users.add(user)
        snapshot = serializers.serialize("json", [event])
        event_pk = event.pk
        event.delete()
        User.objects.filter(pk=user.pk).delete()

        skipped = self._replay(snapshot)  # junction FK error before the fix

        self.assertEqual(skipped, [("crush_lu.MeetupEvent", event_pk)])
        self.assertFalse(MeetupEvent.objects.filter(pk=event_pk).exists())
