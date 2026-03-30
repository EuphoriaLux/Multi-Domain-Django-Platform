"""
Quiz Night table rotation algorithm.

Generates a rotation schedule where:
- Men are anchored at fixed tables (distributed evenly)
- Women rotate between tables (split into groups A and B at different speeds)
- After N rounds (N = number of tables), every woman has visited every table
- No two women are paired at the same table more than once
"""

from django.core.exceptions import ValidationError


def _distribute_evenly(people, num_buckets):
    """Distribute people into num_buckets using round-robin.

    Returns a dict mapping bucket index (0-based) to a list of people.
    Example: 5 people into 3 buckets = {0: [P0, P3], 1: [P1, P4], 2: [P2]}
    """
    buckets = {i: [] for i in range(num_buckets)}
    for idx, person in enumerate(people):
        buckets[idx % num_buckets].append(person)
    return buckets


def generate_rotation_schedule(men, women, num_rounds=3, num_tables=None):
    """
    Generate a rotation schedule for quiz night.

    Args:
        men: list of user objects/IDs to anchor at tables
        women: list of user objects/IDs to rotate between tables
        num_rounds: number of quiz rounds (default 3)
        num_tables: number of physical tables available. If None,
            auto-calculated as len(men) // 2.

    Returns:
        dict: {
            "schedule": [
                {
                    "round_number": 0,
                    "table_number": 1,
                    "user": <user>,
                    "role": "anchor" | "rotator",
                    "rotation_group": "" | "A" | "B",
                },
                ...
            ],
            "num_tables": int,
            "warnings": [str, ...],
        }

    Raises:
        ValidationError: if participant counts make rotation impossible
    """
    warnings = []

    # Determine table count
    if num_tables is None:
        num_tables = len(men) // 2
    else:
        num_tables = int(num_tables)

    if num_tables < 2:
        raise ValidationError(
            f"Need at least 2 tables. Got {num_tables} "
            f"(with {len(men)} anchors, {len(women)} rotators)."
        )

    total_participants = len(men) + len(women)
    if total_participants < 4:
        raise ValidationError(
            f"Need at least 4 participants, got {total_participants}."
        )

    # Distribute men evenly across tables (round-robin)
    men_tables = _distribute_evenly(men, num_tables)

    # Check for empty anchor tables
    empty_anchor_tables = [i for i in range(num_tables) if not men_tables[i]]
    if empty_anchor_tables:
        warnings.append(
            f"{len(empty_anchor_tables)} table(s) have no anchors "
            f"({len(men)} anchors for {num_tables} tables)."
        )

    # Handle women distribution
    max_women = num_tables * 2
    seated_women = women[:max_women]
    unseated_women = women[max_women:]

    if unseated_women:
        warnings.append(
            f"{len(unseated_women)} rotator(s) could not be seated "
            f"(max {max_women} for {num_tables} tables). "
            f"Consider adding more tables."
        )

    if len(seated_women) < num_tables:
        warnings.append(
            f"Only {len(seated_women)} rotator(s) for {num_tables} tables. "
            f"Some tables will have no rotators in some rounds."
        )

    schedule = []

    # Split women into rotation groups
    if num_tables == 2:
        group_a = seated_women  # All women in one group
        group_b = []
    else:
        group_a = seated_women[:num_tables]
        group_b = seated_women[num_tables : num_tables * 2]

    for round_num in range(num_rounds):
        for table_idx in range(num_tables):
            table_number = table_idx + 1  # 1-indexed

            # Men are anchored -- same table every round
            for man in men_tables[table_idx]:
                schedule.append(
                    {
                        "round_number": round_num,
                        "table_number": table_number,
                        "user": man,
                        "role": "anchor",
                        "rotation_group": "",
                    }
                )

            if num_tables == 2:
                # 2 tables: all women in one group, rotating +1
                for offset in range(2):
                    w_idx = (table_idx * 2 + offset + round_num) % len(
                        group_a
                    ) if group_a else -1
                    if 0 <= w_idx < len(group_a):
                        schedule.append(
                            {
                                "round_number": round_num,
                                "table_number": table_number,
                                "user": group_a[w_idx],
                                "role": "rotator",
                                "rotation_group": "A",
                            }
                        )
            else:
                # 3+ tables: Group A at step +1, Group B at step +2
                a_idx = (table_idx + round_num) % num_tables
                if a_idx < len(group_a):
                    schedule.append(
                        {
                            "round_number": round_num,
                            "table_number": table_number,
                            "user": group_a[a_idx],
                            "role": "rotator",
                            "rotation_group": "A",
                        }
                    )

                b_idx = (table_idx + round_num * 2) % num_tables
                if b_idx < len(group_b):
                    schedule.append(
                        {
                            "round_number": round_num,
                            "table_number": table_number,
                            "user": group_b[b_idx],
                            "role": "rotator",
                            "rotation_group": "B",
                        }
                    )

    return {
        "schedule": schedule,
        "num_tables": num_tables,
        "warnings": warnings,
    }


def split_participants_by_gender(registrations_with_profiles):
    """
    Split confirmed registrations into men and women lists.

    Args:
        registrations_with_profiles: queryset of EventRegistration with
            user__crushprofile prefetched

    Returns:
        (men_users, women_users): two lists of User objects

    Uses GENDER_POOL_MAP logic: M -> men pool, F -> women pool,
    NB/O/P -> whichever pool needs more people.
    """
    men = []
    women = []
    flexible = []  # NB, O, P -- assigned to smaller pool

    for reg in registrations_with_profiles:
        profile = getattr(reg.user, "crushprofile", None)
        gender = profile.gender if profile else ""

        if gender == "M":
            men.append(reg.user)
        elif gender == "F":
            women.append(reg.user)
        else:
            flexible.append(reg.user)

    # Assign flexible participants to whichever pool is smaller
    for user in flexible:
        if len(men) <= len(women):
            men.append(user)
        else:
            women.append(user)

    return men, women
