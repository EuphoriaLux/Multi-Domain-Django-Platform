from datetime import timedelta

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .events import MeetupEvent
from .profiles import CrushCoach, CrushProfile, ProfileSubmission


class EventConnectionQuerySet(models.QuerySet):
    """Custom QuerySet for EventConnection with performance optimizations."""

    def crush_leads(self):
        """Only rows declared through the "My Crush!" flow (never legacy rows)."""
        return self.filter(flow=EventConnection.FLOW_CRUSH)

    def open_crush_leads(self):
        """
        Crush leads still requiring coach work.

        Live actionable statuses only: a decline (coach-recorded or member
        block) silently cancels the lead, and a lead whose call is completed
        no longer needs a queue row or reminder.
        """
        return self.crush_leads().filter(
            status__in=EventConnection.OPEN_LEAD_STATUSES,
            coach_call_completed_at__isnull=True,
        )

    def crush_leads_for_coach(self, coach):
        """
        Coach action queue: this coach's open crush leads, oldest first.

        Ordering by ``requested_at`` is ordering by "call by" — the SLA is a
        fixed offset from the declaration timestamp.
        """
        return self.open_crush_leads().filter(
            assigned_coach=coach,
        ).order_by('requested_at', 'id')

    def for_user(self, user):
        """Connections where user is requester or recipient."""
        return self.filter(
            models.Q(requester=user) | models.Q(recipient=user)
        )

    def pending_for_user(self, user):
        """Pending connection requests received by user."""
        return self.filter(recipient=user, status='pending')

    def active_for_user(self, user):
        """Active connections (accepted through shared) for user."""
        return self.for_user(user).filter(
            status__in=['accepted', 'coach_reviewing', 'coach_approved', 'shared']
        )

    def for_event(self, event, user):
        """Connections for a specific event involving user."""
        return self.for_user(user).filter(event=event)

    def annotate_is_mutual(self):
        """
        Annotate queryset with is_mutual_annotated field.

        Use this instead of the is_mutual property to avoid N+1 queries:

        # BAD: N+1 queries
        connections = EventConnection.objects.all()
        for conn in connections:
            if conn.is_mutual:  # Triggers query per connection!
                print("Mutual!")

        # GOOD: Single query with annotation
        connections = EventConnection.objects.annotate_is_mutual()
        for conn in connections:
            if conn.is_mutual_annotated:  # No query!
                print("Mutual!")
        """
        from django.db.models import Exists, OuterRef

        mutual_subquery = EventConnection.objects.filter(
            requester=OuterRef('recipient'),
            recipient=OuterRef('requester'),
            event=OuterRef('event')
        )

        return self.annotate(
            is_mutual_annotated=Exists(mutual_subquery)
        )

    def visible_to_coach(self, coach):
        """
        Hide other coaches' crush leads from shared coach surfaces.

        The ``requester_note`` is promised to the routed Crush Coach alone
        ("only they will read this"), so a pre-``shared`` lead routed to a
        *different* coach is excluded outright — a coach who is not the
        routed coach must not learn the crusher/recipient pair either.

        Unrouted pool leads (``assigned_coach IS NULL``) stay visible so
        they remain claimable, and legacy rows keep their existing
        all-coaches visibility.

        A lead whose routed coach was since **deactivated** counts as
        unassigned here on purpose. ``coach_required`` bars that coach from
        every coach view, so hiding the row from everyone else would strand
        it — unworkable, invisible, and silently missing its 48h SLA.
        Keying the exclusion on ``assigned_coach__is_active`` lets another
        coach see and claim it instead.
        """
        return self.exclude(
            models.Q(flow=EventConnection.FLOW_CRUSH)
            & ~models.Q(status='shared')
            & models.Q(assigned_coach__is_active=True)
            & ~models.Q(assigned_coach=coach)
        )

    def annotate_is_mutual_crush(self):
        """
        Flag leads whose counterpart independently declared a crush too.

        Reciprocal crush leads stay two separate rows routed on their own
        requesters (§5), so they can belong to different coaches and must
        never be merged. Flagging them lets both coaches prioritise the pair
        and coordinate the introduction — it discloses only that a
        reciprocal lead exists, never the other side's ``requester_note``.

        Unlike ``annotate_is_mutual``, the reverse row must itself be a live
        crush lead: a legacy row, or a lead already declined, is not a mutual
        crush.
        """
        from django.db.models import Exists, OuterRef

        reciprocal = EventConnection.objects.filter(
            requester=OuterRef('recipient'),
            recipient=OuterRef('requester'),
            event=OuterRef('event'),
            flow=EventConnection.FLOW_CRUSH,
        ).exclude(status='declined')

        return self.annotate(is_mutual_crush_annotated=Exists(reciprocal))


class EventConnectionManager(models.Manager):
    """Custom manager for EventConnection."""

    def get_queryset(self):
        return EventConnectionQuerySet(self.model, using=self._db)

    def for_user(self, user):
        return self.get_queryset().for_user(user)

    def pending_for_user(self, user):
        return self.get_queryset().pending_for_user(user)

    def active_for_user(self, user):
        return self.get_queryset().active_for_user(user)

    def for_event(self, event, user):
        return self.get_queryset().for_event(event, user)

    def annotate_is_mutual(self):
        return self.get_queryset().annotate_is_mutual()

    def annotate_is_mutual_crush(self):
        return self.get_queryset().annotate_is_mutual_crush()

    def visible_to_coach(self, coach):
        return self.get_queryset().visible_to_coach(coach)

    def crush_leads(self):
        return self.get_queryset().crush_leads()

    def open_crush_leads(self):
        return self.get_queryset().open_crush_leads()

    def crush_leads_for_coach(self, coach):
        return self.get_queryset().crush_leads_for_coach(coach)


class EventConnection(models.Model):
    """Post-event connection requests between attendees"""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('coach_reviewing', 'Coach Reviewing'),
        ('coach_approved', 'Coach Approved - Ready to Share'),
        ('shared', 'Contact Info Shared'),
    ]

    # "My Crush!" post-event flow (spec 2026-07-21-crush-my-crush-post-event-flow §7)
    FLOW_LEGACY = 'legacy'
    FLOW_CRUSH = 'crush'
    FLOW_CHOICES = [
        (FLOW_LEGACY, 'Legacy connection request'),
        (FLOW_CRUSH, 'My Crush! coach lead'),
    ]

    # Recipient's answer, recorded by their own coach through the constrained
    # co-coach task actions — never by the recipient directly (they are never
    # shown the lead).
    RECIPIENT_RESPONSE_CONSENTED = 'consented'
    RECIPIENT_RESPONSE_DECLINED = 'declined'
    RECIPIENT_RESPONSE_CHOICES = [
        (RECIPIENT_RESPONSE_CONSENTED, 'Recipient consented to the introduction'),
        (RECIPIENT_RESPONSE_DECLINED, 'Recipient declined the introduction'),
    ]

    # Statuses in which a crush lead still requires coach action. A member
    # block or coach-recorded decline flips a lead to ``declined`` without
    # touching call/reminder fields, so queue and reminder machinery must
    # key off this list, not only off the call fields.
    OPEN_LEAD_STATUSES = ('pending', 'coach_reviewing', 'coach_approved')

    # Coach-call SLA for crush leads (spec §6/O8: call within 48h).
    CRUSH_LEAD_CALL_SLA = timedelta(hours=48)

    CALL_OUTCOME_CHOICES = [
        ('completed', _('Call completed')),
        ('no_answer', _('No answer')),
        ('rescheduled', _('Rescheduled')),
        ('unreachable', _('Unreachable')),
    ]

    # Who wants to connect
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connection_requests_sent')
    # Who they want to connect with
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='connection_requests_received')
    # Which event brought them together
    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE)

    # Connection details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    flow = models.CharField(
        max_length=10,
        choices=FLOW_CHOICES,
        default=FLOW_LEGACY,
        db_index=True,
        help_text=_(
            "Post-event flow this row belongs to. Historical rows stay "
            "'legacy' (no backfill); only explicit 'My Crush!' declarations "
            "become crush coach leads."
        ),
    )
    requester_note = models.TextField(
        max_length=300,
        blank=True,
        help_text=_("Optional note: What did you talk about? What interested you?")
    )

    # Coach facilitation
    assigned_coach = models.ForeignKey(
        CrushCoach,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text=_("Coach who will facilitate this connection")
    )
    coach_notes = models.TextField(blank=True, help_text=_("Coach's guidance for the introduction"))
    coach_introduction = models.TextField(
        blank=True,
        help_text=_("Personalized introduction message from coach")
    )

    # Mutual consent tracking
    requester_consents_to_share = models.BooleanField(
        default=False,
        help_text=_("Requester agrees to share contact info")
    )
    recipient_consents_to_share = models.BooleanField(
        default=False,
        help_text=_("Recipient agrees to share contact info")
    )

    # Timestamps
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    coach_approved_at = models.DateTimeField(null=True, blank=True)
    shared_at = models.DateTimeField(null=True, blank=True)

    # "My Crush!" coach-call tracking (spec §7 — additive, nullable; only
    # meaningful for flow='crush' rows)
    coach_call_scheduled_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_("When the coach call with the requester is scheduled"),
    )
    coach_call_completed_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_("When the coach call with the requester was completed"),
    )
    call_outcome = models.CharField(
        max_length=20,
        choices=CALL_OUTCOME_CHOICES,
        null=True,
        blank=True,
        help_text=_("Outcome of the coach call"),
    )
    reminder_sent_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_(
            "Idempotency record for the 24h untouched-lead reminder — "
            "repeated timer delivery must never double-remind."
        ),
    )

    # Recipient-side co-coach work item (spec §5). When the recipient has
    # their own active coach, the §4 promise "contacting the crush's coach"
    # becomes a tracked task rather than an informal hand-off. The co-coach
    # never opens the lead itself — these fields are the whole surface they
    # write through.
    recipient_coach = models.ForeignKey(
        CrushCoach,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='crush_cocoach_leads',
        help_text=_(
            "The recipient's own coach, when different from the routed "
            "coach — owns the recipient-side outreach task."
        ),
    )
    recipient_outreach_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_("When the co-coach recorded reaching out to the recipient"),
    )
    recipient_response = models.CharField(
        max_length=20,
        choices=RECIPIENT_RESPONSE_CHOICES,
        null=True,
        blank=True,
        help_text=_("Recipient's answer, as recorded by their own coach"),
    )
    recipient_response_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_("When the recipient's answer was recorded"),
    )
    system_actions = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Append-only audit trail of narrow coach writes (actor + "
            "timestamp), so the co-coach path is reconstructable."
        ),
    )

    objects = EventConnectionManager()

    class Meta:
        unique_together = ('requester', 'recipient', 'event')
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['event', 'status'], name='crush_lu_conn_event_status_idx'),
            models.Index(fields=['requester', 'status'], name='crush_lu_conn_req_status_idx'),
            models.Index(fields=['recipient', 'status'], name='crush_lu_conn_recip_status_idx'),
        ]

    def __str__(self):
        return f"{self.requester.username} → {self.recipient.username} ({self.event.title})"

    def log_system_action(self, type_: str, actor: str = "system", **details):
        """Append an audit entry to ``system_actions``.

        Mirrors ``ProfileSubmission.log_system_action``: write-back rather
        than in-place mutation, because Django does not persist JSONField
        mutations made in place. The caller saves the parent with
        ``update_fields=[..., 'system_actions']``.
        """
        entry = {
            "type": type_,
            "at": timezone.now().isoformat(),
            "actor": actor,
        }
        if details:
            entry["details"] = details
        actions = list(self.system_actions or [])
        actions.append(entry)
        self.system_actions = actions
        return entry

    def assign_recipient_coach(self):
        """
        Route the recipient-side outreach task (spec §5).

        Set only when the recipient has their own *active* coach who is not
        the routed coach — otherwise the routed coach performs both halves of
        the outreach directly and no co-coach task exists. Mirrors
        ``assign_coach``'s tiers on the recipient: approved submission coach
        first, then the profile's permanent coach.

        Returns the assigned co-coach, or ``None``.
        """
        if self.flow != self.FLOW_CRUSH:
            return None

        recipient_profile = CrushProfile.objects.select_related(
            'assigned_coach'
        ).filter(user=self.recipient).first()
        if recipient_profile is None:
            return None

        submission = ProfileSubmission.objects.select_related('coach').filter(
            profile=recipient_profile, status='approved'
        ).first()

        coach = None
        if submission and submission.coach and submission.coach.is_active:
            coach = submission.coach
        elif (
            recipient_profile.assigned_coach
            and recipient_profile.assigned_coach.is_active
        ):
            coach = recipient_profile.assigned_coach

        # Same coach on both sides: one person covers both halves, so there is
        # no hand-off to track and no second surface to authorize.
        if coach is None or coach == self.assigned_coach:
            return None

        self.recipient_coach = coach
        self.save(update_fields=['recipient_coach'])
        return coach

    @property
    def is_mutual(self):
        """
        Check if there's a mutual connection request.

        WARNING: This property causes an N+1 query problem when accessed in a loop.
        Use EventConnection.objects.annotate_is_mutual() instead for better performance.

        Example:
            # BAD: N+1 queries
            connections = EventConnection.objects.all()
            for conn in connections:
                if conn.is_mutual:  # Database query per connection!
                    print("Mutual")

            # GOOD: Single query
            connections = EventConnection.objects.annotate_is_mutual()
            for conn in connections:
                if conn.is_mutual_annotated:  # No query!
                    print("Mutual")
        """
        return EventConnection.objects.filter(
            requester=self.recipient,
            recipient=self.requester,
            event=self.event
        ).exists()

    @classmethod
    def cross_gender_connection_count(cls, user, event):
        """Count cross-gender connection requests sent by user for this event."""
        user_gender = getattr(getattr(user, 'crushprofile', None), 'gender', '')
        connections = cls.objects.filter(
            requester=user, event=event
        ).select_related('recipient__crushprofile')
        count = 0
        for conn in connections:
            rec_gender = getattr(
                getattr(conn.recipient, 'crushprofile', None), 'gender', ''
            )
            if user_gender != rec_gender or not user_gender or not rec_gender:
                count += 1
        return count

    @property
    def is_same_gender(self):
        """Check if both parties have the same gender (and it's specified)."""
        req_profile = getattr(self.requester, 'crushprofile', None)
        rec_profile = getattr(self.recipient, 'crushprofile', None)
        if not req_profile or not rec_profile:
            return False
        req_gender = req_profile.gender
        rec_gender = rec_profile.gender
        if not req_gender or not rec_gender:
            return False
        return req_gender == rec_gender

    @property
    def can_share_contacts(self):
        """Both must consent and coach must approve"""
        return (
            self.requester_consents_to_share
            and self.recipient_consents_to_share
            and self.status == 'coach_approved'
        )

    @property
    def call_by(self):
        """
        "Call by" deadline for a crush lead (spec §6/O8: 48h SLA).

        ``None`` for legacy rows and for rows without a declaration
        timestamp — the SLA only exists for crush leads.
        """
        if self.flow != self.FLOW_CRUSH or not self.requested_at:
            return None
        return self.requested_at + self.CRUSH_LEAD_CALL_SLA

    @classmethod
    def select_event_coach(cls, event):
        """
        Selection policy among an event's coaches (spec §7/O11).

        Deterministic: least-loaded by open crush leads, else first by id.
        Deactivated event coaches are never selected. Returns ``None`` when
        the event has no active coach so routing falls through to the pool.
        """
        return event.coaches.filter(is_active=True).annotate(
            open_leads=models.Count(
                'eventconnection',
                filter=models.Q(
                    eventconnection__flow=cls.FLOW_CRUSH,
                    eventconnection__status__in=cls.OPEN_LEAD_STATUSES,
                    eventconnection__coach_call_completed_at__isnull=True,
                ),
            )
        ).order_by('open_leads', 'id').first()  # None when no active coach

    def assign_coach(self):
        """
        Assign a coach to facilitate this connection.

        Routing tiers (spec §5/§7) — every tier requires an active coach, so
        a stale assignment or deactivated event coach falls through instead
        of stranding the lead:

        1. the requester's assigned coach (approved ``ProfileSubmission``),
           if still active;
        2. the requester's permanent coach (``CrushProfile.assigned_coach``,
           set at first event attendance or premium confirmation), if still
           active — covers members with no approved submission;
        3. an active coach from ``event.coaches`` (selection policy:
           least-loaded by open crush leads, else first by id);
        4. the active coach pool (first by id).
        """
        requester_profile = CrushProfile.objects.select_related(
            'user', 'assigned_coach'
        ).get(user=self.requester)

        # Get the approved submission for the requester with coach pre-fetched
        requester_submission = ProfileSubmission.objects.select_related('coach').filter(
            profile=requester_profile,
            status='approved'
        ).first()

        coach = None
        if (
            requester_submission
            and requester_submission.coach
            and requester_submission.coach.is_active
        ):
            coach = requester_submission.coach

        if coach is None:
            permanent_coach = requester_profile.assigned_coach
            if permanent_coach and permanent_coach.is_active:
                coach = permanent_coach

        if coach is None:
            coach = self.select_event_coach(self.event)

        if coach is None:
            # Fall back to the active coach pool (deterministic order)
            coach = CrushCoach.objects.filter(is_active=True).order_by('id').first()

        self.assigned_coach = coach
        self.save()

        # Route the recipient half too, so the "we'll contact their coach"
        # promise is a tracked task from the moment the lead exists rather
        # than an informal hand-off. No-op for legacy rows and whenever both
        # sides share a coach.
        self.assign_recipient_coach()


class ConnectionMessage(models.Model):
    """Coach-facilitated messages between connections"""

    connection = models.ForeignKey(EventConnection, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.TextField(max_length=500)

    # Coach moderation
    is_coach_message = models.BooleanField(
        default=False,
        help_text=_("True if sent by the coach")
    )
    coach_approved = models.BooleanField(
        default=True,
        help_text=_("Coach can moderate messages if needed")
    )

    sent_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['sent_at']

    def __str__(self):
        return f"Message from {self.sender.username} at {self.sent_at}"
