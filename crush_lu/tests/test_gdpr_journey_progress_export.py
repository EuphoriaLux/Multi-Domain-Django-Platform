"""Own journey progress scalars are portable without exposing journey resources."""

import json
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from crush_lu.models import (
    ChallengeAttempt,
    ChapterProgress,
    CrushProfile,
    JourneyChallenge,
    JourneyChapter,
    JourneyConfiguration,
    JourneyGift,
    JourneyProgress,
    JourneyReward,
    SpecialUserExperience,
    UserDataConsent,
)

User = get_user_model()
FIELDS = {
    "current_chapter",
    "total_points",
    "total_time_seconds",
    "is_completed",
    "completed_at",
    "started_at",
    "last_activity",
    "final_response",
    "final_response_at",
}


class ExportJourneyProgressTests(TestCase):
    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.user = User.objects.create_user(
            "progress-owner", "progress-owner@example.com", "test-password"
        )
        self.other = User.objects.create_user(
            "other-progress-owner", "other-progress-owner@example.com", "test-password"
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            phone_verified=True,
            is_approved=True,
            welcome_seen_at=self.now,
        )
        self.experience = SpecialUserExperience.objects.create(
            first_name="PRIVATE-EXPERIENCE",
            last_name="PRIVATE-PERSON",
            linked_user=self.user,
            auto_approve_profile=False,
        )
        self.other_experience = SpecialUserExperience.objects.create(
            first_name="OTHER-EXPERIENCE",
            last_name="OTHER-PERSON",
            linked_user=self.other,
            auto_approve_profile=False,
        )
        self.client.force_login(self.user)

    def _export(self, lang="en"):
        response = self.client.get(
            f"/{lang}/account/gdpr/export/", HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(
            response["Content-Disposition"],
            f'attachment; filename="crush_lu_data_{self.user.pk}.json"',
        )
        return response.json()

    def _journey(self, *, experience=None, journey_type="wonderland", active=True):
        return JourneyConfiguration.objects.create(
            special_experience=experience or self.experience,
            journey_type=journey_type,
            is_active=active,
            journey_name="PRIVATE-JOURNEY-NAME",
            final_message="PRIVATE-FINAL-MESSAGE",
            location_first_met="PRIVATE-LOCATION",
        )

    def _progress(self, journey=None, user=None, **fields):
        return JourneyProgress.objects.create(
            user=user or self.user,
            journey=journey or self._journey(),
            **fields,
        )

    def test_exports_only_approved_scalars_with_completed_dates_and_raw_response(self):
        progress = self._progress(
            current_chapter=6,
            total_points=321,
            total_time_seconds=1234,
            is_completed=True,
            completed_at=self.now,
            final_response="yes",
            final_response_at=self.now,
        )
        self.assertEqual(
            self._export()["journey_progress"],
            [
                {
                    "current_chapter": 6,
                    "total_points": 321,
                    "total_time_seconds": 1234,
                    "is_completed": True,
                    "completed_at": self.now.isoformat(),
                    "started_at": progress.started_at.isoformat(),
                    "last_activity": progress.last_activity.isoformat(),
                    "final_response": "yes",
                    "final_response_at": self.now.isoformat(),
                }
            ],
        )

    def test_zero_false_blank_response_and_null_dates_are_preserved(self):
        self._progress(current_chapter=0)
        exported = self._export()["journey_progress"][0]
        self.assertEqual(set(exported), FIELDS)
        for name in ("current_chapter", "total_points", "total_time_seconds"):
            self.assertEqual(exported[name], 0)
        self.assertIs(exported["is_completed"], False)
        self.assertIsNone(exported["completed_at"])
        self.assertIsNone(exported["final_response_at"])
        self.assertEqual(exported["final_response"], "")

    def test_inactive_and_namesake_history_is_owned_tracking_not_playback_access(self):
        inactive = self._progress(
            journey=self._journey(active=False), current_chapter=2
        )
        namesake = self._progress(
            journey=self._journey(experience=self.other_experience), current_chapter=3
        )
        self._progress(journey=inactive.journey, user=self.other, current_chapter=99)
        self._progress(journey=namesake.journey, user=self.other, current_chapter=98)
        self.assertEqual(list(JourneyProgress.accessible_to(self.user)), [])
        exported = self._export()["journey_progress"]
        self.assertEqual([row["current_chapter"] for row in exported], [2, 3])
        self.assertEqual(list(JourneyProgress.accessible_to(self.user)), [])
        for row in exported:
            self.assertEqual(set(row), FIELDS)

    def test_empty_collection_when_only_other_members_have_progress(self):
        self._progress(user=self.other)
        self.assertEqual(self._export()["journey_progress"], [])

    def test_profileless_member_without_progress_also_gets_empty_collection(self):
        self.profile.delete()
        data = self._export()
        self.assertNotIn("profile", data)
        self.assertEqual(data["journey_progress"], [])

    def test_started_time_then_pk_order_is_deterministic_with_ties(self):
        first = self._progress(current_chapter=1)
        second = self._progress(
            journey=self._journey(journey_type="advent_calendar"), current_chapter=2
        )
        third = self._progress(
            journey=self._journey(journey_type="custom"), current_chapter=3
        )
        JourneyProgress.objects.filter(pk=first.pk).update(
            started_at=self.now + timedelta(days=1)
        )
        JourneyProgress.objects.filter(pk__in=[second.pk, third.pk]).update(
            started_at=self.now
        )
        exported = self._export()["journey_progress"]
        self.assertEqual([row["current_chapter"] for row in exported], [2, 3, 1])
        self.assertEqual(exported, self._export()["journey_progress"])

    def test_personalized_resources_child_answers_gifts_and_media_are_excluded(self):
        progress = self._progress()
        chapter = JourneyChapter.objects.create(
            journey=progress.journey,
            chapter_number=1,
            title="PRIVATE-CHAPTER",
            theme="PRIVATE-THEME",
            story_introduction="PRIVATE-STORY",
        )
        challenge = JourneyChallenge.objects.create(
            chapter=chapter,
            challenge_order=1,
            challenge_type="riddle",
            question="PRIVATE-QUESTION",
            correct_answer="PRIVATE-CORRECT-ANSWER",
            success_message="PRIVATE-SUCCESS",
        )
        completion = ChapterProgress.objects.create(
            journey_progress=progress, chapter=chapter
        )
        ChallengeAttempt.objects.create(
            chapter_progress=completion,
            challenge=challenge,
            user_answer="PRIVATE-CHILD-ANSWER",
        )
        JourneyReward.objects.create(
            chapter=chapter,
            reward_type="photo_reveal",
            title="PRIVATE-REWARD",
            message="PRIVATE-POEM",
            photo="private/PRIVATE-MEDIA.jpg",
        )
        # Seed a gift without its save-time QR/media generation side effects.
        JourneyGift.objects.bulk_create(
            [
                JourneyGift(
                    sender=self.other,
                    claimed_by=self.user,
                    journey=progress.journey,
                    special_experience=self.experience,
                    gift_code="WOY-SECRET1234",
                    recipient_name="PRIVATE-RECIPIENT",
                    recipient_email="private-recipient@example.com",
                    sender_message="PRIVATE-GIFT-MESSAGE",
                    date_first_met=date(2020, 1, 1),
                    location_first_met="PRIVATE-GIFT-LOCATION",
                )
            ]
        )
        data = self._export()
        self.assertEqual(set(data["journey_progress"][0]), FIELDS)
        body = json.dumps(data)
        for excluded in (
            "PRIVATE-",
            "OTHER-EXPERIENCE",
            "OTHER-PERSON",
            "WOY-SECRET1234",
            self.other.email,
            "private-recipient@example.com",
        ):
            self.assertNotIn(excluded, body)

    def test_projection_has_no_resource_join_and_export_does_not_write(self):
        self._progress()
        self._export()  # Warm Site/auth activity bookkeeping before SQL capture.
        before = list(JourneyProgress.objects.order_by("pk").values())
        config_before = list(JourneyConfiguration.objects.order_by("pk").values())
        experiences_before = list(SpecialUserExperience.objects.order_by("pk").values())
        with CaptureQueriesContext(connection) as queries:
            self._export()
        progress_queries = [
            q["sql"] for q in queries if 'FROM "crush_lu_journeyprogress"' in q["sql"]
        ]
        self.assertEqual(len(progress_queries), 1)
        sql = progress_queries[0]
        self.assertNotIn("JOIN", sql)
        selected = sql.split(" FROM ")[0]
        for column in ("id", "user_id", "journey_id"):
            self.assertNotIn(f'"crush_lu_journeyprogress"."{column}"', selected)
        self.assertFalse(
            any(q["sql"].startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)
        )
        self.assertEqual(before, list(JourneyProgress.objects.order_by("pk").values()))
        self.assertEqual(
            config_before, list(JourneyConfiguration.objects.order_by("pk").values())
        )
        self.assertEqual(
            experiences_before,
            list(SpecialUserExperience.objects.order_by("pk").values()),
        )

    def test_raw_final_response_code_is_language_independent(self):
        self._progress(final_response="thinking")
        for lang in ("en", "de", "fr"):
            with self.subTest(language=lang):
                exported = self._export(lang)["journey_progress"][0]
                self.assertEqual(exported["final_response"], "thinking")
                self.assertIsNone(exported["final_response_at"])

    def test_existing_sections_are_unchanged_when_progress_is_added(self):
        before = self._export()
        self._progress()
        after = self._export()
        self.assertEqual(before.pop("journey_progress"), [])
        self.assertEqual(len(after.pop("journey_progress")), 1)
        before.pop("export_date")
        after.pop("export_date")
        self.assertEqual(before, after)

    def test_anonymous_request_keeps_login_gate(self):
        self._progress()
        self.client.logout()
        response = self.client.get("/en/account/gdpr/export/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("journey_progress", response.content.decode())
