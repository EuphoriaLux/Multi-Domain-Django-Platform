"""Atmos data model — trimmed for the live-clickable pilot slice.

Matches the power_up.crm/onboarding convention: UUID primary keys throughout
(`power_up/crm/models.py`), so a guessed/enumerated URL can't address another
table's data.

**Deliberately deferred from docs/specs/atmos-bar-ordering.md** to get a
vertical slice live quickly — noted here rather than silently dropped:

- `Settlement` (§4.9) — no payment/billing UI in this slice.
- The 2-hour guest window and its warning/expired states (§5) —
  `guest_window_minutes` is kept on `Venue` for later, but nothing enforces
  it yet.
- Rank escalation (§3.3, "Captain" -> "Supreme Overlord") — the alias
  system that actually got built (`power_up.atmos.lore.personas`) is a
  fixed noir catalog with no rank ladder, so `Guest.alias` is stable for
  the life of the guest record, no escalation field.
- `Order.idempotency_key` (§8.2) — double-submit protection matters for a
  real bar on real wifi; skipped for a single-operator demo click-through.

**What this model adds beyond the spec:** `Order.vignette` /
`Order.vignette_source`. The spec's data model (§4.8) has nowhere to store
the chronicle output at all — see docs/specs/atmos-bar-ordering.md for the
gap this closes.
"""

from __future__ import annotations

import secrets
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from power_up.storage import powerup_media_storage, powerup_upload_path

# Mirrors printing.layout.money()'s mapping — kept here too since templates
# can't easily call an imported function, and printing/ deliberately stays
# Django-free (no models import), so the dependency can't run the other way.
_CURRENCY_SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$"}


def currency_symbol(code: str) -> str:
    """Shared by Venue (live, pre-order display) and Order (the currency
    actually snapshotted at placement) so both render the same way — falls
    back to the bare code (with a trailing space) rather than an ambiguous
    plain number, same as printing.layout.money()."""
    return _CURRENCY_SYMBOLS.get(code, code + " ")


class Venue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    address = models.CharField(max_length=255, blank=True, default="")
    currency = models.CharField(max_length=3, default="EUR")
    service_open = models.BooleanField(
        default=True, help_text="Master switch — off means no ordering anywhere."
    )
    guest_window_minutes = models.PositiveSmallIntegerField(
        default=120, help_text="Reserved for the window mechanic (not yet enforced)."
    )
    # `Order.objects.filter(venue=venue).count() + 1` used to derive the
    # next order_number — but OrderAdmin allows deleting orders, and a
    # deleted row moves the count backwards, so a later order can reuse an
    # already-issued short_code. A monotonic counter, incremented under the
    # same venue-row lock _create_order_atomic already holds, can't go
    # backwards regardless of what gets deleted afterward.
    next_order_number = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def currency_symbol(self) -> str:
        """Guest-facing menu/cart templates hardcoded '€' until this existed
        — the ticket already went through printing.layout.money(), so a
        non-EUR venue would show one currency while ordering and get a
        different one on the printed ticket."""
        return currency_symbol(self.currency)


class Table(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    venue = models.ForeignKey(Venue, related_name="tables", on_delete=models.CASCADE)
    label = models.CharField(max_length=32)
    qr_token = models.CharField(max_length=22, unique=True, db_index=True)
    seats = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["venue", "label"], name="uniq_table_label_per_venue"
            ),
        ]
        ordering = ["label"]

    def __str__(self) -> str:
        return f"{self.venue.name} — Table {self.label}"

    def clean(self):
        super().clean()
        # The KDS renders `order.tab.table.label` LIVE, while an already-
        # issued short_code and its chronicle event bake the placement-time
        # label directly into their text — the two would silently diverge
        # the moment a table with active history gets renamed (an order for
        # "4" starts showing under a "9" badge on the KDS, delivered to the
        # wrong table). Once a table has any tabs at all, its label is
        # frozen; deactivate and create a new table instead of renaming one
        # with history. Runs on both the standalone TableAdmin and the
        # VenueAdmin inline — full_clean() is called for each inline row.
        if self.pk is not None:
            original_label = (
                Table.objects.filter(pk=self.pk).values_list("label", flat=True).first()
            )
            if (
                original_label is not None
                and original_label != self.label
                and self.tabs.exists()
            ):
                raise ValidationError(
                    {
                        "label": (
                            "Cannot rename a table that already has tabs — the KDS, "
                            "issued short codes, and chronicle events all bake in the "
                            "old label. Deactivate this table and create a new one "
                            "instead."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        if not self.qr_token:
            # secrets.token_urlsafe(16) per spec §4.3 — the QR encodes this,
            # never the PK, so a leaked sticker can be rotated independently
            # of order history.
            self.qr_token = secrets.token_urlsafe(16)
        super().save(*args, **kwargs)


class MenuCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    venue = models.ForeignKey(
        Venue, related_name="menu_categories", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=120)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_visible = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "menu categories"

    def __str__(self) -> str:
        return self.name


class MenuItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(
        MenuCategory, related_name="items", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    # A negative price has no legitimate meaning here (it would let a guest
    # be *paid* to order, and corrupt total_amount/settlement records) —
    # enforced at both the admin-form level (validator) and the DB level
    # (CheckConstraint below), so a non-admin write path can't bypass it.
    price = models.DecimalField(
        max_digits=7, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    image = models.ImageField(
        upload_to=powerup_upload_path("atmos/menu"),
        storage=powerup_media_storage,
        blank=True,
        null=True,
    )
    is_available = models.BooleanField(default=True, help_text="The 86-button.")
    contains_alcohol = models.BooleanField(default=False)
    allergens = models.CharField(max_length=255, blank=True, default="")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0), name="menuitem_price_non_negative"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Tab(models.Model):
    STATUS_CHOICES = [("open", "Open"), ("closed", "Closed")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    table = models.ForeignKey(Table, related_name="tabs", on_delete=models.CASCADE)
    venue = models.ForeignKey(Venue, related_name="tabs", on_delete=models.CASCADE)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default="open")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["table"],
                condition=Q(status="open"),
                name="uniq_open_tab_per_table",
            ),
        ]

    def __str__(self) -> str:
        return f"Tab for {self.table} ({self.status})"

    def clean(self):
        super().clean()
        # `save()` below only ever handles the open->closed direction (it
        # stamps closed_at, settles guests). Nothing resets closed_at,
        # restores opened_at, or reactivates settled guests for the reverse
        # transition, so admin staff flipping a closed tab's status back to
        # "open" would leave a tab that's simultaneously "open" (accepting
        # new scans/guests/orders) and still carrying its old closed_at and
        # settled guest history — the next scan of this table would attach
        # a fresh service to what looks like a still-open previous one.
        # Reopening isn't a supported transition; scanning the table again
        # creates a genuinely fresh tab instead.
        if self.pk is not None:
            original_status = (
                Tab.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            )
            if original_status == "closed" and self.status == "open":
                raise ValidationError(
                    {
                        "status": (
                            "Cannot reopen a closed tab — scan the table again to "
                            "start a fresh one."
                        )
                    }
                )

    def save(self, *args, **kwargs):
        # `venue` is denormalised for staff queries (see Guest's comment on
        # the same tradeoff) — it must never be independently settable, or
        # the default admin lets staff pick a table at venue A and a venue
        # field pointing at B: a scan of A's QR reuses that tab and serves
        # B's menu, creating guests and orders under the wrong venue's KDS.
        # Always derive it, so the two can't diverge regardless of what's
        # submitted.
        self.venue_id = self.table.venue_id
        current = (
            Tab.objects.filter(pk=self.pk).values_list("status", "closed_at").first()
            if self.pk is not None
            else None
        )
        was_open = current is not None and current[0] == "open"
        just_closed = was_open and self.status == "closed"
        if self.status == "closed" and self.closed_at is None:
            if just_closed:
                # No other writer touches this field (checked repo-wide) —
                # every normal open->closed transition used to leave it NULL
                # forever, making visit-duration/closure-time records
                # inaccurate. Stamp it in the same save() as the transition
                # rather than a follow-up query.
                self.closed_at = timezone.now()
            elif current is not None and current[1] is not None:
                # This instance was loaded before a CONCURRENT save already
                # closed the same tab and stamped closed_at — its own
                # closed_at is still None from that earlier read. Without
                # this, the plain super().save() below would overwrite the
                # real timestamp with NULL. Adopt what's already committed
                # instead of clobbering it.
                self.closed_at = current[1]
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = [*update_fields, "closed_at"]
        super().save(*args, **kwargs)
        if just_closed:
            # join.html tells guests "we only keep this for tonight" — but
            # tab closure is the natural end of that promise, not the
            # cutoff-based purge_stale_guest_names command (which is
            # dry-run by default, has no scheduled invocation on this
            # platform, and only fires after guest_window_minutes has
            # passed regardless of whether the tab already closed). Reset
            # each named guest's own order snapshots to their alias BEFORE
            # the bulk settle below clears display_name, or their typed
            # name would be left as the only remaining copy of something
            # we're about to promise is gone.
            named_guests = list(
                self.guests.filter(status="active").exclude(display_name="")
            )
            for guest in named_guests:
                guest.orders.exclude(alias_snapshot=guest.alias).update(
                    alias_snapshot=guest.alias
                )
            # `uniq_active_alias_per_venue` treats every `status="active"`
            # guest as occupying its alias, with no link to whether the
            # guest's own tab is still open. Without this, a closed tab's
            # guests keep reserving their personas indefinitely — the
            # catalog slowly starves across service nights, and joins
            # eventually fail once it's crowded enough that the bounded
            # collision-retry in guest_join can't find a free one.
            self.guests.filter(status="active").update(
                status="settled", settled_at=timezone.now(), display_name=""
            )


class Guest(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("settled", "Settled"),
        ("removed", "Removed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tab = models.ForeignKey(Tab, related_name="guests", on_delete=models.CASCADE)
    # Denormalised so uniqueness can be enforced with a plain DB constraint —
    # Django can't constrain across the tab -> venue join (spec §4.7).
    venue = models.ForeignKey(Venue, related_name="guests", on_delete=models.CASCADE)
    alias = models.CharField(
        max_length=32, help_text="The rolled noir persona. The delivery key."
    )
    display_name = models.CharField(max_length=20, blank=True, default="")
    joined_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default="active")
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["venue", "alias"],
                condition=Q(status="active"),
                name="uniq_active_alias_per_venue",
            ),
        ]
        ordering = ["joined_at"]

    def __str__(self) -> str:
        return self.display

    def save(self, *args, **kwargs):
        # Same reasoning as Tab.save() above: GuestAdmin exposes `tab` and
        # this denormalised `venue` as independent fields with no
        # cross-check, so a guest at venue-A's tab with venue set to B would
        # get a cookie serving B's menu while its orders (and KDS delivery)
        # stay tied to A's tab. Always derive it.
        self.venue_id = self.tab.venue_id
        super().save(*args, **kwargs)

    @property
    def display(self) -> str:
        """What staff see and shout — spec §3.7."""
        return self.display_name or self.alias


class Order(models.Model):
    STATUS_CHOICES = [
        ("placed", "Placed"),
        ("accepted", "Accepted"),
        ("preparing", "Preparing"),
        ("served", "Served"),
        ("cancelled", "Cancelled"),
    ]
    # spec §4.10 — validated here, not the view.
    _TRANSITIONS = {
        "placed": {"accepted", "cancelled"},
        "accepted": {"preparing", "cancelled"},
        "preparing": {"served", "cancelled"},
        "served": set(),
        "cancelled": set(),
    }
    _TIMESTAMP_FIELD = {
        "accepted": "accepted_at",
        "served": "served_at",
        "cancelled": "cancelled_at",
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guest = models.ForeignKey(Guest, related_name="orders", on_delete=models.CASCADE)
    tab = models.ForeignKey(Tab, related_name="orders", on_delete=models.CASCADE)
    venue = models.ForeignKey(Venue, related_name="orders", on_delete=models.CASCADE)
    # 12, not 8: "T" + table.label[:3] + "-" + order_number formatted `:02d`
    # (a *minimum* width, not a cap) needs room for order_number to grow
    # past 3 digits over a venue's lifetime without truncating/erroring —
    # 1 + 3 + 1 + up to 4 digits = 9 already exceeded the old max_length=8.
    short_code = models.CharField(max_length=12, db_index=True)
    alias_snapshot = models.CharField(max_length=32)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="placed")
    note = models.TextField(blank=True, default="")
    placed_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    total_amount = models.DecimalField(
        max_digits=9, decimal_places=2, default=Decimal("0.00")
    )
    # Snapshotted at placement time, same reasoning as every other *_snapshot
    # field on this model: total_amount/unit_price_snapshot are numbers taken
    # at order time, but the ticket used to re-render with the *live*
    # Venue.currency. Staff changing a venue's currency later would silently
    # turn a historical €12.00 order into a $12.00 receipt.
    currency = models.CharField(max_length=3, default="EUR")

    # Not in the spec's §4.8 table — see the module docstring. Populated by
    # power_up.atmos.lore.engine.generate_vignette() at placement time.
    vignette = models.TextField(blank=True, default="")
    vignette_source = models.CharField(max_length=16, blank=True, default="")

    class Meta:
        ordering = ["-placed_at"]
        indexes = [models.Index(fields=["venue", "status", "placed_at"])]

    def __str__(self) -> str:
        return f"{self.short_code} — {self.alias_snapshot}"

    @property
    def contains_alcohol(self) -> bool:
        """Spec §8.5: flag alcohol-containing orders so staff check at
        delivery. Reads each OrderItem's own snapshot, not the live
        MenuItem — staff editing or repurposing a menu item after an order
        is placed must not silently add or clear the warning on an order
        already in flight. Callers should `.prefetch_related("items")`
        first — this still works without it, just at N+1 query cost."""
        return any(i.contains_alcohol_snapshot for i in self.items.all())

    @property
    def currency_symbol(self) -> str:
        """The symbol for `self.currency` — the placement-time snapshot, not
        whatever `self.venue.currency` reads today."""
        return currency_symbol(self.currency)

    def transition_to(self, new_status: str) -> None:
        allowed = self._TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValidationError(
                f"Cannot move an order from '{self.status}' to '{new_status}'."
            )
        self.status = new_status
        stamp_field = self._TIMESTAMP_FIELD.get(new_status)
        if stamp_field:
            setattr(self, stamp_field, timezone.now())
        self.save(update_fields=["status", stamp_field] if stamp_field else ["status"])


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT)
    name_snapshot = models.CharField(max_length=160)
    unit_price_snapshot = models.DecimalField(max_digits=7, decimal_places=2)
    # Snapshotted at placement time — see Order.contains_alcohol's docstring
    # for why this can't just read menu_item.contains_alcohol live.
    contains_alcohol_snapshot = models.BooleanField(default=False)
    quantity = models.PositiveSmallIntegerField(default=1)
    note = models.CharField(max_length=160, blank=True, default="")
    line_total = models.DecimalField(max_digits=9, decimal_places=2)

    def save(self, *args, **kwargs):
        self.line_total = (self.unit_price_snapshot * self.quantity).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.quantity}x {self.name_snapshot}"
