"""
API tests for the game endpoints.

These encode the promise the whole design rests on: the client sends intents,
the server prices them. A client that lies about its balance, the price, or an
unlock gets nothing.
"""
import json

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from crush_empire.models import BioSegment, EmpireState, GameProfile

User = get_user_model()

GAME_URLS = {"ROOT_URLCONF": "azureproject.urls_game"}
GAME_HOST = "game.crush.lu"


def seed_genuine_only():
    """A deck of nothing but genuine cards, so draws are deterministic."""
    profile = GameProfile.objects.create(
        emoji="🧔", display_name="Marc", age=29, is_scam=False
    )
    BioSegment.objects.create(profile=profile, order=0, text="Loves the fridge.")
    return profile


class SiteTestMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**GAME_URLS, CRUSH_EMPIRE_ENABLED=True)
class GameApiTests(SiteTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="p@e.com", email="p@e.com", password="pw"
        )
        # DomainURLRoutingMiddleware picks the urlconf from the Host header.
        self.client = Client(HTTP_HOST=GAME_HOST)
        self.client.force_login(self.user)
        seed_genuine_only()

    def post(self, name, payload=None):
        return self.client.post(
            reverse(name),
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def state(self):
        # The row is created lazily on the first API call, so tests that seed a
        # balance before touching the API must not assume it exists.
        return EmpireState.objects.get_or_create(user=self.user)[0]

    def play(self, action="like"):
        """Draw a card and answer it, the way the client does."""
        card = self.post("empire_api_draw").json()["card"]
        return self.post(
            "empire_api_resolve",
            {"challenge_id": card["challenge_id"], "action": action},
        )

    # ── auth / gating ────────────────────────────────────────────────────

    def test_anonymous_gets_json_403_not_a_redirect(self):
        """fetch() follows redirects; a 302 to crush.lu would look like a CORS error."""
        anon = Client(HTTP_HOST=GAME_HOST)
        response = anon.post(reverse("empire_api_sync"))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["reauth"])

    @override_settings(CRUSH_EMPIRE_ENABLED=False)
    def test_flag_off_closes_the_api_to_non_staff(self):
        self.assertEqual(self.post("empire_api_sync").status_code, 403)

    @override_settings(CRUSH_EMPIRE_ENABLED=False)
    def test_flag_off_still_open_to_staff(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.assertEqual(self.post("empire_api_sync").status_code, 200)

    def test_banned_user_cannot_play(self):
        consent = self.user.data_consent
        consent.crushlu_banned = True
        consent.save(update_fields=["crushlu_banned"])
        self.assertEqual(self.post("empire_api_sync").status_code, 403)

    def test_get_is_rejected(self):
        self.assertEqual(self.client.get(reverse("empire_api_draw")).status_code, 405)

    # ── the anti-cheat promises ──────────────────────────────────────────

    def test_drawn_card_carries_no_answer(self):
        card = self.post("empire_api_draw").json()["card"]
        self.assertEqual(
            set(card), {"challenge_id", "emoji", "name", "age", "segments"}
        )

    def test_client_supplied_points_are_ignored(self):
        card = self.post("empire_api_draw").json()["card"]
        response = self.post(
            "empire_api_resolve",
            {
                "challenge_id": card["challenge_id"],
                "action": "like",
                "points": 999_999_999,
                "flags": 999,
            },
        )
        self.assertEqual(response.json()["result"]["points"], 2)
        self.assertEqual(self.state().points, 2)
        self.assertEqual(self.state().flags, 0)

    def test_replayed_challenge_rejected(self):
        card = self.post("empire_api_draw").json()["card"]
        payload = {"challenge_id": card["challenge_id"], "action": "like"}
        self.assertEqual(self.post("empire_api_resolve", payload).status_code, 200)
        self.assertEqual(self.post("empire_api_resolve", payload).status_code, 400)
        self.assertEqual(self.state().points, 2)

    def test_another_users_challenge_is_unknown(self):
        other = User.objects.create_user(username="o@e.com", email="o@e.com")
        other_client = Client(HTTP_HOST=GAME_HOST)
        other_client.force_login(other)
        card = other_client.post(reverse("empire_api_draw")).json()["card"]

        response = self.post(
            "empire_api_resolve", {"challenge_id": card["challenge_id"], "action": "like"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "unknown challenge")

    def test_client_supplied_price_is_ignored(self):
        """Claiming a generator costs 1 must not buy it for 1."""
        state = self.state()
        state.points = 5
        state.save()

        response = self.post(
            "empire_api_buy", {"kind": "generator", "id": 0, "cost": 1, "price": 1}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "insufficient")
        self.assertEqual(self.state().generator_count(0), 0)

    def test_cannot_buy_a_locked_upgrade(self):
        state = self.state()
        state.points = 10_000_000
        state.save()

        response = self.post("empire_api_buy", {"kind": "upgrade", "id": 5})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state().upgrades, [])

    def test_unknown_action_rejected(self):
        card = self.post("empire_api_draw").json()["card"]
        response = self.post(
            "empire_api_resolve",
            {"challenge_id": card["challenge_id"], "action": "sideways"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state().swipes, 0)

    def test_unknown_kind_rejected(self):
        response = self.post("empire_api_buy", {"kind": "hearts", "id": 0})
        self.assertEqual(response.status_code, 400)

    def test_malformed_json_rejected(self):
        response = self.client.post(
            reverse("empire_api_resolve"),
            data="{not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_prestige_refused_below_one_heart(self):
        response = self.post("empire_api_prestige")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state().hearts, 0)

    # ── happy paths ──────────────────────────────────────────────────────

    def test_swipe_then_buy_then_cps_rises(self):
        for _ in range(10):
            self.play("like")
        self.assertEqual(self.state().points, 20)

        response = self.post("empire_api_buy", {"kind": "generator", "id": 0})
        body = response.json()
        self.assertTrue(body["success"])

        gen0 = body["state"]["generators"][0]
        self.assertEqual(gen0["owned"], 1)
        self.assertEqual(gen0["cost"], 18)  # next copy already repriced
        self.assertGreater(body["state"]["cps"], 0)
        self.assertEqual(body["state"]["points"], 5)

    def test_sync_reports_offline_earnings(self):
        from django.utils import timezone

        state = self.state()
        state.generators = {"1": 10}
        state.last_tick = timezone.now() - timezone.timedelta(seconds=30)
        state.save()

        body = self.post("empire_api_sync").json()
        self.assertEqual(body["offline_earned"], 300)

    def test_swipe_unlocks_the_first_upgrade(self):
        body = self.post("empire_api_sync").json()
        self.assertFalse(body["state"]["upgrades"][0]["unlocked"])

        for _ in range(10):
            self.play("nope")

        body = self.post("empire_api_sync").json()
        self.assertTrue(body["state"]["upgrades"][0]["unlocked"])
