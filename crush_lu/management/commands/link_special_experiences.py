"""
Link legacy name-matched SpecialUserExperience rows to their user account.

Special journeys used to be granted to whoever logged in with the same first
and last name as the experience, so a namesake could open someone else's
private journey. Access now requires ``linked_user``; this command is the
one-time migration path for the legacy experiences that were never linked.

Usage:
    # Preview: print what would be linked, change nothing
    python manage.py link_special_experiences --dry-run

    # Link every experience with exactly one matching active user
    python manage.py link_special_experiences

Rules:
    - Only experiences with ``linked_user`` unset and a non-empty first AND
      last name are considered (empty names are reported as SKIPPED).
    - Candidates are ACTIVE users whose first and last name match the
      experience case-insensitively, ignoring surrounding whitespace.
    - Exactly one candidate -> LINKED. Two or more -> AMBIGUOUS (never linked;
      link it by hand in the admin). None -> UNMATCHED.
    - A user can only be linked to one experience (``unique_linked_user``), so
      a candidate already linked to another experience is SKIPPED.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.functions import Trim

from crush_lu.models import SpecialUserExperience


class Command(BaseCommand):
    help = (
        "One-time migration path for legacy SpecialUserExperience rows that "
        "relied on first+last name matching: links each unlinked experience "
        "to the single active user with the same first and last name "
        "(case-insensitive, whitespace-trimmed). Ambiguous (2+ users) and "
        "unmatched experiences are reported and never linked. Use --dry-run "
        "first."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be linked without changing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write("DRY RUN - no changes will be saved.")

        counts = {"linked": 0, "ambiguous": 0, "unmatched": 0, "skipped": 0}
        with transaction.atomic():
            self._link_all(dry_run, counts)

        verb = "would link" if dry_run else "linked"
        self.stdout.write(
            f"Summary: {verb}={counts['linked']} ambiguous={counts['ambiguous']}"
            f" unmatched={counts['unmatched']} skipped={counts['skipped']}"
        )
        if dry_run:
            self.stdout.write("Dry run: nothing was changed.")
        if counts["ambiguous"]:
            self.stdout.write(
                self.style.WARNING(
                    "Link the AMBIGUOUS experiences by hand in the admin "
                    "(Special User Experience -> Linked user)."
                )
            )

    def _link_all(self, dry_run, counts):
        User = get_user_model()
        # Users claimed during this run: linked_user is unique per user.
        claimed = {}

        experiences = SpecialUserExperience.objects.filter(
            linked_user__isnull=True
        ).order_by("pk")

        for exp in experiences:
            first = (exp.first_name or "").strip()
            last = (exp.last_name or "").strip()

            if not first or not last:
                counts["skipped"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"SKIPPED   experience #{exp.pk}: empty first or last name"
                        f" (first={exp.first_name!r}, last={exp.last_name!r})"
                    )
                )
                continue

            label = f"experience #{exp.pk} ({first} {last})"

            candidates = list(
                User.objects.filter(is_active=True)
                .annotate(_first=Trim("first_name"), _last=Trim("last_name"))
                .filter(_first__iexact=first, _last__iexact=last)
                .order_by("pk")
            )

            if not candidates:
                counts["unmatched"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"UNMATCHED {label}: no active user with this name"
                    )
                )
                continue

            if len(candidates) > 1:
                counts["ambiguous"] += 1
                listed = ", ".join(_describe(user) for user in candidates)
                self.stdout.write(
                    self.style.WARNING(
                        f"AMBIGUOUS {label}: {len(candidates)} active users"
                        f" match: {listed} - not linked"
                    )
                )
                continue

            user = candidates[0]
            other_pk = claimed.get(user.pk)
            if other_pk is None:
                other_pk = (
                    SpecialUserExperience.objects.filter(linked_user=user)
                    .exclude(pk=exp.pk)
                    .values_list("pk", flat=True)
                    .first()
                )
            if other_pk is not None:
                counts["skipped"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"SKIPPED   {label}: user {_describe(user)} is already"
                        f" linked to experience #{other_pk}"
                    )
                )
                continue

            claimed[user.pk] = exp.pk
            counts["linked"] += 1
            if dry_run:
                self.stdout.write(f"WOULD LINK {label} -> user {_describe(user)}")
                continue

            exp.linked_user = user
            exp.save(update_fields=["linked_user", "updated_at"])
            self.stdout.write(
                self.style.SUCCESS(f"LINKED    {label} -> user {_describe(user)}")
            )


def _describe(user):
    return f"#{user.pk} <{user.email or user.username}>"
