"""
Tests for the September 2026 programme seeder.

Covers the behaviours the launch depends on:
- eight events on the right Wednesdays/Thursdays, 19:00, deadline 18:00;
- language alternation DE/LU ↔ FR matching August's cadence;
- Speed Dating settings (120 min, 14 seats, 7/7 gender balance, voting on);
- Quiz Night settings (160 min, 24 seats, no gender caps, voting off) and
  seeded question packs with distinct questions across consecutive evenings;
- drafts by default, publish gated on the full venue address;
- idempotency: a re-run skips everything.
"""

from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import MeetupEvent
from crush_lu.models.quiz import QuizEvent, QuizQuestion

User = get_user_model()

# Europe/Luxembourg in September is CEST (UTC+2).
TZ = timezone.get_fixed_timezone(120)


def _sep(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


class SeptemberProgrammeShapeTest(TestCase):
    """The eight events land on the right slots with the right settings."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser(
            username="quiz-owner", email="quiz-owner@example.com", password="x"
        )
        call_command("create_september_2026_events", "--quiz-owner", "quiz-owner")

    def test_eight_events_created(self):
        self.assertEqual(MeetupEvent.objects.count(), 8)

    def test_four_wednesdays_and_four_thursdays(self):
        days = sorted(
            e.date_time.astimezone(TZ).day for e in MeetupEvent.objects.all()
        )
        # Sep 2026: Wednesdays are 2, 9, 16, 23; Thursdays are 3, 10, 17, 24.
        self.assertEqual(days, [2, 3, 9, 10, 16, 17, 23, 24])

    def test_speed_dating_on_wednesdays_quiz_on_thursdays(self):
        for day in (2, 9, 16, 23):
            sd = MeetupEvent.objects.get(
                event_type="speed_dating", date_time=_sep(day, 19)
            )
            self.assertEqual(sd.duration_minutes, 120)
            self.assertEqual(sd.max_participants, 14)
            # 7 men / 7 women / 0 NB — a balanced room.
            self.assertEqual(sd.max_participants_m, 7)
            self.assertEqual(sd.max_participants_f, 7)
            self.assertEqual(sd.max_participants_nb, 0)
            self.assertTrue(sd.enable_activity_voting)
            self.assertEqual(sd.registration_deadline, _sep(day, 18))
            self.assertEqual(str(sd.registration_fee), "15.50")
        for day in (3, 10, 17, 24):
            qn = MeetupEvent.objects.get(
                event_type="quiz_night", date_time=_sep(day, 19)
            )
            self.assertEqual(qn.duration_minutes, 160)
            self.assertEqual(qn.max_participants, 24)
            self.assertFalse(qn.enable_activity_voting)
            self.assertEqual(qn.registration_deadline, _sep(day, 18))
            self.assertEqual(str(qn.registration_fee), "15.50")

    def test_language_alternation_matches_august(self):
        def langs(day):
            return list(
                MeetupEvent.objects.get(date_time=_sep(day, 19)).languages
            )

        self.assertEqual(langs(2), ["de", "lu"])
        self.assertEqual(langs(3), ["de", "lu"])
        self.assertEqual(langs(9), ["fr"])
        self.assertEqual(langs(10), ["fr"])
        self.assertEqual(langs(16), ["de", "lu"])
        self.assertEqual(langs(17), ["de", "lu"])
        self.assertEqual(langs(23), ["fr"])
        self.assertEqual(langs(24), ["fr"])

    def test_speed_dating_age_ranges(self):
        de_lu = MeetupEvent.objects.get(date_time=_sep(2, 19))
        fr = MeetupEvent.objects.get(date_time=_sep(9, 19))
        self.assertEqual((de_lu.min_age, de_lu.max_age), (25, 42))
        self.assertEqual((fr.min_age, fr.max_age), (25, 38))

    def test_events_are_drafts_by_default(self):
        self.assertFalse(
            MeetupEvent.objects.filter(is_published=True).exists()
        )

    def test_trilingual_copy_on_every_event(self):
        for e in MeetupEvent.objects.all():
            self.assertTrue(e.title_en and e.title_de and e.title_fr)
            self.assertTrue(
                e.description_en and e.description_de and e.description_fr
            )
            marker = (
                "Speed Dating"
                if e.event_type == "speed_dating"
                else "Quiz Night"
            )
            self.assertIn(marker, e.title_en)
            self.assertIn(marker, e.title_de)
            self.assertIn(marker, e.title_fr)

    def test_canton_set_for_echo_lu(self):
        # Drafts still carry the canton so the admin publish gate passes
        # once the address fields are filled in.
        self.assertTrue(
            MeetupEvent.objects.exclude(canton="Luxembourg").exists() is False
        )


class QuizSeedingTest(TestCase):
    """Quiz nights get their QuizEvent and a populated, distinct pack."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_superuser(
            username="quiz-owner", email="quiz-owner@example.com", password="x"
        )
        call_command("create_september_2026_events", "--quiz-owner", "quiz-owner")

    def test_four_quiz_events_with_questions(self):
        self.assertEqual(QuizEvent.objects.count(), 4)
        for quiz in QuizEvent.objects.all():
            rounds = quiz.rounds.count()
            questions = QuizQuestion.objects.filter(round__quiz=quiz).count()
            self.assertGreaterEqual(rounds, 5)
            self.assertGreaterEqual(questions, 30)

    def test_consecutive_evenings_use_different_questions(self):
        def texts(day):
            quiz = QuizEvent.objects.get(event__date_time=_sep(day, 19))
            return set(
                QuizQuestion.objects.filter(round__quiz=quiz).values_list(
                    "text_en", flat=True
                )
            )

        # Sep 3 (classic) vs Sep 10 (media-love): disjoint sets.
        self.assertFalse(texts(3) & texts(10))
        # Sep 10 (media-love) vs Sep 17 (mettwoch): disjoint sets.
        self.assertFalse(texts(10) & texts(17))
        # Sep 17 (mettwoch) vs Sep 24 (classic): disjoint sets.
        self.assertFalse(texts(17) & texts(24))
        # Same pack reused Sep 3 / Sep 24: identical sets — fine, three weeks
        # apart, but assert it so a pack rotation change is a conscious act.
        self.assertEqual(texts(3), texts(24))

    def test_quiz_owner_recorded(self):
        for quiz in QuizEvent.objects.all():
            self.assertEqual(quiz.created_by, self.owner)

    def test_quiz_questions_are_trilingual(self):
        quiz = QuizEvent.objects.get(event__date_time=_sep(3, 19))
        questions = QuizQuestion.objects.filter(round__quiz=quiz)
        for q in questions:
            self.assertTrue(q.text_en and q.text_de and q.text_fr)


class IdempotencyAndFlagsTest(TestCase):
    """Re-runs are no-ops; flags behave as documented."""

    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="quiz-owner", email="quiz-owner@example.com", password="x"
        )

    def test_rerun_skips_existing_events(self):
        call_command("create_september_2026_events", "--quiz-owner", "quiz-owner")
        self.assertEqual(MeetupEvent.objects.count(), 8)
        call_command("create_september_2026_events", "--quiz-owner", "quiz-owner")
        self.assertEqual(MeetupEvent.objects.count(), 8)

    def test_rerun_does_not_duplicate_quizzes(self):
        call_command("create_september_2026_events", "--quiz-owner", "quiz-owner")
        call_command("create_september_2026_events", "--quiz-owner", "quiz-owner")
        self.assertEqual(QuizEvent.objects.count(), 4)

    def test_dry_run_writes_nothing(self):
        call_command(
            "create_september_2026_events",
            "--dry-run",
            "--quiz-owner",
            "quiz-owner",
        )
        self.assertEqual(MeetupEvent.objects.count(), 0)
        self.assertEqual(QuizEvent.objects.count(), 0)

    def test_publish_requires_full_address(self):
        with self.assertRaises(CommandError):
            call_command(
                "create_september_2026_events",
                "--publish",
                "--quiz-owner",
                "quiz-owner",
            )

    def test_publish_with_address_creates_published_events(self):
        call_command(
            "create_september_2026_events",
            "--publish",
            "--quiz-owner",
            "quiz-owner",
            "--location",
            "ATMOS",
            "--address-street",
            "place de la Gare",
            "--address-number",
            "1",
            "--address-postcode",
            "1616",
            "--address-town",
            "Luxembourg",
        )
        self.assertEqual(MeetupEvent.objects.filter(is_published=True).count(), 8)

    def test_no_gender_caps_flag(self):
        call_command(
            "create_september_2026_events",
            "--no-gender-caps",
            "--quiz-owner",
            "quiz-owner",
        )
        for day in (2, 9, 16, 23):
            sd = MeetupEvent.objects.get(date_time=_sep(day, 19))
            self.assertIsNone(sd.max_participants_m)
            self.assertIsNone(sd.max_participants_f)
            self.assertIsNone(sd.max_participants_nb)

    def test_unknown_quiz_owner_raises(self):
        with self.assertRaises(CommandError):
            call_command(
                "create_september_2026_events", "--quiz-owner", "ghost-user"
            )

    def test_banner_from_missing_event_raises(self):
        with self.assertRaises(CommandError):
            call_command(
                "create_september_2026_events", "--banner-from", "99999"
            )


class NoSuperuserQuizSkippedTest(TestCase):
    """Without any superuser and without --quiz-owner, quizzes are skipped."""

    def test_events_created_without_quizzes(self):
        call_command("create_september_2026_events")
        self.assertEqual(MeetupEvent.objects.count(), 8)
        self.assertEqual(QuizEvent.objects.count(), 0)
