"""
Crush Connect waitlist API endpoints.

Language-neutral endpoints for AJAX join/status operations.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models.crush_connect import CrushConnectWaitlist

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(["POST"])
def join_waitlist(request):
    """Join the Crush Connect waitlist."""
    entry, created = CrushConnectWaitlist.objects.get_or_create(user=request.user)
    total = CrushConnectWaitlist.objects.count()

    if created:
        logger.info("User %s joined Crush Connect waitlist (position %d)", request.user.id, entry.waitlist_position)
        return JsonResponse({
            "status": "joined",
            "position": entry.waitlist_position,
            "total": total,
        })
    else:
        return JsonResponse({
            "status": "already_joined",
            "position": entry.waitlist_position,
            "total": total,
        })


@login_required
@require_http_methods(["GET"])
def waitlist_status(request):
    """Get the user's waitlist status."""
    total = CrushConnectWaitlist.objects.count()
    try:
        entry = CrushConnectWaitlist.objects.get(user=request.user)
        return JsonResponse({
            "on_waitlist": True,
            "position": entry.waitlist_position,
            "total": total,
            "is_eligible": entry.is_eligible,
        })
    except CrushConnectWaitlist.DoesNotExist:
        return JsonResponse({
            "on_waitlist": False,
            "position": None,
            "total": total,
            "is_eligible": False,
        })
