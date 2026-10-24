"""Outreach segments of the Connect real-person check.

``connect_real_person_unchecked`` (and ``_active30``) let a superuser invite the
members no coach has met to a short call through Custom SMS, a newsletter or a
campaign. These tests pin who is in them, that the whole category is absent
while ``CONNECT_REAL_PERSON_CHECK_ENABLED`` is off (so a saved audience naming
a hidden key reaches nobody), and that each channel resolver still applies its
own consent rules on top.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import csv
import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.admin.custom_sms import find_segment, recipient_queryset
from crush_lu.admin.user_segments import get_segment_definitions
from crush_lu.models import (
    Campaign,
    ConnectRealPersonCheck,
    CrushProfile,
    CustomSmsBatch,
    EmailPreference,
    EventRegistration,
    MeetupEvent,
    Newsletter,
    UserDataConsent,
    UserReport,
)
from crush_lu.models.site_config import CrushSiteConfig
from crush_lu.newsletter_service import (
    _get_segment_users,
    get_newsletter_recipients,
    resolve_audience,
)
from crush_lu.real_person_check_rules import REQUIRED_CHECKLIST_KEYS
from crush_lu.services.campaigns import (
    CHANNEL_ADAPTERS,
    estimate_campaign,
    resolve_campaign_audience,
)
from crush_lu.services.real_person_check import claim_check, decide_check
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
)

User = get_user_model()

UNCHECKED = "connect_real_person_unchecked"
ACTIVE30 = "connect_real_person_unchecked_active30"
CATEGORY = "connect_trust"

FLAG_ON = {"CONNECT_REAL_PERSON_CHECK_ENABLED": True}
FLAG_OFF = {"CONNECT_REAL_PERSON_CHECK_ENABLED": False}
CRUSH_LU_URLS = {"ROOT_URLCONF": "azureproject.urls_crush"}
VOICE = {key: True for key in REQUIRED_CHECKLIST_KEYS["voice"]}

SEGMENTS_URL = "/crush-admin/user-segments/"
SMS_URL = "/crush-admin/custom-sms/"


def _segment(key):
    """One segment definition, without paying every segment's COUNT."""
    for category in get_segment_definitions(include_counts=False).values():
        for segment in category["segments"]:
            if segment["key"] == key:
                return segment
    raise AssertionError(f"segment {key!r} is not defined")


def _profile_pks(key):
    return set(_segment(key)["queryset"].values_list("pk", flat=True))


def _user_pks(key):
    return set(_get_segment_users(key).values_list("pk", flat=True))


class SegmentFixtures:
    """Five catalogue-shaped members, one per way of being in or out."""

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        CrushSiteConfig.get_config()
        self.coach = make_coach("seg_coach")

        # In both segments: LuxID only, logged in yesterday.
        self.active = make_member("seg_active")
        # In the first only: nobody has met them, but they logged in 40 days ago.
        self.dormant = make_member("seg_dormant", last_login_days_ago=40)
        # Out: a coach called them and the badge is valid.
        self.badged = make_member("seg_badged")
        self._grant_voice_badge(self.badged)
        # Out: a coach verified them in person at an event door.
        self.coach_seen = make_member("seg_seen")
        CrushProfile.objects.filter(user=self.coach_seen).update(
            verification_method="coach_event"
        )
        # Out: not in the Connect catalogue at all (never onboarded).
        self.outside = make_member("seg_outside", onboarded=False)

    def _grant_voice_badge(self, member):
        check = claim_check(self.coach, profile_of(member).pk)
        decide_check(
            self.coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True
        )

    def _profile_pk(self, user):
        return CrushProfile.objects.get(user=user).pk

    def _phone(self, user, number, *, verified=True):
        CrushProfile.objects.filter(user=user).update(
            phone_number=number, phone_verified=verified
        )

    def _opt_in(self, user, *, whatsapp=False):
        EmailPreference.objects.update_or_create(
            user=user,
            defaults={
                "email_marketing": True,
                "email_newsletter": True,
                "unsubscribed_all": False,
                "whatsapp_opt_in": whatsapp,
            },
        )

    def _superuser_client(self):
        admin = User.objects.create_superuser(
            "seg_root@example.com", "seg_root@example.com", "pw12345678"
        )
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(admin)
        return client


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


class SegmentVisibilityTests(SegmentFixtures, TestCase):
    @override_settings(**FLAG_OFF)
    def test_category_is_absent_while_the_flag_is_off(self):
        for with_counts in (False, True):
            with self.subTest(with_counts=with_counts):
                definitions = get_segment_definitions(include_counts=with_counts)
                self.assertNotIn(CATEGORY, definitions)
                keys = {
                    segment["key"]
                    for category in definitions.values()
                    for segment in category["segments"]
                }
                self.assertFalse({UNCHECKED, ACTIVE30} & keys)

    @override_settings(
        CONNECT_REAL_PERSON_CHECK_ENABLED=False,
        CONNECT_REAL_PERSON_BADGE_VISIBLE=True,
    )
    def test_the_badge_flag_alone_does_not_reveal_it(self):
        self.assertNotIn(CATEGORY, get_segment_definitions(include_counts=False))

    @override_settings(**FLAG_ON)
    def test_category_is_present_while_the_flag_is_on(self):
        definitions = get_segment_definitions(include_counts=False)
        category = definitions[CATEGORY]
        self.assertEqual(category["title"], "Crush Connect trust")
        self.assertEqual(category["group"], "operational")
        self.assertEqual(
            [segment["key"] for segment in category["segments"]], [UNCHECKED, ACTIVE30]
        )

    @override_settings(**FLAG_ON)
    def test_category_sits_inside_the_operational_run(self):
        """The dashboard regroups by ``group``: the run must stay contiguous."""
        groups = [
            category["group"]
            for category in get_segment_definitions(include_counts=False).values()
        ]
        position = list(get_segment_definitions(include_counts=False)).index(CATEGORY)
        self.assertEqual(groups[position], "operational")
        self.assertEqual(groups[position - 1], "operational")
        self.assertEqual(groups[position + 1], "behavioral")

    def test_a_saved_audience_naming_a_hidden_key_reaches_nobody(self):
        """Mirrors the retired ``pending_urgent`` keys: nobody, and no error."""
        with override_settings(**FLAG_ON):
            self.assertTrue(_user_pks(UNCHECKED))
        with override_settings(**FLAG_OFF):
            definitions = get_segment_definitions(include_counts=False)
            for key in (UNCHECKED, ACTIVE30):
                with self.subTest(key=key):
                    self.assertEqual(find_segment(key, definitions), (None, None))
                    self.assertFalse(_get_segment_users(key).exists())
                    self.assertFalse(resolve_audience("segment", key).exists())
                    campaign = Campaign(
                        audience="segment", segment_key=key, language="all"
                    )
                    self.assertFalse(resolve_campaign_audience(campaign).exists())


# ---------------------------------------------------------------------------
# Who is in them
# ---------------------------------------------------------------------------


@override_settings(**FLAG_ON)
class SegmentMembershipTests(SegmentFixtures, TestCase):
    def test_unchecked_is_the_cohort_without_a_valid_badge(self):
        self.assertEqual(
            _profile_pks(UNCHECKED),
            {self._profile_pk(self.active), self._profile_pk(self.dormant)},
        )

    def test_active30_adds_the_login_window(self):
        self.assertEqual(_profile_pks(ACTIVE30), {self._profile_pk(self.active)})

    def test_a_member_with_no_login_at_all_is_in_unchecked_only(self):
        never = make_member("seg_never", last_login_days_ago=None)
        self.assertIn(self._profile_pk(never), _profile_pks(UNCHECKED))
        self.assertNotIn(self._profile_pk(never), _profile_pks(ACTIVE30))

    def test_counts_on_the_cards(self):
        cards = {
            segment["key"]: segment["count"]
            for segment in get_segment_definitions()[CATEGORY]["segments"]
        }
        self.assertEqual(cards, {UNCHECKED: 2, ACTIVE30: 1})

    def test_a_badge_that_stopped_being_valid_puts_the_member_back(self):
        """Valid is the service's rule: ``approved_at`` must still match."""
        self.assertNotIn(self._profile_pk(self.badged), _profile_pks(UNCHECKED))

        CrushProfile.objects.filter(user=self.badged).update(
            approved_at=timezone.now() + timedelta(minutes=5)
        )

        self.assertIn(self._profile_pk(self.badged), _profile_pks(UNCHECKED))
        self.assertIn(self._profile_pk(self.badged), _profile_pks(ACTIVE30))

    def test_a_recent_decline_leaves_the_segment_until_the_cooldown_ends(self):
        """A campaign must not re-invite someone who just said no."""
        t0 = timezone.now()
        check = claim_check(self.coach, self._profile_pk(self.active), now=t0)
        decide_check(self.coach, check.pk, "declined", now=t0)

        self.assertNotIn(self._profile_pk(self.active), _profile_pks(UNCHECKED))
        self.assertNotIn(self._profile_pk(self.active), _profile_pks(ACTIVE30))

        ConnectRealPersonCheck.objects.filter(pk=check.pk).update(
            decided_at=t0 - timedelta(days=91)
        )
        self.assertIn(self._profile_pk(self.active), _profile_pks(UNCHECKED))

    def test_an_unresolved_fake_report_leaves_the_segment(self):
        check = claim_check(self.coach, self._profile_pk(self.active))
        decide_check(self.coach, check.pk, "suspected_fake", confirmed=True)

        self.assertNotIn(self._profile_pk(self.active), _profile_pks(UNCHECKED))

        UserReport.objects.filter(reported_user=self.active).update(status="dismissed")
        self.assertIn(self._profile_pk(self.active), _profile_pks(UNCHECKED))

    def test_a_self_scanned_attendance_alone_does_not_leave_the_segment(self):
        """Only coach-attested attendance counts, as for the cohort itself."""
        event = MeetupEvent.objects.create(
            title="Past mixer",
            description="x",
            event_type="mixer",
            date_time=timezone.now() - timedelta(days=10),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=30,
            registration_deadline=timezone.now() - timedelta(days=11),
            is_published=True,
        )
        EventRegistration.objects.create(
            user=self.active, event=event, status="attended"
        )

        self.assertIn(self._profile_pk(self.active), _profile_pks(UNCHECKED))


# ---------------------------------------------------------------------------
# Dashboard drill-down and export
# ---------------------------------------------------------------------------


@override_settings(**CRUSH_LU_URLS)
class SegmentDashboardTests(SegmentFixtures, TestCase):
    def test_dashboard_lists_the_category_only_while_the_flag_is_on(self):
        client = self._superuser_client()
        with override_settings(**FLAG_ON):
            response = client.get(SEGMENTS_URL)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Crush Connect trust")
            self.assertContains(response, f"{SEGMENTS_URL}{UNCHECKED}/")
        with override_settings(**FLAG_OFF):
            response = client.get(SEGMENTS_URL)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Crush Connect trust")
            self.assertNotContains(response, UNCHECKED)

    @override_settings(**FLAG_ON)
    def test_drill_down_and_csv_export(self):
        client = self._superuser_client()
        expected = {
            UNCHECKED: {self.active.email, self.dormant.email},
            ACTIVE30: {self.active.email},
        }
        for key, emails in expected.items():
            with self.subTest(key=key):
                response = client.get(f"{SEGMENTS_URL}{key}/")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["is_profile_segment"])
                self.assertEqual(
                    {item.user.email for item in response.context["users"]}, emails
                )

                export = client.get(f"{SEGMENTS_URL}{key}/?export=csv")
                self.assertEqual(export.status_code, 200)
                rows = list(csv.reader(io.StringIO(export.content.decode())))
                self.assertEqual({row[0] for row in rows[1:]}, emails)

    @override_settings(**FLAG_OFF)
    def test_a_hidden_segment_has_no_drill_down(self):
        response = self._superuser_client().get(f"{SEGMENTS_URL}{UNCHECKED}/")
        self.assertEqual(response.status_code, 302)


# ---------------------------------------------------------------------------
# Custom SMS
# ---------------------------------------------------------------------------


@override_settings(**CRUSH_LU_URLS)
class SegmentCustomSmsTests(SegmentFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self._phone(self.active, "+352691000001")
        self._phone(self.dormant, "+352691000002")
        # In the segment, but the SMS page's own rules keep them out.
        self.unverified_phone = make_member("seg_unverified")
        self._phone(self.unverified_phone, "+352691000003", verified=False)
        self.no_phone = make_member("seg_nophone")
        self.banned = make_member("seg_banned")
        self._phone(self.banned, "+352691000004")
        UserDataConsent.objects.filter(user=self.banned).update(crushlu_banned=True)
        # Phone on file, but outside the segment.
        self._phone(self.badged, "+352691000005")
        self._phone(self.coach_seen, "+352691000006")

    def _batch(self, key):
        return CustomSmsBatch.objects.create(
            audience_type=CustomSmsBatch.Audience.SEGMENT,
            segment_key=key,
            message_en="Hi {first_name}",
        )

    @override_settings(**FLAG_ON)
    def test_recipients_are_the_segment_members_the_sms_page_may_text(self):
        qs, warnings = recipient_queryset(self._batch(UNCHECKED))
        self.assertEqual(warnings, [])
        self.assertEqual(
            set(qs.values_list("user_id", flat=True)),
            {self.active.pk, self.dormant.pk},
        )

        qs, _warnings = recipient_queryset(self._batch(ACTIVE30))
        self.assertEqual(set(qs.values_list("user_id", flat=True)), {self.active.pk})

    @override_settings(**FLAG_ON)
    def test_unverified_phones_follow_the_batch_opt_in(self):
        batch = self._batch(UNCHECKED)
        batch.include_unverified_phones = True
        qs, _warnings = recipient_queryset(batch)
        self.assertEqual(
            set(qs.values_list("user_id", flat=True)),
            {self.active.pk, self.dormant.pk, self.unverified_phone.pk},
        )

    @override_settings(**FLAG_ON)
    def test_segment_options_offer_both_segments(self):
        response = self._superuser_client().get(f"{SMS_URL}segments/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{UNCHECKED}"')
        self.assertContains(response, f'value="{ACTIVE30}"')
        self.assertContains(response, "Crush Connect trust")

    @override_settings(**FLAG_OFF)
    def test_segment_options_hide_both_segments_while_the_flag_is_off(self):
        response = self._superuser_client().get(f"{SMS_URL}segments/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, UNCHECKED)
        self.assertNotContains(response, "Crush Connect trust")

    def _compose(self, client, key):
        return client.post(
            SMS_URL,
            {
                "audience_type": "segment",
                "segment_key": key,
                "message_en": "Hi {first_name}",
            },
        )

    @override_settings(**FLAG_ON)
    def test_compose_accepts_the_segment(self):
        self._compose(self._superuser_client(), UNCHECKED)
        batch = CustomSmsBatch.objects.get()
        self.assertEqual(batch.segment_key, UNCHECKED)

    @override_settings(**FLAG_OFF)
    def test_compose_refuses_the_segment_while_the_flag_is_off(self):
        response = self._compose(self._superuser_client(), UNCHECKED)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unknown user segment")
        self.assertFalse(CustomSmsBatch.objects.exists())

    def test_a_saved_batch_reaches_nobody_once_the_flag_goes_off(self):
        with override_settings(**FLAG_ON):
            batch = self._batch(UNCHECKED)
            self.assertTrue(recipient_queryset(batch)[0].exists())
        with override_settings(**FLAG_OFF):
            qs, warnings = recipient_queryset(batch)
            self.assertFalse(qs.exists())
            self.assertEqual(len(warnings), 1)
            self.assertIn(UNCHECKED, str(warnings[0]))


# ---------------------------------------------------------------------------
# Newsletter and campaign
# ---------------------------------------------------------------------------


@override_settings(**FLAG_ON)
class SegmentNewsletterAndCampaignTests(SegmentFixtures, TestCase):
    def test_segment_maps_onto_users(self):
        self.assertEqual(_user_pks(UNCHECKED), {self.active.pk, self.dormant.pk})
        self.assertEqual(_user_pks(ACTIVE30), {self.active.pk})

    def test_audience_drops_members_without_crushlu_consent(self):
        UserDataConsent.objects.filter(user=self.dormant).update(
            crushlu_consent_given=False
        )
        # The segment itself does not decide consent...
        self.assertIn(self.dormant.pk, _user_pks(UNCHECKED))
        # ...the shared audience resolver does.
        self.assertEqual(
            set(resolve_audience("segment", UNCHECKED).values_list("pk", flat=True)),
            {self.active.pk},
        )

    def test_newsletter_recipients_keep_the_email_opt_in(self):
        self._opt_in(self.active)
        newsletter = Newsletter(audience="segment", segment_key=UNCHECKED)
        self.assertEqual(
            set(get_newsletter_recipients(newsletter).values_list("pk", flat=True)),
            {self.active.pk},
        )

    def test_banned_member_is_not_a_campaign_recipient(self):
        UserDataConsent.objects.filter(user=self.active).update(crushlu_banned=True)
        campaign = Campaign(audience="segment", segment_key=UNCHECKED, language="all")
        self.assertEqual(
            set(resolve_campaign_audience(campaign).values_list("pk", flat=True)),
            {self.dormant.pk},
        )

    def test_campaign_estimate_counts_each_channel_by_its_own_consent(self):
        self._opt_in(self.active, whatsapp=True)
        self._phone(self.active, "+352691000001")
        # Opted in to email only; WhatsApp needs its own opt-in and a number.
        self._opt_in(self.dormant)
        self._phone(self.dormant, "+352691000002")

        estimate = estimate_campaign(
            "segment", UNCHECKED, "all", ["email", "whatsapp", "push"]
        )

        self.assertEqual(estimate["email"], 2)
        self.assertEqual(estimate["whatsapp"], 1)
        # No web push subscription exists, so push reaches nobody.
        self.assertEqual(estimate["push"], 0)
        self.assertEqual(estimate["reach"], 2)

        estimate = estimate_campaign("segment", ACTIVE30, "all", ["email", "whatsapp"])
        self.assertEqual((estimate["email"], estimate["whatsapp"]), (1, 1))

    def test_whatsapp_adapter_needs_the_opt_in(self):
        self._phone(self.active, "+352691000001")  # verified, but never opted in
        campaign = Campaign(audience="segment", segment_key=UNCHECKED, language="all")
        users = CHANNEL_ADAPTERS["whatsapp"].eligible_users(campaign)
        self.assertFalse(users.exists())


@override_settings(**CRUSH_LU_URLS)
class SegmentCampaignComposerTests(SegmentFixtures, TestCase):
    def _form(self, key):
        return {
            "name": "Real-person invite",
            "audience": "segment",
            "segment_key": key,
            "language": "all",
            "channels": ["email"],
            "email_subject": "A short call?",
            "email_body_html": "<p>Say hello to a coach</p>",
            "send_mode": "draft",
        }

    @override_settings(**FLAG_ON)
    def test_composer_offers_and_creates_the_segment(self):
        client = self._superuser_client()
        page = client.get(reverse("campaign_new"))
        self.assertContains(page, UNCHECKED)
        self.assertContains(page, ACTIVE30)

        client.post(reverse("campaign_create"), self._form(UNCHECKED))

        campaign = Campaign.objects.get(name="Real-person invite")
        self.assertEqual(campaign.segment_key, UNCHECKED)

    @override_settings(**FLAG_OFF)
    def test_composer_hides_and_rejects_the_segment_while_the_flag_is_off(self):
        client = self._superuser_client()
        page = client.get(reverse("campaign_new"))
        self.assertNotContains(page, UNCHECKED)

        response = client.post(reverse("campaign_create"), self._form(UNCHECKED))

        self.assertRedirects(response, reverse("campaign_new"))
        self.assertFalse(Campaign.objects.exists())
