import json
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from crush_lu.models.events import (
    SEAT_HOLDING_STATUSES,
    EventRegistration,
    MeetupEvent,
)
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

    # Every eligibility check lives inside the lock below, on re-read rows --
    # duplicating them out here only invites the two copies to drift.

    # Always open a FRESH checkout; supersede any older one.
    #
    # Reuse-and-reconcile was removed because it required asking SumUp whether
    # an old checkout was still payable, and that call can itself apply a
    # payment -- sequencing it against an editable price, a cancellable event
    # and a concurrent webhook produced a new defect three rounds running.
    # Creating a new checkout every time removes the question: nothing to
    # reconcile, no staleness, no provider call on this path.
    #
    # Superseding is what replaces reuse as double-charge protection: the old
    # checkout is deactivated at SumUp so only the newest is payable.
    #
    # LOCK ORDER: PaymentTransaction rows FIRST, then EventRegistration -- the
    # same order _apply_paid_checkout uses. Taking them the other way round is
    # an ABBA deadlock against a webhook for one of those very rows, and
    # PostgreSQL resolves it by aborting somebody. This has been got wrong more
    # than once here; the order is the invariant, not the comment.
    with transaction.atomic():
        superseded = list(
            PaymentTransaction.objects.select_for_update()
            .filter(
                event_registration_id=registration.pk,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                status=PaymentTransaction.Status.PENDING,
            )
            .exclude(sumup_checkout_id="")
        )
        registration = EventRegistration.objects.select_for_update().get(
            pk=registration.pk
        )
        event = MeetupEvent.objects.only("registration_fee", "is_cancelled").get(
            pk=registration.event_id
        )
        amount = event.registration_fee

        if registration.payment_confirmed:
            return JsonResponse(
                {"error": _("This registration is already paid.")}, status=400
            )
        if registration.status not in ("pending", "confirmed"):
            return JsonResponse(
                {
                    "error": _(
                        "This registration cannot be paid for in its current state."
                    )
                },
                status=400,
            )
        if event.is_cancelled:
            return JsonResponse(
                {"error": _("This event has been cancelled.")}, status=400
            )
        if amount <= Decimal("0.00"):
            return JsonResponse(
                {"error": _("This event does not require payment.")}, status=400
            )

        client = SumUpClient()
        for old in superseded:
            if client.deactivate_checkout(old.sumup_checkout_id):
                old.status = PaymentTransaction.Status.FAILED
                old.save(update_fields=["status"])
                logger.info(
                    "Superseded SumUp checkout %s for registration %s",
                    old.sumup_checkout_id,
                    registration.id,
                )
            else:
                # Deactivation failed -- SumUp unreachable, or the checkout was
                # just PAID and can no longer be cancelled. Leave the row
                # PENDING: _sync_checkout_with_sumup returns immediately for any
                # non-PENDING row, so marking it terminal here would make a
                # captured payment permanently invisible to both the webhook and
                # the browser return, leaving the member unpaid and chargeable
                # again on the new checkout.
                logger.warning(
                    "Could not deactivate SumUp checkout %s for registration %s "
                    "— left PENDING so reconciliation can still apply it",
                    old.sumup_checkout_id,
                    registration.id,
                )

        # Re-read immediately before exposing a payable widget. The deactivate
        # calls above are network I/O, and an organiser can cancel the event
        # while they are in flight -- returning a widget then means a captured
        # charge with no seat and a manual refund.
        event.refresh_from_db()
        if event.is_cancelled:
            return JsonResponse(
                {"error": _("This event has been cancelled.")}, status=400
            )
        amount = event.registration_fee

        checkout_ref = f"CRUSH-EVT-{registration.id}-{uuid.uuid4().hex[:6]}"
        description = f"Crush.lu Event: {registration.event.title[:50]}"
        return_url = request.build_absolute_uri(
            f"/payments/sumup/return/?ref={checkout_ref}"
        )

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


def _send_registration_confirmation_safely(registration):
    """Send the post-payment confirmation without letting it break the payment.

    The money is already captured by the time this runs; a mail failure must not
    surface as an error to SumUp's callback or to the returning browser.
    """
    from .email_helpers import send_event_registration_confirmation

    try:
        send_event_registration_confirmation(registration)
    except Exception as exc:
        logger.error(
            "Failed to send post-payment confirmation for registration %s: %s",
            registration.id,
            type(exc).__name__,
        )


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
            # Lock and re-read the registration before deciding anything about
            # it. Locking only the PaymentTransaction left the revalidation
            # non-atomic: event_cancel() locks and updates the registration
            # independently, so this could read "pending", block behind that
            # cancellation committing, and then overwrite it with "confirmed".
            reg = EventRegistration.objects.select_for_update().get(
                pk=locked.event_registration_id
            )

            # Re-validate at completion, not just at checkout creation. A member
            # can open the widget and then cancel (or the organiser can cancel
            # the event) while the payment is in flight; the checkout stays
            # payable, and confirming unconditionally would resurrect a seat the
            # member deliberately released, or sell one for an event that is off.
            #
            # SumUp has already captured the money by the time this runs, so the
            # transaction is still marked PAID -- dropping it would lose the only
            # record of a real charge. What we refuse to do is hand back the
            # seat. Logged at error level because this needs a human refund.
            # Allow-list, not "reject cancelled". Staff can move a row to
            # waitlist or no_show while the widget is open; confirming those
            # would hand a waitlisted member an over-capacity seat or erase a
            # recorded no-show. Only a seat-holding status may be confirmed by
            # a payment ("confirmed" covers legacy unpaid rows).
            if reg.status not in SEAT_HOLDING_STATUSES or reg.event.is_cancelled:
                logger.error(
                    "SumUp payment %s completed for registration %s but it "
                    "no longer holds a seat (registration=%s, event_cancelled=%s) "
                    "— payment recorded, seat NOT restored, refund required.",
                    locked.transaction_reference,
                    reg.id,
                    reg.status,
                    reg.event.is_cancelled,
                )
                return

            # The seat must have been paid for at its CURRENT price. A widget
            # left open across an admin fee change captures the old amount, and
            # the fresh-checkout refactor removed the pre-payment price check
            # without leaving anything at completion -- so a stale-priced
            # payment bought the seat outright. Judged here, where the amount
            # actually captured is known.
            if locked.amount != reg.event.registration_fee:
                logger.error(
                    "SumUp payment %s completed for registration %s at %s %s but "
                    "the event fee is now %s EUR — payment recorded, seat NOT "
                    "confirmed, refund or top-up required.",
                    locked.transaction_reference,
                    reg.id,
                    locked.amount,
                    locked.currency,
                    reg.event.registration_fee,
                )
                return

            if reg.status != "confirmed" or not reg.payment_confirmed:
                reg.payment_confirmed = True
                reg.payment_date = timezone.now()
                # Never walk "attended" back to "confirmed". A pending seat can
                # be scanned at the door while its payment is still settling
                # (that is the whole point of admitting pending at check-in), and
                # downgrading here would drop a person who physically attended
                # out of every attended-only report, the lobby and the recap --
                # while checked_in_at still says they were there.
                if reg.status != "attended":
                    reg.status = "confirmed"
                reg.save()
                _generate_checkin_token(reg)
                logger.info("Confirmed EventRegistration %s via SumUp", reg.id)

                # The payment-pending email promises "you'll receive a
                # confirmation email once payment is received" -- nothing was
                # keeping that promise, so a paying customer heard nothing.
                # on_commit, because this runs inside the atomic block above and
                # the mail must not go out if the transaction rolls back. It sits
                # inside the idempotency guard, so the browser return racing
                # SumUp's callback still sends exactly one.
                transaction.on_commit(
                    lambda r=reg: _send_registration_confirmation_safely(r)
                )

        elif (
            locked.purpose == PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP
            and locked.premium_membership
        ):
            pm = locked.premium_membership
            if pm.status == "pending":
                # No pre-setting of payment_confirmed/payment_date here.
                # ``confirm()`` sets both itself on success, and on failure it
                # raises *before* its own save(), so assigning them first only
                # ever produced in-memory values that were silently discarded —
                # which read like the money had been recorded on the membership
                # when it had not. The charge is recorded on this
                # PaymentTransaction either way; that is the money's home.
                try:
                    pm.confirm()
                    logger.info("Confirmed PremiumMembership %s via SumUp", pm.id)
                except ValueError as exc:
                    # Same contract as the event-registration branch above:
                    # SumUp has already captured the money by the time this
                    # runs, so the transaction stays PAID — dropping it would
                    # lose the only record of a real charge. What we refuse to
                    # do is grant Premium.
                    #
                    # confirm() raises for two distinct reasons, and the
                    # remedies differ: the chosen coach filled up while the
                    # payment was in flight (reassign the member to a coach with
                    # capacity — that keeps the sale), or the request stopped
                    # being pending mid-flight, i.e. it was cancelled (refund).
                    # Both need a human, so this is logged at error level with
                    # the same "payment recorded, NOT granted" phrasing the
                    # seat path uses, and carries the ids needed to act on it.
                    logger.error(
                        "SumUp payment %s completed for PremiumMembership %s "
                        "(user=%s, coach=%s) but it could not be confirmed: %s "
                        "— payment recorded, Premium NOT granted, coach "
                        "reassignment or refund required.",
                        locked.transaction_reference,
                        pm.id,
                        pm.user_id,
                        pm.coach_id,
                        exc,
                    )


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
            # The hub's Premium badge is not reachable on the one page-load
            # that matters most. premium_choose_coach requires a profile but
            # NOT a CrushConnectMembership, so buying before finishing Connect
            # onboarding is a supported path — and _hub_access_blocker bounces
            # exactly that member straight back out of the hub (to the wizard,
            # or to the teaser without LuxID) before the badge is rendered.
            # A message survives the redirect chain and lands wherever they
            # end up, so the confirmation is not conditional on onboarding.
            pm = tx_obj.premium_membership
            pm.refresh_from_db()
            if pm.status == "active":
                # Only claim Premium once confirm() actually granted it. When
                # the coach filled up mid-flight the charge is real but the
                # entitlement is not (see _apply_paid_checkout) — telling that
                # member they are Premium would be a lie the hub then denies.
                coach_user = getattr(pm.coach, "user", None)
                coach_name = (
                    (coach_user.get_full_name() or coach_user.username)
                    if coach_user
                    else ""
                )
                if coach_name:
                    messages.success(
                        request,
                        _("You're Premium — your coach is %(name)s.")
                        % {"name": coach_name},
                    )
                else:
                    messages.success(request, _("You're now a Premium member."))
            return redirect("crush_lu:crush_connect_hub")
    else:
        messages.warning(request, _("Payment is pending or was not completed."))

    return redirect("crush_lu:home")


@login_required
def sumup_widget_view(request, checkout_id):
    """
    Renders the standalone SumUp Payment Card Widget page.
    """
    # Authorise by who the payment is FOR, not by who happened to create the row.
    # create_sumup_event_checkout explicitly lets staff act for a member, and
    # stamps user=request.user -- so a staff-opened checkout stored the staff
    # user and 404'd when the member clicked Pay and was handed it for reuse (and
    # vice versa). The registration's owner is the authority; staff keep access.
    tx_obj = get_object_or_404(PaymentTransaction, sumup_checkout_id=checkout_id)
    owner_ids = {tx_obj.user_id}
    if tx_obj.event_registration_id:
        owner_ids.add(tx_obj.event_registration.user_id)
    if tx_obj.premium_membership_id:
        owner_ids.add(tx_obj.premium_membership.user_id)
    if request.user.id not in owner_ids and not request.user.is_staff:
        raise Http404("No payment found.")
    context = {
        "checkout_id": checkout_id,
        "transaction": tx_obj,
        "amount": tx_obj.amount,
        "currency": tx_obj.currency,
        "return_url": request.build_absolute_uri(f"/payments/sumup/return/?ref={tx_obj.transaction_reference}"),
    }
    return render(request, "crush_lu/payments/sumup_widget.html", context)
