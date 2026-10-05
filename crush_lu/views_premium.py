"""
Premium membership — member-facing coach directory and selection.

A member browses coaches who are open to new premium members and chooses one.
Choosing creates a ``PremiumMembership`` in the ``pending`` state; payment is
confirmed out-of-band by staff (see ``PremiumMembership.confirm`` and the admin
action), which is the single place that assigns the coach.
"""

import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .connect_phase import is_selected_beta_tester
from .models import CrushCoach, CrushProfile, PremiumMembership
from .ios_app_utils import ios_commerce_suppressed

logger = logging.getLogger(__name__)


def premium_monthly_fee():
    """The Premium monthly price as a Decimal, read from the one setting.

    Shared by the coach picker and the public pricing page (/membership/) so
    neither can advertise a different amount than the SumUp checkout charges
    (views_payments.create_sumup_premium_checkout reads the same setting).
    """
    from django.conf import settings as _settings

    return Decimal(str(getattr(_settings, "SUMUP_PREMIUM_MONTHLY_FEE", "10.00")))


def pending_premium_membership(user):
    """The user's open (``pending``) PremiumMembership, or None.

    One predicate for "has a Premium request still to pay, change or cancel":
    premium_choose_coach lets these members past the Crush Connect beta
    funnel, and the pricing page (/membership/) must offer them the same way
    back in instead of the waitlist.
    """
    if not getattr(user, "is_authenticated", False):
        return None
    return (
        PremiumMembership.objects.filter(user=user, status="pending")
        .select_related("coach__user")
        .first()
    )


def pending_premium_state(user):
    """How the pricing page may address the user's open Premium request.

    ``None`` when there is no pending request. ``"paid"`` when a payment was
    already captured against it but Premium was never granted
    (``views_payments._premium_payment_captured``, the predicate that makes
    create_sumup_premium_checkout answer 409): only staff can reconcile that,
    so the member is pointed at support, never at checkout. ``"complete"``
    when checkout would accept it. ``"manage"`` when the beta allowlist
    refuses its buyer (``views_payments._premium_purchase_refused``, the same
    predicate that makes create_sumup_premium_checkout answer 403): such a
    member can still change or cancel the request, but must not be promised
    completion. ``"paid"`` is checked first, mirroring the checkout's order.
    """
    pending = pending_premium_membership(user)
    if pending is None:
        return None
    from .views_payments import _premium_payment_captured, _premium_purchase_refused

    from .models import PremiumPaymentRecoveryCase

    # #925: also any open case (even on an older request), which the checkout
    # endpoint refuses on -- never offer a pay button that would 409.
    if (
        _premium_payment_captured(pending)
        or PremiumPaymentRecoveryCase.objects.filter(
            user=user, status=PremiumPaymentRecoveryCase.Status.OPEN
        ).exists()
    ):
        return "paid"
    return "manage" if _premium_purchase_refused(pending) else "complete"


def _cancel_premium_request(membership, by_user):
    """#925: cancel unless money moved. Returns ``"cancelled"``, ``"captured"``,
    ``"open"`` (a checkout could still capture) or None (not pending).

    Closes its PENDING SumUp checkouts first (never refunds), then cancels
    under the same payment -> membership locks checkout publication takes, so
    no checkout can be published for a request once it is cancelled."""
    from .models import PaymentTransaction
    from .views_payments import (
        SumUpClient,
        _lock_premium_checkout_state,
        _premium_payment_captured,
        _settle_pending_premium_checkouts,
    )

    # A capture does not end the job: sibling checkouts left PENDING (from
    # before one-checkout-per-membership) are still closed below.
    captured = _premium_payment_captured(membership)
    state, retired_ids = "ok", set()
    if PaymentTransaction.objects.filter(
        premium_membership=membership, status=PaymentTransaction.Status.PENDING
    ).exists():
        # captured=True closes every PENDING checkout, newest included. One
        # SumUp already captured is not closed: it stays PENDING ("open")
        # until its webhook records PAID ("captured" above).
        state, _reuse, retired_ids, _known = _settle_pending_premium_checkouts(
            SumUpClient(), membership, captured=True
        )
    with transaction.atomic():
        locked, still_pending = _lock_premium_checkout_state(membership.pk, retired_ids)
        if captured or (locked is not None and _premium_payment_captured(locked)):
            return "captured"
        if state == "open" or still_pending:
            return "open"
        if locked is None or not locked.cancel(by_user=by_user):
            return None
    return "cancelled"


def open_recovery_case(user):
    """The member's OPEN recovery case (#925): pages show it, not a pay CTA.

    Any open case counts while the member has no current (pending/active)
    request; once one exists, only a case on that request does, so an old
    case never takes over a new request."""
    from .models import PremiumPaymentRecoveryCase

    if not user.is_authenticated:
        return None
    cases = PremiumPaymentRecoveryCase.objects.filter(
        user=user, status=PremiumPaymentRecoveryCase.Status.OPEN
    )
    current = ("pending", "active")
    if PremiumMembership.objects.filter(user=user, status__in=current).exists():
        cases = cases.filter(premium_membership__status__in=current)
    return cases.select_related("payment").first()


def _available_coaches():
    """Coaches open to new premium members and not yet at capacity.

    Capacity counts ACTIVE PremiumMemberships, not assigned_members — coach
    assignment also happens without payment (backfill, attendance
    auto-assign) and must not exhaust premium capacity.
    """
    return (
        CrushCoach.objects.filter(is_active=True, accepting_premium=True, is_away=False)
        .annotate(
            member_count=Count(
                "premium_memberships",
                filter=Q(premium_memberships__status="active"),
            )
        )
        .filter(member_count__lt=F("max_premium_members"))
        .select_related("user")
        .order_by("user__first_name")
    )


@login_required
def premium_choose_coach(request):
    """Show the premium coach directory."""
    from django.conf import settings as _settings

    if ios_commerce_suppressed(request):
        return render(request, "crush_lu/premium/ios_unavailable.html")

    pending = pending_premium_membership(request.user)

    # Crush Connect beta: funnel premium-seekers into the beta waitlist. This
    # runs before the profile gate because the waitlist is open to authenticated
    # users with no profile yet (a profile-less user can't have a pending request
    # anyway). Members with a pending request fall through so they can manage it.
    #
    # Hand-picked testers (``CrushConnectWaitlist.selected_as_tester``) are the
    # deliberate exception: the funnel stays on for everyone else, so selecting
    # someone in the admin is the ONLY way to open self-serve purchase during the
    # beta. The selected-tester flag also admits Connect Week; Premium itself
    # remains the human coach-pick entitlement.
    if (
        getattr(_settings, "PREMIUM_REDIRECTS_TO_BETA", False)
        and not pending
        and not is_selected_beta_tester(request.user)
    ):
        return redirect("crush_lu:crush_connect_teaser")

    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        messages.info(request, _("Create your profile first to go premium."))
        return redirect("crush_lu:dashboard")

    # Already premium — nothing to choose. Checks the membership, not
    # assigned_coach: a coach assigned without payment (backfill, attendance
    # auto-assign) must not lock the member out of actually buying Premium.
    if profile.has_active_premium:
        messages.info(request, _("You already have a personal coach."))
        return redirect("crush_lu:dashboard")

    context = {
        "coaches": _available_coaches(),
        "pending_membership": pending,
        # "manage" = the beta allowlist refuses this buyer at checkout (403), so
        # the template must not offer the pay button (same predicate as /membership/).
        "pending_premium_state": pending_premium_state(request.user),
        # The price shown must come from the same setting the checkout charges
        # (views_payments.create_sumup_premium_checkout reads it too). The label
        # used to hard-code "€10.00 / month", so changing SUMUP_PREMIUM_MONTHLY_FEE
        # would have advertised one price and billed another.
        "premium_monthly_fee": premium_monthly_fee(),
    }
    return render(request, "crush_lu/premium/choose_coach.html", context)


@login_required
@require_POST
def premium_select_coach(request, coach_id):
    """Create a pending premium membership for the chosen coach."""
    from django.conf import settings as _settings

    if ios_commerce_suppressed(request):
        messages.info(
            request,
            _("Premium memberships are not available inside the iOS app yet."),
        )
        return redirect("crush_lu:premium_choose_coach")

    # Crush Connect beta: while the funnel is on, don't let a member start a
    # *fresh* premium request (stale directory form or a direct POST). Members
    # who already have a pending request may still change their chosen coach.
    # Runs before the profile gate, mirroring premium_choose_coach.
    # Selected testers are exempt, mirroring premium_choose_coach — this POST is
    # the step that mints the pending membership, which is the capability token
    # create_sumup_premium_checkout requires, so the allowlist must hold here too
    # or a selected tester could see the directory and still not buy.
    has_pending = PremiumMembership.objects.filter(
        user=request.user, status="pending"
    ).exists()
    if (
        getattr(_settings, "PREMIUM_REDIRECTS_TO_BETA", False)
        and not has_pending
        and not is_selected_beta_tester(request.user)
    ):
        return redirect("crush_lu:crush_connect_teaser")

    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        messages.info(request, _("Create your profile first to go premium."))
        return redirect("crush_lu:dashboard")

    if profile.has_active_premium:
        messages.info(request, _("You already have a personal coach."))
        return redirect("crush_lu:dashboard")

    if not has_pending and open_recovery_case(request.user):
        # #925: an unresolved captured payment must be settled by staff before
        # a fresh request (and a second charge) can start.
        messages.info(
            request,
            _("We have already received a payment for your Premium request."),
        )
        return redirect("crush_lu:premium_choose_coach")

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
    outcome = membership and _cancel_premium_request(membership, request.user)
    if outcome in ("captured", "open"):
        # #925: cancelling would hide the recovery case (or a capture still in
        # flight) and reopen checkout for a new request, i.e. a second charge.
        messages.info(
            request,
            (
                _("We have already received a payment for your Premium request.")
                if outcome == "captured"
                else _(
                    "Your earlier card checkout could not be closed. "
                    "Please wait and try again."
                )
            ),
        )
        return redirect("crush_lu:premium_choose_coach")
    if outcome == "cancelled":
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
