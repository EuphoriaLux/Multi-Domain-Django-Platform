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


def _call_admin_endpoint(name: str, url_env_var: str) -> None:
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
            timeout=60,
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
