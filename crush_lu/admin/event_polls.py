"""
Event Poll admin classes for Crush.lu Coach Panel.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin, TranslationTabularInline

from azureproject.admin_translation_mixin import AutoTranslateMixin

from crush_lu.models.event_polls import EventPoll, EventPollOption, EventPollVote


class EventPollOptionInline(TranslationTabularInline):
    model = EventPollOption
    extra = 3
    fields = ('name', 'description', 'static_image', 'image', 'icon', 'sort_order')


class EventPollAdmin(AutoTranslateMixin, TranslationAdmin):
    list_display = ('title', 'start_date', 'end_date', 'is_published', 'vote_count')
    list_filter = ('is_published', 'start_date')
    search_fields = ('title',)
    inlines = [EventPollOptionInline]
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'image'),
        }),
        (_('Schedule'), {
            'fields': ('start_date', 'end_date', 'is_published'),
        }),
        (_('Settings'), {
            'fields': ('allow_multiple_choices', 'show_results_before_close'),
        }),
    )

    def vote_count(self, obj):
        return obj.votes.count()
    vote_count.short_description = _("Votes")


class EventPollVoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'poll', 'option', 'voted_at')
    list_filter = ('poll', 'voted_at')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('poll', 'option', 'user', 'voted_at')
