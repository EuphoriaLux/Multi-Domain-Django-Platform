"""A block is information for the coach, never a gate on curated groups (#1195)."""

from django.test import TestCase

from crush_lu.models import UserBlock
from crush_lu.services.curated_group_insights import coach_group_panel
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    generate_group_projection,
    get_approved_current_generation,
)
from crush_lu.services.event_grouping import project_event_groups
from crush_lu.tests import test_curated_group_workflow_integration as _workflow_tests

Base = _workflow_tests.CuratedGroupWorkflowIntegrationTests


class CuratedGroupBlocksAreWarningsOnlyTests(TestCase):
    make_event = Base.make_event
    make_applicants = Base.make_applicants

    def setUp(self):
        self.client.defaults["HTTP_HOST"] = "crush.lu"

    def _block(self, registrations, first=0, second=1):
        UserBlock.objects.create(
            blocker=registrations[first].user,
            blocked=registrations[second].user,
            reason="other",
        )

    def test_projection_is_identical_with_and_without_a_block(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        before = project_event_groups(event, deterministic_seed="same")

        self._block(registrations)
        after = project_event_groups(event, deterministic_seed="same")

        self.assertEqual(before, after)
        self.assertEqual(before.input_digest, after.input_digest)

    def test_blocked_pair_still_shares_a_group_and_nothing_is_refused(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        self._block(registrations)
        generate_group_projection(event, deterministic_seed="no-gate")

        approve_current_generation(event)
        approved = get_approved_current_generation(event)

        self.assertEqual(len(approved.applied_registration_ids), 6)

    def test_coach_panel_counts_blocked_pairs_sharing_a_group_as_information(self):
        event = self.make_event()
        registrations = self.make_applicants(event)
        generate_group_projection(event, deterministic_seed="panel-info")
        approve_current_generation(event)
        pair = tuple(sorted((registrations[0].user_id, registrations[1].user_id)))

        self.assertEqual(
            coach_group_panel(event, blocked_user_pairs=[pair])[
                "blocked_pairs_in_groups"
            ],
            1,
        )
        self.assertEqual(coach_group_panel(event)["blocked_pairs_in_groups"], 0)
