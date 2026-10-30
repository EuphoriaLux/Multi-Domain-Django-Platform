"""One-off: DE/FR for the Mollie donation strings on the support card.

Run with: .venv/Scripts/python.exe scripts/i18n/add_mollie_donation_translations.py
Appends missing entries to the crush_lu django.po catalogs (DE informal "du",
FR formal "vous", matching the neighbouring payment strings). The .mo files
are built by scripts/build_assets.py.
"""

import polib

NEW = {
    "Secure checkout by Mollie": {
        "de": "Sichere Zahlung über Mollie",
        "fr": "Paiement sécurisé via Mollie",
    },
}


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Appended as text rather than po.save(): polib re-wraps and re-orders the
# whole catalogue, turning a dozen new strings into a 30,000-line diff.
for lang in ("de", "fr"):
    path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
    po = polib.pofile(path)
    blocks = []
    for msgid, translations in NEW.items():
        if po.find(msgid) is not None:
            continue
        flag = "#, python-format\n" if "%(" in msgid else ""
        blocks.append(
            f"{flag}msgid {_quote(msgid)}\nmsgstr {_quote(translations[lang])}\n"
        )
    if blocks:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n" + "\n".join(blocks))
    print(f"{lang}: {len(blocks)} added")
