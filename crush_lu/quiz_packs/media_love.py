"""Media-edition Crush.lu quiz pack — dating & love, built around clips.

Same subject matter as the ``classic`` pack, rebuilt so that the stimulus does
the work: rom-com scenes, love songs, famous-couple photographs. 6 rounds x 6
questions, trilingual (EN, DE, FR).

See ``crush_lu.quiz_packs`` for the pack schema and the two shapes the optional
``media`` block can take.

Media provenance
----------------
Every embed URL below was **loaded in a real iframe from an http origin** on
**2026-08-11** and confirmed to play. That is the only check that works — three
cheaper-looking methods all lie:

* oEmbed succeeds for embed-blocked videos (it only proves the video exists);
* the YouTube IFrame Player API fires ``onReady`` for embed-blocked videos too,
  because the 101/150 error only arrives on a play attempt;
* opening ``youtube-nocookie.com/embed/<id>`` as a top-level page returns
  ``Error 153`` for *every* video, working ones included.

Record labels disable embedding on essentially all of their own uploads. An
earlier revision of this pack pointed the whole music round at VEVO and official
artist channels; 5 of its 6 clips turned out to be embed-blocked when the pack
was first test-driven. The surviving sources here are broadcasters (BBC, INA),
one official-artist *live* upload, and two third-party re-uploads. Film studios,
by contrast, allow embedding: every Round 1 trailer works.

Two things no check can rule out: the owner may disable embedding at any time,
and a video can be geo-blocked in Luxembourg while it plays elsewhere. So the
URLs are load-bearing but not immortal. Play the media round on the projector
once before the doors open; a coach can swap any URL through the quiz authoring
UI without touching this file.

Round 3 carries no URLs at all: the embed allowlist serves the four video/audio
providers only, so the couple photographs seed media-less and come back as a
``pending_uploads`` manifest. Each entry's ``credit`` is a search hint rather
than a fixed link — confirm the licence on the file you actually upload.
"""

# --- Round 1: rom-com trailers -------------------------------------------
_ROUND_ON_SCREEN = {
    "title_en": "Love on Screen",
    "title_de": "Liebe auf der Leinwand",
    "title_fr": "L'amour à l'écran",
    # Trailers need longer than a text question: the clip runs, then they think.
    "time_per_question": 45,
    "questions": [
        {
            "type": "multiple_choice",
            "text_en": "What kind of shop does Hugh Grant's character own in this film?",
            "text_de": "Was für einen Laden führt die Figur von Hugh Grant in diesem Film?",
            "text_fr": "Quel genre de boutique tient le personnage de Hugh Grant dans ce film ?",
            "correct_answer_en": "A travel bookshop",
            "correct_answer_de": "Eine Reisebuchhandlung",
            "correct_answer_fr": "Une librairie de voyage",
            "choices_en": [
                {"text": "A travel bookshop", "is_correct": True},
                {"text": "A record shop", "is_correct": False},
                {"text": "A flower shop", "is_correct": False},
                {"text": "A bakery", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Eine Reisebuchhandlung", "is_correct": True},
                {"text": "Einen Plattenladen", "is_correct": False},
                {"text": "Ein Blumengeschäft", "is_correct": False},
                {"text": "Eine Bäckerei", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Une librairie de voyage", "is_correct": True},
                {"text": "Un magasin de disques", "is_correct": False},
                {"text": "Un fleuriste", "is_correct": False},
                {"text": "Une boulangerie", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # Movieclips
                "url": "https://www.youtube.com/watch?v=4RI0QvaGoiI",
                "description": (
                    "Trailer for the 1999 romantic comedy Notting Hill, in which a "
                    "London bookshop owner falls for a Hollywood actress."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "In which Paris neighbourhood is this film set?",
            "text_de": "In welchem Pariser Viertel spielt dieser Film?",
            "text_fr": "Dans quel quartier de Paris se déroule ce film ?",
            "correct_answer_en": "Montmartre",
            "correct_answer_de": "Montmartre",
            "correct_answer_fr": "Montmartre",
            "choices_en": [
                {"text": "Montmartre", "is_correct": True},
                {"text": "Le Marais", "is_correct": False},
                {"text": "Saint-Germain-des-Prés", "is_correct": False},
                {"text": "Belleville", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Montmartre", "is_correct": True},
                {"text": "Le Marais", "is_correct": False},
                {"text": "Saint-Germain-des-Prés", "is_correct": False},
                {"text": "Belleville", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Montmartre", "is_correct": True},
                {"text": "Le Marais", "is_correct": False},
                {"text": "Saint-Germain-des-Prés", "is_correct": False},
                {"text": "Belleville", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # Sony Pictures Classics
                "url": "https://www.youtube.com/watch?v=Py7cDXQae2U",
                "description": (
                    "Trailer for Amélie (2001), Jean-Pierre Jeunet's film about a shy "
                    "Parisian waitress who secretly arranges other people's happiness."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "Which song from this film won the Oscar for Best Original Song?",
            "text_de": "Welches Lied aus diesem Film gewann den Oscar für den besten Song?",
            "text_fr": "Quelle chanson de ce film a remporté l'Oscar de la meilleure chanson originale ?",
            "correct_answer_en": "City of Stars",
            "correct_answer_de": "City of Stars",
            "correct_answer_fr": "City of Stars",
            "choices_en": [
                {"text": "City of Stars", "is_correct": True},
                {"text": "Audition (The Fools Who Dream)", "is_correct": False},
                {"text": "Another Day of Sun", "is_correct": False},
                {"text": "Someone in the Crowd", "is_correct": False},
            ],
            "choices_de": [
                {"text": "City of Stars", "is_correct": True},
                {"text": "Audition (The Fools Who Dream)", "is_correct": False},
                {"text": "Another Day of Sun", "is_correct": False},
                {"text": "Someone in the Crowd", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "City of Stars", "is_correct": True},
                {"text": "Audition (The Fools Who Dream)", "is_correct": False},
                {"text": "Another Day of Sun", "is_correct": False},
                {"text": "Someone in the Crowd", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # Lionsgate Movies
                "url": "https://www.youtube.com/watch?v=0pdqf4P9MB8",
                "description": (
                    "Trailer for La La Land (2016), a musical about a jazz pianist and "
                    "an aspiring actress falling in love in Los Angeles."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": 'Complete this film\'s most quoted line: "Nobody puts Baby …"',
            "text_de": 'Vervollständige den bekanntesten Satz des Films: „Nobody puts Baby …"',
            "text_fr": "Complétez la réplique la plus célèbre du film : « Nobody puts Baby… »",
            "correct_answer_en": "… in a corner",
            "correct_answer_de": "… in a corner (in die Ecke)",
            "correct_answer_fr": "… in a corner (dans un coin)",
            "choices_en": [
                {"text": "… in a corner", "is_correct": True},
                {"text": "… on the stage", "is_correct": False},
                {"text": "… in the water", "is_correct": False},
                {"text": "… to bed", "is_correct": False},
            ],
            "choices_de": [
                {"text": "… in a corner (in die Ecke)", "is_correct": True},
                {"text": "… on the stage (auf die Bühne)", "is_correct": False},
                {"text": "… in the water (ins Wasser)", "is_correct": False},
                {"text": "… to bed (ins Bett)", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "… in a corner (dans un coin)", "is_correct": True},
                {"text": "… on the stage (sur scène)", "is_correct": False},
                {"text": "… in the water (dans l'eau)", "is_correct": False},
                {"text": "… to bed (au lit)", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # Rotten Tomatoes Classic Trailers
                "url": "https://www.youtube.com/watch?v=eIcmQNy9FsM",
                "description": (
                    "Trailer for Dirty Dancing (1987), in which a teenager falls for "
                    "the dance instructor at a summer resort."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": 'Who sang this film\'s theme song, "My Heart Will Go On"?',
            "text_de": 'Wer sang den Titelsong dieses Films, „My Heart Will Go On"?',
            "text_fr": "Qui a chanté la chanson de ce film, « My Heart Will Go On » ?",
            "correct_answer_en": "Céline Dion",
            "correct_answer_de": "Céline Dion",
            "correct_answer_fr": "Céline Dion",
            "choices_en": [
                {"text": "Céline Dion", "is_correct": True},
                {"text": "Mariah Carey", "is_correct": False},
                {"text": "Whitney Houston", "is_correct": False},
                {"text": "Cher", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Céline Dion", "is_correct": True},
                {"text": "Mariah Carey", "is_correct": False},
                {"text": "Whitney Houston", "is_correct": False},
                {"text": "Cher", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Céline Dion", "is_correct": True},
                {"text": "Mariah Carey", "is_correct": False},
                {"text": "Whitney Houston", "is_correct": False},
                {"text": "Cher", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # Paramount Movies
                "url": "https://www.youtube.com/watch?v=cIJ8ma0kKtY",
                "description": (
                    "Trailer for Titanic (1997), the story of two passengers from "
                    "different social classes who fall in love aboard the doomed liner."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "In which airport do this film's opening and closing scenes take place?",
            "text_de": "In welchem Flughafen spielen die Anfangs- und Schlussszenen dieses Films?",
            "text_fr": "Dans quel aéroport se déroulent les scènes d'ouverture et de fin de ce film ?",
            "correct_answer_en": "London Heathrow",
            "correct_answer_de": "London Heathrow",
            "correct_answer_fr": "Londres Heathrow",
            "choices_en": [
                {"text": "London Heathrow", "is_correct": True},
                {"text": "London Gatwick", "is_correct": False},
                {"text": "Paris Charles de Gaulle", "is_correct": False},
                {"text": "Luxembourg Findel", "is_correct": False},
            ],
            "choices_de": [
                {"text": "London Heathrow", "is_correct": True},
                {"text": "London Gatwick", "is_correct": False},
                {"text": "Paris Charles de Gaulle", "is_correct": False},
                {"text": "Luxemburg Findel", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Londres Heathrow", "is_correct": True},
                {"text": "Londres Gatwick", "is_correct": False},
                {"text": "Paris Charles de Gaulle", "is_correct": False},
                {"text": "Luxembourg Findel", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # STUDIOCANAL
                "url": "https://www.youtube.com/watch?v=r3mJrSA1x5s",
                "description": (
                    "Trailer for Love Actually (2003), which follows nine intertwined "
                    "love stories in the weeks before Christmas in London."
                ),
            },
        },
    ],
}

# --- Round 2: love songs --------------------------------------------------
_ROUND_LOVE_SONGS = {
    "title_en": "Name That Love Song",
    "title_de": "Errate den Liebessong",
    "title_fr": "Devinez la chanson d'amour",
    "time_per_question": 45,
    "questions": [
        {
            "type": "multiple_choice",
            "text_en": "This Elvis Presley classic was written for which 1961 film?",
            "text_de": "Dieser Elvis-Presley-Klassiker wurde für welchen Film von 1961 geschrieben?",
            "text_fr": "Ce classique d'Elvis Presley a été écrit pour quel film de 1961 ?",
            "correct_answer_en": "Blue Hawaii",
            "correct_answer_de": "Blue Hawaii",
            "correct_answer_fr": "Blue Hawaii",
            "choices_en": [
                {"text": "Blue Hawaii", "is_correct": True},
                {"text": "Jailhouse Rock", "is_correct": False},
                {"text": "Viva Las Vegas", "is_correct": False},
                {"text": "Love Me Tender", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Blue Hawaii", "is_correct": True},
                {"text": "Jailhouse Rock", "is_correct": False},
                {"text": "Viva Las Vegas", "is_correct": False},
                {"text": "Love Me Tender", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Blue Hawaii", "is_correct": True},
                {"text": "Jailhouse Rock", "is_correct": False},
                {"text": "Viva Las Vegas", "is_correct": False},
                {"text": "Love Me Tender", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # 7clouds lyric video. The ElvisPresleyVEVO official audio
                # (vGJTaP6anOU) is embed-blocked — see the provenance note above.
                "url": "https://www.youtube.com/watch?v=MqazV4hbu8E",
                "description": (
                    "Lyric video for Elvis Presley's \"Can't Help Falling in Love\", "
                    "a slow ballad with strings and backing vocals."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "Who wrote this song, years before Whitney Houston recorded it?",
            "text_de": "Wer schrieb dieses Lied, Jahre bevor Whitney Houston es aufnahm?",
            "text_fr": "Qui a écrit cette chanson, des années avant que Whitney Houston ne l'enregistre ?",
            "correct_answer_en": "Dolly Parton",
            "correct_answer_de": "Dolly Parton",
            "correct_answer_fr": "Dolly Parton",
            "choices_en": [
                {"text": "Dolly Parton", "is_correct": True},
                {"text": "Carole King", "is_correct": False},
                {"text": "Aretha Franklin", "is_correct": False},
                {"text": "Whitney Houston herself", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Dolly Parton", "is_correct": True},
                {"text": "Carole King", "is_correct": False},
                {"text": "Aretha Franklin", "is_correct": False},
                {"text": "Whitney Houston selbst", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Dolly Parton", "is_correct": True},
                {"text": "Carole King", "is_correct": False},
                {"text": "Aretha Franklin", "is_correct": False},
                {"text": "Whitney Houston elle-même", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # Remastered live performance. Every whitneyhoustonVEVO and lyric
                # upload of this song is embed-blocked (9 checked).
                "url": "https://www.youtube.com/watch?v=xXomfXQeA0Y",
                "description": (
                    'Whitney Houston performing "I Will Always Love You" live, '
                    "opening a cappella before the full band enters."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "In which decade did Édith Piaf first record this song?",
            "text_de": "In welchem Jahrzehnt nahm Édith Piaf dieses Lied zum ersten Mal auf?",
            "text_fr": "Au cours de quelle décennie Édith Piaf a-t-elle enregistré cette chanson pour la première fois ?",
            "correct_answer_en": "The 1940s",
            "correct_answer_de": "In den 1940er-Jahren",
            "correct_answer_fr": "Dans les années 1940",
            "choices_en": [
                {"text": "The 1940s", "is_correct": True},
                {"text": "The 1920s", "is_correct": False},
                {"text": "The 1960s", "is_correct": False},
                {"text": "The 1970s", "is_correct": False},
            ],
            "choices_de": [
                {"text": "In den 1940er-Jahren", "is_correct": True},
                {"text": "In den 1920er-Jahren", "is_correct": False},
                {"text": "In den 1960er-Jahren", "is_correct": False},
                {"text": "In den 1970er-Jahren", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Dans les années 1940", "is_correct": True},
                {"text": "Dans les années 1920", "is_correct": False},
                {"text": "Dans les années 1960", "is_correct": False},
                {"text": "Dans les années 1970", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # Edith Piaf Officiel — live version. The channel's studio audio
                # upload (-0KvBnIvTFs) is embed-blocked; this one is not.
                "url": "https://www.youtube.com/watch?v=rzeLynj1GYM",
                "description": (
                    'Édith Piaf performing "La vie en rose" live, a French chanson '
                    "with accordion and orchestra."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "On which Ed Sheeran album does this song appear?",
            "text_de": "Auf welchem Ed-Sheeran-Album erschien dieses Lied?",
            "text_fr": "Sur quel album d'Ed Sheeran figure cette chanson ?",
            "correct_answer_en": "÷ (Divide)",
            "correct_answer_de": "÷ (Divide)",
            "correct_answer_fr": "÷ (Divide)",
            "choices_en": [
                {"text": "÷ (Divide)", "is_correct": True},
                {"text": "+ (Plus)", "is_correct": False},
                {"text": "× (Multiply)", "is_correct": False},
                {"text": "= (Equals)", "is_correct": False},
            ],
            "choices_de": [
                {"text": "÷ (Divide)", "is_correct": True},
                {"text": "+ (Plus)", "is_correct": False},
                {"text": "× (Multiply)", "is_correct": False},
                {"text": "= (Equals)", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "÷ (Divide)", "is_correct": True},
                {"text": "+ (Plus)", "is_correct": False},
                {"text": "× (Multiply)", "is_correct": False},
                {"text": "= (Equals)", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # BBC Radio 1 Live Lounge. Ed Sheeran's own official music video
                # (2Vv-BfVoq4g) is embed-blocked; the BBC upload is not.
                "url": "https://www.youtube.com/watch?v=qxbS8qbBL0c",
                "description": (
                    'Ed Sheeran performing "Perfect" acoustically in the BBC Radio 1 '
                    "Live Lounge, accompanying himself on guitar."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "Jacques Brel, who wrote and sang this song, came from which country?",
            "text_de": "Aus welchem Land stammte Jacques Brel, der dieses Lied schrieb und sang?",
            "text_fr": "De quel pays venait Jacques Brel, qui a écrit et chanté cette chanson ?",
            "correct_answer_en": "Belgium",
            "correct_answer_de": "Belgien",
            "correct_answer_fr": "La Belgique",
            "choices_en": [
                {"text": "Belgium", "is_correct": True},
                {"text": "France", "is_correct": False},
                {"text": "Switzerland", "is_correct": False},
                {"text": "Luxembourg", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Belgien", "is_correct": True},
                {"text": "Frankreich", "is_correct": False},
                {"text": "Die Schweiz", "is_correct": False},
                {"text": "Luxemburg", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "La Belgique", "is_correct": True},
                {"text": "La France", "is_correct": False},
                {"text": "La Suisse", "is_correct": False},
                {"text": "Le Luxembourg", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # INA Chansons — official archive of French public broadcasting
                "url": "https://www.youtube.com/watch?v=n0ehZeWGXW0",
                "description": (
                    'Archive footage of Jacques Brel performing "Ne me quitte pas" '
                    "live on French television in the 1960s."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "This German band's biggest ballad comes from which 2006 album?",
            "text_de": "Die größte Ballade dieser deutschen Band stammt von welchem Album aus dem Jahr 2006?",
            "text_fr": "La plus grande ballade de ce groupe allemand vient de quel album de 2006 ?",
            "correct_answer_en": "Laut gedacht",
            "correct_answer_de": "Laut gedacht",
            "correct_answer_fr": "Laut gedacht",
            "choices_en": [
                {"text": "Laut gedacht", "is_correct": True},
                {"text": "Verschwende deine Zeit", "is_correct": False},
                {"text": "Nichts passiert", "is_correct": False},
                {"text": "Himmel auf", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Laut gedacht", "is_correct": True},
                {"text": "Verschwende deine Zeit", "is_correct": False},
                {"text": "Nichts passiert", "is_correct": False},
                {"text": "Himmel auf", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Laut gedacht", "is_correct": True},
                {"text": "Verschwende deine Zeit", "is_correct": False},
                {"text": "Nichts passiert", "is_correct": False},
                {"text": "Himmel auf", "is_correct": False},
            ],
            "media": {
                "kind": "video",
                # Audience recording, Linzer Kronefest 2016. The official
                # Silbermond video (LyYAQHDMqfA) and every lyric/broadcast upload
                # checked are embed-blocked. This is the weakest clip in the pack —
                # amateur audio. Swap it if a better embeddable source appears.
                "url": "https://www.youtube.com/watch?v=zcAlhddo5d8",
                "description": (
                    'Silbermond performing "Das Beste" live at a festival, a German '
                    "pop-rock love song."
                ),
            },
        },
    ],
}

# --- Round 3: famous couples (photographs, uploaded by a coach) -----------
_ROUND_FAMOUS_COUPLES = {
    "title_en": "Famous Couples",
    "title_de": "Berühmte Paare",
    "title_fr": "Couples célèbres",
    "questions": [
        {
            "type": "multiple_choice",
            "text_en": "Which duet did this couple famously record together?",
            "text_de": "Welches Duett nahm dieses Paar gemeinsam auf?",
            "text_fr": "Quel duo ce couple a-t-il enregistré ensemble ?",
            "correct_answer_en": "Jackson",
            "correct_answer_de": "Jackson",
            "correct_answer_fr": "Jackson",
            "choices_en": [
                {"text": "Jackson", "is_correct": True},
                {"text": "Islands in the Stream", "is_correct": False},
                {"text": "Summer Nights", "is_correct": False},
                {"text": "Endless Love", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Jackson", "is_correct": True},
                {"text": "Islands in the Stream", "is_correct": False},
                {"text": "Summer Nights", "is_correct": False},
                {"text": "Endless Love", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Jackson", "is_correct": True},
                {"text": "Islands in the Stream", "is_correct": False},
                {"text": "Summer Nights", "is_correct": False},
                {"text": "Endless Love", "is_correct": False},
            ],
            "media": {
                "kind": "image",
                "upload": "couple-cash-carter.jpg",
                "description": (
                    "Photograph of country singers Johnny Cash and June Carter Cash "
                    "performing together on stage."
                ),
                "credit": (
                    "Wikimedia Commons — search 'Johnny Cash June Carter'; confirm the "
                    "licence on the file you upload."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "What did this couple stage in 1969 as a protest for peace?",
            "text_de": "Was inszenierte dieses Paar 1969 als Protest für den Frieden?",
            "text_fr": "Qu'est-ce que ce couple a organisé en 1969 pour protester en faveur de la paix ?",
            "correct_answer_en": 'A "Bed-In" for Peace',
            "correct_answer_de": 'Ein „Bed-In" für den Frieden',
            "correct_answer_fr": "Un « Bed-In » pour la paix",
            "choices_en": [
                {"text": 'A "Bed-In" for Peace', "is_correct": True},
                {"text": "A hunger strike", "is_correct": False},
                {"text": "A silent march", "is_correct": False},
                {"text": "A rooftop concert", "is_correct": False},
            ],
            "choices_de": [
                {"text": 'Ein „Bed-In" für den Frieden', "is_correct": True},
                {"text": "Einen Hungerstreik", "is_correct": False},
                {"text": "Einen Schweigemarsch", "is_correct": False},
                {"text": "Ein Dachkonzert", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Un « Bed-In » pour la paix", "is_correct": True},
                {"text": "Une grève de la faim", "is_correct": False},
                {"text": "Une marche silencieuse", "is_correct": False},
                {"text": "Un concert sur un toit", "is_correct": False},
            ],
            "media": {
                "kind": "image",
                "upload": "couple-lennon-ono.jpg",
                "description": (
                    "Photograph of musician John Lennon and artist Yoko Ono together "
                    "in the late 1960s."
                ),
                "credit": (
                    "Wikimedia Commons — search 'John Lennon Yoko Ono 1969'; confirm "
                    "the licence on the file you upload."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "Where did this couple first meet?",
            "text_de": "Wo lernte sich dieses Paar kennen?",
            "text_fr": "Où ce couple s'est-il rencontré pour la première fois ?",
            "correct_answer_en": "At a Chicago law firm, where she was his adviser",
            "correct_answer_de": "In einer Anwaltskanzlei in Chicago, wo sie seine Mentorin war",
            "correct_answer_fr": "Dans un cabinet d'avocats de Chicago, où elle était sa référente",
            "choices_en": [
                {
                    "text": "At a Chicago law firm, where she was his adviser",
                    "is_correct": True,
                },
                {"text": "At Harvard Law School", "is_correct": False},
                {"text": "At a campaign rally", "is_correct": False},
                {"text": "At a church in Chicago", "is_correct": False},
            ],
            "choices_de": [
                {
                    "text": "In einer Anwaltskanzlei in Chicago, wo sie seine Mentorin war",
                    "is_correct": True,
                },
                {"text": "An der Harvard Law School", "is_correct": False},
                {"text": "Bei einer Wahlkampfveranstaltung", "is_correct": False},
                {"text": "In einer Kirche in Chicago", "is_correct": False},
            ],
            "choices_fr": [
                {
                    "text": "Dans un cabinet d'avocats de Chicago, où elle était sa référente",
                    "is_correct": True,
                },
                {"text": "À la faculté de droit de Harvard", "is_correct": False},
                {"text": "Lors d'un meeting de campagne", "is_correct": False},
                {"text": "Dans une église de Chicago", "is_correct": False},
            ],
            "media": {
                "kind": "image",
                "upload": "couple-obama.jpg",
                "description": (
                    "Photograph of Barack and Michelle Obama together at an official "
                    "event."
                ),
                "credit": (
                    "Wikimedia Commons — search 'Barack and Michelle Obama'; official "
                    "White House photographs are public domain, but confirm per file."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "In which country was Grand Duchess Maria Teresa born?",
            "text_de": "In welchem Land wurde Großherzogin Maria Teresa geboren?",
            "text_fr": "Dans quel pays est née la Grande-Duchesse Maria Teresa ?",
            "correct_answer_en": "Cuba",
            "correct_answer_de": "Kuba",
            "correct_answer_fr": "Cuba",
            "choices_en": [
                {"text": "Cuba", "is_correct": True},
                {"text": "Spain", "is_correct": False},
                {"text": "Portugal", "is_correct": False},
                {"text": "Belgium", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Kuba", "is_correct": True},
                {"text": "Spanien", "is_correct": False},
                {"text": "Portugal", "is_correct": False},
                {"text": "Belgien", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Cuba", "is_correct": True},
                {"text": "L'Espagne", "is_correct": False},
                {"text": "Le Portugal", "is_correct": False},
                {"text": "La Belgique", "is_correct": False},
            ],
            "media": {
                "kind": "image",
                "upload": "couple-henri-maria-teresa.jpg",
                "description": (
                    "Photograph of Grand Duke Henri and Grand Duchess Maria Teresa of "
                    "Luxembourg at an official occasion."
                ),
                "credit": (
                    "Wikimedia Commons — search 'Henri Maria Teresa Luxembourg'; "
                    "confirm the licence on the file you upload."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "This couple shared which prize in 1903?",
            "text_de": "Welchen Preis teilte sich dieses Paar im Jahr 1903?",
            "text_fr": "Quel prix ce couple a-t-il partagé en 1903 ?",
            "correct_answer_en": "The Nobel Prize in Physics",
            "correct_answer_de": "Den Nobelpreis für Physik",
            "correct_answer_fr": "Le prix Nobel de physique",
            "choices_en": [
                {"text": "The Nobel Prize in Physics", "is_correct": True},
                {"text": "The Nobel Prize in Chemistry", "is_correct": False},
                {"text": "The Nobel Peace Prize", "is_correct": False},
                {"text": "The Fields Medal", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Den Nobelpreis für Physik", "is_correct": True},
                {"text": "Den Nobelpreis für Chemie", "is_correct": False},
                {"text": "Den Friedensnobelpreis", "is_correct": False},
                {"text": "Die Fields-Medaille", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Le prix Nobel de physique", "is_correct": True},
                {"text": "Le prix Nobel de chimie", "is_correct": False},
                {"text": "Le prix Nobel de la paix", "is_correct": False},
                {"text": "La médaille Fields", "is_correct": False},
            ],
            "media": {
                "kind": "image",
                "upload": "couple-curie.jpg",
                "description": (
                    "Photograph of the scientists Marie and Pierre Curie in their "
                    "laboratory."
                ),
                "credit": (
                    "Wikimedia Commons — search 'Pierre and Marie Curie'; most images "
                    "of this era are public domain, but confirm per file."
                ),
            },
        },
        {
            "type": "multiple_choice",
            "text_en": "Before her marriage, this woman was famous as a member of which group?",
            "text_de": "Vor ihrer Ehe war diese Frau als Mitglied welcher Band berühmt?",
            "text_fr": "Avant son mariage, cette femme était connue comme membre de quel groupe ?",
            "correct_answer_en": "The Spice Girls",
            "correct_answer_de": "Die Spice Girls",
            "correct_answer_fr": "Les Spice Girls",
            "choices_en": [
                {"text": "The Spice Girls", "is_correct": True},
                {"text": "All Saints", "is_correct": False},
                {"text": "Sugababes", "is_correct": False},
                {"text": "Girls Aloud", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Die Spice Girls", "is_correct": True},
                {"text": "All Saints", "is_correct": False},
                {"text": "Sugababes", "is_correct": False},
                {"text": "Girls Aloud", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Les Spice Girls", "is_correct": True},
                {"text": "All Saints", "is_correct": False},
                {"text": "Sugababes", "is_correct": False},
                {"text": "Girls Aloud", "is_correct": False},
            ],
            "media": {
                "kind": "image",
                "upload": "couple-beckham.jpg",
                "description": (
                    "Photograph of David and Victoria Beckham together at a public "
                    "event."
                ),
                "credit": (
                    "Wikimedia Commons — search 'David Victoria Beckham'; confirm the "
                    "licence on the file you upload."
                ),
            },
        },
    ],
}

# --- Round 4: famous lines (text only) ------------------------------------
_ROUND_LOVE_LINES = {
    "title_en": "Legendary Love Lines",
    "title_de": "Legendäre Liebeszitate",
    "title_fr": "Répliques d'amour cultes",
    "questions": [
        {
            "type": "multiple_choice",
            "text_en": '"You had me at hello." Which film?',
            "text_de": '„You had me at hello." Aus welchem Film?',
            "text_fr": "« You had me at hello. » De quel film ?",
            "correct_answer_en": "Jerry Maguire",
            "correct_answer_de": "Jerry Maguire",
            "correct_answer_fr": "Jerry Maguire",
            "choices_en": [
                {"text": "Jerry Maguire", "is_correct": True},
                {"text": "When Harry Met Sally", "is_correct": False},
                {"text": "Sleepless in Seattle", "is_correct": False},
                {"text": "Pretty Woman", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Jerry Maguire", "is_correct": True},
                {"text": "Harry und Sally", "is_correct": False},
                {"text": "Schlaflos in Seattle", "is_correct": False},
                {"text": "Pretty Woman", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Jerry Maguire", "is_correct": True},
                {"text": "Quand Harry rencontre Sally", "is_correct": False},
                {"text": "Nuits blanches à Seattle", "is_correct": False},
                {"text": "Pretty Woman", "is_correct": False},
            ],
        },
        {
            "type": "multiple_choice",
            "text_en": '"Here\'s looking at you, kid." Which film?',
            "text_de": "„Here's looking at you, kid.\" Aus welchem Film?",
            "text_fr": "« Here's looking at you, kid. » De quel film ?",
            "correct_answer_en": "Casablanca",
            "correct_answer_de": "Casablanca",
            "correct_answer_fr": "Casablanca",
            "choices_en": [
                {"text": "Casablanca", "is_correct": True},
                {"text": "Gone with the Wind", "is_correct": False},
                {"text": "Roman Holiday", "is_correct": False},
                {"text": "An Affair to Remember", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Casablanca", "is_correct": True},
                {"text": "Vom Winde verweht", "is_correct": False},
                {"text": "Ein Herz und eine Krone", "is_correct": False},
                {"text": "Die große Liebe meines Lebens", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Casablanca", "is_correct": True},
                {"text": "Autant en emporte le vent", "is_correct": False},
                {"text": "Vacances romaines", "is_correct": False},
                {"text": "Elle et lui", "is_correct": False},
            ],
        },
        {
            "type": "true_false",
            "text_en": '"Love means never having to say you\'re sorry" comes from the film Love Story. True or false?',
            "text_de": "„Love means never having to say you're sorry\" stammt aus dem Film Love Story. Wahr oder falsch?",
            "text_fr": "« Love means never having to say you're sorry » vient du film Love Story. Vrai ou faux ?",
            "correct_answer_en": "True",
            "correct_answer_de": "Wahr",
            "correct_answer_fr": "Vrai",
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
            "text_en": 'In Love Actually, Mark declares himself to Juliet on cue cards: "To me, you are …" — what is the missing word?',
            "text_de": 'In Tatsächlich… Liebe gesteht Mark seine Liebe auf Karten: „To me, you are …" — welches Wort fehlt?',
            "text_fr": "Dans Love Actually, Mark se déclare à Juliet sur des pancartes : « To me, you are… » — quel mot manque-t-il ?",
            "correct_answer_en": "perfect",
            "correct_answer_de": "perfect (perfekt)",
            "correct_answer_fr": "perfect (parfaite)",
            "choices_en": [],
            "choices_de": [],
            "choices_fr": [],
        },
        {
            "type": "multiple_choice",
            "text_en": '"I wish I knew how to quit you." Which film?',
            "text_de": '„I wish I knew how to quit you." Aus welchem Film?',
            "text_fr": "« I wish I knew how to quit you. » De quel film ?",
            "correct_answer_en": "Brokeback Mountain",
            "correct_answer_de": "Brokeback Mountain",
            "correct_answer_fr": "Le Secret de Brokeback Mountain",
            "choices_en": [
                {"text": "Brokeback Mountain", "is_correct": True},
                {"text": "Call Me by Your Name", "is_correct": False},
                {"text": "Carol", "is_correct": False},
                {"text": "Moonlight", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Brokeback Mountain", "is_correct": True},
                {"text": "Call Me by Your Name", "is_correct": False},
                {"text": "Carol", "is_correct": False},
                {"text": "Moonlight", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Le Secret de Brokeback Mountain", "is_correct": True},
                {"text": "Call Me by Your Name", "is_correct": False},
                {"text": "Carol", "is_correct": False},
                {"text": "Moonlight", "is_correct": False},
            ],
        },
        {
            "type": "multiple_choice",
            "text_en": "\"I'll have what she's having\" was filmed in which New York institution?",
            "text_de": "„I'll have what she's having\" wurde in welcher New Yorker Institution gedreht?",
            "text_fr": "« I'll have what she's having » a été tourné dans quelle institution new-yorkaise ?",
            "correct_answer_en": "Katz's Delicatessen",
            "correct_answer_de": "Katz's Delicatessen",
            "correct_answer_fr": "Katz's Delicatessen",
            "choices_en": [
                {"text": "Katz's Delicatessen", "is_correct": True},
                {"text": "Carnegie Deli", "is_correct": False},
                {"text": "Russ & Daughters", "is_correct": False},
                {"text": "Junior's", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Katz's Delicatessen", "is_correct": True},
                {"text": "Carnegie Deli", "is_correct": False},
                {"text": "Russ & Daughters", "is_correct": False},
                {"text": "Junior's", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Katz's Delicatessen", "is_correct": True},
                {"text": "Carnegie Deli", "is_correct": False},
                {"text": "Russ & Daughters", "is_correct": False},
                {"text": "Junior's", "is_correct": False},
            ],
        },
    ],
}

# --- Round 5: the science of attraction (text only) -----------------------
_ROUND_SCIENCE = {
    "title_en": "The Science of Attraction",
    "title_de": "Die Wissenschaft der Anziehung",
    "title_fr": "La science de l'attirance",
    "questions": [
        {
            "type": "open_ended",
            "text_en": 'Which hormone is nicknamed the "cuddle hormone" for its role in bonding?',
            "text_de": 'Welches Hormon wird wegen seiner Rolle bei der Bindung „Kuschelhormon" genannt?',
            "text_fr": "Quelle hormone est surnommée « hormone du câlin » pour son rôle dans l'attachement ?",
            "correct_answer_en": "Oxytocin",
            "correct_answer_de": "Oxytocin",
            "correct_answer_fr": "L'ocytocine",
            "choices_en": [],
            "choices_de": [],
            "choices_fr": [],
        },
        {
            "type": "multiple_choice",
            "text_en": "Psychologist Arthur Aron's famous experiment used how many questions to make two strangers feel close?",
            "text_de": "Wie viele Fragen nutzte das berühmte Experiment des Psychologen Arthur Aron, damit sich zwei Fremde nahe fühlen?",
            "text_fr": "Combien de questions l'expérience célèbre du psychologue Arthur Aron utilisait-elle pour rapprocher deux inconnus ?",
            "correct_answer_en": "36",
            "correct_answer_de": "36",
            "correct_answer_fr": "36",
            "choices_en": [
                {"text": "36", "is_correct": True},
                {"text": "12", "is_correct": False},
                {"text": "50", "is_correct": False},
                {"text": "100", "is_correct": False},
            ],
            "choices_de": [
                {"text": "36", "is_correct": True},
                {"text": "12", "is_correct": False},
                {"text": "50", "is_correct": False},
                {"text": "100", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "36", "is_correct": True},
                {"text": "12", "is_correct": False},
                {"text": "50", "is_correct": False},
                {"text": "100", "is_correct": False},
            ],
        },
        {
            "type": "open_ended",
            "text_en": "What is the scientific study of kissing called?",
            "text_de": "Wie heißt die wissenschaftliche Erforschung des Küssens?",
            "text_fr": "Comment s'appelle l'étude scientifique du baiser ?",
            "correct_answer_en": "Philematology",
            "correct_answer_de": "Philematologie",
            "correct_answer_fr": "La philématologie",
            "choices_en": [],
            "choices_de": [],
            "choices_fr": [],
        },
        {
            "type": "true_false",
            "text_en": "Anthropologist Helen Fisher describes three brain systems behind love: lust, attraction and attachment. True or false?",
            "text_de": "Die Anthropologin Helen Fisher beschreibt drei Hirnsysteme hinter der Liebe: Lust, Anziehung und Bindung. Wahr oder falsch?",
            "text_fr": "L'anthropologue Helen Fisher décrit trois systèmes cérébraux derrière l'amour : le désir, l'attirance et l'attachement. Vrai ou faux ?",
            "correct_answer_en": "True",
            "correct_answer_de": "Wahr",
            "correct_answer_fr": "Vrai",
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
            "type": "multiple_choice",
            "text_en": 'Which neurotransmitter surges in early-stage romantic love and drives its "high"?',
            "text_de": 'Welcher Neurotransmitter steigt in der frühen Phase romantischer Liebe stark an und erzeugt das „Hoch"?',
            "text_fr": "Quel neurotransmetteur augmente fortement au début d'une histoire d'amour et provoque cette « euphorie » ?",
            "correct_answer_en": "Dopamine",
            "correct_answer_de": "Dopamin",
            "correct_answer_fr": "La dopamine",
            "choices_en": [
                {"text": "Dopamine", "is_correct": True},
                {"text": "Insulin", "is_correct": False},
                {"text": "Melatonin", "is_correct": False},
                {"text": "Cortisol", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Dopamin", "is_correct": True},
                {"text": "Insulin", "is_correct": False},
                {"text": "Melatonin", "is_correct": False},
                {"text": "Cortisol", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "La dopamine", "is_correct": True},
                {"text": "L'insuline", "is_correct": False},
                {"text": "La mélatonine", "is_correct": False},
                {"text": "Le cortisol", "is_correct": False},
            ],
        },
        {
            "type": "multiple_choice",
            "text_en": "Which small rodent is the classic laboratory model for lifelong pair bonding?",
            "text_de": "Welches kleine Nagetier ist das klassische Labormodell für lebenslange Paarbindung?",
            "text_fr": "Quel petit rongeur est le modèle de laboratoire classique du lien de couple durable ?",
            "correct_answer_en": "The prairie vole",
            "correct_answer_de": "Die Präriewühlmaus",
            "correct_answer_fr": "Le campagnol des prairies",
            "choices_en": [
                {"text": "The prairie vole", "is_correct": True},
                {"text": "The hamster", "is_correct": False},
                {"text": "The guinea pig", "is_correct": False},
                {"text": "The chinchilla", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Die Präriewühlmaus", "is_correct": True},
                {"text": "Der Hamster", "is_correct": False},
                {"text": "Das Meerschweinchen", "is_correct": False},
                {"text": "Die Chinchilla", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "Le campagnol des prairies", "is_correct": True},
                {"text": "Le hamster", "is_correct": False},
                {"text": "Le cochon d'Inde", "is_correct": False},
                {"text": "Le chinchilla", "is_correct": False},
            ],
        },
    ],
}

# --- Round 6: bonus, local flavour ----------------------------------------
_ROUND_LUXEMBOURG = {
    "title_en": "Bonus: Love in Luxembourg",
    "title_de": "Bonus: Liebe in Luxemburg",
    "title_fr": "Bonus : l'amour au Luxembourg",
    "is_bonus": True,
    "questions": [
        {
            "type": "open_ended",
            "points": 20,
            "text_en": 'How do you say "I love you" in Luxembourgish?',
            "text_de": 'Wie sagt man „Ich liebe dich" auf Luxemburgisch?',
            "text_fr": "Comment dit-on « je t'aime » en luxembourgeois ?",
            "correct_answer_en": "Ech hunn dech gär",
            "correct_answer_de": "Ech hunn dech gär",
            "correct_answer_fr": "Ech hunn dech gär",
            "choices_en": [],
            "choices_de": [],
            "choices_fr": [],
        },
        {
            "type": "multiple_choice",
            "points": 20,
            "text_en": "In which year did Luxembourg's same-sex marriage law come into force?",
            "text_de": "In welchem Jahr trat in Luxemburg das Gesetz zur gleichgeschlechtlichen Ehe in Kraft?",
            "text_fr": "En quelle année la loi luxembourgeoise sur le mariage entre personnes de même sexe est-elle entrée en vigueur ?",
            "correct_answer_en": "2015",
            "correct_answer_de": "2015",
            "correct_answer_fr": "2015",
            "choices_en": [
                {"text": "2015", "is_correct": True},
                {"text": "2010", "is_correct": False},
                {"text": "2013", "is_correct": False},
                {"text": "2018", "is_correct": False},
            ],
            "choices_de": [
                {"text": "2015", "is_correct": True},
                {"text": "2010", "is_correct": False},
                {"text": "2013", "is_correct": False},
                {"text": "2018", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "2015", "is_correct": True},
                {"text": "2010", "is_correct": False},
                {"text": "2013", "is_correct": False},
                {"text": "2018", "is_correct": False},
            ],
        },
        {
            "type": "true_false",
            "points": 20,
            "text_en": "Xavier Bettel was the first EU head of government to marry a same-sex partner. True or false?",
            "text_de": "Xavier Bettel war der erste EU-Regierungschef, der einen gleichgeschlechtlichen Partner heiratete. Wahr oder falsch?",
            "text_fr": "Xavier Bettel a été le premier chef de gouvernement de l'UE à épouser un partenaire de même sexe. Vrai ou faux ?",
            "correct_answer_en": "True — he married Gauthier Destenay in May 2015",
            "correct_answer_de": "Wahr — er heiratete Gauthier Destenay im Mai 2015",
            "correct_answer_fr": "Vrai — il a épousé Gauthier Destenay en mai 2015",
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
            "points": 20,
            "text_en": "In Luxembourg, a religious wedding ceremony is legally valid on its own, without a civil ceremony. True or false?",
            "text_de": "In Luxemburg ist eine kirchliche Trauung allein rechtsgültig, ohne standesamtliche Trauung. Wahr oder falsch?",
            "text_fr": "Au Luxembourg, une cérémonie religieuse est valable à elle seule, sans mariage civil. Vrai ou faux ?",
            "correct_answer_en": "False — the civil ceremony must come first",
            "correct_answer_de": "Falsch — die standesamtliche Trauung muss zuerst stattfinden",
            "correct_answer_fr": "Faux — le mariage civil doit avoir lieu en premier",
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
            "type": "multiple_choice",
            "points": 20,
            "text_en": "What is the minimum legal age to marry in Luxembourg?",
            "text_de": "Wie hoch ist das gesetzliche Mindestalter für eine Eheschließung in Luxemburg?",
            "text_fr": "Quel est l'âge légal minimum pour se marier au Luxembourg ?",
            "correct_answer_en": "18",
            "correct_answer_de": "18",
            "correct_answer_fr": "18 ans",
            "choices_en": [
                {"text": "18", "is_correct": True},
                {"text": "16", "is_correct": False},
                {"text": "17", "is_correct": False},
                {"text": "21", "is_correct": False},
            ],
            "choices_de": [
                {"text": "18", "is_correct": True},
                {"text": "16", "is_correct": False},
                {"text": "17", "is_correct": False},
                {"text": "21", "is_correct": False},
            ],
            "choices_fr": [
                {"text": "18 ans", "is_correct": True},
                {"text": "16 ans", "is_correct": False},
                {"text": "17 ans", "is_correct": False},
                {"text": "21 ans", "is_correct": False},
            ],
        },
        {
            "type": "multiple_choice",
            "points": 20,
            "text_en": "Which three languages are Luxembourg's official languages?",
            "text_de": "Welche drei Sprachen sind die Amtssprachen Luxemburgs?",
            "text_fr": "Quelles sont les trois langues officielles du Luxembourg ?",
            "correct_answer_en": "Luxembourgish, French and German",
            "correct_answer_de": "Luxemburgisch, Französisch und Deutsch",
            "correct_answer_fr": "Le luxembourgeois, le français et l'allemand",
            "choices_en": [
                {"text": "Luxembourgish, French and German", "is_correct": True},
                {"text": "Luxembourgish, French and English", "is_correct": False},
                {"text": "French, German and Dutch", "is_correct": False},
                {"text": "Luxembourgish, German and Portuguese", "is_correct": False},
            ],
            "choices_de": [
                {"text": "Luxemburgisch, Französisch und Deutsch", "is_correct": True},
                {
                    "text": "Luxemburgisch, Französisch und Englisch",
                    "is_correct": False,
                },
                {
                    "text": "Französisch, Deutsch und Niederländisch",
                    "is_correct": False,
                },
                {
                    "text": "Luxemburgisch, Deutsch und Portugiesisch",
                    "is_correct": False,
                },
            ],
            "choices_fr": [
                {
                    "text": "Le luxembourgeois, le français et l'allemand",
                    "is_correct": True,
                },
                {
                    "text": "Le luxembourgeois, le français et l'anglais",
                    "is_correct": False,
                },
                {
                    "text": "Le français, l'allemand et le néerlandais",
                    "is_correct": False,
                },
                {
                    "text": "Le luxembourgeois, l'allemand et le portugais",
                    "is_correct": False,
                },
            ],
        },
    ],
}

QUIZ_ROUNDS = [
    _ROUND_ON_SCREEN,
    _ROUND_LOVE_SONGS,
    _ROUND_FAMOUS_COUPLES,
    _ROUND_LOVE_LINES,
    _ROUND_SCIENCE,
    _ROUND_LUXEMBOURG,
]
