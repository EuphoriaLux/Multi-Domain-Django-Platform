"""
Onboarding journey — the 7-step arc every new user walks through.

Single source of truth for the step list, chapter grouping, and the current-step
helper used by views and the `journey_stepper.html` partial. Keeping this in one
module means the backend, the template, and the tests all agree on what step the
user is on.

The step keys mirror the design prototype at
`crush-lu-design-system/project/redesign/stepper.jsx`.
"""

from dataclasses import dataclass
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Step:
    n: int
    key: str
    title: str
    chapter: int
    min_duration: int  # estimated minutes; 0 = instant
    locked: bool = False


@dataclass(frozen=True)
class Chapter:
    n: int
    label: str
    name: str
    color: str


JOURNEY_STEPS = (
    Step(1, "welcome", _("Welcome"), chapter=1, min_duration=1),
    Step(2, "phone", _("Verify number"), chapter=1, min_duration=1),
    Step(3, "coach_intro", _("Meet the Coaches"), chapter=2, min_duration=2),
    Step(4, "profile", _("Build profile"), chapter=2, min_duration=8),
    Step(5, "verify", _("Get verified"), chapter=3, min_duration=0),
)

# Sentinel returned by get_current_step when the user's profile is permanently
# rejected. Callers translate this to `profile_rejected` rather than a journey step.
STEP_REJECTED = -1

JOURNEY_CHAPTERS = {
    1: Chapter(1, _("Chapter 1"), _("Get set up"), "#9b59b6"),
    2: Chapter(2, _("Chapter 2"), _("Build your profile"), "#c04a7e"),
    3: Chapter(3, _("Chapter 3"), _("Get verified"), "#ff6b9d"),
}


def get_current_step(profile) -> int:
    """
    Derive the user's current journey step (1-5) from their CrushProfile state.

    Ordering — first gate that fails is the current step. Step numbers follow
    temporal order so the stepper never moves backwards as the user progresses:

      1. welcome          — welcome_seen_at is null
      2. phone verify     — phone_verified is False
      3. coach intro      — coach_intro_seen_at is null
      4. build profile    — verification_status == 'incomplete'
      5. get verified     — profile submitted (pending) or verified

    Verification no longer goes through a pre-event coach-review queue, so the
    journey ends at "get verified" — the user gets verified in person at an
    event or via LuxID. Step 3 ("Meet the Coaches") is informational.

    `profile` may be None (user has no CrushProfile yet) — treat as step 1.

    Returns STEP_REJECTED (a sentinel outside 1-5) for profiles that have been
    permanently rejected. Callers should translate that to the
    `profile_rejected` URL instead of a journey step.
    """
    if profile is None:
        return 1

    if not profile.welcome_seen_at:
        return 1
    if not profile.phone_verified:
        return 2
    if not profile.coach_intro_seen_at:
        return 3
    # 'incomplete' covers both "never submitted" and legacy "revision" (which
    # resets verification_status to incomplete) — back to the build step.
    if profile.verification_status == "incomplete":
        return 4
    if profile.verification_status == "rejected":
        return STEP_REJECTED
    # 'pending' (awaiting verification) and 'verified' both land on the final
    # "get verified" step.
    return 5


def annotate_steps(current: int):
    """
    Return a list of dicts (one per step) with precomputed `state` for the
    template. State is one of: 'completed' | 'active' | 'upcoming'. Locked is
    carried through separately so templates can render a lock icon on upcoming
    steps that the user can't unlock yet.
    """
    annotated = []
    for s in JOURNEY_STEPS:
        if s.n < current:
            state = "completed"
        elif s.n == current:
            state = "active"
        else:
            state = "upcoming"
        annotated.append(
            {
                "n": s.n,
                "key": s.key,
                "title": s.title,
                "chapter": s.chapter,
                "min_duration": s.min_duration,
                "locked": s.locked,
                "state": state,
            }
        )
    return annotated


def active_step(current: int):
    """Return the Step dataclass for the current step (1-5)."""
    return JOURNEY_STEPS[max(1, min(current, len(JOURNEY_STEPS))) - 1]


def active_chapter(current: int) -> Chapter:
    """Return the Chapter for the currently-active step."""
    return JOURNEY_CHAPTERS[active_step(current).chapter]


def stepper_context(current: int) -> dict:
    """
    Build the context dict required by the `journey_stepper.html` partial.
    Views call this and merge the result into their render context.

    `journey_progress_pct` is the width of the active-progress bar as a
    percentage of the track (dot-1-center to dot-7-center), precomputed so
    the template stays arithmetic-free.
    """
    total = len(JOURNEY_STEPS)
    # track spans 6/7 of the full row (dot-1 center to dot-7 center), and
    # the filled portion is (current-1)/(total-1) of the track.
    filled_ratio = (current - 1) / (total - 1) if total > 1 else 0
    track_span = (total - 1) / total  # 6/7
    progress_pct = round(filled_ratio * track_span * 100, 2)
    return {
        "journey_steps": annotate_steps(current),
        "journey_chapter": active_chapter(current),
        "journey_current": current,
        "journey_total": total,
        "journey_active": active_step(current),
        "journey_progress_pct": progress_pct,
    }


INTENT_PROBE_CHOICES = (
    ("events", _("I want to meet people at real events")),
    ("curious", _("I'm curious but still exploring")),
    ("online", _("I want online dating, events are a bonus")),
    ("friend", _("A friend recommended it")),
)


# Maps each step number to the named URL that renders that step. Used by the
# /onboarding/ smart-resume entry to route the user to their current step.
STEP_URL_NAMES = {
    1: "crush_lu:welcome",
    2: "crush_lu:onboarding_phone",
    3: "crush_lu:onboarding_coach_intro",
    4: "crush_lu:create_profile",
    5: "crush_lu:profile_submitted",  # the "get verified" page
}


def url_name_for_step(step: int) -> str:
    """Return the named URL for the given journey step.

    Handles the STEP_REJECTED sentinel by returning the profile_rejected URL.
    """
    if step == STEP_REJECTED:
        return "crush_lu:profile_rejected"
    return STEP_URL_NAMES[max(1, min(step, len(JOURNEY_STEPS)))]
