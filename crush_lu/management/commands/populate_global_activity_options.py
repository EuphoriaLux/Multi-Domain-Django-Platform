"""
Populate the 6 standard GlobalActivityOptions for all Crush events.
Run this once during setup.
"""

from django.core.management.base import BaseCommand
from crush_lu.models import GlobalActivityOption


class Command(BaseCommand):
    help = 'Populate the 6 standard global activity options'

    def handle(self, *args, **options):
        self.stdout.write('Populating global activity options...\n')

        # Define the 6 standard options
        options_data = [
            # Phase 2: Presentation Style (3 options)
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'music',
                'display_name': 'With Favorite Music',
                'description': 'Introduce yourself while your favorite song plays in the background',
                'sort_order': 1
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'questions',
                'display_name': '5 Predefined Questions',
                'description': 'Answer 5 fun questions about yourself (we provide the questions!)',
                'sort_order': 2
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'picture_story',
                'display_name': 'Share Favorite Picture & Story',
                'description': 'Show us your favorite photo and tell us why it matters to you',
                'sort_order': 3
            },
            # Phase 3: Speed Dating Twist (3 options)
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'spicy_questions',
                'display_name': 'Spicy Questions First',
                'description': 'Break the ice with bold, fun questions right away',
                'sort_order': 4
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'forbidden_word',
                'display_name': 'Forbidden Word Challenge',
                'description': 'Each pair gets a secret word they can\'t say during the date',
                'sort_order': 5
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'algorithm_extended',
                'display_name': 'Algorithm\'s Choice Extended Time',
                'description': 'Trust our matching algorithm - your top match gets extra time!',
                'sort_order': 6
            },
        ]

        created_count = 0
        updated_count = 0

        for option_data in options_data:
            obj, created = GlobalActivityOption.objects.update_or_create(
                activity_variant=option_data['activity_variant'],
                defaults=option_data
            )

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'  [CREATED] {obj.display_name}'))
            else:
                updated_count += 1
                self.stdout.write(f'  [UPDATED] {obj.display_name}')

        self.stdout.write(self.style.SUCCESS(f'\n' + '='*60))
        self.stdout.write(self.style.SUCCESS(f'[OK] Global options populated!'))
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(f'\n  Created: {created_count}')
        self.stdout.write(f'  Updated: {updated_count}')
        self.stdout.write(f'  Total:   {created_count + updated_count}')
        self.stdout.write('\nThese 6 options will be used for ALL future Crush events.')
        self.stdout.write('No need to create options per-event anymore!')
