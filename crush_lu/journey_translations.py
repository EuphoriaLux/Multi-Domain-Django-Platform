"""
Wonderland Journey content translations for all supported languages.

This file contains all translatable content for the "Wonderland of You" journey
in English (en), German (de), and French (fr).

Dynamic placeholders:
- {first_name}: User's first name
- {date_met}: Formatted date of first meeting
- {location_met}: Location of first meeting
- {month}: Month name
- {day}: Day number
- {season}: Season word (blooming, shining, falling, sparkling)
"""

# =============================================================================
# ENGLISH (en) - Default/Primary Language
# =============================================================================
CONTENT_EN = {
    # Journey Configuration
    'journey_name': 'The Wonderland of You',
    'welcome_title': 'Welcome to Your Wonderland, {first_name}',
    'welcome_message': 'Something magical awaits you...',
    'final_message': (
        "You've completed every challenge and discovered every secret. "
        "But there's one thing I haven't said clearly enough: "
        "You're extraordinary, and I'd be honored if you'd let me prove it to you, "
        "one real moment at a time."
    ),

    # Season words for hints
    'seasons': {
        'spring': 'blooming',
        'summer': 'shining',
        'autumn': 'falling',
        'winter': 'sparkling',
    },

    # Chapter 1: Down the Rabbit Hole
    'chapter_1': {
        'title': 'Down the Rabbit Hole',
        'theme': 'Mystery & Curiosity',
        'story_introduction': (
            "Once upon a time, in a world full of ordinary connections, something "
            "extraordinary happened. On {date_met}, a doorway "
            "opened to a wonderland I never knew existed. But this wonderland isn't about "
            "talking cats or mad hatters - it's about discovering someone who makes every "
            "moment feel like magic. Are you ready to follow the white rabbit and see where "
            "this journey leads? Your first challenge awaits..."
        ),
        'completion_message': (
            "You found the door! Just like that day when I first saw you, something clicked. "
            "I didn't know it then, but that smile of yours would become my favorite sight "
            "in the world. Let's continue deeper into our wonderland..."
        ),
        'challenges': [
            {
                'type': 'riddle',
                'question': (
                    "I am the moment when two paths crossed,\n"
                    "When a stranger's smile made the world feel lost.\n"
                    "I happened on a {month} day so bright,\n"
                    "What date marks this moment of pure light?"
                ),
                'hint_1': "Think back to the season when leaves were {season}...",
                'hint_2': "It was in the month of {month}...",
                'hint_3': "The day was the {day}th...",
                'success_message': (
                    "Yes! {date_met} - that date is etched in my memory. "
                    "The day everything changed."
                ),
            },
            {
                'type': 'word_scramble',
                'question': 'Unscramble these letters to reveal what captured my attention:',
                'scrambled': 'TFSIR PELIMS',
                'answer': 'FIRST SMILE',
                'success_message': 'Exactly! Your smile was the first thing that caught my eye.',
            },
        ],
        'reward': {
            'type': 'photo_reveal',
            'title': 'The Smile That Started It All',
            'message': 'This is the moment that changed everything...',
        },
    },

    # Chapter 2: Garden of Rare Flowers
    'chapter_2': {
        'title': 'The Garden of Rare Flowers',
        'theme': 'Appreciation & Uniqueness',
        'story_introduction': (
            "In every garden, there are common flowers - beautiful, but familiar. "
            "And then there are rare blooms that make you stop and stare, wondering how "
            "something so extraordinary exists. You, my dear, are the rarest flower in the garden. "
            "This chapter celebrates all the little things that make you... YOU."
        ),
        'completion_message': (
            "You see? Every petal in this garden represents something about you that I've noticed, "
            "remembered, and treasured. While others might see a flower, I see the entire ecosystem "
            "of beauty that is YOU. And I'm just getting started..."
        ),
        'challenges': [
            {
                'question': "What's your secret superpower that most people don't see?",
                'options': {
                    'A': 'Making people laugh even on their worst days',
                    'B': 'Finding beauty in the smallest moments',
                    'C': 'Staying calm when everything is chaos',
                    'D': 'Remembering tiny details about people',
                },
                'success_message': "Yes! I noticed this about you from the very beginning. It's one of the things that makes you extraordinary.",
            },
            {
                'question': "If your laugh was a sound in nature, what would it be?",
                'options': {
                    'A': 'Wind chimes on a gentle breeze',
                    'B': 'A babbling brook over smooth stones',
                    'C': 'Birds singing at sunrise',
                    'D': 'Ocean waves on a peaceful shore',
                },
                'success_message': "Your laugh brightens everything around you, just like birds at sunrise. I could listen to it for hours.",
            },
            {
                'question': "What do you value most in a connection with someone?",
                'options': {
                    'A': 'Honest conversations, even about hard things',
                    'B': 'Comfortable silence and just being together',
                    'C': 'Shared adventures and creating memories',
                    'D': 'Deep understanding without words',
                },
                'success_message': "I felt this from you from the start. That's when I knew you were different...",
            },
            {
                'question': "If you could give your younger self one piece of advice, it would be...",
                'options': {
                    'A': 'Trust your gut - it knows more than you think',
                    'B': 'The hard times are making you stronger',
                    'C': "Don't change for anyone - you're perfect as you are",
                    'D': "Everything happens exactly when it's supposed to",
                },
                'success_message': "Knowing you, I think you'd say this. And I'm grateful for every step that led you to this moment...",
            },
        ],
        'reward': {
            'type': 'poem',
            'title': 'A Garden of One',
            'message': (
                "In a garden of billions of souls,\n"
                "Yours blooms in colors I've never seen.\n"
                "Not because you try to stand out,\n"
                "But because authenticity is your sunlight,\n"
                "Kindness is your rain,\n"
                "And genuine beauty grows from within.\n\n"
                "I could spend lifetimes studying your petals,\n"
                "And still discover new shades of wonderful."
            ),
        },
    },

    # Chapter 3: Gallery of Moments
    'chapter_3': {
        'title': 'The Gallery of Moments',
        'theme': 'Shared Memories',
        'story_introduction': (
            "Time is funny - it's made of millions of moments, but only a few make us truly feel alive. "
            "This gallery holds the moments with you that painted color into my black-and-white world. "
            "Each frame is a memory I've locked away in my heart. Can you help me remember them too?"
        ),
        'completion_message': (
            "Every photo in this gallery, every memory we've built - they're not just moments in time. "
            "They're proof that some connections are worth remembering, worth treasuring, worth everything. "
            "And we're still creating new moments for future galleries..."
        ),
        'timeline_events': [
            "The day we first met at {location_met}",
            "That conversation that lasted until midnight",
            "When you shared something personal with me",
            "The moment I realized you were special",
            "Today - as you journey through this wonderland",
        ],
        'timeline_question': 'Arrange these moments in the order they happened:',
        'timeline_success': "Perfect! You remember our timeline just as I do. Every moment matters.",
        'moment_question': (
            "There was one moment when 'interesting person' became 'someone I can't stop thinking about.' "
            "What was I thinking in that moment?"
        ),
        'moment_options': {
            'A': 'I want to know everything about this person',
            'B': "I hope this isn't the last time we talk",
            'C': "I've never met anyone like this before",
            'D': 'I need to see that smile again',
        },
        'moment_answer': 'C',
        'moment_success': (
            "Yes. In that exact moment, something shifted. You became less of a 'what if' "
            "and more of a 'I hope so'..."
        ),
        'reward': {
            'type': 'photo_slideshow',
            'title': 'Moments in Time',
            'message': 'A collection of moments that make time stop...',
        },
    },

    # Chapter 4: Carnival of Courage
    'chapter_4': {
        'title': 'The Carnival of Courage',
        'theme': 'Vulnerability & Truth',
        'story_introduction': (
            "The bravest thing anyone can do is be honest - with themselves and with others. "
            "In this carnival of life, we wear many masks. But with you, I want to take mine off. "
            "This chapter is about truth, vulnerability, and the courage to say what matters. "
            "Will you step up to the challenge?"
        ),
        'completion_message': (
            "You made it through the Carnival of Courage. Here's my truth: "
            "I'm taking my mask off. The question is... what happens next?"
        ),
        'would_you_rather': [
            {
                'question': 'Would you rather know the truth even if it hurts, or live in comfortable uncertainty?',
                'options': {'A': 'Know the truth', 'B': 'Comfortable uncertainty'},
            },
            {
                'question': 'Would you rather regret trying, or regret never knowing?',
                'options': {'A': 'Regret trying', 'B': 'Regret never knowing'},
            },
            {
                'question': 'Would you rather have one real connection, or a hundred superficial ones?',
                'options': {'A': 'One real connection', 'B': 'Hundred superficial'},
            },
        ],
        'wyr_success': 'Your answer tells me so much about who you are.',
        'open_question': 'What do you think makes someone worth taking a risk for?',
        'open_success': 'Thank you for being honest. Your words mean everything.',
        'reward': {
            'type': 'voice_message',
            'title': 'A Message from the Heart',
            'message': "I need to tell you something I've been thinking about...",
        },
    },

    # Chapter 5: Starlit Observatory
    'chapter_5': {
        'title': 'The Starlit Observatory',
        'theme': 'Dreams & Future',
        'story_introduction': (
            "They say we're all made of stardust. If that's true, then some stars must be meant "
            "to orbit together. This observatory isn't just for looking at distant galaxies - "
            "it's for dreaming about futures, possibilities, and 'what if's. "
            "Let's gaze at the stars and wonder... what could we create together?"
        ),
        'completion_message': (
            "Looking at stars makes me think about infinity, about possibilities, about futures unwritten. "
            "And here's what I know: whatever the future holds, I want you in it. "
            "Not as a distant star I admire from afar, but as someone who's part of my constellation. "
            "Someone whose light makes my sky brighter. Someone worth wishing on."
        ),
        'dream_question': "If we could do anything together, I'd want to...",
        'dream_options': {
            'A': "Travel somewhere we've never been",
            'B': 'Learn something new together',
            'C': 'Create something beautiful',
            'D': 'Build quiet moments of peace',
        },
        'dream_success': "I love that idea. Let's make it happen.",
        'future_question': 'Complete this sentence: "In 5 years, I hope we\'re..."',
        'future_success': "That future sounds beautiful. Let's build it together.",
        'reward': {
            'type': 'future_letter',
            'title': 'A Letter to the Future',
            'message': (
                "Dear Future {first_name},\n\n"
                "If you're reading this, then we've completed this journey. "
                "I don't know what happens next - that's still being written. "
                "But I want Future You to know what Present Me is thinking right now...\n\n"
                "Whatever happens, I want you to know that creating this for you - "
                "every puzzle, every message, every detail - was worth it. Because YOU are worth it.\n\n"
                "With all the stardust in my heart"
            ),
        },
    },

    # Chapter 6: Door to Tomorrow
    'chapter_6': {
        'title': 'The Door to Tomorrow',
        'theme': 'The Reveal & Next Step',
        'story_introduction': (
            "Every journey needs an ending. But some endings are really beginnings in disguise. "
            "You've solved every puzzle, unlocked every secret, and made it to the final door. "
            "Behind this door is the truth about why I created this entire wonderland for you. "
            "Are you ready?"
        ),
        'completion_message': (
            "{first_name}, you've journeyed through riddles, puzzles, memories, and dreams. "
            "You've unlocked every chapter, discovered every secret. "
            "This isn't the end of our story. It's the beginning of the next chapter. "
            "And I'm hoping - really hoping - that you'll write it with me."
        ),
        'riddles': [
            {
                'question': "I am the reason your name echoes in my mind before sleep. What am I?",
                'answer': 'feelings',
                'alternatives': ['love', 'thoughts', 'emotions', 'affection'],
                'success': "Yes... feelings. That's where this all begins.",
            },
            {
                'question': "I am what I hope we can build together. What am I?",
                'answer': 'future',
                'alternatives': ['connection', 'us', 'relationship', 'life together'],
                'success': "Yes... our future. Something beautiful we create together.",
            },
            {
                'question': "I am what I'm asking you to give this. What am I?",
                'answer': 'chance',
                'alternatives': ['time', 'heart', 'opportunity', 'try'],
                'success': (
                    "Yes... a chance. Feelings for our future, if you'll give it a chance. "
                    "That's all I'm asking."
                ),
            },
        ],
    },
}

# =============================================================================
# GERMAN (de) - Full Translation
# =============================================================================
CONTENT_DE = {
    # Journey Configuration
    'journey_name': 'Das Wunderland von Dir',
    'welcome_title': 'Willkommen in Deinem Wunderland, {first_name}',
    'welcome_message': 'Etwas Magisches erwartet dich...',
    'final_message': (
        "Du hast jede Herausforderung gemeistert und jedes Geheimnis entdeckt. "
        "Aber eine Sache habe ich nicht deutlich genug gesagt: "
        "Du bist außergewöhnlich, und ich würde mich geehrt fühlen, wenn du mir erlaubst, "
        "dir das zu beweisen, einen echten Moment nach dem anderen."
    ),

    # Season words for hints
    'seasons': {
        'spring': 'erblühend',
        'summer': 'strahlend',
        'autumn': 'fallend',
        'winter': 'funkelnd',
    },

    # Chapter 1: Down the Rabbit Hole
    'chapter_1': {
        'title': 'Den Kaninchenbau hinunter',
        'theme': 'Geheimnis & Neugier',
        'story_introduction': (
            "Es war einmal, in einer Welt voller gewöhnlicher Begegnungen, etwas "
            "Außergewöhnliches geschah. Am {date_met} öffnete sich eine Tür "
            "zu einem Wunderland, von dem ich nicht wusste, dass es existiert. Aber dieses Wunderland "
            "handelt nicht von sprechenden Katzen oder verrückten Hutmachern - es geht darum, jemanden zu entdecken, "
            "der jeden Moment wie Magie erscheinen lässt. Bist du bereit, dem weißen Kaninchen zu folgen "
            "und zu sehen, wohin diese Reise führt? Deine erste Herausforderung wartet..."
        ),
        'completion_message': (
            "Du hast die Tür gefunden! Genau wie an dem Tag, als ich dich zum ersten Mal sah, hat etwas geklickt. "
            "Ich wusste es damals nicht, aber dieses Lächeln von dir würde zu meinem Lieblingsanblick "
            "auf der Welt werden. Lass uns tiefer in unser Wunderland vordringen..."
        ),
        'challenges': [
            {
                'type': 'riddle',
                'question': (
                    "Ich bin der Moment, als zwei Wege sich kreuzten,\n"
                    "Als das Lächeln eines Fremden die Welt verzauberte.\n"
                    "Ich geschah an einem hellen {month}-Tag,\n"
                    "Welches Datum markiert diesen Moment reinen Lichts?"
                ),
                'hint_1': "Denke zurück an die Jahreszeit, als die Blätter {season} waren...",
                'hint_2': "Es war im Monat {month}...",
                'hint_3': "Der Tag war der {day}te...",
                'success_message': (
                    "Ja! {date_met} - dieses Datum ist in mein Gedächtnis eingebrannt. "
                    "Der Tag, an dem sich alles veränderte."
                ),
            },
            {
                'type': 'word_scramble',
                'question': 'Entwirre diese Buchstaben, um zu enthüllen, was meine Aufmerksamkeit gefangen hat:',
                'scrambled': 'SERETS LHCEÄNL',
                'answer': 'ERSTES LÄCHELN',
                'success_message': 'Genau! Dein Lächeln war das Erste, was mir aufgefallen ist.',
            },
        ],
        'reward': {
            'type': 'photo_reveal',
            'title': 'Das Lächeln, das alles begann',
            'message': 'Das ist der Moment, der alles verändert hat...',
        },
    },

    # Chapter 2: Garden of Rare Flowers
    'chapter_2': {
        'title': 'Der Garten der seltenen Blumen',
        'theme': 'Wertschätzung & Einzigartigkeit',
        'story_introduction': (
            "In jedem Garten gibt es gewöhnliche Blumen - schön, aber vertraut. "
            "Und dann gibt es seltene Blüten, die einen innehalten und staunen lassen, wie "
            "etwas so Außergewöhnliches existieren kann. Du, mein Lieber, bist die seltenste Blume im Garten. "
            "Dieses Kapitel feiert all die kleinen Dinge, die dich zu... DIR machen."
        ),
        'completion_message': (
            "Siehst du? Jedes Blütenblatt in diesem Garten repräsentiert etwas an dir, das ich bemerkt, "
            "mir gemerkt und geschätzt habe. Während andere vielleicht eine Blume sehen, sehe ich das gesamte Ökosystem "
            "der Schönheit, das DU bist. Und ich fange gerade erst an..."
        ),
        'challenges': [
            {
                'question': "Was ist deine geheime Superkraft, die die meisten Menschen nicht sehen?",
                'options': {
                    'A': 'Menschen auch an ihren schlimmsten Tagen zum Lachen bringen',
                    'B': 'Schönheit in den kleinsten Momenten finden',
                    'C': 'Ruhig bleiben, wenn alles im Chaos versinkt',
                    'D': 'Sich winzige Details über Menschen merken',
                },
                'success_message': "Ja! Das ist mir von Anfang an an dir aufgefallen. Es ist eine der Sachen, die dich außergewöhnlich machen.",
            },
            {
                'question': "Wenn dein Lachen ein Geräusch in der Natur wäre, was wäre es?",
                'options': {
                    'A': 'Windspiele bei einer sanften Brise',
                    'B': 'Ein plätschernder Bach über glatte Steine',
                    'C': 'Vögel, die bei Sonnenaufgang singen',
                    'D': 'Meereswellen an einem friedlichen Strand',
                },
                'success_message': "Dein Lachen erhellt alles um dich herum, genau wie Vögel bei Sonnenaufgang. Ich könnte stundenlang zuhören.",
            },
            {
                'question': "Was schätzt du am meisten an einer Verbindung mit jemandem?",
                'options': {
                    'A': 'Ehrliche Gespräche, auch über schwierige Dinge',
                    'B': 'Behagliche Stille und einfach zusammen sein',
                    'C': 'Gemeinsame Abenteuer und Erinnerungen schaffen',
                    'D': 'Tiefes Verstehen ohne Worte',
                },
                'success_message': "Das habe ich von Anfang an bei dir gespürt. Da wusste ich, dass du anders bist...",
            },
            {
                'question': "Wenn du deinem jüngeren Ich einen Rat geben könntest, wäre es...",
                'options': {
                    'A': 'Vertraue deinem Bauchgefühl - es weiß mehr als du denkst',
                    'B': 'Die schweren Zeiten machen dich stärker',
                    'C': 'Verändere dich für niemanden - du bist perfekt so wie du bist',
                    'D': 'Alles passiert genau dann, wenn es soll',
                },
                'success_message': "Wenn ich dich kenne, würdest du das wohl sagen. Und ich bin dankbar für jeden Schritt, der dich zu diesem Moment geführt hat...",
            },
        ],
        'reward': {
            'type': 'poem',
            'title': 'Ein Garten für Eine/n',
            'message': (
                "In einem Garten von Milliarden Seelen,\n"
                "Erblüht deine in Farben, die ich nie gesehen habe.\n"
                "Nicht weil du versuchst herauszustechen,\n"
                "Sondern weil Authentizität dein Sonnenlicht ist,\n"
                "Freundlichkeit dein Regen,\n"
                "Und wahre Schönheit von innen wächst.\n\n"
                "Ich könnte ein Leben damit verbringen, deine Blütenblätter zu studieren,\n"
                "Und immer noch neue Nuancen des Wunderbaren entdecken."
            ),
        },
    },

    # Chapter 3: Gallery of Moments
    'chapter_3': {
        'title': 'Die Galerie der Momente',
        'theme': 'Gemeinsame Erinnerungen',
        'story_introduction': (
            "Zeit ist seltsam - sie besteht aus Millionen von Momenten, aber nur wenige lassen uns wirklich lebendig fühlen. "
            "Diese Galerie bewahrt die Momente mit dir, die Farbe in meine schwarz-weiße Welt gemalt haben. "
            "Jeder Rahmen ist eine Erinnerung, die ich in meinem Herzen eingeschlossen habe. Kannst du mir helfen, sie auch zu erinnern?"
        ),
        'completion_message': (
            "Jedes Foto in dieser Galerie, jede Erinnerung, die wir aufgebaut haben - sie sind nicht nur Momente in der Zeit. "
            "Sie sind der Beweis, dass manche Verbindungen es wert sind, erinnert zu werden, geschätzt zu werden, alles wert sind. "
            "Und wir erschaffen immer noch neue Momente für zukünftige Galerien..."
        ),
        'timeline_events': [
            "Der Tag, an dem wir uns zum ersten Mal bei {location_met} trafen",
            "Das Gespräch, das bis Mitternacht dauerte",
            "Als du etwas Persönliches mit mir geteilt hast",
            "Der Moment, als ich merkte, dass du besonders bist",
            "Heute - während du durch dieses Wunderland reist",
        ],
        'timeline_question': 'Ordne diese Momente in der Reihenfolge, wie sie passiert sind:',
        'timeline_success': "Perfekt! Du erinnerst dich an unsere Zeitlinie genauso wie ich. Jeder Moment zählt.",
        'moment_question': (
            "Es gab einen Moment, als aus 'interessante Person' 'jemand, an den ich nicht aufhören kann zu denken' wurde. "
            "Was habe ich in diesem Moment gedacht?"
        ),
        'moment_options': {
            'A': 'Ich will alles über diese Person wissen',
            'B': 'Ich hoffe, das ist nicht das letzte Mal, dass wir reden',
            'C': 'Ich habe noch nie jemanden wie diese Person getroffen',
            'D': 'Ich muss dieses Lächeln wiedersehen',
        },
        'moment_answer': 'C',
        'moment_success': (
            "Ja. In genau diesem Moment veränderte sich etwas. Du wurdest weniger ein 'was wäre wenn' "
            "und mehr ein 'ich hoffe es'..."
        ),
        'reward': {
            'type': 'photo_slideshow',
            'title': 'Momente in der Zeit',
            'message': 'Eine Sammlung von Momenten, die die Zeit anhalten...',
        },
    },

    # Chapter 4: Carnival of Courage
    'chapter_4': {
        'title': 'Der Jahrmarkt des Muts',
        'theme': 'Verletzlichkeit & Wahrheit',
        'story_introduction': (
            "Das Mutigste, was jemand tun kann, ist ehrlich zu sein - mit sich selbst und mit anderen. "
            "Auf diesem Jahrmarkt des Lebens tragen wir viele Masken. Aber bei dir möchte ich meine abnehmen. "
            "Dieses Kapitel handelt von Wahrheit, Verletzlichkeit und dem Mut, das zu sagen, was zählt. "
            "Nimmst du die Herausforderung an?"
        ),
        'completion_message': (
            "Du hast es durch den Jahrmarkt des Muts geschafft. Hier ist meine Wahrheit: "
            "Ich nehme meine Maske ab. Die Frage ist... was passiert als Nächstes?"
        ),
        'would_you_rather': [
            {
                'question': 'Würdest du lieber die Wahrheit wissen, auch wenn sie wehtut, oder in bequemer Ungewissheit leben?',
                'options': {'A': 'Die Wahrheit wissen', 'B': 'Bequeme Ungewissheit'},
            },
            {
                'question': 'Würdest du es lieber bereuen, es versucht zu haben, oder bereuen, es nie gewusst zu haben?',
                'options': {'A': 'Es versucht zu haben bereuen', 'B': 'Es nie gewusst zu haben bereuen'},
            },
            {
                'question': 'Würdest du lieber eine echte Verbindung haben, oder hundert oberflächliche?',
                'options': {'A': 'Eine echte Verbindung', 'B': 'Hundert oberflächliche'},
            },
        ],
        'wyr_success': 'Deine Antwort sagt mir so viel darüber, wer du bist.',
        'open_question': 'Was macht deiner Meinung nach jemanden es wert, ein Risiko einzugehen?',
        'open_success': 'Danke für deine Ehrlichkeit. Deine Worte bedeuten mir alles.',
        'reward': {
            'type': 'voice_message',
            'title': 'Eine Nachricht von Herzen',
            'message': "Ich muss dir etwas sagen, worüber ich nachgedacht habe...",
        },
    },

    # Chapter 5: Starlit Observatory
    'chapter_5': {
        'title': 'Das Sternenobservatorium',
        'theme': 'Träume & Zukunft',
        'story_introduction': (
            "Man sagt, wir bestehen alle aus Sternenstaub. Wenn das stimmt, dann sind manche Sterne bestimmt dazu bestimmt, "
            "zusammen zu kreisen. Dieses Observatorium ist nicht nur dazu da, ferne Galaxien zu betrachten - "
            "es ist zum Träumen über Zukünfte, Möglichkeiten und 'was wäre wenns'. "
            "Lass uns die Sterne betrachten und uns fragen... was könnten wir zusammen erschaffen?"
        ),
        'completion_message': (
            "Sterne zu betrachten lässt mich an Unendlichkeit denken, an Möglichkeiten, an ungeschriebene Zukünfte. "
            "Und hier ist, was ich weiß: was auch immer die Zukunft bringt, ich möchte dich darin haben. "
            "Nicht als fernen Stern, den ich aus der Ferne bewundere, sondern als jemanden, der Teil meiner Konstellation ist. "
            "Jemand, dessen Licht meinen Himmel heller macht. Jemand, für den es sich lohnt zu wünschen."
        ),
        'dream_question': "Wenn wir zusammen alles tun könnten, würde ich...",
        'dream_options': {
            'A': 'An einen Ort reisen, an dem wir noch nie waren',
            'B': 'Zusammen etwas Neues lernen',
            'C': 'Etwas Schönes erschaffen',
            'D': 'Ruhige Momente des Friedens aufbauen',
        },
        'dream_success': "Ich liebe diese Idee. Lass sie uns wahr machen.",
        'future_question': 'Vervollständige diesen Satz: "In 5 Jahren hoffe ich, dass wir..."',
        'future_success': "Diese Zukunft klingt wunderschön. Lass sie uns zusammen aufbauen.",
        'reward': {
            'type': 'future_letter',
            'title': 'Ein Brief an die Zukunft',
            'message': (
                "Liebe/r zukünftige/r {first_name},\n\n"
                "Wenn du das liest, dann haben wir diese Reise abgeschlossen. "
                "Ich weiß nicht, was als Nächstes passiert - das wird noch geschrieben. "
                "Aber ich möchte, dass das zukünftige Du weiß, was das gegenwärtige Ich gerade denkt...\n\n"
                "Was auch immer passiert, ich möchte, dass du weißt, dass es das wert war, dies für dich zu erschaffen - "
                "jedes Rätsel, jede Nachricht, jedes Detail. Denn DU bist es wert.\n\n"
                "Mit all dem Sternenstaub in meinem Herzen"
            ),
        },
    },

    # Chapter 6: Door to Tomorrow
    'chapter_6': {
        'title': 'Die Tür zum Morgen',
        'theme': 'Die Enthüllung & der nächste Schritt',
        'story_introduction': (
            "Jede Reise braucht ein Ende. Aber manche Enden sind in Wirklichkeit Anfänge in Verkleidung. "
            "Du hast jedes Rätsel gelöst, jedes Geheimnis entschlüsselt und es bis zur letzten Tür geschafft. "
            "Hinter dieser Tür liegt die Wahrheit, warum ich dieses ganze Wunderland für dich erschaffen habe. "
            "Bist du bereit?"
        ),
        'completion_message': (
            "{first_name}, du bist durch Rätsel, Puzzles, Erinnerungen und Träume gereist. "
            "Du hast jedes Kapitel freigeschaltet, jedes Geheimnis entdeckt. "
            "Das ist nicht das Ende unserer Geschichte. Es ist der Anfang des nächsten Kapitels. "
            "Und ich hoffe - wirklich hoffe - dass du es mit mir schreiben wirst."
        ),
        'riddles': [
            {
                'question': "Ich bin der Grund, warum dein Name vor dem Einschlafen in meinem Kopf hallt. Was bin ich?",
                'answer': 'Gefühle',
                'alternatives': ['Liebe', 'Gedanken', 'Emotionen', 'Zuneigung'],
                'success': "Ja... Gefühle. Da beginnt das alles.",
            },
            {
                'question': "Ich bin das, was ich hoffe, dass wir zusammen aufbauen können. Was bin ich?",
                'answer': 'Zukunft',
                'alternatives': ['Verbindung', 'uns', 'Beziehung', 'gemeinsames Leben'],
                'success': "Ja... unsere Zukunft. Etwas Schönes, das wir zusammen erschaffen.",
            },
            {
                'question': "Ich bin das, worum ich dich bitte, es diesem zu geben. Was bin ich?",
                'answer': 'Chance',
                'alternatives': ['Zeit', 'Herz', 'Gelegenheit', 'Versuch'],
                'success': (
                    "Ja... eine Chance. Gefühle für unsere Zukunft, wenn du ihr eine Chance gibst. "
                    "Das ist alles, worum ich bitte."
                ),
            },
        ],
    },
}

# =============================================================================
# FRENCH (fr) - Full Translation
# =============================================================================
CONTENT_FR = {
    # Journey Configuration
    'journey_name': 'Le Pays des Merveilles de Toi',
    'welcome_title': 'Bienvenue dans Ton Pays des Merveilles, {first_name}',
    'welcome_message': "Quelque chose de magique t'attend...",
    'final_message': (
        "Tu as relevé tous les défis et découvert tous les secrets. "
        "Mais il y a une chose que je n'ai pas dit assez clairement : "
        "Tu es extraordinaire, et je serais honoré(e) si tu me laissais te le prouver, "
        "un vrai moment à la fois."
    ),

    # Season words for hints
    'seasons': {
        'spring': 'en fleurs',
        'summer': 'brillant',
        'autumn': 'tombant',
        'winter': 'étincelant',
    },

    # Chapter 1: Down the Rabbit Hole
    'chapter_1': {
        'title': 'Au fond du terrier',
        'theme': 'Mystère & Curiosité',
        'story_introduction': (
            "Il était une fois, dans un monde plein de rencontres ordinaires, quelque chose "
            "d'extraordinaire s'est produit. Le {date_met}, une porte s'est ouverte "
            "vers un pays des merveilles dont je ne connaissais pas l'existence. Mais ce pays des merveilles "
            "ne parle pas de chats qui parlent ou de chapeliers fous - il s'agit de découvrir quelqu'un qui fait "
            "de chaque moment une magie. Es-tu prêt(e) à suivre le lapin blanc et voir où "
            "ce voyage nous mène ? Ton premier défi t'attend..."
        ),
        'completion_message': (
            "Tu as trouvé la porte ! Tout comme ce jour où je t'ai vu(e) pour la première fois, quelque chose a cliqué. "
            "Je ne le savais pas alors, mais ce sourire deviendrait ma vision préférée "
            "au monde. Continuons plus profondément dans notre pays des merveilles..."
        ),
        'challenges': [
            {
                'type': 'riddle',
                'question': (
                    "Je suis le moment où deux chemins se sont croisés,\n"
                    "Quand le sourire d'un(e) inconnu(e) a enchanté le monde.\n"
                    "Je me suis produit un jour de {month} si lumineux,\n"
                    "Quelle date marque ce moment de pure lumière ?"
                ),
                'hint_1': "Repense à la saison où les feuilles étaient {season}...",
                'hint_2': "C'était au mois de {month}...",
                'hint_3': "Le jour était le {day}e...",
                'success_message': (
                    "Oui ! {date_met} - cette date est gravée dans ma mémoire. "
                    "Le jour où tout a changé."
                ),
            },
            {
                'type': 'word_scramble',
                'question': 'Démêle ces lettres pour révéler ce qui a capturé mon attention :',
                'scrambled': 'RPMEERI SRIROUE',
                'answer': 'PREMIER SOURIRE',
                'success_message': "Exactement ! Ton sourire est la première chose qui m'a frappé(e).",
            },
        ],
        'reward': {
            'type': 'photo_reveal',
            'title': 'Le Sourire qui a tout commencé',
            'message': "C'est le moment qui a tout changé...",
        },
    },

    # Chapter 2: Garden of Rare Flowers
    'chapter_2': {
        'title': 'Le Jardin des Fleurs Rares',
        'theme': 'Appréciation & Unicité',
        'story_introduction': (
            "Dans chaque jardin, il y a des fleurs communes - belles, mais familières. "
            "Et puis il y a ces fleurs rares qui te font t'arrêter et contempler, te demandant comment "
            "quelque chose d'aussi extraordinaire peut exister. Toi, mon/ma cher/chère, tu es la fleur la plus rare du jardin. "
            "Ce chapitre célèbre toutes les petites choses qui font de toi... TOI."
        ),
        'completion_message': (
            "Tu vois ? Chaque pétale dans ce jardin représente quelque chose de toi que j'ai remarqué, "
            "retenu et chéri. Tandis que d'autres voient peut-être une fleur, je vois tout l'écosystème "
            "de beauté que TU es. Et je ne fais que commencer..."
        ),
        'challenges': [
            {
                'question': "Quel est ton super-pouvoir secret que la plupart des gens ne voient pas ?",
                'options': {
                    'A': 'Faire rire les gens même dans leurs pires jours',
                    'B': 'Trouver la beauté dans les plus petits moments',
                    'C': 'Rester calme quand tout est chaos',
                    'D': 'Se souvenir des petits détails sur les gens',
                },
                'success_message': "Oui ! J'ai remarqué ça chez toi dès le début. C'est une des choses qui te rend extraordinaire.",
            },
            {
                'question': "Si ton rire était un son de la nature, ce serait quoi ?",
                'options': {
                    'A': 'Des carillons éoliens dans une brise légère',
                    'B': 'Un ruisseau qui babille sur des pierres lisses',
                    'C': 'Des oiseaux qui chantent au lever du soleil',
                    'D': 'Des vagues de mer sur un rivage paisible',
                },
                'success_message': "Ton rire illumine tout autour de toi, comme les oiseaux au lever du soleil. Je pourrais l'écouter pendant des heures.",
            },
            {
                'question': "Qu'est-ce que tu apprécies le plus dans une connexion avec quelqu'un ?",
                'options': {
                    'A': 'Des conversations honnêtes, même sur des sujets difficiles',
                    'B': 'Un silence confortable et simplement être ensemble',
                    'C': 'Des aventures partagées et créer des souvenirs',
                    'D': 'Une compréhension profonde sans mots',
                },
                'success_message': "Je l'ai ressenti chez toi dès le début. C'est là que j'ai su que tu étais différent(e)...",
            },
            {
                'question': "Si tu pouvais donner un conseil à ton toi plus jeune, ce serait...",
                'options': {
                    'A': "Fais confiance à ton instinct - il en sait plus que tu ne penses",
                    'B': 'Les moments difficiles te rendent plus fort(e)',
                    'C': "Ne change pour personne - tu es parfait(e) comme tu es",
                    'D': "Tout arrive exactement quand c'est censé arriver",
                },
                'success_message': "Te connaissant, je pense que tu dirais ça. Et je suis reconnaissant(e) pour chaque pas qui t'a mené(e) à ce moment...",
            },
        ],
        'reward': {
            'type': 'poem',
            'title': 'Un Jardin pour Un(e)',
            'message': (
                "Dans un jardin de milliards d'âmes,\n"
                "La tienne fleurit en couleurs que je n'ai jamais vues.\n"
                "Non pas parce que tu essaies de te démarquer,\n"
                "Mais parce que l'authenticité est ton soleil,\n"
                "La gentillesse est ta pluie,\n"
                "Et la vraie beauté pousse de l'intérieur.\n\n"
                "Je pourrais passer des vies à étudier tes pétales,\n"
                "Et toujours découvrir de nouvelles nuances de merveilleux."
            ),
        },
    },

    # Chapter 3: Gallery of Moments
    'chapter_3': {
        'title': 'La Galerie des Moments',
        'theme': 'Souvenirs Partagés',
        'story_introduction': (
            "Le temps est drôle - il est fait de millions de moments, mais seuls quelques-uns nous font vraiment sentir vivants. "
            "Cette galerie garde les moments avec toi qui ont peint des couleurs dans mon monde noir et blanc. "
            "Chaque cadre est un souvenir que j'ai enfermé dans mon cœur. Peux-tu m'aider à m'en souvenir aussi ?"
        ),
        'completion_message': (
            "Chaque photo dans cette galerie, chaque souvenir que nous avons construit - ce ne sont pas que des moments dans le temps. "
            "Ce sont la preuve que certaines connexions valent la peine d'être gardées, d'être chéries, de tout. "
            "Et nous créons encore de nouveaux moments pour de futures galeries..."
        ),
        'timeline_events': [
            "Le jour où nous nous sommes rencontrés pour la première fois à {location_met}",
            "Cette conversation qui a duré jusqu'à minuit",
            "Quand tu as partagé quelque chose de personnel avec moi",
            "Le moment où j'ai réalisé que tu étais spécial(e)",
            "Aujourd'hui - alors que tu voyages à travers ce pays des merveilles",
        ],
        'timeline_question': "Arrange ces moments dans l'ordre où ils se sont produits :",
        'timeline_success': "Parfait ! Tu te souviens de notre chronologie comme moi. Chaque moment compte.",
        'moment_question': (
            "Il y a eu un moment où 'personne intéressante' est devenu 'quelqu'un à qui je ne peux pas arrêter de penser'. "
            "À quoi pensais-je à ce moment ?"
        ),
        'moment_options': {
            'A': 'Je veux tout savoir sur cette personne',
            'B': "J'espère que ce n'est pas la dernière fois qu'on se parle",
            'C': "Je n'ai jamais rencontré quelqu'un comme cette personne",
            'D': 'Je dois revoir ce sourire',
        },
        'moment_answer': 'C',
        'moment_success': (
            "Oui. À ce moment précis, quelque chose a changé. Tu es devenu(e) moins un 'et si' "
            "et plus un 'j'espère que oui'..."
        ),
        'reward': {
            'type': 'photo_slideshow',
            'title': 'Moments dans le Temps',
            'message': 'Une collection de moments qui arrêtent le temps...',
        },
    },

    # Chapter 4: Carnival of Courage
    'chapter_4': {
        'title': 'Le Carnaval du Courage',
        'theme': 'Vulnérabilité & Vérité',
        'story_introduction': (
            "La chose la plus courageuse que quelqu'un puisse faire est d'être honnête - avec soi-même et avec les autres. "
            "Dans ce carnaval de la vie, nous portons beaucoup de masques. Mais avec toi, je veux enlever le mien. "
            "Ce chapitre parle de vérité, de vulnérabilité et du courage de dire ce qui compte. "
            "Relèveras-tu le défi ?"
        ),
        'completion_message': (
            "Tu as traversé le Carnaval du Courage. Voici ma vérité : "
            "J'enlève mon masque. La question est... que se passe-t-il ensuite ?"
        ),
        'would_you_rather': [
            {
                'question': 'Préférerais-tu connaître la vérité même si elle fait mal, ou vivre dans une incertitude confortable ?',
                'options': {'A': 'Connaître la vérité', 'B': 'Incertitude confortable'},
            },
            {
                'question': "Préférerais-tu regretter d'avoir essayé, ou regretter de n'avoir jamais su ?",
                'options': {'A': "Regretter d'avoir essayé", 'B': "Regretter de n'avoir jamais su"},
            },
            {
                'question': 'Préférerais-tu avoir une vraie connexion, ou une centaine de superficielles ?',
                'options': {'A': 'Une vraie connexion', 'B': 'Cent superficielles'},
            },
        ],
        'wyr_success': 'Ta réponse me dit tellement sur qui tu es.',
        'open_question': "Qu'est-ce qui fait que quelqu'un vaut la peine de prendre un risque, selon toi ?",
        'open_success': 'Merci pour ton honnêteté. Tes mots signifient tout pour moi.',
        'reward': {
            'type': 'voice_message',
            'title': 'Un Message du Cœur',
            'message': "Il faut que je te dise quelque chose à quoi j'ai pensé...",
        },
    },

    # Chapter 5: Starlit Observatory
    'chapter_5': {
        'title': "L'Observatoire Étoilé",
        'theme': 'Rêves & Avenir',
        'story_introduction': (
            "On dit que nous sommes tous faits de poussière d'étoiles. Si c'est vrai, alors certaines étoiles sont destinées "
            "à orbiter ensemble. Cet observatoire n'est pas seulement pour regarder des galaxies lointaines - "
            "c'est pour rêver d'avenirs, de possibilités et de 'et si'. "
            "Regardons les étoiles et demandons-nous... que pourrions-nous créer ensemble ?"
        ),
        'completion_message': (
            "Regarder les étoiles me fait penser à l'infini, aux possibilités, aux avenirs non écrits. "
            "Et voici ce que je sais : quoi que l'avenir réserve, je te veux dedans. "
            "Pas comme une étoile lointaine que j'admire de loin, mais comme quelqu'un qui fait partie de ma constellation. "
            "Quelqu'un dont la lumière rend mon ciel plus brillant. Quelqu'un qui vaut un vœu."
        ),
        'dream_question': "Si on pouvait faire n'importe quoi ensemble, je voudrais...",
        'dream_options': {
            'A': "Voyager quelque part où on n'a jamais été",
            'B': 'Apprendre quelque chose de nouveau ensemble',
            'C': 'Créer quelque chose de beau',
            'D': 'Construire des moments calmes de paix',
        },
        'dream_success': "J'adore cette idée. Réalisons-la.",
        'future_question': 'Complète cette phrase : "Dans 5 ans, j\'espère qu\'on..."',
        'future_success': "Cet avenir semble magnifique. Construisons-le ensemble.",
        'reward': {
            'type': 'future_letter',
            'title': "Une Lettre pour l'Avenir",
            'message': (
                "Cher/Chère {first_name} du futur,\n\n"
                "Si tu lis ceci, alors nous avons terminé ce voyage. "
                "Je ne sais pas ce qui va se passer ensuite - c'est encore en train d'être écrit. "
                "Mais je veux que le Toi du futur sache ce que le Moi du présent pense en ce moment...\n\n"
                "Quoi qu'il arrive, je veux que tu saches que créer tout ceci pour toi - "
                "chaque puzzle, chaque message, chaque détail - en valait la peine. Parce que TU en vaux la peine.\n\n"
                "Avec toute la poussière d'étoiles de mon cœur"
            ),
        },
    },

    # Chapter 6: Door to Tomorrow
    'chapter_6': {
        'title': 'La Porte de Demain',
        'theme': 'La Révélation & la Prochaine Étape',
        'story_introduction': (
            "Chaque voyage a besoin d'une fin. Mais certaines fins sont en réalité des débuts déguisés. "
            "Tu as résolu chaque énigme, déverrouillé chaque secret et tu es arrivé(e) à la dernière porte. "
            "Derrière cette porte se trouve la vérité sur pourquoi j'ai créé ce pays des merveilles entier pour toi. "
            "Es-tu prêt(e) ?"
        ),
        'completion_message': (
            "{first_name}, tu as voyagé à travers des énigmes, des puzzles, des souvenirs et des rêves. "
            "Tu as déverrouillé chaque chapitre, découvert chaque secret. "
            "Ce n'est pas la fin de notre histoire. C'est le début du prochain chapitre. "
            "Et j'espère - j'espère vraiment - que tu l'écriras avec moi."
        ),
        'riddles': [
            {
                'question': "Je suis la raison pour laquelle ton nom résonne dans mon esprit avant de dormir. Qu'est-ce que je suis ?",
                'answer': 'sentiments',
                'alternatives': ['amour', 'pensées', 'émotions', 'affection'],
                'success': "Oui... des sentiments. C'est là que tout commence.",
            },
            {
                'question': "Je suis ce que j'espère qu'on peut construire ensemble. Qu'est-ce que je suis ?",
                'answer': 'avenir',
                'alternatives': ['connexion', 'nous', 'relation', 'vie ensemble'],
                'success': "Oui... notre avenir. Quelque chose de beau que l'on crée ensemble.",
            },
            {
                'question': "Je suis ce que je te demande de donner à ceci. Qu'est-ce que je suis ?",
                'answer': 'chance',
                'alternatives': ['temps', 'cœur', 'opportunité', 'essai'],
                'success': (
                    "Oui... une chance. Des sentiments pour notre avenir, si tu lui donnes une chance. "
                    "C'est tout ce que je demande."
                ),
            },
        ],
    },
}


# =============================================================================
# COMBINED CONTENT DICTIONARY
# =============================================================================
# This is the main export - the management command will import this
JOURNEY_CONTENT = {
    'en': CONTENT_EN,
    'de': CONTENT_DE,
    'fr': CONTENT_FR,
}


def get_content(language: str = 'en') -> dict:
    """
    Get journey content for specified language.

    Args:
        language: Language code ('en', 'de', 'fr')

    Returns:
        Content dictionary for the language, falling back to English if not found
    """
    return JOURNEY_CONTENT.get(language, CONTENT_EN)


def get_text(language: str, key: str, fallback_to_en: bool = True, **kwargs) -> str:
    """
    Get a specific text string for a language with optional formatting.

    Args:
        language: Language code ('en', 'de', 'fr')
        key: Dot-separated key path (e.g., 'chapter_1.title')
        fallback_to_en: Fall back to English if key not found
        **kwargs: Format arguments for string interpolation

    Returns:
        The translated string with placeholders filled in
    """
    content = JOURNEY_CONTENT.get(language, CONTENT_EN)

    # Navigate nested keys
    keys = key.split('.')
    value = content

    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        elif fallback_to_en:
            # Fall back to English
            value = CONTENT_EN
            for k2 in keys:
                if isinstance(value, dict) and k2 in value:
                    value = value[k2]
                else:
                    return f"[MISSING: {key}]"
            break
        else:
            return f"[MISSING: {key}]"

    # Format the string if kwargs provided
    if isinstance(value, str) and kwargs:
        try:
            value = value.format(**kwargs)
        except KeyError:
            pass  # Ignore missing format keys

    return value
