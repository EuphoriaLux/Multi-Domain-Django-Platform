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
    CATALOGUE_STATUS_URL,
    CONNECT_HOME_URL,
    CONNECT_TEASER_URL,
    _login_eligible,
    _make_user,
    _mark_attended,
    _surface_in_drop,
)

# crush_connect_hub is served at /crush-connect/home/ (the shared landing for
# both tracks); Today's Drop lives at /crush-connect/today/ (CONNECT_HOME_URL).
HUB_URL = "/en/crush-connect/home/"
# Literal paths: reverse() resolves against the fallback urlconf here, since the
# crush.lu urlconf is only bound to the request by DomainURLRoutingMiddleware.
PREMIUM_COACHES_URL = "/en/premium/coaches/"


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


# ---------------------------------------------------------------------------
# Regressions from the Codex review of the beta-phase PR
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_beta_premium_non_tester_has_no_drop_catalogue_loop(client, settings):
    """P1: a Premium non-tester must land on the catalogue and STAY — /today/
    routes to /catalogue/, and /catalogue/ must not bounce back to /today/."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="prem", preferred_genders=["F"])  # premium, not a tester
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (301, 302)
    assert "/catalogue/" in resp.url

    # The catalogue renders instead of bouncing back — the loop is broken.
    resp = client.get(CATALOGUE_STATUS_URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_beta_hub_classifies_non_tester_as_candidate(client, settings):
    """P2: the shared hub must not show receiver UI (Drop card / coach pick) to a
    beta Premium non-tester; a selected tester still gets the receiver track."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="prem", preferred_genders=["F"])
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["is_receiver"] is False
    assert resp.context["track"] == "candidate"

    _select_tester(me)
    resp = client.get(HUB_URL)
    assert resp.context["is_receiver"] is True
    assert resp.context["track"] == "receiver"


@pytest.mark.django_db
def test_beta_nav_visible_for_onboarded_candidate(settings):
    """P2: onboarded candidates keep the persistent Connect nav during beta, not
    just selected testers."""
    from crush_lu.templatetags.crush_connect_tags import crush_connect_nav_visible

    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    candidate = _make_user(username="cand", premium=False, onboarded=True)
    assert crush_connect_nav_visible(candidate) is True
    not_onboarded = _make_user(username="no", premium=False, onboarded=False)
    assert crush_connect_nav_visible(not_onboarded) is False


@pytest.mark.django_db
def test_beta_catalogue_carries_the_waitlist_join(client, settings):
    """The teaser fast-path redirects every approved + LuxID member away during
    beta, so the catalogue page must carry the waitlist join — otherwise nobody
    can join the list that `receiver_access_open` reads."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="cand3", premium=False, has_luxid=True, onboarded=True)
    _login_eligible(client, me)

    resp = client.get(CATALOGUE_STATUS_URL)
    assert resp.status_code == 200
    assert "Join the Waitlist" in resp.content.decode()
    assert resp.context["on_waitlist"] is False


@pytest.mark.django_db
def test_beta_go_premium_reaches_the_waitlist_not_a_loop(client, settings):
    """Regression: Go-Premium used to loop premium → teaser → catalogue with no
    waitlist anywhere. The chain must now end on a page offering the join."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.PREMIUM_REDIRECTS_TO_BETA = True
    me = _make_user(username="cand4", premium=False, has_luxid=True, onboarded=True)
    _login_eligible(client, me)

    resp = client.get(PREMIUM_COACHES_URL, follow=True)
    assert resp.status_code == 200
    assert "Join the Waitlist" in resp.content.decode()


@pytest.mark.django_db
def test_beta_catalogue_keeps_tester_selection_silent(client, settings):
    """A selected tester sees the ordinary waitlist position, never their
    internal selection status (same rule as the teaser)."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="cand5", premium=False, has_luxid=True, onboarded=True)
    _select_tester(me)
    _login_eligible(client, me)

    resp = client.get(CATALOGUE_STATUS_URL)
    body = resp.content.decode()
    assert resp.context["on_waitlist"] is True
    assert "on the waitlist" in body.lower()
    assert "beta tester" not in body.lower()


@pytest.mark.django_db
def test_launched_catalogue_restores_the_coach_directory_cta(client, settings):
    """Once LAUNCHED, Premium is self-serve again: the coach directory link
    comes back and the waitlist disappears."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="cand6", premium=False, has_luxid=True, onboarded=True)
    _login_eligible(client, me)

    resp = client.get(CATALOGUE_STATUS_URL)
    body = resp.content.decode()
    assert resp.context["connect_launched"] is True
    assert "Discover Premium" in body
    assert "Join the Waitlist" not in body


@pytest.mark.django_db
def test_beta_spark_compose_blocked_for_non_tester(client, settings):
    """P2: a beta Premium non-tester holding a stale Drop snapshot cannot open the
    Spark compose / first-mover send flow."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="prem", preferred_genders=["F"])  # premium, not a tester
    _mark_attended(me)
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)  # stale snapshot from an old Drop
    _login_eligible(client, me)

    resp = client.get(f"/en/crush-connect/spark/{her.pk}/")
    assert resp.status_code in (301, 302)
    assert "/catalogue/" in resp.url
