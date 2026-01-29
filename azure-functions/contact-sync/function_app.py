"""
Azure Function App for Crush.lu Outlook Contact Synchronization

Scheduled to run daily to ensure contacts are in sync with the database.
Uses Timer Trigger with NCRONTAB expression for scheduling.

Environment Variables Required:
    - DJANGO_MANAGEMENT_COMMAND_URL: URL to trigger Django management command
      Example: https://crush.azurewebsites.net/api/admin/sync-contacts/
    - ADMIN_API_KEY: Authentication key for admin API endpoint
    - OUTLOOK_CONTACT_SYNC_ENABLED: Should be 'true' in production

Schedule:
    - Runs daily at 3:00 AM UTC (4:00 AM CET in summer, 3:00 AM CET in winter)
    - NCRONTAB: "0 0 3 * * *" (sec min hour day month weekday)

Deployment:
    - Automated via GitHub Actions on push to main branch
    - See .github/workflows/deploy-contact-sync-function.yml
"""

import logging
import os
import azure.functions as func
import requests
from datetime import datetime

app = func.FunctionApp()

logger = logging.getLogger(__name__)


@app.function_name(name="DailyContactSync")
@app.timer_trigger(
    schedule="0 0 3 * * *",  # Daily at 3:00 AM UTC
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True
)
def daily_contact_sync(timer: func.TimerRequest) -> None:
    """
    Scheduled function to sync all Crush.lu profiles to Outlook contacts.

    This ensures contacts stay synchronized even if real-time signals fail.
    Also handles cleanup of orphaned contacts.

    Runs daily at 3:00 AM UTC to avoid peak usage hours.
    """
    utc_timestamp = datetime.utcnow().isoformat()

    if timer.past_due:
        logger.warning(f"Timer is past due! Current time: {utc_timestamp}")

    logger.info(f"Starting daily Outlook contact sync at {utc_timestamp}")

    # Get configuration from environment
    command_url = os.environ.get('DJANGO_MANAGEMENT_COMMAND_URL')
    api_key = os.environ.get('ADMIN_API_KEY')
    sync_enabled = os.environ.get('OUTLOOK_CONTACT_SYNC_ENABLED', '').lower() == 'true'

    # Validation
    if not sync_enabled:
        logger.info("OUTLOOK_CONTACT_SYNC_ENABLED is not true - skipping sync")
        return

    if not command_url:
        logger.error("DJANGO_MANAGEMENT_COMMAND_URL not configured")
        return

    if not api_key:
        logger.error("ADMIN_API_KEY not configured")
        return

    try:
        # Call Django admin API endpoint to trigger sync
        response = requests.post(
            command_url,
            json={
                'command': 'sync_contacts_to_outlook',
                'args': [],
                'options': {}
            },
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            timeout=300  # 5 minute timeout for large syncs
        )

        response.raise_for_status()
        result = response.json()

        if result.get('success'):
            stats = result.get('stats', {})
            logger.info(
                f"Daily contact sync completed successfully:\n"
                f"  Total profiles: {stats.get('total', 'N/A')}\n"
                f"  Synced: {stats.get('synced', 'N/A')}\n"
                f"  Skipped: {stats.get('skipped', 'N/A')}\n"
                f"  Errors: {stats.get('errors', 'N/A')}"
            )
        else:
            error_msg = result.get('error', 'Unknown error')
            logger.error(f"Daily contact sync failed: {error_msg}")

    except requests.exceptions.Timeout:
        logger.error("Daily contact sync timed out after 5 minutes")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Django management command: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during daily contact sync: {e}")
