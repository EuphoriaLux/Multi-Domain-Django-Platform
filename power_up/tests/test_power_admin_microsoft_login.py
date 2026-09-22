"""Microsoft sign-in for the Power-Up admin.

power-up.lu has no public members, so a Microsoft login there is always a
staff login and must come from the company tenant. The tenant is read from the
access token: allauth's extra_data is the Graph /me profile, which has none.
"""

import base64
import json
import os
from types import SimpleNamespace
from unittest import mock

from allauth.core.exceptions import ImmediateHttpResponse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, RequestFactory, TestCase, override_settings

from allauth.socialaccount.models import SocialApp
from azureproject.adapters import (
    MultiDomainSocialAccountAdapter,
    _microsoft_tenant_id,
)
from power_up.admin import power_up_admin_site

COMPANY = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


def _jwt(claims):
    def part(data):
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


def _sociallogin(token, app_tenant=None):
    app_settings = {"tenant": app_tenant} if app_tenant else {}
    return SimpleNamespace(
        account=SimpleNamespace(
            provider="microsoft",
            uid="00000000-0000-0000-0000-000000000001",
            extra_data={"displayName": "Tom", "mail": "tom@example.test"},
        ),
        token=SimpleNamespace(token=token, app=SimpleNamespace(settings=app_settings)),
        is_existing=True,
        user=None,
        email_addresses=[],
    )


class MicrosoftTenantIdTests(TestCase):
    def test_reads_tid_from_a_work_account_token(self):
        login = _sociallogin(_jwt({"tid": COMPANY, "upn": "tom@example.test"}))
        self.assertEqual(_microsoft_tenant_id(login), COMPANY)

    def test_personal_account_tokens_are_opaque(self):
        self.assertIsNone(_microsoft_tenant_id(_sociallogin("EwBwA8l6BAAU...opaque")))

    def test_garbage_is_not_a_tenant(self):
        for token in (
            "",
            "a.b.c",
            "a.!!!.c",
            _jwt(["not", "a", "dict"]),
            _jwt({"tid": 7}),
        ):
            with self.subTest(token=token):
                self.assertIsNone(_microsoft_tenant_id(_sociallogin(token)))

    def test_an_app_pinned_to_a_tenant_is_the_stable_source(self):
        """Microsoft only signs in that tenant's users, whatever the token looks like."""
        login = _sociallogin("EwBwA8l6BAAU...opaque", app_tenant=COMPANY)
        self.assertEqual(_microsoft_tenant_id(login), COMPANY)

    def test_shared_authorities_fall_back_to_the_token(self):
        for authority in ("common", "Organizations", "consumers"):
            with self.subTest(authority=authority):
                login = _sociallogin(_jwt({"tid": OTHER}), app_tenant=authority)
                self.assertEqual(_microsoft_tenant_id(login), OTHER)
                opaque = _sociallogin("EwBwA8l6BAAU...opaque", app_tenant=authority)
                self.assertIsNone(_microsoft_tenant_id(opaque))

    def test_no_token_at_all(self):
        login = _sociallogin("")
        login.token = None
        self.assertIsNone(_microsoft_tenant_id(login))


@override_settings(ALLOWED_HOSTS=["*"])
class PowerUpMicrosoftLoginTenantTests(TestCase):
    def setUp(self):
        self.adapter = MultiDomainSocialAccountAdapter()

    def _run(self, host, token, next_url="", app_tenant=None):
        path = "/accounts/microsoft/login/callback/"
        if next_url:
            path += f"?next={next_url}"
        request = RequestFactory().get(path, HTTP_HOST=host)
        request.session = {}
        self.adapter.pre_social_login(request, _sociallogin(token, app_tenant))

    def assert_refused(self, host, token, next_url="", app_tenant=None):
        with self.assertRaises(ImmediateHttpResponse) as ctx:
            self._run(host, token, next_url, app_tenant)
        self.assertEqual(ctx.exception.response.status_code, 403)

    @mock.patch.dict(os.environ, {"GRAPH_TENANT_ID": COMPANY})
    def test_company_account_is_let_through_on_every_power_up_host(self):
        for host in ("power-up.lu", "www.powerup.lu", "portal.powerup.lu"):
            with self.subTest(host=host):
                self._run(host, _jwt({"tid": COMPANY}))

    @mock.patch.dict(os.environ, {"GRAPH_TENANT_ID": COMPANY})
    def test_other_tenants_and_personal_accounts_are_refused(self):
        self.assert_refused("power-up.lu", _jwt({"tid": OTHER}))
        self.assert_refused("power-up.lu", "EwBwA8l6BAAU...opaque")
        # Not tied to the ?next= target, which is empty at the callback.
        self.assert_refused("power-up.lu", _jwt({"tid": OTHER}), next_url="/")

    @mock.patch.dict(os.environ, {"GRAPH_TENANT_ID": COMPANY})
    def test_an_app_pinned_to_the_company_tenant_admits_any_token_format(self):
        self._run("power-up.lu", "EwBwA8l6BAAU...opaque", app_tenant=COMPANY)
        # A pin to someone else's tenant is still someone else's tenant.
        self.assert_refused("power-up.lu", _jwt({"tid": COMPANY}), app_tenant=OTHER)

    def test_fails_closed_without_a_configured_tenant(self):
        env = {k: v for k, v in os.environ.items() if k != "GRAPH_TENANT_ID"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assert_refused("power-up.lu", _jwt({"tid": COMPANY}))

    @mock.patch.dict(os.environ, {"GRAPH_TENANT_ID": COMPANY})
    def test_crush_consumer_logins_are_unchanged(self):
        """crush.lu members may use any Microsoft account; only admin logins are gated."""
        self._run("crush.lu", _jwt({"tid": OTHER}))
        self._run("crush.lu", "EwBwA8l6BAAU...opaque")


class PowerUpAdminPermissionTests(TestCase):
    def _allowed(self, **flags):
        user = get_user_model()(username="u", **flags)
        return power_up_admin_site.has_permission(SimpleNamespace(user=user))

    def test_staff_and_superusers_get_in(self):
        self.assertTrue(self._allowed(is_active=True, is_staff=True))
        # Mirrors the crush.lu admin, which the platform bar links from.
        self.assertTrue(self._allowed(is_active=True, is_superuser=True))

    def test_members_and_inactive_accounts_do_not(self):
        self.assertFalse(self._allowed(is_active=True))
        self.assertFalse(self._allowed(is_active=False, is_superuser=True))


class PowerUpSocialLoginErrorPageTests(TestCase):
    """The shared error page reversed crush_lu URLs and 500'd on power-up.lu."""

    def setUp(self):
        app = SocialApp.objects.create(
            provider="microsoft", name="Microsoft", client_id="id", secret="secret"
        )
        for domain in ("power-up.lu", "crush.lu"):
            Site.objects.get_or_create(domain=domain, defaults={"name": domain})
        # Whichever Site record the request resolves to, the app is linked.
        app.sites.set(Site.objects.all())
        Site.objects.clear_cache()

    def test_power_up_gets_the_neutral_page(self):
        response = Client(HTTP_HOST="power-up.lu").get(
            "/accounts/microsoft/login/callback/?error=access_denied"
        )
        # allauth answers a provider error with 401; before, power-up got a 500.
        self.assertEqual(response.status_code, 401)
        self.assertTemplateUsed(
            response, "socialaccount/authentication_error_neutral.html"
        )
        self.assertContains(response, 'href="/accounts/login/"', status_code=401)

    def test_crush_keeps_its_own_page(self):
        response = Client(HTTP_HOST="crush.lu").get(
            "/accounts/microsoft/login/callback/?error=access_denied"
        )
        self.assertEqual(response.status_code, 401)
        self.assertTemplateUsed(
            response, "socialaccount/authentication_error_crush.html"
        )
