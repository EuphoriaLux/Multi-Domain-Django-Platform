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
The log is the campaign's email leg (``NewsletterRecipient``), so the campaign
dashboard shows the sends. Idempotency: a row is created BEFORE each send (its
unique (newsletter, user) key blocks a second claim) and stamped after. A crash
between the two leaves a ``pending`` row that is never re-sent; check it with
``--report``. ``--retry-failed`` re-attempts rows that failed with a delivery
error. There is deliberately no batch-wide ``transaction.atomic``: a rollback
at the end would un-record emails that were already delivered.
"""

import time
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_lu.campaign_women_1y import (
    CAMPAIGN_SLUG,
    campaign_report,
    eligible_recipients,
    finalize_status,
    get_campaign,
    get_newsletter,
    send_women_1y_email,
    sync_newsletter_counters,
)
from crush_lu.models import Campaign, NewsletterRecipient
from crush_lu.newsletter_service import BATCH_PAUSE_SECONDS, BATCH_SIZE
from crush_lu.utils.i18n import build_absolute_url

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
        parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
        parser.add_argument(
            "--batch-pause",
            type=float,
            default=BATCH_PAUSE_SECONDS,
            help="Seconds between batches (Graph allows ~30 mails/minute).",
        )
        parser.add_argument(
            "--delay", type=float, default=0, help="Seconds between emails."
        )

    def handle(self, *args, **opts):
        modes = [
            name
            for name, on in (
                ("--dry-run", opts["dry_run"]),
                ("--send", opts["send"]),
                ("--test-to", opts["test_to"]),
                ("--report", opts["report"]),
            )
            if on
        ]
        if len(modes) > 1:
            raise CommandError(f"Choose one mode, not {' + '.join(modes)}.")
        if opts["limit"] is not None and opts["limit"] < 1:
            raise CommandError("--limit must be at least 1.")
        if opts["batch_size"] < 1:
            raise CommandError("--batch-size must be at least 1.")
        if opts["delay"] < 0 or opts["batch_pause"] < 0:
            raise CommandError("--delay and --batch-pause must not be negative.")
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
            NewsletterRecipient.objects.filter(newsletter__campaign__slug=CAMPAIGN_SLUG)
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
        user = get_user_model().objects.filter(email__iexact=to).first()
        unsubscribe_url = None
        if user is None:
            # No account for this address: never borrow a real member's live
            # unsubscribe link. Point the footer at the (login-gated) settings.
            unsubscribe_url = (
                build_absolute_url("crush_lu:edit_profile")
                + "?section=account&sub=notifications"
            )
        sent = send_women_1y_email(
            user,
            campaign,
            to=to,
            test_mode=True,
            unsubscribe_url=unsubscribe_url,
        )
        self.stdout.write(self.style.SUCCESS(f"Test email to {to}: sent={sent}"))

    def _report(self):
        campaign = get_campaign()
        report = campaign_report(campaign)
        if report is None:
            return self.stdout.write("No campaign yet: nothing has been sent.")
        pending = NewsletterRecipient.objects.filter(
            newsletter__campaign=campaign, status="pending"
        ).count()
        for key, value in {**report, "stuck_pending": pending}.items():
            self.stdout.write(f"{key}: {value}")

    def _send(self, opts):
        # The value is an ownership token: only the process that set the lock
        # may renew or release it (a run longer than the TTL must not delete a
        # second run's lock).
        token = uuid.uuid4().hex
        if not cache.add(LOCK_KEY, token, LOCK_TTL):
            raise CommandError("Another send is running (lock held).")
        try:
            return self._send_locked(opts, token)
        finally:
            # Only the lock owner may touch shared state; a run that lost its
            # lock must not reset a newer run's campaign status.
            if cache.get(LOCK_KEY) == token:
                cache.delete(LOCK_KEY)
                # Back to the idle, non-launchable state (never leave "sending").
                Campaign.objects.filter(slug=CAMPAIGN_SLUG, status="sending").update(
                    status="partial"
                )

    @staticmethod
    def _pace(opts, attempted):
        """Throttle on ATTEMPTED sends: failed calls use Graph quota too."""
        if opts["delay"]:
            time.sleep(opts["delay"])
        if attempted % opts["batch_size"] == 0 and opts["batch_pause"]:
            time.sleep(opts["batch_pause"])

    def _send_locked(self, opts, token):
        campaign = get_campaign(create=True)
        if campaign.status == "cancelled":
            raise CommandError("The campaign is cancelled; not sending.")
        newsletter = get_newsletter(campaign)
        if campaign.started_at is None:
            campaign.started_at = timezone.now()
            campaign.save(update_fields=["started_at"])
        # "sending" is what makes Cancel available in the Coach Panel; the
        # generic dispatcher never claims this campaign (MANUAL_ONLY_SLUGS).
        Campaign.objects.filter(pk=campaign.pk).exclude(status="cancelled").update(
            status="sending"
        )
        sent = failed = skipped = attempted = 0
        limit = opts["limit"]
        for user in self._recipients(opts).iterator():
            if limit is not None and sent + failed >= limit:
                break
            if Campaign.objects.filter(pk=campaign.pk, status="cancelled").exists():
                self.stderr.write("Campaign cancelled: stopping.")
                break
            if cache.get(LOCK_KEY) != token:
                self.stderr.write("Send lock lost: stopping.")
                break
            cache.touch(LOCK_KEY, LOCK_TTL)
            # Re-read the member at send time (not the iterator snapshot): they
            # may have unsubscribed, been banned or changed their email while a
            # long run worked through the list.
            user = eligible_recipients(include_sent=True).filter(pk=user.pk).first()
            if user is None:
                skipped += 1
                continue
            row, created = NewsletterRecipient.objects.get_or_create(
                newsletter=newsletter,
                user=user,
                defaults={"email": user.email, "status": "pending"},
            )
            if not created:
                if row.status != "failed" or not opts["retry_failed"]:
                    skipped += 1
                    continue
                # Reclaim a failed receipt for a fresh attempt.
                row.status = "pending"
                row.error_message = ""
                row.email = user.email
                row.save(update_fields=["status", "error_message", "email"])
            sync_newsletter_counters(newsletter)
            attempted += 1
            try:
                ok = send_women_1y_email(user, campaign)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - one bad address must not stop the run
                row.status = "failed"
                row.error_message = str(exc)[:500]
                row.save(update_fields=["status", "error_message"])
                sync_newsletter_counters(newsletter)
                failed += 1
                self.stderr.write(f"failed {user.pk}: {exc}")
                self._pace(opts, attempted)
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
            sync_newsletter_counters(newsletter)
            self._pace(opts, attempted)
        if cache.get(LOCK_KEY) != token:
            # Lost the lock: another run owns the campaign now; leave its state.
            self.stdout.write(
                f"sent={sent} failed={failed} skipped={skipped} (lock lost)"
            )
            return
        status = finalize_status(campaign)
        if status == "sending":
            status = "partial"  # what the idle-state cleanup persists
        remaining = eligible_recipients().count()
        self.stdout.write(
            self.style.SUCCESS(
                f"sent={sent} failed={failed} skipped={skipped} "
                f"remaining={remaining} campaign={status}"
            )
        )
