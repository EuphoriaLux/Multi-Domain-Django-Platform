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
            # Use rotation schedule for specific round
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
            # Default: static membership
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
    current_round_num = 0
    if quiz.current_round:
        current_round_num = quiz.current_round.sort_order

    # Look up rotation schedule
    rotation = (
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=current_round_num,
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
            round_number=current_round_num,
            table=rotation.table,
        )
        .exclude(user=user)
        .select_related("user__crushprofile")
    ):
        profile = getattr(r.user, "crushprofile", None)
        tablemates.append(
            {
                "display_name": profile.display_name if profile else "Anonymous",
                "role": r.role,
            }
        )

    # Peek at next round table
    next_table_number = None
    next_rotation = (
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=current_round_num + 1,
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

    # Only host/staff can score
    if quiz.created_by != request.user and not request.user.is_staff:
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
        question = QuizQuestion.objects.get(id=question_id, round__quiz=quiz)
    except (QuizTable.DoesNotExist, QuizQuestion.DoesNotExist):
        return Response({"error": "Table or question not found"}, status=404)

    # Create or update TableRoundScore
    TableRoundScore.objects.update_or_create(
        quiz=quiz,
        table=table,
        question=question,
        defaults={"is_correct": is_correct},
    )

    points = question.points if is_correct else 0
    if is_correct and quiz.current_round and quiz.current_round.is_bonus:
        points *= 2

    # Get users at this table for the current round
    current_round_num = 0
    if quiz.current_round:
        current_round_num = quiz.current_round.sort_order

    rotation_users = list(
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=current_round_num,
            table=table,
        ).values_list("user_id", flat=True)
    )

    if not rotation_users:
        rotation_users = list(
            QuizTableMembership.objects.filter(
                table=table
            ).values_list("user_id", flat=True)
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
