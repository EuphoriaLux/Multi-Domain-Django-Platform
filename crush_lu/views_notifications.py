"""In-app notification (bell) views and API endpoints."""
import json
import logging

from django.http import JsonResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import crush_login_required
from .models import Notification

logger = logging.getLogger(__name__)


def _wants_json(request) -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    if "application/json" in accept:
        return True
    requested_with = request.headers.get("X-Requested-With", "").lower()
    return requested_with == "xmlhttprequest"


@crush_login_required
def notifications_page(request):
    """Full-page notification history with mark-all-read button."""
    qs = Notification.objects.filter(user=request.user)
    unread_count = qs.filter(read_at__isnull=True).count()
    notifications = list(qs[:100])
    return render(
        request,
        "crush_lu/notifications.html",
        {
            "notifications": notifications,
            "unread_count": unread_count,
        },
    )


@crush_login_required
def api_notifications_list(request):
    """GET /api/notifications/ — list recent notifications + unread count.

    Used by the bell dropdown to populate without a full page load.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    qs = Notification.objects.filter(user=request.user)
    unread_count = qs.filter(read_at__isnull=True).count()
    items = list(qs[:20])
    return JsonResponse(
        {
            "unread_count": unread_count,
            "items": [
                {
                    "id": n.id,
                    "type": n.notification_type,
                    "title": n.title,
                    "body": n.body,
                    "link_url": n.link_url,
                    "is_unread": n.is_unread,
                    "created_at": n.created_at.isoformat(),
                }
                for n in items
            ],
        }
    )


@crush_login_required
@require_POST
def api_notification_mark_read(request, notification_id):
    """POST /api/notifications/<id>/read/ — mark a single row read.

    Returns JSON for fetch/AJAX clients, redirects HTML form posts back
    to the full notifications page so the browser doesn't render raw JSON.
    """
    try:
        notif = Notification.objects.get(id=notification_id, user=request.user)
    except Notification.DoesNotExist:
        if _wants_json(request):
            return JsonResponse({"error": "not_found"}, status=404)
        return redirect("crush_lu:notifications")
    if notif.read_at is None:
        notif.read_at = timezone.now()
        notif.save(update_fields=["read_at"])
    if _wants_json(request):
        return JsonResponse({"ok": True, "id": notif.id})
    return redirect("crush_lu:notifications")


@crush_login_required
@require_POST
def api_notifications_mark_all_read(request):
    """POST /api/notifications/mark-all-read/ — bulk-mark all unread rows.

    JSON for fetch, redirect for HTML form posts (the notifications page
    has a regular form button as a no-JS fallback).
    """
    updated = Notification.objects.filter(
        user=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())
    if _wants_json(request):
        return JsonResponse({"ok": True, "updated": updated})
    return redirect("crush_lu:notifications")
