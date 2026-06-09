"""
Crush Connect views.

M3: a staff-only debug route that renders a single Drop card in isolation.
M4: the user-facing ``Today's Drop`` page.

The full user-facing surface (Today's Drop, Pending Sparks, journey reveal)
will continue to grow in this module across M5–M7.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crush_lu.decorators import crush_login_required
from crush_lu.email_helpers import send_crush_connect_catalogue_welcome
from crush_lu.forms_crush_connect import CrushConnectOnboardingForm
from crush_lu.models import CrushConnectMembership
from crush_lu.services import get_or_create_daily_drop


User = get_user_model()


@staff_member_required
def dev_connect_card_preview(request, user_id: int):
    """
    Render one Drop card for the given user, in isolation, for visual review.

    Staff-only. Not gated by the CRUSH_CONNECT_LAUNCHED flag — staff need to
    preview the card UI long before public launch.
    """
    target = get_object_or_404(
        User.objects.select_related("crushprofile", "crush_connect_membership"),
        pk=user_id,
    )
    context = {
        "target": target,
        "target_profile": getattr(target, "crushprofile", None),
        "target_membership": getattr(target, "crush_connect_membership", None),
        "show_spark_placeholder": True,
    }
    return render(request, "crush_lu/crush_connect/dev_card_preview.html", context)


def _user_is_connect_receiver_eligible(user) -> bool:
    """Verified profile + PREMIUM (personal coach assigned).

    Receiving Drops is the Premium product: it unlocks when a coach is
    assigned, not on plain event attendance or LuxID alone.
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return profile.assigned_coach_id is not None


def _user_is_connect_candidate_eligible(user) -> bool:
    """Verified profile + LuxID linked: may opt in to the candidate catalogue.

    LuxID is the ticket into the catalogue — members verified at an event
    stay verified for events/connections but must link LuxID before they
    can be picked for a Premium member's Drop.
    """
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return profile.has_luxid_connected


def _user_passes_pre_onboarding_gate(user) -> bool:
    """Either track may onboard: receivers (Premium) or candidates (LuxID)."""
    return _user_is_connect_receiver_eligible(
        user
    ) or _user_is_connect_candidate_eligible(user)


def _connect_access_blocker(user):
    """
    Return ``None`` when the user may access Today's Drop, otherwise return
    a redirect/response that should be sent back to them.

    Staff users bypass all checks so they can preview the UI in any state.
    Two tracks exist (asymmetric model):
      - RECEIVER (Premium, coach assigned): onboarded → Today's Drop.
      - CANDIDATE (LuxID, no Premium): onboarded → catalogue status page;
        they appear in others' Drops but never receive their own.
    Routes:
      - flag off / not approved / neither track → teaser
      - eligible but not onboarded → onboarding (so they can opt in)
      - excluded by coach → teaser (silent — don't expose the exclusion)
    """
    if user.is_staff:
        return None

    if not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")

    if not _user_passes_pre_onboarding_gate(user):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")
    if membership is None or membership.onboarded_at is None:
        return redirect("crush_lu:crush_connect_onboarding")

    if not _user_is_connect_receiver_eligible(user):
        # Candidate-only members don't get Drops — show their catalogue state.
        return redirect("crush_lu:crush_connect_catalogue_status")

    return None


def _next_drop_at(now=None):
    """
    Return the next time tomorrow's Drop becomes available — used by the
    home template to render a countdown line under empty/all-viewed states.
    Locked to 06:00 in the project's configured timezone so the rhythm feels
    like a morning ritual rather than an arbitrary midnight refresh.
    """
    now = now or timezone.localtime()
    tomorrow_six = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= tomorrow_six:
        tomorrow_six = tomorrow_six + timedelta(days=1)
    return tomorrow_six


@crush_login_required
def crush_connect_onboarding(request):
    """
    One-page Crush Connect opt-in, shared by both tracks:
      - RECEIVER (Premium): finishing lands on Today's Drop.
      - CANDIDATE (LuxID, no Premium): finishing lands on the catalogue
        status page — they're discoverable, not receiving.

    Pre-conditions: approved profile + (Premium coach OR LuxID linked),
    flag on (or staff). If already onboarded, forward to the right page.
    """
    user = request.user

    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")
    if not user.is_staff and not _user_passes_pre_onboarding_gate(user):
        return redirect("crush_lu:crush_connect_teaser")

    is_receiver = _user_is_connect_receiver_eligible(user) or user.is_staff
    done_url = (
        "crush_lu:crush_connect_home"
        if is_receiver
        else "crush_lu:crush_connect_catalogue_status"
    )

    membership, _created = CrushConnectMembership.objects.get_or_create(user=user)
    if membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")
    if membership.is_onboarded:
        return redirect(done_url)

    if request.method == "POST":
        form = CrushConnectOnboardingForm(request.POST, instance=membership)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.onboarded_at = timezone.now()
            obj.save()
            if is_receiver:
                messages.success(
                    request,
                    _("Welcome to Crush Connect — your first Drop is ready."),
                )
            else:
                messages.success(
                    request,
                    _(
                        "Welcome to Crush Connect — you're now in the "
                        "catalogue and can be matched by a Crush Coach."
                    ),
                )
                send_crush_connect_catalogue_welcome(user, request)
            return redirect(done_url)
    else:
        form = CrushConnectOnboardingForm(instance=membership)

    return render(
        request,
        "crush_lu/crush_connect/onboarding.html",
        {
            "form": form,
            "membership": membership,
            "is_candidate_only": not is_receiver,
        },
    )


@crush_login_required
def crush_connect_catalogue_status(request):
    """
    Status page for candidate-track members (LuxID, no Premium): confirms
    they're in the catalogue, previews their Story card, and offers the
    Premium upgrade for receiving their own coach-curated matches.

    Premium receivers are forwarded to Today's Drop instead.
    """
    user = request.user

    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")

    if _user_is_connect_receiver_eligible(user):
        return redirect("crush_lu:crush_connect_home")
    if not user.is_staff and not _user_is_connect_candidate_eligible(user):
        return redirect("crush_lu:crush_connect_teaser")

    membership = getattr(user, "crush_connect_membership", None)
    if membership is not None and membership.excluded_by_coach:
        return redirect("crush_lu:crush_connect_teaser")
    if membership is None or not membership.is_onboarded:
        return redirect("crush_lu:crush_connect_onboarding")

    return render(
        request,
        "crush_lu/crush_connect/catalogue_status.html",
        {"membership": membership},
    )


@crush_login_required
def crush_connect_home(request):
    """
    Render ``Today's Drop`` — the user-facing Crush Connect landing page.

    M4 deliberately does NOT include the Spark CTA on cards (that comes in M5).
    Cards are read-only; the page also renders an empty state when the
    eligible pool yielded zero candidates for the day.
    """
    user = request.user
    blocker = _connect_access_blocker(user)
    if blocker is not None:
        return blocker

    drop = get_or_create_daily_drop(user)
    recipients = list(
        drop.recipients.select_related(
            "crushprofile", "crush_connect_membership"
        ).all()
    )

    context = {
        "drop": drop,
        "recipients": recipients,
        "next_drop_at": _next_drop_at(),
        "is_staff_preview": user.is_staff
        and (
            not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)
            or not getattr(user, "crush_connect_membership", None)
            or not user.crush_connect_membership.is_onboarded
        ),
    }
    return render(request, "crush_lu/crush_connect/home.html", context)
