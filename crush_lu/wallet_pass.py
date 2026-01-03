from django.utils import timezone

from .models import ReferralCode, EventRegistration
from .referrals import build_referral_url


def build_wallet_pass_barcode_value(profile, request=None, base_url=None):
    """
    Build the QR/barcode payload for wallet passes.
    Embeds the referral URL so scans attribute signups.
    """
    referral_code = ReferralCode.get_or_create_for_profile(profile)
    return build_referral_url(referral_code.code, request=request, base_url=base_url)


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
    registration = (
        EventRegistration.objects.filter(
            user=profile.user,
            event__date_time__gte=timezone.now(),
            status__in=["confirmed", "waitlist"],
        )
        .select_related("event")
        .order_by("event__date_time")
        .first()
    )

    if not registration:
        return None

    event = registration.event
    return {
        "title": event.title,
        "date": event.date_time.strftime("%b %d, %Y"),
        "time": event.date_time.strftime("%I:%M %p"),
        "location": event.location or "",
        "status": registration.status,
    }


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
