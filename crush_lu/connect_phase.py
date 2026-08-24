"""Crush Connect launch-phase helpers.

Access widens in stages through two settings:

- **PRELAUNCH** (both flags off): staff only; everyone else lands on the
  teaser / waitlist.
- **BETA** (candidate flag on, launch flag off): verified members can onboard
  and enter the Mix. Connect Week admits event-verified members, staff, and
  selected waitlist testers. Premium remains the human coach-pick layer.
- **LAUNCHED** (``CRUSH_CONNECT_LAUNCHED`` on): everything public; the beta
  flag is ignored.

``candidate_access_open()`` gates onboarding, the Mix, catalogue, and profile
surfaces. ``cycle_access_open(user)`` adds the member-specific Connect Week beta
gate. Premium coach picks use membership entitlement and are not an automated
matching workflow.
"""

from django.conf import settings


def candidate_access_open():
    """True when the candidate track (onboarding / Mix / catalogue) is
    reachable by eligible members — full launch OR the beta candidate-open phase.
    Staff bypass is handled separately at each call site."""
    return bool(
        getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)
        or getattr(settings, "CRUSH_CONNECT_CANDIDATE_OPEN", False)
    )


def is_selected_beta_tester(user):
    """True if ``user`` is a hand-picked waitlist beta tester
    (``CrushConnectWaitlist.selected_as_tester``)."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    waitlist = getattr(user, "crush_connect_waitlist", None)
    return bool(waitlist and waitlist.selected_as_tester)


def is_event_verified_member(user):
    """True if ``user`` has coach-verified in-person event attendance."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    profile = getattr(user, "crushprofile", None)
    return bool(profile and profile.has_attended_event)


def cycle_access_open(user):
    """Whether this user's phase lets them reach Connect Week.

    Full launch opens it to all eligible members. Beta admits event-verified
    members, staff, and selected testers.
    """
    if getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False) or is_selected_beta_tester(user):
        return True
    return is_event_verified_member(user)
