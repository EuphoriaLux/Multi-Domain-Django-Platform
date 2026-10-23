"""One-off: DE/FR for the coach WhatsApp inbox strings.

Run with: .venv/Scripts/python.exe scripts/i18n/add_coach_whatsapp_translations.py
Appends missing entries to the crush_lu django.po catalogs (DE informal "du",
FR formal "vous", matching the neighbouring coach-panel strings). The .mo
files are built by scripts/build_assets.py.
"""

import polib

NEW = {
    # crush_lu/models/profiles.py (admin help text)
    "Meta phone number ID of this coach's WhatsApp Cloud API number. Set it once the number is registered in WhatsApp Manager; leave blank while the coach still uses the WhatsApp app.": {
        "de": "Meta-Telefonnummer-ID der WhatsApp-Cloud-API-Nummer dieses Coaches. Trage sie ein, sobald die Nummer im WhatsApp Manager registriert ist; lass das Feld leer, solange der Coach noch die WhatsApp-App nutzt.",
        "fr": "Identifiant Meta du numéro WhatsApp Cloud API de ce coach. Renseignez-le une fois le numéro enregistré dans WhatsApp Manager ; laissez vide tant que le coach utilise encore l'application WhatsApp.",
    },
    # crush_lu/services/coach_whatsapp.py
    ("whatsapp message", "Photo"): {"de": "Foto", "fr": "Photo"},
    "Voice message": {"de": "Sprachnachricht", "fr": "Message vocal"},
    ("whatsapp message", "Audio"): {"de": "Audio", "fr": "Audio"},
    ("whatsapp message", "Video"): {"de": "Video", "fr": "Vidéo"},
    ("whatsapp message", "Document"): {"de": "Dokument", "fr": "Document"},
    ("whatsapp message", "Sticker"): {"de": "Sticker", "fr": "Autocollant"},
    ("whatsapp message", "Location"): {"de": "Standort", "fr": "Position"},
    "Contact card": {"de": "Kontaktkarte", "fr": "Fiche de contact"},
    "Reacted %(emoji)s": {"de": "Reagiert mit %(emoji)s", "fr": "A réagi %(emoji)s"},
    "Unsupported message": {
        "de": "Nicht unterstützte Nachricht",
        "fr": "Message non pris en charge",
    },
    "Write a message first.": {
        "de": "Schreib zuerst eine Nachricht.",
        "fr": "Écrivez d'abord un message.",
    },
    "Messages can be at most %(max)s characters.": {
        "de": "Nachrichten dürfen höchstens %(max)s Zeichen lang sein.",
        "fr": "Les messages peuvent contenir au maximum %(max)s caractères.",
    },
    "More than 24 hours have passed since this member's last message. WhatsApp only lets you reply once they write again.": {
        "de": "Die letzte Nachricht dieses Mitglieds ist mehr als 24 Stunden her. WhatsApp lässt dich erst antworten, wenn es wieder schreibt.",
        "fr": "Plus de 24 heures se sont écoulées depuis le dernier message de ce membre. WhatsApp ne vous permet de répondre que lorsqu'il vous écrit à nouveau.",
    },
    "WhatsApp isn't configured on the server.": {
        "de": "WhatsApp ist auf dem Server nicht eingerichtet.",
        "fr": "WhatsApp n'est pas configuré sur le serveur.",
    },
    "New WhatsApp message": {
        "de": "Neue WhatsApp-Nachricht",
        "fr": "Nouveau message WhatsApp",
    },
    "%(name)s wrote to you on WhatsApp.": {
        "de": "%(name)s hat dir auf WhatsApp geschrieben.",
        "fr": "%(name)s vous a écrit sur WhatsApp.",
    },
    # crush_lu/views_coach_whatsapp.py
    "WhatsApp refused the reply: more than 24 hours have passed since this member's last message.": {
        "de": "WhatsApp hat die Antwort abgelehnt: Die letzte Nachricht dieses Mitglieds ist mehr als 24 Stunden her.",
        "fr": "WhatsApp a refusé la réponse : plus de 24 heures se sont écoulées depuis le dernier message de ce membre.",
    },
    "WhatsApp didn't accept the message. Please try again.": {
        "de": "WhatsApp hat die Nachricht nicht angenommen. Bitte versuch es noch einmal.",
        "fr": "WhatsApp n'a pas accepté le message. Veuillez réessayer.",
    },
    "This file is no longer available on WhatsApp.": {
        "de": "Diese Datei ist auf WhatsApp nicht mehr verfügbar.",
        "fr": "Ce fichier n'est plus disponible sur WhatsApp.",
    },
    # Templates: coach_whatsapp_inbox.html, coach_whatsapp_thread.html,
    # _coach_whatsapp_message.html, _coach_whatsapp_messages_list.html, nav
    "WhatsApp": {"de": "WhatsApp", "fr": "WhatsApp"},
    "Messages members sent to %(name)s's WhatsApp number. You can read them here; only the coach can reply.": {
        "de": "Nachrichten, die Mitglieder an die WhatsApp-Nummer von %(name)s geschickt haben. Du kannst sie hier lesen; antworten kann nur der Coach.",
        "fr": "Messages envoyés par des membres au numéro WhatsApp de %(name)s. Vous pouvez les lire ici ; seul le coach peut répondre.",
    },
    "Messages members sent to your WhatsApp number. You can reply for 24 hours after a member's last message.": {
        "de": "Nachrichten, die Mitglieder an deine WhatsApp-Nummer geschickt haben. Du kannst bis 24 Stunden nach der letzten Nachricht eines Mitglieds antworten.",
        "fr": "Messages envoyés par des membres à votre numéro WhatsApp. Vous pouvez répondre pendant 24 heures après le dernier message d'un membre.",
    },
    ("coach whatsapp inbox", "Show"): {"de": "Anzeigen", "fr": "Afficher"},
    "Your WhatsApp number isn't connected to crush.lu yet.": {
        "de": "Deine WhatsApp-Nummer ist noch nicht mit crush.lu verbunden.",
        "fr": "Votre numéro WhatsApp n'est pas encore relié à crush.lu.",
    },
    "Members' messages keep arriving in the WhatsApp app on your phone until an administrator connects your number.": {
        "de": "Nachrichten von Mitgliedern kommen weiter in der WhatsApp-App auf deinem Handy an, bis ein Administrator deine Nummer verbindet.",
        "fr": "Les messages des membres continuent d'arriver dans l'application WhatsApp de votre téléphone jusqu'à ce qu'un administrateur relie votre numéro.",
    },
    "No WhatsApp messages yet.": {
        "de": "Noch keine WhatsApp-Nachrichten.",
        "fr": "Pas encore de messages WhatsApp.",
    },
    "You:": {"de": "Du:", "fr": "Vous :"},
    "Not a member": {"de": "Kein Mitglied", "fr": "Pas membre"},
    "Reply window closed": {
        "de": "Antwortfenster geschlossen",
        "fr": "Délai de réponse dépassé",
    },
    "Photo from the member": {
        "de": "Foto des Mitglieds",
        "fr": "Photo du membre",
    },
    "Download file": {"de": "Datei herunterladen", "fr": "Télécharger le fichier"},
    ("whatsapp status", "Read"): {"de": "Gelesen", "fr": "Lu"},
    ("whatsapp status", "Delivered"): {"de": "Zugestellt", "fr": "Distribué"},
    ("whatsapp status", "Not delivered"): {
        "de": "Nicht zugestellt",
        "fr": "Non distribué",
    },
    ("whatsapp status", "Sent"): {"de": "Gesendet", "fr": "Envoyé"},
    "Back to WhatsApp": {"de": "Zurück zu WhatsApp", "fr": "Retour à WhatsApp"},
    "Read-only: only the coach can reply from this number.": {
        "de": "Nur lesen: Nur der Coach kann von dieser Nummer antworten.",
        "fr": "Lecture seule : seul le coach peut répondre depuis ce numéro.",
    },
    "Reply on WhatsApp": {
        "de": "Auf WhatsApp antworten",
        "fr": "Répondre sur WhatsApp",
    },
    "Reply on WhatsApp...": {
        "de": "Auf WhatsApp antworten …",
        "fr": "Répondre sur WhatsApp…",
    },
    "Sent from your Crush WhatsApp number.": {
        "de": "Wird von deiner Crush-WhatsApp-Nummer gesendet.",
        "fr": "Envoyé depuis votre numéro WhatsApp Crush.",
    },
    "More than 24 hours have passed since this member's last message, so WhatsApp doesn't allow a free reply. You can answer as soon as they write again.": {
        "de": "Die letzte Nachricht dieses Mitglieds ist mehr als 24 Stunden her, deshalb erlaubt WhatsApp keine freie Antwort. Du kannst antworten, sobald es wieder schreibt.",
        "fr": "Plus de 24 heures se sont écoulées depuis le dernier message de ce membre : WhatsApp n'autorise donc pas de réponse libre. Vous pourrez répondre dès qu'il vous écrira à nouveau.",
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
    for key, translations in NEW.items():
        # A (context, msgid) key is a pgettext / {% trans ... context %} entry:
        # one-word msgids get one, so they never re-translate another app's.
        context, msgid = key if isinstance(key, tuple) else (None, key)
        if po.find(msgid, msgctxt=context) is not None:
            continue
        flag = "#, python-format\n" if "%(" in msgid else ""
        ctx = f"msgctxt {_quote(context)}\n" if context else ""
        blocks.append(
            f"{flag}{ctx}msgid {_quote(msgid)}\n"
            f"msgstr {_quote(translations[lang])}\n"
        )
    if blocks:
        # newline="\n": the catalogs are LF; text mode on Windows writes CRLF.
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n" + "\n".join(blocks))
    print(f"{lang}: {len(blocks)} added")
