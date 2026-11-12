"""Stand up a ready-to-play Quiz Night rehearsal (staging / local only).

Wraps ``create_quiz_night_event`` (event, quiz, tables, pack), then:
  * makes the event free, starting soon, published;
  * assigns the named coaches to the event and registers them as attended
    guests so each can open the phone view;
  * seats N synthetic guests at tables.

Idempotent by title. Re-run, or use ``reset_quiz_rehearsal``, to replay.

Usage:
    python manage.py create_quiz_rehearsal --coach alice --coach bob
    python manage.py create_quiz_rehearsal --guests 12 --pack classic --tables 4
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_lu.models import CrushCoach
from crush_lu.models.events import MeetupEvent
from crush_lu.models.quiz import QuizEvent
from crush_lu.services.quiz_rehearsal import (
    add_coaches_as_guests,
    add_rehearsal_guests,
    rehearsal_enabled,
)

TITLE = "Quiz Night REHEARSAL"


class Command(BaseCommand):
    help = "Create a ready-to-play Quiz Night rehearsal (staging/local only)"

    def add_arguments(self, parser):
        parser.add_argument("--title", default=TITLE)
        parser.add_argument("--pack", default="classic")
        parser.add_argument("--tables", type=int, default=4)
        parser.add_argument("--guests", type=int, default=12)
        parser.add_argument(
            "--coach",
            action="append",
            default=[],
            help="Username of a coach to assign as host and guest (repeatable)",
        )
        parser.add_argument("--created-by")

    def handle(self, *args, **options):
        if not rehearsal_enabled():
            raise CommandError("Quiz rehearsal is only available on staging/local.")

        starts_at = timezone.now() + timedelta(hours=2)
        cmd_kwargs = dict(
            title=options["title"],
            pack=options["pack"],
            tables=options["tables"],
            date=starts_at.isoformat(),
            publish=True,
            max_participants=200,
            stdout=self.stdout,
        )
        if options["created_by"]:
            cmd_kwargs["created_by"] = options["created_by"]
        try:
            call_command("create_quiz_night_event", **cmd_kwargs)
        except CommandError as exc:
            # An existing rehearsal that already has rounds is the normal re-run.
            if "already has" not in str(exc):
                raise

        event = MeetupEvent.objects.get(title=options["title"], event_type="quiz_night")
        # Free, and open far enough ahead that no time gate blocks coaches.
        event.registration_fee = 0
        event.date_time = starts_at
        event.registration_deadline = starts_at - timedelta(minutes=5)
        event.is_published = True
        event.save(
            update_fields=[
                "registration_fee",
                "date_time",
                "registration_deadline",
                "is_published",
            ]
        )
        quiz = QuizEvent.objects.get(event=event)

        User = get_user_model()
        coach_users = []
        for username in options["coach"]:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"No user {username!r}.")
            coach = CrushCoach.objects.filter(user=user, is_active=True).first()
            if coach is None:
                raise CommandError(f"{username!r} is not an active coach.")
            coach.assigned_events.add(event)
            coach_users.append(user)

        seated = add_rehearsal_guests(quiz, options["guests"])
        add_coaches_as_guests(quiz, coach_users)

        self.stdout.write(
            self.style.SUCCESS(
                f"Rehearsal ready: event {event.pk}, {seated} test guests, "
                f"{len(coach_users)} coach guest(s)."
            )
        )
        self.stdout.write(f"Host screen : /events/{event.pk}/quiz/coach/")
        self.stdout.write(f"Big screen  : /quiz/{event.pk}/display/")
        self.stdout.write(
            f"Reset       : manage.py reset_quiz_rehearsal --event-id {event.pk}"
        )
