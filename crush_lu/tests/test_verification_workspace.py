"""One coach workspace without merging the three approval policies."""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from crush_lu.models import ConnectRealPersonCheck, CrushProfile, ProfileSubmission
from crush_lu.services.real_person_check import claim_check
from crush_lu.services.verification_funnel import (
    claim_video_request,
    request_verification,
)
from crush_lu.tests.real_person_check_helpers import make_coach, make_member, profile_of
from crush_lu.tests.test_crush_connect import _grant_consent

pytestmark = pytest.mark.django_db
CHANNEL = "/en/coach/channel/"


@pytest.fixture
def workspace(client, settings):
    cache.clear()
    settings.ROOT_URLCONF = "azureproject.urls_crush"
    settings.DEBUG = True
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "public"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    coach = make_coach("workspace_coach")
    _grant_consent(coach.user)
    legacy = make_member("legacy")
    CrushProfile.objects.filter(user=legacy).update(
        is_approved=False, verification_status="pending", verification_method=""
    )
    submission = ProfileSubmission.objects.create(
        profile=profile_of(legacy), coach=coach, status="pending"
    )
    badge = claim_check(coach, profile_of(make_member("badge")).pk)
    video_member = make_member("video")
    CrushProfile.objects.filter(user=video_member).update(approved_at=timezone.now())
    video = claim_video_request(coach, request_verification(video_member).pk)
    client.force_login(coach.user)
    return coach, submission, badge, video


def test_each_assignment_has_a_typed_link_and_the_count_matches(client, workspace):
    coach, submission, badge, video = workspace
    response = client.get(CHANNEL, HTTP_HOST="crush.lu")
    assert response.status_code == 200
    assert response.context["my_pending"] == 3
    assert response.context["verification_assignment_count"] == 3
    rows = response.context["assigned"]
    assert len(rows) == 3
    assert {row.kind for row in rows} == {"submission", "badge", "video"}
    assert {row.continue_url for row in rows} == {
        f"/en/coach/review/{submission.pk}/",
        f"/en/coach/real-person-checks/{badge.pk}/",
        f"/en/coach/real-person-checks/{video.pk}/",
    }
    assert response.context["channel_count"] == 0
    assert not response.context["channel"]
    # Viewing the workspace makes no decision and grants no access.
    submission.refresh_from_db()
    assert submission.status == "pending"
    assert (
        ConnectRealPersonCheck.objects.filter(
            pk__in=[badge.pk, video.pk], status="open", decided_at__isnull=True
        ).count()
        == 2
    )


def test_filters_do_not_hide_owned_work_or_include_other_coaches(client, workspace):
    coach, submission, badge, video = workspace
    other = make_coach("other_workspace_coach")
    foreign = claim_check(other, profile_of(make_member("foreign_badge")).pk)
    expired = claim_check(coach, profile_of(make_member("expired_badge")).pk)
    ConnectRealPersonCheck.objects.filter(pk=expired.pk).update(
        lease_expires_at=timezone.now() - timedelta(minutes=1)
    )
    response = client.get(CHANNEL + "?lang=xx&region=missing", HTTP_HOST="crush.lu")
    assert response.context["my_pending"] == 3
    urls = {row.continue_url for row in response.context["assigned"]}
    assert f"/en/coach/real-person-checks/{foreign.pk}/" not in urls
    assert f"/en/coach/real-person-checks/{expired.pk}/" not in urls


def test_optional_outreach_is_separate_from_member_requests(client, workspace):
    make_member("optional_outreach")
    response = client.get(CHANNEL + "?work=badges", HTTP_HOST="crush.lu")
    assert response.context["my_pending"] == 3
    assert response.context["channel_count"] == 0
    assert response.context["total_waiting"] == 1
    assert b"These members have not requested verification" in response.content
    assert b"Optional badge outreach" in response.content


def test_old_queue_bookmark_preserves_page_inside_channel(client, workspace):
    response = client.get("/en/coach/real-person-checks/?page=2", HTTP_HOST="crush.lu")
    assert response.status_code == 302
    assert response.url == CHANNEL + "?work=badges&page=2"


def test_disabled_badges_leave_profile_reviews_available(client, settings, workspace):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    response = client.get(CHANNEL + "?work=badges", HTTP_HOST="crush.lu")
    assert response.status_code == 200
    assert response.context["my_pending"] == 1
    assert response.context["verification_assignment_count"] == 1
    assert response.context["work_tab"] == "requests"
    assert b"Optional badges" not in response.content


@pytest.mark.parametrize("language", ["en", "de", "fr"])
def test_both_review_pages_return_to_the_same_localized_channel(
    client, workspace, language
):
    _, submission, badge, video = workspace
    for path in (
        f"review/{submission.pk}/",
        f"real-person-checks/{badge.pk}/",
        f"real-person-checks/{video.pk}/",
    ):
        response = client.get(f"/{language}/coach/{path}", HTTP_HOST="crush.lu")
        assert response.status_code == 200
        assert f'href="/{language}/coach/channel/"'.encode() in response.content
        assert b'id="verification-purpose-heading"' in response.content
