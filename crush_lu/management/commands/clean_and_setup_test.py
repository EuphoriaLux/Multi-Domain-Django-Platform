"""
Clean up all old event data and create fresh test data using GlobalActivityOptions.
This creates a clean testing environment with the new design.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from crush_lu.models import (
    MeetupEvent, EventRegistration, CrushProfile, CrushCoach,
    GlobalActivityOption, EventActivityOption, EventActivityVote,
    EventVotingSession, PresentationQueue, PresentationRating,
    SpeedDatingPair
)


class Command(BaseCommand):
    help = 'Clean up old data and create fresh test event with global options'

    def handle(self, *args, **options):
        self.stdout.write('='*60)
        self.stdout.write('CLEANING UP OLD DATA')
        self.stdout.write('='*60 + '\n')

        # 1. Delete all old events (cascades to related data)
        deleted_events = MeetupEvent.objects.all().delete()
        self.stdout.write(f'[OK] Deleted {deleted_events[0]} events and related data')

        # 2. Delete old EventActivityOptions (legacy)
        deleted_options = EventActivityOption.objects.all().delete()
        self.stdout.write(f'[OK] Deleted {deleted_options[0]} legacy event activity options')

        # 3. Keep GlobalActivityOptions (they're reusable!)
        global_options = GlobalActivityOption.objects.count()
        self.stdout.write(f'[OK] Keeping {global_options} global activity options (reusable!)')

        # 4. Keep test users and coaches
        users = User.objects.filter(username__in=['testcoach', 'alice', 'bob', 'charlie', 'diana', 'eve']).count()
        self.stdout.write(f'[OK] Keeping {users} test users\n')

        self.stdout.write('='*60)
        self.stdout.write('CREATING FRESH TEST DATA')
        self.stdout.write('='*60 + '\n')

        # 5. Create new test event
        self.stdout.write('1. Creating test event...')
        event = MeetupEvent.objects.create(
            title='Crush Event - Global Options Test',
            description='Testing the new global activity options design',
            event_type='speed_dating',
            location='Test Venue Luxembourg',
            address='123 Test Street, Luxembourg',
            date_time=timezone.now() + timedelta(hours=1),  # Starts in 1 hour
            duration_minutes=180,
            registration_deadline=timezone.now() + timedelta(days=1),
            registration_fee=0.00,
            is_published=True,
            max_participants=10,
            min_age=18,
            max_age=99
        )
        self.stdout.write(self.style.SUCCESS(f'   [OK] Event created: {event.title} (ID: {event.id})'))

        # 6. Get test users
        self.stdout.write('2. Registering test users...')
        test_users = User.objects.filter(username__in=['alice', 'bob', 'charlie', 'diana', 'eve'])

        if test_users.count() == 0:
            self.stdout.write(self.style.WARNING('   [WARN] No test users found! Run setup_presentation_test first.'))
            return

        for user in test_users:
            EventRegistration.objects.create(
                event=event,
                user=user,
                status='confirmed',
                payment_confirmed=True
            )
            self.stdout.write(f'   [OK] Registered: {user.username}')

        # 7. Create voting session
        self.stdout.write('3. Creating voting session...')
        voting_session = EventVotingSession.objects.create(
            event=event,
            voting_start_time=timezone.now() - timedelta(minutes=5),  # Started 5 min ago
            voting_end_time=timezone.now() + timedelta(minutes=25),   # Ends in 25 min
            is_active=True,
            total_votes=0
        )
        self.stdout.write(self.style.SUCCESS(f'   [OK] Voting session created (ACTIVE)'))
        self.stdout.write(f'        Voting: {voting_session.voting_start_time.strftime("%H:%M")} - {voting_session.voting_end_time.strftime("%H:%M")}')

        # 8. Verify global options exist
        self.stdout.write('4. Verifying global activity options...')
        presentation_options = GlobalActivityOption.objects.filter(activity_type='presentation_style', is_active=True)
        twist_options = GlobalActivityOption.objects.filter(activity_type='speed_dating_twist', is_active=True)

        self.stdout.write(f'   [OK] {presentation_options.count()} presentation style options available')
        self.stdout.write(f'   [OK] {twist_options.count()} speed dating twist options available')

        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('SETUP COMPLETE!'))
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(f'\nEvent ID: {event.id}')
        self.stdout.write(f'Registered Users: {test_users.count()}')
        self.stdout.write(f'Global Options: {GlobalActivityOption.objects.count()} (shared by all events)')
        self.stdout.write('\nTest Accounts:')
        self.stdout.write('  Coach: testcoach / testpass123')
        for user in test_users:
            self.stdout.write(f'  User:  {user.username} / testpass123')

        self.stdout.write('\nURLs to Test:')
        self.stdout.write(f'  Voting:      /events/{event.id}/voting/')
        self.stdout.write(f'  Results:     /events/{event.id}/voting/results/')
        self.stdout.write(f'  Presentations: /events/{event.id}/presentations/')
        self.stdout.write(f'  Coach Control: /coach/events/{event.id}/presentations/control/')

        self.stdout.write('\nNEXT STEPS:')
        self.stdout.write('  1. Views need to be updated to use GlobalActivityOption')
        self.stdout.write('  2. Templates need to be updated to use GlobalActivityOption')
        self.stdout.write('  3. Then you can test the complete flow!')
