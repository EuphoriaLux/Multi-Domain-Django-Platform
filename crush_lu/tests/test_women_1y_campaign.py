"""Women's 1-year campaign: recipient selection, send log, CTA routing.

Paths are literal and requests use ``HTTP_HOST="crush.lu"``: ``reverse()``
resolves against the default urlconf, not the one the host selects.
"""

from datetime import date, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.campaign_women_1y import (
    campaign_report,
    eligible_recipients,
    get_campaign,
)
from crush_lu.models import (
    CampaignRecipient,
    CrushProfile,
    EmailPreference,
    MeetupEvent,
    UserDataConsent,
)

User = get_user_model()
HOST = "crush.lu"
UTM = "utm_source=email&utm_medium=email&utm_campaign=women_1y&utm_content=cta"


def make_member(name, gender="F", status="incomplete", marketing=True, **profile):
    user = User.objects.create_user(
        username=f"{name}@example.com",
        email=f"{name}@example.com",
        password="testpass123",
        first_name=name.title(),
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 1, 1),
        gender=gender,
        verification_status=status,
        is_active=True,
        **profile,
    )
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.marketing_consent = marketing
    consent.crushlu_consent_given = True
    consent.save()
    EmailPreference.get_or_create_for_user(user)
    return user


class RecipientSelectionTests(TestCase):
    def setUp(self):
        cache.clear()

    def emails(self):
        return set(eligible_recipients().values_list("email", flat=True))

    def test_unverified_consenting_woman_is_eligible(self):
        make_member("incomplete", status="incomplete")
        make_member("pending", status="pending")
        self.assertEqual(
            self.emails(), {"incomplete@example.com", "pending@example.com"}
        )

    def test_only_women(self):
        for gender in ("M", "NB", "O", "P", ""):
            make_member(f"g{gender or 'blank'}", gender=gender)
        self.assertEqual(self.emails(), set())

    def test_verified_and_rejected_excluded(self):
        make_member("verified", status="verified")
        make_member("rejected", status="rejected")
        self.assertEqual(self.emails(), set())

    def test_requires_marketing_consent(self):
        make_member("noconsent", marketing=False)
        self.assertEqual(self.emails(), set())

    def test_settings_toggle_also_counts_as_consent(self):
        user = make_member("toggle", marketing=False)
        EmailPreference.objects.filter(user=user).update(email_marketing=True)
        self.assertEqual(self.emails(), {"toggle@example.com"})

    def test_unsubscribed_all_vetoes_consent(self):
        user = make_member("unsub")
        EmailPreference.objects.filter(user=user).update(
            unsubscribed_all=True, email_marketing=True
        )
        self.assertEqual(self.emails(), set())

    def test_inactive_banned_and_deactivated_excluded(self):
        gone = make_member("inactive")
        User.objects.filter(pk=gone.pk).update(is_active=False)
        banned = make_member("banned")
        UserDataConsent.objects.filter(user=banned).update(crushlu_banned=True)
        deactivated = make_member("deactivated")
        CrushProfile.objects.filter(user=deactivated).update(is_active=False)
        self.assertEqual(self.emails(), set())

    def test_already_sent_excluded(self):
        user = make_member("sent")
        make_member("fresh")
        campaign = get_campaign(create=True)
        CampaignRecipient.objects.create(
            campaign=campaign, channel="email", user=user, status="sent"
        )
        self.assertEqual(self.emails(), {"fresh@example.com"})
        self.assertIn(
            user.pk, eligible_recipients(include_sent=True).values_list("pk", flat=True)
        )


class MarketingUnsubscribeTests(TestCase):
    def test_marketing_unsubscribe_removes_member_from_audience(self):
        user = make_member("optout")
        self.assertEqual(eligible_recipients().count(), 1)
        token = EmailPreference.objects.get(user=user).unsubscribe_token
        response = Client(HTTP_HOST=HOST).post(
            f"/en/unsubscribe/{token}/", {"action": "unsubscribe_marketing"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(eligible_recipients().count(), 0)


class CdnUrlTests(TestCase):
    def test_email_poster_and_landing_video_use_cdn_when_configured(self):
        base = "https://cdn.crush.lu/crush-lu-media"
        with self.settings(CRUSH_MEDIA_BASE_URL=base):
            user = make_member("cdn")
            from crush_lu.campaign_women_1y import build_email

            _, _, html = build_email(user, get_campaign(create=True))
            self.assertIn(f"{base}/campaigns/women-1y/email-poster-560x996.png", html)
            landing = Client(HTTP_HOST=HOST).get("/en/women-1-year/").content.decode()
            self.assertIn(f"{base}/campaigns/women-1y/women-1y.mp4", landing)
            self.assertIn("<video", landing)


class CommandTests(TestCase):
    def setUp(self):
        cache.clear()
        make_member("a")
        make_member("b")
        make_member("c")
        mail.outbox.clear()

    def run_cmd(self, *args):
        out = StringIO()
        call_command("send_women_1y_campaign", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_default_is_dry_run_and_sends_nothing(self):
        output = self.run_cmd()
        self.assertIn("eligible recipients: 3", output)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(CampaignRecipient.objects.count(), 0)

    def test_send_respects_limit_and_never_sends_twice(self):
        self.run_cmd("--send", "--limit", "2", "--delay", "0", "--batch-pause", "0")
        self.assertEqual(len(mail.outbox), 2)
        self.run_cmd("--send", "--delay", "0", "--batch-pause", "0")
        self.assertEqual(len(mail.outbox), 3)
        self.run_cmd("--send", "--delay", "0", "--batch-pause", "0")
        self.assertEqual(len(mail.outbox), 3)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertEqual(len(set(recipients)), 3)
        self.assertEqual(CampaignRecipient.objects.filter(status="sent").count(), 3)

    @override_settings(CRUSH_MEDIA_BASE_URL=None)
    def test_sent_email_content(self):
        self.run_cmd("--send", "--limit", "1", "--delay", "0", "--batch-pause", "0")
        message = mail.outbox[0]
        self.assertEqual(message.subject, "We saved you a seat \U0001f495")
        html = message.alternatives[0][0]
        self.assertIn("unsubscribe/", html)
        self.assertNotIn("{{", html)
        self.assertNotIn("placeholder", html.lower())
        self.assertIn("https://crush.lu/c/", html)  # tracked CTA + poster
        self.assertIn("/static/campaigns/women-1y/email-poster-560x996", html)
        self.assertIn("Unsubscribe:", message.body)
        self.assertNotIn("<style", message.body)
        self.assertNotIn("<td", message.body)

    def test_unsubscribe_between_runs_is_respected(self):
        user = User.objects.get(email="a@example.com")
        EmailPreference.objects.filter(user=user).update(unsubscribed_all=True)
        self.run_cmd("--send", "--delay", "0", "--batch-pause", "0")
        self.assertEqual(
            sorted(m.to[0] for m in mail.outbox), ["b@example.com", "c@example.com"]
        )

    def test_test_to_sends_one_and_writes_no_log_row(self):
        self.run_cmd("--test-to", "qa@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["qa@example.com"])
        self.assertEqual(CampaignRecipient.objects.count(), 0)

    def test_send_cannot_combine_with_dry_run(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--send", "--dry-run")

    def test_report_counts_clicks_and_conversions(self):
        self.run_cmd("--send", "--delay", "0", "--batch-pause", "0")
        user = User.objects.get(email="a@example.com")
        CrushProfile.objects.filter(user=user).update(
            verification_status="verified", approved_at=timezone.now() + timedelta(1)
        )
        report = campaign_report(get_campaign())
        self.assertEqual(report["sent"], 3)
        self.assertEqual(report["verified_now"], 1)
        self.assertEqual(report["verified_after_send"], 1)
        self.assertIn("sent: 3", self.run_cmd("--report"))


class LandingRoutingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST=HOST)

    def go(self, query=UTM):
        return self.client.get(f"/en/women-1-year/go/?{query}")

    def test_unprefixed_email_url_redirects_keeping_query(self):
        response = self.client.get(f"/women-1-year/?{UTM}")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/en/women-1-year/?"))
        self.assertIn("utm_campaign=women_1y", response["Location"])

    def test_landing_renders_logged_out(self):
        response = self.client.get(f"/en/women-1-year/?{UTM}")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("We saved you a seat.", html)
        self.assertIn("No fakes. Ever.", html)
        self.assertIn("Get verified", html)
        self.assertIn("/en/women-1-year/go/?utm_source=email", html)
        self.assertIn("poster-frame", html)

    @override_settings(CRUSH_MEDIA_BASE_URL=None)
    def test_landing_degrades_to_poster_without_video_file(self):
        html = self.client.get("/en/women-1-year/").content.decode()
        self.assertNotIn("<video", html)

    def test_landing_shows_upcoming_events(self):
        MeetupEvent.objects.create(
            title="Anniversary Mixer",
            description="x",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=5),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=4),
            is_published=True,
        )
        html = self.client.get("/en/women-1-year/").content.decode()
        self.assertIn("Anniversary Mixer", html)

    def test_logged_out_cta_goes_to_signup_keeping_utm(self):
        response = self.go()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/signup/", response["Location"])
        self.assertIn("utm_campaign=women_1y", response["Location"])
        self.assertIn("next=", response["Location"])

    def test_unverified_member_goes_to_entry_events(self):
        user = make_member("unv", status="pending")
        self.client.force_login(user)
        response = self.go()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/events/", response["Location"])
        self.assertIn("entry=1", response["Location"])
        self.assertIn("utm_campaign=women_1y", response["Location"])

    def test_logged_in_without_profile_goes_to_onboarding(self):
        user = User.objects.create_user(
            username="np@example.com", email="np@example.com", password="x12345678"
        )
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
        self.client.force_login(user)
        self.assertIn("/onboarding/", self.go()["Location"])

    def test_verified_member_goes_to_crush_connect(self):
        user = make_member("ver", status="verified")
        self.client.force_login(user)
        response = self.go()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/crush-connect/", response["Location"])
        landing = self.client.get("/en/women-1-year/").content.decode()
        self.assertIn("Open Crush Connect", landing)
        self.assertNotIn("Next events", landing)
