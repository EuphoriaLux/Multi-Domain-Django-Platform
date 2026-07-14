"""Tests for the Crush Cache scavenger hunt (geo, models, views, flows)."""

import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from crush_lu.geo import bearing_deg, haversine_m
from crush_lu.models import CrushCoach, EventRegistration, MeetupEvent
from crush_lu.models.crush_cache import (
    CacheChallenge,
    CacheChallengeAttempt,
    CacheHunt,
    CacheStation,
    CacheStationAttempt,
    CacheTeam,
    CacheTeamMember,
    CacheTeamProgress,
)

# Gëlle Fra and Pont Adolphe, Luxembourg City — ~280 m apart
GELLE_FRA = (49.60972, 6.12917)
PONT_ADOLPHE = (49.60750, 6.12722)


def _grant_consent(user):
    """Grant GDPR consent + verified email (mirrors test_quiz.py)."""
    from allauth.account.models import EmailAddress
    from crush_lu.models.profiles import UserDataConsent

    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save()
    if user.email:
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(autouse=True)
def cache_enabled(settings):
    settings.CRUSH_CACHE_ENABLED = True
    # Match the urlconf the domain middleware picks for the test host, so
    # reverse() and the routed request agree (same pattern as test_events.py)
    settings.ROOT_URLCONF = "azureproject.urls_crush"


@pytest.fixture(autouse=True)
def _isolate_ratelimit_counters():
    """LocMem cache outlives the per-test DB rollback while SQLite reuses
    primary keys across tests — without this, @ratelimit counters keyed on
    user_<pk> leak between tests in the same worker."""
    from django.core.cache import cache

    cache.clear()
    yield


@pytest.fixture
def player(db):
    user = User.objects.create_user(
        username="cacheplayer@test.com",
        email="cacheplayer@test.com",
        password="testpass123",
    )
    _grant_consent(user)
    return user


@pytest.fixture
def teammate(db):
    user = User.objects.create_user(
        username="cachemate@test.com",
        email="cachemate@test.com",
        password="testpass123",
    )
    _grant_consent(user)
    return user


@pytest.fixture
def coach_user(db):
    user = User.objects.create_user(
        username="cachecoach@test.com",
        email="cachecoach@test.com",
        password="testpass123",
        is_staff=True,
    )
    _grant_consent(user)
    CrushCoach.objects.create(
        user=user,
        bio="Hunt master",
        specializations="Scavenger hunts",
        is_active=True,
    )
    return user


@pytest.fixture
def hunt(db, coach_user):
    event = MeetupEvent.objects.create(
        title="City Hunt Night",
        description="Crush Cache through the city",
        event_type="crush_cache",
        date_time=timezone.now() + timedelta(hours=1),
        location="Luxembourg City",
        address="1 Place d'Armes",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(minutes=30),
        is_published=True,
    )
    return CacheHunt.objects.create(
        event=event,
        title="Old Town Hunt",
        status="draft",
        created_by=coach_user,
        team_size_max=2,
    )


@pytest.fixture
def stations(hunt):
    s1 = CacheStation.objects.create(
        hunt=hunt,
        order=1,
        name="Gëlle Fra",
        unlock_mode="gps",
        latitude=GELLE_FRA[0],
        longitude=GELLE_FRA[1],
        radius_meters=25,
    )
    s2 = CacheStation.objects.create(
        hunt=hunt,
        order=2,
        name="Pont Adolphe",
        unlock_mode="none",
    )
    CacheChallenge.objects.create(
        station=s1,
        challenge_order=1,
        challenge_type="riddle",
        question="What is golden and watches over the city?",
        correct_answer="Gelle Fra",
        alternative_answers=["golden lady"],
        points_awarded=100,
        hint_1="She stands on an obelisk.",
        hint_1_cost=20,
    )
    CacheChallenge.objects.create(
        station=s2,
        challenge_order=1,
        challenge_type="open_text",
        question="Which river does the bridge cross?",
        correct_answer="Petrusse",
        points_awarded=50,
    )
    return [s1, s2]


@pytest.fixture
def registration(hunt, player):
    return EventRegistration.objects.create(
        event=hunt.event, user=player, status="attended"
    )


@pytest.fixture
def team(hunt, registration):
    team = CacheTeam.objects.create(hunt=hunt, name="Team Test", join_code="ABC234")
    CacheTeamMember.objects.create(hunt=hunt, team=team, registration=registration)
    return team


def _start_hunt(hunt):
    hunt.status = "live"
    hunt.started_at = timezone.now()
    hunt.save()
    for t in hunt.teams.all():
        CacheTeamProgress.objects.get_or_create(
            team=t,
            defaults={
                "current_station": hunt.stations.order_by("order").first(),
                "started_at": timezone.now(),
            },
        )


# ============================================================================
# GEO MATH
# ============================================================================


class TestGeo:
    def test_haversine_known_distance(self):
        d = haversine_m(*GELLE_FRA, *PONT_ADOLPHE)
        assert 230 <= d <= 330  # ~280 m as the crow flies

    def test_haversine_zero(self):
        assert haversine_m(*GELLE_FRA, *GELLE_FRA) == pytest.approx(0)

    def test_haversine_accepts_decimals(self):
        from decimal import Decimal

        d = haversine_m(Decimal("49.60972"), Decimal("6.12917"), *PONT_ADOLPHE)
        assert 230 <= d <= 330

    def test_bearing_range(self):
        b = bearing_deg(*GELLE_FRA, *PONT_ADOLPHE)
        assert 0 <= b < 360
        # Pont Adolphe is southwest of the Gëlle Fra
        assert 180 <= b <= 270


# ============================================================================
# MODELS
# ============================================================================


class TestCheckAnswer:
    def _challenge(self, **kwargs):
        return CacheChallenge(
            question="q", correct_answer=kwargs.pop("correct", "Answer"), **kwargs
        )

    def test_exact_and_normalized(self):
        c = self._challenge()
        assert c.check_answer("Answer")
        assert c.check_answer("  answer  ")
        assert not c.check_answer("wrong")

    def test_alternatives(self):
        c = self._challenge(alternative_answers=["Alt One", "alt two"])
        assert c.check_answer("ALT ONE")
        assert c.check_answer("alt two")

    def test_blank_accepts_everything(self):
        c = self._challenge(correct="")
        assert c.check_answer("anything at all")

    def test_accent_folding(self):
        """Players shouldn't lose points over accents they can't type
        outdoors: Pétrusse == petrusse, Gëlle == gelle."""
        c = self._challenge(correct="Pétrusse")
        assert c.check_answer("petrusse")
        assert c.check_answer("PÉTRUSSE")
        c = self._challenge(correct="Gelle Fra")
        assert c.check_answer("Gëlle Fra")

    def test_accents_fold_in_alternatives(self):
        c = self._challenge(correct="x", alternative_answers=["Vallée de la Pétrusse"])
        assert c.check_answer("vallee de la petrusse")

    def test_inner_whitespace_collapsed(self):
        c = self._challenge(correct="Gelle  Fra")
        assert c.check_answer("gelle fra")
        assert c.check_answer("  gelle   fra ")


@pytest.mark.django_db
class TestModels:
    def test_unlock_state_derivation(self, hunt, stations, team):
        attempt = CacheStationAttempt.objects.create(team=team, station=stations[0])
        assert not attempt.is_unlocked  # gps mode, not arrived
        attempt.arrived_at = timezone.now()
        assert attempt.is_unlocked

        stations[0].unlock_mode = "gps_qr"
        attempt.station = stations[0]
        assert not attempt.is_unlocked  # arrived but not scanned
        attempt.scanned_at = timezone.now()
        assert attempt.is_unlocked

    def test_one_team_per_hunt_constraint(self, hunt, registration, team):
        other = CacheTeam.objects.create(hunt=hunt, name="Other", join_code="XYZ789")
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            CacheTeamMember.objects.create(
                hunt=hunt, team=other, registration=registration
            )

    def test_leaderboard_ordering(self, hunt, stations, team, teammate):
        reg2 = EventRegistration.objects.create(
            event=hunt.event, user=teammate, status="attended"
        )
        team2 = CacheTeam.objects.create(hunt=hunt, name="Speedy", join_code="QQQ222")
        CacheTeamMember.objects.create(hunt=hunt, team=team2, registration=reg2)

        now = timezone.now()
        CacheTeamProgress.objects.create(team=team, total_points=100)
        CacheTeamProgress.objects.create(
            team=team2, total_points=100, is_finished=True, finished_at=now
        )

        board = hunt.get_leaderboard()
        # Equal points: the finished team ranks first
        assert board[0]["team_id"] == team2.id
        assert board[0]["rank"] == 1
        assert board[1]["team_id"] == team.id

    def test_readiness_check_flags_missing_coords(self, hunt, stations):
        stations[0].latitude = None
        stations[0].longitude = None
        stations[0].save()
        checks = {c["label"]: c["ok"] for c in hunt.readiness_check()}
        assert not checks["GPS coordinates"]


# ============================================================================
# FEATURE FLAG + ACCESS
# ============================================================================


@pytest.mark.django_db
class TestAccess:
    def test_flag_off_404s(self, client, hunt, player, settings):
        settings.CRUSH_CACHE_ENABLED = False
        client.force_login(player)
        url = reverse("crush_lu:cache_lobby", args=[hunt.event_id])
        assert client.get(url).status_code == 404

    def test_lobby_requires_login(self, client, hunt):
        url = reverse("crush_lu:cache_lobby", args=[hunt.event_id])
        response = client.get(url)
        assert response.status_code == 302

    def test_lobby_renders_for_registered_user(
        self, client, hunt, registration, player
    ):
        client.force_login(player)
        url = reverse("crush_lu:cache_lobby", args=[hunt.event_id])
        response = client.get(url)
        assert response.status_code == 200
        assert b"Old Town Hunt" in response.content

    def test_position_api_rejects_non_member(self, client, hunt, registration, player):
        client.force_login(player)
        url = reverse("crush_lu:cache_position_api", args=[hunt.event_id])
        response = client.post(
            url,
            json.dumps({"lat": 49.6, "lng": 6.1, "accuracy": 10}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_coach_dashboard_blocks_non_coach(self, client, hunt, registration, player):
        client.force_login(player)
        url = reverse("crush_lu:cache_coach_dashboard", args=[hunt.event_id])
        response = client.get(url)
        assert response.status_code == 302  # redirected to dashboard


# ============================================================================
# TEAM JOINING
# ============================================================================


@pytest.mark.django_db
class TestTeams:
    def test_create_team(self, client, hunt, registration, player):
        client.force_login(player)
        url = reverse("crush_lu:cache_join_team", args=[hunt.event_id])
        response = client.post(url, {"team_name": "The Finders"})
        assert response.status_code == 302
        team = hunt.teams.get(name="The Finders")
        assert team.members.filter(registration=registration).exists()

    def test_join_by_code(self, client, hunt, team, teammate):
        EventRegistration.objects.create(
            event=hunt.event, user=teammate, status="confirmed"
        )
        client.force_login(teammate)
        url = reverse("crush_lu:cache_join_team", args=[hunt.event_id])
        response = client.post(url, {"join_code": "abc234"})  # case-insensitive
        assert response.status_code == 302
        assert team.members.count() == 2

    def test_join_full_team(self, client, hunt, team, teammate):
        # team_size_max is 2; fill the second slot first
        filler = User.objects.create_user(
            username="filler@test.com", email="filler@test.com", password="x"
        )
        _grant_consent(filler)
        reg_f = EventRegistration.objects.create(
            event=hunt.event, user=filler, status="confirmed"
        )
        CacheTeamMember.objects.create(hunt=hunt, team=team, registration=reg_f)

        EventRegistration.objects.create(
            event=hunt.event, user=teammate, status="confirmed"
        )
        client.force_login(teammate)
        url = reverse("crush_lu:cache_join_team", args=[hunt.event_id])
        client.post(url, {"join_code": "ABC234"})
        assert team.members.count() == 2  # unchanged

    def test_unregistered_cannot_join(self, client, hunt, teammate):
        client.force_login(teammate)
        url = reverse("crush_lu:cache_join_team", args=[hunt.event_id])
        client.post(url, {"team_name": "Ghosts"})
        assert not hunt.teams.filter(name="Ghosts").exists()

    def test_leave_locked_once_live(self, client, hunt, stations, team, player):
        _start_hunt(hunt)
        client.force_login(player)
        url = reverse("crush_lu:cache_leave_team", args=[hunt.event_id])
        client.post(url)
        assert team.members.count() == 1  # still in


# ============================================================================
# GPS POSITION / ARRIVAL
# ============================================================================


@pytest.mark.django_db
class TestPosition:
    def _post(self, client, hunt, lat, lng, accuracy=10):
        url = reverse("crush_lu:cache_position_api", args=[hunt.event_id])
        return client.post(
            url,
            json.dumps({"lat": lat, "lng": lng, "accuracy": accuracy}),
            content_type="application/json",
        )

    def test_far_away_not_arrived(self, client, hunt, stations, team, player):
        _start_hunt(hunt)
        client.force_login(player)
        response = self._post(client, hunt, *PONT_ADOLPHE)  # ~280 m away
        data = response.json()
        assert data["ok"] and not data["arrived"]
        attempt = CacheStationAttempt.objects.get(team=team, station=stations[0])
        assert attempt.arrived_at is None

    def test_nearby_sets_arrival_once(self, client, hunt, stations, team, player):
        _start_hunt(hunt)
        client.force_login(player)
        response = self._post(client, hunt, *GELLE_FRA)
        data = response.json()
        assert data["arrived"] and data["unlocked"]
        attempt = CacheStationAttempt.objects.get(team=team, station=stations[0])
        first_arrival = attempt.arrived_at
        assert first_arrival is not None

        # Idempotent: a second fix doesn't move the timestamp
        self._post(client, hunt, *GELLE_FRA)
        attempt.refresh_from_db()
        assert attempt.arrived_at == first_arrival

    def test_junk_accuracy_rejected(self, client, hunt, stations, team, player):
        _start_hunt(hunt)
        client.force_login(player)
        response = self._post(client, hunt, *GELLE_FRA, accuracy=5000)
        data = response.json()
        assert data["ok"] and not data["accepted"]
        attempt = CacheStationAttempt.objects.filter(
            team=team, station=stations[0]
        ).first()
        assert attempt is None or attempt.arrived_at is None

    def test_accuracy_cannot_fake_proximity(self, client, hunt, stations, team, player):
        _start_hunt(hunt)
        client.force_login(player)
        # ~280 m away with claimed 150 m accuracy: tolerance caps at 25+50=75 m
        response = self._post(client, hunt, *PONT_ADOLPHE, accuracy=150)
        assert not response.json()["arrived"]

    def test_updates_progress_position(self, client, hunt, stations, team, player):
        _start_hunt(hunt)
        client.force_login(player)
        self._post(client, hunt, *PONT_ADOLPHE)
        progress = CacheTeamProgress.objects.get(team=team)
        assert progress.last_lat is not None
        assert progress.last_position_at is not None


# ============================================================================
# QR SCANNING
# ============================================================================


@pytest.mark.django_db
class TestQRScan:
    def test_scan_current_station(self, client, hunt, stations, team, player):
        stations[0].unlock_mode = "qr"
        stations[0].save()
        _start_hunt(hunt)
        client.force_login(player)
        url = reverse("crush_lu:cache_qr_scan", args=[stations[0].qr_token])
        response = client.get(url)
        assert response.status_code == 302
        attempt = CacheStationAttempt.objects.get(team=team, station=stations[0])
        assert attempt.scanned_at is not None

    def test_scan_wrong_station_rejected(self, client, hunt, stations, team, player):
        _start_hunt(hunt)  # current station is stations[0]
        client.force_login(player)
        url = reverse("crush_lu:cache_qr_scan", args=[stations[1].qr_token])
        response = client.get(url)
        assert response.status_code == 302
        assert not CacheStationAttempt.objects.filter(
            team=team, station=stations[1], scanned_at__isnull=False
        ).exists()

    def test_scan_before_start_rejected(self, client, hunt, stations, team, player):
        client.force_login(player)
        url = reverse("crush_lu:cache_qr_scan", args=[stations[0].qr_token])
        client.get(url)
        assert not CacheStationAttempt.objects.filter(
            team=team, station=stations[0], scanned_at__isnull=False
        ).exists()

    def test_rescan_shows_info_message(self, client, hunt, stations, team, player):
        """A second scan of the current station is acknowledged, not silent."""
        stations[0].unlock_mode = "qr"
        stations[0].save()
        _start_hunt(hunt)
        client.force_login(player)
        url = reverse("crush_lu:cache_qr_scan", args=[stations[0].qr_token])
        client.get(url)
        response = client.get(url, follow=True)

        from django.contrib.messages import constants as msg_constants

        levels = [m.level for m in response.context["messages"]]
        assert msg_constants.INFO in levels

    def test_gps_qr_requires_both(self, client, hunt, stations, team, player):
        stations[0].unlock_mode = "gps_qr"
        stations[0].save()
        _start_hunt(hunt)
        client.force_login(player)
        client.get(reverse("crush_lu:cache_qr_scan", args=[stations[0].qr_token]))
        attempt = CacheStationAttempt.objects.get(team=team, station=stations[0])
        assert attempt.scanned_at is not None
        assert not attempt.is_unlocked  # still needs GPS arrival


# ============================================================================
# MANUAL CODE ENTRY (scanner fallback)
# ============================================================================


@pytest.mark.django_db
class TestManualCode:
    def _post_code(self, client, hunt, code, **kwargs):
        url = reverse("crush_lu:cache_manual_code", args=[hunt.event_id])
        return client.post(url, {"code": code}, **kwargs)

    def test_codes_generated_from_join_alphabet(self, hunt, stations):
        from crush_lu.models.crush_cache import JOIN_CODE_ALPHABET

        for station in stations:
            assert len(station.manual_code) == 6
            assert all(ch in JOIN_CODE_ALPHABET for ch in station.manual_code)
        assert stations[0].manual_code != stations[1].manual_code

    def test_manual_code_records_scan(self, client, hunt, stations, team, player):
        stations[0].unlock_mode = "qr"
        stations[0].save()
        _start_hunt(hunt)
        client.force_login(player)
        # Lowercase input must work — players type in a hurry
        response = self._post_code(client, hunt, stations[0].manual_code.lower())
        assert response.status_code == 302
        assert reverse("crush_lu:cache_play", args=[hunt.event_id]) in response.url
        attempt = CacheStationAttempt.objects.get(team=team, station=stations[0])
        assert attempt.scanned_at is not None

    def test_unknown_code_returns_to_scanner_with_error(
        self, client, hunt, stations, team, player
    ):
        _start_hunt(hunt)
        client.force_login(player)
        response = self._post_code(client, hunt, "XXXXXX", follow=True)

        from django.contrib.messages import constants as msg_constants

        assert (
            reverse("crush_lu:cache_scanner", args=[hunt.event_id])
            in [r[0] for r in response.redirect_chain][0]
        )
        levels = [m.level for m in response.context["messages"]]
        assert msg_constants.ERROR in levels

    def test_other_hunts_code_rejected(self, client, hunt, stations, team, player):
        other_event = MeetupEvent.objects.create(
            title="Other Hunt Night",
            event_type="crush_cache",
            date_time=timezone.now() + timedelta(hours=1),
            registration_deadline=timezone.now() + timedelta(minutes=30),
            location="Esch",
            is_published=True,
        )
        other_hunt = CacheHunt.objects.create(
            event=other_event, title="Other", created_by=hunt.created_by
        )
        other_station = CacheStation.objects.create(
            hunt=other_hunt, order=1, name="Elsewhere", unlock_mode="qr"
        )
        _start_hunt(hunt)
        client.force_login(player)
        self._post_code(client, hunt, other_station.manual_code)
        assert not CacheStationAttempt.objects.filter(
            team=team, station=other_station
        ).exists()

    def test_pasted_uuid_and_url_still_work(self, client, hunt, stations, team, player):
        stations[0].unlock_mode = "qr"
        stations[0].save()
        _start_hunt(hunt)
        client.force_login(player)
        response = self._post_code(
            client, hunt, f"https://crush.lu/cache/qr/{stations[0].qr_token}/"
        )
        assert response.status_code == 302
        attempt = CacheStationAttempt.objects.get(team=team, station=stations[0])
        assert attempt.scanned_at is not None

    def test_wrong_station_code_redirects_to_play(
        self, client, hunt, stations, team, player
    ):
        _start_hunt(hunt)  # current station is stations[0]
        client.force_login(player)
        response = self._post_code(client, hunt, stations[1].manual_code)
        assert reverse("crush_lu:cache_play", args=[hunt.event_id]) in response.url
        assert not CacheStationAttempt.objects.filter(
            team=team, station=stations[1], scanned_at__isnull=False
        ).exists()

    def test_requires_membership(self, client, hunt, stations, registration, player):
        # Registered but not in a team
        _start_hunt(hunt)
        client.force_login(player)
        response = self._post_code(client, hunt, stations[0].manual_code)
        assert reverse("crush_lu:cache_lobby", args=[hunt.event_id]) in response.url

    def test_qr_sheet_generates_with_codes(self, hunt, stations):
        pytest.importorskip("reportlab")
        pytest.importorskip("qrcode")
        from crush_lu.qr_utils import generate_cache_station_sheet

        stations[0].unlock_mode = "qr"
        stations[0].save()
        pdf = generate_cache_station_sheet([stations[0]])
        assert pdf[:4] == b"%PDF"

    def test_regenerate_tokens_also_rotates_manual_codes(self, hunt, stations):
        # The admin action's promise is "printed sheets are now invalid" —
        # sheets carry the typeable fallback too, so it must rotate as well.
        from crush_lu.admin.crush_cache import regenerate_cache_qr_tokens

        old = {s.pk: (s.qr_token, s.manual_code) for s in stations}
        request = RequestFactory().post("/")
        request.session = {}
        request._messages = FallbackStorage(request)
        regenerate_cache_qr_tokens(None, request, CacheHunt.objects.filter(pk=hunt.pk))
        for station in CacheStation.objects.filter(hunt=hunt):
            old_token, old_code = old[station.pk]
            assert station.qr_token != old_token
            assert station.manual_code != old_code
            assert station.manual_code  # regenerated, not left blank


# ============================================================================
# ANSWERING & PROGRESSION
# ============================================================================


@pytest.mark.django_db
class TestAnswering:
    def _unlock_station_one(self, hunt, stations, team):
        _start_hunt(hunt)
        attempt, _ = CacheStationAttempt.objects.get_or_create(
            team=team, station=stations[0]
        )
        attempt.arrived_at = timezone.now()
        attempt.save()
        return attempt

    def _answer(self, client, hunt, challenge, answer):
        url = reverse("crush_lu:cache_answer_api", args=[hunt.event_id, challenge.id])
        return client.post(url, {"answer": answer})

    def test_wrong_answer_no_points(self, client, hunt, stations, team, player):
        attempt = self._unlock_station_one(hunt, stations, team)
        challenge = stations[0].challenges.first()
        client.force_login(player)
        self._answer(client, hunt, challenge, "wrong guess")

        ca = CacheChallengeAttempt.objects.get(
            station_attempt=attempt, challenge=challenge
        )
        assert not ca.is_correct
        assert ca.attempts_count == 1
        assert CacheTeamProgress.objects.get(team=team).total_points == 0

    def test_correct_answer_awards_and_advances(
        self, client, hunt, stations, team, player
    ):
        attempt = self._unlock_station_one(hunt, stations, team)
        challenge = stations[0].challenges.first()
        client.force_login(player)
        response = self._answer(client, hunt, challenge, "golden lady")
        assert response.status_code == 200

        ca = CacheChallengeAttempt.objects.get(
            station_attempt=attempt, challenge=challenge
        )
        assert ca.is_correct and ca.points_earned == 100
        attempt.refresh_from_db()
        assert attempt.completed_at is not None
        progress = CacheTeamProgress.objects.get(team=team)
        assert progress.total_points == 100
        assert progress.current_station_id == stations[1].id

    def test_double_submit_scores_once(self, client, hunt, stations, team, player):
        self._unlock_station_one(hunt, stations, team)
        challenge = stations[0].challenges.first()
        client.force_login(player)
        self._answer(client, hunt, challenge, "Gelle Fra")
        self._answer(client, hunt, challenge, "Gelle Fra")

        assert (
            CacheChallengeAttempt.objects.filter(
                station_attempt__team=team, challenge=challenge
            ).count()
            == 1
        )
        assert CacheTeamProgress.objects.get(team=team).total_points == 100

    def test_hint_costs_deducted(self, client, hunt, stations, team, player):
        self._unlock_station_one(hunt, stations, team)
        challenge = stations[0].challenges.first()
        client.force_login(player)

        hint_url = reverse(
            "crush_lu:cache_hint_api", args=[hunt.event_id, challenge.id, 1]
        )
        client.post(hint_url)
        self._answer(client, hunt, challenge, "Gelle Fra")

        ca = CacheChallengeAttempt.objects.get(challenge=challenge)
        assert ca.hints_used == [1]
        assert ca.points_earned == 80  # 100 - 20 hint cost
        assert CacheTeamProgress.objects.get(team=team).total_points == 80

    def test_locked_station_rejects_answers(self, client, hunt, stations, team, player):
        _start_hunt(hunt)  # station 1 is GPS-locked, no arrival yet
        challenge = stations[0].challenges.first()
        client.force_login(player)
        self._answer(client, hunt, challenge, "Gelle Fra")
        assert not CacheChallengeAttempt.objects.filter(
            challenge=challenge, is_correct=True
        ).exists()

    def test_finishing_last_station(self, client, hunt, stations, team, player):
        self._unlock_station_one(hunt, stations, team)
        client.force_login(player)
        self._answer(client, hunt, stations[0].challenges.first(), "Gelle Fra")
        # Station 2 has unlock_mode="none" — answer directly
        self._answer(client, hunt, stations[1].challenges.first(), "petrusse")

        progress = CacheTeamProgress.objects.get(team=team)
        assert progress.is_finished
        assert progress.finished_at is not None
        assert progress.current_station is None
        assert progress.total_points == 150

        board = hunt.get_leaderboard()
        assert board[0]["team_id"] == team.id and board[0]["is_finished"]

    def test_answer_api_rate_limited(self, client, hunt, stations, team, player):
        """Multiple-choice must not be brute-forceable: the 21st POST within a
        minute is refused before scoring. The form swaps #play-content and HTMX
        ignores a bare 429, so the refusal re-renders the panel with a visible
        notice instead of the button silently going dead."""
        attempt = self._unlock_station_one(hunt, stations, team)
        challenge = stations[0].challenges.first()
        client.force_login(player)

        for _ in range(20):
            response = self._answer(client, hunt, challenge, "wrong guess")
            assert response.status_code == 200
        response = self._answer(client, hunt, challenge, "wrong guess")
        assert response.status_code == 200
        # The swappable panel, carrying the throttle notice — not a bare 429.
        assert b'id="play-content"' in response.content
        assert b"Too many attempts" in response.content

        ca = CacheChallengeAttempt.objects.get(
            station_attempt=attempt, challenge=challenge
        )
        assert ca.attempts_count == 20  # the throttled POST didn't count

    def test_stale_form_after_advance(self, client, hunt, stations, team, player):
        """Answering a challenge from a station the team already left is a no-op."""
        self._unlock_station_one(hunt, stations, team)
        challenge = stations[0].challenges.first()
        client.force_login(player)
        self._answer(client, hunt, challenge, "Gelle Fra")  # advances to station 2

        # Re-post the same (now stale) challenge with a wrong answer
        self._answer(client, hunt, challenge, "whatever")
        ca = CacheChallengeAttempt.objects.get(challenge=challenge)
        assert ca.is_correct and ca.attempts_count == 1  # untouched


# ============================================================================
# PLAY VIEW STATES
# ============================================================================


@pytest.mark.django_db
class TestPlayView:
    def test_redirects_to_lobby_without_team(self, client, hunt, registration, player):
        client.force_login(player)
        url = reverse("crush_lu:cache_play", args=[hunt.event_id])
        response = client.get(url)
        assert response.status_code == 302
        assert reverse("crush_lu:cache_lobby", args=[hunt.event_id]) in response.url

    def test_play_renders_navigate_state(self, client, hunt, stations, team, player):
        _start_hunt(hunt)
        client.force_login(player)
        response = client.get(reverse("crush_lu:cache_play", args=[hunt.event_id]))
        assert response.status_code == 200
        assert "Gëlle Fra".encode() in response.content

    def test_map_mode_exposes_coords_compass_does_not(
        self, client, hunt, stations, team, player
    ):
        _start_hunt(hunt)
        client.force_login(player)
        url = reverse("crush_lu:cache_play", args=[hunt.event_id])

        response = client.get(url)  # navigation_mode defaults to "map"
        assert b"data-target-lat" in response.content

        hunt.navigation_mode = "compass"
        hunt.save()
        response = client.get(url)
        assert b"data-target-lat" not in response.content

    def test_finish_screen_when_done(self, client, hunt, stations, team, player):
        _start_hunt(hunt)
        progress = CacheTeamProgress.objects.get(team=team)
        progress.is_finished = True
        progress.finished_at = timezone.now()
        progress.current_station = None
        progress.total_points = 150
        progress.save()
        client.force_login(player)
        response = client.get(reverse("crush_lu:cache_play", args=[hunt.event_id]))
        assert response.status_code == 200
        assert b"150" in response.content
        # The leaderboard only feeds the finish screen (dropped from the
        # play/HTMX context) — make sure it still arrives here.
        assert b"Rank #" in response.content


# ============================================================================
# COACH FLOWS
# ============================================================================


@pytest.mark.django_db
class TestCoach:
    def test_dashboard_renders(self, client, hunt, stations, team, coach_user):
        client.force_login(coach_user)
        url = reverse("crush_lu:cache_coach_dashboard", args=[hunt.event_id])
        response = client.get(url)
        assert response.status_code == 200
        assert b"Old Town Hunt" in response.content

    def test_start_initializes_progress(self, client, hunt, stations, team, coach_user):
        client.force_login(coach_user)
        url = reverse("crush_lu:cache_coach_start", args=[hunt.event_id])
        client.post(url)
        hunt.refresh_from_db()
        assert hunt.status == "live" and hunt.started_at is not None
        progress = CacheTeamProgress.objects.get(team=team)
        assert progress.current_station_id == stations[0].id

    def test_cannot_start_twice(self, client, hunt, stations, team, coach_user):
        _start_hunt(hunt)
        started_at = hunt.started_at
        client.force_login(coach_user)
        client.post(reverse("crush_lu:cache_coach_start", args=[hunt.event_id]))
        hunt.refresh_from_db()
        assert hunt.status == "live" and hunt.started_at == started_at

    def test_finish(self, client, hunt, stations, team, coach_user):
        _start_hunt(hunt)
        client.force_login(coach_user)
        client.post(reverse("crush_lu:cache_coach_finish", args=[hunt.event_id]))
        hunt.refresh_from_db()
        assert hunt.status == "finished" and hunt.finished_at is not None

    def test_auto_teams(self, client, hunt, stations, coach_user):
        for i in range(5):
            user = User.objects.create_user(
                username=f"attendee{i}@test.com",
                email=f"attendee{i}@test.com",
                password="x",
            )
            _grant_consent(user)
            EventRegistration.objects.create(
                event=hunt.event, user=user, status="attended"
            )
        client.force_login(coach_user)
        client.post(reverse("crush_lu:cache_coach_auto_teams", args=[hunt.event_id]))

        # 5 attendees, team_size_max=2 → 3 teams
        assert hunt.teams.count() == 3
        assert CacheTeamMember.objects.filter(hunt=hunt).count() == 5
        for t in hunt.teams.all():
            assert t.members.count() <= hunt.team_size_max

    def test_coach_state_api(self, client, hunt, stations, team, coach_user):
        _start_hunt(hunt)
        client.force_login(coach_user)
        response = client.get(
            reverse("crush_lu:cache_coach_state_api", args=[hunt.event_id])
        )
        data = response.json()
        assert data["ok"] and data["status"] == "live"
        assert len(data["leaderboard"]) == 1
        assert len(data["positions"]) == 1


# ============================================================================
# CODEX REVIEW REGRESSIONS (PR #610)
# ============================================================================


@pytest.mark.django_db
class TestReviewFixes:
    def test_self_join_disabled_blocks_code_join(self, client, hunt, team, teammate):
        """allow_self_join=False must gate join-by-code, not just creation."""
        hunt.allow_self_join = False
        hunt.save()
        EventRegistration.objects.create(
            event=hunt.event, user=teammate, status="confirmed"
        )
        client.force_login(teammate)
        url = reverse("crush_lu:cache_join_team", args=[hunt.event_id])
        client.post(url, {"join_code": "ABC234"})
        assert team.members.count() == 1  # roster unchanged
        client.post(url, {"team_name": "Rogues"})
        assert not hunt.teams.filter(name="Rogues").exists()

    def test_cancelled_registration_loses_access(
        self, client, hunt, stations, team, registration, player
    ):
        """Cancelling the event registration revokes gameplay access."""
        _start_hunt(hunt)
        registration.status = "cancelled"
        registration.save()
        client.force_login(player)

        response = client.get(reverse("crush_lu:cache_play", args=[hunt.event_id]))
        assert response.status_code == 302
        assert reverse("crush_lu:cache_lobby", args=[hunt.event_id]) in response.url

        response = client.post(
            reverse("crush_lu:cache_position_api", args=[hunt.event_id]),
            json.dumps({"lat": GELLE_FRA[0], "lng": GELLE_FRA[1], "accuracy": 10}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_unrelated_coach_denied(self, client, hunt, stations, team):
        """An active coach who neither created the hunt nor is assigned to
        the event cannot view the dashboard or start the hunt."""
        other = User.objects.create_user(
            username="othercoach@test.com",
            email="othercoach@test.com",
            password="x",
            is_staff=True,
        )
        _grant_consent(other)
        CrushCoach.objects.create(
            user=other, bio="b", specializations="s", is_active=True
        )
        client.force_login(other)

        response = client.get(
            reverse("crush_lu:cache_coach_dashboard", args=[hunt.event_id])
        )
        assert response.status_code == 302

        client.post(reverse("crush_lu:cache_coach_start", args=[hunt.event_id]))
        hunt.refresh_from_db()
        assert hunt.status == "draft"

        response = client.get(
            reverse("crush_lu:cache_coach_state_api", args=[hunt.event_id])
        )
        assert response.status_code == 403

    def test_start_blocked_by_blocking_readiness(
        self, client, hunt, stations, team, coach_user
    ):
        """Starting is refused while a GPS station has no coordinates."""
        stations[0].latitude = None
        stations[0].longitude = None
        stations[0].save()
        client.force_login(coach_user)
        client.post(reverse("crush_lu:cache_coach_start", args=[hunt.event_id]))
        hunt.refresh_from_db()
        assert hunt.status == "draft"

        stations[0].latitude = GELLE_FRA[0]
        stations[0].longitude = GELLE_FRA[1]
        stations[0].save()
        client.post(reverse("crush_lu:cache_coach_start", args=[hunt.event_id]))
        hunt.refresh_from_db()
        assert hunt.status == "live"

    def test_start_allowed_with_zero_teams(self, client, hunt, stations, coach_user):
        """Teams/registrations checks are non-blocking — they may form after
        start (progress rows are created lazily)."""
        client.force_login(coach_user)
        client.post(reverse("crush_lu:cache_coach_start", args=[hunt.event_id]))
        hunt.refresh_from_db()
        assert hunt.status == "live"

    def test_serialized_leaderboard_is_json_safe(self, hunt, stations, team):
        """Regression: raw datetimes crashed the channel-layer broadcast the
        moment the first team finished."""
        CacheTeamProgress.objects.create(
            team=team,
            total_points=150,
            is_finished=True,
            finished_at=timezone.now(),
        )
        payload = hunt.get_serialized_leaderboard()
        json.dumps(payload)  # must not raise
        assert payload[0]["finished_at"] is not None


@pytest.mark.django_db
class TestGeminiReviewFixes:
    def test_admin_readiness_display_on_add(self):
        from crush_lu.admin.crush_cache import CacheHuntAdmin
        from django.contrib.admin.sites import AdminSite

        admin_site = AdminSite()
        model_admin = CacheHuntAdmin(CacheHunt, admin_site)
        res = model_admin.readiness_check_display(None)
        assert "saving" in str(res)

    def test_capacity_check_ignores_cancelled(self, client, hunt, team, teammate):
        # Hunt max size is 2. Currently, team has 1 member (player).
        # Add teammate as a second member:
        EventRegistration.objects.create(
            event=hunt.event, user=teammate, status="confirmed"
        )
        client.force_login(teammate)
        url = reverse("crush_lu:cache_join_team", args=[hunt.event_id])
        client.post(url, {"join_code": team.join_code})
        assert team.member_count() == 2

        # A third user tries to join, should fail because team is full:
        third_user = User.objects.create_user(
            username="third@test.com", email="third@test.com", password="x"
        )
        _grant_consent(third_user)
        EventRegistration.objects.create(
            event=hunt.event, user=third_user, status="confirmed"
        )
        client.force_login(third_user)
        response = client.post(url, {"join_code": team.join_code})
        assert team.member_count() == 2

        # Cancel the second member's registration:
        reg_teammate = EventRegistration.objects.get(event=hunt.event, user=teammate)
        reg_teammate.status = "cancelled"
        reg_teammate.save()
        assert team.member_count() == 1

        # Now the third user tries to join again, should succeed!
        client.force_login(third_user)
        response = client.post(url, {"join_code": team.join_code})
        assert response.status_code == 302
        assert team.member_count() == 2

    def test_leave_team_blocked_on_allow_self_join_false(
        self, client, hunt, team, player
    ):
        hunt.allow_self_join = False
        hunt.save()
        client.force_login(player)
        url = reverse("crush_lu:cache_leave_team", args=[hunt.event_id])
        response = client.post(url)
        assert response.status_code == 302
        assert team.member_count() == 1  # Still in the team

    def test_position_api_not_live_or_finished(self, client, hunt, team, player):
        client.force_login(player)
        url = reverse("crush_lu:cache_position_api", args=[hunt.event_id])

        # 1. Hunt is draft (not live) -> should fail
        response = client.post(
            url,
            json.dumps({"lat": GELLE_FRA[0], "lng": GELLE_FRA[1], "accuracy": 10}),
            content_type="application/json",
        )
        assert response.status_code == 403

        # Start hunt, make team finished
        _start_hunt(hunt)
        progress = CacheTeamProgress.objects.get(team=team)
        progress.is_finished = True
        progress.save()

        # 2. Team is finished -> should fail
        response = client.post(
            url,
            json.dumps({"lat": GELLE_FRA[0], "lng": GELLE_FRA[1], "accuracy": 10}),
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_lobby_redirects_finished(self, client, hunt, team, player):
        hunt.status = "finished"
        hunt.save()
        client.force_login(player)
        url = reverse("crush_lu:cache_lobby", args=[hunt.event_id])
        response = client.get(url)
        assert response.status_code == 302
        assert reverse("crush_lu:cache_play", args=[hunt.event_id]) in response.url

    def test_non_coach_creator_can_manage(self, client, hunt, stations, team):
        # Creator is coach_user by default. Let's create a staff user who is NOT a coach.
        creator = User.objects.create_user(
            username="creator@test.com",
            email="creator@test.com",
            password="x",
            is_staff=True,
        )
        _grant_consent(creator)
        hunt.created_by = creator
        hunt.save()

        client.force_login(creator)
        response = client.get(
            reverse("crush_lu:cache_coach_dashboard", args=[hunt.event_id])
        )
        assert response.status_code == 200

        response = client.post(
            reverse("crush_lu:cache_coach_start", args=[hunt.event_id])
        )
        assert response.status_code == 302
        hunt.refresh_from_db()
        assert hunt.status == "live"

    def test_cache_join_available_requires_hunt(self, db):
        event = MeetupEvent.objects.create(
            title="Temp Hunt",
            event_type="crush_cache",
            date_time=timezone.now() + timedelta(hours=1),
            registration_deadline=timezone.now() + timedelta(minutes=30),
            location="Luxembourg City",
            is_published=True,
        )
        assert event.cache_join_available is False

        # Create hunt
        creator = User.objects.create_user(
            username="temp_creator@test.com",
            email="temp_creator@test.com",
            password="x",
            is_staff=True,
        )
        CacheHunt.objects.create(event=event, title="Temp Hunt", created_by=creator)
        assert event.cache_join_available is True


@pytest.mark.django_db
class TestCodexReviewFixes:
    """Regression coverage for Codex review findings on PR #616."""

    def test_reregistration_after_cancel_does_not_exceed_capacity(
        self, client, hunt, team, teammate
    ):
        """A cancelled member who re-registers must not silently reactivate
        their old team membership and push the team over ``team_size_max``.

        Repro: full team (cap 2) -> a member cancels, so the active-only
        ``member_count()`` frees the slot -> a replacement takes it -> the
        original re-registers, reusing their cancelled EventRegistration row.
        Their stale CacheTeamMember must be cleared so the team stays at
        capacity instead of ballooning to 3.
        """
        from datetime import date

        from crush_lu.models import CrushProfile

        # The `team` fixture already holds `player` (attended). Cap is 2.
        # event.profile_requirement defaults to "completed", so give the
        # teammate a verified profile so event_register accepts the re-signup.
        CrushProfile.objects.create(
            user=teammate,
            date_of_birth=date(1995, 5, 15),
            gender="M",
            location="Luxembourg",
            verification_status="verified",
            is_approved=True,
            is_active=True,
        )

        # Teammate had joined, then cancelled — the membership row lingers.
        cancelled = EventRegistration.objects.create(
            event=hunt.event, user=teammate, status="cancelled"
        )
        CacheTeamMember.objects.create(hunt=hunt, team=team, registration=cancelled)

        # A replacement claimed the freed slot; the team is full again.
        replacement = User.objects.create_user(
            username="replace@test.com", email="replace@test.com", password="x"
        )
        _grant_consent(replacement)
        reg_replacement = EventRegistration.objects.create(
            event=hunt.event, user=replacement, status="confirmed"
        )
        CacheTeamMember.objects.create(
            hunt=hunt, team=team, registration=reg_replacement
        )
        assert team.member_count() == 2  # player + replacement

        # The original teammate re-registers: reuses the cancelled row.
        client.force_login(teammate)
        response = client.post(
            reverse("crush_lu:event_register", args=[hunt.event_id]), {}
        )
        assert response.status_code == 302
        cancelled.refresh_from_db()
        assert cancelled.status == "confirmed"  # re-registration itself succeeded

        # ...but the stale hunt membership was cleared: the team holds at
        # capacity and the re-registered user is no longer on it (they re-join
        # afresh, subject to the capacity check).
        assert team.member_count() == 2
        assert not CacheTeamMember.objects.filter(
            hunt=hunt, registration__user=teammate
        ).exists()


@pytest.mark.django_db
class TestQrUnlockMessaging:
    def test_scan_before_arrival_does_not_claim_unlocked(
        self, client, hunt, team, player
    ):
        """Scanning a gps_qr station before arriving records the scan but must
        NOT claim the station is unlocked — GPS arrival is still required."""
        station = CacheStation.objects.create(
            hunt=hunt,
            order=1,
            name="Grand Ducal Palace",
            unlock_mode="gps_qr",
            latitude="49.610600",
            longitude="6.131900",
            radius_meters=40,
        )
        _start_hunt(hunt)  # hunt live + progress parked at this first station
        client.force_login(player)

        url = reverse("crush_lu:cache_qr_scan", args=[station.qr_token])
        response = client.get(url, follow=True)

        attempt = CacheStationAttempt.objects.get(team=team, station=station)
        assert attempt.scanned_at is not None  # the scan is recorded
        assert attempt.is_unlocked is False  # ...but still locked (not arrived)

        from django.contrib.messages import constants as msg_constants

        levels = [m.level for m in response.context["messages"]]
        assert msg_constants.SUCCESS not in levels  # must not say "unlocked!"
        assert msg_constants.INFO in levels  # nudge to reach the location
