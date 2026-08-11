"""Re-normalise MeetupEvent.canton, matching on letters only.

0222 keyed its alias table on whitespace-collapsed, casefolded text. Real data
turned out to be punctuated: staging holds `Luxembourg - City`, `Luxembourg-City`
and `Lux`, none of which matched `luxembourg city`, so the migration ran, left
them alone, and reported them as unmapped. Nobody reads a deploy log line.

Matching now strips everything that is not a letter, so spacing and hyphens stop
mattering, and the alias table gained the abbreviations that were actually in
use. A separate migration rather than an edit to 0222 because 0222 has already
run: migrations do not re-apply, and the rows it skipped would stay skipped.

Re-running is safe. A value already equal to its canonical form is left
untouched, so this is a no-op everywhere 0222 succeeded.
"""

import re

from django.db import migrations

# Everything that is not a letter goes, so "Luxembourg - City", "Luxembourg-City"
# and "luxembourg  city" all reduce to the same key. Accents are kept: the
# canonical names carry none, and stripping them would fold distinct communes
# together for no gain.
_KEY_RE = re.compile(r"[^a-zà-öø-ÿ]+")

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

# Communes, quarters and abbreviations seen in the wild, mapped to their canton.
# The city ones matter most: "Luxembourg - City" and friends are how the capital
# has been typed, and they are a quarter-or-city name where a canton belongs.
ALIASES = {
    "luxembourgcity": "Luxembourg",
    "luxembourgville": "Luxembourg",
    "villehaute": "Luxembourg",
    "lux": "Luxembourg",
    "luxville": "Luxembourg",
    "eschalzette": "Esch-sur-Alzette",
    "eschsuralzette": "Esch-sur-Alzette",
    "esch": "Esch-sur-Alzette",
    "differdange": "Esch-sur-Alzette",
    "dudelange": "Esch-sur-Alzette",
    "belval": "Esch-sur-Alzette",
    "ettelbruck": "Diekirch",
    "mondorflesbains": "Remich",
    "mondorf": "Remich",
    "beaufort": "Echternach",
}


def _key(value):
    return _KEY_RE.sub("", (value or "").casefold())


def normalize(apps, schema_editor):
    MeetupEvent = apps.get_model("crush_lu", "MeetupEvent")
    by_key = {_key(name): name for name in CANONICAL}

    changed = []
    unmapped = set()
    for event in MeetupEvent.objects.exclude(canton="").only("id", "canton"):
        target = by_key.get(_key(event.canton)) or ALIASES.get(_key(event.canton))
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
        # Still not silent, but `backfill_event_addresses --audit` is what
        # actually blocks on these now, so they cannot slip by unnoticed.
        print(
            "  left unchanged, not a recognised canton or known alias: "
            + ", ".join(sorted(repr(value) for value in unmapped))
        )


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0223_event_canton_choices"),
    ]

    operations = [
        migrations.RunPython(normalize, migrations.RunPython.noop),
    ]
