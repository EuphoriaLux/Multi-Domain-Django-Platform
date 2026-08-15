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
  Crush.lu cancels an event, Luxembourg consumer guidance entitles the member
  to their money back if they ask, and a voucher is not a substitute. The
  refund itself is still made by hand in the SumUp dashboard — this is how a
  human finds everyone who may ask.
"""

from decimal import ROUND_HALF_UP, Decimal

from django import forms
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
from crush_lu.services.credits import available_credit_cents, issue_credit, void_credit
from crush_lu.utils.formatting import format_cents

_STATUS_COLOURS = {
    CrushCredit.Status.ACTIVE: ("#28a745", "💳"),
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
    actions = ["void_credits"]

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
                    "Set only when Crush.lu cancelled the event. Those members "
                    "may ask for their money back instead of the credit, and "
                    "are entitled to it. Refund by hand in the SumUp dashboard, "
                    "then void this credit."
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


@admin.action(description="💳 Issue Crush Credit (goodwill)", permissions=["change"])
def issue_goodwill_credit(modeladmin, request, queryset):
    """Give the selected members credit, with a reason on the record.

    An intermediate page rather than a bare action, because the two things
    worth capturing — how much, and why — cannot be expressed by selecting rows.

    Goes through ``issue_credit`` like everything else. Goodwill credit is
    deliberately NOT linked to a registration: linking it would make it look
    like a refund of that seat, and ``issue_credit`` releases
    ``payment_confirmed`` for exactly those reasons. Goodwill is additional
    money, so the member keeps whatever seat they have already paid for.
    """
    if not modeladmin.has_change_permission(request):
        raise PermissionDenied

    users = list(queryset)
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
            "queryset": queryset,
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
