"""January member -> familiar coach channel -> explicit video decision contract."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.urls import set_urlconf
from django.utils import timezone

from crush_lu.models import (
    ConnectRealPersonBadge,
    ConnectRealPersonCheck,
    CrushProfile,
    ScreeningSlot,
)
from crush_lu.real_person_check_rules import REQUIRED_CHECKLIST_KEYS
from crush_lu.services.connect_verification import has_valid_video_admission
from crush_lu.services.real_person_check import (
    RealPersonCheckError,
    authorize_video_call,
    decide_check,
)
from crush_lu.services.verification_appointments import (
    arrange_appointment,
    cancel_appointment,
)
from crush_lu.services.verification_funnel import (
    cancel_verification,
    claim_video_request,
    member_funnel,
    request_verification,
)
from crush_lu.tests.real_person_check_helpers import make_coach, make_member, profile_of

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def setup_flags(settings):
    settings.DEBUG = True
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "public"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    cache.clear()
    set_urlconf("azureproject.urls_crush")
    yield
    set_urlconf(None)


@pytest.fixture
def journey():
    member = make_member("video_member")
    CrushProfile.objects.filter(user=member).update(approved_at=timezone.now())
    member.refresh_from_db()
    coach = make_coach()
    from crush_lu.tests.test_crush_connect import _grant_consent

    _grant_consent(coach.user)
    return member, coach


def test_request_is_idempotent_without_reopening_profile(journey):
    member, _ = journey
    check = request_verification(member)
    assert request_verification(member).pk == check.pk
    assert check.coach_id is None
    assert check.requested_at
    assert profile_of(member).verification_status == "verified"
    from crush_lu.models import ProfileSubmission

    assert not ProfileSubmission.objects.filter(profile__user=member).exists()


def test_existing_optional_badge_does_not_block_new_purpose(journey):
    member, coach = journey
    profile = profile_of(member)
    old = ConnectRealPersonCheck.objects.create(
        profile=profile, coach=coach, status="passed", medium="voice"
    )
    ConnectRealPersonBadge.objects.create(
        profile=profile,
        source_check=old,
        medium="voice",
        profile_approved_at=profile.approved_at,
    )
    claim_video_request(coach, request_verification(member).pk)


def test_two_coaches_cannot_own_request(journey):
    member, coach = journey
    check = request_verification(member)
    claim_video_request(coach, check.pk)
    with pytest.raises(RealPersonCheckError, match="taken"):
        claim_video_request(make_coach("second"), check.pk)


def test_video_approval_grants_access_and_completes_shared_appointment(journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    slot = arrange_appointment(coach, check.pk, timezone.now() + timedelta(minutes=5))
    assert authorize_video_call(member, str(check.pk)) == "member"
    assert authorize_video_call(coach.user, str(check.pk)) == "coach"
    assert not authorize_video_call(make_coach("unrelated").user, str(check.pk))
    decide_check(
        coach,
        check.pk,
        "pass_video",
        photo_key=profile_of(member).photo_1.name,
        confirmed=True,
        checklist=dict.fromkeys(REQUIRED_CHECKLIST_KEYS["video"], True),
    )
    assert has_valid_video_admission(member)
    slot.refresh_from_db()
    assert slot.status == "completed"
    assert not authorize_video_call(member, str(check.pk))


def test_voice_is_refused_and_cannot_grant(journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    with pytest.raises(RealPersonCheckError, match="video_required"):
        decide_check(
            coach,
            check.pk,
            "pass_voice",
            confirmed=True,
            checklist=dict.fromkeys(REQUIRED_CHECKLIST_KEYS["voice"], True),
        )
    assert not has_valid_video_admission(member)
    check.refresh_from_db()
    assert check.status == "open"


def test_appointment_outlives_claim_and_does_not_open_media_early(journey):
    from crush_lu.services.real_person_check import log_outreach

    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    slot = arrange_appointment(coach, check.pk, timezone.now() + timedelta(days=7))
    check.refresh_from_db()
    assert check.lease_expires_at > slot.end_at
    assert not authorize_video_call(member, str(check.pk))
    log_outreach(coach, check.pk, result="sms_sent")
    check.refresh_from_db()
    assert check.lease_expires_at > slot.end_at


def test_shared_calendar_prevents_overlap_with_legacy_slot(journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    start = timezone.now() + timedelta(days=1)
    ScreeningSlot.objects.create(
        coach=coach,
        status="booked",
        start_at=start,
        end_at=start + timedelta(minutes=30),
    )
    with pytest.raises(RealPersonCheckError, match="appointment_overlap"):
        arrange_appointment(coach, check.pk, start + timedelta(minutes=10))


def test_cancellation_releases_slot_and_allows_new_request(journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    slot = arrange_appointment(coach, check.pk, timezone.now() + timedelta(days=1))
    cancel_verification(member)
    slot.refresh_from_db()
    assert slot.status == "cancelled"
    assert request_verification(member).pk != check.pk


def test_missed_call_can_be_rearranged(journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    old = arrange_appointment(coach, check.pk, timezone.now() + timedelta(minutes=5))
    later = timezone.now() + timedelta(hours=1)
    with patch("django.utils.timezone.now", return_value=later):
        assert member_funnel(member)["state"] == "retry"
        arrange_appointment(coach, check.pk, later + timedelta(hours=1))
    old.refresh_from_db()
    assert old.status == "no_show"


def test_removed_luxid_blocks_join_and_decision(journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    arrange_appointment(coach, check.pk, timezone.now() + timedelta(minutes=5))
    member.socialaccount_set.all().delete()
    assert not authorize_video_call(member, str(check.pk))
    with pytest.raises(RealPersonCheckError):
        decide_check(
            coach,
            check.pk,
            "pass_video",
            photo_key=profile_of(member).photo_1.name,
            confirmed=True,
            checklist=dict.fromkeys(REQUIRED_CHECKLIST_KEYS["video"], True),
        )


def test_flag_off_blocks_new_work_but_allows_cancellation(journey):
    member, coach = journey
    check = request_verification(member)
    with override_settings(CONNECT_VIDEO_VERIFICATION_MODE="off"):
        with pytest.raises(RealPersonCheckError):
            claim_video_request(coach, check.pk)
        cancel_verification(member)
    check.refresh_from_db()
    assert check.status == "released"


def test_channel_has_typed_claim_and_familiar_return(client, journey):
    member, coach = journey
    check = request_verification(member)
    client.force_login(coach.user)
    response = client.get("/en/coach/channel/", HTTP_HOST="crush.lu")
    assert response.status_code == 200
    assert f"/channel/video/{check.pk}/claim/".encode() in response.content
    response = client.post(
        f"/en/coach/channel/video/{check.pk}/claim/", HTTP_HOST="crush.lu"
    )
    assert response.status_code == 302
    response = client.get(response.url, HTTP_HOST="crush.lu")
    assert response.status_code == 200
    assert b"Save agreed appointment" in response.content


def test_member_page_waiting_and_no_private_notes(client, journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    check.notes = "PRIVATE ASSESSMENT"
    check.save(update_fields=["notes"])
    client.force_login(member)
    response = client.get("/en/crush-connect/verification/", HTTP_HOST="crush.lu")
    assert response.status_code == 200
    assert b'data-verification-state="waiting"' in response.content
    assert b"PRIVATE ASSESSMENT" not in response.content


def test_members_cannot_claim(client, journey):
    member, coach = journey
    check = request_verification(member)
    client.force_login(member)
    response = client.post(
        f"/en/coach/channel/video/{check.pk}/claim/", HTTP_HOST="crush.lu"
    )
    assert response.status_code == 302
    check.refresh_from_db()
    assert check.coach_id is None


def test_appointment_cancellation_and_release_allow_reassignment(journey):
    from crush_lu.services.real_person_check import release_check

    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    slot = arrange_appointment(coach, check.pk, timezone.now() + timedelta(days=1))
    cancel_appointment(coach, check.pk)
    slot.refresh_from_db()
    assert slot.status == "cancelled"
    release_check(coach, check.pk)
    check.refresh_from_db()
    assert check.status == "open" and check.coach_id is None
    assert claim_video_request(make_coach("replacement"), check.pk).coach_id != coach.pk


def test_event_completion_releases_redundant_appointment(journey):
    from crush_lu.services.verification_funnel import complete_from_event

    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    slot = arrange_appointment(coach, check.pk, timezone.now() + timedelta(days=1))
    CrushProfile.objects.filter(user=member).update(verification_method="coach_event")
    complete_from_event(member.pk)
    check.refresh_from_db()
    slot.refresh_from_db()
    assert check.status == "released" and slot.status == "cancelled"
    assert not has_valid_video_admission(member)


def test_old_writer_deverification_cancels_video_appointment(journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    slot = arrange_appointment(coach, check.pk, timezone.now() + timedelta(days=1))
    # Queryset update deliberately bypasses new Python lifecycle code.
    CrushProfile.objects.filter(user=member).update(
        verification_status="pending", is_approved=False
    )
    slot.refresh_from_db()
    assert slot.status == "cancelled"


def test_approved_video_with_missing_setup_has_honest_next_step(journey):
    member, coach = journey
    check = claim_video_request(coach, request_verification(member).pk)
    decide_check(
        coach,
        check.pk,
        "pass_video",
        photo_key=profile_of(member).photo_1.name,
        confirmed=True,
        checklist=dict.fromkeys(REQUIRED_CHECKLIST_KEYS["video"], True),
    )
    state = member_funnel(member)
    assert state["state"] == "finish_setup" and state["method"] == "video"
    assert state["action_url"]


@pytest.mark.parametrize("language", ["en", "de", "fr"])
def test_member_page_renders_all_languages(client, journey, language):
    member, _ = journey
    client.force_login(member)
    response = client.get(
        f"/{language}/crush-connect/verification/", HTTP_HOST="crush.lu"
    )
    assert response.status_code == 200
    assert (
        b"Request a video verification" not in response.content
        if language != "en"
        else b"Request a video verification" in response.content
    )


def test_outside_rollout_keeps_existing_experience(client, journey):
    member, _ = journey
    client.force_login(member)
    with override_settings(
        CONNECT_VIDEO_VERIFICATION_MODE="off",
        CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED=False,
    ):
        response = client.get("/en/crush-connect/verification/", HTTP_HOST="crush.lu")
        assert response.status_code == 302 and response.url.endswith("/dashboard/")
