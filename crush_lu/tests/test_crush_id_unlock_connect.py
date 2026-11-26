"""Crush ID: the "Unlock Crush Connect" card (Tom, 2026-11-26).

The Connect readiness checklist moved from the Connect hub into Crush ID as
one path: part 1 (approved + identity, photo, photo consent, onboarding)
makes the member visible in Connect, part 2 (coach identity check, three
questions) opens Connect Week. The Connect tab stays locked until the member
is onboarded and then leads to the card; the Connect onboarding and the
Connect profile (three questions) moved under /id/connect/.

Literal ``/en/...`` paths; testserver resolves to crush.lu.
"""

import html
import re

import pytest
from django.core.cache import cache

from crush_lu.models import CrushProfile
from crush_lu.tests.test_crush_connect import (
    _login_eligible,
    _make_user,
    _mark_attended,
    _set_gate_questions,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")

HUB_URL = "/en/id/"
CONNECT_HUB_URL = "/en/crush-connect/home/"


@pytest.fixture(autouse=True)
def _connect_open(settings):
    cache.clear()
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True


def _body(resp):
    assert resp.status_code == 200, resp.status_code
    return html.unescape(resp.content.decode())


def _card(body):
    start = body.index('id="unlock-connect"')
    return body[start : body.index("</section>", start)]


def _step_keys(card):
    return re.findall(r'data-readiness-step="([^"]+)"', card)


def _next_step(card):
    match = re.search(r'data-readiness-step="([^"]+)"\s+data-complete="false" data-next-step', card)
    return match.group(1) if match else None


def _connect_tab(body):
    start = body.index('<nav class="bottom-nav"')
    nav = body[start : body.index("</nav>", start)]
    tab_end = nav.index('data-tab="crush-connect"')
    return nav[nav.rindex("<a ", 0, tab_end) : tab_end]


def _member(client, username, **kwargs):
    kwargs.setdefault("premium", False)
    user = _make_user(username=username, **kwargs)
    CrushProfile.objects.filter(user=user).update(
        verification_status="verified", phone_verified=True
    )
    _login_eligible(client, user)
    return user


@pytest.mark.django_db
def test_locked_card_lists_every_step_in_unlock_order(client):
    _member(client, "unlock_new", onboarded=False, photo_share_consent=False)

    card = _card(_body(client.get(HUB_URL)))

    assert 'data-unlock-connect="locked"' in card
    assert _step_keys(card) == [
        "identity",
        "photo",
        "photo_consent",
        "onboarding",
        "active_verification",
        "questions",
    ]
    # LuxID-approved with a photo: consent is the first open step.
    assert _next_step(card) == "photo_consent"
    assert card.count("data-next-step") == 1
    assert 'data-unlock-milestone="open"' in card
    assert "Unlock Crush Connect" in card


@pytest.mark.django_db
def test_connect_tab_is_locked_and_leads_to_the_card(client):
    _member(client, "unlock_tab", onboarded=False)

    tab = _connect_tab(_body(client.get("/en/dashboard/")))

    assert "bottom-nav-item-locked" in tab
    assert 'href="/en/id/#unlock-connect"' in tab
    assert "data-connect-locked" in tab


@pytest.mark.django_db
def test_onboarded_member_is_visible_and_next_is_the_coach_check(client):
    _member(client, "unlock_visible")

    body = _body(client.get(HUB_URL))
    card = _card(body)

    assert 'data-unlock-milestone="reached"' in card
    assert _next_step(card) == "active_verification"
    # Onboarded: the tab opens Connect, where a short strip remains.
    tab = _connect_tab(body)
    assert "bottom-nav-item-locked" not in tab
    hub = _body(client.get(CONNECT_HUB_URL))
    assert "data-unlock-strip" in hub
    assert 'href="/en/id/#unlock-connect"' in hub
    assert 'id="connect-readiness-title"' not in hub


@pytest.mark.django_db
def test_all_steps_done_unlocks_and_connect_drops_the_checklist(client):
    user = _member(client, "unlock_done")
    _mark_attended(user)
    _set_gate_questions(user)

    card = _card(_body(client.get(HUB_URL)))

    assert 'data-unlock-connect="unlocked"' in card
    assert "Crush Connect unlocked" in card
    assert "data-unlock-open-connect" in card
    assert "data-next-step" not in card
    hub = _body(client.get(CONNECT_HUB_URL))
    assert "data-unlock-strip" not in hub
    assert "Ready to connect" not in hub
    assert 'id="connect-readiness-title"' not in hub


@pytest.mark.django_db
def test_excluded_member_gets_no_card_and_the_teaser(client):
    _member(client, "unlock_excluded", onboarded=False, excluded_by_coach=True)

    body = _body(client.get(HUB_URL))

    assert 'id="unlock-connect"' not in body
    assert 'href="/en/crush-connect/"' in _connect_tab(body)


@pytest.mark.django_db
def test_no_card_while_connect_is_closed(client, settings):
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    _member(client, "unlock_closed", onboarded=False)

    body = _body(client.get(HUB_URL))

    assert 'id="unlock-connect"' not in body
    assert 'href="/en/crush-connect/"' in _connect_tab(body)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("lang", "title"),
    (("de", "Crush Connect freischalten"), ("fr", "Débloquer Crush Connect")),
)
def test_card_is_translated(client, lang, title):
    _member(client, f"unlock_{lang}", onboarded=False)

    card = _card(_body(client.get(f"/{lang}/id/")))

    assert title in card


def test_onboarding_and_connect_profile_names_point_under_id():
    from django.urls import resolve, reverse
    from django.utils import translation

    urlconf = "azureproject.urls_crush"
    with translation.override("en"):
        assert (
            reverse("crush_lu:crush_connect_onboarding", urlconf=urlconf)
            == "/en/id/connect/onboarding/"
        )
        assert (
            reverse("crush_lu:crush_connect_onboarding_step", args=[3], urlconf=urlconf)
            == "/en/id/connect/onboarding/3/"
        )
        assert (
            reverse("crush_lu:crush_connect_profile_edit", urlconf=urlconf)
            == "/en/id/connect/profile/"
        )
    # The old addresses keep serving the same views (apps, sent emails).
    for old, name in (
        ("/en/crush-connect/onboarding/", "crush_connect_onboarding"),
        ("/en/crush-connect/onboarding/2/", "crush_connect_onboarding_step"),
        ("/en/crush-connect/profile/", "crush_connect_profile_edit"),
    ):
        assert resolve(old, urlconf=urlconf).url_name == name
