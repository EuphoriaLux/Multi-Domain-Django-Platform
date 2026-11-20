from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from power_up.pixel_war.models import PixelCanvas, UserPixelCooldown


class Command(BaseCommand):
    help = "Reset rate limiting data for testing and troubleshooting"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            help="Reset rate limits for specific user by username",
        )
        parser.add_argument(
            "--session",
            type=str,
            help="Reset rate limits for specific session key",
        )
        parser.add_argument(
            "--all-users",
            action="store_true",
            help="Reset rate limits for all users (DANGER: Use only for testing)",
        )
        parser.add_argument(
            "--expired-only",
            action="store_true",
            help="Only reset cooldowns that have expired (safe option)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be reset without actually doing it",
        )
        parser.add_argument(
            "--canvas-id",
            type=int,
            help="Target specific canvas by ID (default: all canvases)",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("🔄 Rate Limiting Reset Tool"))
        self.stdout.write("=" * 60)

        if options["dry_run"]:
            self.stdout.write(
                self.style.WARNING("🧪 DRY RUN MODE - No changes will be made")
            )

        if options["canvas_id"]:
            try:
                canvases = [PixelCanvas.objects.get(id=options["canvas_id"])]
            except PixelCanvas.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(
                        f'❌ PixelCanvas with ID {options["canvas_id"]} not found'
                    )
                )
                return
        else:
            canvases = list(PixelCanvas.objects.all())

        if not canvases:
            self.stdout.write(self.style.WARNING("⚠️  No canvases found"))
            return

        reset_count = 0

        for canvas in canvases:
            self.stdout.write(
                f'\n🎯 Processing Canvas: "{canvas.name}" (ID: {canvas.id})'
            )

            base_filter = {"canvas": canvas}

            if options["user"]:
                try:
                    user = User.objects.get(username=options["user"])
                    base_filter["user"] = user
                    target_description = f'user "{user.username}"'
                except User.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(f'❌ User "{options["user"]}" not found')
                    )
                    continue
            elif options["session"]:
                base_filter["user__isnull"] = True
                base_filter["session_key"] = options["session"]
                target_description = f'session "{options["session"]}"'
            elif options["all_users"]:
                target_description = "all users"
            else:
                self.stdout.write("❌ Must specify --user, --session, or --all-users")
                return

            cooldowns_query = UserPixelCooldown.objects.filter(**base_filter)
            now = timezone.now()

            if options["expired_only"]:
                minute_ago = now - timedelta(minutes=1)
                cooldowns_query = cooldowns_query.filter(
                    last_minute_reset__lt=minute_ago
                )
                target_description += " (expired only)"

            cooldowns = list(cooldowns_query.select_related("user"))

            if not cooldowns:
                self.stdout.write(
                    f"   📭 No cooldown records found for {target_description}"
                )
                continue

            self.stdout.write(
                f"   🔍 Found {len(cooldowns)} cooldown record(s) for {target_description}"
            )

            canvas_reset_count = 0
            for cooldown in cooldowns:
                user_id = (
                    cooldown.user.username
                    if cooldown.user
                    else f"session:{(cooldown.session_key or 'unknown')[:8]}"
                )
                if cooldown.last_minute_reset is None:
                    seconds_ago = None
                else:
                    seconds_ago = (now - cooldown.last_minute_reset).total_seconds()

                if (
                    options["expired_only"]
                    and seconds_ago is not None
                    and seconds_ago < 60
                ):
                    continue

                canvas_reset_count += 1
                reset_count += 1
                previous_count = cooldown.pixels_placed_last_minute

                if options["dry_run"]:
                    ago_label = (
                        f"{seconds_ago:.0f}s ago"
                        if seconds_ago is not None
                        else "never"
                    )
                    self.stdout.write(
                        f"   🔄 Would reset: {user_id} "
                        f"(pixels: {previous_count}, last reset: {ago_label})"
                    )
                else:
                    cooldown.pixels_placed_last_minute = 0
                    cooldown.last_minute_reset = now
                    cooldown.save(
                        update_fields=["pixels_placed_last_minute", "last_minute_reset"]
                    )
                    self.stdout.write(
                        f"   ✅ Reset: {user_id} (was {previous_count} pixels)"
                    )

            if canvas_reset_count == 0:
                self.stdout.write("   ℹ️  No records needed resetting")
            elif options["dry_run"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"   🧪 Would reset {canvas_reset_count} record(s)"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"   ✅ Successfully reset {canvas_reset_count} record(s)"
                    )
                )

        self.stdout.write("\n" + "=" * 60)
        if options["dry_run"]:
            self.stdout.write("🧪 DRY RUN completed - no changes were made")
        else:
            self.stdout.write("✅ Rate limit reset completed")
            if reset_count > 0:
                self.stdout.write(
                    self.style.WARNING(
                        "\n⚠️  Rate limits have been reset. Users can now place pixels immediately."
                    )
                )
