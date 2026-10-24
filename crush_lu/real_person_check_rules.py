"""Rules for the Crush Connect real-person check, free of model and service imports.

Models, services and views all read these, and ``services.crush_connect`` runs
``get_user_model()`` at import time, so the constants live here where a model
module can import them without an app-registry cycle.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from datetime import timedelta

# Verification methods that mean a coach stood in front of the member. They
# are one arm of "coach-attested attendance" in the Connect identity gate and
# in CrushProfile.has_attended_event; a member with one never needs a call.
COACH_ATTESTED_VERIFICATION_METHODS = ("coach_event", "premium_coach")

MEDIUM_VIDEO = "video"
MEDIUM_VOICE = "voice"
MEDIUMS = (MEDIUM_VIDEO, MEDIUM_VOICE)

# Every key must be ticked for a pass; video adds the two keys that tie the
# live person to photo_1. The server rejects any other key.
REQUIRED_CHECKLIST_KEYS = {
    MEDIUM_VOICE: ("spoke_live", "name_matches", "age_matches", "badge_explained"),
    MEDIUM_VIDEO: (
        "spoke_live",
        "name_matches",
        "age_matches",
        "badge_explained",
        "face_matches_photo",
        "live_camera",
    ),
}
CHECKLIST_KEYS = REQUIRED_CHECKLIST_KEYS[MEDIUM_VIDEO]

# A phone check needs room for call-backs, so the lease is days, not the
# photo deck's ten minutes. Each logged contact attempt renews it.
CLAIM_TTL = timedelta(hours=72)
MAX_OPEN_CLAIMS_PER_COACH = 10
DECLINE_COOLDOWN = timedelta(days=90)
NOTES_MAX_LENGTH = 1000
