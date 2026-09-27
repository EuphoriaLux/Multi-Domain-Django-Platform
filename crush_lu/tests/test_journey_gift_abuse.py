"""Journey gift abuse guards (UX Wave 2, finding 7-01).

A gift can email any address from the Crush.lu domain, so creation is limited
to approved, non-banned members and capped at 5 POSTs a day. Recipients see
who sent it ("Created by <first name> · verified Crush.lu member") and can
close and report it, which expires the gift and alerts the team.

Paths are literal: ``reverse("crush_lu:...")`` builds ``/crush/...`` paths
that 404 under ``HTTP_HOST=crush.lu``.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase

User = get_user_model()

CREATE_URL = "/en/journey/gift/create/"


class GiftAbuseTestBase(TestCase):
    def setUp(self):
        # gift_create is @ratelimit(key="user") and gift_report is
        # @ratelimit(key="ip"); the cache survives between tests while the
        # SQLite PK sequence rolls back, so counters would be shared.
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def _user(self, username, *, approved=None, banned=False, staff=False):
        from crush_lu.models import CrushProfile, UserDataConsent

        user = User.objects.create_user(
            username=username,
            email=username,
            password="testpass123",
            first_name=username.split("@")[0].capitalize(),
            last_name="Lastname",
            is_staff=staff,
        )
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"crushlu_consent_given": True, "crushlu_banned": banned},
        )
        if approved is not None:
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 1, 1),
                gender="F",
                location="Luxembourg",
                is_approved=approved,
            )
        return user

    def _login(self, user):
        self.client.login(username=user.username, password="testpass123")

    def _gift(self, sender, **kwargs):
        from crush_lu.models import JourneyGift

        return JourneyGift.objects.create(
            sender=sender,
            recipient_name="Marie",
            date_first_met=date(2024, 2, 14),
            location_first_met="Luxembourg City",
            **kwargs,
        )


class GiftCreateGateTests(GiftAbuseTestBase):
    def _post(self):
        return self.client.post(
            CREATE_URL,
            {
                "recipient_name": "Marie",
                "date_first_met": "2024-02-14",
                "location_first_met": "Luxembourg City",
                "recipient_email": "stranger@example.com",
            },
        )

    def test_unapproved_member_is_redirected_and_cannot_send(self):
        from crush_lu.models import JourneyGift

        self._login(self._user("pending@example.com", approved=False))

        get = self.client.get(CREATE_URL)
        self.assertEqual(get.status_code, 302)
        self.assertIn("/dashboard/", get["Location"])

        post = self._post()
        self.assertEqual(post.status_code, 302)
        self.assertFalse(JourneyGift.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_account_without_profile_is_redirected(self):
        from crush_lu.models import JourneyGift

        self._login(self._user("noprofile@example.com"))
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(JourneyGift.objects.exists())

    def test_banned_member_is_sent_to_banned_page(self):
        # Enforced globally by CrushConsentMiddleware (already on main); kept
        # as a regression guard because an approved profile passes the gate.
        from crush_lu.models import JourneyGift

        self._login(self._user("banned@example.com", approved=True, banned=True))

        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/banned/", response["Location"])
        self.assertFalse(JourneyGift.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_deactivated_approved_member_is_blocked(self):
        # A coach "Deactivate" keeps is_approved=True but sets is_active=False.
        from crush_lu.models import CrushProfile, JourneyGift

        user = self._user("paused@example.com", approved=True)
        CrushProfile.objects.filter(user=user).update(is_active=False)
        self._login(user)

        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard/", response["Location"])
        self.assertFalse(JourneyGift.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

        gift = self._gift(user)
        self.client.logout()
        landing = self.client.get(f"/en/journey/gift/{gift.gift_code}/")
        self.assertNotContains(landing, "verified Crush.lu member")

    def test_approved_member_can_open_the_form(self):
        self._login(self._user("approved@example.com", approved=True))
        response = self.client.get(CREATE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only enter an address if they know")

    def test_sixth_post_in_a_day_is_rate_limited(self):
        self._login(self._user("approved@example.com", approved=True))
        # Invalid (no uploads/fields) POSTs still count toward the daily cap.
        for _ in range(5):
            self.assertNotEqual(self.client.post(CREATE_URL, {}).status_code, 429)
        self.assertEqual(self.client.post(CREATE_URL, {}).status_code, 429)


class GiftTrustLineTests(GiftAbuseTestBase):
    def test_landing_shows_first_name_verified_line_and_report(self):
        sender = self._user("alice@example.com", approved=True)
        gift = self._gift(sender)

        response = self.client.get(f"/en/journey/gift/{gift.gift_code}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Created by Alice")
        self.assertContains(response, "verified Crush.lu member")
        self.assertNotContains(response, "Lastname")
        self.assertContains(response, f"/en/journey/gift/{gift.gift_code}/report/")
        self.assertContains(response, "This isn't for me / Report")

    def test_unapproved_legacy_sender_is_not_called_verified(self):
        sender = self._user("legacy@example.com", approved=False)
        gift = self._gift(sender)

        response = self.client.get(f"/en/journey/gift/{gift.gift_code}/")
        self.assertContains(response, "Created by Legacy")
        self.assertNotContains(response, "verified Crush.lu member")

    def test_claim_page_shows_trust_line_and_report(self):
        sender = self._user("alice@example.com", approved=True)
        gift = self._gift(sender)
        self._login(self._user("bob@example.com"))

        response = self.client.get(f"/en/journey/gift/{gift.gift_code}/claim/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Created by Alice")
        self.assertContains(response, "verified Crush.lu member")
        self.assertContains(response, f"/en/journey/gift/{gift.gift_code}/report/")


class GiftReportTests(GiftAbuseTestBase):
    def test_report_expires_gift_and_notifies_staff(self):
        from crush_lu.models import JourneyGift, Notification

        sender = self._user("alice@example.com", approved=True)
        coach = self._user("coach@example.com", staff=True)
        gift = self._gift(sender)

        response = self.client.post(f"/en/journey/gift/{gift.gift_code}/report/")
        self.assertEqual(response.status_code, 302)

        gift.refresh_from_db()
        self.assertEqual(gift.status, JourneyGift.Status.EXPIRED)
        note = Notification.objects.get(notification_type="journey_gift_reported")
        self.assertEqual(note.user, coach)
        self.assertEqual(note.metadata["gift_id"], gift.pk)
        self.assertIn(f"journeygift/{gift.pk}/", note.link_url)
        self.assertFalse(Notification.objects.filter(user=sender).exists())

        # The link is dead afterwards.
        landing = self.client.get(f"/en/journey/gift/{gift.gift_code}/")
        self.assertTemplateUsed(landing, "crush_lu/journey/gift_expired.html")

    def test_htmx_report_answers_with_hx_redirect(self):
        gift = self._gift(self._user("alice@example.com", approved=True))
        response = self.client.post(
            f"/en/journey/gift/{gift.gift_code}/report/", HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("HX-Redirect", response.headers)

    def test_report_leaves_claimed_gift_alone(self):
        from crush_lu.models import JourneyGift, Notification

        self._user("coach@example.com", staff=True)
        gift = self._gift(
            self._user("alice@example.com", approved=True),
            status=JourneyGift.Status.CLAIMED,
        )
        response = self.client.post(
            f"/en/journey/gift/{gift.gift_code}/report/", follow=True
        )
        gift.refresh_from_db()
        self.assertEqual(gift.status, JourneyGift.Status.CLAIMED)
        self.assertFalse(Notification.objects.exists())
        texts = [str(m) for m in response.context["messages"]]
        self.assertIn("This gift is no longer active.", texts)
        self.assertNotIn("notified", " ".join(texts))

    def test_report_keeps_another_pending_gift_code(self):
        sender = self._user("alice@example.com", approved=True)
        reported, other = self._gift(sender), self._gift(sender)
        self.client.get(f"/en/journey/gift/{other.gift_code}/")
        self.client.post(f"/en/journey/gift/{reported.gift_code}/report/")
        self.assertEqual(self.client.session["pending_gift_code"], other.gift_code)

    def test_report_clears_its_own_pending_gift_code(self):
        gift = self._gift(self._user("alice@example.com", approved=True))
        self.client.get(f"/en/journey/gift/{gift.gift_code}/")
        self.client.post(f"/en/journey/gift/{gift.gift_code}/report/")
        self.assertNotIn("pending_gift_code", self.client.session)

    def test_report_requires_post(self):
        gift = self._gift(self._user("alice@example.com", approved=True))
        response = self.client.get(f"/en/journey/gift/{gift.gift_code}/report/")
        self.assertEqual(response.status_code, 405)

    def test_report_enforces_csrf(self):
        gift = self._gift(self._user("alice@example.com", approved=True))
        client = Client(HTTP_HOST="crush.lu", enforce_csrf_checks=True)
        response = client.post(f"/en/journey/gift/{gift.gift_code}/report/")
        self.assertEqual(response.status_code, 403)


class GiftReportClaimRaceTests(GiftAbuseTestBase):
    """A report landing between a claim's load and its save must win.

    SQLite ignores ``select_for_update``, so the race is simulated: the claim
    runs on an instance loaded before the row was expired, and the lock is
    asserted structurally.
    """

    def _stale_pending_gift_reported_meanwhile(self):
        from crush_lu.models import JourneyGift

        gift = self._gift(self._user("alice@example.com", approved=True))
        self.assertTrue(gift.is_claimable)  # the claim view's check passes
        response = self.client.post(f"/en/journey/gift/{gift.gift_code}/report/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            JourneyGift.objects.get(pk=gift.pk).status, JourneyGift.Status.EXPIRED
        )
        return gift

    def test_stale_claim_refuses_a_reported_gift(self):
        from crush_lu.models import JourneyConfiguration, JourneyGift

        gift = self._stale_pending_gift_reported_meanwhile()

        with self.assertRaises(ValueError):
            gift.claim(self._user("bob@example.com"))

        gift.refresh_from_db()
        self.assertEqual(gift.status, JourneyGift.Status.EXPIRED)
        self.assertIsNone(gift.claimed_by)
        self.assertIsNone(gift.journey)
        self.assertFalse(JourneyConfiguration.objects.exists())

    def test_claim_reads_status_under_a_row_lock(self):
        from unittest import mock

        from django.db.models import QuerySet

        from crush_lu.models import JourneyGift

        gift = self._stale_pending_gift_reported_meanwhile()
        original = QuerySet.select_for_update
        locked = []

        def spy(qs, *args, **kwargs):
            locked.append(qs.model)
            return original(qs, *args, **kwargs)

        with mock.patch.object(QuerySet, "select_for_update", spy):
            with self.assertRaises(ValueError):
                gift.claim(self._user("bob@example.com"))
        self.assertIn(JourneyGift, locked)
