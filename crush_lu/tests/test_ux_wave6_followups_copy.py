"""UX Wave 6 follow-ups, batch 1: copy and translations.

1. #1154 / #1167: every template that shows a point count is plural-aware
   ("1 Punkt", not "1 Punkte"), and the live quiz's feedback line is
   translated instead of hardcoded English.
2. #1164: "This membership is not pending payment." has DE/FR translations.
3. #1166: FR greetings say "Bonjour", not "Salut" (decision H, #1159).
4. #1167: the DE "Save" entry is no longer a fuzzy "Gespeichert" (Saved),
   and the weekly KPI mail says "Introductions made", the term the coach UI
   uses.
"""

import json
import re
from pathlib import Path

import polib
from django.template import Context, Template
from django.test import SimpleTestCase
from django.utils import translation

from crush_lu.management.commands.send_weekly_kpis import EMAIL_SECTIONS

ROOT = Path(__file__).resolve().parents[2]
LOCALE = ROOT / "crush_lu" / "locale"
TEMPLATES = ROOT / "crush_lu" / "templates"
QUIZ_JS = ROOT / "crush_lu" / "static" / "crush_lu" / "js" / "quiz-live.js"

PLURALS = {
    "%(points)s point": (
        ("%(points)s Punkt", "%(points)s Punkte"),
        ("%(points)s point", "%(points)s points"),
    ),
    "%(points)s point earned": (
        ("%(points)s Punkt verdient", "%(points)s Punkte verdient"),
        ("%(points)s point gagné", "%(points)s points gagnés"),
    ),
    "%(total)s point in total": (
        ("%(total)s Punkt insgesamt", "%(total)s Punkte insgesamt"),
        ("%(total)s point au total", "%(total)s points au total"),
    ),
    "Your team finished with %(points)s point.": (
        (
            "Euer Team hat mit %(points)s Punkt abgeschlossen.",
            "Euer Team hat mit %(points)s Punkten abgeschlossen.",
        ),
        (
            "Votre équipe a terminé avec %(points)s point.",
            "Votre équipe a terminé avec %(points)s points.",
        ),
    ),
}

# A count rendered through `blocktrans with`: one msgid for every number.
SINGULAR_ONLY_COUNT = re.compile(
    r"{%\s*blocktrans with [^%]*%}"
    r"(?:(?!{%\s*endblocktrans).)*?"
    r"\{\{\s*\w+\s*\}\}\s+points\b",
    re.DOTALL,
)


def _po(lang):
    return polib.pofile(str(LOCALE / lang / "LC_MESSAGES" / "django.po"))


def _render(source, lang, **context):
    with translation.override(lang):
        return Template("{% load i18n %}" + source).render(Context(context))


class PointCountTests(SimpleTestCase):
    def test_no_template_renders_a_count_with_a_single_form(self):
        offenders = [
            str(path.relative_to(TEMPLATES))
            for path in TEMPLATES.rglob("*.html")
            if SINGULAR_ONLY_COUNT.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [])

    def test_plural_entries_are_translated(self):
        for lang, index in (("de", 0), ("fr", 1)):
            po = _po(lang)
            for msgid, forms in PLURALS.items():
                with self.subTest(lang=lang, msgid=msgid):
                    entry = po.find(msgid)
                    self.assertIsNotNone(entry)
                    self.assertFalse(entry.fuzzy)
                    self.assertEqual(
                        (entry.msgstr_plural[0], entry.msgstr_plural[1]),
                        forms[index],
                    )

    def test_counts_render_with_the_right_form(self):
        source = (
            "{% blocktrans count points=n %}{{ points }} point"
            "{% plural %}{{ points }} points{% endblocktrans %}"
        )
        self.assertEqual(_render(source, "de", n=1), "1 Punkt")
        self.assertEqual(_render(source, "de", n=5), "5 Punkte")
        # French treats 0 as singular.
        self.assertEqual(_render(source, "fr", n=0), "0 point")
        self.assertEqual(_render(source, "fr", n=2), "2 points")
        self.assertEqual(_render(source, "en", n=1), "1 point")


class QuizFeedbackTests(SimpleTestCase):
    def test_feedback_getters_read_translated_strings(self):
        js = QUIZ_JS.read_text(encoding="utf-8")
        getters = js[js.index("get feedbackText()") : js.index("get tableLabel()")]
        self.assertNotIn('"Correct!"', getters)
        self.assertNotIn('"Wrong!"', getters)
        self.assertNotIn('" points"', getters)
        self.assertNotIn('"Correct answer: "', getters)
        self.assertIn("this._i18n.correctAnswer", getters)

    def test_quiz_strings_stay_valid_json_in_every_language(self):
        page = (TEMPLATES / "crush_lu" / "quiz_live.html").read_text(encoding="utf-8")
        attribute = re.search(r"data-i18n='([^']*)'", page).group(1)
        expected = {
            "de": ("Falsch", "Richtige Antwort: {answer}"),
            "fr": ("Faux !", "Bonne réponse : {answer}"),
            "en": ("Wrong!", "Correct answer: {answer}"),
        }
        for lang, (wrong, answer) in expected.items():
            with self.subTest(lang=lang):
                strings = json.loads(_render(attribute, lang))
                self.assertEqual(strings["wrong"], wrong)
                self.assertEqual(strings["correctAnswer"], answer)
                self.assertTrue(strings["correct"])


class CatalogueFixTests(SimpleTestCase):
    def test_not_pending_payment_is_translated(self):
        msgid = "This membership is not pending payment."
        expected = {
            "de": "Diese Mitgliedschaft wartet nicht auf eine Zahlung.",
            "fr": "Cette adhésion n’est pas en attente de paiement.",
        }
        for lang, text in expected.items():
            with self.subTest(lang=lang):
                with translation.override(lang):
                    self.assertEqual(translation.gettext(msgid), text)

    def test_french_greetings_say_bonjour(self):
        offenders = [
            (entry.msgid, entry.msgstr)
            for entry in _po("fr")
            if not entry.obsolete and re.search(r"\bSalut\b", entry.msgstr)
        ]
        self.assertEqual(offenders, [])
        with translation.override("fr"):
            self.assertEqual(
                translation.gettext("Hey %(name)s!") % {"name": "Léa"},
                "Bonjour Léa !",
            )

    def test_german_save_entry_is_not_a_fuzzy_guess(self):
        # Only the catalogue changes: django.contrib.admin sits before
        # crush_lu in INSTALLED_APPS, so its "Sichern" is what renders.
        entry = _po("de").find("Save")
        self.assertFalse(entry.fuzzy)
        self.assertEqual(entry.msgstr, "Speichern")


class WeeklyKpiLabelTests(SimpleTestCase):
    def test_shared_connections_are_called_introductions(self):
        labels = dict(row for _title, rows in EMAIL_SECTIONS for row in rows)
        self.assertEqual(labels["connections_shared"], "Introductions made")
