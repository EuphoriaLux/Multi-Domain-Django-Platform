"""An automatic curated-group remedy keeps every seat and asks a coach.

Incident 2026-10-28, event 26 (free curated speed dating): one invited member
cancelled 27 minutes before the start. Her group degraded, the automatic remedy
found no viable replacement and, because nobody on a free event counts as paid,
sent all 11 remaining invitees back to "applied" with a "your application is
being reconsidered" email. Their tickets 404'd and the door scanner refused
them, yet the evening ran. Earlier that day the same remedy had also rebuilt
the group with an applicant nobody invited or told.

Now a remedy nobody explicitly asked for holds on a free event or within
CURATED_AUTOMATIC_REMEDY_HOLD_HOURS of the start: every seat stays, the group
stays degraded, and the event's coaches are alerted. While holding, it only
reshuffles members who already hold a seat. "Reproject or compensate degraded
groups" is still the explicit decision that releases seats.

Incident: ai-memory-hub/memories/crush-curated-auto-withdrawal.md
Run with: pytest crush_lu/tests/test_curated_auto_remedy_hold.py -v
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedGroupNotification,
    EventRegistration,
    EventRegistrationPreference,
    MeetupEvent,
)
from crush_lu.models.notifications import Notification
from crush_lu.models.profiles import CrushCoach, CrushProfile, UserDataConsent
from crush_lu.services.curated_group_workflow import (
    approve_current_generation,
    automatic_remedy_must_hold,
    generate_group_projection,
    repair_degraded_event_groups,
)

User = get_user_model()

ALERT_TYPE = "curated_group_coach_alert"


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class AutomaticRemedyHoldTests(TestCase):
    def setUp(self):
        # Every test's viewer is the same user pk on SQLite and they share one
        # @ratelimit counter (AGENTS.md traps).
        cache.clear()

    def _member(self, event, index):
        user = User.objects.create_user(
            username=f"hold-{event.pk}-{index}@example.com",
            email=f"hold-{event.pk}-{index}@example.com",
            password="testpass123",
            first_name=f"Member {index}",
        )
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="NB",
            location="Luxembourg",
            event_languages=["en"],
            preferred_language="en",
        )
        registration = EventRegistration.objects.create(
            event=event, user=user, status="applied"
        )
        EventRegistrationPreference.objects.create(
            registration=registration,
            preferred_genders=[],
            preferred_age_min=18,
            preferred_age_max=99,
            languages=[],
        )
        return registration

    def _invited_group(self, *, fee="0.00", starts_in, applicants=6):
        """One provisional group of six, seated the way Invite seats it."""
        event = MeetupEvent.objects.create(
            title="Hold evening",
            description="Testing the automatic remedy hold",
            event_type="speed_dating",
            registration_mode="curated",
            date_time=timezone.now() + starts_in,
            registration_deadline=timezone.now() - timedelta(hours=1),
            location="Luxembourg",
            address="Test venue",
            max_participants=12,
            group_size=6,
            planned_groups=1,
            registration_fee=Decimal(fee),
            profile_requirement="none",
            is_published=True,
        )
        coach_user = User.objects.create_user(
            username=f"coach-{event.pk}@example.com",
            email=f"coach-{event.pk}@example.com",
            password="testpass123",
            first_name="Coach",
        )
        self.coach = CrushCoach.objects.create(
            user=coach_user, bio="coach", is_active=True
        )
        event.coaches.add(self.coach)
        registrations = [self._member(event, index) for index in range(applicants)]
        stored = generate_group_projection(event, deterministic_seed="hold")
        approved = approve_current_generation(event)
        seat = "confirmed" if Decimal(fee) == 0 else "pending"
        EventRegistration.objects.filter(
            pk__in=approved.applied_registration_ids
        ).update(status=seat)
        group = CuratedEventGroup.objects.get(pk=stored.group_ids[0])
        invited = list(
            EventRegistration.objects.filter(
                pk__in=approved.applied_registration_ids
            ).order_by("pk")
        )
        mail.outbox = []
        return event, group, invited, registrations

    def _cancel(self, registration):
        with self.captureOnCommitCallbacks(execute=True):
            registration.status = "cancelled"
            registration.save(update_fields=["status"])

    def _coach_alerts(self):
        return Notification.objects.filter(
            user=self.coach.user, notification_type=ALERT_TYPE
        )

    def test_hold_rule(self):
        now = timezone.now()
        free_far = MeetupEvent(
            registration_fee=Decimal("0"), date_time=now + timedelta(days=7)
        )
        paid_near = MeetupEvent(
            registration_fee=Decimal("15"), date_time=now + timedelta(hours=47)
        )
        paid_far = MeetupEvent(
            registration_fee=Decimal("15"), date_time=now + timedelta(hours=49)
        )
        self.assertTrue(automatic_remedy_must_hold(free_far, now))
        self.assertTrue(automatic_remedy_must_hold(paid_near, now))
        self.assertFalse(automatic_remedy_must_hold(paid_far, now))

    def test_late_cancellation_on_free_event_keeps_every_other_seat(self):
        event, group, invited, _ = self._invited_group(starts_in=timedelta(minutes=27))
        departing, staying = invited[0], invited[1:]

        self._cancel(departing)

        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_DEGRADED)
        self.assertEqual(
            set(
                EventRegistration.objects.filter(
                    pk__in=[r.pk for r in staying]
                ).values_list("status", flat=True)
            ),
            {"confirmed"},
        )
        self.assertEqual(
            CuratedEventGroupMembership.objects.filter(
                group=group, released_at__isnull=True
            ).count(),
            len(invited),
        )
        self.assertFalse(
            CuratedGroupNotification.objects.filter(
                event=event, kind=CuratedGroupNotification.Kind.WITHDRAWAL
            ).exists()
        )
        alert = self._coach_alerts().get()
        self.assertIn("needs a decision", alert.title)
        self.assertEqual(alert.link_url, f"/coach/events/{event.pk}/?status=groups")
        coach_mail = [m for m in mail.outbox if self.coach.user.email in m.to]
        self.assertEqual(len(coach_mail), 1)
        self.assertIn("needs a decision", coach_mail[0].subject)

    def test_reminders_off_does_not_silence_the_coach_email(self):
        from crush_lu.models import EmailPreference

        event, group, invited, _ = self._invited_group(starts_in=timedelta(hours=3))
        prefs = EmailPreference.get_or_create_for_user(self.coach.user)
        prefs.email_event_reminders = False
        prefs.save()

        self._cancel(invited[0])

        self.assertEqual(
            len([m for m in mail.outbox if self.coach.user.email in m.to]), 1
        )

    def test_second_cancellation_does_not_alert_again(self):
        event, group, invited, _ = self._invited_group(starts_in=timedelta(hours=3))
        self._cancel(invited[0])
        self._cancel(invited[1])

        self.assertEqual(self._coach_alerts().count(), 1)
        self.assertEqual(
            set(
                EventRegistration.objects.filter(
                    pk__in=[r.pk for r in invited[2:]]
                ).values_list("status", flat=True)
            ),
            {"confirmed"},
        )

    def test_hold_reshuffles_only_seat_holders_and_places_nobody_new(self):
        event, group, invited, registrations = self._invited_group(
            starts_in=timedelta(hours=3), applicants=7
        )
        invited_ids = {r.pk for r in invited}
        reserve = next(r for r in registrations if r.pk not in invited_ids)

        self._cancel(invited[0])

        reserve.refresh_from_db()
        self.assertEqual(reserve.status, "applied")
        self.assertFalse(
            CuratedEventGroupMembership.objects.filter(
                registration=reserve, released_at__isnull=True
            ).exists()
        )
        self.assertEqual(self._coach_alerts().count(), 1)

    def test_explicit_repair_still_releases_a_held_group(self):
        event, group, invited, _ = self._invited_group(starts_in=timedelta(hours=3))
        self._cancel(invited[0])
        staff = User.objects.create_superuser(
            username="staff@example.com", email="staff@example.com", password="x"
        )

        with patch(
            "crush_lu.services.curated_group_notifications."
            "deliver_curated_group_notifications"
        ):
            remedy = repair_degraded_event_groups(event, actor=staff, explicit=True)

        self.assertEqual(remedy.action, "compensated")
        self.assertEqual(
            set(
                EventRegistration.objects.filter(
                    pk__in=[r.pk for r in invited[1:]]
                ).values_list("status", flat=True)
            ),
            {"applied"},
        )
        self.assertEqual(
            CuratedGroupNotification.objects.filter(
                event=event, kind=CuratedGroupNotification.Kind.WITHDRAWAL
            ).count(),
            len(invited) - 1,
        )

    def test_paid_event_far_from_start_keeps_the_existing_remedy(self):
        event, group, invited, _ = self._invited_group(
            fee="15.00", starts_in=timedelta(days=5)
        )

        with patch(
            "crush_lu.services.curated_group_notifications."
            "deliver_curated_group_notifications"
        ):
            self._cancel(invited[0])

        group.refresh_from_db()
        self.assertEqual(group.status, CuratedEventGroup.STATUS_CANCELLED)
        self.assertEqual(
            set(
                EventRegistration.objects.filter(
                    pk__in=[r.pk for r in invited[1:]]
                ).values_list("status", flat=True)
            ),
            {"applied"},
        )
        self.assertFalse(self._coach_alerts().exists())

    def test_rebuilt_group_with_uninvited_member_alerts_coach_to_invite(self):
        event, group, invited, registrations = self._invited_group(
            fee="15.00", starts_in=timedelta(days=5), applicants=7
        )

        self._cancel(invited[0])

        replacement = (
            CuratedEventGroup.objects.filter(
                event=event, status=CuratedEventGroup.STATUS_PROVISIONAL
            )
            .order_by("-generation")
            .first()
        )
        self.assertIsNotNone(replacement)
        uninvited = replacement.memberships.filter(
            released_at__isnull=True, registration__status="applied"
        ).count()
        self.assertEqual(uninvited, 1)
        alert = self._coach_alerts().get()
        self.assertIn("needs inviting", alert.title)
        self.assertIn("1 member", alert.body)
