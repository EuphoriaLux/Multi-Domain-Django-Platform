"""
Management command to send push notifications to Google Wallet pass holders.

Google Wallet shows notifications when you PATCH a pass object with new messages.
This command sends a message to all (or filtered) pass holders.

Usage:
    python manage.py send_wallet_notification --header "..." --body "..."
    python manage.py send_wallet_notification --header "..." --body "..." --tier gold
    python manage.py send_wallet_notification --header "..." --body "..." --dry-run
"""

from django.core.management.base import BaseCommand
from django.db.models import Q


class Command(BaseCommand):
    help = "Send a notification to Google Wallet pass holders"

    def add_arguments(self, parser):
        parser.add_argument(
            "--header",
            required=True,
            help="Notification title (e.g. 'New Event This Weekend!')",
        )
        parser.add_argument(
            "--body",
            required=True,
            help="Notification body text",
        )
        parser.add_argument(
            "--tier",
            choices=["basic", "bronze", "silver", "gold"],
            default=None,
            help="Only send to users with this membership tier",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many passes would be notified without actually sending",
        )
        parser.add_argument(
            "--no-confirm",
            action="store_true",
            help="Skip confirmation prompt",
        )

    def handle(self, *args, **options):
        from crush_lu.models import CrushProfile

        header = options["header"]
        body = options["body"]
        tier_filter = options["tier"]
        dry_run = options["dry_run"]
        no_confirm = options["no_confirm"]

        # Count target passes
        profiles = CrushProfile.objects.exclude(
            Q(google_wallet_object_id__isnull=True) | Q(google_wallet_object_id="")
        )
        if tier_filter:
            profiles = profiles.filter(membership_tier=tier_filter)

        count = profiles.count()

        if count == 0:
            self.stdout.write(self.style.WARNING("No Google Wallet pass holders found."))
            return

        # Show summary
        self.stdout.write("")
        self.stdout.write(self.style.HTTP_INFO("=== Google Wallet Notification ==="))
        self.stdout.write(f"  Header: {header}")
        self.stdout.write(f"  Body:   {body}")
        if tier_filter:
            self.stdout.write(f"  Tier:   {tier_filter}")
        self.stdout.write(f"  Target: {count} pass holder(s)")
        self.stdout.write("")

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"[DRY RUN] Would send to {count} pass holder(s). No notifications sent."))
            return

        # Confirm before sending
        if not no_confirm:
            confirm = input(f"Send notification to {count} pass holder(s)? [y/N] ")
            if confirm.lower() != "y":
                self.stdout.write(self.style.WARNING("Cancelled."))
                return

        # Send notifications
        from crush_lu.wallet.google_api import send_wallet_notification

        self.stdout.write(f"Sending notifications to {count} pass holder(s)...")
        results = send_wallet_notification(header, body, tier_filter=tier_filter)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"  Sent:    {results['sent']}"))
        if results["failed"]:
            self.stdout.write(self.style.ERROR(f"  Failed:  {results['failed']}"))
        if results["skipped"]:
            self.stdout.write(self.style.WARNING(f"  Skipped: {results['skipped']}"))
        self.stdout.write("")
