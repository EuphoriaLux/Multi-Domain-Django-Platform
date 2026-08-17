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
from django.db import transaction
from django.db.models import F, Q
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
            # Deliberately NOT a bare `.exclude(display_name="")`: a guest
            # whose display_name already reads "" can still have a leftover
            # personal `Order.alias_snapshot` from a race where a placement
            # committed `guest.display` (read before this command's clear)
            # just after an earlier run had already blanked display_name —
            # excluding on the current (already-cleared) field value is
            # exactly what let that leftover snapshot persist indefinitely.
            # But scanning literally every guest past the window forever
            # (once fully purged, a guest never needs revisiting) makes this
            # command's cost grow with the venue's entire history instead of
            # its actual backlog. Cover both real candidates with one query
            # instead: a name still set, OR at least one order whose
            # snapshot has already drifted from the (immutable) alias — that
            # second clause is what actually catches the race above, without
            # requiring "every guest ever" to prove it doesn't apply.
            stale_guests = list(
                Guest.objects.filter(venue=venue, joined_at__lt=cutoff)
                .filter(
                    Q(display_name__gt="")
                    | (Q(orders__isnull=False) & ~Q(orders__alias_snapshot=F("alias")))
                )
                .distinct()
            )
            guests_purged = 0
            orders_purged = 0
            for guest in stale_guests:
                if apply_changes:
                    # Lock this Guest row for the duration of the fix.
                    # _create_order_atomic() (views.py) locks the same row via
                    # select_for_update() before writing Order.alias_snapshot
                    # — the two locks serialize against each other, so a
                    # placement already in flight for this guest either fully
                    # commits (its alias_snapshot is then caught by the
                    # `.exclude()` below, in this same locked transaction)
                    # before we touch it, or fully waits until after we've
                    # cleared display_name and reset every existing snapshot.
                    with transaction.atomic():
                        locked_guest = Guest.objects.select_for_update().get(
                            pk=guest.pk
                        )
                        had_name = bool(locked_guest.display_name)
                        order_count = locked_guest.purge_display_name()
                else:
                    order_count = guest.orders.exclude(
                        alias_snapshot=guest.alias
                    ).count()
                    had_name = bool(guest.display_name)
                if order_count or had_name:
                    guests_purged += 1
                orders_purged += order_count
            total_guests += guests_purged
            total_orders += orders_purged
            if guests_purged:
                verb = "purged" if apply_changes else "would purge"
                self.stdout.write(
                    f"  {venue.name}: {verb} {guests_purged} guest name(s) and "
                    f"{orders_purged} order snapshot(s)"
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
