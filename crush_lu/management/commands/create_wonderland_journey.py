"""
Management command to create the complete "Wonderland of You" journey structure.
Based on the detailed 6-chapter design specification.

Usage:
    python manage.py create_wonderland_journey --first-name Marie --last-name Dupont
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from crush_lu.models import (
    SpecialUserExperience, JourneyConfiguration, JourneyChapter,
    JourneyChallenge, JourneyReward
)
from datetime import date


class Command(BaseCommand):
    help = 'Creates the complete "Wonderland of You" journey for a special user'

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
            '--date-met',
            type=str,
            default='2024-10-15',
            help='Date you first met (YYYY-MM-DD)'
        )
        parser.add_argument(
            '--location-met',
            type=str,
            default='Café de Paris',
            help='Location where you first met'
        )

    def handle(self, *args, **options):
        first_name = options['first_name']
        last_name = options['last_name']
        date_met_str = options['date_met']
        location_met = options['location_met']

        # Parse date
        date_met = date.fromisoformat(date_met_str)

        self.stdout.write(self.style.SUCCESS(
            f'\nCreating "The Wonderland of You" journey for {first_name} {last_name}...\n'
        ))

        # 1. Get or create Special User Experience
        special_exp, created = SpecialUserExperience.objects.get_or_create(
            first_name=first_name,
            last_name=last_name,
            defaults={
                'is_active': True,
                'custom_welcome_title': f'Welcome to Your Wonderland, {first_name}',
                'custom_welcome_message': 'Something magical awaits you...',
                'custom_theme_color': '#9B59B6',
                'animation_style': 'roses',
                'vip_badge': True,
                'auto_approve_profile': True,
                'skip_waitlist': True,
            }
        )

        if created:
            self.stdout.write(f'[OK] Created Special User Experience for {first_name}')
        else:
            self.stdout.write(f'[OK] Found existing Special User Experience for {first_name}')

        # 2. Create Journey Configuration
        journey, created = JourneyConfiguration.objects.get_or_create(
            special_experience=special_exp,
            defaults={
                'is_active': True,
                'journey_name': 'The Wonderland of You',
                'total_chapters': 6,
                'estimated_duration_minutes': 90,
                'date_first_met': date_met,
                'location_first_met': location_met,
                'certificate_enabled': True,
                'final_message': (
                    f"You've completed every challenge and discovered every secret. "
                    f"But there's one thing I haven't said clearly enough: "
                    f"You're extraordinary, and I'd be honored if you'd let me prove it to you, "
                    f"one real moment at a time."
                ),
            }
        )

        if created:
            self.stdout.write(f'[OK] Created Journey Configuration')
        else:
            self.stdout.write(f'[WARN] Journey already exists - updating...')
            journey.date_first_met = date_met
            journey.location_first_met = location_met
            journey.save()

        # 3. Create all 6 chapters
        self.create_all_chapters(journey, date_met, location_met, first_name)

        self.stdout.write(self.style.SUCCESS(
            f'\nJourney creation complete!\n'
            f'Total: 6 chapters with challenges and rewards\n'
            f'Ready to use in admin or when {first_name} logs in!\n'
        ))

    def create_all_chapters(self, journey, date_met, location_met, first_name):
        """Create all 6 chapters with their challenges and rewards"""

        # CHAPTER 1: Down the Rabbit Hole
        self.create_chapter_1(journey, date_met)

        # CHAPTER 2: Garden of Rare Flowers
        self.create_chapter_2(journey, first_name)

        # CHAPTER 3: Gallery of Moments
        self.create_chapter_3(journey, location_met)

        # CHAPTER 4: Carnival of Courage
        self.create_chapter_4(journey)

        # CHAPTER 5: Starlit Observatory
        self.create_chapter_5(journey, first_name)

        # CHAPTER 6: Door to Tomorrow
        self.create_chapter_6(journey, first_name)

    def create_chapter_1(self, journey, date_met):
        """Chapter 1: Down the Rabbit Hole - Mystery & Curiosity"""
        self.stdout.write('Creating Chapter 1: Down the Rabbit Hole...')

        chapter, _ = JourneyChapter.objects.get_or_create(
            journey=journey,
            chapter_number=1,
            defaults={
                'title': 'Down the Rabbit Hole',
                'theme': 'Mystery & Curiosity',
                'background_theme': 'wonderland_night',
                'difficulty': 'easy',
                'estimated_duration': 10,
                'requires_previous_completion': False,
                'story_introduction': (
                    f"Once upon a time, in a world full of ordinary connections, something "
                    f"extraordinary happened. On {date_met.strftime('%B %d, %Y')}, a doorway "
                    f"opened to a wonderland I never knew existed. But this wonderland isn't about "
                    f"talking cats or mad hatters - it's about discovering someone who makes every "
                    f"moment feel like magic. Are you ready to follow the white rabbit and see where "
                    f"this journey leads? Your first challenge awaits..."
                ),
                'completion_message': (
                    "You found the door! Just like that day when I first saw you, something clicked. "
                    "I didn't know it then, but that smile of yours would become my favorite sight "
                    "in the world. Let's continue deeper into our wonderland..."
                ),
            }
        )

        # Challenge 1A: The First Door (Riddle)
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=1,
            defaults={
                'challenge_type': 'riddle',
                'question': (
                    "I am the moment when two paths crossed,\n"
                    "When a stranger's smile made the world feel lost.\n"
                    f"I happened on a {date_met.strftime('%B')} day so bright,\n"
                    "What date marks this moment of pure light?"
                ),
                'correct_answer': date_met.strftime('%m/%d/%Y'),
                'alternative_answers': [
                    date_met.strftime('%d/%m/%Y'),
                    date_met.strftime('%Y-%m-%d'),
                    date_met.strftime('%B %d, %Y'),
                    date_met.strftime('%b %d %Y'),
                ],
                'hint_1': f"Think back to the season when leaves were {self.get_season(date_met)}...",
                'hint_1_cost': 20,
                'hint_2': f"It was in the month of {date_met.strftime('%B')}...",
                'hint_2_cost': 50,
                'hint_3': f"The day was the {date_met.day}th...",
                'hint_3_cost': 80,
                'points_awarded': 100,
                'success_message': (
                    f"Yes! {date_met.strftime('%B %d, %Y')} - that date is etched in my memory. "
                    "The day everything changed."
                ),
            }
        )

        # Challenge 1B: Word Scramble
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=2,
            defaults={
                'challenge_type': 'word_scramble',
                'question': 'Unscramble these letters to reveal what captured my attention:',
                'options': {'scrambled': 'TFSIR PELIMS'},
                'correct_answer': 'FIRST SMILE',
                'alternative_answers': ['first smile', 'FIRSTSMILE', 'firstsmile'],
                'points_awarded': 50,
                'success_message': 'Exactly! Your smile was the first thing that caught my eye.',
            }
        )

        # Reward: Photo Puzzle
        JourneyReward.objects.get_or_create(
            chapter=chapter,
            defaults={
                'reward_type': 'photo_reveal',
                'title': 'The Smile That Started It All',
                'message': 'This is the moment that changed everything... 💫',
                'puzzle_pieces': 16,
            }
        )

    def create_chapter_2(self, journey, first_name):
        """Chapter 2: Garden of Rare Flowers - Appreciation & Uniqueness"""
        self.stdout.write('Creating Chapter 2: Garden of Rare Flowers...')

        chapter, _ = JourneyChapter.objects.get_or_create(
            journey=journey,
            chapter_number=2,
            defaults={
                'title': 'The Garden of Rare Flowers',
                'theme': 'Appreciation & Uniqueness',
                'background_theme': 'enchanted_garden',
                'difficulty': 'easy',
                'estimated_duration': 15,
                'requires_previous_completion': True,
                'story_introduction': (
                    "In every garden, there are common flowers - beautiful, but familiar. "
                    "And then there are rare blooms that make you stop and stare, wondering how "
                    "something so extraordinary exists. You, my dear, are the rarest flower in the garden. "
                    "This chapter celebrates all the little things that make you... YOU."
                ),
                'completion_message': (
                    "You see? Every petal in this garden represents something about you that I've noticed, "
                    "remembered, and treasured. While others might see a flower, I see the entire ecosystem "
                    "of beauty that is YOU. And I'm just getting started..."
                ),
            }
        )

        # Challenge 2A: Multiple choice questions (5 questions)
        questions = [
            {
                'question': "What's your secret superpower that most people don't see?",
                'options': {
                    'A': 'Making people laugh even on their worst days',
                    'B': 'Finding beauty in the smallest moments',
                    'C': 'Staying calm when everything is chaos',
                    'D': 'Remembering tiny details about people',
                },
                'answer': 'B',
                'message': "Yes! I noticed this about you from the very beginning. It's one of the things that makes you extraordinary.",
            },
            {
                'question': "If your laugh was a sound in nature, what would it be?",
                'options': {
                    'A': 'Wind chimes on a gentle breeze',
                    'B': 'A babbling brook over smooth stones',
                    'C': 'Birds singing at sunrise',
                    'D': 'Ocean waves on a peaceful shore',
                },
                'answer': 'C',
                'message': "Your laugh brightens everything around you, just like birds at sunrise. I could listen to it for hours.",
            },
            {
                'question': "What do you value most in a connection with someone?",
                'options': {
                    'A': 'Honest conversations, even about hard things',
                    'B': 'Comfortable silence and just being together',
                    'C': 'Shared adventures and creating memories',
                    'D': 'Deep understanding without words',
                },
                'answer': 'A',
                'message': "I felt this from you from the start. That's when I knew you were different...",
            },
            {
                'question': "If you could give your younger self one piece of advice, it would be...",
                'options': {
                    'A': 'Trust your gut - it knows more than you think',
                    'B': 'The hard times are making you stronger',
                    'C': "Don't change for anyone - you're perfect as you are",
                    'D': "Everything happens exactly when it's supposed to",
                },
                'answer': 'D',
                'message': "Knowing you, I think you'd say this. And I'm grateful for every step that led you to this moment...",
            },
        ]

        for i, q in enumerate(questions, start=1):
            JourneyChallenge.objects.get_or_create(
                chapter=chapter,
                challenge_order=i,
                defaults={
                    'challenge_type': 'multiple_choice',
                    'question': q['question'],
                    'options': q['options'],
                    'correct_answer': q['answer'],
                    'points_awarded': 80,
                    'success_message': q['message'],
                }
            )

        # Reward: Poem
        JourneyReward.objects.get_or_create(
            chapter=chapter,
            defaults={
                'reward_type': 'poem',
                'title': 'A Garden of One',
                'message': (
                    "In a garden of billions of souls,\n"
                    "Yours blooms in colors I've never seen.\n"
                    "Not because you try to stand out,\n"
                    "But because authenticity is your sunlight,\n"
                    "Kindness is your rain,\n"
                    "And genuine beauty grows from within.\n\n"
                    "I could spend lifetimes studying your petals,\n"
                    "And still discover new shades of wonderful."
                ),
            }
        )

    def create_chapter_3(self, journey, location_met):
        """Chapter 3: Gallery of Moments - Shared Memories"""
        self.stdout.write('Creating Chapter 3: Gallery of Moments...')

        chapter, _ = JourneyChapter.objects.get_or_create(
            journey=journey,
            chapter_number=3,
            defaults={
                'title': 'The Gallery of Moments',
                'theme': 'Shared Memories',
                'background_theme': 'art_gallery',
                'difficulty': 'medium',
                'estimated_duration': 20,
                'requires_previous_completion': True,
                'story_introduction': (
                    "Time is funny - it's made of millions of moments, but only a few make us truly feel alive. "
                    "This gallery holds the moments with you that painted color into my black-and-white world. "
                    "Each frame is a memory I've locked away in my heart. Can you help me remember them too?"
                ),
                'completion_message': (
                    "Every photo in this gallery, every memory we've built - they're not just moments in time. "
                    "They're proof that some connections are worth remembering, worth treasuring, worth everything. "
                    "And we're still creating new moments for future galleries..."
                ),
            }
        )

        # Challenge 3A: Timeline sorting
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=1,
            defaults={
                'challenge_type': 'timeline_sort',
                'question': 'Arrange these moments in the order they happened:',
                'options': {
                    'events': [
                        f"The day we first met at {location_met}",
                        "That conversation that lasted until midnight",
                        "When you shared something personal with me",
                        "The moment I realized you were special",
                        "Today - as you journey through this wonderland",
                    ]
                },
                'correct_answer': '0,1,2,3,4',  # Correct order indices
                'points_awarded': 300,
                'success_message': (
                    "Perfect! You remember our timeline just as I do. Every moment matters."
                ),
            }
        )

        # Challenge 3B: The Moment That Changed Everything
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=2,
            defaults={
                'challenge_type': 'multiple_choice',
                'question': (
                    "There was one moment when 'interesting person' became 'someone I can't stop thinking about.' "
                    "What was I thinking in that moment?"
                ),
                'options': {
                    'A': 'I want to know everything about this person',
                    'B': 'I hope this isn\'t the last time we talk',
                    'C': 'I\'ve never met anyone like this before',
                    'D': 'I need to see that smile again',
                },
                'correct_answer': 'C',
                'points_awarded': 200,
                'success_message': (
                    "Yes. In that exact moment, something shifted. You became less of a 'what if' "
                    "and more of a 'I hope so'..."
                ),
            }
        )

        # Reward: Photo slideshow
        JourneyReward.objects.get_or_create(
            chapter=chapter,
            defaults={
                'reward_type': 'photo_slideshow',
                'title': 'Moments in Time',
                'message': 'A collection of moments that make time stop...',
            }
        )

    def create_chapter_4(self, journey):
        """Chapter 4: Carnival of Courage - Vulnerability & Truth"""
        self.stdout.write('Creating Chapter 4: Carnival of Courage...')

        chapter, _ = JourneyChapter.objects.get_or_create(
            journey=journey,
            chapter_number=4,
            defaults={
                'title': 'The Carnival of Courage',
                'theme': 'Vulnerability & Truth',
                'background_theme': 'carnival',
                'difficulty': 'medium',
                'estimated_duration': 15,
                'requires_previous_completion': True,
                'story_introduction': (
                    "The bravest thing anyone can do is be honest - with themselves and with others. "
                    "In this carnival of life, we wear many masks. But with you, I want to take mine off. "
                    "This chapter is about truth, vulnerability, and the courage to say what matters. "
                    "Will you step up to the challenge?"
                ),
                'completion_message': (
                    "You made it through the Carnival of Courage. Here's my truth: "
                    "I'm taking my mask off. The question is... what happens next?"
                ),
            }
        )

        # Challenge 4A: Would You Rather questions
        wyr_questions = [
            {
                'question': 'Would you rather know the truth even if it hurts, or live in comfortable uncertainty?',
                'options': {'A': 'Know the truth', 'B': 'Comfortable uncertainty'},
                'answer': 'A',
            },
            {
                'question': 'Would you rather regret trying, or regret never knowing?',
                'options': {'A': 'Regret trying', 'B': 'Regret never knowing'},
                'answer': 'B',
            },
            {
                'question': 'Would you rather have one real connection, or a hundred superficial ones?',
                'options': {'A': 'One real connection', 'B': 'Hundred superficial'},
                'answer': 'A',
            },
        ]

        for i, q in enumerate(wyr_questions, start=1):
            JourneyChallenge.objects.get_or_create(
                chapter=chapter,
                challenge_order=i,
                defaults={
                    'challenge_type': 'would_you_rather',
                    'question': q['question'],
                    'options': q['options'],
                    'correct_answer': q['answer'],
                    'points_awarded': 100,
                    'success_message': 'Your answer tells me so much about who you are.',
                }
            )

        # Challenge 4B: Open text reflection
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=4,
            defaults={
                'challenge_type': 'open_text',
                'question': 'What do you think makes someone worth taking a risk for?',
                'correct_answer': 'ANY',  # Any answer is accepted
                'points_awarded': 200,
                'success_message': 'Thank you for being honest. Your words mean everything.',
            }
        )

        # Reward: Voice/video message
        JourneyReward.objects.get_or_create(
            chapter=chapter,
            defaults={
                'reward_type': 'voice_message',
                'title': 'A Message from the Heart',
                'message': 'I need to tell you something I\'ve been thinking about...',
            }
        )

    def create_chapter_5(self, journey, first_name):
        """Chapter 5: Starlit Observatory - Dreams & Future"""
        self.stdout.write('Creating Chapter 5: Starlit Observatory...')

        chapter, _ = JourneyChapter.objects.get_or_create(
            journey=journey,
            chapter_number=5,
            defaults={
                'title': 'The Starlit Observatory',
                'theme': 'Dreams & Future',
                'background_theme': 'starlit_sky',
                'difficulty': 'medium',
                'estimated_duration': 20,
                'requires_previous_completion': True,
                'story_introduction': (
                    "They say we're all made of stardust. If that's true, then some stars must be meant "
                    "to orbit together. This observatory isn't just for looking at distant galaxies - "
                    "it's for dreaming about futures, possibilities, and 'what if's. "
                    "Let's gaze at the stars and wonder... what could we create together?"
                ),
                'completion_message': (
                    "Looking at stars makes me think about infinity, about possibilities, about futures unwritten. "
                    "And here's what I know: whatever the future holds, I want you in it. "
                    "Not as a distant star I admire from afar, but as someone who's part of my constellation. "
                    "Someone whose light makes my sky brighter. Someone worth wishing on."
                ),
            }
        )

        # Challenge 5A: Build Your Dreams
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=1,
            defaults={
                'challenge_type': 'multiple_choice',
                'question': 'If we could do anything together, I\'d want to...',
                'options': {
                    'A': 'Travel somewhere we\'ve never been',
                    'B': 'Learn something new together',
                    'C': 'Create something beautiful',
                    'D': 'Build quiet moments of peace',
                },
                'correct_answer': 'ANY',
                'points_awarded': 150,
                'success_message': 'I love that idea. Let\'s make it happen.',
            }
        )

        # Challenge 5B: Future vision
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=2,
            defaults={
                'challenge_type': 'open_text',
                'question': 'Complete this sentence: "In 5 years, I hope we\'re..."',
                'correct_answer': 'ANY',
                'points_awarded': 200,
                'success_message': 'That future sounds beautiful. Let\'s build it together.',
            }
        )

        # Reward: Future Letter
        JourneyReward.objects.get_or_create(
            chapter=chapter,
            defaults={
                'reward_type': 'future_letter',
                'title': 'A Letter to the Future',
                'message': (
                    f"Dear Future {first_name},\n\n"
                    f"If you're reading this, then we've completed this journey. "
                    f"I don't know what happens next - that's still being written. "
                    f"But I want Future You to know what Present Me is thinking right now...\n\n"
                    f"Whatever happens, I want you to know that creating this for you - "
                    f"every puzzle, every message, every detail - was worth it. Because YOU are worth it.\n\n"
                    f"With all the stardust in my heart"
                ),
            }
        )

    def create_chapter_6(self, journey, first_name):
        """Chapter 6: Door to Tomorrow - The Reveal & Next Step"""
        self.stdout.write('Creating Chapter 6: Door to Tomorrow...')

        chapter, _ = JourneyChapter.objects.get_or_create(
            journey=journey,
            chapter_number=6,
            defaults={
                'title': 'The Door to Tomorrow',
                'theme': 'The Reveal & Next Step',
                'background_theme': 'magical_door',
                'difficulty': 'easy',
                'estimated_duration': 15,
                'requires_previous_completion': True,
                'story_introduction': (
                    "Every journey needs an ending. But some endings are really beginnings in disguise. "
                    "You've solved every puzzle, unlocked every secret, and made it to the final door. "
                    "Behind this door is the truth about why I created this entire wonderland for you. "
                    "Are you ready?"
                ),
                'completion_message': (
                    f"{first_name}, you've journeyed through riddles, puzzles, memories, and dreams. "
                    "You've unlocked every chapter, discovered every secret. "
                    "This isn't the end of our story. It's the beginning of the next chapter. "
                    "And I'm hoping - really hoping - that you'll write it with me."
                ),
            }
        )

        # Challenge 6A: Final riddle (3 parts)
        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=1,
            defaults={
                'challenge_type': 'riddle',
                'question': "I am the reason your name echoes in my mind before sleep. What am I?",
                'correct_answer': 'feelings',
                'alternative_answers': ['love', 'thoughts', 'emotions', 'affection'],
                'points_awarded': 200,
                'success_message': 'Yes... feelings. That\'s where this all begins.',
            }
        )

        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=2,
            defaults={
                'challenge_type': 'riddle',
                'question': "I am what I hope we can build together. What am I?",
                'correct_answer': 'future',
                'alternative_answers': ['connection', 'us', 'relationship', 'life together'],
                'points_awarded': 200,
                'success_message': 'Yes... our future. Something beautiful we create together.',
            }
        )

        JourneyChallenge.objects.get_or_create(
            chapter=chapter,
            challenge_order=3,
            defaults={
                'challenge_type': 'riddle',
                'question': "I am what I'm asking you to give this. What am I?",
                'correct_answer': 'chance',
                'alternative_answers': ['time', 'heart', 'opportunity', 'try'],
                'points_awarded': 100,
                'success_message': (
                    'Yes... a chance. Feelings for our future, if you\'ll give it a chance. '
                    'That\'s all I\'m asking.'
                ),
            }
        )

        # No reward here - the certificate IS the reward

    def get_season(self, date_obj):
        """Determine the season based on month"""
        month = date_obj.month
        if month in [3, 4, 5]:
            return 'blooming'
        elif month in [6, 7, 8]:
            return 'shining'
        elif month in [9, 10, 11]:
            return 'falling'
        else:
            return 'sparkling'
