"""
Send the due messages of the running past-event swipe campaign.

In production the hourly SwipeCampaignMessages Azure Function timer POSTs to
/api/admin/swipe-campaign/dispatch/ instead; this command is the manual path.

Usage:
    python manage.py send_swipe_campaign_messages --dry-run
    python manage.py send_swipe_campaign_messages --limit 200
"""

from django.core.management.base import BaseCommand

from crush_lu.services.swipe_campaign_messages import DEFAULT_LIMIT, dispatch


class Command(BaseCommand):
    help = "Send the due messages of the running past-event swipe campaign"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)

    def handle(self, *args, **options):
        result = dispatch(limit=options["limit"], dry_run=options["dry_run"])
        if result["campaign"] is None:
            self.stdout.write("No swipe campaign is running.")
            return
        if options["dry_run"]:
            for key, count in result["would_send"].items():
                self.stdout.write(f"{key}: {count} member(s) still to message")
            return
        self.stdout.write(
            "sent={sent} skipped={skipped} failed={failed} remaining={remaining}".format(
                **result
            )
        )
