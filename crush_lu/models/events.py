from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from datetime import timedelta
import uuid
from .profiles import CrushCoach, SpecialUserExperience
from crush_lu.storage import crush_upload_path, crush_media_storage

# Enforced ceiling on event length (minutes). Module-level so it is reachable
# from the field definition, the Meta CheckConstraint, and live-event lookbacks
# across surfaces (home page, event list, nav menu). See MeetupEvent.
MAX_EVENT_DURATION_MINUTES = 7 * 24 * 60  # 7 days


class MeetupEventQuerySet(models.QuerySet):
    """Custom QuerySet for MeetupEvent with performance optimizations."""

    def with_registration_counts(self):
        """
        Annotate queryset with confirmed_count and waitlist_count.

        Use this instead of calling get_confirmed_count() in a loop to avoid N+1 queries:

        # BAD: N+1 queries
        events = MeetupEvent.objects.all()
        for event in events:
            print(event.get_confirmed_count())  # Query per event!

        # GOOD: Single query with annotation
        events = MeetupEvent.objects.with_registration_counts()
        for event in events:
            print(event.confirmed_count_annotated)  # No query!
        """
        from django.db.models import Count, Q

        return self.annotate(
            confirmed_count_annotated=Count(
                "eventregistration",
                filter=Q(eventregistration__status__in=["confirmed", "attended"]),
            ),
            waitlist_count_annotated=Count(
                "eventregistration", filter=Q(eventregistration__status="waitlist")
            ),
        )


class MeetupEventManager(models.Manager):
    """Custom manager for MeetupEvent."""

    def get_queryset(self):
        return MeetupEventQuerySet(self.model, using=self._db)

    def with_registration_counts(self):
        return self.get_queryset().with_registration_counts()


class MeetupEvent(models.Model):
    """Speed dating and social meetup events"""

    # Enforced ceiling on event length. `duration_minutes` is otherwise an
    # unbounded PositiveIntegerField; capping it keeps every live-event lookback
    # (home page, event list, nav menu) both bounded AND complete — the lookback
    # window equals this ceiling, so no valid live event is ever scanned past or
    # dropped. Enforced at persistence by a Meta CheckConstraint (a validator
    # alone does not run on bulk updates or plain save()). Generous enough for
    # any real event, including multi-day formats.
    MAX_DURATION_MINUTES = MAX_EVENT_DURATION_MINUTES

    EVENT_TYPE_CHOICES = [
        ("speed_dating", "Speed Dating"),
        ("mixer", "Social Mixer"),
        ("activity", "Activity Meetup"),
        ("themed", "Themed Event"),
        ("quiz_night", "Quiz Night"),
        ("crush_cache", "Crush Cache Hunt"),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField()
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES)

    # Event Banner Image
    image = models.ImageField(
        upload_to=crush_upload_path("events/banners"),
        storage=crush_media_storage,  # This is a callable factory that returns storage instance
        blank=True,
        null=True,
        help_text=_(
            "Event banner image (recommended: 1200x600px, 2:1 ratio for best results)"
        ),
    )

    # Event Details
    location = models.CharField(max_length=200)
    address = models.TextField()
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        help_text=_("Venue latitude for Apple Wallet location notifications"),
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        help_text=_("Venue longitude for Apple Wallet location notifications"),
    )
    canton = models.CharField(
        max_length=200,
        blank=True,
        help_text=_(
            "Canton/region visible to public visitors (e.g., 'Luxembourg', 'Esch-sur-Alzette'). Free-text entry for flexibility."
        ),
    )
    date_time = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(
        default=120,
        validators=[MaxValueValidator(MAX_DURATION_MINUTES)],
    )

    # Capacity & Requirements
    max_participants = models.PositiveIntegerField(default=20)
    reserved_premium_seats = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Reserved premium seats"),
        help_text=_(
            "Seats within the total capacity held back for premium "
            "(coach-assigned) members. General members fill only "
            "(max participants − reserved). 0 = no reservation."
        ),
    )
    max_participants_m = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Max spots (Men)"),
        help_text=_(
            "Maximum confirmed spots for Male attendees. "
            "Leave blank to use total-only cap."
        ),
    )
    max_participants_f = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Max spots (Women)"),
        help_text=_(
            "Maximum confirmed spots for Female attendees. "
            "Leave blank to use total-only cap."
        ),
    )
    max_participants_nb = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Max spots (Other genders)"),
        help_text=_(
            "Maximum confirmed spots for Non-binary/Other/Prefer-not-to-say. "
            "Leave blank to use total-only cap."
        ),
    )
    min_age = models.PositiveIntegerField(default=18)
    max_age = models.PositiveIntegerField(default=99)
    PROFILE_REQUIREMENT_CHOICES = [
        ("completed", _("Completed profile (entry event)")),
        ("approved", _("Verified profile only (members)")),
        ("coach_assigned", _("Premium member — coach assigned")),
        ("unverified", _("Unverified profile only")),
        ("profile_exists", _("Profile must exist")),
        ("none", _("No profile required")),
    ]
    profile_requirement = models.CharField(
        max_length=20,
        choices=PROFILE_REQUIREMENT_CHOICES,
        default="completed",
        help_text=_(
            "Controls what level of profile is needed to register for this event"
        ),
    )

    # Registration Form Configuration
    has_food_component = models.BooleanField(
        default=False,
        help_text=_(
            "Does this event include food/drinks? (Shows dietary restrictions field)"
        ),
    )
    allow_plus_ones = models.BooleanField(
        default=False, help_text=_("Can attendees bring a guest?")
    )

    # Registration
    registration_deadline = models.DateTimeField()
    registration_fee = models.DecimalField(
        max_digits=6, decimal_places=2, default=0.00, help_text=_("Event fee in EUR")
    )

    # Status & Features
    is_published = models.BooleanField(default=False)
    is_cancelled = models.BooleanField(default=False)

    # Google Wallet Event Ticket
    google_wallet_event_class_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=_("Google Wallet EventTicketClass ID for this event"),
    )
    enable_activity_voting = models.BooleanField(
        default=False,
        help_text=_(
            "Enable 3-phase interactive system (voting, presentations, speed dating)"
        ),
    )

    # Event Language Requirements
    languages = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Languages this event will be conducted in (e.g. ['en', 'fr']). "
            "Empty list means no language restriction."
        ),
    )

    # Private Invitation Event Settings
    is_private_invitation = models.BooleanField(
        default=False,
        help_text=_("Private invitation-only event (visible only to invited guests)"),
    )
    invitation_code = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        null=True,
        help_text=_("Unique code for this private event"),
    )
    invitation_expires_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When invitations for this event expire")
    )
    max_invited_guests = models.PositiveIntegerField(
        default=20, help_text=_("Maximum invited guests for private event")
    )

    # Invited Existing Users (for private events)
    invited_users = models.ManyToManyField(
        User,
        blank=True,
        related_name="invited_to_events",
        help_text=_(
            "Existing users invited to this private event (no external invitation needed)"
        ),
    )

    # Event Coaches
    coaches = models.ManyToManyField(
        CrushCoach,
        blank=True,
        related_name="assigned_events",
        help_text=_("Coaches assigned to facilitate this event."),
    )

    # Crush Spark Settings
    max_sparks_per_event = models.PositiveIntegerField(
        default=3,
        help_text=_("Maximum number of Crush Sparks a user can send per event"),
    )
    connection_window_hours = models.PositiveIntegerField(
        default=48,
        help_text=_(
            "Hours after the event's scheduled end until post-event "
            "connection requests close (default: 48 — the same span as the "
            "Event Lobby recap, so both close together). After the window "
            "closes, attendees are redirected to the Crush Connect teaser."
        ),
    )

    # Cross-gender connection limit
    max_cross_gender_connections = models.PositiveIntegerField(
        default=1,
        help_text=_(
            "Maximum cross-gender connection requests per user per event "
            "(0 = unlimited). Same-gender connections are always unlimited."
        ),
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MeetupEventManager()

    class Meta:
        ordering = ["date_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    duration_minutes__lte=MAX_EVENT_DURATION_MINUTES
                ),
                name="crush_lu_meetupevent_duration_within_ceiling",
            ),
        ]

    def __str__(self):
        return f"{self.title} - {self.date_time.strftime('%Y-%m-%d %H:%M')}"

    @classmethod
    def live_lookback_cutoff(cls, now):
        """Earliest start an event could have and still be live at ``now``.

        Equals ``now - MAX_DURATION_MINUTES``. Because durations are capped at
        that ceiling, every still-live event started at or after this cutoff, so
        live-event queries can filter ``date_time__gte`` by it — a bounded scan
        that never drops a live event — before the precise ``end_time`` check in
        Python (``timedelta * F()`` is unsupported on SQLite).
        """
        return now - timedelta(minutes=cls.MAX_DURATION_MINUTES)

    # Maps a profile gender code to a capacity pool key
    GENDER_POOL_MAP = {"M": "m", "F": "f", "NB": "nb", "O": "nb", "P": "nb"}
    # Maps a pool key back to the gender codes that belong to it
    POOL_TO_CODES = {"m": ["M"], "f": ["F"], "nb": ["NB", "O", "P"]}

    @property
    def gender_limits_active(self):
        """True when all three per-gender caps are set."""
        return all(
            v is not None
            for v in [
                self.max_participants_m,
                self.max_participants_f,
                self.max_participants_nb,
            ]
        )

    def get_gender_pool(self, gender_code):
        """Return the pool key ('m', 'f', 'nb') for a gender code, or None."""
        return self.GENDER_POOL_MAP.get(gender_code)

    def get_gender_pool_limit(self, gender_code):
        """Return the capacity limit for the pool this gender belongs to."""
        if not self.gender_limits_active:
            return None
        pool = self.get_gender_pool(gender_code)
        return {
            "m": self.max_participants_m,
            "f": self.max_participants_f,
            "nb": self.max_participants_nb,
        }.get(pool)

    def get_confirmed_count_for_gender(self, gender_code):
        """Count confirmed/attended registrations in the same gender pool."""
        pool = self.get_gender_pool(gender_code)
        if pool is None:
            return 0
        return self.eventregistration_set.filter(
            status__in=["confirmed", "attended"],
            user__crushprofile__gender__in=self.POOL_TO_CODES.get(pool, []),
        ).count()

    def is_gender_pool_full(self, gender_code):
        """True when the gender pool for this code has reached its cap."""
        limit = self.get_gender_pool_limit(gender_code)
        if limit is None:
            return False
        return self.get_confirmed_count_for_gender(gender_code) >= limit

    def clean(self):
        """Validate event data before saving"""
        from django.core.exceptions import ValidationError

        super().clean()

        # Require canton for published events
        if self.is_published and not self.canton:
            raise ValidationError(
                {"canton": _("Canton is required for published events.")}
            )

        # Gender caps: all three must be set together or all left blank
        gender_caps = [
            self.max_participants_m,
            self.max_participants_f,
            self.max_participants_nb,
        ]
        set_caps = [c for c in gender_caps if c is not None]
        if 0 < len(set_caps) < 3:
            raise ValidationError(
                _(
                    "Set all three gender caps together, or leave all blank "
                    "for a total-only cap."
                )
            )

        # Age range validation
        if self.min_age > self.max_age:
            raise ValidationError(
                _("Minimum age (%(min)d) cannot exceed maximum age (%(max)d).")
                % {"min": self.min_age, "max": self.max_age}
            )
        if self.min_age < 18:
            raise ValidationError({"min_age": _("Minimum age must be at least 18.")})
        if self.max_age > 120:
            raise ValidationError({"max_age": _("Maximum age cannot exceed 120.")})

        # Sum of gender caps must not exceed total max_participants
        if len(set_caps) == 3:
            total_gender = sum(set_caps)
            if total_gender > self.max_participants:
                raise ValidationError(
                    _(
                        "Sum of gender caps (%(gender_total)d) must not exceed "
                        "total max participants (%(max)d)."
                    )
                    % {"gender_total": total_gender, "max": self.max_participants}
                )

        # Reserved premium seats cannot exceed total capacity
        if self.reserved_premium_seats > self.max_participants:
            raise ValidationError(
                {
                    "reserved_premium_seats": _(
                        "Reserved premium seats (%(reserved)d) cannot exceed "
                        "total max participants (%(max)d)."
                    )
                    % {
                        "reserved": self.reserved_premium_seats,
                        "max": self.max_participants,
                    }
                }
            )

    @property
    def is_registration_accepting(self):
        """Whether registration is accepting signups (confirmed or waitlist)."""
        now = timezone.now()
        return (
            self.is_published
            and not self.is_cancelled
            and now < self.registration_deadline
        )

    @property
    def is_registration_open(self):
        now = timezone.now()
        return (
            self.is_published
            and not self.is_cancelled
            and now < self.registration_deadline
            and self.get_confirmed_count() < self.max_participants
        )

    @property
    def is_full(self):
        return self.get_confirmed_count() >= self.max_participants

    @property
    def spots_remaining(self):
        return max(0, self.max_participants - self.get_confirmed_count())

    @property
    def public_capacity(self):
        """Seats available to general (non-premium) members."""
        return max(0, self.max_participants - self.reserved_premium_seats)

    def is_full_for(self, is_premium=False):
        """Capacity check that respects reserved premium seats.

        Premium (coach-assigned) members measure fullness against the total
        ``max_participants``; everyone else against ``public_capacity``.
        """
        cap = self.max_participants if is_premium else self.public_capacity
        return self.get_confirmed_count() >= cap

    def spots_remaining_for(self, is_premium=False):
        cap = self.max_participants if is_premium else self.public_capacity
        return max(0, cap - self.get_confirmed_count())

    @property
    def reserved_spots_remaining(self):
        """Unclaimed reserved seats (premium-only block at the top of capacity)."""
        confirmed = self.get_confirmed_count()
        total_remaining = max(0, self.max_participants - confirmed)
        public_remaining = max(0, self.public_capacity - confirmed)
        return total_remaining - public_remaining

    @property
    def end_time(self):
        """Calculate event end time based on start time and duration."""
        return self.date_time + timedelta(minutes=self.duration_minutes)

    @property
    def is_live(self):
        """Whether the event is happening right now (started, not ended).

        A cancelled event is never "live" — its detail page stays reachable
        while published, so this guard keeps the "happening now" banner and the
        "Live now" card badge from ever appearing for a cancelled event.
        """
        now = timezone.now()
        return (
            not self.is_cancelled
            and self.date_time <= now < self.end_time
        )

    @property
    def connection_window_deadline(self):
        """When the post-event connection-request window closes.

        Computed from ``end_time`` (scheduled end) + ``connection_window_hours``
        (default 48h — deliberately the same span as the Event Lobby recap, so
        both post-event surfaces close together). After this point, the
        "Request Connection" button on the attendees list is replaced by a
        "Try Crush Connect" link, and any direct POST to the
        connection-request endpoints redirects to the Crush Connect teaser.
        """
        return self.end_time + timedelta(hours=self.connection_window_hours)

    @property
    def connection_window_active(self):
        """True while users may still send post-event connection requests."""
        return timezone.now() <= self.connection_window_deadline

    @property
    def connections_open(self):
        """True while the attendees page and connection requests are available.

        Opens at the scheduled end — live-time socializing belongs to the
        (anonymous) Event Lobby, so the named attendees list must not be
        browsable mid-event (decision 2026-07-18) — and closes with
        ``connection_window_deadline``.
        """
        return self.end_time <= timezone.now() <= self.connection_window_deadline

    @property
    def quiz_join_available(self):
        """Quiz join button visible during event + 2 days after."""
        return (
            self.event_type == "quiz_night"
            and timezone.now() <= self.end_time + timedelta(days=2)
        )

    @property
    def cache_join_available(self):
        """Crush Cache lobby button visible during event + 2 days after."""
        return (
            self.event_type == "crush_cache"
            and hasattr(self, "cache_hunt")
            and timezone.now() <= self.end_time + timedelta(days=2)
        )

    def get_confirmed_count(self):
        """
        Get count of confirmed/attended registrations.

        OPTIMIZATION: To avoid N+1 queries when displaying lists of events,
        use MeetupEvent.objects.with_registration_counts() which annotates
        the queryset with confirmed_count_annotated.

        Example:
            # BAD: N+1 queries
            events = MeetupEvent.objects.all()
            for event in events:
                print(event.get_confirmed_count())  # Query per event!

            # GOOD: Single query
            events = MeetupEvent.objects.with_registration_counts()
            for event in events:
                print(event.confirmed_count_annotated)  # No query!

        For single events, this method is efficient enough.
        """
        # Try to use annotated value if available (from with_registration_counts())
        if hasattr(self, "confirmed_count_annotated"):
            return self.confirmed_count_annotated
        return self.eventregistration_set.filter(
            status__in=["confirmed", "attended"]
        ).count()

    def get_waitlist_count(self):
        """
        Get count of waitlisted registrations.

        OPTIMIZATION: Use MeetupEvent.objects.with_registration_counts() to avoid N+1 queries.
        """
        # Try to use annotated value if available
        if hasattr(self, "waitlist_count_annotated"):
            return self.waitlist_count_annotated
        return self.eventregistration_set.filter(status="waitlist").count()

    LANGUAGE_DISPLAY = {
        "en": {"name": _("English"), "flag": "\U0001f1ec\U0001f1e7"},
        "de": {"name": _("Deutsch"), "flag": "\U0001f1e9\U0001f1ea"},
        "fr": {"name": _("Fran\u00e7ais"), "flag": "\U0001f1eb\U0001f1f7"},
        "lu": {"name": _("L\u00ebtzebuergesch"), "flag": "\U0001f1f1\U0001f1fa"},
    }

    @property
    def get_languages_display(self):
        """Return list of dicts with code/name/flag for each event language."""
        if not self.languages:
            return []
        return [
            {
                "code": code,
                "name": str(self.LANGUAGE_DISPLAY.get(code, {}).get("name", code)),
                "flag": self.LANGUAGE_DISPLAY.get(code, {}).get("flag", ""),
            }
            for code in self.languages
            if code in self.LANGUAGE_DISPLAY
        ]

    def user_meets_language_requirement(self, user):
        """
        Check if a user meets the event's language requirement.
        Returns (bool, error_message).
        """
        if not self.languages:
            return True, ""

        try:
            profile = user.crushprofile
        except Exception:
            return False, _(
                "Please complete your profile before registering for this event."
            )

        user_languages = profile.event_languages or []
        if not user_languages:
            return False, _(
                "This event requires specific language skills. "
                "Please update your profile to include your event languages."
            )

        if not set(self.languages) & set(user_languages):
            lang_names = [
                str(self.LANGUAGE_DISPLAY.get(c, {}).get("name", c))
                for c in self.languages
            ]
            return False, _(
                "This event requires one of these languages: %(languages)s. "
                "Please update your profile languages if you speak any of them."
            ) % {"languages": ", ".join(lang_names)}

        return True, ""


class EventRegistration(models.Model):
    """User registration for meetup events"""

    STATUS_CHOICES = [
        ("pending", _("Pending Payment")),
        ("confirmed", _("Confirmed")),
        ("waitlist", _("Waitlist")),
        ("cancelled", _("Cancelled")),
        ("attended", _("Attended")),
        ("no_show", _("No Show")),
    ]

    event = models.ForeignKey(MeetupEvent, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True
    )

    # Additional info
    accessibility_needs = models.TextField(
        blank=True, help_text=_("Any accessibility accommodations needed")
    )
    dietary_restrictions = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Only for events with food component"),
    )
    bringing_guest = models.BooleanField(
        default=False, help_text=_("Attending with a guest")
    )
    guest_name = models.CharField(
        max_length=100, blank=True, help_text=_("Guest's name (if bringing someone)")
    )
    special_requests = models.TextField(blank=True)

    # Payment (if applicable)
    payment_confirmed = models.BooleanField(default=False)
    payment_date = models.DateTimeField(null=True, blank=True)

    # QR Check-in
    checkin_token = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=_("Signed token for QR check-in"),
    )
    checked_in_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the attendee was checked in via QR scan"),
    )

    # Google Wallet Event Ticket
    google_wallet_ticket_object_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text=_("Google Wallet EventTicketObject ID"),
    )

    # Apple Wallet Event Ticket
    apple_wallet_ticket_serial = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=_("Apple Wallet event ticket serial number"),
    )

    # Post-event feedback email tracking (idempotency for the
    # send_event_feedback_requests mgmt command).
    feedback_request_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp the post-event feedback survey email was sent"),
    )

    # Post-event recap email tracking (24h after event end)
    recap_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp the post-event recap email was sent"),
    )

    # Timestamps
    registered_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("event", "user")
        ordering = ["registered_at"]
        indexes = [
            # Optimize COUNT queries for get_confirmed_count() and get_waitlist_count()
            models.Index(
                fields=["event", "status"], name="eventregistration_event_status"
            ),
            # Covers filter(user=..., status=...) for user-centric dashboards
            # ("my upcoming events", "my attended events"). The unique_together
            # index leads with event, so user-first queries aren't served.
            models.Index(
                fields=["user", "status"],
                name="eventreg_user_status",
            ),
        ]

    def __str__(self):
        return (
            f"{self.user.username} - {self.event.title} ({self.get_status_display()})"
        )

    @property
    def can_make_connections(self):
        """Only attendees can make post-event connections"""
        return self.status == "attended"


class EventInvitation(models.Model):
    """
    Private invitation for exclusive events.
    Tracks invitations sent to guests for invitation-only events.
    """

    STATUS_CHOICES = [
        ("pending", "Invitation Sent"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
        ("attended", "Attended"),
        ("expired", "Expired"),
    ]

    APPROVAL_CHOICES = [
        ("pending_approval", "Awaiting Approval"),
        ("approved", "Approved to Attend"),
        ("rejected", "Rejected"),
    ]

    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="invitations"
    )
    guest_email = models.EmailField(help_text=_("Guest's email address"))
    guest_first_name = models.CharField(
        max_length=100, help_text=_("Guest's first name")
    )
    guest_last_name = models.CharField(max_length=100, help_text=_("Guest's last name"))

    # Link to Special User Experience (optional - for VIP treatment)
    special_user = models.ForeignKey(
        SpecialUserExperience,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event_invitations",
        help_text=_(
            "Link this invitation to a Special User for VIP treatment (auto-fills from name/email match)"
        ),
    )

    # Invitation details
    invitation_code = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text=_("Unique invitation code (UUID)"),
    )
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="invitations_sent",
        help_text=_("Coach/admin who sent the invitation"),
    )

    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        help_text=_("Invitation status"),
    )
    approval_status = models.CharField(
        max_length=20,
        choices=APPROVAL_CHOICES,
        default="pending_approval",
        help_text=_("Approval status (coach must approve before attendance)"),
    )

    # Created user after acceptance
    created_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_invitation",
        help_text=_("User account created when invitation was accepted"),
    )

    # Timestamps
    invitation_sent_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    # Admin notes
    approval_notes = models.TextField(
        blank=True, help_text=_("Internal notes about approval/rejection")
    )
    coach_notes = models.TextField(
        blank=True, help_text=_("Coach notes about the guest")
    )

    class Meta:
        ordering = ["-invitation_sent_at"]
        verbose_name = _("Event Invitation")
        verbose_name_plural = _("Event Invitations")

    def __str__(self):
        return f"{self.guest_first_name} {self.guest_last_name} → {self.event.title} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        """Generate invitation code on first save (handled by default now)"""
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        """Check if invitation has expired"""
        if (
            self.event.invitation_expires_at
            and timezone.now() > self.event.invitation_expires_at
        ):
            return True
        return False

    @property
    def invitation_url(self):
        """Generate the full invitation URL"""
        from django.urls import reverse

        return reverse(
            "crush_lu:invitation_landing", kwargs={"code": self.invitation_code}
        )


class GlobalActivityOption(models.Model):
    """
    Global activity options used across all Crush events.
    These are defined once and reused for all events - no need to recreate per event.
    """

    ACTIVITY_TYPE_CHOICES = [
        ("presentation_style", "Presentation Style (Phase 2)"),
        ("speed_dating_twist", "Speed Dating Twist (Phase 3)"),
    ]

    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPE_CHOICES)
    activity_variant = models.CharField(
        max_length=20,
        unique=True,
        help_text=_("Unique identifier (e.g., 'music', 'spicy_questions')"),
    )
    display_name = models.CharField(max_length=200)
    display_name_fr = models.CharField(max_length=200, blank=True, default="")
    description = models.TextField()
    description_fr = models.TextField(blank=True, default="")
    is_active = models.BooleanField(
        default=True, help_text=_("Inactive options won't appear in voting")
    )
    sort_order = models.PositiveIntegerField(
        default=0, help_text=_("Display order in voting UI")
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["activity_type", "sort_order", "display_name"]
        verbose_name = _("Global Activity Option")
        verbose_name_plural = _("Global Activity Options")

    def get_display_name(self, language=None):
        from django.utils.translation import get_language

        lang = language or get_language() or "en"
        if lang.startswith("fr") and self.display_name_fr:
            return self.display_name_fr
        return self.display_name

    def get_description(self, language=None):
        from django.utils.translation import get_language

        lang = language or get_language() or "en"
        if lang.startswith("fr") and self.description_fr:
            return self.description_fr
        return self.description

    def __str__(self):
        return f"{self.get_activity_type_display()}: {self.display_name}"


class EventActivityOption(models.Model):
    """Activity options available for event voting - Two categories"""

    ACTIVITY_TYPE_CHOICES = [
        ("presentation_style", "Presentation Style (Phase 2)"),
        ("speed_dating_twist", "Speed Dating Twist (Phase 3)"),
    ]

    ACTIVITY_VARIANT_CHOICES = [
        # Presentation Style variants (Phase 2)
        ("music", "With Favorite Music"),
        ("questions", "5 Predefined Questions"),
        ("picture_story", "Share Favorite Picture & Story"),
        # Speed Dating Twist variants (Phase 3)
        ("spicy_questions", "Spicy Questions First"),
        ("forbidden_word", "Forbidden Word Challenge"),
        ("open_conversation", "Open Conversation"),
        ("theme_based", "Theme Based Conversation"),
    ]

    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="activity_options"
    )
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPE_CHOICES)
    activity_variant = models.CharField(
        max_length=20,
        choices=ACTIVITY_VARIANT_CHOICES,
        blank=True,
        help_text=_("Sub-option for the activity"),
    )
    display_name = models.CharField(
        max_length=200, help_text=_("e.g., 'Speed Dating - Random Order'")
    )
    description = models.TextField(
        help_text=_("Explanation of what this activity entails")
    )
    vote_count = models.PositiveIntegerField(default=0)
    is_winner = models.BooleanField(default=False)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["activity_type", "activity_variant"]
        unique_together = ("event", "activity_type", "activity_variant")

    def __str__(self):
        return f"{self.event.title} - {self.display_name}"


class EventActivityVote(models.Model):
    """Individual votes from attendees for event activities (one vote per category)"""

    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="activity_votes"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    selected_option = models.ForeignKey(GlobalActivityOption, on_delete=models.CASCADE)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Each user can vote once per category per event (one presentation_style + one speed_dating_twist)
        unique_together = ("event", "user", "selected_option")
        ordering = ["-voted_at"]

    def __str__(self):
        return f"{self.user.username} voted for {self.selected_option.display_name}"


class PresentationQueue(models.Model):
    """Manages the order and status of presentations during Phase 2"""

    STATUS_CHOICES = [
        ("waiting", "Waiting to Present"),
        ("presenting", "Currently Presenting"),
        ("completed", "Presentation Completed"),
        ("skipped", "Skipped"),
    ]

    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="presentation_queue"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    presentation_order = models.PositiveIntegerField(
        help_text=_("Order in queue (1, 2, 3...)")
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="waiting")

    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event", "presentation_order"]
        unique_together = ("event", "user")

    def __str__(self):
        return f"{self.event.title} - #{self.presentation_order}: {self.user.username}"

    @property
    def duration_seconds(self):
        """Calculate how long the presentation took"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class EventVotingSession(models.Model):
    """Manages voting session state for each event"""

    event = models.OneToOneField(
        MeetupEvent, on_delete=models.CASCADE, related_name="voting_session"
    )
    voting_start_time = models.DateTimeField(
        help_text=_("Event start time + 15 minutes")
    )
    voting_end_time = models.DateTimeField(
        help_text=_("Voting start time + 30 minutes")
    )
    is_active = models.BooleanField(default=False)
    total_votes = models.PositiveIntegerField(default=0)

    # Track winners for both categories
    winning_presentation_style = models.ForeignKey(
        GlobalActivityOption,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="won_presentation_events",
        limit_choices_to={"activity_type": "presentation_style"},
    )
    winning_speed_dating_twist = models.ForeignKey(
        GlobalActivityOption,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="won_speed_dating_events",
        limit_choices_to={"activity_type": "speed_dating_twist"},
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Voting Session for {self.event.title}"

    def save(self, *args, **kwargs):
        # Auto-calculate voting times if not set
        if not self.voting_start_time:
            self.voting_start_time = self.event.date_time + timedelta(minutes=15)
        if not self.voting_end_time:
            self.voting_end_time = self.voting_start_time + timedelta(minutes=30)
        super().save(*args, **kwargs)

    @property
    def is_voting_open(self):
        """Check if voting window is currently open"""
        now = timezone.now()
        return self.is_active and self.voting_start_time <= now <= self.voting_end_time

    @property
    def has_ended(self):
        """True only once the voting window has actually closed.

        Distinct from ``not is_voting_open``, which is also true *before*
        voting starts or while the session is inactive. Use this to gate
        winner calculation and presentation queue creation so they never
        run prematurely.
        """
        return timezone.now() > self.voting_end_time

    @property
    def has_concluded(self):
        """True once voting is over by *either* path.

        Covers the natural end (window elapsed, ``has_ended``) and a manual
        early end via :meth:`end_voting` — which deactivates the session and
        records a winner without moving ``voting_end_time``. A recorded
        winner is a reliable conclusion signal because ``calculate_winner``
        only runs from finalization paths. Use this to decide whether to
        show post-voting UI (results / presentation CTA); use ``has_ended``
        specifically when gating first-time queue creation.
        """
        return self.has_ended or bool(self.winning_presentation_style_id)

    @property
    def time_until_start(self):
        """Seconds until voting starts (negative if already started)"""
        return (self.voting_start_time - timezone.now()).total_seconds()

    @property
    def time_remaining(self):
        """Seconds remaining in voting window (0 if not started or ended)"""
        now = timezone.now()
        if now < self.voting_start_time:
            return 0
        if now > self.voting_end_time:
            return 0
        return (self.voting_end_time - now).total_seconds()

    @property
    def presentations_skipped(self):
        """True if attendees voted to skip the presentation round."""
        return bool(
            self.winning_presentation_style
            and self.winning_presentation_style.activity_variant == "skip_presentations"
        )

    def start_voting(self):
        """Activate voting session"""
        self.is_active = True
        self.save()

    def end_voting(self):
        """End voting and calculate winner"""
        self.is_active = False
        self.calculate_winner()
        self.initialize_presentation_queue()
        self.save()

    def calculate_winner(self):
        """Determine winning activity option for each category"""
        from django.db.models import Count

        # Count votes for each EventActivityOption for presentation style
        presentation_votes = (
            EventActivityVote.objects.filter(
                event=self.event, selected_option__activity_type="presentation_style"
            )
            .values("selected_option__activity_variant")
            .annotate(vote_count=Count("id"))
            .order_by("-vote_count")
        )

        if presentation_votes:
            winner_variant = presentation_votes[0]["selected_option__activity_variant"]
            try:
                self.winning_presentation_style = GlobalActivityOption.objects.get(
                    activity_variant=winner_variant
                )
            except GlobalActivityOption.DoesNotExist:
                pass

        # Count votes for each EventActivityOption for speed dating twist
        twist_votes = (
            EventActivityVote.objects.filter(
                event=self.event, selected_option__activity_type="speed_dating_twist"
            )
            .values("selected_option__activity_variant")
            .annotate(vote_count=Count("id"))
            .order_by("-vote_count")
        )

        if twist_votes:
            winner_variant = twist_votes[0]["selected_option__activity_variant"]
            try:
                self.winning_speed_dating_twist = GlobalActivityOption.objects.get(
                    activity_variant=winner_variant
                )
            except GlobalActivityOption.DoesNotExist:
                pass

    def initialize_presentation_queue(self):
        """Initialize presentation queue with all checked-in (attended) users in random order.

        Idempotent: skips initialization if queue entries already exist for this event.
        Does nothing if attendees voted to skip the presentation round.
        """
        from django.contrib.auth.models import User
        import random

        # Do not create a queue if the group voted to skip presentations
        if self.presentations_skipped:
            return

        # Skip if queue already initialized (prevents inconsistent re-shuffles)
        if PresentationQueue.objects.filter(event=self.event).exists():
            return

        # Only include users who have checked in (attended), not just confirmed
        attendees = User.objects.filter(
            eventregistration__event=self.event,
            eventregistration__status="attended",
        ).distinct()

        # Create a shuffled list of attendees
        attendee_list = list(attendees)
        random.shuffle(attendee_list)

        # Create presentation queue entries
        for order, user in enumerate(attendee_list, start=1):
            PresentationQueue.objects.create(
                event=self.event, user=user, presentation_order=order
            )


class PresentationRating(models.Model):
    """Anonymous yes/no first-impression ratings during presentations"""

    event = models.ForeignKey(
        MeetupEvent, on_delete=models.CASCADE, related_name="presentation_ratings"
    )
    presenter = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="presentations_received"
    )
    rater = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="presentations_given"
    )
    is_positive = models.BooleanField(
        help_text=_("Whether this person left a positive first impression"),
    )

    # Metadata
    rated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "presenter", "rater")
        ordering = ["-rated_at"]

    def __str__(self):
        impression = "✓" if self.is_positive else "✗"
        return f"{self.rater.username} → {self.presenter.username}: {impression}"

    @staticmethod
    def get_mutual_rating_score(event, user1, user2):
        """
        Mutual impression score for the pairing algorithm.
        2.0 = both said yes, 1.0 = one said yes, 0.0 = both said no / no data.
        """
        try:
            r1 = PresentationRating.objects.get(
                event=event, presenter=user2, rater=user1
            ).is_positive
        except PresentationRating.DoesNotExist:
            r1 = None

        try:
            r2 = PresentationRating.objects.get(
                event=event, presenter=user1, rater=user2
            ).is_positive
        except PresentationRating.DoesNotExist:
            r2 = None

        if r1 is True and r2 is True:
            return 2.0
        if r1 is True or r2 is True:
            return 1.0
        return 0.0


class EventFeedback(models.Model):
    """Post-event survey response from an attendee.

    Free-text fields are visible to coaches only; aggregate NPS / would-recommend
    stats are surfaced to coaches on the per-event detail page.
    """

    event = models.ForeignKey(
        MeetupEvent,
        on_delete=models.CASCADE,
        related_name="feedback",
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    nps_score = models.PositiveSmallIntegerField(
        help_text=_(
            "Net Promoter Score: 0 (would not recommend) to 10 (would strongly recommend)"
        ),
    )
    would_recommend = models.BooleanField(
        default=True,
        help_text=_(
            "Quick yes/no convenience flag derived from NPS at submission time"
        ),
    )
    what_worked = models.TextField(
        blank=True,
        help_text=_("Free-text: what the attendee enjoyed. Visible to coaches only."),
    )
    what_to_improve = models.TextField(
        blank=True,
        help_text=_("Free-text: suggestions for next event. Visible to coaches only."),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("event", "user")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event", "-created_at"]),
        ]

    def __str__(self):
        return (
            f"Feedback {self.user.username} → {self.event.title} (NPS {self.nps_score})"
        )

    @property
    def is_promoter(self):
        return self.nps_score >= 9

    @property
    def is_detractor(self):
        return self.nps_score <= 6
