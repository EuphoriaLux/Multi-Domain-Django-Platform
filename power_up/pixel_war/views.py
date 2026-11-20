"""Views and JSON API endpoints for Power-Up Pixel War."""

import json
import logging
import math
import re

from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from power_up.pixel_war.models import (
    Pixel,
    PixelCanvas,
    PixelHistory,
    UserPixelCooldown,
    UserPixelStats,
)

logger = logging.getLogger(__name__)

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEFAULT_HISTORY_LIMIT = 30
MAX_HISTORY_LIMIT = 100


def _get_active_canvas(canvas_id=None, create_default=False):
    """Resolve the requested canvas, falling back to the active default canvas."""
    if canvas_id is not None:
        return get_object_or_404(PixelCanvas, id=canvas_id)

    canvas = (
        PixelCanvas.objects.filter(is_active=True).order_by("id").first()
        or PixelCanvas.objects.order_by("id").first()
    )
    if canvas is None and create_default:
        canvas, _ = PixelCanvas.objects.get_or_create(
            name="Lux Pixel War",
            defaults={
                "width": 100,
                "height": 100,
                "anonymous_cooldown_seconds": 10,
                "registered_cooldown_seconds": 5,
                "anonymous_pixels_per_minute": 6,
                "registered_pixels_per_minute": 12,
            },
        )
    return canvas


def _public_placer_label_parts(placed_by_id, username, first_name):
    """Return a public-safe display label without leaking email usernames."""
    if placed_by_id is None:
        return "Anonymous"
    clean_username = (username or "").strip()
    if "@" in clean_username:
        return (first_name or "").strip() or "Member"
    return clean_username or "Member"


def _public_placer_label(user):
    """Return a public-safe display label for a User instance or None."""
    if user is None:
        return "Anonymous"
    return _public_placer_label_parts(user.id, user.username, user.first_name)


def _parse_int(value):
    """Parse an integer while rejecting booleans and floating-point values."""
    if isinstance(value, (bool, float)) or value is None:
        raise ValueError("Expected integer")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("Empty integer")
    return int(value)


def _get_or_create_locked_cooldown(user, session_key, canvas, now):
    """Fetch (with row lock) or create the cooldown record for a player/canvas."""
    lookup = (
        {"user": user, "canvas": canvas}
        if user is not None
        else {"user__isnull": True, "session_key": session_key, "canvas": canvas}
    )

    def _fetch_locked():
        rows = list(
            UserPixelCooldown.objects.select_for_update()
            .filter(**lookup)
            .order_by("-last_placed", "-id")
        )
        if not rows:
            return None
        if len(rows) > 1:
            UserPixelCooldown.objects.filter(
                id__in=[row.id for row in rows[1:]]
            ).delete()
        return rows[0]

    cooldown = _fetch_locked()
    if cooldown is not None:
        return cooldown, False

    create_kwargs = {
        "user": user,
        "canvas": canvas,
        "session_key": None if user is not None else session_key,
        "pixels_placed_last_minute": 0,
        "last_minute_reset": now,
    }
    try:
        with transaction.atomic():
            return UserPixelCooldown.objects.create(**create_kwargs), True
    except IntegrityError:
        cooldown = _fetch_locked()
        if cooldown is None:
            raise
        return cooldown, False


@require_GET
@ensure_csrf_cookie
def pixel_war(request):
    """Render the interactive Pixel War canvas on power-up.lu / powerup.lu."""
    canvas = _get_active_canvas(create_default=True)
    is_authenticated = request.user.is_authenticated
    user_stats = (
        UserPixelStats.objects.filter(user=request.user, canvas=canvas).first()
        if is_authenticated
        else None
    )
    pixel_count = canvas.pixels.count()
    cooldown_seconds = (
        canvas.registered_cooldown_seconds
        if is_authenticated
        else canvas.anonymous_cooldown_seconds
    )
    pixels_per_minute = (
        canvas.registered_pixels_per_minute
        if is_authenticated
        else canvas.anonymous_pixels_per_minute
    )

    pixel_war_config = {
        "canvasId": canvas.id,
        "canvasName": canvas.name,
        "width": canvas.width,
        "height": canvas.height,
        "isActive": canvas.is_active,
        "isAuthenticated": is_authenticated,
        "cooldownSeconds": cooldown_seconds,
        "pixelsPerMinute": pixels_per_minute,
        "initialPixelCount": pixel_count,
        "apiUrls": {
            "canvasState": f"/pixel-war/api/canvas-state/{canvas.id}/",
            "placePixel": "/pixel-war/api/place-pixel/",
            "pixelHistory": f"/pixel-war/api/pixel-history/?canvas_id={canvas.id}&limit=25",
        },
        "i18n": {
            "ready": str(_("Ready")),
            "waitSeconds": str(_("Wait %(seconds)ss")),
            "pixelsLeft": str(_("%(count)s left")),
            "placePixel": str(_("Place pixel")),
            "selectCell": str(_("Select a cell")),
            "placed": str(_("Pixel placed at (%(x)s, %(y)s)")),
            "limitReached": str(
                _("Cooldown active — please wait %(seconds)s seconds.")
            ),
            "networkError": str(_("Could not reach the server. Please try again.")),
            "canvasInactive": str(_("This canvas is currently paused.")),
            "noActivity": str(_("No pixels placed yet. Be the first!")),
            "tapMode": str(_("Instant tap")),
            "confirmMode": str(_("Preview & place")),
            "gridOn": str(_("Grid on")),
            "gridOff": str(_("Grid off")),
            "fullscreen": str(_("Fullscreen")),
            "exitFullscreen": str(_("Exit fullscreen")),
            "anonymous": str(_("Guest")),
        },
    }

    context = {
        "canvas": canvas,
        "pixel_count": pixel_count,
        "user_stats": user_stats,
        "is_authenticated": is_authenticated,
        "cooldown_seconds": cooldown_seconds,
        "pixels_per_minute": pixels_per_minute,
        "pixel_war_config": pixel_war_config,
        "page_title": _("Lux Pixel War | Power-Up"),
        "meta_description": _(
            "Collaborative pixel art canvas hosted on Power-Up. Place pixels, "
            "explore the grid and build together in real time."
        ),
    }
    return render(request, "pixel_war/pixel_war.html", context)


@require_GET
def get_canvas_state(request, canvas_id=None):
    """Return full canvas metadata and all currently placed pixels."""
    try:
        canvas = _get_active_canvas(canvas_id=canvas_id, create_default=False)
    except Http404:
        return JsonResponse({"error": "Canvas not found"}, status=404)

    if canvas is None:
        return JsonResponse({"error": "No canvas found"}, status=404)

    pixels = canvas.pixels.values(
        "x",
        "y",
        "color",
        "placed_at",
        "placed_by_id",
        "placed_by__username",
        "placed_by__first_name",
    )
    pixel_data = {}
    for pixel in pixels:
        pixel_data[f"{pixel['x']},{pixel['y']}"] = {
            "color": pixel["color"],
            "placed_by": _public_placer_label_parts(
                pixel["placed_by_id"],
                pixel["placed_by__username"],
                pixel["placed_by__first_name"],
            ),
            "placed_at": pixel["placed_at"].isoformat(),
        }

    return JsonResponse(
        {
            "success": True,
            "canvas": {
                "id": canvas.id,
                "name": canvas.name,
                "width": canvas.width,
                "height": canvas.height,
                "anonymous_cooldown": canvas.anonymous_cooldown_seconds,
                "registered_cooldown": canvas.registered_cooldown_seconds,
                "anonymous_pixels_per_minute": canvas.anonymous_pixels_per_minute,
                "registered_pixels_per_minute": canvas.registered_pixels_per_minute,
                "is_active": canvas.is_active,
            },
            "pixel_count": len(pixel_data),
            "pixels": pixel_data,
        }
    )


@require_POST
def place_pixel(request):
    """Place a single pixel with atomic rate limiting and input validation."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({"error": "JSON object required"}, status=400)

    try:
        x = _parse_int(data.get("x"))
        y = _parse_int(data.get("y"))
    except ValueError:
        return JsonResponse({"error": "Invalid coordinates"}, status=400)

    raw_color = data.get("color")
    if not isinstance(raw_color, str) or not HEX_COLOR_RE.match(raw_color):
        return JsonResponse({"error": "Invalid hex color"}, status=400)
    color = raw_color.upper()

    raw_canvas_id = data.get("canvas_id")
    canvas_id = None
    if raw_canvas_id is not None and raw_canvas_id != "":
        try:
            canvas_id = _parse_int(raw_canvas_id)
        except ValueError:
            return JsonResponse({"error": "Invalid canvas ID"}, status=400)

    try:
        canvas = _get_active_canvas(canvas_id=canvas_id, create_default=False)
    except Http404:
        return JsonResponse({"error": "Canvas not found"}, status=404)

    if canvas is None:
        return JsonResponse({"error": "Canvas not found"}, status=404)

    if not canvas.is_active:
        return JsonResponse({"error": "Canvas is not active"}, status=400)

    if x < 0 or x >= canvas.width or y < 0 or y >= canvas.height:
        return JsonResponse({"error": "Invalid coordinates"}, status=400)

    is_authenticated = request.user.is_authenticated
    placed_by = request.user if is_authenticated else None
    session_key = None
    if not is_authenticated:
        if not request.session.session_key:
            request.session.create()
        session_key = request.session.session_key

    max_pixels_per_minute = (
        canvas.registered_pixels_per_minute
        if is_authenticated
        else canvas.anonymous_pixels_per_minute
    )
    cooldown_seconds = (
        canvas.registered_cooldown_seconds
        if is_authenticated
        else canvas.anonymous_cooldown_seconds
    )

    now = timezone.now()
    with transaction.atomic():
        cooldown, created = _get_or_create_locked_cooldown(
            user=placed_by,
            session_key=session_key,
            canvas=canvas,
            now=now,
        )
        previous_last_placed = cooldown.last_placed

        minute_window_reset = False
        if cooldown.last_minute_reset is None:
            cooldown.pixels_placed_last_minute = 0
            cooldown.last_minute_reset = now
            minute_window_reset = True

        time_since_minute_reset = (now - cooldown.last_minute_reset).total_seconds()
        if time_since_minute_reset >= 60:
            cooldown.pixels_placed_last_minute = 0
            cooldown.last_minute_reset = now
            time_since_minute_reset = 0.0
            minute_window_reset = True

        if cooldown.pixels_placed_last_minute >= max_pixels_per_minute:
            if minute_window_reset:
                cooldown.save(
                    update_fields=["pixels_placed_last_minute", "last_minute_reset"]
                )
            remaining = max(1, math.ceil(60 - time_since_minute_reset))
            return JsonResponse(
                {
                    "error": "Pixel limit reached for this minute",
                    "cooldown_remaining": remaining,
                    "limit_info": {
                        "max_per_minute": max_pixels_per_minute,
                        "placed_this_minute": cooldown.pixels_placed_last_minute,
                        "cooldown_seconds": cooldown_seconds,
                        "is_registered": is_authenticated,
                    },
                },
                status=429,
            )

        if not created and cooldown_seconds > 0 and previous_last_placed is not None:
            elapsed = (now - previous_last_placed).total_seconds()
            if elapsed < cooldown_seconds:
                if minute_window_reset:
                    cooldown.save(
                        update_fields=[
                            "pixels_placed_last_minute",
                            "last_minute_reset",
                        ]
                    )
                remaining = max(1, math.ceil(cooldown_seconds - elapsed))
                return JsonResponse(
                    {
                        "error": "Please wait before placing another pixel",
                        "cooldown_remaining": remaining,
                        "limit_info": {
                            "max_per_minute": max_pixels_per_minute,
                            "placed_this_minute": cooldown.pixels_placed_last_minute,
                            "cooldown_seconds": cooldown_seconds,
                            "is_registered": is_authenticated,
                        },
                    },
                    status=429,
                )

        cooldown.last_placed = now
        cooldown.pixels_placed_last_minute += 1
        cooldown.save()

        pixel, _ = Pixel.objects.update_or_create(
            canvas=canvas,
            x=x,
            y=y,
            defaults={"color": color, "placed_by": placed_by},
        )
        PixelHistory.objects.create(
            canvas=canvas,
            x=x,
            y=y,
            color=color,
            placed_by=placed_by,
        )

        if is_authenticated:
            stats, _ = UserPixelStats.objects.get_or_create(
                user=request.user, canvas=canvas
            )
            UserPixelStats.objects.filter(pk=stats.pk).update(
                total_pixels_placed=F("total_pixels_placed") + 1,
                last_pixel_placed=now,
            )

    return JsonResponse(
        {
            "success": True,
            "pixel": {
                "x": pixel.x,
                "y": pixel.y,
                "color": pixel.color,
                "placed_by": _public_placer_label(placed_by),
                "placed_at": pixel.placed_at.isoformat(),
            },
            "cooldown_info": {
                "cooldown_seconds": cooldown_seconds,
                "pixels_remaining": max(
                    0, max_pixels_per_minute - cooldown.pixels_placed_last_minute
                ),
                "max_per_minute": max_pixels_per_minute,
                "is_registered": is_authenticated,
            },
        }
    )


@require_GET
def get_pixel_history(request):
    """Return recent pixel placement history with bounded pagination."""
    raw_limit = request.GET.get("limit")
    if raw_limit is None or raw_limit == "":
        limit = DEFAULT_HISTORY_LIMIT
    else:
        try:
            limit = _parse_int(raw_limit)
        except ValueError:
            return JsonResponse({"error": "Invalid limit"}, status=400)
    limit = max(1, min(limit, MAX_HISTORY_LIMIT))

    raw_canvas_id = request.GET.get("canvas_id")
    canvas = None
    if raw_canvas_id is not None and raw_canvas_id != "":
        try:
            canvas_id = _parse_int(raw_canvas_id)
        except ValueError:
            return JsonResponse({"error": "Invalid canvas ID"}, status=400)
        try:
            canvas = _get_active_canvas(canvas_id=canvas_id, create_default=False)
        except Http404:
            return JsonResponse({"error": "Canvas not found"}, status=404)
    else:
        canvas = _get_active_canvas(create_default=False)

    qs = PixelHistory.objects.all()
    if canvas is not None:
        qs = qs.filter(canvas=canvas)

    rows = qs.values(
        "x",
        "y",
        "color",
        "placed_at",
        "placed_by_id",
        "placed_by__username",
        "placed_by__first_name",
    )[:limit]
    history_data = [
        {
            "x": entry["x"],
            "y": entry["y"],
            "color": entry["color"],
            "placed_by": _public_placer_label_parts(
                entry["placed_by_id"],
                entry["placed_by__username"],
                entry["placed_by__first_name"],
            ),
            "placed_at": entry["placed_at"].isoformat(),
        }
        for entry in rows
    ]
    return JsonResponse({"success": True, "history": history_data})
