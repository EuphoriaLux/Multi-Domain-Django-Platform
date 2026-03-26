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
from django.utils.translation import gettext_lazy as _

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

ZODIAC_SIGN_LABELS = {
    "aries": _("Aries"),
    "taurus": _("Taurus"),
    "gemini": _("Gemini"),
    "cancer": _("Cancer"),
    "leo": _("Leo"),
    "virgo": _("Virgo"),
    "libra": _("Libra"),
    "scorpio": _("Scorpio"),
    "sagittarius": _("Sagittarius"),
    "capricorn": _("Capricorn"),
    "aquarius": _("Aquarius"),
    "pisces": _("Pisces"),
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

CHINESE_ANIMAL_LABELS = {
    "rat": _("Rat"),
    "ox": _("Ox"),
    "tiger": _("Tiger"),
    "rabbit": _("Rabbit"),
    "dragon": _("Dragon"),
    "snake": _("Snake"),
    "horse": _("Horse"),
    "goat": _("Goat"),
    "monkey": _("Monkey"),
    "rooster": _("Rooster"),
    "dog": _("Dog"),
    "pig": _("Pig"),
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
# Language Compatibility
# =============================================================================


def compute_language_score(profile_a, profile_b):
    """Compute language overlap score (0.0-1.0).

    Based on shared event languages relative to the smaller set.
    Returns 0.5 (neutral) if either profile has no languages set.
    """
    langs_a = set(profile_a.event_languages or [])
    langs_b = set(profile_b.event_languages or [])

    if not langs_a or not langs_b:
        return 0.5

    shared = langs_a & langs_b
    if not shared:
        return 0.0

    return len(shared) / min(len(langs_a), len(langs_b))


# =============================================================================
# Age Fit
# =============================================================================


def _age_fit_one_direction(actual_age, pref_min, pref_max):
    """Score how well actual_age fits within [pref_min, pref_max].

    Default range (18-99) = 0.75 (open to anyone).
    Inside range = 0.85-1.0 (higher near center).
    Outside range = decays 0.1/year, floor 0.0.
    """
    if pref_min == 18 and pref_max == 99:
        return 0.75

    if pref_min <= actual_age <= pref_max:
        range_size = pref_max - pref_min
        if range_size == 0:
            return 1.0
        center = (pref_min + pref_max) / 2
        distance = abs(actual_age - center) / (range_size / 2)
        return 1.0 - 0.15 * distance

    if actual_age < pref_min:
        gap = pref_min - actual_age
    else:
        gap = actual_age - pref_max

    return max(0.0, 0.7 - gap * 0.1)


def compute_age_fit_score(profile_a, profile_b):
    """Compute mutual age fit score (0.0-1.0).

    Average of: how well B fits A's age preferences,
    and how well A fits B's age preferences.
    Returns 0.5 if either age is unknown.
    """
    age_a = profile_a.age
    age_b = profile_b.age

    if age_a is None or age_b is None:
        return 0.5

    fit_a = _age_fit_one_direction(age_b, profile_a.preferred_age_min, profile_a.preferred_age_max)
    fit_b = _age_fit_one_direction(age_a, profile_b.preferred_age_min, profile_b.preferred_age_max)

    return (fit_a + fit_b) / 2


# =============================================================================
# Hard Filters (pre-scoring)
# =============================================================================


def has_matching_profile(profile):
    """Check if a profile has completed the minimum required fields for matching.

    Requires: qualities, defects, and sought_qualities all set.
    """
    return (
        profile.qualities.exists()
        and profile.defects.exists()
        and profile.sought_qualities.exists()
    )


def passes_hard_filters(profile_a, profile_b):
    """Check if two profiles pass hard compatibility filters.

    Returns False if the pair should NOT be scored at all:
    - Either profile hasn't completed qualities/defects/sought_qualities
    - Mutual gender mismatch (when both have preferred_genders set)
    - Zero shared languages (when both have languages set)
    - Mutual age mismatch (both must fit each other's non-default range)
    """
    # Profile completeness — both must have set their traits
    if not has_matching_profile(profile_a) or not has_matching_profile(profile_b):
        return False

    # Mutual gender filter — only applies when BOTH have set preferred_genders
    # (empty means they haven't filled out their ideal crush yet, not "open to all")
    genders_a = profile_a.preferred_genders or []
    genders_b = profile_b.preferred_genders or []
    if genders_a and genders_b:
        a_wants_b = profile_b.gender in genders_a if profile_b.gender else True
        b_wants_a = profile_a.gender in genders_b if profile_a.gender else True
        if not a_wants_b and not b_wants_a:
            return False

    # Language filter
    langs_a = set(profile_a.event_languages or [])
    langs_b = set(profile_b.event_languages or [])
    if langs_a and langs_b and not (langs_a & langs_b):
        return False

    # Mutual age filter
    age_a = profile_a.age
    age_b = profile_b.age

    if age_a is not None and age_b is not None:
        # Check A fits B's range (unless B has default)
        if not (profile_b.preferred_age_min == 18 and profile_b.preferred_age_max == 99):
            if age_a < profile_b.preferred_age_min or age_a > profile_b.preferred_age_max:
                return False
        # Check B fits A's range (unless A has default)
        if not (profile_a.preferred_age_min == 18 and profile_a.preferred_age_max == 99):
            if age_b < profile_a.preferred_age_min or age_b > profile_a.preferred_age_max:
                return False

    return True


# =============================================================================
# Combined Matching Score
# =============================================================================

# Score weights (5 signals)
WEIGHT_QUALITIES = 0.55
WEIGHT_ZODIAC_WEST = 0.15
WEIGHT_ZODIAC_CN = 0.05
WEIGHT_LANGUAGE = 0.10
WEIGHT_AGE_FIT = 0.15

# Score thresholds for display labels
THRESHOLD_EXCELLENT = 0.80
THRESHOLD_GOOD = 0.60
THRESHOLD_POSSIBLE = 0.40


def compute_match_score(profile_a, profile_b):
    """Compute the combined match score between two profiles.

    Uses 5 signals: qualities, western zodiac, Chinese zodiac, language, age fit.
    If either user has astro_enabled=False, zodiac weights are redistributed.

    Args:
        profile_a: CrushProfile instance
        profile_b: CrushProfile instance

    Returns:
        dict with score_qualities, score_zodiac_west, score_zodiac_cn,
        score_language, score_age_fit, score_final (all floats).
    """
    q_score = compute_quality_score(profile_a, profile_b)
    lang_score = compute_language_score(profile_a, profile_b)
    age_score = compute_age_fit_score(profile_a, profile_b)

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
            + WEIGHT_LANGUAGE * lang_score
            + WEIGHT_AGE_FIT * age_score
        )
    else:
        z_west = 0.0
        z_cn = 0.0
        # Redistribute zodiac weight proportionally among remaining signals
        remaining = WEIGHT_QUALITIES + WEIGHT_LANGUAGE + WEIGHT_AGE_FIT
        final = (
            (WEIGHT_QUALITIES / remaining) * q_score
            + (WEIGHT_LANGUAGE / remaining) * lang_score
            + (WEIGHT_AGE_FIT / remaining) * age_score
        )

    return {
        "score_qualities": round(q_score, 4),
        "score_zodiac_west": round(z_west, 4),
        "score_zodiac_cn": round(z_cn, 4),
        "score_language": round(lang_score, 4),
        "score_age_fit": round(age_score, 4),
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
    filtered_out_pairs = []
    for other_profile in other_profiles:
        # Enforce user_a.pk < user_b.pk
        if user.pk < other_profile.user.pk:
            user_a, user_b = user, other_profile.user
        else:
            user_a, user_b = other_profile.user, user

        # Hard filter: skip incompatible pairs
        if not passes_hard_filters(profile, other_profile):
            filtered_out_pairs.append((user_a.pk, user_b.pk))
            continue

        scores = compute_match_score(profile, other_profile)

        MatchScore.objects.update_or_create(
            user_a=user_a,
            user_b=user_b,
            defaults={
                "score_qualities": scores["score_qualities"],
                "score_zodiac_west": scores["score_zodiac_west"],
                "score_zodiac_cn": scores["score_zodiac_cn"],
                "score_language": scores["score_language"],
                "score_age_fit": scores["score_age_fit"],
                "score_final": scores["score_final"],
            },
        )
        count += 1

    # Clean up stale MatchScore rows for pairs that now fail hard filters
    if filtered_out_pairs:
        from django.db.models import Q

        filter_q = Q()
        for pk_a, pk_b in filtered_out_pairs:
            filter_q |= Q(user_a_id=pk_a, user_b_id=pk_b)
        deleted, _ = MatchScore.objects.filter(filter_q).delete()
        if deleted:
            logger.info(
                "Deleted %d stale match scores for user %s", deleted, user.pk
            )

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
