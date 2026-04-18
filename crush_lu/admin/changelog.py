"""Admin configuration for the changelog / patch notes system."""

from django.contrib import admin
from django.utils.html import format_html
from modeltranslation.admin import TranslationAdmin, TranslationStackedInline

from azureproject.admin_translation_mixin import AutoTranslateMixin


class PatchNoteInline(AutoTranslateMixin, TranslationStackedInline):
    """Inline editor for patch notes attached to a release."""

    model = None  # set below after import to avoid circular refs at module load
    extra = 0
    fields = ("category", "order", "title", "body", "related_commits")
    classes = ("collapse",)


class PatchReleaseAdmin(AutoTranslateMixin, TranslationAdmin):
    """Admin for PatchRelease with inline note editing and publish toggle."""

    list_display = (
        "version",
        "title",
        "released_on",
        "is_published",
        "note_count",
        "updated_at",
    )
    list_filter = ("is_published", "released_on")
    search_fields = ("version", "slug", "title", "hero_summary")
    list_editable = ("is_published",)
    prepopulated_fields = {"slug": ("version",)}
    date_hierarchy = "released_on"
    ordering = ("-released_on", "-version")
    fieldsets = (
        (None, {
            "fields": ("version", "slug", "title", "hero_summary", "released_on", "is_published"),
        }),
        ("Provenance", {
            "classes": ("collapse",),
            "fields": ("commit_range_start", "commit_range_end"),
        }),
    )
    inlines = []  # populated below

    def note_count(self, obj):
        count = obj.notes.count()
        if not count:
            return format_html('<span style="color:#999;">0</span>')
        return count
    note_count.short_description = "Notes"


class PatchNoteAdmin(AutoTranslateMixin, TranslationAdmin):
    """Admin for individual patch notes (usually edited inline via release)."""

    list_display = ("release", "category", "title", "order")
    list_filter = ("category", "release__is_published", "release")
    list_editable = ("order",)
    search_fields = ("title", "body", "release__version", "release__title")
    ordering = ("release", "category", "order")
    autocomplete_fields = ("release",)


# Wire inline: import the model only here to sidestep load-order issues.
from crush_lu.models import PatchNote, PatchRelease  # noqa: E402

PatchNoteInline.model = PatchNote
PatchReleaseAdmin.inlines = [PatchNoteInline]

# Expose for admin/__init__.py
__all__ = ["PatchReleaseAdmin", "PatchNoteAdmin", "PatchNoteInline"]
