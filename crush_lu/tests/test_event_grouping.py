"""Deterministic grouping and concrete schedule tests for curated events."""

from time import perf_counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from crush_lu.services.event_grouping import (
    GROUPING_POLICY_VERSION,
    MAX_GROUPING_APPLICANTS,
    MAX_PROJECTED_GROUP_SIZE,
    EventApplicant,
    GroupingGroupSizeTooLarge,
    GroupingPoolTooLarge,
    _applicant as _adapt_registration,
    build_compatibility_graph,
    count_mutual_matches,
    load_grouping_candidates,
    project_groups,
)


def _applicant(
    registration_id,
    *,
    gender="NB",
    preferred_genders=(),
    languages=("en",),
    age=30,
    preferred_age_min=18,
    preferred_age_max=99,
    status="applied",
    pinned=False,
    eligible_for_grouping=True,
    incomplete_reasons=(),
):
    return EventApplicant(
        user_id=registration_id + 1_000,
        registration_id=registration_id,
        gender=gender,
        age=age,
        preferred_genders=tuple(preferred_genders),
        preferred_age_min=preferred_age_min,
        preferred_age_max=preferred_age_max,
        languages=tuple(languages),
        status=status,
        pinned=pinned,
        eligible_for_grouping=eligible_for_grouping,
        incomplete_reasons=tuple(incomplete_reasons),
    )


def _event(*, group_size, group_limit, pk=951, **overrides):
    values = {
        "gender_limits_active": False,
        "min_age": 18,
        "max_age": 99,
        "pk": pk,
        "group_size": group_size,
        "planned_groups": group_limit,
        "max_groups": group_limit,
        "max_participants": group_size * group_limit,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class CompatibilityGraphTests(SimpleTestCase):
    def test_grouping_loader_includes_and_pins_seat_holding_statuses(self):
        def registration(key, status):
            return SimpleNamespace(
                pk=key,
                user_id=key + 100,
                user=SimpleNamespace(crushprofile=SimpleNamespace(gender="NB", age=30)),
                preference=SimpleNamespace(
                    preferred_genders=[],
                    preferred_age_min=18,
                    preferred_age_max=99,
                    languages=[],
                ),
                status=status,
            )

        manager = MagicMock()
        queryset = manager.filter.return_value
        queryset.select_related.return_value = queryset
        queryset.order_by.return_value = [
            registration(1, "applied"),
            registration(2, "confirmed"),
        ]

        candidates = load_grouping_candidates(
            SimpleNamespace(eventregistration_set=manager)
        )

        manager.filter.assert_called_once_with(
            status__in=("applied", "pending", "confirmed", "attended")
        )
        self.assertFalse(candidates[0].pinned)
        self.assertTrue(candidates[1].pinned)

    def test_missing_preference_or_identity_fails_closed_for_grouping(self):
        profile = SimpleNamespace(gender=None, age=None)
        registration = SimpleNamespace(
            pk=1,
            user_id=101,
            user=SimpleNamespace(crushprofile=profile),
            status="applied",
        )

        applicant = _adapt_registration(registration)

        self.assertFalse(applicant.eligible_for_grouping)
        self.assertEqual(
            applicant.incomplete_reasons,
            ("missing_event_preferences", "missing_gender", "missing_age"),
        )

    def test_explicit_empty_preference_lists_remain_open_to_all(self):
        profile = SimpleNamespace(gender="NB", age=32)
        registration = SimpleNamespace(
            pk=1,
            user_id=101,
            user=SimpleNamespace(crushprofile=profile),
            preference=SimpleNamespace(
                preferred_genders=[],
                preferred_age_min=18,
                preferred_age_max=99,
                languages=[],
            ),
            status="applied",
        )

        applicant = _adapt_registration(registration)

        self.assertTrue(applicant.eligible_for_grouping)
        self.assertEqual(applicant.incomplete_reasons, ())
        self.assertEqual(applicant.preferred_genders, ())
        self.assertEqual(applicant.languages, ())

    def test_oversized_pool_fails_explicitly_without_truncation(self):
        applicants = [_applicant(key) for key in range(1, MAX_GROUPING_APPLICANTS + 2)]

        with self.assertRaisesMessage(
            GroupingPoolTooLarge,
            f"more than {MAX_GROUPING_APPLICANTS} applicants",
        ):
            build_compatibility_graph(applicants)

    def test_match_signal_excludes_incomplete_viewer_or_applicant(self):
        viewer = _applicant(1)
        eligible = _applicant(2)
        incomplete = _applicant(
            3,
            eligible_for_grouping=False,
            incomplete_reasons=("missing_event_preferences",),
        )

        self.assertEqual(count_mutual_matches(viewer, [eligible, incomplete]), 1)
        incomplete_viewer = _applicant(
            4,
            age=None,
            eligible_for_grouping=False,
            incomplete_reasons=("missing_age",),
        )
        self.assertEqual(count_mutual_matches(incomplete_viewer, [eligible]), 0)

    def test_hard_gender_preferences_are_mutual(self):
        applicants = [
            _applicant(1, gender="M", preferred_genders=("F",)),
            _applicant(2, gender="F", preferred_genders=("M",)),
            _applicant(3, gender="F", preferred_genders=("F",)),
        ]

        graph = build_compatibility_graph(applicants)

        self.assertEqual(graph.edges, ((1, 2),))
        projection = project_groups(
            _event(group_size=2, group_limit=1),
            applicants,
            minimum_dates=1,
            target_dates=1,
        )
        self.assertEqual(projection.selected_registration_ids, (1, 2))
        self.assertEqual(projection.infeasible_registration_ids, (3,))

    def test_language_and_age_filters_are_mutual_without_connect_fields(self):
        applicants = [
            _applicant(
                1,
                age=29,
                languages=("fr",),
                preferred_age_min=25,
                preferred_age_max=35,
            ),
            _applicant(
                2,
                age=31,
                languages=("fr", "en"),
                preferred_age_min=28,
                preferred_age_max=32,
            ),
            _applicant(3, age=50, languages=("de",)),
        ]

        graph = build_compatibility_graph(applicants)

        self.assertEqual(graph.edges, ((1, 2),))


class GroupProjectionTests(SimpleTestCase):
    def test_event_gender_pool_cap_is_enforced_during_projection(self):
        event = _event(
            group_size=6,
            group_limit=1,
            gender_limits_active=True,
            max_participants_m=5,
            max_participants_f=6,
            max_participants_nb=6,
        )
        applicants = [_applicant(key, gender="M") for key in range(1, 7)]

        projection = project_groups(
            event,
            applicants,
            minimum_dates=5,
            target_dates=7,
        )

        self.assertEqual(projection.viable_groups, ())
        self.assertEqual(projection.selected_registration_ids, ())

    def test_event_age_range_fails_closed_inside_the_projector(self):
        event = _event(group_size=6, group_limit=1, min_age=30, max_age=40)
        applicants = [
            *[_applicant(key, age=32) for key in range(1, 7)],
            _applicant(7, age=29),
        ]

        projection = project_groups(
            event,
            applicants,
            minimum_dates=5,
            target_dates=7,
        )

        self.assertEqual(projection.selected_registration_ids, tuple(range(1, 7)))
        self.assertEqual(
            dict(projection.ineligibility_reasons)[7],
            ("outside_event_age_range",),
        )

    def test_adversarial_group_size_is_rejected_before_graph_construction(self):
        applicants = [_applicant(key) for key in range(1, 501)]
        event = _event(group_size=500, group_limit=1, pk=950)

        with patch(
            "crush_lu.services.event_grouping.build_compatibility_graph"
        ) as build_graph:
            with self.assertRaisesMessage(
                GroupingGroupSizeTooLarge,
                f"limit of {MAX_PROJECTED_GROUP_SIZE}",
            ):
                project_groups(event, applicants)

        build_graph.assert_not_called()

    def test_configured_group_count_is_bounded_by_possible_groups(self):
        projection = project_groups(
            _event(group_size=6, group_limit=10**9, pk=949),
            [_applicant(key) for key in range(1, 7)],
            minimum_dates=5,
            target_dates=7,
        )

        self.assertEqual(projection.group_limit, 10**9)
        self.assertEqual(len(projection.viable_groups), 1)

    def test_projection_is_deterministic_across_input_order(self):
        applicants = [_applicant(key) for key in range(1, 13)]
        event = _event(group_size=6, group_limit=2)

        first = project_groups(event, applicants, minimum_dates=5, target_dates=7)
        second = project_groups(
            event,
            list(reversed(applicants)),
            minimum_dates=5,
            target_dates=7,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.policy_version, GROUPING_POLICY_VERSION)
        self.assertEqual(len(first.deterministic_seed), 20)
        self.assertEqual(len(first.viable_groups), 2)
        self.assertEqual(len(first.selected_registration_ids), 12)

    def test_viability_is_backed_by_no_repeat_rounds_and_fixed_membership(self):
        applicants = [_applicant(key) for key in range(1, 9)]
        projection = project_groups(
            _event(group_size=8, group_limit=1),
            applicants,
            minimum_dates=5,
            target_dates=7,
        )

        group = projection.viable_groups[0]
        self.assertTrue(group.viable)
        self.assertEqual(group.minimum_dates_achieved, 7)
        self.assertTrue(group.target_achieved)
        self.assertEqual(len(group.rounds), 7)

        expected_members = set(group.registration_ids)
        seen_pairs = set()
        for round_ in group.rounds:
            round_members = set(round_.break_registration_ids)
            for pair in round_.pairs:
                pair_key = (
                    pair.registration_a_id,
                    pair.registration_b_id,
                )
                self.assertNotIn(pair_key, seen_pairs)
                seen_pairs.add(pair_key)
                self.assertTrue(set(pair_key) <= expected_members)
                self.assertTrue(set(pair_key).isdisjoint(round_members))
                round_members.update(pair_key)
            self.assertEqual(round_members, expected_members)

        self.assertEqual(dict(group.date_counts), {key: 7 for key in range(1, 9)})

    def test_headcount_without_a_schedulable_minimum_is_not_viable(self):
        applicants = [
            *[_applicant(key, languages=("en",)) for key in range(1, 4)],
            *[_applicant(key, languages=("fr",)) for key in range(4, 7)],
        ]

        projection = project_groups(
            _event(group_size=6, group_limit=1),
            applicants,
            minimum_dates=5,
            target_dates=7,
        )

        self.assertEqual(projection.viable_groups, ())
        self.assertEqual(projection.infeasible_registration_ids, tuple(range(1, 7)))

    def test_underserved_track_gets_a_group_before_dominant_track_repeats(self):
        dominant = [_applicant(key, languages=("en",)) for key in range(1, 13)]
        underserved = [_applicant(key, languages=("fr",)) for key in range(13, 19)]

        projection = project_groups(
            _event(group_size=6, group_limit=3),
            [*dominant, *underserved],
            minimum_dates=5,
            target_dates=7,
        )

        self.assertEqual(len(projection.viable_groups), 3)
        first, second, third = projection.viable_groups
        self.assertEqual(first.compatibility_track_id, third.compatibility_track_id)
        self.assertNotEqual(first.compatibility_track_id, second.compatibility_track_id)
        self.assertEqual(
            [group.group_ordinal_in_track for group in projection.viable_groups],
            [1, 1, 2],
        )
        self.assertTrue(second.underserved_priority)
        self.assertFalse(first.underserved_priority)

    def test_flexible_bridge_does_not_hide_scarce_compatibility_community(self):
        dominant = [_applicant(key, languages=("en",)) for key in range(1, 13)]
        scarce = [_applicant(key, languages=("fr",)) for key in range(13, 19)]
        flexible_bridge = _applicant(19, languages=("en", "fr"))

        projection = project_groups(
            _event(group_size=6, group_limit=2, pk=954),
            [*dominant, *scarce, flexible_bridge],
            minimum_dates=5,
            target_dates=7,
        )

        # The bilingual member makes the reciprocal graph one connected
        # component. Category-free global-degree scarcity must still reserve a
        # viable group for the six lower-alternative applicants instead of
        # spending both groups on the dense dominant community.
        self.assertEqual(
            len({group.compatibility_track_id for group in projection.viable_groups}),
            1,
        )
        scarce_ids = set(range(13, 19))
        scarce_group = next(
            group
            for group in projection.viable_groups
            if len(set(group.registration_ids) & scarce_ids) >= 5
        )
        self.assertTrue(scarce_group.underserved_priority)
        self.assertGreater(scarce_group.alternative_scarcity_score, 0)

    def test_scarce_viable_six_precedes_second_dominant_fourteen(self):
        dominant = [_applicant(key, languages=("en",)) for key in range(1, 29)]
        scarce = [_applicant(key, languages=("fr",)) for key in range(29, 35)]
        flexible_bridge = _applicant(35, languages=("en", "fr"))

        projection = project_groups(
            _event(group_size=14, group_limit=2, pk=956),
            [*dominant, *scarce, flexible_bridge],
            minimum_dates=5,
            target_dates=7,
        )

        selected = set(projection.selected_registration_ids)
        scarce_ids = set(range(29, 35))
        self.assertTrue(scarce_ids <= selected)
        self.assertTrue(
            any(
                group.underserved_priority and set(group.registration_ids) & scarce_ids
                for group in projection.viable_groups
            )
        )

    def test_infeasible_applicants_are_excluded_explicitly(self):
        viable = [_applicant(key, languages=("en",)) for key in range(1, 7)]
        infeasible = [_applicant(key, languages=("fr",)) for key in range(7, 12)]

        projection = project_groups(
            _event(group_size=6, group_limit=2),
            [*viable, *infeasible],
            minimum_dates=5,
            target_dates=7,
        )

        self.assertEqual(projection.selected_registration_ids, tuple(range(1, 7)))
        self.assertEqual(projection.infeasible_registration_ids, tuple(range(7, 12)))
        self.assertEqual(projection.unassigned_registration_ids, tuple(range(7, 12)))

    def test_incomplete_applicant_is_reported_even_when_the_pool_is_dense(self):
        applicants = [
            *[_applicant(key) for key in range(1, 7)],
            _applicant(
                7,
                eligible_for_grouping=False,
                incomplete_reasons=("missing_event_preferences",),
            ),
        ]

        projection = project_groups(
            _event(group_size=6, group_limit=1, pk=955),
            applicants,
            minimum_dates=5,
            target_dates=7,
        )

        self.assertEqual(projection.selected_registration_ids, tuple(range(1, 7)))
        self.assertEqual(projection.infeasible_registration_ids, (7,))
        self.assertEqual(
            projection.ineligibility_reasons,
            ((7, ("missing_event_preferences",)),),
        )

    def test_feasible_pinned_member_is_retained_before_unselected_applicants(self):
        applicants = [
            _applicant(
                key, pinned=(key == 7), status="confirmed" if key == 7 else "applied"
            )
            for key in range(1, 8)
        ]

        projection = project_groups(
            _event(group_size=6, group_limit=1),
            applicants,
            minimum_dates=5,
            target_dates=7,
        )

        self.assertIn(7, projection.selected_registration_ids)
        self.assertTrue(projection.retains_all_pinned)
        self.assertEqual(projection.pinned_unassigned_registration_ids, ())

    def test_impossible_pinned_retention_is_reported_not_silently_dropped(self):
        applicants = [
            *[_applicant(key, languages=("en",)) for key in range(1, 7)],
            _applicant(
                7,
                languages=("fr",),
                status="confirmed",
                pinned=True,
            ),
        ]

        projection = project_groups(
            _event(group_size=6, group_limit=1),
            applicants,
            minimum_dates=5,
            target_dates=7,
        )

        self.assertFalse(projection.retains_all_pinned)
        self.assertEqual(projection.pinned_unassigned_registration_ids, (7,))
        self.assertEqual(projection.pinned_infeasible_registration_ids, (7,))

    def test_one_drop_resilience_is_an_exact_schedule_diagnostic(self):
        resilient = project_groups(
            _event(group_size=7, group_limit=1),
            [_applicant(key) for key in range(1, 8)],
            minimum_dates=5,
            target_dates=7,
        )
        fragile = project_groups(
            _event(group_size=6, group_limit=1, pk=952),
            [_applicant(key) for key in range(11, 17)],
            minimum_dates=5,
            target_dates=7,
        )

        self.assertTrue(resilient.viable_groups[0].one_drop_resilient)
        self.assertFalse(fragile.viable_groups[0].one_drop_resilient)

    def test_large_configured_group_uses_bounded_scheduler(self):
        # Two dense language communities joined by bilingual applicants are
        # connected but neither complete nor complete-bipartite. This forces
        # the polynomial >18-member matching path rather than a fast-path.
        applicants = [
            *[_applicant(key, languages=("en",)) for key in range(1, 21)],
            *[_applicant(key, languages=("fr",)) for key in range(21, 41)],
            _applicant(41, languages=("en", "fr")),
            _applicant(42, languages=("en", "fr")),
        ]

        started = perf_counter()
        projection = project_groups(
            _event(group_size=42, group_limit=1, pk=953),
            applicants,
            minimum_dates=5,
            target_dates=7,
        )
        elapsed = perf_counter() - started

        self.assertLess(elapsed, 10.0)
        self.assertEqual(len(projection.viable_groups), 1)
        self.assertEqual(projection.viable_groups[0].minimum_dates_achieved, 7)

    def test_many_disjoint_tracks_have_bounded_plan_composition(self):
        # Fifty independent complete components used to feed all 2,050 local
        # membership alternatives into one 50-bucket global set-packing beam.
        # Track-local optimisation must collapse those alternatives before the
        # cross-track fairness pass.
        applicants = [
            _applicant(
                key,
                languages=(f"l{(key - 1) // 10}",),
            )
            for key in range(1, 501)
        ]

        started = perf_counter()
        projection = project_groups(
            _event(group_size=10, group_limit=50, pk=957),
            applicants,
            minimum_dates=5,
            target_dates=7,
        )
        elapsed = perf_counter() - started

        self.assertLess(elapsed, 10.0)
        self.assertEqual(len(projection.viable_groups), 50)
        self.assertEqual(len(projection.selected_registration_ids), 500)
        self.assertTrue(projection.retains_all_pinned)
