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
from django.utils import timezone
from django.utils.html import format_html

from crush_lu.models import CrushProfile, CrushCoach, UserDataConsent


class UserDataConsentInline(admin.StackedInline):
    """Inline showing UserDataConsent on User detail page"""
    model = UserDataConsent
    can_delete = False
    verbose_name_plural = 'Data Consent (GDPR)'
    fields = (
        'powerup_consent_status',
        'crushlu_consent_status',
        'crushlu_ban_status',
        'marketing_consent',
    )
    readonly_fields = (
        'powerup_consent_status',
        'crushlu_consent_status',
        'crushlu_ban_status',
    )
    extra = 0

    def powerup_consent_status(self, obj):
        """Display PowerUp consent with date and IP"""
        if obj.powerup_consent_given:
            date_str = obj.powerup_consent_date.strftime('%Y-%m-%d %H:%M') if obj.powerup_consent_date else 'Unknown'
            ip_str = f" from {obj.powerup_consent_ip}" if obj.powerup_consent_ip else ""
            return format_html(
                '<span style="color: green;">✅ Given on {}{}</span>',
                date_str, ip_str
            )
        return format_html('<span style="color: red;">❌ Not given</span>')
    powerup_consent_status.short_description = 'PowerUp Consent (Identity Layer)'

    def crushlu_consent_status(self, obj):
        """Display Crush.lu consent with date and IP"""
        if obj.crushlu_consent_given:
            date_str = obj.crushlu_consent_date.strftime('%Y-%m-%d %H:%M') if obj.crushlu_consent_date else 'Unknown'
            ip_str = f" from {obj.crushlu_consent_ip}" if obj.crushlu_consent_ip else ""
            return format_html(
                '<span style="color: green;">✅ Given on {}{}</span>',
                date_str, ip_str
            )
        return format_html('<span style="color: red;">❌ Not given</span>')
    crushlu_consent_status.short_description = 'Crush.lu Consent (Profile Layer)'

    def crushlu_ban_status(self, obj):
        """Display ban status"""
        if obj.crushlu_banned:
            reason_map = {
                'user_deletion': 'User deleted profile',
                'admin_action': 'Admin action',
                'terms_violation': 'Terms violation',
            }
            reason = reason_map.get(obj.crushlu_ban_reason, obj.crushlu_ban_reason or 'Unknown')
            date_str = obj.crushlu_ban_date.strftime('%Y-%m-%d') if obj.crushlu_ban_date else 'Unknown'
            return format_html(
                '<span style="color: red; font-weight: bold;">🚫 BANNED since {} ({})</span>',
                date_str, reason
            )
        return format_html('<span style="color: green;">✅ Not banned</span>')
    crushlu_ban_status.short_description = 'Crush.lu Ban Status'

    def has_add_permission(self, request, obj=None):
        return False


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


class HasCrushProfileFilter(admin.SimpleListFilter):
    """Filter users by whether they have a CrushProfile"""
    title = 'Crush Profile Status'
    parameter_name = 'has_profile'

    def lookups(self, request, model_admin):
        return (
            ('yes', '✅ Has Profile'),
            ('no', '❌ No Profile (Never Started)'),
        )

    def queryset(self, request, queryset):
        from django.db.models import Exists, OuterRef

        if self.value() == 'yes':
            return queryset.filter(
                Exists(
                    CrushProfile.objects.filter(
                        user_id=OuterRef('id')
                    )
                )
            )
        elif self.value() == 'no':
            return queryset.filter(
                ~Exists(
                    CrushProfile.objects.filter(
                        user_id=OuterRef('id')
                    )
                )
            )
        return queryset


class BannedUserFilter(admin.SimpleListFilter):
    """Filter users by Crush.lu ban status"""
    title = 'Ban Status'
    parameter_name = 'banned'

    def lookups(self, request, model_admin):
        return (
            ('banned', '🚫 Banned'),
            ('not_banned', '✅ Not Banned'),
        )

    def queryset(self, request, queryset):
        from django.db.models import Exists, OuterRef

        if self.value() == 'banned':
            return queryset.filter(
                Exists(
                    UserDataConsent.objects.filter(
                        user_id=OuterRef('id'),
                        crushlu_banned=True,
                    )
                )
            )
        elif self.value() == 'not_banned':
            return queryset.filter(
                ~Exists(
                    UserDataConsent.objects.filter(
                        user_id=OuterRef('id'),
                        crushlu_banned=True,
                    )
                )
            )
        return queryset


def ban_users(modeladmin, request, queryset):
    """Admin action to ban selected users from Crush.lu."""
    count = 0
    for user in queryset:
        consent, _ = UserDataConsent.objects.get_or_create(user=user)
        if not consent.crushlu_banned:
            consent.crushlu_banned = True
            consent.crushlu_ban_reason = 'admin_action'
            consent.crushlu_ban_date = timezone.now()
            consent.crushlu_consent_given = False
            consent.save()
            user.is_active = False
            user.save(update_fields=['is_active'])
            count += 1
    modeladmin.message_user(request, f'{count} user(s) banned from Crush.lu.')


ban_users.short_description = '🚫 Ban selected users from Crush.lu'


def unban_users(modeladmin, request, queryset):
    """Admin action to unban selected users."""
    count = 0
    for user in queryset:
        if hasattr(user, 'data_consent') and user.data_consent.crushlu_banned:
            consent = user.data_consent
            consent.crushlu_banned = False
            consent.crushlu_ban_reason = ''
            consent.crushlu_ban_date = None
            consent.save()
            user.is_active = True
            user.save(update_fields=['is_active'])
            count += 1
    modeladmin.message_user(request, f'{count} user(s) unbanned.')


unban_users.short_description = '✅ Unban selected users'


class CrushUserAdmin(BaseUserAdmin):
    """
    User admin for Crush.lu coach panel.
    Shows users with their Crush.lu profiles and provides bidirectional navigation.
    """
    inlines = (UserDataConsentInline, CrushProfileUserInline, CrushCoachUserInline)
    actions = [ban_users, unban_users]
    list_display = (
        'username', 'email', 'first_name', 'last_name',
        'get_crush_profile_link', 'get_consent_status', 'is_coach_status', 'is_active', 'date_joined'
    )
    list_filter = (HasCrushProfileFilter, BannedUserFilter, 'is_active', 'date_joined')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('-date_joined',)

    def get_consent_status(self, obj):
        """Display consent status icons"""
        if not hasattr(obj, 'data_consent'):
            return format_html('<span style="color: red;">❌ No consent record</span>')

        consent = obj.data_consent
        powerup_icon = '✅' if consent.powerup_consent_given else '❌'
        crushlu_icon = '✅' if consent.crushlu_consent_given else '❌'
        ban_icon = '🚫' if consent.crushlu_banned else ''

        return format_html(
            '<span title="PowerUp: {}, Crush.lu: {}">PowerUp:{} Crush:{} {}</span>',
            'Given' if consent.powerup_consent_given else 'Not given',
            'Given' if consent.crushlu_consent_given else 'Not given',
            powerup_icon,
            crushlu_icon,
            ban_icon
        )
    get_consent_status.short_description = '📋 Consent'

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

    def has_module_permission(self, request):
        """Hide User from crush-admin sidebar/index.

        User is registered on crush_admin_site only so that
        autocomplete_fields (Select2 lookups) work — Django requires
        the target model on the same AdminSite.  Individual user
        records are still reachable via direct links from profiles.
        The full user list lives at /admin/auth/user/.
        """
        return False
