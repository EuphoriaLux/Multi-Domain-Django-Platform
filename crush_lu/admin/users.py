"""
User admin classes for Crush.lu Coach Panel.

Includes:
- CrushProfileUserInline
- CrushCoachUserInline
- CrushUserAdmin
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.urls import reverse
from django.utils.html import format_html

from crush_lu.models import CrushProfile, CrushCoach


class CrushProfileUserInline(admin.StackedInline):
    """Inline showing CrushProfile on User detail page in coach panel"""
    model = CrushProfile
    can_delete = False
    verbose_name_plural = 'Crush.lu Profile'
    fields = ('phone_number', 'gender', 'location', 'bio', 'is_approved', 'is_active', 'completion_status')
    readonly_fields = ('is_approved', 'is_active', 'completion_status')
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False


class CrushCoachUserInline(admin.StackedInline):
    """Inline showing CrushCoach status on User detail page in coach panel"""
    model = CrushCoach
    can_delete = False
    verbose_name_plural = 'Coach Status'
    fields = ('bio', 'specializations', 'is_active', 'max_active_reviews')
    readonly_fields = ('is_active',)
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False


class CrushUserAdmin(BaseUserAdmin):
    """
    User admin for Crush.lu coach panel.
    Shows users with their Crush.lu profiles and provides bidirectional navigation.
    """
    inlines = (CrushProfileUserInline, CrushCoachUserInline)
    list_display = (
        'username', 'email', 'first_name', 'last_name',
        'get_crush_profile_link', 'is_coach_status', 'is_active', 'date_joined'
    )
    list_filter = ('is_active', 'date_joined')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('-date_joined',)

    def get_crush_profile_link(self, obj):
        """Clickable link to CrushProfile if exists"""
        try:
            profile = obj.crushprofile
            url = reverse('crush_admin:crush_lu_crushprofile_change', args=[profile.pk])
            status = '✅' if profile.is_approved else '⏳'
            return format_html(
                '<a href="{}" style="color: #9B59B6; font-weight: bold;">{} View Profile</a>',
                url, status
            )
        except CrushProfile.DoesNotExist:
            return format_html('<span style="color: #999;">No profile</span>')
    get_crush_profile_link.short_description = '💕 Profile'

    def is_coach_status(self, obj):
        """Check if user is an active Crush.lu coach"""
        try:
            return obj.crushcoach.is_active
        except CrushCoach.DoesNotExist:
            return False
    is_coach_status.boolean = True
    is_coach_status.short_description = '🎓 Coach'

    # Restrict fieldsets to essential user info only
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    readonly_fields = ('last_login', 'date_joined')

    # Remove permissions fieldset for coaches (they shouldn't manage staff/superuser)
    def get_fieldsets(self, request, obj=None):
        """Hide permissions for non-superusers"""
        fieldsets = super().get_fieldsets(request, obj)
        if not request.user.is_superuser:
            # Filter out the 'Permissions' fieldset
            return [fs for fs in fieldsets if fs[0] != 'Permissions']
        return fieldsets


# NOTE: User is NOT registered with crush_admin_site to hide "Authentication and Authorization" section
# Users can still be viewed/edited via the "👤 User" links in CrushProfile and CrushCoach admin pages
# which link to the default Django admin site
# crush_admin_site.register(User, CrushUserAdmin)  # Commented out intentionally
