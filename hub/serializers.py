from rest_framework import serializers

from .constants import SOCIAL_CONTENT_MAX_LENGTH
from .models import (
    HubRequest,
    HubResource,
    HubTimelineEvent,
    Location,
    LocationContact,
    PaymentIn,
    PaymentOut,
    Payroll,
    Refund,
    SocialPost,
    WhatsAppInboundMessage,
    WhatsAppMessage,
)


class CustomerSerializer(serializers.Serializer):
    organization = serializers.CharField(allow_blank=True)
    primaryContact = serializers.CharField(allow_blank=True)
    email = serializers.EmailField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)


class MeSerializer(serializers.Serializer):
    customer = CustomerSerializer()


class HubRequestSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)

    class Meta:
        model = HubRequest
        fields = [
            "id",
            "subject",
            "summary",
            "category",
            "priority",
            "status",
        ]
        read_only_fields = ["id", "status"]


class HubResourceSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    updatedAt = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = HubResource
        fields = ["id", "title", "type", "summary", "updatedAt"]


class HubTimelineEventSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    date = serializers.DateTimeField(source="occurred_at", read_only=True)
    description = serializers.CharField(source="body", read_only=True)

    class Meta:
        model = HubTimelineEvent
        fields = ["id", "date", "title", "description"]


class LocationContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = LocationContact
        fields = ["name", "role", "email", "phone"]
        read_only_fields = fields


class LocationSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    maxCapacity = serializers.IntegerField(source="max_capacity", read_only=True)
    seatedCapacity = serializers.IntegerField(source="seated_capacity", read_only=True)
    hasOutdoorSpace = serializers.BooleanField(
        source="has_outdoor_space", read_only=True
    )
    hasKitchen = serializers.BooleanField(source="has_kitchen", read_only=True)
    hasPrivateRoom = serializers.BooleanField(source="has_private_room", read_only=True)
    hasSoundSystem = serializers.BooleanField(source="has_sound_system", read_only=True)
    compatibleEventTypes = serializers.JSONField(
        source="compatible_event_types", read_only=True
    )
    partnershipStage = serializers.CharField(source="partnership_stage", read_only=True)
    primaryContact = serializers.SerializerMethodField()
    accountManager = serializers.CharField(source="account_manager", read_only=True)
    commercialTerms = serializers.CharField(source="commercial_terms", read_only=True)
    partnerSince = serializers.DateField(source="partner_since", read_only=True)
    lastContactDate = serializers.SerializerMethodField()
    nextAction = serializers.CharField(source="next_action", read_only=True)
    nextActionDate = serializers.DateField(source="next_action_date", read_only=True)

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
        ]
        read_only_fields = fields

    def get_primaryContact(self, obj):
        try:
            contact = obj.primary_contact
        except LocationContact.DoesNotExist:
            return {"name": "", "role": "", "email": "", "phone": ""}
        return LocationContactSerializer(contact).data

    def get_lastContactDate(self, obj):
        return obj.last_contact_date.isoformat() if obj.last_contact_date else ""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for optional_field in ("seatedCapacity", "partnerSince", "nextActionDate"):
            if data.get(optional_field) is None:
                data.pop(optional_field, None)
        return data


class MoneyField(serializers.DecimalField):
    """A euro amount the SPA can do arithmetic on.

    DRF's ``COERCE_DECIMAL_TO_STRING`` default is on and this project never
    overrides it, so a plain ``DecimalField`` would ship ``"1250.00"``. The
    accounting page sums these values, and summing strings concatenates them,
    so every money field must opt out — doing it here keeps the next one from
    forgetting.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 10)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("coerce_to_string", False)
        kwargs.setdefault("read_only", True)
        super().__init__(**kwargs)


class FinanceSerializerMixin:
    """Drops null optionals so the payload matches the SPA's optional fields.

    Mirrors ``LocationSerializer``: nullable non-string fields are omitted
    entirely rather than sent as ``null``; blank text stays ``""``.
    """

    optional_fields = ()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for optional_field in self.optional_fields:
            if data.get(optional_field) is None:
                data.pop(optional_field, None)
        return data


class PaymentInSerializer(FinanceSerializerMixin, serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    amount = MoneyField()
    clientName = serializers.CharField(source="client_name", read_only=True)
    paymentMethod = serializers.CharField(source="payment_method", read_only=True)
    receiptUrl = serializers.CharField(source="receipt_url", read_only=True)

    class Meta:
        model = PaymentIn
        fields = [
            "id",
            "date",
            "amount",
            "source",
            "clientName",
            "status",
            "reference",
            "paymentMethod",
            "receiptUrl",
        ]
        read_only_fields = fields


class PaymentOutSerializer(FinanceSerializerMixin, serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    amount = MoneyField()
    locationId = serializers.CharField(source="location_id", read_only=True)
    paymentMethod = serializers.CharField(source="payment_method", read_only=True)
    depositStatus = serializers.CharField(source="deposit_status", read_only=True)
    receiptUrl = serializers.CharField(source="receipt_url", read_only=True)

    optional_fields = ("locationId", "depositStatus")

    class Meta:
        model = PaymentOut
        fields = [
            "id",
            "date",
            "amount",
            "locationId",
            "payee",
            "description",
            "status",
            "paymentMethod",
            "category",
            "depositStatus",
            "receiptUrl",
        ]
        read_only_fields = fields


class PayrollSerializer(FinanceSerializerMixin, serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    amount = MoneyField()
    grossSalary = MoneyField(source="gross_salary")
    employerCharges = MoneyField(source="employer_charges")
    employeeName = serializers.CharField(source="employee_name", read_only=True)
    paymentMethod = serializers.CharField(source="payment_method", read_only=True)
    receiptUrl = serializers.CharField(source="receipt_url", read_only=True)

    class Meta:
        model = Payroll
        fields = [
            "id",
            "date",
            "amount",
            "grossSalary",
            "employerCharges",
            "employeeName",
            "category",
            "description",
            "status",
            "paymentMethod",
            "receiptUrl",
        ]
        read_only_fields = fields


class RefundSerializer(FinanceSerializerMixin, serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    amount = MoneyField()
    participantName = serializers.CharField(source="participant_name", read_only=True)
    eventName = serializers.CharField(source="event_name", read_only=True)

    class Meta:
        model = Refund
        fields = [
            "id",
            "date",
            "amount",
            "participantName",
            "eventName",
            "reason",
            "status",
        ]
        read_only_fields = fields


class WhatsAppMessageSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    wa_message_id = serializers.SerializerMethodField()

    class Meta:
        model = WhatsAppMessage
        fields = [
            "id",
            "wa_message_id",
            "recipient",
            "template_name",
            "language",
            "parameters",
            "status",
            "status_history",
            "created_at",
        ]
        read_only_fields = fields

    def get_wa_message_id(self, obj):
        return obj.wa_message_id or None


class SocialPostSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    created_by = serializers.SerializerMethodField()
    featured_profile_id = serializers.CharField(read_only=True)
    source_event_id = serializers.CharField(read_only=True)
    source_event_title = serializers.SerializerMethodField()

    class Meta:
        model = SocialPost
        fields = [
            "id",
            "created_by",
            "featured_profile_id",
            "source_event_id",
            "source_event_title",
            "pillar",
            "language",
            "platforms",
            "buffer_profile_ids",
            "buffer_profile_platforms",
            "dispatched_platforms",
            "hook",
            "content",
            "media_url",
            "status",
            "scheduled_for",
            "buffer_id",
            "article_id",
            "status_history",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "featured_profile_id",
            "source_event_id",
            "source_event_title",
            "buffer_id",
            "dispatched_platforms",
            "article_id",
            "status_history",
            "created_at",
            "updated_at",
        ]

    def get_created_by(self, obj):
        return obj.user.get_username() if obj.user else "system"

    def get_source_event_title(self, obj):
        return obj.source_event.title if obj.source_event_id else None

    def validate_platforms(self, value):
        allowed = {"instagram", "facebook", "linkedin"}
        if not isinstance(value, list) or any(
            not isinstance(item, str) or item not in allowed for item in value
        ):
            raise serializers.ValidationError(
                "Platforms must be a list of supported platform names."
            )
        return list(dict.fromkeys(value))

    def validate_buffer_profile_ids(self, value):
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() or len(item) > 255
            for item in value
        ):
            raise serializers.ValidationError(
                "Buffer channel IDs must be a list of non-empty strings."
            )
        return list(dict.fromkeys(value))

    def validate_buffer_profile_platforms(self, value):
        allowed = {"instagram", "facebook", "linkedin"}
        if not isinstance(value, dict) or any(
            not isinstance(profile_id, str)
            or not profile_id.strip()
            or len(profile_id) > 255
            or not isinstance(platform, str)
            or platform not in allowed
            for profile_id, platform in value.items()
        ):
            raise serializers.ValidationError(
                "Buffer channel platforms must map channel IDs to supported platforms."
            )
        return value

    def validate_content(self, value):
        if len(value) > SOCIAL_CONTENT_MAX_LENGTH:
            raise serializers.ValidationError(
                f"Social post content cannot exceed {SOCIAL_CONTENT_MAX_LENGTH} "
                "characters."
            )
        return value


class EventCancellationSummarySerializer(serializers.Serializer):
    """One cancelled event plus its bulk cancellation aggregates.

    Backs both ``GET /hub/events/cancelled`` (one row per event) and, for the
    single event it names, the ``"event"`` key of
    ``GET /hub/events/<pk>/cancellation``. Source objects are plain
    ``MeetupEvent`` rows annotated in the view with the four aggregate
    attributes below — there is no model to serialize these off directly, see
    ``hub/views_events.py``.
    """

    id = serializers.CharField(source="pk", read_only=True)
    title = serializers.CharField(read_only=True)
    eventType = serializers.SerializerMethodField()
    dateTime = serializers.DateTimeField(source="date_time", read_only=True)
    isCancelled = serializers.BooleanField(source="is_cancelled", read_only=True)
    organiserCancellationStartedAt = serializers.DateTimeField(
        source="organiser_cancellation_started_at", read_only=True
    )
    affectedRegistrations = serializers.IntegerField(
        source="affected_registrations", read_only=True
    )
    issuedCreditsCount = serializers.IntegerField(
        source="issued_credits_count", read_only=True
    )
    issuedCreditsTotalCents = serializers.IntegerField(
        source="issued_credits_total_cents", read_only=True
    )
    openCashRefundTotalCents = serializers.IntegerField(
        source="open_cash_refund_total_cents", read_only=True
    )

    def get_eventType(self, obj):
        return obj.get_event_type_display()


class LinkedCrushCreditSerializer(serializers.Serializer):
    """The one EVENT_CANCELLED credit representing a cancelled registration.

    Deliberately thin: this is a pointer into the real ledger
    (``crush_lu.models.credits.CrushCredit``), not a copy of it. Staff follow
    up in the Crush Credit admin for anything beyond these three fields.
    """

    id = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    amountCents = serializers.IntegerField(source="amount_cents", read_only=True)
    cashRefundEligible = serializers.BooleanField(
        source="cash_refund_eligible", read_only=True
    )


class EventCancellationRegistrationSerializer(serializers.Serializer):
    """One self-cancelled registration for a cancelled event, credit attached.

    Source rows are ``EventRegistration`` instances the view annotates with
    ``linked_credit`` (a ``CrushCredit`` or ``None``) and ``open_cash_refund``
    (a bool computed from the *exact* same predicate the staff cash-refund
    queue uses — see ``CashRefundQueueFilter._open`` in
    ``crush_lu/admin/credits.py`` and ``hub/views_events.py``).
    """

    id = serializers.CharField(source="pk", read_only=True)
    userEmail = serializers.SerializerMethodField()
    status = serializers.CharField(read_only=True)
    cancelledAt = serializers.DateTimeField(source="cancelled_at", read_only=True)
    paymentConfirmed = serializers.BooleanField(
        source="payment_confirmed", read_only=True
    )
    credit = serializers.SerializerMethodField()
    openCashRefund = serializers.BooleanField(source="open_cash_refund", read_only=True)

    def get_userEmail(self, obj):
        return obj.user.email if obj.user_id else None

    def get_credit(self, obj):
        credit = getattr(obj, "linked_credit", None)
        if credit is None:
            return None
        return LinkedCrushCreditSerializer(credit).data


class WhatsAppInboundMessageSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)

    class Meta:
        model = WhatsAppInboundMessage
        fields = [
            "id",
            "wa_message_id",
            "from_number",
            "contact_name",
            "message_type",
            "text",
            "received_at",
            "is_read",
        ]
        read_only_fields = fields
