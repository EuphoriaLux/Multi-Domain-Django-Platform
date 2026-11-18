"""Coach playground: the cast, the Connect buttons and the practice mixer.

Every button goes through the real ``connect_cycle`` / check-in / lobby code,
so these tests fail when a Connect or event rule changes under the playground.

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md
"""

from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.signing import Signer
from django.test import Client
from django.utils import timezone

from crush_lu.connect_phase import cycle_access_open
from crush_lu.models import (
    CrushCoach,
    CrushConnectWaitlist,
    CrushProfile,
    EventRegistration,
    PlaygroundAccount,
    UserDataConsent,
)
from crush_lu.models.crush_connect_cycle import (
    ConnectPairExclusion,
    ConnectTemporaryChat,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)
from crush_lu.services import playground_cast as cast
from crush_lu.services import playground_events as mixer
from crush_lu.services import playground_practice as practice
from crush_lu.services.connect_cycle import get_cycle_eligible_pool
from crush_lu.services.connect_verification import human_verification_method
from crush_lu.services.crush_connect import is_catalogue_eligible

pytestmark = pytest.mark.django_db

HOST = "test.crush.lu"


@pytest.fixture(autouse=True)
def staging(settings, monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "staging")
    settings.DEBUG = False
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "public"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.COACH_PLAYGROUND_SSO_SECRET = ""
    cache.clear()
    with mock.patch(
        "crush_lu.services.video_call.video_calls_available", return_value=True
    ):
        yield settings
    cache.clear()


@pytest.fixture
def coach():
    user = User.objects.create_user(
        username="ana_coach",
        email="ana_coach@crush.lu",
        password="pw-12345678",
        first_name="Ana",
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return CrushCoach.objects.create(user=user, is_active=True, bio="coach")


@pytest.fixture
def seeded(coach):
    cast.seed_cast(coach, download=False)
    return coach


def _second_coach():
    user = User.objects.create_user(
        username="ben_coach",
        email="ben_coach@crush.lu",
        password="pw-12345678",
        first_name="Ben",
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return CrushCoach.objects.create(user=user, is_active=True, bio="coach")


def _lea():
    return cast.cast_member("lea")


# ---------------------------------------------------------------------------
# Cast
# ---------------------------------------------------------------------------


class TestCast:
    def test_command_refuses_off_staging(self, coach, monkeypatch):
        monkeypatch.delenv("DJANGO_ENV")
        with pytest.raises(CommandError):
            call_command("seed_playground_cast", "--no-download")

    def test_command_is_idempotent(self, coach):
        call_command("seed_playground_cast", "--no-download")
        first = set(cast.cast_queryset().values_list("pk", flat=True))
        call_command("seed_playground_cast", "--no-download")
        assert set(cast.cast_queryset().values_list("pk", flat=True)) == first
        assert len(first) == 12

    def test_cast_list_is_frozen(self):
        # Coaches, tests and staging know these twelve: an edit to the staging
        # persona set must not add or rename a cast member.
        chosen = cast.personas()
        assert [p.key for p in chosen] == [key for key, _gender in cast.CAST_KEYS]
        assert [p.gender for p in chosen].count("F") == 6
        assert [p.key for p in chosen if not p.luxid] == ["sam"]
        assert len({p.photo_url for p in chosen if p.photo_url}) == 11

    def test_a_name_missing_from_the_persona_set_keeps_its_key(self):
        from crush_lu.management.commands import enrich_staging_candidates as source

        without_lea = [
            c for c in source.CURATED_FEMALE_CANDIDATES if c["first_name"] != "Lea"
        ]
        with mock.patch.object(source, "CURATED_FEMALE_CANDIDATES", without_lea):
            chosen = cast.personas()
        assert [p.key for p in chosen] == [key for key, _gender in cast.CAST_KEYS]
        lea = next(p for p in chosen if p.key == "lea")
        assert (lea.first_name, lea.gender, lea.photo_url) == ("Lea", "F", "")

    def test_seed_retires_cast_members_no_longer_listed(self, seeded):
        shorter = tuple(k for k in cast.CAST_KEYS if k[0] != "lea")
        with mock.patch.object(cast, "CAST_KEYS", shorter):
            call_command("seed_playground_cast", "--no-download")
        lea = User.objects.get(username="cast_lea")
        assert not lea.is_active
        assert cast.cast_queryset().count() == 11
        # Back on the list: the next seed brings the same account back.
        call_command("seed_playground_cast", "--no-download")
        lea.refresh_from_db()
        assert lea.is_active
        assert cast.cast_queryset().count() == 12

    def test_seed_explains_a_taken_username(self, coach):
        User.objects.create_user(username="cast_marie", email="marie@example.com")
        with pytest.raises(CommandError, match="cast_username_taken"):
            call_command("seed_playground_cast", "--no-download")

    def test_cast_members_are_safe(self, seeded):
        for member in cast.cast_queryset():
            profile = CrushProfile.objects.get(user=member)
            assert member.email.endswith("@crush.test")
            assert not member.has_usable_password()
            assert not member.is_staff
            assert profile.phone_number in ("", None)
            assert profile.assigned_coach_id is None
            assert not profile.has_active_premium
            assert not CrushConnectWaitlist.objects.filter(
                user=member, selected_as_tester=True
            ).exists()
            assert PlaygroundAccount.objects.get(user=member).kind == "cast"

    def test_luxid_cast_is_catalogue_eligible(self, seeded):
        for member in cast.cast_queryset().exclude(username="cast_sam"):
            assert is_catalogue_eligible(member), member.username
        assert not is_catalogue_eligible(cast.cast_member("sam"))

    def test_activity_refresh_is_throttled(self, seeded):
        assert cast.refresh_cast_activity() == 12
        assert cast.refresh_cast_activity() == 0
        assert cast.refresh_cast_activity(force=True) == 12


# ---------------------------------------------------------------------------
# Connect buttons
# ---------------------------------------------------------------------------


@pytest.fixture
def ready_member(seeded):
    return practice.apply_preset(seeded, "connect_ready")


class TestConnect:
    def test_connect_ready_member_sees_the_cast(self, ready_member):
        assert human_verification_method(ready_member) == "video"
        assert cycle_access_open(ready_member)
        assert is_catalogue_eligible(ready_member)
        pool = set(get_cycle_eligible_pool(ready_member).values_list("pk", flat=True))
        luxid_cast = set(
            cast.cast_queryset()
            .exclude(username="cast_sam")
            .values_list("pk", flat=True)
        )
        assert luxid_cast <= pool

    def test_onboarding_todo_is_not_onboarded(self, seeded):
        member = practice.apply_preset(seeded, "connect_onboarding_todo")
        assert human_verification_method(member) == "video"
        membership = getattr(member, "crush_connect_membership", None)
        assert membership is None or membership.onboarded_at is None

    def test_cast_sends_a_request(self, ready_member):
        lea = _lea()
        request = cast.cast_sends_request(ready_member, lea)
        assert request.recipient_id == ready_member.pk
        assert request.status == ConnectWeeklyRequest.Status.PENDING

    def test_a_second_request_from_the_same_cast_member_is_refused(self, ready_member):
        lea = _lea()
        cast.cast_sends_request(ready_member, lea)
        with pytest.raises(practice.PlaygroundError) as excinfo:
            cast.cast_sends_request(ready_member, lea)
        assert excinfo.value.code == "excluded"

    def _own_request(self, member, target):
        from crush_lu.models.crush_connect_cycle import ConnectCycleCard

        session = ConnectWeekSession.objects.create(user=member)
        ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=target,
            generated_date=timezone.localdate(),
            is_completed=True,
            completed_at=timezone.now(),
        )
        session.open_weekly_review()
        return ConnectWeeklyRequest.objects.create(
            session=session, requester=member, recipient=target
        )

    def test_cast_accepts_and_replies(self, ready_member):
        lea = _lea()
        self._own_request(ready_member, lea)
        cast.cast_answers_request(ready_member, lea, accept=True)
        chat = ConnectTemporaryChat.objects.get(
            participant_1__in=[ready_member, lea], participant_2__in=[ready_member, lea]
        )
        cast.cast_replies(ready_member, lea)
        assert chat.messages.filter(sender=lea).count() == 1

    def test_cast_reply_is_written_in_the_coach_language(self, ready_member):
        from django.utils import translation

        lea = _lea()
        self._own_request(ready_member, lea)
        cast.cast_answers_request(ready_member, lea, accept=True)
        with translation.override("fr"):
            cast.cast_replies(ready_member, lea)
        with translation.override("de"):
            cast.cast_replies(ready_member, lea)
        chat = ConnectTemporaryChat.objects.get(
            participant_1__in=[ready_member, lea], participant_2__in=[ready_member, lea]
        )
        texts = list(
            chat.messages.filter(sender=lea)
            .order_by("pk")
            .values_list("message", flat=True)
        )
        assert texts[0].startswith("Bonjour !")
        assert texts[1].startswith("Haha, da musste")

    def test_cast_declines(self, ready_member):
        lea = _lea()
        self._own_request(ready_member, lea)
        cast.cast_answers_request(ready_member, lea, accept=False)
        assert ConnectPairExclusion.are_excluded(ready_member, lea)

    def test_answer_without_a_request(self, ready_member):
        with pytest.raises(practice.PlaygroundError) as excinfo:
            cast.cast_answers_request(ready_member, _lea(), accept=True)
        assert excinfo.value.code == "no_pending_request"

    def test_let_it_expire(self, ready_member):
        lea = _lea()
        request = cast.cast_sends_request(ready_member, lea)
        cast.let_request_expire(ready_member, lea)
        request.refresh_from_db()
        assert request.status == ConnectWeeklyRequest.Status.EXPIRED

    def test_jump_to_review(self, ready_member):
        ConnectWeekSession.objects.create(user=ready_member)
        session = cast.jump_to_review(ready_member)
        assert session.status == ConnectWeekSession.Status.REVIEW_OPEN

    def test_reset_puts_the_cast_member_back_in_the_pool(self, ready_member):
        lea = _lea()
        self._own_request(ready_member, lea)
        cast.cast_answers_request(ready_member, lea, accept=False)
        assert lea.pk not in set(
            get_cycle_eligible_pool(ready_member).values_list("pk", flat=True)
        )
        cast.reset_connect_history(ready_member)
        assert lea.pk in set(
            get_cycle_eligible_pool(ready_member).values_list("pk", flat=True)
        )
        assert not ConnectWeekSession.objects.filter(user=ready_member).exists()

    def test_retired_member_leaves_the_pool(self, seeded):
        old = practice.apply_preset(seeded, "connect_ready")
        other = practice.apply_preset(_second_coach(), "connect_ready")
        assert old.pk in set(
            get_cycle_eligible_pool(other).values_list("pk", flat=True)
        )
        practice.apply_preset(seeded, "connect_ready")
        old.refresh_from_db()
        assert not is_catalogue_eligible(old)
        assert old.pk not in set(
            get_cycle_eligible_pool(other).values_list("pk", flat=True)
        )

    def test_buttons_refuse_off_staging(self, ready_member, monkeypatch):
        monkeypatch.delenv("DJANGO_ENV")
        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            cast.cast_sends_request(ready_member, _lea())


# ---------------------------------------------------------------------------
# Practice mixer and the event route
# ---------------------------------------------------------------------------


def _scan(coach, registration):
    token = Signer().sign(f"{registration.pk}:{registration.event_id}")
    client = Client(HTTP_HOST=HOST)
    client.force_login(coach.user)
    return client.post(f"/api/events/checkin/{registration.pk}/{token}/")


class TestMixer:
    def test_one_mixer_per_coach_kept_ahead(self, coach):
        event = mixer.ensure_practice_mixer(coach.user)
        assert mixer.ensure_practice_mixer(coach.user).pk == event.pk
        assert event.date_time > timezone.now()
        assert not event.coaches.exists()

    def test_member_registers_on_the_real_page(self, seeded):
        member = practice.apply_preset(seeded, "luxid_ready")
        event = mixer.ensure_practice_mixer(seeded.user)
        assert not EventRegistration.objects.filter(event=event, user=member).exists()
        client = Client(HTTP_HOST=HOST)
        client.force_login(member)
        client.post(f"/en/events/{event.pk}/register/", {})
        registration = EventRegistration.objects.get(event=event, user=member)
        assert registration.status == "confirmed"
        assert client.get(f"/en/events/{event.pk}/ticket/").status_code == 200

    def test_door_page_loads_for_a_coach_not_on_the_event(self, seeded):
        event = mixer.ensure_practice_mixer(seeded.user)
        assert not event.coaches.filter(pk=seeded.pk).exists()
        response = Client(HTTP_HOST=HOST)
        response.force_login(seeded.user)
        assert response.get(f"/en/coach/events/{event.pk}/checkin/").status_code == 200

    def test_door_scan_verifies_a_first_time_guest(self, seeded):
        member = practice.apply_preset(seeded, "event_ticket_pending")
        registration = EventRegistration.objects.get(
            event=mixer.practice_mixer(seeded.user), user=member
        )
        response = _scan(seeded, registration)
        assert response.status_code == 200, response.content
        registration.refresh_from_db()
        assert registration.status == "attended"
        profile = CrushProfile.objects.get(user=member)
        assert profile.verification_status == "verified"
        assert human_verification_method(member) == "event"

    def test_lobby_after_the_door_scan(self, seeded):
        from crush_lu.models import EventLobbyParticipation

        member = practice.apply_preset(seeded, "event_lobby_ready")
        event = mixer.practice_mixer(seeded.user)
        registration = EventRegistration.objects.get(event=event, user=member)
        assert _scan(seeded, registration).status_code == 200
        assert EventLobbyParticipation.objects.filter(event=event, user=member).exists()
        assert mixer.fill_the_room(seeded.user) == 12
        # Sam has no LuxID: everyone else joins the Lobby, plus the member.
        assert EventLobbyParticipation.objects.filter(event=event).count() == 12

    def test_my_crush_with_sam_after_the_end(self, seeded):
        from crush_lu.services.event_lobby import crush_flow_decision

        member = practice.apply_preset(seeded, "event_lobby_ready")
        event = mixer.practice_mixer(seeded.user)
        registration = EventRegistration.objects.get(event=event, user=member)
        _scan(seeded, registration)
        mixer.fill_the_room(seeded.user)
        event = mixer.end_practice_mixer(seeded.user)
        sam = cast.cast_member("sam")
        lea = _lea()
        assert crush_flow_decision(member, sam, event) == "crush"
        assert crush_flow_decision(member, lea, event) == "redirect"

    def test_event_verified_without_luxid(self, seeded):
        member = practice.apply_preset(seeded, "event_verified_no_luxid")
        assert human_verification_method(member) == "event"
        assert not CrushProfile.objects.get(user=member).has_luxid_connected

    def test_fresh_mixer_archives_the_old_one(self, coach):
        old = mixer.ensure_practice_mixer(coach.user)
        new = mixer.fresh_practice_mixer(coach.user)
        old.refresh_from_db()
        assert new.pk != old.pk
        assert not old.is_published
        assert "archived" in old.title
        for column in ("title_en", "title_de", "title_fr"):
            assert "archived" in getattr(old, column)

    def test_the_mixer_is_the_same_in_every_language(self, coach):
        # The title is translated: a plain title= lookup only read the active
        # language's column, so each UI language made its own mixer.
        from django.utils import translation

        with translation.override("fr"):
            event = mixer.ensure_practice_mixer(coach.user)
        for language in ("de", "en"):
            with translation.override(language):
                assert mixer.ensure_practice_mixer(coach.user).pk == event.pk
                assert mixer.practice_mixer(coach.user).pk == event.pk
        event.refresh_from_db()
        title = mixer.mixer_title(coach.user)
        assert (event.title_en, event.title_de, event.title_fr) == (title,) * 3
        assert event.description_de == event.description_fr == mixer.DESCRIPTION

    def test_per_language_duplicates_are_archived(self, coach):
        from django.utils import translation

        from crush_lu.models import MeetupEvent

        title = mixer.mixer_title(coach.user)
        older = mixer.ensure_practice_mixer(coach.user)
        MeetupEvent.objects.filter(pk=older.pk).update(title_en=None, title_de=None)
        newer = MeetupEvent.objects.get(pk=older.pk)
        newer.pk = newer.id = None
        newer.title_fr = None
        newer.title_de = title
        newer.save()

        with translation.override("en"):
            kept = mixer.ensure_practice_mixer(coach.user)
        assert kept.pk == newer.pk
        older.refresh_from_db()
        assert not older.is_published
        kept.refresh_from_db()
        assert kept.title_en == kept.title_fr == title

    def test_start_again_frees_an_attended_seat(self, seeded):
        member = practice.apply_preset(seeded, "event_lobby_ready")
        event = mixer.practice_mixer(seeded.user)
        registration = EventRegistration.objects.get(event=event, user=member)
        assert _scan(seeded, registration).status_code == 200
        registration.refresh_from_db()
        assert registration.status == "attended"

        practice.apply_preset(seeded, "luxid_ready")
        registration.refresh_from_db()
        # Not left checked in for the next practice member (door page, My Crush).
        assert registration.status == "cancelled"

    def test_ticket_after_the_mixer_ended_gets_a_fresh_mixer(self, seeded):
        ended = mixer.ensure_practice_mixer(seeded.user)
        mixer.end_practice_mixer(seeded.user)
        member = practice.apply_preset(seeded, "event_ticket_pending")
        registration = EventRegistration.objects.get(user=member)
        assert registration.event_id != ended.pk
        assert registration.event.date_time > timezone.now()
        ended.refresh_from_db()
        assert not ended.is_published

    def test_an_upcoming_mixer_is_kept_for_a_ticket(self, seeded):
        event = mixer.ensure_practice_mixer(seeded.user)
        member = practice.apply_preset(seeded, "event_ticket_pending")
        assert EventRegistration.objects.get(user=member).event_id == event.pk

    def test_practice_mixers_never_reach_echo_lu_or_google(self, coach):
        from crush_lu.models import MeetupEvent
        from crush_lu.services import echo_lu
        from crush_lu.services.google_indexing import should_index_event

        archived = mixer.ensure_practice_mixer(coach.user)
        live = mixer.fresh_practice_mixer(coach.user)
        archived.refresh_from_db()
        for event in (live, archived):
            assert mixer.is_practice_mixer(event)
            assert not should_index_event(event)
            assert not echo_lu.should_publish(event)
            assert not echo_lu._cancellation_notice_wanted(event)
        pending = echo_lu.events_needing_sync(MeetupEvent.objects.all())
        assert live.pk not in set(pending.values_list("pk", flat=True))

    def test_a_real_event_is_still_listed(self, coach):
        from crush_lu.services import echo_lu
        from crush_lu.services.google_indexing import should_index_event

        event = mixer.ensure_practice_mixer(coach.user)
        for column in ("title_en", "title_de", "title_fr"):
            setattr(event, column, "Friday Social Mixer")
        assert not mixer.is_practice_mixer(event)
        assert should_index_event(event)
        assert echo_lu.should_publish(event)

    def test_cast_gets_no_coach_from_the_room(self, seeded):
        mixer.ensure_practice_mixer(seeded.user)
        mixer.fill_the_room(seeded.user)
        assert not CrushProfile.objects.filter(
            user__playground_account__kind="cast", assigned_coach__isnull=False
        ).exists()


class TestQuizRehearsalGuard:
    def test_practice_member_is_never_seated(self, coach):
        from crush_lu.services.quiz_rehearsal import _attend_and_seat

        member = practice.apply_preset(coach, "luxid_ready")
        quiz = mock.Mock()
        _attend_and_seat(quiz, member)
        assert not EventRegistration.objects.filter(user=member).exists()


# ---------------------------------------------------------------------------
# The buttons on the playground page
# ---------------------------------------------------------------------------


def _coach_client(coach):
    client = Client(HTTP_HOST=HOST)
    client.force_login(coach.user)
    return client


class TestButtons:
    def test_page_shows_cast_and_mixer(self, seeded):
        practice.apply_preset(seeded, "connect_ready")
        page = _coach_client(seeded).get("/en/coach/playground/").content.decode()
        assert "Practise Connect Week" in page
        assert 'value="lea"' in page
        assert "Practice mixer (coach" in page

    def test_page_without_cast_says_so(self, coach):
        page = _coach_client(coach).get("/en/coach/playground/").content.decode()
        assert "seed_playground_cast" in page

    def test_send_button(self, ready_member, seeded):
        response = _coach_client(seeded).post(
            "/en/coach/playground/connect/", {"action": "send", "cast": "lea"}
        )
        assert response.status_code == 302
        assert ConnectWeeklyRequest.objects.filter(
            requester=_lea(), recipient=ready_member
        ).exists()

    def test_refused_button_shows_a_message(self, ready_member, seeded):
        client = _coach_client(seeded)
        client.post("/en/coach/playground/connect/", {"action": "send", "cast": "lea"})
        response = client.post(
            "/en/coach/playground/connect/",
            {"action": "send", "cast": "lea"},
            follow=True,
        )
        assert "already met your practice member" in response.content.decode()

    def test_mixer_buttons(self, seeded):
        client = _coach_client(seeded)
        client.get("/en/coach/playground/")
        assert (
            client.post("/en/coach/playground/mixer/", {"action": "fill"}).status_code
            == 302
        )
        client.post("/en/coach/playground/mixer/", {"action": "end"})
        assert mixer.practice_mixer(seeded.user).date_time < timezone.now()

    @pytest.mark.parametrize(
        "path", ["/en/coach/playground/connect/", "/en/coach/playground/mixer/"]
    )
    def test_buttons_404_in_production(self, seeded, monkeypatch, path):
        monkeypatch.delenv("DJANGO_ENV")
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(seeded.user)
        assert client.post(path, {"action": "fill"}).status_code == 404
        assert client.get(path).status_code == 404
