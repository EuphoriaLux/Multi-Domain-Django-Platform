"""
Seed a ready-to-play **Crush Cache** scavenger hunt for manual QA of the
GPS + QR gameplay.

Everything that varies between hunts lives in the `PRESETS` registry, so
adding one is data rather than another branch:

    lux_city          Luxembourg City old town, English landmark trivia
    minette           Fond-de-Gras mining valley, German icebreakers + QR
    echternach_lake   Echternach lake, GPS-only solo field-test prototype
    echternach_um_see Echternach lake, German icebreakers + QR, team hunt

The debug presets create a `crush_cache` MeetupEvent + CacheHunt with landmark
stations, challenges and demo users. `echternach_lake` instead reuses one
explicitly named account, creates no credentials, stays unpublished, and is
permitted only locally or in the Azure staging slot — a preset sets
`solo_prototype` to opt into that shape permanently.

Passing --player-email puts ANY preset on that same solo path for one run, so
a hunt written for local QA can be field-tested on staging without seeding the
debug accounts and published event it would create locally. That matters
because the debug path creates a STAFF account on a shared password: never
run a preset on staging without --player-email.

By default the command refuses to run on Azure (WEBSITE_HOSTNAME set) or when
DEBUG is False. `--force` permits the Echternach prototype on staging, while
its separate production guard cannot be bypassed. The gameplay URLs also
require CRUSH_CACHE_ENABLED=true.

The two Echternach presets walk the same lake by design — `echternach_lake`
gathers feedback about the format on a GPS-only solo loop, `echternach_um_see`
plays the format as a team hunt with a QR Crush Statue at every stop. Both use
points from the official Visit Luxembourg GPX track, so one trip round the
lake can field-test both.

Usage:
    python manage.py seed_crush_cache --reset
    python manage.py seed_crush_cache --reset --live   # start the hunt now
    python manage.py seed_crush_cache --preset echternach_lake --reset --live \
        --player-email you@example.com --force
    python manage.py seed_crush_cache --preset echternach_um_see --reset --live

Testing GPS without being in Luxembourg: open the play page in Chrome, then
DevTools → ⋮ → More tools → Sensors → Location → "Other…" and paste a
station's lat/lng (printed below). The arrival check will pass.
"""

import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.crush_cache import (
    CacheChallenge,
    CacheHunt,
    CacheStation,
    CacheTeam,
)
from crush_lu.models.profiles import CrushCoach

STATIONS_LUX_CITY = [
    {
        "order": 1,
        "name": "Gëlle Fra (Monument of Remembrance)",
        "lat": "49.609720",
        "lng": "6.129170",
        "unlock_mode": "gps",
        "intro": "Head to the golden lady watching over Place de la Constitution.",
        "challenge_type": "riddle",
        "question": "What is the popular Luxembourgish name of this golden statue?",
        "answer": "Gëlle Fra",
        "alternatives": ["Gelle Fra", "Golden Lady", "Golden Fra"],
        "hint_1": "It means 'Golden Lady' in Luxembourgish.",
        "points": 100,
    },
    {
        "order": 2,
        "name": "Pont Adolphe",
        "lat": "49.607500",
        "lng": "6.127220",
        "unlock_mode": "gps",
        "intro": "Cross to the great arched bridge over the Pétrusse valley.",
        "challenge_type": "open_text",
        "question": "Which valley does the Pont Adolphe span?",
        "answer": "Petrusse",
        "alternatives": ["Pétrusse", "Petrusse valley", "Vallée de la Pétrusse"],
        "hint_1": "A small river gorge below the Ville Haute.",
        "points": 80,
    },
    {
        "order": 3,
        "name": "Place Guillaume II (Knuedler)",
        "lat": "49.610830",
        "lng": "6.130000",
        "unlock_mode": "gps",
        "intro": "Walk to the big square with the equestrian statue and the Town Hall.",
        "challenge_type": "riddle",
        "question": "By what nickname do locals call this square?",
        "answer": "Knuedler",
        "alternatives": ["Knudler"],
        "hint_1": "It refers to the knot in a monk's belt (Knued).",
        "points": 100,
    },
    {
        "order": 4,
        "name": "Grand Ducal Palace",
        "lat": "49.610600",
        "lng": "6.131900",
        "unlock_mode": "gps_qr",
        "intro": "Reach the palace, then scan the sticker by the guard's post.",
        "challenge_type": "open_text",
        "question": "Who officially resides at this palace (title, not name)?",
        "answer": "Grand Duke",
        "alternatives": ["The Grand Duke", "Grand-Duke", "Grand Duke of Luxembourg"],
        "hint_1": "The head of state of the Grand Duchy.",
        "points": 120,
    },
    {
        "order": 5,
        "name": "Casemates du Bock",
        "lat": "49.611500",
        "lng": "6.134800",
        "unlock_mode": "gps",
        "intro": "Finish at the cliffside fortifications carved into the Bock promontory.",
        "challenge_type": "open_text",
        "question": "These underground tunnels are famously called the Bock ______?",
        "answer": "Casemates",
        "alternatives": ["Casemates du Bock", "the Casemates"],
        "hint_1": "A French word for fortified galleries.",
        "points": 100,
    },
]

# Fond-de-Gras Geo Crush Cache "Minette" (6 stations: GPS + Crush Statue QR code at every station)
STATIONS_MINETTE = [
    {
        "order": 1,
        "name": "Gare de Fond-de-Gras (Train 1900)",
        "lat": "49.532894",
        "lng": "5.858759",
        "unlock_mode": "gps_qr",
        "intro": "Head to Gare de Fond-de-Gras! Find the Crush Statue near the historic Train 1900 platform and scan the QR code to unlock your first question.",
        "challenge_type": "multiple_choice",
        "question": "Was darf bei einem Date nicht fehlen?",
        "options": {
            "1": "tiefgründige Gespräche",
            "2": "Spaß und lockere Vibes",
            "3": "Flirt und Spannung",
        },
        "answer": "",  # Accepts any choice (icebreaker prompt)
        "alternatives": [],
        "hint_1": "Wähle spontan aus dem Bauch heraus!",
        "points": 100,
    },
    {
        "order": 2,
        "name": "Sentier des Minières (Mining Track Line)",
        "lat": "49.532948",
        "lng": "5.853189",
        "unlock_mode": "gps_qr",
        "intro": "Follow the narrow gauge railway tracks along the valley until you spot the Crush Statue by the trail!",
        "challenge_type": "multiple_choice",
        "question": "Wie tankst du Energie auf?",
        "options": {
            "1": "gerne auch alleine",
            "2": "mit einem guten Gespräch mit Freunden",
            "3": "in Aktion wie beim Sport",
        },
        "answer": "",  # Accepts any choice
        "alternatives": [],
        "hint_1": "Denke an deinen perfekten freien Nachmittag.",
        "points": 100,
    },
    {
        "order": 3,
        "name": "Galerie Mine Doenn",
        "lat": "49.533691",
        "lng": "5.850556",
        "unlock_mode": "gps_qr",
        "intro": "Approach the historic underground mine gallery entrance and find the Crush Statue near the entry sign.",
        "challenge_type": "multiple_choice",
        "question": "Wer bist du in einer Gruppe?",
        "options": {
            "1": "Ich rede mit allen und bringe Stimmung rein",
            "2": "Ich beobachte und wähle Gespräche",
            "3": "Kommt auf die Leute an",
        },
        "answer": "",  # Accepts any choice
        "alternatives": [],
        "hint_1": "Wie verhältst du dich typischerweise unter neuen Leuten?",
        "points": 100,
    },
    {
        "order": 4,
        "name": "Réserve Naturelle Giele Botter Trail",
        "lat": "49.534649",
        "lng": "5.847993",
        "unlock_mode": "gps_qr",
        "intro": "Walk up towards the former open-cast mine turned nature reserve and locate the Crush Statue along the path.",
        "challenge_type": "multiple_choice",
        "question": "Dein Leben ist...",
        "options": {
            "1": "gut organisiert und durchgeplant",
            "2": "von Spontaneität geprägt",
            "3": "ein geordnetes Chaos",
        },
        "answer": "",  # Accepts any choice
        "alternatives": [],
        "hint_1": "Wie schaut deine Wochenplanung meistens aus?",
        "points": 100,
    },
    {
        "order": 5,
        "name": "Viktoriastoll & Mine Heritage",
        "lat": "49.529388",
        "lng": "5.857374",
        "unlock_mode": "gps_qr",
        "intro": "Discover the entrance to the Viktoriastoll adit and scan the QR code on the Crush Statue.",
        "challenge_type": "multiple_choice",
        "question": "Ein Gespräch mit dir:",
        "options": {
            "1": "ich teile gerne von mir mit",
            "2": "ich liebe es zuzuhören",
            "3": "sowohl als auch",
        },
        "answer": "",  # Accepts any choice
        "alternatives": [],
        "hint_1": "Wie läuft eine gute Unterhaltung für dich ab?",
        "points": 100,
    },
    {
        "order": 6,
        "name": "Hall Paul Wurth & Épicerie Ancienne (Schluss)",
        "lat": "49.533819",
        "lng": "5.863352",
        "unlock_mode": "gps_qr",
        "intro": "Reach the final station (Schluss) near the historic Paul Wurth hall and grocery shop. Find the final Crush Statue and scan the QR code!",
        "challenge_type": "riddle",
        "question": "Glückwunsch! Welches historische Industriezeitalter hat dieses Tal geprägt?",
        "answer": "Industrielle Revolution",
        "alternatives": [
            "Industrial Revolution",
            "Bergbau",
            "Stahlindustrie",
            "Stahlzeitalter",
        ],
        "hint_1": "Das Zeitalter von Eisen, Stahl und Dampf im 19. und 20. Jahrhundert.",
        "points": 150,
    },
]

# GPS-only prototype following the official 3.25 km comfort trail clockwise
# from the climbing hall / trampoline park. Coordinates are points on the
# official Visit Luxembourg GPX track, not the centroids of nearby venues.
# The questions intentionally collect field-test feedback instead of scoring
# local trivia: the first real walk should validate the experience itself.
STATIONS_ECHTERNACH_LAKE = [
    {
        "order": 1,
        "name": "Climbing Hall & Trampoline Park Start",
        "lat": "49.801690",
        "lng": "6.415928",
        "unlock_mode": "gps",
        "intro": (
            "Start beside the climbing hall and trampoline park, then follow "
            "the paved lakeside path clockwise with the water on your right."
        ),
        "challenge_type": "multiple_choice",
        "question": "For a future Crush Cache, how would you prefer to navigate?",
        "options": {
            "1": "A map with the next pin",
            "2": "A compass and distance only",
            "3": "Clues without a visible destination",
        },
        "answer": "",
        "alternatives": [],
        "hint_1": "Choose the style that would feel most fun on a first date.",
        "points": 100,
    },
    {
        "order": 2,
        "name": "East Shore Path",
        "lat": "49.797563",
        "lng": "6.416679",
        "unlock_mode": "gps",
        "intro": (
            "Continue along the quiet eastern shore. Stop on the wide path "
            "where you can look back across the lake."
        ),
        "challenge_type": "multiple_choice",
        "question": "How did the first GPS arrival feel?",
        "options": {
            "1": "It unlocked too early",
            "2": "It unlocked at the right moment",
            "3": "It took too long to unlock",
        },
        "answer": "",
        "alternatives": [],
        "hint_1": "Think about where you were standing when the arrival appeared.",
        "points": 100,
    },
    {
        "order": 3,
        "name": "South Shore Bend",
        "lat": "49.794390",
        "lng": "6.415674",
        "unlock_mode": "gps",
        "intro": (
            "Follow the path around the southern end of the lake and pause "
            "after the broad bend. Stay on the paved route."
        ),
        "challenge_type": "multiple_choice",
        "question": "What kind of task would best help two new people connect here?",
        "options": {
            "1": "A playful photo together",
            "2": "A personal conversation question",
            "3": "A small cooperative challenge",
        },
        "answer": "",
        "alternatives": [],
        "hint_1": "Pick the one that would create the least awkwardness.",
        "points": 100,
    },
    {
        "order": 4,
        "name": "Small Islands Stretch",
        "lat": "49.797667",
        "lng": "6.410571",
        "unlock_mode": "gps",
        "intro": (
            "Continue north on the western side near the small islands. Use "
            "the accessible main path rather than an island detour."
        ),
        "challenge_type": "multiple_choice",
        "question": "How does the pacing feel after four checkpoints?",
        "options": {
            "1": "Too many stops",
            "2": "A good rhythm",
            "3": "I would add more stops",
        },
        "answer": "",
        "alternatives": [],
        "hint_1": "Consider both the walking time and time spent on your phone.",
        "points": 100,
    },
    {
        "order": 5,
        "name": "Roman Villa Lakeside Path",
        "lat": "49.804108",
        "lng": "6.411389",
        "unlock_mode": "gps",
        "intro": (
            "Walk past the refreshment area toward the Roman villa. This "
            "checkpoint is on the lakeside path below the archaeological site."
        ),
        "challenge_type": "multiple_choice",
        "question": "What should a finished team receive at the end?",
        "options": {
            "1": "A digital badge or certificate",
            "2": "A drink or partner reward",
            "3": "Only the shared memory and leaderboard",
        },
        "answer": "",
        "alternatives": [],
        "hint_1": "Choose what would make completion feel genuinely rewarding.",
        "points": 100,
    },
    {
        "order": 6,
        "name": "Lake Loop Finish",
        "lat": "49.801690",
        "lng": "6.415928",
        "unlock_mode": "gps",
        "intro": (
            "Complete the loop back at the climbing hall and trampoline park. "
            "You have walked the full prototype route."
        ),
        "challenge_type": "multiple_choice",
        "question": "After walking it, should we develop this into a real Crush experience?",
        "options": {
            "1": "Yes, the concept works",
            "2": "Yes, but the route or gameplay needs changes",
            "3": "No, it needs a different format",
        },
        "answer": "",
        "alternatives": [],
        "hint_1": "Judge the complete experience, not only one checkpoint.",
        "points": 150,
    },
]

# Echternach lake Crush Cache "Um See" — the same lake as the prototype above,
# played as a team hunt: a Crush Statue QR at every stop and German icebreaker
# prompts, the Minette shape rather than the prototype's GPS-only feedback run.
#
# Every station is a point the prototype already uses on the official GPX
# track, so one walk of the ~3.25 km lake loop can field-test both presets.
# The loop runs villa → climbing hall → east shore → south bend → west shore
# and finishes back at the villa. Do not re-derive these from a map — the
# prototype's track is the surveyed source.
STATIONS_ECHTERNACH_UM_SEE = [
    {
        "order": 1,
        "name": "Réimervilla (Start)",
        "lat": "49.804108",
        "lng": "6.411389",
        "unlock_mode": "gps_qr",
        "intro": (
            "Start am Nordwestufer, auf dem Uferweg unterhalb der Réimervilla. "
            "Beim Ausbaggern des Sees stießen die Arbeiter hier 1975 auf etwas "
            "sehr Altes. Findet die Crush-Statue und scannt den QR-Code."
        ),
        "challenge_type": "multiple_choice",
        "question": "Wenn du eine Zeitreise machen könntest — wohin?",
        "options": {
            "1": "Ins römische Echternach, genau hier",
            "2": "Hundert Jahre in die Zukunft",
            "3": "Zurück in meine eigene Kindheit",
        },
        "answer": "",  # Accepts any choice (icebreaker prompt)
        "alternatives": [],
        "hint_1": "Es gibt kein Richtig oder Falsch — nimm das, was dich zuerst anlacht.",
        "points": 100,
    },
    {
        "order": 2,
        "name": "Kloterhal & Jugendherberge",
        "lat": "49.801690",
        "lng": "6.415928",
        "unlock_mode": "gps_qr",
        # The station label is the navigation cue in the field, so the intro
        # repeats it verbatim and glosses it — "Kloterhal" is Luxembourgish,
        # and a tester should not have to bridge it to "Kletterhalle" while
        # standing at the lake.
        "intro": (
            "Folgt dem Uferweg im Uhrzeigersinn bis zur Kloterhal "
            "(Kletterhalle) und dem Trampolinpark bei der Jugendherberge. "
            "Hier wird im Sommer auch gebadet — die Crush-Statue steht am Weg."
        ),
        "challenge_type": "multiple_choice",
        "question": "Der See hat 14 Grad. Du...",
        "options": {
            "1": "springst sofort rein",
            "2": "gehst langsam bis zu den Knien",
            "3": "bleibst am Ufer und lachst die anderen aus",
        },
        "answer": "",  # Accepts any choice
        "alternatives": [],
        "hint_1": "Ehrlich sein zählt hier mehr als mutig sein.",
        "points": 100,
    },
    {
        "order": 3,
        "name": "Ostufer",
        "lat": "49.797563",
        "lng": "6.416679",
        "unlock_mode": "gps_qr",
        "intro": (
            "Weiter am ruhigen Ostufer entlang. Bleibt auf dem breiten Weg, "
            "von dem aus ihr über den ganzen See zurückschauen könnt."
        ),
        "challenge_type": "multiple_choice",
        "question": "Ein perfekter Nachmittag am See ist für dich...",
        "options": {
            "1": "Schwimmen, Sonne, Sprung ins Wasser",
            "2": "Spazieren und gute Gespräche",
            "3": "Decke, Buch und Ruhe",
        },
        "answer": "",  # Accepts any choice
        "alternatives": [],
        "hint_1": "Stell dir den nächsten freien Samstag vor.",
        "points": 100,
    },
    {
        "order": 4,
        "name": "Südspëtz",
        "lat": "49.794390",
        "lng": "6.415674",
        "unlock_mode": "gps_qr",
        "intro": (
            "Am südlichen Ende des Sees, nach der weiten Kurve. Bleibt auf dem "
            "asphaltierten Weg — die Crush-Statue steht kurz dahinter."
        ),
        "challenge_type": "multiple_choice",
        "question": "Beim Grillen mit Freunden bist du...",
        "options": {
            "1": "am Grill — ich habe das im Griff",
            "2": "für Musik und Stimmung zuständig",
            "3": "der, der isst, redet und zuhört",
        },
        "answer": "",  # Accepts any choice
        "alternatives": [],
        "hint_1": "Denk an das letzte Grillfest, bei dem du dabei warst.",
        "points": 100,
    },
    {
        "order": 5,
        "name": "Westufer bei den Inselen",
        "lat": "49.797667",
        "lng": "6.410571",
        "unlock_mode": "gps_qr",
        "intro": (
            "Jetzt nach Norden auf der Westseite, auf Höhe der kleinen Inseln. "
            "Nehmt den Hauptweg, nicht den Abstecher auf die Inseln."
        ),
        "challenge_type": "multiple_choice",
        "question": "Etwas Neues ausprobieren:",
        "options": {
            "1": "sofort, ohne lange nachzudenken",
            "2": "erst zuschauen, dann mitmachen",
            "3": "lieber bei dem bleiben, was ich kann",
        },
        "answer": "",  # Accepts any choice
        "alternatives": [],
        "hint_1": "Wie war es das letzte Mal, als dich jemand zu etwas überredet hat?",
        "points": 100,
    },
    {
        "order": 6,
        # Same coordinates as station 1 on purpose: the hunt is won back at
        # the villa where it started, so the finish line matches what the
        # event copy promises. (The prototype closes its loop the same way.)
        # One statue position therefore carries two codes — or players type
        # the manual codes, which the seed report prints.
        "name": "Réimervilla (Schluss)",
        "lat": "49.804108",
        "lng": "6.411389",
        "unlock_mode": "gps_qr",
        "intro": (
            "Zurück am Startpunkt unterhalb der Réimervilla — der Kreis "
            "schließt sich. Scannt die letzte Crush-Statue und denkt an "
            "Station 1 zurück."
        ),
        "challenge_type": "riddle",
        "question": (
            "Zum Schluss: Beim Ausbaggern dieses künstlichen Sees fanden die "
            "Arbeiter 1975 Mauerreste. Was wurde hier ausgegraben?"
        ),
        "answer": "Römische Villa",
        "alternatives": [
            "Roemische Villa",
            "eine römische Villa",
            "Gallo-römische Villa",
            "Römervilla",
            "Reimervilla",
            "Réimervilla",
            "Villa Romaine",
            "Roman villa",
            "Villa",
        ],
        "hint_1": "Station 1 steht direkt darauf — 40 Räume, Fußbodenheizung, Mosaike.",
        "points": 150,
    },
]

# Applied to every preset that does not override them, so each entry below
# shows only its real deltas.
PRESET_DEFAULTS = {
    "radius_meters": 40,
    "completion_message": "Nice — on to the next station!",
    "success_message": "Correct!",
    "navigation_mode": "map",  # target pin shown — easiest to test GPS with
    "team_size_max": 4,
    "allow_self_join": True,
    "is_published": True,
    # Solo, credential-free field-test shape: registers one named existing
    # account instead of creating debug users, and refuses to touch production.
    "solo_prototype": False,
    "coords_note": None,
}

PRESETS = {
    "lux_city": {
        "event_title": "🧭 [DEBUG] Luxembourg City Crush Cache",
        "event_description": "Debug scavenger hunt through Luxembourg City for QA.",
        "location": "Luxembourg City",
        "address": "Place de la Constitution, Luxembourg",
        "hunt_title": "Old Town GPS Hunt",
        "hunt_description": "Follow the pins around the Ville Haute. First team home wins!",
        "team_name": "Explorers",
        "stations": STATIONS_LUX_CITY,
    },
    "minette": {
        "event_title": '🧭 [DEBUG] Fond-de-Gras Crush Cache "Minette"',
        "event_description": (
            "Debug scavenger hunt through Fond-de-Gras Minette mining area for QA."
        ),
        "location": "Fond-de-Gras",
        "address": "Fond-de-Gras, L-4570 Differdange",
        "hunt_title": 'Crush Cache "Minette"',
        "hunt_description": "Explore the historic red-rock mining valley of Fond-de-Gras. First team to the Schluss station wins!",
        "team_name": "Minette Miners",
        "stations": STATIONS_MINETTE,
    },
    "echternach_lake": {
        "event_title": "🧭 [PROTOTYPE] Echternach Lake Crush Cache",
        "event_description": (
            "Private GPS-only prototype of a Crush Cache around the official "
            "3.25 km Echternach Lake comfort trail."
        ),
        "location": "Echternach Lake",
        "address": "100 Rue Grégoire Schouppe, L-6479 Echternach",
        "hunt_title": "Echternach Lake Prototype",
        "hunt_description": (
            "Walk the full lake loop and test six GPS checkpoints. Your "
            "answers collect feedback for a future Crush experience."
        ),
        "team_name": "Lake Prototype",
        "solo_prototype": True,
        "team_size_max": 1,
        "allow_self_join": False,
        # Reachable only by its direct URL — never in staging's public catalogue.
        "is_published": False,
        "stations": STATIONS_ECHTERNACH_LAKE,
    },
    "echternach_um_see": {
        # Deliberately NOT "Echternach Lake" — the prototype above owns that
        # phrase, and tests look both events up by title substring.
        "event_title": '🧭 [DEBUG] Echternach Crush Cache "Um See"',
        "event_description": "Debug team scavenger hunt around Echternach lake for QA.",
        "location": "Echternach",
        "address": "Rue des Romains, L-6478 Echternach",
        "hunt_title": 'Crush Cache "Um See"',
        "hunt_description": (
            "Ein Rundweg um den Echternacher See: sechs Stationen, sechs "
            "Fragen, rund 3,25 km. Wer zuerst zurück an der Villa ist, gewinnt!"
        ),
        "team_name": "Seewanderer",
        # Wider than the other presets: these are GPX track points rather than
        # surveyed station markers, and the lakeside path is open ground, so
        # the arrival check needs slack the 40 m default does not give.
        "radius_meters": 75,
        "completion_message": "Stark — weiter zur nächsten Station!",
        "success_message": "Gespeichert — weiter geht's!",
        "stations": STATIONS_ECHTERNACH_UM_SEE,
        "coords_note": (
            "Stations follow the official GPX track, not surveyed marker "
            "positions — check each pin on the walk and correct it in the "
            "admin (Cache Stations) if a station is hard to trigger."
        ),
    },
}

REQUIRED_PRESET_KEYS = {
    "event_title",
    "event_description",
    "location",
    "address",
    "hunt_title",
    "hunt_description",
    "team_name",
    "stations",
}


def _build_presets():
    """Fill in PRESET_DEFAULTS and fail loudly on a malformed preset.

    Runs at import, so a typo'd or missing key is a startup error naming the
    preset rather than a KeyError midway through seeding.
    """
    built = {}
    for name, entry in PRESETS.items():
        unknown = set(entry) - REQUIRED_PRESET_KEYS - set(PRESET_DEFAULTS)
        if unknown:
            raise ImproperlyConfigured(
                f"Crush Cache preset '{name}' has unknown keys: {sorted(unknown)}"
            )
        missing = REQUIRED_PRESET_KEYS - set(entry)
        if missing:
            raise ImproperlyConfigured(
                f"Crush Cache preset '{name}' is missing keys: {sorted(missing)}"
            )
        built[name] = {**PRESET_DEFAULTS, **entry}
    return built


PRESETS = _build_presets()

DEFAULT_PRESET = "lux_city"


class Command(BaseCommand):
    help = (
        "Seed a playable Crush Cache hunt for manual QA. The Echternach lake "
        "preset supports a credential-free, private staging prototype."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--preset",
            choices=sorted(PRESETS),
            default=DEFAULT_PRESET,
            help=(
                "Which hunt preset to seed: 'lux_city' (default), 'minette' "
                "(Fond-de-Gras), 'echternach_lake' (solo GPS prototype) or "
                "'echternach_um_see' (Echternach team hunt)."
            ),
        )
        parser.add_argument(
            "--player-email",
            help=(
                "Existing account to register as the solo prototype player. "
                "Required for echternach_lake; no account or password is created."
            ),
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete the existing debug hunt/event first, then reseed.",
        )
        parser.add_argument(
            "--live",
            action="store_true",
            help="Start the hunt immediately (status=live) instead of draft.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Override the local-only safety guard (NOT for production).",
        )

    def handle(self, *args, **options):
        preset = PRESETS[options["preset"]]
        # Passing --player-email puts ANY preset on the solo path, so a hunt
        # written for local QA can be field-tested on staging without seeding
        # the debug accounts and published event it would create locally.
        # solo_prototype presets have no other path — they always require it.
        use_solo_path = preset["solo_prototype"] or bool(options.get("player_email"))
        if use_solo_path:
            self._validate_prototype_environment(options)
            preset = self._solo_safe(preset)

        # The debug path creates debug_cache_coach@crush.lu as STAFF on a
        # shared password and publishes the event into the public catalogue.
        # That is fine on a laptop and never fine on an Azure slot, and
        # --force must not buy its way past it: the solo path is right there.
        if not use_solo_path and "WEBSITE_HOSTNAME" in os.environ:
            raise CommandError(
                f"'{options['preset']}' would create shared-password debug "
                "accounts (including a staff account) and a published event. "
                "Refusing to do that on Azure — pass --player-email <existing "
                "account> to seed it as a private solo hunt instead."
            )

        force = options["force"]
        if not force:
            if "WEBSITE_HOSTNAME" in os.environ:
                raise CommandError(
                    "Refusing to run on Azure. Use --force only if you are sure."
                )
            if not settings.DEBUG:
                raise CommandError(
                    "Refusing to run with DEBUG=False. Use --force to override."
                )

        if not getattr(settings, "CRUSH_CACHE_ENABLED", False):
            self.stdout.write(
                self.style.WARNING(
                    "⚠ CRUSH_CACHE_ENABLED is not true — seeding anyway, but the "
                    "gameplay URLs will 404 until you set CRUSH_CACHE_ENABLED=true "
                    "in your .env and restart runserver."
                )
            )

        event_title = preset["event_title"]
        existing = MeetupEvent.objects.filter(
            title=event_title, event_type="crush_cache"
        ).first()
        if existing:
            if not options["reset"]:
                raise CommandError(
                    f"The debug Crush Cache event '{event_title}' already exists. Re-run with "
                    "--reset to delete and recreate it."
                )
            # Cascades to hunt, stations, challenges, teams, registrations.
            existing.delete()
            self.stdout.write(
                f"Deleted the previous debug Crush Cache event '{event_title}'."
            )

        if use_solo_path:
            player = self._get_existing_player(options["player_email"])
            # One transaction, so a malformed preset cannot leave a half-built
            # event behind that the next run would refuse to overwrite.
            with transaction.atomic():
                event = self._create_event(preset)
                hunt = self._create_hunt(event, player, preset)
                self._create_stations(hunt, preset)
                team = self._create_demo_team(hunt, preset)

                if options["live"]:
                    hunt.status = "live"
                    hunt.started_at = timezone.now()
                    hunt.save(update_fields=["status", "started_at"])

                self._register_prototype_player(hunt, team, player)
            self._report_prototype(hunt, team, player, preset)
            return

        coach = self._ensure_coach()
        with transaction.atomic():
            event = self._create_event(preset)
            hunt = self._create_hunt(event, coach, preset)
            self._create_stations(hunt, preset)
            team = self._create_demo_team(hunt, preset)
            players = self._register_players(event)

            if options["live"]:
                hunt.status = "live"
                hunt.started_at = timezone.now()
                hunt.save(update_fields=["status", "started_at"])

        self._report(hunt, team, players, preset)

    def _solo_safe(self, preset):
        """The shape the solo path guarantees, whatever the preset asked for.

        This is the safety contract that makes a preset runnable on staging:
        nothing published into the public catalogue, and one seat so a leaked
        join code cannot pull anyone else in. Credential handling is the other
        half and lives in the seeding path — the solo path never creates an
        account, sets a password, or verifies an email.
        """
        return {
            **preset,
            "is_published": False,
            "team_size_max": 1,
            "allow_self_join": False,
        }

    def _validate_prototype_environment(self, options):
        # Named from the chosen preset, not hardcoded: any preset can set
        # solo_prototype, and a guard that blames the wrong one sends whoever
        # hits it looking in the wrong place.
        name = options["preset"]
        if not options.get("player_email"):
            raise CommandError(f"--player-email is required for the {name} prototype.")

        # This prototype may run locally or in the staging slot, but never in
        # the production slot. --force only bypasses the command's general
        # local-only guard; it cannot bypass this production boundary.
        hostname = os.getenv("WEBSITE_HOSTNAME", "").casefold()
        slot_name = os.getenv("WEBSITE_SLOT_NAME", "").casefold()
        if hostname and "staging" not in hostname and slot_name != "staging":
            raise CommandError(
                f"The {name} prototype is restricted to local or Azure "
                "staging environments; refusing to modify production."
            )

    def _get_existing_player(self, email):
        player = User.objects.filter(email__iexact=email).first()
        if player is None:
            player = User.objects.filter(username__iexact=email).first()
        if player is None:
            raise CommandError(
                f"No existing account matches '{email}'. Create or choose a "
                "staging account, then rerun the command."
            )
        return player

    # ------------------------------------------------------------------ #

    def _ensure_coach(self):
        from allauth.account.models import EmailAddress

        coach_user, _ = User.objects.get_or_create(
            username="debug_cache_coach@crush.lu",
            defaults={"email": "debug_cache_coach@crush.lu"},
        )
        # Loginable (password + verified email under mandatory verification) so
        # you can reach the coach dashboard to start/finish the hunt.
        coach_user.is_staff = True
        coach_user.set_password("debug2025")
        coach_user.save()
        EmailAddress.objects.update_or_create(
            user=coach_user,
            email=coach_user.email,
            defaults={"verified": True, "primary": True},
        )
        CrushCoach.objects.get_or_create(
            user=coach_user,
            defaults={
                "bio": "Crush Cache debug host",
                "specializations": "Scavenger hunts",
                "is_active": True,
            },
        )
        return coach_user

    def _create_event(self, preset):
        now = timezone.now()
        return MeetupEvent.objects.create(
            title=preset["event_title"],
            description=preset["event_description"],
            event_type="crush_cache",
            date_time=now + timedelta(hours=2),
            registration_deadline=now + timedelta(hours=1),
            location=preset["location"],
            address=preset["address"],
            max_participants=30,
            is_published=preset["is_published"],
        )

    def _create_hunt(self, event, coach, preset):
        return CacheHunt.objects.create(
            event=event,
            title=preset["hunt_title"],
            description=preset["hunt_description"],
            status="draft",
            navigation_mode=preset["navigation_mode"],
            team_size_max=preset["team_size_max"],
            allow_self_join=preset["allow_self_join"],
            created_by=coach,
        )

    def _create_stations(self, hunt, preset):
        for s in preset["stations"]:
            station = CacheStation.objects.create(
                hunt=hunt,
                order=s["order"],
                name=s["name"],
                intro_text=s["intro"],
                latitude=s["lat"],
                longitude=s["lng"],
                radius_meters=s.get("radius_meters", preset["radius_meters"]),
                unlock_mode=s["unlock_mode"],
                completion_message=preset["completion_message"],
            )
            CacheChallenge.objects.create(
                station=station,
                challenge_order=1,
                challenge_type=s["challenge_type"],
                question=s["question"],
                options=s.get("options", {}),
                correct_answer=s["answer"],
                alternative_answers=s["alternatives"],
                hint_1=s["hint_1"],
                points_awarded=s["points"],
                success_message=preset["success_message"],
            )

    def _create_demo_team(self, hunt, preset):
        return CacheTeam.objects.create(
            hunt=hunt, name=preset["team_name"], color="#3b82f6"
        )

    def _register_prototype_player(self, hunt, team, player):
        from crush_lu.models.crush_cache import CacheTeamMember, CacheTeamProgress

        registration = EventRegistration.objects.create(
            event=hunt.event,
            user=player,
            status="attended",
            checked_in_at=timezone.now(),
        )
        CacheTeamMember.objects.create(
            hunt=hunt,
            team=team,
            registration=registration,
        )
        CacheTeamProgress.objects.create(
            team=team,
            current_station=hunt.ordered_stations().first(),
            started_at=timezone.now() if hunt.is_live else None,
        )

    def _register_players(self, event):
        from allauth.account.models import EmailAddress

        for i in (1, 2):
            u, _ = User.objects.get_or_create(
                username=f"debug_cache_player{i}@crush.lu",
                defaults={"email": f"debug_cache_player{i}@crush.lu"},
            )

        seeded_usernames = [
            "debug_cache_coach@crush.lu",
            "debug_cache_player1@crush.lu",
            "debug_cache_player2@crush.lu",
        ]
        debug_users = list(User.objects.filter(username__in=seeded_usernames))

        registered = []
        for user in debug_users:
            user.set_password("debug2025")
            if not user.email and "@" in user.username:
                user.email = user.username
            user.save()

            if user.email:
                EmailAddress.objects.update_or_create(
                    user=user,
                    email=user.email,
                    defaults={"verified": True, "primary": True},
                )

            reg, _ = EventRegistration.objects.get_or_create(
                event=event, user=user, defaults={"status": "confirmed"}
            )
            if reg.status not in ("confirmed", "attended"):
                reg.status = "confirmed"
                reg.save(update_fields=["status"])
            registered.append(user)
        return registered

    def _write_station_lines(self, hunt, preset):
        """Coordinates plus, for QR stations, the code to type when there is
        no printed sticker on site yet."""
        codes = {s.order: s for s in hunt.ordered_stations()}
        for s in preset["stations"]:
            station = codes.get(s["order"])
            self.stdout.write(
                f"     {s['order']}. {s['name']}: {s['lat']}, {s['lng']} "
                f"({s['unlock_mode']})"
            )
            self.stdout.write(
                f"        https://www.google.com/maps?q={s['lat']},{s['lng']}"
            )
            if station is not None and station.requires_qr:
                self.stdout.write(
                    f"        manual code (no sticker needed): {station.manual_code}"
                )

    def _report(self, hunt, team, players, preset):
        out = self.stdout
        style = self.style
        out.write(style.SUCCESS(f"\n✅ Seeded '{hunt.title}' ({hunt.status})."))
        out.write(
            f"   Event id: {hunt.event_id}   "
            f"Coach login: {hunt.created_by.username} / debug2025"
        )
        out.write(f"   Demo team: {team.name} — join code: {team.join_code}")
        out.write(f"   Registered players ({len(players)}):")
        for u in players[:12]:
            out.write(f"     - {u.username}")
        out.write("\n   Play URL (log in as a registered player first):")
        out.write(f"     http://localhost:8000/en/events/{hunt.event_id}/cache/")
        out.write(
            "\n   Station coordinates (for the DevTools → Sensors override, "
            "and to check each pin on a map):"
        )
        self._write_station_lines(hunt, preset)
        if preset["coords_note"]:
            out.write(style.WARNING(f"\n   ⚠ {preset['coords_note']}"))
        if hunt.status == "draft":
            out.write(
                style.WARNING(
                    "\n   Hunt is DRAFT. Log in as the coach and press Start on the "
                    "coach dashboard (or reseed with --live) before GPS positions "
                    "are accepted."
                )
            )
        out.write("")

    def _report_prototype(self, hunt, team, player, preset):
        out = self.stdout
        style = self.style
        out.write(style.SUCCESS(f"\nSeeded private '{hunt.title}' ({hunt.status})."))
        out.write(f"   Event id: {hunt.event_id}")
        out.write(f"   Existing player: {player.email or player.username}")
        out.write(f"   Solo team: {team.name}")
        out.write("\n   Staging play URL (sign in first):")
        out.write(f"     https://test.crush.lu/en/events/{hunt.event_id}/cache/play/")
        out.write("\n   Coach dashboard (you created the hunt, so you can watch it):")
        out.write(f"     https://test.crush.lu/en/events/{hunt.event_id}/cache/coach/")
        needs_qr = any(s.requires_qr for s in hunt.ordered_stations())
        if needs_qr:
            # This preset expects a Crush Statue at each stop. Without printed
            # stickers the manual code is the only way past the scan, so it
            # belongs in the report you carry to the lake.
            out.write("\n   Stations (scan the statue, or type the manual code):")
            self._write_station_lines(hunt, preset)
        else:
            out.write("\n   GPS-only stations on the official lake loop:")
            for station in preset["stations"]:
                out.write(
                    f"     {station['order']}. {station['name']}: "
                    f"{station['lat']}, {station['lng']}"
                )
        if preset["coords_note"]:
            out.write(style.WARNING(f"\n   ⚠ {preset['coords_note']}"))
        if hunt.status == "draft":
            out.write(
                style.WARNING(
                    "\n   Hunt is DRAFT. Rerun with --reset --live before the walk."
                )
            )
        out.write("")
