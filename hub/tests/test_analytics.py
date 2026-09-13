"""Tests for the staff-only Hub analytics API and provider isolation."""

from datetime import date
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from crush_lu.models import CrushProfile, WeeklyMetricsSnapshot
from hub.analytics_service import (
    ProviderResult,
    _merge_landing_pages,
    build_analytics_overview,
    fetch_application_insights,
    fetch_ga4,
    fetch_search_console,
)

User = get_user_model()


class AnalyticsOverviewApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="analytics_staff", password="password123", is_staff=True
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.staff)

    def test_non_staff_cannot_access_analytics(self):
        member = User.objects.create_user(username="member", password="password123")
        self.client.force_authenticate(user=member)
        response = self.client.get("/hub/analytics/overview")
        self.assertEqual(response.status_code, 403)

    def test_invalid_period_is_rejected(self):
        response = self.client.get("/hub/analytics/overview?days=365")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "days must be one of 7, 28, or 90.")

    @patch("hub.views_analytics.build_analytics_overview")
    def test_response_is_cached_without_browser_storage(self, build):
        build.return_value = {"generatedAt": "2026-09-13T10:00:00+00:00"}
        first = self.client.get("/hub/analytics/overview?days=28")
        second = self.client.get("/hub/analytics/overview?days=28")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(first.headers["Cache-Control"], "private, no-store")


class AnalyticsOverviewServiceTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="pending_member")
        CrushProfile.objects.update_or_create(
            user=user,
            defaults={"verification_status": "pending"},
        )
        WeeklyMetricsSnapshot.objects.create(
            week_start=date(2026, 9, 7),
            week_end=date(2026, 9, 13),
            metrics={
                "acquisition": {
                    "new_signups": 12,
                    "profiles_submitted": 7,
                    "profiles_verified": 5,
                    "cumulative_verified_members": 638,
                },
                "revenue": {"waitlist_new": 4, "waitlist_total": 319},
            },
        )

    @patch("hub.analytics_service.timezone.localdate", return_value=date(2026, 9, 13))
    @patch("hub.analytics_service.fetch_application_insights")
    @patch("hub.analytics_service.fetch_ga4")
    @patch("hub.analytics_service.fetch_search_console")
    def test_builds_aggregate_payload(self, gsc, ga4, insights, _today):
        gsc.return_value = {
            "summary": {"clicks": 40, "impressions": 800, "ctr": 0.05, "position": 8.2},
            "daily": [],
            "pages": [
                {
                    "path": "/en/events",
                    "clicks": 20,
                    "impressions": 400,
                    "ctr": 0.05,
                    "position": 7.0,
                }
            ],
        }
        ga4.return_value = {
            "summary": {"sessions": 35, "users": 30, "engagementRate": 0.6},
            "daily": [],
            "pages": [
                {
                    "path": "/en/events",
                    "sessions": 18,
                    "users": 16,
                    "engagementRate": 0.7,
                }
            ],
        }
        insights.return_value = {
            "summary": {"pageViews": 50, "sessions": 20, "users": 15, "exceptions": 1},
            "daily": [],
            "events": [{"name": "events_page_viewed", "count": 10}],
        }

        payload = build_analytics_overview(28)

        self.assertEqual(payload["overview"]["searchImpressions"], 800)
        self.assertEqual(payload["overview"]["webSessions"], 35)
        self.assertEqual(payload["overview"]["pendingProfiles"], 1)
        self.assertEqual(payload["search"]["landingPages"][0]["sessions"], 18)
        self.assertEqual(payload["activation"]["weekly"][0]["profilesVerified"], 5)
        insights.assert_called_once_with(date(2026, 8, 14), date(2026, 9, 10))
        app_insights_source = next(
            source
            for source in payload["sources"]
            if source["id"] == "application-insights"
        )
        self.assertEqual(app_insights_source["observedThrough"], "2026-09-10")

    @patch("hub.analytics_service.timezone.localdate", return_value=date(2026, 9, 13))
    @patch(
        "hub.analytics_service.fetch_application_insights",
        side_effect=RuntimeError("secret diagnostic"),
    )
    @patch(
        "hub.analytics_service.fetch_ga4",
        side_effect=RuntimeError("private upstream error"),
    )
    @patch(
        "hub.analytics_service.fetch_search_console",
        side_effect=RuntimeError("credential detail"),
    )
    def test_provider_failures_are_isolated_and_sanitized(
        self, _gsc, _ga4, _insights, _today
    ):
        payload = build_analytics_overview(28)
        self.assertEqual(payload["overview"]["searchClicks"], None)
        self.assertEqual(payload["overview"]["pendingProfiles"], 1)
        rendered = str(payload)
        self.assertNotIn("secret diagnostic", rendered)
        self.assertNotIn("private upstream error", rendered)
        self.assertNotIn("credential detail", rendered)
        self.assertEqual(
            {source["status"] for source in payload["sources"]},
            {"ready", "error"},
        )


class LandingPageMergeTests(TestCase):
    def test_missing_provider_metrics_remain_null(self):
        gsc = ProviderResult(
            status="ready",
            data={
                "pages": [
                    {
                        "path": "/en/events",
                        "clicks": 5,
                        "impressions": 100,
                        "ctr": 0.05,
                        "position": 4.0,
                    }
                ]
            },
        )
        ga4 = ProviderResult(status="unavailable", data={})
        row = _merge_landing_pages(gsc, ga4)[0]
        self.assertEqual(row["clicks"], 5)
        self.assertIsNone(row["sessions"])

    def test_colliding_search_console_paths_are_aggregated(self):
        gsc = ProviderResult(
            status="ready",
            data={
                "pages": [
                    {
                        "path": "/en/events",
                        "clicks": 5,
                        "impressions": 100,
                        "ctr": 0.05,
                        "position": 4.0,
                    },
                    {
                        "path": "/en/events",
                        "clicks": 3,
                        "impressions": 50,
                        "ctr": 0.06,
                        "position": 10.0,
                    },
                ]
            },
        )
        row = _merge_landing_pages(gsc, ProviderResult(status="unavailable", data={}))[
            0
        ]

        self.assertEqual(row["clicks"], 8)
        self.assertEqual(row["impressions"], 150)
        self.assertAlmostEqual(row["ctr"], 8 / 150)
        self.assertAlmostEqual(row["position"], 6.0)


@override_settings(HUB_ANALYTICS_HTTP_TIMEOUT_SECONDS=5)
class SearchConsoleTests(TestCase):
    @patch("hub.analytics_service.monotonic", side_effect=[100, 101, 102, 103])
    @patch("hub.analytics_service.build")
    @patch("hub.analytics_service.AuthorizedHttp")
    @patch("hub.analytics_service.httplib2.Http")
    @patch("hub.analytics_service._google_credentials")
    def test_uses_one_bounded_http_budget(
        self, credentials, http, authorized_http, build, _monotonic
    ):
        raw_http = Mock()
        http.return_value = raw_http
        transport = Mock()
        transport.http = raw_http
        authorized_http.return_value = transport
        request = Mock()
        request.execute.side_effect = [{"rows": []}, {"rows": []}, {"rows": []}]
        service = Mock()
        service.searchanalytics.return_value.query.return_value = request
        build.return_value = service

        fetch_search_console(date(2026, 9, 1), date(2026, 9, 7))

        http.assert_called_once_with(timeout=5)
        authorized_http.assert_called_once_with(credentials.return_value, http=raw_http)
        build.assert_called_once_with(
            "searchconsole", "v1", http=transport, cache_discovery=False
        )
        self.assertEqual(request.execute.call_count, 3)
        self.assertEqual(raw_http.timeout, 2)


@override_settings(HUB_ANALYTICS_GA4_PROPERTY_ID="516337382")
class GA4Tests(TestCase):
    @patch("hub.analytics_service._ga4_report")
    @patch("hub.analytics_service.BetaAnalyticsDataClient")
    @patch("hub.analytics_service._google_credentials")
    def test_uses_path_only_dimension_without_adding_distinct_users(
        self, _credentials, _client, report
    ):
        summary = Mock(
            rows=[
                Mock(
                    metric_values=[
                        Mock(value="8"),
                        Mock(value="4"),
                        Mock(value="0.6"),
                    ]
                )
            ]
        )
        daily = Mock(rows=[])
        pages = Mock(
            rows=[
                Mock(
                    dimension_values=[Mock(value="/events")],
                    metric_values=[
                        Mock(value="5"),
                        Mock(value="4"),
                        Mock(value="0.6"),
                    ],
                ),
                Mock(
                    dimension_values=[Mock(value="/events/")],
                    metric_values=[
                        Mock(value="3"),
                        Mock(value="3"),
                        Mock(value="0.5"),
                    ],
                ),
            ]
        )
        report.side_effect = [summary, daily, pages]

        result = fetch_ga4(date(2026, 9, 1), date(2026, 9, 7))

        self.assertEqual(report.call_args_list[2].kwargs["dimensions"], ["landingPage"])
        self.assertEqual(result["pages"][0]["sessions"], 8)
        self.assertEqual(result["pages"][0]["users"], 4)
        self.assertAlmostEqual(result["pages"][0]["engagementRate"], 0.5625)


@override_settings(
    APPLICATIONINSIGHTS_CONNECTION_STRING=(
        "InstrumentationKey=test-key;ApplicationId=app-id"
    ),
    HUB_ANALYTICS_HTTP_TIMEOUT_SECONDS=5,
)
class ApplicationInsightsTests(TestCase):
    @patch("hub.analytics_service.requests.get")
    @patch("hub.analytics_service.DefaultAzureCredential")
    def test_uses_managed_identity_and_only_fixed_aggregate_queries(
        self, credential, get
    ):
        credential.return_value.get_token.return_value.token = "token"
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = [
            {
                "tables": [
                    {
                        "columns": [
                            {"name": "page_views"},
                            {"name": "sessions"},
                            {"name": "users"},
                            {"name": "exceptions"},
                        ],
                        "rows": [[100, 40, 25, 2]],
                    }
                ]
            },
            {
                "tables": [
                    {
                        "columns": [
                            {"name": "day_label"},
                            {"name": "page_views"},
                            {"name": "sessions"},
                            {"name": "users"},
                            {"name": "exceptions"},
                        ],
                        "rows": [["2026-09-12", 10, 5, 4, 1]],
                    }
                ]
            },
            {
                "tables": [
                    {
                        "columns": [{"name": "name"}, {"name": "event_count"}],
                        "rows": [["dashboard_viewed", 8]],
                    }
                ]
            },
        ]
        get.return_value = response

        result = fetch_application_insights(
            date(2026, 9, 6),
            date(2026, 9, 12),
        )

        self.assertEqual(result["summary"]["users"], 25)
        self.assertEqual(
            result["daily"][0],
            {
                "date": "2026-09-12",
                "page_views": 10,
                "sessions": 5,
                "users": 4,
                "exceptions": 1,
            },
        )
        self.assertEqual(result["events"][0], {"name": "dashboard_viewed", "count": 8})
        self.assertEqual(get.call_count, 3)
        for call in get.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 5)
            self.assertNotIn("token", call.kwargs["params"]["query"])
            self.assertIn("datetime(2026-09-06)", call.kwargs["params"]["query"])
            self.assertIn("datetime(2026-09-13)", call.kwargs["params"]["query"])
            self.assertNotIn("ago(", call.kwargs["params"]["query"])
            self.assertIn("user_AuthenticatedId", call.kwargs["params"]["query"])
            self.assertIn("coalesce(itemCount, 1)", call.kwargs["params"]["query"])
            self.assertIn(
                "session_Id in (crush_sessions)", call.kwargs["params"]["query"]
            )
        summary_query = get.call_args_list[0].kwargs["params"]["query"]
        self.assertIn("parse_url(url).Host", summary_query)
        self.assertIn("dcount(user_AuthenticatedId)", summary_query)
        self.assertNotIn("dcount(user_Id)", summary_query)
        daily_query = get.call_args_list[1].kwargs["params"]["query"]
        self.assertIn("project day_label=", daily_query)
        self.assertNotIn("project date=", daily_query)
        self.assertIn("datetime_utc_to_local", daily_query)
