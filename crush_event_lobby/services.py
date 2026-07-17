from dataclasses import dataclass
from datetime import timedelta
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from crush_lu.models import EventRegistration, MeetupEvent, Notification
from crush_lu.services.blocking import blocked_user_ids, is_blocked_pair

from .models import (
    ConfirmedEncounter,
    EventLobbyConsent,
    EventLobbyParticipation,
    EventMeetingConfirmation,
    EventMeetSignal,
    EventRecapNotice,
)

CURRENT_CONSENT_VERSION = 1
SIGNAL_LIMIT = 3
RECAP_DURATION = timedelta(hours=48)
RECAP_REMINDER_AFTER = timedelta(hours=24)
logger = logging.getLogger(__name__)
User = get_user_model()


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


@dataclass(frozen=True)
class ConfirmationResult:
    created: bool
    mutual: bool
    first_name: str | None = None


def event_end_at(event):
    return event.date_time + timedelta(minutes=event.duration_minutes)


def recap_closes_at(event):
    return event_end_at(event) + RECAP_DURATION


def event_phase(event, now=None):
    now = now or timezone.now()
    if not event.is_published or event.is_cancelled:
        return "closed"
    if now < event_end_at(event):
        return "live"
    if now < recap_closes_at(event):
        return "recap"
    return "closed"


def is_live(event, now=None):
    return event_phase(event, now) == "live"


def is_recap(event, now=None):
    return event_phase(event, now) == "recap"


def broadcast_lobby_refresh(event_id, reason, *, user_ids=None):
    """Send identity-free refetch hints to authorized lobby clients."""

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    groups = (
        [f"event_lobby_{event_id}"]
        if user_ids is None
        else [f"event_lobby_{event_id}_user_{user_id}" for user_id in user_ids]
    )
    for group in groups:
        try:
            async_to_sync(channel_layer.group_send)(
                group,
                {
                    "type": "lobby.refresh",
                    "reason": reason,
                },
            )
        except Exception:
            # Realtime is advisory; HTTP state remains authoritative.
            logger.exception(
                "Event Lobby realtime hint failed for event %s",
                event_id,
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
        if not EventLobbyConsent.objects.filter(
            user=user, version=CURRENT_CONSENT_VERSION
        ).exists():
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

    # QR check-in and onboarding completion call this service; authenticated
    # lobby entry remains an idempotent fallback for interrupted side effects.
    with transaction.atomic():
        registration = (
            EventRegistration.objects.select_for_update()
            .filter(event=event, user=user, status="attended")
            .first()
        )
        if registration is None:
            raise LobbyAccessError("not_available")
        participation, created = EventLobbyParticipation.objects.get_or_create(
            event_registration=registration,
            defaults={
                "event": event,
                "user": user,
                "joined_at": now,
                "eligibility_source": source,
            },
        )
        if created:
            transaction.on_commit(
                lambda event_id=event.pk: broadcast_lobby_refresh(
                    event_id,
                    "participant_joined",
                )
            )
        return participation


def evaluate_participation_after_checkin(event, user):
    """Enroll an eligible checked-in member without affecting check-in."""

    if _active_member_reason(user) is not None or not is_live(event):
        return None
    return ensure_participation(
        event,
        user,
        source=EventLobbyParticipation.SOURCE_CHECKIN,
    )


def evaluate_participations_after_onboarding(user):
    """Join every still-live attended event after Connect onboarding."""

    participations = []
    registrations = EventRegistration.objects.filter(
        user=user,
        status="attended",
        event__is_published=True,
        event__is_cancelled=False,
    ).select_related("event")
    for registration in registrations:
        if is_live(registration.event):
            try:
                participation = ensure_participation(
                    registration.event,
                    user,
                    source=EventLobbyParticipation.SOURCE_ONBOARDING,
                )
            except LobbyAccessError:
                continue
            participations.append(participation)
    return participations


def lobby_entry_url(event, user):
    """Return the lobby URL only when it is safe to advertise to the member."""

    if _active_member_reason(user, require_lobby_consent=False) is not None:
        return None
    if not is_live(event):
        return None
    if not EventRegistration.objects.filter(
        event=event,
        user=user,
        status="attended",
    ).exists():
        return None
    return reverse(
        "crush_lu:event_lobby:lobby",
        kwargs={"event_id": event.pk},
    )


def materialize_recap_notifications(event, now=None):
    """Persist due recap notifications once; safe for a periodic tick or request."""

    now = now or timezone.now()
    if event_phase(event, now) != "recap":
        return 0

    created_count = 0
    participations = EventLobbyParticipation.objects.filter(event=event).select_related(
        "user",
        "user__crushprofile",
        "user__crush_connect_membership",
        "user__event_lobby_consent",
    )
    for participation in participations:
        if _active_member_reason(participation.user) is not None:
            continue
        with transaction.atomic():
            (
                notice,
                _created,
            ) = EventRecapNotice.objects.select_for_update().get_or_create(
                participation=participation
            )
            if notice.opened_notification_at is None:
                Notification.objects.create(
                    user=participation.user,
                    notification_type="event_lobby_recap_open",
                    title=_("Who did you meet?"),
                    body=_("You have 48 hours to confirm who you met at the event."),
                    link_url=reverse(
                        "crush_lu:event_lobby:recap",
                        kwargs={"event_id": event.pk},
                    ),
                    metadata={"event_id": event.pk},
                )
                notice.opened_notification_at = now
                notice.save(update_fields=["opened_notification_at"])
                created_count += 1

            reminder_due = now >= event_end_at(event) + RECAP_REMINDER_AFTER
            has_recap_action = EventMeetingConfirmation.objects.filter(
                event=event, confirmer=participation.user
            ).exists()
            if (
                reminder_due
                and not has_recap_action
                and notice.reminder_notification_at is None
            ):
                Notification.objects.create(
                    user=participation.user,
                    notification_type="event_lobby_recap_reminder",
                    title=_("Your event recap closes in 24 hours"),
                    body=_("Confirm anyone you met before the private recap closes."),
                    link_url=reverse(
                        "crush_lu:event_lobby:recap",
                        kwargs={"event_id": event.pk},
                    ),
                    metadata={"event_id": event.pk},
                )
                notice.reminder_notification_at = now
                notice.save(update_fields=["reminder_notification_at"])
                created_count += 1
    if created_count:
        broadcast_lobby_refresh(event.pk, "phase_changed")
    return created_count


def active_lobby_card(user):
    """Build the member's current live-lobby or recap hub card."""

    if _active_member_reason(user, require_lobby_consent=False) is not None:
        return None
    registrations = (
        EventRegistration.objects.filter(
            user=user,
            status="attended",
            event__is_published=True,
            event__is_cancelled=False,
        )
        .select_related("event")
        .order_by("-event__date_time")
    )
    for registration in registrations:
        event = registration.event
        phase = event_phase(event)
        if phase == "live":
            return {
                "phase": "live",
                "event_title": event.title,
                "event_end_at": event_end_at(event),
                "url": reverse(
                    "crush_lu:event_lobby:lobby",
                    kwargs={"event_id": event.pk},
                ),
            }
        if (
            phase == "recap"
            and EventLobbyParticipation.objects.filter(event=event, user=user).exists()
        ):
            materialize_recap_notifications(event)
            return {
                "phase": "recap",
                "event_title": event.title,
                "recap_closes_at": recap_closes_at(event),
                "url": reverse(
                    "crush_lu:event_lobby:recap",
                    kwargs={"event_id": event.pk},
                ),
            }
    return None


def _viewer_participation_any_phase(event, user):
    reason = _active_member_reason(user)
    if reason:
        raise LobbyAccessError(reason)
    try:
        return EventLobbyParticipation.objects.get(event=event, user=user)
    except EventLobbyParticipation.DoesNotExist:
        raise LobbyAccessError("not_available") from None


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


def _viewer_recap_participation(event, user):
    if not is_recap(event):
        raise LobbyAccessError("not_available")
    participation = _viewer_participation_any_phase(event, user)
    materialize_recap_notifications(event)
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
        mutual = participation.user_id in sent and participation.user_id in received
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
    phase = event_phase(event)
    if phase != "live":
        _viewer_participation_any_phase(event, viewer)
        payload = {
            "phase": phase,
            "event_title": event.title,
            "hub_url": reverse("crush_lu:crush_connect_hub"),
        }
        if phase == "recap":
            materialize_recap_notifications(event)
            payload.update(
                {
                    "recap_closes_at": recap_closes_at(event).isoformat(),
                    "recap_url": reverse(
                        "crush_lu:event_lobby:recap",
                        kwargs={"event_id": event.pk},
                    ),
                }
            )
        return payload

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


def _target_participation(event, viewer, handle, *, phase="live"):
    if phase == "recap":
        _viewer_recap_participation(event, viewer)
    else:
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
            sender_participation = (
                EventLobbyParticipation.objects.select_for_update().get(
                    event=locked_event, user=sender
                )
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

        used = EventMeetSignal.objects.filter(event=locked_event, sender=sender).count()
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
        reason = "mutual_revealed" if mutual else "incoming_signal"
        refresh_user_ids = [sender.pk, recipient.pk] if mutual else [recipient.pk]
        transaction.on_commit(
            lambda event_id=locked_event.pk, reason=reason, user_ids=refresh_user_ids: broadcast_lobby_refresh(
                event_id,
                reason,
                user_ids=user_ids,
            )
        )
        return SignalResult(
            created=True,
            mutual=mutual,
            remaining=SIGNAL_LIMIT - used - 1,
            first_name=recipient.first_name if mutual else None,
        )


def get_authorized_photo(event, viewer, handle):
    phase = event_phase(event)
    if phase not in {"live", "recap"}:
        raise LobbyAccessError("not_available")
    target = _target_participation(event, viewer, handle, phase=phase)
    return target.user.crushprofile.photo_1


def _confirmation_sets(event, viewer):
    sent = set(
        EventMeetingConfirmation.objects.filter(
            event=event, confirmer=viewer
        ).values_list("other_user_id", flat=True)
    )
    received = set(
        EventMeetingConfirmation.objects.filter(
            event=event, other_user=viewer
        ).values_list("confirmer_id", flat=True)
    )
    return sent, received


def _encounter_for_pair(user_a, user_b):
    low_id, high_id = sorted((user_a.pk, user_b.pk))
    return ConfirmedEncounter.objects.filter(
        user_low_id=low_id, user_high_id=high_id
    ).first()


def list_recap_participants(event, viewer):
    _viewer_recap_participation(event, viewer)
    visible = _currently_visible_participations(event, viewer)
    signal_sent, signal_received = _signal_sets(event, viewer)
    confirmed_sent, confirmed_received = _confirmation_sets(event, viewer)
    payload = []
    for participation in visible:
        other = participation.user
        live_mutual = other.pk in signal_sent and other.pk in signal_received
        confirmed_mutual = other.pk in confirmed_sent and other.pk in confirmed_received
        encounter = _encounter_for_pair(viewer, other)
        item = {
            "handle": str(participation.opaque_handle),
            "photo_url": reverse(
                "crush_lu:event_lobby:participant_photo",
                kwargs={
                    "event_id": event.pk,
                    "handle": participation.opaque_handle,
                },
            ),
            "live_mutual": live_mutual,
            "confirmation_sent": other.pk in confirmed_sent,
            "confirmed_mutual": confirmed_mutual,
            "already_met": encounter is not None,
        }
        if live_mutual or confirmed_mutual or encounter is not None:
            item["first_name"] = other.first_name
        payload.append(item)
    payload.sort(key=lambda item: (not item["live_mutual"],))
    return payload


def recap_state(event, viewer):
    _viewer_recap_participation(event, viewer)
    visible_ids = {
        participation.user_id
        for participation in _currently_visible_participations(event, viewer)
    }
    incoming_count = EventMeetingConfirmation.objects.filter(
        event=event,
        other_user=viewer,
        confirmer_id__in=visible_ids,
    ).count()
    return {
        "phase": "recap",
        "event_title": event.title,
        "recap_closes_at": recap_closes_at(event),
        "incoming_confirmation_count": incoming_count,
    }


def get_recap_confirmation_target(event, viewer, handle):
    target = _target_participation(event, viewer, handle, phase="recap")
    confirmation_sent, confirmation_received = _confirmation_sets(event, viewer)
    live_sent, live_received = _signal_sets(event, viewer)
    encounter = _encounter_for_pair(viewer, target.user)
    live_mutual = target.user_id in live_sent and target.user_id in live_received
    confirmed_mutual = (
        target.user_id in confirmation_sent and target.user_id in confirmation_received
    )
    payload = {
        "handle": str(target.opaque_handle),
        "photo_url": reverse(
            "crush_lu:event_lobby:participant_photo",
            kwargs={"event_id": event.pk, "handle": target.opaque_handle},
        ),
        "live_mutual": live_mutual,
        "confirmation_sent": target.user_id in confirmation_sent,
        "confirmed_mutual": confirmed_mutual,
        "already_met": encounter is not None,
    }
    if live_mutual or confirmed_mutual or encounter is not None:
        payload["first_name"] = target.user.first_name
    return payload


def _notify_confirmed_encounter(encounter, user_a, user_b):
    for recipient, other in ((user_a, user_b), (user_b, user_a)):
        Notification.objects.get_or_create(
            user=recipient,
            notification_type="event_lobby_encounter_confirmed",
            metadata={"encounter_id": encounter.pk},
            defaults={
                "title": _("%(name)s was added to People I've Met")
                % {"name": other.first_name or _("Someone you met")},
                "body": _("You both confirmed that you met at the event."),
                "link_url": reverse(
                    "crush_lu:event_lobby:people_met_profile",
                    kwargs={"handle": encounter.opaque_handle},
                ),
            },
        )


def confirm_event_meeting(event, confirmer, other_handle):
    """Create an irreversible recap confirmation and permanent mutual pair."""

    now = timezone.now()
    with transaction.atomic():
        locked_event = MeetupEvent.objects.select_for_update().get(pk=event.pk)
        if event_phase(locked_event, now) != "recap":
            raise LobbyAccessError("not_available")
        _viewer_participation_any_phase(locked_event, confirmer)
        target = _target_participation(
            locked_event, confirmer, other_handle, phase="recap"
        )
        other = target.user
        if _encounter_for_pair(confirmer, other) is not None:
            raise LobbyAccessError("already_met")

        confirmation, created = EventMeetingConfirmation.objects.get_or_create(
            event=locked_event,
            confirmer=confirmer,
            other_user=other,
        )
        reverse_exists = EventMeetingConfirmation.objects.filter(
            event=locked_event,
            confirmer=other,
            other_user=confirmer,
        ).exists()
        if not reverse_exists:
            return ConfirmationResult(created=created, mutual=False)

        low, high = sorted((confirmer, other), key=lambda user: user.pk)
        encounter, encounter_created = ConfirmedEncounter.objects.get_or_create(
            user_low=low,
            user_high=high,
            defaults={"created_from_event": locked_event},
        )
        if encounter_created:
            _notify_confirmed_encounter(encounter, confirmer, other)
        return ConfirmationResult(
            created=created,
            mutual=True,
            first_name=other.first_name,
        )


def _people_met_target(viewer, encounter_handle):
    """Return the other member only for a currently visible active encounter."""

    if _active_member_reason(viewer) is not None:
        raise LobbyAccessError("not_available")
    try:
        encounter = ConfirmedEncounter.objects.select_related(
            "user_low",
            "user_high",
            "user_low__crushprofile",
            "user_high__crushprofile",
            "user_low__crush_connect_membership",
            "user_high__crush_connect_membership",
            "user_low__event_lobby_consent",
            "user_high__event_lobby_consent",
        ).get(
            opaque_handle=encounter_handle,
            status=ConfirmedEncounter.STATUS_ACTIVE,
        )
    except ConfirmedEncounter.DoesNotExist:
        raise LobbyAccessError("not_available") from None
    if encounter.user_low_id == viewer.pk:
        other = encounter.user_high
    elif encounter.user_high_id == viewer.pk:
        other = encounter.user_low
    else:
        raise LobbyAccessError("not_available")
    if is_blocked_pair(viewer, other) or _active_member_reason(other) is not None:
        raise LobbyAccessError("not_available")
    return encounter, other


def list_people_met(viewer):
    """Return the permanent collection with identity shaped to first name only."""

    if _active_member_reason(viewer) is not None:
        raise LobbyAccessError("not_available")
    hidden_ids = blocked_user_ids(viewer)
    encounters = ConfirmedEncounter.objects.filter(
        Q(user_low=viewer) | Q(user_high=viewer),
        status=ConfirmedEncounter.STATUS_ACTIVE,
    ).select_related(
        "user_low",
        "user_high",
        "user_low__crushprofile",
        "user_high__crushprofile",
        "user_low__crush_connect_membership",
        "user_high__crush_connect_membership",
        "user_low__event_lobby_consent",
        "user_high__event_lobby_consent",
    )
    payload = []
    for encounter in encounters:
        other = (
            encounter.user_high
            if encounter.user_low_id == viewer.pk
            else encounter.user_low
        )
        if other.pk in hidden_ids or _active_member_reason(other) is not None:
            continue
        payload.append(
            {
                "handle": str(encounter.opaque_handle),
                "first_name": other.first_name,
                "photo_url": reverse(
                    "crush_lu:event_lobby:people_met_photo",
                    kwargs={"handle": encounter.opaque_handle, "slot": 1},
                ),
                "profile_url": reverse(
                    "crush_lu:event_lobby:people_met_profile",
                    kwargs={"handle": encounter.opaque_handle},
                ),
            }
        )
    return payload


def people_met_profile(viewer, encounter_handle):
    """Load the other member's current Connect profile after pair authorization."""

    encounter, other = _people_met_target(viewer, encounter_handle)
    target = (
        User.objects.filter(pk=other.pk)
        .select_related("crushprofile", "crush_connect_membership")
        .prefetch_related(
            "crush_connect_membership__interests",
            "crush_connect_membership__qualities",
            "crush_connect_membership__defects",
            "crush_connect_membership__sought_qualities",
            "crush_connect_membership__gate_questions__question",
        )
        .get()
    )
    return {
        "encounter_handle": str(encounter.opaque_handle),
        "member": target,
        "profile": target.crushprofile,
        "membership": target.crush_connect_membership,
        "photo_urls": [
            reverse(
                "crush_lu:event_lobby:people_met_photo",
                kwargs={"handle": encounter.opaque_handle, "slot": slot},
            )
            for slot in (1, 2, 3)
            if getattr(target.crushprofile, f"photo_{slot}")
        ],
    }


def get_people_met_photo(viewer, encounter_handle, slot):
    if slot not in {1, 2, 3}:
        raise LobbyAccessError("not_available")
    _encounter, other = _people_met_target(viewer, encounter_handle)
    photo = getattr(other.crushprofile, f"photo_{slot}")
    if not photo:
        raise LobbyAccessError("not_available")
    return photo
