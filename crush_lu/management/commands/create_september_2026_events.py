"""
Seed the September 2026 event programme for Crush.lu.

One-shot data command: eight events following the proven August cadence —
Speed Dating every Wednesday + Quiz Night every Thursday, 19:00 Europe/Luxembourg,
alternating language weeks (DE/LU, then FR), €15.50 fee, registration deadline
18:00 on the day of the event.

Each Quiz Night also gets its QuizEvent shell and a question pack; packs rotate
across the four evenings so consecutive weeks never replay the same questions.

Events are created UNPUBLISHED and without a venue address on purpose: the
exact venue stays hidden until signup, so the coach fills street/postcode/
banner/coaches in the admin and publishes there (the echo.lu publish gate
requires the full address). ``--publish`` + ``--address-*`` skips that manual
step when the venue is known up front.

Idempotent: events are keyed on (event_type, date_time). Re-running skips what
already exists — nothing is updated or deleted in place. Fix data in the admin.

Usage:
    python manage.py create_september_2026_events                # dry run by default? no — creates drafts
    python manage.py create_september_2026_events --dry-run
    python manage.py create_september_2026_events --publish \
        --address-street "rue du Nord" --address-postcode 2229 \
        --address-town Luxembourg --location "The Venue"
    python manage.py create_september_2026_events --banner-from 15
"""

from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from crush_lu.models import MeetupEvent
from crush_lu.models.quiz import QuizEvent

YEAR = 2026
MONTH = 9

FEE = "15.50"

# Speed Dating copy — identical across all four evenings, matching August.
SD_TITLE = {
    "en": "💘 Speed Dating | What if this was the right moment",
    "de": "💘 Speed Dating | Was, wenn jetzt der richtige Moment ist",
    "fr": "💘 Speed Dating | Et si c'était le bon moment",
}

SD_DESCRIPTION = {
    "en": (
        "We've all spent hours scrolling without ever really meeting anyone 😅  \n"
        "Here, it's the opposite : a few minutes, a real look, a real conversation 💫\n\n"
        "The principle is simple : you go through short face to face encounters, "
        "and you immediately feel if the connection is there ⚡\n\n"
        "On the programme :\n\n"
        "💬 Sincere exchanges, no script and no filter  \n"
        "😊 A caring atmosphere where nobody judges anyone  \n"
        "✨ Connections that happen naturally  \n"
        "🥂 A drink to extend the evening with those who caught your attention\n\n"
        "Everything takes place in a small group, for more intimacy and comfort.  \n"
        "A deliberately limited number of people, so everyone has the time to be "
        "truly met 🤫\n\n"
        "No need to be at ease or to have the perfect line.  \n"
        "Come as you are, the rest follows on its own 💘\n\n"
        "We're waiting for you 🧡"
    ),
    "de": (
        "Wir haben alle schon stundenlang gescrollt, ohne jemals wirklich "
        "jemanden kennenzulernen 😅  \n"
        "Hier ist es umgekehrt : ein paar Minuten, ein echter Blick, ein echtes "
        "Gespräch 💫\n\n"
        "Das Prinzip ist einfach : Du erlebst kurze Begegnungen von Angesicht zu "
        "Angesicht und spürst sofort, ob die Verbindung stimmt ⚡\n\n"
        "Auf dem Programm :\n\n"
        "💬 Ehrliche Gespräche, ohne Skript und ohne Filter  \n"
        "😊 Eine wohlwollende Atmosphäre, in der niemand jemanden verurteilt  \n"
        "✨ Verbindungen, die ganz natürlich entstehen  \n"
        "🥂 Ein Drink, um den Abend mit denen zu verlängern, die dich beeindruckt "
        "haben\n\n"
        "Alles findet in kleiner Runde statt, für mehr Intimität und Wohlbefinden.  \n"
        "Eine bewusst begrenzte Gruppe, damit jeder die Zeit hat, wirklich "
        "wahrgenommen zu werden 🤫\n\n"
        "Du musst nicht selbstsicher sein oder den perfekten Satz parat haben.  \n"
        "Komm, wie du bist, der Rest ergibt sich von selbst 💘\n\n"
        "Wir warten auf dich 🧡"
    ),
    "fr": (
        "On a tous déjà passé des heures à scroller sans jamais vraiment "
        "rencontrer personne 😅  \n"
        "Ici, c'est l'inverse : quelques minutes, un vrai regard, une vraie "
        "conversation 💫\n\n"
        "Le principe est simple : tu enchaînes des rencontres courtes en face à "
        "face, et tu sens tout de suite si le courant passe ⚡\n\n"
        "Au programme :\n\n"
        "💬 Des échanges sincères, sans script et sans filtre  \n"
        "😊 Une ambiance bienveillante où personne ne juge personne  \n"
        "✨ Des connexions qui se créent naturellement  \n"
        "🥂 Un verre pour prolonger la soirée avec ceux qui t'ont marqué\n\n"
        "Tout se passe en petit comité, pour plus d'intimité et de confort.  \n"
        "Un groupe volontairement restreint, pour que chacun ait le temps d'être "
        "vraiment rencontré 🤫\n\n"
        "Pas besoin d'être à l'aise ou d'avoir la réplique parfaite.  \n"
        "Viens comme tu es, le reste suit tout seul 💘\n\n"
        "On t'attend 🧡"
    ),
}

# Quiz Night copy — identical across all four evenings, matching August.
QN_TITLE = {
    "en": "🧩 Quiz Night | New format, new encounters",
    "de": "🧩 Quiz Night | Neues Format, neue Begegnungen",
    "fr": "🧩 Quiz Night | Nouvelle formule, nouvelles rencontres",
}

QN_DESCRIPTION = {
    "en": (
        "We've completely rethought our Quiz Night from A to Z, and we can't "
        "wait to show you the result 🎉\n\n"
        "New format, new questions, new ways to meet each other.  \n"
        "The principle stays the same : have fun first, and let the connections "
        "happen on their own 💫\n\n"
        "On the programme :\n\n"
        "🧠 Brand new questions, sometimes surprising, often revealing  \n"
        "🤝 A format designed so everyone gets to talk, not just the most "
        "outgoing  \n"
        "😄 Laughter, debates and moments of complicity  \n"
        "🥂 A drink to extend the evening with those who caught your attention\n\n"
        "No need to know everything or to have revised.  \n"
        "Here, it's the most sincere answers that make the best encounters ✨\n\n"
        "Come try the new format, we're waiting for you 🧡"
    ),
    "de": (
        "Wir haben unsere Quiz Night von A bis Z neu gedacht, und wir freuen "
        "uns darauf, euch das Ergebnis zu zeigen 🎉\n\n"
        "Neues Format, neue Fragen, neue Wege, sich kennenzulernen.  \n"
        "Das Prinzip bleibt dasselbe : zuerst Spaß haben und die Verbindungen "
        "ganz von selbst entstehen lassen 💫\n\n"
        "Auf dem Programm :\n\n"
        "🧠 Ganz neue Fragen, manchmal überraschend, oft aufschlussreich  \n"
        "🤝 Ein Format, bei dem jeder zu Wort kommt, nicht nur die Gesprächigsten  \n"
        "😄 Lachen, Diskussionen und Momente der Verbundenheit  \n"
        "🥂 Ein Drink, um den Abend mit denen zu verlängern, die dich beeindruckt "
        "haben\n\n"
        "Du musst nicht alles wissen oder dich vorbereitet haben.  \n"
        "Hier sind es die ehrlichsten Antworten, die zu den besten Begegnungen "
        "führen ✨\n\n"
        "Komm und teste das neue Format, wir warten auf dich 🧡"
    ),
    "fr": (
        "On a repensé notre Quiz Night de A à Z, et on a hâte de vous faire "
        "découvrir ce que ça donne 🎉\n\n"
        "Nouveau format, nouvelles questions, nouvelles façons de se "
        "rencontrer.  \n"
        "Le principe reste le même : s'amuser d'abord, et laisser les "
        "connexions se créer toutes seules 💫\n\n"
        "Au programme :\n\n"
        "🧠 Des questions inédites, parfois surprenantes, souvent révélatrices  \n"
        "🤝 Un format pensé pour que tout le monde échange, pas seulement les "
        "plus bavards  \n"
        "😄 Des rires, des débats et des moments de complicité  \n"
        "🥂 Un verre pour prolonger la soirée avec ceux qui t'ont marqué\n\n"
        "Pas besoin d'être incollable ni d'avoir révisé.  \n"
        "Ici, ce sont les réponses les plus sincères qui font les meilleures "
        "rencontres ✨\n\n"
        "Viens tester la nouvelle formule, on t'attend 🧡"
    ),
}

# The Wednesdays (Speed Dating) and Thursdays (Quiz Night) of September 2026.
# Sep 2026 has FIVE Wednesdays (2, 9, 16, 23, 30); the programme deliberately
# covers the first four so the month does not end on a quiz-week language
# imbalance — Sep 30 is left free (school-holiday tail, venue planning).
# Language weeks alternate exactly like August: DE/LU, FR, DE/LU, FR.
# The DE/LU speed datings ran 25–42 in August, the FR ones 25–38.
# Quiz packs rotate so consecutive evenings never replay the same questions.
PROGRAMME = [
    # (day, event_type, languages, min_age, max_age, quiz_pack)
    (2, "speed_dating", ["de", "lu"], 25, 42, None),
    (3, "quiz_night", ["de", "lu"], 18, 99, "classic"),
    (9, "speed_dating", ["fr"], 25, 38, None),
    (10, "quiz_night", ["fr"], 18, 99, "media-love"),
    (16, "speed_dating", ["de", "lu"], 25, 42, None),
    (17, "quiz_night", ["de", "lu"], 18, 99, "mettwoch"),
    (23, "speed_dating", ["fr"], 25, 38, None),
    (24, "quiz_night", ["fr"], 18, 99, "classic"),
]

SD_DURATION = 120
QN_DURATION = 160
SD_MAX = 14
QN_MAX = 24
# 24 seats in teams of 4 → 6 tables, matching create_quiz_night_event's
# table bootstrap (the readiness check requires at least 2).
QN_TABLES = 6
# Balanced speed dating: 7 men / 7 women / 0 NB of the 14 total. Pass
# --no-gender-caps to leave the caps blank (total-only) instead.
SD_GENDER_CAPS = (7, 7, 0)


def _local(year, month, day, hour, minute=0):
    """Aware datetime in the project timezone (Europe/Luxembourg)."""
    return datetime(
        year, month, day, hour, minute, tzinfo=timezone.get_current_timezone()
    )


class Command(BaseCommand):
    help = (
        "Create the September 2026 Crush.lu event programme (4 Speed Dating + "
        "4 Quiz Night) with quiz packs. Idempotent per (event_type, date_time)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen, write nothing.",
        )
        parser.add_argument(
            "--publish",
            action="store_true",
            help=(
                "Publish immediately. Requires the full venue address — the "
                "echo.lu gate rejects published events without it."
            ),
        )
        parser.add_argument(
            "--no-gender-caps",
            action="store_true",
            help="Leave per-gender caps blank on Speed Dating (total cap only).",
        )
        parser.add_argument(
            "--profile-requirement",
            default="completed",
            choices=[c[0] for c in MeetupEvent.PROFILE_REQUIREMENT_CHOICES],
            help="Profile level needed to register (default: completed).",
        )
        parser.add_argument(
            "--premium-seats",
            type=int,
            default=0,
            help="Seats reserved for premium members within the total (default 0).",
        )
        parser.add_argument(
            "--location",
            default="",
            help="Venue name shown to registered members (e.g. 'ATMOS').",
        )
        parser.add_argument("--address-street", default="", help="Venue street.")
        parser.add_argument(
            "--address-number", default="", help="Venue house number."
        )
        parser.add_argument(
            "--address-postcode", default="", help="4-digit Luxembourg postcode."
        )
        parser.add_argument("--address-town", default="", help="Venue town.")
        parser.add_argument(
            "--banner-from",
            type=int,
            default=None,
            metavar="EVENT_ID",
            help=(
                "Reuse the banner image of an existing event, applied ONLY to "
                "new events of the same event_type (e.g. --banner-from 15 for "
                "the August Speed Dating artwork)."
            ),
        )
        parser.add_argument(
            "--quiz-owner",
            default=None,
            metavar="USERNAME",
            help="User recorded as creating the QuizEvents (default: a superuser).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        publish = options["publish"]

        address = {
            "location": options["location"],
            "address_street": options["address_street"],
            "address_number": options["address_number"],
            "address_postcode": options["address_postcode"].replace("L-", "").replace("l-", ""),
            "address_town": options["address_town"],
        }
        if publish:
            missing = [
                name
                for name in ("location", "address_street", "address_postcode", "address_town")
                if not address[name].strip()
            ]
            if missing:
                raise CommandError(
                    "--publish needs the full venue address: "
                    + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
                    + " (drafts skip this: fill the address in the admin)."
                )
            # Same rule the model's echo.lu gate enforces (unmet_publish_
            # requirements): a four-digit postcode. Validated here because
            # objects.create() never runs field validators or clean().
            import re

            if not re.fullmatch(r"[0-9]{4}", address["address_postcode"]):
                raise CommandError(
                    "--address-postcode must be exactly four digits "
                    f"(got '{options['address_postcode']}')."
                )

        banner_event = None
        if options["banner_from"] is not None:
            try:
                banner_event = MeetupEvent.objects.get(pk=options["banner_from"])
            except MeetupEvent.DoesNotExist:
                raise CommandError(
                    f"--banner-from: no MeetupEvent with id {options['banner_from']}."
                )
            if not banner_event.image:
                raise CommandError(
                    f"--banner-from: event {banner_event.pk} has no banner image."
                )

        quiz_owner = self._quiz_owner(options["quiz_owner"])

        if options["premium_seats"] > min(SD_MAX, QN_MAX):
            raise CommandError(
                f"--premium-seats ({options['premium_seats']}) cannot exceed the "
                f"smallest event capacity ({min(SD_MAX, QN_MAX)}): reserved "
                "seats are held WITHIN the total, and above it every regular "
                "member would land on the waitlist."
            )

        created, skipped, quizzes = [], [], []
        for day, event_type, languages, min_age, max_age, pack in PROGRAMME:
            exists = MeetupEvent.objects.filter(
                event_type=event_type,
                date_time=_local(YEAR, MONTH, day, 19),
            ).exists()
            label = f"Sep {day} {'Speed Dating' if event_type == 'speed_dating' else 'Quiz Night'} [{'/'.join(languages)}]"
            is_sd = event_type == "speed_dating"
            if exists:
                # The event row is there but its quiz may not be (e.g. a first
                # run without a superuser). Repair that in place rather than
                # telling the operator to re-run into a wall of skips.
                event = MeetupEvent.objects.get(
                    event_type=event_type,
                    date_time=_local(YEAR, MONTH, day, 19),
                )
                self._seed_quiz_if_missing(
                    event, pack, quiz_owner, dry_run, quizzes
                )
                skipped.append(label)
                self.stdout.write(f"= {label}: already exists, skipped")
                continue

            event_kwargs = {
                "event_type": event_type,
                "title": (SD_TITLE if is_sd else QN_TITLE)["en"],
                "title_en": (SD_TITLE if is_sd else QN_TITLE)["en"],
                "title_de": (SD_TITLE if is_sd else QN_TITLE)["de"],
                "title_fr": (SD_TITLE if is_sd else QN_TITLE)["fr"],
                "description": (SD_DESCRIPTION if is_sd else QN_DESCRIPTION)["en"],
                "description_en": (SD_DESCRIPTION if is_sd else QN_DESCRIPTION)["en"],
                "description_de": (SD_DESCRIPTION if is_sd else QN_DESCRIPTION)["de"],
                "description_fr": (SD_DESCRIPTION if is_sd else QN_DESCRIPTION)["fr"],
                "languages": languages,
                "date_time": _local(YEAR, MONTH, day, 19),
                "duration_minutes": SD_DURATION if is_sd else QN_DURATION,
                "max_participants": SD_MAX if is_sd else QN_MAX,
                "min_age": min_age,
                "max_age": max_age,
                "registration_deadline": _local(YEAR, MONTH, day, 18),
                "registration_fee": FEE,
                "profile_requirement": options["profile_requirement"],
                "reserved_premium_seats": options["premium_seats"],
                "canton": "Luxembourg",
                "enable_activity_voting": is_sd,
                "is_published": publish,
            }
            if is_sd and not options["no_gender_caps"]:
                event_kwargs["max_participants_m"] = SD_GENDER_CAPS[0]
                event_kwargs["max_participants_f"] = SD_GENDER_CAPS[1]
                event_kwargs["max_participants_nb"] = SD_GENDER_CAPS[2]
            event_kwargs.update(address)
            if banner_event is not None and banner_event.event_type == event_type:
                # Same-type artwork only: a Speed Dating banner on a Quiz
                # Night (or the reverse) misleads the public listing.
                event_kwargs["image"] = banner_event.image.name

            if dry_run:
                self.stdout.write(
                    f"+ {label}: would create "
                    f"({'published' if publish else 'draft'}, "
                    f"pack={pack or '—'})"
                )
                created.append(label)
                continue

            with transaction.atomic():
                event = MeetupEvent.objects.create(**event_kwargs)
                self._seed_quiz_if_missing(
                    event, pack, quiz_owner, dry_run, quizzes
                )

            suffix = ""
            if pack:
                if quiz_owner is not None:
                    q = quizzes[-1]
                    suffix = f" + quiz pack '{q[1]}' ({q[2]} rounds / {q[3]} questions)"
                    if q[4]:
                        suffix += (
                            f" — {len(q[4])} image question(s) pending upload"
                        )
                else:
                    suffix = (
                        " — quiz NOT created (no superuser; pass --quiz-owner)"
                    )
            self.stdout.write(
                self.style.SUCCESS(f"+ {label}: created {event.pk} "
                                   f"({'published' if publish else 'draft'}){suffix}")
            )
            created.append(label)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: would create {len(created)}, "
                    f"{len(skipped)} already exist — nothing written."
                )
            )
            return

        pending_uploads = sum(1 for q in quizzes if q[4])
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone: {len(created)} created, {len(skipped)} skipped, "
                f"{len(quizzes)} quiz night(s) seeded."
            )
        )
        if pending_uploads:
            self.stdout.write(
                self.style.WARNING(
                    f"{pending_uploads} quiz night(s) include image questions "
                    "pending upload — finish them in the quiz admin."
                )
            )
        if not publish:
            self.stdout.write(
                "Events are drafts. Fill the venue address + banner + coaches "
                "in the admin, then publish (bulk action checks the echo.lu gate)."
            )

    def _seed_quiz_if_missing(self, event, pack, quiz_owner, dry_run, quizzes):
        """Create the QuizEvent + pack for a quiz night, if not already there.

        Also backfills quizzes on re-runs over existing events (a first run
        without a superuser creates the MeetupEvents but no quizzes), and
        bootstraps the QN_TABLES physical tables the readiness check and the
        live rotation need — mirroring create_quiz_night_event.
        """
        if not pack:
            return
        if dry_run:
            return
        if quiz_owner is None:
            return
        if QuizEvent.objects.filter(event=event).exists():
            return
        with transaction.atomic():
            quiz = QuizEvent.objects.create(
                event=event,
                created_by=quiz_owner,
                num_tables=QN_TABLES,
            )
            quiz.ensure_tables()
            from crush_lu.management.commands.generate_crush_quiz import (
                populate_quiz,
            )

            result = populate_quiz(quiz, pack=pack)
        quizzes.append(
            (
                event,
                pack,
                result.rounds_created,
                result.questions_created,
                result.pending_uploads,
            )
        )

    def _quiz_owner(self, username):
        User = get_user_model()
        if username:
            try:
                return User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"--quiz-owner: no user '{username}'.")
        superuser = User.objects.filter(is_superuser=True).order_by("pk").first()
        if superuser is None:
            self.stdout.write(
                self.style.WARNING(
                    "No superuser found — QuizEvents are NOT created. "
                    "Pass --quiz-owner <username> to seed the quiz nights."
                )
            )
        return superuser
