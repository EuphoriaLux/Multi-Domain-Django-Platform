"""
The scam layer.

Two families of test here. The first guards the one property the mechanic rests
on: the client cannot learn the answer before it commits. The second guards the
payoff table against the degenerate strategies — report-everything and
nope-everything — because a payoff table that doesn't resist them isn't a game,
it's a formality.
"""
import random
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from crush_empire import economy
from crush_empire.models import BioSegment, CardChallenge, EmpireState, GameProfile
from crush_empire.services import deck as deck_service
from crush_empire.services.deck import DeckError

User = get_user_model()


def make_profile(name, is_scam, segments=None):
    """Tier-1 fixtures: one or two segments, so never tier2_eligible."""
    profile = GameProfile.objects.create(
        emoji="🧔", display_name=name, age=30, is_scam=is_scam, tier2_eligible=False
    )
    for i, (text, flag) in enumerate(segments or [("hello", None)]):
        BioSegment.objects.create(
            profile=profile,
            order=i,
            text=text,
            is_red_flag=flag is not None,
            flag_type=flag or "",
            explanation="because reasons" if flag else "",
        )
    return profile


class DeckSecrecyTests(TestCase):
    """The client must not be able to tell a scam from a genuine card."""

    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")
        self.scam = make_profile(
            "Dimitri", True, [("Oil rig engineer.", "unverifiable_job")]
        )

    def test_drawn_card_never_carries_the_answer(self):
        card = deck_service.draw(self.user)

        self.assertEqual(
            set(card),
            {"challenge_id", "tier", "emoji", "avatar", "name", "age", "segments"},
            "draw() payload grew a key — check it is not the answer",
        )
        for segment in card["segments"]:
            self.assertEqual(set(segment), {"id", "text"})

    def test_avatar_derives_from_the_seed_never_from_the_kind(self):
        """No seed → None (emoji card); a seed → a static URL. Nothing about
        is_scam may reach the picture — a 'scam look' would be an oracle."""
        card = deck_service.draw(self.user)
        self.assertIsNone(card["avatar"])

        GameProfile.objects.filter(pk=self.scam.pk).update(avatar_seed="Dimitri")
        CardChallenge.objects.all().delete()
        card = deck_service.draw(self.user)
        self.assertIn("crush_empire/avatars/dimitri.svg", card["avatar"])

    def test_drawn_card_payload_has_no_scam_marker_anywhere(self):
        """Belt and braces: no nested key leaks it either."""
        import json

        blob = json.dumps(deck_service.draw(self.user))
        for forbidden in ("is_scam", "is_red_flag", "flag_type", "explanation"):
            self.assertNotIn(forbidden, blob)

    def test_scam_and_genuine_cards_are_shaped_identically(self):
        """A scam card must not be distinguishable by its shape or segment count."""
        make_profile("Marc", False, [("Loves the fridge.", None)])
        GameProfile.objects.filter(is_scam=True).delete()

        genuine = deck_service.draw(self.user)
        CardChallenge.objects.all().delete()

        GameProfile.objects.all().delete()
        make_profile("Dimitri", True, [("Oil rig engineer.", "unverifiable_job")])
        scam = deck_service.draw(self.user)

        self.assertEqual(set(genuine), set(scam))
        self.assertEqual(
            set(genuine["segments"][0]), set(scam["segments"][0])
        )


class DeckIntegrityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")
        self.other = User.objects.create_user(username="x@y.z", email="x@y.z")
        make_profile("Marc", False)

    def test_draw_is_idempotent_while_a_card_is_open(self):
        """Otherwise a player re-rolls until they get a card they like."""
        first = deck_service.draw(self.user)
        second = deck_service.draw(self.user)
        self.assertEqual(first["challenge_id"], second["challenge_id"])
        self.assertEqual(CardChallenge.objects.filter(user=self.user).count(), 1)

    def test_a_new_card_is_dealt_once_the_last_is_resolved(self):
        first = deck_service.draw(self.user)
        deck_service.resolve(self.user, first["challenge_id"], "like")
        second = deck_service.draw(self.user)
        self.assertNotEqual(first["challenge_id"], second["challenge_id"])

    def test_challenge_cannot_be_resolved_twice(self):
        card = deck_service.draw(self.user)
        deck_service.resolve(self.user, card["challenge_id"], "like")
        with self.assertRaisesMessage(DeckError, "already resolved"):
            deck_service.resolve(self.user, card["challenge_id"], "like")

    def test_cannot_resolve_another_users_challenge(self):
        card = deck_service.draw(self.user)
        with self.assertRaisesMessage(DeckError, "unknown challenge"):
            deck_service.resolve(self.other, card["challenge_id"], "like")

    def test_expired_challenge_rejected(self):
        card = deck_service.draw(self.user)
        CardChallenge.objects.filter(pk=card["challenge_id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaisesMessage(DeckError, "expired"):
            deck_service.resolve(self.user, card["challenge_id"], "like")

    def test_unknown_challenge_id_rejected(self):
        with self.assertRaisesMessage(DeckError, "unknown challenge"):
            deck_service.resolve(self.user, str(uuid.uuid4()), "like")

    def test_malformed_challenge_id_rejected(self):
        with self.assertRaisesMessage(DeckError, "unknown challenge"):
            deck_service.resolve(self.user, "not-a-uuid", "like")

    def test_unknown_action_rejected(self):
        card = deck_service.draw(self.user)
        with self.assertRaisesMessage(DeckError, "unknown action"):
            deck_service.resolve(self.user, card["challenge_id"], "sideways")

    def test_empty_deck_raises_rather_than_500(self):
        GameProfile.objects.all().delete()
        with self.assertRaisesMessage(DeckError, "deck is empty"):
            deck_service.draw(self.user)

    def test_deck_without_scam_cards_still_deals(self):
        """A half-seeded deck degrades to a plain idle game, it does not break."""
        GameProfile.objects.filter(is_scam=True).delete()
        rng = random.Random(1)
        for _ in range(5):
            card = deck_service.draw(self.user, rng=rng)
            deck_service.resolve(self.user, card["challenge_id"], "nope")


class PayoffMatrixTests(TestCase):
    """
                     Like →       Nope ←    Report ↑
      Genuine        +2 💘        +1 💘     0 · streak reset
      Scam           catfished    0 💘      +🚩 × streak
    """

    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")
        self.genuine = make_profile("Marc", False)
        self.scam = make_profile(
            "Dimitri", True, [("Oil rig engineer.", "unverifiable_job")]
        )

    def _play(self, profile, action):
        challenge = CardChallenge.objects.create(user=self.user, profile=profile)
        return deck_service.resolve(self.user, str(challenge.id), action)

    def _state(self):
        return EmpireState.objects.get_or_create(user=self.user)[0]

    # ── genuine ──────────────────────────────────────────────────────────

    def test_genuine_like_pays_per_like(self):
        state, r = self._play(self.genuine, "like")
        self.assertEqual(r["points"], 2)
        self.assertEqual(state.points, 2)
        self.assertEqual(r["outcome"], "neutral")

    def test_genuine_nope_pays_per_nope(self):
        state, r = self._play(self.genuine, "nope")
        self.assertEqual(r["points"], 1)

    def test_genuine_report_pays_nothing_and_burns_the_streak(self):
        state = self._state()
        state.streak = 9
        state.save()

        state, r = self._play(self.genuine, "report")
        self.assertEqual(r["outcome"], "false_report")
        self.assertEqual(r["points"], 0)
        self.assertEqual(r["flags"], 0)
        self.assertEqual(state.streak, 0)

    # ── scam ─────────────────────────────────────────────────────────────

    def test_scam_report_pays_flags_and_builds_the_streak(self):
        state, r = self._play(self.scam, "report")
        self.assertEqual(r["outcome"], "correct")
        self.assertEqual(r["flags"], 1)
        self.assertEqual(r["streak"], 1)
        self.assertEqual(state.flags, 1)

    def test_scam_nope_pays_zero(self):
        """
        The load-bearing zero. At +1, noping is free and 'nope everything' is a
        costless opt-out of the entire scam layer.
        """
        state, r = self._play(self.scam, "nope")
        self.assertEqual(r["points"], 0)
        self.assertEqual(r["outcome"], "missed")
        self.assertEqual(state.points, 0)

    def test_reporting_a_scam_strictly_dominates_noping_it(self):
        bob = User.objects.create_user(username="b@b.c", email="b@b.c")

        _, reported = self._play(self.scam, "report")

        challenge = CardChallenge.objects.create(user=bob, profile=self.scam)
        _, noped = deck_service.resolve(bob, str(challenge.id), "nope")

        self.assertGreaterEqual(reported["points"], noped["points"])
        self.assertGreater(reported["flags"], noped["flags"])

    def test_early_catfish_costs_ten_percent_and_the_streak(self):
        state = self._state()
        state.points = 1000
        state.streak = 4
        state.save()

        state, r = self._play(self.scam, "like")
        self.assertEqual(r["outcome"], "catfished")
        self.assertEqual(r["points"], -100)
        self.assertEqual(state.points, 900)
        self.assertEqual(state.streak, 0)
        self.assertFalse(r["debuffed"])

    def test_catfish_never_drives_points_below_zero(self):
        state, r = self._play(self.scam, "like")
        self.assertEqual(self._state().points, 0)

    def test_late_catfish_throttles_production_instead(self):
        state = self._state()
        state.points = 10_000
        # Tier-4 generators put cps over the late-game threshold.
        state.generators = {"4": 1}
        # Pin the accrual clock ahead so resolve()'s accrue() banks nothing:
        # at 260 cps the milliseconds this test itself takes would mint points
        # and break the exact balance assertion below.
        state.last_tick = timezone.now() + timedelta(seconds=60)
        state.save()
        self.assertTrue(economy.is_late_game(state))

        state, r = self._play(self.scam, "like")
        self.assertTrue(r["debuffed"])
        self.assertTrue(state.is_debuffed)
        self.assertEqual(state.points, 10_000)  # balance untouched

    def test_debuff_halves_the_effective_rate_but_not_the_shop_rate(self):
        state = self._state()
        state.generators = {"4": 1}
        state.debuff_until = timezone.now() + timedelta(seconds=60)
        state.save()

        self.assertAlmostEqual(
            economy.effective_crushes_per_second(state),
            economy.crushes_per_second(state) / 2,
        )
        # is_late_game must read the undebuffed rate, or a throttle would bounce
        # the player back across the early/late threshold.
        self.assertTrue(economy.is_late_game(state))

    # ── the 🚩 shop's effects ────────────────────────────────────────────

    def test_scam_shield_halves_an_early_catfish(self):
        state = self._state()
        state.points = 1000
        state.safety_upgrades = [economy.SAFETY_SCAM_SHIELD]
        state.save()

        state, r = self._play(self.scam, "like")
        self.assertEqual(r["points"], -50)  # 5%, not the naked 10%
        self.assertEqual(state.points, 950)

    def test_scam_shield_halves_the_late_game_debuff(self):
        state = self._state()
        state.points = 10_000
        state.generators = {"4": 1}
        state.safety_upgrades = [economy.SAFETY_SCAM_SHIELD]
        # Same accrual-clock pin as the unshielded test above.
        state.last_tick = timezone.now() + timedelta(seconds=60)
        state.save()

        before = timezone.now()
        state, r = self._play(self.scam, "like")
        self.assertTrue(state.is_debuffed)
        remaining = (state.debuff_until - before).total_seconds()
        self.assertGreater(remaining, 50)
        self.assertLessEqual(remaining, 61, "shield should serve 60s, not 120s")

    def test_workshop_pays_one_extra_flag_on_a_report(self):
        state = self._state()
        state.safety_upgrades = [economy.SAFETY_WORKSHOP]
        state.save()

        _, r = self._play(self.scam, "report")
        self.assertEqual(r["flags"], economy.flag_award(1) + 1)

    # ── reveal ───────────────────────────────────────────────────────────

    def test_resolve_reveals_the_answer_only_after_the_action(self):
        _, r = self._play(self.scam, "report")
        self.assertTrue(r["reveal"]["is_scam"])
        self.assertEqual(len(r["reveal"]["flags"]), 1)
        self.assertEqual(r["reveal"]["flags"][0]["flag_type"], "unverifiable_job")
        self.assertTrue(r["reveal"]["flags"][0]["explanation"])

    def test_action_is_recorded_before_the_reveal_is_returned(self):
        card = deck_service.draw(self.user)
        deck_service.resolve(self.user, card["challenge_id"], "nope")
        challenge = CardChallenge.objects.get(pk=card["challenge_id"])
        self.assertEqual(challenge.action, "nope")
        self.assertIsNotNone(challenge.resolved_at)


class DegenerateStrategyTests(TestCase):
    """
    Simulate the two strategies the payoff table is designed to defeat, and
    assert that honest play beats both. If a change to the numbers ever makes
    one of these win, this fails.
    """

    N = 600

    def setUp(self):
        self.genuine = make_profile("Marc", False)
        self.scam = make_profile("Dimitri", True, [("Oil rig.", "unverifiable_job")])

    def _run(self, strategy):
        user = User.objects.create_user(
            username=f"{strategy}@x.c", email=f"{strategy}@x.c"
        )
        rng = random.Random(20260709)
        for _ in range(self.N):
            is_scam = rng.random() < economy.SCAM_CARD_RATE
            profile = self.scam if is_scam else self.genuine
            challenge = CardChallenge.objects.create(user=user, profile=profile)

            if strategy == "nope_everything":
                action = "nope"
            elif strategy == "report_everything":
                action = "report"
            else:  # an oracle player: the ceiling honest play tends toward
                action = "report" if is_scam else "like"

            deck_service.resolve(user, str(challenge.id), action)

        return EmpireState.objects.get(user=user)

    def test_honest_play_beats_nope_everything(self):
        honest = self._run("honest")
        coward = self._run("nope_everything")

        self.assertGreater(honest.points, coward.points)
        self.assertGreater(honest.flags, 0)
        self.assertEqual(coward.flags, 0, "noping must never earn flags")

    def test_honest_play_beats_report_everything(self):
        honest = self._run("honest")
        spammer = self._run("report_everything")

        self.assertGreater(honest.points, spammer.points)
        # ~5 of 6 reports hit a genuine profile and reset the streak, so the
        # spammer's flag award never climbs off the floor.
        self.assertGreater(honest.flags, spammer.flags)

    def test_report_everything_cannot_build_a_streak(self):
        """
        A spammer's streak is just the longest run of consecutive scam cards —
        every genuine report in between resets it. At f = 1/6 that run never
        reaches STREAK_FLAGS_PER_STEP, so their flag award stays pinned at the
        floor of 1 no matter how long they play.
        """
        spammer = self._run("report_everything")
        honest = self._run("honest")

        self.assertLess(spammer.best_streak, economy.STREAK_FLAGS_PER_STEP)
        self.assertGreater(honest.best_streak, 10 * spammer.best_streak)


class DebuffClearTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")

    def _state(self):
        return EmpireState.objects.get_or_create(user=self.user)[0]

    def test_clearing_costs_flags_and_ends_the_debuff(self):
        state = self._state()
        state.flags = 5
        state.debuff_until = timezone.now() + timedelta(seconds=60)
        state.save()

        state = deck_service.clear_debuff(self.user)
        self.assertEqual(state.flags, 0)
        self.assertIsNone(state.debuff_until)

    def test_cannot_clear_without_enough_flags(self):
        state = self._state()
        state.flags = 4
        state.debuff_until = timezone.now() + timedelta(seconds=60)
        state.save()

        with self.assertRaisesMessage(DeckError, "insufficient"):
            deck_service.clear_debuff(self.user)
        self.assertEqual(self._state().flags, 4)

    def test_cannot_clear_when_not_debuffed(self):
        state = self._state()
        state.flags = 99
        state.save()
        with self.assertRaisesMessage(DeckError, "not debuffed"):
            deck_service.clear_debuff(self.user)


class FlagAwardTests(TestCase):
    def test_award_is_sublinear_and_capped(self):
        self.assertEqual(economy.flag_award(0), 1)
        self.assertEqual(economy.flag_award(4), 1)
        self.assertEqual(economy.flag_award(5), 2)
        self.assertEqual(economy.flag_award(20), 5)
        self.assertEqual(economy.flag_award(1000), economy.MAX_FLAG_AWARD)
