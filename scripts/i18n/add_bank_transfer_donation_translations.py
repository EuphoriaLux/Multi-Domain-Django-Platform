"""One-off: DE/FR for the bank-transfer donation strings on the support card.

Run with: .venv/Scripts/python.exe scripts/i18n/add_bank_transfer_donation_translations.py
Appends missing entries to the crush_lu django.po catalogs (DE informal "du",
FR formal "vous", matching the neighbouring payment strings). The .mo files
are built by scripts/build_assets.py.
"""

import polib

NEW = {
    "Support us by bank transfer": {
        "de": "Unterstütze uns per Banküberweisung",
        "fr": "Soutenez-nous par virement bancaire",
    },
    "Prefer a bank transfer?": {
        "de": "Lieber per Banküberweisung?",
        "fr": "Vous préférez un virement bancaire ?",
    },
    "QR code for a bank transfer to Crush.lu": {
        "de": "QR-Code für eine Banküberweisung an Crush.lu",
        "fr": "Code QR pour un virement bancaire à Crush.lu",
    },
    "Scan the QR code with your banking app, or copy the details below. Any amount helps.": {
        "de": "Scanne den QR-Code mit deiner Banking-App oder kopiere die Angaben unten. Jeder Betrag hilft.",
        "fr": "Scannez le code QR avec votre application bancaire ou copiez les coordonnées ci-dessous. Chaque montant compte.",
    },
    "Beneficiary": {
        "de": "Empfänger",
        "fr": "Bénéficiaire",
    },
    "Reference": {
        "de": "Verwendungszweck",
        "fr": "Communication",
    },
    "Please keep the reference so we can match your transfer and add your Community Supporter badge. Transfers can take a few business days to arrive.": {
        "de": "Bitte übernimm den Verwendungszweck, damit wir deine Überweisung zuordnen und dir das Community-Unterstützer-Abzeichen geben können. Eine Überweisung kann ein paar Werktage dauern.",
        "fr": "Veuillez conserver la communication afin que nous puissions identifier votre virement et vous attribuer le badge « Soutien de la communauté ». Un virement peut prendre quelques jours ouvrables.",
    },
    "Copy": {
        "de": "Kopieren",
        "fr": "Copier",
    },
    "Copy %(label)s": {
        "de": "%(label)s kopieren",
        "fr": "Copier : %(label)s",
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
