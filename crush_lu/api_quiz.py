import json

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _parse_choices(choices):
    """Safely parse question choices — handles list-of-dicts, list-of-strings, and JSON strings."""
    if isinstance(choices, str):
        try:
            choices = json.loads(choices)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(choices, list):
        return []
    # Normalize: if items are plain strings, wrap them in {"text": ...}
    result = []
    for c in choices:
        if isinstance(c, dict):
            result.append(c)
        elif isinstance(c, str):
            result.append({"text": c, "is_correct": False})
    return result

from crush_lu.models.quiz import (
    IndividualScore,
    QuizEvent,
    QuizRotationSchedule,
    QuizTable,
    QuizTableMembership,
    TableRoundScore,
)


def _is_quiz_host(quiz, user):
    """Check if user can host/score this quiz (Issue #6: include coaches)."""
    if quiz.created_by == user or user.is_staff:
        return True
    from crush_lu.models import CrushCoach

    return CrushCoach.objects.filter(user=user, is_active=True).exists()


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
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
            choices = _parse_choices(question.choices)
            q_data["choices"] = [
                {"text": c["text"]} for c in choices if isinstance(c, dict)
            ]
        data["question"] = q_data

    return Response(data)


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
def quiz_tables(request, quiz_id):
    """Return table assignments for a quiz, optionally filtered by round."""
    round_num = request.query_params.get("round")

    tables = QuizTable.objects.filter(quiz_id=quiz_id).order_by("table_number")
    data = []

    for table in tables:
        members = []

        if round_num is not None:
            # INPUT-01: Validate round_num is a valid integer
            try:
                round_num_int = int(round_num)
            except (ValueError, TypeError):
                return Response(
                    {"error": "Invalid round number"}, status=400
                )
            rotations = QuizRotationSchedule.objects.filter(
                quiz_id=quiz_id,
                round_number=round_num_int,
                table=table,
            ).select_related("user__crushprofile")
            for rotation in rotations:
                profile = getattr(rotation.user, "crushprofile", None)
                # AUTHZ-02: Removed user_id to avoid exposing internal IDs
                members.append(
                    {
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
                # AUTHZ-02: Removed user_id to avoid exposing internal IDs
                members.append(
                    {
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
@authentication_classes([SessionAuthentication])
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
    round_number = quiz.get_round_number()

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
            return Response(
                {"error": "Not assigned to a table"}, status=404
            )

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


@login_required
def score_table(request, quiz_id):
    """Host-only REST endpoint to score a table for a question."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        quiz = QuizEvent.objects.select_related(
            "event", "current_round"
        ).get(id=quiz_id)
    except QuizEvent.DoesNotExist:
        return JsonResponse({"error": "Quiz not found"}, status=404)

    # Issue #6: Allow coaches, not just creator/staff
    if not _is_quiz_host(quiz, request.user):
        return JsonResponse({"error": "Not authorized"}, status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    table_id = body.get("table_id")
    question_id = body.get("question_id")
    is_correct = body.get("is_correct", False)

    if table_id is None or question_id is None:
        return JsonResponse(
            {"error": "table_id and question_id required"}, status=400
        )

    from crush_lu.models.quiz import QuizQuestion

    try:
        table = QuizTable.objects.get(id=table_id, quiz=quiz)
        question = QuizQuestion.objects.select_related("round").get(
            id=question_id, round__quiz=quiz
        )
    except (QuizTable.DoesNotExist, QuizQuestion.DoesNotExist):
        return JsonResponse(
            {"error": "Table or question not found"}, status=404
        )

    # CONC-04: Wrap scoring in atomic transaction to ensure all-or-nothing
    from django.db import transaction

    with transaction.atomic():
        # Only allow scoring once per table per question (consistent with WebSocket path)
        _obj, created = TableRoundScore.objects.get_or_create(
            quiz=quiz,
            table=table,
            question=question,
            defaults={"is_correct": is_correct},
        )
        if not created:
            return JsonResponse(
                {
                    "table_number": table.table_number,
                    "already_scored": True,
                },
                status=409,
            )

        # Issue #4: Read bonus from question's own round, not quiz.current_round
        points = question.points if is_correct else 0
        if is_correct and question.round.is_bonus:
            points *= 2

        # Issue #3: Use round index, not sort_order
        round_number = quiz.get_round_number()

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
            IndividualScore.objects.get_or_create(
                quiz=quiz,
                user_id=user_id,
                question=question,
                defaults={
                    "is_correct": is_correct,
                    "points_earned": points,
                    "answer": f"table_scored:{table.table_number}",
                },
            )

    return JsonResponse(
        {
            "table_number": table.table_number,
            "is_correct": is_correct,
            "points_awarded": points,
            "members_scored": len(rotation_users),
        }
    )


@login_required
def regenerate_tables(request, quiz_id):
    """Host-only endpoint to regenerate table assignments based on current attendees."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        quiz = QuizEvent.objects.select_related("event").get(id=quiz_id)
    except QuizEvent.DoesNotExist:
        return JsonResponse({"error": "Quiz not found"}, status=404)

    if not _is_quiz_host(quiz, request.user):
        return JsonResponse({"error": "Not authorized"}, status=403)

    if quiz.status == "active":
        return JsonResponse(
            {"error": "Cannot regenerate tables while quiz is active. Pause or end the quiz first."},
            status=409,
        )

    from django.utils import timezone

    from crush_lu.models.events import EventRegistration
    from crush_lu.services.quiz_rotation import (
        generate_rotation_schedule,
        split_participants_by_gender,
    )

    # Get currently attended or confirmed registrations
    registrations = (
        EventRegistration.objects.filter(
            event=quiz.event, status__in=["confirmed", "attended"]
        )
        .select_related("user__crushprofile")
        .order_by("registered_at")
    )

    if registrations.count() < 4:
        return JsonResponse(
            {
                "error": f"Need at least 4 registrations, "
                f"got {registrations.count()}."
            },
            status=400,
        )

    men, women = split_participants_by_gender(registrations)

    try:
        num_rounds = quiz.rounds.count() or 3
        result = generate_rotation_schedule(
            men, women, num_rounds, num_tables=quiz.num_tables
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

    schedule = result["schedule"]
    actual_num_tables = result["num_tables"]

    # Clear rotation data and memberships (preserve tables for score FKs)
    QuizRotationSchedule.objects.filter(quiz=quiz).delete()
    QuizTableMembership.objects.filter(table__quiz=quiz).delete()

    # Reuse or create tables
    existing_tables = {
        t.table_number: t for t in QuizTable.objects.filter(quiz=quiz)
    }
    tables = {}
    for t in range(1, actual_num_tables + 1):
        if t in existing_tables:
            tables[t] = existing_tables[t]
        else:
            tables[t] = QuizTable.objects.create(quiz=quiz, table_number=t)

    # Remove excess tables only if they have no scores
    for t_num, t_obj in existing_tables.items():
        if t_num > actual_num_tables:
            if not t_obj.round_scores.exists():
                t_obj.delete()

    # Build rotation schedule and round-0 memberships
    rotation_entries = []
    round_0_members = set()
    for entry in schedule:
        table = tables[entry["table_number"]]
        rotation_entries.append(
            QuizRotationSchedule(
                quiz=quiz,
                round_number=entry["round_number"],
                table=table,
                user=entry["user"],
                role=entry["role"],
                rotation_group=entry["rotation_group"],
            )
        )
        if entry["round_number"] == 0:
            round_0_members.add((entry["table_number"], entry["user"]))

    QuizRotationSchedule.objects.bulk_create(rotation_entries)

    memberships = []
    for table_num, user in round_0_members:
        memberships.append(
            QuizTableMembership(table=tables[table_num], user=user)
        )
    QuizTableMembership.objects.bulk_create(memberships)

    quiz.tables_generated_at = timezone.now()
    quiz.save(update_fields=["tables_generated_at"])

    return JsonResponse(
        {
            "num_tables": actual_num_tables,
            "anchors": len(men),
            "rotators": len(women),
            "warnings": result["warnings"],
        }
    )
