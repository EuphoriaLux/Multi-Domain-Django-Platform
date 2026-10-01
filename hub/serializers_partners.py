"""Serializers for the Partner (``hub.Location``) object and its offers.

The wire format is camelCase because the hub SPA reads it that way. Reads keep
every key the old read-only ``/hub/locations`` payload had, so the live
``/locations`` page keeps working; the new keys are additive.

Spec: ai-memory-hub/specs/2026-10-01-hub-partner-and-offers.md
"""

from django.db import transaction
from rest_framework import serializers

from crush_lu.models.echo_lu import EchoVenue

from .models import (
    OFFER_EVENT_TYPE_CHOICES,
    Location,
    LocationContact,
    PartnerOffer,
    PartnerOnboardingStep,
)
from .serializers import MoneyField

OFFER_LANGUAGES = ("en", "de", "fr")

# Fields the SPA models as optional: omitted from the payload when null, the
# same convention the finance serializers use.
_OMIT_WHEN_NULL = (
    "seatedCapacity",
    "partnerSince",
    "nextActionDate",
    "latitude",
    "longitude",
    "minimumSpend",
    "revenueSharePercent",
    "depositAmount",
    "echoVenueId",
)


def _f(field_class, source, **kwargs):
    """A camelCase field bound to a snake_case model attribute, optional on write."""
    kwargs.setdefault("required", False)
    return field_class(source=source, **kwargs)


def _money(source, **kwargs):
    kwargs.setdefault("read_only", False)
    kwargs.setdefault("required", False)
    kwargs.setdefault("allow_null", True)
    kwargs.setdefault("min_value", 0)
    return MoneyField(source=source, **kwargs)


class BlankDateField(serializers.DateField):
    """A date the SPA treats as ``""`` when unset, and accepts ``""`` to clear."""

    def to_internal_value(self, value):
        if value in ("", None):
            return None
        return super().to_internal_value(value)

    def to_representation(self, value):
        return super().to_representation(value) if value else ""


class PrimaryContactSerializer(serializers.ModelSerializer):
    """The legacy single-contact shape the ``/locations`` page still reads."""

    class Meta:
        model = LocationContact
        fields = ["name", "role", "email", "phone"]
        read_only_fields = fields


class LocationContactSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)
    isPrimary = serializers.BooleanField(source="is_primary", required=False)

    class Meta:
        model = LocationContact
        fields = ["id", "name", "role", "email", "phone", "isPrimary"]


class LocationSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    # name and city are copied into MeetupEvent.location (200) and
    # MeetupEvent.address_town (100) by the event prefill, so the API holds
    # them to those limits rather than the wider Location columns.
    name = serializers.CharField(max_length=200)
    address = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    country = serializers.CharField(required=False, max_length=100)

    maxCapacity = _f(serializers.IntegerField, "max_capacity", min_value=1)
    seatedCapacity = _f(
        serializers.IntegerField, "seated_capacity", min_value=1, allow_null=True
    )
    hasOutdoorSpace = _f(serializers.BooleanField, "has_outdoor_space")
    hasKitchen = _f(serializers.BooleanField, "has_kitchen")
    hasPrivateRoom = _f(serializers.BooleanField, "has_private_room")
    hasSoundSystem = _f(serializers.BooleanField, "has_sound_system")
    compatibleEventTypes = _f(
        serializers.ListField,
        "compatible_event_types",
        child=serializers.ChoiceField(choices=Location.EventType.choices),
    )

    partnershipStage = _f(
        serializers.ChoiceField,
        "partnership_stage",
        choices=Location.PartnershipStage.choices,
    )
    accountManager = _f(
        serializers.CharField, "account_manager", allow_blank=True, max_length=255
    )
    commercialTerms = _f(serializers.CharField, "commercial_terms", allow_blank=True)
    partnerSince = _f(serializers.DateField, "partner_since", allow_null=True)
    lastContactDate = _f(BlankDateField, "last_contact_date", allow_null=True)
    nextAction = _f(
        serializers.CharField, "next_action", allow_blank=True, max_length=255
    )
    nextActionDate = _f(serializers.DateField, "next_action_date", allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    tags = serializers.ListField(child=serializers.CharField(), required=False)

    addressStreet = _f(
        serializers.CharField, "address_street", allow_blank=True, max_length=200
    )
    addressNumber = _f(
        serializers.CharField, "address_number", allow_blank=True, max_length=20
    )
    addressPostcode = _f(
        serializers.RegexField,
        "address_postcode",
        regex=r"^[0-9]{4}$",
        allow_blank=True,
        error_messages={"invalid": "Luxembourg postcodes are exactly four digits."},
    )
    addressTown = _f(
        serializers.CharField, "address_town", allow_blank=True, max_length=100
    )
    canton = serializers.CharField(required=False, allow_blank=True, max_length=200)
    latitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-90,
        max_value=90,
        coerce_to_string=False,
        required=False,
        allow_null=True,
    )
    longitude = serializers.DecimalField(
        max_digits=9,
        decimal_places=6,
        min_value=-180,
        max_value=180,
        coerce_to_string=False,
        required=False,
        allow_null=True,
    )

    website = serializers.URLField(required=False, allow_blank=True, max_length=200)
    openingHours = _f(serializers.CharField, "opening_hours", allow_blank=True)
    blackoutNotes = _f(serializers.CharField, "blackout_notes", allow_blank=True)
    houseRules = _f(serializers.CharField, "house_rules", allow_blank=True)

    minimumSpend = _money("minimum_spend", max_digits=8)
    revenueSharePercent = _f(
        serializers.DecimalField,
        "revenue_share_percent",
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
        coerce_to_string=False,
        allow_null=True,
    )
    depositAmount = _money("deposit_amount", max_digits=8)
    echoVenueId = _f(
        serializers.PrimaryKeyRelatedField,
        "echo_venue",
        queryset=EchoVenue.objects.all(),
        allow_null=True,
    )

    contacts = LocationContactSerializer(many=True, required=False)
    primaryContact = serializers.SerializerMethodField()
    offerCount = serializers.SerializerMethodField()
    onboardingProgress = serializers.SerializerMethodField()

    class Meta:
        model = Location
        fields = [
            "id",
            "name",
            "address",
            "city",
            "country",
            "maxCapacity",
            "seatedCapacity",
            "hasOutdoorSpace",
            "hasKitchen",
            "hasPrivateRoom",
            "hasSoundSystem",
            "compatibleEventTypes",
            "partnershipStage",
            "primaryContact",
            "accountManager",
            "commercialTerms",
            "partnerSince",
            "lastContactDate",
            "nextAction",
            "nextActionDate",
            "notes",
            "tags",
            "addressStreet",
            "addressNumber",
            "addressPostcode",
            "addressTown",
            "canton",
            "latitude",
            "longitude",
            "website",
            "openingHours",
            "blackoutNotes",
            "houseRules",
            "minimumSpend",
            "revenueSharePercent",
            "depositAmount",
            "echoVenueId",
            "contacts",
            "offerCount",
            "onboardingProgress",
        ]

    # -- reads -------------------------------------------------------------

    def get_primaryContact(self, obj):
        contact = obj.primary_contact
        if contact is None:
            return {"name": "", "role": "", "email": "", "phone": ""}
        return PrimaryContactSerializer(contact).data

    def get_offerCount(self, obj):
        return len(obj.offers.all())

    def get_onboardingProgress(self, obj):
        done = sum(1 for step in obj.onboarding_steps.all() if step.done_at)
        return {"done": done, "total": len(PartnerOnboardingStep.Key.values)}

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for optional_field in _OMIT_WHEN_NULL:
            if data.get(optional_field) is None:
                data.pop(optional_field, None)
        # DRF skips ``to_representation`` for a null attribute, so the SPA's
        # "" for an unset last-contact date has to be applied here.
        if data.get("lastContactDate") is None:
            data["lastContactDate"] = ""
        return data

    # -- validation --------------------------------------------------------

    def validate_canton(self, value):
        if not value:
            return value
        from crush_lu.models.events import CANTON_CHOICES

        if value not in {key for key, _label in CANTON_CHOICES}:
            raise serializers.ValidationError("Unknown canton.")
        return value

    def validate_contacts(self, contacts):
        if sum(1 for contact in contacts if contact.get("is_primary")) > 1:
            raise serializers.ValidationError("Only one contact can be primary.")
        ids = [contact["id"] for contact in contacts if "id" in contact]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("A contact id appears twice.")
        if ids:
            owned = (
                set(self.instance.contacts.values_list("id", flat=True))
                if self.instance
                else set()
            )
            if not set(ids) <= owned:
                raise serializers.ValidationError(
                    "A contact id does not belong to this partner."
                )
        return contacts

    def validate(self, attrs):
        instance = self.instance

        def current(key):
            if key in attrs:
                return attrs[key]
            return getattr(instance, key, None) if instance else None

        # An explicit blank legacy address means "not given": compose it from
        # the structured fields (or leave the stored one), never erase it.
        if "address" in attrs and not attrs["address"]:
            del attrs["address"]

        errors = {}
        max_capacity = current("max_capacity")
        seated = current("seated_capacity")
        if instance is None and max_capacity is None:
            errors["maxCapacity"] = "This field is required."
        elif seated is not None and (max_capacity is None or seated > max_capacity):
            errors["seatedCapacity"] = (
                "Seated capacity must be between 1 and maximum capacity."
            )

        structured = (
            "address_street",
            "address_number",
            "address_postcode",
            "address_town",
        )
        if any(key in attrs for key in structured) and "address" not in attrs:
            attrs["address"] = self._compose_address(current)
        if instance is None and not attrs.get("address"):
            errors["address"] = "Give an address or the structured street and town."
        # ``city`` is the legacy field the live SPA still shows: follow the
        # structured town unless the caller sets the city explicitly.
        if not attrs.get("city") and attrs.get("address_town"):
            attrs["city"] = attrs["address_town"]
        if instance is None and not attrs.get("city"):
            errors["city"] = "Give a city or the structured town."
        if instance is not None and max_capacity is not None:
            largest = max(
                instance.offers.values_list("max_participants", flat=True),
                default=0,
            )
            if largest > max_capacity:
                errors["maxCapacity"] = (
                    f"An offer here seats {largest}; lower or edit it first."
                )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @staticmethod
    def _compose_address(current):
        street = " ".join(
            part
            for part in (current("address_street"), current("address_number"))
            if part
        )
        postcode = current("address_postcode")
        town = " ".join(
            part
            for part in (f"L-{postcode}" if postcode else "", current("address_town"))
            if part
        )
        return ", ".join(part for part in (street, town) if part)

    # -- writes ------------------------------------------------------------

    @transaction.atomic
    def create(self, validated_data):
        contacts = validated_data.pop("contacts", None)
        location = Location.objects.create(**validated_data)
        if contacts is not None:
            self._sync_contacts(location, contacts)
        return location

    @transaction.atomic
    def update(self, instance, validated_data):
        contacts = validated_data.pop("contacts", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if contacts is not None:
            self._sync_contacts(instance, contacts)
        return instance

    @staticmethod
    def _sync_contacts(location, contacts):
        """Replace the partner's contacts with ``contacts``.

        Rows whose id is absent are deleted, listed ids are updated, and rows
        without an id are created. Primary flags are cleared first so moving
        the flag never trips the one-primary-per-partner constraint, and the
        first contact becomes primary when none is flagged.
        """
        kept = {contact["id"] for contact in contacts if "id" in contact}
        location.contacts.exclude(id__in=kept).delete()
        location.contacts.update(is_primary=False)
        has_primary = any(contact.get("is_primary") for contact in contacts)
        for index, data in enumerate(contacts):
            data = dict(data)
            contact_id = data.pop("id", None)
            data["is_primary"] = bool(data.get("is_primary")) or (
                not has_primary and index == 0
            )
            if contact_id is None:
                LocationContact.objects.create(location=location, **data)
            else:
                LocationContact.objects.filter(id=contact_id).update(**data)


class TranslatedTextField(serializers.Field):
    """An EN/DE/FR modeltranslation pair as ``{"en": …, "de": …, "fr": …}``.

    Bound to the whole instance (``source="*"``) so it reads and writes the
    ``<base>_<lang>`` columns directly: the plain ``<base>`` attribute follows
    the *request's* active language, which would make an API read depend on
    the caller's locale.
    """

    def __init__(self, base, max_length=None, **kwargs):
        self.base = base
        self.max_length = max_length
        kwargs["source"] = "*"
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def to_representation(self, instance):
        return {
            lang: getattr(instance, f"{self.base}_{lang}", None) or ""
            for lang in OFFER_LANGUAGES
        }

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Expected an object keyed by language.")
        unknown = set(data) - set(OFFER_LANGUAGES)
        if unknown:
            raise serializers.ValidationError(
                f"Unsupported languages: {', '.join(sorted(unknown))}."
            )
        values = {}
        for lang, value in data.items():
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise serializers.ValidationError(f"{lang} must be text.")
            value = value.strip()
            if self.max_length and len(value) > self.max_length:
                raise serializers.ValidationError(
                    f"{lang} must be at most {self.max_length} characters."
                )
            values[f"{self.base}_{lang}"] = value
        return values


class PartnerOfferSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    locationId = serializers.CharField(source="location_id", read_only=True)

    eventType = _f(
        serializers.ChoiceField,
        "event_type",
        choices=OFFER_EVENT_TYPE_CHOICES,
    )
    isActive = _f(serializers.BooleanField, "is_active")
    weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6), required=False
    )
    startTime = _f(serializers.TimeField, "start_time", format="%H:%M", allow_null=True)
    durationMinutes = _f(serializers.IntegerField, "duration_minutes", min_value=1)

    maxParticipants = _f(serializers.IntegerField, "max_participants", min_value=1)
    maxParticipantsM = _f(
        serializers.IntegerField, "max_participants_m", min_value=0, allow_null=True
    )
    maxParticipantsF = _f(
        serializers.IntegerField, "max_participants_f", min_value=0, allow_null=True
    )
    maxParticipantsNb = _f(
        serializers.IntegerField, "max_participants_nb", min_value=0, allow_null=True
    )
    minAge = _f(serializers.IntegerField, "min_age", min_value=18)
    maxAge = _f(serializers.IntegerField, "max_age", max_value=120)

    registrationFee = _money("registration_fee", max_digits=6, allow_null=False)
    partnerCostNotes = _f(serializers.CharField, "partner_cost_notes", allow_blank=True)

    title = TranslatedTextField("title", max_length=200)
    description = TranslatedTextField("description")
    hasFoodComponent = _f(serializers.BooleanField, "has_food_component")
    allowPlusOnes = _f(serializers.BooleanField, "allow_plus_ones")

    spaceUsed = _f(
        serializers.CharField, "space_used", allow_blank=True, max_length=200
    )
    setupNotes = _f(serializers.CharField, "setup_notes", allow_blank=True)
    languages = serializers.ListField(
        child=serializers.ChoiceField(choices=OFFER_LANGUAGES), required=False
    )
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = PartnerOffer
        fields = [
            "id",
            "locationId",
            "name",
            "eventType",
            "isActive",
            "weekdays",
            "startTime",
            "durationMinutes",
            "maxParticipants",
            "maxParticipantsM",
            "maxParticipantsF",
            "maxParticipantsNb",
            "minAge",
            "maxAge",
            "registrationFee",
            "partnerCostNotes",
            "title",
            "description",
            "hasFoodComponent",
            "allowPlusOnes",
            "spaceUsed",
            "setupNotes",
            "languages",
            "updatedAt",
        ]

    def validate_durationMinutes(self, value):
        from crush_lu.models.events import MAX_EVENT_DURATION_MINUTES

        if value > MAX_EVENT_DURATION_MINUTES:
            raise serializers.ValidationError(
                f"Duration cannot exceed {MAX_EVENT_DURATION_MINUTES} minutes."
            )
        return value

    def validate(self, attrs):
        instance = self.instance

        def current(key):
            if key in attrs:
                return attrs[key]
            if instance:
                return getattr(instance, key)
            # Create: an omitted field takes the model default, which is what
            # will actually be saved (and checked by the DB constraints).
            field = PartnerOffer._meta.get_field(key)
            return field.default if field.has_default() else None

        errors = {}
        if instance is None:
            if not attrs.get("name"):
                errors["name"] = "This field is required."
            if not attrs.get("event_type"):
                errors["eventType"] = "This field is required."
        # The default language (en) is the one column the model requires.
        if (instance is None and not attrs.get("title_en")) or (
            "title_en" in attrs and not attrs["title_en"]
        ):
            errors["title"] = "An English title is required."
        min_age, max_age = current("min_age"), current("max_age")
        if min_age is not None and max_age is not None and min_age > max_age:
            errors["maxAge"] = "Maximum age must not be below minimum age."
        total = current("max_participants")
        partner = instance.location if instance else self.context.get("partner")
        if partner is not None and total is not None and total > partner.max_capacity:
            errors["maxParticipants"] = (
                f"This venue holds at most {partner.max_capacity} people."
            )
        # Same rules as MeetupEvent.clean(), so a saved offer always yields a
        # draft that can become a valid event.
        caps = [
            current("max_participants_m"),
            current("max_participants_f"),
            current("max_participants_nb"),
        ]
        set_caps = [cap for cap in caps if cap is not None]
        if 0 < len(set_caps) < 3:
            errors["maxParticipantsM"] = (
                "Set all three gender caps together, or leave them all blank."
            )
        elif len(set_caps) == 3 and sum(set_caps) > total:
            errors["maxParticipantsM"] = (
                "The gender caps must not add up to more than the total capacity."
            )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class OnboardingStepSerializer(serializers.Serializer):
    """Read shape of one checklist step, built from a (possibly missing) row."""

    key = serializers.CharField()
    label = serializers.CharField()
    done = serializers.BooleanField()
    doneAt = serializers.DateTimeField(allow_null=True)
    doneBy = serializers.CharField()
    notes = serializers.CharField()


class OnboardingStepUpdateSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=PartnerOnboardingStep.Key.choices)
    done = serializers.BooleanField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)


class OnboardingUpdateSerializer(serializers.Serializer):
    steps = OnboardingStepUpdateSerializer(many=True, allow_empty=False)
