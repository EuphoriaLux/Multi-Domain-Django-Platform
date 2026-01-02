"""
Send profile completion reminder emails to users with incomplete profiles.

This command identifies users who started but didn't finish their profile
and sends them reminder emails at configurable intervals (24h, 72h, 7 days).

Timing is configurable via settings.PROFILE_REMINDER_TIMING.

Usage:
    # Send all reminder types (24h, 72h, 7d)
    python manage.py send_profile_reminders

    # Send only 24h reminders
    python manage.py send_profile_reminders --type=24h

    # Dry run (show what would be sent without sending)
    python manage.py send_profile_reminders --dry-run

    # Limit number of emails per run
    python manage.py send_profile_reminders --limit=50

    # Verbose output
    python manage.py send_profile_reminders -v 2

Scheduling:
    Run this command daily (e.g., via Azure WebJobs or cron):
    0 9 * * * cd /home/site/wwwroot && python manage.py send_profile_reminders
"""
from django.core.management.base import BaseCommand
from django.conf import settings

from crush_lu.email_helpers import get_users_needing_reminder, send_profile_incomplete_reminder


class Command(BaseCommand):
    help = 'Send profile completion reminder emails to users with incomplete profiles'

    def add_arguments(self, parser):
        parser.add_argument(
            '--type',
            choices=['24h', '72h', '7d', 'all'],
            default='all',
            help='Type of reminder to send (default: all)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be sent without actually sending'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Maximum number of emails to send per run (default: 100)'
        )

    def handle(self, *args, **options):
        reminder_type = options['type']
        dry_run = options['dry_run']
        limit = options['limit']
        verbosity = options['verbosity']

        # Determine which reminder types to process
        if reminder_type == 'all':
            reminder_types = ['24h', '72h', '7d']
        else:
            reminder_types = [reminder_type]

        # Show timing configuration
        timing = getattr(settings, 'PROFILE_REMINDER_TIMING', {})
        if verbosity >= 2:
            self.stdout.write('Reminder timing configuration:')
            for rtype, config in timing.items():
                self.stdout.write(
                    f'  {rtype}: {config["min_hours"]}-{config["max_hours"]} hours after signup'
                )
            self.stdout.write('')

        total_sent = 0
        total_skipped = 0
        total_failed = 0
        emails_remaining = limit

        for rtype in reminder_types:
            if emails_remaining <= 0:
                self.stdout.write(
                    self.style.WARNING(f'Limit of {limit} emails reached, stopping')
                )
                break

            if verbosity >= 1:
                self.stdout.write(f'\nProcessing {rtype} reminders...')

            # Get eligible users
            users = get_users_needing_reminder(rtype)
            user_count = users.count()

            if user_count == 0:
                if verbosity >= 1:
                    self.stdout.write(
                        self.style.WARNING(f'  No users eligible for {rtype} reminder')
                    )
                continue

            if verbosity >= 1:
                self.stdout.write(f'  Found {user_count} users eligible for {rtype} reminder')

            # Process users (up to remaining limit)
            users_to_process = users[:emails_remaining]

            for user in users_to_process:
                # Get profile info for logging
                try:
                    profile = user.crushprofile
                    status = profile.completion_status
                except Exception:
                    status = 'unknown'

                if dry_run:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'  [DRY RUN] Would send {rtype} to: {user.email} (status: {status})'
                        )
                    )
                    total_sent += 1
                    emails_remaining -= 1
                    continue

                try:
                    success = send_profile_incomplete_reminder(
                        user=user,
                        reminder_type=rtype,
                        request=None  # No request context for management command
                    )

                    if success:
                        total_sent += 1
                        emails_remaining -= 1
                        if verbosity >= 2:
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'  Sent {rtype} to {user.email} (status: {status})'
                                )
                            )
                    else:
                        total_skipped += 1
                        if verbosity >= 2:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'  Skipped {user.email}: unsubscribed or ineligible'
                                )
                            )

                except Exception as e:
                    total_failed += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'  Error sending to {user.email}: {e}'
                        )
                    )

        # Summary
        self.stdout.write('')
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'[DRY RUN] Would send {total_sent} reminder(s)'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Summary: Sent: {total_sent}, Skipped: {total_skipped}, Failed: {total_failed}'
                )
            )

        # Store counts for testing (accessible as self.counts after call_command())
        self.counts = {
            'sent': total_sent,
            'skipped': total_skipped,
            'failed': total_failed,
        }
