from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth.models import User
from vibe_coding.models import PixelCanvas, UserPixelCooldown
from datetime import timedelta


class Command(BaseCommand):
    help = 'Reset rate limiting data for testing and troubleshooting'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user',
            type=str,
            help='Reset rate limits for specific user by username',
        )
        parser.add_argument(
            '--session',
            type=str,
            help='Reset rate limits for specific session key',
        )
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='Reset rate limits for all users (DANGER: Use only for testing)',
        )
        parser.add_argument(
            '--expired-only',
            action='store_true',
            help='Only reset cooldowns that have expired (safe option)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be reset without actually doing it',
        )
        parser.add_argument(
            '--canvas-id',
            type=int,
            help='Target specific canvas by ID (default: all canvases)',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🔄 Rate Limiting Reset Tool'))
        self.stdout.write('=' * 60)
        
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('🧪 DRY RUN MODE - No changes will be made'))
        
        # Get canvases to work with
        if options['canvas_id']:
            try:
                canvases = [PixelCanvas.objects.get(id=options['canvas_id'])]
            except PixelCanvas.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'❌ PixelCanvas with ID {options["canvas_id"]} not found')
                )
                return
        else:
            canvases = PixelCanvas.objects.all()
            
        if not canvases:
            self.stdout.write(self.style.WARNING('⚠️  No canvases found'))
            return
        
        for canvas in canvases:
            self.stdout.write(f'\n🎯 Processing Canvas: "{canvas.name}" (ID: {canvas.id})')
            
            # Build query filter
            base_filter = {'canvas': canvas}
            
            if options['user']:
                try:
                    user = User.objects.get(username=options['user'])
                    base_filter['user'] = user
                    target_description = f'user "{user.username}"'
                except User.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(f'❌ User "{options["user"]}" not found')
                    )
                    continue
                    
            elif options['session']:
                base_filter['user__isnull'] = True
                base_filter['session_key'] = options['session']
                target_description = f'session "{options["session"]}"'
                
            elif options['all_users']:
                target_description = 'all users'
            else:
                self.stdout.write('❌ Must specify --user, --session, or --all-users')
                return
            
            # Get cooldown records
            cooldowns_query = UserPixelCooldown.objects.filter(**base_filter)
            
            if options['expired_only']:
                # Only reset cooldowns that have expired
                now = timezone.now()
                minute_ago = now - timedelta(minutes=1)
                cooldowns_query = cooldowns_query.filter(
                    last_minute_reset__lt=minute_ago
                )
                target_description += ' (expired only)'
            
            cooldowns = list(cooldowns_query)
            
            if not cooldowns:
                self.stdout.write(f'   📭 No cooldown records found for {target_description}')
                continue
                
            self.stdout.write(f'   🔍 Found {len(cooldowns)} cooldown record(s) for {target_description}')
            
            # Show what would be reset
            now = timezone.now()
            reset_count = 0
            
            for cooldown in cooldowns:
                user_id = cooldown.user.username if cooldown.user else f'session:{cooldown.session_key[:8]}'
                time_since_reset = now - cooldown.last_minute_reset
                
                if options['expired_only'] and time_since_reset.total_seconds() < 60:
                    continue  # Skip non-expired
                    
                reset_count += 1
                
                if options['dry_run']:
                    self.stdout.write(
                        f'   🔄 Would reset: {user_id} '
                        f'(pixels: {cooldown.pixels_placed_last_minute}, '
                        f'last reset: {time_since_reset.total_seconds():.0f}s ago)'
                    )
                else:
                    # Actually reset the cooldown
                    cooldown.pixels_placed_last_minute = 0
                    cooldown.last_minute_reset = now
                    cooldown.save()
                    
                    self.stdout.write(
                        f'   ✅ Reset: {user_id} '
                        f'(was {cooldown.pixels_placed_last_minute} pixels)'
                    )
            
            if reset_count == 0:
                self.stdout.write('   ℹ️  No records needed resetting')
            elif options['dry_run']:
                self.stdout.write(
                    self.style.WARNING(f'   🧪 Would reset {reset_count} record(s)')
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f'   ✅ Successfully reset {reset_count} record(s)')
                )
        
        # Summary and safety reminders
        self.stdout.write('\n' + '=' * 60)
        
        if options['dry_run']:
            self.stdout.write('🧪 DRY RUN completed - no changes were made')
            self.stdout.write('Remove --dry-run to actually perform the reset')
        else:
            self.stdout.write('✅ Rate limit reset completed')
            
        self.stdout.write('\n💡 Usage Examples:')
        self.stdout.write('   # Reset specific user (safe)')
        self.stdout.write('   python manage.py reset_rate_limits --user john_doe')
        self.stdout.write('')
        self.stdout.write('   # Reset expired cooldowns only (safe)')
        self.stdout.write('   python manage.py reset_rate_limits --all-users --expired-only')
        self.stdout.write('')
        self.stdout.write('   # Preview changes (safe)')
        self.stdout.write('   python manage.py reset_rate_limits --all-users --dry-run')
        self.stdout.write('')
        self.stdout.write('   # Reset all users (DANGER - testing only)')
        self.stdout.write('   python manage.py reset_rate_limits --all-users')
        
        if not options['dry_run'] and reset_count > 0:
            self.stdout.write(
                self.style.WARNING('\n⚠️  Rate limits have been reset. Users can now place pixels immediately.')
            )