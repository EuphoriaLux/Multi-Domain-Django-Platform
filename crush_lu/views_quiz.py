from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from crush_lu.models.quiz import QuizEvent


@login_required
def quiz_live_view(request, event_id):
    """Attendee view for the live quiz."""
    quiz = get_object_or_404(
        QuizEvent.objects.select_related("event", "current_round"),
        event_id=event_id,
    )
    return render(
        request,
        "crush_lu/quiz_live.html",
        {"quiz": quiz, "event": quiz.event},
    )


@login_required
def quiz_coach_view(request, event_id):
    """Coach control panel for the live quiz."""
    quiz = get_object_or_404(
        QuizEvent.objects.select_related("event", "current_round"),
        event_id=event_id,
    )
    # Only quiz creator or staff can access coach view
    if quiz.created_by != request.user and not request.user.is_staff:
        raise Http404

    rounds = quiz.rounds.prefetch_related("questions").order_by("sort_order")
    return render(
        request,
        "crush_lu/quiz_coach.html",
        {"quiz": quiz, "event": quiz.event, "rounds": rounds},
    )
