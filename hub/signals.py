"""Lifecycle safeguards for Hub-owned records linked to other apps."""

from django.db.models import Q
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from crush_lu.models import MeetupEvent

from .models import SocialPost


@receiver(pre_delete, sender=MeetupEvent)
def delete_undispatched_event_posts(sender, instance, using, **kwargs):
    """Delete stale drafts while preserving external-publication history."""

    dispatched = Q(
        status__in=[SocialPost.Status.SCHEDULED, SocialPost.Status.PUBLISHED]
    ) | ~Q(buffer_id="")
    (
        SocialPost.objects.using(using)
        .filter(source_event=instance)
        .exclude(dispatched)
        .delete()
    )
