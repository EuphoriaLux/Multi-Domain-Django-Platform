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

# January admission is explicit evidence, separate from the optional photo badge.
PURPOSE_BADGE_ONLY = "badge_only"
PURPOSE_CONNECT_WEEK = "connect_week"
CONNECT_VIDEO_ADMISSION_POLICY_VERSION = 1
CONNECT_VIDEO_PILOT_GROUP = "connect_video_verification_pilot"

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

# Contact details a member may give after a coach claims them: how they want
# to be reached first and roughly when. Asked once per member per
# CONTACT_REQUEST_COOLDOWN however many coaches claim them, and an answer is
# carried onto a later claim for CONTACT_ANSWER_TTL.
CONTACT_CALL = "call"
CONTACT_SMS = "sms"
CONTACT_WHATSAPP = "whatsapp"
CONTACT_PREFERENCES = (CONTACT_CALL, CONTACT_SMS, CONTACT_WHATSAPP)
CONTACT_WINDOWS = (
    "weekday_morning",
    "weekday_lunch",
    "weekday_evening",
    "weekend_morning",
    "weekend_afternoon",
    "weekend_evening",
)
CONTACT_REQUEST_COOLDOWN = timedelta(days=14)
CONTACT_ANSWER_TTL = timedelta(days=30)
# What the claim managed to send: an email, only the in-app notice (emails
# switched off), or nothing (the send failed).
CONTACT_NOTICE_EMAIL = "email"
CONTACT_NOTICE_BELL = "bell"
CONTACT_NOTICE_FAILED = "failed"
