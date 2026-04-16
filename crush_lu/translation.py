"""
django-modeltranslation registration for Crush.lu models.

Registers translatable fields for automatic language switching based on URL prefix.
When a user visits /de/journey/, the German content is automatically returned.
"""

from modeltranslation.translator import translator, TranslationOptions

from .models.newsletter import Newsletter
from .models.profiles import CrushCoach, SpecialUserExperience
from .models.matching import Trait
from .models.site_config import CrushSiteConfig
from .models.events import MeetupEvent
from .models.event_polls import EventPoll, EventPollOption
from .models.quiz import QuizRound, QuizQuestion
from .models.journey import (
    JourneyConfiguration,
    JourneyChapter,
    JourneyChallenge,
    JourneyReward,
)


class CrushCoachTranslationOptions(TranslationOptions):
    """Translatable fields for coach profiles (bio and specializations)."""

    fields = ('bio', 'specializations')


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


class CrushSiteConfigTranslationOptions(TranslationOptions):
    """Translatable fields for site config banner."""

    fields = ('banner_message', 'banner_link_text', 'banner_link_url')


class EventPollTranslationOptions(TranslationOptions):
    """Translatable fields for event polls."""

    fields = ('title', 'description')


class EventPollOptionTranslationOptions(TranslationOptions):
    """Translatable fields for event poll options."""

    fields = ('name', 'description')


class TraitTranslationOptions(TranslationOptions):
    """Translatable fields for matching traits (qualities and defects)."""

    fields = ('label',)


class QuizRoundTranslationOptions(TranslationOptions):
    """Translatable fields for quiz rounds."""

    fields = ('title',)


class QuizQuestionTranslationOptions(TranslationOptions):
    """Translatable fields for quiz questions.

    The 'choices' JSONField is translated because it contains display text
    (answer options) that participants see. Same pattern as JourneyChallenge.options.
    The 'correct_answer' is translated for open-ended reference answers.
    """

    fields = ('text', 'choices', 'correct_answer')


class NewsletterTranslationOptions(TranslationOptions):
    """Translatable fields for newsletters (subject, body content)."""

    fields = ('subject', 'body_html', 'body_text')


# Register models with translation options
translator.register(Newsletter, NewsletterTranslationOptions)
translator.register(Trait, TraitTranslationOptions)
translator.register(CrushSiteConfig, CrushSiteConfigTranslationOptions)
translator.register(CrushCoach, CrushCoachTranslationOptions)
translator.register(SpecialUserExperience, SpecialUserExperienceTranslationOptions)
translator.register(MeetupEvent, MeetupEventTranslationOptions)
translator.register(JourneyConfiguration, JourneyConfigurationTranslationOptions)
translator.register(JourneyChapter, JourneyChapterTranslationOptions)
translator.register(JourneyChallenge, JourneyChallengeTranslationOptions)
translator.register(JourneyReward, JourneyRewardTranslationOptions)
translator.register(EventPoll, EventPollTranslationOptions)
translator.register(EventPollOption, EventPollOptionTranslationOptions)
translator.register(QuizRound, QuizRoundTranslationOptions)
translator.register(QuizQuestion, QuizQuestionTranslationOptions)
