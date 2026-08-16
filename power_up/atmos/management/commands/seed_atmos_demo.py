"""One venue, a few tables, a small menu — enough to click through the pilot.

Venue name and table labels ("4", "9", "12") deliberately match
`power_up.atmos.preview`'s SERVICE fixture, so a demo can walk the preview's
printed tickets and the live web flow side by side without the persona names
looking like two different products.
"""

from __future__ import annotations

import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from power_up.atmos.models import (
    Guest,
    MenuCategory,
    MenuItem,
    Order,
    OrderItem,
    Table,
    Tab,
    Venue,
)

# Every Atmos model the seeded account should be able to administer —
# nothing outside this app. Used to scope permissions below.
_ATMOS_MODELS = [Venue, Table, MenuCategory, MenuItem, Tab, Guest, Order, OrderItem]

## (name, price, description, contains_alcohol)
MENU = {
    "Cocktails": [
        ("Old Fashioned", "8.50", "", True),
        ("Smoky Mezcalita", "9.50", "", True),
        ("Rye Sour", "9.00", "", True),
        ("French 75", "11.00", "", True),
        ("Velvet Fizz (0%)", "6.50", "", False),
    ],
    "Beer & Wine": [
        ("Pilsner, 33cl", "4.50", "", True),
        ("House Red, glass", "6.00", "", True),
        ("House White, glass", "6.00", "", True),
    ],
    "Snacks": [
        ("Bar Nuts", "4.00", "A little salt never hurt anyone.", False),
        ("Olives", "4.50", "", False),
        ("Charcuterie Board", "14.00", "For the table.", False),
    ],
}

TABLES = [("4", 4), ("9", 2), ("12", 6), ("7", 4)]


class Command(BaseCommand):
    help = "Seed one demo venue for Atmos: tables, menu, and a staff login."

    def handle(self, *args, **options):
        venue, created = Venue.objects.get_or_create(
            slug="velvet-hour",
            defaults={"name": "The Velvet Hour", "address": "12 Rue du Fossé, Luxembourg"},
        )
        self.stdout.write(("Created" if created else "Found") + f" venue: {venue.name}")

        for label, seats in TABLES:
            table, table_created = Table.objects.get_or_create(
                venue=venue, label=label, defaults={"seats": seats}
            )
            if table_created:
                self.stdout.write(f"  + table {label} (qr_token={table.qr_token})")

        for cat_name, items in MENU.items():
            category, _ = MenuCategory.objects.get_or_create(venue=venue, name=cat_name)
            for name, price, description, contains_alcohol in items:
                MenuItem.objects.get_or_create(
                    category=category,
                    name=name,
                    defaults={
                        "price": price,
                        "description": description,
                        "contains_alcohol": bool(contains_alcohol),
                    },
                )

        User = get_user_model()
        # Checking for *any* is_staff user (the original guard) is wrong on
        # this platform: test.power-up.lu is a shared multi-domain DB, so
        # crush_lu/crm/other apps' staff accounts already exist there long
        # before Atmos ever runs — that guard would see them and silently
        # skip creating atmos_staff altogether. Check for this specific
        # account instead.
        if not User.objects.filter(username="atmos_staff").exists():
            # A fixed password here would be a fixed, public credential —
            # this repo, and this command's own output, are both visible to
            # anyone. On staging (test.power-up.lu) that's a real account
            # with a guessable password reachable over the network, not a
            # local-only convenience. Generated fresh per run and printed
            # once; if it scrolls off, reset it with `changepassword` or
            # Django admin rather than re-running this (the `if not exists`
            # guard means a second run won't print it again).
            password = secrets.token_urlsafe(12)
            # Deliberately NOT is_superuser: this login is bar/KDS staff on
            # a shared multi-domain platform. A superuser credential handed
            # to bar staff (or leaked from a table-side device) would reach
            # every other app's customer, CRM, and operational data, not
            # just Atmos. is_staff is enough for @staff_member_required
            # (the KDS's only gate); explicit Atmos-model permissions are
            # enough for admin CRUD on power_up_admin_site.
            staff_user = User.objects.create_user(
                username="atmos_staff",
                email="atmos-staff@example.com",
                password=password,
                is_staff=True,
            )
            permissions = Permission.objects.filter(
                content_type__in=ContentType.objects.get_for_models(*_ATMOS_MODELS).values(),
                codename__regex=r"^(add|change|delete|view)_",
            )
            staff_user.user_permissions.add(*permissions)
            self.stdout.write(self.style.WARNING(
                f"Created staff login: atmos_staff / {password}  (save this — shown once)"
            ))
        else:
            self.stdout.write("atmos_staff already exists — not recreating.")

        self.stdout.write(self.style.SUCCESS("Atmos demo data ready."))
        for table in venue.tables.all():
            self.stdout.write(
                f"  Table {table.label}: http://power-up.localhost:8000/atmos/t/{table.qr_token}/"
            )
        self.stdout.write(
            f"  Staff KDS: http://power-up.localhost:8000/atmos/staff/{venue.slug}/kds/"
        )
