"""
The journey API must only act on the caller's own journey.

``submit_challenge`` and ``unlock_puzzle_piece`` used to look their object up
by bare id, so any member with *a* journey could answer another journey's
challenge (and read its private ``success_message``) or unlock pieces of
another journey's photo puzzle. Both now scope the lookup to the caller's
journey, as ``unlock_hint`` already did, and answer a foreign id exactly like
a nonexistent one.
"""

import json
from datetime import date
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from crush_lu.models import (
    ChallengeAttempt,
    ChapterProgress,
    CrushProfile,
    JourneyChallenge,
    JourneyChapter,
    JourneyConfiguration,
    JourneyProgress,
    JourneyReward,
    RewardProgress,
    SpecialUserExperience,
)

User = get_user_model()

# submit-challenge sits inside i18n_patterns; unlock-puzzle-piece is
# language-neutral (photo_reveal.html calls it by hardcoded path).
SUBMIT_URL = "/en/api/journey/submit-challenge/"
UNLOCK_PIECE_URL = "/api/journey/unlock-puzzle-piece/"
HOST = "crush.lu"


def _make_player(name, success_message, points):
    """A member with their own linked experience, journey, challenge and reward."""
    user = User.objects.create_user(
        username=f"{name.lower()}@example.com",
        email=f"{name.lower()}@example.com",
        password="testpass123",
        first_name=name,
        last_name="Player",
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="M",
        location="Luxembourg",
        is_approved=True,
    )
    experience = SpecialUserExperience.objects.create(
        first_name=name,
        last_name="Player",
        linked_user=user,
        is_active=True,
    )
    journey = JourneyConfiguration.objects.create(
        special_experience=experience,
        journey_name=f"{name}'s Journey",
        total_chapters=1,
        is_active=True,
    )
    chapter = JourneyChapter.objects.create(
        journey=journey,
        chapter_number=1,
        title=f"{name}'s Chapter",
        theme="Mystery",
        story_introduction="Once upon a time",
        completion_message="Well done",
    )
    # Chapter 1 riddle with a correct answer: quiz mode, so a right answer is
    # the only way to get success_message back.
    challenge = JourneyChallenge.objects.create(
        chapter=chapter,
        challenge_order=1,
        challenge_type="riddle",
        question="What is 2+2?",
        correct_answer="4",
        points_awarded=100,
        success_message=success_message,
    )
    reward = JourneyReward.objects.create(
        chapter=chapter,
        reward_type="photo_reveal",
        title=f"{name}'s Photo",
    )
    progress = JourneyProgress.objects.create(
        user=user, journey=journey, current_chapter=1, total_points=points
    )
    return SimpleNamespace(
        user=user, challenge=challenge, reward=reward, progress=progress
    )


class JourneyAPIScopingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = _make_player("Alice", "Alice's private message", points=0)
        self.bob = _make_player("Bob", "Bob's private message", points=500)

    def _post(self, url, payload):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HOST=HOST,
        )

    def _submit(self, challenge_id, answer):
        return self._post(SUBMIT_URL, {"challenge_id": challenge_id, "answer": answer})

    def _unlock(self, reward_id, piece_index=0):
        return self._post(
            UNLOCK_PIECE_URL, {"reward_id": reward_id, "piece_index": piece_index}
        )

    # --- submit_challenge -------------------------------------------------

    def test_cannot_answer_another_journeys_challenge(self):
        self.client.force_login(self.bob.user)

        response = self._submit(self.alice.challenge.id, "4")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertNotIn("success_message", body)
        self.assertNotIn(b"Alice's private message", response.content)
        self.assertEqual(ChallengeAttempt.objects.count(), 0)
        self.assertEqual(ChapterProgress.objects.count(), 0)
        self.bob.progress.refresh_from_db()
        self.assertEqual(self.bob.progress.total_points, 500)

    def test_foreign_challenge_is_indistinguishable_from_missing(self):
        """An empty answer must not reach format validation for a foreign id,
        or its 400 would tell the caller the id exists."""
        self.client.force_login(self.bob.user)
        missing_id = JourneyChallenge.objects.order_by("-id").first().id + 1000

        for answer in ("4", ""):
            with self.subTest(answer=answer):
                foreign = self._submit(self.alice.challenge.id, answer)
                missing = self._submit(missing_id, answer)

                self.assertEqual(foreign.status_code, missing.status_code)
                self.assertEqual(foreign.json(), missing.json())

    def test_members_can_still_answer_their_own_challenge(self):
        for player in (self.alice, self.bob):
            with self.subTest(player=player.user.first_name):
                self.client.force_login(player.user)

                response = self._submit(player.challenge.id, "4")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertTrue(body["is_correct"])
                self.assertEqual(
                    body["success_message"], player.challenge.success_message
                )
                self.assertTrue(
                    ChallengeAttempt.objects.filter(
                        chapter_progress__journey_progress=player.progress,
                        challenge=player.challenge,
                        is_correct=True,
                    ).exists()
                )

    # --- unlock_puzzle_piece ----------------------------------------------

    def test_cannot_unlock_another_journeys_puzzle_piece(self):
        self.client.force_login(self.bob.user)

        response = self._unlock(self.alice.reward.id)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.assertEqual(RewardProgress.objects.count(), 0)
        self.bob.progress.refresh_from_db()
        self.assertEqual(self.bob.progress.total_points, 500)

    def test_foreign_reward_is_indistinguishable_from_missing(self):
        self.client.force_login(self.bob.user)
        missing_id = JourneyReward.objects.order_by("-id").first().id + 1000

        foreign = self._unlock(self.alice.reward.id)
        missing = self._unlock(missing_id)

        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.json(), missing.json())

    def test_member_can_still_unlock_their_own_puzzle_piece(self):
        self.client.force_login(self.bob.user)

        response = self._unlock(self.bob.reward.id, piece_index=3)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["unlocked_pieces"], [3])
        self.assertEqual(body["points_remaining"], 450)
        reward_progress = RewardProgress.objects.get(
            journey_progress=self.bob.progress, reward=self.bob.reward
        )
        self.assertEqual(reward_progress.unlocked_pieces, [3])
