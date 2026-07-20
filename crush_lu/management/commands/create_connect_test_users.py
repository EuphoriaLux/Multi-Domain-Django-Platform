"""
Create perfectly-wired Crush Connect test users for staging.

Two roles are created:

  connect_premium_N@crush.lu   — Premium members (drop RECEIVERS)
    Verified profile + assigned coach + Crush Connect onboarded.
    Satisfies is_sender_eligible() so they get a daily drop.

  connect_candidate_N@crush.lu — Catalogue candidates (appear IN drops)
    Verified profile + LuxID social account + Crush Connect onboarded.
    Satisfies is_catalogue_eligible() so they surface in others' drops.

All users are wired so that get_eligible_pool() returns results and
get_or_create_daily_drop() populates immediately on a fresh database.

Usage:
    python manage.py create_connect_test_users
    python manage.py create_connect_test_users --premium-count 5 --catalogue-count 15
    python manage.py create_connect_test_users --skip-photos --no-prefill-drops
    python manage.py create_connect_test_users --reset
"""

import random
import requests
from datetime import date

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from crush_lu.models import CrushProfile, PremiumMembership, ProfileSubmission
from crush_lu.models.crush_connect import CrushConnectMembership, SparkPrompt
from crush_lu.models.profiles import CrushCoach


_PREMIUM_PREFIX = "connect_premium_"
_CANDIDATE_PREFIX = "connect_candidate_"
_EMAIL_DOMAIN = "crush.lu"

# All test users are this age — falls within the default preferred_age range
# (18–99) on both sides so mutual-age filters always pass without overrides.
_TEST_AGE = 28

_MALE_NAMES = [
    "Thomas", "Lucas", "Pierre", "Nicolas", "Jean", "Marc",
    "Alexandre", "Julien", "Antoine", "Maxime", "Philippe",
    "David", "Michel", "Laurent", "François",
]
_FEMALE_NAMES = [
    "Marie", "Sophie", "Julie", "Anne", "Laura", "Claire",
    "Sarah", "Emma", "Camille", "Lea", "Charlotte",
    "Isabelle", "Nathalie", "Caroline", "Stephanie",
]
_LAST_NAMES = [
    "Dupont", "Martin", "Bernard", "Weber", "Muller",
    "Schmit", "Wagner", "Klein", "Hoffmann", "Meyer",
    "Da Silva", "Ferreira", "Santos", "Pereira", "Costa",
]

_LUXEMBOURG_LOCATIONS = [
    ("Luxembourg City", 40),
    ("Esch-sur-Alzette", 15),
    ("Differdange", 8),
    ("Dudelange", 7),
    ("Ettelbruck", 5),
    ("Diekirch", 5),
    ("Wiltz", 3),
    ("Echternach", 3),
    ("Remich", 3),
    ("Vianden", 2),
    ("Clervaux", 2),
    ("Mersch", 3),
    ("Grevenmacher", 2),
    ("Mamer", 2),
]


def _weighted_location():
    locations, weights = zip(*_LUXEMBOURG_LOCATIONS)
    return random.choices(locations, weights=weights, k=1)[0]


def _test_dob():
    today = date.today()
    try:
        return today.replace(year=today.year - _TEST_AGE)
    except ValueError:
        return today.replace(year=today.year - _TEST_AGE, day=28)


def _fake_user_data(gender):
    names = _MALE_NAMES if gender == "male" else _FEMALE_NAMES
    return {
        "first_name": random.choice(names),
        "last_name": random.choice(_LAST_NAMES),
        "photo_data": None,
    }


def _fetch_user_data(gender):
    try:
        resp = requests.get(
            "https://randomuser.me/api/",
            params={"gender": gender, "nat": "fr,de,nl,be,gb,es,it,pt"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("results"):
            return _fake_user_data(gender)
        u = data["results"][0]
        photo_resp = requests.get(u["picture"]["large"], timeout=10)
        photo_resp.raise_for_status()
        return {
            "first_name": u["name"]["first"],
            "last_name": u["name"]["last"],
            "photo_data": photo_resp.content,
        }
    except requests.RequestException:
        return _fake_user_data(gender)


def _ensure_spark_prompt():
    """Return an active SparkPrompt, creating a stub if the table is empty."""
    prompt = SparkPrompt.objects.filter(is_active=True).first()
    if prompt is None:
        prompt, _ = SparkPrompt.objects.get_or_create(
            text="What in their profile made you curious?",
            defaults={"is_active": True, "weight": 1},
        )
    return prompt


class Command(BaseCommand):
    help = (
        "Create Crush Connect test users for staging: "
        "Premium members (drop receivers) and LuxID-verified catalogue candidates."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--premium-count",
            type=int,
            default=3,
            help="Number of Premium members to create (default: 3)",
        )
        parser.add_argument(
            "--catalogue-count",
            type=int,
            default=9,
            help="Number of catalogue candidates to create (default: 9)",
        )
        parser.add_argument(
            "--password",
            default="connect2025",
            help="Password for all created users (default: connect2025)",
        )
        parser.add_argument(
            "--skip-photos",
            action="store_true",
            help=(
                "Skip randomuser.me photo download (faster). WARNING: Connect "
                "eligibility requires photo_1 — photoless seeds are invisible "
                "in Drops and the catalogue."
            ),
        )
        parser.add_argument(
            "--no-prefill-drops",
            action="store_true",
            help="Skip calling get_or_create_daily_drop() after creation",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing connect_premium_* / connect_candidate_* users first",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            self._delete_existing()

        coach = CrushCoach.objects.filter(is_active=True).first()
        if coach is None:
            raise CommandError(
                "No active CrushCoach found. Run 'create_crush_coaches' first."
            )

        prompt = _ensure_spark_prompt()
        password = options["password"]
        skip_photos = options["skip_photos"]

        premium_users = []
        candidate_users = []

        self.stdout.write("\nCreating Premium members (drop receivers)...")
        for i in range(1, options["premium_count"] + 1):
            user = self._create_user(
                prefix=_PREMIUM_PREFIX,
                index=i,
                gender="male" if i % 2 == 1 else "female",
                password=password,
                skip_photos=skip_photos,
                coach=coach,
                prompt=prompt,
                is_premium=True,
            )
            if user:
                premium_users.append(user)

        self.stdout.write("\nCreating Catalogue candidates (appear in drops)...")
        for i in range(1, options["catalogue_count"] + 1):
            user = self._create_user(
                prefix=_CANDIDATE_PREFIX,
                index=i,
                gender="female" if i % 2 == 1 else "male",
                password=password,
                skip_photos=skip_photos,
                coach=None,
                prompt=prompt,
                is_premium=False,
            )
            if user:
                candidate_users.append(user)

        self._print_summary(premium_users, candidate_users, coach, password)

        if not options["no_prefill_drops"] and premium_users:
            self._prefill_drops(premium_users)

    def _delete_existing(self):
        premium_count = User.objects.filter(username__startswith=_PREMIUM_PREFIX).count()
        candidate_count = User.objects.filter(username__startswith=_CANDIDATE_PREFIX).count()
        User.objects.filter(username__startswith=_PREMIUM_PREFIX).delete()
        User.objects.filter(username__startswith=_CANDIDATE_PREFIX).delete()
        self.stdout.write(
            f"Deleted {premium_count} premium + {candidate_count} candidate users."
        )

    def _create_user(
        self, *, prefix, index, gender, password, skip_photos, coach, prompt, is_premium
    ):
        username = f"{prefix}{index}"
        email = f"{username}@{_EMAIL_DOMAIN}"

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"  Skipping {email} (already exists)")
            return None

        try:
            user_data = _fake_user_data(gender) if skip_photos else _fetch_user_data(gender)
            gender_code = "M" if gender == "male" else "F"
            role_label = "premium" if is_premium else "candidate"

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=user_data["first_name"],
                last_name=user_data["last_name"],
            )
            # Satisfy the 30-day inactivity cutoff in get_eligible_pool().
            User.objects.filter(pk=user.pk).update(last_login=timezone.now())
            user.refresh_from_db()

            profile_kwargs = dict(
                user=user,
                date_of_birth=_test_dob(),
                gender=gender_code,
                phone_number=(
                    f"+352 {random.randint(600, 699)} "
                    f"{random.randint(100, 999)} "
                    f"{random.randint(100, 999)}"
                ),
                phone_verified=True,
                phone_verified_at=timezone.now(),
                location=_weighted_location(),
                bio=f"Connect test {role_label} — staging only.",
                interests="hiking, photography, travel",
                show_full_name=True,
                show_exact_age=True,
                preferred_language="en",
                # preferred_genders defaults to [] (no restriction) — skip the
                # gender filter entirely so any mix of test users sees each other.
                # preferred_age_min/max default to 18/99 — everyone passes.
                is_approved=True,
                is_active=True,
                approved_at=timezone.now(),
                verification_status="verified",
                verification_method="admin",
                completion_status="submitted",
            )
            if is_premium:
                profile_kwargs["assigned_coach"] = coach
                profile_kwargs["assigned_coach_at"] = timezone.now()

            profile = CrushProfile.objects.create(**profile_kwargs)

            if is_premium:
                # The receiver gate keys off an ACTIVE PremiumMembership, not
                # assigned_coach — give premium seeds the real entitlement.
                PremiumMembership.objects.create(
                    user=user,
                    coach=coach,
                    status="active",
                    payment_confirmed=True,
                    payment_date=timezone.now(),
                )

            if user_data.get("photo_data"):
                try:
                    profile.photo_1.save(
                        f"{user.pk}_photo1.jpg",
                        ContentFile(user_data["photo_data"]),
                        save=True,
                    )
                except Exception as exc:
                    self.stderr.write(f"  Warning: photo save failed for {email}: {exc}")

            ProfileSubmission.objects.create(
                profile=profile,
                status="approved",
                coach_notes="Auto-created connect test profile",
                review_call_completed=True,
                review_call_date=timezone.now(),
                review_call_notes="Connect test — screening call simulated",
                reviewed_at=timezone.now(),
            )

            membership = CrushConnectMembership.objects.create(
                user=user,
                onboarded_at=timezone.now(),
                # Read-the-Photo: consent so the clear photo is shown + surfaced.
                photo_share_consent=True,
                story_prompt=prompt,
                story_answer=(
                    "Looking for a genuine connection"
                    if is_premium
                    else "Open to meeting interesting people"
                ),
            )
            self._assign_gate_questions(membership)

            if not is_premium:
                from allauth.socialaccount.models import SocialAccount

                SocialAccount.objects.create(
                    user=user,
                    provider="luxid",
                    uid=f"luxid-test-{user.pk}",
                    extra_data={"sub": f"luxid-test-{user.pk}"},
                )

            self.stdout.write(f"  {role_label}: {email}")
            return user

        except Exception as exc:
            self.stderr.write(f"  Error creating {email}: {exc}")
            import traceback
            self.stderr.write(traceback.format_exc())
            return None

    def _assign_gate_questions(self, membership):
        """Give the member 3 gate questions from this week's set with truth answers."""
        from crush_lu.models import MemberGateQuestion
        from crush_lu.services.crush_connect import get_or_create_question_week

        week = get_or_create_question_week()
        questions = list(week.questions.filter(is_active=True)[:3])
        for position, question in enumerate(questions, start=1):
            MemberGateQuestion.objects.create(
                membership=membership,
                question=question,
                position=position,
                owner_answer=(position % 2 == 1),  # alternate Yes/No
                picked_week=week,
            )

    def _print_summary(self, premium_users, candidate_users, coach, password):
        coach_name = coach.user.get_full_name() or coach.user.username
        self.stdout.write("\n" + "=" * 62)
        self.stdout.write(self.style.SUCCESS("  Crush Connect test users ready"))
        self.stdout.write("=" * 62)

        self.stdout.write(f"\nPremium members (receive drop)  coach: {coach_name}")
        for u in premium_users:
            self.stdout.write(f"  {u.email:<38}  pass: {password}")

        self.stdout.write("\nCatalogue candidates (appear in drops)  LuxID: simulated")
        for u in candidate_users:
            self.stdout.write(f"  {u.email:<38}  pass: {password}")

        self.stdout.write("\n" + "-" * 62)
        self.stdout.write("Verify wiring:")
        self.stdout.write(
            "  .venv-1/Scripts/python.exe manage.py shell -c \""
            "from crush_lu.services.crush_connect import get_eligible_pool,"
            "get_or_create_daily_drop; "
            "from django.contrib.auth.models import User; "
            "u = User.objects.get(email='connect_premium_1@crush.lu'); "
            "print('Pool:', get_eligible_pool(u).count()); "
            "drop = get_or_create_daily_drop(u); "
            "print('Drop:', list(drop.recipients.values_list('email', flat=True)))\""
        )
        self.stdout.write("-" * 62 + "\n")

    def _prefill_drops(self, premium_users):
        from crush_lu.services.crush_connect import get_or_create_daily_drop

        self.stdout.write("Pre-filling today's drops...")
        for user in premium_users:
            try:
                drop = get_or_create_daily_drop(user)
                cards = list(drop.recipients.values_list("email", flat=True))
                display = ", ".join(cards) if cards else "(empty pool — check preferences)"
                self.stdout.write(f"  {user.email}")
                self.stdout.write(f"    -> {display}")
            except Exception as exc:
                self.stderr.write(f"  Drop prefill failed for {user.email}: {exc}")
        self.stdout.write("")
