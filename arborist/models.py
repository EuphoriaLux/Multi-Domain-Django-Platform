"""
Models for Arborist app (arborist.lu).

Includes ArboristBooking for on-site consultation & service appointments with
distance-based prepayment calculation and rush order handling.
"""

import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ArboristBooking(models.Model):
    """
    On-site consultation and tree service booking request.
    
    Includes distance-based prepayment calculation (Zone 1: 50€, Zone 2: 100€)
    and emergency Rush Order scheduling.
    """

    SERVICE_CHOICES = [
        ("obstbaumpflege", _("Fruit Tree Care (Obstbaumpflege)")),
        ("baumpflege", _("Tree Care & Pruning (Baumpflege)")),
        ("baumkontrolle", _("Tree Inspection according to FLL (Baumkontrolle)")),
        ("faellung", _("Tree Felling / Removal (Baumfällung)")),
        ("oekologie", _("Ecological Measures (Ökologische Maßnahmen)")),
        ("notdienst", _("Emergency & Storm Damage (Notdienst & Sturmschaden)")),
        ("beratung", _("General Consultation (Beratung)")),
    ]

    TIME_SLOT_CHOICES = [
        ("flexible", _("Flexible / Any time")),
        ("morning", _("Morning (08:00 - 12:00)")),
        ("afternoon", _("Afternoon (12:00 - 17:00)")),
    ]

    STATUS_CHOICES = [
        ("pending", _("Pending Review")),
        ("confirmed", _("Confirmed")),
        ("completed", _("Completed")),
        ("cancelled", _("Cancelled")),
    ]

    PAYMENT_STATUS_CHOICES = [
        ("pending", _("Prepayment Pending")),
        ("paid", _("Prepayment Paid")),
        ("credited", _("Credited to Final Invoice")),
        ("refunded", _("Refunded")),
    ]

    # Reference & Identification
    booking_reference = models.CharField(
        _("Booking Reference"),
        max_length=30,
        unique=True,
        db_index=True,
        editable=False,
    )

    # Customer Contact Info
    name = models.CharField(_("Full Name"), max_length=150)
    email = models.EmailField(_("Email Address"))
    phone = models.CharField(_("Phone Number"), max_length=50)

    # Service Address
    street_address = models.CharField(_("Street Address"), max_length=255)
    postal_code = models.CharField(_("Postal Code"), max_length=10)
    city_or_commune = models.CharField(_("City / Commune"), max_length=100)

    # Service Details
    service_type = models.CharField(
        _("Service Type"),
        max_length=30,
        choices=SERVICE_CHOICES,
        default="baumpflege",
    )
    number_of_trees = models.CharField(
        _("Number of Trees"),
        max_length=50,
        blank=True,
        help_text=_("e.g. 1-2 trees, orchard, group of mature trees"),
    )
    notes = models.TextField(
        _("Project Description / Details"),
        blank=True,
        help_text=_("Details regarding tree species, height, condition, or access situation."),
    )

    # Scheduling & Urgency
    is_rush = models.BooleanField(
        _("Rush Order (Emergency 24-48h)"),
        default=False,
        help_text=_("Urgent intervention needed within 24-48 hours."),
    )
    preferred_date = models.DateField(_("Preferred Date"), null=True, blank=True)
    preferred_time_slot = models.CharField(
        _("Preferred Time Slot"),
        max_length=20,
        choices=TIME_SLOT_CHOICES,
        default="flexible",
    )

    # Zone & Prepayment
    zone = models.PositiveSmallIntegerField(
        _("Distance Zone"),
        choices=[(1, _("Zone 1 - Local (50 €)")), (2, _("Zone 2 - Wider Luxembourg (100 €)"))],
        default=1,
    )
    distance_km = models.DecimalField(
        _("Distance from Altrier (km)"),
        max_digits=5,
        decimal_places=1,
        default=Decimal("0.0"),
    )
    prepayment_amount = models.DecimalField(
        _("Prepayment Amount (€)"),
        max_digits=6,
        decimal_places=2,
        default=Decimal("50.00"),
    )

    # Status & Management
    status = models.CharField(
        _("Booking Status"),
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    payment_status = models.CharField(
        _("Payment Status"),
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    admin_notes = models.TextField(_("Internal Admin Notes"), blank=True)

    # Timestamps
    created_at = models.DateTimeField(_("Created At"), default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Arborist Booking")
        verbose_name_plural = _("Arborist Bookings")

    def __str__(self) -> str:
        rush_tag = " [🚨 RUSH]" if self.is_rush else ""
        return f"{self.booking_reference}{rush_tag} - {self.name} ({self.get_service_type_display()})"

    def save(self, *args, **kwargs):
        if not self.booking_reference:
            prefix = timezone.now().strftime("%Y%m")
            short_id = uuid.uuid4().hex[:6].upper()
            self.booking_reference = f"ARB-{prefix}-{short_id}"
        super().save(*args, **kwargs)

    @property
    def is_urgent(self) -> bool:
        return self.is_rush or self.service_type == "notdienst"
