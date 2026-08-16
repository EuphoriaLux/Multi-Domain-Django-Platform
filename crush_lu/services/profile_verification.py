"""Shared atomic claims for profile verification paths."""

from collections.abc import Iterable
from datetime import datetime

from crush_lu.models import CrushProfile, EventRegistration


def _clear_door_photo_attestations(profile: CrushProfile) -> None:
    """Drop the door provenance mirroring a photo attestation just revoked.

    ``CrushProfile.photo_verification_key`` is the member's current attestation;
    ``EventRegistration.checkin_attested_photo_key`` is the door's record of
    which scan made it. Revoking the first without the second leaves a
    registration claiming a coach vouched for a photo that has since been
    rejected — and `coach_undo_checkin` reads exactly that field as surviving
    coach-authenticated evidence, so a stale row would preserve a verification
    the rejection was meant to take away.

    Written as a queryset update: this runs from post-save-adjacent coach flows
    and must not re-enter `EventRegistration`'s signals.
    """
    EventRegistration.objects.filter(user_id=profile.user_id).exclude(
        checkin_attested_photo_key="",
    ).update(
        checkin_attested_photo_key="",
        checkin_attested_photo_at=None,
    )


def claim_profile_verification(
    profile: CrushProfile,
    *,
    method: str,
    approved_at: datetime,
    claim_from: Iterable[str] = ("pending",),
) -> bool:
    """Atomically claim the first transition to a verified profile.

    Verification can arrive concurrently from LuxID, an event scan, or a
    coach review. The conditional UPDATE makes exactly one path the owner of
    the transition and its side effects; later paths leave both the stored
    method and approval timestamp untouched.
    """
    claimed = CrushProfile.objects.filter(
        pk=profile.pk,
        verification_status__in=tuple(claim_from),
    ).update(
        is_approved=True,
        approved_at=approved_at,
        verification_status="verified",
        verification_method=method,
    )
    if not claimed:
        return False

    # Keep callers' already-loaded instance consistent with the row just
    # claimed without issuing a second, unconditional model save.
    profile.is_approved = True
    profile.approved_at = approved_at
    profile.verification_status = "verified"
    profile.verification_method = method
    return True


def transition_unverified_profile(
    profile: CrushProfile,
    *,
    target_status: str,
    transition_from: Iterable[str] = ("incomplete", "pending", "rejected"),
) -> bool:
    """Move an unverified profile without clobbering a concurrent verifier.

    Coach review forms can remain open while LuxID or an event scan verifies
    the same member. Rejection and revision decisions may update the profile
    only while it is still in an unverified state; once another path has
    committed ``verified``, the stale form records its review decision without
    silently de-verifying the member.
    """
    transitioned = CrushProfile.objects.filter(
        pk=profile.pk,
        verification_status__in=tuple(transition_from),
    ).update(
        is_approved=False,
        approved_at=None,
        verification_method="",
        verification_status=target_status,
        photo_verification_key="",
        photo_verified_at=None,
    )
    if not transitioned:
        return False

    # Only once the CAS above won: a no-op transition means another path holds
    # the verification, and wiping the door's provenance would take evidence
    # away from a member somebody else just verified.
    _clear_door_photo_attestations(profile)

    profile.is_approved = False
    profile.approved_at = None
    profile.verification_method = ""
    profile.verification_status = target_status
    profile.photo_verification_key = ""
    profile.photo_verified_at = None
    return True


def reject_door_verification(
    profile: CrushProfile,
    *,
    target_status: str = "rejected",
    revert_from: Iterable[str] = ("verified", "pending", "incomplete"),
) -> bool:
    """Explicitly reject or revoke verification for a profile (e.g. door photo mismatch).

    Atomically transitions the profile back to an unverified state (`is_approved=False`,
    `approved_at=None`, `verification_method=""`, `verification_status=target_status`).
    """
    updated = CrushProfile.objects.filter(
        pk=profile.pk,
        verification_status__in=tuple(revert_from),
    ).update(
        is_approved=False,
        approved_at=None,
        verification_method="",
        verification_status=target_status,
        photo_verification_key="",
        photo_verified_at=None,
    )
    if not updated:
        return False

    # See `transition_unverified_profile`: only after the CAS won.
    _clear_door_photo_attestations(profile)

    profile.is_approved = False
    profile.approved_at = None
    profile.verification_method = ""
    profile.verification_status = target_status
    profile.photo_verification_key = ""
    profile.photo_verified_at = None
    return True
