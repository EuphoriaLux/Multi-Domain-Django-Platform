"""
Send event reminders to users registered for upcoming events.

This command sends push notifications (with email fallback) to users
who have confirmed registrations for events happening either:
  - in the next N days (use --days, day-granularity), or
  - in the next N hours ± window (use --hours-before, hour-granularity, for same-day reminders).

Usage:
    # Send reminders for events happening tomorrow (24-hour notice)
    python manage.py send_event_reminders

    # Send reminders for events happening in 2 days
    python manage.py send_event_reminders --days=2

    # Send same-day "starts in 2h" reminders. Picks up events whose
    # start_time falls inside (now + 2h ± 30min) — schedule hourly.
    python manage.py send_event_reminders --hours-before=2

    # Override the +/- window for hour-mode (default 30 minutes)
    python manage.py send_event_reminders --hours-before=2 --window-minutes=15

    # Dry run (show what would be sent without sending)
    python manage.py send_event_reminders --dry-run

    # Verbose output
    python manage.py send_event_reminders -v 2
"""
from django.core.management.base import BaseCommand, CommandError
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
            '--hours-before',
            type=int,
            default=None,
            help=(
                'Same-day mode: send reminders for events starting in this many hours '
                '(within --window-minutes). When set, --days is ignored.'
            ),
        )
        parser.add_argument(
            '--window-minutes',
            type=int,
            default=30,
            help='Half-window (in minutes) for --hours-before mode (default: 30)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help=(
                'Re-send the day-before reminder even to registrations already '
                'stamped with reminder_sent_at (ops override; the scheduled '
                'timer never passes this)'
            ),
        )
        parser.add_argument(
            '--event-id',
            type=int,
            help='Send reminders only for a specific event ID'
        )

    def handle(self, *args, **options):
        days = options['days']
        hours_before = options.get('hours_before')
        window_minutes = options['window_minutes']
        dry_run = options['dry_run']
        event_id = options.get('event_id')
        verbosity = options['verbosity']

        if window_minutes <= 0:
            raise CommandError('--window-minutes must be positive')

        now = timezone.now()

        # Build query for upcoming events. Use the boolean fields, not a
        # non-existent `status` field.
        events_query = MeetupEvent.objects.filter(
            is_published=True,
            is_cancelled=False,
        )

        if hours_before is not None:
            # Hour-granularity window for same-day reminders
            target_time = now + timedelta(hours=hours_before)
            window_start = target_time - timedelta(minutes=window_minutes)
            window_end = target_time + timedelta(minutes=window_minutes)
            events_query = events_query.filter(
                date_time__gte=window_start,
                date_time__lt=window_end,
            )
            target_label = (
                f'~{hours_before}h from now '
                f'({window_start.isoformat()} -> {window_end.isoformat()})'
            )
            days_until_for_notification = 0  # same-day
        else:
            # Day-granularity window (legacy behavior)
            target_start = now + timedelta(days=days)
            target_end = target_start + timedelta(days=1)
            events_query = events_query.filter(
                date_time__date__gte=target_start.date(),
                date_time__date__lt=target_end.date(),
            )
            target_label = str(target_start.date())
            days_until_for_notification = days

        if event_id:
            events_query = events_query.filter(id=event_id)

        events = list(events_query.select_related())

        if not events:
            self.stdout.write(
                self.style.WARNING(f'No events found for {target_label}')
            )
            return

        if verbosity >= 1:
            self.stdout.write(f'Found {len(events)} event(s) for {target_label}')

        total_sent = 0
        total_failed = 0
        total_skipped = 0

        for event in events:
            if verbosity >= 1:
                self.stdout.write(f'\nProcessing: {event.title} @ {event.date_time.isoformat()}')

            registrations = EventRegistration.objects.filter(
                event=event,
                status='confirmed'
            ).select_related('user', 'user__crushprofile')

            # Idempotency for the unattended EventReminders timer: without this
            # a catch-up invocation or an operational retry re-notifies every
            # confirmed registration, and each pass creates another in-app row
            # and re-sends the push and the email.
            #
            # Scoped to the *scheduled* shape only: the default `--days 1`
            # day-before sweep. Every other mode is a different reminder for
            # the same event and must neither be suppressed by the day-before
            # stamp nor consume it:
            #   `--hours-before`  a same-day nudge, hours out
            #   `--days N` (N!=1) an early heads-up; stamping here would make
            #                     the following day's scheduled sweep skip the
            #                     registration and never send the real
            #                     day-before reminder
            stamps_reminder = (
                hours_before is None and days == 1 and not options['force']
            )
            if stamps_reminder:
                registrations = registrations.filter(reminder_sent_at__isnull=True)

            if not registrations.exists():
                if verbosity >= 1:
                    self.stdout.write(
                        self.style.WARNING('  No confirmed registrations')
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
                        days_until=days_until_for_notification,
                        request=None,  # No request context for management command
                    )

                    # Stamp only once the *email* is settled — delivered, or
                    # deliberately not going (unsubscribed / no address).
                    #
                    # `any_delivered` is the wrong test: it is true whenever the
                    # in-app bell row was written, and that row is written
                    # unconditionally. NotificationService also swallows a
                    # Graph failure rather than raising, returning
                    # email_sent=False with no skip reason. Stamping on either
                    # of those would mean one provider outage during the daily
                    # sweep permanently suppresses the reminder for everyone it
                    # touched, with no retry able to see it.
                    email_settled = (
                        result.email_sent or result.email_skipped_reason is not None
                    )
                    if stamps_reminder and email_settled:
                        # queryset .update() rather than .save(): a save fires
                        # trigger_wallet_pass_update_on_registration_change,
                        # which performs a *synchronous* Apple/Google Wallet
                        # refresh — one per reminder would put minutes of
                        # network I/O inside the sweep.
                        EventRegistration.objects.filter(pk=registration.pk).update(
                            reminder_sent_at=timezone.now()
                        )
                    elif stamps_reminder and result.errors:
                        self.stdout.write(
                            self.style.WARNING(
                                f'  Not stamping {user.email} — email unsettled: '
                                f'{result.errors}'
                            )
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
