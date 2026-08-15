"""Crush Credit — the store-credit ledger that replaces cash refunds.

Policy v2 (approved 2026-08-13) stopped promising cash on a member
cancellation and promises **credit** instead: 100% over 48h, 50% inside 48h
*if the seat is actually resold*, and a premium credit plus a cash refund on
request when Crush.lu cancels the event. This module is where that credit
lives.

**Append-only.** A ``CrushCredit`` row records an issue and never has its
``amount_cents`` touched again; spending is recorded as ``CreditRedemption``
rows against it. There is deliberately **no balance field** anywhere — not
here, not on ``CrushProfile``, not on ``User``. A stored balance drifts the
first time a redemption is rolled back, retried or written from a second code
path, and nobody notices until a member is told they have money they do not
have. Balance is always Σ issued − Σ redeemed, computed at read time by
``crush_lu.services.credits.available_credit_cents``.

``status`` is the one mutable field, and it is bookkeeping rather than value:
``active`` → ``consumed`` when the last cent is spent, → ``expired`` when the
sweep passes ``expires_at`` (PR 2), → ``void`` when staff withdraw an issue
that should never have happened. None of those change what was issued.

Amounts are integer **cents**, not ``Decimal``. ``PaymentTransaction.amount``
is a 2dp Decimal because that is what SumUp is quoted in; credit is arithmetic
we do ourselves — halving a fee, summing a ledger, subtracting redemptions —
and cents keep every one of those exact with no quantize step to forget.
"""

import calendar

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crush_lu.utils.formatting import format_cents

# How long an issued credit stays spendable. Settable so the policy can move
# without a migration; the published Terms say 6 months.
DEFAULT_EXPIRY_MONTHS = 6


def add_months(moment, months):
    """``moment`` plus whole calendar months, clamped to a real day.

    Not ``timedelta(days=182)``: the member-facing text and the emails both say
    "valid for 6 months", and a credit issued on 31 August has to expire on 28
    February, not on the 3rd of March. Calendar months are what was promised,
    so calendar months are what is stored.
    """
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


class CrushCredit(models.Model):
    """One issue of store credit to one member.

    Created only through ``crush_lu.services.credits.issue_credit`` — no view,
    signal or admin builds one directly. That function is also where
    ``payment_confirmed`` gets cleared on the source registration, and the two
    have to happen in one atomic block (see its docstring for why).
    """

    class Reason(models.TextChoices):
        MEMBER_CANCELLATION = "member_cancellation", _("Member cancelled (>48h)")
        SEAT_RESOLD = "seat_resold", _("Late cancellation, seat resold")
        EVENT_CANCELLED = "event_cancelled", _("Crush.lu cancelled the event")
        GOODWILL = "goodwill", _("Goodwill")
        REFERRAL = "referral", _("Referral")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        CONSUMED = "consumed", _("Fully redeemed")
        EXPIRED = "expired", _("Expired")
        VOID = "void", _("Void")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # Financial ledgers must outlive an accidental hard-delete. The
        # product-level Crush profile deletion flow already voids active
        # credit without deleting the auth user; a true User deletion now
        # requires an explicit retention/anonymisation decision instead of
        # silently erasing issued value.
        on_delete=models.PROTECT,
        related_name="crush_credits",
        db_index=True,
        help_text=_("The member who holds this credit. Non-transferable."),
    )
    amount_cents = models.PositiveIntegerField(
        help_text=_("Face value issued, in cents. Never edited after issue."),
    )
    currency = models.CharField(max_length=3, default="EUR")

    issued_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(
        db_index=True,
        help_text=_("Computed on first save as issued_at + 6 months, then stored."),
    )

    reason = models.CharField(max_length=32, choices=Reason.choices, db_index=True)
    source_registration = models.ForeignKey(
        "crush_lu.EventRegistration",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_credits",
        help_text=_("The seat this credit was issued against, where there was one."),
    )
    source_payment = models.ForeignKey(
        "crush_lu.PaymentTransaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_credits",
        help_text=_("The captured payment this credit gives back, where known."),
    )
    restored_from_credit = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="restoration_credits",
        help_text=_(
            "The original funding tranche restored by this issue. Its expiry "
            "is preserved so booking and cancelling cannot extend credit."
        ),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    cash_refund_eligible = models.BooleanField(
        default=False,
        help_text=_(
            "This member may ask for their money back instead. Set only when "
            "Crush.lu cancelled the event — Luxembourg consumer law entitles "
            "them to a cash refund there and a voucher is not a substitute. "
            "Refunds themselves are still made by hand in the SumUp dashboard."
        ),
    )
    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-issued_at", "-id"]
        verbose_name = _("Crush Credit")
        verbose_name_plural = _("Crush Credits")
        indexes = [
            # The balance read: active credits for one member, oldest expiry
            # first, which is also the order they are spent in.
            models.Index(
                fields=["user", "status", "expires_at"],
                name="crushcredit_balance_idx",
            ),
            # The staff queue of everyone owed a possible cash refund.
            models.Index(
                fields=["cash_refund_eligible", "issued_at"],
                name="crushcredit_refundq_idx",
            ),
        ]
        constraints = [
            # The resale clause pays out once per PAID SEAT — which is one per
            # *payment*, not one per registration row.
            #
            # This originally keyed on (source_registration, reason) and that
            # was wrong in a way that crashed a member-facing flow.
            # ``event_register`` reuses the same ``EventRegistration`` row every
            # time a member re-registers for an event, so someone who pays,
            # late-cancels, watches the seat resell, re-registers, pays AGAIN
            # and late-cancels again is legitimately owed a second 50% share —
            # two payments, two resold seats. The old constraint refused it with
            # an IntegrityError that took the whole cancellation down with it.
            #
            # Keyed on the payment, the constraint says what was actually meant:
            # one resale credit per captured payment. A second cycle carries a
            # different ``PaymentTransaction`` and passes; a duplicate for the
            # same one still cannot be written.
            #
            # NULL ``source_payment`` (a legacy row with no attributable
            # capture) does not collide in Postgres, so this is a backstop, not
            # the guard. The guard is ``payment_confirmed``, read under lock —
            # and ``maybe_issue_resale_credits`` also catches IntegrityError, so
            # no path here can 500 a member trying to cancel.
            models.UniqueConstraint(
                fields=["source_payment", "reason"],
                condition=models.Q(
                    reason="seat_resold",
                    restored_from_credit__isnull=True,
                    source_payment__isnull=False,
                ),
                name="one_resale_credit_per_payment",
            ),
            models.UniqueConstraint(
                fields=[
                    "source_payment",
                    "reason",
                    "restored_from_credit",
                ],
                condition=models.Q(
                    reason="seat_resold",
                    restored_from_credit__isnull=False,
                    source_payment__isnull=False,
                ),
                name="one_resale_restore_per_tranche",
            ),
        ]
        permissions = [
            ("void_crushcredit", "Can void Crush Credit"),
        ]

    def __str__(self):
        return (
            f"{format_cents(self.amount_cents, self.currency)} "
            f"({self.get_reason_display()}) → {self.user}"
        )

    def save(self, *args, **kwargs):
        # Stored rather than derived so an expiry sweep can filter on it in SQL
        # and so moving DEFAULT_EXPIRY_MONTHS never silently re-dates credit
        # already in members' hands. Computed only when absent: the admin and
        # the tests both need to be able to state an expiry outright.
        if not self.expires_at:
            months = getattr(
                settings, "CRUSH_CREDIT_EXPIRY_MONTHS", DEFAULT_EXPIRY_MONTHS
            )
            self.expires_at = add_months(self.issued_at or timezone.now(), months)
        return super().save(*args, **kwargs)

    @property
    def redeemed_cents(self):
        """What has been spent off this credit. Query per call — use the
        annotated queryset in the service and the admin for lists."""
        return (
            self.redemptions.aggregate(total=models.Sum("amount_cents"))["total"] or 0
        )

    @property
    def remaining_cents(self):
        """What is left on this credit.

        Partial consumption is real and not optional: the event-cancellation
        premium issues €20.00 against a €15.50 seat, so the first redemption
        spends 1550 of 2000 and 450 stays spendable. Treating a credit as
        all-or-nothing would make that €20 worth €20 forever.
        """
        return max(0, self.amount_cents - self.redeemed_cents)

    @property
    def is_spendable(self):
        return (
            self.status == self.Status.ACTIVE
            and self.expires_at > timezone.now()
            and self.remaining_cents > 0
        )

    @property
    def cash_refund_still_available(self):
        """Can this member still ask for money instead — right now?

        ``cash_refund_eligible`` records only that the credit was *issued*
        under an organiser cancellation. The offer closes the moment the member
        takes the alternative: spending any of it, letting it expire, or having
        it voided after a cash refund already paid.

        The staff queue applies exactly these conditions, and so must anything
        else that tells someone whether cash is available — the member's own
        data export said "true" off the raw flag long after the answer had
        become no.
        """
        return (
            self.cash_refund_eligible
            and self.status == self.Status.ACTIVE
            and self.expires_at > timezone.now()
            and self.redeemed_cents == 0
        )


class CreditRedemption(models.Model):
    """One spend of one credit against one event registration.

    The join row is what makes the ledger work: a credit can be spent across
    several registrations, and a registration can be paid by several credits.

    ⚠️ ``event_registration`` is ``SET_NULL``, deliberately, and this is the
    whole ledger hanging on one keyword. Under ``CASCADE`` — which is what this
    was first written as — deleting a registration deleted the redemption rows
    against it, and the credit they were spent from silently refilled: a partly
    spent credit stays ``ACTIVE``, so ``available_credit_cents`` would hand the
    member back money they had already spent. Registrations are deletable from
    the admin and are removed by ``services/account_merge.py``, so that is a
    real flow, not a hypothetical. A redemption is a record of value leaving
    the ledger and must outlive whatever it was spent on.
    """

    credit = models.ForeignKey(
        CrushCredit,
        on_delete=models.CASCADE,
        related_name="redemptions",
    )
    event_registration = models.ForeignKey(
        "crush_lu.EventRegistration",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_redemptions",
        help_text=_("The seat this was spent on, while that seat still exists."),
    )
    amount_cents = models.PositiveIntegerField()
    redeemed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-redeemed_at", "-id"]
        verbose_name = _("Credit Redemption")
        verbose_name_plural = _("Credit Redemptions")
        indexes = [
            models.Index(
                fields=["event_registration"],
                name="creditredeem_reg_idx",
            ),
        ]

    def __str__(self):
        return (
            f"{format_cents(self.amount_cents, 'EUR')} off credit #{self.credit_id} "
            f"→ registration {self.event_registration_id}"
        )
