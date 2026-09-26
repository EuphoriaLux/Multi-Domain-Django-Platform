"""
Event Poll admin classes for Crush.lu Coach Panel.
"""

from django.contrib import admin, messages
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin, TranslationTabularInline

from azureproject.admin_translation_mixin import AutoTranslateMixin

from crush_lu.models.event_polls import EventPollOption, EventPollSuggestion


class EventPollOptionInline(TranslationTabularInline):
    model = EventPollOption
    extra = 3
    fields = ('name', 'description', 'static_image', 'image', 'icon', 'sort_order')


class EventPollSuggestionInline(admin.TabularInline):
    model = EventPollSuggestion
    extra = 0
    can_delete = False
    fields = ('text', 'user', 'language', 'status', 'created_at')
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class EventPollAdmin(AutoTranslateMixin, TranslationAdmin):
    list_display = (
        'title', 'start_date', 'end_date', 'is_published', 'is_public', 'vote_count'
    )
    list_filter = ('is_published', 'is_public', 'start_date')
    search_fields = ('title',)
    inlines = [EventPollOptionInline, EventPollSuggestionInline]
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'image'),
        }),
        (_('Schedule'), {
            'fields': ('start_date', 'end_date', 'is_published'),
        }),
        (_('Settings'), {
            'fields': (
                'allow_multiple_choices',
                'show_results_before_close',
                'is_public',
            ),
        }),
    )

    def vote_count(self, obj):
        return obj.votes.count()
    vote_count.short_description = _("Votes")


class EventPollVoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'poll', 'option', 'voted_at')
    list_select_related = ["user", "poll", "option"]
    list_filter = ('poll', 'voted_at')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('poll', 'option', 'user', 'voter_gender', 'voted_at')


class EventPollSuggestionAdmin(admin.ModelAdmin):
    """Voter ideas; approving one adds it to its poll as a new option."""

    list_display = ('text', 'poll', 'user', 'language', 'status', 'created_at')
    list_select_related = ['poll', 'user']
    list_filter = ('status', 'poll')
    search_fields = ('text', 'user__email')
    # status changes only through the actions, so approval always promotes
    readonly_fields = (
        'poll', 'user', 'text', 'language', 'status', 'promoted_to', 'created_at'
    )
    actions = ['approve_suggestions', 'reject_suggestions']

    def has_add_permission(self, request):
        return False

    @admin.action(description=_("Approve: add as a new poll option"))
    def approve_suggestions(self, request, queryset):
        approved = 0
        languages = set(settings.MODELTRANSLATION_LANGUAGES)
        for suggestion in queryset.filter(
            status=EventPollSuggestion.Status.PENDING
        ).select_related('poll'):
            with transaction.atomic():
                # Claim it first: of two concurrent approvals only one flips
                # the row, so only one creates an option.
                claimed = EventPollSuggestion.objects.filter(
                    pk=suggestion.pk, status=EventPollSuggestion.Status.PENDING
                ).update(status=EventPollSuggestion.Status.APPROVED)
                if not claimed:
                    continue
                # Store the text in the language it was written in. English
                # is the only fallback language, so it also fills name_en
                # until a coach translates it.
                language = (suggestion.language or '')[:2]
                names = {'name_en': suggestion.text}
                if language in languages:
                    names[f'name_{language}'] = suggestion.text
                last = suggestion.poll.options.aggregate(m=Max('sort_order'))['m']
                option = EventPollOption.objects.create(
                    poll=suggestion.poll,
                    sort_order=(last or 0) + 1,
                    **names,
                )
                EventPollSuggestion.objects.filter(pk=suggestion.pk).update(
                    promoted_to=option
                )
            approved += 1
        self.message_user(
            request,
            _("%(count)d suggestion(s) added as poll options. "
              "Add translations and an image on the poll.") % {'count': approved},
            messages.SUCCESS,
        )

    @admin.action(description=_("Reject selected suggestions"))
    def reject_suggestions(self, request, queryset):
        count = queryset.filter(status=EventPollSuggestion.Status.PENDING).update(
            status=EventPollSuggestion.Status.REJECTED
        )
        self.message_user(
            request, _("%(count)d suggestion(s) rejected.") % {'count': count}
        )
