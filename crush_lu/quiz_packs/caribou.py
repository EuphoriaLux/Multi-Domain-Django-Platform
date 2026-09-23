"""Caribou general-knowledge quiz pack — 10 rounds x 6 questions, trilingual.

Built from a coach-supplied English question sheet ("Caribou quiz", 2026-09):
geography, current affairs, a song round, world food, a film-poster round,
science, art, music legends, film & TV, and odd facts. German and French are
editorial translations; the sheet's answers are kept as the key.

Shape
-----
Ten rounds rather than the six the other packs use, because that is what the
sheet holds. Nine rounds are ``open_ended`` — tables write their answer and the
host scores it by hand, so a free-text key with a short gloss is enough. Round 4
is the sheet's A/B/C/D round and stays four-option multiple choice. The sheet
marked no answer there; the key is Vietnam, Canada, Peru, Austria (the kipferl,
which Vienna exported to Paris), Brazil, Georgia — correct slots B, A, A, C, D,
B, so the answer position does not leak.

Round 2 is not fact-checked
---------------------------
The current-affairs round was written in September 2026 and postdates what the
pack author could verify: the 2026 BRICS host (India), Tim Curry's death, and a
country singer's recent death keyed as Dolly Parton. The coach's answers are
authoritative and are reproduced as given — **confirm them before the event**,
and expect the round to go stale; swap it out for later nights.

Media
-----
Round 3 (songs) carries no stimulus: the host plays each track from their own
device and tables name artist and title. No embed URLs are seeded, because
label-owned music videos are embed-blocked almost across the board (see the
``media_love`` pack's provenance notes) and a dead embed fails on the
projector, not in review. A coach can attach a verified YouTube/Spotify link
per question in the quiz authoring UI.

Round 5 is a picture round. The coach supplied the six film posters as a PDF;
images cannot be seeded from a pack (``media_url`` only accepts the four embed
hosts), so these questions seed media-less and come back in the
``pending_uploads`` manifest with the filename each poster should be uploaded
under. Note that the posters as supplied show the film title — crop or blur it
before uploading, or the round answers itself.

Edits to the sheet
------------------
Spelling fixed silently: Strait (not straight) of Hormuz, Oktoberfest (not
October fest), pottery (not potter). Two prompts were tightened:

* Batman (Turkey) is a city of several hundred thousand, not a village — the
  prompt says "town".
* "The only letter in no US state name" holds for the English names only; the
  French *Nouveau-Mexique* contains a Q. The DE and FR prompts therefore ask
  about the English names.
"""


def _open(text, answer):
    """An open-ended question. *text* and *answer* are (en, de, fr) tuples."""
    return {
        "type": "open_ended",
        "text_en": text[0],
        "text_de": text[1],
        "text_fr": text[2],
        "correct_answer_en": answer[0],
        "correct_answer_de": answer[1],
        "correct_answer_fr": answer[2],
        "choices_en": [],
        "choices_de": [],
        "choices_fr": [],
    }


def _choice(text, options, correct, gloss=("", "", "")):
    """A multiple-choice question.

    *options* is a list of (en, de, fr) tuples; *correct* is the index of the
    right one. *gloss* is appended to the revealed answer per language, so the
    reveal can explain without the option text drifting from the key.
    """
    question = {
        "type": "multiple_choice",
        "text_en": text[0],
        "text_de": text[1],
        "text_fr": text[2],
    }
    for i, lang in enumerate(("en", "de", "fr")):
        question[f"correct_answer_{lang}"] = options[correct][i] + gloss[i]
        question[f"choices_{lang}"] = [
            {"text": option[i], "is_correct": index == correct}
            for index, option in enumerate(options)
        ]
    return question


def _song(number, answer):
    """A song-round question: the host plays the track, tables name it."""
    return _open(
        (
            f"Song {number}: name the artist and the title.",
            f"Song {number}: Nenne Interpret und Titel.",
            f"Chanson {number} : donnez l'artiste et le titre.",
        ),
        (answer, answer, answer),
    )


def _poster(number, answer, upload, description):
    """A picture-round question; the poster is uploaded by a coach."""
    question = _open(
        (
            f"Poster {number}: which film is this?",
            f"Plakat {number}: Welcher Film ist das?",
            f"Affiche {number} : quel est ce film ?",
        ),
        answer,
    )
    question["media"] = {
        "kind": "image",
        "upload": upload,
        "description": description,
        "credit": (
            f"Coach-supplied poster (Caribou quiz PDF, {answer[0]}); studio key "
            "art — crop the title out before uploading."
        ),
    }
    return question


QUIZ_ROUNDS = [
    # ======================================================================
    # ROUND 1 — Geography
    # ======================================================================
    {
        "title_en": "Geography",
        "title_de": "Geografie",
        "title_fr": "Géographie",
        "questions": [
            _open(
                (
                    "Which country has the longest coastline in the world?",
                    "Welches Land hat die längste Küstenlinie der Welt?",
                    "Quel pays possède le plus long littoral du monde ?",
                ),
                ("Canada", "Kanada", "Le Canada"),
            ),
            _open(
                (
                    "What is the smallest country in the world?",
                    "Welches ist das kleinste Land der Welt?",
                    "Quel est le plus petit pays du monde ?",
                ),
                ("Vatican City", "Vatikanstadt", "Le Vatican"),
            ),
            _open(
                (
                    "Which river flows through Budapest?",
                    "Welcher Fluss fließt durch Budapest?",
                    "Quel fleuve traverse Budapest ?",
                ),
                ("The Danube", "Die Donau", "Le Danube"),
            ),
            _open(
                (
                    "Which city is the world's southernmost national capital?",
                    "Welche Stadt ist die südlichste Hauptstadt eines Staates?",
                    "Quelle ville est la capitale nationale la plus australe du monde ?",
                ),
                (
                    "Wellington, New Zealand",
                    "Wellington, Neuseeland",
                    "Wellington, Nouvelle-Zélande",
                ),
            ),
            _open(
                (
                    "Which African country has more pyramids than Egypt?",
                    "Welches afrikanische Land hat mehr Pyramiden als Ägypten?",
                    "Quel pays africain compte plus de pyramides que l'Égypte ?",
                ),
                ("Sudan", "Sudan", "Le Soudan"),
            ),
            _open(
                (
                    "Which country is home to the ancient city of Petra?",
                    "In welchem Land liegt die antike Stadt Petra?",
                    "Dans quel pays se trouve la cité antique de Pétra ?",
                ),
                ("Jordan", "Jordanien", "La Jordanie"),
            ),
        ],
    },
    # ======================================================================
    # ROUND 2 — Current affairs (coach-keyed, not fact-checked; see docstring)
    # ======================================================================
    {
        "title_en": "Current Affairs",
        "title_de": "Aktuelles",
        "title_fr": "Actualité",
        "questions": [
            _open(
                (
                    'What triggered the protests in Nepal that became known as the "Gen Z protests"?',
                    "Was löste die Proteste in Nepal aus, die als „Gen-Z-Proteste“ bekannt wurden?",
                    "Qu'est-ce qui a déclenché les manifestations au Népal connues comme les « manifestations de la génération Z » ?",
                ),
                (
                    "A ban on social media",
                    "Ein Verbot sozialer Medien",
                    "L'interdiction des réseaux sociaux",
                ),
            ),
            _open(
                (
                    "Which country hosted the 2026 BRICS summit in September?",
                    "Welches Land war im September Gastgeber des BRICS-Gipfels 2026?",
                    "Quel pays a accueilli le sommet des BRICS 2026 en septembre ?",
                ),
                ("India", "Indien", "L'Inde"),
            ),
            _open(
                (
                    'Which famous British actor, who played Pennywise in the original "It", died in 2026?',
                    "Welcher berühmte britische Schauspieler, der im ursprünglichen „Es“ Pennywise spielte, starb 2026?",
                    "Quel célèbre acteur britannique, interprète de Grippe-Sou dans le premier « Ça », est décédé en 2026 ?",
                ),
                ("Tim Curry", "Tim Curry", "Tim Curry"),
            ),
            _open(
                (
                    "What is the name of Munich's famous beer festival, which starts at the end of every September?",
                    "Wie heißt das berühmte Münchner Bierfest, das jedes Jahr Ende September beginnt?",
                    "Comment s'appelle la célèbre fête de la bière de Munich, qui commence chaque année fin septembre ?",
                ),
                ("Oktoberfest", "Oktoberfest", "L'Oktoberfest"),
            ),
            _open(
                (
                    "Which strait is at the centre of the Iran–US conflict?",
                    "Welche Meerenge steht im Mittelpunkt des Konflikts zwischen dem Iran und den USA?",
                    "Quel détroit est au cœur du conflit entre l'Iran et les États-Unis ?",
                ),
                ("The Strait of Hormuz", "Die Straße von Hormus", "Le détroit d'Ormuz"),
            ),
            _open(
                (
                    "Which country music star, whose career spanned an impressive six decades, recently passed away?",
                    "Welcher Country-Star, dessen Karriere beeindruckende sechs Jahrzehnte umspannte, ist kürzlich verstorben?",
                    "Quelle star de la musique country, dont la carrière a duré six décennies, est récemment décédée ?",
                ),
                ("Dolly Parton", "Dolly Parton", "Dolly Parton"),
            ),
        ],
    },
    # ======================================================================
    # ROUND 3 — Name that song (host plays each track)
    # ======================================================================
    {
        "title_en": "Name That Song",
        "title_de": "Erkenne den Song",
        "title_fr": "Blind test",
        # The track plays first, then tables write — longer than a text question.
        "time_per_question": 45,
        "questions": [
            _song(1, "Justin Timberlake — SexyBack"),
            _song(2, "Nelly — Hot in Herre"),
            _song(3, "Chris Isaak — Wicked Game"),
            _song(4, "The Weeknd — Earned It"),
            _song(5, "Marvin Gaye — Let's Get It On"),
            _song(6, "Rihanna — Kiss It Better"),
        ],
    },
    # ======================================================================
    # ROUND 4 — World food (the sheet's A/B/C/D round)
    # ======================================================================
    {
        "title_en": "World Food",
        "title_de": "Küche der Welt",
        "title_fr": "Cuisine du monde",
        "questions": [
            _choice(
                (
                    "Pho is a famous noodle soup from which country?",
                    "Pho ist eine berühmte Nudelsuppe aus welchem Land?",
                    "Le pho est une célèbre soupe de nouilles de quel pays ?",
                ),
                [
                    ("Thailand", "Thailand", "Thaïlande"),
                    ("Vietnam", "Vietnam", "Vietnam"),
                    ("Cambodia", "Kambodscha", "Cambodge"),
                    ("Laos", "Laos", "Laos"),
                ],
                correct=1,
            ),
            _choice(
                (
                    "In which country did poutine originate?",
                    "Aus welchem Land stammt Poutine?",
                    "De quel pays la poutine est-elle originaire ?",
                ),
                [
                    ("Canada", "Kanada", "Canada"),
                    ("USA", "USA", "États-Unis"),
                    ("France", "Frankreich", "France"),
                    ("Belgium", "Belgien", "Belgique"),
                ],
                correct=0,
            ),
            _choice(
                (
                    "Which country is traditionally associated with ceviche?",
                    "Mit welchem Land wird Ceviche traditionell verbunden?",
                    "À quel pays associe-t-on traditionnellement le ceviche ?",
                ),
                [
                    ("Peru", "Peru", "Pérou"),
                    ("Brazil", "Brasilien", "Brésil"),
                    ("Argentina", "Argentinien", "Argentine"),
                    ("Colombia", "Kolumbien", "Colombie"),
                ],
                correct=0,
            ),
            _choice(
                (
                    "Croissants are strongly associated with France, but which country does the croissant originate from?",
                    "Croissants verbindet man mit Frankreich – aber aus welchem Land stammt das Croissant ursprünglich?",
                    "On associe le croissant à la France, mais de quel pays est-il originaire ?",
                ),
                [
                    ("Belgium", "Belgien", "Belgique"),
                    ("China", "China", "Chine"),
                    ("Austria", "Österreich", "Autriche"),
                    ("Portugal", "Portugal", "Portugal"),
                ],
                correct=2,
                gloss=(" (the kipferl)", " (das Kipferl)", " (le kipferl)"),
            ),
            _choice(
                (
                    "Which country is traditionally associated with feijoada, a stew of beans and meat?",
                    "Mit welchem Land wird Feijoada, ein Eintopf aus Bohnen und Fleisch, traditionell verbunden?",
                    "À quel pays associe-t-on traditionnellement la feijoada, un ragoût de haricots et de viande ?",
                ),
                [
                    ("Iceland", "Island", "Islande"),
                    ("Guatemala", "Guatemala", "Guatemala"),
                    ("Spain", "Spanien", "Espagne"),
                    ("Brazil", "Brasilien", "Brésil"),
                ],
                correct=3,
            ),
            _choice(
                (
                    "Which country is home to khinkali, a traditional dumpling?",
                    "Aus welchem Land stammen Chinkali, traditionelle Teigtaschen?",
                    "De quel pays viennent les khinkali, des raviolis traditionnels ?",
                ),
                [
                    ("Ukraine", "Ukraine", "Ukraine"),
                    ("Georgia", "Georgien", "Géorgie"),
                    ("Poland", "Polen", "Pologne"),
                    ("Latvia", "Lettland", "Lettonie"),
                ],
                correct=1,
            ),
        ],
    },
    # ======================================================================
    # ROUND 5 — Name that film (poster picture round; uploads pending)
    # ======================================================================
    {
        "title_en": "Name That Film",
        "title_de": "Erkenne den Film",
        "title_fr": "Devinez le film",
        "time_per_question": 45,
        "questions": [
            _poster(
                1,
                ("The Truman Show", "Die Truman Show", "The Truman Show"),
                "caribou-truman-show.jpg",
                "Film poster: a man's smiling face made as a mosaic of TV stills against a blue sky.",
            ),
            _poster(
                2,
                ("Pulp Fiction", "Pulp Fiction", "Pulp Fiction"),
                "caribou-pulp-fiction.jpg",
                "Film poster: a dark-haired woman lying on a bed, smoking, beside a pistol and a paperback.",
            ),
            _poster(
                3,
                (
                    "No Country for Old Men",
                    "No Country for Old Men",
                    "No Country for Old Men",
                ),
                "caribou-no-country-for-old-men.jpg",
                "Film poster: a man running with a rifle and a case, under a giant pair of staring eyes.",
            ),
            _poster(
                4,
                ("Fight Club", "Fight Club", "Fight Club"),
                "caribou-fight-club.jpg",
                "Film poster: a fist holding up a pink bar of soap above two men's faces.",
            ),
            _poster(
                5,
                (
                    "The Devil Wears Prada",
                    "Der Teufel trägt Prada",
                    "Le Diable s'habille en Prada",
                ),
                "caribou-devil-wears-prada.jpg",
                "Film poster: a red stiletto shoe whose heel ends in a devil's pitchfork.",
            ),
            _poster(
                6,
                ("Trainspotting", "Trainspotting", "Trainspotting"),
                "caribou-trainspotting.jpg",
                "Film poster: five numbered young people in black and white beside an orange stripe.",
            ),
        ],
    },
    # ======================================================================
    # ROUND 6 — Science
    # ======================================================================
    {
        "title_en": "Science",
        "title_de": "Wissenschaft",
        "title_fr": "Sciences",
        "questions": [
            _open(
                (
                    "What is the chemical symbol for gold?",
                    "Was ist das chemische Symbol für Gold?",
                    "Quel est le symbole chimique de l'or ?",
                ),
                ("Au", "Au", "Au"),
            ),
            _open(
                (
                    "Which gas makes up roughly 78% of Earth's atmosphere?",
                    "Welches Gas macht rund 78 % der Erdatmosphäre aus?",
                    "Quel gaz représente environ 78 % de l'atmosphère terrestre ?",
                ),
                ("Nitrogen", "Stickstoff", "L'azote"),
            ),
            _open(
                (
                    "What is the hardest naturally occurring mineral?",
                    "Welches ist das härteste natürlich vorkommende Mineral?",
                    "Quel est le minéral naturel le plus dur ?",
                ),
                ("Diamond", "Diamant", "Le diamant"),
            ),
            _open(
                (
                    "What is the largest organ in the human body?",
                    "Welches ist das größte Organ des menschlichen Körpers?",
                    "Quel est le plus grand organe du corps humain ?",
                ),
                ("The skin", "Die Haut", "La peau"),
            ),
            _open(
                (
                    "Which phenomenon causes the apparent change in pitch of a siren as an ambulance passes you?",
                    "Welches Phänomen lässt die Tonhöhe einer Sirene sich scheinbar ändern, wenn ein Krankenwagen an dir vorbeifährt?",
                    "Quel phénomène explique le changement apparent de hauteur d'une sirène lorsqu'une ambulance passe devant vous ?",
                ),
                ("The Doppler effect", "Der Doppler-Effekt", "L'effet Doppler"),
            ),
            _open(
                (
                    "What is the process by which a caterpillar transforms into a butterfly called?",
                    "Wie heißt der Prozess, bei dem sich eine Raupe in einen Schmetterling verwandelt?",
                    "Comment appelle-t-on le processus par lequel une chenille se transforme en papillon ?",
                ),
                ("Metamorphosis", "Metamorphose", "La métamorphose"),
            ),
        ],
    },
    # ======================================================================
    # ROUND 7 — Art
    # ======================================================================
    {
        "title_en": "Art",
        "title_de": "Kunst",
        "title_fr": "Art",
        "questions": [
            _open(
                (
                    "Who painted the Mona Lisa?",
                    "Wer malte die Mona Lisa?",
                    "Qui a peint La Joconde ?",
                ),
                ("Leonardo da Vinci", "Leonardo da Vinci", "Léonard de Vinci"),
            ),
            _open(
                (
                    'Which artist created the sculpture "The Thinker"?',
                    "Welcher Künstler schuf die Skulptur „Der Denker“?",
                    "Quel artiste a créé la sculpture « Le Penseur » ?",
                ),
                ("Auguste Rodin", "Auguste Rodin", "Auguste Rodin"),
            ),
            _open(
                (
                    "Which Mexican artist was famous for her self-portraits and distinctive unibrow?",
                    "Welche mexikanische Künstlerin war berühmt für ihre Selbstporträts und ihre markante Monobraue?",
                    "Quelle artiste mexicaine était célèbre pour ses autoportraits et son monosourcil caractéristique ?",
                ),
                ("Frida Kahlo", "Frida Kahlo", "Frida Kahlo"),
            ),
            _open(
                (
                    'Which Spanish artist co-founded Cubism and painted "Guernica"?',
                    "Welcher spanische Künstler begründete den Kubismus mit und malte „Guernica“?",
                    "Quel artiste espagnol a cofondé le cubisme et peint « Guernica » ?",
                ),
                ("Pablo Picasso", "Pablo Picasso", "Pablo Picasso"),
            ),
            _open(
                (
                    "In which city would you find Antoni Gaudí's church that was recently, symbolically completed?",
                    "In welcher Stadt steht Antoni Gaudís Kirche, die kürzlich symbolisch vollendet wurde?",
                    "Dans quelle ville se trouve l'église d'Antoni Gaudí, récemment achevée de manière symbolique ?",
                ),
                (
                    "Barcelona (the Sagrada Família)",
                    "Barcelona (die Sagrada Família)",
                    "Barcelone (la Sagrada Família)",
                ),
            ),
            _open(
                (
                    "What is the name of the Japanese art of repairing broken pottery with gold?",
                    "Wie heißt die japanische Kunst, zerbrochene Keramik mit Gold zu reparieren?",
                    "Comment s'appelle l'art japonais de réparer les céramiques cassées avec de l'or ?",
                ),
                ("Kintsugi", "Kintsugi", "Le kintsugi"),
            ),
        ],
    },
    # ======================================================================
    # ROUND 8 — Music legends
    # ======================================================================
    {
        "title_en": "Music Legends",
        "title_de": "Musiklegenden",
        "title_fr": "Légendes de la musique",
        "questions": [
            _open(
                (
                    'Which band released the albums "The Dark Side of the Moon" and "The Wall"?',
                    "Welche Band veröffentlichte die Alben „The Dark Side of the Moon“ und „The Wall“?",
                    "Quel groupe a sorti les albums « The Dark Side of the Moon » et « The Wall » ?",
                ),
                ("Pink Floyd", "Pink Floyd", "Pink Floyd"),
            ),
            _open(
                (
                    'Which artist is known as the "Queen of Pop"?',
                    "Welche Künstlerin ist als „Queen of Pop“ bekannt?",
                    "Quelle artiste est surnommée la « reine de la pop » ?",
                ),
                ("Madonna", "Madonna", "Madonna"),
            ),
            _open(
                (
                    "Which singer's real name is Stefani Germanotta?",
                    "Welche Sängerin heißt mit bürgerlichem Namen Stefani Germanotta?",
                    "Quelle chanteuse s'appelle en réalité Stefani Germanotta ?",
                ),
                ("Lady Gaga", "Lady Gaga", "Lady Gaga"),
            ),
            _open(
                (
                    "Which band had Freddie Mercury as its lead singer?",
                    "Bei welcher Band war Freddie Mercury Leadsänger?",
                    "De quel groupe Freddie Mercury était-il le chanteur ?",
                ),
                ("Queen", "Queen", "Queen"),
            ),
            _open(
                (
                    "What was Michael Jackson's first solo album?",
                    "Wie hieß das erste Soloalbum von Michael Jackson?",
                    "Quel fut le premier album solo de Michael Jackson ?",
                ),
                (
                    "Got to Be There (1972)",
                    "Got to Be There (1972)",
                    "Got to Be There (1972)",
                ),
            ),
            _open(
                (
                    'Which American band released the album "Nevermind"?',
                    "Welche US-amerikanische Band veröffentlichte das Album „Nevermind“?",
                    "Quel groupe américain a sorti l'album « Nevermind » ?",
                ),
                ("Nirvana", "Nirvana", "Nirvana"),
            ),
        ],
    },
    # ======================================================================
    # ROUND 9 — Film & TV
    # ======================================================================
    {
        "title_en": "Film & TV",
        "title_de": "Film & Fernsehen",
        "title_fr": "Cinéma et séries",
        "questions": [
            _open(
                (
                    'What is the name of the coffee shop where the characters regularly meet in "Friends"?',
                    "Wie heißt das Café, in dem sich die Figuren in „Friends“ regelmäßig treffen?",
                    "Comment s'appelle le café où se retrouvent les personnages de « Friends » ?",
                ),
                ("Central Perk", "Central Perk", "Central Perk"),
            ),
            _open(
                (
                    'Which actor played the lead pirate in "Pirates of the Caribbean"?',
                    "Welcher Schauspieler spielte den Piraten in der Hauptrolle von „Fluch der Karibik“?",
                    "Quel acteur a joué le pirate principal de « Pirates des Caraïbes » ?",
                ),
                (
                    "Johnny Depp (Captain Jack Sparrow)",
                    "Johnny Depp (Captain Jack Sparrow)",
                    "Johnny Depp (le capitaine Jack Sparrow)",
                ),
            ),
            _open(
                (
                    'What is the fictional African country in "Black Panther"?',
                    "Wie heißt das fiktive afrikanische Land in „Black Panther“?",
                    "Quel est le pays africain fictif de « Black Panther » ?",
                ),
                ("Wakanda", "Wakanda", "Le Wakanda"),
            ),
            _open(
                (
                    "Which TV series follows the Shelby family and their criminal empire in Birmingham?",
                    "Welche Serie erzählt von der Familie Shelby und ihrem kriminellen Imperium in Birmingham?",
                    "Quelle série suit la famille Shelby et son empire criminel à Birmingham ?",
                ),
                ("Peaky Blinders", "Peaky Blinders", "Peaky Blinders"),
            ),
            _open(
                (
                    'What is the name of the hotel in "The Shining"?',
                    "Wie heißt das Hotel in „Shining“?",
                    "Comment s'appelle l'hôtel de « Shining » ?",
                ),
                ("The Overlook Hotel", "Das Overlook Hotel", "L'hôtel Overlook"),
            ),
            _open(
                (
                    'Which film features the line "There\'s no place like home"?',
                    "In welchem Film fällt der Satz „There's no place like home“?",
                    "Quel film contient la réplique « There's no place like home » ?",
                ),
                ("The Wizard of Oz", "Der Zauberer von Oz", "Le Magicien d'Oz"),
            ),
        ],
    },
    # ======================================================================
    # ROUND 10 — Odd facts
    # ======================================================================
    {
        "title_en": "Odd Facts",
        "title_de": "Kurioses",
        "title_fr": "Insolite",
        "questions": [
            _open(
                (
                    "What is the only letter that doesn't appear in the name of any US state?",
                    "Welcher Buchstabe kommt als einziger in keinem (englischen) Namen eines US-Bundesstaats vor?",
                    "Quelle est la seule lettre absente du nom (en anglais) de tous les États américains ?",
                ),
                ("Q", "Q", "Q"),
            ),
            _open(
                (
                    "Which country has a unicorn as its national animal?",
                    "Welches Land hat ein Einhorn als Nationaltier?",
                    "Quel pays a pour animal national la licorne ?",
                ),
                ("Scotland", "Schottland", "L'Écosse"),
            ),
            _open(
                (
                    "Which animal is famous for producing cube-shaped poop?",
                    "Welches Tier ist berühmt für seinen würfelförmigen Kot?",
                    "Quel animal est célèbre pour ses crottes en forme de cube ?",
                ),
                ("The wombat", "Der Wombat", "Le wombat"),
            ),
            _open(
                (
                    "In which part of the human body are the smallest bones found?",
                    "In welchem Teil des menschlichen Körpers befinden sich die kleinsten Knochen?",
                    "Dans quelle partie du corps humain trouve-t-on les plus petits os ?",
                ),
                (
                    "The ear (middle ear)",
                    "Im Ohr (Mittelohr)",
                    "L'oreille (oreille moyenne)",
                ),
            ),
            _open(
                (
                    "Which country has a town called Batman?",
                    "In welchem Land gibt es eine Stadt namens Batman?",
                    "Dans quel pays se trouve une ville appelée Batman ?",
                ),
                ("Turkey", "Die Türkei", "La Turquie"),
            ),
            _open(
                (
                    'What colour is an aeroplane\'s "black box" actually painted?',
                    "In welcher Farbe ist die „Blackbox“ eines Flugzeugs tatsächlich lackiert?",
                    "De quelle couleur la « boîte noire » d'un avion est-elle réellement peinte ?",
                ),
                ("Orange", "Orange", "Orange"),
            ),
        ],
    },
]
