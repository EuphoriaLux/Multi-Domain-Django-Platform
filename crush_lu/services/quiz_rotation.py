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
    if not num_tables:
        num_tables = max(len(men) // 2, 2)
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
    if len(women) < num_tables:
        warnings.append(
            f"Only {len(women)} rotator(s) for {num_tables} tables. "
            f"Some tables will have no rotators in some rounds."
        )

    schedule = []

    # Split women into rotation groups (all women are seated)
    if num_tables == 2:
        group_a = women[:4] if len(women) >= 4 else women
        group_b = []
        group_c = women[4:]
    else:
        group_a = women[:num_tables]
        group_b = women[num_tables : num_tables * 2]
        group_c = women[num_tables * 2 :]  # spillover — everyone gets seated

    if group_c:
        warnings.append(
            f"{len(group_c)} extra rotator(s) assigned to spillover group "
            f"(groups A/B hold {len(group_a) + len(group_b)}, "
            f"spillover distributed round-robin)."
        )

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
                    w_idx = (
                        (table_idx * 2 + offset + round_num) % len(group_a)
                        if group_a
                        else -1
                    )
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

        # Group C (spillover): distribute round-robin across tables each round
        for c_idx, extra_woman in enumerate(group_c):
            target_table = (c_idx + round_num) % num_tables
            schedule.append(
                {
                    "round_number": round_num,
                    "table_number": target_table + 1,  # 1-indexed
                    "user": extra_woman,
                    "role": "rotator",
                    "rotation_group": "C",
                }
            )

    return {
        "schedule": schedule,
        "num_tables": num_tables,
        "warnings": warnings,
    }


def assign_table_on_checkin(quiz_event, user):
    """
    Incrementally assign a user to a quiz table at check-in time (round 0).

    Uses the same gender-based role logic as the batch algorithm:
    M → anchor, F → rotator, NB/O/P → whichever pool is smaller.

    Returns:
        dict with {"table_number": int, "role": str} or None if no tables.
    """
    from django.db import transaction
    from django.db.models import Count

    from crush_lu.models.quiz import (
        QuizRotationSchedule,
        QuizTable,
        QuizTableMembership,
    )

    if not quiz_event.num_tables:
        return None

    tables = QuizTable.objects.filter(quiz=quiz_event)
    if not tables.exists():
        return None

    # Determine role based on gender (outside atomic — read-only)
    profile = getattr(user, "crushprofile", None)
    gender = profile.gender if profile else ""

    # Find table with fewest members of the same role, tie-break by table_number
    with transaction.atomic():
        # Lock tables to prevent race conditions
        locked_tables = list(
            QuizTable.objects.filter(quiz=quiz_event)
            .select_for_update()
            .order_by("table_number")
        )

        # Idempotent check inside atomic block to prevent race condition
        existing = (
            QuizTableMembership.objects.filter(table__quiz=quiz_event, user=user)
            .select_related("table")
            .first()
        )
        if existing:
            rotation = QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=0, user=user
            ).first()
            return {
                "table_number": existing.table.table_number,
                "role": rotation.role if rotation else "",
            }

        if gender == "M":
            role = "anchor"
        elif gender == "F":
            role = "rotator"
        else:
            # Flexible: join the smaller pool
            anchor_count = QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=0, role="anchor"
            ).count()
            rotator_count = QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=0, role="rotator"
            ).count()
            role = "anchor" if anchor_count <= rotator_count else "rotator"

        # Count role members per table from rotation schedule
        role_counts = dict(
            QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=0, role=role
            )
            .values_list("table_id")
            .annotate(cnt=Count("id"))
            .values_list("table_id", "cnt")
        )

        # Pick table with fewest members of this role
        target_table = min(
            locked_tables,
            key=lambda t: (role_counts.get(t.id, 0), t.table_number),
        )

        # Determine rotation group for rotators
        rotation_group = ""
        if role == "rotator":
            num_tables = quiz_event.num_tables
            existing_rotators = QuizRotationSchedule.objects.filter(
                quiz=quiz_event, round_number=0, role="rotator"
            ).count()
            if existing_rotators < num_tables:
                rotation_group = "A"
            elif existing_rotators < num_tables * 2:
                rotation_group = "B"
            else:
                rotation_group = "C"

        QuizTableMembership.objects.create(table=target_table, user=user)
        QuizRotationSchedule.objects.create(
            quiz=quiz_event,
            round_number=0,
            table=target_table,
            user=user,
            role=role,
            rotation_group=rotation_group,
        )

    # If the quiz has already been started (rounds 1+ exist in the schedule),
    # rebuild future rounds so this late arrival is seated for the remainder
    # of the quiz. We deliberately preserve the current round and any
    # already-played rounds: `advance_round_and_rotate` reads
    # QuizRotationSchedule live at rotate time, so rewriting the current
    # round's rows would move seated participants to different tables
    # mid-game. ``preserve_current_round=True`` reads ``current_round``
    # from the DB *under* the quiz row lock, so a concurrent
    # ``advance_round_and_rotate`` cannot race and make the preserved
    # boundary stale.
    rounds_exist = QuizRotationSchedule.objects.filter(
        quiz=quiz_event, round_number__gte=1
    ).exists()
    if rounds_exist:
        try:
            generate_rotation_rounds(quiz_event, preserve_current_round=True)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Failed to regenerate rotation rounds for late arrival "
                "(quiz=%s user=%s)",
                quiz_event.pk,
                user.pk,
            )

    return {
        "table_number": target_table.table_number,
        "role": role,
    }


def generate_rotation_rounds(quiz, from_round=1, preserve_current_round=False):
    """
    Generate rotation schedule for rounds >= ``from_round`` using existing
    round-0 check-in assignments.

    Preserves round-0 table assignments and builds rotation rounds on top.
    Used by both "Start Quiz" (auto) and "Regenerate Tables" (manual).

    Args:
        quiz: QuizEvent instance with num_tables set and round-0 data populated.
        from_round: rebuild rounds from this number onwards (inclusive). Must
            be >= 1. Rounds with ``round_number < from_round`` are preserved
            as-is, which lets the caller protect the current and already-
            played rounds from being shuffled mid-game. Default 1, which
            preserves only round 0 (check-in placements).
        preserve_current_round: if True, ``from_round`` is recomputed
            *after* acquiring the quiz row lock as
            ``max(from_round, get_round_number(current_round) + 1)``, so
            the boundary is based on committed state and cannot race
            against a concurrent ``advance_round_and_rotate`` that has
            already moved ``current_round`` forward.

    Returns:
        dict: {"num_tables": int, "warnings": list, "anchors": int, "rotators": int}

    Raises:
        ValidationError: if not enough participants for rotation.
    """
    from collections import defaultdict

    from django.db import transaction
    from django.utils import timezone

    from crush_lu.models.events import EventRegistration
    from crush_lu.models.quiz import QuizEvent, QuizRotationSchedule, QuizTable

    if from_round < 1:
        from_round = 1

    # Serialize concurrent callers (late check-ins, admin "Regenerate
    # Tables", and quiz-start auto-generation) so the delete/rebuild
    # sequence below cannot race on the same quiz and violate the
    # (quiz, round_number, user) unique_together constraint.
    with transaction.atomic():
        # Re-fetch under the row lock so current_round reflects committed
        # state at rebuild time — a concurrent advance_round_and_rotate
        # cannot slip in between this read and the delete below.
        locked_quiz = (
            QuizEvent.objects.select_for_update().filter(pk=quiz.pk).first()
        )
        if locked_quiz is None:
            return {
                "num_tables": 0,
                "warnings": [],
                "anchors": 0,
                "rotators": 0,
            }
        quiz = locked_quiz

        if preserve_current_round:
            current_round_number = 0
            if quiz.current_round_id:
                current_round_number = quiz.get_round_number()
            from_round = max(from_round, current_round_number + 1)

        # Delete existing rounds >= from_round (idempotent). Round 0 and
        # any round < from_round are preserved so in-progress gameplay is
        # not disrupted.
        QuizRotationSchedule.objects.filter(
            quiz=quiz, round_number__gte=from_round
        ).delete()

        # Only people who actually checked in via QR should be rotated.
        # This excludes no-shows (status still "confirmed") from the
        # rotation snapshot without touching their registration row, so
        # late arrivals can still QR-scan in (views_checkin.py requires
        # status="confirmed") and be picked up on the next rotation
        # regeneration.
        registrations = (
            EventRegistration.objects.filter(event=quiz.event, status="attended")
            .select_related("user__crushprofile")
            .order_by("registered_at")
        )
        men_all, women_all = split_participants_by_gender(registrations)

        num_rounds = quiz.rounds.count() or 3
        num_tables = quiz.num_tables

        # Read round-0 assignments
        round_0 = QuizRotationSchedule.objects.filter(
            quiz=quiz, round_number=0
        ).select_related("table")

        anchors_by_table = defaultdict(list)
        rotators_by_table = defaultdict(list)  # keyed by (group, table)
        round_0_users = set()

        for r in round_0:
            round_0_users.add(r.user_id)
            t_idx = r.table.table_number - 1
            if r.role == "anchor":
                anchors_by_table[t_idx].append(r.user)
            else:
                rotators_by_table[(r.rotation_group, t_idx)].append(r.user)

        # Interleave anchors to match _distribute_evenly round-robin
        ordered_men = []
        max_anchors = (
            max(len(v) for v in anchors_by_table.values())
            if anchors_by_table
            else 0
        )
        for rank in range(max_anchors):
            for t in range(num_tables):
                if rank < len(anchors_by_table.get(t, [])):
                    ordered_men.append(anchors_by_table[t][rank])
        # Append late arrivals not in round 0
        for m in men_all:
            if m.id not in round_0_users:
                ordered_men.append(m)

        # Rotators: ordered by group (A, B, C), then by table index
        ordered_women = []
        for group in ["A", "B", "C"]:
            for t in range(num_tables):
                ordered_women.extend(rotators_by_table.get((group, t), []))
        for w in women_all:
            if w.id not in round_0_users:
                ordered_women.append(w)

        result = generate_rotation_schedule(
            ordered_men, ordered_women, num_rounds, num_tables=num_tables
        )

        schedule = result["schedule"]
        actual_num_tables = result["num_tables"]

        # Reuse or create tables
        existing_tables = {
            t.table_number: t for t in QuizTable.objects.filter(quiz=quiz)
        }
        tables = {}
        for t in range(1, actual_num_tables + 1):
            if t in existing_tables:
                tables[t] = existing_tables[t]
            else:
                tables[t] = QuizTable.objects.create(quiz=quiz, table_number=t)

        # Remove excess tables only if they have no scores
        for t_num, t_obj in existing_tables.items():
            if t_num > actual_num_tables:
                if not t_obj.round_scores.exists():
                    t_obj.delete()

        # Build rotation entries for rounds >= from_round. Round 0 and
        # rounds < from_round are never (re)inserted here so they stay
        # exactly as they are in the DB.
        rotation_entries = []
        for entry in schedule:
            if entry["round_number"] < from_round:
                continue
            table = tables[entry["table_number"]]
            rotation_entries.append(
                QuizRotationSchedule(
                    quiz=quiz,
                    round_number=entry["round_number"],
                    table=table,
                    user=entry["user"],
                    role=entry["role"],
                    rotation_group=entry["rotation_group"],
                )
            )

        QuizRotationSchedule.objects.bulk_create(rotation_entries)

        quiz.tables_generated_at = timezone.now()
        quiz.save(update_fields=["tables_generated_at"])

    return {
        "num_tables": actual_num_tables,
        "warnings": result["warnings"],
        "anchors": len(ordered_men),
        "rotators": len(ordered_women),
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
