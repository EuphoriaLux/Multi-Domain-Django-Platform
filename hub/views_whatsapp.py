"""WhatsApp Cloud API integration for the hub.

Three authenticated views (send / list templates / list messages) plus a public
webhook receiver for Meta's status callbacks. Meta credentials are read from
settings and never sent to the browser.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timezone

import requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status as http_status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import WhatsAppMessage
from .serializers import WhatsAppMessageSerializer

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v25.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
META_TIMEOUT = 15


def _meta_settings_ok() -> bool:
    return bool(
        getattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "")
        and getattr(settings, "META_PHONE_NUMBER_ID", "")
        and getattr(settings, "META_WABA_ID", "")
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_recipient(recipient: str) -> str:
    """Meta accepts both `+352...` and `352...`; strip the leading `+`."""
    return (recipient or "").lstrip("+").strip()


def _build_components(parameters: dict) -> list[dict]:
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


class WhatsAppSendView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        if not _meta_settings_ok():
            return Response(
                {"detail": "WhatsApp integration is not configured on the server."},
                status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        data = request.data or {}
        template_name = (data.get("template_name") or "").strip()
        language = (data.get("language") or "").strip()
        recipient = (data.get("to") or "").strip()
        parameters = data.get("parameters") or {}

        if not template_name or not language or not recipient:
            return Response(
                {"detail": "template_name, language, and to are required."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(parameters, dict):
            return Response(
                {"detail": "parameters must be an object keyed by placeholder index."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        message = WhatsAppMessage.objects.create(
            user=request.user,
            recipient=recipient,
            template_name=template_name,
            language=language,
            parameters=parameters,
            status=WhatsAppMessage.Status.QUEUED,
            status_history=[{"status": "queued", "timestamp": _now_iso()}],
        )

        payload = {
            "messaging_product": "whatsapp",
            "to": _normalize_recipient(recipient),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": _build_components(parameters),
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
                    "timestamp": _now_iso(),
                    "error_message": f"Transport error: {exc.__class__.__name__}",
                }
            ]
            message.save(update_fields=["status", "status_history", "updated_at"])
            return Response(
                {"message": WhatsAppMessageSerializer(message).data},
                status=http_status.HTTP_502_BAD_GATEWAY,
            )

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
                shape = (
                    sorted(body.keys())
                    if isinstance(body, dict)
                    else type(body).__name__
                )
                logger.warning(
                    "WhatsApp send 2xx response missing messages[0].id (shape=%s)",
                    shape,
                )
            message.wa_message_id = wa_id
            message.status = WhatsAppMessage.Status.SENT
            message.status_history = message.status_history + [
                {"status": "sent", "timestamp": _now_iso()}
            ]
            message.save(
                update_fields=[
                    "wa_message_id",
                    "status",
                    "status_history",
                    "updated_at",
                ]
            )
            return Response(
                {"message": WhatsAppMessageSerializer(message).data},
                status=http_status.HTTP_201_CREATED,
            )

        # Meta returned 4xx/5xx — surface the error inline so the UI shows it.
        err = (body.get("error") or {}) if isinstance(body, dict) else {}
        message.status = WhatsAppMessage.Status.FAILED
        message.status_history = message.status_history + [
            {
                "status": "failed",
                "timestamp": _now_iso(),
                "error_code": err.get("code"),
                "error_message": err.get("message") or f"HTTP {resp.status_code}",
            }
        ]
        message.save(update_fields=["status", "status_history", "updated_at"])
        return Response(
            {"message": WhatsAppMessageSerializer(message).data},
            status=http_status.HTTP_502_BAD_GATEWAY,
        )


class WhatsAppTemplatesView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        if not _meta_settings_ok():
            return Response({"items": []})

        url = f"{GRAPH_BASE}/{settings.META_WABA_ID}/message_templates"
        headers = {
            "Authorization": f"Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}",
        }
        try:
            resp = requests.get(
                url,
                headers=headers,
                params={"limit": 100},
                timeout=META_TIMEOUT,
            )
        except requests.RequestException:
            logger.exception("WhatsApp templates fetch transport error")
            return Response(
                {"detail": "Unable to reach Meta Graph API."},
                status=http_status.HTTP_502_BAD_GATEWAY,
            )

        if not resp.ok:
            return Response(
                {"detail": f"Meta returned HTTP {resp.status_code}."},
                status=http_status.HTTP_502_BAD_GATEWAY,
            )

        body = resp.json() if resp.content else {}
        items = []
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
        return Response({"items": items})


class WhatsAppMessagesView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = WhatsAppMessage.objects.filter(user=request.user)
        since = request.query_params.get("since")
        if since:
            try:
                ts = datetime.fromisoformat(since.replace("Z", "+00:00"))
                qs = qs.filter(created_at__gte=ts)
            except ValueError:
                pass
        serializer = WhatsAppMessageSerializer(qs, many=True)
        return Response({"items": serializer.data})


# --- Webhook (public; signature-verified) ----------------------------------

# Meta status → our model status
_WEBHOOK_STATUS_MAP = {
    "sent": WhatsAppMessage.Status.SENT,
    "delivered": WhatsAppMessage.Status.DELIVERED,
    "read": WhatsAppMessage.Status.READ,
    "failed": WhatsAppMessage.Status.FAILED,
}

# Order used to avoid downgrading a message that already advanced.
_STATUS_RANK = {
    WhatsAppMessage.Status.QUEUED: 0,
    WhatsAppMessage.Status.SENT: 1,
    WhatsAppMessage.Status.DELIVERED: 2,
    WhatsAppMessage.Status.READ: 3,
    WhatsAppMessage.Status.FAILED: 4,
}


@method_decorator(csrf_exempt, name="dispatch")
class WhatsAppWebhookView(View):
    def get(self, request):
        """Meta verify-token handshake."""
        mode = request.GET.get("hub.mode")
        token = request.GET.get("hub.verify_token")
        challenge = request.GET.get("hub.challenge", "")
        expected = getattr(settings, "META_WHATSAPP_VERIFY_TOKEN", "")
        if (
            mode == "subscribe"
            and expected
            and token
            and secrets.compare_digest(token, expected)
        ):
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse(status=403)

    def post(self, request):
        app_secret = getattr(settings, "META_WHATSAPP_APP_SECRET", "")
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not app_secret or not signature.startswith("sha256="):
            return JsonResponse({"detail": "missing signature"}, status=403)

        expected_sig = (
            "sha256="
            + hmac.new(
                app_secret.encode("utf-8"),
                request.body,
                hashlib.sha256,
            ).hexdigest()
        )
        if not secrets.compare_digest(signature, expected_sig):
            return JsonResponse({"detail": "invalid signature"}, status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"detail": "invalid json"}, status=400)

        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value") or {}
                for status_event in value.get("statuses", []) or []:
                    self._apply_status(status_event)

        # Always 200 — Meta retries non-200 for hours.
        return JsonResponse({"ok": True})

    def _apply_status(self, event: dict) -> None:
        wa_id = event.get("id")
        raw_status = event.get("status")
        if not wa_id or raw_status not in _WEBHOOK_STATUS_MAP:
            return

        new_status = _WEBHOOK_STATUS_MAP[raw_status]
        try:
            message = WhatsAppMessage.objects.get(wa_message_id=wa_id)
        except WhatsAppMessage.DoesNotExist:
            logger.info("WhatsApp webhook for unknown wa_message_id=%s", wa_id)
            return
        except WhatsAppMessage.MultipleObjectsReturned:
            logger.warning("Multiple rows share wa_message_id=%s", wa_id)
            return

        ts_raw = event.get("timestamp")
        try:
            ts_iso = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            ts_iso = _now_iso()

        history_entry = {"status": raw_status, "timestamp": ts_iso}
        errors = event.get("errors") or []
        if errors:
            first = errors[0] or {}
            history_entry["error_code"] = first.get("code")
            history_entry["error_message"] = first.get("title") or first.get("message")

        message.status_history = (message.status_history or []) + [history_entry]
        if _STATUS_RANK[new_status] >= _STATUS_RANK[message.status]:
            message.status = new_status
        message.save(update_fields=["status", "status_history", "updated_at"])
