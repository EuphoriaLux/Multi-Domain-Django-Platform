"""Settings parity on the account drill-down (UX Wave 4 · WP9a, finding 8-08).

The drill-down (/profile/edit/?section=account[&sub=...]) is the surface that
survives the settings merge. Before /account/settings/ can redirect to it,
the drill-down must serve every signed-in user the monolith serves, and carry
the items that only existed on the monolith: the WhatsApp opt-in card, the
Cookie Settings and Blocked Members links, the shared toggle component and the
#email-/#whatsapp-/#push-notifications anchors.
"""

from datetime import date
from html.parser import HTMLParser

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from crush_lu.models import CrushCoach, CrushProfile, EmailPreference
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

HOST = "crush.lu"
ACCOUNT = "/en/profile/edit/?section=account"
SUBS = ("", "&sub=settings", "&sub=notifications", "&sub=danger")


class _TagCollector(HTMLParser):
    """Collects ids, anchors, forms and inputs without regex tag matching."""

    def __init__(self):
        super().__init__()
        self.ids = set()
        self.hrefs = []
        self.forms = []
        self.inputs = []
        self.cookie_triggers = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a":
            self.hrefs.append(attrs.get("href") or "")
            if "data-cookie-settings" in attrs:
                self.cookie_triggers += 1
        if tag == "form":
            self.forms.append(attrs)
        if tag == "input":
            self.inputs.append(attrs)


def _parse(response):
    collector = _TagCollector()
    collector.feed(response.content.decode())
    return collector


def _make_user(email):
    user = User.objects.create_user(username=email, email=email, password="testpass123")
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    return user


def _make_profile(user, **overrides):
    defaults = dict(
        user=user,
        date_of_birth=date(1995, 1, 1),
        gender="M",
        location="Luxembourg",
        is_approved=True,
        verification_status="verified",
    )
    defaults.update(overrides)
    return CrushProfile.objects.create(**defaults)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class AccountDrillDownAccessTests(TestCase):
    """Every signed-in user who can open /account/settings/ can open the drill-down."""

    def setUp(self):
        cache.clear()
        self.users = {}
        self.users["no_profile"] = _make_user("noprofile@example.com")
        for state in ("pending", "incomplete", "rejected"):
            user = _make_user(f"{state}@example.com")
            _make_profile(user, is_approved=False, verification_status=state)
            self.users[state] = user
        coach = _make_user("coach@example.com")
        CrushCoach.objects.create(user=coach, is_active=True)
        self.users["coach_no_profile"] = coach
        approved = _make_user("approved@example.com")
        _make_profile(approved)
        self.users["approved"] = approved

    def test_every_logged_in_user_gets_every_account_page(self):
        failures = []
        for label, user in self.users.items():
            self.client.force_login(user)
            for sub in SUBS:
                response = self.client.get(ACCOUNT + sub, HTTP_HOST=HOST)
                if response.status_code != 200:
                    failures.append((label, sub, response.status_code))
        self.assertEqual(failures, [])

    def test_htmx_partials_render_without_a_profile(self):
        self.client.force_login(self.users["no_profile"])
        for sub in SUBS:
            response = self.client.get(
                ACCOUNT + sub, HTTP_HOST=HOST, HTTP_HX_REQUEST="true"
            )
            self.assertEqual(response.status_code, 200, sub)

    def test_other_sections_keep_the_verified_only_gate(self):
        self.client.force_login(self.users["pending"])
        response = self.client.get("/en/profile/edit/?section=photos", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.users["no_profile"])
        response = self.client.get("/en/profile/edit/?section=privacy", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 302)

    def test_back_link_skips_the_profile_editor_for_unapproved_users(self):
        self.client.force_login(self.users["pending"])
        hrefs = _parse(self.client.get(ACCOUNT, HTTP_HOST=HOST)).hrefs
        self.assertIn("/en/dashboard/", hrefs)
        self.client.force_login(self.users["approved"])
        hrefs = _parse(self.client.get(ACCOUNT, HTTP_HOST=HOST)).hrefs
        self.assertIn("/en/profile/edit/", hrefs)

    def test_rejected_profile_sees_its_status_on_the_settings_sub(self):
        self.client.force_login(self.users["rejected"])
        response = self.client.get(ACCOUNT + "&sub=settings", HTTP_HOST=HOST)
        self.assertContains(response, "Not Verified")
        self.assertIn("/en/profile/rejected/", _parse(response).hrefs)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DangerZoneParityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_privacy_card_has_cookie_settings_and_blocked_members(self):
        user = _make_user("member@example.com")
        _make_profile(user)
        self.client.force_login(user)
        # HTMX partial only: the full page's footer carries its own trigger.
        parsed = _parse(
            self.client.get(
                ACCOUNT + "&sub=danger", HTTP_HOST=HOST, HTTP_HX_REQUEST="true"
            )
        )
        self.assertEqual(parsed.cookie_triggers, 1)
        self.assertIn("/en/settings/blocked/", parsed.hrefs)
        self.assertIn("/en/account/take-a-break/", parsed.hrefs)
        self.assertIn("/en/account/delete-profile/", parsed.hrefs)

    def test_profileless_user_gets_only_the_actions_they_can_use(self):
        self.client.force_login(_make_user("noprofile@example.com"))
        parsed = _parse(self.client.get(ACCOUNT + "&sub=danger", HTTP_HOST=HOST))
        self.assertNotIn("/en/account/take-a-break/", parsed.hrefs)
        self.assertNotIn("/en/account/delete-profile/", parsed.hrefs)
        self.assertIn("/en/settings/blocked/", parsed.hrefs)
        self.assertTrue(any("gdpr" in href for href in parsed.hrefs))


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class NotificationsParityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_user("phone@example.com")
        self.profile = _make_profile(
            self.user, phone_number="+352621123456", phone_verified=True
        )
        self.client.force_login(self.user)

    def _get(self):
        return self.client.get(ACCOUNT + "&sub=notifications", HTTP_HOST=HOST)

    def test_monolith_hash_anchors_exist(self):
        ids = _parse(self._get()).ids
        for anchor in (
            "email-notifications",
            "whatsapp-notifications",
            "push-notifications",
        ):
            self.assertIn(anchor, ids)

    def test_whatsapp_card_posts_to_the_existing_endpoint(self):
        parsed = _parse(self._get())
        actions = [form.get("action") for form in parsed.forms]
        self.assertIn("/en/account/settings/whatsapp-preference/", actions)
        names = {field.get("name"): field for field in parsed.inputs}
        self.assertIn("whatsapp_opt_in", names)
        self.assertEqual(names["whatsapp_opt_in"].get("role"), "switch")
        self.assertEqual(names["return_to"].get("value"), "notifications")

    def test_whatsapp_card_asks_for_a_verified_phone_first(self):
        # save() refuses to un-verify a phone, so bypass it.
        CrushProfile.objects.filter(pk=self.profile.pk).update(phone_verified=False)
        response = self._get()
        self.assertContains(
            response,
            "A verified phone number is required to enable WhatsApp notifications.",
        )
        names = {field.get("name") for field in _parse(response).inputs}
        self.assertNotIn("whatsapp_opt_in", names)

    def test_switches_use_the_shared_toggle_component(self):
        response = self._get()
        self.assertNotContains(response, "peer-toggle")
        email_switches = [
            field
            for field in _parse(response).inputs
            if "email-pref-toggle" in (field.get("class") or "")
        ]
        self.assertEqual(len(email_switches), 5)
        self.assertTrue(all(f.get("role") == "switch" for f in email_switches))

    def test_whatsapp_post_from_drill_down_returns_there(self):
        response = self.client.post(
            "/en/account/settings/whatsapp-preference/",
            {"whatsapp_opt_in": "on", "return_to": "notifications"},
            HTTP_HOST=HOST,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "/en/profile/edit/?section=account&sub=notifications"
            "#whatsapp-notifications",
        )
        self.assertTrue(EmailPreference.objects.get(user=self.user).whatsapp_opt_in)

    def test_whatsapp_post_without_return_to_keeps_old_redirect(self):
        response = self.client.post(
            "/en/account/settings/whatsapp-preference/",
            {"return_to": "https://evil.example/"},
            HTTP_HOST=HOST,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/en/account/settings/")
