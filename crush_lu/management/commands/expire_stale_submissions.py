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

    # Custom cutoff: a date (local midnight) or an ISO datetime
    python manage.py expire_stale_submissions --before 2026-07-01
    python manage.py expire_stale_submissions --before 2026-07-11T21:10:42+00:00

Safety: paused submissions (own reactivation story) and submissions holding a
future booked screening slot are always skipped. Approved / rejected /
revision submissions are never touched.
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from crush_lu.models import ProfileSubmission

# The production slot swap that shipped the verification pivot — the swap's
# "Succeeded" event in the Azure activity log. Submissions made earlier that
# day were still created under the old coach-review flow, so the cutoff is
# the swap moment itself, not the day's midnight.
PIVOT_SWAP_AT = "2026-07-11T21:10:42+00:00"


class Command(BaseCommand):
    help = (
        "Close stale pre-pivot coach-review submissions (pending/recontact_coach) "
        "so their users get the self-serve verification flow (LuxID or event)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--before",
            default=PIVOT_SWAP_AT,
            help=(
                "Only expire submissions submitted strictly before this cutoff: "
                "a YYYY-MM-DD date (local midnight) or an ISO datetime. "
                f"Default: {PIVOT_SWAP_AT}, the moment the pivot swap completed."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything",
        )

    def handle(self, *args, **options):
        cutoff = parse_datetime(options["before"])
        if cutoff is None:
            cutoff_date = parse_date(options["before"])
            if cutoff_date is None:
                raise CommandError(
                    "--before must be a YYYY-MM-DD date or an ISO datetime, "
                    f"got {options['before']!r}"
                )
            cutoff = datetime.datetime.combine(cutoff_date, datetime.time.min)
        if timezone.is_naive(cutoff):
            cutoff = timezone.make_aware(cutoff)
        now = timezone.now()
        dry_run = options["dry_run"]

        candidates = ProfileSubmission.objects.filter(
            status__in=("pending", "recontact_coach"),
            submitted_at__lt=cutoff,
        )
        # Paused submissions carry their own reactivation flow — leave them.
        skipped_paused = candidates.filter(is_paused=True).count()
        candidates = candidates.exclude(is_paused=True)
        # A completed screening call puts the row on the admin's "Ready to
        # Approve" path (bulk_approve_profiles only needs the call done) —
        # expiring it would throw away a call that already happened. Leave
        # those for an explicit approve/expire decision.
        skipped_call_done = candidates.filter(review_call_completed=True).count()
        candidates = candidates.exclude(review_call_completed=True)
        # An active or future booked screening call means a coach interaction
        # is genuinely scheduled (or happening right now — a slot stays
        # 'booked' until the coach completes it, so test end_at, not
        # start_at, or a mid-call submission would be expired). filter()
        # binds both kwargs to the same slot row, but exclude() would not
        # (it matches the conditions on possibly different slots), so
        # resolve the matching submissions via filter() and exclude by pk.
        active_booked_pks = candidates.filter(
            booked_slots__status="booked",
            booked_slots__end_at__gte=now,
        ).values("pk")
        skipped_booked = active_booked_pks.distinct().count()
        candidates = candidates.exclude(pk__in=active_booked_pks)

        by_status = {
            row["status"]: row["n"]
            for row in candidates.values("status").order_by().annotate(n=Count("id"))
        }
        total = sum(by_status.values())

        self.stdout.write(f"Cutoff: submitted before {cutoff.isoformat()}")
        for status in ("pending", "recontact_coach"):
            self.stdout.write(f"  {status}: {by_status.get(status, 0)}")
        self.stdout.write(f"  skipped (paused): {skipped_paused}")
        self.stdout.write(f"  skipped (completed screening call): {skipped_call_done}")
        self.stdout.write(f"  skipped (active/future booked slot): {skipped_booked}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry run — {total} submission(s) NOT modified.")
            )
            return

        expired = 0
        skipped_late = 0
        with transaction.atomic():
            for submission in candidates.select_for_update().iterator():
                # The guards above were evaluated before this row lock was
                # taken — re-check them under the lock so a pause, completed
                # call, booking, or status change committed in the meantime
                # still protects the row.
                if (
                    submission.status not in ("pending", "recontact_coach")
                    or submission.is_paused
                    or submission.review_call_completed
                    or submission.booked_slots.filter(
                        status="booked", end_at__gte=now
                    ).exists()
                ):
                    skipped_late += 1
                    continue
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

        if skipped_late:
            self.stdout.write(
                f"  skipped (state changed during run): {skipped_late}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {expired} submission(s) — their users now get the "
                "self-serve verification flow."
            )
        )
