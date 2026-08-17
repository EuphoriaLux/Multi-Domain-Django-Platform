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

Runs dry-run by default, matching this platform's convention for destructive
personal-data-purge commands (see crush_lu's `gdpr_retention_cleanup`) — pass
`--apply` to actually mutate. Once this is wired to a real scheduler, a
misconfigured `guest_window_minutes` or a timezone/date bug in the cutoff
would otherwise purge personal data immediately and irreversibly on every
run, with no preview step to catch the mistake first.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from power_up.atmos.models import Guest, Venue


class Command(BaseCommand):
    help = (
        "Purge Guest.display_name and its Order.alias_snapshot copies past the guest "
        "window (dry-run unless --apply is passed)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually purge. Default is a dry-run report.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        total_guests = 0
        total_orders = 0
        for venue in Venue.objects.all():
            cutoff = timezone.now() - timezone.timedelta(
                minutes=venue.guest_window_minutes
            )
            stale_guests = list(
                Guest.objects.filter(venue=venue, joined_at__lt=cutoff).exclude(
                    display_name=""
                )
            )
            for guest in stale_guests:
                order_count = guest.orders.exclude(alias_snapshot=guest.alias).count()
                if apply_changes:
                    # Reset each of this guest's past orders back to the
                    # (non-personal) alias before clearing display_name
                    # below — otherwise the snapshot is the only remaining
                    # copy of a name we just promised to forget.
                    total_orders += guest.orders.exclude(
                        alias_snapshot=guest.alias
                    ).update(alias_snapshot=guest.alias)
                    guest.display_name = ""
                    guest.save(update_fields=["display_name"])
                else:
                    total_orders += order_count
                total_guests += 1
            if stale_guests:
                verb = "purged" if apply_changes else "would purge"
                self.stdout.write(
                    f"  {venue.name}: {verb} {len(stale_guests)} guest name(s)"
                )

        if apply_changes:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Purged {total_guests} guest display name(s) and "
                    f"{total_orders} order snapshot(s)."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"Would purge {total_guests} guest display name(s) and "
                    f"{total_orders} order snapshot(s). Dry-run only — re-run with "
                    "--apply to purge."
                )
            )
