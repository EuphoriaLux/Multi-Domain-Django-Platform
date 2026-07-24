"""
Send the 24h untouched-lead reminder for "My Crush!" coach leads.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md
§6/O8 — a member who declares a crush is promised a coach call within 48h.
This fires at the halfway mark so the routed coach still has a day to make
it, and only for leads nobody has touched.

A lead is skipped when the coach already scheduled or completed the call,
when a reminder was already recorded (``reminder_sent_at``), when the lead
left the open statuses (a member block or coach decline flips it to
``declined``), or when it has no active routed coach — an unrouted pool lead
has nobody to remind and waits for triage instead.

Usage:
    # Preview what would be sent (recommended first)
    python manage.py send_crush_lead_reminders --dry-run

    # Send
    python manage.py send_crush_lead_reminders

In production this is driven by the CrushLeadReminders Azure Function timer,
which POSTs to /api/admin/crush-lead-reminders/ rather than running the
command directly — same pattern as the SLA sweep and profile reminders.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from crush_lu.services.crush_leads import (
    REMINDER_AFTER,
    reminder_candidates,
    sweep_lead_reminders,
)


class Command(BaseCommand):
    help = "Send the 24h untouched-lead reminder for My Crush! coach leads"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the leads that would be reminded without sending or "
            "recording anything.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        dry_run = options["dry_run"]

        if dry_run:
            candidates = list(reminder_candidates(now))
            if not candidates:
                self.stdout.write(
                    self.style.SUCCESS("No crush leads are due a reminder.")
                )
                return
            self.stdout.write(
                f"{len(candidates)} lead(s) due a reminder "
                f"(untouched for {REMINDER_AFTER}):"
            )
            for lead in candidates:
                age = now - lead.requested_at
                self.stdout.write(
                    f"  lead #{lead.pk} — coach "
                    f"{lead.assigned_coach.user.username} — "
                    f"declared {age} ago — call by {lead.call_by:%Y-%m-%d %H:%M}"
                )
            self.stdout.write(
                self.style.WARNING("Dry run — nothing sent, nothing recorded.")
            )
            return

        result = sweep_lead_reminders(now=now)
        if result["failed"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Sent {result['sent']} reminder(s), {result['failed']} "
                    f"failed (see logs; failed leads stay eligible)."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Sent {result['sent']} reminder(s).")
            )
