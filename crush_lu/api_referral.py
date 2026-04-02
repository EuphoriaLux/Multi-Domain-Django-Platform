"""
Referral API endpoints for Crush.lu wallet integration.

Provides endpoints for:
- GET /api/referral/me/ - Get user's referral code, URL, stats, and points
- POST /api/referral/redeem/ - Redeem points for rewards
"""
import logging

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import CrushProfile, ReferralCode
from .referrals import get_referral_stats
from .qr_utils import generate_qr_code_base64

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def referral_me(request):
    """
    GET /api/referral/me/

    Returns the user's referral code, URL, QR code, and statistics.

    Response:
    {
        "code": "ABC123XY",
        "url": "https://crush.lu/r/ABC123XY/",
        "qr_code_base64": "data:image/png;base64,...",
        "stats": {
            "clicks": 10,
            "conversions": 3,
            "points_earned": 300
        },
        "current_points": 450,
        "membership_tier": "bronze",
        "tier_progress": {
            "current_tier": "bronze",
            "next_tier": "silver",
            "points_to_next": 50
        }
    }
    """
    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        return Response(
            {"error": "Profile not found. Please create a profile first."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Get or create referral code
    referral_code = ReferralCode.get_or_create_for_profile(profile)

    # Build referral URL
    base_url = getattr(settings, "CRUSH_BASE_URL", None)
    if not base_url:
        base_url = request.build_absolute_uri("/").rstrip("/")
    referral_url = referral_code.get_referral_url(base_url=base_url)

    # Generate QR code
    try:
        qr_code_base64 = generate_qr_code_base64(referral_url)
    except Exception as e:
        logger.warning("Failed to generate QR code: %s", e)
        qr_code_base64 = None

    # Get referral stats
    stats = get_referral_stats(profile)

    # Calculate tier progress
    thresholds = getattr(settings, "MEMBERSHIP_TIER_THRESHOLDS", {
        "bronze": 200,
        "silver": 500,
        "gold": 1000,
    })

    tier_order = ["basic", "bronze", "silver", "gold"]
    current_tier_index = tier_order.index(profile.membership_tier)

    tier_progress = {
        "current_tier": profile.membership_tier,
        "next_tier": None,
        "points_to_next": None,
    }

    if current_tier_index < len(tier_order) - 1:
        next_tier = tier_order[current_tier_index + 1]
        next_threshold = thresholds.get(next_tier, 0)
        tier_progress["next_tier"] = next_tier
        tier_progress["points_to_next"] = max(0, next_threshold - profile.referral_points)

    return Response({
        "code": referral_code.code,
        "url": referral_url,
        "qr_code_base64": qr_code_base64,
        "stats": {
            "clicks": stats["clicks"],
            "conversions": stats["conversions"],
            "points_earned": stats["points_earned"],
        },
        "current_points": profile.referral_points,
        "membership_tier": profile.membership_tier,
        "tier_progress": tier_progress,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def redeem_points(request):
    """
    POST /api/referral/redeem/

    Redeem points for rewards.

    Request body:
    {
        "reward_type": "event_discount" | "priority_access" | "visibility_boost",
        "event_id": 123  // Required for event_discount
    }

    Response:
    {
        "success": true,
        "message": "Reward redeemed successfully",
        "reward": {
            "type": "event_discount",
            "value": "€2 discount",
            "points_spent": 100
        },
        "new_balance": 350
    }
    """
    try:
        profile = request.user.crushprofile
    except CrushProfile.DoesNotExist:
        return Response(
            {"error": "Profile not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    reward_type = request.data.get("reward_type")
    if not reward_type:
        return Response(
            {"error": "reward_type is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Define reward costs
    reward_costs = {
        "event_discount": getattr(settings, "POINTS_PER_EURO_DISCOUNT", 50),
        "priority_access": getattr(settings, "POINTS_FOR_PRIORITY_ACCESS", 200),
        "visibility_boost": getattr(settings, "POINTS_FOR_VISIBILITY_BOOST", 150),
    }

    if reward_type not in reward_costs:
        return Response(
            {"error": f"Invalid reward_type. Valid options: {list(reward_costs.keys())}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    points_required = reward_costs[reward_type]

    # Process the reward atomically with select_for_update to prevent double-spend
    with transaction.atomic():
        profile = CrushProfile.objects.select_for_update().get(pk=profile.pk)

        if profile.referral_points < points_required:
            return Response(
                {
                    "error": "Insufficient points",
                    "points_required": points_required,
                    "current_points": profile.referral_points,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Deduct points
        CrushProfile.objects.filter(pk=profile.pk).update(
            referral_points=F("referral_points") - points_required
        )
        profile.refresh_from_db()

        # Record the redemption (for auditing)
        # Note: In a full implementation, you'd create a PointsRedemption model
        # to track all redemptions. For now, we'll log it.
        logger.info(
            "User %s redeemed %d points for %s. New balance: %d",
            request.user.id,
            points_required,
            reward_type,
            profile.referral_points,
        )

        # Prepare reward details based on type
        if reward_type == "event_discount":
            euros = points_required // getattr(settings, "POINTS_PER_EURO_DISCOUNT", 50)
            reward_value = f"€{euros} discount"
        elif reward_type == "priority_access":
            reward_value = "Priority event registration unlocked"
        elif reward_type == "visibility_boost":
            reward_value = "Profile visibility boost activated"
        else:
            reward_value = reward_type

    return Response({
        "success": True,
        "message": "Reward redeemed successfully",
        "reward": {
            "type": reward_type,
            "value": reward_value,
            "points_spent": points_required,
        },
        "new_balance": profile.referral_points,
    })
