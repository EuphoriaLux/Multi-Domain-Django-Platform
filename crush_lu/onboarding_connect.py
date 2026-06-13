"""
Crush Connect onboarding — the 7-step resumable opt-in wizard.

Single source of truth for the wizard's step list, the per-step form, the
template partial, and the progress helpers used by the wizard shell and the
profile-edit screen. Mirrors ``crush_lu/onboarding.py``, but the Connect
wizard is pointer-driven (``CrushConnectMembership.onboarding_step``) and
resumable rather than derived from profile state.

``form_key`` is a string (resolved to a form class lazily by ``form_for_step``)
so importing this module stays light — it's imported at URL/import time, while
``forms_crush_connect`` pulls in the model layer.
"""

from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _

TOTAL_STEPS = 7

_STEP_PARTIAL = "crush_lu/crush_connect/onboarding_steps/_step{n}_{key}.html"


@dataclass(frozen=True)
class ConnectStep:
    n: int
    key: str
    title: str
    template: str
    form_key: str


CONNECT_STEPS = (
    ConnectStep(1, "intention", _("Intention"),
                _STEP_PARTIAL.format(n=1, key="intention"), "intention"),
    ConnectStep(2, "lifestyle", _("Lifestyle"),
                _STEP_PARTIAL.format(n=2, key="lifestyle"), "lifestyle"),
    ConnectStep(3, "languages", _("Languages & interests"),
                _STEP_PARTIAL.format(n=3, key="languages"), "languages"),
    ConnectStep(4, "life", _("Life basics"),
                _STEP_PARTIAL.format(n=4, key="life"), "life"),
    ConnectStep(5, "family", _("Family & future"),
                _STEP_PARTIAL.format(n=5, key="family"), "family"),
    ConnectStep(6, "ideal_match", _("Ideal match"),
                _STEP_PARTIAL.format(n=6, key="ideal_match"), "ideal_match"),
    ConnectStep(7, "story", _("Your story"),
                _STEP_PARTIAL.format(n=7, key="story"), "story"),
)

_BY_N = {s.n: s for s in CONNECT_STEPS}
_BY_KEY = {s.key: s for s in CONNECT_STEPS}


def clamp_step(n) -> int:
    """Coerce any value (pointer, URL kwarg) into the 1..TOTAL_STEPS range."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 1
    return max(1, min(n, TOTAL_STEPS))


def step_for(n) -> ConnectStep:
    """Return the ConnectStep for a (clamped) step number."""
    return _BY_N[clamp_step(n)]


def step_for_key(key) -> "ConnectStep | None":
    """Return the ConnectStep for a section key, or None if unknown."""
    return _BY_KEY.get(key)


def progress_pct(n) -> float:
    """
    Completed-progress width for the wizard bar, as a percentage. Uses
    ``n / TOTAL_STEPS`` (step 1 already shows forward motion, step 7 = 100%) —
    intentionally simpler than ``onboarding.py``'s dot-stepper geometry.
    """
    return round(clamp_step(n) / TOTAL_STEPS * 100, 2)


def annotate_steps(current):
    """Per-step dicts with completed|active|upcoming state for the stepper."""
    current = clamp_step(current)
    out = []
    for s in CONNECT_STEPS:
        state = "completed" if s.n < current else "active" if s.n == current else "upcoming"
        out.append({"n": s.n, "key": s.key, "title": s.title, "state": state})
    return out


def form_for_step(n):
    """
    Resolve a step number to its bound form CLASS. Imported lazily to break the
    forms↔models import cycle (forms import models; this module is imported at
    URL-load time). Keep this mapping next to ``CONNECT_STEPS``.
    """
    from crush_lu.forms_crush_connect import (
        ConnectIntentionForm,
        ConnectLifestyleForm,
        ConnectLanguagesForm,
        ConnectLifeForm,
        ConnectFamilyForm,
        ConnectIdealMatchForm,
        ConnectStoryForm,
    )

    return {
        "intention": ConnectIntentionForm,
        "lifestyle": ConnectLifestyleForm,
        "languages": ConnectLanguagesForm,
        "life": ConnectLifeForm,
        "family": ConnectFamilyForm,
        "ideal_match": ConnectIdealMatchForm,
        "story": ConnectStoryForm,
    }[step_for(n).form_key]
