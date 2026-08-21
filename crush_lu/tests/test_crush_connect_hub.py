"""
Tests for the Crush Connect hub (homepage) and its dedicated navigation.

The hub is the shared landing for both onboarded tracks; the navbar "Crush
Connect" entry and the mobile bottom-nav "My Crush" tab point here. View tests
use ``/en/crush-connect/…`` URLs which only resolve under ``urls_crush``.
"""

import pytest
from django.contrib.messages import get_messages
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
    _set_gate_questions,
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
    me = _make_user(username="me", onboarded=False, premium=False, has_luxid=False)
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code in (301, 302)
    assert resp.url.endswith("/crush-connect/")


@pytest.mark.django_db
def test_eligible_not_onboarded_sees_preparation_checklist(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", onboarded=False, premium=False, has_luxid=True)
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    readiness = resp.context["connect_readiness"]
    steps = {step["key"]: step for step in readiness["steps"]}
    assert steps["onboarding"]["complete"] is False
    assert steps["onboarding"]["cta_url"] == reverse(
        "crush_lu:crush_connect_onboarding"
    )
    body = resp.content.decode()
    assert "Continue onboarding" in body
    assert (
        "You are not visible in Connect until you explicitly finish onboarding."
        in body
    )
    # Seeing preparation does not expose any gated feature surface.
    assert reverse("crush_lu:connect_week_home") not in body
    assert reverse("crush_lu:crush_connect_home") not in body


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
    me = _make_user(username="me", onboarded=True, premium=False, has_luxid=True)
    _login_eligible(client, me)

    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["has_premium"] is False
    assert "Premium member" not in resp.content.decode()


@pytest.mark.django_db
def test_premium_badge_shows_during_beta_even_when_not_a_receiver(client, settings):
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
    me = _make_user(username="me", onboarded=True, premium=False, has_luxid=True)
    _login_eligible(client, me)
    resp = client.get(HUB_URL)
    assert resp.status_code == 200
    assert resp.context["track"] == "candidate"
    assert resp.context["is_receiver"] is False
    # Candidate hero links to the catalogue status, not Today's Drop.
    assert reverse("crush_lu:crush_connect_catalogue_status") in resp.content.decode()


# ---------------------------------------------------------------------------
# Readiness checklist and privacy-safe trust labels
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "has_luxid,event_verified,kind,label",
    [
        (True, False, "luxid", "Identity confirmed by LuxID"),
        (False, True, "event", "Verified at a Crush event"),
        (True, True, "double", "Double verification: LuxID + Crush event"),
    ],
)
def test_hub_shows_the_three_member_facing_trust_states(
    client, settings, has_luxid, event_verified, kind, label
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username=f"trust_{kind}",
        onboarded=True,
        premium=False,
        has_luxid=has_luxid,
    )
    if event_verified:
        me.crushprofile.verification_method = "coach_event"
        me.crushprofile.save(update_fields=["verification_method"])
    _login_eligible(client, me)

    resp = client.get(HUB_URL)

    assert resp.status_code == 200
    trust = resp.context["connect_readiness"]["trust_status"]
    assert trust["kind"] == kind
    assert str(trust["label"]) == label
    body = resp.content.decode()
    assert f'data-trust-status="{kind}"' in body
    assert label in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "prefix,has_luxid,event_verified,expected",
    [
        ("fr", True, False, "Identité confirmée par LuxID"),
        ("fr", False, True, "Vérifié lors d’un événement Crush"),
        ("fr", True, True, "Double vérification : LuxID + événement Crush"),
        ("de", True, False, "Identität durch LuxID bestätigt"),
        ("de", False, True, "Bei einem Crush-Event verifiziert"),
        ("de", True, True, "Doppelte Verifizierung: LuxID + Crush-Event"),
    ],
)
def test_trust_labels_are_translated_without_exposing_verification_details(
    client, settings, prefix, has_luxid, event_verified, expected
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username=f"{prefix}_{'event' if event_verified else 'luxid'}_{has_luxid}",
        onboarded=True,
        premium=False,
        has_luxid=has_luxid,
    )
    if event_verified:
        me.crushprofile.verification_method = "coach_event"
        me.crushprofile.save(update_fields=["verification_method"])
    _login_eligible(client, me)

    resp = client.get(f"/{prefix}/crush-connect/home/")

    assert resp.status_code == 200
    body = resp.content.decode()
    assert expected in body
    # Only the approved status text is rendered; no provider/check-in fields
    # or machine-readable provenance are added to the page.
    assert "verification_method" not in body
    assert "checkin_attested_photo_key" not in body


@pytest.mark.django_db
def test_luxid_only_status_explains_event_unlock_and_links_to_events(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    me = _make_user(
        username="luxid_only", onboarded=True, premium=False, has_luxid=True
    )
    _login_eligible(client, me)

    resp = client.get(HUB_URL)

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Identity confirmed by LuxID" in body
    assert (
        "Taking part in a Crush event unlocks the active Connect journey and your "
        "three daily suggestions" in body
    )
    assert reverse("crush_lu:event_list") in body
    steps = {step["key"]: step for step in resp.context["connect_readiness"]["steps"]}
    assert steps["active_verification"]["complete"] is False
    assert steps["active_verification"]["cta_url"] == reverse("crush_lu:event_list")


@pytest.mark.django_db
def test_missing_photo_stays_on_hub_with_direct_cta_and_explicit_blocker_message(
    client, settings
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="no_photo", onboarded=True, premium=False)
    me.crushprofile.verification_method = "coach_event"
    me.crushprofile.photo_1 = ""
    me.crushprofile.save(update_fields=["verification_method", "photo_1"])
    _login_eligible(client, me)

    hub = client.get(HUB_URL)

    assert hub.status_code == 200
    steps = {step["key"]: step for step in hub.context["connect_readiness"]["steps"]}
    assert steps["photo"]["complete"] is False
    assert steps["photo"]["cta_url"].endswith("/profile/edit/?section=photos")
    body = hub.content.decode()
    assert 'data-readiness-step="photo"' in body
    assert "A profile photo is required for your daily Connect suggestions." in body

    week = client.get(reverse("crush_lu:connect_week_home"))
    assert week.status_code in (301, 302)
    assert week.url.endswith("/profile/edit/?section=photos")
    notices = [str(message) for message in get_messages(week.wsgi_request)]
    assert any(
        "It blocks access to your three daily Connect Week suggestions" in notice
        for notice in notices
    )


@pytest.mark.django_db
def test_consent_and_questions_use_the_connect_questions_screen(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="missing_connect_steps",
        onboarded=True,
        premium=False,
        photo_share_consent=False,
    )
    me.crushprofile.verification_method = "coach_event"
    me.crushprofile.save(update_fields=["verification_method"])
    _login_eligible(client, me)

    resp = client.get(HUB_URL)

    assert resp.status_code == 200
    steps = {step["key"]: step for step in resp.context["connect_readiness"]["steps"]}
    questions_url = (
        reverse("crush_lu:crush_connect_profile_edit") + "?section=questions"
    )
    assert steps["photo_consent"]["complete"] is False
    assert steps["photo_consent"]["cta_url"] == questions_url
    assert steps["questions"]["complete"] is False
    assert steps["questions"]["cta_url"] == questions_url
    body = resp.content.decode()
    assert "Review photo consent" in body
    assert "Choose my questions" in body


@pytest.mark.django_db
def test_complete_checklist_uses_real_member_state_and_no_activity_pseudogate(
    client, settings
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="ready", onboarded=True, premium=False)
    me.crushprofile.verification_method = "coach_event"
    me.crushprofile.save(update_fields=["verification_method"])
    _set_gate_questions(me)
    _login_eligible(client, me)

    resp = client.get(HUB_URL)

    assert resp.status_code == 200
    readiness = resp.context["connect_readiness"]
    assert readiness["is_complete"] is True
    assert readiness["completed_count"] == readiness["total_count"] == 6
    assert {step["key"] for step in readiness["steps"]} == {
        "identity",
        "active_verification",
        "photo",
        "photo_consent",
        "onboarding",
        "questions",
    }


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
