"""
Security tests for the crush.lu → game.crush.lu session handoff.

The handoff hands a real crush.lu identity to another origin, so the failure
modes here are the ones worth codifying: open redirect, code replay, login-CSRF,
and quietly skipping the ban gate.
"""
import secrets
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from azureproject.views_empire_auth import (
    CODE_CACHE_PREFIX,
    STATE_SESSION_KEY,
)

User = get_user_model()

GAME_URLS = {"ROOT_URLCONF": "azureproject.urls_game"}
CRUSH_URLS = {"ROOT_URLCONF": "azureproject.urls_crush"}

RETURN_URL = "http://game.localhost:8000/auth/callback/"
ALLOWED_RETURNS = {("http", "game.localhost:8000", "/auth/callback/")}

# override_settings(ROOT_URLCONF=...) is not enough on its own:
# DomainURLRoutingMiddleware sets request.urlconf from the Host header, which
# wins. crush.lu tests get away with it because testserver falls back to
# DEV_DEFAULT ('crush.lu'); game-host requests must actually say who they are.
GAME_HOST = "game.crush.lu"


def game_client(**kwargs):
    return Client(HTTP_HOST=GAME_HOST, **kwargs)


class SiteTestMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


class HandoffMixin(SiteTestMixin):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="player@example.com",
            email="player@example.com",
            password="testpass123",
        )
        # A signal creates data_consent on user creation. The handoff lives on a
        # consent-enforced path, so without this every mint 302s to the consent
        # page instead of reaching the view — see HandoffConsentGateTests.
        consent = self.user.data_consent
        consent.crushlu_consent_given = True
        consent.save(update_fields=["crushlu_consent_given"])

    def _mint_code(self, state):
        """Drive step 2 on crush.lu and pull the code out of the redirect."""
        client = Client()
        client.force_login(self.user)
        with override_settings(
            **CRUSH_URLS, EMPIRE_CALLBACK_ALLOWED_RETURN_URLS=ALLOWED_RETURNS
        ):
            response = client.get(
                reverse("empire_auth_handoff"),
                {"return": RETURN_URL, "state": state},
            )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        return query["code"][0]


@override_settings(**CRUSH_URLS)
class HandoffMintTests(HandoffMixin, TestCase):
    """Step 2, on crush.lu: minting the single-use code."""

    @override_settings(EMPIRE_CALLBACK_ALLOWED_RETURN_URLS=ALLOWED_RETURNS)
    def test_rejects_non_allowlisted_return_url(self):
        """An attacker-supplied return URL must not receive a code."""
        client = Client()
        client.force_login(self.user)
        response = client.get(
            reverse("empire_auth_handoff"),
            {"return": "https://evil.example.com/auth/callback/", "state": "abc"},
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(EMPIRE_CALLBACK_ALLOWED_RETURN_URLS=ALLOWED_RETURNS)
    def test_rejects_prefix_match_on_allowed_host(self):
        """Exact (scheme, netloc, path) match — a suffixed path is not allowed."""
        client = Client()
        client.force_login(self.user)
        response = client.get(
            reverse("empire_auth_handoff"),
            {"return": RETURN_URL + "evil", "state": "abc"},
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(EMPIRE_CALLBACK_ALLOWED_RETURN_URLS=ALLOWED_RETURNS)
    def test_requires_state(self):
        client = Client()
        client.force_login(self.user)
        response = client.get(reverse("empire_auth_handoff"), {"return": RETURN_URL})
        self.assertEqual(response.status_code, 400)

    @override_settings(EMPIRE_CALLBACK_ALLOWED_RETURN_URLS=ALLOWED_RETURNS)
    def test_anonymous_is_sent_to_login_not_given_a_code(self):
        response = Client().get(
            reverse("empire_auth_handoff"), {"return": RETURN_URL, "state": "abc"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response["Location"])
        self.assertNotIn("code=", response["Location"])

    @override_settings(EMPIRE_CALLBACK_ALLOWED_RETURN_URLS=ALLOWED_RETURNS)
    def test_strips_attacker_supplied_code_from_return_url(self):
        """
        The allowlist matches (scheme, netloc, path) — a query string is not part
        of it, so `?code=stale` passes the whitelist. The view must therefore
        rebuild the query rather than concatenate: many parsers read the first
        value of a repeated key and would consume the attacker's stale code.
        """
        client = Client()
        client.force_login(self.user)
        response = client.get(
            reverse("empire_auth_handoff"),
            {"return": RETURN_URL + "?code=stale", "state": "abc"},
        )
        self.assertEqual(response.status_code, 302)

        codes = parse_qs(urlparse(response["Location"]).query)["code"]
        self.assertEqual(len(codes), 1, "repeated ?code= survived the redirect")
        self.assertNotEqual(codes[0], "stale")

    @override_settings(EMPIRE_CALLBACK_ALLOWED_RETURN_URLS=ALLOWED_RETURNS)
    def test_strips_attacker_supplied_state_from_return_url(self):
        client = Client()
        client.force_login(self.user)
        response = client.get(
            reverse("empire_auth_handoff"),
            {"return": RETURN_URL + "?state=evil", "state": "genuine"},
        )
        self.assertEqual(response.status_code, 302)

        states = parse_qs(urlparse(response["Location"]).query)["state"]
        self.assertEqual(states, ["genuine"])


@override_settings(**GAME_URLS)
class HandoffCallbackTests(HandoffMixin, TestCase):
    """Step 3, on game.crush.lu: consuming the code."""

    def _callback(self, client, code, state):
        return client.get(reverse("empire_auth_callback"), {"code": code, "state": state})

    def _client_with_state(self, state):
        """Simulate step 1 having stashed `state` in this browser's game session."""
        client = game_client()
        session = client.session
        session[STATE_SESSION_KEY] = state
        session.save()
        return client

    def test_happy_path_opens_a_session(self):
        state = secrets.token_urlsafe(16)
        code = self._mint_code(state)
        client = self._client_with_state(state)

        response = self._callback(client, code, state)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(client.session["_auth_user_id"]), self.user.pk)

    def test_code_is_single_use(self):
        state = secrets.token_urlsafe(16)
        code = self._mint_code(state)

        first = self._client_with_state(state)
        self.assertEqual(self._callback(first, code, state).status_code, 302)

        # Replay the very same code in a fresh browser that also knows the state.
        second = self._client_with_state(state)
        self.assertEqual(self._callback(second, code, state).status_code, 400)
        self.assertNotIn("_auth_user_id", second.session)

    def test_unknown_code_rejected(self):
        state = secrets.token_urlsafe(16)
        client = self._client_with_state(state)
        response = self._callback(client, "not-a-real-code", state)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", client.session)

    def test_expired_code_rejected(self):
        state = secrets.token_urlsafe(16)
        code = self._mint_code(state)
        cache.delete(f"{CODE_CACHE_PREFIX}{code}")  # what TTL expiry looks like

        client = self._client_with_state(state)
        response = self._callback(client, code, state)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", client.session)

    def test_login_csrf_blocked_without_matching_state(self):
        """
        The attack: attacker mints a code from their own crush.lu session and
        walks a victim's browser through the callback, silently signing the
        victim in as the attacker. The victim's game-host session never held the
        attacker's state, so it must fail.
        """
        state = secrets.token_urlsafe(16)
        code = self._mint_code(state)

        victim = game_client()  # no state stashed — never went through /auth/start/
        response = self._callback(victim, code, state)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", victim.session)

    def test_mismatched_state_rejected(self):
        state = secrets.token_urlsafe(16)
        code = self._mint_code(state)

        client = self._client_with_state(secrets.token_urlsafe(16))  # different state
        response = self._callback(client, code, state)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", client.session)

    def test_state_is_consumed_so_it_cannot_be_reused(self):
        state = secrets.token_urlsafe(16)
        code = self._mint_code(state)
        client = self._client_with_state(state)
        self._callback(client, code, state)

        second_code = self._mint_code(state)
        response = self._callback(client, second_code, state)
        self.assertEqual(response.status_code, 400)

    def test_inactive_user_cannot_redeem(self):
        state = secrets.token_urlsafe(16)
        code = self._mint_code(state)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        client = self._client_with_state(state)
        response = self._callback(client, code, state)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("_auth_user_id", client.session)

    def test_auth_start_stashes_state_and_bounces_to_crush_lu(self):
        client = game_client()
        response = client.get(reverse("empire_auth_start"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/game/auth/handoff/", response["Location"])
        self.assertIn("state=", response["Location"])
        self.assertIn(STATE_SESSION_KEY, client.session)


@override_settings(**CRUSH_URLS)
class HandoffConsentGateTests(HandoffMixin, TestCase):
    """
    The handoff must sit outside CrushConsentMiddleware's exempt prefixes.

    '/api/' is prefix-exempt, so minting a code there would skip both the GDPR
    consent gate and the ban check — a banned user could still walk away with a
    game session that outlives the ban by SESSION_COOKIE_AGE (14 days).
    """

    def _attempt_mint(self):
        client = Client()
        client.force_login(self.user)
        with override_settings(EMPIRE_CALLBACK_ALLOWED_RETURN_URLS=ALLOWED_RETURNS):
            return client.get(
                reverse("empire_auth_handoff"),
                {"return": RETURN_URL, "state": "abc"},
            )

    def test_handoff_path_is_not_consent_exempt(self):
        from crush_lu.consent_middleware import CrushConsentMiddleware

        path = "/game/auth/handoff/"
        exempt = [
            p
            for p in CrushConsentMiddleware.EXEMPT_PATHS
            if path == p or path.startswith(p)
        ]
        self.assertEqual(
            exempt, [], f"{path} is exempt from consent/ban enforcement via {exempt}"
        )

    def test_api_prefix_would_have_been_exempt(self):
        """Guards the reasoning above: an /api/… handoff really would be exempt."""
        from crush_lu.consent_middleware import CrushConsentMiddleware

        self.assertIn("/api/", CrushConsentMiddleware.EXEMPT_PATHS)

    def test_unconsented_user_gets_no_code(self):
        consent = self.user.data_consent
        consent.crushlu_consent_given = False
        consent.save(update_fields=["crushlu_consent_given"])

        response = self._attempt_mint()
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("code=", response["Location"])

    def test_banned_user_gets_no_code(self):
        consent = self.user.data_consent
        consent.crushlu_banned = True
        consent.save(update_fields=["crushlu_banned"])

        response = self._attempt_mint()
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("code=", response["Location"])


@override_settings(**CRUSH_URLS)
class SpaBridgeUntouchedTests(HandoffMixin, TestCase):
    """
    The Empire handoff is a parallel path, not a widening of the hub SPA bridge.

    views_spa_auth.spa_session_callback gates on is_staff in both of its steps
    because it mints JWTs for the internal CRM. Admitting ordinary players there
    would hand a token-issuing path to every user. If someone ever "simplifies"
    the two flows into one, this fails.
    """

    @override_settings(
        SPA_CALLBACK_ALLOWED_RETURN_URLS={("https", "hub.crush.lu", "/auth/callback")}
    )
    def test_spa_callback_still_forbids_non_staff(self):
        client = Client()
        client.force_login(self.user)  # authenticated, not staff
        response = client.get(
            reverse("spa_session_callback"),
            {"return": "https://hub.crush.lu/auth/callback"},
        )
        self.assertEqual(response.status_code, 403)

    def test_empire_and_spa_use_different_cache_prefixes(self):
        from azureproject import views_spa_auth

        self.assertNotEqual(CODE_CACHE_PREFIX, views_spa_auth.CODE_CACHE_PREFIX)


@override_settings(**GAME_URLS)
class GameHostBanTests(HandoffMixin, TestCase):
    """
    CrushConsentMiddleware is scoped to the crush.lu urlconf, so it never runs
    here. A user banned *after* signing in would otherwise keep playing for the
    full 14-day session lifetime. empire_login_required re-checks.
    """

    def _logged_in_client(self):
        state = secrets.token_urlsafe(16)
        code = self._mint_code(state)
        client = game_client()
        session = client.session
        session[STATE_SESSION_KEY] = state
        session.save()
        client.get(reverse("empire_auth_callback"), {"code": code, "state": state})
        return client

    @override_settings(CRUSH_EMPIRE_ENABLED=True)
    def test_play_reachable_before_ban(self):
        client = self._logged_in_client()
        self.assertEqual(client.get(reverse("crush_empire:play")).status_code, 200)

    @override_settings(CRUSH_EMPIRE_ENABLED=True)
    def test_ban_takes_effect_on_existing_game_session(self):
        client = self._logged_in_client()

        consent = self.user.data_consent
        consent.crushlu_banned = True
        consent.save(update_fields=["crushlu_banned"])

        self.assertEqual(client.get(reverse("crush_empire:play")).status_code, 403)
