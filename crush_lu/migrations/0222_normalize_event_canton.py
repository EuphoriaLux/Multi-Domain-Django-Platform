"""Normalise MeetupEvent.canton onto the 12 canonical canton names.

`canton` was free text ("Free-text entry for flexibility"), so real rows mix
casing and levels: 'luxembourg' lowercase, and communes such as 'Beaufort' sat
where a canton belongs. Now that the field has `choices`, those values fail
admin validation on the next save -- which is a good prompt for a human, but a
bad surprise for whoever opens an unrelated event first.

Anything this cannot map is left exactly as it is and named in the output.
`choices` is not a database constraint, so an unmapped value still reads and
renders; only the next admin save asks for it to be corrected.
"""

from django.db import migrations

# Communes and cities that have turned up in `canton`, mapped to the canton
# they are actually in. Seeded from the map in
# 0031_migrate_location_to_cantons (which stores slugs; these are the display
# names the event side uses), plus Beaufort, which the seed data uses.
COMMUNE_TO_CANTON = {
    "luxembourg city": "Luxembourg",
    "esch/alzette": "Esch-sur-Alzette",
    "esch sur alzette": "Esch-sur-Alzette",
    "differdange": "Esch-sur-Alzette",
    "dudelange": "Esch-sur-Alzette",
    "ettelbruck": "Diekirch",
    "mondorf-les-bains": "Remich",
    "beaufort": "Echternach",
}

CANONICAL = [
    "Capellen",
    "Clervaux",
    "Diekirch",
    "Echternach",
    "Esch-sur-Alzette",
    "Grevenmacher",
    "Luxembourg",
    "Mersch",
    "Redange",
    "Remich",
    "Vianden",
    "Wiltz",
]


def normalize(apps, schema_editor):
    MeetupEvent = apps.get_model("crush_lu", "MeetupEvent")
    by_casefold = {name.casefold(): name for name in CANONICAL}

    changed = []
    unmapped = set()
    for event in MeetupEvent.objects.exclude(canton="").only("id", "canton"):
        key = " ".join(event.canton.split()).casefold()
        target = by_casefold.get(key) or COMMUNE_TO_CANTON.get(key)
        if target is None:
            unmapped.add(event.canton)
            continue
        if target != event.canton:
            event.canton = target
            changed.append(event)

    if changed:
        MeetupEvent.objects.bulk_update(changed, ["canton"], batch_size=500)
        print(f"  normalised canton on {len(changed)} event(s)")
    if unmapped:
        print(
            "  left unchanged, not a recognised canton or known commune: "
            + ", ".join(sorted(repr(value) for value in unmapped))
        )


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0221_structured_event_address"),
    ]

    operations = [
        # Forward-only: the pre-normalisation spellings carry no information the
        # normalised ones lack, so there is nothing to restore.
        migrations.RunPython(normalize, migrations.RunPython.noop),
    ]
