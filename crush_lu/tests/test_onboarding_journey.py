"""
Tests for crush_lu.onboarding — the journey step data module + the helper
that the stepper template depends on.

The journey is a 5-step arc ending at "Get verified" — verification no longer
goes through a pre-event coach-review queue, so get_current_step derives the
step purely from CrushProfile state (no ProfileSubmission lookup).
"""

import datetime

from django.test import TestCase

from crush_lu import onboarding


class JourneyConstantsTests(TestCase):

    def test_five_steps(self):
        self.assertEqual(len(onboarding.JOURNEY_STEPS), 5)

    def test_step_numbers_are_sequential(self):
        numbers = [s.n for s in onboarding.JOURNEY_STEPS]
        self.assertEqual(numbers, list(range(1, 6)))

    def test_three_chapters(self):
        self.assertEqual(set(onboarding.JOURNEY_CHAPTERS.keys()), {1, 2, 3})

    def test_last_step_is_get_verified(self):
        last = onboarding.JOURNEY_STEPS[-1]
        self.assertEqual(last.key, "verify")
        # The final step is reachable, not a locked review/screening gate.
        self.assertFalse(last.locked)

    def test_no_step_is_locked(self):
        self.assertTrue(all(not s.locked for s in onboarding.JOURNEY_STEPS))

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
        self.assertEqual(
            states,
            [
                "completed",
                "completed",
                "active",
                "upcoming",
                "upcoming",
            ],
        )

    def test_first_step_active_has_no_completed(self):
        steps = onboarding.annotate_steps(current=1)
        self.assertEqual(steps[0]["state"], "active")
        self.assertTrue(all(s["state"] == "upcoming" for s in steps[1:]))

    def test_last_step_active_all_others_completed(self):
        steps = onboarding.annotate_steps(current=5)
        self.assertEqual(steps[-1]["state"], "active")
        self.assertTrue(all(s["state"] == "completed" for s in steps[:-1]))


class StepperContextTests(TestCase):

    def test_context_carries_everything_template_needs(self):
        ctx = onboarding.stepper_context(current=4)
        self.assertEqual(ctx["journey_current"], 4)
        self.assertEqual(ctx["journey_total"], 5)
        self.assertEqual(len(ctx["journey_steps"]), 5)
        self.assertEqual(ctx["journey_active"].key, "profile")
        self.assertEqual(ctx["journey_chapter"].n, 2)

    def test_progress_pct_is_zero_at_step_1(self):
        ctx = onboarding.stepper_context(current=1)
        self.assertEqual(ctx["journey_progress_pct"], 0)

    def test_progress_pct_reaches_track_span_at_last_step(self):
        ctx = onboarding.stepper_context(current=5)
        # 100% of the track span; track is (total-1)/total = 4/5 of the full row.
        self.assertAlmostEqual(ctx["journey_progress_pct"], 4 / 5 * 100, places=1)


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
            self.coach_intro_seen_at = kw.get("coach_intro_seen_at")
            self.verification_status = kw.get("verification_status", "incomplete")

    def _ready_stub(self, verification_status):
        """A profile past the welcome/phone/coach-intro gates."""
        return self._Stub(
            welcome_seen_at=datetime.datetime(2026, 1, 1),
            phone_verified=True,
            coach_intro_seen_at=datetime.datetime(2026, 1, 2),
            verification_status=verification_status,
        )

    def test_null_profile_is_step_1(self):
        self.assertEqual(onboarding.get_current_step(None), 1)

    def test_no_welcome_seen_is_step_1(self):
        p = self._Stub()
        self.assertEqual(onboarding.get_current_step(p), 1)

    def test_welcome_seen_but_no_phone_is_step_2(self):
        p = self._Stub(welcome_seen_at=datetime.datetime(2026, 1, 1))
        self.assertEqual(onboarding.get_current_step(p), 2)

    def test_phone_verified_but_no_coach_intro_is_step_3(self):
        p = self._Stub(
            welcome_seen_at=datetime.datetime(2026, 1, 1),
            phone_verified=True,
        )
        self.assertEqual(onboarding.get_current_step(p), 3)

    def test_coach_intro_seen_but_incomplete_is_step_4(self):
        p = self._ready_stub("incomplete")
        self.assertEqual(onboarding.get_current_step(p), 4)

    def test_pending_is_step_5_get_verified(self):
        # Submitted, awaiting verification at an event or via LuxID.
        p = self._ready_stub("pending")
        self.assertEqual(onboarding.get_current_step(p), 5)

    def test_verified_is_step_5_get_verified(self):
        p = self._ready_stub("verified")
        self.assertEqual(onboarding.get_current_step(p), 5)

    def test_rejected_returns_sentinel(self):
        p = self._ready_stub("rejected")
        self.assertEqual(
            onboarding.get_current_step(p),
            onboarding.STEP_REJECTED,
        )


class UrlNameForStepTests(TestCase):

    def test_rejected_sentinel_maps_to_profile_rejected(self):
        self.assertEqual(
            onboarding.url_name_for_step(onboarding.STEP_REJECTED),
            "crush_lu:profile_rejected",
        )

    def test_step_5_maps_to_profile_submitted(self):
        # Final step ("Get verified") renders on the profile_submitted page.
        self.assertEqual(onboarding.url_name_for_step(5), "crush_lu:profile_submitted")

    def test_out_of_range_step_clamps(self):
        # High clamps to the final step; low clamps to the first.
        self.assertEqual(onboarding.url_name_for_step(42), "crush_lu:profile_submitted")
        self.assertEqual(onboarding.url_name_for_step(-99), "crush_lu:welcome")
