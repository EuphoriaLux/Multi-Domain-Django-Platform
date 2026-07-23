"""
Economy and server-authority tests.

The load-bearing property: the client sends intents, never balances or prices.
Anything it asserts about its own wealth must be ignored.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from crush_empire import economy
from crush_empire.models import EmpireState
from crush_empire.services import state as svc

User = get_user_model()


class EconomyMathTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")
        self.state = EmpireState.objects.create(user=self.user)

    def test_first_generator_costs_its_base(self):
        self.assertEqual(economy.generator_cost(self.state, 0), 15)

    def test_cost_curve_compounds(self):
        self.state.generators = {"0": 1}
        self.assertEqual(economy.generator_cost(self.state, 0), 18)  # ceil(15 * 1.15)
        self.state.generators = {"0": 2}
        self.assertEqual(economy.generator_cost(self.state, 0), 20)  # ceil(15 * 1.15^2)

    def test_generator_count_reads_string_keys(self):
        """JSON object keys are strings; an int lookup would silently return 0."""
        self.state.generators = {"3": 7}
        self.assertEqual(self.state.generator_count(3), 7)

    def test_cps_sums_owned_generators(self):
        self.state.generators = {"0": 10, "1": 2}
        # 10 * 0.1 + 2 * 1.0
        self.assertAlmostEqual(economy.crushes_per_second(self.state), 3.0)

    def test_hearts_boost_everything(self):
        self.state.generators = {"1": 1}
        base = economy.crushes_per_second(self.state)
        self.state.hearts = 50  # +100%
        self.assertAlmostEqual(economy.crushes_per_second(self.state), base * 2)

    def test_global_upgrades_multiply_cps_not_clicks(self):
        self.state.generators = {"1": 1}
        self.state.upgrades = [3]  # Viral Profiles, global x2
        self.assertAlmostEqual(economy.crushes_per_second(self.state), 2.0)
        self.assertEqual(economy.per_like(self.state), economy.BASE_PER_LIKE)

    def test_click_upgrades_multiply_clicks_not_cps(self):
        self.state.generators = {"1": 1}
        self.state.upgrades = [0]  # Confident Thumb, click x2
        self.assertAlmostEqual(economy.crushes_per_second(self.state), 1.0)
        self.assertEqual(economy.per_like(self.state), 4)

    def test_pending_hearts_is_quadratic(self):
        self.state.total_earned = 1_000_000
        self.assertEqual(economy.pending_hearts(self.state), 1)
        self.state.total_earned = 4_000_000
        self.assertEqual(economy.pending_hearts(self.state), 2)
        self.state.total_earned = 999_999
        self.assertEqual(economy.pending_hearts(self.state), 0)

    def test_generator_unlocks_follow_the_tier_below(self):
        self.assertTrue(economy.generator_unlocked(self.state, 0))
        self.assertFalse(economy.generator_unlocked(self.state, 1))
        self.state.generators = {"0": 1}
        self.assertTrue(economy.generator_unlocked(self.state, 1))
        self.assertFalse(economy.generator_unlocked(self.state, 2))


class ServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")

    def _state(self):
        # Created lazily by the services on first use.
        return EmpireState.objects.get_or_create(user=self.user)[0]

    def test_buy_charges_the_server_price(self):
        state = self._state()
        state.points = 100
        state.save()

        state = svc.buy_generator(self.user, 0)
        self.assertEqual(state.generator_count(0), 1)
        self.assertEqual(state.points, 85)  # 100 - 15

    def test_buy_rejects_when_too_poor(self):
        with self.assertRaisesMessage(ValueError, "insufficient"):
            svc.buy_generator(self.user, 0)
        self.assertEqual(self._state().generator_count(0), 0)

    def test_buy_rejects_locked_generator(self):
        state = self._state()
        state.points = 10_000_000
        state.save()
        with self.assertRaisesMessage(ValueError, "locked"):
            svc.buy_generator(self.user, 3)

    def test_buy_rejects_locked_upgrade(self):
        state = self._state()
        state.points = 10_000_000
        state.save()
        with self.assertRaisesMessage(ValueError, "locked"):
            svc.buy_upgrade(self.user, 0)  # needs 10 swipes

    def test_upgrade_cannot_be_bought_twice(self):
        state = self._state()
        state.points = 10_000
        state.swipes = 10
        state.save()

        svc.buy_upgrade(self.user, 0)
        with self.assertRaisesMessage(ValueError, "already owned"):
            svc.buy_upgrade(self.user, 0)

    def test_idle_accrual_uses_the_server_clock(self):
        state = self._state()
        state.generators = {"1": 10}  # 10/sec
        state.last_tick = timezone.now() - timedelta(seconds=60)
        state.save()

        state, offline = svc.sync(self.user)
        self.assertEqual(offline, 600)
        self.assertEqual(state.points, 600)

    def test_idle_accrual_is_capped(self):
        state = self._state()
        state.generators = {"1": 1}  # 1/sec
        state.last_tick = timezone.now() - timedelta(days=30)
        state.save()

        state, offline = svc.sync(self.user)
        self.assertEqual(offline, svc.OFFLINE_CAP_SECONDS)

    def test_backwards_clock_credits_nothing(self):
        state = self._state()
        state.generators = {"1": 1}
        state.last_tick = timezone.now() + timedelta(hours=1)
        state.save()

        state, offline = svc.sync(self.user)
        self.assertEqual(offline, 0)
        self.assertEqual(state.points, 0)

    def test_prestige_requires_a_whole_heart(self):
        with self.assertRaises(ValueError):
            svc.prestige(self.user)

    def test_prestige_resets_progress_but_keeps_hearts_and_flags(self):
        state = self._state()
        state.total_earned = 4_000_000
        state.points = 500
        state.generators = {"0": 5}
        state.upgrades = [0]
        state.swipes = 99
        state.flags = 7
        state.save()

        state, gained = svc.prestige(self.user)
        self.assertEqual(gained, 2)
        self.assertEqual(state.hearts, 2)
        self.assertEqual(state.flags, 7)  # scam currency survives prestige
        self.assertEqual(state.points, 0)
        self.assertEqual(state.total_earned, 0)
        self.assertEqual(state.generators, {})
        self.assertEqual(state.upgrades, [])
        self.assertEqual(state.swipes, 0)


class SafetyUpgradeTests(TestCase):
    """The 🚩 shop: what catching scams buys."""

    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")
        self.state = EmpireState.objects.create(user=self.user)

    def _reload(self):
        self.state.refresh_from_db()
        return self.state

    # ── effects ──────────────────────────────────────────────────────────

    def test_verified_badge_multiplies_production_not_clicks(self):
        self.state.generators = {"1": 1}
        self.state.safety_upgrades = [economy.SAFETY_VERIFIED_BADGE]
        self.assertAlmostEqual(economy.crushes_per_second(self.state), 1.10)
        self.assertEqual(economy.per_like(self.state), economy.BASE_PER_LIKE)

    def test_scam_shield_halves_catfish_loss_and_debuff(self):
        self.assertAlmostEqual(economy.catfish_loss_fraction(self.state), 0.10)
        self.assertEqual(economy.debuff_seconds(self.state), 120)

        self.state.safety_upgrades = [economy.SAFETY_SCAM_SHIELD]
        self.assertAlmostEqual(economy.catfish_loss_fraction(self.state), 0.05)
        self.assertEqual(economy.debuff_seconds(self.state), 60)

    def test_workshop_pays_a_report_bonus(self):
        self.assertEqual(economy.report_flag_bonus(self.state), 0)
        self.state.safety_upgrades = [economy.SAFETY_WORKSHOP]
        self.assertEqual(economy.report_flag_bonus(self.state), 1)

    # ── the purchase ─────────────────────────────────────────────────────

    def test_buy_safety_charges_flags_not_crushes(self):
        self.state.flags = 25
        self.state.points = 7
        self.state.save()

        state = svc.buy_safety(self.user, economy.SAFETY_VERIFIED_BADGE)
        self.assertEqual(state.safety_upgrades, [economy.SAFETY_VERIFIED_BADGE])
        self.assertEqual(state.flags, 5)
        self.assertEqual(state.points, 7)  # crushes are the wrong currency here

    def test_buy_safety_rejects_when_short_of_flags(self):
        self.state.flags = 19
        self.state.save()

        with self.assertRaisesMessage(ValueError, "insufficient flags"):
            svc.buy_safety(self.user, economy.SAFETY_VERIFIED_BADGE)
        self.assertEqual(self._reload().safety_upgrades, [])

    def test_buy_safety_rejects_a_double_purchase(self):
        self.state.flags = 100
        self.state.save()

        svc.buy_safety(self.user, economy.SAFETY_VERIFIED_BADGE)
        with self.assertRaisesMessage(ValueError, "already owned"):
            svc.buy_safety(self.user, economy.SAFETY_VERIFIED_BADGE)
        self.assertEqual(self._reload().flags, 80, "a rejected buy must not charge")

    def test_buy_safety_rejects_an_unknown_id(self):
        self.state.flags = 10_000
        self.state.save()
        with self.assertRaisesMessage(ValueError, "unknown upgrade"):
            svc.buy_safety(self.user, 999)

    def test_safety_upgrades_survive_prestige(self):
        """Paid for in flags, and flags survive — resetting them would just tax
        the scam layer for prestiging."""
        self.state.total_earned = 1_000_000
        self.state.safety_upgrades = [economy.SAFETY_SCAM_SHIELD]
        self.state.save()

        state, _gained = svc.prestige(self.user)
        self.assertEqual(state.safety_upgrades, [economy.SAFETY_SCAM_SHIELD])

    def test_serializer_exposes_the_safety_shop(self):
        self.state.safety_upgrades = [economy.SAFETY_SCAM_SHIELD]
        rows = {r["id"]: r for r in svc.serialize(self.state)["safety"]}

        self.assertTrue(rows[economy.SAFETY_SCAM_SHIELD]["owned"])
        self.assertFalse(rows[economy.SAFETY_VERIFIED_BADGE]["owned"])
        self.assertEqual(
            rows[economy.SAFETY_VERIFIED_BADGE]["cost"],
            economy.SAFETY_UPGRADES_BY_ID[economy.SAFETY_VERIFIED_BADGE]["cost"],
        )


class SerializerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="a@b.c", email="a@b.c")

    def test_costs_are_server_computed_and_present(self):
        state = EmpireState.objects.get_or_create(user=self.user)[0]
        payload = svc.serialize(state)

        gen0 = payload["generators"][0]
        self.assertEqual(gen0["cost"], 15)
        self.assertTrue(gen0["unlocked"])
        self.assertFalse(payload["generators"][1]["unlocked"])

    def test_serialized_names_are_strings_not_lazy(self):
        """json.dumps chokes on gettext_lazy proxies."""
        import json

        state = EmpireState.objects.get_or_create(user=self.user)[0]
        json.dumps(svc.serialize(state))  # must not raise
