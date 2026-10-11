from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin, TranslationTabularInline

from azureproject.admin_translation_mixin import AutoTranslateMixin

from crush_lu.models.quiz import (
    QuizEvent,
    QuizQuestion,
    QuizRound,
    QuizRotationSchedule,
    QuizTable,
    QuizTableMembership,
)
from crush_lu.quiz_packs import PACKS, pack_names


class QuizEventInline(admin.StackedInline):
    """Inline for creating/editing a QuizEvent from the MeetupEvent admin page."""

    model = QuizEvent
    extra = 0
    max_num = 1
    fields = (
        "status",
        "created_by",
        "current_round",
        "current_question_index",
        "num_tables",
    )
    raw_id_fields = ("created_by", "current_round")


class QuizRoundInline(TranslationTabularInline):
    model = QuizRound
    extra = 1
    fields = ("title", "sort_order", "time_per_question", "is_bonus")


class QuizQuestionInline(TranslationTabularInline):
    model = QuizQuestion
    extra = 1
    fields = (
        "text",
        "question_type",
        "choices",
        "correct_answer",
        "media_kind",
        "media_url",
        "sort_order",
        "points",
    )


class QuizTableMembershipInline(admin.TabularInline):
    model = QuizTableMembership
    extra = 1
    raw_id_fields = ("user",)


class QuizRotationScheduleInline(admin.TabularInline):
    model = QuizRotationSchedule
    extra = 0
    fields = ("round_number", "table", "user", "role", "rotation_group")
    readonly_fields = ("round_number", "table", "user", "role", "rotation_group")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def generate_quiz_night_tables(modeladmin, request, queryset):
    """Admin action: generate table rotation schedule for quiz night events."""
    from django.utils import timezone

    from crush_lu.models.events import EventRegistration
    from crush_lu.services.quiz_rotation import (
        generate_rotation_schedule,
        split_participants_by_gender,
    )

    for quiz in queryset:
        event = quiz.event

        # Only people who actually checked in via QR are rotated. No-shows
        # (still sitting in "confirmed") must not leak into the schedule.
        registrations = (
            EventRegistration.objects.filter(event=event, status="attended")
            .select_related("user__crushprofile")
            .order_by("registered_at")
        )

        if registrations.count() < 4:
            messages.error(
                request,
                f"'{event}': Need at least 4 registrations, "
                f"got {registrations.count()}.",
            )
            continue

        men, women = split_participants_by_gender(registrations)

        try:
            num_rounds = quiz.rounds.count() or 3
            result = generate_rotation_schedule(
                men, women, num_rounds, num_tables=quiz.num_tables
            )
        except Exception as e:
            messages.error(
                request, _("'%(event)s': %(error)s") % {"event": event, "error": e}
            )
            continue

        schedule = result["schedule"]
        actual_num_tables = result["num_tables"]

        # Display any warnings from the algorithm
        for warning in result["warnings"]:
            messages.warning(
                request,
                _("'%(event)s': %(warning)s") % {"event": event, "warning": warning},
            )

        # Clear existing rotation data and memberships (but preserve tables
        # to avoid cascade-deleting TableRoundScore records)
        QuizRotationSchedule.objects.filter(quiz=quiz).delete()
        QuizTableMembership.objects.filter(table__quiz=quiz).delete()

        # Reuse or create tables (don't delete -- TableRoundScore has FK)
        existing_tables = {
            t.table_number: t for t in QuizTable.objects.filter(quiz=quiz)
        }
        tables = {}
        for t in range(1, actual_num_tables + 1):
            if t in existing_tables:
                tables[t] = existing_tables[t]
            else:
                tables[t] = QuizTable.objects.create(quiz=quiz, table_number=t)

        # Remove excess tables (only if no scores attached)
        for t_num, t_obj in existing_tables.items():
            if t_num > actual_num_tables:
                if not t_obj.round_scores.exists():
                    t_obj.delete()

        # Bulk-create rotation schedule
        rotation_entries = []
        round_0_members = set()
        for entry in schedule:
            table = tables[entry["table_number"]]
            rotation_entries.append(
                QuizRotationSchedule(
                    quiz=quiz,
                    round_number=entry["round_number"],
                    table=table,
                    user=entry["user"],
                    role=entry["role"],
                    rotation_group=entry["rotation_group"],
                )
            )
            # Track round 0 members for QuizTableMembership (backward compat)
            if entry["round_number"] == 0:
                round_0_members.add((entry["table_number"], entry["user"]))

        QuizRotationSchedule.objects.bulk_create(rotation_entries)

        # Create QuizTableMembership for round 0 (backward compat with consumer)
        memberships = []
        for table_num, user in round_0_members:
            memberships.append(QuizTableMembership(table=tables[table_num], user=user))
        QuizTableMembership.objects.bulk_create(memberships)

        # Update generation timestamp
        quiz.tables_generated_at = timezone.now()
        quiz.save(update_fields=["tables_generated_at"])

        messages.success(
            request,
            f"'{event}': Created {actual_num_tables} tables with "
            f"{len(men)} anchors + {len(women)} rotators, "
            f"{num_rounds} rounds.",
        )


generate_quiz_night_tables.short_description = _("Generate Quiz Night table rotation")


def mark_unattended_as_no_show(modeladmin, request, queryset):
    """Admin action: flip still-'confirmed' registrations to 'no_show' for
    the selected quiz events. Useful when the host forgot to scan someone
    out and wants the attendance record to reflect reality.

    The work (and the wallet refreshes it owes) lives in
    ``services.no_shows``, shared with the MeetupEvent action that reaches
    every other event type."""
    from crush_lu.services.no_shows import (
        curated_group_refusal,
        mark_unattended_no_show,
    )

    # No time guard here (the quiz host may tidy up a live quiz), but a quiz
    # can sit on a curated speed-dating event, and the bulk path must never
    # bypass a provisional or locked group there.
    events = []
    for quiz in queryset.select_related("event"):
        reason = curated_group_refusal(quiz.event)
        if reason is None:
            events.append(quiz.event)
        else:
            messages.warning(request, f"'{quiz.event}' skipped: {reason}.")

    results = mark_unattended_no_show(
        events, context="Admin mark_unattended_as_no_show"
    )
    for event, updated, left in results:
        if updated or not left:
            messages.success(
                request,
                f"'{event}': marked {updated} registration(s) as No Show.",
            )
        if left:
            messages.warning(
                request,
                f"'{event}': {left} registration(s) not changed yet — the "
                f"Google Wallet refresh limit was reached. Run the action "
                f"again to record them.",
            )


mark_unattended_as_no_show.short_description = _(
    "Mark unchecked-in registrations as No Show"
)


def _make_populate_action(pack_name):
    """Build an admin action that seeds selected QuizEvents from *pack_name*.

    One action per registered pack, so a coach picks the pack from the same
    dropdown they already use rather than needing shell access.
    """

    def action(modeladmin, request, queryset):
        from crush_lu.management.commands.generate_crush_quiz import populate_quiz

        for quiz in queryset:
            existing = quiz.rounds.count()
            if existing:
                messages.warning(
                    request,
                    f"'{quiz.event}': Already has {existing} rounds. "
                    f"Skipped. Delete existing rounds first or use "
                    f"the management command with --clear.",
                )
                continue

            try:
                result = populate_quiz(quiz, pack=pack_name)
            except ValueError as exc:
                messages.error(request, f"'{quiz.event}': {exc}")
                continue

            messages.success(
                request,
                f"'{quiz.event}': Created {result.rounds_created} rounds and "
                f"{result.questions_created} questions from pack "
                f"'{pack_name}'.",
            )
            if result.pending_uploads:
                # Seeded without media on purpose — images have no embed path.
                messages.warning(
                    request,
                    f"'{quiz.event}': {len(result.pending_uploads)} image "
                    f"question(s) still need a file uploaded via the quiz "
                    f"authoring UI: "
                    + ", ".join(p.filename for p in result.pending_uploads),
                )

    rounds = PACKS[pack_name]
    questions = sum(len(r.get("questions") or []) for r in rounds)
    action.__name__ = f"populate_quiz_pack_{pack_name.replace('-', '_')}"
    action.short_description = _(
        "Populate quiz questions — %(pack)s pack (%(rounds)d rounds, "
        "%(questions)d questions)"
    ) % {"pack": pack_name, "rounds": len(rounds), "questions": questions}
    return action


POPULATE_PACK_ACTIONS = [_make_populate_action(name) for name in pack_names()]


class QuizEventAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "status",
        "num_tables",
        "current_round",
        "created_by",
        "tables_generated_at",
        "created_at",
    )
    list_select_related = ["event", "current_round", "created_by"]
    list_filter = ("status",)
    inlines = [QuizRoundInline, QuizRotationScheduleInline]
    raw_id_fields = ("event", "created_by", "current_round")
    readonly_fields = (
        "created_at",
        "updated_at",
        "tables_generated_at",
        "readiness_check_display",
    )
    actions = [
        generate_quiz_night_tables,
        mark_unattended_as_no_show,
        *POPULATE_PACK_ACTIONS,
    ]

    def readiness_check_display(self, obj):
        from django.utils.html import format_html, format_html_join

        checks = obj.readiness_check()
        rows = format_html_join(
            "\n",
            '<tr><td style="padding:4px 8px">{}</td>'
            '<td style="padding:4px 8px;font-weight:600">{}</td>'
            '<td style="padding:4px 8px;color:#666">{}</td></tr>',
            (
                (
                    format_html(
                        '<span style="color:{}">{}</span>',
                        "#16a34a" if c["ok"] else "#dc2626",
                        "\u2705" if c["ok"] else "\u274c",
                    ),
                    c["label"],
                    c["detail"],
                )
                for c in checks
            ),
        )
        return format_html('<table style="border-collapse:collapse">{}</table>', rows)

    readiness_check_display.short_description = _("Readiness Check")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.num_tables:
            obj.ensure_tables()


class QuizRoundAdmin(AutoTranslateMixin, TranslationAdmin):
    list_display = ("title", "quiz", "sort_order", "time_per_question", "is_bonus")
    list_select_related = ["quiz__event"]
    list_filter = ("quiz__event", "is_bonus")
    inlines = [QuizQuestionInline]


class QuizQuestionAdmin(AutoTranslateMixin, TranslationAdmin):
    list_display = (
        "text",
        "round",
        "question_type",
        "media_kind",
        "points",
        "sort_order",
    )
    list_select_related = ["round__quiz__event"]
    list_filter = ("question_type", "media_kind", "round__quiz__event")


class QuizTableAdmin(admin.ModelAdmin):
    list_display = ("table_number", "quiz", "member_count")
    list_select_related = ["quiz__event"]
    list_filter = ("quiz__event",)
    inlines = [QuizTableMembershipInline]

    def member_count(self, obj):
        return obj.members.count()

    member_count.short_description = _("Members")


class TableRoundScoreAdmin(admin.ModelAdmin):
    list_display = ("table", "question", "is_correct", "scored_at")
    list_select_related = ["table__quiz__event", "question"]
    list_filter = ("is_correct", "quiz__event")
    raw_id_fields = ("quiz", "table", "question")
    readonly_fields = ("scored_at",)


class IndividualScoreAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "quiz",
        "question",
        "is_correct",
        "points_earned",
        "answered_at",
    )
    list_select_related = ["user", "quiz__event", "question"]
    list_filter = ("is_correct", "quiz__event")
    raw_id_fields = ("user", "quiz", "question")
    readonly_fields = ("answered_at",)
