"""
Backfill ``recipient_coach`` on "My Crush!" leads declared before Phase D.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md
§11 / O13.

Phase D added the recipient-side co-coach hand-off. Leads declared before it
shipped were never routed on the recipient side, so their ``recipient_coach``
is null and the recipient's own coach never got an outreach task — the routed
coach silently owns both halves for those rows.

**A command, not a data migration.** The routing rule lives in
``EventConnection.assign_recipient_coach()``. Historical models inside a
migration do not carry model methods, so a migration would have to
re-implement it — and that copy would drift from the real one. This calls the
real method, which also means the rule can only ever be wrong in one place.

The rule it applies is the *recipient* one, which is deliberately narrower
than the requester-side ``assign_coach()``: the recipient's approved-submission
coach, then their profile's permanent coach, both of which must be active.
There is no event-coach or pool fallback, and it returns ``None`` — leaving
``recipient_coach`` null on purpose — when the answer is the routed coach or
there is no coach at all. In that case one person covers both halves, there is
no hand-off to track, and the routed coach's own recipient-answer controls
stay enabled. Manufacturing a co-coach here would break both.

Scope: open crush leads with no recipient coach and no recorded recipient
answer — the set where a hand-off could still happen. A closed or
already-answered lead has no outreach task to create, so assigning it a
co-coach would only add a name nobody acts on.

Idempotent and re-runnable: a lead that already has a coach is not in the
queryset, and a lead the rule declines to route stays null and is simply
re-examined (and re-skipped) on the next run.

Usage:
    # Preview (recommended first — this is a write against historical rows)
    python manage.py backfill_crush_recipient_coaches --dry-run

    # Apply
    python manage.py backfill_crush_recipient_coaches

Check first whether there is anything to do: if Phase B/C (#681) had not
deployed before Phase D, the candidate set is empty and this is a no-op.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from crush_lu.models.connections import EventConnection


class Command(BaseCommand):
    help = (
        "Backfill recipient_coach on My Crush! leads declared before Phase D"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be assigned without writing anything.",
        )

    def _candidates(self):
        return (
            EventConnection.objects.filter(
                flow=EventConnection.FLOW_CRUSH,
                recipient_coach__isnull=True,
                recipient_response__isnull=True,
                status__in=EventConnection.OPEN_LEAD_STATUSES,
            )
            .select_related("recipient", "assigned_coach__user", "event")
            .order_by("requested_at", "id")
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        candidates = list(self._candidates())

        if not candidates:
            self.stdout.write(
                self.style.SUCCESS(
                    "No crush leads are missing a recipient coach."
                )
            )
            return

        self.stdout.write(f"{len(candidates)} lead(s) to examine:")

        assigned = 0
        skipped = 0
        for lead in candidates:
            if dry_run:
                # Ask the same question without writing: resolve the coach the
                # way the model method would, then throw the write away. A
                # savepoint is cheaper and truer than duplicating the tier
                # logic here, which is exactly the drift O13 warns about.
                with transaction.atomic():
                    coach = lead.assign_recipient_coach()
                    transaction.set_rollback(True)
            else:
                coach = lead.assign_recipient_coach()

            if coach is None:
                skipped += 1
                self.stdout.write(
                    f"  lead #{lead.pk} — no co-coach (recipient has no "
                    f"active coach, or it is the routed coach) — left null"
                )
                continue

            assigned += 1
            self.stdout.write(
                f"  lead #{lead.pk} — recipient coach "
                f"{coach.user.username}"
            )

        summary = (
            f"{assigned} lead(s) routed to a co-coach, "
            f"{skipped} deliberately left null."
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry run — nothing written. {summary}")
            )
        else:
            self.stdout.write(self.style.SUCCESS(summary))
