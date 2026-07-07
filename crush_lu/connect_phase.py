"""Crush Connect launch-phase helpers.

Three phases, driven by two settings, so access can widen in stages:

- **PRELAUNCH** (both flags off): staff only; everyone else lands on the
  teaser / waitlist.
- **BETA** (``CRUSH_CONNECT_CANDIDATE_OPEN`` on, ``CRUSH_CONNECT_LAUNCHED``
  off): the candidate — "in the Mix" — track opens to any verified + LuxID
  member (they can opt in and become discoverable), but the Premium/receiver
  track (Today's Drop) stays limited to staff + hand-picked waitlist testers
  (``CrushConnectWaitlist.selected_as_tester``). €15 stays funnelled to the
  waitlist via ``PREMIUM_REDIRECTS_TO_BETA``.
- **LAUNCHED** (``CRUSH_CONNECT_LAUNCHED`` on): everything public; the beta
  flag is ignored.

Keep the two access questions separate: ``candidate_access_open()`` gates the
Mix/onboarding/catalogue/sparks surfaces; ``receiver_access_open(user)`` gates
Today's Drop. Callers combine the latter with ``_user_is_connect_receiver_
eligible`` (approved + coach) — this module only answers "does the *phase* let
this user receive".
"""

from django.conf import settings


def candidate_access_open():
    """True when the candidate track (onboarding / Mix / catalogue / sparks) is
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


def receiver_access_open(user):
    """Whether this user's phase lets them reach the receiver track (Today's
    Drop). Full launch opens it to every Premium member; the beta limits it to
    staff + selected waitlist testers. Does NOT check coach/onboarded — callers
    combine it with ``_user_is_connect_receiver_eligible``."""
    if getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "is_staff", False) or is_selected_beta_tester(user))
