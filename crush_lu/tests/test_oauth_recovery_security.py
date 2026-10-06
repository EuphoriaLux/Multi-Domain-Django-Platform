"""OAuth state leakage and cross-browser recovery security regressions (#1182)."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models.query import QuerySet
from django.test import Client
from django.utils import timezone

from crush_lu.models import OAuthState
from crush_lu.oauth_recovery import BROWSER_COOKIE, RECOVERY_COOKIE, RECOVERY_SECONDS
from crush_lu.tests.test_mobile_auth_handoff_chain import (
    HANDOFF_PATH,
    SESSION_KEY,
    _callback_request_without_session,
    _finish_provider_login,
    _start_provider_login,
    crush_client as crush_client,
    google_user as google_user,
)

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


def browser(token=None, origin=None):
    client = Client(HTTP_HOST="crush.lu")
    if token:
        client.cookies[RECOVERY_COOKIE] = token
    if origin:
        client.cookies[BROWSER_COOKIE] = origin
    return client


def complete(client):
    state = _start_provider_login(client, "/accounts/google/login/")
    response = _finish_provider_login(client, state)
    assert response.status_code == 302
    return state, response.cookies[RECOVERY_COOKIE].value


def assert_anonymous(client, state):
    client.get("/en/oauth/landing/", {"state": state})
    assert "_auth_user_id" not in client.session


def test_attacker_minted_state_cannot_redeem_victims_login(crush_client, google_user):
    state = _start_provider_login(crush_client, "/accounts/google/login/")
    victim = browser()
    _finish_provider_login(victim, state)
    # Same IP/UA, different browser possession: the transplanted callback
    # itself fails closed, before either browser can recover a result.
    assert "_auth_user_id" not in victim.session
    assert RECOVERY_COOKIE not in victim.cookies
    assert_anonymous(crush_client, state)
    assert_anonymous(browser(), state)
    assert not OAuthState.objects.get(pk=state).auth_completed


def test_leaked_completed_state_cannot_redeem_victims_login(crush_client, google_user):
    state, token = complete(crush_client)
    assert_anonymous(browser(), state)
    assert OAuthState.objects.get(pk=state).auth_completed


def test_only_callback_browser_can_recover_without_its_session(
    crush_client, google_user
):
    state, token = complete(crush_client)
    fresh = browser(token)
    response = fresh.get("/en/oauth/landing/", {"state": state})
    assert response.status_code == 200
    assert fresh.session["_auth_user_id"] == str(google_user.pk)
    row = OAuthState.objects.get(pk=state)
    assert not row.auth_completed
    assert row.auth_user_id is None
    assert row.auth_recovery_hash == ""
    assert response.cookies[RECOVERY_COOKIE]["max-age"] == 0
    # Retaining the old token cannot replay the login, even from a new session.
    assert_anonymous(browser(token), state)


@pytest.mark.parametrize("field", ["expires_at", "last_callback_at"])
def test_expired_state_or_recovery_window_refuses_even_valid_proof(
    crush_client, google_user, field
):
    state, token = complete(crush_client)
    OAuthState.objects.filter(pk=state).update(
        **{field: timezone.now() - timedelta(seconds=RECOVERY_SECONDS + 1)}
    )
    assert_anonymous(browser(token), state)


def test_legacy_completed_state_without_callback_proof_cannot_login(
    crush_client, google_user
):
    state, token = complete(crush_client)
    OAuthState.objects.filter(pk=state).update(auth_recovery_hash="")
    assert_anonymous(browser(token), state)


@pytest.mark.parametrize("token", ["wrong", "x" * 43, "é" * 43, "x" * 10000])
def test_wrong_or_malformed_proof_cannot_consume_real_recovery(
    crush_client, google_user, token
):
    state, real_token = complete(crush_client)
    assert_anonymous(browser(token), state)
    assert OAuthState.objects.get(pk=state).auth_completed
    fresh = browser(real_token)
    fresh.get("/en/oauth/landing/", {"state": state})
    assert fresh.session["_auth_user_id"] == str(google_user.pk)


def test_proof_is_per_flow_and_callback_result_is_not_issued_at_start(
    crush_client, google_user
):
    state, token = complete(crush_client)
    other_client = browser()
    other_state = _start_provider_login(other_client, "/accounts/google/login/")
    assert RECOVERY_COOKIE not in other_client.cookies
    assert_anonymous(browser(token), other_state)
    assert OAuthState.objects.get(pk=state).auth_completed


def test_inactive_member_cannot_recover(crush_client, google_user):
    state, token = complete(crush_client)
    get_user_model().objects.filter(pk=google_user.pk).update(is_active=False)
    assert_anonymous(browser(token), state)


def test_callback_cookie_is_host_only_secure_httponly_and_bounded(
    crush_client, google_user
):
    state, token = complete(crush_client)
    cookie = crush_client.cookies[RECOVERY_COOKIE]
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/" and cookie["domain"] == ""
    assert cookie["max-age"] == RECOVERY_SECONDS
    row = OAuthState.objects.get(pk=state)
    assert token != row.auth_recovery_hash
    assert token not in row.state_data
    assert token not in row.auth_redirect_url


def test_authenticated_landing_retires_proof_without_changing_the_user(
    crush_client, google_user
):
    state, token = complete(crush_client)
    crush_client.get("/en/oauth/landing/", {"state": state})
    assert crush_client.session["_auth_user_id"] == str(google_user.pk)
    assert_anonymous(browser(token), state)


def test_another_authenticated_user_cannot_read_handoff_metadata(
    crush_client, google_user
):
    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})
    state, token = complete(crush_client)
    other = get_user_model().objects.create_user("other", "other@example.com", "pass")
    attacker = browser()
    attacker.force_login(other)
    response = attacker.get("/en/oauth/landing/", {"state": state})
    assert response.status_code == 200
    assert attacker.session["_auth_user_id"] == str(other.pk)
    assert OAuthState.objects.get(pk=state).auth_completed
    assert SESSION_KEY not in attacker.session


def test_non_callback_login_cannot_arm_a_state(crush_client, google_user):
    from django.contrib.auth import login
    from django.test import RequestFactory

    state = _start_provider_login(crush_client, "/accounts/google/login/")
    request = RequestFactory().get("/accounts/login/", {"state": state})
    request.session = crush_client.session
    login(request, google_user, backend="django.contrib.auth.backends.ModelBackend")
    assert not OAuthState.objects.get(pk=state).auth_completed
    assert not hasattr(request, "_oauth_recovery_cookie")


def test_logout_retires_proof_left_by_native_handoff(crush_client, google_user):
    crush_client.get(HANDOFF_PATH, {"redirect_uri": "crushlu://auth"})
    state, token = complete(crush_client)
    response = crush_client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response.cookies[RECOVERY_COOKIE]["max-age"] == 0
    assert_anonymous(browser(token), state)


def test_duplicate_callback_recovers_only_with_callback_browser_proof(
    crush_client, google_user, settings
):
    # Production's replay middleware must not mint fresh proof on a replay.
    settings.MIDDLEWARE = list(settings.MIDDLEWARE)
    index = settings.MIDDLEWARE.index(
        "django.contrib.auth.middleware.AuthenticationMiddleware"
    )
    settings.MIDDLEWARE.insert(
        index + 1, "azureproject.middleware.OAuthCallbackProtectionMiddleware"
    )
    callback_client = browser()
    state, token = complete(callback_client)
    for client, expected_login in [(browser(), False), (browser(token), True)]:
        response = client.get(
            "/accounts/google/login/callback/", {"state": state, "code": "used-code"}
        )
        assert response.status_code == 302
        assert RECOVERY_COOKIE not in response.cookies
        client.get(response.headers["Location"], follow=True)
        assert ("_auth_user_id" in client.session) == expected_login
        if expected_login:
            assert client.session["_auth_user_id"] == str(google_user.pk)
    assert not OAuthState.objects.get(pk=state).auth_completed


def test_session_copy_cannot_revive_state_consumed_in_another_browser(
    crush_client, google_user
):
    from allauth.socialaccount.internal import statekit

    state = _start_provider_login(crush_client, "/accounts/google/login/")
    callback = browser(origin=crush_client.cookies[BROWSER_COOKIE].value)
    response = _finish_provider_login(callback, state)
    assert response.status_code == 302
    request = _callback_request_without_session(state)
    request.session = crush_client.session
    assert statekit.unstash_state(request, state) is None


def test_no_state_fallback_does_not_select_another_browser_by_shared_ip(
    crush_client, google_user
):
    from allauth.socialaccount.internal import statekit

    state = _start_provider_login(crush_client, "/accounts/google/login/")
    request = _callback_request_without_session(state)
    assert statekit.unstash_last_state(request) is None
    assert not OAuthState.objects.get(pk=state).used


def test_two_recovery_readers_can_only_issue_one_login(crush_client, google_user):
    from django.contrib.auth.models import AnonymousUser
    from crush_lu.oauth_recovery import recover_callback_login

    state, token = complete(crush_client)
    requests = [_callback_request_without_session(state) for _ in range(2)]
    for request in requests:
        request.COOKIES[RECOVERY_COOKIE] = token
        request.user = AnonymousUser()
    original_first = QuerySet.first
    competing_results = []
    interleaved = False

    def interleave(queryset):
        nonlocal interleaved
        row = original_first(queryset)
        if queryset.model is OAuthState and not interleaved:
            interleaved = True
            competing_results.append(recover_callback_login(requests[1], state))
        return row

    with patch.object(QuerySet, "first", interleave):
        losing_result = recover_callback_login(requests[0], state)
    assert losing_result is None
    assert competing_results[0] is not None
    assert "_auth_user_id" not in requests[0].session
    assert requests[1].session["_auth_user_id"] == str(google_user.pk)


def test_expired_oauth_states_are_purged_by_existing_scheduled_retention(db):
    expired = OAuthState.create_state({"next": "/"}, expiry_minutes=-1)
    fresh = OAuthState.create_state({"next": "/"}, expiry_minutes=10)
    out = StringIO()
    call_command("gdpr_retention_cleanup", stdout=out)
    assert "Expired OAuthState: 1 row(s)" in out.getvalue()
    assert OAuthState.objects.filter(pk=expired.pk).exists()
    call_command("gdpr_retention_cleanup", apply=True, stdout=out)
    assert not OAuthState.objects.filter(pk=expired.pk).exists()
    assert OAuthState.objects.filter(pk=fresh.pk).exists()


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_late_cookie_recovers_on_poll_and_resumes_native_handoff(
    crush_client, google_user, platform
):
    handoff = f"/api/mobile/{platform}/auth/handoff/"
    crush_client.get(handoff, {"redirect_uri": "crushlu://auth"})
    state, token = complete(crush_client)
    fresh = browser()
    # The first navigation arrives before even the recovery cookie committed.
    response = fresh.get("/en/oauth/landing/", {"state": state})
    assert response.status_code == 200
    assert "_auth_user_id" not in fresh.session
    fresh.cookies[RECOVERY_COOKIE] = token
    response = fresh.get("/api/auth/status/", {"state": state})
    assert response.json()["authenticated"]
    assert response.json()["redirect_url"].startswith(handoff)
    response = fresh.get(response.json()["redirect_url"])
    assert response.status_code == 302
    assert response.headers["Location"].startswith("crushlu://auth?")
    assert_anonymous(browser(token), state)


def test_polling_with_only_a_leaked_state_cannot_authenticate(
    crush_client, google_user
):
    state, token = complete(crush_client)
    attacker = browser()
    response = attacker.get("/api/auth/status/", {"state": state})
    assert not response.json()["authenticated"]
    assert "_auth_user_id" not in attacker.session
    assert OAuthState.objects.get(pk=state).auth_completed


def test_expired_state_in_original_session_cannot_finish_oauth(
    crush_client, google_user
):
    state = _start_provider_login(crush_client, "/accounts/google/login/")
    OAuthState.objects.filter(pk=state).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    _finish_provider_login(crush_client, state)
    assert "_auth_user_id" not in crush_client.session
    assert RECOVERY_COOKIE not in crush_client.cookies


def test_two_callback_readers_can_only_consume_state_once(crush_client, google_user):
    state = _start_provider_login(crush_client, "/accounts/google/login/")
    original_first = QuerySet.first
    competing = []
    interleaved = False

    def interleave(queryset):
        nonlocal interleaved
        row = original_first(queryset)
        if queryset.model is OAuthState and not interleaved:
            interleaved = True
            competing.append(OAuthState.get_and_consume_state(state))
        return row

    with patch.object(QuerySet, "first", interleave):
        assert OAuthState.get_and_consume_state(state) is None
    assert competing[0] is not None
    assert OAuthState.objects.get(pk=state).used


@pytest.mark.parametrize("platform", ["ios", "android"])
def test_sessionless_callback_with_browser_proof_preserves_native_handoff(
    crush_client, google_user, platform
):
    from crush_lu.tests.test_mobile_auth_handoff_chain import (
        _follow_until_scheme_redirect,
    )

    handoff = f"/api/mobile/{platform}/auth/handoff/"
    crush_client.get(handoff, {"redirect_uri": "crushlu://auth"})
    state = _start_provider_login(crush_client, "/accounts/google/login/")
    callback = browser(origin=crush_client.cookies[BROWSER_COOKIE].value)
    response = _finish_provider_login(callback, state)
    hops = _follow_until_scheme_redirect(callback, response)
    assert any(hop.startswith("crushlu://auth?") for hop in hops)
    assert callback.session["_auth_user_id"] == str(google_user.pk)


def test_wrong_browser_proof_cannot_consume_an_outgoing_state(
    crush_client, google_user
):
    state = _start_provider_login(crush_client, "/accounts/google/login/")
    wrong = browser(origin="x" * 43)
    _finish_provider_login(wrong, state)
    assert "_auth_user_id" not in wrong.session
    assert not OAuthState.objects.get(pk=state).used
    # An unrelated callback cannot burn the legitimate initiator's flow.
    _finish_provider_login(crush_client, state)
    assert crush_client.session["_auth_user_id"] == str(google_user.pk)


def test_browser_proof_is_host_only_and_supports_cross_site_post_callbacks(
    crush_client, google_user
):
    state = _start_provider_login(crush_client, "/accounts/google/login/")
    cookie = crush_client.cookies[BROWSER_COOKIE]
    assert cookie["secure"] and cookie["httponly"]
    assert cookie["path"] == "/" and cookie["domain"] == ""
    assert cookie["samesite"] == "None"
    assert cookie["max-age"] == 900
    row = OAuthState.objects.get(pk=state)
    assert row.auth_origin_hash != cookie.value
    assert cookie.value not in row.state_data
