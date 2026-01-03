import logging

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from .models import ReferralCode, ReferralAttribution, CrushProfile
from .oauth_statekit import get_client_ip

logger = logging.getLogger(__name__)


def ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def build_referral_url(code, request=None, base_url=None, language_neutral=False):
    """
    Build the referral URL for a given code.

    Args:
        code: The referral code string
        request: Optional HttpRequest for building absolute URLs
        base_url: Optional base URL override
        language_neutral: If True, generates URL without language prefix (for wallet passes)

    Returns:
        Absolute referral URL
    """
    if language_neutral:
        # For wallet passes and sharing - no language prefix
        # Users will get the site in their browser's preferred language
        path = f"/r/{code}/"
    else:
        path = reverse('crush_lu:referral_redirect', kwargs={'code': code})

    if request is not None and not language_neutral:
        return request.build_absolute_uri(path)

    base = base_url or getattr(settings, 'CRUSH_BASE_URL', None) or "https://crush.lu"
    return f"{base.rstrip('/')}{path}"


def capture_referral(request, code, source="link"):
    if not code:
        return None

    referral = ReferralCode.objects.filter(code__iexact=code, is_active=True).select_related('referrer').first()
    if not referral:
        return None

    request.session['referral_code'] = referral.code
    request.session['referral_source'] = source
    session_key = ensure_session_key(request)

    ReferralAttribution.objects.get_or_create(
        referral_code=referral,
        referrer=referral.referrer,
        referred_user=None,
        session_key=session_key,
        defaults={
            'ip_address': get_client_ip(request) or "",
            'user_agent': (request.META.get('HTTP_USER_AGENT') or "")[:1000],
            'landing_path': request.get_full_path(),
        }
    )
    return referral


def capture_referral_from_request(request):
    code = request.GET.get('ref')
    if code:
        return capture_referral(request, code, source="query")
    return None


def apply_referral_to_user(request, user):
    code = request.session.pop('referral_code', None)
    if not code:
        return None

    referral = ReferralCode.objects.filter(code__iexact=code, is_active=True).select_related('referrer').first()
    if not referral:
        return None

    session_key = ensure_session_key(request)
    attribution = ReferralAttribution.objects.filter(
        referral_code=referral,
        referrer=referral.referrer,
        referred_user=None,
        session_key=session_key
    ).order_by('-created_at').first()

    if attribution:
        attribution.mark_converted(user)
    else:
        attribution = ReferralAttribution.objects.create(
            referral_code=referral,
            referrer=referral.referrer,
            referred_user=user,
            status=ReferralAttribution.Status.CONVERTED,
            session_key=session_key,
            ip_address=get_client_ip(request) or "",
            user_agent=(request.META.get('HTTP_USER_AGENT') or "")[:1000],
            landing_path=request.get_full_path(),
            converted_at=timezone.now(),
        )

    referral.last_used_at = timezone.now()
    referral.save(update_fields=['last_used_at'])

    # Award signup points to the referrer
    if attribution:
        apply_referral_reward(attribution, reward_type="signup")

    return referral


def update_membership_tier(profile):
    """Update user's membership tier based on total referral points."""
    thresholds = getattr(settings, "MEMBERSHIP_TIER_THRESHOLDS", {
        "bronze": 200,
        "silver": 500,
        "gold": 1000,
    })

    new_tier = "basic"
    for tier, threshold in sorted(thresholds.items(), key=lambda x: x[1], reverse=True):
        if profile.referral_points >= threshold:
            new_tier = tier
            break

    if profile.membership_tier != new_tier:
        old_tier = profile.membership_tier
        profile.membership_tier = new_tier
        profile.save(update_fields=["membership_tier"])
        logger.info(
            "User %s tier upgraded from %s to %s (points: %d)",
            profile.user_id,
            old_tier,
            new_tier,
            profile.referral_points,
        )
        return True
    return False


def apply_referral_reward(attribution, reward_type="signup"):
    """
    Apply referral reward points to the referrer.

    Args:
        attribution: ReferralAttribution instance
        reward_type: 'signup' (when referred user signs up) or
                     'profile_approved' (when their profile is approved)

    Returns:
        Tuple of (points_awarded, new_total_points) or (0, 0) if not applicable
    """
    if attribution.reward_applied:
        logger.debug(
            "Reward already applied for attribution %s", attribution.id
        )
        return 0, attribution.referrer.referral_points

    if attribution.status != ReferralAttribution.Status.CONVERTED:
        logger.debug(
            "Attribution %s not converted yet, skipping reward", attribution.id
        )
        return 0, 0

    # Determine points based on reward type
    if reward_type == "signup":
        points = getattr(settings, "REFERRAL_POINTS_PER_SIGNUP", 100)
    elif reward_type == "profile_approved":
        points = getattr(settings, "REFERRAL_POINTS_PER_PROFILE_APPROVED", 50)
    else:
        logger.warning("Unknown reward type: %s", reward_type)
        return 0, 0

    with transaction.atomic():
        # Update attribution
        attribution.reward_applied = True
        attribution.reward_applied_at = timezone.now()
        attribution.reward_points = F("reward_points") + points
        attribution.save(update_fields=["reward_applied", "reward_applied_at", "reward_points"])

        # Update referrer's points
        CrushProfile.objects.filter(pk=attribution.referrer_id).update(
            referral_points=F("referral_points") + points
        )

        # Refresh to get actual values
        attribution.refresh_from_db()
        attribution.referrer.refresh_from_db()

        # Check for tier upgrade
        update_membership_tier(attribution.referrer)

        logger.info(
            "Awarded %d points to user %s for referral %s (%s). New total: %d",
            points,
            attribution.referrer.user_id,
            attribution.id,
            reward_type,
            attribution.referrer.referral_points,
        )

        return points, attribution.referrer.referral_points


def check_and_apply_signup_reward(user):
    """
    Check if the user was referred and apply signup reward to referrer.
    Called after a user successfully signs up.

    Args:
        user: The newly signed up User instance

    Returns:
        The ReferralAttribution if reward was applied, None otherwise
    """
    attribution = ReferralAttribution.objects.filter(
        referred_user=user,
        status=ReferralAttribution.Status.CONVERTED,
        reward_applied=False,
    ).select_related("referrer").first()

    if not attribution:
        return None

    points_awarded, _ = apply_referral_reward(attribution, reward_type="signup")
    if points_awarded > 0:
        return attribution
    return None


def check_and_apply_profile_approved_reward(profile):
    """
    Check if the profile's user was referred and apply profile approval bonus.
    Called after a profile is approved by a coach.

    Args:
        profile: The CrushProfile instance that was approved

    Returns:
        The ReferralAttribution if reward was applied, None otherwise
    """
    # Find the attribution for this user
    attribution = ReferralAttribution.objects.filter(
        referred_user=profile.user,
        status=ReferralAttribution.Status.CONVERTED,
    ).select_related("referrer").first()

    if not attribution:
        return None

    # Check if signup reward was already applied (profile approval is a bonus)
    if not attribution.reward_applied:
        # Apply signup reward first
        apply_referral_reward(attribution, reward_type="signup")

    # For profile approval bonus, we need a different tracking mechanism
    # since reward_applied is already True after signup
    # We'll use the reward_points field to check if bonus was already given
    signup_points = getattr(settings, "REFERRAL_POINTS_PER_SIGNUP", 100)
    bonus_points = getattr(settings, "REFERRAL_POINTS_PER_PROFILE_APPROVED", 50)

    if attribution.reward_points >= signup_points + bonus_points:
        # Bonus already applied
        return None

    # Apply the bonus
    with transaction.atomic():
        attribution.reward_points = F("reward_points") + bonus_points
        attribution.save(update_fields=["reward_points"])

        CrushProfile.objects.filter(pk=attribution.referrer_id).update(
            referral_points=F("referral_points") + bonus_points
        )

        attribution.refresh_from_db()
        attribution.referrer.refresh_from_db()
        update_membership_tier(attribution.referrer)

        logger.info(
            "Awarded %d bonus points to user %s for referred profile approval",
            bonus_points,
            attribution.referrer.user_id,
        )

    return attribution


def get_referral_stats(profile):
    """
    Get referral statistics for a user's profile.

    Args:
        profile: CrushProfile instance

    Returns:
        Dictionary with referral stats
    """
    from django.db.models import Count, Sum, Q

    stats = ReferralAttribution.objects.filter(
        referrer=profile
    ).aggregate(
        total_clicks=Count("id"),
        total_conversions=Count("id", filter=Q(status=ReferralAttribution.Status.CONVERTED)),
        total_points_earned=Sum("reward_points"),
    )

    return {
        "clicks": stats["total_clicks"] or 0,
        "conversions": stats["total_conversions"] or 0,
        "points_earned": stats["total_points_earned"] or 0,
        "current_points": profile.referral_points,
        "membership_tier": profile.membership_tier,
    }
