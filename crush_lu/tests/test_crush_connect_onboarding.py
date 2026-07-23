"""
Tests for the Crush Connect 7-step resumable onboarding wizard, the blended
Drop weighting, the post-onboarding edit page, and the pref-copy migration
helper.

Companion to ``test_crush_connect.py`` (whose helpers we reuse). View tests use
``/en/crush-connect/…`` URLs which only resolve under ``urls_crush``.
"""

import importlib
from datetime import date, timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.urls("azureproject.urls_crush")

from crush_lu.models import (
    Interest,
    CrushConnectMembership,
    CrushProfile,
    Trait,
)
from crush_lu.services.crush_connect import (
    INTEREST_OVERLAP_BOOST_PER,
    MATCHSCORE_NEUTRAL,
    NEW_MEMBER_BOOST,
    SHARED_LANGUAGE_BOOST,
    _weight_for,
    get_eligible_pool,
)
from crush_lu.tests.test_crush_connect import (
    _grant_consent,
    _login_eligible,
    _make_user,
    _mark_attended,
    _seed_pool_for,
    _set_gate_questions,
)
from django.contrib.auth import get_user_model

User = get_user_model()


# ---------------------------------------------------------------------------
# Wizard helpers
# ---------------------------------------------------------------------------

ONBOARDING_URL = "/en/crush-connect/onboarding/"
PROFILE_EDIT_URL = "/en/crush-connect/profile/"


def _step_url(step):
    return f"/en/crush-connect/onboarding/{step}/"


def _interest_pks(n):
    return list(
        Interest.objects.filter(is_active=True).values_list("pk", flat=True)[:n]
    )


def _trait_pks(trait_type, n=2):
    """Return ``n`` Trait pks of the given type, creating them if the test DB
    has none seeded (traits live on the membership now)."""
    existing = list(Trait.objects.filter(trait_type=trait_type).order_by("pk"))
    while len(existing) < n:
        idx = len(existing) + 1
        existing.append(
            Trait.objects.create(
                slug=f"{trait_type}-{idx}",
                label=f"{trait_type.title()} {idx}",
                trait_type=trait_type,
            )
        )
    return [t.pk for t in existing[:n]]


def _valid_step_data(step):
    if step == 1:
        return {"relationship_goal": "serious"}
    if step == 2:
        return {
            "lifestyle_energy": "mix",
            "lifestyle_social": "flexible",
            "lifestyle_pace": "balanced",
            "qualities": _trait_pks("quality", 2),
            "defects": _trait_pks("defect", 2),
        }
    if step == 3:
        return {"languages": ["lu", "fr"], "interests": _interest_pks(2)}
    if step == 4:
        return {
            "work_field": "it",
            "education_level": "master",
            "smoking": "no",
            "drinking": "socially",
        }
    if step == 5:
        return {
            "has_children": "no",
            "wants_children": "open",
            "relationship_timeline": "no_rush",
        }
    if step == 6:
        return {
            "preferred_genders": ["F"],
            "preferred_age_min": "25",
            "preferred_age_max": "40",
            "sought_qualities": _trait_pks("quality", 2),
            "first_step_preference": "no_preference",
            "astro_enabled": "on",
        }
    if step == 7:
        return _gate_question_data()
    raise ValueError(step)


def _week_question_ids(n=3):
    """The first ``n`` question ids from this week's active set (built lazily)."""
    from crush_lu.services.crush_connect import get_or_create_question_week

    week = get_or_create_question_week()
    return list(week.questions.filter(is_active=True).values_list("pk", flat=True)[:n])


def _gate_question_data(answers=None):
    """Step-7 POST payload: pick 3 questions (Yes/No) + both consent gates.

    ``answers`` optionally overrides the per-question Yes/No (list aligned to the
    3 picked questions); defaults to all "yes".
    """
    ids = _week_question_ids(3)
    answers = answers or ["yes"] * len(ids)
    data = {f"q_{qid}": ans for qid, ans in zip(ids, answers)}
    data["photo_share_consent"] = "on"
    data["confirm_terms"] = "on"
    return data


def _complete_steps(client, lo=1, hi=7):
    resp = None
    for step in range(lo, hi + 1):
        resp = client.post(_step_url(step), data=_valid_step_data(step))
    return resp


# ---------------------------------------------------------------------------
# Resume / step-gating
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resume_redirects_to_pointer_step(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 3)  # pointer now 4
    resp = client.get(ONBOARDING_URL)
    assert resp.status_code in (301, 302)
    assert "/crush-connect/onboarding/4/" in resp.url


@pytest.mark.django_db
def test_cannot_skip_ahead(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    # Fresh member (pointer=1): jumping to step 4 bounces back to step 1.
    resp = client.get(_step_url(4))
    assert resp.status_code in (301, 302)
    assert "/crush-connect/onboarding/1/" in resp.url


@pytest.mark.django_db
def test_back_edit_allowed_without_pointer_regression(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 3)  # pointer 4
    # Revisit a completed step.
    assert client.get(_step_url(2)).status_code == 200
    # Re-submit step 2 with new data — saves, but pointer must not regress.
    resp = client.post(
        _step_url(2),
        data={
            "lifestyle_energy": "homebody",
            "lifestyle_social": "intimate",
            "lifestyle_pace": "structured",
            "qualities": _trait_pks("quality", 2),
            "defects": _trait_pks("defect", 2),
        },
    )
    assert resp.status_code in (301, 302)
    m = CrushConnectMembership.objects.get(user=me)
    assert m.lifestyle_energy == "homebody"
    assert m.onboarding_step == 4  # unchanged


@pytest.mark.django_db
def test_per_step_immediate_persistence(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    client.post(_step_url(1), data=_valid_step_data(1))
    m = CrushConnectMembership.objects.get(user=me)
    assert m.relationship_goal == "serious"
    assert m.onboarding_step == 2
    assert m.onboarded_at is None  # not finished


@pytest.mark.django_db
def test_started_at_stamped_once(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    client.post(_step_url(1), data=_valid_step_data(1))
    started = CrushConnectMembership.objects.get(user=me).onboarding_started_at
    assert started is not None

    client.post(_step_url(2), data=_valid_step_data(2))
    again = CrushConnectMembership.objects.get(user=me).onboarding_started_at
    assert again == started


# ---------------------------------------------------------------------------
# Per-step validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_languages_requires_at_least_one(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 2)  # pointer 3
    resp = client.post(
        _step_url(3), data={"languages": [], "interests": _interest_pks(2)}
    )
    assert resp.status_code == 200  # re-render with errors
    assert CrushConnectMembership.objects.get(user=me).languages == []


@pytest.mark.django_db
def test_interests_capped_at_eight(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 2)  # pointer 3
    resp = client.post(
        _step_url(3), data={"languages": ["fr"], "interests": _interest_pks(9)}
    )
    assert resp.status_code == 200
    assert CrushConnectMembership.objects.get(user=me).interests.count() == 0


@pytest.mark.django_db
def test_prefer_not_say_accepted(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 3)  # pointer 4
    resp = client.post(
        _step_url(4),
        data={
            "work_field": "prefer_not_say",
            "education_level": "prefer_not_say",
            "smoking": "prefer_not_say",
            "drinking": "prefer_not_say",
        },
    )
    assert resp.status_code in (301, 302)  # advances
    resp = client.post(
        _step_url(5),
        data={
            "has_children": "prefer_not_say",
            "wants_children": "prefer_not_say",
            "relationship_timeline": "prefer_not_say",
        },
    )
    assert resp.status_code in (301, 302)
    m = CrushConnectMembership.objects.get(user=me)
    assert m.work_field == "prefer_not_say"
    assert m.has_children == "prefer_not_say"


@pytest.mark.django_db
def test_crossed_age_range_swapped(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 5)  # pointer 6
    client.post(
        _step_url(6),
        data={
            "preferred_genders": ["F"],
            "preferred_age_min": "40",
            "preferred_age_max": "25",
            "sought_qualities": _trait_pks("quality", 2),
            "first_step_preference": "no_preference",
            "astro_enabled": "on",
        },
    )
    m = CrushConnectMembership.objects.get(user=me)
    assert m.preferred_age_min == 25
    assert m.preferred_age_max == 40


@pytest.mark.django_db
def test_prefs_written_to_membership_not_profile(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    # Profile + membership both start with ['M']; the wizard must touch only
    # the membership.
    me = _make_user(username="me", preferred_genders=["M"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 6)
    m = CrushConnectMembership.objects.get(user=me)
    me.crushprofile.refresh_from_db()
    assert m.preferred_genders == ["F"]
    assert me.crushprofile.preferred_genders == ["M"]  # untouched


@pytest.mark.django_db
def test_onboarded_at_null_until_final_step(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 6)
    assert CrushConnectMembership.objects.get(user=me).onboarded_at is None

    resp = client.post(_step_url(7), data=_valid_step_data(7))
    assert resp.status_code in (301, 302)
    m = CrushConnectMembership.objects.get(user=me)
    assert m.onboarded_at is not None
    assert m.gate_questions.count() == 3
    assert m.photo_share_consent is True


@pytest.mark.django_db
def test_consent_required_on_final_step(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 6)
    data = _valid_step_data(7)
    data.pop("confirm_terms")
    resp = client.post(_step_url(7), data=data)
    assert resp.status_code == 200
    assert CrushConnectMembership.objects.get(user=me).onboarded_at is None


@pytest.mark.django_db
def test_fewer_than_three_questions_rejected(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 6)
    # Only two questions answered → form requires exactly 3.
    ids = _week_question_ids(2)
    data = {f"q_{qid}": "yes" for qid in ids}
    data["photo_share_consent"] = "on"
    data["confirm_terms"] = "on"
    resp = client.post(_step_url(7), data=data)
    assert resp.status_code == 200
    m = CrushConnectMembership.objects.get(user=me)
    assert m.onboarded_at is None
    assert m.gate_questions.count() == 0


@pytest.mark.django_db
def test_photo_consent_required_on_final_step(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 6)
    data = _valid_step_data(7)
    data.pop("photo_share_consent")
    resp = client.post(_step_url(7), data=data)
    assert resp.status_code == 200
    assert CrushConnectMembership.objects.get(user=me).onboarded_at is None


# ---------------------------------------------------------------------------
# Life step redesign: height slider + emoji radio tiles (no native selects)
# ---------------------------------------------------------------------------


def _login_at_step_4(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)
    _complete_steps(client, 1, 3)  # pointer 4
    return me


@pytest.mark.django_db
def test_life_step_renders_slider_and_tiles(client, settings):
    """Step 4 renders the height slider (single named hidden input) and radio
    tiles for all four choice fields — the native selects are gone."""
    _login_at_step_4(client, settings)

    resp = client.get(_step_url(4))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'x-data="heightSlider"' in body
    # Only the hidden input carries the field name; the visible range is unnamed.
    assert body.count('name="height_cm"') == 1
    assert body.count('name="work_field"') == 14
    assert body.count('name="education_level"') == 6
    assert body.count('name="smoking"') == 4
    assert body.count('name="drinking"') == 4
    assert "💻" in body  # emoji tiles present
    assert '<select name="work_field"' not in body
    assert '<select name="education_level"' not in body


@pytest.mark.django_db
def test_life_step_height_prefill(client, settings):
    """A stored height reaches the slider via data-initial and the hidden input."""
    me = _login_at_step_4(client, settings)
    m = CrushConnectMembership.objects.get(user=me)
    m.height_cm = 165
    m.save()

    resp = client.get(_step_url(4))
    assert resp.status_code == 200
    assert 'data-initial="165"' in resp.content.decode()


@pytest.mark.django_db
def test_life_step_empty_height_saves_none(client, settings):
    """The 'prefer not to say' pill submits an empty height_cm → stored NULL."""
    me = _login_at_step_4(client, settings)
    m = CrushConnectMembership.objects.get(user=me)
    m.height_cm = 165  # previously answered, now opting out
    m.save()

    data = dict(_valid_step_data(4), height_cm="")
    resp = client.post(_step_url(4), data=data)
    assert resp.status_code in (301, 302)
    assert CrushConnectMembership.objects.get(user=me).height_cm is None


@pytest.mark.django_db
def test_life_step_height_persists(client, settings):
    me = _login_at_step_4(client, settings)

    data = dict(_valid_step_data(4), height_cm="184")
    resp = client.post(_step_url(4), data=data)
    assert resp.status_code in (301, 302)
    assert CrushConnectMembership.objects.get(user=me).height_cm == 184


@pytest.mark.django_db
def test_life_step_height_out_of_bounds_rejected(client, settings):
    """Validators (120–230) still apply; the re-render keeps the posted tile
    selection checked."""
    import re

    me = _login_at_step_4(client, settings)

    data = dict(_valid_step_data(4), height_cm="119")
    resp = client.post(_step_url(4), data=data)
    assert resp.status_code == 200  # re-render with errors
    assert CrushConnectMembership.objects.get(user=me).height_cm is None
    body = resp.content.decode()
    assert re.search(r'name="work_field" value="it"[^>]*checked', body)


@pytest.mark.django_db
def test_profile_edit_life_section_renders_and_saves(client, settings):
    """The ?section=life editor shares the redesigned partial: slider + tiles
    render, and a POST saves every field and returns to the index."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(PROFILE_EDIT_URL + "?section=life")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'name="section" value="life"' in body
    assert 'x-data="heightSlider"' in body
    assert body.count('name="work_field"') == 14

    data = dict(_valid_step_data(4), section="life", height_cm="172")
    resp = client.post(PROFILE_EDIT_URL + "?section=life", data=data)
    assert resp.status_code in (301, 302)
    m = CrushConnectMembership.objects.get(user=me)
    assert m.height_cm == 172
    assert m.work_field == "it"
    assert m.education_level == "master"
    assert m.smoking == "no"
    assert m.drinking == "socially"


# ---------------------------------------------------------------------------
# Completion redirects per track + candidate email
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_receiver_completion_redirects_to_today(client, settings, mailoutbox):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = _complete_steps(client, 1, 7)
    assert resp.status_code in (301, 302)
    assert "/crush-connect/today/" in resp.url
    assert len(mailoutbox) == 0  # receivers get no catalogue email


@pytest.mark.django_db
def test_candidate_completion_redirects_to_catalogue_with_email(
    client, settings, mailoutbox
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=False, premium=False
    )
    _login_eligible(client, me)

    resp = _complete_steps(client, 1, 7)
    assert resp.status_code in (301, 302)
    assert "/crush-connect/catalogue/" in resp.url
    assert CrushConnectMembership.objects.get(user=me).onboarded_at is not None
    assert len(mailoutbox) == 1


@pytest.mark.django_db
def test_checked_in_candidate_completion_redirects_to_joined_live_lobby(
    client, settings
):
    from django.urls import reverse

    from crush_lu.models import EventLobbyParticipation
    from crush_lu.tests.test_event_lobby import (
        _attend,
        _make_event as _make_live_event,
        _make_member,
    )

    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    me = _make_member("me", onboarded=False)
    event = _make_live_event()
    registration = _attend(me, event)
    _login_eligible(client, me)

    resp = _complete_steps(client, 1, 7)

    assert resp.status_code in (301, 302)
    assert resp.url == reverse("crush_lu:event_lobby", args=[event.pk])
    participation = EventLobbyParticipation.objects.get(event_registration=registration)
    assert participation.eligibility_source == "onboarding_completed"


# ---------------------------------------------------------------------------
# Gate (ported)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_step_redirects_to_teaser_when_flag_off(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(_step_url(1))
    assert resp.status_code in (301, 302)
    assert "/crush-connect/" in resp.url


@pytest.mark.django_db
def test_step_redirects_to_teaser_when_ineligible(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me",
        preferred_genders=["F"],
        onboarded=False,
        premium=False,
        has_luxid=False,
    )
    _login_eligible(client, me)

    resp = client.get(_step_url(1))
    assert resp.status_code in (301, 302)


@pytest.mark.django_db
def test_step_redirects_onboarded_user_away(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(_step_url(1))
    assert resp.status_code in (301, 302)
    assert "/crush-connect/today/" in resp.url


# ---------------------------------------------------------------------------
# LuxID-first opt-in gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_premium_without_luxid_blocked_from_onboarding(client, settings):
    """LuxID is the entry requirement for opt-in: a Premium member without LuxID
    can no longer start the wizard (previously the Premium coach alone let them in)."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me",
        preferred_genders=["F"],
        onboarded=False,
        premium=True,
        has_luxid=False,
    )
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(_step_url(1))
    assert resp.status_code in (301, 302)
    assert resp.url.endswith("/crush-connect/")  # teaser, not the wizard
    assert "/onboarding/" not in resp.url
    # They can't opt in, so onboarding never starts for them.
    assert CrushConnectMembership.objects.get(user=me).onboarded_at is None


@pytest.mark.django_db
def test_luxid_candidate_without_premium_can_onboard(client, settings):
    """The LuxID-only (candidate) track may opt in and complete onboarding."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me",
        preferred_genders=["F"],
        onboarded=False,
        premium=False,
        has_luxid=True,
    )
    _login_eligible(client, me)

    resp = client.get(_step_url(1))
    assert resp.status_code == 200  # in the wizard


@pytest.mark.django_db
def test_onboarded_member_without_luxid_grandfathered(client, settings):
    """A member who onboarded before the LuxID-first gate keeps access — the gate
    only guards new opt-ins / data collection, not already-onboarded members."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me",
        preferred_genders=["F"],
        onboarded=True,
        premium=True,
        has_luxid=False,
    )
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(_step_url(1))
    assert resp.status_code in (301, 302)
    assert "/crush-connect/today/" in resp.url  # sent to their Drop, not the teaser


@pytest.mark.django_db
def test_premium_without_luxid_sees_teaser_not_redirect_loop(client, settings):
    """Regression: a launched-Connect Premium member without LuxID and not
    onboarded must land on the teaser (with the Connect-LuxID CTA), not bounce
    in a teaser <-> onboarding redirect loop."""
    from django.urls import reverse

    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me",
        preferred_genders=["F"],
        onboarded=False,
        premium=True,
        has_luxid=False,
    )
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(reverse("crush_lu:crush_connect_teaser"))
    assert resp.status_code == 200  # renders the teaser — no redirect loop
    assert resp.context["profile_approved"] is True
    assert resp.context["has_luxid_connected"] is False


@pytest.mark.django_db
def test_legacy_traits_prefill_wizard(client, settings):
    """The legacy Ideal Crush traits on CrushProfile pre-fill the Connect wizard
    the first time (the lazy migration) — confirmed/consented before they persist."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    legacy_q = _trait_pks("quality", 3)
    me.crushprofile.qualities.set(legacy_q)

    client.post(_step_url(1), data=_valid_step_data(1))  # pointer → 2
    resp = client.get(_step_url(2))
    assert resp.status_code == 200
    assert set(resp.context["selected_quality_ids"]) == set(legacy_q)
    # Prefill is form-initial only — nothing persisted onto the membership yet.
    assert CrushConnectMembership.objects.get(user=me).qualities.count() == 0


@pytest.mark.django_db
def test_legacy_age_gender_prefill_step6(client, settings):
    """Legacy Ideal Crush age/gender preferences pre-fill step 6 so a first-time
    opt-in doesn't overwrite them with the wizard's open defaults."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    # Legacy profile prefs that differ from the membership's defaults.
    me.crushprofile.preferred_genders = ["F", "NB"]
    me.crushprofile.preferred_age_min = 28
    me.crushprofile.preferred_age_max = 42
    me.crushprofile.save()

    _complete_steps(client, 1, 5)  # pointer → 6
    resp = client.get(_step_url(6))
    assert resp.status_code == 200
    form = resp.context["form"]
    assert list(form["preferred_genders"].value()) == ["F", "NB"]
    assert int(form["preferred_age_min"].value()) == 28
    assert int(form["preferred_age_max"].value()) == 42


@pytest.mark.django_db
def test_migration_0165_backfills_onboarded_member_traits():
    """The 0165 data migration copies legacy CrushProfile traits onto an
    already-onboarded membership whose trait fields are still empty."""
    from importlib import import_module

    from django.apps import apps as django_apps

    mig = import_module("crush_lu.migrations.0165_migrate_legacy_traits_to_memberships")
    me = _make_user(username="legacy", preferred_genders=["F"], onboarded=True)
    quals = list(
        Trait.objects.filter(trait_type="quality")
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    defs = list(
        Trait.objects.filter(trait_type="defect")
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    me.crushprofile.qualities.set(quals[:3])
    me.crushprofile.defects.set(defs[:2])
    me.crushprofile.sought_qualities.set(quals[5:7])
    me.crushprofile.first_step_preference = "i_initiate"
    me.crushprofile.save()

    membership = CrushConnectMembership.objects.get(user=me)
    assert membership.qualities.count() == 0  # empty before migration

    mig.copy_legacy_traits(django_apps, None)

    membership.refresh_from_db()
    assert set(membership.qualities.values_list("pk", flat=True)) == set(quals[:3])
    assert set(membership.defects.values_list("pk", flat=True)) == set(defs[:2])
    assert set(membership.sought_qualities.values_list("pk", flat=True)) == set(
        quals[5:7]
    )
    assert membership.first_step_preference == "i_initiate"


@pytest.mark.django_db
def test_migration_0165_does_not_clobber_existing_member_traits():
    """The backfill is only-if-empty — a member who already has membership traits
    keeps them (a deliberate edit is never overwritten by stale profile data)."""
    from importlib import import_module

    from django.apps import apps as django_apps

    mig = import_module("crush_lu.migrations.0165_migrate_legacy_traits_to_memberships")
    me = _make_user(username="hasdata", preferred_genders=["F"], onboarded=True)
    quals = list(
        Trait.objects.filter(trait_type="quality")
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    me.crushprofile.qualities.set(quals[:3])
    me.crushprofile.save()
    membership = CrushConnectMembership.objects.get(user=me)
    membership.qualities.set(quals[5:7])  # member already chose different traits

    mig.copy_legacy_traits(django_apps, None)

    membership.refresh_from_db()
    assert set(membership.qualities.values_list("pk", flat=True)) == set(quals[5:7])


# ---------------------------------------------------------------------------
# Blended Drop weighting
# ---------------------------------------------------------------------------


def _set_membership(user, *, languages=None, interest_pks=None, onboarded_days_ago=60):
    m = user.crush_connect_membership
    if languages is not None:
        m.languages = languages
    m.onboarded_at = timezone.now() - timedelta(days=onboarded_days_ago)
    m.save()
    if interest_pks is not None:
        m.interests.set(interest_pks)
    return m


def _weight(
    cand,
    *,
    viewer_languages=frozenset(),
    viewer_interest_ids=frozenset(),
    match_scores=None,
    today=None,
):
    return _weight_for(
        cand,
        today=today or date.today(),
        viewer_languages=viewer_languages,
        viewer_interest_ids=viewer_interest_ids,
        match_scores=match_scores or {},
    )


@pytest.mark.django_db
def test_weight_neutral_for_unenriched_member():
    """Empty languages/interests + missing MatchScore + old member → base 1.0."""
    cand = _make_user(username="c", gender="F")
    _set_membership(cand, languages=[], interest_pks=[], onboarded_days_ago=60)
    assert _weight(cand) == pytest.approx(1.0)


@pytest.mark.django_db
def test_weight_shared_language_boost():
    cand = _make_user(username="c", gender="F")
    _set_membership(cand, languages=["fr"], onboarded_days_ago=60)
    assert _weight(cand, viewer_languages=frozenset({"fr"})) == pytest.approx(
        SHARED_LANGUAGE_BOOST
    )
    assert _weight(cand, viewer_languages=frozenset({"de"})) == pytest.approx(1.0)


@pytest.mark.django_db
def test_weight_interest_overlap_capped():
    cand = _make_user(username="c", gender="F")
    pks = _interest_pks(4)
    _set_membership(cand, interest_pks=pks, onboarded_days_ago=60)
    # 4 shared → capped at 3 → 1 + 3*0.1 = 1.3
    expected = 1.0 + INTEREST_OVERLAP_BOOST_PER * 3
    assert _weight(cand, viewer_interest_ids=frozenset(pks)) == pytest.approx(expected)
    # 2 shared → 1.2
    assert _weight(cand, viewer_interest_ids=frozenset(pks[:2])) == pytest.approx(1.2)


@pytest.mark.django_db
def test_weight_matchscore_blend():
    cand = _make_user(username="c", gender="F")
    _set_membership(cand, languages=[], interest_pks=[], onboarded_days_ago=60)
    assert _weight(cand, match_scores={cand.pk: 0.9}) == pytest.approx(
        MATCHSCORE_NEUTRAL + 0.9
    )
    assert _weight(cand, match_scores={cand.pk: 0.1}) == pytest.approx(
        MATCHSCORE_NEUTRAL + 0.1
    )
    assert _weight(cand, match_scores={}) == pytest.approx(1.0)  # missing → neutral


@pytest.mark.django_db
def test_weight_multiplicative_with_new_member_boost():
    cand = _make_user(username="c", gender="F")
    _set_membership(cand, languages=["fr"], onboarded_days_ago=1)  # in window
    # base 1.0 (neutral) * 1.3 language * 1.0 interest * 1.5 new-member
    expected = 1.0 * SHARED_LANGUAGE_BOOST * NEW_MEMBER_BOOST
    assert _weight(cand, viewer_languages=frozenset({"fr"})) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Post-onboarding edit page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_edit_page_requires_onboarded(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(PROFILE_EDIT_URL)
    assert resp.status_code in (301, 302)
    assert "/crush-connect/onboarding/" in resp.url


@pytest.mark.django_db
def test_edit_page_renders_index_for_onboarded(client, settings):
    """Bare GET renders the drill-down index: tappable section links, not a
    wall of forms."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(PROFILE_EDIT_URL)
    assert resp.status_code == 200
    body = resp.content.decode()
    # A representative section title is listed…
    assert "Languages &amp; interests" in body
    # …and each row links into its focused editor.
    assert "?section=questions" in body
    assert "?section=intention" in body


@pytest.mark.django_db
def test_edit_section_detail_renders(client, settings):
    """GET ?section=<key> opens just that one section's editor."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(PROFILE_EDIT_URL + "?section=questions")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'name="section" value="questions"' in body
    assert "Back to your profile" in body


@pytest.mark.django_db
def test_edit_index_shows_current_value_summary(client, settings):
    """The index previews each section's current value so members don't have
    to open one to remember what's in it."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    m = me.crush_connect_membership
    m.relationship_goal = "serious"
    m.save()

    resp = client.get(PROFILE_EDIT_URL)
    assert resp.status_code == 200
    assert m.get_relationship_goal_display() in resp.content.decode()


@pytest.mark.django_db
def test_edit_section_save_roundtrip_keeps_onboarded_at(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    before = CrushConnectMembership.objects.get(user=me).onboarded_at
    resp = client.post(
        PROFILE_EDIT_URL + "?section=intention",
        data={
            "section": "intention",
            "relationship_goal": "open",
        },
    )
    assert resp.status_code in (301, 302)
    m = CrushConnectMembership.objects.get(user=me)
    assert m.relationship_goal == "open"
    assert m.onboarded_at == before  # untouched


@pytest.mark.django_db
def test_edit_questions_section_saves_without_consent(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    data = _gate_question_data()
    # Edit mode drops the terms-consent gate; still saves the 3 questions.
    data["section"] = "questions"
    data.pop("confirm_terms", None)
    resp = client.post(PROFILE_EDIT_URL + "?section=questions", data=data)
    assert resp.status_code in (301, 302)
    assert CrushConnectMembership.objects.get(user=me).gate_questions.count() == 3


@pytest.mark.django_db
def test_repick_changing_truth_clears_stale_guesses(client, settings):
    """Editing a question's own-truth clears viewers' now-stale guesses so they
    can answer the fresh gate; unchanged questions keep their guesses (Codex P2)."""
    from crush_lu.models import ConnectQuestionAnswer

    settings.CRUSH_CONNECT_LAUNCHED = True
    owner = _make_user(username="owner", preferred_genders=["F"])
    _mark_attended(owner)
    _login_eligible(client, owner)
    qs = _set_gate_questions(owner, answers=[True, True, True])

    viewer = _make_user(username="viewer", gender="F", premium=False)
    for q in qs:
        ConnectQuestionAnswer.objects.create(
            responder=viewer, profile_owner=owner, question=q, answer=True
        )

    # Owner re-picks the SAME 3 questions but flips the first one's truth.
    data = {
        f"q_{qs[0].id}": "no",  # truth changed → guesses become stale
        f"q_{qs[1].id}": "yes",  # unchanged
        f"q_{qs[2].id}": "yes",  # unchanged
        "photo_share_consent": "on",
        "section": "questions",
    }
    resp = client.post(PROFILE_EDIT_URL + "?section=questions", data=data)
    assert resp.status_code in (301, 302)

    assert not ConnectQuestionAnswer.objects.filter(
        responder=viewer, profile_owner=owner, question=qs[0]
    ).exists()
    assert ConnectQuestionAnswer.objects.filter(
        responder=viewer, profile_owner=owner, question=qs[1]
    ).exists()


# ---------------------------------------------------------------------------
# Pref-copy migration helper
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_copy_connect_prefs_mirrors_profile_onto_membership():
    mod = importlib.import_module("crush_lu.migrations.0162_copy_connect_prefs")

    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _seed_pool_for(me, n=3)

    # Simulate a pre-migration member: membership prefs at defaults, the real
    # prefs only on the profile.
    m = me.crush_connect_membership
    m.preferred_genders = []
    m.preferred_age_min = 18
    m.preferred_age_max = 99
    m.onboarding_step = 1
    m.save()
    p = me.crushprofile
    p.preferred_genders = ["F"]
    p.preferred_age_min = 25
    p.preferred_age_max = 40
    p.save()

    updated = mod.copy_connect_prefs(CrushProfile, CrushConnectMembership)
    assert updated >= 1

    m.refresh_from_db()
    assert m.preferred_genders == ["F"]
    assert m.preferred_age_min == 25
    assert m.preferred_age_max == 40
    # Onboarded member parked past the last step.
    assert m.onboarding_step == mod.ONBOARDED_STEP
    # Pool still computes (reads the now-copied membership prefs).
    assert get_eligible_pool(me).count() >= 0


# ---------------------------------------------------------------------------
# Display: model helpers + template smoke
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_life_and_family_display_skip_prefer_not_say():
    me = _make_user(username="me")
    m = me.crush_connect_membership
    m.height_cm = 180
    m.work_field = "it"
    m.education_level = "prefer_not_say"  # skipped
    m.smoking = "no"
    m.drinking = ""  # blank, skipped
    m.has_children = "no"
    m.wants_children = "prefer_not_say"  # skipped
    m.relationship_timeline = "no_rush"
    m.save()

    life = m.life_situation_display
    assert "180 cm" in life
    assert m.get_work_field_display() in life
    assert m.get_smoking_display() in life
    assert m.get_education_level_display() not in life  # prefer_not_say skipped

    fam = m.family_future_display
    assert m.get_has_children_display() in fam
    assert m.get_relationship_timeline_display() in fam
    assert m.get_wants_children_display() not in fam


def _staff_client(client):
    staff = User.objects.create_user(
        username="staffy", email="staffy@example.com", password="x", is_staff=True
    )
    _grant_consent(staff)
    client.force_login(staff)
    return staff


@pytest.mark.django_db
def test_drop_card_shows_membership_chips(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    _staff_client(client)
    target = _make_user(username="t", gender="F")
    m = target.crush_connect_membership
    m.languages = ["fr"]
    m.save()
    pks = _interest_pks(2)
    m.interests.set(pks)

    resp = client.get(f"/en/dev/connect-card/{target.pk}/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Français" in body  # languages_display label
    assert Interest.objects.get(pk=pks[0]).label in body


@pytest.mark.django_db
def test_drop_card_falls_back_to_profile_chips(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    _staff_client(client)
    target = _make_user(username="t2", gender="F")
    # Membership has no languages/interests → fall back to the event profile's
    # Event Identity taxonomy (interests_new), not the retired free-text field
    # (Event Identity redesign §7).
    p = target.crushprofile
    p.event_languages = ["en"]
    p.save()
    pks = _interest_pks(2)
    p.interests_new.set(pks)

    resp = client.get(f"/en/dev/connect-card/{target.pk}/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "EN" in body  # |upper fallback
    assert Interest.objects.get(pk=pks[0]).label in body


@pytest.mark.django_db
def test_catalogue_status_shows_chips_and_edit_link(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=True, premium=False
    )
    _login_eligible(client, me)
    m = me.crush_connect_membership
    m.languages = ["lu"]
    m.save()
    m.interests.set(_interest_pks(1))

    resp = client.get("/en/crush-connect/catalogue/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Lëtzebuergesch" in body
    assert "/crush-connect/profile/" in body


@pytest.mark.django_db
def test_admin_pages_load(client):
    """The new fieldsets / Interest admin render without error."""
    admin_user = User.objects.create_superuser(
        username="root", email="root@example.com", password="x"
    )
    client.force_login(admin_user)

    me = _make_user(username="member", preferred_genders=["F"], onboarded=True)
    m = me.crush_connect_membership
    m.interests.set(_interest_pks(2))

    # Interest changelist + add.
    assert client.get("/crush-admin/crush_lu/interest/").status_code == 200
    assert client.get("/crush-admin/crush_lu/interest/add/").status_code == 200
    # Membership changelist + change form (exercises the new fieldsets).
    assert (
        client.get("/crush-admin/crush_lu/crushconnectmembership/").status_code == 200
    )
    assert (
        client.get(
            f"/crush-admin/crush_lu/crushconnectmembership/{m.pk}/change/"
        ).status_code
        == 200
    )


# ---------------------------------------------------------------------------
# Edit-page discoverability (links into crush_connect_profile_edit)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_today_drop_shows_edit_profile_link(client, settings):
    """Premium (receiver) members land on Today's Drop — it must link to the
    Connect profile editor (the gap this change closes)."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _seed_pool_for(me, n=3)
    _login_eligible(client, me)

    resp = client.get("/en/crush-connect/today/")
    assert resp.status_code == 200
    assert "/crush-connect/profile/" in resp.content.decode()


@pytest.mark.django_db
def test_nav_edit_link_gated_on_onboarded(settings):
    """The nav 'Edit your Connect profile' item is gated by
    crush_connect_nav_visible — shown to onboarded members, hidden otherwise."""
    from crush_lu.templatetags.crush_connect_tags import crush_connect_nav_visible

    settings.CRUSH_CONNECT_LAUNCHED = True
    onboarded = _make_user(username="on", onboarded=True)
    not_onboarded = _make_user(username="off", onboarded=False)

    assert crush_connect_nav_visible(onboarded) is True
    assert crush_connect_nav_visible(not_onboarded) is False
