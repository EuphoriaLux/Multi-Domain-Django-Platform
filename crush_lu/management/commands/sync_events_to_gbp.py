"""
Mirror Crush.lu events as event posts on the Google Business Profile listing.

Every upcoming, public event gets one English EVENT post (date, photo, BOOK
button to the event page with UTM tags). Edits update the post; a cancelled,
unpublished or invitation-only event has its post deleted. Finished events are
left alone: Google retires an event post after its end date. Idempotent — an
event whose payload is unchanged since Google last accepted it costs no API
call — so it runs hourly from the ``GbpSync`` Azure Function timer.

Usage:
    # Standard run (hourly timer)
    python manage.py sync_events_to_gbp

    # What would happen, without a token and without writing anything
    python manage.py sync_events_to_gbp --dry-run --show-payload

    # One event (the first live run should be exactly this)
    python manage.py sync_events_to_gbp --event-id 42

    # Take an event's post down while the event stays published
    python manage.py sync_events_to_gbp --event-id 42 --remove

Requires GOOGLE_BUSINESS_PROFILE_ENABLED=true, GOOGLE_BUSINESS_PROFILE_LOCATION
(set by production.py on the production slot only) and
GOOGLE_BUSINESS_PROFILE_TOKEN_JSON. Without them the command says why and
exits cleanly, so the timer can ship before the integration is switched on.

Exits non-zero when the sweep itself is broken (token refused, permission or
quota error) so the timer invocation shows as Failed. A failure that belongs
to one event is recorded on its sync row, named in a WARNING, and does not
stop the other events. With --event-id every failure is fatal.
"""

import json
import logging

from django.core.management.base import BaseCommand, CommandError

from crush_lu.models import MeetupEvent
from crush_lu.services import google_business_profile as gbp

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync Crush.lu events to Google Business Profile event posts."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--event-id", type=int)
        parser.add_argument(
            "--force",
            action="store_true",
            help="Resend even when the payload is unchanged",
        )
        parser.add_argument(
            "--remove",
            action="store_true",
            help="Delete the post of --event-id; the event stays published",
        )
        parser.add_argument(
            "--show-payload",
            action="store_true",
            help="Print the JSON that would be sent",
        )
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        event_id = options["event_id"]
        if options["remove"] and not event_id:
            raise CommandError("--remove needs --event-id")

        if not dry_run:
            reason = gbp.disabled_reason()
            if reason:
                self.stdout.write(f"GBP sync disabled: {reason}. Nothing sent.")
                return

        if event_id:
            events = list(MeetupEvent.objects.filter(pk=event_id))
            if not events:
                raise CommandError(f"No event with id {event_id}")
        else:
            events = list(gbp.events_needing_sync())
        if options["limit"]:
            events = events[: options["limit"]]

        session = None
        if not dry_run:
            try:
                session = gbp.get_session()
            except gbp.GbpError as exc:
                raise CommandError(f"GBP auth failed: {exc}") from exc

        counts = {}
        failures = []
        for event in events:
            label = f"[{event.pk}] {event.title}"
            if options["show_payload"] and gbp.should_post(event):
                self.stdout.write(
                    json.dumps(
                        gbp.build_post_payload(event), indent=2, ensure_ascii=False
                    )
                )
            try:
                if options["remove"]:
                    action = gbp.remove_event_post(
                        event, session=session, dry_run=dry_run
                    )
                else:
                    action = gbp.sync_event(
                        event, session=session, force=options["force"], dry_run=dry_run
                    )
            except gbp.GbpError as exc:
                if exc.fatal or event_id:
                    raise CommandError(f"{label}: {exc}") from exc
                failures.append(f"{label}: {exc}")
                self.stdout.write(f"  ✗ {label}: {exc}")
                continue
            counts[action] = counts.get(action, 0) + 1
            if action != "skipped" and action != "unchanged":
                prefix = "(dry run) would be " if dry_run else ""
                self.stdout.write(f"  ✓ {label}: {prefix}{action}")

        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
        self.stdout.write(
            f"GBP sync{' (dry run)' if dry_run else ''}: {len(events)} events, "
            f"{summary}, failed={len(failures)}"
        )
        if failures:
            logger.warning(
                "[GBP] %d event(s) failed to sync: %s",
                len(failures),
                "; ".join(failures),
            )
