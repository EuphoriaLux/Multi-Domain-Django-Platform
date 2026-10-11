"""Current-member reads shared only within one dashboard/template request."""

from django.db.models import Q
from django.utils.functional import cached_property

from crush_lu.models import CrushProfile, EventConnection, ProfileSubmission

_REQUEST_DATA = "_crush_member_context_data"


class MemberContextData:
    def __init__(self, user):
        self.user = user
        self.user_id = user.pk

    @cached_property
    def profile(self):
        try:
            return CrushProfile.objects.select_related("assigned_coach__user").get(
                user=self.user
            )
        except CrushProfile.DoesNotExist:
            return None

    @cached_property
    def submission(self):
        if self.profile is None:
            return None
        return ProfileSubmission.latest_for_profile(
            self.profile, select_related=("coach__user",)
        )

    @cached_property
    def hidden_counterpart_ids(self):
        # Resolve the existing policy helpers lazily, including their failures:
        # a failed lookup is not cached and the navbar retains its safe fallback.
        from .blocking import blocked_user_ids
        from .event_lobby import hidden_encounter_user_ids

        return frozenset(
            blocked_user_ids(self.user) | hidden_encounter_user_ids(self.user)
        )

    @cached_property
    def connection_count(self):
        hidden_ids = self.hidden_counterpart_ids
        return (
            EventConnection.objects.active_for_user(self.user)
            .excluding_unshared_crushes()
            .exclude(Q(requester_id__in=hidden_ids) | Q(recipient_id__in=hidden_ids))
            .count()
        )


def member_context_data(request):
    """Bind lazy reads to this request's current account, never a global cache."""
    user = request.user
    data = getattr(request, _REQUEST_DATA, None)
    if data is None or data.user_id != user.pk:
        data = MemberContextData(user)
        setattr(request, _REQUEST_DATA, data)
    return data


def invalidate_member_context_data(request):
    """Discard a snapshot after a write, including a partially failed transition."""
    if hasattr(request, _REQUEST_DATA):
        delattr(request, _REQUEST_DATA)
