"""Stand up the permanent Quiz Night rehearsal (staging / local only).

Coaches can also do this themselves from the coach dashboard ("Quiz rehearsal"
link, staging only); this command is the shell equivalent.

Idempotent: re-running reuses the same event ("Quiz Night REHEARSAL"). Any
active coach can host it, so ``--coach`` only seats those coaches as guests.

Usage:
    python manage.py create_quiz_rehearsal --coach alice --coach bob
    python manage.py create_quiz_rehearsal --guests 12 --pack classic --tables 4
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from crush_lu.services.quiz_rehearsal import ensure_rehearsal_quiz, rehearsal_enabled


class Command(BaseCommand):
    help = "Create or reuse the permanent Quiz Night rehearsal (staging/local only)"

    def add_arguments(self, parser):
        parser.add_argument("--pack", default="classic")
        parser.add_argument("--tables", type=int, default=4)
        parser.add_argument("--guests", type=int, default=12)
        parser.add_argument(
            "--coach",
            action="append",
            default=[],
            help="Username of a coach to seat as a guest (repeatable)",
        )
        parser.add_argument("--created-by")

    def handle(self, *args, **options):
        if not rehearsal_enabled():
            raise CommandError("Quiz rehearsal is only available on staging/local.")

        User = get_user_model()
        coach_users = []
        for username in options["coach"]:
            try:
                coach_users.append(User.objects.get(username=username))
            except User.DoesNotExist:
                raise CommandError(f"No user {username!r}.")

        if options["created_by"]:
            owner = User.objects.filter(username=options["created_by"]).first()
        else:
            owner = (
                User.objects.filter(is_superuser=True).order_by("pk").first()
                or User.objects.filter(is_staff=True).order_by("pk").first()
            )
        if owner is None:
            raise CommandError("No owner user found. Pass --created-by <username>.")

        event, _quiz = ensure_rehearsal_quiz(
            owner,
            coach_users=coach_users,
            guests=options["guests"],
            pack=options["pack"],
            tables=options["tables"],
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Rehearsal ready: event {event.pk}, any coach can host."
            )
        )
        self.stdout.write(f"Host screen : /events/{event.pk}/quiz/coach/")
        self.stdout.write(f"Big screen  : /quiz/{event.pk}/display/")
        self.stdout.write(
            f"Reset       : manage.py reset_quiz_rehearsal --event-id {event.pk}"
        )
