"""Crush Credit admin — the ledger, and the two things staff actually do.

Read-only, like ``PaymentTransactionAdmin`` and for the same reason: this is a
record of value owed to members, not a form. Credit is issued through
``crush_lu.services.credits.issue_credit`` and spent at checkout; an editable
``amount_cents`` would be a balance nobody could reconstruct.

Two staff surfaces beyond the ledger itself:

* **Goodwill credit**, an action on the member list with a mandatory reason —
  because "why does this member have €20" is the question the ledger exists to
  answer, and a blank note makes it unanswerable a month later.
* **The cash-refund queue**, a filter over ``cash_refund_eligible``. When
  Crush.lu cancels an event or cannot provide the certified curated group it
  sold, the member may ask for their money back and a voucher is not a
  substitute. Staff can send the refund at SumUp and withdraw the credit via
  ``refund_via_sumup`` — the row still has to be found and selected by a
  human, and the action never runs anywhere unattended.
"""

import logging
import time
from decimal import ROUND_HALF_UP, Decimal

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from crush_lu.models.credits import CreditRedemption, CrushCredit
from crush_lu.services.credits import (
    available_credit_cents,
    finalize_cash_refund,
    issue_credit,
    lease_cash_refund,
    payment_amount_cents,
    restore_cash_refund_lease,
    void_credit,
)
from crush_lu.services.sumup import (
    SumUpClient,
    SumUpError,
    extract_successful_transaction_id,
)
from crush_lu.utils.formatting import format_cents

logger = logging.getLogger(__name__)

# Same bound as PaymentTransactionAdmin.recheck_with_sumup, and for the same
# reason: no task worker, so this runs inline in the admin request, one
# 10s-timeout call at a time, under a gunicorn timeout. Read per call, not at
# import, so override_settings actually reaches them.
DEFAULT_REFUND_LIMIT = 20
DEFAULT_REFUND_BUDGET_SECONDS = 60.0

_STATUS_COLOURS = {
    CrushCredit.Status.ACTIVE: ("#28a745", "💳"),
    CrushCredit.Status.REFUNDING: ("#0d6efd", "🔒"),
    CrushCredit.Status.CONSUMED: ("#6c757d", "✅"),
    CrushCredit.Status.EXPIRED: ("#fd7e14", "⌛"),
    CrushCredit.Status.VOID: ("#dc3545", "🚫"),
}


class CashRefundQueueFilter(admin.SimpleListFilter):
    """Everyone who may still ask for their money back.

    Only ``event_cancelled`` credit carries the flag, and only while it is
    still active — once it has been spent or has expired the member has taken
    the credit and the cash question is closed.
    """

    title = "cash refund"
    parameter_name = "refund_queue"

    def lookups(self, request, model_admin):
        return [
            ("open", "May still ask for cash"),
            ("settled", "Cash no longer available"),
        ]

    def _open(self, queryset):
        """Credits whose holder can still be given cash instead.

        ``redemptions__isnull=True`` is the load-bearing clause. The standard
        event-cancellation credit is €20 against a €15.50 seat, so a member who
        has spent it on a replacement event still holds €4.50 and the row is
        still ``ACTIVE`` — without this it would keep reading "may still ask
        for cash", and staff following the queue would refund €15.50 in cash on
        top of €15.50 of credit the member had already used. Once any of a
        credit is spent, the member has taken the alternative and the cash
        question is closed.
        """
        # Mirrors ``CrushCredit.cash_refund_still_available`` exactly. The two
        # must agree: that property is what the member's data export reports,
        # and this is what staff work from. Them disagreeing means one of the
        # two parties is being told the wrong answer about money.
        return queryset.filter(
            cash_refund_eligible=True,
            status=CrushCredit.Status.ACTIVE,
            expires_at__gt=timezone.now(),
            redemptions__isnull=True,
        )

    def queryset(self, request, queryset):
        if self.value() == "open":
            return self._open(queryset)
        if self.value() == "settled":
            return queryset.filter(cash_refund_eligible=True).exclude(
                pk__in=self._open(queryset).values("pk")
            )
        return queryset


class CreditRedemptionInline(admin.TabularInline):
    """Where this credit went. Read-only — spending happens at checkout."""

    model = CreditRedemption
    extra = 0
    can_delete = False
    fields = ("event_registration", "amount_cents", "redeemed_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class CrushCreditAdmin(admin.ModelAdmin):
    """The ledger. Issued, redeemed, what is left, and when it dies."""

    list_display = (
        "issued_at",
        "get_member",
        "amount_display",
        "redeemed_display",
        "remaining_display",
        "get_status_badge",
        "reason",
        "expires_at",
        "get_member_balance",
        "get_cash_refund_flag",
    )
    list_filter = (
        CashRefundQueueFilter,
        "status",
        "reason",
        "issued_at",
        "expires_at",
    )
    search_fields = (
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
        "note",
    )
    date_hierarchy = "issued_at"
    ordering = ["-issued_at"]
    inlines = [CreditRedemptionInline]
    actions = ["void_credits", "refund_via_sumup"]

    readonly_fields = (
        "user",
        "amount_cents",
        "currency",
        "issued_at",
        "expires_at",
        "reason",
        "source_registration",
        "source_payment",
        "restored_from_credit",
        "status",
        "cash_refund_eligible",
        "note",
        "get_member_balance",
    )

    fieldsets = (
        (
            "Credit",
            {
                "fields": (
                    "user",
                    "amount_cents",
                    "currency",
                    "status",
                    "reason",
                    "get_member_balance",
                ),
                "description": (
                    "Amounts are in <strong>cents</strong>. There is no stored "
                    "balance anywhere — it is always issued minus redeemed, "
                    "computed on read."
                ),
            },
        ),
        (
            "Validity",
            {"fields": ("issued_at", "expires_at")},
        ),
        (
            "Cash refund",
            {
                "fields": ("cash_refund_eligible",),
                "description": (
                    "Set only when Crush.lu cancelled the event or could not "
                    "provide the certified curated group sold. Those members "
                    "may ask for their money back instead of the credit. Select "
                    "eligible rows on the changelist "
                    "and use the &ldquo;Refund via SumUp&rdquo; action to send "
                    "the money and void the credit in one step, or refund by "
                    "hand in the SumUp dashboard and use &ldquo;Void selected "
                    "credits&rdquo; instead."
                ),
            },
        ),
        (
            "Where it came from",
            {
                "fields": (
                    "source_registration",
                    "source_payment",
                    "restored_from_credit",
                    "note",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .select_related("user", "source_registration__event", "source_payment")
            .annotate(
                _redeemed=Coalesce(Sum("redemptions__amount_cents"), Value(0)),
            )
        )
        return annotate_credit_balance(queryset, user_path="user_id")

    @admin.display(description="Member", ordering="user__email")
    def get_member(self, obj):
        return obj.user.email

    @admin.display(description="Issued", ordering="amount_cents")
    def amount_display(self, obj):
        return format_cents(obj.amount_cents, obj.currency)

    @admin.display(description="Redeemed")
    def redeemed_display(self, obj):
        redeemed = getattr(obj, "_redeemed", None)
        if redeemed is None:
            redeemed = obj.redeemed_cents
        return format_cents(redeemed)

    @admin.display(description="Left")
    def remaining_display(self, obj):
        redeemed = getattr(obj, "_redeemed", None)
        if redeemed is None:
            redeemed = obj.redeemed_cents
        return format_cents(max(0, obj.amount_cents - redeemed))

    @admin.display(description="Status", ordering="status")
    def get_status_badge(self, obj):
        colour, icon = _STATUS_COLOURS.get(obj.status, ("#6c757d", "•"))
        # A credit past its expiry that the sweep (PR 2) has not stamped yet is
        # still unspendable. Saying "Active" here would have a coach promise a
        # member money the checkout will refuse.
        label = obj.get_status_display()
        if obj.status == CrushCredit.Status.ACTIVE and obj.expires_at <= timezone.now():
            colour, icon, label = "#fd7e14", "⌛", "Expired (unswept)"
        return format_html(
            '<span style="background: {}; color: white; padding: 3px 8px; '
            'border-radius: 12px; font-size: 11px; white-space: nowrap;">{} {}</span>',
            colour,
            icon,
            label,
        )

    @admin.display(description="Member balance")
    def get_member_balance(self, obj):
        """This member's whole spendable balance, not just this row."""
        issued = getattr(obj, "_credit_issued", None)
        if issued is None:
            cents = available_credit_cents(obj.user)
        else:
            cents = max(0, issued - getattr(obj, "_credit_redeemed", 0))
        return format_cents(cents, "EUR")

    @admin.display(description="Cash?", boolean=True)
    def get_cash_refund_flag(self, obj):
        return obj.cash_refund_eligible

    @admin.action(
        description="🚫 Void selected credits (after a cash refund)",
        permissions=["void"],
    )
    def void_credits(self, request, queryset):
        """Withdraw credit that has been settled in cash instead.

        The "Cash refund" fieldset tells staff to refund in the SumUp dashboard
        and then void the credit — and until this existed there was no way to
        do the second half, so the member kept a spendable balance on top of
        their money back. That is the credit-plus-cash double benefit the queue
        is meant to prevent, left open by the very screen that describes the
        workflow.

        Only ``active`` credits are voided, and only the unspent part is at
        stake: a credit already partly redeemed has value that has left the
        ledger and cannot be taken back by a status flip, so those are refused
        outright rather than half-withdrawn. Redemptions are never deleted.

        ⚠️ Each credit is locked before it is inspected, and the check and the
        void happen under that one lock. Staff run this action *after* sending
        the cash refund, so a checkout redeeming the credit between an unlocked
        "has it been spent?" read and the save would leave the member holding
        the refunded cash AND a seat they bought with credit that was voided
        out from under the ledger a moment later. ``CrushCredit`` is the last
        lock taken anywhere (see ``services/credits``), so taking it here on
        its own cannot close a cycle.
        """
        if not self.has_void_permission(request):
            raise PermissionDenied

        voided = spent = skipped = 0
        for pk in list(queryset.values_list("pk", flat=True)):
            _credit, outcome = void_credit(
                pk,
                note=(
                    f"— voided by {request.user.email or request.user}: "
                    "settled in cash instead."
                ),
                mark_source_payment_refunded=True,
            )
            if outcome == "spent":
                spent += 1
            elif outcome == "inactive":
                skipped += 1
            else:
                voided += 1

        if voided:
            self.message_user(
                request, f"Voided {voided} credit(s).", level=messages.SUCCESS
            )
        if spent:
            self.message_user(
                request,
                f"{spent} credit(s) have already been partly or fully spent and "
                "were NOT voided — that value has left the ledger. Reconcile "
                "those by hand before refunding any cash.",
                level=messages.ERROR,
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} credit(s) were not active and were left alone.",
                level=messages.WARNING,
            )

    @admin.action(
        description="💶 Refund via SumUp (sends real money — staff click only)",
        permissions=["refund"],
    )
    def refund_via_sumup(self, request, queryset):
        """Actually send the cash back at SumUp, then withdraw the credit.

        This is the automated half of the workflow the "Cash refund"
        fieldset describes: until now a coach had to leave the admin, refund
        by hand in the SumUp dashboard, come back, and run "Void selected
        credits" separately — two systems, two steps, easy to do one and
        forget the other. This action does both in one click, for exactly
        the same rows ``void_credits`` would let through and no others.

        **Never automated.** There is no scheduled job, signal, or
        management command anywhere in this codebase that calls
        ``SumUpClient.refund_transaction`` — this admin action, requiring an
        explicit row selection, the "Go" button, and the
        ``refund_crushcredit`` permission, is its only caller. See that
        method's docstring for why.

        Eligibility is intersected against
        ``CashRefundQueueFilter._open()`` — the exact same predicate that
        decides what the "May still ask for cash" filter shows — so a
        credit that has been spent, voided, expired, or was never
        cash-refund-eligible in the first place is silently skipped rather
        than refunded. The refund amount is always the **payment's**
        captured amount, never the credit's face value: the §4.3 premium
        issues €20.00 credit against a €15.50 payment, and refunding €20.00
        in cash on top of that would hand back more than was ever taken.

        Before provider I/O the action commits a ``REFUNDING`` lease under the
        payment -> credit lock order. A checkout cannot spend that leased value.
        A definitive pre-refund failure restores ``ACTIVE``; any exception from
        the money-moving POST remains ``REFUNDING`` because the provider may
        have accepted it before its response was lost.

        Bounded like ``PaymentTransactionAdmin.recheck_with_sumup`` — no
        task worker, so this calls SumUp inline, one row at a time, under a
        gunicorn timeout.
        """
        if not self.has_refund_permission(request):
            raise PermissionDenied

        eligible_pks = set(
            CashRefundQueueFilter(None, {}, CrushCredit, self)
            ._open(CrushCredit.objects.all())
            .values_list("pk", flat=True)
        )

        limit = getattr(settings, "SUMUP_ADMIN_REFUND_LIMIT", DEFAULT_REFUND_LIMIT)
        budget = getattr(
            settings, "SUMUP_ADMIN_REFUND_BUDGET_SECONDS", DEFAULT_REFUND_BUDGET_SECONDS
        )
        deadline = time.monotonic() + budget

        refunded = failed = skipped = 0
        stopped_early = []

        for selected_credit in queryset.only("pk").order_by("pk"):
            credit_id = selected_credit.pk
            if credit_id not in eligible_pks:
                skipped += 1
                continue

            if refunded + failed >= limit or time.monotonic() > deadline:
                stopped_early.append(credit_id)
                continue

            try:
                credit, payment, lease_outcome = lease_cash_refund(credit_id)
            except Exception as exc:  # noqa: BLE001 — report one row, keep batch moving
                failed += 1
                logger.error(
                    "Could not lease credit %s for a SumUp refund: %s",
                    credit_id,
                    exc,
                    exc_info=True,
                )
                self.message_user(
                    request,
                    f"Credit #{credit_id}: refund lease FAILED — {exc}. No "
                    "provider call was made.",
                    level=messages.ERROR,
                )
                continue

            if lease_outcome != "leased":
                skipped += 1
                self.message_user(
                    request,
                    f"Credit #{credit_id}: not refundable now "
                    f"(outcome={lease_outcome}) — skipped.",
                    level=messages.WARNING,
                )
                continue

            # Reading the checkout and locating its successful transaction are
            # definitive pre-refund steps: if either fails, no refund POST was
            # attempted and the lease can safely become spendable again.
            try:
                client = SumUpClient()
                checkout_data = client.get_checkout(payment.sumup_checkout_id)
                transaction_id = extract_successful_transaction_id(checkout_data)
                if not transaction_id:
                    raise SumUpError("no successful transaction found on this checkout")
            except Exception as exc:  # noqa: BLE001 — no refund POST happened
                failed += 1
                try:
                    _credit, restore_outcome = restore_cash_refund_lease(
                        credit_id, payment.pk
                    )
                except Exception as restore_exc:  # noqa: BLE001
                    restore_outcome = f"restore_failed: {restore_exc}"
                    logger.error(
                        "Credit %s pre-refund check failed and its lease could "
                        "not be restored: %s",
                        credit_id,
                        restore_exc,
                        exc_info=True,
                    )
                logger.error(
                    "SumUp refund preflight failed for credit %s (payment %s): %s",
                    credit_id,
                    payment.pk,
                    exc,
                    exc_info=True,
                )
                self.message_user(
                    request,
                    f"Credit #{credit_id}: refund preflight FAILED — {exc}. No "
                    f"refund request was sent; lease outcome={restore_outcome}.",
                    level=messages.ERROR,
                )
                continue

            # This is the point of no safe automatic rollback. A timeout or
            # other exception can arrive after SumUp accepted the POST, so any
            # failure below leaves the durable REFUNDING lease in place.
            try:
                # Full refund of what was actually captured — never the
                # credit's face value. Omitting `amount` is a full refund.
                client.refund_transaction(transaction_id)
            except Exception as exc:  # noqa: BLE001 — provider outcome is ambiguous
                failed += 1
                logger.error(
                    "SumUp refund outcome is ambiguous for credit %s "
                    "(payment %s, txn %s): %s",
                    credit_id,
                    payment.pk,
                    transaction_id,
                    exc,
                    exc_info=True,
                )
                self.message_user(
                    request,
                    f"Credit #{credit_id}: SumUp refund outcome is UNKNOWN "
                    f"(txn {transaction_id}) — the credit remains locked as "
                    "REFUNDING. Do not retry the refund; reconcile it in SumUp.",
                    level=messages.ERROR,
                )
                continue

            # The money has now genuinely left the merchant account. From here
            # on a failure must never look like "nothing happened" — wrap the
            # local write separately so a DB hiccup still reaches staff with
            # the one message that matters: reconcile by hand, the refund
            # already went through.
            try:
                _credit, _payment, outcome = finalize_cash_refund(
                    credit_id,
                    payment.pk,
                    note=(
                        f"— refunded "
                        f"{format_cents(payment_amount_cents(payment), payment.currency)} "
                        f"via SumUp (txn {transaction_id}) by "
                        f"{request.user.email or request.user}."
                    ),
                )
            except Exception as exc:  # noqa: BLE001 — see comment above
                failed += 1
                logger.error(
                    "Credit %s: SumUp refund (txn %s) succeeded but finalizing "
                    "the local lease raised: %s",
                    credit_id,
                    transaction_id,
                    exc,
                    exc_info=True,
                )
                self.message_user(
                    request,
                    f"Credit #{credit_id}: SumUp refund SUCCEEDED (txn "
                    f"{transaction_id}) but recording it here raised an error "
                    f"({exc}) — the member has already been refunded in cash; "
                    "the credit remains REFUNDING; reconcile by hand now.",
                    level=messages.ERROR,
                )
                continue

            if outcome == "finalized":
                refunded += 1
                self.message_user(
                    request,
                    f"Credit #{credit_id}: refunded via SumUp and voided.",
                    level=messages.SUCCESS,
                )
            else:
                failed += 1
                # The money is already gone at SumUp — this is not a state to
                # retry, it is a state to reconcile by hand immediately.
                self.message_user(
                    request,
                    f"Credit #{credit_id}: SumUp refund SUCCEEDED (txn "
                    f"{transaction_id}) but the credit could not be voided "
                    f"automatically (outcome={outcome}) — the member has "
                    "already been refunded in cash; leave REFUNDING in place "
                    "and reconcile the ledger by hand now.",
                    level=messages.ERROR,
                )

        if skipped:
            self.message_user(
                request,
                f"{skipped} selected row(s) were not eligible (not in the open "
                "cash-refund queue, or carry no refundable SumUp payment) and "
                "were left alone.",
                level=messages.WARNING,
            )
        if stopped_early:
            self.message_user(
                request,
                f"Stopped after the limit/time budget — {len(stopped_early)} "
                "eligible row(s) not attempted this run. Re-run the action on "
                "those.",
                level=messages.WARNING,
            )
        if refunded and not failed:
            self.message_user(
                request,
                f"Refunded {refunded} credit(s) via SumUp.",
                level=messages.SUCCESS,
            )

    def has_add_permission(self, request):
        # Issued through services.credits.issue_credit, which is also what
        # clears payment_confirmed on the seat being credited. A hand-made row
        # would hand out money and leave the member holding a free seat too.
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_void_permission(self, request):
        return request.user.is_superuser or request.user.has_perm(
            "crush_lu.void_crushcredit"
        )

    def has_refund_permission(self, request):
        # Deliberately separate from has_void_permission: this one calls
        # SumUp and moves a real euro, void only flips a local status flag.
        return request.user.is_superuser or request.user.has_perm(
            "crush_lu.refund_crushcredit"
        )

    def has_delete_permission(self, request, obj=None):
        # This is a record of value owed to a member. Deleting one loses the
        # only evidence that they are owed it.
        return False


class CreditRedemptionAdmin(admin.ModelAdmin):
    """Every spend, for reconciling a member's balance against their seats."""

    list_display = (
        "redeemed_at",
        "get_member",
        "amount_display",
        "credit",
        "event_registration",
    )
    list_filter = ("redeemed_at",)
    search_fields = (
        "credit__user__email",
        "credit__user__username",
        "event_registration__event__title",
    )
    date_hierarchy = "redeemed_at"
    ordering = ["-redeemed_at"]
    readonly_fields = ("credit", "event_registration", "amount_cents", "redeemed_at")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("credit__user", "event_registration__event")
        )

    @admin.display(description="Member")
    def get_member(self, obj):
        return obj.credit.user.email

    @admin.display(description="Amount", ordering="amount_cents")
    def amount_display(self, obj):
        return format_cents(obj.amount_cents, "EUR")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deleting a redemption hands the member their money back without
        # taking the seat back. Void the credit instead.
        return False


class GoodwillCreditForm(forms.Form):
    """Amount and reason for a hand-issued goodwill credit.

    ``reason`` is **required** and is not one of the ``Reason`` choices — those
    say what kind of event produced the credit, and every hand-issued one is
    ``goodwill`` by definition. This is the sentence that tells the next person
    why, and it is mandatory because a goodwill credit with no explanation is
    indistinguishable from a mistake.
    """

    amount_eur = forms.DecimalField(
        label="Amount (EUR)",
        min_value=0.01,
        max_value=500,
        decimal_places=2,
        help_text="Face value of the credit. Valid for 6 months from today.",
    )
    reason = forms.CharField(
        label="Why (required)",
        widget=forms.Textarea(attrs={"rows": 3}),
        min_length=10,
        help_text=(
            "What happened, in a sentence. This is the only record of why this "
            "member was given credit — it shows on the ledger."
        ),
    )


def may_issue_goodwill_credit(user):
    """Who may mint credit out of nothing.

    Mirrors :meth:`CrushCreditAdmin.has_void_permission`: superusers plus
    holders of the dedicated ``issue_crushcredit`` permission. Goodwill used to
    ride ``change_crushprofile`` — routine profile-review permission — which
    let any staff account able to read a member list issue up to €500 each.
    """
    return user.is_superuser or user.has_perm("crush_lu.issue_crushcredit")


class GoodwillCreditPermissionMixin:
    """Supplies the permission hook ``permissions=["issue_crushcredit"]`` needs.

    Django resolves an action's declared permissions by calling
    ``has_<perm>_permission(request)`` on the ModelAdmin with a bare
    ``getattr`` and no default, so every admin that registers
    :func:`issue_goodwill_credit` must define this method or its changelist
    raises AttributeError before it renders a single row. Mix this in
    alongside the action rather than repeating the method on each admin.
    """

    def has_issue_crushcredit_permission(self, request):
        return may_issue_goodwill_credit(request.user)


@admin.action(
    description="💳 Issue Crush Credit (goodwill)",
    permissions=["issue_crushcredit"],
)
def issue_goodwill_credit(modeladmin, request, queryset):
    """Give the selected members credit, with a reason on the record.

    An intermediate page rather than a bare action, because the two things
    worth capturing — how much, and why — cannot be expressed by selecting rows.

    Goes through ``issue_credit`` like everything else. Goodwill credit is
    deliberately NOT linked to a registration: linking it would make it look
    like a refund of that seat, and ``issue_credit`` releases
    ``payment_confirmed`` for exactly those reasons. Goodwill is additional
    money, so the member keeps whatever seat they have already paid for.

    Gated on the dedicated ``issue_crushcredit`` permission rather than the
    admin's change permission: this action mints spendable money, and it is
    exposed on admins (the member list, the hidden user list) whose change
    permission is handed out for profile review. The decorator's
    ``permissions`` hides the dropdown entry; this check enforces it on the
    POST itself, so a crafted action request cannot skip the first.
    """
    if not may_issue_goodwill_credit(request.user):
        raise PermissionDenied

    selected = list(queryset)
    # The action is exposed on both the hidden autocomplete-only User admin
    # and the visible CrushProfile member list. Keep the money workflow in one
    # function while mapping profile rows to their owning users.
    users = [getattr(row, "user", row) for row in selected]
    if "cancel" in request.POST:
        # Returning None sends the admin back to the changelist having done
        # nothing, which is what the Cancel button has to mean on a page whose
        # only other button moves money.
        return None
    if "apply" in request.POST:
        form = GoodwillCreditForm(request.POST)
        if form.is_valid():
            # Decimal all the way to cents, never through float. The field is
            # already a Decimal and the rest of this feature is careful to
            # quantize exactly; routing money through a binary float here for
            # no reason is the kind of inconsistency that is harmless until the
            # bounds change.
            amount_cents = int(
                (form.cleaned_data["amount_eur"] * 100).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
            note = form.cleaned_data["reason"].strip()
            issued = 0
            # One transaction for the whole selection. Each issue_credit opens
            # its own atomic block, so without this a failure part-way through
            # — a member deleted concurrently, anything — would leave the
            # earlier members credited while the action reported an error and
            # named nobody. The obvious response to that error is to re-run the
            # same selection, which would pay those earlier members twice.
            # All-or-nothing makes the retry safe, which is the property that
            # actually matters here.
            with transaction.atomic():
                for user in users:
                    credit = issue_credit(
                        user,
                        amount_cents,
                        CrushCredit.Reason.GOODWILL,
                        note=(
                            f"{note}\n— issued by "
                            f"{request.user.email or request.user}"
                        ),
                    )
                    if credit is not None:
                        issued += 1
            modeladmin.message_user(
                request,
                f"Issued {format_cents(amount_cents, 'EUR')} of Crush Credit to "
                f"{issued} member(s).",
                level=messages.SUCCESS,
            )
            return None
    else:
        form = GoodwillCreditForm()

    return render(
        request,
        "admin/crush_lu/issue_goodwill_credit.html",
        {
            "title": "Issue Crush Credit",
            "users": users,
            "form": form,
            "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
            # Preserve the selected model rows for Django's action checkbox
            # round-trip. On CrushProfileAdmin these ids are profile ids even
            # though the displayed/credited objects above are users.
            "queryset": selected,
            "opts": modeladmin.model._meta,
            "media": modeladmin.media + form.media,
        },
    )


def annotate_credit_balance(queryset, user_path="pk"):
    """Annotate a User queryset with ``_credit_cents``, the spendable balance.

    One query for the whole page instead of one per row. Rendering the balance
    in a ``list_display`` column called ``available_credit_cents`` per object,
    which is what this replaced, is exactly the per-row query
    ``PaymentTransactionAdmin.get_queryset`` documents itself avoiding.

    Two subqueries rather than a join-and-group: annotating a sum over credits
    and a sum over their redemptions in one queryset multiplies the first by
    the row count of the second, and a member with two redemptions against one
    credit would be shown double the money they have.
    """
    now = timezone.now()
    active = CrushCredit.objects.filter(
        user=OuterRef(user_path),
        status=CrushCredit.Status.ACTIVE,
        expires_at__gt=now,
    )
    issued = (
        active.order_by()
        .values("user")
        .annotate(total=Sum("amount_cents"))
        .values("total")
    )
    redeemed = (
        CreditRedemption.objects.filter(
            credit__user=OuterRef(user_path),
            credit__status=CrushCredit.Status.ACTIVE,
            credit__expires_at__gt=now,
        )
        .order_by()
        .values("credit__user")
        .annotate(total=Sum("amount_cents"))
        .values("total")
    )
    return queryset.annotate(
        _credit_issued=Coalesce(
            Subquery(issued, output_field=IntegerField()), Value(0)
        ),
        _credit_redeemed=Coalesce(
            Subquery(redeemed, output_field=IntegerField()), Value(0)
        ),
    )


def credit_balance_column(obj):
    """Reusable ``list_display`` cell showing a member's spendable balance.

    Uses the annotation from :func:`annotate_credit_balance` when the admin
    added one, and falls back to the authoritative read otherwise so the cell
    is never silently wrong on a queryset nobody annotated.
    """
    issued = getattr(obj, "_credit_issued", None)
    if issued is None:
        cents = available_credit_cents(obj)
    else:
        cents = max(0, issued - getattr(obj, "_credit_redeemed", 0))
    if not cents:
        return mark_safe('<span style="color: #999;">—</span>')
    return format_html(
        '<strong style="color: #28a745;">{}</strong>', format_cents(cents, "EUR")
    )
