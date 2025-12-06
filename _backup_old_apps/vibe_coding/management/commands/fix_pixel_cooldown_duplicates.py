from django.core.management.base import BaseCommand
from django.db.models import Count, Min
from vibe_coding.models import UserPixelCooldown


class Command(BaseCommand):
    help = 'Fix duplicate UserPixelCooldown records'

    def handle(self, *args, **options):
        self.stdout.write('Checking for duplicate UserPixelCooldown records...')
        
        # Find duplicates for authenticated users
        auth_duplicates = UserPixelCooldown.objects.filter(
            user__isnull=False
        ).values('user', 'canvas').annotate(
            count=Count('id'),
            min_id=Min('id')
        ).filter(count__gt=1)
        
        auth_count = 0
        for dup in auth_duplicates:
            # Keep the oldest record, delete others
            UserPixelCooldown.objects.filter(
                user_id=dup['user'],
                canvas_id=dup['canvas']
            ).exclude(id=dup['min_id']).delete()
            auth_count += dup['count'] - 1
        
        # Find duplicates for anonymous users
        anon_duplicates = UserPixelCooldown.objects.filter(
            user__isnull=True
        ).exclude(session_key__isnull=True).values(
            'session_key', 'canvas'
        ).annotate(
            count=Count('id'),
            min_id=Min('id')
        ).filter(count__gt=1)
        
        anon_count = 0
        for dup in anon_duplicates:
            # Keep the oldest record, delete others
            UserPixelCooldown.objects.filter(
                user__isnull=True,
                session_key=dup['session_key'],
                canvas_id=dup['canvas']
            ).exclude(id=dup['min_id']).delete()
            anon_count += dup['count'] - 1
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully removed {auth_count} duplicate authenticated user records '
                f'and {anon_count} duplicate anonymous user records'
            )
        )