"""Shared Crush processors must not query or expose state on other platforms."""

from datetime import date
from types import ModuleType
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings

from azureproject.domains import DOMAINS, DEV_DOMAIN_MAPPINGS
from azureproject.middleware import DomainURLRoutingMiddleware
from crush_lu.context_processors import (
    _site_config_cache,
    crush_user_context,
    site_config_context,
)
from crush_lu.models import CrushProfile, CrushSiteConfig


@override_settings(ALLOWED_HOSTS=["*"])
class ContextPlatformGateTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="context-gate@example.com", email="context-gate@example.com"
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1993, 3, 15),
            gender="M",
            location="Luxembourg City",
            verification_status="verified",
            is_approved=True,
            is_active=True,
        )
        self.enterContext(
            patch.dict(_site_config_cache, {"config": None, "expires": 0}, clear=True)
        )

    def _request(self, host="crush.lu", path="/en/dashboard/", *, routed=True):
        request = self.factory.get(path, HTTP_HOST=host)
        request.user = self.user
        request.session = {}
        if routed:
            DomainURLRoutingMiddleware(lambda req: req)(request)
        return request

    def _assert_no_crush_context(self, request):
        with self.assertNumQueries(0):
            user_context = crush_user_context(request)
            config_context = site_config_context(request)
        self.assertEqual(user_context, {})
        self.assertEqual(config_context, {})

    def test_all_other_platforms_and_aliases_skip_cold_context_queries(self):
        hosts = []
        for domain, config in DOMAINS.items():
            if config["app"] != "crush_lu":
                hosts.extend([domain, *config.get("aliases", [])])
        hosts.extend(
            host
            for host, domain in DEV_DOMAIN_MAPPINGS.items()
            if DOMAINS[domain]["app"] != "crush_lu"
        )
        for host in hosts:
            with self.subTest(host=host):
                self._assert_no_crush_context(self._request(host))
        self.assertIsNone(_site_config_cache["config"])

    def test_other_platform_anonymous_page_skips_site_config_query(self):
        request = self._request("power-up.lu")
        request.user = AnonymousUser()
        self._assert_no_crush_context(request)

    def test_other_platform_admin_routes_skip_crush_queries(self):
        for host in ("power-up.lu", "portal.powerup.lu", "entreprinder.lu"):
            with self.subTest(host=host):
                self._assert_no_crush_context(self._request(host, "/admin/"))

    def test_warm_crush_site_config_does_not_leak_to_another_platform(self):
        CrushSiteConfig.objects.create(
            pk=1, whatsapp_enabled=True, whatsapp_number="352123456789"
        )
        crush_context = site_config_context(self._request())
        self.assertEqual(crush_context["whatsapp_number"], "352123456789")
        self.assertTrue(crush_context["whatsapp_enabled"])
        self._assert_no_crush_context(self._request("power-up.lu"))

    def test_crush_aliases_and_local_hosts_keep_member_state(self):
        for host in (
            "crush.lu",
            "www.crush.lu",
            "test.crush.lu",
            "crush.localhost:8000",
            "localhost:8000",
            "127.0.0.1:8000",
            "testserver",
        ):
            with self.subTest(host=host):
                context = crush_user_context(self._request(host))
                self.assertEqual(context["profile"].pk, self.profile.pk)
                self.assertTrue(context["nav_has_profile"])
                self.assertTrue(context["profile_is_approved"])
                self.assertTrue(context["prompt_install_eligible"])
                self.assertFalse(context["prompt_push_eligible"])

    def test_crush_admin_routes_keep_existing_context(self):
        for path in ("/admin/", "/crush-admin/"):
            with self.subTest(path=path):
                context = crush_user_context(self._request(path=path))
                self.assertEqual(context["profile"].pk, self.profile.pk)
                self.assertTrue(context["nav_has_profile"])

    def test_member_eligibility_is_never_shared_between_requests(self):
        first = crush_user_context(self._request())
        other = get_user_model().objects.create_user(
            username="context-other@example.com", email="context-other@example.com"
        )
        other_request = self._request()
        other_request.user = other
        second = crush_user_context(other_request)
        self.assertTrue(first["prompt_install_eligible"])
        self.assertIsNone(second["profile"])
        self.assertFalse(second["nav_has_profile"])
        self.assertFalse(second["prompt_install_eligible"])

    def test_middleware_selected_urlconf_takes_precedence_over_host(self):
        request = self._request()
        request.urlconf = DOMAINS["power-up.lu"]["urlconf"]
        self._assert_no_crush_context(request)
        request = self._request("power-up.lu")
        request.urlconf = DOMAINS["crush.lu"]["urlconf"]
        context = crush_user_context(request)
        self.assertEqual(context["profile"].pk, self.profile.pk)

    def test_selected_urlconf_can_be_a_module(self):
        request = self._request()
        request.urlconf = ModuleType(DOMAINS["crush.lu"]["urlconf"])
        self.assertTrue(crush_user_context(request)["nav_has_profile"])
        request.urlconf = ModuleType(DOMAINS["power-up.lu"]["urlconf"])
        self._assert_no_crush_context(request)

    def test_without_middleware_resolves_aliases_and_non_crush_hosts(self):
        for host in ("www.crush.lu", "crush.localhost:8000"):
            with self.subTest(host=host):
                context = crush_user_context(self._request(host, routed=False))
                self.assertEqual(context["profile"].pk, self.profile.pk)
        self._assert_no_crush_context(self._request("power-up.lu", routed=False))

    def test_without_middleware_obeys_configurable_dev_default(self):
        with patch("azureproject.domains.DEV_DEFAULT", "power-up.lu"):
            self._assert_no_crush_context(self._request("localhost:8000", routed=False))
        with patch("azureproject.domains.DEV_DEFAULT", "crush.lu"):
            context = crush_user_context(self._request("localhost:8000", routed=False))
            self.assertEqual(context["profile"].pk, self.profile.pk)

    def test_azure_and_unknown_hosts_follow_production_fallback(self):
        for host in ("platform.azurewebsites.net", "unknown.example"):
            with self.subTest(host=host):
                self._assert_no_crush_context(self._request(host))
                self._assert_no_crush_context(self._request(host, routed=False))

    def test_crush_anonymous_flags_remain_available_without_user_queries(self):
        request = self._request()
        request.user = AnonymousUser()
        with self.assertNumQueries(0):
            context = crush_user_context(request)
        self.assertIn("is_native_app", context)
        self.assertNotIn("profile", context)
