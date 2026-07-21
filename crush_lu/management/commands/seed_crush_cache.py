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

# Fond-de-Gras Geo Crush Cache "Minette" (6 stations based on exact coordinates)
STATIONS_MINETTE = [
    {
        "order": 1,
        "name": "Gare de Fond-de-Gras (Train 1900)",
        "lat": "49.532894",
        "lng": "5.858759",
        "unlock_mode": "gps",
        "intro": "Welcome to Fond-de-Gras station! Start your hunt near the historic Train 1900 platform.",
        "challenge_type": "open_text",
        "question": "What type of red iron-ore was historically extracted in this southern Luxembourg region?",
        "answer": "Minette",
        "alternatives": ["Iron ore", "Eisenerz", "Minette ore", "iron"],
        "hint_1": "It gives the region and the soil its characteristic reddish color.",
        "points": 100,
    },
    {
        "order": 2,
        "name": "Sentier des Minières (Mining Track Line)",
        "lat": "49.532948",
        "lng": "5.853189",
        "unlock_mode": "gps",
        "intro": "Follow the narrow gauge railway tracks along the mining valley.",
        "challenge_type": "open_text",
        "question": "What is the name of the steam train association operating historic trains in Fond-de-Gras?",
        "answer": "Train 1900",
        "alternatives": ["Train1900", "AMTF", "AMTF Train 1900"],
        "hint_1": "It includes a historic year in its name (1900).",
        "points": 100,
    },
    {
        "order": 3,
        "name": "Galerie Mine Doenn",
        "lat": "49.533691",
        "lng": "5.850556",
        "unlock_mode": "gps",
        "intro": "Approach the historic underground mine gallery entrance.",
        "challenge_type": "open_text",
        "question": "What traditional mine cart was used by miners to haul ore out of the tunnels?",
        "answer": "Hont",
        "alternatives": ["Hunt", "Wagon", "Mine cart", "Lore", "Grubenwagen"],
        "hint_1": "In Luxembourgish/German mining terminology, it is a small iron mine wagon.",
        "points": 100,
    },
    {
        "order": 4,
        "name": "Réserve Naturelle Giele Botter Trail",
        "lat": "49.534649",
        "lng": "5.847993",
        "unlock_mode": "gps",
        "intro": "Walk up towards the former open-cast mine transformed into a rich nature reserve.",
        "challenge_type": "open_text",
        "question": "What does the Luxembourgish name 'Giele Botter' translate to in English?",
        "answer": "Yellow Butter",
        "alternatives": ["Butter", "Yellow butter", "Giele botter"],
        "hint_1": "It is named after the yellow-colored clay/soil ('Yellow Butter').",
        "points": 100,
    },
    {
        "order": 5,
        "name": "Viktoriastoll & Mine Heritage",
        "lat": "49.529388",
        "lng": "5.857374",
        "unlock_mode": "gps",
        "intro": "Discover the entrance to the Viktoriastoll adit.",
        "challenge_type": "open_text",
        "question": "In which municipality in southern Luxembourg is Fond-de-Gras situated?",
        "answer": "Differdange",
        "alternatives": ["Differdingen", "Pétange", "Petange"],
        "hint_1": "The third largest city in Luxembourg.",
        "points": 100,
    },
    {
        "order": 6,
        "name": "Hall Paul Wurth & Épicerie Ancienne (Schluss)",
        "lat": "49.533819",
        "lng": "5.863352",
        "unlock_mode": "gps_qr",
        "intro": "Reach the final station (Schluss) near the historic Paul Wurth hall and grocery shop. Scan the QR code or enter the code to finish!",
        "challenge_type": "riddle",
        "question": "Congratulations on completing the Minette hunt! What major industrial era shaped this valley?",
        "answer": "Industrial Revolution",
        "alternatives": ["Iron industry", "Mining era", "Steel industry", "Steel age"],
        "hint_1": "The era of iron, steel, and steam in the 19th-20th century.",
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
        debug_users = list(User.objects.filter(username__startswith="debug_"))
        if not debug_users:
            debug_users = []
            for i in (1, 2):
                u, _ = User.objects.get_or_create(
                    username=f"debug_cache_player{i}@crush.lu",
                    defaults={"email": f"debug_cache_player{i}@crush.lu"},
                )
                debug_users.append(u)

        registered = []
        for user in debug_users:
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

