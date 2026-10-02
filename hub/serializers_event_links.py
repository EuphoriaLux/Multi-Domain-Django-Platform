"""Staff-only event attribution; no event content is changed by linking."""

from collections.abc import Mapping

from rest_framework import serializers

from crush_lu.models import MeetupEvent
from .models import Location, PartnerOffer


class PartnerEventSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    dateTime = serializers.DateTimeField(source="date_time", read_only=True)
    durationMinutes = serializers.IntegerField(
        source="duration_minutes", read_only=True
    )
    eventType = serializers.CharField(source="event_type", read_only=True)
    partnerId = serializers.CharField(
        source="partner_id", read_only=True, allow_null=True
    )
    offerId = serializers.CharField(source="offer_id", read_only=True, allow_null=True)
    offerName = serializers.CharField(source="offer.name", read_only=True, default="")
    isPublished = serializers.BooleanField(source="is_published", read_only=True)
    isCancelled = serializers.BooleanField(source="is_cancelled", read_only=True)
    maxParticipants = serializers.IntegerField(
        source="max_participants", read_only=True
    )
    seatHolders = serializers.IntegerField(
        source="confirmed_count_annotated", read_only=True
    )
    applications = serializers.IntegerField(
        source="applied_count_annotated", read_only=True
    )
    waitlisted = serializers.IntegerField(
        source="waitlist_count_annotated", read_only=True
    )
    attended = serializers.IntegerField(
        source="attended_count_annotated", read_only=True
    )

    class Meta:
        model = MeetupEvent
        fields = (
            "id",
            "title",
            "dateTime",
            "durationMinutes",
            "eventType",
            "location",
            "partnerId",
            "offerId",
            "offerName",
            "isPublished",
            "isCancelled",
            "maxParticipants",
            "seatHolders",
            "applications",
            "waitlisted",
            "attended",
        )


class EventPartnerLinkSerializer(serializers.Serializer):
    partnerId = serializers.PrimaryKeyRelatedField(queryset=Location.objects.all())
    offerId = serializers.PrimaryKeyRelatedField(
        queryset=PartnerOffer.objects.all(), required=False, allow_null=True
    )

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            return super().to_internal_value(data)
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {
                    key: "Only partnerId and offerId can be changed here."
                    for key in unknown
                }
            )
        return super().to_internal_value(data)

    def validate(self, attrs):
        # Re-linking without an offer clears old offer attribution, never guesses.
        offer = attrs.get("offerId")
        if offer and offer.location_id != attrs["partnerId"].pk:
            raise serializers.ValidationError(
                {"offerId": "This offer does not belong to the selected partner."}
            )
        return attrs
