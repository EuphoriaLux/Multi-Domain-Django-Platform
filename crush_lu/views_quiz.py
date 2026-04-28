import json
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from crush_lu.models import CrushCoach, CrushProfile
from crush_lu.models.events import EventRegistration
from crush_lu.models.quiz import (
    QuizEvent,
    QuizRotationSchedule,
    QuizTable,
    QuizTableMembership,
)
from crush_lu.throttling import QuizPinRateThrottle, ratelimit_view


def _photo_url(profile):
    """Return public quiz photo URL if photo_1 exists, else None."""
    if profile and getattr(profile, "photo_1", None):
        return f"/api/quiz/photo/{profile.user_id}/"
    return None


_AVATAR_COLORS = [
    "#8B5CF6", "#EC4899", "#F59E0B", "#10B981", "#3B82F6",
    "#EF4444", "#06B6D4", "#F97316", "#6366F1", "#14B8A6",
    "#E879F9", "#84CC16", "#F43F5E", "#22D3EE", "#A78BFA",
]


def _member_initials(name):
    """Extract up to 2 initials from a display name."""
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper() if name else "?"


def _member_color(name):
    """Deterministic color from name."""
    h = sum(ord(c) for c in name) if name else 0
    return _AVATAR_COLORS[h % len(_AVATAR_COLORS)]


def _get_table_members_json(quiz, round_number=0):
    """Build JSON-safe list of tables with their current members.

    Decides rotation-vs-fallback at the *quiz* level: if any rotation
    rows exist for this round, use them exclusively (so an empty table
    shows as empty rather than the stale round-0 check-in snapshot).
    Only fall back to ``QuizTableMembership`` when the quiz has no
    rotation schedule for this round at all (legacy non-rotating
    events).
    """
    tables = QuizTable.objects.filter(quiz=quiz).order_by("table_number")
    rotation_exists = QuizRotationSchedule.objects.filter(
        quiz=quiz, round_number=round_number
    ).exists()
    result = []
    for table in tables:
        members = []
        if rotation_exists:
            rotations = QuizRotationSchedule.objects.filter(
                quiz=quiz, round_number=round_number, table=table
            ).select_related("user__crushprofile")
            for r in rotations:
                profile = getattr(r.user, "crushprofile", None)
                name = profile.display_name if profile else "Anonymous"
                members.append(
                    {
                        "display_name": name,
                        "role": r.role,
                        "initials": _member_initials(name),
                        "color": _member_color(name),
                        "photo_url": _photo_url(profile),
                    }
                )
        else:
            for m in table.memberships.select_related("user__crushprofile"):
                profile = getattr(m.user, "crushprofile", None)
                name = profile.display_name if profile else "Anonymous"
                members.append(
                    {
                        "display_name": name,
                        "role": "",
                        "initials": _member_initials(name),
                        "color": _member_color(name),
                        "photo_url": _photo_url(profile),
                    }
                )
        result.append(
            {
                "table_id": table.id,
                "table_number": table.table_number,
                "members": members,
                "total_score": table.get_total_score(),
            }
        )
    return result


@login_required
def quiz_live_view(request, event_id):
    """Attendee view for the live quiz."""
    quiz = get_object_or_404(
        QuizEvent.objects.select_related("event", "current_round"),
        event_id=event_id,
    )

    # Only attended registrants (or staff) can access the quiz
    if not request.user.is_staff:
        if not EventRegistration.objects.filter(
            event=quiz.event, user=request.user, status="attended"
        ).exists():
            raise Http404

    is_quiz_night = quiz.event.event_type == "quiz_night"

    # Get user's current table assignment
    round_number = quiz.get_round_number()

    rotation = (
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=round_number,
            user=request.user,
        )
        .select_related("table")
        .first()
    )

    # Fall back to static membership if no rotation schedule
    user_table_number = None
    user_role = None
    user_table = None
    if rotation:
        user_table_number = rotation.table.table_number
        user_role = rotation.role
        user_table = rotation.table
    elif is_quiz_night:
        membership = (
            QuizTableMembership.objects.filter(table__quiz=quiz, user=request.user)
            .select_related("table")
            .first()
        )
        if membership:
            user_table_number = membership.table.table_number
            user_table = membership.table

    # Build initial tablemates list (so it shows immediately without API call)
    tablemates = []
    if user_table:
        if rotation:
            mates = (
                QuizRotationSchedule.objects.filter(
                    quiz=quiz, round_number=round_number, table=user_table
                )
                .exclude(user=request.user)
                .select_related("user__crushprofile")
            )
            for r in mates:
                profile = getattr(r.user, "crushprofile", None)
                tablemates.append(
                    {
                        "display_name": (
                            profile.display_name if profile else "Anonymous"
                        ),
                        "role": r.role,
                    }
                )
        else:
            mates = user_table.memberships.exclude(user=request.user).select_related(
                "user__crushprofile"
            )
            for m in mates:
                profile = getattr(m.user, "crushprofile", None)
                tablemates.append(
                    {
                        "display_name": (
                            profile.display_name if profile else "Anonymous"
                        ),
                        "role": "",
                    }
                )

    # For coaches/staff who aren't assigned to a table, provide the full
    # table overview so they can see the setup from the participant view
    is_coach_viewer = not user_table and (
        request.user.is_staff
        or CrushCoach.objects.filter(user=request.user, is_active=True).exists()
    )
    all_tables_json = ""
    if is_coach_viewer and is_quiz_night:
        all_tables_json = json.dumps(_get_table_members_json(quiz, round_number))

    context = {
        "quiz": quiz,
        "event": quiz.event,
        "is_quiz_night": is_quiz_night,
        "quiz_status": quiz.status,
        "user_table_number": user_table_number,
        "user_role": user_role,
        "tablemates_json": json.dumps(tablemates),
        "is_coach_viewer": is_coach_viewer,
        "all_tables_json": all_tables_json,
    }
    return render(request, "crush_lu/quiz_live.html", context)


@login_required
def quiz_coach_view(request, event_id):
    """Host control panel for the live quiz."""
    quiz = get_object_or_404(
        QuizEvent.objects.select_related("event", "current_round"),
        event_id=event_id,
    )
    # Only the quiz creator or a CrushCoach explicitly assigned to this
    # event can access the host view. Blanket is_staff and "any active
    # coach" bypasses removed so unrelated staff/coaches cannot control
    # the live quiz.
    if quiz.created_by_id != request.user.id:
        is_assigned_coach = CrushCoach.objects.filter(
            user=request.user,
            is_active=True,
            assigned_events=quiz.event_id,
        ).exists()
        if not is_assigned_coach:
            raise Http404

    rounds = quiz.rounds.prefetch_related("questions").order_by("sort_order")
    tables = QuizTable.objects.filter(quiz=quiz).order_by("table_number")
    is_quiz_night = quiz.event.event_type == "quiz_night"

    # Build table members data for the overview panel
    round_number = quiz.get_round_number()
    table_members = _get_table_members_json(quiz, round_number)

    # Surface persistent capacity warnings (empty anchor tables, too few
    # rotators, spillover) so the coach can act on them mid-event
    # instead of relying on the one-shot Django messages flash from the
    # admin generate action.
    rotation_warnings = []
    if is_quiz_night:
        try:
            from crush_lu.services.quiz_rotation import compute_rotation_warnings

            rotation_warnings = compute_rotation_warnings(quiz)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "compute_rotation_warnings failed for quiz %s", quiz.id
            )

    context = {
        "quiz": quiz,
        "event": quiz.event,
        "rounds": rounds,
        "tables": tables,
        "is_quiz_night": is_quiz_night,
        "table_members_json": json.dumps(table_members),
        "rotation_warnings": rotation_warnings,
    }
    return render(request, "crush_lu/quiz_coach.html", context)


def quiz_table_display(request, event_id):
    """Full-screen projector view for quiz events. No auth required."""
    quiz = get_object_or_404(
        QuizEvent.objects.select_related("event"), event_id=event_id
    )

    pin_required = bool(quiz.display_token)
    # If PIN provided in query string, validate for initial page load
    if pin_required and request.GET.get("token") == quiz.display_token:
        pin_required = False

    confirmed_count = EventRegistration.objects.filter(
        event_id=event_id, status__in=["confirmed", "attended"]
    ).count()

    context = {
        "quiz": quiz,
        "event": quiz.event,
        "num_tables": quiz.num_tables or 0,
        "confirmed_count": confirmed_count,
        "pin_required": pin_required,
    }
    return render(request, "crush_lu/quiz_display.html", context)


@csrf_exempt
@require_POST
@ratelimit_view([QuizPinRateThrottle])
def quiz_display_verify_pin(request, event_id):
    """Verify a 4-digit PIN for projector display access.

    Rate-limited per IP (5/min) so the short display_token can't be
    brute-forced. Legitimate retries when a coach mistypes still fit
    well under the limit.
    """
    try:
        quiz = QuizEvent.objects.get(event_id=event_id)
    except QuizEvent.DoesNotExist:
        return JsonResponse({"valid": False}, status=404)

    # No PIN configured — always valid
    if not quiz.display_token:
        return JsonResponse({"valid": True})

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"valid": False})

    pin = str(data.get("pin", ""))
    valid = pin == quiz.display_token
    return JsonResponse({"valid": valid})


def quiz_table_display_data(request, event_id):
    """JSON endpoint for table assignments, polled by the display page."""
    from crush_lu.models.quiz import TableRoundScore

    try:
        quiz = QuizEvent.objects.select_related("current_round").get(event_id=event_id)
    except QuizEvent.DoesNotExist:
        return JsonResponse({"error": "Quiz not found"}, status=404)

    # Optional token check
    if quiz.display_token and request.GET.get("token") != quiz.display_token:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    round_number = quiz.get_round_number()
    tables = _get_table_members_json(quiz, round_number)
    attended_count = EventRegistration.objects.filter(
        event_id=event_id, status="attended"
    ).count()
    confirmed_count = EventRegistration.objects.filter(
        event_id=event_id, status__in=["confirmed", "attended"]
    ).count()

    data = {
        "tables": tables,
        "attended_count": attended_count,
        "confirmed_count": confirmed_count,
        "quiz_status": quiz.status,
    }

    # Include current question and scoring state for polling-based display
    total_tables = QuizTable.objects.filter(quiz=quiz).count()
    data["total_tables"] = total_tables

    question = quiz.get_current_question()
    if question and quiz.is_active:
        data["question"] = {
            "id": question.id,
            "text": question.text,
            "question_type": question.question_type,
            "points": question.points,
        }
        if question.question_type in ("multiple_choice", "true_false"):
            from crush_lu.models.quiz import parse_choices

            choices = parse_choices(question.choices)
            data["question"]["choices"] = [
                {"text": c["text"]} for c in choices if isinstance(c, dict)
            ]

        questions = quiz.current_round.questions.order_by("sort_order")
        data["question_index"] = quiz.current_question_index
        data["question_total"] = questions.count()
        data["is_bonus"] = quiz.current_round.is_bonus if quiz.current_round else False
        data["round_title"] = quiz.current_round.title if quiz.current_round else ""
        data["time_per_question"] = (
            quiz.current_round.time_per_question if quiz.current_round else 30
        )

        # Scoring progress for current question
        scored_count = TableRoundScore.objects.filter(
            quiz=quiz, question=question
        ).count()
        data["scored_count"] = scored_count
        all_scored = scored_count >= total_tables and total_tables > 0

        # If all tables scored, include reveal results
        if all_scored:
            reveal = []
            for s in TableRoundScore.objects.filter(
                quiz=quiz, question=question
            ).select_related("table"):
                reveal.append(
                    {
                        "table_id": s.table_id,
                        "table_number": s.table.table_number,
                        "is_correct": s.is_correct,
                    }
                )
            reveal.sort(key=lambda x: x["table_number"])
            data["reveal_results"] = reveal

    # Include leaderboard
    leaderboard_tables = []
    for table in QuizTable.objects.filter(quiz=quiz).order_by("table_number"):
        leaderboard_tables.append(
            {
                "table_number": table.table_number,
                "total_score": table.get_total_score(),
            }
        )
    leaderboard_tables.sort(key=lambda x: x["total_score"], reverse=True)
    data["leaderboard_tables"] = leaderboard_tables

    # Individual top scorers
    from crush_lu.models.quiz import IndividualScore
    from django.db.models import Sum

    top_individuals = (
        IndividualScore.objects.filter(quiz=quiz)
        .values("user_id")
        .annotate(total=Sum("points_earned"))
        .order_by("-total")[:10]
    )
    individual_scores = []
    for entry in top_individuals:
        try:
            profile = CrushProfile.objects.get(user_id=entry["user_id"])
            name = profile.display_name
            has_photo = bool(getattr(profile, "photo_1", None))
            photo_url = (
                f"/api/quiz/photo/{entry['user_id']}/" if has_photo else None
            )
        except CrushProfile.DoesNotExist:
            name = "Anonymous"
            photo_url = None
        individual_scores.append(
            {
                "display_name": name,
                "total_score": entry["total"],
                "initials": _member_initials(name),
                "color": _member_color(name),
                "photo_url": photo_url,
            }
        )
    data["leaderboard_individuals"] = individual_scores

    return JsonResponse(data)


@login_required
def quiz_display_photo(request, user_id):
    """Serve photo_1 for quiz display.

    Only serves photos for users who have an approved CrushProfile.
    Returns a cache-friendly response suitable for projector displays.
    """
    profile = get_object_or_404(CrushProfile, user_id=user_id)

    if not profile.is_approved:
        raise Http404("Profile not approved")

    photo = getattr(profile, "photo_1", None)
    if not photo:
        raise Http404("No photo")

    # Azure Blob Storage: redirect with SAS token
    if hasattr(settings, "AZURE_ACCOUNT_NAME") and settings.AZURE_ACCOUNT_NAME:
        from crush_lu.storage import CrushProfilePhotoStorage
        from django.shortcuts import redirect

        storage = CrushProfilePhotoStorage()
        secure_url = storage.url(photo.name, expire=1800)
        response = redirect(secure_url)
        response["Cache-Control"] = "private, max-age=1800"
        return response

    # Local filesystem
    photo_path = photo.path
    if not os.path.exists(photo_path):
        raise Http404("Photo file not found")

    with open(photo_path, "rb") as f:
        content_type = "image/jpeg"
        if photo_path.lower().endswith(".png"):
            content_type = "image/png"
        elif photo_path.lower().endswith(".webp"):
            content_type = "image/webp"
        response = HttpResponse(f.read(), content_type=content_type)
        response["Content-Disposition"] = "inline"
        response["Cache-Control"] = "private, max-age=1800"
        return response
