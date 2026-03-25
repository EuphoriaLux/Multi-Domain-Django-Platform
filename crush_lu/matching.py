"""
Matching algorithm for Crush.lu.

Combines three signals into a single compatibility score:
- Qualities/Defects matching (70% weight)
- Western zodiac element compatibility (20% weight)
- Chinese zodiac animal compatibility (10% weight)

Formula: score = 0.70 * qualities + 0.20 * zodiac_west + 0.10 * zodiac_cn
When either user disables astrology: score = qualities (100%)
"""

import logging
from datetime import date

from django.db import models, transaction
from django.db.models import Q

logger = logging.getLogger(__name__)

# =============================================================================
# Western Zodiac
# =============================================================================

# (sign, (start_month, start_day), (end_month, end_day), element)
WESTERN_ZODIAC_SIGNS = [
    ("capricorn", (12, 22), (1, 19), "earth"),
    ("aquarius", (1, 20), (2, 18), "air"),
    ("pisces", (2, 19), (3, 20), "water"),
    ("aries", (3, 21), (4, 19), "fire"),
    ("taurus", (4, 20), (5, 20), "earth"),
    ("gemini", (5, 21), (6, 20), "air"),
    ("cancer", (6, 21), (7, 22), "water"),
    ("leo", (7, 23), (8, 22), "fire"),
    ("virgo", (8, 23), (9, 22), "earth"),
    ("libra", (9, 23), (10, 22), "air"),
    ("scorpio", (10, 23), (11, 21), "water"),
    ("sagittarius", (11, 22), (12, 21), "fire"),
]

SIGN_TO_ELEMENT = {sign: element for sign, _, _, element in WESTERN_ZODIAC_SIGNS}

# Element compatibility matrix (symmetric, values 0.0-1.0)
# Same element = 0.85 (very compatible but can lack balance)
# Complementary pairs (fire+air, earth+water) = 1.0
# Neutral = 0.5
# Challenging (fire+water, earth+air) = 0.3
ELEMENT_COMPAT = {
    ("fire", "fire"): 0.85,
    ("fire", "air"): 1.0,
    ("fire", "earth"): 0.5,
    ("fire", "water"): 0.3,
    ("air", "fire"): 1.0,
    ("air", "air"): 0.85,
    ("air", "earth"): 0.5,
    ("air", "water"): 0.3,
    ("earth", "fire"): 0.5,
    ("earth", "air"): 0.5,
    ("earth", "earth"): 0.85,
    ("earth", "water"): 1.0,
    ("water", "fire"): 0.3,
    ("water", "air"): 0.3,
    ("water", "earth"): 1.0,
    ("water", "water"): 0.85,
}

ZODIAC_SIGN_EMOJIS = {
    "aries": "\u2648",
    "taurus": "\u2649",
    "gemini": "\u264a",
    "cancer": "\u264b",
    "leo": "\u264c",
    "virgo": "\u264d",
    "libra": "\u264e",
    "scorpio": "\u264f",
    "sagittarius": "\u2650",
    "capricorn": "\u2651",
    "aquarius": "\u2652",
    "pisces": "\u2653",
}


def get_western_zodiac(birth_date):
    """Return the western zodiac sign for a given birth date.

    Args:
        birth_date: date object

    Returns:
        str: zodiac sign name (lowercase), e.g. "aries"
    """
    if not birth_date:
        return None

    month, day = birth_date.month, birth_date.day

    for sign, (sm, sd), (em, ed), _ in WESTERN_ZODIAC_SIGNS:
        # Handle Capricorn wrapping around year boundary
        if sm > em:  # e.g. Dec 22 - Jan 19
            if (month == sm and day >= sd) or (month == em and day <= ed):
                return sign
        else:
            if (month == sm and day >= sd) or (month == em and day <= ed) or (sm < month < em):
                return sign

    return "capricorn"  # Fallback (shouldn't reach here)


def get_western_element(sign):
    """Return the element (fire/earth/air/water) for a zodiac sign."""
    return SIGN_TO_ELEMENT.get(sign)


def compute_western_zodiac_score(dob_a, dob_b):
    """Compute western zodiac compatibility score (0.0-1.0).

    Based on element compatibility between two zodiac signs.
    """
    if not dob_a or not dob_b:
        return 0.5  # Neutral if missing data

    sign_a = get_western_zodiac(dob_a)
    sign_b = get_western_zodiac(dob_b)

    if not sign_a or not sign_b:
        return 0.5

    elem_a = get_western_element(sign_a)
    elem_b = get_western_element(sign_b)

    return ELEMENT_COMPAT.get((elem_a, elem_b), 0.5)


# =============================================================================
# Chinese Zodiac
# =============================================================================

CHINESE_ANIMALS = [
    "rat", "ox", "tiger", "rabbit", "dragon", "snake",
    "horse", "goat", "monkey", "rooster", "dog", "pig",
]

CHINESE_ANIMAL_EMOJIS = {
    "rat": "\U0001f400",
    "ox": "\U0001f402",
    "tiger": "\U0001f405",
    "rabbit": "\U0001f407",
    "dragon": "\U0001f409",
    "snake": "\U0001f40d",
    "horse": "\U0001f40e",
    "goat": "\U0001f410",
    "monkey": "\U0001f412",
    "rooster": "\U0001f413",
    "dog": "\U0001f415",
    "pig": "\U0001f416",
}

# Trine groups: animals in the same trine are highly compatible
CHINESE_TRINES = [
    frozenset({"rat", "dragon", "monkey"}),
    frozenset({"ox", "snake", "rooster"}),
    frozenset({"tiger", "horse", "dog"}),
    frozenset({"rabbit", "goat", "pig"}),
]

# Six harmony pairs (secret friends)
CHINESE_HARMONY_PAIRS = {
    frozenset({"rat", "ox"}),
    frozenset({"tiger", "pig"}),
    frozenset({"rabbit", "dog"}),
    frozenset({"dragon", "rooster"}),
    frozenset({"snake", "monkey"}),
    frozenset({"horse", "goat"}),
}

# Clash pairs (directly opposing)
CHINESE_CLASH_PAIRS = {
    frozenset({"rat", "horse"}),
    frozenset({"ox", "goat"}),
    frozenset({"tiger", "monkey"}),
    frozenset({"rabbit", "rooster"}),
    frozenset({"dragon", "dog"}),
    frozenset({"snake", "pig"}),
}

# Reference year for Chinese zodiac cycle: 1924 = Year of the Rat
CHINESE_REFERENCE_YEAR = 1924


def get_chinese_zodiac(birth_date):
    """Return the Chinese zodiac animal for a given birth date.

    Uses a simplified Feb 4 cutoff (approximate Lichun / start of spring).
    People born before Feb 4 belong to the previous year's animal.

    Args:
        birth_date: date object

    Returns:
        str: animal name (lowercase), e.g. "dragon"
    """
    if not birth_date:
        return None

    year = birth_date.year
    # Approximate Chinese new year cutoff
    if birth_date.month < 2 or (birth_date.month == 2 and birth_date.day < 4):
        year -= 1

    index = (year - CHINESE_REFERENCE_YEAR) % 12
    return CHINESE_ANIMALS[index]


def compute_chinese_zodiac_score(dob_a, dob_b):
    """Compute Chinese zodiac compatibility score (0.0-1.0).

    Same trine = 1.0, harmony pair = 0.9, same animal = 0.8,
    neutral = 0.6, clash = 0.2.
    """
    if not dob_a or not dob_b:
        return 0.5

    animal_a = get_chinese_zodiac(dob_a)
    animal_b = get_chinese_zodiac(dob_b)

    if not animal_a or not animal_b:
        return 0.5

    pair = frozenset({animal_a, animal_b})

    # Same animal
    if animal_a == animal_b:
        return 0.8

    # Same trine (best compatibility)
    for trine in CHINESE_TRINES:
        if animal_a in trine and animal_b in trine:
            return 1.0

    # Harmony pair (secret friends)
    if pair in CHINESE_HARMONY_PAIRS:
        return 0.9

    # Clash pair (worst compatibility)
    if pair in CHINESE_CLASH_PAIRS:
        return 0.2

    # Neutral
    return 0.6


# =============================================================================
# Quality/Defect Scoring
# =============================================================================


def compute_quality_score(profile_a, profile_b):
    """Compute bidirectional quality matching score (0.0-1.0).

    For each direction:
        score = |sought_qualities ∩ other's qualities| / 5

    Final score = average of both directions.

    Returns 0.5 (neutral) if either user hasn't set sought_qualities.
    """
    sought_a = set(profile_a.sought_qualities.values_list("slug", flat=True))
    sought_b = set(profile_b.sought_qualities.values_list("slug", flat=True))

    # If either user hasn't set preferences, return neutral
    if not sought_a or not sought_b:
        return 0.5

    has_a = set(profile_a.qualities.values_list("slug", flat=True))
    has_b = set(profile_b.qualities.values_list("slug", flat=True))

    # A's sought qualities found in B's qualities
    score_a_to_b = len(sought_a & has_b) / 5 if sought_a else 0
    # B's sought qualities found in A's qualities
    score_b_to_a = len(sought_b & has_a) / 5 if sought_b else 0

    return (score_a_to_b + score_b_to_a) / 2


# =============================================================================
# Combined Matching Score
# =============================================================================

# Score weights
WEIGHT_QUALITIES = 0.70
WEIGHT_ZODIAC_WEST = 0.20
WEIGHT_ZODIAC_CN = 0.10

# Score thresholds for display labels
THRESHOLD_EXCELLENT = 0.80
THRESHOLD_GOOD = 0.60
THRESHOLD_POSSIBLE = 0.40


def compute_match_score(profile_a, profile_b):
    """Compute the combined match score between two profiles.

    Returns a dict with all sub-scores and the final weighted score.
    If either user has astro_enabled=False, only qualities count.

    Args:
        profile_a: CrushProfile instance
        profile_b: CrushProfile instance

    Returns:
        dict: {
            'score_qualities': float,
            'score_zodiac_west': float,
            'score_zodiac_cn': float,
            'score_final': float,
        }
    """
    q_score = compute_quality_score(profile_a, profile_b)

    astro_enabled = profile_a.astro_enabled and profile_b.astro_enabled

    if astro_enabled and profile_a.date_of_birth and profile_b.date_of_birth:
        z_west = compute_western_zodiac_score(
            profile_a.date_of_birth, profile_b.date_of_birth
        )
        z_cn = compute_chinese_zodiac_score(
            profile_a.date_of_birth, profile_b.date_of_birth
        )
        final = (
            WEIGHT_QUALITIES * q_score
            + WEIGHT_ZODIAC_WEST * z_west
            + WEIGHT_ZODIAC_CN * z_cn
        )
    else:
        z_west = 0.0
        z_cn = 0.0
        final = q_score

    return {
        "score_qualities": round(q_score, 4),
        "score_zodiac_west": round(z_west, 4),
        "score_zodiac_cn": round(z_cn, 4),
        "score_final": round(final, 4),
    }


def get_score_label(score):
    """Return a display label for a match score.

    Returns None for scores below 40% (should not be shown).
    """
    if score >= THRESHOLD_EXCELLENT:
        return "excellent"
    elif score >= THRESHOLD_GOOD:
        return "good"
    elif score >= THRESHOLD_POSSIBLE:
        return "possible"
    return None


def get_score_display(score):
    """Return a user-friendly display dict for a match score."""
    label = get_score_label(score)
    if label is None:
        return None

    DISPLAY_MAP = {
        "excellent": {
            "label": "Excellent match",
            "color": "text-green-600 dark:text-green-400",
            "bg_color": "bg-green-100 dark:bg-green-900/30",
            "hex": "#0F6E56",
        },
        "good": {
            "label": "Good match",
            "color": "text-blue-600 dark:text-blue-400",
            "bg_color": "bg-blue-100 dark:bg-blue-900/30",
            "hex": "#185FA5",
        },
        "possible": {
            "label": "Possible match",
            "color": "text-gray-600 dark:text-gray-400",
            "bg_color": "bg-gray-100 dark:bg-gray-800",
            "hex": "#888780",
        },
    }
    return DISPLAY_MAP[label]


# =============================================================================
# Score Persistence
# =============================================================================


def update_match_scores_for_user(user):
    """Recalculate all match scores for a given user against all other
    approved profiles that have sought_qualities set.

    Convention: user_a.pk < user_b.pk to avoid duplicates.

    Returns:
        int: number of scores created or updated
    """
    from crush_lu.models import CrushProfile, MatchScore

    try:
        profile = CrushProfile.objects.get(user=user)
    except CrushProfile.DoesNotExist:
        return 0

    if not profile.is_approved:
        return 0

    # Find all other approved profiles with at least some traits set
    other_profiles = (
        CrushProfile.objects.filter(is_approved=True, is_active=True)
        .exclude(user=user)
        .select_related("user")
        .prefetch_related("qualities", "defects", "sought_qualities")
    )

    count = 0
    for other_profile in other_profiles:
        scores = compute_match_score(profile, other_profile)

        # Enforce user_a.pk < user_b.pk
        if user.pk < other_profile.user.pk:
            user_a, user_b = user, other_profile.user
        else:
            user_a, user_b = other_profile.user, user

        MatchScore.objects.update_or_create(
            user_a=user_a,
            user_b=user_b,
            defaults={
                "score_qualities": scores["score_qualities"],
                "score_zodiac_west": scores["score_zodiac_west"],
                "score_zodiac_cn": scores["score_zodiac_cn"],
                "score_final": scores["score_final"],
            },
        )
        count += 1

    logger.info(
        "Updated %d match scores for user %s (pk=%s)", count, user, user.pk
    )
    return count


def get_matches_for_user(user, min_score=THRESHOLD_POSSIBLE):
    """Get all match scores for a user, ordered by score descending.

    Returns MatchScore queryset with the other user annotated.
    Filters out scores below min_score.

    Args:
        user: User instance
        min_score: minimum score threshold (default 0.40)

    Returns:
        QuerySet of MatchScore objects
    """
    from crush_lu.models import MatchScore

    return (
        MatchScore.objects.filter(
            Q(user_a=user) | Q(user_b=user),
            score_final__gte=min_score,
        )
        .select_related("user_a", "user_b")
        .order_by("-score_final")
    )
