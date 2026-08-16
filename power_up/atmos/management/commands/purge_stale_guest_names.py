"""Clears Guest.display_name (and its Order.alias_snapshot copies) once a
guest's window has passed.

join.html tells guests: "We only keep this for tonight." Nothing enforced
that — no Settlement flow, no tab-close hook, no scheduled job exists on
this platform to run this automatically (per docs/specs §2.2, there's no
`db_worker`/scheduler here at all). This command makes the promise
operationally true, but only when something actually runs it — wiring that
up (a real timer, matching the platform's existing scheduled-job pattern)
is a separate follow-up, not solved here.

`alias` (the noir persona) is deliberately left untouched: it's not personal
data (spec §8.4), and it's what order history / the chronicle read to stay
legible. Only a guest-typed `display_name` is personal data — but it doesn't
live in one place: `order_place()` copies `guest.display` (which prefers
`display_name` over `alias`) into every `Order.alias_snapshot` at placement
time, a permanent record independent of the Guest row. Clearing
`Guest.display_name` alone leaves that name sitting in `Order` history
forever, so both need purging together.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from power_up.atmos.models import Guest, Venue


class Command(BaseCommand):
    help = "Purge Guest.display_name and its Order.alias_snapshot copies past the guest window."

    def handle(self, *args, **options):
        total_guests = 0
        total_orders = 0
        for venue in Venue.objects.all():
            cutoff = timezone.now() - timezone.timedelta(minutes=venue.guest_window_minutes)
            stale_guests = list(
                Guest.objects.filter(venue=venue, joined_at__lt=cutoff).exclude(display_name="")
            )
            for guest in stale_guests:
                # Reset each of this guest's past orders back to the
                # (non-personal) alias before clearing display_name below —
                # otherwise the snapshot is the only remaining copy of a
                # name we just promised to forget.
                total_orders += guest.orders.exclude(alias_snapshot=guest.alias).update(
                    alias_snapshot=guest.alias
                )
                guest.display_name = ""
                guest.save(update_fields=["display_name"])
                total_guests += 1
            if stale_guests:
                self.stdout.write(f"  {venue.name}: purged {len(stale_guests)} guest name(s)")
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {total_guests} guest display name(s) and {total_orders} order snapshot(s)."
            )
        )
