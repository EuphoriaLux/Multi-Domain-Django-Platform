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

    # Determine role based on gender (outside atomic — read-only)
    profile = getattr(user, "crushprofile", None)
    gender = profile.gender if profile else ""

    # Find table with fewest members of the same role, tie-break by table_number
    with transaction.atomic():
        # Create QuizTable rows on demand if they don't exist yet. The
        # initial batch generate_rotation_rounds normally creates them,
        # but it can fail (e.g. host pressed Start before enough people
        # arrived) or simply not have run yet (early check-ins while
        # quiz is still draft). Either way, every check-in needs a
        # table row to anchor a round-0 placement to. get_or_create is
        # race-safe under the (quiz, table_number) unique_together.
        for n in range(1, quiz_event.num_tables + 1):
            QuizTable.objects.get_or_create(quiz=quiz_event, table_number=n)

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

    # If the quiz has already been started, (re)build future rounds so
    # this late arrival is seated for the remainder of the quiz. We
    # trigger on status alone — not on "round 1+ rows already exist" —
    # because the very first auto-generation at quiz start can fail
    # (e.g. fewer than 4 attendees at that moment), in which case no
    # rows 1+ exist yet and subsequent check-ins must still be able to
    # build the schedule. generate_rotation_rounds is idempotent and
    # serializes on a quiz row lock, so calling it per check-in is
    # safe. We deliberately preserve the current round and any already-
    # played rounds via preserve_current_round=True: the boundary is
    # recomputed under that lock so a concurrent
    # advance_round_and_rotate cannot make the preserved range stale.
    if quiz_event.status in ("active", "paused"):
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


def check_can_rotate(quiz_id):
    """Return ``{}`` if the quiz's current round is complete and fully
    scored, or ``{"error": "..."}`` otherwise.

    Server-side defense-in-depth for the WS ``rotate`` action: even if
    the host UI hides the button until ``round_complete`` is broadcast,
    a second tab or a direct WS payload could still fire the action.
    This guard blocks rotating when:

    - the current round still has unshown questions, or
    - the last shown question has not been scored by every table.

    Only enforced for ``quiz_night`` events — legacy quizzes do not use
    table-level scoring.
    """
    from crush_lu.models.quiz import (
        QuizEvent,
        QuizRound,
        QuizTable,
        TableRoundScore,
    )

    try:
        quiz = QuizEvent.objects.select_related("event").get(id=quiz_id)
    except QuizEvent.DoesNotExist:
        return {}

    if quiz.event.event_type != "quiz_night":
        return {}

    if not quiz.current_round_id:
        return {}

    current_round = QuizRound.objects.get(pk=quiz.current_round_id)
    questions = list(current_round.questions.order_by("sort_order"))
    total_questions = len(questions)
    if total_questions == 0:
        return {}

    # (a) Every question in the round must have been shown. Index is
    # 0-based; -1 means "not started". Round is exhausted only when the
    # last question has been shown.
    if quiz.current_question_index < total_questions - 1:
        remaining = total_questions - 1 - quiz.current_question_index
        return {
            "error": (
                f"Cannot rotate: {remaining} question(s) remain in this "
                f"round. Finish the round first."
            )
        }

    # (b) Every table must have been scored on the last shown question.
    last_question = questions[quiz.current_question_index]
    total_tables = QuizTable.objects.filter(quiz=quiz).count()
    if total_tables > 0:
        scored = TableRoundScore.objects.filter(
            quiz=quiz, question=last_question
        ).count()
        if scored < total_tables:
            return {
                "error": (
                    f"Cannot rotate: only {scored}/{total_tables} tables "
                    f"scored for the last question."
                )
            }

    return {}


def get_current_assignment(quiz, user_id):
    """Return ``user_id``'s table assignment for the quiz's *current*
    round, or None if they aren't seated.

    Used by the WS connect-path so reconnecting clients don't display
    the stale table number from before a rotate they missed. Falls
    back to round-0 ``QuizTableMembership`` only when the quiz has
    *no* rotation rows at all for the current round (legacy
    non-rotating events).
    """
    from crush_lu.models.quiz import (
        QuizRotationSchedule,
        QuizTableMembership,
    )

    round_number = quiz.get_round_number()
    rotation = (
        QuizRotationSchedule.objects.filter(
            quiz=quiz, round_number=round_number, user_id=user_id
        )
        .select_related("table")
        .first()
    )
    if rotation:
        return {
            "table_number": rotation.table.table_number,
            "table_id": rotation.table_id,
            "role": rotation.role,
            "round_number": round_number,
        }

    membership = (
        QuizTableMembership.objects.filter(
            table__quiz=quiz, user_id=user_id
        )
        .select_related("table")
        .first()
    )
    if membership:
        return {
            "table_number": membership.table.table_number,
            "table_id": membership.table_id,
            "role": "",
            "round_number": round_number,
        }
    return None


def reset_quiz_to_draft(quiz, clear_scores=False):
    """Bring a finished/paused quiz back to draft.

    Resets ``current_round``, ``current_question_index``, and
    ``question_started_at`` so the host can re-run a session (e.g.
    after a test rehearsal). When ``clear_scores=True``, also wipes
    ``TableRoundScore`` and ``IndividualScore`` rows.

    Returns a status dict suitable for broadcasting to the
    ``quiz.status`` group event.

    Raises:
        ValidationError: if the quiz is not in ``finished`` or
        ``paused`` state. Active quizzes must be paused/ended first.
    """
    from django.db import transaction

    from crush_lu.models.quiz import (
        IndividualScore,
        QuizEvent,
        TableRoundScore,
    )

    with transaction.atomic():
        try:
            locked = QuizEvent.objects.select_for_update().get(pk=quiz.pk)
        except QuizEvent.DoesNotExist:
            raise ValidationError("Quiz not found.")

        if locked.status not in ("finished", "paused"):
            raise ValidationError(
                f"Cannot reset a quiz in '{locked.status}' state. "
                f"Only 'finished' or 'paused' quizzes can be reset."
            )

        locked.status = "draft"
        locked.current_round = None
        locked.current_question_index = -1
        locked.question_started_at = None
        locked.save(
            update_fields=[
                "status",
                "current_round",
                "current_question_index",
                "question_started_at",
            ]
        )

        cleared = 0
        if clear_scores:
            cleared = TableRoundScore.objects.filter(quiz=locked).count()
            TableRoundScore.objects.filter(quiz=locked).delete()
            IndividualScore.objects.filter(quiz=locked).delete()

    return {
        "status": "draft",
        "reset": True,
        "cleared_scores": clear_scores,
        "scores_cleared_count": cleared,
    }


def compute_rotation_warnings(quiz):
    """Stateless re-derivation of the warnings that
    ``generate_rotation_schedule`` would emit for the current quiz
    setup. Lets the coach view surface ongoing capacity issues (empty
    anchor tables, too few rotators, spillover groups) without
    re-running or persisting the algorithm output.

    Returns a list of human-readable strings; empty when everything is
    fine or the quiz has no ``num_tables`` configured yet.
    """
    from crush_lu.models.events import EventRegistration

    if not quiz.num_tables or quiz.num_tables < 2:
        return []

    num_tables = int(quiz.num_tables)
    registrations = (
        EventRegistration.objects.filter(event=quiz.event, status="attended")
        .select_related("user__crushprofile")
        .order_by("registered_at")
    )
    if registrations.count() < 4:
        return [
            f"Only {registrations.count()} attended registration(s). "
            f"Need at least 4 to start the quiz."
        ]

    men, women = split_participants_by_gender(registrations)
    warnings = []

    empty_anchor_tables = max(0, num_tables - len(men))
    if empty_anchor_tables > 0:
        warnings.append(
            f"{empty_anchor_tables} table(s) have no anchors "
            f"({len(men)} anchors for {num_tables} tables)."
        )

    if len(women) < num_tables:
        warnings.append(
            f"Only {len(women)} rotator(s) for {num_tables} tables. "
            f"Some tables will have no rotators in some rounds."
        )

    spillover = max(0, len(women) - num_tables * 2)
    if spillover > 0:
        warnings.append(
            f"{spillover} extra rotator(s) assigned to spillover group "
            f"(groups A/B hold {len(women) - spillover}, "
            f"spillover distributed round-robin)."
        )

    return warnings


def dissolve_table(quiz, table_number):
    """
    Dissolve (remove) a quiz table from the rotation, decrement
    ``quiz.num_tables``, and reseat any displaced rotators in the
    remaining tables for future rounds.

    Use case: confirmed-but-no-show participants leave a table empty in
    the current round; the host can collapse the configured table count
    so the all-tables-scored gate doesn't block rotation, and so the
    leaderboard isn't polluted by a ghost table.

    Guards:
        - quiz status must be ``draft``, ``active``, or ``paused``.
        - ``num_tables`` must be at least 3 — dissolving below 2 tables
          would invalidate the rotation algorithm's preconditions.
        - The target table must exist on this quiz.
        - The target table must have zero members in the *current* round
          (and no rotation rows for any future round; current-round
          membership is the user-visible signal).
        - The target table is the highest-numbered table on the quiz.
          We only dissolve from the top so rotation indices and
          remaining table numbers stay stable; dissolving an interior
          table would re-number the rest and confuse coaches mid-game.

    Side effects:
        - Deletes ``QuizRotationSchedule`` rows for this table from the
          current round forward (round_number >= current_round_number).
          Earlier rounds are preserved so historical seating is intact.
        - Deletes ``QuizTableMembership`` rows for this table.
        - Deletes the ``QuizTable`` itself **only** if no
          ``TableRoundScore`` rows reference it. Otherwise the table is
          retained as an orphan so leaderboard history (e.g. table 5
          won round 1 before being dissolved) is preserved.
        - Decrements ``quiz.num_tables`` by 1.
        - Calls ``generate_rotation_rounds(preserve_current_round=True)``
          to rebuild future rounds against the new table count.

    Returns:
        dict: {"num_tables": int, "table_deleted": bool}

    Raises:
        ValidationError: if any guard fails.
    """
    from django.db import transaction

    from crush_lu.models.quiz import (
        QuizEvent,
        QuizRotationSchedule,
        QuizTable,
        QuizTableMembership,
    )

    if not isinstance(table_number, int) or table_number < 1:
        raise ValidationError("Invalid table number.")

    with transaction.atomic():
        locked_quiz = (
            QuizEvent.objects.select_for_update().filter(pk=quiz.pk).first()
        )
        if locked_quiz is None:
            raise ValidationError("Quiz not found.")
        quiz = locked_quiz

        if quiz.status not in ("draft", "active", "paused"):
            raise ValidationError(
                f"Cannot dissolve a table while quiz status is '{quiz.status}'."
            )

        current_num_tables = quiz.num_tables or 0
        if current_num_tables < 3:
            raise ValidationError(
                "Need at least 3 tables to dissolve one — rotation requires "
                "a minimum of 2 tables."
            )

        # Only allow dissolving the top-numbered table to keep numbering
        # stable for the coach and the rotation algorithm.
        if table_number != current_num_tables:
            raise ValidationError(
                f"Only the highest-numbered table can be dissolved "
                f"(table {current_num_tables}). Got table {table_number}."
            )

        try:
            table = QuizTable.objects.select_for_update().get(
                quiz=quiz, table_number=table_number
            )
        except QuizTable.DoesNotExist:
            raise ValidationError(
                f"Table {table_number} does not exist on this quiz."
            )

        # Compute the boundary: keep rounds < current_round_number, drop
        # this table's rows from the current round forward.
        current_round_number = 0
        if quiz.current_round_id:
            current_round_number = quiz.get_round_number()

        # Block if anyone is currently seated at this table in the
        # current round — dissolving an occupied table would silently
        # boot players mid-game.
        currently_seated = QuizRotationSchedule.objects.filter(
            quiz=quiz, round_number=current_round_number, table=table
        ).exists()
        if currently_seated:
            raise ValidationError(
                f"Cannot dissolve table {table_number}: it has seated "
                f"participants in the current round. Wait until the next "
                f"rotation or move them first."
            )

        QuizRotationSchedule.objects.filter(
            quiz=quiz, table=table, round_number__gte=current_round_number
        ).delete()
        QuizTableMembership.objects.filter(table=table).delete()

        # Preserve the QuizTable row if it carries scoring history; the
        # leaderboard reads TableRoundScore directly and we don't want
        # to lose past rounds' results just because the table is empty
        # going forward.
        table_deleted = False
        if not table.round_scores.exists():
            table.delete()
            table_deleted = True

        quiz.num_tables = current_num_tables - 1
        quiz.save(update_fields=["num_tables"])

        # Rebuild future rounds against the new table count. This will
        # also pick up any newly-displaced rotators who used to be
        # scheduled at the dissolved table in rounds > current.
        if quiz.status in ("active", "paused"):
            generate_rotation_rounds(quiz, preserve_current_round=True)

    return {
        "num_tables": quiz.num_tables,
        "table_deleted": table_deleted,
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
