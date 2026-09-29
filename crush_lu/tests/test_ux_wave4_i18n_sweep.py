"""UX Wave 4 · WP15 — i18n sweep (findings 2-12, 7-10, 8-14, 3-12).

Renders the touched screens in DE and FR and checks the corrected copy:
the signup consent sentence is one translatable sentence, "optional" reads
idiomatically, phone-verification JSON errors, signup errors and the social
auth-error title are translated, the journey "already completed" line,
chapter duration and advent month are translated, the welcome road-ahead
estimate is the sum of the step minutes and a chapter whose only step
repeats its name has no subtitle. Also guards the .po files against
duplicate msgids and checks the emoji rule of ``check_translations``.
"""

import collections
import io
import json
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import polib
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import translation

from crush_lu import onboarding
from crush_lu.forms import CrushSignupForm
from crush_lu.management.commands.check_translations import (
    Command as CheckTranslations,
)
from crush_lu.management.commands.check_translations import added_emoji
from crush_lu.models import (
    ChallengeAttempt,
    ChapterProgress,
    CrushProfile,
    UserDataConsent,
)
from crush_lu.tests.test_journey_api_scoping import _make_player
from crush_lu.tests.test_ux_wave4_advent import DEC_5, NOW, make_advent_user

User = get_user_model()
HOST = "crush.lu"
LOCALE = Path(settings.BASE_DIR) / "crush_lu" / "locale"


class _Text(HTMLParser):
    """Visible text of a page (script/style skipped), whitespace-collapsed,
    plus the text of elements carrying every class in ``css_class``."""

    SKIP = {"script", "style", "title"}

    def __init__(self, css_class=None):
        super().__init__()
        self.css_class = css_class
        self.parts = []
        self.marked = []
        self._skip = 0
        self._stack = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        classes = set((dict(attrs).get("class") or "").split())
        is_marked = self.css_class is not None and set(self.css_class) <= classes
        if tag in ("br", "img", "input", "meta", "link", "hr", "source"):
            return
        self._stack.append((tag, is_marked))
        if is_marked:
            self.marked.append("")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        while self._stack:
            open_tag, _marked = self._stack.pop()
            if open_tag == tag:
                break

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if any(m for _t, m in self._stack):
            self.marked[-1] += data

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


def page_text(html, css_class=None):
    parser = _Text(css_class)
    parser.feed(html)
    parser.close()
    return parser


class SignupScreenTests(TestCase):
    def setUp(self):
        cache.clear()

    def get_text(self, lang):
        response = self.client.get(f"/{lang}/signup/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        return page_text(response.content.decode()).text

    def test_german_consent_is_one_grammatical_sentence(self):
        text = self.get_text("de")
        self.assertIn(
            "Ich stimme den Nutzungsbedingungen und der Datenschutzerklärung zu",
            text,
        )
        self.assertIn("(optional)", text)
        self.assertNotIn("Fakultativ", text)

    def test_french_consent_and_optional(self):
        text = self.get_text("fr")
        self.assertIn(
            "J'accepte les Conditions d'utilisation et la Politique de "
            "confidentialité",
            text,
        )
        self.assertIn("(facultatif)", text)
        self.assertNotIn("Optionnel", text)

    def test_consent_links_keep_their_targets(self):
        response = self.client.get("/de/signup/", HTTP_HOST=HOST)
        self.assertContains(response, 'href="/de/terms-of-service/" target="_blank"')
        self.assertContains(response, 'href="/de/privacy-policy/" target="_blank"')
        # The link classes are a placeholder, not part of the msgid.
        self.assertContains(
            response, 'target="_blank" class="text-crush-purple hover:underline"', 2
        )

    @patch.object(CrushSignupForm, "is_valid", return_value=True)
    @patch.object(
        CrushSignupForm, "save", side_effect=Exception("UNIQUE constraint failed")
    )
    def test_duplicate_email_error_is_translated(self, _save, _valid):
        response = self.client.post("/de/signup/", {}, HTTP_HOST=HOST)
        self.assertContains(
            response, "Es gibt bereits ein Konto mit dieser E-Mail-Adresse."
        )

    @patch.object(CrushSignupForm, "is_valid", return_value=True)
    @patch.object(CrushSignupForm, "save", side_effect=Exception("boom"))
    def test_generic_signup_error_is_translated(self, _save, _valid):
        response = self.client.post("/fr/signup/", {}, HTTP_HOST=HOST)
        self.assertContains(
            response,
            "Une erreur s&#x27;est produite lors de la création de votre compte.",
        )


class PhoneVerificationErrorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="phone-i18n@example.com",
            email="phone-i18n@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

    def post(self, url, body, lang):
        return self.client.post(
            url,
            body,
            content_type="application/json",
            HTTP_HOST=HOST,
            HTTP_ACCEPT_LANGUAGE=lang,
        )

    def test_check_available_errors_are_translated(self):
        bad_json = self.post("/api/phone/check-available/", "{", "de")
        self.assertEqual(bad_json.status_code, 400)
        self.assertEqual(bad_json.json()["error"], "Ungültige Anfrage")

        missing = self.post(
            "/api/phone/check-available/", json.dumps({"phone_number": ""}), "fr"
        )
        self.assertEqual(missing.json()["error"], "Le numéro de téléphone est requis")

    def test_mark_verified_errors_are_translated(self):
        bad_json = self.post("/api/phone/mark-verified/", "{", "de")
        self.assertEqual(bad_json.json()["error"], "Ungültiges JSON-Format")

        no_token = self.post("/api/phone/mark-verified/", json.dumps({}), "fr")
        self.assertEqual(no_token.json()["error"], "idToken est requis")


class SocialAuthErrorTitleTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_title_is_translated(self):
        # Same setup as test_ux_wave3_verify_email._render_crush_page.
        request = RequestFactory().get("/", HTTP_HOST=HOST)
        request.user = AnonymousUser()
        request.session = SessionStore()
        with translation.override("de"), override_settings(
            ROOT_URLCONF="azureproject.urls_crush"
        ):
            html = render_to_string(
                "socialaccount/authentication_error_crush.html", request=request
            )
        self.assertIn("<title>Drittanbieter-Anmeldefehler - Crush.lu", html)
        self.assertNotIn("Login Error - Crush.lu", html)


class JourneyCopyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.player = _make_player("Lea", "Well done", 0)
        self.client.force_login(self.player.user)

    def test_already_solved_riddle_line_is_one_translated_sentence(self):
        chapter_progress = ChapterProgress.objects.create(
            journey_progress=self.player.progress, chapter=self.player.chapter
        )
        ChallengeAttempt.objects.create(
            chapter_progress=chapter_progress,
            challenge=self.player.challenge,
            user_answer="4",
            is_correct=True,
            points_earned=100,
        )
        url = f"/journey/chapter/1/challenge/{self.player.challenge.id}/"
        expected = {
            "de": "Du hast dieses Rätsel bereits gelöst und "
            "<strong>100 Punkte</strong> verdient.",
            "fr": "Vous avez déjà résolu cette énigme et gagné "
            "<strong>100 points</strong>.",
            "en": "You already solved this riddle and earned "
            "<strong>100 points</strong>.",
        }
        for lang, sentence in expected.items():
            response = self.client.get(f"/{lang}{url}", HTTP_HOST=HOST)
            self.assertContains(response, sentence)

    def test_every_challenge_type_says_one_point_in_the_singular(self):
        chapter_progress = ChapterProgress.objects.create(
            journey_progress=self.player.progress, chapter=self.player.chapter
        )
        ChallengeAttempt.objects.create(
            chapter_progress=chapter_progress,
            challenge=self.player.challenge,
            user_answer="4",
            is_correct=True,
            points_earned=1,
        )
        url = f"/journey/chapter/1/challenge/{self.player.challenge.id}/"
        expected = {
            "multiple_choice": {
                "de": "Du hast diese Frage bereits beantwortet und "
                "<strong>1 Punkt</strong> gesammelt.",
                "fr": "Vous avez déjà répondu à cette question et gagné "
                "<strong>1 point</strong>.",
            },
            "riddle": {
                "de": "Du hast dieses Rätsel bereits gelöst und "
                "<strong>1 Punkt</strong> verdient.",
                "fr": "Vous avez déjà résolu cette énigme et gagné "
                "<strong>1 point</strong>.",
            },
            "timeline_sort": {
                "de": "Du hast diese Zeitleiste bereits richtig sortiert und "
                "<strong>1 Punkt</strong> erhalten.",
                "fr": "Vous avez déjà trié cette chronologie correctement et "
                "gagné <strong>1 point</strong>.",
            },
            "word_scramble": {
                "de": "Du hast dieses Wort bereits entschlüsselt und "
                "<strong>1 Punkt</strong> verdient.",
                "fr": "Vous avez déjà déchiffré ce mot et gagné "
                "<strong>1 point</strong>.",
            },
            "would_you_rather": {
                "de": "Du hast deine Wahl bereits geteilt und "
                "<strong>1 Punkt</strong> gesammelt.",
                "fr": "Vous avez déjà partagé votre choix et gagné "
                "<strong>1 point</strong>.",
            },
        }
        rendered = {}
        for challenge_type, sentences in expected.items():
            self.player.challenge.challenge_type = challenge_type
            self.player.challenge.save(update_fields=["challenge_type"])
            for lang, sentence in sentences.items():
                response = self.client.get(f"/{lang}{url}", HTTP_HOST=HOST)
                self.assertEqual(response.status_code, 200, challenge_type)
                rendered[(challenge_type, lang)] = sentence in response.content.decode()
        self.assertEqual([k for k, ok in rendered.items() if not ok], [])

    def test_chapter_duration_is_translated(self):
        response = self.client.get("/de/journey/wonderland/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertIn("~10 Min.", page_text(response.content.decode()).text)


class AdventSubtitleTests(TestCase):
    def setUp(self):
        cache.clear()
        make_advent_user(self)

    def test_month_is_translated(self):
        for lang, month in (("de", "Dezember 2024"), ("fr", "Décembre 2024")):
            with patch(NOW, return_value=DEC_5):
                response = self.client.get(f"/{lang}/advent/", HTTP_HOST=HOST)
            self.assertEqual(response.status_code, 200)
            text = page_text(response.content.decode()).text
            self.assertIn(month, text)
            self.assertNotIn("December 2024", text)


class WelcomeRoadAheadTests(TestCase):
    SUBTITLE = ("truncate", "text-[10.5px]")

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="welcome-i18n@example.com",
            email="welcome-i18n@example.com",
            password="testpass123",
            first_name="Alex",
        )
        CrushProfile.objects.create(user=self.user, date_of_birth=date(1995, 1, 1))
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        self.client.force_login(self.user)
        self.total = sum(s.min_duration for s in onboarding.JOURNEY_STEPS)

    def get(self, lang):
        response = self.client.get(f"/{lang}/welcome/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        return page_text(response.content.decode(), css_class=self.SUBTITLE)

    def test_total_is_the_sum_of_the_steps(self):
        self.assertEqual(self.total, 12)
        en = self.get("en").text
        self.assertIn(f"The road ahead · ≈ {self.total} min to get started", en)
        self.assertNotIn("≈ 15 min", en)
        de = self.get("de").text
        self.assertIn(f"Dein Weg · ca. {self.total} Min. bis zum Start", de)

    def test_subtitle_hidden_when_it_repeats_the_chapter_name(self):
        subtitles = [" ".join(s.split()) for s in self.get("en").marked]
        self.assertEqual(
            subtitles, ["Welcome · Verify number", "Meet the Coaches · Build profile"]
        )
        self.assertNotIn("Get verified", subtitles)


class TranslationSettingsFrenchTests(SimpleTestCase):
    """Catalogue-level checks for strings whose screens need heavy fixtures."""

    def setUp(self):
        cache.clear()

    def test_french_fixes(self):
        expected = {
            "Receive notifications via WhatsApp on:": (
                "Recevez des notifications via WhatsApp au :"
            ),
            "Notifications about new messages from connections": (
                "Notifications des nouveaux messages de vos connexions"
            ),
            "Install Crush.lu App": "Installer l'app Crush.lu",
            "Permanently Delete My Account": "Supprimer définitivement mon compte",
            "Declare My Crush!": "Déclarer mon coup de cœur !",
            "Email Preferences": "Préférences e-mail",
        }
        with translation.override("fr"):
            for msgid, msgstr in expected.items():
                self.assertEqual(translation.gettext(msgid), msgstr)

    def test_german_du_and_declare(self):
        expected = {
            "Declare My Crush!": "Meinen Crush erklären!",
            "Confirming your payment — please don't close this page.": (
                "Deine Zahlung wird bestätigt — bitte schließ diese Seite nicht."
            ),
            "The payment could not be completed. Please check your card "
            "details and try again.": (
                "Die Zahlung konnte nicht abgeschlossen werden. Bitte überprüfe "
                "deine Kartendaten und versuche es erneut."
            ),
        }
        with translation.override("de"):
            for msgid, msgstr in expected.items():
                self.assertEqual(translation.gettext(msgid), msgstr)


class PoFileTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_no_duplicate_msgids(self):
        for lang in ("en", "de", "fr"):
            po = polib.pofile(str(LOCALE / lang / "LC_MESSAGES" / "django.po"))
            counts = collections.Counter(
                (e.msgctxt, e.msgid) for e in po if not e.obsolete
            )
            duplicates = [key for key, n in counts.items() if n > 1]
            self.assertEqual(duplicates, [], lang)

    def test_french_uses_e_mail(self):
        po = polib.pofile(str(LOCALE / "fr" / "LC_MESSAGES" / "django.po"))
        bare = [
            e.msgid
            for e in po
            if not e.obsolete
            and any(
                word.strip(".,:;!?()'\"").lower() in ("email", "emails")
                for word in e.msgstr.replace("l'", " ").replace("d'", " ").split()
            )
        ]
        self.assertEqual(bare, [])


class CheckTranslationsEmojiRuleTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def entry(self, msgid, msgstr, **kwargs):
        return polib.POEntry(msgid=msgid, msgstr=msgstr, **kwargs)

    def test_rule_flags_only_emoji_missing_from_msgid(self):
        self.assertEqual(
            added_emoji(self.entry("Install Crush.lu App", "📲 Installer l'app")),
            {"📲"},
        )
        self.assertEqual(added_emoji(self.entry("🔔 Remind", "🔔 Rappeler")), set())
        self.assertEqual(added_emoji(self.entry("Your Vote", "Deine Stimme ✓")), {"✓"})
        self.assertEqual(
            added_emoji(self.entry("Install", "📲 Installer", flags=["fuzzy"])), set()
        )

    def test_command_reports_the_entry(self):
        po = polib.POFile()
        po.metadata = {"Content-Type": "text/plain; charset=UTF-8"}
        po.append(
            polib.POEntry(
                msgid="Install Crush.lu App",
                msgstr="📲 Installer l'app Crush.lu",
                occurrences=[("templates/crush_lu/partials/pwa.html", "1")],
            )
        )
        po.append(polib.POEntry(msgid="Hello", msgstr="Bonjour"))
        path = Path(self.tmp_dir()) / "django.po"
        po.save(str(path))
        out = io.StringIO()
        command = CheckTranslations(stdout=out)
        command._check_file(
            str(path), "fr", "django.po", {"no_fuzzy": False, "summary": False}
        )
        report = out.getvalue()
        self.assertIn("Emoji not in msgid: 1", report)
        self.assertIn('"Install Crush.lu App"', report)

    def tmp_dir(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name
