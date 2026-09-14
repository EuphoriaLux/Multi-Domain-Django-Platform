"""Inspect or retire stale event-checkout claims and legacy orphan payments.

Claims normally live only around provider I/O.  A stopped worker can leave one
behind, so this command gives operations a bounded, provider-safe recovery path
instead of making account cleanup depend on the same member retrying checkout.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from crush_lu.models.payments import (
    EventCheckoutCreationClaim,
    PaymentTransaction,
)
from crush_lu.services.sumup import SumUpClient

MINIMUM_STALE_MINUTES = 5


class Command(BaseCommand):
    help = (
        "Report or safely retire stale event checkout creation claims and "
        "PENDING SumUp event payments whose registration was already erased."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Deactivate provider checkouts, retire claims, and cancel proven-safe "
                "legacy orphan PENDING payments."
            ),
        )
        parser.add_argument("--minutes", type=int, default=10)
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        minutes = options["minutes"]
        limit = options["limit"]
        if minutes < MINIMUM_STALE_MINUTES:
            raise CommandError(
                f"--minutes must be at least {MINIMUM_STALE_MINUTES}; a younger "
                "claim may still own bounded provider I/O"
            )
        if limit < 1 or limit > 100:
            raise CommandError("--limit must be between 1 and 100")

        cutoff = timezone.now() - timedelta(minutes=minutes)
        claims = list(
            EventCheckoutCreationClaim.objects.filter(
                claimed_at__lt=cutoff,
                state=EventCheckoutCreationClaim.State.ACTIVE,
            ).order_by("claimed_at", "pk")[:limit]
        )
        orphan_payments = list(
            PaymentTransaction.objects.filter(
                created_at__lt=cutoff,
                event_registration__isnull=True,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                provider=PaymentTransaction.Provider.SUMUP,
                status=PaymentTransaction.Status.PENDING,
            ).order_by("created_at", "pk")[:limit]
        )
        mode = "APPLY" if options["apply"] else "DRY-RUN"
        self.stdout.write(f"Stale event checkout claims [{mode}]: {len(claims)}")
        if not options["apply"]:
            for claim in claims:
                state = (
                    "provider checkout known"
                    if claim.provider_checkout_id
                    else "pre-provider"
                )
                self.stdout.write(
                    f"  claim {claim.pk}: registration "
                    f"{claim.registration_id_snapshot}, {state}, "
                    f"reference {claim.transaction_reference}"
                )
            self.stdout.write(
                f"Legacy orphan PENDING event payments [{mode}]: "
                f"{len(orphan_payments)}"
            )
            for payment in orphan_payments:
                state = (
                    f"provider checkout {payment.sumup_checkout_id}"
                    if payment.sumup_checkout_id
                    else "provider checkout unknown"
                )
                self.stdout.write(
                    f"  payment {payment.pk}: user {payment.user_id}, {state}, "
                    f"reference {payment.transaction_reference}"
                )
            return

        client = SumUpClient()
        retired = 0
        retained = 0
        for snapshot in claims:
            with transaction.atomic():
                claim = (
                    EventCheckoutCreationClaim.objects.select_for_update()
                    .filter(
                        pk=snapshot.pk,
                        claimed_at__lt=cutoff,
                        state=EventCheckoutCreationClaim.State.ACTIVE,
                    )
                    .first()
                )
                if claim is None:
                    continue
                claim.state = EventCheckoutCreationClaim.State.RETIRING
                claim.save(update_fields=["state"])

            # A published payment and claim should be committed atomically, but
            # prefer the financial record if legacy/manual repair left both.
            if PaymentTransaction.objects.filter(
                transaction_reference=snapshot.transaction_reference
            ).exists():
                provider_safe = True
            elif snapshot.provider_checkout_id:
                provider_safe = client.ensure_checkout_not_payable(
                    snapshot.provider_checkout_id
                )
            else:
                # A remote POST may have timed out after SumUp accepted it.
                # Without a provider ID, absence is ambiguous rather than safe.
                provider_safe = False

            if not provider_safe:
                retained += 1
                self.stderr.write(
                    f"  retained claim {snapshot.pk}: provider absence or "
                    "deactivation could not be proven"
                )

            with transaction.atomic():
                claim = (
                    EventCheckoutCreationClaim.objects.select_for_update()
                    .filter(
                        pk=snapshot.pk,
                        claimed_at__lt=cutoff,
                        state=EventCheckoutCreationClaim.State.RETIRING,
                    )
                    .first()
                )
                if claim is None:
                    continue
                if (
                    claim.provider_checkout_id != snapshot.provider_checkout_id
                    or claim.transaction_reference != snapshot.transaction_reference
                ):
                    claim.state = EventCheckoutCreationClaim.State.ACTIVE
                    claim.save(update_fields=["state"])
                    if provider_safe:
                        retained += 1
                    continue
                if provider_safe:
                    claim.delete()
                    retired += 1
                else:
                    claim.state = EventCheckoutCreationClaim.State.ACTIVE
                    claim.save(update_fields=["state"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Retired {retired} stale claim(s); retained {retained}."
            )
        )

        orphan_retired = 0
        orphan_retained = 0
        for snapshot in orphan_payments:
            if not snapshot.sumup_checkout_id:
                orphan_retained += 1
                self.stderr.write(
                    f"  retained orphan payment {snapshot.pk}: provider checkout "
                    "id is unknown"
                )
                continue

            provider_safe = client.ensure_checkout_not_payable(
                snapshot.sumup_checkout_id
            )
            if not provider_safe:
                orphan_retained += 1
                self.stderr.write(
                    f"  retained orphan payment {snapshot.pk}: provider "
                    "deactivation could not be proven"
                )
                continue

            with transaction.atomic():
                payment = (
                    PaymentTransaction.objects.select_for_update(of=("self",))
                    .filter(pk=snapshot.pk)
                    .first()
                )
                if payment is None:
                    continue
                if payment.status != PaymentTransaction.Status.PENDING:
                    # A concurrent successful callback owns the captured-value
                    # remedy; _apply_paid_checkout handles the NULL registration
                    # fail-safe in the same transaction as PAID.
                    continue
                if (
                    payment.event_registration_id is not None
                    or payment.provider != PaymentTransaction.Provider.SUMUP
                    or payment.purpose != PaymentTransaction.Purpose.EVENT_REGISTRATION
                    or payment.sumup_checkout_id != snapshot.sumup_checkout_id
                    or payment.transaction_reference != snapshot.transaction_reference
                ):
                    orphan_retained += 1
                    continue
                payment.status = PaymentTransaction.Status.CANCELLED
                payment.failure_reason = (
                    "Legacy orphan event checkout safely deactivated by "
                    "cleanup_event_checkout_claims."
                )
                payment.save(update_fields=["status", "failure_reason", "updated_at"])
                orphan_retired += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Retired "
                f"{orphan_retired} legacy orphan payment(s); retained "
                f"{orphan_retained}."
            )
        )
