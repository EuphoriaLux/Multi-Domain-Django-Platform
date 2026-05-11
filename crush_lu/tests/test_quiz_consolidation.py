"""Tests for consolidate_tables — compacting seating after no-shows."""

from datetime import date, timedelta

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from crush_lu.models.quiz import (
    QuizEvent,
    QuizRotationSchedule,
    QuizRound,
    QuizTable,
    QuizTableMembership,
)
from crush_lu.services.quiz_rotation import (
    assign_table_on_checkin,
    consolidate_tables,
    get_unassigned_attendees,
    manual_assign_table,
)


def _grant_consent(user):
    from allauth.account.models import EmailAddress
    from crush_lu.models.profiles import UserDataConsent

    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save()
    if user.email:
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )


def _create_profile(user, gender):
    from crush_lu.models.profiles import CrushProfile

    profile, _ = CrushProfile.objects.get_or_create(
        user=user,
        defaults={"gender": gender, "date_of_birth": date(1995, 1, 1)},
    )
    if profile.gender != gender:
        profile.gender = gender
        profile.save(update_fields=["gender"])
    return profile


def _make_user(username, gender):
    user = User.objects.create_user(
        username=username, email=f"{username}@test.com", password="testpass123"
    )
    _grant_consent(user)
    _create_profile(user, gender)
    return user


@pytest.fixture
def quiz_event_4t(db):
    """Quiz with 4 tables and 3 rounds — minimal setup for rotation."""
    from crush_lu.models import MeetupEvent

    coach = User.objects.create_user(
        username="consol_coach@test.com",
        email="consol_coach@test.com",
        password="testpass123",
        is_staff=True,
    )
    _grant_consent(coach)

    event = MeetupEvent.objects.create(
        title="Consol Quiz Night",
        description="Test",
        event_type="quiz_night",
        date_time=timezone.now() + timedelta(days=1),
        location="LU",
        address="1",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(hours=12),
        is_published=True,
    )
    quiz = QuizEvent.objects.create(
        event=event, status="draft", created_by=coach, num_tables=4
    )
    quiz.ensure_tables()
    # generate_rotation_rounds wants at least one round to exist.
    for i in range(3):
        QuizRound.objects.create(
            quiz=quiz, title=f"Round {i + 1}", sort_order=i, time_per_question=30
        )
    return quiz


def _check_in_attended(quiz, user):
    """Seat a user at round 0 AND mark their registration attended.

    consolidate_tables doesn't read registrations directly, but the
    downstream generate_rotation_rounds it triggers does.
    """
    from crush_lu.models.events import EventRegistration

    EventRegistration.objects.update_or_create(
        event=quiz.event,
        user=user,
        defaults={"status": "attended", "checked_in_at": timezone.now()},
    )
    return assign_table_on_checkin(quiz, user)


@pytest.mark.django_db
class TestConsolidateTables:
    def _seat_3M_4F(self, quiz):
        """Simulate: 4 tables configured, but only 3 men + 4 women showed up.

        With ``assign_table_on_checkin``:
        - Men → anchors on tables 1, 2, 3 (least-filled rule)
        - Women → rotators on tables 1, 2, 3, 4 (group A spreads across tables)

        Result: table 4 has 1 rotator and 0 anchors — the classic post-no-show
        shape this feature exists to fix.
        """
        users = {"men": [], "women": []}
        for i in range(3):
            u = _make_user(f"shrink_m{i}", "M")
            _check_in_attended(quiz, u)
            users["men"].append(u)
        for i in range(4):
            u = _make_user(f"shrink_f{i}", "F")
            _check_in_attended(quiz, u)
            users["women"].append(u)
        return users

    def test_dry_run_shrinks_num_tables(self, quiz_event_4t):
        """4 tables configured, 3 anchors + 4 rotators show up →
        consolidation should target 3 tables (table 4 removed) and move
        the rotator currently on table 4."""
        quiz = quiz_event_4t
        self._seat_3M_4F(quiz)

        # Sanity: assign_table_on_checkin should have parked one woman on table 4.
        on_t4 = list(
            QuizRotationSchedule.objects.filter(
                quiz=quiz, round_number=0, table__table_number=4
            ).values_list("user_id", flat=True)
        )
        assert on_t4, "Test precondition: expected a rotator on table 4"

        result = consolidate_tables(quiz, apply=False)

        assert result["changed"] is True
        assert result["current_num_tables"] == 4
        assert result["new_num_tables"] == 3
        assert result["tables_removed"] == [4]
        moved_user_ids = {m["user_id"] for m in result["moves"]}
        assert set(on_t4).issubset(moved_user_ids)
        for m in result["moves"]:
            assert m["to_table"] in (1, 2, 3)
            assert m["from_table"] == 4

        # Dry run must not mutate state.
        quiz.refresh_from_db()
        assert quiz.num_tables == 4
        assert QuizTable.objects.filter(quiz=quiz).count() == 4

    def test_apply_moves_users_and_regenerates_rounds(self, quiz_event_4t):
        quiz = quiz_event_4t
        self._seat_3M_4F(quiz)

        result = consolidate_tables(quiz, apply=True)
        assert result["changed"] is True

        quiz.refresh_from_db()
        assert quiz.num_tables == 3
        assert QuizTable.objects.filter(quiz=quiz).count() == 3

        # Every membership now points to one of the kept tables.
        for m in QuizTableMembership.objects.filter(table__quiz=quiz):
            assert m.table.table_number in (1, 2, 3)

        # Every round-0 schedule row likewise.
        for r in QuizRotationSchedule.objects.filter(quiz=quiz, round_number=0):
            assert r.table.table_number in (1, 2, 3)

        # Rounds 1+ regenerated.
        assert QuizRotationSchedule.objects.filter(
            quiz=quiz, round_number__gte=1
        ).exists()

    def test_no_op_when_already_consolidated(self, quiz_event_4t):
        """If everyone fits on the configured tables, consolidation reports
        changed=False and the data isn't touched."""
        quiz = quiz_event_4t
        # 4 men + 4 women fitting 4 tables exactly = ideal layout.
        for i in range(4):
            _check_in_attended(quiz, _make_user(f"ok_m{i}", "M"))
        for i in range(4):
            _check_in_attended(quiz, _make_user(f"ok_f{i}", "F"))

        result = consolidate_tables(quiz, apply=True)
        assert result["changed"] is False
        assert result["moves"] == []
        quiz.refresh_from_db()
        assert quiz.num_tables == 4

    def test_rejects_when_quiz_is_active(self, quiz_event_4t):
        quiz = quiz_event_4t
        _check_in_attended(quiz, _make_user("active_m1", "M"))
        _check_in_attended(quiz, _make_user("active_f1", "F"))
        quiz.status = "active"
        quiz.save(update_fields=["status"])

        with pytest.raises(ValidationError):
            consolidate_tables(quiz, apply=False)

    def test_rejects_when_no_attendees(self, quiz_event_4t):
        quiz = quiz_event_4t
        with pytest.raises(ValidationError):
            consolidate_tables(quiz, apply=False)

    def test_apply_with_coach_moves_override(self, quiz_event_4t):
        """Coach picks the destination for the excess rotator instead of
        accepting the auto-balanced suggestion. The override must win."""
        quiz = quiz_event_4t
        self._seat_3M_4F(quiz)

        preview = consolidate_tables(quiz, apply=False)
        assert preview["changed"] is True
        assert preview["new_num_tables"] == 3
        assert len(preview["moves"]) == 1
        excess_user_id = preview["moves"][0]["user_id"]
        suggested = preview["moves"][0]["to_table"]
        # Pick a different keeper table than the suggestion.
        coach_pick = next(t for t in (1, 2, 3) if t != suggested)

        override = [{"user_id": excess_user_id, "to_table": coach_pick}]
        result = consolidate_tables(quiz, apply=True, moves_override=override)

        assert result["changed"] is True
        assert result["moves"][0]["to_table"] == coach_pick

        # Verify the user actually landed at the coach's pick.
        membership = QuizTableMembership.objects.get(
            table__quiz=quiz, user_id=excess_user_id
        )
        assert membership.table.table_number == coach_pick

    def test_override_rejects_invalid_destination(self, quiz_event_4t):
        quiz = quiz_event_4t
        self._seat_3M_4F(quiz)
        preview = consolidate_tables(quiz, apply=False)
        excess_user_id = preview["moves"][0]["user_id"]

        # Table 99 doesn't exist.
        with pytest.raises(ValidationError):
            consolidate_tables(
                quiz,
                apply=True,
                moves_override=[{"user_id": excess_user_id, "to_table": 99}],
            )

    def test_override_rejects_missing_user(self, quiz_event_4t):
        quiz = quiz_event_4t
        self._seat_3M_4F(quiz)
        consolidate_tables(quiz, apply=False)
        with pytest.raises(ValidationError):
            # No entries at all — but there's an excess user that needs a home.
            consolidate_tables(quiz, apply=True, moves_override=[])


@pytest.mark.django_db
class TestManualAssignTable:
    def _make_attended_orphan(self, quiz, gender):
        """An attended registration with no QuizTableMembership."""
        from crush_lu.models.events import EventRegistration

        username = f"orphan_{gender.lower()}_{User.objects.count()}"
        user = _make_user(username, gender)
        EventRegistration.objects.create(
            event=quiz.event,
            user=user,
            status="attended",
            checked_in_at=timezone.now(),
        )
        return user

    def test_get_unassigned_lists_attended_without_membership(
        self, quiz_event_4t
    ):
        quiz = quiz_event_4t
        # One seated (control), one orphan.
        seated = _make_user("seated_m", "M")
        _check_in_attended(quiz, seated)
        orphan = self._make_attended_orphan(quiz, "M")

        unassigned = get_unassigned_attendees(quiz)
        ids = {u["user_id"] for u in unassigned}
        assert orphan.id in ids
        assert seated.id not in ids
        # Role precomputed from gender.
        orphan_entry = next(u for u in unassigned if u["user_id"] == orphan.id)
        assert orphan_entry["role"] == "anchor"

    def test_manual_assign_seats_orphan(self, quiz_event_4t):
        quiz = quiz_event_4t
        orphan = self._make_attended_orphan(quiz, "F")  # rotator

        result = manual_assign_table(quiz, orphan, table_number=2)
        assert result["table_number"] == 2
        assert result["role"] == "rotator"

        membership = QuizTableMembership.objects.get(
            table__quiz=quiz, user=orphan
        )
        assert membership.table.table_number == 2
        schedule = QuizRotationSchedule.objects.get(
            quiz=quiz, round_number=0, user=orphan
        )
        assert schedule.table.table_number == 2
        assert schedule.role == "rotator"
        assert schedule.rotation_group in ("A", "B", "C")

    def test_manual_assign_rejects_existing_seated_user(self, quiz_event_4t):
        quiz = quiz_event_4t
        user = _make_user("already_seated", "M")
        _check_in_attended(quiz, user)
        with pytest.raises(ValidationError):
            manual_assign_table(quiz, user, table_number=2)

    def test_manual_assign_rejects_invalid_table(self, quiz_event_4t):
        quiz = quiz_event_4t
        orphan = self._make_attended_orphan(quiz, "M")
        with pytest.raises(ValidationError):
            manual_assign_table(quiz, orphan, table_number=99)
        with pytest.raises(ValidationError):
            manual_assign_table(quiz, orphan, table_number=0)

    def test_orphan_disappears_from_list_after_assignment(self, quiz_event_4t):
        quiz = quiz_event_4t
        orphan = self._make_attended_orphan(quiz, "M")
        assert any(
            u["user_id"] == orphan.id for u in get_unassigned_attendees(quiz)
        )
        manual_assign_table(quiz, orphan, table_number=1)
        assert not any(
            u["user_id"] == orphan.id for u in get_unassigned_attendees(quiz)
        )

    def test_rejects_user_with_no_registration(self, quiz_event_4t):
        """A user with no EventRegistration for this event cannot be seated,
        even if a quiz host POSTs their user_id directly."""
        quiz = quiz_event_4t
        stranger = _make_user("stranger_m", "M")
        with pytest.raises(ValidationError):
            manual_assign_table(quiz, stranger, table_number=1)

    def test_rejects_user_with_confirmed_status(self, quiz_event_4t):
        """Registered but never checked in — must go through the QR /
        mark_attended flow before they can be seated."""
        from crush_lu.models.events import EventRegistration

        quiz = quiz_event_4t
        user = _make_user("not_checked_in", "F")
        EventRegistration.objects.create(
            event=quiz.event, user=user, status="confirmed"
        )
        with pytest.raises(ValidationError):
            manual_assign_table(quiz, user, table_number=1)

    def test_rejects_user_with_no_show_status(self, quiz_event_4t):
        """Once a host has marked someone as no_show, they need to be
        flipped back to attended before they can be seated."""
        from crush_lu.models.events import EventRegistration

        quiz = quiz_event_4t
        user = _make_user("noshow_m", "M")
        EventRegistration.objects.create(
            event=quiz.event, user=user, status="no_show"
        )
        with pytest.raises(ValidationError):
            manual_assign_table(quiz, user, table_number=1)
