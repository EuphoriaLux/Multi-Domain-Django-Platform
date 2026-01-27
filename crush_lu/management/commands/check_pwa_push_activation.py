"""
Management command to check PWA users and their push notification activation status.

Usage:
    python manage.py check_pwa_push_activation
    python manage.py check_pwa_push_activation --show-users
    python manage.py check_pwa_push_activation --platform android
    python manage.py check_pwa_push_activation --platform ios
"""

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef, Count, Q
from crush_lu.models import UserActivity, PushSubscription, PWADeviceInstallation


class Command(BaseCommand):
    help = 'Check PWA users and their push notification activation status'

    def add_arguments(self, parser):
        parser.add_argument(
            '--show-users',
            action='store_true',
            help='Show individual users (not just statistics)',
        )
        parser.add_argument(
            '--platform',
            type=str,
            choices=['android', 'ios', 'desktop'],
            help='Filter by platform (android, ios, desktop)',
        )
        parser.add_argument(
            '--inactive-days',
            type=int,
            default=30,
            help='Number of days to consider user inactive (default: 30)',
        )

    def handle(self, *args, **options):
        show_users = options['show_users']
        platform = options['platform']
        inactive_days = options['inactive_days']

        self.stdout.write(self.style.SUCCESS('=== PWA Push Notification Activation Report ===\n'))

        # Filter by platform if specified
        pwa_filter = Q(is_pwa_user=True)
        if platform:
            pwa_filter &= Q(device_category=platform)

        # Get total PWA users
        total_pwa_users = UserActivity.objects.filter(pwa_filter).count()

        if total_pwa_users == 0:
            self.stdout.write(self.style.WARNING('No PWA users found.'))
            return

        # Get PWA users with push enabled
        pwa_users_with_push = UserActivity.objects.filter(pwa_filter).annotate(
            has_push=Exists(
                PushSubscription.objects.filter(
                    user=OuterRef('user'),
                    enabled=True
                )
            )
        )

        users_with_push = pwa_users_with_push.filter(has_push=True).count()
        users_without_push = total_pwa_users - users_with_push

        # Calculate activation rate
        activation_rate = (users_with_push / total_pwa_users * 100) if total_pwa_users > 0 else 0

        # Display summary
        platform_label = platform.upper() if platform else "ALL PLATFORMS"
        self.stdout.write(f'Platform: {platform_label}\n')
        self.stdout.write(f'Total PWA users: {total_pwa_users}')
        self.stdout.write(self.style.SUCCESS(f'  ✅ With push enabled: {users_with_push} ({activation_rate:.1f}%)'))
        self.stdout.write(self.style.WARNING(f'  ❌ Without push: {users_without_push} ({100-activation_rate:.1f}%)\n'))

        # Breakdown by platform
        if not platform:
            self.stdout.write('Breakdown by platform:')
            for category in ['android', 'ios', 'desktop']:
                platform_users = UserActivity.objects.filter(
                    is_pwa_user=True,
                    device_category=category
                ).annotate(
                    has_push=Exists(
                        PushSubscription.objects.filter(
                            user=OuterRef('user'),
                            enabled=True
                        )
                    )
                )
                total = platform_users.count()
                with_push = platform_users.filter(has_push=True).count()
                if total > 0:
                    rate = (with_push / total * 100)
                    self.stdout.write(f'  {category.upper()}: {with_push}/{total} ({rate:.1f}%)')

        # Count total push subscriptions (can be multiple per user)
        total_subscriptions = PushSubscription.objects.filter(
            enabled=True,
            user__useractivity__is_pwa_user=True
        ).count()
        self.stdout.write(f'\nTotal active push subscriptions: {total_subscriptions}')
        if users_with_push > 0:
            avg_per_user = total_subscriptions / users_with_push
            self.stdout.write(f'  (Average {avg_per_user:.1f} subscriptions per user)\n')

        # Show users without push (to encourage them)
        if show_users:
            self.stdout.write('\n' + '='*60)
            self.stdout.write('PWA users WITHOUT push notifications:')
            self.stdout.write('='*60 + '\n')

            users_no_push = pwa_users_with_push.filter(has_push=False).select_related('user')

            if users_no_push.exists():
                for activity in users_no_push:
                    user = activity.user
                    device = activity.device_category or 'unknown'
                    last_visit = activity.last_pwa_visit.strftime('%Y-%m-%d') if activity.last_pwa_visit else 'Never'

                    # Check if user is still active
                    if activity.last_pwa_visit:
                        days_since = (timezone.now() - activity.last_pwa_visit).days
                        status = '🟢 Active' if days_since < inactive_days else f'🔴 Inactive ({days_since}d)'
                    else:
                        status = '⚪ Never used'

                    self.stdout.write(
                        f'  {user.username:20} | {device:10} | Last: {last_visit:12} | {status}'
                    )

                self.stdout.write(f'\n💡 Tip: Encourage these {users_no_push.count()} users to enable push notifications!')
            else:
                self.stdout.write(self.style.SUCCESS('  All PWA users have push notifications enabled! 🎉'))

        # Recommendations
        self.stdout.write('\n' + '='*60)
        self.stdout.write('Recommendations:')
        self.stdout.write('='*60)

        if activation_rate < 30:
            self.stdout.write(self.style.ERROR(
                f'⚠️  LOW activation rate ({activation_rate:.1f}%). Consider:'
            ))
            self.stdout.write('   - Add in-app prompts to encourage push activation')
            self.stdout.write('   - Show benefits of push notifications during onboarding')
            self.stdout.write('   - Send email to PWA users without push')
        elif activation_rate < 60:
            self.stdout.write(self.style.WARNING(
                f'⚡ MODERATE activation rate ({activation_rate:.1f}%). Room for improvement:'
            ))
            self.stdout.write('   - A/B test different push permission prompts')
            self.stdout.write('   - Show value proposition before asking for permission')
        else:
            self.stdout.write(self.style.SUCCESS(
                f'✅ GOOD activation rate ({activation_rate:.1f}%)! Keep it up!'
            ))

        self.stdout.write('')


# Import at module level for timezone
from django.utils import timezone
