"""Crush Cache staging field-test & daily rehearsal service (test.crush.lu / local DEBUG only).

Allows coaches (directly or switched to their Playground practice member) to walk any
of the Crush Cache courses outdoors on Safari without manual daily reseeding:

* Keeps one permanent rehearsal event per seed preset (unpublished, zero-cost,
  daily-rolled date window, status="live") plus any existing staging CacheHunt rows.
* Automatically resets stale previous-day team progress back to Station 1 on first
  entry of a new day, while supporting manual 1-tap reset and multi-coach team Lobby.
* Refuses execution unless :func:`cache_rehearsal_enabled` is True, so production is
  unaffected even when synced code lands there via release swaps.
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from crush_lu.services.playground_practice import (
    is_practice_user_id,
    switch_marker,
)
from crush_lu.services.quiz_rehearsal import rehearsal_enabled

REHEARSAL_TITLE_PREFIX = "🧭 [REHEARSAL] "


def cache_rehearsal_enabled():
    """True on the staging slot (``DJANGO_ENV=staging``) or in local DEBUG."""
    return rehearsal_enabled()


def require_cache_rehearsal():
    """Raise ``PermissionDenied`` off staging/DEBUG."""
    if not cache_rehearsal_enabled():
        raise PermissionDenied(
            "Crush Cache field-test rehearsal is only available on staging."
        )


def _languages():
    return tuple(getattr(settings, "MODELTRANSLATION_LANGUAGES", ("en",)))


def _in_every_language(field, value):
    """Return ``{"<field>_en": value, ...}`` for every translated language column."""
    return {f"{field}_{lang}": value for lang in _languages()}


def rehearsal_title_for_preset(preset_key):
    """Canonical event & hunt title prefix identifying a staging preset rehearsal."""
    from crush_lu.management.commands.seed_crush_cache import PRESETS

    preset = PRESETS[preset_key]
    return f"{REHEARSAL_TITLE_PREFIX}{preset['hunt_title']}"[:200]


def is_rehearsal_cache_event(event):
    """True if ``event`` is a staging Crush Cache rehearsal in any language column."""
    for lang in _languages():
        title = getattr(event, f"title_{lang}", None)
        if isinstance(title, str) and title.startswith(REHEARSAL_TITLE_PREFIX):
            return True
    return False


def exclude_rehearsal_cache_events(queryset):
    """Exclude Crush Cache rehearsal events across all modeltranslation columns."""
    for lang in _languages():
        queryset = queryset.exclude(
            **{f"title_{lang}__startswith": REHEARSAL_TITLE_PREFIX}
        )
    return queryset


def is_rehearsal_cache_host(hunt, user):
    """True on staging/DEBUG for any active CrushCoach (never True on production)."""
    if not cache_rehearsal_enabled() or not getattr(user, "is_authenticated", False):
        return False
    from crush_lu.models import CrushCoach

    return CrushCoach.objects.filter(user=user, is_active=True).exists()


def resolve_rehearsal_actor(request):
    """Return ``(acting_user, coach_user)`` for a direct coach or active practice member."""
    if not cache_rehearsal_enabled():
        return None
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None

    from crush_lu.models import CrushCoach, PlaygroundAccount

    if CrushCoach.objects.filter(user=user, is_active=True).exists():
        return (user, user)

    marker = switch_marker(request)
    if not marker:
        return None
    coach_id = marker.get("coach")
    if not isinstance(coach_id, int):
        return None
    has_active_link = PlaygroundAccount.objects.filter(
        user=user,
        kind=PlaygroundAccount.KIND_PRACTICE,
        owner_id=coach_id,
        retired_at__isnull=True,
    ).exists()
    if not has_active_link:
        return None
    coach = (
        CrushCoach.objects.select_related("user")
        .filter(user_id=coach_id, is_active=True, user__is_active=True)
        .first()
    )
    if coach is None:
        return None
    return (user, coach.user)


def keep_hunt_daily_active(hunt):
    """Ensure a rehearsal or staging hunt stays joinable and live today."""
    require_cache_rehearsal()
    now = timezone.now()
    event = hunt.event
    event_updates = []
    hunt_updates = []

    if is_rehearsal_cache_event(event):
        if event.is_published:
            event.is_published = False
            event_updates.append("is_published")
        if event.registration_fee != 0:
            event.registration_fee = 0
            event_updates.append("registration_fee")
        if event.duration_minutes != 1440:
            event.duration_minutes = 1440
            event_updates.append("duration_minutes")
        if (
            event.registration_deadline is None
            or event.registration_deadline < now + timedelta(days=30)
        ):
            event.registration_deadline = now + timedelta(days=365)
            event_updates.append("registration_deadline")
        if event.end_time < now + timedelta(hours=6) or event.date_time > now:
            event.date_time = now
            event_updates.append("date_time")

        if hunt.status != "live":
            hunt.status = "live"
            hunt_updates.append("status")
        if hunt.started_at is None:
            hunt.started_at = now
            hunt_updates.append("started_at")
        if hunt.finished_at is not None:
            hunt.finished_at = None
            hunt_updates.append("finished_at")
        if not hunt.allow_self_join:
            hunt.allow_self_join = True
            hunt_updates.append("allow_self_join")
        if hunt.team_size_max < 4:
            hunt.team_size_max = 4
            hunt_updates.append("team_size_max")
    else:
        if event.end_time < now + timedelta(hours=6):
            event.date_time = now
            event_updates.append("date_time")
        if event.registration_deadline is None or event.registration_deadline <= now:
            event.registration_deadline = now + timedelta(days=1)
            event_updates.append("registration_deadline")

        if hunt.status != "live":
            hunt.status = "live"
            hunt_updates.append("status")
        if hunt.started_at is None:
            hunt.started_at = now
            hunt_updates.append("started_at")
        if hunt.finished_at is not None:
            hunt.finished_at = None
            hunt_updates.append("finished_at")

    if event_updates:
        event.save(update_fields=event_updates)
    if hunt_updates:
        hunt.save(update_fields=hunt_updates)


def _fill_missing_translations(obj, wanted):
    updates = []
    for column, value in wanted.items():
        if getattr(obj, column, None) != value:
            setattr(obj, column, value)
            updates.append(column)
    if updates:
        obj.save(update_fields=updates)


def _create_stations_from_preset(hunt, preset):
    from crush_lu.models.crush_cache import CacheChallenge, CacheStation

    for s in preset["stations"]:
        station = CacheStation.objects.create(
            hunt=hunt,
            order=s["order"],
            **_in_every_language("name", s["name"]),
            **_in_every_language("intro_text", s["intro"]),
            **_in_every_language("completion_message", preset["completion_message"]),
            latitude=s["lat"],
            longitude=s["lng"],
            radius_meters=s.get("radius_meters", preset["radius_meters"]),
            unlock_mode=s["unlock_mode"],
        )
        CacheChallenge.objects.create(
            station=station,
            challenge_order=1,
            challenge_type=s["challenge_type"],
            **_in_every_language("question", s["question"]),
            **_in_every_language("options", s.get("options", {})),
            **_in_every_language("hint_1", s["hint_1"]),
            **_in_every_language("success_message", preset["success_message"]),
            correct_answer=s["answer"],
            alternative_answers=s["alternatives"],
            points_awarded=s["points"],
        )


def ensure_preset_rehearsal_hunt(preset_key, owner):
    """Create or reuse a daily-active staging rehearsal hunt for ``preset_key``."""
    require_cache_rehearsal()
    from crush_lu.management.commands.seed_crush_cache import PRESETS
    from crush_lu.models.crush_cache import CacheHunt
    from crush_lu.models.events import MeetupEvent

    preset = PRESETS[preset_key]
    title = rehearsal_title_for_preset(preset_key)
    hunt_description = preset["hunt_description"]
    event_description = preset["event_description"]

    matches_title = Q()
    for column in _in_every_language("title", title):
        matches_title |= Q(**{column: title})

    existing_events = list(
        MeetupEvent.objects.filter(matches_title, event_type="crush_cache")
        .select_related("cache_hunt")
        .order_by("-pk")
    )
    for event in existing_events:
        if hasattr(event, "cache_hunt"):
            hunt = event.cache_hunt
            _fill_missing_translations(
                event,
                {
                    **_in_every_language("title", title),
                    **_in_every_language("description", event_description),
                },
            )
            _fill_missing_translations(
                hunt,
                {
                    **_in_every_language("title", preset["hunt_title"]),
                    **_in_every_language("description", hunt_description),
                },
            )
            keep_hunt_daily_active(hunt)
            return hunt

    now = timezone.now()
    with transaction.atomic():
        event = MeetupEvent.objects.create(
            **_in_every_language("title", title),
            **_in_every_language("description", event_description),
            event_type="crush_cache",
            date_time=now,
            duration_minutes=1440,
            registration_deadline=now + timedelta(days=365),
            registration_fee=0,
            location=preset["location"],
            address=preset["address"],
            max_participants=100,
            is_published=False,
        )
        hunt = CacheHunt.objects.create(
            event=event,
            **_in_every_language("title", preset["hunt_title"]),
            **_in_every_language("description", hunt_description),
            status="live",
            started_at=now,
            navigation_mode=preset["navigation_mode"],
            team_size_max=max(preset["team_size_max"], 4),
            allow_self_join=True,
            created_by=owner,
        )
        _create_stations_from_preset(hunt, preset)
    return hunt


def ensure_all_preset_rehearsal_hunts(owner):
    """Ensure all configured presets exist as daily-active rehearsal hunts."""
    require_cache_rehearsal()
    from crush_lu.management.commands.seed_crush_cache import PRESETS

    return [
        (key, preset, ensure_preset_rehearsal_hunt(key, owner))
        for key, preset in PRESETS.items()
    ]


def list_existing_staging_hunts(exclude_event_ids):
    """Return existing non-rehearsal CacheHunts present in the staging DB."""
    from crush_lu.models.crush_cache import CacheHunt

    return list(
        CacheHunt.objects.select_related("event")
        .prefetch_related("stations")
        .exclude(event_id__in=list(exclude_event_ids))
        .order_by("-created_at")
    )


def ensure_rehearsal_registration(hunt, user):
    """Ensure ``user`` holds an active seat on ``hunt.event``.

    Practice members stay on ``"confirmed"`` rather than ``"attended"`` so they
    are never marked event-verified by side-effect.
    """
    require_cache_rehearsal()
    from crush_lu.models import EventRegistration

    desired_status = "confirmed" if is_practice_user_id(user.pk) else "attended"
    now = timezone.now()
    defaults = {"status": desired_status}
    if desired_status == "attended":
        defaults["checked_in_at"] = now

    registration, created = EventRegistration.objects.get_or_create(
        event=hunt.event,
        user=user,
        defaults=defaults,
    )
    if not created:
        updates = []
        if registration.status in ("cancelled", "no_show") or (
            desired_status == "attended" and registration.status != "attended"
        ):
            registration.status = desired_status
            updates.append("status")
        if desired_status == "attended" and registration.checked_in_at is None:
            registration.checked_in_at = now
            updates.append("checked_in_at")
        if updates:
            registration.save(update_fields=updates)
    return registration


def _reset_fields(first_station, now):
    return {
        "current_station": first_station,
        "total_points": 0,
        "is_finished": False,
        "finished_at": None,
        "last_lat": None,
        "last_lng": None,
        "last_accuracy": None,
        "last_position_at": None,
        "started_at": now,
    }


def _wipe_locked_team_progress(team, progress, reset_defaults, save_progress=True):
    """Delete station attempts and reset fields while ``progress`` is locked."""
    from crush_lu.models.crush_cache import CacheStationAttempt

    CacheStationAttempt.objects.filter(team=team).delete()
    if save_progress:
        for field, value in reset_defaults.items():
            setattr(progress, field, value)
        progress.save(update_fields=list(reset_defaults.keys()))
    return progress


def reset_team_run(hunt, team):
    """Wipe ``team``'s station attempts & points back to Station 1 today."""
    require_cache_rehearsal()
    from crush_lu.models.crush_cache import CacheTeamProgress

    first_station = hunt.ordered_stations().first()
    now = timezone.now()
    reset_defaults = _reset_fields(first_station, now)
    with transaction.atomic():
        # Acquire CacheTeamProgress lock before deleting attempts to match
        # cache_answer_api / cache_hint_api (progress -> attempts) and prevent
        # PostgreSQL deadlocks when resetting a shared team during live play.
        progress, created = CacheTeamProgress.objects.select_for_update().get_or_create(
            team=team,
            defaults=reset_defaults,
        )
        _wipe_locked_team_progress(
            team, progress, reset_defaults, save_progress=not created
        )
    return progress


def maybe_daily_reset_team(hunt, team):
    """Auto-reset ``team`` to Station 1 when its run started on a previous local day."""
    require_cache_rehearsal()
    from crush_lu.models.crush_cache import CacheTeamProgress

    now = timezone.now()
    first_station = hunt.ordered_stations().first()
    progress, created = CacheTeamProgress.objects.get_or_create(
        team=team,
        defaults={"current_station": first_station, "started_at": now},
    )
    if not created and progress.started_at is not None:
        if timezone.localdate(progress.started_at) < timezone.localdate(now):
            reset_defaults = _reset_fields(first_station, now)
            with transaction.atomic():
                (
                    locked,
                    locked_created,
                ) = CacheTeamProgress.objects.select_for_update().get_or_create(
                    team=team,
                    defaults=reset_defaults,
                )
                if locked_created:
                    return locked, True
                if locked.started_at is not None and timezone.localdate(
                    locked.started_at
                ) < timezone.localdate(now):
                    return (
                        _wipe_locked_team_progress(team, locked, reset_defaults),
                        True,
                    )
                return locked, False

    updates = []
    if progress.started_at is None:
        progress.started_at = now
        updates.append("started_at")
    if (
        not progress.is_finished
        and progress.current_station_id is None
        and first_station
    ):
        progress.current_station = first_station
        updates.append("current_station")
    if updates:
        progress.save(update_fields=updates)
    return progress, False


def _personal_team_name(user):
    label = (user.first_name or user.username or f"User {user.pk}").strip()
    if not is_practice_user_id(user.pk):
        return f"Coach {label}"[:100]
    return label[:100]


def enter_solo_play(hunt, user):
    """Put ``user`` into a ready-to-play solo team for ``hunt``, resetting if stale."""
    require_cache_rehearsal()
    from crush_lu.models import EventRegistration
    from crush_lu.models.crush_cache import CacheTeam, CacheTeamMember

    keep_hunt_daily_active(hunt)
    registration = ensure_rehearsal_registration(hunt, user)
    try:
        with transaction.atomic():
            # Lock the registration row first so overlapping Play solo requests
            # for the same user serialize on PostgreSQL without creating extra teams.
            EventRegistration.objects.select_for_update().get(pk=registration.pk)
            membership = (
                CacheTeamMember.objects.filter(hunt=hunt, registration=registration)
                .select_related("team")
                .first()
            )
            if membership:
                team = membership.team
            else:
                color = CacheTeam.COLOR_CHOICES[
                    hunt.teams.count() % len(CacheTeam.COLOR_CHOICES)
                ][0]
                team = CacheTeam.objects.create(
                    hunt=hunt, name=_personal_team_name(user), color=color
                )
                CacheTeamMember.objects.create(
                    hunt=hunt, team=team, registration=registration
                )
    except IntegrityError:
        # Another concurrent request (e.g. cache_join_team or a race on a
        # backend without row locks) inserted the membership first; our
        # atomic block rolled back the unused CacheTeam, so reuse the winner.
        membership = (
            CacheTeamMember.objects.filter(hunt=hunt, registration=registration)
            .select_related("team")
            .first()
        )
        if membership is None:
            raise
        team = membership.team
    _progress, did_reset = maybe_daily_reset_team(hunt, team)
    return team, did_reset


def enter_lobby_mode(hunt, user, leave_solo=True):
    """Prepare ``user`` to form or join a team in the Lobby on a live hunt."""
    require_cache_rehearsal()
    from crush_lu.models.crush_cache import CacheTeamMember

    keep_hunt_daily_active(hunt)
    ensure_rehearsal_registration(hunt, user)
    membership = (
        CacheTeamMember.objects.filter(hunt=hunt, registration__user=user)
        .select_related("team")
        .first()
    )
    if not membership:
        return
    if leave_solo and membership.team.members.count() == 1:
        solo_team = membership.team
        membership.delete()
        if not solo_team.members.exists():
            solo_team.delete()
        return
    maybe_daily_reset_team(hunt, membership.team)
