"""Admin configuration for the matching system (Trait and MatchScore models)."""

from django.contrib import admin
from modeltranslation.admin import TranslationAdmin


class TraitAdmin(TranslationAdmin):
    """Admin for managing personality traits (qualities and defects)."""

    list_display = ("slug", "label", "trait_type", "category", "opposite", "sort_order")
    list_filter = ("trait_type", "category")
    search_fields = ("slug", "label")
    list_editable = ("sort_order",)
    ordering = ("trait_type", "sort_order")


class MatchScoreAdmin(admin.ModelAdmin):
    """Read-only admin for debugging match scores."""

    list_display = (
        "user_a",
        "user_b",
        "score_final",
        "score_qualities",
        "score_zodiac_west",
        "score_zodiac_cn",
        "computed_at",
    )
    list_filter = ("computed_at",)
    search_fields = (
        "user_a__username",
        "user_a__first_name",
        "user_b__username",
        "user_b__first_name",
    )
    readonly_fields = (
        "user_a",
        "user_b",
        "score_qualities",
        "score_zodiac_west",
        "score_zodiac_cn",
        "score_final",
        "computed_at",
    )
    ordering = ("-score_final",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
