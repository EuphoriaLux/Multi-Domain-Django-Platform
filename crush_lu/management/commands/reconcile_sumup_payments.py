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
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import EventRegistration
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import PremiumMembership
from crush_lu.services.credits import void_credit
from crush_lu.services.sumup import SumUpClient, SumUpError

logger = logging.getLogger(__name__)


def _send_refund_after_cancellation_notice_safely(registration_id, withdrawn_cents):
    """Email a member whose ALREADY-cancelled seat was refunded in cash.

    Runs on commit. Never raises: the refund is already durable, and a mail
    failure must not turn a reconciled row into a sweep error.
    """
    try:
        from crush_lu.email_helpers import send_refund_after_cancellation_notice

        registration = (
            EventRegistration.objects.select_related("event", "user")
            .filter(pk=registration_id)
            .first()
        )
        if registration is None:
            return
        send_refund_after_cancellation_notice(registration, withdrawn_cents)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to send refund-after-cancellation email for registration %s: %s",
            registration_id,
            type(exc).__name__,
        )


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

# SumUpClient.get_transactions_history clamps ``limit`` to 100.
_HISTORY_PREFETCH_LIMIT = 100

# Truthy outcomes of Command._reconcile_refunded.
RECONCILED = "reconciled"
SUPERSEDED = "superseded"


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


def _history_refund_rank(item) -> int:
    """How informative one history row is about a refund. Higher wins.

    2 — states an explicit refunded TOTAL. SumUp keeps a cumulative
        ``refunded_amount`` on the PAYMENT row, and that total is the only
        thing that can tell a fully refunded payment from a partly refunded
        one when the refund was taken in several goes.
    1 — shows that a refund happened, but not how much in total. A ``REFUND``
        row states only its OWN amount, so three partial refunds adding up to
        the full capture look like one small partial.
    0 — says nothing about a refund.

    Ranking rather than a plain "is it a refund row?" preference because both
    directions lose money: keep the bare PAYMENT row and the refund is
    invisible; keep the bare REFUND row over a PAYMENT row carrying the
    cumulative total and ``refunded_amount()`` under-reads, so the
    partial-refund guard parks a fully refunded payment as "needs manual
    review" and it is never reconciled.
    """
    if not isinstance(item, dict):
        return 0
    if _refunded_total(item) > 0:
        return 2
    if history_row_shows_refund(item):
        return 1
    return 0


def index_history(history_map: dict, items) -> dict:
    """Index history rows by ``transaction_code``, keeping the most informative.

    A payment and the refund taken against it SHARE a transaction_code and come
    back as two separate rows — a ``PAYMENT``/``SUCCESSFUL`` row and, later, a
    ``REFUND``/``REFUNDED`` one. A plain ``history_map[code] = item`` is
    last-write-wins, so whichever of the pair happened to come last in the
    response won; when that was the PAYMENT row it evicted the REFUND row
    beside it and the refund vanished. Rows are now kept by
    ``_history_refund_rank`` instead, which is order-independent in both
    directions — see that function for why "prefer the REFUND row" is not
    enough on its own.
    """
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = item.get("transaction_code")
        if not code:
            continue
        if code not in history_map or _history_refund_rank(
            item
        ) > _history_refund_rank(history_map[code]):
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


def refund_history_evidence(data: dict, history_map: Optional[dict]) -> list:
    """The history rows that show a refund for this checkout's transaction codes.

    Kept with the reconciled payment: when the checkout resource still says
    PAID (a dashboard/terminal refund never mutates it), these rows are the
    only proof of the refund, and storing the checkout alone would lose it.
    """
    if not isinstance(data, dict) or not history_map:
        return []
    codes = []
    if data.get("transaction_code"):
        codes.append(data["transaction_code"])
    for tx in data.get("transactions") or []:
        if isinstance(tx, dict) and tx.get("transaction_code"):
            codes.append(tx["transaction_code"])
    evidence = []
    for code in dict.fromkeys(codes):
        item = history_map.get(code)
        if history_row_shows_refund(item):
            evidence.append(item)
    return evidence


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
        # handle() must not return the counters: BaseCommand.execute() writes
        # any truthy return value to stdout and expects a string. The sweep
        # body lives in run_sweep() so the /api/admin/sumup-reconciliation/
        # endpoint can read the counters without parsing stdout
        # (contract: ai-memory-hub/policies/sumup-tier2-refund-automation-contract.md §6.5).
        self.run_sweep(
            days=options["days"],
            dry_run=options["dry_run"],
            include_partial=options["include_partial"],
            quiet=options["quiet"],
            checkout_id=options.get("checkout_id"),
            reference=options.get("reference"),
            batch_delay=options["batch_delay"],
        )

    def run_sweep(
        self,
        *,
        days=30,
        dry_run=False,
        include_partial=False,
        quiet=False,
        checkout_id=None,
        reference=None,
        batch_delay=0.05,
        budget_seconds=None,
        read_reserve_seconds=0.0,
        write_reserve_seconds=0.0,
        max_writes=None,
        oldest_first=False,
    ) -> dict:
        """Run one sweep and return its counters.

        Returns ``{"in_window", "checked", "reconciled", "refunded_superseded",
        "partial", "errors", "unchecked"}``. ``unchecked`` is only ever
        non-zero when ``budget_seconds`` or ``max_writes`` is given: the CLI
        passes neither; the scheduled endpoint does, so the request cannot run
        into the App Service front end's ~230 s cap. Rows left unchecked stay
        PAID and untouched.

        The window is on when the member PAID (``paid_at``, falling back to
        ``created_at`` for rows from before it was stamped), not on when the
        checkout was opened.

        ``oldest_first`` (the endpoint) walks the window oldest-first, so a
        run cut short leaves the NEWEST rows for later — they stay in the
        window for weeks — rather than the oldest ones, which are about to age
        out. The CLI keeps newest-first.

        ``budget_seconds`` is a hard deadline for the whole sweep, including
        what a write sets off after it commits. Two reserves keep it one:

        * ``read_reserve_seconds`` — worst case to read one row from SumUp.
          A row is only started if its reads can finish before the deadline.
        * ``write_reserve_seconds`` — worst case for one reconciliation,
          including its ``on_commit`` work (a cancelled registration promotes
          the waitlist and emails synchronously after commit). A detected
          full refund is only written if that can finish before the deadline;
          otherwise the row is left PAID, counted ``unchecked``, and the sweep
          stops — never a committed promotion whose email the caller's
          timeout then cuts off.

        ``max_writes`` stops the sweep after that many refund writes (the CLI
        passes None: unlimited). The scheduled endpoint allows ONE, because a
        write's post-commit chain can be long (see api_admin_sumup); the rest
        of the window is counted ``unchecked`` and read by the next run.
        """
        delay = batch_delay
        started = time.monotonic()

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
            qs = (
                qs.annotate(paid_or_created=Coalesce(F("paid_at"), F("created_at")))
                .filter(paid_or_created__gte=cutoff)
                .exclude(sumup_checkout_id="")
            )

        if oldest_first:
            qs = qs.order_by(Coalesce(F("paid_at"), F("created_at")).asc(), "pk")
        else:
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
            return {
                "in_window": 0,
                "checked": 0,
                "reconciled": 0,
                "refunded_superseded": 0,
                "partial": 0,
                "errors": 0,
                "unchecked": 0,
            }

        client = SumUpClient()
        history_map = {}
        try:
            # Prefetch recent merchant transaction history to catch refunds done via
            # the dashboard/POS terminal that do not mutate the static checkout resource.
            history_data = client.get_transactions_history(
                limit=_HISTORY_PREFETCH_LIMIT, order="descending"
            )
            prefetched = history_data.get("items") or []
            index_history(history_map, prefetched)
            # The client caps this at 100 (sumup.py). A full page means the
            # prefetch no longer reaches back over the whole lookback window,
            # so refunds on older payments rely entirely on the per-row
            # lookup below — say so, rather than degrade silently (contract §6.4).
            if len(prefetched) >= _HISTORY_PREFETCH_LIMIT:
                logger.warning(
                    "SumUp history prefetch returned a full page (%s items): "
                    "it no longer covers the whole %s-day window, so external "
                    "refunds on older payments are found only by the "
                    "per-transaction lookup.",
                    len(prefetched),
                    days,
                )
        except Exception as exc:
            logger.warning("Could not prefetch SumUp transaction history: %s", exc)

        checked = 0
        refunded_count = 0
        errors_count = 0
        partial_count = 0
        superseded_count = 0
        unchecked = 0

        def _over_budget(reserve):
            return (
                budget_seconds is not None
                and time.monotonic() - started + reserve >= budget_seconds
            )

        for tx_obj in qs:
            if _over_budget(read_reserve_seconds):
                unchecked = total_count - checked
                logger.warning(
                    "SumUp reconciliation stopped at its %ss budget: checked %s "
                    "of %s transaction(s) in the window; %s left unchecked "
                    "this run.",
                    budget_seconds,
                    checked,
                    total_count,
                    unchecked,
                )
                break
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

            history_lookup_failed = False
            try:
                code = remote_data.get("transaction_code")
                if not code:
                    tx_list = remote_data.get("transactions") or []
                    for t in tx_list:
                        if isinstance(t, dict) and t.get("transaction_code"):
                            code = t.get("transaction_code")
                            break

                # Gather the evidence BEFORE classifying, and ask SumUp about
                # this transaction unless the prefetch already holds a row
                # stating a CUMULATIVE refunded total for it.
                #
                # Three gates have stood here, each too weak in its own way,
                # and each hid money:
                #
                #   ``code not in history_map`` — the prefetch covers the
                #   account's most recent 100 transactions, so a refund taken
                #   today against an older payment falls outside it while the
                #   payment's own PAYMENT row sits inside. The stale row was
                #   present, so the one lookup that would have found the refund
                #   never ran and the sweep reported "still PAID".
                #
                #   ``not history_row_shows_refund(...)`` — a bare REFUND row
                #   then suppressed the lookup instead. That row states only
                #   its OWN amount, so a capture refunded in several goes reads
                #   as one small partial, and the guard below parks a fully
                #   refunded payment as "needs manual review" while quoting a
                #   figure that is not what was refunded.
                #
                # Only a rank-2 row (an explicit cumulative total) can answer
                # "how much in total", so anything less is worth the call. In
                # practice that is the same set of rows as the previous gate
                # plus refund-bearing ones, which are rare.
                #
                # This also had to move OUT of ``if not refunded`` — a
                # refund-bearing row short-circuits detection to True, which is
                # exactly when the amount still needs establishing.
                if code and _history_refund_rank(history_map.get(code)) < 2:
                    # ``--batch-delay`` promises a pause "between SumUp API
                    # requests", and this is the SECOND request for this row.
                    # Without a sleep here the ordinary unrefunded case — now
                    # the common path — fires two back-to-back calls that no
                    # value of the setting can throttle, which is how a long
                    # sweep earns a rate limit and reconciles only part of its
                    # window.
                    if delay > 0:
                        time.sleep(delay)
                    try:
                        code_data = client.get_transactions_history(
                            limit=10, transaction_code=code
                        )
                        index_history(history_map, code_data.get("items"))

                        # Belt and braces: if SumUp answers with refund rows
                        # but none carrying a cumulative total, add the
                        # individual refunds up. Only reachable when no rank-2
                        # row exists, so a cumulative figure and the refunds
                        # composing it can never be counted twice.
                        if _history_refund_rank(history_map.get(code)) < 2:
                            summed = sum(
                                (
                                    _to_decimal(item.get("amount"))
                                    for item in (code_data.get("items") or [])
                                    if isinstance(item, dict)
                                    and item.get("transaction_code") == code
                                    and (item.get("type") or "").upper() == "REFUND"
                                ),
                                Decimal("0"),
                            )
                            if summed > 0:
                                history_map[code] = dict(
                                    history_map.get(code)
                                    or {"transaction_code": code},
                                    refunded_amount=str(summed),
                                )
                    except Exception as exc:
                        # Was a bare ``pass``. Logging it is not enough on its
                        # own: the row still fell through to the "still PAID"
                        # line below, and the console handler only emits ERROR
                        # in production, so the operator saw a clean tick for a
                        # check that never ran. The flag makes it an error and
                        # skips that line.
                        history_lookup_failed = True
                        logger.warning(
                            "Could not look up SumUp history for "
                            "transaction_code %s (checkout %s): %s",
                            code,
                            tx_obj.sumup_checkout_id,
                            exc,
                        )

                refunded = is_checkout_refunded(remote_data, history_map=history_map)
                # Sized here rather than below so the blocking decision and the
                # partial-refund guard read the same numbers.
                refunded_amt = (
                    refunded_amount(remote_data, history_map=history_map)
                    if refunded
                    else Decimal("0")
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

            captured_amt = tx_obj.amount or Decimal("0")

            # A failed history lookup blocks only when what is already in hand
            # cannot classify the row safely. When the CHECKOUT resource itself
            # establishes both the refund AND a total covering the capture,
            # nothing the history could have added would change the answer —
            # further refunds cannot make a full refund less than full — so
            # refusing there would strand a plainly refunded payment on a
            # transient provider blip. Introduced when the lookup moved out of
            # ``if not refunded``: before that, a checkout-confirmed refund
            # never reached this branch at all.
            #
            # Deliberately narrow. A refund SumUp reports by status alone, with
            # no amount, does NOT qualify: the established rule that an unknown
            # amount counts as full rests on having asked and been told
            # nothing, which is not the same as having been unable to ask, and
            # reconciling a partial as full unbooks a still-mostly-paid seat
            # and voids the member's credit.
            evidence_is_sufficient = (
                refunded and captured_amt > 0 and refunded_amt >= captured_amt
            )
            if history_lookup_failed and not evidence_is_sufficient:
                # The refund check for this row DID NOT HAPPEN. Reporting it as
                # "still PAID" is precisely the failure this command exists to
                # prevent — an unchecked row that reads as verified — so it is
                # counted as an error and named as unknown. Printed even under
                # --quiet, which is for actionable desyncs and errors.
                errors_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"Could not verify {tx_obj.transaction_reference} "
                        f"({tx_obj.sumup_checkout_id}): SumUp history "
                        "unreachable — refund state and amount UNKNOWN, "
                        "NOT confirmed paid."
                    )
                )
                continue

            if refunded:
                # A partial refund is not a cancellation. Reconciling one would
                # unbook a still-mostly-paid seat, release it to the waitlist and
                # void the member's credit over what may be a small goodwill
                # adjustment. Amount 0 means SumUp signalled the refund by status
                # alone and told us no amount — treated as full, as before.
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
                if not dry_run and _over_budget(write_reserve_seconds):
                    # Not enough time left to commit this refund AND finish
                    # what the commit sets off. Leave it PAID for the next run
                    # and stop: a later row could only hit the same wall.
                    checked -= 1
                    unchecked = total_count - checked
                    logger.warning(
                        "SumUp reconciliation deferred a detected refund on "
                        "payment %s: too little of the %ss budget left to "
                        "write it safely. Checked %s of %s transaction(s) in "
                        "the window; %s left unchecked this run.",
                        tx_obj.pk,
                        budget_seconds,
                        checked,
                        total_count,
                        unchecked,
                    )
                    break

                write_attempted = not dry_run
                try:
                    evidence = refund_history_evidence(remote_data, history_map)
                    kwargs = {"dry_run": dry_run}
                    if evidence:
                        kwargs["history_evidence"] = evidence
                    transitioned = self._reconcile_refunded(
                        tx_obj, remote_data, **kwargs
                    )
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
                    # An exception can come from an on_commit callback AFTER
                    # the write committed, so a raised write still used up
                    # this run's write allowance.
                    if max_writes is not None and write_attempted:
                        unchecked = total_count - checked
                        break
                    continue

                # False when an overlapping run got there first: it waited on
                # the row lock, found the row no longer PAID and did nothing.
                if transitioned:
                    if transitioned == SUPERSEDED:
                        superseded_count += 1
                    else:
                        refunded_count += 1
                    writes = refunded_count + superseded_count
                    if (
                        max_writes is not None
                        and write_attempted
                        and writes >= max_writes
                    ):
                        unchecked = total_count - checked
                        if unchecked:
                            logger.warning(
                                "SumUp reconciliation stopped after %s refund "
                                "write(s), its per-run limit: checked %s of %s "
                                "transaction(s) in the window; %s left "
                                "unchecked this run.",
                                writes,
                                checked,
                                total_count,
                                unchecked,
                            )
                        break
            elif not quiet:
                self.stdout.write(
                    f"✓ {tx_obj.transaction_reference} ({tx_obj.sumup_checkout_id}): still PAID"
                )

        summary_msg = (
            f"Sweep complete: {checked} checked, {refunded_count} external refund(s) reconciled, "
            f"{partial_count} partial refund(s) flagged for manual review, "
            f"{errors_count} error(s)."
        )
        if superseded_count:
            summary_msg += (
                f" {superseded_count} superseded payment(s) marked refunded; "
                "their registration kept (another payment covers it)."
            )
        if dry_run:
            summary_msg = f"[DRY RUN] {summary_msg}"

        if refunded_count > 0 or superseded_count > 0:
            self.stdout.write(self.style.SUCCESS(summary_msg))
        elif not quiet:
            self.stdout.write(summary_msg)

        return {
            "in_window": total_count,
            "checked": checked,
            "reconciled": refunded_count,
            "refunded_superseded": superseded_count,
            "partial": partial_count,
            "errors": errors_count,
            "unchecked": unchecked,
        }

    @staticmethod
    def _other_paid_payment_ids(tx):
        """Other PAID payments funding the same registration or membership.

        EventRegistration rows are reused on re-registration (views_events
        ``_admitted_status``), so a member who cancelled, re-registered and
        paid again has TWO PaymentTransactions on one registration. Refunding
        the old one must not cancel the seat the new one paid for.
        """
        others = PaymentTransaction.objects.filter(
            status=PaymentTransaction.Status.PAID
        ).exclude(pk=tx.pk)
        if tx.event_registration_id:
            return list(
                others.filter(event_registration_id=tx.event_registration_id)
                .values_list("pk", flat=True)
            )
        if tx.premium_membership_id:
            return list(
                others.filter(premium_membership_id=tx.premium_membership_id)
                .values_list("pk", flat=True)
            )
        return []

    def _reconcile_refunded(
        self, tx_obj, remote_data, dry_run=False, history_evidence=None
    ):
        """Apply external refund adjustments across PaymentTransaction, EventRegistration,
        CrushCredit, and PremiumMembership under atomic lock order.

        Returns a truthy outcome when this call moved the row PAID -> REFUNDED
        (or, in a dry run, would have): ``RECONCILED``, or ``SUPERSEDED`` when
        another PAID payment still funds the same registration/membership —
        then only this payment and the credit sourced FROM it change, the
        seat/membership is left alone. Returns False when the row was no
        longer PAID under the lock — an overlapping run already reconciled it.

        ``history_evidence`` — the transaction-history rows that proved the
        refund — is stored beside the checkout payload in ``raw_response``,
        because a dashboard refund leaves the checkout itself saying PAID."""
        ref = tx_obj.transaction_reference
        cid = tx_obj.sumup_checkout_id

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] External refund detected on {ref} (checkout {cid}). Would reconcile to REFUNDED."
                )
            )
            return SUPERSEDED if self._other_paid_payment_ids(tx_obj) else RECONCILED

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
                return False

            # Take the registration / membership lock BEFORE asking whether
            # another payment funds it (same order as below: payment first).
            # A capture of another payment locks that registration too
            # (_apply_paid_checkout: payment -> event -> registration), so once
            # we hold it, any racing capture has either committed — and is
            # seen here as PAID — or waits until this write is done.
            if locked_tx.event_registration_id:
                EventRegistration.objects.select_for_update().filter(
                    pk=locked_tx.event_registration_id
                ).first()
            elif locked_tx.premium_membership_id:
                PremiumMembership.objects.select_for_update().filter(
                    pk=locked_tx.premium_membership_id
                ).first()
            superseding_ids = self._other_paid_payment_ids(locked_tx)

            locked_tx.status = PaymentTransaction.Status.REFUNDED
            if history_evidence and isinstance(remote_data, dict):
                # A dashboard/terminal refund leaves the checkout saying PAID;
                # the history rows are the proof. Keep both. Readers of
                # raw_response only look at "transactions"/"redemptions", so an
                # extra top-level key changes nothing for them.
                locked_tx.raw_response = {
                    **remote_data,
                    "reconciliation_history_evidence": list(history_evidence),
                }
            else:
                locked_tx.raw_response = remote_data
            locked_tx.failure_reason = (
                "External refund detected and reconciled by background sweep."
            )
            locked_tx.save(
                update_fields=["status", "raw_response", "failure_reason", "updated_at"]
            )

            # Set when the refunded seat was ALREADY cancelled before this
            # write. The cancellation signal stays silent then (its
            # _previous_status == "cancelled" guard), so this path owes the
            # member the only email about the refund — see the end of this
            # method.
            already_cancelled_reg_id = None
            withdrawn_cents = 0

            if superseding_ids:
                # Another PAID payment funds this seat/membership: leave it
                # alone. Only the credit sourced from THIS payment follows the
                # usual rule (cash back and credit still spendable would be a
                # double-dip); credit sourced from the other payment, or
                # matched only by registration, is not touched.
                logger.warning(
                    "SumUp refund of superseded payment %s: registration %s / "
                    "membership %s kept — still funded by payment(s) %s.",
                    locked_tx.pk,
                    locked_tx.event_registration_id,
                    locked_tx.premium_membership_id,
                    superseding_ids,
                )

            # 1. Reconcile EventRegistration
            if locked_tx.event_registration_id and not superseding_ids:
                reg = (
                    EventRegistration.objects.select_for_update()
                    .filter(pk=locked_tx.event_registration_id)
                    .first()
                )
                if reg:
                    reg.payment_confirmed = False
                    reg.payment_date = None
                    update_fields = ["payment_confirmed", "payment_date"]
                    # Read by the cancellation signal's member email: without
                    # it the member is told "No payment was recorded … no
                    # Crush Credit is due", which is false — they paid and
                    # were refunded in cash.
                    reg._external_cash_refund = True
                    if reg.status == "cancelled":
                        already_cancelled_reg_id = reg.pk

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
            if locked_tx.premium_membership_id and not superseding_ids:
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
            if locked_tx.event_registration_id and not superseding_ids:
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
                # What the member could still spend and now cannot.
                withdrawn_cents += max(0, credit.amount_cents - redeemed_cents)
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

            # A seat cancelled by THIS write is announced by the cancellation
            # signal (with the cash-refund wording). A seat that was already
            # cancelled gets no signal email at all, yet the member can see
            # the change — the payment is now refunded and any credit issued
            # for that cancellation is gone — so tell them, once.
            # A superseded payment's refund voids credit the member can see
            # while their current seat stays put; tell them about the credit,
            # not about the seat. No credit touched: nothing they can see
            # changed beyond the card refund, and a mail saying "payment
            # refunded" beside a live seat would only alarm them.
            if (
                superseding_ids
                and withdrawn_cents > 0
                and locked_tx.event_registration_id
            ):
                already_cancelled_reg_id = locked_tx.event_registration_id

            if already_cancelled_reg_id is not None:
                transaction.on_commit(
                    lambda reg_id=already_cancelled_reg_id, cents=withdrawn_cents: (
                        _send_refund_after_cancellation_notice_safely(reg_id, cents)
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled external refund for {ref} (checkout {cid}) -> status=REFUNDED"
            )
        )
        return SUPERSEDED if superseding_ids else RECONCILED
