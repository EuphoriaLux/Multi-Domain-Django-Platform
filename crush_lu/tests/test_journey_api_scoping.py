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
from django.core import mail
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
FINAL_RESPONSE_URL = "/en/api/journey/final-response/"
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


class LastChapterFinalQuestionTests(TestCase):
    """The final question completes a journey, so it belongs on the journey's
    own last chapter (``total_chapters``), not on a hardcoded Chapter 6.

    Chapter 6 is Wonderland's last chapter. A custom journey can be shorter or
    longer: the hardcode left a 4-chapter one uncompletable and completed an
    8-chapter one with two chapters unplayed (Codex on #1034). The endpoint
    also refuses an answer until that last chapter is done.
    """

    def setUp(self):
        cache.clear()
        user, experience = _make_member("Erin")
        self.custom = _add_journey(
            user,
            experience,
            "Custom",
            "Custom secret",
            points=0,
            journey_type="custom",
        )
        self.wonderland = _add_journey(
            user, experience, "Wonderland", "Wonderland secret", points=0
        )
        self.client.force_login(user)

    def _chapters(self, played, total, completed_through):
        """Give ``played`` ``total`` chapters with the first
        ``completed_through`` of them done."""
        played.journey.total_chapters = total
        played.journey.save()
        for number in range(2, total + 1):
            JourneyChapter.objects.create(
                journey=played.journey,
                chapter_number=number,
                title=f"Chapter {number}",
                theme="Mystery",
                story_introduction="Once upon a time",
                completion_message="Well done",
            )
        for chapter in JourneyChapter.objects.filter(
            journey=played.journey, chapter_number__lte=completed_through
        ):
            ChapterProgress.objects.create(
                journey_progress=played.progress, chapter=chapter, is_completed=True
            )

    def _custom_page(self, number):
        return self.client.get(
            f"/en/journey/chapter/{number}/?journey_id={self.custom.journey.pk}",
            HTTP_HOST=HOST,
        )

    def _answer(self, query=""):
        return self.client.post(
            f"{FINAL_RESPONSE_URL}{query}",
            data=json.dumps({"response": "yes"}),
            content_type="application/json",
            HTTP_HOST=HOST,
        )

    def _assert_not_completed(self, played):
        played.progress.refresh_from_db()
        self.assertFalse(played.progress.is_completed)
        self.assertIsNone(played.progress.completed_at)
        self.assertEqual(played.progress.final_response, "")

    def test_short_custom_journey_asks_on_its_last_chapter(self):
        self._chapters(self.custom, total=4, completed_through=4)
        query = f"?journey_id={self.custom.journey.pk}"

        page = self._custom_page(4)

        # chapter_view turns a template error into a 302, so check 200 first
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="finalResponseSection"')
        self.assertContains(page, f'data-submit-url="{FINAL_RESPONSE_URL}{query}"')

        answer = self._answer(query)

        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.json()["success"])
        self.custom.progress.refresh_from_db()
        self.assertTrue(self.custom.progress.is_completed)
        self.assertEqual(self.custom.progress.final_response, "yes")
        self.assertEqual(len(mail.outbox), 1)
        self._assert_not_completed(self.wonderland)

        answered = self._custom_page(4)
        self.assertEqual(answered.status_code, 200)
        self.assertNotContains(answered, 'id="finalResponseSection"')
        self.assertContains(answered, "You chose:")

    def test_custom_journey_does_not_ask_before_its_last_chapter(self):
        for total, number in ((4, 3), (8, 6)):
            with self.subTest(total_chapters=total, chapter=number):
                ChapterProgress.objects.all().delete()
                JourneyChapter.objects.filter(
                    journey=self.custom.journey, chapter_number__gt=1
                ).delete()
                self._chapters(self.custom, total=total, completed_through=number)

                page = self._custom_page(number)

                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "Chapter Complete!")
                self.assertContains(page, "Next Chapter")
                self.assertNotContains(page, 'id="finalResponseSection"')

    def test_answer_is_refused_before_the_last_chapter_is_done(self):
        # Custom: Chapter 6 done, the journey's last chapter (8) is not.
        self._chapters(self.custom, total=8, completed_through=6)
        # Wonderland: Chapter 5 done, its last chapter (6) is not.
        self._chapters(self.wonderland, total=6, completed_through=5)

        cases = (
            (self.custom, f"?journey_id={self.custom.journey.pk}", 8),
            (self.wonderland, "", 6),
        )
        for played, query, last in cases:
            with self.subTest(journey=played.journey.journey_type):
                response = self._answer(query)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {
                        "success": False,
                        "message": f"Please complete Chapter {last} first.",
                    },
                )
                self._assert_not_completed(played)
        self.assertEqual(len(mail.outbox), 0)

    def test_wonderland_still_asks_on_chapter_six(self):
        self._chapters(self.wonderland, total=6, completed_through=6)

        page = self.client.get("/en/journey/chapter/6/", HTTP_HOST=HOST)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="finalResponseSection"')
        self.assertContains(page, f'data-submit-url="{FINAL_RESPONSE_URL}"')

        answer = self._answer()

        self.assertEqual(answer.status_code, 200)
        self.wonderland.progress.refresh_from_db()
        self.assertTrue(self.wonderland.progress.is_completed)
        self.assertEqual(self.wonderland.progress.final_response, "yes")
        self._assert_not_completed(self.custom)

    def test_journey_id_only_names_a_custom_journey(self):
        """Same scope as the chapter page: the Wonderland journey is answered
        without a journey_id, never through one. Its last chapter is done, so
        only the journey-type scope refuses this."""
        self._chapters(self.wonderland, total=6, completed_through=6)

        response = self._answer(f"?journey_id={self.wonderland.journey.pk}")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self._assert_not_completed(self.wonderland)
        self.assertEqual(len(mail.outbox), 0)


class QuestionnaireChapterTests(TestCase):
    """Chapters 2, 4 and 5 are questionnaires in Wonderland only.

    submit_challenge accepted any answer in those chapters of every journey.
    Once #1034 made custom journeys playable, a custom journey's Chapter 2
    riddle took any answer for full points (Codex on #1034). A custom journey
    still gets questionnaire mode from a blank correct_answer or an
    open_text / would_you_rather challenge.
    """

    def setUp(self):
        cache.clear()
        user, experience = _make_member("Frank")
        self.custom = _add_journey(
            user,
            experience,
            "Custom",
            "Custom secret",
            points=0,
            journey_type="custom",
        )
        self.wonderland = _add_journey(
            user, experience, "Wonderland", "Wonderland secret", points=0
        )
        self.client.force_login(user)

    def _challenge(self, played, number, **fields):
        """A new challenge in Chapter ``number`` of ``played``'s journey. By
        default it is Chapter 1's riddle: correct_answer "4", 100 points."""
        chapter = JourneyChapter.objects.get_or_create(
            journey=played.journey,
            chapter_number=number,
            defaults={
                "title": f"Chapter {number}",
                "theme": "Mystery",
                "story_introduction": "Once upon a time",
                "completion_message": "Well done",
            },
        )[0]
        values = {
            "challenge_type": "riddle",
            "question": "What is 2+2?",
            "correct_answer": "4",
            "points_awarded": 100,
            "success_message": f"Chapter {number} secret",
        }
        values.update(fields)
        return JourneyChallenge.objects.create(
            chapter=chapter,
            challenge_order=chapter.challenges.count() + 1,
            **values,
        )

    def _submit(self, challenge, answer):
        return self.client.post(
            SUBMIT_URL,
            data=json.dumps({"challenge_id": challenge.id, "answer": answer}),
            content_type="application/json",
            HTTP_HOST=HOST,
        )

    def _points(self, played):
        played.progress.refresh_from_db()
        return played.progress.total_points

    def test_custom_journey_checks_answers_in_chapters_2_4_and_5(self):
        for number in (2, 4, 5):
            with self.subTest(chapter=number):
                challenge = self._challenge(self.custom, number)
                before = self._points(self.custom)

                wrong = self._submit(challenge, "banana")

                self.assertEqual(wrong.status_code, 200)
                body = wrong.json()
                self.assertTrue(body["success"])
                self.assertFalse(body["is_correct"])
                self.assertNotIn("success_message", body)
                self.assertNotIn("points_earned", body)
                attempt = ChallengeAttempt.objects.get(challenge=challenge)
                self.assertFalse(attempt.is_correct)
                self.assertEqual(attempt.points_earned, 0)
                self.assertEqual(self._points(self.custom), before)

                # A quiz, not a dead end: the right answer still scores.
                right = self._submit(challenge, "4")

                self.assertEqual(right.status_code, 200)
                body = right.json()
                self.assertTrue(body["is_correct"])
                self.assertEqual(body["points_earned"], 100)
                self.assertEqual(body["success_message"], f"Chapter {number} secret")
                self.assertEqual(self._points(self.custom), before + 100)

    def test_wonderland_chapters_2_4_and_5_stay_questionnaires(self):
        for number in (2, 4, 5):
            with self.subTest(chapter=number):
                challenge = self._challenge(self.wonderland, number)
                before = self._points(self.wonderland)

                response = self._submit(challenge, "banana")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertTrue(body["is_correct"])
                self.assertEqual(body["points_earned"], 100)
                self.assertEqual(self._points(self.wonderland), before + 100)
                self.assertTrue(
                    ChallengeAttempt.objects.get(challenge=challenge).is_correct
                )

    def test_custom_journey_keeps_questionnaire_mode_without_the_chapter_rule(self):
        cases = (
            ("blank correct_answer", {"correct_answer": ""}),
            ("open_text", {"challenge_type": "open_text"}),
            ("would_you_rather", {"challenge_type": "would_you_rather"}),
        )
        for label, fields in cases:
            with self.subTest(label):
                challenge = self._challenge(self.custom, 2, **fields)
                before = self._points(self.custom)

                response = self._submit(challenge, "banana")

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertTrue(body["is_correct"])
                self.assertEqual(body["points_earned"], 100)
                self.assertEqual(self._points(self.custom), before + 100)
