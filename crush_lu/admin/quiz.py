import logging

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

logger = logging.getLogger(__name__)


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
    out and wants the attendance record to reflect reality."""
    from django.utils import timezone

    from crush_lu.models import CrushProfile
    from crush_lu.models.events import EventRegistration
    from crush_lu.wallet_pass import get_next_event_registrations

    now = timezone.now()

    # Accumulated across the whole action, then refreshed ONCE: each refresh
    # helper opens its own push budget, so refreshing per quiz would restart
    # the deadline for every selected row.
    voiding_serials = []
    # The Google half of the same accumulation, keyed by pk to DEDUPE. One
    # holder can be registered for two of the selected quizzes, and unlike a
    # repeated Apple serial each duplicate here is a real extra PATCH spent out
    # of a budget whose overflow leaves other passes permanently stale.
    voiding_google_profiles = {}

    for quiz in queryset:
        # Include "pending" seat holders: on a paid quiz a cash-at-the-door
        # registrant who never arrives would otherwise sit as Pending Payment
        # forever, still consuming a seat and never reaching no-show reporting.
        # "attended", "cancelled" and "waitlist" stay excluded.
        affected = EventRegistration.objects.filter(
            event=quiz.event, status__in=["confirmed", "pending"]
        )
        # Collected before the update, while the seats are still valid.
        # no_show is not a seat-holding status, so the rebuilt ticket is
        # `voided` — but .update() emits no signals, so without this the
        # holder keeps a ticket that still looks valid at the door.
        voiding_serials += list(
            affected.exclude(apple_wallet_ticket_serial="").values_list(
                "apple_wallet_ticket_serial", flat=True
            )
        )
        # ...and their MEMBER passes. no_show is outside the candidate set of
        # get_next_event_for_pass, so this quiz stops being the holder's next
        # event and the card would otherwise keep advertising an event they
        # were just marked absent from. A holder who never downloaded the
        # ticket has only this serial.
        voiding_serials += list(
            CrushProfile.objects.filter(user__eventregistration__in=affected)
            .exclude(apple_pass_serial="")
            .values_list("apple_pass_serial", flat=True)
            .distinct()
        )
        # ...and their GOOGLE member objects, which print the same "Next Event"
        # block and have no Google ticket to carry the change instead.
        # Collected before the update, while `affected` still matches.
        #
        # Two ways a holder here needs the PATCH, and the second is the USUAL
        # one, because this action is normally run after the quiz:
        #
        #   * the quiz is still live and is what their card is showing. The
        #     no_show flip takes it out of PASS_NEXT_EVENT_STATUSES, so the
        #     block changes. Crossing the status boundary is necessary but not
        #     sufficient — a holder with an earlier eligible registration is
        #     looking at that instead, and refreshing them writes a
        #     byte-identical object out of a capped, poll-less budget.
        #
        #   * the quiz has already ENDED. get_next_event_for_pass stopped
        #     selecting it the moment it finished, so it is nobody's displayed
        #     event and the test above answers "refresh nobody" — but the Google
        #     object is SERVER-SIDE state that only changes when we PATCH it,
        #     and the last PATCH went out while the quiz was still upcoming. It
        #     is therefore still advertising a finished quiz, and nothing else
        #     recomputes it: there is no "event ended" trigger anywhere in the
        #     estate. Optimising on the current logical selector cannot see
        #     that, because the two diverge the instant an event ends.
        #
        # So an ended quiz takes the conservative branch. That is the same rule
        # the event-change fan-out follows: over-refreshing spends budget,
        # under-refreshing strands a wrong card with nothing left to correct it.
        quiz_ended = quiz.event.end_time < now
        quiz_candidates = list(
            CrushProfile.objects.filter(
                user__eventregistration__in=affected
            ).exclude(google_wallet_object_id="")
        )
        displayed = (
            {}
            if quiz_ended
            else get_next_event_registrations(
                [profile.user_id for profile in quiz_candidates], now=now
            )
        )
        for profile in quiz_candidates:
            showing = displayed.get(profile.user_id)
            if quiz_ended or (
                showing is not None and showing.event_id == quiz.event_id
            ):
                voiding_google_profiles[profile.pk] = profile
        updated = affected.update(status="no_show")
        messages.success(
            request,
            f"'{quiz.event}': marked {updated} registration(s) as No Show.",
        )

    if voiding_serials:
        try:
            from crush_lu.wallet.passkit_service import refresh_ticket_serials

            refresh_ticket_serials(
                voiding_serials, context="Admin mark_unattended_as_no_show"
            )
        except Exception:
            logger.exception(
                "Failed scheduling Apple ticket refresh for %s no-show "
                "registration(s)",
                len(voiding_serials),
            )

    if voiding_google_profiles:
        try:
            from crush_lu.wallet.google_api import refresh_google_wallet_objects

            refresh_google_wallet_objects(
                list(voiding_google_profiles.values()),
                context="Admin mark_unattended_as_no_show",
            )
        except Exception:
            logger.exception(
                "Failed scheduling Google wallet refresh for %s no-show "
                "registration(s)",
                len(voiding_google_profiles),
            )


mark_unattended_as_no_show.short_description = _(
    "Mark unchecked-in registrations as No Show"
)


def populate_crush_quiz_questions(modeladmin, request, queryset):
    """Admin action: populate selected QuizEvents with Crush quiz rounds & questions."""
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

        rounds_created, questions_created = populate_quiz(quiz)
        messages.success(
            request,
            f"'{quiz.event}': Created {rounds_created} rounds and "
            f"{questions_created} questions.",
        )


populate_crush_quiz_questions.short_description = _(
    "Populate Crush Quiz questions (6 rounds, 36 questions)"
)


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
        populate_crush_quiz_questions,
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
