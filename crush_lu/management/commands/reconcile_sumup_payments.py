"""
reconcile_sumup_payments — background reconciliation sweep for SumUp payments.

Tier-2 refund reconciliation: SumUp sends no webhook for refunds initiated
directly in its own merchant portal or on a POS terminal. This command polls
recently-settled (PAID) SumUp transactions, detects external refunds, and
brings Django's financial, registration, credit, and membership state back in sync.

Usage::

    # Sweep PAID checkouts from the last 30 days
    python manage.py reconcile_sumup_payments

    # Preview changes without modifying the database
    python manage.py reconcile_sumup_payments --dry-run

    # Check a custom lookback window (e.g. 60 days) quietly
    python manage.py reconcile_sumup_payments --days 60 --quiet

    # Reconcile a single checkout
    python manage.py reconcile_sumup_payments --checkout-id <sumup_checkout_id>

⚠️ ``--days`` windows on ``PaymentTransaction.created_at`` — when the member
PAID, not when the refund happened. A refund taken today against a three-week-old
seat is outside ``--days 7`` no matter how recent the refund is. To check a
refund you just made, name the payment instead: ``--reference`` and
``--checkout-id`` skip the date window entirely.
"""

import logging
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import EventRegistration
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import PremiumMembership
from crush_lu.services.credits import void_credit
from crush_lu.services.sumup import SumUpClient, SumUpError

logger = logging.getLogger(__name__)


def _to_decimal(value) -> Decimal:
    """Coerce a SumUp amount to Decimal, never raising.

    SumUp is not consistent about whether amounts come back as numbers or as
    numeric strings, and a bare ``"0.00" > 0`` raises TypeError on Python 3.
    ``is_checkout_refunded()`` is called *outside* the per-row try/except in
    ``handle()``, so an uncaught TypeError there would kill the entire sweep
    rather than skip one checkout. Everything numeric from SumUp goes through
    here — the same defensive coercion ``views_payments`` already applies.
    """
    if value is None:
        return Decimal("0")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")

    # "Infinity" and "NaN" are valid Decimal literals, and the stdlib json
    # module parses those tokens by default — so response.json() can hand us
    # either. Neither raises here; NaN raises InvalidOperation later, from the
    # max() comparison in refunded_amount(), which sits outside the caller's
    # try/except and would kill the sweep. Same guard as the donation-amount
    # parse in views_payments.
    if not parsed.is_finite():
        return Decimal("0")
    return parsed


# SumUp spells the refunded total differently in the two places it reports it:
# the checkout resource says ``amount_refunded``, the transaction-history rows
# say ``refunded_amount``. Only the first was ever read, so a history row
# recording a full refund — the one place an externally-refunded payment shows
# up at all — reported nothing refunded.
_REFUNDED_TOTAL_KEYS = ("amount_refunded", "refunded_amount")


def _refunded_total(item) -> Decimal:
    """Largest refunded total this payload reports, under either spelling."""
    if not isinstance(item, dict):
        return Decimal("0")
    return max(_to_decimal(item.get(key)) for key in _REFUNDED_TOTAL_KEYS)


def history_row_shows_refund(item) -> bool:
    """Does one transaction-history row record a refund?

    Kept as one predicate because the same four signals are asked for in three
    places — indexing, detection, and the fallback's decision to look again —
    and a copy that learns about ``refunded_amount`` while its siblings do not
    is exactly the drift that hid the last one.
    """
    if not isinstance(item, dict):
        return False
    return (
        (item.get("status") or "").upper() == "REFUNDED"
        or (item.get("type") or "").upper() == "REFUND"
        or _refunded_total(item) > 0
        or bool(item.get("refunds"))
    )


def index_history(history_map: dict, items) -> dict:
    """Index history rows by ``transaction_code``, keeping the refund-bearing one.

    A payment and the refund taken against it SHARE a transaction_code and come
    back as two separate rows — a ``PAYMENT``/``SUCCESSFUL`` row and, later, a
    ``REFUND``/``REFUNDED`` one. A plain ``history_map[code] = item`` is
    last-write-wins, so whichever of the pair happened to come last in the
    response won; when that was the PAYMENT row it evicted the REFUND row
    beside it and the refund vanished. Refund-bearing rows now win regardless
    of arrival order.
    """
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = item.get("transaction_code")
        if not code:
            continue
        current = history_map.get(code)
        if current is None or (
            history_row_shows_refund(item) and not history_row_shows_refund(current)
        ):
            history_map[code] = item
    return history_map


def refunded_amount(data: dict, history_map: Optional[dict] = None) -> Decimal:
    """Best available total refunded on this checkout or transaction history.

    Returns ``Decimal("0")`` when SumUp signals a refund by status alone and
    reports no amount — callers must treat that as "amount unknown", not as
    "nothing was refunded".
    """
    if not isinstance(data, dict):
        return Decimal("0")

    candidates = [_refunded_total(data)]

    per_tx_total = Decimal("0")
    refunds_total = Decimal("0")
    for tx in data.get("transactions") or []:
        if not isinstance(tx, dict):
            continue
        per_tx_total += _refunded_total(tx)
        for refund in tx.get("refunds") or []:
            if isinstance(refund, dict):
                refunds_total += _to_decimal(refund.get("amount"))

        tx_code = tx.get("transaction_code")
        if tx_code and history_map and tx_code in history_map:
            hist_item = history_map[tx_code]
            hist_amt_ref = _refunded_total(hist_item)
            if hist_amt_ref > 0:
                candidates.append(hist_amt_ref)
            elif (hist_item.get("status") or "").upper() == "REFUNDED":
                candidates.append(_to_decimal(hist_item.get("amount") or data.get("amount")))

    code = data.get("transaction_code")
    if code and history_map and code in history_map:
        hist_item = history_map[code]
        hist_amt_ref = _refunded_total(hist_item)
        if hist_amt_ref > 0:
            candidates.append(hist_amt_ref)
        elif (hist_item.get("status") or "").upper() == "REFUNDED":
            candidates.append(_to_decimal(hist_item.get("amount") or data.get("amount")))

    candidates.extend([per_tx_total, refunds_total])
    return max(candidates)


def is_checkout_refunded(data: dict, history_map: Optional[dict] = None) -> bool:
    """Determine if a SumUp checkout resource or transaction history indicates an external refund.

    Checks:
    - Checkout-level status == "REFUNDED"
    - Any transaction entry status == "REFUNDED"
    - Any transaction entry has non-empty "refunds" list or a refunded total > 0
    - Top-level refunded total > 0
    - Transaction history matching by transaction_code reports status == "REFUNDED",
      type == "REFUND", or a refunded total > 0 (for external dashboard/POS refunds
      where SumUp does not mutate the static /v0.1/checkouts resource).

    "Refunded total" is read under both spellings SumUp uses — ``amount_refunded``
    on the checkout resource, ``refunded_amount`` on transaction-history rows. See
    ``_REFUNDED_TOTAL_KEYS``; reading only the first is what let a fully refunded
    payment report nothing.

    Detection only. Whether the refund was full or partial is decided by the
    caller against the captured amount — see ``handle()``.
    """
    if not isinstance(data, dict):
        return False

    status = (data.get("status") or "").upper()
    if status == "REFUNDED":
        return True

    if _refunded_total(data) > 0:
        return True

    transactions = data.get("transactions") or []
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        tx_status = (tx.get("status") or "").upper()
        if tx_status == "REFUNDED":
            return True
        if tx.get("refunds") or _refunded_total(tx) > 0:
            return True
        tx_code = tx.get("transaction_code")
        if tx_code and history_map and history_row_shows_refund(history_map.get(tx_code)):
            return True

    code = data.get("transaction_code")
    if code and history_map and history_row_shows_refund(history_map.get(code)):
        return True

    return False


class Command(BaseCommand):
    help = "Reconcile recent PAID SumUp transactions against SumUp to catch external refunds."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Number of lookback days for PAID transactions (default: 30)",
        )
        parser.add_argument(
            "--include-partial",
            action="store_true",
            help=(
                "Also reconcile refunds smaller than the captured amount. Off by "
                "default: a partial refund would otherwise cancel a still-mostly-paid "
                "registration and free its seat."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the sweep and print actions without saving database changes",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Only output actionable desyncs and errors",
        )
        parser.add_argument(
            "--checkout-id",
            help="Reconcile a specific SumUp checkout ID",
        )
        parser.add_argument(
            "--reference",
            help="Reconcile a specific transaction reference",
        )
        parser.add_argument(
            "--batch-delay",
            type=float,
            default=0.05,
            help="Delay in seconds between SumUp API requests (default: 0.05s)",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        include_partial = options["include_partial"]
        quiet = options["quiet"]
        checkout_id = options.get("checkout_id")
        reference = options.get("reference")
        delay = options["batch_delay"]

        if days < 1:
            raise CommandError("--days must be at least 1")

        if checkout_id and reference:
            raise CommandError("Provide either --checkout-id or --reference, not both.")

        qs = PaymentTransaction.objects.filter(
            provider=PaymentTransaction.Provider.SUMUP,
            status=PaymentTransaction.Status.PAID,
        )

        if checkout_id:
            qs = qs.filter(sumup_checkout_id=checkout_id)
        elif reference:
            qs = qs.filter(transaction_reference=reference)
        else:
            cutoff = timezone.now() - timedelta(days=days)
            # sumup_checkout_id is CharField(blank=True) with no null=True, so
            # the column is never NULL — excluding "" is the whole filter.
            qs = qs.filter(created_at__gte=cutoff).exclude(sumup_checkout_id="")

        qs = qs.order_by("-created_at")
        total_count = qs.count()

        if not quiet:
            prefix = "[DRY RUN] " if dry_run else ""
            self.stdout.write(
                f"{prefix}Starting SumUp refund reconciliation sweep: {total_count} transactions to check."
            )

        if total_count == 0:
            if not quiet:
                self.stdout.write("No matching PAID transactions found.")
            return

        client = SumUpClient()
        history_map = {}
        try:
            # Prefetch recent merchant transaction history to catch refunds done via
            # the dashboard/POS terminal that do not mutate the static checkout resource.
            history_data = client.get_transactions_history(
                limit=100, order="descending"
            )
            index_history(history_map, history_data.get("items"))
        except Exception as exc:
            logger.warning("Could not prefetch SumUp transaction history: %s", exc)

        checked = 0
        refunded_count = 0
        errors_count = 0
        partial_count = 0

        for tx_obj in qs:
            checked += 1
            if delay > 0 and checked > 1:
                time.sleep(delay)

            try:
                remote_data = client.get_checkout(tx_obj.sumup_checkout_id)
            except (SumUpError, ValueError) as exc:
                # ValueError too: get_checkout() wraps only requests.RequestException,
                # so a 2xx with a malformed or non-JSON body (a proxy returning an
                # HTML error page, a truncated response) makes response.json() raise
                # JSONDecodeError — a ValueError, not a RequestException — which
                # would escape uncaught and kill the sweep.
                #
                # Fixed here rather than in SumUpClient because get_checkout has five
                # callers including the live payment path in views_payments; making
                # it raise SumUpError for malformed bodies is the better contract but
                # changes behaviour for callers this PR does not test.
                errors_count += 1
                logger.error(
                    "Failed to fetch or parse SumUp checkout %s: %s",
                    tx_obj.sumup_checkout_id,
                    exc,
                )
                self.stdout.write(
                    self.style.ERROR(
                        f"Error fetching checkout {tx_obj.sumup_checkout_id} ({tx_obj.transaction_reference}): {exc}"
                    )
                )
                continue

            try:
                refunded = is_checkout_refunded(remote_data, history_map=history_map)
                if not refunded:
                    # If not found in prefetched history_map, attempt fallback lookup by transaction_code
                    code = remote_data.get("transaction_code")
                    if not code:
                        tx_list = remote_data.get("transactions") or []
                        for t in tx_list:
                            if isinstance(t, dict) and t.get("transaction_code"):
                                code = t.get("transaction_code")
                                break
                    # Ask SumUp about THIS transaction whenever the prefetch
                    # holds nothing that shows a refund — not merely when the
                    # code is absent from it. The prefetch covers the most
                    # recent 100 transactions on the whole account, so a refund
                    # taken today against an older payment falls outside that
                    # window while the payment's own PAYMENT row sits inside
                    # it; guarding on ``code not in history_map`` let that
                    # stale row suppress the one lookup that would have found
                    # the refund. Costs one extra call per unrefunded row,
                    # which is the price of not silently missing money.
                    if code and not history_row_shows_refund(history_map.get(code)):
                        try:
                            code_data = client.get_transactions_history(
                                limit=10, transaction_code=code
                            )
                            index_history(history_map, code_data.get("items"))
                            refunded = is_checkout_refunded(
                                remote_data, history_map=history_map
                            )
                        except Exception as exc:
                            # Was a bare ``pass``. A provider error here means
                            # the refund check for this row did not happen, and
                            # the sweep goes on to print "still PAID" — the one
                            # outcome an operator reads as "checked, and fine".
                            logger.warning(
                                "Could not look up SumUp history for "
                                "transaction_code %s (checkout %s): %s",
                                code,
                                tx_obj.sumup_checkout_id,
                                exc,
                            )
            except Exception as exc:  # defensive: never let one payload kill the sweep
                errors_count += 1
                logger.exception(
                    "Could not interpret SumUp payload for checkout %s: %s",
                    tx_obj.sumup_checkout_id,
                    exc,
                )
                self.stdout.write(
                    self.style.ERROR(
                        f"Unreadable payload for checkout {tx_obj.sumup_checkout_id} "
                        f"({tx_obj.transaction_reference}): {exc}"
                    )
                )
                continue

            if refunded:
                # A partial refund is not a cancellation. Reconciling one would
                # unbook a still-mostly-paid seat, release it to the waitlist and
                # void the member's credit over what may be a small goodwill
                # adjustment. Amount 0 means SumUp signalled the refund by status
                # alone and told us no amount — treated as full, as before.
                refunded_amt = refunded_amount(remote_data, history_map=history_map)
                captured_amt = tx_obj.amount or Decimal("0")
                is_partial = (
                    refunded_amt > 0
                    and captured_amt > 0
                    and refunded_amt < captured_amt
                )

                if is_partial and not include_partial:
                    partial_count += 1
                    logger.warning(
                        "PARTIAL refund on %s (checkout %s): %s of %s refunded. "
                        "Left unreconciled for manual review — rerun with "
                        "--include-partial to force it.",
                        tx_obj.transaction_reference,
                        tx_obj.sumup_checkout_id,
                        refunded_amt,
                        captured_amt,
                    )
                    self.stdout.write(
                        self.style.WARNING(
                            f"⚠ PARTIAL refund {refunded_amt}/{captured_amt} on "
                            f"{tx_obj.transaction_reference} — needs manual review, not reconciled."
                        )
                    )
                    continue

                # The write path needs the same boundary as the read path.
                # _reconcile_refunded writes across four models and calls
                # void_credit(), which opens with an unguarded .get() — a
                # DoesNotExist, IntegrityError or deadlock would otherwise
                # propagate out of this loop and kill the run. Worse, the
                # queryset is ordered with no per-run offset, so one poisoned
                # row would abort every future sweep at the same place.
                try:
                    self._reconcile_refunded(tx_obj, remote_data, dry_run=dry_run)
                except Exception as exc:
                    errors_count += 1
                    logger.exception(
                        "Failed to reconcile refund for %s (checkout %s): %s",
                        tx_obj.transaction_reference,
                        tx_obj.sumup_checkout_id,
                        exc,
                    )
                    self.stdout.write(
                        self.style.ERROR(
                            f"Error reconciling {tx_obj.transaction_reference} "
                            f"({tx_obj.sumup_checkout_id}): {exc}"
                        )
                    )
                    continue

                refunded_count += 1
            elif not quiet:
                self.stdout.write(
                    f"✓ {tx_obj.transaction_reference} ({tx_obj.sumup_checkout_id}): still PAID"
                )

        summary_msg = (
            f"Sweep complete: {checked} checked, {refunded_count} external refund(s) reconciled, "
            f"{partial_count} partial refund(s) flagged for manual review, "
            f"{errors_count} error(s)."
        )
        if dry_run:
            summary_msg = f"[DRY RUN] {summary_msg}"

        if refunded_count > 0:
            self.stdout.write(self.style.SUCCESS(summary_msg))
        elif not quiet:
            self.stdout.write(summary_msg)

    def _reconcile_refunded(self, tx_obj, remote_data, dry_run=False):
        """Apply external refund adjustments across PaymentTransaction, EventRegistration,
        CrushCredit, and PremiumMembership under atomic lock order."""
        ref = tx_obj.transaction_reference
        cid = tx_obj.sumup_checkout_id

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] External refund detected on {ref} (checkout {cid}). Would reconcile to REFUNDED."
                )
            )
            return

        # LOCK ORDER: PaymentTransaction FIRST, then EventRegistration / CrushProfile / CrushCredit
        with transaction.atomic():
            locked_tx = (
                PaymentTransaction.objects.select_for_update()
                .filter(pk=tx_obj.pk)
                .first()
            )
            if not locked_tx or locked_tx.status != PaymentTransaction.Status.PAID:
                logger.info(
                    "Skipping reconciliation for %s — status is already %s",
                    ref,
                    locked_tx.status if locked_tx else "None",
                )
                return

            locked_tx.status = PaymentTransaction.Status.REFUNDED
            locked_tx.raw_response = remote_data
            locked_tx.failure_reason = (
                "External refund detected and reconciled by background sweep."
            )
            locked_tx.save(
                update_fields=["status", "raw_response", "failure_reason", "updated_at"]
            )

            # 1. Reconcile EventRegistration
            if locked_tx.event_registration_id:
                reg = (
                    EventRegistration.objects.select_for_update()
                    .filter(pk=locked_tx.event_registration_id)
                    .first()
                )
                if reg:
                    reg.payment_confirmed = False
                    reg.payment_date = None
                    update_fields = ["payment_confirmed", "payment_date"]

                    # If the registration was confirmed and hasn't attended yet, cancel it.
                    # Saving status='cancelled' invokes promote_waitlist_on_cancellation automatically.
                    if reg.status == "confirmed":
                        reg.status = "cancelled"
                        update_fields.append("status")

                    reg.save(update_fields=update_fields)
                    logger.info(
                        "Reconciled event registration %s to payment_confirmed=False (status=%s) after external refund.",
                        reg.pk,
                        reg.status,
                    )

            # 2. Reconcile PremiumMembership
            if locked_tx.premium_membership_id:
                pm = (
                    PremiumMembership.objects.select_for_update()
                    .filter(pk=locked_tx.premium_membership_id)
                    .first()
                )
                if pm:
                    # `cancel_active` owns the active case: it re-checks status
                    # under the row lock and hands back the coach that confirm()
                    # assigned — the invariant used to be spelled out here, which
                    # is how it came to be written by hand at all. It clears the
                    # FK only when *this* membership is what set it, so a coach
                    # earned by attending (signals.py) or backfilled by 0150 is
                    # left alone. Lock order stays PaymentTransaction (held
                    # above) → PremiumMembership → CrushProfile.
                    #
                    # A membership still `pending` never assigned a coach, so
                    # there is nothing to unwind and only the payment fields are
                    # cleared. `cancel_active` reports that by returning False.
                    if not pm.cancel_active(
                        reason=f"external refund reconciled from SumUp {ref}"
                    ):
                        pm.status = "cancelled"
                        pm.payment_confirmed = False
                        pm.payment_date = None
                        pm.save(
                            update_fields=[
                                "status",
                                "payment_confirmed",
                                "payment_date",
                            ]
                        )
                    logger.info(
                        "Reconciled premium membership %s to cancelled after external refund.",
                        pm.pk,
                    )

            # 3. Reconcile linked CrushCredits (to prevent double-dip of cash refund + active credit)
            #
            # The registration clause is deliberately narrowed to credits with no
            # payment of their own. EventRegistration rows are REUSED across
            # re-registration cycles (see EventRegistration.save()), so matching on
            # registration alone would also catch a still-active credit issued in an
            # earlier cycle against a different payment — silently destroying credit
            # the member is genuinely owed. A credit naming its own source_payment
            # is only ours when that payment is the one being refunded.
            credit_filters = Q(source_payment=locked_tx)
            if locked_tx.event_registration_id:
                credit_filters |= Q(
                    source_registration_id=locked_tx.event_registration_id,
                    source_payment__isnull=True,
                )

            # Model instances, not values_list: redeemed_cents is a computed
            # property over the redemption rows, not a column.
            linked_credits = list(
                CrushCredit.objects.filter(credit_filters).filter(
                    status=CrushCredit.Status.ACTIVE
                )
            )
            for linked in linked_credits:
                # Voiding goes through services.credits.void_credit(), the single
                # guarded door that module's docstring requires, rather than a
                # second hand-rolled copy of the same lifecycle that can drift.
                # require_unspent=False: a cash refund supersedes a partly spent
                # credit, and the note records that it was already drawn on.
                credit, outcome = void_credit(
                    linked.pk,
                    note="Voided: External SumUp cash refund reconciled.",
                    require_unspent=False,
                )
                if outcome != "voided":
                    logger.info(
                        "CrushCredit #%s not voided (%s) during reconciliation of %s.",
                        linked.pk,
                        outcome,
                        ref,
                    )
                    continue

                # Read the redemption total off the row void_credit LOCKED, not
                # off the unlocked snapshot the candidate query returned. A
                # concurrent redemption between those two points would otherwise
                # write a wrong number onto an append-only ledger row.
                redeemed_cents = credit.redeemed_cents
                if redeemed_cents:
                    credit.note = (
                        f"{credit.note}\nWARNING: {redeemed_cents} cents already "
                        "redeemed before this void."
                    ).strip()
                    credit.save(update_fields=["note"])
                    logger.warning(
                        "CrushCredit #%s had %s cents already redeemed when external cash refund on payment %s was reconciled!",
                        credit.pk,
                        redeemed_cents,
                        ref,
                    )
                else:
                    logger.info(
                        "Voided unused CrushCredit #%s following external cash refund on payment %s.",
                        credit.pk,
                        ref,
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled external refund for {ref} (checkout {cid}) -> status=REFUNDED"
            )
        )
