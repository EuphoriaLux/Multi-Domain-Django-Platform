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
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from crush_lu.models.phone_otp import PhoneOTP
from crush_lu.models.profiles import CallAttempt

DEFAULT_RETENTION = {
    "phone_otp_days": 30,
    "daily_activity_days": 90,
    "call_attempt_days": 365,
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

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        configured = getattr(settings, "GDPR_RETENTION", {}) or {}
        windows = {**DEFAULT_RETENTION, **configured}
        phone_days = options["phone_otp_days"] or windows["phone_otp_days"]
        activity_days = (
            options["daily_activity_days"] or windows["daily_activity_days"]
        )
        call_days = options["call_attempt_days"] or windows["call_attempt_days"]

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(f"GDPR retention sweep [{mode}]")

        now = timezone.now()
        total_deleted = 0

        # 1. PhoneOTP — phone number + code hash, worthless after expiry.
        otp_cutoff = now - timedelta(days=phone_days)
        otp_qs = PhoneOTP.objects.filter(created_at__lt=otp_cutoff)
        otp_count = otp_qs.count()
        self.stdout.write(
            f"  PhoneOTP older than {phone_days}d: {otp_count} row(s)"
        )
        if apply_changes and otp_count:
            deleted, _ = otp_qs.delete()
            total_deleted += deleted
            self.stdout.write(f"    deleted {deleted}")

        # 2. CallAttempt — screening-call audit trail with PII notes.
        call_cutoff = now - timedelta(days=call_days)
        call_qs = CallAttempt.objects.filter(attempt_date__lt=call_cutoff)
        call_count = call_qs.count()
        self.stdout.write(
            f"  CallAttempt older than {call_days}d: {call_count} row(s)"
        )
        if apply_changes and call_count:
            deleted, _ = call_qs.delete()
            total_deleted += deleted
            self.stdout.write(f"    deleted {deleted}")

        # 3. DailyUserActivity — delegate to the existing single-purpose
        # command so the window logic stays in one place.
        self.stdout.write(
            f"  DailyUserActivity older than {activity_days}d "
            f"(via cleanup_daily_activity):"
        )
        call_command(
            "cleanup_daily_activity",
            days=activity_days,
            dry_run=not apply_changes,
            stdout=self.stdout,
        )

        if apply_changes:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Retention sweep applied — {total_deleted} row(s) deleted "
                    "(plus DailyUserActivity above)."
                )
            )
        else:
            self.stdout.write(
                "Dry-run only — re-run with --apply to delete."
            )
