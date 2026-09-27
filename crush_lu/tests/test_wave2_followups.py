"""UX Wave 2 follow-ups (#1054, #1036, #1059).

Run with: pytest crush_lu/tests/test_wave2_followups.py -n 0
"""

import pytest
from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils import translation


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


# --- #1054: no photo-blur promise in the 72h reminder -----------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "lang, blur, first_name",
    [
        ("en", "blur", "show only your first name"),
        ("de", "verwischen", "nur deinen Vornamen anzeigen"),
        ("fr", "flouter", "montrer seulement votre prénom"),
    ],
)
def test_incomplete_profile_reminder_promises_no_photo_blur(lang, blur, first_name):
    """Photo blurring was removed; showing only the first name is still the
    default (CrushProfile.show_full_name=False)."""
    with translation.override(lang):
        html = render_to_string(
            "crush_lu/emails/profile_incomplete_72h.html",
            {"completion_status": "step2", "greeting_name": "Alex"},
        )
    assert blur not in html
    assert first_name in html
