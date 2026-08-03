"""Shared atomic claims for profile verification paths."""

from collections.abc import Iterable
from datetime import datetime

from crush_lu.models import CrushProfile


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
