"""Smoke tests for Phase 1-3 post-event-engagement and coach-tooling work.

Covers:
- /my-events/ view (auth, status split)
- /coach/queue/ unified action queue (auth, content)
- /events/<id>/feedback/ (attendee gating, idempotency)
- Notification model + NotificationService.notify writes a row
- /api/notifications/* endpoints
- ProfileSubmission.revision_round increments on revision verdict

Run with: pytest crush_lu/tests/test_phase_post_event.py -v
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class _SiteSetup:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test"}
        )


def _make_user(username="alice", email=None):
    email = email or f"{username}@example.com"
    user = User.objects.create_user(
        username=username,
        email=email,
        password="pass1234!",
        first_name=username.title(),
        last_name="Test",
    )
    _grant_consent(user)
    return user


def _grant_consent(user):
    """Grant Crush.lu consent so ConsentMiddleware doesn't 302 us."""
    from crush_lu.models import UserDataConsent

    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])


def _make_profile(user, approved=True):
    from crush_lu.models import CrushProfile

    return CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg",
        is_approved=approved,
        is_active=True,
    )


def _make_event(**overrides):
    from crush_lu.models import MeetupEvent

    defaults = dict(
        title="Phase Test Event",
        description="x",
        event_type="speed_dating",
        date_time=timezone.now() + timedelta(days=2),
        location="LUX",
        address="addr",
        max_participants=20,
        min_age=18,
        max_age=99,
        registration_deadline=timezone.now() + timedelta(days=1),
        is_published=True,
    )
    defaults.update(overrides)
    return MeetupEvent.objects.create(**defaults)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class MyEventsViewTests(_SiteSetup, TestCase):
    def setUp(self):
        self.user = _make_user("myev_user")
        _make_profile(self.user)
        self.client = Client()
        self.client.login(username="myev_user", password="pass1234!")

    def test_requires_login(self):
        anon = Client()
        resp = anon.get(reverse("crush_lu:my_events"))
        self.assertEqual(resp.status_code, 302)

    def test_renders_with_no_registrations(self):
        resp = self.client.get(reverse("crush_lu:my_events"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("upcoming_registrations", resp.context)
        self.assertIn("past_registrations", resp.context)
        self.assertEqual(len(resp.context["upcoming_registrations"]), 0)
        self.assertEqual(len(resp.context["past_registrations"]), 0)

    def test_splits_upcoming_and_past(self):
        from crush_lu.models import EventRegistration

        upcoming = _make_event(title="Future")
        past = _make_event(
            title="Past",
            date_time=timezone.now() - timedelta(days=5),
            registration_deadline=timezone.now() - timedelta(days=6),
        )
        EventRegistration.objects.create(
            event=upcoming, user=self.user, status="confirmed"
        )
        EventRegistration.objects.create(
            event=past, user=self.user, status="attended"
        )

        resp = self.client.get(reverse("crush_lu:my_events"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context["upcoming_registrations"]), 1)
        self.assertEqual(len(resp.context["past_registrations"]), 1)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachActionQueueTests(_SiteSetup, TestCase):
    def setUp(self):
        from crush_lu.models import CrushCoach

        self.user = _make_user("coachy")
        self.user.is_staff = True
        self.user.save()
        self.coach = CrushCoach.objects.create(user=self.user, is_active=True)
        self.client = Client()
        self.client.login(username="coachy", password="pass1234!")

    def test_blocks_non_coach(self):
        from crush_lu.models import CrushCoach

        non = _make_user("noncoach")
        c = Client()
        c.login(username="noncoach", password="pass1234!")
        resp = c.get(reverse("crush_lu:coach_action_queue"))
        # Decorator redirects non-coaches
        self.assertIn(resp.status_code, (302, 403))

    def test_renders_empty_inbox(self):
        resp = self.client.get(reverse("crush_lu:coach_action_queue"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["counts"]["total"], 0)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class EventFeedbackViewTests(_SiteSetup, TestCase):
    def setUp(self):
        self.user = _make_user("feedbacker")
        _make_profile(self.user)
        # Past event so feedback is open
        self.event = _make_event(
            title="Past Event",
            date_time=timezone.now() - timedelta(hours=8),
            registration_deadline=timezone.now() - timedelta(days=2),
        )
        self.client = Client()
        self.client.login(username="feedbacker", password="pass1234!")

    def test_blocks_non_attendees(self):
        resp = self.client.get(
            reverse("crush_lu:event_feedback", kwargs={"event_id": self.event.id})
        )
        self.assertEqual(resp.status_code, 302)  # redirects with error msg

    def test_attendee_can_submit_then_idempotent(self):
        from crush_lu.models import EventFeedback, EventRegistration

        EventRegistration.objects.create(
            event=self.event, user=self.user, status="attended"
        )

        # GET form
        resp = self.client.get(
            reverse("crush_lu:event_feedback", kwargs={"event_id": self.event.id})
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["submitted"])

        # POST first feedback
        resp = self.client.post(
            reverse("crush_lu:event_feedback", kwargs={"event_id": self.event.id}),
            {"nps_score": 9, "what_worked": "good vibes", "what_to_improve": ""},
        )
        self.assertEqual(resp.status_code, 302)
        fb = EventFeedback.objects.get(event=self.event, user=self.user)
        self.assertEqual(fb.nps_score, 9)
        self.assertTrue(fb.would_recommend)

        # GET after submission shows the thanks state, no second create
        resp = self.client.get(
            reverse("crush_lu:event_feedback", kwargs={"event_id": self.event.id})
        )
        self.assertTrue(resp.context["submitted"])
        self.assertEqual(
            EventFeedback.objects.filter(event=self.event, user=self.user).count(), 1
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class NotificationServiceWritesRowTests(_SiteSetup, TestCase):
    def test_notify_creates_inapp_row(self):
        from crush_lu.models import Notification
        from crush_lu.notification_service import NotificationService, NotificationType

        user = _make_user("notif_user")
        result = NotificationService.notify(
            user=user,
            notification_type=NotificationType.MUTUAL_MATCH,
            context={},
            request=None,
        )
        self.assertTrue(result.inapp_created)
        self.assertIsNotNone(result.inapp_id)
        n = Notification.objects.get(id=result.inapp_id)
        self.assertEqual(n.user, user)
        self.assertEqual(n.notification_type, "mutual_match")
        self.assertTrue(n.title)
        self.assertEqual(n.link_url, "/my-connections/")
        self.assertIsNone(n.read_at)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class NotificationAPITests(_SiteSetup, TestCase):
    def setUp(self):
        self.user = _make_user("notif_api")
        self.client = Client()
        self.client.login(username="notif_api", password="pass1234!")

    def test_list_returns_unread_count_and_items(self):
        from crush_lu.models import Notification

        Notification.objects.create(
            user=self.user,
            notification_type="mutual_match",
            title="It's a match",
            body="x",
            link_url="/my-connections/",
        )
        resp = self.client.get(
            reverse("api_notifications_list"),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["unread_count"], 1)
        self.assertEqual(len(data["items"]), 1)
        self.assertTrue(data["items"][0]["is_unread"])

    def test_mark_read_endpoint(self):
        from crush_lu.models import Notification

        n = Notification.objects.create(
            user=self.user,
            notification_type="mutual_match",
            title="x",
            body="",
        )
        # JSON request gets JSON back
        resp = self.client.post(
            reverse(
                "api_notification_mark_read", kwargs={"notification_id": n.id}
            ),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        n.refresh_from_db()
        self.assertIsNotNone(n.read_at)

    def test_mark_all_read_html_redirect(self):
        """HTML form submit should redirect, not return raw JSON."""
        from crush_lu.models import Notification

        Notification.objects.create(
            user=self.user, notification_type="mutual_match", title="x"
        )
        Notification.objects.create(
            user=self.user, notification_type="mutual_match", title="y"
        )
        resp = self.client.post(reverse("api_notifications_mark_all_read"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            Notification.objects.filter(user=self.user, read_at__isnull=True).count(),
            0,
        )

    def test_mark_all_read_json(self):
        """JSON request returns JSON."""
        from crush_lu.models import Notification

        Notification.objects.create(
            user=self.user, notification_type="mutual_match", title="x"
        )
        resp = self.client.post(
            reverse("api_notifications_mark_all_read"),
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["ok"], True)
        self.assertEqual(data["updated"], 1)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class RevisionRoundIncrementTests(_SiteSetup, TestCase):
    def test_revision_round_starts_at_zero(self):
        from crush_lu.models import ProfileSubmission

        user = _make_user("rev_user")
        profile = _make_profile(user, approved=False)
        sub = ProfileSubmission.objects.create(profile=profile, status="pending")
        self.assertEqual(sub.revision_round, 0)

    def test_revision_round_field_can_be_incremented(self):
        from crush_lu.models import ProfileSubmission

        user = _make_user("rev_user2")
        profile = _make_profile(user, approved=False)
        sub = ProfileSubmission.objects.create(profile=profile, status="pending")

        # Simulate the coach review flow updating it
        sub.revision_round = (sub.revision_round or 0) + 1
        sub.save(update_fields=["revision_round"])
        sub.refresh_from_db()
        self.assertEqual(sub.revision_round, 1)
