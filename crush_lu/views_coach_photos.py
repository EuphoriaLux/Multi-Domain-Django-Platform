"""
Coach Photo Review views for Crush.lu.

Interactive swipe deck for coaches to review, approve, or flag photos and fakes in Crush Connect.
"""

import json
import logging
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from crush_lu.decorators import coach_required
from crush_lu.services.photo_review import (
    get_photo_review_queue,
    PhotoReviewError,
    submit_photo_review,
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
    cards, total_waiting = get_photo_review_queue(request.coach, limit=30)
    context = {
        "coach": request.coach,
        "initial_cards": cards,
        "total_waiting": total_waiting,
    }
    return render(request, "crush_lu/coach_photo_review.html", context)


@coach_required
@require_POST
def coach_photo_review_decide(request):
    """
    Submit a swipe decision (approve, flag fake, request revision).
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
        for field in ("decision", "reason", "notes", "photo_key", "photo_field"):
            if not isinstance(data.get(field, ""), str):
                raise ValueError

        profile_id = int(data.get("profile_id"))
        decision = data.get("decision", "").strip().lower()
        reason = data.get("reason", "").strip()
        notes = data.get("notes", "").strip()
        photo_key = data.get("photo_key", "")
        photo_field = data.get("photo_field", "photo_1")
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse(
            {"success": False, "error": _("Invalid request payload")}, status=400
        )

    try:
        result = submit_photo_review(
            coach=request.coach,
            profile_id=profile_id,
            decision=decision,
            reason=reason,
            notes=notes,
            request=request,
            photo_key=photo_key,
            photo_field=photo_field,
        )
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
    if len(cursor) > 1024:
        return JsonResponse({"error": _("Invalid request payload")}, status=400)
    try:
        cards, total_waiting = get_photo_review_queue(
            request.coach, limit=30, cursor=cursor
        )
    except PhotoReviewError as exc:
        return JsonResponse({"error": exc.message}, status=exc.status)
    return JsonResponse({"cards": cards, "total_waiting": total_waiting})
