"""Sign in to test.crush.lu with a crush.lu account (coach playground).

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md
"""

import json
import json as _json
import logging
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client

from crush_lu.models import CrushCoach, PlaygroundAccount, UserDataConsent
from crush_lu.services import playground_sso as sso

pytestmark = pytest.mark.django_db

SECRET = "s" * 48
AUTHORIZE = "/api/auth/coach-playground/authorize/"
EXCHANGE = "/api/auth/coach-playground/exchange/"
START = "/coach-playground/sso/start/"
CALLBACK = "/coach-playground/sso/callback/"
RETURN_URL = "https://test.crush.lu/coach-playground/sso/callback/"
STATE = "a" * 43
VERIFIER = "v" * 64


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def configured(settings):
    settings.COACH_PLAYGROUND_SSO_SECRET = SECRET
    settings.COACH_PLAYGROUND_SSO_ISSUER = "https://crush.lu"
    settings.DEBUG = False
    return settings


@pytest.fixture
def production(monkeypatch, configured):
    monkeypatch.delenv("DJANGO_ENV", raising=False)
    return configured


@pytest.fixture
def staging(monkeypatch, configured):
    monkeypatch.setenv("DJANGO_ENV", "staging")
    return configured


def _make_user(
    email, *, coach=False, superuser=False, active=True, verified_email=True
):
    user = User.objects.create_user(
        username=email,
        email=email,
        password="pw-12345678",
        first_name="Ana",
        last_name="Coach",
        is_superuser=superuser,
        is_active=active,
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    if verified_email:
        EmailAddress.objects.update_or_create(
            user=user, email=email, defaults={"verified": True, "primary": True}
        )
    if coach:
        CrushCoach.objects.create(user=user, is_active=True)
    return user


def _authorize_query(**overrides):
    params = {
        "return": RETURN_URL,
        "state": STATE,
        "code_challenge": sso.pkce_challenge(VERIFIER),
        "code_challenge_method": "S256",
    }
    params.update(overrides)
    return params


def _issue(client, user, **overrides):
    client.force_login(user)
    response = client.get(AUTHORIZE, _authorize_query(**overrides))
    assert response.status_code == 302, response.status_code
    query = parse_qs(urlparse(response["Location"]).query)
    return query["code"][0]


def _exchange(client, code, verifier=VERIFIER, secret=SECRET):
    # Server to server: a fresh client, never the browser's session.
    del client
    headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"} if secret else {}
    return Client(HTTP_HOST="crush.lu").post(
        EXCHANGE,
        data=json.dumps({"code": code, "code_verifier": verifier}),
        content_type="application/json",
        **headers,
    )


# ---------------------------------------------------------------------------
# Issuer gates (crush.lu, production slot)
# ---------------------------------------------------------------------------


class TestIssuerGate:
    def test_404_on_staging(self, staging):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(coach)
        assert client.get(AUTHORIZE, _authorize_query()).status_code == 404
        assert _exchange(client, "x" * 43).status_code == 404

    def test_404_under_debug(self, production):
        production.DEBUG = True
        client = Client(HTTP_HOST="crush.lu")
        assert client.get(AUTHORIZE, _authorize_query()).status_code == 404

    @pytest.mark.parametrize("secret", ["", "short-secret"])
    def test_404_without_a_long_secret(self, production, secret):
        production.COACH_PLAYGROUND_SSO_SECRET = secret
        client = Client(HTTP_HOST="crush.lu")
        assert client.get(AUTHORIZE, _authorize_query()).status_code == 404

    def test_404_on_the_test_host(self, production):
        client = Client(HTTP_HOST="test.crush.lu")
        assert client.get(AUTHORIZE, _authorize_query()).status_code == 404


class TestAuthorize:
    @pytest.mark.parametrize(
        "bad_return",
        [
            "https://test.crush.lu/coach-playground/sso/callback",
            "https://test.crush.lu/coach-playground/sso/callback/x",
            "http://test.crush.lu/coach-playground/sso/callback/",
            "https://evil.example/coach-playground/sso/callback/",
            "https://test.crush.lu.evil.example/coach-playground/sso/callback/",
            "https://user@test.crush.lu/coach-playground/sso/callback/",
            "https://hub.crush.lu/auth/callback",
            "",
        ],
    )
    def test_return_url_must_match_exactly(self, production, bad_return):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(coach)
        response = client.get(AUTHORIZE, _authorize_query(**{"return": bad_return}))
        assert response.status_code == 400

    @pytest.mark.parametrize(
        "override",
        [
            {"state": "short"},
            {"state": "a" * 40 + "!!!"},
            {"code_challenge": "c" * 42},
            {"code_challenge_method": "plain"},
        ],
    )
    def test_bad_state_or_challenge(self, production, override):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(coach)
        assert client.get(AUTHORIZE, _authorize_query(**override)).status_code == 400

    def test_anonymous_goes_to_login_without_personal_data(self, production):
        client = Client(HTTP_HOST="crush.lu")
        response = client.get(AUTHORIZE, _authorize_query())
        assert response.status_code == 302
        location = response["Location"]
        assert "next=" in location
        assert "@" not in location
        assert response["Cache-Control"] == "no-store"

    def test_coach_gets_code_and_state(self, production):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(coach)
        response = client.get(AUTHORIZE, _authorize_query())
        assert response.status_code == 302
        location = urlparse(response["Location"])
        assert (location.scheme, location.netloc, location.path) == (
            "https",
            "test.crush.lu",
            "/coach-playground/sso/callback/",
        )
        query = parse_qs(location.query)
        assert query["state"] == [STATE]
        assert sso.TOKEN_RE.match(query["code"][0])
        assert "@" not in response["Location"]
        assert response["Cache-Control"] == "no-store"
        assert response["Referrer-Policy"] == "no-referrer"


class TestExchange:
    def test_missing_or_wrong_bearer(self, production):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        code = _issue(client, coach)
        assert _exchange(client, code, secret=None).status_code == 401
        assert _exchange(client, code, secret="w" * 48).status_code == 401
        # The code survived the bad attempts and still works once.
        assert _exchange(client, code).status_code == 200

    def test_unknown_code(self, production):
        client = Client(HTTP_HOST="crush.lu")
        assert _exchange(client, "u" * 43).status_code == 401

    def test_coach_claims_and_single_use(self, production, caplog):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        code = _issue(client, coach)
        with caplog.at_level(logging.DEBUG):
            response = _exchange(client, code)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        claims = response.json()
        assert claims["sub"] == str(coach.pk)
        assert claims["role"] == "coach"
        assert claims["email"] == "c@crush.lu"
        assert claims["state"] == STATE
        assert claims["aud"] == "test.crush.lu"
        assert "c@crush.lu" not in caplog.text
        assert _exchange(client, code).status_code == 401

    def test_unverified_email_is_not_vouched_for(self, production):
        coach = _make_user("c@crush.lu", coach=True, verified_email=False)
        client = Client(HTTP_HOST="crush.lu")
        claims = _exchange(client, _issue(client, coach)).json()
        assert claims["role"] == "coach"
        assert claims["email_verified"] is False

    def test_superuser_role(self, production):
        admin = _make_user("boss@crush.lu", superuser=True)
        client = Client(HTTP_HOST="crush.lu")
        claims = _exchange(client, _issue(client, admin)).json()
        assert claims["role"] == "superuser"

    def test_pkce_mismatch_burns_the_code(self, production):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        code = _issue(client, coach)
        assert _exchange(client, code, verifier="w" * 64).status_code == 401
        assert _exchange(client, code).status_code == 401

    def test_non_coach_gets_role_none_without_email(self, production):
        member = _make_user("member@crush.lu")
        client = Client(HTTP_HOST="crush.lu")
        claims = _exchange(client, _issue(client, member)).json()
        assert claims["role"] == "none"
        assert "email" not in claims
        assert "given_name" not in claims

    def test_coach_deactivated_after_authorize(self, production):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        code = _issue(client, coach)
        CrushCoach.objects.filter(user=coach).update(is_active=False)
        assert _exchange(client, code).json()["role"] == "none"

    def test_banned_user_gets_role_none(self, production):
        coach = _make_user("c@crush.lu", coach=True)
        client = Client(HTTP_HOST="crush.lu")
        code = _issue(client, coach)
        UserDataConsent.objects.filter(user=coach).update(crushlu_banned=True)
        assert _exchange(client, code).json()["role"] == "none"


# ---------------------------------------------------------------------------
# Consumer (test.crush.lu, staging slot)
# ---------------------------------------------------------------------------


class TestConsumerGate:
    def test_404_off_staging(self, production):
        client = Client(HTTP_HOST="crush.lu")
        assert client.get(START).status_code == 404
        assert client.get(CALLBACK).status_code == 404

    def test_404_when_unconfigured(self, monkeypatch, settings):
        monkeypatch.setenv("DJANGO_ENV", "staging")
        settings.COACH_PLAYGROUND_SSO_SECRET = ""
        client = Client(HTTP_HOST="test.crush.lu")
        assert client.get(START).status_code == 404

    def test_404_with_a_plain_http_issuer(self, staging):
        staging.COACH_PLAYGROUND_SSO_ISSUER = "http://crush.lu"
        client = Client(HTTP_HOST="test.crush.lu")
        assert client.get(START).status_code == 404


def _start(client, next_url=None):
    params = {"next": next_url} if next_url is not None else {}
    response = client.get(START, params)
    assert response.status_code == 302
    location = urlparse(response["Location"])
    assert location.netloc == "crush.lu"
    assert location.path == AUTHORIZE
    query = parse_qs(location.query)
    assert query["return"] == [RETURN_URL]
    return query["state"][0]


def _claims(state, **overrides):
    claims = {
        "sub": "9001",
        "role": "coach",
        "email": "ana@crush.lu",
        "email_verified": True,
        "given_name": "Ana",
        "family_name": "Coach",
        "state": state,
        "aud": "test.crush.lu",
        "iat": 0,
    }
    claims.update(overrides)
    return claims


def _fake_response(status=200, body=None):
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = body if body is not None else {}
    return response


class TestCallback:
    def _callback(self, client, state, claims=None, status=200, code="k" * 43):
        with mock.patch("requests.post") as post:
            post.return_value = _fake_response(status, claims)
            response = client.get(CALLBACK, {"code": code, "state": state})
        return response, post

    def test_new_coach_is_created_and_signed_in(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, post = self._callback(client, state, _claims(state))
        assert response.status_code == 302
        assert response["Location"] == "/en/coach/dashboard/"

        kwargs = post.call_args.kwargs
        assert kwargs["timeout"] == sso.EXCHANGE_TIMEOUT
        assert kwargs["allow_redirects"] is False
        assert kwargs["headers"]["Authorization"] == f"Bearer {SECRET}"
        assert post.call_args.args[0] == "https://crush.lu" + EXCHANGE

        user = User.objects.get(email="ana@crush.lu")
        assert not user.has_usable_password()
        assert user.is_staff  # the coach signal grants it
        assert not user.is_superuser
        assert CrushCoach.objects.get(user=user).is_active
        assert EmailAddress.objects.get(user=user).verified
        link = PlaygroundAccount.objects.get(user=user)
        assert (link.kind, link.production_account_id) == ("coach", 9001)
        assert int(client.session["_auth_user_id"]) == user.pk
        assert sso.SIGNED_IN_AT_SESSION_KEY in client.session

    def test_second_sign_in_reuses_the_account(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        self._callback(client, state, _claims(state))
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        self._callback(client, state, _claims(state, email="ana.new@crush.lu"))
        assert User.objects.filter(email__iexact="ana.new@crush.lu").count() == 1
        assert not User.objects.filter(email="ana@crush.lu").exists()

    def test_existing_account_keeps_password_and_superuser(self, staging):
        existing = _make_user("ana@crush.lu", superuser=True)
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, _ = self._callback(client, state, _claims(state))
        assert response.status_code == 302
        existing.refresh_from_db()
        assert existing.check_password("pw-12345678")
        assert existing.is_superuser
        assert (
            PlaygroundAccount.objects.get(user=existing).production_account_id == 9001
        )

    def test_unverified_email_claim_is_refused(self, staging):
        _make_user("ana@crush.lu", superuser=True)
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, _ = self._callback(
            client, state, _claims(state, email_verified=False)
        )
        assert response.status_code == 400
        assert "_auth_user_id" not in client.session

    def test_first_landing_asks_for_consent_once(self, staging):
        # A new staging account has no Crush.lu consent yet: the coach
        # confirms it once, like any member. No redirect loop.
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        with mock.patch("requests.post") as post:
            post.return_value = _fake_response(200, _claims(state))
            response = client.get(
                CALLBACK, {"code": "k" * 43, "state": state}, follow=True
            )
        assert response.status_code == 200
        assert len(response.redirect_chain) <= 3
        assert response.request["PATH_INFO"] == "/en/consent/confirm/"

    def test_inactive_account_is_refused(self, staging):
        _make_user("ana@crush.lu", active=False)
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, _ = self._callback(client, state, _claims(state))
        assert response.status_code == 403
        assert "_auth_user_id" not in client.session

    def test_ambiguous_email_is_refused(self, staging):
        # auth_user.email is unique, so ambiguity can only come from allauth
        # address rows that point two accounts at the same email.
        first = _make_user("first@crush.lu", verified_email=False)
        second = _make_user("second@crush.lu", verified_email=False)
        EmailAddress.objects.create(user=first, email="ana@crush.lu", verified=True)
        try:
            EmailAddress.objects.create(
                user=second, email="ANA@crush.lu", verified=True
            )
        except Exception:
            pytest.skip("allauth already forbids two verified rows for one email")
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, _ = self._callback(client, state, _claims(state))
        assert response.status_code == 409

    def test_practice_account_email_is_refused(self, staging):
        practice = _make_user("ana@crush.lu")
        owner = _make_user("owner@crush.lu", coach=True)
        PlaygroundAccount.objects.create(user=practice, kind="practice", owner=owner)
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, _ = self._callback(client, state, _claims(state))
        assert response.status_code == 403

    def test_role_none_deactivates_the_linked_staging_coach(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        self._callback(client, state, _claims(state))
        user = User.objects.get(email="ana@crush.lu")

        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, _ = self._callback(
            client,
            state,
            {"sub": "9001", "role": "none", "state": state, "aud": "test.crush.lu"},
        )
        assert response.status_code == 403
        assert not CrushCoach.objects.get(user=user).is_active
        user.refresh_from_db()
        assert not user.is_staff

    def test_missing_pending_sign_in(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        response, post = self._callback(client, STATE, _claims(STATE))
        assert response.status_code == 400
        post.assert_not_called()

    def test_state_mismatch(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        _start(client)
        response, post = self._callback(client, "b" * 43, _claims("b" * 43))
        assert response.status_code == 400
        post.assert_not_called()

    def test_pending_sign_in_is_single_use(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        self._callback(client, state, _claims(state))
        client.logout()
        response, post = self._callback(client, state, _claims(state))
        assert response.status_code == 400
        post.assert_not_called()

    def test_stale_pending_sign_in(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        session = client.session
        pending = session[sso.PENDING_SESSION_KEY]
        pending["at"] -= sso.PENDING_MAX_AGE_SECONDS + 5
        session[sso.PENDING_SESSION_KEY] = pending
        session.save()
        response, post = self._callback(client, state, _claims(state))
        assert response.status_code == 400
        post.assert_not_called()

    def test_claims_state_must_match(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, _ = self._callback(client, state, _claims("z" * 43))
        assert response.status_code == 400
        assert not User.objects.filter(email="ana@crush.lu").exists()

    def test_issuer_timeout_means_no_login(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        with mock.patch("requests.post", side_effect=requests.Timeout):
            response = client.get(CALLBACK, {"code": "k" * 43, "state": state})
        assert response.status_code == 502
        assert "_auth_user_id" not in client.session

    def test_issuer_refusal_means_no_login(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client)
        response, _ = self._callback(client, state, {}, status=401)
        assert response.status_code == 400
        assert "_auth_user_id" not in client.session

    @pytest.mark.parametrize(
        "next_url", ["https://evil.example/", "//evil.example/", "javascript:x"]
    )
    def test_external_next_is_ignored(self, staging, next_url):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client, next_url)
        response, _ = self._callback(client, state, _claims(state))
        assert response["Location"] == "/en/coach/dashboard/"

    def test_relative_next_is_kept(self, staging):
        client = Client(HTTP_HOST="test.crush.lu")
        state = _start(client, "/en/coach/channel/")
        response, _ = self._callback(client, state, _claims(state))
        assert response["Location"] == "/en/coach/channel/"


def test_login_page_shows_the_button_only_when_enabled(staging):
    client = Client(HTTP_HOST="test.crush.lu")
    assert START.encode() in client.get("/en/login/").content


def test_login_page_has_no_button_in_production(production):
    client = Client(HTTP_HOST="crush.lu")
    assert START.encode() not in client.get("/en/login/").content


def test_round_trip_through_the_real_issuer(monkeypatch, configured):
    """Staging start → crush.lu authorize → staging callback → crush.lu exchange."""
    prod_coach = _make_user("real.coach@crush.lu", coach=True)
    monkeypatch.setenv("DJANGO_ENV", "staging")
    staging_client = Client(HTTP_HOST="test.crush.lu")
    state = _start(staging_client)

    # crush.lu (production slot) hands out the code.
    monkeypatch.delenv("DJANGO_ENV")
    prod_client = Client(HTTP_HOST="crush.lu")
    prod_client.force_login(prod_coach)
    verifier = staging_client.session[sso.PENDING_SESSION_KEY]["verifier"]
    authorize = prod_client.get(
        AUTHORIZE,
        _authorize_query(state=state, code_challenge=sso.pkce_challenge(verifier)),
    )
    code = parse_qs(urlparse(authorize["Location"]).query)["code"][0]

    def fake_post(url, json=None, headers=None, timeout=None, allow_redirects=None):
        monkeypatch.delenv("DJANGO_ENV")
        try:
            issuer = Client(HTTP_HOST="crush.lu")
            response = issuer.post(
                EXCHANGE,
                data=_json.dumps(json),
                content_type="application/json",
                HTTP_AUTHORIZATION=headers["Authorization"],
            )
        finally:
            monkeypatch.setenv("DJANGO_ENV", "staging")
        return _fake_response(response.status_code, response.json())

    monkeypatch.setenv("DJANGO_ENV", "staging")
    with mock.patch("requests.post", side_effect=fake_post):
        response = staging_client.get(CALLBACK, {"code": code, "state": state})
    assert response.status_code == 302
    # Same database in tests: the existing account is matched, linked, signed in.
    link = PlaygroundAccount.objects.get(production_account_id=prod_coach.pk)
    assert link.user_id == prod_coach.pk
    assert int(staging_client.session["_auth_user_id"]) == prod_coach.pk
