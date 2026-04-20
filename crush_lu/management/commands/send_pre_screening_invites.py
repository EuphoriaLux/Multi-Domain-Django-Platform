"""Trigger pre-screening invite / reminder / push flows.

Intended to be called on a short cron (e.g. every 10 minutes) by an Azure
Function timer — the same pattern as the existing crush-contact-sync
function. The command is idempotent: per-submission cache keys dedupe so a
single submission will receive at most one invite, one reminder, and one push.

Windows applied to each submission:
- 1h since submit: invite email (no pre-screening yet)
- 4h since submit: user push notification
- 24h since submit: reminder email (one-shot, not periodic)

All three are skipped if the user already submitted the pre-screening.
"""
from django.core.management.base import BaseCommand
from django.conf import settings

from crush_lu.pre_screening_notifications import (
    candidates_for_invite,
    candidates_for_push,
    candidates_for_reminder,
    send_pre_screening_invite_email,
    send_pre_screening_user_push,
)


class Command(BaseCommand):
    help = "Send pre-screening invites, reminders, and push nudges for pending submissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be sent without sending.",
        )
        parser.add_argument(
            "--only",
            choices=["invite", "reminder", "push"],
            help="Run only one of the three flows.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Per-flow upper bound on submissions processed.",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "PRE_SCREENING_ENABLED", False):
            self.stdout.write(
                self.style.WARNING(
                    "PRE_SCREENING_ENABLED is off; nothing to send."
                )
            )
            return

        dry = options["dry_run"]
        only = options.get("only")
        limit = options["limit"]

        totals = {"invite": 0, "reminder": 0, "push": 0}
        skipped = {"invite": 0, "reminder": 0, "push": 0}

        if only in (None, "invite"):
            for sub in candidates_for_invite()[:limit]:
                if dry:
                    totals["invite"] += 1
                    continue
                if send_pre_screening_invite_email(sub, reminder=False):
                    totals["invite"] += 1
                else:
                    skipped["invite"] += 1

        if only in (None, "reminder"):
            for sub in candidates_for_reminder()[:limit]:
                if dry:
                    totals["reminder"] += 1
                    continue
                if send_pre_screening_invite_email(sub, reminder=True):
                    totals["reminder"] += 1
                else:
                    skipped["reminder"] += 1

        if only in (None, "push"):
            for sub in candidates_for_push()[:limit]:
                if dry:
                    totals["push"] += 1
                    continue
                if send_pre_screening_user_push(sub):
                    totals["push"] += 1
                else:
                    skipped["push"] += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"pre_screening flows {'(dry run) ' if dry else ''}"
                f"sent={totals} skipped={skipped}"
            )
        )
