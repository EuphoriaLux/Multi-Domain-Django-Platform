from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from crush_lu.models.quiz import (
    QuizRound,
    QuizQuestion,
    QuizTableMembership,
)


class QuizRoundInline(admin.TabularInline):
    model = QuizRound
    extra = 1
    fields = ("title", "sort_order", "time_per_question")


class QuizQuestionInline(admin.TabularInline):
    model = QuizQuestion
    extra = 1
    fields = ("text", "question_type", "choices", "sort_order", "points")


class QuizTableMembershipInline(admin.TabularInline):
    model = QuizTableMembership
    extra = 1
    raw_id_fields = ("user",)


class QuizEventAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "status",
        "current_round",
        "created_by",
        "created_at",
    )
    list_filter = ("status",)
    inlines = [QuizRoundInline]
    raw_id_fields = ("event", "created_by", "current_round")
    readonly_fields = ("created_at", "updated_at")


class QuizRoundAdmin(admin.ModelAdmin):
    list_display = ("title", "quiz", "sort_order", "time_per_question")
    list_filter = ("quiz__event",)
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
