"""
Tests for Coach Photo Review & Swipe Deck functionality.
"""

from datetime import date, timedelta
import json
import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushConnectMembership,
    CrushProfile,
    ProfilePhotoReviewLog,
    ProfilePhotoReviewState,
    PublishedProfilePhoto,
    UserDataConsent,
    UserReport,
)
from crush_lu.services.crush_connect import is_catalogue_eligible
from crush_lu.services.photo_review import (
    get_photo_review_queue,
    submit_photo_review,
    undo_last_photo_review,
)

pytestmark = pytest.mark.urls("azureproject.urls_crush")
User = get_user_model()


def _make_coach(username="test_coach", is_active=True):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@crush.lu",
        password="coachpassword123",
        first_name="Coach",
        last_name="Test",
    )
    coach = CrushCoach.objects.create(
        user=user,
        bio="Test coach bio",
        specializations="General",
        phone_number="+352691000000",
        is_active=is_active,
    )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    return coach


def _make_candidate(
    username="candidate_1",
    has_photo=True,
    photo_key="users/1/photos/photo1.jpg",
    photo_review_status="pending",
    onboarded=True,
    is_approved=True,
    has_luxid=True,
):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        first_name=username.title(),
        last_login=timezone.now() - timedelta(days=1),
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 1, 1),
        gender="F",
        location="Luxembourg City",
        is_approved=is_approved,
        verification_status="verified" if is_approved else "pending",
        is_active=True,
        photo_1=photo_key if has_photo else None,
        photo_review_status=photo_review_status,
        photo_review_key=photo_key if photo_review_status == "approved" else "",
    )
    if has_photo and photo_review_status not in ("needs_revision", "flagged_fake"):
        # Members see this photo, as for every member when held replacements
        # were introduced (migration 0272); new uploads wait for a coach.
        PublishedProfilePhoto.objects.create(
            profile=profile, photo_field="photo_1", photo_key=photo_key
        )
    if onboarded:
        CrushConnectMembership.objects.create(
            user=user,
            onboarded_at=timezone.now(),
            photo_share_consent=True,
        )
    if has_luxid:
        SocialAccount.objects.create(
            user=user, provider="luxid", uid=f"luxid_{username}"
        )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    return profile


@pytest.mark.django_db
def test_coach_photo_review_access_control():
    client = Client()
    deck_url = reverse("crush_lu:coach_photo_review_deck")

    # Anonymous user -> redirected to login
    resp = client.get(deck_url)
    assert resp.status_code == 302
    assert "login" in resp.url

    # Regular non-coach user -> redirected / forbidden
    regular = User.objects.create_user(username="regular", password="pwd")
    consent, _ = UserDataConsent.objects.get_or_create(user=regular)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    client.force_login(regular)
    resp = client.get(deck_url)
    assert resp.status_code == 302 or resp.status_code == 403

    # Coach -> 200 OK
    coach = _make_coach("active_coach")
    client.force_login(coach.user)
    resp = client.get(deck_url)
    assert resp.status_code == 200
    assert "Photo Review Deck" in resp.content.decode("utf-8")


@pytest.mark.django_db
def test_get_photo_review_queue_filtering_and_priority():
    coach = _make_coach("deck_coach")

    # Profile 1: Onboarded Connect member with photo -> highest priority
    p1 = _make_candidate("onboarded_cand", photo_key="users/1/photos/a.jpg")

    # Profile 2: Approved photo -> should NOT be in queue
    _make_candidate("approved_cand", photo_review_status="approved")

    # Profile 3: No photo -> should NOT be in queue
    _make_candidate("nophoto_cand", has_photo=False)

    # Profile 4: Coach's own profile -> should NOT be in queue
    coach_profile = CrushProfile.objects.create(
        user=coach.user,
        date_of_birth=date(1990, 1, 1),
        photo_1="users/coach/photos/me.jpg",
        is_active=True,
    )

    cards, total = get_photo_review_queue(coach)
    card_ids = [c["id"] for c in cards]

    assert p1.id in card_ids
    assert coach_profile.id not in card_ids
    assert total >= 1


@pytest.mark.django_db
def test_photo_queue_scopes_and_shared_per_image_decisions():
    """One card per member, listing every waiting photo; totals count members."""
    coach = _make_coach("scoped_deck_coach")
    connect_profile = _make_candidate(
        "connect_scoped", photo_key="users/20/photos/primary.jpg"
    )
    connect_profile.photo_2 = "users/20/photos/second.jpg"
    connect_profile.photo_3 = "users/20/photos/third.jpg"
    connect_profile.save(update_fields=["photo_2", "photo_3"])
    general_profile = _make_candidate("general_scoped", onboarded=False)

    def pending(cards, profile):
        return next(c["pending_fields"] for c in cards if c["id"] == profile.pk)

    all_cards, all_total = get_photo_review_queue(coach, scope="all")
    connect_cards, connect_total = get_photo_review_queue(coach, scope="connect")
    assert all_total == 2
    assert connect_total == 1
    assert {card["id"] for card in all_cards} == {
        connect_profile.pk,
        general_profile.pk,
    }
    assert pending(all_cards, connect_profile) == ["photo_1", "photo_2", "photo_3"]
    assert [card["id"] for card in connect_cards] == [connect_profile.pk]
    CrushConnectMembership.objects.filter(user=connect_profile.user).update(
        excluded_by_coach=True
    )
    assert get_photo_review_queue(coach, scope="all")[1] == 2
    assert get_photo_review_queue(coach, scope="connect")[1] == 0
    CrushConnectMembership.objects.filter(user=connect_profile.user).update(
        excluded_by_coach=False
    )

    submit_photo_review(
        coach=coach,
        profile_id=connect_profile.pk,
        decision="approved",
        photo_key=connect_profile.photo_2.name,
        photo_field="photo_2",
    )
    all_cards, _total = get_photo_review_queue(coach, scope="all")
    connect_cards, _total = get_photo_review_queue(coach, scope="connect")
    assert pending(all_cards, connect_profile) == ["photo_1", "photo_3"]
    assert pending(connect_cards, connect_profile) == ["photo_1", "photo_3"]

    undo_last_photo_review(coach)
    cards, _total = get_photo_review_queue(coach, scope="connect")
    assert pending(cards, connect_profile) == ["photo_1", "photo_2", "photo_3"]
    submit_photo_review(
        coach=coach,
        profile_id=connect_profile.pk,
        decision="approved",
        photo_key=connect_profile.photo_2.name,
        photo_field="photo_2",
    )

    connect_profile.photo_2 = "users/20/photos/replaced.jpg"
    connect_profile.save(update_fields=["photo_2"])
    connect_profile.refresh_from_db()
    assert connect_profile.get_photo_field_review_status("photo_2") == "pending"
    cards, total = get_photo_review_queue(coach, scope="connect")
    assert total == 1
    assert pending(cards, connect_profile) == ["photo_1", "photo_2", "photo_3"]
    assert ProfilePhotoReviewState.objects.filter(
        profile=connect_profile, photo_field="photo_2", status="approved"
    ).exists()


@pytest.mark.django_db
def test_submit_photo_review_approved():
    coach = _make_coach("approver_coach")
    cand = _make_candidate("approve_me", photo_key="users/2/photos/real.jpg")

    result = submit_photo_review(
        coach=coach,
        profile_id=cand.id,
        photo_key=cand.photo_1.name,
        decision="approved",
        notes="Clear and authentic photo",
    )
    assert result["success"] is True

    cand.refresh_from_db()
    assert cand.photo_review_status == "approved"
    assert cand.photo_review_key == "users/2/photos/real.jpg"
    assert cand.photo_reviewed_by == coach
    assert cand.photo_reviewed_at is not None
    assert cand.is_photo_review_approved is True

    # Audit log created
    log = ProfilePhotoReviewLog.objects.filter(profile=cand, coach=coach).first()
    assert log is not None
    assert log.decision == "approved"
    assert log.previous_status == "pending"

    # Profile qualifies for candidate catalogue
    assert is_catalogue_eligible(cand.user) is True


@pytest.mark.django_db
def test_submit_photo_review_flagged_fake():
    coach = _make_coach("moderator_coach")
    cand = _make_candidate("fake_bot", photo_key="users/3/photos/ai_generated.jpg")

    result = submit_photo_review(
        coach=coach,
        profile_id=cand.id,
        photo_key=cand.photo_1.name,
        decision="flagged_fake",
        reason="fake_profile",
        notes="Stock model image detected",
    )
    assert result["success"] is True

    cand.refresh_from_db()
    assert cand.photo_review_status == "flagged_fake"

    # Coach panic button is flipped on CrushConnectMembership
    mem = CrushConnectMembership.objects.get(user=cand.user)
    assert mem.excluded_by_coach is True
    assert mem.excluded_by == coach

    # UserReport is created in moderation queue
    report = UserReport.objects.filter(
        reported_user=cand.user, reporter=coach.user
    ).first()
    assert report is not None
    assert report.reason == "fake_profile"
    assert report.status == "actioned"

    # Profile is excluded from Connect catalogue
    assert is_catalogue_eligible(cand.user) is False


@pytest.mark.django_db
def test_submit_photo_review_needs_revision():
    coach = _make_coach("revision_coach")
    cand = _make_candidate("sunglasses_user", photo_key="users/4/photos/dark.jpg")

    result = submit_photo_review(
        coach=coach,
        profile_id=cand.id,
        photo_key=cand.photo_1.name,
        decision="needs_revision",
        reason="unclear_face",
        notes="Please upload a photo without sunglasses",
    )
    assert result["success"] is True

    cand.refresh_from_db()
    assert cand.photo_review_status == "needs_revision"

    # Excluded from Connect catalogue until cleared
    assert is_catalogue_eligible(cand.user) is False


@pytest.mark.django_db
def test_primary_photo_replacement_resets_status():
    _make_coach("approver")
    cand = _make_candidate("swapper", photo_key="users/5/photos/approved.jpg")
    cand.photo_review_status = "approved"
    cand.photo_review_key = "users/5/photos/approved.jpg"
    cand.photo_reviewed_at = timezone.now()
    cand.save()

    # Member replaces their primary photo
    cand.photo_1 = "users/5/photos/new_sketchy_photo.jpg"
    cand.save()

    cand.refresh_from_db()
    assert cand.photo_review_status == "pending"
    assert cand.photo_review_key == ""
    assert cand.photo_reviewed_at is None
    assert cand.is_photo_review_approved is False


@pytest.mark.django_db
def test_undo_last_photo_review():
    coach = _make_coach("undo_coach")
    cand = _make_candidate("undo_candidate", photo_key="users/6/photos/test.jpg")

    # Flag as fake
    submit_photo_review(
        coach=coach,
        profile_id=cand.id,
        photo_key=cand.photo_1.name,
        decision="flagged_fake",
        reason="fake_profile",
    )
    cand.refresh_from_db()
    assert cand.photo_review_status == "flagged_fake"

    # Undo
    undo_result = undo_last_photo_review(coach)
    assert undo_result["success"] is True

    cand.refresh_from_db()
    assert cand.photo_review_status == "pending"

    # Membership exclusion reverted
    mem = CrushConnectMembership.objects.get(user=cand.user)
    assert mem.excluded_by_coach is False


@pytest.mark.django_db
def test_photo_review_endpoints_json():
    coach = _make_coach("json_coach")
    cand = _make_candidate("endpoint_cand", photo_key="users/7/photos/pic.jpg")

    client = Client()
    client.force_login(coach.user)

    # 1. Decide endpoint
    decide_url = reverse("crush_lu:coach_photo_review_decide")
    payload = {
        "profile_id": cand.id,
        "photo_key": cand.photo_1.name,
        "decision": "approved",
        "notes": "Verified via API",
    }
    resp = client.post(
        decide_url, data=json.dumps(payload), content_type="application/json"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True

    cand.refresh_from_db()
    assert cand.photo_review_status == "approved"

    # 2. Undo endpoint
    undo_url = reverse("crush_lu:coach_photo_review_undo")
    resp_undo = client.post(undo_url, content_type="application/json")
    assert resp_undo.status_code == 200
    assert resp_undo.json()["success"] is True

    cand.refresh_from_db()
    assert cand.photo_review_status == "pending"

    # 3. More cards endpoint
    more_url = reverse("crush_lu:coach_photo_review_more")
    resp_more = client.get(more_url)
    assert resp_more.status_code == 200
    data_more = resp_more.json()
    assert "cards" in data_more
    assert "total_waiting" in data_more
