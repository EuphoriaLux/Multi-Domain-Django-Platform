# crush_lu/management/commands/backfill_outlook_immutable_ids.py
"""
One-off migration of stored Outlook contact IDs to immutable IDs.

Outlook item IDs are not stable by default: they change when the item moves to
a different container, and when a mailbox is archived or exported and
re-imported. CrushProfile.outlook_contact_id is persisted for the lifetime of
the profile, so a changed ID means the next sync gets a 404, clears the ID and
creates a *duplicate* contact.

GraphContactsService now sends Prefer: IdType="ImmutableId" on create and read,
so new contacts are safe. This command converts the IDs written before that,
using the Graph translateExchangeIds function (1000 IDs per call).

Usage:
    # ALWAYS start here
    python manage.py backfill_outlook_immutable_ids --dry-run

    # Apply
    python manage.py backfill_outlook_immutable_ids

Re-running is harmless: IDs that are already immutable fail translation
per-item, are logged, and are left untouched.
"""

from django.core.management.base import BaseCommand, CommandError

from crush_lu.models import CrushProfile
from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled

# Graph accepts up to 1000 ids per translateExchangeIds call.
BATCH_SIZE = 1000


class Command(BaseCommand):
    help = "Convert stored Outlook contact IDs to immutable IDs (one-off migration)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be converted without writing to the database",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if OUTLOOK_CONTACT_SYNC_ENABLED is not set",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]

        if not is_sync_enabled() and not force:
            raise CommandError(
                "Outlook contact sync is disabled for this environment. "
                "Set OUTLOOK_CONTACT_SYNC_ENABLED=true, or pass --force."
            )

        try:
            service = GraphContactsService()
        except Exception as e:
            raise CommandError(f"Failed to initialize GraphContactsService: {e}")

        profiles = list(
            CrushProfile.objects.exclude(outlook_contact_id="")
            .exclude(outlook_contact_id__isnull=True)
            .values_list("pk", "outlook_contact_id")
        )

        self.stdout.write(
            f"\n{'[DRY RUN] ' if dry_run else ''}"
            f"Converting Outlook contact IDs to immutable format\n"
            f"  Mailbox:            {service.mailbox}\n"
            f"  Profiles with an ID: {len(profiles)}\n"
        )

        if not profiles:
            self.stdout.write(self.style.SUCCESS("Nothing to convert."))
            return

        converted = 0
        unchanged = 0
        failed = 0

        for start in range(0, len(profiles), BATCH_SIZE):
            batch = profiles[start : start + BATCH_SIZE]
            ids = [contact_id for _, contact_id in batch]

            self.stdout.write(
                f"  Translating {len(ids)} IDs "
                f"({start + 1}-{start + len(ids)} of {len(profiles)})..."
            )

            try:
                translated = service.translate_ids_to_immutable(ids)
            except Exception as e:
                raise CommandError(f"translateExchangeIds failed: {e}")

            for pk, old_id in batch:
                new_id = translated.get(old_id)

                if not new_id:
                    # Graph reported an error for this one -- most likely it is
                    # already immutable. Left alone deliberately.
                    failed += 1
                    continue

                if new_id == old_id:
                    unchanged += 1
                    continue

                converted += 1
                if not dry_run:
                    # update() rather than save() so this cannot re-enter the
                    # post_save Outlook sync signal.
                    CrushProfile.objects.filter(pk=pk).update(
                        outlook_contact_id=new_id
                    )

        verb = "Would convert" if dry_run else "Converted"
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'Dry run complete' if dry_run else 'Backfill complete'}!\n"
                f"  {verb}:                {converted}\n"
                f"  Already immutable:     {unchanged}\n"
                f"  Not translated:        {failed}\n"
            )
        )

        if failed:
            self.stdout.write(
                self.style.WARNING(
                    "  'Not translated' is expected when this command has "
                    "already run once; see the logged per-ID reasons.\n"
                )
            )
