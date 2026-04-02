from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from crush_lu.models import ReferralCode, ReferralAttribution


class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'get_referrer_link',
        'is_active',
        'created_at',
        'last_used_at',
        'get_referral_counts',
    )
    list_filter = ('is_active', 'created_at', 'last_used_at')
    search_fields = ('code', 'referrer__user__email', 'referrer__user__username')
    readonly_fields = ('created_at', 'last_used_at', 'get_referral_counts', 'get_referrer_link')
    fields = (
        'code',
        'referrer',
        'is_active',
        'created_at',
        'last_used_at',
        'get_referral_counts',
    )

    def get_referrer_link(self, obj):
        profile_url = reverse('crush_admin:crush_lu_crushprofile_change', args=[obj.referrer.pk])
        return format_html(
            '<a href="{}">{}</a>',
            profile_url,
            obj.referrer.user.email
        )
    get_referrer_link.short_description = 'Referrer'

    def get_referral_counts(self, obj):
        converted = obj.attributions.filter(status=ReferralAttribution.Status.CONVERTED).count()
        pending = obj.attributions.filter(status=ReferralAttribution.Status.PENDING).count()
        return f"{converted} converted / {pending} pending"
    get_referral_counts.short_description = 'Referrals'


class ReferralAttributionAdmin(admin.ModelAdmin):
    list_display = (
        'referral_code',
        'get_referrer_email',
        'referred_user',
        'status',
        'created_at',
        'converted_at',
    )
    list_filter = ('status', 'created_at', 'converted_at')
    search_fields = (
        'referral_code__code',
        'referrer__user__email',
        'referred_user__email',
    )
    readonly_fields = ('created_at', 'converted_at')

    def get_referrer_email(self, obj):
        return obj.referrer.user.email
    get_referrer_email.short_description = 'Referrer'
