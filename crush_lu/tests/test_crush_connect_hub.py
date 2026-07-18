"""
Tests for the Crush Connect hub (homepage) and its dedicated navigation.

The hub is the shared landing for both onboarded tracks; the navbar "Crush
Connect" entry and the mobile bottom-nav "Connect" tab point here. View tests
use ``/en/crush-connect/…`` URLs which only resolve under ``urls_crush``.
"""

import pytest
from django.urls import reverse

pytestmark = pytest.mark.urls("azureproject.urls_crush")

from crush_lu.models import CuriositySpark  # noqa: E402
from crush_lu.tests.test_crush_connect import (  # noqa: E402
    _login_eligible,
    _make_user,
)

HUB_URL = "/en/crush-connect/home/"


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_flag_off_redirects_to_teaser(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    me = _make_user(username="me", onboarded=True)
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code in (301, 302)
    assert resp.url.endswith("/crush-connect/")


@pytest.mark.django_db
def test_not_eligible_redirects_to_teaser(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", onboarded=False, premium=False, has_luxid=False
    )
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code in (301, 302)
    assert resp.url.endswith("/crush-connect/")


@pytest.mark.django_db
def test_eligible_not_onboarded_redirects_to_onboarding(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", onboarded=False, premium=False, has_luxid=True
    )
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code in (301, 302)
    assert "/crush-connect/onboarding/" in resp.url


@pytest.mark.django_db
def test_excluded_redirects_to_teaser(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, excluded_by_coach=True)
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code in (301, 302)
    assert resp.url.endswith("/crush-connect/")


# ---------------------------------------------------------------------------
# Rendering per track
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_onboarded_receiver_sees_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["track"] == "receiver"
    assert resp.context["is_receiver"] is True
    body = resp.content.decode()
    # Hero card links to Today's Drop, and the sub-nav links back to the hub.
    assert reverse("crush_lu:crush_connect_home") in body
    assert reverse("crush_lu:crush_connect_hub") in body
    assert reverse("crush_lu:crush_connect_sparks_received") in body


@pytest.mark.django_db
def test_onboarded_candidate_sees_catalogue_card(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", onboarded=True, premium=False, has_luxid=True
    )
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["track"] == "candidate"
    assert resp.context["is_receiver"] is False
    # Candidate hero links to the catalogue status, not Today's Drop.
    assert reverse("crush_lu:crush_connect_catalogue_status") in resp.content.decode()


@pytest.mark.django_db
def test_people_ive_met_link_hidden_when_event_lobby_rollout_is_off(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CRUSH_EVENT_LOBBY_ENABLED = False
    me = _make_user(username="me", onboarded=True, premium=True)
    _login_eligible(client, me)

    resp = client.get(HUB_URL)

    assert resp.status_code == 200
    assert resp.context["event_lobby_enabled"] is False
    assert resp.context["people_ive_met_count"] == 0
    assert reverse("crush_lu:event_lobby_people") not in resp.content.decode()


@pytest.mark.django_db
def test_hub_surfaces_pending_sparks(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)
    sender = _make_user(username="sender", gender="F", onboarded=True, premium=True)
    CuriositySpark.objects.create(sender=sender, recipient=me, status="pending")
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["pending_sparks_count"] == 1


# ---------------------------------------------------------------------------
# Shared shell: member-facing pages render the Connect sub-nav
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_member_page_renders_connect_subnav(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)
    _login_eligible(client, me)
    # Today's Drop now extends the Connect shell → its sub-nav links to the hub.
    resp = client.get(reverse("crush_lu:crush_connect_home"))
    assert resp.status_code == 200
    assert reverse("crush_lu:crush_connect_hub") in resp.content.decode()
