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

from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
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
            reverse("crush_lu:crush_id_verify"),
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
                reverse("crush_lu:crush_id_verify"),
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


def public_badge_card(user, profile):
    """Step 3: the "What other members see" card.

    The public badge is the coach-verified badge members already see on
    Connect cards (``CrushProfile.real_person_badge``): a coach checked the
    member's photo at an event door ("in_person"), or saw them on a video or
    voice check. Crush ID shows the member their own badge and how to get or
    renew it; it grants nothing itself. Returns a dict for the template:

    ``state``  "shown"         other members see ``kind`` now
               "hidden"        a video/voice badge was granted, but those
                               badges are not shown to members yet (flag)
               "photo_changed" a coach checked an earlier main photo
               "none"          no badge yet
    ``kind``   "in_person" / "video" / "voice" / ""
    ``action`` (label, url) for the next step, or None
    """
    from crush_lu.services.real_person_check import granted_badge_medium
    from crush_lu.services.verification_funnel import funnel_visible

    if profile is None:
        return None
    kind = profile.real_person_badge
    if kind:
        return {"state": "shown", "kind": kind, "action": None}

    events = (_("Find an event"), reverse("crush_lu:event_list"))
    if profile.is_approved and funnel_visible(user):
        action = (_("Open video check"), reverse("crush_lu:crush_id_verify"))
    else:
        action = events

    granted = granted_badge_medium(profile)
    if granted:
        return {"state": "hidden", "kind": granted, "action": events}
    if profile.photo_verification_key:
        # is_photo_verified is False here, so the main photo changed since a
        # coach checked it at the door.
        return {"state": "photo_changed", "kind": "", "action": events}
    # Not approved yet: the status card's "Get approved" step already leads.
    return {
        "state": "none",
        "kind": "",
        "action": action if profile.is_approved else None,
    }


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
        "public_badge": public_badge_card(user, profile),
    }
    return render(request, "crush_lu/crush_id/hub.html", context)


@crush_login_required
@never_cache
def crush_id_verify(request):
    """Step 2: one page for every way to get verified.

    Members inside the Connect video-verification funnel get the funnel page;
    everyone else gets the get-verified page (LuxID, entry event at the door,
    coach call). Approved members with nothing left to do go to the hub.
    """
    from crush_lu import views, views_verification_funnel
    from crush_lu.services.verification_funnel import funnel_visible

    if funnel_visible(request.user):
        return views_verification_funnel.connect_verification(request)
    return views.profile_submitted(request)


def legacy_verify_redirect(request, *args, **kwargs):
    """/profile-submitted/ and /crush-connect/verification/ moved to /id/verify/.

    Permanent, query string kept, so links in sent emails and push messages
    keep working in the reader's language.
    """
    target = reverse("crush_lu:crush_id_verify")
    query = request.GET.urlencode()
    url = f"{target}?{query}" if query else target
    if not url_has_allowed_host_and_scheme(url, allowed_hosts=None):
        url = target
    return redirect(url, permanent=True)


def _edit_profile_section(request, **defaults):
    """Serve the profile editor with ``defaults`` as its query parameters.

    Step 4: /id/privacy/, /id/account/ and /id/data/ are short addresses for
    sections of the one editor (views.edit_profile), which reads its section
    from the query string. Explicit query parameters win, so links inside a
    section (``?sub=notifications``) keep working on these addresses too.
    """
    from crush_lu import views

    query = request.GET.copy()
    for key, value in defaults.items():
        query.setdefault(key, value)
    request.GET = query
    return views.edit_profile(request)


@crush_login_required
def crush_id_privacy(request):
    return _edit_profile_section(request, section="privacy")


@crush_login_required
def crush_id_account(request):
    return _edit_profile_section(request, section="account")


@crush_login_required
def crush_id_data(request):
    return _edit_profile_section(request, section="account", sub="danger")


def coaching_coming_soon(request):
    return render(request, "crush_lu/crush_id/coaching_soon.html")
