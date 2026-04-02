from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.core.files import File
from entreprinder.models import EntrepreneurProfile, Industry, Skill
from django.conf import settings
import random
import os

class Command(BaseCommand):
    help = 'Creates test profiles for the Entreprinder application'

    def add_arguments(self, parser):
        parser.add_argument('total', type=int, help='Indicates the number of profiles to be created')

    def handle(self, *args, **kwargs):
        total = kwargs['total']
        industries = ['Technology', 'Finance', 'Healthcare', 'Education', 'Retail', 'Manufacturing', 'Entertainment', 'Food & Beverage']
        locations = ['New York', 'San Francisco', 'London', 'Tokyo', 'Berlin', 'Paris', 'Sydney', 'Toronto']
        skills = ['Programming', 'Marketing', 'Sales', 'Design', 'Finance', 'Management', 'Data Analysis', 'Customer Service']

        # Ensure industries and skills exist
        for industry in industries:
            Industry.objects.get_or_create(name=industry)
        for skill in skills:
            Skill.objects.get_or_create(name=skill)

        for i in range(total):
            username = f'testuser{i}'
            email = f'testuser{i}@example.com'
            password = 'testpassword123'
            user = User.objects.create_user(username=username, email=email, password=password)
            
            # Select a profile picture
            profile_pic_path = os.path.join(settings.MEDIA_ROOT, 'profile_pics', f'Test{i % 9 + 1}.jpg')
            
            profile = EntrepreneurProfile.objects.create(
                user=user,
                bio=f"Hi, I'm {user.username}! I'm passionate about entrepreneurship and innovation.",
                tagline=f"Innovator in {random.choice(industries)}",
                company=f"{user.username}'s Ventures",
                industry=Industry.objects.get(name=random.choice(industries)),
                location=random.choice(locations),
                looking_for='Seeking partnerships, mentorship, and potential investors',
                offering='Expertise in product development and market strategy',
                website=f'https://www.{username.lower()}.com',
                linkedin_profile=f'https://www.linkedin.com/in/{username.lower()}',
                years_of_experience=random.randint(1, 20),
                is_mentor=random.choice([True, False]),
                is_looking_for_funding=random.choice([True, False]),
                is_investor=random.choice([True, False])
            )
            
            # Add profile picture
            if os.path.exists(profile_pic_path):
                with open(profile_pic_path, 'rb') as f:
                    profile.profile_picture.save(f'Test{i % 9 + 1}.jpg', File(f), save=True)
            
            # Add random skills (between 3 and 6)
            profile_skills = random.sample(skills, random.randint(3, 6))
            for skill in profile_skills:
                profile.skills.add(Skill.objects.get(name=skill))
            
            self.stdout.write(self.style.SUCCESS(f'Successfully created profile for {username}'))

        self.stdout.write(self.style.SUCCESS(f'Successfully created {total} test profiles'))