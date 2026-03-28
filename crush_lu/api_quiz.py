from django.db.models import Sum
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from crush_lu.models.quiz import (
    IndividualScore,
    QuizEvent,
    QuizRotationSchedule,
    QuizTable,
    QuizTableMembership,
    TableRoundScore,
)


def _get_current_round_number(quiz):
    """Get the rotation round_number (0-indexed) for the current round.

    Issue #3: round_number in QuizRotationSchedule is the index of the
    round among all rounds (0, 1, 2, ...), NOT the sort_order value.
    """
    if not quiz.current_round:
        return 0
    return quiz.rounds.filter(
        sort_order__lt=quiz.current_round.sort_order
    ).count()


def _is_quiz_host(quiz, user):
    """Check if user can host/score this quiz (Issue #6: include coaches)."""
    if quiz.created_by == user or user.is_staff:
        return True
    from crush_lu.models import CrushCoach

    return CrushCoach.objects.filter(user=user, is_active=True).exists()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def quiz_state(request, quiz_id):
    """Return current quiz state for initial page load."""
    try:
        quiz = QuizEvent.objects.select_related(
            "event", "current_round"
        ).get(id=quiz_id)
    except QuizEvent.DoesNotExist:
        return Response({"error": "Quiz not found"}, status=404)

    question = quiz.get_current_question()
    data = {
        "id": quiz.id,
        "event_title": str(quiz.event),
        "event_type": quiz.event.event_type,
        "status": quiz.status,
        "current_round": None,
        "question": None,
        "question_index": quiz.current_question_index,
    }

    if quiz.current_round:
        data["current_round"] = {
            "id": quiz.current_round.id,
            "title": quiz.current_round.title,
            "time_per_question": quiz.current_round.time_per_question,
            "is_bonus": quiz.current_round.is_bonus,
        }

    if question and quiz.is_active:
        q_data = {
            "id": question.id,
            "text": question.text,
            "question_type": question.question_type,
            "points": question.points,
        }
        if question.question_type in ("multiple_choice", "true_false"):
            q_data["choices"] = [{"text": c["text"]} for c in question.choices]
        data["question"] = q_data

    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def quiz_tables(request, quiz_id):
    """Return table assignments for a quiz, optionally filtered by round."""
    round_num = request.query_params.get("round")

    tables = QuizTable.objects.filter(quiz_id=quiz_id).order_by("table_number")
    data = []

    for table in tables:
        members = []

        if round_num is not None:
            rotations = QuizRotationSchedule.objects.filter(
                quiz_id=quiz_id,
                round_number=int(round_num),
                table=table,
            ).select_related("user__crushprofile")
            for rotation in rotations:
                profile = getattr(rotation.user, "crushprofile", None)
                members.append(
                    {
                        "user_id": rotation.user_id,
                        "display_name": (
                            profile.display_name if profile else "Anonymous"
                        ),
                        "role": rotation.role,
                        "rotation_group": rotation.rotation_group,
                    }
                )
        else:
            for membership in table.memberships.select_related(
                "user__crushprofile"
            ):
                profile = getattr(membership.user, "crushprofile", None)
                members.append(
                    {
                        "user_id": membership.user_id,
                        "display_name": (
                            profile.display_name if profile else "Anonymous"
                        ),
                    }
                )

        data.append(
            {
                "table_number": table.table_number,
                "members": members,
                "total_score": table.get_total_score(),
            }
        )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_assignment(request, quiz_id):
    """Return current user's table assignment, role, tablemates, and score."""
    try:
        quiz = QuizEvent.objects.select_related(
            "event", "current_round"
        ).get(id=quiz_id)
    except QuizEvent.DoesNotExist:
        return Response({"error": "Quiz not found"}, status=404)

    user = request.user
    round_number = _get_current_round_number(quiz)

    # Look up rotation schedule
    rotation = (
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=round_number,
            user=user,
        )
        .select_related("table")
        .first()
    )

    if not rotation:
        # Fall back to static membership
        membership = (
            QuizTableMembership.objects.filter(
                table__quiz=quiz, user=user
            )
            .select_related("table")
            .first()
        )
        if not membership:
            return Response({"error": "Not assigned to a table"}, status=404)

        return Response(
            {
                "table_number": membership.table.table_number,
                "role": "unknown",
                "rotation_group": "",
                "tablemates": [],
                "personal_score": _get_personal_score(quiz, user),
            }
        )

    # Get tablemates for current round
    tablemates = []
    for r in (
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=round_number,
            table=rotation.table,
        )
        .exclude(user=user)
        .select_related("user__crushprofile")
    ):
        profile = getattr(r.user, "crushprofile", None)
        tablemates.append(
            {
                "display_name": (
                    profile.display_name if profile else "Anonymous"
                ),
                "role": r.role,
            }
        )

    # Peek at next round table
    next_table_number = None
    next_rotation = (
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=round_number + 1,
            user=user,
        )
        .select_related("table")
        .first()
    )
    if next_rotation:
        next_table_number = next_rotation.table.table_number

    return Response(
        {
            "table_number": rotation.table.table_number,
            "role": rotation.role,
            "rotation_group": rotation.rotation_group,
            "tablemates": tablemates,
            "personal_score": _get_personal_score(quiz, user),
            "next_table": next_table_number,
        }
    )


def _get_personal_score(quiz, user):
    return (
        IndividualScore.objects.filter(quiz=quiz, user=user).aggregate(
            total=Sum("points_earned")
        )["total"]
        or 0
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def score_table(request, quiz_id):
    """Host-only REST endpoint to score a table for a question."""
    try:
        quiz = QuizEvent.objects.select_related(
            "event", "current_round"
        ).get(id=quiz_id)
    except QuizEvent.DoesNotExist:
        return Response({"error": "Quiz not found"}, status=404)

    # Issue #6: Allow coaches, not just creator/staff
    if not _is_quiz_host(quiz, request.user):
        return Response({"error": "Not authorized"}, status=403)

    table_id = request.data.get("table_id")
    question_id = request.data.get("question_id")
    is_correct = request.data.get("is_correct", False)

    if table_id is None or question_id is None:
        return Response(
            {"error": "table_id and question_id required"}, status=400
        )

    from crush_lu.models.quiz import QuizQuestion

    try:
        table = QuizTable.objects.get(id=table_id, quiz=quiz)
        question = QuizQuestion.objects.select_related("round").get(
            id=question_id, round__quiz=quiz
        )
    except (QuizTable.DoesNotExist, QuizQuestion.DoesNotExist):
        return Response({"error": "Table or question not found"}, status=404)

    # Create or update TableRoundScore
    TableRoundScore.objects.update_or_create(
        quiz=quiz,
        table=table,
        question=question,
        defaults={"is_correct": is_correct},
    )

    # Issue #4: Read bonus from question's own round, not quiz.current_round
    points = question.points if is_correct else 0
    if is_correct and question.round.is_bonus:
        points *= 2

    # Issue #3: Use round index, not sort_order
    round_number = _get_current_round_number(quiz)

    rotation_users = list(
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=round_number,
            table=table,
        ).values_list("user_id", flat=True)
    )

    if not rotation_users:
        rotation_users = list(
            QuizTableMembership.objects.filter(table=table).values_list(
                "user_id", flat=True
            )
        )

    # Create IndividualScore for each table member
    for user_id in rotation_users:
        IndividualScore.objects.update_or_create(
            quiz=quiz,
            user_id=user_id,
            question=question,
            defaults={
                "is_correct": is_correct,
                "points_earned": points,
                "answer": f"table_scored:{table.table_number}",
            },
        )

    return Response(
        {
            "table_number": table.table_number,
            "is_correct": is_correct,
            "points_awarded": points,
            "members_scored": len(rotation_users),
        }
    )
