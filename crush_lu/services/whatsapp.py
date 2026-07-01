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

# Stable, non-sensitive labels for the Meta send errors that actually tell an
# operator where to look. Logging a label from this controlled map (rather than
# the raw response value) keeps the send-failure diagnosable without echoing
# anything response-derived — see the logging note in send_otp(). Unmapped codes
# log as "other"; the HTTP status still narrows those down.
_META_ERROR_REASONS = {
    ERROR_NOT_ON_WHATSAPP: "not_on_whatsapp",
    0: "auth_or_permission",  # generic auth/permission failure
    3: "capability_or_permission",
    10: "permission_denied",
    190: "access_token_invalid",  # expired/invalid token -> use System User token
    100: "invalid_parameter",
    131000: "generic_send_error",
    131008: "missing_required_param",
    131009: "invalid_param_value",
    131047: "re_engagement_required",
    131056: "pair_rate_limit",
    132000: "template_param_mismatch",
    132001: "template_not_found",  # template/language not approved
    132005: "template_hydrated_text_too_long",
    132007: "template_format_policy_violation",
    132012: "template_param_format_mismatch",
    132015: "template_paused",
    132016: "template_disabled",
    133010: "number_not_registered",
    80007: "throughput_rate_limit",
}


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
    if meta_error_code == ERROR_NOT_ON_WHATSAPP:
        # Expected, not an error: the view returns 422 and the client offers SMS.
        # Log at INFO (captured by App Insights, below the ERROR-only console
        # handler) so routine fallbacks don't raise console alarms.
        logger.info("WhatsApp OTP not delivered: recipient not on WhatsApp")
    else:
        # Genuine send failure -> the view returns 502. Translate Meta's numeric
        # error to a stable label from the controlled map and log THAT plus the
        # HTTP status — never any response-derived value, which in this OTP
        # context is indistinguishable from the passcode (and which CodeQL flags
        # as clear-text logging of sensitive data). ERROR so it clears the
        # ERROR-only production console handler; without it the 502 is
        # undiagnosable from the console stream.
        logger.error(
            "WhatsApp OTP send failed: http=%s reason=%s",
            resp.status_code,
            _META_ERROR_REASONS.get(meta_error_code, "other"),
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
