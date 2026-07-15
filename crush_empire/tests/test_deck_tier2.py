"""
Tier 2: spot the red flag.

The property this tier lives or dies by is not the scoring — it's that the modal
carries no information. If a timed puzzle only ever wrapped a scam, the player
would learn "modal means scam" in about four cards and never read a bio again.
"""
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from crush_empire import economy
from crush_empire.models import BioSegment, CardChallenge, EmpireState, GameProfile
from crush_empire.services import deck as deck_service
from crush_empire.services.deck import DeckError

User = get_user_model()


def make_profile(name, is_scam, segments):
    profile = GameProfile.objects.create(
        emoji="🧔",
        display_name=name,
        age=30,
        is_scam=is_scam,
        tier2_eligible=len(segments) >= economy.TIER2_MIN_SEGMENTS,
    )
    rows = []
    for i, (text, flag) in enumerate(segments):
        rows.append(
            BioSegment.objects.create(
                profile=profile,
                order=i,
                text=text,
                is_red_flag=flag is not None,
                flag_type=flag or "",
                explanation=f"because {flag}" if flag else "",
            )
        )
    return profile, rows


def unlock_tier2(user):
    state, _ = EmpireState.objects.get_or_create(user=user)
    state.swipes = economy.TIER2_UNLOCK_SWIPES
    state.save()
    return state


class Tier2SecrecyTests(TestCase):
    """The modal must not be a tell."""

    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")
        unlock_tier2(self.user)
        make_profile("Marc", False, [("a", None), ("b", None), ("c", None)])
        make_profile("Dimitri", True, [("oil rig", "unverifiable_job"), ("b", None), ("c", None)])

    def _draw_many(self, n=400):
        """Draw and resolve n cards, recording (tier, was_scam) for each."""
        rng = random.Random(20260709)
        seen = []
        for _ in range(n):
            card = deck_service.draw(self.user, rng=rng)
            action = "clear" if card["tier"] == 2 else "nope"
            _, result = deck_service.resolve(
                self.user, card["challenge_id"], action, tapped=[]
            )
            seen.append((card["tier"], result["reveal"]["is_scam"]))
        return seen

    def test_the_modal_fires_on_genuine_cards_too(self):
        seen = self._draw_many()
        tier2 = [is_scam for tier, is_scam in seen if tier == 2]

        self.assertTrue(tier2, "tier 2 never dealt")
        self.assertIn(True, tier2, "tier 2 never dealt a scam")
        self.assertIn(False, tier2, "tier 2 only ever dealt scams — the modal IS the answer")

    def test_scam_rate_is_the_same_inside_and_outside_the_modal(self):
        """
        If P(scam | tier 2) drifts from P(scam | tier 1), the modal leaks a prior.
        Both should sit near SCAM_CARD_RATE.
        """
        seen = self._draw_many()
        t1 = [s for tier, s in seen if tier == 1]
        t2 = [s for tier, s in seen if tier == 2]

        rate1 = sum(t1) / len(t1)
        rate2 = sum(t2) / len(t2)
        self.assertAlmostEqual(rate1, economy.SCAM_CARD_RATE, delta=0.08)
        self.assertAlmostEqual(rate2, economy.SCAM_CARD_RATE, delta=0.08)

    def test_tier2_payload_hides_which_segments_are_flags(self):
        card = None
        rng = random.Random(3)
        while card is None or card["tier"] != 2:
            if card:
                deck_service.resolve(self.user, card["challenge_id"], "nope")
            card = deck_service.draw(self.user, rng=rng)

        self.assertEqual(
            set(card),
            {"challenge_id", "tier", "emoji", "name", "age", "segments", "deadline", "seconds"},
        )
        for segment in card["segments"]:
            self.assertEqual(set(segment), {"id", "text"})

    def test_tier2_not_dealt_before_unlock(self):
        rookie = User.objects.create_user(username="new@b.c", email="new@b.c")
        rng = random.Random(1)
        for _ in range(30):
            card = deck_service.draw(rookie, rng=rng)
            self.assertEqual(card["tier"], 1)
            deck_service.resolve(rookie, card["challenge_id"], "nope")

    def test_tier2_not_dealt_when_a_pool_is_empty(self):
        """A modal that can only be a scam is worse than no modal."""
        GameProfile.objects.filter(is_scam=False).update(tier2_eligible=False)
        rng = random.Random(1)
        for _ in range(30):
            card = deck_service.draw(self.user, rng=rng)
            self.assertEqual(card["tier"], 1)
            deck_service.resolve(self.user, card["challenge_id"], "nope")


class Tier2ScoringTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")
        unlock_tier2(self.user)

        self.genuine, self.genuine_rows = make_profile(
            "Marc", False, [("a", None), ("b", None), ("c", None)]
        )
        self.scam, self.scam_rows = make_profile(
            "Dimitri",
            True,
            [
                ("oil rig engineer", "unverifiable_job"),
                ("likes classical music", None),
                ("camera broken for two years", "never_video_calls"),
            ],
        )
        self.scam_flags = [r.id for r in self.scam_rows if r.is_red_flag]
        self.scam_innocent = [r.id for r in self.scam_rows if not r.is_red_flag]

    def _play(self, profile, action, tapped=(), age_seconds=0):
        challenge = CardChallenge.objects.create(user=self.user, profile=profile, tier=2)
        if age_seconds:
            CardChallenge.objects.filter(pk=challenge.pk).update(
                issued_at=timezone.now() - timedelta(seconds=age_seconds)
            )
            challenge.refresh_from_db()
        return deck_service.resolve(self.user, str(challenge.id), action, tapped=list(tapped))

    def _state(self):
        return EmpireState.objects.get_or_create(user=self.user)[0]

    # ── the brute force ──────────────────────────────────────────────────

    def test_tapping_every_line_never_advances_the_streak(self):
        """
        The obvious exploit: tap everything, catch every flag. Scoring is
        found − false_positives, so smearing the innocent line downgrades a full
        catch to partial credit and the streak never moves.
        """
        all_ids = [r.id for r in self.scam_rows]
        state, r = self._play(self.scam, "report", tapped=all_ids)

        self.assertEqual(r["outcome"], "partial")
        self.assertEqual(r["streak"], 0, "brute force must not build a streak")

    def test_brute_force_falls_further_behind_as_a_streak_grows(self):
        """
        At streak 0 the two pay the same single flag; the gap is the streak.
        A player who has earned a streak sees brute force pay a fraction.
        """
        state = self._state()
        state.streak = 10
        state.save()

        all_ids = [r.id for r in self.scam_rows]
        _, brute = self._play(self.scam, "report", tapped=all_ids)

        state = self._state()
        state.streak = 10
        state.save()
        _, honest = self._play(self.scam, "report", tapped=self.scam_flags)

        self.assertLess(brute["flags"], honest["flags"])
        self.assertEqual(brute["streak"], 10, "partial credit holds, but does not build")
        self.assertEqual(honest["streak"], 11)

    def test_brute_force_on_the_genuine_majority_burns_the_streak(self):
        """
        Five cards in six are genuine, where tapping everything is a false report.
        That is what makes tap-everything self-defeating overall.
        """
        state = self._state()
        state.streak = 9
        state.save()

        all_ids = [r.id for r in self.genuine_rows]
        _, r = self._play(self.genuine, "report", tapped=all_ids)
        self.assertEqual(r["outcome"], "false_report")
        self.assertEqual(r["streak"], 0)

    def test_tapping_every_line_on_a_genuine_card_is_a_false_report(self):
        all_ids = [r.id for r in self.genuine_rows]
        state, r = self._play(self.genuine, "report", tapped=all_ids)
        self.assertEqual(r["outcome"], "false_report")
        self.assertEqual(r["points"], 0)
        self.assertEqual(state.streak, 0)

    # ── the honest paths ─────────────────────────────────────────────────

    def test_exactly_the_flags_is_a_full_catch(self):
        state, r = self._play(self.scam, "report", tapped=self.scam_flags)
        self.assertEqual(r["outcome"], "correct")
        self.assertEqual(r["streak"], 1)
        self.assertEqual(r["flags"], economy.flag_award(1))

    def test_some_of_the_flags_is_partial_credit_without_a_streak(self):
        state, r = self._play(self.scam, "report", tapped=self.scam_flags[:1])
        self.assertEqual(r["outcome"], "partial")
        self.assertEqual(r["streak"], 0)
        self.assertGreaterEqual(r["flags"], 1)

    def test_workshop_bonus_pays_on_clean_catches_only(self):
        """The Safety Workshop rewards precision, not enthusiasm: a full catch
        earns the extra 🚩, partial credit earns exactly what it always did."""
        state = self._state()
        state.safety_upgrades = [economy.SAFETY_WORKSHOP]
        state.save()

        _, full = self._play(self.scam, "report", tapped=self.scam_flags)
        self.assertEqual(full["flags"], economy.flag_award(1) + 1)

        _, partial = self._play(self.scam, "report", tapped=self.scam_flags[:1])
        self.assertEqual(partial["flags"], economy.partial_flag_award(1))

    def test_clearing_a_genuine_card_pays_like_a_like(self):
        state, r = self._play(self.genuine, "clear")
        self.assertEqual(r["outcome"], "neutral")
        self.assertEqual(r["points"], economy.per_like(self._state()))

    def test_clearing_a_scam_is_a_catfish(self):
        state, r = self._play(self.scam, "clear")
        self.assertEqual(r["outcome"], "catfished")

    def test_tapping_only_innocent_lines_on_a_scam_is_a_catfish(self):
        state, r = self._play(self.scam, "report", tapped=self.scam_innocent)
        self.assertEqual(r["outcome"], "catfished")

    # ── the clock ────────────────────────────────────────────────────────

    def test_timeout_is_judged_from_the_servers_issued_at(self):
        state, r = self._play(
            self.scam, "report", tapped=self.scam_flags,
            age_seconds=economy.TIER2_SECONDS + economy.TIER2_GRACE_SECONDS + 1,
        )
        # A perfect answer, submitted too late. The client cannot buy time.
        self.assertEqual(r["outcome"], "catfished")
        self.assertEqual(r["flags"], 0)

    def test_timeout_on_a_genuine_card_costs_nothing_and_pays_nothing(self):
        state, r = self._play(
            self.genuine, "clear",
            age_seconds=economy.TIER2_SECONDS + economy.TIER2_GRACE_SECONDS + 1,
        )
        self.assertEqual(r["outcome"], "timeout")
        self.assertEqual(r["points"], 0)
        self.assertEqual(state.points, 0)

    def test_answer_inside_the_grace_period_still_counts(self):
        state, r = self._play(
            self.scam, "report", tapped=self.scam_flags,
            age_seconds=economy.TIER2_SECONDS + 1,
        )
        self.assertEqual(r["outcome"], "correct")

    def test_redrawing_does_not_reset_the_timer(self):
        card = deck_service.draw(self.user, rng=random.Random(0))
        CardChallenge.objects.filter(pk=card["challenge_id"]).update(
            tier=2, issued_at=timezone.now() - timedelta(seconds=10)
        )
        again = deck_service.draw(self.user)
        self.assertEqual(again["challenge_id"], card["challenge_id"])
        remaining = (
            timezone.datetime.fromisoformat(again["deadline"]) - timezone.now()
        ).total_seconds()
        self.assertLess(remaining, economy.TIER2_SECONDS - 5)

    # ── the tier boundary ────────────────────────────────────────────────

    def test_cannot_answer_a_tier2_card_with_a_cheap_swipe(self):
        challenge = CardChallenge.objects.create(user=self.user, profile=self.scam, tier=2)
        with self.assertRaisesMessage(DeckError, "illegal action for tier"):
            deck_service.resolve(self.user, str(challenge.id), "like")

    def test_cannot_clear_a_tier1_card(self):
        challenge = CardChallenge.objects.create(user=self.user, profile=self.scam, tier=1)
        with self.assertRaisesMessage(DeckError, "illegal action for tier"):
            deck_service.resolve(self.user, str(challenge.id), "clear")

    def test_segment_ids_from_another_card_are_ignored(self):
        """Tapping a foreign id must not count as a flag, nor as a false positive."""
        foreign = self.genuine_rows[0].id
        state, r = self._play(
            self.scam, "report", tapped=[*self.scam_flags, foreign]
        )
        self.assertEqual(r["outcome"], "correct")
        self.assertNotIn(foreign, r["reveal"]["tapped"])

    def test_garbage_tapped_values_are_dropped(self):
        state, r = self._play(
            self.scam, "report", tapped=[*self.scam_flags, "; DROP TABLE", None, -1]
        )
        self.assertEqual(r["outcome"], "correct")

    # ── the reveal ───────────────────────────────────────────────────────

    def test_reveal_names_the_lines_and_which_were_caught(self):
        state, r = self._play(self.scam, "report", tapped=self.scam_flags[:1])
        flags = r["reveal"]["flags"]
        self.assertEqual(len(flags), 2)
        self.assertTrue(all(f["text"] for f in flags))
        self.assertEqual(r["reveal"]["tapped"], self.scam_flags[:1])
