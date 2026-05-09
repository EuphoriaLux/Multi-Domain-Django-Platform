"""
Send post-event feedback survey requests to attendees of recently-ended events.

Targets EventRegistrations with status='attended' where:
  - the event has ended (event end_time is in the past),
  - the event ended within the last `--lookback-hours` (default 48),
  - feedback_request_sent_at is null (idempotent).

Usage:
    # Standard daily run — events that ended in the last 48h
    python manage.py send_event_feedback_requests

    # Different lookback window
    python manage.py send_event_feedback_requests --lookback-hours=72

    # Dry run
    python manage.py send_event_feedback_requests --dry-run
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.email_helpers import send_event_feedback_request


class Command(BaseCommand):
    help = 'Send post-event feedback survey emails to attendees of recently-ended events.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--lookback-hours',
            type=int,
            default=48,
            help='Only target events that ended within this many hours (default: 48)',
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
        dry_run = options['dry_run']
        event_id = options.get('event_id')
        verbosity = options['verbosity']

        now = timezone.now()
        lookback_cutoff = now - timedelta(hours=lookback_hours)

        # Pre-filter events that *might* have ended in window. We must compute
        # end_time in Python because it's a property (date_time + duration).
        events_qs = MeetupEvent.objects.filter(
            is_published=True,
            is_cancelled=False,
            date_time__gte=lookback_cutoff - timedelta(days=1),
            date_time__lt=now,
        )
        if event_id:
            events_qs = events_qs.filter(id=event_id)

        candidate_events = [
            e for e in events_qs if lookback_cutoff <= e.end_time <= now
        ]

        if not candidate_events:
            self.stdout.write(
                self.style.WARNING(
                    f'No events ended in the last {lookback_hours}h'
                )
            )
            return

        if verbosity >= 1:
            self.stdout.write(
                f'Found {len(candidate_events)} event(s) ended within {lookback_hours}h'
            )

        registrations = (
            EventRegistration.objects.filter(
                event__in=candidate_events,
                status='attended',
                feedback_request_sent_at__isnull=True,
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
                count = send_event_feedback_request(reg, request=None)
            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f'Error emailing {reg.user.email}: {e}')
                )
                continue

            if count > 0:
                reg.feedback_request_sent_at = timezone.now()
                reg.save(update_fields=['feedback_request_sent_at'])
                sent += 1
                if verbosity >= 2:
                    self.stdout.write(
                        self.style.SUCCESS(f'Sent feedback request to {reg.user.email}')
                    )
            else:
                # Mark even when skipped so we don't retry indefinitely on a
                # user who has unsubscribed.
                reg.feedback_request_sent_at = timezone.now()
                reg.save(update_fields=['feedback_request_sent_at'])
                skipped += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'[DRY RUN] Would send {sent} feedback requests')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Sent: {sent}, Skipped (unsubscribed): {skipped}, Failed: {failed}'
                )
            )
