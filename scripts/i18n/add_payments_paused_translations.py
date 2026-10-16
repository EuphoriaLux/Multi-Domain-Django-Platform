"""One-off: DE/FR for the "online payments paused / pay at the door" strings.

Run with: .venv/Scripts/python.exe scripts/i18n/add_payments_paused_translations.py
Appends missing entries to the crush_lu django.po catalogs (DE informal "du",
FR formal "vous", matching the neighbouring payment strings). The .mo files
are built by scripts/build_assets.py.
"""

import polib

NEW = {
    "Pay €%(amount)s in cash at the door.": {
        "de": "Bezahle %(amount)s € in bar an der Tür.",
        "fr": "Payez %(amount)s € en espèces à l'entrée.",
    },
    "Pay €%(amount)s in cash at the door": {
        "de": "Bezahle %(amount)s € in bar an der Tür",
        "fr": "Payez %(amount)s € en espèces à l'entrée",
    },
    "Online card payment is paused for now. Your seat is held: bring your ticket and pay on arrival.": {
        "de": "Die Online-Kartenzahlung ist vorübergehend ausgesetzt. Dein Platz ist reserviert: Bring dein Ticket mit und bezahle bei der Ankunft.",
        "fr": "Le paiement par carte en ligne est suspendu pour le moment. Votre place est réservée : apportez votre billet et payez à votre arrivée.",
    },
    "Your seat is held. Pay €%(amount)s in cash at the door.": {
        "de": "Dein Platz ist reserviert. Bezahle %(amount)s € in bar an der Tür.",
        "fr": "Votre place est réservée. Payez %(amount)s € en espèces à l'entrée.",
    },
    "Online contributions are paused for now. Thank you for your support!": {
        "de": "Online-Beiträge sind vorübergehend ausgesetzt. Danke für deine Unterstützung!",
        "fr": "Les contributions en ligne sont suspendues pour le moment. Merci pour votre soutien !",
    },
    "Online card payments are paused for now. For events, please pay at the door.": {
        "de": "Online-Kartenzahlungen sind vorübergehend ausgesetzt. Bei Events bezahlst du bitte an der Tür.",
        "fr": "Les paiements par carte en ligne sont suspendus pour le moment. Pour les événements, veuillez payer à l'entrée.",
    },
    "Your spot is reserved - pay at the door - {title}": {
        "de": "Dein Platz ist reserviert – Bezahlung an der Tür – {title}",
        "fr": "Votre place est réservée – paiement à l'entrée – {title}",
    },
    "Your place is held for you. Online card payment is paused for now, so please pay %(amount)s EUR in cash at the door when you arrive.": {
        "de": "Dein Platz wird für dich freigehalten. Die Online-Kartenzahlung ist vorübergehend ausgesetzt, bitte bezahle die %(amount)s EUR bei deiner Ankunft in bar an der Tür.",
        "fr": "Votre place vous est réservée. Le paiement par carte en ligne est suspendu pour le moment : veuillez régler %(amount)s EUR en espèces à l'entrée, à votre arrivée.",
    },
    "Your spot is reserved. Pay in cash at the door when you arrive.": {
        "de": "Dein Platz ist reserviert. Bezahle bei deiner Ankunft in bar an der Tür.",
        "fr": "Votre place est réservée. Payez en espèces à l'entrée, à votre arrivée.",
    },
    "If someone cancels, we hold the spot for you and email you straight away. You then pay %(fee)s EUR in cash at the door.": {
        "de": "Wenn jemand absagt, halten wir den Platz für dich frei und schreiben dir sofort. Du bezahlst dann %(fee)s EUR in bar an der Tür.",
        "fr": "Si quelqu'un annule, nous vous réservons la place et vous écrivons aussitôt. Vous payez alors %(fee)s EUR en espèces à l'entrée.",
    },
    "Pay €%(amount)s in cash at the door when you arrive. We've emailed you the details.": {
        "de": "Bezahle %(amount)s € bei deiner Ankunft in bar an der Tür. Wir haben dir die Details per E-Mail geschickt.",
        "fr": "Payez %(amount)s € en espèces à l'entrée, à votre arrivée. Nous vous avons envoyé les détails par e-mail.",
    },
    "Your spot is reserved! Please pay in cash at the door when you arrive.": {
        "de": "Dein Platz ist reserviert! Bitte bezahle bei deiner Ankunft in bar an der Tür.",
        "fr": "Votre place est réservée ! Veuillez payer en espèces à l'entrée, à votre arrivée.",
    },
    "You were on the waitlist and your spot is reserved. Please pay in cash at the door.": {
        "de": "Du warst auf der Warteliste und dein Platz ist reserviert. Bitte bezahle in bar an der Tür.",
        "fr": "Vous étiez sur la liste d'attente et votre place est réservée. Veuillez payer en espèces à l'entrée.",
    },
    "Bring your ticket (QR code) and the amount in cash": {
        "de": "Bring dein Ticket (QR-Code) und den Betrag in bar mit",
        "fr": "Apportez votre billet (code QR) et le montant en espèces",
    },
    "Our team confirms your payment at check-in": {
        "de": "Unser Team bestätigt deine Zahlung beim Check-in",
        "fr": "Notre équipe confirme votre paiement lors de l'accueil",
    },
    "If you can no longer come, please cancel your registration so someone on the waitlist can take your place.": {
        "de": "Falls du nicht mehr kommen kannst, storniere bitte deine Anmeldung, damit jemand von der Warteliste deinen Platz bekommt.",
        "fr": "Si vous ne pouvez plus venir, veuillez annuler votre inscription afin qu'une personne de la liste d'attente puisse prendre votre place.",
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
