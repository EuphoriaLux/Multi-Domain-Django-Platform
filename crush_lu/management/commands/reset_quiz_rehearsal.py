"""Reset a Quiz Night to a clean, replayable draft (staging / local only).

Works from any state, including an active quiz, wipes scores and seating, and
re-seats the attended guests.

Usage:
    python manage.py reset_quiz_rehearsal --event-id 12
    python manage.py reset_quiz_rehearsal --event-id 12 --no-reseed
"""

from django.core.management.base import BaseCommand, CommandError

from crush_lu.models.quiz import QuizEvent
from crush_lu.services.quiz_rehearsal import (
    broadcast_reset,
    rehearsal_enabled,
    reset_quiz_for_rehearsal,
)


class Command(BaseCommand):
    help = "Reset a Quiz Night to draft for rehearsal (staging/local only)"

    def add_arguments(self, parser):
        parser.add_argument("--event-id", type=int, required=True)
        parser.add_argument("--no-reseed", action="store_true")

    def handle(self, *args, **options):
        if not rehearsal_enabled():
            raise CommandError("Quiz rehearsal is only available on staging/local.")
        try:
            quiz = QuizEvent.objects.get(event_id=options["event_id"])
        except QuizEvent.DoesNotExist:
            raise CommandError(f"No quiz for event {options['event_id']}.")
        data = reset_quiz_for_rehearsal(quiz, reseed=not options["no_reseed"])
        broadcast_reset(quiz, data)
        self.stdout.write(self.style.SUCCESS(f"Quiz for event {quiz.event_id} reset."))
