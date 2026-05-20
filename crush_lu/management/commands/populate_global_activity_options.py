"""
Populate the 6 standard GlobalActivityOptions for all Crush events.
Run this once during setup.
"""

from django.core.management.base import BaseCommand
from crush_lu.models import GlobalActivityOption


class Command(BaseCommand):
    help = 'Populate the standard global activity options'

    def handle(self, *args, **options):
        self.stdout.write('Populating global activity options...\n')

        options_data = [
            # Phase 2: Presentation Style (3 options)
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'music',
                'display_name': 'With Favorite Music',
                'display_name_fr': 'Avec ta musique préférée',
                'description': 'Introduce yourself while your favorite song plays in the background',
                'description_fr': 'Présente-toi pendant que ta chanson préférée joue en fond sonore',
                'sort_order': 1
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'questions',
                'display_name': '5 Predefined Questions',
                'display_name_fr': '5 questions prédéfinies',
                'description': 'Answer 5 fun questions about yourself (we provide the questions!)',
                'description_fr': 'Réponds à 5 questions amusantes sur toi-même (on fournit les questions !)',
                'sort_order': 2
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'picture_story',
                'display_name': 'Share Favorite Picture & Story',
                'display_name_fr': 'Partage une photo et une histoire',
                'description': 'Show us your favorite photo and tell us why it matters to you',
                'description_fr': 'Montre ta photo préférée et raconte pourquoi elle compte pour toi',
                'sort_order': 3
            },
            # Phase 3: Speed Dating Twist (3 options)
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'spicy_questions',
                'display_name': 'Spicy Questions First',
                'display_name_fr': 'Questions piquantes d\'abord',
                'description': 'Break the ice with bold, fun questions right away',
                'description_fr': 'Brise la glace avec des questions audacieuses et amusantes dès le départ',
                'sort_order': 4
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'forbidden_word',
                'display_name': 'Forbidden Word Challenge',
                'display_name_fr': 'Défi du mot interdit',
                'description': 'Each pair gets a secret word they can\'t say during the date',
                'description_fr': 'Chaque duo reçoit un mot secret qu\'il ne peut pas prononcer pendant le rendez-vous',
                'sort_order': 5
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'algorithm_extended',
                'display_name': 'Algorithm\'s Choice Extended Time',
                'display_name_fr': 'Choix de l\'algorithme — Temps prolongé',
                'description': 'Trust our matching algorithm - your top match gets extra time!',
                'description_fr': 'Fais confiance à notre algorithme — ton meilleur match obtient du temps en plus !',
                'sort_order': 6
            },
            # Skip option: skip the presentation round entirely
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'skip_presentations',
                'display_name': 'Skip — Go Straight to Speed Dating',
                'display_name_fr': 'Passer — Aller directement au Speed Dating',
                'description': 'Vote to skip the presentation round and jump directly into speed dating!',
                'description_fr': 'Vote pour passer le tour des présentations et aller directement au speed dating !',
                'sort_order': 10
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
        self.stdout.write('\nThese options will be used for ALL future Crush events.')
        self.stdout.write('No need to create options per-event anymore!')
