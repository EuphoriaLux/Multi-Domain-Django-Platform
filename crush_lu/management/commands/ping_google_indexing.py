"""
Management command to publish Google Search Indexing API notifications for Crush.lu events.

Usage:
    python manage.py ping_google_indexing --all
    python manage.py ping_google_indexing --event 15
    python manage.py ping_google_indexing --url https://crush.lu/en/events/15/
    python manage.py ping_google_indexing --all --dry-run
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from crush_lu.models import MeetupEvent
from crush_lu.services.google_indexing import (
    build_event_indexing_urls,
    notify_event_indexing,
    notify_url_indexing,
    should_index_event,
)


class Command(BaseCommand):
    help = "Send instant Googlebot indexing notifications (URL_UPDATED or URL_DELETED) for events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--event",
            type=int,
            help="Single MeetupEvent ID to notify Googlebot about.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Notify Googlebot for all published, non-cancelled events.",
        )
        parser.add_argument(
            "--url",
            type=str,
            help="Explicit URL to notify Googlebot about.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Send URL_DELETED notification instead of URL_UPDATED.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print URLs that would be pinged without making actual API calls.",
        )

    def handle(self, *args, **options):
        action = "URL_DELETED" if options["delete"] else "URL_UPDATED"
        dry_run = options["dry_run"]
        event_id = options.get("event")
        all_events = options.get("all")
        explicit_url = options.get("url")

        if not any([event_id, all_events, explicit_url]):
            self.stdout.write(
                self.style.ERROR(
                    "Please specify an action: --event <id>, --all, or --url <url>"
                )
            )
            return

        if explicit_url:
            self.stdout.write(f"Target URL: {explicit_url} [{action}]")
            if dry_run:
                self.stdout.write(self.style.SUCCESS("Dry-run: would ping URL."))
                return

            res = notify_url_indexing(explicit_url, action=action)
            if res:
                self.stdout.write(
                    self.style.SUCCESS(f"✅ Notified Googlebot for {explicit_url}")
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"⚠️ Notification skipped or failed for {explicit_url}"
                    )
                )
            return

        if event_id:
            try:
                event = MeetupEvent.objects.get(pk=event_id)
            except MeetupEvent.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"Event with ID {event_id} not found.")
                )
                return

            urls = build_event_indexing_urls(event)
            self.stdout.write(f"Event: [{event.id}] {event.title} ({len(urls)} URLs)")
            for u in urls:
                self.stdout.write(f"  - {u}")

            if dry_run:
                self.stdout.write(self.style.SUCCESS("Dry-run completed."))
                return

            # Respect event eligibility (published, active, non-private) unless explicit --delete
            if not options["delete"]:
                action = "URL_UPDATED" if should_index_event(event) else "URL_DELETED"

            results = notify_event_indexing(event, action=action)
            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ Successfully sent {len(results)} notification(s) ({action}) to Googlebot."
                )
            )
            return

        if all_events:
            events = MeetupEvent.objects.filter(
                is_published=True,
                is_cancelled=False,
                is_private_invitation=False,
                date_time__gte=timezone.now(),
            ).order_by("date_time")

            count = events.count()
            self.stdout.write(f"Found {count} upcoming published event(s).")

            if dry_run:
                for ev in events:
                    self.stdout.write(f"\n[{ev.id}] {ev.title}")
                    for u in build_event_indexing_urls(ev):
                        self.stdout.write(f"  - {u}")
                self.stdout.write(
                    self.style.SUCCESS(f"\nDry-run completed for {count} event(s).")
                )
                return

            total_notified = 0
            for ev in events:
                self.stdout.write(f"Pinging event [{ev.id}] {ev.title}...")
                results = notify_event_indexing(ev, action=action)
                total_notified += len(results)

            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ Complete: Dispatched {total_notified} Google indexing notification(s)."
                )
            )
