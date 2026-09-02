"""GDPR data-minimization retention sweep.

Prunes personal-data tables whose rows have no operational value past a
bounded retention window, per the crush.lu GDPR retention rule (launch
checklist item 4). Runs dry-run by default; the scheduled caller
(``GdprRetention`` Azure Function -> ``POST /api/admin/gdpr-retention/``)
passes ``--apply``.

Categories and default windows (override via ``settings.GDPR_RETENTION``)::

    {
        "phone_otp_days": 30,        # PhoneOTP: phone number + code hash
        "daily_activity_days": 90,   # DailyUserActivity WAU rows
        "call_attempt_days": 365,    # CallAttempt screening-call audit trail
        "event_preference_days": 30, # preferences + named group schedule
    }

``DailyUserActivity`` pruning delegates to the existing
``cleanup_daily_activity`` command so the window logic lives in one place.

Usage:
    # Preview what would be deleted (safe default)
    python manage.py gdpr_retention_cleanup

    # Actually delete
    python manage.py gdpr_retention_cleanup --apply

    # One-off window overrides
    python manage.py gdpr_retention_cleanup --phone-otp-days 14 --apply
"""

from datetime import timedelta

from django.conf import settings
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_lu.models.events import CuratedEventGroup, EventRegistrationPreference
from crush_lu.models.phone_otp import PhoneOTP
from crush_lu.models.profiles import CallAttempt, DailyUserActivity

# The GdprRetention endpoint runs this synchronously under _call_admin_endpoint's
# 60s HTTP timeout. A single bulk DELETE of a large first-run backlog could
# exceed it (and gunicorn's 120s cap), leaving the weekly job perpetually
# failed. Delete in chunks under a wall-clock budget instead; the sweep is
# idempotent (re-queries expired rows), so the next weekly run resumes.
RETENTION_TIME_BUDGET_SECONDS = 45
RETENTION_CHUNK_SIZE = 2000

DEFAULT_RETENTION = {
    "phone_otp_days": 30,
    "daily_activity_days": 90,
    "call_attempt_days": 365,
    # Speed-dating preference rows (gender preference is Art. 9-adjacent):
    # organiser-only input for composing the group, worthless once the event
    # is a month behind us. Window measured from the event's start time.
    "event_preference_days": 30,
}


class Command(BaseCommand):
    help = (
        "Prune personal-data tables past their GDPR retention windows "
        "(dry-run unless --apply is passed)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete rows. Default is a dry-run report.",
        )
        parser.add_argument("--phone-otp-days", type=int, default=None)
        parser.add_argument("--daily-activity-days", type=int, default=None)
        parser.add_argument("--call-attempt-days", type=int, default=None)
        parser.add_argument("--event-preference-days", type=int, default=None)

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        configured = getattr(settings, "GDPR_RETENTION", {}) or {}
        windows = {**DEFAULT_RETENTION, **configured}

        # `opt if opt is not None else window` — NOT `opt or window`: 0 is a
        # valid boundary for a destructive purge ("delete everything older
        # than now"), and an `or` chain would silently fall back to the
        # default window, leaving up to 30/90/365 days of data behind.
        def _window(cli_value, key):
            return cli_value if cli_value is not None else windows[key]

        phone_days = _window(options["phone_otp_days"], "phone_otp_days")
        activity_days = _window(options["daily_activity_days"], "daily_activity_days")
        call_days = _window(options["call_attempt_days"], "call_attempt_days")
        pref_days = _window(options["event_preference_days"], "event_preference_days")

        # A negative window (CLI or GDPR_RETENTION) produces a cutoff in the
        # future, so `created_at < cutoff` would match — and delete — every
        # row in the category. Reject it rather than purge everything. For
        # event preferences a negative window is doubly wrong: the cutoff is
        # measured from the event's start, so it would delete preferences for
        # events that have not happened yet.
        for label, value in (
            ("phone_otp_days", phone_days),
            ("daily_activity_days", activity_days),
            ("call_attempt_days", call_days),
            ("event_preference_days", pref_days),
        ):
            if value < 0:
                raise CommandError(
                    f"Retention window {label!r} must be >= 0 (got {value}). "
                    "A negative window makes a future cutoff that would delete "
                    "every row in the category."
                )

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(f"GDPR retention sweep [{mode}]")

        now = timezone.now()
        deadline = time.monotonic() + RETENTION_TIME_BUDGET_SECONDS
        total_deleted = 0
        budget_hit = False

        # 1. PhoneOTP — phone number + code hash, worthless after expiry.
        otp_qs = PhoneOTP.objects.filter(
            created_at__lt=now - timedelta(days=phone_days)
        )
        if apply_changes:
            deleted, budget_hit = self._delete_in_chunks(
                otp_qs, deadline, order_by="created_at"
            )
            total_deleted += deleted
            self.stdout.write(f"  PhoneOTP older than {phone_days}d: deleted {deleted}")
        else:
            self.stdout.write(
                f"  PhoneOTP older than {phone_days}d: {otp_qs.count()} row(s)"
            )

        # 2. CallAttempt — screening-call audit trail with PII notes.
        if not budget_hit:
            call_qs = CallAttempt.objects.filter(
                attempt_date__lt=now - timedelta(days=call_days)
            )
            if apply_changes:
                deleted, budget_hit = self._delete_in_chunks(
                    call_qs, deadline, order_by="attempt_date"
                )
                total_deleted += deleted
                self.stdout.write(
                    f"  CallAttempt older than {call_days}d: deleted {deleted}"
                )
            else:
                self.stdout.write(
                    f"  CallAttempt older than {call_days}d: "
                    f"{call_qs.count()} row(s)"
                )

        # 3. DailyUserActivity — WAU snapshot rows. Prune through the same
        # bounded loop (mirrors cleanup_daily_activity's date cutoff) so a large
        # activity backlog can't blow the budget after the earlier categories.
        if not budget_hit:
            activity_cutoff = timezone.localdate() - timedelta(days=activity_days)
            activity_qs = DailyUserActivity.objects.filter(
                activity_date__lt=activity_cutoff
            )
            if apply_changes:
                deleted, budget_hit = self._delete_in_chunks(
                    activity_qs, deadline, order_by="activity_date"
                )
                total_deleted += deleted
                self.stdout.write(
                    f"  DailyUserActivity older than {activity_days}d: "
                    f"deleted {deleted}"
                )
            else:
                self.stdout.write(
                    f"  DailyUserActivity older than {activity_days}d: "
                    f"{activity_qs.count()} row(s)"
                )

        # 4. Curated group lineage — named rosters and exact pairings are
        # derived dating/preference information. The aggregate fairness proof
        # and digest have served their support window once the event is a month
        # behind us, so prune the group root and its cascaded member/schedule
        # rows before pruning the raw preference snapshots below. Event and
        # registration history remain intact.
        if not budget_hit:
            group_qs = CuratedEventGroup.objects.filter(
                event__date_time__lt=now - timedelta(days=pref_days)
            )
            if apply_changes:
                deleted, budget_hit = self._delete_in_chunks(
                    group_qs, deadline, order_by="event__date_time"
                )
                total_deleted += deleted
                self.stdout.write(
                    "  Curated group roster/schedule data for events older than "
                    f"{pref_days}d: deleted {deleted} row(s), including cascades"
                )
            else:
                self.stdout.write(
                    "  Curated groups for events older than "
                    f"{pref_days}d: {group_qs.count()} group(s)"
                )

        # 5. EventRegistrationPreference — speed-dating preference snapshots
        # (age range / languages / preferred genders). The registration row
        # itself is untouched; only the preference side row is pruned, once
        # the event started more than the window ago.
        if not budget_hit:
            pref_qs = EventRegistrationPreference.objects.filter(
                registration__event__date_time__lt=now - timedelta(days=pref_days)
            )
            if apply_changes:
                # Ordered by the event date, not created_at: expiry is keyed
                # off the event, and a preference submitted months before a
                # recent event is younger data than a last-minute registration
                # for a much older one. Ordering by row age would delete the
                # former first and leave the oldest special-category data
                # behind when the budget truncates the sweep.
                deleted, budget_hit = self._delete_in_chunks(
                    pref_qs, deadline, order_by="registration__event__date_time"
                )
                total_deleted += deleted
                self.stdout.write(
                    f"  EventRegistrationPreference for events older than "
                    f"{pref_days}d: deleted {deleted}"
                )
            else:
                self.stdout.write(
                    f"  EventRegistrationPreference for events older than "
                    f"{pref_days}d: {pref_qs.count()} row(s)"
                )

        if apply_changes and budget_hit:
            self.stdout.write(
                self.style.WARNING(
                    f"Time budget reached — {total_deleted} row(s) deleted so far; "
                    "remaining expired rows will be pruned on the next sweep."
                )
            )
        elif apply_changes:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Retention sweep applied — {total_deleted} row(s) deleted."
                )
            )
        else:
            self.stdout.write("Dry-run only — re-run with --apply to delete.")

    def _delete_in_chunks(
        self, queryset, deadline, order_by, chunk_size=RETENTION_CHUNK_SIZE
    ):
        """Delete a filtered queryset in bounded batches, oldest first, stopping
        at the wall-clock deadline. Returns (deleted_count, budget_exhausted).

        Ordering by the age field (asc) with a pk tiebreaker guarantees the
        oldest expired PII is deleted first, so a budget-truncated run can never
        indefinitely leave the oldest rows behind while newer rows keep ageing
        in. Each batch re-queries the (shrinking) filtered set, so the deletes
        stay small and the loop makes progress; the next scheduled run prunes
        whatever is left.
        """
        model = queryset.model
        ordered = queryset.order_by(order_by, "pk")
        total = 0
        while True:
            pks = list(ordered.values_list("pk", flat=True)[:chunk_size])
            if not pks:
                return total, False
            deleted, _ = model.objects.filter(pk__in=pks).delete()
            total += deleted
            if time.monotonic() >= deadline:
                return total, True
