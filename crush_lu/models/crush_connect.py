"""
Crush Connect models.

- ``CrushConnectWaitlist``: pre-launch waitlist for users interested in Crush Connect.
- ``SparkPrompt``: legacy-named, translatable story prompts used during Connect
  onboarding. The name is retained to preserve existing story data.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

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
    # Beta tester selection — staff hand-pick the testers. ``payment_confirmed``
    # tracks an out-of-band payment (manual flag, no processor — mirrors
    # PremiumMembership.payment_confirmed / EventRegistration). No label here
    # names a price: the amount is ``SUMUP_PREMIUM_MONTHLY_FEE``, set per
    # environment, and restating it made the admin advertise a figure the
    # checkout did not necessarily charge.
    #
    # Scope note: ``selected_as_tester`` grants beta access in three places:
    #   - connect_phase.cycle_access_open() — opens Connect Week;
    #   - views_premium — lets the member past the PREMIUM_REDIRECTS_TO_BETA
    #     funnel and buy Premium, which is how they obtain the active
    #     PremiumMembership for the human coach-pick layer;
    #   - views_payments._premium_purchase_refused — re-asks at each of the
    #     three moments money can move (opening the checkout, opening the card
    #     widget, granting Premium at completion), because the pending
    #     PremiumMembership minted by views_premium is a capability that
    #     otherwise outlives the permission that created it. Clearing this flag
    #     revokes a purchase already in flight, which is what makes rotating
    #     testers safe.
    # Being on the waitlist grants nothing by itself; joining is self-serve, so
    # only this staff-set flag opens any of those doors. (An earlier note here
    # said these flags were tracking-only and that the gate required a premium
    # coach — both stopped being true when the beta phase and the purchase
    # allowlist landed.)
    selected_as_tester = models.BooleanField(
        default=False,
        help_text=_("Hand-picked beta tester: may enter Connect Week and buy Premium"),
    )
    selected_at = models.DateTimeField(null=True, blank=True)
    payment_confirmed = models.BooleanField(
        default=False,
        help_text=_("Monthly payment confirmed by staff (manual, no processor)"),
    )
    payment_date = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_crush_connect_payments",
        help_text=_("Staff member who confirmed the payment"),
    )

    # Beta launch invite tracking (Task 13.4 / ``send_connect_beta_invites``).
    #
    # These two fields are a SEND LOG, not an entitlement. Nothing reads them
    # to open a door: Wave 1 recipients already reach the Connect Week through
    # ``connect_phase.cycle_access_open`` (event-verified), Wave 2 recipients
    # already reach the Mix through ``candidate_access_open`` + catalogue
    # eligibility, and Wave 3 recipients are told how to become eligible. The
    # invite command deliberately never touches ``selected_as_tester`` — that
    # flag opens selected-tester beta access and the Premium purchase funnel;
    # mailing the whole waitlist must not hand out either.
    #
    # Stored on the row rather than in a cache key because the send has to stay
    # deduped across a Redis eviction (a re-run would otherwise double-mail the
    # whole wave) and because "who did we invite, when, in which wave" is an
    # auditable launch record, not transient state.
    beta_invited_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_("When the Connect beta launch invite was emailed (blank = never)"),
    )
    beta_invite_wave = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_(
            "Which send_connect_beta_invites wave delivered the invite "
            "(1 = Connect Week, 2 = In the Mix, 3 = verification reminder)"
        ),
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

    Crush Connect is opt-in: an identity-verified member is *eligible* to
    onboard, but enters the private catalogue and Connect Week pool only after
    completing Connect-specific onboarding.

    Fields written in later milestones (M4+) will include the user's coach-
    curated Story answer and any other Connect-specific profile data. Storing
    them on this model — not on ``CrushProfile`` — keeps the regular profile
    surface clean for members who never opt into Connect.

    Coach panic-button: ``excluded_by_coach`` removes a member from every
    other user's pool and blocks their Connect surfaces without revoking core
    profile approval. Use ``exclusion_reason`` for the audit trail.

    Member pause: ``paused_at`` is a reversible, self-service snooze. It keeps
    onboarding answers and existing temporary chats intact while removing the
    member from new Connect discovery and interaction flows. It is deliberately
    separate from the coach exclusion so a voluntary break is never represented
    as a moderation action.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crush_connect_membership",
    )
    onboarded_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "Set when the user completes Crush Connect onboarding. Null = waitlisted/not opted-in."
        ),
    )

    # Coach panic button
    excluded_by_coach = models.BooleanField(
        default=False,
        db_index=True,
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
        help_text=_(
            "Why this user was excluded (audit trail; never shown to the user)"
        ),
    )

    paused_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "Set while the member has paused Crush Connect; onboarding and existing chats are preserved"
        ),
    )

    # Connect-specific onboarding content. The "Story" is the short answer that
    # appears on the member's private Connect Week and coach-curation cards.
    story_prompt = models.ForeignKey(
        "crush_lu.SparkPrompt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="story_owners",
        help_text=_("The story prompt this member chose during onboarding"),
    )
    story_answer = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("One-line answer shown on the member's private Connect card"),
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
        help_text=_(
            "Genders this member wants to see in Connect (empty = open to all)"
        ),
    )
    preferred_age_min = models.PositiveSmallIntegerField(default=18)
    preferred_age_max = models.PositiveSmallIntegerField(default=99)

    # --- Languages & interests (soft signals, shown on the card) ----------
    languages = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Languages this member speaks (codes from CONNECT_LANGUAGE_CHOICES)"
        ),
    )
    interests = models.ManyToManyField(
        "crush_lu.Interest",
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
        # "Legal" alone is already translated as the footer's legal-notices
        # heading; the work sector needs its own context (DE "Recht", not
        # "Rechtliches"; FR "Juridique", not "Mentions légales").
        ("legal", pgettext_lazy("work field", "Legal")),
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
        help_text=_(
            "Member agreed their clear photo is shown to the people matched to them each day"
        ),
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
        elif self.is_paused:
            state += " (paused)"
        return f"{self.user} — {state}"

    @property
    def is_onboarded(self) -> bool:
        return self.onboarded_at is not None and not self.excluded_by_coach

    @property
    def is_paused(self) -> bool:
        return self.paused_at is not None

    @property
    def is_participating(self) -> bool:
        """Whether the member is currently available for Connect activity."""
        return self.is_onboarded and not self.is_paused

    def pause(self) -> None:
        """Hide the member from new Connect activity without losing setup."""
        if self.paused_at is None:
            self.paused_at = timezone.now()
            self.save(update_fields=["paused_at", "updated_at"])

    def reactivate(self) -> None:
        """Resume Connect participation with the existing onboarding data."""
        if self.paused_at is not None:
            self.paused_at = None
            self.save(update_fields=["paused_at", "updated_at"])

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


class SparkPrompt(models.Model):
    """
    A curated story prompt used by Crush Connect onboarding.

    Example texts:
        - "What in their profile made you curious?"
        - "What would your perfect first meetup look like?"
        - "What's a small thing that delights you?"

    ``weight`` controls rotation: a prompt with weight=2 is twice as likely to
    be surfaced as one with weight=1. Set ``is_active=False`` to retire it
    without removing existing member story answers.
    """

    text = models.CharField(
        max_length=200,
        help_text=_(
            "Story prompt shown to the member (translated via modeltranslation)"
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text=_("Inactive prompts stop being offered during onboarding"),
    )
    weight = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Rotation weight (higher = more often)"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "-weight", "id"]
        verbose_name = _("Story Prompt")
        verbose_name_plural = _("Story Prompts")

    def __str__(self):
        return self.text


class Interest(models.Model):
    """
    A curated interest/hobby, shared cross-product: Crush Connect catalogue
    profiles and the classic event profile's Event Identity section both attach
    it via M2M (mirrors ``Trait``/``SparkPrompt``).

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
        verbose_name = _("Interest")
        verbose_name_plural = _("Interests")

    def __str__(self):
        return self.label


class ConnectCoachPick(models.Model):
    """
    A Crush Coach's hand-picked match proposal for one of their Premium
    members (M7 — the coach-curated heart of Crush Connect).

    Flow: coach browses the member's eligible pool (full profiles) and
    proposes ONE candidate with a personal note. The member accepts or declines:
      - accept  → lands in the coach's queue; the coach contacts the
                  candidate personally to confirm interest and arrange the
                  date (no automatic notification to the candidate).
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
