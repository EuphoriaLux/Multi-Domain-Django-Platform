"""
Admin API endpoints for the Hybrid Coach Review System + pre-screening sweeps.

Invoked by Azure Function timer triggers in `azure-functions/hybrid-maintenance/`
on the schedule documented in the Phase-D plan. Each endpoint:

- Requires a Bearer token matching ``settings.ADMIN_API_KEY`` (see
  ``_authenticate_admin_request``, cloned from ``crush_lu.api_admin_sync``).
- Lives outside ``i18n_patterns`` so Functions can hit ``/api/admin/...``
  without a language prefix.
- Returns ``JsonResponse({"processed": N, ...}, status=202)``.
- Enqueues user-facing notifications via ``django.tasks`` (runs through the
  configured TASKS backend — ImmediateBackend in dev, DatabaseBackend +
  ``manage.py db_worker`` in production).

The work lives *here*, not in the Function, because (a) Python/Django stays
close to its ORM + model invariants, and (b) the Function layer should be a
thin scheduler. The contact-sync pattern in production already does this.
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from crush_lu.api_admin_auth import authenticate_admin_request as _authenticate_admin_request
from crush_lu.api_admin_auth import unauthorized as _unauthorized

logger = logging.getLogger(__name__)


def _hybrid_disabled() -> JsonResponse:
    return JsonResponse(
        {"skipped": True, "reason": "HYBRID_COACH_SYSTEM_ENABLED=False"},
        status=200,
    )


FALLBACK_TOKEN_TTL = timedelta(days=30)


# -------------------------------------------------------------------------
# SLA sweep (Phase 3) — offer self-booking to users whose SLA breached.
# -------------------------------------------------------------------------

# Drain strategy. The only caller, the HybridSLASweep timer in
# azure-functions/hybrid-maintenance/function_app.py, fires HOURLY (:15), and
# production has no task worker, so each call sends mail synchronously. A fixed
# per-call cap would take hours to clear a burst, so instead one call claims
# small chunks (oldest breach first) and keeps going until the queue is empty or
# the start-of-send budget below is spent. A faster timer would shorten recovery time but is not needed for correctness.
SLA_SWEEP_CLAIM_CHUNK = 10
# The caller (_call_admin_endpoint in the HybridSLASweep Function) gives up after
# 60 s. A send that STARTS late can still run for its worst case, so the sweep
# stops starting sends at caller_timeout - worst_case_send - margin:
#   worst case = Graph sendMail 30 s + a cold token fetch up to 20 s
#   (GraphEmailBackend timeouts) = 50 s, margin 3 s  ->  new sends start in the
#   first 7 s only. Typical sends take well under a second, so this still
#   clears dozens per call; if throughput matters more, raise the Function's
#   caller timeout (recommended in the PR) and this budget follows.
SLA_SWEEP_CALLER_TIMEOUT_SECONDS = 60
SLA_SWEEP_WORST_CASE_SEND_SECONDS = 50
SLA_SWEEP_SAFETY_MARGIN_SECONDS = 3
SLA_SWEEP_SEND_BUDGET_SECONDS = (
    SLA_SWEEP_CALLER_TIMEOUT_SECONDS
    - SLA_SWEEP_WORST_CASE_SEND_SECONDS
    - SLA_SWEEP_SAFETY_MARGIN_SECONDS
)
# Delivery lease: a claim that was never marked sent for this long belonged to a
# worker that died between commit and send, so it is claimed again.
SLA_CLAIM_LEASE = timedelta(minutes=15)
# A failed send is retried by a later call, not hammered in a loop.
SLA_FAILED_RETRY_BACKOFF = timedelta(minutes=30)


def _stale_claim_q(now):
    """A claim made and never marked sent for ``SLA_CLAIM_LEASE``."""
    from django.db.models import Q

    return Q(
        fallback_offered_at__isnull=False,
        booking_token__isnull=False,
        fallback_offer_claimed_at__isnull=False,
        fallback_offer_claimed_at__lt=now - SLA_CLAIM_LEASE,
        fallback_offer_sent_at__isnull=True,
    )


def _with_sweep_priority(queryset, now):
    """Annotate ``sweep_priority``: 0 for stale-lease recoveries, 1 otherwise.

    A coach-initiated claim has a future or null ``sla_deadline``, so ordering
    only by deadline would put every breached submission ahead of its recovery
    and, when the send-start budget keeps running out, starve it. Sweep order
    is therefore ``("sweep_priority", "sla_deadline", "pk")``.
    """
    from django.db.models import Case, IntegerField, Value, When

    return queryset.annotate(
        sweep_priority=Case(
            When(_stale_claim_q(now), then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    )


def _sweep_candidates(now, exclude_pks=()):
    """Submissions the sweep may claim now.

    Two independent groups:

    * new work, which needs the SLA breach and the hybrid coach gate: fresh
      breaches and backed-off retries of failed sends;
    * lease recovery: a claim that was made (by the sweep OR by a coach's manual
      offer) and never marked sent for ``SLA_CLAIM_LEASE``. It only re-sends the
      SAME booking token, so it needs neither the SLA breach nor the coach gate;
      it just must still be bookable.
    """

    from django.db.models import Q

    from .models import ProfileSubmission

    never_offered = Q(
        fallback_offered_at__isnull=True,
        booking_token__isnull=True,
        sla_deadline__isnull=False,
        sla_deadline__lte=now,
        coach__hybrid_features_enabled=True,
    ) & (
        Q(fallback_offer_claimed_at__isnull=True)
        | Q(fallback_offer_claimed_at__lt=now - SLA_FAILED_RETRY_BACKOFF)
    )
    stale_claim = _stale_claim_q(now)
    return _with_sweep_priority(
        ProfileSubmission.objects.filter(
            never_offered | stale_claim,
            status="pending",
            is_paused=False,
            review_call_completed=False,
        )
        .exclude(booked_slots__status="booked")
        .exclude(pk__in=list(exclude_pks)),
        now,
    )


def mark_fallback_offered(sub, now, *, actor, reason, **details):
    """Claim a locked submission for a fallback email (no email here).

    Returns True when this recovered an earlier claim that was never sent: the
    existing booking token is kept, since the earlier mail may have gone out.
    """
    recovered = bool(sub.fallback_offered_at and sub.booking_token)
    sub.fallback_offer_claimed_at = now
    sub.fallback_offer_sent_at = None
    if recovered:
        sub.log_system_action(
            "fallback_claim_recovered", actor=actor, reason="stale_claim"
        )
        # Keep the token only while its link still works; an expired token
        # (booking page 404s) is replaced before the email goes out.
        if sub.booking_token_expires_at is None or sub.booking_token_expires_at < now:
            sub.booking_token = uuid.uuid4()
            sub.booking_token_expires_at = now + FALLBACK_TOKEN_TTL
            sub.log_system_action(
                "fallback_token_renewed", actor=actor, reason="expired_token"
            )
    else:
        sub.fallback_offered_at = now
        sub.booking_token = uuid.uuid4()
        sub.booking_token_expires_at = now + FALLBACK_TOKEN_TTL
        sub.log_system_action("fallback_offered", actor=actor, reason=reason, **details)
    sub.save(
        update_fields=[
            "fallback_offered_at",
            "booking_token",
            "booking_token_expires_at",
            "fallback_offer_claimed_at",
            "fallback_offer_sent_at",
            "system_actions",
        ]
    )
    return recovered


def mark_fallback_sent(submission_pk, now=None):
    """Record that the email went out or was terminally skipped."""
    from .models import ProfileSubmission

    ProfileSubmission.objects.filter(pk=submission_pk).update(
        fallback_offer_sent_at=now or timezone.now()
    )


def revert_fallback_offer(submission_pk, booking_token, *, reason):
    """Undo an offer whose email did not go out so a later sweep retries it.

    Keyed on the token this attempt minted: if anything changed the offer in the
    meantime we leave it alone. Also leaves it alone when the member already
    booked a screening slot with the token (claim_for_submission does not change
    the token, so equality alone cannot see that). ``claimed_at`` is kept as the
    last-attempt time so failures back off. Returns True when cleared.
    """
    from .models import ProfileSubmission, ScreeningSlot

    with transaction.atomic():
        sub = (
            ProfileSubmission.objects.select_for_update()
            .filter(pk=submission_pk, booking_token=booking_token)
            .first()
        )
        if sub is None:
            return False
        if ScreeningSlot.objects.filter(submission=sub, status="booked").exists():
            return False
        sub.fallback_offered_at = None
        sub.booking_token = None
        sub.booking_token_expires_at = None
        sub.log_system_action("fallback_email_failed", actor="system", reason=reason)
        sub.save(
            update_fields=[
                "fallback_offered_at",
                "booking_token",
                "booking_token_expires_at",
                "system_actions",
            ]
        )
    return True


def run_sla_sweep(host, is_secure, *, actor="system"):
    """One drain of the SLA fallback queue; the single implementation.

    Used by the ``sla_sweep`` endpoint and the ``sla_tick`` dev command, so
    the candidate selection (new breaches, backed-off retries, stale-lease
    recovery) and the per-row claim/send/undo logic exist exactly once.
    Returns the counters that the endpoint reports.
    """
    import time

    from .models import ProfileSubmission
    from .tasks import (
        SLA_EMAIL_AMBIGUOUS,
        SLA_EMAIL_FAILED,
        SLA_EMAIL_SENT,
        SLA_EMAIL_STALE,
        deliver_sla_fallback_email,
    )

    now = timezone.now()
    started = time.monotonic()

    def budget_spent():
        return time.monotonic() - started > SLA_SWEEP_SEND_BUDGET_SECONDS

    processed = failed = skipped = deferred = uncertain = 0
    attempted = set()
    while not budget_spent():
        # Phase 1 -- claim a chunk under the lock, no I/O beyond the database.
        claimed = []  # (pk, booking_token, recovered)
        with transaction.atomic():
            locked_ids = list(
                ProfileSubmission.objects.filter(
                    pk__in=_sweep_candidates(now, attempted)
                    .order_by("sweep_priority", "sla_deadline", "pk")
                    .values("pk")[:SLA_SWEEP_CLAIM_CHUNK]
                )
                .select_for_update(skip_locked=True)
                .values_list("pk", flat=True)
            )
            for sub in _with_sweep_priority(
                ProfileSubmission.objects.filter(pk__in=locked_ids), now
            ).order_by("sweep_priority", "sla_deadline", "pk"):
                attempted.add(sub.pk)
                # Savepoint per row: one bad row must not abort the chunk.
                try:
                    with transaction.atomic():
                        recovered = mark_fallback_offered(
                            sub,
                            now,
                            actor=actor,
                            reason="sla_breach",
                            sla_deadline=(
                                sub.sla_deadline.isoformat()
                                if sub.sla_deadline
                                else None
                            ),
                        )
                    claimed.append((sub.pk, sub.booking_token, recovered))
                except Exception:  # noqa: BLE001
                    logger.exception("[sla_sweep] Failed on submission %s", sub.pk)
                    failed += 1
        if not locked_ids:
            break

        # Phase 2 -- the claim is committed and no lock is held: send.
        for pk, token, recovered in claimed:
            if budget_spent():
                if not recovered:
                    revert_fallback_offer(pk, token, reason="sweep_time_budget")
                deferred += 1
                continue
            outcome = deliver_sla_fallback_email(pk, host, is_secure)
            if outcome == SLA_EMAIL_FAILED:
                # A recovered claim keeps its token (the earlier mail may have
                # reached the member); the lease will retry it.
                if not recovered:
                    revert_fallback_offer(pk, token, reason="email_not_sent")
                failed += 1
                continue
            if outcome == SLA_EMAIL_AMBIGUOUS:
                # Graph may have accepted it: keep the token in the mail, leave
                # the claim leased so the retry re-sends the SAME token.
                uncertain += 1
                continue
            if outcome == SLA_EMAIL_STALE:
                # Completed/paused/approved/rejected since the claim: clear the
                # misleading offer, no retry (it is no longer eligible).
                revert_fallback_offer(pk, token, reason="no_longer_bookable")
                skipped += 1
                continue
            mark_fallback_sent(pk)
            if outcome == SLA_EMAIL_SENT:
                processed += 1
            else:
                skipped += 1

    logger.info(
        "[sla_sweep] processed=%d failed=%d skipped=%d deferred=%d uncertain=%d",
        processed,
        failed,
        skipped,
        deferred,
        uncertain,
    )
    return {
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
        "deferred": deferred,
        "uncertain": uncertain,
        "timestamp": now.isoformat(),
    }


@csrf_exempt
@require_http_methods(["POST"])
def sla_sweep(request):
    """POST /api/admin/hybrid-coach-sla-sweep/

    For each pending submission where:
      * sla_deadline has passed,
      * fallback hasn't been offered yet (or an earlier claim went stale, or a
        failed send has backed off),
      * the submission isn't paused or already booked,
      * the assigned coach opted into hybrid features,

    claim it under a row lock (``fallback_offered_at``, a 30-day
    ``booking_token``, ``fallback_offer_claimed_at``, a ``fallback_offered``
    ``system_actions`` entry), commit, and only then send the email.

    Mail never goes out under a lock. If the send fails (or the time budget runs
    out) the offer is cleared so the submission is retried; if the worker dies
    mid-way the claim has no ``fallback_offer_sent_at`` and is reclaimed after
    ``SLA_CLAIM_LEASE``. A submission that stopped being bookable between
    claim and send is cleared without a mail. A member who unsubscribed or whose address is suppressed
    is marked sent without a mail (retrying cannot help).

    Idempotent on repeat calls. One call drains oldest-breach-first until the
    queue is empty or ``SLA_SWEEP_SEND_BUDGET_SECONDS`` is spent.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    if not getattr(settings, "HYBRID_COACH_SYSTEM_ENABLED", False):
        logger.info("[sla_sweep] HYBRID_COACH_SYSTEM_ENABLED=False — skipping")
        return _hybrid_disabled()

    result = run_sla_sweep(_resolve_email_host(request), request.is_secure())
    return JsonResponse(result, status=202)


def _resolve_email_host(request) -> str:
    """Pick the host for links in system-generated emails.

    Prefers the incoming request host when it looks like a real domain (so
    Function→``test.crush.lu`` produces links back to staging naturally).
    Falls back to ``STAGING_MODE`` — same slot-sticky env var the rest of the
    codebase uses — then to the first ``ALLOWED_HOSTS`` entry containing
    ``crush.lu``.
    """
    import os

    req_host = request.get_host()
    if req_host and "azurewebsites" not in req_host and "localhost" not in req_host:
        return req_host
    if os.environ.get("STAGING_MODE", "").lower() in ("true", "1", "yes"):
        return "test.crush.lu"
    for allowed in getattr(settings, "ALLOWED_HOSTS", []) or []:
        if allowed and "crush.lu" in allowed:
            return allowed.lstrip(".")
    return "crush.lu"


# -------------------------------------------------------------------------
# Pre-screening invites (reuses existing management command)
# -------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def pre_screening_invites(request):
    """POST /api/admin/pre-screening-invites/

    Thin wrapper around the existing ``send_pre_screening_invites`` command so
    a Function timer can drive it on the desired hourly cadence. All logic
    (the 1h invite / 4h push / 24h reminder windows) stays in the command so
    it remains usable from a dev shell.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    from io import StringIO

    from django.core.management import CommandError, call_command

    started = timezone.now()
    buffer = StringIO()
    try:
        call_command("send_pre_screening_invites", stdout=buffer, stderr=buffer)
    except CommandError:
        # CodeQL: don't echo the raw exception message to the caller — leaks
        # stack traces / internal paths. The failure is fully captured in logs
        # with stack via logger.exception; callers only need the error code.
        logger.exception("[pre_screening_invites] Command error")
        return JsonResponse({"error": "command_error"}, status=500)
    except Exception:  # noqa: BLE001
        logger.exception("[pre_screening_invites] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    took_ms = int((timezone.now() - started).total_seconds() * 1000)
    logger.info("[pre_screening_invites] took_ms=%d", took_ms)
    return JsonResponse(
        {"ok": True, "took_ms": took_ms}, status=202
    )


# -------------------------------------------------------------------------
# "My Crush!" 24h untouched-lead reminders (spec §6/O8)
# -------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def crush_lead_reminders(request):
    """POST /api/admin/crush-lead-reminders/

    Remind each routed coach about crush leads they have not touched in 24h,
    halfway through the 48h call SLA the member was promised.

    The sweep itself lives in ``crush_lu.services.crush_leads`` so it stays
    usable from a dev shell via ``manage.py send_crush_lead_reminders``.
    Idempotent by **claim-then-send** (spec §11/O14): ``reminder_sent_at`` is
    both the filter and the record, and it is committed as a *claim* under the
    row lock *before* the push goes out — not in the same savepoint as it, as
    this docstring said until Phase E. A failed or cancelled send clears the
    stamp again in a second guarded ``UPDATE``, so the lead stays eligible for
    the next sweep; a delivered one keeps it, so repeated timer delivery
    produces exactly one reminder per lead.

    The trade that buys: the push helper's device-health writes (``mark_failure``
    and the 410 ``delete``) no longer roll back with the stamp, so a dead
    endpoint actually reaches its five-failure auto-delete. The cost is a
    deliberate crash window — a process killed between committing the claim
    and releasing it leaves that one reminder stamped but unsent, so it is
    skipped rather than retried. Anything that *raises* is covered by a
    best-effort release; only an abrupt process death is not.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    # Feature gate (spec §10). Its own switch rather than
    # HYBRID_COACH_SYSTEM_ENABLED: this timer shares a function app with the
    # other maintenance jobs, so without it the only ways to stage or stop the
    # reminder flow are turning off every hybrid job or unsetting
    # DJANGO_CRUSH_LEAD_REMINDERS_URL — and that one no-ops silently.
    # `manage.py send_crush_lead_reminders` stays ungated: it is the manual,
    # explicitly-invoked path, not the scheduler.
    if not getattr(settings, "CRUSH_LEAD_REMINDERS_ENABLED", False):
        logger.info(
            "[crush_lead_reminders] CRUSH_LEAD_REMINDERS_ENABLED=False — skipping"
        )
        return JsonResponse(
            {"skipped": True, "reason": "CRUSH_LEAD_REMINDERS_ENABLED=False"},
            status=200,
        )

    from crush_lu.services.crush_leads import sweep_lead_reminders

    started = timezone.now()
    try:
        result = sweep_lead_reminders(now=started)
    except Exception:  # noqa: BLE001
        # CodeQL: never echo the raw exception to the caller (stack traces /
        # internal paths). Fully captured in logs by logger.exception.
        logger.exception("[crush_lead_reminders] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    took_ms = int((timezone.now() - started).total_seconds() * 1000)
    logger.info(
        "[crush_lead_reminders] sent=%d failed=%d took_ms=%d",
        result["sent"],
        result["failed"],
        took_ms,
    )
    return JsonResponse(
        {
            "sent": result["sent"],
            "failed": result["failed"],
            "took_ms": took_ms,
            "timestamp": started.isoformat(),
        },
        status=202,
    )
