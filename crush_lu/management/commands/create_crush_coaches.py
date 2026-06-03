"""
Create / refresh sample Crush Coach profiles for local development.

Re-running this command UPDATES existing coaches (bio, specializations,
languages, premium settings) so the premium coach directory looks good.

Every coach gets a photo: a real portrait from randomuser.me when reachable,
otherwise a locally generated gradient avatar with their initials (no network).
Flags: --avatars-only (always generate, skip the network), --force-photos
(replace existing photos), --skip-photos (don't touch photos at all).
"""

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from crush_lu.models import CrushCoach

COACHES = [
    {
        "username": "coach.marie",
        "email": "marie@crush.lu",
        "first_name": "Marie",
        "last_name": "Dupont",
        "gender": "female",
        "specializations": "Young professionals · 25–35 · First-timers",
        "spoken_languages": ["fr", "en", "lu"],
        "bio": (
            "Hi, I'm Marie! For five years I've helped busy young professionals "
            "in Luxembourg slow down and build connections that actually last. "
            "Expect warm, honest feedback and a plan tailored to you — no scripts, "
            "no games. I'll be right beside you at your first event."
        ),
        "max_premium_members": 15,
    },
    {
        "username": "coach.thomas",
        "email": "thomas@crush.lu",
        "first_name": "Thomas",
        "last_name": "Weber",
        "gender": "male",
        "specializations": "Students · 18–25 · Confidence building",
        "spoken_languages": ["de", "en", "lu"],
        "bio": (
            "Dating should feel exciting, not stressful. I work with students and "
            "young adults who want to meet people without the awkwardness. We'll "
            "focus on confidence, good conversation, and being yourself — then put "
            "it into practice at a real meetup."
        ),
        "max_premium_members": 20,
    },
    {
        "username": "coach.sophie",
        "email": "sophie@crush.lu",
        "first_name": "Sophie",
        "last_name": "Muller",
        "gender": "female",
        "specializations": "35+ · Professionals · Second chances",
        "spoken_languages": ["fr", "de", "en"],
        "bio": (
            "Life after 35 is the perfect time for authentic, grown-up love. I "
            "specialise in helping established professionals reconnect with dating "
            "on their own terms — thoughtful, unhurried, and genuine. Let's find "
            "the people who truly fit your life."
        ),
        "max_premium_members": 12,
    },
    {
        "username": "coach.lena",
        "email": "lena@crush.lu",
        "first_name": "Lena",
        "last_name": "Schmit",
        "gender": "female",
        "specializations": "LGBTQ+ · Inclusive dating · All ages",
        "spoken_languages": ["lu", "de", "fr", "en"],
        "bio": (
            "Everyone deserves to feel safe and seen while dating. As an inclusive, "
            "LGBTQ+-friendly coach, I create a judgement-free space where you can be "
            "fully yourself. Together we'll find events and people where you belong."
        ),
        "max_premium_members": 15,
    },
    {
        "username": "coach.paolo",
        "email": "paolo@crush.lu",
        "first_name": "Paolo",
        "last_name": "Ferreira",
        "gender": "male",
        "specializations": "Internationals · Expats · New in Luxembourg",
        "spoken_languages": ["en", "fr"],
        "bio": (
            "Moving to a new country and trying to date at the same time? I've been "
            "there. I help internationals and expats build a social life and meet "
            "people in Luxembourg from scratch — practical, friendly, and zero "
            "pressure."
        ),
        "max_premium_members": 18,
    },
]


class Command(BaseCommand):
    help = "Create or refresh sample Crush Coach profiles (rich, premium-ready)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-photos",
            action="store_true",
            help="Don't set coach photos at all",
        )
        parser.add_argument(
            "--avatars-only",
            action="store_true",
            help="Skip the randomuser.me network call; always use generated avatars",
        )
        parser.add_argument(
            "--force-photos",
            action="store_true",
            help="Replace photos even for coaches that already have one",
        )

    def handle(self, *args, **options):
        skip_photos = options["skip_photos"]
        avatars_only = options["avatars_only"]
        force_photos = options["force_photos"]
        created = 0
        updated = 0

        for index, data in enumerate(COACHES):
            user, user_created = User.objects.get_or_create(
                username=data["username"],
                defaults={
                    "email": data["email"],
                    "first_name": data["first_name"],
                    "last_name": data["last_name"],
                },
            )
            if user_created:
                user.set_password("crushcoach2025")
                user.save()
            else:
                # Keep names in sync for existing dev users.
                user.first_name = data["first_name"]
                user.last_name = data["last_name"]
                user.email = data["email"]
                user.save(update_fields=["first_name", "last_name", "email"])

            coach, coach_created = CrushCoach.objects.get_or_create(user=user)
            coach.bio = data["bio"]
            coach.specializations = data["specializations"]
            coach.spoken_languages = data["spoken_languages"]
            coach.is_active = True
            coach.max_active_reviews = 10
            coach.accepting_premium = True
            coach.max_premium_members = data["max_premium_members"]
            coach.save()

            if coach_created:
                created += 1
            else:
                updated += 1

            if not skip_photos and (force_photos or not coach.photo):
                self._attach_photo(coach, data, index, avatars_only)

            self.stdout.write(
                self.style.SUCCESS(
                    f"{'Created' if coach_created else 'Updated'}: "
                    f"{coach.user.get_full_name()}"
                )
            )

        # Keep the member-facing premium directory clean: an active coach with
        # no bio or no name (e.g. test fixtures) shouldn't be offered as a
        # premium choice.
        from django.db.models import Q

        hidden = (
            CrushCoach.objects.filter(accepting_premium=True)
            .filter(Q(bio__isnull=True) | Q(bio="") | Q(user__first_name=""))
            .update(accepting_premium=False)
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created} created, {updated} updated. "
                f"All are accepting premium members."
            )
        )
        if hidden:
            self.stdout.write(
                self.style.WARNING(
                    f"Hid {hidden} incomplete coach(es) (no bio) from the premium directory."
                )
            )
        self.stdout.write("Default password for all coaches: crushcoach2025")

    def _attach_photo(self, coach, data, index, avatars_only):
        """Give the coach a photo: real portrait if possible, else an avatar.

        Always succeeds — falls back to a locally generated gradient avatar so
        the directory never shows a missing picture.
        """
        content, source = None, None
        if not avatars_only:
            content = self._fetch_portrait(data)
            source = "portrait"
        if content is None:
            content = self._gradient_avatar(data, index)
            source = "avatar"
        try:
            coach.photo.save(f"{data['username']}.jpg", ContentFile(content), save=True)
            self.stdout.write(
                self.style.SUCCESS(f"  - photo added for {data['username']} ({source})")
            )
        except Exception as e:  # storage/network failure (e.g. Azure offline)
            self.stdout.write(
                self.style.WARNING(f"  - photo failed for {data['username']}: {e}")
            )

    def _fetch_portrait(self, data):
        """Return JPEG bytes of a real portrait from randomuser.me, or None."""
        try:
            import requests

            resp = requests.get(
                "https://randomuser.me/api/",
                params={"gender": data["gender"], "seed": data["username"]},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json().get("results") or []
            if not results:
                return None
            photo = requests.get(results[0]["picture"]["large"], timeout=10)
            photo.raise_for_status()
            return photo.content
        except Exception:
            return None

    # Brand-ish gradient pairs, one per coach (top -> bottom RGB).
    GRADIENTS = [
        ((155, 89, 182), (232, 67, 147)),  # purple -> pink
        ((52, 152, 219), (41, 128, 185)),  # blue
        ((26, 188, 156), (22, 160, 133)),  # teal
        ((230, 126, 34), (211, 84, 0)),  # orange
        ((155, 89, 182), (52, 73, 94)),  # purple -> slate
    ]

    def _gradient_avatar(self, data, index):
        """Generate a 400x400 gradient avatar with the coach's initials."""
        from io import BytesIO

        from PIL import Image, ImageDraw, ImageFont

        size = 400
        top, bottom = self.GRADIENTS[index % len(self.GRADIENTS)]
        img = Image.new("RGB", (size, size))
        draw = ImageDraw.Draw(img)
        for y in range(size):
            t = y / size
            draw.line(
                [(0, y), (size, y)],
                fill=(
                    int(top[0] + (bottom[0] - top[0]) * t),
                    int(top[1] + (bottom[1] - top[1]) * t),
                    int(top[2] + (bottom[2] - top[2]) * t),
                ),
            )

        initials = (data["first_name"][:1] + data["last_name"][:1]).upper()
        try:
            font = ImageFont.load_default(size=190)
        except TypeError:  # older Pillow without size kwarg
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), initials, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(
            ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
            initials,
            font=font,
            fill="white",
        )

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
