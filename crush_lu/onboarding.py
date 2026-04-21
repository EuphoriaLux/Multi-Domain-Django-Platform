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
    Step(1, "welcome",   _("Welcome"),           chapter=1, min_duration=1),
    Step(2, "phone",     _("Verify number"),     chapter=1, min_duration=1),
    Step(3, "select",    _("Select your Coach"), chapter=2, min_duration=2),
    Step(4, "profile",   _("Build profile"),     chapter=2, min_duration=8),
    Step(5, "coach",     _("Meet your Coach"),   chapter=2, min_duration=1),
    Step(6, "submitted", _("Under review"),      chapter=3, min_duration=0),
    Step(7, "call",      _("Screening call"),    chapter=3, min_duration=20, locked=True),
)

JOURNEY_CHAPTERS = {
    1: Chapter(1, _("Chapter 1"), _("Get set up"),        "#9b59b6"),
    2: Chapter(2, _("Chapter 2"), _("You & your coach"),  "#c04a7e"),
    3: Chapter(3, _("Chapter 3"), _("Review & match"),    "#ff6b9d"),
}


def get_current_step(profile) -> int:
    """
    Derive the user's current journey step (1-7) from their CrushProfile state.

    Ordering — first gate that fails is the current step:
      1. welcome          — welcome_seen_at is null
      2. phone verify     — phone_verified is False
      3. coach intro      — coach_intro_seen_at is null (user hasn't acked the coach-intro page)
      4. build profile    — completion_status != 'submitted'
      5. meet coach       — submission exists, coach reviewing
      6. under review     — submission queued
      7. screening call   — submission approved + call pending

    Step 3 is an informational page: users don't pick a coach here — coaches
    claim submissions from a broadcast channel post-submit. The timestamp
    `coach_intro_seen_at` just marks that the user has seen the intro.

    `profile` may be None (user has no CrushProfile yet) — treat as step 1.
    """
    if profile is None:
        return 1

    if not profile.welcome_seen_at:
        return 1
    if not profile.phone_verified:
        return 2
    if not profile.coach_intro_seen_at:
        return 3
    if profile.completion_status != "submitted":
        return 4

    submission = getattr(profile, "profilesubmission_set", None)
    latest = submission.order_by("-submitted_at").first() if submission else None
    if latest is None:
        return 4
    if latest.status == "pending" and not latest.assigned_at:
        return 6  # still in queue, no coach yet
    if latest.status == "pending" and latest.assigned_at:
        return 5  # coach reviewing
    if latest.status == "approved" and not latest.review_call_completed:
        return 7  # screening call pending
    return 7


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
        annotated.append({
            "n": s.n,
            "key": s.key,
            "title": s.title,
            "chapter": s.chapter,
            "min_duration": s.min_duration,
            "locked": s.locked,
            "state": state,
        })
    return annotated


def active_step(current: int):
    """Return the Step dataclass for the current step (1-7)."""
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
    ("events",  _("I want to meet people at real events")),
    ("curious", _("I'm curious but still exploring")),
    ("online",  _("I want online dating, events are a bonus")),
    ("friend",  _("A friend recommended it")),
)


# Maps each step number to the named URL that renders that step. Used by the
# /onboarding/ smart-resume entry to route the user to their current step.
STEP_URL_NAMES = {
    1: "crush_lu:welcome",
    2: "crush_lu:onboarding_phone",
    3: "crush_lu:onboarding_coach_intro",
    4: "crush_lu:create_profile",
    5: "crush_lu:onboarding_meet_coach",
    6: "crush_lu:profile_submitted",
    7: "crush_lu:onboarding_screening_call",
}


def url_name_for_step(step: int) -> str:
    """Return the named URL for the given journey step (1-7)."""
    return STEP_URL_NAMES[max(1, min(step, len(JOURNEY_STEPS)))]
