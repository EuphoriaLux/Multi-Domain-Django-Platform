import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class PaymentTransactionQuerySet(models.QuerySet):
    def paid_event_registrations(self):
        """Captured cash for event seats — the definition every report shares.

        Three readers ask this question and must agree by construction rather
        than by convention: ``business_plan_metrics``, ``weekly_kpis`` and the
        admin revenue dashboard. They diverge only in what they do afterwards
        (group by event, count by paid week, sum the amount), so the shared
        part is the definition itself — which provider counts as money, and
        what "paid" means.

        CREDIT is excluded deliberately. Crush Credit is a payment *method*,
        not new revenue: the cash behind a redeemed credit was captured (and
        counted) when it was first taken, so counting it again would book one
        payment in two periods.
        """
        return self.filter(
            provider__in=(
                PaymentTransaction.Provider.SUMUP,
                PaymentTransaction.Provider.MANUAL,
            ),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=PaymentTransaction.Status.PAID,
        )

    def bought_by(self, user):
        """Payments FOR ``user`` -- the stamped buyer, the links otherwise.

        Same answer as ``PaymentTransaction.owner_id`` row by row, so a
        payment is listed under exactly one account. Rows without a buyer
        (created by the old code during a slot swap, or left unresolved by
        migration 0279) fall back to the derivation that predates ``buyer``.
        """
        return self.filter(
            Q(buyer=user)
            | (
                Q(buyer__isnull=True)
                & (
                    Q(event_registration__user=user)
                    | Q(event_registration__isnull=True, premium_membership__user=user)
                    | Q(
                        event_registration__isnull=True,
                        premium_membership__isnull=True,
                        user=user,
                    )
                )
            )
        )


class EventCheckoutCreationClaim(models.Model):
    """Durable mutex while one event checkout is being replaced at SumUp.

    A row lock cannot cover the first-checkout case because no payment row
    exists yet, and holding the event/roster locks over provider I/O stalls an
    entire curated evening.  This one-per-registration claim bridges the short
    database validation phases around the unlocked network phase.  The stored
    reference also leaves an audit handle if the process stops after SumUp
    creates a checkout but before the local payment row is published.
    """

    class State(models.TextChoices):
        ACTIVE = "active", _("Active")
        RETIRING = "retiring", _("Retiring")
        RETIRED = "retired", _("Safely retired")

    registration = models.OneToOneField(
        "crush_lu.EventRegistration",
        # Account erasure must not be held hostage by an abandoned worker.
        # Snapshot ids below keep the claim operable after this link clears.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkout_creation_claim",
    )
    registration_id_snapshot = models.PositiveBigIntegerField(db_index=True)
    event_id_snapshot = models.PositiveBigIntegerField(db_index=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    transaction_reference = models.CharField(max_length=64, unique=True)
    payment_method = models.CharField(
        max_length=10,
        choices=(("card", _("Card")), ("credit", _("Crush Credit"))),
    )
    claimed_at = models.DateTimeField(default=timezone.now, db_index=True)
    state = models.CharField(
        max_length=10,
        choices=State.choices,
        default=State.ACTIVE,
        db_index=True,
    )
    provider_checkout_id = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        verbose_name = _("Event checkout creation claim")
        verbose_name_plural = _("Event checkout creation claims")


class PaymentTransaction(models.Model):
    """
    Tracks payment transactions initiated via payment providers (SumUp, Mollie).
    Links payment checkouts to EventRegistration or PremiumMembership models.
    """

    objects = PaymentTransactionQuerySet.as_manager()

    class Provider(models.TextChoices):
        SUMUP = "sumup", _("SumUp")
        # Crush Credit is a payment *method*, not a payment provider — no money
        # moves and no checkout exists at anybody's API. It lives here because
        # this field is what every reader uses to decide how a payment was
        # made, and because keeping credit payments out of the SumUp value is
        # what stops `sumup_checkout_status` and the reconciliation sweep
        # hunting for a checkout that was never opened. A CREDIT row carries an
        # empty `sumup_checkout_id` for the same reason.
        CREDIT = "credit", _("Crush Credit")
        MANUAL = "manual", _("Cash / bank transfer")
        # Donations since 2026-10 (crush_lu/views_mollie.py). The payment id
        # lives in ``mollie_payment_id``, never in ``sumup_checkout_id``.
        MOLLIE = "mollie", _("Mollie")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PAID = "paid", _("Paid")
        FAILED = "failed", _("Failed")
        CANCELLED = "cancelled", _("Cancelled")
        REFUNDED = "refunded", _("Refunded")

    class Purpose(models.TextChoices):
        EVENT_REGISTRATION = "event_registration", _("Event Registration")
        PREMIUM_MEMBERSHIP = "premium_membership", _("Premium Membership")
        DONATION = "donation", _("Donation / Support")

    transaction_reference = models.CharField(
        max_length=64,
        unique=True,
        default=uuid.uuid4,
        help_text=_("Unique internal reference ID for this payment transaction"),
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.SUMUP,
    )
    sumup_checkout_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text=_("Checkout ID generated by SumUp API"),
    )
    sumup_customer_id = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Customer ID generated by SumUp API for tokenization"),
    )
    # db_default as well as default: during a slot swap the release still
    # serving inserts rows without this column, and they must not fail.
    mollie_payment_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_default="",
        db_index=True,
        help_text=_("Payment ID generated by the Mollie API (tr_...)"),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text=_("Payment amount in specified currency"),
    )
    currency = models.CharField(
        max_length=3,
        default="EUR",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    purpose = models.CharField(
        max_length=30,
        choices=Purpose.choices,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # A captured payment is a financial record. Do not let a hard User
        # delete erase it; profile-only deletion/anonymisation is the supported
        # member flow.
        on_delete=models.PROTECT,
        related_name="payment_transactions",
        null=True,
        blank=True,
    )
    # Who the payment is FOR. ``user`` is whoever opened the checkout, which is
    # the staff account when staff act for a member, and the links that name
    # the member do not last: deleting a registration SET_NULLs it. ``save``
    # stamps this once from the evidence (see ``_evident_buyer_id``) and
    # nothing changes it afterwards -- not even an account merge, which
    # ``AccountMerge`` resolves instead. NULL means "not known": read
    # ``owner_id()``, never this field directly.
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # A financial record, like ``user``: erasure anonymises the account,
        # it never deletes it.
        on_delete=models.PROTECT,
        related_name="purchased_payments",
        null=True,
        blank=True,
        editable=False,
    )
    event_registration = models.ForeignKey(
        "crush_lu.EventRegistration",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_transactions",
    )
    event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        # Captured payments are immutable event revenue. Only PAID/REFUNDED
        # rows receive this relation (see save), so an abandoned checkout does
        # not prevent stock-admin event deletion.
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_transactions",
        help_text=_(
            "Immutable event attribution, retained if the registration is deleted."
        ),
    )
    # PROTECT (#925): deleting a membership while its checkout can still
    # capture would leave the payment without the member it was for.
    premium_membership = models.ForeignKey(
        "crush_lu.PremiumMembership",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_transactions",
    )
    raw_response = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Raw JSON response / webhook payload from payment provider"),
    )
    failure_reason = models.TextField(
        blank=True,
        default="",
        help_text=_(
            "Why this payment did not complete, in words. The provider's own "
            "wording where it gives one."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_("When this transaction first became paid. Never moved later."),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Payment Transaction")
        verbose_name_plural = _("Payment Transactions")

    def __str__(self):
        return f"{self.provider.upper()} {self.transaction_reference} - {self.amount} {self.currency} ({self.get_status_display()})"

    # Providers whose checkout creators always pass the member as ``user``
    # (the admin's manual capture and the Crush Credit redemption), so an
    # unlinked row of theirs still names its buyer.
    MEMBER_STAMPED_PROVIDERS = (Provider.MANUAL, Provider.CREDIT)

    def _evident_buyer_id(self):
        """The buyer the row itself proves, or None when it proves nobody.

        The linked registration or membership names the member outright. With
        neither link, ``user`` is the buyer only where the creator cannot have
        been staff acting for someone else: a donation (self-service only) and
        the member-stamped providers. An unlinked SumUp seat or Premium row
        proves nothing -- its registration may be gone and ``user`` may be the
        staff opener -- so it stays unstamped and ``owner_id`` falls back to
        ``user`` exactly as before ``buyer`` existed. Migration 0279 applies
        this same rule to the rows that predate the field.
        """
        if self.event_registration_id:
            return self.event_registration.user_id
        if self.premium_membership_id:
            return self.premium_membership.user_id
        if (
            self.purpose == self.Purpose.DONATION
            or self.provider in self.MEMBER_STAMPED_PROVIDERS
        ):
            return self.user_id
        return None

    def _derived_owner_id(self):
        """The derivation that predates ``buyer``: links first, then ``user``."""
        if self.event_registration_id:
            return self.event_registration.user_id
        if self.premium_membership_id:
            return self.premium_membership.user_id
        return self.user_id

    def owner_id(self):
        """Who this payment is FOR -- not who happened to open the checkout.

        The single answer shared by the widget and return page
        (``views_payments._payment_owner_ids``), the receipt
        (``email_helpers._receipt_recipient``), the admin's Buyer column and
        the data export (``bought_by``). The account this names may since have
        been merged into another; ``AccountMerge.surviving_user_id`` follows
        that.
        """
        return self.buyer_id or self._derived_owner_id()

    def owner(self):
        """``owner_id()`` as a User, reusing whichever relation is loaded."""
        if self.buyer_id:
            return self.buyer
        if self.event_registration_id:
            return self.event_registration.user
        if self.premium_membership_id:
            return self.premium_membership.user
        return self.user

    def save(self, *args, **kwargs):
        """Stamp the buyer, event and paid transition, incl. update_fields saves.

        ``buyer`` is stamped on the first save that can prove it and never
        again. Normally that is the create; a row the old code created during
        a slot swap gets it on its next save (the capture, usually).
        """
        if self.buyer_id is None:
            buyer_id = self._evident_buyer_id()
            if buyer_id is not None:
                self.buyer_id = buyer_id
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = set(update_fields) | {"buyer"}
        if (
            self.event_id is None
            and self.event_registration_id
            and self.status in (self.Status.PAID, self.Status.REFUNDED)
        ):
            self.event_id = self.event_registration.event_id
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"event"}
        if self.status == self.Status.PAID and self.paid_at is None:
            self.paid_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"paid_at"}
        return super().save(*args, **kwargs)
