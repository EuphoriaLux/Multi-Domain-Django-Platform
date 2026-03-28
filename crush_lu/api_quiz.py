from django.db.models import Sum
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from crush_lu.models.quiz import (
    IndividualScore,
    QuizEvent,
    QuizTable,
    QuizTableMembership,
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
        "status": quiz.status,
        "current_round": None,
        "question": None,
        "question_index": quiz.current_question_index,
    }

    if quiz.current_round:
        data["current_round"] = {
            "title": quiz.current_round.title,
            "time_per_question": quiz.current_round.time_per_question,
        }

    if question and quiz.is_active:
        data["question"] = {
            "id": question.id,
            "text": question.text,
            "choices": [{"text": c["text"]} for c in question.choices],
            "points": question.points,
        }

    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def quiz_tables(request, quiz_id):
    """Return table assignments for a quiz."""
    tables = QuizTable.objects.filter(quiz_id=quiz_id).order_by("table_number")
    data = []
    for table in tables:
        members = []
        for membership in table.memberships.select_related("user__crushprofile"):
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
