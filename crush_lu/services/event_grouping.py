"""Reading the curated applicant pool as a compatibility problem.

Phase A uses this for one thing only: telling a member how much of the pool is
a mutual match for *them*, which is the recruiting signal that replaces the
per-gender seat chips on the event page. The table-composition engine lands
here too, on the same applicant adapter, so the number a member is shown and
the graph the organiser's groups are built from can never come from two
different definitions of "compatible".

Nothing in here reads or returns another member's preferences. The adapter
exists so that comparisons happen on plain values in Python, and the only thing
that ever leaves this module for a member surface is a count -- bucketed by the
caller before display, because an exact integer over Art. 9-adjacent preference
rows is a derived disclosure (flip your own stated preference, diff the count,
infer the pool).
"""

from types import SimpleNamespace

from crush_lu.matching import passes_event_hard_filters

# How big a mutual-match pool is, expressed in tables rather than people. The
# caller turns these into copy; they exist as keys so the thresholds live in one
# place and the templates cannot invent their own.
MATCH_BUCKET_FEW = "few"
MATCH_BUCKET_HALF = "half"
MATCH_BUCKET_EVENING = "evening"
MATCH_BUCKET_MULTIPLE = "multiple"


def _applicant(registration):
    """Adapt one application to the duck-typed shape the filters expect.

    Identity (gender, age) comes from the profile; what they are looking for
    comes from the application's own preference snapshot, not the profile --
    the snapshot is what they said for *this* event, and Phase 1 collects it
    precisely so a preference change later cannot rewrite a past application.

    A missing preference row falls back to the neutral 18-99 / open-to-all
    defaults rather than being dropped: an application without preferences is
    someone who is happy with anyone, which is the easiest person to seat, not
    someone to exclude.
    """
    profile = getattr(registration.user, "crushprofile", None)
    pref = getattr(registration, "preference", None)
    return SimpleNamespace(
        user_id=registration.user_id,
        registration_id=registration.pk,
        gender=getattr(profile, "gender", None) or None,
        age=getattr(profile, "age", None),
        preferred_genders=list(getattr(pref, "preferred_genders", None) or []),
        preferred_age_min=getattr(pref, "preferred_age_min", None) or 18,
        preferred_age_max=getattr(pref, "preferred_age_max", None) or 99,
        languages=list(getattr(pref, "languages", None) or []),
    )


def load_applicants(event):
    """Every application on ``event``, adapted, in one query.

    ``user__crushprofile`` and ``preference`` are both reverse one-to-ones, so
    this joins outward twice -- fine here, and deliberately never combined with
    ``select_for_update()``: PostgreSQL refuses row locks on the nullable side
    of an outer join, the trap ``confirm_registrations`` documents at length.
    """
    registrations = event.eventregistration_set.filter(status="applied").select_related(
        "user__crushprofile", "preference"
    )
    return [_applicant(reg) for reg in registrations]


def viewer_applicant(user, profile, event, registration=None):
    """The viewer's own side of the comparison.

    Their live application when they have one; otherwise the same prefill chain
    the application form itself offers (Connect membership -> legacy profile
    preferences -> blank), so the number someone sees *before* applying is
    computed against the answers they would arrive with rather than against
    nothing.
    """
    if registration is not None and registration.status == "applied":
        return _applicant(registration)

    from crush_lu.forms import EventPreferenceForm

    initial = EventPreferenceForm.initial_for(user, profile, event) or {}
    return SimpleNamespace(
        user_id=user.pk,
        registration_id=None,
        gender=getattr(profile, "gender", None) or None,
        age=getattr(profile, "age", None),
        preferred_genders=list(initial.get("preferred_genders") or []),
        preferred_age_min=initial.get("preferred_age_min") or 18,
        preferred_age_max=initial.get("preferred_age_max") or 99,
        languages=list(initial.get("languages") or []),
    )


def count_mutual_matches(viewer, applicants):
    """How many applicants this viewer and the applicant both want to meet.

    Self-excluded by ``user_id``: an applicant is always a perfect match for
    themselves, and counting it would inflate every number by one and make the
    signal appear on a pool of one.
    """
    return sum(
        1
        for applicant in applicants
        if applicant.user_id != viewer.user_id
        and passes_event_hard_filters(viewer, applicant)
    )


def match_bucket(count, group_size):
    """Size a mutual-match pool in tables.

    Bucketed rather than exact for the privacy reason in the module docstring,
    and because "enough to fill an evening" is the more motivating sentence than
    "23". ``MATCH_BUCKET_FEW`` is the caller's cue to offer widening the
    preferences instead of printing a discouraging number.
    """
    if group_size < 2:
        return MATCH_BUCKET_FEW
    if count >= 2 * group_size:
        return MATCH_BUCKET_MULTIPLE
    if count >= group_size:
        return MATCH_BUCKET_EVENING
    if count >= max(1, group_size // 2):
        return MATCH_BUCKET_HALF
    return MATCH_BUCKET_FEW
