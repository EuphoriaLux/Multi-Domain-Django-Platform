"""Ensure all Crush Cache preset hunts exist as daily-active staging rehearsals.

Staging / local DEBUG only. Mirrors ``create_quiz_rehearsal``: safe and
idempotent to run on every deploy or from the shell.

Usage:
    python manage.py ensure_cache_rehearsals
    python manage.py ensure_cache_rehearsals --created-by tom@crush.lu
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from crush_lu.services.cache_rehearsal import (
    cache_rehearsal_enabled,
    ensure_all_preset_rehearsal_hunts,
)


class Command(BaseCommand):
    help = (
        "Create or reuse daily-active Crush Cache rehearsal hunts (staging/local only)"
    )

    def add_arguments(self, parser):
        parser.add_argument("--created-by")

    def handle(self, *args, **options):
        if not cache_rehearsal_enabled():
            raise CommandError(
                "Crush Cache field-test rehearsals are only available on staging/local."
            )

        User = get_user_model()
        if options["created_by"]:
            owner = User.objects.filter(username=options["created_by"]).first()
            if owner is None:
                owner = User.objects.filter(email=options["created_by"]).first()
        else:
            owner = (
                User.objects.filter(is_superuser=True).order_by("pk").first()
                or User.objects.filter(is_staff=True).order_by("pk").first()
            )
        if owner is None:
            raise CommandError("No owner user found. Pass --created-by <username>.")

        ensured = ensure_all_preset_rehearsal_hunts(owner)
        self.stdout.write(
            self.style.SUCCESS(
                f"Crush Cache field-test hub ready ({len(ensured)} presets): /en/coach/cache-rehearsal/"
            )
        )
        for key, _preset, hunt in ensured:
            self.stdout.write(
                f"  - {key}: event={hunt.event_id} '{hunt.title}' "
                f"(play: /en/events/{hunt.event_id}/cache/play/, "
                f"coach: /en/events/{hunt.event_id}/cache/coach/)"
            )
