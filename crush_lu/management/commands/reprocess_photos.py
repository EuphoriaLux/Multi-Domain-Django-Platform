"""
Management command to reprocess existing profile photos.

Strips EXIF metadata (including GPS), fixes orientation, and resizes
to max 1200px. Safe to re-run (idempotent).
"""

import logging

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from crush_lu.models import CrushProfile, CrushCoach
from crush_lu.utils.image_processing import process_uploaded_image

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reprocess existing profile photos: strip EXIF, fix orientation, resize to max 1200px"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be processed without making changes",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Number of profiles to process per batch (default: 100)",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="Process photos for a specific user ID only",
        )
        parser.add_argument(
            "--include-coaches",
            action="store_true",
            default=True,
            help="Also process CrushCoach photos (default: True)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]
        user_id = options["user_id"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        # Process CrushProfile photos
        stats = {"processed": 0, "skipped": 0, "errors": 0}

        profiles = CrushProfile.objects.exclude(photo_1="", photo_2="", photo_3="")
        if user_id:
            profiles = profiles.filter(user_id=user_id)

        total = profiles.count()
        self.stdout.write(f"Found {total} profiles with photos to process")

        for i, profile in enumerate(profiles.iterator(chunk_size=batch_size)):
            if i > 0 and i % batch_size == 0:
                self.stdout.write(f"  Progress: {i}/{total}")

            for field_name in ("photo_1", "photo_2", "photo_3"):
                field = getattr(profile, field_name)
                if not field:
                    continue

                self._process_photo(profile, field_name, field, dry_run, stats)

        # Process CrushCoach photos
        if options["include_coaches"]:
            coaches = CrushCoach.objects.exclude(photo="")
            if user_id:
                coaches = coaches.filter(user_id=user_id)

            coach_count = coaches.count()
            if coach_count:
                self.stdout.write(f"\nFound {coach_count} coach photos to process")

            for coach in coaches.iterator():
                self._process_photo(coach, "photo", coach.photo, dry_run, stats)

        # Summary
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done! Processed: {stats['processed']}, "
                f"Skipped: {stats['skipped']}, "
                f"Errors: {stats['errors']}"
            )
        )

    def _process_photo(self, obj, field_name, field, dry_run, stats):
        """Process a single photo field on a model instance."""
        obj_label = self._get_label(obj, field_name)

        try:
            # Read the current file
            field.open("rb")
            original_data = field.read()
            field.close()
            original_size = len(original_data)

            if dry_run:
                self.stdout.write(
                    f"  [DRY RUN] Would process {obj_label} "
                    f"({original_size:,} bytes)"
                )
                stats["processed"] += 1
                return

            # Process the image
            raw_file = ContentFile(original_data, name=field.name)
            processed = process_uploaded_image(raw_file, field.name)
            new_size = processed.size

            # Skip if already optimized (less than 5% size reduction)
            size_change = new_size - original_size
            pct = (size_change / original_size * 100) if original_size else 0
            if abs(pct) < 5:
                self.stdout.write(
                    f"  Skipped {obj_label}: already optimized "
                    f"({original_size:,} bytes, {pct:+.1f}%)"
                )
                stats["skipped"] += 1
                return

            # Delete the old blob first, then save the new one.
            # The storage backend generates unique names (UUID) on save,
            # so without deleting first we'd leave orphan blobs.
            old_blob_name = field.name
            storage = field.storage
            field.save(old_blob_name, processed, save=False)

            # Delete old blob if the path changed (storage generated a new name)
            if field.name != old_blob_name:
                try:
                    storage.delete(old_blob_name)
                except Exception:
                    logger.warning("Could not delete old blob: %s", old_blob_name)

            # Save the model to persist the field reference. If this is a verified
            # primary photo, carry forward the attestation to the new key.
            # CrushProfile.save validates that the old key was still verified
            # in the database, preventing resurrection of concurrent revocations.
            update_fields = [field_name]
            carries_attestation = (
                isinstance(obj, CrushProfile)
                and field_name == "photo_1"
                and old_blob_name
                and obj.photo_verification_key == old_blob_name
                and obj.photo_verified_at is not None
            )
            if carries_attestation:
                obj.photo_verification_key = field.name
                update_fields.extend(["photo_verification_key", "photo_verified_at"])
                # Only the carry-forward needs the save and the provenance
                # repoint to land together; a plain reprocess (the overwhelming
                # majority of a backfill) must not pay a BEGIN/COMMIT per photo.
                with transaction.atomic():
                    obj.save(update_fields=update_fields)
                    self._carry_forward_registration_attestation(
                        obj, old_blob_name, field.name
                    )
            else:
                obj.save(update_fields=update_fields)

            self.stdout.write(
                f"  Processed {obj_label}: "
                f"{original_size:,} -> {new_size:,} bytes ({pct:+.1f}%)"
            )
            stats["processed"] += 1

        except (FileNotFoundError, Exception) as e:
            # Catch Azure ResourceNotFoundError (blob missing from storage)
            err_str = str(e)
            if (
                "BlobNotFound" in err_str
                or "does not exist" in err_str
                or isinstance(e, FileNotFoundError)
            ):
                self.stdout.write(
                    self.style.WARNING(
                        f"  Skipped {obj_label}: file not found in storage"
                    )
                )
                stats["skipped"] += 1
            else:
                self.stdout.write(
                    self.style.ERROR(f"  Error processing {obj_label}: {e}")
                )
                logger.exception("Error reprocessing %s", obj_label)
                stats["errors"] += 1

    def _carry_forward_registration_attestation(self, profile, old_key, new_key):
        """Repoint door check-in provenance at the reprocessed photo.

        Call *after* ``profile.save()``. ``CrushProfile.save`` is the authority
        on whether the attestation survived: it re-reads the row and clears the
        attestation when a revocation (a door rejection, a check-in undo) landed
        while this command was reprocessing the image. Reading that decision
        back out of the database — rather than trusting the in-memory copy this
        command wrote just before saving — is what stops a revoked attestation
        being resurrected on the registration under a key nobody ever verified,
        where `coach_undo_checkin` would later read it as coach attendance.
        """
        from crush_lu.models import EventRegistration

        carried_forward = CrushProfile.objects.filter(
            pk=profile.pk,
            photo_verification_key=new_key,
        ).exists()
        if not carried_forward:
            return

        EventRegistration.objects.filter(
            user_id=profile.user_id,
            checkin_attested_photo_key=old_key,
        ).update(checkin_attested_photo_key=new_key)

    def _get_label(self, obj, field_name):
        """Get a human-readable label for logging."""
        if isinstance(obj, CrushProfile):
            return f"CrushProfile(user={obj.user_id}).{field_name}"
        elif isinstance(obj, CrushCoach):
            return f"CrushCoach(user={obj.user_id}).{field_name}"
        return f"{obj.__class__.__name__}(pk={obj.pk}).{field_name}"
