"""UX Wave 6 follow-ups, batch 2: confirmations, labels, buttons, plurals.

1. #1167: member-facing pages ask through the branded confirm sheet instead
   of a native ``confirm()``. The Django-admin journey generator keeps its
   native prompt: the admin does not load the sheet.
2. #1167: the admin label of a shared ``EventConnection`` says "Introduction
   made", the term the coach screens use.
3. #1167: ``connection_detail.html`` (and ``account/email_crush.html``,
   touched here) keep one gradient CTA, so the design-token lint is clean.
4. #1166: the dead ``.cta-subtext`` span and bullet-separator rules are gone.
5. #1154: vote counts are plural-aware ("1 Stimme", not "1 Stimmen"), in the
   rendered templates and in the JS that updates them live.
"""

import importlib.util
import re
from pathlib import Path

import polib
from django.template import Context, Template
from django.test import SimpleTestCase
from django.utils import translation

from crush_lu.models import EventConnection

ROOT = Path(__file__).resolve().parents[2]
LOCALE = ROOT / "crush_lu" / "locale"
TEMPLATES = ROOT / "crush_lu" / "templates"
JS = ROOT / "crush_lu" / "static" / "crush_lu" / "js"
TAILWIND_INPUT = ROOT / "tailwind-src" / "crush_lu" / "tailwind-input.css"

NATIVE_CONFIRM = re.compile(r"(?<![\w.])confirm\(")


def _read(path):
    return path.read_text(encoding="utf-8")


def _render(source, lang, **context):
    with translation.override(lang):
        return Template("{% load i18n %}" + source).render(Context(context))


def _load_linter():
    spec = importlib.util.spec_from_file_location(
        "lint_design_tokens", ROOT / "crush_lu/scripts/lint_design_tokens.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfirmSheetTests(SimpleTestCase):
    def test_member_pages_use_no_native_confirm(self):
        for name in (
            "account/email_crush.html",
            "crush_lu/coach_presentation_control.html",
        ):
            with self.subTest(template=name):
                source = _read(TEMPLATES / name)
                self.assertIsNone(NATIVE_CONFIRM.search(source))

    def test_email_remove_asks_through_the_sheet(self):
        source = _read(TEMPLATES / "account" / "email_crush.html")
        button = re.search(r'<button[^>]*name="action_remove"[^>]*>', source)
        self.assertIsNotNone(button)
        self.assertIn("data-confirm=", button.group(0))
        self.assertNotIn("extra_js", source)

    def test_pwa_debug_falls_back_only_without_the_sheet(self):
        source = _read(TEMPLATES / "crush_lu" / "pwa_debug.html")
        # The single native call is the fallback inside askConfirm().
        self.assertIsNone(NATIVE_CONFIRM.search(source))
        self.assertEqual(source.count("window.confirm("), 1)
        self.assertIn("window.crushConfirm(message)", source)
        self.assertEqual(source.count("await askConfirm("), 2)


class ConnectionLabelTests(SimpleTestCase):
    def test_shared_status_is_called_an_introduction(self):
        connection = EventConnection(status="shared")
        self.assertEqual(connection.get_status_display(), "Introduction made")


class DesignTokenTests(SimpleTestCase):
    def test_pages_keep_one_gradient_cta(self):
        lint = _load_linter()
        for name in ("crush_lu/connection_detail.html", "account/email_crush.html"):
            with self.subTest(template=name):
                self.assertEqual(lint.scan_file(TEMPLATES / name), [])

    def test_dead_cta_subtext_rules_are_gone(self):
        css = _read(TAILWIND_INPUT)
        self.assertIn(".cta-subtext {", css)
        self.assertNotIn(".cta-subtext span", css)


class VoteCountTests(SimpleTestCase):
    SOURCE = "{% blocktrans count votes=n %}vote{% plural %}votes{% endblocktrans %}"

    def test_templates_render_counts_with_the_right_form(self):
        self.assertEqual(_render(self.SOURCE, "en", n=1), "vote")
        self.assertEqual(_render(self.SOURCE, "de", n=1), "Stimme")
        self.assertEqual(_render(self.SOURCE, "de", n=3), "Stimmen")
        # French treats 0 as singular.
        self.assertEqual(_render(self.SOURCE, "fr", n=0), "vote")
        self.assertEqual(_render(self.SOURCE, "fr", n=2), "votes")

    def test_no_template_shows_a_count_with_a_fixed_votes_label(self):
        for name in (
            "admin/crush_lu/poll_analytics_detail.html",
            "crush_lu/event_voting_results.html",
        ):
            with self.subTest(template=name):
                self.assertNotIn('{% trans "votes" %}', _read(TEMPLATES / name))

    def test_live_results_relabel_after_each_update(self):
        template = _read(TEMPLATES / "crush_lu" / "event_voting_results.html")
        self.assertEqual(template.count('-count-label">{% blocktrans count'), 4)
        script = _read(JS / "event-voting.js")
        self.assertIn('"-count-label"', script)
        self.assertRegex(script, r'window\.ngettext\(\s*"vote",\s*"votes",')

    def test_display_loads_the_catalog_before_alpine(self):
        page = _read(TEMPLATES / "crush_lu" / "speed_dating_display.html")
        catalog = page.index("{% url 'javascript-catalog-lang' LANGUAGE_CODE %}")
        self.assertLess(catalog, page.index("alpinejs-csp"))
        self.assertIn('x-text="votesSubmittedLabel"', page)
        self.assertIn('x-text="votesCastLabel"', page)

    def test_js_plural_entries_are_translated(self):
        expected = {
            "de": {
                "vote": ("Stimme", "Stimmen"),
                "vote submitted": ("Stimme abgegeben", "Stimmen abgegeben"),
                "vote cast": ("Stimme abgegeben", "Stimmen abgegeben"),
            },
            "fr": {
                "vote": ("vote", "votes"),
                "vote submitted": ("vote enregistré", "votes enregistrés"),
                "vote cast": ("vote exprimé", "votes exprimés"),
            },
        }
        for lang, entries in expected.items():
            po = polib.pofile(str(LOCALE / lang / "LC_MESSAGES" / "djangojs.po"))
            for msgid, forms in entries.items():
                with self.subTest(lang=lang, msgid=msgid):
                    entry = po.find(msgid)
                    self.assertIsNotNone(entry)
                    self.assertFalse(entry.fuzzy)
                    self.assertEqual(
                        (entry.msgstr_plural[0], entry.msgstr_plural[1]), forms
                    )
