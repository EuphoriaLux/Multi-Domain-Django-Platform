"""
Ensure the current (or a given) ISO week's Crush Connect question set exists.

Members pick their 3 gate questions from this weekly set; the set is built once
per ISO week by a deterministic weighted pick of the active catalogue, so this
command is idempotent — re-running never re-rolls an existing week. Run from a
dev shell, or let the Azure Function timer drive it via
``/api/admin/rotate-connect-questions/`` on Monday mornings.

    python manage.py rotate_connect_questions               # current ISO week
    python manage.py rotate_connect_questions --week-start 2026-06-29
    python manage.py rotate_connect_questions --dry-run     # report only, no write
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Ensure this week's Crush Connect question set exists (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--week-start",
            type=str,
            help="Any date (YYYY-MM-DD) within the target ISO week. Defaults to today.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without creating the week.",
        )

    def handle(self, *args, **options):
        from crush_lu.models import ConnectQuestionWeek
        from crush_lu.services.crush_connect import (
            WEEKLY_CATALOGUE_SIZE,
            get_or_create_question_week,
        )

        if options["week_start"]:
            try:
                day = datetime.strptime(options["week_start"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError(f"Invalid --week-start: {exc}") from exc
        else:
            day = timezone.localdate()

        iso = day.isocalendar()
        exists = ConnectQuestionWeek.objects.filter(
            iso_year=iso.year, iso_week=iso.week
        ).exists()

        if options["dry_run"]:
            if exists:
                self.stdout.write(
                    f"ISO week {iso.year}-W{iso.week:02d} already exists — no-op."
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"ISO week {iso.year}-W{iso.week:02d} would be built with up to "
                        f"{WEEKLY_CATALOGUE_SIZE} active questions."
                    )
                )
            return

        week = get_or_create_question_week(day)
        self.stdout.write(
            self.style.SUCCESS(
                f"{'Reused' if exists else 'Created'} question week "
                f"{week.iso_year}-W{week.iso_week:02d} "
                f"({week.questions.count()} questions)."
            )
        )
