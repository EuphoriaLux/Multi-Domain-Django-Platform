"""Current Crush Connect hub tests."""

import pytest

from crush_lu.models import CrushConnectWaitlist
from crush_lu.services.crush_connect import propose_coach_pick
from crush_lu.tests.test_crush_connect import (
    HUB_URL,
    _coach_for,
    _login_eligible,
    _make_user,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")


@pytest.mark.django_db
def test_flag_off_redirects_nonstaff_to_teaser(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    response = client.get(HUB_URL)

    assert response.status_code == 302
    assert "/crush-connect/" in response.url


@pytest.mark.django_db
def test_onboarded_member_sees_catalogue_and_connect_week_without_legacy_tiles(
    client, settings
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    response = client.get(HUB_URL)
    body = response.content.decode()

    assert response.status_code == 200
    assert "Your Connect Week" in body
    assert "anonymous totals" in body
    assert "Today's Drop" not in body
    assert "Your Sparks" not in body


@pytest.mark.django_db
def test_unonboarded_eligible_member_gets_preparation_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", premium=False, onboarded=False)
    _login_eligible(client, member)

    response = client.get(HUB_URL)

    assert response.status_code == 200
    assert "Complete your setup" in response.content.decode()


@pytest.mark.django_db
def test_beta_selected_tester_gets_connect_week_tile(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(username="member", premium=False)
    CrushConnectWaitlist.objects.create(user=member, selected_as_tester=True)
    _login_eligible(client, member)

    response = client.get(HUB_URL)

    assert response.status_code == 200
    assert "Your Connect Week" in response.content.decode()


@pytest.mark.django_db
def test_premium_badge_and_named_coach_are_visible(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member")
    _login_eligible(client, member)

    body = client.get(HUB_URL).content.decode()

    assert "Premium member" in body
    assert member.crushprofile.assigned_coach.user.username in body


@pytest.mark.django_db
def test_open_coach_pick_is_linked_from_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    candidate = _make_user(username="candidate", gender="F", premium=False)
    propose_coach_pick(_coach_for(member), member, candidate)
    _login_eligible(client, member)

    body = client.get(HUB_URL).content.decode()

    assert "Your coach picked a match" in body
    assert "/crush-connect/coach-pick/" in body
