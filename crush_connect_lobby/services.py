import hashlib
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from crush_lu.models import CrushConnectMembership, CrushProfile
from crush_lu.models.moderation import UserBlock
from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_connect_lobby.models import (
    EventLobbyParticipation,
    EventMeetSignal,
    EventMeetingConfirmation,
    ConfirmedEncounter,
    ConfirmedEncounterRemovalRequest,
)

User = get_user_model()


def get_lobby_phase(event):
    """
    Derives the active phase of the event lobby based on server time.
    """
    now = timezone.now()
    event_end = event.date_time + timedelta(minutes=event.duration_minutes)
    recap_end = event_end + timedelta(hours=48)
    if now < event_end:
        return "live"
    elif now < recap_end:
        return "recap"
    else:
        return "closed"


def generate_opaque_handle(user, event):
    """
    Generates a deterministic event-scoped unique handle for a participant
    to hide their real user ID from the client.
    """
    user_id = user.pk if hasattr(user, "pk") else user
    event_id = event.pk if hasattr(event, "pk") else event
    data = f"{user_id}-{event_id}-{settings.SECRET_KEY}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def check_eligibility(user, event) -> bool:
    """
    Applies the full active Crush Connect eligibility gate (§5.1).
    """
    if not user or not user.is_authenticated:
        return False

    # 1. Event published and not cancelled
    if not event.is_published or event.is_cancelled:
        return False

    # 2. EventRegistration for this event has status="attended"
    reg = EventRegistration.objects.filter(event=event, user=user).first()
    if not reg or reg.status != "attended":
        return False

    # 3. CrushConnectMembership exists, onboarded_at is non-null, excluded_by_coach is false
    membership = getattr(user, "crush_connect_membership", None)
    if not membership or not membership.onboarded_at or membership.excluded_by_coach:
        return False

    # 4. Crush profile is active/approved
    profile = getattr(user, "crushprofile", None)
    if not profile or not (profile.verification_status == "verified" or profile.is_approved):
        return False

    # 5. LuxID connected
    if not profile.has_luxid_connected:
        return False

    # 6. Has lobby consent given
    if not membership.lobby_consent_given:
        return False

    # 7. Has usable primary photo (photo_1)
    if not profile.photo_1:
        return False

    # 8. Not banned or blocked from Crush Connect
    if hasattr(user, "data_consent") and user.data_consent.crushlu_banned:
        return False

    return True


def evaluate_and_join_lobby(user, event, source="checkin"):
    """
    Idempotently creates an EventLobbyParticipation if the user is eligible.
    """
    if not check_eligibility(user, event):
        return None

    # Eligibility check succeeded, let's verify event phase is not closed
    phase = get_lobby_phase(event)
    if phase == "closed":
        return None

    reg = EventRegistration.objects.get(event=event, user=user)

    participation, created = EventLobbyParticipation.objects.get_or_create(
        event=event,
        user=user,
        defaults={
            "event_registration": reg,
            "eligibility_source": source,
        }
    )
    return participation


def list_participants(user, event):
    """
    Lists the roster of participants formatted with server-side identity shaping.
    - Hides user IDs and names before mutual reveal.
    - Returns a list of dicts: {handle, photo_url, is_revealed, first_name, has_sent_signal, already_met, is_live_mutual}.
    - In recap phase, sorts live mutuals first and keeps their names visible.
    """
    phase = get_lobby_phase(event)
    if phase == "closed" or not check_eligibility(user, event):
        return []

    # Get blocked IDs
    blocked_ids = UserBlock.objects.blocked_ids_for(user)

    # Get all participation rows for the event (excluding self)
    participations = EventLobbyParticipation.objects.filter(event=event).exclude(user=user).select_related("user", "user__crushprofile", "user__crush_connect_membership")

    roster = []
    # Fetch user's outgoing signals and confirmations
    outgoing_signals = set(EventMeetSignal.objects.filter(event=event, sender=user).values_list("recipient_id", flat=True))
    incoming_signals = set(EventMeetSignal.objects.filter(event=event, recipient=user).values_list("sender_id", flat=True))
    outgoing_confirmations = set(EventMeetingConfirmation.objects.filter(event=event, confirmer=user).values_list("other_user_id", flat=True))

    for p in participations:
        other_user = p.user
        if other_user.pk in blocked_ids:
            continue

        # Re-check other user's eligibility dynamically
        if not check_eligibility(other_user, event):
            continue

        # Check existing confirmed encounter
        low_id, high_id = min(user.pk, other_user.pk), max(user.pk, other_user.pk)
        encounter = ConfirmedEncounter.objects.filter(user_low_id=low_id, user_high_id=high_id).first()
        already_met = encounter is not None and encounter.status == "active"

        # Check live mutual signal
        is_live_mutual = (other_user.pk in outgoing_signals) and (other_user.pk in incoming_signals)

        # Check if first name is revealed (either live mutual or already met)
        is_revealed = is_live_mutual or already_met

        handle = generate_opaque_handle(other_user, event)
        first_name = other_user.first_name if is_revealed else None
        
        from django.urls import reverse
        photo_url = reverse(
            "crush_connect_lobby:serve_participant_photo",
            kwargs={"event_id": event.id, "handle": handle}
        )

        roster.append({
            "handle": handle,
            "photo_url": photo_url,
            "is_revealed": is_revealed,
            "first_name": first_name,
            "has_sent_signal": other_user.pk in outgoing_signals,
            "has_confirmed_meeting": other_user.pk in outgoing_confirmations,
            "already_met": already_met,
            "is_live_mutual": is_live_mutual,
            "joined_at": p.joined_at,
            "user_id": other_user.pk, # keep internally for sorting
        })

    # Sort rules:
    # In recap phase: live mutuals sort first, then by joined_at descending
    # In live phase: default order is stable by join time, newest first
    if phase == "recap":
        roster.sort(key=lambda x: (not x["is_live_mutual"], x["joined_at"]), reverse=True)
    else:
        roster.sort(key=lambda x: x["joined_at"], reverse=True)

    # Strip out user_id before returning to template/API to enforce privacy
    for item in roster:
        item.pop("user_id")

    return roster


def send_meet_signal(sender, recipient_handle, event):
    """
    Sends a live meet signal from sender to recipient using their handle.
    Enforces the quota of exactly 3 outgoing signals.
    """
    phase = get_lobby_phase(event)
    if phase != "live":
        raise ValidationError("Meet signals can only be sent during the live event.")

    if not check_eligibility(sender, event):
        raise ValidationError("Sender is not eligible for this lobby.")

    # Find the recipient by handle
    recipient = None
    for p in EventLobbyParticipation.objects.filter(event=event).select_related("user"):
        if generate_opaque_handle(p.user, event) == recipient_handle:
            recipient = p.user
            break

    if not recipient:
        raise ValidationError("Recipient not found in this event lobby.")

    if not check_eligibility(recipient, event):
        raise ValidationError("Recipient is no longer eligible.")

    if sender == recipient:
        raise ValidationError("Cannot send signal to yourself.")

    # Block check
    if UserBlock.objects.between(sender, recipient).exists():
        raise ValidationError("Cannot send signal due to block.")

    # Check for existing encounter
    low_id, high_id = min(sender.pk, recipient.pk), max(sender.pk, recipient.pk)
    if ConfirmedEncounter.objects.filter(user_low_id=low_id, user_high_id=high_id, status="active").exists():
        raise ValidationError("You have already met this person.")

    # Execute inside transaction with row locks to enforce quota under concurrency
    with transaction.atomic():
        # Get count of sent signals for this event
        sent_count = EventMeetSignal.objects.select_for_update().filter(event=event, sender=sender).count()
        if sent_count >= 3:
            raise ValidationError("You have already used all 3 meet signals for this event.")

        # Create signal idempotently
        signal, created = EventMeetSignal.objects.get_or_create(
            event=event,
            sender=sender,
            recipient=recipient,
        )

        # Check for mutual signal
        reverse_exists = EventMeetSignal.objects.filter(event=event, sender=recipient, recipient=sender).exists()
        if reverse_exists:
            # Mark both as mutual revealed
            now = timezone.now()
            signal.mutual_revealed_at = now
            signal.save(update_fields=["mutual_revealed_at"])
            EventMeetSignal.objects.filter(event=event, sender=recipient, recipient=sender).update(
                mutual_revealed_at=now
            )
            return {"status": "mutual", "first_name": recipient.first_name}

        return {"status": "sent"}


def confirm_meeting(confirmer, other_handle, event):
    """
    Confirms meeting with a participant during the 48-hour recap window.
    If reciprocal confirmation is found, creates a ConfirmedEncounter.
    """
    phase = get_lobby_phase(event)
    if phase != "recap":
        raise ValidationError("Meeting confirmations are only allowed during the 48-hour recap.")

    if not check_eligibility(confirmer, event):
        raise ValidationError("Confirmer is not eligible.")

    # Find other user by handle
    other_user = None
    for p in EventLobbyParticipation.objects.filter(event=event).select_related("user"):
        if generate_opaque_handle(p.user, event) == other_handle:
            other_user = p.user
            break

    if not other_user:
        raise ValidationError("Recipient not found in this event lobby.")

    if not check_eligibility(other_user, event):
        raise ValidationError("Recipient is no longer eligible.")

    if confirmer == other_user:
        raise ValidationError("Cannot confirm meeting with yourself.")

    if UserBlock.objects.between(confirmer, other_user).exists():
        raise ValidationError("Cannot confirm meeting due to block.")

    # Execute in transaction to avoid simultaneous creation race conditions
    with transaction.atomic():
        confirmation, created = EventMeetingConfirmation.objects.get_or_create(
            event=event,
            confirmer=confirmer,
            other_user=other_user,
        )

        # Check reciprocal confirmation
        reverse_exists = EventMeetingConfirmation.objects.filter(
            event=event, confirmer=other_user, other_user=confirmer
        ).exists()

        if reverse_exists:
            # Create permanent encounter
            low_id, high_id = min(confirmer.pk, other_user.pk), max(confirmer.pk, other_user.pk)
            encounter, enc_created = ConfirmedEncounter.objects.get_or_create(
                user_low_id=low_id,
                user_high_id=high_id,
                defaults={
                    "created_from_event": event,
                    "status": "active",
                }
            )
            if not enc_created and encounter.status != "active":
                encounter.status = "active"
                encounter.save(update_fields=["status"])

            return {"status": "encounter_created", "first_name": other_user.first_name}

        return {"status": "confirmed"}


def get_people_ive_met(user):
    """
    Returns a list of confirmed active encounters for the given user, sorted chronologically (newest first).
    Only returns encounters where both users remain active and neither has blocked the other.
    """
    # Verify the calling user is active and has active Connect membership
    membership = getattr(user, "crush_connect_membership", None)
    if not membership or not membership.onboarded_at or membership.excluded_by_coach:
        return []

    # Get blocked user IDs
    blocked_ids = UserBlock.objects.blocked_ids_for(user)

    # Fetch active encounters
    encounters = ConfirmedEncounter.objects.filter(
        (Q(user_low=user) | Q(user_high=user)) & Q(status="active")
    ).select_related("user_low", "user_low__crushprofile", "user_low__crush_connect_membership", "user_high", "user_high__crushprofile", "user_high__crush_connect_membership")
    encounters = encounters.order_by("-created_at")

    results = []
    for enc in encounters:
        other_user = enc.user_high if enc.user_low == user else enc.user_low
        
        if other_user.pk in blocked_ids:
            continue

        # Re-check other user's active Connect membership and profile status
        other_membership = getattr(other_user, "crush_connect_membership", None)
        if not other_membership or not other_membership.onboarded_at or other_membership.excluded_by_coach:
            continue

        other_profile = getattr(other_user, "crushprofile", None)
        if not other_profile or not (other_profile.verification_status == "verified" or other_profile.is_approved):
            continue

        results.append({
            "encounter_id": enc.id,
            "first_name": other_user.first_name,
            "photo_url": other_profile.photo_1.url if other_profile.photo_1 else None,
            "user_id": other_user.pk,
            "created_at": enc.created_at,
        })

    return results


def request_encounter_removal(user, encounter_id, reason, details=""):
    """
    Submits a request to remove a confirmed encounter.
    Hides the encounter immediately by setting status to 'removal_pending'.
    """
    encounter = ConfirmedEncounter.objects.filter(
        Q(id=encounter_id) & (Q(user_low=user) | Q(user_high=user))
    ).first()

    if not encounter:
        raise ValidationError("Encounter not found or access denied.")

    with transaction.atomic():
        encounter.status = "removal_pending"
        encounter.hidden_at = timezone.now()
        encounter.save(update_fields=["status", "hidden_at"])

        request = ConfirmedEncounterRemovalRequest.objects.create(
            encounter=encounter,
            requested_by=user,
            reason=reason,
            details=details,
            status="pending",
        )

    return request


def review_encounter_removal(coach, request_id, approved, resolution_notes=""):
    """
    Moderator / Coach review action on a removal request.
    If approved, sets encounter status to 'removed'.
    If rejected, sets status back to 'active'.
    """
    req = ConfirmedEncounterRemovalRequest.objects.filter(id=request_id).first()
    if not req:
        raise ValidationError("Removal request not found.")

    with transaction.atomic():
        req.status = "approved" if approved else "rejected"
        req.reviewed_by_coach = coach
        req.reviewed_at = timezone.now()
        req.resolution_notes = resolution_notes
        req.save()

        encounter = req.encounter
        if approved:
            encounter.status = "removed"
            encounter.removed_at = timezone.now()
            encounter.save(update_fields=["status", "removed_at"])
        else:
            # If rejected, restore to active
            encounter.status = "active"
            encounter.hidden_at = None
            encounter.save(update_fields=["status", "hidden_at"])

    return req
