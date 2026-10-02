"""Link existing events to partners without altering public event snapshots."""

from django.db import transaction
from django.contrib.admin.models import CHANGE, LogEntry
from django.shortcuts import get_object_or_404
from django.utils.translation import override
from rest_framework.permissions import IsAdminUser
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from crush_lu.models import MeetupEvent
from .models import Location
from .serializers_event_links import EventPartnerLinkSerializer, PartnerEventSerializer


def _events():
    return MeetupEvent.objects.select_related("offer").with_registration_counts()


class PartnerEventsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        get_object_or_404(Location, pk=pk)
        events = _events().filter(partner_id=pk).order_by("-date_time", "-pk")
        with override("en"):
            return Response({"items": PartnerEventSerializer(events, many=True).data})


class EventPartnerLinkView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        with override("en"):
            return Response(
                PartnerEventSerializer(get_object_or_404(_events(), pk=pk)).data
            )

    @transaction.atomic
    def patch(self, request, pk):
        if not request.user.has_perm("crush_lu.change_meetupevent"):
            raise PermissionDenied("Event change permission is required.")
        event = get_object_or_404(
            MeetupEvent.objects.select_for_update(of=("self",)), pk=pk
        )
        serializer = EventPartnerLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if (
            event.partner_id
            and event.partner_id != serializer.validated_data["partnerId"].pk
        ):
            return Response(
                {
                    "detail": "This event is already linked to another partner. Review it in event administration."
                },
                status=409,
            )
        previous = (event.partner_id, event.offer_id)
        event.partner = serializer.validated_data["partnerId"]
        event.offer = serializer.validated_data.get("offerId")
        event.validate_partner_offer()
        # QuerySet.update intentionally avoids event publication/indexing signals.
        MeetupEvent.objects.filter(pk=event.pk).update(
            partner_id=event.partner_id, offer_id=event.offer_id
        )
        if previous != (event.partner_id, event.offer_id):
            LogEntry.objects.log_actions(
                user_id=request.user.pk,
                queryset=[event],
                action_flag=CHANGE,
                change_message=(
                    f"Hub partner link: partner {previous[0]} -> {event.partner_id}; "
                    f"offer {previous[1]} -> {event.offer_id}. Event content preserved."
                ),
            )
        with override("en"):
            return Response(PartnerEventSerializer(_events().get(pk=event.pk)).data)
