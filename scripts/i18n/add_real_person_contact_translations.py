"""Add the real-person check contact-request strings (EN, DE, FR).

Member copy: DE "du", FR "vous". Coach copy: DE "du", FR "tu".
Appends only missing entries: polib.save() would rewrap the whole catalogue.

Run: python scripts/i18n/add_real_person_contact_translations.py
     python scripts/build_assets.py --no-npm
"""

from pathlib import Path

import polib

# (msgid / EN, DE, FR)
STRINGS = [
    # --- choices and time windows (member form, coach page)
    ("Phone call", "Anruf", "Appel téléphonique"),
    ("SMS first", "Zuerst per SMS", "D'abord par SMS"),
    ("WhatsApp first", "Zuerst per WhatsApp", "D'abord par WhatsApp"),
    ("Weekday mornings", "Werktags vormittags", "En semaine, le matin"),
    ("Weekday lunchtime", "Werktags mittags", "En semaine, à midi"),
    ("Weekday evenings", "Werktags abends", "En semaine, le soir"),
    ("Weekend mornings", "Am Wochenende vormittags", "Le week-end, le matin"),
    (
        "Weekend afternoons",
        "Am Wochenende nachmittags",
        "Le week-end, l'après-midi",
    ),
    ("Weekend evenings", "Am Wochenende abends", "Le week-end, le soir"),
    # --- in-app notices
    (
        "%(coach)s, a Crush coach, would like a short call",
        "%(coach)s, ein Crush Coach, möchte kurz mit dir telefonieren",
        "%(coach)s, coach Crush, souhaite un court appel",
    ),
    (
        "Tell them how and when to reach you.",
        "Sag, wie und wann du am besten erreichbar bist.",
        "Indiquez comment et quand vous joindre.",
    ),
    (
        "A member answered your contact request",
        "Ein Mitglied hat auf deine Kontaktanfrage geantwortet",
        "Un membre a répondu à ta demande de contact",
    ),
    (
        "Open the check to see how and when to reach them.",
        "Öffne die Prüfung, um zu sehen, wie und wann du die Person erreichst.",
        "Ouvre la vérification pour voir comment et quand joindre cette personne.",
    ),
    # --- email
    (
        "%(coach)s, your Crush coach, would like a short call",
        "%(coach)s, dein Crush Coach, möchte kurz mit dir telefonieren",
        "%(coach)s, votre coach Crush, souhaite un court appel",
    ),
    (
        "Your Crush coach would like a short call",
        "Dein Crush Coach möchte kurz mit dir telefonieren",
        "Votre coach Crush souhaite un court appel",
    ),
    (
        "<strong>%(name)s</strong> is the Crush coach for the video verification you requested.",
        "<strong>%(name)s</strong> ist der Crush Coach für die Videoverifizierung, die du angefragt hast.",
        "<strong>%(name)s</strong> est le coach Crush chargé de la vérification vidéo que vous avez demandée.",
    ),
    (
        "<strong>%(name)s</strong>, a Crush coach, would like a short call with you to confirm that you are a real person. We do this for Crush Connect members no coach has met at an event yet.",
        "<strong>%(name)s</strong>, ein Crush Coach, möchte kurz mit dir telefonieren, um zu bestätigen, dass du eine echte Person bist. Das machen wir bei Crush-Connect-Mitgliedern, die noch kein Coach bei einem Event getroffen hat.",
        "<strong>%(name)s</strong>, coach Crush, souhaite un court appel avec vous pour confirmer que vous êtes une personne réelle. Nous le faisons pour les membres de Crush Connect qu'aucun coach n'a encore rencontrés lors d'un événement.",
    ),
    (
        "Tell %(name)s how you would like to be contacted first (phone call, SMS or WhatsApp) and when you are usually free. They will then agree a time with you.",
        "Sag %(name)s, wie du zuerst kontaktiert werden möchtest (Anruf, SMS oder WhatsApp) und wann du meistens Zeit hast. Danach vereinbart ihr gemeinsam einen Termin.",
        "Indiquez à %(name)s comment vous souhaitez être contacté(e) en premier (appel, SMS ou WhatsApp) et quand vous êtes généralement disponible. Vous conviendrez ensuite d'un horaire ensemble.",
    ),
    (
        "Choose how to be reached",
        "Kontaktweg wählen",
        "Choisir comment être joint(e)",
    ),
    ("What the call is for", "Wozu der Anruf dient", "À quoi sert l'appel"),
    (
        "A coach video approval completes human verification for Connect Week. Nothing is recorded.",
        "Die Videofreigabe durch einen Coach schließt die persönliche Verifizierung für die Connect Week ab. Es wird nichts aufgezeichnet.",
        "L'approbation vidéo par un coach complète la vérification humaine pour la Connect Week. Rien n'est enregistré.",
    ),
    ("It is optional", "Es ist freiwillig", "C'est facultatif"),
    (
        "You can ignore this email and nothing changes. If the call goes well, your Connect card gets a badge that tells other members a coach confirmed you are a real person. Nothing is recorded.",
        "Du kannst diese E-Mail ignorieren, dann ändert sich nichts. Wenn der Anruf gut läuft, bekommt deine Connect-Karte ein Abzeichen, das anderen Mitgliedern zeigt, dass ein Coach dich als echte Person bestätigt hat. Es wird nichts aufgezeichnet.",
        "Vous pouvez ignorer cet e-mail, rien ne changera. Si l'appel se passe bien, votre carte Connect reçoit un badge indiquant aux autres membres qu'un coach a confirmé que vous êtes une personne réelle. Rien n'est enregistré.",
    ),
    (
        "You can ignore this email and nothing changes. If the call goes well, your Connect card gets a badge as soon as badges are shown to other members. Nothing is recorded.",
        "Du kannst diese E-Mail ignorieren, dann ändert sich nichts. Wenn der Anruf gut läuft, bekommt deine Connect-Karte ein Abzeichen, sobald Abzeichen für andere Mitglieder sichtbar sind. Es wird nichts aufgezeichnet.",
        "Vous pouvez ignorer cet e-mail, rien ne changera. Si l'appel se passe bien, votre carte Connect recevra un badge dès que les badges seront visibles par les autres membres. Rien n'est enregistré.",
    ),
    (
        "You're receiving this because a Crush coach would like to contact you about your Crush Connect profile.",
        "Du erhältst diese E-Mail, weil ein Crush Coach dich wegen deines Crush-Connect-Profils kontaktieren möchte.",
        "Vous recevez cet e-mail parce qu'un coach Crush souhaite vous contacter au sujet de votre profil Crush Connect.",
    ),
    # --- member page
    (
        "How can your coach reach you?",
        "Wie kann dein Coach dich erreichen?",
        "Comment votre coach peut-il vous joindre ?",
    ),
    ("Your coach", "Dein Coach", "Votre coach"),
    (
        "How can %(name)s reach you?",
        "Wie kann %(name)s dich erreichen?",
        "Comment %(name)s peut vous joindre ?",
    ),
    (
        "%(name)s is the Crush coach for your video verification. Tell them how to contact you and when you are usually free, and they will agree a time with you.",
        "%(name)s ist der Crush Coach für deine Videoverifizierung. Sag, wie du kontaktiert werden möchtest und wann du meistens Zeit hast, dann vereinbart ihr einen Termin.",
        "%(name)s est le coach Crush chargé de votre vérification vidéo. Indiquez comment vous contacter et quand vous êtes généralement disponible ; vous conviendrez ensuite d'un horaire.",
    ),
    (
        "%(name)s, a Crush coach, would like a short call to confirm that you are a real person. It is optional. If you would like it, tell them how to contact you and when you are usually free.",
        "%(name)s, ein Crush Coach, möchte kurz mit dir telefonieren, um zu bestätigen, dass du eine echte Person bist. Das ist freiwillig. Wenn du möchtest, sag, wie du kontaktiert werden möchtest und wann du meistens Zeit hast.",
        "%(name)s, coach Crush, souhaite un court appel pour confirmer que vous êtes une personne réelle. C'est facultatif. Si vous le souhaitez, indiquez comment vous contacter et quand vous êtes généralement disponible.",
    ),
    (
        "Saved. %(name)s can see your answer. You can change it below.",
        "Gespeichert. %(name)s kann deine Antwort sehen. Du kannst sie unten ändern.",
        "Enregistré. %(name)s peut voir votre réponse. Vous pouvez la modifier ci-dessous.",
    ),
    (
        "Choose how you would like to be contacted first.",
        "Wähle, wie du zuerst kontaktiert werden möchtest.",
        "Choisissez comment vous souhaitez être contacté(e) en premier.",
    ),
    ("Contact me first by", "Kontaktiere mich zuerst per", "Contactez-moi d'abord par"),
    ("I am usually free", "Ich habe meistens Zeit", "Je suis généralement disponible"),
    (
        "Tick any that fit. This is a rough guide, not a booking.",
        "Wähle alles, was passt. Das ist nur eine grobe Angabe, keine Buchung.",
        "Cochez ce qui convient. C'est une indication, pas une réservation.",
    ),
    ("Update my answer", "Antwort aktualisieren", "Mettre à jour ma réponse"),
    ("Send to my coach", "An meinen Coach senden", "Envoyer à mon coach"),
    (
        "Your coach contacts you on the phone number in your profile. Only your coach sees this answer.",
        "Dein Coach kontaktiert dich unter der Telefonnummer in deinem Profil. Nur dein Coach sieht diese Antwort.",
        "Votre coach vous contacte au numéro de téléphone de votre profil. Seul votre coach voit cette réponse.",
    ),
    # --- coach page
    (
        "%(name)s prefers: %(method)s",
        "%(name)s bevorzugt: %(method)s",
        "%(name)s préfère : %(method)s",
    ),
    ("Usually free:", "Meistens Zeit:", "Généralement disponible :"),
    (
        "No usual times given.",
        "Keine üblichen Zeiten angegeben.",
        "Aucun horaire habituel indiqué.",
    ),
    ("Answered %(d)s ago.", "Vor %(d)s beantwortet.", "Répondu il y a %(d)s."),
    (
        "%(name)s was asked by email %(d)s ago how to be reached.",
        "%(name)s wurde vor %(d)s per E-Mail gefragt, wie die Kontaktaufnahme erfolgen soll.",
        "%(name)s a reçu il y a %(d)s un e-mail demandant comment être joint(e).",
    ),
    (
        "No answer yet. If nothing arrives within 48 hours, send the SMS below.",
        "Noch keine Antwort. Wenn innerhalb von 48 Stunden nichts kommt, schick die SMS unten.",
        "Pas encore de réponse. Si rien n'arrive sous 48 heures, envoie le SMS ci-dessous.",
    ),
    (
        "No email reached %(name)s.",
        "%(name)s hat keine E-Mail erhalten.",
        "Aucun e-mail n'a atteint %(name)s.",
    ),
    (
        "They only have an in-app notice, so contact them directly with the SMS below.",
        "Es gibt nur eine Mitteilung in der App, kontaktiere die Person also direkt mit der SMS unten.",
        "Il n'y a qu'une notification dans l'application : contacte cette personne directement avec le SMS ci-dessous.",
    ),
]


def main():
    root = Path(__file__).resolve().parents[2]
    for language, index in (("en", 0), ("de", 1), ("fr", 2)):
        path = root / "crush_lu" / "locale" / language / "LC_MESSAGES/django.po"
        source = path.read_text(encoding="utf-8")
        po = polib.pofile(path)
        additions = []
        for row in STRINGS:
            entry = po.find(row[0])
            if entry is None:
                additions.append(
                    str(polib.POEntry(msgid=row[0], msgstr=row[index])).strip()
                )
            elif not entry.msgstr or "fuzzy" in entry.flags:
                print(f"  {language}: existing entry needs a look: {row[0][:50]!r}")
        if additions:
            result = source.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
            path.write_text(result, encoding="utf-8", newline="\n")
        print(f"{language}: {len(additions)} of {len(STRINGS)} strings added")


if __name__ == "__main__":
    main()
