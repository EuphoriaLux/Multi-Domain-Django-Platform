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
from django.db import models
from django.db.models import Q
from django.utils import timezone

from power_up.storage import powerup_media_storage, powerup_upload_path

# Mirrors printing.layout.money()'s mapping — kept here too since templates
# can't easily call an imported function, and printing/ deliberately stays
# Django-free (no models import), so the dependency can't run the other way.
_CURRENCY_SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$"}


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
        different one on the printed ticket. Falls back to the bare code
        (with a trailing space) rather than an ambiguous plain number, same
        as money()."""
        return _CURRENCY_SYMBOLS.get(self.currency, self.currency + " ")


class Table(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    venue = models.ForeignKey(Venue, related_name="tables", on_delete=models.CASCADE)
    label = models.CharField(max_length=32)
    qr_token = models.CharField(max_length=22, unique=True, db_index=True)
    seats = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["venue", "label"], name="uniq_table_label_per_venue"),
        ]
        ordering = ["label"]

    def __str__(self) -> str:
        return f"{self.venue.name} — Table {self.label}"

    def save(self, *args, **kwargs):
        if not self.qr_token:
            # secrets.token_urlsafe(16) per spec §4.3 — the QR encodes this,
            # never the PK, so a leaked sticker can be rotated independently
            # of order history.
            self.qr_token = secrets.token_urlsafe(16)
        super().save(*args, **kwargs)


class MenuCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    venue = models.ForeignKey(Venue, related_name="menu_categories", on_delete=models.CASCADE)
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
    category = models.ForeignKey(MenuCategory, related_name="items", on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=7, decimal_places=2)
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

    def save(self, *args, **kwargs):
        # `venue` is denormalised for staff queries (see Guest's comment on
        # the same tradeoff) — it must never be independently settable, or
        # the default admin lets staff pick a table at venue A and a venue
        # field pointing at B: a scan of A's QR reuses that tab and serves
        # B's menu, creating guests and orders under the wrong venue's KDS.
        # Always derive it, so the two can't diverge regardless of what's
        # submitted.
        self.venue_id = self.table.venue_id
        super().save(*args, **kwargs)


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
    alias = models.CharField(max_length=32, help_text="The rolled noir persona. The delivery key.")
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
    total_amount = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("0.00"))

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
        delivery. Callers should `.prefetch_related("items__menu_item")`
        first — this still works without it, just at N+1 query cost."""
        return any(i.menu_item.contains_alcohol for i in self.items.all())

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
