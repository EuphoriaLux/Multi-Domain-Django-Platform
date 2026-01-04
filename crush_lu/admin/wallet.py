"""
Wallet Pass admin classes for Crush.lu Coach Panel.

Provides admin interface for managing Apple and Google Wallet passes,
including viewing pass status, triggering updates, and batch operations.
"""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.db.models import Q
from django.urls import reverse
from django.utils.html import format_html

from crush_lu.models import CrushProfile


class WalletPassFilter(admin.SimpleListFilter):
    """Filter profiles by wallet pass status"""
    title = 'Wallet Pass Status'
    parameter_name = 'wallet_status'

    def lookups(self, request, model_admin):
        return (
            ('has_apple', 'Has Apple Wallet'),
            ('has_google', 'Has Google Wallet'),
            ('has_both', 'Has Both'),
            ('has_any', 'Has Any Pass'),
            ('no_pass', 'No Wallet Pass'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'has_apple':
            return queryset.exclude(
                Q(apple_pass_serial__isnull=True) | Q(apple_pass_serial='')
            )
        if self.value() == 'has_google':
            return queryset.exclude(
                Q(google_wallet_object_id__isnull=True) | Q(google_wallet_object_id='')
            )
        if self.value() == 'has_both':
            return queryset.exclude(
                Q(apple_pass_serial__isnull=True) | Q(apple_pass_serial='')
            ).exclude(
                Q(google_wallet_object_id__isnull=True) | Q(google_wallet_object_id='')
            )
        if self.value() == 'has_any':
            return queryset.filter(
                Q(apple_pass_serial__isnull=False) & ~Q(apple_pass_serial='') |
                Q(google_wallet_object_id__isnull=False) & ~Q(google_wallet_object_id='')
            )
        if self.value() == 'no_pass':
            return queryset.filter(
                Q(apple_pass_serial__isnull=True) | Q(apple_pass_serial=''),
                Q(google_wallet_object_id__isnull=True) | Q(google_wallet_object_id=''),
            )
        return queryset


class WalletPassAdmin(admin.ModelAdmin):
    """
    Admin view for managing wallet passes.

    This is a proxy admin that shows CrushProfiles with wallet pass management
    actions and information.
    """
    list_display = (
        'get_user_link',
        'get_email',
        'get_tier_badge',
        'referral_points',
        'get_apple_pass_status',
        'get_google_pass_status',
        'get_next_event_display',
        'get_actions_buttons',
    )
    list_filter = (WalletPassFilter, 'membership_tier', 'is_approved')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    ordering = ['-referral_points', '-created_at']
    readonly_fields = (
        'get_wallet_summary',
        'apple_pass_serial',
        'apple_auth_token',
        'google_wallet_object_id',
        'referral_points',
        'membership_tier',
    )
    actions = [
        'update_google_wallet_passes',
        'update_apple_wallet_passes',
        'update_all_wallet_passes',
    ]

    # Only show profiles with wallet passes by default
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('user').filter(is_approved=True)

    def has_add_permission(self, request):
        # Don't allow adding - users get passes through the app
        return False

    def has_delete_permission(self, request, obj=None):
        # Don't allow deleting profiles from here
        return False

    def get_user_link(self, obj):
        """Display username with link to profile"""
        profile_url = reverse('crush_admin:crush_lu_crushprofile_change', args=[obj.pk])
        return format_html(
            '<a href="{}" style="color: #9B59B6; font-weight: bold;">{}</a>',
            profile_url,
            obj.user.get_full_name() or obj.user.username
        )
    get_user_link.short_description = 'User'
    get_user_link.admin_order_field = 'user__first_name'

    def get_email(self, obj):
        return obj.user.email
    get_email.short_description = 'Email'
    get_email.admin_order_field = 'user__email'

    def get_tier_badge(self, obj):
        """Display membership tier with badge"""
        tier = obj.membership_tier or 'basic'
        tier_config = {
            'basic': {'emoji': '💜', 'color': '#9B59B6', 'bg': '#f0e6f7'},
            'bronze': {'emoji': '🥉', 'color': '#CD7F32', 'bg': '#fdf4e8'},
            'silver': {'emoji': '🥈', 'color': '#C0C0C0', 'bg': '#f5f5f5'},
            'gold': {'emoji': '🥇', 'color': '#FFD700', 'bg': '#fffbe6'},
        }
        config = tier_config.get(tier, tier_config['basic'])
        return format_html(
            '<span style="background: {}; color: {}; padding: 3px 10px; '
            'border-radius: 12px; font-size: 12px; font-weight: bold;">'
            '{} {}</span>',
            config['bg'], config['color'], config['emoji'], tier.capitalize()
        )
    get_tier_badge.short_description = 'Tier'
    get_tier_badge.admin_order_field = 'membership_tier'

    def get_apple_pass_status(self, obj):
        """Display Apple Wallet pass status"""
        if obj.apple_pass_serial:
            return format_html(
                '<span style="color: #28a745;" title="Serial: {}">🍎 Active</span>',
                obj.apple_pass_serial[:20] + '...' if len(obj.apple_pass_serial) > 20 else obj.apple_pass_serial
            )
        return format_html('<span style="color: #999;">—</span>')
    get_apple_pass_status.short_description = 'Apple'

    def get_google_pass_status(self, obj):
        """Display Google Wallet pass status"""
        if obj.google_wallet_object_id:
            return format_html(
                '<span style="color: #28a745;" title="Object ID: {}">🤖 Active</span>',
                obj.google_wallet_object_id[:30] + '...' if len(obj.google_wallet_object_id) > 30 else obj.google_wallet_object_id
            )
        return format_html('<span style="color: #999;">—</span>')
    get_google_pass_status.short_description = 'Google'

    def get_next_event_display(self, obj):
        """Display next event info"""
        from crush_lu.wallet_pass import get_next_event_for_pass
        next_event = get_next_event_for_pass(obj)
        if next_event:
            return format_html(
                '<span title="{}">{}</span>',
                next_event.get('date', ''),
                next_event.get('title', '')[:25] + '...' if len(next_event.get('title', '')) > 25 else next_event.get('title', '')
            )
        return format_html('<span style="color: #999;">No events</span>')
    get_next_event_display.short_description = 'Next Event'

    def get_actions_buttons(self, obj):
        """Display quick action buttons"""
        buttons = []

        if obj.google_wallet_object_id:
            buttons.append(
                f'<a href="#" onclick="updateGooglePass({obj.pk}); return false;" '
                f'style="color: #4285f4; font-size: 11px;" title="Update Google Pass">🔄 Google</a>'
            )

        if obj.apple_pass_serial:
            buttons.append(
                f'<a href="#" onclick="updateApplePass({obj.pk}); return false;" '
                f'style="color: #333; font-size: 11px;" title="Update Apple Pass">🔄 Apple</a>'
            )

        if not buttons:
            return format_html('<span style="color: #999; font-size: 11px;">No pass</span>')

        return format_html(' | '.join(buttons))
    get_actions_buttons.short_description = 'Actions'

    def get_wallet_summary(self, obj):
        """Display comprehensive wallet summary"""
        from crush_lu.wallet_pass import build_wallet_pass_data

        pass_data = build_wallet_pass_data(obj)

        html = '''
        <div style="background: linear-gradient(135deg, #9B59B6, #FF6B9D);
                    color: white; padding: 20px; border-radius: 12px; margin-bottom: 15px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0; font-size: 18px;">{display_name}</h3>
                    <p style="margin: 5px 0 0 0; opacity: 0.9;">{tier_display}</p>
                </div>
                <div style="text-align: right;">
                    <div style="font-size: 24px; font-weight: bold;">{points:,}</div>
                    <div style="font-size: 12px; opacity: 0.9;">POINTS</div>
                </div>
            </div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
            <div style="background: #f8f9fa; padding: 15px; border-radius: 8px;">
                <h4 style="margin: 0 0 10px 0; color: #333;">🍎 Apple Wallet</h4>
                {apple_status}
            </div>
            <div style="background: #f8f9fa; padding: 15px; border-radius: 8px;">
                <h4 style="margin: 0 0 10px 0; color: #4285f4;">🤖 Google Wallet</h4>
                {google_status}
            </div>
        </div>

        <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-top: 15px;">
            <h4 style="margin: 0 0 10px 0; color: #9B59B6;">📋 Pass Data</h4>
            <table style="width: 100%; font-size: 13px;">
                <tr><td style="padding: 4px 0;"><strong>Referral URL:</strong></td>
                    <td><code style="background: #e9ecef; padding: 2px 6px; border-radius: 4px;">{referral_url}</code></td></tr>
                <tr><td style="padding: 4px 0;"><strong>Next Event:</strong></td>
                    <td>{next_event}</td></tr>
                <tr><td style="padding: 4px 0;"><strong>Member Since:</strong></td>
                    <td>{member_since}</td></tr>
                <tr><td style="padding: 4px 0;"><strong>Photo on Pass:</strong></td>
                    <td>{has_photo}</td></tr>
            </table>
        </div>
        '''

        apple_status = '<span style="color: #28a745;">✅ Active</span><br><code style="font-size: 10px;">{}</code>'.format(
            obj.apple_pass_serial[:40] + '...' if obj.apple_pass_serial and len(obj.apple_pass_serial) > 40 else obj.apple_pass_serial or ''
        ) if obj.apple_pass_serial else '<span style="color: #999;">Not installed</span>'

        google_status = '<span style="color: #28a745;">✅ Active</span><br><code style="font-size: 10px;">{}</code>'.format(
            obj.google_wallet_object_id[:40] + '...' if obj.google_wallet_object_id and len(obj.google_wallet_object_id) > 40 else obj.google_wallet_object_id or ''
        ) if obj.google_wallet_object_id else '<span style="color: #999;">Not installed</span>'

        next_event_info = pass_data.get('next_event')
        if next_event_info:
            next_event_str = f"{next_event_info.get('title', '')} - {next_event_info.get('date', '')}"
        else:
            next_event_str = '<span style="color: #999;">No upcoming events</span>'

        return format_html(
            html,
            display_name=pass_data.get('display_name', ''),
            tier_display=pass_data.get('tier_display', ''),
            points=pass_data.get('referral_points', 0),
            apple_status=apple_status,
            google_status=google_status,
            referral_url=pass_data.get('referral_url', ''),
            next_event=next_event_str,
            member_since=pass_data.get('member_since', 'N/A'),
            has_photo='Yes' if pass_data.get('photo_url') else 'No',
        )
    get_wallet_summary.short_description = 'Wallet Pass Preview'

    fieldsets = (
        ('Wallet Pass Preview', {
            'fields': ('get_wallet_summary',),
            'description': 'Preview of how the wallet pass looks',
        }),
        ('Apple Wallet', {
            'fields': ('apple_pass_serial', 'apple_auth_token'),
            'classes': ('collapse',),
        }),
        ('Google Wallet', {
            'fields': ('google_wallet_object_id',),
            'classes': ('collapse',),
        }),
        ('Points & Tier', {
            'fields': ('referral_points', 'membership_tier'),
        }),
    )

    @admin.action(description='🔄 Update Google Wallet passes')
    def update_google_wallet_passes(self, request, queryset):
        """Update Google Wallet passes for selected profiles"""
        from crush_lu.wallet.google_api import update_google_wallet_pass

        updated = 0
        failed = 0
        skipped = 0

        for profile in queryset:
            if not profile.google_wallet_object_id:
                skipped += 1
                continue

            try:
                result = update_google_wallet_pass(profile)
                if result.get('success'):
                    updated += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1

        if updated > 0:
            django_messages.success(request, f'🤖 Updated {updated} Google Wallet pass(es)')
        if failed > 0:
            django_messages.warning(request, f'❌ Failed to update {failed} pass(es)')
        if skipped > 0:
            django_messages.info(request, f'⏭️ Skipped {skipped} profile(s) without Google Wallet')

    @admin.action(description='🔄 Update Apple Wallet passes (trigger refresh)')
    def update_apple_wallet_passes(self, request, queryset):
        """Trigger Apple Wallet pass refresh for selected profiles"""
        from crush_lu.signals import _trigger_apple_pass_refresh

        updated = 0
        skipped = 0

        for profile in queryset:
            if not profile.apple_pass_serial:
                skipped += 1
                continue

            try:
                _trigger_apple_pass_refresh(profile)
                updated += 1
            except Exception:
                pass  # Apple refresh is best-effort

        if updated > 0:
            django_messages.success(request, f'🍎 Triggered refresh for {updated} Apple Wallet pass(es)')
        if skipped > 0:
            django_messages.info(request, f'⏭️ Skipped {skipped} profile(s) without Apple Wallet')

    @admin.action(description='🔄 Update ALL wallet passes (Apple + Google)')
    def update_all_wallet_passes(self, request, queryset):
        """Update both Apple and Google wallet passes"""
        from crush_lu.signals import trigger_wallet_pass_updates

        updated = 0
        skipped = 0

        for profile in queryset:
            if not profile.apple_pass_serial and not profile.google_wallet_object_id:
                skipped += 1
                continue

            try:
                trigger_wallet_pass_updates(profile)
                updated += 1
            except Exception:
                pass

        if updated > 0:
            django_messages.success(request, f'🔄 Updated {updated} wallet pass(es)')
        if skipped > 0:
            django_messages.info(request, f'⏭️ Skipped {skipped} profile(s) without any wallet pass')

    def changelist_view(self, request, extra_context=None):
        """Add statistics to the changelist view"""
        extra_context = extra_context or {}

        # Calculate wallet pass statistics
        total_profiles = CrushProfile.objects.filter(is_approved=True).count()
        apple_passes = CrushProfile.objects.exclude(
            Q(apple_pass_serial__isnull=True) | Q(apple_pass_serial='')
        ).count()
        google_passes = CrushProfile.objects.exclude(
            Q(google_wallet_object_id__isnull=True) | Q(google_wallet_object_id='')
        ).count()
        both_passes = CrushProfile.objects.exclude(
            Q(apple_pass_serial__isnull=True) | Q(apple_pass_serial='')
        ).exclude(
            Q(google_wallet_object_id__isnull=True) | Q(google_wallet_object_id='')
        ).count()

        extra_context['wallet_stats'] = {
            'total_profiles': total_profiles,
            'apple_passes': apple_passes,
            'google_passes': google_passes,
            'both_passes': both_passes,
            'any_pass': apple_passes + google_passes - both_passes,
        }

        return super().changelist_view(request, extra_context=extra_context)


# Create a proxy model for the admin
class WalletPassProxy(CrushProfile):
    """Proxy model for Wallet Pass admin"""
    class Meta:
        proxy = True
        verbose_name = 'Wallet Pass'
        verbose_name_plural = 'Wallet Passes'