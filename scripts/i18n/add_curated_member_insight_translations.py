"""One-off: EN/DE/FR catalog entries for the member-facing curated insights.

Run with: .venv/Scripts/python.exe scripts/i18n/add_curated_member_insight_translations.py
Check only (no writes): add --check

Adds the strings introduced on the member event page for a curated speed
dating: the personal match-bucket sentence and coarse social proof on the
outlook card (``components/curated_group_outlook.html``) and the selected
member's own group summary and tables (``components/curated_member_group.html``).

Register follows the catalogs: DE du, FR vous. Every sentence is a bucket or a
fact about the viewer's own place; none carries a count about other people.

No ``makemessages`` on this machine: every msgid is typed by hand, and
``--check`` extracts every ``trans``/``blocktrans`` body from the two partials
and refuses to run when the table and the sources disagree. Compiles the DE/FR
.mo directly via polib. Sits on top of add_curated_insights_translations.py
(PR A); neither script touches the other's entries.
"""

import re
import sys

import polib

OUTLOOK = ("templates/crush_lu/components/curated_group_outlook.html", "")
GROUP = ("templates/crush_lu/components/curated_member_group.html", "")

NEW = {
    # -- outlook card: personal match bucket ---------------------------------
    (
        "Add your gender and date of birth to your profile to see how well the "
        "current applicants match your preferences."
    ): {
        "occ": OUTLOOK,
        "de": (
            "Ergänze dein Geschlecht und dein Geburtsdatum in deinem Profil, um zu "
            "sehen, wie gut die aktuellen Bewerber zu deinen Vorlieben passen."
        ),
        "fr": (
            "Ajoutez votre genre et votre date de naissance à votre profil pour "
            "voir dans quelle mesure les candidats actuels correspondent à vos "
            "préférences."
        ),
    },
    "Few of the current applicants match your preferences; widening them helps.": {
        "occ": OUTLOOK,
        "de": (
            "Nur wenige der aktuellen Bewerber passen zu deinen Vorlieben; sie zu "
            "erweitern hilft."
        ),
        "fr": (
            "Peu de candidats actuels correspondent à vos préférences ; les "
            "élargir aide."
        ),
    },
    "About half an evening's worth of the current applicants match your preferences.": {
        "occ": OUTLOOK,
        "de": (
            "Die aktuellen Bewerber, die zu deinen Vorlieben passen, reichen für "
            "etwa einen halben Abend."
        ),
        "fr": (
            "Les candidats actuels qui correspondent à vos préférences suffisent "
            "pour environ une demi-soirée."
        ),
    },
    "Enough of the current applicants match your preferences to fill an evening.": {
        "occ": OUTLOOK,
        "de": (
            "Genug der aktuellen Bewerber passen zu deinen Vorlieben, um einen "
            "Abend zu füllen."
        ),
        "fr": (
            "Assez de candidats actuels correspondent à vos préférences pour "
            "remplir une soirée."
        ),
    },
    "Enough of the current applicants match your preferences for more than one group.": {
        "occ": OUTLOOK,
        "de": (
            "Genug der aktuellen Bewerber passen zu deinen Vorlieben für mehr als "
            "eine Gruppe."
        ),
        "fr": (
            "Assez de candidats actuels correspondent à vos préférences pour plus "
            "d’un groupe."
        ),
    },
    # -- outlook card: coarse social proof -----------------------------------
    "Several first-timers have applied.": {
        "occ": OUTLOOK,
        "de": "Mehrere Neulinge haben sich beworben.",
        "fr": "Plusieurs personnes participent pour la première fois.",
    },
    "Most applicants are verified.": {
        "occ": OUTLOOK,
        "de": "Die meisten Bewerber sind verifiziert.",
        "fr": "La plupart des candidats sont vérifiés.",
    },
    # -- selected member's own group -----------------------------------------
    "Your group is final.": {
        "occ": GROUP,
        "de": "Deine Gruppe steht fest.",
        "fr": "Votre groupe est définitif.",
    },
    "Your group is provisional.": {
        "occ": GROUP,
        "de": "Deine Gruppe ist vorläufig.",
        "fr": "Votre groupe est provisoire.",
    },
    (
        "You are in a group of %(size)s people; %(rounds)s rounds are planned; "
        "everyone gets at least %(minimum)s mini-dates."
    ): {
        "occ": GROUP,
        "de": (
            "Du bist in einer Gruppe von %(size)s Personen; %(rounds)s Runden sind "
            "geplant; alle bekommen mindestens %(minimum)s Mini-Dates."
        ),
        "fr": (
            "Vous faites partie d’un groupe de %(size)s personnes ; %(rounds)s "
            "rondes sont prévues ; chacun a au moins %(minimum)s mini-rencontres."
        ),
    },
    "Groups stay together for the whole evening; this one is locked and will not change.": {
        "occ": GROUP,
        "de": (
            "Gruppen bleiben den ganzen Abend zusammen; diese ist gesperrt und "
            "ändert sich nicht mehr."
        ),
        "fr": (
            "Les groupes restent ensemble toute la soirée ; celui-ci est "
            "verrouillé et ne changera plus."
        ),
    },
    "Your tables": {"occ": GROUP, "de": "Deine Tische", "fr": "Vos tables"},
    "Round %(round)s: table %(table)s, seat %(seat)s": {
        "occ": GROUP,
        "de": "Runde %(round)s: Tisch %(table)s, Platz %(seat)s",
        "fr": "Ronde %(round)s : table %(table)s, place %(seat)s",
    },
    "Round %(round)s: break": {
        "occ": GROUP,
        "de": "Runde %(round)s: Pause",
        "fr": "Ronde %(round)s : pause",
    },
    (
        "The group can still change until it is locked before the first round. "
        "Your tables appear here once it is final."
    ): {
        "occ": GROUP,
        "de": (
            "Die Gruppe kann sich noch ändern, bis sie vor der ersten Runde "
            "gesperrt wird. Deine Tische erscheinen hier, sobald sie feststeht."
        ),
        "fr": (
            "Le groupe peut encore changer jusqu’à son verrouillage avant la "
            "première ronde. Vos tables apparaîtront ici une fois qu’il sera "
            "définitif."
        ),
    },
}


def _template_msgids(path):
    text = open(path, encoding="utf-8").read()
    found = set()
    for match in re.finditer(r"{%\s*trans\s+(['\"])(.*?)\1\s*%}", text):
        found.add(match.group(2))
    for match in re.finditer(
        r"{%\s*blocktrans\b[^%]*%}(.*?){%\s*endblocktrans\s*%}", text, re.S
    ):
        # A plural block is keyed by its singular form; the existing outlook
        # partial carries two of those and they are already translated.
        singular = match.group(1).split("{% plural %}")[0]
        found.add(re.sub(r"{{\s*(\w+)\s*}}", r"%(\1)s", singular))
    return found


def check():
    """Refuse to run when the sources and this table disagree."""

    existing = {
        entry.msgid
        for entry in polib.pofile("crush_lu/locale/en/LC_MESSAGES/django.po")
    }
    extracted = _template_msgids("crush_lu/" + OUTLOOK[0]) | _template_msgids(
        "crush_lu/" + GROUP[0]
    )
    extracted_new = {msgid for msgid in extracted if msgid not in existing}
    declared = set(NEW)
    missing = extracted_new - declared
    orphans = {msgid for msgid in declared - extracted if msgid not in existing}
    problems = []
    if missing:
        problems.append(f"in sources but not in NEW: {sorted(missing)}")
    if orphans:
        problems.append(f"in NEW but not in sources: {sorted(orphans)}")
    if problems:
        sys.exit("\n".join(problems))
    print(f"check: {len(declared)} entries match the sources")


def main():
    for lang in ("en", "de", "fr"):
        path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
        existing = {entry.msgid for entry in polib.pofile(path)}
        # Append as text rather than ``po.save()``: polib re-wraps every long
        # entry it touches. gettext accepts entries in any order.
        with open(path, "rb") as handle:
            raw = handle.read()
        newline = "\r\n" if b"\r\n" in raw[:2000] else "\n"
        chunks = []
        for msgid, spec in NEW.items():
            if msgid in existing:
                continue
            entry = polib.POEntry(
                msgid=msgid,
                msgstr="" if lang == "en" else spec[lang],
                occurrences=[spec["occ"]],
            )
            chunks.append(str(entry).replace("\n", newline))
        if chunks:
            if not raw.endswith(newline.encode()):
                raw += newline.encode()
            # A blank line before the first appended entry keeps the catalog
            # readable; gettext does not need it.
            raw += newline.encode() + newline.encode().join(
                chunk.encode("utf-8") for chunk in chunks
            )
            with open(path, "wb") as handle:
                handle.write(raw)
        po = polib.pofile(path)
        if lang != "en":
            mo_path = path.replace(".po", ".mo")
            po.save_as_mofile(mo_path)
            mo = polib.mofile(mo_path)
            assert mo.metadata.get("Content-Type"), "compiled .mo lost its metadata"
            assert len(mo) == len(po.translated_entries()), (
                f"{lang}: mo has {len(mo)} entries, po has "
                f"{len(po.translated_entries())} translated"
            )
        print(f"{lang}: {len(chunks)} added, catalog size {len(po)}")


if __name__ == "__main__":
    check()
    if "--check" not in sys.argv:
        main()
