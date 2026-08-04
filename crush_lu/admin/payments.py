"""Payment transaction admin for the Crush.lu Coach Panel.

``PaymentTransaction`` went live without an admin at all, which meant the
platform kept a complete record of every SumUp checkout and offered no way to
look at it. When a production test payment came back "Échec" in SumUp's own
dashboard, the only trace on our side was a row nobody could reach: not the
amount, not the member, not the reason. This is read-only on purpose — money
is recorded here, not edited here — with one action that re-asks SumUp.
"""

import json
import logging

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from crush_lu.models.payments import PaymentTransaction

logger = logging.getLogger(__name__)

_STATUS_COLOURS = {
    PaymentTransaction.Status.PAID: ("#28a745", "✅"),
    PaymentTransaction.Status.PENDING: ("#17a2b8", "🔄"),
    PaymentTransaction.Status.FAILED: ("#dc3545", "❌"),
    PaymentTransaction.Status.CANCELLED: ("#6c757d", "⚫"),
    PaymentTransaction.Status.REFUNDED: ("#fd7e14", "↩️"),
}


class PaymentTransactionAdmin(admin.ModelAdmin):
    """Read-only view of every checkout we have ever opened at SumUp."""

    list_display = (
        "transaction_reference",
        "get_status_badge",
        "amount_display",
        "purpose",
        "get_buyer",
        "get_bought",
        "short_failure_reason",
        "created_at",
    )
    list_filter = ("status", "purpose", "provider", "created_at")
    search_fields = (
        "transaction_reference",
        "sumup_checkout_id",
        "sumup_customer_id",
        "user__email",
        "user__username",
        "failure_reason",
    )
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    actions = ["recheck_with_sumup"]

    readonly_fields = (
        "transaction_reference",
        "provider",
        "sumup_checkout_id",
        "sumup_customer_id",
        "amount",
        "currency",
        "status",
        "purpose",
        "user",
        "event_registration",
        "premium_membership",
        "failure_reason",
        "created_at",
        "updated_at",
        "get_raw_response_formatted",
    )

    fieldsets = (
        (
            "Payment",
            {
                "fields": (
                    "transaction_reference",
                    "status",
                    "amount",
                    "currency",
                    "purpose",
                    "provider",
                ),
            },
        ),
        (
            "Why it did not complete",
            {
                "fields": ("failure_reason",),
                "description": (
                    "SumUp's own account of the attempt where it gives one. "
                    "Blank on a payment that went through, and on a checkout "
                    "nobody has tried to pay yet. Use the "
                    "&ldquo;Re-check with SumUp&rdquo; action to refresh it."
                ),
            },
        ),
        (
            "Who and what",
            {
                "fields": ("user", "event_registration", "premium_membership"),
            },
        ),
        (
            "SumUp identifiers",
            {
                "fields": ("sumup_checkout_id", "sumup_customer_id"),
                "classes": ("collapse",),
            },
        ),
        (
            "Raw provider response",
            {
                "fields": ("get_raw_response_formatted",),
                "classes": ("collapse",),
                "description": (
                    "Exactly what SumUp last returned for this checkout, "
                    "including the transactions array."
                ),
            },
        ),
        (
            "Timing",
            {"fields": ("created_at", "updated_at")},
        ),
    )

    @admin.display(description="Status", ordering="status")
    def get_status_badge(self, obj):
        colour, icon = _STATUS_COLOURS.get(obj.status, ("#6c757d", "•"))
        return format_html(
            '<span style="background: {}; color: white; padding: 3px 8px; '
            'border-radius: 12px; font-size: 11px; white-space: nowrap;">{} {}</span>',
            colour,
            icon,
            obj.get_status_display(),
        )

    @admin.display(description="Amount", ordering="amount")
    def amount_display(self, obj):
        return f"{obj.amount} {obj.currency}"

    @admin.display(description="Buyer")
    def get_buyer(self, obj):
        """Who the payment is FOR, not who opened the row.

        Staff can start a checkout on a member's behalf, and that stamps the
        staff account on ``user`` — the linked registration or membership is the
        authority, exactly as ``_payment_owner_ids`` treats it in the views.
        """
        if obj.event_registration_id:
            return obj.event_registration.user.email
        if obj.premium_membership_id:
            return obj.premium_membership.user.email
        return obj.user.email if obj.user_id else "—"

    @admin.display(description="For")
    def get_bought(self, obj):
        if obj.event_registration_id:
            return f"Event: {obj.event_registration.event.title}"
        if obj.premium_membership_id:
            return f"Premium #{obj.premium_membership_id}"
        return "—"

    @admin.display(description="Reason")
    def short_failure_reason(self, obj):
        if not obj.failure_reason:
            return mark_safe('<span style="color: #999;">—</span>')
        text = obj.failure_reason
        shown = text if len(text) <= 60 else text[:57] + "…"
        return format_html('<span title="{}">{}</span>', text, shown)

    @admin.display(description="Raw SumUp response")
    def get_raw_response_formatted(self, obj):
        try:
            formatted = json.dumps(obj.raw_response, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            formatted = str(obj.raw_response)
        return format_html(
            '<pre style="background: #f8f9fa; padding: 10px; border-radius: 4px; '
            'overflow-x: auto; max-width: 900px; font-size: 12px;">{}</pre>',
            formatted,
        )

    @admin.action(description="Re-check with SumUp")
    def recheck_with_sumup(self, request, queryset):
        """Ask SumUp what actually happened to the selected checkouts.

        Goes through the same ``_sync_checkout_with_sumup`` the webhook and the
        return page use, so a payment that turns out to have succeeded is
        applied here too — seat confirmed, ticket issued, confirmation sent —
        rather than merely reported. That is the point: a checkout can be paid
        at SumUp while our row is stuck PENDING because the customer closed the
        tab and no callback ever arrived, and this is the manual repair.
        """
        from crush_lu.views_payments import _sync_checkout_with_sumup

        checked = failed = 0
        for tx_obj in queryset:
            if not tx_obj.sumup_checkout_id:
                continue
            before = tx_obj.status
            try:
                _sync_checkout_with_sumup(tx_obj)
            except Exception as exc:  # provider call — never 500 the admin
                failed += 1
                logger.error(
                    "Admin re-check failed for %s: %s",
                    tx_obj.transaction_reference,
                    exc,
                    exc_info=True,
                )
                self.message_user(
                    request,
                    f"{tx_obj.transaction_reference}: could not reach SumUp ({exc}).",
                    level=messages.ERROR,
                )
                continue

            checked += 1
            tx_obj.refresh_from_db()
            detail = tx_obj.failure_reason or "no further detail from SumUp"
            if tx_obj.status != before:
                self.message_user(
                    request,
                    f"{tx_obj.transaction_reference}: {before} → "
                    f"{tx_obj.status}. {detail}",
                    level=messages.WARNING,
                )
            else:
                self.message_user(
                    request,
                    f"{tx_obj.transaction_reference}: still {tx_obj.status}. {detail}",
                    level=messages.INFO,
                )

        if checked and not failed:
            self.message_user(
                request, f"Re-checked {checked} transaction(s) with SumUp."
            )

    def has_add_permission(self, request):
        # Payments are created by the checkout flow. A hand-made row would carry
        # no checkout at SumUp and could confirm a seat nobody paid for.
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # This is the record of money moving. Deleting one loses the only
        # evidence that a member was charged.
        return False
