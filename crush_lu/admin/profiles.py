"""
Profile-related admin classes for Crush.lu Coach Panel.

Includes:
- CrushCoachAdmin
- CrushProfileAdmin
- ProfileSubmissionAdmin
- CoachSessionAdmin
"""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.db import transaction
from django.db.models import Prefetch, Count, Q, F
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from datetime import timedelta

from crush_lu.models import (
    CrushCoach, CrushProfile, ProfileSubmission, CoachSession,
    EventRegistration, EventConnection,
    SpecialUserExperience, JourneyConfiguration, JourneyProgress,
    ReferralCode, ReferralAttribution,
)
from .filters import (
    ReviewTimeFilter, SubmissionWorkflowFilter, CoachAssignmentFilter,
    PhoneVerificationFilter, AgeRangeFilter, LastLoginFilter,
    DaysSinceSignupFilter, DaysPendingApprovalFilter,
    ProfileCompletenessFilter, EventParticipationFilter,
    # New production-informed filters
    EmailVerificationStatusFilter, PrivacySettingsFilter,
    ProfileSubmissionDetailFilter, ConnectionActivityFilter,
)


class CrushCoachAdmin(admin.ModelAdmin):
    list_display = ('get_user_link', 'get_email', 'get_photo_preview', 'specializations', 'is_active', 'max_active_reviews', 'created_at', 'has_dating_profile')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('created_at', 'get_photo_preview')
    actions = ['deactivate_coach_allow_dating', 'deactivate_coaches', 'activate_coaches']
    fieldsets = (
        ('Coach Information', {
            'fields': ('user', 'bio', 'specializations')
        }),
        ('Photo', {
            'fields': ('photo', 'get_photo_preview'),
            'description': _('Upload a profile photo for this coach')
        }),
        ('Settings', {
            'fields': ('is_active', 'max_active_reviews')
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    def get_user_link(self, obj):
        """Display username with dual navigation links to Coach profile and User record"""
        coach_url = reverse('crush_admin:crush_lu_crushcoach_change', args=[obj.pk])
        user_url = reverse('crush_admin:auth_user_change', args=[obj.user.pk])
        status = '🟢' if obj.is_active else '🔴'

        return format_html(
            '<strong>{}</strong> {}<br>'
            '<a href="{}" style="color: #9B59B6; font-size: 11px;" title="View/Edit Coach Profile">🎓 Coach</a> '
            '<span style="color: #ccc;">|</span> '
            '<a href="{}" style="color: #666; font-size: 11px;" title="View Django User record">👤 User</a>',
            obj.user.username,
            status,
            coach_url,
            user_url
        )
    get_user_link.short_description = _('User / Links')
    get_user_link.admin_order_field = 'user__username'

    def get_email(self, obj):
        """Display coach's email address"""
        return obj.user.email
    get_email.short_description = _('Email')
    get_email.admin_order_field = 'user__email'

    def has_dating_profile(self, obj):
        """Check if this coach also has a dating profile"""
        return hasattr(obj.user, 'crushprofile')
    has_dating_profile.boolean = True
    has_dating_profile.short_description = _('Has Dating Profile')

    def get_photo_preview(self, obj):
        """Display coach photo thumbnail"""
        if obj.photo:
            return format_html(
                '<img src="{}" style="width: 50px; height: 50px; border-radius: 50%; object-fit: cover;" />',
                obj.photo.url
            )
        return format_html('<span style="color: #999;">No photo</span>')
    get_photo_preview.short_description = _('Photo')

    @admin.action(description=_('Deactivate coach role (allows them to date)'))
    def deactivate_coach_allow_dating(self, request, queryset):
        """Deactivate coach so they can create/use dating profile"""
        deactivated = queryset.update(is_active=False)
        django_messages.success(
            request,
            f"Deactivated {deactivated} coach(es). They can now create/use dating profiles."
        )

    @admin.action(description=_('Deactivate selected coaches'))
    def deactivate_coaches(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} coach(es)")

    @admin.action(description=_('Activate selected coaches'))
    def activate_coaches(self, request, queryset):
        updated = queryset.update(is_active=True)
        django_messages.success(request, f"Activated {updated} coach(es)")

    def get_queryset(self, request):
        """Optimize queries with select_related for user FK"""
        qs = super().get_queryset(request)
        return qs.select_related('user')


class ProfileSubmissionProfileInline(admin.TabularInline):
    """Show profile submission/review history"""
    model = ProfileSubmission
    extra = 0
    fields = ('coach', 'status', 'review_call_completed', 'submitted_at', 'reviewed_at')
    readonly_fields = ('submitted_at', 'reviewed_at')
    can_delete = False
    show_change_link = True
    verbose_name = "Review Submission"
    verbose_name_plural = "Review History"


class CrushProfileAdmin(admin.ModelAdmin):
    list_display = ('get_user_link', 'get_email', 'age', 'gender', 'location', 'get_language_display', 'phone_verified_icon', 'get_consent_status', 'completion_status', 'get_assigned_coach', 'get_referral_code', 'get_referral_count', 'is_approved', 'is_active', 'outlook_synced', 'created_at', 'is_coach')

    def save_model(self, request, obj, form, change):
        """
        Override save_model to allow phone verification field changes in admin.
        This bypasses the model's protection to allow manual corrections for testing.
        """
        if change:
            # Get the old instance to check if we're modifying phone verification fields
            old_instance = CrushProfile.objects.get(pk=obj.pk)

            # Check if any phone-related fields are being changed
            phone_fields_changed = any(
                field in form.changed_data
                for field in ['phone_number', 'phone_verified', 'phone_verified_at', 'phone_verification_uid']
            )

            if old_instance.phone_verified and phone_fields_changed:
                # Admin is explicitly changing phone fields - bypass model protection
                # Save directly to database without triggering model's save() override
                super(CrushProfile, obj).save(update_fields=form.changed_data)
                return

        # Normal save for all other cases
        super().save_model(request, obj, form, change)

    list_filter = (
        # Approval & Status
        'is_approved',
        'is_active',

        # NEW: Email & Privacy Filters (Production-Informed Priorities)
        EmailVerificationStatusFilter,  # Priority 1: 58% unverified
        PrivacySettingsFilter,           # Priority 2: 95% name privacy

        # User Verification
        PhoneVerificationFilter,

        # User Segmentation
        AgeRangeFilter,
        LastLoginFilter,
        DaysSinceSignupFilter,
        ProfileCompletenessFilter,
        EventParticipationFilter,

        # NEW: Engagement Filters
        ConnectionActivityFilter,        # Priority 4: Engagement tracking
        ProfileSubmissionDetailFilter,   # Priority 3: 57% never submitted

        # Direct Fields
        'gender',
        'completion_status',
        CoachAssignmentFilter,
        'preferred_language',
        'looking_for',
        'created_at'
    )
    search_fields = ('user__username', 'user__email', 'location', 'bio', 'phone_number')
    ordering = ['-created_at']  # Most recent profiles first
    readonly_fields = (
        'get_quick_status_summary',
        'get_user_account_info',
        'created_at', 'updated_at', 'approved_at',
        'get_assigned_coach',
        'phone_verified_at', 'phone_verification_uid',
        'get_event_registrations',
        'get_connections_summary',
        'get_referral_code',
        'get_referral_count',
        'get_journey_progress',
        'outlook_contact_id',
    )
    actions = ['promote_to_coach', 'approve_profiles', 'deactivate_profiles', 'reset_phone_verification', 'sync_to_outlook', 'export_profiles_csv', 'send_bulk_email']
    inlines = [ProfileSubmissionProfileInline]
    change_list_template = 'admin/crush_lu/crushprofile/change_list.html'
    fieldsets = (
        ('Quick Status', {
            'fields': ('get_quick_status_summary',),
            'description': _('At-a-glance status overview'),
        }),
        ('User Account Information', {
            'fields': ('get_user_account_info',),
            'description': _('Django User account details with link to full user record'),
        }),
        ('Profile Basics', {
            'fields': ('user', 'date_of_birth', 'gender', 'phone_number', 'location', 'preferred_language'),
            'description': _('Core profile information'),
        }),
        ('Phone Verification', {
            'fields': ('phone_verified', 'phone_verified_at', 'phone_verification_uid'),
            'description': _('Phone verification via Firebase/Google Identity Platform SMS OTP'),
        }),
        ('Profile Content', {
            'fields': ('bio', 'interests', 'looking_for'),
        }),
        ('Photos', {
            'fields': ('photo_1', 'photo_2', 'photo_3'),
        }),
        ('Privacy Settings', {
            'fields': ('show_full_name', 'show_exact_age', 'blur_photos'),
        }),
        ('Coach Assignment', {
            'fields': ('get_assigned_coach',),
            'description': _('View which coach is assigned to review this profile.'),
        }),
        ('Profile Completion', {
            'fields': ('completion_status',),
            'classes': ('collapse',),
            'description': _('Track which step of profile creation user completed'),
        }),
        ('Status', {
            'fields': ('is_approved', 'is_active', 'approved_at'),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
        ('Event Registrations', {
            'fields': ('get_event_registrations',),
            'description': _('User event history and registrations'),
        }),
        ('Connections', {
            'fields': ('get_connections_summary',),
            'description': _('Connections sent and received from events'),
        }),
        ('Referrals', {
            'fields': ('get_referral_code', 'get_referral_count'),
            'description': _('Referral code and conversions for this profile'),
        }),
        ('Journey Progress', {
            'fields': ('get_journey_progress',),
            'description': _('Interactive journey experiences (Wonderland, etc.)'),
        }),
        ('Outlook Contact Sync', {
            'fields': ('outlook_contact_id',),
            'classes': ('collapse',),
            'description': _('Microsoft Graph contact sync for caller ID'),
        }),
    )

    def changelist_view(self, request, extra_context=None):
        """Add filter counts for quick filter tabs with mutually exclusive categories"""
        from allauth.account.models import EmailAddress
        from django.db.models import Exists, OuterRef

        extra_context = extra_context or {}

        # Mutually exclusive categories for clearer understanding
        total = CrushProfile.objects.count()
        approved = CrushProfile.objects.filter(is_approved=True).count()

        # Incomplete: Not started OR partially filled (step1, step2, step3)
        incomplete_statuses = ['not_started', 'step1', 'step2', 'step3']
        incomplete_qs = CrushProfile.objects.filter(
            completion_status__in=incomplete_statuses
        )
        incomplete = incomplete_qs.count()

        # Incomplete with phone verified (users who verified phone but didn't finish profile)
        incomplete_verified = incomplete_qs.filter(phone_verified=True).count()

        # Incomplete without phone verified
        incomplete_unverified = incomplete_qs.filter(phone_verified=False).count()

        # Awaiting Review: Profile completed/submitted but not yet approved
        awaiting_review = CrushProfile.objects.filter(
            is_approved=False,
            completion_status__in=['completed', 'submitted']
        ).count()

        # NEW: Email verification count (Priority 1)
        unverified_email = CrushProfile.objects.filter(
            ~Exists(
                EmailAddress.objects.filter(
                    user_id=OuterRef('user_id'),
                    verified=True
                )
            )
        ).count()

        # NEW: Privacy-conscious users (Priority 2)
        high_privacy = CrushProfile.objects.filter(
            show_full_name=False,
            show_exact_age=False,
            blur_photos=True
        ).count()

        name_privacy = CrushProfile.objects.filter(
            show_full_name=False
        ).count()

        # NEW: Never submitted profiles (Priority 3)
        never_submitted = CrushProfile.objects.filter(
            ~Exists(
                ProfileSubmission.objects.filter(
                    profile_id=OuterRef('id')
                )
            )
        ).count()

        # NEW: No connections (Priority 4)
        from crush_lu.models import EventConnection
        no_connections = CrushProfile.objects.filter(
            ~Exists(
                EventConnection.objects.filter(
                    Q(requester__crushprofile__id=OuterRef('id')) |
                    Q(recipient__crushprofile__id=OuterRef('id'))
                )
            )
        ).count()

        # NEW: Users without profiles (signed in but never started profile creation)
        from django.contrib.auth.models import User
        users_without_profile = User.objects.filter(
            ~Exists(
                CrushProfile.objects.filter(
                    user_id=OuterRef('id')
                )
            )
        ).count()

        extra_context['filter_counts'] = {
            'total': total,
            'approved': approved,
            'awaiting_review': awaiting_review,
            'incomplete': incomplete,
            'incomplete_verified': incomplete_verified,
            'incomplete_unverified': incomplete_unverified,
            # New production-informed counts
            'unverified_email': unverified_email,
            'high_privacy': high_privacy,
            'name_privacy': name_privacy,
            'never_submitted': never_submitted,
            'no_connections': no_connections,
            'users_without_profile': users_without_profile,
        }

        return super().changelist_view(request, extra_context=extra_context)

    def get_user_link(self, obj):
        """Display username with dual navigation links to Profile and User"""
        profile_url = reverse('crush_admin:crush_lu_crushprofile_change', args=[obj.pk])
        user_url = reverse('crush_admin:auth_user_change', args=[obj.user.pk])
        status = '✅' if obj.is_approved else '⏳'

        return format_html(
            '<strong>{}</strong> {}<br>'
            '<a href="{}" style="color: #9B59B6; font-size: 11px;" title="View/Edit CrushProfile">💕 Profile</a> '
            '<span style="color: #ccc;">|</span> '
            '<a href="{}" style="color: #666; font-size: 11px;" title="View Django User record">👤 User</a>',
            obj.user.username,
            status,
            profile_url,
            user_url
        )
    get_user_link.short_description = _('User / Links')
    get_user_link.admin_order_field = 'user__username'

    def get_email(self, obj):
        """Display user's email address"""
        return obj.user.email
    get_email.short_description = _('Email')
    get_email.admin_order_field = 'user__email'

    def phone_verified_icon(self, obj):
        """Display phone verification status with icon"""
        if obj.phone_verified:
            return format_html('<span style="color: green;" title="Phone verified">✅</span>')
        else:
            return format_html('<span style="color: red;" title="Phone not verified">❌</span>')
    phone_verified_icon.short_description = _('Phone')
    phone_verified_icon.admin_order_field = 'phone_verified'

    def get_language_display(self, obj):
        """Display user's preferred language with flag"""
        flags = {'en': '🇬🇧', 'de': '🇩🇪', 'fr': '🇫🇷'}
        flag = flags.get(obj.preferred_language, '')
        lang_name = obj.get_preferred_language_display() if hasattr(obj, 'get_preferred_language_display') else obj.preferred_language
        return format_html('{} {}', flag, lang_name)
    get_language_display.short_description = _('Lang')
    get_language_display.admin_order_field = 'preferred_language'

    def is_coach(self, obj):
        """Check if this user is also a coach"""
        return hasattr(obj.user, 'crushcoach')
    is_coach.boolean = True
    is_coach.short_description = _('Is Coach')

    def get_consent_status(self, obj):
        """Display consent status icons for Crush.lu"""
        if not hasattr(obj.user, 'data_consent'):
            return format_html('<span style="color: red;">❌ No consent</span>')

        consent = obj.user.data_consent
        crushlu_icon = '✅' if consent.crushlu_consent_given else '❌'
        ban_icon = ' 🚫' if consent.crushlu_banned else ''

        return format_html(
            '<span title="Crush.lu: {}{}">Crush:{}{}</span>',
            'Given' if consent.crushlu_consent_given else 'Not given',
            ' (BANNED)' if consent.crushlu_banned else '',
            crushlu_icon,
            ban_icon
        )
    get_consent_status.short_description = _('📋 Consent')

    def outlook_synced(self, obj):
        """Display Outlook contact sync status with icon"""
        if obj.outlook_contact_id:
            return format_html('<span style="color: green;" title="Synced to Outlook">📇</span>')
        elif obj.phone_number:
            return format_html('<span style="color: orange;" title="Not synced (has phone)">⏳</span>')
        else:
            return format_html('<span style="color: gray;" title="No phone number">—</span>')
    outlook_synced.short_description = _('Outlook')
    outlook_synced.admin_order_field = 'outlook_contact_id'

    def get_assigned_coach(self, obj):
        """Display the assigned coach from ProfileSubmission with clickable link"""
        submissions = getattr(obj, '_prefetched_submissions', None)
        if submissions is not None:
            submission = submissions[0] if submissions else None
        else:
            submission = ProfileSubmission.objects.filter(profile=obj).select_related('coach__user').first()

        if submission is None:
            return format_html('<em style="color: #999;">Not submitted yet</em>')

        if submission.coach:
            coach_url = reverse('crush_admin:crush_lu_crushcoach_change', args=[submission.coach.pk])
            status_colors = {
                'pending': '#ffc107',
                'approved': '#28a745',
                'rejected': '#dc3545',
                'revision': '#17a2b8',
            }
            status_color = status_colors.get(submission.status, '#666')
            return format_html(
                '<a href="{}" style="color: #9B59B6; font-weight: bold;">{}</a> '
                '<span style="color: {};">({}) </span>',
                coach_url,
                submission.coach.user.get_full_name() or submission.coach.user.username,
                status_color,
                submission.get_status_display()
            )
        else:
            return format_html('<em style="color: #dc3545;">No coach assigned</em>')
    get_assigned_coach.short_description = _('Assigned Coach')

    def get_event_registrations(self, obj):
        """Display event registrations for this user as HTML table"""
        registrations = EventRegistration.objects.filter(user=obj.user).select_related('event').order_by('-registered_at')[:10]
        if not registrations:
            return format_html('<em style="color: #999;">No event registrations yet</em>')

        html = '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">'
        html += '<tr style="background: #f5f5f5;"><th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Event</th>'
        html += '<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Status</th>'
        html += '<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Date</th>'
        html += '<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Actions</th></tr>'

        for reg in registrations:
            status_colors = {
                'confirmed': '#28a745',
                'pending': '#ffc107',
                'waitlist': '#17a2b8',
                'cancelled': '#dc3545',
                'attended': '#28a745',
                'no_show': '#dc3545',
            }
            status_color = status_colors.get(reg.status, '#666')
            reg_url = reverse('crush_admin:crush_lu_eventregistration_change', args=[reg.pk])
            event_url = reverse('crush_admin:crush_lu_meetupevent_change', args=[reg.event.pk])

            html += f'<tr>'
            html += f'<td style="padding: 8px; border: 1px solid #ddd;"><a href="{event_url}">{reg.event.title}</a></td>'
            html += f'<td style="padding: 8px; border: 1px solid #ddd;"><span style="color: {status_color}; font-weight: bold;">{reg.get_status_display()}</span></td>'
            html += f'<td style="padding: 8px; border: 1px solid #ddd;">{reg.registered_at.strftime("%Y-%m-%d")}</td>'
            html += f'<td style="padding: 8px; border: 1px solid #ddd;"><a href="{reg_url}">View</a></td>'
            html += '</tr>'

        html += '</table>'

        total = EventRegistration.objects.filter(user=obj.user).count()
        if total > 10:
            html += f'<p style="color: #666; font-size: 12px; margin-top: 8px;">Showing 10 of {total} registrations</p>'

        return format_html(html)
    get_event_registrations.short_description = _('Event Registrations')

    def get_connections_summary(self, obj):
        """Display connections sent/received as HTML table"""
        sent = EventConnection.objects.filter(requester=obj.user).select_related('recipient', 'event').order_by('-requested_at')[:5]
        received = EventConnection.objects.filter(recipient=obj.user).select_related('requester', 'event').order_by('-requested_at')[:5]

        if not sent.exists() and not received.exists():
            return format_html('<em style="color: #999;">No connections yet</em>')

        html = ''

        if sent:
            html += '<strong style="color: #9B59B6;">Connections Sent:</strong>'
            html += '<table style="width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 15px;">'
            html += '<tr style="background: #f5f5f5;"><th style="padding: 6px; text-align: left; border: 1px solid #ddd;">To</th>'
            html += '<th style="padding: 6px; text-align: left; border: 1px solid #ddd;">Event</th>'
            html += '<th style="padding: 6px; text-align: left; border: 1px solid #ddd;">Status</th></tr>'

            for conn in sent:
                status_colors = {'pending': '#ffc107', 'accepted': '#28a745', 'declined': '#dc3545', 'shared': '#9B59B6'}
                status_color = status_colors.get(conn.status, '#666')
                conn_url = reverse('crush_admin:crush_lu_eventconnection_change', args=[conn.pk])

                html += f'<tr>'
                html += f'<td style="padding: 6px; border: 1px solid #ddd;">{conn.recipient.get_full_name() or conn.recipient.username}</td>'
                html += f'<td style="padding: 6px; border: 1px solid #ddd;">{conn.event.title}</td>'
                html += f'<td style="padding: 6px; border: 1px solid #ddd;"><a href="{conn_url}" style="color: {status_color};">{conn.get_status_display()}</a></td>'
                html += '</tr>'
            html += '</table>'

        if received:
            html += '<strong style="color: #FF6B9D;">Connections Received:</strong>'
            html += '<table style="width: 100%; border-collapse: collapse; font-size: 13px;">'
            html += '<tr style="background: #f5f5f5;"><th style="padding: 6px; text-align: left; border: 1px solid #ddd;">From</th>'
            html += '<th style="padding: 6px; text-align: left; border: 1px solid #ddd;">Event</th>'
            html += '<th style="padding: 6px; text-align: left; border: 1px solid #ddd;">Status</th></tr>'

            for conn in received:
                status_colors = {'pending': '#ffc107', 'accepted': '#28a745', 'declined': '#dc3545', 'shared': '#9B59B6'}
                status_color = status_colors.get(conn.status, '#666')
                conn_url = reverse('crush_admin:crush_lu_eventconnection_change', args=[conn.pk])

                html += f'<tr>'
                html += f'<td style="padding: 6px; border: 1px solid #ddd;">{conn.requester.get_full_name() or conn.requester.username}</td>'
                html += f'<td style="padding: 6px; border: 1px solid #ddd;">{conn.event.title}</td>'
                html += f'<td style="padding: 6px; border: 1px solid #ddd;"><a href="{conn_url}" style="color: {status_color};">{conn.get_status_display()}</a></td>'
                html += '</tr>'
            html += '</table>'

        return format_html(html)
    get_connections_summary.short_description = _('Connections')

    def get_referral_code(self, obj):
        code = ReferralCode.objects.filter(referrer=obj, is_active=True).order_by('-created_at').first()
        return code.code if code else '—'
    get_referral_code.short_description = _('Referral Code')

    def get_referral_count(self, obj):
        converted = ReferralAttribution.objects.filter(
            referrer=obj,
            status=ReferralAttribution.Status.CONVERTED
        ).count()
        pending = ReferralAttribution.objects.filter(
            referrer=obj,
            status=ReferralAttribution.Status.PENDING
        ).count()
        return f"{converted} converted / {pending} pending"
    get_referral_count.short_description = _('Referrals')

    def get_user_account_info(self, obj):
        """Display comprehensive User account information as HTML block"""
        user = obj.user
        user_url = reverse('crush_admin:auth_user_change', args=[user.pk])

        date_joined = user.date_joined.strftime('%Y-%m-%d %H:%M') if user.date_joined else 'N/A'
        last_login = user.last_login.strftime('%Y-%m-%d %H:%M') if user.last_login else 'Never'
        user_status = '🟢 Active' if user.is_active else '🔴 Inactive'

        # Get consent information
        consent_html = ''
        if hasattr(user, 'data_consent'):
            consent = user.data_consent

            # PowerUp consent
            if consent.powerup_consent_given:
                powerup_date = consent.powerup_consent_date.strftime('%Y-%m-%d %H:%M') if consent.powerup_consent_date else 'Unknown'
                powerup_ip = f" from {consent.powerup_consent_ip}" if consent.powerup_consent_ip else ""
                powerup_status = f'<span style="color: green;">✅ Given on {powerup_date}{powerup_ip}</span>'
            else:
                powerup_status = '<span style="color: red;">❌ Not given</span>'

            # Crush.lu consent
            if consent.crushlu_consent_given:
                crushlu_date = consent.crushlu_consent_date.strftime('%Y-%m-%d %H:%M') if consent.crushlu_consent_date else 'Unknown'
                crushlu_ip = f" from {consent.crushlu_consent_ip}" if consent.crushlu_consent_ip else ""
                crushlu_status = f'<span style="color: green;">✅ Given on {crushlu_date}{crushlu_ip}</span>'
            else:
                crushlu_status = '<span style="color: red;">❌ Not given</span>'

            # Ban status
            if consent.crushlu_banned:
                reason_map = {
                    'user_deletion': 'User deleted profile',
                    'admin_action': 'Admin action',
                    'terms_violation': 'Terms violation',
                }
                reason = reason_map.get(consent.crushlu_ban_reason, consent.crushlu_ban_reason or 'Unknown')
                ban_date = consent.crushlu_ban_date.strftime('%Y-%m-%d') if consent.crushlu_ban_date else 'Unknown'
                ban_status = f'<span style="color: red; font-weight: bold;">🚫 BANNED since {ban_date} ({reason})</span>'
            else:
                ban_status = '<span style="color: green;">✅ Not banned</span>'

            consent_html = f'''
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;"><strong>PowerUp Consent:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{powerup_status}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;"><strong>Crush.lu Consent:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{crushlu_status}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;"><strong>Ban Status:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{ban_status}</td>
                </tr>
            '''
        else:
            consent_html = '''
                <tr>
                    <td colspan="2" style="padding: 6px 12px; border-bottom: 1px solid #eee; background: #fff3cd;">
                        <span style="color: #856404;">⚠️ No consent record found for this user</span>
                    </td>
                </tr>
            '''

        html = f'''
        <div style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 15px; margin-bottom: 10px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h4 style="margin: 0; color: #9B59B6;">👤 Django User Account</h4>
                <a href="{user_url}" style="background: linear-gradient(90deg, #9B59B6, #FF6B9D); color: white; padding: 8px 16px; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: bold;">
                    View Full User Record →
                </a>
            </div>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee; width: 30%;"><strong>Username:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{user.username}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;"><strong>Email:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{user.email}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;"><strong>Full Name:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{user.first_name} {user.last_name}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;"><strong>Account Status:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{user_status}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;"><strong>Date Joined:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{date_joined}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;"><strong>Last Login:</strong></td>
                    <td style="padding: 6px 12px; border-bottom: 1px solid #eee;">{last_login}</td>
                </tr>
                {consent_html}
            </table>
        </div>
        '''
        return format_html(html)
    get_user_account_info.short_description = _('User Account Details')

    def get_journey_progress(self, obj):
        """Display journey progress for this user as HTML"""
        user = obj.user

        special_exp = SpecialUserExperience.objects.filter(
            first_name__iexact=user.first_name,
            last_name__iexact=user.last_name,
            is_active=True
        ).first()

        if not special_exp:
            return format_html('<em style="color: #999;">No special experience configured for this user</em>')

        journeys = JourneyConfiguration.objects.filter(
            special_experience=special_exp
        ).prefetch_related('chapters')

        if not journeys.exists():
            exp_url = reverse('crush_admin:crush_lu_specialuserexperience_change', args=[special_exp.pk])
            return format_html(
                '<div style="background: #fff3cd; padding: 10px; border-radius: 4px;">'
                '⚠️ Special Experience exists but no journeys configured. '
                '<a href="{}">Configure Journey →</a></div>',
                exp_url
            )

        html = '<div style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 15px;">'
        html += '<h4 style="margin: 0 0 15px 0; color: #9B59B6;">🗺️ Journey Progress</h4>'

        for journey in journeys:
            journey_url = reverse('crush_admin:crush_lu_journeyconfiguration_change', args=[journey.pk])

            progress = JourneyProgress.objects.filter(
                user=user,
                journey=journey
            ).first()

            html += f'<div style="border: 1px solid #ddd; border-radius: 6px; padding: 12px; margin-bottom: 10px; background: white;">'
            html += f'<div style="display: flex; justify-content: space-between; align-items: center;">'
            html += f'<strong><a href="{journey_url}" style="color: #9B59B6;">{journey.journey_name}</a></strong>'

            if progress:
                if progress.is_completed:
                    status_badge = '<span style="background: #28a745; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px;">✅ Completed</span>'
                else:
                    status_badge = f'<span style="background: #17a2b8; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px;">🔄 Chapter {progress.current_chapter}/{journey.total_chapters}</span>'
                html += f'{status_badge}'
            else:
                html += '<span style="background: #6c757d; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px;">Not Started</span>'

            html += '</div>'

            if progress:
                progress_url = reverse('crush_admin:crush_lu_journeyprogress_change', args=[progress.pk])
                completion_pct = progress.completion_percentage

                html += f'''
                <div style="margin-top: 10px;">
                    <div style="background: #e9ecef; border-radius: 4px; height: 8px; overflow: hidden;">
                        <div style="background: linear-gradient(90deg, #9B59B6, #FF6B9D); width: {completion_pct}%; height: 100%;"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-top: 6px; font-size: 12px; color: #666;">
                        <span>📊 {completion_pct}% Complete</span>
                        <span>🏆 {progress.total_points} points</span>
                        <span>⏱️ {progress.total_time_seconds // 60} min</span>
                        <a href="{progress_url}" style="color: #9B59B6;">View Details →</a>
                    </div>
                </div>
                '''

                if progress.final_response:
                    response_display = '💫 Yes!' if progress.final_response == 'yes' else '✨ Thinking...'
                    html += f'<div style="margin-top: 8px; padding: 6px 10px; background: #f0e6f7; border-radius: 4px; font-size: 12px;"><strong>Final Response:</strong> {response_display}</div>'

            html += '</div>'

        html += '</div>'
        return format_html(html)
    get_journey_progress.short_description = _('Journey Progress')

    def get_quick_status_summary(self, obj):
        """Display a quick visual status summary at the top"""
        if obj.is_approved:
            profile_status = '✅ Approved'
            profile_color = '#28a745'
        else:
            profile_status = '⏳ Pending Approval'
            profile_color = '#ffc107'

        completion_map = {
            'not_started': ('🔴', 'Not Started'),
            'step1': ('🟡', 'Step 1'),
            'step2': ('🟠', 'Step 2'),
            'step3': ('🟢', 'Step 3'),
            'complete': ('✅', 'Complete'),
        }
        comp_icon, comp_text = completion_map.get(obj.completion_status, ('❓', obj.completion_status or 'Unknown'))

        phone_status = '✅ Verified' if obj.phone_verified else '❌ Not Verified'
        phone_color = '#28a745' if obj.phone_verified else '#dc3545'

        active_status = '🟢 Active' if obj.is_active else '🔴 Inactive'

        html = f'''
        <div style="display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 15px;">
            <div style="background: {profile_color}22; border: 1px solid {profile_color}; border-radius: 6px; padding: 8px 15px;">
                <span style="color: {profile_color}; font-weight: bold;">{profile_status}</span>
            </div>
            <div style="background: #f8f9fa; border: 1px solid #ddd; border-radius: 6px; padding: 8px 15px;">
                <span>{comp_icon} Profile: {comp_text}</span>
            </div>
            <div style="background: {phone_color}22; border: 1px solid {phone_color}; border-radius: 6px; padding: 8px 15px;">
                <span style="color: {phone_color};">📱 {phone_status}</span>
            </div>
            <div style="background: #f8f9fa; border: 1px solid #ddd; border-radius: 6px; padding: 8px 15px;">
                <span>{active_status}</span>
            </div>
        </div>
        '''
        return format_html(html)
    get_quick_status_summary.short_description = _('Status Summary')

    @admin.action(description=_('Promote selected profiles to Crush Coach role'))
    def promote_to_coach(self, request, queryset):
        """Convert dating profiles to coaches"""
        promoted_count = 0
        errors = []

        for profile in queryset:
            if hasattr(profile.user, 'crushcoach'):
                errors.append(f"{profile.user.username} is already a coach")
                continue

            try:
                with transaction.atomic():
                    CrushCoach.objects.create(
                        user=profile.user,
                        bio=profile.bio,
                        is_active=True,
                        max_active_reviews=10
                    )

                    profile.is_active = False
                    profile.save()

                    promoted_count += 1

            except Exception as e:
                errors.append(f"{profile.user.username}: {str(e)}")

        if promoted_count > 0:
            django_messages.success(
                request,
                f"Successfully promoted {promoted_count} profile(s) to Crush Coach. "
                f"Their dating profiles have been deactivated."
            )

        for error in errors:
            django_messages.error(request, error)

    @admin.action(description=_('Approve selected profiles'))
    def approve_profiles(self, request, queryset):
        updated = queryset.update(is_approved=True, approved_at=timezone.now())
        django_messages.success(request, f"Approved {updated} profile(s)")

    @admin.action(description=_('Deactivate selected profiles'))
    def deactivate_profiles(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} profile(s)")

    @admin.action(description=_('Reset phone verification (allows re-verification)'))
    def reset_phone_verification(self, request, queryset):
        """Reset phone verification for selected profiles."""
        reset_count = 0
        for profile in queryset:
            if profile.phone_verified:
                profile.reset_phone_verification()
                reset_count += 1

        if reset_count > 0:
            django_messages.success(
                request,
                f"Reset phone verification for {reset_count} profile(s). "
                f"Users can now verify a new phone number."
            )
        else:
            django_messages.warning(request, "No profiles had phone verification to reset.")

    @admin.action(description=_('Sync selected profiles to Outlook contacts'))
    def sync_to_outlook(self, request, queryset):
        """Sync selected profiles to Outlook contacts via Microsoft Graph API."""
        from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled

        if not is_sync_enabled():
            django_messages.warning(
                request,
                "Outlook contact sync is disabled for this environment. "
                "Set OUTLOOK_CONTACT_SYNC_ENABLED=true in production."
            )
            return

        try:
            service = GraphContactsService()
        except Exception as e:
            django_messages.error(request, f"Failed to initialize sync service: {e}")
            return

        synced = 0
        skipped = 0
        errors = 0

        for profile in queryset.select_related('user'):
            if not profile.phone_number:
                skipped += 1
                continue

            try:
                result = service.sync_profile(profile)
                if result:
                    synced += 1
                else:
                    errors += 1
            except Exception as e:
                errors += 1

        if synced > 0:
            django_messages.success(request, f"Synced {synced} profile(s) to Outlook contacts.")
        if skipped > 0:
            django_messages.info(request, f"Skipped {skipped} profile(s) without phone numbers.")
        if errors > 0:
            django_messages.error(request, f"Failed to sync {errors} profile(s).")

    @admin.action(description=_('Export selected profiles to CSV'))
    def export_profiles_csv(self, request, queryset):
        """Export selected profiles to CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="crush_profiles_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        writer = csv.writer(response)
        writer.writerow([
            'Username', 'Email', 'First Name', 'Last Name', 'Age', 'Gender',
            'Location', 'Phone', 'Is Approved', 'Is Active', 'Approved Date',
            'Created Date', 'Completion Status',
        ])

        for profile in queryset.select_related('user'):
            writer.writerow([
                profile.user.username,
                profile.user.email,
                profile.user.first_name,
                profile.user.last_name,
                profile.age,
                profile.gender,
                profile.location,
                profile.phone_number,
                'Yes' if profile.is_approved else 'No',
                'Yes' if profile.is_active else 'No',
                profile.approved_at.strftime('%Y-%m-%d %H:%M') if profile.approved_at else 'Not yet',
                profile.created_at.strftime('%Y-%m-%d %H:%M'),
                profile.completion_status,
            ])

        django_messages.success(request, f"Exported {queryset.count()} profile(s) to CSV.")
        return response

    @admin.action(description=_('Send bulk email to selected users'))
    def send_bulk_email(self, request, queryset):
        """Redirect to bulk email composition form with selected user IDs"""
        from django.http import HttpResponseRedirect
        from django.contrib import messages
        import urllib.parse

        # Get all user emails from selected profiles
        emails = list(queryset.values_list('user__email', flat=True))

        if not emails:
            messages.warning(request, "No profiles selected for email.")
            return

        # Store selected profile IDs in session for the email form
        request.session['bulk_email_profile_ids'] = list(queryset.values_list('pk', flat=True))
        request.session['bulk_email_count'] = len(emails)

        # For now, show a summary message with instructions
        # In the future, this can redirect to a custom email composition view
        email_list = ', '.join(emails[:10])
        if len(emails) > 10:
            email_list += f'... and {len(emails) - 10} more'

        messages.info(
            request,
            f"📧 Ready to email {len(emails)} user(s). "
            f"Recipients: {email_list}. "
            f"Use the Email Preferences panel or your email client to send messages."
        )

        # Could redirect to a custom bulk email form:
        # return HttpResponseRedirect(f'/crush-admin/bulk-email/?profiles={",".join(map(str, queryset.values_list("pk", flat=True)))}')

    def get_queryset(self, request):
        """Optimize queries with select_related and prefetch_related"""
        qs = super().get_queryset(request)
        return qs.select_related('user').prefetch_related(
            Prefetch(
                'profilesubmission_set',
                queryset=ProfileSubmission.objects.select_related('coach__user'),
                to_attr='_prefetched_submissions'
            )
        )


class CallAttemptInline(admin.TabularInline):
    """Inline for viewing call attempts in profile submission"""
    from crush_lu.models import CallAttempt
    model = CallAttempt
    extra = 0
    readonly_fields = ['attempt_date', 'coach']
    fields = ['attempt_date', 'result', 'failure_reason', 'notes', 'coach']
    can_delete = False  # Preserve audit trail

    def has_add_permission(self, request, obj=None):
        return False  # Only created through coach interface


class ProfileSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        'get_profile_link', 'get_user_email', 'get_workflow_status',
        'coach', 'get_pending_time', 'status', 'review_call_completed', 'submitted_at'
    )
    list_filter = ('status', ReviewTimeFilter, SubmissionWorkflowFilter, DaysPendingApprovalFilter, 'review_call_completed', 'coach', 'submitted_at')
    search_fields = ('profile__user__username', 'profile__user__email', 'coach__user__username', 'coach_notes', 'feedback_to_user')
    readonly_fields = ('submitted_at', 'get_profile_details')
    # Quick inline editing for common workflow actions
    list_editable = ('review_call_completed', 'status')
    inlines = [CallAttemptInline]  # Show call attempts in submission detail
    actions = [
        'bulk_approve_profiles',
        'bulk_reject_profiles',
        'bulk_request_revision',
        'bulk_assign_coach',
        'bulk_mark_call_completed',
        'export_submissions_csv',
    ]
    fieldsets = (
        ('Profile Summary', {
            'fields': ('get_profile_details',),
            'description': _('Quick overview of the profile being reviewed')
        }),
        ('Submission Details', {
            'fields': ('profile', 'coach', 'status')
        }),
        ('Screening Call (During Review)', {
            'fields': ('review_call_completed', 'review_call_date', 'review_call_notes'),
            'description': _('Coach must complete screening call before approving profile')
        }),
        ('Review', {
            'fields': ('coach_notes', 'feedback_to_user')
        }),
        ('Timestamps', {
            'fields': ('submitted_at', 'reviewed_at')
        }),
    )

    def get_queryset(self, request):
        """Optimize queries with select_related for profile and coach FKs"""
        qs = super().get_queryset(request)
        return qs.select_related('profile__user', 'coach__user')

    def get_profile_link(self, obj):
        """Clickable link to the profile"""
        url = reverse('crush_admin:crush_lu_crushprofile_change', args=[obj.profile.pk])
        name = obj.profile.user.get_full_name() or obj.profile.user.username
        return format_html(
            '<a href="{}" style="color: #9B59B6; font-weight: bold;">{}</a>',
            url, name
        )
    get_profile_link.short_description = _('Profile')
    get_profile_link.admin_order_field = 'profile__user__first_name'

    def get_user_email(self, obj):
        """Display user's email"""
        return obj.profile.user.email
    get_user_email.short_description = _('Email')
    get_user_email.admin_order_field = 'profile__user__email'

    def get_workflow_status(self, obj):
        """Display visual workflow badge"""
        now = timezone.now()
        cutoff_24h = now - timedelta(hours=24)

        if obj.status == 'approved':
            return format_html(
                '<span style="background: #28a745; color: white; padding: 3px 8px; '
                'border-radius: 12px; font-size: 11px;">✅ Approved</span>'
            )
        elif obj.status == 'rejected':
            return format_html(
                '<span style="background: #dc3545; color: white; padding: 3px 8px; '
                'border-radius: 12px; font-size: 11px;">❌ Rejected</span>'
            )
        elif obj.status == 'revision':
            return format_html(
                '<span style="background: #17a2b8; color: white; padding: 3px 8px; '
                'border-radius: 12px; font-size: 11px;">🔄 Revision</span>'
            )
        elif obj.status == 'pending':
            if not obj.coach:
                return format_html(
                    '<span style="background: #dc3545; color: white; padding: 3px 8px; '
                    'border-radius: 12px; font-size: 11px;">🚨 Needs Coach</span>'
                )
            elif obj.review_call_completed:
                return format_html(
                    '<span style="background: #28a745; color: white; padding: 3px 8px; '
                    'border-radius: 12px; font-size: 11px;">✅ Ready to Approve</span>'
                )
            elif obj.submitted_at < cutoff_24h:
                return format_html(
                    '<span style="background: #ffc107; color: #333; padding: 3px 8px; '
                    'border-radius: 12px; font-size: 11px;">⚠️ Awaiting Call</span>'
                )
            else:
                return format_html(
                    '<span style="background: #17a2b8; color: white; padding: 3px 8px; '
                    'border-radius: 12px; font-size: 11px;">🆕 New</span>'
                )
        return obj.get_status_display()
    get_workflow_status.short_description = _('Workflow')

    def get_pending_time(self, obj):
        """Display how long submission has been pending with color coding"""
        if obj.status != 'pending':
            return format_html('<span style="color: #999;">-</span>')

        now = timezone.now()
        delta = now - obj.submitted_at
        days = delta.days
        hours = delta.seconds // 3600

        if days > 3:
            color = '#dc3545'
            text = f"{days} days"
        elif days > 0:
            color = '#ffc107'
            text = f"{days}d {hours}h"
        else:
            color = '#28a745'
            text = f"{hours}h"

        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, text
        )
    get_pending_time.short_description = _('Pending')
    get_pending_time.admin_order_field = 'submitted_at'

    def get_profile_details(self, obj):
        """Display comprehensive profile details for review"""
        profile = obj.profile
        user = profile.user

        lang_flags = {'en': '🇬🇧', 'de': '🇩🇪', 'fr': '🇫🇷'}
        lang_flag = lang_flags.get(profile.preferred_language, '')
        lang_name = profile.get_preferred_language_display() if hasattr(profile, 'get_preferred_language_display') else profile.preferred_language

        html = '<div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 10px;">'

        html += f'<h4 style="margin: 0 0 10px 0; color: #9B59B6;">{user.get_full_name() or user.username}</h4>'
        html += f'<p style="margin: 5px 0;"><strong>Email:</strong> {user.email}</p>'
        html += f'<p style="margin: 5px 0;"><strong>Age:</strong> {profile.age} | <strong>Gender:</strong> {profile.gender} | <strong>Language:</strong> {lang_flag} {lang_name}</p>'
        html += f'<p style="margin: 5px 0;"><strong>Location:</strong> {profile.location or "Not specified"}</p>'

        phone_status = '✅ Verified' if profile.phone_verified else '❌ Not verified'
        html += f'<p style="margin: 5px 0;"><strong>Phone:</strong> {profile.phone_number or "N/A"} ({phone_status})</p>'

        if profile.bio:
            html += f'<p style="margin: 10px 0 5px 0;"><strong>Bio:</strong></p>'
            html += f'<p style="margin: 0; padding: 10px; background: white; border-radius: 4px;">{profile.bio[:500]}{"..." if len(profile.bio) > 500 else ""}</p>'

        if profile.interests:
            html += f'<p style="margin: 10px 0 5px 0;"><strong>Interests:</strong> {profile.interests}</p>'

        if profile.looking_for:
            html += f'<p style="margin: 5px 0;"><strong>Looking for:</strong> {profile.looking_for}</p>'

        html += '</div>'

        return format_html(html)
    get_profile_details.short_description = _('Profile Details')

    @admin.action(description=_('Approve selected profiles (requires completed call)'))
    def bulk_approve_profiles(self, request, queryset):
        """Bulk approve profiles - only if screening call is completed"""
        now = timezone.now()
        approved_count = 0
        skipped_count = 0

        for submission in queryset.select_related('profile'):
            if not submission.review_call_completed:
                skipped_count += 1
                continue

            with transaction.atomic():
                submission.status = 'approved'
                submission.reviewed_at = now
                submission.save()

                submission.profile.is_approved = True
                submission.profile.approved_at = now
                submission.profile.save()
                approved_count += 1

        if approved_count > 0:
            django_messages.success(request, f"✅ Approved {approved_count} profile(s)")
        if skipped_count > 0:
            django_messages.warning(
                request,
                f"⚠️ Skipped {skipped_count} profile(s) - screening call not completed"
            )

    @admin.action(description=_('Reject selected profiles'))
    def bulk_reject_profiles(self, request, queryset):
        """Bulk reject profiles"""
        now = timezone.now()
        updated = queryset.update(status='rejected', reviewed_at=now)
        django_messages.success(request, f"Rejected {updated} profile(s)")

    @admin.action(description=_('Request revision for selected profiles'))
    def bulk_request_revision(self, request, queryset):
        """Request revision for profiles"""
        now = timezone.now()
        updated = queryset.update(status='revision', reviewed_at=now)
        django_messages.success(request, f"Requested revision for {updated} profile(s)")

    @admin.action(description=_('Auto-assign available coach'))
    def bulk_assign_coach(self, request, queryset):
        """Auto-assign coach to submissions without one"""
        assigned_count = 0
        skipped_count = 0

        available_coaches = CrushCoach.objects.filter(is_active=True).annotate(
            active_reviews=Count(
                'profilesubmission',
                filter=Q(profilesubmission__status='pending')
            )
        ).filter(active_reviews__lt=F('max_active_reviews')).order_by('active_reviews')

        if not available_coaches.exists():
            django_messages.error(request, "❌ No available coaches found")
            return

        coach_list = list(available_coaches)
        coach_index = 0

        for submission in queryset.filter(coach__isnull=True):
            coach = coach_list[coach_index % len(coach_list)]
            submission.coach = coach
            submission.save()
            assigned_count += 1
            coach_index += 1

        skipped_count = queryset.filter(coach__isnull=False).count()

        if assigned_count > 0:
            django_messages.success(request, f"👤 Assigned coach to {assigned_count} profile(s)")
        if skipped_count > 0:
            django_messages.info(request, f"ℹ️ Skipped {skipped_count} profile(s) - already have coach assigned")

    @admin.action(description=_('Mark screening call as completed'))
    def bulk_mark_call_completed(self, request, queryset):
        """Mark screening call as completed for selected submissions"""
        now = timezone.now()
        updated = queryset.update(review_call_completed=True, review_call_date=now)
        django_messages.success(request, f"Marked {updated} screening call(s) as completed")

    @admin.action(description=_('Export selected submissions to CSV'))
    def export_submissions_csv(self, request, queryset):
        """Export selected profile submissions to CSV with review details"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="profile_submissions_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        writer = csv.writer(response)
        writer.writerow([
            'Profile Name', 'Email', 'Status', 'Coach', 'Call Completed',
            'Days Pending', 'Submitted At', 'Reviewed At', 'Coach Notes', 'Feedback'
        ])

        for submission in queryset.select_related('profile__user', 'coach__user'):
            profile = submission.profile
            user = profile.user
            coach_name = submission.coach.user.get_full_name() if submission.coach else 'Unassigned'

            # Calculate days pending
            if submission.status == 'pending':
                days_pending = (timezone.now() - submission.submitted_at).days
            else:
                days_pending = '-'

            writer.writerow([
                user.get_full_name() or user.username,
                user.email,
                submission.get_status_display(),
                coach_name,
                'Yes' if submission.review_call_completed else 'No',
                days_pending,
                submission.submitted_at.strftime('%Y-%m-%d %H:%M') if submission.submitted_at else '',
                submission.reviewed_at.strftime('%Y-%m-%d %H:%M') if submission.reviewed_at else '',
                submission.coach_notes or '',
                submission.feedback_to_user or '',
            ])

        django_messages.success(request, f"Exported {queryset.count()} submission(s) to CSV.")
        return response


class CoachSessionAdmin(admin.ModelAdmin):
    list_display = ('coach', 'user', 'session_type', 'scheduled_at', 'completed_at', 'created_at')
    list_filter = ('session_type', 'scheduled_at', 'completed_at')
    search_fields = ('coach__user__username', 'user__username', 'notes')
    readonly_fields = ('created_at',)

    def get_queryset(self, request):
        """Optimize queries with select_related for coach and user FKs"""
        qs = super().get_queryset(request)
        return qs.select_related('coach__user', 'user')
