"""
Seed a ready-to-play **Crush Cache** scavenger hunt for manual QA of the
GPS + QR gameplay.

The debug presets create a `crush_cache` MeetupEvent + CacheHunt with landmark
stations, challenges and demo users. The Echternach Lake preset instead reuses
one explicitly named account, creates no credentials, stays unpublished, and
is permitted only locally or in the Azure staging slot.

By default the command refuses to run on Azure (WEBSITE_HOSTNAME set) or when
DEBUG is False. `--force` permits the Echternach preset on staging, while its
separate production guard cannot be bypassed. The gameplay URLs also require
CRUSH_CACHE_ENABLED=true.

Usage:
    python manage.py seed_crush_cache --reset
    python manage.py seed_crush_cache --reset --live   # start the hunt now
    python manage.py seed_crush_cache --preset echternach_lake --reset --live \
        --player-email you@example.com --force

Testing GPS without being in Luxembourg: open the play page in Chrome, then
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

EVENT_TITLE_LUX = "🧭 [DEBUG] Luxembourg City Crush Cache"
EVENT_TITLE_MINETTE = '🧭 [DEBUG] Fond-de-Gras Crush Cache "Minette"'
EVENT_TITLE_ECHTERNACH = "🧭 [PROTOTYPE] Echternach Lake Crush Cache"

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


class Command(BaseCommand):
    help = (
        "Seed a playable Crush Cache hunt for manual QA. The Echternach preset "
        "supports a credential-free, private staging prototype."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--preset",
            choices=["lux_city", "minette", "echternach_lake"],
            default="lux_city",
            help=(
                "Which hunt preset to seed: 'lux_city' (default), 'minette' "
                "(Fond-de-Gras), or 'echternach_lake'."
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
        preset = options["preset"]
        if preset == "echternach_lake":
            self._validate_echternach_environment(options)

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

        if preset == "echternach_lake":
            event_title = EVENT_TITLE_ECHTERNACH
            stations_data = STATIONS_ECHTERNACH_LAKE
        elif preset == "minette":
            event_title = EVENT_TITLE_MINETTE
            stations_data = STATIONS_MINETTE
        else:
            event_title = EVENT_TITLE_LUX
            stations_data = STATIONS_LUX_CITY

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

        if preset == "echternach_lake":
            player = self._get_existing_player(options["player_email"])
            event = self._create_event(preset, event_title)
            hunt = self._create_hunt(event, player, preset)
            self._create_stations(hunt, stations_data)
            team = self._create_demo_team(hunt, preset)

            if options["live"]:
                hunt.status = "live"
                hunt.started_at = timezone.now()
                hunt.save(update_fields=["status", "started_at"])

            self._register_prototype_player(hunt, team, player)
            self._report_prototype(hunt, team, player, stations_data)
            return

        coach = self._ensure_coach()
        event = self._create_event(preset, event_title)
        hunt = self._create_hunt(event, coach, preset)
        self._create_stations(hunt, stations_data)
        team = self._create_demo_team(hunt, preset)
        players = self._register_players(event)

        if options["live"]:
            hunt.status = "live"
            hunt.started_at = timezone.now()
            hunt.save(update_fields=["status", "started_at"])

        self._report(hunt, team, players, stations_data)

    def _validate_echternach_environment(self, options):
        if not options.get("player_email"):
            raise CommandError(
                "--player-email is required for the echternach_lake prototype."
            )

        # This prototype may run locally or in the staging slot, but never in
        # the production slot. --force only bypasses the command's general
        # local-only guard; it cannot bypass this production boundary.
        hostname = os.getenv("WEBSITE_HOSTNAME", "").casefold()
        slot_name = os.getenv("WEBSITE_SLOT_NAME", "").casefold()
        if hostname and "staging" not in hostname and slot_name != "staging":
            raise CommandError(
                "The echternach_lake prototype is restricted to local or Azure "
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

    def _create_event(self, preset, title):
        now = timezone.now()
        if preset == "echternach_lake":
            desc = (
                "Private GPS-only prototype of a Crush Cache around the official "
                "3.25 km Echternach Lake comfort trail."
            )
            loc = "Echternach Lake"
            addr = "100 Rue Grégoire Schouppe, L-6479 Echternach"
        elif preset == "minette":
            desc = (
                "Debug scavenger hunt through Fond-de-Gras Minette mining area for QA."
            )
            loc = "Fond-de-Gras"
            addr = "Fond-de-Gras, L-4570 Differdange"
        else:
            desc = "Debug scavenger hunt through Luxembourg City for QA."
            loc = "Luxembourg City"
            addr = "Place de la Constitution, Luxembourg"

        return MeetupEvent.objects.create(
            title=title,
            description=desc,
            event_type="crush_cache",
            date_time=now + timedelta(hours=2),
            registration_deadline=now + timedelta(hours=1),
            location=loc,
            address=addr,
            max_participants=30,
            # The Echternach prototype is reachable only by its direct URL and
            # does not appear in staging's public event catalogue.
            is_published=preset != "echternach_lake",
        )

    def _create_hunt(self, event, coach, preset):
        if preset == "echternach_lake":
            title = "Echternach Lake Prototype"
            desc = (
                "Walk the full lake loop and test six GPS checkpoints. Your "
                "answers collect feedback for a future Crush experience."
            )
        elif preset == "minette":
            title = 'Crush Cache "Minette"'
            desc = "Explore the historic red-rock mining valley of Fond-de-Gras. First team to the Schluss station wins!"
        else:
            title = "Old Town GPS Hunt"
            desc = "Follow the pins around the Ville Haute. First team home wins!"

        return CacheHunt.objects.create(
            event=event,
            title=title,
            description=desc,
            status="draft",
            navigation_mode="map",  # target pin shown — easiest to test GPS with
            team_size_max=1 if preset == "echternach_lake" else 4,
            allow_self_join=preset != "echternach_lake",
            created_by=coach,
        )

    def _create_stations(self, hunt, stations_data):
        for s in stations_data:
            station = CacheStation.objects.create(
                hunt=hunt,
                order=s["order"],
                name=s["name"],
                intro_text=s["intro"],
                latitude=s["lat"],
                longitude=s["lng"],
                radius_meters=s.get("radius_meters", 40),
                unlock_mode=s["unlock_mode"],
                completion_message="Nice — on to the next station!",
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
                success_message="Correct!",
            )

    def _create_demo_team(self, hunt, preset):
        if preset == "echternach_lake":
            team_name = "Lake Prototype"
        elif preset == "minette":
            team_name = "Minette Miners"
        else:
            team_name = "Explorers"
        return CacheTeam.objects.create(hunt=hunt, name=team_name, color="#3b82f6")

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

    def _report(self, hunt, team, players, stations_data):
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
        out.write("\n   Station coordinates (for the DevTools → Sensors override):")
        for s in stations_data:
            out.write(
                f"     {s['order']}. {s['name']}: {s['lat']}, {s['lng']} "
                f"({s['unlock_mode']})"
            )
        if hunt.status == "draft":
            out.write(
                style.WARNING(
                    "\n   Hunt is DRAFT. Log in as the coach and press Start on the "
                    "coach dashboard (or reseed with --live) before GPS positions "
                    "are accepted."
                )
            )
        out.write("")

    def _report_prototype(self, hunt, team, player, stations_data):
        out = self.stdout
        style = self.style
        out.write(style.SUCCESS(f"\nSeeded private '{hunt.title}' ({hunt.status})."))
        out.write(f"   Event id: {hunt.event_id}")
        out.write(f"   Existing player: {player.email or player.username}")
        out.write(f"   Solo team: {team.name}")
        out.write("\n   Staging play URL (sign in first):")
        out.write(f"     https://test.crush.lu/en/events/{hunt.event_id}/cache/play/")
        out.write("\n   GPS-only stations on the official lake loop:")
        for station in stations_data:
            out.write(
                f"     {station['order']}. {station['name']}: "
                f"{station['lat']}, {station['lng']}"
            )
        if hunt.status == "draft":
            out.write(
                style.WARNING(
                    "\n   Hunt is DRAFT. Rerun with --reset --live before the walk."
                )
            )
        out.write("")
