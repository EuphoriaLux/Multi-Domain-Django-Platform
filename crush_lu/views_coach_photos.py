"""
Coach Photo Review views for Crush.lu.

Member-card deck for coaches: every photo of a member waiting for review is
decided on one card and submitted once (approve, request a replacement, or
flag a fake profile).
"""

import json
import logging
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from crush_lu.decorators import coach_required
from crush_lu.photo_review_reasons import PHOTO_REVISION_REASON_CHOICES
from crush_lu.services.photo_review import (
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
