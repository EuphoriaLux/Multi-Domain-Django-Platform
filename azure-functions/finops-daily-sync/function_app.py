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
from datetime import datetime

app = func.FunctionApp()

# One region is 6-16 pages, so this is generous; it exists to stop a wedged
# region from eating the whole invocation.
PER_REGION_TIMEOUT = 120
# host.json gives this function 10 minutes. Leave headroom so the run ends with
# a summary rather than being killed mid-region.
BUDGET_SECONDS = 8 * 60


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
    schedule="0 0 4 * * *",  # 4:00 AM UTC daily, after the cost import timer
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
    """
    timestamp = datetime.utcnow().isoformat()
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
    # a stale local copy would silently stop capturing a region.
    regions_url = webhook_url.rstrip("/") + "/regions/"
    try:
        regions_response = requests.get(regions_url, headers=headers, timeout=30)
        regions_response.raise_for_status()
        regions = regions_response.json()["regions"]
    except Exception as e:
        logging.error(
            f"[{timestamp}] Could not fetch the region list from {regions_url}: {e}"
        )
        raise

    if not regions:
        raise RuntimeError("The retail price endpoint returned an empty region list")

    logging.info(f"[{timestamp}] Syncing {len(regions)} region(s) one at a time")

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
        if remaining <= 0:
            skipped = [r for r in regions if r not in attempted]
            logging.error(
                f"[{timestamp}] Ran out of time after {len(attempted)} region(s); "
                f"skipped: {', '.join(skipped)}"
            )
            break

        attempted.append(region)
        try:
            response = requests.post(
                webhook_url,
                headers=headers,
                json={"region": region},
                timeout=min(PER_REGION_TIMEOUT, max(remaining, 1)),
            )
            response.raise_for_status()
            result = response.json()
            succeeded.append(region)
            logging.info(
                f"[{timestamp}] {region}: "
                f"{result.get('message', 'No message provided')}"
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body = e.response.text[:500] if e.response is not None else ""
            failures.append(f"{region} (HTTP {status})")
            logging.error(f"[{timestamp}] {region}: HTTP error {e} - {body}")
        except Exception as e:
            failures.append(f"{region} ({type(e).__name__})")
            logging.error(f"[{timestamp}] {region}: {type(e).__name__}: {e}")

    logging.info(
        f"[{timestamp}] Retail price daily sync finished: "
        f"{len(succeeded)}/{len(regions)} region(s) captured"
    )

    # Fail the invocation so a partial night still reaches the alert, but only
    # after every region has been attempted.
    if failures or skipped:
        detail = []
        if failures:
            detail.append(f"{len(failures)} failed ({'; '.join(failures)})")
        if skipped:
            detail.append(
                f"{len(skipped)} skipped for time ({', '.join(skipped)})"
            )
        raise RuntimeError(
            f"Retail price sync captured {len(succeeded)} of {len(regions)} "
            f"region(s): {'; '.join(detail)}"
        )
