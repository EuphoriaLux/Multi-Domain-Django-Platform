"""
Utility functions for creating Wonderland journey chapters and challenges.

Extracted from the create_wonderland_journey management command for reuse
in the gift system and other journey creation flows.

Uses django-modeltranslation to populate all language fields (EN, DE, FR) at once.
Content automatically switches based on URL language prefix (/en/, /de/, /fr/).
"""

from crush_lu.models import JourneyChapter, JourneyChallenge, JourneyReward
from crush_lu.journey_translations import JOURNEY_CONTENT


def get_season_key(date_obj):
    """Determine the season key based on month"""
    month = date_obj.month
    if month in [3, 4, 5]:
        return 'spring'
    elif month in [6, 7, 8]:
        return 'summer'
    elif month in [9, 10, 11]:
        return 'autumn'
    else:
        return 'winter'


def get_chapter_content(lang, chapter_num):
    """Get content for a specific chapter and language."""
    content = JOURNEY_CONTENT.get(lang, JOURNEY_CONTENT['en'])
    chapter_key = f'chapter_{chapter_num}'
    return content.get(chapter_key, JOURNEY_CONTENT['en'].get(chapter_key, {}))


def get_text(lang, key, fallback_to_en=True, **kwargs):
    """
    Get content for specified language with optional fallback to English.

    Args:
        lang: Language code ('en', 'de', 'fr')
        key: Dot-separated key path (e.g., 'chapter_1.title')
        fallback_to_en: If True, falls back to English if key not found
        **kwargs: Format arguments for string interpolation
    """
    content = JOURNEY_CONTENT.get(lang, JOURNEY_CONTENT['en'])

    # Navigate nested keys
    keys = key.split('.')
    value = content

    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        elif fallback_to_en:
            # Fall back to English
            value = JOURNEY_CONTENT['en']
            for k2 in keys:
                if isinstance(value, dict) and k2 in value:
                    value = value[k2]
                else:
                    return ""
            break
        else:
            return ""

    # Format the string if kwargs provided
    if isinstance(value, str) and kwargs:
        try:
            value = value.format(**kwargs)
        except KeyError:
            pass

    return value if isinstance(value, str) else ""


def get_german_month(month_num):
    """Get German month name"""
    months = {
        1: 'Januar', 2: 'Februar', 3: 'März', 4: 'April',
        5: 'Mai', 6: 'Juni', 7: 'Juli', 8: 'August',
        9: 'September', 10: 'Oktober', 11: 'November', 12: 'Dezember'
    }
    return months.get(month_num, '')


def get_french_month(month_num):
    """Get French month name"""
    months = {
        1: 'janvier', 2: 'février', 3: 'mars', 4: 'avril',
        5: 'mai', 6: 'juin', 7: 'juillet', 8: 'août',
        9: 'septembre', 10: 'octobre', 11: 'novembre', 12: 'décembre'
    }
    return months.get(month_num, '')


def create_wonderland_chapters(journey, recipient_name, date_met, location_met):
    """
    Create all 6 Wonderland chapters with their challenges and rewards.

    Args:
        journey: JourneyConfiguration instance
        recipient_name: Name/nickname for personalization (e.g., "My Crush", "Marie")
        date_met: Date when sender and recipient first met
        location_met: Location where they first met

    Returns:
        List of created JourneyChapter instances
    """
    chapters = []

    # CHAPTER 1: Down the Rabbit Hole
    chapters.append(_create_chapter_1(journey, date_met))

    # CHAPTER 2: Garden of Rare Flowers
    chapters.append(_create_chapter_2(journey, recipient_name))

    # CHAPTER 3: Gallery of Moments
    chapters.append(_create_chapter_3(journey, location_met))

    # CHAPTER 4: Carnival of Courage
    chapters.append(_create_chapter_4(journey))

    # CHAPTER 5: Starlit Observatory
    chapters.append(_create_chapter_5(journey, recipient_name))

    # CHAPTER 6: Door to Tomorrow
    chapters.append(_create_chapter_6(journey, recipient_name))

    return chapters


def _create_chapter_1(journey, date_met):
    """Chapter 1: Down the Rabbit Hole - Mystery & Curiosity"""
    # Get content for all languages
    content_en = get_chapter_content('en', 1)
    content_de = get_chapter_content('de', 1)
    content_fr = get_chapter_content('fr', 1)

    chapter, _ = JourneyChapter.objects.update_or_create(
        journey=journey,
        chapter_number=1,
        defaults={
            # English
            'title_en': content_en.get('title', ''),
            'theme_en': content_en.get('theme', ''),
            'story_introduction_en': content_en.get('story_introduction', '').format(
                date_met=date_met.strftime('%B %d, %Y')
            ),
            'completion_message_en': content_en.get('completion_message', ''),
            # German
            'title_de': content_de.get('title', ''),
            'theme_de': content_de.get('theme', ''),
            'story_introduction_de': content_de.get('story_introduction', '').format(
                date_met=date_met.strftime('%d. %B %Y')
            ),
            'completion_message_de': content_de.get('completion_message', ''),
            # French
            'title_fr': content_fr.get('title', ''),
            'theme_fr': content_fr.get('theme', ''),
            'story_introduction_fr': content_fr.get('story_introduction', '').format(
                date_met=date_met.strftime('%d %B %Y')
            ),
            'completion_message_fr': content_fr.get('completion_message', ''),
            # Other fields
            'background_theme': 'wonderland_night',
            'difficulty': 'easy',
            'estimated_duration': 10,
            'requires_previous_completion': False,
        }
    )

    # Challenge 1A: The First Door (Riddle)
    challenges_en = content_en.get('challenges', [])
    challenges_de = content_de.get('challenges', [])
    challenges_fr = content_fr.get('challenges', [])

    if challenges_en:
        riddle_en = challenges_en[0]
        riddle_de = challenges_de[0] if challenges_de else riddle_en
        riddle_fr = challenges_fr[0] if challenges_fr else riddle_en

        season_en = get_text('en', f'seasons.{get_season_key(date_met)}')
        season_de = get_text('de', f'seasons.{get_season_key(date_met)}')
        season_fr = get_text('fr', f'seasons.{get_season_key(date_met)}')

        JourneyChallenge.objects.update_or_create(
            chapter=chapter,
            challenge_order=1,
            defaults={
                'challenge_type': 'riddle',
                # English
                'question_en': riddle_en.get('question', '').format(month=date_met.strftime('%B')),
                'hint_1_en': riddle_en.get('hint_1', '').format(season=season_en),
                'hint_2_en': riddle_en.get('hint_2', '').format(month=date_met.strftime('%B')),
                'hint_3_en': riddle_en.get('hint_3', '').format(day=date_met.day),
                'success_message_en': riddle_en.get('success_message', '').format(
                    date_met=date_met.strftime('%B %d, %Y')
                ),
                # German
                'question_de': riddle_de.get('question', '').format(month=get_german_month(date_met.month)),
                'hint_1_de': riddle_de.get('hint_1', '').format(season=season_de),
                'hint_2_de': riddle_de.get('hint_2', '').format(month=get_german_month(date_met.month)),
                'hint_3_de': riddle_de.get('hint_3', '').format(day=date_met.day),
                'success_message_de': riddle_de.get('success_message', '').format(
                    date_met=date_met.strftime('%d. %B %Y')
                ),
                # French
                'question_fr': riddle_fr.get('question', '').format(month=get_french_month(date_met.month)),
                'hint_1_fr': riddle_fr.get('hint_1', '').format(season=season_fr),
                'hint_2_fr': riddle_fr.get('hint_2', '').format(month=get_french_month(date_met.month)),
                'hint_3_fr': riddle_fr.get('hint_3', '').format(day=date_met.day),
                'success_message_fr': riddle_fr.get('success_message', '').format(
                    date_met=date_met.strftime('%d %B %Y')
                ),
                # Other fields
                'correct_answer': date_met.strftime('%m/%d/%Y'),
                'alternative_answers': [
                    date_met.strftime('%d/%m/%Y'),
                    date_met.strftime('%Y-%m-%d'),
                    date_met.strftime('%B %d, %Y'),
                ],
                'hint_1_cost': 20,
                'hint_2_cost': 50,
                'hint_3_cost': 80,
                'points_awarded': 100,
            }
        )

    # Challenge 1B: Word Scramble
    if len(challenges_en) > 1:
        scramble_en = challenges_en[1]
        scramble_de = challenges_de[1] if len(challenges_de) > 1 else scramble_en
        scramble_fr = challenges_fr[1] if len(challenges_fr) > 1 else scramble_en

        JourneyChallenge.objects.update_or_create(
            chapter=chapter,
            challenge_order=2,
            defaults={
                'challenge_type': 'word_scramble',
                # English
                'question_en': scramble_en.get('question', ''),
                'success_message_en': scramble_en.get('success_message', ''),
                # German
                'question_de': scramble_de.get('question', ''),
                'success_message_de': scramble_de.get('success_message', ''),
                # French
                'question_fr': scramble_fr.get('question', ''),
                'success_message_fr': scramble_fr.get('success_message', ''),
                # Other fields - use language-specific options
                'options_en': {'scrambled': scramble_en.get('scrambled', 'TFSIR PELIMS')},
                'options_de': {'scrambled': scramble_de.get('scrambled', 'SERETS LHCEÄNL')},
                'options_fr': {'scrambled': scramble_fr.get('scrambled', 'RPMEERI SRIROUE')},
                'correct_answer': scramble_en.get('answer', 'FIRST SMILE'),
                'alternative_answers': ['first smile', 'FIRSTSMILE', 'firstsmile'],
                'points_awarded': 50,
            }
        )

    # Reward: Photo Puzzle
    reward_en = content_en.get('reward', {})
    reward_de = content_de.get('reward', {})
    reward_fr = content_fr.get('reward', {})

    JourneyReward.objects.update_or_create(
        chapter=chapter,
        defaults={
            'reward_type': reward_en.get('type', 'photo_reveal'),
            'title_en': reward_en.get('title', ''),
            'message_en': reward_en.get('message', ''),
            'title_de': reward_de.get('title', ''),
            'message_de': reward_de.get('message', ''),
            'title_fr': reward_fr.get('title', ''),
            'message_fr': reward_fr.get('message', ''),
            'puzzle_pieces': 16,
        }
    )

    return chapter


def _create_chapter_2(journey, recipient_name):
    """Chapter 2: Garden of Rare Flowers - Appreciation & Uniqueness"""
    content_en = get_chapter_content('en', 2)
    content_de = get_chapter_content('de', 2)
    content_fr = get_chapter_content('fr', 2)

    chapter, _ = JourneyChapter.objects.update_or_create(
        journey=journey,
        chapter_number=2,
        defaults={
            'title_en': content_en.get('title', ''),
            'theme_en': content_en.get('theme', ''),
            'story_introduction_en': content_en.get('story_introduction', ''),
            'completion_message_en': content_en.get('completion_message', ''),
            'title_de': content_de.get('title', ''),
            'theme_de': content_de.get('theme', ''),
            'story_introduction_de': content_de.get('story_introduction', ''),
            'completion_message_de': content_de.get('completion_message', ''),
            'title_fr': content_fr.get('title', ''),
            'theme_fr': content_fr.get('theme', ''),
            'story_introduction_fr': content_fr.get('story_introduction', ''),
            'completion_message_fr': content_fr.get('completion_message', ''),
            'background_theme': 'enchanted_garden',
            'difficulty': 'easy',
            'estimated_duration': 15,
            'requires_previous_completion': True,
        }
    )

    # Multiple choice challenges
    challenges_en = content_en.get('challenges', [])
    challenges_de = content_de.get('challenges', [])
    challenges_fr = content_fr.get('challenges', [])

    for i, q_en in enumerate(challenges_en, start=1):
        q_de = challenges_de[i - 1] if i <= len(challenges_de) else q_en
        q_fr = challenges_fr[i - 1] if i <= len(challenges_fr) else q_en

        JourneyChallenge.objects.update_or_create(
            chapter=chapter,
            challenge_order=i,
            defaults={
                'challenge_type': 'multiple_choice',
                'question_en': q_en.get('question', ''),
                'success_message_en': q_en.get('success_message', ''),
                'question_de': q_de.get('question', ''),
                'success_message_de': q_de.get('success_message', ''),
                'question_fr': q_fr.get('question', ''),
                'success_message_fr': q_fr.get('success_message', ''),
                'options_en': q_en.get('options', {}),
                'options_de': q_de.get('options', {}),
                'options_fr': q_fr.get('options', {}),
                'correct_answer': '',  # Questionnaire mode - all answers accepted
                'points_awarded': 80,
            }
        )

    # Reward: Poem
    reward_en = content_en.get('reward', {})
    reward_de = content_de.get('reward', {})
    reward_fr = content_fr.get('reward', {})

    JourneyReward.objects.update_or_create(
        chapter=chapter,
        defaults={
            'reward_type': reward_en.get('type', 'poem'),
            'title_en': reward_en.get('title', ''),
            'message_en': reward_en.get('message', ''),
            'title_de': reward_de.get('title', ''),
            'message_de': reward_de.get('message', ''),
            'title_fr': reward_fr.get('title', ''),
            'message_fr': reward_fr.get('message', ''),
        }
    )

    return chapter


def _create_chapter_3(journey, location_met):
    """Chapter 3: Gallery of Moments - Shared Memories"""
    content_en = get_chapter_content('en', 3)
    content_de = get_chapter_content('de', 3)
    content_fr = get_chapter_content('fr', 3)

    chapter, _ = JourneyChapter.objects.update_or_create(
        journey=journey,
        chapter_number=3,
        defaults={
            'title_en': content_en.get('title', ''),
            'theme_en': content_en.get('theme', ''),
            'story_introduction_en': content_en.get('story_introduction', ''),
            'completion_message_en': content_en.get('completion_message', ''),
            'title_de': content_de.get('title', ''),
            'theme_de': content_de.get('theme', ''),
            'story_introduction_de': content_de.get('story_introduction', ''),
            'completion_message_de': content_de.get('completion_message', ''),
            'title_fr': content_fr.get('title', ''),
            'theme_fr': content_fr.get('theme', ''),
            'story_introduction_fr': content_fr.get('story_introduction', ''),
            'completion_message_fr': content_fr.get('completion_message', ''),
            'background_theme': 'art_gallery',
            'difficulty': 'medium',
            'estimated_duration': 20,
            'requires_previous_completion': True,
        }
    )

    # Challenge 3A: Timeline sorting
    events_en = content_en.get('timeline_events', [])
    events_de = content_de.get('timeline_events', [])
    events_fr = content_fr.get('timeline_events', [])

    formatted_events_en = [e.format(location_met=location_met) for e in events_en]
    formatted_events_de = [e.format(location_met=location_met) for e in events_de]
    formatted_events_fr = [e.format(location_met=location_met) for e in events_fr]

    JourneyChallenge.objects.update_or_create(
        chapter=chapter,
        challenge_order=1,
        defaults={
            'challenge_type': 'timeline_sort',
            'question_en': content_en.get('timeline_question', ''),
            'success_message_en': content_en.get('timeline_success', ''),
            'question_de': content_de.get('timeline_question', ''),
            'success_message_de': content_de.get('timeline_success', ''),
            'question_fr': content_fr.get('timeline_question', ''),
            'success_message_fr': content_fr.get('timeline_success', ''),
            # Store events per language (django-modeltranslation creates options_en, options_de, options_fr)
            'options_en': {'events': formatted_events_en},
            'options_de': {'events': formatted_events_de},
            'options_fr': {'events': formatted_events_fr},
            'correct_answer': '0,1,2,3,4',
            'points_awarded': 300,
        }
    )

    # Challenge 3B: The Moment That Changed Everything
    JourneyChallenge.objects.update_or_create(
        chapter=chapter,
        challenge_order=2,
        defaults={
            'challenge_type': 'multiple_choice',
            'question_en': content_en.get('moment_question', ''),
            'success_message_en': content_en.get('moment_success', ''),
            'question_de': content_de.get('moment_question', ''),
            'success_message_de': content_de.get('moment_success', ''),
            'question_fr': content_fr.get('moment_question', ''),
            'success_message_fr': content_fr.get('moment_success', ''),
            'options_en': content_en.get('moment_options', {}),
            'options_de': content_de.get('moment_options', {}),
            'options_fr': content_fr.get('moment_options', {}),
            'correct_answer': content_en.get('moment_answer', 'C'),
            'points_awarded': 200,
        }
    )

    # Reward: Photo slideshow
    reward_en = content_en.get('reward', {})
    reward_de = content_de.get('reward', {})
    reward_fr = content_fr.get('reward', {})

    JourneyReward.objects.update_or_create(
        chapter=chapter,
        defaults={
            'reward_type': reward_en.get('type', 'photo_slideshow'),
            'title_en': reward_en.get('title', ''),
            'message_en': reward_en.get('message', ''),
            'title_de': reward_de.get('title', ''),
            'message_de': reward_de.get('message', ''),
            'title_fr': reward_fr.get('title', ''),
            'message_fr': reward_fr.get('message', ''),
        }
    )

    return chapter


def _create_chapter_4(journey):
    """Chapter 4: Carnival of Courage - Vulnerability & Truth"""
    content_en = get_chapter_content('en', 4)
    content_de = get_chapter_content('de', 4)
    content_fr = get_chapter_content('fr', 4)

    chapter, _ = JourneyChapter.objects.update_or_create(
        journey=journey,
        chapter_number=4,
        defaults={
            'title_en': content_en.get('title', ''),
            'theme_en': content_en.get('theme', ''),
            'story_introduction_en': content_en.get('story_introduction', ''),
            'completion_message_en': content_en.get('completion_message', ''),
            'title_de': content_de.get('title', ''),
            'theme_de': content_de.get('theme', ''),
            'story_introduction_de': content_de.get('story_introduction', ''),
            'completion_message_de': content_de.get('completion_message', ''),
            'title_fr': content_fr.get('title', ''),
            'theme_fr': content_fr.get('theme', ''),
            'story_introduction_fr': content_fr.get('story_introduction', ''),
            'completion_message_fr': content_fr.get('completion_message', ''),
            'background_theme': 'carnival',
            'difficulty': 'medium',
            'estimated_duration': 15,
            'requires_previous_completion': True,
        }
    )

    # Would You Rather questions
    wyr_en = content_en.get('would_you_rather', [])
    wyr_de = content_de.get('would_you_rather', [])
    wyr_fr = content_fr.get('would_you_rather', [])
    wyr_success_en = content_en.get('wyr_success', '')
    wyr_success_de = content_de.get('wyr_success', '')
    wyr_success_fr = content_fr.get('wyr_success', '')

    for i, q_en in enumerate(wyr_en, start=1):
        q_de = wyr_de[i - 1] if i <= len(wyr_de) else q_en
        q_fr = wyr_fr[i - 1] if i <= len(wyr_fr) else q_en

        JourneyChallenge.objects.update_or_create(
            chapter=chapter,
            challenge_order=i,
            defaults={
                'challenge_type': 'would_you_rather',
                'question_en': q_en.get('question', ''),
                'success_message_en': wyr_success_en,
                'question_de': q_de.get('question', ''),
                'success_message_de': wyr_success_de,
                'question_fr': q_fr.get('question', ''),
                'success_message_fr': wyr_success_fr,
                'options_en': q_en.get('options', {}),
                'options_de': q_de.get('options', {}),
                'options_fr': q_fr.get('options', {}),
                'correct_answer': '',  # Questionnaire mode
                'points_awarded': 100,
            }
        )

    # Open text reflection
    JourneyChallenge.objects.update_or_create(
        chapter=chapter,
        challenge_order=len(wyr_en) + 1,
        defaults={
            'challenge_type': 'open_text',
            'question_en': content_en.get('open_question', ''),
            'success_message_en': content_en.get('open_success', ''),
            'question_de': content_de.get('open_question', ''),
            'success_message_de': content_de.get('open_success', ''),
            'question_fr': content_fr.get('open_question', ''),
            'success_message_fr': content_fr.get('open_success', ''),
            'correct_answer': '',  # Questionnaire mode
            'points_awarded': 200,
        }
    )

    # Reward: Voice message
    reward_en = content_en.get('reward', {})
    reward_de = content_de.get('reward', {})
    reward_fr = content_fr.get('reward', {})

    JourneyReward.objects.update_or_create(
        chapter=chapter,
        defaults={
            'reward_type': reward_en.get('type', 'voice_message'),
            'title_en': reward_en.get('title', ''),
            'message_en': reward_en.get('message', ''),
            'title_de': reward_de.get('title', ''),
            'message_de': reward_de.get('message', ''),
            'title_fr': reward_fr.get('title', ''),
            'message_fr': reward_fr.get('message', ''),
        }
    )

    return chapter


def _create_chapter_5(journey, recipient_name):
    """Chapter 5: Starlit Observatory - Dreams & Future"""
    content_en = get_chapter_content('en', 5)
    content_de = get_chapter_content('de', 5)
    content_fr = get_chapter_content('fr', 5)

    chapter, _ = JourneyChapter.objects.update_or_create(
        journey=journey,
        chapter_number=5,
        defaults={
            'title_en': content_en.get('title', ''),
            'theme_en': content_en.get('theme', ''),
            'story_introduction_en': content_en.get('story_introduction', ''),
            'completion_message_en': content_en.get('completion_message', ''),
            'title_de': content_de.get('title', ''),
            'theme_de': content_de.get('theme', ''),
            'story_introduction_de': content_de.get('story_introduction', ''),
            'completion_message_de': content_de.get('completion_message', ''),
            'title_fr': content_fr.get('title', ''),
            'theme_fr': content_fr.get('theme', ''),
            'story_introduction_fr': content_fr.get('story_introduction', ''),
            'completion_message_fr': content_fr.get('completion_message', ''),
            'background_theme': 'starlit_sky',
            'difficulty': 'medium',
            'estimated_duration': 20,
            'requires_previous_completion': True,
        }
    )

    # Challenge 5A: Build Your Dreams
    JourneyChallenge.objects.update_or_create(
        chapter=chapter,
        challenge_order=1,
        defaults={
            'challenge_type': 'multiple_choice',
            'question_en': content_en.get('dream_question', ''),
            'success_message_en': content_en.get('dream_success', ''),
            'question_de': content_de.get('dream_question', ''),
            'success_message_de': content_de.get('dream_success', ''),
            'question_fr': content_fr.get('dream_question', ''),
            'success_message_fr': content_fr.get('dream_success', ''),
            'options_en': content_en.get('dream_options', {}),
            'options_de': content_de.get('dream_options', {}),
            'options_fr': content_fr.get('dream_options', {}),
            'correct_answer': '',  # Questionnaire mode
            'points_awarded': 150,
        }
    )

    # Challenge 5B: Future vision
    JourneyChallenge.objects.update_or_create(
        chapter=chapter,
        challenge_order=2,
        defaults={
            'challenge_type': 'open_text',
            'question_en': content_en.get('future_question', ''),
            'success_message_en': content_en.get('future_success', ''),
            'question_de': content_de.get('future_question', ''),
            'success_message_de': content_de.get('future_success', ''),
            'question_fr': content_fr.get('future_question', ''),
            'success_message_fr': content_fr.get('future_success', ''),
            'correct_answer': '',  # Questionnaire mode
            'points_awarded': 200,
        }
    )

    # Reward: Future Letter
    reward_en = content_en.get('reward', {})
    reward_de = content_de.get('reward', {})
    reward_fr = content_fr.get('reward', {})

    JourneyReward.objects.update_or_create(
        chapter=chapter,
        defaults={
            'reward_type': reward_en.get('type', 'future_letter'),
            'title_en': reward_en.get('title', ''),
            'message_en': reward_en.get('message', '').format(first_name=recipient_name),
            'title_de': reward_de.get('title', ''),
            'message_de': reward_de.get('message', '').format(first_name=recipient_name),
            'title_fr': reward_fr.get('title', ''),
            'message_fr': reward_fr.get('message', '').format(first_name=recipient_name),
        }
    )

    return chapter


def _create_chapter_6(journey, recipient_name):
    """Chapter 6: Door to Tomorrow - The Reveal & Next Step"""
    content_en = get_chapter_content('en', 6)
    content_de = get_chapter_content('de', 6)
    content_fr = get_chapter_content('fr', 6)

    chapter, _ = JourneyChapter.objects.update_or_create(
        journey=journey,
        chapter_number=6,
        defaults={
            'title_en': content_en.get('title', ''),
            'theme_en': content_en.get('theme', ''),
            'story_introduction_en': content_en.get('story_introduction', ''),
            'completion_message_en': content_en.get('completion_message', '').format(first_name=recipient_name),
            'title_de': content_de.get('title', ''),
            'theme_de': content_de.get('theme', ''),
            'story_introduction_de': content_de.get('story_introduction', ''),
            'completion_message_de': content_de.get('completion_message', '').format(first_name=recipient_name),
            'title_fr': content_fr.get('title', ''),
            'theme_fr': content_fr.get('theme', ''),
            'story_introduction_fr': content_fr.get('story_introduction', ''),
            'completion_message_fr': content_fr.get('completion_message', '').format(first_name=recipient_name),
            'background_theme': 'magical_door',
            'difficulty': 'easy',
            'estimated_duration': 15,
            'requires_previous_completion': True,
        }
    )

    # Final riddles
    riddles_en = content_en.get('riddles', [])
    riddles_de = content_de.get('riddles', [])
    riddles_fr = content_fr.get('riddles', [])

    for i, riddle_en in enumerate(riddles_en, start=1):
        riddle_de = riddles_de[i - 1] if i <= len(riddles_de) else riddle_en
        riddle_fr = riddles_fr[i - 1] if i <= len(riddles_fr) else riddle_en
        points = 200 if i < 3 else 100

        JourneyChallenge.objects.update_or_create(
            chapter=chapter,
            challenge_order=i,
            defaults={
                'challenge_type': 'riddle',
                'question_en': riddle_en.get('question', ''),
                'success_message_en': riddle_en.get('success', ''),
                'question_de': riddle_de.get('question', ''),
                'success_message_de': riddle_de.get('success', ''),
                'question_fr': riddle_fr.get('question', ''),
                'success_message_fr': riddle_fr.get('success', ''),
                'correct_answer': riddle_en.get('answer', ''),
                'alternative_answers': riddle_en.get('alternatives', []),
                'points_awarded': points,
            }
        )

    # No reward here - the certificate IS the reward

    return chapter
