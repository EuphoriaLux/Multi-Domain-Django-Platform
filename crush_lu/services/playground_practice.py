"""The practice member a coach plays on test.crush.lu (coach playground).

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

A coach's own account behaves like staff (active coaches get ``is_staff``),
which skips the member page gates, and six self-guards stop a coach acting on
themselves. So each coach gets a separate, ordinary member account, linked by
``PlaygroundAccount(kind="practice", owner=<coach user>)``.

Every practice member is built through the real services (photo review,
profile verification, the verification funnel), so when a gate changes, the
canary tests in ``test_playground_presets.py`` fail loudly instead of the
playground drifting silently.

A practice member is never deleted: "start again" retires it (releases its
open checks and appointments, dismisses reports, deactivates the account) and
builds a new one. ``User.delete()`` would hit PROTECT foreign keys and leave
photo blobs behind.
"""

import io
import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crush_lu.services.staging_playground import require_playground

logger = logging.getLogger(__name__)

EMAIL_DOMAIN = "crush.test"
USERNAME_PREFIX = "practice_"
#: Session key set (after login) when a coach switches into their practice member.
SWITCH_SESSION_KEY = "playground_switch"
SWITCH_MAX_AGE_SECONDS = 12 * 3600


class PlaygroundError(Exception):
    """A scenario could not be built; ``code`` names the gate that refused."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    luxid: bool = True
    method: str = "luxid"
    steps: tuple = ()
    verify: bool = True


#: Starting scenarios, in the order the coach guide lists the cases.
#: ``steps`` run after the base build, in order.
PRESETS = {
    preset.key: preset
    for preset in (
        Preset(
            "luxid_ready",
            _("LuxID linked, ready to request a video check"),
            _(
                "Verified with LuxID, photo approved. On the phone, request a "
                "video verification; then claim it in the channel."
            ),
        ),
        Preset(
            "video_requested",
            _("Video check requested"),
            _(
                'The member already pressed "Request a video verification". '
                "Start in the Verification Channel: claim, book, call, decide."
            ),
            steps=("request",),
        ),
        Preset(
            "call_booked",
            _("Video call booked in 3 minutes"),
            _(
                "Claimed by you and booked to start in 3 minutes, so the call "
                "buttons are already open. Practise the call, a no-show or a "
                "connection problem."
            ),
            steps=("request", "claim", "book"),
        ),
        Preset(
            "video_approved",
            _("Video verification approved"),
            _(
                "You already passed this member on video. See what Connect "
                "Week asks for next."
            ),
            steps=("request", "claim", "pass_video"),
        ),
        Preset(
            "video_without_luxid",
            _("Asks for video, no LuxID"),
            _(
                "Verified by Crush.lu but without LuxID. The video route asks "
                "them to link LuxID first, or to come to an event."
            ),
            luxid=False,
            method="admin",
        ),
        Preset(
            "old_voice_screening",
            _("Old voice screening only"),
            _(
                "Approved after an old voice screening call. That does not "
                "count as a video verification: they can still request one."
            ),
            method="legacy",
            steps=("voice_screening",),
        ),
        Preset(
            "photo_replaced",
            _("New photo after verification"),
            _(
                "Video-verified, then uploaded a new photo that waits for "
                "review. Others still see the approved photo."
            ),
            steps=("request", "claim", "pass_video", "replace_photo"),
        ),
        Preset(
            "premium",
            _("Premium member assigned to you"),
            _(
                "LuxID-verified with an active Premium membership and you as "
                "their coach. Other coaches cannot verify them."
            ),
            steps=("premium",),
        ),
        Preset(
            "event_verified_no_luxid",
            _("Event route, no LuxID"),
            _(
                "Verified by you at your practice mixer, without LuxID. The "
                "event route opens Connect Week; the Event Lobby still needs "
                "LuxID."
            ),
            luxid=False,
            method="coach_event",
            steps=("mixer_attended",),
        ),
        Preset(
            "connect_ready",
            _("Connect Week ready"),
            _(
                "Video-verified and Connect onboarding done. On the phone, open "
                "Connect Week; use the test member buttons below to play the "
                "other side."
            ),
            steps=("request", "claim", "pass_video", "connect_onboard"),
        ),
        Preset(
            "connect_onboarding_todo",
            _("Connect onboarding still to do"),
            _(
                "Video-verified, but Connect setup not started. Walk through the "
                "onboarding on the phone like a new member."
            ),
            steps=("request", "claim", "pass_video"),
        ),
        Preset(
            "event_ticket_pending",
            _("First-time guest with a ticket"),
            _(
                "Profile submitted, not verified, no LuxID, registered for your "
                "practice mixer. Scan the ticket at the door: the scan verifies "
                "them."
            ),
            luxid=False,
            method="",
            verify=False,
            steps=("mixer_ticket",),
        ),
        Preset(
            "event_lobby_ready",
            _("Event Lobby ready"),
            _(
                "Connect Week ready and registered for your practice mixer. Scan "
                "the ticket, fill the room, then open the Lobby on the phone."
            ),
            steps=(
                "request",
                "claim",
                "pass_video",
                "connect_onboard",
                "mixer_ticket",
            ),
        ),
    )
}
DEFAULT_PRESET = "luxid_ready"
GENDERS = ("F", "M")


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def live_practice_link(coach_user):
    from crush_lu.models import PlaygroundAccount

    return (
        PlaygroundAccount.objects.filter(
            kind=PlaygroundAccount.KIND_PRACTICE,
            owner=coach_user,
            retired_at__isnull=True,
        )
        .select_related("user")
        .first()
    )


def practice_link_for(user):
    """The live practice link if ``user`` is a practice member, else ``None``."""
    from crush_lu.models import PlaygroundAccount

    if not user or not user.is_authenticated:
        return None
    return (
        PlaygroundAccount.objects.filter(
            user=user,
            kind=PlaygroundAccount.KIND_PRACTICE,
            retired_at__isnull=True,
        )
        .select_related("owner")
        .first()
    )


def switch_marker(request):
    """The switch marker if it is valid for the signed-in practice member."""
    marker = request.session.get(SWITCH_SESSION_KEY)
    if not isinstance(marker, dict) or not request.user.is_authenticated:
        return None
    started = marker.get("at")
    if not isinstance(started, int) or not (
        0 <= time.time() - started <= SWITCH_MAX_AGE_SECONDS
    ):
        return None
    if marker.get("practice") != request.user.pk:
        return None
    return marker


def is_practice_user_id(user_id):
    from crush_lu.models import PlaygroundAccount

    return PlaygroundAccount.objects.filter(
        user_id=user_id, kind=PlaygroundAccount.KIND_PRACTICE
    ).exists()


# ---------------------------------------------------------------------------
# Start again
# ---------------------------------------------------------------------------


def lock_calendar(coach_id):
    """The coach's calendar lock (``verification_appointments.lock_calendar``)."""
    from crush_lu.services.verification_appointments import (
        lock_calendar as _lock_calendar,
    )

    _lock_calendar(coach_id)


def lock_coach_row(coach_id):
    """The claiming coach's row lock (``real_person_check._lock_coach``)."""
    from crush_lu.services.real_person_check import _lock_coach

    _lock_coach(coach_id)


def lock_owner(coach_user):
    """Lock the coach's user row for the rest of the transaction.

    The stable row that serialises every change to a coach's practice member:
    the practice row itself does not exist before the first build and is
    replaced on "start again", so it cannot be the lock. Lock order:
    owner user -> practice ``PlaygroundAccount`` -> member profile -> check.
    ``no_key`` (FOR NO KEY UPDATE) still conflicts with itself but not with
    the KEY SHARE that foreign-key inserts take on this user row.
    Full lock order: coach calendar (advisory) -> ``CrushCoach`` row ->
    owner user -> practice ``PlaygroundAccount`` -> member profile -> check.
    Every other path takes a subset in the same order (booking: calendar ->
    profile; claim: coach row -> profile -> check).
    """
    list(
        get_user_model()
        .objects.select_for_update(no_key=True)
        .filter(pk=coach_user.pk)
        .values_list("pk", flat=True)
    )


def retire_practice_member(coach_user):
    """Retire the coach's live practice member, if any. Returns the old user.

    Releases its open Connect Week check and appointments (which also frees
    the coach's claim count), its optional badge checks, dismisses open
    reports against it, and deactivates the account so the phone is signed
    out on its next request. Nothing is deleted.
    """
    from crush_lu.models import (
        ConnectRealPersonCheck,
        CrushCoach,
        CrushProfile,
        EventRegistration,
        PlaygroundAccount,
        ScreeningSlot,
        UserReport,
    )
    from crush_lu.services.connect_verification import invalidate_video_admissions
    from crush_lu.services.verification_funnel import cancel_verification

    require_playground()
    now = timezone.now()
    with transaction.atomic():
        # Same order as apply_preset: calendar, owner, then practice row. The
        # retire cancels appointments in the coach's calendar.
        coach_id = (
            CrushCoach.objects.filter(user=coach_user)
            .values_list("pk", flat=True)
            .first()
        )
        if coach_id is not None:
            lock_calendar(coach_id)
            lock_coach_row(coach_id)
        lock_owner(coach_user)
        link = (
            PlaygroundAccount.objects.select_for_update()
            .filter(
                kind=PlaygroundAccount.KIND_PRACTICE,
                owner=coach_user,
                retired_at__isnull=True,
            )
            .first()
        )
        if link is None:
            return None
        old = get_user_model().objects.get(pk=link.user_id)
        cancel_verification(old)
        profile_id = (
            CrushProfile.objects.filter(user=old).values_list("pk", flat=True).first()
        )
        if profile_id:
            invalidate_video_admissions(profile_id, now=now)
            ScreeningSlot.objects.filter(
                real_person_check__profile_id=profile_id, status="booked"
            ).update(status="cancelled", updated_at=now)
            ConnectRealPersonCheck.objects.filter(
                profile_id=profile_id, status="open"
            ).update(status="released", decided_at=now)
        UserReport.objects.filter(
            reported_user=old, status__in=("open", "reviewing")
        ).update(status="dismissed")
        # Give back the seat a ticket holds on a practice mixer.
        EventRegistration.objects.filter(
            user=old, status__in=("pending", "confirmed", "waitlist")
        ).update(status="cancelled")
        old.is_active = False
        old.save(update_fields=["is_active"])
        link.retired_at = now
        link.save(update_fields=["retired_at"])
    logger.info("Coach playground: practice member %s retired", old.pk)
    return old


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def apply_preset(coach, key, *, gender=None):
    """Retire the current practice member and build a new one in scenario ``key``.

    ``coach`` is the active ``CrushCoach``. The whole build is one
    transaction: if a gate refuses a step, nothing changes and
    ``PlaygroundError`` names the gate. Returns the new user.
    """
    from crush_lu.services.real_person_check import RealPersonCheckError

    require_playground()
    preset = PRESETS.get(key)
    if preset is None:
        raise PlaygroundError("unknown_scenario")
    gender = gender if gender in GENDERS else GENDERS[0]
    try:
        with transaction.atomic():
            # The coach's calendar first, as arrange_appointment() takes it
            # before any profile: retiring locks the old member's profile and
            # "call booked" books an appointment, so the reverse order could
            # deadlock with a booking for the old member.
            lock_calendar(coach.pk)
            # Then the coach row, as claim_video_request() locks it before any
            # profile and several scenarios claim after retiring.
            lock_coach_row(coach.pk)
            # Then the coach's own user row, which exists from the very first
            # build, so two presses (first creation or "start again") run one
            # after the other.
            lock_owner(coach.user)
            retire_practice_member(coach.user)
            user = _build_base(coach, preset, gender)
            check = None
            for step in preset.steps:
                check = _STEPS[step](coach, user, check)
    except RealPersonCheckError as exc:
        raise PlaygroundError(exc.code) from exc
    except IntegrityError as exc:
        # Two presses at once: the one-live-practice-member constraint lets
        # only one build through (PostgreSQL; SQLite serialises writes).
        raise PlaygroundError("try_again") from exc
    logger.info(
        "Coach playground: practice member %s built for coach %s (%s)",
        user.pk,
        coach.pk,
        key,
    )
    return user


def _next_index(coach_user):
    from crush_lu.models import PlaygroundAccount

    return (
        PlaygroundAccount.objects.filter(
            kind=PlaygroundAccount.KIND_PRACTICE, owner=coach_user
        ).count()
        + 1
    )


def _build_base(coach, preset, gender):
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    from crush_lu.models import CrushProfile, PlaygroundAccount, UserDataConsent
    from crush_lu.services import photo_review
    from crush_lu.services.profile_verification import claim_profile_verification

    User = get_user_model()
    coach_user = coach.user
    index = _next_index(coach_user)
    username = f"{USERNAME_PREFIX}{coach_user.pk}_{index}"
    while User.objects.filter(username=username).exists():
        index += 1
        username = f"{USERNAME_PREFIX}{coach_user.pk}_{index}"
    email = f"practice-{coach_user.pk}-{index}@{EMAIL_DOMAIN}"
    now = timezone.now()

    user = User(
        username=username,
        email=email,
        first_name=(coach_user.first_name or "Coach")[:150],
        last_name="Practice",
        last_login=now,
    )
    user.set_unusable_password()
    user.save()
    PlaygroundAccount.objects.create(
        user=user,
        kind=PlaygroundAccount.KIND_PRACTICE,
        owner=coach_user,
        preset=preset.key,
    )
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )

    coach_profile = CrushProfile.objects.filter(user=coach_user).first()
    language = (coach_profile.preferred_language if coach_profile else "") or "en"
    today = now.date()
    profile, _created = CrushProfile.objects.get_or_create(user=user)
    profile.date_of_birth = date(today.year - 30, 6, 15)
    profile.gender = gender
    profile.location = "Luxembourg"
    profile.bio = "Practice member on test.crush.lu. Nothing here is real."
    profile.preferred_language = language
    profile.is_active = True
    profile.verification_status = "pending"
    profile.completion_status = "submitted"
    # No phone number, ever: the coach pages would show tel:/sms: links. The
    # phone step counts as done (as for the quiz rehearsal guests) so the
    # member pages do not ask for an SMS the guard would refuse anyway.
    profile.phone_number = ""
    profile.phone_verified = True
    profile.phone_verified_at = now
    profile.photo_1.save(
        f"practice-{user.pk}.jpg", ContentFile(practice_photo_bytes(user)), save=False
    )
    profile.save()

    if preset.luxid:
        SocialAccount.objects.create(
            user=user, provider="luxid", uid=f"playground-{user.pk}"
        )

    profile.refresh_from_db()
    photo_review.submit_member_review(
        coach,
        profile.pk,
        [
            {
                "decision": "approved",
                "photo_field": "photo_1",
                "photo_key": profile.photo_1.name,
            }
        ],
    )
    profile.refresh_from_db()
    if preset.verify:
        claim_profile_verification(profile, method=preset.method, approved_at=now)
    return user


def practice_photo_bytes(user, *, variant=0):
    """A unique generated JPEG (never a shared key: unpublished blobs get
    deleted when a profile's photo changes)."""
    from PIL import Image, ImageDraw

    width, height = 600, 750
    palettes = (((155, 89, 182), (232, 67, 147)), ((74, 105, 189), (155, 89, 182)))
    top, bottom = palettes[variant % len(palettes)]
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
        )
    light = (245, 240, 250)
    draw.ellipse([210, 170, 390, 370], fill=light)
    draw.rounded_rectangle([130, 400, 470, 750], radius=150, fill=light)
    draw.text((24, 24), f"PRACTICE #{user.pk}", fill=light)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Scenario steps (each through the real service)
# ---------------------------------------------------------------------------


def _step_request(coach, user, check):
    from crush_lu.services.verification_funnel import request_verification

    return request_verification(user)


def _step_claim(coach, user, check):
    from crush_lu.services.verification_funnel import claim_video_request

    return claim_video_request(coach, check.pk)


def _step_book(coach, user, check):
    from crush_lu.services.verification_appointments import arrange_appointment

    start = (timezone.now() + timedelta(minutes=3)).replace(second=0, microsecond=0)
    arrange_appointment(coach, check.pk, start, 15)
    return check


def _step_pass_video(coach, user, check):
    from crush_lu.models import CrushProfile
    from crush_lu.real_person_check_rules import REQUIRED_CHECKLIST_KEYS
    from crush_lu.services.real_person_check import decide_check

    profile = CrushProfile.objects.get(user=user)
    decide_check(
        coach,
        check.pk,
        "pass_video",
        checklist={key: True for key in REQUIRED_CHECKLIST_KEYS["video"]},
        notes="Practice scenario on test.crush.lu.",
        photo_key=profile.photo_1.name,
        confirmed=True,
    )
    return check


def _step_replace_photo(coach, user, check):
    from crush_lu.models import CrushProfile

    profile = CrushProfile.objects.get(user=user)
    profile.photo_1.save(
        f"practice-{user.pk}-new.jpg",
        ContentFile(practice_photo_bytes(user, variant=1)),
        save=False,
    )
    profile.save()
    return check


def _step_voice_screening(coach, user, check):
    from crush_lu.models import CrushProfile, ProfileSubmission

    profile = CrushProfile.objects.get(user=user)
    now = timezone.now()
    ProfileSubmission.objects.create(
        profile=profile,
        coach=coach,
        status="approved",
        review_call_completed=True,
        review_call_date=now - timedelta(days=60),
        reviewed_at=now - timedelta(days=60),
    )
    return check


def _step_premium(coach, user, check):
    from crush_lu.models import CrushProfile, PremiumMembership

    now = timezone.now()
    CrushProfile.objects.filter(user=user).update(
        assigned_coach=coach, assigned_coach_at=now
    )
    PremiumMembership.objects.create(
        user=user,
        coach=coach,
        status="active",
        payment_confirmed=True,
        payment_date=now,
    )
    return check


def _step_connect_onboard(coach, user, check):
    from crush_lu.services.playground_cast import onboard_for_connect

    onboard_for_connect(user)
    return check


def _step_mixer_ticket(coach, user, check):
    from crush_lu.models import EventRegistration
    from crush_lu.services.playground_events import ensure_practice_mixer

    event = ensure_practice_mixer(coach.user)
    EventRegistration.objects.create(event=event, user=user, status="confirmed")
    return check


def _step_mixer_attended(coach, user, check):
    """Attended the coach's mixer, recorded as the coach's own door scan."""
    from crush_lu.models import EventRegistration
    from crush_lu.services.playground_events import ensure_practice_mixer

    event = ensure_practice_mixer(coach.user)
    now = timezone.now()
    registration = EventRegistration.objects.create(
        event=event, user=user, status="attended"
    )
    EventRegistration.objects.filter(pk=registration.pk).update(
        checkin_granted_coach=coach, checkin_granted_coach_at=now
    )
    return check


_STEPS = {
    "connect_onboard": _step_connect_onboard,
    "mixer_ticket": _step_mixer_ticket,
    "mixer_attended": _step_mixer_attended,
    "request": _step_request,
    "claim": _step_claim,
    "book": _step_book,
    "pass_video": _step_pass_video,
    "replace_photo": _step_replace_photo,
    "voice_screening": _step_voice_screening,
    "premium": _step_premium,
}


# ---------------------------------------------------------------------------
# Status for the playground page
# ---------------------------------------------------------------------------


def practice_status(user):
    """Plain facts the playground card shows (no gate is bypassed here)."""
    from crush_lu.connect_phase import cycle_access_open
    from crush_lu.models import ConnectRealPersonCheck, CrushProfile
    from crush_lu.services.connect_verification import human_verification_method
    from crush_lu.services.photo_publication import get_public_photo_key
    from crush_lu.services.verification_funnel import member_prerequisite

    profile = CrushProfile.objects.select_related("user").filter(user=user).first()
    if profile is None:
        return {}
    open_check = (
        ConnectRealPersonCheck.objects.filter(
            profile=profile, purpose="connect_week", status="open"
        )
        .select_related("coach__user")
        .first()
    )
    return {
        "luxid": profile.has_luxid_connected,
        "verified": profile.verification_status == "verified",
        "method": profile.verification_method,
        "photo_public": bool(profile.photo_1)
        and get_public_photo_key(profile, "photo_1") == profile.photo_1.name,
        "photo_in_review": bool(profile.photo_1)
        and get_public_photo_key(profile, "photo_1") != profile.photo_1.name,
        "prerequisite": member_prerequisite(profile),
        "human_verification": human_verification_method(user),
        "open_check": open_check,
        "connect_week_open": cycle_access_open(user),
    }


def annotate_practice_items(items, coach_user, *, show_all=False):
    """Mark practice members in a channel list (staging only).

    Each item gets ``practice_owner_name`` and ``is_my_practice``. Other
    coaches' practice members are dropped unless ``show_all`` (pair practice),
    so nobody claims a colleague's practice member for 72 hours by accident.
    The coach's own practice member is moved to the top.
    """
    from crush_lu.models import PlaygroundAccount

    user_ids = {item.profile.user_id for item in items}
    if not user_ids:
        return list(items)
    owners_by_user = dict(
        PlaygroundAccount.objects.filter(
            kind=PlaygroundAccount.KIND_PRACTICE, user_id__in=user_ids
        ).values_list("user_id", "owner_id")
    )
    names = dict(
        get_user_model()
        .objects.filter(pk__in=set(owners_by_user.values()))
        .values_list("pk", "first_name")
    )
    kept = []
    for item in items:
        owner_id = owners_by_user.get(item.profile.user_id)
        item.practice_owner_name = ""
        item.is_my_practice = False
        if owner_id is not None:
            item.practice_owner_name = names.get(owner_id) or "?"
            item.is_my_practice = owner_id == coach_user.pk
            if not item.is_my_practice and not show_all:
                continue
        kept.append(item)
    kept.sort(key=lambda item: not item.is_my_practice)
    return kept
