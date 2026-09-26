"""
The journey API must only act on the caller's own journey.

``submit_challenge`` and ``unlock_puzzle_piece`` used to look their object up
by bare id, so any member with *a* journey could answer another journey's
challenge (and read its private ``success_message``) or unlock pieces of
another journey's photo puzzle. Both now scope the lookup to the caller's
journey, as ``unlock_hint`` already did, and answer a foreign id exactly like
a nonexistent one.

A member can hold more than one accessible journey (one linked experience,
one journey per type). Every call that names a challenge or reward plays that
object's own journey; everything else plays the Wonderland journey the map
shows. Both used to take the oldest progress row, so only one journey was
ever playable and the map and its chapters could disagree.
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
    UserDataConsent,
)

User = get_user_model()

# submit-challenge sits inside i18n_patterns; unlock-puzzle-piece is
# language-neutral (photo_reveal.html calls it by hardcoded path).
SUBMIT_URL = "/en/api/journey/submit-challenge/"
UNLOCK_PIECE_URL = "/api/journey/unlock-puzzle-piece/"
UNLOCK_HINT_URL = "/en/api/journey/unlock-hint/"
PROGRESS_URL = "/en/api/journey/progress/"
SAVE_STATE_URL = "/en/api/journey/save-state/"
HOST = "crush.lu"


def _make_member(name):
    """A member with their own linked, active special experience."""
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
    # consent_middleware 302s every crush page (not the API) without this.
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    experience = SpecialUserExperience.objects.create(
        first_name=name,
        last_name="Player",
        linked_user=user,
        is_active=True,
    )
    return user, experience


def _add_journey(
    user, experience, name, success_message, points, journey_type="wonderland"
):
    """A journey on ``experience`` with a challenge, a reward and ``user``'s
    progress row."""
    journey = JourneyConfiguration.objects.create(
        special_experience=experience,
        journey_type=journey_type,
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
        hint_1=f"{name}'s hint",
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
        user=user,
        journey=journey,
        chapter=chapter,
        challenge=challenge,
        reward=reward,
        progress=progress,
    )


def _make_player(name, success_message, points):
    """A member with their own linked experience, journey, challenge and reward."""
    user, experience = _make_member(name)
    return _add_journey(user, experience, name, success_message, points)


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

    # --- get_reward_progress ----------------------------------------------

    def test_foreign_reward_progress_is_indistinguishable_from_missing(self):
        """Neither may echo the caller's points against someone else's id."""
        self.client.force_login(self.bob.user)
        missing_id = JourneyReward.objects.order_by("-id").first().id + 1000

        foreign = self.client.get(
            f"/api/journey/reward-progress/{self.alice.reward.id}/", HTTP_HOST=HOST
        )
        missing = self.client.get(
            f"/api/journey/reward-progress/{missing_id}/", HTTP_HOST=HOST
        )

        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.json(), missing.json())
        self.assertNotIn("current_points", foreign.json())

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


class MultiJourneyScopingTests(TestCase):
    """One member, two accessible journeys on their one linked experience.

    The custom journey's row is created first, so an unscoped ``.first()``
    resolves it while the map shows the Wonderland journey: the case Codex
    raised on #1030. Distinct point balances show which row each call used.
    """

    def setUp(self):
        cache.clear()
        user, experience = _make_member("Carol")
        self.custom = _add_journey(
            user,
            experience,
            "Custom",
            "Custom secret",
            points=300,
            journey_type="custom",
        )
        self.wonderland = _add_journey(
            user, experience, "Wonderland", "Wonderland secret", points=500
        )
        self.assertLess(self.custom.progress.pk, self.wonderland.progress.pk)
        self.client.force_login(user)

    def _post(self, url, payload):
        return self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_HOST=HOST,
        )

    def _points(self, played):
        played.progress.refresh_from_db()
        return played.progress.total_points

    # --- API calls that name a challenge or reward ------------------------

    def test_answers_challenges_in_both_journeys(self):
        # (journey, its total after a correct answer, the other one's total)
        cases = (
            (self.wonderland, 600, self.custom, 300),
            (self.custom, 400, self.wonderland, 600),
        )
        for played, total, other, other_total in cases:
            with self.subTest(journey=played.journey.journey_type):
                response = self._post(
                    SUBMIT_URL, {"challenge_id": played.challenge.id, "answer": "4"}
                )

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertTrue(body["is_correct"])
                self.assertEqual(
                    body["success_message"], played.challenge.success_message
                )
                self.assertEqual(body["total_points"], total)
                self.assertEqual(self._points(played), total)
                self.assertEqual(self._points(other), other_total)
                self.assertTrue(
                    ChallengeAttempt.objects.filter(
                        chapter_progress__journey_progress=played.progress,
                        chapter_progress__chapter=played.chapter,
                        challenge=played.challenge,
                        is_correct=True,
                    ).exists()
                )

    def test_unlocks_pieces_in_both_journeys(self):
        # (journey, its balance after one piece, the other one's balance)
        cases = (
            (self.wonderland, 450, self.custom, 300),
            (self.custom, 250, self.wonderland, 450),
        )
        for played, remaining, other, other_total in cases:
            with self.subTest(journey=played.journey.journey_type):
                response = self._post(
                    UNLOCK_PIECE_URL, {"reward_id": played.reward.id, "piece_index": 5}
                )

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertTrue(body["success"])
                self.assertEqual(body["unlocked_pieces"], [5])
                self.assertEqual(body["points_remaining"], remaining)
                self.assertEqual(self._points(played), remaining)
                self.assertEqual(self._points(other), other_total)
                reward_progress = RewardProgress.objects.get(
                    journey_progress=played.progress, reward=played.reward
                )
                self.assertEqual(reward_progress.unlocked_pieces, [5])

    def test_reward_progress_reads_the_rewards_own_journey(self):
        for played in (self.custom, self.wonderland):
            with self.subTest(journey=played.journey.journey_type):
                response = self.client.get(
                    f"/api/journey/reward-progress/{played.reward.id}/",
                    HTTP_HOST=HOST,
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json()["current_points"], played.progress.total_points
                )

    def test_hint_is_recorded_on_the_challenges_own_journey(self):
        for played in (self.custom, self.wonderland):
            with self.subTest(journey=played.journey.journey_type):
                response = self._post(
                    UNLOCK_HINT_URL,
                    {"challenge_id": played.challenge.id, "hint_number": 1},
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["hint_text"], played.challenge.hint_1)
                attempt = ChallengeAttempt.objects.get(challenge=played.challenge)
                self.assertEqual(
                    attempt.chapter_progress.journey_progress, played.progress
                )
                self.assertEqual(attempt.hints_used, [1])

    # --- pages ------------------------------------------------------------

    def test_chapter_page_and_progress_follow_the_map(self):
        """No challenge or reward is named, so both play the Wonderland
        journey the map shows, not the older custom row."""
        chapter = self.client.get("/en/journey/chapter/1/", HTTP_HOST=HOST)
        progress = self.client.get(PROGRESS_URL, HTTP_HOST=HOST)

        self.assertEqual(chapter.status_code, 200)
        self.assertEqual(chapter.context["journey_progress"], self.wonderland.progress)
        self.assertEqual(chapter.context["chapter"], self.wonderland.chapter)
        self.assertEqual(
            progress.json()["data"]["journey_name"],
            self.wonderland.journey.journey_name,
        )

    def test_challenge_page_uses_the_challenges_own_journey(self):
        for played in (self.custom, self.wonderland):
            ChapterProgress.objects.create(
                journey_progress=played.progress, chapter=played.chapter
            )
            with self.subTest(journey=played.journey.journey_type):
                response = self.client.get(
                    f"/en/journey/chapter/1/challenge/{played.challenge.id}/",
                    HTTP_HOST=HOST,
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["journey_progress"], played.progress)
                self.assertEqual(response.context["challenge"], played.challenge)

    def test_challenge_page_rejects_a_mismatched_chapter_number(self):
        """The challenge is now found by id across journeys, so the chapter
        number in the URL must still be checked: without it this chapter-1
        challenge would render at /chapter/2/."""
        ChapterProgress.objects.create(
            journey_progress=self.custom.progress, chapter=self.custom.chapter
        )

        response = self.client.get(
            f"/en/journey/chapter/2/challenge/{self.custom.challenge.id}/",
            HTTP_HOST=HOST,
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(b"What is 2+2?", response.content)

    def test_certificate_follows_the_map(self):
        """A completed custom journey does not open the Wonderland map's
        certificate; the completed Wonderland journey does."""
        self.custom.progress.is_completed = True
        self.custom.progress.save()

        before = self.client.get("/en/journey/certificate/", HTTP_HOST=HOST)
        self.assertEqual(before.status_code, 302)

        self.wonderland.progress.is_completed = True
        self.wonderland.progress.save()

        after = self.client.get("/en/journey/certificate/", HTTP_HOST=HOST)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.context["journey"], self.wonderland.journey)

    def test_selector_lists_custom_and_wonderland_journeys(self):
        """Journeys sort by type, so the custom row is fetched before the
        Wonderland card's description is translated. A selector that rebinds
        ``_`` while fetching that row crashes there (Codex on #1034)."""
        response = self.client.get("/en/journey/select/", HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 200)
        entries = {e["journey"].pk: e for e in response.context["journeys"]}
        custom = entries[self.custom.journey.pk]
        self.assertEqual(custom["journey_id"], self.custom.journey.pk)
        self.assertEqual(custom["chapter_number"], 1)
        self.assertIsNone(custom["url"])
        wonderland = entries[self.wonderland.journey.pk]
        self.assertEqual(wonderland["url"], "crush_lu:journey_map_wonderland")
        self.assertIsNone(wonderland["journey_id"])

    # --- deactivated journeys ---------------------------------------------

    def test_deactivated_journey_answers_like_a_missing_one(self):
        """The selector and map hide a deactivated journey, so its ids must
        not stay playable by bookmark or guess (Codex on #1034)."""
        ChapterProgress.objects.create(
            journey_progress=self.custom.progress, chapter=self.custom.chapter
        )
        self.custom.journey.is_active = False
        self.custom.journey.save()
        missing_challenge = JourneyChallenge.objects.order_by("-id").first().id + 1000
        missing_reward = JourneyReward.objects.order_by("-id").first().id + 1000

        for answer in ("4", ""):
            with self.subTest(answer=answer):
                closed = self._post(
                    SUBMIT_URL,
                    {"challenge_id": self.custom.challenge.id, "answer": answer},
                )
                missing = self._post(
                    SUBMIT_URL, {"challenge_id": missing_challenge, "answer": answer}
                )
                self.assertEqual(closed.status_code, 404)
                self.assertEqual(closed.json(), missing.json())

        for url, payload, missing_payload in (
            (
                UNLOCK_HINT_URL,
                {"challenge_id": self.custom.challenge.id, "hint_number": 1},
                {"challenge_id": missing_challenge, "hint_number": 1},
            ),
            (
                UNLOCK_PIECE_URL,
                {"reward_id": self.custom.reward.id, "piece_index": 0},
                {"reward_id": missing_reward, "piece_index": 0},
            ),
        ):
            with self.subTest(url=url):
                closed = self._post(url, payload)
                missing = self._post(url, missing_payload)
                self.assertEqual(closed.status_code, 404)
                self.assertEqual(closed.json(), missing.json())

        reward_progress = self.client.get(
            f"/api/journey/reward-progress/{self.custom.reward.id}/", HTTP_HOST=HOST
        )
        challenge_page = self.client.get(
            f"/en/journey/chapter/1/challenge/{self.custom.challenge.id}/",
            HTTP_HOST=HOST,
        )
        self.assertEqual(reward_progress.status_code, 404)
        self.assertEqual(challenge_page.status_code, 302)
        self.assertNotIn(b"What is 2+2?", challenge_page.content)
        self.assertEqual(self._points(self.custom), 300)
        self.assertFalse(ChallengeAttempt.objects.exists())
        self.assertFalse(RewardProgress.objects.exists())

        # The active Wonderland journey is unaffected
        response = self._post(
            SUBMIT_URL, {"challenge_id": self.wonderland.challenge.id, "answer": "4"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._points(self.wonderland), 600)

    def test_deactivated_wonderland_closes_the_chapter_pages(self):
        self.wonderland.journey.is_active = False
        self.wonderland.journey.save()

        chapter = self.client.get("/en/journey/chapter/1/", HTTP_HOST=HOST)
        progress = self.client.get(PROGRESS_URL, HTTP_HOST=HOST)

        self.assertEqual(chapter.status_code, 302)
        self.assertEqual(progress.status_code, 404)

    # --- save_state -------------------------------------------------------

    def _time(self, played):
        played.progress.refresh_from_db()
        return played.progress.total_time_seconds

    def test_pages_render_their_own_journey_for_the_timer(self):
        ChapterProgress.objects.create(
            journey_progress=self.custom.progress, chapter=self.custom.chapter
        )

        response = self.client.get(
            f"/en/journey/chapter/1/challenge/{self.custom.challenge.id}/",
            HTTP_HOST=HOST,
        )

        self.assertContains(response, f'data-journey-id="{self.custom.journey.id}"')

    def test_save_state_credits_the_journey_the_page_shows(self):
        # The timer's JSON save and its form-encoded unload beacon
        json_save = self._post(
            SAVE_STATE_URL, {"time_increment": 30, "journey_id": self.custom.journey.id}
        )
        beacon = self.client.post(
            SAVE_STATE_URL,
            {"time_increment": 20, "journey_id": str(self.custom.journey.id)},
            HTTP_HOST=HOST,
        )

        self.assertEqual(json_save.status_code, 200)
        self.assertEqual(beacon.status_code, 200)
        self.assertEqual(beacon.json()["total_time"], 50)
        self.assertEqual(self._time(self.custom), 50)
        self.assertEqual(self._time(self.wonderland), 0)

    def test_save_state_without_a_journey_credits_the_map_journey(self):
        response = self._post(SAVE_STATE_URL, {"time_increment": 30})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._time(self.wonderland), 30)
        self.assertEqual(self._time(self.custom), 0)

    def test_save_state_refuses_a_journey_the_member_cannot_play(self):
        outsider = _make_player("Dave", "Dave's private message", points=0)
        self.custom.journey.is_active = False
        self.custom.journey.save()

        for journey_id in (
            outsider.journey.id,  # someone else's journey
            self.custom.journey.id,  # deactivated
            "not-a-number",
        ):
            with self.subTest(journey_id=journey_id):
                response = self._post(
                    SAVE_STATE_URL, {"time_increment": 30, "journey_id": journey_id}
                )
                self.assertEqual(response.status_code, 404)
                self.assertFalse(response.json()["success"])

        self.assertEqual(self._time(self.custom), 0)
        self.assertEqual(self._time(self.wonderland), 0)
        outsider.progress.refresh_from_db()
        self.assertEqual(outsider.progress.total_time_seconds, 0)
