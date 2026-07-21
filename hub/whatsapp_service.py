"""WhatsApp Cloud API send service.

Extracted from ``hub/views_whatsapp.py`` so both the hub CRM API and the
Coach Panel campaign dispatcher (``crush_lu/services/campaigns.py``) share one
implementation of the Meta template send: create a QUEUED ``WhatsAppMessage``
row, POST to the Graph API, record SENT/FAILED plus status history, and flag
not-on-WhatsApp recipients. Delivery/read transitions keep arriving through
the webhook in ``views_whatsapp.py`` — they key on ``wa_message_id`` and are
independent of who initiated the send.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
from django.conf import settings
from django.core.cache import cache

from crush_lu.services.whatsapp import ERROR_NOT_ON_WHATSAPP, mark_not_on_whatsapp

from .models import WhatsAppMessage

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v25.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
META_TIMEOUT = 15

TEMPLATES_CACHE_KEY = "hub:whatsapp:approved-templates"
TEMPLATES_CACHE_SECONDS = 600
TEMPLATES_MAX_PAGES = 20


class TemplatesFetchError(Exception):
    """Raised when the approved-templates list cannot be fetched from Meta."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def meta_settings_ok() -> bool:
    return bool(
        getattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "")
        and getattr(settings, "META_PHONE_NUMBER_ID", "")
        and getattr(settings, "META_WABA_ID", "")
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def maybe_flag_not_on_whatsapp(recipient: str, error_code) -> None:
    """Persist the not_on_whatsapp flag when Meta reports code 131026 (issue #519).

    Never lets a lookup/update failure disrupt the send response or webhook ack.
    """
    if error_code != ERROR_NOT_ON_WHATSAPP:
        return
    try:
        mark_not_on_whatsapp(recipient)
    except Exception:  # noqa: BLE001 — best-effort side channel
        logger.warning("Failed to flag not_on_whatsapp recipient", exc_info=True)


def normalize_recipient(recipient: str) -> str:
    """Meta accepts both `+352...` and `352...`; strip the leading `+`."""
    return (recipient or "").lstrip("+").strip()


def build_components(parameters: dict) -> list[dict]:
    """Convert {"1": "v1", "2": "v2"} → Meta body parameters payload."""
    if not parameters:
        return []
    ordered = sorted(parameters.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0)
    return [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": str(v)} for _, v in ordered],
        }
    ]


def send_whatsapp_template(*, sender, recipient, template_name, language,
                           parameters) -> WhatsAppMessage:
    """Send one approved Meta template message and record its lifecycle.

    Always returns the persisted ``WhatsAppMessage`` — callers read
    ``message.status`` (SENT/FAILED) and ``message.status_history`` for the
    outcome; nothing is raised for transport or Meta-side errors.
    ``sender`` is the admin User initiating the send (the model's ``user``
    field is the sender, not the recipient).
    """
    message = WhatsAppMessage.objects.create(
        user=sender,
        recipient=recipient,
        template_name=template_name,
        language=language,
        parameters=parameters,
        status=WhatsAppMessage.Status.QUEUED,
        status_history=[{"status": "queued", "timestamp": now_iso()}],
    )

    payload = {
        "messaging_product": "whatsapp",
        "to": normalize_recipient(recipient),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": build_components(parameters),
        },
    }

    url = f"{GRAPH_BASE}/{settings.META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=META_TIMEOUT)
    except requests.RequestException as exc:
        logger.exception("WhatsApp send transport error")
        message.status = WhatsAppMessage.Status.FAILED
        message.status_history = message.status_history + [
            {
                "status": "failed",
                "timestamp": now_iso(),
                "error_message": f"Transport error: {exc.__class__.__name__}",
            }
        ]
        message.save(update_fields=["status", "status_history", "updated_at"])
        return message

    body = {}
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}

    if resp.ok:
        wa_id = ""
        try:
            wa_id = body["messages"][0]["id"]
        except (KeyError, IndexError, TypeError):
            logger.warning("WhatsApp send 2xx response missing messages[0].id")
        message.wa_message_id = wa_id
        message.status = WhatsAppMessage.Status.SENT
        message.status_history = message.status_history + [
            {"status": "sent", "timestamp": now_iso()}
        ]
        message.save(
            update_fields=[
                "wa_message_id",
                "status",
                "status_history",
                "updated_at",
            ]
        )
        return message

    # Meta returned 4xx/5xx — record the error inline so callers can show it.
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    message.status = WhatsAppMessage.Status.FAILED
    message.status_history = message.status_history + [
        {
            "status": "failed",
            "timestamp": now_iso(),
            "error_code": err.get("code"),
            "error_message": err.get("message") or f"HTTP {resp.status_code}",
        }
    ]
    message.save(update_fields=["status", "status_history", "updated_at"])
    maybe_flag_not_on_whatsapp(message.recipient, err.get("code"))
    return message


def fetch_approved_templates(use_cache: bool = True) -> list[dict]:
    """List the WABA's message templates from Meta.

    Returns ``[]`` when the integration is not configured. Raises
    ``TemplatesFetchError`` on transport failures or non-2xx responses.
    Successful results are cached briefly so composer previews don't hammer
    the Graph API; the hub CRM view bypasses the cache to keep its behavior
    unchanged.
    """
    if not meta_settings_ok():
        return []

    if use_cache:
        cached = cache.get(TEMPLATES_CACHE_KEY)
        if cached is not None:
            return cached

    headers = {
        "Authorization": f"Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}",
    }
    items = []
    url = f"{GRAPH_BASE}/{settings.META_WABA_ID}/message_templates"
    params = {"limit": 100}
    # Follow Meta's paging cursors — a WABA can hold more than one page of
    # templates and the composer must see (and validate against) all of them.
    # Page cap is a runaway guard, far above any real template count.
    for _ in range(TEMPLATES_MAX_PAGES):
        try:
            resp = requests.get(
                url, headers=headers, params=params, timeout=META_TIMEOUT,
            )
        except requests.RequestException:
            logger.exception("WhatsApp templates fetch transport error")
            raise TemplatesFetchError("Unable to reach Meta Graph API.")

        if not resp.ok:
            raise TemplatesFetchError(f"Meta returned HTTP {resp.status_code}.")

        body = resp.json() if resp.content else {}
        for raw in body.get("data", []):
            items.append(
                {
                    "name": raw.get("name", ""),
                    "language": raw.get("language", ""),
                    "category": raw.get("category", ""),
                    "status": raw.get("status", ""),
                    "components": raw.get("components", []),
                }
            )

        next_url = (body.get("paging") or {}).get("next")
        if not next_url:
            break
        # paging.next is a complete URL (cursor included) — no extra params.
        url, params = next_url, None

    if use_cache:
        cache.set(TEMPLATES_CACHE_KEY, items, TEMPLATES_CACHE_SECONDS)
    return items
