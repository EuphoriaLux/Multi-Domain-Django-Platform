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


# --- #1036 item 8: the real GA4 property cookie is registered ---------------


def _run_setup_cookie_groups():
    from io import StringIO

    from django.core.management import call_command

    call_command("setup_cookie_groups", stdout=StringIO())


def _analytics_cookie_names():
    from cookie_consent.models import Cookie

    return sorted(
        Cookie.objects.filter(cookiegroup__varname="analytics").values_list(
            "name", flat=True
        )
    )


@pytest.mark.django_db
def test_setup_cookie_groups_registers_real_ga4_property_cookies(monkeypatch):
    """django-cookie-consent deletes cookies by exact name, so the literal
    ``_ga_*`` row never matched GA4's ``_ga_<Measurement ID sans G->``."""
    monkeypatch.setenv("GA4_CRUSH_LU", "G-ABC123XYZ")
    monkeypatch.setenv("GA4_POWERUP", " g-POWER42 ")
    monkeypatch.setenv("GA4_ARBORIST", "G-ABC123XYZ")  # shared property
    monkeypatch.setenv("GA4_DELEGATIONS", "")
    monkeypatch.setenv("GA4_VINSDELUX", "not a measurement id")

    _run_setup_cookie_groups()
    names = _analytics_cookie_names()

    assert "_ga" in names
    assert "_ga_ABC123XYZ" in names
    assert "_ga_POWER42" in names
    assert "_ga_*" not in names
    assert not [n for n in names if n.startswith("_ga_") and " " in n]
    assert names.count("_ga_ABC123XYZ") == 1

    # Idempotent: a second start adds nothing.
    _run_setup_cookie_groups()
    assert _analytics_cookie_names() == names


@pytest.mark.django_db
def test_setup_cookie_groups_without_ga4_settings_adds_no_property_cookie(
    monkeypatch,
):
    from azureproject.analytics_context import GA4_MEASUREMENT_ID_ENV_VARS

    for var in GA4_MEASUREMENT_ID_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    _run_setup_cookie_groups()

    assert [n for n in _analytics_cookie_names() if n.startswith("_ga_")] == []
