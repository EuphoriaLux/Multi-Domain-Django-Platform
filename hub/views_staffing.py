"""Staff API for event staffing and coach availabilities on hub.crush.lu."""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from crush_lu.models import CrushCoach, MeetupEvent
from .models import EventCoachAvailability
from .serializers_staffing import (
    EventCoachAvailabilitySerializer,
    HubEventSerializer,
)


class HubEventsView(APIView):
    """List published events for staff & coach planning."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        include_past_param = request.query_params.get("include_past", "true").lower()
        include_past = include_past_param in ("true", "1", "yes")

        qs = MeetupEvent.objects.filter(
            is_published=True,
            is_cancelled=False,
            is_private_invitation=False,
        )
        if not include_past:
            qs = qs.filter(date_time__gte=timezone.now())

        events = qs.order_by("date_time")
        serializer = HubEventSerializer(events, many=True, context={"request": request})
        return Response({"items": serializer.data})


class EventAvailabilitiesView(APIView):
    """List coach availabilities across events, including currently assigned coaches."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        stored_avails = EventCoachAvailability.objects.select_related("user").all()
        items = list(EventCoachAvailabilitySerializer(stored_avails, many=True).data)

        # Surface coaches already attached to active events via crush_lu admin
        existing_keys = {
            (item["eventId"], item["coachEmail"].lower())
            for item in items
            if item.get("coachEmail")
        }

        events_with_coaches = (
            MeetupEvent.objects.filter(
                is_published=True,
                is_cancelled=False,
                is_private_invitation=False,
            )
            .prefetch_related("coaches__user")
            .filter(coaches__isnull=False)
            .distinct()
        )

        for event in events_with_coaches:
            for coach in event.coaches.all():
                email = coach.user.email.lower() if coach.user.email else ""
                if (str(event.pk), email) not in existing_keys:
                    items.append(
                        {
                            "id": f"coach-{event.pk}-{coach.pk}",
                            "eventId": str(event.pk),
                            "coachName": coach.user.get_full_name()
                            or coach.user.username,
                            "coachEmail": coach.user.email,
                            "role": EventCoachAvailability.Role.ANIMATION,
                            "status": EventCoachAvailability.Status.ASSIGNED,
                            "note": "",
                            "declaredAt": event.date_time.isoformat(),
                            "assignedAt": event.date_time.isoformat(),
                        }
                    )
                    existing_keys.add((str(event.pk), email))

        return Response({"items": items})


class EventAvailabilityDeclareView(APIView):
    """A coach declares or withdraws availability for an event."""

    permission_classes = [IsAdminUser]

    def post(self, request, event_id):
        event = get_object_or_404(MeetupEvent, pk=event_id)
        role = request.data.get("role", EventCoachAvailability.Role.ANIMATION)
        if role not in EventCoachAvailability.Role.values:
            role = EventCoachAvailability.Role.ANIMATION
        note = request.data.get("note", "")

        coach_name = (
            request.user.get_full_name()
            if hasattr(request.user, "get_full_name")
            else ""
        ) or request.user.username

        avail, created = EventCoachAvailability.objects.update_or_create(
            event=event,
            user=request.user,
            defaults={
                "role": role,
                "note": note,
                "status": EventCoachAvailability.Status.AVAILABLE,
                "coach_name": coach_name,
            },
        )
        serializer = EventCoachAvailabilitySerializer(avail)
        return Response(
            {"item": serializer.data},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, event_id):
        avail = EventCoachAvailability.objects.filter(
            event_id=event_id, user=request.user
        ).first()
        if avail:
            if avail.status == EventCoachAvailability.Status.ASSIGNED:
                if hasattr(request.user, "crushcoach"):
                    avail.event.coaches.remove(request.user.crushcoach)
            avail.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EventAvailabilityStatusView(APIView):
    """Admin updates the status of a coach availability (assigned, available, declined)."""

    permission_classes = [IsAdminUser]

    def patch(self, request, event_id, availability_id):
        new_status = request.data.get("status")
        if new_status not in EventCoachAvailability.Status.values:
            return Response(
                {"error": f"Invalid status '{new_status}'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        avail_id_str = str(availability_id)
        if avail_id_str.startswith("coach-"):
            parts = avail_id_str.split("-")
            coach_pk = int(parts[2])
            coach = get_object_or_404(CrushCoach, pk=coach_pk)
            event = get_object_or_404(MeetupEvent, pk=event_id)
            avail, _ = EventCoachAvailability.objects.get_or_create(
                event=event,
                user=coach.user,
                defaults={
                    "role": EventCoachAvailability.Role.ANIMATION,
                    "status": EventCoachAvailability.Status.ASSIGNED,
                    "coach_name": coach.user.get_full_name() or coach.user.username,
                    "assigned_at": timezone.now(),
                },
            )
        else:
            avail = get_object_or_404(
                EventCoachAvailability,
                pk=int(avail_id_str),
                event_id=event_id,
            )

        avail.status = new_status
        if new_status == EventCoachAvailability.Status.ASSIGNED:
            avail.assigned_at = timezone.now()
            if hasattr(avail.user, "crushcoach"):
                avail.event.coaches.add(avail.user.crushcoach)
        else:
            avail.assigned_at = None
            if hasattr(avail.user, "crushcoach"):
                avail.event.coaches.remove(avail.user.crushcoach)

        avail.save()
        serializer = EventCoachAvailabilitySerializer(avail)
        return Response({"item": serializer.data})
