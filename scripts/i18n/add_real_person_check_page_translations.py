"""Add the coach real-person check page's layout strings (EN, DE, FR).

Coach copy: DE "du", FR "tu" register; neither string addresses the coach.
Appends only missing entries: polib.save() would rewrap the whole catalogue.

Run: python scripts/i18n/add_real_person_check_page_translations.py
     python scripts/build_assets.py --no-npm
"""

from pathlib import Path

import polib

# (msgid / EN, DE, FR)
STRINGS = [
    ("Steps", "Schritte", "Étapes"),
    (
        "The video call opens on this page 15 minutes before the saved appointment.",
        "Der Videoanruf öffnet sich auf dieser Seite 15 Minuten vor dem gespeicherten Termin.",
        "L'appel vidéo s'ouvre sur cette page 15 minutes avant le rendez-vous enregistré.",
    ),
]


def main():
    root = Path(__file__).resolve().parents[2]
    for language, index in (("en", 0), ("de", 1), ("fr", 2)):
        path = root / "crush_lu" / "locale" / language / "LC_MESSAGES/django.po"
        source = path.read_text(encoding="utf-8")
        po = polib.pofile(path)
        additions = [
            str(polib.POEntry(msgid=row[0], msgstr=row[index])).strip()
            for row in STRINGS
            if po.find(row[0]) is None
        ]
        if additions:
            result = source.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
            path.write_text(result, encoding="utf-8", newline="\n")
        print(f"{language}: {len(additions)} of {len(STRINGS)} strings added")


if __name__ == "__main__":
    main()
