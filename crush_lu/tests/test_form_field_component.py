"""components/form_field.html: every aria-describedby id must exist (audit A9).

Django adds aria-describedby="<auto_id>_helptext <auto_id>_error" to a
widget by itself; a reference to an id that is not on the page makes the
screen reader announce nothing, so the error and help paragraphs have to
carry exactly those ids.
"""

from html.parser import HTMLParser

from django import forms
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from crush_lu.forms import EventPreferenceForm
from crush_lu.forms_crush_spark import SparkRequestForm

FIELD_TEMPLATE = "crush_lu/components/form_field.html"


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.described_by = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if attrs.get("aria-describedby"):
            key = attrs.get("id") or attrs.get("name") or tag
            self.described_by[key] = attrs["aria-describedby"].split()


def _collect(html):
    collector = _Collector()
    collector.feed(html)
    return collector


class _NameForm(forms.Form):
    name = forms.CharField(help_text="Your first name is enough.")


class _PlainForm(forms.Form):
    nickname = forms.CharField(required=False)


class _PresetForm(forms.Form):
    code = forms.CharField(
        widget=forms.TextInput(attrs={"aria-describedby": "code-rules"})
    )


class _PresetHelpForm(forms.Form):
    code = forms.CharField(
        help_text="From your ticket.",
        widget=forms.TextInput(attrs={"aria-describedby": "code-rules"}),
    )


class FormFieldDescribedByTests(SimpleTestCase):
    def assert_references_resolve(self, html):
        page = _collect(html)
        self.assertEqual(len(page.ids), len(set(page.ids)), page.ids)
        for element, ids in page.described_by.items():
            for ref in ids:
                self.assertIn(ref, page.ids, f"{element} -> {ref}")
        return page

    def test_error_and_help_text_ids_resolve(self):
        form = _NameForm(data={})
        self.assertFalse(form.is_valid())
        html = render_to_string(FIELD_TEMPLATE, {"field": form["name"]})
        page = self.assert_references_resolve(html)
        self.assertEqual(
            page.described_by["id_name"], ["id_name_helptext", "id_name_error"]
        )

    def test_valid_field_points_at_help_text_only(self):
        form = _NameForm(data={"name": "Ana"})
        self.assertTrue(form.is_valid())
        html = render_to_string(FIELD_TEMPLATE, {"field": form["name"]})
        page = self.assert_references_resolve(html)
        self.assertEqual(page.described_by["id_name"], ["id_name_helptext"])

    def test_caller_help_is_announced_with_django_ids(self):
        form = _NameForm(data={})
        form.is_valid()
        html = render_to_string(
            FIELD_TEMPLATE, {"field": form["name"], "help": "Shown to coaches."}
        )
        page = self.assert_references_resolve(html)
        self.assertEqual(
            page.described_by["id_name"],
            ["id_name_helptext", "id_name_error", "id_name_help"],
        )

    def test_caller_help_alone(self):
        form = _PlainForm()
        html = render_to_string(
            FIELD_TEMPLATE, {"field": form["nickname"], "help": "Optional."}
        )
        page = self.assert_references_resolve(html)
        self.assertEqual(page.described_by["id_nickname"], ["id_nickname_help"])

    def test_widget_describedby_is_kept(self):
        form = _PresetForm()
        html = render_to_string(
            FIELD_TEMPLATE, {"field": form["code"], "help": "Six digits."}
        )
        page = _collect(html)
        self.assertEqual(page.described_by["id_code"], ["code-rules", "id_code_help"])

    def test_widget_describedby_is_merged_with_help_text_and_errors(self):
        # Django adds nothing when the widget presets aria-describedby, which
        # would leave the rendered help and error paragraphs unannounced.
        form = _PresetHelpForm(data={})
        self.assertFalse(form.is_valid())
        html = render_to_string(FIELD_TEMPLATE, {"field": form["code"]})
        self.assertEqual(
            _collect(html).described_by["id_code"],
            ["code-rules", "id_code_helptext", "id_code_error"],
        )
        html = render_to_string(
            FIELD_TEMPLATE, {"field": form["code"], "help": "Six digits."}
        )
        self.assertEqual(
            _collect(html).described_by["id_code"],
            ["code-rules", "id_code_helptext", "id_code_error", "id_code_help"],
        )

    def test_no_references_without_help_or_errors(self):
        html = render_to_string(FIELD_TEMPLATE, {"field": _PlainForm()["nickname"]})
        self.assertEqual(_collect(html).described_by, {})

    def test_prefixed_forms_stay_unique(self):
        first = _NameForm(data={}, prefix="first")
        second = _NameForm(data={}, prefix="second")
        first.is_valid()
        second.is_valid()
        html = render_to_string(
            FIELD_TEMPLATE, {"field": first["name"], "help": "A"}
        ) + render_to_string(FIELD_TEMPLATE, {"field": second["name"], "help": "B"})
        page = self.assert_references_resolve(html)
        self.assertIn("id_first-name_error", page.described_by["id_first-name"])
        self.assertIn("id_second-name_error", page.described_by["id_second-name"])

    def test_no_auto_id_renders_no_ids(self):
        form = _NameForm(data={}, auto_id=False)
        form.is_valid()
        html = render_to_string(FIELD_TEMPLATE, {"field": form["name"], "help": "X"})
        page = _collect(html)
        self.assertEqual(page.ids, [])
        self.assertEqual(page.described_by, {})


class RealFormDescribedByTests(TestCase):
    """The two callers of form_field.html, rendered the way their pages do."""

    def test_event_age_range_errors_resolve(self):
        form = EventPreferenceForm(
            data={"preferred_age_min": "10", "preferred_age_max": "120"}
        )
        self.assertFalse(form.is_valid())
        html = render_to_string(
            "crush_lu/components/event_preference_fields.html", {"pref_form": form}
        )
        page = _collect(html)
        for field in ("preferred_age_min", "preferred_age_max"):
            self.assertIn(f"id_{field}_error", page.described_by[f"id_{field}"])
        for element, ids in page.described_by.items():
            for ref in ids:
                self.assertIn(ref, page.ids, f"{element} -> {ref}")

    def test_spark_request_help_is_described(self):
        form = SparkRequestForm(data={})
        form.is_valid()
        html = render_to_string(
            FIELD_TEMPLATE,
            {"field": form["sender_description"], "help": "Be specific."},
        )
        page = _collect(html)
        refs = page.described_by["id_sender_description"]
        self.assertIn("id_sender_description_help", refs)
        for ref in refs:
            self.assertIn(ref, page.ids)
