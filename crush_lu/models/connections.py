from datetime import timedelta

from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from .events import MeetupEvent
from .profiles import CrushCoach, CrushProfile, ProfileSubmission


class EventConnectionQuerySet(models.QuerySet):
    """Custom QuerySet for EventConnection with performance optimizations."""

    def crush_leads(self):
        """Only rows declared through the "My Crush!" flow (never legacy rows)."""
        return self.filter(flow=EventConnection.FLOW_CRUSH)

    def excluding_unshared_crushes(self):
        """
        Hide pre-``shared`` crush rows from member-facing surfaces.

        A crush declaration is private: until the coach-facilitated
        introduction completes (``shared``), the row is invisible to the
        recipient on every surface (inbox, badges, aggregates, exports) and
        renders only as a neutral "with your coach" lead to the requester.
        """
        return self.exclude(
            models.Q(flow=EventConnection.FLOW_CRUSH)
            & ~models.Q(status='shared')
        )

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

    def annotate_is_visible_mutual(self):
        """
        Like ``annotate_is_mutual``, but mutual-derived member metrics must
        never confirm a private crush: pre-``shared`` ``flow=crush`` reverse
        rows do not count as a mutual match (spec §5 — flow-blind reverse-row
        existence leaks a private declaration the moment it lands).
        """
        from django.db.models import Exists, OuterRef

        mutual_subquery = (
            EventConnection.objects.filter(
                requester=OuterRef('recipient'),
                recipient=OuterRef('requester'),
                event=OuterRef('event')
            )
            .exclude(
                models.Q(flow=EventConnection.FLOW_CRUSH)
                & ~models.Q(status='shared')
            )
        )

        return self.annotate(
            is_mutual_annotated=Exists(mutual_subquery)
        )


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

    def annotate_is_visible_mutual(self):
        return self.get_queryset().annotate_is_visible_mutual()

    def excluding_unshared_crushes(self):
        return self.get_queryset().excluding_unshared_crushes()

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

    # Statuses in which a crush lead still requires coach action. A member
    # block or coach-recorded decline flips a lead to ``declined`` without
    # touching call/reminder fields, so queue and reminder machinery must
    # key off this list, not only off the call fields.
    OPEN_LEAD_STATUSES = ('pending', 'coach_reviewing', 'coach_approved')

    # Coach-call SLA for crush leads (spec §6/O8: call within 48h).
    CRUSH_LEAD_CALL_SLA = timedelta(hours=48)

    # Per-event crush limit (spec §6/O9): 1 crush per event per member, for
    # free AND Connect members alike — scarcity protects signal quality and
    # bounds coach load. Enforced gender-independently via
    # ``crush_declaration_count`` under a per-(requester, event) lock.
    MAX_CRUSHES_PER_EVENT = 1

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

    @classmethod
    def crush_declaration_count(cls, user, event):
        """
        Gender-independent crush counter (spec §5/§7, O9).

        Counts ALL "My Crush!" declarations by ``user`` for ``event``,
        regardless of gender pair or lead status — a declined lead still
        consumed coach time, so it still counts against the per-event limit.
        The legacy ``cross_gender_connection_count`` cannot serve as the
        capacity bound (it skips same-gender pairs) and is not reused here.
        """
        return cls.objects.filter(
            requester=user, event=event, flow=cls.FLOW_CRUSH
        ).count()

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
