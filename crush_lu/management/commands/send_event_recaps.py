"""
Send 24h post-event recap emails to attendees.

Targets EventRegistrations with status='attended' where:
  - the event ended in the last `--lookback-hours` (default 36),
  - at least `--min-hours-after-end` have passed since end_time (default 24),
  - recap_sent_at is null.

Usage:
    # Standard daily run
    python manage.py send_event_recaps

    # Dry run
    python manage.py send_event_recaps --dry-run
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.email_helpers import send_event_recap


class Command(BaseCommand):
    help = 'Send post-event recap emails (24h after event end).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--lookback-hours',
            type=int,
            default=36,
            help='Only target events that ended within this many hours (default: 36)',
        )
        parser.add_argument(
            '--min-hours-after-end',
            type=int,
            default=24,
            help='Only send when at least this many hours have passed since end_time (default: 24)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without sending',
        )
        parser.add_argument(
            '--event-id',
            type=int,
            help='Restrict to a single event id',
        )

    def handle(self, *args, **options):
        lookback_hours = options['lookback_hours']
        min_hours = options['min_hours_after_end']
        dry_run = options['dry_run']
        event_id = options.get('event_id')
        verbosity = options['verbosity']

        now = timezone.now()
        send_floor = now - timedelta(hours=min_hours)
        lookback_floor = now - timedelta(hours=lookback_hours)

        events_qs = MeetupEvent.objects.filter(
            is_published=True,
            is_cancelled=False,
            date_time__gte=lookback_floor - timedelta(days=1),
            date_time__lt=send_floor,
        )
        if event_id:
            events_qs = events_qs.filter(id=event_id)

        candidate_events = [
            e for e in events_qs if lookback_floor <= e.end_time <= send_floor
        ]

        if not candidate_events:
            self.stdout.write(self.style.WARNING('No events in the recap window'))
            return

        if verbosity >= 1:
            self.stdout.write(
                f'Found {len(candidate_events)} event(s) eligible for recap'
            )

        registrations = (
            EventRegistration.objects.filter(
                event__in=candidate_events,
                status='attended',
                recap_sent_at__isnull=True,
            )
            .select_related('user', 'event')
        )

        sent = skipped = failed = 0

        for reg in registrations:
            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[DRY RUN] Would email {reg.user.email} for {reg.event.title}'
                    )
                )
                sent += 1
                continue

            try:
                count = send_event_recap(reg, request=None)
            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f'Error emailing {reg.user.email}: {e}')
                )
                continue

            reg.recap_sent_at = timezone.now()
            reg.save(update_fields=['recap_sent_at'])
            if count > 0:
                sent += 1
                if verbosity >= 2:
                    self.stdout.write(
                        self.style.SUCCESS(f'Sent recap to {reg.user.email}')
                    )
            else:
                skipped += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'[DRY RUN] Would send {sent} recaps')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Sent: {sent}, Skipped (unsubscribed): {skipped}, Failed: {failed}'
                )
            )
