"""Admin configuration for the changelog / patch notes system."""

from django.contrib import admin
from django.db.models import Count
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
    list_filter = ("is_published", "released_on", "notes__category")
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

    def get_queryset(self, request):
        # Annotate note count so the list page doesn't fire one COUNT per row.
        qs = super().get_queryset(request)
        return qs.annotate(_note_count=Count("notes", distinct=True))

    def note_count(self, obj):
        return obj._note_count
    note_count.short_description = "Notes"
    note_count.admin_order_field = "_note_count"

    def save_formset(self, request, form, formset, change):
        # When a curator touches a note inline, flag it so the generator
        # won't overwrite it on the next run.
        instances = formset.save(commit=False)
        for obj in instances:
            obj.auto_generated = False
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


class PatchNoteAdmin(AutoTranslateMixin, TranslationAdmin):
    """Admin for individual patch notes (usually edited inline via release)."""

    list_display = ("release", "category", "title", "order", "auto_generated")
    list_filter = ("category", "auto_generated", "release__is_published", "release")
    list_editable = ("order",)
    search_fields = ("title", "body", "release__version", "release__title")
    ordering = ("release", "category", "order")
    autocomplete_fields = ("release",)
    readonly_fields = ("auto_generated",)

    def save_model(self, request, obj, form, change):
        # Any direct admin edit counts as curator-owned.
        obj.auto_generated = False
        super().save_model(request, obj, form, change)


# Wire inline: import the model only here to sidestep load-order issues.
from crush_lu.models import PatchNote, PatchRelease  # noqa: E402

PatchNoteInline.model = PatchNote
PatchReleaseAdmin.inlines = [PatchNoteInline]

# Expose for admin/__init__.py
__all__ = ["PatchReleaseAdmin", "PatchNoteAdmin", "PatchNoteInline"]
