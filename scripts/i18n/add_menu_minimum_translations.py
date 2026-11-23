"""One-off: DE/FR for the "minimum formula to take part" on Wine Tasting events.

Run with: python scripts/i18n/add_menu_minimum_translations.py

2026-11-23: an event's tasting formula can be marked as the minimum to take
part (Vinsmoselle, 5 Dec 2026: Wine Lover). The event page and the sign-up
picker say so. Compiles .mo via polib.
"""

import polib

NEW = {
    "Required to take part": {
        "de": "Pflicht für die Teilnahme",
        "fr": "Obligatoire pour participer",
    },
    "Available instead of the minimum formula": {
        "de": "Statt der Mindestformel möglich",
        "fr": "Possible à la place de la formule minimum",
    },
    "To take part, the %(name)s formula (%(price)s €) is the minimum. Drinks are "
    "paid on site directly to %(venue)s, separately from your Crush.lu "
    "registration.": {
        "de": "Für die Teilnahme ist die Formel %(name)s (%(price)s €) das "
        "Minimum. Die Getränke bezahlst du vor Ort direkt bei %(venue)s, "
        "getrennt von deiner Crush.lu-Anmeldung.",
        "fr": "Pour participer, la formule %(name)s (%(price)s €) est le "
        "minimum. Les boissons se règlent sur place directement auprès de "
        "%(venue)s, séparément de votre inscription Crush.lu.",
    },
    "The %(name)s formula (%(price)s €) is the minimum to take part. Your drinks "
    "are paid on site directly to %(venue)s, not to Crush.lu.": {
        "de": "Die Formel %(name)s (%(price)s €) ist das Minimum für die "
        "Teilnahme. Deine Getränke bezahlst du vor Ort direkt bei %(venue)s, "
        "nicht an Crush.lu.",
        "fr": "La formule %(name)s (%(price)s €) est le minimum pour participer. "
        "Tes boissons se règlent sur place directement auprès de %(venue)s, "
        "pas à Crush.lu.",
    },
}


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Appends raw entries instead of po.save(): polib re-wraps the whole catalog.
for lang in ("de", "fr"):
    path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
    existing = {e.msgid for e in polib.pofile(path)}
    chunks = []
    for msgid, translations in NEW.items():
        if msgid in existing:
            continue
        flag = "#, python-format\n" if "%(" in msgid else ""
        chunks.append(
            "#: crush_lu/templates/crush_lu/components/event_menu_list.html\n"
            f"{flag}msgid {_quote(msgid)}\nmsgstr {_quote(translations[lang])}\n"
        )
    if chunks:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(chunks))
    polib.pofile(path).save_as_mofile(path.replace(".po", ".mo"))
    print(f"{lang}: {len(chunks)} added")
