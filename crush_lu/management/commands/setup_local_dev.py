"""
Master setup command for local development environment.

This command initializes everything needed for local development:
1. Sets up Azurite blob containers (if Azurite mode is enabled)
2. Runs all platform-specific sample data commands
3. Creates sample Crush.lu profiles with photos
4. Reports summary of created data

Usage:
    python manage.py setup_local_dev
    python manage.py setup_local_dev --skip-photos
    python manage.py setup_local_dev --profiles-only
"""

import sys
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.conf import settings


class Command(BaseCommand):
    help = 'Set up complete local development environment with sample data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-photos',
            action='store_true',
            help='Skip downloading profile photos (faster setup)'
        )
        parser.add_argument(
            '--profiles-only',
            action='store_true',
            help='Only create Crush.lu profiles, skip other platform data'
        )
        parser.add_argument(
            '--profile-count',
            type=int,
            default=30,
            help='Number of Crush.lu profiles to create (default: 30)'
        )
        parser.add_argument(
            '--skip-azurite',
            action='store_true',
            help='Skip Azurite container setup'
        )

    def handle(self, *args, **options):
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('  Local Development Environment Setup'))
        self.stdout.write('=' * 60 + '\n')

        skip_photos = options['skip_photos']
        profiles_only = options['profiles_only']
        profile_count = options['profile_count']
        skip_azurite = options['skip_azurite']

        results = {
            'azurite': False,
            'coaches': False,
            'events': False,
            'activities': False,
            'producers': False,
            'plots': False,
            'profiles': False,
        }

        # Step 1: Set up Azurite containers
        if not skip_azurite and getattr(settings, 'AZURITE_MODE', False):
            self.stdout.write('\n[1/7] Setting up Azurite containers...')
            results['azurite'] = self.setup_azurite()
        else:
            self.stdout.write('\n[1/7] Skipping Azurite setup (not in Azurite mode or --skip-azurite)')
            results['azurite'] = 'skipped'

        if not profiles_only:
            # Step 2: Create Crush.lu coaches
            self.stdout.write('\n[2/7] Creating Crush.lu coaches...')
            results['coaches'] = self.run_command_safe('create_crush_coaches')

            # Step 3: Create sample events
            self.stdout.write('\n[3/7] Creating sample events...')
            results['events'] = self.run_command_safe('create_sample_events')

            # Step 4: Create global activity options
            self.stdout.write('\n[4/7] Creating global activity options...')
            results['activities'] = self.run_command_safe('populate_global_activity_options')

            # Step 5: Create VinsDelux producers
            self.stdout.write('\n[5/7] Creating VinsDelux producers...')
            results['producers'] = self.run_command_safe('create_luxembourg_producers')

            # Step 6: Create VinsDelux plots
            self.stdout.write('\n[6/7] Creating VinsDelux plots...')
            results['plots'] = self.run_command_safe('create_sample_plots')
        else:
            self.stdout.write('\n[2-6/7] Skipping platform data (--profiles-only)')
            for key in ['coaches', 'events', 'activities', 'producers', 'plots']:
                results[key] = 'skipped'

        # Step 7: Create Crush.lu profiles with photos
        self.stdout.write(f'\n[7/7] Creating {profile_count} Crush.lu profiles...')
        results['profiles'] = self.create_crush_profiles(profile_count, skip_photos)

        # Print summary
        self.print_summary(results)

    def setup_azurite(self):
        """Initialize Azurite blob containers."""
        try:
            # Import and run the setup script
            sys.path.insert(0, str(settings.BASE_DIR / 'scripts'))
            from setup_azurite import setup_containers
            success = setup_containers()
            if success:
                self.stdout.write(self.style.SUCCESS('  Azurite containers ready'))
            return success
        except ImportError:
            self.stdout.write(self.style.WARNING('  Could not import setup_azurite.py'))
            return False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Azurite setup failed: {e}'))
            return False

    def run_command_safe(self, command_name, **kwargs):
        """Run a management command safely, catching errors."""
        try:
            call_command(command_name, verbosity=1, **kwargs)
            return True
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  Warning: {command_name} failed: {e}'))
            return False

    def create_crush_profiles(self, count, skip_photos):
        """Create sample Crush.lu profiles."""
        try:
            call_command(
                'create_sample_crush_profiles',
                count=count,
                skip_photos=skip_photos,
                verbosity=1
            )
            return True
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Profile creation failed: {e}'))
            return False

    def print_summary(self, results):
        """Print summary of setup results."""
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('  Setup Complete!'))
        self.stdout.write('=' * 60)

        self.stdout.write('\nResults:')
        for step, success in results.items():
            if success == 'skipped':
                status = self.style.WARNING('')
            elif success:
                status = self.style.SUCCESS('')
            else:
                status = self.style.ERROR('')

            step_name = step.replace('_', ' ').title()
            self.stdout.write(f'  {status} {step_name}')

        self.stdout.write('\n' + '-' * 60)
        self.stdout.write('Next steps:')
        self.stdout.write('  1. Create a superuser: python manage.py createsuperuser')
        self.stdout.write('  2. Run the server: python manage.py runserver')
        self.stdout.write('  3. Access the site at: http://localhost:8000')
        self.stdout.write('\nTest user credentials:')
        self.stdout.write('  Email: testuser1@crush.lu (through testuser30@crush.lu)')
        self.stdout.write('  Password: testuser2025')
        self.stdout.write('\nCoach credentials:')
        self.stdout.write('  Email: marie@crush.lu, thomas@crush.lu, sophie@crush.lu')
        self.stdout.write('  Password: crushcoach2025')
        self.stdout.write('-' * 60 + '\n')
