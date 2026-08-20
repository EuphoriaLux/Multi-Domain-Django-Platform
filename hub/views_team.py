"""Read-only team-directory endpoint backing the CRM's ``/team`` page.

There is no hub-only team model. The roster is the live set of active
``crush_lu.CrushCoach`` rows -- the same "hub reads crush_lu live" pattern
as ``SocialUpcomingEventsView`` (``hub/views_social.py``) and
``SocialPost.featured_profile`` (``hub/models.py``): a cross-app FK/query
into crush_lu rather than a duplicated hub model, because the SPA has no
hub-only write state to persist here (see ``hub/serializers.py``'s
``TeamMemberSerializer`` docstring for the two fields -- ``role`` and
``presence`` -- that had to be derived/placeholder-ed for lack of a
CrushCoach-native source).
"""

from django.db.models import Count
from rest_framework import generics
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from crush_lu.models import CrushCoach

from .serializers import TeamMemberSerializer


class TeamMembersView(generics.ListAPIView):
    """Staff-only list endpoint using the hub's ``{"items": [...]}`` envelope.

    Mirrors ``FinanceListView`` (``hub/views_finance.py``) -- kept as a
    self-contained ``list()`` override here rather than importing that class,
    since it's a finance-specific name for a shape every hub list view reuses.
    """

    permission_classes = [IsAdminUser]
    serializer_class = TeamMemberSerializer

    def get_queryset(self):
        return (
            CrushCoach.objects.filter(is_active=True)
            .select_related("user")
            .annotate(events_count=Count("assigned_events", distinct=True))
            .order_by("created_at")
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response({"items": serializer.data})
