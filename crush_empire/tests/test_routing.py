"""Routing and host-isolation tests for game.crush.lu."""
from django.test import Client, TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from azureproject.domains import (
    DEV_DOMAIN_MAPPINGS,
    get_domain_config,
    get_all_hosts,
    get_urlconf_for_host,
)

GAME_URLS = {"ROOT_URLCONF": "azureproject.urls_game"}


class GameHostRoutingTests(TestCase):
    def test_game_host_routes_to_urls_game(self):
        self.assertEqual(
            get_urlconf_for_host("game.crush.lu"), "azureproject.urls_game"
        )

    def test_staging_alias_routes_to_urls_game(self):
        self.assertEqual(
            get_urlconf_for_host("test.game.crush.lu"), "azureproject.urls_game"
        )

    def test_dev_mapping_routes_to_urls_game(self):
        self.assertEqual(DEV_DOMAIN_MAPPINGS["game.localhost"], "game.crush.lu")
        self.assertEqual(
            get_urlconf_for_host("game.localhost"), "azureproject.urls_game"
        )

    def test_game_host_is_in_allowed_hosts(self):
        hosts = get_all_hosts()
        self.assertIn("game.crush.lu", hosts)
        self.assertIn("test.game.crush.lu", hosts)

    def test_game_host_maps_to_crush_empire_app(self):
        self.assertEqual(get_domain_config("game.crush.lu")["app"], "crush_empire")

    def test_api_crush_lu_still_routes_to_hub(self):
        """The new sibling host must not disturb api.crush.lu."""
        self.assertEqual(get_urlconf_for_host("api.crush.lu"), "azureproject.urls_api")


@override_settings(**GAME_URLS)
class GameHostIsolationTests(TestCase):
    def setUp(self):
        # DomainURLRoutingMiddleware picks the urlconf from the Host header,
        # which overrides ROOT_URLCONF. Without this the request would be routed
        # to urls_crush and every assertion below would be meaningless.
        self.client = Client(HTTP_HOST="game.crush.lu")

    def test_no_allauth_on_game_host(self):
        """
        allauth must not be mounted here. Every OAuth provider's redirect URI
        points at crush.lu; a login form on this host would 404 on every social
        button, and mounting it would defeat the reason the handoff exists.
        """
        with self.assertRaises(NoReverseMatch):
            reverse("account_login")

        self.assertEqual(self.client.get("/accounts/login/").status_code, 404)

    def test_auth_endpoints_are_language_neutral(self):
        """crush.lu redirects here without a locale prefix."""
        self.assertEqual(reverse("empire_auth_callback"), "/auth/callback/")
        self.assertEqual(reverse("empire_auth_start"), "/auth/start/")

    def test_pages_are_language_prefixed(self):
        self.assertEqual(reverse("crush_empire:play"), "/en/play/")

    def test_healthz_available(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
