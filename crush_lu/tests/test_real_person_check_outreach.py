"""The text a coach sends a member for the Connect real-person check.

The body is a gettext msgid rendered in the member's language (not a
CrushSiteConfig template), and the ``sms:`` / ``wa.me`` links reuse the
phone formatting of the screening-call outreach.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import re
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import translation

from crush_lu import views_coach
from crush_lu.models import CrushProfile, EmailPreference
from crush_lu.services.real_person_check import claim_check
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
)
from crush_lu.tests.test_crush_connect import _grant_consent
from crush_lu.views_coach_real_person import (
    _dial_number,
    _outreach_context,
    real_person_outreach_body,
    whatsapp_link_allowed,
)

LIVE_FLAGS = {
    "ROOT_URLCONF": "azureproject.urls_crush",
    "CONNECT_REAL_PERSON_CHECK_ENABLED": True,
}


class OutreachBodyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.coach = make_coach("rpco_coach")
        self.member = make_member("rpco_member")

    def body(self, language, coach=None):
        CrushProfile.objects.filter(user=self.member).update(
            preferred_language=language
        )
        return real_person_outreach_body(coach or self.coach, profile_of(self.member))

    def test_english_body_names_the_member_and_the_coach(self):
        body = self.body("en")
        self.assertEqual(
            body,
            "Hi Rpco_Member, this is Rpco_Coach from Crush.lu. To confirm every "
            "Crush Connect member is a real person, could we do a quick 5-minute "
            "video call? It's optional and adds a 'verified by a coach' badge to "
            "your card.",
        )

    def test_the_language_follows_the_members_preferred_language(self):
        self.assertTrue(self.body("de").startswith("Hallo Rpco_Member, hier ist"))
        self.assertTrue(self.body("fr").startswith("Bonjour Rpco_Member, ici"))
        self.assertIn("Rpco_Coach", self.body("de"))
        self.assertIn("Rpco_Coach", self.body("fr"))

    def test_register_is_du_in_german_and_vous_in_french(self):
        self.assertIn("Hast du Lust", self.body("de"))
        self.assertIn("deiner Karte", self.body("de"))
        self.assertIn("votre carte", self.body("fr"))
        self.assertNotIn("ta carte", self.body("fr"))

    def test_without_video_the_text_proposes_a_call_and_names_no_video_badge(self):
        CrushProfile.objects.filter(user=self.member).update(preferred_language="en")
        profile = profile_of(self.member)
        body = real_person_outreach_body(self.coach, profile, video_available=False)
        self.assertEqual(
            body,
            "Hi Rpco_Member, this is Rpco_Coach from Crush.lu. To confirm every "
            "Crush Connect member is a real person, could we have a quick "
            "5-minute call? It's optional and adds a badge to your card.",
        )
        # Same default as before: video is what the text offers.
        self.assertEqual(
            real_person_outreach_body(self.coach, profile),
            real_person_outreach_body(self.coach, profile, video_available=True),
        )
        for language, opening, register in (
            ("de", "Hallo", "Hast du Lust auf einen kurzen Anruf"),
            ("fr", "Bonjour", "votre carte"),
        ):
            with self.subTest(language):
                CrushProfile.objects.filter(user=self.member).update(
                    preferred_language=language
                )
                text = real_person_outreach_body(
                    self.coach, profile_of(self.member), video_available=False
                )
                self.assertTrue(text.startswith(opening), text)
                self.assertIn(register, text)
                self.assertNotIn("ideo", text)

    def test_the_coachs_own_page_language_does_not_matter(self):
        with translation.override("fr"):
            self.assertTrue(self.body("de").startswith("Hallo"))
        with translation.override("de"):
            self.assertTrue(self.body("en").startswith("Hi "))

    def test_the_active_language_is_restored_afterwards(self):
        with translation.override("fr"):
            self.body("de")
            self.assertEqual(translation.get_language(), "fr")

    def test_an_unsupported_language_falls_back_to_english(self):
        self.assertTrue(self.body("nl").startswith("Hi "))

    def test_a_coach_without_a_first_name_is_still_named(self):
        self.coach.user.first_name = ""
        self.coach.user.save(update_fields=["first_name"])
        self.assertIn("this is a coach from Crush.lu", self.body("en", self.coach))
        self.assertIn("hier ist ein Coach von Crush.lu", self.body("de", self.coach))
        self.assertIn("ici un coach de Crush.lu", self.body("fr", self.coach))


class OutreachLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.coach = make_coach("rpcl_coach")
        self.member = make_member("rpcl_member")
        CrushProfile.objects.filter(user=self.member).update(
            phone_number="+352 621 123 456",
            phone_verified=True,
            preferred_language="de",
        )

    def context(self, blocked=False, **kwargs):
        return _outreach_context(
            self.coach, profile_of(self.member), blocked=blocked, **kwargs
        )

    def opt_in(self, **overrides):
        EmailPreference.objects.update_or_create(
            user=self.member, defaults={"whatsapp_opt_in": True, **overrides}
        )

    def test_links_carry_the_dialable_number_and_the_encoded_body(self):
        self.opt_in()
        links = self.context()
        self.assertEqual(links["tel_href"], "tel:+352621123456")
        self.assertTrue(links["sms_href"].startswith("sms:+352621123456?body="))
        body = unquote(links["sms_href"].split("?body=", 1)[1])
        self.assertTrue(body.startswith("Hallo Rpcl_Member"))
        wa = urlparse(links["whatsapp_url"])
        self.assertEqual((wa.netloc, wa.path), ("wa.me", "/352621123456"))
        self.assertEqual(parse_qs(wa.query)["text"][0], body)
        self.assertEqual(links["phone_display"], "+352 621 123 456")

    def test_whatsapp_needs_the_opt_in_and_sms_does_not(self):
        # Spec decision D11: the wa.me link only for members who opted in.
        # EmailPreference rows are created with the account, opted out.
        EmailPreference.objects.filter(user=self.member).update(whatsapp_opt_in=False)
        links = self.context()
        self.assertEqual(links["whatsapp_url"], "")
        self.assertTrue(links["sms_href"].startswith("sms:+352621123456?body="))
        self.assertEqual(links["tel_href"], "tel:+352621123456")
        self.assertEqual(links["phone_display"], "+352 621 123 456")
        self.opt_in()
        self.assertTrue(self.context()["whatsapp_url"].startswith("https://wa.me/"))

    def test_no_preference_row_at_all_is_not_an_opt_in(self):
        EmailPreference.objects.filter(user=self.member).delete()
        self.assertFalse(whatsapp_link_allowed(profile_of(self.member)))
        self.assertEqual(self.context()["whatsapp_url"], "")
        self.assertNotEqual(self.context()["sms_href"], "")

    def test_a_number_known_to_be_off_whatsapp_gets_no_link(self):
        self.opt_in()
        CrushProfile.objects.filter(user=self.member).update(not_on_whatsapp=True)
        self.assertFalse(whatsapp_link_allowed(profile_of(self.member)))
        links = self.context()
        self.assertEqual(links["whatsapp_url"], "")
        self.assertNotEqual(links["sms_href"], "")

    def test_unsubscribing_from_everything_withdraws_the_whatsapp_opt_in(self):
        self.opt_in(unsubscribed_all=True)
        self.assertFalse(whatsapp_link_allowed(profile_of(self.member)))
        self.assertEqual(self.context()["whatsapp_url"], "")

    def test_the_text_follows_video_availability_in_every_link(self):
        self.opt_in()
        CrushProfile.objects.filter(user=self.member).update(preferred_language="en")
        with_video = self.context()
        without_video = self.context(video_available=False)
        for key in ("sms_href", "whatsapp_url"):
            self.assertIn("video%20call", with_video[key])
            self.assertNotIn("ideo", unquote(without_video[key]))
        self.assertEqual(with_video["tel_href"], without_video["tel_href"])

    def test_no_links_for_an_unverified_or_missing_number(self):
        self.opt_in()
        CrushProfile.objects.filter(user=self.member).update(phone_verified=False)
        self.assertEqual(self.context()["tel_href"], "")
        CrushProfile.objects.filter(user=self.member).update(
            phone_verified=True, phone_number=""
        )
        links = self.context()
        self.assertEqual(
            (links["tel_href"], links["sms_href"], links["whatsapp_url"]), ("", "", "")
        )

    def test_no_links_while_a_guard_blocks(self):
        self.opt_in()
        links = self.context(blocked=True)
        self.assertEqual(
            (
                links["tel_href"],
                links["sms_href"],
                links["whatsapp_url"],
                links["phone_display"],
            ),
            ("", "", "", ""),
        )

    def test_dial_number_keeps_a_plus_and_the_digits_only(self):
        self.assertEqual(_dial_number("+352 (621) 123-456"), "+352621123456")
        self.assertEqual(_dial_number("0621 123 456"), "0621123456")
        self.assertEqual(_dial_number("  "), "")


class SharedFormattingTests(TestCase):
    """``_build_outreach_context`` keeps its output after the extraction."""

    def test_build_outreach_context_is_unchanged(self):
        coach = make_coach("rpcs_coach")
        member = make_member("rpcs_member")
        CrushProfile.objects.filter(user=member).update(
            phone_number="+352 621 765 432", phone_verified=True
        )
        profile = profile_of(member)
        context = views_coach._build_outreach_context(coach, profile)
        from crush_lu.models.site_config import CrushSiteConfig

        template = CrushSiteConfig.get_config().sms_template_en
        body = template.format(first_name=member.first_name, coach_name="Rpcs_Coach")
        self.assertEqual(
            context, views_coach._outreach_links(profile.phone_number, body)
        )
        self.assertEqual(
            context["whatsapp_url"],
            f"https://wa.me/352621765432?text={context['sms_template_encoded']}",
        )

    def test_unverified_phone_still_gives_empty_strings(self):
        coach = make_coach("rpcs_coach2")
        member = make_member("rpcs_member2")
        profile = profile_of(member)
        self.assertEqual(
            views_coach._build_outreach_context(coach, profile),
            {"sms_template_encoded": "", "whatsapp_url": ""},
        )

    def test_outreach_links_without_digits_has_no_whatsapp_url(self):
        links = views_coach._outreach_links("n/a", "hello there")
        self.assertEqual(links["sms_template_encoded"], "hello%20there")
        self.assertEqual(links["whatsapp_url"], "")


@override_settings(**LIVE_FLAGS)
class OutreachOnThePageTests(TestCase):
    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.coach = make_coach("rpcp_coach")
        _grant_consent(self.coach.user)
        self.member = make_member("rpcp_member")
        CrushProfile.objects.filter(user=self.member).update(
            phone_number="+352 621 555 000", phone_verified=True
        )
        self.client.force_login(self.coach.user)

    def sms_body(self):
        check = claim_check(self.coach, profile_of(self.member).pk)
        html = self.client.get(
            f"/en/coach/real-person-checks/{check.pk}/"
        ).content.decode()
        href = re.search(r'href="sms:[^"?]+\?body=([^"]+)"', html).group(1)
        return unquote(href)

    def test_the_page_link_is_in_the_members_language_not_the_coachs(self):
        CrushProfile.objects.filter(user=self.member).update(preferred_language="fr")
        # The coach reads the page in English; the text is for a French reader.
        self.assertTrue(self.sms_body().startswith("Bonjour Rpcp_Member"))

    def test_a_german_member_gets_the_german_text(self):
        CrushProfile.objects.filter(user=self.member).update(preferred_language="de")
        self.assertTrue(self.sms_body().startswith("Hallo Rpcp_Member"))

    def test_an_english_member_gets_the_english_text(self):
        self.assertTrue(self.sms_body().startswith("Hi Rpcp_Member"))
        self.assertNotIn("{", self.sms_body())

    def test_the_site_config_sms_templates_are_not_used(self):
        from crush_lu.models.site_config import CrushSiteConfig

        config = CrushSiteConfig.get_config()
        config.sms_template_en = "SITE-CONFIG-TEMPLATE {first_name}"
        config.save()
        self.assertNotIn("SITE-CONFIG-TEMPLATE", self.sms_body())

    def test_the_coach_namespace_helper_accepts_a_bare_coach(self):
        # real_person_outreach_body only needs coach.user.first_name.
        coach = SimpleNamespace(user=SimpleNamespace(first_name="Zoe"))
        body = real_person_outreach_body(coach, profile_of(self.member))
        self.assertIn("this is Zoe from Crush.lu", body)
