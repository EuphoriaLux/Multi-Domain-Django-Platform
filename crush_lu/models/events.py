from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MaxValueValidator, RegexValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from datetime import timedelta
import re
import uuid
from .profiles import CrushCoach, SpecialUserExperience
from crush_lu.storage import crush_upload_path, crush_media_storage

# Enforced ceiling on event length (minutes). Module-level so it is reachable
# from the field definition, the Meta CheckConstraint, and live-event lookbacks
# across surfaces (home page, event list, nav menu). See MeetupEvent.
MAX_EVENT_DURATION_MINUTES = 7 * 24 * 60  # 7 days

# Registration statuses that occupy a seat.
#
# "pending" means Pending Payment: a paid event's signup lands here and holds
# its seat until the SumUp return handler flips it to "confirmed". It has to be
# counted, or a paid event would never fill up and would never waitlist anyone.
#
# Defined once because two places consume it -- MeetupEventQuerySet's annotation
# and MeetupEvent.get_confirmed_count() -- and get_confirmed_count() *prefers
# the annotation when present*, so if the two lists ever disagree the same event
# reports different capacities depending on how it was fetched.
SEAT_HOLDING_STATUSES = ["confirmed", "attended", "pending"]

# "applied" is deliberately NOT in the list above. A curated speed-dating
# application is an expression of interest, not a seat: forty people may apply
# for twenty places, and the organiser picks. Because capacity, door tickets,
# check-in, reminders, wallet passes and the KPI/metrics rollups all derive
# from SEAT_HOLDING_STATUSES, leaving "applied" out of it is what keeps an
# application from consuming a place or minting a ticket. Selection is a move
# from "applied" into a seat-holding status, and that single transition is what
# grants all of the above at once.

# Luxembourg's 12 cantons, stored as display names rather than slugs.
#
# The profile side keeps the same 12 as `canton-*` slugs (crush_lu/forms.py),
# because those key an interactive SVG map. Events have no map, and `canton` is
# rendered raw in a dozen places -- OG tags, meta description, JSON-LD
# addressRegion, the anonymous location teaser -- so slugs would need a display
# lookup at every one of them, and would silently publish "canton-esch" at any
# site that was missed. The two vocabularies stay separate on purpose.
#
# Cantons rather than communes: this field is a privacy control. The event page
# shows it to anonymous visitors *instead of* the address, and a commune -- many
# under 2,000 residents -- narrows an unlisted venue to roughly a street. The
# precise locality now lives in `address_town`, which is what removed the
# pressure that made canton do both jobs.
CANTON_CHOICES = [
    ("Capellen", _("Capellen")),
    ("Clervaux", _("Clervaux")),
    ("Diekirch", _("Diekirch")),
    ("Echternach", _("Echternach")),
    ("Esch-sur-Alzette", _("Esch-sur-Alzette")),
    ("Grevenmacher", _("Grevenmacher")),
    ("Luxembourg", _("Luxembourg")),
    ("Mersch", _("Mersch")),
    ("Redange", _("Redange")),
    ("Remich", _("Remich")),
    ("Vianden", _("Vianden")),
    ("Wiltz", _("Wiltz")),
]

# A Luxembourg postcode once whitespace is out of the way: four digits behind
# an optional national "L-" prefix.
#
# Whitespace is stripped separately rather than woven into this pattern. An
# earlier version read `^\s*(?:L\s*-?\s*)?(\d{4})\s*$`, where the leading `\s*`
# and the prefix group's own `\s*` can match the same run of spaces: input like
# "l" followed by many spaces gives the engine a quadratic number of ways to
# split them before it can fail, which CodeQL flags as a polynomial ReDoS. This
# field is filled from an admin form, so the input is attacker-influenced.
_LU_POSTCODE_INPUT_RE = re.compile(r"^(?:L-?)?([0-9]{4})$", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
# What the column is supposed to hold: four ASCII digits and nothing else. The
# input pattern above accepts an "L-" prefix by design, so it cannot double as
# the check for whether one still needs adding.
#
# Always used with `fullmatch`: a bare `$` also matches immediately before a
# trailing newline, so `match` would accept a postcode with one on the end.
_LU_POSTCODE_STORED_RE = re.compile(r"^[0-9]{4}$")


def normalize_lu_postcode(value):
    """Reduce a typed Luxembourg postcode to its four bare digits.

    Accepts "L-2229", "L2229", "l - 2229" and "2229". Anything else is handed
    back stripped but otherwise untouched, so the field's RegexValidator is what
    reports the typo -- this helper must never quietly swallow one, because a
    wrong postcode on a national portal sends people to the wrong town.
    """
    compact = _WHITESPACE_RE.sub("", value or "")
    match = _LU_POSTCODE_INPUT_RE.match(compact)
    return match.group(1) if match else (value or "").strip()


class MeetupEventQuerySet(models.QuerySet):
    """Custom QuerySet for MeetupEvent with performance optimizations."""

    def with_registration_counts(self):
        """
        Annotate queryset with confirmed_count, waitlist_count and applied_count.

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
                filter=Q(eventregistration__status__in=SEAT_HOLDING_STATUSES),
            ),
            waitlist_count_annotated=Count(
                "eventregistration", filter=Q(eventregistration__status="waitlist")
            ),
            # Curated applications, kept apart from confirmed_count so an
            # over-subscribed applicant pool can never read as a full event.
            applied_count_annotated=Count(
                "eventregistration", filter=Q(eventregistration__status="applied")
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

    # Reachable as MeetupEvent.CANTON_CHOICES for callers that validate against
    # the list without importing the module constant.
    CANTON_CHOICES = CANTON_CHOICES

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
    # The venue address, one component per field. echo.lu publishes these
    # separately on a national listing and renders whatever it is given
    # verbatim, so they are captured as typed rather than parsed back out of
    # free text.
    #
    # All blank=True, and nothing requires them yet: making them mandatory for
    # published events is deliberately held back until the backfill has run
    # (`manage.py backfill_event_addresses`), because `clean()` fires on every
    # admin save and would otherwise block a coach editing an unrelated field
    # on an event whose address is still legacy text.
    address_street = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Street"),
        help_text=_("Street name only, without the house number (e.g. 'rue du Nord')."),
    )
    address_number = models.CharField(
        max_length=20,
        blank=True,
        verbose_name=_("House number"),
        help_text=_(
            "House number exactly as written: '7', '12A', '12-14'. "
            "Leave blank if the venue has no number."
        ),
    )
    address_postcode = models.CharField(
        max_length=4,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^[0-9]{4}$",
                message=_("Luxembourg postcodes are exactly four digits."),
            )
        ],
        verbose_name=_("Postcode"),
        help_text=_(
            "Four digits. Type 'L-2229' or '2229' - the 'L-' is added on display."
        ),
    )
    address_town = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Town"),
        help_text=_(
            "The town the venue is in (e.g. 'Luxembourg', 'Differdange'). This is "
            "NOT the canton - the canton is its own field."
        ),
    )
    address = models.TextField(
        blank=True,
        verbose_name=_("Legacy address (free text)"),
        help_text=_(
            "Being replaced by the street/number/postcode/town fields above. Kept so "
            "events created before the split keep rendering, and still what "
            "echo.lu publishes until an event's street is filled in."
        ),
    )
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
        choices=CANTON_CHOICES,
        help_text=_(
            "The region anonymous visitors see instead of the exact address. "
            "This is the canton, not the town - the town has its own field."
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
            "Seats within the total capacity held back for Premium members "
            "(an active paid membership — an assigned coach alone does not "
            "count). General members fill only (max participants − reserved). "
            "0 = no reservation."
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
    # Labels name what each option CHECKS, not what it was intended for — the
    # previous wording described intent and diverged from the gates in both
    # directions (see docs/superpowers/specs/2026-07-27-profile-requirement-audit.md).
    # Notably `coach_assigned` is NOT a Premium check: a coach is auto-assigned
    # on first attendance without payment, so it admits every past attendee.
    # `CrushProfile.has_active_premium` is the real entitlement.
    PROFILE_REQUIREMENT_CHOICES = [
        ("none", _("All logged-in people")),
        ("completed", _("Participation-ready Crush profile")),
        ("approved", _("Verified members only")),
        ("profile_exists", _("Any Crush profile (except rejected)")),
        ("unverified", _("Not-yet-verified profiles only (except rejected)")),
        (
            "coach_assigned",
            _("Members with an assigned coach (not Premium)"),
        ),
    ]
    profile_requirement = models.CharField(
        max_length=20,
        choices=PROFILE_REQUIREMENT_CHOICES,
        default="completed",
        help_text=_("Controls which audience can register for this event."),
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

    # How a sign-up becomes a seat.
    #
    # "direct" is the historical behaviour and stays the default: whoever
    # arrives first is admitted (or waitlisted) by the capacity check at signup
    # time, and a paid event holds the seat as "pending" until SumUp confirms.
    #
    # "curated" inverts that for speed dating: sign-ups land as "applied",
    # which holds no seat at all, and the organiser composes the group from the
    # applicant pool afterwards. Deliberately per-event rather than per-type —
    # switching every speed-dating event to an application flow at once would
    # change the deal for events already taking sign-ups.
    REGISTRATION_MODE_DIRECT = "direct"
    REGISTRATION_MODE_CURATED = "curated"
    REGISTRATION_MODE_CHOICES = [
        (REGISTRATION_MODE_DIRECT, _("Direct — first come, first served")),
        (REGISTRATION_MODE_CURATED, _("Curated — organiser selects the group")),
    ]
    registration_mode = models.CharField(
        max_length=10,
        choices=REGISTRATION_MODE_CHOICES,
        default=REGISTRATION_MODE_DIRECT,
        db_index=True,
        help_text=_(
            "Curated mode applies to speed dating only: sign-ups are held as "
            "applications that take no seat until an organiser selects them."
        ),
    )

    # Status & Features
    is_published = models.BooleanField(default=False)
    is_cancelled = models.BooleanField(default=False)
    organiser_cancellation_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "Start of the current organiser-cancellation cycle. Used to keep "
            "remedy emails from reusing credit issued before an event restore."
        ),
    )

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
                condition=models.Q(duration_minutes__lte=MAX_EVENT_DURATION_MINUTES),
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
            status__in=SEAT_HOLDING_STATUSES,
            user__crushprofile__gender__in=self.POOL_TO_CODES.get(pool, []),
        ).count()

    def is_gender_pool_full(self, gender_code):
        """True when the gender pool for this code has reached its cap."""
        limit = self.get_gender_pool_limit(gender_code)
        if limit is None:
            return False
        return self.get_confirmed_count_for_gender(gender_code) >= limit

    # Display labels for the three pools. Named here rather than in the
    # template so the member-facing wording and the pool keys cannot drift
    # apart, and so the admin's field verbose_names stay free to say
    # "Max spots (Men)" without that phrasing leaking onto the event page.
    GENDER_POOL_LABELS = {"m": _("Men"), "f": _("Women"), "nb": _("Other genders")}

    def get_gender_pool_availability(self, capacity_remaining=None):
        """Per-pool availability rows for display.

        Returns ``[]`` when the caps are not active, so callers can branch on
        truthiness alone and an uncapped event keeps its total-only display.

        Each row carries two different notions of "full", because they answer
        two different questions and conflating them is how a page ends up
        promising a seat nobody can take:

        ``pool_full``
            ``confirmed >= limit`` -- purely this pool's own cap. The exact
            predicate :meth:`is_gender_pool_full` re-checks under lock when
            registration decides who gets waitlisted.
        ``remaining`` / ``is_full``
            What a viewer could *actually* claim. Pass ``capacity_remaining``
            (from :meth:`spots_remaining_for`) and every pool is capped by it,
            so a total cap or a reserved-premium block that has already spoken
            for the seats cannot be advertised as pool availability. Left
            uncapped, these collapse onto the pool's own cap.

        One grouped query instead of three ``COUNT``s, because every caller
        renders all three pools together -- the same reason the coach list
        aggregates in a single pass (``views_coach._attach_event_gender_stats``).

        Counting mirrors :meth:`get_confirmed_count_for_gender` exactly,
        blind spots included: a seat held by someone with no ``CrushProfile``,
        or with a gender outside ``POOL_TO_CODES``, belongs to no pool and is
        counted in none of them. The two must agree -- this method decides what
        the event page *shows*, and that one decides who actually gets
        waitlisted, so a divergence would recreate the very mismatch this
        exists to fix (#866).
        """
        if not self.gender_limits_active:
            return []
        return self._pool_rows(self._seat_counts_by_gender(), capacity_remaining)

    def _seat_counts_by_gender(self):
        """One grouped query: ``{gender_code_or_None: seats}``.

        A seat held by someone with no ``CrushProfile`` groups under ``None``
        rather than being dropped -- the join is an outer one -- so these values
        sum to exactly :meth:`get_confirmed_count`. That is what lets
        :meth:`registration_capacity` take the *total* from this same read
        instead of counting again.
        """
        # order_by() strips any default ordering: a Meta.ordering field would
        # otherwise join the GROUP BY and split each gender into several rows.
        return dict(
            self.eventregistration_set.filter(status__in=SEAT_HOLDING_STATUSES)
            .order_by()
            .values_list("user__crushprofile__gender")
            .annotate(seats=models.Count("id"))
        )

    def _pool_rows(self, counts, capacity_remaining=None):
        """Build the display rows from an already-fetched count map."""
        limits = {
            "m": self.max_participants_m,
            "f": self.max_participants_f,
            "nb": self.max_participants_nb,
        }
        pools = []
        for key in ("m", "f", "nb"):
            limit = limits[key]
            confirmed = sum(counts.get(code, 0) for code in self.POOL_TO_CODES[key])
            remaining = max(0, limit - confirmed)
            if capacity_remaining is not None:
                remaining = min(remaining, max(0, capacity_remaining))
            pools.append(
                {
                    "key": key,
                    "label": self.GENDER_POOL_LABELS[key],
                    "limit": limit,
                    "confirmed": confirmed,
                    "pool_full": confirmed >= limit,
                    "remaining": remaining,
                    "is_full": remaining == 0,
                }
            )
        return pools

    def registration_capacity(self, is_premium=False):
        """``(total_full, capacity_remaining, pools)`` -- the whole picture, one read.

        Asking for the total and the per-pool counts separately means two
        queries against two database states, and a registration landing between
        them makes a single page contradict itself: ``capacity_remaining`` from
        one moment marking every chip "full", beside a CTA still offering a seat
        because fullness was counted a moment earlier. That is the shape of #866
        rather than a fix for it, so both come from one read here.

        For a gender-capped event that read is :meth:`_seat_counts_by_gender`,
        whose rows already cover *every* seat-holding registration; an event
        without caps has no pools to fetch and falls back to a plain count.

        Either way the count is memoised under ``confirmed_count_annotated`` --
        the name :meth:`get_confirmed_count` already honours -- so every later
        capacity read on this instance is free *and* returns this same number.
        Without it the premium reserved-seat banner re-counts and can disagree
        with the CTA rendered beside it. Both branches must do this:
        ``reserved_premium_seats`` is independent of ``gender_limits_active``,
        so an uncapped event with reserved seats hits that race too. Request
        scoped by construction, since views load their own event instance.
        """
        if not self.gender_limits_active:
            self.confirmed_count_annotated = self.get_confirmed_count()
            total_full, remaining = self.capacity_snapshot(is_premium=is_premium)
            return total_full, remaining, []

        counts = self._seat_counts_by_gender()
        self.confirmed_count_annotated = sum(counts.values())

        cap = self.max_participants if is_premium else self.public_capacity
        remaining = max(0, cap - self.confirmed_count_annotated)
        return remaining == 0, remaining, self._pool_rows(counts, remaining)

    def clean(self):
        """Validate event data before saving"""
        from django.core.exceptions import ValidationError

        super().clean()

        # Eligibility lives inside unmet_publish_requirements(), which returns
        # nothing for an event echo.lu would not publish -- so cancelling,
        # making private, or editing a finished event is never blocked by an
        # address. See that method for why the gate is not written here.
        unmet = self.unmet_publish_requirements()
        if unmet:
            raise ValidationError(dict(unmet))

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
    def accepts_waitlist_promotion(self):
        """Whether a freed seat may still be handed to this event's waitlist.

        Promotion writes ``confirmed`` **and** sends a registration
        confirmation email, so doing it for a finished event tells someone they
        have a seat at a party that already happened, and for a cancelled event
        that a cancelled party is on.

        Deliberately a single definition: three call sites depend on this
        condition (both promotion signals and the member cancel view), and the
        incident that motivated it was one of them simply not having the check.
        Keeping three hand-copied variants is how the next one goes missing.

        ``is_published`` counts for the same reason ``is_cancelled`` does: the
        promoted member gets a confirmation email for an event that
        ``event_detail`` rejects and ``my_events`` hides, both of which require
        it — the same treatment ``event_lobby_phase`` gives an unpublished
        event.
        """
        return (
            self.is_published
            and not self.is_cancelled
            and self.date_time > timezone.now()
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

    def capacity_snapshot(self, is_premium=False):
        """``(is_full, spots_remaining)`` from a *single* count.

        :meth:`is_full_for` and :meth:`spots_remaining_for` each issue their own
        ``COUNT``, so a caller needing both reads the database twice -- and one
        registration landing between those two reads lets a single response
        contradict itself: every pool chip full because ``capacity_remaining``
        came back zero, beside a CTA still promising a seat because fullness was
        counted a moment earlier. Anything that needs both must take them here.

        Premium members measure fullness against the total ``max_participants``;
        everyone else against ``public_capacity``. Premium here is an active
        ``PremiumMembership`` — ``CrushProfile.has_active_premium``, NOT
        ``assigned_coach``, which is also granted free on first attendance.
        """
        cap = self.max_participants if is_premium else self.public_capacity
        remaining = max(0, cap - self.get_confirmed_count())
        # `remaining == 0` and the older `count >= cap` are the same predicate --
        # max() only clamps the already-full side -- so the two helpers below
        # keep their exact behaviour while the capacity rule lives in one place.
        return remaining == 0, remaining

    def is_full_for(self, is_premium=False):
        """Capacity check that respects reserved premium seats.

        Premium members measure fullness against the total ``max_participants``;
        everyone else against ``public_capacity``. Premium here is an active
        ``PremiumMembership`` — ``CrushProfile.has_active_premium``, NOT
        ``assigned_coach``, which is also granted free on first attendance.
        """
        return self.capacity_snapshot(is_premium=is_premium)[0]

    def spots_remaining_for(self, is_premium=False):
        return self.capacity_snapshot(is_premium=is_premium)[1]

    @property
    def reserved_spots_remaining(self):
        """Unclaimed reserved seats (premium-only block at the top of capacity)."""
        confirmed = self.get_confirmed_count()
        total_remaining = max(0, self.max_participants - confirmed)
        public_remaining = max(0, self.public_capacity - confirmed)
        return total_remaining - public_remaining

    @property
    def street_line(self):
        """The street with its house number: "45, rue Emile Mark".

        One definition, because `full_address` and the schema.org PostalAddress
        both need it and a separator change made in only one of them would put
        two different addresses on the same page.
        """
        if not self.address_street:
            return ""
        if self.address_number:
            return f"{self.address_number}, {self.address_street}"
        return self.address_street

    def reaches_echo_lu(self, *, as_published=False):
        """Whether this event is one echo.lu would publish.

        `as_published=True` asks it of a DRAFT: "if this were published, would
        echo.lu take it?" The admin's bulk publish action needs that, because
        it checks before flipping the flag — asked plainly, a draft is never
        echo-eligible and the gate would never fire.

        Wraps `should_publish()` so callers get a straight answer even on a
        half-filled form: it reads `end_time`, which needs `date_time` and
        `duration_minutes`, and a form still missing those has its own errors
        to report rather than a spurious address complaint.
        """
        from ..services.echo_lu import should_publish

        was_published = self.is_published
        if as_published:
            # In memory only, and restored below — this asks a hypothetical.
            self.is_published = True
        try:
            return should_publish(self)
        except (TypeError, AttributeError):
            return False
        finally:
            self.is_published = was_published

    def unmet_publish_requirements(self, *, as_published=False):
        """What stops this event being published, as {field: message}.

        The single source of truth for "is this event fit for a public national
        listing" — and empty for any event echo.lu would not publish anyway.

        Three call sites depend on that being one answer: `clean()` raises on
        it, the admin's bulk publish action filters on it, and
        `backfill_event_addresses --audit` gates on it. Each has already grown
        its own version of the rule once and drifted — the bulk action forgot
        `canton`, the audit checked the postcode was present rather than valid
        — and when the eligibility gate was added to `clean()` alone, the other
        two became STRICTER than the rule they enforce, blocking cancelled and
        finished events nobody can fix by editing an address.

        So eligibility lives here too, not at the call sites.

        `address_number` is deliberately absent: real venues have none, and a
        required number field only invites a made-up one.
        """
        if not self.reaches_echo_lu(as_published=as_published):
            return {}

        unmet = {}
        # Membership, not just presence. `.update()` and `.create()` skip the
        # field's own choice validation, so a canton like "test" or a commune
        # where a canton belongs can already be in the column -- and it is
        # rendered raw in OG tags and JSON-LD.
        valid_cantons = {value for value, _label in CANTON_CHOICES}
        if self.canton not in valid_cantons:
            unmet["canton"] = _("A recognised canton is required for published events.")
        for name in ("address_street", "address_town"):
            # `.strip()`: a whitespace-only value is truthy and would pass as
            # complete, then reach echo.lu as a blank line on a public listing.
            if not (getattr(self, name) or "").strip():
                unmet[name] = _("Required for published events.")
        # Validity, not just presence: `.create()` and `.update()` skip field
        # validators, so an impossible postcode can already be sitting in the
        # column -- and echo.lu is now sent it verbatim.
        if not _LU_POSTCODE_STORED_RE.fullmatch(self.address_postcode or ""):
            unmet["address_postcode"] = _(
                "A four-digit postcode is required for published events."
            )
        return unmet

    @property
    def postcode_display(self):
        """The postcode as Luxembourg writes it: "L-2229".

        The prefix is only added to something that is actually four digits.
        `normalize_lu_postcode` runs in the admin form, but `.create()` and
        `.update()` call neither it nor `full_clean()`, so a fixture or a data
        migration can put anything in this column -- and "L-L-2229" on a ticket
        would be a self-inflicted wound.
        """
        if not self.address_postcode:
            return ""
        if _LU_POSTCODE_STORED_RE.fullmatch(self.address_postcode):
            return f"L-{self.address_postcode}"
        return self.address_postcode

    @property
    def structured_address(self):
        """The structured fields on one line, or "" if none are filled in."""
        parts = []
        if self.street_line:
            parts.append(self.street_line)
        locality = " ".join(
            part for part in (self.postcode_display, self.address_town) if part
        )
        if locality:
            parts.append(locality)
        return ", ".join(parts)

    @property
    def full_address(self):
        """The venue address on one line, for tickets, e-mails, ICS and JSON-LD.

        Formats as "7, rue du Nord, L-2229 Luxembourg". Missing components are
        dropped rather than left as gaps or stray separators. Returns "" when
        there is nothing at all, because several templates guard on the
        truthiness of the address before printing a label.

        **The legacy free text wins over a half-filled structured address.**
        Transcribing an address into four boxes is not atomic -- a coach who
        fills in the town and saves would otherwise turn
        "7, rue du Nord, L-2229 Luxembourg" into "Luxembourg" on every wallet
        pass, ticket, e-mail and calendar entry already issued. So the
        structured fields only take over once `address_street` is set, which is
        the component that makes them at least as informative as the text they
        replace. With no legacy text there is nothing to lose, so whatever is
        filled in is used.
        """
        composed = self.structured_address
        if self.address_street:
            return composed
        return (self.address or "").strip() or composed

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
        return not self.is_cancelled and self.date_time <= now < self.end_time

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
            status__in=SEAT_HOLDING_STATUSES
        ).count()

    @property
    def uses_curated_registration(self):
        """Do sign-ups on this event land as applications rather than seats?

        Both halves are required. ``registration_mode`` is a plain field an
        admin can set on any event, but the curated flow only makes sense where
        a preference snapshot is collected to compose the group — which is
        speed dating alone (see ``event_register``). Checking the type here,
        rather than trusting the field, keeps a mixer accidentally flipped to
        "curated" behaving exactly as it does today.
        """
        return (
            self.event_type == "speed_dating"
            and self.registration_mode == self.REGISTRATION_MODE_CURATED
        )

    def get_applied_count(self):
        """Applications awaiting organiser selection.

        Counted separately from ``get_confirmed_count`` on purpose: these hold
        no seat, so they must never be folded into capacity.
        """
        if hasattr(self, "applied_count_annotated"):
            return self.applied_count_annotated
        return self.eventregistration_set.filter(status="applied").count()

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
        # Curated speed dating only: an application awaiting organiser
        # selection. Holds no seat (see SEAT_HOLDING_STATUSES).
        ("applied", _("Applied — awaiting selection")),
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
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "When this registration entered cancelled status. Used to apply "
            "the cancellation policy at the member's actual cancellation time."
        ),
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

    # A waitlist promotion is only a *candidate* resale until the replacement
    # actually pays. These links keep that obligation durable across the
    # redirect/webhook boundary. They are cleared when payment settles or when
    # this reusable registration row starts a fresh registration cycle.
    resale_source_registration = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resale_replacements",
    )
    resale_source_payment = models.ForeignKey(
        "crush_lu.PaymentTransaction",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resale_replacements",
    )
    resale_beneficiary = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pending_resale_replacements",
        help_text=_(
            "The member owed the resale share. Kept separately so account merges "
            "or source-registration deletion cannot erase the obligation."
        ),
    )
    organiser_cancellation_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "When the organiser-cancellation remedy email was delivered. Used to "
            "resume bounded cancellation batches safely."
        ),
    )

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

    # --- Undo provenance -------------------------------------------------
    # How this attendance was reached, written at the moment it is reached.
    # `coach_undo_checkin` used to infer both of these and got both wrong:
    # it hardcoded `confirmed` as the status to restore (so undoing a
    # mistaken waitlist promotion handed out a seat that was never given),
    # and it read `assigned_coach_at >= checked_in_at` as "this check-in
    # granted the coach". A timestamp establishes ordering, not which
    # workflow did the writing — `PremiumMembership.confirm()` and an admin
    # reassignment write the same two profile fields, so a premium
    # confirmation four minutes after a scan read as a door grant and the
    # undo stripped a *paid* coach with nothing able to restore it.
    #
    # Cleared again by the undo, so a row that is not `attended` carries no
    # stale provenance for whatever marks it attended next.
    checkin_prior_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text=_("Status this registration held immediately before check-in"),
    )
    checkin_granted_coach = models.ForeignKey(
        CrushCoach,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_(
            "Coach this check-in granted the member as their permanent coach "
            "(empty when the member already had one)"
        ),
    )
    checkin_granted_coach_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "The exact CrushProfile.assigned_coach_at this check-in wrote. "
            "Undo clears the coach only while the profile still holds this "
            "pair, so any later write — premium confirmation, admin "
            "reassignment — survives the undo."
        ),
    )
    checkin_attested_photo_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        editable=False,
        help_text=_(
            "Photo key verified during this check-in. Cleared on undo to revoke "
            "the in-person photo trust badge."
        ),
    )
    checkin_attested_photo_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text=_("When the photo was attested during this check-in."),
    )
    checkin_auto_verified = models.BooleanField(
        default=False,
        editable=False,
        help_text=_(
            "True if this check-in auto-verified a previously pending profile."
        ),
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
    apple_wallet_auth_token = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Apple Wallet PassKit auth token for this ticket. Set when the "
            "ticket is built so the web service can authenticate update "
            "requests for it, including for attendees with no CrushProfile."
        ),
    )
    apple_wallet_language = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text=_(
            "Language this Apple ticket was issued in. A PassKit rebuild "
            "carries no locale, so without this the pass would come back in "
            "English — and an open-event attendee may have no CrushProfile "
            "whose preference could stand in."
        ),
    )
    apple_wallet_checkin_origin = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "scheme://host that issued this ticket's check-in QR. A check-in "
            "token only validates in the environment that minted it, and a "
            "PassKit rebuild has no request to derive the host from — the "
            "forwarded webServiceURL points at whatever the setting names, "
            "which may be a different slot. Persisted so rebuilds keep the "
            "original check-in host."
        ),
    )

    # Pre-event reminder tracking (idempotency for the send_event_reminders
    # mgmt command, now driven unattended by the EventReminders timer).
    # Only the day-granularity mode stamps these: `--hours-before` and
    # `--days N` are different reminders and must not be suppressed by them.
    #
    # Two markers because the channels fail independently. The sweep repeats
    # hourly through the day so a failed email is recoverable, but push and the
    # in-app bell are fire-and-forget — replaying them would mean up to twelve
    # pushes during an email outage. `reminder_notified_at` records that the
    # non-email channels have had their turn, so later passes retry the email
    # alone; `reminder_sent_at` records that the email itself is settled and
    # takes the registration out of the sweep entirely.
    reminder_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp push + in-app reminder channels were fired"),
    )
    reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Timestamp the day-before reminder email was settled"),
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

    @staticmethod
    def applied_status_message(event):
        """Why "applied" is wrong for ``event``, or None when it is allowed.

        ``applied`` is deliberately outside ``SEAT_HOLDING_STATUSES``, so
        setting it releases the member's seat, voids their door ticket and
        drops them from reminders and the wallet pass. On a curated event that
        is precisely the point. On a direct-mode event -- every mixer and every
        speed dating running the ordinary flow -- it is a misclick in an admin
        dropdown that costs a member their place with nothing on screen to say
        so, and no email either.

        One rule, one message, two callers: :meth:`clean` below and
        ``EventRegistrationAdminForm.clean``. They see the event by different
        routes and neither reaches every case alone, so keeping the rule here
        is what stops them drifting apart.

        ``event`` of None means "cannot tell" -- never "allowed". The callers
        only pass None when there is no event to judge against at all, which is
        an incomplete form whose required-field error is its own to report.

        Returns the message rather than a built ``ValidationError`` so each
        caller can aim it: at the ``status`` field where the form has one, and
        as a non-field error where it does not, rather than ``add_error``
        raising ``ValueError`` over a field that is not there.
        """
        if event is None or event.uses_curated_registration:
            return None
        return _(
            "“Applied” belongs to speed-dating events whose registration "
            "mode is Curated. “%(title)s” admits members on arrival, so an "
            "application here would release the seat without telling anyone. "
            "Set the event to Curated first, or choose another status."
        ) % {"title": event.title}

    def clean(self):
        """Backstop for the "applied" rule, for anything running full_clean().

        Covers the standalone change page and any other ModelForm over this
        model. It canNOT cover the admin inline under a *new* event, and that
        is not a fixable oversight: ``BaseInlineFormSet._construct_form`` sets
        only ``event_id = parent.pk`` -- None while the parent is unsaved --
        and ``construct_instance`` never copies the FK object across, because
        ``EventRegistrationInline.fields`` does not list ``event``. So the row
        being validated genuinely does not know its event, by any route the
        model can reach. The parent exists only on the formset, which is why
        ``EventRegistrationAdminForm.clean`` carries the same check against
        the event it resolves from ``cleaned_data`` -- and why that form, not
        this method, is what actually guards the inline.
        """
        super().clean()
        if self.status != "applied":
            return
        try:
            event = self.event
        except ObjectDoesNotExist:
            event = None
        message = self.applied_status_message(event)
        if message is not None:
            raise ValidationError({"status": message})

    def save(self, *args, **kwargs):
        """Keep the cancellation policy timestamp aligned with the status."""
        update_fields = kwargs.get("update_fields")
        if self.status == "cancelled" and self.cancelled_at is None:
            self.cancelled_at = timezone.now()
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"cancelled_at"}
        elif self.status != "cancelled" and self.cancelled_at is not None:
            # EventRegistration rows are reused on re-registration. A later
            # cancellation is a new policy decision and needs a fresh time.
            self.cancelled_at = None
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"cancelled_at"}
        return super().save(*args, **kwargs)

    @property
    def can_make_connections(self):
        """May this member use the post-event connection features?

        Attendance alone is not enough: a profile door-rejected by a coach
        (photo mismatch, #713) must lose the named attendee roster, connection
        requests and responses the moment the rejection lands. Every caller of
        this property (all in views_connections.py) gates the ACTING member's
        own access, so the verification re-check lives here — the single door —
        mirroring the read-time re-check the Event Lobby already does
        (services/event_lobby.py::eligible_participations) and the invariant
        documented at context_processors.py (verification gates Connect
        surfaces). ``verification_status`` is the single source of truth
        (``save()`` syncs the legacy ``is_approved`` flag into it).

        Members with no CrushProfile are denied, never crashed on.
        """
        if self.status != "attended":
            return False
        profile = getattr(self.user, "crushprofile", None)
        return profile is not None and profile.verification_status == "verified"


class EventRegistrationPreference(models.Model):
    """Per-application dating preferences for speed-dating events.

    A side model rather than columns on EventRegistration, on the
    CrushConnectMembership precedent (models/crush_connect.py): preferences sit
    next to the feature that consumes them while the registration row keeps
    only identity/payment/attendance data. The split is also a GDPR lifecycle
    boundary — ``preferred_genders`` can reveal sexual orientation (Art. 9
    special category), so these rows are pruned by ``gdpr_retention_cleanup``
    shortly after the event, while the registration row itself persists for
    payment lineage and attendance history. Never render these values to other
    members; they are organiser-only input for composing the group.

    Field names deliberately mirror CrushConnectMembership so the duck-typed
    scorers in crush_lu/matching.py accept either object unchanged.

    ``languages`` uses the 4-code event vocabulary
    (CrushProfile.EVENT_LANGUAGE_CHOICES), not Connect's 8-code set: it must
    intersect MeetupEvent.languages and other applicants' profile
    event_languages, which both speak the 4-code vocabulary.
    """

    registration = models.OneToOneField(
        EventRegistration,
        on_delete=models.CASCADE,
        related_name="preference",
    )
    # Empty list = open to all genders (same semantics as Connect).
    preferred_genders = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Gender codes the applicant wants to meet; empty = open to all"),
    )
    preferred_age_min = models.PositiveSmallIntegerField(default=18)
    preferred_age_max = models.PositiveSmallIntegerField(default=99)
    languages = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Event-language codes (en/de/fr/lu) the applicant wants at the "
            "table; empty = any"
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences for registration {self.registration_id}"

    def preferred_genders_display(self):
        """Display labels for the stored gender codes (organiser surfaces)."""
        from .profiles import CrushProfile

        labels = dict(CrushProfile.GENDER_CHOICES)
        return [labels[code] for code in self.preferred_genders if code in labels]

    def languages_display(self):
        """Display labels for the stored event-language codes."""
        from .profiles import CrushProfile

        labels = dict(CrushProfile.EVENT_LANGUAGE_CHOICES)
        return [labels[code] for code in self.languages if code in labels]


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
