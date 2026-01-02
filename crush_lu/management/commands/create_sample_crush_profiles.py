"""
Create sample Crush.lu user profiles with photos for local development testing.

Downloads profile photos from randomuser.me API and uploads them to storage
(Azurite in development, Azure Blob Storage in production).

Usage:
    python manage.py create_sample_crush_profiles
    python manage.py create_sample_crush_profiles --count 50
    python manage.py create_sample_crush_profiles --skip-photos
"""

import random
import requests
from datetime import date, timedelta
from io import BytesIO

from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.contrib.auth.models import User
from django.utils import timezone

from crush_lu.models import CrushProfile, ProfileSubmission


# Luxembourg locations with realistic distribution
LUXEMBOURG_LOCATIONS = [
    ('Luxembourg City', 40),  # 40% weight - capital
    ('Esch-sur-Alzette', 15),
    ('Differdange', 8),
    ('Dudelange', 7),
    ('Ettelbruck', 5),
    ('Diekirch', 5),
    ('Wiltz', 3),
    ('Echternach', 3),
    ('Remich', 3),
    ('Vianden', 2),
    ('Clervaux', 2),
    ('Mersch', 3),
    ('Grevenmacher', 2),
    ('Mamer', 2),
]

# Realistic bio templates
BIO_TEMPLATES = [
    "Love exploring Luxembourg's hidden gems. Weekend hiker, coffee enthusiast, and "
    "{interest} fan. Looking to meet genuine people for {looking_for}.",

    "Originally from {origin}, now calling Luxembourg home. Passionate about {interest} "
    "and always up for trying new restaurants. {emoji}",

    "Work in {job_field} but my real passion is {interest}. Always looking for adventure "
    "partners and interesting conversations!",

    "Life's too short for boring moments! You'll find me {activity} on weekends. "
    "Let's grab a {drink} and see where it goes.",

    "Curious soul with a love for {interest}. Fluent in {languages}. "
    "Here to make meaningful connections.",

    "{interest} enthusiast | {job_field} professional | Always planning the next {activity}",

    "Just moved to Luxembourg and excited to explore! Love {interest}, good food, "
    "and spontaneous road trips. Say hi!",

    "Believer in good vibes and great coffee. {interest} keeps me sane. "
    "Looking for someone who doesn't take life too seriously.",
]

INTERESTS_POOL = [
    'hiking', 'photography', 'cooking', 'wine tasting', 'travel',
    'reading', 'yoga', 'cycling', 'music', 'art', 'cinema',
    'running', 'swimming', 'tennis', 'board games', 'dancing',
    'technology', 'startups', 'languages', 'volunteering', 'nature',
    'fitness', 'meditation', 'skiing', 'concerts', 'museums'
]

JOB_FIELDS = [
    'finance', 'tech', 'consulting', 'law', 'healthcare',
    'education', 'marketing', 'HR', 'engineering', 'architecture'
]

ORIGINS = [
    'France', 'Germany', 'Belgium', 'Portugal', 'Italy',
    'Spain', 'UK', 'Netherlands', 'Poland', 'Luxembourg'
]

DRINKS = ['coffee', 'wine', 'craft beer', 'cocktail', 'tea']

ACTIVITIES = ['hiking the Mullerthal', 'exploring old towns', 'trying new cafes', 'wine tours']

LANGUAGES = [
    'French and English', 'German and English', 'French and German',
    'English and Portuguese', 'Multiple languages', 'French, German, and English'
]

EMOJIS = ['', '', '', '', '', '', '', '']


def get_weighted_location():
    """Return a location based on weighted distribution."""
    locations = [loc for loc, _ in LUXEMBOURG_LOCATIONS]
    weights = [weight for _, weight in LUXEMBOURG_LOCATIONS]
    return random.choices(locations, weights=weights, k=1)[0]


def generate_bio(gender, looking_for):
    """Generate a realistic bio from templates."""
    template = random.choice(BIO_TEMPLATES)
    return template.format(
        interest=random.choice(INTERESTS_POOL),
        looking_for='friendship and maybe more' if looking_for == 'both' else looking_for,
        origin=random.choice(ORIGINS),
        job_field=random.choice(JOB_FIELDS),
        activity=random.choice(ACTIVITIES),
        drink=random.choice(DRINKS),
        languages=random.choice(LANGUAGES),
        emoji=random.choice(EMOJIS)
    )


def generate_interests():
    """Generate 3-5 random interests."""
    num_interests = random.randint(3, 5)
    selected = random.sample(INTERESTS_POOL, num_interests)
    return ', '.join(selected)


def generate_dob(min_age=18, max_age=55):
    """Generate random date of birth within age range."""
    today = date.today()
    min_date = today - timedelta(days=max_age * 365)
    max_date = today - timedelta(days=min_age * 365)
    days_range = (max_date - min_date).days
    random_days = random.randint(0, days_range)
    return min_date + timedelta(days=random_days)


class Command(BaseCommand):
    help = 'Create sample Crush.lu user profiles with photos for testing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--count',
            type=int,
            default=30,
            help='Number of profiles to create (default: 30)'
        )
        parser.add_argument(
            '--skip-photos',
            action='store_true',
            help='Skip downloading photos (faster but less realistic)'
        )
        parser.add_argument(
            '--password',
            type=str,
            default='testuser2025',
            help='Password for all test users (default: testuser2025)'
        )

    def handle(self, *args, **options):
        count = options['count']
        skip_photos = options['skip_photos']
        password = options['password']

        self.stdout.write(f'\nCreating {count} sample Crush.lu profiles...')
        if not skip_photos:
            self.stdout.write('Photos will be downloaded from randomuser.me API')

        # Determine gender distribution (roughly balanced)
        male_count = count // 2
        female_count = count - male_count

        created_count = 0
        photo_count = 0

        # Create male profiles
        for i in range(male_count):
            result = self.create_profile('male', i + 1, password, skip_photos)
            if result['created']:
                created_count += 1
                photo_count += result['photos']
            self.show_progress(i + 1, count)

        # Create female profiles
        for i in range(female_count):
            result = self.create_profile('female', male_count + i + 1, password, skip_photos)
            if result['created']:
                created_count += 1
                photo_count += result['photos']
            self.show_progress(male_count + i + 1, count)

        self.stdout.write('\n')
        self.stdout.write(self.style.SUCCESS(
            f'\nSuccessfully created {created_count} profiles with {photo_count} photos'
        ))
        self.stdout.write(f'Default password for all users: {password}')
        self.stdout.write(f'Email format: testuser{{N}}@crush.lu')

    def show_progress(self, current, total):
        """Show progress indicator."""
        percent = (current / total) * 100
        bar_length = 30
        filled = int(bar_length * current / total)
        bar = '' * filled + '' * (bar_length - filled)
        self.stdout.write(f'\r[{bar}] {percent:.0f}% ({current}/{total})', ending='')
        self.stdout.flush()

    def create_profile(self, gender, index, password, skip_photos):
        """Create a single user profile."""
        username = f'testuser{index}'
        email = f'testuser{index}@crush.lu'

        result = {'created': False, 'photos': 0}

        # Check if user already exists
        if User.objects.filter(username=username).exists():
            return result

        try:
            # Fetch random user data and photo from API
            api_gender = 'male' if gender == 'male' else 'female'
            if not skip_photos:
                user_data = self.fetch_random_user(api_gender)
            else:
                user_data = self.generate_fake_user_data(api_gender)

            if not user_data:
                return result

            # Create Django User
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=user_data['first_name'],
                last_name=user_data['last_name']
            )

            # Create CrushProfile
            gender_code = 'M' if gender == 'male' else 'F'
            looking_for = random.choice(['friends', 'dating', 'both', 'networking'])

            profile = CrushProfile.objects.create(
                user=user,
                date_of_birth=generate_dob(),
                gender=gender_code,
                phone_number=f'+352 {random.randint(600, 699)} {random.randint(100, 999)} {random.randint(100, 999)}',
                phone_verified=True,  # Mark as verified for testing
                phone_verified_at=timezone.now(),
                location=get_weighted_location(),
                bio=generate_bio(gender_code, looking_for),
                interests=generate_interests(),
                looking_for=looking_for,
                show_full_name=random.choice([True, False]),
                show_exact_age=random.choice([True, True, False]),  # 66% show exact age
                blur_photos=random.choice([False, False, False, True]),  # 25% blur
                preferred_language=random.choice(['en', 'en', 'de', 'fr']),  # More English
                is_approved=True,  # Pre-approve for testing
                is_active=True,
                approved_at=timezone.now(),
                completion_status='submitted',  # Profile was submitted and approved
            )

            # Upload photos if available
            if user_data.get('photo_data'):
                try:
                    photo_content = ContentFile(user_data['photo_data'])
                    profile.photo_1.save(
                        f'{user.id}_photo1.jpg',
                        photo_content,
                        save=True
                    )
                    result['photos'] = 1
                except Exception as e:
                    self.stderr.write(f'  Warning: Could not save photo: {e}')

            # Create approved ProfileSubmission record
            ProfileSubmission.objects.create(
                profile=profile,
                status='approved',
                coach_notes='Auto-created test profile',
                review_call_completed=True,
                review_call_date=timezone.now(),
                review_call_notes='Test profile - screening call simulated',
                reviewed_at=timezone.now()
            )

            result['created'] = True

        except Exception as e:
            self.stderr.write(f'\n  Error creating profile {index}: {e}')

        return result

    def fetch_random_user(self, gender):
        """Fetch random user data and photo from randomuser.me API."""
        try:
            # Request user data with specific parameters
            response = requests.get(
                'https://randomuser.me/api/',
                params={
                    'gender': gender,
                    'nat': 'fr,de,nl,be,gb,es,it,pt',  # European nationals
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if not data.get('results'):
                return None

            user = data['results'][0]

            # Download the large photo
            photo_url = user['picture']['large']
            photo_response = requests.get(photo_url, timeout=10)
            photo_response.raise_for_status()

            return {
                'first_name': user['name']['first'],
                'last_name': user['name']['last'],
                'photo_data': photo_response.content
            }

        except requests.RequestException as e:
            self.stderr.write(f'\n  Warning: API request failed: {e}')
            return self.generate_fake_user_data(gender)

    def generate_fake_user_data(self, gender):
        """Generate fake user data when API is unavailable."""
        male_names = [
            'Thomas', 'Lucas', 'Pierre', 'Nicolas', 'Jean', 'Marc',
            'Alexandre', 'Julien', 'Antoine', 'Maxime', 'Philippe',
            'François', 'David', 'Michel', 'Laurent'
        ]
        female_names = [
            'Marie', 'Sophie', 'Julie', 'Anne', 'Laura', 'Claire',
            'Sarah', 'Emma', 'Camille', 'Lea', 'Charlotte',
            'Isabelle', 'Nathalie', 'Caroline', 'Stephanie'
        ]
        last_names = [
            'Dupont', 'Martin', 'Bernard', 'Weber', 'Muller',
            'Schmit', 'Wagner', 'Klein', 'Hoffmann', 'Meyer',
            'Da Silva', 'Ferreira', 'Santos', 'Pereira', 'Costa'
        ]

        first_names = male_names if gender == 'male' else female_names

        return {
            'first_name': random.choice(first_names),
            'last_name': random.choice(last_names),
            'photo_data': None
        }
