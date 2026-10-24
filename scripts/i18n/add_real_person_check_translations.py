"""One-off: DE/FR for the Connect real-person check coach UI (PR 3) and
member surfaces (PR 4).

Run with: .venv/Scripts/python.exe scripts/i18n/add_real_person_check_translations.py
Appends missing entries to the crush_lu django.po catalogs; the .mo files are
built by scripts/build_assets.py (gettext is not installed here, so there is no
msgfmt or makemessages).

Registers (spec ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md):
* coach templates (queue, check page, outreach partial, nav, member card):
  DE "du", FR "tu";
* flash and guard messages from views_coach_real_person.py: formal, DE "Sie",
  FR "vous";
* the text message a coach sends a member: DE "du", FR "vous";
* checklist labels and model status labels address nobody;
* member surfaces (badge labels and tooltips, trust labels, hub note): DE "du",
  FR "vous".
Entries already in a catalog are left alone, so the script can run twice.
"""

import polib

NEW = {
    # ---- coach_real_person_queue.html (du / tu) ----
    "Real-person checks": {
        "de": "Echtheitsprüfungen",
        "fr": "Contrôles de personne réelle",
    },
    "Crush Connect members no coach has met. A short video or voice call confirms they are a real person and adds a badge to their card.": {
        "de": "Crush-Connect-Mitglieder, denen noch kein Coach begegnet ist. Ein kurzer Video- oder Sprachanruf bestätigt, dass sie echte Personen sind, und fügt ihrer Karte ein Abzeichen hinzu.",
        "fr": "Membres de Crush Connect qu'aucun coach n'a rencontrés. Un court appel vidéo ou vocal confirme qu'il s'agit de personnes réelles et ajoute un badge à leur carte.",
    },
    "My open checks": {
        "de": "Meine offenen Prüfungen",
        "fr": "Mes contrôles en cours",
    },
    "%(current)s of %(max)s": {
        "de": "%(current)s von %(max)s",
        "fr": "%(current)s sur %(max)s",
    },
    "Your claim ends in %(d)s": {
        "de": "Deine Übernahme endet in %(d)s",
        "fr": "Ta prise en charge se termine dans %(d)s",
    },
    "You have no open checks. Claim a member below to start.": {
        "de": "Du hast keine offenen Prüfungen. Übernimm unten ein Mitglied, um zu beginnen.",
        "fr": "Tu n'as aucun contrôle en cours. Prends un membre ci-dessous pour commencer.",
    },
    "Waiting for a call": {
        "de": "Warten auf einen Anruf",
        "fr": "En attente d'un appel",
    },
    "Showing %(first)s–%(last)s of %(total)s": {
        "de": "%(first)s–%(last)s von %(total)s angezeigt",
        "fr": "%(first)s–%(last)s sur %(total)s affichés",
    },
    "You hold %(max)s open checks. Release or finish one before claiming another.": {
        "de": "Du hast %(max)s offene Prüfungen. Gib eine frei oder schließe sie ab, bevor du eine weitere übernimmst.",
        "fr": "Tu as %(max)s contrôles en cours. Libère-en un ou termine-le avant d'en prendre un autre.",
    },
    "last login %(d)s ago": {
        "de": "letzter Login vor %(d)s",
        "fr": "dernière connexion il y a %(d)s",
    },
    "never logged in": {"de": "nie eingeloggt", "fr": "jamais connecté"},
    "Pages": {"de": "Seiten", "fr": "Pages"},
    "Page %(number)s of %(total)s": {
        "de": "Seite %(number)s von %(total)s",
        "fr": "Page %(number)s sur %(total)s",
    },
    "Nobody is waiting": {"de": "Niemand wartet", "fr": "Personne n'attend"},
    "No Connect members are waiting for a real-person check right now.": {
        "de": "Zurzeit wartet kein Connect-Mitglied auf eine Echtheitsprüfung.",
        "fr": "Aucun membre Connect n'attend de contrôle de personne réelle pour le moment.",
    },
    # ---- coach_real_person_check.html (du / tu) ----
    "All real-person checks": {
        "de": "Alle Echtheitsprüfungen",
        "fr": "Tous les contrôles de personne réelle",
    },
    "Real-person check": {
        "de": "Echtheitsprüfung",
        "fr": "Contrôle de personne réelle",
    },
    "This check is closed: %(status)s.": {
        "de": "Diese Prüfung ist abgeschlossen: %(status)s.",
        "fr": "Ce contrôle est clos : %(status)s.",
    },
    "Your claim on this member has lapsed. Claim them again from the list to continue.": {
        "de": "Deine Übernahme dieses Mitglieds ist abgelaufen. Übernimm es erneut aus der Liste, um fortzufahren.",
        "fr": "Ta prise en charge de ce membre a expiré. Reprends-le depuis la liste pour continuer.",
    },
    "Photo 1": {"de": "Foto 1", "fr": "Photo 1"},
    "The member's current main photo": {
        "de": "Das aktuelle Hauptfoto des Mitglieds",
        "fr": "La photo principale actuelle du membre",
    },
    "Current upload (what you see)": {
        "de": "Aktueller Upload (was du siehst)",
        "fr": "Téléversement actuel (ce que tu vois)",
    },
    "The main photo other members see": {
        "de": "Das Hauptfoto, das andere Mitglieder sehen",
        "fr": "La photo principale que voient les autres membres",
    },
    "What other members see": {
        "de": "Was andere Mitglieder sehen",
        "fr": "Ce que voient les autres membres",
    },
    "No coach has approved this photo yet, so other members see no photo. Only a voice check is possible until it is approved.": {
        "de": "Noch kein Coach hat dieses Foto freigegeben, deshalb sehen andere Mitglieder kein Foto. Bis zur Freigabe ist nur eine Sprachprüfung möglich.",
        "fr": "Aucun coach n'a encore approuvé cette photo : les autres membres ne voient donc aucune photo. Seul un contrôle vocal est possible tant qu'elle n'est pas approuvée.",
    },
    "Other members still see an earlier photo. Only a voice check is possible until the new one is approved.": {
        "de": "Andere Mitglieder sehen noch ein früheres Foto. Bis das neue freigegeben ist, ist nur eine Sprachprüfung möglich.",
        "fr": "Les autres membres voient encore une photo précédente. Seul un contrôle vocal est possible tant que la nouvelle n'est pas approuvée.",
    },
    "How did you speak with them?": {
        "de": "Wie hast du mit der Person gesprochen?",
        "fr": "Comment as-tu parlé avec cette personne ?",
    },
    "Video call": {"de": "Videoanruf", "fr": "Appel vidéo"},
    "Voice call": {"de": "Sprachanruf", "fr": "Appel vocal"},
    "Video is unavailable until a coach approves the member's current main photo.": {
        "de": "Video ist nicht verfügbar, bis ein Coach das aktuelle Hauptfoto des Mitglieds freigibt.",
        "fr": "La vidéo n'est pas disponible tant qu'un coach n'a pas approuvé la photo principale actuelle du membre.",
    },
    "Choose video or voice to open the checklist. The badge says which one you used.": {
        "de": "Wähle Video oder Sprache, um die Checkliste zu öffnen. Das Abzeichen zeigt, welche Variante du genutzt hast.",
        "fr": "Choisis vidéo ou voix pour ouvrir la liste de contrôle. Le badge indique lequel tu as utilisé.",
    },
    "Checklist": {"de": "Checkliste", "fr": "Liste de contrôle"},
    "Notes (only coaches see them)": {
        "de": "Notizen (nur Coaches sehen sie)",
        "fr": "Notes (seuls les coachs les voient)",
    },
    "Grant the video badge": {
        "de": "Video-Abzeichen vergeben",
        "fr": "Attribuer le badge vidéo",
    },
    "Grant the voice badge": {
        "de": "Sprach-Abzeichen vergeben",
        "fr": "Attribuer le badge vocal",
    },
    "Not a pass?": {"de": "Nicht bestanden?", "fr": "Pas concluant ?"},
    "If you could not reach them, why?": {
        "de": "Falls du die Person nicht erreicht hast: warum?",
        "fr": "Si tu n'as pas pu la joindre, pourquoi ?",
    },
    "Could not reach them": {"de": "Nicht erreicht", "fr": "Injoignable"},
    "They declined": {"de": "Hat abgelehnt", "fr": "A refusé"},
    "Looks fake": {"de": "Wirkt gefälscht", "fr": "Semble faux"},
    "Unreachable keeps them on the list. Declined leaves them alone for a while. Looks fake opens a report for an administrator; you exclude nobody yourself.": {
        "de": "„Nicht erreicht“ lässt das Mitglied auf der Liste. „Hat abgelehnt“ lässt es eine Weile in Ruhe. „Wirkt gefälscht“ öffnet eine Meldung für einen Administrator; du schließt selbst niemanden aus.",
        "fr": "« Injoignable » garde le membre sur la liste. « A refusé » le laisse tranquille un moment. « Semble faux » ouvre un signalement pour un administrateur ; tu n'exclus personne toi-même.",
    },
    "Confirm that you spoke with %(name)s live and ticked every item truthfully. Other members will see the badge.": {
        "de": "Bestätige, dass du live mit %(name)s gesprochen und jeden Punkt wahrheitsgemäß abgehakt hast. Andere Mitglieder sehen das Abzeichen.",
        "fr": "Confirme que tu as parlé en direct avec %(name)s et coché chaque point en toute honnêteté. Les autres membres verront le badge.",
    },
    "Report %(name)s as a suspected fake? An administrator reviews it, and %(name)s is left alone until then.": {
        "de": "%(name)s als vermutliche Fälschung melden? Ein Administrator prüft die Meldung, und %(name)s wird bis dahin nicht weiter kontaktiert.",
        "fr": "Signaler %(name)s comme faux profil présumé ? Un administrateur examine le signalement, et %(name)s n'est pas contacté d'ici là.",
    },
    "Yes, confirm": {"de": "Ja, bestätigen", "fr": "Oui, confirmer"},
    "Release this member": {
        "de": "Mitglied freigeben",
        "fr": "Libérer ce membre",
    },
    "They go back on the list for any coach to claim.": {
        "de": "Das Mitglied kommt zurück auf die Liste, und jeder Coach kann es übernehmen.",
        "fr": "Le membre retourne sur la liste et n'importe quel coach peut le prendre.",
    },
    "Your notes": {"de": "Deine Notizen", "fr": "Tes notes"},
    # ---- coach_real_person_outreach.html (du / tu) ----
    "Reach out": {"de": "Kontakt aufnehmen", "fr": "Prendre contact"},
    "Your claim ends in %(d)s. Logging a contact attempt renews it.": {
        "de": "Deine Übernahme endet in %(d)s. Ein erfasster Kontaktversuch verlängert sie.",
        "fr": "Ta prise en charge se termine dans %(d)s. Enregistrer une tentative de contact la prolonge.",
    },
    "Contact links are switched off for this member.": {
        "de": "Die Kontakt-Links sind für dieses Mitglied deaktiviert.",
        "fr": "Les liens de contact sont désactivés pour ce membre.",
    },
    "This check is closed, so the contact links are switched off.": {
        "de": "Diese Prüfung ist abgeschlossen, daher sind die Kontakt-Links deaktiviert.",
        "fr": "Ce contrôle est clos : les liens de contact sont donc désactivés.",
    },
    "This member has no verified phone number, so there is nothing to call or text.": {
        "de": "Dieses Mitglied hat keine verifizierte Telefonnummer, daher gibt es nichts zum Anrufen oder Anschreiben.",
        "fr": "Ce membre n'a pas de numéro de téléphone vérifié : il n'y a rien à appeler ni à écrire.",
    },
    "Open SMS app": {"de": "SMS-App öffnen", "fr": "Ouvrir l'app SMS"},
    "The message is written in the member's language. Open it on your own phone, then log it below.": {
        "de": "Die Nachricht ist in der Sprache des Mitglieds verfasst. Öffne sie auf deinem eigenen Handy und erfasse sie dann unten.",
        "fr": "Le message est rédigé dans la langue du membre. Ouvre-le sur ton propre téléphone, puis enregistre-le ci-dessous.",
    },
    "Log a contact attempt": {
        "de": "Kontaktversuch erfassen",
        "fr": "Enregistrer une tentative de contact",
    },
    "I sent an SMS": {
        "de": "Ich habe eine SMS gesendet",
        "fr": "J'ai envoyé un SMS",
    },
    "I sent a WhatsApp message": {
        "de": "Ich habe eine WhatsApp-Nachricht gesendet",
        "fr": "J'ai envoyé un message WhatsApp",
    },
    "I could not reach them": {
        "de": "Ich konnte die Person nicht erreichen",
        "fr": "Je n'ai pas pu la joindre",
    },
    "What happened?": {"de": "Was ist passiert?", "fr": "Que s'est-il passé ?"},
    "Choose a reason": {"de": "Grund wählen", "fr": "Choisir un motif"},
    "Log failed attempt": {
        "de": "Gescheiterten Versuch erfassen",
        "fr": "Enregistrer la tentative échouée",
    },
    "Contact attempts": {
        "de": "Kontaktversuche",
        "fr": "Tentatives de contact",
    },
    "No contact attempts yet.": {
        "de": "Noch keine Kontaktversuche.",
        "fr": "Aucune tentative de contact pour l'instant.",
    },
    # ---- nav, action queue, member overview card (du / tu) ----
    "claim ends in %(d)s": {
        "de": "Übernahme endet in %(d)s",
        "fr": "prise en charge : fin dans %(d)s",
    },
    "No check yet.": {"de": "Noch keine Prüfung.", "fr": "Pas encore de contrôle."},
    "Badge: video-verified by a coach": {
        "de": "Abzeichen: per Video von einem Coach verifiziert",
        "fr": "Badge : vérifié en vidéo par un coach",
    },
    "Badge: spoke with a coach": {
        "de": "Abzeichen: hat mit einem Coach gesprochen",
        "fr": "Badge : a parlé avec un coach",
    },
    "No badge": {"de": "Kein Abzeichen", "fr": "Pas de badge"},
    "not shown to members yet": {
        "de": "für Mitglieder noch nicht sichtbar",
        "fr": "pas encore visible par les membres",
    },
    "Continue the check": {
        "de": "Prüfung fortsetzen",
        "fr": "Poursuivre le contrôle",
    },
    # ---- checklist labels (address nobody) ----
    "I spoke with them live": {
        "de": "Ich habe live mit der Person gesprochen",
        "fr": "J'ai parlé en direct avec la personne",
    },
    "Name matches the profile": {
        "de": "Der Name passt zum Profil",
        "fr": "Le nom correspond au profil",
    },
    "Age looks consistent with the profile": {
        "de": "Das Alter passt zum Profil",
        "fr": "L'âge semble cohérent avec le profil",
    },
    "I explained the badge and they agreed": {
        "de": "Ich habe das Abzeichen erklärt und die Person war einverstanden",
        "fr": "J'ai expliqué le badge et la personne a accepté",
    },
    "Their face matches photo 1": {
        "de": "Das Gesicht passt zu Foto 1",
        "fr": "Son visage correspond à la photo 1",
    },
    "Live camera, not a recording": {
        "de": "Live-Kamera, keine Aufnahme",
        "fr": "Caméra en direct, pas un enregistrement",
    },
    # ---- model labels shown on the member card and the check page ----
    "Passed": {"de": "Bestanden", "fr": "Réussi"},
    "Unreachable": {"de": "Nicht erreichbar", "fr": "Injoignable"},
    "Declined": {"de": "Abgelehnt", "fr": "Refusé"},
    "Released": {"de": "Freigegeben", "fr": "Libéré"},
    "Suspected fake": {
        "de": "Vermutliche Fälschung",
        "fr": "Faux profil présumé",
    },
    "Revoked": {"de": "Widerrufen", "fr": "Révoqué"},
    "Not decided": {"de": "Nicht entschieden", "fr": "Pas encore décidé"},
    # ---- views: flash and guard messages (formal: Sie / vous) ----
    "You cannot check your own profile.": {
        "de": "Sie können Ihr eigenes Profil nicht prüfen.",
        "fr": "Vous ne pouvez pas contrôler votre propre profil.",
    },
    "Your coach account is not active.": {
        "de": "Ihr Coach-Konto ist nicht aktiv.",
        "fr": "Votre compte coach n'est pas actif.",
    },
    "No coach has approved this member's current main photo yet, so a video pass cannot be granted. Review the photo first, or do a voice check.": {
        "de": "Noch kein Coach hat das aktuelle Hauptfoto dieses Mitglieds freigegeben, daher kann kein Video-Abzeichen vergeben werden. Prüfen Sie zuerst das Foto oder führen Sie eine Sprachprüfung durch.",
        "fr": "Aucun coach n'a encore approuvé la photo principale actuelle de ce membre : un badge vidéo ne peut donc pas être attribué. Examinez d'abord la photo ou faites un contrôle vocal.",
    },
    "This member is not available for a check right now.": {
        "de": "Dieses Mitglied steht derzeit nicht für eine Prüfung zur Verfügung.",
        "fr": "Ce membre n'est pas disponible pour un contrôle pour le moment.",
    },
    "Confirm your decision before saving it.": {
        "de": "Bestätigen Sie Ihre Entscheidung, bevor Sie sie speichern.",
        "fr": "Confirmez votre décision avant de l'enregistrer.",
    },
    "This member already has a real-person badge.": {
        "de": "Dieses Mitglied hat bereits ein Echtheits-Abzeichen.",
        "fr": "Ce membre a déjà un badge de personne réelle.",
    },
    "This member is not on the real-person check list.": {
        "de": "Dieses Mitglied steht nicht auf der Liste der Echtheitsprüfungen.",
        "fr": "Ce membre ne figure pas sur la liste des contrôles de personne réelle.",
    },
    "Another coach has already claimed this member.": {
        "de": "Ein anderer Coach hat dieses Mitglied bereits übernommen.",
        "fr": "Un autre coach a déjà pris en charge ce membre.",
    },
    "This member declined a check recently and is left alone for now.": {
        "de": "Dieses Mitglied hat kürzlich eine Prüfung abgelehnt und wird vorerst in Ruhe gelassen.",
        "fr": "Ce membre a récemment refusé un contrôle et est laissé tranquille pour l'instant.",
    },
    "This check is already closed.": {
        "de": "Diese Prüfung ist bereits abgeschlossen.",
        "fr": "Ce contrôle est déjà clos.",
    },
    "Your claim on this member has lapsed. Claim them again from the list.": {
        "de": "Ihre Übernahme dieses Mitglieds ist abgelaufen. Übernehmen Sie es erneut aus der Liste.",
        "fr": "Votre prise en charge de ce membre a expiré. Reprenez-le depuis la liste.",
    },
    "Choose what you did to reach this member.": {
        "de": "Wählen Sie, wie Sie dieses Mitglied kontaktiert haben.",
        "fr": "Choisissez comment vous avez contacté ce membre.",
    },
    "Choose why the attempt failed.": {
        "de": "Wählen Sie, warum der Versuch gescheitert ist.",
        "fr": "Choisissez pourquoi la tentative a échoué.",
    },
    "Choose video or voice.": {
        "de": "Wählen Sie Video oder Sprache.",
        "fr": "Choisissez vidéo ou voix.",
    },
    "The checklist could not be read. Reload the page and try again.": {
        "de": "Die Checkliste konnte nicht gelesen werden. Laden Sie die Seite neu und versuchen Sie es erneut.",
        "fr": "La liste de contrôle n'a pas pu être lue. Rechargez la page et réessayez.",
    },
    "Tick every item on the checklist before granting the badge.": {
        "de": "Haken Sie jeden Punkt der Checkliste ab, bevor Sie das Abzeichen vergeben.",
        "fr": "Cochez chaque point de la liste avant d'attribuer le badge.",
    },
    "Choose what to do with this check.": {
        "de": "Wählen Sie, wie diese Prüfung abgeschlossen werden soll.",
        "fr": "Choisissez comment clore ce contrôle.",
    },
    "You do not have access to do that.": {
        "de": "Dafür haben Sie keine Berechtigung.",
        "fr": "Vous n'avez pas l'autorisation de faire cela.",
    },
    "You already hold %(max)s open checks. Release one before claiming another.": {
        "de": "Sie haben bereits %(max)s offene Prüfungen. Geben Sie eine frei, bevor Sie eine weitere übernehmen.",
        "fr": "Vous avez déjà %(max)s contrôles en cours. Libérez-en un avant d'en prendre un autre.",
    },
    "Notes can be at most %(max)s characters.": {
        "de": "Notizen dürfen höchstens %(max)s Zeichen lang sein.",
        "fr": "Les notes peuvent contenir au maximum %(max)s caractères.",
    },
    "This check could not be saved.": {
        "de": "Diese Prüfung konnte nicht gespeichert werden.",
        "fr": "Ce contrôle n'a pas pu être enregistré.",
    },
    "Contact attempt logged. Your claim now runs for another %(hours)s hours.": {
        "de": "Kontaktversuch erfasst. Ihre Übernahme läuft jetzt weitere %(hours)s Stunden.",
        "fr": "Tentative de contact enregistrée. Votre prise en charge court maintenant pour %(hours)s heures de plus.",
    },
    "Video check passed for %(name)s.": {
        "de": "Videoprüfung für %(name)s bestanden.",
        "fr": "Contrôle vidéo réussi pour %(name)s.",
    },
    "Voice check passed for %(name)s.": {
        "de": "Sprachprüfung für %(name)s bestanden.",
        "fr": "Contrôle vocal réussi pour %(name)s.",
    },
    "%(name)s is marked as unreachable and stays on the list.": {
        "de": "%(name)s ist als nicht erreichbar markiert und bleibt auf der Liste.",
        "fr": "%(name)s est marqué comme injoignable et reste sur la liste.",
    },
    "%(name)s declined. They are left alone for a while.": {
        "de": "%(name)s hat abgelehnt. Das Mitglied wird eine Weile in Ruhe gelassen.",
        "fr": "%(name)s a refusé. Le membre est laissé tranquille un moment.",
    },
    "%(name)s was reported as a suspected fake. An administrator will review it.": {
        "de": "%(name)s wurde als vermutliche Fälschung gemeldet. Ein Administrator prüft die Meldung.",
        "fr": "%(name)s a été signalé comme faux profil présumé. Un administrateur va l'examiner.",
    },
    "You released %(name)s. They are back on the list.": {
        "de": "Sie haben %(name)s freigegeben. Das Mitglied ist wieder auf der Liste.",
        "fr": "Vous avez libéré %(name)s. Le membre est de retour sur la liste.",
    },
    # ---- the text message a coach sends (DE du / FR vous) ----
    "Hi %(first_name)s, this is %(coach_name)s from Crush.lu. To confirm every Crush Connect member is a real person, could we do a quick 5-minute video call? It's optional and adds a 'verified by a coach' badge to your card.": {
        "de": "Hallo %(first_name)s, hier ist %(coach_name)s von Crush.lu. Um zu bestätigen, dass jedes Crush-Connect-Mitglied eine echte Person ist: Hast du Lust auf einen kurzen Videoanruf (5 Minuten)? Er ist freiwillig und gibt deiner Karte das Abzeichen „von einem Coach verifiziert“.",
        "fr": "Bonjour %(first_name)s, ici %(coach_name)s de Crush.lu. Pour confirmer que chaque membre de Crush Connect est une personne réelle, pourrions-nous faire un rapide appel vidéo de 5 minutes ? C'est facultatif et cela ajoute à votre carte un badge « vérifié par un coach ».",
    },
    (
        "real-person check text message: no coach first name",
        "a coach",
    ): {"de": "ein Coach", "fr": "un coach"},
    # Used while only a voice pass is possible (the member's main photo is not
    # approved yet): an invitation that does not promise video.
    "Hi %(first_name)s, this is %(coach_name)s from Crush.lu. To confirm every Crush Connect member is a real person, could we have a quick 5-minute call? It's optional and adds a badge to your card.": {
        "de": "Hallo %(first_name)s, hier ist %(coach_name)s von Crush.lu. Um zu bestätigen, dass jedes Crush-Connect-Mitglied eine echte Person ist: Hast du Lust auf einen kurzen Anruf (5 Minuten)? Er ist freiwillig und gibt deiner Karte ein Abzeichen.",
        "fr": "Bonjour %(first_name)s, ici %(coach_name)s de Crush.lu. Pour confirmer que chaque membre de Crush Connect est une personne réelle, pourrions-nous faire un rapide appel de 5 minutes ? C'est facultatif et cela ajoute un badge à votre carte.",
    },
    # ---- coach_real_person_check.html: confirmation while badges are off (du / tu) ----
    "Confirm that you spoke with %(name)s live and ticked every item truthfully. The badge is saved now, and other members will see it once badges are switched on.": {
        "de": "Bestätige, dass du live mit %(name)s gesprochen und jeden Punkt wahrheitsgemäß abgehakt hast. Das Abzeichen wird jetzt gespeichert; andere Mitglieder sehen es, sobald Abzeichen freigeschaltet sind.",
        "fr": "Confirme que tu as parlé en direct avec %(name)s et coché chaque point en toute honnêteté. Le badge est enregistré maintenant ; les autres membres le verront dès que les badges seront activés.",
    },
    # ---- PR 4: member-facing surfaces (member copy: DE du, FR vous) ----
    # coach_verified_badge.html. The two badge labels are the approved D10 copy.
    # "In-person photo verified" is an existing msgid that never had a DE/FR
    # entry (D10 approved the translations).
    "In-person photo verified": {
        "de": "Foto persönlich verifiziert",
        "fr": "Photo vérifiée en personne",
    },
    "Video-verified by a coach": {
        "de": "Per Video von einem Coach verifiziert",
        "fr": "Vérifié en vidéo par un coach",
    },
    "Spoke with a Crush coach": {
        "de": "Hat mit einem Crush Coach gesprochen",
        "fr": "A parlé avec un coach Crush",
    },
    # Tooltips on the two pills: NOT in D10, agent-written, third person
    # (they address nobody), awaiting Tom's approval.
    "A Crush coach compared this member with their photo on a live video call": {
        "de": "Ein Crush Coach hat dieses Mitglied in einem Live-Videoanruf mit seinem Foto abgeglichen",
        "fr": "Un coach Crush a comparé ce membre à sa photo lors d'un appel vidéo en direct",
    },
    "A Crush coach spoke with this member on a call": {
        "de": "Ein Crush Coach hat in einem Anruf mit diesem Mitglied gesprochen",
        "fr": "Un coach Crush a parlé avec ce membre lors d'un appel",
    },
    # views_crush_connect._connect_trust_status: the LuxID-only member a coach
    # also called (NOT in D10, agent-written, awaiting Tom's approval). Same
    # register as the neighbouring "Identity confirmed by LuxID"; the second
    # sentence is that entry's, word for word, because the check does not
    # unlock Connect Week.
    "Identity confirmed by LuxID and a coach video call": {
        "de": "Identität durch LuxID und einen Videoanruf mit einem Coach bestätigt",
        "fr": "Identité confirmée par LuxID et un appel vidéo avec un coach",
    },
    "Identity confirmed by LuxID and a coach call": {
        "de": "Identität durch LuxID und einen Anruf mit einem Coach bestätigt",
        "fr": "Identité confirmée par LuxID et un appel avec un coach",
    },
    "LuxID confirms your identity, and a Crush coach confirmed on a video call that you are a real person. Taking part in a Crush event unlocks the active Connect journey and up to two new profiles a day, once the other steps are complete.": {
        "de": "LuxID bestätigt deine Identität, und ein Crush Coach hat in einem Videoanruf bestätigt, dass du eine echte Person bist. Die Teilnahme an einem Crush-Event schaltet den aktiven Connect-Weg und bis zu zwei neue Profile pro Tag frei, sobald die übrigen Schritte abgeschlossen sind.",
        "fr": "LuxID confirme votre identité, et un coach Crush a confirmé lors d'un appel vidéo que vous êtes une personne réelle. La participation à un événement Crush débloque le parcours Connect actif et jusqu'à deux nouveaux profils par jour, une fois les autres étapes terminées.",
    },
    "LuxID confirms your identity, and a Crush coach spoke with you and confirmed that you are a real person. Taking part in a Crush event unlocks the active Connect journey and up to two new profiles a day, once the other steps are complete.": {
        "de": "LuxID bestätigt deine Identität, und ein Crush Coach hat mit dir gesprochen und bestätigt, dass du eine echte Person bist. Die Teilnahme an einem Crush-Event schaltet den aktiven Connect-Weg und bis zu zwei neue Profile pro Tag frei, sobald die übrigen Schritte abgeschlossen sind.",
        "fr": "LuxID confirme votre identité, et un coach Crush a parlé avec vous et a confirmé que vous êtes une personne réelle. La participation à un événement Crush débloque le parcours Connect actif et jusqu'à deux nouveaux profils par jour, une fois les autres étapes terminées.",
    },
    # views_crush_connect._real_person_check_note: the hub note, approved copy (D10).
    "To confirm every Connect member is a real person, a Crush coach may text you and suggest a short video or voice call. It's optional: it adds a badge to your card and changes nothing else.": {
        "de": "Damit wir sicher sind, dass jedes Connect-Mitglied eine echte Person ist, kann dir ein Crush Coach schreiben und einen kurzen Video- oder Sprachanruf vorschlagen. Das ist freiwillig: Deine Karte bekommt ein Abzeichen, sonst ändert sich nichts.",
        "fr": "Pour confirmer que chaque membre de Connect est une personne réelle, un coach Crush peut vous envoyer un message et vous proposer un court appel vidéo ou vocal. C'est facultatif : cela ajoute un badge à votre carte et ne change rien d'autre.",
    },
}


# "Unreachable" already sits in both catalogs as a fuzzy guess carried over from
# "Breached" (models/connections.py, the call outcome of the same name). Django
# ignores fuzzy entries, so the real-person check status would stay English;
# the meaning is the same, so settle that entry instead of adding a duplicate.
#
# "Decision" is not fuzzy but wrong: the DE entry reads "Version:" (a stray
# translation of the screening-call decision heading, _decision_tab.html). The
# check page uses the same msgid as the heading of its decision form, so settle
# it in place rather than fork the msgid.
SETTLE_FUZZY = {
    "de": [
        (
            '#, fuzzy\n#| msgid "Breached"\nmsgid "Unreachable"\nmsgstr "Überschritten"\n',
            'msgid "Unreachable"\nmsgstr "Nicht erreichbar"\n',
        ),
        (
            '\nmsgid "Decision"\nmsgstr "Version:"\n',
            '\nmsgid "Decision"\nmsgstr "Entscheidung"\n',
        ),
    ],
    "fr": [
        (
            '#, fuzzy\n#| msgid "Breached"\nmsgid "Unreachable"\nmsgstr "Compromis"\n',
            'msgid "Unreachable"\nmsgstr "Injoignable"\n',
        ),
    ],
}


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Appended as text rather than po.save(): polib re-wraps and re-orders the
# whole catalogue, turning a few dozen new strings into a 30,000-line diff.
for lang in ("de", "fr"):
    path = f"crush_lu/locale/{lang}/LC_MESSAGES/django.po"
    po = polib.pofile(path)
    blocks = []
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    settled_any = False
    for stale, settled in SETTLE_FUZZY[lang]:
        if stale in text:
            text = text.replace(stale, settled, 1)
            settled_any = True
            print(f"{lang}: settled {settled.strip().splitlines()[0]}")
    if settled_any:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        po = polib.pofile(path)
    for key, translations in NEW.items():
        # A (context, msgid) key is a pgettext / {% trans ... context %} entry.
        context, msgid = key if isinstance(key, tuple) else (None, key)
        existing = po.find(msgid, msgctxt=context)
        if existing is not None:
            # Already in the catalog (translated, or a fuzzy / empty entry a
            # human should settle): appending would duplicate the msgid.
            if not existing.msgstr or "fuzzy" in existing.flags:
                print(f"{lang}: left alone, not translated yet: {msgid[:60]!r}")
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
