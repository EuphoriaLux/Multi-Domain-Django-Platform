"""Durable record of a captured Premium payment that was NOT applied (#925).

The OneToOne on payment makes opening a case idempotent across replays.
Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class PremiumPaymentRecoveryCase(models.Model):
    class Reason(models.TextChoices):
        DUPLICATE_CAPTURE = "duplicate_capture", _("Duplicate capture")
        COACH_UNAVAILABLE = "coach_unavailable", _("Coach unavailable")
        REQUEST_CANCELLED = "request_cancelled", _("Request cancelled")
        BETA_REVOKED = "beta_revoked", _("Beta access revoked")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        RESOLVED = "resolved", _("Resolved")

    class Resolution(models.TextChoices):
        # D2/D4: refunded in SumUp; the hourly sweep records it.
        REFUNDED = "refunded", _("Refunded")
        # D3: the member agreed to a coach and the capture was applied.
        APPLIED = "applied", _("Applied (member agreed)")
        OTHER = "other", _("Other")

    payment = models.OneToOneField(
        "crush_lu.PaymentTransaction",
        on_delete=models.PROTECT,
        related_name="premium_recovery_case",
    )
    # premium_membership.user, not whoever opened the checkout.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="premium_recovery_cases",
    )
    premium_membership = models.ForeignKey(
        "crush_lu.PremiumMembership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recovery_cases",
    )
    reason = models.CharField(max_length=32, choices=Reason.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    member_notified_at = models.DateTimeField(null=True, blank=True)
    staff_alerted_at = models.DateTimeField(null=True, blank=True)
    # Set once at creation (premium_recovery.open_case): only staff are told,
    # no member notice. Either the member is unknown (below), or a backfilled
    # capture's application cannot be proven (several captures on one
    # membership) -- that member is still gated from paying again.
    staff_only = models.BooleanField(default=False)
    # A legacy unlinked capture opened by staff: ``user`` is the opener, not
    # the member, so the case gates and closes nothing of ``user``. Stored,
    # not derived from is_staff, which can change later.
    member_unknown = models.BooleanField(default=False)
    # Set when the case is resolved (premium_recovery.resolve_case): how, when
    # and by whom. resolved_by stays empty for the sweep's refund records.
    resolution = models.CharField(max_length=16, choices=Resolution.choices, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Premium payment recovery case")
        verbose_name_plural = _("Premium payment recovery cases")

    def __str__(self):
        return f"{self.payment.transaction_reference} ({self.get_reason_display()})"
