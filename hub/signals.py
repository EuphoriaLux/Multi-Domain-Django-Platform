"""Lifecycle safeguards for Hub-owned records linked to other apps."""

from django.db.models import Q
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from crush_lu.models import MeetupEvent

from .models import SocialPost


@receiver(pre_delete, sender=MeetupEvent)
def delete_undispatched_event_posts(sender, instance, using, **kwargs):
    """Delete every post the event never dispatched, whatever its review state.

    This is wider than "drafts" on purpose: a PENDING_REVIEW or APPROVED post
    goes too. `source_event` is SET_NULL, so a surviving post loses the only
    record that it came from an event — and `SocialPostDetailView.patch` gates
    its eligibility re-check on `post.source_event_id`, so an orphan skips the
    check entirely and can still be scheduled, publishing copy whose embedded
    event URL now 404s. Reviewed copy for a deleted event is not salvageable
    work, so it goes with the event.

    Posts that already reached Buffer (SCHEDULED, PUBLISHED, or holding a
    `buffer_id`) are kept: the external publication outlives the event row, and
    their IDs and status history are what reconciles it.
    """

    dispatched = Q(
        status__in=[SocialPost.Status.SCHEDULED, SocialPost.Status.PUBLISHED]
    ) | ~Q(buffer_id="")
    (
        SocialPost.objects.using(using)
        .filter(source_event=instance)
        .exclude(dispatched)
        .delete()
    )
