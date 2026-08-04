import json
import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import override
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from crush_lu.connect_phase import is_selected_beta_tester
from crush_lu.models.events import (
    SEAT_HOLDING_STATUSES,
    EventRegistration,
    MeetupEvent,
)
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile, PremiumMembership
from crush_lu.services.sumup import SumUpClient, SumUpError
from crush_lu.utils.i18n import get_onscreen_language
from crush_lu.views_ticket import _generate_checkin_token

logger = logging.getLogger(__name__)

# How long one checkout's status may be served from our own row before a
# browser-driven path is allowed to hit SumUp about it again.
SUMUP_SYNC_THROTTLE_SECONDS = 3

# Bounds on a voluntary support donation. The floor keeps the card fee from
# eating the whole contribution; the ceiling is a typo guard, not a policy on
# generosity — the error points anyone genuinely wanting to give more at us.
DONATION_MIN_EUR = Decimal("2.00")
DONATION_MAX_EUR = Decimal("500.00")

# How long one member must wait between opening donation checkouts. Long enough
# that a stuck button or a script cannot mint provider resources in a loop,
# short enough that someone who genuinely mistyped an amount just retries.
DONATION_CREATE_COOLDOWN_SECONDS = 10


def _native_commerce_suppressed(request):
    """Is this a native-app session that may not take payment?

    Mirrors ``crush_user_context``'s ``suppress_native_commerce`` exactly -- the
    template gate and the endpoint must not be able to disagree about whether a
    surface is purchasable.
    """
    from crush_lu.ios_app_utils import (
        is_android_native_request,
        is_ios_native_request,
    )

    if is_ios_native_request(request):
        return not getattr(settings, "IOS_NATIVE_COMMERCE_ENABLED", False)
    if is_android_native_request(request):
        return not getattr(settings, "ANDROID_NATIVE_COMMERCE_ENABLED", False)
    return False


def _may_ask_sumup(scope, checkout_id):
    """Claim the right to make one provider call for this checkout, or don't.

    ``cache.add`` is the atomic form and the reason this is a function rather
    than an inline get-then-set: two requests landing in the same tick — two
    tabs, a fast retry — can both pass a ``cache.get`` check before either
    ``set`` lands, and then both call SumUp. ``add`` is a single operation, so
    exactly one of them wins.

    Scoped per caller on purpose. The widget's poll and its failure report ask
    SumUp the same question, but they are not the same kind of caller: the poll
    runs on a 3s loop, and sharing one key with it would let that loop routinely
    swallow the single sync a genuine failure report needs. Separate scopes cap
    each independently, which is what the bound is actually for.

    Fails OPEN when the cache itself is unavailable. Production configures
    django-redis with IGNORE_EXCEPTIONS, so a Redis outage makes ``cache.add``
    return None rather than raise — and None is falsy, which would have turned
    "we lost the rate limiter" into "refuse every provider read". A member who
    had just completed 3DS would then sit on the polling screen for the whole
    five-minute window while SumUp had the payment marked paid all along. The
    throttle protects a quota; the read it gates is how someone gets their seat.
    Losing the first must not cost the second.

    Returns True at most once per SUMUP_SYNC_THROTTLE_SECONDS per scope, and
    True whenever the cache cannot answer.
    """
    claimed = cache.add(
        f"sumup-{scope}:{checkout_id}", True, timeout=SUMUP_SYNC_THROTTLE_SECONDS
    )
    # None means the backend swallowed an error; False means the key was
    # genuinely already there. Only the second is a real "no".
    return True if claimed is None else claimed


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
                # CANCELLED, not FAILED. Both are terminal and behave
                # identically everywhere (_sync_checkout_with_sumup returns
                # early for any non-PENDING row, and only PAID unlocks
                # anything), but they answer different questions. SumUp's
                # dashboard lists a deactivated checkout as a failed sale, so
                # an organiser looking at a row of "Échec" needs some way to
                # tell the ones we killed ourselves from the ones a bank
                # declined. Marking our own supersessions FAILED made those two
                # indistinguishable in the only record we control.
                old.status = PaymentTransaction.Status.CANCELLED
                # Deliberately not translated: this field is read in the Coach
                # Panel, which is forced to English, and it would otherwise be
                # frozen in whatever language the member happened to browse in.
                old.failure_reason = (
                    "Superseded by a newer checkout — the member re-opened the "
                    "payment page, so this one was deactivated at SumUp before "
                    "any card was charged."
                )
                # updated_at is auto_now, so it only moves if it is named here —
                # and the admin shows it as when the row was last touched.
                old.save(update_fields=["status", "failure_reason", "updated_at"])
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

    # A captured payment that never became an entitlement leaves this membership
    # PENDING with a PAID transaction against it -- the coach filled up, the
    # request stopped being pending, or the buyer lost their beta seat. "Pending"
    # was the only thing this endpoint required, so the member was then one
    # button away from paying for the same membership twice, and re-selecting a
    # de-selected tester is exactly the moment that button comes back.
    # Re-opening the ORIGINAL checkout cannot repair it either: _apply_paid_
    # checkout returns immediately for a row that is already PAID.
    #
    # Refuse until a human reconciles, rather than trying to self-heal. The
    # remedies differ per cause (refund, coach reassignment, re-selection) and
    # each needs a decision this endpoint cannot make.
    if PaymentTransaction.objects.filter(
        premium_membership=membership,
        status=PaymentTransaction.Status.PAID,
    ).exists():
        logger.error(
            "Refused a second premium checkout for membership %s (user=%s): a "
            "PAID transaction is already recorded against it and Premium was "
            "never granted — needs reconciliation, not another charge.",
            membership.id,
            membership.user_id,
        )
        return JsonResponse(
            {
                "error": _(
                    "We have already received a payment for this membership. "
                    "Please contact support@crush.lu so we can finish setting "
                    "it up."
                )
            },
            status=409,
        )

    # Ask the beta allowlist again, here, at the moment money is about to move.
    # views_premium checks it when the pending membership is MINTED, and nothing
    # between there and confirm() ever re-asked -- so the pending row was a
    # capability that outlived the permission that created it. De-selecting a
    # tester in the admin bounced them off the coach directory while leaving
    # this endpoint, which they already hold a link to, fully payable: they
    # could still be charged and still have a coach permanently assigned.
    # Rotating testers in and out is the normal way to run the beta, so that
    # gap is a matter of when, not if.
    if _premium_purchase_refused(membership):
        logger.warning(
            "Blocked premium checkout for membership %s: user %s is not a "
            "selected beta tester",
            membership.id,
            membership.user_id,
        )
        return JsonResponse(
            {"error": _("Premium is invite-only during the beta.")}, status=403
        )

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


@login_required
@require_POST
def create_sumup_donation_checkout(request):
    """
    Creates a SumUp checkout session for a voluntary project donation.
    """
    # Store-compliance gate, enforced here and not only in the template. Apple
    # and Google both treat taking payment for a digital good outside their
    # billing as grounds for rejection, and _products.html already hides its
    # Premium CTA on a native session with commerce disabled. A hidden button
    # is not a closed endpoint -- the webview can still reach this URL -- so the
    # same condition has to hold on the server.
    if _native_commerce_suppressed(request):
        return JsonResponse(
            {"error": _("Available outside the mobile app.")}, status=403
        )

    try:
        if request.body:
            data = json.loads(request.body.decode("utf-8"))
            # A JSON body is not necessarily an object. ``[1,2]`` and ``"x"``
            # both parse fine and then have no .get(), which would be an
            # AttributeError the except clause below deliberately does not
            # catch -- a malformed body has to be a 400, not a 500.
            if not isinstance(data, dict):
                return JsonResponse(
                    {"error": _("Invalid donation amount.")}, status=400
                )
            raw_amount = data.get("amount")
        else:
            raw_amount = request.POST.get("amount")
        if raw_amount is None:
            return JsonResponse({"error": _("Donation amount is required.")}, status=400)
        amount = Decimal(str(raw_amount))
    except (TypeError, ValueError, InvalidOperation):
        # Named exceptions, not a bare ``Exception``. The amount is the only
        # thing being parsed here; anything else that raises is a bug in this
        # view and must surface as a 500 rather than be reported to the member
        # as "your amount was invalid".
        return JsonResponse({"error": _("Invalid donation amount.")}, status=400)

    # "Infinity" and "NaN" are perfectly valid Decimal literals. Infinity slips
    # past the range check below (no comparison is ever true for it in the
    # rejecting direction) and reaches SumUp as ``inf``; NaN raises
    # InvalidOperation from the comparison itself, outside the try above, and
    # would 500. Neither is a donation, so both are refused up front.
    if not amount.is_finite():
        return JsonResponse({"error": _("Invalid donation amount.")}, status=400)

    # Money, to the cent, before anything reads the value. Left un-quantized, a
    # custom amount of 2.355 produced three different numbers for one donation:
    # the description said EUR 2.35, ``float(amount)`` handed SumUp 2.355, and
    # PaymentTransaction.amount (decimal_places=2) stored a rounded third. The
    # member must be charged, shown and recorded the same figure. Quantizing
    # before the range checks also means 1.999 is treated as the EUR 2.00 it
    # will actually be charged, rather than rejected against a value nothing
    # downstream would have used.
    #
    # Guarded, because quantize() is not total: a finite Decimal whose result
    # would exceed the context precision raises InvalidOperation. "1e30" clears
    # both the parse and the is_finite() check above and would 500 here -- the
    # ceiling below never gets to reject it, because this line runs first.
    try:
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return JsonResponse({"error": _("Invalid donation amount.")}, status=400)

    if amount < DONATION_MIN_EUR:
        return JsonResponse({"error": _("Minimum donation amount is €2.00.")}, status=400)

    # The amount is chosen by the member -- the card offers presets but also a
    # free-text field, so a slipped decimal point or a hand-rolled POST can ask
    # for an arbitrary sum. Cap it so a typo cannot open a five-figure checkout
    # (and cannot leave a bogus PENDING row behind when SumUp then refuses it).
    if amount > DONATION_MAX_EUR:
        return JsonResponse(
            {
                "error": _("Maximum donation amount is €%(max)s. Please contact us for larger contributions.")
                % {"max": f"{DONATION_MAX_EUR:.0f}"}
            },
            status=400,
        )

    # Throttle here rather than at the top of the view: this is the first line
    # past which a request costs anything. Every call beyond it is a live SumUp
    # request plus a PENDING row, and nothing but a disabled button stood
    # between a held-down key (or a script) and an unbounded number of both.
    # Charging the cooldown before validation instead would lock out a member
    # for ten seconds over a typo they never got to correct. cache.add is the
    # atomic form, the same primitive _may_ask_sumup uses.
    claimed = cache.add(
        f"sumup:donation:create:{request.user.id}",
        1,
        timeout=DONATION_CREATE_COOLDOWN_SECONDS,
    )
    # None is not False. django-redis runs with IGNORE_EXCEPTIONS, so a Redis
    # outage makes cache.add return None -- and treating that falsy value as a
    # live cooldown would turn a cache blip into "nobody may donate at all",
    # which is a far worse failure than the burst the throttle exists to stop.
    # _may_ask_sumup draws exactly this distinction; this follows it.
    if claimed is False:
        return JsonResponse(
            {"error": _("Please wait a moment before trying again.")}, status=429
        )

    checkout_ref = f"CRUSH-DON-{request.user.id}-{uuid.uuid4().hex[:6]}"
    description = f"Crush.lu Project Support (€{amount:.2f})"
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
        # No DEBUG mock-checkout fallback here. Neither sibling
        # (create_sumup_event_checkout, create_sumup_premium_checkout) has one,
        # and fabricating a checkout id writes a PaymentTransaction pointing at
        # a checkout SumUp has never heard of -- which then fails every later
        # status sync in a way that looks like a provider problem.
        logger.error("Failed to create SumUp donation checkout: %s", exc)
        return JsonResponse(
            {"error": _("Unable to initiate donation checkout. Please try again later.")},
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
        purpose=PaymentTransaction.Purpose.DONATION,
        user=request.user,
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


def _premium_purchase_refused(membership, *, lock=False):
    """True when the beta allowlist refuses this membership's BUYER.

    A premium purchase gets judged at three separate moments -- opening the
    checkout, opening the widget, and granting Premium at completion -- and each
    is a place revocation has to reach. One predicate for all three so they
    cannot drift into disagreeing about who may buy, the same reason
    ``_payment_owner_ids`` is shared.

    Always asks about ``membership.user``. Both checkout creators deliberately
    let staff act for a member, so reading the allowlist off the requester would
    ask about the staff account -- which has no waitlist row -- and would let the
    staff bypass launder the allowlist. The entitlement belongs to the buyer.

    ``lock=True`` reads the waitlist row ``FOR UPDATE`` and belongs to the
    completion path ONLY. That path is inside a transaction that is about to
    grant a permanent entitlement, and an unlocked read leaves a window: an
    admin de-selecting between the read and ``confirm()`` would be overtaken and
    Premium granted despite the revocation. Holding the lock orders the two
    deterministically -- the de-selection either lands before the read (refused)
    or blocks until after the grant.

    The other two call sites deliberately stay unlocked. They run outside any
    transaction, and the checkout creator goes on to make SumUp network calls --
    holding a row lock across provider I/O is exactly the shape to avoid. Being
    advisory there is fine, because the completion check is the one that
    actually gates the entitlement.
    """
    if membership is None:
        return False
    if not getattr(settings, "PREMIUM_REDIRECTS_TO_BETA", False):
        return False
    if not lock:
        return not is_selected_beta_tester(membership.user)

    from crush_lu.models.crush_connect import CrushConnectWaitlist

    entry = (
        CrushConnectWaitlist.objects.select_for_update()
        .filter(user_id=membership.user_id)
        .first()
    )
    # Mirrors is_selected_beta_tester's semantics exactly: no row, or a row that
    # is not selected, both mean "not a tester".
    return not (entry and entry.selected_as_tester)


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
        # A declined attempt leaves the checkout payable and records a reason,
        # so the SAME row can go on to be paid by a second card. Leaving the
        # reason behind would file every such payment under a failure that no
        # longer applies — and the Coach Panel promises this field is blank on
        # a payment that went through. The refused attempt is not lost: it is
        # still in the transactions array on raw_response, which this replaces
        # with the payload that reports the success.
        locked.failure_reason = ""
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

            # Re-validate the allowlist at completion, exactly as the seat
            # branch above re-validates the registration. Blocking checkout
            # creation is not enough on its own: a tester who opened a checkout
            # and was de-selected while it was in flight still holds a payable
            # widget, and granting here would hand them the permanent coach
            # assignment that de-selection was meant to prevent.
            #
            # Same contract as every other refusal in this function -- SumUp has
            # already captured the money, so the transaction stays PAID because
            # dropping it would lose the only record of a real charge. What we
            # refuse to do is grant Premium. Logged at error level because it
            # needs a human: refund, or re-select the member if the de-selection
            # was the mistake.
            if _premium_purchase_refused(pm, lock=True):
                logger.error(
                    "SumUp payment %s completed for PremiumMembership %s "
                    "(user=%s, coach=%s) but the buyer is no longer a selected "
                    "beta tester — payment recorded, Premium NOT granted, "
                    "refund or re-selection required.",
                    locked.transaction_reference,
                    pm.id,
                    pm.user_id,
                    pm.coach_id,
                )
                return

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

        elif locked.purpose == PaymentTransaction.Purpose.DONATION:
            # A donation buys nothing revocable — there is no seat to re-validate
            # and no membership to grant, so unlike the two branches above this
            # one cannot fail in a way that needs a refund. It only marks the
            # badge. Guarded on the current value so a repeat donation does not
            # rewrite a row that already says the same thing; the enclosing
            # PAID check already makes the whole block run once per checkout.
            #
            # LOCK ORDER: PaymentTransaction (locked at the top of this
            # function) before CrushProfile. account_merge.merge_accounts takes
            # the same two in the same order, and it must stay that way — the
            # two of them taking them in opposite orders is an ABBA cycle that
            # PostgreSQL breaks by aborting one transaction, which here would be
            # a payment callback dying mid-confirmation. select_for_update
            # rather than a plain read because the save() below locks the row
            # anyway; taking the lock at the read closes the window in which a
            # merge could see a stale False and delete the profile.
            profile = (
                CrushProfile.objects.select_for_update()
                .filter(user_id=locked.user_id)
                .first()
                if locked.user_id
                else None
            )
            if profile and not profile.is_community_supporter:
                profile.is_community_supporter = True
                profile.save(update_fields=["is_community_supporter"])
                logger.info(
                    "Granted Community Supporter badge to user %s via SumUp donation %s",
                    locked.user_id,
                    locked.transaction_reference,
                )


def describe_sumup_failure(data):
    """Say, in words, why SumUp did not pay a checkout.

    SumUp's own dashboard shows a failed online payment as nothing but "Échec"
    and a struck-through amount, which is where every "why did our test payment
    fail?" investigation starts and stops. The detail it does not show is in the
    checkout resource: each card attempt is an entry in ``transactions``, with
    its own status and — when the acquirer supplies one — a decline code.

    Returns a one-line summary suitable for the Coach Panel. Defensive about
    shape throughout: this runs on a live provider payload during a payment, and
    a KeyError here would be a 500 on somebody's checkout.
    """
    if not isinstance(data, dict):
        return ""

    status = (data.get("status") or "").upper() or "UNKNOWN"
    parts = [f"SumUp reports the checkout as {status}"]

    transactions = data.get("transactions")
    if isinstance(transactions, list) and transactions:
        for tx in transactions:
            if not isinstance(tx, dict):
                continue
            attempt = (tx.get("status") or "UNKNOWN").upper()
            # SumUp is not consistent about which of these it populates, and
            # the useful one differs by decline type, so take whichever came.
            detail = next(
                (
                    str(tx[key])
                    for key in (
                        "failure_reason",
                        "error_message",
                        "message",
                        "auth_code",
                    )
                    if tx.get(key)
                ),
                "",
            )
            summary = f"attempt {attempt}"
            if detail:
                summary += f" ({detail})"
            parts.append(summary)
    else:
        # No transactions array at all means no card was ever submitted: the
        # checkout expired, or we deactivated it. Worth stating outright,
        # because it rules out the card and points back at us.
        parts.append("no card attempt was recorded against it")

    return "; ".join(parts)


def _count_failed_attempts(data):
    """How many cards SumUp says were submitted against this checkout and refused.

    One definition of "refused", shared by the question "did anything fail?"
    and the question "has anything failed since last time?" — they must not be
    able to disagree about what counts.
    """
    if not isinstance(data, dict):
        return 0
    transactions = data.get("transactions")
    if not isinstance(transactions, list):
        return 0
    return sum(
        1
        for tx in transactions
        if isinstance(tx, dict)
        and (tx.get("status") or "").upper() not in ("SUCCESSFUL", "PAID", "PENDING")
    )


def _has_unsuccessful_attempt(data):
    """Did a card get submitted against this checkout and not go through?

    Kept separate from the status check because SumUp answers "was it paid?"
    and "was a card refused?" independently: a declined attempt leaves the
    checkout PENDING so the customer can retry, which is why a decline never
    showed up anywhere on our side.
    """
    return _count_failed_attempts(data) > 0


def _record_pending_failure(tx_obj, reason, data):
    """Write a decline against a checkout that is still open, under a lock.

    Blind writes lose a race here that they must not lose. The widget's poll
    and its failure report are deliberately allowed to ask SumUp at the same
    time (they hold separate throttle slots so the poll cannot starve the
    report), so a report holding a PENDING payload can be overtaken mid-call by
    a poll that comes back PAID and applies it. Persisting without re-reading
    then puts a failure reason and a superseded payload back onto a row that
    has just been paid — precisely the state ``_apply_paid_checkout`` clears,
    reintroduced through the back door.

    Returns True when the reason itself changed, i.e. when there is something
    new to say about this payment rather than merely a fresher payload.
    """
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx_obj.pk)
        # Re-read, not the caller's copy: the whole point is that it may be
        # stale by now.
        if locked.status != PaymentTransaction.Status.PENDING:
            logger.info(
                "Discarded a late SumUp decline for checkout %s — the row is "
                "%s now, so the attempt it describes has been superseded.",
                tx_obj.sumup_checkout_id,
                locked.status,
            )
            return False
        # Rewrite SumUp's half, keep the widget's. Replacing the whole field
        # dropped the record of what the customer had been shown, and moved the
        # attempt marker off the back of a refresh that learned nothing new.
        previous_provider, notes = _split_failure_reason(locked.failure_reason)
        combined = _join_failure_reason(reason, notes)
        if combined == locked.failure_reason and data == locked.raw_response:
            return False

        changed = reason != previous_provider
        locked.failure_reason = combined
        locked.raw_response = data
        locked.save(update_fields=["failure_reason", "raw_response", "updated_at"])
        return changed


def _record_terminal_failure(tx_obj, sumup_status, data):
    """Close a checkout SumUp reports as dead, under a lock.

    Same guard as the other two write paths, and it matters most here because
    this one moves ``status``. A checkout we deactivated ourselves comes back
    from SumUp as FAILED — that is what deactivation looks like from outside —
    so a poll still in flight when the member opens a replacement would
    otherwise reset the row from CANCELLED to FAILED and replace our own
    account of the supersession with a generic one.

    Returns True when the row was closed here.
    """
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx_obj.pk)
        if locked.status != PaymentTransaction.Status.PENDING:
            logger.info(
                "Late SumUp %s for checkout %s ignored — already %s locally.",
                sumup_status,
                tx_obj.sumup_checkout_id,
                locked.status,
            )
            return False

        locked.status = PaymentTransaction.Status.FAILED
        locked.raw_response = data
        # SumUp's half only; the widget's record of what the customer was shown
        # survives into the closed row, where a coach reading it back wants both.
        _provider, notes = _split_failure_reason(locked.failure_reason)
        locked.failure_reason = _join_failure_reason(
            describe_sumup_failure(data), notes
        )
        locked.save(
            update_fields=["status", "raw_response", "failure_reason", "updated_at"]
        )
        # The caller logs from tx_obj, so give it the values that were stored.
        tx_obj.status = locked.status
        tx_obj.failure_reason = locked.failure_reason
        return True


# ``failure_reason`` carries two things with different lifetimes: SumUp's
# account of the attempts, which a refresh legitimately rewrites in place, and
# the widget's own wording for a failure the customer has already been shown,
# which is a one-off record. One string is right for reading them — the Coach
# Panel wants both — but they must not overwrite each other, and only the first
# may move the attempt marker. Re-deriving the provider half was silently
# discarding the widget half AND moving the marker, which told the page a fresh
# card had been refused when nothing had happened but a refresh.
WIDGET_NOTE_SEPARATOR = "\n— widget: "
# What separates one widget note from the next. Nothing is counted from it —
# the attempt marker is the provider's count alone — so this is only for
# reading the notes back in the Coach Panel.
NOTE_JOIN = " | "


def _split_failure_reason(text):
    """Return (SumUp's account, the widget's own notes)."""
    provider, _sep, notes = (text or "").partition(WIDGET_NOTE_SEPARATOR)
    return provider, notes


def _join_failure_reason(provider, notes):
    if not notes:
        return provider
    return f"{provider}{WIDGET_NOTE_SEPARATOR}{notes}"


def attempt_marker(tx_obj):
    """How many refused attempts SUMUP has recorded against this checkout.

    A count, and only of SumUp's own record. Three earlier versions of this
    tried to fold in what the widget had reported, so that a decline the SDK
    saw before SumUp recorded it would still be visible — and every one of them
    broke, because the server cannot tell whether a widget report and a
    provider record that arrives later are the same refused card or two of
    them. Counting reports made a duplicated callback look like a second card;
    hashing the reason made a rewording look like one.

    So it does not try. This is a plain, monotonic count of what the provider
    says, which is unambiguous. Reconciling it with a failure the widget
    already displayed is the page's job — only the page knows which failures it
    has shown, and it knows that without asking anyone.
    """
    provider_failures = _count_failed_attempts(tx_obj.raw_response)
    return str(provider_failures) if provider_failures else ""


def _append_widget_note(tx_obj, note):
    """Add the widget's own wording to a payment that has not completed.

    Locked and re-read for the same reason ``_record_pending_failure`` is, and
    it is a separate write so it needs its own guard: an in-flight poll can
    apply a successful payment between this request's SumUp read and this save,
    and an unlocked write would then stamp a failure note onto a row
    ``_apply_paid_checkout`` had just cleared. Locking only the first of the two
    writes closed half the hole.

    Returns True when the note was recorded.
    """
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx_obj.pk)
        if locked.status != PaymentTransaction.Status.PENDING:
            logger.info(
                "Discarded a widget failure note for checkout %s — the payment "
                "is %s now.",
                tx_obj.sumup_checkout_id,
                locked.status,
            )
            return False

        # Into the widget's half, leaving SumUp's alone. One checkout can absorb
        # several refused cards, so notes accumulate; the tail is kept because
        # the most recent attempt is the one being investigated. Truncating the
        # notes rather than the whole field is what stops a long history eating
        # the separator and taking SumUp's account with it.
        provider, notes = _split_failure_reason(locked.failure_reason)
        notes = f"{notes}{NOTE_JOIN}{note}" if notes else note
        locked.failure_reason = _join_failure_reason(provider, notes[-1000:])
        locked.save(update_fields=["failure_reason", "updated_at"])
        return True


def _sync_checkout_with_sumup(tx_obj):
    """Re-read the checkout from SumUp and apply whatever it reports.

    Every path that can mark a payment complete funnels through here, so no
    caller has to be trusted — both the webhook and the return URL are public,
    unauthenticated endpoints.

    Returns the status SumUp reported, or "" when it could not be asked at all
    — because it was unreachable, or because the row was already settled and
    there was nothing to ask. Callers that report to a human need to tell "we
    checked and nothing changed" apart from "we never got an answer"; without
    a return value, an outage looked exactly like a clean verification.
    """
    if tx_obj.status != PaymentTransaction.Status.PENDING:
        return ""

    try:
        data = SumUpClient().get_checkout(tx_obj.sumup_checkout_id)
    except SumUpError as exc:
        logger.warning(
            "SumUp verification failed for checkout %s: %s", tx_obj.sumup_checkout_id, exc
        )
        return ""

    sumup_status = (data.get("status") or "").upper()
    if sumup_status in ("PAID", "SUCCESSFUL"):
        _apply_paid_checkout(tx_obj, data)
    elif sumup_status in ("FAILED", "CANCELLED", "EXPIRED"):
        # Locked and re-read like the other two write paths. This was the last
        # one still writing a stale in-memory row, and a full save() at that —
        # so it overwrote every field, not just its own. The interleaving that
        # bites: a poll reads the row as PENDING, the member opens a
        # replacement checkout (which locks the row, deactivates this checkout
        # at SumUp and records CANCELLED with our own supersession note), then
        # SumUp answers this poll with FAILED — because it was just deactivated
        # — and the stale save puts the row back to FAILED with a generic
        # reason, erasing the record of what actually happened.
        if _record_terminal_failure(tx_obj, sumup_status, data):
            logger.warning(
                "SumUp checkout %s (%s) ended %s: %s",
                tx_obj.sumup_checkout_id,
                tx_obj.transaction_reference,
                sumup_status,
                tx_obj.failure_reason,
            )
    elif _has_unsuccessful_attempt(data):
        # Still PENDING at SumUp, but with card attempts recorded against it.
        # That is exactly what a *declined* card looks like: SumUp leaves the
        # checkout open so the customer can try another one, so the row stays
        # PENDING here too — closing it would kill a checkout that is still
        # payable. What changes is that the decline stops being invisible.
        # (A PENDING checkout with no attempts is just one nobody has paid yet;
        # there is nothing to say about it.)
        # Two separate questions, and _record_pending_failure decides both under
        # a lock. Whether there is anything NEW TO SAY is the summary changing;
        # whether the stored payload is STALE is the payload differing. SumUp
        # can return fresh diagnostic detail — a later timestamp, a code it did
        # not have before — under a summary that reads identically, and the
        # Coach Panel's "Raw provider response" claims to be the last thing
        # SumUp returned. Comparing rather than always writing matters because
        # this branch is re-entered every few seconds for a whole checkout.
        reason = describe_sumup_failure(data)
        if reason and _record_pending_failure(tx_obj, reason, data):
            # Only when the account of it actually changed — the log is for
            # someone reading back what happened, not a per-poll heartbeat.
            logger.info(
                "SumUp checkout %s (%s) is still open after a failed attempt: %s",
                tx_obj.sumup_checkout_id,
                tx_obj.transaction_reference,
                reason,
            )

    tx_obj.refresh_from_db()
    return sumup_status


def refresh_sumup_snapshot(tx_obj):
    """Re-read a checkout we have already settled, for its detail only.

    ``_sync_checkout_with_sumup`` returns immediately for any non-PENDING row,
    which is what stops a terminal row being re-applied — and it also means the
    Coach Panel's "Re-check with SumUp" did nothing whatsoever on a FAILED or
    CANCELLED payment while reporting that it had checked. Rows that failed
    before ``failure_reason`` existed could never be given one.

    So: fetch, record what SumUp says, change nothing else. ``status`` is not
    touched here on purpose. If SumUp reports a checkout PAID that we have
    written off, that is a discrepancy for a human to look at — quietly
    confirming a seat from an admin refresh is not this function's call to
    make, and the caller surfaces the mismatch instead.

    Returns SumUp's reported status, or "" if it could not be reached.
    """
    if not tx_obj.sumup_checkout_id:
        return ""

    try:
        data = SumUpClient().get_checkout(tx_obj.sumup_checkout_id)
    except SumUpError as exc:
        logger.warning(
            "SumUp refresh failed for checkout %s: %s", tx_obj.sumup_checkout_id, exc
        )
        return ""

    reported = (data.get("status") or "").upper()
    was = tx_obj.status
    with transaction.atomic():
        # The fourth writer, and the last one that was doing this unlocked. The
        # provider read above takes real time, and a reconciliation that loaded
        # this row as PENDING can apply PAID while it is in flight — clearing
        # the failure reason as it goes. Writing a diagnostic snapshot blind
        # then puts a failed payload and a reason back onto a payment that has
        # just been paid.
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx_obj.pk)
        if locked.status != was:
            logger.info(
                "Discarded an admin refresh for checkout %s — the row moved "
                "from %s to %s while SumUp was being read.",
                tx_obj.sumup_checkout_id,
                was,
                locked.status,
            )
            tx_obj.refresh_from_db()
            return reported

        locked.raw_response = data
        if reported in ("PAID", "SUCCESSFUL"):
            # Don't describe a success as a failure. A checkout SumUp reports as
            # paid has nothing to explain, whatever our row says about it.
            locked.failure_reason = ""
        elif locked.status != PaymentTransaction.Status.CANCELLED:
            _provider, notes = _split_failure_reason(locked.failure_reason)
            locked.failure_reason = _join_failure_reason(
                describe_sumup_failure(data), notes
            )
        else:
            # A CANCELLED row carries a reason WE wrote — "superseded by a
            # newer checkout" — and SumUp has no idea that is what happened: it
            # reports a deactivated checkout as a plain FAILED with no card
            # attempt. Taking its wording here would replace the one sentence
            # that distinguishes a checkout we killed from a card a bank
            # refused, which is the whole reason CANCELLED exists as a separate
            # status. The snapshot still refreshes; only the account is ours.
            pass
        locked.save(update_fields=["raw_response", "failure_reason", "updated_at"])

    tx_obj.refresh_from_db()
    return reported


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


def _payment_owner_ids(tx_obj):
    """Who this payment is FOR — not who happened to create the row.

    BOTH checkout creators let staff act for a member and stamp
    ``user=request.user``, so ``tx.user`` on a staff-opened checkout is the
    *staff* account, not the buyer. The linked registration or membership
    therefore wins outright wherever one exists; ``tx.user`` is the fallback
    only for unlinked rows that name nobody else.

    Including ``tx.user`` alongside the real owner outlived its usefulness the
    moment staff access could be revoked: a former staff member who once opened
    a checkout for someone kept a permanent, personal route to that member's
    widget and return page — including the Premium and coach disclosure — long
    after the ``is_staff`` bypass at the call sites stopped applying to them.
    Current staff are unaffected; they still pass via that bypass.

    Shared by the widget and the return page so the two cannot drift into
    disagreeing about who may see a payment.
    """
    if tx_obj.event_registration_id:
        return {tx_obj.event_registration.user_id}
    if tx_obj.premium_membership_id:
        return {tx_obj.premium_membership.user_id}
    return {tx_obj.user_id}


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

    # Ownership, not merely authentication. The reference is a short string
    # that travels in a URL — browser history, a shared link, a support ticket
    # — and this page now names the member's coach, so "somebody is logged in"
    # was enough to hand one member's Premium status and coach to another.
    # An unowned reference is answered exactly like an unknown one, so this
    # cannot be used to probe which references exist.
    if not tx_obj or (
        request.user.id not in _payment_owner_ids(tx_obj)
        and not request.user.is_staff
    ):
        if tx_obj:
            logger.warning(
                "User %s opened the SumUp return for transaction %s they do not own",
                request.user.id,
                tx_obj.transaction_reference,
            )
        messages.error(request, _("Payment transaction reference not found."))
        return redirect("crush_lu:home")

    _sync_checkout_with_sumup(tx_obj)

    # This route lives OUTSIDE i18n_patterns (urls_crush.py), so there is no
    # /fr/ or /de/ prefix for LocaleMiddleware to read and it falls back to the
    # language cookie or Accept-Language. Django only sets that cookie from the
    # set_language view, so a member who browsed in French without ever
    # touching the language switcher can still reach this page in English.
    #
    # NOT get_user_preferred_language. That one is profile-first, which is
    # right for email and wrong here: preferred_language is default="en" and
    # non-blank, so preferring it pins English on everyone who never opened
    # the setting. Using it here made this page WORSE than no override at all
    # — measured, for a default-profile member sending Accept-Language: fr it
    # turned a French message and a /fr/ redirect into English and /en/.
    # get_onscreen_language reads a stored "en" as "no answer" and defers to
    # the request, while still honouring an explicit de/fr choice.
    #
    # The redirects stay inside the override deliberately: reverse() picks the
    # language prefix from the active language, so this is also what stops a
    # translated confirmation from landing on an English page.
    lang = get_onscreen_language(user=request.user, request=request)
    with override(lang):
        return _sumup_return_response(request, tx_obj)


def _sumup_return_response(request, tx_obj):
    """Messages + destination for the human-facing return, under the caller's
    activated language. Split out only so the ``override`` block above stays
    readable — every path here returns a redirect."""
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
                #
                # Reads the CURRENT assignment, not pm.coach. This URL is
                # replayable from history long after the purchase, and both
                # CrushProfile.assigned_coach and PremiumMembership.coach are
                # editable in the admin — reassigning a member to a coach with
                # capacity is the documented remedy when confirm() fails. Two
                # fields meant the message could name the purchase-time coach
                # while the hub it redirects to named the current one, telling
                # the member two different coaches on one page-load. The hub
                # renders profile.assigned_coach, so this reads the same field
                # and the two cannot disagree.
                member_profile = getattr(pm.user, "crushprofile", None)
                coach_user = getattr(
                    getattr(member_profile, "assigned_coach", None), "user", None
                )
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
            else:
                # PAID but not granted. Three ways to get here and they are all
                # the same to the customer: the coach filled up mid-flight, the
                # request stopped being pending, or the buyer is no longer a
                # selected tester. _apply_paid_checkout logs each at error level
                # for a human, but until now this page said only "Payment
                # completed successfully" and dropped them on the hub with
                # nothing to show for it -- the charge is real, so silence here
                # reads as a purchase that worked.
                #
                # Deliberately vague about the cause: the remedies differ
                # (refund, coach reassignment, re-selection) and none of them is
                # the member's to action. What they need is to know the money
                # moved and that they did not get what they paid for.
                #
                # It does NOT say "our team has been notified". The only signal
                # this path emits is logger.error, which lands in App Insights
                # as an ordinary trace on a SUCCESSFUL request -- and
                # infra/alerts.bicep alerts on failed requests, server
                # exceptions, response time and availability, none of which this
                # trips. Nothing pages anyone and no work item exists, so
                # promising proactive contact would be false, and worse, it
                # would talk the member out of the one action that actually
                # reaches a human.
                messages.warning(
                    request,
                    _(
                        "Your payment went through, but we could not activate "
                        "your Premium membership yet. Please contact "
                        "support@crush.lu and we will sort it out."
                    ),
                )
            return redirect("crush_lu:crush_connect_hub")
    else:
        messages.warning(request, _("Payment is pending or was not completed."))

    return redirect("crush_lu:home")


@login_required
@require_GET
def sumup_widget_status(request, checkout_id):
    """Has this checkout resolved yet? Polled by the widget after 3DS.

    The card widget does not reliably announce the end of a 3-D Secure
    challenge. SumUp's own guidance for widget integrations is that
    ``onResponse`` emits ``auth-screen`` when the challenge *starts*, and that
    the final result must then be read back from the checkout resource on the
    server — the SDK is not the source of truth once the bank takes over.
    Without that second half, a customer who completed 3DS sat on the payment
    page forever: the widget had nothing more to say, and the page had no other
    way to learn the payment had gone through.

    Answers from our row after re-reading SumUp, so it can never be talked into
    a "paid" it has not verified. Side effects are the webhook's, deliberately:
    a payment that completed while the browser was stuck on the challenge is
    applied here — seat confirmed, ticket issued — rather than waiting for a
    callback that may never come.
    """
    tx_obj = get_object_or_404(PaymentTransaction, sumup_checkout_id=checkout_id)
    if request.user.id not in _payment_owner_ids(tx_obj) and not request.user.is_staff:
        raise Http404("No payment found.")

    # Bound the provider calls: the browser owns the polling interval, so the
    # ceiling has to live here. A throttled answer costs nothing — it is read
    # from a row that another worker may just have updated, and the next poll
    # picks up anything it missed.
    if tx_obj.status == PaymentTransaction.Status.PENDING and _may_ask_sumup(
        "poll", checkout_id
    ):
        _sync_checkout_with_sumup(tx_obj)

    settled = tx_obj.status != PaymentTransaction.Status.PENDING
    return JsonResponse(
        {
            "status": tx_obj.status,
            # The one field the page acts on: stop polling and move on.
            "settled": settled,
            "paid": tx_obj.status == PaymentTransaction.Status.PAID,
            # A refused card is not "settled" — SumUp keeps the checkout open so
            # another one can be tried, which is why the row stays PENDING. But
            # the page cannot be left waiting on it: if the SDK went quiet
            # (exactly the case this polling exists for) the customer would
            # watch "Confirming your payment" for the full five minutes over a
            # card that was declined a moment after the 3DS screen. Telling the
            # page an attempt failed lets it stop, say so, and let them try
            # another card on the same still-payable checkout.
            "attempt_failed": not settled and bool(tx_obj.failure_reason),
            # WHICH failure, so the page can tell a new one from the one it has
            # already shown — see attempt_marker().
            "attempt_marker": attempt_marker(tx_obj),
        }
    )


@login_required
@require_POST
def report_sumup_widget_failure(request, checkout_id):
    """The card widget telling us a payment attempt did not go through.

    Until this existed, a declined card was silent server-side. The widget
    printed a message in the customer's browser and stopped; SumUp left the
    checkout PENDING so another card could be tried, so neither the webhook nor
    the return page ever fired, and the PaymentTransaction sat PENDING forever
    with nothing recorded. The only trace of the whole event was a row in
    SumUp's dashboard reading "Échec" — off in a different system, with no
    reference tying it back to a member or a registration.

    The posted body is a *hint*, exactly as in the webhook: it is written by the
    browser, so it names the checkout and supplies the widget's own wording, and
    then the checkout is re-read from SumUp for anything authoritative. It
    cannot move a payment to PAID; ``_sync_checkout_with_sumup`` is the only
    thing that decides status, and it asks SumUp.
    """
    tx_obj = get_object_or_404(PaymentTransaction, sumup_checkout_id=checkout_id)
    if request.user.id not in _payment_owner_ids(tx_obj) and not request.user.is_staff:
        raise Http404("No payment found.")

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    widget_type = str(payload.get("type") or "error")[:40]
    # Truncated because it is attacker-controlled in the sense that any logged-in
    # owner can post arbitrary text here; it lands in a TextField and in the log.
    # "|" removed because notes are counted by their joiner, and that count is
    # what tells the page whether another card was refused. Left in, a member
    # could inflate their own failure count by typing it into a card form.
    widget_message = str(payload.get("message") or "")[:300].replace("|", "/")

    logger.warning(
        "SumUp widget reported '%s' for checkout %s (%s), user %s: %s",
        widget_type,
        checkout_id,
        tx_obj.transaction_reference,
        request.user.id,
        widget_message or "(no message)",
    )

    # Ask SumUp first — if the checkout is genuinely terminal, its verdict and
    # its wording are better than the widget's, and this is also the path that
    # would catch a payment that actually succeeded despite a widget error.
    #
    # Bounded like the poll is. Nothing stops an owner (or a retry loop in some
    # future version of the widget) posting here as fast as it likes, and while
    # the checkout stays PENDING — the normal state after a decline — every one
    # of those posts would otherwise be a live call to SumUp. The report itself
    # is still always recorded below; only the provider call is rationed.
    if _may_ask_sumup("report", checkout_id):
        _sync_checkout_with_sumup(tx_obj)

    note = f"Card widget reported '{widget_type}'"
    if widget_message:
        note += f": {widget_message}"
    _append_widget_note(tx_obj, note)

    # Answer with where the payment actually stands, not just "noted".
    #
    # The sync above can find the checkout PAID — an SDK error raised after the
    # money was captured is a real sequence, and the page has already stopped
    # polling by the time it calls this. Saying only "recorded" left that
    # customer looking at a decline for a payment that went through, with
    # nothing left running to correct it. This is the last thing the page hears
    # on that path, so it has to carry the verdict.
    tx_obj.refresh_from_db()
    settled = tx_obj.status != PaymentTransaction.Status.PENDING
    return JsonResponse(
        {
            "status": "recorded",
            "settled": settled,
            "paid": tx_obj.status == PaymentTransaction.Status.PAID,
            "attempt_marker": attempt_marker(tx_obj),
        }
    )


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
    if request.user.id not in _payment_owner_ids(tx_obj) and not request.user.is_staff:
        raise Http404("No payment found.")

    # Revocation has to reach the card form, not only checkout creation. THIS is
    # the step that prevents the charge; refusing at completion only withholds
    # the entitlement, after SumUp has captured the money and a human owes a
    # refund. Deliberately NOT applied to the return page or the status/failure
    # endpoints below -- those report on a payment that may already have
    # happened, and someone who just handed over money is owed the truth about
    # it. Answers exactly like an unknown checkout, so it is not an oracle for
    # which checkouts exist.
    if _premium_purchase_refused(tx_obj.premium_membership):
        logger.warning(
            "Blocked SumUp widget for checkout %s: the buyer of membership %s "
            "is not a selected beta tester",
            checkout_id,
            tx_obj.premium_membership_id,
        )
        raise Http404("No payment found.")

    context = {
        "checkout_id": checkout_id,
        "transaction": tx_obj,
        "amount": tx_obj.amount,
        "currency": tx_obj.currency,
        # The failure baseline, rendered into the page rather than fetched by
        # it. Fetching cannot be made safe here however early it is started:
        # the status endpoint does a live provider read, so its answer can
        # reflect state recorded AFTER the customer submitted a card, and the
        # page would file that failure as one it had already seen. Serving the
        # value with the HTML makes it true by construction — it is the record
        # as it stood before this page existed, so nothing the customer does
        # from here can be inside it.
        "attempt_marker": attempt_marker(tx_obj),
        "return_url": request.build_absolute_uri(f"/payments/sumup/return/?ref={tx_obj.transaction_reference}"),
    }
    return render(request, "crush_lu/payments/sumup_widget.html", context)
