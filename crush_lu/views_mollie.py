"""Donations and event seats through Mollie's hosted checkout.

SumUp refused the platform in 2026-10; donations moved to Mollie first, then
event seats (``MOLLIE_EVENT_PAYMENTS_ENABLED``). Premium stays on views_payments.
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
webhook then records REFUNDED on the row but never touches the registration.
"""

import logging
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
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
from crush_lu.models.profiles import CrushProfile
from crush_lu.services import mollie
from crush_lu.utils.i18n import get_onscreen_language
from crush_lu.views_payments import (
    DONATION_CREATE_COOLDOWN_SECONDS,
    _apply_paid_checkout,
    _event_checkout_validation_error,
    _lock_event_checkout_state,
    _native_commerce_suppressed,
    _parse_donation_amount,
    _payment_owner_ids,
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
EVENT_CREATE_COOLDOWN_SECONDS = 5


def _event_json_error(message, status):
    return JsonResponse({"error": message}, status=status)


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
                "pending_ids": pending,
                "amount": event.registration_fee,
                "title": event.title,
                "event_id": event.pk,
                "registration_id": registration.pk,
            }
    return _being_prepared_response(), None


def open_mollie_event_checkout(request, registration_id):
    """Open (or reuse) a Mollie payment for an event seat.

    Called by ``create_sumup_event_checkout`` once it has parsed the request and
    found Mollie is the event provider. Three phases, so no provider call runs
    under a lock: (1) validate and list open Mollie rows, (2) re-read each from
    Mollie and reuse, close or settle it, (3) write the PENDING row under the
    same locks, then ask Mollie. Reusing the member's still-open payment, rather
    than opening a second one beside it, is what stops a double charge.
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
            if url and mollie.amount_matches(remote, state["amount"], "EUR"):
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
            # Stale price or no link: close it so it can never be paid beside
            # the replacement. If Mollie will not cancel it, open nothing new.
            try:
                client.cancel_payment(tx.mollie_payment_id)
            except mollie.MollieError as exc:
                logger.warning(
                    "Mollie event checkout could not cancel %s: %s",
                    tx.mollie_payment_id,
                    exc,
                )
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
            if any(
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
                        " Registration not changed; staff review the seat."
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
