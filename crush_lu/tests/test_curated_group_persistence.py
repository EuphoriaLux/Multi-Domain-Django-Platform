"""Durable and auditable parallel-group state for curated speed dating."""

import importlib

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.utils import timezone

from crush_lu.admin.events import (
    CuratedEventGroupInline,
    EventRegistrationAdmin,
    MeetupEventAdmin,
)
from crush_lu.models import (
    CrushProfile,
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedEventPairing,
    CuratedEventPairingParticipant,
    EventRegistration,
    EventRegistrationPreference,
    MeetupEvent,
)

User = get_user_model()


class CuratedGroupPersistenceBase(TestCase):
    def setUp(self):
        self.event = self.make_event()

    @staticmethod
    def fairness_audit(**overrides):
        decision = {
            "min_required": 5,
            "min_achieved": 5,
            "target_requested": 7,
            "target_achieved": False,
            "members_meeting_target": 0,
            "track_size": 6,
            "track_ordinal": 1,
            "underserved_priority": True,
            "alternative_scarcity_score": 1.0,
            "one_drop_resilient": False,
            "pinned_member_count": 0,
        }
        decision.update(overrides)
        return {"fairness_decision": decision}

    def make_event(self, **overrides):
        values = {
            "title": "Elastic evening",
            "description": "Persistence tests",
            "event_type": "speed_dating",
            "registration_mode": "curated",
            "date_time": timezone.now() + timedelta(days=7),
            "location": "Luxembourg",
            "address": "Test venue",
            "max_participants": 12,
            "group_size": 6,
            "planned_groups": 2,
            "registration_deadline": timezone.now() + timedelta(days=5),
            "profile_requirement": "none",
            "is_published": False,
        }
        values.update(overrides)
        return MeetupEvent.objects.create(**values)

    def make_registration(
        self,
        index,
        *,
        event=None,
        status="applied",
        gender="M",
        with_preference=True,
    ):
        event = event or self.event
        user = User.objects.create_user(
            username=f"member{event.pk}-{index}@example.com",
            email=f"member{event.pk}-{index}@example.com",
            password="testpass123",
            first_name=f"Member {index}",
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location="Luxembourg",
            event_languages=["en"],
        )
        registration = EventRegistration.objects.create(
            event=event,
            user=user,
            status=status,
        )
        if with_preference:
            EventRegistrationPreference.objects.create(
                registration=registration,
                preferred_genders=[],
                preferred_age_min=18,
                preferred_age_max=99,
                languages=[],
            )
        return registration

    def make_group(
        self,
        *,
        generation=1,
        group_number=1,
        registrations=None,
        registration_start=0,
    ):
        group = CuratedEventGroup.objects.create(
            event=self.event,
            generation=generation,
            group_number=group_number,
            seed="da66c0de8675309abc12",
            policy_version="reciprocal-graph-v1",
        )
        registrations = registrations or [
            self.make_registration(index)
            for index in range(registration_start, registration_start + 6)
        ]
        memberships = []
        for position, registration in enumerate(registrations, start=1):
            memberships.append(
                CuratedEventGroupMembership.objects.create(
                    event=self.event,
                    group=group,
                    registration=registration,
                    position=position,
                )
            )
        return group, registrations, memberships

    def add_round_robin_schedule(self, group, registrations):
        """Store the five-round complete round robin for six people."""
        rotation = list(registrations)
        for round_number in range(1, len(rotation)):
            for table_number in range(1, len(rotation) // 2 + 1):
                first = rotation[table_number - 1]
                second = rotation[-table_number]
                pairing = CuratedEventPairing.objects.create(
                    event=self.event,
                    group=group,
                    round_number=round_number,
                    table_number=table_number,
                )
                for seat, registration in (("a", first), ("b", second)):
                    CuratedEventPairingParticipant.objects.create(
                        event=self.event,
                        group=group,
                        pairing=pairing,
                        round_number=round_number,
                        registration=registration,
                        seat=seat,
                    )
            rotation = [rotation[0], rotation[-1], *rotation[1:-1]]

    def make_viable_group(self, **group_kwargs):
        group, registrations, memberships = self.make_group(**group_kwargs)
        self.add_round_robin_schedule(group, registrations)
        return group, registrations, memberships

    def admin_request(self):
        request = RequestFactory().post("/crush-admin/")
        request.user = User.objects.create_superuser(
            username=f"coach-{User.objects.count()}@example.com",
            email=f"coach-{User.objects.count()}@example.com",
            password="testpass123",
        )
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def run_confirm_action(self, registrations):
        queryset = EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        )
        EventRegistrationAdmin(EventRegistration, AdminSite()).confirm_registrations(
            self.admin_request(), queryset
        )


class WholeGroupCapacityTests(CuratedGroupPersistenceBase):
    def test_group_smaller_than_the_five_date_guarantee_is_rejected(self):
        event = self.make_event(
            title="Too-small group",
            max_participants=10,
            group_size=5,
            planned_groups=2,
        )

        with self.assertRaisesMessage(ValidationError, "at least 6"):
            event.full_clean()

    def test_group_larger_than_the_projector_bound_is_rejected(self):
        event = self.make_event(
            title="Too-large group",
            max_participants=86,
            group_size=43,
            planned_groups=2,
        )

        with self.assertRaisesMessage(ValidationError, "cannot exceed 42"):
            event.full_clean()

    def test_non_divisible_venue_ceiling_never_rounds_up(self):
        event = self.make_event(
            title="Thirty-five-seat venue",
            max_participants=35,
            group_size=16,
            planned_groups=None,
        )

        event.full_clean()

        self.assertEqual(event.max_groups, 2)
        self.assertEqual(event.group_capacity_remainder, 3)
        self.assertEqual(event.selection_capacity, 32)

    def test_planned_groups_reduce_selection_below_the_venue_ceiling(self):
        self.event.max_participants = 35
        self.event.group_size = 16
        self.event.planned_groups = 1

        self.assertEqual(self.event.selection_capacity, 16)

    def test_direct_events_still_select_against_raw_capacity(self):
        direct = self.make_event(
            title="Direct event",
            registration_mode="direct",
            group_size=None,
            planned_groups=None,
            max_participants=35,
        )

        self.assertEqual(direct.selection_capacity, 35)

    def test_admin_selection_uses_whole_group_capacity(self):
        self.event.max_participants = 13
        self.event.group_size = 6
        self.event.planned_groups = 1
        self.event.save(
            update_fields=["max_participants", "group_size", "planned_groups"]
        )
        seated = [self.make_registration(index) for index in range(5)]
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in seated]
        ).update(status="confirmed")
        applicants = [self.make_registration(index + 10) for index in range(2)]

        self.run_confirm_action(applicants)

        self.assertEqual(self.event.selection_capacity, 6)
        self.assertEqual(len(seated), 5)
        self.assertEqual(
            EventRegistration.objects.filter(
                pk__in=[r.pk for r in applicants], status="applied"
            ).count(),
            2,
        )


class ApplicationPoolPrivacyTests(CuratedGroupPersistenceBase):
    def test_public_default_omits_private_gender_breakdown(self):
        self.make_registration(1, gender="F")

        pool = self.event.get_application_pool()

        self.assertNotIn("by_pool", pool)

    def test_organizer_can_explicitly_request_private_breakdown(self):
        self.make_registration(1, gender="F")

        pool = self.event.get_application_pool(include_private_breakdown=True)

        self.assertEqual(pool["by_pool"]["F"], 1)


class CuratedGenderCapCleanupMigrationTests(CuratedGroupPersistenceBase):
    def test_cleanup_is_scoped_to_effective_curated_speed_dating(self):
        stale = self.event
        stale.max_participants_m = 6
        stale.max_participants_f = 6
        stale.max_participants_nb = 0
        stale.save(
            update_fields=[
                "max_participants_m",
                "max_participants_f",
                "max_participants_nb",
            ]
        )
        direct = self.make_event(
            title="Direct control",
            registration_mode="direct",
            group_size=None,
            planned_groups=None,
            max_participants_m=6,
            max_participants_f=6,
            max_participants_nb=0,
        )
        legacy_curated = self.make_event(
            title="Legacy curated control",
            group_size=None,
            planned_groups=None,
            max_participants_m=6,
            max_participants_f=6,
            max_participants_nb=0,
        )
        curated_mixer = self.make_event(
            title="Mixer control",
            event_type="mixer",
            registration_mode="curated",
            group_size=None,
            planned_groups=None,
            max_participants_m=6,
            max_participants_f=6,
            max_participants_nb=0,
        )
        migration = importlib.import_module(
            "crush_lu.migrations.0245_curated_event_group_persistence"
        )
        from django.apps import apps

        migration.clear_curated_speed_dating_gender_caps(apps, None)

        stale.refresh_from_db()
        direct.refresh_from_db()
        legacy_curated.refresh_from_db()
        curated_mixer.refresh_from_db()
        self.assertIsNone(stale.max_participants_m)
        self.assertIsNone(stale.max_participants_f)
        self.assertIsNone(stale.max_participants_nb)
        self.assertEqual(direct.max_participants_nb, 0)
        self.assertEqual(legacy_curated.max_participants_nb, 0)
        self.assertEqual(curated_mixer.max_participants_nb, 0)


class GroupIdentityAndAssignmentConstraintTests(CuratedGroupPersistenceBase):
    def test_generation_and_group_number_start_at_one(self):
        for field_name in ("generation", "group_number"):
            values = {"generation": 1, "group_number": 1, field_name: 0}
            with self.subTest(field_name=field_name):
                with self.assertRaises((ValidationError, IntegrityError)):
                    with transaction.atomic():
                        CuratedEventGroup.objects.create(event=self.event, **values)

    def test_same_display_number_can_be_reused_in_a_new_generation(self):
        CuratedEventGroup.objects.create(event=self.event, generation=1, group_number=1)

        replacement = CuratedEventGroup.objects.create(
            event=self.event, generation=2, group_number=1
        )

        self.assertEqual(replacement.group_number, 1)

    def test_database_rejects_two_active_assignments_for_one_event(self):
        first, registrations, _ = self.make_group()
        second = CuratedEventGroup.objects.create(
            event=self.event, generation=1, group_number=2
        )
        duplicate = CuratedEventGroupMembership(
            event=self.event,
            group=second,
            registration=registrations[0],
            position=1,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CuratedEventGroupMembership.objects.bulk_create([duplicate])

        self.assertEqual(first.memberships.filter(released_at__isnull=True).count(), 6)

    def test_active_assignment_uniqueness_cannot_be_bypassed_with_false_event(self):
        first, registrations, _ = self.make_group()
        other_event = self.make_event(title="False event")
        second = CuratedEventGroup.objects.create(
            event=self.event, generation=1, group_number=2
        )
        duplicate = CuratedEventGroupMembership(
            event=other_event,
            group=second,
            registration=registrations[0],
            position=1,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CuratedEventGroupMembership.objects.bulk_create([duplicate])

        self.assertTrue(
            first.memberships.filter(registration=registrations[0]).exists()
        )

    def test_released_assignment_is_retained_and_can_be_reassigned(self):
        first, registrations, memberships = self.make_group()
        second = CuratedEventGroup.objects.create(
            event=self.event, generation=2, group_number=1
        )
        memberships[0].release(reason="new projection")

        replacement = CuratedEventGroupMembership.objects.create(
            event=self.event,
            group=second,
            registration=registrations[0],
            position=1,
        )

        memberships[0].refresh_from_db()
        self.assertIsNotNone(memberships[0].released_at)
        self.assertIsNone(replacement.released_at)
        self.assertEqual(
            registrations[0].curated_group_memberships.count(),
            2,
        )

    def test_cross_event_assignment_is_rejected(self):
        group, _, _ = self.make_group()
        other_event = self.make_event(title="Other evening")
        other_registration = self.make_registration(99, event=other_event)
        membership = CuratedEventGroupMembership(
            event=self.event,
            group=group,
            registration=other_registration,
            position=7,
        )

        with self.assertRaises(ValidationError):
            membership.save()


class ScheduleConstraintAndViabilityTests(CuratedGroupPersistenceBase):
    def test_pairing_table_uniqueness_cannot_be_bypassed_with_false_event(self):
        group, _, _ = self.make_group()
        other_event = self.make_event(title="False pairing event")
        CuratedEventPairing.objects.create(
            event=self.event, group=group, round_number=1, table_number=1
        )
        duplicate = CuratedEventPairing(
            event=other_event,
            group=group,
            round_number=1,
            table_number=1,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CuratedEventPairing.objects.bulk_create([duplicate])

    def test_database_rejects_a_second_slot_for_one_person_in_a_round(self):
        group, registrations, _ = self.make_group()
        first_pairing = CuratedEventPairing.objects.create(
            event=self.event, group=group, round_number=1, table_number=1
        )
        second_pairing = CuratedEventPairing.objects.create(
            event=self.event, group=group, round_number=1, table_number=2
        )
        CuratedEventPairingParticipant.objects.create(
            event=self.event,
            group=group,
            pairing=first_pairing,
            round_number=1,
            registration=registrations[0],
            seat="a",
        )
        duplicate = CuratedEventPairingParticipant(
            event=self.event,
            group=group,
            pairing=second_pairing,
            round_number=1,
            registration=registrations[0],
            seat="a",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CuratedEventPairingParticipant.objects.bulk_create([duplicate])

    def test_cancelled_generation_does_not_block_same_round_in_replacement(self):
        first, registrations, _ = self.make_viable_group()
        first.cancel(reason="new projection")
        replacement, _, _ = self.make_group(
            generation=2,
            registrations=registrations,
        )

        self.add_round_robin_schedule(replacement, registrations)

        self.assertEqual(replacement.pairing_participants.count(), 30)

    def test_schedule_rejects_a_participant_outside_the_group(self):
        group, _, _ = self.make_group()
        outsider = self.make_registration(100)
        pairing = CuratedEventPairing.objects.create(
            event=self.event, group=group, round_number=1, table_number=1
        )

        with self.assertRaises(ValidationError):
            CuratedEventPairingParticipant.objects.create(
                event=self.event,
                group=group,
                pairing=pairing,
                round_number=1,
                registration=outsider,
                seat="a",
            )

    def test_provisional_requires_five_distinct_compatible_dates_each(self):
        group, registrations, _ = self.make_group()
        pairing = CuratedEventPairing.objects.create(
            event=self.event, group=group, round_number=1, table_number=1
        )
        for seat, registration in (("a", registrations[0]), ("b", registrations[1])):
            CuratedEventPairingParticipant.objects.create(
                event=self.event,
                group=group,
                pairing=pairing,
                round_number=1,
                registration=registration,
                seat=seat,
            )

        with self.assertRaisesMessage(ValidationError, "guarantee is 5"):
            group.mark_provisional(audit_data=self.fairness_audit())

    def test_duplicate_partner_pair_is_rejected_across_rounds(self):
        group, registrations, _ = self.make_viable_group()
        duplicate = CuratedEventPairing.objects.create(
            event=self.event, group=group, round_number=6, table_number=1
        )
        for seat, registration in (("a", registrations[0]), ("b", registrations[1])):
            CuratedEventPairingParticipant.objects.create(
                event=self.event,
                group=group,
                pairing=duplicate,
                round_number=6,
                registration=registration,
                seat=seat,
            )

        with self.assertRaisesMessage(ValidationError, "multiple rounds"):
            group.schedule_viability()

    def test_incompatible_preference_pair_is_rejected(self):
        group, registrations, _ = self.make_viable_group()
        preference = registrations[0].preference
        preference.preferred_genders = ["F"]
        preference.languages = ["en"]
        preference.save(update_fields=["preferred_genders", "languages"])

        with self.assertRaisesMessage(ValidationError, "event preferences"):
            group.schedule_viability()

    def test_missing_preference_snapshot_cannot_be_provisional(self):
        group, registrations, _ = self.make_viable_group()
        registrations[0].preference.delete()

        with self.assertRaisesMessage(ValidationError, "complete event preference"):
            group.mark_provisional(audit_data=self.fairness_audit())

    def test_cancelled_member_cannot_count_toward_viability(self):
        group, registrations, _ = self.make_viable_group()
        EventRegistration.objects.filter(pk=registrations[0].pk).update(
            status="cancelled"
        )

        with self.assertRaisesMessage(ValidationError, "cannot count"):
            group.schedule_viability()

    def test_certification_rechecks_membership_registration_event(self):
        group, registrations, _ = self.make_viable_group()
        other_event = self.make_event(title="Moved registration")
        EventRegistration.objects.filter(pk=registrations[0].pk).update(
            event=other_event
        )

        with self.assertRaisesMessage(ValidationError, "group's event"):
            group.schedule_viability()

    def test_rounds_must_be_contiguous_from_one(self):
        group, _, _ = self.make_viable_group()
        group.pairings.filter(round_number=5).update(round_number=7)
        group.pairing_participants.filter(round_number=5).update(round_number=7)

        with self.assertRaisesMessage(ValidationError, "contiguous from round 1"):
            group.schedule_viability()

    def test_tables_must_be_contiguous_from_one(self):
        group, _, _ = self.make_viable_group()
        pairing = group.pairings.get(round_number=1, table_number=3)
        pairing.table_number = 4
        pairing.save(update_fields=["table_number"])

        with self.assertRaisesMessage(ValidationError, "contiguous from 1"):
            group.schedule_viability()


class GroupLifecycleTests(CuratedGroupPersistenceBase):
    def test_status_transitions_require_canonical_lifecycle_methods(self):
        group, _, _ = self.make_viable_group()
        group.status = CuratedEventGroup.STATUS_PROVISIONAL
        group.provisional_at = timezone.now()
        group.schedule_digest = "manual-bypass"

        with self.assertRaisesMessage(ValidationError, "audited group lifecycle"):
            group.save()

    def test_saved_group_identity_is_immutable(self):
        group, _, _ = self.make_group()
        other_event = self.make_event(title="Other identity")
        group.event = other_event

        with self.assertRaisesMessage(ValidationError, "immutable"):
            group.save(update_fields=["event"])

    def test_populated_draft_cannot_be_deleted(self):
        group, _, _ = self.make_group()

        with self.assertRaisesMessage(ValidationError, "unused draft"):
            group.delete()

    def test_viable_group_moves_draft_to_provisional_with_metrics(self):
        group, _, _ = self.make_viable_group()

        metrics = group.mark_provisional(audit_data=self.fairness_audit())

        self.assertEqual(group.status, CuratedEventGroup.STATUS_PROVISIONAL)
        self.assertIsNotNone(group.provisional_at)
        self.assertEqual(metrics["minimum_dates"], 5)
        self.assertEqual(group.viability_summary, metrics)
        self.assertEqual(group.audit_data["schema_version"], 1)
        self.assertEqual(group.audit_data["policy_version"], group.policy_version)
        self.assertEqual(group.audit_data["seed"], group.seed)
        self.assertEqual(group.audit_data["generation"], group.generation)
        self.assertTrue(group.audit_data["fairness_decision"])

    def test_provisional_requires_privacy_safe_fairness_evidence(self):
        group, _, _ = self.make_viable_group()

        with self.assertRaisesMessage(ValidationError, "fairness_decision"):
            group.mark_provisional()
        with self.assertRaisesMessage(ValidationError, "non-sensitive"):
            group.mark_provisional(
                audit_data={"fairness_decision": {"gender_shortage": "F"}}
            )

    def test_track_audit_can_describe_a_component_larger_than_one_group(self):
        group, _, _ = self.make_viable_group()

        group.mark_provisional(
            audit_data=self.fairness_audit(
                track_size=18,
                track_ordinal=2,
                target_achieved=False,
            )
        )

        self.assertEqual(group.audit_data["fairness_decision"]["track_size"], 18)
        self.assertEqual(group.audit_data["fairness_decision"]["track_ordinal"], 2)

    def test_lock_requires_every_member_to_be_checked_in(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())

        with self.assertRaisesMessage(ValidationError, "checked in"):
            group.lock()

        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        group.lock()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_LOCKED)
        self.assertIsNotNone(group.locked_at)

    def test_certified_projection_freezes_event_capacity_configuration(self):
        group, _, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        self.event.max_participants = 18

        with self.assertRaisesMessage(ValidationError, "cannot change"):
            self.event.full_clean()

    def test_certified_projection_freezes_price_and_age(self):
        group, _, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())

        changes = (
            {"registration_fee": Decimal("15.00")},
            {"min_age": 21},
        )
        for changed_fields in changes:
            with self.subTest(changed_fields=changed_fields):
                event = MeetupEvent.objects.get(pk=self.event.pk)
                for field_name, value in changed_fields.items():
                    setattr(event, field_name, value)

                with self.assertRaisesMessage(ValidationError, "cannot change"):
                    event.full_clean()

    def test_profile_edit_after_provisional_does_not_rewrite_frozen_proof(self):
        group, registrations, _ = self.make_viable_group()
        preference = registrations[0].preference
        preference.preferred_genders = ["M"]
        preference.preferred_age_min = 25
        preference.preferred_age_max = 40
        preference.languages = ["en"]
        preference.save(
            update_fields=[
                "preferred_genders",
                "preferred_age_min",
                "preferred_age_max",
                "languages",
            ]
        )
        group.mark_provisional(audit_data=self.fairness_audit())
        certified_digest = group.schedule_digest
        edited_profile = registrations[1].user.crushprofile
        edited_profile.gender = "F"
        edited_profile.date_of_birth = date(1950, 1, 1)
        edited_profile.save(update_fields=["gender", "date_of_birth"])
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")

        group.lock()

        self.assertEqual(group.status, CuratedEventGroup.STATUS_LOCKED)
        self.assertEqual(group.schedule_digest, certified_digest)

    def test_locked_roster_schedule_and_group_are_immutable(self):
        group, registrations, memberships = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        group.lock()
        membership = memberships[0]
        pairing = group.pairings.first()
        participant = pairing.participants.first()

        with self.assertRaises(ValidationError):
            membership.release(reason="silent move")
        with self.assertRaises(ValidationError):
            pairing.delete()
        participant.seat = "b" if participant.seat == "a" else "a"
        with self.assertRaises(ValidationError):
            participant.save()
        with self.assertRaises(ValidationError):
            group.save()

    def test_organiser_reprojection_cannot_degrade_a_locked_group(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        group.lock()

        with self.assertRaisesMessage(ValidationError, "never ordinary reprojection"):
            group.degrade_for_reprojection(
                reason=CuratedEventGroup.DEGRADATION_REASON_ORGANISER
            )

    def test_reopening_is_explicit_and_clears_stale_viability(self):
        group, _, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())

        group.reopen_draft()

        self.assertEqual(group.status, CuratedEventGroup.STATUS_DRAFT)
        self.assertIsNone(group.provisional_at)
        self.assertEqual(group.viability_summary, {})
        self.assertEqual(group.audit_data, {})

    def test_invited_or_paid_member_pins_a_provisional_group(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(pk=registrations[0].pk).update(
            status="pending"
        )

        with self.assertRaisesMessage(ValidationError, "cannot be reopened"):
            group.reopen_draft()
        with self.assertRaisesMessage(ValidationError, "cannot be cancelled"):
            group.cancel(reason="unsafe")

    def test_payment_confirmation_pins_even_an_applied_member(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(pk=registrations[0].pk).update(
            payment_confirmed=True
        )

        with self.assertRaisesMessage(ValidationError, "cannot be reopened"):
            group.reopen_draft()

    def test_invitation_freezes_provisional_children(self):
        group, registrations, memberships = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(pk=registrations[0].pk).update(
            status="pending"
        )

        with self.assertRaisesMessage(ValidationError, "atomic reprojection"):
            memberships[1].release(reason="silent roster edit")
        with self.assertRaisesMessage(ValidationError, "atomic reprojection"):
            group.pairings.first().delete()

    def test_member_status_exit_marks_provisional_group_degraded(self):
        group, registrations, memberships = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(pk=registrations[0].pk).update(
            status="pending"
        )
        departing = EventRegistration.objects.get(pk=registrations[0].pk)
        departing.status = "cancelled"

        departing.save(update_fields=["status"])

        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(
            group.degradation_reason,
            CuratedEventGroup.DEGRADATION_REASON_STATUS_EXIT,
        )
        self.assertEqual(
            departing._curated_group_degraded_event_ids,
            (self.event.pk,),
        )
        self.assertFalse(
            CuratedEventGroup.objects.filter(
                event=self.event,
                status__in=(
                    CuratedEventGroup.STATUS_PROVISIONAL,
                    CuratedEventGroup.STATUS_LOCKED,
                ),
            ).exists()
        )
        with self.assertRaisesMessage(ValidationError, "atomic reprojection"):
            memberships[1].release(reason="unsafe")

    def test_atomic_remedy_can_release_then_cancel_a_degraded_roster(self):
        group, registrations, memberships = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        departing = registrations[0]
        departing.status = "cancelled"
        departing.save(update_fields=["status"])
        group.refresh_from_db()

        with transaction.atomic():
            released_registration_ids = group.release_degraded_memberships_for_remedy(
                reason="replacement generation prepared"
            )
            group.cancel(reason="reprojected")

        self.assertEqual(
            released_registration_ids,
            sorted(registration.pk for registration in registrations),
        )
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertEqual(
            CuratedEventGroupMembership.objects.filter(
                pk__in=[membership.pk for membership in memberships],
                released_at__isnull=True,
            ).count(),
            0,
        )

    def test_cancelling_a_draft_releases_assignments_without_erasing_history(self):
        group, registrations, memberships = self.make_group()

        group.cancel(reason="superseded projection")

        memberships[0].refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertIsNotNone(memberships[0].released_at)
        replacement = CuratedEventGroup.objects.create(
            event=self.event, generation=2, group_number=1
        )
        CuratedEventGroupMembership.objects.create(
            event=self.event,
            group=replacement,
            registration=registrations[0],
            position=1,
        )
        self.assertEqual(registrations[0].curated_group_memberships.count(), 2)

    def test_canonical_cancel_can_thaw_an_uninvited_provisional_group(self):
        group, _, memberships = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        certified_digest = group.schedule_digest

        group.cancel(reason="projection withdrawn")

        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertEqual(group.schedule_digest, certified_digest)
        self.assertEqual(
            CuratedEventGroupMembership.objects.filter(
                pk__in=[membership.pk for membership in memberships],
                released_at__isnull=False,
            ).count(),
            6,
        )

    def test_attendance_cannot_leave_a_locked_group_through_plain_save(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        group.lock()
        attendee = EventRegistration.objects.get(pk=registrations[0].pk)
        attendee.status = "confirmed"

        with self.assertRaisesMessage(ValidationError, "locked final group"):
            attendee.save()

    def test_attendance_exit_rechecks_after_registration_group_locks(self):
        from unittest.mock import patch

        from django.db.models import QuerySet

        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        attendee = EventRegistration.objects.get(pk=registrations[0].pk)
        attendee.status = "confirmed"
        locked_sql = []
        group_finalised_before_registration_lock = False
        real_fetch_all = QuerySet._fetch_all

        def finalise_then_record_lock(queryset):
            nonlocal group_finalised_before_registration_lock
            if queryset._result_cache is None and getattr(
                queryset.query, "select_for_update", False
            ):
                sql = str(queryset.query)
                if (
                    not group_finalised_before_registration_lock
                    and "crush_lu_eventregistration" in sql.lower()
                ):
                    CuratedEventGroup.objects.filter(pk=group.pk).update(
                        status=CuratedEventGroup.STATUS_LOCKED,
                        locked_at=timezone.now(),
                    )
                    group_finalised_before_registration_lock = True
                locked_sql.append(sql)
            return real_fetch_all(queryset)

        with patch.object(QuerySet, "_fetch_all", finalise_then_record_lock):
            with self.assertRaisesMessage(ValidationError, "locked final group"):
                attendee.save(update_fields=["status"])

        self.assertGreaterEqual(len(locked_sql), 2)
        self.assertIn("crush_lu_eventregistration", locked_sql[0].lower())
        self.assertIn("crush_lu_curatedeventgroup", locked_sql[1].lower())
        self.assertFalse(
            any("crush_lu_meetupevent" in sql.lower() for sql in locked_sql)
        )


class AdminSelectionAndVisibilityTests(CuratedGroupPersistenceBase):
    def test_group_configuration_and_read_only_projection_are_visible(self):
        admin_object = MeetupEventAdmin(MeetupEvent, AdminSite())
        capacity_fields = dict(admin_object.fieldsets)["Capacity & Requirements"][
            "fields"
        ]
        inline = CuratedEventGroupInline(CuratedEventGroup, AdminSite())

        self.assertIn(("group_size", "planned_groups"), capacity_fields)
        self.assertFalse(inline.has_add_permission(self.admin_request(), self.event))
        self.assertFalse(inline.has_delete_permission(self.admin_request(), self.event))

    def test_event_inline_is_bounded_to_latest_projection_generation(self):
        CuratedEventGroup.objects.create(event=self.event, generation=1, group_number=1)
        latest = CuratedEventGroup.objects.create(
            event=self.event, generation=2, group_number=1
        )
        inline = CuratedEventGroupInline(CuratedEventGroup, AdminSite())

        visible = list(
            inline.get_queryset(self.admin_request()).filter(event=self.event)
        )

        self.assertEqual([group.pk for group in visible], [latest.pk])

    def test_paid_parallel_applicant_is_not_invited_before_viable_selection(self):
        self.event.registration_fee = Decimal("15.00")
        self.event.save(update_fields=["registration_fee"])
        applicant = self.make_registration(1)

        self.run_confirm_action([applicant])

        applicant.refresh_from_db()
        self.assertEqual(applicant.status, "applied")

    def test_paid_applicant_in_current_provisional_group_is_invited_to_pay(self):
        self.event.registration_fee = Decimal("15.00")
        self.event.save(update_fields=["registration_fee"])
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())

        self.run_confirm_action(registrations)

        self.assertEqual(
            EventRegistration.objects.filter(
                pk__in=[registration.pk for registration in registrations],
                status="pending",
            ).count(),
            6,
        )
        self.assertFalse(
            EventRegistration.objects.filter(
                pk__in=[registration.pk for registration in registrations],
                payment_confirmed=True,
            ).exists()
        )

    def test_partial_provisional_roster_cannot_trigger_payment(self):
        self.event.registration_fee = Decimal("15.00")
        self.event.save(update_fields=["registration_fee"])
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())

        self.run_confirm_action([registrations[0]])

        self.assertEqual(
            EventRegistration.objects.filter(
                pk__in=[registration.pk for registration in registrations],
                status="applied",
            ).count(),
            6,
        )

    def test_selection_cannot_skip_an_earlier_fairness_group(self):
        self.event.max_participants = 18
        self.event.planned_groups = 3
        self.event.save(update_fields=["max_participants", "planned_groups"])
        first, first_registrations, _ = self.make_viable_group(
            group_number=1, registration_start=0
        )
        second, _, _ = self.make_viable_group(group_number=2, registration_start=10)
        third, third_registrations, _ = self.make_viable_group(
            group_number=3, registration_start=20
        )
        first.mark_provisional(audit_data=self.fairness_audit(track_ordinal=1))
        second.mark_provisional(audit_data=self.fairness_audit(track_ordinal=2))
        third.mark_provisional(audit_data=self.fairness_audit(track_ordinal=3))

        self.run_confirm_action(first_registrations + third_registrations)

        self.assertEqual(
            EventRegistration.objects.filter(
                pk__in=[
                    registration.pk
                    for registration in first_registrations + third_registrations
                ],
                status="applied",
            ).count(),
            12,
        )

    def test_newer_draft_preview_does_not_block_payable_generation(self):
        self.event.registration_fee = Decimal("15.00")
        self.event.save(update_fields=["registration_fee"])
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        CuratedEventGroup.objects.create(
            event=self.event,
            generation=2,
            group_number=1,
        )

        self.run_confirm_action(registrations)

        self.assertEqual(
            EventRegistration.objects.filter(
                pk__in=[registration.pk for registration in registrations],
                status="pending",
            ).count(),
            6,
        )

    def test_admin_form_cannot_bypass_parallel_group_selection(self):
        from crush_lu.admin.events import EventRegistrationAdminForm

        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        registration = registrations[0]
        form = EventRegistrationAdminForm(
            data={
                "event": self.event.pk,
                "user": registration.user_id,
                "status": "confirmed",
            },
            instance=registration,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("status", form.errors)

    def test_admin_cannot_record_payment_before_explicit_group_selection(self):
        from crush_lu.admin.events import EventRegistrationAdminForm

        registration = self.make_registration(86)
        form = EventRegistrationAdminForm(
            data={
                "event": self.event.pk,
                "user": registration.user_id,
                "status": "applied",
                "payment_confirmed": True,
            },
            instance=registration,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("payment_confirmed", form.errors)

    def test_registration_with_group_history_cannot_move_events(self):
        _, registrations, _ = self.make_group()
        registration = registrations[0]
        other_event = self.make_event(title="Move target")
        registration.event = other_event

        with self.assertRaisesMessage(ValidationError, "group history"):
            registration.save(update_fields=["event"])

    def test_seat_holding_registration_cannot_move_into_parallel_event(self):
        direct = self.make_event(
            title="Direct source",
            registration_mode="direct",
            group_size=None,
            planned_groups=None,
        )
        registration = self.make_registration(87, event=direct)
        EventRegistration.objects.filter(pk=registration.pk).update(status="confirmed")
        registration.refresh_from_db()
        registration.event = self.event

        with self.assertRaisesMessage(ValidationError, "cannot be moved"):
            registration.save(update_fields=["event"])

    def test_waitlist_detour_cannot_bypass_parallel_group_selection(self):
        registration = self.make_registration(88)
        registration.status = "waitlist"
        registration.save(update_fields=["status"])
        registration.status = "confirmed"

        with self.assertRaisesMessage(ValidationError, "bulk Confirm action"):
            registration.save(update_fields=["status"])

    def test_new_parallel_registration_cannot_start_with_a_seat(self):
        existing = self.make_registration(89)
        another_event = self.make_event(title="Other parallel evening")
        registration = EventRegistration(
            event=another_event,
            user=existing.user,
            status="confirmed",
        )

        with self.assertRaisesMessage(ValidationError, "bulk Confirm action"):
            registration.save()

    def test_waitlist_action_remedies_a_provisional_group_immediately(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        admin_object = EventRegistrationAdmin(EventRegistration, AdminSite())

        admin_object.move_to_waitlist(
            self.admin_request(),
            EventRegistration.objects.filter(pk=registrations[0].pk),
        )

        group.refresh_from_db()
        registrations[0].refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertFalse(group.memberships.filter(released_at__isnull=True).exists())
        self.assertEqual(registrations[0].status, "waitlist")

    def test_waitlist_action_cannot_change_a_locked_group_member(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        group.lock()
        admin_object = EventRegistrationAdmin(EventRegistration, AdminSite())

        admin_object.move_to_waitlist(
            self.admin_request(),
            EventRegistration.objects.filter(pk=registrations[0].pk),
        )

        group.refresh_from_db()
        registrations[0].refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_LOCKED)
        self.assertEqual(registrations[0].status, "attended")

    def test_selection_locks_event_before_registration(self):
        from unittest.mock import patch

        from django.db.models import QuerySet

        legacy = self.make_event(
            title="Legacy curated",
            group_size=None,
            planned_groups=None,
        )
        registration = self.make_registration(77, event=legacy)
        locked_sql = []
        real_fetch_all = QuerySet._fetch_all

        def record_lock(queryset):
            if queryset._result_cache is None and getattr(
                queryset.query, "select_for_update", False
            ):
                locked_sql.append(str(queryset.query))
            return real_fetch_all(queryset)

        with patch.object(QuerySet, "_fetch_all", record_lock):
            self.run_confirm_action([registration])

        self.assertGreaterEqual(len(locked_sql), 2)
        self.assertIn("crush_lu_meetupevent", locked_sql[0].lower())
        self.assertIn("crush_lu_eventregistration", locked_sql[1].lower())

    def test_child_mutation_uses_event_registration_group_lock_order(self):
        from unittest.mock import patch

        from django.db.models import QuerySet

        group = CuratedEventGroup.objects.create(
            event=self.event,
            generation=1,
            group_number=1,
        )
        registration = self.make_registration(90)
        locked_sql = []
        real_fetch_all = QuerySet._fetch_all

        def record_lock(queryset):
            if queryset._result_cache is None and getattr(
                queryset.query, "select_for_update", False
            ):
                locked_sql.append(str(queryset.query))
            return real_fetch_all(queryset)

        with patch.object(QuerySet, "_fetch_all", record_lock):
            CuratedEventGroupMembership.objects.create(
                event=self.event,
                group=group,
                registration=registration,
                position=1,
            )

        self.assertGreaterEqual(len(locked_sql), 3)
        self.assertIn("crush_lu_meetupevent", locked_sql[0].lower())
        self.assertIn("crush_lu_eventregistration", locked_sql[1].lower())
        self.assertIn("crush_lu_curatedeventgroup", locked_sql[2].lower())


class AccountErasureCompatibilityTests(CuratedGroupPersistenceBase):
    def test_deleting_member_account_cascades_derived_group_rows(self):
        group, registrations, memberships = self.make_viable_group()
        erased_registration = registrations[0]
        erased_registration_id = erased_registration.pk
        erased_user = erased_registration.user
        participant_count = group.pairing_participants.filter(
            registration=erased_registration
        ).count()

        erased_user.delete()

        self.assertFalse(
            CuratedEventGroupMembership.objects.filter(pk=memberships[0].pk).exists()
        )
        self.assertEqual(
            group.pairing_participants.filter(
                registration_id=erased_registration_id
            ).count(),
            0,
        )
        self.assertGreater(participant_count, 0)
        self.assertTrue(CuratedEventGroup.objects.filter(pk=group.pk).exists())
        with self.assertRaises(ValidationError):
            group.schedule_viability()

    def test_erasure_marks_a_provisional_group_degraded_before_cascade(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())

        registrations[0].user.delete()

        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(
            group.degradation_reason,
            CuratedEventGroup.DEGRADATION_REASON_ERASURE,
        )
        self.assertEqual(group.audit_data["degradation"]["from_status"], "provisional")
        with self.assertRaises(ValidationError):
            group.schedule_viability(evaluate_preferences=False)

    def test_erasure_marks_a_locked_group_degraded_before_cascade(self):
        group, registrations, _ = self.make_viable_group()
        group.mark_provisional(audit_data=self.fairness_audit())
        EventRegistration.objects.filter(
            pk__in=[registration.pk for registration in registrations]
        ).update(status="attended")
        group.lock()

        registrations[0].user.delete()

        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(group.audit_data["degradation"]["from_status"], "locked")
        self.assertIsNotNone(group.locked_at)
