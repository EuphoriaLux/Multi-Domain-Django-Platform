"""
Comprehensive tests for Delegations.lu onboarding, tenant/domain matching,
nOAuth spoof-resistance, status routing, admin actions, and adapter URLs.
"""

import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from allauth.socialaccount.signals import pre_social_login, social_account_updated
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory, TestCase, override_settings

from azureproject.adapters import (
    MultiDomainAccountAdapter,
    MultiDomainSocialAccountAdapter,
)
from delegations.admin import DelegationProfileAdmin
from delegations.models import AccessLog, Company, DelegationProfile
from delegations.signals import (
    _thread_local,
    ensure_delegation_profile_for_user,
)
from delegations.views import _get_or_create_profile

User = get_user_model()


def _jwt_with_claims(claims: dict) -> str:
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode())
        .decode()
        .rstrip("=")
    )
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


@override_settings(
    ALLOWED_HOSTS=[
        "delegations.lu",
        "www.delegations.lu",
        "test.delegations.lu",
        "delegation.localhost",
        "crush.lu",
        "localhost",
        "127.0.0.1",
    ]
)
class DelegationsOnboardingAndSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.company = Company.objects.create(
            name="Acme Luxembourg",
            slug="acme-lu",
            email_domains=["acme.lu"],
            microsoft_tenant_id="11111111-2222-3333-4444-555555555555",
            is_active=True,
            auto_approve_workers=True,
        )

    def test_matches_company_by_jwt_access_token_tid_when_extra_data_lacks_tid(self):
        """
        Graph /me never populates extra_data['tid']. Ensure tenant matching
        reads tid from the Microsoft access token via _microsoft_tenant_id.
        """
        user = User.objects.create_user(
            username="worker_tid",
            email="worker@subsidiary.example",
            first_name="Alice",
            last_name="Worker",
        )
        app = SocialApp.objects.create(
            provider="microsoft", name="MS", client_id="cid", secret="sec"
        )
        account = SocialAccount.objects.create(
            user=user,
            provider="microsoft",
            uid="ms-uid-tid-1",
            extra_data={
                "id": "ms-uid-tid-1",
                "mail": "worker@subsidiary.example",
                "userPrincipalName": "worker@subsidiary.example",
                "jobTitle": "Software Engineer",
                "department": "Engineering",
            },
        )
        SocialToken.objects.create(
            app=app,
            account=account,
            token=_jwt_with_claims({"tid": "11111111-2222-3333-4444-555555555555"}),
        )

        profile, created = _get_or_create_profile(user)
        self.assertTrue(created)
        self.assertEqual(profile.company, self.company)
        self.assertEqual(
            profile.microsoft_tenant_id, "11111111-2222-3333-4444-555555555555"
        )
        self.assertEqual(profile.status, "approved")
        self.assertEqual(profile.role, "worker")
        self.assertTrue(profile.is_approved)
        self.assertTrue(
            AccessLog.objects.filter(profile=profile, action="auto_approved").exists()
        )

    def test_exact_email_domain_match_rejects_substring_domains(self):
        """
        Views previously used email_domains__icontains, which matched 'acme.lu'
        against 'notacme.lu' or matched 'me.lu' against 'acme.lu'. Verify only
        exact domain entries match.
        """
        Company.objects.create(
            name="Other Co",
            slug="other-co",
            email_domains=["notacme.lu"],
            is_active=True,
            auto_approve_workers=True,
        )
        user = User.objects.create_user(
            username="sub_user", email="bob@me.lu", first_name="Bob", last_name="Sub"
        )
        SocialAccount.objects.create(
            user=user,
            provider="microsoft",
            uid="ms-uid-sub-1",
            extra_data={
                "id": "ms-uid-sub-1",
                "mail": "bob@me.lu",
                "userPrincipalName": "bob@me.lu",
                "jobTitle": "Technician",
            },
        )

        profile, created = _get_or_create_profile(user)
        self.assertTrue(created)
        self.assertIsNone(profile.company)
        self.assertEqual(profile.status, "no_company")

    def test_noauth_spoofed_mail_attribute_does_not_match_company(self):
        """
        An external Entra tenant admin can set a user's writable `mail`
        attribute to 'victim@acme.lu' while their UPN is 'attacker@evil.lu'.
        Such an account must not auto-match or auto-approve into Acme.
        """
        user = User.objects.create_user(
            username="spoof_user",
            email="victim@acme.lu",
            first_name="Evil",
            last_name="Actor",
        )
        SocialAccount.objects.create(
            user=user,
            provider="microsoft",
            uid="ms-uid-spoof-1",
            extra_data={
                "id": "ms-uid-spoof-1",
                "mail": "victim@acme.lu",
                "userPrincipalName": "attacker@evil.onmicrosoft.com",
                "jobTitle": "Specialist",
            },
        )

        profile, created = _get_or_create_profile(user)
        self.assertTrue(created)
        self.assertIsNone(profile.company)
        self.assertEqual(profile.status, "pending")
        self.assertFalse(profile.is_approved)

    def test_external_b2b_guest_upn_is_held_for_admin_approval(self):
        """
        Even if a B2B guest authenticates against the company's tenant ID,
        an #EXT# UPN must not auto-approve into the company's delegation portal.
        """
        user = User.objects.create_user(
            username="guest_user",
            email="partner@acme.lu",
            first_name="Guest",
            last_name="User",
        )
        SocialAccount.objects.create(
            user=user,
            provider="microsoft",
            uid="ms-uid-guest-1",
            extra_data={
                "id": "ms-uid-guest-1",
                "tid": "11111111-2222-3333-4444-555555555555",
                "mail": "partner@acme.lu",
                "userPrincipalName": "partner_external.com#EXT#@acme.onmicrosoft.com",
                "jobTitle": "Consultant",
            },
        )

        profile, created = _get_or_create_profile(user)
        self.assertTrue(created)
        self.assertIsNone(profile.company)
        self.assertEqual(profile.status, "pending")
        self.assertFalse(profile.is_approved)

    def test_extended_management_keywords_and_consumer_domains_handled_uniformly(self):
        """
        Verify that keywords like 'Managing Director' / 'Vice President' and
        consumer domains like 'proton.me' behave identically through the unified
        onboarding helper.
        """
        vp_user = User.objects.create_user(
            username="vp_user", email="vp@acme.lu", first_name="Vic", last_name="Pres"
        )
        SocialAccount.objects.create(
            user=vp_user,
            provider="microsoft",
            uid="ms-uid-vp-1",
            extra_data={
                "id": "ms-uid-vp-1",
                "mail": "vp@acme.lu",
                "userPrincipalName": "vp@acme.lu",
                "jobTitle": "Vice President of Operations",
            },
        )
        vp_profile, _ = _get_or_create_profile(vp_user)
        self.assertEqual(vp_profile.company, self.company)
        self.assertEqual(vp_profile.status, "rejected")
        self.assertIn("Management role detected", vp_profile.rejection_reason)

        proton_user = User.objects.create_user(
            username="proton_user",
            email="person@proton.me",
            first_name="Pat",
            last_name="Personal",
        )
        SocialAccount.objects.create(
            user=proton_user,
            provider="microsoft",
            uid="ms-uid-proton-1",
            extra_data={
                "id": "ms-uid-proton-1",
                "mail": "person@proton.me",
                "userPrincipalName": "person@proton.me",
            },
        )
        proton_profile, _ = _get_or_create_profile(proton_user)
        self.assertIsNone(proton_profile.company)
        self.assertEqual(proton_profile.status, "pending")

    def test_multiple_profiles_without_microsoft_id_do_not_collide_on_unique(self):
        """
        When extra_data['id'] is missing or blank, microsoft_id is stored as
        None rather than '', avoiding IntegrityError on the second profile.
        """
        for idx in (1, 2):
            u = User.objects.create_user(
                username=f"noid_{idx}", email=f"u{idx}@acme.lu"
            )
            SocialAccount.objects.create(
                user=u,
                provider="microsoft",
                uid=f"uid-noid-{idx}",
                extra_data={
                    "mail": f"u{idx}@acme.lu",
                    "userPrincipalName": f"u{idx}@acme.lu",
                },
            )
            profile, created = ensure_delegation_profile_for_user(u)
            self.assertTrue(created)
            self.assertIsNone(profile.microsoft_id)

    def test_preexisting_microsoft_account_creates_profile_on_delegations_login(self):
        """
        A user who previously created a Microsoft SocialAccount on another host
        (e.g. crush.lu) and later logs into delegations.lu gets their
        DelegationProfile created during user_logged_in.
        """
        user = User.objects.create_user(
            username="cross_domain", email="worker2@acme.lu"
        )
        _thread_local.is_delegation_login = False
        SocialAccount.objects.create(
            user=user,
            provider="microsoft",
            uid="ms-uid-cross-1",
            extra_data={
                "id": "ms-uid-cross-1",
                "mail": "worker2@acme.lu",
                "userPrincipalName": "worker2@acme.lu",
                "jobTitle": "Analyst",
            },
        )
        self.assertFalse(DelegationProfile.objects.filter(user=user).exists())

        request = self.factory.get(
            "/accounts/microsoft/login/callback/", HTTP_HOST="delegations.lu"
        )
        user_logged_in.send(sender=User, request=request, user=user)

        profile = DelegationProfile.objects.get(user=user)
        self.assertTrue(profile.is_approved)
        self.assertIsNotNone(profile.last_login_at)
        self.assertTrue(
            AccessLog.objects.filter(profile=profile, action="login_success").exists()
        )

    def test_promotion_to_management_on_account_refresh_revokes_access(self):
        """
        If an approved worker's jobTitle changes to a management title on
        social_account_updated (and they aren't manually_approved), their
        status is updated to rejected and logged.
        """
        user = User.objects.create_user(username="promoted", email="dev@acme.lu")
        account = SocialAccount.objects.create(
            user=user,
            provider="microsoft",
            uid="ms-uid-promo-1",
            extra_data={
                "id": "ms-uid-promo-1",
                "mail": "dev@acme.lu",
                "userPrincipalName": "dev@acme.lu",
                "jobTitle": "Developer",
            },
        )
        profile, _ = ensure_delegation_profile_for_user(user, social_account=account)
        self.assertTrue(profile.is_approved)

        account.extra_data["jobTitle"] = "Chief Technology Officer"
        account.save()
        request = self.factory.get("/", HTTP_HOST="delegations.lu")
        sociallogin = SimpleNamespace(account=account, user=user, token=None)
        social_account_updated.send(
            sender=SocialAccount, request=request, sociallogin=sociallogin
        )

        profile.refresh_from_db()
        self.assertEqual(profile.status, "rejected")
        self.assertFalse(profile.is_approved)
        self.assertTrue(
            AccessLog.objects.filter(profile=profile, action="auto_rejected").exists()
        )

    @patch("delegations.signals.requests.get")
    def test_photo_download_uses_unsaved_sociallogin_token_during_post_save(
        self, mock_get
    ):
        """
        During allauth signup, SocialAccount post_save fires before SocialToken
        is saved to DB. Ensure photo download uses the token on sociallogin.
        """
        mock_get.return_value = SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "image/jpeg"},
            content=b"\xff\xd8\xff\xe0fake-jpeg",
        )
        user = User.objects.create_user(username="photo_user", email="pic@acme.lu")
        unsaved_account = SocialAccount(
            user=user,
            provider="microsoft",
            uid="ms-uid-photo-1",
            extra_data={
                "id": "ms-uid-photo-1",
                "mail": "pic@acme.lu",
                "userPrincipalName": "pic@acme.lu",
                "jobTitle": "Designer",
            },
        )
        sociallogin = SimpleNamespace(
            account=unsaved_account,
            user=user,
            is_existing=False,
            token=SimpleNamespace(token="graph-access-token", app=None),
        )
        request = self.factory.get(
            "/accounts/microsoft/login/callback/", HTTP_HOST="delegations.lu"
        )
        pre_social_login.send(
            sender=SocialAccount, request=request, sociallogin=sociallogin
        )
        unsaved_account.save()

        profile = DelegationProfile.objects.get(user=user)
        self.assertTrue(bool(profile.profile_photo))
        mock_get.assert_called_once()

    @patch("delegations.signals.requests.get")
    def test_stale_thread_local_sociallogin_never_leaks_across_accounts_or_requests(
        self, mock_get
    ):
        """
        If _thread_local.sociallogin holds User A's Microsoft login (e.g. from
        an earlier request on a reused worker), creating or onboarding User B's
        SocialAccount must never consume User A's token for tenant extraction
        or photo download, and request boundaries clear _thread_local.
        """
        from django.core.signals import request_finished

        user_a = User.objects.create_user(username="user_a", email="a@acme.lu")
        account_a = SocialAccount(
            user=user_a,
            provider="microsoft",
            uid="ms-uid-user-a",
            extra_data={
                "id": "ms-uid-user-a",
                "mail": "a@acme.lu",
                "userPrincipalName": "a@acme.lu",
            },
        )
        sociallogin_a = SimpleNamespace(
            account=account_a,
            user=user_a,
            is_existing=False,
            token=SimpleNamespace(
                token=_jwt_with_claims({"tid": "11111111-2222-3333-4444-555555555555"}),
                app=None,
            ),
        )
        req_a = self.factory.get(
            "/accounts/microsoft/login/callback/", HTTP_HOST="delegations.lu"
        )
        pre_social_login.send(
            sender=SocialAccount, request=req_a, sociallogin=sociallogin_a
        )

        # User B is created while _thread_local still points to sociallogin_a
        user_b = User.objects.create_user(
            username="user_b", email="b@unmatched-domain.example"
        )
        account_b = SocialAccount.objects.create(
            user=user_b,
            provider="microsoft",
            uid="ms-uid-user-b",
            extra_data={
                "id": "ms-uid-user-b",
                "mail": "b@unmatched-domain.example",
                "userPrincipalName": "b@unmatched-domain.example",
                "jobTitle": "Worker",
            },
        )
        # Stale post_save was ignored and cleared _thread_local
        self.assertFalse(DelegationProfile.objects.filter(user=user_b).exists())
        self.assertFalse(_thread_local.is_delegation_login)
        self.assertIsNone(_thread_local.sociallogin)

        # Even if ensure_delegation_profile_for_user is called directly with
        # sociallogin_a for user_b, user_a's token/tenant/photo must be ignored.
        profile_b, created = ensure_delegation_profile_for_user(
            user_b, social_account=account_b, sociallogin=sociallogin_a
        )
        self.assertTrue(created)
        self.assertIsNone(profile_b.company)
        self.assertEqual(profile_b.microsoft_tenant_id, "")
        self.assertEqual(profile_b.status, "no_company")
        self.assertFalse(bool(profile_b.profile_photo))
        mock_get.assert_not_called()

        # Verify request_finished also resets _thread_local
        _thread_local.is_delegation_login = True
        _thread_local.sociallogin = sociallogin_a
        request_finished.send(sender=self.__class__)
        self.assertFalse(_thread_local.is_delegation_login)
        self.assertIsNone(_thread_local.sociallogin)


@override_settings(
    ALLOWED_HOSTS=[
        "delegations.lu",
        "www.delegations.lu",
        "test.delegations.lu",
        "delegation.localhost",
    ]
)
class DelegationsRoutingAndAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.company = Company.objects.create(
            name="Beta S.A.",
            slug="beta-sa",
            email_domains=["beta.lu"],
            is_active=True,
            auto_approve_workers=True,
        )
        self.user = User.objects.create_user(
            username="beta_worker",
            email="worker@beta.lu",
            password="unused-password",
        )
        SocialAccount.objects.create(
            user=self.user,
            provider="microsoft",
            uid="ms-beta-1",
            extra_data={
                "id": "ms-beta-1",
                "mail": "worker@beta.lu",
                "userPrincipalName": "worker@beta.lu",
                "jobTitle": "Operator",
            },
        )
        self.profile, _ = ensure_delegation_profile_for_user(self.user)

    def test_manually_blocked_user_routes_to_access_denied_everywhere(self):
        """
        When an approved profile is manually_blocked by an admin (status remains
        'approved'), home, dashboard, no_company, pending_approval, and the
        account adapter redirect must all route to /access-denied/.
        """
        self.profile.manually_blocked = True
        self.profile.save(update_fields=["manually_blocked"])
        self.assertFalse(self.profile.is_approved)

        client = Client(HTTP_HOST="delegations.lu")
        client.force_login(self.user)

        for path in ("/", "/dashboard/", "/no-company/", "/pending-approval/"):
            with self.subTest(path=path):
                resp = client.get(path)
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(resp["Location"], "/access-denied/")

        denied_resp = client.get("/access-denied/")
        self.assertEqual(denied_resp.status_code, 200)
        self.assertIn("blocked by an administrator", denied_resp.content.decode())

        adapter = MultiDomainAccountAdapter()
        for host in ("delegations.lu", "test.delegations.lu", "delegation.localhost"):
            req = self.factory.get("/", HTTP_HOST=host)
            req.user = self.user
            self.assertEqual(adapter.get_login_redirect_url(req), "/access-denied/")

    def test_blocked_user_pre_social_login_raises_permission_denied_and_logs(self):
        self.profile.manually_blocked = True
        self.profile.save(update_fields=["manually_blocked"])

        account = SocialAccount.objects.get(user=self.user, provider="microsoft")
        sociallogin = SimpleNamespace(
            account=account, user=self.user, is_existing=True, token=None
        )
        request = self.factory.get(
            "/accounts/microsoft/login/callback/",
            HTTP_HOST="delegations.lu",
            REMOTE_ADDR="192.0.2.10",
        )
        with self.assertRaises(PermissionDenied):
            pre_social_login.send(
                sender=SocialAccount, request=request, sociallogin=sociallogin
            )
        self.assertTrue(
            AccessLog.objects.filter(
                profile=self.profile, action="login_blocked", ip_address="192.0.2.10"
            ).exists()
        )

    def test_admin_approve_action_clears_manual_block_and_preserves_delegate_role(self):
        admin_user = User.objects.create_superuser(
            username="admin_del", email="admin@powerup.lu", password="x"
        )
        self.profile.role = "staff_delegate"
        self.profile.status = "pending"
        self.profile.manually_blocked = True
        self.profile.save()

        model_admin = DelegationProfileAdmin(DelegationProfile, AdminSite())
        request = self.factory.post("/delegation-admin/delegations/delegationprofile/")
        request.user = admin_user
        request.session = "session"
        request._messages = FallbackStorage(request)

        model_admin.approve_profiles(
            request, DelegationProfile.objects.filter(pk=self.profile.pk)
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_approved)
        self.assertFalse(self.profile.manually_blocked)
        self.assertEqual(self.profile.status, "approved")
        self.assertEqual(self.profile.role, "staff_delegate")
        self.assertTrue(
            AccessLog.objects.filter(
                profile=self.profile, action="manual_approved"
            ).exists()
        )

    def test_adapter_urls_and_staging_alias_parity(self):
        account_adapter = MultiDomainAccountAdapter()
        social_adapter = MultiDomainSocialAccountAdapter()

        for host in ("delegations.lu", "test.delegations.lu", "delegation.localhost"):
            with self.subTest(host=host):
                req = self.factory.get("/", HTTP_HOST=host)
                req.user = self.user
                self.assertEqual(account_adapter.get_login_url(req), "/accounts/login/")
                self.assertEqual(
                    social_adapter.get_connect_redirect_url(req, None), "/profile/"
                )
                self.assertFalse(account_adapter.is_open_for_signup(req))
                self.assertTrue(
                    social_adapter.is_open_for_signup(req, sociallogin=None)
                )


@override_settings(
    ALLOWED_HOSTS=[
        "delegations.lu",
        "www.delegations.lu",
        "test.delegations.lu",
        "delegation.localhost",
    ]
)
class DelegationsMarketingAndSEOTests(TestCase):
    """
    Tests for the multilingual SEO marketing frontpage (/en/, /fr/, /de/),
    robots.txt, sitemap.xml, and the employment-issue triage prototype.
    """

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="delegations.lu")

    def test_multilingual_frontpage_renders_with_seo_tags_and_translations(self):
        cases = [
            (
                "/en/",
                "en",
                "Facing an Employment Issue in Luxembourg?",
                "https://delegations.lu/en/",
            ),
            (
                "/fr/",
                "fr",
                "Un problème au travail au Luxembourg ?",
                "https://delegations.lu/fr/",
            ),
            (
                "/de/",
                "de",
                "Probleme am Arbeitsplatz in Luxemburg?",
                "https://delegations.lu/de/",
            ),
        ]
        for path, lang, expected_heading, expected_canonical in cases:
            with self.subTest(path=path, lang=lang):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200)
                html = resp.content.decode("utf-8")
                self.assertIn(f'<html lang="{lang}">', html)
                self.assertIn(expected_heading, html)
                self.assertIn(
                    f'<link rel="canonical" href="{expected_canonical}">', html
                )
                self.assertIn(
                    '<link rel="alternate" hreflang="en" href="https://delegations.lu/en/">',
                    html,
                )
                self.assertIn(
                    '<link rel="alternate" hreflang="de" href="https://delegations.lu/de/">',
                    html,
                )
                self.assertIn(
                    '<link rel="alternate" hreflang="fr" href="https://delegations.lu/fr/">',
                    html,
                )
                self.assertIn(
                    '<link rel="alternate" hreflang="x-default" href="https://delegations.lu/fr/">',
                    html,
                )
                self.assertIn('"@type": "FAQPage"', html)
                self.assertIn('details class="faq-item" name="delegations-faq"', html)

    def test_robots_txt_and_sitemap_xml_on_delegations_domain(self):
        robots_resp = self.client.get("/robots.txt")
        self.assertEqual(robots_resp.status_code, 200)
        robots_body = robots_resp.content.decode("utf-8")
        self.assertIn("Allow: /en/", robots_body)
        self.assertIn("Allow: /fr/", robots_body)
        self.assertIn("Allow: /de/", robots_body)
        self.assertIn("Disallow: /dashboard/", robots_body)
        self.assertIn("Sitemap: https://delegations.lu/sitemap.xml", robots_body)

        sitemap_resp = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap_resp.status_code, 200)
        sitemap_body = sitemap_resp.content.decode("utf-8")
        self.assertIn("/en/", sitemap_body)
        self.assertIn("/fr/", sitemap_body)
        self.assertIn("/de/", sitemap_body)

    def test_employment_issue_prototype_intake_valid_submission_across_languages(self):
        payload = {
            "issue_category": "dismissal",
            "urgency": "urgent",
            "company_size": "15_plus",
            "has_delegation": "yes",
            "situation_summary": "I received a dismissal letter with notice yesterday after 4 years in the company.",
            "contact_email": "employee@example.lu",
            "preferred_language": "fr",
            "consent": "yes",
        }

        for path, expected_snippet in (
            ("/en/", "DLG-LU-"),
            ("/fr/", "Code du travail luxembourgeois (Art. L.124-5"),
            ("/de/", "Luxemburgisches Arbeitsgesetzbuch (Art. L.124-5"),
        ):
            with self.subTest(path=path):
                resp = self.client.post(path, data=payload)
                self.assertEqual(resp.status_code, 200)
                self.assertIsNotNone(resp.context["intake_result"])
                self.assertEqual(resp.context["form_errors"], {})
                html = resp.content.decode("utf-8")
                self.assertIn(expected_snippet, html)
                self.assertIn("DLG-LU-", html)

    def test_employment_issue_prototype_intake_invalid_submission_returns_errors(self):
        resp = self.client.post(
            "/fr/",
            data={
                "issue_category": "",
                "urgency": "urgent",
                "company_size": "15_plus",
                "situation_summary": "Trop court",
                "contact_email": "not-an-email",
                "preferred_language": "fr",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["intake_result"])
        errors = resp.context["form_errors"]
        self.assertIn("issue_category", errors)
        self.assertIn("situation_summary", errors)
        self.assertIn("contact_email", errors)
        self.assertIn("consent", errors)
        html = resp.content.decode("utf-8")
        self.assertIn('aria-invalid="true"', html)
