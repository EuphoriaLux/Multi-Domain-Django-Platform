"""
Management command to update existing Wonderland journeys with DE/FR translations.

This command populates the German and French translation fields for existing
journeys without deleting any data. All user progress is preserved.

Usage:
    python manage.py update_journey_translations
    python manage.py update_journey_translations --dry-run  # Preview changes
"""

from django.core.management.base import BaseCommand
from crush_lu.models import (
    JourneyConfiguration, JourneyChapter, JourneyChallenge, JourneyReward
)
from crush_lu.journey_translations import JOURNEY_CONTENT


class Command(BaseCommand):
    help = 'Update existing Wonderland journeys with DE/FR translations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving to database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes will be saved\n'))

        # Find all Wonderland journeys
        journeys = JourneyConfiguration.objects.filter(journey_type='wonderland')

        if not journeys.exists():
            self.stdout.write(self.style.WARNING('No Wonderland journeys found.'))
            return

        self.stdout.write(f'Found {journeys.count()} Wonderland journey(s) to update.\n')

        for journey in journeys:
            self.stdout.write(f'\nUpdating journey: {journey.journey_name}')
            self.stdout.write(f'  For: {journey.special_experience.first_name} {journey.special_experience.last_name}')

            # Update journey configuration
            self.update_journey_config(journey, dry_run)

            # Update chapters
            for chapter in journey.chapters.all().order_by('chapter_number'):
                self.update_chapter(chapter, journey, dry_run)

        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN complete - no changes saved.'))
        else:
            self.stdout.write(self.style.SUCCESS('\nAll translations updated successfully!'))

    def get_content(self, lang, key):
        """Get content from JOURNEY_CONTENT for a specific language."""
        return JOURNEY_CONTENT.get(lang, {}).get(key, '')

    def get_chapter_content(self, lang, chapter_num):
        """Get chapter content for a specific language and chapter number."""
        # Structure uses chapter_1, chapter_2, etc. as keys
        chapter_key = f'chapter_{chapter_num}'
        return JOURNEY_CONTENT.get(lang, {}).get(chapter_key, {})

    def update_journey_config(self, journey, dry_run):
        """Update journey configuration with DE/FR translations."""
        content_de = JOURNEY_CONTENT.get('de', {})
        content_fr = JOURNEY_CONTENT.get('fr', {})

        updates = {
            'journey_name_de': content_de.get('journey_name', ''),
            'journey_name_fr': content_fr.get('journey_name', ''),
            'final_message_de': content_de.get('final_message', ''),
            'final_message_fr': content_fr.get('final_message', ''),
        }

        self.stdout.write(f'  Journey name DE: {updates["journey_name_de"][:50]}...')
        self.stdout.write(f'  Journey name FR: {updates["journey_name_fr"][:50]}...')

        if not dry_run:
            for field, value in updates.items():
                setattr(journey, field, value)
            journey.save()

    def update_chapter(self, chapter, journey, dry_run):
        """Update chapter with DE/FR translations."""
        chapter_num = chapter.chapter_number
        content_de = self.get_chapter_content('de', chapter_num)
        content_fr = self.get_chapter_content('fr', chapter_num)

        if not content_de or not content_fr:
            self.stdout.write(self.style.WARNING(
                f'  Skipping Chapter {chapter_num}: No translation content found'
            ))
            return

        self.stdout.write(f'  Chapter {chapter_num}: {chapter.title_en}')

        # Format story introductions with personalization data
        date_met = journey.date_first_met
        location_met = journey.location_first_met or ''
        first_name = journey.special_experience.first_name

        # Chapter updates
        chapter_updates = {
            'title_de': content_de.get('title', ''),
            'title_fr': content_fr.get('title', ''),
            'theme_de': content_de.get('theme', ''),
            'theme_fr': content_fr.get('theme', ''),
            'completion_message_de': content_de.get('completion_message', ''),
            'completion_message_fr': content_fr.get('completion_message', ''),
        }

        # Handle story introductions with formatting
        story_de = content_de.get('story_introduction', '')
        story_fr = content_fr.get('story_introduction', '')

        try:
            if date_met:
                story_de = story_de.format(date_met=date_met.strftime('%d. %B %Y'))
                story_fr = story_fr.format(date_met=date_met.strftime('%d %B %Y'))
            if '{location_met}' in story_de:
                story_de = story_de.format(location_met=location_met)
            if '{location_met}' in story_fr:
                story_fr = story_fr.format(location_met=location_met)
            if '{first_name}' in story_de:
                story_de = story_de.format(first_name=first_name)
            if '{first_name}' in story_fr:
                story_fr = story_fr.format(first_name=first_name)
        except (KeyError, ValueError):
            pass  # Keep unformatted if placeholders don't match

        chapter_updates['story_introduction_de'] = story_de
        chapter_updates['story_introduction_fr'] = story_fr

        self.stdout.write(f'    DE: {chapter_updates["title_de"]}')
        self.stdout.write(f'    FR: {chapter_updates["title_fr"]}')

        if not dry_run:
            for field, value in chapter_updates.items():
                setattr(chapter, field, value)
            chapter.save()

        # Update challenges
        challenges_de = content_de.get('challenges', [])
        challenges_fr = content_fr.get('challenges', [])

        for idx, challenge in enumerate(chapter.challenges.all().order_by('challenge_order')):
            self.update_challenge(challenge, idx, challenges_de, challenges_fr, dry_run)

        # Update rewards
        rewards_de = content_de.get('rewards', [])
        rewards_fr = content_fr.get('rewards', [])

        for idx, reward in enumerate(chapter.rewards.all()):
            self.update_reward(reward, idx, rewards_de, rewards_fr, first_name, dry_run)

    def update_challenge(self, challenge, idx, challenges_de, challenges_fr, dry_run):
        """Update challenge with DE/FR translations."""
        challenge_de = challenges_de[idx] if idx < len(challenges_de) else {}
        challenge_fr = challenges_fr[idx] if idx < len(challenges_fr) else {}

        if not challenge_de and not challenge_fr:
            return

        updates = {
            'question_de': challenge_de.get('question', ''),
            'question_fr': challenge_fr.get('question', ''),
            'success_message_de': challenge_de.get('success_message', ''),
            'success_message_fr': challenge_fr.get('success_message', ''),
            'hint_1_de': challenge_de.get('hint_1', ''),
            'hint_1_fr': challenge_fr.get('hint_1', ''),
            'hint_2_de': challenge_de.get('hint_2', ''),
            'hint_2_fr': challenge_fr.get('hint_2', ''),
            'hint_3_de': challenge_de.get('hint_3', ''),
            'hint_3_fr': challenge_fr.get('hint_3', ''),
        }

        if not dry_run:
            for field, value in updates.items():
                if value:  # Only update if we have a value
                    setattr(challenge, field, value)
            challenge.save()

    def update_reward(self, reward, idx, rewards_de, rewards_fr, first_name, dry_run):
        """Update reward with DE/FR translations."""
        reward_de = rewards_de[idx] if idx < len(rewards_de) else {}
        reward_fr = rewards_fr[idx] if idx < len(rewards_fr) else {}

        if not reward_de and not reward_fr:
            return

        # Handle message formatting with first_name
        message_de = reward_de.get('message', '')
        message_fr = reward_fr.get('message', '')

        try:
            if '{first_name}' in message_de:
                message_de = message_de.format(first_name=first_name)
            if '{first_name}' in message_fr:
                message_fr = message_fr.format(first_name=first_name)
        except (KeyError, ValueError):
            pass

        updates = {
            'title_de': reward_de.get('title', ''),
            'title_fr': reward_fr.get('title', ''),
            'message_de': message_de,
            'message_fr': message_fr,
        }

        if not dry_run:
            for field, value in updates.items():
                if value:  # Only update if we have a value
                    setattr(reward, field, value)
            reward.save()
