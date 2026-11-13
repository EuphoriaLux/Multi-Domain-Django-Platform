"""Seed the coach playground cast on test.crush.lu (staging only).

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

Creates 12 ``cast-<name>@crush.test`` members (6 women, 6 men; Sam has no
LuxID) that the coach playground buttons drive. Idempotent: existing cast
members are kept and their activity refreshed. Prints no passwords: cast
members have none.

    /antenv/bin/python manage.py seed_playground_cast --as-coach coach@example.com
"""

from django.core.management.base import BaseCommand, CommandError

from crush_lu.services.staging_playground import playground_enabled


class Command(BaseCommand):
    help = "Seed the coach playground cast (staging or local DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--as-coach",
            dest="as_coach",
            default="",
            help="Email of the active coach who approves the cast photos "
            "(default: the first active coach).",
        )
        parser.add_argument(
            "--no-download",
            action="store_true",
            help="Use generated photos instead of downloading them.",
        )

    def handle(self, *args, **options):
        from crush_lu.models import CrushCoach
        from crush_lu.services.playground_cast import refresh_cast_activity, seed_cast

        if not playground_enabled():
            raise CommandError(
                "The coach playground cast can only be seeded on staging "
                "(DJANGO_ENV=staging) or in local DEBUG."
            )
        coaches = CrushCoach.objects.filter(is_active=True, user__is_active=True)
        if options["as_coach"]:
            coach = coaches.filter(user__email__iexact=options["as_coach"]).first()
        else:
            coach = coaches.order_by("pk").first()
        if coach is None:
            raise CommandError("No active coach found to approve the cast photos.")

        created, kept = seed_cast(
            coach,
            download=not options["no_download"],
            log=lambda line: self.stdout.write(f"  {line}"),
        )
        refresh_cast_activity(force=True)
        self.stdout.write(
            self.style.SUCCESS(f"Cast ready: {created} created, {kept} kept.")
        )
