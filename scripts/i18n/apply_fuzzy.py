"""Apply DE/FR translations for fuzzy user-facing template entries.

Surgical edit: replaces only msgstr + removes fuzzy marker for target entries.
"""
import sys, re
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

# Translation table: msgid -> (de, fr). None means skip for that language.
T = {
    "\n            Page %(current)s of %(total)s\n            ": (
        "\n            Seite %(current)s von %(total)s\n            ",
        "\n            Page %(current)s sur %(total)s\n            ",
    ),
    "%(name)s - No photo": ("%(name)s – Kein Foto", "%(name)s – Pas de photo"),
    "%(name)s will be notified of your request": ("%(name)s wird über deine Anfrage benachrichtigt", "%(name)s sera informé·e de ta demande"),
    "%(opened)s/24 doors opened": ("%(opened)s/24 Türchen geöffnet", "%(opened)s/24 portes ouvertes"),
    "%(provider)s photo": ("%(provider)s-Foto", "Photo %(provider)s"),
    "&copy; 2026 Crush.lu - Made with love in Luxembourg": ("&copy; 2026 Crush.lu – mit Liebe in Luxemburg gemacht", "&copy; 2026 Crush.lu – Fait avec amour au Luxembourg"),
    "&copy; 2026 Crush.lu - Made with 💕 in Luxembourg": ("&copy; 2026 Crush.lu – mit 💕 in Luxemburg gemacht", "&copy; 2026 Crush.lu – Fait avec 💕 au Luxembourg"),
    "(optional)": ("(optional)", "(facultatif)"),
    "3-5 minute conversations. Your ratings help decide who you're paired with! Twist chosen by YOUR votes.": (
        "3–5-minütige Gespräche. Deine Bewertungen entscheiden mit, wer mit dir gepaart wird! Das Thema wird durch EURE Stimmen gewählt.",
        "Conversations de 3 à 5 minutes. Tes notes aident à décider avec qui tu seras jumelé·e ! La touche spéciale est choisie par VOS votes.",
    ),
    "A Crush Coach from our team will pick up your profile and review it within 24-48 hours": (
        "Ein Crush Coach aus unserem Team übernimmt dein Profil und prüft es innerhalb von 24–48 Stunden",
        "Un Crush Coach de notre équipe prendra ton profil en charge et l'examinera sous 24 à 48 heures",
    ),
    "About Crush.lu - Made in Luxembourg Dating Platform | Press & Founders": (
        "Über Crush.lu – Dating-Plattform Made in Luxembourg | Presse & Gründer",
        "À propos de Crush.lu – Plateforme de rencontres Made in Luxembourg | Presse & fondateurs",
    ),
    "Account": ("Konto", "Compte"),
    "Account Deactivated": ("Konto deaktiviert", "Compte désactivé"),
    "Account Deletion": ("Konto löschen", "Suppression du compte"),
    "Account Suspended": ("Konto gesperrt", "Compte suspendu"),
    "Active Polls": ("Aktive Abstimmungen", "Sondages actifs"),
    "Add Email": ("E-Mail hinzufügen", "Ajouter un e-mail"),
    "Add Email Address": ("E-Mail-Adresse hinzufügen", "Ajouter une adresse e-mail"),
    "Add Photo %(slot)s": ("Foto %(slot)s hinzufügen", "Ajouter la photo %(slot)s"),
    "Add a note (optional)": ("Notiz hinzufügen (optional)", "Ajouter une note (facultatif)"),
    "Add it to Apple Wallet or Google Wallet": ("Zum Apple Wallet oder Google Wallet hinzufügen", "Ajoute-le à Apple Wallet ou Google Wallet"),
    "Add to Calendar": ("Zum Kalender hinzufügen", "Ajouter au calendrier"),
    "Advent Calendar - Coming Soon": ("Adventskalender – demnächst", "Calendrier de l'Avent – Bientôt disponible"),
    "After voting closes, the most voted options are revealed for BOTH categories. Each one shapes a different part of your evening!": (
        "Nach Abstimmungsende werden für BEIDE Kategorien die Top-Optionen aufgedeckt. Jede gestaltet einen anderen Teil deines Abends!",
        "À la clôture du vote, les options les plus votées sont dévoilées pour les DEUX catégories. Chacune façonne une partie différente de ta soirée !",
    ),
    "After voting closes, you'll see the chosen options for BOTH categories - each one shapes a different part of your evening!": (
        "Nach Abstimmungsende siehst du die gewählten Optionen für BEIDE Kategorien – jede gestaltet einen anderen Teil deines Abends!",
        "À la clôture du vote, tu verras les options choisies pour les DEUX catégories – chacune façonne une partie différente de ta soirée !",
    ),
    "After you submit, your coach will review your profile and you'll be ready to join events!": (
        "Nach dem Absenden prüft dein Coach dein Profil, und du kannst an Events teilnehmen!",
        "Une fois que tu auras soumis, ton coach examinera ton profil et tu pourras participer aux événements !",
    ),
    "Age Verification": ("Altersverifizierung", "Vérification de l'âge"),
    "Age Verification Required": ("Altersverifizierung erforderlich", "Vérification de l'âge requise"),
    "Age, gender, traits, zodiac": ("Alter, Geschlecht, Eigenschaften, Sternzeichen", "Âge, genre, traits, zodiaque"),
    "All set!": ("Alles erledigt!", "Tout est prêt !"),
    "All you need to do is hit submit! Your profile is ready for review.": (
        "Du musst nur noch auf Absenden klicken! Dein Profil ist bereit zur Prüfung.",
        "Il ne te reste qu'à cliquer sur Envoyer ! Ton profil est prêt à être examiné.",
    ),
    "Almost done:": ("Fast fertig:", "Presque fini :"),
    "Almost there!": ("Fast geschafft!", "Presque fini !"),
    "Already Sent": ("Bereits gesendet", "Déjà envoyé"),
    "Anything else to record about the call?": ("Noch etwas zum Gespräch festzuhalten?", "Autre chose à noter concernant l'appel ?"),
    "Apple Calendar (.ics)": ("Apple Calendar (.ics)", "Apple Calendar (.ics)"),
    "Approved - Create Journey": ("Freigegeben – Journey erstellen", "Approuvé – Créer le voyage"),
    "Assigned:": ("Zugewiesen:", "Attribué :"),
    "Attend an event and someone might send you a Crush Spark!": (
        "Nimm an einem Event teil – vielleicht schickt dir jemand einen Crush Spark!",
        "Participe à un événement – quelqu'un pourrait t'envoyer un Crush Spark !",
    ),
    "Back to Calendar": ("Zurück zum Kalender", "Retour au calendrier"),
    "Back to Event Dashboard": ("Zurück zum Event-Dashboard", "Retour au tableau de bord de l'événement"),
    "Back to Profile": ("Zurück zum Profil", "Retour au profil"),
    "Back to Sparks": ("Zurück zu Sparks", "Retour aux Sparks"),
    "Back to login": ("Zurück zur Anmeldung", "Retour à la connexion"),
    "Bio must be under 500 characters": ("Bio darf höchstens 500 Zeichen haben", "La bio doit contenir moins de 500 caractères"),
    "Bio, interests, personality, contact": ("Bio, Interessen, Persönlichkeit, Kontakt", "Bio, centres d'intérêt, personnalité, contact"),
    "Bonus Unlocked!": ("Bonus freigeschaltet!", "Bonus débloqué !"),
    "Bonus content available!": ("Bonusinhalt verfügbar!", "Contenu bonus disponible !"),
    "Bonus photo available!": ("Bonusfoto verfügbar!", "Photo bonus disponible !"),
    "Book my screening call": ("Screening-Gespräch buchen", "Réserver mon appel de vérification"),
    "Book your screening call.": ("Buche dein Screening-Gespräch.", "Réserve ton appel de vérification."),
    "Build your profile": ("Profil erstellen", "Crée ton profil"),
    "Calibration Call Completed": ("Kalibrierungsgespräch abgeschlossen", "Appel de calibration terminé"),
    "Call attempts": ("Anrufversuche", "Tentatives d'appel"),
    "Cancelling...": ("Stornieren …", "Annulation…"),
    "Candidate has no verified phone number — cannot send SMS.": (
        "Kandidat hat keine verifizierte Telefonnummer – SMS nicht möglich.",
        "La personne n'a pas de numéro vérifié – SMS impossible.",
    ),
    "Category A - Selected": ("Kategorie A – ausgewählt", "Catégorie A – sélectionnée"),
    "Category B - Selected": ("Kategorie B – ausgewählt", "Catégorie B – sélectionnée"),
    "Change Password - Crush.lu": ("Passwort ändern – Crush.lu", "Modifier le mot de passe – Crush.lu"),
    "Change Photo": ("Foto ändern", "Changer la photo"),
    "Change password": ("Passwort ändern", "Modifier le mot de passe"),
    "Check In to Vote": ("Einchecken, um abzustimmen", "Faire son check-in pour voter"),
    "Check Subscription Health": ("Abo-Status prüfen", "Vérifier l'état de l'abonnement"),
    "Check your email": ("Prüfe deine E-Mails", "Vérifie ta boîte mail"),
    "Check your email - Crush.lu": ("Prüfe deine E-Mails – Crush.lu", "Vérifie ta boîte mail – Crush.lu"),
    "Check-In Required": ("Check-in erforderlich", "Check-in requis"),
    "Checked In (%(count)s)": ("Eingecheckt (%(count)s)", "Enregistré·e·s (%(count)s)"),
    "Choose a new password": ("Neues Passwort wählen", "Choisir un nouveau mot de passe"),
    "Choose a new password - Crush.lu": ("Neues Passwort wählen – Crush.lu", "Choisir un nouveau mot de passe – Crush.lu"),
    "Click to upload audio": ("Klicken, um Audio hochzuladen", "Clique pour téléverser un audio"),
    "Click to upload photo": ("Klicken, um ein Foto hochzuladen", "Clique pour téléverser une photo"),
    "Click to upload video": ("Klicken, um ein Video hochzuladen", "Clique pour téléverser une vidéo"),
    "Close This Window": ("Dieses Fenster schließen", "Fermer cette fenêtre"),
    "Close Window": ("Fenster schließen", "Fermer la fenêtre"),
    "Closed": ("Geschlossen", "Fermé"),
    "Coach Approved": ("Vom Coach freigegeben", "Approuvé par le coach"),
    "Coach Assigned": ("Coach zugewiesen", "Coach attribué"),
    "Coach Assignment in Progress": ("Coach-Zuweisung läuft", "Attribution du coach en cours"),
    "Coach View": ("Coach-Ansicht", "Vue coach"),
    "Coach Wants to Connect": ("Coach möchte sich verbinden", "Un coach souhaite se connecter"),
    "Coach wants to connect": ("Coach möchte sich verbinden", "Un coach souhaite se connecter"),
    "Coach-Only Section": ("Nur für Coaches", "Section réservée aux coachs"),
    "Complete all required steps before approval": ("Alle erforderlichen Schritte vor Freigabe erledigen", "Effectue toutes les étapes requises avant l'approbation"),
    "Complete the call to get your profile approved": ("Schließe das Gespräch ab, um dein Profil freizugeben", "Termine l'appel pour faire approuver ton profil"),
    "Completing Login": ("Anmeldung wird abgeschlossen", "Finalisation de la connexion"),
    "Completing Login...": ("Anmeldung wird abgeschlossen …", "Finalisation de la connexion…"),
    "Concept Calibration": ("Konzept-Kalibrierung", "Calibration du concept"),
    "Confirm & Accept Invitation": ("Bestätigen & Einladung annehmen", "Confirmer & accepter l'invitation"),
    "Confirm & Continue": ("Bestätigen & weiter", "Confirmer & continuer"),
    "Confirm New Password": ("Neues Passwort bestätigen", "Confirmer le nouveau mot de passe"),
    "Confirm Your Acceptance": ("Deine Zusage bestätigen", "Confirme ton acceptation"),
    "Confirm Your Consent": ("Deine Zustimmung bestätigen", "Confirme ton consentement"),
    "Congratulations!": ("Herzlichen Glückwunsch!", "Félicitations !"),
    "Connection": ("Verbindung", "Connexion"),
    "Consents": ("Einwilligungen", "Consentements"),
    "Contact %(name)s": ("%(name)s kontaktieren", "Contacter %(name)s"),
    "Contact & Social": ("Kontakt & Soziales", "Contact & réseaux sociaux"),
    "Contact Coach": ("Coach kontaktieren", "Contacter le coach"),
    "Contact Support": ("Support kontaktieren", "Contacter le support"),
    "Contact us": ("Kontaktiere uns", "Contacte-nous"),
    "Contact via WhatsApp": ("Per WhatsApp kontaktieren", "Contacter via WhatsApp"),
    "Contact your coach": ("Kontaktiere deinen Coach", "Contacte ton coach"),
    "Contact your coach using the information below": ("Kontaktiere deinen Coach über die Angaben unten", "Contacte ton coach à l'aide des informations ci-dessous"),
    "Contacts Shared": ("Kontakte geteilt", "Contacts partagés"),
    "Content Coming Soon!": ("Inhalt demnächst!", "Contenu bientôt disponible !"),
    "Continue Editing": ("Bearbeitung fortsetzen", "Continuer l'édition"),
    "Continue to Crush.lu": ("Weiter zu Crush.lu", "Continuer vers Crush.lu"),
    "Control what other users can see about you.": ("Lege fest, was andere Nutzer von dir sehen können.", "Contrôle ce que les autres utilisateurs peuvent voir de toi."),
    "Could not access camera. Please use manual input.": ("Kamera nicht zugänglich. Bitte manuell eingeben.", "Impossible d'accéder à la caméra. Utilise la saisie manuelle."),
    "Could not enable push notifications. Please try from Settings.": (
        "Push-Benachrichtigungen konnten nicht aktiviert werden. Versuche es in den Einstellungen.",
        "Impossible d'activer les notifications push. Essaie depuis les paramètres.",
    ),
    "Create & Deliver Journey": ("Journey erstellen & versenden", "Créer & envoyer le voyage"),
    "Create Journey": ("Journey erstellen", "Créer le voyage"),
    "Create Your Anonymous Journey": ("Erstelle deine anonyme Journey", "Crée ton voyage anonyme"),
    "Created": ("Erstellt", "Créé"),
    "Creating Account...": ("Konto wird erstellt …", "Création du compte…"),
    "Crush Spark": ("Crush Spark", "Crush Spark"),
    "Crush Sparks": ("Crush Sparks", "Crush Sparks"),
    "Crush.lu Profile": ("Crush.lu-Profil", "Profil Crush.lu"),
    "Current Password": ("Aktuelles Passwort", "Mot de passe actuel"),
    "Data Management": ("Datenverwaltung", "Gestion des données"),
    "Date": ("Datum", "Date"),
    "Day %(num)s": ("Tag %(num)s", "Jour %(num)s"),
    "Delete Crush.lu Profile Only": ("Nur Crush.lu-Profil löschen", "Supprimer uniquement le profil Crush.lu"),
    "Delete my Crush.lu profile": ("Mein Crush.lu-Profil löschen", "Supprimer mon profil Crush.lu"),
    "Delivered:": ("Zugestellt:", "Livré :"),
    "Dismiss error": ("Fehler schließen", "Ignorer l'erreur"),
    "Dismiss this message": ("Nachricht schließen", "Ignorer ce message"),
    "Display": ("Anzeige", "Affichage"),
    "Don't want to continue?": ("Du möchtest nicht fortfahren?", "Tu ne veux pas continuer ?"),
    "Door %(num)s": ("Türchen %(num)s", "Porte %(num)s"),
    "Download My Data": ("Meine Daten herunterladen", "Télécharger mes données"),
    "Download Your Data": ("Deine Daten herunterladen", "Télécharge tes données"),
    "Email Address": ("E-Mail-Adresse", "Adresse e-mail"),
    "Email address": ("E-Mail-Adresse", "Adresse e-mail"),
    "Enabling...": ("Wird aktiviert …", "Activation…"),
    "Enter code from QR": ("Code vom QR eingeben", "Saisis le code du QR"),
    "Enter your number and click Verify to receive an SMS code.": (
        "Gib deine Nummer ein und klicke auf Verifizieren, um einen SMS-Code zu erhalten.",
        "Saisis ton numéro et clique sur Vérifier pour recevoir un code par SMS.",
    ),
    "Error": ("Fehler", "Erreur"),
    "Error code:": ("Fehlercode:", "Code d'erreur :"),
    "Error saving. Please try again.": ("Fehler beim Speichern. Bitte erneut versuchen.", "Erreur d'enregistrement. Réessaie."),
    "Estimated review time": ("Geschätzte Prüfzeit", "Temps d'examen estimé"),
    "Event": ("Event", "Événement"),
    "Event Dashboard": ("Event-Dashboard", "Tableau de bord de l'événement"),
    "Event Fee:": ("Teilnahmegebühr:", "Frais de participation :"),
    "Event Polls": ("Event-Abstimmungen", "Sondages d'événement"),
    "Event is Full": ("Event ist ausgebucht", "L'événement est complet"),
    "Everyone gets 90 seconds to introduce themselves. You rate each presenter anonymously (1-5 stars).": (
        "Jede Person hat 90 Sekunden, um sich vorzustellen. Du bewertest jeden Vortrag anonym (1–5 Sterne).",
        "Chacun·e a 90 secondes pour se présenter. Tu notes chaque personne anonymement (1 à 5 étoiles).",
    ),
    "Explore Crush.lu": ("Crush.lu entdecken", "Découvre Crush.lu"),
    "Feedback & Approval": ("Rückmeldung & Freigabe", "Retour & approbation"),
    "Feedback from our team": ("Rückmeldung von unserem Team", "Retour de notre équipe"),
    "Final call notes": ("Abschließende Gesprächsnotizen", "Notes finales de l'appel"),
    "Fix Now": ("Jetzt beheben", "Corriger maintenant"),
    "Forgot your password?": ("Passwort vergessen?", "Mot de passe oublié ?"),
    "Get ready to choose your favorite activities!": ("Mach dich bereit, deine Lieblingsaktivitäten zu wählen!", "Prépare-toi à choisir tes activités préférées !"),
    "Gift Teaser Coming Soon!": ("Geschenk-Teaser demnächst!", "Aperçu du cadeau bientôt !"),
    "Gift preview": ("Geschenkvorschau", "Aperçu du cadeau"),
    "Go Back": ("Zurück", "Retour"),
    "Go to Presentation Control": ("Zur Präsentationssteuerung", "Aller au contrôle des présentations"),
    "Go to Presentations": ("Zu den Präsentationen", "Aller aux présentations"),
    "Go to my dashboard": ("Zu meinem Dashboard", "Aller à mon tableau de bord"),
    "Google Calendar": ("Google Calendar", "Google Calendar"),
    "Have Voted": ("Haben abgestimmt", "Ont voté"),
    "Have a question? Reach out:": ("Du hast eine Frage? Melde dich:", "Une question ? Contacte-nous :"),
    "Haven't Voted Yet (%(count)s)": ("Noch nicht abgestimmt (%(count)s)", "N'ont pas encore voté (%(count)s)"),
    "Hi! I need help with phone verification on Crush.lu.": (
        "Hallo! Ich brauche Hilfe bei der Telefon-Verifizierung auf Crush.lu.",
        "Bonjour ! J'ai besoin d'aide pour la vérification du téléphone sur Crush.lu.",
    ),
    "Hi! I need help with phone verification on Crush.lu. My phone number seems to be linked to another account.": (
        "Hallo! Ich brauche Hilfe bei der Telefon-Verifizierung auf Crush.lu. Meine Nummer scheint mit einem anderen Konto verknüpft zu sein.",
        "Bonjour ! J'ai besoin d'aide pour la vérification du téléphone sur Crush.lu. Mon numéro semble lié à un autre compte.",
    ),
    "How it works": ("So funktioniert's", "Comment ça marche"),
    "How will everyone introduce themselves in 90 seconds?": (
        "Wie wird sich jede Person in 90 Sekunden vorstellen?",
        "Comment chacun·e se présentera-t-il/elle en 90 secondes ?",
    ),
    "I agree to the": ("Ich stimme den", "J'accepte les"),
    "I have an existing account": ("Ich habe bereits ein Konto", "J'ai déjà un compte"),
    "I'm new, continue to create my profile": ("Ich bin neu, Profil anlegen", "Je suis nouveau·nouvelle, créer mon profil"),
    "Ideal Crush": ("Idealer Crush", "Crush idéal·e"),
    "If you have questions about this decision, you can contact our support team.": (
        "Bei Fragen zu dieser Entscheidung kannst du unser Support-Team kontaktieren.",
        "Si tu as des questions sur cette décision, tu peux contacter notre équipe de support.",
    ),
    "Importing…": ("Importiere …", "Importation…"),
    "In a real event, you would now see the results page with live vote counts. Scrolling to results...": (
        "Bei einem echten Event würdest du jetzt die Ergebnisseite mit Live-Stimmen sehen. Zu den Ergebnissen scrollen …",
        "Lors d'un vrai événement, tu verrais maintenant la page des résultats en direct. Défilement vers les résultats…",
    ),
    "Incorrect PIN. Please try again.": ("Falsche PIN. Bitte erneut versuchen.", "PIN incorrect. Réessaie."),
    "Interests must be under 300 characters": ("Interessen dürfen höchstens 300 Zeichen haben", "Les centres d'intérêt doivent contenir moins de 300 caractères"),
    "Invalid reset link": ("Ungültiger Reset-Link", "Lien de réinitialisation invalide"),
    "Join Crush.lu to get your digital membership card, earn rewards, and unlock exclusive benefits.": (
        "Tritt Crush.lu bei, um deine digitale Mitgliedskarte zu erhalten, Belohnungen zu sammeln und exklusive Vorteile freizuschalten.",
        "Rejoins Crush.lu pour obtenir ta carte de membre numérique, gagner des récompenses et débloquer des avantages exclusifs.",
    ),
    "Join Quiz": ("Quiz beitreten", "Rejoindre le quiz"),
    "Journey Created": ("Journey erstellt", "Voyage créé"),
    "Language Requirement": ("Sprachanforderung", "Exigence linguistique"),
    "Let's verify your phone number.": ("Lass uns deine Nummer verifizieren.", "Vérifions ton numéro de téléphone."),
    "Link Your Account": ("Konto verknüpfen", "Lier ton compte"),
    "Live Voting Dashboard": ("Live-Abstimmungs-Dashboard", "Tableau de bord du vote en direct"),
    "Loading": ("Lädt", "Chargement"),
    "Locked until approval": ("Gesperrt bis zur Freigabe", "Verrouillé jusqu'à l'approbation"),
    "Log Failed Attempt": ("Fehlschlag protokollieren", "Enregistrer la tentative échouée"),
    "Log Sent": ("Als gesendet protokollieren", "Enregistrer comme envoyé"),
    "Log out": ("Abmelden", "Se déconnecter"),
    "Logging in...": ("Anmeldung läuft …", "Connexion…"),
    "Login Failed": ("Anmeldung fehlgeschlagen", "Échec de la connexion"),
    "Looking for QR code...": ("Suche QR-Code …", "Recherche du code QR…"),
    "Main Photo": ("Hauptfoto", "Photo principale"),
    "Main navigation": ("Hauptnavigation", "Navigation principale"),
    "Main profile photo": ("Haupt-Profilfoto", "Photo de profil principale"),
    "Manage": ("Verwalten", "Gérer"),
    "Manage Email Addresses": ("E-Mail-Adressen verwalten", "Gérer les adresses e-mail"),
    "Mark Calibration Call Complete": ("Kalibrierungsgespräch als abgeschlossen markieren", "Marquer l'appel de calibration comme terminé"),
    "Members": ("Mitglieder", "Membres"),
    "Mobile navigation": ("Mobile Navigation", "Navigation mobile"),
    "Most Voted": ("Am meisten gewählt", "Le plus voté"),
    "Most voted = Phase 2 format": ("Am meisten gewählt = Phase-2-Format", "Plus voté = format phase 2"),
    "Most voted = Phase 3 twist": ("Am meisten gewählt = Phase-3-Twist", "Plus voté = touche phase 3"),
    "My Crush": ("Mein Crush", "Mon Crush"),
    "My Crush Sparks": ("Meine Crush Sparks", "Mes Crush Sparks"),
    "My Sparks": ("Meine Sparks", "Mes Sparks"),
    "Nearly there.": ("Fast geschafft.", "Presque fini."),
    "New": ("Neu", "Nouveau"),
    "New password": ("Neues Passwort", "Nouveau mot de passe"),
    "Next in queue": ("Als Nächstes in der Warteschlange", "Prochain dans la file"),
    "No matches yet": ("Noch keine Matches", "Pas encore d'affinités"),
    "No messages yet. Start the conversation!": ("Noch keine Nachrichten. Fang das Gespräch an!", "Pas encore de messages. Lance la conversation !"),
    "No photo available": ("Kein Foto verfügbar", "Pas de photo disponible"),
    "No photos yet": ("Noch keine Fotos", "Pas encore de photos"),
    "No sparks received yet.": ("Noch keine Sparks erhalten.", "Aucun spark reçu pour l'instant."),
    "No upcoming events right now — check back soon for speed dating nights and social mixers.": (
        "Momentan keine kommenden Events – schau bald wieder rein für Speed-Dating und Social-Mixers.",
        "Aucun événement à venir pour le moment – reviens bientôt pour les soirées speed dating et les social mixers.",
    ),
    "Not Checked In (%(count)s)": ("Nicht eingecheckt (%(count)s)", "Non enregistré·e·s (%(count)s)"),
    "Not Yet Voted": ("Noch nicht abgestimmt", "Pas encore voté"),
    "Not given": ("Keine Angabe", "Non renseigné"),
    "Note sent to your coach": ("Notiz an deinen Coach gesendet", "Note envoyée à ton coach"),
    "Notes (optional)": ("Notizen (optional)", "Notes (facultatif)"),
    "Once approved, you'll receive an email with event details": (
        "Nach der Freigabe erhältst du eine E-Mail mit den Event-Details",
        "Une fois approuvé·e, tu recevras un e-mail avec les détails de l'événement",
    ),
    "Once you complete the screening call, your profile can be approved and you'll be able to register for events!": (
        "Sobald das Screening-Gespräch abgeschlossen ist, kann dein Profil freigegeben werden und du kannst dich für Events anmelden!",
        "Une fois l'appel de vérification terminé, ton profil pourra être approuvé et tu pourras t'inscrire aux événements !",
    ),
    "Open in Crush.lu App": ("In der Crush.lu-App öffnen", "Ouvrir dans l'application Crush.lu"),
    "Opening login...": ("Anmeldung wird geöffnet …", "Ouverture de la connexion…"),
    "Option 2: Delete Entire PowerUp Account": ("Option 2: Gesamtes PowerUp-Konto löschen", "Option 2 : supprimer l'intégralité du compte PowerUp"),
    "Optional: Marketing Communications": ("Optional: Marketing-Kommunikation", "Facultatif : communications marketing"),
    "Or import from your accounts": ("Oder aus deinen Konten importieren", "Ou importer depuis tes comptes"),
    "Our Crush Coach will review your attendance request": (
        "Unser Crush Coach prüft deine Teilnahmeanfrage",
        "Notre Crush Coach examinera ta demande de participation",
    ),
    "Outlook Calendar": ("Outlook Calendar", "Outlook Calendar"),
    "Password changed": ("Passwort geändert", "Mot de passe modifié"),
    "Password changed - Crush.lu": ("Passwort geändert – Crush.lu", "Mot de passe modifié – Crush.lu"),
    "Past events will appear here once you've attended your first meetup.": (
        "Vergangene Events erscheinen hier, sobald du an deinem ersten Meetup teilgenommen hast.",
        "Les événements passés apparaîtront ici une fois que tu auras participé à ton premier meetup.",
    ),
    "Personal Message": ("Persönliche Nachricht", "Message personnel"),
    "Photo Coming Soon!": ("Foto demnächst!", "Photo bientôt disponible !"),
    "Photo Puzzle": ("Foto-Puzzle", "Puzzle photo"),
    "Photo visible after contact is shared": ("Foto sichtbar, sobald Kontakt geteilt ist", "Photo visible une fois les contacts partagés"),
    "Photos added": ("Fotos hinzugefügt", "Photos ajoutées"),
    "Play the Journey": ("Journey abspielen", "Lancer le voyage"),
    "Please complete all required steps (Introduction and Residence Check) before submitting.": (
        "Bitte schließe alle erforderlichen Schritte (Vorstellung und Wohnsitzprüfung) ab, bevor du absendest.",
        "Effectue toutes les étapes requises (Introduction et Vérification de résidence) avant de soumettre.",
    ),
    "Please wait a moment.": ("Bitte einen Moment Geduld.", "Un instant, s'il te plaît."),
    "Poem Coming Soon!": ("Gedicht demnächst!", "Poème bientôt disponible !"),
    "Point your camera at the QR code on your physical gift to unlock bonus content!": (
        "Richte deine Kamera auf den QR-Code deines physischen Geschenks, um Bonusinhalte freizuschalten!",
        "Pointe ta caméra sur le code QR de ton cadeau physique pour débloquer du contenu bonus !",
    ),
    "PowerUp Account": ("PowerUp-Konto", "Compte PowerUp"),
    "Pre-Screening Answers": ("Antworten auf das Vor-Screening", "Réponses au questionnaire préalable"),
    "Pre-screening readiness": ("Vor-Screening-Bereitschaft", "Préparation au pré-screening"),
    "Preferences configured": ("Vorlieben konfiguriert", "Préférences configurées"),
    "Prepare for your screening call": ("Bereite dich auf dein Screening-Gespräch vor", "Prépare-toi à ton appel de vérification"),
    "Presentation Style Results": ("Ergebnisse Präsentationsstil", "Résultats du style de présentation"),
    "Presentation Style: <strong>%(choice)s</strong>": ("Präsentationsstil: <strong>%(choice)s</strong>", "Style de présentation : <strong>%(choice)s</strong>"),
    "Preview Email": ("E-Mail-Vorschau", "Aperçu de l'e-mail"),
    "Privacy": ("Datenschutz", "Confidentialité"),
    "Privacy-protected profile": ("Datenschutz-geschütztes Profil", "Profil protégé par la confidentialité"),
    "Processing...": ("Wird verarbeitet …", "Traitement…"),
    "Profile Queue": ("Profil-Warteschlange", "File des profils"),
    "Profile Required": ("Profil erforderlich", "Profil requis"),
    "Profile Updated Successfully!": ("Profil erfolgreich aktualisiert!", "Profil mis à jour avec succès !"),
    "Profile paused": ("Profil pausiert", "Profil en pause"),
    "Profile photo": ("Profilfoto", "Photo de profil"),
    "Profile photo %(slot)s": ("Profilfoto %(slot)s", "Photo de profil %(slot)s"),
    "Push notifications enabled! You will now receive instant alerts.": (
        "Push-Benachrichtigungen aktiviert! Du erhältst jetzt sofortige Hinweise.",
        "Notifications push activées ! Tu recevras désormais des alertes instantanées.",
    ),
    "Re-send Verification": ("Verifizierung erneut senden", "Renvoyer la vérification"),
    "Readiness": ("Bereitschaft", "Préparation"),
    "Ready to Create": ("Bereit zum Erstellen", "Prêt·e à créer"),
    "Reason": ("Grund", "Raison"),
    "Reassigned for a faster review": ("Für schnellere Prüfung neu zugewiesen", "Réaffecté pour un examen plus rapide"),
    "Reconnect %(provider)s": ("%(provider)s erneut verbinden", "Reconnecter %(provider)s"),
    "Recontact": ("Erneut kontaktieren", "Reprise de contact"),
    "Record or upload a video message (MP4, MOV - max 50MB).": (
        "Nimm eine Videobotschaft auf oder lade sie hoch (MP4, MOV – max. 50 MB).",
        "Enregistre ou téléverse un message vidéo (MP4, MOV – max. 50 Mo).",
    ),
    "Refresh": ("Aktualisieren", "Actualiser"),
    "Register for": ("Anmelden für", "S'inscrire à"),
    "Registered": ("Angemeldet", "Inscrit·e"),
    "Remove photo": ("Foto entfernen", "Supprimer la photo"),
    "Remove photo %(slot)s": ("Foto %(slot)s entfernen", "Supprimer la photo %(slot)s"),
    "Remove this photo?": ("Dieses Foto entfernen?", "Supprimer cette photo ?"),
    "Request a copy of all your personal data in JSON format (GDPR Article 20).": (
        "Eine Kopie all deiner personenbezogenen Daten im JSON-Format anfordern (Art. 20 DSGVO).",
        "Demander une copie de toutes tes données personnelles au format JSON (article 20 RGPD).",
    ),
    "Request a new reset link": ("Neuen Reset-Link anfordern", "Demander un nouveau lien de réinitialisation"),
    "Reset Password - Crush.lu": ("Passwort zurücksetzen – Crush.lu", "Réinitialiser le mot de passe – Crush.lu"),
    "Reset my password": ("Passwort zurücksetzen", "Réinitialiser mon mot de passe"),
    "Reset your Crush.lu password": ("Crush.lu-Passwort zurücksetzen", "Réinitialise ton mot de passe Crush.lu"),
    "Reset your password": ("Passwort zurücksetzen", "Réinitialise ton mot de passe"),
    "Results": ("Ergebnisse", "Résultats"),
    "Review Progress": ("Prüf-Fortschritt", "Avancement de l'examen"),
    "Review our Terms of Service": ("Lies unsere Nutzungsbedingungen", "Consulte nos conditions d'utilisation"),
    "SMS sent": ("SMS gesendet", "SMS envoyé"),
    "SMS template sent": ("SMS-Vorlage gesendet", "Modèle SMS envoyé"),
    "Scan QR Code": ("QR-Code scannen", "Scanner le code QR"),
    "Scan the QR code on your physical gift to unlock!": (
        "Scanne den QR-Code auf deinem physischen Geschenk, um es freizuschalten!",
        "Scanne le code QR sur ton cadeau physique pour le débloquer !",
    ),
    "Scan the QR code to unlock!": ("QR-Code scannen zum Freischalten!", "Scanne le code QR pour débloquer !"),
    "Scanning...": ("Scannen …", "Analyse…"),
    "Schedule a time for your screening call": ("Termin für dein Screening-Gespräch festlegen", "Planifie un horaire pour ton appel de vérification"),
    "Scoring in progress...": ("Bewertung läuft …", "Notation en cours…"),
    "Secret Admirer Journeys": ("Geheime-Verehrer-Journeys", "Voyages d'admirateur·trice secret·ète"),
    "See review status": ("Prüfstatus ansehen", "Voir le statut d'examen"),
    "See you soon,": ("Bis bald,", "À bientôt,"),
    "Select reason...": ("Grund wählen …", "Sélectionner une raison…"),
    "Send Connection Request": ("Verbindungsanfrage senden", "Envoyer une demande de connexion"),
    "Send Note": ("Notiz senden", "Envoyer la note"),
    "Send SMS reminder": ("SMS-Erinnerung senden", "Envoyer un rappel SMS"),
    "Send a Crush Spark? A coach will review before it proceeds.": (
        "Einen Crush Spark senden? Ein Coach prüft ihn, bevor er weitergeht.",
        "Envoyer un Crush Spark ? Un coach l'examinera avant qu'il soit transmis.",
    ),
    "Send a note to your coach": ("Notiz an deinen Coach senden", "Envoyer une note à ton coach"),
    "Send reset link": ("Reset-Link senden", "Envoyer le lien de réinitialisation"),
    "Set Preferences": ("Vorlieben festlegen", "Définir les préférences"),
    "Set up your personality profile": ("Persönlichkeitsprofil einrichten", "Configure ton profil de personnalité"),
    "Share": ("Teilen", "Partager"),
    "Sign up with Apple": ("Mit Apple registrieren", "S'inscrire avec Apple"),
    "Since your profile is not yet verified, please confirm your age to register for this event.": (
        "Da dein Profil noch nicht verifiziert ist, bestätige bitte dein Alter, um dich für dieses Event anzumelden.",
        "Comme ton profil n'est pas encore vérifié, confirme ton âge pour t'inscrire à cet événement.",
    ),
    "Skip to main content": ("Zum Hauptinhalt springen", "Passer au contenu principal"),
    "Spark deadline passed": ("Spark-Frist abgelaufen", "Délai du spark dépassé"),
    "Sparks You've Received": ("Erhaltene Sparks", "Sparks reçus"),
    "Sparks You've Sent": ("Gesendete Sparks", "Sparks envoyés"),
    "Speed Dating Twist Results": ("Ergebnisse Speed-Dating-Twist", "Résultats de la touche speed dating"),
    "Speed Dating Twist: <strong>%(choice)s</strong>": ("Speed-Dating-Twist: <strong>%(choice)s</strong>", "Touche speed dating : <strong>%(choice)s</strong>"),
    "Step %(n)s of %(total)s: %(title)s": ("Schritt %(n)s von %(total)s: %(title)s", "Étape %(n)s sur %(total)s : %(title)s"),
    "Step 1": ("Schritt 1", "Étape 1"),
    "Step 1 of 5 - Let's get started with the basics!": ("Schritt 1 von 5 – Starten wir mit den Basics!", "Étape 1 sur 5 – Commençons par les bases !"),
    "Step 2": ("Schritt 2", "Étape 2"),
    "Step 2 of 5 - Tell us about yourself": ("Schritt 2 von 5 – Erzähle uns von dir", "Étape 2 sur 5 – Parle-nous de toi"),
    "Step 3": ("Schritt 3", "Étape 3"),
    "Step 3 of 5 - Add your photos": ("Schritt 3 von 5 – Füge deine Fotos hinzu", "Étape 3 sur 5 – Ajoute tes photos"),
    "Step 4": ("Schritt 4", "Étape 4"),
    "Step 4 of 5 - Choose your coach": ("Schritt 4 von 5 – Wähle deinen Coach", "Étape 4 sur 5 – Choisis ton coach"),
    "Step 4: See the Results": ("Schritt 4: Ergebnisse sehen", "Étape 4 : voir les résultats"),
    "Step 5 of 5 - Review and submit": ("Schritt 5 von 5 – Prüfen und absenden", "Étape 5 sur 5 – Relis et envoie"),
    "Submitted %(time)s ago": ("Eingereicht vor %(time)s", "Soumis il y a %(time)s"),
    "Subscription not active": ("Abo nicht aktiv", "Abonnement non actif"),
    "Templates": ("Vorlagen", "Modèles"),
    "Thanks for being part of the Crush.lu community!": ("Danke, dass du Teil der Crush.lu-Community bist!", "Merci de faire partie de la communauté Crush.lu !"),
    "The Results!": ("Die Ergebnisse!", "Les résultats !"),
    "The following email addresses are associated with your account:": (
        "Folgende E-Mail-Adressen sind mit deinem Konto verknüpft:",
        "Les adresses e-mail suivantes sont associées à ton compte :",
    ),
    "They've completed the journey and your identity has been revealed!": (
        "Sie haben die Journey abgeschlossen und deine Identität wurde enthüllt!",
        "Ils ont terminé le voyage et ton identité a été révélée !",
    ),
    "Think about what you're looking for in a partner": ("Überlege, was du bei einer Partnerin oder einem Partner suchst", "Réfléchis à ce que tu recherches chez un·e partenaire"),
    "This door's content is being prepared...": ("Der Inhalt dieses Türchens wird vorbereitet …", "Le contenu de cette porte est en cours de préparation…"),
    "This event requires an approved profile. Your profile is currently under review by our coaches.": (
        "Dieses Event setzt ein freigegebenes Profil voraus. Dein Profil wird gerade von unseren Coaches geprüft.",
        "Cet événement nécessite un profil approuvé. Ton profil est en cours d'examen par nos coachs.",
    ),
    "This password reset link is invalid or has expired": ("Dieser Reset-Link ist ungültig oder abgelaufen", "Ce lien de réinitialisation est invalide ou a expiré"),
    "This will be visible to your Crush Coach.": ("Dies ist für deinen Crush Coach sichtbar.", "Visible par ton Crush Coach."),
    "Time": ("Zeit", "Heure"),
    "Timeline": ("Zeitleiste", "Chronologie"),
    "Toggle navigation menu": ("Navigationsmenü umschalten", "Afficher/masquer le menu"),
    "Track your sent and received sparks": ("Deine gesendeten und empfangenen Sparks verfolgen", "Suis tes sparks envoyés et reçus"),
    "Unverified": ("Unverifiziert", "Non vérifié"),
    "Upcoming Event": ("Kommendes Event", "Événement à venir"),
    "Update Profile Languages": ("Profil-Sprachen aktualisieren", "Mettre à jour les langues du profil"),
    "Upload Photo": ("Foto hochladen", "Téléverser une photo"),
    "Upload background music for the Future Letter (MP3, WAV, M4A - max 10MB).": (
        "Lade Hintergrundmusik für den Zukunftsbrief hoch (MP3, WAV, M4A – max. 10 MB).",
        "Téléverse de la musique de fond pour la Lettre du Futur (MP3, WAV, M4A – max. 10 Mo).",
    ),
    "Upload from device": ("Vom Gerät hochladen", "Téléverser depuis l'appareil"),
    "Upload main profile photo": ("Haupt-Profilfoto hochladen", "Téléverser la photo de profil principale"),
    "Upload profile photo %(slot)s": ("Profilfoto %(slot)s hochladen", "Téléverser la photo de profil %(slot)s"),
    "Upload to replace": ("Zum Ersetzen hochladen", "Téléverser pour remplacer"),
    "Upload up to 5 photos for the slideshow.": ("Lade bis zu 5 Fotos für die Diashow hoch.", "Téléverse jusqu'à 5 photos pour le diaporama."),
    "Uploading...": ("Wird hochgeladen …", "Téléversement…"),
    "Verification History": ("Verifizierungshistorie", "Historique de vérification"),
    "Verify with LuxID": ("Mit LuxID verifizieren", "Vérifier avec LuxID"),
    "Verify your number above to continue.": ("Verifiziere deine Nummer oben, um fortzufahren.", "Vérifie ton numéro ci-dessus pour continuer."),
    "Verify your phone number": ("Telefonnummer verifizieren", "Vérifie ton numéro de téléphone"),
    "View Attendees & Connections": ("Teilnehmende & Verbindungen ansehen", "Voir participant·e·s & connexions"),
    "View profile": ("Profil ansehen", "Voir le profil"),
    "Vote on <strong>TWO categories</strong>: Presentation Style and Speed Dating Twist": (
        "Stimme über <strong>ZWEI Kategorien</strong> ab: Präsentationsstil und Speed-Dating-Twist",
        "Vote sur <strong>DEUX catégories</strong> : style de présentation et touche speed dating",
    ),
    "Votes Submitted!": ("Stimmen abgegeben!", "Votes envoyés !"),
    "Voting Has Ended": ("Abstimmung beendet", "Vote terminé"),
    "Voting Open": ("Abstimmung offen", "Vote ouvert"),
    "Voting has ended. You were not checked in so you could not participate in the vote.": (
        "Die Abstimmung ist beendet. Du warst nicht eingecheckt und konntest daher nicht mitstimmen.",
        "Le vote est terminé. Tu n'étais pas enregistré·e et n'as donc pas pu participer.",
    ),
    "Waiting for quiz to begin...": ("Warten auf Quiz-Start …", "En attente du début du quiz…"),
    "We never share your email address": ("Wir geben deine E-Mail-Adresse niemals weiter", "Nous ne partageons jamais ton adresse e-mail"),
    "We're matching you with the best available coach. You'll be notified once assigned.": (
        "Wir suchen den besten verfügbaren Coach für dich. Du wirst benachrichtigt, sobald zugewiesen.",
        "Nous te mettons en relation avec le meilleur coach disponible. Tu seras prévenu·e dès l'attribution.",
    ),
    "Welcome!": ("Willkommen!", "Bienvenue !"),
    "Welcome, %(name)s!": ("Willkommen, %(name)s!", "Bienvenue, %(name)s !"),
    "What You'll Vote On": ("Worüber du abstimmst", "Sur quoi tu votes"),
    "What can I do?": ("Was kann ich tun?", "Que puis-je faire ?"),
    "What did you talk about?": ("Worüber habt ihr gesprochen?", "De quoi avez-vous parlé ?"),
    "What fun twist will spice up the speed dating phase?": (
        "Welcher lustige Twist würzt die Speed-Dating-Phase?",
        "Quelle touche amusante va pimenter la phase speed dating ?",
    ),
    "What's New": ("Was ist neu", "Nouveautés"),
    "What's New on Crush.lu": ("Neuigkeiten auf Crush.lu", "Nouveautés sur Crush.lu"),
    "Where to find it": ("Wo du es findest", "Où le trouver"),
    "Why a coach?": ("Warum ein Coach?", "Pourquoi un coach ?"),
    "Write a message...": ("Nachricht schreiben …", "Écris un message…"),
    "Write feedback in:": ("Feedback verfassen in:", "Rédiger le retour en :"),
    "Yahoo Calendar": ("Yahoo Calendar", "Yahoo Calendar"),
    "You create an anonymous Wonderland journey for them": (
        "Du erstellst eine anonyme Wonderland-Journey für sie",
        "Tu crées un voyage Wonderland anonyme pour elle/lui",
    ),
    "You currently do not have any email addresses associated with your account.": (
        "Aktuell sind keine E-Mail-Adressen mit deinem Konto verknüpft.",
        "Aucune adresse e-mail n'est actuellement associée à ton compte.",
    ),
    "You do not have an active Crush.lu profile.": ("Du hast kein aktives Crush.lu-Profil.", "Tu n'as pas de profil Crush.lu actif."),
    "You have <strong>30 minutes</strong> to cast your votes": ("Du hast <strong>30 Minuten</strong>, um abzustimmen", "Tu as <strong>30 minutes</strong> pour voter"),
    "You must complete the screening call before submitting a review decision": (
        "Du musst das Screening-Gespräch abschließen, bevor du eine Prüfentscheidung abgibst",
        "Tu dois terminer l'appel de vérification avant de soumettre une décision d'examen",
    ),
    "You will be added to the waitlist. We'll notify you if a spot opens up.": (
        "Du wirst auf die Warteliste gesetzt. Wir melden uns, sobald ein Platz frei wird.",
        "Tu seras ajouté·e à la liste d'attente. Nous te préviendrons si une place se libère.",
    ),
    "You'll be notified when complete": ("Du wirst benachrichtigt, wenn es fertig ist", "Tu seras prévenu·e une fois terminé"),
    "You'll both need to consent before contact info is shared": (
        "Ihr müsst beide zustimmen, bevor Kontaktdaten geteilt werden",
        "Vous devez tous les deux consentir avant que les contacts soient partagés",
    ),
    "You'll vote on TWO categories. The most voted option in each shapes a different phase of the event!": (
        "Du stimmst über ZWEI Kategorien ab. Die meistgewählte Option je Kategorie gestaltet eine andere Event-Phase!",
        "Tu voteras sur DEUX catégories. L'option la plus choisie dans chacune façonne une phase différente de l'événement !",
    ),
    "You're about to accept your exclusive invitation to:": (
        "Du bist dabei, deine exklusive Einladung anzunehmen zu:",
        "Tu es sur le point d'accepter ton invitation exclusive à :",
    ),
    "You're checked in!": ("Du bist eingecheckt!", "Tu es enregistré·e !"),
    "You're literally one click away! Just review and submit your profile.": (
        "Du bist buchstäblich einen Klick entfernt! Prüfe dein Profil und reiche es ein.",
        "Tu es littéralement à un clic ! Relis ton profil et soumets-le.",
    ),
    "You're receiving this because your coach tried to reach you for a screening call.": (
        "Du erhältst diese Nachricht, weil dein Coach dich für ein Screening-Gespräch erreichen wollte.",
        "Tu reçois ce message parce que ton coach a essayé de te joindre pour un appel de vérification.",
    ),
    "You're receiving this email because a password reset was requested for your Crush.lu account.": (
        "Du erhältst diese E-Mail, weil für dein Crush.lu-Konto eine Passwort-Zurücksetzung angefordert wurde.",
        "Tu reçois cet e-mail car une réinitialisation de mot de passe a été demandée pour ton compte Crush.lu.",
    ),
    "You've Voted on Both Categories!": ("Du hast in beiden Kategorien abgestimmt!", "Tu as voté dans les deux catégories !"),
    "Your Advent Calendar Awaits!": ("Dein Adventskalender wartet!", "Ton calendrier de l'Avent t'attend !"),
    "Your Consent Status": ("Dein Zustimmungsstatus", "Statut de ton consentement"),
    "Your Crush Spark has been approved! Now create an anonymous Wonderland journey for them!": (
        "Dein Crush Spark wurde freigegeben! Erstelle jetzt eine anonyme Wonderland-Journey für diese Person!",
        "Ton Crush Spark a été approuvé ! Crée maintenant un voyage Wonderland anonyme pour cette personne !",
    ),
    "Your Event Coaches": ("Deine Event-Coaches", "Tes coachs d'événement"),
    "Your Ideal Crush": ("Dein idealer Crush", "Ton Crush idéal·e"),
    "Your Ideal Crush - Crush.lu": ("Dein idealer Crush – Crush.lu", "Ton Crush idéal·e – Crush.lu"),
    "Your Information": ("Deine Informationen", "Tes informations"),
    "Your Matches": ("Deine Matches", "Tes affinités"),
    "Your Matches - Crush.lu": ("Deine Matches – Crush.lu", "Tes affinités – Crush.lu"),
    "Your Vote": ("Deine Stimme", "Ton vote"),
    "Your browser does not support the video tag.": ("Dein Browser unterstützt das Video-Tag nicht.", "Ton navigateur ne prend pas en charge la balise vidéo."),
    "Your changes have been saved.": ("Deine Änderungen wurden gespeichert.", "Tes modifications ont été enregistrées."),
    "Your coach is waiting to hear from you": ("Dein Coach wartet auf deine Antwort", "Ton coach attend de tes nouvelles"),
    "Your coach needs to speak with you": ("Dein Coach möchte mit dir sprechen", "Ton coach a besoin de te parler"),
    "Your description": ("Deine Beschreibung", "Ta description"),
    "Your gift": ("Dein Geschenk", "Ton cadeau"),
    "Your main tab is ready to use.": ("Dein Haupt-Tab ist einsatzbereit.", "Ton onglet principal est prêt à l'emploi."),
    "Your membership card awaits": ("Deine Mitgliedskarte wartet", "Ta carte de membre t'attend"),
    "Your note:": ("Deine Notiz:", "Ta note :"),
    "Your phone number is verified.": ("Deine Telefonnummer ist verifiziert.", "Ton numéro de téléphone est vérifié."),
    "Your primary photo": ("Dein Hauptfoto", "Ta photo principale"),
    "Your screening call is done and your profile is live. Head to the dashboard to find your first event.": (
        "Dein Screening-Gespräch ist erledigt und dein Profil ist aktiv. Geh zum Dashboard, um dein erstes Event zu finden.",
        "Ton appel de vérification est terminé et ton profil est actif. Rends-toi sur le tableau de bord pour trouver ton premier événement.",
    ),
    "Your session is secure": ("Deine Sitzung ist sicher", "Ta session est sécurisée"),
    "Your spark is being reviewed by a coach. You'll be notified once it's approved.": (
        "Dein Spark wird von einem Coach geprüft. Du wirst benachrichtigt, sobald er freigegeben ist.",
        "Ton spark est en cours d'examen par un coach. Tu seras prévenu·e dès son approbation.",
    ),
    "Your vote has been recorded. Thank you!": ("Deine Stimme wurde gespeichert. Danke!", "Ton vote a été enregistré. Merci !"),
    "all accepted": ("alle angenommen", "tous acceptés"),
    "checked in": ("eingecheckt", "enregistré·e·s"),
    "completed": ("abgeschlossen", "terminé"),
    "has created a journey for you!": ("hat eine Journey für dich erstellt!", "a créé un voyage pour toi !"),
    "optional": ("optional", "facultatif"),
    "past event": ("vergangenes Event", "événement passé"),
    "required complete": ("erforderlich abgeschlossen", "obligatoire effectué"),
    "saved": ("gespeichert", "enregistré"),
    "wants to connect": ("möchte sich verbinden", "veut se connecter"),
    "years": ("Jahre", "ans"),
    "— Privacy-first meetups in Luxembourg": ("– Privatsphärenorientierte Meetups in Luxemburg", "– Des rencontres axées sur la confidentialité au Luxembourg"),
}


# ============ Apply logic ============

def escape_po(s):
    return (s.replace('\\', '\\\\')
             .replace('"', '\\"')
             .replace('\n', '\\n')
             .replace('\t', '\\t'))


def wrap_po_string(s, prefix='msgstr ', width=76):
    esc = escape_po(s)
    if '\\n' not in esc and len(prefix) + 2 + len(esc) <= width:
        return [f'{prefix}"{esc}"']
    parts = esc.split('\\n')
    segments = [p + '\\n' for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    lines = [f'{prefix}""']
    for seg in segments:
        lines.append(f'"{seg}"')
    return lines


def parse_entries(lines):
    i = 0
    n = len(lines)
    while i < n:
        while i < n and lines[i].strip() == '':
            i += 1
        if i >= n:
            break
        start = i
        while i < n and lines[i].strip() != '':
            i += 1
        yield start, i


def extract_quoted(line):
    m = re.match(r'^[^"]*"(.*)"\s*$', line)
    if not m:
        return None
    raw = m.group(1)
    return (raw.replace('\\"', '"')
               .replace('\\n', '\n')
               .replace('\\t', '\t')
               .replace('\\\\', '\\'))


def collect_string(block, first_idx):
    pieces = [extract_quoted(block[first_idx]) or '']
    k = first_idx + 1
    while k < len(block) and block[k].startswith('"'):
        pieces.append(extract_quoted(block[k]) or '')
        k += 1
    return ''.join(pieces), k - 1


def apply_file(path, lang):
    text = Path(path).read_text(encoding='utf-8')
    has_crlf = '\r\n' in text[:4000]
    if has_crlf:
        text = text.replace('\r\n', '\n')
    lines = text.split('\n')
    applied = 0
    replacements = []

    for start, end in list(parse_entries(lines)):
        block = lines[start:end]
        # Find fuzzy flag comment line(s) and msgid/msgstr
        flag_line_idx = None
        prev_msgid_idx = None
        msgid_start = None
        msgstr_start = None
        is_plural = False
        for j, ln in enumerate(block):
            if ln.startswith('#,') and 'fuzzy' in ln:
                flag_line_idx = j
            elif ln.startswith('#| '):
                if prev_msgid_idx is None:
                    prev_msgid_idx = j
            elif ln.startswith('msgid_plural "') or ln.startswith('msgstr['):
                is_plural = True
            elif ln.startswith('msgid "'):
                msgid_start = j
            elif ln.startswith('msgstr "'):
                msgstr_start = j

        if is_plural:
            # Plural fuzzies (msgstr[0]/[1]) aren't supported — T can't model them.
            if msgid_start is not None:
                msgid_val, _ = collect_string(block, msgid_start)
                if msgid_val in T:
                    print(f'  WARNING: skipped plural fuzzy (not supported): {msgid_val[:80]!r}')
            continue

        if msgid_start is None or msgstr_start is None or flag_line_idx is None:
            continue

        msgid_val, _ = collect_string(block, msgid_start)
        if msgid_val not in T:
            continue
        spec = T[msgid_val]
        idx = 0 if lang == 'de' else 1
        translation = spec[idx]
        if translation is None:
            continue

        # Build new block:
        # - Remove the 'fuzzy' flag (but keep other flags like python-format)
        # - Remove all #| previous_msgid lines
        # - Replace msgstr with new translation
        _, msgstr_last = collect_string(block, msgstr_start)

        new_block = []
        for j, ln in enumerate(block):
            if j == flag_line_idx:
                # Strip fuzzy from flag line
                flags = [f.strip() for f in ln[2:].split(',') if f.strip() and f.strip() != 'fuzzy']
                if flags:
                    new_block.append('#, ' + ', '.join(flags))
                # else: omit the line
                continue
            if ln.startswith('#| '):
                # Skip previous-msgid tracking lines
                continue
            if msgstr_start <= j <= msgstr_last:
                if j == msgstr_start:
                    new_block.extend(wrap_po_string(translation, prefix='msgstr '))
                continue
            new_block.append(ln)

        replacements.append((start, end, new_block))
        applied += 1

    for start, end, new_block in sorted(replacements, key=lambda r: -r[0]):
        lines[start:end] = new_block
    new_text = '\n'.join(lines)
    if has_crlf:
        new_text = new_text.replace('\n', '\r\n')
    Path(path).write_text(new_text, encoding='utf-8', newline='')
    print(f'{lang.upper()}: applied {applied} fuzzy rewrites -> {path}')


print(f'Table: {len(T)}')
apply_file('crush_lu/locale/de/LC_MESSAGES/django.po', 'de')
apply_file('crush_lu/locale/fr/LC_MESSAGES/django.po', 'fr')
