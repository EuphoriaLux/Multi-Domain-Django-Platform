"""
Management command to publish Google Search Indexing API notifications for Crush.lu events.

Usage:
    python manage.py ping_google_indexing --all
    python manage.py ping_google_indexing --event 15
    python manage.py ping_google_indexing --url https://crush.lu/en/events/15/
    python manage.py ping_google_indexing --all --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_lu.models import MeetupEvent
from crush_lu.services.google_indexing import (
    build_event_indexing_urls,
    notify_event_indexing,
    notify_url_indexing,
    should_index_event,
)

NOTHING_SENT_HINT = (
    "No Google indexing notification was delivered for {target}. "
    "Check GOOGLE_INDEXING_ENABLED, the service account credentials, and the "
    "log for the API response."
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
            if not res:
                raise CommandError(NOTHING_SENT_HINT.format(target=explicit_url))

            self.stdout.write(
                self.style.SUCCESS(f"✅ Notified Googlebot for {explicit_url}")
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
            sent = len(results)
            if urls and not sent:
                raise CommandError(NOTHING_SENT_HINT.format(target=f"event {event.id}"))

            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ Successfully sent {sent} notification(s) ({action}) to Googlebot."
                )
            )
            if sent < len(urls):
                self.stderr.write(
                    self.style.WARNING(
                        f"⚠️ {len(urls) - sent} of {len(urls)} URL(s) were not "
                        f"accepted for event {event.id}."
                    )
                )
            return

        if all_events:
            now = timezone.now()
            generous_cutoff = MeetupEvent.live_lookback_cutoff(now)
            candidate_events = MeetupEvent.objects.filter(
                is_published=True,
                is_cancelled=False,
                is_private_invitation=False,
                date_time__gte=generous_cutoff,
            ).order_by("date_time")
            events = [ev for ev in candidate_events if ev.end_time >= now]

            count = len(events)
            self.stdout.write(f"Found {count} upcoming/in-progress published event(s).")

            if dry_run:
                for ev in events:
                    self.stdout.write(f"\n[{ev.id}] {ev.title}")
                    for u in build_event_indexing_urls(ev):
                        self.stdout.write(f"  - {u}")
                self.stdout.write(
                    self.style.SUCCESS(f"\nDry-run completed for {count} event(s).")
                )
                return

            total_expected = 0
            total_notified = 0
            for ev in events:
                self.stdout.write(f"Pinging event [{ev.id}] {ev.title}...")
                total_expected += len(build_event_indexing_urls(ev))
                results = notify_event_indexing(ev, action=action)
                total_notified += len(results)

            # An empty match is a legitimate no-op; expected URLs that all failed
            # is not, and a scheduled run must not report that as healthy.
            if total_expected and not total_notified:
                raise CommandError(NOTHING_SENT_HINT.format(target=f"{count} event(s)"))

            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ Complete: Dispatched {total_notified} Google indexing notification(s)."
                )
            )
            if total_notified < total_expected:
                self.stderr.write(
                    self.style.WARNING(
                        f"⚠️ {total_expected - total_notified} of {total_expected} "
                        f"URL(s) were not accepted."
                    )
                )
