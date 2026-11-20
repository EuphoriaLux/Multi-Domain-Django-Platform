"""Send verification reminders to submitted ("pending") members.

    # Day 1 / 3 / 7 sequence (run daily, after send_profile_reminders)
    python manage.py send_verification_reminders

    # One-off catch-up for members who submitted before the reminders existed.
    # Never part of the daily run: it emails the whole legacy backlog.
    python manage.py send_verification_reminders --legacy --dry-run
    python manage.py send_verification_reminders --legacy --limit=50

See crush_lu/services/verification_nudges.py for who qualifies.
"""

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand

from crush_lu.services import verification_nudges as nudges

SWEEP_LOCK_KEY = "verification_reminders_sweep_lock"
SWEEP_LOCK_TTL = 900
# Leave room for one slow Graph send to finish inside the caller's budget
# (same reasoning as send_profile_reminders).
GRAPH_SEND_TIMEOUT_SECONDS = 30


class Command(BaseCommand):
    help = "Send verification reminder emails to submitted, not yet verified members"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--legacy",
            action="store_true",
            help="Send the one-off catch-up email to members who submitted "
            "before the reminders existed, instead of the daily sequence.",
        )
        parser.add_argument(
            "--time-budget",
            type=float,
            default=55.0,
            help="Wall-clock seconds this run may spend sending.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        remaining = options["limit"]
        deadline = time.monotonic() + options["time_budget"]

        lock_held = False
        if not dry_run:
            acquired = cache.add(SWEEP_LOCK_KEY, "1", SWEEP_LOCK_TTL)
            if acquired is False:
                self.stdout.write(
                    "Another verification-reminder run is in progress; skipping."
                )
                return
            lock_held = acquired is True

        if options["legacy"]:
            stages = [(nudges.LEGACY_TYPE, nudges.get_legacy_pending_users())]
        else:
            # Latest stage first, so this run's new row for an earlier stage
            # cannot make the same member eligible for the next one at once.
            stages = [
                (rtype, nudges.get_users_needing_verification_reminder(rtype))
                for rtype in reversed(nudges.SEQUENCE_TYPES)
            ]

        sent = skipped = 0
        try:
            for rtype, users in stages:
                if remaining <= 0:
                    break
                for user in users[:remaining]:
                    if (
                        not dry_run
                        and deadline - time.monotonic() < GRAPH_SEND_TIMEOUT_SECONDS
                    ):
                        self.stdout.write(
                            "Time budget reached; the rest goes out on the next run."
                        )
                        remaining = 0
                        break
                    if dry_run:
                        self.stdout.write(f"[DRY RUN] {rtype} -> user pk={user.pk}")
                        sent += 1
                        remaining -= 1
                        continue
                    if nudges.send_verification_reminder(user, rtype):
                        sent += 1
                        remaining -= 1
                    else:
                        skipped += 1
        finally:
            if lock_held:
                cache.delete(SWEEP_LOCK_KEY)

        label = "would send" if dry_run else "sent"
        self.stdout.write(f"Verification reminders: {label} {sent}, skipped {skipped}.")
