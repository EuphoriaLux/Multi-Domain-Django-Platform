"""UX Wave 3 · WP11 — Connect hub readiness gating (findings 6-01, 6-12).

Companion to ``test_crush_connect.py`` (whose helpers we reuse). All view
tests use literal ``/en/crush-connect/...`` paths under ``urls_crush`` —
``reverse()`` resolves against the default urlconf, not the host-selected
one (see AGENTS.md).
"""

import io
from urllib.parse import quote

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile

from crush_lu.tests.test_crush_connect import (
    HUB_URL,
    _login_eligible,
    _make_user,
    _mark_attended,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")

WEEK_HOME_URL = "/en/crush-connect/week/"
EDIT_PROFILE_URL = "/en/profile/edit/"


def setup_function(_function):
    cache.clear()


def _valid_photo_upload(name="profile.jpg"):
    """A real (if tiny) JPEG large enough to pass the >=200x200 photo check."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (200, 200), color="red").save(buf, format="JPEG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/jpeg")


def _make_verified_phone_member(**kwargs):
    """A member whose phone is already verified, so a full-profile POST (the
    photos section form re-submits the whole ``CrushProfileForm``) doesn't
    also need to clear the separate phone-verification gate."""
    member = _make_user(**kwargs)
    member.crushprofile.phone_number = "+352600000000"
    member.crushprofile.phone_verified = True
    member.crushprofile.event_languages = ["en"]
    member.crushprofile.save(
        update_fields=["phone_number", "phone_verified", "event_languages"]
    )
    return member


@pytest.mark.django_db
def test_hub_explains_blocking_step_instead_of_open_today(client, settings):
    """Finding 6-01: cycle access (event-verified) does not imply readiness
    (photo). The hub must explain what's missing rather than show "Open
    Today", which would otherwise silently bounce to the photo editor."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="blocked_member", onboarded=True)
    _mark_attended(me)
    me.crushprofile.photo_1 = ""
    me.crushprofile.save(update_fields=["photo_1"])
    _login_eligible(client, me)

    response = client.get(HUB_URL)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Almost ready for today's suggestions" in content
    assert "A profile photo is required" in content
    # The CTA carries a same-app `next` back to the hub, and there is no bare
    # "Open Today" link left for a member who cannot use it yet.
    assert f"next={HUB_URL}" in content
    assert ">Open Today<" not in content


@pytest.mark.django_db
def test_hub_shows_open_today_once_readiness_is_complete(client, settings):
    """Control case: once every readiness step is complete, the ordinary
    "Open Today" card renders (no regression from the 6-01 fix)."""
    from crush_lu.tests.test_crush_connect import _set_gate_questions

    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="ready_member", onboarded=True)
    _mark_attended(me)
    _set_gate_questions(me)
    _login_eligible(client, me)

    response = client.get(HUB_URL)
    content = response.content.decode()

    assert response.status_code == 200
    assert ">Open Today<" in content
    assert "Almost ready for today's suggestions" not in content


@pytest.mark.django_db
def test_connect_week_home_missing_photo_redirect_carries_next(client, settings):
    """Finding 6-01: the Today bounce must carry a way back, not just land on
    the generic photo editor."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="week_next_member", onboarded=True)
    _mark_attended(me)
    me.crushprofile.photo_1 = ""
    me.crushprofile.save(update_fields=["photo_1"])
    _login_eligible(client, me)

    response = client.get(WEEK_HOME_URL)

    assert response.status_code == 302
    assert "section=photos" in response.url
    assert f"next={quote(WEEK_HOME_URL, safe='')}" in response.url


@pytest.mark.django_db
def test_edit_profile_photo_upload_returns_to_safe_next(client, settings):
    """Finding 6-01: after adding the missing photo, the member lands back
    where they came from (e.g. Connect Week), not on the generic profile
    overview with no way back."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_verified_phone_member(username="week_return_member", onboarded=True)
    me.crushprofile.photo_1 = ""
    me.crushprofile.save(update_fields=["photo_1"])
    _login_eligible(client, me)

    response = client.post(
        f"{EDIT_PROFILE_URL}?section=photos",
        data={
            "next": WEEK_HOME_URL,
            "phone_number": "+352600000000",
            "event_languages": ["en"],
            "photo_1": _valid_photo_upload(),
        },
    )

    assert response.status_code == 302
    assert response.url == WEEK_HOME_URL
    me.crushprofile.refresh_from_db()
    assert me.crushprofile.photo_1


@pytest.mark.django_db
def test_edit_profile_photo_upload_ignores_unsafe_next(client, settings):
    """The `next` redirect target is validated: an off-host value falls back
    to the ordinary profile overview instead of being followed."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_verified_phone_member(username="unsafe_next_member", onboarded=True)
    me.crushprofile.photo_1 = ""
    me.crushprofile.save(update_fields=["photo_1"])
    _login_eligible(client, me)

    response = client.post(
        f"{EDIT_PROFILE_URL}?section=photos",
        data={
            "next": "https://evil.example.com/",
            "phone_number": "+352600000000",
            "event_languages": ["en"],
            "photo_1": _valid_photo_upload(),
        },
    )

    assert response.status_code == 302
    assert response.url == EDIT_PROFILE_URL


@pytest.mark.django_db
def test_hub_premium_line_names_and_links_the_coach(client, settings):
    """Finding 6-12: the premium line must say who the coach is and link to
    Coach's Pick, not just name-drop them with no context."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="premium_member", onboarded=True, premium=True)
    _login_eligible(client, me)

    response = client.get(HUB_URL)
    content = response.content.decode()

    assert response.status_code == 200
    assert "Your coach:" in content
    assert '<a href="/en/crush-connect/coach-pick/"' in content


@pytest.mark.django_db
def test_hub_card_headings_use_card_title_not_bare_h2(client, settings):
    """Finding 6-12: the Connect Week card heading must not render at the
    browser-default (bare) h2 size, which dwarfs the styled h1 above it."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="heading_member", onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    response = client.get(HUB_URL)
    content = response.content.decode()

    assert response.status_code == 200
    assert '<h2 class="card-title">' in content
    assert "<h2>" not in content
