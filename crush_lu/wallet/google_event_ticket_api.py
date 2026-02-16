"""
Google Wallet Event Ticket REST API client.

Provides functions to create, update, expire, and complete EventTicketClass
and EventTicketObject resources via the Google Wallet REST API.

Reuses authentication from google_api.py.
"""

import logging

import httpx
from django.conf import settings

from .google_api import GOOGLE_WALLET_API_BASE, _get_access_token
from .google_event_ticket import _build_event_ticket_class_id

logger = logging.getLogger(__name__)


def create_event_ticket_class(event):
    """
    Create an EventTicketClass for a published event.

    Called automatically when an event is published. The class defines
    the event details shared across all ticket objects for this event.

    Args:
        event: MeetupEvent instance

    Returns:
        dict: {"success": bool, "message": str, "class_id": str|None}
    """
    from .google_wallet import _require_setting

    try:
        issuer_id = _require_setting("WALLET_GOOGLE_ISSUER_ID")
    except Exception as e:
        return {"success": False, "message": str(e), "class_id": None}

    class_id = _build_event_ticket_class_id(event)

    # Logo URL
    logo_url = getattr(settings, "WALLET_GOOGLE_LOGO_URL", None)
    if not logo_url:
        logo_url = "https://crush.lu/static/crush_lu/icons/android-launchericon-192-192.png"

    class_payload = {
        "id": class_id,
        "issuerName": "Crush.lu",
        "eventName": {
            "defaultValue": {"language": "en-US", "value": event.title}
        },
        "venue": {
            "name": {
                "defaultValue": {"language": "en-US", "value": event.location}
            },
            "address": {
                "defaultValue": {"language": "en-US", "value": event.address}
            },
        },
        "dateTime": {
            "start": event.date_time.isoformat(),
        },
        "reviewStatus": "UNDER_REVIEW",
        "hexBackgroundColor": "#9B59B6",
        "logo": {
            "sourceUri": {"uri": logo_url},
            "contentDescription": {
                "defaultValue": {"language": "en-US", "value": "Crush.lu"}
            },
        },
    }

    if event.duration_minutes:
        class_payload["dateTime"]["end"] = event.end_time.isoformat()

    try:
        access_token = _get_access_token()
        url = f"{GOOGLE_WALLET_API_BASE}/eventTicketClass"

        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=class_payload,
            )

            if response.status_code in (200, 201):
                # Save class ID to event
                event.google_wallet_event_class_id = class_id
                event.save(update_fields=["google_wallet_event_class_id"])
                logger.info(
                    "Created EventTicketClass %s for event %s",
                    class_id,
                    event.id,
                )
                return {"success": True, "message": "Class created", "class_id": class_id}

            elif response.status_code == 409:
                # Class already exists - update it
                event.google_wallet_event_class_id = class_id
                event.save(update_fields=["google_wallet_event_class_id"])
                logger.info(
                    "EventTicketClass %s already exists for event %s",
                    class_id,
                    event.id,
                )
                return {"success": True, "message": "Class already exists", "class_id": class_id}

            else:
                logger.error(
                    "Failed to create EventTicketClass for event %s: %s - %s",
                    event.id,
                    response.status_code,
                    response.text,
                )
                return {"success": False, "message": f"API error: {response.status_code}", "class_id": None}

    except Exception as e:
        logger.exception("Error creating EventTicketClass for event %s: %s", event.id, e)
        return {"success": False, "message": str(e), "class_id": None}


def update_event_ticket(registration):
    """
    Update an existing EventTicketObject (e.g., event details changed).

    Args:
        registration: EventRegistration with google_wallet_ticket_object_id

    Returns:
        dict: {"success": bool, "message": str}
    """
    if not registration.google_wallet_ticket_object_id:
        return {"success": False, "message": "No ticket object ID"}

    try:
        access_token = _get_access_token()
        encoded_id = registration.google_wallet_ticket_object_id.replace(".", "%2E")
        url = f"{GOOGLE_WALLET_API_BASE}/eventTicketObject/{encoded_id}"

        event = registration.event
        event_date = event.date_time.strftime("%A, %B %d, %Y")
        event_time = event.date_time.strftime("%I:%M %p")

        update_payload = {
            "textModulesData": [
                {"id": "date", "header": "Date", "body": event_date},
                {"id": "time", "header": "Time", "body": event_time},
                {"id": "location", "header": "Location", "body": f"{event.location}\n{event.address}"},
            ],
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.patch(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=update_payload,
            )

            if response.status_code == 200:
                logger.info("Updated event ticket for registration %s", registration.id)
                return {"success": True, "message": "Ticket updated"}
            elif response.status_code == 404:
                return {"success": False, "message": "Ticket not found"}
            else:
                logger.error(
                    "Failed to update event ticket %s: %s",
                    registration.id,
                    response.status_code,
                )
                return {"success": False, "message": f"API error: {response.status_code}"}

    except Exception as e:
        logger.exception("Error updating event ticket for registration %s: %s", registration.id, e)
        return {"success": False, "message": str(e)}


def expire_event_ticket(registration):
    """
    Mark an event ticket as expired (registration cancelled).

    Args:
        registration: EventRegistration with google_wallet_ticket_object_id

    Returns:
        dict: {"success": bool, "message": str}
    """
    return _patch_ticket_state(registration, "expired")


def complete_event_ticket(registration):
    """
    Mark an event ticket as completed (user checked in / attended).

    Args:
        registration: EventRegistration with google_wallet_ticket_object_id

    Returns:
        dict: {"success": bool, "message": str}
    """
    return _patch_ticket_state(registration, "completed")


def _patch_ticket_state(registration, state):
    """
    PATCH the state of an EventTicketObject.

    Args:
        registration: EventRegistration with google_wallet_ticket_object_id
        state: New state ("active", "completed", "expired", "inactive")

    Returns:
        dict: {"success": bool, "message": str}
    """
    if not registration.google_wallet_ticket_object_id:
        return {"success": False, "message": "No ticket object ID"}

    try:
        access_token = _get_access_token()
        encoded_id = registration.google_wallet_ticket_object_id.replace(".", "%2E")
        url = f"{GOOGLE_WALLET_API_BASE}/eventTicketObject/{encoded_id}"

        with httpx.Client(timeout=30.0) as client:
            response = client.patch(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"state": state},
            )

            if response.status_code == 200:
                logger.info(
                    "Set event ticket state to '%s' for registration %s",
                    state,
                    registration.id,
                )
                return {"success": True, "message": f"Ticket state set to {state}"}
            elif response.status_code == 404:
                return {"success": False, "message": "Ticket not found"}
            else:
                logger.error(
                    "Failed to set ticket state for registration %s: %s",
                    registration.id,
                    response.status_code,
                )
                return {"success": False, "message": f"API error: {response.status_code}"}

    except Exception as e:
        logger.exception(
            "Error setting ticket state for registration %s: %s",
            registration.id,
            e,
        )
        return {"success": False, "message": str(e)}
