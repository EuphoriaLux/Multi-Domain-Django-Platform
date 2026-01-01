"""
Advent Calendar admin classes for Crush.lu Coach Panel.

Includes:
- AdventDoorContentInline
- AdventDoorInline
- QRCodeTokenInline
- AdventCalendarAdmin
- AdventDoorAdmin
- AdventDoorContentAdmin
- AdventProgressAdmin
- QRCodeTokenAdmin
"""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.utils.html import format_html

from crush_lu.models import (
    AdventCalendar, AdventDoor, AdventDoorContent, AdventProgress, QRCodeToken,
)


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
