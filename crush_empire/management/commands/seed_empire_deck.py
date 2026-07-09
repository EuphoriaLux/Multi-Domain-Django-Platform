"""
Seed the Crush Empire deck.

Idempotent: re-running updates existing cards by display_name rather than
duplicating them. `--reset` wipes the deck first.

Every card carries three segments. On a scam card the tells hide among innocent
lines, which is what makes the tier-2 modal a puzzle rather than a formality —
and it is why the innocent lines must be genuinely innocuous. If the filler on a
scam card reads as suspicious, tapping it is a false positive for a reason the
player cannot see, and the game has taught them something false.

Only English is authored here. DE and FR must be written natively in
crush-admin, not machine-translated: broken grammar as a warning sign does not
survive a translation pass, and a mistranslated tell teaches the wrong lesson.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from crush_empire.economy import TIER2_MIN_SEGMENTS
from crush_empire.models import BioSegment, GameProfile

# (emoji, name, age, [(text, flag_type|None, explanation)])
GENUINE = [
    ("🧔", "Marc", 29, [
        ("Loves long walks… to the fridge.", None, ""),
        ("Teaches maths in Esch.", None, ""),
        ("Will absolutely video call, badly lit.", None, ""),
    ]),
    ("👩‍🦰", "Sofia", 26, [
        ("Fluent in sarcasm and three dead languages.", None, ""),
        ("Translator. Yes, really. You can check.", None, ""),
        ("Coffee first, opinions second.", None, ""),
    ]),
    ("🧑‍🌾", "Luc", 34, [
        ("Owns 14 plants. Two are still alive.", None, ""),
        ("Landscape gardener, mostly in Bertrange.", None, ""),
        ("Free most Saturdays if you want to meet.", None, ""),
    ]),
    ("💃", "Elena", 31, [
        ("Will judge your Spotify Wrapped.", None, ""),
        ("Nurse at the CHL. Nights, sadly.", None, ""),
        ("Terrible at texting. Better in person.", None, ""),
    ]),
    ("🧑‍💻", "Ben", 28, [
        ("Emotionally available 9am–9:03am.", None, ""),
        ("Writes software. Complains about software.", None, ""),
        ("Ask me about my sourdough. Please don't.", None, ""),
    ]),
    ("🦸", "Nadia", 27, [
        ("Red flags, but make it aesthetic.", None, ""),
        ("Graphic designer, freelance, chaotic.", None, ""),
        ("My dog has more followers than me.", None, ""),
    ]),
    ("🧑‍🍳", "Tom", 33, [
        ("Makes one (1) very good pasta.", None, ""),
        ("Chef. Yes, I cook on days off too.", None, ""),
        ("Happy to meet somewhere public first.", None, ""),
    ]),
    ("👸", "Lea", 24, [
        ("Looking for someone to ignore together.", None, ""),
        ("Law student. Ask me nothing about law.", None, ""),
        ("I split the bill. Always.", None, ""),
    ]),
    ("🧑‍🎤", "Rico", 30, [
        ("Plays guitar. Only 'Wonderwall'.", None, ""),
        ("Sound engineer at Rockhal.", None, ""),
        ("I promise the singing stops eventually.", None, ""),
    ]),
    ("🕵️", "Mira", 29, [
        ("Will find your ex's new haircut in 4 minutes.", None, ""),
        ("Librarian. It's mostly databases.", None, ""),
        ("Video call before meeting? Absolutely.", None, ""),
    ]),
    ("🧑‍🚀", "Jonas", 32, [
        ("Ambitious. Also asleep by 9:30.", None, ""),
        ("Physiotherapist in Differdange.", None, ""),
        ("Coffee this week? I'm around.", None, ""),
    ]),
    ("🧜", "Chloé", 25, [
        ("Water sign. Emotionally, a monsoon.", None, ""),
        ("Swimming instructor. The irony is noted.", None, ""),
        ("I'll meet you at the pool. Obviously.", None, ""),
    ]),
    ("🧑‍⚖️", "Anouk", 28, [
        ("Wins every argument, including this one.", None, ""),
        ("Notary's assistant. Thrilling stuff.", None, ""),
        ("Long chats, short walks, real dates.", None, ""),
    ]),
    ("🧑‍🔧", "Pit", 37, [
        ("Can fix your car, not your feelings.", None, ""),
        ("Mechanic, garage near the station.", None, ""),
        ("Come by, I'm there most days.", None, ""),
    ]),
    ("🧗", "Kim", 30, [
        ("Climbs rocks. Avoids feelings. Working on it.", None, ""),
        ("Architect. Draws boxes for money.", None, ""),
        ("Let's meet at the climbing gym.", None, ""),
    ]),
]

# Every flagged line below is a documented romance-fraud pattern. The unflagged
# lines are deliberately ordinary: a player who taps them has made a real mistake.
SCAM = [
    ("🛢️", "Dimitri", 41, [
        ("Offshore oil rig engineer, six weeks on, two off.", "unverifiable_job",
         "High-status jobs on inaccessible sites are a classic: impressive, well paid, and impossible to check."),
        ("I love classical music and long conversations.", None, ""),
        ("My camera has been broken for two years.", "never_video_calls",
         "A permanent excuse never to appear on video. Real people can video call."),
    ]),
    ("💂", "Hargrove", 52, [
        ("Peacekeeping deployment, location classified.", "unverifiable_job",
         "Military impersonation is one of the most common romance-scam personas."),
        ("Two grown children. I miss them daily.", None, ""),
        ("Message me on WhatsApp, this app is monitored.", "off_platform",
         "Moving you off-platform removes the moderation and reporting that protect you."),
    ]),
    ("💎", "Anastasia", 27, [
        ("Crypto analyst. I made 400% last month.", "crypto",
         "Unsolicited investment talk on a dating profile is the opening move of a pig-butchering scam."),
        ("I like hiking and bad reality television.", None, ""),
        ("I can show you the platform I use.", "money_request",
         "The 'platform' is theirs. Your deposit is the product."),
    ]),
    ("👨‍⚕️", "Dr Alain", 46, [
        ("Surgeon with a medical charity, currently in Yemen.", "unverifiable_job",
         "Aid work abroad explains away every absence and every request for money."),
        ("I knew you were the one from your first message.", "love_bombing",
         "Overwhelming affection within hours is a manufactured bond, not chemistry."),
        ("I read too much and sleep too little.", None, ""),
    ]),
    ("🧳", "Elenora", 33, [
        ("Interior designer, travelling for a project.", None, ""),
        ("Stuck at customs, they are holding my luggage.", "urgency",
         "The manufactured emergency: a crisis that only money can solve, right now."),
        ("Could you send €200? I'll repay you when I land.", "money_request",
         "Nobody you have never met should ever need your money. This is the ask the whole story was built for."),
    ]),
    ("📸", "Lucas", 29, [
        ("Model and entrepreneur, currently in Dubai.", "unverifiable_job",
         "Glamour plus distance. You can never meet, and you can never check."),
        ("Dogs over cats. I will not be debating this.", None, ""),
        ("I'm 29. I graduated university in 1994.", "inconsistency",
         "The story does not add up. Scripts get reused; details slip."),
    ]),
    ("💐", "Sébastien", 38, [
        ("Widowed. My late wife was my everything.", "love_bombing",
         "A tragic backstory arrives early to buy sympathy and to discourage questions."),
        ("I run a small import business.", None, ""),
        ("Delete this chat after reading, for privacy.", "off_platform",
         "Anything that destroys the evidence trail protects them, not you."),
    ]),
    ("🎰", "Yulia", 24, [
        ("Studying nursing. Almost qualified.", None, ""),
        ("My mother is ill and the hospital wants payment.", "urgency",
         "The emergency is always urgent, always unverifiable, and always yours to solve."),
        ("Send iTunes gift cards, it's faster than a transfer.", "money_request",
         "Gift cards are untraceable and irreversible. No hospital has ever accepted one."),
    ]),
    ("⚓", "Captain Ray", 49, [
        ("Merchant navy. At sea most of the year.", "unverifiable_job",
         "At sea, unreachable, and conveniently unable to meet — for months."),
        ("I cook a passable curry when I'm ashore.", None, ""),
        ("My daughter's school fees are due and my card is frozen.", "money_request",
         "A sympathetic third party — a child, a mother — makes the ask harder to refuse."),
    ]),
    ("🌹", "Marcus", 36, [
        ("Architect. I design hotels nobody can afford.", None, ""),
        ("You are my soulmate. I've never felt this.", "love_bombing",
         "Declared devotion before a single call. The speed is the tactic."),
        ("Let's move this to Telegram, I check it more.", "off_platform",
         "Every step away from a moderated platform is a step toward being unprotected."),
    ]),
]


class Command(BaseCommand):
    help = "Seed the Crush Empire deck with genuine and scam profiles (EN only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Retire every existing card before seeding (does not delete them).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            # Retire, don't delete. CardChallenge.profile is PROTECTed because it
            # is the audit trail of what each player was shown and how they
            # answered; a content reseed must never destroy that. Retired cards
            # stop being dealt (is_active=False) and the upsert below revives any
            # name that is still in the seed.
            retired = GameProfile.objects.update(is_active=False)
            self.stdout.write(f"retired {retired} existing cards")

        for emoji, name, age, segments in GENUINE:
            self._upsert(emoji, name, age, segments, is_scam=False)
        for emoji, name, age, segments in SCAM:
            self._upsert(emoji, name, age, segments, is_scam=True)

        active = GameProfile.objects.filter(is_active=True)
        genuine = active.filter(is_scam=False).count()
        scam = active.filter(is_scam=True).count()
        t2_genuine = active.filter(is_scam=False, tier2_eligible=True).count()
        t2_scam = active.filter(is_scam=True, tier2_eligible=True).count()

        self.stdout.write(self.style.SUCCESS(f"deck: {genuine} genuine, {scam} scam"))
        self.stdout.write(f"tier 2 eligible: {t2_genuine} genuine, {t2_scam} scam")
        if not (t2_genuine and t2_scam):
            # Tier 2 refuses to deal unless both pools can supply a card, because
            # a modal that only ever wraps a scam announces the answer.
            self.stdout.write(
                self.style.WARNING("tier 2 will not deal: both pools must be non-empty")
            )
        self.stdout.write("DE/FR bios are empty — author them in crush-admin, natively.")

    def _upsert(self, emoji, name, age, segments, is_scam):
        profile, _created = GameProfile.objects.update_or_create(
            display_name=name,
            is_scam=is_scam,
            defaults={
                "emoji": emoji,
                "age": age,
                "is_active": True,
                "tier2_eligible": len(segments) >= TIER2_MIN_SEGMENTS,
            },
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
