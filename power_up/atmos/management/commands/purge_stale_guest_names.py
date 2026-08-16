"""Clears Guest.display_name once a guest's window has passed.

join.html tells guests: "We only keep this for tonight." Nothing enforced
that — no Settlement flow, no tab-close hook, no scheduled job exists on
this platform to run this automatically (per docs/specs §2.2, there's no
`db_worker`/scheduler here at all). This command makes the promise
operationally true, but only when something actually runs it — wiring that
up (a real timer, matching the platform's existing scheduled-job pattern)
is a separate follow-up, not solved here.

`alias` (the noir persona) is deliberately left untouched: it's not personal
data (spec §8.4), and it's what order history / the chronicle read to stay
legible. Only `display_name` — the one field a guest can type themselves —
is personal data and gets cleared.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from power_up.atmos.models import Guest, Venue


class Command(BaseCommand):
    help = "Purge Guest.display_name for guests past their venue's guest window."

    def handle(self, *args, **options):
        total = 0
        for venue in Venue.objects.all():
            cutoff = timezone.now() - timezone.timedelta(minutes=venue.guest_window_minutes)
            updated = (
                Guest.objects.filter(venue=venue, joined_at__lt=cutoff)
                .exclude(display_name="")
                .update(display_name="")
            )
            total += updated
            if updated:
                self.stdout.write(f"  {venue.name}: purged {updated} guest name(s)")
        self.stdout.write(self.style.SUCCESS(f"Purged {total} guest display name(s) total."))
