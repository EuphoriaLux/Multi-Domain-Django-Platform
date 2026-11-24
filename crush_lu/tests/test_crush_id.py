"""Crush ID pillar, step 1: the four-pillar menu and the Crush ID hub.

Tom, 2026-11-23: verification, approval and personal data get their own
pillar ("Crush ID") beside Events, Crush Connect and Coaching. The menu is
the same for every profile status, Matches moved under Events, Coaching shows
as "coming soon", and active coaches keep their Coach tab in place of Home.

Literal ``/en/...`` paths; testserver resolves to crush.lu.
"""

import html
import re

import pytest
from django.core.cache import cache

from crush_lu.models import CrushProfile
from crush_lu.tests.test_crush_connect import (
    _get_coach,
    _login_eligible,
    _make_user,
    _mark_attended,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")

HUB_URL = "/en/id/"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _body(resp):
    assert resp.status_code == 200, resp.status_code
    return html.unescape(resp.content.decode())


def _bottom_nav(body):
    start = body.index('<nav class="bottom-nav"')
    return body[start : body.index("</nav>", start)]


def _tabs(body):
    return re.findall(r'data-tab="([^"]+)"', _bottom_nav(body))


def _hub(body):
    """The hub content only, without the menus that share the page."""
    start = body.index("data-crush-id-level")
    return body[start : body.index('id="mobile-drawer-panel"', start)]


def _level(body):
    return int(re.search(r'data-crush-id-level="(\d)"', body).group(1))


def _pending_member(client, username="cid_pending"):
    user = _make_user(
        username=username, is_approved=False, has_luxid=False, premium=False
    )
    CrushProfile.objects.filter(user=user).update(
        verification_status="pending", phone_verified=True
    )
    _login_eligible(client, user)
    return user


def _approved_member(client, username="cid_approved", **kwargs):
    user = _make_user(username=username, premium=False, **kwargs)
    CrushProfile.objects.filter(user=user).update(
        verification_status="verified", phone_verified=True
    )
    _login_eligible(client, user)
    return user


@pytest.mark.django_db
def test_hub_requires_login(client):
    resp = client.get(HUB_URL)
    assert resp.status_code == 302
    assert "login" in resp["Location"]


@pytest.mark.django_db
def test_luxid_alone_is_level_2(client):
    """LuxID puts a member in the Connect mix; Connect Week needs a coach
    to have met them (Tom, 2026-11-23)."""
    _approved_member(client, has_luxid=True)

    body = _body(client.get(HUB_URL))

    assert _level(body) == 2
    assert "data-crush-id-next" in body
    assert 'href="/en/id/privacy/"' in _hub(body)


@pytest.mark.django_db
def test_event_check_in_confirms_identity(client):
    """Door approval stays a full verification path (Tom, 2026-11-23)."""
    user = _approved_member(client, username="cid_door", has_luxid=False)
    _mark_attended(user)

    body = _body(client.get(HUB_URL))
    assert _level(body) == 3
    assert "Verified and identity confirmed" in body
    assert "data-crush-id-next" not in body


@pytest.mark.django_db
def test_approved_member_without_identity_check_is_level_2(client):
    _approved_member(client, username="cid_l2", has_luxid=False)

    body = _body(client.get(HUB_URL))

    assert _level(body) == 2
    assert "Verified and approved" in body
    assert "data-crush-id-next" in body
    assert "Confirm your identity" in body


@pytest.mark.django_db
def test_pending_member_gets_the_hub_and_one_next_step(client):
    _pending_member(client)

    body = _body(client.get(HUB_URL))

    assert _level(body) == 1
    assert "Profile submitted, not verified yet" in body
    assert 'href="/en/id/verify/"' in body
    # Privacy edits need an approved profile; the card stays hidden until then.
    assert "/en/id/privacy/" not in _hub(body)
    assert 'href="/en/id/data/"' in _hub(body)


@pytest.mark.django_db
def test_member_without_profile_is_sent_to_onboarding(client):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(
        username="cid_new", email="cid_new@example.com", password="x"
    )
    _login_eligible(client, user)

    body = _body(client.get(HUB_URL))

    assert _level(body) == 0
    assert 'href="/en/onboarding/"' in body


@pytest.mark.django_db
def test_bottom_nav_has_the_four_pillars_for_a_member(client):
    _approved_member(client, username="cid_tabs")

    tabs = _tabs(_body(client.get("/en/dashboard/")))

    assert tabs == ["home", "events", "crush-connect", "coaching", "crush-id"]


@pytest.mark.django_db
def test_menu_shape_does_not_change_with_profile_status(client):
    _pending_member(client, username="cid_shape")

    body = _body(client.get("/en/events/"))

    assert _tabs(body) == ["home", "events", "crush-connect", "coaching", "crush-id"]
    nav = _bottom_nav(body)
    assert 'data-crush-id-status="action"' in nav
    assert "bottom-nav-item-locked" in nav
    # The old status-specific top-bar entries are gone. (The onboarding
    # resume counter stays for members who have not finished setup.)
    for label in ("Under Review", "Fix Profile", "Contact Coach"):
        assert f"<span>{label}</span>" not in body
    assert 'data-nav="crush-id"' in body
    assert 'data-nav="crush-connect-teaser"' in body


@pytest.mark.django_db
def test_active_coach_keeps_coach_tab_in_place_of_home(client):
    coach = _get_coach()
    _make_user(username="cid_coach_member", premium=False)
    _login_eligible(client, coach.user)

    tabs = _tabs(_body(client.get("/en/events/")))

    assert tabs[0] == "coach"
    assert "home" not in tabs
    assert len(tabs) == 5


@pytest.mark.django_db
def test_matches_live_under_events(client):
    _approved_member(client, username="cid_matches")

    body = _body(client.get("/en/my-events/"))

    assert 'data-tab="connections"' not in body
    assert 'data-nav="matches"' in body
    assert 'href="/en/connections/"' in body


@pytest.mark.django_db
def test_coaching_page_is_public_teaser(client):
    body = _body(client.get("/en/coaching/"))

    assert "data-coaching-soon" in body
    assert "Coming in 2027" in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("lang", "expected"),
    (("de", "Meine Daten"), ("fr", "Mes données")),
)
def test_hub_is_translated(client, lang, expected):
    _approved_member(client, username=f"cid_{lang}")

    body = _body(client.get(f"/{lang}/id/"))

    assert expected in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "old_path", ("/en/profile-submitted/", "/en/crush-connect/verification/")
)
def test_old_verify_pages_redirect_permanently_with_query(client, old_path):
    """Step 2: links in sent emails and push messages keep working."""
    resp = client.get(f"{old_path}?utm_source=email&next=//evil.example")

    assert resp.status_code == 301
    location = resp["Location"]
    assert location.startswith("/en/id/verify/?")
    assert "utm_source=email" in location


# ---------------------------------------------------------------------------
# Step 4: profile, privacy, data and account under /id/
# ---------------------------------------------------------------------------


def _templates(resp):
    assert resp.status_code == 200, resp.status_code
    return {t.name for t in resp.templates}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "partial"),
    (
        ("/en/id/privacy/", "crush_lu/partials/edit_privacy.html"),
        ("/en/id/account/", "crush_lu/partials/edit_account.html"),
        ("/en/id/data/", "crush_lu/partials/edit_account_danger.html"),
        (
            "/en/id/account/?sub=notifications",
            "crush_lu/partials/edit_account_notifications.html",
        ),
    ),
)
def test_short_addresses_open_their_section(client, path, partial):
    _approved_member(client, username="cid_s4_" + re.sub(r"\W", "_", path))

    assert partial in _templates(client.get(path))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    (
        "/en/id/profile/",
        "/en/id/data/gdpr/",
        # Old addresses: same pages, no redirect (apps, emails, open forms).
        "/en/profile/edit/",
        "/en/account/gdpr/",
    ),
)
def test_new_and_old_addresses_both_serve(client, path):
    _approved_member(
        client, username="cid_s4_both_" + path.strip("/").replace("/", "_")
    )

    assert client.get(path).status_code == 200


def test_url_names_point_under_id():
    from django.urls import reverse
    from django.utils import translation

    expected = {
        "edit_profile": "/en/id/profile/",
        "crush_id_privacy": "/en/id/privacy/",
        "crush_id_account": "/en/id/account/",
        "crush_id_data": "/en/id/data/",
        "set_password": "/en/id/account/password/",
        "apple_link_prompt": "/en/id/account/link-apple/",
        "update_whatsapp_preference": "/en/id/account/whatsapp-preference/",
        "delete_crushlu_profile": "/en/id/data/delete-profile/",
        "take_a_break": "/en/id/data/take-a-break/",
        "resume_from_break": "/en/id/data/resume/",
        "gdpr_data_management": "/en/id/data/gdpr/",
        "export_user_data": "/en/id/data/gdpr/export/",
    }
    with translation.override("en"):
        for name, path in expected.items():
            assert (
                reverse(f"crush_lu:{name}", urlconf="azureproject.urls_crush") == path
            )


@pytest.mark.parametrize(
    ("old", "new", "name"),
    (
        ("/en/account/gdpr/", "/en/id/data/gdpr/", "gdpr_data_management"),
        ("/en/account/set-password/", "/en/id/account/password/", "set_password"),
        (
            "/en/account/delete-profile/",
            "/en/id/data/delete-profile/",
            "delete_crushlu_profile",
        ),
    ),
)
def test_old_and_new_paths_share_the_guarded_url_name(old, new, name):
    """The playground practice guard blocks by url_name: both paths must
    carry it, or the old one would slip past the guard."""
    from django.urls import resolve

    from crush_lu.middleware import PracticeModeGuardMiddleware as Guard

    for path in (old, new):
        assert resolve(path, urlconf="azureproject.urls_crush").url_name == name
    assert name in Guard.BLOCKED_URL_NAMES | Guard.BLOCKED_POST_URL_NAMES


@pytest.mark.parametrize(
    ("path", "query", "allowed"),
    (
        ("/en/id/data/", None, True),
        ("/en/id/data/gdpr/", None, True),
        ("/en/id/data/delete-profile/", None, True),
        ("/en/id/account/", {}, True),
        ("/en/id/account/", {"sub": "settings"}, False),
        ("/en/id/profile/", {"section": "account", "sub": "danger"}, True),
        ("/en/id/profile/", {"section": "privacy"}, False),
        ("/en/id/privacy/", None, False),
    ),
)
def test_paused_deletion_can_finish_from_the_new_addresses(path, query, allowed):
    from crush_lu.consent_middleware import CrushConsentMiddleware

    middleware = CrushConsentMiddleware(lambda request: None)

    assert middleware._is_deletion_retry_path(path, query) is allowed
