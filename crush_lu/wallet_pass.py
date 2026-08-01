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
        .order_by("event__date_time")
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
    next_event = get_next_event_for_pass(profile)
    tier_display = get_membership_tier_display(profile)
    photo_url = get_profile_photo_url(profile, request=request)

    return {
        "display_name": profile.display_name,
        "membership_tier": profile.membership_tier,
        "tier_display": tier_display,
        "referral_points": profile.referral_points,
        "referral_url": referral_url,
        "next_event": next_event,
        "photo_url": photo_url,
        "member_since": profile.created_at.strftime("%Y-%m-%d") if profile.created_at else None,
    }
