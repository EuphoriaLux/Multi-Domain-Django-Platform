"""Tests for the Crush Data MCP read-only analytics API.

Spec: ai-memory-hub/specs/2026-09-25-crush-data-mcp.md

Three things matter more than any single number:

* the API is dark (404) unless fully configured, and needs its own key;
* no response ever carries an identifier (SensitiveKeyTests);
* every SQL statement the service runs stays inside ``GRANTS``
  (SqlColumnAuditTests). SQLite has no grants, so this parser is the only
  thing standing between a stray ``.get()`` and a production permission error.

Run with: pytest crush_lu/tests/test_api_analytics.py -v
"""

import json
from collections import Counter
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from crush_lu.models import (
    CreditRedemption,
    CrushCoach,
    CrushConnectMembership,
    CrushCredit,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PaymentTransaction,
    PremiumMembership,
    ProfileSubmission,
    UserDataConsent,
    WeeklyMetricsSnapshot,
)
from crush_lu.services import analytics_readonly as analytics

User = get_user_model()

API_KEY = "test-analytics-key"
BASE = "/api/analytics/"
CONFIGURED = {
    "ROOT_URLCONF": "azureproject.urls_crush",
    "ANALYTICS_API_KEY": API_KEY,
    "ANALYTICS_PSEUDONYM_KEY": "test-pseudonym-key",
    # Tests run the queries on the single test DB; production pins "analytics".
    "ANALYTICS_DB_ALIAS": "default",
}
AUTH = {"HTTP_AUTHORIZATION": f"Bearer {API_KEY}"}
ALL_TOOLS = (
    "definitions",
    "kpi_weekly",
    "funnel",
    "events",
    "payments",
    "connect",
    "retention",
    "demographics",
    "members",
)
FORBIDDEN_KEYS = {
    "user_id",
    "user",
    "email",
    "username",
    "first_name",
    "last_name",
    "phone_number",
    "date_of_birth",
    "location",
    "bio",
    "raw_response",
    "sumup_checkout_id",
}


def _make_member(
    email, gender, dob, location="canton-luxembourg", status="verified", **user_kwargs
):
    user = User.objects.create_user(
        username=email,
        email=email,
        password="x",
        first_name="Secret",
        last_name="Name",
        **user_kwargs,
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=dob,
        gender=gender,
        location=location,
        verification_status=status,
        verification_method="luxid" if status == "verified" else "",
        phone_number=f"+352621{user.pk:06d}",
    )
    return user


def _event(title, days_ago, fee="15.00"):
    when = timezone.now() - timedelta(days=days_ago)
    return MeetupEvent.objects.create(
        title=title,
        description="event",
        event_type="speed_dating",
        date_time=when,
        registration_deadline=when - timedelta(days=1),
        location="Luxembourg",
        address="1 Test St",
        canton="Luxembourg",
        registration_fee=Decimal(fee),
        max_participants=20,
    )


class AnalyticsFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = _make_member("alice@crush.lu", "F", date(1995, 3, 1))
        cls.bob = _make_member(
            "bob@crush.lu", "M", date(1990, 6, 1), location="canton-esch"
        )
        cls.carol = _make_member(
            "carol@crush.lu", "F", date(1988, 1, 1), status="pending"
        )
        cls.dave = _make_member(
            "dave@crush.lu", "M", date(1999, 9, 9), status="incomplete"
        )
        cls.staff = _make_member("staff@crush.lu", "M", date(1985, 1, 1), is_staff=True)
        cls.qa = _make_member("qa@example.com", "F", date(1992, 1, 1))
        cls.banned = _make_member("eve@crush.lu", "F", date(1993, 1, 1))
        UserDataConsent.objects.filter(user=cls.banned).update(crushlu_banned=True)

        cls.earlier = _event("Earlier Night", days_ago=40)
        cls.night = _event("Speed Night", days_ago=10)
        EventRegistration.objects.create(
            event=cls.earlier,
            user=cls.alice,
            status="attended",
            checked_in_at=cls.earlier.date_time,
        )
        cls.alice_reg = EventRegistration.objects.create(
            event=cls.night,
            user=cls.alice,
            status="attended",
            checked_in_at=cls.night.date_time,
        )
        cls.bob_reg = EventRegistration.objects.create(
            event=cls.night, user=cls.bob, status="no_show"
        )
        EventRegistration.objects.create(
            event=cls.night, user=cls.carol, status="confirmed"
        )
        staff_reg = EventRegistration.objects.create(
            event=cls.night, user=cls.staff, status="attended"
        )
        EventRegistration.objects.create(
            event=cls.night, user=cls.qa, status="attended"
        )

        # Unrestricted events accept guests without a profile: not members.
        cls.guest = User.objects.create_user(
            username="guest@crush.lu", email="guest@crush.lu", password="x"
        )
        guest_reg = EventRegistration.objects.create(
            event=cls.night, user=cls.guest, status="attended"
        )

        for user, reg in (
            (cls.alice, cls.alice_reg),
            (cls.staff, staff_reg),
            (cls.guest, guest_reg),
        ):
            PaymentTransaction.objects.create(
                provider=PaymentTransaction.Provider.SUMUP,
                amount=Decimal("15.00"),
                currency="EUR",
                status=PaymentTransaction.Status.PAID,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                user=user,
                event=cls.night,
                event_registration=reg,
                paid_at=cls.night.date_time - timedelta(days=2),
            )
        credit = CrushCredit.objects.create(
            user=cls.bob,
            amount_cents=500,
            reason=CrushCredit.Reason.GOODWILL,
            expires_at=timezone.now() + timedelta(days=300),
        )
        CreditRedemption.objects.create(
            credit=credit, event_registration=cls.bob_reg, amount_cents=500
        )
        # An excluded account's redemption must not reach any credit total.
        staff_credit = CrushCredit.objects.create(
            user=cls.staff,
            amount_cents=700,
            reason=CrushCredit.Reason.GOODWILL,
            expires_at=timezone.now() + timedelta(days=300),
        )
        CreditRedemption.objects.create(
            credit=staff_credit, event_registration=staff_reg, amount_cents=700
        )
        EventRegistration.objects.filter(
            pk__in=[cls.alice_reg.pk, cls.bob_reg.pk]
        ).update(payment_confirmed=True)
        CrushConnectMembership.objects.create(
            user=cls.alice,
            onboarding_started_at=timezone.now(),
            onboarded_at=timezone.now(),
        )
        coach_user = User.objects.create_user(
            username="coach@crush.lu", email="coach@crush.lu", password="x"
        )
        cls.coach = coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        PremiumMembership.objects.create(user=cls.bob, coach=coach, status="active")
        for weeks_ago, signups in ((2, 3), (1, 5)):
            start = timezone.localdate() - timedelta(
                days=timezone.localdate().weekday() + 7 * weeks_ago
            )
            WeeklyMetricsSnapshot.objects.create(
                week_start=start,
                week_end=start + timedelta(days=6),
                metrics={"acquisition": {"signups": signups}},
            )

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def get(self, tool, **params):
        return self.client.get(f"{BASE}{tool}/", params, **AUTH)

    def data(self, tool, **params):
        response = self.get(tool, **params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]


@override_settings(**CONFIGURED)
class AccessTests(AnalyticsFixture):
    def test_non_ascii_bearer_is_unauthorized_not_a_crash(self):
        response = self.client.get(f"{BASE}events/", HTTP_AUTHORIZATION="Bearer é")
        self.assertEqual(response.status_code, 401)

    def test_out_of_range_dates_are_400_not_a_crash(self):
        for params in (
            {"to": "0001-01-01"},
            {"to": "9999-12-31"},
            {"from": "1999-12-31"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.get("events", **params).status_code, 400)
        self.assertEqual(self.get("members", signup_to="9999-12-31").status_code, 400)

    def test_missing_bearer_is_unauthorized(self):
        self.assertEqual(self.client.get(f"{BASE}events/").status_code, 401)

    def test_admin_api_key_is_not_accepted(self):
        with override_settings(ADMIN_API_KEY="admin-key"):
            response = self.client.get(
                f"{BASE}events/", HTTP_AUTHORIZATION="Bearer admin-key"
            )
        self.assertEqual(response.status_code, 401)

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(f"{BASE}events/", **AUTH).status_code, 405)

    def test_unknown_tool_lists_tools(self):
        response = self.get("raw_sql")
        self.assertEqual(response.status_code, 404)
        self.assertIn("members", response.json()["tools"])

    def test_parameter_errors_are_400(self):
        today = timezone.localdate()
        cases = [
            {"from": "2026-13-01"},
            {"from": today.isoformat(), "to": (today - timedelta(days=1)).isoformat()},
            {"from": (today - timedelta(days=500)).isoformat()},
            {"event_type": "rave"},
        ]
        for params in cases:
            with self.subTest(params=params):
                self.assertEqual(self.get("events", **params).status_code, 400)
        self.assertEqual(self.get("members", limit="5000").status_code, 400)
        self.assertEqual(self.get("demographics", group_by="email").status_code, 400)
        self.assertEqual(self.get("event_detail").status_code, 400)

    def test_range_limit_counts_both_endpoints(self):
        today = timezone.localdate()
        exact = (today - timedelta(days=analytics.MAX_RANGE_DAYS - 1)).isoformat()
        over = (today - timedelta(days=analytics.MAX_RANGE_DAYS)).isoformat()
        self.assertEqual(self.get("events", **{"from": exact}).status_code, 200)
        self.assertEqual(self.get("events", **{"from": over}).status_code, 400)
        default_from = self.get("events").json()["params"]["start"]
        self.assertEqual(
            default_from,
            (today - timedelta(days=analytics.DEFAULT_RANGE_DAYS - 1)).isoformat(),
        )

    def test_missing_event_is_404(self):
        self.assertEqual(self.get("event_detail", event_id="999999").status_code, 404)

    def test_responses_are_not_cacheable_by_intermediaries(self):
        self.assertEqual(self.get("definitions")["Cache-Control"], "no-store")

    def test_language_is_pinned_so_cached_titles_never_mix(self):
        MeetupEvent.objects.filter(pk=self.night.pk).update(title_fr="Soirée rapide")
        response = self.client.get(f"{BASE}events/", HTTP_ACCEPT_LANGUAGE="fr", **AUTH)
        self.assertEqual(response.json()["language"], "en")
        titles = {e["title"] for e in response.json()["data"]["events"]}
        self.assertIn("Speed Night", titles)
        self.assertNotIn("Soirée rapide", titles)


class DarkUnlessConfiguredTests(AnalyticsFixture):
    def test_without_keys_the_endpoint_does_not_exist(self):
        with override_settings(
            ROOT_URLCONF="azureproject.urls_crush", ANALYTICS_API_KEY=""
        ):
            self.assertEqual(self.get("events").status_code, 404)

    def test_without_the_analytics_alias_the_endpoint_does_not_exist(self):
        # Production defines DATABASES["analytics"] only when its credentials exist.
        with override_settings(**{**CONFIGURED, "ANALYTICS_DB_ALIAS": "analytics"}):
            self.assertEqual(self.get("events").status_code, 404)

    def test_dark_route_is_404_for_any_method_and_rate(self):
        # The dark gate runs before the method check and the rate limiter, so
        # nothing distinguishes the route from one that does not exist.
        with override_settings(
            ROOT_URLCONF="azureproject.urls_crush", ANALYTICS_API_KEY=""
        ):
            self.assertEqual(self.client.post(f"{BASE}events/").status_code, 404)
            statuses = {
                self.client.get(f"{BASE}events/").status_code for _ in range(65)
            }
        self.assertEqual(statuses, {404})


@override_settings(**CONFIGURED)
class ToolTests(AnalyticsFixture):
    """Computations, with the suppression floor at 1 so raw values show.

    The fixture's four members would otherwise all generalize to
    "suppressed"; DisclosureControlTests covers the real floor.
    """

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(analytics, "SUPPRESSION_FLOOR", 1)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_events_count_real_members_and_report_excluded_seats(self):
        events = {e["title"]: e for e in self.data("events")["events"]}
        night = events["Speed Night"]
        self.assertEqual(
            night["registrations_by_status"],
            {"attended": 1, "no_show": 1, "confirmed": 1},
        )
        self.assertEqual(
            night["seat_holders"], 2
        )  # attended + confirmed; no_show holds no seat
        self.assertEqual(night["seat_holders_by_gender"], {"F": 2})
        self.assertEqual(night["checked_in"], 1)
        self.assertEqual(night["no_show"], 1)
        self.assertEqual(night["fill_pct"], 10.0)
        self.assertEqual(night["paid_registrations"], 1)
        self.assertEqual(night["paid_revenue_eur"], 15.0)  # staff's payment excluded
        self.assertEqual(night["credit_redeemed_eur"], 5.0)
        self.assertEqual(
            night["excluded_account_registrations"], 3
        )  # staff + test account + profile-less guest

    def test_event_detail_rows_are_pseudonymized(self):
        rows = self.data("event_detail", event_id=str(self.night.id))["registrations"]
        self.assertEqual(len(rows), 3)
        by_member = {r["member"]: r for r in rows}
        alice = by_member[analytics.pseudonym(self.alice.id)]
        self.assertEqual(alice["prior_events_attended"], 1)
        self.assertFalse(alice["first_event"])
        self.assertTrue(alice["paid"])
        self.assertEqual(alice["age_band"], "30-34")
        bob = by_member[analytics.pseudonym(self.bob.id)]
        self.assertTrue(bob["paid_with_credit"])
        self.assertTrue(bob["first_event"])

    def test_payment_flags_follow_the_current_registration_cycle(self):
        # carol paid with credit once, cancelled (payment_confirmed cleared) and
        # re-registered on the same row (registered_at reset): the historical
        # ledger must not make the new pending cycle look paid.
        carol_reg = EventRegistration.objects.get(event=self.night, user=self.carol)
        old_credit = CrushCredit.objects.create(
            user=self.carol,
            amount_cents=1500,
            reason=CrushCredit.Reason.GOODWILL,
            expires_at=timezone.now() + timedelta(days=300),
        )
        redemption = CreditRedemption.objects.create(
            credit=old_credit, event_registration=carol_reg, amount_cents=1500
        )
        CreditRedemption.objects.filter(pk=redemption.pk).update(
            redeemed_at=timezone.now() - timedelta(days=5)
        )
        EventRegistration.objects.filter(pk=carol_reg.pk).update(
            status="pending", payment_confirmed=False, registered_at=timezone.now()
        )
        rows = self.data("event_detail", event_id=str(self.night.id))["registrations"]
        carol = next(
            r for r in rows if r["member"] == analytics.pseudonym(self.carol.id)
        )
        self.assertFalse(carol["paid"])
        self.assertFalse(carol["paid_with_credit"])

    def test_event_detail_summary_ignores_the_event_list_cap(self):
        with mock.patch.object(analytics, "MAX_EVENTS", 0):
            detail = self.data("event_detail", event_id=str(self.night.id))
        self.assertEqual(detail["event"]["event_id"], self.night.id)
        self.assertEqual(detail["event"]["seat_holders"], 2)

    def test_definitions_publish_separate_canton_enums(self):
        enums = self.data("definitions")["enums"]
        self.assertIn("Luxembourg", enums["event_canton"])
        self.assertIn("canton-luxembourg", enums["member_canton"])
        self.assertIn("other", enums["member_canton"])
        self.assertNotIn("canton", enums)
        for value in ("canton-luxembourg", "other"):
            self.assertEqual(self.get("members", canton=value).status_code, 200)

    def test_panel_verification_audit_rows_are_not_submissions(self):
        # A coach verified dave from the panel without a submission: the
        # audit-trail row (approved, coach set, reviewed on creation) must not
        # count. carol's real submission (pending, no reviewer yet) does.
        # The panel captures `now` before its checks, so even a slow path
        # leaves reviewed_at BEFORE the row's own submitted_at.
        ProfileSubmission.objects.create(
            profile=self.dave.crushprofile,
            coach=self.coach,
            status="approved",
            reviewed_at=timezone.now() - timedelta(seconds=10),
        )
        ProfileSubmission.objects.create(
            profile=self.carol.crushprofile, status="pending"
        )
        # A real submission approved very quickly still counts.
        quick = ProfileSubmission.objects.create(
            profile=self.bob.crushprofile, coach=self.coach, status="approved"
        )
        ProfileSubmission.objects.filter(pk=quick.pk).update(
            reviewed_at=quick.submitted_at + timedelta(seconds=1)
        )
        self.assertEqual(self.data("funnel")["totals"]["submitted"], 2)

    def test_self_serve_submission_counts_without_a_row(self):
        # The self-serve path marks the profile and writes no ProfileSubmission.
        CrushProfile.objects.filter(user=self.alice).update(
            completion_status="submitted"
        )
        # alice (completion_status) + carol (verification pending); bob and
        # dave never submitted.
        self.assertEqual(self.data("funnel")["totals"]["submitted"], 2)

    def test_funnel_counts_only_real_members(self):
        totals = self.data("funnel")["totals"]
        self.assertEqual(totals["signups"], 4)
        self.assertEqual(totals["verified"], 2)
        self.assertEqual(totals["attended_event"], 1)
        self.assertEqual(totals["paid_event"], 1)
        self.assertEqual(totals["connect_onboarded"], 1)
        self.assertEqual(totals["premium_active"], 1)

    def test_payments_split_real_and_excluded_money(self):
        data = self.data("payments")
        self.assertEqual(
            sum(r["amount_eur"] for r in data["paid_event_revenue_by_paid_at"]), 15.0
        )
        self.assertEqual(
            data["excluded_accounts"], {"transactions": 2, "amount_eur": 30.0}
        )
        self.assertEqual(data["credits_redeemed"], {"count": 1, "amount_eur": 5.0})
        self.assertEqual(data["premium"]["active_now"], 1)

    def test_connect_and_retention(self):
        self.assertEqual(self.data("connect")["memberships"]["onboarded_total"], 1)
        retention = self.data("retention")
        self.assertEqual(retention["attendees"], 1)
        self.assertEqual(retention["events_attended_in_window"], {"2": 1})
        self.assertEqual(retention["median_days_between_events"], 30)

    def test_return_within_90_days_compares_the_full_interval(self):
        frank = _make_member("frank@crush.lu", "M", date(1991, 2, 2))
        first = _event("Old Night", days_ago=200)
        second = MeetupEvent.objects.create(
            title="Late Return",
            description="event",
            event_type="speed_dating",
            date_time=first.date_time + timedelta(days=90, hours=12),
            registration_deadline=first.date_time,
            location="Luxembourg",
            address="1 Test St",
            registration_fee=Decimal("15.00"),
            max_participants=20,
        )
        for event in (first, second):
            EventRegistration.objects.create(event=event, user=frank, status="attended")
        start = (timezone.localdate() - timedelta(days=250)).isoformat()
        returned = self.data("retention", **{"from": start})[
            "first_timers_returned_within_90d"
        ]
        self.assertEqual((returned["eligible"], returned["returned"]), (1, 0))

    def test_retention_gaps_are_local_calendar_days_across_dst(self):
        import zoneinfo

        lux = zoneinfo.ZoneInfo("Europe/Luxembourg")
        gina = _make_member("gina@crush.lu", "F", date(1990, 4, 4))
        for day in (25, 32):  # 2026-03-25 and 2026-04-01, across 2026-03-29
            when = datetime(2026, 3, 1, 20, 0, tzinfo=lux) + timedelta(days=day - 1)
            event = MeetupEvent.objects.create(
                title=f"DST {day}",
                description="event",
                event_type="speed_dating",
                date_time=when,
                registration_deadline=when - timedelta(days=1),
                location="Luxembourg",
                address="1 Test St",
                registration_fee=Decimal("15.00"),
                max_participants=20,
            )
            EventRegistration.objects.create(event=event, user=gina, status="attended")
        retention = self.data("retention", **{"from": "2026-03-01", "to": "2026-04-30"})
        self.assertEqual(retention["median_days_between_events"], 7)

    def test_members_page_is_limited_but_counts_all_matches(self):
        data = self.data("members", limit="1")
        self.assertEqual((data["total_matching"], data["returned"]), (4, 1))
        self.assertTrue(data["truncated"])
        attended_only = self.data("members", attended="true", limit="1")
        self.assertEqual(attended_only["total_matching"], 1)
        self.assertEqual(attended_only["members"][0]["events_attended"], 2)

    def test_members_rows_have_a_fixed_shape(self):
        data = self.data("members")
        self.assertEqual(data["total_matching"], 4)
        expected = {
            "member", "signup_week", "gender", "age_band", "canton", "verification_status",
            "verification_method", "phone_verified", "luxid_linked", "events_attended",
            "first_event_day", "last_event_day", "paid_event_registrations",
            "premium_active", "connect_onboarded",
        }  # fmt: skip
        for row in data["members"]:
            self.assertEqual(set(row), expected)
        filtered = self.data("members", gender="M", attended="false")
        self.assertEqual(filtered["total_matching"], 2)

    def test_kpi_weekly_returns_deltas(self):
        weeks = self.data("kpi_weekly", weeks="1")["weeks"]
        self.assertEqual(len(weeks), 1)
        self.assertEqual(weeks[0]["deltas"]["acquisition"]["signups"], 2)

    def test_pseudonyms_are_stable_and_keyed(self):
        first = analytics.pseudonym(self.alice.id)
        self.assertEqual(first, analytics.pseudonym(self.alice.id))
        self.assertRegex(first, r"^[0-9a-f]{16}$")
        with override_settings(ANALYTICS_PSEUDONYM_KEY="another-key"):
            self.assertNotEqual(first, analytics.pseudonym(self.alice.id))


@override_settings(**CONFIGURED)
class DisclosureControlTests(AnalyticsFixture):
    """The real floor (5). Four real members make every small cell visible."""

    def test_demographics_withholds_a_population_below_the_floor(self):
        # Four real members are below the floor of 5: no total, no cells.
        whole = self.data("demographics", group_by="gender")
        self.assertTrue(whole["suppressed"])
        self.assertIsNone(whole["total_members"])
        self.assertEqual(whole["cells"], [])

    def test_demographics_drops_small_cross_tab_cells_with_their_labels(self):
        with mock.patch.object(analytics, "SUPPRESSION_FLOOR", 3):
            crossed = self.data("demographics", group_by="gender,age_band")
            self.assertEqual(crossed["total_members"], 4)
            self.assertEqual(crossed["cells"], [])  # every cell has 1 member
            self.assertEqual(crossed["suppressed_cells"], 4)
            single = self.data("demographics", group_by="gender")
            self.assertEqual(
                {c["gender"]: c["members"] for c in single["cells"]}, {"F": 2, "M": 2}
            )
            # One pending member: the filtered population is withheld whole.
            pending = self.data(
                "demographics",
                group_by="gender,age_band",
                verification_status="pending",
            )
            self.assertTrue(pending["suppressed"])
            self.assertIsNone(pending["total_members"])
            self.assertEqual(pending["cells"], [])

    def test_member_rows_cannot_rebuild_a_suppressed_cell(self):
        # Four real members are below the floor of 5: no count, no rows.
        whole = self.data("members")
        self.assertTrue(whole["suppressed"])
        self.assertEqual(whole["members"], [])
        with mock.patch.object(analytics, "SUPPRESSION_FLOOR", 3):
            cache.clear()
            rows = self.data("members")["members"]
            self.assertEqual(len(rows), 4)
            for row in rows:  # no gender reaches 3, so nothing is retained
                self.assertEqual(
                    (row["gender"], row["age_band"], row["canton"]),
                    ("suppressed", "suppressed", "suppressed"),
                )
            # Filters match the generalized values, so they cannot count the cell.
            cache.clear()
            self.assertEqual(self.data("members", gender="F")["total_matching"], 0)
        detail = self.data("event_detail", event_id=str(self.night.id))
        for row in detail["registrations"]:
            self.assertEqual(
                (row["gender"], row["age_band"], row["canton"]),
                ("suppressed", "suppressed", "suppressed"),
            )

    def test_unknown_locations_are_reported_as_other(self):
        self.assertEqual(analytics.canton_code("canton-esch"), "canton-esch")
        self.assertEqual(analytics.canton_code("canton-bereldange"), "other")
        self.assertEqual(analytics.canton_code("Rue de la Gare 12"), "other")
        self.assertIsNone(analytics.canton_code(""))

    def test_floor_applies_to_the_final_filtered_population(self):
        with mock.patch.object(analytics, "SUPPRESSION_FLOOR", 2):
            # gender F is retained (2 members), but only carol is pending.
            data = self.data("members", gender="F", verification_status="pending")
        self.assertTrue(data["suppressed"])
        self.assertIsNone(data["total_matching"])
        self.assertEqual(data["members"], [])

    def test_profile_less_guests_are_never_member_rows(self):
        with mock.patch.object(analytics, "SUPPRESSION_FLOOR", 1):
            detail = self.data("event_detail", event_id=str(self.night.id))
        members = {r["member"] for r in detail["registrations"]}
        self.assertNotIn(analytics.pseudonym(self.guest.id), members)
        self.assertEqual(len(members), 3)

    def test_suppressed_groups_can_be_filtered(self):
        with mock.patch.object(analytics, "SUPPRESSION_FLOOR", 3):
            data = self.data("members", gender="suppressed")
        self.assertEqual(data["total_matching"], 4)
        enums = self.data("definitions")["enums"]
        for key in ("member_gender", "member_age_band", "member_canton"):
            self.assertIn("suppressed", enums[key])
            self.assertIn("unknown", enums[key])
        for param in ("gender", "age_band", "canton"):
            self.assertEqual(self.get("members", **{param: "unknown"}).status_code, 200)

    def test_generalization_drops_canton_then_age_before_gender(self):
        with mock.patch.object(analytics, "SUPPRESSION_FLOOR", 2):
            rows = self.data("members")["members"]
            self.assertEqual({r["gender"] for r in rows}, {"F", "M"})
            self.assertEqual({r["age_band"] for r in rows}, {"suppressed"})
            self.assertEqual({r["canton"] for r in rows}, {"suppressed"})
            cache.clear()
            self.assertEqual(self.data("members", gender="F")["total_matching"], 2)
            self.assertEqual(
                self.data("members", age_band="30-34")["total_matching"], 0
            )


@override_settings(**CONFIGURED)
class HardeningTests(AnalyticsFixture):
    def test_failed_privilege_audit_is_a_503_not_data(self):
        with mock.patch.object(
            analytics,
            "_assert_least_privilege",
            side_effect=analytics.NotConfigured("exceeds"),
        ):
            response = self.get("events")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("data", response.json())

    def test_rotating_the_pseudonym_key_never_serves_cached_old_pseudonyms(self):
        with mock.patch.object(analytics, "SUPPRESSION_FLOOR", 1):
            before = self.data("event_detail", event_id=str(self.night.id))
            with override_settings(ANALYTICS_PSEUDONYM_KEY="rotated-key"):
                after = self.data("event_detail", event_id=str(self.night.id))
        old = {r["member"] for r in before["registrations"]}
        new = {r["member"] for r in after["registrations"]}
        self.assertTrue(old)
        self.assertFalse(old & new)

    def test_privilege_audit_runs_before_a_cached_payload_is_served(self):
        self.assertEqual(self.get("events").status_code, 200)  # now cached
        with mock.patch.object(
            analytics,
            "_assert_least_privilege",
            side_effect=analytics.NotConfigured("exceeds"),
        ):
            self.assertEqual(self.get("events").status_code, 503)

    def test_database_failure_during_the_audit_is_a_json_503(self):
        from django.db import OperationalError

        with mock.patch.object(
            analytics,
            "_assert_least_privilege",
            side_effect=OperationalError("too many connections for role"),
        ):
            response = self.get("events")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "database unavailable")

    def test_rate_limit_key_ignores_the_azure_client_port(self):
        from crush_lu.api_analytics import _client_ip_key
        from django.test import RequestFactory

        factory = RequestFactory()
        keys = {
            _client_ip_key(factory.get("/", HTTP_X_FORWARDED_FOR=f"203.0.113.7:{port}"))
            for port in (50123, 50124)
        }
        self.assertEqual(keys, {"203.0.113.7"})

    def test_reversed_signup_window_is_400(self):
        response = self.get("members", signup_from="2026-09-01", signup_to="2026-08-01")
        self.assertEqual(response.status_code, 400)


class GeneralizationTests(TestCase):
    """_generalized_quasi_identifiers: every output tuple covers >= floor members."""

    @staticmethod
    def _profile(uid, gender, age, location):
        today = timezone.localdate()
        return {
            "user_id": uid,
            "gender": gender,
            "date_of_birth": date(today.year - age, 1, 1),
            "location": location,
        }

    def _assert_k_anonymous(self, profiles, floor):
        with mock.patch.object(analytics, "SUPPRESSION_FLOOR", floor):
            out = analytics._generalized_quasi_identifiers(profiles)
        self.assertEqual(set(out), {p["user_id"] for p in profiles})
        classes = Counter(tuple(v.values()) for v in out.values())
        if len(profiles) >= floor:
            self.assertTrue(all(n >= floor for n in classes.values()), classes)
        return out

    def test_a_small_sibling_cell_is_pooled_with_a_retained_one(self):
        # Five F/25-29/Luxembourg and one F/25-29/Vianden: the Vianden member
        # must not become the only F/25-29/suppressed row.
        profiles = [self._profile(i, "F", 27, "canton-luxembourg") for i in range(5)]
        profiles.append(self._profile(99, "F", 27, "canton-vianden"))
        out = self._assert_k_anonymous(profiles, 5)
        self.assertEqual({v["canton"] for v in out.values()}, {"suppressed"})
        self.assertEqual({v["age_band"] for v in out.values()}, {"25-29"})

    def test_retained_cells_stay_detailed_when_the_bucket_is_big_enough(self):
        profiles = [self._profile(i, "M", 33, "canton-esch") for i in range(5)]
        profiles += [
            self._profile(10 + i, "M", 33, f"canton-{c}")
            for i, c in enumerate(["vianden", "wiltz", "remich", "mersch", "redange"])
        ]
        out = self._assert_k_anonymous(profiles, 5)
        self.assertEqual(out[0]["canton"], "canton-esch")
        self.assertEqual(out[10]["canton"], "suppressed")

    def test_missing_values_are_the_filterable_unknown_token(self):
        profiles = [
            {"user_id": i, "gender": "", "date_of_birth": None, "location": ""}
            for i in range(5)
        ]
        out = self._assert_k_anonymous(profiles, 5)
        self.assertEqual(
            {tuple(v.values()) for v in out.values()},
            {("unknown", "unknown", "unknown")},
        )

    def test_invariant_holds_on_varied_populations(self):
        import random

        cantons = sorted(analytics.LOCATION_CODES) + ["", "somewhere"]
        for seed in range(25):
            rng = random.Random(seed)
            profiles = [
                self._profile(
                    i,
                    rng.choice(["M", "F", "NB", None]),
                    rng.randint(18, 70),
                    rng.choice(cantons),
                )
                for i in range(rng.randint(5, 400))
            ]
            with self.subTest(seed=seed):
                self._assert_k_anonymous(profiles, 5)


class PrivilegeAuditTests(TestCase):
    """The pure evaluation behind the runtime and setup-command audits."""

    CLEAN_RELATIONS = [
        ("public", "crush_lu_eventregistration", False, True, False, False),
        ("public", "django_session", False, False, False, False),
        # PUBLIC's default catalog access is not a violation.
        ("pg_catalog", "pg_class", True, True, False, True),
    ]

    def test_exactly_the_allowlist_is_clean(self):
        columns = [("crush_lu_eventregistration", "status")]
        self.assertEqual(
            analytics.privilege_violations(False, self.CLEAN_RELATIONS, columns, []),
            [],
        )

    def test_everything_beyond_the_allowlist_is_reported(self):
        relations = [
            ("public", "auth_user", True, True, False, False),  # table-level SELECT
            ("public", "django_session", False, True, False, True),  # e.g. via PUBLIC
            ("public", "crush_lu_eventregistration", False, True, True, False),  # write
            ("reporting", "crush_lu_meetupevent", False, True, False, False),
            ("pg_catalog", "pg_authid", True, True, False, False),  # beyond PUBLIC
            ("pg_toast", "pg_toast_16385", True, True, False, False),
            ("pg_catalog", "pg_statistic", True, True, False, True),  # sensitive
        ]
        columns = [("auth_user", "email")]
        violations = analytics.privilege_violations(
            True,
            relations,
            columns,
            ["public"],
            ["azure_pg_admin"],
            [("public", "unsafe_export")],
            [("public", "crush_lu_meetupevent_id_seq")],
            [("postgres", True), ("pythonapp_staging", False)],
            ["postgres"],
            True,
            2,
            True,
            3,
            [("pg_catalog", "lo_import", "text")],
            ["lo_compat_privileges"],
            [("column", "crush_lu_eventregistration.status")],
        )
        joined = " | ".join(violations)
        self.assertNotIn("connect to database postgres", joined)  # allowlisted
        for expected in (
            "elevated attribute",
            "table-level SELECT on public.auth_user",
            "can read public.django_session",
            "can write public.crush_lu_eventregistration",
            "can read reporting.crush_lu_meetupevent",
            "can read public.auth_user.email",
            "can CREATE in schema public",
            "member of role azure_pg_admin",
            "can execute SECURITY DEFINER public.unsafe_export",
            "can use sequence public.crush_lu_meetupevent_id_seq",
            "can connect to database pythonapp_staging",
            "can CREATE in database postgres",
            "can CREATE in the current database",
            "can access 2 large object(s)",
            "can create TEMPORARY tables in the current database",
            "owns 3 object(s)",
            "can read system relation pg_catalog.pg_authid",
            "can read system relation pg_toast.pg_toast_16385",
            "can read system relation pg_catalog.pg_statistic",
            "can EXECUTE pg_catalog.lo_import(text) beyond PUBLIC",
            "can SET or ALTER SYSTEM parameter lo_compat_privileges",
            # An allowed column, but re-grantable: still excess.
            "holds a grant option on column crush_lu_eventregistration.status",
        ):
            self.assertIn(expected, joined)

    def _audit_with(self, version, parameter_rows):
        class FakeCursor:
            def __init__(self):
                self.executed = []
                self.last = ""

            def execute(self, sql, params=None):
                self.executed.append(sql)
                self.last = sql

            def fetchone(self):
                return (str(version),)

            def fetchall(self):
                if self.last == analytics.PARAMETER_AUDIT_SQL:
                    return parameter_rows
                return []

        cursor = FakeCursor()
        return analytics.audit_role(cursor, "crush_analytics_ro"), cursor.executed

    def test_parameter_privileges_are_audited_from_postgres_15(self):
        violations, executed = self._audit_with(170011, [("lo_compat_privileges",)])
        self.assertIn(analytics.PARAMETER_AUDIT_SQL, executed)
        self.assertEqual(
            violations, ["can SET or ALTER SYSTEM parameter lo_compat_privileges"]
        )

    def test_parameter_audit_is_skipped_before_postgres_15(self):
        violations, executed = self._audit_with(140010, [("lo_compat_privileges",)])
        self.assertNotIn(analytics.PARAMETER_AUDIT_SQL, executed)
        self.assertEqual(violations, [])


@override_settings(**CONFIGURED)
class SensitiveKeyTests(AnalyticsFixture):
    """No tool may return an identifier, whatever the parameters."""

    def _keys(self, value):
        if isinstance(value, dict):
            for key, inner in value.items():
                yield key
                yield from self._keys(inner)
        elif isinstance(value, list):
            for inner in value:
                yield from self._keys(inner)

    def test_no_identifier_in_any_response(self):
        responses = [self.get(tool) for tool in ALL_TOOLS]
        responses.append(self.get("event_detail", event_id=str(self.night.id)))
        responses.append(
            self.get(
                "demographics",
                group_by="gender,age_band,canton,verification_status,luxid",
            )
        )
        secrets_ = [u.email for u in User.objects.all()] + ["Secret", "+352621"]
        for response in responses:
            self.assertEqual(response.status_code, 200, response.content)
            payload = response.json()
            with self.subTest(tool=payload["tool"]):
                self.assertFalse(FORBIDDEN_KEYS & set(self._keys(payload["data"])))
                body = json.dumps(payload)
                for secret in secrets_:
                    self.assertNotIn(secret, body)


@override_settings(**CONFIGURED)
class SqlColumnAuditTests(AnalyticsFixture):
    """Every column the service reads must be in GRANTS (the production role)."""

    ALIAS_DEF = re.compile(r'"(\w+)"\s+(?:AS\s+)?"?([A-Z]\d+)"?(?=[\s)]|$)')
    COLUMN_REF = re.compile(r'(?:"(\w+)"|\b([A-Z]\d+))\."(\w+)"')
    TABLE_REF = re.compile(r'(?:FROM|JOIN)\s+"(\w+)"')

    def _run_everything(self):
        today = timezone.localdate()
        start = today - timedelta(days=90)
        analytics.definitions()
        analytics.kpi_weekly(12)
        analytics.funnel(start, today, "week")
        analytics.events(start, today)
        analytics.event_detail(self.night.id)
        analytics.payments(start, today, "month")
        analytics.connect(start, today)
        analytics.retention(start, today)
        analytics.demographics(list(analytics.GROUPABLE_DIMENSIONS))
        analytics.members(luxid=False, attended=True, age_band_filter="30-34")
        for granularity in ("day", "month"):
            analytics.funnel(start, today, granularity)
            analytics.payments(start, today, granularity)
        analytics.members(
            signup_from=start,
            signup_to=today,
            verification_status="verified",
            gender="F",
            canton="canton-luxembourg",
            luxid=True,
            attended=False,
        )

    def _read_columns(self, captured_queries):
        read = set()
        for query in captured_queries:
            sql = query["sql"]
            aliases = {alias: table for table, alias in self.ALIAS_DEF.findall(sql)}
            for quoted, bare, column in self.COLUMN_REF.findall(sql):
                name = quoted or bare
                read.add((aliases.get(name, name), column))
        return read

    def _violations(self, captured_queries):
        violations = set()
        for query in captured_queries:
            sql = query["sql"]
            aliases = {alias: table for table, alias in self.ALIAS_DEF.findall(sql)}
            for table in self.TABLE_REF.findall(sql):
                if table not in analytics.GRANTS:
                    violations.add(f"table {table}")
            for quoted, bare, column in self.COLUMN_REF.findall(sql):
                name = quoted or bare
                table = aliases.get(name, name)
                if re.fullmatch(r"[A-Z]\d+", table):
                    violations.add(f"unresolved alias {name}.{column}")
                elif column not in analytics.GRANTS.get(table, ()):
                    violations.add(f"{table}.{column}")
        return violations

    def test_all_sql_stays_inside_grants(self):
        # The test-account list runs on "default" by design (it needs email);
        # in production that is a different connection, so leave it out here.
        with mock.patch.object(analytics, "_test_account_user_ids", return_value=set()):
            with CaptureQueriesContext(connection) as ctx:
                self._run_everything()
        self.assertGreater(len(ctx.captured_queries), 20)
        self.assertEqual(self._violations(ctx.captured_queries), set())

    def test_every_granted_column_is_read(self):
        # The reverse direction: a grant no query needs only widens what leaked
        # credentials expose. Primary keys, and the title translations
        # modeltranslation picks by active language, are exempt.
        with mock.patch.object(analytics, "_test_account_user_ids", return_value=set()):
            with CaptureQueriesContext(connection) as ctx:
                self._run_everything()
        read = self._read_columns(ctx.captured_queries)
        exempt = {"id", "title", "title_en", "title_de", "title_fr"}
        unread = {
            f"{table}.{column}"
            for table, columns in analytics.GRANTS.items()
            for column in columns
            if column not in exempt and (table, column) not in read
        }
        self.assertEqual(unread, set())

    def test_audit_catches_an_ungranted_column(self):
        # Guard against a parser regression that silently matches nothing.
        with CaptureQueriesContext(connection) as ctx:
            list(User.objects.order_by().values_list("email", flat=True)[:1])
            list(
                MeetupEvent.objects.order_by().values_list("description", flat=True)[:1]
            )
        violations = self._violations(ctx.captured_queries)
        self.assertIn("auth_user.email", violations)
        self.assertTrue(
            any(v.startswith("crush_lu_meetupevent.description") for v in violations)
        )

    def test_test_account_lookup_never_uses_the_analytics_alias(self):
        with override_settings(ANALYTICS_DB_ALIAS="analytics"):
            with CaptureQueriesContext(connection) as ctx:
                ids = analytics._test_account_user_ids()
        self.assertIn(self.qa.id, ids)
        self.assertEqual(len(ctx.captured_queries), 1)


class SetupRoleCommandTests(TestCase):
    """Dry-run only: the real run needs PostgreSQL and an interactive getpass."""

    def test_dry_run_prints_exactly_the_allowlist(self):
        out = StringIO()
        call_command("setup_analytics_role", dry_run=True, stdout=out)
        sql = out.getvalue()
        for table in analytics.GRANTS:
            self.assertIn(f'ON public."{table}" TO "crush_analytics_ro"', sql)
        self.assertNotIn("ALL TABLES", sql)
        self.assertIn(
            'GRANT CONNECT ON DATABASE "pythonapp" TO "crush_analytics_ro"', sql
        )
        self.assertIn('GRANT USAGE ON SCHEMA public TO "crush_analytics_ro"', sql)
        self.assertIn('REVOKE TEMPORARY ON DATABASE "pythonapp" FROM PUBLIC', sql)
        self.assertIn("NOBYPASSRLS", sql)
        self.assertIn("default_transaction_read_only = 'on'", sql)
        self.assertNotIn('"email"', sql)
        self.assertNotIn('"username"', sql)

    @skipUnless(connection.vendor == "sqlite", "checks the non-PostgreSQL guard")
    def test_refuses_to_run_on_sqlite(self):
        with self.assertRaises(CommandError):
            call_command("setup_analytics_role", keep_password=True, stdout=StringIO())
