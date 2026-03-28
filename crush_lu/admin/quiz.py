from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from crush_lu.models.quiz import (
    IndividualScore,
    QuizEvent,
    QuizQuestion,
    QuizRound,
    QuizRotationSchedule,
    QuizTable,
    QuizTableMembership,
    TableRoundScore,
)


class QuizEventInline(admin.StackedInline):
    """Inline for creating/editing a QuizEvent from the MeetupEvent admin page."""

    model = QuizEvent
    extra = 0
    max_num = 1
    fields = ("status", "created_by", "current_round", "current_question_index")
    raw_id_fields = ("created_by", "current_round")


class QuizRoundInline(admin.TabularInline):
    model = QuizRound
    extra = 1
    fields = ("title", "sort_order", "time_per_question", "is_bonus")


class QuizQuestionInline(admin.TabularInline):
    model = QuizQuestion
    extra = 1
    fields = (
        "text",
        "question_type",
        "choices",
        "correct_answer",
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
    from crush_lu.models import MeetupEvent
    from crush_lu.models.events import EventRegistration
    from crush_lu.services.quiz_rotation import (
        generate_rotation_schedule,
        split_participants_by_gender,
    )

    for quiz in queryset:
        event = quiz.event

        # Get confirmed registrations with profiles
        registrations = (
            EventRegistration.objects.filter(
                event=event, status="confirmed"
            )
            .select_related("user__crushprofile")
            .order_by("registered_at")
        )

        if registrations.count() < 8:
            messages.error(
                request,
                f"'{event}': Need at least 8 confirmed registrations, "
                f"got {registrations.count()}.",
            )
            continue

        men, women = split_participants_by_gender(registrations)

        try:
            num_tables = len(men) // 2
            num_rounds = quiz.rounds.count() or 3
            schedule = generate_rotation_schedule(men, women, num_rounds)
        except Exception as e:
            messages.error(request, f"'{event}': {e}")
            continue

        # Clear existing rotation data
        QuizRotationSchedule.objects.filter(quiz=quiz).delete()
        QuizTable.objects.filter(quiz=quiz).delete()

        # Create tables
        tables = {}
        for t in range(1, num_tables + 1):
            tables[t] = QuizTable.objects.create(quiz=quiz, table_number=t)

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
            memberships.append(
                QuizTableMembership(table=tables[table_num], user=user)
            )
        QuizTableMembership.objects.bulk_create(memberships)

        messages.success(
            request,
            f"'{event}': Created {num_tables} tables with "
            f"{len(men)} anchors + {len(women)} rotators, "
            f"{num_rounds} rounds.",
        )


generate_quiz_night_tables.short_description = _(
    "Generate Quiz Night table rotation"
)


class QuizEventAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "status",
        "current_round",
        "created_by",
        "created_at",
    )
    list_filter = ("status",)
    inlines = [QuizRoundInline, QuizRotationScheduleInline]
    raw_id_fields = ("event", "created_by", "current_round")
    readonly_fields = ("created_at", "updated_at")
    actions = [generate_quiz_night_tables]


class QuizRoundAdmin(admin.ModelAdmin):
    list_display = ("title", "quiz", "sort_order", "time_per_question", "is_bonus")
    list_filter = ("quiz__event", "is_bonus")
    inlines = [QuizQuestionInline]


class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ("text", "round", "question_type", "points", "sort_order")
    list_filter = ("question_type", "round__quiz__event")


class QuizTableAdmin(admin.ModelAdmin):
    list_display = ("table_number", "quiz", "member_count")
    list_filter = ("quiz__event",)
    inlines = [QuizTableMembershipInline]

    def member_count(self, obj):
        return obj.members.count()

    member_count.short_description = _("Members")


class TableRoundScoreAdmin(admin.ModelAdmin):
    list_display = ("table", "question", "is_correct", "scored_at")
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
    list_filter = ("is_correct", "quiz__event")
    raw_id_fields = ("user", "quiz", "question")
    readonly_fields = ("answered_at",)
