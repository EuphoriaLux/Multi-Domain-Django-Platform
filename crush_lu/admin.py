from django.contrib import admin
from django.contrib import messages as django_messages
from django.db import transaction
from .models import (
    CrushCoach, CrushProfile, ProfileSubmission,
    CoachSession, MeetupEvent, EventRegistration,
    EventConnection, ConnectionMessage
)


@admin.register(CrushCoach)
class CrushCoachAdmin(admin.ModelAdmin):
    list_display = ('user', 'specializations', 'is_active', 'max_active_reviews', 'created_at', 'has_dating_profile')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('created_at',)
    actions = ['deactivate_coach_allow_dating', 'deactivate_coaches', 'activate_coaches']

    def has_dating_profile(self, obj):
        """Check if this coach also has a dating profile"""
        return hasattr(obj.user, 'crushprofile')
    has_dating_profile.boolean = True
    has_dating_profile.short_description = 'Has Dating Profile'

    @admin.action(description='Deactivate coach role (allows them to date)')
    def deactivate_coach_allow_dating(self, request, queryset):
        """Deactivate coach so they can create/use dating profile"""
        deactivated = queryset.update(is_active=False)
        django_messages.success(
            request,
            f"Deactivated {deactivated} coach(es). They can now create/use dating profiles."
        )

    @admin.action(description='Deactivate selected coaches')
    def deactivate_coaches(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} coach(es)")

    @admin.action(description='Activate selected coaches')
    def activate_coaches(self, request, queryset):
        updated = queryset.update(is_active=True)
        django_messages.success(request, f"Activated {updated} coach(es)")


@admin.register(CrushProfile)
class CrushProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'age', 'gender', 'location', 'is_approved', 'is_active', 'created_at', 'is_coach')
    list_filter = ('is_approved', 'is_active', 'gender', 'created_at')
    search_fields = ('user__username', 'user__email', 'location', 'bio')
    readonly_fields = ('created_at', 'updated_at', 'approved_at')
    actions = ['promote_to_coach', 'approve_profiles', 'deactivate_profiles']
    fieldsets = (
        ('User Information', {
            'fields': ('user', 'date_of_birth', 'gender', 'phone_number', 'location')
        }),
        ('Profile Content', {
            'fields': ('bio', 'interests', 'looking_for')
        }),
        ('Photos', {
            'fields': ('photo_1', 'photo_2', 'photo_3')
        }),
        ('Privacy Settings', {
            'fields': ('show_full_name', 'show_exact_age', 'blur_photos')
        }),
        ('Status', {
            'fields': ('is_approved', 'is_active', 'approved_at')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def is_coach(self, obj):
        """Check if this user is also a coach"""
        return hasattr(obj.user, 'crushcoach')
    is_coach.boolean = True
    is_coach.short_description = 'Is Coach'

    @admin.action(description='Promote selected profiles to Crush Coach role')
    def promote_to_coach(self, request, queryset):
        """Convert dating profiles to coaches"""
        promoted_count = 0
        errors = []

        for profile in queryset:
            # Check if user is already a coach
            if hasattr(profile.user, 'crushcoach'):
                errors.append(f"{profile.user.username} is already a coach")
                continue

            try:
                with transaction.atomic():
                    # Create coach profile
                    CrushCoach.objects.create(
                        user=profile.user,
                        bio=profile.bio,  # Transfer bio from dating profile
                        is_active=True,
                        max_active_reviews=10
                    )

                    # Optionally deactivate dating profile
                    # (Comment this out if you want to allow dual roles)
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

    @admin.action(description='Approve selected profiles')
    def approve_profiles(self, request, queryset):
        from django.utils import timezone
        updated = queryset.update(is_approved=True, approved_at=timezone.now())
        django_messages.success(request, f"Approved {updated} profile(s)")

    @admin.action(description='Deactivate selected profiles')
    def deactivate_profiles(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} profile(s)")


@admin.register(ProfileSubmission)
class ProfileSubmissionAdmin(admin.ModelAdmin):
    list_display = ('profile', 'coach', 'status', 'submitted_at', 'reviewed_at')
    list_filter = ('status', 'submitted_at', 'reviewed_at')
    search_fields = ('profile__user__username', 'coach__user__username')
    readonly_fields = ('submitted_at',)
    fieldsets = (
        ('Submission Details', {
            'fields': ('profile', 'coach', 'status')
        }),
        ('Review', {
            'fields': ('coach_notes', 'feedback_to_user')
        }),
        ('Timestamps', {
            'fields': ('submitted_at', 'reviewed_at')
        }),
    )


@admin.register(CoachSession)
class CoachSessionAdmin(admin.ModelAdmin):
    list_display = ('coach', 'user', 'session_type', 'scheduled_at', 'completed_at', 'created_at')
    list_filter = ('session_type', 'scheduled_at', 'completed_at')
    search_fields = ('coach__user__username', 'user__username', 'notes')
    readonly_fields = ('created_at',)


@admin.register(MeetupEvent)
class MeetupEventAdmin(admin.ModelAdmin):
    list_display = ('title', 'event_type', 'date_time', 'location', 'max_participants', 'is_published', 'is_cancelled')
    list_filter = ('event_type', 'is_published', 'is_cancelled', 'date_time')
    search_fields = ('title', 'description', 'location', 'address')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Event Information', {
            'fields': ('title', 'description', 'event_type')
        }),
        ('Location & Timing', {
            'fields': ('location', 'address', 'date_time', 'duration_minutes')
        }),
        ('Capacity & Requirements', {
            'fields': ('max_participants', 'min_age', 'max_age')
        }),
        ('Registration', {
            'fields': ('registration_deadline', 'registration_fee')
        }),
        ('Status', {
            'fields': ('is_published', 'is_cancelled')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'status', 'payment_confirmed', 'registered_at')
    list_filter = ('status', 'payment_confirmed', 'registered_at')
    search_fields = ('user__username', 'event__title')
    readonly_fields = ('registered_at', 'updated_at')
    fieldsets = (
        ('Registration Details', {
            'fields': ('event', 'user', 'status')
        }),
        ('Additional Information', {
            'fields': ('dietary_restrictions', 'special_requests')
        }),
        ('Payment', {
            'fields': ('payment_confirmed', 'payment_date')
        }),
        ('Timestamps', {
            'fields': ('registered_at', 'updated_at')
        }),
    )


@admin.register(EventConnection)
class EventConnectionAdmin(admin.ModelAdmin):
    list_display = ('requester', 'recipient', 'event', 'status', 'is_mutual', 'assigned_coach', 'requested_at')
    list_filter = ('status', 'requested_at', 'coach_approved_at')
    search_fields = ('requester__username', 'recipient__username', 'event__title')
    readonly_fields = ('requested_at', 'responded_at', 'coach_approved_at', 'shared_at', 'is_mutual')
    fieldsets = (
        ('Connection Details', {
            'fields': ('requester', 'recipient', 'event', 'status', 'is_mutual')
        }),
        ('Requester Info', {
            'fields': ('requester_note', 'requester_consents_to_share')
        }),
        ('Recipient Info', {
            'fields': ('recipient_consents_to_share',)
        }),
        ('Coach Facilitation', {
            'fields': ('assigned_coach', 'coach_notes', 'coach_introduction')
        }),
        ('Timestamps', {
            'fields': ('requested_at', 'responded_at', 'coach_approved_at', 'shared_at')
        }),
    )

    def is_mutual(self, obj):
        return obj.is_mutual
    is_mutual.boolean = True
    is_mutual.short_description = 'Mutual'


@admin.register(ConnectionMessage)
class ConnectionMessageAdmin(admin.ModelAdmin):
    list_display = ('connection', 'sender', 'is_coach_message', 'coach_approved', 'sent_at')
    list_filter = ('is_coach_message', 'coach_approved', 'sent_at')
    search_fields = ('sender__username', 'message', 'connection__event__title')
    readonly_fields = ('sent_at', 'read_at')
    fieldsets = (
        ('Message Details', {
            'fields': ('connection', 'sender', 'message')
        }),
        ('Moderation', {
            'fields': ('is_coach_message', 'coach_approved')
        }),
        ('Timestamps', {
            'fields': ('sent_at', 'read_at')
        }),
    )
