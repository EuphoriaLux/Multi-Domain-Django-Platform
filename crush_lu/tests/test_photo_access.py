"""Who may load another member's photo through the id-addressed photo routes.

``/<lang>/media/profile/<user_id>/<field>/`` and ``/api/quiz/photo/<user_id>/``
are addressed by sequential user id, so the page that renders the URL is not
the gate: the view must re-check the viewer's relationship to the owner on
every request. These tests pin each relationship the rendering surfaces rely
on, and refuse everyone else.
"""

from datetime import date, timedelta

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushConnectMembership,
    CrushProfile,
    EventConnection,
    EventRegistration,
    MeetupEvent,
    PremiumMembership,
    UserBlock,
)
from crush_lu.models.crush_connect import ConnectCoachPick
from crush_lu.models.event_lobby import ConfirmedEncounter
from crush_lu.models.crush_connect_cycle import (
    ConnectCycleCard,
    ConnectTemporaryChat,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.models.profiles import UserDataConsent
from crush_lu.models.quiz import QuizEvent

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def _local_photos(settings, tmp_path):
    """Serve from a temp MEDIA_ROOT (no SAS redirect) so allowed means 200."""
    settings.AZURE_ACCOUNT_NAME = ""
    settings.MEDIA_ROOT = str(tmp_path)
    cache.clear()
    yield
    cache.clear()


def _member(username, *, verified=True, photo_consent=None, photo=True):
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x"
    )
    User.objects.filter(pk=user.pk).update(last_login=timezone.now())
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1994, 3, 2),
        gender="F",
        location="Luxembourg City",
        is_approved=verified,
        verification_status="verified" if verified else "incomplete",
        is_active=True,
    )
    if photo:
        profile.photo_1.save("p.jpg", ContentFile(b"jpegbytes"), save=True)
    if photo_consent is not None:
        SocialAccount.objects.create(user=user, provider="luxid", uid=f"lx-{user.pk}")
        CrushConnectMembership.objects.create(
            user=user,
            onboarded_at=timezone.now(),
            photo_share_consent=photo_consent,
        )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return User.objects.get(pk=user.pk)


def _event(ended_hours_ago=1, duration=120):
    now = timezone.now()
    return MeetupEvent.objects.create(
        title="Photo Test Event",
        description="x",
        event_type="mixer",
        location="Lux",
        address="1 Test St",
        canton="Luxembourg",
        date_time=now - timedelta(hours=ended_hours_ago, minutes=duration),
        duration_minutes=duration,
        max_participants=30,
        registration_deadline=now - timedelta(days=2),
        is_published=True,
    )


def _attend(user, event, status="attended"):
    return EventRegistration.objects.create(event=event, user=user, status=status)


def _photo(client, viewer, owner):
    client.force_login(viewer)
    return client.get(f"/en/media/profile/{owner.pk}/photo_1/")


def _quiz_photo(client, viewer, owner):
    client.force_login(viewer)
    return client.get(f"/api/quiz/photo/{owner.pk}/")


# ---------------------------------------------------------------------------
# serve_profile_photo
# ---------------------------------------------------------------------------


class TestProfilePhotoAllowed:
    def test_owner_sees_own_photo(self, client):
        alice = _member("alice", verified=False)
        assert _photo(client, alice, alice).status_code == 200

    def test_active_coach_sees_any_photo(self, client):
        alice = _member("alice", verified=False)
        coach = _member("coach")
        CrushCoach.objects.create(user=coach, is_active=True)
        assert _photo(client, coach, alice).status_code == 200

    def test_superuser_sees_approved_photo(self, client):
        alice = _member("alice")
        admin = User.objects.create_superuser("root", "root@example.com", "x")
        assert _photo(client, admin, alice).status_code == 200

    def test_co_attendee_while_attendee_list_is_open(self, client):
        alice, ben = _member("alice"), _member("ben")
        event = _event(ended_hours_ago=1)
        _attend(alice, event)
        _attend(ben, event)
        assert _photo(client, ben, alice).status_code == 200

    def test_event_connection_either_direction(self, client):
        alice, ben = _member("alice"), _member("ben")
        event = _event(ended_hours_ago=24 * 30)
        EventConnection.objects.create(
            requester=ben, recipient=alice, event=event, status="accepted"
        )
        assert _photo(client, ben, alice).status_code == 200
        assert _photo(client, alice, ben).status_code == 200

    def test_connect_cycle_card_with_photo_consent(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(user=ben)
        ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=alice,
            generated_date=timezone.localdate(),
        )
        assert _photo(client, ben, alice).status_code == 200

    def test_connect_pending_request_recipient_sees_requester(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(user=alice)
        ConnectWeeklyRequest.objects.create(
            session=session,
            requester=alice,
            recipient=ben,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        assert _photo(client, ben, alice).status_code == 200

    def test_connect_chat_partner(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(user=alice)
        request = ConnectWeeklyRequest.objects.create(
            session=session,
            requester=alice,
            recipient=ben,
            status="accepted",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        ConnectTemporaryChat.objects.create(
            request=request,
            participant_1=alice,
            participant_2=ben,
            expires_at=timezone.now() + timedelta(days=7),
        )
        assert _photo(client, ben, alice).status_code == 200

    def test_coach_pick_member_sees_candidate(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        coach = CrushCoach.objects.create(user=_member("coach"), is_active=True)
        CrushProfile.objects.filter(user=ben).update(assigned_coach=coach)
        PremiumMembership.objects.create(user=ben, coach=coach, status="active")
        ConnectCoachPick.objects.create(coach=coach, member=ben, candidate=alice)
        assert _photo(client, ben, alice).status_code == 200

    def test_connect_completed_card_during_review(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(
            user=ben,
            status=ConnectWeekSession.Status.REVIEW_OPEN,
            review_expires_at=timezone.now() + timedelta(hours=5),
        )
        ConnectCycleCard.objects.create(
            session=session,
            day_number=2,
            card_index=1,
            target_user=alice,
            generated_date=timezone.localdate(),
            is_completed=True,
        )
        assert _photo(client, ben, alice).status_code == 200

    def test_requester_of_declined_crush_lead_keeps_neutral_card(self, client):
        """``my_connections`` hides a declined lead's outcome; a 403 on its
        photo would reveal it."""
        alice, ben = _member("alice"), _member("ben")
        EventConnection.objects.create(
            requester=ben,
            recipient=alice,
            event=_event(ended_hours_ago=24 * 30),
            status="declined",
            flow=EventConnection.FLOW_CRUSH,
        )
        assert _photo(client, ben, alice).status_code == 200


class TestProfilePhotoRefused:
    def test_unrelated_approved_member(self, client):
        alice, mallory = _member("alice"), _member("mallory")
        assert _photo(client, mallory, alice).status_code == 403

    def test_account_without_profile(self, client):
        alice = _member("alice")
        stranger = User.objects.create_user("stranger", "s@example.com", "x")
        UserDataConsent.objects.update_or_create(
            user=stranger, defaults={"crushlu_consent_given": True}
        )
        assert _photo(client, stranger, alice).status_code == 403

    def test_unapproved_viewer_even_if_co_attendee(self, client):
        """Deliberate: an unverified attendee has no attendee list, so no
        photos either (``can_make_connections`` gates that page)."""
        alice, ben = _member("alice"), _member("ben", verified=False)
        event = _event()
        _attend(alice, event)
        _attend(ben, event)
        assert _photo(client, ben, alice).status_code == 403

    def test_co_attendee_after_connection_window(self, client):
        alice, ben = _member("alice"), _member("ben")
        event = _event(ended_hours_ago=24 * 10)
        _attend(alice, event)
        _attend(ben, event)
        assert _photo(client, ben, alice).status_code == 403

    def test_co_registrant_who_did_not_attend(self, client):
        alice, ben = _member("alice"), _member("ben")
        event = _event()
        _attend(alice, event)
        _attend(ben, event, status="confirmed")
        assert _photo(client, ben, alice).status_code == 403

    def test_blocked_pair_even_with_relationship(self, client):
        alice, ben = _member("alice"), _member("ben")
        event = _event()
        _attend(alice, event)
        _attend(ben, event)
        UserBlock.objects.create(blocker=alice, blocked=ben)
        assert _photo(client, ben, alice).status_code == 403

    def test_declined_connection(self, client):
        alice, ben = _member("alice"), _member("ben")
        event = _event(ended_hours_ago=24 * 30)
        EventConnection.objects.create(
            requester=ben, recipient=alice, event=event, status="declined"
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_recipient_of_unshared_crush_lead(self, client):
        """A My Crush! lead is never shown to its recipient until shared."""
        alice, ben = _member("alice"), _member("ben")
        event = _event(ended_hours_ago=24 * 30)
        EventConnection.objects.create(
            requester=alice,
            recipient=ben,
            event=event,
            status="coach_reviewing",
            flow=EventConnection.FLOW_CRUSH,
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_connect_card_without_photo_consent(self, client):
        alice = _member("alice", photo_consent=False)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(user=ben)
        ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=alice,
            generated_date=timezone.localdate(),
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_connect_chat_after_consent_withdrawn(self, client):
        alice = _member("alice", photo_consent=False)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(user=alice)
        request = ConnectWeeklyRequest.objects.create(
            session=session,
            requester=alice,
            recipient=ben,
            status="accepted",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        ConnectTemporaryChat.objects.create(
            request=request,
            participant_1=alice,
            participant_2=ben,
            expires_at=timezone.now() + timedelta(days=7),
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_hidden_encounter_even_with_relationship(self, client):
        alice, ben = _member("alice"), _member("ben")
        event = _event()
        _attend(alice, event)
        _attend(ben, event)
        low, high = sorted([alice, ben], key=lambda u: u.pk)
        ConfirmedEncounter.objects.create(
            user_low=low, user_high=high, status="removal_pending"
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_expired_cycle_card(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(user=ben)
        ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=alice,
            generated_date=timezone.localdate(),
            is_expired=True,
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_card_from_a_past_day(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(user=ben)
        ConnectWeekSession.objects.filter(pk=session.pk).update(
            started_at=timezone.now() - timedelta(days=2)
        )
        ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=alice,
            generated_date=timezone.localdate() - timedelta(days=2),
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_review_window_closed(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(
            user=ben,
            status=ConnectWeekSession.Status.REVIEW_OPEN,
            review_expires_at=timezone.now() - timedelta(minutes=1),
        )
        ConnectCycleCard.objects.create(
            session=session,
            day_number=2,
            card_index=1,
            target_user=alice,
            generated_date=timezone.localdate(),
            is_completed=True,
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_pending_request_past_its_deadline(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        session = ConnectWeekSession.objects.create(user=alice)
        ConnectWeeklyRequest.objects.create(
            session=session,
            requester=alice,
            recipient=ben,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_chat_partner_excluded_by_coach(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        CrushConnectMembership.objects.filter(user=alice).update(excluded_by_coach=True)
        session = ConnectWeekSession.objects.create(user=alice)
        request = ConnectWeeklyRequest.objects.create(
            session=session,
            requester=alice,
            recipient=ben,
            status="accepted",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        ConnectTemporaryChat.objects.create(
            request=request,
            participant_1=alice,
            participant_2=ben,
            expires_at=timezone.now() + timedelta(days=7),
        )
        assert _photo(client, ben, alice).status_code == 403

    def test_coach_pick_from_a_former_coach(self, client):
        alice = _member("alice", photo_consent=True)
        ben = _member("ben", photo_consent=True)
        old_coach = CrushCoach.objects.create(user=_member("old"), is_active=True)
        new_coach = CrushCoach.objects.create(user=_member("new"), is_active=True)
        CrushProfile.objects.filter(user=ben).update(assigned_coach=new_coach)
        PremiumMembership.objects.create(user=ben, coach=new_coach, status="active")
        ConnectCoachPick.objects.create(coach=old_coach, member=ben, candidate=alice)
        assert _photo(client, ben, alice).status_code == 403

    def test_plain_staff_is_not_a_coach(self, client):
        alice = _member("alice")
        staff = User.objects.create_user("staff", "st@example.com", "x", is_staff=True)
        assert _photo(client, staff, alice).status_code == 403


# ---------------------------------------------------------------------------
# quiz_display_photo
# ---------------------------------------------------------------------------


def _quiz(created_by, owner=None, status="attended"):
    event = _event(ended_hours_ago=-1)  # starts in the future: live quiz night
    event.event_type = "quiz_night"
    event.save(update_fields=["event_type"])
    quiz = QuizEvent.objects.create(event=event, status="active", created_by=created_by)
    if owner is not None:
        _attend(owner, event, status=status)
    return quiz


class TestQuizPhoto:
    def test_quiz_creator_sees_participant(self, client):
        host = _member("host")
        alice = _member("alice")
        _quiz(created_by=host, owner=alice)
        assert _quiz_photo(client, host, alice).status_code == 200

    def test_staff_sees_participant_of_a_quiz(self, client):
        host = _member("host")
        alice = _member("alice")
        _quiz(created_by=host, owner=alice, status="confirmed")
        staff = User.objects.create_user("staff", "st@example.com", "x", is_staff=True)
        assert _quiz_photo(client, staff, alice).status_code == 200

    def test_active_coach_and_owner(self, client):
        alice = _member("alice")
        coach = _member("coach")
        CrushCoach.objects.create(user=coach, is_active=True)
        assert _quiz_photo(client, coach, alice).status_code == 200
        assert _quiz_photo(client, alice, alice).status_code == 200

    def test_unrelated_approved_member_refused(self, client):
        alice, mallory = _member("alice"), _member("mallory")
        _quiz(created_by=_member("host"), owner=alice)
        assert _quiz_photo(client, mallory, alice).status_code == 403

    def test_fellow_quiz_player_refused(self, client):
        """Only the projector renders these photos; players never need them."""
        host = _member("host")
        alice, ben = _member("alice"), _member("ben")
        quiz = _quiz(created_by=host, owner=alice)
        _attend(ben, quiz.event)
        assert _quiz_photo(client, ben, alice).status_code == 403

    def test_account_without_profile_refused(self, client):
        alice = _member("alice")
        _quiz(created_by=_member("host"), owner=alice)
        stranger = User.objects.create_user("stranger", "s@example.com", "x")
        assert _quiz_photo(client, stranger, alice).status_code == 403

    def test_staff_refused_for_member_in_no_quiz(self, client):
        alice = _member("alice")
        staff = User.objects.create_user("staff", "st@example.com", "x", is_staff=True)
        assert _quiz_photo(client, staff, alice).status_code == 403

    def test_creator_refused_for_member_not_in_their_quiz(self, client):
        host = _member("host")
        alice = _member("alice")
        _quiz(created_by=host)
        _quiz(created_by=_member("other_host"), owner=alice)
        assert _quiz_photo(client, host, alice).status_code == 403
