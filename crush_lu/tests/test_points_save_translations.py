"""Point availability and coach Save copy rendered with shipped translations."""

import gettext
import re
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import polib
import pytest
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import override_settings
from django.utils import translation

LOCALE = Path(__file__).resolve().parents[1] / "locale"
CHALLENGES = [
    "multiple_choice",
    "riddle",
    "timeline_sort",
    "word_scramble",
    "would_you_rather",
    "open_text",
]


class PointsBadge(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.labels = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "journey-points-display" in attrs.get("class", "").split():
            self.labels.append(attrs.get("aria-label"))


def expected_label(lang, points):
    if lang == "de":
        return f"{points} {'Punkt' if points == 1 else 'Punkte'} verfügbar"
    if lang == "fr":
        return f"{points} {'point disponible' if points <= 1 else 'points disponibles'}"
    return f"{points} {'point' if points == 1 else 'points'} available"


@pytest.mark.parametrize("challenge_name", CHALLENGES)
@pytest.mark.parametrize("lang", ["en", "de", "fr"])
@pytest.mark.parametrize("points", [0, 1, 2, 7])
def test_challenge_page_announces_available_points_with_correct_plural(
    challenge_name, lang, points
):
    context = {
        "LANGUAGE_CODE": lang,
        "user": AnonymousUser(),
        "challenge": SimpleNamespace(
            id=1, points_awarded=points, options={}, question="A question"
        ),
        "chapter": SimpleNamespace(id=1, chapter_number=1),
        "journey": SimpleNamespace(journey_name="Test journey"),
        "journey_query": "",
        "localized_options": {},
        "existing_attempt": None,
    }
    with override_settings(ROOT_URLCONF="azureproject.urls_crush"):
        with translation.override(lang):
            markup = render_to_string(
                f"crush_lu/journey/challenges/{challenge_name}.html", context
            )
    assert PointsBadge(markup).labels == [expected_label(lang, points)]


def test_french_coach_call_form_offers_save_as_an_action():
    connection = SimpleNamespace(
        id=1,
        status="pending",
        requester=SimpleNamespace(id=1),
        recipient=SimpleNamespace(id=2),
        event=SimpleNamespace(id=1, title="Test event"),
    )
    with override_settings(ROOT_URLCONF="azureproject.urls_crush"):
        with translation.override("fr"):
            markup = render_to_string(
                "crush_lu/coach_connection_review.html",
                {
                    "LANGUAGE_CODE": "fr",
                    "user": AnonymousUser(),
                    "connection": connection,
                    "is_crush_lead": True,
                    "lead_open": True,
                    "lead_needs_claim": False,
                },
            )
    forms = re.findall(r"<form\b[^>]*>(.*?)</form>", markup, re.S)
    schedule = next(
        form for form in forms if 'name="action" value="crush_schedule_call"' in form
    )
    assert re.search(r"<button\b[^>]*>\s*Enregistrer\s*</button>", schedule)
    assert "Enregistré" not in schedule


def test_reviewed_french_save_is_in_the_compiled_catalogue():
    entry = polib.pofile(LOCALE / "fr/LC_MESSAGES/django.po").find("Save")
    assert "fuzzy" not in entry.flags
    assert entry.msgstr == "Enregistrer"
    with (LOCALE / "fr/LC_MESSAGES/django.mo").open("rb") as compiled:
        assert gettext.GNUTranslations(compiled).gettext("Save") == "Enregistrer"
    with translation.override("fr"):
        assert translation.gettext("Save") == "Enregistrer"
        assert translation.gettext("Saved") == "Enregistré"


@pytest.mark.parametrize("lang", ["en", "de", "fr"])
@pytest.mark.parametrize("points", [0, 1, 2, 7])
def test_application_compiled_catalogue_interpolates_available_points(lang, points):
    with (LOCALE / lang / "LC_MESSAGES/django.mo").open("rb") as compiled:
        catalogue = gettext.GNUTranslations(compiled)
    message = catalogue.ngettext(
        "%(points)s point available", "%(points)s points available", points
    )
    assert message % {"points": points} == expected_label(lang, points)
