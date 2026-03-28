"""
Quiz Night table rotation algorithm.

Generates a rotation schedule where:
- Men are anchored at fixed tables (2 per table)
- Women rotate between tables (split into groups A and B at different speeds)
- After N rounds (N = number of tables), every woman has visited every table
- No two women are paired at the same table more than once
"""

from django.core.exceptions import ValidationError


def generate_rotation_schedule(men, women, num_rounds=3):
    """
    Generate a rotation schedule for quiz night.

    Args:
        men: list of user objects/IDs to anchor at tables (2 per table)
        women: list of user objects/IDs to rotate between tables
        num_rounds: number of quiz rounds (default 3)

    Returns:
        list of dicts: [
            {
                "round_number": 0,
                "table_number": 1,
                "user": <user>,
                "role": "anchor" | "rotator",
                "rotation_group": "" | "A" | "B",
            },
            ...
        ]

    Raises:
        ValidationError: if participant counts are invalid
    """
    num_tables = len(men) // 2

    if num_tables < 2:
        raise ValidationError(
            f"Need at least 4 men for 2 tables, got {len(men)}."
        )

    if len(women) < num_tables:
        raise ValidationError(
            f"Need at least {num_tables} women for {num_tables} tables, "
            f"got {len(women)}."
        )

    if len(women) > num_tables * 2:
        raise ValidationError(
            f"Too many women ({len(women)}) for {num_tables} tables. "
            f"Maximum is {num_tables * 2}."
        )

    schedule = []

    # Assign men to tables: men[0],men[1] → T1; men[2],men[3] → T2; etc.
    men_tables = {}
    for i in range(num_tables):
        men_tables[i] = [men[i * 2], men[i * 2 + 1]]
        # Handle odd number of men: last table gets 1 man
        if i * 2 + 1 >= len(men):
            men_tables[i] = [men[i * 2]]

    # Split women into two rotation groups
    group_a = women[:num_tables]
    group_b = women[num_tables : num_tables * 2]

    for round_num in range(num_rounds):
        for table_idx in range(num_tables):
            table_number = table_idx + 1  # 1-indexed

            # Men are anchored — same table every round
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

            # Group A rotates by +1 per round
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

            # Group B rotates by +2 per round (different speed)
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

    return schedule


def split_participants_by_gender(registrations_with_profiles):
    """
    Split confirmed registrations into men and women lists.

    Args:
        registrations_with_profiles: queryset of EventRegistration with
            user__crushprofile prefetched

    Returns:
        (men_users, women_users): two lists of User objects

    Uses GENDER_POOL_MAP logic: M → men pool, F → women pool,
    NB/O/P → whichever pool needs more people.
    """
    men = []
    women = []
    flexible = []  # NB, O, P — assigned to smaller pool

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
