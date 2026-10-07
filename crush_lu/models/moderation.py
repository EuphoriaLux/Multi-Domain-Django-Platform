"""
Peer safety models — user-to-user blocking and reporting.

These are the Trust & Safety primitives an online discovery surface needs that
the in-person/coach-mediated product never required (review finding C2):

- ``UserBlock``: a one-directional record (``blocker`` blocked ``blocked``) that
  is enforced *symmetrically* — once A blocks B, neither sees the other anywhere
  (Connect Week, coach picks, event connections). The symmetric query mirrors
  ``existing_connection_subq`` pattern in ``services.crush_connect``; reuse the
  helpers in ``crush_lu.services.blocking`` rather than hand-rolling the
  ``Q(...) | Q(...)`` at each call site.

- ``UserReport``: a moderation-queue record. Filing a report does NOT auto-hide
  anyone (that's what a block is for) — it lands in the admin queue
  (``UserReportAdmin``) for a coach/staff member to action, optionally flipping
  the reported user's ``CrushConnectMembership.excluded_by_coach`` panic button.

Blocking is silent: the blocked user is never notified (standard practice).
"""

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class UserBlockQuerySet(models.QuerySet):
    """Symmetric-pair helpers, mirroring ``EventConnectionQuerySet``."""

    def involving(self, user):
        """Every block where ``user`` is on either side of the pair."""
        return self.filter(Q(blocker=user) | Q(blocked=user))

    def between(self, user_a, user_b):
        """Blocks in either direction between two users."""
        return self.filter(
            Q(blocker=user_a, blocked=user_b) | Q(blocker=user_b, blocked=user_a)
        )

    def blocked_ids_for(self, user):
        """Set of counterpart user ids ``user`` can no longer see (symmetric).

        Returns the *other* side of every block involving ``user`` — both the
        people they blocked and the people who blocked them.
        """
        pairs = self.involving(user).values_list("blocker_id", "blocked_id")
        uid = user.pk if hasattr(user, "pk") else user
        return {
            (blocked_id if blocker_id == uid else blocker_id)
            for blocker_id, blocked_id in pairs
        }


class UserBlock(models.Model):
    """One member blocking another. Enforced symmetrically everywhere."""

    REASON_CHOICES = [
        ("harassment", _("Harassment or abuse")),
        ("inappropriate", _("Inappropriate content")),
        ("fake", _("Fake or suspicious profile")),
        ("not_interested", _("Just not interested")),
        ("other", _("Other")),
    ]

    blocker = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="blocks_made",
    )
    blocked = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="blocks_received",
    )
    reason = models.CharField(
        max_length=20,
        choices=REASON_CHOICES,
        blank=True,
        help_text=_("Optional — why the member blocked them (private)."),
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = UserBlockQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("User Block")
        verbose_name_plural = _("User Blocks")
        constraints = [
            models.UniqueConstraint(
                fields=["blocker", "blocked"], name="userblock_unique_pair"
            ),
            models.CheckConstraint(
                condition=~Q(blocker=models.F("blocked")),
                name="userblock_no_self",
            ),
        ]
        indexes = [
            models.Index(fields=["blocked"], name="userblock_blocked_idx"),
        ]

    def __str__(self):
        return f"{self.blocker_id} ⛔ {self.blocked_id}"


class UserReport(models.Model):
    """A member's report about another member, for the moderation queue."""

    REASON_CHOICES = [
        ("harassment", _("Harassment or abuse")),
        ("fake_profile", _("Fake or impersonating profile")),
        ("inappropriate_photos", _("Inappropriate photos")),
        ("spam", _("Spam or solicitation")),
        ("underage", _("Appears underage")),
        ("other", _("Other")),
    ]

    SOURCE_CHOICES = [
        ("spark", _("Curiosity Spark (legacy)")),
        ("drop", _("Daily Drop (legacy)")),
        ("connection", _("Event connection")),
        ("message", _("Message")),
        ("profile", _("Profile")),
        ("connect_week", _("Connect Week")),
        ("coach_pick", _("Coach pick")),
        ("connect_chat", _("Connect chat")),
    ]

    STATUS_CHOICES = [
        ("open", _("Open")),
        ("reviewing", _("Reviewing")),
        ("actioned", _("Actioned")),
        ("dismissed", _("Dismissed")),
    ]

    reporter = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="reports_made",
    )
    reported_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="reports_received",
    )
    reason = models.CharField(max_length=24, choices=REASON_CHOICES)
    details = models.TextField(
        blank=True,
        help_text=_("Free-text context the reporter added."),
    )
    # Lightweight context pointer (no GenericFK) — which surface the report came
    # from and the pk of the originating object, for the moderator's triage.
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, blank=True)
    source_id = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default="open",
        db_index=True,
    )
    handled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports_handled",
        help_text=_("Staff/coach who actioned or dismissed the report."),
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(
        blank=True,
        help_text=_("Internal note on how the report was resolved (audit trail)."),
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("User Report")
        verbose_name_plural = _("User Reports")
        indexes = [
            models.Index(fields=["status", "created_at"], name="userreport_queue_idx"),
        ]

    def __str__(self):
        return f"{self.reporter_id} ⚑ {self.reported_user_id} ({self.status})"


class ProfilePhotoReviewLog(models.Model):
    """Audit log of coach photo review decisions in the swipe queue."""

    DECISION_CHOICES = [
        ("approved", _("Approved / Authentic")),
        ("flagged_fake", _("Flagged Fake or Suspicious")),
        ("needs_revision", _("Needs Revision")),
        ("skipped", _("Skipped")),
    ]

    REASON_CHOICES = [
        ("clear_authentic", _("Clear and authentic photo")),
        ("fake_profile", _("Fake or impersonating profile")),
        ("inappropriate", _("Inappropriate or explicit content")),
        ("unclear_face", _("Face unclear or covered")),
        ("group_photo", _("Group photo / cannot identify member")),
        ("other", _("Other")),
    ]

    profile = models.ForeignKey(
        "crush_lu.CrushProfile",
        on_delete=models.CASCADE,
        related_name="photo_review_logs",
    )
    photo_field = models.CharField(
        max_length=7,
        choices=(
            ("photo_1", _("Primary photo")),
            ("photo_2", _("Photo 2")),
            ("photo_3", _("Photo 3")),
        ),
        default="photo_1",
    )
    coach = models.ForeignKey(
        "crush_lu.CrushCoach",
        on_delete=models.CASCADE,
        related_name="photo_review_logs",
    )
    photo_key = models.CharField(max_length=255)
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES)
    reason = models.CharField(max_length=50, blank=True, choices=REASON_CHOICES)
    notes = models.CharField(max_length=255, blank=True)
    previous_status = models.CharField(max_length=20, blank=True)
    decision_at = models.DateTimeField(default=timezone.now)
    undone_at = models.DateTimeField(null=True, blank=True)
    revision_notification_state = models.CharField(
        max_length=20, blank=True, default="", editable=False
    )
    exclusion_created = models.BooleanField(default=False)
    # Whether this decision created the Connect membership that carries the
    # exclusion, so Undo can remove a row the member never started.
    membership_created = models.BooleanField(default=False)
    withdrawn_picks = models.JSONField(default=list, blank=True)
    report = models.ForeignKey(
        UserReport, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Profile Photo Review Log")
        verbose_name_plural = _("Profile Photo Review Logs")
        indexes = [
            models.Index(fields=["coach", "-created_at"], name="photo_rev_coach_idx"),
            models.Index(fields=["profile", "-created_at"], name="photo_rev_prof_idx"),
        ]

    def __str__(self):
        return f"{self.coach} -> {self.profile} {self.photo_field} ({self.decision})"


class ProfilePhotoReviewState(models.Model):
    """Current coach decision for one exact profile photo file."""

    PHOTO_FIELDS = (
        ("photo_1", _("Primary photo")),
        ("photo_2", _("Photo 2")),
        ("photo_3", _("Photo 3")),
    )
    STATUS_CHOICES = [
        ("approved", _("Coach reviewed")),
        ("needs_revision", _("Needs revision")),
        ("flagged_fake", _("Flagged fake or suspicious")),
    ]

    profile = models.ForeignKey(
        "crush_lu.CrushProfile",
        on_delete=models.CASCADE,
        related_name="photo_review_states",
    )
    photo_field = models.CharField(max_length=7, choices=PHOTO_FIELDS)
    photo_key = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    reviewed_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey(
        "crush_lu.CrushCoach",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="photo_review_states",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("profile", "photo_field"),
                name="unique_photo_review_state_per_slot",
            )
        ]
        indexes = [
            models.Index(
                fields=("photo_field", "status"),
                name="photo_state_field_status_idx",
            )
        ]

    def __str__(self):
        return f"{self.profile} {self.photo_field}: {self.status}"
