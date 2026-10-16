"""One-off: DE/FR for the "Are you still coming?" attendance-check strings.

Run with: .venv/Scripts/python.exe scripts/i18n/add_attendance_check_translations.py
Appends missing entries to the crush_lu django.po catalogs as text (never
po.save(): polib re-wraps the whole catalogue). DE informal "du", FR formal
"vous". Build the .mo files with scripts/build_assets.py.
"""

import polib

NEW = {
    "Are you still coming to {title}?": {
        "de": "Kommst du noch zu {title}?",
        "fr": "Venez-vous toujours à {title} ?",
    },
    "Are you still coming?": {
        "de": "Kommst du noch?",
        "fr": "Venez-vous toujours ?",
    },
    "Your seat for <strong>%(title)s</strong> is coming up soon. Please let us know if you are still coming.": {
        "de": "Dein Platz für <strong>%(title)s</strong> rückt näher. Bitte sag uns, ob du noch kommst.",
        "fr": "Votre place pour <strong>%(title)s</strong> approche. Merci de nous dire si vous venez toujours.",
    },
    "Please confirm in the Crush.lu app or on crush.lu: open the event and tap the confirm button. Releasing your seat works the same way.": {
        "de": "Bitte bestätige in der Crush.lu-App oder auf crush.lu: Öffne das Event und tippe auf den Bestätigungsbutton. Deinen Platz freigeben kannst du dort genauso.",
        "fr": "Veuillez confirmer dans l'application Crush.lu ou sur crush.lu : ouvrez l'événement et appuyez sur le bouton de confirmation. Vous pouvez libérer votre place de la même façon.",
    },
    "Confirm in the app": {
        "de": "In der App bestätigen",
        "fr": "Confirmer dans l'application",
    },
    "Yes, I'm coming": {
        "de": "Ja, ich komme",
        "fr": "Oui, je viens",
    },
    "I can't come — release my seat": {
        "de": "Ich kann nicht kommen – Platz freigeben",
        "fr": "Je ne peux pas venir – libérer ma place",
    },
    "If you can't make it, releasing your seat lets someone on the waitlist take your place. Thank you!": {
        "de": "Wenn du nicht kommen kannst, kann jemand von der Warteliste deinen Platz bekommen, sobald du ihn freigibst. Danke!",
        "fr": "Si vous ne pouvez pas venir, libérer votre place permet à une personne de la liste d'attente de la prendre. Merci !",
    },
    "Attendance": {
        "de": "Teilnahme",
        "fr": "Participation",
    },
    "Thanks, see you there!": {
        "de": "Danke, bis dann!",
        "fr": "Merci, à bientôt !",
    },
    "We have noted that you are coming to %(title)s.": {
        "de": "Wir haben notiert, dass du zu %(title)s kommst.",
        "fr": "Nous avons noté que vous venez à %(title)s.",
    },
    "This link is no longer valid": {
        "de": "Dieser Link ist nicht mehr gültig",
        "fr": "Ce lien n'est plus valide",
    },
    "The link has expired, or the event is over or your registration was cancelled.": {
        "de": "Der Link ist abgelaufen, das Event ist vorbei oder deine Anmeldung wurde storniert.",
        "fr": "Le lien a expiré, l'événement est terminé ou votre inscription a été annulée.",
    },
    "Browse events": {
        "de": "Events entdecken",
        "fr": "Découvrir les événements",
    },
    "Coming ✓": {
        "de": "Kommt ✓",
        "fr": "Vient ✓",
    },
    "Asked, no answer": {
        "de": "Gefragt, keine Antwort",
        "fr": "Demandé, sans réponse",
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
