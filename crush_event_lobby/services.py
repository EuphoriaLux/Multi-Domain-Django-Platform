from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.services.blocking import blocked_user_ids, is_blocked_pair

from .models import EventLobbyConsent, EventLobbyParticipation, EventMeetSignal

CURRENT_CONSENT_VERSION = 1
SIGNAL_LIMIT = 3


class LobbyAccessError(Exception):
    """A client-safe reason code; never contains another member's identity."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SignalResult:
    created: bool
    mutual: bool
    remaining: int
    first_name: str | None = None


def event_end_at(event):
    return event.date_time + timedelta(minutes=event.duration_minutes)


def is_live(event, now=None):
    now = now or timezone.now()
    return (
        event.is_published
        and not event.is_cancelled
        and now < event_end_at(event)
    )


def _active_member_reason(user, *, require_lobby_consent=True):
    if not getattr(settings, "CRUSH_EVENT_LOBBY_ENABLED", False):
        return "feature_unavailable"
    if not getattr(user, "is_authenticated", False):
        return "not_available"

    try:
        profile = user.crushprofile
        membership = user.crush_connect_membership
    except (AttributeError, ObjectDoesNotExist):
        return "not_available"

    if not profile.is_active or not profile.is_approved:
        return "not_available"
    if not profile.has_luxid_connected:
        return "not_available"
    if membership.onboarded_at is None or membership.excluded_by_coach:
        return "not_available"
    if not membership.photo_share_consent or not profile.photo_1:
        return "not_available"
    if require_lobby_consent:
        try:
            consent = user.event_lobby_consent
        except ObjectDoesNotExist:
            consent = None
        if consent is None or consent.version != CURRENT_CONSENT_VERSION:
            return "consent_required"
    return None


# Import here to keep the public service helpers above easy to scan.
from django.core.exceptions import ObjectDoesNotExist  # noqa: E402


def acknowledge_consent(user):
    reason = _active_member_reason(user, require_lobby_consent=False)
    if reason:
        raise LobbyAccessError(reason)
    consent, _ = EventLobbyConsent.objects.update_or_create(
        user=user,
        defaults={
            "version": CURRENT_CONSENT_VERSION,
            "acknowledged_at": timezone.now(),
        },
    )
    return consent


def ensure_participation(event, user, source=EventLobbyParticipation.SOURCE_CHECKIN):
    """Create the immutable event participation once eligibility is satisfied."""

    now = timezone.now()
    reason = _active_member_reason(user)
    if reason:
        raise LobbyAccessError(reason)
    if not is_live(event, now):
        raise LobbyAccessError("not_available")

    # PROTOTYPE-STUB: production check-in and onboarding completion should call
    # this service after commit. The prototype invokes it on authenticated lobby
    # entry and from the seed command, preserving idempotency without touching
    # the existing check-in response path.
    with transaction.atomic():
        registration = (
            EventRegistration.objects.select_for_update()
            .filter(event=event, user=user, status="attended")
            .first()
        )
        if registration is None:
            raise LobbyAccessError("not_available")
        participation, _ = EventLobbyParticipation.objects.get_or_create(
            event_registration=registration,
            defaults={
                "event": event,
                "user": user,
                "joined_at": now,
                "eligibility_source": source,
            },
        )
        return participation


def _viewer_participation(event, user):
    if not is_live(event):
        raise LobbyAccessError("not_available")
    reason = _active_member_reason(user)
    if reason:
        raise LobbyAccessError(reason)
    try:
        participation = EventLobbyParticipation.objects.get(event=event, user=user)
    except EventLobbyParticipation.DoesNotExist:
        participation = ensure_participation(event, user)
    return participation


def _currently_visible_participations(event, viewer):
    hidden_ids = blocked_user_ids(viewer)
    candidates = (
        EventLobbyParticipation.objects.filter(event=event)
        .exclude(user=viewer)
        .exclude(user_id__in=hidden_ids)
        .select_related(
            "user",
            "user__crushprofile",
            "user__crush_connect_membership",
            "user__event_lobby_consent",
        )
        .order_by("-joined_at")
    )
    return [
        participation
        for participation in candidates
        if _active_member_reason(participation.user) is None
    ]


def _signal_sets(event, viewer):
    sent = set(
        EventMeetSignal.objects.filter(event=event, sender=viewer).values_list(
            "recipient_id", flat=True
        )
    )
    received = set(
        EventMeetSignal.objects.filter(event=event, recipient=viewer).values_list(
            "sender_id", flat=True
        )
    )
    return sent, received


def list_live_participants(event, viewer):
    _viewer_participation(event, viewer)
    visible = _currently_visible_participations(event, viewer)
    sent, received = _signal_sets(event, viewer)
    payload = []
    for participation in visible:
        mutual = (
            participation.user_id in sent and participation.user_id in received
        )
        item = {
            "handle": str(participation.opaque_handle),
            "photo_url": reverse(
                "crush_lu:event_lobby:participant_photo",
                kwargs={
                    "event_id": event.pk,
                    "handle": participation.opaque_handle,
                },
            ),
            "signal_sent": participation.user_id in sent,
            "mutual": mutual,
        }
        if mutual:
            item["first_name"] = participation.user.first_name
        payload.append(item)
    return payload


def lobby_state(event, viewer):
    _viewer_participation(event, viewer)
    visible_ids = {
        participation.user_id
        for participation in _currently_visible_participations(event, viewer)
    }
    sent_count = EventMeetSignal.objects.filter(event=event, sender=viewer).count()
    incoming_count = EventMeetSignal.objects.filter(
        event=event,
        recipient=viewer,
        sender_id__in=visible_ids,
    ).count()
    return {
        "phase": "live",
        "event_title": event.title,
        "event_end_at": event_end_at(event).isoformat(),
        "signals_remaining": max(0, SIGNAL_LIMIT - sent_count),
        "incoming_signal_count": incoming_count,
    }


def _target_participation(event, viewer, handle):
    _viewer_participation(event, viewer)
    try:
        target = EventLobbyParticipation.objects.select_related(
            "user",
            "user__crushprofile",
            "user__crush_connect_membership",
            "user__event_lobby_consent",
        ).get(event=event, opaque_handle=handle)
    except EventLobbyParticipation.DoesNotExist:
        raise LobbyAccessError("not_available") from None
    if target.user_id == viewer.pk:
        raise LobbyAccessError("not_available")
    if is_blocked_pair(viewer, target.user):
        raise LobbyAccessError("not_available")
    if _active_member_reason(target.user):
        raise LobbyAccessError("not_available")
    return target


def get_confirmation_target(event, viewer, handle):
    target = _target_participation(event, viewer, handle)
    sent, received = _signal_sets(event, viewer)
    mutual = target.user_id in sent and target.user_id in received
    payload = {
        "handle": str(target.opaque_handle),
        "photo_url": reverse(
            "crush_lu:event_lobby:participant_photo",
            kwargs={"event_id": event.pk, "handle": target.opaque_handle},
        ),
        "signal_sent": target.user_id in sent,
        "mutual": mutual,
    }
    if mutual:
        payload["first_name"] = target.user.first_name
    return payload


def send_meet_signal(event, sender, recipient_handle):
    """Create one immutable signal and reveal only an exact reciprocal pair."""

    now = timezone.now()
    with transaction.atomic():
        locked_event = MeetupEvent.objects.select_for_update().get(pk=event.pk)
        if not is_live(locked_event, now):
            raise LobbyAccessError("not_available")

        try:
            sender_participation = EventLobbyParticipation.objects.select_for_update().get(
                event=locked_event, user=sender
            )
        except EventLobbyParticipation.DoesNotExist:
            raise LobbyAccessError("not_available") from None
        if _active_member_reason(sender):
            raise LobbyAccessError("not_available")

        try:
            recipient_participation = (
                EventLobbyParticipation.objects.select_for_update()
                .select_related(
                    "user",
                    "user__crushprofile",
                    "user__crush_connect_membership",
                    "user__event_lobby_consent",
                )
                .get(event=locked_event, opaque_handle=recipient_handle)
            )
        except EventLobbyParticipation.DoesNotExist:
            raise LobbyAccessError("not_available") from None
        recipient = recipient_participation.user
        if recipient.pk == sender_participation.user_id:
            raise LobbyAccessError("not_available")
        if is_blocked_pair(sender, recipient) or _active_member_reason(recipient):
            raise LobbyAccessError("not_available")

        existing = EventMeetSignal.objects.filter(
            event=locked_event, sender=sender, recipient=recipient
        ).first()
        if existing is not None:
            mutual = EventMeetSignal.objects.filter(
                event=locked_event, sender=recipient, recipient=sender
            ).exists()
            remaining = max(
                0,
                SIGNAL_LIMIT
                - EventMeetSignal.objects.filter(
                    event=locked_event, sender=sender
                ).count(),
            )
            return SignalResult(
                created=False,
                mutual=mutual,
                remaining=remaining,
                first_name=recipient.first_name if mutual else None,
            )

        used = EventMeetSignal.objects.filter(
            event=locked_event, sender=sender
        ).count()
        if used >= SIGNAL_LIMIT:
            raise LobbyAccessError("signal_limit_reached")

        signal = EventMeetSignal.objects.create(
            event=locked_event, sender=sender, recipient=recipient
        )
        reverse_signal = EventMeetSignal.objects.filter(
            event=locked_event, sender=recipient, recipient=sender
        ).first()
        mutual = reverse_signal is not None
        if mutual:
            EventMeetSignal.objects.filter(
                pk__in=[signal.pk, reverse_signal.pk], mutual_revealed_at__isnull=True
            ).update(mutual_revealed_at=now)

        # PROTOTYPE-STUB: a production implementation sends a sanitized refetch
        # hint to the recipient's private lobby channel. Polling and page refresh
        # are authoritative in this local slice, and no outbound channel fires.
        return SignalResult(
            created=True,
            mutual=mutual,
            remaining=SIGNAL_LIMIT - used - 1,
            first_name=recipient.first_name if mutual else None,
        )


def get_authorized_photo(event, viewer, handle):
    target = _target_participation(event, viewer, handle)
    return target.user.crushprofile.photo_1
