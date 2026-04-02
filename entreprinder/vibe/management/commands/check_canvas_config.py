from django.core.management.base import BaseCommand
from django.utils import timezone
from entreprinder.vibe.models import PixelCanvas, UserPixelCooldown
import json


class Command(BaseCommand):
    help = 'Check and optionally fix Canvas rate limiting configuration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Fix Canvas configuration to match expected defaults',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed information including cooldown records',
        )
        parser.add_argument(
            '--canvas-id',
            type=int,
            help='Check specific canvas by ID (default: all canvases)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🔍 Canvas Rate Limiting Configuration Check'))
        self.stdout.write('=' * 60)

        # Expected default values
        expected_config = {
            'anonymous_cooldown_seconds': 10,
            'registered_cooldown_seconds': 10,
            'anonymous_pixels_per_minute': 6,
            'registered_pixels_per_minute': 12,
        }

        # Get canvases to check
        if options['canvas_id']:
            try:
                canvases = [PixelCanvas.objects.get(id=options['canvas_id'])]
            except PixelCanvas.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'❌ Canvas with ID {options["canvas_id"]} not found')
                )
                return
        else:
            canvases = PixelCanvas.objects.all()

        if not canvases:
            self.stdout.write(self.style.WARNING('⚠️  No canvases found in database'))
            return

        issues_found = False

        for canvas in canvases:
            self.stdout.write(f'\n📊 Canvas: "{canvas.name}" (ID: {canvas.id})')
            self.stdout.write(f'   Size: {canvas.width}×{canvas.height}')
            self.stdout.write(f'   Active: {"✅" if canvas.is_active else "❌"}')

            # Check each configuration value
            config_issues = []
            current_config = {
                'anonymous_cooldown_seconds': canvas.anonymous_cooldown_seconds,
                'registered_cooldown_seconds': canvas.registered_cooldown_seconds,
                'anonymous_pixels_per_minute': canvas.anonymous_pixels_per_minute,
                'registered_pixels_per_minute': canvas.registered_pixels_per_minute,
            }

            self.stdout.write('\n📋 Current Configuration:')
            for key, current_value in current_config.items():
                expected_value = expected_config[key]
                status_icon = "✅" if current_value == expected_value else "⚠️"

                self.stdout.write(f'   {status_icon} {key}: {current_value}')

                if current_value != expected_value:
                    config_issues.append({
                        'field': key,
                        'current': current_value,
                        'expected': expected_value
                    })
                    issues_found = True

            # Show configuration issues
            if config_issues:
                self.stdout.write(f'\n🚨 Found {len(config_issues)} configuration issue(s):')
                for issue in config_issues:
                    self.stdout.write(
                        f'   • {issue["field"]}: {issue["current"]} → should be {issue["expected"]}'
                    )

                # Fix configuration if requested
                if options['fix']:
                    self.stdout.write('\n🔧 Applying fixes...')
                    for key, expected_value in expected_config.items():
                        setattr(canvas, key, expected_value)
                    canvas.save()
                    self.stdout.write(
                        self.style.SUCCESS(f'✅ Fixed Canvas "{canvas.name}" configuration')
                    )
            else:
                self.stdout.write(self.style.SUCCESS('\n✅ Configuration matches expected defaults'))

            # Show verbose information if requested
            if options['verbose']:
                self.stdout.write('\n📈 Cooldown Statistics:')

                # Count cooldown records
                auth_cooldowns = UserPixelCooldown.objects.filter(
                    canvas=canvas, user__isnull=False
                ).count()
                anon_cooldowns = UserPixelCooldown.objects.filter(
                    canvas=canvas, user__isnull=True
                ).count()

                self.stdout.write(f'   • Authenticated user cooldowns: {auth_cooldowns}')
                self.stdout.write(f'   • Anonymous user cooldowns: {anon_cooldowns}')

                # Recent activity
                recent_cooldowns = UserPixelCooldown.objects.filter(
                    canvas=canvas,
                    last_placed__gte=timezone.now() - timezone.timedelta(hours=1)
                )

                if recent_cooldowns.exists():
                    self.stdout.write(f'\n🕒 Recent Activity (last hour):')
                    for cooldown in recent_cooldowns[:5]:  # Show first 5
                        user_type = "Authenticated" if cooldown.user else "Anonymous"
                        user_id = cooldown.user.username if cooldown.user else (cooldown.session_key[:8] if cooldown.session_key else "Unknown")
                        self.stdout.write(
                            f'   • {user_type} ({user_id}): '
                            f'{cooldown.pixels_placed_last_minute} pixels this minute'
                        )
                else:
                    self.stdout.write('   📭 No recent activity in the last hour')

        # Summary
        self.stdout.write('\n' + '=' * 60)

        if issues_found and not options['fix']:
            self.stdout.write(
                self.style.WARNING('⚠️  Configuration issues found! Run with --fix to correct them.')
            )
            self.stdout.write('\nExample: python manage.py check_canvas_config --fix')
        elif issues_found and options['fix']:
            self.stdout.write(
                self.style.SUCCESS('✅ All configuration issues have been fixed!')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('✅ All Canvas configurations are correct!')
            )

        self.stdout.write('\n📊 Expected Default Values:')
        for key, value in expected_config.items():
            self.stdout.write(f'   • {key}: {value}')

        # JavaScript configuration check
        self.stdout.write(f'\n🔧 To verify frontend configuration, check these template variables:')
        self.stdout.write(f'   • {{{{ canvas.anonymous_cooldown_seconds }}}}')
        self.stdout.write(f'   • {{{{ canvas.registered_cooldown_seconds }}}}')
        self.stdout.write(f'   • {{{{ canvas.anonymous_pixels_per_minute }}}}')
        self.stdout.write(f'   • {{{{ canvas.registered_pixels_per_minute }}}}')
