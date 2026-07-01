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
    meta_error_code = err.get("code")
    # Log the HTTP status and Meta's numeric error code (both safe — a scanner
    # can't mistake an HTTP status or a Meta error code like 190/132001 for a
    # passcode), never the free-text error message/payload, which in this OTP
    # context is indistinguishable from the code itself. Logged at ERROR so it
    # clears the ERROR-only console handler in production: the view turns this
    # into a 502, and without the status/code here that 502 is undiagnosable
    # from the console log stream (App Insights aside).
    logger.error(
        "WhatsApp OTP send failed: http=%s meta_code=%s not_on_whatsapp=%s",
        resp.status_code,
        meta_error_code,
        meta_error_code == ERROR_NOT_ON_WHATSAPP,
    )
    return WhatsAppSendResult(
        ok=False,
        error_code=meta_error_code,
        error_message=err.get("message") or f"HTTP {resp.status_code}",
    )


def mark_not_on_whatsapp(phone_number: str) -> int:
    """Flag every CrushProfile with this number as not-on-WhatsApp (issue #519).

    Called when Meta reports ``ERROR_NOT_ON_WHATSAPP`` for a send (synchronously
    in the send view, or asynchronously via the status webhook). Setting the flag
    stops us re-attempting WhatsApp for that number and lets notifications fall
    back to email. Returns the number of profiles updated.

    Matches on **digits only**: verified numbers are stored canonically
    (``+<digits>``) but form-entered ones may carry the spaces/dashes/parens the
    model's validator allows, while Meta reports the ``+``-stripped canonical
    form — so both the stored value and the recipient are reduced to bare digits
    before comparing.
    """
    from django.db.models import F, Value
    from django.db.models.functions import Replace

    from crush_lu.models.profiles import CrushProfile

    target_digits = "".join(ch for ch in (phone_number or "") if ch.isdigit())
    if not target_digits:
        return 0

    digits_expr = F("phone_number")
    for ch in (" ", "-", "(", ")", ".", "+"):
        digits_expr = Replace(digits_expr, Value(ch), Value(""))

    pks = list(
        CrushProfile.objects.filter(not_on_whatsapp=False)
        .annotate(_pn_digits=digits_expr)
        .filter(_pn_digits=target_digits)
        .values_list("pk", flat=True)
    )
    if not pks:
        return 0
    return CrushProfile.objects.filter(pk__in=pks).update(not_on_whatsapp=True)


def can_send_whatsapp(profile) -> bool:
    """True when WhatsApp should be attempted for this profile (issue #519).

    False once Meta has told us the number isn't on WhatsApp, or when there's no
    verified phone to send to — callers should route to email instead.
    """
    return bool(
        profile
        and getattr(profile, "phone_number", "")
        and getattr(profile, "phone_verified", False)
        and not getattr(profile, "not_on_whatsapp", False)
    )
