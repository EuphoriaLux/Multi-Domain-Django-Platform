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
    ConnectInterest,
    CrushConnectMembership,
    CrushProfile,
    SparkPrompt,
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
)
from django.contrib.auth import get_user_model

User = get_user_model()


# ---------------------------------------------------------------------------
# Wizard helpers
# ---------------------------------------------------------------------------

ONBOARDING_URL = "/en/crush-connect/onboarding/"
PROFILE_EDIT_URL = "/en/crush-connect/profile/"
STORY_ANSWER = "I love foggy walks along the Pétrusse valley."


def _step_url(step):
    return f"/en/crush-connect/onboarding/{step}/"


def _interest_pks(n):
    return list(
        ConnectInterest.objects.filter(is_active=True).values_list("pk", flat=True)[:n]
    )


def _valid_step_data(step):
    if step == 1:
        return {"relationship_goal": "serious"}
    if step == 2:
        return {
            "lifestyle_energy": "mix",
            "lifestyle_social": "flexible",
            "lifestyle_pace": "balanced",
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
        }
    if step == 7:
        return {
            "story_prompt": SparkPrompt.objects.filter(is_active=True).first().pk,
            "story_answer": STORY_ANSWER,
            "confirm_terms": "on",
        }
    raise ValueError(step)


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
    resp = client.post(_step_url(2), data={
        "lifestyle_energy": "homebody",
        "lifestyle_social": "intimate",
        "lifestyle_pace": "structured",
    })
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
    resp = client.post(_step_url(3), data={"languages": [], "interests": _interest_pks(2)})
    assert resp.status_code == 200  # re-render with errors
    assert CrushConnectMembership.objects.get(user=me).languages == []


@pytest.mark.django_db
def test_interests_capped_at_eight(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 2)  # pointer 3
    resp = client.post(_step_url(3), data={"languages": ["fr"], "interests": _interest_pks(9)})
    assert resp.status_code == 200
    assert CrushConnectMembership.objects.get(user=me).interests.count() == 0


@pytest.mark.django_db
def test_prefer_not_say_accepted(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 3)  # pointer 4
    resp = client.post(_step_url(4), data={
        "work_field": "prefer_not_say",
        "education_level": "prefer_not_say",
        "smoking": "prefer_not_say",
        "drinking": "prefer_not_say",
    })
    assert resp.status_code in (301, 302)  # advances
    resp = client.post(_step_url(5), data={
        "has_children": "prefer_not_say",
        "wants_children": "prefer_not_say",
        "relationship_timeline": "prefer_not_say",
    })
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
    client.post(_step_url(6), data={
        "preferred_genders": ["F"],
        "preferred_age_min": "40",
        "preferred_age_max": "25",
    })
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
    assert STORY_ANSWER[:10] in m.story_answer


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
def test_short_story_rejected(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    _complete_steps(client, 1, 6)
    data = _valid_step_data(7)
    data["story_answer"] = "hi"
    resp = client.post(_step_url(7), data=data)
    assert resp.status_code == 200
    assert CrushConnectMembership.objects.get(user=me).onboarded_at is None


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
def test_candidate_completion_redirects_to_catalogue_with_email(client, settings, mailoutbox):
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


# ---------------------------------------------------------------------------
# Gate (ported)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_step_redirects_to_teaser_when_flag_off(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
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
        username="me", preferred_genders=["F"], onboarded=False,
        premium=False, has_luxid=False,
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


def _weight(cand, *, viewer_languages=frozenset(), viewer_interest_ids=frozenset(),
            match_scores=None, today=None):
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
    assert _weight(cand, viewer_languages=frozenset({"fr"})) == pytest.approx(SHARED_LANGUAGE_BOOST)
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
    assert _weight(cand, match_scores={cand.pk: 0.9}) == pytest.approx(MATCHSCORE_NEUTRAL + 0.9)
    assert _weight(cand, match_scores={cand.pk: 0.1}) == pytest.approx(MATCHSCORE_NEUTRAL + 0.1)
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
def test_edit_page_renders_for_onboarded(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(PROFILE_EDIT_URL)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_edit_section_save_roundtrip_keeps_onboarded_at(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    before = CrushConnectMembership.objects.get(user=me).onboarded_at
    resp = client.post(PROFILE_EDIT_URL + "?section=intention", data={
        "section": "intention",
        "relationship_goal": "open",
    })
    assert resp.status_code in (301, 302)
    m = CrushConnectMembership.objects.get(user=me)
    assert m.relationship_goal == "open"
    assert m.onboarded_at == before  # untouched


@pytest.mark.django_db
def test_edit_story_section_saves_without_consent(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    prompt = SparkPrompt.objects.filter(is_active=True).first()
    resp = client.post(PROFILE_EDIT_URL + "?section=story", data={
        "section": "story",
        "story_prompt": prompt.pk,
        "story_answer": "A brand new sentence about my evenings.",
        # no confirm_terms — edit mode drops the consent gate
    })
    assert resp.status_code in (301, 302)
    assert "evenings" in CrushConnectMembership.objects.get(user=me).story_answer


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
    assert ConnectInterest.objects.get(pk=pks[0]).label in body


@pytest.mark.django_db
def test_drop_card_falls_back_to_profile_chips(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    _staff_client(client)
    target = _make_user(username="t2", gender="F")
    # Membership has no languages/interests → fall back to the profile fields.
    p = target.crushprofile
    p.event_languages = ["en"]
    p.interests = "hiking, cooking"
    p.save()

    resp = client.get(f"/en/dev/connect-card/{target.pk}/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "EN" in body  # |upper fallback
    assert "hiking" in body


@pytest.mark.django_db
def test_catalogue_status_shows_chips_and_edit_link(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True, premium=False)
    _login_eligible(client, me)
    m = me.crush_connect_membership
    m.languages = ["lu"]
    m.story_answer = "A line about me and my evenings."
    m.save()
    m.interests.set(_interest_pks(1))

    resp = client.get("/en/crush-connect/catalogue/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Lëtzebuergesch" in body
    assert "/crush-connect/profile/" in body


@pytest.mark.django_db
def test_admin_pages_load(client):
    """The new fieldsets / ConnectInterest admin render without error."""
    admin_user = User.objects.create_superuser(
        username="root", email="root@example.com", password="x"
    )
    client.force_login(admin_user)

    me = _make_user(username="member", preferred_genders=["F"], onboarded=True)
    m = me.crush_connect_membership
    m.interests.set(_interest_pks(2))

    # ConnectInterest changelist + add.
    assert client.get("/crush-admin/crush_lu/connectinterest/").status_code == 200
    assert client.get("/crush-admin/crush_lu/connectinterest/add/").status_code == 200
    # Membership changelist + change form (exercises the new fieldsets).
    assert client.get("/crush-admin/crush_lu/crushconnectmembership/").status_code == 200
    assert client.get(
        f"/crush-admin/crush_lu/crushconnectmembership/{m.pk}/change/"
    ).status_code == 200
