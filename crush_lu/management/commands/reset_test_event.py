"""
Reset test event to clean state for testing the complete event flow.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crush_lu.models import (
    MeetupEvent, EventActivityOption, EventVotingSession,
    EventActivityVote, PresentationQueue, PresentationRating,
    SpeedDatingPair
)


class Command(BaseCommand):
    help = 'Reset test event to clean state for testing'

    def handle(self, *args, **options):
        self.stdout.write('Resetting test event...\n')

        try:
            event = MeetupEvent.objects.get(title='Test Presentation Event')
        except MeetupEvent.DoesNotExist:
            self.stdout.write(self.style.ERROR('Test event not found! Run setup_presentation_test first.'))
            return

        # 1. Clear all votes
        deleted_votes = EventActivityVote.objects.filter(event=event).delete()
        self.stdout.write(f'[OK] Deleted {deleted_votes[0]} votes')

        # 2. Clear all presentation queue entries
        deleted_queue = PresentationQueue.objects.filter(event=event).delete()
        self.stdout.write(f'[OK] Deleted {deleted_queue[0]} presentation queue entries')

        # 3. Clear all ratings
        deleted_ratings = PresentationRating.objects.filter(event=event).delete()
        self.stdout.write(f'[OK] Deleted {deleted_ratings[0]} ratings')

        # 4. Clear speed dating pairs
        deleted_pairs = SpeedDatingPair.objects.filter(event=event).delete()
        self.stdout.write(f'[OK] Deleted {deleted_pairs[0]} speed dating pairs')

        # 5. Reset activity options (clear vote counts and winners)
        options = EventActivityOption.objects.filter(event=event)
        options.update(vote_count=0, is_winner=False)
        self.stdout.write(f'[OK] Reset {options.count()} activity options')

        # 6. Reset voting session
        try:
            voting_session = EventVotingSession.objects.get(event=event)

            # Set voting to be active and in progress
            voting_session.voting_start_time = timezone.now() - timedelta(minutes=5)  # Started 5 min ago
            voting_session.voting_end_time = timezone.now() + timedelta(minutes=25)   # Ends in 25 min
            voting_session.is_active = True
            voting_session.total_votes = 0
            voting_session.winning_option = None
            voting_session.save()

            self.stdout.write('[OK] Reset voting session (voting is now ACTIVE)')
            self.stdout.write(f'     Voting window: {voting_session.voting_start_time.strftime("%H:%M")} - {voting_session.voting_end_time.strftime("%H:%M")}')

        except EventVotingSession.DoesNotExist:
            self.stdout.write(self.style.WARNING('[WARN] No voting session found'))

        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
        self.stdout.write(self.style.SUCCESS('[OK] Test event reset complete!'))
        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(f'\nEvent ID: {event.id}')
        self.stdout.write('\nYou can now test the complete flow:')
        self.stdout.write('  1. Vote: /events/{}/voting/'.format(event.id))
        self.stdout.write('  2. View Results: /events/{}/voting/results/'.format(event.id))
        self.stdout.write('  3. Presentations: /events/{}/presentations/'.format(event.id))
        self.stdout.write('  4. Coach Control: /coach/events/{}/presentations/control/'.format(event.id))
        self.stdout.write('  5. My Scores: /events/{}/presentations/my-scores/'.format(event.id))
