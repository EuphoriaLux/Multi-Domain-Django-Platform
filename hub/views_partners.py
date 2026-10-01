"""Staff API for partners (``hub.Location``), their offers and onboarding.

Spec: ai-memory-hub/specs/2026-10-01-hub-partner-and-offers.md
"""

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Location, PartnerOffer, PartnerOnboardingStep
from .partner_services import build_event_prefill
from .serializers_partners import (
    LocationSerializer,
    OnboardingStepSerializer,
    OnboardingUpdateSerializer,
    PartnerOfferSerializer,
)


def _partners():
    return Location.objects.prefetch_related("contacts", "offers", "onboarding_steps")


class PartnersView(generics.ListCreateAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = LocationSerializer

    def get_queryset(self):
        return _partners()

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            self.filter_queryset(self.get_queryset()), many=True
        )
        return Response({"items": serializer.data})

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        location = serializer.save()
        # Re-read through the prefetching queryset so the response is complete.
        output = self.get_serializer(_partners().get(pk=location.pk))
        return Response(output.data, status=status.HTTP_201_CREATED)


class PartnerDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = LocationSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return _partners()

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        serializer = self.get_serializer(
            self.get_object(), data=request.data, partial=partial
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        # ``get_object`` prefetched before the write; read again for fresh data.
        return Response(
            self.get_serializer(self.get_queryset().get(pk=kwargs["pk"])).data
        )

    def destroy(self, request, *args, **kwargs):
        partner = self.get_object()
        # Money rows and (soon) events point at a partner; deleting one erases
        # that history link, so steer the caller to the Archived stage instead.
        if partner.payments_out.exists():
            return Response(
                {
                    "detail": "This partner has recorded payments. "
                    "Set its stage to Archived instead of deleting it."
                },
                status=status.HTTP_409_CONFLICT,
            )
        partner.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PartnerOffersView(generics.ListCreateAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = PartnerOfferSerializer

    def get_partner(self):
        return get_object_or_404(Location, pk=self.kwargs["pk"])

    def get_queryset(self):
        return PartnerOffer.objects.filter(location_id=self.get_partner().pk)

    def list(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response({"items": serializer.data})

    def perform_create(self, serializer):
        serializer.save(location=self.get_partner())


class PartnerOfferDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = PartnerOfferSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]
    lookup_url_kwarg = "offer_pk"

    def get_queryset(self):
        return PartnerOffer.objects.filter(location_id=self.kwargs["pk"])


class OfferEventDraftView(APIView):
    """A ``MeetupEvent``-shaped prefill for an offer. Creates nothing."""

    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        offer = get_object_or_404(
            PartnerOffer.objects.select_related("location"), pk=pk
        )
        return Response(build_event_prefill(offer))


def _onboarding_payload(partner):
    rows = {
        step.key: step for step in partner.onboarding_steps.select_related("done_by")
    }
    items = []
    for key, label in PartnerOnboardingStep.Key.choices:
        step = rows.get(key)
        done_by = ""
        if step and step.done_by:
            done_by = (
                step.done_by.get_full_name().strip() or step.done_by.get_username()
            )
        items.append(
            {
                "key": key,
                "label": label,
                "done": bool(step and step.done_at),
                "doneAt": step.done_at if step else None,
                "doneBy": done_by,
                "notes": step.notes if step else "",
            }
        )
    return OnboardingStepSerializer(items, many=True).data


class PartnerOnboardingView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        partner = get_object_or_404(Location, pk=pk)
        return Response({"items": _onboarding_payload(partner)})

    @transaction.atomic
    def patch(self, request, pk):
        partner = get_object_or_404(Location, pk=pk)
        serializer = OnboardingUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for change in serializer.validated_data["steps"]:
            step, _ = PartnerOnboardingStep.objects.get_or_create(
                location=partner, key=change["key"]
            )
            if "notes" in change:
                step.notes = change["notes"]
            if "done" in change:
                already_done = step.done_at is not None
                if change["done"] and not already_done:
                    step.done_at = timezone.now()
                    step.done_by = request.user
                elif not change["done"]:
                    step.done_at = None
                    step.done_by = None
            step.save()
        return Response({"items": _onboarding_payload(partner)})
