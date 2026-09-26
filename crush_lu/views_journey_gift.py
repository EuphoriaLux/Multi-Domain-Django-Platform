"""
Views for the Journey Gift system.

Handles gift creation, landing page, and claiming flow.
"""

import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from .decorators import crush_login_required, ratelimit
from .models import CrushProfile, JourneyGift
from .forms import JourneyGiftForm
from .utils.qr_generator import save_gift_qr_code
from .email_helpers import send_journey_gift_notification

logger = logging.getLogger(__name__)


def _sender_is_verified(user):
    """True when the gift sender holds a coach-approved Crush.lu profile."""
    return CrushProfile.objects.filter(user=user, is_approved=True).exists()


def _gift_sender_block_redirect(request):
    """Redirect unapproved senders away from gift creation.

    A gift can email any address, so only coach-screened members may send one.
    Banned accounts never get here: CrushConsentMiddleware sends them to the
    banned page on every Crush.lu path.
    """
    if not _sender_is_verified(request.user):
        messages.info(
            request,
            _(
                "You can send a Wonderland gift once a coach has approved "
                "your profile."
            ),
        )
        return redirect("crush_lu:dashboard")
    return None


@crush_login_required
@require_http_methods(["GET", "POST"])
@ratelimit(key="user", rate="5/d", method="POST", block=True)
def gift_create(request):
    """
    Create a new journey gift.

    Only approved members can create a gift (7-01: the optional
    recipient email makes this an outbound-mail surface), capped at 5 per day.
    A QR code is generated for sharing.
    If recipient email is provided, sends notification email with QR code.
    """
    blocked = _gift_sender_block_redirect(request)
    if blocked:
        return blocked

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

    # Check if gift is claimable
    if not gift.is_claimable:
        if gift.is_expired:
            return render(request, "crush_lu/journey/gift_expired.html", {"gift": gift})
        elif gift.status == JourneyGift.Status.CLAIMED:
            return render(request, "crush_lu/journey/gift_claimed.html", {"gift": gift})

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

    # Check if gift is claimable
    if not gift.is_claimable:
        if gift.is_expired:
            messages.error(request, _("This gift has expired."))
        elif gift.status == JourneyGift.Status.CLAIMED:
            messages.error(request, _("This gift has already been claimed."))
        return redirect("crush_lu:journey_map_wonderland")

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
    gift = get_object_or_404(JourneyGift, gift_code=gift_code)

    updated = JourneyGift.objects.filter(
        pk=gift.pk,
        status__in=[JourneyGift.Status.PENDING, JourneyGift.Status.CLAIM_FAILED],
    ).update(status=JourneyGift.Status.EXPIRED)
    if updated:
        request.session.pop("pending_gift_code", None)
        try:
            from .notification_service import notify_gift_reported

            notify_gift_reported(gift)
        except Exception:  # notification must never break the report
            logger.exception("Gift-reported notification failed for gift %s", gift.pk)

    messages.success(
        request,
        _(
            "Thanks for letting us know. This gift is now closed and our team "
            "has been notified."
        ),
    )
    if request.headers.get("HX-Request"):
        return HttpResponse(headers={"HX-Redirect": reverse("crush_lu:home")})
    return redirect("crush_lu:home")


@login_required
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
