"""Open a recovery case when a captured Premium payment is not applied (#925).

The case is written in the PAID transaction; emails go out on commit and only
when that call created the case (replays never mail twice).
Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

import contextlib
import contextvars
import functools
import logging
import time

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone, translation

logger = logging.getLogger(__name__)

# Siblings re-read per membership and run; the rest wait for the next hourly
# tick.
SIBLING_SYNC_LIMIT = 2
# Memberships closed inside a member's request; the rest wait for the tick.
REQUEST_CLOSE_LIMIT = 1
# Worst case of one Graph send (GRAPH_SEND_TIMEOUT_SECONDS in api_admin_sumup).
SEND_SECONDS = 30

# Set by retry_unsent_notifications for the hourly tick: every network step
# below, nested on-commit work included, starts only while it still fits.
# Unset (None) on the member's own request paths.
_deadline = contextvars.ContextVar("premium_recovery_deadline", default=None)
# True inside a member's request callback (set by _request_deadline).
_on_request = contextvars.ContextVar("premium_recovery_on_request", default=False)


# Wall time a member's request (return page, webhook) may spend on its commit
# callbacks -- receipt mail included -- counted from when they are queued,
# after the request's own SumUp read, under Gunicorn's 120 s timeout: room for
# one close pass (_close_seconds, 70 s); whatever no longer fits (a close after
# a slow receipt, the notices) is left to the hourly tick.
REQUEST_BUDGET_SECONDS = 75


# Whole-request bound for a member endpoint that settles checkouts and may
# apply several captures (checkout creation, cancel): one deadline from the
# request's start for all of it, under Gunicorn's 120 s, with room left for
# the SumUp call already in flight when it runs out.
REQUEST_TOTAL_SECONDS = 100


def request_deadline():
    """The deadline to hand an on-commit callback, taken when it is queued so
    every callback of the request shares one budget -- the endpoint's own
    deadline when it runs under bounded_request."""
    if _on_request.get() and _deadline.get() is not None:
        return _deadline.get()
    return time.monotonic() + REQUEST_BUDGET_SECONDS


def bounded_request(func):
    """Run ``func`` under one request-wide deadline (REQUEST_TOTAL_SECONDS)."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if _deadline.get() is not None:
            return func(*args, **kwargs)
        token = _deadline.set(time.monotonic() + REQUEST_TOTAL_SECONDS)
        flag = _on_request.set(True)
        try:
            return func(*args, **kwargs)
        finally:
            _on_request.reset(flag)
            _deadline.reset(token)

    return wrapper


@contextlib.contextmanager
def _request_deadline(deadline=None):
    """Bound a member-request callback unless a tick deadline is already set."""
    if _deadline.get() is not None:
        yield
        return
    token = _deadline.set(deadline if deadline is not None else request_deadline())
    flag = _on_request.set(True)
    try:
        yield
    finally:
        _on_request.reset(flag)
        _deadline.reset(token)


def _fits(seconds):
    deadline = _deadline.get()
    return deadline is None or time.monotonic() + seconds <= deadline


def _close_seconds():
    """One settle pass: its own retire budget plus the close in flight when
    that budget runs out (a DELETE and one read)."""
    from crush_lu.management.commands.reconcile_sumup_payments import (
        SUMUP_REQUEST_WORST_CASE_SECONDS,
    )
    from crush_lu.views_payments import _PREMIUM_CHECKOUT_RETIRE_BUDGET_SECONDS

    return (
        _PREMIUM_CHECKOUT_RETIRE_BUDGET_SECONDS + 2 * SUMUP_REQUEST_WORST_CASE_SECONDS
    )


def _sync_seconds():
    from crush_lu.management.commands.reconcile_sumup_payments import (
        SUMUP_REQUEST_WORST_CASE_SECONDS,
    )

    return SUMUP_REQUEST_WORST_CASE_SECONDS


def member_notice(payment):
    """The D1 notice text for ``payment``, in the active language."""
    from django.utils.formats import localize
    from django.utils.translation import gettext as _

    return _(
        "We received your payment of %(amount)s %(currency)s, "
        "but it has not been applied to your Premium "
        "membership. Please do not pay again. Reference: "
        "%(reference)s. Contact support@crush.lu if you need "
        "help."
    ) % {
        # localize() matches {{ amount }} (10,00 in DE/FR).
        "amount": localize(payment.amount),
        "currency": payment.currency,
        "reference": payment.transaction_reference,
    }


def _stale_checkouts(user_id):
    """PENDING checkouts on the member's memberships that are no longer up for
    payment (active or cancelled): an open case may have failed to close
    them, and resolving it must not leave them payable or unblocked."""
    from crush_lu.models import PaymentTransaction

    return PaymentTransaction.objects.filter(
        premium_membership__user_id=user_id,
        status=PaymentTransaction.Status.PENDING,
    ).exclude(premium_membership__status="pending")


def blocks_new_charge(user):
    """True while ``user`` must not be charged again (#925): any OPEN case,
    any case (even resolved) whose membership still has a checkout SumUp has
    not confirmed closed, or -- case or not -- any such checkout left on a
    membership of the member no longer up for payment (the hourly tick closes
    those)."""
    from django.db.models import Q

    from crush_lu.models import PaymentTransaction, PremiumPaymentRecoveryCase

    # A member-unknown case is filed under the staff opener: it is not that
    # person's own payment, so it gates nothing of theirs. A staff-only case
    # of a known member (ambiguous backfill) still gates: only its notice is
    # withheld.
    cases = PremiumPaymentRecoveryCase.objects.filter(user=user).exclude(
        MEMBER_UNKNOWN_Q
    )
    return (
        cases.filter(
            Q(status=PremiumPaymentRecoveryCase.Status.OPEN)
            | Q(
                premium_membership__payment_transactions__status=(
                    PaymentTransaction.Status.PENDING
                )
            )
        ).exists()
        or _stale_checkouts(user.pk).exists()
    )


def reason_for_membership_status(status):
    """Map the membership state a capture found to the case reason."""
    from crush_lu.models import PremiumPaymentRecoveryCase

    Reason = PremiumPaymentRecoveryCase.Reason
    return {
        "active": Reason.DUPLICATE_CAPTURE,
        "cancelled": Reason.REQUEST_CANCELLED,
        # Still pending after confirm() refused: the coach was full.
        "pending": Reason.COACH_UNAVAILABLE,
    }.get(status, Reason.OTHER)


def open_case(payment, reason, detail="", staff_only=False):
    """Get-or-create the case inside the caller's PAID transaction.

    May raise: a failure must roll PAID back so the capture is retried, never
    commit without its case. Locks the owner's User row first -- after the
    caller's payment and membership locks, the order checkout publication
    takes -- so publication's member-locked recheck
    (views_payments._lock_member_and_check_blocked) serializes with this
    insert instead of relying on a deferred FK check. ``staff_only`` withholds
    the member-facing notice (the applied capture is not provable)."""
    from django.contrib.auth import get_user_model

    from crush_lu.models import PremiumPaymentRecoveryCase

    membership = payment.premium_membership
    # Unlinked: only legacy rows (the FK is PROTECT now), so payment.user is
    # the only member evidence left, as in email_helpers._receipt_recipient --
    # unless staff opened the checkout for a member (user=request.user): then
    # the member is unknown and only staff are told (_staff_only).
    owner = membership.user if membership else payment.user
    member_unknown = _member_unknown_for(membership, owner)
    if member_unknown:
        staff_only = True
        detail = f"{MEMBER_UNKNOWN_DETAIL} {detail}".strip()
    if owner is not None:
        list(get_user_model().objects.select_for_update().filter(pk=owner.pk))
    return PremiumPaymentRecoveryCase.objects.get_or_create(
        payment=payment,
        defaults={
            "user": owner,
            "premium_membership": membership,
            "reason": reason,
            "detail": detail,
            "staff_only": staff_only,
            "member_unknown": member_unknown,
        },
    )


MEMBER_UNKNOWN_DETAIL = (
    "Member unknown: legacy unlinked checkout opened by staff; the case is "
    "filed under the staff account."
)


# Query form of _staff_only: no member notice.
STAFF_ONLY_Q = Q(staff_only=True)
# The case's user is the staff opener: it gates and closes nothing of theirs.
MEMBER_UNKNOWN_Q = Q(member_unknown=True)


def _member_unknown_for(membership, owner):
    # Any unlinked row is legacy (the FK is PROTECT now), and whether staff
    # opened it for someone else is not recorded: the opener's is_staff bit
    # may have changed since. Conservatively, only staff are told.
    return membership is None and owner is not None


def _staff_only(case):
    return case.staff_only


def notify_safely(case_pk, deadline=None):
    """on_commit callback: member notice + staff alert for a NEW case."""
    from crush_lu.models import PremiumPaymentRecoveryCase

    try:
        case = PremiumPaymentRecoveryCase.objects.select_related("payment", "user").get(
            pk=case_pk
        )
    except Exception as exc:
        logger.error(
            "Failed to load Premium recovery case %s: %s",
            case_pk,
            type(exc).__name__,
        )
        return
    with _request_deadline(deadline):
        try:
            _close_sibling_checkouts_safely(case)
        except Exception as exc:
            # The notices below must still go out; the tick retries the close.
            logger.error(
                "Failed to close checkouts for recovery case %s: %s",
                case_pk,
                type(exc).__name__,
            )
        # Staff may have resolved it during the close's SumUp calls.
        if (
            PremiumPaymentRecoveryCase.objects.filter(pk=case_pk)
            .values_list("status", flat=True)
            .first()
            != PremiumPaymentRecoveryCase.Status.OPEN
        ):
            return
        _notify_member_safely(case)
        _alert_staff_safely(case)


def close_after_activation_safely(membership, deadline=None):
    """on_commit after a successful activation: close the membership's other
    checkouts within the request budget; the hourly tick does the rest."""
    try:
        with _request_deadline(deadline):
            close_open_checkouts_safely(
                [membership], f"premium membership {membership.pk}"
            )
    except Exception as exc:
        logger.error(
            "Failed to close checkouts for premium membership %s: %s",
            membership.pk,
            type(exc).__name__,
        )


def _close_sibling_checkouts_safely(case):
    """While the case is OPEN, close every PENDING checkout the member has,
    whichever membership it belongs to: a checkout published just before the
    case was inserted must not stay payable. A resolved case keeps closing its
    own membership's and any left on a membership no longer up for payment."""
    from django.db import transaction

    from crush_lu.models import PaymentTransaction, PremiumMembership

    # The status and the memberships it selects are read under the case row
    # lock, so staff cannot resolve the case in between (and a replacement
    # checkout cannot be published while it is OPEN). Released before any
    # SumUp call; skipped if a tick holds the case (it closes them itself).
    with transaction.atomic():
        locked = (
            type(case)
            .objects.select_for_update(skip_locked=True, of=("self",))
            .filter(pk=case.pk)
            .values_list("status", flat=True)
            .first()
        )
        if locked is None:
            return
        case.status = locked
        memberships = [
            m
            for m in [case.premium_membership]
            if m is not None
            and PaymentTransaction.objects.filter(
                premium_membership=m, status=PaymentTransaction.Status.PENDING
            ).exists()
        ]
        # A member-unknown case is filed under the staff opener, whose own
        # memberships have nothing to do with this payment.
        if case.user_id and not case.member_unknown:
            others = PremiumMembership.objects.filter(
                user_id=case.user_id,
                payment_transactions__status=PaymentTransaction.Status.PENDING,
            ).exclude(pk=case.premium_membership_id)
            if case.status != case.Status.OPEN:
                # Resolved: only memberships no longer up for payment (see
                # _stale_checkouts); a new request's own checkout is its own.
                others = others.exclude(status="pending")
            memberships += list(others.distinct().order_by("pk"))
        # The exact rows this case may close, fixed under the lock: a
        # replacement checkout published once the case is resolved is not one.
        snapshot = set(
            PaymentTransaction.objects.filter(
                premium_membership__in=memberships,
                status=PaymentTransaction.Status.PENDING,
            ).values_list("pk", flat=True)
        )
    if _on_request.get() or _deadline.get() is None:
        # A member's request (return page, webhook): the case's own
        # membership first and at most REQUEST_CLOSE_LIMIT in all; the hourly
        # retry closes the rest, as the open case still has open checkouts.
        memberships = memberships[:REQUEST_CLOSE_LIMIT]
    # PK order: every caller locks payments -> membership in the same order.
    memberships.sort(key=lambda m: m.pk)
    close_open_checkouts_safely(
        memberships, f"recovery case {case.pk}", only_ids=snapshot
    )


def close_open_checkouts_safely(memberships, label, only_ids=None):
    """Close the PENDING SumUp checkouts of ``memberships`` (deactivate only,
    never a refund): with a capture recorded, none of them may take another
    payment. Post-commit, no lock held during the network calls. A checkout
    SumUp already captured is recorded (applied, or its own recovery case),
    from the payload the close read when there is one -- the sweep reads PAID
    rows only. ``only_ids`` limits it to a snapshot of payment rows taken by
    the caller; rows published after it are left alone."""
    from django.db import transaction

    from crush_lu.models import PaymentTransaction

    def pending_of(membership):
        rows = PaymentTransaction.objects.filter(
            premium_membership=membership, status=PaymentTransaction.Status.PENDING
        )
        return rows if only_ids is None else rows.filter(pk__in=only_ids)

    for membership in memberships:
        if not pending_of(membership).exists():
            continue
        if not _fits(_close_seconds()):
            logger.warning("No time left to close checkouts for %s; will retry", label)
            return
        from crush_lu.views_payments import (
            SumUpClient,
            _apply_paid_checkout,
            _lock_premium_checkout_state,
            _settle_pending_premium_checkouts,
            _sync_checkout_with_sumup,
        )

        try:
            paid_payloads = {}
            state, _reuse, retired, _known = _settle_pending_premium_checkouts(
                SumUpClient(),
                membership,
                captured=True,
                paid_payloads=paid_payloads,
                only_ids=only_ids,
            )
            if retired:
                with transaction.atomic():
                    _lock_premium_checkout_state(membership.pk, retired)
            # A capture SumUp already reported is always recorded: left
            # PENDING, nothing would read it again. Only its mails and cleanup
            # (on-commit, deadline-bounded) wait. It counts against the same
            # cap as a read; the rest stay PENDING beside a PAID row, where
            # the hourly tick finds them.
            handled = 0
            rows = sorted(
                pending_of(membership).filter(sumup_checkout_id__isnull=False),
                # Captures already read first: they need no further request.
                key=lambda row: (row.pk not in paid_payloads, row.pk),
            )
            for row in rows:
                if handled >= SIBLING_SYNC_LIMIT:
                    break
                if row.pk in paid_payloads:
                    handled += 1
                    _apply_paid_checkout(row, paid_payloads[row.pk])
                # A read that finds PAID applies it, which may mail on commit.
                elif _fits(_sync_seconds() + 2 * SEND_SECONDS):
                    handled += 1
                    _sync_checkout_with_sumup(row)
            if state == "open":
                # Still PENDING, so the hourly retry picks it up again.
                logger.warning("Checkout still open for %s; will retry", label)
        except Exception as exc:
            logger.error(
                "Failed to close checkouts for %s: %s", label, type(exc).__name__
            )


def _annotated_cases():
    """Recovery cases annotated with the open checkouts they must close."""
    from django.db.models import Exists, OuterRef

    from crush_lu.models import PaymentTransaction, PremiumPaymentRecoveryCase

    pending = PaymentTransaction.objects.filter(
        status=PaymentTransaction.Status.PENDING
    )
    return PremiumPaymentRecoveryCase.objects.annotate(
        # A sibling checkout that could not be closed is retried too.
        has_open_checkout=Exists(
            pending.filter(premium_membership_id=OuterRef("premium_membership_id"))
        ),
        # An open case closes the member's other memberships' too.
        member_has_open_checkout=Exists(
            pending.filter(premium_membership__user_id=OuterRef("user_id"))
        ),
        # Any case keeps closing those left where no payment is due.
        member_has_stale_checkout=Exists(
            pending.filter(premium_membership__user_id=OuterRef("user_id")).exclude(
                premium_membership__status="pending"
            )
        ),
    )


def _needs_close():
    from django.db.models import Q

    from crush_lu.models import PremiumPaymentRecoveryCase

    # Member-wide lookups do not apply to a member-unknown case: its user is
    # the staff opener, whose own checkouts it never closes.
    return (
        Q(has_open_checkout=True)
        | Q(member_has_stale_checkout=True, member_unknown=False)
        | Q(
            member_has_open_checkout=True,
            member_unknown=False,
            status=PremiumPaymentRecoveryCase.Status.OPEN,
        )
    )


def close_payable_checkouts(budget_seconds, limit=5, settle_minutes=10):
    """The money half of the hourly tick, run BEFORE the refund sweep so a busy
    sweep cannot starve it: close checkouts left beside a capture, on a
    deactivated owner's or a finished membership, or that a case must close.
    Idempotent (deactivate only); each close starts only while it fits."""
    from datetime import timedelta

    token = _deadline.set(time.monotonic() + budget_seconds)
    try:
        _close_checkouts_beside_a_capture(limit, settle_minutes)
        cutoff = timezone.now() - timedelta(minutes=settle_minutes)
        cases = (
            _annotated_cases()
            .filter(_needs_close(), created_at__lte=cutoff)
            .select_related("payment", "user", "premium_membership")
            .order_by("?")[:limit]
        )
        for case in cases:
            if not _fits(_close_seconds()):
                break
            _close_sibling_checkouts_safely(case)
    finally:
        _deadline.reset(token)


def retry_unsent_notifications(budget_seconds, limit=5, settle_minutes=10):
    """Hourly retry of what failed after a case opened: a member notice, a
    staff alert, or closing a checkout that can still take money.

    Run by the SumUp reconciliation tick with what is left of its budget:
    each send, close or read starts only while its own worst case still fits
    (``_fits``); what does not fit stays undone for the next tick. Skips cases newer than ``settle_minutes`` (their on-commit
    send may still be running). No age cut-off: an open case keeps being
    retried until it is delivered or resolved; random order so one that can
    never be delivered cannot starve the others."""
    from datetime import timedelta

    from django.db import transaction
    from django.db.models import Q

    from crush_lu.models import PremiumPaymentRecoveryCase

    Case = PremiumPaymentRecoveryCase
    now = timezone.now()
    case_ids = list(
        _annotated_cases()
        .filter(
            # Notices only for open cases; closing a checkout that could
            # still take money continues even after staff resolve the case.
            (
                # A member-unknown case (_staff_only) sends no notice.
                Q(member_notified_at__isnull=True) & ~STAFF_ONLY_Q
                | Q(staff_alerted_at__isnull=True)
                | Q(member_has_open_checkout=True, member_unknown=False)
            )
            & Q(status=Case.Status.OPEN)
            | _needs_close(),
            created_at__lte=now - timedelta(minutes=settle_minutes),
        )
        .order_by("?")
        .values_list("pk", flat=True)[:limit]
    )
    token = _deadline.set(time.monotonic() + budget_seconds)
    sent = 0
    try:
        # Money first: a notice that keeps timing out must not starve the
        # close of a checkout that could still take a payment.
        _close_checkouts_beside_a_capture(limit, settle_minutes)
        for pk in case_ids:
            # Nothing per case is cheaper than a send.
            if not _fits(SEND_SECONDS):
                break
            case = (
                Case.objects.select_related("payment", "user", "premium_membership")
                .filter(pk=pk)
                .first()
            )
            if case is None:
                continue
            # Outside the claim below: closing is idempotent, and holding the
            # case lock across several memberships' payment locks could
            # deadlock an overlapping tick or an account merge.
            _close_sibling_checkouts_safely(case)
            with transaction.atomic():
                # Claim: an overlapping tick skips a case another run holds,
                # and the timestamps are re-read under the lock, so no double
                # send.
                case = (
                    # of=("self",): PostgreSQL refuses FOR UPDATE on the
                    # nullable side of the premium_membership outer join.
                    Case.objects.select_for_update(skip_locked=True, of=("self",))
                    .select_related("payment", "user", "premium_membership")
                    .filter(pk=pk)
                    .first()
                )
                if case is None:
                    continue
                sent += 1
                if case.status != Case.Status.OPEN:
                    continue
                if case.member_notified_at is None:
                    _notify_member_safely(case)
                if case.staff_alerted_at is None:
                    _alert_staff_safely(case)
    finally:
        _deadline.reset(token)
    return sent


def _close_checkouts_beside_a_capture(limit, settle_minutes):
    """Retry the close a successful activation queued on commit: a membership
    with a PAID payment must have no PENDING checkout left, case or not.
    The PENDING row itself is the durable to-do; overlapping ticks only
    repeat an idempotent deactivate."""
    from datetime import timedelta

    from django.db.models import Exists, OuterRef, Q

    from crush_lu.models import PaymentTransaction, PremiumMembership

    def payments(status, **extra):
        return PaymentTransaction.objects.filter(
            premium_membership_id=OuterRef("pk"), status=status, **extra
        )

    cutoff = timezone.now() - timedelta(minutes=settle_minutes)
    memberships = list(
        PremiumMembership.objects.filter(
            # A recorded capture, an owner a merge deactivated, or a
            # membership no longer up for payment: a checkout nobody will
            # close by hand stays payable otherwise.
            Q(Exists(payments(PaymentTransaction.Status.PAID)))
            | Q(user__is_active=False)
            | ~Q(status="pending"),
            Exists(payments(PaymentTransaction.Status.PENDING, created_at__lte=cutoff)),
        ).order_by("?")[:limit]
    )
    memberships.sort(key=lambda m: m.pk)
    close_open_checkouts_safely(
        memberships, "memberships whose checkouts must not stay payable"
    )


def _notify_member_safely(case):
    from crush_lu.email_helpers import send_premium_payment_recovery_notice

    if _staff_only(case):
        # Would mail the member's notice to the staff opener; the staff alert
        # carries MEMBER_UNKNOWN_DETAIL instead.
        return
    if not _fits(SEND_SECONDS):
        return
    try:
        # The case's recorded member, whom it gates -- not whoever the
        # membership names now (its user is editable in the admin).
        if send_premium_payment_recovery_notice(case.payment, recipient=case.user):
            case.member_notified_at = timezone.now()
            case.save(update_fields=["member_notified_at"])
    except Exception as exc:
        logger.error(
            "Failed to send Premium recovery notice for case %s: %s",
            case.pk,
            type(exc).__name__,
        )


def _alert_staff_safely(case):
    from azureproject.email_utils import send_domain_email

    if not _fits(SEND_SECONDS):
        return
    try:
        payment = case.payment
        admin_path = reverse(
            "crush_admin:crush_lu_premiumpaymentrecoverycase_change",
            args=[case.pk],
            urlconf="azureproject.urls_crush",
        )
        # English only: the Coach Panel is English.
        with translation.override("en"):
            reason = str(case.get_reason_display())
        message = (
            "A captured Premium payment was NOT applied and needs a human.\n\n"
            f"Reason: {reason}\n"
            f"Member: {case.user.email} (user {case.user_id})\n"
            f"Amount: {payment.amount} {payment.currency}\n"
            f"Reference: {payment.transaction_reference}\n"
            + (f"Detail: {case.detail}\n" if case.detail else "")
            + f"Case: {settings.PREMIUM_RECOVERY_ADMIN_BASE_URL}{admin_path}\n"
        )
        if send_domain_email(
            subject=f"[Crush.lu] Premium payment not applied: "
            f"{payment.transaction_reference}",
            message=message,
            recipient_list=[settings.PREMIUM_RECOVERY_ALERT_EMAIL],
            domain="crush.lu",
            fail_silently=False,
        ):
            case.staff_alerted_at = timezone.now()
            case.save(update_fields=["staff_alerted_at"])
        else:
            logger.warning("Premium recovery staff alert not sent: case %s", case.pk)
    except Exception as exc:
        logger.error(
            "Failed to send Premium recovery staff alert for case %s: %s",
            case.pk,
            type(exc).__name__,
        )


# --- Resolution (#925 WP1 part 2) -------------------------------------------


def resolve_case(case, resolution, by_user=None):
    """Mark ``case`` resolved: how (``Resolution``), when and by whom."""
    from crush_lu.models import PremiumPaymentRecoveryCase

    case.status = PremiumPaymentRecoveryCase.Status.RESOLVED
    case.resolution = resolution
    case.resolved_at = timezone.now()
    case.resolved_by = by_user
    case.save(update_fields=["status", "resolution", "resolved_at", "resolved_by"])


def refund_is_of_unapplied_capture(payment, lock=False):
    """True when ``payment``'s case proves its capture was never applied, so a
    refund of it settles the case and nothing else (D2/D4).

    Only an OPEN case that was never resolved proves it: one resolved any
    other way (applied, by hand, or before resolutions were recorded) may
    have had its capture applied, and reopening it in the admin keeps that
    resolution. Nor a staff-only case of a known member -- which capture was
    applied is unknown there. All take the sweep's usual path.

    ``lock`` (payment row already locked): classify the locked case, so a
    resolution staff save meanwhile is read, never overwritten."""
    from crush_lu.models import PremiumPaymentRecoveryCase

    cases = PremiumPaymentRecoveryCase.objects.filter(payment=payment)
    if lock:
        cases = cases.select_for_update()
    case = cases.first()
    return (
        case is not None
        and case.status == PremiumPaymentRecoveryCase.Status.OPEN
        and not case.resolution
        and (case.member_unknown or not case.staff_only)
    )


def record_refund_on_case(payment):
    """After the sweep wrote a refund (payment row locked): resolve its case,
    on either path -- an open case must not keep gating a refunded member.

    Lock order stays payment -> case: the hourly retry holds a case lock only
    while it mails, never a payment lock."""
    from crush_lu.models import PremiumPaymentRecoveryCase

    case = (
        PremiumPaymentRecoveryCase.objects.select_for_update()
        .filter(payment=payment)
        .first()
    )
    if case is not None and case.resolution != (
        PremiumPaymentRecoveryCase.Resolution.REFUNDED
    ):
        resolve_case(case, PremiumPaymentRecoveryCase.Resolution.REFUNDED)


def apply_case_payment(case_pk, by_user):
    """D3: the member agreed to the membership's coach (staff may have
    reassigned it first); apply this capture and resolve the case.

    Returns an error message, or None on success. Locks payment ->
    membership -> CrushProfile (confirm()), the payment path's order; the
    case row is locked after the payment."""
    from django.db import transaction

    from crush_lu.models import (
        CrushProfile,
        PaymentTransaction,
        PremiumMembership,
        PremiumPaymentRecoveryCase,
    )
    from crush_lu.views_payments import (
        _premium_purchase_refused,
        _send_premium_membership_receipt_safely,
    )

    Case = PremiumPaymentRecoveryCase
    payment_id = (
        Case.objects.filter(pk=case_pk).values_list("payment_id", flat=True).first()
    )
    if payment_id is None:
        return "Case not found."
    try:
        with transaction.atomic():
            payment = PaymentTransaction.objects.select_for_update().get(pk=payment_id)
            case = Case.objects.select_for_update().get(pk=case_pk)
            if case.status != Case.Status.OPEN:
                return "The case is already resolved."
            if case.reason != Case.Reason.COACH_UNAVAILABLE or case.staff_only:
                return "Only an open coach-unavailable case can be applied."
            # Locked and read fresh: the owner and coach confirm() applies
            # are the ones validated here, not a stale admin edit's.
            membership = (
                PremiumMembership.objects.select_for_update()
                .filter(pk=case.premium_membership_id)
                .first()
            )
            if (
                payment.status != PaymentTransaction.Status.PAID
                or membership is None
                or payment.premium_membership_id != membership.pk
            ):
                return "The payment is no longer a capture of this membership."
            if membership.user_id != case.user_id:
                # The membership changed hands in the admin since: the
                # member who paid and agreed is case.user.
                return "The membership no longer belongs to the case's member."
            # As at payment completion: revocation wins the race (locked).
            if _premium_purchase_refused(membership, lock=True):
                return "The member is no longer a selected beta tester."
            membership.confirm(by_user=by_user)
            resolve_case(case, Case.Resolution.APPLIED, by_user)
            transaction.on_commit(
                lambda: _send_premium_membership_receipt_safely(payment)
            )
            transaction.on_commit(
                lambda: close_after_activation_safely(membership), robust=True
            )
    except CrushProfile.DoesNotExist:
        return "The member has no profile."
    except ValueError as exc:
        return str(exc)
    return None
