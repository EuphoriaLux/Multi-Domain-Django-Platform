from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from crush_lu.models import CrushCoach


class Command(BaseCommand):
    help = 'Create sample Crush Coach profiles'

    def handle(self, *args, **options):
        coaches_data = [
            {
                'username': 'coach.marie',
                'email': 'marie@crush.lu',
                'first_name': 'Marie',
                'last_name': 'Dupont',
                'bio': 'Specializing in helping young professionals find meaningful connections. 5 years experience in relationship coaching.',
                'specializations': 'Young professionals, 25-35',
            },
            {
                'username': 'coach.thomas',
                'email': 'thomas@crush.lu',
                'first_name': 'Thomas',
                'last_name': 'Weber',
                'bio': 'Passionate about helping students and young adults navigate the dating world with confidence.',
                'specializations': 'Students, 18-25',
            },
            {
                'username': 'coach.sophie',
                'email': 'sophie@crush.lu',
                'first_name': 'Sophie',
                'last_name': 'Muller',
                'bio': 'Experienced coach focused on mature dating and second chances. Creating authentic connections for 35+.',
                'specializations': '35+, Professionals',
            },
        ]

        created_count = 0
        for coach_data in coaches_data:
            # Create user if doesn't exist
            user, user_created = User.objects.get_or_create(
                username=coach_data['username'],
                defaults={
                    'email': coach_data['email'],
                    'first_name': coach_data['first_name'],
                    'last_name': coach_data['last_name'],
                }
            )

            if user_created:
                user.set_password('crushcoach2025')  # Default password
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Created user: {user.username}'))

            # Create coach profile if doesn't exist
            coach, coach_created = CrushCoach.objects.get_or_create(
                user=user,
                defaults={
                    'bio': coach_data['bio'],
                    'specializations': coach_data['specializations'],
                    'is_active': True,
                    'max_active_reviews': 10,
                }
            )

            if coach_created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Created coach: {coach.user.get_full_name()} - {coach.specializations}'
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'Coach already exists: {coach.user.username}')
                )

        self.stdout.write(
            self.style.SUCCESS(f'\nSuccessfully created {created_count} new Crush Coaches')
        )
        self.stdout.write('Default password for all coaches: crushcoach2025')
