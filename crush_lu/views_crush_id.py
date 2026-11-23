"""Crush ID: the member's verification, approval and personal-data space.

Fourth menu pillar beside Events, Crush Connect and Coaching (thread "Make
Verification its own menu section", 2026-11-23). Step 1 is a hub that shows
the member's verification level and links to the existing pages; the pages
themselves move under /id/ in later steps.

Levels (shared with the 2027 Coaching plan):
  1 Account            - signed up, phone confirmed
  2 Approved           - profile and photos approved (coach call, LuxID or
                         event door). Event tickets and Crush Connect.
  3 Identity confirmed - a coach saw the member: coach-attested event
                         check-in or Connect video check. Connect Week and
                         Coaching Blind Dates. LuxID alone does not count: it
                         approves and puts a member in the Connect mix, but
                         Connect Week needs a real-life check (Tom,
                         2026-11-23).
"""

from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache

from crush_lu.decorators import crush_login_required


def identity_confirmed(user, profile):
    """Level 3: a coach-attested event check-in or a valid video check."""
    from crush_lu.services.connect_verification import human_verification_method

    if not profile or not profile.is_approved:
        return False
    return bool(human_verification_method(user))


def verification_level(user, profile):
    if profile is None:
        return 0
    if profile.is_approved:
        return 3 if identity_confirmed(user, profile) else 2
    return 1 if profile.phone_verified else 0


def _next_step(user, profile, level):
    """One next action for the status card: (title, body, url, label)."""
    from crush_lu.services.verification_funnel import funnel_visible

    if profile is None or profile.verification_status == "incomplete":
        return (
            _("Finish your profile"),
            _("Complete and submit your profile to start your verification."),
            reverse("crush_lu:onboarding_entry"),
            _("Continue setup"),
        )
    if profile.verification_status == "rejected":
        return (
            _("Your profile was not approved"),
            _("See the details and what you can do next."),
            reverse("crush_lu:profile_rejected"),
            _("View details"),
        )
    if not profile.is_approved:
        return (
            _("Get approved"),
            _(
                "Pick one way to get verified: LuxID, coming to an event, or a "
                "call with a coach. You only need one."
            ),
            reverse("crush_lu:profile_submitted"),
            _("Choose how to get verified"),
        )
    if level < 3:
        if funnel_visible(user):
            return (
                _("Confirm your identity"),
                _(
                    "A short video check with a coach opens Connect Week. "
                    "Checking in with a coach at an event works too."
                ),
                reverse("crush_lu:connect_verification"),
                _("Open video check"),
            )
        return (
            _("Confirm your identity"),
            _(
                "Check in with a coach at one of our events to confirm your "
                "identity and open Connect Week."
            ),
            reverse("crush_lu:event_list"),
            _("Browse events"),
        )
    return None


@crush_login_required
@never_cache
def crush_id_hub(request):
    from crush_lu.models import CrushProfile

    user = request.user
    profile = CrushProfile.objects.filter(user=user).first()
    level = verification_level(user, profile)
    membership = getattr(user, "crush_connect_membership", None)
    method_labels = {
        "luxid": _("LuxID"),
        "coach_event": _("Event check-in"),
        "premium_coach": _("Coach call"),
        "admin": _("Coach review"),
    }
    context = {
        "crush_profile": profile,
        "level": level,
        "next_step": _next_step(user, profile, level),
        "verification_method_label": (
            method_labels.get(profile.verification_method, "")
            if profile and profile.is_approved
            else ""
        ),
        "connect_onboarded": bool(membership and membership.is_onboarded),
    }
    return render(request, "crush_lu/crush_id/hub.html", context)


def coaching_coming_soon(request):
    return render(request, "crush_lu/crush_id/coaching_soon.html")
