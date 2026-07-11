"""
Expire stale pre-pivot coach-review submissions.

The verification pivot (shipped to production 2026-07-11) replaced the
coach-review queue with self-serve verification: LuxID (instant) or in-person
at an event. Submissions left 'pending' or 'recontact_coach' from the old flow
keep their users trapped in dead-end coach messaging on /profile-submitted/
("contact your coach for your screening call") with no LuxID/event option.

This command closes those submissions (status -> 'expired'). The user's
CrushProfile stays 'pending', so on their next visit they get the standard
"Verify your identity" hero — the same experience as a fresh post-pivot
signup, with their profile data intact. No email is sent; the re-engagement
campaign notifies this cohort separately.

Usage:
    # Preview what would change (recommended first)
    python manage.py expire_stale_submissions --dry-run

    # Expire everything submitted before the pivot swap (default cutoff)
    python manage.py expire_stale_submissions

    # Custom cutoff date (submissions strictly before this date, local midnight)
    python manage.py expire_stale_submissions --before 2026-07-01

Safety: paused submissions (own reactivation story) and submissions holding a
future booked screening slot are always skipped. Approved / rejected /
revision submissions are never touched.
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_date

from crush_lu.models import ProfileSubmission

# The production slot swap that shipped the verification pivot.
PIVOT_SWAP_DATE = "2026-07-11"


class Command(BaseCommand):
    help = (
        "Close stale pre-pivot coach-review submissions (pending/recontact_coach) "
        "so their users get the self-serve verification flow (LuxID or event)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--before",
            default=PIVOT_SWAP_DATE,
            help=(
                "Only expire submissions submitted strictly before this date "
                f"(YYYY-MM-DD, local midnight). Default: {PIVOT_SWAP_DATE}, "
                "the pivot swap date."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything",
        )

    def handle(self, *args, **options):
        cutoff_date = parse_date(options["before"])
        if cutoff_date is None:
            raise CommandError(
                f"--before must be a YYYY-MM-DD date, got {options['before']!r}"
            )
        cutoff = timezone.make_aware(
            datetime.datetime.combine(cutoff_date, datetime.time.min)
        )
        now = timezone.now()
        dry_run = options["dry_run"]

        candidates = ProfileSubmission.objects.filter(
            status__in=("pending", "recontact_coach"),
            submitted_at__lt=cutoff,
        )
        # Paused submissions carry their own reactivation flow — leave them.
        skipped_paused = candidates.filter(is_paused=True).count()
        candidates = candidates.exclude(is_paused=True)
        # A future booked screening call means a coach interaction is genuinely
        # scheduled — both kwargs apply to the same slot row, so this only
        # skips submissions with an upcoming *booked* slot.
        future_booked = {
            "booked_slots__status": "booked",
            "booked_slots__start_at__gte": now,
        }
        skipped_booked = candidates.filter(**future_booked).count()
        candidates = candidates.exclude(**future_booked)

        by_status = {
            row["status"]: row["n"]
            for row in candidates.values("status").order_by().annotate(n=Count("id"))
        }
        total = sum(by_status.values())

        self.stdout.write(f"Cutoff: submitted before {cutoff.isoformat()}")
        for status in ("pending", "recontact_coach"):
            self.stdout.write(f"  {status}: {by_status.get(status, 0)}")
        self.stdout.write(f"  skipped (paused): {skipped_paused}")
        self.stdout.write(f"  skipped (future booked slot): {skipped_booked}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry run — {total} submission(s) NOT modified.")
            )
            return

        expired = 0
        with transaction.atomic():
            for submission in candidates.select_for_update().iterator():
                previous = submission.status
                submission.status = "expired"
                submission.log_system_action(
                    "expired_to_self_serve",
                    previous_status=previous,
                    reason="post-pivot cleanup",
                    cutoff=cutoff.isoformat(),
                )
                submission.save(update_fields=["status", "system_actions"])
                expired += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {expired} submission(s) — their users now get the "
                "self-serve verification flow."
            )
        )
