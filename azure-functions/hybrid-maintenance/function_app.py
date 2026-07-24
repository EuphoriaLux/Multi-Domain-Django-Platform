"""
Azure Function App for Crush.lu Hybrid Coach Review + Pre-Screening sweeps.

Two timer triggers drive the admin endpoints in `crush_lu/api_admin_hybrid.py`:

- PreScreeningInvites: every 10 minutes, POSTs to
  /api/admin/pre-screening-invites/ so the send_pre_screening_invites
  management command drives the 1h-invite / 4h-push / 24h-reminder cadence.
- HybridSLASweep: hourly, POSTs to /api/admin/hybrid-coach-sla-sweep/ so
  submissions whose Coach SLA has breached get the self-booking fallback
  offered (and the fallback email enqueued).

Both endpoints return 202 immediately and run the work in a background task
on the Django side, so the Function just has to fire the HTTP request and
log the outcome.

Environment Variables Required:
    - DJANGO_PRE_SCREENING_INVITES_URL: e.g. https://crush.lu/api/admin/pre-screening-invites/
    - DJANGO_HYBRID_SLA_SWEEP_URL: e.g. https://crush.lu/api/admin/hybrid-coach-sla-sweep/
    - DJANGO_WEEKLY_KPIS_URL: e.g. https://crush.lu/api/admin/weekly-kpis/
    - DJANGO_ROTATE_CONNECT_QUESTIONS_URL: e.g. https://crush.lu/api/admin/rotate-connect-questions/
    - DJANGO_CAMPAIGN_DISPATCH_URL: e.g. https://crush.lu/api/admin/campaigns/dispatch/
    - DJANGO_CRUSH_LEAD_REMINDERS_URL: e.g. https://crush.lu/api/admin/crush-lead-reminders/
      (OPS: like every URL here it must be set manually on the Function App —
      an unset var logs an error and no-ops, so the 24h reminder silently
      never fires.)
    - ADMIN_API_KEY: Bearer token shared with the Django ADMIN_API_KEY setting
    - HYBRID_MAINTENANCE_ENABLED: Should be 'true' in production; anything
      else skips both triggers (safe-default: functions are deployed disabled
      until the flag is flipped).

Deployment:
    - Automated via GitHub Actions (.github/workflows/deploy-hybrid-maintenance-function.yml)
    - Azure resource name: crush-hybrid-maintenance (django-app-rg, westeurope)
"""

import logging
import os
from datetime import datetime

import azure.functions as func
import requests

app = func.FunctionApp()


def _call_admin_endpoint(name: str, url_env_var: str, timeout: int = 60) -> None:
    """Shared body: POST to a Django admin endpoint with bearer auth.

    Raises so Azure Functions marks the invocation as Failed on any
    network / auth / server error — missing config just early-returns
    (logged as an error) since it isn't a retryable runtime fault.

    The URL's own host is sent in the Host header (requests' default).
    DomainURLRoutingMiddleware treats `test.crush.lu` as an alias of
    `crush.lu` (see `azureproject/domains.py`), so both production and
    staging targets route through `urls_crush.py` correctly. Forcing a
    literal `Host: crush.lu` would break staging because `crush.lu` is
    not bound to the staging slot — Azure App Service returns 404
    before Django sees the request.
    """
    url = os.environ.get(url_env_var)
    api_key = os.environ.get("ADMIN_API_KEY")
    enabled = os.environ.get("HYBRID_MAINTENANCE_ENABLED", "").lower() == "true"

    if not enabled:
        logging.info("%s: HYBRID_MAINTENANCE_ENABLED is not true — skipping", name)
        return
    if not url:
        logging.error("%s: %s not configured", name, url_env_var)
        return
    if not api_key:
        logging.error("%s: ADMIN_API_KEY not configured", name)
        return

    try:
        response = requests.post(
            url,
            json={},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        response.raise_for_status()

        if response.status_code == 202:
            logging.info("%s: triggered successfully (202 Accepted)", name)
        else:
            # Endpoints return 202 on success, 200 on skipped (flag off).
            try:
                body = response.json()
            except ValueError:
                body = {"raw": response.text[:200]}
            logging.info("%s: %s body=%s", name, response.status_code, body)

    except requests.exceptions.Timeout:
        logging.error("%s: request timed out", name)
        raise
    except requests.exceptions.RequestException as exc:
        logging.error("%s: request failed — %s", name, exc)
        raise


@app.function_name(name="PreScreeningInvites")
@app.timer_trigger(
    schedule="0 */10 * * * *",  # Every 10 minutes
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def pre_screening_invites(timer: func.TimerRequest) -> None:
    """Drive the pre-screening invite/reminder/push sweeps.

    The Django command dedupes per submission via cache keys, so running
    every 10 minutes is safe — at-most-once per submission per flow.
    """
    ts = datetime.utcnow().isoformat()
    if timer.past_due:
        logging.warning("PreScreeningInvites: timer past due at %s", ts)
    logging.info("PreScreeningInvites: starting at %s", ts)
    _call_admin_endpoint(
        "PreScreeningInvites", "DJANGO_PRE_SCREENING_INVITES_URL"
    )


@app.function_name(name="HybridSLASweep")
@app.timer_trigger(
    schedule="0 15 * * * *",  # Hourly at :15 (offset from invites)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def hybrid_sla_sweep(timer: func.TimerRequest) -> None:
    """Offer self-booking fallback to submissions whose SLA has breached."""
    ts = datetime.utcnow().isoformat()
    if timer.past_due:
        logging.warning("HybridSLASweep: timer past due at %s", ts)
    logging.info("HybridSLASweep: starting at %s", ts)
    _call_admin_endpoint("HybridSLASweep", "DJANGO_HYBRID_SLA_SWEEP_URL")


@app.function_name(name="WeeklyKPIs")
@app.timer_trigger(
    schedule="0 0 7 * * 1",  # Mondays at 07:00 UTC (NCRONTAB: sec min hour day month dow)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def weekly_kpis(timer: func.TimerRequest) -> None:
    """Compute + persist the last full week's KPIs and email the digest.

    The Django command ``update_or_create``s the snapshot, so a retried or
    catch-up invocation just refreshes the same week's row.
    """
    ts = datetime.utcnow().isoformat()
    if timer.past_due:
        logging.warning("WeeklyKPIs: timer past due at %s", ts)
    logging.info("WeeklyKPIs: starting at %s", ts)
    _call_admin_endpoint("WeeklyKPIs", "DJANGO_WEEKLY_KPIS_URL")


@app.function_name(name="RotateConnectQuestions")
@app.timer_trigger(
    schedule="0 30 6 * * 1",  # Mondays at 06:30 UTC (offset from WeeklyKPIs at 07:00)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def rotate_connect_questions(timer: func.TimerRequest) -> None:
    """Ensure the new ISO week's Crush Connect question set exists.

    The Django command builds the set once per week (deterministic weighted
    pick), so a retried or catch-up invocation is a no-op.
    """
    ts = datetime.utcnow().isoformat()
    if timer.past_due:
        logging.warning("RotateConnectQuestions: timer past due at %s", ts)
    logging.info("RotateConnectQuestions: starting at %s", ts)
    _call_admin_endpoint(
        "RotateConnectQuestions", "DJANGO_ROTATE_CONNECT_QUESTIONS_URL"
    )


@app.function_name(name="CampaignDispatch")
@app.timer_trigger(
    schedule="0 2/5 * * * *",  # Every 5 minutes at :02 (offset from invites at :00/:10)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def campaign_dispatch(timer: func.TimerRequest) -> None:
    """Drive one bounded dispatch tick for multi-channel campaigns.

    The Django endpoint promotes due scheduled campaigns and sends bounded
    per-channel batches with per-recipient resumability, guarded by a
    heartbeat claim — so overlapping or retried invocations never
    double-send. Gated Django-side by CAMPAIGN_DISPATCH_ENABLED (returns
    200 skipped when off) on top of this app's HYBRID_MAINTENANCE_ENABLED.
    """
    ts = datetime.utcnow().isoformat()
    if timer.past_due:
        logging.warning("CampaignDispatch: timer past due at %s", ts)
    logging.info("CampaignDispatch: starting at %s", ts)
    # Dispatch runs synchronously with an ~80s wall-clock budget on the
    # Django side — give the HTTP call headroom beyond that (but stay under
    # gunicorn's 120s) so a slow batch doesn't get marked as a failure here.
    _call_admin_endpoint(
        "CampaignDispatch", "DJANGO_CAMPAIGN_DISPATCH_URL", timeout=110
    )


@app.function_name(name="ProfileReminders")
@app.timer_trigger(
    schedule="0 0 8 * * *",  # Daily at 08:00 UTC
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def profile_reminders(timer: func.TimerRequest) -> None:
    """Send the 24h/72h/7d profile-completion reminder emails.

    Idempotent per user per reminder type: a ProfileReminder row (unique on
    (user, reminder_type)) records each send, so a retried or catch-up
    invocation never re-sends the same reminder.
    """
    ts = datetime.utcnow().isoformat()
    if timer.past_due:
        logging.warning("ProfileReminders: timer past due at %s", ts)
    logging.info("ProfileReminders: starting at %s", ts)
    _call_admin_endpoint("ProfileReminders", "DJANGO_PROFILE_REMINDERS_URL")


@app.function_name(name="GdprRetention")
@app.timer_trigger(
    schedule="0 30 5 * * 0",  # Sundays at 05:30 UTC (off-peak, clear of Monday jobs)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def gdpr_retention(timer: func.TimerRequest) -> None:
    """Weekly GDPR data-minimization retention sweep.

    The Django endpoint runs gdpr_retention_cleanup with --apply; retention
    windows come from settings.GDPR_RETENTION on the Django side. Deletions
    are bounded to rows past their window per category.
    """
    ts = datetime.utcnow().isoformat()
    if timer.past_due:
        logging.warning("GdprRetention: timer past due at %s", ts)
    logging.info("GdprRetention: starting at %s", ts)
    _call_admin_endpoint("GdprRetention", "DJANGO_GDPR_RETENTION_URL")


@app.function_name(name="CrushLeadReminders")
@app.timer_trigger(
    schedule="0 45 * * * *",  # Hourly at :45 (clear of invites :00/:10 and SLA :15)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def crush_lead_reminders(timer: func.TimerRequest) -> None:
    """Remind coaches about untouched "My Crush!" leads at the 24h mark.

    A member who declares a crush is promised a coach call within 48h, so
    this fires halfway through while there is still a day to make it.

    Hourly rather than daily: the SLA is measured per lead from its own
    declaration time, so a daily pass would leave a lead up to 23h late.

    Idempotent per lead: reminder_sent_at is both the filter and the record
    on the Django side, so a retried or catch-up invocation never
    double-reminds.
    """
    ts = datetime.utcnow().isoformat()
    if timer.past_due:
        logging.warning("CrushLeadReminders: timer past due at %s", ts)
    logging.info("CrushLeadReminders: starting at %s", ts)
    _call_admin_endpoint("CrushLeadReminders", "DJANGO_CRUSH_LEAD_REMINDERS_URL")
