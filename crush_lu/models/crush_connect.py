"""
Crush Connect models.

- ``CrushConnectWaitlist``: pre-launch waitlist for users interested in Crush Connect.
- ``SparkPrompt``: coach-authored prompts a sender answers when sending a
  Curiosity Spark (M1 of the Crush Connect rollout). The translatable ``text``
  field is what the sender sees as the question they're answering.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


# Languages spoken, shared in the Crush Connect catalogue. Uses ``"lu"`` (not
# ISO "lb") so prefilling from ``CrushProfile.event_languages`` is a straight
# copy. Overlap math is done in Python (SQLite JSON containment is unreliable).
CONNECT_LANGUAGE_CHOICES = [
    ("lu", _("Lëtzebuergesch")),
    ("fr", _("Français")),
    ("de", _("Deutsch")),
    ("en", _("English")),
    ("pt", _("Português")),
    ("it", _("Italiano")),
    ("es", _("Español")),
    ("other", _("Other")),
]


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
    # Beta tester selection — staff hand-pick the 20 "4 weeks / 4 matches" testers
    # and track the €10/month payment out-of-band (manual flag, no payment
    # processor — mirrors PremiumMembership.payment_confirmed / EventRegistration).
    #
    # Scope note: these flags are recruitment + teaser-status tracking ONLY. They
    # do NOT grant access to Crush Connect — the access gate
    # (views_crush_connect._user_passes_pre_onboarding_gate) intentionally still
    # requires a premium coach, because the existing gate guards the daily-drop
    # feature, not the coach-picked weekly-match concept this beta advertises.
    # Wiring tester access belongs with building that weekly-match delivery
    # (a deferred phase), not here.
    selected_as_tester = models.BooleanField(
        default=False,
        help_text=_("Marked as one of the 20 beta testers"),
    )
    selected_at = models.DateTimeField(null=True, blank=True)
    payment_confirmed = models.BooleanField(
        default=False,
        help_text=_("€10/month payment confirmed by staff (manual, no processor)"),
    )
    payment_date = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_crush_connect_payments",
        help_text=_("Staff member who confirmed the €10 payment"),
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

    # Connect onboarding — intent & lifestyle signals
    RELATIONSHIP_GOAL_CHOICES = [
        ("serious", _("Looking for something serious")),
        ("open", _("Open to see where it goes")),
        ("curious", _("Here to explore")),
    ]
    relationship_goal = models.CharField(
        max_length=20,
        blank=True,
        choices=RELATIONSHIP_GOAL_CHOICES,
        help_text=_("Member's relationship intent, set during Connect onboarding"),
    )
    LIFESTYLE_ENERGY_CHOICES = [
        ("homebody", _("Homebody")),
        ("mix", _("Mix of both")),
        ("adventurer", _("Adventurer")),
    ]
    lifestyle_energy = models.CharField(
        max_length=10,
        blank=True,
        choices=LIFESTYLE_ENERGY_CHOICES,
    )
    LIFESTYLE_SOCIAL_CHOICES = [
        ("intimate", _("Deep 1:1s")),
        ("flexible", _("Depends on mood")),
        ("social", _("Group energy")),
    ]
    lifestyle_social = models.CharField(
        max_length=10,
        blank=True,
        choices=LIFESTYLE_SOCIAL_CHOICES,
    )
    LIFESTYLE_PACE_CHOICES = [
        ("structured", _("Structured")),
        ("balanced", _("Balanced")),
        ("spontaneous", _("Spontaneous")),
    ]
    lifestyle_pace = models.CharField(
        max_length=12,
        blank=True,
        choices=LIFESTYLE_PACE_CHOICES,
    )

    # Optional second story card
    story_prompt_2 = models.ForeignKey(
        "crush_lu.SparkPrompt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="story_owners_2",
    )
    story_answer_2 = models.CharField(
        max_length=200,
        blank=True,
    )

    # --- Connect match preferences (hard filters) -------------------------
    # Moved off CrushProfile so the catalogue's "who do I want to see" lives
    # next to the rest of the Connect-shared data. The eligible-pool service
    # reads these (not the profile's) for gender/age filtering. Non-null age
    # defaults keep the pool query shape unchanged for migrated members.
    preferred_genders = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Genders this member wants to see in their Drop (empty = open to all)"),
    )
    preferred_age_min = models.PositiveSmallIntegerField(default=18)
    preferred_age_max = models.PositiveSmallIntegerField(default=99)

    # --- Languages & interests (soft signals, shown on the card) ----------
    languages = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Languages this member speaks (codes from CONNECT_LANGUAGE_CHOICES)"),
    )
    interests = models.ManyToManyField(
        "crush_lu.ConnectInterest",
        blank=True,
        related_name="interested_members",
        help_text=_("Curated interests & hobbies (cap of 8 enforced in the wizard)"),
    )

    # --- Trait matching (migrated off CrushProfile) -----------------------
    # The "Ideal Crush" personality data now lives here, not on CrushProfile,
    # so members who never opt into Connect are never asked to complete it.
    # Trait-based scoring (crush_lu.matching) reads these; identity fields
    # (date_of_birth, gender, event_languages) stay on CrushProfile.
    FIRST_STEP_CHOICES = [
        ("i_initiate", _("I prefer to make the first step")),
        ("they_initiate", _("I prefer the other person to make the first step")),
        ("no_preference", _("No preference")),
    ]
    qualities = models.ManyToManyField(
        "crush_lu.Trait",
        blank=True,
        related_name="connect_profiles_as_quality",
        limit_choices_to={"trait_type": "quality"},
        help_text=_("This member's top 5 qualities (max 5)"),
    )
    defects = models.ManyToManyField(
        "crush_lu.Trait",
        blank=True,
        related_name="connect_profiles_as_defect",
        limit_choices_to={"trait_type": "defect"},
        help_text=_("This member's top 5 defects (max 5)"),
    )
    sought_qualities = models.ManyToManyField(
        "crush_lu.Trait",
        blank=True,
        related_name="connect_profiles_seeking",
        limit_choices_to={"trait_type": "quality"},
        help_text=_("Top 5 qualities this member seeks in a partner (max 5)"),
    )
    astro_enabled = models.BooleanField(
        default=True,
        help_text=_("Include zodiac compatibility in this member's match score"),
    )
    first_step_preference = models.CharField(
        max_length=20,
        choices=FIRST_STEP_CHOICES,
        blank=True,
        default="",
        help_text=_("Who should make the first step?"),
    )

    # --- Life situation ---------------------------------------------------
    height_cm = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(120), MaxValueValidator(230)],
        help_text=_("Height in centimetres (optional)"),
    )
    WORK_FIELD_CHOICES = [
        ("finance", _("Finance")),
        ("eu_public", _("EU institutions & public sector")),
        ("it", _("IT & tech")),
        ("health", _("Healthcare")),
        ("education", _("Education")),
        ("legal", _("Legal")),
        ("construction", _("Construction & trades")),
        ("hospitality", _("Hospitality")),
        ("logistics", _("Logistics & transport")),
        ("creative", _("Creative & media")),
        ("entrepreneur", _("Entrepreneur / self-employed")),
        ("student", _("Student")),
        ("other", _("Other")),
        ("prefer_not_say", _("Prefer not to say")),
    ]
    work_field = models.CharField(
        max_length=20,
        blank=True,
        choices=WORK_FIELD_CHOICES,
    )
    EDUCATION_LEVEL_CHOICES = [
        ("high_school", _("High school")),
        ("vocational", _("Vocational training")),
        ("bachelor", _("Bachelor's degree")),
        ("master", _("Master's degree")),
        ("doctorate", _("Doctorate")),
        ("prefer_not_say", _("Prefer not to say")),
    ]
    education_level = models.CharField(
        max_length=20,
        blank=True,
        choices=EDUCATION_LEVEL_CHOICES,
    )
    SMOKING_CHOICES = [
        ("no", _("Non-smoker")),
        ("occasionally", _("Occasionally")),
        ("yes", _("Smoker")),
        ("prefer_not_say", _("Prefer not to say")),
    ]
    smoking = models.CharField(
        max_length=20,
        blank=True,
        choices=SMOKING_CHOICES,
    )
    DRINKING_CHOICES = [
        ("no", _("Doesn't drink")),
        ("socially", _("Socially")),
        ("regularly", _("Regularly")),
        ("prefer_not_say", _("Prefer not to say")),
    ]
    drinking = models.CharField(
        max_length=20,
        blank=True,
        choices=DRINKING_CHOICES,
    )

    # --- Family & future (sensitive — every field has prefer_not_say) -----
    HAS_CHILDREN_CHOICES = [
        ("no", _("No children")),
        ("yes", _("Has children")),
        ("prefer_not_say", _("Prefer not to say")),
    ]
    has_children = models.CharField(
        max_length=20,
        blank=True,
        choices=HAS_CHILDREN_CHOICES,
    )
    WANTS_CHILDREN_CHOICES = [
        ("yes", _("Wants children")),
        ("open", _("Open to it")),
        ("no", _("Doesn't want children")),
        ("prefer_not_say", _("Prefer not to say")),
    ]
    wants_children = models.CharField(
        max_length=20,
        blank=True,
        choices=WANTS_CHILDREN_CHOICES,
    )
    RELATIONSHIP_TIMELINE_CHOICES = [
        ("ready_now", _("Ready for a relationship now")),
        ("few_months", _("Open in the next few months")),
        ("no_rush", _("No rush, taking it slow")),
        ("prefer_not_say", _("Prefer not to say")),
    ]
    relationship_timeline = models.CharField(
        max_length=20,
        blank=True,
        choices=RELATIONSHIP_TIMELINE_CHOICES,
    )

    # --- Wizard progress pointer ------------------------------------------
    # ``onboarding_step`` doubles as minimal resume state: the highest step the
    # member may land on. Already-onboarded members are parked past the end by
    # the data migration. ``onboarding_started_at`` is stamped once, on the
    # first successful step POST.
    onboarding_step = models.PositiveSmallIntegerField(default=1)
    onboarding_started_at = models.DateTimeField(null=True, blank=True)

    # Consent to the "Read-the-Photo" model: the member's clear photo is shown to
    # the few curated, verified people surfaced their card each day so those people
    # can guess the member's 3 questions. Default False so members who onboarded
    # under the old blurred-until-mutual model are NOT surfaced with a clear photo
    # until they re-consent (the eligible-pool service requires this True).
    photo_share_consent = models.BooleanField(
        default=False,
        help_text=_("Member agreed their clear photo is shown to the people matched to them each day"),
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

    @property
    def active_gate_questions(self):
        """The member's 3 ordered gate questions (with their truth answers)."""
        return self.gate_questions.select_related("question").order_by("position")

    @property
    def has_gate_questions(self) -> bool:
        """Whether the member has picked their 3 gate questions."""
        return self.gate_questions.count() >= 3

    @property
    def languages_display(self):
        """Translated labels for the member's stored language codes."""
        labels = dict(CONNECT_LANGUAGE_CHOICES)
        return [labels.get(code, code) for code in (self.languages or [])]

    @property
    def life_situation_display(self):
        """Human labels for the life-situation answers, skipping blanks and
        ``prefer_not_say`` — for coach views (coaches see everything)."""
        parts = []
        if self.height_cm:
            parts.append(f"{self.height_cm} cm")
        for field in ("work_field", "education_level", "smoking", "drinking"):
            value = getattr(self, field)
            if value and value != "prefer_not_say":
                parts.append(getattr(self, f"get_{field}_display")())
        return parts

    @property
    def family_future_display(self):
        """Human labels for the family/future answers, skipping blanks and
        ``prefer_not_say``."""
        parts = []
        for field in ("has_children", "wants_children", "relationship_timeline"):
            value = getattr(self, field)
            if value and value != "prefer_not_say":
                parts.append(getattr(self, f"get_{field}_display")())
        return parts


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


class ConnectInterest(models.Model):
    """
    A curated interest/hobby a member can attach to their Crush Connect
    catalogue profile (mirrors ``Trait``/``SparkPrompt``).

    Curated rather than free-text so the shared data needs no moderation,
    can't leak identifying details, and translates cleanly. ``label`` is
    translated via modeltranslation; set ``is_active=False`` to retire an
    interest without breaking members who already selected it.
    """

    class Category(models.TextChoices):
        SPORTS = "sports", _("Sports")
        MUSIC = "music", _("Music")
        TRAVEL = "travel", _("Travel")
        FOOD = "food", _("Food & Drink")
        ARTS = "arts", _("Arts & Culture")
        OUTDOORS = "outdoors", _("Outdoors")
        GAMES = "games", _("Games")
        WELLNESS = "wellness", _("Wellness")

    slug = models.SlugField(
        max_length=40,
        unique=True,
        help_text=_("Unique identifier, e.g. 'hiking' or 'live-music'"),
    )
    label = models.CharField(
        max_length=50,
        help_text=_("Display label (translated via modeltranslation)"),
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        db_index=True,
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "sort_order", "label"]
        verbose_name = _("Connect Interest")
        verbose_name_plural = _("Connect Interests")

    def __str__(self):
        return self.label


class CuriositySpark(models.Model):
    """
    A Premium member's expression of interest in someone from their Drop (M5).

    Asymmetric by design: only Drop receivers (Premium) can SEND a Spark, but
    anyone in the candidate catalogue can RECEIVE one — candidates respond
    from their own "Sparks received" page, not from a Drop of their own.

    Privacy: the recipient sees the sender exactly like a Drop card (blurred
    photo, first name, age range, Story) plus the sender's message. Declines
    are silent — the sender is never notified of a decline; acceptance is the
    only event that travels back. The mutual reveal itself ships in M6; until
    then an accepted Spark is handed to the coach (admin queue) to arrange
    the date.
    """

    STATUS_CHOICES = [
        ("pending", _("Pending")),
        ("accepted", _("Accepted")),
        ("declined", _("Declined")),
    ]

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_sparks_sent",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_sparks_received",
    )
    # Audit trail: the Drop that surfaced the recipient to the sender. M5's
    # cardinal rule — you can only Spark someone who actually appeared in one
    # of your Drops — is enforced in the service layer using this snapshot.
    drop = models.ForeignKey(
        ConnectDailyDrop,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sparks",
    )
    message = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("The sender's one-line opener ('What made you curious?')"),
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Curiosity Spark")
        verbose_name_plural = _("Curiosity Sparks")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["sender", "recipient"], name="connect_spark_unique_pair"
            ),
            models.CheckConstraint(
                condition=~models.Q(sender=models.F("recipient")),
                name="connect_spark_no_self",
            ),
        ]

    def __str__(self):
        return f"{self.sender} → {self.recipient} ({self.status})"

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"


class ConnectCoachPick(models.Model):
    """
    A Crush Coach's hand-picked match proposal for one of their Premium
    members (M7 — the coach-curated heart of Crush Connect).

    Flow: coach browses the member's eligible pool (full profiles) and
    proposes ONE candidate with a personal note. The pick REPLACES the
    algorithmic Drop as the hero card on the member's Today page. The
    member accepts or declines:
      - accept  → lands in the coach's queue; the coach contacts the
                  candidate personally to confirm interest and arrange the
                  date (no automatic Spark/notification to the candidate).
      - decline → coach is notified and can propose someone else.
    """

    STATUS_CHOICES = [
        ("proposed", _("Proposed")),
        ("accepted", _("Accepted by member")),
        ("declined", _("Declined by member")),
        ("withdrawn", _("Withdrawn by coach")),
    ]

    coach = models.ForeignKey(
        "crush_lu.CrushCoach",
        on_delete=models.CASCADE,
        related_name="connect_picks",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_coach_picks",
    )
    candidate = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_picks_as_candidate",
    )
    note = models.CharField(
        max_length=300,
        blank=True,
        help_text=_("Coach's 'why I picked them' — shown to the member"),
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="proposed", db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Connect Coach Pick")
        verbose_name_plural = _("Connect Coach Picks")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["member", "candidate"], name="connect_pick_unique_pair"
            ),
            models.CheckConstraint(
                condition=~models.Q(member=models.F("candidate")),
                name="connect_pick_no_self",
            ),
        ]

    def __str__(self):
        return f"{self.coach} → {self.member}: {self.candidate} ({self.status})"
