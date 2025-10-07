"""
Signal handlers for Crush.lu app
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import MeetupEvent, EventActivityOption


@receiver(post_save, sender=MeetupEvent)
def create_default_activity_options(sender, instance, created, **kwargs):
    """
    Automatically create the 6 standard activity options when a new event is created.
    This ensures every Crush event has the same voting options without manual creation.
    """
    if created:  # Only for newly created events
        # Define the 6 standard activity options
        default_options = [
            # Phase 2: Presentation Style (3 options)
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'music',
                'display_name': 'With Favorite Music',
                'description': 'Introduce yourself while your favorite song plays in the background'
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'questions',
                'display_name': '5 Predefined Questions',
                'description': 'Answer 5 fun questions about yourself (we provide the questions!)'
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'picture_story',
                'display_name': 'Share Favorite Picture & Story',
                'description': 'Show us your favorite photo and tell us why it matters to you'
            },
            # Phase 3: Speed Dating Twist (3 options)
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'spicy_questions',
                'display_name': 'Spicy Questions First',
                'description': 'Break the ice with bold, fun questions right away'
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'forbidden_word',
                'display_name': 'Forbidden Word Challenge',
                'description': 'Each pair gets a secret word they can\'t say during the date'
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'algorithm_extended',
                'display_name': 'Algorithm\'s Choice Extended Time',
                'description': 'Trust our matching algorithm - your top match gets extra time!'
            },
        ]

        # Create all 6 options for this event
        for option_data in default_options:
            EventActivityOption.objects.create(
                event=instance,
                **option_data
            )
