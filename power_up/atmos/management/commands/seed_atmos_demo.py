"""One venue, a few tables, a small menu — enough to click through the pilot.

Venue name and table labels ("4", "9", "12") deliberately match
`power_up.atmos.preview`'s SERVICE fixture, so a demo can walk the preview's
printed tickets and the live web flow side by side without the persona names
looking like two different products.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from power_up.atmos.models import MenuCategory, MenuItem, Table, Venue

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
        if not User.objects.filter(is_staff=True).exists():
            User.objects.create_user(
                username="atmos_staff",
                email="atmos-staff@example.com",
                password="atmos-staff",
                is_staff=True,
                is_superuser=True,
            )
            self.stdout.write("Created staff login: atmos_staff / atmos-staff")
        else:
            self.stdout.write("A staff user already exists — not creating another.")

        self.stdout.write(self.style.SUCCESS("Atmos demo data ready."))
        for table in venue.tables.all():
            self.stdout.write(
                f"  Table {table.label}: http://power-up.localhost:8000/atmos/t/{table.qr_token}/"
            )
        self.stdout.write(
            f"  Staff KDS: http://power-up.localhost:8000/atmos/staff/{venue.slug}/kds/"
        )
