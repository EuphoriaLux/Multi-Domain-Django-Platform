"""Staging-only Crush Cache field-test & daily rehearsal suite."""

import time
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from crush_lu.management.commands.seed_crush_cache import PRESETS
from crush_lu.models import (
    CrushCoach,
    EventRegistration,
    MeetupEvent,
    PlaygroundAccount,
)
from crush_lu.models.crush_cache import (
    CacheHunt,
    CacheStation,
    CacheStationAttempt,
    CacheTeam,
    CacheTeamMember,
    CacheTeamProgress,
)
from crush_lu.services.playground_practice import SWITCH_SESSION_KEY
from crush_lu.tests import test_crush_cache as _cache_tests

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("azureproject.urls_crush"),
]


@pytest.fixture
def staging(settings, monkeypatch):
    monkeypatch.setenv("DJANGO_ENV", "staging")
    settings.CRUSH_CACHE_ENABLED = True
    settings.ROOT_URLCONF = "azureproject.urls_crush"


def _make_coach(username, is_staff=True):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
        is_staff=is_staff,
    )
    _cache_tests._grant_consent(user)
    CrushCoach.objects.create(
        user=user,
        bio="Field-test coach",
        specializations="Crush Cache",
        is_active=True,
    )
    return user


def _make_user(username):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@test.com",
        password="testpass123",
    )
    _cache_tests._grant_consent(user)
    return user


def _make_custom_hunt(creator):
    now = timezone.now()
    event = MeetupEvent.objects.create(
        title="Custom Staging Hunt",
        description="Already sitting in staging admin",
        event_type="crush_cache",
        date_time=now - timedelta(days=5),
        duration_minutes=120,
        location="Luxembourg",
        address="Place d'Armes",
        max_participants=20,
        registration_deadline=now - timedelta(days=5),
        is_published=False,
    )
    hunt = CacheHunt.objects.create(
        event=event,
        title="Custom Course",
        description="Handcrafted hunt in staging DB",
        status="finished",
        navigation_mode="map",
        created_by=creator,
    )
    CacheStation.objects.create(
        hunt=hunt,
        order=1,
        name="Station Alpha",
        latitude="49.610000",
        longitude="6.130000",
        radius_meters=30,
        unlock_mode="gps",
    )
    CacheStation.objects.create(
        hunt=hunt,
        order=2,
        name="Station Beta",
        latitude="49.611000",
        longitude="6.131000",
        radius_meters=30,
        unlock_mode="gps_qr",
    )
    return hunt


def test_rehearsal_routes_and_command_404_off_staging(monkeypatch, settings):
    from crush_lu.services.cache_rehearsal import (
        ensure_all_preset_rehearsal_hunts,
        is_rehearsal_cache_host,
    )

    monkeypatch.setenv("DJANGO_ENV", "production")
    settings.DEBUG = False
    settings.CRUSH_CACHE_ENABLED = True

    coach = _make_coach("prodcoach")
    hunt = _make_custom_hunt(coach)
    assert is_rehearsal_cache_host(hunt, coach) is False

    anon = Client()
    authed = Client()
    authed.force_login(coach)

    urls = [
        ("get", reverse("crush_lu:cache_rehearsal")),
        ("post", reverse("crush_lu:cache_rehearsal_play", args=[hunt.event_id])),
        ("post", reverse("crush_lu:cache_rehearsal_lobby", args=[hunt.event_id])),
        ("post", reverse("crush_lu:cache_rehearsal_reset", args=[hunt.event_id])),
    ]
    for method, url in urls:
        assert getattr(anon, method)(url).status_code == 404
        assert getattr(authed, method)(url).status_code == 404

    with pytest.raises(CommandError, match="only available on staging"):
        call_command("ensure_cache_rehearsals")

    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        ensure_all_preset_rehearsal_hunts(coach)


def test_hub_creates_all_four_presets_idempotently_across_languages(staging):
    coach = _make_coach("fieldcoach")
    client = Client()
    client.force_login(coach)

    for prefix in ("/en", "/fr", "/de", "/fr"):
        resp = client.get(f"{prefix}/coach/cache-rehearsal/")
        assert resp.status_code == 200
        assert len(resp.context["preset_cards"]) == len(PRESETS)

    assert MeetupEvent.objects.filter(event_type="crush_cache").count() == len(PRESETS)
    assert CacheHunt.objects.count() == len(PRESETS)
    for card in resp.context["preset_cards"]:
        assert card["hunt"].status == "live"
        assert card["event"].is_published is False
        assert card["event"].cache_join_available is True
        station = card["hunt"].ordered_stations().first()
        assert station.name_fr
        assert station.name_de


def test_hub_lists_existing_staging_hunt_without_overwriting(staging):
    coach = _make_coach("existingcoach")
    custom_hunt = _make_custom_hunt(coach)

    client = Client()
    client.force_login(coach)
    resp = client.get(reverse("crush_lu:cache_rehearsal"))
    assert resp.status_code == 200

    existing_ids = {card["hunt"].id for card in resp.context["existing_cards"]}
    assert custom_hunt.id in existing_ids
    custom_hunt.refresh_from_db()
    assert custom_hunt.title == "Custom Course"
    assert custom_hunt.stations.count() == 2


def test_rehearsal_events_excluded_from_echo_google_and_public_catalogue(staging):
    from crush_lu.services.cache_rehearsal import (
        ensure_all_preset_rehearsal_hunts,
        exclude_rehearsal_cache_events,
    )
    from crush_lu.services.echo_lu import should_publish
    from crush_lu.services.google_indexing import should_index_event

    coach = _make_coach("echocoach")
    ensured = ensure_all_preset_rehearsal_hunts(coach)
    rehearsal_events = [hunt.event for _key, _preset, hunt in ensured]

    assert exclude_rehearsal_cache_events(MeetupEvent.objects.all()).count() == 0

    for event in rehearsal_events:
        assert event.is_published is False
        assert should_publish(event) is False
        assert should_index_event(event) is False

        # Defense-in-depth: even if toggled published in admin, echo & Google skip it.
        event.is_published = True
        assert should_publish(event) is False
        assert should_index_event(event) is False

    events_resp = Client().get(reverse("crush_lu:event_list"))
    assert events_resp.status_code == 200
    assert "Old Town GPS Hunt".encode() not in events_resp.content


def test_solo_play_daily_auto_reset_and_isolated_manual_reset(staging):
    from crush_lu.services.cache_rehearsal import ensure_preset_rehearsal_hunt

    coach1 = _make_coach("solocoach1")
    coach2 = _make_coach("solocoach2")
    hunt = ensure_preset_rehearsal_hunt("lux_city", coach1)
    stations = list(hunt.ordered_stations())

    c1 = Client()
    c1.force_login(coach1)
    resp = c1.post(reverse("crush_lu:cache_rehearsal_play", args=[hunt.event_id]))
    assert resp.status_code == 302
    assert resp["Location"].endswith(
        reverse("crush_lu:cache_play", args=[hunt.event_id])
    )

    play_resp = c1.get(resp["Location"])
    assert play_resp.status_code == 200
    assert play_resp.context["cache_rehearsal_toolbar"] is True

    reg = EventRegistration.objects.get(event=hunt.event, user=coach1)
    assert reg.status == "attended"

    team1 = CacheTeamMember.objects.get(hunt=hunt, registration__user=coach1).team
    progress1 = CacheTeamProgress.objects.get(team=team1)
    progress1.current_station = stations[1]
    progress1.total_points = 100
    progress1.started_at = timezone.now()
    progress1.save()
    CacheStationAttempt.objects.update_or_create(
        team=team1,
        station=stations[0],
        defaults={"arrived_at": timezone.now(), "points_earned": 100},
    )

    # Same-day re-entry preserves progress.
    c1.post(reverse("crush_lu:cache_rehearsal_play", args=[hunt.event_id]))
    progress1.refresh_from_db()
    assert progress1.current_station_id == stations[1].id
    assert progress1.total_points == 100

    # Yesterday's run triggers auto-reset on GET cache_play (even via bookmark).
    CacheTeamProgress.objects.filter(pk=progress1.pk).update(
        started_at=timezone.now() - timedelta(days=1)
    )
    c1.get(reverse("crush_lu:cache_play", args=[hunt.event_id]))
    progress1.refresh_from_db()
    assert progress1.current_station_id == stations[0].id
    assert progress1.total_points == 0
    assert progress1.is_finished is False
    reset_s1 = CacheStationAttempt.objects.get(team=team1, station=stations[0])
    assert reset_s1.arrived_at is None
    assert reset_s1.points_earned == 0

    # Manual reset isolates coach1's team and leaves coach2 untouched.
    c2 = Client()
    c2.force_login(coach2)
    c2.post(reverse("crush_lu:cache_rehearsal_play", args=[hunt.event_id]))
    team2 = CacheTeamMember.objects.get(hunt=hunt, registration__user=coach2).team
    progress2 = CacheTeamProgress.objects.get(team=team2)
    progress2.current_station = stations[1]
    progress2.total_points = 80
    progress2.save()

    progress1.current_station = stations[1]
    progress1.total_points = 100
    progress1.save()

    reset_resp = c1.post(
        reverse("crush_lu:cache_rehearsal_reset", args=[hunt.event_id]),
        data={"next": "play"},
    )
    assert reset_resp.status_code == 302
    assert reset_resp["Location"] == reverse(
        "crush_lu:cache_play", args=[hunt.event_id]
    )

    progress1.refresh_from_db()
    progress2.refresh_from_db()
    assert progress1.current_station_id == stations[0].id
    assert progress1.total_points == 0
    assert progress2.current_station_id == stations[1].id
    assert progress2.total_points == 80


def test_team_lobby_join_code_and_live_leave_team(staging):
    from crush_lu.services.cache_rehearsal import ensure_preset_rehearsal_hunt

    coach1 = _make_coach("lobbycoach1")
    coach2 = _make_coach("lobbycoach2")
    hunt = ensure_preset_rehearsal_hunt("minette", coach1)

    c1 = Client()
    c1.force_login(coach1)
    c1.post(reverse("crush_lu:cache_rehearsal_play", args=[hunt.event_id]))
    assert CacheTeam.objects.filter(hunt=hunt).count() == 1

    # Going to Lobby leaves the 1-coach solo team cleanly so the coach sees Join/Create.
    lobby_redirect = c1.post(
        reverse("crush_lu:cache_rehearsal_lobby", args=[hunt.event_id])
    )
    assert lobby_redirect.status_code == 302
    assert "?view=lobby" in lobby_redirect["Location"]
    assert CacheTeam.objects.filter(hunt=hunt).count() == 0

    # Create a shared team while the hunt is already live, then join coach2 by code.
    c1.post(
        reverse("crush_lu:cache_join_team", args=[hunt.event_id]),
        data={"team_name": "Route Duo"},
    )
    shared_team = CacheTeam.objects.get(hunt=hunt, name="Route Duo")

    c2 = Client()
    c2.force_login(coach2)
    c2.post(reverse("crush_lu:cache_rehearsal_lobby", args=[hunt.event_id]))
    c2.post(
        reverse("crush_lu:cache_join_team", args=[hunt.event_id]),
        data={"join_code": shared_team.join_code},
    )
    assert shared_team.members.count() == 2

    # Coach2 can leave the live team on staging to test solo or join another team.
    lobby_get = c2.get(
        f"{reverse('crush_lu:cache_lobby', args=[hunt.event_id])}?view=lobby"
    )
    assert lobby_get.status_code == 200
    assert b"Leave team" in lobby_get.content

    c2.post(reverse("crush_lu:cache_leave_team", args=[hunt.event_id]))
    assert shared_team.members.count() == 1


def test_practice_member_can_play_without_attended_status(staging):
    from crush_lu.services.cache_rehearsal import ensure_preset_rehearsal_hunt

    coach = _make_coach("practiceowner")
    practice_user = _make_user("practicemember")
    PlaygroundAccount.objects.create(
        user=practice_user,
        kind=PlaygroundAccount.KIND_PRACTICE,
        owner=coach,
        preset="luxid_ready",
    )
    hunt = ensure_preset_rehearsal_hunt("echternach_lake", coach)

    client = Client()
    client.force_login(practice_user)
    session = client.session
    session[SWITCH_SESSION_KEY] = {
        "coach": coach.pk,
        "practice": practice_user.pk,
        "at": int(time.time()),
    }
    session.save()

    hub = client.get(reverse("crush_lu:cache_rehearsal"))
    assert hub.status_code == 200
    assert hub.context["rehearsal_is_practice"] is True
    assert hub.context["rehearsal_is_coach"] is False
    assert b"Coach Control" not in hub.content

    play = client.post(reverse("crush_lu:cache_rehearsal_play", args=[hunt.event_id]))
    assert play.status_code == 302

    reg = EventRegistration.objects.get(event=hunt.event, user=practice_user)
    assert reg.status == "confirmed"


def test_unassigned_coach_and_consumer_host_access_on_staging(
    staging, monkeypatch, settings
):
    from crush_lu.consumers import CacheHuntConsumer
    from crush_lu.services.cache_rehearsal import ensure_preset_rehearsal_hunt

    creator = _make_coach("creatorcoach")
    other_coach = _make_coach("otherfieldcoach")
    plain_user = _make_user("plainmember")
    hunt = ensure_preset_rehearsal_hunt("echternach_um_see", creator)

    client = Client()
    client.force_login(other_coach)

    detail_resp = client.get(
        reverse("crush_lu:coach_event_detail", args=[hunt.event_id])
    )
    assert detail_resp.status_code == 200
    assert detail_resp.context["can_manage_cache_hunt"] is True

    dash_resp = client.get(
        reverse("crush_lu:cache_coach_dashboard", args=[hunt.event_id])
    )
    assert dash_resp.status_code == 200

    consumer = CacheHuntConsumer()
    consumer.hunt_id = hunt.id
    is_host = CacheHuntConsumer.__dict__["_is_hunt_host"]
    assert is_host(consumer, other_coach) is True
    assert is_host(consumer, plain_user) is False

    # Production flag state immediately revokes unassigned host access.
    monkeypatch.setenv("DJANGO_ENV", "production")
    settings.DEBUG = False
    assert is_host(consumer, other_coach) is False


def test_non_coach_denied_and_ios_native_shell_suppression(staging, settings):
    from crush_lu.services.cache_rehearsal import ensure_preset_rehearsal_hunt

    coach = _make_coach("iosgatecoach")
    hunt = ensure_preset_rehearsal_hunt("lux_city", coach)
    plain = _make_user("ordinaryuser")

    plain_client = Client()
    plain_client.force_login(plain)
    denied = plain_client.get(reverse("crush_lu:cache_rehearsal"))
    assert denied.status_code == 302
    assert denied["Location"].endswith(reverse("crush_lu:dashboard"))

    settings.IOS_NATIVE_CACHE_ENABLED = False
    coach_client = Client()
    coach_client.force_login(coach)
    ios_headers = {"HTTP_USER_AGENT": "CrushLUApp/1.0 CFNetwork/1568"}

    ios_dash = coach_client.get(reverse("crush_lu:coach_dashboard"), **ios_headers)
    assert ios_dash.status_code == 200
    assert reverse("crush_lu:cache_rehearsal").encode() not in ios_dash.content

    ios_pg = coach_client.get(reverse("crush_lu:coach_playground"), **ios_headers)
    assert ios_pg.status_code == 200
    assert reverse("crush_lu:cache_rehearsal").encode() not in ios_pg.content

    ios_hub = coach_client.get(reverse("crush_lu:cache_rehearsal"), **ios_headers)
    assert ios_hub.status_code == 200
    assert ios_hub.context["ios_suppressed"] is True
    assert b"Open test.crush.lu in Safari" in ios_hub.content

    assert (
        coach_client.post(
            reverse("crush_lu:cache_rehearsal_play", args=[hunt.event_id]),
            **ios_headers,
        ).status_code
        == 404
    )

    safari_dash = coach_client.get(reverse("crush_lu:coach_dashboard"))
    assert safari_dash.status_code == 200
    assert reverse("crush_lu:cache_rehearsal").encode() in safari_dash.content


def test_ensure_cache_rehearsals_command_works_on_staging(staging):
    _make_coach("cmdcoach")
    call_command("ensure_cache_rehearsals")
    assert CacheHunt.objects.count() == len(PRESETS)

    # Second run is idempotent.
    call_command("ensure_cache_rehearsals")
    assert CacheHunt.objects.count() == len(PRESETS)
