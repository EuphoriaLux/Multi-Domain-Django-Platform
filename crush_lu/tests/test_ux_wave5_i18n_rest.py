"""UX Wave 5 . WP15: remaining i18n fixes.

Plural vote/point counts, the single-sentence consent line, FR tone and
"Connect Week" / "Mix" terminology, and the last inline ``confirm()`` calls.
"""

import re
from pathlib import Path
from types import SimpleNamespace

import polib
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import translation

from crush_lu.models import UserDataConsent
from crush_lu.models.event_polls import EventPollVote
from crush_lu.tests.test_ux_wave4_polls_timeline import HOST, make_member, make_poll

CRUSH_LU = Path(__file__).resolve().parent.parent
TEMPLATES = CRUSH_LU / "templates" / "crush_lu"


def _fr_entries():
    po = polib.pofile(str(CRUSH_LU / "locale/fr/LC_MESSAGES/django.po"))
    return {e.msgid: e for e in po if "fuzzy" not in e.flags and not e.obsolete}


class PollVoteCountPluralTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_member()
        self.client.force_login(self.user)
        self.poll = make_poll()

    def _vote(self, user):
        option = self.poll.options.first()
        EventPollVote.objects.create(poll=self.poll, option=option, user=user)

    def test_list_singular_and_plural_in_english_and_german(self):
        self._vote(self.user)
        single_en = self.client.get("/en/polls/", HTTP_HOST=HOST).content.decode()
        single_de = self.client.get("/de/polls/", HTTP_HOST=HOST).content.decode()
        self.assertIn("1 vote<", single_en.replace("\n", "").replace("  ", ""))
        self.assertNotIn("1 votes", single_en)
        self.assertIn("1 Stimme<", single_de.replace("\n", "").replace("  ", ""))

        self._vote(make_member("second@example.com"))
        plural_en = self.client.get("/en/polls/", HTTP_HOST=HOST).content.decode()
        plural_de = self.client.get("/de/polls/", HTTP_HOST=HOST).content.decode()
        self.assertIn("2 votes", plural_en)
        self.assertIn("2 Stimmen", plural_de)

    def test_detail_keeps_the_hook_and_pluralises(self):
        self._vote(self.user)
        page = self.client.get(f"/en/polls/{self.poll.id}/", HTTP_HOST=HOST)
        self.assertContains(
            page, "<span data-poll-total-votes>1</span> vote<", html=False
        )
        fr = self.client.get(f"/fr/polls/{self.poll.id}/", HTTP_HOST=HOST)
        self.assertContains(fr, "<span data-poll-total-votes>1</span> vote<")
        self.assertNotContains(fr, "<span data-poll-total-votes>1</span> votes")


class PointsPluralTests(TestCase):
    def _render(self, lang, points):
        with translation.override(lang):
            return render_to_string(
                "crush_lu/journey/partials/already_completed.html",
                {
                    "existing_attempt": SimpleNamespace(points_earned=points),
                    "chapter": SimpleNamespace(chapter_number=1),
                    "journey_query": "",
                },
            )

    def test_one_point_is_singular(self):
        self.assertIn("<strong>1 point</strong>", self._render("en", 1))
        self.assertIn("<strong>1 Punkt</strong>", self._render("de", 1))
        self.assertIn("<strong>1 point</strong>", self._render("fr", 1))

    def test_several_points_are_plural(self):
        self.assertIn("<strong>5 points</strong>", self._render("en", 5))
        self.assertIn("<strong>5 Punkte</strong>", self._render("de", 5))
        self.assertIn("<strong>5 points</strong>", self._render("fr", 5))

    def test_french_zero_is_singular(self):
        self.assertIn("<strong>0 point</strong>", self._render("fr", 0))


class ConsentConfirmSentenceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="old@example.com", email="old@example.com", password="x-pass-123"
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": False}
        )
        self.client.force_login(self.user)

    def test_german_sentence_reads_naturally_with_links_inside(self):
        page = self.client.get("/de/consent/confirm/", HTTP_HOST=HOST)
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertRegex(
            html, r"Ich stimme den\s+<a [^>]*>Nutzungsbedingungen</a> und der\s*<a"
        )
        self.assertRegex(html, r"Datenschutzerkl\S+rung</a> zu")


class FrenchCatalogueTests(TestCase):
    def test_no_semaine_connect_left_in_french(self):
        offenders = [
            e.msgid
            for e in _fr_entries().values()
            if re.search(r"[Ss]emaine Connect", e.msgstr)
        ]
        self.assertEqual(offenders, [])

    def test_informal_strings_are_vous(self):
        fr = _fr_entries()
        self.assertEqual(
            fr["Your Connect readiness"].msgstr, "Votre préparation Connect"
        )
        self.assertEqual(fr["or verify by SMS"].msgstr, "ou vérifiez par SMS")
        self.assertEqual(
            fr["Add it to Apple Wallet or Google Wallet"].msgstr,
            "Ajoutez-la à Apple Wallet ou Google Wallet",
        )
        self.assertEqual(
            fr["Come to an event and meet people in person."].msgstr,
            "Venez à un événement et rencontrez des gens en personne.",
        )
        curiosity = next(
            e for m, e in fr.items() if m.startswith("Curiosity is a great starting")
        )
        self.assertIn("Venez à un événement", curiosity.msgstr)
        self.assertIn("vous faire vérifier", curiosity.msgstr)
        self.assertNotIn("Viens", curiosity.msgstr)

    def test_mix_is_named_not_translated_as_a_game(self):
        fr = _fr_entries()
        self.assertEqual(fr["Join the Mix"].msgstr, "Rejoindre le Mix")
        leftovers = [
            e.msgid
            for e in fr.values()
            if "mix" in e.msgid.lower() and "de la partie" in e.msgstr
        ]
        self.assertEqual(leftovers, [])

    def test_compiled_catalogue_matches(self):
        with translation.override("fr"):
            self.assertEqual(translation.gettext("Join the Mix"), "Rejoindre le Mix")
            self.assertEqual(
                translation.gettext("or verify by SMS"), "ou vérifiez par SMS"
            )


class InlineConfirmRemovedTests(TestCase):
    def test_no_window_confirm_left_in_the_two_templates(self):
        for name in (
            "partials/edit_account_settings.html",
            "coach_invitation_dashboard.html",
        ):
            source = (TEMPLATES / name).read_text(encoding="utf-8")
            self.assertNotIn("confirm(", source, name)
            self.assertIn("data-confirm=", source, name)

    def test_disconnect_and_approve_use_the_confirm_sheet_attributes(self):
        settings_src = (TEMPLATES / "partials/edit_account_settings.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-confirm="{{ disconnect_question }}"', settings_src)
        invite_src = (TEMPLATES / "coach_invitation_dashboard.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('data-confirm-style="neutral"', invite_src)
