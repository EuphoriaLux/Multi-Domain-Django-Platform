"""
Account-exit UX (UX Wave 2, WP7: findings 8-05 and 8-15).

- GDPR / delete pages speak user-facing language (no internal brand names),
  render labelled confirm inputs, and route destructive forms through the
  global confirm sheet (``data-confirm`` on the <form>).
- The legacy /account/delete/ URL redirects GETs to the profile deletion
  page and still accepts the GDPR deletion POST (Codex #1050).
- Unblock is confirmed through the sheet and offers an Undo toast that
  re-blocks through the existing block endpoint.
"""

import re
import time

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import translation

from crush_lu.models import UserBlock
from crush_lu.tests.test_crush_connect import _make_user
from crush_lu.tests.test_moderation import _grant_consent
from crush_lu.views_moderation import (
    UNDO_UNBLOCK_MAX_AGE_SECONDS,
    UNDO_UNBLOCK_SESSION_KEY,
)

pytestmark = [pytest.mark.urls("azureproject.urls_crush"), pytest.mark.django_db]

User = get_user_model()
HOST = {"HTTP_HOST": "crush.lu"}
INTERNAL_NAMES = ("PowerUp", "Entreprinder", "VinsDelux", "multi-domain")


@pytest.fixture(autouse=True)
def _clear_cache():
    # Shared @ratelimit counters survive between tests (AGENTS.md trap).
    cache.clear()
    yield
    cache.clear()


def _login(client, username="me"):
    user = _grant_consent(_make_user(username=username))
    client.force_login(user)
    return user


def _forms(html):
    return re.findall(r"<form\b[^>]*>", html)


# ---------------------------------------------------------------------------
# 8-05: legacy URL, GDPR + delete pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_legacy_account_delete_redirects_to_profile_deletion(client, lang):
    _login(client)
    resp = client.get(f"/{lang}/account/delete/", **HOST)
    assert resp.status_code == 302
    assert resp["Location"] == f"/{lang}/account/delete-profile/"


def test_legacy_account_delete_head_redirects(client):
    _login(client)
    resp = client.head("/en/account/delete/", **HOST)
    assert resp.status_code == 302
    assert resp["Location"] == "/en/account/delete-profile/"


def test_legacy_account_delete_post_performs_gdpr_deletion(client):
    # Codex #1050: forms rendered at the legacy URL POST back to it; a 302
    # would be replayed as GET and silently drop the deletion request.
    from crush_lu.models import CrushProfile

    user = _login(client)
    assert CrushProfile.objects.filter(user=user).exists()
    resp = client.post(
        "/en/account/delete/",
        {"deletion_type": "crushlu_only", "confirm_email": user.email},
        **HOST,
    )
    assert resp.status_code == 302
    assert resp["Location"] == "/en/account/settings/"
    assert not CrushProfile.objects.filter(user=user).exists()
    assert User.objects.filter(pk=user.pk).exists()


def test_legacy_account_delete_post_checks_email_and_csrf(client):
    user = _login(client)
    resp = client.post(
        "/en/account/delete/",
        {"deletion_type": "full_account", "confirm_email": "wrong@example.com"},
        **HOST,
    )
    assert resp.status_code == 302
    assert resp["Location"] == "/en/account/gdpr/"
    assert User.objects.filter(pk=user.pk).exists()

    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    resp = strict.post(
        "/en/account/delete/",
        {"deletion_type": "full_account", "confirm_email": user.email},
        **HOST,
    )
    assert resp.status_code == 403
    assert User.objects.filter(pk=user.pk).exists()


def test_legacy_account_delete_post_requires_login(client):
    resp = client.post(
        "/en/account/delete/",
        {"deletion_type": "full_account", "confirm_email": "x@example.com"},
        **HOST,
    )
    assert resp.status_code == 302
    assert "/account/delete-profile/" not in resp["Location"]


def test_gdpr_page_has_no_internal_brand_names(client):
    _login(client)
    html = client.get("/en/account/gdpr/", **HOST).content.decode()
    for name in INTERNAL_NAMES:
        assert name not in html
    assert "Login account" in html


def test_gdpr_page_download_comes_before_deletion(client):
    _login(client)
    html = client.get("/en/account/gdpr/", **HOST).content.decode()
    assert html.index("/en/account/gdpr/export/") < html.index("Account Deletion")


def test_gdpr_page_confirm_inputs_are_labelled(client):
    _login(client)
    html = client.get("/en/account/gdpr/", **HOST).content.decode()
    for prefix in ("profile", "account"):
        field_id = f"id_{prefix}_confirm_email"
        assert f'<label for="{field_id}"' in html
        assert re.search(rf'<input[^>]*name="confirm_email"[^>]*id="{field_id}"', html)
    assert html.count('class="input-crush"') >= 2


def test_gdpr_deletion_forms_use_confirm_sheet(client):
    _login(client)
    html = client.get("/en/account/gdpr/", **HOST).content.decode()
    confirm_forms = [f for f in _forms(html) if "data-confirm=" in f]
    assert len(confirm_forms) == 2
    assert "onsubmit" not in html
    assert html.count('class="btn-danger btn-sm"') == 2


def test_gdpr_full_deletion_still_checks_email(client):
    user = _login(client)
    resp = client.post(
        "/en/account/gdpr/",
        {"deletion_type": "full_account", "confirm_email": "wrong@example.com"},
        **HOST,
    )
    assert resp.status_code == 302
    assert User.objects.filter(pk=user.pk).exists()


def test_delete_profile_page_labelled_and_confirmed(client):
    _login(client)
    html = client.get("/en/account/delete-profile/", **HOST).content.decode()
    assert '<label for="id_confirm_email"' in html
    assert [f for f in _forms(html) if "data-confirm=" in f]
    assert "onclick=" not in html
    for name in INTERNAL_NAMES:
        assert name not in html


def test_settings_danger_zone_has_no_internal_brand_names():
    from django.contrib.auth.models import AnonymousUser

    request = RequestFactory().get("/en/account/settings/")
    request.user = AnonymousUser()
    with translation.override("en"):
        html = render_to_string(
            "crush_lu/partials/edit_account_danger.html", request=request
        )
    assert "PowerUp" not in html


# ---------------------------------------------------------------------------
# 8-15: Blocked members — confirm, 44px target, empty state, undo
# ---------------------------------------------------------------------------


def test_unblock_form_goes_through_confirm_sheet(client):
    me = _login(client)
    target = _make_user(username="target")
    UserBlock.objects.create(blocker=me, blocked=target)
    html = client.get("/en/settings/blocked/", **HOST).content.decode()
    unblock_form = next(
        f for f in _forms(html) if f"/members/{target.id}/unblock/" in f
    )
    assert 'data-confirm="Unblock Target?' in unblock_form
    assert 'data-confirm-label="Unblock"' in unblock_form
    assert "btn-crush-outline btn-sm min-h-11" in html


def test_blocked_members_empty_state_uses_ghost(client):
    _login(client)
    html = client.get("/en/settings/blocked/", **HOST).content.decode()
    assert "ghost-glow" in html
    assert "block someone from their member card" in html


def test_unblock_offers_undo_toast_once(client):
    me = _login(client)
    target = _make_user(username="target")
    UserBlock.objects.create(blocker=me, blocked=target, reason="harassment")

    resp = client.post(
        f"/en/members/{target.id}/unblock/",
        {"next": "/en/settings/blocked/"},
        **HOST,
    )
    assert resp.status_code == 302
    assert not UserBlock.objects.filter(blocker=me, blocked=target).exists()
    assert client.session[UNDO_UNBLOCK_SESSION_KEY]["user_id"] == target.id

    html = client.get("/en/settings/blocked/", **HOST).content.decode()
    assert "data-toast " in html or "data-toast\n" in html
    assert 'data-toast-message="You unblocked Target."' in html
    assert 'data-toast-action-form="undo-unblock-form"' in html
    assert f'action="/en/members/{target.id}/block/"' in html
    assert 'name="reason" value="harassment"' in html

    # The toast is raised once: a reload no longer offers Undo.
    html = client.get("/en/settings/blocked/", **HOST).content.decode()
    assert "undo-unblock-form" not in html


def test_undo_reblocks_with_original_reason(client):
    me = _login(client)
    target = _make_user(username="target")
    UserBlock.objects.create(blocker=me, blocked=target, reason="harassment")
    client.post(f"/en/members/{target.id}/unblock/", **HOST)

    # Submit exactly what the rendered Undo form carries (action + hidden inputs).
    html = client.get("/en/settings/blocked/", **HOST).content.decode()
    form = re.search(r'<form id="undo-unblock-form"[^>]*>.*?</form>', html, re.S)
    assert form, "Undo form not rendered"
    action = re.search(r'action="([^"]+)"', form.group(0)).group(1)
    data = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(0)))
    assert action == f"/en/members/{target.id}/block/"
    assert data["reason"] == "harassment"
    assert "csrfmiddlewaretoken" in data

    resp = client.post(action, data, **HOST)
    assert resp.status_code == 302
    assert resp["Location"] == "/en/settings/blocked/"
    assert UserBlock.objects.filter(
        blocker=me, blocked=target, reason="harassment"
    ).exists()


def test_undo_toast_stays_until_dismissed_and_works_without_js(client):
    me = _login(client)
    target = _make_user(username="target")
    UserBlock.objects.create(blocker=me, blocked=target)
    client.post(f"/en/members/{target.id}/unblock/", **HOST)
    html = client.get("/en/settings/blocked/", **HOST).content.decode()
    # No auto-dismiss: the only Undo must not time out (WCAG 2.2.1).
    assert 'data-toast-duration="0"' in html
    noscripts = re.findall(r"<noscript>(.*?)</noscript>", html, re.S)
    fallback = next(n for n in noscripts if "You unblocked Target." in n)
    assert 'form="undo-unblock-form"' in fallback


def test_stale_undo_payload_is_ignored(client, monkeypatch):
    me = _login(client)
    target = _make_user(username="target")
    UserBlock.objects.create(blocker=me, blocked=target)
    client.post(f"/en/members/{target.id}/unblock/", **HOST)

    later = time.time() + UNDO_UNBLOCK_MAX_AGE_SECONDS + 1
    monkeypatch.setattr("crush_lu.views_moderation.time.time", lambda: later)
    html = client.get("/en/settings/blocked/", **HOST).content.decode()
    assert "undo-unblock-form" not in html
    assert UNDO_UNBLOCK_SESSION_KEY not in client.session


def test_no_undo_toast_when_already_reblocked(client):
    me = _login(client)
    target = _make_user(username="target")
    UserBlock.objects.create(blocker=me, blocked=target)
    client.post(f"/en/members/{target.id}/unblock/", **HOST)
    UserBlock.objects.create(blocker=me, blocked=target)
    html = client.get("/en/settings/blocked/", **HOST).content.decode()
    assert "undo-unblock-form" not in html


def test_unblock_without_block_sets_no_undo(client):
    _login(client)
    target = _make_user(username="target")
    client.post(f"/en/members/{target.id}/unblock/", **HOST)
    assert UNDO_UNBLOCK_SESSION_KEY not in client.session


def test_confirm_sheet_has_a_single_plain_form_listener():
    """One submit listener handles data-confirm forms.

    WP6 and WP7 each added one; with both, a confirm form opened two sheets.
    The merged listener keeps data-confirm-when (WP6) and re-submits with the
    clicked button via requestSubmit(submitter) (WP7).
    """
    from pathlib import Path

    from django.conf import settings

    source = (
        Path(settings.BASE_DIR) / "crush_lu/static/crush_lu/js/confirm-sheet.js"
    ).read_text(encoding="utf-8")
    assert source.count('addEventListener("submit"') == 1
    assert "data-confirm-when" in source
    assert "requestSubmit(submitter)" in source


# ---------------------------------------------------------------------------
# Wave 2 follow-ups (#1058)
# ---------------------------------------------------------------------------


def test_gdpr_full_deletion_copy_is_readable_in_dark_mode(client):
    """.text-secondary is #6b7280 in both themes: on the dark:bg-red-950 box
    that is ~3.3:1, so the paragraph needs its own dark colour."""
    _login(client)
    html = client.get("/en/account/gdpr/", **HOST).content.decode()
    body = html.index("Permanently deletes your login account")
    tag = html[html.rindex("<p", 0, body) : body]
    assert "dark:text-gray-300" in tag


@pytest.mark.parametrize(
    "template",
    [
        "coach_spark_assign.html",
        "request_connection.html",
        "delete_account_confirm.html",
    ],
)
def test_confirmations_use_the_confirm_sheet_not_window_confirm(template):
    from pathlib import Path

    from django.conf import settings

    source = (
        Path(settings.BASE_DIR) / "crush_lu" / "templates" / "crush_lu" / template
    ).read_text(encoding="utf-8")
    assert "confirm(" not in source
    assert "onclick=" not in source and "onsubmit=" not in source
    assert "data-confirm=" in source


@pytest.mark.parametrize("lang", ["de", "fr"])
def test_confirm_sheet_questions_are_translated(lang):
    questions = [
        "Declare your crush? It stays completely private and cannot be undone. "
        "Your Crush Coach will call you within 48 hours to talk about it.",
        "Are you absolutely sure you want to delete your account? "
        "This cannot be undone.",
    ]
    with translation.override(lang):
        for question in questions:
            translated = translation.gettext(question)
            assert translated != question
            assert "connexion" not in translated and "Verbindung" not in translated
