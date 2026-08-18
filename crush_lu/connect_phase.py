"""Crush Connect launch-phase helpers.

Three phases, driven by two settings, so access can widen in stages:

- **PRELAUNCH** (both flags off): staff only; everyone else lands on the
  teaser / waitlist.
- **BETA** (``CRUSH_CONNECT_CANDIDATE_OPEN`` on, ``CRUSH_CONNECT_LAUNCHED``
  off): the candidate — "in the Mix" — track opens to any verified + LuxID
  member (they can opt in and become discoverable), but the Premium/receiver
  track (Today's Drop) stays limited to staff + hand-picked waitlist testers
  (``CrushConnectWaitlist.selected_as_tester``). Premium purchase stays
  funnelled to the waitlist by ``PREMIUM_REDIRECTS_TO_BETA`` for everyone
  except those same selected testers, who may buy (see ``views_premium``) —
  that is how they obtain the active membership the receiver entitlement gate
  then requires. No price is named here: it is ``SUMUP_PREMIUM_MONTHLY_FEE``,
  set per environment.
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


def is_event_verified_member(user):
    """True if ``user`` has coach-verified in-person event attendance."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    profile = getattr(user, "crushprofile", None)
    return bool(profile and profile.has_attended_event)


def receiver_access_open(user):
    """Whether this user's phase lets them reach the active discovery track
    (Today's Drop / 7-Day Connect Cycle). Full launch opens it to all eligible members;
    the beta opens it to Event-verified members, staff, and selected testers."""
    if getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False) or is_selected_beta_tester(user):
        return True
    return is_event_verified_member(user)
