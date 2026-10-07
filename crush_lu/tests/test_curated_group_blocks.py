"""A block created after a curated generation exists must not slip through (#1195)."""

from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from crush_lu.models import UserBlock
from crush_lu.models.events import CuratedEventGroup
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    generate_group_projection,
    get_approved_current_generation,
    lock_current_generation,
)
from crush_lu.tests import test_curated_group_workflow_integration as _workflow_tests

Base = _workflow_tests.CuratedGroupWorkflowIntegrationTests


class CuratedGroupLateBlockTests(TestCase):
    make_event = Base.make_event
    make_applicants = Base.make_applicants

    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "crush.lu"

    def _block_two_members(self, registrations):
        UserBlock.objects.create(
            blocker=registrations[0].user,
            blocked=registrations[1].user,
            reason="other",
        )

    def test_block_after_draft_forces_regeneration_before_approval(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        generate_group_projection(event, deterministic_seed="late-block-draft")

        self._block_two_members(registrations)

        with self.assertRaises(ValidationError):
            approve_current_generation(event)
        self.assertFalse(
            CuratedEventGroup.objects.filter(
                event=event, status=CuratedEventGroup.STATUS_PROVISIONAL
            ).exists()
        )

    def test_block_after_approval_refuses_invitation(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        generate_group_projection(event, deterministic_seed="late-block-invite")
        approve_current_generation(event)

        self._block_two_members(registrations)

        with self.assertRaisesMessage(ValidationError, "blocked each other"):
            get_approved_current_generation(event)

    def test_no_block_still_invites(self):
        event = self.make_event()
        self.make_applicants(event)
        generate_group_projection(event, deterministic_seed="no-block")
        approve_current_generation(event)

        approved = get_approved_current_generation(event)

        self.assertEqual(len(approved.applied_registration_ids), 6)

    def test_block_at_lock_time_is_flagged_not_refused(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        generate_group_projection(event, deterministic_seed="late-block-lock")
        approve_current_generation(event)
        self._block_two_members(registrations)

        # Check-in is out of scope here; only the block flag is under test.
        with patch.object(CuratedEventGroup, "lock") as lock, self.assertLogs(
            "crush_lu.services.curated_group_workflow", "WARNING"
        ):
            group_ids = lock_current_generation(event)

        lock.assert_called_once()

        self.assertTrue(group_ids)
