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
from datetime import datetime

app = func.FunctionApp()


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
