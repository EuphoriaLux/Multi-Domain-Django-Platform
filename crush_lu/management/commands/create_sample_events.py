from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crush_lu.models import MeetupEvent


class Command(BaseCommand):
    help = 'Create sample meetup events for Crush.lu'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete all existing events before creating new ones',
        )

    def handle(self, *args, **options):
        if options['clear']:
            deleted_count = MeetupEvent.objects.all().count()
            MeetupEvent.objects.all().delete()
            self.stdout.write(
                self.style.WARNING(f'Deleted {deleted_count} existing events')
            )

        # Base datetime for events (starting next week)
        base_date = timezone.now() + timedelta(days=7)

        events_data = [
            {
                'title': '❤️ Saint-Valentin au Bar – Rencontre & Finalisation de profil',
                'description': '''Suite à un fort succès, nous avons décidé d'organiser une après-midi spéciale Saint-Valentin, de 14h à 18h, dans un bar convivial, pour permettre aux participants de finaliser leur profil sur le site… tout en faisant des rencontres en vrai ☕🍷

L'objectif de l'événement est simple :

👉 venir sur place et retrouver les deux personnes reconnaissables grâce à leur couvre-chef inspiré de l'univers d'Alice au Pays des Merveilles, qui seront là pour t'aider à compléter et valider ton profil.

Pas d'activités programmées, pas de pression. Juste une ambiance détendue, des échanges naturels, et un moment agréable à partager autour d'un verre.

Que tu sois déjà inscrit(e) ou que tu souhaites finaliser ton inscription, cette après-midi Saint-Valentin est l'occasion idéale pour :
• terminer ton profil en toute simplicité
• poser tes questions
• rencontrer d'autres membres
• et profiter d'un moment chaleureux

On t'attend pour une Saint-Valentin différente, authentique et sans prise de tête 💕''',
                'event_type': 'activity',
                'location': 'ATMOS',
                'address': 'Place de la Gare, L-1616 Luxembourg',
                'canton': 'Luxembourg',
                'date_time': base_date.replace(hour=14, minute=0, second=0, microsecond=0),
                'duration_minutes': 240,  # 4 hours
                'max_participants': 300,
                'min_age': 18,
                'max_age': 99,
                'registration_fee': 0.00,
                'require_approved_profile': False,  # Allow non-approved users to attend
            },
            {
                'title': '🎉 Friday After-Work Mixer – Happy Hour & Networking',
                'description': '''Kick off your weekend with our popular Friday after-work mixer!

Join us for a relaxed happy hour where you can:
• Meet new people in a casual, pressure-free environment
• Enjoy special drink prices from 18h-20h
• Network with other young professionals in Luxembourg
• Play fun icebreaker games (optional!)

No formal structure, no pressure—just good vibes, great company, and the perfect way to start your weekend.

Whether you're new to Luxembourg or a long-time resident, this is the perfect opportunity to expand your social circle and make genuine connections.

Dress code: Casual chic
Languages: EN, FR, DE welcome 🇬🇧🇫🇷🇩🇪''',
                'event_type': 'mixer',
                'location': 'Urban Bar',
                'address': '12 Rue de la Reine, L-2418 Luxembourg',
                'canton': 'Luxembourg',
                'date_time': (base_date + timedelta(days=5)).replace(hour=18, minute=0, second=0, microsecond=0),
                'duration_minutes': 180,
                'max_participants': 50,
                'min_age': 21,
                'max_age': 45,
                'registration_fee': 0.00,
            },
            {
                'title': '🍷 Wine Tasting & Speed Dating – Moselle Edition',
                'description': '''Combine your love for wine with the search for love!

Join us in the beautiful Moselle region for an elegant evening of:
• Guided tasting of 5 premium Luxembourg wines
• 7-minute speed dating rounds with 10-15 potential matches
• Gourmet appetizers paired with each wine
• Stunning vineyard views as the sun sets

Our sommelier will guide you through the tasting while you get to know interesting people in a sophisticated setting. Each wine comes with a fun conversation starter to keep things flowing smoothly.

Perfect for wine enthusiasts looking for a refined dating experience!

What's included:
✓ Wine tasting (5 wines)
✓ Appetizers & snacks
✓ Professional sommelier guidance
✓ Speed dating facilitation
✓ Complimentary water & soft drinks

Limited to 24 participants for an intimate experience.''',
                'event_type': 'speed_dating',
                'location': 'Vinothèque Remich',
                'address': '15 Route du Vin, L-5440 Remich',
                'canton': 'Remich',
                'date_time': (base_date + timedelta(days=10)).replace(hour=19, minute=30, second=0, microsecond=0),
                'duration_minutes': 150,
                'max_participants': 24,
                'min_age': 28,
                'max_age': 50,
                'registration_fee': 25.00,
            },
            {
                'title': '🎨 Art Gallery Opening & Singles Mixer',
                'description': '''Discover local art while discovering interesting people!

Experience a unique combination of culture and connection at this exclusive art gallery opening turned singles event.

The evening includes:
• Private viewing of new contemporary art exhibition
• Guided tour by the artist (30 minutes)
• Complimentary welcome drink & canapés
• Interactive art-themed conversation starters
• Live acoustic music
• Relaxed mingling in beautiful surroundings

This isn't your typical dating event—it's for people who appreciate art, culture, and meaningful conversations. No speed dating, no forced interactions, just organic connections in an inspiring space.

Art lovers, creative souls, and culture enthusiasts welcome!

Dress code: Smart casual (this is an art opening!)
Languages: Multilingual crowd expected 🇱🇺🇫🇷🇩🇪🇬🇧''',
                'event_type': 'themed',
                'location': 'Neimënster Cultural Centre',
                'address': '28 Rue Münster, L-2160 Luxembourg',
                'canton': 'Luxembourg',
                'date_time': (base_date + timedelta(days=15)).replace(hour=19, minute=0, second=0, microsecond=0),
                'duration_minutes': 180,
                'max_participants': 40,
                'min_age': 25,
                'max_age': 55,
                'registration_fee': 15.00,
            },
            {
                'title': '🥾 Mullerthal Trail Hike & Brunch – Active Singles',
                'description': '''For the outdoor enthusiasts! Combine nature, exercise, and social connections.

Join us for a beautiful morning hike in Luxembourg's "Little Switzerland" followed by a well-deserved brunch.

Schedule:
• 09:00 - Meet at parking lot, introductions
• 09:30 - Start moderate 2-hour hike (8km)
• 11:30 - Arrive at scenic brunch spot
• 12:00 - Enjoy hearty Luxembourg brunch together
• 14:00 - Event ends (optional coffee in town)

The hike is moderate difficulty—you should be comfortable walking for 2 hours with some elevation. Perfect for meeting fellow nature lovers in a relaxed, active setting.

What to bring:
✓ Good hiking shoes (trails can be muddy!)
✓ Water bottle (1L recommended)
✓ Weather-appropriate clothing
✓ Your best smile 😊

Brunch includes: Coffee/tea, fresh bread, local cheeses & meats, eggs, fresh fruit, pastries

Limited to 20 participants for a personal experience. Rain or shine (we'll provide route alternatives for bad weather).''',
                'event_type': 'activity',
                'location': 'Mullerthal Trail - Beaufort',
                'address': 'Parking Heringermill, L-6360 Beaufort',
                'canton': 'Beaufort',
                'date_time': (base_date + timedelta(days=16)).replace(hour=9, minute=0, second=0, microsecond=0),
                'duration_minutes': 300,  # 5 hours total
                'max_participants': 20,
                'min_age': 22,
                'max_age': 45,
                'registration_fee': 18.00,
            },
            {
                'title': '🎭 Masquerade Ball – Mystery & Romance',
                'description': '''Step into a world of mystery, elegance, and intrigue at our Masquerade Ball!

This isn't just another dating event—it's an experience. Don your finest attire and a mysterious mask as you step into an evening of:

• Elegant ballroom atmosphere with live string quartet
• Masked speed dating rounds (7 minutes each)
• Identity reveal at midnight
• Professional ballroom dance lesson (optional)
• Complimentary welcome champagne
• Gourmet finger foods throughout the evening
• Best mask competition with prizes!

The twist? You won't know who's behind the mask until the grand reveal at midnight. Focus on personality, conversation, and connection without the pressure of immediate physical judgment.

Dress code: Formal/cocktail attire + mask (we'll provide basic masks if you forget yours)
Music: Live string quartet + DJ for dancing after midnight

This is our most popular event—book early!

What's included:
✓ Mask (if needed)
✓ Welcome champagne
✓ Food throughout evening
✓ Dance lesson
✓ Speed dating facilitation
✓ After-midnight DJ & dancing''',
                'event_type': 'themed',
                'location': 'Casino Luxembourg - Forum d\'art contemporain',
                'address': '41 Rue Notre-Dame, L-2240 Luxembourg',
                'canton': 'Luxembourg',
                'date_time': (base_date + timedelta(days=21)).replace(hour=20, minute=0, second=0, microsecond=0),
                'duration_minutes': 240,
                'max_participants': 60,
                'min_age': 25,
                'max_age': 50,
                'registration_fee': 35.00,
            },
            {
                'title': '☕ Sunday Brunch & Board Games – Laid-Back Connections',
                'description': '''The perfect low-key Sunday activity for people who prefer games to small talk!

Start your Sunday with delicious brunch and fun board games in a cozy café atmosphere.

We'll have 6-8 different board games set up at tables:
• Strategy games (Catan, Ticket to Ride)
• Party games (Codenames, Dixit)
• Classic games (Scrabble, Chess)
• Quick social games (Love Letter, Exploding Kittens)

How it works:
• Arrive anytime between 11h-11:30h
• Order from brunch menu (pay for your own food)
• Join a table/game that interests you
• Rotate tables every 45 minutes for variety
• Casual, pressure-free mingling

Perfect for:
• Introverts who find conversation easier over games
• Board game enthusiasts
• People who hate traditional "dating events"
• Anyone looking for a relaxed Sunday social activity

No RSVP limits on games or activities—just show up, eat, play, and make connections naturally.

Languages: Mix of EN, FR, DE—games are universal! 🎲''',
                'event_type': 'activity',
                'location': 'Chocolate House Café',
                'address': '45 Avenue de la Liberté, L-1931 Luxembourg',
                'canton': 'Luxembourg',
                'date_time': (base_date + timedelta(days=23)).replace(hour=11, minute=0, second=0, microsecond=0),
                'duration_minutes': 180,
                'max_participants': 35,
                'min_age': 21,
                'max_age': 40,
                'registration_fee': 0.00,  # Free event, participants pay for their own food
            },
            {
                'title': '🎤 Karaoke Night – Sing Your Heart Out & Meet People',
                'description': '''Let loose and show off your vocal talents (or lack thereof!) at our karaoke singles night!

No judgment, all fun, and plenty of laughs. Whether you're a shower singer or a hidden star, this is the perfect icebreaker event.

The vibe:
• Private karaoke room with professional setup
• Song catalog: 10,000+ songs in multiple languages
• Solo performances, duets, and group numbers welcome
• Drink specials all night
• Prizes for best performance, worst performance, and best duet!

Perfect for breaking the ice—it's hard to stay shy when you're belting out "Bohemian Rhapsody" with strangers who quickly become friends.

No pressure to perform! You can enjoy the show from the audience if you prefer.

Group warm-up: We'll start with a group number to get everyone comfortable 🎶

Drinks & snacks available for purchase (special karaoke menu).

Tip: Liquid courage is available at the bar 🍹😉''',
                'event_type': 'themed',
                'location': 'Rock Box Karaoke Bar',
                'address': '8 Boulevard J.F. Kennedy, L-4170 Esch-sur-Alzette',
                'canton': 'Esch-sur-Alzette',
                'date_time': (base_date + timedelta(days=26)).replace(hour=20, minute=0, second=0, microsecond=0),
                'duration_minutes': 180,
                'max_participants': 30,
                'min_age': 23,
                'max_age': 45,
                'registration_fee': 12.00,
            },
        ]

        created_count = 0
        for event_data in events_data:
            # Set registration deadline to 2 days before event (or 2 hours before for Saint-Valentin)
            if 'Saint-Valentin' in event_data['title']:
                event_data['registration_deadline'] = event_data['date_time'] - timedelta(hours=46)  # ~2 days
            else:
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
                # Remove emojis from console output for Windows compatibility
                safe_title = event.title.encode('ascii', 'ignore').decode('ascii')
                self.stdout.write(
                    self.style.SUCCESS(
                        f'[+] Created: {safe_title[:60]}... on {event.date_time.strftime("%Y-%m-%d %H:%M")}'
                    )
                )
            else:
                safe_title = event.title.encode('ascii', 'ignore').decode('ascii')
                self.stdout.write(
                    self.style.WARNING(f'[!] Already exists: {safe_title[:60]}...')
                )

        self.stdout.write(
            self.style.SUCCESS(f'\nSuccessfully created {created_count} new events!')
        )
