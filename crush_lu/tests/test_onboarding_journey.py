"""
Tests for crush_lu.onboarding — the journey step data module + the helper
that the stepper template depends on.
"""
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
        import datetime
        p = self._Stub(
            welcome_seen_at=datetime.datetime(2026, 1, 1),
            phone_verified=True,
            coach_intro_seen_at=datetime.datetime(2026, 1, 2),
            completion_status="step2",
        )
        self.assertEqual(onboarding.get_current_step(p), 4)
