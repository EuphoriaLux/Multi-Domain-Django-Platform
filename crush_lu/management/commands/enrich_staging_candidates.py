"""
Enrich existing Crush Connect staging candidate profiles with high-resolution
photos, realistic biographies, Luxembourg locations, prompt answers, and
proper verification/consent gates for manual QA and coach testing.

Can enrich existing staging profiles in-place or create realistic missing
candidates up to a target count.

Usage:
    # Enrich all existing candidates with curated high-res photos & connect gates:
    python manage.py enrich_staging_candidates --all

    # Enrich existing candidates and ensure at least 16 realistic candidates exist:
    python manage.py enrich_staging_candidates --create-missing 16 --refresh-drops

    # Leave 3 candidates in pending review for testing Coach approval flows:
    python manage.py enrich_staging_candidates --leave-pending 3

    # Enrich a specific user:
    python manage.py enrich_staging_candidates --email connect_candidate_1@crush.lu
"""

import io
import logging
import random
import secrets
import requests
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_lu.models import CrushProfile, ProfileSubmission
from crush_lu.models.crush_connect import (
    CrushConnectMembership,
    CrushConnectWaitlist,
    SparkPrompt,
)
from crush_lu.models.crush_connect_questions import MemberGateQuestion
from crush_lu.models.profiles import CrushCoach, UserDataConsent
from crush_lu.services.crush_connect import (
    get_or_create_daily_drop,
    get_or_create_question_week,
)
from crush_lu.utils.image_processing import process_uploaded_image

try:
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    ALLAUTH_AVAILABLE = True
except ImportError:
    ALLAUTH_AVAILABLE = False

logger = logging.getLogger(__name__)
User = get_user_model()

# ============================================================================== #
# Curated High-Definition Photo & Persona Dataset (Diverse Luxembourg Candidates)
# ============================================================================== #

CURATED_FEMALE_CANDIDATES = [
    {
        "first_name": "Marie",
        "last_name": "Schmit",
        "age": 27,
        "location": "Luxembourg City",
        "bio": "Architect passionate about historical preservation, urban sketching, and finding the best espresso in Grund.",
        "interests": "architecture, specialty coffee, jazz vinyl, photography, cycling",
        "story_prompt": "My ideal Sunday in Luxembourg...",
        "story_answer": "Morning flat white in Grund, a walk through Mudam, and fresh pastries with friends.",
        "relationship_goal": "serious",
        "lifestyle_energy": "mix",
        "weekend_vibe": "Exploring local art exhibitions and cooking dinner.",
        "photos": [
            "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=800&auto=format&fit=crop&q=80",  # Portrait
            "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=800&auto=format&fit=crop&q=80",  # Cafe / Sketching
            "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=800&auto=format&fit=crop&q=80",  # Outdoor
        ],
    },
    {
        "first_name": "Sophie",
        "last_name": "Weber",
        "age": 29,
        "location": "Esch-sur-Alzette",
        "bio": "UX designer in Belval. Outdoor enthusiast, bouldering regular, and always up for an impromptu road trip.",
        "interests": "bouldering, indie films, ceramics, live music, trail running",
        "story_prompt": "A skill I want to learn next...",
        "story_answer": "Throwing pottery on a wheel without making a complete mess of the studio.",
        "relationship_goal": "serious",
        "lifestyle_energy": "active",
        "weekend_vibe": "Morning bouldering session followed by a cinema night.",
        "photos": [
            "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=800&auto=format&fit=crop&q=80",  # Portrait
            "https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?w=800&auto=format&fit=crop&q=80",  # Bouldering / Active
            "https://images.unsplash.com/photo-1488426862026-3ee34a7d66df?w=800&auto=format&fit=crop&q=80",  # Travel
        ],
    },
    {
        "first_name": "Camille",
        "last_name": "Muller",
        "age": 26,
        "location": "Echternach",
        "bio": "Environmental scientist who spends weekends trail running in Mullerthal and trying ambitious sourdough recipes.",
        "interests": "trail running, botany, sourdough baking, camping, podcasts",
        "story_prompt": "The best travel story I have...",
        "story_answer": "Wild camping under the stars near the Swiss Alps after a 20km ridge hike.",
        "relationship_goal": "open",
        "lifestyle_energy": "active",
        "weekend_vibe": "Hiking the Mullerthal trail with my backpack and camera.",
        "photos": [
            "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=800&auto=format&fit=crop&q=80",  # Portrait
            "https://images.unsplash.com/photo-1517457373958-b7bdd4587205?w=800&auto=format&fit=crop&q=80",  # Hiking
            "https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?w=800&auto=format&fit=crop&q=80",  # Outdoor
        ],
    },
    {
        "first_name": "Lea",
        "last_name": "Wagner",
        "age": 31,
        "location": "Luxembourg City",
        "bio": "Corporate counsel on Kirchberg. Natural wine lover, amateur tennis player, and avid reader of contemporary fiction.",
        "interests": "tennis, natural wine, contemporary art, French cinema, literature",
        "story_prompt": "My non-negotiable comfort food...",
        "story_answer": "Fresh burrata with heirloom tomatoes and crusty sourdough baguette.",
        "relationship_goal": "serious",
        "lifestyle_energy": "mix",
        "weekend_vibe": "Tennis match Saturday morning and dinner with friends at a wine bar.",
        "photos": [
            "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=800&auto=format&fit=crop&q=80",  # Portrait
            "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=800&auto=format&fit=crop&q=80",  # Tennis / Social
            "https://images.unsplash.com/photo-1502685104226-ee32379fefbe?w=800&auto=format&fit=crop&q=80",  # Lifestyle
        ],
    },
    {
        "first_name": "Charlotte",
        "last_name": "Klein",
        "age": 28,
        "location": "Mersch",
        "bio": "Software engineer with a passion for classical piano, board game marathons, and mountain hiking.",
        "interests": "piano, board games, sci-fi, hiking, specialty tea",
        "story_prompt": "What in my profile made you curious?",
        "story_answer": "I can play Chopin and beat you at Catan in the same afternoon.",
        "relationship_goal": "open",
        "lifestyle_energy": "homebody",
        "weekend_vibe": "Cozy board game evening with tea and homemade crumble.",
        "photos": [
            "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=800&auto=format&fit=crop&q=80",
        ],
    },
    {
        "first_name": "Laura",
        "last_name": "Hoffmann",
        "age": 25,
        "location": "Differdange",
        "bio": "Graphic designer, analog photographer, and vintage furniture hunter. Big fan of indie rock concerts.",
        "interests": "illustration, film photography, thrifting, vinyl, indie rock",
        "story_prompt": "My ideal Sunday in Luxembourg...",
        "story_answer": "Flea market browsing followed by developing 35mm film in my makeshift darkroom.",
        "relationship_goal": "curious",
        "lifestyle_energy": "mix",
        "weekend_vibe": "Concerts at Rockhal and thrifting vintage treasures.",
        "photos": [
            "https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=800&auto=format&fit=crop&q=80",
        ],
    },
    {
        "first_name": "Julie",
        "last_name": "Da Silva",
        "age": 30,
        "location": "Remich",
        "bio": "Moselle valley winemaker & sommelier. Love vineyard walks, sunset river cruises, and Mediterranean cooking.",
        "interests": "wine tasting, gastronomy, paddleboarding, travel, running",
        "story_prompt": "What in my profile made you curious?",
        "story_answer": "Ask me which local Riesling matches your favorite comfort food.",
        "relationship_goal": "serious",
        "lifestyle_energy": "active",
        "weekend_vibe": "Harvest tasting in Remich and sunset walk by the Moselle.",
        "photos": [
            "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1508214751196-bcfd4ca60f91?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1488426862026-3ee34a7d66df?w=800&auto=format&fit=crop&q=80",
        ],
    },
    {
        "first_name": "Emma",
        "last_name": "Bernard",
        "age": 28,
        "location": "Dudelange",
        "bio": "Translator fluent in 4 languages. Passionate about world literature, yoga teacher-in-training, and travel.",
        "interests": "literature, yoga, languages, travel, vegetarian cooking",
        "story_prompt": "A skill I want to learn next...",
        "story_answer": "Conversational Portuguese and mastering headstands in yoga class.",
        "relationship_goal": "serious",
        "lifestyle_energy": "homebody",
        "weekend_vibe": "Morning Vinyasa yoga and an afternoon lost in a bookstore.",
        "photos": [
            "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=800&auto=format&fit=crop&q=80",
        ],
    },
]

CURATED_MALE_CANDIDATES = [
    {
        "first_name": "Thomas",
        "last_name": "Dupont",
        "age": 29,
        "location": "Luxembourg City",
        "bio": "Financial analyst and trail runner. Big fan of specialty pour-over coffee, vinyl collecting, and weekend bike tours.",
        "interests": "trail running, specialty coffee, jazz vinyl, cycling, hiking",
        "story_prompt": "My ideal Sunday in Luxembourg...",
        "story_answer": "Early morning 15k run in Bambësch forest, followed by brunch in Limpertsberg.",
        "relationship_goal": "serious",
        "lifestyle_energy": "active",
        "weekend_vibe": "Long run in the morning, listening to records with good coffee in the afternoon.",
        "photos": [
            "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=800&auto=format&fit=crop&q=80",  # Portrait
            "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=800&auto=format&fit=crop&q=80",  # Running / Coffee
            "https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=800&auto=format&fit=crop&q=80",  # Outdoor
        ],
    },
    {
        "first_name": "Lucas",
        "last_name": "Martin",
        "age": 31,
        "location": "Esch-sur-Alzette",
        "bio": "Product manager at a tech startup in Belval. Avid boulderer, amateur chef, and board game geek.",
        "interests": "bouldering, cooking, board games, cinema, tech",
        "story_prompt": "My non-negotiable comfort food...",
        "story_answer": "Homemade hand-pulled noodles with chili oil and braised beef.",
        "relationship_goal": "serious",
        "lifestyle_energy": "mix",
        "weekend_vibe": "Bouldering session at the gym followed by hosting a dinner party.",
        "photos": [
            "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=800&auto=format&fit=crop&q=80",  # Portrait
            "https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=800&auto=format&fit=crop&q=80",  # Chef / Lifestyle
            "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=800&auto=format&fit=crop&q=80",  # Travel
        ],
    },
    {
        "first_name": "Nicolas",
        "last_name": "Ferreira",
        "age": 28,
        "location": "Luxembourg City",
        "bio": "Landscape architect living in Grund. Passionate about urban greening, botanical gardens, and craft beer brewing.",
        "interests": "architecture, urban sketching, craft beer, botany, hiking",
        "story_prompt": "What in my profile made you curious?",
        "story_answer": "I know every secret viewpoint and hidden garden in the Ville-Haute.",
        "relationship_goal": "open",
        "lifestyle_energy": "mix",
        "weekend_vibe": "Sketching old facades in Grund and visiting craft breweries.",
        "photos": [
            "https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=800&auto=format&fit=crop&q=80",  # Portrait
            "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=800&auto=format&fit=crop&q=80",  # Sketching
            "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=800&auto=format&fit=crop&q=80",  # Outdoor
        ],
    },
    {
        "first_name": "Alexandre",
        "last_name": "Santos",
        "age": 33,
        "location": "Strassen",
        "bio": "Data scientist who loves playing jazz piano, mountain biking in the Ardennes, and espresso brewing.",
        "interests": "piano, jazz, mountain biking, espresso, astronomy",
        "story_prompt": "The best travel story I have...",
        "story_answer": "Watching the Perseid meteor shower from a remote observatory in Chile.",
        "relationship_goal": "serious",
        "lifestyle_energy": "active",
        "weekend_vibe": "Mountain biking through Ardennes trails and jamming on the piano.",
        "photos": [
            "https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=800&auto=format&fit=crop&q=80",  # Portrait
            "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=800&auto=format&fit=crop&q=80",  # Lifestyle
            "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=800&auto=format&fit=crop&q=80",  # Travel
        ],
    },
    {
        "first_name": "Julien",
        "last_name": "Wagner",
        "age": 27,
        "location": "Echternach",
        "bio": "Photographer and trail guide. Passionate about nature conservation, wild foraging, and outdoor cooking.",
        "interests": "photography, camping, foraging, cycling, documentaries",
        "story_prompt": "A skill I want to learn next...",
        "story_answer": "Mushroom foraging identification without needing my field guide on every step.",
        "relationship_goal": "open",
        "lifestyle_energy": "active",
        "weekend_vibe": "Forest camping and campfire cooking with friends.",
        "photos": [
            "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=800&auto=format&fit=crop&q=80",
        ],
    },
    {
        "first_name": "Maxime",
        "last_name": "Meyer",
        "age": 30,
        "location": "Dudelange",
        "bio": "Civil engineer & amateur woodworker. Love building handcrafted furniture, running marathons, and good coffee.",
        "interests": "woodworking, running, craft beer, architecture, design",
        "story_prompt": "My ideal Sunday in Luxembourg...",
        "story_answer": "Finishing a dining table in my workshop, then a relaxed run in the afternoon sun.",
        "relationship_goal": "serious",
        "lifestyle_energy": "mix",
        "weekend_vibe": "Hands-on woodworking projects and relaxing with a craft beer.",
        "photos": [
            "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=800&auto=format&fit=crop&q=80",
        ],
    },
    {
        "first_name": "David",
        "last_name": "Costa",
        "age": 32,
        "location": "Mamer",
        "bio": "Hospital physician with a passion for tennis, Mediterranean cuisine, and road cycling.",
        "interests": "tennis, cycling, cooking, history, travel",
        "story_prompt": "My non-negotiable comfort food...",
        "story_answer": "Traditional Portuguese Bacalhau à Brás with a chilled Vinho Verde.",
        "relationship_goal": "serious",
        "lifestyle_energy": "active",
        "weekend_vibe": "Tennis tournament in the morning and dinner with friends in the evening.",
        "photos": [
            "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=800&auto=format&fit=crop&q=80",
        ],
    },
    {
        "first_name": "Antoine",
        "last_name": "Bernard",
        "age": 26,
        "location": "Differdange",
        "bio": "Sound engineer and electronic music producer. Love indie game soundtracks, vinyl digging, and bouldering.",
        "interests": "music production, sound design, bouldering, vinyl, video games",
        "story_prompt": "What in my profile made you curious?",
        "story_answer": "I can compose a personalized synth theme song for your morning coffee routine.",
        "relationship_goal": "curious",
        "lifestyle_energy": "homebody",
        "weekend_vibe": "Studio recording sessions and late-night listening parties.",
        "photos": [
            "https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=800&auto=format&fit=crop&q=80",
            "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=800&auto=format&fit=crop&q=80",
        ],
    },
]


def _dob_for_age(target_age: int) -> date:
    today = date.today()
    try:
        return today.replace(year=today.year - target_age)
    except ValueError:
        return today.replace(year=today.year - target_age, day=28)


def _fetch_or_fallback_image(url: str, fallback_label: str) -> bytes:
    """Download image bytes from Unsplash or fallback to an in-memory test JPEG."""
    try:
        resp = requests.get(url, timeout=12)
        if resp.status_code == 200 and len(resp.content) > 1024:
            return resp.content
    except Exception as exc:
        logger.warning("Could not download %s: %s — generating fallback JPEG", url, exc)

    # Generate a lightweight colored JPEG fallback if network is unreachable
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (600, 750), color=(68, 70, 84))
        draw = ImageDraw.Draw(img)
        draw.text((200, 350), fallback_label, fill=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"


class Command(BaseCommand):
    help = (
        "Enrich existing staging candidate profiles with curated high-resolution "
        "photos, Luxembourg bios, prompt answers, and matching gates."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Enrich all existing candidate/test profiles in the database",
        )
        parser.add_argument(
            "--include-any-profile",
            action="store_true",
            help=(
                "With --all, if no username/email matches the candidate naming "
                "convention, also allow falling back to every non-staff user "
                "with a CrushProfile. Off by default -- on a live-reachable "
                "site this would silently overwrite real testers' bio/photos/"
                "DOB/verification and reset their password. Only pass this on "
                "a database you know holds nothing but seeded candidates."
            ),
        )
        parser.add_argument(
            "--create-missing",
            type=int,
            default=0,
            help="Ensure at least N curated candidate profiles exist (creates missing if fewer)",
        )
        parser.add_argument(
            "--email",
            type=str,
            help="Enrich a specific user by email",
        )
        parser.add_argument(
            "--leave-pending",
            type=int,
            default=0,
            help="Leave N profiles in pending review state so coaches can test approval flows",
        )
        parser.add_argument(
            "--refresh-drops",
            action="store_true",
            help="Regenerate today's daily drops for all receiver accounts",
        )
        parser.add_argument(
            "--password",
            default=None,
            help=(
                "Password for created/reset accounts. Omit to generate a "
                "random one per run (printed once at the end) -- a fixed "
                "default would be a predictable, publicly-visible credential "
                "for every seeded account on a live-reachable site."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without updating the database or blob storage",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY-RUN MODE — no changes will be saved")
            )

        password = options["password"]
        password_was_generated = password is None
        if password_was_generated:
            # A fixed default would be a predictable, publicly-visible
            # credential (this file is in the OSS repo) for every seeded
            # account on a live-reachable site -- generate one per run
            # instead and print it once, below.
            password = secrets.token_urlsafe(12)
        options["password"] = password

        coach = CrushCoach.objects.filter(is_active=True).first()
        if not coach and not dry_run:
            self.stdout.write("No active CrushCoach found — creating default coach...")
            from django.core.management import call_command

            call_command("create_crush_coaches")
            coach = CrushCoach.objects.filter(is_active=True).first()

        prompt = SparkPrompt.objects.filter(is_active=True).first()
        if not prompt and not dry_run:
            prompt, _ = SparkPrompt.objects.get_or_create(
                text="My ideal Sunday in Luxembourg...",
                defaults={"is_active": True, "weight": 1},
            )

        # 1. Identify target candidates
        targets = []
        if options["email"]:
            user = User.objects.filter(email__iexact=options["email"]).first()
            if not user:
                raise CommandError(f"User not found for email: {options['email']}")
            targets = [user]
        elif options["all"]:
            targets = list(
                User.objects.filter(username__startswith="connect_candidate_")
                | User.objects.filter(username__startswith="debug_cand_")
                | User.objects.filter(email__contains="candidate")
            )
            if not targets and options["include_any_profile"]:
                # Opt-in only: every non-staff user with a CrushProfile. On a
                # live-reachable site this would silently overwrite real
                # testers' data and reset their password, so it never fires
                # unless explicitly requested.
                self.stdout.write(
                    self.style.WARNING(
                        "No candidate-named profiles found -- --include-any-profile "
                        "is set, falling back to ALL non-staff CrushProfile users."
                    )
                )
                targets = list(
                    User.objects.filter(is_staff=False, crushprofile__isnull=False)
                )
            elif not targets:
                self.stdout.write(
                    self.style.WARNING(
                        "No candidate-named profiles found. Pass --include-any-profile "
                        "to fall back to every non-staff CrushProfile user (only safe "
                        "on a database that holds nothing but seeded candidates)."
                    )
                )

        self.stdout.write(
            f"Identified {len(targets)} existing candidate profiles to enrich."
        )

        # 2. Enrich existing candidates
        enriched_count = 0
        leave_pending_budget = options["leave_pending"]

        all_personas = CURATED_FEMALE_CANDIDATES + CURATED_MALE_CANDIDATES
        random.shuffle(all_personas)

        for i, user in enumerate(targets):
            persona = all_personas[i % len(all_personas)]
            should_leave_pending = leave_pending_budget > 0
            if should_leave_pending:
                leave_pending_budget -= 1

            self._enrich_user(
                user=user,
                persona=persona,
                coach=coach,
                default_prompt=prompt,
                leave_pending=should_leave_pending,
                dry_run=dry_run,
                password=options["password"],
            )
            enriched_count += 1

        # 3. Create missing candidates if requested
        create_target = options["create_missing"]
        if create_target > len(targets) and not dry_run:
            missing_count = create_target - len(targets)
            self.stdout.write(
                f"\nCreating {missing_count} missing curated candidates..."
            )
            start_index = len(targets) + 1

            for idx in range(missing_count):
                persona_idx = (len(targets) + idx) % len(all_personas)
                persona = all_personas[persona_idx]
                username = f"connect_candidate_{start_index + idx}"
                email = f"{username}@crush.lu"

                existing = User.objects.filter(username=username).first()
                if not existing:
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=options["password"],
                        first_name=persona["first_name"],
                        last_name=persona["last_name"],
                    )
                else:
                    user = existing

                should_leave_pending = leave_pending_budget > 0
                if should_leave_pending:
                    leave_pending_budget -= 1

                self._enrich_user(
                    user=user,
                    persona=persona,
                    coach=coach,
                    default_prompt=prompt,
                    leave_pending=should_leave_pending,
                    dry_run=dry_run,
                    password=options["password"],
                )
                enriched_count += 1

        # 4. Refresh Daily Drops if requested
        if options["refresh_drops"] and not dry_run:
            self.stdout.write(
                "\nRefreshing Daily Drops for Premium receiver accounts..."
            )
            receivers = User.objects.filter(
                username__startswith="connect_premium_"
            ) | User.objects.filter(username="debug_receiver")
            for receiver in receivers:
                try:
                    drop = get_or_create_daily_drop(receiver)
                    cards = list(drop.recipients.values_list("email", flat=True))
                    self.stdout.write(
                        f"  Drop for {receiver.email}: {len(cards)} candidates ({', '.join(cards)})"
                    )
                except Exception as exc:
                    self.stderr.write(f"  Drop failed for {receiver.email}: {exc}")

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Successfully enriched {enriched_count} candidates on staging!"
            )
        )
        if password_was_generated and not dry_run:
            self.stdout.write(
                self.style.WARNING(f"🔑 Generated password for this run: {password}")
            )
        self.stdout.write("=" * 60)

    def _enrich_user(
        self,
        *,
        user: User,
        persona: dict,
        coach: CrushCoach,
        default_prompt: SparkPrompt,
        leave_pending: bool,
        dry_run: bool,
        password: str,
    ):
        if dry_run:
            self.stdout.write(
                f"  [DRY-RUN] Would enrich: {user.email} as {persona['first_name']} {persona['last_name']}"
            )
            return

        self.stdout.write(
            f"  Enriching {user.email} -> {persona['first_name']} {persona['last_name']} (age {persona['age']}, {persona['location']})..."
        )

        now = timezone.now()
        gender_code = "F" if persona in CURATED_FEMALE_CANDIDATES else "M"

        # Update User basics
        user.first_name = persona["first_name"]
        user.last_name = persona["last_name"]
        user.last_login = now
        user.set_password(password)
        user.save()

        # Allauth Email verification
        if ALLAUTH_AVAILABLE:
            EmailAddress.objects.update_or_create(
                user=user,
                email=user.email,
                defaults={"verified": True, "primary": True},
            )
            SocialAccount.objects.get_or_create(
                user=user,
                provider="luxid",
                defaults={
                    "uid": f"luxid-test-{user.pk}",
                    "extra_data": {"sub": f"luxid-test-{user.pk}"},
                },
            )

        # GDPR / Data Consent
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"crushlu_consent_given": True, "crushlu_consent_date": now},
        )

        # CrushProfile
        profile, _ = CrushProfile.objects.get_or_create(user=user)
        profile.date_of_birth = _dob_for_age(persona["age"])
        profile.gender = gender_code
        profile.location = persona["location"]
        profile.bio = persona["bio"]
        profile.interests = persona["interests"]
        profile.phone_number = (
            f"+352 661 {random.randint(100, 999)} {random.randint(100, 999)}"
        )
        profile.phone_verified = True
        profile.phone_verified_at = now
        profile.show_full_name = True
        profile.show_exact_age = True
        profile.preferred_language = "en"
        profile.preferred_age_min = 18
        profile.preferred_age_max = 99
        profile.preferred_genders = []
        profile.completion_status = "submitted"
        profile.welcome_seen_at = now
        profile.coach_intro_seen_at = now

        if leave_pending:
            profile.is_approved = False
            profile.verification_status = "pending"
            profile.verification_method = "coach"
        else:
            profile.is_approved = True
            profile.approved_at = now
            profile.verification_status = "verified"
            profile.verification_method = "admin"

        # Download and attach high-res photos (photo_1, photo_2, photo_3)
        for idx, photo_url in enumerate(persona["photos"][:3], start=1):
            field_name = f"photo_{idx}"
            label = f"{persona['first_name']} {idx}"
            photo_bytes = _fetch_or_fallback_image(photo_url, label)
            if photo_bytes:
                try:
                    processed = process_uploaded_image(
                        io.BytesIO(photo_bytes), filename=f"{user.pk}_photo{idx}.jpg"
                    )
                    field = getattr(profile, field_name)
                    field.save(f"{user.pk}_photo{idx}.jpg", processed, save=False)
                except Exception as exc:
                    self.stderr.write(
                        f"    Warning: photo_{idx} save failed for {user.email}: {exc}"
                    )

        profile.save()

        # Connect Membership
        prompt_obj = (
            SparkPrompt.objects.filter(
                text__icontains=persona.get("story_prompt", "")[:15]
            ).first()
            or default_prompt
        )

        membership, _ = CrushConnectMembership.objects.update_or_create(
            user=user,
            defaults={
                "onboarded_at": now,
                "photo_share_consent": True,
                "story_prompt": prompt_obj,
                "story_answer": persona["story_answer"],
                "relationship_goal": persona["relationship_goal"],
                "lifestyle_energy": persona["lifestyle_energy"],
                "lifestyle_social": "flexible",
                "lifestyle_pace": "balanced",
                "excluded_by_coach": False,
            },
        )

        # Gate questions
        try:
            week = get_or_create_question_week()
            questions = list(week.questions.filter(is_active=True)[:3])
            for position, question in enumerate(questions, start=1):
                MemberGateQuestion.objects.update_or_create(
                    membership=membership,
                    position=position,
                    defaults={
                        "question": question,
                        "owner_answer": (position % 2 == 1),
                        "picked_week": week,
                    },
                )
        except Exception as exc:
            logger.warning("Could not set gate questions for %s: %s", user.email, exc)

        # Waitlist tester status
        CrushConnectWaitlist.objects.update_or_create(
            user=user,
            defaults={"selected_as_tester": True, "selected_at": now},
        )

        # Profile Submission for Coach
        ProfileSubmission.objects.update_or_create(
            profile=profile,
            defaults={
                "status": "pending" if leave_pending else "approved",
                "coach": coach,
                "coach_notes": "Enriched staging candidate for QA testing",
                "review_call_completed": not leave_pending,
                "review_call_date": now if not leave_pending else None,
                "reviewed_at": now if not leave_pending else None,
            },
        )
