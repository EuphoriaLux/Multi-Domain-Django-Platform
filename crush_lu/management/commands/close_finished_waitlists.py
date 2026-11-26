"""Close waitlist spots (and open curated applications) on events that are over.

Usage:
    # Standard run (hourly, second pass of POST /api/admin/event-recaps/):
    # events that ended in the last 14 days
    python manage.py close_finished_waitlists

    # What it would change, per event, without writing anything
    python manage.py close_finished_waitlists --dry-run
    python manage.py close_finished_waitlists --all-past --dry-run

    # One-off backlog: every finished event, however old
    python manage.py close_finished_waitlists --all-past

The rules live in ``crush_lu/services/waitlist_closing.py``.
"""

from django.core.management.base import BaseCommand

from crush_lu.services.waitlist_closing import (
    close_finished_waitlists,
    finished_event_ids,
    still_waiting,
)


class Command(BaseCommand):
    help = (
        "Move waitlist registrations on finished events to 'waitlist_closed' "
        "and open curated applications to 'not_selected' (no emails)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would change, per event; write nothing",
        )
        parser.add_argument(
            "--all-past",
            action="store_true",
            help="Every finished event, not only those that ended in the last 14 days",
        )

    def handle(self, *args, **options):
        kwargs = {"lookback": None} if options["all_past"] else {}
        dry_run = options["dry_run"]

        if dry_run:
            self._report(finished_event_ids(**kwargs))

        counts = close_finished_waitlists(dry_run=dry_run, **kwargs)
        verb = "Would close" if dry_run else "Closed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb}: events={counts['events']}, "
                f"waitlist_closed={counts['waitlist_closed']}, "
                f"not_selected={counts['not_selected']}"
            )
        )

    def _report(self, event_ids):
        from collections import Counter

        rows = still_waiting(event_ids).select_related("event")
        per_event = Counter(
            (r.event.date_time.date(), r.event_id, r.event.title, r.status)
            for r in rows
        )
        for (day, event_id, title, status), n in sorted(per_event.items()):
            self.stdout.write(
                f"  event {event_id:>4}  {day}  {status:<8} {n:>3}  {title[:50]}"
            )
