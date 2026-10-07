from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, router, transaction
from django.db.models import F, Q


class HubProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_profile",
    )
    organization = models.CharField(max_length=255, blank=True, default="")
    primary_contact = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.organization or self.user.get_username()


class HubRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "Open", "Open"
        IN_REVIEW = "In Review", "In Review"
        WAITING_FOR_CLIENT = "Waiting for Client", "Waiting for Client"
        CLOSED = "Closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "Low", "Low"
        MEDIUM = "Medium", "Medium"
        HIGH = "High", "High"

    class Category(models.TextChoices):
        PROJECT = "Project", "Project"
        TECHNICAL = "Technical", "Technical"
        BILLING = "Billing", "Billing"
        GENERAL = "General", "General"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_requests",
    )
    subject = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.GENERAL
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    priority = models.CharField(
        max_length=20, choices=Priority.choices, default=Priority.MEDIUM
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} ({self.get_status_display()})"


class HubResource(models.Model):
    class Type(models.TextChoices):
        GUIDE = "Guide", "Guide"
        REPORT = "Report", "Report"
        ASSET = "Asset", "Asset"
        INVOICE = "Invoice", "Invoice"

    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.GUIDE)
    url = models.URLField(blank=True, default="")
    is_public = models.BooleanField(default=True)
    audience = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="hub_resources",
        help_text="Leave empty + is_public=True to expose to every authenticated user.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class HubTimelineEvent(models.Model):
    class Kind(models.TextChoices):
        SYSTEM = "system", "System"
        REQUEST = "request", "Request"
        NOTE = "note", "Note"
        MILESTONE = "milestone", "Milestone"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_timeline",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.title} @ {self.occurred_at:%Y-%m-%d}"


class Location(models.Model):
    class EventType(models.TextChoices):
        COOKING_WORKSHOP = "Cooking workshop", "Cooking workshop"
        WINE_TASTING = "Wine tasting", "Wine tasting"
        SPEED_DATING = "Speed dating", "Speed dating"
        OUTDOOR_ACTIVITY = "Outdoor activity", "Outdoor activity"
        QUIZ_NIGHT = "Quiz night", "Quiz night"

    class PartnershipStage(models.TextChoices):
        PROSPECT = "Prospect", "Prospect"
        NEGOTIATING = "Negotiating", "Negotiating"
        ACTIVE = "Active", "Active"
        PAUSED = "Paused", "Paused"
        ARCHIVED = "Archived", "Archived"

    name = models.CharField(max_length=255)
    address = models.TextField()
    city = models.CharField(max_length=255)
    country = models.CharField(max_length=100, default="Luxembourg")

    max_capacity = models.PositiveIntegerField()
    seated_capacity = models.PositiveIntegerField(blank=True, null=True)
    has_outdoor_space = models.BooleanField(default=False)
    has_kitchen = models.BooleanField(default=False)
    has_private_room = models.BooleanField(default=False)
    has_sound_system = models.BooleanField(default=False)
    compatible_event_types = models.JSONField(default=list, blank=True)

    partnership_stage = models.CharField(
        max_length=20,
        choices=PartnershipStage.choices,
        default=PartnershipStage.PROSPECT,
    )
    account_manager = models.CharField(max_length=255, blank=True, default="")
    commercial_terms = models.TextField(blank=True, default="")
    partner_since = models.DateField(blank=True, null=True)

    last_contact_date = models.DateField(blank=True, null=True)
    next_action = models.CharField(max_length=255, blank=True, default="")
    next_action_date = models.DateField(blank=True, null=True)
    notes = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list, blank=True)

    # Structured address, same shape as ``crush_lu.MeetupEvent`` so an offer can
    # prefill an event verbatim. ``address`` stays as the legacy free text.
    address_street = models.CharField(max_length=200, blank=True, default="")
    address_number = models.CharField(max_length=20, blank=True, default="")
    address_postcode = models.CharField(
        max_length=4,
        blank=True,
        default="",
        validators=[
            RegexValidator(
                regex=r"^[0-9]{4}$",
                message="Luxembourg postcodes are exactly four digits.",
            )
        ],
    )
    address_town = models.CharField(max_length=100, blank=True, default="")
    canton = models.CharField(max_length=200, blank=True, default="")
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )

    website = models.URLField(blank=True, default="")
    opening_hours = models.TextField(blank=True, default="")
    blackout_notes = models.TextField(blank=True, default="")
    house_rules = models.TextField(blank=True, default="")

    # Structured deal terms; ``commercial_terms`` keeps the free-text version.
    minimum_spend = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
    )
    revenue_share_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    deposit_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
    )

    # String FK: hub already imports crush_lu, never the other way round.
    echo_venue = models.ForeignKey(
        "crush_lu.EchoVenue",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="partners",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(max_capacity__gte=1),
                name="hub_location_max_capacity_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(seated_capacity__isnull=True)
                    | Q(
                        seated_capacity__gte=1,
                        seated_capacity__lte=F("max_capacity"),
                    )
                ),
                name="hub_location_valid_seated_capacity",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.max_capacity is not None and self.max_capacity < 1:
            errors["max_capacity"] = "Maximum capacity must be at least 1."
        if self.seated_capacity is not None and (
            self.seated_capacity < 1
            or self.max_capacity is None
            or self.seated_capacity > self.max_capacity
        ):
            errors["seated_capacity"] = (
                "Seated capacity must be between 1 and maximum capacity."
            )
        if self.canton:
            # Imported here: crush_lu may not be loaded when this module is.
            from crush_lu.models.events import CANTON_CHOICES

            if self.canton not in {value for value, _label in CANTON_CHOICES}:
                errors["canton"] = "Unknown canton."
        if self.pk and self.max_capacity is not None:
            largest = max(
                self.offers.values_list("max_participants", flat=True), default=0
            )
            if largest > self.max_capacity:
                errors["max_capacity"] = (
                    f"An offer here seats {largest}; lower or edit it first."
                )
        allowed_event_types = set(self.EventType.values)
        if not isinstance(self.compatible_event_types, list) or any(
            not isinstance(event_type, str) or event_type not in allowed_event_types
            for event_type in self.compatible_event_types
        ):
            errors["compatible_event_types"] = (
                "Use a list containing only supported event types."
            )
        if not isinstance(self.tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in self.tags
        ):
            errors["tags"] = "Use a list containing only non-empty tag names."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.name} ({self.city})"

    @property
    def primary_contact(self):
        """The flagged primary contact, else the first one, else ``None``.

        Iterates ``contacts.all()`` so a ``prefetch_related("contacts")`` is
        honoured and the list endpoint stays at one query for contacts.
        """
        contacts = list(self.contacts.all())
        for contact in contacts:
            if contact.is_primary:
                return contact
        return contacts[0] if contacts else None


class LocationContact(models.Model):
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="contacts",
    )
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["location"],
                condition=Q(is_primary=True),
                name="hub_locationcontact_one_primary_per_location",
            ),
        ]

    def __str__(self):
        return f"{self.name} — {self.location.name}"


# Languages an event can be conducted in: ``MeetupEvent.languages`` /
# ``MeetupEvent.LANGUAGE_DISPLAY`` also allow Luxembourgish, unlike the three
# modeltranslation languages used for titles and descriptions.
OFFER_EVENT_LANGUAGES = ("en", "de", "fr", "lu")


# Mirrors ``crush_lu.MeetupEvent.EVENT_TYPE_CHOICES``. Copied rather than
# imported because crush_lu may not be loaded when this module is; a test pins
# the two lists together.
OFFER_EVENT_TYPE_CHOICES = [
    ("speed_dating", "Speed Dating"),
    ("mixer", "Social Mixer"),
    ("activity", "Activity Meetup"),
    ("themed", "Themed Event"),
    ("quiz_night", "Quiz Night"),
    ("crush_cache", "Crush Cache Hunt"),
]


class PartnerOffer(models.Model):
    """One reusable way of running an event at a partner venue.

    A partner can hold several offers, including several of the same event
    type (weekday vs weekend speed dating at different fees). An offer only
    *prefills* a new event; the event keeps its own copy, so editing an offer
    never rewrites past or live events.

    ``title`` and ``description`` are modeltranslation fields (see
    ``hub/translation.py``), like ``MeetupEvent``'s.

    Spec: ai-memory-hub/specs/2026-10-01-hub-partner-and-offers.md
    """

    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, related_name="offers"
    )
    name = models.CharField(max_length=120)
    event_type = models.CharField(max_length=20, choices=OFFER_EVENT_TYPE_CHOICES)
    is_active = models.BooleanField(default=True)

    weekdays = models.JSONField(
        default=list, blank=True, help_text="Weekdays, 0 = Monday … 6 = Sunday."
    )
    start_time = models.TimeField(blank=True, null=True)
    duration_minutes = models.PositiveIntegerField(default=120)

    max_participants = models.PositiveIntegerField(default=20)
    max_participants_m = models.PositiveIntegerField(blank=True, null=True)
    max_participants_f = models.PositiveIntegerField(blank=True, null=True)
    max_participants_nb = models.PositiveIntegerField(blank=True, null=True)
    min_age = models.PositiveIntegerField(default=18)
    max_age = models.PositiveIntegerField(default=99)

    registration_fee = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Event fee in EUR.",
        validators=[MinValueValidator(0)],
    )
    partner_cost_notes = models.TextField(blank=True, default="")

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    has_food_component = models.BooleanField(default=False)
    allow_plus_ones = models.BooleanField(default=False)

    space_used = models.CharField(max_length=200, blank=True, default="")
    setup_notes = models.TextField(blank=True, default="")
    languages = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "event_type", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(max_participants__gte=1),
                name="hub_partneroffer_max_participants_positive",
            ),
            models.CheckConstraint(
                condition=Q(min_age__lte=F("max_age")),
                name="hub_partneroffer_valid_age_range",
            ),
        ]

    def clean(self):
        super().clean()
        from crush_lu.models.events import MAX_EVENT_DURATION_MINUTES

        errors = {}
        if not self._state.adding:
            using = self._state.db or router.db_for_read(type(self), instance=self)
            previous = (
                type(self)
                .objects.using(using)
                .filter(pk=self.pk)
                .values_list("location_id", flat=True)
                .first()
            )
            self._validate_location_change(previous, using)
        if self.duration_minutes is not None and not (
            1 <= self.duration_minutes <= MAX_EVENT_DURATION_MINUTES
        ):
            errors["duration_minutes"] = (
                f"Duration must be between 1 and {MAX_EVENT_DURATION_MINUTES} minutes."
            )
        if self.location_id and self.max_participants is not None:
            venue_capacity = self.location.max_capacity
            if self.max_participants > venue_capacity:
                errors["max_participants"] = (
                    f"This venue holds at most {venue_capacity} people."
                )
        if not isinstance(self.weekdays, list) or any(
            isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6
            for day in self.weekdays
        ):
            errors["weekdays"] = "Use a list of weekday numbers from 0 to 6."
        if not isinstance(self.languages, list) or any(
            language not in OFFER_EVENT_LANGUAGES for language in self.languages
        ):
            errors["languages"] = "Use a list containing only en, de, fr or lu."
        if self.min_age is not None and self.min_age < 18:
            errors["min_age"] = "Minimum age must be at least 18."
        if self.max_age is not None and self.max_age > 120:
            errors["max_age"] = "Maximum age cannot exceed 120."
        if (
            self.min_age is not None
            and self.max_age is not None
            and self.min_age > self.max_age
        ):
            errors["max_age"] = "Maximum age must not be below minimum age."
        # Mirrors MeetupEvent.clean(): all three gender caps or none, summing
        # to no more than the total.
        caps = [
            cap
            for cap in (
                self.max_participants_m,
                self.max_participants_f,
                self.max_participants_nb,
            )
            if cap is not None
        ]
        if 0 < len(caps) < 3:
            errors["max_participants_m"] = (
                "Set all three gender caps together, or leave them all blank."
            )
        elif len(caps) == 3 and sum(caps) > (self.max_participants or 0):
            errors["max_participants_m"] = (
                "The gender caps must not add up to more than the total capacity."
            )
        if errors:
            raise ValidationError(errors)

    def _validate_location_change(self, previous_location_id, using):
        if (
            previous_location_id is not None
            and previous_location_id != self.location_id
            and self.meetup_events.using(using).exists()
        ):
            raise ValidationError(
                {
                    "location": "This offer is used by events and cannot move to another "
                    "partner. Create a new offer at that partner instead."
                }
            )

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if self._state.adding or (
            update_fields is not None
            and not {"location", "location_id"}.intersection(update_fields)
        ):
            return super().save(*args, **kwargs)
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        # A form's clean() is only a preview: an event can be linked after it.
        # The link endpoint takes this same offer lock, so ownership is checked
        # again under the lock held through the write/commit.
        with transaction.atomic(using=using):
            previous = (
                type(self)
                .objects.using(using)
                .order_by()
                .select_for_update(of=("self",))
                .filter(pk=self.pk)
                .values_list("location_id", flat=True)
                .first()
            )
            self._validate_location_change(previous, using)
            return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} @ {self.location.name}"


class PartnerOnboardingStep(models.Model):
    """One onboarding checklist item for a partner; done when ``done_at`` is set.

    Rows are created lazily: a partner with no rows simply has every step open.
    """

    class Key(models.TextChoices):
        CONTACT_MADE = "contact_made", "First contact made"
        TERMS_AGREED = "terms_agreed", "Terms agreed"
        VENUE_VISIT = "venue_visit", "Venue visit done"
        PHOTOS = "photos", "Photos collected"
        HOUSE_RULES = "house_rules", "House rules recorded"
        TEST_EVENT = "test_event", "Test event held"
        ECHO_VENUE_REGISTERED = "echo_venue_registered", "echo.lu venue registered"
        ACTIVE = "active", "Partner active"

    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, related_name="onboarding_steps"
    )
    key = models.CharField(max_length=30, choices=Key.choices)
    done_at = models.DateTimeField(blank=True, null=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["location", "key"],
                name="hub_partneronboardingstep_unique_key",
            ),
        ]

    def __str__(self):
        return f"{self.location.name}: {self.key}"


class PaymentStatus(models.TextChoices):
    """Shared settlement state for every accounting row.

    Values are stored verbatim because the CRM SPA both renders them and
    derives CSS classes from them (``status.toLowerCase()``) — see the
    ``PaymentStatus`` union in the frontend's ``lib/types.ts``.
    """

    PAID = "Paid", "Paid"
    PENDING = "Pending", "Pending"
    OVERDUE = "Overdue", "Overdue"
    SCHEDULED = "Scheduled", "Scheduled"


class PaymentMethod(models.TextChoices):
    CARD = "Card", "Card"
    TRANSFER = "Transfer", "Transfer"
    CASH = "Cash", "Cash"
    PAYCONIQ = "Payconiq", "Payconiq"


class PaymentIn(models.Model):
    """Money received — ticket sales, sponsorships, and other inbound revenue.

    Bookkeeping only: these rows are kept by hand for the CRM accounting page
    and are deliberately not wired to ``crush_lu.PaymentTransaction``/SumUp,
    which is a separate live payments pipeline.
    """

    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    source = models.CharField(max_length=255)
    client_name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    reference = models.CharField(max_length=255, blank=True, default="")
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.TRANSFER
    )
    receipt_url = models.URLField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="hub_paymentin_amount_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.source} +{self.amount} [{self.status}]"


class PaymentOut(models.Model):
    """Money spent — venue fees, marketing, tooling, supplies."""

    class Category(models.TextChoices):
        VENUE = "Lieu", "Lieu"
        MARKETING = "Marketing", "Marketing"
        TECH = "Tech", "Tech"
        SUPPLIES = "Fournitures", "Fournitures"
        OTHER = "Autre", "Autre"

    class DepositStatus(models.TextChoices):
        DEPOSIT = "deposit", "Deposit"
        BALANCE = "balance", "Balance"
        FULL = "full", "Full"

    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    # Bookkeeping outlives the partnership, so an archived venue must not take
    # its cost history with it.
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        related_name="payments_out",
        blank=True,
        null=True,
    )
    payee = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.TRANSFER
    )
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    deposit_status = models.CharField(
        max_length=20,
        choices=DepositStatus.choices,
        blank=True,
        null=True,
        default=None,
    )
    receipt_url = models.URLField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="hub_paymentout_amount_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.payee} -{self.amount} [{self.status}]"


class Payroll(models.Model):
    """Staff compensation: salaries, reimbursed expenses, and bonuses.

    ``amount`` is what actually leaves the bank; ``gross_salary`` and
    ``employer_charges`` are kept alongside it so the accounting page can show
    the employer's full cost without re-deriving it.
    """

    class Category(models.TextChoices):
        SALARY = "Salary", "Salary"
        EXPENSE = "Expense", "Expense"
        BONUS = "Bonus", "Bonus"

    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    gross_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    employer_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    employee_name = models.CharField(max_length=255)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.SALARY
    )
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    payment_method = models.CharField(
        max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.TRANSFER
    )
    receipt_url = models.URLField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0)
                & Q(gross_salary__gte=0)
                & Q(employer_charges__gte=0),
                name="hub_payroll_amounts_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.employee_name} {self.amount} ({self.category})"


class Refund(models.Model):
    """A participant reimbursement recorded for the books.

    Standalone from the live refund path in ``crush_lu`` — this is the
    accountant's ledger view, not an instruction to move money.
    """

    date = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    participant_name = models.CharField(max_length=255)
    event_name = models.CharField(max_length=255, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="hub_refund_amount_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.participant_name} -{self.amount} [{self.status}]"


class WhatsAppMessage(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_whatsapp_messages",
    )
    wa_message_id = models.CharField(
        max_length=255, blank=True, default="", db_index=True
    )
    recipient = models.CharField(max_length=32)
    template_name = models.CharField(max_length=255)
    language = models.CharField(max_length=16)
    parameters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED
    )
    status_history = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template_name} → {self.recipient} [{self.status}]"


class WhatsAppInboundMessage(models.Model):
    """A message a user sent TO our WhatsApp number (Cloud API inbound).

    A Cloud API number has no phone-app inbox, so an inbound reply is otherwise
    only a transient webhook event that the status-only webhook used to drop.
    Persisting each one lets the hub CRM show a support inbox.

    ``wa_message_id`` is unique so Meta's webhook retries (it re-POSTs until it
    gets a 200) are idempotent — the second delivery is a no-op.
    """

    wa_message_id = models.CharField(max_length=255, unique=True)
    from_number = models.CharField(max_length=32, db_index=True)
    contact_name = models.CharField(max_length=255, blank=True, default="")
    message_type = models.CharField(max_length=32, default="text")
    # Body for text messages; non-text types (image, audio, …) keep the full
    # message object in ``payload`` and leave this blank.
    text = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        indexes = [models.Index(fields=["is_read", "-received_at"])]

    def __str__(self):
        return f"{self.from_number} ({self.message_type}) @ {self.received_at:%Y-%m-%d %H:%M}"


class SocialPost(models.Model):
    class Pillar(models.TextChoices):
        EVENT_RECAP = "event_recap", "Event Recap"
        DATING_TIP = "dating_tip", "Dating Tip"
        MILESTONE = "milestone", "Milestone"
        COMMUNITY = "community", "Community"
        PROMO = "promo", "Promotion"

    class Language(models.TextChoices):
        FR = "fr", "French"
        EN = "en", "English"
        DE = "de", "German"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_REVIEW = "pending_review", "Pending Review"
        APPROVED = "approved", "Approved"
        SCHEDULED = "scheduled", "Scheduled"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_social_posts",
    )
    featured_profile = models.ForeignKey(
        "crush_lu.CrushProfile",
        on_delete=models.CASCADE,
        related_name="hub_social_posts",
        blank=True,
        null=True,
    )
    source_event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.SET_NULL,
        related_name="social_promotion_posts",
        blank=True,
        null=True,
        help_text="Published Crush event whose existing copy was reused for this post.",
    )
    pillar = models.CharField(
        max_length=32, choices=Pillar.choices, default=Pillar.EVENT_RECAP
    )
    language = models.CharField(
        max_length=8, choices=Language.choices, default=Language.FR
    )
    platforms = models.JSONField(default=list, blank=True)
    buffer_profile_ids = models.JSONField(default=list, blank=True)
    buffer_profile_platforms = models.JSONField(default=dict, blank=True)
    dispatched_platforms = models.JSONField(default=list, blank=True)
    hook = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField(blank=True, default="")
    media_url = models.URLField(blank=True, null=True, default=None)
    media_urls = models.JSONField(default=list, blank=True)
    media_type = models.CharField(
        max_length=5, choices=[("image", "Image"), ("video", "Video")], default="image"
    )
    generation_key = models.CharField(
        max_length=200, unique=True, null=True, blank=True
    )
    source_metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    scheduled_for = models.DateTimeField(blank=True, null=True, default=None)
    buffer_id = models.CharField(max_length=255, blank=True, default="")
    buffer_delivery_uncertain = models.BooleanField(default=False)
    article_id = models.CharField(max_length=255, blank=True, default="")
    status_history = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.language.upper()}] {self.hook or self.content[:30]} ({self.status})"


class EventCoachAvailability(models.Model):
    class Role(models.TextChoices):
        ANIMATION = "Animation", "Animation"
        ACCUEIL = "Accueil", "Accueil"
        PHOTO_VIDEO = "Photo/Vidéo", "Photo/Vidéo"
        SUPPORT = "Support", "Support"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Disponible"
        ASSIGNED = "assigned", "Assigné"
        DECLINED = "declined", "Écarté"

    event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="coach_availabilities",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hub_coach_availabilities",
    )
    coach_name = models.CharField(max_length=255, blank=True)
    role = models.CharField(
        max_length=50, choices=Role.choices, default=Role.ANIMATION
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.AVAILABLE
    )
    note = models.TextField(blank=True, default="")
    declared_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-declared_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "user"],
                name="unique_hub_event_user_availability",
            )
        ]

    def __str__(self):
        return f"{self.coach_name or self.user} - Event {self.event_id} ({self.status})"


class ErasedPhoneNumber(models.Model):
    """Erasure suppression record: a keyed digest of an erased phone number.

    Lets the inbound webhook (and the Hub send path) store a late message for
    an erased member sanitised instead of resurrecting their number, name and
    text. Only an HMAC-SHA256 of the digits is kept, never the number.

    Retention: rows are pruned after 90 days by ``gdpr_retention_cleanup``
    (``erased_phone_days``): a late delivery arrives within minutes or days.

    Key: ``settings.ERASURE_DIGEST_KEY`` when set. It must be dedicated and
    NEVER rotated: the numbers are gone, so a digest can never be rebuilt, and
    a rotated key would orphan every tombstone. Without it the digest falls
    back to ``SECRET_KEY`` (checked together with ``SECRET_KEY_FALLBACKS``,
    migrating a fallback match lazily), which is only as stable as that key.
    """

    digest = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def digits(number):
        return "".join(ch for ch in str(number or "") if ch.isdigit())

    @classmethod
    def _digests(cls, number):
        """(current_digest, [all digests incl. fallback keys]) for ``number``."""
        import hashlib
        import hmac

        from django.conf import settings

        digits = cls.digits(number)
        if not digits:
            return "", []
        dedicated = getattr(settings, "ERASURE_DIGEST_KEY", "") or ""
        keys = [
            *([dedicated] if dedicated else []),
            settings.SECRET_KEY,
            *getattr(settings, "SECRET_KEY_FALLBACKS", []),
        ]
        all_digests = [
            hmac.new(str(k).encode(), digits.encode(), hashlib.sha256).hexdigest()
            for k in keys
        ]
        return all_digests[0], all_digests

    @classmethod
    def record(cls, numbers):
        for number in numbers or ():
            current, _all = cls._digests(number)
            if current:
                cls.objects.get_or_create(digest=current)

    @classmethod
    def _has_live_verified_owner(cls, digits):
        """True when an active member currently has this number verified.

        A tombstone must not silence a legitimate member: the old profile
        released the number, so the same person rejoining (or a carrier
        reassigning it) can verify it on a new account.
        """
        from django.apps import apps
        from django.db.models import F, Value
        from django.db.models.functions import Replace

        profile_model = apps.get_model("crush_lu", "CrushProfile")
        digits_expr = F("phone_number")
        for junk in (" ", "-", "(", ")", ".", "+"):
            digits_expr = Replace(digits_expr, Value(junk), Value(""))
        return (
            profile_model.objects.filter(phone_verified=True, user__is_active=True)
            # A member whose deletion is under way (or done) is the one being
            # erased, not a new owner: their consent row is banned.
            .exclude(user__data_consent__crushlu_banned=True)
            .annotate(_digits=digits_expr)
            .filter(_digits=digits)
            .exists()
        )

    @classmethod
    def is_erased(cls, number):
        current, all_digests = cls._digests(number)
        if not current:
            return False
        matched = list(
            cls.objects.filter(digest__in=all_digests).values_list("digest", flat=True)
        )
        if not matched:
            return False
        if current not in matched:
            # Matched under a fallback key only: migrate it to the current key.
            cls.objects.get_or_create(digest=current)
        return not cls._has_live_verified_owner(cls.digits(number))

    def __str__(self):
        return f"erased:{self.digest[:8]}"
