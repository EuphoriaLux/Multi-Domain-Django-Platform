"""
Seed a ready-to-play **Crush Cache** scavenger hunt through Luxembourg City,
for manual QA of the GPS + QR gameplay.

Creates one `crush_cache` MeetupEvent + a CacheHunt with a walking loop of real
Luxembourg City landmarks (GPS coords + a QR station), each with a challenge,
plus a demo team. Every existing `debug_*` account (from
`seed_debug_profiles`) is registered as a confirmed attendee so you can log in
and play immediately; if none exist it creates a couple of players.

LOCAL-ONLY: refuses to run on Azure (WEBSITE_HOSTNAME set) or when DEBUG is
False, unless --force. Also needs the feature flag: CRUSH_CACHE_ENABLED=true
(in your .env), or the gameplay URLs 404.

Usage:
    python manage.py seed_crush_cache --reset
    python manage.py seed_crush_cache --reset --live   # start the hunt now

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
EVENT_TITLE_MINETTE = "🧭 [DEBUG] Fond-de-Gras Crush Cache \"Minette\""

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
        "alternatives": ["Industrial Revolution", "Bergbau", "Stahlindustrie", "Stahlzeitalter"],
        "hint_1": "Das Zeitalter von Eisen, Stahl und Dampf im 19. und 20. Jahrhundert.",
        "points": 150,
    },
]


class Command(BaseCommand):
    help = (
        "Seed a playable Crush Cache hunt (Luxembourg City or Fond-de-Gras Minette) "
        "for manual QA. Local-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--preset",
            choices=["lux_city", "minette"],
            default="lux_city",
            help="Which hunt preset to seed: 'lux_city' (default) or 'minette' (Fond-de-Gras).",
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

        preset = options["preset"]
        event_title = EVENT_TITLE_MINETTE if preset == "minette" else EVENT_TITLE_LUX
        stations_data = STATIONS_MINETTE if preset == "minette" else STATIONS_LUX_CITY

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
            self.stdout.write(f"Deleted the previous debug Crush Cache event '{event_title}'.")

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
        if preset == "minette":
            desc = "Debug scavenger hunt through Fond-de-Gras Minette mining area for QA."
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
            is_published=True,
        )

    def _create_hunt(self, event, coach, preset):
        if preset == "minette":
            title = "Crush Cache \"Minette\""
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
            team_size_max=4,
            allow_self_join=True,
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
                radius_meters=40,
                unlock_mode=s["unlock_mode"],
                completion_message="Nice — on to the next station!",
            )
            CacheChallenge.objects.create(
                station=station,
                challenge_order=1,
                challenge_type=s["challenge_type"],
                question=s["question"],
                correct_answer=s["answer"],
                alternative_answers=s["alternatives"],
                hint_1=s["hint_1"],
                points_awarded=s["points"],
                success_message="Correct!",
            )

    def _create_demo_team(self, hunt, preset):
        team_name = "Minette Miners" if preset == "minette" else "Explorers"
        return CacheTeam.objects.create(
            hunt=hunt, name=team_name, color="#3b82f6"
        )

    def _register_players(self, event):
        from allauth.account.models import EmailAddress

        for i in (1, 2):
            u, _ = User.objects.get_or_create(
                username=f"debug_cache_player{i}@crush.lu",
                defaults={"email": f"debug_cache_player{i}@crush.lu"},
            )

        debug_users = list(User.objects.filter(username__startswith="debug_"))

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

