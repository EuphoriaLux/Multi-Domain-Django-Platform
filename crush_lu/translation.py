"""
django-modeltranslation registration for Crush.lu models.

Registers translatable fields for automatic language switching based on URL prefix.
When a user visits /de/journey/, the German content is automatically returned.
"""

from modeltranslation.translator import translator, TranslationOptions

from .models.profiles import SpecialUserExperience
from .models.events import MeetupEvent
from .models.journey import (
    JourneyConfiguration,
    JourneyChapter,
    JourneyChallenge,
    JourneyReward,
)


class SpecialUserExperienceTranslationOptions(TranslationOptions):
    """Translatable fields for special user welcome experience."""

    fields = ('custom_welcome_title', 'custom_welcome_message')


class MeetupEventTranslationOptions(TranslationOptions):
    """Translatable fields for meetup events."""

    fields = ('title', 'description')


class JourneyConfigurationTranslationOptions(TranslationOptions):
    """Translatable fields for journey configuration."""

    fields = ('journey_name', 'final_message')


class JourneyChapterTranslationOptions(TranslationOptions):
    """Translatable fields for journey chapters."""

    fields = ('title', 'theme', 'story_introduction', 'completion_message')


class JourneyChallengeTranslationOptions(TranslationOptions):
    """
    Translatable fields for journey challenges.

    Note: 'correct_answer' and 'alternative_answers' are NOT translated
    because they contain validation logic values.

    The 'options' field IS translated because it contains display text
    for multiple choice answers that users see.
    """

    fields = ('question', 'hint_1', 'hint_2', 'hint_3', 'success_message', 'options')


class JourneyRewardTranslationOptions(TranslationOptions):
    """Translatable fields for journey rewards (poems, letters, etc.)."""

    fields = ('title', 'message')


# Register models with translation options
translator.register(SpecialUserExperience, SpecialUserExperienceTranslationOptions)
translator.register(MeetupEvent, MeetupEventTranslationOptions)
translator.register(JourneyConfiguration, JourneyConfigurationTranslationOptions)
translator.register(JourneyChapter, JourneyChapterTranslationOptions)
translator.register(JourneyChallenge, JourneyChallengeTranslationOptions)
translator.register(JourneyReward, JourneyRewardTranslationOptions)
