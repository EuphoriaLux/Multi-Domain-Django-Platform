"""EN/DE/FR source catalogue entries for the January verification funnel.

Run with the project venv. Compile through scripts/build_assets.py.
"""

from pathlib import Path
import polib

STRINGS = [
    (
        "Video verification is currently unavailable",
        "Die Video-Verifizierung ist derzeit nicht verfügbar",
        "La vérification vidéo est actuellement indisponible",
    ),
    (
        "Your request is saved. You can still have your participation confirmed by a coach at a Crush event.",
        "Ihre Anfrage ist gespeichert. Sie können Ihre Teilnahme weiterhin bei einem Crush-Event von einem Coach bestätigen lassen.",
        "Votre demande est enregistrée. Vous pouvez toujours faire confirmer votre participation par un coach lors d’un événement Crush.",
    ),
    (
        "Video verification is currently unavailable. You can have your participation confirmed by a coach at a Crush event.",
        "Die Video-Verifizierung ist derzeit nicht verfügbar. Lassen Sie Ihre Teilnahme bei einem Crush-Event von einem Coach bestätigen.",
        "La vérification vidéo est actuellement indisponible. Vous pouvez faire confirmer votre participation par un coach lors d’un événement Crush.",
    ),
    (
        "Your Connect verification",
        "Ihre Connect-Verifizierung",
        "Votre vérification Connect",
    ),
    (
        "Your path to Connect Week",
        "Ihr Weg zur Connect-Woche",
        "Votre parcours vers Connect Week",
    ),
    (
        "Your account needs review",
        "Ihr Konto muss geprüft werden",
        "Votre compte doit être examiné",
    ),
    (
        "Contact the team for help with your verification.",
        "Wenden Sie sich an unser Team, wenn Sie Hilfe bei der Verifizierung benötigen.",
        "Contactez notre équipe pour obtenir de l’aide avec votre vérification.",
    ),
    ("Contact the team", "Team kontaktieren", "Contacter l’équipe"),
    (
        "Complete and submit your profile before arranging verification.",
        "Vervollständigen Sie Ihr Profil und reichen Sie es ein, bevor Sie die Verifizierung vereinbaren.",
        "Complétez et envoyez votre profil avant d’organiser votre vérification.",
    ),
    ("Complete my profile", "Mein Profil vervollständigen", "Compléter mon profil"),
    (
        "Confirm your identity",
        "Bestätigen Sie Ihre Identität",
        "Confirmez votre identité",
    ),
    (
        "Link LuxID before a coach video verification, or meet a coach at an event.",
        "Verknüpfen Sie LuxID vor der Video-Verifizierung oder treffen Sie einen Coach bei einem Event.",
        "Associez LuxID avant la vérification vidéo, ou rencontrez un coach lors d’un événement.",
    ),
    ("Link LuxID", "LuxID verknüpfen", "Associer LuxID"),
    (
        "Prepare your Connect photo",
        "Bereiten Sie Ihr Connect-Foto vor",
        "Préparez votre photo Connect",
    ),
    (
        "Add a clear main photo so a coach can compare it with you on video.",
        "Fügen Sie ein deutliches Hauptfoto hinzu, damit ein Coach es im Video mit Ihnen vergleichen kann.",
        "Ajoutez une photo principale nette pour qu’un coach puisse la comparer à votre visage en vidéo.",
    ),
    (
        "Your photo is awaiting coach review",
        "Ihr Foto wartet auf die Prüfung durch einen Coach",
        "Votre photo attend l’examen d’un coach",
    ),
    (
        "You can prepare your Connect card while a coach reviews your photo.",
        "Während ein Coach Ihr Foto prüft, können Sie Ihre Connect-Karte vorbereiten.",
        "Vous pouvez préparer votre fiche Connect pendant qu’un coach examine votre photo.",
    ),
    (
        "Prepare my Connect card",
        "Meine Connect-Karte vorbereiten",
        "Préparer ma fiche Connect",
    ),
    (
        "Your verification history is retained. Resume Connect when you are ready.",
        "Ihre Verifizierung bleibt gespeichert. Setzen Sie Connect fort, wenn Sie bereit sind.",
        "Votre historique de vérification est conservé. Reprenez Connect lorsque vous le souhaitez.",
    ),
    ("Manage Connect", "Connect verwalten", "Gérer Connect"),
    (
        "Coach verification complete; finish your setup",
        "Coach-Verifizierung abgeschlossen; Einrichtung vervollständigen",
        "Vérification par un coach terminée ; finalisez votre préparation",
    ),
    (
        "Connect Week is available",
        "Die Connect-Woche ist verfügbar",
        "Connect Week est disponible",
    ),
    (
        "Your verification and Connect card are ready.",
        "Ihre Verifizierung und Ihre Connect-Karte sind bereit.",
        "Votre vérification et votre fiche Connect sont prêtes.",
    ),
    ("Open Connect Week", "Connect-Woche öffnen", "Ouvrir Connect Week"),
    (
        "Coach verification complete",
        "Coach-Verifizierung abgeschlossen",
        "Vérification par un coach terminée",
    ),
    (
        "Your verification is saved. Connect Week will be available when this phase opens.",
        "Ihre Verifizierung ist gespeichert. Die Connect-Woche wird verfügbar, sobald diese Phase beginnt.",
        "Votre vérification est enregistrée. Connect Week sera disponible à l’ouverture de cette phase.",
    ),
    (
        "Video verification needs another attempt",
        "Die Video-Verifizierung benötigt einen weiteren Versuch",
        "Une nouvelle tentative de vérification vidéo est nécessaire",
    ),
    (
        "Arrange another video call with your coach, or choose an event.",
        "Vereinbaren Sie einen weiteren Videoanruf mit Ihrem Coach oder wählen Sie ein Event.",
        "Convenez d’un nouvel appel vidéo avec votre coach, ou choisissez un événement.",
    ),
    (
        "Your video verification is arranged",
        "Ihre Video-Verifizierung ist vereinbart",
        "Votre vérification vidéo est organisée",
    ),
    (
        "Join your coach at the agreed time. Keep your camera and microphone ready.",
        "Treffen Sie Ihren Coach zur vereinbarten Zeit. Halten Sie Kamera und Mikrofon bereit.",
        "Rejoignez votre coach à l’heure convenue. Préparez votre caméra et votre microphone.",
    ),
    (
        "Your video verification request is received",
        "Ihre Anfrage zur Video-Verifizierung ist eingegangen",
        "Votre demande de vérification vidéo a été reçue",
    ),
    (
        "A coach will contact you to agree a time. You can finish your Connect preparation meanwhile.",
        "Ein Coach meldet sich bei Ihnen, um einen Termin zu vereinbaren. In der Zwischenzeit können Sie Ihre Connect-Vorbereitung abschließen.",
        "Un coach vous contactera pour convenir d’un horaire. Vous pouvez terminer votre préparation Connect en attendant.",
    ),
    (
        "Join video verification",
        "Video-Verifizierung beitreten",
        "Rejoindre la vérification vidéo",
    ),
    (
        "Meet a coach to open Connect Week",
        "Treffen Sie einen Coach, um die Connect-Woche freizuschalten",
        "Rencontrez un coach pour accéder à Connect Week",
    ),
    (
        "Choose a coach video verification with LuxID, or have your participation confirmed at a Crush event. Both routes give the same Connect Week access once your card is ready.",
        "Wählen Sie eine Video-Verifizierung mit LuxID oder lassen Sie Ihre Teilnahme an einem Crush-Event bestätigen. Beide Wege eröffnen denselben Zugang zur Connect-Woche, sobald Ihre Karte bereit ist.",
        "Choisissez une vérification vidéo avec LuxID, ou faites confirmer votre participation à un événement Crush. Les deux parcours donnent le même accès à Connect Week une fois votre fiche prête.",
    ),
    (
        "Your video verification has an update",
        "Es gibt Neuigkeiten zu Ihrer Video-Verifizierung",
        "Votre vérification vidéo a été mise à jour",
    ),
    (
        "Open your verification page to see your next step.",
        "Öffnen Sie Ihre Verifizierungsseite, um Ihren nächsten Schritt zu sehen.",
        "Ouvrez votre page de vérification pour consulter votre prochaine étape.",
    ),
    ("Agreed appointment time", "Vereinbarter Termin", "Horaire convenu"),
    (
        "Enter the time agreed with the member, in Luxembourg time.",
        "Geben Sie die mit dem Mitglied vereinbarte Uhrzeit in Luxemburger Zeit ein.",
        "Indiquez l’horaire convenu avec le membre, à l’heure du Luxembourg.",
    ),
    ("Duration in minutes", "Dauer in Minuten", "Durée en minutes"),
    (
        "Your request could not be saved. Check your current next step below.",
        "Ihre Anfrage konnte nicht gespeichert werden. Prüfen Sie unten Ihren aktuellen nächsten Schritt.",
        "Votre demande n’a pas pu être enregistrée. Consultez votre prochaine étape ci-dessous.",
    ),
    (
        "Two routes, the same Connect Week",
        "Zwei Wege, dieselbe Connect-Woche",
        "Deux parcours, le même accès à Connect Week",
    ),
    (
        "Digital: submit your profile, link LuxID, then complete a live video verification with a coach.",
        "Digital: Profil einreichen, LuxID verknüpfen und dann eine Live-Video-Verifizierung mit einem Coach abschließen.",
        "En ligne : envoyez votre profil, associez LuxID, puis effectuez une vérification vidéo en direct avec un coach.",
    ),
    (
        "At an event: have your participation confirmed in person by a Crush coach.",
        "Bei einem Event: Lassen Sie Ihre Teilnahme persönlich von einem Crush-Coach bestätigen.",
        "Lors d’un événement : faites confirmer votre participation en personne par un coach Crush.",
    ),
    (
        "Your first photo needs coach approval before discovery. You can complete your Connect preferences, questions and consent alongside verification. Premium is a separate service.",
        "Ihr erstes Foto muss vor der Entdeckung von einem Coach freigegeben werden. Ihre Connect-Präferenzen, Fragen und Einwilligung können Sie parallel zur Verifizierung ausfüllen. Premium ist ein separater Service.",
        "Votre première photo doit être approuvée par un coach avant la découverte. Vous pouvez compléter vos préférences, questions et consentements Connect en parallèle. Premium est un service distinct.",
    ),
    (
        "Your verification coach: %(name)s",
        "Ihr Verifizierungscoach: %(name)s",
        "Votre coach de vérification : %(name)s",
    ),
    (
        "Request a video verification",
        "Video-Verifizierung anfragen",
        "Demander une vérification vidéo",
    ),
    (
        "Cancel my video request",
        "Meine Video-Anfrage stornieren",
        "Annuler ma demande vidéo",
    ),
    ("View my next step", "Meinen nächsten Schritt ansehen", "Voir ma prochaine étape"),
    (
        "Your video verifications",
        "Ihre Video-Verifizierungen",
        "Vos vérifications vidéo",
    ),
    ("Continue verification", "Verifizierung fortsetzen", "Continuer la vérification"),
    (
        "Connect Week video verification",
        "Video-Verifizierung für die Connect-Woche",
        "Vérification vidéo pour Connect Week",
    ),
    ("Claim video request", "Video-Anfrage übernehmen", "Prendre la demande vidéo"),
    ("Photo approved", "Foto freigegeben", "Photo approuvée"),
    (
        "A successful live video approval satisfies human verification for Connect Week. Photo publication, Connect setup and consent remain separate requirements.",
        "Eine erfolgreiche Live-Video-Freigabe erfüllt die menschliche Verifizierung für die Connect-Woche. Fotoveröffentlichung, Connect-Einrichtung und Einwilligung bleiben gesonderte Voraussetzungen.",
        "Une approbation vidéo en direct valide la vérification humaine pour Connect Week. La publication des photos, la préparation Connect et les consentements restent des conditions distinctes.",
    ),
    ("Cancel appointment", "Termin absagen", "Annuler le rendez-vous"),
    (
        "Save agreed appointment",
        "Vereinbarten Termin speichern",
        "Enregistrer le rendez-vous convenu",
    ),
    (
        "Join at the agreed time. Nothing is recorded. If video fails, arrange another attempt; a voice conversation does not approve Connect Week access.",
        "Treten Sie zur vereinbarten Zeit bei. Es wird nichts aufgezeichnet. Falls das Video ausfällt, vereinbaren Sie einen neuen Versuch; ein Sprachgespräch gewährt keinen Zugang zur Connect-Woche.",
        "Rejoignez l’appel à l’heure convenue. Rien n’est enregistré. En cas de problème vidéo, prévoyez une nouvelle tentative ; une conversation vocale ne valide pas l’accès à Connect Week.",
    ),
    (
        "Select Video call, then complete the checklist after speaking with the member.",
        "Wählen Sie Videoanruf und füllen Sie nach dem Gespräch mit dem Mitglied die Checkliste aus.",
        "Sélectionnez Appel vidéo, puis remplissez la liste après votre échange avec le membre.",
    ),
    (
        "Approve Connect Week video verification",
        "Video-Verifizierung für die Connect-Woche freigeben",
        "Approuver la vérification vidéo Connect Week",
    ),
    (
        "Confirm your live video decision. This grants human verification for Connect Week; the member must still complete their other requirements.",
        "Bestätigen Sie Ihre Live-Video-Entscheidung. Damit wird die menschliche Verifizierung für die Connect-Woche erteilt; das Mitglied muss die übrigen Voraussetzungen weiterhin erfüllen.",
        "Confirmez votre décision après la vidéo en direct. Elle valide la vérification humaine pour Connect Week ; le membre doit encore remplir les autres conditions.",
    ),
    (
        "I explained Connect Week access and the remaining preparation",
        "Ich habe den Zugang zur Connect-Woche und die verbleibende Vorbereitung erklärt",
        "J’ai expliqué l’accès à Connect Week et la préparation restante",
    ),
    (
        "A coach video approval completes human verification for Connect Week. You can also choose verification at an event.",
        "Eine Video-Freigabe durch einen Coach schließt die menschliche Verifizierung für die Connect-Woche ab. Sie können auch die Verifizierung bei einem Event wählen.",
        "L’approbation vidéo d’un coach valide la vérification humaine pour Connect Week. Vous pouvez aussi choisir une vérification lors d’un événement.",
    ),
    (
        "A successful call needs an explicit coach decision. If the camera or connection fails, arrange another attempt.",
        "Ein erfolgreicher Anruf erfordert eine ausdrückliche Coach-Entscheidung. Falls Kamera oder Verbindung ausfallen, vereinbaren Sie einen weiteren Versuch.",
        "Un appel réussi nécessite une décision explicite du coach. Si la caméra ou la connexion ne fonctionne pas, prévoyez une nouvelle tentative.",
    ),
    (
        "LuxID and coach video verification complete",
        "LuxID- und Coach-Video-Verifizierung abgeschlossen",
        "Vérifications LuxID et vidéo par un coach terminées",
    ),
    (
        "Your human verification for Connect Week is complete. Your photo, Connect setup and participation requirements still apply.",
        "Ihre menschliche Verifizierung für die Connect-Woche ist abgeschlossen. Die Voraussetzungen für Foto, Connect-Einrichtung und Teilnahme gelten weiterhin.",
        "Votre vérification humaine pour Connect Week est terminée. Les conditions relatives à votre photo, à votre préparation Connect et à votre participation restent applicables.",
    ),
    ("Coach verification", "Coach-Verifizierung", "Vérification par un coach"),
    (
        "Meet a coach at an event, or link LuxID and complete a coach video verification to open Connect Week.",
        "Treffen Sie einen Coach bei einem Event oder verknüpfen Sie LuxID und schließen Sie eine Video-Verifizierung ab, um die Connect-Woche freizuschalten.",
        "Rencontrez un coach lors d’un événement, ou associez LuxID et effectuez une vérification vidéo pour accéder à Connect Week.",
    ),
    (
        "Choose my verification route",
        "Meinen Verifizierungsweg wählen",
        "Choisir mon parcours de vérification",
    ),
    (
        "Video verification is not available for this member right now.",
        "Die Video-Verifizierung ist für dieses Mitglied derzeit nicht verfügbar.",
        "La vérification vidéo n’est pas disponible pour ce membre actuellement.",
    ),
    (
        "Connect Week verification requires a successful live video call. Arrange another attempt if video failed.",
        "Die Verifizierung für die Connect-Woche erfordert einen erfolgreichen Live-Videoanruf. Vereinbaren Sie bei einem Videoausfall einen weiteren Versuch.",
        "La vérification Connect Week nécessite un appel vidéo en direct réussi. Prévoyez une nouvelle tentative en cas de problème vidéo.",
    ),
    (
        "Choose an agreed future appointment within the next 30 days.",
        "Wählen Sie einen vereinbarten zukünftigen Termin innerhalb der nächsten 30 Tage.",
        "Choisissez un rendez-vous futur convenu dans les 30 prochains jours.",
    ),
    (
        "Cancel the existing appointment before arranging another.",
        "Sagen Sie den bestehenden Termin ab, bevor Sie einen neuen vereinbaren.",
        "Annulez le rendez-vous existant avant d’en organiser un autre.",
    ),
    (
        "This appointment overlaps another slot in your calendar.",
        "Dieser Termin überschneidet sich mit einem anderen Zeitfenster in Ihrem Kalender.",
        "Ce rendez-vous chevauche un autre créneau de votre calendrier.",
    ),
    (
        "Hello %(first_name)s, this is %(coach_name)s from Crush.lu. You requested a video verification for Connect Week. Let us agree a time to meet on video. You can check your request here: %(call_url)s",
        "Hallo %(first_name)s, hier ist %(coach_name)s von Crush.lu. Sie haben eine Video-Verifizierung für die Connect-Woche angefragt. Lassen Sie uns einen Termin für den Videoanruf vereinbaren. Ihre Anfrage finden Sie hier: %(call_url)s",
        "Bonjour %(first_name)s, c’est %(coach_name)s de Crush.lu. Vous avez demandé une vérification vidéo pour Connect Week. Convenons d’un horaire pour nous rencontrer en vidéo. Vous pouvez consulter votre demande ici : %(call_url)s",
    ),
]


def main():
    root = Path(__file__).resolve().parents[2]
    for language, index in (("en", 0), ("de", 1), ("fr", 2)):
        path = root / "crush_lu" / "locale" / language / "LC_MESSAGES/django.po"
        source = path.read_text(encoding="utf-8")
        po = polib.pofile(path)
        additions = []
        replacements = {}
        for row in STRINGS:
            entry = po.find(row[0])
            if entry is None:
                additions.append(
                    str(polib.POEntry(msgid=row[0], msgstr=row[index])).strip()
                )
            elif not entry.msgstr:
                entry.msgstr = row[index]
                entry.flags = [flag for flag in entry.flags if flag != "fuzzy"]
                replacements[entry.linenum] = str(entry).strip()
        # Re-serialize only changed blocks: polib.save() rewraps thousands of
        # unrelated entries and obscures the actual copy change in review.
        blocks = source.split("\n\n")
        line = 1
        for i, block in enumerate(blocks):
            next_line = line + block.count("\n") + 2
            for start, replacement in replacements.items():
                if line <= start < next_line:
                    blocks[i] = replacement
            line = next_line
        result = "\n\n".join(blocks)
        if additions:
            result = result.rstrip() + "\n\n" + "\n\n".join(additions) + "\n"
        path.write_text(result, encoding="utf-8", newline="\n")
        print(f"{language}: {len(STRINGS)} funnel strings checked")


if __name__ == "__main__":
    main()
