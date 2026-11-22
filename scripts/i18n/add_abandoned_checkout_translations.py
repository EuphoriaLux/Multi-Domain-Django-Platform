"""One-off: DE/FR for the abandoned-checkout reminder and released-seat credit.

Run with: .venv/Scripts/python.exe scripts/i18n/add_abandoned_checkout_translations.py

Strings from crush_lu/templates/crush_lu/emails/event_checkout_reminder.html,
event_released_seat_credit.html, their subjects in email_helpers.py and the
new model labels (EventRegistration.checkout_*, CrushCredit reason). Register
is DE du, FR vous, as in the other member emails (see
add_email_wording_translations.py). Appends only msgids not yet in the file;
build the .mo with scripts/build_assets.py --no-npm.
"""

import polib

NEW = {
    # --- event_checkout_reminder ------------------------------------------
    "Your seat for {title} is still waiting": {
        "de": "Dein Platz für {title} wartet noch auf dich",
        "fr": "Votre place pour {title} vous attend encore",
    },
    "Finish your payment": {
        "de": "Schließe deine Zahlung ab",
        "fr": "Finalisez votre paiement",
    },
    (
        "You started paying for your seat at <strong>%(title)s</strong>, but "
        "the payment was not completed."
    ): {
        "de": (
            "Du hast angefangen, deinen Platz für <strong>%(title)s</strong> zu "
            "bezahlen, aber die Zahlung wurde nicht abgeschlossen."
        ),
        "fr": (
            "Vous avez commencé à payer votre place pour "
            "<strong>%(title)s</strong>, mais le paiement n'a pas été finalisé."
        ),
    },
    (
        "We are holding your seat for about %(minutes)s more minutes. Finish "
        "your payment now to keep it."
    ): {
        "de": (
            "Wir halten deinen Platz noch etwa %(minutes)s Minuten für dich "
            "frei. Schließe jetzt deine Zahlung ab, damit er dir sicher ist."
        ),
        "fr": (
            "Nous gardons votre place encore environ %(minutes)s minutes. "
            "Finalisez votre paiement maintenant pour la conserver."
        ),
    },
    "Finish my payment": {
        "de": "Zahlung abschließen",
        "fr": "Finaliser mon paiement",
    },
    (
        "After that, the seat goes back to the pool so someone else can take "
        "it. If you changed your mind, you don't need to do anything."
    ): {
        "de": (
            "Danach wird der Platz wieder freigegeben, damit jemand anderes "
            "ihn nehmen kann. Wenn du es dir anders überlegt hast, musst du "
            "nichts tun."
        ),
        "fr": (
            "Passé ce délai, la place est remise en jeu pour que quelqu'un "
            "d'autre puisse la prendre. Si vous avez changé d'avis, vous "
            "n'avez rien à faire."
        ),
    },
    # --- event_released_seat_credit ---------------------------------------
    "Your payment for {title} is now Crush Credit": {
        "de": "Deine Zahlung für {title} ist jetzt Crush Credit",
        "fr": "Votre paiement pour {title} est désormais du Crush Credit",
    },
    "Your payment is now Crush Credit": {
        "de": "Deine Zahlung ist jetzt Crush Credit",
        "fr": "Votre paiement est désormais du Crush Credit",
    },
    (
        "Your payment for <strong>%(title)s</strong> arrived after we had "
        "released your unpaid seat, and the event filled up in the meantime."
    ): {
        "de": (
            "Deine Zahlung für <strong>%(title)s</strong> ist erst eingegangen, "
            "nachdem wir deinen unbezahlten Platz freigegeben hatten, und das "
            "Event ist inzwischen voll."
        ),
        "fr": (
            "Votre paiement pour <strong>%(title)s</strong> est arrivé après "
            "que nous avons libéré votre place non payée, et l'événement est "
            "complet entre-temps."
        ),
    },
    (
        "Would you rather have your money back? Reply to this email and we "
        "will refund it to the payment method you used."
    ): {
        "de": (
            "Hättest du lieber dein Geld zurück? Antworte auf diese E-Mail und "
            "wir erstatten es auf das Zahlungsmittel, das du verwendet hast."
        ),
        "fr": (
            "Vous préférez être remboursé ? Répondez à cet e-mail et nous vous "
            "rembourserons sur le moyen de paiement utilisé."
        ),
    },
    # --- model labels (admin is English, kept for completeness) -----------
    "Paid after the unpaid seat was released": {
        "de": "Bezahlt, nachdem der unbezahlte Platz freigegeben wurde",
        "fr": "Payé après la libération de la place non payée",
    },
    (
        "When the 'finish your payment' step ran for an abandoned online "
        "checkout (sent, or skipped for an unsubscribed member)"
    ): {
        "de": (
            "Wann der Schritt „Zahlung abschließen“ für einen abgebrochenen "
            "Online-Checkout lief (gesendet oder für ein abgemeldetes Mitglied "
            "übersprungen)"
        ),
        "fr": (
            "Quand l'étape « finalisez votre paiement » a été exécutée pour un "
            "paiement en ligne abandonné (envoyée, ou ignorée pour un membre "
            "désabonné)"
        ),
    },
    (
        "When this unpaid seat was released because its online checkout was "
        "abandoned. A payment that still lands afterwards re-confirms the "
        "seat if one is free, otherwise it becomes Crush Credit."
    ): {
        "de": (
            "Wann dieser unbezahlte Platz freigegeben wurde, weil der "
            "Online-Checkout abgebrochen wurde. Eine später noch eingehende "
            "Zahlung bestätigt den Platz erneut, wenn einer frei ist, sonst "
            "wird sie zu Crush Credit."
        ),
        "fr": (
            "Quand cette place non payée a été libérée parce que son paiement "
            "en ligne a été abandonné. Un paiement qui arrive encore ensuite "
            "reconfirme la place s'il en reste une, sinon il devient du Crush "
            "Credit."
        ),
    },
}


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
