from django.contrib import admin
from modeltranslation.admin import TranslationTabularInline

from crush_lu.admin.site import crush_admin_site

from .models import BioSegment, CardChallenge, EmpireState, GameProfile


class BioSegmentInline(TranslationTabularInline):
    """Per-language tabs on `text` and `explanation`; the flag itself is neutral."""

    model = BioSegment
    extra = 1
    fields = ("order", "text", "is_red_flag", "flag_type", "explanation")


class GameProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "age", "emoji", "is_scam", "is_active", "weight")
    list_filter = ("is_scam", "is_active")
    search_fields = ("display_name",)
    inlines = [BioSegmentInline]

    fieldsets = (
        (None, {"fields": ("emoji", "display_name", "age")}),
        (
            "Deck",
            {
                "fields": ("is_scam", "is_active", "weight"),
                "description": (
                    "is_scam is the answer key. It is never sent to a player "
                    "before they answer. Never model a card on a real reported "
                    "member."
                ),
            },
        ),
    )


class CardChallengeAdmin(admin.ModelAdmin):
    """Read-only: an audit trail of what was dealt and how it was answered."""

    list_display = ("issued_at", "user", "profile", "action", "outcome", "reward_flags")
    list_filter = ("outcome", "action")
    date_hierarchy = "issued_at"
    readonly_fields = [f.name for f in CardChallenge._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class EmpireStateAdmin(admin.ModelAdmin):
    list_display = ("user", "total_earned", "hearts", "flags", "best_streak")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at", "last_tick")


crush_admin_site.register(GameProfile, GameProfileAdmin)
crush_admin_site.register(CardChallenge, CardChallengeAdmin)
crush_admin_site.register(EmpireState, EmpireStateAdmin)
