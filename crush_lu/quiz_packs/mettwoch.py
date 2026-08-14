"""Mëttwoch general-knowledge quiz pack — 6 rounds x 6 questions, trilingual.

Built from a coach-supplied German question sheet ("Quiz_mettwoch"), which held
40 three-option questions across eight headings: Kunst, Musik, Wissenschaft,
Filme, Geschichte, Superhelden, Geographie, Politik.

Two headings were merged to reach the 6x6 shape the other packs use — Kunst
with Musik, Geschichte with Politik — which left those two rounds oversubscribed
(9 and 11 questions for 6 slots) and the four standalone rounds one short. Eight
source questions were therefore dropped and four written to match:

Dropped from "Kunst & Musik" (9 -> 6):
  * Which Madrid museum shows Velázquez's "Las Meninas"? (Museo del Prado)
  * Which French painter named Impressionism? (Claude Monet)
  * Michael Jackson's best-selling album after Thriller? (Bad)
Dropped from "Geschichte & Politik" (11 -> 6):
  * Which country flew the flag on Columbus's 1492 expedition? (Spain)
  * Which country the Black Death reached first in 1347? (Italy)
  * Which country has the world's oldest unwritten constitution? (UK)
  * What "lobbyism" means in politics? (representing sectional interests)
  * Which party family has historically dominated the EP? (Christian democracy)

Written to fill the four one-short rounds — marked ``# NEW`` at the question:
Wissenschaft (atmosphere), Filme (The Godfather), Superhelden (Gotham City),
Geographie (Luxembourg's neighbours). They are kept at the sheet's difficulty
and, like every source question, three-option multiple choice.

Four source questions were corrected rather than reproduced. In each case the
sheet's intended answer is kept and only the prompt is tightened, because as
written each one would have scored a knowledgeable player wrong:

* The Sistine Chapel question asked who painted *the chapel*. Its walls were
  frescoed by Botticelli, Perugino, Ghirlandaio and Rosselli; Michelangelo
  painted the ceiling and the Last Judgment. Narrowed to the ceiling, which
  makes the keyed Michelangelo the only defensible answer.
* The geology question asked for "die Wissenschaft von den Gesteinen", whose
  exact name is petrology — not among the three options offered. Reworded to
  ask which science studies the structure and history of the Earth, which the
  keyed Geologie answers uniquely and Mineralogie no longer does.
* The EU question read "In welchem *Land* wurde die Europäische Union
  gegründet?" while offering years (1951/1957/1993) as answers. Reworded to
  "Jahr"; keyed 1993, the Maastricht Treaty that created the Union proper,
  leaving 1951 (ECSC) and 1957 (EEC) as meaningful distractors.
* The European Parliament plenary is keyed at 720, its size since the June 2024
  election. The sheet's other option, 705, was the 2019–2024 figure and now
  works as a distractor.

Spelling was fixed silently where the sheet slipped: Michael (not Micheal),
Truman (not Trumman), Themyscira (not Themiscyra), Captain America (not
Captain Amerika), flächenmäßig.

Text-only: no round in this pack carries a media stimulus, so it seeds with no
pending uploads and is playable the moment it is generated.
"""

QUIZ_ROUNDS = [
    # ======================================================================
    # ROUND 1 — Kunst & Musik
    # ======================================================================
    {
        "title_en": "Art & Music",
        "title_de": "Kunst & Musik",
        "title_fr": "Art et musique",
        "questions": [
            {
                "type": "multiple_choice",
                "text_en": "Who painted the ceiling of the Sistine Chapel?",
                "text_de": "Wer malte die Decke der Sixtinischen Kapelle?",
                "text_fr": "Qui a peint le plafond de la chapelle Sixtine ?",
                "correct_answer_en": "Michelangelo",
                "correct_answer_de": "Michelangelo",
                "correct_answer_fr": "Michel-Ange",
                "choices_en": [
                    {"text": "Leonardo da Vinci", "is_correct": False},
                    {"text": "Michelangelo", "is_correct": True},
                    {"text": "Raphael Sanzio", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Leonardo da Vinci", "is_correct": False},
                    {"text": "Michelangelo", "is_correct": True},
                    {"text": "Raffael Sanzio", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Léonard de Vinci", "is_correct": False},
                    {"text": "Michel-Ange", "is_correct": True},
                    {"text": "Raphaël Sanzio", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which Spanish artist is regarded as one of the leading figures of Surrealism?",
                "text_de": "Welcher spanische Künstler gilt als einer der Hauptvertreter des Surrealismus?",
                "text_fr": "Quel artiste espagnol est considéré comme l'un des principaux représentants du surréalisme ?",
                "correct_answer_en": "Salvador Dalí",
                "correct_answer_de": "Salvador Dalí",
                "correct_answer_fr": "Salvador Dalí",
                "choices_en": [
                    {"text": "Pablo Picasso", "is_correct": False},
                    {"text": "Diego Velázquez", "is_correct": False},
                    {"text": "Salvador Dalí", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Pablo Picasso", "is_correct": False},
                    {"text": "Diego Velázquez", "is_correct": False},
                    {"text": "Salvador Dalí", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Pablo Picasso", "is_correct": False},
                    {"text": "Diego Vélasquez", "is_correct": False},
                    {"text": "Salvador Dalí", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which Mexican artist is famous for her self-portraits?",
                "text_de": "Welche mexikanische Künstlerin ist berühmt für ihre Selbstporträts?",
                "text_fr": "Quelle artiste mexicaine est célèbre pour ses autoportraits ?",
                "correct_answer_en": "Frida Kahlo",
                "correct_answer_de": "Frida Kahlo",
                "correct_answer_fr": "Frida Kahlo",
                "choices_en": [
                    {"text": "María Izquierdo", "is_correct": False},
                    {"text": "Remedios Varo", "is_correct": False},
                    {"text": "Frida Kahlo", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "María Izquierdo", "is_correct": False},
                    {"text": "Remedios Varo", "is_correct": False},
                    {"text": "Frida Kahlo", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "María Izquierdo", "is_correct": False},
                    {"text": "Remedios Varo", "is_correct": False},
                    {"text": "Frida Kahlo", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which country does the band ABBA come from?",
                "text_de": "Aus welchem Land stammt die Band ABBA?",
                "text_fr": "De quel pays vient le groupe ABBA ?",
                "correct_answer_en": "Sweden",
                "correct_answer_de": "Schweden",
                "correct_answer_fr": "La Suède",
                "choices_en": [
                    {"text": "Denmark", "is_correct": False},
                    {"text": "Sweden", "is_correct": True},
                    {"text": "Finland", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Dänemark", "is_correct": False},
                    {"text": "Schweden", "is_correct": True},
                    {"text": "Finnland", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Le Danemark", "is_correct": False},
                    {"text": "La Suède", "is_correct": True},
                    {"text": "La Finlande", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": 'Which composer wrote the famous "Moonlight Sonata"?',
                "text_de": "Welcher Komponist schrieb die berühmte „Mondscheinsonate“?",
                "text_fr": "Quel compositeur a écrit la célèbre « Sonate au clair de lune » ?",
                "correct_answer_en": "Beethoven",
                "correct_answer_de": "Beethoven",
                "correct_answer_fr": "Beethoven",
                "choices_en": [
                    {"text": "Mozart", "is_correct": False},
                    {"text": "Bach", "is_correct": False},
                    {"text": "Beethoven", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Mozart", "is_correct": False},
                    {"text": "Bach", "is_correct": False},
                    {"text": "Beethoven", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Mozart", "is_correct": False},
                    {"text": "Bach", "is_correct": False},
                    {"text": "Beethoven", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": 'Which band had its commercial breakthrough in 1991 with "Smells Like Teen Spirit"?',
                "text_de": "Welche Band hatte 1991 mit „Smells Like Teen Spirit“ ihren kommerziellen Durchbruch?",
                "text_fr": "Quel groupe a connu sa percée commerciale en 1991 avec « Smells Like Teen Spirit » ?",
                "correct_answer_en": "Nirvana",
                "correct_answer_de": "Nirvana",
                "correct_answer_fr": "Nirvana",
                "choices_en": [
                    {"text": "Nirvana", "is_correct": True},
                    {"text": "Red Hot Chili Peppers", "is_correct": False},
                    {"text": "Green Day", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Nirvana", "is_correct": True},
                    {"text": "Red Hot Chili Peppers", "is_correct": False},
                    {"text": "Green Day", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Nirvana", "is_correct": True},
                    {"text": "Red Hot Chili Peppers", "is_correct": False},
                    {"text": "Green Day", "is_correct": False},
                ],
            },
        ],
    },
    # ======================================================================
    # ROUND 2 — Wissenschaft
    # ======================================================================
    {
        "title_en": "Science",
        "title_de": "Wissenschaft",
        "title_fr": "Sciences",
        "questions": [
            {
                "type": "multiple_choice",
                "text_en": "How many planets does our solar system have?",
                "text_de": "Wie viele Planeten hat unser Sonnensystem?",
                "text_fr": "Combien de planètes compte notre système solaire ?",
                "correct_answer_en": "Eight",
                "correct_answer_de": "Acht",
                "correct_answer_fr": "Huit",
                "choices_en": [
                    {"text": "Eight", "is_correct": True},
                    {"text": "Nine", "is_correct": False},
                    {"text": "Seven", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Acht", "is_correct": True},
                    {"text": "Neun", "is_correct": False},
                    {"text": "Sieben", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Huit", "is_correct": True},
                    {"text": "Neuf", "is_correct": False},
                    {"text": "Sept", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "What is the smallest unit of a chemical element called?",
                "text_de": "Wie nennt man die kleinste Einheit eines chemischen Elements?",
                "text_fr": "Comment appelle-t-on la plus petite unité d'un élément chimique ?",
                "correct_answer_en": "Atom",
                "correct_answer_de": "Atom",
                "correct_answer_fr": "L'atome",
                "choices_en": [
                    {"text": "Molecule", "is_correct": False},
                    {"text": "Ion", "is_correct": False},
                    {"text": "Atom", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Molekül", "is_correct": False},
                    {"text": "Ion", "is_correct": False},
                    {"text": "Atom", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "La molécule", "is_correct": False},
                    {"text": "L'ion", "is_correct": False},
                    {"text": "L'atome", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which metal is liquid at room temperature?",
                "text_de": "Welches Metall ist bei Raumtemperatur flüssig?",
                "text_fr": "Quel métal est liquide à température ambiante ?",
                "correct_answer_en": "Mercury",
                "correct_answer_de": "Quecksilber",
                "correct_answer_fr": "Le mercure",
                "choices_en": [
                    {"text": "Lead", "is_correct": False},
                    {"text": "Mercury", "is_correct": True},
                    {"text": "Tin", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Blei", "is_correct": False},
                    {"text": "Quecksilber", "is_correct": True},
                    {"text": "Zinn", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Le plomb", "is_correct": False},
                    {"text": "Le mercure", "is_correct": True},
                    {"text": "L'étain", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which science studies the structure and history of the Earth?",
                "text_de": "Welche Wissenschaft erforscht den Aufbau und die Geschichte der Erde?",
                "text_fr": "Quelle science étudie la structure et l'histoire de la Terre ?",
                "correct_answer_en": "Geology",
                "correct_answer_de": "Geologie",
                "correct_answer_fr": "La géologie",
                "choices_en": [
                    {"text": "Biology", "is_correct": False},
                    {"text": "Mineralogy", "is_correct": False},
                    {"text": "Geology", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Biologie", "is_correct": False},
                    {"text": "Mineralogie", "is_correct": False},
                    {"text": "Geologie", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "La biologie", "is_correct": False},
                    {"text": "La minéralogie", "is_correct": False},
                    {"text": "La géologie", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "What is the main energy source of our Sun?",
                "text_de": "Was ist die Hauptenergiequelle unserer Sonne?",
                "text_fr": "Quelle est la principale source d'énergie de notre Soleil ?",
                "correct_answer_en": "Nuclear fusion",
                "correct_answer_de": "Kernfusion",
                "correct_answer_fr": "La fusion nucléaire",
                "choices_en": [
                    {"text": "Nuclear fusion", "is_correct": True},
                    {"text": "Nuclear fission", "is_correct": False},
                    {"text": "Combustion", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Kernfusion", "is_correct": True},
                    {"text": "Kernspaltung", "is_correct": False},
                    {"text": "Verbrennung", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "La fusion nucléaire", "is_correct": True},
                    {"text": "La fission nucléaire", "is_correct": False},
                    {"text": "La combustion", "is_correct": False},
                ],
            },
            {
                # NEW — the source sheet had five science questions, this round needs six.
                "type": "multiple_choice",
                "text_en": "Which gas makes up the largest share of Earth's atmosphere?",
                "text_de": "Welches Gas macht den größten Anteil der Erdatmosphäre aus?",
                "text_fr": "Quel gaz constitue la plus grande partie de l'atmosphère terrestre ?",
                "correct_answer_en": "Nitrogen",
                "correct_answer_de": "Stickstoff",
                "correct_answer_fr": "L'azote",
                "choices_en": [
                    {"text": "Oxygen", "is_correct": False},
                    {"text": "Nitrogen", "is_correct": True},
                    {"text": "Carbon dioxide", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Sauerstoff", "is_correct": False},
                    {"text": "Stickstoff", "is_correct": True},
                    {"text": "Kohlendioxid", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "L'oxygène", "is_correct": False},
                    {"text": "L'azote", "is_correct": True},
                    {"text": "Le dioxyde de carbone", "is_correct": False},
                ],
            },
        ],
    },
    # ======================================================================
    # ROUND 3 — Filme
    # ======================================================================
    {
        "title_en": "Movies",
        "title_de": "Filme",
        "title_fr": "Cinéma",
        "questions": [
            {
                "type": "multiple_choice",
                "text_en": 'Which actor played the lead character in "Forrest Gump"?',
                "text_de": "Welcher Schauspieler spielte den Hauptcharakter in „Forrest Gump“?",
                "text_fr": "Quel acteur a joué le personnage principal dans « Forrest Gump » ?",
                "correct_answer_en": "Tom Hanks",
                "correct_answer_de": "Tom Hanks",
                "correct_answer_fr": "Tom Hanks",
                "choices_en": [
                    {"text": "Johnny Depp", "is_correct": False},
                    {"text": "Tom Hanks", "is_correct": True},
                    {"text": "Brad Pitt", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Johnny Depp", "is_correct": False},
                    {"text": "Tom Hanks", "is_correct": True},
                    {"text": "Brad Pitt", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Johnny Depp", "is_correct": False},
                    {"text": "Tom Hanks", "is_correct": True},
                    {"text": "Brad Pitt", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which ship belonged to Han Solo in Star Wars?",
                "text_de": "Welches Schiff gehörte Han Solo in Star Wars?",
                "text_fr": "Quel vaisseau appartenait à Han Solo dans Star Wars ?",
                "correct_answer_en": "Millennium Falcon",
                "correct_answer_de": "Millennium Falcon",
                "correct_answer_fr": "Le Faucon Millenium",
                "choices_en": [
                    {"text": "Razor Crest", "is_correct": False},
                    {"text": "Star Destroyer", "is_correct": False},
                    {"text": "Millennium Falcon", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Razor Crest", "is_correct": False},
                    {"text": "Star Destroyer", "is_correct": False},
                    {"text": "Millennium Falcon", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Le Razor Crest", "is_correct": False},
                    {"text": "Le Star Destroyer", "is_correct": False},
                    {"text": "Le Faucon Millenium", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": 'Who wrote the novel "The Lord of the Rings"?',
                "text_de": "Wer schrieb den Roman „Der Herr der Ringe“?",
                "text_fr": "Qui a écrit le roman « Le Seigneur des anneaux » ?",
                "correct_answer_en": "J.R.R. Tolkien",
                "correct_answer_de": "J.R.R. Tolkien",
                "correct_answer_fr": "J.R.R. Tolkien",
                "choices_en": [
                    {"text": "J.R.R. Tolkien", "is_correct": True},
                    {"text": "G.R.R. Martin", "is_correct": False},
                    {"text": "J.K. Rowling", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "J.R.R. Tolkien", "is_correct": True},
                    {"text": "G.R.R. Martin", "is_correct": False},
                    {"text": "J.K. Rowling", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "J.R.R. Tolkien", "is_correct": True},
                    {"text": "G.R.R. Martin", "is_correct": False},
                    {"text": "J.K. Rowling", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": 'What job does the main character Truman Burbank (played by Jim Carrey) have in the tragicomedy "The Truman Show"?',
                "text_de": "Welchen Beruf übt die Hauptfigur Truman Burbank (gespielt von Jim Carrey) in der Tragikomödie „Die Truman Show“ aus?",
                "text_fr": "Quel métier exerce le personnage principal Truman Burbank (joué par Jim Carrey) dans la tragicomédie « The Truman Show » ?",
                "correct_answer_en": "Insurance salesman",
                "correct_answer_de": "Versicherungskaufmann",
                "correct_answer_fr": "Agent d'assurance",
                "choices_en": [
                    {"text": "Bank clerk", "is_correct": False},
                    {"text": "Insurance salesman", "is_correct": True},
                    {"text": "Architect", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Bankangestellter", "is_correct": False},
                    {"text": "Versicherungskaufmann", "is_correct": True},
                    {"text": "Architekt", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Employé de banque", "is_correct": False},
                    {"text": "Agent d'assurance", "is_correct": True},
                    {"text": "Architecte", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "For which 1991 psychological thriller did Anthony Hopkins win the Oscar for Best Actor?",
                "text_de": "Für welchen Psychothriller aus dem Jahr 1991 gewann Anthony Hopkins einen Oscar als bester Hauptdarsteller?",
                "text_fr": "Pour quel thriller psychologique de 1991 Anthony Hopkins a-t-il remporté l'Oscar du meilleur acteur ?",
                "correct_answer_en": "The Silence of the Lambs",
                "correct_answer_de": "Das Schweigen der Lämmer",
                "correct_answer_fr": "Le Silence des agneaux",
                "choices_en": [
                    {"text": "The Silence of the Lambs", "is_correct": True},
                    {"text": "Basic Instinct", "is_correct": False},
                    {"text": "Se7en", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Das Schweigen der Lämmer", "is_correct": True},
                    {"text": "Basic Instinct", "is_correct": False},
                    {"text": "Sieben", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Le Silence des agneaux", "is_correct": True},
                    {"text": "Basic Instinct", "is_correct": False},
                    {"text": "Seven", "is_correct": False},
                ],
            },
            {
                # NEW — the source sheet had five film questions, this round needs six.
                "type": "multiple_choice",
                "text_en": "Which film features the famous line \"I'm gonna make him an offer he can't refuse\"?",
                "text_de": "In welchem Film fällt der berühmte Satz „Ich mache ihm ein Angebot, das er nicht ablehnen kann“?",
                "text_fr": "Dans quel film entend-on la célèbre réplique « Je vais lui faire une offre qu'il ne pourra pas refuser » ?",
                "correct_answer_en": "The Godfather",
                "correct_answer_de": "Der Pate",
                "correct_answer_fr": "Le Parrain",
                "choices_en": [
                    {"text": "The Godfather", "is_correct": True},
                    {"text": "Scarface", "is_correct": False},
                    {"text": "Goodfellas", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Der Pate", "is_correct": True},
                    {"text": "Scarface", "is_correct": False},
                    {"text": "Goodfellas", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Le Parrain", "is_correct": True},
                    {"text": "Scarface", "is_correct": False},
                    {"text": "Les Affranchis", "is_correct": False},
                ],
            },
        ],
    },
    # ======================================================================
    # ROUND 4 — Superhelden
    # ======================================================================
    {
        "title_en": "Superheroes",
        "title_de": "Superhelden",
        "title_fr": "Super-héros",
        "questions": [
            {
                "type": "multiple_choice",
                "text_en": "What material is Captain America's shield made of?",
                "text_de": "Aus welchem Material besteht Captain Americas Schild?",
                "text_fr": "En quel matériau est fait le bouclier de Captain America ?",
                "correct_answer_en": "Vibranium",
                "correct_answer_de": "Vibranium",
                "correct_answer_fr": "Le vibranium",
                "choices_en": [
                    {"text": "Adamantium", "is_correct": False},
                    {"text": "Vibranium", "is_correct": True},
                    {"text": "Uru", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Adamantium", "is_correct": False},
                    {"text": "Vibranium", "is_correct": True},
                    {"text": "Uru", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "L'adamantium", "is_correct": False},
                    {"text": "Le vibranium", "is_correct": True},
                    {"text": "L'uru", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "What is the name of Thor's magic hammer?",
                "text_de": "Wie heißt Thors magischer Hammer?",
                "text_fr": "Comment s'appelle le marteau magique de Thor ?",
                "correct_answer_en": "Mjolnir",
                "correct_answer_de": "Mjolnir",
                "correct_answer_fr": "Mjolnir",
                "choices_en": [
                    {"text": "Mjolnir", "is_correct": True},
                    {"text": "Gungnir", "is_correct": False},
                    {"text": "Stormbreaker", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Mjolnir", "is_correct": True},
                    {"text": "Gungnir", "is_correct": False},
                    {"text": "Stormbreaker", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Mjolnir", "is_correct": True},
                    {"text": "Gungnir", "is_correct": False},
                    {"text": "Stormbreaker", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "What is Cyclops's power in the X-Men?",
                "text_de": "Welche Fähigkeit hat Cyclops bei den X-Men?",
                "text_fr": "Quel est le pouvoir de Cyclope chez les X-Men ?",
                "correct_answer_en": "He fires energy beams from his eyes",
                "correct_answer_de": "Er schießt Energiestrahlen aus den Augen",
                "correct_answer_fr": "Il tire des rayons d'énergie par les yeux",
                "choices_en": [
                    {"text": "He can fly", "is_correct": False},
                    {"text": "He can move objects with his eyes", "is_correct": False},
                    {"text": "He fires energy beams from his eyes", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Er kann fliegen", "is_correct": False},
                    {
                        "text": "Er kann Gegenstände mit den Augen bewegen",
                        "is_correct": False,
                    },
                    {
                        "text": "Er schießt Energiestrahlen aus den Augen",
                        "is_correct": True,
                    },
                ],
                "choices_fr": [
                    {"text": "Il peut voler", "is_correct": False},
                    {
                        "text": "Il peut déplacer des objets avec les yeux",
                        "is_correct": False,
                    },
                    {
                        "text": "Il tire des rayons d'énergie par les yeux",
                        "is_correct": True,
                    },
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Who is the leader of the Avengers?",
                "text_de": "Wer ist der Anführer der Avengers?",
                "text_fr": "Qui est le chef des Avengers ?",
                "correct_answer_en": "Captain America",
                "correct_answer_de": "Captain America",
                "correct_answer_fr": "Captain America",
                "choices_en": [
                    {"text": "Captain America", "is_correct": True},
                    {"text": "Iron Man", "is_correct": False},
                    {"text": "Black Widow", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Captain America", "is_correct": True},
                    {"text": "Iron Man", "is_correct": False},
                    {"text": "Black Widow", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Captain America", "is_correct": True},
                    {"text": "Iron Man", "is_correct": False},
                    {"text": "Black Widow", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "On which island did Wonder Woman grow up?",
                "text_de": "Auf welcher Insel wuchs Wonder Woman auf?",
                "text_fr": "Sur quelle île Wonder Woman a-t-elle grandi ?",
                "correct_answer_en": "Themyscira",
                "correct_answer_de": "Themyscira",
                "correct_answer_fr": "Themyscira",
                "choices_en": [
                    {"text": "Themyscira", "is_correct": True},
                    {"text": "Atlantis", "is_correct": False},
                    {"text": "Asgard", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Themyscira", "is_correct": True},
                    {"text": "Atlantis", "is_correct": False},
                    {"text": "Asgard", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Themyscira", "is_correct": True},
                    {"text": "Atlantis", "is_correct": False},
                    {"text": "Asgard", "is_correct": False},
                ],
            },
            {
                # NEW — the source sheet had five superhero questions, this round needs six.
                "type": "multiple_choice",
                "text_en": "In which city does Batman fight crime?",
                "text_de": "In welcher Stadt kämpft Batman gegen das Verbrechen?",
                "text_fr": "Dans quelle ville Batman combat-il le crime ?",
                "correct_answer_en": "Gotham City",
                "correct_answer_de": "Gotham City",
                "correct_answer_fr": "Gotham City",
                "choices_en": [
                    {"text": "Metropolis", "is_correct": False},
                    {"text": "Gotham City", "is_correct": True},
                    {"text": "Star City", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Metropolis", "is_correct": False},
                    {"text": "Gotham City", "is_correct": True},
                    {"text": "Star City", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Metropolis", "is_correct": False},
                    {"text": "Gotham City", "is_correct": True},
                    {"text": "Star City", "is_correct": False},
                ],
            },
        ],
    },
    # ======================================================================
    # ROUND 5 — Geographie
    # ======================================================================
    {
        "title_en": "Geography",
        "title_de": "Geographie",
        "title_fr": "Géographie",
        "questions": [
            {
                "type": "multiple_choice",
                "text_en": "Which river is the longest in the world?",
                "text_de": "Welcher Fluss ist der längste der Welt?",
                "text_fr": "Quel est le fleuve le plus long du monde ?",
                "correct_answer_en": "The Nile",
                "correct_answer_de": "Nil",
                "correct_answer_fr": "Le Nil",
                "choices_en": [
                    {"text": "The Amazon", "is_correct": False},
                    {"text": "The Nile", "is_correct": True},
                    {"text": "The Mississippi", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Amazonas", "is_correct": False},
                    {"text": "Nil", "is_correct": True},
                    {"text": "Mississippi", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "L'Amazone", "is_correct": False},
                    {"text": "Le Nil", "is_correct": True},
                    {"text": "Le Mississippi", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which ocean lies between Africa and America?",
                "text_de": "Welcher Ozean liegt zwischen Afrika und Amerika?",
                "text_fr": "Quel océan se situe entre l'Afrique et l'Amérique ?",
                "correct_answer_en": "The Atlantic Ocean",
                "correct_answer_de": "Atlantischer Ozean",
                "correct_answer_fr": "L'océan Atlantique",
                "choices_en": [
                    {"text": "The Pacific Ocean", "is_correct": False},
                    {"text": "The Atlantic Ocean", "is_correct": True},
                    {"text": "The Indian Ocean", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Pazifischer Ozean", "is_correct": False},
                    {"text": "Atlantischer Ozean", "is_correct": True},
                    {"text": "Indischer Ozean", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "L'océan Pacifique", "is_correct": False},
                    {"text": "L'océan Atlantique", "is_correct": True},
                    {"text": "L'océan Indien", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which country is the largest in the world by area?",
                "text_de": "Welches Land ist flächenmäßig das größte der Welt?",
                "text_fr": "Quel est le plus grand pays du monde en superficie ?",
                "correct_answer_en": "Russia",
                "correct_answer_de": "Russland",
                "correct_answer_fr": "La Russie",
                "choices_en": [
                    {"text": "Canada", "is_correct": False},
                    {"text": "China", "is_correct": False},
                    {"text": "Russia", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "Kanada", "is_correct": False},
                    {"text": "China", "is_correct": False},
                    {"text": "Russland", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "Le Canada", "is_correct": False},
                    {"text": "La Chine", "is_correct": False},
                    {"text": "La Russie", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Which is the deepest lake in the world?",
                "text_de": "Welches ist der tiefste See der Welt?",
                "text_fr": "Quel est le lac le plus profond du monde ?",
                "correct_answer_en": "Lake Baikal",
                "correct_answer_de": "Baikalsee",
                "correct_answer_fr": "Le lac Baïkal",
                "choices_en": [
                    {"text": "The Caspian Sea", "is_correct": False},
                    {"text": "Lake Baikal", "is_correct": True},
                    {"text": "Lake Tanganyika", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Kaspisches Meer", "is_correct": False},
                    {"text": "Baikalsee", "is_correct": True},
                    {"text": "Tanganjikasee", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "La mer Caspienne", "is_correct": False},
                    {"text": "Le lac Baïkal", "is_correct": True},
                    {"text": "Le lac Tanganyika", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "What is the capital of Australia?",
                "text_de": "Was ist die Hauptstadt von Australien?",
                "text_fr": "Quelle est la capitale de l'Australie ?",
                "correct_answer_en": "Canberra",
                "correct_answer_de": "Canberra",
                "correct_answer_fr": "Canberra",
                "choices_en": [
                    {"text": "Sydney", "is_correct": False},
                    {"text": "Canberra", "is_correct": True},
                    {"text": "Melbourne", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Sydney", "is_correct": False},
                    {"text": "Canberra", "is_correct": True},
                    {"text": "Melbourne", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Sydney", "is_correct": False},
                    {"text": "Canberra", "is_correct": True},
                    {"text": "Melbourne", "is_correct": False},
                ],
            },
            {
                # NEW — the source sheet had five geography questions, this round
                # needs six. Kept local: Crush.lu plays in Luxembourg.
                "type": "multiple_choice",
                "text_en": "How many countries does Luxembourg share a border with?",
                "text_de": "An wie viele Länder grenzt Luxemburg?",
                "text_fr": "Avec combien de pays le Luxembourg partage-t-il une frontière ?",
                "correct_answer_en": "Three (Belgium, Germany, France)",
                "correct_answer_de": "Drei (Belgien, Deutschland, Frankreich)",
                "correct_answer_fr": "Trois (Belgique, Allemagne, France)",
                "choices_en": [
                    {"text": "Two", "is_correct": False},
                    {"text": "Three", "is_correct": True},
                    {"text": "Four", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Zwei", "is_correct": False},
                    {"text": "Drei", "is_correct": True},
                    {"text": "Vier", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Deux", "is_correct": False},
                    {"text": "Trois", "is_correct": True},
                    {"text": "Quatre", "is_correct": False},
                ],
            },
        ],
    },
    # ======================================================================
    # ROUND 6 — Geschichte & Politik
    # ======================================================================
    {
        "title_en": "History & Politics",
        "title_de": "Geschichte & Politik",
        "title_fr": "Histoire et politique",
        "questions": [
            {
                "type": "multiple_choice",
                "text_en": "In which year did the French Revolution begin with the storming of the Bastille?",
                "text_de": "In welchem Jahr begann mit dem Sturm auf die Bastille die Französische Revolution?",
                "text_fr": "En quelle année la Révolution française a-t-elle commencé avec la prise de la Bastille ?",
                "correct_answer_en": "1789",
                "correct_answer_de": "1789",
                "correct_answer_fr": "1789",
                "choices_en": [
                    {"text": "1776", "is_correct": False},
                    {"text": "1789", "is_correct": True},
                    {"text": "1815", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "1776", "is_correct": False},
                    {"text": "1789", "is_correct": True},
                    {"text": "1815", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "1776", "is_correct": False},
                    {"text": "1789", "is_correct": True},
                    {"text": "1815", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "Who was the first elected Chancellor of the Federal Republic of Germany after the Second World War?",
                "text_de": "Wer war der erste gewählte Bundeskanzler der Bundesrepublik Deutschland nach dem Zweiten Weltkrieg?",
                "text_fr": "Qui a été le premier chancelier élu de la République fédérale d'Allemagne après la Seconde Guerre mondiale ?",
                "correct_answer_en": "Konrad Adenauer",
                "correct_answer_de": "Konrad Adenauer",
                "correct_answer_fr": "Konrad Adenauer",
                "choices_en": [
                    {"text": "Konrad Adenauer", "is_correct": True},
                    {"text": "Ludwig Erhard", "is_correct": False},
                    {"text": "Willy Brandt", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Konrad Adenauer", "is_correct": True},
                    {"text": "Ludwig Erhard", "is_correct": False},
                    {"text": "Willy Brandt", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Konrad Adenauer", "is_correct": True},
                    {"text": "Ludwig Erhard", "is_correct": False},
                    {"text": "Willy Brandt", "is_correct": False},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "What was the name of the famous Greek commander who in the 4th century BC conquered a vast empire stretching from Greece to India?",
                "text_de": "Wie hieß der berühmte griechische Feldherr, der im 4. Jahrhundert v. Chr. ein riesiges Weltreich eroberte, das von Griechenland bis nach Indien reichte?",
                "text_fr": "Comment s'appelait le célèbre général grec qui, au IVe siècle av. J.-C., a conquis un immense empire allant de la Grèce jusqu'à l'Inde ?",
                "correct_answer_en": "Alexander the Great",
                "correct_answer_de": "Alexander der Große",
                "correct_answer_fr": "Alexandre le Grand",
                "choices_en": [
                    {"text": "Julius Caesar", "is_correct": False},
                    {"text": "Alexander the Great", "is_correct": True},
                    {"text": "Pericles", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "Julius Cäsar", "is_correct": False},
                    {"text": "Alexander der Große", "is_correct": True},
                    {"text": "Perikles", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "Jules César", "is_correct": False},
                    {"text": "Alexandre le Grand", "is_correct": True},
                    {"text": "Périclès", "is_correct": False},
                ],
            },
            {
                # The source sheet asked "In welchem *Land* wurde die Europäische
                # Union gegründet?" but offered years as answers. Reworded to
                # "Jahr"; 1993 is Maastricht, which created the Union proper.
                "type": "multiple_choice",
                "text_en": "In which year was the European Union founded?",
                "text_de": "In welchem Jahr wurde die Europäische Union gegründet?",
                "text_fr": "En quelle année l'Union européenne a-t-elle été fondée ?",
                "correct_answer_en": "1993 (Maastricht Treaty)",
                "correct_answer_de": "1993 (Vertrag von Maastricht)",
                "correct_answer_fr": "1993 (traité de Maastricht)",
                "choices_en": [
                    {"text": "1951", "is_correct": False},
                    {"text": "1957", "is_correct": False},
                    {"text": "1993", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "1951", "is_correct": False},
                    {"text": "1957", "is_correct": False},
                    {"text": "1993", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "1951", "is_correct": False},
                    {"text": "1957", "is_correct": False},
                    {"text": "1993", "is_correct": True},
                ],
            },
            {
                "type": "multiple_choice",
                "text_en": "How many federal states does Germany have?",
                "text_de": "Wie viele Bundesländer gibt es in Deutschland?",
                "text_fr": "Combien de Länder compte l'Allemagne ?",
                "correct_answer_en": "16",
                "correct_answer_de": "16",
                "correct_answer_fr": "16",
                "choices_en": [
                    {"text": "14", "is_correct": False},
                    {"text": "16", "is_correct": True},
                    {"text": "18", "is_correct": False},
                ],
                "choices_de": [
                    {"text": "14", "is_correct": False},
                    {"text": "16", "is_correct": True},
                    {"text": "18", "is_correct": False},
                ],
                "choices_fr": [
                    {"text": "14", "is_correct": False},
                    {"text": "16", "is_correct": True},
                    {"text": "18", "is_correct": False},
                ],
            },
            {
                # 720 since the June 2024 election; 705 was the 2019–2024 term.
                "type": "multiple_choice",
                "text_en": "How many members does the plenary of the European Parliament have?",
                "text_de": "Wie viele Mitglieder hat das Plenum des Europäischen Parlaments?",
                "text_fr": "Combien de membres compte la plénière du Parlement européen ?",
                "correct_answer_en": "720",
                "correct_answer_de": "720",
                "correct_answer_fr": "720",
                "choices_en": [
                    {"text": "500", "is_correct": False},
                    {"text": "705", "is_correct": False},
                    {"text": "720", "is_correct": True},
                ],
                "choices_de": [
                    {"text": "500", "is_correct": False},
                    {"text": "705", "is_correct": False},
                    {"text": "720", "is_correct": True},
                ],
                "choices_fr": [
                    {"text": "500", "is_correct": False},
                    {"text": "705", "is_correct": False},
                    {"text": "720", "is_correct": True},
                ],
            },
        ],
    },
]
