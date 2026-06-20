"""WhatsApp OTP delivery via the Meta Cloud API.

This is the *delivery* half of WhatsApp phone verification. Meta does NOT
generate or check the code — it is purely a transport, like SMS. We generate
and verify the code ourselves (see ``crush_lu.models.PhoneOTP`` and
``views_phone_verification``); this module only sends an approved
Authentication-category template carrying the code.

The template is named ``<WHATSAPP_OTP_TEMPLATE_PREFIX>_phone_verification`` and
exists once per language (en/de/fr) under that single name, so the language
code selects the localized variant at send time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v25.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
META_TIMEOUT = 15

# Meta error code returned when the recipient number is not a WhatsApp user.
# Callers use this to fall back to SMS (see issue #519).
ERROR_NOT_ON_WHATSAPP = 131026


@dataclass(frozen=True)
class WhatsAppSendResult:
    ok: bool
    wa_message_id: str = ""
    error_code: int | None = None
    error_message: str = ""

    @property
    def not_on_whatsapp(self) -> bool:
        return self.error_code == ERROR_NOT_ON_WHATSAPP


def is_configured() -> bool:
    """True when the Meta credentials needed to send are present."""
    return bool(
        getattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "")
        and getattr(settings, "META_PHONE_NUMBER_ID", "")
    )


def otp_template_name() -> str:
    prefix = getattr(settings, "WHATSAPP_OTP_TEMPLATE_PREFIX", "crush_staging")
    return f"{prefix}_phone_verification"


def _normalize_recipient(recipient: str) -> str:
    """Meta accepts both ``+352...`` and ``352...``; strip the leading ``+``."""
    return (recipient or "").lstrip("+").strip()


def send_otp(recipient: str, code: str, language: str = "en") -> WhatsAppSendResult:
    """Send the one-time passcode via the Authentication copy-code template.

    For authentication templates the code is passed twice: once as the body
    ``{{1}}`` parameter and once as the copy-code button parameter (so tapping
    "Copy code" copies the right value). Both must equal ``code``.
    """
    if not is_configured():
        return WhatsAppSendResult(
            ok=False, error_message="WhatsApp integration is not configured."
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": _normalize_recipient(recipient),
        "type": "template",
        "template": {
            "name": otp_template_name(),
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": code}],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": 0,
                    "parameters": [{"type": "text", "text": code}],
                },
            ],
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
        logger.warning("WhatsApp OTP send transport error: %s", exc.__class__.__name__)
        return WhatsAppSendResult(
            ok=False, error_message=f"Transport error: {exc.__class__.__name__}"
        )

    try:
        body = resp.json()
    except ValueError:
        body = {}

    if resp.ok:
        wa_id = ""
        try:
            wa_id = body["messages"][0]["id"]
        except (KeyError, IndexError, TypeError):
            logger.warning("WhatsApp OTP 2xx response missing messages[0].id")
        return WhatsAppSendResult(ok=True, wa_message_id=wa_id)

    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    code_val = err.get("code")
    logger.warning(
        "WhatsApp OTP send failed: http=%s code=%s", resp.status_code, code_val
    )
    return WhatsAppSendResult(
        ok=False,
        error_code=code_val,
        error_message=err.get("message") or f"HTTP {resp.status_code}",
    )
