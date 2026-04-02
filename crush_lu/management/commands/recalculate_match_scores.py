"""
Management command to recalculate match scores.

Usage:
    python manage.py recalculate_match_scores           # All approved users
    python manage.py recalculate_match_scores --user-id 42  # Single user
    python manage.py recalculate_match_scores --dry-run     # Preview only
"""

from django.core.management.base import BaseCommand

from crush_lu.matching import update_match_scores_for_user
from crush_lu.models import CrushProfile


class Command(BaseCommand):
    help = "Recalculate match scores for approved profiles"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            help="Recalculate scores for a single user ID only",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be calculated without writing to DB",
        )

    def handle(self, *args, **options):
        user_id = options.get("user_id")
        dry_run = options.get("dry_run", False)

        if user_id:
            profiles = CrushProfile.objects.filter(
                user_id=user_id, is_approved=True
            ).select_related("user")
        else:
            profiles = CrushProfile.objects.filter(
                is_approved=True, is_active=True
            ).select_related("user")

        total = profiles.count()
        self.stdout.write(f"Found {total} approved profile(s) to process")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no scores will be written"))
            for profile in profiles:
                self.stdout.write(f"  Would recalculate for: {profile.user} (pk={profile.user.pk})")
            return

        total_updated = 0
        for i, profile in enumerate(profiles, 1):
            count = update_match_scores_for_user(profile.user)
            total_updated += count
            self.stdout.write(
                f"  [{i}/{total}] {profile.user}: {count} score(s) updated"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {total_updated} total score(s) created/updated across {total} user(s)."
            )
        )
