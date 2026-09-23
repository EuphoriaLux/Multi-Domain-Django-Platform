"""
Admin API endpoint for the SumUp Tier-2 refund reconciliation sweep.

Invoked daily by the ``SumUpReconciliation`` Azure Function timer in
``azure-functions/hybrid-maintenance/``. Same thin-wrapper shape as
``api_admin_campaigns``:

- Bearer token matching ``settings.ADMIN_API_KEY`` (``crush_lu.api_admin_auth``).
- Outside ``i18n_patterns`` so the Function hits a stable, unprefixed
  ``/api/admin/sumup-reconciliation/`` path.
- Gated by ``settings.SUMUP_RECONCILIATION_ENABLED`` (default off): when off it
  answers ``200 {"skipped": true, ...}`` and runs nothing.

It runs ``reconcile_sumup_payments``' sweep inline with the command's defaults
(30-day window, partial refunds left for a human, no dry run) plus a wall-clock
budget, and reports the sweep's counters. It OBSERVES refunds a human already
took in the SumUp dashboard or on a terminal; it never issues one. Only the
read-side client calls are reachable from here, and a structural test pins
that.

Contract: ai-memory-hub/policies/sumup-tier2-refund-automation-contract.md
"""

from __future__ import annotations

import logging
from io import StringIO

from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from crush_lu.api_admin_auth import (
    authenticate_admin_request as _authenticate_admin_request,
)
from crush_lu.api_admin_auth import unauthorized as _unauthorized

logger = logging.getLogger(__name__)

# Contract §6.2: the window filters on when the member PAID, not on when the
# refund happened, so it must stay at the command's 30-day default.
RECONCILIATION_DAYS = 30

# Time budget (contract §6.3). The request must end before the
# SumUpReconciliation Function's 110 s HTTP timeout (function_app.py), itself
# under gunicorn's --timeout 120 (startup.sh). Nothing retries a request cut off
# mid-flight.
#
# Reads: SumUpClient.get_checkout / get_transactions_history, timeout=10 each
# (crush_lu/services/sumup.py); a row makes at most two.
#
# Writes: ONE per invocation (MAX_WRITES_PER_RUN). Reconciling a confirmed
# registration saves it as `cancelled`, and every callback below then runs
# SYNCHRONOUSLY on commit, inside this request. R = the refunded member,
# P = the waitlisted member promoted into the freed seat; N = Apple Wallet
# devices registered for one serial.
#
#   callback                                            source                              worst case
#   promote_waitlist_on_cancellation -> R's email       signals.py:3722 -> views_payments   30 (graph_email_backend.py:130)
#   promote_waitlist_on_cancellation -> P's seat email  signals.py:3871                     30
#   R member pass, Google: token + PATCH                signals.py:3926 -> :185             30 + 30 (google_api.py:31, :385)
#   R member pass, Apple: APNs, no deadline passed      signals.py:3926 -> :127             10 x N (passkit_apns.py:128)
#   R event ticket, Google expire: token + PATCH        signals.py:3982                     30 + 30 (google_event_ticket_api.py:248)
#   R event ticket, Apple: APNs, no deadline passed     signals.py:4086                     10 x N
#   P's candidate.save() (views_events.py:303): member pass Google + Apple,
#     event ticket Apple (validity flips)               signals.py:3926 / :4086             60 + 20 x N
#   curated speed-dating event whose group degrades:
#     repair_degraded_event_groups (SumUp checkout
#     closes, remedy emails per payer)                  signals.py:3905                     unbounded
#   premium row instead: cancel_active -> profile.save
#     -> stale Outlook contact delete (prod only)       signals.py:4338                     ~25 (graph_contacts.py:78, :82)
#
# Bounded part for an event refund: 240 s, plus 40 s per Apple device — 280 s
# with one device each. THAT IS OVER THE 100 s DEADLINE ON ITS OWN, so no
# write threshold can make it safe. What is done instead:
#   * at most one write per run, so a slow tail can only ever hit one refund;
#   * the write is started only while the two emails (the part that fires for
#     every member, wallet or not) still fit: WRITE_RESERVE_SECONDS.
# Residual risk, accepted: if the wallet calls all hang to their timeouts, the
# request is cut off mid-tail. The refund itself is committed and durable;
# each callback catches its own errors; Apple passes recover on Wallet's next
# poll (the update tag is advanced first); a Google ticket left `active` and
# an unsent email are NOT retried. The numbers are pinned by a test.
FUNCTION_TIMEOUT_SECONDS = 110
DEADLINE_MARGIN_SECONDS = 10
SUMUP_READ_TIMEOUT_SECONDS = 10
SUMUP_READS_PER_ROW = 2
GRAPH_SEND_TIMEOUT_SECONDS = 30
EMAILS_PER_RECONCILED_ROW = 2
WRITE_MARGIN_SECONDS = 5  # locks, row writes, credit void, MSAL token
MAX_WRITES_PER_RUN = 1

GOOGLE_WALLET_TIMEOUT_SECONDS = 30  # token and PATCH alike
APNS_TIMEOUT_SECONDS = 10  # per device
# 240 s: emails 2x30 + R pass (30+30) + R ticket (30+30) + P pass (30+30).
POST_COMMIT_BOUNDED_WORST_SECONDS = (
    GRAPH_SEND_TIMEOUT_SECONDS * EMAILS_PER_RECONCILED_ROW
    + GOOGLE_WALLET_TIMEOUT_SECONDS * 2 * 3
)
# 40 s per device: R pass, R ticket, P pass, P ticket.
POST_COMMIT_APNS_PER_DEVICE_SECONDS = APNS_TIMEOUT_SECONDS * 4

# 100 s: the hard deadline for the sweep.
RECONCILIATION_BUDGET_SECONDS = FUNCTION_TIMEOUT_SECONDS - DEADLINE_MARGIN_SECONDS
# 21 s: a row is started only while elapsed < 79 s.
READ_RESERVE_SECONDS = SUMUP_READ_TIMEOUT_SECONDS * SUMUP_READS_PER_ROW + 1
# 65 s: the one write is started only while elapsed < 35 s.
WRITE_RESERVE_SECONDS = (
    GRAPH_SEND_TIMEOUT_SECONDS * EMAILS_PER_RECONCILED_ROW + WRITE_MARGIN_SECONDS
)

_FLAG = "SUMUP_RECONCILIATION_ENABLED"

COUNTER_KEYS = (
    "in_window",
    "checked",
    "reconciled",
    "refunded_superseded",
    "partial",
    "errors",
    "unchecked",
)

# Resume cursor (Codex 4080651811). Bounded oldest-first runs that leave the
# rows they read PAID would otherwise re-read the same oldest rows every
# night. The cursor is the (paid-or-created moment, pk) of the last row READ;
# the next run starts strictly after it, and a run that reaches the end of the
# window clears it so the following one wraps to the oldest again. Kept in the
# Django cache (Redis in production), so it needs no migration; losing it only
# means one run starts from the oldest row — never a failed run.
CURSOR_CACHE_KEY = "sumup_reconcile:cursor"
CURSOR_TTL_SECONDS = 7 * 24 * 60 * 60


def _load_cursor():
    """The stored cursor, or None (missing, unreadable, aged out, cache down)."""
    try:
        raw = cache.get(CURSOR_CACHE_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sumup_reconciliation] cursor unavailable (%s); starting from the "
            "oldest row",
            type(exc).__name__,
        )
        return None
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw["t"])
        pk = int(raw["pk"])
    except (KeyError, TypeError, ValueError):
        logger.warning(
            "[sumup_reconciliation] unreadable cursor ignored; starting from the "
            "oldest row"
        )
        return None
    if timezone.is_naive(moment):
        return None
    if moment < timezone.now() - timedelta(days=RECONCILIATION_DAYS):
        # The row it points at has aged out of the window; any row after it
        # is still in the window, but starting over is simpler and safe.
        logger.info("[sumup_reconciliation] cursor older than the window ignored")
        return None
    return moment, pk


def _store_cursor(counters):
    """Advance past the last row read, or clear the cursor after a full pass."""
    try:
        if counters.get("reached_end"):
            cache.delete(CURSOR_CACHE_KEY)
        elif counters.get("last_read") is not None:
            moment, pk = counters["last_read"]
            cache.set(
                CURSOR_CACHE_KEY,
                {"t": moment.isoformat(), "pk": pk},
                CURSOR_TTL_SECONDS,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[sumup_reconciliation] could not store the cursor (%s); the next "
            "run starts from the oldest row",
            type(exc).__name__,
        )


@csrf_exempt
@require_http_methods(["POST"])
def sumup_reconciliation_endpoint(request):
    """POST /api/admin/sumup-reconciliation/

    Run one bounded reconciliation sweep over recent PAID SumUp payments.
    Safe to retry or overlap: only PAID rows are selected, and each write
    re-checks the row under ``select_for_update`` and skips it if it is no
    longer PAID, so a refund is applied exactly once.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    if not getattr(settings, _FLAG, False):
        logger.warning("[sumup_reconciliation] skipped: %s is off", _FLAG)
        return JsonResponse(
            {"skipped": True, "reason": f"{_FLAG} is off"},
            status=200,
        )

    from crush_lu.management.commands.reconcile_sumup_payments import Command

    started = timezone.now()
    buffer = StringIO()
    command = Command(stdout=buffer, stderr=buffer, no_color=True)
    try:
        # include_partial stays False: a partial refund is a human decision
        # (contract §3, §9.8). batch_delay is left at the command default.
        counters = command.run_sweep(
            days=RECONCILIATION_DAYS,
            dry_run=False,
            include_partial=False,
            quiet=True,
            budget_seconds=RECONCILIATION_BUDGET_SECONDS,
            read_reserve_seconds=READ_RESERVE_SECONDS,
            write_reserve_seconds=WRITE_RESERVE_SECONDS,
            max_writes=MAX_WRITES_PER_RUN,
            # Oldest first: a run cut short (time or write limit) leaves the
            # newest rows, which stay in the 30-day window for weeks, rather
            # than the oldest, which are about to age out unchecked.
            oldest_first=True,
            resume_after=_load_cursor(),
        )
    except Exception:  # noqa: BLE001
        logger.exception("[sumup_reconciliation] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    _store_cursor(counters)

    body = {"status": "ok", "timestamp": started.isoformat()}
    body.update({key: counters[key] for key in COUNTER_KEYS})
    body["cursor_resumed"] = bool(counters.get("cursor_resumed"))
    # This run finished the pass; the next one starts from the oldest row.
    body["wrapped"] = bool(counters.get("reached_end"))
    # One structured line, queryable in App Insights without a database.
    # Counts only — no references, emails or payloads (contract §7.3).
    needs_attention = (
        body["partial"]
        or body["errors"]
        or body["unchecked"]
        or body["refunded_superseded"]
    )
    logger.log(
        logging.WARNING if needs_attention else logging.INFO,
        "[sumup_reconciliation] %s",
        " ".join(
            f"{key}={body[key]}" for key in (*COUNTER_KEYS, "cursor_resumed", "wrapped")
        ),
    )
    return JsonResponse(body, status=202)
