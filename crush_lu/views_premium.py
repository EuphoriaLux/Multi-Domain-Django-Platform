"""
Premium membership — member-facing coach directory and selection.

A member browses coaches who are open to new premium members and chooses one.
Choosing creates a ``PremiumMembership`` in the ``pending`` state; payment is
confirmed out-of-band by staff (see ``PremiumMembership.confirm`` and the admin
action), which is the single place that assigns the coach.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .models import CrushCoach, CrushProfile, PremiumMembership

logger = logging.getLogger(__name__)


def _available_coaches():
    """Coaches open to new premium members and not yet at capacity."""
    return (
        CrushCoach.objects.filter(is_active=True, accepting_premium=True, is_away=False)
        .annotate(member_count=Count("assigned_members"))
        .filter(member_count__lt=F("max_premium_members"))
        .select_related("user")
        .order_by("user__first_name")
    )


@login_required
def premium_choose_coach(request):
    """Show the premium coach directory."""
    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        messages.info(request, _("Create your profile first to go premium."))
        return redirect("crush_lu:dashboard")

    # Already premium — nothing to choose.
    if profile.assigned_coach_id:
        messages.info(request, _("You already have a personal coach."))
        return redirect("crush_lu:dashboard")

    pending = (
        PremiumMembership.objects.filter(user=request.user, status="pending")
        .select_related("coach__user")
        .first()
    )

    # Crush Connect beta: funnel premium-seekers into the beta waitlist. Members
    # with a pending request fall through so they can still change/cancel it.
    from django.conf import settings as _settings

    if getattr(_settings, "PREMIUM_REDIRECTS_TO_BETA", False) and not pending:
        return redirect("crush_lu:crush_connect_teaser")

    context = {
        "coaches": _available_coaches(),
        "pending_membership": pending,
    }
    return render(request, "crush_lu/premium/choose_coach.html", context)


@login_required
@require_POST
def premium_select_coach(request, coach_id):
    """Create a pending premium membership for the chosen coach."""
    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        messages.info(request, _("Create your profile first to go premium."))
        return redirect("crush_lu:dashboard")

    if profile.assigned_coach_id:
        messages.info(request, _("You already have a personal coach."))
        return redirect("crush_lu:dashboard")

    # Crush Connect beta: while the funnel is on, don't let a member start a
    # *fresh* premium request (stale directory form or a direct POST). Members
    # who already have a pending request may still change their chosen coach.
    from django.conf import settings as _settings

    has_pending = PremiumMembership.objects.filter(
        user=request.user, status="pending"
    ).exists()
    if getattr(_settings, "PREMIUM_REDIRECTS_TO_BETA", False) and not has_pending:
        return redirect("crush_lu:crush_connect_teaser")

    coach = get_object_or_404(CrushCoach, id=coach_id)
    if not coach.can_accept_premium():
        messages.error(
            request,
            _("Sorry, this coach is no longer available. Please choose another."),
        )
        return redirect("crush_lu:premium_choose_coach")

    # One open request at a time — reuse any existing pending row.
    membership, created = PremiumMembership.objects.get_or_create(
        user=request.user,
        status="pending",
        defaults={"coach": coach},
    )
    if not created and membership.coach_id != coach.id:
        membership.coach = coach
        membership.save(update_fields=["coach"])

    logger.info(
        "Premium membership %s (pending) for user %s with coach %s",
        membership.id,
        request.user_id if hasattr(request, "user_id") else request.user.id,
        coach.id,
    )
    messages.success(
        request,
        _(
            "Great choice! We've reserved %(coach)s for you. We'll be in touch to "
            "complete your premium membership."
        )
        % {"coach": coach.user.first_name or _("your coach")},
    )
    return redirect("crush_lu:dashboard")


@login_required
@require_POST
def premium_cancel_membership(request):
    """Cancel a pending premium membership so the member can re-choose."""
    membership = (
        PremiumMembership.objects.filter(user=request.user, status="pending")
        .select_related("coach__user")
        .first()
    )
    if membership and membership.cancel(by_user=request.user):
        logger.info(
            "Premium membership %s cancelled by user %s",
            membership.id,
            request.user.id,
        )
        messages.success(
            request,
            _("Your premium request has been cancelled. You can choose again anytime."),
        )
    else:
        messages.info(request, _("You have no pending premium request to cancel."))
    return redirect("crush_lu:premium_choose_coach")
