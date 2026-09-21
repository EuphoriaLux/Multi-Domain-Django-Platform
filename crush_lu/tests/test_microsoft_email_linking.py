"""Email-based account linking for Microsoft sign-in.

SOCIALACCOUNT_EMAIL_AUTHENTICATION(_AUTO_CONNECT) lets a social login sign
straight into the existing account that already owns its email address, and
links the two permanently. allauth's Microsoft provider takes that address
from the Graph ``/me`` profile's ``mail`` field, which any tenant
administrator can point at an address they do not own -- so the address has to
be checked against something the tenant had to prove.
"""

from allauth.account.models import EmailAddress
from allauth.core import context
from allauth.socialaccount.adapter import get_adapter as get_socialaccount_adapter
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialLogin
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase

from azureproject.adapters import (
    MultiDomainSocialAccountAdapter,
    _microsoft_email_is_tenant_owned,
)

VICTIM_EMAIL = "victim@example.test"
COMPANY_DOMAIN = "contoso.test"
ATTACKER_TENANT = "evil.onmicrosoft.test"


class SocialAppTestCase(TestCase):
    """Resolving a SocialLogin's provider needs an app and a request in scope."""

    def setUp(self):
        super().setUp()
        Site.objects.get_or_create(domain="crush.lu", defaults={"name": "crush.lu"})
        for provider in ("microsoft", "google"):
            app = SocialApp.objects.create(
                provider=provider, name=provider, client_id="id", secret="secret"
            )
            # conftest pins SITE_ID for the test run, so link every Site rather
            # than guess which one a request resolves to.
            app.sites.set(Site.objects.all())
        Site.objects.clear_cache()
        self.request = RequestFactory().get(
            "/accounts/microsoft/login/callback/", HTTP_HOST="crush.lu"
        )
        self.request.session = {}
        ctx = context.request_context(self.request)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)


def _graph_profile(upn, mail=None):
    """The subset of the Graph /me profile allauth stores in extra_data."""
    return {
        "id": "00000000-0000-0000-0000-000000000009",
        "displayName": "Someone",
        "givenName": "Some",
        "surname": "One",
        "userPrincipalName": upn,
        "mail": mail,
    }


def _sociallogin(extra_data, email, provider="microsoft", uid="graph-uid-1"):
    """A SocialLogin as allauth builds it at the OAuth callback.

    Needs a request and the provider's SocialApp in scope -- see
    SocialAppTestCase.
    """
    return SocialLogin(
        user=get_user_model()(email=email),
        provider=get_socialaccount_adapter().get_provider(
            context.request, provider=provider
        ),
        account=SocialAccount(provider=provider, uid=uid, extra_data=extra_data),
        # VERIFIED_EMAIL: True makes allauth stamp these verified.
        email_addresses=[EmailAddress(email=email, verified=True, primary=True)],
    )


class MicrosoftTenantOwnedEmailTests(TestCase):
    """What Microsoft actually vouches for."""

    def test_mail_on_the_upn_domain_is_owned(self):
        # The ordinary corporate shape: the UPN and the mailbox differ, but
        # both sit on a domain the tenant verified with Microsoft.
        profile = _graph_profile(f"t.scheuer@{COMPANY_DOMAIN}", f"tom@{COMPANY_DOMAIN}")
        self.assertTrue(
            _microsoft_email_is_tenant_owned(profile, f"tom@{COMPANY_DOMAIN}")
        )

    def test_mail_on_any_other_domain_is_not(self):
        """The nOAuth pattern: a writable `mail` pointed at someone else."""
        profile = _graph_profile(f"attacker@{ATTACKER_TENANT}", VICTIM_EMAIL)
        self.assertFalse(_microsoft_email_is_tenant_owned(profile, VICTIM_EMAIL))

    def test_personal_microsoft_accounts_are_owned(self):
        # Graph reports the MSA's own address as both mail and UPN.
        profile = _graph_profile("someone@outlook.test", "someone@outlook.test")
        self.assertTrue(
            _microsoft_email_is_tenant_owned(profile, "someone@outlook.test")
        )

    def test_guest_accounts_prove_nothing(self):
        """A B2B guest UPN is the invited address mangled into the host tenant."""
        profile = _graph_profile(
            f"victim_example.test#EXT#@{ATTACKER_TENANT}", VICTIM_EMAIL
        )
        self.assertFalse(_microsoft_email_is_tenant_owned(profile, VICTIM_EMAIL))

    def test_case_and_whitespace_do_not_decide_ownership(self):
        profile = _graph_profile(f"  T.Scheuer@{COMPANY_DOMAIN.upper()}  ")
        self.assertTrue(
            _microsoft_email_is_tenant_owned(profile, f"TOM@{COMPANY_DOMAIN}")
        )

    def test_nothing_to_compare_against_is_not_ownership(self):
        for profile, email in (
            (_graph_profile("", VICTIM_EMAIL), VICTIM_EMAIL),
            (_graph_profile(None, VICTIM_EMAIL), VICTIM_EMAIL),
            ({}, VICTIM_EMAIL),
            (_graph_profile(f"tom@{COMPANY_DOMAIN}"), ""),
            (_graph_profile(f"tom@{COMPANY_DOMAIN}"), None),
            # No domain at all on either side.
            (_graph_profile("tom"), "tom"),
        ):
            with self.subTest(upn=profile.get("userPrincipalName"), email=email):
                self.assertFalse(_microsoft_email_is_tenant_owned(profile, email))


class EmailAutoConnectTests(SocialAppTestCase):
    """Who may claim an existing account by presenting its email address."""

    def setUp(self):
        super().setUp()
        self.adapter = MultiDomainSocialAccountAdapter()
        self.victim = get_user_model().objects.create_user(
            username="victim", email=VICTIM_EMAIL, password="pw-victim"
        )
        EmailAddress.objects.create(
            user=self.victim, email=VICTIM_EMAIL, verified=True, primary=True
        )

    def _authenticate(self, extra_data, email, provider="microsoft"):
        return self.adapter.authenticate_by_email(
            _sociallogin(extra_data, email, provider=provider)
        )

    def test_a_foreign_tenant_cannot_claim_a_members_account(self):
        """The attack: any Entra admin can set `mail` to a member's address."""
        match = self._authenticate(
            _graph_profile(f"attacker@{ATTACKER_TENANT}", VICTIM_EMAIL),
            VICTIM_EMAIL,
        )
        self.assertIsNone(match)

    def test_a_member_whose_tenant_owns_the_address_still_signs_in(self):
        member = get_user_model().objects.create_user(
            username="member", email=f"tom@{COMPANY_DOMAIN}", password="pw-member"
        )
        EmailAddress.objects.create(
            user=member, email=member.email, verified=True, primary=True
        )
        match = self._authenticate(
            _graph_profile(f"t.scheuer@{COMPANY_DOMAIN}", member.email),
            member.email,
        )
        self.assertEqual(match, (member, member.email))

    def test_personal_microsoft_accounts_still_sign_in(self):
        """crush.lu members mostly use personal accounts; they keep working."""
        msa = "someone@outlook.test"
        member = get_user_model().objects.create_user(
            username="msa-member", email=msa, password="pw-msa"
        )
        EmailAddress.objects.create(user=member, email=msa, verified=True, primary=True)
        self.assertEqual(
            self._authenticate(_graph_profile(msa, msa), msa), (member, msa)
        )

    def test_other_providers_are_untouched(self):
        """Google vouches for its own addresses; the Microsoft rule is not applied."""
        match = self._authenticate({"email": VICTIM_EMAIL}, VICTIM_EMAIL, "google")
        self.assertEqual(match, (self.victim, VICTIM_EMAIL))

    def test_no_local_account_is_still_no_match(self):
        self.assertIsNone(
            self._authenticate(
                _graph_profile(f"tom@{COMPANY_DOMAIN}", f"tom@{COMPANY_DOMAIN}"),
                f"tom@{COMPANY_DOMAIN}",
            )
        )


class PrivilegedAccountAutoConnectTests(SocialAppTestCase):
    """Admin accounts are linked deliberately, never by an OAuth callback."""

    def setUp(self):
        super().setUp()
        self.adapter = MultiDomainSocialAccountAdapter()

    def _claim(self, user, provider="microsoft"):
        email = user.email
        extra = _graph_profile(email, email) if provider == "microsoft" else {}
        return self.adapter.authenticate_by_email(
            _sociallogin(extra, email, provider=provider)
        )

    def test_a_coachs_account_is_not_auto_connected(self):
        # crush_lu.signals.manage_coach_staff_status grants coaches is_staff,
        # which is what /crush-admin/ checks.
        coach = get_user_model().objects.create_user(
            username="coach", email=f"coach@{COMPANY_DOMAIN}", password="pw-coach"
        )
        coach.is_staff = True
        coach.save(update_fields=["is_staff"])
        EmailAddress.objects.create(
            user=coach, email=coach.email, verified=True, primary=True
        )
        self.assertIsNone(self._claim(coach))

    def test_a_superusers_account_is_not_auto_connected(self):
        admin = get_user_model().objects.create_superuser(
            username="admin", email=f"admin@{COMPANY_DOMAIN}", password="pw-admin"
        )
        EmailAddress.objects.create(
            user=admin, email=admin.email, verified=True, primary=True
        )
        self.assertIsNone(self._claim(admin))
        # Not a Microsoft-only rule: any provider would be claiming the same
        # account on the same evidence.
        self.assertIsNone(self._claim(admin, provider="google"))

    def test_an_ordinary_member_is_unaffected(self):
        member = get_user_model().objects.create_user(
            username="plain", email=f"plain@{COMPANY_DOMAIN}", password="pw-plain"
        )
        EmailAddress.objects.create(
            user=member, email=member.email, verified=True, primary=True
        )
        self.assertEqual(self._claim(member), (member, member.email))


class SocialLoginLookupTests(SocialAppTestCase):
    """The same rules seen through allauth's own lookup(), as at the callback."""

    def setUp(self):
        super().setUp()
        self.victim = get_user_model().objects.create_user(
            username="victim", email=VICTIM_EMAIL, password="pw-victim"
        )
        EmailAddress.objects.create(
            user=self.victim, email=VICTIM_EMAIL, verified=True, primary=True
        )

    def test_a_spoofed_mail_does_not_resolve_to_the_victim(self):
        sociallogin = _sociallogin(
            _graph_profile(f"attacker@{ATTACKER_TENANT}", VICTIM_EMAIL),
            VICTIM_EMAIL,
        )
        sociallogin.lookup()
        self.assertFalse(sociallogin.is_existing)
        self.assertNotEqual(sociallogin.user.pk, self.victim.pk)

    def test_an_already_linked_account_signs_in_regardless(self):
        """Staff who linked Microsoft before keep signing in: lookup matches
        the stored social account and never consults the email at all."""
        self.victim.is_staff = True
        self.victim.save(update_fields=["is_staff"])
        SocialAccount.objects.create(
            user=self.victim, provider="microsoft", uid="graph-uid-1"
        )
        sociallogin = _sociallogin(
            _graph_profile(f"attacker@{ATTACKER_TENANT}", VICTIM_EMAIL),
            VICTIM_EMAIL,
        )
        sociallogin.lookup()
        self.assertTrue(sociallogin.is_existing)
        self.assertEqual(sociallogin.user.pk, self.victim.pk)


class MicrosoftAddressVerificationTests(SocialAppTestCase):
    """What gets written to EmailAddress.verified for a brand-new signup."""

    def setUp(self):
        super().setUp()
        self.adapter = MultiDomainSocialAccountAdapter()

    def _pre_social_login(self, sociallogin, host="crush.lu"):
        request = RequestFactory().get(
            "/accounts/microsoft/login/callback/", HTTP_HOST=host
        )
        request.session = {}
        self.adapter.pre_social_login(request, sociallogin)
        return sociallogin

    def test_an_unproven_address_is_not_recorded_as_verified(self):
        """Otherwise the real owner can never register it here (ACCOUNT_UNIQUE_EMAIL)."""
        sociallogin = self._pre_social_login(
            _sociallogin(
                _graph_profile(f"attacker@{ATTACKER_TENANT}", VICTIM_EMAIL),
                VICTIM_EMAIL,
            )
        )
        self.assertFalse(sociallogin.email_addresses[0].verified)

    def test_an_owned_address_stays_verified(self):
        email = f"tom@{COMPANY_DOMAIN}"
        sociallogin = self._pre_social_login(
            _sociallogin(_graph_profile(f"t.scheuer@{COMPANY_DOMAIN}", email), email)
        )
        self.assertTrue(sociallogin.email_addresses[0].verified)

    def test_other_providers_keep_their_verified_addresses(self):
        sociallogin = self._pre_social_login(
            _sociallogin({"email": VICTIM_EMAIL}, VICTIM_EMAIL, provider="google")
        )
        self.assertTrue(sociallogin.email_addresses[0].verified)
