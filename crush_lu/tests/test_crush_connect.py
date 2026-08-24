"""Core Crush Connect catalogue and human coach-pick tests."""

from datetime import date, timedelta

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushConnectMembership,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PremiumMembership,
    SparkPrompt,
)
from crush_lu.services.crush_connect import (
    get_active_coach_pick,
    get_eligible_pool,
    propose_coach_pick,
    respond_to_coach_pick,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")

User = get_user_model()

CONNECT_HOME_URL = "/en/crush-connect/today/"  # retired bookmark
CONNECT_TEASER_URL = "/en/crush-connect/"
CATALOGUE_STATUS_URL = "/en/crush-connect/catalogue/"
HUB_URL = "/en/crush-connect/home/"
COACH_PICK_URL = "/en/crush-connect/coach-pick/"


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
    photo_share_consent=True,
):
    """Build a current Connect member; flags allow focused negative cases."""
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
        photo_1="users/1/photos/test.jpg",
        preferred_genders=preferred_genders or [],
        preferred_age_min=preferred_age_min,
        preferred_age_max=preferred_age_max,
    )
    if premium:
        profile.assigned_coach = _get_coach()
        profile.assigned_coach_at = timezone.now()
        profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
        PremiumMembership.objects.create(
            user=user,
            coach=profile.assigned_coach,
            status="active",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )
    if has_luxid:
        SocialAccount.objects.create(user=user, provider="luxid", uid=username)

    CrushConnectMembership.objects.create(
        user=user,
        onboarded_at=timezone.now() if onboarded else None,
        excluded_by_coach=excluded_by_coach,
        photo_share_consent=photo_share_consent,
        preferred_genders=preferred_genders or [],
        preferred_age_min=preferred_age_min,
        preferred_age_max=preferred_age_max,
    )
    return user


def _set_gate_questions(user, answers=None):
    from crush_lu.models import MemberGateQuestion
    from crush_lu.services.crush_connect import get_or_create_question_week

    week = get_or_create_question_week()
    questions = list(week.questions.filter(is_active=True)[:3])
    answers = answers if answers is not None else [True, True, True]
    membership = user.crush_connect_membership
    membership.gate_questions.all().delete()
    for position, (question, owner_answer) in enumerate(
        zip(questions, answers), start=1
    ):
        MemberGateQuestion.objects.create(
            membership=membership,
            question=question,
            position=position,
            owner_answer=owner_answer,
            picked_week=week,
        )
    return questions


def _mark_attended(user, event=None):
    event = event or _make_event(title=f"Event for {user.username}")
    profile = getattr(user, "crushprofile", None)
    if profile:
        profile.verification_method = "coach_event"
        profile.save(update_fields=["verification_method"])
    return EventRegistration.objects.create(user=user, event=event, status="attended")


def _seed_pool_for(me, n=10):
    out = []
    for i in range(n):
        user = _make_user(
            username=f"target_{i:02d}",
            gender="F",
            preferred_genders=["M"],
            premium=False,
        )
        _mark_attended(user)
        _set_gate_questions(user)
        out.append(user)
    return out


def _grant_consent(user):
    from crush_lu.models import UserDataConsent

    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"crushlu_consent_given": True},
    )


def _login_eligible(client, user):
    _grant_consent(user)
    client.force_login(user)


def _admin_request(user):
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    request = RequestFactory().post("/admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _coach_for(member):
    return member.crushprofile.assigned_coach


@pytest.mark.django_db
def test_seed_prompts_loaded():
    assert SparkPrompt.objects.filter(is_active=True).exists()


@pytest.mark.django_db
def test_coach_pick_pool_requires_active_premium_member():
    member = _make_user(username="member", preferred_genders=["F"], premium=False)
    candidate = _make_user(username="candidate", gender="F", premium=False)
    assert candidate not in get_eligible_pool(member)


@pytest.mark.django_db
def test_coach_pick_pool_includes_verified_consented_nonpremium_candidate():
    member = _make_user(username="member", preferred_genders=["F"])
    candidate = _make_user(
        username="candidate",
        gender="F",
        preferred_genders=["M"],
        premium=False,
    )
    assert candidate in get_eligible_pool(member)


@pytest.mark.django_db
def test_coach_pick_pool_excludes_revoked_photo_consent():
    member = _make_user(username="member", preferred_genders=["F"])
    candidate = _make_user(
        username="candidate",
        gender="F",
        premium=False,
        photo_share_consent=False,
    )
    assert candidate not in get_eligible_pool(member)


@pytest.mark.django_db
def test_coach_pick_happy_path_and_member_response():
    member = _make_user(username="member", preferred_genders=["F"])
    candidate = _make_user(username="candidate", gender="F", premium=False)
    pick = propose_coach_pick(_coach_for(member), member, candidate, note="Thoughtful")

    assert get_active_coach_pick(member) == pick
    assert respond_to_coach_pick(pick, accept=True).status == "accepted"


@pytest.mark.django_db
def test_retired_today_route_redirects_to_hub(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member")
    _login_eligible(client, member)

    response = client.get(CONNECT_HOME_URL)

    assert response.status_code == 302
    assert response.url == HUB_URL


@pytest.mark.django_db
def test_premium_member_can_open_dedicated_coach_pick_page(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", preferred_genders=["F"])
    candidate = _make_user(username="candidate", gender="F", premium=False)
    propose_coach_pick(_coach_for(member), member, candidate)
    _login_eligible(client, member)

    response = client.get(COACH_PICK_URL)

    assert response.status_code == 200
    assert "Your Coach's Pick" in response.content.decode()
    assert candidate.first_name in response.content.decode()


@pytest.mark.django_db
def test_nonpremium_member_is_redirected_from_coach_pick(client, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", premium=False)
    _login_eligible(client, member)

    response = client.get(COACH_PICK_URL)

    assert response.status_code == 302
    assert CATALOGUE_STATUS_URL in response.url
