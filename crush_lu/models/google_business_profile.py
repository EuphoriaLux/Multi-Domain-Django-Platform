"""Sync state for events mirrored as Google Business Profile event posts.

One row per event that has ever been sent to the Crush.lu listing. The
server-assigned post ``name`` (``accounts/…/locations/…/localPosts/…``) is the
only handle we hold on the public post, so it lives here rather than as a
column on MeetupEvent — the same reasoning as ``EchoExperienceSync``.

The sync service (``crush_lu/services/google_business_profile.py``) also tags
every post's booking link with ``utm_content=event-<id>``, which lets a create
whose response was lost be adopted on the next sweep instead of duplicated.
"""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class GoogleLocalPostSync(models.Model):
    """Links one MeetupEvent to its Google Business Profile event post."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        # Google holds a post matching `payload_hash`.
        SYNCED = "synced", _("Synced")
        # The last call failed; `last_error` says how. Retried next sweep.
        FAILED = "failed", _("Failed")
        # The post was deleted because the event stopped qualifying
        # (unpublished, cancelled, made private) or by hand.
        REMOVED = "removed", _("Removed")

    event = models.OneToOneField(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="gbp_post_sync",
        help_text=_("The Crush.lu event this Google post mirrors."),
    )
    post_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text=_(
            "Full resource name of the Google local post. Empty until the "
            "first successful create, and cleared once the post is deleted."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    payload_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "SHA-256 of the last payload Google accepted. An unchanged event "
            "is skipped, so the hourly sweep costs no API call."
        ),
    )
    search_url = models.URLField(
        max_length=500,
        blank=True,
        default="",
        help_text=_("Google's public link to the post, as returned by the API."),
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Google Business Profile post sync")
        verbose_name_plural = _("Google Business Profile post syncs")
        ordering = ["-updated_at"]

    def __str__(self):
        label = self.post_name or _("no post")
        return f"{self.event_id} -> GBP {label} ({self.status})"

    def mark_success(self, post_name, payload_hash, search_url=""):
        now = timezone.now()
        if post_name:
            self.post_name = post_name
        if search_url:
            self.search_url = search_url[:500]
        self.payload_hash = payload_hash
        self.status = self.Status.SYNCED
        self.last_synced_at = now
        self.last_attempted_at = now
        self.last_error = ""
        self.save()

    def mark_failure(self, error):
        """Record a rejected call. `payload_hash` stays what Google last accepted."""
        self.status = self.Status.FAILED
        self.last_attempted_at = timezone.now()
        self.last_error = str(error)[:2000]
        self.save(
            update_fields=["status", "last_attempted_at", "last_error", "updated_at"]
        )

    def mark_removed(self):
        now = timezone.now()
        self.post_name = ""
        self.search_url = ""
        self.payload_hash = ""
        self.status = self.Status.REMOVED
        self.last_synced_at = now
        self.last_attempted_at = now
        self.last_error = ""
        self.save()
