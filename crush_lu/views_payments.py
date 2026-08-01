import json
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from crush_lu.models.events import EventRegistration
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import PremiumMembership
from crush_lu.services.sumup import SumUpClient, SumUpError
from crush_lu.views_ticket import _generate_checkin_token

logger = logging.getLogger(__name__)


@login_required
@require_POST
def create_sumup_event_checkout(request, registration_id):
    """
    Creates a SumUp checkout session for an event registration.
    """
    registration = get_object_or_404(EventRegistration, pk=registration_id)

    if registration.user != request.user and not request.user.is_staff:
        return JsonResponse({"error": _("Unauthorized access to this registration.")}, status=403)

    # Gate on payment_confirmed, not status. A normal signup lands in
    # status="confirmed" long before it is paid (views_events.py), and the
    # payment return handler also sets status="confirmed" once it *is* paid --
    # so status cannot tell the two apart and only the flag can. This mirrors
    # the condition the templates use to show the Pay button. The old check
    # (`status != "pending" and not payment_confirmed`) was wrong both ways:
    # it rejected every unpaid confirmed registration, and it let an already
    # paid one start a second checkout.
    if registration.payment_confirmed:
        return JsonResponse(
            {"error": _("This registration is already paid.")}, status=400
        )

    if registration.status == "cancelled":
        return JsonResponse(
            {"error": _("This registration has been cancelled.")}, status=400
        )

    amount = registration.event.registration_fee
    if amount <= Decimal("0.00"):
        return JsonResponse({"error": _("This event does not require payment.")}, status=400)

    checkout_ref = f"CRUSH-EVT-{registration.id}-{uuid.uuid4().hex[:6]}"
    description = f"Crush.lu Event: {registration.event.title[:50]}"
    return_url = request.build_absolute_uri(f"/payments/sumup/return/?ref={checkout_ref}")

    client = SumUpClient()
    try:
        checkout_data = client.create_checkout(
            amount=float(amount),
            currency="EUR",
            checkout_reference=checkout_ref,
            description=description,
            return_url=return_url,
        )
    except SumUpError as exc:
        logger.error("Failed to create SumUp event checkout: %s", exc)
        return JsonResponse(
            {"error": _("Unable to initiate payment at the moment. Please try again later.")},
            status=500,
        )

    checkout_id = checkout_data.get("id")
    if not checkout_id:
        return JsonResponse({"error": _("SumUp did not return a valid checkout ID.")}, status=500)

    PaymentTransaction.objects.create(
        transaction_reference=checkout_ref,
        provider=PaymentTransaction.Provider.SUMUP,
        sumup_checkout_id=checkout_id,
        amount=amount,
        currency="EUR",
        status=PaymentTransaction.Status.PENDING,
        purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
        user=request.user,
        event_registration=registration,
        raw_response=checkout_data,
    )

    return JsonResponse({
        "success": True,
        "checkout_id": checkout_id,
        "checkout_reference": checkout_ref,
        "amount": float(amount),
        "currency": "EUR",
        "widget_url": f"/payments/sumup/widget/{checkout_id}/",
    })


@login_required
@require_POST
def create_sumup_premium_checkout(request, membership_id):
    """
    Creates a SumUp checkout session for a Crush Connect Premium Membership.
    Configures card tokenization (`SETUP_RECURRING_PAYMENT`) for monthly billing.
    """
    membership = get_object_or_404(PremiumMembership, pk=membership_id)

    if membership.user != request.user and not request.user.is_staff:
        return JsonResponse({"error": _("Unauthorized access to this membership.")}, status=403)

    if membership.status != "pending":
        return JsonResponse({"error": _("This membership is not pending payment.")}, status=400)

    fee = getattr(settings, "SUMUP_PREMIUM_MONTHLY_FEE", 10.00)
    amount = Decimal(str(fee))

    checkout_ref = f"CRUSH-PREM-{membership.id}-{uuid.uuid4().hex[:6]}"
    description = f"Crush Connect Premium - Coach {membership.coach}"
    return_url = request.build_absolute_uri(f"/payments/sumup/return/?ref={checkout_ref}")

    sumup_customer_id = f"crush-user-{request.user.id}"
    client = SumUpClient()

    # Ensure customer object exists on SumUp side
    try:
        client.create_customer(
            customer_id=sumup_customer_id,
            email=request.user.email or f"user{request.user.id}@crush.lu",
            name=request.user.get_full_name() or request.user.username,
        )
    except SumUpError as exc:
        logger.info("SumUp customer creation returned (may already exist): %s", exc)

    try:
        checkout_data = client.create_checkout(
            amount=float(amount),
            currency="EUR",
            checkout_reference=checkout_ref,
            description=description,
            return_url=return_url,
            customer_id=sumup_customer_id,
            purpose="SETUP_RECURRING_PAYMENT",
        )
    except SumUpError as exc:
        logger.error("Failed to create SumUp premium checkout: %s", exc)
        return JsonResponse(
            {"error": _("Unable to initiate payment at the moment. Please try again later.")},
            status=500,
        )

    checkout_id = checkout_data.get("id")
    if not checkout_id:
        return JsonResponse({"error": _("SumUp did not return a valid checkout ID.")}, status=500)

    PaymentTransaction.objects.create(
        transaction_reference=checkout_ref,
        provider=PaymentTransaction.Provider.SUMUP,
        sumup_checkout_id=checkout_id,
        sumup_customer_id=sumup_customer_id,
        amount=amount,
        currency="EUR",
        status=PaymentTransaction.Status.PENDING,
        purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
        user=request.user,
        premium_membership=membership,
        raw_response=checkout_data,
    )

    return JsonResponse({
        "success": True,
        "checkout_id": checkout_id,
        "checkout_reference": checkout_ref,
        "amount": float(amount),
        "currency": "EUR",
        "widget_url": f"/payments/sumup/widget/{checkout_id}/",
    })


def _apply_paid_checkout(tx_obj, data):
    """Mark the transaction paid and unlock whatever it bought.

    Callers must have read the paid state *from SumUp* — never from a request
    body. Idempotent under concurrency: the browser return and SumUp's server
    callback routinely race each other, so the row is locked and re-checked
    before any side effect (a second run would re-issue the check-in token).
    """
    with transaction.atomic():
        # select_for_update() has to run inside a transaction — ATOMIC_REQUESTS
        # is off, so locking outside one raises TransactionManagementError.
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx_obj.pk)
        if locked.status == PaymentTransaction.Status.PAID:
            return

        locked.status = PaymentTransaction.Status.PAID
        locked.raw_response = data
        locked.save()

        if (
            locked.purpose == PaymentTransaction.Purpose.EVENT_REGISTRATION
            and locked.event_registration
        ):
            reg = locked.event_registration
            if reg.status != "confirmed" or not reg.payment_confirmed:
                reg.payment_confirmed = True
                reg.payment_date = timezone.now()
                reg.status = "confirmed"
                reg.save()
                _generate_checkin_token(reg)
                logger.info("Confirmed EventRegistration %s via SumUp", reg.id)

        elif (
            locked.purpose == PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP
            and locked.premium_membership
        ):
            pm = locked.premium_membership
            if pm.status == "pending":
                pm.payment_confirmed = True
                pm.payment_date = timezone.now()
                try:
                    pm.confirm()
                    logger.info("Confirmed PremiumMembership %s via SumUp", pm.id)
                except ValueError as exc:
                    logger.error("Could not confirm PremiumMembership %s: %s", pm.id, exc)


def _sync_checkout_with_sumup(tx_obj):
    """Re-read the checkout from SumUp and apply whatever it reports.

    Every path that can mark a payment complete funnels through here, so no
    caller has to be trusted — both the webhook and the return URL are public,
    unauthenticated endpoints.
    """
    if tx_obj.status != PaymentTransaction.Status.PENDING:
        return

    try:
        data = SumUpClient().get_checkout(tx_obj.sumup_checkout_id)
    except SumUpError as exc:
        logger.warning(
            "SumUp verification failed for checkout %s: %s", tx_obj.sumup_checkout_id, exc
        )
        return

    sumup_status = (data.get("status") or "").upper()
    if sumup_status in ("PAID", "SUCCESSFUL"):
        _apply_paid_checkout(tx_obj, data)
    elif sumup_status in ("FAILED", "CANCELLED", "EXPIRED"):
        tx_obj.status = PaymentTransaction.Status.FAILED
        tx_obj.raw_response = data
        tx_obj.save()

    tx_obj.refresh_from_db()


@csrf_exempt
@require_POST
def sumup_webhook(request):
    """
    Asynchronous webhook receiver for SumUp payment status notifications.
    Endpoint: POST /payments/sumup/webhook/

    The endpoint is public and unauthenticated, so the posted status is a
    *hint only* — it names a checkout to go and re-read. Acting on
    payload["status"] directly would let anyone confirm a registration, and
    mint themselves a check-in token, with a forged POST.
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse("Invalid payload", status=400)

    checkout_id = payload.get("id") or payload.get("checkout_id")
    if not checkout_id:
        return HttpResponse("Missing checkout_id", status=400)

    logger.info(
        "Received SumUp webhook for checkout %s with status %s",
        checkout_id,
        (payload.get("status") or payload.get("event_type") or "").upper(),
    )

    tx_obj = PaymentTransaction.objects.filter(sumup_checkout_id=checkout_id).first()
    if not tx_obj:
        logger.warning("PaymentTransaction not found for SumUp checkout ID %s", checkout_id)
        return JsonResponse({"status": "ignored", "reason": "transaction not found"})

    _sync_checkout_with_sumup(tx_obj)
    return JsonResponse({"status": "ok"})


@csrf_exempt
def sumup_payment_return(request):
    """
    Return URL for a SumUp checkout.

    Two very different callers land here. The browser arrives by GET once the
    widget reports success. SumUp's platform *also* POSTs the checkout status
    server-to-server (user agent ReactorNetty, no session, no CSRF token) —
    that request was being rejected with 403, so a customer who closed the tab
    before the redirect never had their registration confirmed. Neither path
    trusts the request: both re-read the checkout from SumUp.
    """
    ref = request.GET.get("ref") or request.GET.get("checkout_reference")
    checkout_id = request.GET.get("checkout_id")

    tx_obj = None
    if ref:
        tx_obj = PaymentTransaction.objects.filter(transaction_reference=ref).first()
    elif checkout_id:
        tx_obj = PaymentTransaction.objects.filter(sumup_checkout_id=checkout_id).first()

    if request.method == "POST":
        if not tx_obj:
            logger.warning("SumUp return POST for unknown reference %s", ref or checkout_id)
            return JsonResponse({"status": "ignored", "reason": "transaction not found"})
        _sync_checkout_with_sumup(tx_obj)
        return JsonResponse({"status": "ok"})

    # Everything below is the human-facing page, so the login gate applies here
    # rather than as a decorator — a decorator would bounce SumUp's POST to the
    # login form and silently drop the notification.
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())

    if not tx_obj:
        messages.error(request, _("Payment transaction reference not found."))
        return redirect("crush_lu:home")

    _sync_checkout_with_sumup(tx_obj)

    if tx_obj.status == PaymentTransaction.Status.PAID:
        messages.success(request, _("Payment completed successfully! Thank you."))
        if tx_obj.event_registration:
            # The route is events/<int:event_id>/ — passing pk raises
            # NoReverseMatch, and it fires *after* the payment is recorded, so
            # the user sees a 500 on a purchase that actually succeeded.
            return redirect(
                "crush_lu:event_detail", event_id=tx_obj.event_registration.event.pk
            )
        elif tx_obj.premium_membership:
            return redirect("crush_lu:crush_connect_hub")
    else:
        messages.warning(request, _("Payment is pending or was not completed."))

    return redirect("crush_lu:home")


@login_required
def sumup_widget_view(request, checkout_id):
    """
    Renders the standalone SumUp Payment Card Widget page.
    """
    tx_obj = get_object_or_404(PaymentTransaction, sumup_checkout_id=checkout_id, user=request.user)
    context = {
        "checkout_id": checkout_id,
        "transaction": tx_obj,
        "amount": tx_obj.amount,
        "currency": tx_obj.currency,
        "return_url": request.build_absolute_uri(f"/payments/sumup/return/?ref={tx_obj.transaction_reference}"),
    }
    return render(request, "crush_lu/payments/sumup_widget.html", context)
