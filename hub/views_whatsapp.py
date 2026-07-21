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

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status as http_status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import WhatsAppInboundMessage, WhatsAppMessage
from .serializers import (
    WhatsAppInboundMessageSerializer,
    WhatsAppMessageSerializer,
)
from .whatsapp_service import (
    TemplatesFetchError,
    fetch_approved_templates,
    maybe_flag_not_on_whatsapp as _maybe_flag_not_on_whatsapp,
    meta_settings_ok as _meta_settings_ok,
    now_iso as _now_iso,
    send_whatsapp_template,
)

logger = logging.getLogger(__name__)


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

        message = send_whatsapp_template(
            sender=request.user,
            recipient=recipient,
            template_name=template_name,
            language=language,
            parameters=parameters,
        )
        return Response(
            {"message": WhatsAppMessageSerializer(message).data},
            status=(
                http_status.HTTP_201_CREATED
                if message.status == WhatsAppMessage.Status.SENT
                else http_status.HTTP_502_BAD_GATEWAY
            ),
        )


class WhatsAppTemplatesView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        try:
            # No cache here: the hub CRM always sees Meta's current list.
            items = fetch_approved_templates(use_cache=False)
        except TemplatesFetchError as exc:
            return Response(
                {"detail": exc.detail},
                status=http_status.HTTP_502_BAD_GATEWAY,
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
                # Filter on updated_at, not created_at, so polling clients
                # see webhook-driven status transitions on older rows.
                qs = qs.filter(updated_at__gte=ts)
            except ValueError:
                pass
        serializer = WhatsAppMessageSerializer(qs, many=True)
        return Response({"items": serializer.data})


class WhatsAppInboxView(APIView):
    """List inbound (user→us) WhatsApp messages for the hub support inbox.

    Shared across admins (not scoped to ``request.user`` like the outbound
    list) — inbound replies belong to no single sender. Supports ``?since=``
    (ISO timestamp) for polling and ``?unread=1`` to show only unread.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = WhatsAppInboundMessage.objects.all()
        since = request.query_params.get("since")
        if since:
            try:
                ts = datetime.fromisoformat(since.replace("Z", "+00:00"))
                qs = qs.filter(received_at__gte=ts)
            except ValueError:
                pass
        if request.query_params.get("unread") in ("1", "true", "True"):
            qs = qs.filter(is_read=False)
        serializer = WhatsAppInboundMessageSerializer(qs, many=True)
        return Response(
            {
                "items": serializer.data,
                "unread_count": WhatsAppInboundMessage.objects.filter(
                    is_read=False
                ).count(),
            }
        )


class WhatsAppInboxReadView(APIView):
    """Mark inbound messages as read.

    Body: ``{"ids": [1, 2, ...]}`` to mark specific rows, or ``{"all": true}``
    to clear the whole unread queue.
    """

    permission_classes = [IsAdminUser]

    def post(self, request):
        data = request.data or {}
        ids = data.get("ids")
        mark_all = bool(data.get("all"))
        if not mark_all and not ids:
            return Response(
                {"detail": "Provide ids (a list) or all=true."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        qs = WhatsAppInboundMessage.objects.filter(is_read=False)
        if not mark_all:
            if not isinstance(ids, list):
                return Response(
                    {"detail": "ids must be a list."},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(id__in=ids)
        updated = qs.update(is_read=True)
        return Response({"updated": updated})


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
                # wa_id → profile name, attached to each inbound message below.
                contacts = self._index_contacts(value.get("contacts", []) or [])
                for status_event in value.get("statuses", []) or []:
                    self._apply_status(status_event)
                for inbound in value.get("messages", []) or []:
                    self._store_inbound(inbound, contacts)

        # Always 200 — Meta retries non-200 for hours.
        return JsonResponse({"ok": True})

    @staticmethod
    def _index_contacts(contacts: list) -> dict:
        """Map each contact's wa_id → WhatsApp profile name."""
        out = {}
        for contact in contacts:
            wa_id = contact.get("wa_id")
            if wa_id:
                out[wa_id] = (contact.get("profile") or {}).get("name") or ""
        return out

    def _store_inbound(self, msg: dict, contacts: dict) -> None:
        """Persist one inbound (user→us) message; idempotent on wa_message_id."""
        wa_id = msg.get("id")
        from_number = msg.get("from")
        if not wa_id or not from_number:
            return

        msg_type = msg.get("type") or "unknown"
        text = ""
        if msg_type == "text":
            text = (msg.get("text") or {}).get("body") or ""

        ts_raw = msg.get("timestamp")
        try:
            received_at = datetime.fromtimestamp(int(ts_raw), tz=timezone.utc)
        except (TypeError, ValueError):
            received_at = datetime.now(timezone.utc)

        # get_or_create makes Meta's webhook retries a no-op (wa_id is unique).
        WhatsAppInboundMessage.objects.get_or_create(
            wa_message_id=wa_id,
            defaults={
                "from_number": from_number,
                "contact_name": contacts.get(from_number, ""),
                "message_type": msg_type,
                "text": text,
                "payload": msg,
                "received_at": received_at,
            },
        )

    def _apply_status(self, event: dict) -> None:
        wa_id = event.get("id")
        raw_status = event.get("status")
        if not wa_id or raw_status not in _WEBHOOK_STATUS_MAP:
            return

        new_status = _WEBHOOK_STATUS_MAP[raw_status]

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

        # Serialize concurrent webhook callbacks for the same wa_message_id:
        # without row-level locking, two near-simultaneous events (e.g.
        # `sent` and `delivered`) can both read the same pre-update row and
        # one save would clobber the other's status_history append.
        with transaction.atomic():
            try:
                message = (
                    WhatsAppMessage.objects.select_for_update()
                    .get(wa_message_id=wa_id)
                )
            except WhatsAppMessage.DoesNotExist:
                logger.info(
                    "WhatsApp webhook for unknown wa_message_id=%s", wa_id
                )
                return
            except WhatsAppMessage.MultipleObjectsReturned:
                logger.warning("Multiple rows share wa_message_id=%s", wa_id)
                return

            message.status_history = (message.status_history or []) + [
                history_entry
            ]
            if _STATUS_RANK[new_status] >= _STATUS_RANK[message.status]:
                message.status = new_status
            message.save(
                update_fields=["status", "status_history", "updated_at"]
            )
            _maybe_flag_not_on_whatsapp(
                message.recipient, history_entry.get("error_code")
            )
