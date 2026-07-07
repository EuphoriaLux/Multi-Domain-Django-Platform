"""Access-matrix tests for the Crush Connect BETA phase (candidate-open).

The beta (``CRUSH_CONNECT_CANDIDATE_OPEN`` on, ``CRUSH_CONNECT_LAUNCHED`` off)
opens the candidate "in the Mix" track to any verified + LuxID member, while the
Premium/receiver track (Today's Drop) stays limited to staff + selected waitlist
testers. See ``crush_lu/connect_phase.py``.

Backward-compat (both flags off = prelaunch, LAUNCHED on = full launch) is
covered by the existing ``test_crush_connect*`` suites; here we exercise the new
middle phase and the tester-gated receiver rule.
"""

import pytest

from crush_lu.connect_phase import (
    candidate_access_open,
    is_selected_beta_tester,
    receiver_access_open,
)
from crush_lu.models.crush_connect import CrushConnectWaitlist
from crush_lu.tests.test_crush_connect import (
    CONNECT_HOME_URL,
    CONNECT_TEASER_URL,
    _login_eligible,
    _make_user,
    _mark_attended,
)


def _select_tester(user):
    return CrushConnectWaitlist.objects.create(user=user, selected_as_tester=True)


# ---------------------------------------------------------------------------
# Phase helpers (pure)
# ---------------------------------------------------------------------------


def test_candidate_access_open_matrix(settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    assert candidate_access_open() is False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    assert candidate_access_open() is True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    settings.CRUSH_CONNECT_LAUNCHED = True
    assert candidate_access_open() is True


@pytest.mark.django_db
def test_receiver_access_open_beta_limited_to_testers(settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    tester = _make_user(username="t", premium=True)
    _select_tester(tester)
    plain = _make_user(username="p", premium=True)

    assert is_selected_beta_tester(tester) is True
    assert is_selected_beta_tester(plain) is False
    assert receiver_access_open(tester) is True
    assert receiver_access_open(plain) is False

    # Full launch opens the receiver track to every Premium member.
    settings.CRUSH_CONNECT_LAUNCHED = True
    assert receiver_access_open(plain) is True


# ---------------------------------------------------------------------------
# Access matrix via the Today's-Drop gate (_connect_access_blocker)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_beta_candidate_not_onboarded_routed_to_onboarding(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="cand1", premium=False, has_luxid=True, onboarded=False)
    _login_eligible(client, me)
    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (301, 302)
    assert "/onboarding/" in resp.url


@pytest.mark.django_db
def test_beta_onboarded_candidate_routed_to_catalogue(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="cand2", premium=False, has_luxid=True, onboarded=True)
    _login_eligible(client, me)
    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (301, 302)
    assert "/catalogue/" in resp.url


@pytest.mark.django_db
def test_beta_selected_tester_reaches_todays_drop(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="tester", preferred_genders=["F"])  # premium + onboarded
    _mark_attended(me)
    _select_tester(me)
    _login_eligible(client, me)
    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_beta_premium_non_tester_treated_as_candidate(client, settings):
    """The key beta rule: a Premium member who is NOT a selected tester does not
    get Today's Drop — they're routed to the candidate catalogue instead."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="prem", preferred_genders=["F"])  # premium, NOT a tester
    _mark_attended(me)
    _login_eligible(client, me)
    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (301, 302)
    assert "/catalogue/" in resp.url


@pytest.mark.django_db
def test_beta_candidate_still_requires_luxid(client, settings):
    """Candidate access still needs LuxID — a verified member without it is
    bounced to the teaser even in beta."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="nolux", premium=False, has_luxid=False, onboarded=False)
    _login_eligible(client, me)
    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (301, 302)
    assert resp.url.endswith("/crush-connect/")  # teaser


@pytest.mark.django_db
def test_prelaunch_gates_everyone_including_testers(client, settings):
    """Both flags off = unchanged prelaunch: even a selected tester hits the
    teaser (the beta flag is what opens the door)."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    me = _make_user(username="pre", preferred_genders=["F"])
    _mark_attended(me)
    _select_tester(me)
    _login_eligible(client, me)
    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (301, 302)
    assert resp.url.endswith("/crush-connect/")  # teaser


# ---------------------------------------------------------------------------
# Teaser fast-path routing during beta
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_teaser_routes_beta_tester_to_drop(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="tt", preferred_genders=["F"])
    _mark_attended(me)
    _select_tester(me)
    _login_eligible(client, me)
    resp = client.get(CONNECT_TEASER_URL)
    assert resp.status_code in (301, 302)
    assert "/today/" in resp.url


@pytest.mark.django_db
def test_teaser_routes_beta_premium_non_tester_to_catalogue(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="ptn", preferred_genders=["F"])  # premium, not a tester
    _mark_attended(me)
    _login_eligible(client, me)
    resp = client.get(CONNECT_TEASER_URL)
    assert resp.status_code in (301, 302)
    assert "/catalogue/" in resp.url


@pytest.mark.django_db
def test_teaser_routes_beta_candidate_to_onboarding(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="ct", premium=False, has_luxid=True, onboarded=False)
    _login_eligible(client, me)
    resp = client.get(CONNECT_TEASER_URL)
    assert resp.status_code in (301, 302)
    assert "/onboarding/" in resp.url
