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
import re
from datetime import date, timedelta
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

        for user, reg in ((cls.alice, cls.alice_reg), (cls.staff, staff_reg)):
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
        CrushConnectMembership.objects.create(
            user=cls.alice,
            onboarding_started_at=timezone.now(),
            onboarded_at=timezone.now(),
        )
        coach_user = User.objects.create_user(
            username="coach@crush.lu", email="coach@crush.lu", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
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

    def test_missing_event_is_404(self):
        self.assertEqual(self.get("event_detail", event_id="999999").status_code, 404)

    def test_responses_are_not_cacheable_by_intermediaries(self):
        self.assertEqual(self.get("definitions")["Cache-Control"], "no-store")


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


@override_settings(**CONFIGURED)
class ToolTests(AnalyticsFixture):
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
            night["excluded_account_registrations"], 2
        )  # staff + test account

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
            data["excluded_accounts"], {"transactions": 1, "amount_eur": 15.0}
        )
        self.assertEqual(data["credits_redeemed"], {"count": 1, "amount_eur": 5.0})
        self.assertEqual(data["premium"]["active_now"], 1)

    def test_connect_and_retention(self):
        self.assertEqual(self.data("connect")["memberships"]["onboarded_total"], 1)
        retention = self.data("retention")
        self.assertEqual(retention["attendees"], 1)
        self.assertEqual(retention["events_attended_in_window"], {"2": 1})
        self.assertEqual(retention["median_days_between_events"], 30)

    def test_demographics_suppresses_small_cross_tab_cells(self):
        crossed = self.data("demographics", group_by="gender,age_band")
        self.assertEqual(crossed["total_members"], 4)
        self.assertTrue(
            all(c["suppressed"] and c["members"] is None for c in crossed["cells"])
        )
        single = self.data("demographics", group_by="gender")
        self.assertEqual(
            {c["gender"]: c["members"] for c in single["cells"]}, {"F": 2, "M": 2}
        )

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
        self.assertIn("NOBYPASSRLS", sql)
        self.assertIn("default_transaction_read_only = 'on'", sql)
        self.assertNotIn('"email"', sql)
        self.assertNotIn('"username"', sql)

    @skipUnless(connection.vendor == "sqlite", "checks the non-PostgreSQL guard")
    def test_refuses_to_run_on_sqlite(self):
        with self.assertRaises(CommandError):
            call_command("setup_analytics_role", keep_password=True, stdout=StringIO())
