import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Fields that should trigger a wallet pass update when changed
WALLET_UPDATE_PROFILE_FIELDS = {
    "referral_points",
    "membership_tier",
    "show_photo_on_wallet",
    "photo_1",
    "display_name",
    "first_name",
    "last_name",
    "show_full_name",
}


def _trigger_apple_pass_refresh(profile):
    """
    Trigger Apple Wallet pass refresh via APNS push notification.

    When Apple Wallet receives this silent push, it calls our PassKit web service
    endpoint to fetch the updated pass.

    Args:
        profile: CrushProfile instance with apple_pass_serial set
    """
    if not profile.apple_pass_serial:
        return

    pass_type_id = getattr(settings, "WALLET_APPLE_PASS_TYPE_IDENTIFIER", None)
    if not pass_type_id:
        logger.warning(
            "Cannot trigger Apple pass refresh: WALLET_APPLE_PASS_TYPE_IDENTIFIER not configured"
        )
        return

    try:
        from crush_lu.wallet.passkit_apns import send_passkit_push_notifications

        result = send_passkit_push_notifications(
            pass_type_identifier=pass_type_id,
            serial_number=profile.apple_pass_serial,
        )

        if result["total"] > 0:
            logger.info(
                f"Apple Wallet pass refresh triggered for user {profile.user_id}: "
                f"success={result['success']}, failed={result['failed']}, total={result['total']}"
            )
        else:
            logger.debug(
                f"No Apple Wallet device registrations for user {profile.user_id}"
            )

    except Exception as e:
        logger.error(
            f"Error triggering Apple pass refresh for user {profile.user_id}: {e}"
        )


def _trigger_google_wallet_object_update(profile):
    """
    Trigger Google Wallet object update via REST API.

    For Google Wallet, we PATCH the object to update its content.
    Uses the Google Wallet REST API with service account authentication.

    Args:
        profile: CrushProfile instance with google_wallet_object_id set
    """
    if not profile.google_wallet_object_id:
        return

    try:
        from crush_lu.wallet.google_api import update_google_wallet_pass

        result = update_google_wallet_pass(profile)

        if result["success"]:
            logger.info(
                f"Google Wallet pass updated for user {profile.user_id}: "
                f"object_id={profile.google_wallet_object_id}"
            )
        else:
            logger.warning(
                f"Google Wallet pass update failed for user {profile.user_id}: "
                f"{result['message']}"
            )

    except Exception as e:
        logger.error(
            f"Error updating Google Wallet pass for user {profile.user_id}: {e}"
        )


def trigger_wallet_pass_updates(profile):
    """
    Trigger updates for both Apple and Google Wallet passes.

    Args:
        profile: CrushProfile instance
    """
    _trigger_apple_pass_refresh(profile)
    _trigger_google_wallet_object_update(profile)
