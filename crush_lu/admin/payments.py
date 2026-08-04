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
import time

from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from crush_lu.models.payments import PaymentTransaction

logger = logging.getLogger(__name__)

# The re-check action calls SumUp inline, one row at a time, with a 10s client
# timeout — production has no task worker to hand it to (see AGENTS.md), so the
# work has to be bounded in the request the way the PassKit bulk push is. Both
# caps apply; whichever is reached first stops the run, and the action reports
# what it did not get to.
#
# Read per call, not at import, so override_settings actually reaches them —
# same as passkit_service does with PASSKIT_BULK_PUSH_LIMIT.
DEFAULT_RECHECK_LIMIT = 20
DEFAULT_RECHECK_BUDGET_SECONDS = 60.0

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
    # Searchable by whoever the Buyer column actually shows. ``user`` is the
    # account that *opened* the checkout, which for a staff-opened one is the
    # staff member — so searching the email printed in the Buyer column found
    # nothing at all for exactly the payments most likely to need looking up.
    search_fields = (
        "transaction_reference",
        "sumup_checkout_id",
        "sumup_customer_id",
        "user__email",
        "user__username",
        "event_registration__user__email",
        "event_registration__user__username",
        "premium_membership__user__email",
        "premium_membership__user__username",
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

    def get_queryset(self, request):
        """Pull in what the display methods dereference.

        ``get_buyer`` and ``get_bought`` walk to the registration's user and
        event, and to the membership's user. Django cannot infer those from
        ``list_display`` because they are reached inside methods, so a full page
        of rows was several hundred extra queries.
        """
        return (
            super()
            .get_queryset(request)
            .select_related(
                "user",
                "event_registration__user",
                "event_registration__event",
                "premium_membership__user",
            )
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

        A PENDING row goes through the same ``_sync_checkout_with_sumup`` the
        webhook and the return page use, so a payment that turns out to have
        succeeded is *applied* here — seat confirmed, ticket issued,
        confirmation sent — rather than merely reported. That is the point: a
        checkout can be paid at SumUp while our row is stuck PENDING because the
        customer closed the tab and no callback ever arrived, and this is the
        manual repair.

        A settled row takes a different path. ``_sync_checkout_with_sumup``
        returns immediately for anything non-PENDING — that early return is what
        stops a terminal payment being re-applied — so running it on a FAILED
        row did nothing at all while this action cheerfully reported it had
        checked. ``refresh_sumup_snapshot`` fetches the detail without touching
        status, which is also the only way rows that failed before
        ``failure_reason`` existed can ever be given one.

        Bounded, because there is no task worker: this runs inline in the admin
        request, one 10-second-timeout call at a time, under a gunicorn timeout.
        Unbounded, a coach selecting a screenful of unreachable checkouts would
        have the request killed mid-way, after applying some of the payments and
        not others.
        """
        from crush_lu.views_payments import (
            _sync_checkout_with_sumup,
            refresh_sumup_snapshot,
        )

        limit = getattr(settings, "SUMUP_ADMIN_RECHECK_LIMIT", DEFAULT_RECHECK_LIMIT)
        budget = getattr(
            settings,
            "SUMUP_ADMIN_RECHECK_BUDGET_SECONDS",
            DEFAULT_RECHECK_BUDGET_SECONDS,
        )
        deadline = time.monotonic() + budget
        checked = failed = 0
        skipped = []

        for tx_obj in queryset:
            if not tx_obj.sumup_checkout_id:
                continue
            if checked + failed >= limit or time.monotonic() > deadline:
                skipped.append(tx_obj.transaction_reference)
                continue

            before = tx_obj.status
            settled = before != PaymentTransaction.Status.PENDING
            try:
                if settled:
                    reported = refresh_sumup_snapshot(tx_obj)
                else:
                    reported = _sync_checkout_with_sumup(tx_obj)
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

            if not reported:
                # Both helpers swallow SumUpError and return quietly, so the
                # except above never fires on an outage — and this action was
                # counting those as checked and finishing with "Re-checked N
                # transaction(s) with SumUp". During a SumUp outage a coach
                # would be told every row was verified when not one had been
                # asked about, which is worse than an error: it is a false all
                # clear on money.
                failed += 1
                self.message_user(
                    request,
                    f"{tx_obj.transaction_reference}: no answer from SumUp — "
                    "NOT checked, nothing recorded.",
                    level=messages.ERROR,
                )
                continue

            checked += 1
            tx_obj.refresh_from_db()
            detail = tx_obj.failure_reason or "no further detail from SumUp"

            if settled:
                # Deliberately does not act on a mismatch — it says so instead.
                # A checkout SumUp calls PAID under a row we WROTE OFF is a real
                # discrepancy (money taken, nothing delivered), and confirming a
                # seat off the back of an admin refresh is a human's decision.
                #
                # "Settled" is not "written off", though: a PAID row is settled
                # too, and SumUp agreeing that it was paid is the single most
                # ordinary thing this action can find. Flagging that as a
                # financial anomaly would train coaches to ignore the warning
                # that matters.
                if (
                    reported in ("PAID", "SUCCESSFUL")
                    and tx_obj.status != PaymentTransaction.Status.PAID
                ):
                    self.message_user(
                        request,
                        f"{tx_obj.transaction_reference}: recorded as "
                        f"{tx_obj.status} here but SumUp reports {reported} — "
                        "check whether this member was charged.",
                        level=messages.ERROR,
                    )
                else:
                    self.message_user(
                        request,
                        f"{tx_obj.transaction_reference}: {tx_obj.status}, "
                        f"refreshed from SumUp. {detail}",
                        level=messages.INFO,
                    )
            elif tx_obj.status != before:
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

        if skipped:
            # Never let a cap look like completion.
            self.message_user(
                request,
                f"Stopped after {checked + failed} transaction(s) — "
                f"{len(skipped)} not checked: {', '.join(skipped[:5])}"
                f"{'…' if len(skipped) > 5 else ''}. Re-run the action on those.",
                level=messages.WARNING,
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
