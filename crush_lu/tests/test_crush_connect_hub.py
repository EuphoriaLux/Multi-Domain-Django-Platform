"""
Tests for the Crush Connect hub (homepage) and its dedicated navigation.

The hub is the shared landing for both onboarded tracks; the navbar "Crush
Connect" entry and the mobile bottom-nav "My Crush" tab point here. View tests
use ``/en/crush-connect/…`` URLs which only resolve under ``urls_crush``.
"""

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

pytestmark = pytest.mark.urls("azureproject.urls_crush")

from crush_lu.models import CuriositySpark, PremiumMembership  # noqa: E402
from crush_lu.templatetags.crush_connect_tags import (  # noqa: E402
    crush_connect_is_receiver,
)
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
def test_staff_without_onboarded_membership_sees_connect_week_preview_link(
    client, settings
):
    """cycle_access requires onboarding even for staff (mirrors is_receiver),
    but _connect_week_access_blocker fully bypasses onboarding for staff
    (mirrors _connect_access_blocker) — so an unonboarded staff account has
    no UI path into Connect Week without this preview link, exactly the gap
    the existing Today's Drop staff-preview link already covers for the
    receiver track."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    staffer = _make_user(username="staffer", onboarded=False)
    staffer.is_staff = True
    staffer.save(update_fields=["is_staff"])
    _login_eligible(client, staffer)

    resp = client.get(HUB_URL)

    assert resp.status_code == 200
    assert resp.context["cycle_access"] is False
    assert resp.context["cycle_staff_preview"] is True
    body = resp.content.decode()
    assert reverse("crush_lu:connect_week_home") in body
    assert "Staff preview: open Connect Week" in body


# ---------------------------------------------------------------------------
# Premium status is visible, and follows the ENTITLEMENT not the receiver gate
#
# A member who paid had no indication anywhere that they were Premium: after
# checkout the hub looked identical apart from one line of hero copy.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_premium_member_sees_premium_badge_and_their_coach(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)
    # The shared _get_coach() fixture leaves first_name blank, so asserting
    # `coach.user.first_name in body` would be `"" in body` — true of every
    # page ever rendered. Give the coach a name so the assertion has teeth.
    coach_user = me.crushprofile.assigned_coach.user
    coach_user.first_name = "Nadia"
    coach_user.save(update_fields=["first_name"])
    _login_eligible(client, me)

    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["has_premium"] is True
    body = resp.content.decode()
    assert "Premium member" in body
    # The coach is the thing that was actually bought — name them.
    assert resp.context["premium_coach"] is not None
    assert "Nadia" in body


@pytest.mark.django_db
def test_coach_without_a_first_name_still_gets_named(client, settings):
    """A blank ``first_name`` is valid, and must not render "Your coach is ".

    Coach accounts are created by hand in the admin, so the name fields are
    exactly the ones that get left empty — on the single line whose whole job
    is to confirm what the member paid for.
    """
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)
    coach_user = me.crushprofile.assigned_coach.user
    coach_user.first_name = ""
    coach_user.last_name = ""
    coach_user.save(update_fields=["first_name", "last_name"])
    _login_eligible(client, me)

    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Premium member" in body
    assert f"Your coach is {coach_user.username}" in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "prefix,expected",
    [("fr", "Membre Premium"), ("de", "Premium-Mitglied")],
)
def test_premium_badge_is_translated(client, settings, prefix, expected):
    """The badge is the confirmation of a payment, so it must not be the one
    English island on a /fr/ or /de/ page. Pins the catalog entries: a missing
    msgid falls back to the English msgid silently, which is exactly how the
    two new strings shipped untranslated in the first place."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)
    _login_eligible(client, me)

    resp = client.get(f"/{prefix}/crush-connect/home/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert expected in body
    assert "Premium member" not in body


@pytest.mark.django_db
def test_candidate_without_premium_sees_no_premium_badge(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", onboarded=True, premium=False, has_luxid=True
    )
    _login_eligible(client, me)

    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["has_premium"] is False
    assert "Premium member" not in resp.content.decode()


@pytest.mark.django_db
def test_premium_badge_shows_during_beta_even_when_not_a_receiver(
    client, settings
):
    """The badge must key off the entitlement, not ``is_receiver``.

    In the beta phase ``is_receiver`` is (entitlement AND the phase lets them
    receive), so a member who has PAID but is not a hand-picked tester has
    ``is_receiver`` False. Badging on that would show someone who just handed
    over money no sign at all that they are Premium — which is the bug this
    whole section exists to prevent.
    """
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="me", onboarded=True, premium=True)
    _login_eligible(client, me)

    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["is_receiver"] is False, "precondition: not a receiver"
    assert resp.context["has_premium"] is True
    assert "Premium member" in resp.content.decode()


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
# Shared shell: member-facing pages link back to the Connect hub
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_member_page_links_back_to_the_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)
    _login_eligible(client, me)
    # That link comes from the persistent navbar / bottom nav
    # (crush_connect_nav_visible) — NOT from _connect_subnav.html, which no
    # template has included since d5a8a891. The old name and comment here
    # claimed the sub-nav, which is what made its stale predicate look live.
    resp = client.get(reverse("crush_lu:crush_connect_home"))
    assert resp.status_code == 200
    assert reverse("crush_lu:crush_connect_hub") in resp.content.decode()


# ---------------------------------------------------------------------------
# Sub-nav track tab: Premium entitlement, NOT assigned_coach
#
# The partial is rendered directly because no template includes it right now
# (d5a8a891 dropped the include from _base_connect.html). These tests pin the
# predicate so the coach-based test can't come back if it is ever wired in.
# ---------------------------------------------------------------------------


def _render_subnav(user):
    return render_to_string(
        "crush_lu/crush_connect/_connect_subnav.html", {"user": user}
    )


def _drop_coach_assigned_non_premium(username="coached"):
    """A member the 0150 backfill / attendance auto-assign left with a coach but
    no active PremiumMembership — the shape of every one of the ~464 affected
    accounts on production (0 active memberships platform-wide)."""
    user = _make_user(username=username, onboarded=True, premium=True)
    PremiumMembership.objects.filter(user=user).delete()
    user.crushprofile.refresh_from_db()
    assert user.crushprofile.assigned_coach_id is not None
    assert user.crushprofile.has_active_premium is False
    return user


@pytest.mark.django_db
def test_subnav_shows_in_the_mix_for_coach_assigned_non_premium(settings):
    """A coach without payment must NOT surface a 'Today's Drop' tab: it is
    mislabeled and _connect_access_blocker bounces it straight back to the
    catalogue status page."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _drop_coach_assigned_non_premium()

    body = _render_subnav(me)

    assert reverse("crush_lu:crush_connect_catalogue_status") in body
    assert reverse("crush_lu:crush_connect_home") not in body


@pytest.mark.django_db
def test_subnav_shows_todays_drop_for_premium_receiver(settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)

    body = _render_subnav(me)

    assert reverse("crush_lu:crush_connect_home") in body
    assert reverse("crush_lu:crush_connect_catalogue_status") not in body


@pytest.mark.django_db
def test_subnav_track_tab_matches_the_hub(client, settings):
    """The sub-nav filter and the hub's view-supplied ``is_receiver`` must agree
    — a coach-assigned non-premium member is a candidate on both surfaces."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _drop_coach_assigned_non_premium(username="me")
    _login_eligible(client, me)

    resp = client.get(HUB_URL)

    assert resp.status_code == 200
    assert resp.context["is_receiver"] is False
    assert crush_connect_is_receiver(me) is False


@pytest.mark.django_db
def test_subnav_is_receiver_filter_tracks_membership_status(settings):
    """Cancelling the membership revokes the receiver tab even though the coach
    relationship survives — mirrors the view-layer gate."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=True, premium=True)
    assert crush_connect_is_receiver(me) is True

    PremiumMembership.objects.filter(user=me).update(status="cancelled")

    assert crush_connect_is_receiver(me) is False
    assert me.crushprofile.assigned_coach_id is not None


@pytest.mark.django_db
def test_subnav_beta_premium_non_tester_is_a_candidate(settings):
    """In beta the phase also gates the receiver track: a Premium member who
    isn't a selected tester is a candidate, exactly as _user_can_receive_now
    reports it."""
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(username="me", onboarded=True, premium=True)

    body = _render_subnav(me)

    assert crush_connect_is_receiver(me) is False
    assert reverse("crush_lu:crush_connect_catalogue_status") in body
    assert reverse("crush_lu:crush_connect_home") not in body
