"""The statekit patch under both allauth state APIs (#1318).

allauth 65.20 binds every stashed OAuth state to the provider that stashed it:
the statekit functions became keyword-only, take ``provider_id`` and, when
unstashing, ``raises=``. The patch in ``crush_lu.oauth_statekit`` must keep
working under the older positional API too, and its database fallback must
not hand a state to a different provider than the one that stashed it.
"""

import inspect

import pytest
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from crush_lu.models import OAuthState
from crush_lu.oauth_recovery import BROWSER_COOKIE
from crush_lu.tests.test_mobile_auth_handoff_chain import (
    _callback_request_without_session,
    _start_provider_login,
    crush_client as crush_client,
    google_user as google_user,
)

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


def _statekit():
    from allauth.socialaccount.internal import statekit

    from crush_lu.oauth_statekit import ensure_patched

    ensure_patched()
    return statekit


def _allauth_binds_provider():
    from allauth.socialaccount.internal import statekit

    # get_oldest_state is not patched, so its signature is allauth's own.
    return "provider_id" in inspect.signature(statekit.get_oldest_state).parameters


binding_only = pytest.mark.skipif(
    not _allauth_binds_provider(),
    reason="this allauth does not bind OAuth states to a provider",
)


def _lost_session_callback(client, state):
    return _callback_request_without_session(
        state, client.cookies[BROWSER_COOKIE].value
    )


def test_database_fallback_refuses_another_providers_state(crush_client, google_user):
    statekit = _statekit()
    state = _start_provider_login(crush_client, "/accounts/google/login/")

    request = _lost_session_callback(crush_client, state)
    assert (
        statekit.unstash_state(request, state_id=state, provider_id="facebook") is None
    )
    # Refused, not burnt: the real provider's callback still succeeds.
    assert not OAuthState.objects.get(pk=state).used

    request = _lost_session_callback(crush_client, state)
    assert statekit.unstash_state(request, state_id=state, provider_id="google")
    assert OAuthState.objects.get(pk=state).used


def test_raises_does_not_skip_the_database_fallback(crush_client, google_user):
    statekit = _statekit()
    state = _start_provider_login(crush_client, "/accounts/google/login/")

    request = _lost_session_callback(crush_client, state)
    assert statekit.unstash_state(
        request, state_id=state, provider_id="google", raises=True
    )


def test_raises_when_no_state_is_found(crush_client, google_user):
    statekit = _statekit()
    request = _callback_request_without_session("missing-state")

    assert (
        statekit.unstash_state(request, state_id="missing-state", provider_id="google")
        is None
    )
    with pytest.raises(PermissionDenied):
        statekit.unstash_state(
            request, state_id="missing-state", provider_id="google", raises=True
        )
    with pytest.raises(PermissionDenied):
        statekit.unstash_last_state(request, provider_id="google", raises=True)


def test_stash_records_the_provider_it_is_given(db):
    from django.contrib.sessions.backends.db import SessionStore

    statekit = _statekit()
    # An OIDC login path names "oidc" in the segment the old fallback reads.
    request = RequestFactory().get("/accounts/oidc/linkedin/login/")
    request.session = SessionStore()

    state_id = statekit.stash_state(
        request, provider_id="linkedin", state={"process": "login"}
    )

    assert OAuthState.objects.get(pk=state_id).provider == "linkedin"


@binding_only
def test_session_copy_refuses_another_providers_state(crush_client, google_user):
    statekit = _statekit()
    state = _start_provider_login(crush_client, "/accounts/google/login/")

    request = _callback_request_without_session(state)
    request.session = crush_client.session
    assert (
        statekit.unstash_state(request, state_id=state, provider_id="facebook") is None
    )
    assert not OAuthState.objects.get(pk=state).used


@binding_only
def test_missing_provider_is_an_error_not_a_wildcard(crush_client, google_user):
    statekit = _statekit()
    state = _start_provider_login(crush_client, "/accounts/google/login/")
    request = _lost_session_callback(crush_client, state)

    with pytest.raises(TypeError):
        statekit.unstash_state(request, state_id=state)
    with pytest.raises(TypeError):
        statekit.unstash_last_state(request)
    with pytest.raises(TypeError):
        statekit.stash_state(request, state={"process": "login"})
    assert not OAuthState.objects.get(pk=state).used
