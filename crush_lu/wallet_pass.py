from django.utils import timezone

from .models import ReferralCode, EventRegistration, MeetupEvent
from .models.events import SEAT_HOLDING_STATUSES
from .referrals import build_referral_url

# Registration statuses that can still make an event somebody's "next event" on
# a wallet pass. "waitlist" counts: a waitlisted member is still going, pending
# a seat, and the card names the event either way.
#
# Defined once because the admin bulk actions consume it too. They decide
# whether a status change is worth a wallet refresh by asking whether it moves
# a registration ACROSS this boundary — the member surfaces render only the
# event's title and date, never the registration status, so a move *within* the
# set rebuilds a byte-identical pass. A stale copy of this list would either
# miss real changes or spend a capped refresh budget on no-ops.
PASS_NEXT_EVENT_STATUSES = [*SEAT_HOLDING_STATUSES, "waitlist"]


def build_wallet_pass_barcode_value(profile, request=None, base_url=None):
    """
    Build the QR/barcode payload for wallet passes.
    Embeds the referral URL so scans attribute signups.
    Uses language-neutral URL so users get the site in their browser's preferred language.
    """
    referral_code = ReferralCode.get_or_create_for_profile(profile)
    return build_referral_url(referral_code.code, base_url=base_url, language_neutral=True)


def _next_event_candidates(user_ids, now):
    """Registrations that could be the card's next event, earliest first.

    Shared by the single-profile and bulk selectors below so the rule for what
    a card may display lives in exactly one place. ``end_time`` cannot be part
    of the SQL — it is ``date_time + duration_minutes``, and ``timedelta * F()``
    is unsupported on SQLite — so this is the bounded pre-filter and callers
    apply the precise check in Python.
    """
    return (
        EventRegistration.objects.filter(
            user_id__in=user_ids,
            event__date_time__gte=MeetupEvent.live_lookback_cutoff(now),
            status__in=PASS_NEXT_EVENT_STATUSES,
        )
        # A cancelled event is not anybody's next event. Without this the
        # member card kept advertising it — and worse, cancelling an event
        # rebuilt a byte-identical pass, so the refresh that cancellation
        # triggers accomplished nothing.
        .exclude(event__is_cancelled=True)
        .select_related("event")
        # Tie-broken, not just sorted by start. Two eligible registrations
        # sharing a `date_time` would otherwise be returned in whatever order
        # the database felt like, and the choice has to be REPRODUCIBLE across
        # queries: the single-user call builds the pass a holder is looking at,
        # while the bulk call decides whether an admin action needs to refresh
        # them. Disagree on a tie and the action skips a holder whose card
        # really does show the changed event — stale for good, since Google
        # never polls. It also keeps successive rebuilds of one card from
        # flipping between two tied events.
        .order_by("event__date_time", "event_id", "id")
    )


def get_next_event_registrations(user_ids, now=None):
    """Which registration each user's card is currently showing.

    ``{user_id: EventRegistration}``, users with nothing to show omitted. One
    query for the whole batch, which is the point: the admin bulk actions need
    to know whether the row they are about to change is the one a holder's card
    actually displays, and asking per holder would cost a query each.

    That question matters because both wallet fan-outs are capped. A holder with
    an earlier eligible registration is not showing this event, so refreshing
    them writes a byte-identical object — and on the Google side the cap is a
    hard loss, no poll heals what it skips, so each no-op can leave someone
    whose card IS wrong stale for good.
    """
    now = now or timezone.now()
    chosen = {}
    for candidate in _next_event_candidates(user_ids, now):
        # Ordered by start, so the first ELIGIBLE candidate per user wins. An
        # earlier one that has already ended is skipped without claiming the
        # slot — exactly what the single-profile selector does.
        if candidate.user_id in chosen:
            continue
        if candidate.event.end_time >= now:
            chosen[candidate.user_id] = candidate
    return chosen


def get_next_event_for_pass(profile):
    """
    Returns formatted next event info for wallet passes.

    Args:
        profile: CrushProfile instance

    Returns:
        Dictionary with event info or None if no upcoming events
        {
            'title': 'Speed Dating Night',
            'date': 'Jan 15, 2026',
            'time': '7:00 PM',
            'location': 'Luxembourg City'
        }
    """
    now = timezone.now()
    registration = next(
        (
            candidate
            for candidate in _next_event_candidates([profile.user_id], now)
            if candidate.event.end_time >= now
        ),
        None,
    )

    if not registration:
        return None

    event = registration.event
    result = {
        "title": event.title,
        "date": event.date_time.strftime("%b %d, %Y"),
        "time": event.date_time.strftime("%I:%M %p"),
        "location": event.location or "",
        "status": registration.status,
        "date_time_iso": event.date_time.isoformat(),
    }
    if event.latitude and event.longitude:
        result["latitude"] = float(event.latitude)
        result["longitude"] = float(event.longitude)
    return result


def get_membership_tier_display(profile):
    """
    Returns a display-friendly membership tier string.

    Args:
        profile: CrushProfile instance

    Returns:
        String like "Gold Member" or "Basic Member"
    """
    tier = profile.membership_tier or "basic"
    return f"{tier.capitalize()} Member"


def get_wallet_status_display(profile):
    """Short punchy status string for wallet headers (e.g. '✨ VIP', '🛡️ Verified', '🥇 Gold')."""
    if getattr(profile, "has_active_premium", False):
        return "✨ VIP"
    if getattr(profile, "verification_status", "") == "verified":
        if getattr(profile, "verification_method", "") == "luxid":
            return "🛡️ LuxID"
        return "🛡️ Verified"
    tier = (getattr(profile, "membership_tier", "") or "basic").lower()
    tier_emojis = {
        "gold": "🥇 Gold",
        "silver": "🥈 Silver",
        "bronze": "🥉 Bronze",
        "basic": "💜 Member",
    }
    return tier_emojis.get(tier, "💜 Member")


def get_wallet_verification_badge(profile):
    """Detailed badge display for membership information."""
    if getattr(profile, "has_active_premium", False):
        return "✨ Premium Member"
    if getattr(profile, "verification_status", "") == "verified":
        method = getattr(profile, "verification_method", "")
        if method == "luxid":
            return "🛡️ LuxID Verified"
        elif method == "coach_event":
            return "🛡️ Coach Verified (Event)"
        return "🛡️ Verified Member"
    tier = (getattr(profile, "membership_tier", "") or "basic").capitalize()
    return f"{tier} Member"


def get_profile_photo_url(profile, request=None):
    """
    Returns the URL for the profile's primary photo.
    For Apple Wallet, photos need to be accessible URLs.

    Args:
        profile: CrushProfile instance
        request: Optional HttpRequest for building absolute URLs

    Returns:
        Absolute URL to photo or None
    """
    if not profile.show_photo_on_wallet:
        return None

    if not profile.photo_1:
        return None

    try:
        photo_url = profile.photo_1.url
        if request:
            return request.build_absolute_uri(photo_url)
        return photo_url
    except Exception:
        return None


def build_wallet_pass_data(profile, request=None, base_url=None):
    """
    Build complete wallet pass data for a user profile.

    Args:
        profile: CrushProfile instance
        request: Optional HttpRequest for building absolute URLs
        base_url: Optional base URL override

    Returns:
        Dictionary with all pass data
    """
    referral_url = build_wallet_pass_barcode_value(profile, request=request, base_url=base_url)
    referral_code_obj = ReferralCode.get_or_create_for_profile(profile)
    referral_code = referral_code_obj.code if referral_code_obj else ""
    next_event = get_next_event_for_pass(profile)
    tier_display = get_membership_tier_display(profile)
    photo_url = get_profile_photo_url(profile, request=request)
    created_at = getattr(profile, "created_at", None)
    member_id = f"#{profile.pk:05d}" if profile and profile.pk else "#00001"

    return {
        "display_name": profile.display_name,
        "membership_tier": profile.membership_tier,
        "tier_display": tier_display,
        "status_display": get_wallet_status_display(profile),
        "verification_badge": get_wallet_verification_badge(profile),
        "member_id": member_id,
        "referral_points": profile.referral_points,
        "referral_code": referral_code,
        "referral_url": referral_url,
        "next_event": next_event,
        "photo_url": photo_url,
        "location": getattr(profile, "location", "") or "Luxembourg 🇱🇺",
        "member_since": created_at.strftime("%Y-%m-%d") if created_at else None,
        "member_since_formatted": created_at.strftime("%b %Y") if created_at else None,
        "member_since_full": created_at.strftime("%B %d, %Y") if created_at else None,
    }


# Where a member pass points when the singleton has no URL configured. These
# are Crush.lu's real accounts: the Instagram handle "crush.lu" belongs to an
# unrelated person, which is how the pass shipped with a wrong link for months.
WALLET_SOCIAL_LINK_FALLBACKS = (
    (
        "social_instagram_url",
        "https://www.instagram.com/crushluofficial/",
        "📸 Instagram",
    ),
    ("social_facebook_url", "https://www.facebook.com/crushluxembourg", "👍 Facebook"),
)


def build_wallet_social_links():
    """Social entries for a Google Wallet pass's ``linksModuleData``.

    Reads the same ``CrushSiteConfig`` singleton the site footer and the email
    templates render, so the pass can never disagree with them. Unlike
    ``email_helpers.get_social_links`` this never returns an empty list: a
    blank field falls back to the real account rather than dropping the link,
    because the pass has a fixed layout and a missing entry looks like a bug.
    A config read that fails (no table yet, DB down) also falls back.
    """
    from .models import CrushSiteConfig

    try:
        config = CrushSiteConfig.get_config()
    except Exception:
        config = None

    return [
        {
            "uri": (getattr(config, field, "") or fallback) if config else fallback,
            "description": description,
        }
        for field, fallback, description in WALLET_SOCIAL_LINK_FALLBACKS
    ]
