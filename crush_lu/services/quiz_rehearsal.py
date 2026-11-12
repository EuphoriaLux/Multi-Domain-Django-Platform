"""Quiz Night rehearsal tooling (staging / local only).

Coaches need to train on Quiz Night on test.crush.lu. The normal flow makes
that painful: a replay needs Pause/Finish then "Reset Quiz", which wipes scores
but keeps the old seating, and tables only fill from real check-ins.

Everything here refuses to run unless :func:`rehearsal_enabled` is true, so a
production slot can never reach it. Synthetic guests live on the ``crush.test``
email domain, which ``signals.TEST_EMAIL_DOMAINS`` already keeps out of
Outlook sync.
"""

import logging
import os
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

GUEST_USERNAME_PREFIX = "quizrehearsal_"
GUEST_EMAIL_DOMAIN = "crush.test"


def rehearsal_enabled():
    """True on the staging slot (``DJANGO_ENV=staging``) or in local DEBUG."""
    return os.environ.get("DJANGO_ENV") == "staging" or bool(settings.DEBUG)


def require_rehearsal():
    if not rehearsal_enabled():
        raise PermissionDenied("Quiz rehearsal is only available on staging.")


def add_rehearsal_guests(quiz, count):
    """Create/reuse ``count`` synthetic attendees, mark them attended and seat them.

    Seating goes through ``assign_table_on_checkin`` so tables, memberships and
    round-0 rotation rows are built exactly as a real check-in builds them.
    Returns the number of guests now attended and seated by this call.
    """
    from crush_lu.models import CrushProfile

    require_rehearsal()
    User = get_user_model()
    seated = 0
    for index in range(1, count + 1):
        username = f"{GUEST_USERNAME_PREFIX}{index}"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@{GUEST_EMAIL_DOMAIN}",
                "first_name": f"Guest{index}",
                "last_name": "Rehearsal",
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        if not CrushProfile.objects.filter(user=user).exists():
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1990, 1, 1),
                # Alternate genders so anchors and rotators both fill.
                gender="M" if index % 2 else "F",
                phone_verified=True,
                phone_verified_at=timezone.now(),
                location="Luxembourg",
                bio="Quiz rehearsal guest — staging only.",
                preferred_language="en",
                is_approved=True,
                is_active=True,
                approved_at=timezone.now(),
                verification_status="verified",
                verification_method="admin",
                completion_status="submitted",
            )
        user.refresh_from_db()
        _attend_and_seat(quiz, user)
        seated += 1
    return seated


def add_coaches_as_guests(quiz, users):
    """Register real (coach) accounts as attended guests so each can open the
    phone view on their own device."""
    require_rehearsal()
    for user in users:
        _attend_and_seat(quiz, user)


def _attend_and_seat(quiz, user):
    from crush_lu.models.events import EventRegistration
    from crush_lu.services.quiz_rotation import assign_table_on_checkin

    reg, created = EventRegistration.objects.get_or_create(
        event=quiz.event,
        user=user,
        defaults={"status": "attended", "registered_at": timezone.now()},
    )
    if not created and reg.status != "attended":
        reg.status = "attended"
        reg.save(update_fields=["status"])
    assign_table_on_checkin(quiz, user)


def reset_quiz_for_rehearsal(quiz, reseed=True):
    """Return a quiz in *any* state to a clean, replayable draft.

    Unlike ``reset_quiz_to_draft`` this works from ``active`` too, and it also
    throws away seating (memberships + rotation rows) so the next Start builds
    fresh tables. With ``reseed`` the attended guests are seated again.

    Lock order follows the other seat operations: quiz row first.
    Returns the status dict the ``quiz.status`` group event expects.
    """
    from crush_lu.models.quiz import (
        IndividualScore,
        QuizEvent,
        QuizRotationSchedule,
        QuizTableMembership,
        TableRoundScore,
    )

    require_rehearsal()

    with transaction.atomic():
        locked = QuizEvent.objects.select_for_update().get(pk=quiz.pk)
        TableRoundScore.objects.filter(quiz=locked).delete()
        IndividualScore.objects.filter(quiz=locked).delete()
        QuizRotationSchedule.objects.filter(quiz=locked).delete()
        QuizTableMembership.objects.filter(table__quiz=locked).delete()

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

        if reseed:
            from crush_lu.models.events import EventRegistration
            from crush_lu.services.quiz_rotation import assign_table_on_checkin

            attended = EventRegistration.objects.filter(
                event=locked.event, status="attended"
            ).select_related("user")
            for reg in attended:
                assign_table_on_checkin(locked, reg.user)

    return {
        "status": "draft",
        "reset": True,
        "cleared_scores": True,
        "rehearsal": True,
    }


def broadcast_reset(quiz, data):
    """Tell open host/guest/big-screen pages the quiz is back in the lobby."""
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(
            f"quiz_{quiz.pk}", {"type": "quiz.status", "data": data}
        )
    except Exception:  # pragma: no cover - broadcast is best effort
        logger.warning("Quiz rehearsal reset broadcast failed", exc_info=True)
