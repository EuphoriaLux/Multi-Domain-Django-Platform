"""Coach playground: practice member, scenarios, switch, guard, inbox, channel.

The scenario tests are canaries: each scenario is built through the real
services, so when a Connect or verification gate changes, these fail instead
of the playground silently handing coaches a member stuck at the wrong step.

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md
"""

import inspect
import time
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from crush_lu.connect_phase import cycle_access_open
from crush_lu.models import (
    ConnectRealPersonCheck,
    CrushCoach,
    CrushProfile,
    PlaygroundAccount,
    PlaygroundMail,
    ProfileSubmission,
    ScreeningSlot,
    UserDataConsent,
)
from crush_lu.services import playground_practice as practice
from crush_lu.services.connect_verification import (
    human_verification_method,
    video_verification_requests_open,
)
from crush_lu.services.photo_publication import get_public_photo_key
from crush_lu.services.real_person_check import (
    GUARD_OTHER_COACH_PREMIUM,
    _call_role,
    check_guards,
    decide_check,
)
from crush_lu.services.verification_appointments import arrange_appointment
from crush_lu.services.verification_funnel import (
    claim_video_request,
    member_prerequisite,
    request_verification,
    video_channel_requests,
)

pytestmark = pytest.mark.django_db

HOST = "test.crush.lu"
PLAYGROUND = "/en/coach/playground/"
SCENARIO = "/en/coach/playground/scenario/"
SWITCH = "/en/coach/playground/switch/"
BACK = "/en/playground/back-to-coach/"


@pytest.fixture(autouse=True)
def staging(settings, monkeypatch):
    """Staging's flag set (measured 2026-11-13)."""
    monkeypatch.setenv("DJANGO_ENV", "staging")
    settings.DEBUG = False
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "public"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.COACH_PLAYGROUND_SSO_SECRET = ""
    cache.clear()
    with mock.patch(
        "crush_lu.services.video_call.video_calls_available", return_value=True
    ):
        yield settings
    cache.clear()


@pytest.fixture
def production(monkeypatch, staging):
    monkeypatch.delenv("DJANGO_ENV")
    return staging


def _coach(username="ana_coach", first_name="Ana"):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@crush.lu",
        password="pw-12345678",
        first_name=first_name,
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return CrushCoach.objects.create(user=user, is_active=True, bio="coach")


@pytest.fixture
def coach():
    return _coach()


@pytest.fixture
def other_coach():
    return _coach("ben_coach", "Ben")


def _profile(user):
    return CrushProfile.objects.select_related("user").get(user=user)


def _open_check(user):
    return (
        ConnectRealPersonCheck.objects.select_related("coach__user", "profile__user")
        .filter(profile__user=user, purpose="connect_week", status="open")
        .first()
    )


# ---------------------------------------------------------------------------
# Scenario canaries
# ---------------------------------------------------------------------------


class TestScenarios:
    def test_base_member_is_a_safe_ordinary_member(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        profile = _profile(member)
        link = PlaygroundAccount.objects.get(user=member)
        assert (link.kind, link.owner_id, link.preset) == (
            "practice",
            coach.user_id,
            "luxid_ready",
        )
        assert member.email.endswith("@crush.test")
        assert not member.has_usable_password()
        assert not member.is_staff
        assert not CrushCoach.objects.filter(user=member).exists()
        assert profile.phone_number in ("", None)
        assert profile.phone_verified
        assert UserDataConsent.objects.get(user=member).crushlu_consent_given
        assert member.emailaddress_set.get().verified
        from crush_lu.models import CrushConnectWaitlist

        assert not CrushConnectWaitlist.objects.filter(
            user=member, selected_as_tester=True
        ).exists()

    def test_luxid_ready(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        profile = _profile(member)
        assert member_prerequisite(profile) == ""
        assert video_verification_requests_open(member)
        assert human_verification_method(member) == ""
        assert get_public_photo_key(profile, "photo_1") == profile.photo_1.name
        assert not cycle_access_open(member)

    def test_video_requested(self, coach, other_coach):
        member = practice.apply_preset(coach, "video_requested")
        check = _open_check(member)
        assert check is not None and check.coach is None
        assert check in video_channel_requests(coach)
        assert check in video_channel_requests(other_coach)
        assert check_guards(coach, _profile(member), purpose="connect_week") == []

    def test_call_booked_opens_the_call_for_both(self, coach):
        member = practice.apply_preset(coach, "call_booked")
        check = _open_check(member)
        assert check.coach_id == coach.pk
        assert ScreeningSlot.objects.filter(
            real_person_check=check, status="booked"
        ).exists()
        assert _call_role(coach.user, check) == "coach"
        assert _call_role(member, check) == "member"

    def test_video_approved(self, coach):
        member = practice.apply_preset(coach, "video_approved")
        assert human_verification_method(member) == "video"
        assert cycle_access_open(member)

    def test_video_without_luxid(self, coach):
        member = practice.apply_preset(coach, "video_without_luxid")
        assert member_prerequisite(_profile(member)) == "luxid"

    def test_old_voice_screening_does_not_count(self, coach):
        member = practice.apply_preset(coach, "old_voice_screening")
        profile = _profile(member)
        assert ProfileSubmission.objects.filter(
            profile=profile, status="approved", review_call_completed=True
        ).exists()
        assert human_verification_method(member) == ""
        assert member_prerequisite(profile) == ""

    def test_photo_replaced_keeps_the_approved_photo_public(self, coach):
        member = practice.apply_preset(coach, "photo_replaced")
        profile = _profile(member)
        assert human_verification_method(member) == "video"
        assert get_public_photo_key(profile, "photo_1")
        assert get_public_photo_key(profile, "photo_1") != profile.photo_1.name

    def test_premium_is_reserved_for_its_coach(self, coach, other_coach):
        member = practice.apply_preset(coach, "premium")
        profile = _profile(member)
        assert profile.has_active_premium
        assert profile.assigned_coach_id == coach.pk
        assert GUARD_OTHER_COACH_PREMIUM in check_guards(
            other_coach, profile, purpose="connect_week"
        )
        assert GUARD_OTHER_COACH_PREMIUM not in check_guards(
            coach, profile, purpose="connect_week"
        )

    def test_full_video_journey_from_luxid_ready(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        check = request_verification(member)
        claim_video_request(coach, check.pk)
        start = timezone.now() + timedelta(minutes=5)
        arrange_appointment(coach, check.pk, start, 15)
        check = _open_check(member)
        assert _call_role(member, check) == "member"
        decide_check(
            coach,
            check.pk,
            "pass_video",
            checklist={
                key: True
                for key in (
                    "spoke_live",
                    "name_matches",
                    "age_matches",
                    "badge_explained",
                    "face_matches_photo",
                    "live_camera",
                )
            },
            photo_key=_profile(member).photo_1.name,
            confirmed=True,
        )
        assert human_verification_method(member) == "video"

    def test_double_press_is_a_friendly_error(self, coach):
        from django.db import IntegrityError

        with mock.patch.object(
            practice, "_build_base", side_effect=IntegrityError("unique")
        ):
            with pytest.raises(practice.PlaygroundError) as excinfo:
                practice.apply_preset(coach, "luxid_ready")
        assert excinfo.value.code == "try_again"

    def test_unknown_scenario(self, coach):
        with pytest.raises(practice.PlaygroundError):
            practice.apply_preset(coach, "nope")

    def test_refused_step_changes_nothing(self, coach, staging):
        practice.apply_preset(coach, "luxid_ready")
        first = practice.live_practice_link(coach.user)
        staging.CONNECT_VIDEO_VERIFICATION_MODE = "off"
        with pytest.raises(practice.PlaygroundError) as excinfo:
            practice.apply_preset(coach, "video_requested")
        assert excinfo.value.code == "video_admission_closed"
        assert practice.live_practice_link(coach.user).pk == first.pk
        first.user.refresh_from_db()
        assert first.user.is_active


class TestStartAgain:
    def test_retire_releases_everything_and_frees_the_claim(self, coach):
        old = practice.apply_preset(coach, "call_booked")
        check = _open_check(old)
        new = practice.apply_preset(coach, "luxid_ready")

        assert new.pk != old.pk
        old.refresh_from_db()
        assert not old.is_active
        assert PlaygroundAccount.objects.get(user=old).retired_at is not None
        check.refresh_from_db()
        assert check.status == "released"
        assert not ScreeningSlot.objects.filter(
            real_person_check=check, status="booked"
        ).exists()
        assert not ConnectRealPersonCheck.objects.filter(
            coach=coach, status="open", lease_expires_at__gt=timezone.now()
        ).exists()
        assert practice.live_practice_link(coach.user).user_id == new.pk

    def test_retire_dismisses_open_reports(self, coach):
        from crush_lu.models import UserReport

        old = practice.apply_preset(coach, "luxid_ready")
        report = UserReport.objects.create(
            reporter=coach.user,
            reported_user=old,
            reason="fake_profile",
            status="open",
        )
        practice.retire_practice_member(coach.user)
        report.refresh_from_db()
        assert report.status == "dismissed"

    def test_lock_order_starts_with_the_owner_row(self):
        # SQLite ignores select_for_update, so assert the order structurally:
        # the coach's user row (stable from the first build) is locked before
        # the practice row is looked up, and both before the funnel locks
        # profile -> check.
        build = inspect.getsource(practice.apply_preset)
        assert build.index("lock_owner(coach.user)") < build.index(
            "retire_practice_member(coach.user)"
        )
        retire = inspect.getsource(practice.retire_practice_member)
        assert retire.index("lock_owner(coach_user)") < retire.index(
            "select_for_update"
        )
        assert retire.index("select_for_update") < retire.index(
            "cancel_verification(old)"
        )

    def test_owner_lock_is_taken_on_the_first_build_too(self, coach):
        with mock.patch.object(
            practice, "lock_owner", wraps=practice.lock_owner
        ) as lock:
            practice.apply_preset(coach, "luxid_ready")
        assert lock.call_args_list[0].args == (coach.user,)


class TestGate:
    def test_services_refuse_in_production(self, production, coach):
        with pytest.raises(PermissionDenied):
            practice.apply_preset(coach, "luxid_ready")
        with pytest.raises(PermissionDenied):
            practice.retire_practice_member(coach.user)

    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", PLAYGROUND),
            ("post", PLAYGROUND),
            ("post", SCENARIO),
            ("get", SCENARIO),
            ("post", SWITCH),
            ("get", SWITCH),
            ("post", BACK),
            ("get", BACK),
        ],
    )
    def test_pages_404_in_production(self, production, coach, method, path):
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(coach.user)
        assert getattr(client, method)(path).status_code == 404

    def test_dashboard_has_no_playground_link_in_production(self, production, coach):
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(coach.user)
        assert PLAYGROUND.encode() not in client.get("/en/coach/dashboard/").content


# ---------------------------------------------------------------------------
# Pages, switch and back
# ---------------------------------------------------------------------------


def _coach_client(coach):
    client = Client(HTTP_HOST=HOST)
    client.force_login(coach.user)
    return client


class TestPages:
    def test_playground_page_and_dashboard_link(self, coach):
        client = _coach_client(coach)
        assert client.get(PLAYGROUND).status_code == 200
        assert PLAYGROUND.encode() in client.get("/en/coach/dashboard/").content

    def test_scenario_form_creates_the_member(self, coach):
        client = _coach_client(coach)
        response = client.post(SCENARIO, {"preset": "luxid_ready", "gender": "M"})
        assert response.status_code == 302
        link = practice.live_practice_link(coach.user)
        assert _profile(link.user).gender == "M"
        page = client.get(PLAYGROUND).content.decode()
        assert link.user.email in page

    def test_non_coach_cannot_use_it(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        client = Client(HTTP_HOST=HOST)
        client.force_login(member)
        assert client.get(PLAYGROUND).status_code == 302
        assert client.post(SWITCH).status_code == 302
        assert int(client.session["_auth_user_id"]) == member.pk

    def test_freshness_applies_only_once_sso_is_configured(self, coach, staging):
        client = _coach_client(coach)
        assert client.get(PLAYGROUND).status_code == 200
        staging.COACH_PLAYGROUND_SSO_SECRET = "s" * 48
        response = client.get(PLAYGROUND)
        assert response.status_code == 302
        assert response["Location"].startswith("/coach-playground/sso/start/")
        session = client.session
        session["coach_playground_sso_at"] = int(time.time())
        session.save()
        assert client.get(PLAYGROUND).status_code == 200


class TestSwitch:
    def test_switch_and_back(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        client = _coach_client(coach)

        response = client.post(SWITCH)
        assert response.status_code == 302
        assert response["Location"] == "/en/dashboard/"
        assert int(client.session["_auth_user_id"]) == member.pk
        page = client.get("/en/dashboard/").content.decode()
        assert "Practice member of Ana" in page
        assert BACK in page

        response = client.post(BACK)
        assert response.status_code == 302
        assert response["Location"] == PLAYGROUND
        assert int(client.session["_auth_user_id"]) == coach.user_id

    def test_switch_keeps_the_sso_sign_in_for_the_way_back(self, coach):
        practice.apply_preset(coach, "luxid_ready")
        client = _coach_client(coach)
        session = client.session
        session["coach_playground_sso_at"] = 12345
        session.save()
        client.post(SWITCH)
        client.post(BACK)
        assert client.session["coach_playground_sso_at"] == 12345

    def test_switch_needs_post(self, coach):
        practice.apply_preset(coach, "luxid_ready")
        assert _coach_client(coach).get(SWITCH).status_code == 405

    def test_switch_without_a_practice_member(self, coach):
        client = _coach_client(coach)
        assert client.post(SWITCH).status_code == 302
        assert int(client.session["_auth_user_id"]) == coach.user_id

    def test_back_needs_the_marker(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        client = Client(HTTP_HOST=HOST)
        client.force_login(member)
        response = client.post(BACK)
        assert response["Location"].startswith("/en/login/")
        assert int(client.session["_auth_user_id"]) == member.pk
        assert BACK.encode() not in client.get("/en/dashboard/").content

    def test_back_refuses_an_expired_marker(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        client = _coach_client(coach)
        client.post(SWITCH)
        session = client.session
        marker = session[practice.SWITCH_SESSION_KEY]
        marker["at"] -= practice.SWITCH_MAX_AGE_SECONDS + 5
        session[practice.SWITCH_SESSION_KEY] = marker
        session.save()
        client.post(BACK)
        assert int(client.session["_auth_user_id"]) == member.pk

    def test_back_refuses_another_coach(self, coach, other_coach):
        member = practice.apply_preset(coach, "luxid_ready")
        client = _coach_client(coach)
        client.post(SWITCH)
        session = client.session
        marker = session[practice.SWITCH_SESSION_KEY]
        marker["coach"] = other_coach.user_id
        session[practice.SWITCH_SESSION_KEY] = marker
        session.save()
        client.post(BACK)
        assert int(client.session["_auth_user_id"]) == member.pk

    def test_back_refuses_a_retired_member(self, coach):
        old = practice.apply_preset(coach, "luxid_ready")
        client = _coach_client(coach)
        client.post(SWITCH)
        # Starting again elsewhere retires it: the phone session ends.
        practice.apply_preset(coach, "luxid_ready")
        old.refresh_from_db()
        assert not old.is_active
        response = client.post(BACK)
        assert response.status_code == 302
        assert (
            "_auth_user_id" not in client.session
            or int(client.session["_auth_user_id"]) != coach.user_id
        )


class TestGuard:
    def test_practice_member_cannot_reach_risky_pages(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        client = Client(HTTP_HOST=HOST)
        client.force_login(member)
        response = client.get("/en/onboarding/phone/")
        assert response.status_code == 302
        assert response["Location"] == "/en/dashboard/"
        response = client.get("/accounts/password/change/")
        assert response["Location"] == "/en/dashboard/"
        response = client.post("/api/phone/mark-verified/")
        assert response.status_code == 403
        assert response.json() == {"error": "practice_member"}

    def test_other_members_are_not_guarded(self, coach):
        client = _coach_client(coach)
        response = client.get("/accounts/password/change/")
        assert response.get("Location") != "/en/dashboard/"


# ---------------------------------------------------------------------------
# Practice inbox
# ---------------------------------------------------------------------------


def _send(recipients, **kwargs):
    from azureproject.email_utils import send_domain_email

    return send_domain_email(
        "Your video verification",
        "Open https://test.crush.lu/en/crush-connect/verification/ to see it.",
        recipients,
        domain="crush.lu",
        html_message="<p>hello</p>",
        **kwargs,
    )


class TestInbox:
    def test_practice_mail_is_kept_not_sent(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        mail.outbox.clear()
        assert _send([member.email]) == 1
        assert mail.outbox == []
        kept = PlaygroundMail.objects.get(recipient=member)
        assert kept.subject == "Your video verification"
        assert "<p>hello</p>" == kept.body_html

    def test_mixed_recipients(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        mail.outbox.clear()
        _send([member.email, "real@example.com"], bcc=[member.email])
        assert PlaygroundMail.objects.filter(recipient=member).count() == 1
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["real@example.com"]
        assert mail.outbox[0].bcc == []

    def test_crush_test_without_playground_account_is_sent(self):
        mail.outbox.clear()
        _send(["quizrehearsal_1@crush.test"])
        assert len(mail.outbox) == 1
        assert not PlaygroundMail.objects.exists()

    def test_nothing_is_kept_in_production(self, coach, monkeypatch):
        member = practice.apply_preset(coach, "luxid_ready")
        monkeypatch.delenv("DJANGO_ENV")
        mail.outbox.clear()
        _send([member.email])
        assert len(mail.outbox) == 1
        assert not PlaygroundMail.objects.exists()

    def test_retention(self, coach):
        from crush_lu.services.playground_mail import KEEP_PER_MEMBER

        member = practice.apply_preset(coach, "luxid_ready")
        stale = PlaygroundMail.objects.create(
            recipient=member, to_address=member.email, subject="old"
        )
        PlaygroundMail.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(days=20)
        )
        for _ in range(KEEP_PER_MEMBER + 3):
            _send([member.email])
        assert PlaygroundMail.objects.filter(recipient=member).count() == (
            KEEP_PER_MEMBER
        )
        assert not PlaygroundMail.objects.filter(pk=stale.pk).exists()

    def test_playground_page_shows_the_inbox(self, coach):
        member = practice.apply_preset(coach, "luxid_ready")
        _send([member.email])
        page = _coach_client(coach).get(PLAYGROUND).content.decode()
        assert "Your video verification" in page
        assert "https://test.crush.lu/en/crush-connect/verification/" in page


# ---------------------------------------------------------------------------
# Verification Channel
# ---------------------------------------------------------------------------


class TestChannel:
    def test_own_practice_member_is_labelled(self, coach):
        member = practice.apply_preset(coach, "video_requested")
        page = _coach_client(coach).get("/en/coach/channel/").content.decode()
        assert "Practice · Ana" in page
        assert member.first_name in page

    def test_other_coaches_practice_is_hidden_unless_asked(self, coach, other_coach):
        practice.apply_preset(coach, "video_requested")
        client = _coach_client(other_coach)
        assert "Practice · Ana" not in client.get("/en/coach/channel/").content.decode()
        page = client.get("/en/coach/channel/?practice=all").content.decode()
        assert "Practice · Ana" in page

    def test_coach_check_page_has_no_phone_links(self, coach):
        member = practice.apply_preset(coach, "call_booked")
        check = _open_check(member)
        response = _coach_client(coach).get(f"/en/coach/real-person-checks/{check.pk}/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "tel:" not in content
        assert "sms:" not in content
        assert "wa.me" not in content
