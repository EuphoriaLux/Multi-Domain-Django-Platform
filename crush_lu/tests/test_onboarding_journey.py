"""
Tests for crush_lu.onboarding — the journey step data module + the helper
that the stepper template depends on.
"""
import datetime
from unittest.mock import MagicMock

from django.test import TestCase

from crush_lu import onboarding


class JourneyConstantsTests(TestCase):

    def test_seven_steps(self):
        self.assertEqual(len(onboarding.JOURNEY_STEPS), 7)

    def test_step_numbers_are_sequential(self):
        numbers = [s.n for s in onboarding.JOURNEY_STEPS]
        self.assertEqual(numbers, list(range(1, 8)))

    def test_three_chapters(self):
        self.assertEqual(set(onboarding.JOURNEY_CHAPTERS.keys()), {1, 2, 3})

    def test_last_step_is_locked(self):
        last = onboarding.JOURNEY_STEPS[-1]
        self.assertTrue(last.locked)
        self.assertEqual(last.key, "call")

    def test_under_review_precedes_meet_coach(self):
        """Temporal order: users hit 'under review' before a coach claims."""
        keys = [s.key for s in onboarding.JOURNEY_STEPS]
        self.assertLess(keys.index("submitted"), keys.index("coach"))

    def test_step_url_names_all_point_to_existing_url(self):
        from django.urls import reverse, NoReverseMatch
        for step_n, url_name in onboarding.STEP_URL_NAMES.items():
            with self.subTest(step=step_n, url_name=url_name):
                try:
                    reverse(url_name)
                except NoReverseMatch:
                    self.fail(
                        f"STEP_URL_NAMES[{step_n}] = {url_name!r} does not resolve"
                    )


class AnnotateStepsTests(TestCase):

    def test_annotate_returns_state_per_step(self):
        steps = onboarding.annotate_steps(current=3)
        states = [s["state"] for s in steps]
        self.assertEqual(states, [
            "completed", "completed", "active",
            "upcoming", "upcoming", "upcoming", "upcoming",
        ])

    def test_first_step_active_has_no_completed(self):
        steps = onboarding.annotate_steps(current=1)
        self.assertEqual(steps[0]["state"], "active")
        self.assertTrue(all(s["state"] == "upcoming" for s in steps[1:]))

    def test_last_step_active_all_others_completed(self):
        steps = onboarding.annotate_steps(current=7)
        self.assertEqual(steps[-1]["state"], "active")
        self.assertTrue(all(s["state"] == "completed" for s in steps[:-1]))


class StepperContextTests(TestCase):

    def test_context_carries_everything_template_needs(self):
        ctx = onboarding.stepper_context(current=4)
        self.assertEqual(ctx["journey_current"], 4)
        self.assertEqual(ctx["journey_total"], 7)
        self.assertEqual(len(ctx["journey_steps"]), 7)
        self.assertEqual(ctx["journey_active"].key, "profile")
        self.assertEqual(ctx["journey_chapter"].n, 2)

    def test_progress_pct_is_zero_at_step_1(self):
        ctx = onboarding.stepper_context(current=1)
        self.assertEqual(ctx["journey_progress_pct"], 0)

    def test_progress_pct_reaches_track_span_at_step_7(self):
        ctx = onboarding.stepper_context(current=7)
        # 100% of the track span; track is 6/7 of the full row.
        self.assertAlmostEqual(ctx["journey_progress_pct"], 6 / 7 * 100, places=1)


class GetCurrentStepTests(TestCase):
    """
    get_current_step derives the step from profile state. Use lightweight stub
    objects rather than spinning up the full CrushProfile (which requires
    fixtures).
    """

    class _Stub:
        def __init__(self, **kw):
            self.welcome_seen_at = kw.get("welcome_seen_at")
            self.phone_verified = kw.get("phone_verified", False)
            self.coach_id = kw.get("coach_id")
            self.coach_intro_seen_at = kw.get("coach_intro_seen_at")
            self.completion_status = kw.get("completion_status", "not_started")
            # Django's default reverse accessor for ProfileSubmission.profile
            # (no related_name set on the FK). Tests pass a mock manager here.
            self.profilesubmission_set = kw.get("profilesubmission_set")

    def test_null_profile_is_step_1(self):
        self.assertEqual(onboarding.get_current_step(None), 1)

    def test_no_welcome_seen_is_step_1(self):
        p = self._Stub()
        self.assertEqual(onboarding.get_current_step(p), 1)

    def test_welcome_seen_but_no_phone_is_step_2(self):
        import datetime
        p = self._Stub(welcome_seen_at=datetime.datetime(2026, 1, 1))
        self.assertEqual(onboarding.get_current_step(p), 2)

    def test_phone_verified_but_no_coach_intro_is_step_3(self):
        import datetime
        p = self._Stub(
            welcome_seen_at=datetime.datetime(2026, 1, 1),
            phone_verified=True,
        )
        self.assertEqual(onboarding.get_current_step(p), 3)

    def test_coach_intro_seen_but_not_submitted_is_step_4(self):
        p = self._Stub(
            welcome_seen_at=datetime.datetime(2026, 1, 1),
            phone_verified=True,
            coach_intro_seen_at=datetime.datetime(2026, 1, 2),
            completion_status="step2",
        )
        self.assertEqual(onboarding.get_current_step(p), 4)

    def _submitted_stub(self, submission):
        """Build a stub whose submission-set returns a single submission."""
        manager = MagicMock()
        manager.order_by.return_value.first.return_value = submission
        return self._Stub(
            welcome_seen_at=datetime.datetime(2026, 1, 1),
            phone_verified=True,
            coach_intro_seen_at=datetime.datetime(2026, 1, 2),
            completion_status="submitted",
            profilesubmission_set=manager,
        )

    def test_submitted_but_no_submission_row_falls_back_to_step_4(self):
        # Edge case: completion_status flipped to submitted but the submission
        # row did not persist. Bounce the user back to the wizard.
        manager = MagicMock()
        manager.order_by.return_value.first.return_value = None
        p = self._Stub(
            welcome_seen_at=datetime.datetime(2026, 1, 1),
            phone_verified=True,
            coach_intro_seen_at=datetime.datetime(2026, 1, 2),
            completion_status="submitted",
            profilesubmission_set=manager,
        )
        self.assertEqual(onboarding.get_current_step(p), 4)

    def test_pending_no_coach_is_step_5_under_review(self):
        submission = MagicMock(status="pending", assigned_at=None)
        self.assertEqual(onboarding.get_current_step(self._submitted_stub(submission)), 5)

    def test_pending_with_coach_assigned_is_step_6_meet_coach(self):
        submission = MagicMock(status="pending", assigned_at=datetime.datetime(2026, 1, 3))
        self.assertEqual(onboarding.get_current_step(self._submitted_stub(submission)), 6)

    def test_recontact_coach_no_assignment_is_step_5(self):
        submission = MagicMock(status="recontact_coach", assigned_at=None)
        self.assertEqual(onboarding.get_current_step(self._submitted_stub(submission)), 5)

    def test_recontact_coach_with_assignment_is_step_6(self):
        submission = MagicMock(
            status="recontact_coach",
            assigned_at=datetime.datetime(2026, 1, 3),
        )
        self.assertEqual(onboarding.get_current_step(self._submitted_stub(submission)), 6)

    def test_revision_sends_user_back_to_step_4(self):
        submission = MagicMock(status="revision", assigned_at=datetime.datetime(2026, 1, 3))
        self.assertEqual(onboarding.get_current_step(self._submitted_stub(submission)), 4)

    def test_rejected_returns_sentinel(self):
        submission = MagicMock(status="rejected", assigned_at=datetime.datetime(2026, 1, 3))
        self.assertEqual(
            onboarding.get_current_step(self._submitted_stub(submission)),
            onboarding.STEP_REJECTED,
        )

    def test_approved_but_no_call_is_step_7(self):
        submission = MagicMock(
            status="approved",
            assigned_at=datetime.datetime(2026, 1, 3),
            review_call_completed=False,
        )
        self.assertEqual(onboarding.get_current_step(self._submitted_stub(submission)), 7)


class UrlNameForStepTests(TestCase):

    def test_rejected_sentinel_maps_to_profile_rejected(self):
        self.assertEqual(
            onboarding.url_name_for_step(onboarding.STEP_REJECTED),
            "crush_lu:profile_rejected",
        )

    def test_step_5_maps_to_profile_submitted_under_review(self):
        # After the step-5/6 reorder, step 5 is "under review" not "meet coach".
        self.assertEqual(onboarding.url_name_for_step(5), "crush_lu:profile_submitted")

    def test_step_6_maps_to_meet_coach(self):
        self.assertEqual(onboarding.url_name_for_step(6), "crush_lu:onboarding_meet_coach")

    def test_out_of_range_step_clamps(self):
        self.assertEqual(onboarding.url_name_for_step(42), "crush_lu:onboarding_screening_call")
        self.assertEqual(onboarding.url_name_for_step(-99), "crush_lu:welcome")
