#!/usr/bin/env python
"""
French translation script for Crush.lu
Translates all untranslated entries in the French .po file
Uses informal "tu" form for Gen Z/Millennial audience
"""

import polib
from pathlib import Path

# Comprehensive translation dictionary
TRANSLATIONS = {
    # Admin actions - fuzzy entries to fix
    "📋 Export attendees to CSV": "📋 Exporter les participants en CSV",
    "📋 Export selected registrations to CSV": "📋 Exporter les inscriptions sélectionnées en CSV",
    "✅ Confirm selected registrations": "✅ Confirmer les inscriptions sélectionnées",
    "⏳ Move to waitlist": "⏳ Déplacer vers la liste d'attente",
    "📋 Export selected submissions to CSV": "📋 Exporter les soumissions sélectionnées en CSV",
    "Export selected submissions to CSV": "Exporter les soumissions sélectionnées en CSV",

    # Fixed fuzzy entries from the list
    "Languages for Events": "Langues pour les événements",
    "Invalid language selection: %(lang)s": "Sélection de langue invalide : %(lang)s",
    "Voice Message": "Message vocal",
    "Video Message (Alternative)": "Message vidéo (alternatif)",
    "The Wonderland of You": "Le Wonderland de toi",
    "Custom Journey": "Parcours personnalisé",
    "Medium": "Moyen",
    "Timeline Sorting": "Tri chronologique",
    "Interactive Story Choice": "Choix d'histoire interactive",
    "Open Text Response": "Réponse texte libre",
    "Constellation Drawing": "Dessin de constellation",
    "Voice Recording": "Enregistrement vocal",
    "Video Message": "Message vidéo",
    "Photo Slideshow": "Diaporama photo",
    "Completion Certificate": "Certificat de complétion",
    "Phone Verification": "Vérification téléphonique",
    "Profile Photo": "Photo de profil",
    "Review Decision": "Décision d'examen",
    "Decision": "Décision",
    "Internal Notes": "Notes internes",
    "Quick notes:": "Notes rapides :",
    "ID verified": "Identité vérifiée",
    "Photos match": "Photos correspondent",
    "Photo issues": "Problèmes de photo",
    "visible if rejected/revision": "visible si rejeté/révision",
    "Better photo": "Meilleure photo",
    "More bio": "Plus de bio",
    "Submit Review": "Soumettre l'examen",
    "Review Guidelines": "Directives d'examen",

    # Gift/reward related
    "Marked {count} gift(s) as expired": "{count} cadeau(x) marqué(s) comme expiré(s)",

    # API messages - empty entries
    "Missing challenge ID or answer": "ID du défi ou réponse manquant",
    "Challenge not found": "Défi introuvable",
    "No active journey found": "Aucun parcours actif trouvé",
    "You already completed this challenge!": "Tu as déjà terminé ce défi !",
    "Correct! Well done! 🎉": "Correct ! Bien joué ! 🎉",
    "Not quite right. Try again! 💪": "Pas tout à fait. Réessaie ! 💪",
    "An error occurred processing your answer": "Une erreur s'est produite lors du traitement de ta réponse",
    "Missing challenge ID or hint number": "ID du défi ou numéro d'indice manquant",
    "Invalid hint number": "Numéro d'indice invalide",
    "Hint not available": "Indice non disponible",
    "An error occurred unlocking the hint": "Une erreur s'est produite lors du déverrouillage de l'indice",
    "An error occurred retrieving progress": "Une erreur s'est produite lors de la récupération de la progression",
    "An error occurred saving state": "Une erreur s'est produite lors de la sauvegarde de l'état",
    "Invalid response choice": "Choix de réponse invalide",
    "Response recorded": "Réponse enregistrée",
    "An error occurred recording your response": "Une erreur s'est produite lors de l'enregistrement de ta réponse",
    "Missing reward ID or piece index": "ID de récompense ou indice de pièce manquant",
    "Reward not found": "Récompense introuvable",
    "This piece is already unlocked": "Cette pièce est déjà déverrouillée",
    "Not enough points! You need %(points)s points to unlock this piece.": "Pas assez de points ! Tu as besoin de %(points)s points pour déverrouiller cette pièce.",
    "Piece unlocked! -%(points)s points": "Pièce déverrouillée ! -%(points)s points",
    "An error occurred unlocking the piece": "Une erreur s'est produite lors du déverrouillage de la pièce",
    "An error occurred retrieving reward progress": "Une erreur s'est produite lors de la récupération de la progression des récompenses",

    # Email/gift
    "{sender_name} has created a magical journey for you!": "{sender_name} a créé un parcours magique pour toi !",

    # Form fields and labels - common patterns
    "Phone": "Téléphone",
    "Date of Birth": "Date de naissance",
    "Gender": "Genre",
    "Male": "Homme",
    "Female": "Femme",
    "Non-binary": "Non-binaire",
    "Other": "Autre",
    "Prefer not to say": "Préfère ne pas dire",
    "Bio": "Biographie",
    "About me": "À propos de moi",
    "Looking for": "Je recherche",
    "Interests": "Centres d'intérêt",
    "Height": "Taille",
    "Education": "Éducation",
    "Job title": "Titre du poste",
    "Company": "Entreprise",
    "Location": "Localisation",
    "City": "Ville",
    "Canton": "Canton",
    "Languages": "Langues",
    "Smoking": "Fumeur",
    "Drinking": "Alcool",
    "Children": "Enfants",
    "Wants children": "Souhaite avoir des enfants",
    "Religion": "Religion",
    "Politics": "Politique",

    # Privacy settings
    "Privacy Settings": "Paramètres de confidentialité",
    "Show full name": "Afficher le nom complet",
    "Show exact age": "Afficher l'âge exact",
    "Blur photos": "Flouter les photos",
    "Hide my profile": "Masquer mon profil",

    # Event types and details
    "Speed Dating": "Speed Dating",
    "Mixer": "Soirée rencontre",
    "Activity": "Activité",
    "Themed": "Thématique",
    "Event Type": "Type d'événement",
    "Date and Time": "Date et heure",
    "Venue": "Lieu",
    "Address": "Adresse",
    "Description": "Description",
    "Max Participants": "Participants maximum",
    "Registration Fee": "Frais d'inscription",
    "Registration Deadline": "Date limite d'inscription",
    "Min Age": "Âge minimum",
    "Max Age": "Âge maximum",
    "Dietary Restrictions": "Restrictions alimentaires",
    "Special Requests": "Demandes spéciales",

    # Registration status
    "Confirmed": "Confirmé",
    "Waitlist": "Liste d'attente",
    "Cancelled": "Annulé",
    "Attended": "Présent",
    "No-show": "Absent",

    # Connection/messaging
    "Request Connection": "Demander une connexion",
    "Accept": "Accepter",
    "Decline": "Refuser",
    "Connected": "Connecté",
    "Message": "Message",
    "Send Message": "Envoyer un message",
    "Type your message": "Tape ton message",
    "No messages yet": "Pas encore de messages",
    "Mark as read": "Marquer comme lu",
    "Mark as unread": "Marquer comme non lu",

    # Journey/challenge types
    "Riddle": "Énigme",
    "Multiple Choice": "Choix multiple",
    "Word Scramble": "Anagramme",
    "Timeline Sort": "Tri chronologique",
    "Would You Rather": "Tu préfères",
    "Open Text": "Texte libre",

    # Reward types
    "Poem": "Poème",
    "Photo Reveal": "Révélation de photo",
    "Future Letter": "Lettre future",
    "Audio": "Audio",
    "Video": "Vidéo",

    # Common UI elements
    "Loading...": "Chargement...",
    "Please wait": "Merci de patienter",
    "Try again": "Réessayer",
    "Go back": "Retour",
    "Continue": "Continuer",
    "Skip": "Passer",
    "Submit": "Envoyer",
    "Save": "Enregistrer",
    "Save Changes": "Enregistrer les modifications",
    "Cancel": "Annuler",
    "Delete": "Supprimer",
    "Edit": "Modifier",
    "Update": "Mettre à jour",
    "Close": "Fermer",
    "View": "Voir",
    "View Details": "Voir les détails",
    "Show More": "Afficher plus",
    "Show Less": "Afficher moins",
    "Read More": "Lire plus",
    "Read Less": "Lire moins",

    # Status messages
    "Success": "Succès",
    "Error": "Erreur",
    "Warning": "Attention",
    "Info": "Info",
    "Required": "Requis",
    "Optional": "Optionnel",
    "This field is required": "Ce champ est requis",
    "Please enter a valid email": "Merci de saisir une adresse e-mail valide",
    "Please enter a valid phone number": "Merci de saisir un numéro de téléphone valide",
    "Password too short": "Mot de passe trop court",
    "Passwords do not match": "Les mots de passe ne correspondent pas",

    # Date/time
    "Today": "Aujourd'hui",
    "Tomorrow": "Demain",
    "Yesterday": "Hier",
    "This week": "Cette semaine",
    "This month": "Ce mois-ci",
    "This year": "Cette année",
    "Just now": "À l'instant",
    "minutes ago": "minutes",
    "hours ago": "heures",
    "days ago": "jours",

    # Navigation
    "Home": "Accueil",
    "Dashboard": "Tableau de bord",
    "Profile": "Profil",
    "Events": "Événements",
    "Connections": "Connexions",
    "Messages": "Messages",
    "Settings": "Paramètres",
    "Help": "Aide",
    "Logout": "Déconnexion",
    "Login": "Connexion",
    "Sign up": "Inscription",
    "Register": "S'inscrire",

    # Account settings
    "Account Settings": "Paramètres du compte",
    "Change Password": "Changer le mot de passe",
    "Current Password": "Mot de passe actuel",
    "New Password": "Nouveau mot de passe",
    "Confirm New Password": "Confirmer le nouveau mot de passe",
    "Email Notifications": "Notifications par e-mail",
    "Push Notifications": "Notifications push",
    "Language": "Langue",
    "Preferred Language": "Langue préférée",
    "Delete Account": "Supprimer le compte",
    "Deactivate Account": "Désactiver le compte",

    # Coach panel
    "Coach Dashboard": "Tableau de bord coach",
    "Profile Reviews": "Examens de profils",
    "Pending Reviews": "Examens en attente",
    "Approved": "Approuvé",
    "Rejected": "Rejeté",
    "Revision Requested": "Révision demandée",
    "Screening Call": "Appel de vérification",
    "Call Completed": "Appel terminé",
    "Coach Notes": "Notes du coach",
    "Feedback to User": "Commentaires pour l'utilisateur",
    "Assign Coach": "Assigner un coach",
    "Review Profile": "Examiner le profil",
    "Approve Profile": "Approuver le profil",
    "Reject Profile": "Rejeter le profil",
    "Request Revision": "Demander une révision",

    # Wallet/passes
    "Add to Apple Wallet": "Ajouter à Apple Wallet",
    "Add to Google Wallet": "Ajouter à Google Wallet",
    "Wallet Pass": "Pass Wallet",
    "Member Card": "Carte de membre",
    "VIP": "VIP",
    "Premium": "Premium",
    "Standard": "Standard",

    # Referrals
    "Invite Friends": "Inviter des amis",
    "Your Referral Code": "Ton code de parrainage",
    "Share your code": "Partage ton code",
    "Referrals": "Parrainages",
    "Total Referrals": "Total de parrainages",

    # Yes/No
    "Yes": "Oui",
    "No": "Non",
    "Maybe": "Peut-être",

    # Untranslated entries from the list
    "Which languages can you speak at in-person events?": "Quelles langues peux-tu parler lors des événements en personne ?",
    "e.g., Verified identity via video call. Photos match. Genuineinterest in dating.": "Ex : Identité vérifiée par appel vidéo. Photos correspondent. Intérêt sincère pour les rencontres.",
    "e.g., Welcome to Crush.lu! Your profile looks great...": "Ex : Bienvenue sur Crush.lu ! Ton profil est super...",
    "Photo Puzzle Image": "Image du puzzle photo",
    "This image will be revealed as a puzzle in Chapter 1. Recommended:800x800px square image.": "Cette image sera révélée sous forme de puzzle au Chapitre 1. Recommandé : image carrée 800x800px.",
    "Slideshow Photo 1": "Photo diaporama 1",
    "First photo for the Chapter 3 slideshow.": "Première photo pour le diaporama du Chapitre 3.",
    "Slideshow Photo 2": "Photo diaporama 2",
    "Slideshow Photo 3": "Photo diaporama 3",
    "Slideshow Photo 4": "Photo diaporama 4",
    "Slideshow Photo 5": "Photo diaporama 5",
    "Record a personal voice message for Chapter 4. Formats: MP3, WAV, M4A(max 10MB).": "Enregistre un message vocal personnel pour le Chapitre 4. Formats : MP3, WAV, M4A (max 10MB).",
    "Alternatively, record a video message. Formats: MP4, MOV (max 50MB).": "Alternativement, enregistre un message vidéo. Formats : MP4, MOV (max 50MB).",
    "Image file size must be less than %(max_size)s MB.": "La taille du fichier image doit être inférieure à %(max_size)s MB.",
    "Audio file size must be less than 10 MB.": "La taille du fichier audio doit être inférieure à 10 MB.",
    "Invalid audio format. Please use MP3, WAV, or M4A files.": "Format audio invalide. Merci d'utiliser des fichiers MP3, WAV ou M4A.",
    "Video file size must be less than 50 MB.": "La taille du fichier vidéo doit être inférieure à 50 MB.",
    "Invalid video format. Please use MP4 or MOV files.": "Format vidéo invalide. Merci d'utiliser des fichiers MP4 ou MOV.",
    "Wonderland Night (Dark starry sky)": "Nuit au Wonderland (ciel étoilé sombre)",
    "Enchanted Garden (Flowers & butterflies)": "Jardin enchanté (fleurs et papillons)",
    "Art Gallery (Golden frames & vintage)": "Galerie d'art (cadres dorés et vintage)",
    "Carnival (Warm lights & mirrors)": "Carnaval (lumières chaudes et miroirs)",
    "Starlit Observatory (Deep space & cosmos)": "Observatoire étoilé (espace profond et cosmos)",
    "Magical Door (Sunrise & celebration)": "Porte magique (lever du soleil et célébration)",
    "Easy": "Facile",
    "Hard": "Difficile",
    "Memory Matching Game": "Jeu de mémoire",
    "Photo Jigsaw Puzzle": "Puzzle photo",
    "Star Catcher Mini-Game": "Mini-jeu attrape-étoiles",
    "Photo Reveal (Jigsaw)": "Révélation photo (puzzle)",
    "Poem/Letter": "Poème/Lettre",
    "Photo for the Chapter 1 puzzle reveal (recommended: 800x800px)": "Photo pour le puzzle du Chapitre 1 (recommandé : 800x800px)",
    "First slideshow photo": "Première photo du diaporama",
    "Second slideshow photo": "Deuxième photo du diaporama",
    "Third slideshow photo": "Troisième photo du diaporama",
    "Fourth slideshow photo": "Quatrième photo du diaporama",
    "Fifth slideshow photo": "Cinquième photo du diaporama",
    "Voice message audio file (MP3, WAV, M4A - max 10MB)": "Fichier audio du message vocal (MP3, WAV, M4A - max 10MB)",
    "Video message file (MP4, MOV - max 50MB)": "Fichier vidéo du message (MP4, MOV - max 50MB)",
    "Direct link to user (bypasses name matching). Used for giftedjourneys.": "Lien direct vers l'utilisateur (contourne la correspondance des noms). Utilisé pour les parcours offerts.",
    "Lëtzebuergesch": "Lëtzebuergesch",
    "Languages the user can speak at in-person events": "Langues que l'utilisateur peut parler lors des événements en personne",
    "Select the appropriate review outcome": "Sélectionner le résultat de l'examen approprié",
    "not visible to user": "non visible pour l'utilisateur",
    "Genuine": "Authentique",
    "Suspicious": "Suspect",
    "Templates:": "Modèles :",
    "Look for red flags or suspicious content": "Chercher des signaux d'alarme ou du contenu suspect",
    "Not quite right! Try a different answer.": "Pas tout à fait ! Essaie une autre réponse.",
    "Write at least 10 characters to submit your response": "Écris au moins 10 caractères pour soumettre ta réponse",

    # Remaining fuzzy entries to fix
    "Complete screening call first!": "Complète d'abord l'appel de vérification !",
    "Verify photos are clear and appropriate": "Vérifie que les photos sont nettes et appropriées",
    "Ensure bio is genuine and respectful": "Assure-toi que la bio est authentique et respectueuse",
    "Confirm age verification (18+)": "Confirme la vérification d'âge (18+)",
    "Your phone number is verified": "Ton numéro de téléphone est vérifié",
    "Please fill in the following required fields:": "Merci de remplir les champs obligatoires suivants :",
    "A Magical Gift Awaits": "Un cadeau magique t'attend",
    "Correct!": "Correct !",
    "Thank you for sharing!": "Merci de partager !",
    "Points Earned:": "Points gagnés :",
    "%(points)s points available": "%(points)s points disponibles",
    "You already answered this question and earned <strong>": "Tu as déjà répondu à cette question et gagné <strong>",
    "Answer options": "Options de réponse",
    "Checking...": "Vérification...",
    "Share your thoughts freely - there are no wrong answers": "Partage tes pensées librement - il n'y a pas de mauvaises réponses",
    "Not sure what to write? Consider:": "Pas sûr de quoi écrire ? Considère :",
    "Your response": "Ta réponse",
    "You already solved this riddle and earned <strong>": "Tu as déjà résolu cette énigme et gagné <strong>",
    "Your answer": "Ta réponse",
    "Select a date using the calendar": "Sélectionne une date avec le calendrier",
    "You already sorted this timeline correctly and earned <strong>": "Tu as déjà trié cette chronologie correctement et gagné <strong>",
    "You already unscrambled this word and earned <strong>": "Tu as déjà déchiffré ce mot et gagné <strong>",
    "Enter the unscrambled word or phrase": "Saisis le mot ou la phrase déchiffrée",
    "You've already shared your choice and earned <strong>": "Tu as déjà partagé ton choix et gagné <strong>",
    "Choose an option": "Choisis une option",
    "Submit Choice": "Soumettre le choix",
    "Navigation": "Navigation",
    "Completed Challenges": "Défis terminés",
    "The Final Question": "La question finale",
    "Yes, let's see where this goes": "Oui, voyons où ça mène",

    # Remaining untranslated entries
    "Not quite right. Try again!": "Pas tout à fait. Réessaie !",
    "Perfect!": "Parfait !",
    "Not quite right. Try rearranging the events!": "Pas tout à fait. Essaie de réorganiser les événements !",
    "Sortable timeline events": "Événements de chronologie triables",
    "Great choice!": "Super choix !",
    "Network error. Please check your connection and try again.": "Erreur réseau. Vérifie ta connexion et réessaie.",
    "Next: Add Media": "Suivant : Ajouter des médias",
    "You can skip media and create a basic gift": "Tu peux passer les médias et créer un cadeau basique",
    "Add photos and voice/video messages to make the journey extraspecial. These will be revealed as rewards when your recipientcompletes challenges!": "Ajoute des photos et des messages vocaux/vidéo pour rendre le parcours extra spécial. Ils seront révélés en tant que récompenses lorsque ton destinataire complète les défis !",
    "Chapter 1: Photo Puzzle": "Chapitre 1 : Puzzle photo",
    "This photo will be revealed piece by piece as a puzzle. Best with asquare image (800x800px).": "Cette photo sera révélée morceau par morceau comme un puzzle. Meilleur avec une image carrée (800x800px).",
    "Click or drag to upload photo": "Clique ou glisse pour télécharger une photo",
    "Add up to 5 photos for a beautiful slideshow reveal. Add memories ofyour time together!": "Ajoute jusqu'à 5 photos pour une belle révélation en diaporama. Ajoute des souvenirs de votre temps ensemble !",
    "Record a heartfelt voice or video message. This will be the emotionalfinale of the journey!": "Enregistre un message vocal ou vidéo sincère. Ce sera la finale émotionnelle du parcours !",
    "MP3, WAV, M4A (max 10MB)": "MP3, WAV, M4A (max 10MB)",
    "MP4, MOV (max 50MB)": "MP4, MOV (max 50MB)",
    "Congratulations on completing your journey! View your personalizedcompletion certificate.": "Félicitations pour avoir terminé ton parcours ! Consulte ton certificat de complétion personnalisé.",
    "Photo puzzle - click pieces to reveal": "Puzzle photo - clique sur les morceaux pour révéler",
    "locked, click to unlock for 50 points": "verrouillé, clique pour déverrouiller pour 50 points",
    "unlocked": "déverrouillé",
    "Unlock this piece for 50 points?": "Déverrouiller ce morceau pour 50 points ?",
    "Security token missing. Please refresh the page.": "Jeton de sécurité manquant. Merci de rafraîchir la page.",
    "Piece unlocked! -50 points": "Morceau déverrouillé ! -50 points",
    "Not enough points! You need": "Pas assez de points ! Tu as besoin de",
    "Press play to watch something special...": "Appuie sur play pour regarder quelque chose de spécial...",
    "Read the message below": "Lis le message ci-dessous",
    "We'll send a verification code via SMS to confirm your phone number.": "Nous enverrons un code de vérification par SMS pour confirmer ton numéro de téléphone.",
    "Select your country and enter your phone number": "Sélectionne ton pays et saisis ton numéro de téléphone",
    "A 6-digit code has been sent to": "Un code à 6 chiffres a été envoyé à",
    "Submission not found or not assigned to you.": "Soumission introuvable ou non assignée à toi.",
    "A magical 6-chapter adventure through the Wonderland of You": "Une aventure magique en 6 chapitres à travers le Wonderland de toi",
    "24 doors of surprises waiting to be discovered": "24 portes de surprises à découvrir",
    "Complete all challenges to unlock this reward.": "Termine tous les défis pour déverrouiller cette récompense.",
    "Complete the journey to unlock your certificate.": "Termine le parcours pour déverrouiller ton certificat.",

    # Final batch of fuzzy entries
    "I need to think about this": "J'ai besoin d'y réfléchir",
    "Your Response": "Ta réponse",
    "Rewards Unlocked": "Récompenses déverrouillées",
    "Chapter navigation": "Navigation des chapitres",
    "Submitting your response...": "Envoi de ta réponse...",
    "Thank you for your response!": "Merci pour ta réponse !",
    "Story Details": "Détails de l'histoire",
    "Add Media": "Ajouter des médias",
    "Optional:": "Optionnel :",
    "Chapter 3: Photo Slideshow": "Chapitre 3 : Diaporama photo",
    "Chapter 4: Personal Message": "Chapitre 4 : Message personnel",
    "A Magical Journey Awaits...": "Un parcours magique t'attend...",
    "has created a Wonderland journey for": "a créé un parcours Wonderland pour",
    "Share this magical journey with": "Partage ce parcours magique avec",
    "Share Your Gift": "Partage ton cadeau",
    "Download QR": "Télécharger le QR",
    "More Options...": "Plus d'options...",
    "When they scan the QR code or click the link, they'll see yourpersonalized message and can begin their Wonderland journey!": "Quand ils scannent le code QR ou cliquent sur le lien, ils verront ton message personnalisé et pourront commencer leur parcours Wonderland !",
    "Journey progress": "Progression du parcours",
    "Journey chapters": "Chapitres du parcours",
    "Locked": "Verrouillé",
    "Chapter %(num)s": "Chapitre %(num)s",
    "Revisit Chapter": "Revisiter le chapitre",
    "Continue Journey": "Continuer le parcours",
    "Start Chapter": "Commencer le chapitre",
    "Journey Complete!": "Parcours terminé !",
    "You already completed this challenge and earned <strong>%(points)spoints</strong>.": "Tu as déjà terminé ce défi et gagné <strong>%(points)s points</strong>.",
    "Hint 1 Used": "Indice 1 utilisé",
    "Hint 1 (-%(cost)s pts)": "Indice 1 (-%(cost)s pts)",
    "Hint 2 Used": "Indice 2 utilisé",
    "Hint 2 (-%(cost)s pts)": "Indice 2 (-%(cost)s pts)",
    "Hint 3 Used": "Indice 3 utilisé",
    "Hint 3 (-%(cost)s pts)": "Indice 3 (-%(cost)s pts)",
    "Hint": "Indice",
    "Your Certificate Awaits!": "Ton certificat t'attend !",
    "View Certificate": "Voir le certificat",
    "Click on pieces to unlock them! Each piece costs": "Clique sur les morceaux pour les déverrouiller ! Chaque morceau coûte",
    "Puzzle completion progress": "Progression du puzzle",
    "Puzzle piece": "Morceau de puzzle",
    "Photo Revealed!": "Photo révélée !",
    "Tip: Download this special memory to keep forever!": "Astuce : Télécharge ce souvenir spécial pour le garder pour toujours !",
    "points.": "points.",
    "A special memory awaits...": "Un souvenir spécial t'attend...",
    "With all my heart": "De tout mon cœur",
    "Your browser does not support the video element.": "Ton navigateur ne supporte pas l'élément vidéo.",
    "A special message, just for you...": "Un message spécial, rien que pour toi...",
    "Verify Your Phone Number": "Vérifie ton numéro de téléphone",
    "Send Verification Code": "Envoyer le code de vérification",
    "Sending...": "Envoi...",
    "Verification Code": "Code de vérification",
    "Verifying...": "Vérification...",
    "Resend code in": "Renvoyer le code dans",
    "Change Phone Number": "Changer le numéro de téléphone",
    "Your number": "Ton numéro",
    "has been verified.": "a été vérifié.",
    "Submission not found.": "Soumission introuvable.",
    "No special journey found for your account.": "Aucun parcours spécial trouvé pour ton compte.",
    "Welcome! Your journey is being prepared.": "Bienvenue ! Ton parcours est en préparation.",
    "An error occurred loading your journey. Please contact support ifthis persists.": "Une erreur s'est produite lors du chargement de ton parcours. Contacte le support si cela persiste.",
    "No active journey found.": "Aucun parcours actif trouvé.",
    "Please complete Chapter %(chapter)s first.": "Merci de terminer d'abord le Chapitre %(chapter)s.",
    "An error occurred loading Chapter %(chapter)s. Please try again orcontact support.": "Une erreur s'est produite lors du chargement du Chapitre %(chapter)s. Réessaie ou contacte le support.",
    "An error occurred loading this challenge. Please try again.": "Une erreur s'est produite lors du chargement de ce défi. Réessaie.",
    "You must complete the chapter first.": "Tu dois d'abord terminer le chapitre.",
    "An error occurred loading this reward. Please try again.": "Une erreur s'est produite lors du chargement de cette récompense. Réessaie.",
    "An error occurred generating your certificate. Please contact support.": "Une erreur s'est produite lors de la génération de ton certificat. Contacte le support.",
    "Your gift has been created and sent to %(email)s!": "Ton cadeau a été créé et envoyé à %(email)s !",
    "Please select your gender": "Merci de sélectionner ton genre",
    "Invalid gender selection": "Sélection de genre invalide",
    "Please select your location": "Merci de sélectionner ta localisation",
    "Invalid location selection": "Sélection de localisation invalide",
    "Please fill in all required fields": "Merci de remplir tous les champs obligatoires",

    # Greeting messages (informal tu form)
    "Welcome": "Bienvenue",
    "Welcome back": "Content de te revoir",
    "Hello": "Salut",
    "Hi": "Salut",
    "Good morning": "Bonjour",
    "Good evening": "Bonsoir",
    "Good night": "Bonne nuit",
    "See you soon": "À bientôt",
    "Thank you": "Merci",
    "Thanks": "Merci",
    "You're welcome": "De rien",
}

def translate_text(msgid, context=""):
    """
    Translate English text to French with informal tu form
    """
    # Direct match
    if msgid in TRANSLATIONS:
        return TRANSLATIONS[msgid]

    # Try without markup/formatting
    stripped = msgid.strip()
    if stripped in TRANSLATIONS:
        return TRANSLATIONS[stripped]

    # Pattern-based translations with tu form
    import re

    # Your X patterns -> Ton/Ta
    if msgid.startswith("Your "):
        rest = msgid[5:]
        # Feminine nouns start with vowel or specific words
        if rest[0].lower() in 'aeiouhéè' or rest.lower() in ['photo', 'invitation', 'adresse', 'carte']:
            return f"Ton {rest.lower()}"  # Use ton before vowels
        return f"Ton {rest.lower()}"

    # Enter your X -> Saisis ton X
    if msgid.startswith("Enter your "):
        rest = msgid[11:]
        return f"Saisis ton {rest.lower()}"

    # You have X -> Tu as X
    if msgid.startswith("You have "):
        rest = msgid[9:]
        return f"Tu as {rest.lower()}"

    # You are X -> Tu es X
    if msgid.startswith("You are "):
        rest = msgid[8:]
        return f"Tu es {rest.lower()}"

    # Are you X? -> Es-tu X ?
    if msgid.startswith("Are you "):
        rest = msgid[8:].rstrip('?')
        return f"Es-tu {rest.lower()} ?"

    # Do you X? -> Est-ce que tu X ?
    if msgid.startswith("Do you "):
        rest = msgid[7:].rstrip('?')
        return f"Est-ce que tu {rest.lower()} ?"

    # View X -> Voir X
    if msgid.startswith("View "):
        rest = msgid[5:]
        return f"Voir {rest.lower()}"

    # Edit X -> Modifier X
    if msgid.startswith("Edit "):
        rest = msgid[5:]
        return f"Modifier {rest.lower()}"

    # Delete X -> Supprimer X
    if msgid.startswith("Delete "):
        rest = msgid[7:]
        return f"Supprimer {rest.lower()}"

    # Create X -> Créer X
    if msgid.startswith("Create "):
        rest = msgid[7:]
        return f"Créer {rest.lower()}"

    # X successfully -> X avec succès
    if msgid.endswith(" successfully"):
        rest = msgid[:-13]
        return f"{rest} avec succès"

    # X is required -> X est requis
    if msgid.endswith(" is required"):
        rest = msgid[:-12]
        return f"{rest} est requis"

    # Please X -> Merci de X
    if msgid.startswith("Please "):
        rest = msgid[7:]
        return f"Merci de {rest.lower()}"

    # No translation found
    return None

def main():
    po_file = Path("crush_lu/locale/fr/LC_MESSAGES/django.po")

    if not po_file.exists():
        print(f"Error: {po_file} not found")
        return

    # Load .po file
    po = polib.pofile(str(po_file))

    print(f"Total entries: {len(po)}")
    print(f"Translated: {len(po.translated_entries())}")
    print(f"Untranslated: {len(po.untranslated_entries())}")
    print(f"Fuzzy: {len(po.fuzzy_entries())}")
    print()

    translated_count = 0
    fuzzy_fixed_count = 0

    # Fix fuzzy entries first
    for entry in po.fuzzy_entries():
        if entry.msgid in TRANSLATIONS:
            entry.msgstr = TRANSLATIONS[entry.msgid]
            if 'fuzzy' in entry.flags:
                entry.flags.remove('fuzzy')
            fuzzy_fixed_count += 1
            try:
                print(f"Fixed fuzzy: \"{entry.msgid[:60]}\" → \"{entry.msgstr[:60]}\"")
            except (UnicodeEncodeError, UnicodeDecodeError):
                print(f"Fixed fuzzy entry (contains special chars)")

    # Translate untranslated entries
    for entry in po.untranslated_entries():
        if not entry.msgstr:  # Empty translation
            translation = translate_text(entry.msgid, entry.msgctxt or "")
            if translation:
                entry.msgstr = translation
                translated_count += 1
                try:
                    print(f"Translated: \"{entry.msgid}\" → \"{translation}\"")
                except UnicodeEncodeError:
                    print(f"Translated entry (contains special chars)")

    # Save the file
    po.save()

    print()
    print(f"[OK] Fixed {fuzzy_fixed_count} fuzzy entries")
    print(f"[OK] Translated {translated_count} new entries")
    print(f"[INFO] Total entries: {len(po)}")
    print(f"[OK] Translated: {len(po.translated_entries())}")
    print(f"[WARN] Remaining untranslated: {len(po.untranslated_entries())}")
    print(f"[WARN] Remaining fuzzy: {len(po.fuzzy_entries())}")
    print()
    print("Don't forget to run: python manage.py compilemessages")

if __name__ == "__main__":
    main()
