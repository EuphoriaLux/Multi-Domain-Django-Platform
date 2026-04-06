"""
Management command to generate the Crush.lu quiz rounds and questions.

Creates 6 rounds with 6 multilingual questions each (EN, DE, FR)
for a given QuizEvent. Can also be invoked as an admin action.

Usage:
    python manage.py generate_crush_quiz --quiz-id 1
    python manage.py generate_crush_quiz --quiz-id 1 --clear
"""

from django.core.management.base import BaseCommand, CommandError

from crush_lu.models.quiz import QuizEvent, QuizQuestion, QuizRound

# ---------------------------------------------------------------------------
# Quiz data: 6 rounds x 6 questions, trilingual (EN, DE, FR)
# ---------------------------------------------------------------------------

QUIZ_ROUNDS = [
    {
        "title_en": "Science of Love",
        "title_de": "Wissenschaft der Liebe",
        "title_fr": "La science de l'amour",
        "questions": [
            {
                "type": "open_ended",
                "text_en": "How many seconds does it take on average to fall in love or feel an initial attraction?",
                "text_de": "Wie viele Sekunden dauert es durchschnittlich, bis sich Menschen in jemandem verlieben?",
                "text_fr": "Combien de secondes faut-il en moyenne pour tomber amoureux ou ressentir une première attirance ?",
                "correct_answer_en": "4–7 seconds",
                "correct_answer_de": "4–7 Sekunden",
                "correct_answer_fr": "4–7 secondes",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "true_false",
                "text_en": "When you find someone attractive, your pupils dilate. True or false?",
                "text_de": "Wenn man jemanden attraktiv findet, weiten sich die Pupillen. Wahr oder falsch?",
                "text_fr": "Quand on trouve quelqu'un attirant, les pupilles se dilatent. Vrai ou faux ?",
                "correct_answer_en": "Yes / True",
                "correct_answer_de": "Ja / Wahr",
                "correct_answer_fr": "Oui / Vrai",
                "choices_en": [
                    {"text": "True", "is_correct": True},
                    {"text": "False", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Wahr", "is_correct": True},
                    {"text": "Falsch", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Vrai", "is_correct": True},
                    {"text": "Faux", "is_correct": False},
                ],
            },
            {
                "type": "true_false",
                "text_en": 'There are 5 different "love languages." True or false?',
                "text_de": 'Es gibt 5 verschiedene „Love Languages". Wahr oder falsch?',
                "text_fr": "Il existe 5 « langages de l'amour » différents. Vrai ou faux ?",
                "correct_answer_en": "Yes / True",
                "correct_answer_de": "Ja / Wahr",
                "correct_answer_fr": "Oui / Vrai",
                "choices_en": [
                    {"text": "True", "is_correct": True},
                    {"text": "False", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Wahr", "is_correct": True},
                    {"text": "Falsch", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Vrai", "is_correct": True},
                    {"text": "Faux", "is_correct": False},
                ],
            },
            {
                "type": "open_ended",
                "text_en": "Opposites attract. What does science say?",
                "text_de": "Gegensätze ziehen sich an. Was sagt die Wissenschaft?",
                "text_fr": "Les contraires s'attirent. Qu'en dit la science ?",
                "correct_answer_en": "No – similarity matters more",
                "correct_answer_de": "Nein – Ähnlichkeit ist entscheidend",
                "correct_answer_fr": "Non – la similarité compte davantage",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "multiple_choice",
                "text_en": 'Which hormone is often called the "cuddling hormone"? Adrenaline, dopamine, or oxytocin?',
                "text_de": 'Welches Hormon wird oft als „Kuschelhormon" bezeichnet? Adrenalin, Dopamin oder Oxytocin?',
                "text_fr": "Quelle hormone est souvent appelée « hormone du câlin » ? Adrénaline, dopamine ou ocytocine ?",
                "correct_answer_en": "Oxytocin",
                "correct_answer_de": "Oxytocin",
                "correct_answer_fr": "Ocytocine",
                "choices_en": [
                    {"text": "Adrenaline", "is_correct": False},
                    {"text": "Dopamine", "is_correct": False},
                    {"text": "Oxytocin", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Adrenalin", "is_correct": False},
                    {"text": "Dopamin", "is_correct": False},
                    {"text": "Oxytocin", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Adrénaline", "is_correct": False},
                    {"text": "Dopamine", "is_correct": False},
                    {"text": "Ocytocine", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "According to studies, what is the most attractive first impression? Humor, looks, or confidence?",
                "text_de": "Was ist laut Studien der attraktivste erste Eindruck? Humor, Aussehen oder Selbstbewusstsein?",
                "text_fr": "Selon les études, quelle première impression est la plus attirante ? L'humour, le physique ou la confiance en soi ?",
                "correct_answer_en": "Confidence",
                "correct_answer_de": "Selbstbewusstsein",
                "correct_answer_fr": "Confiance en soi",
                "choices_en": [
                    {"text": "Humor", "is_correct": False},
                    {"text": "Looks", "is_correct": False},
                    {"text": "Confidence", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Humor", "is_correct": False},
                    {"text": "Aussehen", "is_correct": False},
                    {"text": "Selbstbewusstsein", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "L'humour", "is_correct": False},
                    {"text": "Le physique", "is_correct": False},
                    {"text": "Confiance en soi", "is_correct": True},
                ],
            },
        ],
    },
    {
        "title_en": "Love, Lore & Legends",
        "title_de": "Liebe, Mythen & Legenden",
        "title_fr": "Amour, mythes & légendes",
        "questions": [
            {
                "type": "open_ended",
                "text_en": "In which country do people sometimes give a cucumber as a symbol of loyalty on a date?",
                "text_de": "In welchem Land schenkt man dem Date manchmal eine Gurke als Symbol für Treue?",
                "text_fr": "Dans quel pays offre-t-on parfois un concombre comme symbole de fidélité lors d'un rendez-vous ?",
                "correct_answer_en": "Japan",
                "correct_answer_de": "Japan",
                "correct_answer_fr": "Japon",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "open_ended",
                "text_en": "Which animal is known for staying faithful to its partner for life?",
                "text_de": "Welches Tier ist bekannt dafür, seinem Partner ein Leben lang treu zu bleiben?",
                "text_fr": "Quel animal est connu pour rester fidèle à son partenaire toute sa vie ?",
                "correct_answer_en": "Swan",
                "correct_answer_de": "Schwan",
                "correct_answer_fr": "Cygne",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which food has been considered an aphrodisiac for centuries? Oysters, caviar, or tomatoes?",
                "text_de": "Welches Lebensmittel gilt seit Jahrhunderten als Aphrodisiakum? Austern, Kaviar oder Tomaten?",
                "text_fr": "Quel aliment est considéré comme un aphrodisiaque depuis des siècles ? Huîtres, caviar ou tomates ?",
                "correct_answer_en": "Oysters",
                "correct_answer_de": "Austern",
                "correct_answer_fr": "Huîtres",
                "choices_en": [
                    {"text": "Oysters", "is_correct": True},
                    {"text": "Caviar", "is_correct": False},
                    {"text": "Tomatoes", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Austern", "is_correct": True},
                    {"text": "Kaviar", "is_correct": False},
                    {"text": "Tomaten", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Huîtres", "is_correct": True},
                    {"text": "Caviar", "is_correct": False},
                    {"text": "Tomates", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Who wrote famous love letters in verse? Goethe, Mozart, or Beethoven?",
                "text_de": "Wer schrieb berühmte Liebesbriefe in Reimen? Goethe, Mozart oder Beethoven?",
                "text_fr": "Qui a écrit de célèbres lettres d'amour en vers ? Goethe, Mozart ou Beethoven ?",
                "correct_answer_en": "Wolfgang Amadeus Mozart",
                "correct_answer_de": "Wolfgang Amadeus Mozart",
                "correct_answer_fr": "Wolfgang Amadeus Mozart",
                "choices_en": [
                    {"text": "Goethe", "is_correct": False},
                    {"text": "Mozart", "is_correct": True},
                    {"text": "Beethoven", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Goethe", "is_correct": False},
                    {"text": "Mozart", "is_correct": True},
                    {"text": "Beethoven", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Goethe", "is_correct": False},
                    {"text": "Mozart", "is_correct": True},
                    {"text": "Beethoven", "is_correct": False},
                ],
            },
            {
                "type": "true_false",
                "text_en": "The longest kiss lasted 58 hours (Thailand, 2013). True or false?",
                "text_de": "Der längste Kuss dauerte 58 Stunden (Thailand, 2013). Wahr oder falsch?",
                "text_fr": "Le plus long baiser a duré 58 heures (Thaïlande, 2013). Vrai ou faux ?",
                "correct_answer_en": "Yes / True",
                "correct_answer_de": "Ja / Wahr",
                "correct_answer_fr": "Oui / Vrai",
                "choices_en": [
                    {"text": "True", "is_correct": True},
                    {"text": "False", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Wahr", "is_correct": True},
                    {"text": "Falsch", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Vrai", "is_correct": True},
                    {"text": "Faux", "is_correct": False},
                ],
            },
            {
                "type": "open_ended",
                "text_en": "How long does the honeymoon phase last?",
                "text_de": "Wie lange dauert die Honeymoon-Phase?",
                "text_fr": "Combien de temps dure la phase lune de miel ?",
                "correct_answer_en": "6 months – 2 years",
                "correct_answer_de": "6 Monate – 2 Jahre",
                "correct_answer_fr": "6 mois – 2 ans",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
        ],
    },
    {
        "title_en": "Dating by the Numbers",
        "title_de": "Dating in Zahlen",
        "title_fr": "Le dating en chiffres",
        "questions": [
            {
                "type": "multiple_choice",
                "text_en": "What percentage of communication is non-verbal? 50, 88, or 93?",
                "text_de": "Wie viel Prozent der Kommunikation ist non-verbal? 50, 88 oder 93?",
                "text_fr": "Quel pourcentage de la communication est non-verbale ? 50, 88 ou 93 ?",
                "correct_answer_en": "93 percent",
                "correct_answer_de": "93 Prozent",
                "correct_answer_fr": "93 pour cent",
                "choices_en": [
                    {"text": "50%", "is_correct": False},
                    {"text": "88%", "is_correct": False},
                    {"text": "93%", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "50%", "is_correct": False},
                    {"text": "88%", "is_correct": False},
                    {"text": "93%", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "50%", "is_correct": False},
                    {"text": "88%", "is_correct": False},
                    {"text": "93%", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "How many Tinder matches are made daily worldwide? 1, 5, or 10 million?",
                "text_de": "Wie viele Tinder-Matches werden weltweit täglich vergeben? 1, 5 oder 10 Millionen?",
                "text_fr": "Combien de matchs Tinder sont attribués chaque jour dans le monde ? 1, 5 ou 10 millions ?",
                "correct_answer_en": "Approx. 10 million",
                "correct_answer_de": "Ca. 10 Millionen",
                "correct_answer_fr": "Environ 10 millions",
                "choices_en": [
                    {"text": "1 million", "is_correct": False},
                    {"text": "5 million", "is_correct": False},
                    {"text": "10 million", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "1 Million", "is_correct": False},
                    {"text": "5 Millionen", "is_correct": False},
                    {"text": "10 Millionen", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "1 million", "is_correct": False},
                    {"text": "5 millions", "is_correct": False},
                    {"text": "10 millions", "is_correct": True},
                ],
            },
            {
                "type": "open_ended",
                "text_en": "What percentage of marriage proposals are rejected?",
                "text_de": "Wie viel Prozent der Heiratsanträge werden abgelehnt?",
                "text_fr": "Quel pourcentage de demandes en mariage sont refusées ?",
                "correct_answer_en": "15 percent",
                "correct_answer_de": "15 Prozent",
                "correct_answer_fr": "15 pour cent",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "true_false",
                "text_en": "40 percent of people have lied on a first date. True or false?",
                "text_de": "40 Prozent der Menschen haben beim ersten Date schon mal gelogen. Wahr oder falsch?",
                "text_fr": "40 pour cent des gens ont déjà menti lors d'un premier rendez-vous. Vrai ou faux ?",
                "correct_answer_en": "False – it's 60%",
                "correct_answer_de": "Falsch – es sind 60 %",
                "correct_answer_fr": "Faux – c'est 60 %",
                "choices_en": [
                    {"text": "True", "is_correct": False},
                    {"text": "False", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Wahr", "is_correct": False},
                    {"text": "Falsch", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Vrai", "is_correct": False},
                    {"text": "Faux", "is_correct": True},
                ],
            },
            {
                "type": "open_ended",
                "text_en": "Statistically, how long from an online match to the first meeting?",
                "text_de": "Wie lange dauert es statistisch von einem Online-Match bis zum ersten Treffen?",
                "text_fr": "Combien de temps faut-il statistiquement entre un match en ligne et le premier rendez-vous ?",
                "correct_answer_en": "12 days",
                "correct_answer_de": "12 Tage",
                "correct_answer_fr": "12 jours",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "multiple_choice",
                "text_en": "How long does the average first date last? 1, 2, or 3 hours?",
                "text_de": "Wie lange dauert durchschnittlich ein erstes Date? 1, 2 oder 3 Stunden?",
                "text_fr": "Combien de temps dure en moyenne un premier rendez-vous ? 1, 2 ou 3 heures ?",
                "correct_answer_en": "2 hours",
                "correct_answer_de": "2 Stunden",
                "correct_answer_fr": "2 heures",
                "choices_en": [
                    {"text": "1 hour", "is_correct": False},
                    {"text": "2 hours", "is_correct": True},
                    {"text": "3 hours", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "1 Stunde", "is_correct": False},
                    {"text": "2 Stunden", "is_correct": True},
                    {"text": "3 Stunden", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "1 heure", "is_correct": False},
                    {"text": "2 heures", "is_correct": True},
                    {"text": "3 heures", "is_correct": False},
                ],
            },
        ],
    },
    {
        "title_en": "First Impressions & Flirting",
        "title_de": "Erster Eindruck & Flirten",
        "title_fr": "Premières impressions & drague",
        "questions": [
            {
                "type": "multiple_choice",
                "text_en": 'How many people say they\'ve experienced "love at first sight"? 30, 35, or 40%?',
                "text_de": 'Wie viele Menschen sagen, sie hätten „Liebe auf den ersten Blick" erlebt? 30, 35 oder 40 %?',
                "text_fr": "Combien de personnes disent avoir vécu un « coup de foudre » ? 30, 35 ou 40 % ?",
                "correct_answer_en": "40 percent",
                "correct_answer_de": "40 Prozent",
                "correct_answer_fr": "40 pour cent",
                "choices_en": [
                    {"text": "30%", "is_correct": False},
                    {"text": "35%", "is_correct": False},
                    {"text": "40%", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "30%", "is_correct": False},
                    {"text": "35%", "is_correct": False},
                    {"text": "40%", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "30%", "is_correct": False},
                    {"text": "35%", "is_correct": False},
                    {"text": "40%", "is_correct": True},
                ],
            },
            {
                "type": "open_ended",
                "text_en": "What percentage of people Google their date before meeting?",
                "text_de": "Wie viel Prozent der Menschen googlen ihr Date vor dem Treffen?",
                "text_fr": "Quel pourcentage de personnes recherchent leur date sur Google avant le rendez-vous ?",
                "correct_answer_en": "70 percent",
                "correct_answer_de": "70 Prozent",
                "correct_answer_fr": "70 pour cent",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "multiple_choice",
                "text_en": "What's most unattractive? Talking too much, too little, or only about yourself?",
                "text_de": "Was wirkt am unattraktivsten? Zu viel reden, zu wenig reden oder nur über sich reden?",
                "text_fr": "Qu'est-ce qui est le moins attirant ? Trop parler, trop peu parler ou ne parler que de soi ?",
                "correct_answer_en": "Only talking about yourself",
                "correct_answer_de": "Nur über sich reden",
                "correct_answer_fr": "Ne parler que de soi",
                "choices_en": [
                    {"text": "Talking too much", "is_correct": False},
                    {"text": "Talking too little", "is_correct": False},
                    {"text": "Only talking about yourself", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Zu viel reden", "is_correct": False},
                    {"text": "Zu wenig reden", "is_correct": False},
                    {"text": "Nur über sich reden", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Trop parler", "is_correct": False},
                    {"text": "Trop peu parler", "is_correct": False},
                    {"text": "Ne parler que de soi", "is_correct": True},
                ],
            },
            {
                "type": "open_ended",
                "text_en": "What is the most common excuse to cancel a date?",
                "text_de": "Was ist meistens die Ausrede, um ein Date abzusagen?",
                "text_fr": "Quelle est généralement l'excuse pour annuler un rendez-vous ?",
                "correct_answer_en": "I'm sick",
                "correct_answer_de": "Ich bin krank",
                "correct_answer_fr": "Je suis malade",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which body part is looked at most when flirting? Eyes, mouth, or hands?",
                "text_de": "Welches Körperteil wird beim Flirten am meisten angesehen? Augen, Mund oder Hände?",
                "text_fr": "Quelle partie du corps est la plus regardée en flirtant ? Les yeux, la bouche ou les mains ?",
                "correct_answer_en": "Eyes",
                "correct_answer_de": "Augen",
                "correct_answer_fr": "Les yeux",
                "choices_en": [
                    {"text": "Eyes", "is_correct": True},
                    {"text": "Mouth", "is_correct": False},
                    {"text": "Hands", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Augen", "is_correct": True},
                    {"text": "Mund", "is_correct": False},
                    {"text": "Hände", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Les yeux", "is_correct": True},
                    {"text": "La bouche", "is_correct": False},
                    {"text": "Les mains", "is_correct": False},
                ],
            },
            {
                "type": "true_false",
                "text_en": "People need about 5 dates to know if there's long-term potential. True or false?",
                "text_de": "Menschen brauchen ungefähr 5 Dates, um zu wissen, ob es Langzeitpotenzial hat. Wahr oder falsch?",
                "text_fr": "Il faut environ 5 rendez-vous pour savoir si ça a du potentiel à long terme. Vrai ou faux ?",
                "correct_answer_en": "False – it's 3",
                "correct_answer_de": "Falsch – es sind 3",
                "correct_answer_fr": "Faux – c'est 3",
                "choices_en": [
                    {"text": "True", "is_correct": False},
                    {"text": "False", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Wahr", "is_correct": False},
                    {"text": "Falsch", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Vrai", "is_correct": False},
                    {"text": "Faux", "is_correct": True},
                ],
            },
        ],
    },
    {
        "title_en": "Dating Lingo 2026",
        "title_de": "Dating-Begriffe 2026",
        "title_fr": "Le jargon du dating 2026",
        "questions": [
            {
                "type": "open_ended",
                "text_en": "What is ghosting?",
                "text_de": "Was ist Ghosting?",
                "text_fr": "Qu'est-ce que le ghosting ?",
                "correct_answer_en": "Cutting contact without explanation",
                "correct_answer_de": "Kontakt abbrechen ohne Erklärung",
                "correct_answer_fr": "Couper le contact sans explication",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "open_ended",
                "text_en": "What is breadcrumbing?",
                "text_de": "Was ist Breadcrumbing?",
                "text_fr": "Qu'est-ce que le breadcrumbing ?",
                "correct_answer_en": "Texting/meeting without real intentions",
                "correct_answer_de": "Schreiben/treffen ohne reale Intentionen",
                "correct_answer_fr": "Envoyer des messages sans réelle intention",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "multiple_choice",
                "text_en": "What is catfishing? Multiple dates at once, fake profile, or texting too much?",
                "text_de": "Was ist Catfishing? Mehrere Dates gleichzeitig, Fake-Profil oder zu viel schreiben?",
                "text_fr": "Qu'est-ce que le catfishing ? Plusieurs dates en même temps, faux profil ou trop écrire ?",
                "correct_answer_en": "Fake / embellished profile",
                "correct_answer_de": "Fake-Profil / verschönertes Profil",
                "correct_answer_fr": "Faux profil / profil embelli",
                "choices_en": [
                    {"text": "Multiple dates at once", "is_correct": False},
                    {"text": "Fake profile", "is_correct": True},
                    {"text": "Texting too much", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Mehrere Dates gleichzeitig", "is_correct": False},
                    {"text": "Fake-Profil", "is_correct": True},
                    {"text": "Zu viel schreiben", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Plusieurs dates en même temps", "is_correct": False},
                    {"text": "Faux profil", "is_correct": True},
                    {"text": "Trop écrire", "is_correct": False},
                ],
            },
            {
                "type": "open_ended",
                "text_en": "What do you call it when someone suddenly reappears after radio silence?",
                "text_de": "Wie nennt man es, wenn jemand nach Funkstille plötzlich wieder auftaucht?",
                "text_fr": "Comment appelle-t-on le fait que quelqu'un réapparaisse après un silence radio ?",
                "correct_answer_en": "Zombieing",
                "correct_answer_de": "Zombieing",
                "correct_answer_fr": "Zombieing",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which app is used most for dating? Tinder, Facebook Dating, or Bumble?",
                "text_de": "Welche App wird am häufigsten fürs Dating genutzt? Tinder, Facebook Dating oder Bumble?",
                "text_fr": "Quelle appli est la plus utilisée pour les rencontres ? Tinder, Facebook Dating ou Bumble ?",
                "correct_answer_en": "Tinder",
                "correct_answer_de": "Tinder",
                "correct_answer_fr": "Tinder",
                "choices_en": [
                    {"text": "Tinder", "is_correct": True},
                    {"text": "Facebook Dating", "is_correct": False},
                    {"text": "Bumble", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Tinder", "is_correct": True},
                    {"text": "Facebook Dating", "is_correct": False},
                    {"text": "Bumble", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Tinder", "is_correct": True},
                    {"text": "Facebook Dating", "is_correct": False},
                    {"text": "Bumble", "is_correct": False},
                ],
            },
            {
                "type": "true_false",
                "text_en": "Dating apps play a big role in Luxembourg. True or false?",
                "text_de": "Dating-Apps spielen in Luxemburg eine große Rolle. Wahr oder falsch?",
                "text_fr": "Les applis de rencontres jouent un grand rôle au Luxembourg. Vrai ou faux ?",
                "correct_answer_en": "Yes / True",
                "correct_answer_de": "Ja / Wahr",
                "correct_answer_fr": "Oui / Vrai",
                "choices_en": [
                    {"text": "True", "is_correct": True},
                    {"text": "False", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Wahr", "is_correct": True},
                    {"text": "Falsch", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Vrai", "is_correct": True},
                    {"text": "Faux", "is_correct": False},
                ],
            },
        ],
    },
    {
        "title_en": "Love in Luxembourg",
        "title_de": "Liebe in Luxemburg",
        "title_fr": "L'amour au Luxembourg",
        "questions": [
            {
                "type": "open_ended",
                "text_en": "How many people are verified on Crush.lu?",
                "text_de": "Wie viele Menschen sind bei Crush.lu verifiziert?",
                "text_fr": "Combien de personnes sont vérifiées sur Crush.lu ?",
                "correct_answer_en": "Approx. 350",
                "correct_answer_de": "Ca. 350",
                "correct_answer_fr": "Environ 350",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "multiple_choice",
                "text_en": "How many verifications are still pending? 106, 126, or 166?",
                "text_de": "Wie viele Verifikationen stehen noch aus? 106, 126 oder 166?",
                "text_fr": "Combien de vérifications sont encore en attente ? 106, 126 ou 166 ?",
                "correct_answer_en": "166",
                "correct_answer_de": "166",
                "correct_answer_fr": "166",
                "choices_en": [
                    {"text": "106", "is_correct": False},
                    {"text": "126", "is_correct": False},
                    {"text": "166", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "106", "is_correct": False},
                    {"text": "126", "is_correct": False},
                    {"text": "166", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "106", "is_correct": False},
                    {"text": "126", "is_correct": False},
                    {"text": "166", "is_correct": True},
                ],
            },
            {
                "type": "true_false",
                "text_en": "55 percent of people in Luxembourg are foreigners. True or false?",
                "text_de": "55 Prozent der Menschen in Luxemburg sind Ausländer. Wahr oder falsch?",
                "text_fr": "55 pour cent des habitants du Luxembourg sont étrangers. Vrai ou faux ?",
                "correct_answer_en": "False – it's 47%",
                "correct_answer_de": "Falsch – es sind 47 %",
                "correct_answer_fr": "Faux – c'est 47 %",
                "choices_en": [
                    {"text": "True", "is_correct": False},
                    {"text": "False", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Wahr", "is_correct": False},
                    {"text": "Falsch", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Vrai", "is_correct": False},
                    {"text": "Faux", "is_correct": True},
                ],
            },
            {
                "type": "open_ended",
                "text_en": "What is the goal of Crush.lu?",
                "text_de": "Was ist das Ziel von Crush.lu?",
                "text_fr": "Quel est l'objectif de Crush.lu ?",
                "correct_answer_en": "Reduce loneliness / connect people",
                "correct_answer_de": "Einsamkeit vermindern / Menschen verbinden",
                "correct_answer_fr": "Réduire la solitude / connecter les gens",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "open_ended",
                "text_en": "What is the biggest challenge of dating in Luxembourg?",
                "text_de": "Was ist die größte Herausforderung beim Dating in Luxemburg?",
                "text_fr": "Quel est le plus grand défi du dating au Luxembourg ?",
                "correct_answer_en": "Small dating pool",
                "correct_answer_de": "Kleiner Dating-Pool",
                "correct_answer_fr": "Petit bassin de célibataires",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
            {
                "type": "open_ended",
                "text_en": 'What is a typical "Luxembourg dating moment"?',
                "text_de": 'Was ist ein typischer „Luxemburg-Dating-Moment"?',
                "text_fr": "Qu'est-ce qu'un « moment dating typique au Luxembourg » ?",
                "correct_answer_en": "Mutual friends / acquaintances",
                "correct_answer_de": "Gemeinsame Freunde / Bekannte",
                "correct_answer_fr": "Amis / connaissances en commun",
                "choices_en": [],
                "choices_de": [],
                "choices_fr": [],
            },
        ],
    },
]

# Map question type strings to model choices
QUESTION_TYPE_MAP = {
    "multiple_choice": "multiple_choice",
    "true_false": "true_false",
    "open_ended": "open_ended",
}


def populate_quiz(quiz, clear=False):
    """Populate a QuizEvent with the Crush quiz rounds and questions.

    Args:
        quiz: QuizEvent instance to populate.
        clear: If True, delete existing rounds/questions first.

    Returns:
        Tuple of (rounds_created, questions_created).
    """
    if clear:
        quiz.rounds.all().delete()

    rounds_created = 0
    questions_created = 0

    for round_idx, round_data in enumerate(QUIZ_ROUNDS):
        quiz_round = QuizRound(
            quiz=quiz,
            sort_order=round_idx,
            time_per_question=30,
            is_bonus=False,
        )
        # Set translated title fields directly
        quiz_round.title_en = round_data["title_en"]
        quiz_round.title_de = round_data["title_de"]
        quiz_round.title_fr = round_data["title_fr"]
        quiz_round.save()
        rounds_created += 1

        for q_idx, q_data in enumerate(round_data["questions"]):
            question = QuizQuestion(
                round=quiz_round,
                question_type=QUESTION_TYPE_MAP[q_data["type"]],
                sort_order=q_idx,
                points=10,
            )
            # Set translated text fields
            question.text_en = q_data["text_en"]
            question.text_de = q_data["text_de"]
            question.text_fr = q_data["text_fr"]

            # Set translated choices fields
            question.choices_en = q_data["choices_en"]
            question.choices_de = q_data["choices_de"]
            question.choices_fr = q_data["choices_fr"]

            # Set translated correct_answer fields
            question.correct_answer_en = q_data["correct_answer_en"]
            question.correct_answer_de = q_data["correct_answer_de"]
            question.correct_answer_fr = q_data["correct_answer_fr"]

            question.save()
            questions_created += 1

    return rounds_created, questions_created


class Command(BaseCommand):
    help = "Generate Crush.lu quiz rounds and questions for a QuizEvent"

    def add_arguments(self, parser):
        parser.add_argument(
            "--quiz-id",
            type=int,
            required=True,
            help="ID of the QuizEvent to populate with rounds and questions",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove existing rounds and questions before generating",
        )

    def handle(self, *args, **options):
        quiz_id = options["quiz_id"]
        clear = options["clear"]

        try:
            quiz = QuizEvent.objects.get(pk=quiz_id)
        except QuizEvent.DoesNotExist:
            raise CommandError(f"QuizEvent with id={quiz_id} does not exist.")

        existing_rounds = quiz.rounds.count()
        if existing_rounds and not clear:
            raise CommandError(
                f"QuizEvent {quiz_id} already has {existing_rounds} rounds. "
                f"Use --clear to remove them first."
            )

        rounds_created, questions_created = populate_quiz(quiz, clear=clear)

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully created {rounds_created} rounds and "
                f"{questions_created} questions for QuizEvent {quiz_id} "
                f'("{quiz.event}").'
            )
        )
