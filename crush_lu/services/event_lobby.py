"""
Event Lobby service layer — eligibility, phase math, roster shaping, and the
irrevocable meet-signal loop.

Spec: docs/superpowers/specs/2026-07-17-crush-connect-event-lobby-design.md
(§5 eligibility, §6 time/state model, §7.2–7.4 live lobby, §9–10 domain model
and service boundaries, §13 privacy invariants).

Every rule lives here — not in templates, consumers, or the check-in endpoint
(§10). All functions re-derive phase and eligibility from server time and
current rows; broadcasts are hints, never the source of truth.

Identity shaping contract (§13): functions that feed client-visible payloads
return opaque participation handles and — only for an authorized mutual pair —
the first name. They never return durable user ids, usernames, or profile
fields for pre-mutual participants.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from .blocking import blocked_user_ids, is_blocked_pair

logger = logging.getLogger(__name__)

# §2/§6: the recap opens at the exact scheduled end and closes exactly 48h later.
RECAP_WINDOW_HOURS = 48

PHASE_LIVE = "live"
PHASE_RECAP = "recap"
PHASE_CLOSED = "closed"

# participant_gate denial reasons (service codes, never user-facing copy)
GATE_OK = "ok"
GATE_NO_MEMBERSHIP = "no_membership"
GATE_NOT_ONBOARDED = "not_onboarded"
GATE_EXCLUDED = "excluded"
GATE_NOT_VERIFIED = "profile_not_verified"
GATE_NO_LUXID = "no_luxid"
GATE_NO_PHOTO_CONSENT = "no_photo_consent"
GATE_NO_PHOTO = "no_photo"


def lobby_feature_enabled() -> bool:
    """§17 Phase A: one global rollout flag (never a per-event switch), on top
    of the Crush Connect launch-phase policy (§5.1 item 10)."""
    from crush_lu.connect_phase import candidate_access_open

    return bool(
        getattr(settings, "CRUSH_EVENT_LOBBY_ENABLED", False)
        and candidate_access_open()
    )


def event_lobby_phase(event, now=None) -> str:
    """Derive the lobby phase from server time (§6 — derived, never stored).

    ``live``   : until the exact scheduled end (``date_time + duration``).
                 Live access before the official start is fine — check-in
                 already proves the member is at the venue.
    ``recap``  : from the exact end until exactly 48 hours later.
    ``closed`` : afterwards, and always for cancelled/unpublished events (§16).
    """
    now = now or timezone.now()
    if event.is_cancelled or not event.is_published:
        return PHASE_CLOSED
    end = event.end_time
    if now < end:
        return PHASE_LIVE
    if now < end + timedelta(hours=RECAP_WINDOW_HOURS):
        return PHASE_RECAP
    return PHASE_CLOSED


def participant_gate(user) -> tuple[bool, str]:
    """§5.1 conditions 4–9: is this user an active, lobby-capable Crush
    Connect member *right now*? (Conditions 1–3 — auth, attendance, event
    state — are per-request/per-event and checked by the callers.)

    Premium, an assigned coach, and dating preferences are deliberately NOT
    part of this gate (§5.1).
    """
    membership = getattr(user, "crush_connect_membership", None)
    if membership is None:
        return False, GATE_NO_MEMBERSHIP
    if membership.onboarded_at is None:
        return False, GATE_NOT_ONBOARDED
    if membership.excluded_by_coach:
        return False, GATE_EXCLUDED

    profile = getattr(user, "crushprofile", None)
    if profile is None or profile.verification_status != "verified":
        return False, GATE_NOT_VERIFIED
    if not profile.has_luxid_connected:
        return False, GATE_NO_LUXID

    # PROTOTYPE-STUB: §5.1 requires a *versioned* Connect consent that
    # explicitly covers clear-photo sharing with checked-in members in an
    # Event Lobby. The prototype reuses the existing catalogue
    # ``photo_share_consent``; a real implementation adds a consent version
    # field + one-time re-acknowledgement flow before first lobby entry.
    if not membership.photo_share_consent:
        return False, GATE_NO_PHOTO_CONSENT
    if not profile.photo_1:
        return False, GATE_NO_PHOTO
    return True, GATE_OK


def evaluate_participation(registration, source="checkin", now=None):
    """Idempotently create lobby participation for an attended registration
    (§5.3, §10 integration points 1–2).

    Returns ``(participation, created)``; ``(None, False)`` when the guest is
    not (or no longer) eligible. Never raises for business reasons — callers
    in the check-in path must never fail a valid check-in because of the
    lobby (§19).
    """
    from crush_lu.models import EventLobbyParticipation

    now = now or timezone.now()
    event = registration.event

    if not lobby_feature_enabled():
        return None, False
    if registration.status != "attended":
        return None, False
    if event.is_cancelled or not event.is_published:
        return None, False
    # §5.3: finishing onboarding (or scanning in) after the exact scheduled
    # end never grants access to that event's lobby or recap.
    if now >= event.end_time:
        return None, False
    ok, _reason = participant_gate(registration.user)
    if not ok:
        return None, False

    with transaction.atomic():
        participation, created = EventLobbyParticipation.objects.get_or_create(
            event_registration=registration,
            defaults={
                "event": event,
                "user": registration.user,
                "joined_at": now,
                "eligibility_source": source,
            },
        )
    return participation, created


def handle_checkin(registration):
    """Integration point for ``views_checkin.event_checkin_api`` (§10.1)."""
    return evaluate_participation(registration, source="checkin")


def handle_onboarding_completed(user, now=None):
    """Integration point for Connect onboarding completion (§10.2): join every
    currently-attended, not-yet-ended event idempotently. Returns the list of
    newly created participations."""
    from crush_lu.models import EventRegistration

    now = now or timezone.now()
    created_participations = []
    # Generous DB cutoff, exact end computed in Python (end_time is a property;
    # mirrors the context_processors idiom for SQLite compatibility).
    candidates = EventRegistration.objects.filter(
        user=user,
        status="attended",
        event__is_published=True,
        event__is_cancelled=False,
        event__date_time__gte=now - timedelta(days=7),
    ).select_related("event")
    for registration in candidates:
        if now >= registration.event.end_time:
            continue
        participation, created = evaluate_participation(
            registration, source="onboarding_completed", now=now
        )
        if created:
            created_participations.append(participation)
    return created_participations


def eligible_participations(event):
    """Participations whose member passes the §5.1 gate *at read time* (§5.2:
    loss of eligibility takes effect when rendering, not only at creation)."""
    from crush_lu.models import CrushProfile, EventLobbyParticipation

    luxid_native_subq, luxid_oidc_subq = CrushProfile.luxid_account_querysets(
        OuterRef("user_id")
    )
    return (
        EventLobbyParticipation.objects.filter(
            event=event,
            user__crush_connect_membership__onboarded_at__isnull=False,
            user__crush_connect_membership__excluded_by_coach=False,
            user__crush_connect_membership__photo_share_consent=True,
            user__crushprofile__verification_status="verified",
        )
        .exclude(user__crushprofile__photo_1="")
        .exclude(user__crushprofile__photo_1__isnull=True)
        .annotate(
            _has_luxid_native=Exists(luxid_native_subq),
            _has_luxid_oidc=Exists(luxid_oidc_subq),
        )
        .filter(Q(_has_luxid_native=True) | Q(_has_luxid_oidc=True))
        .select_related("user", "user__crushprofile")
    )


def viewer_participation(user, event):
    """The viewer's own participation, or None. Re-checks the member gate so a
    stale session loses roster access the moment eligibility is lost (§5.2)."""
    from crush_lu.models import EventLobbyParticipation

    if not user.is_authenticated or not lobby_feature_enabled():
        return None
    ok, _reason = participant_gate(user)
    if not ok:
        return None
    return EventLobbyParticipation.objects.filter(event=event, user=user).first()


def _mutual_user_ids(user, event) -> set[int]:
    """User ids with an authorized mutual reveal with ``user`` for this event."""
    from crush_lu.models import EventMeetSignal

    return set(
        EventMeetSignal.objects.filter(
            event=event, sender=user, mutual_revealed_at__isnull=False
        ).values_list("recipient_id", flat=True)
    )


def _photo_url(event, handle) -> str:
    """Roster-authorized, handle-addressed photo route (§7.2 / §13 — never the
    durable user-id route used elsewhere)."""
    return reverse(
        "crush_lu:event_lobby_photo",
        kwargs={"event_id": event.pk, "handle": handle},
    )


def get_roster(viewer, event) -> list[dict]:
    """The live photo grid (§7.2): everyone eligible except self and blocked
    pairs, newest joiner first, photo-only until the pair is mutual.

    Each entry carries ONLY: the opaque handle, the authorized photo URL, the
    mutual flag — plus the first name for authorized mutual pairs. No name,
    user id, or profile field for anyone else (§13).
    """
    blocked = blocked_user_ids(viewer)
    mutual_ids = _mutual_user_ids(viewer, event)
    roster = []
    qs = eligible_participations(event).exclude(user=viewer).order_by("-joined_at")
    for participation in qs:
        if participation.user_id in blocked:
            continue
        # PROTOTYPE-STUB: §7.3 step 2 — once ConfirmedEncounter exists, pairs
        # already in "People I've Met" render a non-actionable "You've already
        # met" tile here instead of a signal target.
        entry = {
            "handle": participation.handle,
            "photo_url": _photo_url(event, participation.handle),
            "is_mutual": participation.user_id in mutual_ids,
        }
        if entry["is_mutual"]:
            entry["first_name"] = participation.user.first_name
        roster.append(entry)
    return roster


def get_mutuals(viewer, event) -> list[dict]:
    """The retrievable "Say hello" area (§7.4): every authorized mutual reveal
    for this event, newest first, excluding blocked pairs."""
    from crush_lu.models import EventMeetSignal

    blocked = blocked_user_ids(viewer)
    participations_by_user = {
        p.user_id: p
        for p in eligible_participations(event).exclude(user=viewer)
    }
    mutuals = []
    signal_qs = (
        EventMeetSignal.objects.filter(
            event=event, sender=viewer, mutual_revealed_at__isnull=False
        )
        .select_related("recipient")
        .order_by("-mutual_revealed_at")
    )
    for signal in signal_qs:
        if signal.recipient_id in blocked:
            continue
        participation = participations_by_user.get(signal.recipient_id)
        if participation is None:
            # Counterpart lost eligibility — hide the reveal at read time (§5.2).
            continue
        mutuals.append(
            {
                "handle": participation.handle,
                "first_name": signal.recipient.first_name,
                "photo_url": _photo_url(event, participation.handle),
                "revealed_at": signal.mutual_revealed_at.isoformat(),
            }
        )
    return mutuals


def signals_remaining(user, event) -> int:
    from crush_lu.models import EventMeetSignal

    used = EventMeetSignal.objects.filter(event=event, sender=user).count()
    return max(0, EventMeetSignal.MAX_SIGNALS_PER_EVENT - used)


def incoming_signal_count(user, event) -> int:
    """The exact anonymous counter (§7.4): one-sided incoming signals from
    senders who are still eligible and not block-related (§18: anonymous
    counters exclude blocked/ineligible senders). Mutual signals are no longer
    anonymous — they surface as reveals instead."""
    from crush_lu.models import EventMeetSignal

    blocked = blocked_user_ids(user)
    eligible_sender_ids = eligible_participations(event).values("user_id")
    qs = EventMeetSignal.objects.filter(
        event=event,
        recipient=user,
        mutual_revealed_at__isnull=True,
        sender_id__in=eligible_sender_ids,
    )
    if blocked:
        qs = qs.exclude(sender_id__in=blocked)
    return qs.count()


def lobby_state(user, event, now=None) -> dict:
    """Own-state payload for the header + pollers (§11: phase, countdown, own
    quotas/counters — no roster identity)."""
    now = now or timezone.now()
    phase = event_lobby_phase(event, now)
    end = event.end_time
    return {
        "phase": phase,
        "event_end_at": end.isoformat(),
        "recap_closes_at": (end + timedelta(hours=RECAP_WINDOW_HOURS)).isoformat(),
        "seconds_to_end": max(0, int((end - now).total_seconds())),
        "signals_remaining": signals_remaining(user, event),
        "signals_total": _max_signals(),
        "incoming_count": incoming_signal_count(user, event),
    }


def _max_signals() -> int:
    from crush_lu.models import EventMeetSignal

    return EventMeetSignal.MAX_SIGNALS_PER_EVENT


def send_meet_signal(sender, event, target_handle, now=None) -> dict:
    """One irreversible, anonymous live meet signal (§7.3, §9.2, §13).

    Returns a dict whose ``result`` is one of:

    - ``sent``              — neutral one-sided signal created
    - ``mutual``            — reverse signal existed; pair revealed atomically
    - ``duplicate``         — sender already signalled this member (idempotent,
                              consumes nothing; re-reports ``mutual`` when the
                              pair is already revealed)
    - ``quota_exhausted``   — three distinct recipients already used
    - ``phase_closed``      — live phase over / event not live (§7.6)
    - ``feature_disabled``  — rollout flag or launch phase off
    - ``not_participant``   — sender has no participation or lost eligibility
    - ``unknown_participant`` — handle not in this event's eligible roster
                              (also covers blocked pairs, §8.2 — a block must
                              not be probeable)

    ``mutual`` results include the pair's reveal payload for the SENDER only;
    the recipient's private notification is derived server-side by the caller
    from ``recipient_user_id`` (which must never reach any client payload for
    a non-mutual result, §13).
    """
    from crush_lu.models import EventLobbyParticipation, EventMeetSignal

    now = now or timezone.now()

    if not lobby_feature_enabled():
        return {"result": "feature_disabled"}
    if event_lobby_phase(event, now) != PHASE_LIVE:
        return {"result": "phase_closed"}

    ok, _reason = participant_gate(sender)
    if not ok:
        return {"result": "not_participant"}

    # Resolve the opaque handle within THIS event only — handles cannot be
    # replayed across events (§13). Unknown, self, ineligible, and blocked all
    # collapse into one indistinguishable answer.
    target = (
        eligible_participations(event)
        .filter(handle=target_handle)
        .exclude(user=sender)
        .first()
    )
    if target is None or is_blocked_pair(sender, target.user):
        return {"result": "unknown_participant"}
    recipient = target.user

    with transaction.atomic():
        # Lock BOTH participation rows in pk order (§13: row locks — the same
        # pair of locks serialises A→B and B→A, so two simultaneous signals
        # produce exactly one transactional mutual reveal, §16), and the
        # sender's lock alone serialises the three-signal quota across tabs.
        locked = list(
            EventLobbyParticipation.objects.select_for_update()
            .filter(event=event, user__in=[sender.pk, recipient.pk])
            .order_by("pk")
        )
        locked_user_ids = {p.user_id for p in locked}
        if sender.pk not in locked_user_ids:
            return {"result": "not_participant"}
        if recipient.pk not in locked_user_ids:
            return {"result": "unknown_participant"}

        # §6: compare server time inside the same transaction as the write.
        if timezone.now() >= event.end_time:
            return {"result": "phase_closed"}

        existing = EventMeetSignal.objects.filter(
            event=event, sender=sender, recipient=recipient
        ).first()
        if existing is not None:
            # §7.3: duplicate taps are idempotent and never consume attempts.
            if existing.is_mutual:
                return {
                    "result": "mutual",
                    "already": True,
                    "handle": target.handle,
                    "first_name": recipient.first_name,
                    "photo_url": _photo_url(event, target.handle),
                    "signals_remaining": signals_remaining(sender, event),
                }
            return {
                "result": "duplicate",
                "signals_remaining": signals_remaining(sender, event),
            }

        used = EventMeetSignal.objects.filter(event=event, sender=sender).count()
        if used >= EventMeetSignal.MAX_SIGNALS_PER_EVENT:
            return {"result": "quota_exhausted", "signals_remaining": 0}

        signal = EventMeetSignal.objects.create(
            event=event, sender=sender, recipient=recipient
        )

        reverse_signal = EventMeetSignal.objects.filter(
            event=event, sender=recipient, recipient=sender
        ).first()
        remaining = EventMeetSignal.MAX_SIGNALS_PER_EVENT - used - 1
        if reverse_signal is not None:
            revealed_at = timezone.now()
            EventMeetSignal.objects.filter(
                pk__in=[signal.pk, reverse_signal.pk]
            ).update(mutual_revealed_at=revealed_at)
            logger.info(
                "Lobby mutual reveal for event %s (pair of signals %s/%s)",
                event.pk,
                signal.pk,
                reverse_signal.pk,
            )
            return {
                "result": "mutual",
                "already": False,
                "handle": target.handle,
                "first_name": recipient.first_name,
                "photo_url": _photo_url(event, target.handle),
                "signals_remaining": remaining,
                "recipient_user_id": recipient.pk,
            }

    logger.info("Lobby signal sent for event %s (signal %s)", event.pk, signal.pk)
    return {
        "result": "sent",
        "signals_remaining": remaining,
        "recipient_user_id": recipient.pk,
    }


def get_active_live_lobby(user, now=None):
    """The user's participation in a currently-live lobby, for the hub card
    (§2 Navigation / §10.4). None when the feature is off, no lobby is live,
    or the member no longer passes the gate."""
    from crush_lu.models import EventLobbyParticipation

    now = now or timezone.now()
    if not lobby_feature_enabled():
        return None
    ok, _reason = participant_gate(user)
    if not ok:
        return None
    candidates = (
        EventLobbyParticipation.objects.filter(
            user=user,
            event__is_published=True,
            event__is_cancelled=False,
            # Generous DB cutoff; exact end derived below (end_time is a property).
            event__date_time__gte=now - timedelta(days=7),
        )
        .select_related("event")
        .order_by("-joined_at")
    )
    for participation in candidates:
        if event_lobby_phase(participation.event, now) == PHASE_LIVE:
            return participation
    return None
