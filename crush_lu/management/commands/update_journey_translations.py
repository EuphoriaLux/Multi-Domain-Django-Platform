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

    def get_chapter_content(self, lang, chapter_num):
        """Get chapter content for a specific language and chapter number."""
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
        content_en = self.get_chapter_content('en', chapter_num)
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
            if date_met and '{date_met}' in story_de:
                story_de = story_de.format(date_met=date_met.strftime('%d. %B %Y'))
            if date_met and '{date_met}' in story_fr:
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

        # Update challenges based on chapter number
        self.update_chapter_challenges(chapter, chapter_num, content_en, content_de, content_fr, journey, dry_run)

        # Update rewards
        self.update_chapter_rewards(chapter, content_de, content_fr, first_name, dry_run)

    def update_chapter_challenges(self, chapter, chapter_num, content_en, content_de, content_fr, journey, dry_run):
        """Update challenges based on chapter-specific structure."""
        challenges = list(chapter.challenges.all().order_by('challenge_order'))

        if not challenges:
            return

        if chapter_num == 1:
            # Chapter 1: riddle + word_scramble from 'challenges' array
            self._update_from_challenges_array(challenges, content_de, content_fr, dry_run)

        elif chapter_num == 2:
            # Chapter 2: 4 multiple_choice from 'challenges' array
            self._update_from_challenges_array(challenges, content_de, content_fr, dry_run)

        elif chapter_num == 3:
            # Chapter 3: timeline_sort + multiple_choice
            # Challenge 0: timeline_sort
            if len(challenges) > 0:
                timeline_challenge = challenges[0]
                self._update_challenge_fields(
                    timeline_challenge,
                    question_de=content_de.get('timeline_question', ''),
                    question_fr=content_fr.get('timeline_question', ''),
                    success_de=content_de.get('timeline_success', ''),
                    success_fr=content_fr.get('timeline_success', ''),
                    dry_run=dry_run
                )
                # Also update events in options JSONField
                if not dry_run:
                    events_en = content_en.get('timeline_events', [])
                    events_de = content_de.get('timeline_events', [])
                    events_fr = content_fr.get('timeline_events', [])
                    location_met = journey.location_first_met or ''
                    formatted_events_en = [e.format(location_met=location_met) for e in events_en]
                    formatted_events_de = [e.format(location_met=location_met) for e in events_de]
                    formatted_events_fr = [e.format(location_met=location_met) for e in events_fr]
                    # Update options to include all language versions
                    options = timeline_challenge.options or {}
                    options['events'] = formatted_events_en  # Default/fallback
                    options['events_en'] = formatted_events_en
                    options['events_de'] = formatted_events_de
                    options['events_fr'] = formatted_events_fr
                    timeline_challenge.options = options
                    timeline_challenge.save()
            # Challenge 1: moment multiple_choice
            if len(challenges) > 1:
                moment_challenge = challenges[1]
                self._update_challenge_fields(
                    moment_challenge,
                    question_de=content_de.get('moment_question', ''),
                    question_fr=content_fr.get('moment_question', ''),
                    success_de=content_de.get('moment_success', ''),
                    success_fr=content_fr.get('moment_success', ''),
                    dry_run=dry_run
                )
                # Update moment options in JSONField
                if not dry_run:
                    options_de = content_de.get('moment_options', {})
                    options_fr = content_fr.get('moment_options', {})
                    if options_de or options_fr:
                        options = moment_challenge.options or {}
                        if options_de:
                            options['options_de'] = options_de
                        if options_fr:
                            options['options_fr'] = options_fr
                        moment_challenge.options = options
                        moment_challenge.save(update_fields=['options'])

        elif chapter_num == 4:
            # Chapter 4: 3 would_you_rather + 1 open_text
            wyr_de = content_de.get('would_you_rather', [])
            wyr_fr = content_fr.get('would_you_rather', [])
            wyr_success_de = content_de.get('wyr_success', '')
            wyr_success_fr = content_fr.get('wyr_success', '')

            for idx, challenge in enumerate(challenges):
                if challenge.challenge_type == 'would_you_rather' and idx < len(wyr_de):
                    self._update_challenge_fields(
                        challenge,
                        question_de=wyr_de[idx].get('question', ''),
                        question_fr=wyr_fr[idx].get('question', '') if idx < len(wyr_fr) else '',
                        success_de=wyr_success_de,
                        success_fr=wyr_success_fr,
                        dry_run=dry_run
                    )
                elif challenge.challenge_type == 'open_text':
                    self._update_challenge_fields(
                        challenge,
                        question_de=content_de.get('open_question', ''),
                        question_fr=content_fr.get('open_question', ''),
                        success_de=content_de.get('open_success', ''),
                        success_fr=content_fr.get('open_success', ''),
                        dry_run=dry_run
                    )

        elif chapter_num == 5:
            # Chapter 5: dream multiple_choice + future open_text
            for challenge in challenges:
                if challenge.challenge_type == 'multiple_choice':
                    self._update_challenge_fields(
                        challenge,
                        question_de=content_de.get('dream_question', ''),
                        question_fr=content_fr.get('dream_question', ''),
                        success_de=content_de.get('dream_success', ''),
                        success_fr=content_fr.get('dream_success', ''),
                        dry_run=dry_run
                    )
                    # Update dream options in JSONField
                    if not dry_run:
                        options_de = content_de.get('dream_options', {})
                        options_fr = content_fr.get('dream_options', {})
                        if options_de or options_fr:
                            options = challenge.options or {}
                            if options_de:
                                options['options_de'] = options_de
                            if options_fr:
                                options['options_fr'] = options_fr
                            challenge.options = options
                            challenge.save(update_fields=['options'])
                elif challenge.challenge_type == 'open_text':
                    self._update_challenge_fields(
                        challenge,
                        question_de=content_de.get('future_question', ''),
                        question_fr=content_fr.get('future_question', ''),
                        success_de=content_de.get('future_success', ''),
                        success_fr=content_fr.get('future_success', ''),
                        dry_run=dry_run
                    )

        elif chapter_num == 6:
            # Chapter 6: 3 riddles from 'riddles' array
            riddles_de = content_de.get('riddles', [])
            riddles_fr = content_fr.get('riddles', [])

            for idx, challenge in enumerate(challenges):
                if idx < len(riddles_de):
                    self._update_challenge_fields(
                        challenge,
                        question_de=riddles_de[idx].get('question', ''),
                        question_fr=riddles_fr[idx].get('question', '') if idx < len(riddles_fr) else '',
                        success_de=riddles_de[idx].get('success', ''),
                        success_fr=riddles_fr[idx].get('success', '') if idx < len(riddles_fr) else '',
                        dry_run=dry_run
                    )

        self.stdout.write(f'    Updated {len(challenges)} challenge(s)')

    def _update_from_challenges_array(self, challenges, content_de, content_fr, dry_run):
        """Update challenges from a standard 'challenges' array."""
        challenges_de = content_de.get('challenges', [])
        challenges_fr = content_fr.get('challenges', [])

        for idx, challenge in enumerate(challenges):
            ch_de = challenges_de[idx] if idx < len(challenges_de) else {}
            ch_fr = challenges_fr[idx] if idx < len(challenges_fr) else {}

            self._update_challenge_fields(
                challenge,
                question_de=ch_de.get('question', ''),
                question_fr=ch_fr.get('question', ''),
                success_de=ch_de.get('success_message', ''),
                success_fr=ch_fr.get('success_message', ''),
                hint_1_de=ch_de.get('hint_1', ''),
                hint_1_fr=ch_fr.get('hint_1', ''),
                hint_2_de=ch_de.get('hint_2', ''),
                hint_2_fr=ch_fr.get('hint_2', ''),
                hint_3_de=ch_de.get('hint_3', ''),
                hint_3_fr=ch_fr.get('hint_3', ''),
                dry_run=dry_run
            )

            # Update options JSONField with translated versions for multiple_choice
            if challenge.challenge_type == 'multiple_choice' and not dry_run:
                options_de = ch_de.get('options', {})
                options_fr = ch_fr.get('options', {})
                if options_de or options_fr:
                    options = challenge.options or {}
                    if options_de:
                        options['options_de'] = options_de
                    if options_fr:
                        options['options_fr'] = options_fr
                    challenge.options = options
                    challenge.save(update_fields=['options'])

    def _update_challenge_fields(self, challenge, dry_run, **kwargs):
        """Update a single challenge with the given field values."""
        field_mapping = {
            'question_de': 'question_de',
            'question_fr': 'question_fr',
            'success_de': 'success_message_de',
            'success_fr': 'success_message_fr',
            'hint_1_de': 'hint_1_de',
            'hint_1_fr': 'hint_1_fr',
            'hint_2_de': 'hint_2_de',
            'hint_2_fr': 'hint_2_fr',
            'hint_3_de': 'hint_3_de',
            'hint_3_fr': 'hint_3_fr',
        }

        if not dry_run:
            for arg_name, field_name in field_mapping.items():
                value = kwargs.get(arg_name, '')
                if value:
                    setattr(challenge, field_name, value)
            challenge.save()

    def update_chapter_rewards(self, chapter, content_de, content_fr, first_name, dry_run):
        """Update rewards with DE/FR translations."""
        reward_de = content_de.get('reward', {})
        reward_fr = content_fr.get('reward', {})

        if not reward_de and not reward_fr:
            return

        rewards = list(chapter.rewards.all())
        if not rewards:
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

        if not dry_run:
            for reward in rewards:
                reward.title_de = reward_de.get('title', '')
                reward.title_fr = reward_fr.get('title', '')
                reward.message_de = message_de
                reward.message_fr = message_fr
                reward.save()

        self.stdout.write(f'    Updated {len(rewards)} reward(s)')
