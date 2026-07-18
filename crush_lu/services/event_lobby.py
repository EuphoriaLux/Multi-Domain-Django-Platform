"""
Event Lobby service layer — eligibility, phase math, roster shaping, and the
irrevocable meet-signal loop.

Spec: docs/superpowers/specs/2026-07-17-crush-connect-event-lobby-design.md
(§5 eligibility, §6 time/state model, §7.2–7.8 live lobby / recap / People
I've Met, §9–10 domain model and service boundaries, §13 privacy invariants).

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


class LobbyAccessError(Exception):
    """Private service-layer denial with a stable, non-identifying code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


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
    if (
        profile is None
        or not profile.is_active
        or profile.verification_status != "verified"
    ):
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
            event_registration__status="attended",
            user__crush_connect_membership__onboarded_at__isnull=False,
            user__crush_connect_membership__excluded_by_coach=False,
            user__crush_connect_membership__photo_share_consent=True,
            user__crushprofile__is_active=True,
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
    return EventLobbyParticipation.objects.filter(
        event=event,
        user=user,
        event_registration__status="attended",
    ).first()


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
    from crush_lu.models import EventMeetSignal

    unavailable = blocked_user_ids(viewer) | hidden_encounter_user_ids(viewer)
    mutual_ids = _mutual_user_ids(viewer, event)
    # §7.3 step 2 / §2: pairs already in People I've Met stay visible but are
    # non-actionable ("You've already met") and can never consume a signal.
    encounter_ids = _encounter_user_ids(viewer)
    # The viewer's own outgoing signals — own data, shown back to them so a
    # signalled tile renders as "sent" (never exposed to anyone else).
    signalled_ids = set(
        EventMeetSignal.objects.filter(event=event, sender=viewer).values_list(
            "recipient_id", flat=True
        )
    )
    roster = []
    qs = eligible_participations(event).exclude(user=viewer).order_by("-joined_at")
    for participation in qs:
        if participation.user_id in unavailable:
            continue
        entry = {
            "handle": participation.handle,
            "photo_url": _photo_url(event, participation.handle),
            "is_mutual": participation.user_id in mutual_ids,
            "already_met": participation.user_id in encounter_ids,
            "signalled": participation.user_id in signalled_ids,
        }
        # First name authorized for live mutuals and existing permanent
        # encounters (both already revealed); never for a plain participant.
        if entry["is_mutual"] or entry["already_met"]:
            entry["first_name"] = participation.user.first_name
        roster.append(entry)
    return roster


def get_mutuals(viewer, event) -> list[dict]:
    """The retrievable "Say hello" area (§7.4): every authorized mutual reveal
    for this event, newest first, excluding blocked pairs."""
    from crush_lu.models import EventMeetSignal

    unavailable = blocked_user_ids(viewer) | hidden_encounter_user_ids(viewer)
    participations_by_user = {
        p.user_id: p for p in eligible_participations(event).exclude(user=viewer)
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
        if signal.recipient_id in unavailable:
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

    unavailable = blocked_user_ids(user) | hidden_encounter_user_ids(user)
    eligible_sender_ids = eligible_participations(event).values("user_id")
    qs = EventMeetSignal.objects.filter(
        event=event,
        recipient=user,
        mutual_revealed_at__isnull=True,
        sender_id__in=eligible_sender_ids,
    )
    if unavailable:
        qs = qs.exclude(sender_id__in=unavailable)
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
    - ``already_met``       — pair already has a permanent encounter (§7.3
                              step 2); non-actionable, consumes nothing
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

    if viewer_participation(sender, event) is None:
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
    if (
        target is None
        or is_blocked_pair(sender, target.user)
        or target.user_id in hidden_encounter_user_ids(sender)
    ):
        return {"result": "unknown_participant"}
    recipient = target.user

    with transaction.atomic():
        # Lock BOTH participation rows in pk order (§13: row locks — the same
        # pair of locks serialises A→B and B→A, so two simultaneous signals
        # produce exactly one transactional mutual reveal, §16), and the
        # sender's lock alone serialises the three-signal quota across tabs.
        locked = list(
            EventLobbyParticipation.objects.select_for_update()
            .select_related("event_registration")
            .filter(
                event=event,
                user_id__in=[sender.pk, recipient.pk],
                event_registration__status="attended",
            )
            .order_by("user_id")
        )
        locked_user_ids = {p.user_id for p in locked}
        if sender.pk not in locked_user_ids:
            return {"result": "not_participant"}
        if recipient.pk not in locked_user_ids:
            return {"result": "unknown_participant"}

        # §6: compare server time inside the same transaction as the write.
        if timezone.now() >= event.end_time:
            return {"result": "phase_closed"}

        # §7.3 step 2 / §2: an existing permanent encounter is non-actionable —
        # tapping shows "You've already met" and consumes no signal.
        if recipient.pk in _encounter_user_ids(sender):
            return {
                "result": "already_met",
                "handle": target.handle,
                "first_name": recipient.first_name,
                "signals_remaining": signals_remaining(sender, event),
            }

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
    """The user's participation in a live or recap lobby, for the hub card.

    The attended registration is authoritative.  Re-evaluating it here makes
    the hub entry point self-healing when the check-in hook failed or the
    feature was enabled after the attendee had already checked in (§10.4).
    """
    from crush_lu.models import EventRegistration

    now = now or timezone.now()
    if not lobby_feature_enabled():
        return None
    ok, _reason = participant_gate(user)
    if not ok:
        return None
    candidates = (
        EventRegistration.objects.filter(
            user=user,
            status="attended",
            event__is_published=True,
            event__is_cancelled=False,
            # Generous DB cutoff; exact end derived below (end_time is a property).
            event__date_time__gte=now - timedelta(days=7),
        )
        .select_related("event")
        .order_by("-event__date_time")
    )
    for registration in candidates:
        phase = event_lobby_phase(registration.event, now)
        if phase not in (PHASE_LIVE, PHASE_RECAP):
            continue
        if phase == PHASE_LIVE:
            participation, _created = evaluate_participation(
                registration,
                source="checkin",
                now=now,
            )
        else:
            # Recap membership is frozen at the scheduled end. Never create a
            # late participation, but keep the existing participant's route
            # back to the 48-hour confirmation grid visible from the hub.
            participation = (
                registration.lobby_participation
                if hasattr(registration, "lobby_participation")
                else None
            )
        if participation is not None:
            participation.lobby_phase = phase
            return participation
    return None


# ---------------------------------------------------------------------------
# Recap phase — meeting confirmations & permanent encounters (§7.7, §9.3–9.4)
# ---------------------------------------------------------------------------


def _is_active_connect_member(user) -> bool:
    """Lighter check than ``participant_gate`` — onboarded + not excluded.
    Connect deactivation/exclusion hides the collection and recap access."""
    membership = getattr(user, "crush_connect_membership", None)
    profile = getattr(user, "crushprofile", None)
    return (
        membership is not None
        and membership.onboarded_at is not None
        and not membership.excluded_by_coach
        and profile is not None
        and profile.is_active
    )


def _can_appear_in_collection(user) -> bool:
    """A counterpart is renderable in People I've Met only while they remain an
    active Connect member with a usable current photo (§7.8: entries disappear
    while either side is inactive/excluded)."""
    if not _is_active_connect_member(user):
        return False
    profile = getattr(user, "crushprofile", None)
    return bool(
        profile is not None
        and profile.is_active
        and profile.verification_status == "verified"
        and profile.photo_1
    )


def _encounter_user_ids(viewer) -> set[int]:
    """User ids ``viewer`` already has an active permanent encounter with."""
    from crush_lu.models import ConfirmedEncounter

    ids = set()
    pairs = ConfirmedEncounter.objects.filter(
        Q(user_low=viewer) | Q(user_high=viewer), status="active"
    ).values_list("user_low_id", "user_high_id")
    for low_id, high_id in pairs:
        ids.add(high_id if low_id == viewer.pk else low_id)
    return ids


def hidden_encounter_user_ids(viewer) -> set[int]:
    """Counterparts hidden by a pending or approved removal request.

    These pairs stay mutually invisible in later lobbies as well as People
    I've Met. Treating their handles like unknown participants prevents stale
    URLs or handles from revealing whether a safety removal exists.
    """
    from crush_lu.models import ConfirmedEncounter

    ids = set()
    pairs = ConfirmedEncounter.objects.filter(
        Q(user_low=viewer) | Q(user_high=viewer),
        status__in=("removal_pending", "removed"),
    ).values_list("user_low_id", "user_high_id")
    for low_id, high_id in pairs:
        ids.add(high_id if low_id == viewer.pk else low_id)
    return ids


def incoming_confirmation_count(user, event) -> int:
    """The recap's exact anonymous counter (§7.7): incoming "we met"
    confirmations from still-eligible, non-blocked senders the viewer has NOT
    reciprocally confirmed (a reciprocal confirmation is revealed as an
    encounter, so it leaves the anonymous count — mirrors the live counter)."""
    from crush_lu.models import EventMeetingConfirmation

    unavailable = blocked_user_ids(user) | hidden_encounter_user_ids(user)
    eligible_sender_ids = eligible_participations(event).values("user_id")
    reciprocated = EventMeetingConfirmation.objects.filter(
        event=event, confirmer=user
    ).values("other_user_id")
    qs = EventMeetingConfirmation.objects.filter(
        event=event, other_user=user, confirmer_id__in=eligible_sender_ids
    ).exclude(confirmer_id__in=reciprocated)
    if unavailable:
        qs = qs.exclude(confirmer_id__in=unavailable)
    return qs.count()


def get_recap_roster(viewer, event) -> list[dict]:
    """The 48-hour recap grid (§7.7): every eligible lobby participant except
    self and blocked pairs. Live mutuals sort first and keep photo + first
    name ("You wanted to meet at the event"); everyone else is photo-only.
    Pairs already in a permanent encounter are non-actionable "You've already
    met" tiles."""
    from crush_lu.models import EventMeetingConfirmation

    unavailable = blocked_user_ids(viewer) | hidden_encounter_user_ids(viewer)
    live_mutual_ids = _mutual_user_ids(viewer, event)
    encounter_ids = _encounter_user_ids(viewer)
    confirmed_ids = set(
        EventMeetingConfirmation.objects.filter(
            event=event, confirmer=viewer
        ).values_list("other_user_id", flat=True)
    )
    entries = []
    qs = eligible_participations(event).exclude(user=viewer).order_by("-joined_at")
    for participation in qs:
        if participation.user_id in unavailable:
            continue
        is_live_mutual = participation.user_id in live_mutual_ids
        already_met = participation.user_id in encounter_ids
        entry = {
            "handle": participation.handle,
            "photo_url": _photo_url(event, participation.handle),
            "is_live_mutual": is_live_mutual,
            "already_met": already_met,
            "confirmed": participation.user_id in confirmed_ids,
        }
        # First name is authorized for live mutuals (already revealed live) and
        # for permanent encounters (already revealed) — never for a plain
        # unconfirmed participant (§13).
        if is_live_mutual or already_met:
            entry["first_name"] = participation.user.first_name
        entries.append(entry)
    # Stable sort: live mutuals first, order otherwise preserved (newest join).
    entries.sort(key=lambda e: not e["is_live_mutual"])
    return entries


def recap_state(user, event, now=None) -> dict:
    """Own recap-state payload for the header + pollers (§11): phase, countdown
    to recap close, exact incoming confirmation counter — no roster identity."""
    now = now or timezone.now()
    end = event.end_time
    recap_closes = end + timedelta(hours=RECAP_WINDOW_HOURS)
    return {
        "phase": event_lobby_phase(event, now),
        "event_end_at": end.isoformat(),
        "recap_closes_at": recap_closes.isoformat(),
        "seconds_to_recap_close": max(0, int((recap_closes - now).total_seconds())),
        "incoming_confirmations": incoming_confirmation_count(user, event),
    }


def _create_or_get_encounter(user_a, user_b, event):
    """Idempotently return the pair's ConfirmedEncounter (§9.4).

    A removed/removal_pending encounter is returned UNCHANGED — approved
    removal is permanent and never resurrected by a later confirmation
    (§7.8). ``created_at`` and ordering are set once and never touched by
    later shared events.
    """
    from crush_lu.models import ConfirmedEncounter

    low, high = ConfirmedEncounter.canonical_pair(user_a, user_b)
    encounter, created = ConfirmedEncounter.objects.get_or_create(
        user_low=low,
        user_high=high,
        defaults={"created_from_event": event, "status": "active"},
    )
    return encounter, created


def confirm_meeting(confirmer, event, target_handle, now=None) -> dict:
    """One irreversible, anonymous "Yes, we met" confirmation (§7.7, §9.3).

    Returns a dict whose ``result`` is one of:

    - ``confirmed``         — one-sided confirmation created, still anonymous
    - ``encounter``         — reverse confirmation existed; permanent encounter
                              created/returned and the first name revealed
    - ``encounter_hidden``  — reverse confirmation existed, but a prior removal
                              keeps the encounter hidden and sends no reveal
    - ``already_met``       — pair already has an active permanent encounter
                              (non-actionable, consumes nothing)
    - ``duplicate``         — confirmer already confirmed this person
    - ``phase_closed``      — not in the 48-hour recap window
    - ``feature_disabled``  — rollout flag / launch phase off
    - ``not_participant``   — confirmer has no participation or lost eligibility
    - ``unknown_participant`` — handle not in this event's eligible roster
                              (also covers blocked pairs — not probeable, §8.2)

    ``encounter`` results include ``recipient_user_id`` for server-side private
    notification routing; it must never reach any client payload (§13).
    """
    from crush_lu.models import (
        ConfirmedEncounter,
        EventLobbyParticipation,
        EventMeetingConfirmation,
    )

    now = now or timezone.now()

    if not lobby_feature_enabled():
        return {"result": "feature_disabled"}
    if event_lobby_phase(event, now) != PHASE_RECAP:
        return {"result": "phase_closed"}
    if viewer_participation(confirmer, event) is None:
        return {"result": "not_participant"}

    target = (
        eligible_participations(event)
        .filter(handle=target_handle)
        .exclude(user=confirmer)
        .first()
    )
    if (
        target is None
        or is_blocked_pair(confirmer, target.user)
        or target.user_id in hidden_encounter_user_ids(confirmer)
    ):
        return {"result": "unknown_participant"}
    other = target.user

    with transaction.atomic():
        # Lock the same canonical pair of participation rows before either
        # directional confirmation is inserted. Concurrent A→B and B→A
        # requests therefore serialize, and the second transaction always
        # observes the first confirmation before checking for reciprocity.
        low, high = ConfirmedEncounter.canonical_pair(confirmer, other)
        locked = list(
            EventLobbyParticipation.objects.select_for_update()
            .select_related("event_registration")
            .filter(
                event=event,
                user_id__in=[low.pk, high.pk],
                event_registration__status="attended",
            )
            .order_by("user_id")
        )
        locked_user_ids = {participation.user_id for participation in locked}
        if confirmer.pk not in locked_user_ids:
            return {"result": "not_participant"}
        if other.pk not in locked_user_ids:
            return {"result": "unknown_participant"}

        # §6: re-derive the phase inside the write transaction.
        if event_lobby_phase(event, timezone.now()) != PHASE_RECAP:
            return {"result": "phase_closed"}

        existing = (
            ConfirmedEncounter.objects.select_for_update()
            .filter(user_low=low, user_high=high)
            .first()
        )
        if existing is not None and existing.status == "active":
            # §7.7: previously confirmed permanent encounters are non-actionable.
            return {
                "result": "already_met",
                "handle": target.handle,
                "first_name": other.first_name,
            }

        confirmation, created = EventMeetingConfirmation.objects.get_or_create(
            event=event, confirmer=confirmer, other_user=other
        )
        if not created:
            return {"result": "duplicate"}

        reverse_exists = EventMeetingConfirmation.objects.filter(
            event=event, confirmer=other, other_user=confirmer
        ).exists()
        if reverse_exists:
            encounter, _enc_created = _create_or_get_encounter(confirmer, other, event)
            if encounter.status != "active":
                logger.info(
                    "Lobby reciprocal confirmation kept hidden for event %s "
                    "(encounter %s, status %s)",
                    event.pk,
                    encounter.pk,
                    encounter.status,
                )
                return {"result": "encounter_hidden"}
            logger.info(
                "Lobby permanent encounter for event %s (pair %s+%s)",
                event.pk,
                low.pk,
                high.pk,
            )
            return {
                "result": "encounter",
                "handle": target.handle,
                "first_name": other.first_name,
                "recipient_user_id": other.pk,
            }

    logger.info(
        "Lobby meeting confirmation for event %s (confirmation %s)",
        event.pk,
        confirmation.pk,
    )
    return {"result": "confirmed", "recipient_user_id": other.pk}


# ---------------------------------------------------------------------------
# People I've Met — permanent collection (§7.8)
# ---------------------------------------------------------------------------


def get_people_ive_met(user) -> list[dict]:
    """The flat, chronological permanent collection (§7.8), newest first.

    One entry per pair; current photo + first name only; no event, date,
    counter, or history. Entries disappear while either side is
    inactive/excluded or the pair is blocked; the viewer must be an active
    Connect member (deactivation hides the whole collection)."""
    from crush_lu.models import ConfirmedEncounter
    from crush_lu.views_media import get_profile_photo_url

    if not user.is_authenticated or not _is_active_connect_member(user):
        return []

    blocked = blocked_user_ids(user)
    entries = []
    encounters = (
        ConfirmedEncounter.objects.filter(
            Q(user_low=user) | Q(user_high=user), status="active"
        )
        .select_related(
            "user_low",
            "user_high",
            "user_low__crushprofile",
            "user_high__crushprofile",
            "user_low__crush_connect_membership",
            "user_high__crush_connect_membership",
        )
        .order_by("-created_at")
    )
    for encounter in encounters:
        other = encounter.counterpart_of(user)
        if other.pk in blocked or not _can_appear_in_collection(other):
            continue
        entries.append(
            {
                "user_id": other.pk,
                "first_name": other.first_name,
                "photo_url": get_profile_photo_url(other.crushprofile, "photo_1"),
                "profile_url": reverse(
                    "crush_lu:event_lobby_person", kwargs={"user_id": other.pk}
                ),
                "created_at": encounter.created_at.isoformat(),
            }
        )
    return entries


def encounter_counterpart(viewer, user_id):
    """Return the other user IFF ``viewer`` has an active permanent encounter
    with them and they can still appear (§13: full-profile access is
    authorized by the encounter, not an unguessable id). Else None."""
    from django.contrib.auth.models import User

    from crush_lu.models import ConfirmedEncounter

    if not _is_active_connect_member(viewer):
        return None
    other = (
        User.objects.select_related("crushprofile", "crush_connect_membership")
        .filter(pk=user_id)
        .first()
    )
    if other is None or other.pk == viewer.pk:
        return None
    if is_blocked_pair(viewer, other) or not _can_appear_in_collection(other):
        return None
    low, high = ConfirmedEncounter.canonical_pair(viewer, other)
    has_active = ConfirmedEncounter.objects.filter(
        user_low=low, user_high=high, status="active"
    ).exists()
    return other if has_active else None


# ═══════════════════════════════════════════════════════════════════════════
# REMOVAL REVIEW WORKFLOW — ported from PR #633 (Codex)
# ═══════════════════════════════════════════════════════════════════════════


def submit_encounter_removal_request(user, encounter_handle, reason, details=""):
    """Submit a private removal request for a confirmed encounter.

    Immediately hides the encounter for both parties (two-sided hiding)
    and queues it for coach/Support review.
    """
    from crush_lu.models import ConfirmedEncounter, ConfirmedEncounterRemovalRequest

    if not _is_active_connect_member(user):
        raise LobbyAccessError("not_available")

    # Validate reason
    valid_reasons = dict(ConfirmedEncounterRemovalRequest.REASON_CHOICES)
    if reason not in valid_reasons:
        raise LobbyAccessError("invalid_reason")

    # People-I've-Met addresses the counterpart with this member-visible
    # handle. Resolve the exact canonical pair; never fall back to "the user's
    # only encounter", which can hide the wrong person or raise when several
    # encounters exist.
    try:
        counterpart_id = int(encounter_handle)
    except (TypeError, ValueError):
        raise LobbyAccessError("not_available") from None
    if counterpart_id <= 0 or counterpart_id == user.pk:
        raise LobbyAccessError("not_available")

    details = (details or "").strip()[:500]

    with transaction.atomic():
        try:
            encounter = (
                ConfirmedEncounter.objects.select_for_update()
                .filter(
                    Q(user_low=user, user_high_id=counterpart_id)
                    | Q(user_high=user, user_low_id=counterpart_id),
                    status="active",
                )
                .get()
            )
        except ConfirmedEncounter.DoesNotExist:
            raise LobbyAccessError("not_available") from None

        # Create the removal request
        removal_request = ConfirmedEncounterRemovalRequest.objects.create(
            encounter=encounter,
            requested_by=user,
            reason=reason,
            details=details,
        )

        # Immediately hide the encounter for both parties
        encounter.status = "removal_pending"
        encounter.hidden_at = timezone.now()
        encounter.save(update_fields=["status", "hidden_at"])

    logger.info(
        "Encounter removal requested: encounter=%s user=%s reason=%s",
        encounter.pk,
        user.pk,
        reason,
    )

    return removal_request


def reviewable_removal_requests(user):
    """Return removal requests that the given user can review.

    Staff see all. Coaches remain denied until requests can be scoped to an
    assigned member or event; exposing the global queue would disclose private
    safety details and allow cross-scope moderation.
    """
    from crush_lu.models import ConfirmedEncounterRemovalRequest

    if not user.is_authenticated:
        return ConfirmedEncounterRemovalRequest.objects.none()

    # Coach accounts receive ``is_staff`` for their dedicated admin surfaces,
    # so plain staff status is not enough to authorize this private global
    # queue. Superusers retain emergency access; ordinary coach accounts stay
    # denied until member/event scoping exists.
    coach = getattr(user, "crushcoach", None)
    if coach and not user.is_superuser:
        return ConfirmedEncounterRemovalRequest.objects.none()

    # Support/admin staff can see all.
    if user.is_staff:
        return ConfirmedEncounterRemovalRequest.objects.all()

    return ConfirmedEncounterRemovalRequest.objects.none()


def review_encounter_removal_request(actor, request_id, decision, notes):
    """Record a moderation outcome for a removal request.

    Decisions:
    - approve: encounter permanently removed
    - keep_hidden: encounter stays hidden (removal_pending)
    - restore: encounter visibility restored to active
    """
    from crush_lu.models import ConfirmedEncounterRemovalRequest

    valid_decisions = {"approve", "keep_hidden", "restore"}
    notes = (notes or "").strip()

    if decision not in valid_decisions or not notes or len(notes) > 1000:
        raise LobbyAccessError("invalid_request")

    with transaction.atomic():
        try:
            removal_request = (
                reviewable_removal_requests(actor)
                .select_for_update()
                .select_related("encounter")
                .get(pk=request_id, status="pending")
            )
        except ConfirmedEncounterRemovalRequest.DoesNotExist:
            raise LobbyAccessError("not_available") from None

        encounter = removal_request.encounter
        now = timezone.now()

        # The queue is staff-only until coach scope can be enforced.
        removal_request.reviewed_by_staff = actor

        removal_request.reviewed_at = now
        removal_request.resolution_notes = notes

        if decision == "approve":
            removal_request.status = "approved"
            encounter.status = "removed"
            encounter.removed_at = now
        elif decision == "keep_hidden":
            removal_request.status = "kept_hidden"
            # Encounter stays in removal_pending state
        elif decision == "restore":
            removal_request.status = "restored"
            encounter.status = "active"
            encounter.hidden_at = None

        removal_request.save()
        encounter.save()

    logger.info(
        "Encounter removal reviewed: request=%s decision=%s actor=%s",
        request_id,
        decision,
        actor.pk,
    )

    return removal_request


def get_people_met_profile(user, handle):
    """Get full profile for a People I've Met entry by handle.

    Used by the removal request flow to show who is being reported.
    """
    from crush_lu.views_media import get_profile_photo_url

    if not _is_active_connect_member(user):
        raise LobbyAccessError("not_available")

    # Find encounter by user (handle is the other user's opaque id)
    # For now, we look up by user_id since handles are per-event
    try:
        other_user_id = int(handle)
    except (ValueError, TypeError):
        raise LobbyAccessError("not_available") from None

    other = encounter_counterpart(user, other_user_id)
    if other is None:
        raise LobbyAccessError("not_available")

    return {
        "user_id": other.pk,
        "first_name": other.first_name,
        "photo_url": get_profile_photo_url(other.crushprofile, "photo_1"),
        "handle": handle,
    }
