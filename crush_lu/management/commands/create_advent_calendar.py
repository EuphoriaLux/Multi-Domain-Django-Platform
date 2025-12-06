"""
Management command to create an Advent Calendar experience for a user.

This creates the full structure including:
- JourneyConfiguration (with type 'advent_calendar')
- AdventCalendar
- 24 AdventDoors (with configurable content types)
- Optional QR tokens for physical gift integration

Usage:
    # Create calendar for existing SpecialUserExperience
    python manage.py create_advent_calendar --first-name Marie --last-name Dupont

    # Create with specific year
    python manage.py create_advent_calendar --first-name Marie --last-name Dupont --year 2025

    # Create with QR tokens
    python manage.py create_advent_calendar --first-name Marie --last-name Dupont --generate-qr

    # Customize welcome message
    python manage.py create_advent_calendar --first-name Marie --last-name Dupont \
        --title "Marie's Magical December" \
        --welcome "24 days of surprises, just for you!"
"""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.utils import timezone
from crush_lu.models import (
    SpecialUserExperience, JourneyConfiguration,
    AdventCalendar, AdventDoor, AdventDoorContent, QRCodeToken
)
import uuid

User = get_user_model()


# Default door configuration for 24 days
DEFAULT_DOOR_CONFIG = [
    # Day 1: Welcome poem
    {'day': 1, 'type': 'poem', 'qr': 'none', 'teaser': 'Your journey begins...'},
    # Day 2: First memory
    {'day': 2, 'type': 'memory', 'qr': 'none', 'teaser': 'Remember when...'},
    # Day 3: Photo reveal
    {'day': 3, 'type': 'photo', 'qr': 'bonus', 'teaser': 'A captured moment'},
    # Day 4: Small challenge
    {'day': 4, 'type': 'challenge', 'qr': 'none', 'teaser': 'Can you solve this?'},
    # Day 5: Audio message
    {'day': 5, 'type': 'audio', 'qr': 'none', 'teaser': 'Listen closely...'},
    # Day 6: Physical gift (QR required)
    {'day': 6, 'type': 'gift_teaser', 'qr': 'required', 'teaser': 'Something special awaits...'},
    # Day 7: Memory
    {'day': 7, 'type': 'memory', 'qr': 'none', 'teaser': 'A favorite moment'},
    # Day 8: Quiz
    {'day': 8, 'type': 'quiz', 'qr': 'none', 'teaser': 'Test your knowledge'},
    # Day 9: Poem
    {'day': 9, 'type': 'poem', 'qr': 'none', 'teaser': 'Words from the heart'},
    # Day 10: Photo
    {'day': 10, 'type': 'photo', 'qr': 'bonus', 'teaser': 'Through my eyes'},
    # Day 11: Challenge
    {'day': 11, 'type': 'challenge', 'qr': 'none', 'teaser': 'A festive puzzle'},
    # Day 12: Memory
    {'day': 12, 'type': 'memory', 'qr': 'none', 'teaser': 'Halfway there...'},
    # Day 13: Physical gift
    {'day': 13, 'type': 'gift_teaser', 'qr': 'required', 'teaser': 'Unwrap with care'},
    # Day 14: Video message
    {'day': 14, 'type': 'video', 'qr': 'none', 'teaser': 'A message for you'},
    # Day 15: Poem
    {'day': 15, 'type': 'poem', 'qr': 'none', 'teaser': 'Verses of affection'},
    # Day 16: Challenge
    {'day': 16, 'type': 'challenge', 'qr': 'none', 'teaser': 'Think carefully...'},
    # Day 17: Photo
    {'day': 17, 'type': 'photo', 'qr': 'bonus', 'teaser': 'Captured magic'},
    # Day 18: Memory
    {'day': 18, 'type': 'memory', 'qr': 'none', 'teaser': 'A shared moment'},
    # Day 19: Quiz
    {'day': 19, 'type': 'quiz', 'qr': 'none', 'teaser': 'How well do you know...'},
    # Day 20: Physical gift
    {'day': 20, 'type': 'gift_teaser', 'qr': 'required', 'teaser': 'The countdown continues'},
    # Day 21: Audio
    {'day': 21, 'type': 'audio', 'qr': 'none', 'teaser': 'Hear my heart'},
    # Day 22: Challenge
    {'day': 22, 'type': 'challenge', 'qr': 'none', 'teaser': 'Almost there...'},
    # Day 23: Photo
    {'day': 23, 'type': 'photo', 'qr': 'bonus', 'teaser': 'One more sleep'},
    # Day 24: Grand finale
    {'day': 24, 'type': 'countdown', 'qr': 'required', 'teaser': 'The final door...'},
]


class Command(BaseCommand):
    help = 'Creates an Advent Calendar experience for a special user'

    def add_arguments(self, parser):
        parser.add_argument(
            '--first-name',
            type=str,
            required=True,
            help='First name of the special user'
        )
        parser.add_argument(
            '--last-name',
            type=str,
            required=True,
            help='Last name of the special user'
        )
        parser.add_argument(
            '--year',
            type=int,
            default=None,
            help='Year for the calendar (default: current year)'
        )
        parser.add_argument(
            '--title',
            type=str,
            default=None,
            help='Custom calendar title'
        )
        parser.add_argument(
            '--welcome',
            type=str,
            default=None,
            help='Custom welcome message'
        )
        parser.add_argument(
            '--generate-qr',
            action='store_true',
            help='Generate QR tokens for physical gifts'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite existing calendar if it exists'
        )

    def handle(self, *args, **options):
        first_name = options['first_name']
        last_name = options['last_name']
        year = options['year'] or timezone.now().year
        custom_title = options['title']
        custom_welcome = options['welcome']
        generate_qr = options['generate_qr']
        force = options['force']

        self.stdout.write(self.style.SUCCESS(
            f'\nCreating Advent Calendar for {first_name} {last_name} ({year})...\n'
        ))

        # 1. Get or create Special User Experience
        special_exp, created = SpecialUserExperience.objects.get_or_create(
            first_name=first_name,
            last_name=last_name,
            defaults={
                'is_active': True,
                'custom_welcome_title': f'Welcome, {first_name}!',
                'custom_welcome_message': 'Your December adventure awaits...',
                'custom_theme_color': '#c41e3a',  # Christmas red
                'animation_style': 'snowflakes',
                'vip_badge': True,
                'auto_approve_profile': True,
                'skip_waitlist': True,
            }
        )

        if created:
            self.stdout.write(f'[+] Created Special User Experience for {first_name}')
        else:
            self.stdout.write(f'[=] Found existing Special User Experience for {first_name}')

        # 2. Check for existing advent calendar journey
        existing_journey = JourneyConfiguration.objects.filter(
            special_experience=special_exp,
            journey_type='advent_calendar'
        ).first()

        if existing_journey:
            if force:
                self.stdout.write(self.style.WARNING(
                    f'[!] Deleting existing advent calendar journey...'
                ))
                # Delete cascade will remove calendar, doors, content, progress, tokens
                existing_journey.delete()
            else:
                raise CommandError(
                    f'Advent calendar already exists for {first_name} {last_name}. '
                    f'Use --force to overwrite.'
                )

        # 3. Create Journey Configuration for Advent Calendar
        journey = JourneyConfiguration.objects.create(
            special_experience=special_exp,
            journey_type='advent_calendar',
            is_active=True,
            journey_name=custom_title or f"{first_name}'s Advent Calendar {year}",
            total_chapters=24,  # 24 doors as "chapters"
            estimated_duration_minutes=480,  # 24 days * ~20 min average
            certificate_enabled=False,  # No certificate for advent
            final_message=f"Merry Christmas, {first_name}! You've discovered all 24 surprises.",
        )
        self.stdout.write(f'[+] Created Journey Configuration (advent_calendar type)')

        # 4. Create Advent Calendar
        calendar = AdventCalendar.objects.create(
            journey=journey,
            calendar_title=custom_title or f"{first_name}'s Magical December",
            year=year,
            welcome_message=custom_welcome or (
                f"Welcome to your personal Advent Calendar, {first_name}! "
                f"Each day in December unlocks a new surprise, just for you. "
                f"Some doors hide poems, some hide memories, and some hide "
                f"clues to physical gifts waiting to be discovered..."
            ),
            theme_color='#c41e3a',  # Christmas red
            timezone='Europe/Luxembourg',
            unlock_hour=0,  # Midnight unlock
        )
        self.stdout.write(f'[+] Created Advent Calendar: "{calendar.calendar_title}"')

        # 5. Create 24 doors with default configuration
        doors_created = 0
        for config in DEFAULT_DOOR_CONFIG:
            door = AdventDoor.objects.create(
                calendar=calendar,
                door_number=config['day'],
                content_type=config['type'],
                qr_mode=config['qr'],
                teaser_text=config['teaser'],
                door_color=self._get_door_color(config['day']),
                door_icon=self._get_door_icon(config['type']),
            )
            doors_created += 1

            # Create empty content placeholder
            AdventDoorContent.objects.create(
                door=door,
                primary_text=f"Content for Day {config['day']} - {config['type'].title()}",
            )

        self.stdout.write(f'[+] Created {doors_created} doors with content placeholders')

        # 6. Generate QR tokens if requested
        if generate_qr:
            self._generate_qr_tokens(calendar, first_name, last_name)

        # Summary
        self.stdout.write('\n' + '=' * 50)
        self.stdout.write(self.style.SUCCESS('Advent Calendar created successfully!'))
        self.stdout.write('=' * 50)
        self.stdout.write(f'Calendar: {calendar.calendar_title}')
        self.stdout.write(f'Year: {year}')
        self.stdout.write(f'Doors: 24')
        self.stdout.write(f'QR Required Doors: {sum(1 for c in DEFAULT_DOOR_CONFIG if c["qr"] == "required")}')
        self.stdout.write(f'QR Bonus Doors: {sum(1 for c in DEFAULT_DOOR_CONFIG if c["qr"] == "bonus")}')
        self.stdout.write('')
        self.stdout.write('Next steps:')
        self.stdout.write('1. Add personalized content via Django Admin')
        self.stdout.write('2. Upload photos, audio, video files')
        self.stdout.write('3. Configure challenge questions')
        if generate_qr:
            self.stdout.write('4. Print QR codes for physical gifts')
        else:
            self.stdout.write('4. Run with --generate-qr to create QR codes')
        self.stdout.write('')

    def _get_door_color(self, day: int) -> str:
        """Assign colors to doors based on day number."""
        colors = [
            '#c41e3a',  # Christmas red
            '#165b33',  # Christmas green
            '#bb2528',  # Darker red
            '#146b3a',  # Forest green
            '#ea4630',  # Bright red
            '#0c4827',  # Dark green
            '#f8b229',  # Gold
            '#1e5945',  # Teal green
        ]
        return colors[day % len(colors)]

    def _get_door_icon(self, content_type: str) -> str:
        """Assign icons based on content type."""
        icons = {
            'poem': 'bi-journal-text',
            'memory': 'bi-heart',
            'photo': 'bi-camera',
            'challenge': 'bi-puzzle',
            'quiz': 'bi-question-circle',
            'gift_teaser': 'bi-gift',
            'audio': 'bi-music-note-beamed',
            'video': 'bi-camera-video',
            'countdown': 'bi-stars',
        }
        return icons.get(content_type, 'bi-star')

    def _generate_qr_tokens(self, calendar, first_name, last_name):
        """Generate QR tokens for doors that need them."""
        # Find user by name matching
        user = User.objects.filter(
            first_name__iexact=first_name,
            last_name__iexact=last_name
        ).first()

        if not user:
            self.stdout.write(self.style.WARNING(
                f'[!] No user found matching {first_name} {last_name}. '
                f'QR tokens will be created when user registers.'
            ))
            return

        # Create tokens for doors with QR requirements
        tokens_created = 0
        for door in calendar.doors.filter(qr_mode__in=['required', 'bonus']):
            QRCodeToken.objects.create(
                door=door,
                user=user,
                token=uuid.uuid4(),
            )
            tokens_created += 1

        self.stdout.write(f'[+] Generated {tokens_created} QR tokens for {user.username}')

        # Offer to export QR codes
        self.stdout.write(self.style.SUCCESS(
            f'\nTo generate printable QR codes, use the admin panel or run:\n'
            f'  python manage.py export_advent_qr_codes --calendar-id {calendar.id}'
        ))
