"""
Prune expired Event Lobby audit rows (spec §13 retention).

Spec: docs/superpowers/specs/2026-07-17-crush-connect-event-lobby-design.md

> Expired one-sided signals, directional meeting confirmations, and lobby
> participation rows are no longer user-visible after recap close. Retain them
> for at most 30 days after recap close for abuse/support investigation, then
> hard-delete them. [...] a permanent encounter keeps only its own minimal
> provenance reference; it does not preserve or expose the expired anonymous
> interaction records.

So this deletes ``EventMeetSignal`` / ``EventMeetingConfirmation`` /
``EventLobbyParticipation`` rows once an event's recap has been closed for
longer than the retention window. ``ConfirmedEncounter`` rows are NEVER touched
— they are the permanent collection and keep only ``created_from_event`` as
provenance (the FK is ``SET_NULL``, so pruning the event later cannot cascade).

Idempotent by construction: it only ever deletes rows whose event is already
past the cutoff, so re-running finds nothing left to do. Safe to schedule.

Usage:
    python manage.py cleanup_event_lobby --dry-run
    python manage.py cleanup_event_lobby
    python manage.py cleanup_event_lobby --days 45
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crush_lu.services.event_lobby import RECAP_WINDOW_HOURS

# §13: "at most 30 days after recap close".
DEFAULT_RETENTION_DAYS = 30


class Command(BaseCommand):
    help = "Hard-delete Event Lobby signal/confirmation/participation rows past retention"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=(
                "Days to retain rows after recap close "
                f"(default {DEFAULT_RETENTION_DAYS}, the spec maximum)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without making changes.",
        )

    def handle(self, *args, **options):
        from crush_lu.models import (
            EventLobbyParticipation,
            EventMeetingConfirmation,
            EventMeetSignal,
            MeetupEvent,
        )

        days = options["days"]
        dry_run = options["dry_run"]
        now = timezone.now()

        # An event's recap closes at date_time + duration + 48h; rows expire
        # `days` later. duration_minutes varies per event and SQLite can't do
        # timedelta * F(), so filter generously in SQL then confirm in Python
        # (same idiom as context_processors / get_active_live_lobby).
        generous_cutoff = now - timedelta(
            days=days, hours=RECAP_WINDOW_HOURS
        )
        candidates = MeetupEvent.objects.filter(
            date_time__lt=generous_cutoff
        ).only("id", "date_time", "duration_minutes")

        expired_event_ids = [
            event.pk
            for event in candidates.iterator()
            if event.end_time
            + timedelta(hours=RECAP_WINDOW_HOURS)
            + timedelta(days=days)
            <= now
        ]

        if not expired_event_ids:
            self.stdout.write("No Event Lobby rows past the retention window.")
            return

        signals = EventMeetSignal.objects.filter(event_id__in=expired_event_ids)
        confirmations = EventMeetingConfirmation.objects.filter(
            event_id__in=expired_event_ids
        )
        participations = EventLobbyParticipation.objects.filter(
            event_id__in=expired_event_ids
        )
        counts = {
            "signals": signals.count(),
            "confirmations": confirmations.count(),
            "participations": participations.count(),
        }
        total = sum(counts.values())

        if total == 0:
            self.stdout.write("No Event Lobby rows past the retention window.")
            return

        summary = (
            f"{counts['signals']} signal(s), "
            f"{counts['confirmations']} confirmation(s), "
            f"{counts['participations']} participation(s) "
            f"across {len(expired_event_ids)} event(s)"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING(f"[dry-run] Would delete {summary}."))
            return

        with transaction.atomic():
            # Signals/confirmations first: participations are what froze recap
            # membership, so they are the last thing to go.
            signals.delete()
            confirmations.delete()
            participations.delete()

        self.stdout.write(self.style.SUCCESS(f"Deleted {summary}."))
