"""
Django Admin configuration for Crush Delegation app.

Provides admin interfaces for managing companies, user profiles, and access logs.
"""
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import Company, DelegationProfile, AccessLog


# ============================================================================
# CUSTOM ADMIN SITE - Crush Delegation Management
# ============================================================================

class CrushDelegationAdminSite(admin.AdminSite):
    site_header = '👥 Crush Delegation Management'
    site_title = 'Crush Delegation Admin'
    index_title = 'Company & Employee Access Management'

    def get_app_list(self, request, app_label=None):
        """
        Override to customize the admin index page grouping.
        Groups models into logical categories for better organization.
        """
        app_list = super().get_app_list(request, app_label)

        # Custom ordering and grouping
        custom_order = {
            'company': {'order': 1, 'icon': '🏢', 'group': 'Companies'},
            'delegationprofile': {'order': 10, 'icon': '👤', 'group': 'Users'},
            'accesslog': {'order': 20, 'icon': '📋', 'group': 'Audit'},
        }

        # Create grouped app list
        new_app_list = []

        for app in app_list:
            if app['app_label'] == 'crush_delegation':
                # Group models by category
                groups = {}

                for model in app['models']:
                    model_name = model['object_name'].lower()

                    if model_name in custom_order:
                        config = custom_order[model_name]
                        model['_order'] = config['order']
                        group_name = config['group']

                        # Add icon to model name
                        icon = config['icon']
                        if not model['name'].startswith(icon):
                            model['name'] = f"{icon} {model['name']}"

                        if group_name not in groups:
                            groups[group_name] = []
                        groups[group_name].append(model)
                    else:
                        if 'Other' not in groups:
                            groups['Other'] = []
                        groups['Other'].append(model)

                # Sort models within each group
                for group_name in groups:
                    groups[group_name].sort(key=lambda x: x.get('_order', 999))

                # Create new apps for each group
                group_order = ['Companies', 'Users', 'Audit', 'Other']
                group_icons = {
                    'Companies': '🏢',
                    'Users': '👤',
                    'Audit': '📋',
                    'Other': '📁',
                }

                for group_key in group_order:
                    if group_key in groups and groups[group_key]:
                        new_app_list.append({
                            'name': f"{group_icons.get(group_key, '')} {group_key}",
                            'app_label': f'crush_delegation_{group_key.lower()}',
                            'app_url': app['app_url'],
                            'has_module_perms': app['has_module_perms'],
                            'models': groups[group_key],
                        })
            else:
                new_app_list.append(app)

        return new_app_list


# Instantiate the custom admin site
crush_delegation_admin_site = CrushDelegationAdminSite(name='crush_delegation_admin')


class CompanyAdmin(admin.ModelAdmin):
    """Admin interface for Company management."""

    list_display = [
        'name', 'slug', 'get_domains_display', 'is_active',
        'auto_approve_workers', 'profile_count', 'created_at'
    ]
    list_filter = ['is_active', 'auto_approve_workers', 'created_at']
    search_fields = ['name', 'slug', 'email_domains']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'description', 'logo')
        }),
        ('Domain Configuration', {
            'fields': ('email_domains', 'microsoft_tenant_id'),
            'description': 'Configure how users are matched to this company.'
        }),
        ('Settings', {
            'fields': ('is_active', 'auto_approve_workers')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def profile_count(self, obj):
        """Return count of profiles in this company."""
        return obj.profiles.count()
    profile_count.short_description = 'Users'


class DelegationProfileAdmin(admin.ModelAdmin):
    """Admin interface for DelegationProfile management with approve/reject actions."""

    list_display = [
        'user_display', 'company', 'job_title', 'role', 'status_badge',
        'manually_approved', 'manually_blocked', 'last_login_at'
    ]
    list_filter = [
        'status', 'role', 'company', 'manually_approved',
        'manually_blocked', 'created_at'
    ]
    search_fields = [
        'user__email', 'user__first_name', 'user__last_name',
        'job_title', 'department'
    ]
    raw_id_fields = ['user']
    readonly_fields = [
        'microsoft_id', 'microsoft_tenant_id', 'created_at',
        'updated_at', 'approved_at', 'last_login_at', 'profile_photo_preview'
    ]
    actions = ['approve_profiles', 'reject_profiles', 'block_profiles', 'unblock_profiles']

    fieldsets = (
        ('User', {
            'fields': ('user', 'company', 'profile_photo_preview')
        }),
        ('Microsoft Account', {
            'fields': (
                'microsoft_id', 'microsoft_tenant_id',
                'job_title', 'department', 'office_location'
            ),
            'classes': ('collapse',)
        }),
        ('Access Control', {
            'fields': (
                'role', 'status', 'manually_approved', 'manually_blocked',
                'rejection_reason'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'approved_at', 'last_login_at'),
            'classes': ('collapse',)
        }),
    )

    def user_display(self, obj):
        """Display user name and email."""
        name = obj.user.get_full_name() or obj.user.email.split('@')[0]
        return f"{name} ({obj.user.email})"
    user_display.short_description = 'User'
    user_display.admin_order_field = 'user__email'

    def status_badge(self, obj):
        """Display status with color coding."""
        colors = {
            'approved': '#28a745',
            'pending': '#ffc107',
            'rejected': '#dc3545',
            'no_company': '#6c757d',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; '
            'border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def profile_photo_preview(self, obj):
        """Display profile photo thumbnail."""
        if obj.profile_photo:
            return format_html(
                '<img src="{}" style="max-width: 100px; max-height: 100px; '
                'border-radius: 50%;" />',
                obj.profile_photo.url
            )
        return '-'
    profile_photo_preview.short_description = 'Photo'

    @admin.action(description='Approve selected profiles')
    def approve_profiles(self, request, queryset):
        """Bulk approve selected profiles."""
        count = 0
        for profile in queryset:
            if not profile.is_approved:
                profile.status = 'approved'
                profile.role = 'worker'
                profile.approved_at = timezone.now()
                profile.save(update_fields=['status', 'role', 'approved_at', 'updated_at'])
                AccessLog.objects.create(
                    profile=profile,
                    action='manual_approved',
                    details=f'Approved by {request.user.email}'
                )
                count += 1
        self.message_user(request, f'{count} profile(s) approved.')

    @admin.action(description='Reject selected profiles')
    def reject_profiles(self, request, queryset):
        """Bulk reject selected profiles."""
        count = 0
        for profile in queryset:
            if profile.status != 'rejected':
                profile.status = 'rejected'
                profile.rejection_reason = f'Rejected by admin ({request.user.email})'
                profile.save(update_fields=['status', 'rejection_reason', 'updated_at'])
                AccessLog.objects.create(
                    profile=profile,
                    action='manual_rejected',
                    details=f'Rejected by {request.user.email}'
                )
                count += 1
        self.message_user(request, f'{count} profile(s) rejected.')

    @admin.action(description='Block selected profiles')
    def block_profiles(self, request, queryset):
        """Manually block selected profiles."""
        count = queryset.update(manually_blocked=True)
        for profile in queryset:
            AccessLog.objects.create(
                profile=profile,
                action='login_blocked',
                details=f'Manually blocked by {request.user.email}'
            )
        self.message_user(request, f'{count} profile(s) blocked.')

    @admin.action(description='Unblock selected profiles')
    def unblock_profiles(self, request, queryset):
        """Remove manual block from selected profiles."""
        count = queryset.update(manually_blocked=False)
        self.message_user(request, f'{count} profile(s) unblocked.')


class AccessLogAdmin(admin.ModelAdmin):
    """Admin interface for viewing access audit logs."""

    list_display = ['profile', 'action', 'short_details', 'ip_address', 'created_at']
    list_filter = ['action', 'created_at']
    search_fields = ['profile__user__email', 'details', 'ip_address']
    readonly_fields = ['profile', 'action', 'details', 'ip_address', 'user_agent', 'created_at']
    date_hierarchy = 'created_at'

    def short_details(self, obj):
        """Truncate details for display."""
        if len(obj.details) > 50:
            return obj.details[:50] + '...'
        return obj.details or '-'
    short_details.short_description = 'Details'

    def has_add_permission(self, request):
        """Disable manual creation of access logs."""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable editing of access logs."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Only superusers can delete access logs."""
        return request.user.is_superuser


# ============================================================================
# REGISTER MODELS TO CUSTOM CRUSH DELEGATION ADMIN SITE
# ============================================================================

crush_delegation_admin_site.register(Company, CompanyAdmin)
crush_delegation_admin_site.register(DelegationProfile, DelegationProfileAdmin)
crush_delegation_admin_site.register(AccessLog, AccessLogAdmin)
