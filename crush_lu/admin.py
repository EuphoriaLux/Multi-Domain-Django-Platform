from django.contrib import admin
from django.contrib import messages as django_messages
from django.db import transaction
from django.utils.html import format_html
from .models import (
    CrushCoach, CrushProfile, ProfileSubmission,
    CoachSession, MeetupEvent, EventRegistration, EventInvitation,
    EventConnection, ConnectionMessage,
    GlobalActivityOption, EventActivityOption, EventActivityVote, EventVotingSession,
    PresentationQueue, PresentationRating, SpeedDatingPair,
    SpecialUserExperience,
    # Journey System Models
    JourneyConfiguration, JourneyChapter, JourneyChallenge,
    JourneyReward, JourneyProgress, ChapterProgress, ChallengeAttempt, RewardProgress,
    # Push Notifications & Activity
    PushSubscription, UserActivity,
    # Email Preferences
    EmailPreference,
    # Advent Calendar Models
    AdventCalendar, AdventDoor, AdventDoorContent, AdventProgress, QRCodeToken
)


# ============================================================================
# CUSTOM ADMIN SITE - Improved Organization
# ============================================================================

class CrushLuAdminSite(admin.AdminSite):
    site_header = '💕 Crush.lu Coach Panel'
    site_title = 'Crush.lu Coach Panel'
    index_title = 'Welcome to Crush.lu Coach Management'

    def has_permission(self, request):
        """
        Custom permission check: Only Crush coaches can access this admin panel.
        Superusers can always access.

        Note: We override the default is_staff check to allow coaches access.
        """
        # Superusers always have access
        if request.user.is_superuser:
            return True

        # Check if user is authenticated
        if not request.user.is_authenticated:
            return False

        # Check if user is an active Crush coach
        try:
            coach = request.user.crushcoach
            # Grant access to active coaches even if they're not staff
            if coach.is_active:
                return True
        except:
            pass

        # Fallback to default staff check
        return request.user.is_active and request.user.is_staff

    def has_module_perms(self, request, app_label):
        """
        Allow coaches to see all Crush.lu models.
        """
        if not self.has_permission(request):
            return False

        # Coaches can see crush_lu app
        if app_label == 'crush_lu':
            return True

        # Superusers can see everything
        if request.user.is_superuser:
            return True

        # Default Django permission check for other apps
        return super().has_module_perms(request, app_label)

    def index(self, request, extra_context=None):
        """
        Override index to add custom dashboard link and analytics.
        """
        extra_context = extra_context or {}
        extra_context['show_dashboard_link'] = True
        extra_context['dashboard_url'] = '/crush-admin/dashboard/'

        # Add coach information to context
        try:
            coach = request.user.crushcoach
            extra_context['is_coach'] = True
            extra_context['coach_name'] = request.user.get_full_name() or request.user.username
        except:
            extra_context['is_coach'] = False

        return super().index(request, extra_context)

    def get_app_list(self, request, app_label=None):
        """
        Override to customize the admin index page grouping.
        Groups models into logical categories for better organization.
        """
        app_list = super().get_app_list(request, app_label)

        # Custom ordering and grouping
        custom_order = {
            # 1. Special Journey System (VIP Experience)
            'special_user_experience': {'order': 1, 'icon': '✨', 'group': 'Special Journey System'},
            'journeyconfiguration': {'order': 2, 'icon': '🗺️', 'group': 'Special Journey System'},
            'journeychapter': {'order': 3, 'icon': '📖', 'group': 'Special Journey System'},
            'journeychallenge': {'order': 4, 'icon': '🎯', 'group': 'Special Journey System'},
            'journeyreward': {'order': 5, 'icon': '🎁', 'group': 'Special Journey System'},
            'journeyprogress': {'order': 6, 'icon': '📊', 'group': 'Special Journey System'},
            'chapterprogress': {'order': 7, 'icon': '📈', 'group': 'Special Journey System'},
            'challengeattempt': {'order': 8, 'icon': '🎮', 'group': 'Special Journey System'},
            'rewardprogress': {'order': 9, 'icon': '🏆', 'group': 'Special Journey System'},

            # 2. User Profiles & Onboarding
            'crushprofile': {'order': 10, 'icon': '👤', 'group': 'Users & Profiles'},
            'profilesubmission': {'order': 11, 'icon': '📝', 'group': 'Users & Profiles'},
            'crushcoach': {'order': 12, 'icon': '🎓', 'group': 'Users & Profiles'},
            'coachsession': {'order': 13, 'icon': '💬', 'group': 'Users & Profiles'},
            'useractivity': {'order': 14, 'icon': '📊', 'group': 'Users & Profiles'},
            'pushsubscription': {'order': 15, 'icon': '🔔', 'group': 'Users & Profiles'},
            'emailpreference': {'order': 16, 'icon': '📧', 'group': 'Users & Profiles'},

            # 3. Events & Meetups
            'meetupevent': {'order': 20, 'icon': '🎉', 'group': 'Events & Meetups'},
            'eventregistration': {'order': 21, 'icon': '✅', 'group': 'Events & Meetups'},
            'globalactivityoption': {'order': 22, 'icon': '🎯', 'group': 'Events & Meetups'},
            'eventactivityoption': {'order': 23, 'icon': '🎲', 'group': 'Events & Meetups'},
            'eventactivityvote': {'order': 24, 'icon': '🗳️', 'group': 'Events & Meetups'},
            'eventvotingsession': {'order': 25, 'icon': '⏱️', 'group': 'Events & Meetups'},
            'presentationqueue': {'order': 26, 'icon': '📋', 'group': 'Events & Meetups'},
            'presentationrating': {'order': 27, 'icon': '⭐', 'group': 'Events & Meetups'},
            'speeddatingpair': {'order': 28, 'icon': '💑', 'group': 'Events & Meetups'},
            'eventinvitation': {'order': 29, 'icon': '💌', 'group': 'Events & Meetups'},

            # 4. Connections & Messages
            'eventconnection': {'order': 30, 'icon': '🔗', 'group': 'Connections'},
            'connectionmessage': {'order': 31, 'icon': '💌', 'group': 'Connections'},

            # 5. Advent Calendar System
            'adventcalendar': {'order': 40, 'icon': '🎄', 'group': 'Advent Calendar'},
            'adventdoor': {'order': 41, 'icon': '🚪', 'group': 'Advent Calendar'},
            'adventdoorcontent': {'order': 42, 'icon': '📦', 'group': 'Advent Calendar'},
            'adventprogress': {'order': 43, 'icon': '📊', 'group': 'Advent Calendar'},
            'qrcodetoken': {'order': 44, 'icon': '📱', 'group': 'Advent Calendar'},
        }

        # Create grouped app list - transform single crush_lu app into multiple sections
        new_app_list = []

        for app in app_list:
            if app['app_label'] == 'crush_lu':
                # Group models by category
                groups = {}

                for model in app['models']:
                    model_name = model['object_name'].lower()

                    # Handle the special case where object_name doesn't match the key
                    # Map known variations
                    model_key = model_name
                    if model_key == 'specialuserexperience':
                        model_key = 'special_user_experience'

                    if model_key in custom_order:
                        config = custom_order[model_key]
                        model['_order'] = config['order']
                        group_name = config['group']

                        # Add icon to model name only if it doesn't already have one
                        icon = config['icon']
                        if not model['name'].startswith(icon):
                            # Remove any existing numbering (e.g., "2. Journey Configurations" -> "Journey Configurations")
                            clean_name = model['name']
                            if '. ' in clean_name and clean_name.split('. ')[0].isdigit():
                                clean_name = '. '.join(clean_name.split('. ')[1:])

                            # Add sequential number and icon
                            model_number = config['order']
                            model['name'] = f"{icon} {model_number}. {clean_name}"

                        # Add to appropriate group
                        if group_name not in groups:
                            groups[group_name] = []
                        groups[group_name].append(model)

                # Create separate "app" entry for each group
                group_order = [
                    ('✨ Special Journey System', 'Special Journey System'),
                    ('🎄 Advent Calendar', 'Advent Calendar'),
                    ('👥 Users & Profiles', 'Users & Profiles'),
                    ('🎉 Events & Meetups', 'Events & Meetups'),
                    ('💕 Connections', 'Connections'),
                ]

                for display_name, group_key in group_order:
                    if group_key in groups:
                        # Sort models within each group
                        groups[group_key].sort(key=lambda x: x.get('_order', 999))

                        # Create a fake "app" for this group
                        new_app_list.append({
                            'name': display_name,
                            'app_label': f'crush_lu_{group_key.lower().replace(" ", "_").replace("&", "and")}',
                            'app_url': '#',
                            'has_module_perms': True,
                            'models': groups[group_key],
                        })
            else:
                # Keep other apps as-is
                new_app_list.append(app)

        return new_app_list


# Use custom admin site
crush_admin_site = CrushLuAdminSite(name='crush_admin')


# ============================================================================
# SPECIAL JOURNEY SYSTEM - VIP Experience Models
# ============================================================================


class SpecialUserExperienceAdmin(admin.ModelAdmin):
    """
    ✨ SPECIAL JOURNEY SYSTEM - VIP Experience Configuration

    This is the entry point for creating personalized journey experiences.
    Configure who gets the special journey and customize their experience.
    """
    list_display = (
        'first_name', 'last_name', 'is_active',
        'custom_welcome_title', 'animation_style',
        'auto_approve_profile', 'vip_badge',
        'get_journeys_status', 'trigger_count', 'last_triggered_at'
    )
    list_filter = ('is_active', 'animation_style', 'auto_approve_profile', 'vip_badge', 'skip_waitlist')
    search_fields = ('first_name', 'last_name', 'custom_welcome_title', 'custom_welcome_message')
    readonly_fields = ('created_at', 'updated_at', 'last_triggered_at', 'trigger_count', 'get_journey_status')
    actions = ['activate_experiences', 'deactivate_experiences', 'generate_wonderland_journey', 'generate_advent_calendar']

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:object_id>/generate-journey/',
                self.admin_site.admin_view(self.generate_journey_view),
                name='crush_lu_specialuserexperience_generate_journey',
            ),
        ]
        return custom_urls + urls

    def generate_journey_view(self, request, object_id):
        """Custom view to handle journey generation form (Wonderland or Advent Calendar)"""
        from django.shortcuts import render, get_object_or_404
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        from datetime import date
        from django.utils import timezone

        special_exp = get_object_or_404(SpecialUserExperience, pk=object_id)

        if request.method == 'POST':
            journey_type = request.POST.get('journey_type', 'wonderland')

            if journey_type == 'wonderland':
                # Handle Wonderland Journey creation
                return self._create_wonderland_journey(request, special_exp)
            else:
                # Handle Advent Calendar creation
                return self._create_advent_calendar(request, special_exp)

        # GET request - show the form
        # Check existing journeys
        existing_wonderland = JourneyConfiguration.objects.filter(
            special_experience=special_exp,
            journey_type='wonderland'
        ).exists()
        existing_advent = JourneyConfiguration.objects.filter(
            special_experience=special_exp,
            journey_type='advent_calendar'
        ).exists()

        context = {
            **self.admin_site.each_context(request),
            'special_exp': special_exp,
            'opts': self.model._meta,
            'title': f'Generate Journey for {special_exp.first_name} {special_exp.last_name}',
            'existing_wonderland': existing_wonderland,
            'existing_advent': existing_advent,
        }
        return render(request, 'admin/crush_lu/generate_journey_form.html', context)

    def _create_wonderland_journey(self, request, special_exp):
        """Create a Wonderland Journey for the special user"""
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        from datetime import date

        # Check if already exists
        existing = JourneyConfiguration.objects.filter(
            special_experience=special_exp,
            journey_type='wonderland'
        ).exists()
        if existing:
            django_messages.warning(
                request,
                f"Wonderland Journey already exists for {special_exp.first_name} {special_exp.last_name}. "
                f"Delete the existing journey first if you want to recreate it."
            )
            return HttpResponseRedirect(reverse('crush_admin:crush_lu_specialuserexperience_changelist'))

        date_met = request.POST.get('date_met', '2024-10-15')
        location_met = request.POST.get('location_met', 'Café de Paris')

        try:
            from crush_lu.management.commands.create_wonderland_journey import Command

            command = Command()
            parsed_date = date.fromisoformat(date_met)

            # Create Journey Configuration
            journey = JourneyConfiguration.objects.create(
                special_experience=special_exp,
                is_active=True,
                journey_name='The Wonderland of You',
                total_chapters=6,
                estimated_duration_minutes=90,
                date_first_met=parsed_date,
                location_first_met=location_met,
                certificate_enabled=True,
                final_message=(
                    f"You've completed every challenge and discovered every secret. "
                    f"But there's one thing I haven't said clearly enough: "
                    f"You're extraordinary, and I'd be honored if you'd let me prove it to you, "
                    f"one real moment at a time."
                ),
            )

            # Create all chapters using the command's methods
            command.create_all_chapters(journey, parsed_date, location_met, special_exp.first_name)

            django_messages.success(
                request,
                f"Successfully generated Wonderland Journey for {special_exp.first_name} {special_exp.last_name}! "
                f"Journey includes 6 chapters with all challenges and rewards."
            )

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            django_messages.error(request, f"Error generating Wonderland journey: {str(e)}")
            print(f"Error details: {error_detail}")

        return HttpResponseRedirect(reverse('crush_admin:crush_lu_specialuserexperience_changelist'))

    def _create_advent_calendar(self, request, special_exp):
        """Create an Advent Calendar for the special user"""
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        from django.utils import timezone
        from datetime import date
        from crush_lu.models import AdventCalendar, AdventDoor, AdventDoorContent

        # Check if already exists
        existing = JourneyConfiguration.objects.filter(
            special_experience=special_exp,
            journey_type='advent_calendar'
        ).exists()
        if existing:
            django_messages.warning(
                request,
                f"Advent Calendar already exists for {special_exp.first_name} {special_exp.last_name}. "
                f"Delete the existing calendar first if you want to recreate it."
            )
            return HttpResponseRedirect(reverse('crush_admin:crush_lu_specialuserexperience_changelist'))

        # Get form data
        year = int(request.POST.get('advent_year', timezone.now().year))
        title = request.POST.get('advent_title', 'Your Advent Calendar') or f"{special_exp.first_name}'s Advent Calendar"
        welcome = request.POST.get('advent_welcome', '')
        allow_catch_up = 'allow_catch_up' in request.POST
        generate_qr = 'generate_qr_tokens' in request.POST

        try:
            # Default door configuration
            DEFAULT_DOORS = [
                {'day': 1, 'type': 'poem', 'qr': 'none', 'teaser': 'Your journey begins...', 'icon': '📜'},
                {'day': 2, 'type': 'memory', 'qr': 'none', 'teaser': 'Remember when...', 'icon': '💭'},
                {'day': 3, 'type': 'photo', 'qr': 'bonus', 'teaser': 'A captured moment', 'icon': '📷'},
                {'day': 4, 'type': 'poem', 'qr': 'none', 'teaser': 'Words from the heart', 'icon': '📜'},
                {'day': 5, 'type': 'audio', 'qr': 'none', 'teaser': 'Listen closely...', 'icon': '🎵'},
                {'day': 6, 'type': 'gift_teaser', 'qr': 'required', 'teaser': 'Something special awaits...', 'icon': '🎁'},
                {'day': 7, 'type': 'poem', 'qr': 'none', 'teaser': 'A favorite thought', 'icon': '📜'},
                {'day': 8, 'type': 'video', 'qr': 'none', 'teaser': 'A message for you', 'icon': '🎥'},
                {'day': 9, 'type': 'memory', 'qr': 'none', 'teaser': 'A shared moment', 'icon': '💭'},
                {'day': 10, 'type': 'photo', 'qr': 'bonus', 'teaser': 'Through my eyes', 'icon': '📷'},
                {'day': 11, 'type': 'poem', 'qr': 'none', 'teaser': 'Verses of affection', 'icon': '📜'},
                {'day': 12, 'type': 'memory', 'qr': 'none', 'teaser': 'Halfway there...', 'icon': '💭'},
                {'day': 13, 'type': 'gift_teaser', 'qr': 'required', 'teaser': 'Unwrap with care', 'icon': '🎁'},
                {'day': 14, 'type': 'photo', 'qr': 'none', 'teaser': 'A special picture', 'icon': '📷'},
                {'day': 15, 'type': 'poem', 'qr': 'none', 'teaser': 'Poetry for you', 'icon': '📜'},
                {'day': 16, 'type': 'video', 'qr': 'none', 'teaser': 'Watch this...', 'icon': '🎥'},
                {'day': 17, 'type': 'photo', 'qr': 'bonus', 'teaser': 'Captured magic', 'icon': '📷'},
                {'day': 18, 'type': 'poem', 'qr': 'none', 'teaser': 'From my heart', 'icon': '📜'},
                {'day': 19, 'type': 'audio', 'qr': 'none', 'teaser': 'Hear my voice', 'icon': '🎵'},
                {'day': 20, 'type': 'gift_teaser', 'qr': 'required', 'teaser': 'The countdown continues', 'icon': '🎁'},
                {'day': 21, 'type': 'video', 'qr': 'none', 'teaser': 'Almost there...', 'icon': '🎥'},
                {'day': 22, 'type': 'poem', 'qr': 'none', 'teaser': 'Two more sleeps', 'icon': '📜'},
                {'day': 23, 'type': 'photo', 'qr': 'bonus', 'teaser': 'One more sleep', 'icon': '📷'},
                {'day': 24, 'type': 'countdown', 'qr': 'required', 'teaser': 'The final door...', 'icon': '⏰'},
            ]

            # Door colors (Christmas themed)
            DOOR_COLORS = ['#c41e3a', '#165b33', '#bb2528', '#146b3a', '#ea4630', '#0c4827', '#f8b229', '#1e5945']

            # 1. Create Journey Configuration
            journey = JourneyConfiguration.objects.create(
                special_experience=special_exp,
                journey_type='advent_calendar',
                is_active=True,
                journey_name=title,
                total_chapters=24,
                estimated_duration_minutes=480,
                certificate_enabled=False,
                final_message=f"Merry Christmas, {special_exp.first_name}! You've discovered all 24 surprises.",
            )

            # 2. Create Advent Calendar
            calendar = AdventCalendar.objects.create(
                journey=journey,
                year=year,
                start_date=date(year, 12, 1),
                end_date=date(year, 12, 24),
                allow_catch_up=allow_catch_up,
                timezone_name='Europe/Luxembourg',
                calendar_title=title,
                calendar_description=welcome or f"Welcome to your personal Advent Calendar, {special_exp.first_name}! Each day unlocks a new surprise just for you.",
            )

            # 3. Create 24 doors with content placeholders
            doors_created = 0
            for config in DEFAULT_DOORS:
                door = AdventDoor.objects.create(
                    calendar=calendar,
                    door_number=config['day'],
                    content_type=config['type'],
                    qr_mode=config['qr'],
                    door_color=DOOR_COLORS[config['day'] % len(DOOR_COLORS)],
                    door_icon=config['icon'],
                    teaser_text=config['teaser'],
                )

                # Create content placeholder
                AdventDoorContent.objects.create(
                    door=door,
                    title=f"Day {config['day']}",
                    message=f"[Add your {config['type']} content here]",
                )
                doors_created += 1

            # 4. Generate QR tokens if requested
            qr_count = 0
            if generate_qr:
                from crush_lu.models import QRCodeToken
                from django.contrib.auth import get_user_model
                User = get_user_model()

                # Try to find the user
                user = User.objects.filter(
                    first_name__iexact=special_exp.first_name,
                    last_name__iexact=special_exp.last_name
                ).first()

                if user:
                    for door in calendar.doors.filter(qr_mode__in=['required', 'bonus']):
                        QRCodeToken.objects.create(
                            door=door,
                            user=user,
                        )
                        qr_count += 1

            # Success message
            success_msg = (
                f"Successfully generated Advent Calendar for {special_exp.first_name} {special_exp.last_name}! "
                f"Created {doors_created} doors."
            )
            if generate_qr and qr_count > 0:
                success_msg += f" Generated {qr_count} QR tokens."
            elif generate_qr and qr_count == 0:
                success_msg += " Note: QR tokens not created - user account not found."

            django_messages.success(request, success_msg)

        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            django_messages.error(request, f"Error generating Advent Calendar: {str(e)}")
            print(f"Error details: {error_detail}")

        return HttpResponseRedirect(reverse('crush_admin:crush_lu_specialuserexperience_changelist'))

    fieldsets = (
        ('👤 User Matching', {
            'fields': ('first_name', 'last_name', 'is_active'),
            'description': 'User must match BOTH first name AND last name (case-insensitive)'
        }),
        ('🎨 Custom Welcome Experience', {
            'fields': (
                'custom_welcome_title',
                'custom_welcome_message',
                'custom_theme_color',
                'animation_style',
                'custom_landing_url',
            ),
            'description': 'Customize the special welcome page appearance'
        }),
        ('⭐ VIP Features & Permissions', {
            'fields': (
                'auto_approve_profile',
                'skip_waitlist',
                'vip_badge',
            ),
            'description': 'Special permissions and features for this user'
        }),
        ('🗺️ Journey Status', {
            'fields': ('get_journey_status',),
            'description': 'View or generate the Wonderland Journey for this user'
        }),
        ('📊 Tracking & Analytics', {
            'fields': (
                'trigger_count',
                'last_triggered_at',
            ),
            'classes': ('collapse',),
            'description': 'Track how often this special experience has been used'
        }),
        ('🗓️ Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_journeys_status(self, obj):
        """Display which journey types exist for this user"""
        journeys = JourneyConfiguration.objects.filter(special_experience=obj)
        if not journeys.exists():
            return format_html('<span style="color: #999;">—</span>')

        badges = []
        for journey in journeys:
            if journey.journey_type == 'wonderland':
                badges.append('<span style="background: #9B59B6; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px;">🎭 Wonderland</span>')
            elif journey.journey_type == 'advent_calendar':
                badges.append('<span style="background: #C41E3A; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px;">🎄 Advent</span>')
            else:
                badges.append(f'<span style="background: #666; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin-right: 4px;">📖 {journey.journey_type}</span>')

        return format_html(''.join(badges))
    get_journeys_status.short_description = 'Journeys'

    def get_journey_status(self, obj):
        """Display journey status with generation button"""
        try:
            journey = obj.journey
            chapter_count = journey.chapters.count()
            challenge_count = sum(chapter.challenges.count() for chapter in journey.chapters.all())
            return format_html(
                '<div style="padding: 10px; background: #e8f5e9; border-radius: 5px;">'
                '<strong>✅ Journey Created:</strong> {}<br>'
                '<strong>Chapters:</strong> {}<br>'
                '<strong>Challenges:</strong> {}<br>'
                '<strong>Status:</strong> {}<br>'
                '<a href="/admin/crush_lu/journeyconfiguration/{}/change/" '
                'class="button" style="margin-top: 10px;">View/Edit Journey</a>'
                '</div>',
                journey.journey_name,
                chapter_count,
                challenge_count,
                'Active' if journey.is_active else 'Inactive',
                journey.id
            )
        except JourneyConfiguration.DoesNotExist:
            return format_html(
                '<div style="padding: 10px; background: #fff3e0; border-radius: 5px;">'
                '<strong>⚠️ No Journey Created Yet</strong><br>'
                '<p>Use the "Generate Wonderland Journey" action to create one.</p>'
                '<p><strong>Tip:</strong> Select this experience and choose the action from the dropdown above.</p>'
                '</div>'
            )
    get_journey_status.short_description = 'Journey Status'

    @admin.action(description='✅ Activate selected experiences')
    def activate_experiences(self, request, queryset):
        updated = queryset.update(is_active=True)
        django_messages.success(request, f"Activated {updated} special experience(s)")

    @admin.action(description='❌ Deactivate selected experiences')
    def deactivate_experiences(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} special experience(s)")

    @admin.action(description='🎭 Generate Wonderland Journey (with customization)')
    def generate_wonderland_journey(self, request, queryset):
        """Redirect to the custom journey generation form"""
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        if queryset.count() != 1:
            django_messages.error(request, "Please select exactly ONE special experience to generate a journey for.")
            return

        special_exp = queryset.first()

        # Check if WONDERLAND journey type already exists (user can have multiple journey types)
        existing_wonderland = JourneyConfiguration.objects.filter(
            special_experience=special_exp,
            journey_type='wonderland'
        ).first()
        if existing_wonderland:
            django_messages.warning(
                request,
                f"Wonderland Journey already exists for {special_exp.first_name} {special_exp.last_name}. "
                f"Delete the existing Wonderland journey first if you want to recreate it."
            )
            return

        # Redirect to the custom form view (use crush_admin namespace, not admin)
        return HttpResponseRedirect(
            reverse('crush_admin:crush_lu_specialuserexperience_generate_journey', args=[special_exp.id])
        )

    @admin.action(description='🎄 Generate Advent Calendar (24 doors)')
    def generate_advent_calendar(self, request, queryset):
        """Redirect to the custom journey generation form with advent calendar pre-selected"""
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        if queryset.count() != 1:
            django_messages.error(request, "Please select exactly ONE special experience to generate an Advent Calendar for.")
            return

        special_exp = queryset.first()

        # Check if ADVENT CALENDAR journey type already exists
        existing_advent = JourneyConfiguration.objects.filter(
            special_experience=special_exp,
            journey_type='advent_calendar'
        ).first()
        if existing_advent:
            django_messages.warning(
                request,
                f"Advent Calendar already exists for {special_exp.first_name} {special_exp.last_name}. "
                f"Delete the existing Advent Calendar first if you want to recreate it."
            )
            return

        # Redirect to the custom form view with advent parameter
        return HttpResponseRedirect(
            reverse('crush_admin:crush_lu_specialuserexperience_generate_journey', args=[special_exp.id]) + '?type=advent'
        )


# ============================================================================
# USER PROFILES & ONBOARDING - Profile Management
# ============================================================================


class CrushCoachAdmin(admin.ModelAdmin):
    list_display = ('user', 'get_email', 'specializations', 'is_active', 'max_active_reviews', 'created_at', 'has_dating_profile')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('created_at',)
    actions = ['deactivate_coach_allow_dating', 'deactivate_coaches', 'activate_coaches']

    def get_email(self, obj):
        """Display coach's email address"""
        return obj.user.email
    get_email.short_description = 'Email'
    get_email.admin_order_field = 'user__email'  # Allow sorting by email

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


class CrushProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'get_email', 'age', 'gender', 'location', 'get_assigned_coach', 'is_approved', 'is_active', 'created_at', 'is_coach')
    list_filter = ('is_approved', 'is_active', 'gender', 'created_at')
    search_fields = ('user__username', 'user__email', 'location', 'bio')
    readonly_fields = ('created_at', 'updated_at', 'approved_at', 'get_assigned_coach')
    actions = ['promote_to_coach', 'approve_profiles', 'deactivate_profiles', 'export_profiles_csv']
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
        ('Coach Assignment', {
            'fields': ('get_assigned_coach',),
            'description': 'View which coach is assigned to review this profile. Screening calls are handled during the review process.'
        }),
        ('Profile Completion', {
            'fields': ('completion_status',),
            'classes': ('collapse',),
            'description': 'Track which step of profile creation user completed'
        }),
        ('Status', {
            'fields': ('is_approved', 'is_active', 'approved_at')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def get_email(self, obj):
        """Display user's email address"""
        return obj.user.email
    get_email.short_description = 'Email'
    get_email.admin_order_field = 'user__email'  # Allow sorting by email

    def is_coach(self, obj):
        """Check if this user is also a coach"""
        return hasattr(obj.user, 'crushcoach')
    is_coach.boolean = True
    is_coach.short_description = 'Is Coach'

    def get_assigned_coach(self, obj):
        """Display the assigned coach from ProfileSubmission"""
        try:
            submission = ProfileSubmission.objects.get(profile=obj)
            if submission.coach:
                return f"{submission.coach.user.get_full_name()} ({submission.get_status_display()})"
            else:
                return "No coach assigned"
        except ProfileSubmission.DoesNotExist:
            return "Not submitted yet"
    get_assigned_coach.short_description = 'Assigned Coach'

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

    @admin.action(description='📊 Export selected profiles to CSV')
    def export_profiles_csv(self, request, queryset):
        """Export selected profiles to CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="crush_profiles_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        writer = csv.writer(response)
        # Header row
        writer.writerow([
            'Username',
            'Email',
            'First Name',
            'Last Name',
            'Age',
            'Gender',
            'Location',
            'Phone',
            'Is Approved',
            'Is Active',
            'Approved Date',
            'Created Date',
            'Completion Status',
        ])

        # Data rows
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

        django_messages.success(
            request,
            f"Exported {queryset.count()} profile(s) to CSV."
        )

        return response


class ProfileSubmissionAdmin(admin.ModelAdmin):
    list_display = ('profile', 'coach', 'status', 'review_call_completed', 'submitted_at', 'reviewed_at')
    list_filter = ('status', 'review_call_completed', 'submitted_at', 'reviewed_at')
    search_fields = ('profile__user__username', 'coach__user__username')
    readonly_fields = ('submitted_at',)
    fieldsets = (
        ('Submission Details', {
            'fields': ('profile', 'coach', 'status')
        }),
        ('Screening Call (During Review)', {
            'fields': ('review_call_completed', 'review_call_date', 'review_call_notes'),
            'description': 'Coach must complete screening call before approving profile'
        }),
        ('Review', {
            'fields': ('coach_notes', 'feedback_to_user')
        }),
        ('Timestamps', {
            'fields': ('submitted_at', 'reviewed_at')
        }),
    )


class CoachSessionAdmin(admin.ModelAdmin):
    list_display = ('coach', 'user', 'session_type', 'scheduled_at', 'completed_at', 'created_at')
    list_filter = ('session_type', 'scheduled_at', 'completed_at')
    search_fields = ('coach__user__username', 'user__username', 'notes')
    readonly_fields = ('created_at',)


# Inline admin for Event Registrations
class EventRegistrationInline(admin.TabularInline):
    model = EventRegistration
    extra = 0
    fields = ('user', 'status', 'payment_confirmed', 'registered_at')
    readonly_fields = ('registered_at',)
    can_delete = False
    show_change_link = True


# Inline admin for Event Invitations (Private Events)
class EventInvitationInline(admin.TabularInline):
    model = EventInvitation
    extra = 0
    fields = ('guest_email', 'guest_first_name', 'guest_last_name', 'status', 'approval_status', 'invitation_sent_at')
    readonly_fields = ('invitation_sent_at', 'invitation_code')
    can_delete = True
    show_change_link = True
    verbose_name = "Private Invitation"
    verbose_name_plural = "Private Invitations"


# Inline admin for Voting Session
class EventVotingSessionInline(admin.StackedInline):
    model = EventVotingSession
    extra = 0
    fields = (
        ('is_active', 'total_votes'),
        ('voting_start_time', 'voting_end_time'),
        ('winning_presentation_style', 'winning_speed_dating_twist')
    )
    readonly_fields = ('total_votes',)
    can_delete = False


# Inline admin for Presentation Queue
class PresentationQueueInline(admin.TabularInline):
    model = PresentationQueue
    extra = 0
    fields = ('user', 'presentation_order', 'status', 'started_at', 'completed_at', 'duration_seconds')
    readonly_fields = ('duration_seconds', 'started_at', 'completed_at')
    can_delete = False
    ordering = ['presentation_order']
    show_change_link = True


# Inline admin for Speed Dating Pairs
class SpeedDatingPairInline(admin.TabularInline):
    model = SpeedDatingPair
    extra = 0
    fields = ('round_number', 'user1', 'user2', 'mutual_rating_score', 'is_top_match', 'duration_minutes')
    readonly_fields = ('mutual_rating_score', 'duration_minutes')
    can_delete = False
    ordering = ['round_number']
    show_change_link = True


# ============================================================================
# EVENTS & MEETUPS - Event Management System
# ============================================================================


class MeetupEventAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'event_type', 'date_time', 'location',
        'get_registration_count', 'get_confirmed_count', 'get_waitlist_count',
        'max_participants', 'get_spots_remaining',
        'is_private_invitation', 'get_invited_users_count', 'get_voting_status', 'is_published', 'is_cancelled'
    )
    list_filter = ('event_type', 'is_published', 'is_cancelled', 'is_private_invitation', 'date_time')
    search_fields = ('title', 'description', 'location', 'address')
    readonly_fields = (
        'created_at', 'updated_at', 'invitation_code',
        'get_registration_count', 'get_confirmed_count', 'get_waitlist_count',
        'get_spots_remaining', 'get_revenue',
        'get_voting_status', 'get_presentation_status', 'get_speed_dating_status'
    )
    inlines = [EventRegistrationInline, EventInvitationInline, EventVotingSessionInline, PresentationQueueInline, SpeedDatingPairInline]
    actions = ['publish_events', 'unpublish_events', 'cancel_events']
    filter_horizontal = ('invited_users',)  # Nice widget for ManyToMany field

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
        ('✨ Private Invitation Settings', {
            'fields': ('is_private_invitation', 'invited_users', 'invitation_code', 'max_invited_guests', 'invitation_expires_at'),
            'classes': ('collapse',),
            'description': 'Configure this event as invitation-only. You can invite existing users directly OR send external guest invitations (managed via EventInvitation inline below)'
        }),
        ('📊 Event Statistics', {
            'fields': (
                'get_registration_count', 'get_confirmed_count', 'get_waitlist_count',
                'get_spots_remaining', 'get_revenue'
            ),
            'classes': ('collapse',),
            'description': 'Real-time event statistics and capacity information'
        }),
        ('🎯 Phase Status Overview', {
            'fields': ('get_voting_status', 'get_presentation_status', 'get_speed_dating_status'),
            'classes': ('collapse',),
            'description': 'Track progress through the 3-phase event system'
        }),
        ('Status', {
            'fields': ('is_published', 'is_cancelled')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    # Custom display methods
    def get_registration_count(self, obj):
        """Total registrations (all statuses)"""
        return obj.eventregistration_set.count()
    get_registration_count.short_description = '📝 Total Registrations'

    def get_invited_users_count(self, obj):
        """Count of directly invited existing users"""
        count = obj.invited_users.count()
        if count > 0:
            return f"👥 {count}"
        return "-"
    get_invited_users_count.short_description = 'Invited Users'

    def get_confirmed_count(self, obj):
        """Confirmed registrations only"""
        return obj.get_confirmed_count()
    get_confirmed_count.short_description = '✅ Confirmed'

    def get_waitlist_count(self, obj):
        """Waitlisted registrations"""
        return obj.get_waitlist_count()
    get_waitlist_count.short_description = '⏳ Waitlist'

    def get_spots_remaining(self, obj):
        """Calculate remaining spots"""
        remaining = obj.spots_remaining
        if remaining == 0:
            return f"🔴 FULL (0/{obj.max_participants})"
        elif remaining <= 5:
            return f"🟡 {remaining}/{obj.max_participants}"
        else:
            return f"🟢 {remaining}/{obj.max_participants}"
    get_spots_remaining.short_description = 'Spots Available'

    def get_revenue(self, obj):
        """Calculate total revenue from confirmed payments"""
        confirmed = obj.eventregistration_set.filter(payment_confirmed=True).count()
        revenue = confirmed * obj.registration_fee
        return f"€{revenue:.2f} ({confirmed} paid)"
    get_revenue.short_description = '💰 Revenue'

    def get_voting_status(self, obj):
        """Display Phase 1 voting status"""
        try:
            voting_session = obj.voting_session
            if not voting_session.is_active and voting_session.voting_end_time:
                # Voting ended
                return f"✅ Completed ({voting_session.total_votes} votes) | Winners: {voting_session.winning_presentation_style or 'N/A'} & {voting_session.winning_speed_dating_twist or 'N/A'}"
            elif voting_session.is_active:
                return f"🟢 ACTIVE ({voting_session.total_votes} votes so far)"
            else:
                return "⏸️ Not Started"
        except EventVotingSession.DoesNotExist:
            return "❌ No Voting Session"
    get_voting_status.short_description = '🗳️ Phase 1: Voting'

    def get_presentation_status(self, obj):
        """Display Phase 2 presentation status"""
        presentations = obj.presentation_queue.all()
        if not presentations.exists():
            return "❌ Not Initialized"

        total = presentations.count()
        completed = presentations.filter(status='completed').count()
        in_progress = presentations.filter(status='in_progress').exists()

        if completed == total:
            return f"✅ All Complete ({total}/{total})"
        elif in_progress:
            return f"🟢 IN PROGRESS ({completed}/{total} done)"
        elif completed > 0:
            return f"⏸️ Paused ({completed}/{total} done)"
        else:
            return f"⏳ Ready to Start (0/{total})"
    get_presentation_status.short_description = '🎤 Phase 2: Presentations'

    def get_speed_dating_status(self, obj):
        """Display Phase 3 speed dating status"""
        pairs = obj.speed_dating_pairs.all()
        if not pairs.exists():
            return "❌ Not Initialized"

        total_pairs = pairs.count()
        completed_pairs = pairs.filter(completed_at__isnull=False).count()
        in_progress = pairs.filter(started_at__isnull=False, completed_at__isnull=True).exists()

        if completed_pairs == total_pairs:
            return f"✅ All Rounds Complete ({total_pairs} pairs)"
        elif in_progress:
            return f"🟢 IN PROGRESS ({completed_pairs}/{total_pairs} rounds done)"
        elif completed_pairs > 0:
            return f"⏸️ Paused ({completed_pairs}/{total_pairs} rounds done)"
        else:
            return f"⏳ Ready to Start (0/{total_pairs} pairs)"
    get_speed_dating_status.short_description = '💕 Phase 3: Speed Dating'

    # Admin actions
    @admin.action(description='✅ Publish selected events')
    def publish_events(self, request, queryset):
        updated = queryset.update(is_published=True)
        django_messages.success(request, f"Published {updated} event(s)")

    @admin.action(description='❌ Unpublish selected events')
    def unpublish_events(self, request, queryset):
        updated = queryset.update(is_published=False)
        django_messages.success(request, f"Unpublished {updated} event(s)")

    @admin.action(description='🚫 Cancel selected events')
    def cancel_events(self, request, queryset):
        updated = queryset.update(is_cancelled=True)
        django_messages.success(request, f"Cancelled {updated} event(s)")


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


class EventInvitationAdmin(admin.ModelAdmin):
    """
    ✨ PRIVATE EVENT INVITATIONS - Manage VIP Guest Invitations

    Send and manage private invitations for exclusive events.
    Track invitation status, approvals, and guest account creation.
    """
    list_display = (
        'get_guest_name', 'guest_email', 'event', 'status', 'approval_status',
        'invitation_sent_at', 'invited_by', 'has_special_user', 'get_invitation_link'
    )
    list_filter = ('status', 'approval_status', 'invitation_sent_at', 'event', 'special_user')
    search_fields = (
        'guest_email', 'guest_first_name', 'guest_last_name',
        'event__title', 'invited_by__username',
        'special_user__first_name', 'special_user__last_name'
    )
    readonly_fields = (
        'invitation_code', 'invitation_sent_at', 'accepted_at', 'approved_at',
        'get_invitation_link', 'get_status_display'
    )
    actions = ['approve_guests', 'reject_guests', 'resend_invitations']

    fieldsets = (
        ('👤 Guest Information', {
            'fields': ('guest_first_name', 'guest_last_name', 'guest_email')
        }),
        ('🎉 Event Details', {
            'fields': ('event', 'invited_by')
        }),
        ('✨ Special User VIP Treatment', {
            'fields': ('special_user',),
            'classes': ('collapse',),
            'description': 'Link this invitation to a Special User Experience for VIP treatment (auto-approval, custom journey, etc.)'
        }),
        ('📧 Invitation Status', {
            'fields': (
                'status', 'invitation_code', 'get_invitation_link',
                'invitation_sent_at', 'accepted_at'
            ),
            'description': 'Track invitation delivery and guest response'
        }),
        ('✅ Approval Workflow', {
            'fields': (
                'approval_status', 'approval_notes', 'approved_at'
            ),
            'description': 'Coach approval for guests to attend the event'
        }),
        ('👥 User Account', {
            'fields': ('created_user',),
            'description': 'Linked user account (created when guest accepts invitation)'
        }),
        ('📊 Status Overview', {
            'fields': ('get_status_display',),
            'classes': ('collapse',),
            'description': 'Complete invitation lifecycle status'
        }),
    )

    # Custom display methods
    def get_guest_name(self, obj):
        """Display guest's full name"""
        return f"{obj.guest_first_name} {obj.guest_last_name}"
    get_guest_name.short_description = 'Guest Name'
    get_guest_name.admin_order_field = 'guest_first_name'

    def has_special_user(self, obj):
        """Display if linked to Special User Experience"""
        return obj.special_user is not None
    has_special_user.boolean = True
    has_special_user.short_description = '✨ VIP'

    def get_invitation_link(self, obj):
        """Display clickable invitation link"""
        if obj.invitation_code:
            # Build absolute URL
            from django.urls import reverse
            url = f"https://crush.lu{reverse('crush_lu:invitation_landing', kwargs={'code': obj.invitation_code})}"
            return format_html(
                '<a href="{}" target="_blank" style="color: #9B59B6; font-weight: bold;">'
                '📧 View Invitation Page</a><br>'
                '<small style="color: #666; font-family: monospace;">{}</small>',
                url, url
            )
        return "N/A"
    get_invitation_link.short_description = 'Invitation Link'

    def get_status_display(self, obj):
        """Display comprehensive status with visual indicators"""
        status_html = '<div style="padding: 15px; background: #f8f9fa; border-radius: 8px;">'

        # Invitation Status
        status_colors = {
            'pending': '#ffc107',
            'accepted': '#0dcaf0',
            'declined': '#6c757d',
            'attended': '#28a745',
            'expired': '#dc3545',
        }
        status_color = status_colors.get(obj.status, '#6c757d')
        status_html += f'<p><strong>Invitation:</strong> <span style="color: {status_color}; font-weight: bold;">● {obj.get_status_display()}</span></p>'

        # Approval Status
        approval_colors = {
            'pending_approval': '#ffc107',
            'approved': '#28a745',
            'rejected': '#dc3545',
        }
        approval_color = approval_colors.get(obj.approval_status, '#6c757d')
        status_html += f'<p><strong>Approval:</strong> <span style="color: {approval_color}; font-weight: bold;">● {obj.get_approval_status_display()}</span></p>'

        # Expiration check
        if obj.is_expired:
            status_html += '<p style="color: #dc3545;"><strong>⚠️ EXPIRED</strong></p>'

        # User account
        if obj.created_user:
            status_html += f'<p><strong>Account Created:</strong> ✅ {obj.created_user.username}</p>'
        else:
            status_html += '<p><strong>Account:</strong> ❌ Not yet created</p>'

        status_html += '</div>'
        return format_html(status_html)
    get_status_display.short_description = 'Complete Status'

    # Admin actions
    @admin.action(description='✅ Approve selected guests')
    def approve_guests(self, request, queryset):
        """Approve guests to attend the event and send notification emails"""
        from django.utils import timezone
        from crush_lu.email_notifications import send_invitation_approval_email

        # Filter only accepted invitations
        accepted_invitations = queryset.filter(
            status='accepted',
            approval_status='pending_approval'
        )

        updated = 0
        emails_sent = 0

        for invitation in accepted_invitations:
            invitation.approval_status = 'approved'
            invitation.approved_at = timezone.now()
            invitation.save()
            updated += 1

            # Send approval email
            if send_invitation_approval_email(invitation, request=request):
                emails_sent += 1

        if updated > 0:
            django_messages.success(
                request,
                f"Approved {updated} guest(s) to attend the event. "
                f"Sent {emails_sent} email notification(s)."
            )
        else:
            django_messages.warning(
                request,
                "No pending invitations to approve. Only accepted invitations can be approved."
            )

    @admin.action(description='❌ Reject selected guests')
    def reject_guests(self, request, queryset):
        """Reject guests from attending the event"""
        from django.utils import timezone

        # Filter only accepted invitations
        accepted_invitations = queryset.filter(
            status='accepted',
            approval_status='pending_approval'
        )

        updated = accepted_invitations.update(
            approval_status='rejected',
            approved_at=timezone.now()
        )

        if updated > 0:
            django_messages.success(
                request,
                f"Rejected {updated} guest(s). They will be notified."
            )
        else:
            django_messages.warning(
                request,
                "No pending invitations to reject. Only accepted invitations can be rejected."
            )

    @admin.action(description='📧 Resend invitation emails')
    def resend_invitations(self, request, queryset):
        """Resend invitation emails to guests who haven't accepted"""
        pending_invitations = queryset.filter(status='pending')

        # TODO: Implement email sending logic
        count = pending_invitations.count()

        if count > 0:
            django_messages.info(
                request,
                f"Would resend {count} invitation(s). Email sending not yet implemented."
            )
        else:
            django_messages.warning(
                request,
                "No pending invitations to resend. Only unaccepted invitations can be resent."
            )


# ============================================================================
# CONNECTIONS & MESSAGES - Post-Event Networking
# ============================================================================


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


# Inline admin for Activity Options within Event admin
class EventActivityOptionInline(admin.TabularInline):
    model = EventActivityOption
    extra = 0
    fields = ('display_name', 'activity_type', 'activity_variant', 'vote_count', 'is_winner')
    readonly_fields = ('vote_count', 'is_winner')


# ============================================================================
# EVENT ACTIVITIES - Voting & Activity Management
# ============================================================================


class GlobalActivityOptionAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'get_activity_phase', 'activity_variant', 'is_active', 'sort_order', 'created_at')
    list_filter = ('activity_type', 'is_active')
    search_fields = ('display_name', 'description', 'activity_variant')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        ('Activity Details', {
            'fields': ('activity_type', 'activity_variant', 'display_name', 'description'),
            'description': 'These global options are reused across ALL Crush events'
        }),
        ('Settings', {
            'fields': ('is_active', 'sort_order')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    def get_activity_phase(self, obj):
        """Display friendly name for activity type"""
        phase_map = {
            'presentation_style': '🎤 Phase 2: Presentation Style',
            'speed_dating_twist': '💕 Phase 3: Speed Dating Twist',
        }
        return phase_map.get(obj.activity_type, obj.activity_type)
    get_activity_phase.short_description = 'Event Phase'


class EventActivityOptionAdmin(admin.ModelAdmin):
    list_display = ('event', 'display_name', 'get_activity_phase', 'activity_variant', 'vote_count', 'is_winner', 'created_at')
    list_filter = ('activity_type', 'is_winner', 'event__date_time')
    search_fields = ('event__title', 'display_name', 'description')
    readonly_fields = ('created_at', 'vote_count', 'get_activity_phase')
    fieldsets = (
        ('Activity Details', {
            'fields': ('event', 'get_activity_phase', 'activity_type', 'activity_variant', 'display_name', 'description'),
            'description': 'Activity Type determines which phase this option belongs to: Presentation Style (Phase 2) or Speed Dating Twist (Phase 3)'
        }),
        ('Voting Results', {
            'fields': ('vote_count', 'is_winner')
        }),
        ('Metadata', {
            'fields': ('created_at',)
        }),
    )

    def get_activity_phase(self, obj):
        """Display friendly name for activity type"""
        phase_map = {
            'presentation_style': '🎤 Phase 2: Presentation Style',
            'speed_dating_twist': '💕 Phase 3: Speed Dating Twist',
        }
        return phase_map.get(obj.activity_type, obj.activity_type)
    get_activity_phase.short_description = 'Event Phase'


class EventActivityVoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'selected_option', 'voted_at')
    list_filter = ('event', 'voted_at')
    search_fields = ('user__username', 'event__title', 'selected_option__display_name')
    readonly_fields = ('voted_at',)
    fieldsets = (
        ('Vote Details', {
            'fields': ('event', 'user', 'selected_option')
        }),
        ('Metadata', {
            'fields': ('voted_at',)
        }),
    )


class EventVotingSessionAdmin(admin.ModelAdmin):
    list_display = ('event', 'is_active', 'voting_start_time', 'voting_end_time', 'total_votes', 'winning_presentation_style', 'winning_speed_dating_twist')
    list_filter = ('is_active', 'voting_start_time', 'voting_end_time')
    search_fields = ('event__title',)
    readonly_fields = ('created_at', 'updated_at', 'total_votes')
    actions = ['start_voting_session', 'end_voting_session']
    fieldsets = (
        ('Event', {
            'fields': ('event',)
        }),
        ('Voting Schedule', {
            'fields': ('voting_start_time', 'voting_end_time', 'is_active')
        }),
        ('Results', {
            'fields': ('total_votes', 'winning_presentation_style', 'winning_speed_dating_twist')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    @admin.action(description='Start voting for selected sessions')
    def start_voting_session(self, request, queryset):
        """Manually start voting sessions"""
        updated = 0
        for session in queryset:
            session.start_voting()
            updated += 1
        django_messages.success(request, f"Started voting for {updated} session(s)")

    @admin.action(description='End voting and calculate winners')
    def end_voting_session(self, request, queryset):
        """Manually end voting sessions and calculate winners"""
        updated = 0
        for session in queryset:
            session.end_voting()
            updated += 1
        django_messages.success(request, f"Ended voting and calculated winners for {updated} session(s)")


# ============================================================================
# PRESENTATIONS - Speed Dating Presentation System
# ============================================================================


class PresentationQueueAdmin(admin.ModelAdmin):
    list_display = ('event', 'user', 'presentation_order', 'status', 'started_at', 'completed_at', 'duration_seconds')
    list_filter = ('status', 'event')
    search_fields = ('user__username', 'event__title')
    readonly_fields = ('created_at', 'updated_at', 'duration_seconds')
    ordering = ['event', 'presentation_order']

    fieldsets = (
        ('Presentation Details', {
            'fields': ('event', 'user', 'presentation_order', 'status')
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at', 'duration_seconds')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at')
        }),
    )


class PresentationRatingAdmin(admin.ModelAdmin):
    list_display = ('event', 'presenter', 'rater', 'rating', 'rated_at')
    list_filter = ('rating', 'event')
    search_fields = ('presenter__username', 'rater__username', 'event__title')
    readonly_fields = ('rated_at',)
    ordering = ['-rated_at']

    fieldsets = (
        ('Rating Details', {
            'fields': ('event', 'presenter', 'rater', 'rating')
        }),
        ('Metadata', {
            'fields': ('rated_at',)
        }),
    )


class SpeedDatingPairAdmin(admin.ModelAdmin):
    list_display = ('event', 'round_number', 'user1', 'user2', 'mutual_rating_score', 'is_top_match', 'duration_minutes')
    list_filter = ('is_top_match', 'event', 'round_number')
    search_fields = ('user1__username', 'user2__username', 'event__title')
    readonly_fields = ('created_at', 'duration_minutes')
    ordering = ['event', 'round_number']

    fieldsets = (
        ('Pairing Details', {
            'fields': ('event', 'user1', 'user2', 'round_number')
        }),
        ('Matching Data', {
            'fields': ('mutual_rating_score', 'is_top_match', 'duration_minutes')
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at')
        }),
        ('Metadata', {
            'fields': ('created_at',)
        }),
    )


# ============================================================================
# INTERACTIVE JOURNEY SYSTEM - ADMIN INTERFACES
# ============================================================================

# Inline Admins for nested management
class JourneyChallengeInline(admin.TabularInline):
    model = JourneyChallenge
    extra = 0
    fields = ('challenge_order', 'challenge_type', 'question', 'points_awarded')
    show_change_link = True
    ordering = ['challenge_order']


class JourneyRewardInline(admin.StackedInline):
    model = JourneyReward
    extra = 0
    fields = ('reward_type', 'title', 'photo', 'audio_file', 'video_file')
    show_change_link = True


class JourneyChapterInline(admin.TabularInline):
    model = JourneyChapter
    extra = 0
    fields = ('chapter_number', 'title', 'theme', 'background_theme', 'difficulty')
    show_change_link = True
    ordering = ['chapter_number']


class ChapterProgressInline(admin.TabularInline):
    model = ChapterProgress
    extra = 0
    fields = ('chapter', 'is_completed', 'points_earned', 'time_spent_seconds', 'completed_at')
    readonly_fields = ('started_at', 'completed_at')
    can_delete = False


class ChallengeAttemptInline(admin.TabularInline):
    model = ChallengeAttempt
    extra = 0
    fields = ('challenge', 'user_answer', 'is_correct', 'points_earned', 'attempted_at')
    readonly_fields = ('attempted_at',)
    can_delete = False
    ordering = ['-attempted_at']


# ============================================================================
# JOURNEY SYSTEM - Content Configuration (Admin Creates Journey)
# ============================================================================


class JourneyConfigurationAdmin(admin.ModelAdmin):
    """
    🗺️ JOURNEY CONFIGURATION - Create the Journey Structure

    Start here to create a new personalized journey experience.
    Define chapters, challenges, and rewards for a specific user.
    """
    list_display = (
        'journey_name', 'get_user_name', 'journey_type', 'is_active',
        'total_chapters', 'estimated_duration_minutes',
        'certificate_enabled', 'created_at'
    )
    list_filter = ('journey_type', 'is_active', 'certificate_enabled', 'created_at')
    search_fields = (
        'journey_name', 'special_experience__first_name',
        'special_experience__last_name', 'final_message'
    )
    readonly_fields = ('created_at', 'updated_at')
    inlines = [JourneyChapterInline]
    actions = ['activate_journeys', 'deactivate_journeys', 'duplicate_journey']

    fieldsets = (
        ('🎯 Journey Basics', {
            'fields': ('special_experience', 'journey_type', 'is_active', 'journey_name'),
            'description': 'Link this journey to a Special User Experience. Select "Advent Calendar" for December experiences.'
        }),
        ('📊 Journey Metadata', {
            'fields': ('total_chapters', 'estimated_duration_minutes')
        }),
        ('💝 Personalization Data', {
            'fields': ('date_first_met', 'location_first_met'),
            'description': 'Personal facts used in riddles and challenges'
        }),
        ('🏆 Completion Settings', {
            'fields': ('certificate_enabled', 'final_message'),
            'description': 'What happens when the journey is completed'
        }),
        ('🗓️ Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_user_name(self, obj):
        """Display the target user's name"""
        return f"{obj.special_experience.first_name} {obj.special_experience.last_name}"
    get_user_name.short_description = 'For User'
    get_user_name.admin_order_field = 'special_experience__first_name'

    @admin.action(description='✅ Activate selected journeys')
    def activate_journeys(self, request, queryset):
        updated = queryset.update(is_active=True)
        django_messages.success(request, f"Activated {updated} journey(s)")

    @admin.action(description='❌ Deactivate selected journeys')
    def deactivate_journeys(self, request, queryset):
        updated = queryset.update(is_active=False)
        django_messages.success(request, f"Deactivated {updated} journey(s)")

    @admin.action(description='📋 Duplicate journey (create copy)')
    def duplicate_journey(self, request, queryset):
        """Clone a journey for reuse with another user"""
        if queryset.count() != 1:
            django_messages.error(request, "Please select exactly one journey to duplicate")
            return

        original = queryset.first()
        # Note: Actual duplication logic would be implemented here
        django_messages.info(
            request,
            f"Duplication feature coming soon! Would create a copy of '{original.journey_name}'"
        )


class JourneyChapterAdmin(admin.ModelAdmin):
    """
    📖 JOURNEY CHAPTERS - Structure the Journey

    Each chapter represents a section of the journey with multiple challenges.
    """
    list_display = (
        'get_chapter_display', 'journey', 'title', 'theme',
        'background_theme', 'difficulty', 'estimated_duration',
        'get_challenge_count', 'get_reward_count'
    )
    list_filter = ('difficulty', 'background_theme', 'journey')
    search_fields = ('title', 'theme', 'story_introduction', 'journey__journey_name')
    readonly_fields = ('get_challenge_count', 'get_reward_count')
    inlines = [JourneyChallengeInline, JourneyRewardInline]
    ordering = ['journey', 'chapter_number']

    fieldsets = (
        ('🔢 Chapter Identity', {
            'fields': ('journey', 'chapter_number', 'title', 'theme')
        }),
        ('📖 Story & Theme', {
            'fields': ('story_introduction', 'background_theme')
        }),
        ('⚙️ Settings', {
            'fields': ('estimated_duration', 'difficulty', 'requires_previous_completion')
        }),
        ('💬 Completion Message', {
            'fields': ('completion_message',),
            'description': 'Personal message shown after completing all challenges'
        }),
        ('📊 Statistics', {
            'fields': ('get_challenge_count', 'get_reward_count'),
            'classes': ('collapse',)
        }),
    )

    def get_chapter_display(self, obj):
        return f"Chapter {obj.chapter_number}"
    get_chapter_display.short_description = 'Chapter #'
    get_chapter_display.admin_order_field = 'chapter_number'

    def get_challenge_count(self, obj):
        return obj.challenges.count()
    get_challenge_count.short_description = '🎯 Challenges'

    def get_reward_count(self, obj):
        return obj.rewards.count()
    get_reward_count.short_description = '🎁 Rewards'


class JourneyChallengeAdmin(admin.ModelAdmin):
    """
    🎯 JOURNEY CHALLENGES - Add Interactive Activities

    Create riddles, quizzes, word scrambles, and more.
    Questionnaire mode (blank correct_answer) saves all responses for analysis.
    """
    list_display = (
        'get_chapter_display', 'challenge_order', 'challenge_type',
        'get_question_preview', 'points_awarded', 'has_hints'
    )
    list_filter = ('challenge_type', 'chapter__journey', 'chapter__chapter_number')
    search_fields = ('question', 'correct_answer', 'success_message')
    ordering = ['chapter__chapter_number', 'challenge_order']

    fieldsets = (
        ('📍 Challenge Location', {
            'fields': ('chapter', 'challenge_order', 'challenge_type')
        }),
        ('❓ Challenge Content', {
            'fields': ('question', 'options', 'correct_answer', 'alternative_answers'),
            'description': '''
                <strong>For Quiz Challenges:</strong> Set correct_answer and optionally alternative_answers.<br>
                <strong>For Questionnaires:</strong> Leave correct_answer blank - all answers are saved for analysis.<br>
                <em>Questionnaire types: open_text, would_you_rather, or any challenge in Chapters 2, 4, 5</em>
            '''
        }),
        ('💡 Hints System', {
            'fields': (
                ('hint_1', 'hint_1_cost'),
                ('hint_2', 'hint_2_cost'),
                ('hint_3', 'hint_3_cost'),
            ),
            'classes': ('collapse',)
        }),
        ('🏆 Scoring & Feedback', {
            'fields': ('points_awarded', 'success_message')
        }),
    )

    def get_chapter_display(self, obj):
        return f"Ch{obj.chapter.chapter_number}: {obj.chapter.title}"
    get_chapter_display.short_description = 'Chapter'

    def get_question_preview(self, obj):
        return obj.question[:50] + '...' if len(obj.question) > 50 else obj.question
    get_question_preview.short_description = 'Question Preview'

    def has_hints(self, obj):
        return bool(obj.hint_1 or obj.hint_2 or obj.hint_3)
    has_hints.boolean = True
    has_hints.short_description = 'Has Hints?'


class JourneyRewardAdmin(admin.ModelAdmin):
    """
    🎁 JOURNEY REWARDS - Special Surprises & Media

    Upload photos, videos, audio messages, and letters as rewards.
    Photo reveals use jigsaw puzzles that cost points to unlock.
    """
    list_display = (
        'get_chapter_display', 'title', 'reward_type',
        'has_photo', 'has_audio', 'has_video'
    )
    list_filter = ('reward_type', 'chapter__journey', 'chapter__chapter_number')
    search_fields = ('title', 'message')
    ordering = ['chapter__chapter_number']

    fieldsets = (
        ('📍 Reward Location', {
            'fields': ('chapter', 'reward_type', 'title')
        }),
        ('📝 Content', {
            'fields': ('message',)
        }),
        ('🖼️ Media Files', {
            'fields': ('photo', 'audio_file', 'video_file'),
            'description': 'Upload photos, audio recordings, or video messages'
        }),
        ('🧩 Puzzle Settings', {
            'fields': ('puzzle_pieces',),
            'description': 'For photo_reveal type: number of jigsaw pieces'
        }),
    )

    def get_chapter_display(self, obj):
        return f"Ch{obj.chapter.chapter_number}: {obj.chapter.title}"
    get_chapter_display.short_description = 'Chapter'

    def has_photo(self, obj):
        return bool(obj.photo)
    has_photo.boolean = True
    has_photo.short_description = '📷 Photo'

    def has_audio(self, obj):
        return bool(obj.audio_file)
    has_audio.boolean = True
    has_audio.short_description = '🎵 Audio'

    def has_video(self, obj):
        return bool(obj.video_file)
    has_video.boolean = True
    has_video.short_description = '🎬 Video'


# ============================================================================
# JOURNEY SYSTEM - User Progress Tracking (User Experience Data)
# ============================================================================


class JourneyProgressAdmin(admin.ModelAdmin):
    """
    📊 JOURNEY PROGRESS - Track User Experience

    Monitor how users progress through their personalized journey.
    View completion rates, points earned, and time spent.
    """
    list_display = (
        'user', 'get_journey_name', 'current_chapter',
        'get_completion_pct', 'total_points', 'get_time_spent',
        'is_completed', 'final_response', 'last_activity'
    )
    list_filter = ('is_completed', 'final_response', 'started_at', 'completed_at')
    search_fields = (
        'user__username', 'user__email', 'user__first_name', 'user__last_name',
        'journey__journey_name'
    )
    readonly_fields = (
        'started_at', 'last_activity', 'completed_at',
        'get_completion_pct', 'get_time_spent'
    )
    inlines = [ChapterProgressInline]
    ordering = ['-last_activity']

    fieldsets = (
        ('👤 User & Journey', {
            'fields': ('user', 'journey')
        }),
        ('📊 Progress Tracking', {
            'fields': (
                'current_chapter', 'get_completion_pct',
                'total_points', 'get_time_spent'
            )
        }),
        ('✅ Completion Status', {
            'fields': ('is_completed', 'completed_at')
        }),
        ('💖 Final Response', {
            'fields': ('final_response', 'final_response_at'),
            'description': 'User\'s response to the final chapter reveal'
        }),
        ('🗓️ Timestamps', {
            'fields': ('started_at', 'last_activity'),
            'classes': ('collapse',)
        }),
    )

    def get_journey_name(self, obj):
        return obj.journey.journey_name
    get_journey_name.short_description = 'Journey'

    def get_completion_pct(self, obj):
        pct = obj.completion_percentage
        if pct == 100:
            return f"✅ {pct}%"
        elif pct >= 75:
            return f"🟢 {pct}%"
        elif pct >= 50:
            return f"🟡 {pct}%"
        else:
            return f"🔴 {pct}%"
    get_completion_pct.short_description = 'Completion'

    def get_time_spent(self, obj):
        """Convert seconds to human-readable format"""
        seconds = obj.total_time_seconds
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    get_time_spent.short_description = 'Time Spent'


class ChapterProgressAdmin(admin.ModelAdmin):
    """
    📈 CHAPTER PROGRESS - Detailed Chapter Tracking

    See how users progress through each chapter and their scores.
    """
    list_display = (
        'get_user', 'get_chapter_display', 'is_completed',
        'points_earned', 'get_time_spent', 'started_at', 'completed_at'
    )
    list_filter = ('is_completed', 'chapter__chapter_number', 'started_at', 'completed_at')
    search_fields = (
        'journey_progress__user__username',
        'chapter__title',
        'journey_progress__journey__journey_name'
    )
    readonly_fields = ('started_at', 'completed_at', 'get_time_spent')
    inlines = [ChallengeAttemptInline]
    ordering = ['journey_progress__user', 'chapter__chapter_number']

    fieldsets = (
        ('📖 Chapter Info', {
            'fields': ('journey_progress', 'chapter')
        }),
        ('📊 Progress', {
            'fields': ('is_completed', 'points_earned', 'time_spent_seconds', 'get_time_spent')
        }),
        ('🗓️ Timestamps', {
            'fields': ('started_at', 'completed_at')
        }),
    )

    def get_user(self, obj):
        return obj.journey_progress.user.username
    get_user.short_description = 'User'

    def get_chapter_display(self, obj):
        return f"Ch{obj.chapter.chapter_number}: {obj.chapter.title}"
    get_chapter_display.short_description = 'Chapter'

    def get_time_spent(self, obj):
        """Convert seconds to human-readable format"""
        seconds = obj.time_spent_seconds
        minutes = seconds // 60
        return f"{minutes}m {seconds % 60}s"
    get_time_spent.short_description = 'Duration'


class ChallengeAttemptAdmin(admin.ModelAdmin):
    """
    🎮 CHALLENGE ATTEMPTS - User Answers & Responses

    View all user answers to challenges. Export questionnaire responses to CSV.
    """
    list_display = (
        'get_user', 'get_chapter', 'get_challenge_display', 'is_correct',
        'points_earned', 'get_hints_count', 'attempted_at'
    )
    list_filter = (
        'is_correct', 'attempted_at', 'challenge__challenge_type',
        'challenge__chapter__chapter_number'
    )
    search_fields = (
        'chapter_progress__journey_progress__user__username',
        'challenge__question',
        'user_answer'
    )
    readonly_fields = ('attempted_at',)
    ordering = ['-attempted_at']
    actions = ['export_chapter2_responses']

    fieldsets = (
        ('🎯 Attempt Details', {
            'fields': ('chapter_progress', 'challenge', 'is_correct', 'points_earned')
        }),
        ('📝 User Response', {
            'fields': ('user_answer',)
        }),
        ('💡 Hints Used', {
            'fields': ('hints_used',)
        }),
        ('🗓️ Timestamp', {
            'fields': ('attempted_at',)
        }),
    )

    def get_user(self, obj):
        return obj.chapter_progress.journey_progress.user.username
    get_user.short_description = 'User'

    def get_chapter(self, obj):
        return f"Ch.{obj.challenge.chapter.chapter_number}"
    get_chapter.short_description = 'Chapter'

    def get_challenge_display(self, obj):
        return f"{obj.challenge.get_challenge_type_display()}"
    get_challenge_display.short_description = 'Challenge Type'

    def get_hints_count(self, obj):
        return len(obj.hints_used) if obj.hints_used else 0
    get_hints_count.short_description = '💡 Hints Used'

    def export_chapter2_responses(self, request, queryset):
        """Export Chapter 2 questionnaire responses as CSV"""
        import csv
        from django.http import HttpResponse
        from datetime import datetime

        # Filter for Chapter 2 only
        chapter2_attempts = queryset.filter(challenge__chapter__chapter_number=2).select_related(
            'chapter_progress__journey_progress__user',
            'challenge'
        ).order_by('chapter_progress__journey_progress__user', 'challenge__challenge_order')

        if not chapter2_attempts.exists():
            self.message_user(
                request,
                "No Chapter 2 responses found in selected items.",
                level=django_messages.WARNING
            )
            return

        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="chapter2_responses_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'

        writer = csv.writer(response)
        # Header row
        writer.writerow([
            'User',
            'Question',
            'User Answer',
            'Option Selected',
            'Points Earned',
            'Submitted At'
        ])

        # Data rows
        for attempt in chapter2_attempts:
            writer.writerow([
                attempt.chapter_progress.journey_progress.user.username,
                attempt.challenge.question,
                attempt.user_answer,
                f"Option {attempt.user_answer}",  # Shows which option letter was chosen
                attempt.points_earned,
                attempt.attempted_at.strftime('%Y-%m-%d %H:%M:%S')
            ])

        self.message_user(
            request,
            f"Exported {chapter2_attempts.count()} Chapter 2 responses.",
            level=django_messages.SUCCESS
        )

        return response

    export_chapter2_responses.short_description = "📊 Export Chapter 2 Questionnaire Responses (CSV)"


class RewardProgressAdmin(admin.ModelAdmin):
    """
    🏆 REWARD PROGRESS - Puzzle & Interactive Reward Tracking

    Track which jigsaw puzzle pieces users have unlocked and points spent.
    """
    list_display = (
        'get_user', 'get_reward', 'get_pieces_unlocked',
        'points_spent', 'is_completed', 'started_at'
    )
    list_filter = ('is_completed', 'reward__reward_type', 'started_at')
    search_fields = (
        'journey_progress__user__username',
        'reward__title'
    )
    readonly_fields = ('started_at', 'completed_at')

    def get_user(self, obj):
        return obj.journey_progress.user.username
    get_user.short_description = 'User'

    def get_reward(self, obj):
        return f"{obj.reward.title} (Ch{obj.reward.chapter.chapter_number})"
    get_reward.short_description = 'Reward'

    def get_pieces_unlocked(self, obj):
        total = 16  # Standard jigsaw puzzle size
        unlocked = len(obj.unlocked_pieces)
        return f"{unlocked}/{total}"
    get_pieces_unlocked.short_description = 'Progress'


# ============================================================================
# ADVENT CALENDAR SYSTEM - December Experience
# ============================================================================

# Inline admin for AdventDoorContent within AdventDoor
class AdventDoorContentInline(admin.StackedInline):
    model = AdventDoorContent
    extra = 0
    can_delete = True
    fields = (
        'title', 'message',
        'photo', 'bonus_photo',
        'video_file', 'audio_file',
        'bonus_title', 'bonus_content'
    )


# Inline admin for AdventDoor within AdventCalendar
class AdventDoorInline(admin.TabularInline):
    model = AdventDoor
    extra = 0
    fields = ('door_number', 'content_type', 'qr_mode', 'teaser_text', 'door_color', 'door_icon')
    ordering = ['door_number']
    show_change_link = True


# Inline admin for QRCodeToken within AdventDoor
class QRCodeTokenInline(admin.TabularInline):
    model = QRCodeToken
    extra = 0
    fields = ('user', 'token', 'is_used', 'used_at', 'expires_at', 'created_at')
    readonly_fields = ('token', 'is_used', 'used_at', 'created_at')
    can_delete = True


class AdventCalendarAdmin(admin.ModelAdmin):
    """
    🎄 ADVENT CALENDAR - December Experience Configuration

    Create and manage Advent Calendar experiences linked to users via JourneyConfiguration.
    Each calendar has 24 doors with personalized content.
    """
    list_display = (
        'calendar_title', 'get_user_name', 'year',
        'get_door_count', 'get_progress_count',
        'is_december_active', 'created_at'
    )
    list_filter = ('year', 'created_at')
    search_fields = (
        'calendar_title', 'calendar_description',
        'journey__special_experience__first_name',
        'journey__special_experience__last_name'
    )
    readonly_fields = ('created_at', 'updated_at', 'get_door_count', 'get_progress_count', 'is_december_active')
    inlines = [AdventDoorInline]

    def get_fieldsets(self, request, obj=None):
        """Hide statistics section on add form (no object yet)"""
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None:
            # Remove statistics section for new objects
            return [fs for fs in fieldsets if fs[0] != '📊 Statistics']
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        """Only show computed fields for existing objects"""
        if obj is None:
            return ('created_at', 'updated_at')
        return self.readonly_fields
    actions = ['create_default_doors']

    fieldsets = (
        ('🎄 Calendar Basics', {
            'fields': ('journey', 'calendar_title', 'year'),
            'description': 'Link this calendar to a JourneyConfiguration (which links to a SpecialUserExperience)'
        }),
        ('📅 Date Configuration', {
            'fields': ('start_date', 'end_date', 'allow_catch_up', 'timezone_name'),
            'description': 'Configure calendar dates and timezone'
        }),
        ('📝 Description', {
            'fields': ('calendar_description',),
            'description': 'Description shown on the calendar page'
        }),
        ('🎨 Theme Settings', {
            'fields': ('background_image',),
            'classes': ('collapse',),
        }),
        ('📊 Statistics', {
            'fields': ('get_door_count', 'get_progress_count', 'is_december_active'),
            'classes': ('collapse',),
        }),
        ('🗓️ Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_user_name(self, obj):
        """Display the target user's name from the linked journey"""
        try:
            return f"{obj.journey.special_experience.first_name} {obj.journey.special_experience.last_name}"
        except Exception:
            return "N/A"
    get_user_name.short_description = 'For User'

    def get_door_count(self, obj):
        """Count doors created for this calendar"""
        count = obj.doors.count()
        if count == 24:
            return f"✅ {count}/24"
        else:
            return f"⚠️ {count}/24"
    get_door_count.short_description = '🚪 Doors'

    def get_progress_count(self, obj):
        """Count users with progress on this calendar"""
        return obj.user_progress.count()
    get_progress_count.short_description = '👥 Users'

    def is_december_active(self, obj):
        """Check if currently December"""
        return obj.is_december()
    is_december_active.boolean = True
    is_december_active.short_description = '🎄 December Active'

    @admin.action(description='🚪 Create 24 default doors')
    def create_default_doors(self, request, queryset):
        """Create 24 default doors for selected calendars"""
        for calendar in queryset:
            existing_doors = set(calendar.doors.values_list('door_number', flat=True))
            created = 0

            for day in range(1, 25):
                if day not in existing_doors:
                    AdventDoor.objects.create(
                        calendar=calendar,
                        door_number=day,
                        content_type='poem' if day % 3 == 0 else 'memory',
                        qr_mode='none',
                        teaser_text=f'Day {day} surprise awaits...',
                    )
                    created += 1

            django_messages.success(request, f"Created {created} doors for '{calendar.calendar_title}'")


class AdventDoorAdmin(admin.ModelAdmin):
    """
    🚪 ADVENT DOOR - Individual Door Configuration

    Configure each of the 24 doors with content type, QR requirements, and theming.
    When content_type='challenge', you can select one of 11 Wonderland challenge types.
    """
    list_display = (
        'get_door_display', 'calendar', 'content_type', 'get_challenge_type_display',
        'qr_mode', 'has_content', 'has_qr_tokens', 'door_color'
    )
    list_filter = ('content_type', 'challenge_type', 'qr_mode', 'calendar', 'door_number')
    search_fields = ('teaser_text', 'calendar__calendar_title')
    readonly_fields = ('has_content', 'has_qr_tokens')
    inlines = [AdventDoorContentInline, QRCodeTokenInline]
    ordering = ['calendar', 'door_number']

    fieldsets = (
        ('🚪 Door Identity', {
            'fields': ('calendar', 'door_number'),
        }),
        ('📦 Content Configuration', {
            'fields': ('content_type', 'challenge_type', 'teaser_text'),
            'description': (
                'Content type determines what the door contains. '
                'If "Interactive Challenge" is selected, choose a challenge type below.'
            )
        }),
        ('📱 QR Code Settings', {
            'fields': ('qr_mode',),
            'description': 'none = no QR, required = must scan to open, bonus = extra content after scan'
        }),
        ('🎨 Visual Theming', {
            'fields': ('door_color', 'door_icon'),
            'classes': ('collapse',),
        }),
        ('📊 Status', {
            'fields': ('has_content', 'has_qr_tokens'),
            'classes': ('collapse',),
        }),
    )

    class Media:
        js = ('admin/js/advent_door_admin.js',)  # For dynamic challenge_type visibility

    def get_door_display(self, obj):
        return f"Door {obj.door_number}"
    get_door_display.short_description = 'Door #'
    get_door_display.admin_order_field = 'door_number'

    def get_challenge_type_display(self, obj):
        """Show challenge type only for challenge doors"""
        if obj.content_type == 'challenge' and obj.challenge_type:
            return obj.get_challenge_type_display()
        return '—'
    get_challenge_type_display.short_description = '🎯 Challenge Type'

    def has_content(self, obj):
        """Check if door has content configured"""
        try:
            return obj.content is not None
        except AdventDoorContent.DoesNotExist:
            return False
    has_content.boolean = True
    has_content.short_description = '📦 Has Content'

    def has_qr_tokens(self, obj):
        """Check if QR tokens exist for this door"""
        return obj.qr_tokens.exists()
    has_qr_tokens.boolean = True
    has_qr_tokens.short_description = '📱 Has QR Tokens'


class AdventDoorContentAdmin(admin.ModelAdmin):
    """
    📦 ADVENT DOOR CONTENT - The Actual Content Behind Doors

    Create personalized content for each door: poems, photos, memories, challenges, etc.
    For challenge doors: configure interactive challenges using the same 11 types as Wonderland.
    Bonus content is only shown after QR scan.
    """
    list_display = (
        'get_door_display', 'get_calendar', 'get_content_type', 'has_title',
        'has_challenge', 'has_bonus', 'has_media'
    )
    list_filter = ('door__content_type', 'door__challenge_type', 'door__calendar')
    search_fields = ('title', 'message', 'challenge_question', 'bonus_content', 'door__calendar__calendar_title')

    fieldsets = (
        ('🚪 Door Reference', {
            'fields': ('door',),
        }),
        ('📝 Primary Content', {
            'fields': ('title', 'message', 'photo', 'video_file', 'audio_file'),
            'description': 'Main content shown when door is opened'
        }),
        ('🎯 Challenge Configuration (for Interactive Challenge doors)', {
            'fields': (
                'challenge_question',
                'challenge_options',
                'challenge_correct_answer',
                'challenge_alternative_answers',
                'success_message',
                'points_awarded',
            ),
            'description': (
                'Configure interactive challenges here. '
                'Leave correct_answer blank for questionnaire mode (all answers accepted). '
                'Options format: {"A": "option1", "B": "option2"}'
            ),
            'classes': ('collapse',),
        }),
        ('💡 Challenge Hints', {
            'fields': (
                ('hint_1', 'hint_1_cost'),
                ('hint_2', 'hint_2_cost'),
                ('hint_3', 'hint_3_cost'),
            ),
            'description': 'Optional hints that cost points to reveal',
            'classes': ('collapse',),
        }),
        ('🎁 Bonus Content (QR Unlock)', {
            'fields': ('bonus_title', 'bonus_content', 'bonus_photo'),
            'description': 'Extra content revealed after scanning QR code'
        }),
        ('🎁 Physical Gift', {
            'fields': ('gift_hint', 'gift_location_clue'),
            'description': 'Hints for physical gift doors',
            'classes': ('collapse',),
        }),
    )

    def get_door_display(self, obj):
        return f"Door {obj.door.door_number}"
    get_door_display.short_description = 'Door #'

    def get_calendar(self, obj):
        return obj.door.calendar.calendar_title
    get_calendar.short_description = 'Calendar'

    def get_content_type(self, obj):
        """Display content type with challenge type if applicable"""
        content_type = obj.door.get_content_type_display()
        if obj.door.content_type == 'challenge' and obj.door.challenge_type:
            return f"{content_type} ({obj.door.get_challenge_type_display()})"
        return content_type
    get_content_type.short_description = 'Type'

    def has_title(self, obj):
        return bool(obj.title)
    has_title.boolean = True
    has_title.short_description = '📝 Title'

    def has_challenge(self, obj):
        """Check if challenge content is configured"""
        return bool(obj.challenge_question)
    has_challenge.boolean = True
    has_challenge.short_description = '🎯 Challenge'

    def has_bonus(self, obj):
        return bool(obj.bonus_content or obj.bonus_photo or obj.bonus_title)
    has_bonus.boolean = True
    has_bonus.short_description = '🎁 Bonus'

    def has_media(self, obj):
        return bool(obj.photo or obj.video_file or obj.audio_file)
    has_media.boolean = True
    has_media.short_description = '🎬 Media'


class AdventProgressAdmin(admin.ModelAdmin):
    """
    📊 ADVENT PROGRESS - Track User Progress Through Calendar

    Monitor which doors users have opened and which QR codes they've scanned.
    """
    list_display = (
        'user', 'calendar', 'get_doors_opened', 'get_qr_scans',
        'completion_percentage', 'first_visit', 'last_visit'
    )
    list_filter = ('calendar', 'first_visit', 'last_visit')
    search_fields = ('user__username', 'user__email', 'calendar__calendar_title')
    readonly_fields = ('first_visit', 'last_visit', 'completion_percentage')

    fieldsets = (
        ('👤 User & Calendar', {
            'fields': ('user', 'calendar'),
        }),
        ('🚪 Progress', {
            'fields': ('doors_opened', 'qr_scans', 'last_door_opened', 'last_opened_at', 'completion_percentage'),
            'description': 'JSON arrays tracking which doors were opened and QR codes scanned'
        }),
        ('🗓️ Activity Timestamps', {
            'fields': ('first_visit', 'last_visit'),
        }),
    )

    def get_doors_opened(self, obj):
        count = len(obj.doors_opened or [])
        return f"{count}/24"
    get_doors_opened.short_description = '🚪 Doors Opened'

    def get_qr_scans(self, obj):
        count = len(obj.qr_scans or [])
        return f"{count} scans"
    get_qr_scans.short_description = '📱 QR Scans'


class QRCodeTokenAdmin(admin.ModelAdmin):
    """
    📱 QR CODE TOKENS - Physical Gift Unlock Codes

    Generate and manage QR codes for physical gifts. Each token is unique per user per door.
    """
    list_display = (
        'get_door_display', 'user', 'get_token_short',
        'is_used', 'used_at', 'is_valid_display', 'expires_at', 'created_at'
    )
    list_filter = ('is_used', 'door__calendar', 'door__door_number', 'created_at')
    search_fields = (
        'token', 'user__username', 'user__email',
        'door__calendar__calendar_title'
    )
    readonly_fields = ('token', 'is_used', 'used_at', 'created_at', 'is_valid_display', 'get_qr_url')
    actions = ['generate_tokens_for_all_doors', 'regenerate_tokens']

    fieldsets = (
        ('🔗 Token Details', {
            'fields': ('door', 'user', 'token', 'get_qr_url'),
        }),
        ('📊 Status', {
            'fields': ('is_used', 'used_at', 'is_valid_display'),
        }),
        ('⏰ Expiration', {
            'fields': ('expires_at',),
            'description': 'Leave blank for no expiration'
        }),
        ('🗓️ Created', {
            'fields': ('created_at',),
        }),
    )

    def get_door_display(self, obj):
        return f"Door {obj.door.door_number}"
    get_door_display.short_description = 'Door'

    def get_token_short(self, obj):
        """Display shortened token for list view"""
        return f"{str(obj.token)[:8]}..."
    get_token_short.short_description = 'Token'

    def is_valid_display(self, obj):
        return obj.is_valid()
    is_valid_display.boolean = True
    is_valid_display.short_description = '✅ Valid'

    def get_qr_url(self, obj):
        """Display the URL that should be encoded in the QR code"""
        if obj.token:
            url = f"https://crush.lu/advent/qr/{obj.token}/"
            return format_html(
                '<div style="padding: 10px; background: #f8f9fa; border-radius: 5px;">'
                '<strong>QR Code URL:</strong><br>'
                '<code style="font-size: 12px; word-break: break-all;">{}</code>'
                '</div>',
                url
            )
        return "N/A"
    get_qr_url.short_description = 'QR URL'

    @admin.action(description='🔄 Regenerate tokens (creates new UUIDs)')
    def regenerate_tokens(self, request, queryset):
        """Regenerate tokens for selected entries"""
        import uuid
        count = 0
        for token in queryset:
            if not token.is_used:
                token.token = uuid.uuid4()
                token.save()
                count += 1

        django_messages.success(request, f"Regenerated {count} token(s). Used tokens were skipped.")

    @admin.action(description='📱 Generate tokens for all doors (selected users)')
    def generate_tokens_for_all_doors(self, request, queryset):
        """Generate QR tokens for all 24 doors for selected user-calendar combinations"""
        import uuid

        # Get unique user-calendar pairs from selected tokens
        created = 0
        for token in queryset:
            user = token.user
            calendar = token.door.calendar

            # Create tokens for all doors
            for door in calendar.doors.all():
                if not QRCodeToken.objects.filter(door=door, user=user).exists():
                    QRCodeToken.objects.create(
                        door=door,
                        user=user,
                        token=uuid.uuid4()
                    )
                    created += 1

        django_messages.success(request, f"Generated {created} new QR token(s)")


# ============================================================================
# END ADVENT CALENDAR SYSTEM
# ============================================================================


# ============================================================================
# REGISTER ALL MODELS WITH CUSTOM ADMIN SITE
# ============================================================================

crush_admin_site.register(SpecialUserExperience, SpecialUserExperienceAdmin)
crush_admin_site.register(CrushCoach, CrushCoachAdmin)
crush_admin_site.register(CrushProfile, CrushProfileAdmin)
crush_admin_site.register(ProfileSubmission, ProfileSubmissionAdmin)
crush_admin_site.register(CoachSession, CoachSessionAdmin)
crush_admin_site.register(MeetupEvent, MeetupEventAdmin)
crush_admin_site.register(EventRegistration, EventRegistrationAdmin)
crush_admin_site.register(EventInvitation, EventInvitationAdmin)
crush_admin_site.register(EventConnection, EventConnectionAdmin)
crush_admin_site.register(ConnectionMessage, ConnectionMessageAdmin)
crush_admin_site.register(GlobalActivityOption, GlobalActivityOptionAdmin)
crush_admin_site.register(EventActivityOption, EventActivityOptionAdmin)
crush_admin_site.register(EventActivityVote, EventActivityVoteAdmin)
crush_admin_site.register(EventVotingSession, EventVotingSessionAdmin)
crush_admin_site.register(PresentationQueue, PresentationQueueAdmin)
crush_admin_site.register(PresentationRating, PresentationRatingAdmin)
crush_admin_site.register(SpeedDatingPair, SpeedDatingPairAdmin)
crush_admin_site.register(JourneyConfiguration, JourneyConfigurationAdmin)
crush_admin_site.register(JourneyChapter, JourneyChapterAdmin)
crush_admin_site.register(JourneyChallenge, JourneyChallengeAdmin)
crush_admin_site.register(JourneyReward, JourneyRewardAdmin)
crush_admin_site.register(JourneyProgress, JourneyProgressAdmin)
crush_admin_site.register(ChapterProgress, ChapterProgressAdmin)
crush_admin_site.register(ChallengeAttempt, ChallengeAttemptAdmin)
crush_admin_site.register(RewardProgress, RewardProgressAdmin)

# Advent Calendar System
crush_admin_site.register(AdventCalendar, AdventCalendarAdmin)
crush_admin_site.register(AdventDoor, AdventDoorAdmin)
crush_admin_site.register(AdventDoorContent, AdventDoorContentAdmin)
crush_admin_site.register(AdventProgress, AdventProgressAdmin)
crush_admin_site.register(QRCodeToken, QRCodeTokenAdmin)

# ============================================================================
# END INTERACTIVE JOURNEY SYSTEM - ADMIN INTERFACES
# ============================================================================


# ============================================================================
# PUSH NOTIFICATIONS ADMIN
# ============================================================================

@admin.register(PushSubscription, site=crush_admin_site)
class PushSubscriptionAdmin(admin.ModelAdmin):
    """
    🔔 PUSH SUBSCRIPTION MANAGEMENT

    View and manage user push notification subscriptions.
    Each user can have multiple subscriptions (different devices).
    """
    list_display = (
        'user', 'device_name', 'enabled', 'created_at',
        'last_used_at', 'failure_count', 'get_preferences'
    )
    list_filter = (
        'enabled', 'created_at', 'failure_count',
        'notify_new_messages', 'notify_event_reminders',
        'notify_new_connections', 'notify_profile_updates'
    )
    search_fields = ('user__username', 'user__email', 'device_name', 'endpoint')
    readonly_fields = (
        'endpoint', 'p256dh_key', 'auth_key', 'user_agent',
        'created_at', 'updated_at', 'last_used_at', 'failure_count'
    )
    date_hierarchy = 'created_at'

    fieldsets = (
        ('User & Device', {
            'fields': ('user', 'device_name', 'user_agent')
        }),
        ('Subscription Details', {
            'fields': ('endpoint', 'p256dh_key', 'auth_key'),
            'classes': ('collapse',)
        }),
        ('Notification Preferences', {
            'fields': (
                'enabled',
                'notify_new_messages',
                'notify_event_reminders',
                'notify_new_connections',
                'notify_profile_updates',
            )
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at', 'last_used_at', 'failure_count')
        }),
    )

    def get_preferences(self, obj):
        """Display active notification types"""
        prefs = []
        if obj.notify_new_messages:
            prefs.append('Messages')
        if obj.notify_event_reminders:
            prefs.append('Events')
        if obj.notify_new_connections:
            prefs.append('Connections')
        if obj.notify_profile_updates:
            prefs.append('Profile')
        return ', '.join(prefs) if prefs else 'None'
    get_preferences.short_description = 'Active Notifications'

    actions = ['enable_subscriptions', 'disable_subscriptions', 'send_test_notification']

    def enable_subscriptions(self, request, queryset):
        """Enable selected subscriptions"""
        updated = queryset.update(enabled=True)
        self.message_user(
            request,
            f'{updated} subscription(s) enabled.',
            level=django_messages.SUCCESS
        )
    enable_subscriptions.short_description = '✅ Enable selected subscriptions'

    def disable_subscriptions(self, request, queryset):
        """Disable selected subscriptions"""
        updated = queryset.update(enabled=False)
        self.message_user(
            request,
            f'{updated} subscription(s) disabled.',
            level=django_messages.SUCCESS
        )
    disable_subscriptions.short_description = '🔕 Disable selected subscriptions'

    def send_test_notification(self, request, queryset):
        """Send test notification to selected subscriptions"""
        from .push_notifications import send_test_notification

        total = 0
        success = 0
        for subscription in queryset:
            result = send_test_notification(subscription.user)
            total += result.get('total', 0)
            success += result.get('success', 0)

        self.message_user(
            request,
            f'Sent test notifications: {success}/{total} successful.',
            level=django_messages.SUCCESS if success > 0 else django_messages.WARNING
        )
    send_test_notification.short_description = '📤 Send test notification'

# ============================================================================
# END PUSH NOTIFICATIONS ADMIN
# ============================================================================


# ============================================================================
# USER ACTIVITY TRACKING ADMIN
# ============================================================================

@admin.register(UserActivity, site=crush_admin_site)
class UserActivityAdmin(admin.ModelAdmin):
    """
    📊 USER ACTIVITY TRACKING

    Monitor user activity, online status, and PWA usage.
    """
    list_display = (
        'user', 'get_status', 'last_seen', 'get_pwa_status',
        'total_visits', 'is_active_user', 'minutes_since_last_seen'
    )
    list_filter = (
        'is_pwa_user', 'last_seen', 'first_seen'
    )
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('user', 'last_seen', 'last_pwa_visit', 'total_visits', 'first_seen')
    date_hierarchy = 'last_seen'

    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Activity Status', {
            'fields': ('last_seen', 'last_pwa_visit', 'total_visits')
        }),
        ('PWA Usage', {
            'fields': ('is_pwa_user',)
        }),
        ('Tracking Info', {
            'fields': ('first_seen',)
        }),
    )

    def get_status(self, obj):
        """Display online/offline status with icon"""
        if obj.is_online:
            return format_html('<span style="color: green;">🟢 Online</span>')
        elif obj.is_active_user:
            return format_html('<span style="color: orange;">🟡 Active ({})</span>', obj.minutes_since_last_seen)
        else:
            return format_html('<span style="color: gray;">⚫ Inactive</span>')
    get_status.short_description = 'Status'
    get_status.admin_order_field = 'last_seen'

    def get_pwa_status(self, obj):
        """Display PWA usage status"""
        if obj.uses_pwa:
            return format_html('<span style="color: purple;">📱 PWA User</span>')
        elif obj.is_pwa_user:
            return format_html('<span style="color: gray;">📱 PWA (Inactive)</span>')
        else:
            return format_html('<span style="color: gray;">🌐 Browser Only</span>')
    get_pwa_status.short_description = 'PWA Status'
    get_pwa_status.admin_order_field = 'is_pwa_user'

    def get_queryset(self, request):
        """Add computed fields for filtering"""
        qs = super().get_queryset(request)
        return qs.select_related('user')

# ============================================================================
# END USER ACTIVITY TRACKING ADMIN
# ============================================================================


# ============================================================================
# EMAIL PREFERENCES ADMIN
# ============================================================================

@admin.register(EmailPreference, site=crush_admin_site)
class EmailPreferenceAdmin(admin.ModelAdmin):
    """
    📧 EMAIL PREFERENCE MANAGEMENT

    View and manage user email notification preferences.
    Track unsubscribes and email category preferences.
    """
    list_display = (
        'user', 'get_email', 'get_email_status_icons', 'unsubscribed_all',
        'email_marketing', 'updated_at'
    )
    list_filter = (
        'unsubscribed_all', 'email_marketing',
        'email_profile_updates', 'email_event_reminders',
        'email_new_connections', 'email_new_messages',
        'created_at', 'updated_at'
    )
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('unsubscribe_token', 'created_at', 'updated_at', 'get_unsubscribe_link')
    date_hierarchy = 'updated_at'

    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('📧 Email Categories', {
            'fields': (
                'email_profile_updates',
                'email_event_reminders',
                'email_new_connections',
                'email_new_messages',
            ),
            'description': 'Control which types of emails the user receives'
        }),
        ('📢 Marketing', {
            'fields': ('email_marketing',),
            'description': 'Marketing emails require explicit opt-in (GDPR)'
        }),
        ('🔕 Master Switch', {
            'fields': ('unsubscribed_all',),
            'description': 'If enabled, user receives NO emails'
        }),
        ('🔗 Unsubscribe Link', {
            'fields': ('unsubscribe_token', 'get_unsubscribe_link'),
            'classes': ('collapse',),
            'description': 'Secure one-click unsubscribe link'
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_email(self, obj):
        """Display user's email"""
        return obj.user.email
    get_email.short_description = 'Email'
    get_email.admin_order_field = 'user__email'

    def get_email_status_icons(self, obj):
        """Display enabled email categories with icons"""
        if obj.unsubscribed_all:
            return format_html('<span style="color: red;">❌ All Unsubscribed</span>')

        icons = []
        if obj.email_profile_updates:
            icons.append('👤')
        if obj.email_event_reminders:
            icons.append('📅')
        if obj.email_new_connections:
            icons.append('🔗')
        if obj.email_new_messages:
            icons.append('💬')
        if obj.email_marketing:
            icons.append('📢')

        if icons:
            return format_html('<span title="Profile, Events, Connections, Messages, Marketing">{}</span>', ' '.join(icons))
        return format_html('<span style="color: orange;">⚠️ All Off</span>')
    get_email_status_icons.short_description = 'Active Categories'

    def get_unsubscribe_link(self, obj):
        """Display the unsubscribe URL for testing"""
        if obj.unsubscribe_token:
            url = f"https://crush.lu/email/unsubscribe/{obj.unsubscribe_token}/"
            return format_html(
                '<div style="padding: 10px; background: #f8f9fa; border-radius: 5px;">'
                '<strong>One-Click Unsubscribe URL:</strong><br>'
                '<code style="font-size: 11px; word-break: break-all;">{}</code>'
                '</div>',
                url
            )
        return "N/A"
    get_unsubscribe_link.short_description = 'Unsubscribe Link'

    actions = ['enable_all_emails', 'disable_all_emails', 'enable_marketing', 'disable_marketing']

    def enable_all_emails(self, request, queryset):
        """Re-subscribe users to all emails"""
        updated = queryset.update(
            unsubscribed_all=False,
            email_profile_updates=True,
            email_event_reminders=True,
            email_new_connections=True,
            email_new_messages=True
        )
        self.message_user(
            request,
            f'{updated} user(s) re-subscribed to all emails (except marketing).',
            level=django_messages.SUCCESS
        )
    enable_all_emails.short_description = '✅ Re-subscribe to all emails'

    def disable_all_emails(self, request, queryset):
        """Unsubscribe users from all emails"""
        updated = queryset.update(unsubscribed_all=True)
        self.message_user(
            request,
            f'{updated} user(s) unsubscribed from all emails.',
            level=django_messages.SUCCESS
        )
    disable_all_emails.short_description = '🔕 Unsubscribe from all emails'

    def enable_marketing(self, request, queryset):
        """Opt users into marketing emails"""
        updated = queryset.update(email_marketing=True)
        self.message_user(
            request,
            f'{updated} user(s) opted into marketing emails.',
            level=django_messages.SUCCESS
        )
    enable_marketing.short_description = '📢 Opt into marketing'

    def disable_marketing(self, request, queryset):
        """Opt users out of marketing emails"""
        updated = queryset.update(email_marketing=False)
        self.message_user(
            request,
            f'{updated} user(s) opted out of marketing emails.',
            level=django_messages.SUCCESS
        )
    disable_marketing.short_description = '🔕 Opt out of marketing'

# ============================================================================
# END EMAIL PREFERENCES ADMIN
# ============================================================================


# ============================================================================
# NOTE: PushSubscription, UserActivity, and EmailPreference are ONLY in crush-admin
# Standard Django admin does not have access to these models
# ============================================================================
