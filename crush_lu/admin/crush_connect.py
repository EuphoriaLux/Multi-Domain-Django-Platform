from django.contrib import admin, messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext

from crush_lu.models import MemberGateQuestion


class MemberGateQuestionInline(admin.TabularInline):
    """A member's 3 gate questions (with their own truth answer) on the
    membership page."""

    model = MemberGateQuestion
    extra = 0
    ordering = ("position",)
    raw_id_fields = ("question", "picked_week")
    fields = ("position", "question", "owner_answer", "picked_week", "created_at")
    readonly_fields = ("created_at",)


class CrushConnectMembershipAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "is_onboarded_display",
        "onboarding_step",
        "is_paused_display",
        "excluded_by_coach",
        "onboarded_at",
        "paused_at",
        "excluded_at",
        "updated_at",
    ]
    list_select_related = ["user"]
    list_filter = ["excluded_by_coach", "paused_at", "onboarded_at"]
    list_editable = ["excluded_by_coach"]
    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
        "user__username",
        "exclusion_reason",
    ]
    raw_id_fields = ["user", "excluded_by", "story_prompt", "story_prompt_2"]
    filter_horizontal = ["interests"]
    inlines = [MemberGateQuestionInline]
    readonly_fields = [
        "created_at",
        "updated_at",
        "onboarding_started_at",
        "paused_at",
    ]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "user",
                    "onboarded_at",
                    "onboarding_step",
                    "onboarding_started_at",
                    "photo_share_consent",
                )
            },
        ),
        (
            _("Onboarding — Intent"),
            {"fields": ("relationship_goal",)},
        ),
        (
            _("Onboarding — Lifestyle"),
            {"fields": ("lifestyle_energy", "lifestyle_social", "lifestyle_pace")},
        ),
        (
            _("Match preferences (hard filters)"),
            {
                "fields": (
                    "preferred_genders",
                    "preferred_age_min",
                    "preferred_age_max",
                ),
                "description": _(
                    "These are the Connect catalogue's gender/age filters. They are "
                    "separate from the member's main profile 'Interested in' — editing "
                    "the main profile no longer affects Crush Connect cards."
                ),
            },
        ),
        (
            _("Languages & interests"),
            {"fields": ("languages", "interests")},
        ),
        (
            _("Life situation"),
            {
                "fields": (
                    "height_cm",
                    "work_field",
                    "education_level",
                    "smoking",
                    "drinking",
                )
            },
        ),
        (
            _("Family & future"),
            {"fields": ("has_children", "wants_children", "relationship_timeline")},
        ),
        (
            _("Story"),
            {
                "fields": (
                    "story_prompt",
                    "story_answer",
                    "story_prompt_2",
                    "story_answer_2",
                )
            },
        ),
        (
            _("Member pause"),
            {
                "fields": ("paused_at",),
                "description": _(
                    "A member-controlled pause hides them from new Crush Connect discovery while preserving onboarding and existing chats."
                ),
            },
        ),
        (
            _("Coach exclusion (panic button)"),
            {
                "fields": (
                    "excluded_by_coach",
                    "excluded_by",
                    "excluded_at",
                    "exclusion_reason",
                ),
                "description": _(
                    "Flipping ``excluded_by_coach`` removes this user from every other user's "
                    "Crush Connect pool immediately. Add a reason for the audit trail."
                ),
            },
        ),
        (_("Audit"), {"fields": ("created_at", "updated_at")}),
    )

    def is_onboarded_display(self, obj):
        return obj.is_onboarded

    is_onboarded_display.boolean = True
    is_onboarded_display.short_description = _("Onboarded")

    def is_paused_display(self, obj):
        return obj.is_paused

    is_paused_display.boolean = True
    is_paused_display.short_description = _("Paused")

    def save_model(self, request, obj, form, change):
        # Stamp the exclusion audit fields automatically when the flag is flipped.
        if change and "excluded_by_coach" in form.changed_data:
            if obj.excluded_by_coach:
                obj.excluded_at = obj.excluded_at or timezone.now()
                if not obj.excluded_by and hasattr(request.user, "crushcoach"):
                    obj.excluded_by = request.user.crushcoach
            else:
                obj.excluded_at = None
                obj.excluded_by = None
                obj.exclusion_reason = ""
        super().save_model(request, obj, form, change)


class CrushConnectWaitlistAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "joined_at",
        "notification_preference",
        "is_eligible",
        "beta_invite_wave",
        "beta_invited_at",
        "selected_as_tester",
        "selected_at",
        "payment_confirmed",
        "payment_date",
    ]
    list_select_related = ["user"]
    list_filter = [
        "selected_as_tester",
        "payment_confirmed",
        "joined_at",
        "notification_preference",
        "beta_invite_wave",
    ]
    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
        "user__username",
    ]
    raw_id_fields = ["user", "confirmed_by"]
    readonly_fields = [
        "joined_at",
        "selected_at",
        "payment_date",
        "confirmed_by",
        # Written only by send_connect_beta_invites, and only after an email
        # actually left. Editable here, a cleared timestamp would silently
        # re-mail the member on the next run.
        "beta_invited_at",
        "beta_invite_wave",
    ]
    actions = ["select_as_tester", "confirm_payment"]

    def is_eligible(self, obj):
        return obj.is_eligible

    is_eligible.boolean = True
    is_eligible.short_description = _("Eligible")

    def save_model(self, request, obj, form, change):
        # Stamp the audit/date fields when staff flips the booleans directly in
        # the change form (the bulk actions stamp them too). Keeps the teaser's
        # "beta status" honest — no "selected"/"paid" without a timestamp.
        if "selected_as_tester" in form.changed_data:
            obj.selected_at = timezone.now() if obj.selected_as_tester else None
        if "payment_confirmed" in form.changed_data:
            if obj.payment_confirmed:
                obj.payment_date = obj.payment_date or timezone.now()
                obj.confirmed_by = obj.confirmed_by or request.user
            else:
                obj.payment_date = None
                obj.confirmed_by = None
        super().save_model(request, obj, form, change)

    @admin.action(description=_("Select as beta tester (4 weeks / 4 matches)"))
    def select_as_tester(self, request, queryset):
        selected = 0
        for entry in queryset:
            if entry.selected_as_tester:
                continue
            entry.selected_as_tester = True
            entry.selected_at = timezone.now()
            entry.save(update_fields=["selected_as_tester", "selected_at"])
            selected += 1
        self.message_user(
            request,
            ngettext(
                "%(n)d member selected as a beta tester.",
                "%(n)d members selected as beta testers.",
                selected,
            )
            % {"n": selected},
            level=messages.SUCCESS,
        )

    # No price in the label: the amount is SUMUP_PREMIUM_MONTHLY_FEE, set per
    # environment. Naming it here made the admin state €10 while the checkout
    # charged whatever the env var said.
    @admin.action(description=_("Confirm monthly payment"))
    def confirm_payment(self, request, queryset):
        confirmed = 0
        for entry in queryset:
            if entry.payment_confirmed:
                continue
            entry.payment_confirmed = True
            entry.payment_date = timezone.now()
            entry.confirmed_by = request.user
            entry.save(
                update_fields=["payment_confirmed", "payment_date", "confirmed_by"]
            )
            confirmed += 1
        self.message_user(
            request,
            ngettext(
                "%(n)d payment confirmed.",
                "%(n)d payments confirmed.",
                confirmed,
            )
            % {"n": confirmed},
            level=messages.SUCCESS,
        )


class SparkPromptAdmin(admin.ModelAdmin):
    list_display = ["text", "is_active", "weight", "updated_at"]
    list_filter = ["is_active"]
    list_editable = ["is_active", "weight"]
    search_fields = ["text", "text_en", "text_de", "text_fr"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("is_active", "weight")}),
        (
            _("Prompt text (translations)"),
            {
                "fields": ("text", "text_en", "text_de", "text_fr"),
                "description": _(
                    "Edit each language version. ``text`` is the fallback for users in unmatched locales."
                ),
            },
        ),
        (_("Audit"), {"fields": ("created_at", "updated_at")}),
    )


class InterestAdmin(admin.ModelAdmin):
    list_display = ["label", "slug", "category", "is_active", "sort_order"]
    list_filter = ["category", "is_active"]
    list_editable = ["category", "is_active", "sort_order"]
    search_fields = ["slug", "label", "label_en", "label_de", "label_fr"]
    prepopulated_fields = {"slug": ("label",)}
    ordering = ["category", "sort_order", "label"]
    fieldsets = (
        (None, {"fields": ("slug", "category", "is_active", "sort_order")}),
        (
            _("Label (translations)"),
            {
                "fields": ("label", "label_en", "label_de", "label_fr"),
                "description": _(
                    "Edit each language version. ``label`` is the fallback for users in unmatched locales."
                ),
            },
        ),
    )


class ConnectCoachPickAdmin(admin.ModelAdmin):
    """Oversight for coach-curated match proposals (M7)."""

    list_display = (
        "coach",
        "member",
        "candidate",
        "status",
        "created_at",
        "responded_at",
    )
    list_select_related = ["coach__user", "member", "candidate"]
    list_filter = ("status", "created_at")
    search_fields = (
        "member__username",
        "member__first_name",
        "candidate__username",
        "candidate__first_name",
        "coach__user__username",
    )
    raw_id_fields = ("coach", "member", "candidate")
    readonly_fields = ("created_at", "responded_at")
    date_hierarchy = "created_at"


# ---------------------------------------------------------------------------
# Read-the-Photo question-gated matching (M8/M9)
# ---------------------------------------------------------------------------
class ConnectQuestionAdmin(admin.ModelAdmin):
    """The catalogue — mirrors SparkPromptAdmin. Edit weight/tier/is_active in
    the list for quick A/B tuning; spicy questions ship inactive."""

    list_display = ["text", "category", "tier", "is_active", "weight", "updated_at"]
    list_filter = ["category", "tier", "is_active"]
    list_editable = ["category", "tier", "is_active", "weight"]
    search_fields = ["slug", "text", "text_en", "text_de", "text_fr"]
    prepopulated_fields = {"slug": ("text",)}
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("slug", "category", "tier", "is_active", "weight")}),
        (
            _("Question text (translations)"),
            {
                "fields": ("text", "text_en", "text_de", "text_fr"),
                "description": _(
                    "Owner-POV yes/no question. ``text`` is the fallback for users in unmatched locales."
                ),
            },
        ),
        (_("Audit"), {"fields": ("created_at", "updated_at")}),
    )


class ConnectQuestionWeekAdmin(admin.ModelAdmin):
    """Weekly rotation visibility. Use the action to build the current week's
    set on demand (idempotent)."""

    list_display = ["week_start", "iso_year", "iso_week", "question_count"]
    date_hierarchy = "week_start"
    filter_horizontal = ["questions"]
    readonly_fields = ["created_at"]
    actions = ["roll_current_week"]

    @admin.display(description=_("Questions"))
    def question_count(self, obj):
        return obj.questions.count()

    @admin.action(description=_("Ensure THIS week's question set exists"))
    def roll_current_week(self, request, queryset):
        from crush_lu.services.crush_connect import rotate_question_week

        week = rotate_question_week()
        self.message_user(
            request,
            _("This week's set is ready: %(w)s") % {"w": week},
            messages.SUCCESS,
        )


# ---------------------------------------------------------------------------
# Crush Connect 7-Day Deliberate Cycle Admins
# ---------------------------------------------------------------------------
class ConnectCycleCardInline(admin.TabularInline):
    from crush_lu.models.crush_connect_cycle import ConnectCycleCard

    model = ConnectCycleCard
    extra = 0
    raw_id_fields = ("target_user",)
    readonly_fields = (
        "day_number",
        "card_index",
        "target_user",
        "generated_date",
        "is_completed",
        "completed_at",
        "is_expired",
    )


class ConnectWeekSessionAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "current_day_number",
        "status",
        "is_review_open",
        "started_at",
        "review_expires_at",
        "completed_at",
    ]
    list_select_related = ["user", "compatibility_highlight_user"]
    list_filter = ["status", "is_review_open", "current_day_number"]
    search_fields = [
        "user__username",
        "user__email",
        "user__first_name",
        "user__last_name",
    ]
    raw_id_fields = ["user", "compatibility_highlight_user"]
    readonly_fields = [
        "started_at",
        "review_opened_at",
        "review_expires_at",
        "completed_at",
    ]
    inlines = [ConnectCycleCardInline]
    date_hierarchy = "started_at"


class ConnectCycleCardAdmin(admin.ModelAdmin):
    list_display = [
        "session",
        "day_number",
        "card_index",
        "target_user",
        "generated_date",
        "is_completed",
        "completed_at",
        "is_expired",
    ]
    list_select_related = ["session__user", "target_user"]
    list_filter = ["day_number", "is_completed", "is_expired", "generated_date"]
    search_fields = [
        "session__user__username",
        "target_user__username",
        "target_user__email",
    ]
    raw_id_fields = ["session", "target_user"]
    readonly_fields = ["completed_at"]
    date_hierarchy = "generated_date"


class ConnectWeeklyRequestAdmin(admin.ModelAdmin):
    list_display = [
        "requester",
        "recipient",
        "status",
        "sent_at",
        "expires_at",
        "responded_at",
    ]
    list_select_related = ["requester", "recipient", "session"]
    list_filter = ["status", "sent_at"]
    search_fields = ["requester__username", "recipient__username", "message"]
    raw_id_fields = ["session", "requester", "recipient", "target_card"]
    readonly_fields = ["sent_at", "expires_at", "responded_at"]
    date_hierarchy = "sent_at"


class ConnectChatMessageInline(admin.TabularInline):
    from crush_lu.models.crush_connect_cycle import ConnectChatMessage

    model = ConnectChatMessage
    extra = 0
    raw_id_fields = ("sender",)
    readonly_fields = ("sender", "message", "sent_at", "read_at")


class ConnectTemporaryChatAdmin(admin.ModelAdmin):
    list_display = [
        "participant_1",
        "participant_2",
        "status",
        "created_at",
        "expires_at",
        "reminder_sent",
        "closed_at",
        "close_reason",
    ]
    list_select_related = ["participant_1", "participant_2", "request"]
    list_filter = ["status", "close_reason", "reminder_sent", "created_at"]
    search_fields = ["participant_1__username", "participant_2__username"]
    raw_id_fields = ["request", "participant_1", "participant_2"]
    readonly_fields = ["created_at", "expires_at", "closed_at"]
    inlines = [ConnectChatMessageInline]
    date_hierarchy = "created_at"


class ConnectChatMessageAdmin(admin.ModelAdmin):
    list_display = ["chat", "sender", "message_preview", "sent_at", "read_at"]
    list_select_related = ["chat", "sender"]
    list_filter = ["sent_at", "read_at"]
    search_fields = ["sender__username", "message"]
    raw_id_fields = ["chat", "sender"]
    readonly_fields = ["sent_at"]
    date_hierarchy = "sent_at"

    @admin.display(description=_("Message"))
    def message_preview(self, obj):
        return (obj.message[:60] + "...") if len(obj.message) > 60 else obj.message


class ConnectCoffeeDateAdmin(admin.ModelAdmin):
    list_display = [
        "chat",
        "proposer",
        "venue_display",
        "proposed_date",
        "proposed_time_slot",
        "status",
        "meeting_confirmed_at",
    ]
    list_select_related = ["chat", "proposer", "venue_location"]
    list_filter = ["status", "proposed_date"]
    search_fields = ["proposer__username", "venue_location__name", "custom_venue_name"]
    raw_id_fields = ["chat", "proposer", "venue_location"]
    readonly_fields = [
        "participant_1_confirmed_at",
        "participant_2_confirmed_at",
        "meeting_confirmed_at",
    ]
    date_hierarchy = "proposed_date"

    @admin.display(description=_("Venue"))
    def venue_display(self, obj):
        return (
            obj.venue_location.name
            if obj.venue_location
            else (obj.custom_venue_name or _("Custom"))
        )


class ConnectPairExclusionAdmin(admin.ModelAdmin):
    list_display = ["user_a", "user_b", "reason", "created_at"]
    list_select_related = ["user_a", "user_b"]
    list_filter = ["reason", "created_at"]
    search_fields = ["user_a__username", "user_b__username"]
    raw_id_fields = ["user_a", "user_b"]
    readonly_fields = ["created_at"]
    date_hierarchy = "created_at"


class ConnectReportAdmin(admin.ModelAdmin):
    list_display = [
        "reporter",
        "reported_user",
        "reason",
        "status",
        "created_at",
        "reviewed_by",
        "reviewed_at",
    ]
    list_select_related = ["reporter", "reported_user", "reviewed_by__user"]
    list_filter = ["status", "reason", "created_at"]
    search_fields = [
        "reporter__username",
        "reported_user__username",
        "details",
        "admin_notes",
    ]
    raw_id_fields = ["reporter", "reported_user", "reviewed_by"]
    readonly_fields = ["created_at"]
    date_hierarchy = "created_at"


class ConnectCycleFeedbackAdmin(admin.ModelAdmin):
    """Read the beta's post-cycle verdicts (Task 13.4).

    Fully read-only: every field is written by the member through
    ``connect_week_feedback``, and an editable survey answer is no longer
    survey data. ``dismissed`` rows are kept in the list on purpose — the
    dismissal rate is itself a signal about the prompt, and hiding them would
    make the answered rows look like the whole population.
    """

    list_display = [
        "user",
        "session",
        "sentiment",
        "match_quality",
        "dismissed",
        "has_comment",
        "created_at",
    ]
    list_select_related = ["user", "session"]
    list_filter = ["sentiment", "match_quality", "dismissed", "created_at"]
    search_fields = ["user__username", "user__email", "comment"]
    raw_id_fields = ["session", "user"]
    readonly_fields = [
        "session",
        "user",
        "sentiment",
        "match_quality",
        "comment",
        "dismissed",
        "created_at",
    ]
    date_hierarchy = "created_at"

    @admin.display(boolean=True, description=_("Comment"))
    def has_comment(self, obj):
        return bool(obj.comment)

    def has_add_permission(self, request):
        return False
