"""The playground cast: synthetic members that answer when a coach asks.

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

Connect Week has nobody on the other side on test.crush.lu: the old seed
commands make accounts that cannot log in or never show up. The cast is a
small set of ``cast-<key>@crush.test`` members built through the real
services (photo review, verification, Connect onboarding), with no phone
number, no Premium, no coach and no tester flag.

A cast member never acts on its own. A coach presses a button on the
playground ("Lea sends you a request", "Lea accepts", "skip to review day"),
and the button calls the same ``connect_cycle`` functions a member's click
would. When a Connect rule changes, these calls fail loudly in the tests.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from crush_lu.services.playground_practice import PlaygroundError
from crush_lu.services.staging_playground import require_playground

logger = logging.getLogger(__name__)

EMAIL_DOMAIN = "crush.test"
USERNAME_PREFIX = "cast_"
ACTIVITY_CACHE_KEY = "playground:cast_activity_refreshed"
ACTIVITY_REFRESH_SECONDS = 3600

CANNED_REPLIES = (
    "Hi! Nice to meet you here. How is your week going?",
    "Ha, that made me smile. Coffee in Grund some time?",
    "I'd like that. Shall we pick a day this week?",
)


@dataclass(frozen=True)
class Persona:
    key: str
    first_name: str
    last_name: str
    gender: str
    age: int
    location: str
    bio: str
    photo_url: str = ""
    luxid: bool = True


def personas():
    """12 personas: 6 women and 5 men from the staging persona set (each with
    its own photo URL), plus Sam, who has no LuxID: the My Crush counterpart.
    """
    from crush_lu.management.commands.enrich_staging_candidates import (
        CURATED_FEMALE_CANDIDATES,
        CURATED_MALE_CANDIDATES,
    )

    chosen = []
    used_urls = set()
    for source, gender, wanted in (
        (CURATED_FEMALE_CANDIDATES, "F", 6),
        (CURATED_MALE_CANDIDATES, "M", 5),
    ):
        taken = 0
        for raw in source:
            if taken == wanted:
                break
            url = next((u for u in raw.get("photos", []) if u not in used_urls), "")
            if not url:
                continue
            used_urls.add(url)
            chosen.append(
                Persona(
                    key=raw["first_name"].lower(),
                    first_name=raw["first_name"],
                    last_name=raw["last_name"],
                    gender=gender,
                    age=raw["age"],
                    location=raw.get("location", "Luxembourg"),
                    bio=raw.get("bio", ""),
                    photo_url=url,
                )
            )
            taken += 1
    chosen.append(
        Persona(
            key="sam",
            first_name="Sam",
            last_name="Weber",
            gender="M",
            age=31,
            location="Esch-sur-Alzette",
            bio="Teacher, trail runner, terrible at karaoke. Here for the events.",
            luxid=False,
        )
    )
    return chosen


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_cast(coach, *, download=True, log=None):
    """Create the missing cast members; refresh the ones that exist.

    ``coach`` (an active ``CrushCoach``) approves the cast photos, as a coach
    would. ``download=False`` uses generated images (tests, no network).
    Returns ``(created, refreshed)``.
    """
    require_playground()
    created = refreshed = 0
    for persona in personas():
        user, was_created = ensure_cast_member(coach, persona, download=download)
        if was_created:
            created += 1
        else:
            refreshed += 1
        if log:
            log(f"{'created' if was_created else 'kept'} {user.username}")
    return created, refreshed


def ensure_cast_member(coach, persona, *, download=True):
    from crush_lu.models import PlaygroundAccount

    User = get_user_model()
    username = f"{USERNAME_PREFIX}{persona.key}"
    existing = User.objects.filter(
        username=username, playground_account__kind=PlaygroundAccount.KIND_CAST
    ).first()
    if existing is not None:
        User.objects.filter(pk=existing.pk).update(
            last_login=timezone.now(), is_active=True
        )
        if persona.luxid:
            _ensure_connect_membership(existing)
        return existing, False
    if User.objects.filter(username=username).exists():
        raise PlaygroundError("cast_username_taken")
    with transaction.atomic():
        user = _build_cast_member(coach, persona, download=download)
    return user, True


def _cast_photo_bytes(persona, download):
    from crush_lu.management.commands.enrich_staging_candidates import (
        _fetch_or_fallback_image,
    )
    from crush_lu.services.playground_practice import practice_photo_bytes

    if download and persona.photo_url:
        return _fetch_or_fallback_image(persona.photo_url, persona.first_name)
    placeholder = type("Placeholder", (), {"pk": persona.key})()
    return practice_photo_bytes(placeholder, variant=1)


def _build_cast_member(coach, persona, *, download):
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    from crush_lu.models import CrushProfile, PlaygroundAccount, UserDataConsent
    from crush_lu.services import photo_review
    from crush_lu.services.profile_verification import claim_profile_verification

    User = get_user_model()
    now = timezone.now()
    email = f"cast-{persona.key}@{EMAIL_DOMAIN}"
    user = User(
        username=f"{USERNAME_PREFIX}{persona.key}",
        email=email,
        first_name=persona.first_name,
        last_name=persona.last_name,
        last_login=now,
    )
    user.set_unusable_password()
    user.save()
    PlaygroundAccount.objects.create(
        user=user, kind=PlaygroundAccount.KIND_CAST, persona_key=persona.key
    )
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )

    today = now.date()
    profile, _created = CrushProfile.objects.get_or_create(user=user)
    profile.date_of_birth = date(today.year - persona.age, 3, 10)
    profile.gender = persona.gender
    profile.location = persona.location
    profile.bio = persona.bio
    profile.preferred_language = "en"
    profile.is_active = True
    profile.verification_status = "pending"
    profile.completion_status = "submitted"
    profile.phone_number = ""
    profile.phone_verified = True
    profile.phone_verified_at = now
    profile.photo_1.save(
        f"cast-{persona.key}.jpg",
        ContentFile(_cast_photo_bytes(persona, download)),
        save=False,
    )
    profile.save()

    if persona.luxid:
        SocialAccount.objects.create(
            user=user, provider="luxid", uid=f"playground-cast-{persona.key}"
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
    claim_profile_verification(
        profile, method="luxid" if persona.luxid else "admin", approved_at=now
    )
    if persona.luxid:
        _ensure_connect_membership(user)
    return user


def _ensure_connect_membership(user):
    """Onboarded, open preferences, sharing consent and 3 gate questions."""
    from crush_lu.models import CrushConnectMembership, MemberGateQuestion
    from crush_lu.services.crush_connect import (
        GATE_QUESTION_COUNT,
        get_or_create_question_week,
    )

    now = timezone.now()
    membership, _created = CrushConnectMembership.objects.get_or_create(user=user)
    changed = []
    for field, value in (
        ("onboarded_at", membership.onboarded_at or now),
        ("photo_share_consent", True),
        ("preferred_genders", []),
        ("preferred_age_min", 18),
        ("preferred_age_max", 99),
        ("paused_at", None),
    ):
        if getattr(membership, field) != value:
            setattr(membership, field, value)
            changed.append(field)
    if changed:
        membership.save(update_fields=changed)

    if membership.gate_questions.count() >= GATE_QUESTION_COUNT:
        return membership
    week = get_or_create_question_week()
    questions = list(week.questions.filter(is_active=True)[:GATE_QUESTION_COUNT])
    membership.gate_questions.all().delete()
    for position, question in enumerate(questions, start=1):
        MemberGateQuestion.objects.create(
            membership=membership,
            question=question,
            position=position,
            owner_answer=position % 2 == 1,
            picked_week=week,
        )
    return membership


def onboard_for_connect(user):
    """Connect onboarding done, for a practice member (``connect_ready``)."""
    return _ensure_connect_membership(user)


# ---------------------------------------------------------------------------
# Keeping the cast eligible
# ---------------------------------------------------------------------------


def cast_queryset():
    from crush_lu.models import PlaygroundAccount

    return (
        get_user_model()
        .objects.filter(
            playground_account__kind=PlaygroundAccount.KIND_CAST, is_active=True
        )
        .order_by("first_name")
    )


def refresh_cast_activity(force=False):
    """Keep the cast inside Connect's 30-day activity window (at most hourly)."""
    if not force and not cache.add(
        ACTIVITY_CACHE_KEY, True, timeout=ACTIVITY_REFRESH_SECONDS
    ):
        return 0
    return cast_queryset().update(last_login=timezone.now())


def cast_member(key):
    member = cast_queryset().filter(username=f"{USERNAME_PREFIX}{key}").first()
    if member is None:
        raise PlaygroundError("unknown_cast_member")
    return member


# ---------------------------------------------------------------------------
# Connect buttons (each through the real connect_cycle service)
# ---------------------------------------------------------------------------


def cast_sends_request(practice_user, cast_user):
    """``cast_user`` meets the practice member on a card and sends a request."""
    from crush_lu.models.crush_connect_cycle import ConnectCycleCard, ConnectWeekSession
    from crush_lu.services.connect_cycle import (
        can_send_weekly_request,
        record_card_answer,
        send_weekly_request,
    )
    from crush_lu.services.crush_connect import owner_gate_truths

    require_playground()
    Status = ConnectWeekSession.Status
    now = timezone.now()
    with transaction.atomic():
        # A cast member is shared by every coach, so each press gets a fresh
        # session (one request per session is the real rule).
        ConnectWeekSession.objects.filter(
            user=cast_user, status__in=(Status.ACTIVE, Status.REVIEW_OPEN)
        ).update(status=Status.COMPLETED, is_review_open=False, completed_at=now)
        session = ConnectWeekSession.objects.create(user=cast_user)
        card = ConnectCycleCard.objects.create(
            session=session,
            day_number=1,
            card_index=1,
            target_user=practice_user,
            generated_date=timezone.localdate(),
        )
        record_card_answer(card, owner_gate_truths(practice_user))
        session.open_weekly_review()
        allowed, reason = can_send_weekly_request(session, cast_user, practice_user)
        if not allowed:
            raise PlaygroundError(reason)
        try:
            return send_weekly_request(session, cast_user, practice_user)
        except ValueError as exc:
            raise PlaygroundError(str(exc)) from exc


def cast_answers_request(practice_user, cast_user, accept):
    """``cast_user`` accepts or declines the practice member's pending request."""
    from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest
    from crush_lu.services.connect_cycle import respond_to_weekly_request

    require_playground()
    pending = ConnectWeeklyRequest.objects.filter(
        requester=practice_user,
        recipient=cast_user,
        status=ConnectWeeklyRequest.Status.PENDING,
    ).first()
    if pending is None:
        raise PlaygroundError("no_pending_request")
    try:
        return respond_to_weekly_request(pending, accept)
    except ValueError as exc:
        raise PlaygroundError(str(exc)) from exc


def cast_replies(practice_user, cast_user):
    """``cast_user`` writes a canned line in their open chat with the member."""
    from crush_lu.models.crush_connect_cycle import ConnectTemporaryChat
    from crush_lu.services.connect_chat import send_message

    require_playground()
    chat = (
        ConnectTemporaryChat.objects.filter(
            Q(participant_1=practice_user, participant_2=cast_user)
            | Q(participant_1=cast_user, participant_2=practice_user)
        )
        .order_by("-pk")
        .first()
    )
    if chat is None:
        raise PlaygroundError("no_chat")
    sent = chat.messages.filter(sender=cast_user).count()
    try:
        return send_message(chat, cast_user, CANNED_REPLIES[sent % len(CANNED_REPLIES)])
    except ValueError as exc:
        raise PlaygroundError(str(exc)) from exc


def practice_session(practice_user):
    from crush_lu.models.crush_connect_cycle import ConnectWeekSession

    Status = ConnectWeekSession.Status
    return (
        ConnectWeekSession.objects.filter(
            user=practice_user, status__in=(Status.ACTIVE, Status.REVIEW_OPEN)
        )
        .order_by("-started_at")
        .first()
    )


def skip_days(practice_user, days):
    """Move the practice member's Connect Week ``days`` days forward."""
    from crush_lu.models.crush_connect_cycle import ConnectWeekSession
    from crush_lu.services.connect_cycle import sync_session_state

    require_playground()
    session = practice_session(practice_user)
    if session is None:
        raise PlaygroundError("no_connect_week")
    ConnectWeekSession.objects.filter(pk=session.pk).update(
        started_at=F("started_at") - timedelta(days=days)
    )
    session.refresh_from_db()
    return sync_session_state(session)


def jump_to_review(practice_user):
    from crush_lu.services.connect_cycle import CYCLE_LENGTH_DAYS

    session = practice_session(practice_user)
    if session is None:
        raise PlaygroundError("no_connect_week")
    start = timezone.localtime(session.started_at).date()
    elapsed = (timezone.localdate() - start).days
    return skip_days(practice_user, max(1, CYCLE_LENGTH_DAYS - elapsed))


def let_request_expire(practice_user, cast_user):
    """A pending request between the two lapses now (both directions)."""
    from crush_lu.models.crush_connect_cycle import ConnectWeeklyRequest
    from crush_lu.services.connect_cycle import sync_request_state

    require_playground()
    pending = (
        ConnectWeeklyRequest.objects.filter(
            Q(requester=practice_user, recipient=cast_user)
            | Q(requester=cast_user, recipient=practice_user),
            status=ConnectWeeklyRequest.Status.PENDING,
        )
        .order_by("-sent_at")
        .first()
    )
    if pending is None:
        raise PlaygroundError("no_pending_request")
    ConnectWeeklyRequest.objects.filter(pk=pending.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    pending.refresh_from_db()
    return sync_request_state(pending)


def reset_connect_history(practice_user):
    """Forget every Connect row that involves the practice member, so the
    cast can meet them again. Other members' history is untouched."""
    from crush_lu.models.crush_connect_cycle import (
        ConnectCycleCard,
        ConnectPairExclusion,
        ConnectWeeklyRequest,
        ConnectWeekSession,
    )

    require_playground()
    with transaction.atomic():
        ConnectWeeklyRequest.objects.filter(
            Q(requester=practice_user) | Q(recipient=practice_user)
        ).delete()
        ConnectCycleCard.objects.filter(target_user=practice_user).delete()
        ConnectWeekSession.objects.filter(user=practice_user).delete()
        ConnectPairExclusion.objects.filter(
            Q(user_a=practice_user) | Q(user_b=practice_user)
        ).delete()
