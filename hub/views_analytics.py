"""Staff-only aggregate analytics endpoint for hub.crush.lu."""

from django.conf import settings
from django.core.cache import cache
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .analytics_service import build_analytics_overview

ALLOWED_PERIODS = {7, 28, 90}


class AnalyticsOverviewView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        raw_days = request.query_params.get("days", "28")
        try:
            days = int(raw_days)
        except (TypeError, ValueError):
            days = 0
        if days not in ALLOWED_PERIODS:
            return Response(
                {"error": "days must be one of 7, 28, or 90."},
                status=400,
            )

        cache_key = f"hub:analytics:overview:v1:{days}"
        payload = cache.get(cache_key)
        if payload is None:
            payload = build_analytics_overview(days)
            cache.set(
                cache_key,
                payload,
                timeout=getattr(settings, "HUB_ANALYTICS_CACHE_SECONDS", 900),
            )
        response = Response(payload)
        response["Cache-Control"] = "private, no-store"
        return response
