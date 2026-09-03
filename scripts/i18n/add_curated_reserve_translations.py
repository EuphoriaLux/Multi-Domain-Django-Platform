"""One-off: EN/DE/FR catalog entries for the curated-group reserve notice.

Run with: .venv/Scripts/python.exe scripts/i18n/add_curated_reserve_translations.py

Adds the strings introduced with the "you stay in the pool" email sent to
applicants left out of an invited curated generation, the matching
notification-kind label, and the event-card pill that replaces the seat count
on curated events.

Register follows the catalogs: DE du, FR vous. "Pool" is kept as the FR noun
because the withdrawal email this one sits next to already says "rester dans
le pool"; the public outlook card's "vivier" is a different surface.

Compiles the DE/FR .mo directly via polib -- gettext is not installed on this
machine and a hand-rolled .mo 500s every DE/FR request. EN gets the msgids
with an empty msgstr, matching how every other EN entry is recorded.
"""

import polib

EMAIL = ("templates/crush_lu/emails/curated_group_reserve.html", "")
HELPER = ("email_helpers.py", "")
CARD = ("templates/crush_lu/includes/event_card.html", "")
MODEL = ("models/events.py", "")

NEW = {
    "Your application for {title} stays in the pool": {
        "occ": HELPER,
        "de": "Deine Bewerbung für {title} bleibt im Pool",
        "fr": "Votre candidature pour {title} reste dans le pool",
    },
    "Your application stays in the pool": {
        "occ": EMAIL,
        "de": "Deine Bewerbung bleibt im Pool",
        "fr": "Votre candidature reste dans le pool",
    },
    (
        "Thank you for applying to <strong>%(title)s</strong>. The first groups "
        "for this evening have now been formed, and we could not place you in "
        "one of them this time."
    ): {
        "occ": EMAIL,
        "de": (
            "Danke für deine Bewerbung für <strong>%(title)s</strong>. Die "
            "ersten Gruppen für diesen Abend stehen jetzt fest, und dieses Mal "
            "konnten wir dir keinen Platz in einer davon geben."
        ),
        "fr": (
            "Merci d’avoir postulé pour <strong>%(title)s</strong>. Les premiers "
            "groupes de cette soirée sont maintenant formés et nous n’avons pas "
            "pu vous y attribuer une place cette fois-ci."
        ),
    },
    "No payment was taken. Your application stays in the pool.": {
        "occ": EMAIL,
        "de": "Es wurde keine Zahlung eingezogen. Deine Bewerbung bleibt im Pool.",
        "fr": "Aucun paiement n’a été prélevé. Votre candidature reste dans le pool.",
    },
    (
        "Groups can still change before the evening. If a place opens in a "
        "group that works for you, we will contact you before any payment is "
        "required."
    ): {
        "occ": EMAIL,
        "de": (
            "Die Gruppen können sich bis zum Abend noch ändern. Wenn ein Platz "
            "in einer passenden Gruppe frei wird, melden wir uns bei dir, bevor "
            "eine Zahlung fällig wird."
        ),
        "fr": (
            "Les groupes peuvent encore évoluer avant la soirée. Si une place se "
            "libère dans un groupe qui vous correspond, nous vous contacterons "
            "avant tout paiement."
        ),
    },
    "Not selected — application stays in the pool": {
        "occ": MODEL,
        "de": "Nicht ausgewählt — Bewerbung bleibt im Pool",
        "fr": "Non sélectionné — la candidature reste dans le pool",
    },
    "Curated groups · applications open": {
        "occ": CARD,
        "de": "Kuratierte Gruppen · Bewerbungen offen",
        "fr": "Groupes sur sélection · candidatures ouvertes",
    },
    "Curated groups · applications closed": {
        "occ": CARD,
        "de": "Kuratierte Gruppen · Bewerbungen geschlossen",
        "fr": "Groupes sur sélection · candidatures closes",
    },
}


def main():
    for lang in ("en", "de", "fr"):
        path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
        existing = {entry.msgid for entry in polib.pofile(path)}
        # Append as text rather than ``po.save()``: polib re-wraps every long
        # entry it touches, which turns an eight-string change into a
        # several-hundred-line diff. gettext accepts entries in any order.
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
            raw += newline.encode().join(chunk.encode("utf-8") for chunk in chunks)
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
    main()
