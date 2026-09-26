"""
Event Poll admin classes for Crush.lu Coach Panel.
"""

from django.contrib import admin, messages
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
    readonly_fields = ('poll', 'option', 'user', 'voted_at')


class EventPollSuggestionAdmin(admin.ModelAdmin):
    """Voter ideas; approving one adds it to its poll as a new option."""

    list_display = ('text', 'poll', 'user', 'language', 'status', 'created_at')
    list_select_related = ['poll', 'user']
    list_filter = ('status', 'poll')
    search_fields = ('text', 'user__email')
    readonly_fields = (
        'poll', 'user', 'text', 'language', 'promoted_to', 'created_at'
    )
    actions = ['approve_suggestions', 'reject_suggestions']

    def has_add_permission(self, request):
        return False

    @admin.action(description=_("Approve: add as a new poll option"))
    def approve_suggestions(self, request, queryset):
        approved = 0
        for suggestion in queryset.filter(
            status=EventPollSuggestion.Status.PENDING
        ).select_related('poll'):
            last = suggestion.poll.options.aggregate(m=Max('sort_order'))['m']
            option = EventPollOption.objects.create(
                poll=suggestion.poll,
                name=suggestion.text,
                sort_order=(last or 0) + 1,
            )
            suggestion.status = EventPollSuggestion.Status.APPROVED
            suggestion.promoted_to = option
            suggestion.save(update_fields=['status', 'promoted_to'])
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
