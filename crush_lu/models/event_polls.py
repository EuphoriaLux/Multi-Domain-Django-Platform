from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from crush_lu.storage import crush_upload_path, crush_media_storage


class EventPoll(models.Model):
    """Admin-configured poll for users to vote on future event preferences."""

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    image = models.ImageField(
        upload_to=crush_upload_path("event-polls"),
        storage=crush_media_storage,
        blank=True,
        null=True,
    )
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    is_published = models.BooleanField(default=False)
    allow_multiple_choices = models.BooleanField(default=False)
    show_results_before_close = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = _("Event Poll")
        verbose_name_plural = _("Event Polls")

    def __str__(self):
        return self.title

    @property
    def is_active(self):
        """Poll is published and within date range."""
        now = timezone.now()
        return self.is_published and self.start_date <= now <= self.end_date

    @property
    def is_closed(self):
        """Poll is past its end date."""
        return timezone.now() > self.end_date


class EventPollOption(models.Model):
    """An option within an event poll."""

    poll = models.ForeignKey(
        EventPoll, on_delete=models.CASCADE, related_name="options"
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    image = models.ImageField(
        upload_to=crush_upload_path("event-polls/options"),
        storage=crush_media_storage,
        blank=True,
        null=True,
        help_text=_(
            "Uploaded image (stored in Azure). Use static_image for bundled images."
        ),
    )
    static_image = models.CharField(
        max_length=100,
        blank=True,
        help_text=_(
            "Filename in crush_lu/images/event-polls/ (e.g. speeddating.png). Deployed with collectstatic."
        ),
    )
    icon = models.CharField(max_length=50, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "pk"]
        verbose_name = _("Poll Option")
        verbose_name_plural = _("Poll Options")

    def __str__(self):
        return self.name


class EventPollVote(models.Model):
    """A user's vote on a poll option."""

    poll = models.ForeignKey(EventPoll, on_delete=models.CASCADE, related_name="votes")
    option = models.ForeignKey(
        EventPollOption, on_delete=models.CASCADE, related_name="votes"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="event_poll_votes"
    )
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("poll", "user", "option")]
        verbose_name = _("Poll Vote")
        verbose_name_plural = _("Poll Votes")

    def __str__(self):
        return f"{self.user.username} -> {self.option.name}"
