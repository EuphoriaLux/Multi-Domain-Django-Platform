"""Create a Quiz Night event and seed it from a question pack, in one step.

``generate_crush_quiz`` fills a QuizEvent that already exists. This command
covers the step before it: it creates the MeetupEvent, its QuizEvent, the
tables, and the questions together, so a media-pack quiz can be stood up from a
clean database without clicking through the coach UI first.

The event is created **unpublished** unless ``--publish`` is passed — it is a
draft a coach reviews (date, venue, capacity) and publishes when ready. Re-runs
match on the exact title, so the command is idempotent and safe to repeat; it
refuses to seed over an existing set of rounds unless ``--clear`` is given.

Usage:
    python manage.py create_quiz_night_event
    python manage.py create_quiz_night_event --pack classic --tables 8
    python manage.py create_quiz_night_event --date 2026-09-19T19:30 --publish
    python manage.py create_quiz_night_event --dry-run
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from crush_lu.management.commands.generate_crush_quiz import (
    populate_quiz,
    validate_pack,
)
from crush_lu.models.events import MeetupEvent
from crush_lu.models.quiz import QuizEvent
from crush_lu.quiz_packs import PACKS, pack_names

DEFAULT_TITLE = "Crush Quiz Night — Media Edition"
DEFAULT_PACK = "media-love"

#: How far ahead the event lands when no ``--date`` is given. A placeholder a
#: coach is expected to correct, not a scheduling decision.
DEFAULT_DAYS_AHEAD = 30

#: Registration closes this long before the doors open.
REGISTRATION_CUTOFF = timedelta(days=1)

DEFAULT_DESCRIPTION_EN = (
    "A quiz night about love, dating and everything in between — played in "
    "teams, with film clips, love songs and famous couples on the big screen. "
    "Questions run in English, German and French."
)


class Command(BaseCommand):
    help = "Create a Quiz Night event and seed its questions from a pack"

    def add_arguments(self, parser):
        parser.add_argument("--title", default=DEFAULT_TITLE)
        parser.add_argument(
            "--pack",
            default=DEFAULT_PACK,
            choices=pack_names(),
            help=f"Question pack to seed (default: {DEFAULT_PACK})",
        )
        parser.add_argument(
            "--date",
            help=(
                "Event start, ISO 8601 (e.g. 2026-09-19T19:30). "
                f"Defaults to {DEFAULT_DAYS_AHEAD} days from now at 19:30."
            ),
        )
        parser.add_argument("--tables", type=int, default=6)
        parser.add_argument("--location", default="Luxembourg City")
        parser.add_argument("--address", default="To be confirmed")
        parser.add_argument(
            "--canton",
            default="Luxembourg",
            help="Region shown publicly. Required when publishing.",
        )
        parser.add_argument("--max-participants", type=int, default=48)
        parser.add_argument(
            "--created-by",
            help=(
                "Username to record as the quiz's owner. Defaults to the first "
                "superuser, then the first staff user."
            ),
        )
        parser.add_argument(
            "--publish",
            action="store_true",
            help="Publish immediately instead of leaving the event as a draft",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Replace the quiz's existing rounds instead of refusing",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate the pack and report what would happen; write nothing",
        )

    def handle(self, *args, **options):
        pack = options["pack"]
        tables = options["tables"]
        if tables < 2:
            raise CommandError("--tables must be at least 2.")

        # Fail on the pack before touching the database: a stub pack should not
        # leave an empty event behind.
        problems = validate_pack(PACKS[pack])
        if problems:
            raise CommandError(f"Pack '{pack}' is invalid:\n  " + "\n  ".join(problems))

        starts_at = self._resolve_date(options.get("date"))

        if options["dry_run"]:
            self._report_dry_run(options, pack, starts_at, tables)
            return

        owner = self._resolve_owner(options.get("created_by"))

        with transaction.atomic():
            event, event_created = self._get_or_create_event(options, starts_at)
            quiz, quiz_created = QuizEvent.objects.get_or_create(
                event=event,
                defaults={
                    "status": "draft",
                    "num_tables": tables,
                    "created_by": owner,
                },
            )
            if quiz_created:
                quiz.ensure_tables()

            existing_rounds = quiz.rounds.count()
            if existing_rounds and not options["clear"]:
                raise CommandError(
                    f"'{event.title}' already has {existing_rounds} rounds. "
                    f"Re-run with --clear to replace them."
                )

            result = populate_quiz(quiz, clear=options["clear"], pack=pack)

        self._report(event, quiz, result, pack, event_created)

    # --- helpers ---------------------------------------------------------

    def _resolve_owner(self, username):
        """Return the User to record as the quiz's creator.

        ``QuizEvent.created_by`` is a non-nullable FK, so a quiz cannot exist
        without one. Resolved before the transaction opens: an unknown username
        should fail before an event row is written.
        """
        User = get_user_model()
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"No user with username {username!r}.")

        owner = (
            User.objects.filter(is_superuser=True).order_by("pk").first()
            or User.objects.filter(is_staff=True).order_by("pk").first()
        )
        if owner is None:
            raise CommandError(
                "No superuser or staff user exists to own the quiz. "
                "Create one, or pass --created-by <username>."
            )
        return owner

    def _resolve_date(self, raw):
        """Return an aware datetime for the event start."""
        if not raw:
            return timezone.localtime(
                timezone.now() + timedelta(days=DEFAULT_DAYS_AHEAD)
            ).replace(hour=19, minute=30, second=0, microsecond=0)

        parsed = parse_datetime(raw)
        if parsed is None:
            raise CommandError(
                f"Could not parse --date {raw!r}. Use ISO 8601, e.g. 2026-09-19T19:30."
            )
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed

    def _get_or_create_event(self, options, starts_at):
        """Match on the exact title so re-runs update rather than duplicate."""
        return MeetupEvent.objects.get_or_create(
            title=options["title"],
            defaults={
                "description": DEFAULT_DESCRIPTION_EN,
                "event_type": "quiz_night",
                "location": options["location"],
                "address": options["address"],
                "canton": options["canton"],
                "date_time": starts_at,
                "registration_deadline": starts_at - REGISTRATION_CUTOFF,
                "max_participants": options["max_participants"],
                "is_published": options["publish"],
            },
        )

    def _report_dry_run(self, options, pack, starts_at, tables):
        rounds = PACKS[pack]
        questions = sum(len(r.get("questions") or []) for r in rounds)
        media = sum(
            1 for r in rounds for q in (r.get("questions") or []) if q.get("media")
        )
        self.stdout.write("Dry run — nothing was written.")
        self.stdout.write(f"  event    : {options['title']}")
        self.stdout.write(f"  starts   : {starts_at:%Y-%m-%d %H:%M %Z}")
        self.stdout.write(f"  tables   : {tables}")
        self.stdout.write(
            f"  pack     : {pack} — {len(rounds)} rounds, {questions} questions, "
            f"{media} with media (valid)"
        )
        self.stdout.write(
            f"  published: {'yes' if options['publish'] else 'no (draft)'}"
        )

    def _report(self, event, quiz, result, pack, event_created):
        verb = "Created" if event_created else "Reused"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} Quiz Night '{event.title}' (event id={event.pk}, "
                f"quiz id={quiz.pk}) starting {event.date_time:%Y-%m-%d %H:%M}."
            )
        )
        self.stdout.write(
            f"Seeded {result.rounds_created} rounds and "
            f"{result.questions_created} questions from pack '{pack}'."
        )

        if not event.is_published:
            self.stdout.write(
                self.style.WARNING(
                    "The event is a DRAFT. Review the date, venue and capacity, "
                    "then publish it from the admin or re-run with --publish."
                )
            )

        self.stdout.write("")
        self.stdout.write("Readiness:")
        for check in quiz.readiness_check():
            mark = "ok  " if check["ok"] else "TODO"
            self.stdout.write(f"  [{mark}] {check['label']}: {check['detail']}")
        self.stdout.write(
            "  (Registrations and table assignments come from real check-ins "
            "on the night — seeding cannot satisfy them.)"
        )

        if result.pending_uploads:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"{len(result.pending_uploads)} image question(s) still need a "
                    f"file uploaded through the quiz authoring UI:"
                )
            )
            for item in result.pending_uploads:
                self.stdout.write(f"  [{item.round_title}] {item.question_text}")
                self.stdout.write(f"      file: {item.filename}")
                self.stdout.write(f"      alt : {item.description}")
                if item.credit:
                    self.stdout.write(f"      src : {item.credit}")

        self.stdout.write("")
        self.stdout.write(f"Authoring UI: /coach/events/{event.pk}/quiz/config/")
        self.stdout.write(f"Host screen : /events/{event.pk}/quiz/coach/")
