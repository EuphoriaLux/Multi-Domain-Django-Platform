"""
Crush Cache gameplay views — GPS + QR scavenger hunt at events.

Flow: lobby (join/create team) → play (navigate → arrive/scan → answer
challenges → next station) → finish. A coach dashboard starts/finishes
the hunt and follows a live leaderboard + team map.

All mutations happen over HTTP (HTMX posts) and broadcast via the channel
layer, mirroring views_checkin.py; CacheHuntConsumer is a read-only relay.
"""

import json
import logging
import uuid
from functools import wraps

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from .decorators import crush_login_required, ratelimit
from .geo import bearing_deg, haversine_m
from .ios_app_utils import ios_cache_suppressed
from .models import EventRegistration
from .models.crush_cache import (
    CacheChallenge,
    CacheChallengeAttempt,
    CacheHunt,
    CacheStation,
    CacheStationAttempt,
    CacheTeam,
    CacheTeamMember,
    CacheTeamProgress,
)

from .models.events import SEAT_HOLDING_STATUSES

logger = logging.getLogger(__name__)

# Positions with worse reported accuracy than this are ignored outright —
# they carry no locational information worth acting on.
MAX_ACCEPTED_ACCURACY_M = 200
# Cap on how much reported GPS inaccuracy widens the arrival tolerance.
# Without it, a spoofer could claim accuracy=5000 and be "within range"
# of every station from their couch.
ACCURACY_TOLERANCE_CAP_M = 50


# =============================================================================
# Helpers
# =============================================================================


def _cache_enabled(request=None):
    if not getattr(settings, "CRUSH_CACHE_ENABLED", False):
        return False
    if request is not None and ios_cache_suppressed(request):
        return False
    return True


def _get_hunt_or_404(event_id, request=None):
    if not _cache_enabled(request):
        raise Http404
    return get_object_or_404(
        CacheHunt.objects.select_related("event"), event_id=event_id
    )


def _get_registration(hunt, user):
    """The user's active registration for the hunt's event, or None."""
    return EventRegistration.objects.filter(
        event=hunt.event, user=user, status__in=SEAT_HOLDING_STATUSES
    ).first()


def _get_membership(hunt, user):
    """The user's team membership, only while their registration is active.

    Cancelled/no-show registrations keep their CacheTeamMember row (history)
    but lose all gameplay access — every player endpoint gates on this.
    """
    return (
        CacheTeamMember.objects.filter(
            hunt=hunt,
            registration__user=user,
            registration__status__in=SEAT_HOLDING_STATUSES,
        )
        .select_related("team", "registration")
        .first()
    )


def _can_manage_hunt(user, hunt):
    """Mirror of the quiz host check (QuizConsumer.is_host): the hunt's
    creator or a coach assigned to its event — NOT any active coach, since
    coach pages expose live team GPS and start/finish controls."""
    if hunt.created_by_id == user.id:
        return True
    from .models import CrushCoach
    from .services.cache_rehearsal import is_rehearsal_cache_host

    if is_rehearsal_cache_host(hunt, user):
        return True

    return CrushCoach.objects.filter(
        user=user, is_active=True, assigned_events=hunt.event_id
    ).exists()


def _first_station(hunt):
    return hunt.stations.order_by("order").first()


def _ensure_progress(hunt, team):
    """Create the team's progress row pointing at station 1.

    Called at hunt start for existing teams and lazily for teams formed
    after the coach pressed start.
    """
    progress, created = CacheTeamProgress.objects.get_or_create(
        team=team,
        defaults={
            "current_station": _first_station(hunt),
            "started_at": timezone.now() if hunt.is_live else None,
        },
    )
    if hunt.is_live and progress.started_at is None:
        progress.started_at = timezone.now()
        if progress.current_station is None and not progress.is_finished:
            progress.current_station = _first_station(hunt)
        progress.save(update_fields=["started_at", "current_station"])
    return progress


def _broadcast_cache(hunt_id, msg_type, data, coach_only=False):
    """Broadcast a hunt update over the channel layer.

    msg_type is the consumer handler in dotted form, e.g. "cache.progress".
    Player positions go only to the coach group — teams never see each
    other's location.
    """
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    group = f"cache_{hunt_id}_coach" if coach_only else f"cache_{hunt_id}"
    try:
        async_to_sync(channel_layer.group_send)(group, {"type": msg_type, "data": data})
    except Exception:
        logger.exception("Failed to broadcast %s for hunt %s", msg_type, hunt_id)


def _challenge_states(attempt):
    """Ordered list of (challenge, challenge_attempt-or-None) for a station."""
    attempts_by_challenge = {
        ca.challenge_id: ca for ca in attempt.challenge_attempts.all()
    }
    return [
        (challenge, attempts_by_challenge.get(challenge.id))
        for challenge in attempt.station.challenges.order_by("challenge_order")
    ]


def _play_context(hunt, membership):
    """Build the full re-entrant state for the play screen.

    Whatever happened before (refresh, phone swap, mid-challenge), the
    current state derives entirely from progress + attempt timestamps.
    """
    team = membership.team
    progress = _ensure_progress(hunt, team)

    context = {
        "hunt": hunt,
        "event": hunt.event,
        "team": team,
        "membership": membership,
        "progress": progress,
        "station": None,
        "attempt": None,
        "challenge_states": [],
        "current_challenge": None,
        "current_challenge_attempt": None,
        "station_count": hunt.stations.count(),
        "show_target_coords": hunt.navigation_mode == "map",
    }

    if progress.is_finished or hunt.status == "finished":
        return context

    station = progress.current_station
    if station is None:
        return context

    attempt, _ = CacheStationAttempt.objects.get_or_create(team=team, station=station)
    states = _challenge_states(attempt)
    current = next(((c, ca) for c, ca in states if not (ca and ca.is_correct)), None)
    current_attempt = current[1] if current else None
    needs_gps = station.requires_gps and attempt.arrived_at is None
    context.update(
        {
            "station": station,
            "attempt": attempt,
            "challenge_states": states,
            "current_challenge": current[0] if current else None,
            "current_challenge_attempt": current_attempt,
            "current_hints_used": (
                (current_attempt.hints_used or []) if current_attempt else []
            ),
            "needs_gps": needs_gps,
            # Target coordinates reach the client ONLY in map mode
            "show_map": (
                hunt.navigation_mode == "map"
                and needs_gps
                and station.latitude is not None
            ),
            "completed_stations_json": json.dumps(
                [
                    {
                        "name": s.name,
                        "lat": float(s.latitude) if s.latitude is not None else None,
                        "lng": float(s.longitude) if s.longitude is not None else None,
                        "order": s.order,
                    }
                    for s in hunt.stations.filter(
                        attempts__team=team, attempts__completed_at__isnull=False
                    ).order_by("order")
                ]
            ),
        }
    )
    return context


def _render_play_content(request, hunt, membership, extra=None):
    """The single HTMX-swappable play region — every mutation returns it.

    `extra` carries transient, response-only context (celebration,
    answer-feedback and rate-limit flags) on top of the re-entrant
    DB-derived state — a refresh always lands back on plain derived state.
    """
    context = _play_context(hunt, membership)
    if extra:
        context.update(extra)
    return render(request, "crush_lu/cache/_play_content.html", context)


# =============================================================================
# Player views — lobby & teams
# =============================================================================


@crush_login_required
def cache_lobby(request, event_id):
    """Team formation screen shown before (and during) the hunt."""
    hunt = _get_hunt_or_404(event_id, request=request)
    registration = _get_registration(hunt, request.user)
    membership = _get_membership(hunt, request.user) if registration else None

    if membership and (
        hunt.status == "finished"
        or (hunt.is_live and request.GET.get("view") != "lobby")
    ):
        return redirect("crush_lu:cache_play", event_id=event_id)

    teams = hunt.teams.prefetch_related(
        "members__registration__user__crushprofile"
    ).order_by("created_at")

    from .services.cache_rehearsal import (
        cache_rehearsal_enabled,
        resolve_rehearsal_actor,
    )

    rehearsal_actor = (
        resolve_rehearsal_actor(request) if cache_rehearsal_enabled() else None
    )

    return render(
        request,
        "crush_lu/cache/lobby.html",
        {
            "hunt": hunt,
            "event": hunt.event,
            "registration": registration,
            "membership": membership,
            "teams": teams,
            "cache_rehearsal_can_leave": rehearsal_actor is not None,
        },
    )


@crush_login_required
@require_POST
def cache_join_team(request, event_id):
    """Join an existing team by code, or create a new one."""
    hunt = _get_hunt_or_404(event_id, request=request)
    registration = _get_registration(hunt, request.user)

    if registration is None:
        messages.error(request, _("You must be registered for this event to play."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    if hunt.status == "finished":
        messages.error(request, _("This hunt has already finished."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    if not hunt.allow_self_join:
        # Coach forms ALL teams for this hunt — joining by code would let
        # anyone with a leaked code alter coach-made rosters.
        messages.error(request, _("Teams are formed by the coach for this hunt."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    if _get_membership(hunt, request.user):
        messages.info(request, _("You are already in a team."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    join_code = request.POST.get("join_code", "").strip().upper()
    team_name = request.POST.get("team_name", "").strip()

    try:
        with transaction.atomic():
            if join_code:
                # Lock the team row so two concurrent joins can't both pass
                # the capacity check for the last remaining slot.
                team = (
                    hunt.teams.select_for_update().filter(join_code=join_code).first()
                )
                if team is None:
                    messages.error(request, _("No team found with that code."))
                    return redirect("crush_lu:cache_lobby", event_id=event_id)
                if team.member_count() >= hunt.team_size_max:
                    messages.error(request, _("That team is already full."))
                    return redirect("crush_lu:cache_lobby", event_id=event_id)
            elif team_name:
                color = CacheTeam.COLOR_CHOICES[
                    hunt.teams.count() % len(CacheTeam.COLOR_CHOICES)
                ][0]
                team = CacheTeam.objects.create(
                    hunt=hunt, name=team_name[:100], color=color
                )
            else:
                messages.error(request, _("Enter a join code or a new team name."))
                return redirect("crush_lu:cache_lobby", event_id=event_id)

            CacheTeamMember.objects.create(
                hunt=hunt, team=team, registration=registration
            )
    except IntegrityError:
        messages.info(request, _("You are already in a team."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    messages.success(request, _("You joined %(team)s!") % {"team": team.name})
    if hunt.is_live:
        return redirect("crush_lu:cache_play", event_id=event_id)
    return redirect("crush_lu:cache_lobby", event_id=event_id)


@crush_login_required
@require_POST
def cache_leave_team(request, event_id):
    """Leave the current team — only while the hunt hasn't started (or staging rehearsal)."""
    hunt = _get_hunt_or_404(event_id, request=request)
    from .services.cache_rehearsal import (
        cache_rehearsal_enabled,
        resolve_rehearsal_actor,
    )

    is_rehearsal_actor = (
        cache_rehearsal_enabled() and resolve_rehearsal_actor(request) is not None
    )

    if hunt.status != "draft" and not is_rehearsal_actor:
        messages.error(request, _("Teams are locked once the hunt has started."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    if not hunt.allow_self_join and not is_rehearsal_actor:
        messages.error(request, _("Teams are formed by the coach for this hunt."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    membership = _get_membership(hunt, request.user)
    if membership:
        team = membership.team
        membership.delete()
        # Remove empty self-created teams so stale codes don't linger
        if not team.members.exists():
            team.delete()
        messages.success(request, _("You left the team."))
    return redirect("crush_lu:cache_lobby", event_id=event_id)


# =============================================================================
# Player views — gameplay
# =============================================================================


@crush_login_required
def cache_play(request, event_id):
    """The main play screen — a server-rendered state machine."""
    hunt = _get_hunt_or_404(event_id, request=request)
    membership = _get_membership(hunt, request.user)

    if membership is None:
        messages.info(request, _("Join a team first."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    if hunt.status == "draft":
        messages.info(request, _("The hunt hasn't started yet."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    from .services.cache_rehearsal import (
        cache_rehearsal_enabled,
        maybe_daily_reset_team,
        resolve_rehearsal_actor,
    )

    rehearsal_actor = (
        resolve_rehearsal_actor(request) if cache_rehearsal_enabled() else None
    )
    if rehearsal_actor is not None:
        _prog, did_reset = maybe_daily_reset_team(hunt, membership.team)
        if did_reset:
            messages.info(request, _("New day — your run was reset to Station 1."))

    context = _play_context(hunt, membership)
    if rehearsal_actor is not None:
        context["cache_rehearsal_toolbar"] = True
        context["rehearsal_is_coach"] = rehearsal_actor[0].pk == rehearsal_actor[1].pk
    if context["progress"].is_finished or hunt.status == "finished":
        # Only the finish screen shows the leaderboard — the play screen
        # and its HTMX swaps don't, so _play_context skips the query.
        context["leaderboard"] = hunt.get_leaderboard()
        if hunt.status != "finished":
            # Baseline for the finish page's standings poll: mirror the
            # cache_state_api payload so the first poll compares against
            # the rendered page instead of becoming the baseline itself
            # (which would swallow any change in the first interval).
            context["state_snapshot"] = {
                "status": hunt.status,
                "leaderboard": hunt.get_serialized_leaderboard(),
            }
        return render(request, "crush_lu/cache/finish.html", context)
    return render(request, "crush_lu/cache/play.html", context)


@crush_login_required
def cache_scanner(request, event_id):
    """Camera QR scanner (html5-qrcode), mirroring the Advent scanner."""
    hunt = _get_hunt_or_404(event_id, request=request)
    if _get_membership(hunt, request.user) is None:
        messages.info(request, _("Join a team first."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)
    return render(
        request,
        "crush_lu/cache/scanner.html",
        {"hunt": hunt, "event": hunt.event},
    )


@crush_login_required
def cache_qr_scan(request, token):
    """Destination of a scanned station QR code.

    The token only identifies the station — authorization is the scanning
    user's team state. Many teams scan the same physical sticker.
    """
    if not _cache_enabled(request):
        raise Http404
    station = get_object_or_404(
        CacheStation.objects.select_related("hunt__event"), qr_token=token
    )
    return _process_station_scan(request, station)


@crush_login_required
@require_POST
@ratelimit(key="user", rate="20/m", method="POST")
def cache_manual_code(request, event_id):
    """Manual fallback for the QR scanner: type the short code printed
    under the QR (or paste a full token URL/UUID) instead of scanning."""
    hunt = _get_hunt_or_404(event_id, request=request)
    if _get_membership(hunt, request.user) is None:
        messages.error(request, _("Join a team first, then enter the code again."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    code = request.POST.get("code", "").strip()
    station = None
    if code:
        # A pasted QR URL or raw UUID still works; otherwise treat the
        # input as the printed short code. Both scoped to this hunt.
        try:
            token = uuid.UUID(code.rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            station = hunt.stations.filter(manual_code=code.upper()).first()
        else:
            station = hunt.stations.filter(qr_token=token).first()

    if station is None:
        messages.error(
            request,
            _(
                "That code doesn't match any station of this hunt — check the "
                "characters printed under the QR code."
            ),
        )
        return redirect("crush_lu:cache_scanner", event_id=event_id)

    return _process_station_scan(request, station)


def _process_station_scan(request, station):
    """Shared body of the QR-scan and manual-code endpoints: record the
    scan for the user's team if this is their current station."""
    hunt = station.hunt
    event_id = hunt.event_id

    membership = _get_membership(hunt, request.user)
    if membership is None:
        messages.error(request, _("Join a team first, then scan the code again."))
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    if not hunt.is_live:
        messages.error(
            request,
            _("The hunt isn't running right now — this code will work once it starts."),
        )
        return redirect("crush_lu:cache_lobby", event_id=event_id)

    progress = _ensure_progress(hunt, membership.team)
    if progress.is_finished:
        return redirect("crush_lu:cache_play", event_id=event_id)

    if progress.current_station_id != station.id:
        already_done = CacheStationAttempt.objects.filter(
            team=membership.team, station=station, completed_at__isnull=False
        ).exists()
        if already_done:
            messages.info(
                request,
                _("Your team already completed %(station)s.")
                % {"station": station.name},
            )
        else:
            messages.warning(
                request,
                _("That's not your current station — keep following your clue!"),
            )
        return redirect("crush_lu:cache_play", event_id=event_id)

    attempt, _created = CacheStationAttempt.objects.get_or_create(
        team=membership.team, station=station
    )
    if attempt.scanned_at is None:
        now = timezone.now()
        CacheStationAttempt.objects.filter(
            pk=attempt.pk, scanned_at__isnull=True
        ).update(scanned_at=now)
        attempt.scanned_at = now  # reflect the write for the is_unlocked check
        if attempt.is_unlocked:
            messages.success(
                request,
                _("Code scanned — %(station)s unlocked!") % {"station": station.name},
            )
        else:
            # A gps_qr station scanned before the team has arrived: the scan is
            # recorded, but GPS arrival is still required — don't claim it's
            # unlocked when it isn't.
            messages.info(
                request,
                _("Code scanned — now reach the location to unlock %(station)s.")
                % {"station": station.name},
            )
    elif attempt.is_unlocked:
        messages.info(
            request,
            _("Already scanned — your challenges await below."),
        )
    else:
        messages.info(
            request,
            _("Already scanned — now reach the location to unlock %(station)s.")
            % {"station": station.name},
        )
    return redirect("crush_lu:cache_play", event_id=event_id)


@crush_login_required
@require_POST
@ratelimit(key="user", rate="60/m", method="POST", block=False)
def cache_position_api(request, event_id):
    """Receive a GPS fix, decide arrival server-side, feed the coach map.

    The client never decides "arrived", and for compass/hidden navigation
    modes the response carries distance/bearing only — never raw
    coordinates a player could feed into a map app.

    Rate limit: the client throttles itself to one POST per 3 s (20/min),
    but the same user with the page open twice (task-switcher reopen,
    duplicated tab) doubles that — 60/m keeps the brute-force guard
    without refusing honest players. block=False so a refusal is our own
    JSON 429 the client backs off from, never django-ratelimit's HTML 403,
    which players' browsers used to treat as fatal.
    """
    if getattr(request, "limited", False):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)

    hunt = _get_hunt_or_404(event_id, request=request)
    membership = _get_membership(hunt, request.user)
    if membership is None:
        return JsonResponse({"ok": False, "error": "no_team"}, status=403)

    try:
        payload = json.loads(request.body)
        lat = float(payload["lat"])
        lng = float(payload["lng"])
        accuracy = float(payload.get("accuracy", 0))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)

    if not (-90 <= lat <= 90 and -180 <= lng <= 180 and accuracy >= 0):
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)

    if not hunt.is_live:
        return JsonResponse({"ok": False, "error": "not_live"}, status=403)

    progress = _ensure_progress(hunt, membership.team)
    if progress.is_finished:
        return JsonResponse({"ok": False, "error": "finished"}, status=403)

    if accuracy > MAX_ACCEPTED_ACCURACY_M:
        return JsonResponse({"ok": True, "accepted": False, "reason": "accuracy"})

    now = timezone.now()
    CacheTeamProgress.objects.filter(pk=progress.pk).update(
        last_lat=lat, last_lng=lng, last_accuracy=accuracy, last_position_at=now
    )

    _broadcast_cache(
        hunt.id,
        "cache.position",
        {
            "team_id": membership.team_id,
            "team_name": membership.team.name,
            "team_color": membership.team.color,
            "lat": lat,
            "lng": lng,
            "accuracy": accuracy,
            "at": now.isoformat(),
        },
        coach_only=True,
    )

    response = {"ok": True, "accepted": True, "arrived": False, "unlocked": False}
    station = progress.current_station

    if progress.is_finished or not hunt.is_live or station is None:
        return JsonResponse(response)

    attempt, _created = CacheStationAttempt.objects.get_or_create(
        team=membership.team, station=station
    )

    if station.latitude is not None and station.longitude is not None:
        distance = haversine_m(lat, lng, station.latitude, station.longitude)
        if hunt.navigation_mode in ("map", "compass"):
            response["distance_m"] = round(distance)
            response["bearing"] = round(
                bearing_deg(lat, lng, station.latitude, station.longitude)
            )

        if (
            station.requires_gps
            and attempt.arrived_at is None
            and distance
            <= station.radius_meters + min(accuracy, ACCURACY_TOLERANCE_CAP_M)
        ):
            CacheStationAttempt.objects.filter(
                pk=attempt.pk, arrived_at__isnull=True
            ).update(arrived_at=now)
            attempt.refresh_from_db()
            response["arrived"] = True

    response["arrived"] = response["arrived"] or attempt.arrived_at is not None
    response["unlocked"] = attempt.is_unlocked
    return JsonResponse(response)


@crush_login_required
@require_POST
@ratelimit(key="user", rate="20/m", method="POST", block=False)
def cache_answer_api(request, event_id, challenge_id):
    """Submit an answer for the current station (HTMX form post).

    Concurrency: any team member may answer; the progress row is locked
    and the (station_attempt, challenge) unique constraint makes the
    first correct submission win — a concurrent duplicate re-renders as
    "already answered".

    Rate-limited per user: without it a multiple-choice challenge is
    brute-forceable in seconds (position API is likewise capped). We ask
    the decorator not to block (``block=False``) and handle the limit
    here: this form swaps ``#play-content``, and HTMX ignores a bare 429,
    so a blocking response would just make the button silently stop
    working. Re-rendering the panel with a visible notice instead — and
    returning before the attempt row is touched, so a throttled post never
    counts against the team.
    """
    hunt = _get_hunt_or_404(event_id, request=request)
    membership = _get_membership(hunt, request.user)
    if membership is None:
        return redirect("crush_lu:cache_lobby", event_id=event_id)
    if not hunt.is_live:
        return _render_play_content(request, hunt, membership)
    if getattr(request, "limited", False):
        return _render_play_content(
            request, hunt, membership, extra={"rate_limited": True}
        )

    challenge = get_object_or_404(
        CacheChallenge.objects.select_related("station"),
        pk=challenge_id,
        station__hunt=hunt,
    )
    team = membership.team
    now = timezone.now()
    station_completed = False
    hunt_completed = False
    answer_correct = False

    with transaction.atomic():
        progress = CacheTeamProgress.objects.select_for_update().get(team=team)

        if progress.is_finished or progress.current_station_id != challenge.station_id:
            # Stale form — another member advanced the team meanwhile
            if request.headers.get("HX-Request") == "true":
                return HttpResponse(status=200, headers={"HX-Refresh": "true"})
            return _render_play_content(request, hunt, membership)

        attempt, _created = CacheStationAttempt.objects.get_or_create(
            team=team, station=challenge.station
        )
        if not attempt.is_unlocked:
            return _render_play_content(request, hunt, membership)

        ca, _created = CacheChallengeAttempt.objects.get_or_create(
            station_attempt=attempt, challenge=challenge
        )
        if ca.is_correct:
            return _render_play_content(request, hunt, membership)

        answer = request.POST.get("answer", "").strip()
        photo = request.FILES.get("photo")

        if challenge.challenge_type == "photo_task" and photo is None:
            messages.error(request, _("Please take a photo to complete this task."))
            return _render_play_content(request, hunt, membership)

        ca.last_answer = answer[:2000]
        ca.attempts_count += 1
        ca.answered_by = request.user
        if photo is not None:
            ca.photo = photo

        answer_correct = challenge.check_answer(answer)
        if answer_correct:
            ca.is_correct = True
            ca.points_earned = max(0, challenge.points_awarded - ca.hint_cost_total())
            ca.answered_at = now
            progress.total_points += ca.points_earned
        ca.save()

        if answer_correct:
            total_challenges = challenge.station.challenges.count()
            correct_count = attempt.challenge_attempts.filter(is_correct=True).count()
            if correct_count >= total_challenges:
                station_completed = True
                attempt.completed_at = now
                attempt.points_earned = (
                    attempt.challenge_attempts.aggregate(Sum("points_earned"))[
                        "points_earned__sum"
                    ]
                    or 0
                )
                attempt.save(update_fields=["completed_at", "points_earned"])

                next_station = (
                    hunt.stations.filter(order__gt=challenge.station.order)
                    .order_by("order")
                    .first()
                )
                if next_station:
                    progress.current_station = next_station
                else:
                    hunt_completed = True
                    progress.current_station = None
                    progress.is_finished = True
                    progress.finished_at = now
        progress.save()

    if answer_correct:
        if station_completed:
            _broadcast_cache(
                hunt.id,
                "cache.progress",
                {
                    "team_id": team.id,
                    "team_name": team.name,
                    "station_order": challenge.station.order,
                    "station_name": challenge.station.name,
                    "points": progress.total_points,
                    "is_finished": hunt_completed,
                },
            )
        _broadcast_cache(
            hunt.id,
            "cache.leaderboard",
            {"leaderboard": hunt.get_serialized_leaderboard()},
        )

    if station_completed:
        # Render an in-flow celebration instead of an abrupt HX-Refresh;
        # its "Continue" link does the full reload that refreshes the
        # stale play shell (map, target coords). A manual refresh in the
        # meantime simply lands on the next station's derived state.
        return _render_play_content(
            request,
            hunt,
            membership,
            extra={
                "celebrated_station": challenge.station,
                "celebrated_points": attempt.points_earned,
                "hunt_completed": hunt_completed,
            },
        )

    if answer_correct:
        # Correct but more challenges remain at this station: flash the
        # success before showing the next challenge.
        return _render_play_content(
            request,
            hunt,
            membership,
            extra={
                "just_correct": True,
                "just_correct_message": challenge.success_message,
            },
        )

    return _render_play_content(request, hunt, membership, extra={"just_wrong": True})


@crush_login_required
@require_POST
def cache_hint_api(request, event_id, challenge_id, hint_number):
    """Reveal a hint. The cost is deducted at scoring time (Journey
    semantics), so peeking is free until the team actually answers."""
    hunt = _get_hunt_or_404(event_id, request=request)
    membership = _get_membership(hunt, request.user)
    if membership is None:
        return redirect("crush_lu:cache_lobby", event_id=event_id)
    if not hunt.is_live or hint_number not in (1, 2, 3):
        return _render_play_content(request, hunt, membership)

    challenge = get_object_or_404(
        CacheChallenge.objects.select_related("station"),
        pk=challenge_id,
        station__hunt=hunt,
    )
    if not challenge.get_hint(hint_number):
        return _render_play_content(request, hunt, membership)

    with transaction.atomic():
        progress = CacheTeamProgress.objects.select_for_update().get(
            team=membership.team
        )
        if progress.is_finished or progress.current_station_id != challenge.station_id:
            if request.headers.get("HX-Request") == "true":
                return HttpResponse(status=200, headers={"HX-Refresh": "true"})
            return _render_play_content(request, hunt, membership)

        attempt, _created = CacheStationAttempt.objects.get_or_create(
            team=membership.team, station=challenge.station
        )
        if not attempt.is_unlocked:
            return _render_play_content(request, hunt, membership)

        ca, _created = CacheChallengeAttempt.objects.get_or_create(
            station_attempt=attempt, challenge=challenge
        )
        if not ca.is_correct and hint_number not in (ca.hints_used or []):
            ca.hints_used = sorted((ca.hints_used or []) + [hint_number])
            ca.save(update_fields=["hints_used"])

    return _render_play_content(request, hunt, membership)


@crush_login_required
def cache_state_api(request, event_id):
    """Polling fallback for clients whose WebSocket won't connect."""
    hunt = _get_hunt_or_404(event_id, request=request)
    membership = _get_membership(hunt, request.user)
    if membership is None:
        return JsonResponse({"ok": False, "error": "no_team"}, status=403)
    if hunt.status != "draft":
        progress = _ensure_progress(hunt, membership.team)
        current_station_id = progress.current_station_id
        is_finished = progress.is_finished
    else:
        current_station_id = None
        is_finished = False
    return JsonResponse(
        {
            "ok": True,
            "status": hunt.status,
            "current_station_id": current_station_id,
            "is_finished": is_finished,
            "leaderboard": hunt.get_serialized_leaderboard(),
        }
    )


# =============================================================================
# Coach views
# =============================================================================


@crush_login_required
def cache_coach_dashboard(request, event_id):
    """Live control room: readiness, start/finish, leaderboard, team map."""
    hunt = _get_hunt_or_404(event_id, request=request)
    if not _can_manage_hunt(request.user, hunt):
        messages.error(request, _("You are not a coach for this event."))
        return redirect("crush_lu:dashboard")

    teams = hunt.teams.prefetch_related(
        "members__registration__user__crushprofile"
    ).order_by("created_at")

    stations = list(hunt.ordered_stations())
    map_data = {
        "stations": [
            {
                "order": s.order,
                "name": s.name,
                "lat": float(s.latitude) if s.latitude is not None else None,
                "lng": float(s.longitude) if s.longitude is not None else None,
                "radius": s.radius_meters,
            }
            for s in stations
        ],
        "teams": [
            {
                "id": t.id,
                "name": t.name,
                "color": t.color,
                "lat": (
                    float(t.progress.last_lat)
                    if hasattr(t, "progress") and t.progress.last_lat is not None
                    else None
                ),
                "lng": (
                    float(t.progress.last_lng)
                    if hasattr(t, "progress") and t.progress.last_lng is not None
                    else None
                ),
            }
            for t in teams.select_related("progress")
        ],
    }

    # The "View attendees" link targets coach_event_detail, which is
    # @coach_required — but _can_manage_hunt also admits the hunt's
    # creator even when they aren't an active coach, and for them that
    # link would only bounce back with a coach-access error.
    from .models import CrushCoach

    viewer_is_coach = CrushCoach.objects.filter(
        user=request.user, is_active=True
    ).exists()

    return render(
        request,
        "crush_lu/cache/coach_dashboard.html",
        {
            "hunt": hunt,
            "event": hunt.event,
            "teams": teams,
            "stations": stations,
            "readiness": hunt.readiness_check(),
            "leaderboard": hunt.get_leaderboard(),
            "map_data_json": json.dumps(map_data),
            "viewer_is_coach": viewer_is_coach,
            "unassigned_count": EventRegistration.objects.filter(
                event=hunt.event, status__in=SEAT_HOLDING_STATUSES
            )
            .exclude(cache_memberships__hunt=hunt)
            .count(),
        },
    )


@crush_login_required
@require_POST
def cache_coach_start(request, event_id):
    """Start the hunt: draft → live, initialize every team's progress."""
    hunt = _get_hunt_or_404(event_id, request=request)
    if not _can_manage_hunt(request.user, hunt):
        messages.error(request, _("You are not a coach for this event."))
        return redirect("crush_lu:dashboard")

    if not hunt.can_transition_to("live"):
        messages.error(
            request,
            _("Cannot start a hunt in status '%(status)s'.")
            % {"status": hunt.get_status_display()},
        )
        return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)

    blocking_failures = [
        check
        for check in hunt.readiness_check()
        if check.get("blocking") and not check["ok"]
    ]
    if blocking_failures:
        messages.error(
            request,
            _("Cannot start — fix these first: %(issues)s")
            % {
                "issues": "; ".join(
                    f"{check['label']}: {check['detail']}"
                    for check in blocking_failures
                )
            },
        )
        return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)

    now = timezone.now()
    hunt.status = "live"
    hunt.started_at = now
    hunt.save(update_fields=["status", "started_at", "updated_at"])

    for team in hunt.teams.all():
        _ensure_progress(hunt, team)

    _broadcast_cache(hunt.id, "cache.status", {"status": "live"})
    messages.success(request, _("The hunt is live — good luck to all teams!"))
    return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)


@crush_login_required
@require_POST
def cache_coach_finish(request, event_id):
    """Finish the hunt: live → finished. Standings freeze as they are."""
    hunt = _get_hunt_or_404(event_id, request=request)
    if not _can_manage_hunt(request.user, hunt):
        messages.error(request, _("You are not a coach for this event."))
        return redirect("crush_lu:dashboard")

    if not hunt.can_transition_to("finished"):
        messages.error(
            request,
            _("Cannot finish a hunt in status '%(status)s'.")
            % {"status": hunt.get_status_display()},
        )
        return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)

    hunt.status = "finished"
    hunt.finished_at = timezone.now()
    hunt.save(update_fields=["status", "finished_at", "updated_at"])

    _broadcast_cache(hunt.id, "cache.status", {"status": "finished"})
    _broadcast_cache(
        hunt.id,
        "cache.leaderboard",
        {"leaderboard": hunt.get_serialized_leaderboard()},
    )
    messages.success(request, _("The hunt is finished."))
    return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)


@crush_login_required
@require_POST
def cache_coach_auto_teams(request, event_id):
    """Split checked-in attendees without a team into teams of ≤ max size."""
    hunt = _get_hunt_or_404(event_id, request=request)
    if not _can_manage_hunt(request.user, hunt):
        messages.error(request, _("You are not a coach for this event."))
        return redirect("crush_lu:dashboard")

    if hunt.status == "finished":
        messages.error(request, _("The hunt has already finished."))
        return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)

    registrations = list(
        EventRegistration.objects.filter(event=hunt.event, status="attended")
        .exclude(cache_memberships__hunt=hunt)
        .order_by("?")
    )
    if not registrations:
        # Nobody checked in yet — fall back to confirmed so teams can be
        # pre-formed before the doors open.
        registrations = list(
            EventRegistration.objects.filter(
                event=hunt.event, status__in=SEAT_HOLDING_STATUSES
            )
            .exclude(cache_memberships__hunt=hunt)
            .order_by("?")
        )
    if not registrations:
        messages.warning(request, _("No unassigned attendees to place in teams."))
        return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)

    size = max(1, hunt.team_size_max)
    num_teams = -(-len(registrations) // size)  # ceil division
    existing = hunt.teams.count()

    created = 0
    for i in range(num_teams):
        color = CacheTeam.COLOR_CHOICES[(existing + i) % len(CacheTeam.COLOR_CHOICES)][
            0
        ]
        team = CacheTeam.objects.create(
            hunt=hunt,
            name=_("Team %(number)d") % {"number": existing + i + 1},
            color=color,
        )
        for registration in registrations[i * size : (i + 1) * size]:
            CacheTeamMember.objects.create(
                hunt=hunt, team=team, registration=registration
            )
            created += 1
        if hunt.is_live:
            _ensure_progress(hunt, team)

    messages.success(
        request,
        _("Placed %(count)d attendees into %(teams)d teams.")
        % {"count": created, "teams": num_teams},
    )
    return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)


@crush_login_required
def cache_coach_state_api(request, event_id):
    """Polling fallback for the coach dashboard (leaderboard + positions)."""
    hunt = _get_hunt_or_404(event_id, request=request)
    if not _can_manage_hunt(request.user, hunt):
        return JsonResponse({"ok": False, "error": "not_event_coach"}, status=403)
    positions = [
        {
            "team_id": p.team_id,
            "team_name": p.team.name,
            "team_color": p.team.color,
            "lat": float(p.last_lat) if p.last_lat is not None else None,
            "lng": float(p.last_lng) if p.last_lng is not None else None,
            "at": p.last_position_at.isoformat() if p.last_position_at else None,
        }
        for p in CacheTeamProgress.objects.filter(team__hunt=hunt).select_related(
            "team"
        )
    ]
    return JsonResponse(
        {
            "ok": True,
            "status": hunt.status,
            "leaderboard": hunt.get_serialized_leaderboard(),
            "positions": positions,
        }
    )


@crush_login_required
def cache_coach_qr_sheet(request, event_id):
    """Printable A4 PDF of the hunt's QR-station codes — the coach-facing
    twin of the admin action (coaches aren't admin users, but they are
    the ones sticking codes to lampposts on event day)."""
    from .qr_utils import HAS_REPORTLAB, generate_cache_station_sheet

    hunt = _get_hunt_or_404(event_id, request=request)
    if not _can_manage_hunt(request.user, hunt):
        messages.error(request, _("You are not a coach for this event."))
        return redirect("crush_lu:dashboard")

    stations = [s for s in hunt.ordered_stations() if s.requires_qr]
    if not stations:
        messages.warning(
            request,
            _("No station of this hunt unlocks via QR — nothing to print."),
        )
        return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)

    if not HAS_REPORTLAB:
        messages.warning(
            request,
            _(
                "PDF generation is unavailable on this server — download the "
                "QR images from the station admin instead."
            ),
        )
        return redirect("crush_lu:cache_coach_dashboard", event_id=event_id)

    pdf_bytes = generate_cache_station_sheet(
        stations,
        title=f"Crush Cache — {hunt.title}",
        # Print URLs for the host the coach is on — a sheet generated on a
        # test slot or localhost must not send players to production.
        domain=request.get_host(),
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="crush-cache-{hunt.pk}-qr-sheet.pdf"'
    )
    return response


# =============================================================================
# Staging-only Field-Test / Rehearsal Hub (404 off staging & DEBUG=False)
# =============================================================================


def _cache_rehearsal_view(view_func):
    """404 off staging before login redirect; then allow active coaches or active practice members."""
    from .services.cache_rehearsal import (
        cache_rehearsal_enabled,
        resolve_rehearsal_actor,
    )

    @crush_login_required
    def _login_gated(request, *args, **kwargs):
        actor = resolve_rehearsal_actor(request)
        if actor is None:
            messages.error(
                request, _("Crush Cache field-test is only open to active coaches.")
            )
            return redirect("crush_lu:dashboard")
        request.rehearsal_coach_user = actor[1]
        request.rehearsal_is_practice = actor[0].pk != actor[1].pk
        return view_func(request, *args, **kwargs)

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not cache_rehearsal_enabled():
            raise Http404
        return _login_gated(request, *args, **kwargs)

    return wrapper


def _rehearsal_unlock_label(stations):
    modes = {s.unlock_mode for s in stations}
    if not modes:
        return _("Open")
    if modes == {"gps"}:
        return "GPS"
    if modes == {"gps_qr"}:
        return "GPS + QR"
    if modes == {"qr"}:
        return "QR"
    if "gps_qr" in modes or ({"gps", "qr"} <= modes):
        return "GPS + QR"
    if "gps" in modes:
        return "GPS"
    if "qr" in modes:
        return "QR"
    return _("Open")


def _rehearsal_hunt_card(hunt, user, subtitle=None):
    stations = list(hunt.ordered_stations())
    membership = (
        CacheTeamMember.objects.filter(hunt=hunt, registration__user=user)
        .select_related("team", "team__progress", "team__progress__current_station")
        .first()
    )
    progress = None
    is_stale_day = False
    if membership:
        try:
            progress = membership.team.progress
        except CacheTeamProgress.DoesNotExist:
            progress = None
        if progress and progress.started_at is not None:
            is_stale_day = timezone.localdate(progress.started_at) < timezone.localdate(
                timezone.now()
            )
    return {
        "hunt": hunt,
        "event": hunt.event,
        "subtitle": subtitle or hunt.event.location,
        "station_count": len(stations),
        "has_qr": any(s.requires_qr for s in stations),
        "unlock_label": _rehearsal_unlock_label(stations),
        "membership": membership,
        "team": membership.team if membership else None,
        "progress": progress,
        "is_stale_day": is_stale_day,
    }


@_cache_rehearsal_view
@require_GET
def cache_rehearsal_hub(request):
    """Staging-only hub listing all preset & existing Crush Caches ready to test today."""
    from .services.cache_rehearsal import (
        ensure_all_preset_rehearsal_hunts,
        list_existing_staging_hunts,
    )

    seeded = ensure_all_preset_rehearsal_hunts(request.rehearsal_coach_user)
    preset_cards = [
        _rehearsal_hunt_card(
            hunt,
            request.user,
            subtitle=f"{preset['location']} — {preset['address']}",
        )
        for _key, preset, hunt in seeded
    ]
    preset_event_ids = {hunt.event_id for _key, _preset, hunt in seeded}
    existing_cards = [
        _rehearsal_hunt_card(hunt, request.user)
        for hunt in list_existing_staging_hunts(preset_event_ids)
    ]

    return render(
        request,
        "crush_lu/cache/rehearsal.html",
        {
            "preset_cards": preset_cards,
            "existing_cards": existing_cards,
            "cache_flag_enabled": getattr(settings, "CRUSH_CACHE_ENABLED", False),
            "ios_suppressed": ios_cache_suppressed(request),
            "rehearsal_is_practice": request.rehearsal_is_practice,
            "rehearsal_is_coach": not request.rehearsal_is_practice,
        },
    )


@_cache_rehearsal_view
@require_POST
def cache_rehearsal_play(request, event_id):
    """Ensure live state + daily reset + personal team, then jump straight into play."""
    from .services.cache_rehearsal import enter_solo_play

    hunt = _get_hunt_or_404(event_id, request=request)
    _team, did_reset = enter_solo_play(hunt, request.user)
    if did_reset:
        messages.info(request, _("New day — your run was reset to Station 1."))
    return redirect("crush_lu:cache_play", event_id=event_id)


@_cache_rehearsal_view
@require_POST
def cache_rehearsal_lobby(request, event_id):
    """Prepare the user to join or create a multi-coach team in the Lobby."""
    from .services.cache_rehearsal import enter_lobby_mode

    hunt = _get_hunt_or_404(event_id, request=request)
    enter_lobby_mode(hunt, request.user, leave_solo=True)
    return redirect(f"{reverse('crush_lu:cache_lobby', args=[event_id])}?view=lobby")


@_cache_rehearsal_view
@require_POST
def cache_rehearsal_reset(request, event_id):
    """Reset only the requesting user's team progress back to Station 1."""
    from .services.cache_rehearsal import keep_hunt_daily_active, reset_team_run

    hunt = _get_hunt_or_404(event_id, request=request)
    keep_hunt_daily_active(hunt)
    membership = (
        CacheTeamMember.objects.filter(hunt=hunt, registration__user=request.user)
        .select_related("team")
        .first()
    )
    if membership is not None:
        reset_team_run(hunt, membership.team)
        messages.success(request, _("Your run has been reset to Station 1."))
    else:
        messages.info(
            request, _("You have not joined this hunt yet — press Play to start.")
        )
    if request.POST.get("next") == "play" and membership is not None:
        return redirect("crush_lu:cache_play", event_id=event_id)
    return redirect("crush_lu:cache_rehearsal")
