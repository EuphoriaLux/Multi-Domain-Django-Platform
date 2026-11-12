"""
Remind recipients once about a Connect Week request that is about to expire.

Runs hourly as the second pass of the ``EventRecaps`` timer endpoint
(``crush_lu.api_admin_events.event_recaps_sweep``). The rules live in
``crush_lu.services.connect_cycle.send_due_request_reminders``.

Usage:
    python manage.py send_connect_request_reminders
    python manage.py send_connect_request_reminders --dry-run
"""

from django.core.management.base import BaseCommand

from crush_lu.services.connect_cycle import send_due_request_reminders


class Command(BaseCommand):
    help = "Send the one reminder for Connect Week requests close to expiry."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Count the requests that are due without sending. The inbox "
                "visibility check is skipped, so the count is an upper bound."
            ),
        )

    def handle(self, *args, **options):
        counts = send_due_request_reminders(dry_run=options["dry_run"])
        if counts["quiet_hours"]:
            self.stdout.write("Quiet hours: no reminders sent")
            return
        prefix = "[DRY RUN] " if options["dry_run"] else ""
        self.stdout.write(
            f"{prefix}due={counts['due']} sent={counts['sent']} "
            f"hidden={counts['hidden']} "
            f"already_claimed={counts['already_claimed']} "
            f"failed={counts['failed']}"
        )
