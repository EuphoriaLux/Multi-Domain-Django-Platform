"""
manage_coach_staff_status must ignore raw saves.

A raw save (``loaddata`` fixtures, ``serializers.deserialize(...).save()``,
and the migration-catalogue replay in crush_lu/tests/conftest.py's
``_restore_migration_seeded_rows``) replays stored state. It must not grant
or revoke staff status, and it must not dereference ``instance.user``, which
may not exist yet while constraint checks are deferred.
"""

from django.contrib.auth import get_user_model
from django.core import serializers
from django.db import connection, transaction
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import CrushCoach

User = get_user_model()


def _unsaved_coach(pk, user, is_active):
    """A CrushCoach that has never gone through save(), so no signal fired.

    ``created_at`` is auto_now_add, which a raw save does not fill in (a real
    fixture always carries it), so set it the way a dump would.
    """
    return CrushCoach(pk=pk, user=user, is_active=is_active, created_at=timezone.now())


def _raw_replay(*objects):
    """Serialize unsaved/saved objects and save them back as raw saves."""
    payload = serializers.serialize("json", objects)
    for wrapped in serializers.deserialize("json", payload):
        wrapped.save()  # Model.save_base(raw=True) -> post_save(raw=True)


class CoachStaffSignalRawSaveTests(TestCase):
    def _user(self, username, is_staff=False):
        return User.objects.create_user(
            username=username,
            email=username,
            password="x",
            is_staff=is_staff,
        )

    def test_normal_save_still_grants_staff(self):
        """Control: the signal is live for ordinary saves."""
        user = self._user("normal@example.com")
        CrushCoach.objects.create(user=user, is_active=True)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_raw_save_of_active_coach_does_not_grant_staff(self):
        user = self._user("raw-active@example.com")
        _raw_replay(_unsaved_coach(90001, user, True))

        self.assertTrue(CrushCoach.objects.filter(pk=90001).exists())
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_raw_save_of_inactive_coach_does_not_revoke_staff(self):
        user = self._user("raw-inactive@example.com", is_staff=True)
        _raw_replay(_unsaved_coach(90002, user, False))

        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_raw_save_before_its_user_exists_does_not_raise(self):
        """The replay order the conftest restore hook can produce: the coach
        row lands while its User is not (yet) in the database."""
        user = self._user("raw-missing@example.com")
        coach = _unsaved_coach(90003, user, True)
        payload = serializers.serialize("json", [coach])
        missing_user_id = user.pk + 100000
        payload = payload.replace(f'"user": {user.pk}', f'"user": {missing_user_id}')
        self.assertIn(f'"user": {missing_user_id}', payload)

        # FK checks deferred, exactly as the conftest replay does it.
        with transaction.atomic(), connection.constraint_checks_disabled():
            for wrapped in serializers.deserialize("json", payload):
                wrapped.save()
            CrushCoach.objects.filter(pk=90003).delete()
