"""Access gating for private-invitation events.

Covers ``event_detail`` (anonymous visitors used to hit a 500) and
``event_calendar_download`` (used to ignore the private-invitation gate).
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import (
    CrushProfile,
    EventInvitation,
    EventRegistration,
    MeetupEvent,
)

HOST = "crush.lu"
SECRET_TITLE = "SecretDinnerTitle"
SECRET_VENUE = "HiddenVenueName"


def _make_member(email):
    user = User.objects.create_user(
        username=email, email=email, password="pw", first_name="Test"
    )
    CrushProfile.objects.create(user=user)
    consent = user.data_consent
    consent.crushlu_consent_given = True
    consent.crushlu_consent_date = timezone.now()
    consent.save()
    return user


def _make_event(**overrides):
    now = timezone.now()
    fields = {
        "title": SECRET_TITLE,
        "description": "Hush hush",
        "event_type": "speed_dating",
        "location": SECRET_VENUE,
        "address_street": "rue Secrete",
        "address_number": "9",
        "address_postcode": "2229",
        "address_town": "Luxembourg",
        "canton": "Luxembourg",
        "date_time": now + timedelta(days=7),
        "registration_deadline": now + timedelta(days=5),
        "is_published": True,
        "is_private_invitation": True,
        "invitation_code": "private-code",
    }
    fields.update(overrides)
    return MeetupEvent.objects.create(**fields)


class PrivateEventDetailAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.event = _make_event()
        self.path = f"/en/events/{self.event.id}/"
        self.member = _make_member("member@example.com")

    def test_anonymous_is_redirected_to_login_not_500(self):
        self.client.raise_request_exception = False
        response = self.client.get(self.path, HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
        self.assertIn("next=", response["Location"])
        self.assertIn(self.path, response["Location"].replace("%2F", "/"))
        self.assertNotIn(SECRET_TITLE.encode(), response.content)

    def test_non_invited_member_keeps_invitation_only_redirect(self):
        self.client.force_login(self.member)
        response = self.client.get(self.path, HTTP_HOST=HOST)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn("login", response["Location"])
        self.assertIn("/events/", response["Location"])
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertIn("This event is by invitation only.", messages)

    def test_invited_member_sees_event(self):
        self.event.invited_users.add(self.member)
        self.client.force_login(self.member)
        response = self.client.get(self.path, HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)


class PrivateEventCalendarAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.event = _make_event()
        self.path = f"/en/events/{self.event.id}/calendar/"
        self.member = _make_member("member@example.com")

    def _get(self, path=None):
        return self.client.get(path or self.path, HTTP_HOST=HOST)

    def test_anonymous_private_calendar_is_404(self):
        response = self._get()
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(SECRET_TITLE.encode(), response.content)

    def test_non_invited_member_private_calendar_is_404(self):
        self.client.force_login(self.member)
        response = self._get()
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(SECRET_TITLE.encode(), response.content)
        self.assertNotIn(SECRET_VENUE.encode(), response.content)

    def test_pending_invitation_member_is_404(self):
        EventInvitation.objects.create(
            event=self.event,
            guest_email="member@example.com",
            guest_first_name="Test",
            guest_last_name="Guest",
            created_user=self.member,
            approval_status="pending_approval",
        )
        self.client.force_login(self.member)
        self.assertEqual(self._get().status_code, 404)

    def test_invited_users_member_gets_ics(self):
        self.event.invited_users.add(self.member)
        self.client.force_login(self.member)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertIn(SECRET_TITLE, response.content.decode())

    def test_approved_invitation_member_gets_ics(self):
        EventInvitation.objects.create(
            event=self.event,
            guest_email="member@example.com",
            guest_first_name="Test",
            guest_last_name="Guest",
            created_user=self.member,
            approval_status="approved",
        )
        self.client.force_login(self.member)
        self.assertEqual(self._get().status_code, 200)

    def test_registered_member_gets_ics(self):
        EventRegistration.objects.create(
            event=self.event, user=self.member, status="confirmed"
        )
        self.client.force_login(self.member)
        self.assertEqual(self._get().status_code, 200)

    def test_cancelled_registration_does_not_grant_access(self):
        EventRegistration.objects.create(
            event=self.event, user=self.member, status="cancelled"
        )
        self.client.force_login(self.member)
        self.assertEqual(self._get().status_code, 404)

    def test_public_event_calendar_unchanged_for_anonymous(self):
        public = _make_event(
            title="PublicMixer",
            is_private_invitation=False,
            invitation_code="",
        )
        response = self._get(f"/en/events/{public.id}/calendar/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        body = response.content.decode()
        self.assertIn("SUMMARY:PublicMixer", body)
        self.assertIn("STATUS:CONFIRMED", body)
        self.assertNotIn("STATUS:CANCELLED", body)

    def test_cancelled_event_emits_status_cancelled(self):
        public = _make_event(
            title="CancelledMixer",
            is_private_invitation=False,
            invitation_code="",
            is_cancelled=True,
        )
        response = self._get(f"/en/events/{public.id}/calendar/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("STATUS:CANCELLED", body)
        self.assertNotIn("STATUS:CONFIRMED", body)
