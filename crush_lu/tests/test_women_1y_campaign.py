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
    finalize_status,
    get_campaign,
    get_newsletter,
)
from crush_lu.models import (
    CrushProfile,
    EmailPreference,
    Campaign,
    CampaignLink,
    MeetupEvent,
    NewsletterRecipient,
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
    consent.marketing_consent_date = timezone.now() if marketing else None
    EmailPreference.objects.filter(user=user).update(email_marketing=marketing)
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

    def test_signup_tick_alone_is_not_consent(self):
        # A tick with email_marketing off may be a stale, pre-toggle record.
        user = make_member("tickonly")
        EmailPreference.objects.filter(user=user).update(email_marketing=False)
        self.assertEqual(self.emails(), set())

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
        NewsletterRecipient.objects.create(
            newsletter=get_newsletter(campaign),
            user=user,
            email=user.email,
            status="sent",
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
        self.assertEqual(NewsletterRecipient.objects.count(), 0)

    def test_send_respects_limit_and_never_sends_twice(self):
        self.run_cmd("--send", "--limit", "2", "--delay", "0", "--batch-pause", "0")
        self.assertEqual(len(mail.outbox), 2)
        self.run_cmd("--send", "--delay", "0", "--batch-pause", "0")
        self.assertEqual(len(mail.outbox), 3)
        self.run_cmd("--send", "--delay", "0", "--batch-pause", "0")
        self.assertEqual(len(mail.outbox), 3)
        recipients = [m.to[0] for m in mail.outbox]
        self.assertEqual(len(set(recipients)), 3)
        self.assertEqual(NewsletterRecipient.objects.filter(status="sent").count(), 3)

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
        self.assertEqual(NewsletterRecipient.objects.count(), 0)

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

    def test_logged_out_cta_goes_to_login_keeping_utm(self):
        response = self.go()
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
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


class ReviewFollowUpTests(TestCase):
    """Behaviours added in response to PR review."""

    def setUp(self):
        cache.clear()

    def test_member_on_a_break_is_excluded(self):
        user = make_member("break")
        CrushProfile.objects.filter(user=user).update(on_break_at=timezone.now())
        self.assertEqual(eligible_recipients().count(), 0)

    def test_settings_toggle_off_removes_from_audience_without_touching_other_consent(
        self,
    ):
        user = make_member("toggleoff")
        client = Client(HTTP_HOST=HOST)
        client.force_login(user)
        response = client.post(
            "/api/email/preferences/",
            data='{"key": "email_marketing", "value": false}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(eligible_recipients().count(), 0)
        # Email preference controls must never rewrite the broader consent record.
        self.assertTrue(UserDataConsent.objects.get(user=user).marketing_consent)

    def test_incomplete_profile_cta_resumes_onboarding(self):
        user = make_member("inc", status="incomplete")
        client = Client(HTTP_HOST=HOST)
        client.force_login(user)
        response = client.get("/en/women-1-year/go/?" + UTM)
        self.assertIn("/onboarding/", response["Location"])
        self.assertIn("utm_campaign=women_1y", response["Location"])

    def test_event_with_closed_registration_is_not_listed(self):
        MeetupEvent.objects.create(
            title="Closed Soirée",
            description="x",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=5),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() - timedelta(hours=1),
            is_published=True,
        )
        html = Client(HTTP_HOST=HOST).get("/en/women-1-year/").content.decode()
        self.assertNotIn("Closed Soirée", html)

    def test_test_to_without_account_never_uses_a_members_unsubscribe_link(self):
        member = make_member("realmember")
        token = str(EmailPreference.objects.get(user=member).unsubscribe_token)
        mail.outbox.clear()
        call_command(
            "send_women_1y_campaign",
            "--test-to",
            "qa@example.com",
            stdout=StringIO(),
        )
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertNotIn(token, message.body)
        self.assertNotIn(token, message.alternatives[0][0])
        self.assertIn("section=account", message.body)

    def test_send_populates_dashboard_email_stats_and_status(self):
        make_member("s1")
        make_member("s2")
        call_command(
            "send_women_1y_campaign",
            "--send",
            "--delay",
            "0",
            "--batch-pause",
            "0",
            stdout=StringIO(),
        )
        campaign = get_campaign()
        self.assertEqual(campaign.status, "sent")
        self.assertEqual(campaign.stats["email"]["sent"], 2)

    def test_finalize_status_partial_and_failed(self):
        make_member("p1")
        make_member("p2")
        campaign = get_campaign(create=True)
        newsletter = get_newsletter(campaign)
        users = list(User.objects.order_by("pk"))
        NewsletterRecipient.objects.create(
            newsletter=newsletter, user=users[0], email="a", status="sent"
        )
        NewsletterRecipient.objects.create(
            newsletter=newsletter, user=users[1], email="b", status="failed"
        )
        self.assertEqual(finalize_status(campaign), "partial")
        NewsletterRecipient.objects.filter(status="sent").update(status="failed")
        self.assertEqual(finalize_status(campaign), "failed")


class ReviewRound2Tests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def send(self, *extra):
        out = StringIO()
        call_command(
            "send_women_1y_campaign",
            "--send",
            "--delay",
            "0",
            "--batch-pause",
            "0",
            *extra,
            stdout=out,
            stderr=out,
        )
        return out.getvalue()

    def test_email_uses_shared_brand_tokens(self):
        make_member("brand")
        self.send()
        html = mail.outbox[0].alternatives[0][0]
        from crush_lu.templatetags.crush_brand import BRAND

        self.assertIn(BRAND["pink"], html)
        template = open(
            "crush_lu/templates/crush_lu/emails/women_1y.html", encoding="utf-8"
        ).read()
        for hex_ in (BRAND["pink"], BRAND["pink_dark"], BRAND["purple_dark"]):
            self.assertNotIn(hex_, template)

    def test_test_send_links_directly_and_creates_no_tracking_rows(self):
        call_command(
            "send_women_1y_campaign",
            "--test-to",
            "qa@example.com",
            stdout=StringIO(),
        )
        self.assertEqual(CampaignLink.objects.count(), 0)
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("/c/", html)
        self.assertIn("utm_content=cta", html)

    def test_skipped_only_run_still_finalizes(self):
        make_member("skip")
        from unittest.mock import patch

        with patch(
            "crush_lu.management.commands.send_women_1y_campaign.send_women_1y_email",
            return_value=0,
        ):
            self.send()
        self.assertEqual(get_campaign().status, "failed")

    def test_cancelled_campaign_is_not_sent_or_overwritten(self):
        make_member("cx")
        Campaign.objects.create(
            slug="women_1y", name="x", channels=["email"], status="cancelled"
        )
        with self.assertRaises(CommandError):
            self.send()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(finalize_status(get_campaign()), "cancelled")

    def test_cancel_mid_run_stops_sending(self):
        make_member("m1")
        make_member("m2")
        from unittest.mock import patch

        real = __import__(
            "crush_lu.campaign_women_1y", fromlist=["send_women_1y_email"]
        ).send_women_1y_email

        def send_then_cancel(*args, **kwargs):
            result = real(*args, **kwargs)
            Campaign.objects.filter(slug="women_1y").update(status="cancelled")
            return result

        with patch(
            "crush_lu.management.commands.send_women_1y_campaign.send_women_1y_email",
            side_effect=send_then_cancel,
        ):
            self.send()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(get_campaign().status, "cancelled")

    def test_default_pacing_matches_newsletter_graph_limit(self):
        from crush_lu.management.commands.send_women_1y_campaign import Command
        from crush_lu.newsletter_service import BATCH_PAUSE_SECONDS, BATCH_SIZE

        parser = Command().create_parser("manage.py", "send_women_1y_campaign")
        defaults = parser.parse_args([])
        self.assertEqual(defaults.batch_size, BATCH_SIZE)
        self.assertEqual(defaults.batch_pause, BATCH_PAUSE_SECONDS)

    def test_private_events_do_not_crowd_out_public_ones(self):
        for i in range(10):
            MeetupEvent.objects.create(
                title=f"Private {i}",
                description="x",
                event_type="mixer",
                date_time=timezone.now() + timedelta(days=1, hours=i),
                location="L",
                address="a",
                max_participants=10,
                registration_deadline=timezone.now() + timedelta(hours=12),
                is_published=True,
                is_private_invitation=True,
            )
        MeetupEvent.objects.create(
            title="Public Later",
            description="x",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=9),
            location="L",
            address="a",
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=8),
            is_published=True,
        )
        html = Client(HTTP_HOST=HOST).get("/en/women-1-year/").content.decode()
        self.assertIn("Public Later", html)
        self.assertNotIn("Private 0", html)


class ReviewRound3Tests(TestCase):
    def setUp(self):
        cache.clear()

    def test_campaign_is_not_launchable_from_the_dashboard(self):
        self.assertNotEqual(get_campaign(create=True).status, "draft")

    def test_pending_claim_is_failure_in_final_status(self):
        make_member("done")
        stuck = make_member("stuck")
        campaign = get_campaign(create=True)
        newsletter = get_newsletter(campaign)
        done = User.objects.get(email="done@example.com")
        NewsletterRecipient.objects.create(
            newsletter=newsletter, user=done, email="d", status="sent"
        )
        NewsletterRecipient.objects.create(
            newsletter=newsletter, user=stuck, email="s", status="pending"
        )
        self.assertEqual(finalize_status(campaign), "partial")
        self.assertEqual(NewsletterRecipient.objects.get(user=stuck).status, "failed")

    def test_landing_has_no_nested_main_landmark(self):
        html = Client(HTTP_HOST=HOST).get("/en/women-1-year/").content.decode()
        self.assertEqual(html.count("<main"), 1)

    def test_consent_page_records_marketing_in_email_preference(self):
        user = User.objects.create_user(
            username="c@example.com", email="c@example.com", password="x12345678"
        )
        client = Client(HTTP_HOST=HOST)
        client.force_login(user)
        client.post(
            "/en/consent/confirm/",
            {"crushlu_consent": "on", "marketing_consent": "on"},
        )
        self.assertTrue(EmailPreference.objects.get(user=user).email_marketing)


class ReviewRound4Tests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def run_send(self, *extra):
        out = StringIO()
        call_command(
            "send_women_1y_campaign",
            "--send",
            "--delay",
            "0",
            *extra,
            stdout=out,
            stderr=out,
        )
        return out.getvalue()

    def test_failed_attempts_count_toward_the_batch_pause(self):
        for i in range(3):
            make_member(f"f{i}")
        from unittest.mock import patch

        with (
            patch(
                "crush_lu.management.commands.send_women_1y_campaign.send_women_1y_email",
                side_effect=RuntimeError("graph throttled"),
            ),
            patch(
                "crush_lu.management.commands.send_women_1y_campaign.time.sleep"
            ) as sleep,
        ):
            self.run_send("--batch-size", "2", "--batch-pause", "7")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [7])

    def test_campaign_is_cancellable_while_sending_and_idle_afterwards(self):
        make_member("c1")
        seen = {}

        from crush_lu.campaign_women_1y import send_women_1y_email as real

        def spy(*args, **kwargs):
            campaign = get_campaign()
            seen["status"] = campaign.status
            seen["can_cancel"] = campaign.can_cancel()
            return real(*args, **kwargs)

        from unittest.mock import patch

        with patch(
            "crush_lu.management.commands.send_women_1y_campaign.send_women_1y_email",
            side_effect=spy,
        ):
            self.run_send("--batch-pause", "0")
        self.assertEqual(seen, {"status": "sending", "can_cancel": True})
        self.assertNotEqual(get_campaign().status, "sending")

    def test_dispatcher_never_claims_the_campaign(self):
        from crush_lu.services.campaigns import dispatch_campaigns

        Campaign.objects.create(
            slug="women_1y", name="x", channels=["email"], status="sending"
        )
        summary = dispatch_campaigns()
        self.assertEqual(summary["campaigns"], [])

    def test_lock_is_released_only_by_its_owner(self):
        from crush_lu.management.commands import send_women_1y_campaign as mod

        make_member("l1")
        from unittest.mock import patch

        from crush_lu.campaign_women_1y import send_women_1y_email as real

        def steal_lock(*args, **kwargs):
            cache.set(mod.LOCK_KEY, "someone-else", 3600)
            return real(*args, **kwargs)

        with patch.object(mod, "send_women_1y_email", side_effect=steal_lock):
            self.run_send("--batch-pause", "0")
        self.assertEqual(cache.get(mod.LOCK_KEY), "someone-else")


class ReviewRound5Tests(TestCase):
    def setUp(self):
        cache.clear()

    def test_enabling_the_email_toggle_never_grants_publication_consent(self):
        user = make_member("noconsent", marketing=False)
        client = Client(HTTP_HOST=HOST)
        client.force_login(user)
        client.post(
            "/api/email/preferences/",
            data='{"key": "email_marketing", "value": true}',
            content_type="application/json",
        )
        self.assertTrue(EmailPreference.objects.get(user=user).email_marketing)
        self.assertFalse(UserDataConsent.objects.get(user=user).marketing_consent)

    def test_marketing_unsubscribe_leaves_the_broader_consent_alone(self):
        user = make_member("keepsocial")
        token = EmailPreference.objects.get(user=user).unsubscribe_token
        Client(HTTP_HOST=HOST).post(
            f"/en/unsubscribe/{token}/", {"action": "unsubscribe_marketing"}
        )
        self.assertTrue(UserDataConsent.objects.get(user=user).marketing_consent)
        self.assertEqual(eligible_recipients().count(), 0)

    def test_landing_offers_signup_as_secondary_link_when_logged_out(self):
        html = Client(HTTP_HOST=HOST).get("/en/women-1-year/").content.decode()
        self.assertIn("/signup/", html)

    def test_lock_loser_does_not_reset_campaign_status(self):
        from unittest.mock import patch

        from crush_lu.management.commands import send_women_1y_campaign as mod
        from crush_lu.campaign_women_1y import send_women_1y_email as real

        make_member("lk")
        make_member("lk2")

        def lose_lock(*args, **kwargs):
            cache.set(mod.LOCK_KEY, "newer-run", 3600)
            return real(*args, **kwargs)

        with patch.object(mod, "send_women_1y_email", side_effect=lose_lock):
            call_command(
                "send_women_1y_campaign",
                "--send",
                "--delay",
                "0",
                "--batch-pause",
                "0",
                stdout=StringIO(),
                stderr=StringIO(),
            )
        self.assertEqual(get_campaign().status, "sending")


class ReviewRound6Tests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()

    def run_send(self, *extra, **kw):
        out = StringIO()
        call_command(
            "send_women_1y_campaign",
            "--send",
            "--delay",
            "0",
            "--batch-pause",
            "0",
            *extra,
            stdout=out,
            stderr=out,
        )
        return out.getvalue()

    def test_counters_are_derived_from_receipts(self):
        from crush_lu.campaign_women_1y import sync_newsletter_counters

        a = make_member("a1")
        b = make_member("b1")
        newsletter = get_newsletter(get_campaign(create=True))
        NewsletterRecipient.objects.create(
            newsletter=newsletter, user=a, email="a", status="sent"
        )
        NewsletterRecipient.objects.create(
            newsletter=newsletter, user=b, email="b", status="pending"
        )
        # Stale, over-counted totals (as after a crash) are corrected.
        type(newsletter).objects.filter(pk=newsletter.pk).update(
            total_sent=2, total_failed=1
        )
        sync_newsletter_counters(newsletter)
        newsletter.refresh_from_db()
        self.assertEqual(
            (
                newsletter.total_recipients,
                newsletter.total_sent,
                newsletter.total_failed,
            ),
            (2, 1, 0),
        )

    def test_retry_refreshes_error_and_email_snapshot(self):
        user = make_member("retry")
        newsletter = get_newsletter(get_campaign(create=True))
        NewsletterRecipient.objects.create(
            newsletter=newsletter,
            user=user,
            email="old@example.com",
            status="failed",
            error_message="boom",
        )
        User.objects.filter(pk=user.pk).update(email="new@example.com")
        self.run_send("--retry-failed")
        row = NewsletterRecipient.objects.get(user=user)
        self.assertEqual(
            (row.status, row.error_message, row.email),
            ("sent", "", "new@example.com"),
        )
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])

    def test_capped_run_reports_the_persisted_status(self):
        make_member("cap1")
        make_member("cap2")
        output = self.run_send("--limit", "1")
        self.assertIn("campaign=partial", output)
        self.assertEqual(get_campaign().status, "partial")

    def test_negative_pacing_is_rejected_before_sending(self):
        make_member("neg")
        for flag in ("--delay", "--batch-pause"):
            with self.assertRaises(CommandError):
                call_command(
                    "send_women_1y_campaign",
                    "--send",
                    flag,
                    "-1",
                    stdout=StringIO(),
                )
        self.assertEqual(len(mail.outbox), 0)
