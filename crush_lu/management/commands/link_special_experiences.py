"""
Link legacy name-matched SpecialUserExperience rows to their user account.

Special journeys used to be granted to whoever logged in with the same first
and last name as the experience, so a namesake could open someone else's
private journey. Access now requires ``linked_user``; this command is the
one-time migration path for the legacy experiences that were never linked.

Usage:
    # Report: list every name match, change nothing. A unique match prints
    # as NEEDS APPROVAL with the exact --approve pair to pass once verified.
    python manage.py link_special_experiences

    # Preview exactly what a real run with these approvals would do,
    # including its abort, saving nothing
    python manage.py link_special_experiences --dry-run --approve 12:345

    # Link only the pairs you verified, one --approve per NEEDS APPROVAL line
    python manage.py link_special_experiences --approve 12:345 --approve 13:678

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
    - Exactly one candidate -> LINKED, but only when that exact pair was
      passed as --approve EXPERIENCE_ID:USER_ID; otherwise NEEDS APPROVAL.
      A unique name match is not proof of identity (the intended recipient
      may never have signed up), so nothing is linked without a person
      checking it.
    - Two or more candidates -> AMBIGUOUS (never linked; link it by hand in
      the admin). None -> UNMATCHED.
    - A user can only be linked to one experience (``unique_linked_user``), so
      a candidate already linked to another experience is SKIPPED, and the
      skipped experience's journeys are listed with how to move them.
    - Approvals are all-or-nothing. An --approve pair that is not an unlinked
      experience's single matching active user, or whose user is already
      linked elsewhere, is REJECTED and the run aborts with nothing linked
      (the transaction rolls back). --dry-run applies the same rule, so it
      previews that abort too.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
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
        "relied on first+last name matching. Reports each active, unlinked "
        "experience's single active user with the same first and last name "
        "(case-insensitive, whitespace-trimmed) as NEEDS APPROVAL, and links "
        "only the pairs passed as --approve EXPERIENCE_ID:USER_ID. Ambiguous "
        "(2+ users), unmatched and inactive experiences are reported and never "
        "linked. --dry-run previews a run with the same approvals, including "
        "its abort; run --report-stale afterwards."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Print what the real run would do with the same --approve "
                "pairs (WOULD LINK / NEEDS APPROVAL / REJECTED) without "
                "changing anything."
            ),
        )
        parser.add_argument(
            "--approve",
            action="append",
            default=[],
            metavar="EXPERIENCE_ID:USER_ID",
            help=(
                "Link this experience to this user (repeatable, or "
                "comma-separated). Only a pair that is the experience's single "
                "matching active user, not linked elsewhere, is linked; any "
                "other pair is REJECTED and aborts the run, also under "
                "--dry-run. Copy the pairs from the NEEDS APPROVAL lines."
            ),
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
        approvals = _parse_approvals(options["approve"])
        if dry_run:
            self.stdout.write("DRY RUN - no changes will be saved.")

        counts = {
            "linked": 0,
            "ambiguous": 0,
            "unmatched": 0,
            "skipped": 0,
            "needs_approval": 0,
        }
        # LINKED / WOULD LINK lines are held back until the run is known to
        # commit: a green LINKED line for a row the abort then rolls back
        # would read as linked to anyone scanning stdout.
        linked_lines = []
        with transaction.atomic():
            used = self._link_all(dry_run, approvals, counts, linked_lines)
            rejected = sorted(approvals - used)
            if rejected:
                for exp_pk, user_pk in rejected:
                    self.stdout.write(
                        self.style.ERROR(
                            f"REJECTED  --approve {exp_pk}:{user_pk}: not an "
                            "unlinked experience's single matching active user, "
                            "or that user is already linked to another "
                            "experience (see the lines above)"
                        )
                    )
                prefix = "Dry run: the real run would abort. " if dry_run else ""
                # Raising inside atomic() rolls back every row saved above.
                raise CommandError(
                    f"{prefix}Nothing was linked: {len(rejected)} --approve "
                    "pair(s) rejected (see the REJECTED lines)."
                )

        for line in linked_lines:
            self.stdout.write(line)

        verb = "would link" if dry_run else "linked"
        self.stdout.write(
            f"Summary: {verb}={counts['linked']} ambiguous={counts['ambiguous']}"
            f" unmatched={counts['unmatched']} skipped={counts['skipped']}"
            f" needs_approval={counts['needs_approval']}"
        )
        if counts["needs_approval"]:
            self.stdout.write(
                self.style.WARNING(
                    "Not linked without approval: a unique name match is not "
                    "proof of identity (the intended recipient may never have "
                    "signed up). Check each NEEDS APPROVAL line and re-run with "
                    "--approve EXPERIENCE_ID:USER_ID for the ones that are the "
                    "right person."
                )
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

    def _link_all(self, dry_run, approvals, counts, linked_lines):
        """Link the approved pairs and report every other experience.

        Returns the --approve pairs that were linked (or would be). The same
        branches run with and without --dry-run; only the save differs."""
        User = get_user_model()
        # Users claimed during this run: linked_user is unique per user.
        claimed = {}
        used = set()

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

            pair = (exp.pk, user.pk)
            if pair not in approvals:
                counts["needs_approval"] += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"NEEDS APPROVAL {label} -> user {_describe(user)}:"
                        f" not linked (--approve {exp.pk}:{user.pk} after checking)"
                    )
                )
                continue

            used.add(pair)
            claimed[user.pk] = exp.pk
            counts["linked"] += 1
            if dry_run:
                linked_lines.append(f"WOULD LINK {label} -> user {_describe(user)}")
                continue

            exp.linked_user = user
            exp.save(update_fields=["linked_user", "updated_at"])
            linked_lines.append(
                self.style.SUCCESS(f"LINKED    {label} -> user {_describe(user)}")
            )
        return used

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


def _parse_approvals(values):
    """``["12:34", "13:35,14:36"]`` -> ``{(12, 34), (13, 35), (14, 36)}``.

    Anything that is not two ASCII-decimal ids around a colon is a
    CommandError, never a traceback: ``str.isdecimal`` rejects what ``int``
    would not parse the same way (superscripts, signs, underscores), and the
    ``int`` call is still guarded for its own limits (digit-count cap)."""
    pairs = set()
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            exp_id, sep, user_id = item.partition(":")
            exp_id, user_id = exp_id.strip(), user_id.strip()
            if not (sep and exp_id.isdecimal() and user_id.isdecimal()):
                raise CommandError(
                    f"--approve expects EXPERIENCE_ID:USER_ID, got {item!r}"
                )
            try:
                pairs.add((int(exp_id), int(user_id)))
            except ValueError as exc:
                raise CommandError(
                    f"--approve expects EXPERIENCE_ID:USER_ID, got {item!r}: {exc}"
                ) from None
    return pairs
