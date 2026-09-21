"""
FinOps Daily Sync - Azure Function

Automated daily synchronization of Azure Cost Management data.
Triggers the Django webhook endpoint to refresh cost data.

Schedule: Daily at 3:00 AM UTC (0 0 3 * * *)
Timeout: 10 minutes
"""

import azure.functions as func
import logging
import requests
import os
import time
from datetime import datetime, time as dtime, timedelta, timezone

app = func.FunctionApp()

# Deliberately ABOVE Django's own per-region budget (90s, plus at most one
# page's worst case). `requests` timing out only stops this side waiting - it
# does not cancel the synchronous work still running in the web worker. If this
# fired first we would abandon a live worker and immediately occupy another
# with the next region; a few of those and the site has no workers left. So
# Django is given room to always answer first, and a timeout here is treated as
# "the backend is wedged" rather than "try the next one".
PER_REGION_TIMEOUT = 170
# requests applies a scalar timeout to connecting and to reading separately,
# so a bare 170 could spend 170s + 170s. Connecting gets its own short limit
# and a region only starts when both fit in what is left of the budget.
CONNECT_TIMEOUT = 10
# host.json gives this function 10 minutes. Leave headroom so the run ends with
# a summary rather than being killed mid-region.
BUDGET_SECONDS = 8 * 60

# One run fits roughly eight regions: in production a region takes 15-100s
# (throttling from prices.azure.com, not data volume), so nineteen never fit
# in one budget and the same regions were skipped every night. The timer runs
# every 20 minutes through a morning window instead, and each run takes only
# the regions Django reports as still missing for the day; a region lost to a
# 429 storm is simply retried in the next slot.
RETAIL_SCHEDULE = "0 0,20,40 4-6 * * *"  # 04:00-06:40 UTC
# The window's last slot. Only that run turns regions still missing into a
# failed invocation (and so the alert); earlier runs leave them to the next.
RETAIL_LAST_RUN_UTC = dtime(6, 40)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_last_run_of_window(now: datetime) -> bool:
    last = datetime.combine(now.date(), RETAIL_LAST_RUN_UTC, tzinfo=timezone.utc)
    # A timer can fire a hair early; a minute of grace keeps the last slot
    # from being read as an earlier one and never alerting.
    return now >= last - timedelta(minutes=1)


@app.function_name(name="finops_daily_sync")
@app.timer_trigger(
    schedule="0 0 3 * * *",  # 3:00 AM UTC daily
    arg_name="timer",
    run_on_startup=False,  # Don't run on deployment
    use_monitor=True  # Enable monitoring in Azure Portal
)
def daily_cost_sync(timer: func.TimerRequest) -> None:
    """
    Trigger daily cost data sync via Django webhook

    Environment Variables Required:
        - FINOPS_SYNC_ENABLED: Set to 'true' to enable sync
        - DJANGO_WEBHOOK_URL: Full URL to Django webhook endpoint
        - SECRET_SYNC_TOKEN: Shared secret for webhook authentication
    """
    timestamp = datetime.utcnow().isoformat()

    # Check if sync is enabled
    if not os.getenv('FINOPS_SYNC_ENABLED', 'false').lower() == 'true':
        logging.info(f'[{timestamp}] FinOps sync is disabled (FINOPS_SYNC_ENABLED=false)')
        return

    # Get configuration from environment
    webhook_url = os.getenv('DJANGO_WEBHOOK_URL')
    sync_token = os.getenv('SECRET_SYNC_TOKEN')

    # Validate required configuration
    if not webhook_url:
        logging.error(f'[{timestamp}] Missing required environment variable: DJANGO_WEBHOOK_URL')
        raise ValueError('DJANGO_WEBHOOK_URL environment variable is required')

    if not sync_token:
        logging.error(f'[{timestamp}] Missing required environment variable: SECRET_SYNC_TOKEN')
        raise ValueError('SECRET_SYNC_TOKEN environment variable is required')

    # Log sync initiation
    if timer.past_due:
        logging.warning(f'[{timestamp}] Function past-due warning (cold start delay)')

    logging.info(f'[{timestamp}] Initiating FinOps daily cost sync')
    logging.info(f'[{timestamp}] Target webhook: {webhook_url}')

    try:
        # Call Django webhook endpoint
        response = requests.post(
            webhook_url,
            headers={
                'X-Sync-Token': sync_token,
                'User-Agent': 'Azure-Function-FinOps-Sync/1.0'
            },
            timeout=600  # 10 minutes (same as Azure Function timeout)
        )

        # Check response status
        response.raise_for_status()

        # Parse response
        result = response.json()
        status = result.get('status', 'unknown')
        message = result.get('message', 'No message provided')

        # Log success
        logging.info(f'[{timestamp}] Sync completed successfully')
        logging.info(f'[{timestamp}] Status: {status}')
        logging.info(f'[{timestamp}] Message: {message}')

        # Log additional details if available
        if 'details' in result:
            details = result['details']
            logging.info(f'[{timestamp}] Details: {details}')

    except requests.exceptions.Timeout:
        logging.error(f'[{timestamp}] Sync request timed out after 10 minutes')
        raise

    except requests.exceptions.HTTPError as e:
        logging.error(f'[{timestamp}] HTTP error during sync: {e}')
        logging.error(f'[{timestamp}] Response status: {e.response.status_code}')
        logging.error(f'[{timestamp}] Response body: {e.response.text}')
        raise

    except requests.exceptions.RequestException as e:
        logging.error(f'[{timestamp}] Network error during sync: {str(e)}')
        raise

    except ValueError as e:
        # JSON parsing error
        logging.error(f'[{timestamp}] Invalid JSON response from webhook: {str(e)}')
        logging.error(f'[{timestamp}] Response text: {response.text}')
        raise

    except Exception as e:
        logging.error(f'[{timestamp}] Unexpected error during sync: {str(e)}')
        raise

    logging.info(f'[{timestamp}] FinOps daily sync function completed')


@app.function_name(name="retail_price_daily_sync")
@app.timer_trigger(
    schedule=RETAIL_SCHEDULE,  # after the 3:00 AM UTC cost import timer
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def daily_retail_price_sync(timer: func.TimerRequest) -> None:
    """Trigger the append-only European Azure retail-price snapshot.

    Walks the catalogue one region at a time. The full European catalogue is
    ~200k rows over 208 pages, which no single App Service request can carry:
    the Azure load balancer cuts a request off at ~240s on Linux, so a
    whole-catalogue POST times out every night no matter how high this side's
    timeout is set. One region is 6-16 pages and finishes comfortably inside
    that ceiling, and a region that fails no longer costs the whole day.

    Several runs share one day (see ``RETAIL_SCHEDULE``): each takes only the
    regions still missing, and only the window's last run fails the invocation
    if any remain.
    """
    now = _utcnow()
    last_run = _is_last_run_of_window(now)
    timestamp = now.replace(tzinfo=None).isoformat()
    if os.getenv("RETAIL_PRICE_SYNC_ENABLED", "false").lower() != "true":
        logging.info(
            f"[{timestamp}] Retail price sync is disabled "
            "(RETAIL_PRICE_SYNC_ENABLED=false)"
        )
        return

    webhook_url = os.getenv("DJANGO_RETAIL_PRICE_WEBHOOK_URL")
    sync_token = os.getenv("SECRET_SYNC_TOKEN")
    if not webhook_url:
        raise ValueError(
            "DJANGO_RETAIL_PRICE_WEBHOOK_URL environment variable is required"
        )
    if not sync_token:
        raise ValueError("SECRET_SYNC_TOKEN environment variable is required")
    if timer.past_due:
        logging.warning(f"[{timestamp}] Retail price timer is past due")

    logging.info(f"[{timestamp}] Initiating retail price sync")
    logging.info(f"[{timestamp}] Target webhook: {webhook_url}")

    headers = {
        "X-Sync-Token": sync_token,
        "User-Agent": "Azure-Function-Power-Hub-Retail-Price-Sync/2.0",
    }

    # Ask Django which regions to walk rather than duplicating the list here;
    # a stale local copy would silently stop capturing a region. The same call
    # pins the snapshot date for the whole invocation: each region is a
    # separate request, so without a fixed date a walk that straddled local
    # midnight would file its regions under two dates and complete neither.
    # Django supplies it so it cannot drift from this side's clock or timezone.
    regions_url = webhook_url.rstrip("/") + "/regions/"
    try:
        regions_response = requests.get(
            regions_url, headers=headers, timeout=(CONNECT_TIMEOUT, 30)
        )
        if regions_response.status_code == 404:
            # This Function App deploys straight to production on merge, while
            # the Django side lands on the staging slot and waits for a manual
            # portal swap. So there is a window where the new timer is live
            # against the old production app, and /regions/ does not exist yet.
            # Say so plainly: the old whole-catalogue POST is NOT a safe
            # fallback — it is the request that timed out every night, and
            # nineteen of them would be far worse.
            raise RuntimeError(
                f"{regions_url} returned 404. The production Django slot has "
                "not been swapped to a build containing the per-region retail "
                "price endpoint yet. Swap it in the Azure Portal; this timer "
                "will recover on its next run. No regions were synced."
            )
        regions_response.raise_for_status()
        plan = regions_response.json()
        all_regions = plan["regions"]
        snapshot_date = plan.get("snapshot_date")
    except Exception as e:
        logging.error(
            f"[{timestamp}] Could not fetch the region list from {regions_url}: {e}"
        )
        # Every failed invocation reaches the alert, and a later slot may well
        # get through, so only the window's last run fails over this.
        if last_run:
            raise
        return

    if not all_regions:
        message = "The retail price endpoint returned an empty region list"
        if last_run:
            raise RuntimeError(message)
        logging.error(f"[{timestamp}] {message}; a later run in the window retries")
        return

    # "pending" leaves out regions already captured for the day. A Django
    # build without it (this app deploys on merge, Django waits for a swap)
    # gets every region again; those it already holds answer 200 in about a
    # second, so the fallback costs time, not correctness.
    regions = plan["pending"] if "pending" in plan else all_regions
    if not regions:
        logging.info(
            f"[{timestamp}] All {len(all_regions)} region(s) are already captured "
            f"for {snapshot_date}; nothing to do"
        )
        return

    logging.info(
        f"[{timestamp}] Syncing {len(regions)} of {len(all_regions)} region(s) "
        f"one at a time for snapshot date "
        f"{snapshot_date or 'unset (server default)'}"
        f"{' (last run of the window)' if last_run else ''}"
    )

    succeeded: list[str] = []
    attempted: list[str] = []
    failures: list[str] = []
    skipped: list[str] = []
    # host.json allows this function 10 minutes. Stop short of that so a slow
    # night ends with a logged summary instead of being killed mid-region with
    # no diagnostic at all.
    deadline = time.monotonic() + BUDGET_SECONDS

    for region in regions:
        remaining = deadline - time.monotonic()
        # Only start a region that can be waited on in full. Posting with a
        # few seconds left gave up on Django before it could answer, which
        # left a web worker fetching for nobody and was then reported as a
        # backend timeout that stopped the walk, every night.
        if remaining < CONNECT_TIMEOUT + PER_REGION_TIMEOUT:
            skipped = [r for r in regions if r not in attempted]
            logging.warning(
                f"[{timestamp}] Out of time for this run after {len(attempted)} "
                f"region(s); left for a later run: {', '.join(skipped)}"
            )
            break

        attempted.append(region)
        payload = {"region": region}
        if snapshot_date:
            payload["snapshot_date"] = snapshot_date
        try:
            response = requests.post(
                webhook_url,
                headers=headers,
                json=payload,
                timeout=(CONNECT_TIMEOUT, PER_REGION_TIMEOUT),
            )
            response.raise_for_status()
            result = response.json()
            succeeded.append(region)
            logging.info(
                f"[{timestamp}] {region}: "
                f"{result.get('message', 'No message provided')}"
            )
        except requests.exceptions.Timeout:
            # Django should have answered inside its own budget. That it did
            # not means the worker is still busy on a request nobody is
            # reading any more. Stop: posting the next region would occupy a
            # second worker while this one is still stuck, and so on.
            failures.append(f"{region} (timeout after {PER_REGION_TIMEOUT}s)")
            skipped = [r for r in regions if r not in attempted]
            if skipped:
                failures.append(
                    f"{len(skipped)} region(s) not attempted: stopped after a "
                    "backend timeout"
                )
            logging.error(
                f"[{timestamp}] {region}: no response within "
                f"{PER_REGION_TIMEOUT}s. Stopping the walk so abandoned "
                f"requests cannot stack up; not attempted: "
                f"{', '.join(skipped) or 'none'}"
            )
            break
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body = e.response.text[:500] if e.response is not None else ""
            failures.append(f"{region} (HTTP {status})")
            logging.error(f"[{timestamp}] {region}: HTTP error {e} - {body}")
        except Exception as e:
            failures.append(f"{region} ({type(e).__name__})")
            logging.error(f"[{timestamp}] {region}: {type(e).__name__}: {e}")

    logging.info(
        f"[{timestamp}] Retail price sync run finished: "
        f"{len(succeeded)}/{len(regions)} region(s) captured"
    )

    if not (failures or skipped):
        return

    detail = []
    if failures:
        detail.append(f"{len(failures)} failed ({'; '.join(failures)})")
    if skipped:
        detail.append(f"{len(skipped)} skipped for time ({', '.join(skipped)})")
    summary = (
        f"Retail price sync captured {len(succeeded)} of {len(regions)} "
        f"region(s): {'; '.join(detail)}"
    )
    # Fail the invocation so an incomplete day reaches the alert, but only
    # once the window is over; before that the next run picks the rest up.
    if last_run:
        raise RuntimeError(f"{summary}. Last run of the window for {snapshot_date}.")
    logging.warning(f"[{timestamp}] {summary}. A later run in the window retries them.")
