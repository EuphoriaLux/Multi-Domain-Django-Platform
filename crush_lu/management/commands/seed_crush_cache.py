"""
Seed a ready-to-play **Crush Cache** scavenger hunt, for manual QA of the
GPS + QR gameplay.

Creates one `crush_cache` MeetupEvent + a CacheHunt with a walking loop of real
Luxembourg landmarks (GPS coords + QR stations), each with a challenge, plus a
demo team. Every existing `debug_*` account (from `seed_debug_profiles`) is
registered as a confirmed attendee so you can log in and play immediately; if
none exist it creates a couple of players.

Presets live in the `PRESETS` registry below — adding a hunt is data, not code:

    lux_city    Luxembourg City old town, English trivia about landmarks
    minette     Fond-de-Gras mining valley, German icebreakers, QR at every stop
    echternach  Echternach lake loop, German icebreakers, QR at every stop

LOCAL-ONLY: refuses to run on Azure (WEBSITE_HOSTNAME set) or when DEBUG is
False, unless --force. Also needs the feature flag: CRUSH_CACHE_ENABLED=true
(in your .env), or the gameplay URLs 404.

Usage:
    python manage.py seed_crush_cache --reset
    python manage.py seed_crush_cache --preset echternach --reset --live

Testing GPS without being on site: open the play page in Chrome, then
DevTools → ⋮ → More tools → Sensors → Location → "Other…" and paste a
station's lat/lng (printed below). The arrival check will pass.
"""

import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
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

# Echternach lake Crush Cache "Um See" — a ~1.8 km clockwise loop of the
# lakeside path, 6 stations, Crush Statue QR at every stop (same shape as the
# Minette hunt). The loop starts at the Roman villa on the west shore, runs
# east along the south bank to the playground/BBQ area, and comes back along
# the north bank past the bike park to the boat rental.
#
# Station 1 is the only surveyed point: the villa's published visitor-parking
# GPS (N49°48'18.1" E6°24'30.8"). Stations 2-6 are laid out from it along the
# lake and are ACCURATE TO ROUGHLY 50-100 m — see `coords_note` on the preset.
# Walk the loop with a phone and correct them here (or in the Django admin,
# Cache Stations) before running this hunt for real.
STATIONS_ECHTERNACH = [
    {
        "order": 1,
        "name": "Réimervilla (Villa Romaine)",
        "lat": "49.805028",
        "lng": "6.408556",
        "unlock_mode": "gps_qr",
        "intro": "Start am Westufer bei der Réimervilla! Beim Ausbaggern des Sees stießen die Arbeiter hier 1975 auf etwas sehr Altes. Findet die Crush-Statue beim Museumseingang und scannt den QR-Code.",
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
        "name": "Uferwee Südufer",
        "lat": "49.805900",
        "lng": "6.411800",
        "unlock_mode": "gps_qr",
        "intro": "Folgt dem Uferweg nach Osten, mit dem Wasser zu eurer Linken. Zwischen See und Wald wartet die nächste Crush-Statue am Wegrand.",
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
        "order": 3,
        "name": "Aventure-Insel & Jugendherberge",
        "lat": "49.807000",
        "lng": "6.415200",
        "unlock_mode": "gps_qr",
        "intro": "Ihr erreicht die Aventure-Insel bei der Jugendherberge — hier wird von Mai bis September geschwommen. Die Crush-Statue steht beim Badebereich.",
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
        "order": 4,
        "name": "Ostufer: Spillplaz & Grillplazen",
        "lat": "49.809200",
        "lng": "6.418200",
        "unlock_mode": "gps_qr",
        "intro": "Am Ostufer liegen Spielplatz und Grillplätze, mit Blick zurück über den ganzen See. Sucht die Crush-Statue bei den Grillhütten.",
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
        "name": "Nordufer: Bike Park & Trampolinen",
        "lat": "49.809600",
        "lng": "6.414300",
        "unlock_mode": "gps_qr",
        "intro": "Jetzt zurück am Nordufer, vorbei am Bike Park und den Trampolinen. Die nächste Crush-Statue steht am Wegrand.",
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
        "name": "Bootsverleih Nordwestufer (Schluss)",
        "lat": "49.807600",
        "lng": "6.410600",
        "unlock_mode": "gps_qr",
        "intro": "Letzte Station am Nordwestufer beim Bootsverleih — der Kreis schließt sich. Scannt die letzte Crush-Statue und denkt an Station 1 zurück.",
        "challenge_type": "riddle",
        "question": "Zum Schluss: Beim Ausbaggern dieses künstlichen Sees fanden die Arbeiter 1975 Mauerreste. Was wurde hier ausgegraben?",
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

# Everything that differs between hunts lives here, so a new preset is data,
# not another branch in the command.
PRESETS = {
    "lux_city": {
        "event_title": "🧭 [DEBUG] Luxembourg City Crush Cache",
        "event_description": "Debug scavenger hunt through Luxembourg City for QA.",
        "location": "Luxembourg City",
        "address": "Place de la Constitution, Luxembourg",
        "hunt_title": "Old Town GPS Hunt",
        "hunt_description": "Follow the pins around the Ville Haute. First team home wins!",
        "team_name": "Explorers",
        "radius_meters": 40,
        "completion_message": "Nice — on to the next station!",
        "success_message": "Correct!",
        "stations": STATIONS_LUX_CITY,
    },
    "minette": {
        "event_title": '🧭 [DEBUG] Fond-de-Gras Crush Cache "Minette"',
        "event_description": "Debug scavenger hunt through Fond-de-Gras Minette mining area for QA.",
        "location": "Fond-de-Gras",
        "address": "Fond-de-Gras, L-4570 Differdange",
        "hunt_title": 'Crush Cache "Minette"',
        "hunt_description": "Explore the historic red-rock mining valley of Fond-de-Gras. First team to the Schluss station wins!",
        "team_name": "Minette Miners",
        "radius_meters": 40,
        "completion_message": "Nice — on to the next station!",
        "success_message": "Correct!",
        "stations": STATIONS_MINETTE,
    },
    "echternach": {
        "event_title": '🧭 [DEBUG] Echternach Lake Crush Cache "Um See"',
        "event_description": "Debug scavenger hunt around Echternach lake for QA.",
        "location": "Echternach",
        "address": "Rue des Romains, L-6478 Echternach",
        "hunt_title": 'Crush Cache "Um See"',
        "hunt_description": "Ein Rundweg um den Echternacher See: sechs Stationen, sechs Fragen, rund 1,8 km. Wer zuerst zurück beim Bootsverleih ist, gewinnt!",
        "team_name": "Seewanderer",
        # Slightly more forgiving than the other presets: the lakeside path is
        # open ground and these coordinates are still estimates.
        "radius_meters": 50,
        "completion_message": "Stark — weiter zur nächsten Station!",
        "success_message": "Gespeichert — weiter geht's!",
        "stations": STATIONS_ECHTERNACH,
        "coords_note": (
            "Only station 1 (Réimervilla) is a surveyed coordinate. Stations 2-6 "
            "are laid out along the lake and are accurate to roughly 50-100 m. "
            "Walk the loop and correct them in the admin (Cache Stations) "
            "before running this hunt for real."
        ),
    },
}

DEFAULT_PRESET = "lux_city"


class Command(BaseCommand):
    help = (
        "Seed a playable Crush Cache hunt (Luxembourg City, Fond-de-Gras Minette "
        "or Echternach lake) for manual QA. Local-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--preset",
            choices=sorted(PRESETS),
            default=DEFAULT_PRESET,
            help=(
                "Which hunt preset to seed: 'lux_city' (default), 'minette' "
                "(Fond-de-Gras) or 'echternach' (lake loop)."
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

        preset = PRESETS[options["preset"]]
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

        coach = self._ensure_coach()
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
            is_published=True,
        )

    def _create_hunt(self, event, coach, preset):
        return CacheHunt.objects.create(
            event=event,
            title=preset["hunt_title"],
            description=preset["hunt_description"],
            status="draft",
            navigation_mode="map",  # target pin shown — easiest to test GPS with
            team_size_max=4,
            allow_self_join=True,
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
                radius_meters=preset["radius_meters"],
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
        for s in preset["stations"]:
            out.write(
                f"     {s['order']}. {s['name']}: {s['lat']}, {s['lng']} "
                f"({s['unlock_mode']})"
            )
            out.write(f"        https://www.google.com/maps?q={s['lat']},{s['lng']}")
        if preset.get("coords_note"):
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
