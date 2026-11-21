"""
Close every open "My Crush!" coach lead (decision 2026-11-20).

"My Crush!" was replaced by post-event swiping, and for the December swipe
campaign Tom chose to close the old leads and let everyone swipe again rather
than carry them over. Each open lead (pending, coach reviewing, coach
approved) becomes ``declined`` with a ``crush_lead_retired`` audit entry. That
takes it out of every coach queue and off the member's connection list.
Nobody is notified: the member page treats a closed lead the same way.

Usage:
    python manage.py close_open_crush_leads            # list only
    python manage.py close_open_crush_leads --apply    # close them
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from crush_lu.models.connections import EventConnection


class Command(BaseCommand):
    help = 'Close every open "My Crush!" coach lead (lists them without --apply)'

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", help="Close the leads (default: list only)"
        )

    def handle(self, *args, **options):
        leads = (
            EventConnection.objects.filter(
                flow=EventConnection.FLOW_CRUSH,
                status__in=EventConnection.OPEN_LEAD_STATUSES,
            )
            .select_related("event")
            .order_by("requested_at")
        )
        rows = list(leads)
        for lead in rows:
            self.stdout.write(
                f"#{lead.pk}  {lead.status:<16} {lead.event.title}  "
                f"requested {lead.requested_at:%Y-%m-%d}"
            )
        self.stdout.write(f"{len(rows)} open lead(s).")
        if not options["apply"]:
            self.stdout.write("Dry run: nothing changed. Re-run with --apply.")
            return

        closed = 0
        for lead in rows:
            with transaction.atomic():
                locked = (
                    EventConnection.objects.select_for_update()
                    .filter(
                        pk=lead.pk,
                        flow=EventConnection.FLOW_CRUSH,
                        status__in=EventConnection.OPEN_LEAD_STATUSES,
                    )
                    .first()
                )
                if locked is None:
                    continue  # changed since the listing
                locked.log_system_action(
                    "crush_lead_retired", previous_status=locked.status
                )
                locked.status = "declined"
                locked.save(update_fields=["status", "system_actions"])
                closed += 1
        self.stdout.write(self.style.SUCCESS(f"Closed {closed} lead(s)."))
