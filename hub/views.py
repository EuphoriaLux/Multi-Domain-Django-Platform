from django.db.models import Q
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import HubProfile, HubRequest, HubResource, HubTimelineEvent
from .serializers import (
    HubRequestSerializer,
    HubResourceSerializer,
    HubTimelineEventSerializer,
)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def _customer_payload(self, user):
        profile, _ = HubProfile.objects.get_or_create(user=user)
        primary_contact = (
            profile.primary_contact
            or user.get_full_name().strip()
            or user.get_username()
        )
        return {
            "customer": {
                "organization": profile.organization,
                "primaryContact": primary_contact,
                "email": user.email or "",
                "phone": profile.phone,
            }
        }

    def get(self, request):
        return Response(self._customer_payload(request.user))

    def patch(self, request):
        profile, _ = HubProfile.objects.get_or_create(user=request.user)
        customer = (request.data or {}).get("customer", {})

        if "organization" in customer:
            profile.organization = customer["organization"] or ""
        if "primaryContact" in customer:
            profile.primary_contact = customer["primaryContact"] or ""
        if "phone" in customer:
            profile.phone = customer["phone"] or ""
        if "email" in customer and customer["email"]:
            request.user.email = customer["email"]
            request.user.save(update_fields=["email"])
        profile.save()

        return Response(self._customer_payload(request.user))


class RequestsView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = HubRequestSerializer

    def get_queryset(self):
        return HubRequest.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response({"items": serializer.data})

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ResourcesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = HubResource.objects.filter(
            Q(is_public=True) | Q(audience=request.user)
        ).distinct()
        serializer = HubResourceSerializer(queryset, many=True)
        return Response({"items": serializer.data})


class TimelineView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = HubTimelineEvent.objects.filter(user=request.user)
        serializer = HubTimelineEventSerializer(queryset, many=True)
        return Response({"items": serializer.data})
