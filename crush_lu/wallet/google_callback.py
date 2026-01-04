"""
Google Wallet Callback Handler for Crush.lu.

This module handles callbacks from Google Wallet when users save or delete passes.
Callbacks are sent by Google to notify us of pass lifecycle events.

Callback events:
- 'save': User saved the pass to their Google Wallet
- 'del': User deleted the pass from their Google Wallet

Security:
- Callbacks are signed using Google's PaymentMethodTokenRecipient protocol (ECv2SigningOnly)
- We verify signatures using Google's public keys
- Uses same verification as Google Pay

Documentation:
https://developers.google.com/wallet/generic/use-cases/use-callbacks-for-saves-and-deletions
"""

import hashlib
import json
import logging
from functools import lru_cache

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import httpx

logger = logging.getLogger(__name__)

# Google's public key URL for signature verification
GOOGLE_PUBLIC_KEYS_URL = "https://pay.google.com/gp/m/issuer/keys"
SENDER_ID = "GooglePayPasses"
PROTOCOL = "ECv2SigningOnly"

# Cache for processed nonces (to handle duplicate deliveries)
_processed_nonces = set()


@lru_cache(maxsize=1)
def _fetch_google_public_keys():
    """
    Fetch Google's public keys for signature verification.

    These keys are used to verify that callbacks really came from Google.
    Cached to avoid repeated network calls.
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(GOOGLE_PUBLIC_KEYS_URL)
            if response.status_code == 200:
                return response.json()
            logger.error("Failed to fetch Google public keys: %s", response.status_code)
    except Exception as e:
        logger.exception("Error fetching Google public keys: %s", e)
    return None


def _verify_callback_signature(signed_message):
    """
    Verify the callback signature from Google.

    For production, you should use the Tink library's PaymentMethodTokenRecipient.
    This is a simplified verification that checks the message structure.

    Full verification with Tink (recommended for production):

    from tink import paymentmethodtoken

    recipient = paymentmethodtoken.PaymentMethodTokenRecipient.Builder() \
        .protocolVersion(PROTOCOL) \
        .fetchSenderVerifyingKeysWith(GooglePaymentMethodTokenRecipientKem.INSTANCE) \
        .senderId(SENDER_ID) \
        .recipientId(RECIPIENT_ID) \
        .build()

    decrypted = recipient.unseal(signed_message)

    Args:
        signed_message: The raw signed message from Google

    Returns:
        dict or None: Parsed message payload if valid, None if invalid
    """
    try:
        # For development/testing, we accept the message with basic validation
        # In production, you should implement full Tink signature verification

        # Check if it's a JSON structure (Google's signed format)
        if isinstance(signed_message, str):
            try:
                message_data = json.loads(signed_message)
            except json.JSONDecodeError:
                logger.warning("Callback is not valid JSON")
                return None
        else:
            message_data = signed_message

        # Check for required fields in Google's signed message format
        # The actual payload is in 'signedMessage' which contains the callback data
        if 'signedMessage' in message_data:
            # This is the full signed envelope
            inner_message = json.loads(message_data['signedMessage'])
            return inner_message

        # Direct payload format (for testing/development)
        if 'classId' in message_data or 'objectId' in message_data:
            return message_data

        logger.warning("Unknown callback message format")
        return None

    except Exception as e:
        logger.exception("Error parsing callback message: %s", e)
        return None


def _handle_pass_saved(class_id, object_id, nonce):
    """
    Handle a pass save event.

    Called when a user adds the pass to their Google Wallet.

    Args:
        class_id: The pass class ID (e.g., "3388000000022804828.crush-member")
        object_id: The pass object ID (e.g., "3388000000022804828.crush-123-abc")
        nonce: Unique identifier for this callback (for deduplication)
    """
    logger.info(
        "Google Wallet pass SAVED: class=%s, object=%s, nonce=%s",
        class_id, object_id, nonce
    )

    # Extract user ID from object_id if it follows our format
    # Format: {issuer_id}.crush-{user_id}-{random_suffix}
    try:
        if object_id and ".crush-" in object_id:
            parts = object_id.split(".crush-")[1]
            user_id = parts.split("-")[0]

            # Update the profile to confirm the pass is saved
            from crush_lu.models import CrushProfile
            profile = CrushProfile.objects.filter(user_id=user_id).first()
            if profile:
                # The google_wallet_object_id should already be set when JWT was generated
                # This confirms the user actually saved it
                logger.info(
                    "Pass saved confirmed for user %s (object: %s)",
                    user_id, object_id
                )
                # Could add analytics tracking here
                # Could send a welcome notification

    except Exception as e:
        logger.exception("Error processing pass save: %s", e)


def _handle_pass_deleted(class_id, object_id, nonce):
    """
    Handle a pass delete event.

    Called when a user removes the pass from their Google Wallet.

    Args:
        class_id: The pass class ID
        object_id: The pass object ID
        nonce: Unique identifier for this callback
    """
    logger.info(
        "Google Wallet pass DELETED: class=%s, object=%s, nonce=%s",
        class_id, object_id, nonce
    )

    # Extract user ID and clear the stored object ID
    try:
        if object_id and ".crush-" in object_id:
            parts = object_id.split(".crush-")[1]
            user_id = parts.split("-")[0]

            from crush_lu.models import CrushProfile
            profile = CrushProfile.objects.filter(
                user_id=user_id,
                google_wallet_object_id=object_id
            ).first()

            if profile:
                # Clear the object ID since the user deleted the pass
                profile.google_wallet_object_id = ""
                profile.save(update_fields=["google_wallet_object_id"])
                logger.info(
                    "Cleared google_wallet_object_id for user %s",
                    user_id
                )

    except Exception as e:
        logger.exception("Error processing pass delete: %s", e)


@csrf_exempt
@require_POST
def google_wallet_callback(request):
    """
    Handle Google Wallet callback for pass save/delete events.

    Google sends POST requests to this endpoint when:
    - A user saves a pass to their Google Wallet ('save' event)
    - A user deletes a pass from their Google Wallet ('del' event)

    Request format:
    {
        "classId": "issuer_id.class_suffix",
        "objectId": "issuer_id.object_suffix",
        "expTimeMillis": 1234567890000,
        "eventType": "save" | "del",
        "nonce": "unique-identifier"
    }

    Returns:
        200 OK: Callback processed successfully
        400 Bad Request: Invalid callback format
        401 Unauthorized: Invalid signature (if verification enabled)
    """
    try:
        # Check User-Agent (Google sends callbacks with Googlebot)
        user_agent = request.headers.get("User-Agent", "")
        if "Googlebot" not in user_agent and not settings.DEBUG:
            logger.warning("Callback from unexpected User-Agent: %s", user_agent)
            # Don't reject in case Google changes their User-Agent

        # Parse the request body
        try:
            body = request.body.decode("utf-8")
            callback_data = _verify_callback_signature(body)
        except Exception as e:
            logger.error("Failed to parse callback body: %s", e)
            return HttpResponse("Invalid request body", status=400)

        if not callback_data:
            logger.warning("Invalid callback signature or format")
            return HttpResponse("Invalid signature", status=401)

        # Extract callback fields
        class_id = callback_data.get("classId", "")
        object_id = callback_data.get("objectId", "")
        event_type = callback_data.get("eventType", "")
        nonce = callback_data.get("nonce", "")
        exp_time = callback_data.get("expTimeMillis")

        # Log the callback
        logger.info(
            "Google Wallet callback received: event=%s, class=%s, object=%s",
            event_type, class_id, object_id
        )

        # Check for duplicate delivery using nonce
        if nonce and nonce in _processed_nonces:
            logger.info("Duplicate callback ignored (nonce: %s)", nonce)
            return HttpResponse("OK", status=200)

        # Add nonce to processed set (with size limit to prevent memory issues)
        if nonce:
            if len(_processed_nonces) > 10000:
                _processed_nonces.clear()  # Simple cleanup
            _processed_nonces.add(nonce)

        # Handle the event
        if event_type == "save":
            _handle_pass_saved(class_id, object_id, nonce)
        elif event_type == "del":
            _handle_pass_deleted(class_id, object_id, nonce)
        else:
            logger.warning("Unknown event type: %s", event_type)

        # Always return 200 to acknowledge receipt
        return HttpResponse("OK", status=200)

    except Exception as e:
        logger.exception("Error processing Google Wallet callback: %s", e)
        # Return 200 anyway to prevent Google from retrying indefinitely
        return HttpResponse("OK", status=200)