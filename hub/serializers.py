from rest_framework import serializers

from .constants import SOCIAL_CONTENT_MAX_LENGTH
from .models import (
    HubRequest,
    HubResource,
    HubTimelineEvent,
    Location,
    LocationContact,
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
