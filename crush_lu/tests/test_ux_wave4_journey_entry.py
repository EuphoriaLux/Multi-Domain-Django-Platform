"""UX Wave 4 · WP2 "journey-entry" (findings 7-04, 7-05).

* 7-04: /journey/ and /advent/ without a journey render a plain-language page
  instead of bouncing to home under a jargon banner; a non-claimable gift
  renders its status page from the claim URL instead of double-redirecting.
* 7-05 / decision C: gift sender routes are staff/coach-only (404 otherwise);
  the recipient routes (landing, claim, report) stay public.

Paths are literal: ``reverse("crush_lu:...")`` builds ``/crush/...`` paths
that 404 under ``HTTP_HOST=crush.lu``.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    JourneyConfiguration,
    JourneyGift,
    SpecialUserExperience,
    UserDataConsent,
)

User = get_user_model()
HOST = "crush.lu"
NO_JOURNEY = "crush_lu/no_journey.html"


def _user(email, first="Lena", last="Schmit", **extra):
    user = User.objects.create_user(
        username=email,
        email=email,
        password="testpass123",
        first_name=first,
        last_name=last,
        **extra,
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


class NoJourneyPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.owner = _user("owner@example.com")
        self.namesake = _user("namesake@example.com")
        self.stranger = _user("stranger@example.com", first="Max", last="Muster")
        self.experience = SpecialUserExperience.objects.create(
            first_name="Lena",
            last_name="Schmit",
            linked_user=self.owner,
            is_active=True,
        )

    def _get(self, user, path):
        self.client.force_login(user)
        return self.client.get(path)

    def test_journey_urls_render_the_explainer_without_a_banner(self):
        for path in ("/en/journey/", "/en/journey/select/", "/en/journey/wonderland/"):
            response = self._get(self.stranger, path)
            if response.status_code == 302:  # /journey/ -> /journey/select/
                response = self.client.get(response["Location"])
            self.assertEqual(response.status_code, 200, path)
            self.assertTemplateUsed(response, NO_JOURNEY)
            self.assertContains(response, "You don't have a journey yet")
            self.assertContains(response, 'href="/en/dashboard/"')
            self.assertEqual(_messages(response), [], path)

    def test_advent_urls_render_the_explainer(self):
        for path in ("/en/advent/", "/en/advent/door/1/", "/en/advent/qr-scanner/"):
            response = self._get(self.stranger, path)
            self.assertEqual(response.status_code, 200, path)
            self.assertTemplateUsed(response, NO_JOURNEY)
            self.assertContains(response, "You don't have an Advent Calendar yet")
            self.assertEqual(_messages(response), [], path)

    def test_namesake_sees_exactly_what_a_stranger_sees(self):
        """Privacy (7-02): nothing tells a namesake a journey exists."""
        JourneyConfiguration.objects.create(
            special_experience=self.experience,
            journey_type="wonderland",
            journey_name="The Wonderland of You",
            is_active=True,
        )
        namesake = self._get(self.namesake, "/en/journey/select/")
        stranger = self._get(self.stranger, "/en/journey/select/")
        self.assertEqual(namesake.status_code, stranger.status_code)
        self.assertTemplateUsed(namesake, NO_JOURNEY)
        self.assertEqual(namesake.context["kind"], stranger.context["kind"])
        self.assertNotContains(namesake, "Wonderland of You")

    def test_owner_without_active_journey_gets_the_explainer(self):
        # No login-time session flag: special_welcome would bounce home.
        response = self._get(self.owner, "/en/journey/select/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, NO_JOURNEY)

    def test_flagged_session_still_goes_to_special_welcome(self):
        self.client.force_login(self.owner)
        session = self.client.session
        session["special_experience_active"] = True
        session.save()
        response = self.client.get("/en/journey/select/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/special-welcome/", response["Location"])

    def test_owner_without_advent_calendar_gets_the_explainer(self):
        response = self._get(self.owner, "/en/advent/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, NO_JOURNEY)
        self.assertEqual(response.context["kind"], "advent")

    def test_explainer_is_translated(self):
        for lang, heading in (
            ("de", "Du hast noch keine Reise"),
            ("fr", "Vous n'avez pas encore de parcours"),
        ):
            response = self._get(self.stranger, f"/{lang}/journey/select/")
            self.assertContains(response, heading, msg_prefix=lang)


class GiftClaimStatusPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.sender = _user("sender@example.com", first="Sam", is_staff=True)
        self.recipient = _user("recipient@example.com", first="Marie")
        self.client.force_login(self.recipient)

    def _gift(self, **kwargs):
        return JourneyGift.objects.create(
            sender=self.sender,
            recipient_name="Marie",
            date_first_met=date(2024, 2, 14),
            location_first_met="Luxembourg City",
            **kwargs,
        )

    def _claim(self, gift):
        return self.client.get(f"/en/journey/gift/{gift.gift_code}/claim/")

    def test_expired_gift_renders_expired_page(self):
        response = self._claim(self._gift(status=JourneyGift.Status.EXPIRED))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/journey/gift_expired.html")
        self.assertEqual(_messages(response), [])

    def test_past_expiry_date_renders_expired_page(self):
        gift = self._gift(expires_at=timezone.now() - timedelta(days=1))
        response = self._claim(gift)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/journey/gift_expired.html")

    def test_claimed_and_completed_gifts_render_claimed_page(self):
        for status in (JourneyGift.Status.CLAIMED, JourneyGift.Status.COMPLETED):
            response = self._claim(self._gift(status=status))
            self.assertEqual(response.status_code, 200, status)
            self.assertTemplateUsed(response, "crush_lu/journey/gift_claimed.html")
            self.assertEqual(_messages(response), [], status)

    def test_exhausted_claim_attempts_render_a_status_page(self):
        gift = self._gift(
            status=JourneyGift.Status.CLAIM_FAILED,
            claim_attempts=JourneyGift.MAX_CLAIM_ATTEMPTS,
        )
        response = self._claim(gift)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/journey/gift_expired.html")

    def test_own_claimed_gift_returns_to_wonderland(self):
        for status in (JourneyGift.Status.CLAIMED, JourneyGift.Status.COMPLETED):
            gift = self._gift(status=status, claimed_by=self.recipient)
            for path in (
                f"/en/journey/gift/{gift.gift_code}/claim/",
                f"/en/journey/gift/{gift.gift_code}/",
            ):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302, path)
                self.assertEqual(response["Location"], "/en/journey/wonderland/")

    def test_logged_in_landing_of_completed_gift_ends_on_a_status_page(self):
        gift = self._gift(status=JourneyGift.Status.COMPLETED)
        response = self.client.get(f"/en/journey/gift/{gift.gift_code}/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/journey/gift_claimed.html")


class GiftRoutesAreAdminOnlyTests(TestCase):
    """Decision C: journey gifts are an admin/coach tool."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)
        self.staff = _user("staff@example.com", first="Sam", is_staff=True)
        self.gift = JourneyGift.objects.create(
            sender=self.staff,
            recipient_name="Marie",
            date_first_met=date(2024, 2, 14),
            location_first_met="Luxembourg City",
        )
        self.sender_paths = (
            "/en/journey/gift/create/",
            f"/en/journey/gift/success/{self.gift.gift_code}/",
            "/en/journey/gifts/",
        )

    def _statuses(self, user):
        self.client.force_login(user)
        return [self.client.get(path).status_code for path in self.sender_paths]

    def test_members_get_404_on_sender_routes(self):
        member = _user("member@example.com")
        self.gift.sender = member  # even their own legacy gift's success page
        self.gift.save()
        self.assertEqual(self._statuses(member), [404, 404, 404])

    def test_member_post_is_404_before_rate_limit(self):
        # The gate sits above @ratelimit: no 429 that reveals the route.
        self.client.force_login(_user("member@example.com"))
        statuses = {
            self.client.post("/en/journey/gift/create/", {}).status_code
            for _ in range(7)
        }
        self.assertEqual(statuses, {404})

    def test_staff_can_use_sender_routes(self):
        self.assertEqual(self._statuses(self.staff), [200, 200, 200])

    def test_active_coach_can_use_sender_routes(self):
        coach_user = _user("coach@example.com", first="Cora")
        CrushCoach.objects.create(user=coach_user, is_active=True)
        self.gift.sender = coach_user
        self.gift.save()
        self.assertEqual(self._statuses(coach_user), [200, 200, 200])

    def test_inactive_coach_gets_404(self):
        coach_user = _user("old-coach@example.com", first="Olga")
        CrushCoach.objects.create(user=coach_user, is_active=False)
        self.assertEqual(self._statuses(coach_user), [404, 404, 404])

    def test_recipient_routes_stay_open_for_members(self):
        anonymous = Client(HTTP_HOST=HOST)
        landing = anonymous.get(f"/en/journey/gift/{self.gift.gift_code}/")
        self.assertEqual(landing.status_code, 200)
        self.assertTemplateUsed(landing, "crush_lu/journey/gift_landing.html")

        self.client.force_login(_user("member@example.com"))
        claim = self.client.get(f"/en/journey/gift/{self.gift.gift_code}/claim/")
        self.assertEqual(claim.status_code, 200)
        self.assertTemplateUsed(claim, "crush_lu/journey/gift_claim.html")

    def test_anonymous_sender_route_still_asks_to_log_in(self):
        response = Client(HTTP_HOST=HOST).get("/en/journey/gift/create/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
