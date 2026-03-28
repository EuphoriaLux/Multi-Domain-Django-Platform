from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from crush_lu.models import CrushCoach
from crush_lu.models.events import EventRegistration
from crush_lu.models.quiz import QuizEvent, QuizRotationSchedule, QuizTable


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

    # Get user's current table assignment
    current_round_num = 0
    if quiz.current_round:
        current_round_num = quiz.current_round.sort_order

    rotation = (
        QuizRotationSchedule.objects.filter(
            quiz=quiz,
            round_number=current_round_num,
            user=request.user,
        )
        .select_related("table")
        .first()
    )

    is_quiz_night = quiz.event.event_type == "quiz_night"

    context = {
        "quiz": quiz,
        "event": quiz.event,
        "is_quiz_night": is_quiz_night,
        "user_table_number": rotation.table.table_number if rotation else None,
        "user_role": rotation.role if rotation else None,
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

    context = {
        "quiz": quiz,
        "event": quiz.event,
        "rounds": rounds,
        "tables": tables,
        "is_quiz_night": is_quiz_night,
    }
    return render(request, "crush_lu/quiz_coach.html", context)
