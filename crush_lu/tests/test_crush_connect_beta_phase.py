"""Crush Connect beta access after retirement of the receiver/Drop track."""

import pytest

from crush_lu.connect_phase import candidate_access_open, cycle_access_open
from crush_lu.models import CrushConnectWaitlist
from crush_lu.tests.test_crush_connect import (
    CATALOGUE_STATUS_URL,
    CONNECT_TEASER_URL,
    _login_eligible,
    _make_user,
    _mark_attended,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")

ONBOARDING_URL = "/en/crush-connect/onboarding/"
WEEK_URL = "/en/crush-connect/week/"


@pytest.mark.django_db
def test_candidate_access_open_matrix(settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    assert not candidate_access_open()

    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    assert candidate_access_open()

    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    settings.CRUSH_CONNECT_LAUNCHED = True
    assert candidate_access_open()


@pytest.mark.django_db
def test_cycle_beta_access_selected_tester_or_event_verified(settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    tester = _make_user(username="tester", premium=False)
    plain = _make_user(username="plain", premium=False)
    attendee = _make_user(username="attendee", premium=False, has_luxid=False)
    _mark_attended(attendee)
    CrushConnectWaitlist.objects.create(user=tester, selected_as_tester=True)

    assert cycle_access_open(tester)
    assert cycle_access_open(attendee)
    assert not cycle_access_open(plain)


@pytest.mark.django_db
def test_beta_candidate_not_onboarded_routed_to_onboarding(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(username="member", premium=False, onboarded=False)
    _login_eligible(client, member)

    response = client.get("/en/crush-connect/home/")

    assert response.status_code == 200
    assert "Complete your setup" in response.content.decode()


@pytest.mark.django_db
def test_beta_onboarded_luxid_member_sees_catalogue(settings, client):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    response = client.get(CONNECT_TEASER_URL)

    assert response.status_code == 302
    assert CATALOGUE_STATUS_URL in response.url


@pytest.mark.django_db
def test_beta_selected_tester_teaser_routes_to_connect_week(settings, client):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(username="member", premium=False)
    CrushConnectWaitlist.objects.create(user=member, selected_as_tester=True)
    _login_eligible(client, member)

    response = client.get(CONNECT_TEASER_URL)

    assert response.status_code == 302
    assert WEEK_URL in response.url


@pytest.mark.django_db
def test_prelaunch_keeps_nonstaff_on_teaser(settings, client):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    response = client.get(CONNECT_TEASER_URL)

    assert response.status_code == 200


@pytest.mark.django_db
def test_beta_catalogue_carries_waitlist_join(settings, client):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    response = client.get(CATALOGUE_STATUS_URL)

    assert response.status_code == 200
    assert "waitlist" in response.content.decode().lower()
