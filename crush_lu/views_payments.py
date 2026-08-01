import json
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

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

    if registration.status != "pending" and not registration.payment_confirmed:
        return JsonResponse({"error": _("This registration is not pending payment.")}, status=400)

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
        return JsonResponse({"error": str(exc)}, status=500)

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
        return JsonResponse({"error": str(exc)}, status=500)

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


@csrf_exempt
@require_POST
def sumup_webhook(request):
    """
    Asynchronous Webhook receiver endpoint for SumUp Payment Status notifications.
    Endpoint: POST /payments/sumup/webhook/
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponse("Invalid payload", status=400)

    checkout_id = payload.get("id") or payload.get("checkout_id")
    status_str = (payload.get("status") or payload.get("event_type") or "").upper()

    if not checkout_id:
        return HttpResponse("Missing checkout_id", status=400)

    logger.info("Received SumUp webhook for checkout %s with status %s", checkout_id, status_str)

    try:
        tx_obj = PaymentTransaction.objects.select_for_update().get(sumup_checkout_id=checkout_id)
    except PaymentTransaction.DoesNotExist:
        logger.warning("PaymentTransaction not found for SumUp checkout ID %s", checkout_id)
        return JsonResponse({"status": "ignored", "reason": "transaction not found"})

    if status_str in ["PAID", "SUCCESSFUL", "CHECKOUT_COMPLETED"]:
        with transaction.atomic():
            tx_obj.status = PaymentTransaction.Status.PAID
            tx_obj.raw_response = payload
            tx_obj.save()

            if tx_obj.purpose == PaymentTransaction.Purpose.EVENT_REGISTRATION and tx_obj.event_registration:
                reg = tx_obj.event_registration
                if reg.status != "confirmed" or not reg.payment_confirmed:
                    reg.payment_confirmed = True
                    reg.payment_date = timezone.now()
                    reg.status = "confirmed"
                    reg.save()
                    _generate_checkin_token(reg)
                    logger.info("Confirmed EventRegistration %s via SumUp Webhook", reg.id)

            elif tx_obj.purpose == PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP and tx_obj.premium_membership:
                pm = tx_obj.premium_membership
                if pm.status == "pending":
                    pm.payment_confirmed = True
                    pm.payment_date = timezone.now()
                    try:
                        pm.confirm()
                        logger.info("Confirmed PremiumMembership %s via SumUp Webhook", pm.id)
                    except ValueError as exc:
                        logger.error("Could not confirm PremiumMembership %s: %s", pm.id, exc)

    elif status_str in ["FAILED", "CANCELLED", "EXPIRED"]:
        tx_obj.status = PaymentTransaction.Status.FAILED
        tx_obj.raw_response = payload
        tx_obj.save()

    return JsonResponse({"status": "ok"})


@login_required
def sumup_payment_return(request):
    """
    Return URL endpoint after completing the SumUp Checkout widget.
    Verifies payment state server-side and redirects user with confirmation.
    """
    ref = request.GET.get("ref") or request.GET.get("checkout_reference")
    checkout_id = request.GET.get("checkout_id")

    tx_obj = None
    if ref:
        tx_obj = PaymentTransaction.objects.filter(transaction_reference=ref).first()
    elif checkout_id:
        tx_obj = PaymentTransaction.objects.filter(sumup_checkout_id=checkout_id).first()

    if not tx_obj:
        messages.error(request, _("Payment transaction reference not found."))
        return redirect("crush_lu:home")

    # Verify status directly with SumUp API if still pending
    if tx_obj.status == PaymentTransaction.Status.PENDING:
        client = SumUpClient()
        try:
            data = client.get_checkout(tx_obj.sumup_checkout_id)
            sumup_status = (data.get("status") or "").upper()
            if sumup_status in ["PAID", "SUCCESSFUL"]:
                with transaction.atomic():
                    tx_obj.status = PaymentTransaction.Status.PAID
                    tx_obj.raw_response = data
                    tx_obj.save()

                    if tx_obj.purpose == PaymentTransaction.Purpose.EVENT_REGISTRATION and tx_obj.event_registration:
                        reg = tx_obj.event_registration
                        reg.payment_confirmed = True
                        reg.payment_date = timezone.now()
                        reg.status = "confirmed"
                        reg.save()
                        _generate_checkin_token(reg)
                    elif tx_obj.purpose == PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP and tx_obj.premium_membership:
                        pm = tx_obj.premium_membership
                        if pm.status == "pending":
                            pm.payment_confirmed = True
                            pm.payment_date = timezone.now()
                            try:
                                pm.confirm()
                            except ValueError:
                                pass
        except SumUpError as exc:
            logger.warning("Return verification get_checkout failed: %s", exc)

    if tx_obj.status == PaymentTransaction.Status.PAID:
        messages.success(request, _("Payment completed successfully! Thank you."))
        if tx_obj.event_registration:
            return redirect("crush_lu:event_detail", pk=tx_obj.event_registration.event.pk)
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
