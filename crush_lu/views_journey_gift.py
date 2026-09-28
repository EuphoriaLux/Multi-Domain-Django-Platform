"""
Views for the Journey Gift system.

Handles gift creation, landing page, and claiming flow.
"""

import logging
from functools import wraps

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from .decorators import crush_login_required, ratelimit
from .models import CrushProfile, JourneyGift
from .models.journey_gift import GiftNoLongerClaimable
from .forms import JourneyGiftForm
from .utils.qr_generator import save_gift_qr_code
from .email_helpers import send_journey_gift_notification

logger = logging.getLogger(__name__)


def _sender_is_verified(user):
    """True when the sender is an active, unbanned account holding a
    coach-approved, still-active profile."""
    return (
        CrushProfile.objects.filter(
            user=user,
            user__is_active=True,
            is_approved=True,
            is_active=True,
        )
        .exclude(user__data_consent__crushlu_banned=True)
        .exists()
    )


def _is_gift_admin(user):
    """Journey gifts are an admin/coach tool (decision C, finding 7-05):
    staff or an active coach may create and list them."""
    if user.is_staff:
        return True
    coach = getattr(user, "crushcoach", None)
    return bool(coach and coach.is_active)


def _gift_admin_only(view):
    """Members get a plain 404: the sender routes are not a member feature.

    Sits above @ratelimit so a denied member never reaches the rate counter
    (a 429 would reveal the route exists)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not _is_gift_admin(request.user):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper


def _unclaimable_response(request, gift):
    """The status page for a gift that can no longer be claimed.

    The recipient who claimed it goes back to their own Wonderland."""
    if request.user.is_authenticated and gift.claimed_by_id == request.user.pk:
        return redirect("crush_lu:journey_map_wonderland")
    template = "crush_lu/journey/gift_expired.html"
    if gift.status in (JourneyGift.Status.CLAIMED, JourneyGift.Status.COMPLETED):
        template = "crush_lu/journey/gift_claimed.html"
    return render(request, template, {"gift": gift})


@crush_login_required
@_gift_admin_only
@require_http_methods(["GET", "POST"])
@ratelimit(key="user", rate="5/d", method="POST", block=True)
def gift_create(request):
    """
    Create a new journey gift.

    Only staff and active coaches can create a gift (decision C; the
    optional recipient email makes this an outbound-mail surface), capped
    at 5 per day.
    A QR code is generated for sharing.
    If recipient email is provided, sends notification email with QR code.
    """
    if request.method == "POST":
        form = JourneyGiftForm(request.POST, request.FILES)
        if form.is_valid():
            # Create the gift
            gift = form.save(commit=False)
            gift.sender = request.user
            gift.save()

            # Generate QR code (non-critical - gift works without it)
            try:
                save_gift_qr_code(gift)
            except Exception as e:
                logger.warning(
                    f"Failed to generate QR code for gift {gift.gift_code}: {e}"
                )

            # Send email notification if recipient email provided
            if gift.recipient_email:
                try:
                    email_sent = send_journey_gift_notification(gift, request)
                    if email_sent:
                        messages.success(
                            request,
                            _("Your gift has been created and sent to %(email)s!")
                            % {"email": gift.recipient_email},
                        )
                    else:
                        messages.success(
                            request,
                            _("Your gift has been created! Share the QR code below."),
                        )
                except Exception as e:
                    logger.error(
                        f"Failed to send gift notification email: {e}", exc_info=True
                    )
                    messages.success(
                        request,
                        _("Your gift has been created! Share the QR code below."),
                    )
            else:
                messages.success(
                    request, _("Your gift has been created! Share the QR code below.")
                )

            return redirect("crush_lu:gift_success", gift_code=gift.gift_code)
    else:
        form = JourneyGiftForm()

    return render(
        request,
        "crush_lu/journey/gift_create.html",
        {
            "form": form,
        },
    )


@login_required
@_gift_admin_only
def gift_success(request, gift_code):
    """
    Display the success page with the QR code after gift creation.

    Shows the QR code and provides sharing options.
    """
    gift = get_object_or_404(JourneyGift, gift_code=gift_code, sender=request.user)

    return render(
        request,
        "crush_lu/journey/gift_success.html",
        {
            "gift": gift,
        },
    )


def gift_landing(request, gift_code):
    """
    Public landing page for a gift.

    Non-authenticated users see the gift preview and can start the claim flow.
    """
    gift = get_object_or_404(JourneyGift, gift_code=gift_code)

    if not gift.is_claimable:
        return _unclaimable_response(request, gift)

    # If user is logged in, redirect to claim page
    if request.user.is_authenticated:
        return redirect("crush_lu:gift_claim", gift_code=gift_code)

    # Store gift code in session for post-signup claiming
    request.session["pending_gift_code"] = gift_code

    return render(
        request,
        "crush_lu/journey/gift_landing.html",
        {
            "gift": gift,
            "sender_verified": _sender_is_verified(gift.sender),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def gift_claim(request, gift_code):
    """
    Claim a gift and create the journey.

    Authenticated users can claim a pending gift.
    """
    gift = get_object_or_404(JourneyGift, gift_code=gift_code)

    if not gift.is_claimable:
        return _unclaimable_response(request, gift)

    if request.method == "POST":
        try:
            # Claim the gift - this creates the journey
            journey = gift.claim(request.user)

            # Clear session gift code if present
            if "pending_gift_code" in request.session:
                del request.session["pending_gift_code"]

            messages.success(
                request, _("Welcome to your Wonderland! Your journey has been created.")
            )
            return redirect("crush_lu:journey_map_wonderland")

        except GiftNoLongerClaimable:
            # Reported, claimed or expired since the page loaded.
            messages.error(request, _("This gift is no longer active."))
            return redirect("crush_lu:gift_landing", gift_code=gift_code)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("crush_lu:gift_landing", gift_code=gift_code)

    return render(
        request,
        "crush_lu/journey/gift_claim.html",
        {
            "gift": gift,
            "sender_verified": _sender_is_verified(gift.sender),
        },
    )


@ratelimit(key="ip", rate="10/h", method="POST", block=True)
@require_POST
def gift_report(request, gift_code):
    """
    Recipient says "This isn't for me": expire the gift and alert the team.

    Public (the landing page is shown to signed-out recipients), POST + CSRF.
    Only an unclaimed gift is expired; a claimed gift is left untouched.
    """
    from .notification_service import notify_gift_reported

    gift = get_object_or_404(JourneyGift, gift_code=gift_code)

    # Expiry and staff alert commit together: a report that reached nobody
    # is rolled back so the gift stays reportable and the recipient can retry.
    notified = 0
    with transaction.atomic():
        updated = JourneyGift.objects.filter(
            pk=gift.pk,
            status__in=[JourneyGift.Status.PENDING, JourneyGift.Status.CLAIM_FAILED],
        ).update(status=JourneyGift.Status.EXPIRED)
        if updated:
            try:
                notified = notify_gift_reported(gift)
            except Exception:
                logger.exception(
                    "Gift-reported notification failed for gift %s", gift.pk
                )
            if not notified:
                transaction.set_rollback(True)

    target = reverse("crush_lu:home")
    if updated and not notified:
        # Back to the gift, whose report button is still live.
        target = reverse("crush_lu:gift_landing", kwargs={"gift_code": gift_code})
        messages.error(
            request,
            _(
                "We couldn't send your report right now. The gift is still "
                "open. Please try again in a moment."
            ),
        )
    elif updated:
        if request.session.get("pending_gift_code") == gift_code:
            del request.session["pending_gift_code"]
        messages.success(
            request,
            _(
                "Thanks for letting us know. This gift is now closed and our "
                "team has been notified."
            ),
        )
    else:
        # Already claimed, expired or reported: nothing closed, nobody notified.
        messages.info(request, _("This gift is no longer active."))
    if request.headers.get("HX-Request"):
        return HttpResponse(headers={"HX-Redirect": target})
    return redirect(target)


@login_required
@_gift_admin_only
def gift_list(request):
    """
    List all gifts created by the current user.

    Shows gift status and allows tracking of sent gifts.
    """
    gifts = JourneyGift.objects.filter(sender=request.user).order_by("-created_at")

    return render(
        request,
        "crush_lu/journey/gift_list.html",
        {
            "gifts": gifts,
        },
    )
