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

    CrushConnectMembership.objects.create(
        user=user,
        onboarded_at=timezone.now() if onboarded else None,
        excluded_by_coach=excluded_by_coach,
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
def test_onboarding_renders_form_for_eligible_user(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    resp = client.get(ONBOARDING_URL)
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Welcome to Crush Connect" in body
    assert "story_prompt" in body
    assert "story_answer" in body


@pytest.mark.django_db
def test_onboarding_submission_stamps_membership(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    prompt = SparkPrompt.objects.filter(is_active=True).first()
    resp = client.post(
        ONBOARDING_URL,
        data={
            "story_prompt": prompt.pk,
            "story_answer": "I love foggy walks along the Pétrusse valley.",
            "confirm_terms": "on",
        },
    )
    assert resp.status_code in (302, 301)
    assert "/crush-connect/today/" in resp.url

    membership = CrushConnectMembership.objects.get(user=me)
    assert membership.onboarded_at is not None
    assert membership.story_prompt_id == prompt.pk
    assert "Pétrusse" in membership.story_answer


@pytest.mark.django_db
def test_onboarding_rejects_short_answer(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="me", preferred_genders=["F"], onboarded=False)
    _mark_attended(me)
    _login_eligible(client, me)

    prompt = SparkPrompt.objects.filter(is_active=True).first()
    resp = client.post(
        ONBOARDING_URL,
        data={
            "story_prompt": prompt.pk,
            "story_answer": "hi",
            "confirm_terms": "on",
        },
    )
    # Form re-renders with error; no membership stamping
    assert resp.status_code == 200
    me.refresh_from_db()
    membership = CrushConnectMembership.objects.get(user=me)
    assert membership.onboarded_at is None


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
def test_onboarding_renders_for_luxid_non_premium_user(client, settings):
    """LuxID-only members may opt in as catalogue candidates."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=False, premium=False
    )
    _login_eligible(client, me)

    resp = client.get(ONBOARDING_URL)
    assert resp.status_code == 200
    assert "story_prompt" in resp.content.decode()


@pytest.mark.django_db
def test_onboarding_redirects_candidate_to_catalogue_status(client, settings):
    """Candidate-track submit lands on the catalogue page, not Today's Drop."""
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(
        username="me", preferred_genders=["F"], onboarded=False, premium=False
    )
    _login_eligible(client, me)

    prompt = SparkPrompt.objects.filter(is_active=True).first()
    resp = client.post(
        ONBOARDING_URL,
        data={
            "story_prompt": prompt.pk,
            "story_answer": "I love foggy walks along the Pétrusse valley.",
            "confirm_terms": "on",
        },
    )
    assert resp.status_code in (302, 301)
    assert "/crush-connect/catalogue/" in resp.url

    membership = CrushConnectMembership.objects.get(user=me)
    assert membership.onboarded_at is not None


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
    assert "catalogue" in body.lower()


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
    """A bulk-rate check: when 1 target is "new" (boosted) and 4 are old,
    sampling Drops across many synthetic dates surfaces the new one more often
    than uniform chance would predict (3/5 = 60%)."""
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

    today = date.today()
    surfaced = 0
    trials = 60
    for offset in range(trials):
        d = today + timedelta(days=offset)
        drop = get_or_create_daily_drop(me, drop_date=d)
        if newbie in drop.recipients.all():
            surfaced += 1

    # Uniform would be ~3/5 = 36 of 60. Boosted (×1.5) should comfortably exceed
    # that. We assert > 40 (≈67%) to leave plenty of slack.
    assert surfaced > 40, f"Boosted newcomer surfaced only {surfaced}/{trials} times"
