"""
sumup_checkout_status — ask SumUp what actually happened to a payment.

SumUp's merchant dashboard shows a failed online payment as "Échec" and a
struck-through amount, and nothing else. The detail sits in the checkout
resource: the ``transactions`` array records each card attempt with its own
status and, where the acquirer gave one, a decline reason. This prints it.

Usage::

    # What have we opened lately, and what does our side think happened?
    python manage.py sumup_checkout_status

    # Interrogate SumUp about a specific payment (any one of these)
    python manage.py sumup_checkout_status --reference CRUSH-EVT-42-a1b2c3
    python manage.py sumup_checkout_status --checkout-id 8f2c...
    python manage.py sumup_checkout_status --registration-id 42

    # Same, but also apply what SumUp reports (confirms a seat if it was paid)
    python manage.py sumup_checkout_status --registration-id 42 --sync

Read-only unless ``--sync`` is passed. ``--sync`` runs the same reconciliation
the webhook and the return page use, so a payment that succeeded while our row
was stuck PENDING is properly applied rather than merely described.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from crush_lu.models.payments import PaymentTransaction
from crush_lu.services.sumup import SumUpClient, SumUpError


class Command(BaseCommand):
    help = "Show what SumUp reports for a checkout, including why it failed."

    def add_arguments(self, parser):
        parser.add_argument("--reference", help="Our transaction_reference")
        parser.add_argument("--checkout-id", help="SumUp checkout id")
        parser.add_argument(
            "--registration-id",
            type=int,
            help="EventRegistration id — shows every checkout opened for it",
        )
        parser.add_argument(
            "--recent",
            type=int,
            default=10,
            help="How many recent transactions to list when no selector is "
            "given (default: 10)",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Apply what SumUp reports, don't just print it",
        )

    def handle(self, *args, **options):
        selective = any(
            options.get(key) for key in ("reference", "checkout_id", "registration_id")
        )
        # One payment, named one way. The lookup takes the first selector it
        # finds and ignores the rest, so combining copied identifiers would let
        # an operator reconcile — and with --sync, potentially grant — the
        # payment named by whichever happened to win, while believing the
        # others had narrowed it.
        chosen = [
            key
            for key in ("reference", "checkout_id", "registration_id")
            if options.get(key)
        ]
        if len(chosen) > 1:
            named = ", ".join("--" + key.replace("_", "-") for key in chosen)
            raise CommandError(
                f"Give one selector, not {len(chosen)}: {named} name different "
                "things and only the first would be used."
            )

        # A repair flag that silently does nothing is worse than one that
        # refuses: the listing mode never touches the provider, so --sync on it
        # applied nothing at all while --help promised "apply what SumUp
        # reports". Refusing rather than syncing the recent rows is deliberate —
        # that would fire a provider call per row and could confirm seats nobody
        # asked about, off a command someone ran to look around.
        if options["sync"] and not selective:
            raise CommandError(
                "--sync needs one payment to act on: pass --reference, "
                "--checkout-id or --registration-id. Without a selector this "
                "command only lists, and would apply nothing."
            )

        rows = self._select(options)
        if not rows:
            self.stdout.write(self.style.WARNING("No matching transactions."))
            return

        for tx_obj in rows:
            self._print_local(tx_obj)
            # Listing everything would fire one API call per row; only reach for
            # the provider when a specific payment was asked about.
            if selective:
                self._print_remote(tx_obj, sync=options["sync"])
            self.stdout.write("")

        if not selective:
            self.stdout.write(
                "Pass --reference / --checkout-id / --registration-id to ask "
                "SumUp what happened to one of these."
            )

    def _select(self, options):
        qs = PaymentTransaction.objects.all().order_by("-created_at")
        if options.get("reference"):
            return list(qs.filter(transaction_reference=options["reference"]))
        if options.get("checkout_id"):
            return list(qs.filter(sumup_checkout_id=options["checkout_id"]))
        if options.get("registration_id"):
            return list(qs.filter(event_registration_id=options["registration_id"]))
        recent = options["recent"]
        if recent < 1:
            raise CommandError("--recent must be at least 1")
        return list(qs[:recent])

    def _print_local(self, tx_obj):
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"{tx_obj.transaction_reference}  "
                f"{tx_obj.amount} {tx_obj.currency}  "
                f"[{tx_obj.status}]"
            )
        )
        self.stdout.write(f"  created      {tx_obj.created_at:%Y-%m-%d %H:%M:%S %Z}")
        self.stdout.write(f"  purpose      {tx_obj.purpose}")
        self.stdout.write(f"  checkout id  {tx_obj.sumup_checkout_id or '(none)'}")
        if tx_obj.event_registration_id:
            reg = tx_obj.event_registration
            self.stdout.write(
                f"  buyer        {reg.user.email} "
                f"(registration {reg.id}, status {reg.status}, "
                f"paid={reg.payment_confirmed})"
            )
            self.stdout.write(f"  event        {reg.event.title}")
        elif tx_obj.premium_membership_id:
            pm = tx_obj.premium_membership
            self.stdout.write(
                f"  buyer        {pm.user.email} "
                f"(premium {pm.id}, status {pm.status})"
            )
        if tx_obj.failure_reason:
            self.stdout.write(
                self.style.WARNING(f"  reason       {tx_obj.failure_reason}")
            )

    def _print_remote(self, tx_obj, sync=False):
        if not tx_obj.sumup_checkout_id:
            self.stdout.write("  SumUp        (no checkout id recorded)")
            return

        try:
            data = SumUpClient().get_checkout(tx_obj.sumup_checkout_id)
        except SumUpError as exc:
            self.stdout.write(self.style.ERROR(f"  SumUp        unreachable: {exc}"))
            return

        remote_status = (data.get("status") or "UNKNOWN").upper()
        self.stdout.write(f"  SumUp says   {remote_status}")

        transactions = data.get("transactions") or []
        if not transactions:
            self.stdout.write(
                "  attempts     none — no card was ever submitted against this "
                "checkout (it expired, or we deactivated it when a newer one "
                "was opened)"
            )
        for attempt in transactions:
            if not isinstance(attempt, dict):
                continue
            bits = [f"status={attempt.get('status')}"]
            for key in (
                "failure_reason",
                "error_message",
                "message",
                "auth_code",
                "payment_type",
                "entry_mode",
                "timestamp",
            ):
                if attempt.get(key):
                    bits.append(f"{key}={attempt[key]}")
            self.stdout.write(f"  attempt      {', '.join(bits)}")

        self.stdout.write("  raw:")
        for line in json.dumps(data, indent=2, ensure_ascii=False).splitlines():
            self.stdout.write(f"    {line}")

        if sync:
            # Imported here so the module stays importable without pulling the
            # whole view layer in for a --recent listing.
            from crush_lu.views_payments import (
                _sync_checkout_with_sumup,
                refresh_sumup_snapshot,
            )

            before = tx_obj.status
            if before == PaymentTransaction.Status.PENDING:
                # Its own read, deliberately — the reconciliation that can
                # confirm a seat re-reads from SumUp rather than trusting a
                # payload handed to it. But that means a second call, which can
                # fail on its own, and the error is swallowed inside. Without
                # checking the answer the command would print "synced unchanged
                # (pending)" directly beneath a dump showing SumUp reported the
                # checkout PAID — announcing that the repair had been
                # considered and declined, when it had never been attempted.
                if not _sync_checkout_with_sumup(tx_obj):
                    self.stdout.write(
                        self.style.ERROR(
                            "  synced       no answer from SumUp on the second "
                            "read — NOT synced (see the log for the provider "
                            "error); re-run to apply it"
                        )
                    )
                    return
            else:
                # ``_sync_checkout_with_sumup`` returns immediately for a
                # settled row — that early return is what stops a terminal
                # payment being re-applied — so --sync on a FAILED row used to
                # do nothing while printing "unchanged". Recording the detail is
                # still worth doing, and it is the only way the payments that
                # failed before ``failure_reason`` existed can be given one,
                # which is the whole reason someone runs this command.
                reported = refresh_sumup_snapshot(tx_obj)
                if not reported:
                    # Say so. This branch used to return in silence when SumUp
                    # was unreachable, which in a command whose entire job is
                    # diagnosis reads as "nothing to report" rather than "could
                    # not ask" — the two an operator most needs to tell apart.
                    self.stdout.write(
                        self.style.ERROR(
                            "  refreshed    could not reach SumUp — nothing "
                            "recorded (see the log for the provider error)"
                        )
                    )
                    return
                self.stdout.write(
                    f"  refreshed    detail recorded (SumUp says {reported}; "
                    f"status left at {tx_obj.status})"
                )
                # Only when the two records DISAGREE, and in either direction.
                # SumUp calling a row we wrote off paid is money taken with
                # nothing delivered; SumUp NOT calling a row we marked paid
                # paid is a seat granted against a checkout that never settled.
                # Both are the money and the entitlement coming apart. Agreement
                # stays quiet, because a warning that fires on the ordinary case
                # is one nobody reads.
                provider_paid = reported in ("PAID", "SUCCESSFUL")
                locally_paid = tx_obj.status == PaymentTransaction.Status.PAID
                if provider_paid != locally_paid:
                    self.stdout.write(
                        self.style.ERROR(
                            "  ⚠ recorded here as "
                            f"{tx_obj.status} but SumUp reports {reported} — "
                            "check whether this member was charged."
                        )
                    )
                return

            tx_obj.refresh_from_db()
            if tx_obj.status != before:
                self.stdout.write(
                    self.style.SUCCESS(f"  synced       {before} → {tx_obj.status}")
                )
            else:
                self.stdout.write(f"  synced       unchanged ({tx_obj.status})")
