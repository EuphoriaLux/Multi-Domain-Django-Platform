"""
Coach Photo Review views for Crush.lu.

Member-card deck for coaches: every photo of a member waiting for review is
decided on one card and submitted once (approve, request a replacement, or
flag a fake profile).
"""

import json
import logging
from django.contrib import messages
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from crush_lu.decorators import coach_required
from crush_lu.models import CrushProfile
from crush_lu.photo_review_reasons import PHOTO_REVISION_REASON_CHOICES
from crush_lu.services.photo_framing import (
    DEFAULT_FOCUS,
    PhotoFramingError,
    clear_focus,
    get_focus,
    set_focus,
)
from crush_lu.services.photo_order import (
    PhotoOrderError,
    can_become_main,
    swap_photo_slots,
)
from crush_lu.services.photo_publication import PHOTO_FIELDS, get_published_key
from crush_lu.services.photo_review import (
    _photo_slot_labels,
    get_photo_review_queue,
    PhotoReviewError,
    submit_member_review,
    undo_last_photo_review,
)

logger = logging.getLogger(__name__)


@coach_required
@require_GET
def coach_photo_review_deck(request):
    """
    Render the coach swipe deck for fast photo review.
    Initial cards are injected directly into the template context for immediate render.
    """
    scope = request.GET.get("scope", "all")
    if scope not in ("all", "connect"):
        return HttpResponseBadRequest(_("Invalid review queue."))
    cards, total_waiting = get_photo_review_queue(request.coach, scope=scope)
    context = {
        "coach": request.coach,
        "initial_cards": cards,
        "total_waiting": total_waiting,
        "scope": scope,
        "photo_review_reason_choices": [
            ("fake_profile", _("Fake or Suspicious Profile")),
            *PHOTO_REVISION_REASON_CHOICES,
        ],
    }
    return render(request, "crush_lu/coach_photo_review.html", context)


@coach_required
@require_POST
def coach_photo_review_decide(request):
    """
    Submit a member card: ``{"profile_id", "decisions": [{"photo_field",
    "photo_key", "decision", "reason", "notes"}, ...]}``, one entry per
    reviewed photo. A single-photo payload (``decision``, ``photo_key``,
    ``photo_field`` at the top level) is still accepted.
    Skipping is client-side only and never reaches this endpoint.
    Accepts JSON body or multipart/form-data.
    """
    try:
        if request.content_type == "application/json":
            data = json.loads(request.body.decode("utf-8"))
        else:
            data = request.POST

        if not isinstance(data, dict):
            raise ValueError
        profile_id = int(data.get("profile_id"))
        decisions = data.get("decisions")
        if decisions is None:
            for field in ("decision", "reason", "notes", "photo_key", "photo_field"):
                if not isinstance(data.get(field, ""), str):
                    raise ValueError
            decisions = [
                {
                    field: data.get(field, default)
                    for field, default in (
                        ("decision", ""),
                        ("reason", ""),
                        ("notes", ""),
                        ("photo_key", ""),
                        ("photo_field", "photo_1"),
                    )
                }
            ]
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse(
            {"success": False, "error": _("Invalid request payload")}, status=400
        )

    try:
        result = submit_member_review(
            request.coach, profile_id, decisions, request=request
        )
        if len(decisions) == 1:
            # Single-photo callers read the decision back at the top level.
            result["decision"] = decisions[0]["decision"].strip().lower()
            result["new_status"] = next(iter(result["decisions"].values()))
        return JsonResponse(result)
    except PhotoReviewError as exc:
        return JsonResponse({"success": False, "error": exc.message}, status=exc.status)
    except Exception:
        logger.exception(
            "Error processing photo review decision for profile %s", profile_id
        )
        return JsonResponse(
            {
                "success": False,
                "error": _("Could not save the review. Please try again."),
            },
            status=500,
        )


@coach_required
@require_POST
def coach_photo_review_undo(request):
    """Undo the last photo review decision made by this coach."""
    try:
        data = (
            (json.loads(request.body) if request.body else {})
            if request.content_type == "application/json"
            else request.POST
        )
        if not isinstance(data, dict):
            raise ValueError
        log_id = int(data["log_id"]) if "log_id" in data else None
    except (ValueError, TypeError):
        return JsonResponse(
            {"success": False, "error": _("Invalid request payload")}, status=400
        )
    try:
        result = undo_last_photo_review(request.coach, log_id=log_id, request=request)
        status_code = 200 if result.get("success") else 400
        return JsonResponse(result, status=status_code)
    except PhotoReviewError as exc:
        return JsonResponse({"success": False, "error": exc.message}, status=exc.status)
    except Exception:
        logger.exception("Error undoing photo review for coach %s", request.coach.id)
        return JsonResponse(
            {
                "success": False,
                "error": _("Could not undo the review. Please try again."),
            },
            status=500,
        )


@coach_required
@require_GET
def coach_photo_review_more(request):
    """Load the next batch of cards for endless swipe review."""
    cursor = request.GET.get("cursor", "")
    scope = request.GET.get("scope", "all")
    if scope not in ("all", "connect"):
        return JsonResponse({"error": _("Invalid review queue.")}, status=400)
    if len(cursor) > 1024:
        return JsonResponse({"error": _("Invalid request payload")}, status=400)
    try:
        cards, total_waiting = get_photo_review_queue(
            request.coach, cursor=cursor, scope=scope
        )
    except PhotoReviewError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)
    return JsonResponse({"cards": cards, "total_waiting": total_waiting})


@coach_required
@require_GET
def coach_member_photos(request, user_id):
    """Coach page to set the point of each photo that every crop keeps.

    Frames the live upload: the file the coach sees. Any active coach may
    frame any member, as on the member overview.
    Spec: ai-memory-hub/specs/2026-10-13-crush-photo-framing.md
    """
    profile = get_object_or_404(
        CrushProfile.objects.select_related("user").prefetch_related(
            "published_photos", "photo_framings", "photo_review_states"
        ),
        user_id=user_id,
    )
    labels = _photo_slot_labels()
    keys = {field: getattr(profile, field).name or "" for field in PHOTO_FIELDS}
    slots = []
    for index, photo_field in enumerate(PHOTO_FIELDS):
        photo = getattr(profile, photo_field)
        if not photo:
            continue
        focus = get_focus(profile, photo.name)
        published = get_published_key(profile, photo_field)
        # The slot before this one: "Make main photo" for photo 2, "Move up"
        # for photo 3 (services/photo_order.py).
        previous = PHOTO_FIELDS[index - 1] if index else ""
        slots.append(
            {
                "field": photo_field,
                "label": labels[photo_field],
                "url": reverse(
                    "crush_lu:serve_profile_photo",
                    kwargs={"user_id": profile.user_id, "photo_field": photo_field},
                ),
                "photo_key": photo.name,
                "focus_x": (focus or DEFAULT_FOCUS)[0],
                "focus_y": (focus or DEFAULT_FOCUS)[1],
                "has_focus": bool(focus),
                # Members still see an earlier approved photo in this slot.
                "is_replacement": bool(published and published != photo.name),
                "can_become_main": can_become_main(profile, photo_field),
                "previous_field": previous,
                "previous_key": keys.get(previous, ""),
            }
        )
    return render(
        request,
        "crush_lu/coach_member_photos.html",
        {
            "member": profile.user,
            "profile": profile,
            "slots": slots,
            "main_key": keys["photo_1"],
            # A new main photo drops the in-person badge, which names a file.
            "main_is_verified": profile.is_photo_verified,
        },
    )


@coach_required
@require_POST
def coach_member_photo_swap(request, user_id):
    """Swap two of a member's photo slots (``photo_field``/``photo_key`` with
    ``target_field``/``target_key``), most often to choose the main photo.
    The member is told. Plain form post: redirects back to the photo page."""
    profile = get_object_or_404(CrushProfile, user_id=user_id)
    try:
        swap = swap_photo_slots(
            request.coach,
            profile,
            request.POST.get("photo_field", ""),
            request.POST.get("target_field", ""),
            request.POST.get("photo_key", ""),
            request.POST.get("target_key", ""),
            request=request,
        )
    except PhotoOrderError as exc:
        messages.error(request, exc.message)
    else:
        if swap.changed_main_photo:
            message = _("Main photo changed. %(name)s has been told.")
        else:
            message = _("Photo order changed. %(name)s has been told.")
        messages.success(request, message % {"name": profile.display_name})
    return redirect("crush_lu:coach_member_photos", user_id=user_id)


@coach_required
@require_POST
def coach_member_photo_focus(request, user_id):
    """Save or reset one photo's focus point: ``{"photo_field", "photo_key",
    "focus_x", "focus_y"}`` in percent, or ``{"photo_field", "photo_key",
    "reset": true}``. Refused (409) when the photo changed since the page
    loaded."""
    try:
        data = json.loads(request.body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        photo_field = data.get("photo_field", "")
        photo_key = data.get("photo_key", "")
        if not isinstance(photo_field, str) or not isinstance(photo_key, str):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse(
            {"success": False, "error": _("Invalid request payload")}, status=400
        )
    profile = get_object_or_404(CrushProfile, user_id=user_id)
    try:
        if data.get("reset") is True:
            clear_focus(profile, photo_field, photo_key)
            focus_x, focus_y = DEFAULT_FOCUS
        else:
            focus_x, focus_y = set_focus(
                request.coach,
                profile,
                photo_field,
                photo_key,
                data.get("focus_x"),
                data.get("focus_y"),
            )
    except PhotoFramingError as exc:
        return JsonResponse({"success": False, "error": exc.message}, status=exc.status)
    logger.info(
        "Coach %s %s the focus of profile %s %s",
        request.coach.pk,
        "reset" if data.get("reset") is True else "set",
        profile.pk,
        photo_field,
    )
    return JsonResponse(
        {
            "success": True,
            "focus_x": focus_x,
            "focus_y": focus_y,
            "has_focus": data.get("reset") is not True,
        }
    )
