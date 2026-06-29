"""
Tests for the Crush Connect feature.

M1: eligible-pool query, SparkPrompt seed, CrushConnectMembership gating.
Later milestones append to this module.
"""

from datetime import date, timedelta

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.utils import timezone

# View tests use /en/crush-connect/… URLs which only resolve under urls_crush.
pytestmark = pytest.mark.urls("azureproject.urls_crush")

from crush_lu.models import (
    ConnectDailyDrop,
    CrushCoach,
    CrushConnectMembership,
    CrushProfile,
    EventConnection,
    EventRegistration,
    MeetupEvent,
    SparkPrompt,
)
from crush_lu.services.crush_connect import (
    DAILY_DROP_SIZE,
    get_eligible_pool,
    get_or_create_daily_drop,
)


User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(title="Past Event"):
    return MeetupEvent.objects.create(
        title=title,
        description="x",
        event_type="mixer",
        date_time=timezone.now() - timedelta(days=14),
        location="Luxembourg",
        address="1 Test St",
        max_participants=20,
        registration_deadline=timezone.now() - timedelta(days=16),
        is_published=True,
    )


def _get_coach():
    """A shared CrushCoach used to make users 'premium' (coach-assigned).

    Uses get_or_create (no module-level cache) so it respects each test's
    transaction — a cached Python object would dangle after rollback.
    """
    coach_user, _ = User.objects.get_or_create(
        username="cc_coach", defaults={"email": "cc_coach@example.com"}
    )
    coach, _ = CrushCoach.objects.get_or_create(
        user=coach_user,
        defaults={
            "bio": "Test coach",
            "specializations": "General",
            "phone_number": "+352123456",
            "is_active": True,
        },
    )
    return coach


def _make_user(
    *,
    username,
    gender="M",
    dob=date(1995, 5, 15),
    is_approved=True,
    preferred_genders=None,
    preferred_age_min=18,
    preferred_age_max=99,
    onboarded=True,
    excluded_by_coach=False,
    last_login_days_ago=1,
    premium=True,
    has_luxid=True,
):
    """
    Build a user ready to participate in Crush Connect by default:
    verified + LuxID-linked + PREMIUM (coach assigned) + onboarded membership
    + recent last_login. Receiving Drops requires premium; appearing in the
    candidate catalogue requires LuxID — the default user qualifies for both.
    Pass ``premium=False`` / ``has_luxid=False`` / ``onboarded=False`` /
    ``last_login_days_ago=None`` for negative tests.
    """
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        first_name=username.title(),
    )
    if last_login_days_ago is not None:
        user.last_login = timezone.now() - timedelta(days=last_login_days_ago)
        user.save(update_fields=["last_login"])

    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=dob,
        gender=gender,
        location="Luxembourg City",
        is_approved=is_approved,
        is_active=True,
        preferred_genders=preferred_genders if preferred_genders is not None else [],
        preferred_age_min=preferred_age_min,
        preferred_age_max=preferred_age_max,
    )
    if premium:
        profile.assigned_coach = _get_coach()
        profile.assigned_coach_at = timezone.now()
        profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
    if has_luxid:
        SocialAccount.objects.create(user=user, provider="luxid", uid=username)

    # Match preferences live on the membership now (the catalogue/profile data
    # split). The profile fields above are kept populated only so divergence
    # tests can set contradictory profile prefs and assert they're ignored.
    CrushConnectMembership.objects.create(
        user=user,
        onboarded_at=timezone.now() if onboarded else None,
        excluded_by_coach=excluded_by_coach,
        preferred_genders=preferred_genders if preferred_genders is not None else [],
        preferred_age_min=preferred_age_min,
        preferred_age_max=preferred_age_max,
    )
    return user


def _mark_attended(user, event=None):
    event = event or _make_event(title=f"Event for {user.username}")
    return EventRegistration.objects.create(user=user, event=event, status="attended")


# ---------------------------------------------------------------------------
# SparkPrompt seed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_seed_prompts_loaded():
    """The 0140 migration should seed at least 10 active prompts in EN/DE/FR."""
    prompts = SparkPrompt.objects.filter(is_active=True)
    assert prompts.count() >= 10
    for p in prompts:
        assert p.text_en, f"Prompt {p.pk} missing EN"
        assert p.text_de, f"Prompt {p.pk} missing DE"
        assert p.text_fr, f"Prompt {p.pk} missing FR"


# ---------------------------------------------------------------------------
# Eligible pool — base eligibility (the requester themselves)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pool_empty_when_requester_has_no_profile():
    user = User.objects.create_user(username="noprof", email="np@example.com", password="x")
    assert list(get_eligible_pool(user)) == []


@pytest.mark.django_db
def test_pool_empty_when_requester_not_approved():
    user = _make_user(username="pending", is_approved=False)
    assert list(get_eligible_pool(user)) == []


@pytest.mark.django_db
def test_pool_empty_when_requester_not_premium():
    # Crush Connect is premium-only: a verified user without a coach is excluded.
    user = _make_user(username="not_premium", premium=False)
    assert list(get_eligible_pool(user)) == []


# ---------------------------------------------------------------------------
# Eligible pool — base eligibility (the targets)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pool_excludes_self():
    me = _make_user(username="me", gender="M", preferred_genders=["F", "M"])
    _mark_attended(me)
    assert me not in get_eligible_pool(me)


@pytest.mark.django_db
def test_pool_excludes_unapproved_targets():
    me = _make_user(username="me", preferred_genders=["F", "M"])
    _mark_attended(me)
    unapproved = _make_user(username="unapproved", is_approved=False)
    _mark_attended(unapproved)
    assert unapproved not in get_eligible_pool(me)


@pytest.mark.django_db
def test_pool_includes_non_premium_luxid_targets():
    # Asymmetric catalogue: candidates don't need premium — LuxID + opt-in is
    # the ticket into the pool. Only RECEIVING drops requires premium.
    me = _make_user(username="me", preferred_genders=["F", "M"])
    non_premium = _make_user(username="nonprem", premium=False, has_luxid=True)
    assert non_premium in get_eligible_pool(me)


@pytest.mark.django_db
def test_pool_excludes_targets_without_luxid():
    # LuxID is mandatory for the candidate catalogue — even premium members
    # drop out of OTHERS' pools until they link LuxID.
    me = _make_user(username="me", preferred_genders=["F", "M"])
    no_luxid_premium = _make_user(username="noluxprem", premium=True, has_luxid=False)
    no_luxid_free = _make_user(username="noluxfree", premium=False, has_luxid=False)
    pool = get_eligible_pool(me)
    assert no_luxid_premium not in pool
    assert no_luxid_free not in pool


@pytest.mark.django_db
def test_pool_empty_when_requester_not_premium_even_with_luxid():
    # LuxID alone never unlocks receiving drops — that stays premium-only.
    me = _make_user(username="luxonly", premium=False, has_luxid=True)
    assert list(get_eligible_pool(me)) == []


@pytest.mark.django_db
def test_pool_excludes_generic_oidc_account_not_scoped_to_luxid():
    # The generic openid_connect provider is shared with non-LuxID apps
    # (e.g. LinkedIn on Entreprinder) — a bare openid_connect account must
    # NOT count as LuxID for the catalogue.
    me = _make_user(username="me", preferred_genders=["F", "M"])
    other = _make_user(username="generic_oidc", has_luxid=False)
    SocialAccount.objects.create(
        user=other, provider="openid_connect", uid="generic_oidc"
    )
    assert other not in get_eligible_pool(me)


@pytest.mark.django_db
def test_pool_includes_oidc_account_scoped_to_luxid_app():
    # openid_connect accounts DO count when their token belongs to the
    # SocialApp configured as LuxID (provider_id='luxid') — the production
    # OIDC configuration.
    from allauth.socialaccount.models import SocialApp, SocialToken

    me = _make_user(username="me", preferred_genders=["F", "M"])
    other = _make_user(username="oidc_luxid", has_luxid=False)
    app = SocialApp.objects.create(
        provider="openid_connect",
        provider_id="luxid",
        name="LuxID",
        client_id="test",
        secret="test",
    )
    acct = SocialAccount.objects.create(
        user=other, provider="openid_connect", uid="oidc_luxid"
    )
    SocialToken.objects.create(account=acct, app=app, token="tok")
    assert other in get_eligible_pool(me)


@pytest.mark.django_db
def test_pool_does_not_require_shared_event():
    """Cardinal rule of M1 per product-owner: pool is community-wide."""
    me = _make_user(username="me", preferred_genders=["F", "M"])
    _mark_attended(me, _make_event(title="Event A"))

    other = _make_user(username="other", gender="M", preferred_genders=["F", "M"])
    _mark_attended(other, _make_event(title="Event B"))  # DIFFERENT event

    assert other in get_eligible_pool(me)


# ---------------------------------------------------------------------------
# Eligible pool — gender preference (mutual)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pool_filters_by_requester_gender_preference():
    me = _make_user(username="me", gender="M", preferred_genders=["F"])
    _mark_attended(me)

    her = _make_user(username="her", gender="F", preferred_genders=["M"])
    _mark_attended(her)
    him = _make_user(username="him", gender="M", preferred_genders=["M"])
    _mark_attended(him)

    pool = list(get_eligible_pool(me))
    assert her in pool
    assert him not in pool


@pytest.mark.django_db
def test_pool_filters_by_target_gender_preference():
    me = _make_user(username="me", gender="M", preferred_genders=["F"])
    _mark_attended(me)

    # She prefers women only — she should be excluded from my pool
    she_prefers_women = _make_user(
        username="she_prefers_women", gender="F", preferred_genders=["F"]
    )
    _mark_attended(she_prefers_women)
    # She prefers men — included
    she_prefers_men = _make_user(
        username="she_prefers_men", gender="F", preferred_genders=["M"]
    )
    _mark_attended(she_prefers_men)
    # She has no preference (empty list) — included
    she_no_pref = _make_user(
        username="she_no_pref", gender="F", preferred_genders=[]
    )
    _mark_attended(she_no_pref)

    pool = list(get_eligible_pool(me))
    assert she_prefers_women not in pool
    assert she_prefers_men in pool
    assert she_no_pref in pool


@pytest.mark.django_db
def test_pool_empty_preferred_genders_means_no_filter():
    """An empty preferred_genders list means 'no preference' — see all genders."""
    me = _make_user(username="me", gender="M", preferred_genders=[])
    _mark_attended(me)

    her = _make_user(username="her", gender="F", preferred_genders=["M"])
    _mark_attended(her)
    him = _make_user(username="him", gender="M", preferred_genders=["M"])
    _mark_attended(him)

    pool = list(get_eligible_pool(me))
    assert her in pool
    assert him in pool


# ---------------------------------------------------------------------------
# Eligible pool — age range (mutual)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pool_excludes_targets_outside_requester_age_range():
    today = date.today()
    me = _make_user(
        username="me",
        gender="M",
        preferred_genders=["F"],
        preferred_age_min=28,
        preferred_age_max=35,
    )
    _mark_attended(me)

    too_young = _make_user(
        username="young", gender="F", dob=today.replace(year=today.year - 22),
        preferred_genders=["M"],
    )
    _mark_attended(too_young)
    in_range = _make_user(
        username="in_range", gender="F", dob=today.replace(year=today.year - 30),
        preferred_genders=["M"],
    )
    _mark_attended(in_range)
    too_old = _make_user(
        username="old", gender="F", dob=today.replace(year=today.year - 50),
        preferred_genders=["M"],
    )
    _mark_attended(too_old)

    pool = list(get_eligible_pool(me))
    assert too_young not in pool
    assert in_range in pool
    assert too_old not in pool


# ---------------------------------------------------------------------------
# Eligible pool — onboarding, coach exclusion, inactivity, existing connections
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pool_empty_when_requester_not_onboarded():
    me = _make_user(username="me", preferred_genders=["F", "M"], onboarded=False)
    _mark_attended(me)
    # A perfectly valid target exists, but requester hasn't opted into Connect yet
    her = _make_user(username="her", gender="F", preferred_genders=["M"])
    _mark_attended(her)
    assert list(get_eligible_pool(me)) == []


@pytest.mark.django_db
def test_pool_empty_when_requester_coach_excluded():
    me = _make_user(
        username="me", preferred_genders=["F", "M"], excluded_by_coach=True
    )
    _mark_attended(me)
    her = _make_user(username="her", gender="F", preferred_genders=["M"])
    _mark_attended(her)
    assert list(get_eligible_pool(me)) == []


@pytest.mark.django_db
def test_pool_excludes_not_onboarded_targets():
    me = _make_user(username="me", preferred_genders=["F", "M"])
    _mark_attended(me)
    onboarded = _make_user(username="onboarded", gender="F", preferred_genders=["M"])
    _mark_attended(onboarded)
    not_onboarded = _make_user(
        username="not_onboarded", gender="F", preferred_genders=["M"], onboarded=False
    )
    _mark_attended(not_onboarded)

    pool = list(get_eligible_pool(me))
    assert onboarded in pool
    assert not_onboarded not in pool


@pytest.mark.django_db
def test_pool_excludes_coach_excluded_targets():
    me = _make_user(username="me", preferred_genders=["F", "M"])
    _mark_attended(me)
    fine = _make_user(username="fine", gender="F", preferred_genders=["M"])
    _mark_attended(fine)
    excluded = _make_user(
        username="excluded",
        gender="F",
        preferred_genders=["M"],
        excluded_by_coach=True,
    )
    _mark_attended(excluded)

    pool = list(get_eligible_pool(me))
    assert fine in pool
    assert excluded not in pool


@pytest.mark.django_db
def test_pool_excludes_inactive_targets():
    me = _make_user(username="me", preferred_genders=["F", "M"])
    _mark_attended(me)
    active = _make_user(
        username="active", gender="F", preferred_genders=["M"], last_login_days_ago=2
    )
    _mark_attended(active)
    stale = _make_user(
        username="stale", gender="F", preferred_genders=["M"], last_login_days_ago=45
    )
    _mark_attended(stale)
    never_logged_in = _make_user(
        username="never", gender="F", preferred_genders=["M"], last_login_days_ago=None
    )
    _mark_attended(never_logged_in)

    pool = list(get_eligible_pool(me))
    assert active in pool
    assert stale not in pool
    assert never_logged_in not in pool


@pytest.mark.django_db
def test_pool_excludes_targets_with_existing_connection():
    me = _make_user(username="me", preferred_genders=["F", "M"])
    _mark_attended(me)
    her = _make_user(username="her", gender="F", preferred_genders=["M"])
    _mark_attended(her)
    her2 = _make_user(username="her2", gender="F", preferred_genders=["M"])
    _mark_attended(her2)

    # Pre-existing EventConnection in any status removes her from my pool
    shared_event = _make_event(title="Shared")
    EventConnection.objects.create(
        requester=me, recipient=her, event=shared_event, status="pending"
    )
    # Reverse direction also blocks: she requested me at some point
    EventConnection.objects.create(
        requester=her2, recipient=me, event=shared_event, status="declined"
    )

    pool = list(get_eligible_pool(me))
    assert her not in pool
    assert her2 not in pool


@pytest.mark.django_db
def test_pool_excludes_targets_whose_age_range_excludes_requester():
    today = date.today()
    me = _make_user(
        username="me",
        gender="M",
        dob=today.replace(year=today.year - 45),
        preferred_genders=["F"],
        preferred_age_min=18,
        preferred_age_max=99,
    )
    _mark_attended(me)

    # She prefers 25–35; I'm 45 → I should not be in her preference window → she stays out of my pool
    she_wants_younger = _make_user(
        username="younger",
        gender="F",
        dob=today.replace(year=today.year - 30),
        preferred_genders=["M"],
        preferred_age_min=25,
        preferred_age_max=35,
    )
    _mark_attended(she_wants_younger)

    she_open = _make_user(
        username="open",
        gender="F",
        dob=today.replace(year=today.year - 30),
        preferred_genders=["M"],
        preferred_age_min=18,
        preferred_age_max=99,
    )
    _mark_attended(she_open)

    pool = list(get_eligible_pool(me))
    assert she_wants_younger not in pool
    assert she_open in pool


# ---------------------------------------------------------------------------
# Daily Drop — M2
# ---------------------------------------------------------------------------


def _seed_pool_for(me, n=10):
    """Create ``n`` distinct female targets for ``me`` (an M user). Returns the list."""
    out = []
    for i in range(n):
        u = _make_user(
            username=f"target_{i:02d}",
            gender="F",
            preferred_genders=["M"],
        )
        _mark_attended(u)
        out.append(u)
    return out


@pytest.mark.django_db
def test_drop_idempotent_for_same_user_and_date():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _seed_pool_for(me, n=8)
    today = date.today()

    drop_1 = get_or_create_daily_drop(me, drop_date=today)
    drop_2 = get_or_create_daily_drop(me, drop_date=today)

    assert drop_1.pk == drop_2.pk
    assert set(drop_1.recipients.values_list("pk", flat=True)) == set(
        drop_2.recipients.values_list("pk", flat=True)
    )


@pytest.mark.django_db
def test_drop_size_caps_at_daily_drop_size():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _seed_pool_for(me, n=10)
    drop = get_or_create_daily_drop(me, drop_date=date.today())
    assert drop.recipients.count() == DAILY_DROP_SIZE


@pytest.mark.django_db
def test_drop_size_smaller_when_pool_smaller_than_drop_size():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _seed_pool_for(me, n=2)
    drop = get_or_create_daily_drop(me, drop_date=date.today())
    assert drop.recipients.count() == 2


@pytest.mark.django_db
def test_drop_changes_across_dates():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _seed_pool_for(me, n=10)

    today = date.today()
    tomorrow = today + timedelta(days=1)

    drop_today = set(
        get_or_create_daily_drop(me, drop_date=today).recipients.values_list(
            "pk", flat=True
        )
    )
    drop_tomorrow = set(
        get_or_create_daily_drop(me, drop_date=tomorrow).recipients.values_list(
            "pk", flat=True
        )
    )
    # With pool of 10 and drop size 3, the chance of identical sets is ~1 in 120.
    # We assert non-equal; if it ever flakes we'd revisit the seed function.
    assert drop_today != drop_tomorrow


@pytest.mark.django_db
def test_drop_only_contains_pool_members():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    targets = _seed_pool_for(me, n=6)
    # A user who is *not* in the pool (excluded by coach)
    blocked = _make_user(
        username="blocked",
        gender="F",
        preferred_genders=["M"],
        excluded_by_coach=True,
    )
    _mark_attended(blocked)

    drop = get_or_create_daily_drop(me, drop_date=date.today())
    recipients = set(drop.recipients.values_list("pk", flat=True))
    target_pks = {t.pk for t in targets}

    assert recipients.issubset(target_pks)
    assert blocked.pk not in recipients


@pytest.mark.django_db
def test_drop_persists_empty_when_no_pool():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    # No targets exist
    drop = get_or_create_daily_drop(me, drop_date=date.today())
    assert drop is not None
    assert drop.recipients.count() == 0
    # A second call should return the same empty snapshot, not recompute.
    drop_2 = get_or_create_daily_drop(me, drop_date=date.today())
    assert drop.pk == drop_2.pk


@pytest.mark.django_db
def test_drop_is_none_when_user_not_eligible():
    """get_or_create_daily_drop should still create a row (empty), but the
    caller can distinguish 'no candidates' from 'user not in Connect yet'."""
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    drop = get_or_create_daily_drop(me, drop_date=date.today())
    # User not onboarded → empty pool → empty drop snapshot, no recipients.
    assert drop.recipients.count() == 0


@pytest.mark.django_db
def test_drop_unique_constraint_per_day():
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _seed_pool_for(me, n=5)
    today = date.today()
    get_or_create_daily_drop(me, drop_date=today)
    # Idempotency means we should not be able to create a second row
    assert ConnectDailyDrop.objects.filter(user=me, drop_date=today).count() == 1


# ---------------------------------------------------------------------------
# M3 — Drop card preview (debug view + partial)
# ---------------------------------------------------------------------------


def _grant_consent(user):
    """Grant the Crush.lu consent required by middleware. Idempotent."""
    from crush_lu.models import UserDataConsent

    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"crushlu_consent_given": True},
    )


@pytest.mark.django_db
def test_dev_card_preview_requires_staff(client):
    target = _make_user(username="target_card", gender="F", preferred_genders=["M"])
    _mark_attended(target)

    url = f"/en/dev/connect-card/{target.pk}/"
    # Anonymous — redirected to admin login (staff_member_required default)
    resp = client.get(url)
    assert resp.status_code in (302, 301)

    # Non-staff user — also redirected
    plain = User.objects.create_user(
        username="plain_user", email="plain@example.com", password="x"
    )
    _grant_consent(plain)
    client.force_login(plain)
    resp = client.get(url)
    assert resp.status_code in (302, 301)


@pytest.mark.django_db
def test_dev_card_preview_renders_card_for_staff(client):
    # Staff user to view as
    staff = User.objects.create_user(
        username="staffer",
        email="staffer@example.com",
        password="x",
        is_staff=True,
    )
    _grant_consent(staff)
    target = _make_user(username="target_render", gender="F", preferred_genders=["M"])
    _mark_attended(target)
    # Give the target a story so the card renders the italic-quote branch.
    membership = target.crush_connect_membership
    prompt = SparkPrompt.objects.filter(is_active=True).first()
    membership.story_prompt = prompt
    membership.story_answer = "I love foggy walks along the Petrusse valley."
    membership.save(update_fields=["story_prompt", "story_answer"])

    client.force_login(staff)
    resp = client.get(f"/en/dev/connect-card/{target.pk}/")
    assert resp.status_code == 200
    body = resp.content.decode()

    # First name appears (privacy: never full name)
    assert "Target_Render" in body
    # Story answer renders
    assert "foggy walks" in body
    # Age range — never exact age
    assert "30-34" in body or "25-29" in body or "18-24" in body
    # Blurred photo class present (or the gradient-initials fallback if no photo)
    assert "blur-xl" in body or "from-crush-purple" in body
    # Staff-only banner
    assert "Staff preview" in body


@pytest.mark.django_db
def test_dev_card_preview_renders_no_story_fallback(client):
    staff = User.objects.create_user(
        username="staffer2", email="staffer2@example.com", password="x", is_staff=True
    )
    _grant_consent(staff)
    target = _make_user(
        username="target_nostory", gender="F", preferred_genders=["M"]
    )
    _mark_attended(target)
    # Membership exists but no story_answer (default blank)

    client.force_login(staff)
    resp = client.get(f"/en/dev/connect-card/{target.pk}/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "hasn&#x27;t shared a story" in body or "hasn't shared a story" in body


# ---------------------------------------------------------------------------
# M4 — Today's Drop user-facing page
# ---------------------------------------------------------------------------


CONNECT_HOME_URL = "/en/crush-connect/today/"
CONNECT_TEASER_URL = "/en/crush-connect/"


def _login_eligible(client, user):
    """Login a user with all the consent/session boilerplate needed for the urlconf."""
    _grant_consent(user)
    client.force_login(user)


@pytest.mark.django_db
def test_home_requires_login(client):
    resp = client.get(CONNECT_HOME_URL)
    # crush_login_required → redirects to login
    assert resp.status_code in (302, 301)


@pytest.mark.django_db
def test_home_redirects_to_teaser_when_flag_off_for_non_staff(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (302, 301)
    assert CONNECT_TEASER_URL in resp.url


@pytest.mark.django_db
def test_home_redirects_to_onboarding_when_user_not_onboarded(client, settings):
    """Eligible-but-not-onboarded users get a chance to opt in, not the teaser."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/onboarding/" in resp.url


@pytest.mark.django_db
def test_home_redirects_to_teaser_when_user_coach_excluded(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], excluded_by_coach=True
    )
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (302, 301)
    assert CONNECT_TEASER_URL in resp.url


@pytest.mark.django_db
def test_home_renders_drop_with_cards(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    targets = _seed_pool_for(me, n=5)
    _login_eligible(client, me)

    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code == 200
    body = resp.content.decode()

    assert "This Week&#x27;s Drop" in body or "This Week's Drop" in body
    # The drop pinned recipients should match what get_or_create_daily_drop returns.
    # Don't hardcode date.today(): the service dates the drop to "yesterday" before
    # 6am, so fetch the drop the view actually created for this user.
    drop = ConnectDailyDrop.objects.filter(user=me).latest("drop_date")
    expected_firstnames = {t.first_name for t in drop.recipients.all()}
    for fn in expected_firstnames:
        assert fn in body


@pytest.mark.django_db
def test_home_renders_empty_state_when_no_pool(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    # No targets exist
    _login_eligible(client, me)

    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "No Drop for this week" in body
    assert "Browse upcoming events" in body


@pytest.mark.django_db
def test_home_idempotent_across_refreshes(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _seed_pool_for(me, n=8)
    _login_eligible(client, me)

    resp_1 = client.get(CONNECT_HOME_URL)
    resp_2 = client.get(CONNECT_HOME_URL)
    # Same recipients across refreshes (Drop is pinned)
    assert ConnectDailyDrop.objects.filter(user=me).count() == 1
    drop_pks = list(
        ConnectDailyDrop.objects.get(user=me).recipients.values_list("pk", flat=True)
    )
    assert len(drop_pks) == DAILY_DROP_SIZE


@pytest.mark.django_db
def test_home_staff_bypass_when_flag_off(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    staff = User.objects.create_user(
        username="staff_preview",
        email="staff_preview@example.com",
        password="x",
        is_staff=True,
    )
    _grant_consent(staff)
    client.force_login(staff)

    resp = client.get(CONNECT_HOME_URL)
    # Staff sees the page even with flag off and no membership
    assert resp.status_code == 200
    assert "Staff preview" in resp.content.decode()


# ---------------------------------------------------------------------------
# M4.5 — Onboarding flow
# ---------------------------------------------------------------------------


ONBOARDING_URL = "/en/crush-connect/onboarding/"


@pytest.mark.django_db
def test_onboarding_redirects_to_teaser_when_flag_off(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = False
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(ONBOARDING_URL)
    assert resp.status_code in (302, 301)
    assert CONNECT_TEASER_URL in resp.url


@pytest.mark.django_db
def test_onboarding_redirects_to_teaser_when_ineligible(client, settings):
    """Neither track qualifies: no premium coach AND no LuxID → can't onboard."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me",
        preferred_genders=["F"],
        onboarded=False,
        premium=False,
        has_luxid=False,
    )
    _login_eligible(client, me)

    resp = client.get(ONBOARDING_URL)
    assert resp.status_code in (302, 301)
    assert CONNECT_TEASER_URL in resp.url


@pytest.mark.django_db
def test_onboarding_resume_redirects_eligible_user_to_step(client, settings):
    """The bare onboarding URL is now a smart-resume entry: it routes an
    eligible, not-yet-onboarded user to their current wizard step. Full
    step-by-step coverage lives in test_crush_connect_onboarding.py."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(ONBOARDING_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/onboarding/1/" in resp.url


@pytest.mark.django_db
def test_onboarding_already_onboarded_redirects_to_home(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(ONBOARDING_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/today/" in resp.url


@pytest.mark.django_db
def test_teaser_auto_redirects_eligible_onboarded_user_to_home(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(CONNECT_TEASER_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/today/" in resp.url


@pytest.mark.django_db
def test_teaser_auto_redirects_eligible_not_onboarded_to_onboarding(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(CONNECT_TEASER_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/onboarding/" in resp.url


# ---------------------------------------------------------------------------
# Asymmetric catalogue — candidate track (LuxID, no Premium)
# ---------------------------------------------------------------------------


CATALOGUE_STATUS_URL = "/en/crush-connect/catalogue/"


@pytest.mark.django_db
def test_onboarding_resume_redirects_luxid_candidate_to_step(client, settings):
    """LuxID-only (candidate-track) members may opt in too — resume routes
    them into the wizard. Candidate completion → catalogue is covered in
    test_crush_connect_onboarding.py."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=False, premium=False
    )
    _login_eligible(client, me)

    resp = client.get(ONBOARDING_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/onboarding/1/" in resp.url


@pytest.mark.django_db
def test_catalogue_status_renders_for_onboarded_candidate(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=True, premium=False
    )
    _login_eligible(client, me)

    resp = client.get(CATALOGUE_STATUS_URL)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "in the mix" in body.lower()


@pytest.mark.django_db
def test_catalogue_status_redirects_premium_to_home(client, settings):
    """Premium receivers don't belong on the catalogue page — Drop instead."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=True)
    _login_eligible(client, me)

    resp = client.get(CATALOGUE_STATUS_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/today/" in resp.url


@pytest.mark.django_db
def test_catalogue_status_redirects_to_onboarding_if_not_onboarded(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=False, premium=False
    )
    _login_eligible(client, me)

    resp = client.get(CATALOGUE_STATUS_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/onboarding/" in resp.url


@pytest.mark.django_db
def test_catalogue_status_redirects_to_teaser_without_luxid(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me",
        preferred_genders=["F"],
        onboarded=True,
        premium=False,
        has_luxid=False,
    )
    _login_eligible(client, me)

    resp = client.get(CATALOGUE_STATUS_URL)
    assert resp.status_code in (302, 301)
    assert CONNECT_TEASER_URL in resp.url


@pytest.mark.django_db
def test_home_redirects_candidate_only_to_catalogue_status(client, settings):
    """Onboarded LuxID members without Premium never see Today's Drop."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=True, premium=False
    )
    _login_eligible(client, me)

    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/catalogue/" in resp.url


@pytest.mark.django_db
def test_teaser_auto_redirects_onboarded_candidate_to_catalogue(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=True, premium=False
    )
    _login_eligible(client, me)

    resp = client.get(CONNECT_TEASER_URL)
    assert resp.status_code in (302, 301)
    assert "/crush-connect/catalogue/" in resp.url


# ---------------------------------------------------------------------------
# Beta tester selection — waitlist fields, admin actions, teaser context
# ---------------------------------------------------------------------------


def _admin_request(user):
    """A POST request usable by admin actions (messages framework attached)."""
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    req = RequestFactory().post("/admin/")
    req.user = user
    req.session = {}
    req._messages = FallbackStorage(req)
    return req


@pytest.mark.django_db
def test_admin_select_as_tester_stamps_fields():
    from django.contrib.admin.sites import AdminSite

    from crush_lu.admin.crush_connect import CrushConnectWaitlistAdmin
    from crush_lu.models import CrushConnectWaitlist

    me = _make_user(username="me", premium=False, onboarded=False)
    staff = User.objects.create_user(
        username="cc_staff", email="cc_staff@example.com", password="x", is_staff=True
    )
    entry = CrushConnectWaitlist.objects.create(user=me)

    admin = CrushConnectWaitlistAdmin(CrushConnectWaitlist, AdminSite())
    admin.select_as_tester(
        _admin_request(staff), CrushConnectWaitlist.objects.filter(pk=entry.pk)
    )

    entry.refresh_from_db()
    assert entry.selected_as_tester is True
    assert entry.selected_at is not None


@pytest.mark.django_db
def test_admin_confirm_payment_stamps_confirmed_by():
    from django.contrib.admin.sites import AdminSite

    from crush_lu.admin.crush_connect import CrushConnectWaitlistAdmin
    from crush_lu.models import CrushConnectWaitlist

    me = _make_user(username="me", premium=False, onboarded=False)
    staff = User.objects.create_user(
        username="cc_staff", email="cc_staff@example.com", password="x", is_staff=True
    )
    entry = CrushConnectWaitlist.objects.create(user=me)

    admin = CrushConnectWaitlistAdmin(CrushConnectWaitlist, AdminSite())
    admin.confirm_payment(
        _admin_request(staff), CrushConnectWaitlist.objects.filter(pk=entry.pk)
    )

    entry.refresh_from_db()
    assert entry.payment_confirmed is True
    assert entry.payment_date is not None
    assert entry.confirmed_by_id == staff.id


@pytest.mark.django_db
def test_admin_form_save_stamps_tester_fields():
    """Toggling the booleans in the change form (not the bulk action) still
    stamps selected_at / payment_date / confirmed_by via save_model."""
    from django.contrib.admin.sites import AdminSite

    from crush_lu.admin.crush_connect import CrushConnectWaitlistAdmin
    from crush_lu.models import CrushConnectWaitlist

    me = _make_user(username="me", premium=False, onboarded=False)
    staff = User.objects.create_user(
        username="cc_staff2", email="cc_staff2@example.com", password="x", is_staff=True
    )
    entry = CrushConnectWaitlist.objects.create(user=me)
    entry.selected_as_tester = True
    entry.payment_confirmed = True

    class _Form:
        changed_data = ["selected_as_tester", "payment_confirmed"]

    admin = CrushConnectWaitlistAdmin(CrushConnectWaitlist, AdminSite())
    admin.save_model(_admin_request(staff), entry, _Form(), change=True)

    entry.refresh_from_db()
    assert entry.selected_at is not None
    assert entry.payment_date is not None
    assert entry.confirmed_by_id == staff.id


@pytest.mark.django_db
def test_teaser_context_exposes_tester_status(client, settings):
    """The teaser surfaces the new beta-status flags for the logged-in user."""
    settings.CRUSH_CONNECT_LAUNCHED = False  # stay on the teaser, no fast-path
    from crush_lu.models import CrushConnectWaitlist

    me = _make_user(username="me", premium=False, onboarded=False)
    CrushConnectWaitlist.objects.create(
        user=me, selected_as_tester=True, payment_confirmed=True
    )
    _login_eligible(client, me)

    resp = client.get(CONNECT_TEASER_URL)
    assert resp.status_code == 200
    assert resp.context["on_waitlist"] is True
    assert resp.context["selected_as_tester"] is True
    assert resp.context["tester_payment_confirmed"] is True
    assert "Selected as a beta tester" in resp.content.decode()


@pytest.mark.django_db
def test_drop_new_member_boost_increases_selection_frequency():
    """The new-member boost must make the newcomer surface in *more* Drops than
    they would without it.

    The Drop RNG is seeded deterministically from ``user.pk + drop_date`` (see
    ``get_or_create_daily_drop``), so any absolute hit-count threshold is
    fragile — including a comparison against the established-member average,
    which (because every Drop holds exactly ``DAILY_DROP_SIZE`` of the 5
    candidates) reduces algebraically to the same strict ``> 60%`` bound and can
    tie/fail on an unlucky date or PK assignment.

    Instead we run each date twice with the *identical* seed (same viewer +
    date), once with the newcomer inside its boost window and once aged out.
    Raising a candidate's weight only ever raises its A-Res selection key
    (``log(u) / w`` with the same ``u``), so the boost can only *add* the
    newcomer to a Drop, never drop them: ``boosted >= unboosted`` holds for
    every seed by construction, and the boost is doing its job iff it strictly
    adds the newcomer on at least one date. This is a monotonic, date/PK-robust
    property rather than a noisy threshold."""
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)

    # 4 old members onboarded long ago
    old = []
    for i in range(4):
        u = _make_user(
            username=f"old_{i}",
            gender="F",
            preferred_genders=["M"],
        )
        _mark_attended(u)
        # Backdate their onboarding past the boost window
        m = u.crush_connect_membership
        m.onboarded_at = timezone.now() - timedelta(days=120)
        m.save(update_fields=["onboarded_at"])
        old.append(u)

    # 1 new member onboarded today (boosted)
    newbie = _make_user(username="newbie", gender="F", preferred_genders=["M"])
    _mark_attended(newbie)

    newbie_membership = newbie.crush_connect_membership

    def _newcomer_surfaced(drop_date, onboarded_at):
        # Same (viewer, date) → same seed and same per-candidate random draws;
        # only the newcomer's onboarding (hence its boost weight) varies.
        newbie_membership.onboarded_at = onboarded_at
        newbie_membership.save(update_fields=["onboarded_at"])
        ConnectDailyDrop.objects.filter(user=me, drop_date=drop_date).delete()
        drop = get_or_create_daily_drop(me, drop_date=drop_date)
        return newbie in drop.recipients.all()

    today = date.today()
    trials = 90
    boosted = 0
    unboosted = 0
    for offset in range(trials):
        d = today + timedelta(days=offset)
        # Boosted: onboarded just before the drop date (inside the window).
        if _newcomer_surfaced(d, timezone.now() + timedelta(days=offset - 1)):
            boosted += 1
        # Control: onboarded long ago, well outside the boost window.
        if _newcomer_surfaced(d, timezone.now() - timedelta(days=120)):
            unboosted += 1

    # boosted >= unboosted is guaranteed seed-by-seed (boosting only lifts the
    # newcomer's selection key); a strictly greater total proves the boost
    # actually surfaces them on dates where they'd otherwise miss the cut.
    assert boosted > unboosted, (
        f"New-member boost did not increase surfacing: boosted={boosted} vs "
        f"unboosted={unboosted} over {trials} identically-seeded Drops"
    )


# ---------------------------------------------------------------------------
# M5 — Curiosity Sparks
# ---------------------------------------------------------------------------


from crush_lu.models import CuriositySpark
from crush_lu.services.crush_connect import (
    can_send_spark,
    respond_to_spark,
    send_spark,
)


def _surface_in_drop(sender, target):
    """Create a Drop snapshot so ``target`` was 'surfaced' to ``sender``."""
    drop, _ = ConnectDailyDrop.objects.get_or_create(
        user=sender, drop_date=timezone.localdate()
    )
    drop.recipients.add(target)
    return drop


SPARKS_RECEIVED_URL = "/en/crush-connect/sparks/"


@pytest.mark.django_db
def test_can_send_spark_requires_surfaced_target():
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    allowed, reason = can_send_spark(me, her)
    assert (allowed, reason) == (False, "not_surfaced")

    _surface_in_drop(me, her)
    allowed, reason = can_send_spark(me, her)
    assert (allowed, reason) == (True, "ok")


@pytest.mark.django_db
def test_can_send_spark_requires_premium_sender():
    me = _make_user(username="me", premium=False)  # candidate-only
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    allowed, reason = can_send_spark(me, her)
    assert (allowed, reason) == (False, "not_receiver")


@pytest.mark.django_db
def test_send_spark_blocks_duplicates_both_directions():
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F")
    _surface_in_drop(me, her)
    send_spark(me, her, message="Curious!")

    allowed, reason = can_send_spark(me, her)
    assert (allowed, reason) == (False, "already_sparked")

    # Reverse direction also blocked (her drop surfaces me)
    _surface_in_drop(her, me)
    allowed, reason = can_send_spark(her, me)
    assert (allowed, reason) == (False, "already_sparked")


@pytest.mark.django_db
def test_send_spark_creates_notification_for_recipient():
    from crush_lu.models import Notification

    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her, message="Your story made me smile.")

    assert spark.status == "pending"
    assert spark.drop is not None
    notif = Notification.objects.filter(
        user=her, notification_type="connect_spark_received"
    )
    assert notif.count() == 1


@pytest.mark.django_db
def test_respond_accept_notifies_sender():
    from crush_lu.models import Notification

    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her)

    respond_to_spark(spark, accept=True)
    spark.refresh_from_db()
    assert spark.status == "accepted"
    assert spark.responded_at is not None
    assert Notification.objects.filter(
        user=me, notification_type="connect_spark_accepted"
    ).exists()


@pytest.mark.django_db
def test_respond_decline_is_silent_for_sender():
    from crush_lu.models import Notification

    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her)

    respond_to_spark(spark, accept=False)
    spark.refresh_from_db()
    assert spark.status == "declined"
    assert not Notification.objects.filter(
        user=me, notification_type="connect_spark_accepted"
    ).exists()


@pytest.mark.django_db
def test_respond_is_idempotent_after_decision():
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her)
    respond_to_spark(spark, accept=False)
    respond_to_spark(spark, accept=True)  # must NOT flip a decided spark
    spark.refresh_from_db()
    assert spark.status == "declined"


@pytest.mark.django_db
def test_spark_compose_view_sends_spark(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    _login_eligible(client, me)

    url = f"/en/crush-connect/spark/{her.pk}/"
    resp = client.get(url)
    assert resp.status_code == 200

    resp = client.post(url, data={"message": "Your story made me curious."})
    assert resp.status_code in (302, 301)
    spark = CuriositySpark.objects.get(sender=me, recipient=her)
    assert spark.message == "Your story made me curious."


@pytest.mark.django_db
def test_spark_compose_rejected_when_not_surfaced(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _login_eligible(client, me)

    resp = client.post(
        f"/en/crush-connect/spark/{her.pk}/", data={"message": "hi"}
    )
    assert resp.status_code in (302, 301)
    assert not CuriositySpark.objects.filter(sender=me, recipient=her).exists()


@pytest.mark.django_db
def test_sparks_received_page_lists_pending_and_responds(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her, message="Hello there")

    _login_eligible(client, her)
    resp = client.get(SPARKS_RECEIVED_URL)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Hello there" in body
    assert me.first_name in body

    resp = client.post(
        f"/en/crush-connect/sparks/{spark.pk}/respond/", data={"action": "accept"}
    )
    assert resp.status_code in (302, 301)
    spark.refresh_from_db()
    assert spark.status == "accepted"


@pytest.mark.django_db
def test_spark_respond_forbidden_for_non_recipient(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    other = _make_user(username="other", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her)

    _login_eligible(client, other)
    resp = client.post(
        f"/en/crush-connect/sparks/{spark.pk}/respond/", data={"action": "accept"}
    )
    assert resp.status_code == 404
    spark.refresh_from_db()
    assert spark.status == "pending"


@pytest.mark.django_db
def test_home_card_shows_spark_cta_and_sent_state(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    _mark_attended(me)
    targets = _seed_pool_for(me, n=3)
    _login_eligible(client, me)

    resp = client.get(CONNECT_HOME_URL)
    body = resp.content.decode()
    assert "Send a Curiosity Spark" in body
    assert "coming soon" not in body


@pytest.mark.django_db
def test_can_send_spark_rechecks_recipient_eligibility():
    """Drop snapshots are immutable — eligibility lost AFTER surfacing
    (rejection, LuxID unlink, coach exclusion) must block new Sparks."""
    me = _make_user(username="me", preferred_genders=["F"])

    # Rejected after surfacing
    rejected = _make_user(username="rejected", gender="F", premium=False)
    _surface_in_drop(me, rejected)
    rejected.crushprofile.is_approved = False
    rejected.crushprofile.verification_status = "rejected"
    rejected.crushprofile.save(
        update_fields=["is_approved", "verification_status"]
    )
    assert can_send_spark(me, rejected) == (False, "recipient_unavailable")

    # LuxID unlinked after surfacing
    unlinked = _make_user(username="unlinked", gender="F", premium=False)
    _surface_in_drop(me, unlinked)
    SocialAccount.objects.filter(user=unlinked).delete()
    assert can_send_spark(me, unlinked) == (False, "recipient_unavailable")

    # Coach-excluded after surfacing
    excluded = _make_user(username="excluded", gender="F", premium=False)
    _surface_in_drop(me, excluded)
    excluded.crush_connect_membership.excluded_by_coach = True
    excluded.crush_connect_membership.save(update_fields=["excluded_by_coach"])
    assert can_send_spark(me, excluded) == (False, "recipient_unavailable")


@pytest.mark.django_db
def test_respond_accept_blocked_when_recipient_lost_eligibility():
    """An ineligible recipient must not be able to trigger the mutual
    notification/coach queue from an old pending Spark."""
    from crush_lu.models import Notification

    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her)

    SocialAccount.objects.filter(user=her).delete()  # LuxID unlinked

    respond_to_spark(spark, accept=True)
    spark.refresh_from_db()
    assert spark.status == "pending"  # accept refused, no flip
    assert not Notification.objects.filter(
        user=me, notification_type="connect_spark_accepted"
    ).exists()


@pytest.mark.django_db
def test_sparks_views_blocked_when_recipient_lost_eligibility(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her)

    her.crushprofile.is_approved = False
    her.crushprofile.verification_status = "rejected"
    her.crushprofile.save(update_fields=["is_approved", "verification_status"])

    _login_eligible(client, her)
    resp = client.get(SPARKS_RECEIVED_URL)
    assert resp.status_code in (302, 301)
    assert CONNECT_TEASER_URL in resp.url

    resp = client.post(
        f"/en/crush-connect/sparks/{spark.pk}/respond/", data={"action": "accept"}
    )
    assert resp.status_code in (302, 301)
    spark.refresh_from_db()
    assert spark.status == "pending"


@pytest.mark.django_db
def test_can_send_spark_blocks_inactive_recipient():
    """The 30-day activity gate applies to Sparks, not just Drops — an old
    snapshot or bookmarked compose URL must not reach inactive members."""
    me = _make_user(username="me", preferred_genders=["F"])
    dormant = _make_user(
        username="dormant", gender="F", premium=False, last_login_days_ago=40
    )
    _surface_in_drop(me, dormant)
    assert can_send_spark(me, dormant) == (False, "recipient_unavailable")


@pytest.mark.django_db
def test_respond_accept_blocked_when_sender_lost_eligibility():
    """A sender who lost Premium (or got rejected/excluded) after sending
    must not reach the accepted-sparks coach queue via an old pending Spark."""
    from crush_lu.models import Notification

    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    spark = send_spark(me, her)

    me.crushprofile.assigned_coach = None
    me.crushprofile.save(update_fields=["assigned_coach"])

    respond_to_spark(spark, accept=True)
    spark.refresh_from_db()
    assert spark.status == "pending"
    assert not Notification.objects.filter(
        user=me, notification_type="connect_spark_accepted"
    ).exists()


@pytest.mark.django_db
def test_sparks_received_hides_sparks_from_ineligible_sender(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"])
    her = _make_user(username="her", gender="F", premium=False)
    _surface_in_drop(me, her)
    send_spark(me, her, message="Hello there")

    me.crushprofile.assigned_coach = None
    me.crushprofile.save(update_fields=["assigned_coach"])

    _login_eligible(client, her)
    resp = client.get(SPARKS_RECEIVED_URL)
    assert resp.status_code == 200
    assert "Hello there" not in resp.content.decode()


# ---------------------------------------------------------------------------
# M7 — Coach Picks
# ---------------------------------------------------------------------------


from crush_lu.models import ConnectCoachPick
from crush_lu.services.crush_connect import (
    get_active_coach_pick,
    propose_coach_pick,
    respond_to_coach_pick,
)


def _coach_for(member):
    return member.crushprofile.assigned_coach


@pytest.mark.django_db
def test_propose_coach_pick_validates_pool_and_pair():
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    candidate = _make_user(username="cand", gender="F", premium=False)
    stranger = _make_user(
        username="stranger", gender="F", premium=False, has_luxid=False
    )

    with pytest.raises(ValueError, match="candidate_not_eligible"):
        propose_coach_pick(coach, member, stranger)

    pick = propose_coach_pick(coach, member, candidate, note="Great energy match")
    assert pick.status == "proposed"

    with pytest.raises(ValueError, match="already_picked"):
        propose_coach_pick(coach, member, candidate)


@pytest.mark.django_db
def test_new_pick_withdraws_previous_proposal():
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    c1 = _make_user(username="c1", gender="F", premium=False)
    c2 = _make_user(username="c2", gender="F", premium=False)

    p1 = propose_coach_pick(coach, member, c1)
    p2 = propose_coach_pick(coach, member, c2)
    p1.refresh_from_db()
    assert p1.status == "withdrawn"
    assert get_active_coach_pick(member).pk == p2.pk


@pytest.mark.django_db
def test_pick_respond_notifies_coach_both_ways():
    from crush_lu.models import Notification

    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    pick = propose_coach_pick(coach, member, cand)

    # Member got the bell
    assert Notification.objects.filter(
        user=member, notification_type="connect_coach_pick"
    ).exists()

    respond_to_coach_pick(pick, accept=True)
    pick.refresh_from_db()
    assert pick.status == "accepted"
    assert Notification.objects.filter(
        user=coach.user, notification_type="connect_coach_pick_response"
    ).exists()
    # Idempotent: decline after accept is a no-op
    respond_to_coach_pick(pick, accept=False)
    pick.refresh_from_db()
    assert pick.status == "accepted"


@pytest.mark.django_db
def test_home_shows_coach_pick_instead_of_drop(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    _seed_pool_for(member, n=4)
    cand = _make_user(
        username="pickme", gender="F", premium=False, preferred_genders=["M"]
    )
    propose_coach_pick(coach, member, cand, note="You both love hiking")

    _login_eligible(client, member)
    resp = client.get(CONNECT_HOME_URL)
    body = resp.content.decode()
    assert "Your Coach&#x27;s Pick" in body or "Your Coach's Pick" in body
    assert "Pickme" in body
    assert "You both love hiking" in body
    assert "Send a Curiosity Spark" not in body  # drop replaced


@pytest.mark.django_db
def test_member_accepts_pick_via_view(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    pick = propose_coach_pick(coach, member, cand)

    _login_eligible(client, member)
    resp = client.post(
        f"/en/crush-connect/pick/{pick.pk}/respond/", data={"action": "accept"}
    )
    assert resp.status_code in (302, 301)
    pick.refresh_from_db()
    assert pick.status == "accepted"


@pytest.mark.django_db
def test_pick_respond_forbidden_for_non_member(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    other = _make_user(username="other", gender="F", premium=False)
    pick = propose_coach_pick(coach, member, cand)

    _login_eligible(client, other)
    resp = client.post(
        f"/en/crush-connect/pick/{pick.pk}/respond/", data={"action": "accept"}
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_coach_connect_views_require_coach_and_own_member(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)

    # Non-coach is redirected away
    _login_eligible(client, member)
    resp = client.get("/en/coach/connect/")
    assert resp.status_code in (302, 301)

    # The coach can curate and propose
    _login_eligible(client, coach.user)
    resp = client.get("/en/coach/connect/")
    assert resp.status_code == 200
    resp = client.get(f"/en/coach/connect/member/{member.pk}/")
    assert resp.status_code == 200
    assert "Cand" in resp.content.decode()

    resp = client.post(
        f"/en/coach/connect/member/{member.pk}/",
        data={"candidate_id": cand.pk, "note": "Perfect fit"},
    )
    assert resp.status_code in (302, 301)
    pick = ConnectCoachPick.objects.get(member=member, candidate=cand)
    assert pick.note == "Perfect fit"


@pytest.mark.django_db
def test_stale_pick_candidate_hides_pick_and_falls_back():
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    propose_coach_pick(coach, member, cand)

    SocialAccount.objects.filter(user=cand).delete()  # LuxID unlinked
    assert get_active_coach_pick(member) is None


@pytest.mark.django_db
def test_no_drop_created_while_pick_active(client, settings):
    """Drop snapshots authorize Sparks — none may be persisted while the
    coach's pick replaces the Drop."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    _seed_pool_for(member, n=4)
    cand = _make_user(
        username="pickme", gender="F", premium=False, preferred_genders=["M"]
    )
    propose_coach_pick(coach, member, cand)

    _login_eligible(client, member)
    resp = client.get(CONNECT_HOME_URL)
    assert resp.status_code == 200
    assert ConnectDailyDrop.objects.filter(user=member).count() == 0


@pytest.mark.django_db
def test_pick_accept_blocked_when_candidate_lost_eligibility():
    from crush_lu.models import Notification

    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    pick = propose_coach_pick(coach, member, cand)

    SocialAccount.objects.filter(user=cand).delete()  # LuxID unlinked

    respond_to_coach_pick(pick, accept=True)
    pick.refresh_from_db()
    assert pick.status == "proposed"
    assert not Notification.objects.filter(
        user=coach.user, notification_type="connect_coach_pick_response"
    ).exists()


@pytest.mark.django_db
def test_pick_accept_blocked_when_either_party_coach_excluded():
    """The panic button (excluded_by_coach) is enforced via is_onboarded
    inside both eligibility helpers — an exclusion after proposal makes
    accept a no-op for candidate AND member exclusions."""
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    pick = propose_coach_pick(coach, member, cand)

    cand.crush_connect_membership.excluded_by_coach = True
    cand.crush_connect_membership.save(update_fields=["excluded_by_coach"])
    respond_to_coach_pick(pick, accept=True)
    pick.refresh_from_db()
    assert pick.status == "proposed"

    # Reset candidate, exclude the member instead
    cand.crush_connect_membership.excluded_by_coach = False
    cand.crush_connect_membership.save(update_fields=["excluded_by_coach"])
    member.crush_connect_membership.excluded_by_coach = True
    member.crush_connect_membership.save(update_fields=["excluded_by_coach"])
    pick = type(pick).objects.get(pk=pick.pk)
    respond_to_coach_pick(pick, accept=True)
    pick.refresh_from_db()
    assert pick.status == "proposed"


@pytest.mark.django_db
def test_pick_hidden_and_unacceptable_after_coach_reassignment():
    """An ex-coach's proposed pick must neither surface nor be acceptable."""
    member = _make_user(username="member", preferred_genders=["F"])
    old_coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    pick = propose_coach_pick(old_coach, member, cand)

    new_coach_user = User.objects.create_user(
        username="coach2", email="coach2@example.com", password="x"
    )
    new_coach = CrushCoach.objects.create(
        user=new_coach_user, bio="b", specializations="g",
        phone_number="+352999999", is_active=True,
    )
    member.crushprofile.assigned_coach = new_coach
    member.crushprofile.save(update_fields=["assigned_coach"])

    assert get_active_coach_pick(member) is None
    respond_to_coach_pick(pick, accept=True)
    pick.refresh_from_db()
    assert pick.status == "proposed"


@pytest.mark.django_db
def test_stale_decline_recorded_but_ex_coach_not_notified():
    from crush_lu.models import Notification

    member = _make_user(username="member", preferred_genders=["F"])
    old_coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    pick = propose_coach_pick(old_coach, member, cand)

    new_coach = CrushCoach.objects.create(
        user=User.objects.create_user(
            username="coach2", email="coach2@example.com", password="x"
        ),
        bio="b", specializations="g", phone_number="+352999998", is_active=True,
    )
    member.crushprofile.assigned_coach = new_coach
    member.crushprofile.save(update_fields=["assigned_coach"])

    respond_to_coach_pick(pick, accept=False)
    pick.refresh_from_db()
    assert pick.status == "declined"  # member's intent honored
    assert not Notification.objects.filter(
        user=old_coach.user, notification_type="connect_coach_pick_response"
    ).exists()


@pytest.mark.django_db
def test_stale_accept_view_shows_no_false_promise(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    pick = propose_coach_pick(coach, member, cand)
    SocialAccount.objects.filter(user=cand).delete()

    _login_eligible(client, member)
    resp = client.post(
        f"/en/crush-connect/pick/{pick.pk}/respond/",
        data={"action": "accept"},
        follow=True,
    )
    body = resp.content.decode()
    assert "no longer available" in body
    assert "arrange your date" not in body


@pytest.mark.django_db
def test_pick_accept_blocked_when_candidate_left_member_pool():
    """Accept re-checks the FULL pool: an EventConnection created after the
    proposal (they already met) makes the accept a no-op."""
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    pick = propose_coach_pick(coach, member, cand)

    EventConnection.objects.create(
        requester=member, recipient=cand, event=_make_event(), status="pending"
    )
    respond_to_coach_pick(pick, accept=True)
    pick.refresh_from_db()
    assert pick.status == "proposed"


@pytest.mark.django_db
def test_pick_hidden_when_candidate_leaves_member_pool():
    """Display uses the same full-pool check as accept — a post-proposal
    EventConnection hides the pick so the member is never stuck on it."""
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    propose_coach_pick(coach, member, cand)

    EventConnection.objects.create(
        requester=member, recipient=cand, event=_make_event(), status="pending"
    )
    assert get_active_coach_pick(member) is None


@pytest.mark.django_db
def test_coach_hub_hides_stale_proposed_pick(client, settings):
    """A proposed pick the member can no longer see must prompt the coach
    to re-pick, not show 'awaiting their answer'."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    coach = _coach_for(member)
    cand = _make_user(username="cand", gender="F", premium=False)
    propose_coach_pick(coach, member, cand)
    SocialAccount.objects.filter(user=cand).delete()  # candidate left pool

    _login_eligible(client, coach.user)
    resp = client.get("/en/coach/connect/")
    body = resp.content.decode()
    assert "awaiting their answer" not in body
    assert "No open pick" in body
