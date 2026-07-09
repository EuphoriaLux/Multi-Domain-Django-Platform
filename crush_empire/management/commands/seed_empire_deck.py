"""
Seed the Crush Empire deck.

Idempotent: re-running updates existing cards by display_name rather than
duplicating them. `--reset` wipes the deck first.

Only English is authored here. DE and FR must be written natively in
crush-admin, not machine-translated: broken grammar as a warning sign does not
survive a translation pass, and a mistranslated tell teaches the wrong lesson.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from crush_empire.models import BioSegment, GameProfile

# (emoji, name, age, [segments]) — segments are (text, flag_type or None, explanation)
GENUINE = [
    ("🧔", "Marc", 29, [("Loves long walks… to the fridge.", None, "")]),
    ("👩‍🦰", "Sofia", 26, [("Fluent in sarcasm and three dead languages.", None, "")]),
    ("🧑‍🌾", "Luc", 34, [("Owns 14 plants. Two are still alive.", None, "")]),
    ("💃", "Elena", 31, [("Will judge your Spotify Wrapped.", None, "")]),
    ("🧑‍💻", "Ben", 28, [("Emotionally available 9am–9:03am.", None, "")]),
    ("🦸", "Nadia", 27, [("Red flags, but make it aesthetic.", None, "")]),
    ("🧑‍🍳", "Tom", 33, [("Makes one (1) very good pasta.", None, "")]),
    ("👸", "Lea", 24, [("Looking for someone to ignore together.", None, "")]),
    ("🧑‍🎤", "Rico", 30, [("Plays guitar. Only 'Wonderwall'.", None, "")]),
    ("🕵️", "Mira", 29, [("Will find your ex's new haircut in 4 minutes.", None, "")]),
    ("🧑‍🚀", "Jonas", 32, [("Ambitious. Also asleep by 9:30.", None, "")]),
    ("🧜", "Chloé", 25, [("Water sign. Emotionally, a monsoon.", None, "")]),
    ("🐶", "Bruno", 35, [("It's the dog's profile. He's a catch.", None, "")]),
    ("🧑‍⚖️", "Anouk", 28, [("Wins every argument, including this one.", None, "")]),
    ("🧑‍🔧", "Pit", 37, [("Can fix your car, not your feelings.", None, "")]),
    ("🧑‍🏫", "Isabelle", 31, [("Corrects your grammar. Lovingly. Mostly.", None, "")]),
    ("🎨", "Yann", 27, [("Paints. Badly. Enthusiastically.", None, "")]),
    ("🧗", "Kim", 30, [("Climbs rocks. Avoids feelings. Working on it.", None, "")]),
]

# Every flagged segment below is a documented romance-fraud pattern. The phrasing
# is comic; the tell is not.
SCAM = [
    (
        "🛢️", "Dimitri", 41,
        [
            ("Offshore oil rig engineer.", "unverifiable_job",
             "High-status jobs on inaccessible sites are a classic: impressive, well-paid, and impossible to check."),
            ("My camera has been broken for two years.", "never_video_calls",
             "A permanent excuse never to appear on video. Real people can video call."),
        ],
    ),
    (
        "💂", "General Hargrove", 52,
        [
            ("Peacekeeping deployment, location classified.", "unverifiable_job",
             "Military impersonation is one of the most common romance-scam personas."),
            ("Message me on WhatsApp, this app is monitored.", "off_platform",
             "Moving you off-platform removes the moderation and reporting that protect you."),
        ],
    ),
    (
        "💎", "Anastasia", 27,
        [
            ("Crypto analyst. I made 400% last month.", "crypto",
             "Unsolicited investment talk on a dating profile is the opening move of a pig-butchering scam."),
            ("I can show you the platform I use.", "money_request",
             "The 'platform' is theirs. Your deposit is the product."),
        ],
    ),
    (
        "👨‍⚕️", "Dr Alain", 46,
        [
            ("Surgeon with Médecins Sans Frontières.", "unverifiable_job",
             "Aid work abroad explains away every absence and every request for money."),
            ("I knew you were the one from your first message.", "love_bombing",
             "Overwhelming affection within hours is a manufactured bond, not chemistry."),
        ],
    ),
    (
        "🧳", "Elena", 33,
        [
            ("Stuck at customs, they are holding my luggage.", "urgency",
             "The manufactured emergency: a crisis that only money can solve, right now."),
            ("Could you send €200? I will repay you when I land.", "money_request",
             "Nobody you have never met should ever need your money. This is the ask the whole story was built for."),
        ],
    ),
    (
        "📸", "Lucas", 29,
        [
            ("Model and entrepreneur, currently in Dubai.", "unverifiable_job",
             "Glamour plus distance. You can never meet, and you can never check."),
            ("I am 29. I graduated in 1994.", "inconsistency",
             "The story does not add up. Scripts get reused; details slip."),
        ],
    ),
    (
        "💐", "Sébastien", 38,
        [
            ("Widowed. My late wife was my everything.", "love_bombing",
             "A tragic backstory arrives early to buy sympathy and discourage questions."),
            ("Delete this chat after reading, for privacy.", "off_platform",
             "Anything that destroys the evidence trail protects them, not you."),
        ],
    ),
    (
        "🎰", "Yulia", 24,
        [
            ("I need iTunes gift cards for my sick mother.", "money_request",
             "Gift cards are untraceable and irreversible. No hospital has ever accepted one."),
        ],
    ),
]


class Command(BaseCommand):
    help = "Seed the Crush Empire deck with genuine and scam profiles (EN only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="Delete existing profiles first."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            deleted, _ = GameProfile.objects.all().delete()
            self.stdout.write(f"deleted {deleted} rows")

        for emoji, name, age, segments in GENUINE:
            self._upsert(emoji, name, age, segments, is_scam=False)
        for emoji, name, age, segments in SCAM:
            self._upsert(emoji, name, age, segments, is_scam=True)

        genuine = GameProfile.objects.filter(is_scam=False).count()
        scam = GameProfile.objects.filter(is_scam=True).count()
        self.stdout.write(
            self.style.SUCCESS(f"deck: {genuine} genuine, {scam} scam")
        )
        self.stdout.write(
            "DE/FR bios are empty — author them in crush-admin, natively."
        )

    def _upsert(self, emoji, name, age, segments, is_scam):
        profile, _created = GameProfile.objects.update_or_create(
            display_name=name,
            is_scam=is_scam,
            defaults={"emoji": emoji, "age": age, "is_active": True},
        )
        profile.segments.all().delete()
        BioSegment.objects.bulk_create(
            BioSegment(
                profile=profile,
                order=i,
                text=text,
                is_red_flag=flag is not None,
                flag_type=flag or "",
                explanation=explanation,
            )
            for i, (text, flag, explanation) in enumerate(segments)
        )
