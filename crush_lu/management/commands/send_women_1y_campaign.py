"""
Send the women's 1-year campaign ("We saved you a seat").

Safe by default: with no flags this only PRINTS the recipient count and a
sample. Nothing is sent until you pass ``--send``.

Usage::

    # Look first (default): count + sample recipients
    python manage.py send_women_1y_campaign

    # One real email to yourself, to check rendering (no log row written)
    python manage.py send_women_1y_campaign --test-to you@example.com

    # Send, capped
    python manage.py send_women_1y_campaign --send --limit 50

    # Clicks and verification conversions for everyone mailed so far
    python manage.py send_women_1y_campaign --report

Recipients and the send log are defined in ``crush_lu/campaign_women_1y.py``.
Idempotency: a ``CampaignRecipient`` row is created BEFORE each send (its
unique (campaign, channel, user) key blocks a second claim) and stamped after.
A crash between the two leaves a ``pending`` row that is never re-sent; check
it with ``--report``. ``--retry-failed`` re-attempts rows that failed with a
delivery error. There is deliberately no batch-wide ``transaction.atomic``: a
rollback at the end would un-record emails that were already delivered.
"""

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from django.utils import timezone

from crush_lu.campaign_women_1y import (
    CAMPAIGN_SLUG,
    CHANNEL,
    campaign_report,
    eligible_recipients,
    get_campaign,
    send_women_1y_email,
)
from crush_lu.models import CampaignRecipient

LOCK_KEY = "women_1y_campaign_send_lock"
LOCK_TTL = 3600
SAMPLE_SIZE = 10


class Command(BaseCommand):
    help = "Send the women's 1-year campaign (dry-run unless --send)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Default mode.")
        parser.add_argument("--send", action="store_true", help="Actually send.")
        parser.add_argument("--limit", type=int, default=None, help="Max emails.")
        parser.add_argument("--test-to", metavar="EMAIL", help="Send one test email.")
        parser.add_argument("--report", action="store_true", help="Show results.")
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--batch-size", type=int, default=25)
        parser.add_argument(
            "--batch-pause", type=float, default=5.0, help="Seconds between batches."
        )
        parser.add_argument(
            "--delay", type=float, default=0.5, help="Seconds between emails."
        )

    def handle(self, *args, **opts):
        if opts["send"] and (opts["dry_run"] or opts["test_to"] or opts["report"]):
            raise CommandError("--send cannot be combined with other modes.")
        if opts["limit"] is not None and opts["limit"] < 1:
            raise CommandError("--limit must be at least 1.")
        if opts["batch_size"] < 1:
            raise CommandError("--batch-size must be at least 1.")
        if opts["report"]:
            return self._report()
        if opts["test_to"]:
            return self._test(opts["test_to"])
        if opts["send"]:
            return self._send(opts)
        return self._dry_run(opts)

    def _recipients(self, opts):
        if not opts.get("retry_failed"):
            return eligible_recipients()
        # Also re-attempt rows that failed: everyone eligible whose only log
        # row (if any) is a failure.
        logged_ok = (
            CampaignRecipient.objects.filter(
                campaign__slug=CAMPAIGN_SLUG, channel=CHANNEL
            )
            .exclude(status="failed")
            .values("user_id")
        )
        return eligible_recipients(include_sent=True).exclude(pk__in=logged_ok)

    def _dry_run(self, opts):
        qs = self._recipients(opts)
        total = qs.count()
        will_send = min(total, opts["limit"]) if opts["limit"] else total
        self.stdout.write(f"[dry-run] eligible recipients: {total}")
        if opts["limit"]:
            self.stdout.write(
                f"[dry-run] --limit {opts['limit']}: would send {will_send}"
            )
        for user in qs[:SAMPLE_SIZE]:
            self.stdout.write(f"  - {user.pk}  {user.email}")
        if total > SAMPLE_SIZE:
            self.stdout.write(f"  ... and {total - SAMPLE_SIZE} more")
        self.stdout.write("Nothing sent. Re-run with --send to send.")

    def _test(self, to):
        campaign = get_campaign(create=True)
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(email__iexact=to).first()
        if user is None:
            user = eligible_recipients(include_sent=True).first()
        if user is None:
            raise CommandError(
                "No user to render the email for: the test needs one real user "
                "for the unsubscribe link."
            )
        sent = send_women_1y_email(user, campaign, to=to, test_mode=True)
        self.stdout.write(self.style.SUCCESS(f"Test email to {to}: sent={sent}"))

    def _report(self):
        report = campaign_report(get_campaign())
        if report is None:
            return self.stdout.write("No campaign yet: nothing has been sent.")
        pending = CampaignRecipient.objects.filter(
            campaign__slug=CAMPAIGN_SLUG, channel=CHANNEL, status="pending"
        ).count()
        for key, value in {**report, "stuck_pending": pending}.items():
            self.stdout.write(f"{key}: {value}")

    def _send(self, opts):
        if not cache.add(LOCK_KEY, "1", LOCK_TTL):
            raise CommandError("Another send is running (lock held).")
        try:
            return self._send_locked(opts)
        finally:
            cache.delete(LOCK_KEY)

    def _send_locked(self, opts):
        campaign = get_campaign(create=True)
        sent = failed = skipped = 0
        limit = opts["limit"]
        for user in self._recipients(opts).iterator():
            if limit is not None and sent + failed >= limit:
                break
            # Re-check eligibility at send time: a member can unsubscribe or be
            # banned while a long run works through its list.
            if not eligible_recipients(include_sent=True).filter(pk=user.pk).exists():
                skipped += 1
                continue
            try:
                row, created = CampaignRecipient.objects.get_or_create(
                    campaign=campaign,
                    channel=CHANNEL,
                    user=user,
                    defaults={"status": "pending"},
                )
            except IntegrityError:
                skipped += 1
                continue
            if not created:
                if row.status != "failed" or not opts["retry_failed"]:
                    skipped += 1
                    continue
                row.status = "pending"
                row.save(update_fields=["status"])
            try:
                ok = send_women_1y_email(user, campaign)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - one bad address must not stop the run
                row.status = "failed"
                row.error_message = str(exc)[:500]
                row.save(update_fields=["status", "error_message"])
                failed += 1
                self.stderr.write(f"failed {user.pk}: {exc}")
                continue
            if ok:
                row.status = "sent"
                row.sent_at = timezone.now()
                sent += 1
            else:
                # Suppressed address or no backend delivery: terminal, not retried.
                row.status = "skipped"
                skipped += 1
            row.save(update_fields=["status", "sent_at"])
            if opts["delay"]:
                time.sleep(opts["delay"])
            if sent and sent % opts["batch_size"] == 0 and opts["batch_pause"]:
                time.sleep(opts["batch_pause"])
        remaining = eligible_recipients().count()
        if remaining == 0 and sent:
            campaign.status = "sent"
            campaign.completed_at = timezone.now()
            campaign.save(update_fields=["status", "completed_at"])
        self.stdout.write(
            self.style.SUCCESS(
                f"sent={sent} failed={failed} skipped={skipped} remaining={remaining}"
            )
        )
