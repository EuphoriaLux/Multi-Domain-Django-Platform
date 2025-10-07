"""
Management command to set up a complete test scenario for the presentation system.
This creates:
- A test event
- Test users with Crush profiles
- Activity options for voting
- A voting session
- Event registrations
- Simulates voting results
- Initializes presentation queue
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from crush_lu.models import (
    MeetupEvent, EventRegistration, CrushProfile, CrushCoach,
    EventActivityOption, EventVotingSession, EventActivityVote,
    PresentationQueue
)


class Command(BaseCommand):
    help = 'Set up complete test scenario for presentation system'

    def handle(self, *args, **options):
        self.stdout.write('Setting up presentation test scenario...\n')

        # 1. Create or get test coach
        self.stdout.write('1. Creating test coach...')
        coach_user, created = User.objects.get_or_create(
            username='testcoach',
            defaults={
                'email': 'coach@test.com',
                'first_name': 'Test',
                'last_name': 'Coach'
            }
        )
        if created:
            coach_user.set_password('testpass123')
            coach_user.save()
            self.stdout.write(self.style.SUCCESS('   [OK] Created coach user: testcoach / testpass123'))

        coach, created = CrushCoach.objects.get_or_create(
            user=coach_user,
            defaults={
                'bio': 'Test coach for presentations',
                'specializations': 'Testing',
                'max_active_reviews': 10
            }
        )

        # 2. Create test event
        self.stdout.write('2. Creating test event...')
        event, created = MeetupEvent.objects.get_or_create(
            title='Test Presentation Event',
            defaults={
                'description': 'Test event for presentation system',
                'event_type': 'speed_dating',
                'date_time': timezone.now() - timedelta(minutes=20),  # Started 20 min ago
                'location': 'Test Location',
                'max_participants': 10,
                'registration_fee': 0,
                'registration_deadline': timezone.now() + timedelta(days=1),
                'is_published': True
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'   [OK] Created event: {event.title} (ID: {event.id})'))
        else:
            self.stdout.write(self.style.WARNING(f'   ! Event already exists (ID: {event.id})'))

        # 3. Create test users with profiles
        self.stdout.write('3. Creating test users...')
        test_users = [
            ('alice', 'Alice', 'Wonder'),
            ('bob', 'Bob', 'Builder'),
            ('charlie', 'Charlie', 'Chaplin'),
            ('diana', 'Diana', 'Prince'),
            ('eve', 'Eve', 'Online'),
        ]

        users_created = []
        for username, first_name, last_name in test_users:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': f'{username}@test.com',
                    'first_name': first_name,
                    'last_name': last_name
                }
            )
            if created:
                user.set_password('testpass123')
                user.save()
                self.stdout.write(self.style.SUCCESS(f'   [OK] Created user: {username} / testpass123'))

            # Create Crush profile
            profile, p_created = CrushProfile.objects.get_or_create(
                user=user,
                defaults={
                    'date_of_birth': timezone.now().date() - timedelta(days=365*25),
                    'gender': 'M' if username in ['bob', 'charlie'] else 'F',
                    'location': 'Luxembourg',
                    'bio': f'Test profile for {first_name}',
                    'is_approved': True,
                    'approved_at': timezone.now()
                }
            )

            users_created.append(user)

        # 4. Register users for event
        self.stdout.write('4. Registering users for event...')
        for user in users_created:
            registration, created = EventRegistration.objects.get_or_create(
                event=event,
                user=user,
                defaults={
                    'status': 'confirmed',
                    'payment_confirmed': True
                }
            )
            if created:
                self.stdout.write(f'   [OK] Registered: {user.username}')

        # 5. Create activity options
        self.stdout.write('5. Creating activity options...')
        activity_definitions = [
            # Presentation Style variants (Phase 2)
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
            # Speed Dating Twist variants (Phase 3)
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

        for activity_def in activity_definitions:
            option, created = EventActivityOption.objects.get_or_create(
                event=event,
                activity_type=activity_def['activity_type'],
                activity_variant=activity_def['activity_variant'],
                defaults={
                    'display_name': activity_def['display_name'],
                    'description': activity_def['description']
                }
            )
            if created:
                self.stdout.write(f'   [OK] Created option: {option.display_name}')

        # 6. Create voting session
        self.stdout.write('6. Creating voting session...')
        voting_session, created = EventVotingSession.objects.get_or_create(
            event=event,
            defaults={
                'voting_start_time': event.date_time + timedelta(minutes=15),
                'voting_end_time': event.date_time + timedelta(minutes=45),
                'is_active': False  # Voting ended
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS('   [OK] Created voting session'))

        # 7. Simulate votes
        self.stdout.write('7. Simulating votes...')
        presentation_options = EventActivityOption.objects.filter(
            event=event,
            activity_type='presentation_style'
        )
        twist_options = EventActivityOption.objects.filter(
            event=event,
            activity_type='speed_dating_twist'
        )

        # Vote distribution (make "5 Questions" win for presentations)
        import random
        for user in users_created:
            # Vote for presentation style
            pres_option = presentation_options.get(activity_variant='questions')  # Make this one win
            vote, created = EventActivityVote.objects.get_or_create(
                event=event,
                user=user,
                selected_option=pres_option
            )
            if created:
                pres_option.vote_count += 1
                pres_option.save()

            # Vote for speed dating twist (random)
            twist_option = random.choice(list(twist_options))
            vote, created = EventActivityVote.objects.get_or_create(
                event=event,
                user=user,
                selected_option=twist_option,
                defaults={'selected_option': twist_option}
            )
            if created:
                twist_option.vote_count += 1
                twist_option.save()

        # Set total votes
        voting_session.total_votes = len(users_created)
        voting_session.save()
        self.stdout.write(self.style.SUCCESS(f'   [OK] Simulated {len(users_created)} votes'))

        # 8. Calculate winners
        self.stdout.write('8. Calculating winners...')
        voting_session.calculate_winner()

        pres_winner = EventActivityOption.objects.filter(
            event=event,
            activity_type='presentation_style',
            is_winner=True
        ).first()

        twist_winner = EventActivityOption.objects.filter(
            event=event,
            activity_type='speed_dating_twist',
            is_winner=True
        ).first()

        if pres_winner:
            self.stdout.write(self.style.SUCCESS(f'   [OK] Presentation winner: {pres_winner.display_name}'))
        if twist_winner:
            self.stdout.write(self.style.SUCCESS(f'   [OK] Speed dating winner: {twist_winner.display_name}'))

        # 9. Initialize presentation queue
        self.stdout.write('9. Initializing presentation queue...')
        voting_session.initialize_presentation_queue()

        queue_count = PresentationQueue.objects.filter(event=event).count()
        self.stdout.write(self.style.SUCCESS(f'   [OK] Created presentation queue with {queue_count} presenters'))

        # Print presentation order
        presentations = PresentationQueue.objects.filter(event=event).order_by('presentation_order')
        self.stdout.write('\n   Presentation Order:')
        for p in presentations:
            self.stdout.write(f'      #{p.presentation_order}: {p.user.crushprofile.display_name}')

        # 10. Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('[OK] Test setup complete!'))
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write('\nTest Accounts Created:')
        self.stdout.write(f'  Coach: testcoach / testpass123')
        for username, first_name, last_name in test_users:
            self.stdout.write(f'  User:  {username} / testpass123')

        self.stdout.write(f'\nEvent ID: {event.id}')
        self.stdout.write('\nURLs to Test:')
        self.stdout.write(f'  Attendee View: /events/{event.id}/presentations/')
        self.stdout.write(f'  Coach Control: /coach/events/{event.id}/presentations/control/')
        self.stdout.write('\nNext Steps:')
        self.stdout.write('  1. Login as testcoach and visit coach control panel')
        self.stdout.write('  2. Click "Start Presentations" to begin')
        self.stdout.write('  3. In another browser/tab, login as alice/bob/etc to rate')
        self.stdout.write('  4. Use coach panel to advance through presenters')
