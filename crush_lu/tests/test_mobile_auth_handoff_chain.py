"""End-to-end tests for the native-app auth handoff chain.

The iOS/Android shells log in through a browser sheet opened at
/api/mobile/<platform>/auth/handoff/. These tests simulate that sheet
server-side — including a mocked Google OAuth callback — and assert the flow
always ends in the crushlu:// redirect the shells wait for:

  handoff (unauthenticated) -> login page (?next=handoff) -> provider
  -> callback -> handoff -> crushlu://auth?code=...

Covered regressions:
  * SOCIALACCOUNT_LOGIN_ON_GET=False renders a confirmation page on
    GET /accounts/<provider>/login/ — it must be the styled Crush template
    and must preserve ?next= through its POST (2026-07-19: the unstyled
    allauth default stalled real users in the iOS sheet).
  * When ?next= is lost inside the sheet (signup page, language switch),
    the session flag stashed by the handoff must still route the completed
    login back to the app (2026-07-19: lost next stranded users on
    /oauth/landing/ inside the sheet).
"""

import re
import time
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialApp

from crush_lu.mobile_auth import SESSION_KEY

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("azureproject.urls_crush"),
]

User = get_user_model()

HANDOFF_PATH = "/api/mobile/ios/auth/handoff/"
GOOGLE_UID = "google-uid-123"
GOOGLE_EMAIL = "handoff-user@example.com"


@pytest.fixture
def crush_client(client, settings):
    """Test client that presents as crush.lu so the domain adapters engage."""
    settings.IOS_AUTH_REDIRECT_URIS = ["crushlu://auth"]
    settings.ANDROID_AUTH_REDIRECT_URIS = ["crushlu://auth"]
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["crush.lu"]
    client.defaults["HTTP_HOST"] = "crush.lu"
    return client


@pytest.fixture
def google_user(db):
    user = User.objects.create_user(
        username="handoff-user", email=GOOGLE_EMAIL, password="test-pass-123"
    )
    EmailAddress.objects.create(
        user=user, email=GOOGLE_EMAIL, verified=True, primary=True
    )
    SocialAccount.objects.create(
        user=user, provider="google", uid=GOOGLE_UID, extra_data={}
    )
    app = SocialApp.objects.create(
        provider="google", name="Google", client_id="test-id", secret="test-secret"
    )
    app.sites.set(Site.objects.all())
    return user


@contextmanager
def mocked_google_callback():
    """Bypass the network parts of the OAuth callback (token + profile)."""
    from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter

    def fake_get_access_token_data(self, request, app, client, **kwargs):
        return {"access_token": "tok", "expires_in": 3600, "token_type": "Bearer"}

    def fake_complete_login(self, request, app, token, response, **kwargs):
        provider = app.get_provider(request)
        return provider.sociallogin_from_response(
            request,
            {
                "sub": GOOGLE_UID,
                "email": GOOGLE_EMAIL,
                "email_verified": True,
                "given_name": "Handoff",
                "family_name": "User",
            },
        )

    with patch.object(
        GoogleOAuth2Adapter, "get_access_token_data", fake_get_access_token_data
    ), patch.object(GoogleOAuth2Adapter, "complete_login", fake_complete_login):
        yield


def _start_provider_login(client, url):
    """GET the provider login URL, POST through the confirmation page, and
    return the OAuth state id from the redirect to the provider."""
    response = client.get(url)
    if response.status_code == 200:  # SOCIALACCOUNT_LOGIN_ON_GET = False
        response = client.post(url)
    assert response.status_code == 302, response.status_code
    # Compare the parsed host exactly — a substring check would also accept
    # lookalike hosts (and CodeQL flags it as incomplete URL sanitization).
    parsed = urlparse(response.headers["Location"])
    assert parsed.netloc == "accounts.google.com", parsed.netloc
    return parse_qs(parsed.query)["state"][0]


def _finish_provider_login(client, state_id):
    with mocked_google_callback():
        return client.get(
            "/accounts/google/login/callback/",
            {"state": state_id, "code": "fake-code"},
        )


def _follow_until_scheme_redirect(client, response, max_hops=6):
    """Follow redirects until we leave http(s); return the list of hops."""
    hops = []
    for _ in range(max_hops):
        assert response.status_code == 302, response.status_code
        location = response.headers["Location"]
        hops.append(location)
        if not location.startswith("/"):
            break
        response = client.get(location)
    return hops


def test_social_login_with_next_returns_to_app(crush_client, google_user):
    # Sheet opens the handoff unauthenticated -> bounced to login with ?next=
    response = crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})
    assert response.status_code == 302
    login_url = response.headers["Location"]
    next_param = parse_qs(urlparse(login_url).query)["next"][0]
    assert next_param.startswith(HANDOFF_PATH)

    # Login page renders the provider button with next intact
    response = crush_client.get(login_url, follow=True)
    assert response.status_code == 200
    match = re.search(
        r'href="(/accounts/google/login/[^"]*)"', response.content.decode()
    )
    assert match, "google login link missing from login page"
    google_url = match.group(1).replace("&amp;", "&")
    assert "next=" in google_url

    state_id = _start_provider_login(crush_client, google_url)
    response = _finish_provider_login(crush_client, state_id)

    hops = _follow_until_scheme_redirect(crush_client, response)
    assert any(h.startswith("crushlu://auth?") for h in hops), hops

    # The shell then redeems the one-time code in the WKWebView
    final = [h for h in hops if h.startswith("crushlu://auth?")][0]
    complete_url = parse_qs(urlparse(final).query)["complete_url"][0]
    crush_client.logout()
    response = crush_client.get(urlparse(complete_url).path)
    assert response.status_code == 302
    assert response.headers["Location"] == "/en/dashboard/?source=ios_app"


def test_social_login_with_lost_next_still_returns_to_app(crush_client, google_user):
    # Sheet opens the handoff -> session flag stashed
    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})
    assert SESSION_KEY in crush_client.session

    # User wandered off the happy path: provider login WITHOUT ?next=
    state_id = _start_provider_login(crush_client, "/accounts/google/login/")
    response = _finish_provider_login(crush_client, state_id)

    hops = _follow_until_scheme_redirect(crush_client, response)
    assert any(h.startswith("crushlu://auth?") for h in hops), hops


def test_handoff_flag_cleared_after_code_issued(crush_client, google_user):
    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})
    state_id = _start_provider_login(crush_client, "/accounts/google/login/")
    response = _finish_provider_login(crush_client, state_id)
    _follow_until_scheme_redirect(crush_client, response)
    assert SESSION_KEY not in crush_client.session


def test_web_social_login_without_flag_lands_on_oauth_landing(
    crush_client, google_user
):
    """Normal website OAuth (no handoff involved) keeps its behaviour."""
    state_id = _start_provider_login(crush_client, "/accounts/google/login/")
    response = _finish_provider_login(crush_client, state_id)
    assert response.status_code == 302
    assert "/oauth/landing/" in response.headers["Location"]


def test_stale_handoff_flag_is_ignored(crush_client, google_user):
    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})
    session = crush_client.session
    data = session[SESSION_KEY]
    data["expires"] = time.time() - 1
    session[SESSION_KEY] = data
    session.save()

    state_id = _start_provider_login(crush_client, "/accounts/google/login/")
    response = _finish_provider_login(crush_client, state_id)
    assert response.status_code == 302
    assert "/oauth/landing/" in response.headers["Location"]


# --- Codex review: the handoff must survive a session it cannot rely on, and
# --- must not capture logins that were never part of the auth sheet.


def test_handoff_is_pinned_into_the_oauth_state(crush_client, google_user):
    """A provider login started in the sheet without ?next= pins the handoff
    into the (database-backed) OAuth state, so it survives paths where the
    session does not."""
    import json

    from crush_lu.models import OAuthState

    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})
    state_id = _start_provider_login(crush_client, "/accounts/google/login/")

    state_data = json.loads(OAuthState.objects.get(state_id=state_id).state_data)
    assert state_data.get("next", "").startswith(HANDOFF_PATH), state_data


def test_handoff_resumes_when_session_is_lost_entirely(crush_client, google_user):
    """The replayed-callback path: OAuthCallbackProtectionMiddleware sends the
    user to /oauth/landing/?state=..., which logs them in from the database
    against a brand-new session carrying no handoff flag. The handoff must
    still be recovered — from the OAuth state."""
    from crush_lu.models import OAuthState

    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})
    state_id = _start_provider_login(crush_client, "/accounts/google/login/")
    _finish_provider_login(crush_client, state_id)

    # Simulate the duplicate callback arriving with no usable session at all.
    OAuthState.objects.filter(state_id=state_id).update(
        auth_completed=True, auth_user_id=google_user.id
    )
    fresh = Client()
    fresh.defaults["HTTP_HOST"] = "crush.lu"
    assert SESSION_KEY not in fresh.session

    # /en/... directly: LocaleMiddleware would otherwise spend a redirect
    # adding the language prefix before the view ever runs.
    response = fresh.get("/en/oauth/landing/", {"state": state_id})

    assert response.status_code == 302, response.status_code
    assert response.headers["Location"].startswith(HANDOFF_PATH), response.headers[
        "Location"
    ]


def test_abandoned_flag_does_not_hijack_a_login_with_an_explicit_next(
    crush_client, google_user
):
    """A cancelled auth sheet leaves its flag in Safari's shared cookie jar.
    A later ordinary login that asked for a destination must keep it."""
    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})  # abandoned
    assert SESSION_KEY in crush_client.session

    response = crush_client.post(
        "/accounts/login/",
        {
            "login": GOOGLE_EMAIL,
            "password": "test-pass-123",
            "next": "/en/events/",
        },
    )

    assert response.status_code == 302, response.status_code
    assert response.headers["Location"] == "/en/events/", response.headers["Location"]


def test_abandoned_flag_is_consumed_after_one_login(crush_client, google_user):
    """A stale flag may affect at most one login, not every login for its
    whole lifetime."""
    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})  # abandoned

    # First login (no explicit next) is captured by the flag...
    response = crush_client.post(
        "/accounts/login/", {"login": GOOGLE_EMAIL, "password": "test-pass-123"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].startswith(HANDOFF_PATH)
    assert SESSION_KEY not in crush_client.session, "flag should be consumed"

    # ...and the next one is not.
    crush_client.logout()
    response = crush_client.post(
        "/accounts/login/", {"login": GOOGLE_EMAIL, "password": "test-pass-123"}
    )
    assert response.status_code == 302
    assert not response.headers["Location"].startswith(HANDOFF_PATH), response.headers[
        "Location"
    ]


def test_interstitial_is_styled_and_preserves_next(crush_client, google_user):
    """The confirmation page shown on GET (SOCIALACCOUNT_LOGIN_ON_GET=False)
    must be the Crush-branded template, not allauth's bare default, and its
    POST must keep ?next= alive in the stashed OAuth state."""
    next_value = f"{HANDOFF_PATH}?redirect_uri=crushlu%3A%2F%2Fauth"
    url = f"/accounts/google/login/?next={next_value}"

    response = crush_client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert "btn-crush-solid" in html, "not the styled Crush confirmation page"
    assert "Google" in html

    response = crush_client.post(url)
    assert response.status_code == 302
    state_id = parse_qs(urlparse(response.headers["Location"]).query)["state"][0]

    import json

    from crush_lu.models import OAuthState

    state_data = json.loads(OAuthState.objects.get(state_id=state_id).state_data)
    assert state_data.get("next", "").startswith(HANDOFF_PATH)


@pytest.mark.parametrize(
    "host,expect_crush",
    [
        ("crush.lu", True),
        ("www.crush.lu", True),
        ("test.crush.lu", True),
        # get_host() keeps the dev port, so these must not be matched with ==
        ("localhost:8000", True),
        ("crush.localhost:8000", True),
        ("127.0.0.1:8000", True),
        # Other platforms render the standalone page: their URLconfs do not
        # expose the Crush URLs that crush_lu/base.html reverses.
        ("entreprinder.lu", False),
        ("delegations.lu", False),
        ("power-up.lu", False),
        ("vinsdelux.com", False),
        ("portal.localhost:8000", False),
    ],
)
def test_interstitial_template_routes_by_host(
    client, settings, google_user, host, expect_crush
):
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + [
        "crush.lu",
        "www.crush.lu",
        "test.crush.lu",
        "crush.localhost",
        "entreprinder.lu",
        "delegations.lu",
        "power-up.lu",
        "vinsdelux.com",
        "portal.localhost",
    ]
    client.defaults["HTTP_HOST"] = host

    response = client.get("/accounts/google/login/")

    assert response.status_code == 200, response.status_code
    html = response.content.decode()
    is_crush = "btn-crush-solid" in html
    assert is_crush is expect_crush, (
        f"{host} rendered the {'Crush' if is_crush else 'neutral'} page, "
        f"expected {'Crush' if expect_crush else 'neutral'}"
    )
    # Either way it must be a real confirmation page that can POST onward.
    assert "csrfmiddlewaretoken" in html


def test_android_handoff_stashes_flag_and_redirects_to_login(crush_client):
    response = crush_client.get(
        "/api/mobile/android/auth/handoff/", {"redirect_uri": "crushlu://auth"}
    )
    assert response.status_code == 302
    assert "login" in response.headers["Location"]
    flag = crush_client.session.get(SESSION_KEY)
    assert flag and flag["platform"] == "android"


def test_handoff_rejects_unknown_redirect_uri_before_login(crush_client):
    response = crush_client.get(HANDOFF_PATH, {"redirect_uri": "https://evil.example"})
    assert response.status_code == 400
    assert SESSION_KEY not in crush_client.session
