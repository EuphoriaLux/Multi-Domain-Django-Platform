"""
Custom SMS batches for the Crush-Admin "Custom SMS" page.

A batch is a free-text message (per language) plus an audience definition.
Nothing is sent server-side: like the coach panel's event invites, each
recipient row on the send page opens the sender's own SMS app through a
``sms:`` deep link with the personalised body prefilled, and a
``CallAttempt`` row with ``result="custom_sms"`` records that it was sent.

Recipients are resolved from the audience definition on every render, so a
batch created for an event picks up new registrations automatically. The
audit rows are what make the page resumable: a recipient counts as "sent"
when a ``custom_sms`` attempt tagged with this batch exists for their
profile (see :func:`CustomSmsBatch.notes_prefix`).

Batches are pruned by the GDPR retention sweep (``gdpr_retention_cleanup``,
``custom_sms_batch_days``) on the same window as the ``CallAttempt`` audit
rows they belong to, measured from ``last_activity_at``.
"""

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class CustomSmsBatch(models.Model):
    """One composed message + audience, worked through one recipient at a time."""

    class Audience(models.TextChoices):
        EVENT = "event", _("Event registrations")
        SEGMENT = "segment", _("User segment")
        MANUAL = "manual", _("Manual list (emails or phone numbers)")

    # Registration statuses a sender can target for an event audience.
    # Keys are EventRegistration.status values.
    REGISTRATION_STATUS_OPTIONS = [
        ("confirmed", _("Confirmed")),
        ("attended", _("Attended")),
        ("waitlist", _("Waitlist")),
        ("pending", _("Pending payment")),
        ("applied", _("Applied (curated)")),
        ("cancelled", _("Cancelled")),
        ("no_show", _("No-show")),
    ]

    title = models.CharField(
        max_length=120,
        blank=True,
        help_text=_("Optional label so you can find this batch again later."),
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_sms_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Bumped by every log / undo on the send list. The GDPR sweep expires a
    # batch from this, not from created_at, so a list resumed a year later
    # is not pulled from under its own fresh audit rows.
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)

    audience_type = models.CharField(
        max_length=16, choices=Audience.choices, default=Audience.EVENT
    )
    event = models.ForeignKey(
        "crush_lu.MeetupEvent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="custom_sms_batches",
        help_text=_(
            "Source of recipients for an event audience; for other audiences it "
            "only feeds the {event_title} / {event_date} / {event_url} placeholders."
        ),
    )
    registration_statuses = models.JSONField(
        default=list,
        blank=True,
        help_text=_("EventRegistration statuses included (event audience only)."),
    )
    segment_key = models.CharField(max_length=64, blank=True)
    # Manual audience: the pasted emails / phone numbers are resolved to
    # members at compose time and only the user ids are kept, so the batch
    # never holds a second copy of contact data — banning or deleting a
    # member removes them from the list like everywhere else.
    manual_user_ids = models.JSONField(
        default=list,
        blank=True,
        help_text=_("User ids resolved from the pasted list (manual audience)."),
    )
    include_unverified_phones = models.BooleanField(
        default=False,
        help_text=_(
            "Also list members whose phone number is not verified. Off by default: "
            "an unverified number may belong to someone else."
        ),
    )

    message_en = models.TextField(
        help_text=_("Used for every recipient without a variant below.")
    )
    message_de = models.TextField(blank=True)
    message_fr = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Custom SMS batch")
        verbose_name_plural = _("Custom SMS batches")

    def __str__(self):
        return self.title or f"Custom SMS batch #{self.pk}"

    @property
    def display_title(self):
        return self.title or _("Batch #%(id)s") % {"id": self.pk}

    @property
    def notes_prefix(self):
        """Tag written at the start of every audit row's ``notes`` for this batch."""
        return f"[custom-sms:{self.pk}]"

    def message_for_language(self, lang):
        """Return the variant for ``lang``, falling back to English."""
        if lang == "de" and self.message_de.strip():
            return self.message_de
        if lang == "fr" and self.message_fr.strip():
            return self.message_fr
        return self.message_en
