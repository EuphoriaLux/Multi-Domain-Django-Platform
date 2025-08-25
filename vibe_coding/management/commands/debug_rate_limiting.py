from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth.models import User
from vibe_coding.models import PixelCanvas, UserPixelCooldown
import json
from datetime import timedelta


class Command(BaseCommand):
    help = 'Debug rate limiting behavior and show detailed diagnostics'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user',
            type=str,
            help='Check specific user by username',
        )
        parser.add_argument(
            '--session',
            type=str,
            help='Check specific session key (first 8 characters)',
        )
        parser.add_argument(
            '--clear-cooldowns',
            action='store_true',
            help='Clear all cooldown records (DANGER: Use only for testing)',
        )
        parser.add_argument(
            '--simulate',
            action='store_true',
            help='Simulate rate limiting logic for testing',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🔬 Rate Limiting Debug Analysis'))
        self.stdout.write('=' * 60)
        
        # Get canvas
        try:
            canvas = PixelCanvas.objects.first()
            if not canvas:
                self.stdout.write(self.style.ERROR('❌ No PixelCanvas found in database'))
                return
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error accessing PixelCanvas: {e}'))
            return
            
        self.stdout.write(f'🎯 Canvas: "{canvas.name}" (ID: {canvas.id})')
        
        # Show current time and timezone
        now = timezone.now()
        self.stdout.write(f'🕐 Current Time: {now} ({timezone.get_current_timezone()})')
        
        # Canvas configuration
        self.stdout.write(f'\n📊 Canvas Rate Limiting Settings:')
        self.stdout.write(f'   • Anonymous: {canvas.anonymous_pixels_per_minute} pixels/min, {canvas.anonymous_cooldown_seconds}s cooldown')
        self.stdout.write(f'   • Registered: {canvas.registered_pixels_per_minute} pixels/min, {canvas.registered_cooldown_seconds}s cooldown')
        
        # Clear cooldowns if requested (DANGER!)
        if options['clear_cooldowns']:
            count = UserPixelCooldown.objects.filter(canvas=canvas).count()
            UserPixelCooldown.objects.filter(canvas=canvas).delete()
            self.stdout.write(
                self.style.WARNING(f'⚠️  CLEARED {count} cooldown records for testing!')
            )
        
        # Specific user analysis
        if options['user']:
            try:
                user = User.objects.get(username=options['user'])
                self.analyze_user_cooldown(canvas, user, None)
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'❌ User "{options["user"]}" not found')
                )
                return
        
        # Specific session analysis
        elif options['session']:
            session_prefix = options['session']
            cooldowns = UserPixelCooldown.objects.filter(
                canvas=canvas,
                user__isnull=True,
                session_key__startswith=session_prefix
            )
            
            if cooldowns.exists():
                cooldown = cooldowns.first()
                self.analyze_user_cooldown(canvas, None, cooldown.session_key)
            else:
                self.stdout.write(
                    self.style.ERROR(f'❌ No session found starting with "{session_prefix}"')
                )
                return
        
        # General analysis
        else:
            self.general_analysis(canvas)
            
        # Simulation mode
        if options['simulate']:
            self.simulate_rate_limiting(canvas)

    def analyze_user_cooldown(self, canvas, user, session_key):
        """Analyze cooldown for specific user or session"""
        self.stdout.write(f'\n🔍 Detailed Analysis:')
        
        if user:
            self.stdout.write(f'   👤 User: {user.username} (ID: {user.id})')
            cooldown_seconds = canvas.registered_cooldown_seconds
            max_pixels_per_minute = canvas.registered_pixels_per_minute
            cooldown_filter = {'user': user, 'canvas': canvas}
        else:
            self.stdout.write(f'   🔐 Session: {session_key}')
            cooldown_seconds = canvas.anonymous_cooldown_seconds
            max_pixels_per_minute = canvas.anonymous_pixels_per_minute
            cooldown_filter = {'user': None, 'canvas': canvas, 'session_key': session_key}
        
        try:
            cooldown = UserPixelCooldown.objects.get(**cooldown_filter)
            
            # Calculate time differences
            now = timezone.now()
            time_since_last_placed = now - cooldown.last_placed if cooldown.last_placed else None
            time_since_minute_reset = now - cooldown.last_minute_reset
            
            self.stdout.write(f'\n📈 Cooldown Record:')
            self.stdout.write(f'   • Last Placed: {cooldown.last_placed}')
            self.stdout.write(f'   • Last Reset: {cooldown.last_minute_reset}')
            self.stdout.write(f'   • Pixels This Minute: {cooldown.pixels_placed_last_minute}/{max_pixels_per_minute}')
            
            self.stdout.write(f'\n⏱️  Time Analysis:')
            if time_since_last_placed:
                self.stdout.write(f'   • Since Last Placed: {time_since_last_placed.total_seconds():.1f}s')
            self.stdout.write(f'   • Since Minute Reset: {time_since_minute_reset.total_seconds():.1f}s')
            
            # Determine current status
            needs_minute_reset = time_since_minute_reset.total_seconds() >= 60
            at_pixel_limit = cooldown.pixels_placed_last_minute >= max_pixels_per_minute
            
            self.stdout.write(f'\n🚦 Current Status:')
            if needs_minute_reset:
                self.stdout.write('   ✅ Minute counter needs reset - can place pixels')
                effective_pixels_remaining = max_pixels_per_minute
            elif at_pixel_limit:
                time_until_reset = 60 - time_since_minute_reset.total_seconds()
                self.stdout.write(f'   🚫 At pixel limit - wait {time_until_reset:.1f}s')
                effective_pixels_remaining = 0
            else:
                pixels_remaining = max_pixels_per_minute - cooldown.pixels_placed_last_minute
                self.stdout.write(f'   ✅ Can place {pixels_remaining} more pixels')
                effective_pixels_remaining = pixels_remaining
                
            self.stdout.write(f'   • Effective Pixels Remaining: {effective_pixels_remaining}')
            
        except UserPixelCooldown.DoesNotExist:
            self.stdout.write('   📭 No cooldown record found - can place pixels')

    def general_analysis(self, canvas):
        """General analysis of all cooldown records"""
        self.stdout.write(f'\n📊 General Statistics:')
        
        total_cooldowns = UserPixelCooldown.objects.filter(canvas=canvas).count()
        auth_cooldowns = UserPixelCooldown.objects.filter(canvas=canvas, user__isnull=False).count()
        anon_cooldowns = UserPixelCooldown.objects.filter(canvas=canvas, user__isnull=True).count()
        
        self.stdout.write(f'   • Total Cooldown Records: {total_cooldowns}')
        self.stdout.write(f'   • Authenticated Users: {auth_cooldowns}')
        self.stdout.write(f'   • Anonymous Users: {anon_cooldowns}')
        
        # Recent activity analysis
        now = timezone.now()
        recent_cutoff = now - timedelta(minutes=5)
        
        recent_cooldowns = UserPixelCooldown.objects.filter(
            canvas=canvas,
            last_placed__gte=recent_cutoff
        ).order_by('-last_placed')
        
        if recent_cooldowns.exists():
            self.stdout.write(f'\n🕒 Recent Activity (last 5 minutes):')
            for i, cooldown in enumerate(recent_cooldowns[:10]):  # Show top 10
                if cooldown.user:
                    identifier = f'User: {cooldown.user.username}'
                else:
                    identifier = f'Session: {cooldown.session_key[:8]}...'
                    
                time_ago = (now - cooldown.last_placed).total_seconds()
                self.stdout.write(
                    f'   {i+1:2d}. {identifier} - {cooldown.pixels_placed_last_minute} pixels ({time_ago:.0f}s ago)'
                )
        else:
            self.stdout.write('   📭 No recent activity in the last 5 minutes')

    def simulate_rate_limiting(self, canvas):
        """Simulate the rate limiting logic"""
        self.stdout.write(f'\n🧪 Rate Limiting Simulation:')
        self.stdout.write('Simulating anonymous user placing 3 pixels quickly...\n')
        
        # Create test session
        test_session = 'test_debug_session_123'
        now = timezone.now()
        
        # Clean up any existing test records
        UserPixelCooldown.objects.filter(
            canvas=canvas, 
            session_key=test_session
        ).delete()
        
        for attempt in range(1, 4):
            self.stdout.write(f'   Attempt #{attempt}:')
            
            # Get or create cooldown record
            cooldown, created = UserPixelCooldown.objects.get_or_create(
                user=None,
                canvas=canvas,
                session_key=test_session,
                defaults={'pixels_placed_last_minute': 0}
            )
            
            # Check if minute counter needs reset
            time_since_minute_reset = now - cooldown.last_minute_reset
            if time_since_minute_reset.total_seconds() >= 60:
                cooldown.pixels_placed_last_minute = 0
                cooldown.last_minute_reset = now
                cooldown.save()
                self.stdout.write(f'     → Reset minute counter')
            
            # Check pixel limit
            max_pixels = canvas.anonymous_pixels_per_minute
            if cooldown.pixels_placed_last_minute >= max_pixels:
                time_until_reset = 60 - time_since_minute_reset.total_seconds()
                self.stdout.write(
                    f'     → 🚫 BLOCKED: {cooldown.pixels_placed_last_minute}/{max_pixels} pixels used, '
                    f'wait {time_until_reset:.1f}s'
                )
            else:
                # Allow placement
                cooldown.last_placed = now
                cooldown.pixels_placed_last_minute += 1
                cooldown.save()
                remaining = max_pixels - cooldown.pixels_placed_last_minute
                self.stdout.write(
                    f'     → ✅ ALLOWED: Pixel placed, {remaining} remaining this minute'
                )
        
        # Clean up test record
        UserPixelCooldown.objects.filter(
            canvas=canvas,
            session_key=test_session
        ).delete()
        self.stdout.write('     → Test session cleaned up')