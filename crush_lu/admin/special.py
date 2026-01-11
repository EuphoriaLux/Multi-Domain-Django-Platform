"""
Special User Experience admin for Crush.lu Coach Panel.

Handles VIP/personalized journey experiences configuration.
"""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.utils.html import format_html

from crush_lu.models import (
    SpecialUserExperience,
    JourneyConfiguration,
    AdventCalendar, AdventDoor, AdventDoorContent,
)


class SpecialUserExperienceAdmin(admin.ModelAdmin):
    """
    ✨ SPECIAL JOURNEY SYSTEM - VIP Experience Configuration

    This is the entry point for creating personalized journey experiences.
    Configure who gets the special journey and customize their experience.
    """
    list_display = (
        'first_name', 'last_name', 'get_linked_user_display', 'is_active',
        'get_journeys_status', 'get_source_display',
        'trigger_count', 'last_triggered_at'
    )
    list_filter = ('is_active', 'animation_style', 'auto_approve_profile', 'vip_badge', 'skip_waitlist')
    search_fields = ('first_name', 'last_name', 'custom_welcome_title', 'custom_welcome_message', 'linked_user__username', 'linked_user__email')
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
            'fields': ('first_name', 'last_name', 'linked_user', 'is_active'),
            'description': 'Match by linked_user (gift system) OR first+last name (legacy). linked_user takes priority.'
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

    def get_linked_user_display(self, obj):
        """Display the linked user if set"""
        if obj.linked_user:
            return format_html(
                '<a href="/admin/auth/user/{}/change/" style="color: #9B59B6;">'
                '<strong>{}</strong></a>',
                obj.linked_user.id,
                obj.linked_user.email or obj.linked_user.username
            )
        return format_html('<span style="color: #999;">Name match</span>')
    get_linked_user_display.short_description = 'Linked User'
    get_linked_user_display.admin_order_field = 'linked_user'

    def get_source_display(self, obj):
        """Display where this experience came from"""
        # Check if this experience was created from a gift
        from crush_lu.models import JourneyGift
        gift = JourneyGift.objects.filter(special_experience=obj).first()
        if gift:
            return format_html(
                '<span style="background: #FF6B9D; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px;">'
                '🎁 Gift from {}</span>',
                gift.sender.first_name
            )
        return format_html('<span style="background: #666; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px;">📋 Manual</span>')
    get_source_display.short_description = 'Source'

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
            from django.urls import reverse

            chapter_count = journey.chapters.count()
            challenge_count = sum(chapter.challenges.count() for chapter in journey.chapters.all())
            journey_url = reverse('crush_admin:crush_lu_journeyconfiguration_change', args=[journey.id])
            return format_html(
                '<div style="padding: 10px; background: #e8f5e9; border-radius: 5px;">'
                '<strong>✅ Journey Created:</strong> {}<br>'
                '<strong>Chapters:</strong> {}<br>'
                '<strong>Challenges:</strong> {}<br>'
                '<strong>Status:</strong> {}<br>'
                '<a href="{}" '
                'class="button" style="margin-top: 10px;">View/Edit Journey</a>'
                '</div>',
                journey.journey_name,
                chapter_count,
                challenge_count,
                'Active' if journey.is_active else 'Inactive',
                journey_url
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
