"""
Secure media serving views for Crush.lu
Handles photo access with authentication and privacy checks
"""

from django.shortcuts import get_object_or_404
from django.http import HttpResponse, Http404
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
import os
import logging
from datetime import timedelta

from django.utils import timezone

from .models import CrushProfile, CrushCoach
from .oauth_statekit import get_client_ip

logger = logging.getLogger(__name__)


def can_view_profile_photo(viewer, profile_owner):
    """
    Determine if viewer can see profile_owner's photos.

    The URL is addressed by user id, so this check — not the page that
    rendered the URL — is the gate. It encodes the same relationships as the
    surfaces that render these URLs:

    - the owner, active coaches (review) and superusers (admin, voting results)
    - otherwise both profiles must be approved, the pair must not be blocked,
      and one of:
        - both attended the same event while its attendee list is open
          (``MeetupEvent.connections_open``; ``event_attendees``)
        - an ``EventConnection`` between them (``my_connections``,
          ``connection_detail``), except a declined one and a My Crush! lead
          its recipient has not been shown yet
        - a Crush Connect pairing (cycle card, pending weekly request, chat,
          coach pick) and the owner's ``photo_share_consent``

    Args:
        viewer: User object of the person viewing
        profile_owner: CrushProfile object being viewed

    Returns:
        bool: whether the viewer is allowed to see the photo
    """
    owner = profile_owner.user

    # Owner can always see their own photos
    if viewer.pk == owner.pk:
        return True

    # Coaches can see all photos (for review)
    if CrushCoach.objects.filter(user=viewer, is_active=True).exists():
        return True

    if viewer.is_superuser:
        return True

    # Profile must be approved for others to see
    if not profile_owner.is_approved:
        return False

    # Check if viewer has an approved profile
    if not CrushProfile.objects.filter(user=viewer, is_approved=True).exists():
        return False

    from .services.blocking import is_blocked_pair
    from .services.event_lobby import hidden_encounter_user_ids

    # A safety removal of a confirmed encounter hides the pair from each
    # other exactly like a block (``event_attendees`` excludes both).
    if is_blocked_pair(viewer, owner) or owner.pk in hidden_encounter_user_ids(viewer):
        return False

    return (
        _share_open_attendee_list(viewer, owner)
        or _have_event_connection(viewer, owner)
        or _are_connect_paired(viewer, owner)
    )


def _share_open_attendee_list(viewer, owner):
    """Both attended an event whose named attendee list is currently open."""
    from .models import MeetupEvent

    shared_events = MeetupEvent.objects.filter(
        eventregistration__user=viewer, eventregistration__status="attended"
    ).filter(eventregistration__user=owner, eventregistration__status="attended")
    return any(event.connections_open for event in shared_events.distinct())


def _have_event_connection(viewer, owner):
    from django.db.models import Q

    from .models import EventConnection

    crush = Q(flow=EventConnection.FLOW_CRUSH)
    return EventConnection.objects.filter(
        (
            Q(requester=viewer, recipient=owner)
            # ``my_connections`` keeps a declined My Crush! lead in the same
            # neutral card as any other unshared one; refusing its photo
            # would leak the coach-recorded outcome the UI hides.
            & (crush | ~Q(status="declined"))
        )
        | (
            Q(requester=owner, recipient=viewer)
            & ~Q(status="declined")
            # The recipient of a My Crush! lead is never shown it until
            # it is shared (mirrors ``connection_detail``).
            & ~(crush & ~Q(status="shared"))
        )
    ).exists()


def _are_connect_paired(viewer, owner):
    """A Crush Connect surface shows ``owner`` to ``viewer``, and ``owner``
    consented to sharing their photo there ("Read-the-Photo")."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from .models.crush_connect_cycle import ConnectTemporaryChat, ConnectWeeklyRequest
    from .services.crush_connect import (
        exclude_assigned_coach_pairs,
        filter_catalogue_eligible,
        get_active_coach_pick,
    )

    User = get_user_model()
    if not User.objects.filter(
        pk=owner.pk, crush_connect_membership__photo_share_consent=True
    ).exists():
        return False

    # Chats are reached through a request, so they outlive catalogue
    # eligibility; they stay visible while both participants pass
    # ``views_connect_chat._participants_available``.
    available = User.objects.filter(
        pk__in=(viewer.pk, owner.pk),
        is_active=True,
        crushprofile__is_active=True,
        crush_connect_membership__onboarded_at__isnull=False,
        crush_connect_membership__excluded_by_coach=False,
    )
    if (
        available.count() == 2
        and ConnectTemporaryChat.objects.filter(
            Q(participant_1=viewer, participant_2=owner)
            | Q(participant_1=owner, participant_2=viewer)
        )
        .exclude(status=ConnectTemporaryChat.Status.BLOCKED)
        .exists()
    ):
        return True

    # Cards and the inbox only show catalogue-eligible members, never a
    # member's assigned coach (``visible_cycle_cards``, ``get_pending_inbox``).
    owner_qs = exclude_assigned_coach_pairs(User.objects.filter(pk=owner.pk), viewer)
    if not filter_catalogue_eligible(owner_qs).exists():
        return False
    if _has_visible_cycle_card(viewer, owner):
        return True
    if ConnectWeeklyRequest.objects.filter(
        requester=owner,
        recipient=viewer,
        status=ConnectWeeklyRequest.Status.PENDING,
        # Expiry is applied lazily by ``sync_request_state``; a stale PENDING
        # row past its deadline is already gone from the inbox.
        expires_at__gt=timezone.now(),
    ).exists():
        return True
    # The pick page re-validates the pool and the assigned coach on read.
    pick = get_active_coach_pick(viewer, include_accepted=True)
    return pick is not None and pick.candidate_id == owner.pk


def _has_visible_cycle_card(viewer, owner):
    """A card for ``owner`` that Connect Week still renders to ``viewer``:
    today's live card of an active session, or a completed card during the
    24h review. Session bookkeeping is lazy (``sync_session_state``), so the
    day and the review deadline are derived from the clock, not the rows."""
    from .models.crush_connect_cycle import ConnectCycleCard, ConnectWeekSession
    from .services.connect_cycle import CYCLE_LENGTH_DAYS, REVIEW_WINDOW_HOURS

    Status = ConnectWeekSession.Status
    now = timezone.now()
    cards = ConnectCycleCard.objects.filter(
        session__user=viewer,
        target_user=owner,
        session__status__in=(Status.ACTIVE, Status.REVIEW_OPEN),
    ).select_related("session")
    for card in cards:
        session = card.session
        wall_day = (
            timezone.localdate() - timezone.localtime(session.started_at).date()
        ).days + 1
        if session.status == Status.REVIEW_OPEN:
            review_open = session.review_expires_at is None or (
                now < session.review_expires_at
            )
            if card.is_completed and review_open:
                return True
            continue
        if wall_day <= CYCLE_LENGTH_DAYS:
            if card.day_number == wall_day and not card.is_expired:
                return True
        elif card.is_completed:
            # The review opens on the next visit; bound it by the latest it
            # could close had it opened when the cycle ended.
            cycle_end = timezone.localtime(session.started_at).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=CYCLE_LENGTH_DAYS)
            if now < cycle_end + timedelta(hours=REVIEW_WINDOW_HOURS):
                return True
    return False


def _increment_rate_limit_counter(key, period_seconds):
    """Atomically increment a cache rate-limit counter.

    Uses cache.add() for the first request in a window and cache.incr() for
    subsequent requests, so concurrent workers cannot all read the same value
    and pass before their increments land. Returns the new counter value, or
    None if the cache backend is unavailable (rate limiting is fail-open, as
    before).
    """
    try:
        if cache.add(key, 1, period_seconds):
            return 1
        return cache.incr(key)
    except ValueError:
        # Key expired between add() and incr(); retry once.
        try:
            if cache.add(key, 1, period_seconds):
                return 1
            return cache.incr(key)
        except Exception:
            return None
    except Exception:
        return None


@login_required
def serve_profile_photo(request, user_id, photo_field):
    """
    Serve profile photos with authentication and privacy checks

    URL: /crush/media/profile/{user_id}/{photo_field}/
    Where photo_field is: photo_1, photo_2, or photo_3

    Rate limits: 200/min for regular users, 300/min for coaches.

    Args:
        user_id: ID of the profile owner
        photo_field: Which photo field (photo_1, photo_2, photo_3)

    Returns:
        Image file or 403/404 error
    """
    # Apply rate limit: higher for coaches who review many profiles
    # Regular users need ~50 requests just to load the attendees page (15+ photos)
    is_coach = CrushCoach.objects.filter(user=request.user, is_active=True).exists()
    max_requests = 300 if is_coach else 200
    period_seconds = 60  # 1 minute window

    # Per-user cap (primary). A single account cannot exceed max_requests/min.
    user_key = f"ratelimit:serve_profile_photo:user_{request.user.id}"
    # Per-IP cap (secondary, anti-abuse). Caps aggregate traffic from one IP
    # across multiple accounts — closes the "botnet of N accounts each get the
    # full quota" hole. Photos are PII under GDPR, so the IP ceiling is set
    # well above any single legitimate user's budget (200/min) but below
    # N * max_requests for attacker N.
    ip_addr = get_client_ip(request) or "unknown"
    ip_max_requests = 600 if is_coach else 400
    ip_key = f"ratelimit:serve_profile_photo:ip_{ip_addr}"

    user_current = _increment_rate_limit_counter(user_key, period_seconds)
    if user_current is not None and user_current > max_requests:
        logger.warning(
            "serve_profile_photo user rate-limited: user=%s ip=%s "
            "user_count=%s max=%s",
            request.user.id, ip_addr, user_current, max_requests,
        )
        return HttpResponse("Rate limit exceeded. Please try again later.", status=429)

    ip_current = _increment_rate_limit_counter(ip_key, period_seconds)
    if ip_current is not None and ip_current > ip_max_requests:
        logger.warning(
            "serve_profile_photo IP rate-limited: user=%s ip=%s "
            "ip_count=%s max=%s",
            request.user.id, ip_addr, ip_current, ip_max_requests,
        )
        return HttpResponse("Rate limit exceeded. Please try again later.", status=429)

    # Validate photo_field
    if photo_field not in ["photo_1", "photo_2", "photo_3"]:
        raise Http404("Invalid photo field")

    # Get the profile
    profile = get_object_or_404(CrushProfile, user_id=user_id)

    # Check permissions
    if not can_view_profile_photo(request.user, profile):
        logger.warning(
            f"User {request.user.id} denied access to {profile.user.id}'s {photo_field}"
        )
        raise PermissionDenied("You don't have permission to view this photo")

    # Get the photo field
    photo = getattr(profile, photo_field)
    if not photo:
        raise Http404("Photo not found")

    # AZURE BLOB STORAGE: Generate SAS URL and redirect
    if hasattr(settings, "AZURE_ACCOUNT_NAME") and settings.AZURE_ACCOUNT_NAME:
        from .storage import CrushProfilePhotoStorage

        storage = CrushProfilePhotoStorage()

        # Redirect to Azure with time-limited SAS token
        secure_url = storage.url(photo.name, expire=1800)  # 30 min expiry
        from django.shortcuts import redirect

        return redirect(secure_url)

    # LOCAL FILESYSTEM: Serve directly
    else:
        photo_path = photo.path

        # Check if file exists
        if not os.path.exists(photo_path):
            raise Http404("Photo file not found")

        # Serve original photo
        try:
            with open(photo_path, "rb") as f:
                content_type = "image/jpeg"
                if photo_path.lower().endswith(".png"):
                    content_type = "image/png"
                elif photo_path.lower().endswith(".webp"):
                    content_type = "image/webp"

                response = HttpResponse(f.read(), content_type=content_type)
                response["Content-Disposition"] = "inline"
                return response
        except Exception as e:
            logger.error(f"Error serving photo {photo_path}: {e}")
            raise Http404("Error loading photo")


@login_required
def serve_coach_photo(request, coach_id):
    """Serve a coach's directory photo to any authenticated member.

    Coach photos live in the same private container as profile photos but are
    meant to be shown in the member-facing premium coach directory, so the only
    gate is authentication.

    URL: /crush/media/coach/{coach_id}/
    """
    coach = get_object_or_404(CrushCoach, id=coach_id, is_active=True)
    photo = coach.photo
    if not photo:
        raise Http404("Photo not found")

    # AZURE BLOB STORAGE: redirect to a time-limited SAS URL.
    if hasattr(settings, "AZURE_ACCOUNT_NAME") and settings.AZURE_ACCOUNT_NAME:
        from django.shortcuts import redirect

        try:
            secure_url = photo.storage.url(photo.name, expire=1800)  # 30 min
        except TypeError:
            secure_url = photo.storage.url(photo.name)
        return redirect(secure_url)

    # LOCAL FILESYSTEM: serve directly.
    photo_path = photo.path
    if not os.path.exists(photo_path):
        raise Http404("Photo file not found")
    try:
        with open(photo_path, "rb") as f:
            content_type = "image/jpeg"
            if photo_path.lower().endswith(".png"):
                content_type = "image/png"
            elif photo_path.lower().endswith(".webp"):
                content_type = "image/webp"
            response = HttpResponse(f.read(), content_type=content_type)
            response["Content-Disposition"] = "inline"
            return response
    except Exception as e:
        logger.error(f"Error serving coach photo {photo_path}: {e}")
        raise Http404("Error loading photo")


def get_profile_photo_url(profile, photo_field, request=None):
    """
    Helper function to generate the correct photo URL

    Use this in templates and views instead of accessing photo.url directly

    Args:
        profile: CrushProfile instance
        photo_field: Which photo ('photo_1', 'photo_2', 'photo_3')
        request: Optional request object (for building absolute URLs)

    Returns:
        URL string to the photo (through the secure view)
    """
    from django.urls import reverse

    if not getattr(profile, photo_field):
        return None

    # Generate URL through the secure view
    url = reverse(
        "crush_lu:serve_profile_photo",
        kwargs={"user_id": profile.user.id, "photo_field": photo_field},
    )

    # Build absolute URL if request provided
    if request:
        return request.build_absolute_uri(url)

    return url
