"""
Crush Connect real-person check: claims, history and the current badge.

A Connect candidate no coach has seen at an event has only LuxID behind their
card. A coach can call them (video or voice) and, when the call goes well,
grant a badge other members see. Two small tables carry that:

* ``ConnectRealPersonCheck``: one row per attempt (claim, lease, outcome). Rows
  stay as history; at most one is ``open`` per profile.
* ``ConnectRealPersonBadge``: the badge as it stands now. The row exists only
  while a badge is granted.

Neither table adds a column to ``CrushProfile`` (its ``save()`` photo branch,
``from_db`` tuples and stale-instance preservation make that expensive), and
neither touches ``ProfileSubmission``, which the navbar reads as "is this
member pending or rejected".

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crush_lu.real_person_check_rules import MEDIUMS, MEDIUM_VIDEO

__all__ = ["ConnectRealPersonCheck", "ConnectRealPersonBadge"]


class ConnectRealPersonCheck(models.Model):
    """One coach attempt to confirm a Connect member is a real person.

    ``open`` is a lease: only an open row whose ``lease_expires_at`` is still in
    the future keeps the member in one coach's queue. An expired open row is
    closed as ``released`` the next time anything looks at it.
    """

    STATUS_OPEN = "open"
    STATUS_PASSED = "passed"
    STATUS_UNREACHABLE = "unreachable"
    STATUS_DECLINED = "declined"
    STATUS_RELEASED = "released"
    STATUS_SUSPECTED_FAKE = "suspected_fake"
    STATUS_REVOKED = "revoked"
    STATUS_CHOICES = [
        (STATUS_OPEN, _("Open")),
        (STATUS_PASSED, _("Passed")),
        (STATUS_UNREACHABLE, _("Unreachable")),
        (STATUS_DECLINED, _("Declined")),
        (STATUS_RELEASED, _("Released")),
        (STATUS_SUSPECTED_FAKE, _("Suspected fake")),
        (STATUS_REVOKED, _("Revoked")),
    ]
    PASSED_MEDIUM_CHOICES = [
        ("video", _("Video call")),
        ("voice", _("Voice call")),
    ]
    MEDIUM_CHOICES = [("", _("Not decided"))] + PASSED_MEDIUM_CHOICES

    profile = models.ForeignKey(
        "crush_lu.CrushProfile",
        on_delete=models.CASCADE,
        related_name="real_person_checks",
    )
    coach = models.ForeignKey(
        "crush_lu.CrushCoach",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="real_person_checks",
        help_text=_("The coach who claimed this check."),
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        db_index=True,
    )
    medium = models.CharField(
        max_length=5,
        choices=MEDIUM_CHOICES,
        blank=True,
        default="",
        help_text=_("How the coach spoke to the member (set when the check passes)."),
    )
    claimed_at = models.DateTimeField(default=timezone.now)
    lease_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "While open: when the claim lapses. Every logged contact attempt "
            "renews it. Kept on closed rows as the last lease."
        ),
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    photo_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        editable=False,
        help_text=_(
            "photo_1 storage key the coach compared on a video pass. The badge "
            "says 'video' only while photo_1 still has this key."
        ),
    )
    checklist = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("The coach's ticked checklist. Coach-only; never exported."),
    )
    notes = models.TextField(
        blank=True,
        help_text=_("Coach-only notes (at most 1000 characters)."),
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_real_person_checks",
    )
    revoke_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-claimed_at", "-pk"]
        verbose_name = _("Real-person check")
        verbose_name_plural = _("Real-person checks")
        constraints = [
            # The real race guard between two coaches claiming one member,
            # on SQLite as well as PostgreSQL.
            models.UniqueConstraint(
                fields=["profile"],
                condition=models.Q(status="open"),
                name="rpcheck_one_open_per_profile",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="passed") | models.Q(medium__in=MEDIUMS),
                name="rpcheck_passed_has_medium",
            ),
            models.CheckConstraint(
                condition=(
                    ~(models.Q(status="passed") & models.Q(medium=MEDIUM_VIDEO))
                    | ~models.Q(photo_key="")
                ),
                name="rpcheck_video_has_photo_key",
            ),
        ]
        indexes = [
            models.Index(fields=["coach", "status"], name="rpcheck_coach_status_idx"),
            models.Index(
                fields=["profile", "status"], name="rpcheck_profile_status_idx"
            ),
        ]

    def __str__(self):
        # The id only. Django prints this as the label of the read-only link on
        # CallAttemptAdmin, which staff who are not superusers can open; the
        # outcome (a "suspected_fake" above all) is a superuser-only fact.
        return f"Real-person check #{self.pk}"

    def is_live(self, now=None) -> bool:
        """True while this is an open claim whose lease has not lapsed."""
        now = now or timezone.now()
        return (
            self.status == self.STATUS_OPEN
            and self.lease_expires_at is not None
            and self.lease_expires_at > now
        )


class ConnectRealPersonBadge(models.Model):
    """The current "a coach confirmed this member is real" badge.

    The row exists only while a badge is granted. It is valid only while
    ``profile_approved_at`` still equals ``CrushProfile.approved_at``: a
    de-verification or re-verification changes that value, so the badge dies
    without a hook in every verification path. The one writer that leaves
    ``approved_at`` alone is the admin change form (the field is read-only
    there), so ``CrushProfile.save()`` also deletes this row when a save takes
    the profile out of ``verified``; ticking the member verified again then
    cannot bring the badge back.
    """

    profile = models.OneToOneField(
        "crush_lu.CrushProfile",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="connect_real_person_badge",
    )
    # Not called ``check``: that shadows Model.check() (Django error E020).
    source_check = models.OneToOneField(
        ConnectRealPersonCheck,
        on_delete=models.CASCADE,
        related_name="badge",
    )
    medium = models.CharField(
        max_length=5, choices=ConnectRealPersonCheck.PASSED_MEDIUM_CHOICES
    )
    photo_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        editable=False,
        help_text=_(
            "Video badge: the photo_1 key the coach compared. Empty for voice."
        ),
    )
    granted_at = models.DateTimeField(default=timezone.now)
    profile_approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("CrushProfile.approved_at when the badge was granted."),
    )

    class Meta:
        verbose_name = _("Real-person badge")
        verbose_name_plural = _("Real-person badges")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(medium__in=MEDIUMS),
                name="rpbadge_medium_valid",
            ),
            models.CheckConstraint(
                condition=(~models.Q(medium=MEDIUM_VIDEO) | ~models.Q(photo_key="")),
                name="rpbadge_video_has_photo_key",
            ),
        ]

    def __str__(self):
        return f"Real-person badge for profile {self.profile_id} ({self.medium})"
