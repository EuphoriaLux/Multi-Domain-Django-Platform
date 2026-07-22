"""Seed the Event Identity taxonomy additions (spec O3 / §8.2.1).

The Phase A keyword-map dry-run (2026-07-22) found the 38-interest catalogue
covered ~66% of legacy free-text interest occurrences. These additions lift
coverage to ~77% — the realistic ceiling — capturing the largest measured gaps
(Dancing was the single biggest, present in four languages).

All rows fit the existing 8 ``Interest.Category`` choices. ``sort_order`` starts
at 100 so the additions append after the original per-category rows (which use
0..n) without renumbering them. Idempotent via ``update_or_create(slug=...)``.
"""

from django.db import migrations


# (slug, category, en, de, fr) — ranked by captured occurrences in the dry-run.
ADDITIONS = [
    ("dancing", "music", "Dancing", "Tanzen", "Danse"),
    ("ball-racket-sports", "sports", "Ball & racket sports", "Ball- & Schlägersport", "Sports de balle & raquette"),
    ("water-sports", "sports", "Water sports", "Wassersport", "Sports nautiques"),
    ("animals-pets", "outdoors", "Animals & pets", "Tiere & Haustiere", "Animaux & compagnie"),
    ("cars-motorcycles", "sports", "Cars & motorcycles", "Autos & Motorräder", "Voitures & motos"),
    ("anime-manga", "arts", "Anime & manga", "Anime & Manga", "Anime & manga"),
    ("nightlife", "music", "Nightlife & going out", "Nachtleben & Ausgehen", "Vie nocturne & sorties"),
    ("winter-sports", "sports", "Winter sports", "Wintersport", "Sports d'hiver"),
    ("languages", "arts", "Languages", "Sprachen", "Langues"),
    ("self-development", "wellness", "Self-development", "Persönliche Entwicklung", "Développement personnel"),
    ("diy-crafts", "arts", "DIY & crafts", "Heimwerken & Basteln", "Bricolage & loisirs créatifs"),
    ("tech", "games", "Tech", "Technik", "Tech"),
]

SORT_ORDER_BASE = 100


def seed_additions(apps, schema_editor):
    Interest = apps.get_model("crush_lu", "Interest")
    for offset, (slug, category, en, de, fr) in enumerate(ADDITIONS):
        Interest.objects.update_or_create(
            slug=slug,
            defaults={
                "category": category,
                "sort_order": SORT_ORDER_BASE + offset,
                "is_active": True,
                "label": en,
                "label_en": en,
                "label_de": de,
                "label_fr": fr,
            },
        )


# Reverse is intentionally a no-op: `update_or_create` doesn't record which rows
# it created, so a blanket delete-by-slug on reverse would also remove a row an
# admin created independently, or one members have since selected — cascading
# away their `interests_new` M2M links. Leaving the seeded rows in place on
# reverse is safe (they're inert curated data); prune them by hand if ever needed.


class Migration(migrations.Migration):
    dependencies = [
        ("crush_lu", "0195_event_identity_fields"),
    ]

    operations = [
        migrations.RunPython(seed_additions, migrations.RunPython.noop),
    ]
