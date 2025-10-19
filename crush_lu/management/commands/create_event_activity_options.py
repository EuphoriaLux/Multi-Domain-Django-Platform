from django.core.management.base import BaseCommand
from crush_lu.models import MeetupEvent, EventActivityOption, EventVotingSession


class Command(BaseCommand):
    help = 'Create default activity options for all upcoming events'

    def add_arguments(self, parser):
        parser.add_argument(
            '--event-id',
            type=int,
            help='Specific event ID to create options for (optional)',
        )

    def handle(self, *args, **options):
        event_id = options.get('event_id')

        if event_id:
            events = MeetupEvent.objects.filter(id=event_id)
            if not events.exists():
                self.stdout.write(self.style.ERROR(f'Event with ID {event_id} not found'))
                return
        else:
            # Get all published, non-cancelled upcoming events
            from django.utils import timezone
            events = MeetupEvent.objects.filter(
                is_published=True,
                is_cancelled=False,
                date_time__gte=timezone.now()
            )

        activity_definitions = [
            # Phase 2: Presentation Style options (MANDATORY)
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'music',
                'display_name': 'With Favorite Music',
                'description': 'Introduce yourself while your favorite song plays in the background! Music reveals personality and creates memorable first impressions. Pick a song that represents you!'
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'questions',
                'display_name': '5 Predefined Questions',
                'description': 'Answer 5 fun questions we provide! Questions like "What\'s your hidden talent?" or "Dream vacation?" - ensures everyone shares interesting details in a structured way.'
            },
            {
                'activity_type': 'presentation_style',
                'activity_variant': 'picture_story',
                'display_name': 'Share Favorite Picture & Story',
                'description': 'Bring or show your favorite photo and tell its story! Pictures are worth a thousand words, and personal stories create instant emotional connections.'
            },
            # Phase 3: Speed Dating Twist options
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'spicy_questions',
                'display_name': 'Spicy Questions First',
                'description': 'Break the ice with bold, fun questions provided by the app! Skip the small talk and dive into interesting conversations. Get to know the real person faster!'
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'forbidden_word',
                'display_name': 'Forbidden Word Challenge',
                'description': 'Can\'t say certain common words during conversations! A playful game that makes you more creative and leads to unexpected, memorable interactions.'
            },
            {
                'activity_type': 'speed_dating_twist',
                'activity_variant': 'algorithm_extended',
                'display_name': 'Trust the Algorithm - Extended Time',
                'description': 'Get 8 minutes (instead of 5) with your #1 match based on presentation ratings! Trust the algorithm to give you extra time with your best potential connection.'
            },
        ]

        created_count = 0
        for event in events:
            # Create voting session if doesn't exist
            voting_session, session_created = EventVotingSession.objects.get_or_create(
                event=event
            )
            if session_created:
                self.stdout.write(self.style.SUCCESS(f'Created voting session for "{event.title}"'))

            # Create activity options
            for activity_def in activity_definitions:
                option, created = EventActivityOption.objects.get_or_create(
                    event=event,
                    activity_type=activity_def['activity_type'],
                    activity_variant=activity_def['activity_variant'],
                    defaults={
                        'display_name': activity_def['display_name'],
                        'description': activity_def['description'],
                    }
                )
                if created:
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'  ✓ Created: {option.display_name}'
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone! Created {created_count} activity options across {events.count()} event(s)'
            )
        )
