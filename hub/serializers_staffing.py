"""Serializers for Hub events and coach availability staffing endpoints."""

from rest_framework import serializers

from crush_lu.models import MeetupEvent
from crush_lu.utils.i18n import build_absolute_url
from .models import EventCoachAvailability


class HubEventSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="pk", read_only=True)
    title = serializers.CharField(read_only=True)
    eventType = serializers.CharField(source="get_event_type_display", read_only=True)
    dateTime = serializers.DateTimeField(source="date_time", read_only=True)
    location = serializers.CharField(read_only=True)
    imageUrl = serializers.SerializerMethodField()
    eventUrl = serializers.SerializerMethodField()
    coachesNeeded = serializers.SerializerMethodField()

    class Meta:
        model = MeetupEvent
        fields = [
            "id",
            "title",
            "eventType",
            "dateTime",
            "location",
            "imageUrl",
            "eventUrl",
            "coachesNeeded",
        ]

    def get_imageUrl(self, obj):
        if obj.image:
            url = obj.image.url
            request = self.context.get("request")
            if request and url.startswith("/"):
                return request.build_absolute_uri(url)
            return url
        return None

    def get_eventUrl(self, obj):
        available_languages = [
            lang for lang in ["fr", "en", "de"] if getattr(obj, f"title_{lang}", None)
        ]
        lang = available_languages[0] if available_languages else "fr"
        domain = "crush.lu"
        request = self.context.get("request")
        if request:
            host = request.get_host().split(":")[0].lower()
            if (
                host in ("crush.lu", "test.crush.lu", "localhost")
                or host.endswith(".crush.lu")
                or host.endswith(".azurewebsites.net")
            ):
                domain = host
        try:
            return build_absolute_url(
                "crush_lu:event_detail",
                lang=lang,
                domain=domain,
                kwargs={"event_id": obj.pk},
            )
        except Exception:
            return f"/events/{obj.pk}/"

    def get_coachesNeeded(self, obj):
        return 2


class EventCoachAvailabilitySerializer(serializers.ModelSerializer):
    id = serializers.SerializerMethodField()
    eventId = serializers.CharField(source="event_id", read_only=True)
    coachName = serializers.SerializerMethodField()
    coachEmail = serializers.CharField(source="user.email", read_only=True)
    role = serializers.CharField()
    status = serializers.CharField()
    note = serializers.CharField(required=False, allow_blank=True, default="")
    declaredAt = serializers.DateTimeField(source="declared_at", read_only=True)
    assignedAt = serializers.DateTimeField(
        source="assigned_at", read_only=True, allow_null=True
    )

    class Meta:
        model = EventCoachAvailability
        fields = [
            "id",
            "eventId",
            "coachName",
            "coachEmail",
            "role",
            "status",
            "note",
            "declaredAt",
            "assignedAt",
        ]

    def get_id(self, obj):
        return str(obj.pk)

    def get_coachName(self, obj):
        return (
            obj.coach_name
            or (obj.user.get_full_name() if hasattr(obj.user, "get_full_name") else "")
            or obj.user.username
        )
