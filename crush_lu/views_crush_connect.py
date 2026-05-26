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
from crush_lu.forms_crush_connect import CrushConnectOnboardingForm
from crush_lu.models import CrushConnectMembership, EventRegistration
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


def _user_passes_pre_onboarding_gate(user) -> bool:
    """Approved profile + at least one attended event."""
    profile = getattr(user, "crushprofile", None)
    if profile is None or not profile.is_approved:
        return False
    return EventRegistration.objects.filter(user=user, status="attended").exists()


def _connect_access_blocker(user):
    """
    Return ``None`` when the user may access Today's Drop, otherwise return
    a redirect/response that should be sent back to them.

    Staff users bypass all checks so they can preview the UI in any state.
    Everyone else must clear all four gates:
      1. global launch flag is on
      2. has an approved profile
      3. has attended ≥1 event
      4. has an onboarded ``CrushConnectMembership`` (and isn't coach-excluded)
    Routes:
      - flag off / not approved / no event → teaser
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
    One-page Crush Connect opt-in.

    Pre-conditions (mirrors the waitlist eligibility):
      - approved profile
      - at least 1 attended event
      - flag on (or staff)
    If the user is already onboarded, send them straight to Today's Drop.
    """
    user = request.user

    if not user.is_staff and not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return redirect("crush_lu:crush_connect_teaser")
    if not user.is_staff and not _user_passes_pre_onboarding_gate(user):
        return redirect("crush_lu:crush_connect_teaser")

    membership, _created = CrushConnectMembership.objects.get_or_create(user=user)
    if membership.is_onboarded:
        return redirect("crush_lu:crush_connect_home")

    if request.method == "POST":
        form = CrushConnectOnboardingForm(request.POST, instance=membership)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.onboarded_at = timezone.now()
            obj.save()
            messages.success(
                request,
                _("Welcome to Crush Connect — your first Drop is ready."),
            )
            return redirect("crush_lu:crush_connect_home")
    else:
        form = CrushConnectOnboardingForm(instance=membership)

    return render(
        request,
        "crush_lu/crush_connect/onboarding.html",
        {"form": form, "membership": membership},
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
