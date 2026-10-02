"""UX Wave 6 · WP4 trust copy.

1. "Verified instantly with LuxID" is only promised when the LuxID button is
   offered (About, profile "About Crush.lu" section, social-signup completion),
   with the same ``luxid_available`` gate as the footer.
2. Decision C: the "No credit card required • Free to join • Cancel anytime"
   strips show the existing "Join free, upgrade when you want your own coach.
   Cancel anytime." msgid instead.
3. Decision H: member-facing FR screening and onboarding copy uses "vous".
4. The event recap's fuzzy, wrong DE/FR entries are translated.
5. DE referral tiers no longer read like a paid "Mitgliedschaft".

Literal paths with HTTP_HOST crush.lu.
"""

import html
import re
from unittest import mock

import polib
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from crush_lu.tests.test_ux_wave3_verify_email import (
    _FakeSocialSignupForm,
    _render_crush_page,
)

HOST = "crush.lu"
LIST_APPS = "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.list_apps"
LUXID_ON = [mock.Mock(settings={})]

INSTANT_LUXID = "verified instantly with LuxID"
ABOUT_NO_LUXID = (
    "Members are verified in person at an event. Every event is thoughtfully "
    "organized."
)
FOOTER_NO_LUXID = "Your privacy matters. Members are verified in person at an event."
JOIN_FREE = "Join free, upgrade when you want your own coach. Cancel anytime."
OLD_STRIP = ("No credit card required", "Free to join")


def _po(lang):
    return polib.pofile(
        f"{settings.BASE_DIR}/crush_lu/locale/{lang}/LC_MESSAGES/django.po"
    )


class LuxidClaimGatingTests(TestCase):
    def setUp(self):
        cache.clear()

    def _about(self, apps, lang="en"):
        cache.clear()
        with mock.patch(LIST_APPS, return_value=apps):
            response = self.client.get(f"/{lang}/about/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        return html.unescape(response.content.decode())

    def test_about_promises_luxid_only_when_offered(self):
        offered = self._about(LUXID_ON)
        absent = self._about([])

        self.assertIn(
            "Members are verified instantly with LuxID or in person at an event. "
            "Every event",
            offered,
        )
        self.assertNotIn(INSTANT_LUXID, absent)
        self.assertIn(ABOUT_NO_LUXID, absent)
        # The "Human Verification" list must not promise LuxID either.
        self.assertIn("Or instant verification with LuxID", offered)
        absent_list = absent[absent.index("about-feature-list") :]
        absent_list = absent_list[: absent_list.index("</ul>")]
        self.assertIn("In-person verification by a coach", absent_list)
        self.assertNotIn("LuxID", absent_list)

    def test_about_no_luxid_variant_is_translated(self):
        de = self._about([], "de")
        fr = self._about([], "fr")

        self.assertIn("Mitglieder werden persönlich bei einer Veranstaltung", de)
        self.assertNotIn("sofort mit LuxID", de)
        self.assertIn("Les membres sont vérifiés en personne lors d'un", fr)
        self.assertNotIn("instantanément avec LuxID", fr)

    def _edit_about(self, apps):
        with mock.patch(LIST_APPS, return_value=apps):
            return html.unescape(
                _render_crush_page("crush_lu/partials/edit_about_crushlu.html", {})
            )

    def test_profile_about_section_reuses_footer_gate(self):
        offered = self._edit_about(LUXID_ON)
        absent = self._edit_about([])

        self.assertIn(INSTANT_LUXID, offered)
        self.assertNotIn(INSTANT_LUXID, absent)
        self.assertIn(FOOTER_NO_LUXID, absent)

    def _signup(self, apps):
        account = mock.Mock()
        account.provider = "google"
        form = _FakeSocialSignupForm(initial={"email": "member@example.com"})
        with mock.patch(LIST_APPS, return_value=apps):
            return html.unescape(
                _render_crush_page(
                    "socialaccount/signup_crush.html",
                    {"form": form, "account": account},
                )
            )

    def test_social_signup_next_steps_gate_luxid(self):
        offered = self._signup(LUXID_ON)
        absent = self._signup([])

        self.assertIn("Get verified instantly with LuxID — or in person", offered)
        self.assertNotIn("instantly with LuxID", absent)
        self.assertIn("Get verified in person at an event", absent)

    def test_signup_no_luxid_variant_is_translated(self):
        po_de, po_fr = _po("de"), _po("fr")
        for po in (po_de, po_fr):
            entry = po.find("Get verified in person at an event")
            self.assertIsNotNone(entry)
            self.assertTrue(entry.msgstr)
            self.assertNotIn("fuzzy", entry.flags)
            self.assertNotIn("LuxID", entry.msgstr)

    def test_signup_next_steps_fr_uses_vous(self):
        entry = _po("fr").find("Start attending events and meeting people!")
        self.assertTrue(entry.msgstr.startswith("Commencez "), entry.msgstr)


class CtaMicrocopyTests(TestCase):
    """Decision C: reuse the existing Join-free msgid, no new copy."""

    def setUp(self):
        cache.clear()

    def test_about_cta_uses_join_free_copy(self):
        body = html.unescape(
            self.client.get("/en/about/", HTTP_HOST=HOST).content.decode()
        )
        cta = body[body.index("about-cta-section") :]

        self.assertIn(JOIN_FREE, cta)
        for old in OLD_STRIP:
            self.assertNotIn(old, body)

    def test_about_cta_is_translated(self):
        de = html.unescape(
            self.client.get("/de/about/", HTTP_HOST=HOST).content.decode()
        )
        fr = html.unescape(
            self.client.get("/fr/about/", HTTP_HOST=HOST).content.decode()
        )
        self.assertIn("Jederzeit kündbar.", de[de.index("about-cta-section") :])
        self.assertIn("Résiliable à tout moment.", fr[fr.index("about-cta-section") :])
        # House style is lowercase du/dein.
        self.assertIn("wenn du deinen eigenen Coach", de)

    def test_default_cta_section_uses_join_free_copy(self):
        out = html.unescape(
            _render_crush_page("crush_lu/includes/cta_section.html", {})
        )

        self.assertIn(JOIN_FREE, out)
        for old in OLD_STRIP:
            self.assertNotIn(old, out)


# Decision H: member-facing FR screening + onboarding sources.
VOUS_SOURCES = (
    "templates/crush_lu/book_screening.html",
    "templates/crush_lu/pre_screening.html",
    "templates/crush_lu/pre_screening/",
    "templates/crush_lu/onboarding/coach_intro.html",
    "templates/crush_lu/emails/pre_screening_invite.",
    "templates/crush_lu/emails/screening_fallback_offered.",
    "templates/crush_lu/emails/screening_confirmed.",
    "pre_screening_schema.py",
    "pre_screening_notifications.py",
    "views_pre_screening.py",
)
# Coach-facing strings that share these sources keep their register.
COACH_FACING = {
    "User thinks Crush.lu is a swipe app — expect to clarify during call.",
    "User prefers online-only dating. Discuss whether in-person events fit.",
    "User would prefer no intermediary. Explain the Coach's role gently.",
    "Free-text answer is very short or repetitive — ask them to elaborate.",
}
TU_FORMS = re.compile(
    r"\b(tu|ton|ta|tes|toi|te)\b|\bt['’]"
    r"|\b(choisis|choisis-en|réponds|prends|appuie|peux-tu|rencontre-les)\b"
    r"|\b(réserve|aide|confirme) (ton|ta|tes)\b|\brencontre l['’]",
    re.IGNORECASE,
)


class FrenchVousRegisterTests(SimpleTestCase):
    def test_screening_and_onboarding_copy_uses_vous(self):
        offenders = []
        checked = 0
        for entry in _po("fr"):
            if entry.obsolete or entry.msgid in COACH_FACING:
                continue
            files = [path for path, _line in entry.occurrences]
            if not any(src in path for path in files for src in VOUS_SOURCES):
                continue
            checked += 1
            texts = [entry.msgstr, *entry.msgstr_plural.values()]
            if any(TU_FORMS.search(text) for text in texts):
                offenders.append((entry.msgid, entry.msgstr))

        self.assertGreater(checked, 100)
        self.assertEqual(offenders, [])

    def test_pre_screening_display_stays_coach_register(self):
        entry = _po("fr").find(
            "You can send a quick SMS reminder asking them to fill it out "
            "before you call."
        )
        self.assertTrue(entry.msgstr.startswith("Tu peux"))

    def test_pre_screening_invite_greeting_is_formal(self):
        # The invite body uses vous, so its greeting must not be "Salut".
        entry = _po("fr").find("Hi %(name)s,")
        self.assertEqual(entry.msgstr, "Bonjour %(name)s,")


RECAP = {
    "Confirm you met this participant": (
        "Confirmez que vous avez rencontré cette personne",
        "Bestätige, dass du diese Person getroffen hast",
    ),
    "Who did you meet?": ("Qui avez-vous rencontré ?", "Wen hast du getroffen?"),
    "You already confirmed you met this person.": (
        "Vous avez déjà confirmé avoir rencontré cette personne.",
        "Du hast bereits bestätigt, dass du diese Person getroffen hast.",
    ),
    "left to confirm": ("restant pour confirmer", "verbleibend zum Bestätigen"),
    "Did you meet in person?": (
        "Vous êtes-vous rencontrés en personne ?",
        "Habt ihr euch persönlich getroffen?",
    ),
    "See People I've Met": ("Mes rencontres", "Meine Begegnungen"),
}


class RecapTranslationTests(SimpleTestCase):
    def test_recap_entries_translated_and_not_fuzzy(self):
        fr, de = _po("fr"), _po("de")
        wrong = []
        for msgid, (fr_text, de_text) in RECAP.items():
            for po, expected in ((fr, fr_text), (de, de_text)):
                entry = po.find(msgid)
                stale = entry.previous_msgid is not None
                if "fuzzy" in entry.flags or stale or entry.msgstr != expected:
                    wrong.append((msgid, entry.msgstr, entry.flags))
        self.assertEqual(wrong, [])


REFERRAL_TIER_MSGIDS = (
    "Membership tier based on referral activity",
    "Referral points and membership tier (basic, bronze, silver, gold)",
    "Points contribute to membership tiers: Basic, Bronze, Silver, Gold",
    "Referral points and membership tier are forfeited",
)


class GermanReferralTierTests(SimpleTestCase):
    def test_referral_tiers_use_stufe_not_membership(self):
        de = _po("de")
        # The term the same Terms section already uses for these tiers.
        self.assertEqual(
            de.find("Referral points and tiers have no monetary value").msgstr,
            "Empfehlungspunkte und Stufen haben keinen Geldwert",
        )
        msgstrs = [de.find(msgid).msgstr for msgid in REFERRAL_TIER_MSGIDS]
        self.assertTrue(all("stufe" in text.lower() for text in msgstrs), msgstrs)
        self.assertFalse(any("Mitglied" in text for text in msgstrs), msgstrs)
        # Name the referral tier explicitly where a bare "Stufe" could be
        # confused with the privacy policy's consent tiers.
        for msgid in REFERRAL_TIER_MSGIDS[:2]:
            self.assertIn("Empfehlungsstufe", de.find(msgid).msgstr)
