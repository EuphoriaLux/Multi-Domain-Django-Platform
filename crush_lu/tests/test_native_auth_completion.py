"""Redeeming a native-app auth code: the four outcomes and who gets which.

The endpoint used to answer every refusal with the same opaque 400, including
the very common case where the caller was already signed in as the code's own
user and the only thing that had gone wrong was the app delivering its
callback twice. These tests pin the distinction.
"""

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from crush_lu.models import IOSNativeAuthCode
from crush_lu.models.ios_app import (
    AUTH_CODE_EXPIRED,
    AUTH_CODE_OK,
    AUTH_CODE_REPLAYED,
    AUTH_CODE_UNKNOWN,
)

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("azureproject.urls_crush"),
]

User = get_user_model()

PLATFORMS = [
    ("ios", "IOS_AUTH_REDIRECT_URIS", "/en/dashboard/?source=ios_app"),
    ("android", "ANDROID_AUTH_REDIRECT_URIS", "/en/dashboard/?source=android_app"),
]


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="native-auth@example.com",
        email="native-auth@example.com",
        password="test-pass-123",
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        username="native-auth-other@example.com",
        email="native-auth-other@example.com",
        password="test-pass-123",
    )


def _issue(client, settings, platform, setting_name):
    """Run the handoff and return the code the app would have received."""
    setattr(settings, setting_name, ["crushlu://auth"])
    response = client.get(
        f"/api/mobile/{platform}/auth/handoff/",
        {"redirect_uri": "crushlu://auth"},
    )
    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["Location"]).query)["code"][0]


def _complete_path(platform, code):
    return f"/api/mobile/{platform}/auth/complete/{code}/"


# --------------------------------------------------------------------------
# redeem(): the reason codes
# --------------------------------------------------------------------------


def test_redeem_reports_ok_then_replayed(user):
    code = IOSNativeAuthCode.issue(user, "crushlu://auth")

    first = IOSNativeAuthCode.redeem(code)
    assert first.user == user
    assert first.reason == AUTH_CODE_OK

    second = IOSNativeAuthCode.redeem(code)
    assert second.user is None
    assert second.reason == AUTH_CODE_REPLAYED
    # The record comes back so the caller can check whose code it was.
    assert second.record.user_id == user.pk


def test_redeem_reports_expired_separately_from_replayed(user):
    code = IOSNativeAuthCode.issue(user, "crushlu://auth")
    IOSNativeAuthCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    result = IOSNativeAuthCode.redeem(code)

    assert result.user is None
    assert result.reason == AUTH_CODE_EXPIRED
    # An expiry must not burn the row as if it had been used.
    assert result.record.consumed_at is None


def test_redeem_reports_unknown_for_a_code_that_never_existed(db):
    result = IOSNativeAuthCode.redeem("no-such-code")

    assert result.user is None
    assert result.reason == AUTH_CODE_UNKNOWN
    assert result.record is None


def test_redeem_claims_the_row_atomically(user):
    """The claim is a conditional UPDATE, so only the first caller wins.

    SQLite ignores row locks, so this cannot exercise true concurrency — what
    it does pin is that the guard lives in the UPDATE's WHERE clause and not
    only in a preceding SELECT, which is what made the old read-then-write
    able to hand the same code to two requests.
    """
    code = IOSNativeAuthCode.issue(user, "crushlu://auth")

    winners = [IOSNativeAuthCode.redeem(code) for _ in range(5)]

    assert [r.reason for r in winners] == [
        AUTH_CODE_OK,
        AUTH_CODE_REPLAYED,
        AUTH_CODE_REPLAYED,
        AUTH_CODE_REPLAYED,
        AUTH_CODE_REPLAYED,
    ]
    assert sum(1 for r in winners if r.user is not None) == 1


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


@pytest.mark.parametrize("platform,setting_name,landing", PLATFORMS)
def test_replay_by_the_codes_own_session_lands_on_the_dashboard(
    client, settings, user, platform, setting_name, landing
):
    client.force_login(user)
    code = _issue(client, settings, platform, setting_name)
    path = _complete_path(platform, code)

    assert client.get(path).status_code == 302

    replay = client.get(path)

    assert replay.status_code == 302
    assert replay.headers["Location"] == landing


@pytest.mark.parametrize("platform,setting_name,landing", PLATFORMS)
def test_replay_survives_an_old_consumption(
    client, settings, user, platform, setting_name, landing
):
    """Android carries the callback intent for the lifetime of the task.

    An activity recreation — an automatic dark-theme flip, a locale change —
    re-fires the saved intent hours after the login it belonged to. The
    redirect is not honouring the stale code, it is declining to break a
    session that is already valid, so age is irrelevant.
    """
    client.force_login(user)
    code = _issue(client, settings, platform, setting_name)
    path = _complete_path(platform, code)
    assert client.get(path).status_code == 302

    IOSNativeAuthCode.objects.update(consumed_at=timezone.now() - timedelta(hours=9))

    replay = client.get(path)

    assert replay.status_code == 302
    assert replay.headers["Location"] == landing


@pytest.mark.parametrize("platform,setting_name,landing", PLATFORMS)
def test_replay_by_an_anonymous_caller_is_refused(
    client, settings, user, platform, setting_name, landing
):
    client.force_login(user)
    code = _issue(client, settings, platform, setting_name)
    path = _complete_path(platform, code)
    assert client.get(path).status_code == 302

    client.logout()

    assert client.get(path).status_code == 400


@pytest.mark.parametrize("platform,setting_name,landing", PLATFORMS)
def test_replay_by_a_different_user_is_refused(
    client, settings, user, other_user, platform, setting_name, landing
):
    """The identity check is the security boundary, not the clock."""
    client.force_login(user)
    code = _issue(client, settings, platform, setting_name)
    path = _complete_path(platform, code)
    assert client.get(path).status_code == 302

    client.force_login(other_user)

    response = client.get(path)

    assert response.status_code == 400
    assert client.session["_auth_user_id"] == str(other_user.pk)


@pytest.mark.parametrize("platform,setting_name,landing", PLATFORMS)
def test_expired_code_is_refused_even_for_the_right_user(
    client, settings, user, platform, setting_name, landing
):
    """An expiry is not a replay: nothing minted a session, so there is none
    to preserve, and short-circuiting here would hand out a login on a code
    that was never redeemed."""
    client.force_login(user)
    code = _issue(client, settings, platform, setting_name)
    IOSNativeAuthCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

    response = client.get(_complete_path(platform, code))

    assert response.status_code == 400


@pytest.mark.parametrize("platform,setting_name,landing", PLATFORMS)
def test_failure_renders_html_with_a_retry_link_for_a_browser(
    client, settings, user, platform, setting_name, landing
):
    setattr(settings, setting_name, ["crushlu://auth"])

    response = client.get(
        _complete_path(platform, "no-such-code"),
        headers={"accept": "text/html,application/xhtml+xml"},
    )

    assert response.status_code == 400
    body = response.content.decode()
    # The retry must restart the handoff, never re-request the spent code —
    # re-requesting it is exactly what pull-to-refresh on the old JSON page did.
    assert (
        f'href="/api/mobile/{platform}/auth/handoff/'
        "?redirect_uri=crushlu%3A%2F%2Fauth\"" in body
    )
    # And the code must not be echoed back into the markup at all: base.html
    # would otherwise put the request path in rel=canonical and hreflang.
    assert "no-such-code" not in body


def test_retry_keeps_the_flavor_that_started_the_handoff(client, settings):
    """A local-build retry must not aim at the production callback scheme.

    Local settings allow crushlu://auth first and append crushlulocal://auth,
    so falling back to the first allowed URI would restart the handoff on a
    scheme the local app cannot receive.
    """
    settings.ANDROID_AUTH_REDIRECT_URIS = ["crushlu://auth", "crushlulocal://auth"]

    response = client.get(
        _complete_path("android", "no-such-code"),
        {"redirect_uri": "crushlulocal://auth"},
        headers={"accept": "text/html"},
    )

    assert "redirect_uri=crushlulocal%3A%2F%2Fauth" in response.content.decode()


def test_retry_ignores_a_redirect_uri_that_is_not_allowlisted(client, settings):
    settings.ANDROID_AUTH_REDIRECT_URIS = ["crushlu://auth"]

    response = client.get(
        _complete_path("android", "no-such-code"),
        {"redirect_uri": "evil://auth"},
        headers={"accept": "text/html"},
    )

    body = response.content.decode()
    assert "evil" not in body
    assert "redirect_uri=crushlu%3A%2F%2Fauth" in body


def test_retry_prefers_the_uri_the_code_was_issued_for(client, settings, user):
    """A row that still exists outranks the query string."""
    settings.ANDROID_AUTH_REDIRECT_URIS = ["crushlu://auth", "crushlustaging://auth"]
    code = IOSNativeAuthCode.issue(user, "crushlustaging://auth")
    IOSNativeAuthCode.redeem(code)  # spend it, so the retry page is what renders

    response = client.get(
        _complete_path("android", code),
        {"redirect_uri": "crushlu://auth"},
        headers={"accept": "text/html"},
    )

    assert "redirect_uri=crushlustaging%3A%2F%2Fauth" in response.content.decode()


@pytest.mark.parametrize("platform,setting_name,landing", PLATFORMS)
def test_failure_keeps_its_json_body_for_api_callers(
    client, settings, user, platform, setting_name, landing
):
    response = client.get(
        _complete_path(platform, "no-such-code"),
        headers={"accept": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "error": "Invalid or expired authentication code",
    }


@pytest.mark.parametrize("language", ["de", "fr"])
def test_failure_page_is_translated(language):
    """EN/DE/FR is site-wide; a recovery screen is a bad place to drop to English.

    Catches both halves of the usual failure: a msgid missing from the PO, and
    a .mo that was never recompiled after the PO was edited.
    """
    from django.template.loader import render_to_string
    from django.utils import translation

    with translation.override(language):
        html = render_to_string(
            "crush_lu/native_auth_failed.html",
            {"retry_url": "/api/mobile/ios/auth/handoff/", "expired": False},
        )

    for english in (
        "Sign-in didn't complete",
        "Let's try that sign-in again",
        "Try signing in again",
        "Back to Crush.lu",
        "This sign-in link has already been used",
    ):
        assert english not in html, f"{english!r} fell back to English in {language}"


@pytest.mark.parametrize("platform,setting_name,landing", PLATFORMS)
def test_refusals_are_logged_with_a_distinguishable_reason(
    client, settings, user, caplog, platform, setting_name, landing
):
    """Before this, all three causes arrived as the same silent None and no
    amount of production telemetry could separate them."""
    with caplog.at_level("WARNING", logger="crush_lu.native_auth"):
        client.get(_complete_path(platform, "no-such-code"))

    assert f"reason={AUTH_CODE_UNKNOWN}" in caplog.text
