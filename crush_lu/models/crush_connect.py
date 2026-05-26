"""
Crush Connect models.

- ``CrushConnectWaitlist``: pre-launch waitlist for users interested in Crush Connect.
- ``SparkPrompt``: coach-authored prompts a sender answers when sending a
  Curiosity Spark (M1 of the Crush Connect rollout). The translatable ``text``
  field is what the sender sees as the question they're answering.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class CrushConnectWaitlist(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crush_connect_waitlist",
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    notification_preference = models.BooleanField(
        default=True,
        help_text=_("Wants to be notified when Crush Connect launches"),
    )

    class Meta:
        ordering = ["joined_at"]
        verbose_name = _("Crush Connect Waitlist Entry")
        verbose_name_plural = _("Crush Connect Waitlist")

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - #{self.waitlist_position}"

    @property
    def is_eligible(self):
        """Approved profile + at least 1 attended event."""
        from .events import EventRegistration

        has_approved_profile = (
            hasattr(self.user, "crushprofile") and self.user.crushprofile.is_approved
        )
        has_attended_event = EventRegistration.objects.filter(
            user=self.user, status="attended"
        ).exists()
        return has_approved_profile and has_attended_event

    @property
    def waitlist_position(self):
        return (
            CrushConnectWaitlist.objects.filter(joined_at__lt=self.joined_at).count()
            + 1
        )


class CrushConnectMembership(models.Model):
    """
    Per-user opt-in state for Crush Connect.

    Crush Connect is opt-in: an approved-and-attended member is *eligible* to
    onboard, but they only appear in others' Drops (and only get a Drop of their
    own) once they've completed Connect-specific onboarding. The onboarding
    flow itself ships in a later milestone (M4/M5); M1 only needs the schema
    so the eligible-pool service can require ``onboarded_at IS NOT NULL``.

    Fields written in later milestones (M4+) will include the user's coach-
    curated Story answer and any other Connect-specific profile data. Storing
    them on this model — not on ``CrushProfile`` — keeps the regular profile
    surface clean for members who never opt into Connect.

    Coach panic-button: ``excluded_by_coach`` removes a member from every
    other user's pool (and prevents their own Drop from rendering) without
    revoking their core profile approval. Use ``exclusion_reason`` for the
    audit trail.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crush_connect_membership",
    )
    onboarded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Set when the user completes Crush Connect onboarding. Null = waitlisted/not opted-in."),
    )

    # Coach panic button
    excluded_by_coach = models.BooleanField(
        default=False,
        help_text=_("Coach exclusion — removes the user from every Crush Connect pool"),
    )
    excluded_at = models.DateTimeField(null=True, blank=True)
    excluded_by = models.ForeignKey(
        "crush_lu.CrushCoach",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connect_exclusions_made",
    )
    exclusion_reason = models.TextField(
        blank=True,
        help_text=_("Why this user was excluded (audit trail; never shown to the user)"),
    )

    # Connect-specific onboarding content (populated by the M4 onboarding flow).
    # The "Story" is the one coach-curated answer that appears on the user's
    # Drop card — the single line a viewer reads before deciding whether to Spark.
    story_prompt = models.ForeignKey(
        "crush_lu.SparkPrompt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="story_owners",
        help_text=_("The prompt this member chose to answer for their Drop card"),
    )
    story_answer = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("One-line answer shown on the member's Drop card"),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Crush Connect Membership")
        verbose_name_plural = _("Crush Connect Memberships")

    def __str__(self):
        state = "onboarded" if self.onboarded_at else "pending onboarding"
        if self.excluded_by_coach:
            state += " (excluded)"
        return f"{self.user} — {state}"

    @property
    def is_onboarded(self) -> bool:
        return self.onboarded_at is not None and not self.excluded_by_coach


class ConnectDailyDrop(models.Model):
    """
    Immutable per-day snapshot of who appeared in a user's Crush Connect Drop.

    The Drop is computed once per day (lazily, on first view) and pinned for
    24 hours so refreshing the page never re-rolls the cards. The snapshot
    also gives coaches an audit trail and lets M5 enforce "you can only Spark
    someone who was actually surfaced to you".

    The selection itself lives in ``services.crush_connect.get_or_create_daily_drop``.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_drops",
    )
    drop_date = models.DateField(
        help_text=_("The local date this Drop was generated for"),
    )
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="connect_drops_appeared_in",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Crush Connect Daily Drop")
        verbose_name_plural = _("Crush Connect Daily Drops")
        ordering = ["-drop_date", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "drop_date"], name="connect_drop_unique_per_day"
            )
        ]

    def __str__(self):
        return f"{self.user} — {self.drop_date.isoformat()} ({self.recipients.count()} cards)"


class SparkPrompt(models.Model):
    """
    A coach-authored question the sender answers when sending a Curiosity Spark.

    Example texts:
        - "What in their profile made you curious?"
        - "What would your perfect first meetup look like?"
        - "What's a small thing that delights you?"

    ``weight`` controls the rotation: a prompt with weight=2 is twice as likely
    to be surfaced as a prompt with weight=1. Set ``is_active=False`` to retire
    a prompt without deleting it (preserves historical Sparks that reference it).
    """

    text = models.CharField(
        max_length=200,
        help_text=_("Prompt text shown to the sender (translated via modeltranslation)"),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Inactive prompts stop being offered to senders but stay linked from historical Sparks"),
    )
    weight = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Rotation weight (higher = more often)"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "-weight", "id"]
        verbose_name = _("Spark Prompt")
        verbose_name_plural = _("Spark Prompts")

    def __str__(self):
        return self.text
