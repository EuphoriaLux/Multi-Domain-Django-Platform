import json

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from crush_lu.models import CrushCoach
from crush_lu.models.events import EventRegistration
from crush_lu.models.quiz import (
    QuizEvent,
    QuizRotationSchedule,
    QuizTable,
    QuizTableMembership,
)


def _get_table_members_json(quiz, round_number=0):
    """Build JSON-safe list of tables with their current members."""
    tables = QuizTable.objects.filter(quiz=quiz).order_by("table_number")
    result = []
    for table in tables:
        members = []
        # Try rotation schedule first
        rotations = (
            QuizRotationSchedule.objects.filter(
                quiz=quiz, round_number=round_number, table=table
            )
            .select_related("user__crushprofile")
        )
        if rotations.exists():
            for r in rotations:
                profile = getattr(r.user, "crushprofile", None)
                members.append({
                    "display_name": profile.display_name if profile else "Anonymous",
                    "role": r.role,
                })
        else:
            # Fall back to static membership
            for m in table.memberships.select_related("user__crushprofile"):
                profile = getattr(m.user, "crushprofile", None)
                members.append({
                    "display_name": profile.display_name if profile else "Anonymous",
                    "role": "",
                })
        result.append({
            "table_number": table.table_number,
            "members": members,
            "total_score": table.get_total_score(),
        })
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
            QuizTableMembership.objects.filter(
                table__quiz=quiz, user=request.user
            )
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
                tablemates.append({
                    "display_name": profile.display_name if profile else "Anonymous",
                    "role": r.role,
                })
        else:
            mates = (
                user_table.memberships
                .exclude(user=request.user)
                .select_related("user__crushprofile")
            )
            for m in mates:
                profile = getattr(m.user, "crushprofile", None)
                tablemates.append({
                    "display_name": profile.display_name if profile else "Anonymous",
                    "role": "",
                })

    # For coaches/staff who aren't assigned to a table, provide the full
    # table overview so they can see the setup from the participant view
    is_coach_viewer = not user_table and (
        request.user.is_staff
        or CrushCoach.objects.filter(user=request.user, is_active=True).exists()
    )
    all_tables_json = ""
    if is_coach_viewer and is_quiz_night:
        all_tables_json = json.dumps(
            _get_table_members_json(quiz, round_number)
        )

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
    # Quiz creator, staff, or assigned coaches can access host view
    is_coach = CrushCoach.objects.filter(
        user=request.user, is_active=True
    ).exists()
    if (
        quiz.created_by != request.user
        and not request.user.is_staff
        and not is_coach
    ):
        raise Http404

    rounds = quiz.rounds.prefetch_related("questions").order_by("sort_order")
    tables = QuizTable.objects.filter(quiz=quiz).order_by("table_number")
    is_quiz_night = quiz.event.event_type == "quiz_night"

    # Build table members data for the overview panel
    round_number = quiz.get_round_number()
    table_members = _get_table_members_json(quiz, round_number)

    context = {
        "quiz": quiz,
        "event": quiz.event,
        "rounds": rounds,
        "tables": tables,
        "is_quiz_night": is_quiz_night,
        "table_members_json": json.dumps(table_members),
    }
    return render(request, "crush_lu/quiz_coach.html", context)
