from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crush_lu.models import MeetupEvent


class Command(BaseCommand):
    help = 'Create sample meetup events for Crush.lu'

    def handle(self, *args, **options):
        # Base datetime for events (starting next week)
        base_date = timezone.now() + timedelta(days=7)

        events_data = [
            {
                'title': 'Speed Dating: Young Professionals',
                'description': 'An evening of speed dating designed for young professionals in Luxembourg. 7-minute conversations with 10-15 potential matches. Enjoy complimentary drinks and a relaxed atmosphere.',
                'event_type': 'speed_dating',
                'location': 'Urban Bar, Luxembourg City',
                'address': '12 Rue de la Reine, L-2418 Luxembourg',
                'date_time': base_date.replace(hour=19, minute=0, second=0, microsecond=0),
                'duration_minutes': 150,
                'max_participants': 30,
                'min_age': 25,
                'max_age': 35,
                'registration_fee': 15.00,
            },
            {
                'title': 'Casual Friday Mixer',
                'description': 'Kick off your weekend at our casual Friday night mixer! Meet new people in a relaxed environment with music, drinks, and fun icebreaker games. No pressure, just good vibes.',
                'event_type': 'mixer',
                'location': 'Café des Artistes, Luxembourg',
                'address': '22 Montée du Grund, L-1645 Luxembourg',
                'date_time': (base_date + timedelta(days=2)).replace(hour=18, minute=30, second=0, microsecond=0),
                'duration_minutes': 180,
                'max_participants': 40,
                'min_age': 21,
                'max_age': 45,
                'registration_fee': 0.00,
            },
            {
                'title': 'Wine & Dine Speed Dating',
                'description': 'Combine great wine with great company! An upscale speed dating event featuring local wines and gourmet appetizers. Perfect for wine enthusiasts looking for sophisticated connections.',
                'event_type': 'speed_dating',
                'location': 'Vinothèque, Remich',
                'address': '15 Route du Vin, L-5440 Remich',
                'date_time': (base_date + timedelta(days=5)).replace(hour=19, minute=30, second=0, microsecond=0),
                'duration_minutes': 120,
                'max_participants': 24,
                'min_age': 28,
                'max_age': 45,
                'registration_fee': 25.00,
            },
            {
                'title': 'Outdoor Adventure Meetup',
                'description': 'For the active singles! Join us for a scenic hike in the Mullerthal followed by drinks and snacks. Great way to meet fellow outdoor enthusiasts in a natural setting.',
                'event_type': 'activity',
                'location': 'Mullerthal Trail, Beaufort',
                'address': 'Parking Heringermill, L-6360 Beaufort',
                'date_time': (base_date + timedelta(days=9)).replace(hour=10, minute=0, second=0, microsecond=0),
                'duration_minutes': 240,
                'max_participants': 20,
                'min_age': 22,
                'max_age': 40,
                'registration_fee': 10.00,
            },
            {
                'title': '90s Throwback Speed Dating',
                'description': 'Totally rad speed dating event with a 90s theme! Dress up in your best 90s outfit, enjoy nostalgic music, and meet people who share your love for the decade. Prizes for best costume!',
                'event_type': 'themed',
                'location': 'Retro Club, Esch-sur-Alzette',
                'address': '8 Boulevard J.F. Kennedy, L-4170 Esch-sur-Alzette',
                'date_time': (base_date + timedelta(days=12)).replace(hour=20, minute=0, second=0, microsecond=0),
                'duration_minutes': 180,
                'max_participants': 35,
                'min_age': 25,
                'max_age': 40,
                'registration_fee': 12.00,
            },
            {
                'title': 'Brunch & Mingle',
                'description': 'Start your Sunday right with a social brunch meetup. Enjoy delicious food, bottomless mimosas, and meet new people in a bright, friendly atmosphere. Perfect for laid-back connections.',
                'event_type': 'mixer',
                'location': 'Sunny Side Café, Luxembourg City',
                'address': '45 Avenue de la Liberté, L-1931 Luxembourg',
                'date_time': (base_date + timedelta(days=14)).replace(hour=11, minute=0, second=0, microsecond=0),
                'duration_minutes': 150,
                'max_participants': 30,
                'min_age': 23,
                'max_age': 38,
                'registration_fee': 18.00,
            },
        ]

        created_count = 0
        for event_data in events_data:
            # Set registration deadline to 2 days before event
            event_data['registration_deadline'] = event_data['date_time'] - timedelta(days=2)

            event, created = MeetupEvent.objects.get_or_create(
                title=event_data['title'],
                date_time=event_data['date_time'],
                defaults=event_data
            )

            if created:
                # Mark as published
                event.is_published = True
                event.save()
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Created event: {event.title} on {event.date_time.strftime("%Y-%m-%d %H:%M")}'
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'Event already exists: {event.title}')
                )

        self.stdout.write(
            self.style.SUCCESS(f'\nSuccessfully created {created_count} new events')
        )
