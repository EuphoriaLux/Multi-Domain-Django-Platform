"""
Send one "your Crush Credit expires soon" email per member, N days out.

Finds every ACTIVE, still-spendable CrushCredit whose ``expires_at`` falls
inside the next ``settings.CRUSH_CREDIT_EXPIRY_REMINDER_DAYS`` days (default
14 — a judgment call, not a published Terms figure; confirm/adjust with
Tom), that has never been reminded before, groups them by member, and sends
ONE email per member listing every one of their soon-to-expire credits —
not one email per credit, so a member holding two credits expiring the same
week is not emailed twice.

A credit is reminded at MOST ONCE, ever: ``CrushCreditExpiryReminder`` is a
``OneToOne`` on ``CrushCredit``, created only after the email actually sends
(see ``email_helpers.send_crush_credit_expiry_reminder``), so a crashed run
leaves no false "sent" record and the credit is simply retried next time.
Running this command twice in a row sends nothing the second time.

Usage:
    # Preview who would be emailed, and with what
    python manage.py send_crush_credit_expiry_reminders --dry-run

    # Send
    python manage.py send_crush_credit_expiry_reminders

    # Cap how many members are emailed in one run (default 100)
    python manage.py send_crush_credit_expiry_reminders --limit=50

Scheduling: NOT wired to an Azure Function timer as of this command's
introduction — see the PR description. Run it by hand until a timer is
added.
"""

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from crush_lu.email_helpers import send_crush_credit_expiry_reminder
from crush_lu.models.credits import CrushCredit

# Cross-process lock so an overlapping manual run and a future timer can't
# both select and email the same member before either records the
# CrushCreditExpiryReminder row. Mirrors send_profile_reminders' SWEEP_LOCK_KEY.
SWEEP_LOCK_KEY = "crush_credit_expiry_reminder_sweep_lock"
SWEEP_LOCK_TTL = 900  # seconds


class Command(BaseCommand):
    help = (
        "Send a one-time reminder email per member for Crush Credit expiring "
        "within CRUSH_CREDIT_EXPIRY_REMINDER_DAYS days."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be sent without actually sending",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Maximum number of members to email per run (default: 100)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        lock_acquired = False
        if not dry_run:
            acquired = cache.add(SWEEP_LOCK_KEY, "1", SWEEP_LOCK_TTL)
            if acquired is False:
                self.stdout.write(
                    self.style.WARNING(
                        "Another Crush Credit reminder sweep is in progress; "
                        "skipping this run."
                    )
                )
                return
            # None -> cache unavailable (Redis down with IGNORE_EXCEPTIONS).
            # Fail open rather than silently no-op every run until it recovers.
            if acquired is None:
                self.stdout.write(
                    self.style.WARNING(
                        "Reminder sweep lock cache is unavailable; proceeding "
                        "without cross-process locking."
                    )
                )
            lock_acquired = acquired is True

        try:
            self._run(dry_run=dry_run, limit=limit)
        finally:
            if lock_acquired:
                cache.delete(SWEEP_LOCK_KEY)

    def _run(self, *, dry_run, limit):
        now = timezone.now()
        window_days = getattr(settings, "CRUSH_CREDIT_EXPIRY_REMINDER_DAYS", 14)
        window_end = now + timezone.timedelta(days=window_days)

        due_credits = (
            CrushCredit.objects.filter(
                status=CrushCredit.Status.ACTIVE,
                expires_at__gt=now,
                expires_at__lte=window_end,
                # Never remind a member we must not contact (ban / deletion
                # path) — mirrors get_users_needing_reminder's exclusions.
                user__is_active=True,
                expiry_reminder__isnull=True,
            )
            .select_related("user")
            .order_by("expires_at", "id")
        )

        by_user = {}
        order = []
        for credit in due_credits:
            # Skip a credit that has already been fully spent down (status
            # would normally already be CONSUMED, but remaining_cents is the
            # authoritative check — nothing to remind about on a zero balance).
            if credit.remaining_cents <= 0:
                continue
            if credit.user_id not in by_user:
                order.append(credit.user_id)
                by_user[credit.user_id] = {"user": credit.user, "credits": []}
            by_user[credit.user_id]["credits"].append(credit)

        self.stdout.write(
            f"{len(order)} member(s) with Crush Credit expiring within "
            f"{window_days} day(s) (window ends {window_end.isoformat()})"
        )

        if dry_run:
            for user_id in order:
                entry = by_user[user_id]
                total = sum(c.remaining_cents for c in entry["credits"])
                self.stdout.write(
                    f"  [DRY RUN] Would remind {entry['user'].email}: "
                    f"{len(entry['credits'])} credit(s), {total / 100:.2f} EUR"
                )
            self.stdout.write(
                self.style.WARNING(f"Dry run — {len(order)} email(s) NOT sent.")
            )
            return

        sent = failed = 0
        for user_id in order[:limit]:
            entry = by_user[user_id]
            user = entry["user"]
            credits = entry["credits"]
            try:
                result = send_crush_credit_expiry_reminder(
                    user=user, credits=credits, request=None
                )
            except Exception as exc:  # noqa: BLE001 — one bad row must not kill the run
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f"  Error sending to {user.email}: {exc}")
                )
                continue

            if result > 0:
                # Record the send for every credit included in this email —
                # only now, after send_domain_email reported success, so a
                # failed/skipped send leaves nothing to make the next run
                # think it already happened.
                from crush_lu.models.credits import CrushCreditExpiryReminder

                CrushCreditExpiryReminder.objects.bulk_create(
                    [CrushCreditExpiryReminder(credit=credit) for credit in credits],
                    ignore_conflicts=True,
                )
                sent += 1
            else:
                # can_send_email said no (unsubscribed) — not a failure, just
                # nothing to record; the credit stays eligible so a later
                # opt-in would not retroactively get this email (window may
                # have moved on by then, which is fine — it is a courtesy
                # nudge, not a guarantee).
                pass

        if len(order) > limit:
            self.stdout.write(
                self.style.WARNING(
                    f"Limit of {limit} reached — {len(order) - limit} member(s) "
                    "not processed this run; they remain eligible next run."
                )
            )

        self.stdout.write(
            self.style.SUCCESS(f"Summary: Sent: {sent}, Failed: {failed}")
        )
