"""
Link legacy name-matched SpecialUserExperience rows to their user account.

Special journeys used to be granted to whoever logged in with the same first
and last name as the experience, so a namesake could open someone else's
private journey. Access now requires ``linked_user``; this command is the
one-time migration path for the legacy experiences that were never linked.

Usage:
    # Preview: print what would be linked, change nothing. Review every
    # WOULD LINK line: a unique name match is not proof of identity.
    python manage.py link_special_experiences --dry-run

    # Link every experience with exactly one matching active user
    python manage.py link_special_experiences

    # Afterwards (read-only): list journey/advent progress and QR tokens held
    # by someone other than the experience's linked user
    python manage.py link_special_experiences --report-stale

Rules:
    - Only ACTIVE experiences with ``linked_user`` unset and a non-empty first
      AND last name are considered. Inactive and empty-name ones are SKIPPED:
      a gift or Crush Spark reuses (and reactivates) the user's linked
      experience, so linking a disabled one would bring it back.
    - Candidates are ACTIVE users whose first and last name match the
      experience case-insensitively, ignoring surrounding whitespace.
    - Exactly one candidate -> LINKED. Two or more -> AMBIGUOUS (never linked;
      link it by hand in the admin). None -> UNMATCHED.
    - A user can only be linked to one experience (``unique_linked_user``), so
      a candidate already linked to another experience is SKIPPED, and the
      skipped experience's journeys are listed with how to move them.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.functions import Trim

from crush_lu.models import (
    AdventProgress,
    JourneyConfiguration,
    JourneyProgress,
    QRCodeToken,
    SpecialUserExperience,
)


class Command(BaseCommand):
    help = (
        "One-time migration path for legacy SpecialUserExperience rows that "
        "relied on first+last name matching: links each active, unlinked "
        "experience to the single active user with the same first and last "
        "name (case-insensitive, whitespace-trimmed). Ambiguous (2+ users), "
        "unmatched and inactive experiences are reported and never linked. "
        "Use --dry-run first and review every WOULD LINK line; run "
        "--report-stale afterwards."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be linked without changing anything.",
        )
        parser.add_argument(
            "--report-stale",
            action="store_true",
            help=(
                "Only list journey progress, advent progress and QR tokens "
                "held by someone other than the experience's linked user "
                "(run after linking). Links and changes nothing."
            ),
        )

    def handle(self, *args, **options):
        if options["report_stale"]:
            self._report_stale()
            return

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
            if counts["linked"]:
                self.stdout.write(
                    self.style.WARNING(
                        "Review every WOULD LINK line before the real run: a "
                        "unique name match is not proof of identity (the "
                        "intended recipient may never have signed up)."
                    )
                )
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

            if not exp.is_active:
                counts["skipped"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"SKIPPED   experience #{exp.pk} ({first} {last}): inactive"
                        " - not linked, so a later gift cannot reactivate it."
                        " Set Linked user by hand if it should come back."
                    )
                )
                continue

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
                self._explain_unreachable_journeys(exp, other_pk)
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

    def _explain_unreachable_journeys(self, exp, target_pk):
        """A skipped experience keeps its journeys, but nobody can reach them:
        say how to move each one to the user's linked experience."""
        taken = set(
            JourneyConfiguration.objects.filter(
                special_experience_id=target_pk
            ).values_list("journey_type", flat=True)
        )
        for journey in exp.journeys.order_by("pk"):
            if journey.journey_type in taken:
                remedy = (
                    f"experience #{target_pk} already has a"
                    f" {journey.journey_type} journey, so it cannot be moved;"
                    " decide by hand which one to keep"
                )
            else:
                remedy = (
                    f"to keep it, move it to experience #{target_pk} in the"
                    " admin (Journey Configuration -> Special experience)"
                )
            self.stdout.write(
                self.style.WARNING(
                    f"          journey #{journey.pk} ({journey.journey_type})"
                    f" is unreachable: {remedy}"
                )
            )

    def _report_stale(self):
        """List rows held by someone other than the experience's linked user,
        e.g. a namesake who opened a journey through the old name match."""
        sources = (
            (
                "JourneyProgress",
                JourneyProgress.objects.select_related(
                    "user", "journey__special_experience__linked_user"
                ),
                lambda row: row.journey.special_experience,
            ),
            (
                "AdventProgress",
                AdventProgress.objects.select_related(
                    "user", "calendar__journey__special_experience__linked_user"
                ),
                lambda row: row.calendar.journey.special_experience,
            ),
            (
                "QRCodeToken",
                QRCodeToken.objects.select_related(
                    "user", "door__calendar__journey__special_experience__linked_user"
                ),
                lambda row: row.door.calendar.journey.special_experience,
            ),
        )
        totals = {}
        for model_label, queryset, experience_of in sources:
            totals[model_label] = 0
            for row in queryset.order_by("pk"):
                exp = experience_of(row)
                if exp.linked_user_id == row.user_id:
                    continue
                totals[model_label] += 1
                owner = (
                    f"linked to {_describe(exp.linked_user)}"
                    if exp.linked_user_id
                    else "not linked"
                )
                self.stdout.write(
                    self.style.WARNING(
                        f"STALE     {model_label} #{row.pk}: user"
                        f" {_describe(row.user)} on experience #{exp.pk} ({owner})"
                    )
                )

        self.stdout.write(
            "Stale rows: "
            + " ".join(f"{label}={count}" for label, count in totals.items())
        )
        if any(totals.values()):
            self.stdout.write(
                "They grant no access. Delete the stale progress rows in the "
                "admin, and give the linked user new QR tokens for those doors "
                "(admin: QR Code Tokens)."
            )


def _describe(user):
    return f"#{user.pk} <{user.email or user.username}>"
