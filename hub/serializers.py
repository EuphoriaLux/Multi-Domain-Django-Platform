from rest_framework import serializers

from .models import (
    HubRequest,
    HubResource,
    HubTimelineEvent,
    WhatsAppInboundMessage,
    WhatsAppMessage,
    SocialPost,
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

    class Meta:
        model = SocialPost
        fields = [
            "id",
            "created_by",
            "featured_profile_id",
            "pillar",
            "language",
            "platforms",
            "buffer_profile_ids",
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
            "buffer_id",
            "article_id",
            "status_history",
            "created_at",
            "updated_at",
        ]

    def get_created_by(self, obj):
        return obj.user.get_username() if obj.user else "system"

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
