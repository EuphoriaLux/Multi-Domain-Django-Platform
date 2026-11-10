"""Donations, event seats and Premium through Mollie's hosted checkout.

SumUp refused the platform in 2026-10; donations moved to Mollie first, then
event seats (``MOLLIE_EVENT_PAYMENTS_ENABLED``), then the first Premium payment
(``MOLLIE_PREMIUM_ENABLED``, ``open_mollie_premium_checkout``).
A payment here ends in the same ``_apply_paid_checkout`` as a SumUp one, so the
Community Supporter badge, the seat confirmation, the check-in token and the
receipts behave identically.

Flow: the member POSTs (an amount, or the event registration), a PENDING row is
written, Mollie opens a payment and the browser goes to Mollie's checkout.
Mollie then calls the webhook and sends the member back to the return page.
Neither request is trusted: both re-read the payment from Mollie
(``sync_mollie_payment``).

Event seats: ``create_sumup_event_checkout`` hands card payments to
``open_mollie_event_checkout`` when ``mollie_event_payments_active()``. Refunds
of a seat are made by staff in the Mollie dashboard (or by bank transfer); the
webhook then records REFUNDED on the row, voids the credit it funded and marks
the seat unpaid (status unchanged, so staff decide about the seat).
"""

import logging
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import override
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import EventCheckoutCreationClaim, PaymentTransaction
from crush_lu.models.premium_recovery import PremiumPaymentRecoveryCase
from crush_lu.models.profiles import CrushProfile, PremiumMembership
from crush_lu.services import mollie, premium_recovery
from crush_lu.utils.i18n import get_onscreen_language
from crush_lu.views_payments import (
    DONATION_CREATE_COOLDOWN_SECONDS,
    _apply_paid_checkout,
    _close_premium_checkout,
    _event_checkout_validation_error,
    _lock_event_checkout_state,
    _lock_member_and_check_blocked,
    _lock_premium_checkout_state,
    _native_commerce_suppressed,
    _parse_donation_amount,
    _payment_owner_ids,
    _premium_checkout_retry_response,
    _premium_payment_captured,
    _premium_payment_received_response,
    _premium_purchase_refused,
    _sumup_return_response,
)

logger = logging.getLogger(__name__)

RETURN_PATH = "/payments/mollie/return/"
WEBHOOK_PATH = "/payments/mollie/webhook/"

_UNPAID_STATUS = {
    mollie.STATUS_FAILED: PaymentTransaction.Status.FAILED,
    mollie.STATUS_CANCELED: PaymentTransaction.Status.CANCELLED,
    mollie.STATUS_EXPIRED: PaymentTransaction.Status.CANCELLED,
}


def _webhook_url(request):
    """The webhook Mollie should call, or None where Mollie cannot reach it.

    Mollie refuses a payment whose webhook it cannot reach, so a local run
    (http, localhost, *.localhost) opens payments without one and relies on
    the return page re-reading the status.
    """
    host = request.get_host().split(":")[0].lower()
    if (
        not request.is_secure()
        or host == "localhost"
        or host.endswith(".localhost")
        or host.startswith("127.")
    ):
        return None
    return request.build_absolute_uri(WEBHOOK_PATH)


@login_required
@require_POST
def create_mollie_donation_checkout(request):
    """Open a Mollie payment for a donation and return its checkout URL."""
    # The same store-compliance gate as the SumUp donation checkout: the card
    # hides itself inside the native shell, and the endpoint must agree.
    if _native_commerce_suppressed(request):
        return JsonResponse(
            {"error": _("Available outside the mobile app.")}, status=403
        )

    if not mollie.mollie_configured():
        return JsonResponse(
            {
                "error": _(
                    "Online contributions are paused for now. Thank you for your support!"
                )
            },
            status=503,
        )

    # The badge lives on CrushProfile: refuse before money moves, as the SumUp
    # checkout does (see create_sumup_donation_checkout for the history).
    if not CrushProfile.objects.filter(user=request.user).exists():
        return JsonResponse(
            {"error": _("Please complete your profile before contributing.")},
            status=400,
        )

    amount, error_response = _parse_donation_amount(request)
    if error_response is not None:
        return error_response

    # None (cache down) is not a live cooldown; see the SumUp checkout.
    claimed = cache.add(
        f"mollie:donation:create:{request.user.id}",
        1,
        timeout=DONATION_CREATE_COOLDOWN_SECONDS,
    )
    if claimed is False:
        return JsonResponse(
            {"error": _("Please wait a moment before trying again.")}, status=429
        )

    reference = f"CRUSH-DON-{request.user.id}-{uuid.uuid4().hex[:6]}"
    # Written before Mollie is asked, so a payment Mollie opened always has a
    # row to land on: the webhook only acts on payment ids found here.
    tx = PaymentTransaction.objects.create(
        transaction_reference=reference,
        provider=PaymentTransaction.Provider.MOLLIE,
        amount=amount,
        currency="EUR",
        status=PaymentTransaction.Status.PENDING,
        purpose=PaymentTransaction.Purpose.DONATION,
        user=request.user,
    )

    payment, failure = None, ""
    try:
        payment = mollie.MollieClient().create_payment(
            amount=amount,
            description=f"Crush.lu Project Support (€{amount:.2f})",
            redirect_url=request.build_absolute_uri(f"{RETURN_PATH}?ref={reference}"),
            webhook_url=_webhook_url(request),
            metadata={"transaction_reference": reference},
            locale=mollie.mollie_locale(
                get_onscreen_language(user=request.user, request=request)
            ),
        )
    except mollie.MollieError as exc:
        failure = str(exc)

    payment_id = (payment or {}).get("id") or ""
    url = mollie.checkout_url(payment) if payment else None
    if not mollie.PAYMENT_ID_RE.match(payment_id) or not url:
        failure = failure or "no payment id or checkout link in the response"
        logger.error("Mollie donation checkout %s not opened: %s", reference, failure)
        # Nothing was charged: the member never reached a checkout. The row
        # stays as the record of the attempt.
        tx.status = PaymentTransaction.Status.FAILED
        tx.failure_reason = f"Mollie did not open a checkout: {failure}"
        tx.raw_response = payment or {}
        tx.save(
            update_fields=["status", "failure_reason", "raw_response", "updated_at"]
        )
        return JsonResponse(
            {
                "error": _(
                    "Unable to initiate donation checkout. Please try again later."
                )
            },
            status=502,
        )

    tx.mollie_payment_id = payment_id
    tx.raw_response = payment
    tx.save(update_fields=["mollie_payment_id", "raw_response", "updated_at"])
    return JsonResponse(
        {
            "success": True,
            "checkout_url": url,
            "checkout_reference": reference,
            "amount": float(amount),
            "currency": "EUR",
        }
    )


def _withdraw_event_credits(tx):
    """Void the Crush Credit this payment funded, now that its money went back.

    Mirrors ``reconcile_sumup_payments._reconcile_refunded``: otherwise a
    cancellation credit issued from the capture stays spendable beside the
    refund. The caller holds the PaymentTransaction lock; credits are locked
    after it, status-blind and in PK order. Credit already spent cannot be
    undone, so it is logged critical for staff and left on the ledger.
    """
    from crush_lu.models.credits import CreditRedemption, CrushCredit
    from crush_lu.services.credits import void_credit

    filters = Q(source_payment=tx)
    if tx.event_registration_id:
        filters |= Q(
            source_registration_id=tx.event_registration_id,
            source_payment__isnull=True,
        )
    list(
        CrushCredit.objects.select_for_update()
        .filter(filters)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    spent = list(
        CreditRedemption.objects.filter(
            credit__in=CrushCredit.objects.filter(filters)
        ).values_list("credit_id", "event_registration_id", "amount_cents")
    )
    for credit in CrushCredit.objects.filter(filters, status=CrushCredit.Status.ACTIVE):
        void_credit(
            credit.pk,
            note="Voided: Mollie refund or chargeback on the funding payment.",
            require_unspent=False,
        )
    if spent:
        logger.critical(
            "Mollie payment %s was returned but credit it funded was already "
            "spent (credit, registration, cents: %s) - staff review required.",
            tx.transaction_reference,
            spent,
        )


def _unconfirm_refunded_seat(tx):
    """Mark the seat unpaid once all of its money went back.

    Left True, ``payment_confirmed`` would let a later cancellation treat the
    refunded payment as a legacy cash payment and mint Crush Credit for it.
    The status stays as it is (staff decide about the seat), and the member can
    pay again. Skipped when another PAID payment still backs the seat. Lock
    order: the caller holds the payment, then the event, then the registration.
    """
    if not tx.event_registration_id or _already_paid_by(tx):
        return
    snapshot = EventRegistration.objects.only("event_id").get(
        pk=tx.event_registration_id
    )
    MeetupEvent.objects.select_for_update().get(pk=snapshot.event_id)
    registration = EventRegistration.objects.select_for_update().get(
        pk=tx.event_registration_id
    )
    if registration.payment_confirmed:
        registration.payment_confirmed = False
        registration.save(update_fields=["payment_confirmed"])


def _already_paid_by(tx):
    """Reference of another PAID payment for ``tx``'s registration, or None."""
    other = (
        PaymentTransaction.objects.filter(
            event_registration_id=tx.event_registration_id,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=PaymentTransaction.Status.PAID,
        )
        .exclude(pk=tx.pk)
        .order_by("pk")
        .first()
    )
    return other.transaction_reference if other else None


# A row written before Mollie is asked has no payment id for a moment. Past this
# age it is a request that died between the two writes.
_PREPARING_GRACE = timedelta(seconds=120)
_OPEN_STATUSES = ("open", "pending", "authorized")
# Started at Mollie: no checkout link, and Mollie may refuse to cancel them
# (it did for a pending one on 2026-11-03).
_IN_PROGRESS_STATUSES = ("pending", "authorized")
EVENT_CREATE_COOLDOWN_SECONDS = 5


def _event_json_error(message, status):
    return JsonResponse({"error": message}, status=status)


def _payment_in_progress_response(request, event_id):
    """Send the member back to the event while Mollie still processes a payment.

    Such a payment may still turn into paid (Pay by Bank stays pending until
    the transfer lands, up to 2-3 business days for a standard one), so it is
    not cancelled and nothing new is opened beside it. An abandoned one ends by
    itself: Mollie's docs give an open card payment 30 minutes and a Pay by
    Bank session an hour. The button only follows ``widget_url``, so the copy
    goes out as a message on the event page, in the language that page opens in.
    """
    with override(get_onscreen_language(user=request.user, request=request)):
        message = _(
            "Your earlier payment is still being processed. If you completed "
            "it, your seat is confirmed automatically, so there is no need to "
            "pay again. If you did not finish it, it closes by itself, usually "
            "within an hour. You can then pay here, or pay at the door."
        )
        event_url = reverse("crush_lu:event_detail", kwargs={"event_id": event_id})
    messages.warning(request, message)
    return JsonResponse(
        {
            "success": True,
            "payment_in_progress": True,
            "message": message,
            "widget_url": event_url,
        }
    )


def _being_prepared_response():
    return _event_json_error(
        _("A payment checkout is already being prepared. Please wait and try again."),
        409,
    )


def _event_prepare_state(registration_id, event_id, acting_user):
    """Phase 1: validate under the payment-first locks and list open Mollie rows.

    Returns ``(response, state)``: a response to send back, or the state the
    next phases need. No network I/O happens here.
    """
    for _attempt in range(3):
        with transaction.atomic():
            try:
                payments, event, registration, scope_changed = (
                    _lock_event_checkout_state(registration_id, event_id)
                )
            except (EventRegistration.DoesNotExist, MeetupEvent.DoesNotExist):
                return (
                    _event_json_error(
                        _("This registration is no longer available."), 409
                    ),
                    None,
                )
            if scope_changed:
                continue
            error = _event_checkout_validation_error(
                registration, event, acting_user=acting_user, payment_method="card"
            )
            if error is not None:
                return error, None
            if EventCheckoutCreationClaim.objects.filter(
                registration_id=registration.pk
            ).exists():
                return _being_prepared_response(), None
            now = timezone.now()
            pending = []
            sumup_pending = [
                (payment.pk, payment.sumup_checkout_id)
                for payment in payments
                if payment.provider == PaymentTransaction.Provider.SUMUP
                and payment.status == PaymentTransaction.Status.PENDING
                and payment.sumup_checkout_id
            ]
            for payment in payments:
                if (
                    payment.provider != PaymentTransaction.Provider.MOLLIE
                    or payment.status != PaymentTransaction.Status.PENDING
                ):
                    continue
                if not payment.mollie_payment_id:
                    if now - payment.created_at < _PREPARING_GRACE:
                        return _being_prepared_response(), None
                    payment.status = PaymentTransaction.Status.CANCELLED
                    payment.failure_reason = (
                        "Mollie did not open a checkout for this attempt."
                    )
                    payment.save(
                        update_fields=["status", "failure_reason", "updated_at"]
                    )
                    continue
                pending.append(payment.pk)
            return None, {
                "sumup_pending": sumup_pending,
                "pending_ids": pending,
                "amount": event.registration_fee,
                "title": event.title,
                "event_id": event.pk,
                "registration_id": registration.pk,
            }
    return _being_prepared_response(), None


def _close_sumup_checkouts(pending):
    """Deactivate earlier SumUp checkouts so only the Mollie one can be paid.

    ``pending`` is ``[(payment pk, checkout id)]``. While SumUp is shut
    (``ONLINE_PAYMENTS_ENABLED=False``) a checkout SumUp will not close is
    treated as closed, the accepted risk recorded in
    ``services.sumup`` (Tom, 2026-10-16); otherwise closure must be proven.
    Returns False when any checkout is not closed or has since been paid.
    """
    from crush_lu.services.event_payments import sumup_payments_paused
    from crush_lu.services.sumup import SumUpClient, SumUpError

    paused = sumup_payments_paused()
    try:
        client = SumUpClient()
    except SumUpError:
        client = None
    closed = {}
    for payment_pk, checkout_id in pending:
        try:
            ok = bool(client and client.ensure_checkout_not_payable(checkout_id))
        except SumUpError:
            ok = False
        if not ok and paused:
            logger.warning(
                "[PAYMENTS-PAUSED] SumUp checkout %s not proven closed; "
                "treated as closed while SumUp is paused.",
                checkout_id,
            )
            ok = True
        closed[payment_pk] = ok
    with transaction.atomic():
        rows = {
            row.pk: row
            for row in PaymentTransaction.objects.select_for_update()
            .filter(pk__in=closed)
            .order_by("pk")
        }
        for payment_pk, ok in closed.items():
            row = rows.get(payment_pk)
            if row is None or row.status == PaymentTransaction.Status.PAID or not ok:
                return False
            if row.status == PaymentTransaction.Status.PENDING:
                row.status = PaymentTransaction.Status.CANCELLED
                row.failure_reason = (
                    "Superseded by a Mollie checkout - the SumUp checkout was "
                    "deactivated before any card was charged."
                )
                row.save(update_fields=["status", "failure_reason", "updated_at"])
    return True


def open_mollie_event_checkout(request, registration_id):
    """Open (or reuse) a Mollie payment for an event seat.

    Called by ``create_sumup_event_checkout`` once it has parsed the request and
    found Mollie is the event provider. Three phases, so no provider call runs
    under a lock: (1) validate and list open Mollie rows, (2) re-read each from
    Mollie and reuse, close, settle or wait for it, (3) write the PENDING row
    under the same locks, then ask Mollie. Reusing the member's still-open
    payment, rather than opening a second one beside it, is what stops a double
    charge.
    """
    initial = get_object_or_404(EventRegistration, pk=registration_id)
    if not mollie.mollie_configured():
        return _event_json_error(
            _(
                "Online card payments are paused for now. For events, "
                "please pay at the door."
            ),
            503,
        )
    # None (cache down) is not a live cooldown, as in the donation checkout.
    claimed = cache.add(
        f"mollie:event:create:{initial.pk}", 1, timeout=EVENT_CREATE_COOLDOWN_SECONDS
    )
    if claimed is False:
        return _event_json_error(_("Please wait a moment before trying again."), 429)

    response, state = _event_prepare_state(initial.pk, initial.event_id, request.user)
    if response is not None:
        return response

    if state["sumup_pending"] and not _close_sumup_checkouts(state["sumup_pending"]):
        return _event_json_error(
            _(
                "Your earlier card checkout could not be closed. "
                "Please wait and try again."
            ),
            409,
        )

    client = mollie.MollieClient()
    event_url = reverse("crush_lu:event_detail", kwargs={"event_id": state["event_id"]})
    unavailable = _("Unable to initiate payment at the moment. Please try again later.")

    for payment_pk in state["pending_ids"]:
        tx = PaymentTransaction.objects.get(pk=payment_pk)
        try:
            remote = client.get_payment(tx.mollie_payment_id)
        except mollie.MollieError as exc:
            logger.warning(
                "Mollie event checkout could not re-read %s: %s",
                tx.mollie_payment_id,
                exc,
            )
            return _event_json_error(unavailable, 502)
        status = remote.get("status")
        if status == mollie.STATUS_PAID:
            try:
                sync_mollie_payment(tx)
            except mollie.MollieError:
                pass  # the webhook settles it; the seat page shows the truth
            return JsonResponse(
                {"success": True, "already_paid": True, "widget_url": event_url}
            )
        if status in _UNPAID_STATUS:
            _record_unpaid(tx, status, remote)
            continue
        if status in _OPEN_STATUSES:
            url = mollie.checkout_url(remote)
            price_matches = mollie.amount_matches(remote, state["amount"], "EUR")
            if url and price_matches:
                return JsonResponse(
                    {
                        "success": True,
                        "checkout_url": url,
                        "widget_url": url,
                        "checkout_reference": tx.transaction_reference,
                        "amount": float(state["amount"]),
                        "currency": "EUR",
                    }
                )
            if status in _IN_PROGRESS_STATUSES and price_matches:
                return _payment_in_progress_response(request, state["event_id"])
            # Stale price, or open without a link: close it so it can never be
            # paid beside the replacement. If Mollie will not cancel it, open
            # nothing new.
            try:
                client.cancel_payment(tx.mollie_payment_id)
            except mollie.MollieError as exc:
                logger.warning(
                    "Mollie event checkout could not cancel %s: %s",
                    tx.mollie_payment_id,
                    exc,
                )
                # Mollie answers 422 for a payment that already ended (the
                # member backed out of the checkout page): re-read before
                # deciding it is genuinely stuck.
                try:
                    latest = client.get_payment(tx.mollie_payment_id)
                except mollie.MollieError:
                    latest = None
                latest_status = latest.get("status") if latest else None
                if latest_status == mollie.STATUS_PAID:
                    try:
                        sync_mollie_payment(tx)
                    except mollie.MollieError:
                        pass  # the webhook settles it
                    return JsonResponse(
                        {"success": True, "already_paid": True, "widget_url": event_url}
                    )
                if latest_status in _UNPAID_STATUS:
                    _record_unpaid(tx, latest_status, latest)
                    continue
                if latest_status in _IN_PROGRESS_STATUSES and mollie.amount_matches(
                    latest, state["amount"], "EUR"
                ):
                    # The member started paying between the two reads.
                    return _payment_in_progress_response(request, state["event_id"])
                return _event_json_error(
                    _(
                        "Your earlier card checkout could not be closed. "
                        "Please wait and try again."
                    ),
                    409,
                )
            _record_unpaid(tx, mollie.STATUS_CANCELED, remote)

    # Phase 3: the PENDING row, written under the locks before Mollie is asked.
    reference = f"CRUSH-EVT-{state['registration_id']}-{uuid.uuid4().hex[:10]}"
    for _attempt in range(3):
        with transaction.atomic():
            try:
                payments, event, registration, scope_changed = (
                    _lock_event_checkout_state(
                        state["registration_id"], state["event_id"]
                    )
                )
            except (EventRegistration.DoesNotExist, MeetupEvent.DoesNotExist):
                return _event_json_error(
                    _("This registration is no longer available."), 409
                )
            if scope_changed:
                continue
            error = _event_checkout_validation_error(
                registration, event, acting_user=request.user, payment_method="card"
            )
            if error is not None:
                return error
            if event.registration_fee != state["amount"]:
                return _event_json_error(
                    _(
                        "The event price changed while payment was being "
                        "prepared. Please try again."
                    ),
                    409,
                )
            # A claim means another checkout (a Crush Credit switch, or a
            # SumUp creator) is mid-flight for this seat: opening a payable
            # Mollie checkout beside it would defeat its retirement barrier.
            if EventCheckoutCreationClaim.objects.filter(
                registration_id=registration.pk
            ).exists() or any(
                p.provider == PaymentTransaction.Provider.MOLLIE
                and p.status == PaymentTransaction.Status.PENDING
                for p in payments
            ):
                return _being_prepared_response()
            tx = PaymentTransaction.objects.create(
                transaction_reference=reference,
                provider=PaymentTransaction.Provider.MOLLIE,
                amount=state["amount"],
                currency="EUR",
                status=PaymentTransaction.Status.PENDING,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                user=request.user,
                event_registration=registration,
            )
        break
    else:
        return _being_prepared_response()

    payment, failure = None, ""
    try:
        payment = client.create_payment(
            amount=state["amount"],
            description=f"Crush.lu Event: {state['title'][:50]}",
            redirect_url=request.build_absolute_uri(f"{RETURN_PATH}?ref={reference}"),
            webhook_url=_webhook_url(request),
            metadata={"transaction_reference": reference},
            locale=mollie.mollie_locale(
                get_onscreen_language(user=request.user, request=request)
            ),
        )
    except mollie.MollieError as exc:
        failure = str(exc)

    payment_id = (payment or {}).get("id") or ""
    url = mollie.checkout_url(payment) if payment else None
    if not mollie.PAYMENT_ID_RE.match(payment_id) or not url:
        failure = failure or "no payment id or checkout link in the response"
        logger.error("Mollie event checkout %s not opened: %s", reference, failure)
        tx.status = PaymentTransaction.Status.FAILED
        tx.failure_reason = f"Mollie did not open a checkout: {failure}"
        tx.raw_response = payment or {}
        tx.save(
            update_fields=["status", "failure_reason", "raw_response", "updated_at"]
        )
        return _event_json_error(unavailable, 502)

    tx.mollie_payment_id = payment_id
    tx.raw_response = payment
    tx.save(update_fields=["mollie_payment_id", "raw_response", "updated_at"])
    return JsonResponse(
        {
            "success": True,
            "checkout_url": url,
            "widget_url": url,
            "checkout_reference": reference,
            "amount": float(state["amount"]),
            "currency": "EUR",
        }
    )


PREMIUM_CREATE_COOLDOWN_SECONDS = 5


def _premium_description(membership, onboarding_fee):
    """What the member reads on Mollie's checkout.

    It carries the classification, as the SumUp checkout's does: the reuse
    check compares it, so a payment opened as onboarding is never handed out
    again as a first month even when both cost the same.
    """
    description = f"Crush Connect Premium - Coach {membership.coach}"
    if not onboarding_fee:
        description += " - first month"
    return description[:255]


def _premium_return_response(request, tx, **extra):
    """Send the member to the return page, which says what the payment did."""
    return JsonResponse(
        {
            "success": True,
            "widget_url": request.build_absolute_uri(
                f"{RETURN_PATH}?ref={tx.transaction_reference}"
            ),
            **extra,
        }
    )


def _premium_in_progress_response(request):
    """The Premium twin of ``_payment_in_progress_response``."""
    with override(get_onscreen_language(user=request.user, request=request)):
        message = _(
            "Your earlier payment is still being processed. If you completed "
            "it, your Premium membership starts automatically, so there is no "
            "need to pay again. If you did not finish it, it closes by itself, "
            "usually within an hour."
        )
        page = reverse("crush_lu:premium_choose_coach")
    messages.warning(request, message)
    return JsonResponse(
        {
            "success": True,
            "payment_in_progress": True,
            "message": message,
            "widget_url": page,
        }
    )


def _cancel_unopened(tx):
    """Close a row whose request died before Mollie opened a payment for it."""
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx.pk)
        if (
            locked.status != PaymentTransaction.Status.PENDING
            or locked.mollie_payment_id
        ):
            return
        locked.status = PaymentTransaction.Status.CANCELLED
        locked.failure_reason = "Mollie did not open a checkout for this attempt."
        locked.save(update_fields=["status", "failure_reason", "updated_at"])


def _retire_premium_payment(client, tx, remote=None):
    """Make sure ``tx``'s Mollie payment can take no money. No lock is held.

    Returns ``"closed"`` (proven unpayable, row closed), ``"paid"`` (the caller
    applies it), ``"in_progress"`` (the member started paying and Mollie will
    not cancel it) or ``"unknown"`` (could not be proven either way).
    """
    if not tx.mollie_payment_id:
        if timezone.now() - tx.created_at < _PREPARING_GRACE:
            return "unknown"
        _cancel_unopened(tx)
        return "closed"
    try:
        if remote is None:
            remote = client.get_payment(tx.mollie_payment_id)
        status = remote.get("status")
        if status in _OPEN_STATUSES:
            try:
                client.cancel_payment(tx.mollie_payment_id)
            except mollie.MollieError:
                # Mollie answers 422 for a payment that ended meanwhile, and
                # for a started one it will not cancel: read it again.
                remote = client.get_payment(tx.mollie_payment_id)
                status = remote.get("status")
                if status in _IN_PROGRESS_STATUSES:
                    return "in_progress"
                if status in _OPEN_STATUSES:
                    return "unknown"
            else:
                _record_unpaid(tx, mollie.STATUS_CANCELED, remote)
                return "closed"
        if status == mollie.STATUS_PAID:
            return "paid"
        if status in _UNPAID_STATUS:
            _record_unpaid(tx, status, remote)
            return "closed"
    except mollie.MollieError as exc:
        logger.warning(
            "Mollie Premium payment %s could not be closed: %s",
            tx.mollie_payment_id,
            exc,
        )
    return "unknown"


def close_mollie_premium_payments(rows):
    """Close the PENDING Mollie payments among ``rows`` (Premium rows).

    For everything that must leave a membership with no payable checkout: the
    member cancelling the request, a capture already recorded, a recovery case
    (``premium_recovery.close_open_checkouts_safely``). A payment Mollie
    reports paid is applied, exactly as the webhook would. Returns False when
    one could not be proven closed; it stays PENDING and the next caller
    retries. Never raises.
    """
    rows = [
        row
        for row in rows
        if row.provider == PaymentTransaction.Provider.MOLLIE
        and row.status == PaymentTransaction.Status.PENDING
    ]
    if not rows:
        return True
    try:
        client = mollie.MollieClient()
    except mollie.MollieError:
        return False
    proven = True
    for tx in rows:
        outcome = _retire_premium_payment(client, tx)
        if outcome == "paid":
            try:
                sync_mollie_payment(tx)
            except mollie.MollieError:
                proven = False
        elif outcome != "closed":
            proven = False
    return proven


def _retire_sumup_premium_checkouts(membership, pending):
    """Close a membership's earlier SumUp checkouts before Mollie opens one.

    ``pending`` is ``[(payment pk, checkout id)]``. Returns ``"closed"``,
    ``"paid"`` or ``"open"``. Unlike ``_close_sumup_checkouts`` (event seats),
    a checkout SumUp reports captured is never treated as closed: it is
    recorded from that read through ``_apply_paid_checkout`` (applied, or its
    own recovery case), because nothing else would read it again, and no
    Mollie payment is opened beside it. Only a checkout whose state SumUp will
    not tell counts as closed while SumUp is shut, the accepted risk recorded
    in ``services.sumup`` (Tom, 2026-10-16); otherwise closure must be proven.
    """
    from crush_lu.services.event_payments import sumup_payments_paused
    from crush_lu.services.sumup import SumUpClient, SumUpError

    paused = sumup_payments_paused()
    try:
        client = SumUpClient()
    except SumUpError:
        client = None
    retired, paid, still_open = set(), {}, False
    for payment_pk, checkout_id in pending:
        payload = {}
        outcome = (
            _close_premium_checkout(client, checkout_id, payload) if client else "open"
        )
        if outcome == "paid":
            paid[payment_pk] = payload
        elif outcome == "closed":
            retired.add(payment_pk)
        elif paused:
            logger.warning(
                "[PAYMENTS-PAUSED] SumUp Premium checkout %s not proven closed; "
                "treated as closed while SumUp is paused.",
                checkout_id,
            )
            retired.add(payment_pk)
        else:
            still_open = True
    if retired:
        with transaction.atomic():
            _lock_premium_checkout_state(
                membership.pk,
                retired,
                reason=(
                    "Superseded by a Mollie checkout - the SumUp checkout was "
                    "deactivated before any card was charged."
                ),
            )
    for row in PaymentTransaction.objects.filter(
        pk__in=paid, status=PaymentTransaction.Status.PENDING
    ).order_by("pk"):
        _apply_paid_checkout(row, paid[row.pk])
    if paid:
        return "paid"
    return "open" if still_open else "closed"


def _premium_customer_id(client, membership, buyer):
    """The Mollie customer the first payment is made under.

    One customer per member: an earlier membership's is reused. A new one
    carries no name or email, only our user id, because the customer exists
    solely to hold the mandate. Raises MollieError.
    """
    known = (
        PremiumMembership.objects.filter(user=buyer)
        .exclude(mollie_customer_id="")
        .order_by("-created_at")
        .values_list("mollie_customer_id", flat=True)
        .first()
    )
    customer_id = known or str(
        client.create_customer(metadata={"user_id": buyer.pk}).get("id") or ""
    )
    if not mollie.CUSTOMER_ID_RE.match(customer_id):
        raise mollie.MollieError("no customer id in the response")
    PremiumMembership.objects.filter(pk=membership.pk).update(
        mollie_customer_id=customer_id
    )
    return customer_id


def open_mollie_premium_checkout(request, membership):
    """Open (or reuse) the Mollie first payment for a pending Premium request.

    Called by ``create_sumup_premium_checkout`` once it has checked who is
    asking and found Mollie takes Premium (``mollie_premium_active``). The
    refusals are the SumUp checkout's own, in the same order. As for event
    seats, no provider call runs under a lock and at most one payment per
    membership stays payable: an open one is reused or closed before the next.

    The payment is a ``first`` payment under a Mollie customer, so paying it
    leaves a mandate. Nothing charges that mandate yet.
    """
    if membership.status != "pending":
        return _event_json_error(_("This membership is not pending payment."), 400)

    def pending_rows():
        return list(
            PaymentTransaction.objects.filter(
                premium_membership=membership,
                status=PaymentTransaction.Status.PENDING,
            ).order_by("pk")
        )

    if _premium_payment_captured(membership):
        logger.error(
            "Refused a second premium checkout for membership %s (user=%s): a "
            "PAID transaction is already recorded against it and Premium was "
            "never granted — needs reconciliation, not another charge.",
            membership.id,
            membership.user_id,
        )
        if not close_mollie_premium_payments(pending_rows()):
            return _premium_checkout_retry_response(membership)
        return _premium_payment_received_response()

    if premium_recovery.blocks_new_charge(membership.user):
        return _premium_payment_received_response()

    if _premium_purchase_refused(membership):
        logger.warning(
            "Blocked premium checkout for membership %s: user %s is not a "
            "selected beta tester",
            membership.id,
            membership.user_id,
        )
        return _event_json_error(_("Premium is invite-only during the beta."), 403)

    # None (cache down) is not a live cooldown, as in the other checkouts.
    claimed = cache.add(
        f"mollie:premium:create:{membership.pk}",
        1,
        timeout=PREMIUM_CREATE_COOLDOWN_SECONDS,
    )
    if claimed is False:
        return _event_json_error(_("Please wait a moment before trying again."), 429)

    from django.conf import settings

    from crush_lu.views_premium import premium_monthly_fee, premium_onboarding_fee

    # Read at payment time and only once, like the SumUp checkout: the amount,
    # the recorded fee and the description come from one classification.
    onboarding_fee = premium_onboarding_fee(membership.user)
    amount = onboarding_fee if onboarding_fee > 0 else premium_monthly_fee()
    description = _premium_description(membership, onboarding_fee)
    unavailable = _("Unable to initiate payment at the moment. Please try again later.")
    client = mollie.MollieClient()

    rows = pending_rows()
    sumup_pending = [
        (row.pk, row.sumup_checkout_id)
        for row in rows
        if row.provider == PaymentTransaction.Provider.SUMUP and row.sumup_checkout_id
    ]
    if sumup_pending:
        outcome = _retire_sumup_premium_checkouts(membership, sumup_pending)
        if outcome == "paid":
            return _premium_payment_received_response()
        if outcome != "closed":
            return _premium_checkout_retry_response(membership)

    for tx in rows:
        if tx.provider != PaymentTransaction.Provider.MOLLIE:
            continue
        remote = None
        if tx.mollie_payment_id:
            try:
                remote = client.get_payment(tx.mollie_payment_id)
            except mollie.MollieError as exc:
                logger.warning(
                    "Mollie Premium checkout could not re-read %s: %s",
                    tx.mollie_payment_id,
                    exc,
                )
                return _event_json_error(unavailable, 502)
            status = remote.get("status")
            same_terms = (
                mollie.amount_matches(remote, amount, "EUR")
                and remote.get("description") == description
            )
            if status in _OPEN_STATUSES and same_terms:
                url = mollie.checkout_url(remote)
                if url:
                    return JsonResponse(
                        {
                            "success": True,
                            "checkout_url": url,
                            "widget_url": url,
                            "checkout_reference": tx.transaction_reference,
                            "amount": float(amount),
                            "currency": "EUR",
                        }
                    )
                if status in _IN_PROGRESS_STATUSES:
                    return _premium_in_progress_response(request)
        outcome = _retire_premium_payment(client, tx, remote)
        if outcome == "paid":
            try:
                sync_mollie_payment(tx)
            except mollie.MollieError:
                pass  # the webhook settles it; the return page shows the truth
            return _premium_return_response(request, tx, already_paid=True)
        if outcome == "in_progress":
            return _premium_in_progress_response(request)
        if outcome != "closed":
            if not tx.mollie_payment_id:
                return _being_prepared_response()
            return _premium_checkout_retry_response(membership)

    # The PENDING row, written under the payments -> membership lock before
    # Mollie is asked, so a payment Mollie opened always has a row to land on.
    reference = f"CRUSH-PREM-{membership.id}-{uuid.uuid4().hex[:10]}"
    error, tx, buyer, customer_id = None, None, None, ""
    with transaction.atomic():
        locked, still_pending = _lock_premium_checkout_state(membership.pk, set())
        if (
            locked is None
            or locked.status != "pending"
            # A merge that won the membership lock deactivated this account.
            or not locked.user.is_active
            or _premium_payment_captured(membership)
            # Under the member's row lock, after payment -> membership, the
            # order a capture opening a recovery case takes.
            or _lock_member_and_check_blocked(locked.user_id)
        ):
            error = _event_json_error(_("This membership is not pending payment."), 409)
        elif still_pending:
            # Another click published its own payment in between.
            error = _being_prepared_response()
        else:
            locked.onboarding_fee = onboarding_fee
            locked.save(update_fields=["onboarding_fee"])
            tx = PaymentTransaction.objects.create(
                transaction_reference=reference,
                provider=PaymentTransaction.Provider.MOLLIE,
                amount=amount,
                currency="EUR",
                status=PaymentTransaction.Status.PENDING,
                purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
                user=request.user,
                premium_membership=membership,
            )
            buyer, customer_id = locked.user, locked.mollie_customer_id
    if error is not None:
        return error

    payment, failure = None, ""
    try:
        if not customer_id:
            customer_id = _premium_customer_id(client, membership, buyer)
        payment = client.create_payment(
            amount=amount,
            description=description,
            redirect_url=request.build_absolute_uri(f"{RETURN_PATH}?ref={reference}"),
            webhook_url=_webhook_url(request),
            metadata={"transaction_reference": reference},
            locale=mollie.mollie_locale(
                get_onscreen_language(user=request.user, request=request)
            ),
            customer_id=customer_id,
            sequence_type=mollie.SEQUENCE_FIRST,
            method=getattr(settings, "MOLLIE_PREMIUM_METHOD", "") or None,
        )
    except mollie.MollieError as exc:
        failure = str(exc)

    payment_id = (payment or {}).get("id") or ""
    url = mollie.checkout_url(payment) if payment else None
    if not mollie.PAYMENT_ID_RE.match(payment_id) or not url:
        failure = failure or "no payment id or checkout link in the response"
        logger.error("Mollie Premium checkout %s not opened: %s", reference, failure)
        # Nothing was charged: the member never reached a checkout.
        tx.status = PaymentTransaction.Status.FAILED
        tx.failure_reason = f"Mollie did not open a checkout: {failure}"
        tx.raw_response = payment or {}
        tx.save(
            update_fields=["status", "failure_reason", "raw_response", "updated_at"]
        )
        return _event_json_error(unavailable, 502)

    tx.mollie_payment_id = payment_id
    tx.raw_response = payment
    tx.save(update_fields=["mollie_payment_id", "raw_response", "updated_at"])
    return JsonResponse(
        {
            "success": True,
            "checkout_url": url,
            "widget_url": url,
            "checkout_reference": reference,
            "amount": float(amount),
            "currency": "EUR",
        }
    )


def _remember_premium_mandate(tx, payment):
    """Keep the mandate the first payment left, for later renewals.

    Runs after ``_settle_paid`` committed, with no lock held, because it may
    ask Mollie. Only for the payment that actually started the membership: a
    capture that opened a recovery case (duplicate, cancelled request, coach
    full) is money to hand back, and its mandate must never be charged. Once a
    mandate is stored this does nothing, so webhook repeats cost no request.

    The paid payment normally names its mandate; when it does not, the
    customer's mandates are read. A ``pending`` one is kept too (a direct debit
    mandate starts that way): whatever charges renewals must check the mandate
    is ``valid`` first. A failed lookup leaves the field empty, which only
    means "cannot be renewed automatically".
    """
    membership = (
        PremiumMembership.objects.filter(
            pk=tx.premium_membership_id, status="active", mollie_mandate_id=""
        ).first()
        if tx.premium_membership_id
        else None
    )
    if (
        membership is None
        or not PaymentTransaction.objects.filter(
            pk=tx.pk, status=PaymentTransaction.Status.PAID
        ).exists()
        or PremiumPaymentRecoveryCase.objects.filter(payment=tx).exists()
    ):
        return
    customer_id = mollie.customer_id_of(payment) or membership.mollie_customer_id
    mandate_id = mollie.mandate_id_of(payment)
    if not mandate_id and customer_id:
        try:
            mandates = mollie.MollieClient().list_mandates(customer_id)
        except mollie.MollieError as exc:
            logger.warning(
                "Mollie mandates of membership %s could not be read: %s",
                membership.pk,
                exc,
            )
            return
        for mandate in mandates:
            candidate = str(mandate.get("id") or "")
            if mandate.get("status") in ("valid", "pending") and (
                mollie.MANDATE_ID_RE.match(candidate)
            ):
                mandate_id = candidate
                break
    if not mandate_id:
        logger.warning(
            "Premium membership %s was paid through Mollie payment %s but no "
            "mandate was found; it cannot be renewed automatically.",
            membership.pk,
            tx.mollie_payment_id,
        )
        return
    updates = {"mollie_mandate_id": mandate_id}
    if customer_id:
        updates["mollie_customer_id"] = customer_id
    PremiumMembership.objects.filter(
        pk=membership.pk, status="active", mollie_mandate_id=""
    ).update(**updates)


def _end_refunded_premium(tx):
    """All of a Premium payment went back: end what it bought.

    The rules of ``reconcile_sumup_payments`` for an external refund. A refund
    of a capture that was never applied only settles its recovery case. A
    refund of the payment that started the membership ends it through
    ``cancel_active``, which gives the coach back. Lock order: the caller holds
    the payment, then the membership, then (inside ``cancel_active``) the
    profile.
    """
    membership = (
        PremiumMembership.objects.select_for_update()
        .filter(pk=tx.premium_membership_id)
        .first()
        if tx.premium_membership_id
        else None
    )
    if membership is None or premium_recovery.refund_is_of_unapplied_capture(
        tx, lock=True
    ):
        premium_recovery.record_refund_on_case(tx)
        return
    other_paid = (
        PaymentTransaction.objects.filter(
            premium_membership=membership, status=PaymentTransaction.Status.PAID
        )
        .exclude(pk=tx.pk)
        .exists()
    )
    if other_paid:
        # Another payment still backs the membership: which one staff meant to
        # return is theirs to say.
        logger.critical(
            "Mollie Premium payment %s was returned but membership %s has "
            "another PAID payment — membership left as it is, staff review "
            "required.",
            tx.transaction_reference,
            membership.pk,
        )
    elif not membership.cancel_active(
        reason=f"Mollie refund or chargeback of {tx.transaction_reference}"
    ):
        if membership.status == "pending":
            membership.status = "cancelled"
            membership.payment_confirmed = False
            membership.payment_date = None
            membership.save(
                update_fields=["status", "payment_confirmed", "payment_date"]
            )
    premium_recovery.record_refund_on_case(tx)


def _record_unpaid(tx, mollie_status, payment):
    """Close a still-pending row whose Mollie payment can no longer be paid."""
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx.pk)
        # Never moves a paid row back: failed, canceled and expired are final
        # at Mollie, but this row may have been settled some other way.
        if locked.status != PaymentTransaction.Status.PENDING:
            return
        locked.status = _UNPAID_STATUS[mollie_status]
        locked.failure_reason = f"Mollie: payment {mollie_status}"
        locked.raw_response = payment
        locked.save(
            update_fields=["status", "failure_reason", "raw_response", "updated_at"]
        )


def _describe_money_returned(tx, refunded, charged_back):
    parts = []
    if refunded:
        parts.append(f"{tx.currency} {refunded:.2f} refunded")
    if charged_back:
        parts.append(f"{tx.currency} {charged_back:.2f} charged back")
    return f"Mollie: {', '.join(parts)}"


def _revoke_supporter_badge(tx):
    """Take the badge off ``tx``'s member, unless another donation still earns it.

    Tom's rule, 2026-10-30. Only the move to REFUNDED calls this, so a badge staff
    restored in the profile admin survives repeats of the webhook. Nothing puts
    it back automatically, and the member is not told.
    """
    # LOCK ORDER: the caller holds PaymentTransaction; CrushProfile comes second,
    # as in _apply_paid_checkout. Read the donations only once this lock is held,
    # because _apply_paid_checkout grants under it: a donation confirming right
    # now has either committed and counts below, or grants after this commits.
    profile = (
        CrushProfile.objects.select_for_update().filter(user_id=tx.user_id).first()
    )
    if profile is None or not profile.is_community_supporter:
        return
    if PaymentTransaction.objects.filter(
        user_id=tx.user_id,
        purpose=PaymentTransaction.Purpose.DONATION,
        status=PaymentTransaction.Status.PAID,
    ).exists():
        return
    profile.is_community_supporter = False
    profile.save(update_fields=["is_community_supporter"])
    logger.info(
        "Removed Community Supporter badge from user %s after Mollie donation %s",
        tx.user_id,
        tx.transaction_reference,
    )


def _settle_paid(tx, payment):
    """Apply a ``paid`` read: the payment itself, then whatever has gone back.

    The rules are the ones ``reconcile_sumup_payments`` settled on. The row
    becomes REFUNDED once all of it is back, by refund, chargeback or both, and
    the badge goes with it (``_revoke_supporter_badge``). A partial refund stays
    PAID for a human to look at, with the amounts in ``raw_response``, keeps the
    badge, and leaves ``failure_reason`` blank as the admin promises.
    """
    refunded, charged_back = mollie.money_returned(payment)
    returned = refunded + charged_back
    with transaction.atomic():
        # LOCK ORDER: PaymentTransaction before CrushProfile, as in
        # _apply_paid_checkout and merge_accounts.
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx.pk)
        # Mollie says paid for ever, and _apply_paid_checkout stops only on a
        # PAID row, so re-applying here would undo the refund and send a second
        # receipt on every repeat of the webhook.
        if locked.status == PaymentTransaction.Status.REFUNDED:
            # One-way, like the SumUp sweep. A refund can still fail after
            # processing and a chargeback can be reversed, but the row only
            # says so to a human, who can restore the badge in the profile admin.
            if returned < locked.amount:
                logger.warning(
                    "Mollie donation %s is REFUNDED but Mollie now counts %s "
                    "returned; row and badge not restored, manual follow-up "
                    "required.",
                    locked.transaction_reference,
                    returned,
                )
            return

        known = sum(mollie.money_returned(locked.raw_response))
        is_event = locked.purpose == PaymentTransaction.Purpose.EVENT_REGISTRATION
        is_premium = locked.purpose == PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP
        if (
            (is_event or is_premium)
            and locked.status == PaymentTransaction.Status.PENDING
            and returned >= locked.amount
        ):
            # All of it went back before we ever settled the payment. Applying
            # the capture first would confirm the seat, or for a cancelled
            # event/registration issue Crush Credit, on money that has already
            # been returned; for Premium it would start a membership, send its
            # receipt and end it again. Record the charge as REFUNDED and grant
            # nothing.
            locked.status = PaymentTransaction.Status.REFUNDED
            locked.raw_response = payment
            locked.failure_reason = _describe_money_returned(
                locked, refunded, charged_back
            )
            locked.save(
                update_fields=["status", "raw_response", "failure_reason", "updated_at"]
            )
            logger.warning(
                "Mollie %s payment %s was fully returned before it was "
                "settled; recorded REFUNDED, nothing granted.",
                locked.purpose,
                locked.transaction_reference,
            )
            return
        duplicate_of = (
            _already_paid_by(locked)
            if is_event and locked.status == PaymentTransaction.Status.PENDING
            else None
        )
        _apply_paid_checkout(locked, payment)
        locked.refresh_from_db()
        if duplicate_of:
            # The member paid twice (two open checkouts, or card after credit).
            # The seat is already settled, so this money has no purchase: keep
            # the row PAID as the record of the charge and hand it to a human.
            locked.failure_reason = (
                f"Duplicate payment: registration already paid by {duplicate_of}; "
                "refund this charge in Mollie."
            )
            locked.save(update_fields=["failure_reason", "updated_at"])
            logger.critical(
                "Mollie payment %s duplicates %s for registration %s - "
                "refund required.",
                locked.transaction_reference,
                duplicate_of,
                locked.event_registration_id,
            )
        if returned == known:
            return

        description = _describe_money_returned(locked, refunded, charged_back)
        locked.raw_response = payment
        update_fields = ["raw_response", "updated_at"]
        if returned >= locked.amount:
            locked.status = PaymentTransaction.Status.REFUNDED
            locked.failure_reason = description
            update_fields += ["status", "failure_reason"]
        locked.save(update_fields=update_fields)
        if (
            locked.status == PaymentTransaction.Status.REFUNDED
            and locked.purpose == PaymentTransaction.Purpose.DONATION
        ):
            _revoke_supporter_badge(locked)
        if (
            locked.status == PaymentTransaction.Status.REFUNDED
            and locked.purpose == PaymentTransaction.Purpose.EVENT_REGISTRATION
        ):
            _withdraw_event_credits(locked)
            _unconfirm_refunded_seat(locked)
        if (
            locked.status == PaymentTransaction.Status.REFUNDED
            and locked.purpose == PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP
        ):
            _end_refunded_premium(locked)
        transaction.on_commit(
            lambda reference=locked.transaction_reference, state=locked.status, kind=(
                locked.purpose
            ): (
                logger.warning(
                    "Mollie %s payment %s: %s; row is %s.%s",
                    kind,
                    reference,
                    description,
                    state,
                    (
                        " Seat marked unpaid; staff review it."
                        if kind == PaymentTransaction.Purpose.EVENT_REGISTRATION
                        else ""
                    ),
                )
            )
        )


def sync_mollie_payment(tx):
    """Re-read ``tx``'s payment from Mollie and apply it. Returns its status.

    Raises MollieError when Mollie cannot be asked. A paid payment whose amount
    differs from the row is not applied: the row recorded what the member
    chose, and Mollie must have charged exactly that.
    """
    payment = mollie.MollieClient().get_payment(tx.mollie_payment_id)
    status = payment.get("status")
    if status == mollie.STATUS_PAID:
        if not mollie.amount_matches(payment, tx.amount, tx.currency):
            logger.error(
                "Mollie payment %s is paid for %s but transaction %s expects "
                "%s %s; not applied, manual follow-up required.",
                tx.mollie_payment_id,
                payment.get("amount"),
                tx.transaction_reference,
                tx.amount,
                tx.currency,
            )
            return status
        _settle_paid(tx, payment)
        if tx.purpose == PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP:
            _remember_premium_mandate(tx, payment)
    elif status in _UNPAID_STATUS:
        _record_unpaid(tx, status, payment)
    return status


@csrf_exempt
@require_POST
def mollie_webhook(request):
    """Mollie's server-to-server call: ``id=tr_...`` whenever a payment changes.

    The body is not signed, so it is only a hint to re-read that payment.
    Unknown ids get the same 200 as known ones, so the endpoint does not reveal
    which payments exist. A failed re-read answers 503, and Mollie retries.
    """
    payment_id = (request.POST.get("id") or "").strip()
    tx = None
    if mollie.PAYMENT_ID_RE.match(payment_id):
        tx = PaymentTransaction.objects.filter(
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id=payment_id,
        ).first()
    if tx is None:
        logger.info("Mollie webhook for an unknown payment id %r", payment_id[:40])
        return HttpResponse(status=200)

    try:
        sync_mollie_payment(tx)
    except mollie.MollieError as exc:
        logger.warning("Mollie webhook could not re-read %s: %s", payment_id, exc)
        return HttpResponse(status=503)
    return HttpResponse(status=200)


@login_required
@require_GET
def mollie_payment_return(request):
    """Where Mollie sends the member back, paid or not."""
    reference = request.GET.get("ref") or ""
    tx = (
        PaymentTransaction.objects.filter(
            transaction_reference=reference,
            provider=PaymentTransaction.Provider.MOLLIE,
        ).first()
        if reference
        else None
    )
    # An unowned reference is answered exactly like an unknown one, so the
    # page cannot be used to probe references (same rule as the SumUp return).
    if tx is None or request.user.id not in _payment_owner_ids(tx):
        messages.error(request, _("Payment transaction reference not found."))
        return redirect("crush_lu:home")

    # The webhook usually lands first; when it has not (or cannot: a local run
    # has none), this read is what settles the donation.
    if tx.status == PaymentTransaction.Status.PENDING and tx.mollie_payment_id:
        try:
            sync_mollie_payment(tx)
        except mollie.MollieError as exc:
            logger.warning(
                "Mollie return could not re-read %s: %s", tx.mollie_payment_id, exc
            )
        tx.refresh_from_db()

    # Outside i18n_patterns, so pick the member's language the way the SumUp
    # return does; the redirect inside the override keeps the same prefix.
    with override(get_onscreen_language(user=request.user, request=request)):
        if tx.purpose == PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP:
            if tx.status == PaymentTransaction.Status.PAID:
                # The SumUp return's Premium wording: active, recovery case,
                # or paid but not granted.
                return _sumup_return_response(request, tx)
            messages.warning(request, _("Payment is pending or was not completed."))
            return redirect("crush_lu:premium_choose_coach")
        if (
            tx.purpose == PaymentTransaction.Purpose.EVENT_REGISTRATION
            and tx.event_registration_id
        ):
            # Back to the event, whose status card shows the seat's real state.
            if tx.status == PaymentTransaction.Status.PAID:
                messages.success(request, _("Payment completed successfully!"))
            else:
                messages.warning(request, _("Payment is pending or was not completed."))
            return redirect(
                "crush_lu:event_detail", event_id=tx.event_registration.event_id
            )
        if tx.status == PaymentTransaction.Status.PAID:
            messages.success(request, _("Payment completed successfully! Thank you."))
        else:
            messages.warning(request, _("Payment is pending or was not completed."))
        # my_events, not dashboard: it shows the card and the new badge, and
        # needs only a login (see _sumup_return_response).
        return redirect("crush_lu:my_events")
