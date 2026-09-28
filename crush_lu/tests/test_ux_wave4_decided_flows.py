"""UX Wave 4 · WP12b decided-flows.

Decisions 5-13 (spark_create_journey redirects to the Connect hub), 8-13
(on-break invite warning) and 4-18 (event share uses the referral link).

Literal paths, not reverse(), per AGENTS.md.
"""

from decimal import Decimal
from unittest import mock

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone, translation

from crush_lu.models import CrushCoach, EventInvitation, ReferralCode
from crush_lu.models.crush_spark import CrushSpark
from crush_lu.models.events import MeetupEvent
from crush_lu.models.profiles import CrushProfile, UserDataConsent

User = get_user_model()
RESEND = "/en/signup/resend-verification/"
SEND = (
    "allauth.account.internal.flows.email_verification."
    "send_verification_email_to_address"
)


def _unverified_user(email):
    user = User.objects.create_user(username=email, email=email, password="x-Pa55!")
    address = EmailAddress.objects.create(
        user=user, email=email, primary=True, verified=False
    )
    return user, address


def _member(email, **profile_kwargs):
    user = User.objects.create_user(
        username=email, email=email, password="password123", first_name="Mia"
    )
    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
    )
    profile = CrushProfile.objects.create(
        user=user,
        verification_status="verified",
        completion_status="step4",
        **profile_kwargs,
    )
    return user, profile


def _event(**kwargs):
    defaults = dict(
        title="WP12 Event",
        description="WP12 event",
        event_type="speed_dating",
        location="Luxembourg City",
        address="10 Grand Rue",
        date_time=timezone.now() + timezone.timedelta(days=3),
        registration_deadline=timezone.now() + timezone.timedelta(days=2),
        registration_fee=Decimal("15.00"),
        is_published=True,
    )
    defaults.update(kwargs)
    return MeetupEvent.objects.create(**defaults)


# ---------------------------------------------------------------------------
# 5-13: spark_create_journey
# ---------------------------------------------------------------------------


class SparkCreateJourneyRedirectTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_create_journey_is_a_301_to_the_connect_hub(self):
        user, _ = _member("spark@crush.lu")
        spark = CrushSpark.objects.create(
            event=_event(), sender=user, sender_description="red dress"
        )
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(user)
        response = client.get(f"/en/sparks/{spark.id}/create-journey/")
        self.assertEqual(response.status_code, 301)
        self.assertIn("/crush-connect/home/", response.url)

    def test_event_spark_links_keep_their_teaser_target(self):
        response = Client(HTTP_HOST="crush.lu").get("/en/events/1/spark/request/")
        self.assertNotIn("/crush-connect/home/", response.url)


# ---------------------------------------------------------------------------
# 8-13: warn when inviting an on-break member
# ---------------------------------------------------------------------------


class OnBreakInvitationWarningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.member, self.profile = _member(
            "resting@crush.lu", on_break_at=timezone.now()
        )
        coach_user, _ = _member("coach@crush.lu")
        self.coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        self.event = _event(is_private_invitation=True, registration_fee=Decimal("0"))
        self.event.coaches.add(self.coach)
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(coach_user)

    def _invite(self, email):
        with mock.patch(
            "crush_lu.email_notifications.send_external_guest_invitation_email",
            return_value=True,
        ):
            return self.client.post(
                f"/en/coach/event/{self.event.pk}/invitations/",
                {
                    "action": "send_invitation",
                    "email": email,
                    "first_name": "Mia",
                    "last_name": "Rest",
                },
            )

    def test_inviting_an_on_break_member_warns_but_still_invites(self):
        response = self._invite("Resting@crush.lu")
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("taking a break" in t for t in texts), texts)
        self.assertTrue(
            EventInvitation.objects.filter(
                event=self.event, guest_email="Resting@crush.lu"
            ).exists()
        )

    def test_active_member_gets_no_warning(self):
        CrushProfile.objects.filter(pk=self.profile.pk).update(on_break_at=None)
        response = self._invite("resting@crush.lu")
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertFalse(any("taking a break" in t for t in texts), texts)

    def test_warning_is_translated(self):
        from crush_lu.services.on_break import warn_if_inviting_on_break

        request = RequestFactory().get("/")
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        with translation.override("de"):
            warn_if_inviting_on_break(request, users=[self.member])
        texts = [str(m) for m in get_messages(request)]
        self.assertEqual(len(texts), 1)
        self.assertIn("Pause", texts[0])
        self.assertIn("Mia", texts[0])

    def _direct_warning(self, emails):
        from crush_lu.services.on_break import warn_if_inviting_on_break

        request = RequestFactory().get("/")
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        names = warn_if_inviting_on_break(request, emails=emails)
        return names, [str(m) for m in get_messages(request)]

    def test_verified_secondary_address_warns_once(self):
        # Review P2: a merge moves verified addresses onto the keeper, so an
        # invite to a secondary address must still find the on-break member.
        EmailAddress.objects.create(
            user=self.member, email="alias@crush.lu", primary=False, verified=True
        )
        names, texts = self._direct_warning(["Alias@crush.lu"])
        self.assertEqual(names, ["Mia"])
        self.assertEqual(len(texts), 1)
        self.assertIn("taking a break", texts[0])

    def test_primary_and_secondary_address_name_the_member_once(self):
        EmailAddress.objects.create(
            user=self.member, email="alias@crush.lu", primary=False, verified=True
        )
        names, _texts = self._direct_warning(["alias@crush.lu", "resting@crush.lu"])
        self.assertEqual(names, ["Mia"])

    def test_unverified_secondary_address_does_not_warn(self):
        EmailAddress.objects.create(
            user=self.member, email="alias@crush.lu", primary=False, verified=False
        )
        names, texts = self._direct_warning(["alias@crush.lu"])
        self.assertEqual(names, [])
        self.assertEqual(texts, [])

    def _admin_save_existing_invitation(self, changed_data, new_event=None):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.events import EventInvitationAdmin

        invitation = EventInvitation.objects.create(
            event=self.event,
            guest_email="resting@crush.lu",
            guest_first_name="Mia",
            guest_last_name="Rest",
        )
        if new_event is not None:
            invitation.event = new_event
        request = RequestFactory().post("/")
        request.user = self.coach.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        EventInvitationAdmin(EventInvitation, crush_admin_site).save_model(
            request, invitation, form=mock.Mock(changed_data=changed_data), change=True
        )
        return [str(m) for m in get_messages(request)]

    def test_admin_invitation_moved_to_another_event_warns(self):
        # Review P2: changing only the event is a new invite for that event.
        other = _event(
            title="Other", is_private_invitation=True, registration_fee=Decimal("0")
        )
        texts = self._admin_save_existing_invitation(["event"], new_event=other)
        self.assertTrue(any("taking a break" in t for t in texts), texts)

    def test_admin_invitation_unrelated_edit_does_not_rewarn(self):
        texts = self._admin_save_existing_invitation(["guest_first_name"])
        self.assertEqual(texts, [])

    def test_admin_invitation_save_warns(self):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.events import EventInvitationAdmin

        request = RequestFactory().post("/")
        request.user = self.coach.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        invitation = EventInvitation(
            event=self.event,
            guest_email="resting@crush.lu",
            guest_first_name="Mia",
            guest_last_name="Rest",
        )
        EventInvitationAdmin(EventInvitation, crush_admin_site).save_model(
            request, invitation, form=mock.Mock(changed_data=[]), change=False
        )
        texts = [str(m) for m in get_messages(request)]
        self.assertTrue(any("taking a break" in t for t in texts), texts)

    def _event_admin_save_related(
        self, initial_users, users, guest_forms=(), changed_data=()
    ):
        from django.contrib import admin

        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.events import MeetupEventAdmin

        request = RequestFactory().post("/")
        request.user = self.coach.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        form = mock.Mock(
            instance=self.event,
            initial={"invited_users": initial_users},
            cleaned_data={"invited_users": users},
            changed_data=list(changed_data),
        )
        formsets = [mock.Mock(model=EventInvitation, forms=list(guest_forms))]
        with mock.patch.object(admin.ModelAdmin, "save_related"):
            MeetupEventAdmin(MeetupEvent, crush_admin_site).save_related(
                request, form, formsets, change=True
            )
        return [str(m) for m in get_messages(request)]

    def test_event_admin_warns_for_a_newly_invited_user(self):
        texts = self._event_admin_save_related([], [self.member])
        self.assertTrue(any("taking a break" in t for t in texts), texts)

    def test_event_admin_does_not_rewarn_an_already_invited_user(self):
        texts = self._event_admin_save_related([self.member], [self.member])
        self.assertEqual(texts, [])

    def test_event_admin_warns_for_a_new_inline_guest_row(self):
        row = mock.Mock(
            changed_data=["guest_email"],
            cleaned_data={"guest_email": "resting@crush.lu", "DELETE": False},
        )
        texts = self._event_admin_save_related([], [], guest_forms=[row])
        self.assertTrue(any("taking a break" in t for t in texts), texts)

    def test_event_admin_warns_existing_invitees_when_made_private(self):
        # Review P2: public -> private makes unchanged invitees effective.
        EventInvitation.objects.create(
            event=self.event,
            guest_email="resting@crush.lu",
            guest_first_name="Mia",
            guest_last_name="Rest",
        )
        by_user = self._event_admin_save_related(
            [self.member], [self.member], changed_data=["is_private_invitation"]
        )
        self.assertEqual(len(by_user), 1)
        self.assertIn("taking a break", by_user[0])
        self.assertIn("Mia", by_user[0])

    def test_event_admin_warns_unchanged_guest_rows_when_made_private(self):
        EventInvitation.objects.create(
            event=self.event,
            guest_email="resting@crush.lu",
            guest_first_name="Mia",
            guest_last_name="Rest",
        )
        texts = self._event_admin_save_related(
            [], [], changed_data=["is_private_invitation"]
        )
        self.assertTrue(any("taking a break" in t for t in texts), texts)


# ---------------------------------------------------------------------------
# 4-18: the share button uses the referral link
# ---------------------------------------------------------------------------


class EventShareReferralTests(TestCase):
    def setUp(self):
        cache.clear()
        self.event = _event()
        self.client = Client(HTTP_HOST="crush.lu")

    def _share_url(self):
        from html.parser import HTMLParser

        found = {}

        class Finder(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get("id") == "shareEventBtn":
                    found["url"] = attrs.get("data-share-url")

        Finder().feed(self.client.get(f"/en/events/{self.event.pk}/").content.decode())
        return found["url"]

    def test_member_with_a_code_shares_the_referral_link_to_the_event(self):
        user, profile = _member("sharer@crush.lu")
        code = ReferralCode.objects.create(referrer=profile)
        self.client.force_login(user)
        self.assertEqual(
            self._share_url(),
            f"http://crush.lu/en/r/{code.code}/?next=%2Fen%2Fevents%2F{self.event.pk}%2F",
        )

    def test_member_without_a_code_shares_the_plain_url(self):
        user, _ = _member("nocode@crush.lu")
        self.client.force_login(user)
        self.assertEqual(
            self._share_url(), f"http://crush.lu/en/events/{self.event.pk}/"
        )
        self.assertFalse(ReferralCode.objects.exists())

    def test_visitor_shares_the_plain_url(self):
        self.assertEqual(
            self._share_url(), f"http://crush.lu/en/events/{self.event.pk}/"
        )

    def test_referral_link_lands_on_the_event_and_keeps_the_code(self):
        _user, profile = _member("referrer@crush.lu")
        code = ReferralCode.objects.create(referrer=profile)
        response = self.client.get(
            f"/en/r/{code.code}/", {"next": f"/en/events/{self.event.pk}/"}
        )
        self.assertEqual(response.url, f"/en/events/{self.event.pk}/")
        self.assertEqual(self.client.session["referral_code"], code.code)

    def test_offsite_next_is_ignored(self):
        _user, profile = _member("referrer2@crush.lu")
        code = ReferralCode.objects.create(referrer=profile)
        response = self.client.get(
            f"/en/r/{code.code}/", {"next": "https://evil.example/"}
        )
        self.assertIn("/signup/", response.url)

    def test_unknown_code_ignores_next_and_keeps_the_signup_redirect(self):
        # Review P2: an unknown/inactive code keeps main's signup redirect.
        response = self.client.get(
            "/en/r/NOSUCHCODE/", {"next": f"/en/events/{self.event.pk}/"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/signup/", response.url)
        self.assertNotIn("ref=", response.url)

    def test_inactive_code_ignores_next(self):
        _user, profile = _member("inactive@crush.lu")
        code = ReferralCode.objects.create(referrer=profile, is_active=False)
        response = self.client.get(
            f"/en/r/{code.code}/", {"next": f"/en/events/{self.event.pk}/"}
        )
        self.assertIn("/signup/", response.url)
