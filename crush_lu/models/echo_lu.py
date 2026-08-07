"""Sync state for events published to echo.lu.

echo.lu is Luxembourg's national events portal. Its partner API (see
``crush_lu/services/echo_lu.py``) is a plain CRUD surface over "experiences"
with server-assigned ids and no notion of an external key: nothing in the
create payload lets us say "this is Crush.lu event 42". So the id it hands back
is the ONLY link between a MeetupEvent and its listing, and losing it means the
next sync creates a duplicate listing that we can no longer reach to delete.

Hence a row per event rather than a couple of columns on MeetupEvent: the id
has to survive, and it comes with enough bookkeeping (last error, payload
fingerprint, attempt timestamps) that bolting it onto an already 300-line model
would bury it.
"""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class EchoExperienceSync(models.Model):
    """Links one MeetupEvent to its echo.lu experience and records sync health.

    A row exists as soon as a sync is *attempted*, not only when one succeeds —
    a failed attempt has to record its error somewhere the admin can show it,
    and an attempt that failed after echo.lu already created the experience
    still needs somewhere to put the id.
    """

    class Status(models.TextChoices):
        # Never sent, or queued after an eligibility change.
        PENDING = "pending", _("Pending")
        # echo.lu holds a listing that matches `payload_hash`.
        SYNCED = "synced", _("Synced")
        # The last call failed; `last_error` says how. Retried on next sweep.
        FAILED = "failed", _("Failed")
        # Taken down because the event stopped qualifying (unpublished,
        # cancelled, went private, finished). `experience_id` is kept: echo.lu
        # keeps cancelled experiences addressable, so re-publishing updates the
        # same listing instead of creating a second one. The sweep is free to
        # re-publish one of these the moment the event qualifies again.
        WITHDRAWN = "withdrawn", _("Withdrawn")
        # Taken down *by hand* — the admin's remove action or `--withdraw` —
        # while the event still qualifies for a listing. Distinct from
        # WITHDRAWN because the sweep must not undo it: the event's own fields
        # still say "publish me", so nothing else would stop the next pass
        # putting it straight back. Cleared only by an intentional re-sync.
        SUPPRESSED = "suppressed", _("Suppressed")
        # A create that may have left a listing we cannot address: echo.lu
        # either accepted it and returned no id, or failed in a way that does
        # not say whether it committed first (a 5xx, a timeout, a dropped
        # socket). Creating again would risk a second listing, so this state
        # blocks automatic creates until somebody finds out which happened
        # (`--audit`) and either adopts the id or deletes the listing.
        ORPHANED = "orphaned", _("Orphaned — may hold an untracked listing")

    event = models.OneToOneField(
        "crush_lu.MeetupEvent",
        on_delete=models.CASCADE,
        related_name="echo_sync",
        help_text=_("The Crush.lu event this echo.lu listing mirrors."),
    )
    experience_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text=_(
            "Server-assigned echo.lu experience id. Empty until the first "
            "successful create. This is the only handle we have on the "
            "listing — never clear it while the listing still exists."
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
            "SHA-256 of the last payload echo.lu accepted. A resync whose "
            "payload hashes the same is skipped, so the hourly sweep and the "
            "save-signal do not spend a request per event per pass."
        ),
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When echo.lu last accepted a write for this event."),
    )
    last_attempted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When a sync was last attempted, successful or not."),
    )
    last_error = models.TextField(
        blank=True,
        default="",
        help_text=_(
            "Failure detail from the last attempt, cleared on success. "
            "echo.lu returns field-level validation errors here — most often "
            "an unknown category/audience slug."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("echo.lu experience sync")
        verbose_name_plural = _("echo.lu experience syncs")
        ordering = ["-updated_at"]

    def __str__(self):
        label = self.experience_id or _("not yet created")
        return f"{self.event_id} -> echo.lu {label} ({self.status})"

    @property
    def is_live(self):
        """True when echo.lu is currently showing this event."""
        return bool(self.experience_id) and self.status == self.Status.SYNCED

    def mark_success(self, experience_id, payload_hash):
        """Record an accepted write.

        `experience_id` is only ever written, never blanked: a PUT response
        that omits the id (the API is not consistent about echoing it back)
        must not wipe the one we already hold, or the next sync would create a
        duplicate listing.
        """
        now = timezone.now()
        if experience_id:
            self.experience_id = str(experience_id)
        self.payload_hash = payload_hash
        self.status = self.Status.SYNCED
        self.last_synced_at = now
        self.last_attempted_at = now
        self.last_error = ""
        self.save(
            update_fields=[
                "experience_id",
                "payload_hash",
                "status",
                "last_synced_at",
                "last_attempted_at",
                "last_error",
                "updated_at",
            ]
        )

    def mark_failure(self, error):
        """Record a rejected write.

        `payload_hash` is deliberately NOT touched. It describes what echo.lu
        last *accepted*, so leaving it alone keeps the skip-if-unchanged check
        honest — clearing it here would be harmless, but setting it to the
        rejected payload would make the retry a no-op forever.
        """
        now = timezone.now()
        self.status = self.Status.FAILED
        self.last_attempted_at = now
        self.last_error = str(error)[:2000]
        self.save(
            update_fields=[
                "status",
                "last_attempted_at",
                "last_error",
                "updated_at",
            ]
        )

    def mark_orphaned(self, error):
        """Record a create that was accepted without giving us an id.

        A listing now exists on echo.lu that we hold no handle on. Anything
        that would issue another create has to stop here — a retry does not
        recover the lost id, it just adds a second listing beside it. Recovery
        is manual and starts with `sync_events_to_echo --audit`.
        """
        now = timezone.now()
        self.status = self.Status.ORPHANED
        self.last_attempted_at = now
        self.last_error = str(error)[:2000]
        self.save(
            update_fields=[
                "status",
                "last_attempted_at",
                "last_error",
                "updated_at",
            ]
        )

    def mark_withdrawn(self, explicit=False):
        """Record that the listing was taken down on purpose.

        `explicit` separates the two ways that happens. Left False, the event
        itself stopped qualifying, and the sweep should put the listing back as
        soon as it qualifies again. Set True, somebody removed a still-eligible
        event by hand — and because the event's own fields still say "publish
        me", only a status the sweep respects keeps it down.

        The fingerprint is cleared so that re-publishing later always sends a
        full update: while withdrawn, edits to the event go unsynced, and a
        stale hash would let an unchanged-since-withdrawal event come back
        without echo.lu ever being told it is live again.
        """
        now = timezone.now()
        self.status = self.Status.SUPPRESSED if explicit else self.Status.WITHDRAWN
        self.payload_hash = ""
        self.last_attempted_at = now
        self.last_synced_at = now
        self.last_error = ""
        self.save(
            update_fields=[
                "status",
                "payload_hash",
                "last_attempted_at",
                "last_synced_at",
                "last_error",
                "updated_at",
            ]
        )
