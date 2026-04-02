"""
Send event reminders to users registered for upcoming events.

This command sends push notifications (with email fallback) to users
who have confirmed registrations for events happening in the next N days.

Usage:
    # Send reminders for events happening tomorrow
    python manage.py send_event_reminders

    # Send reminders for events happening in 2 days
    python manage.py send_event_reminders --days=2

    # Dry run (show what would be sent without sending)
    python manage.py send_event_reminders --dry-run

    # Verbose output
    python manage.py send_event_reminders -v 2
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.notification_service import notify_event_reminder


class Command(BaseCommand):
    help = 'Send event reminders to users with confirmed event registrations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=1,
            help='Send reminders for events happening in this many days (default: 1)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending'
        )
        parser.add_argument(
            '--event-id',
            type=int,
            help='Send reminders only for a specific event ID'
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        event_id = options.get('event_id')
        verbosity = options['verbosity']

        # Calculate target date range
        now = timezone.now()
        target_start = now + timedelta(days=days)
        target_end = target_start + timedelta(days=1)

        # Build query for upcoming events
        events_query = MeetupEvent.objects.filter(
            event_date__gte=target_start.date(),
            event_date__lt=target_end.date(),
            status='published',
        )

        if event_id:
            events_query = events_query.filter(id=event_id)

        events = events_query.select_related()

        if not events.exists():
            self.stdout.write(
                self.style.WARNING(f'No events found for {target_start.date()}')
            )
            return

        if verbosity >= 1:
            self.stdout.write(
                f'Found {events.count()} event(s) on {target_start.date()}'
            )

        total_sent = 0
        total_failed = 0
        total_skipped = 0

        for event in events:
            if verbosity >= 1:
                self.stdout.write(f'\nProcessing: {event.title}')

            # Get confirmed registrations
            registrations = EventRegistration.objects.filter(
                event=event,
                status='confirmed'
            ).select_related('user', 'user__crushprofile')

            if not registrations.exists():
                if verbosity >= 1:
                    self.stdout.write(
                        self.style.WARNING(f'  No confirmed registrations')
                    )
                continue

            if verbosity >= 1:
                self.stdout.write(f'  {registrations.count()} confirmed registrations')

            for registration in registrations:
                user = registration.user

                if dry_run:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'  [DRY RUN] Would notify: {user.email}'
                        )
                    )
                    total_sent += 1
                    continue

                try:
                    result = notify_event_reminder(
                        user=user,
                        registration=registration,
                        event=event,
                        days_until=days,
                        request=None  # No request context for management command
                    )

                    if result.any_delivered:
                        total_sent += 1
                        if verbosity >= 2:
                            channel = 'push' if result.push_success else 'email'
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'  Sent to {user.email} via {channel}'
                                )
                            )
                    else:
                        total_skipped += 1
                        if verbosity >= 2:
                            reason = result.email_skipped_reason or 'unknown'
                            self.stdout.write(
                                self.style.WARNING(
                                    f'  Skipped {user.email}: {reason}'
                                )
                            )

                except Exception as e:
                    total_failed += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'  Error notifying {user.email}: {e}'
                        )
                    )

        # Summary
        self.stdout.write('')
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'[DRY RUN] Would send {total_sent} reminders'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Sent: {total_sent}, Skipped: {total_skipped}, Failed: {total_failed}'
                )
            )
