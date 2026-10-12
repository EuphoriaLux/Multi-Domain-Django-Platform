"""Dashboard history must be counted without hydrating closed event models."""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLWrapper
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from crush_lu.context_processors import crush_user_context
from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.services.member_context import (
    invalidate_member_context_data,
    member_context_data,
)
from crush_lu.tests.test_dashboard_event_status import _make_event
from crush_lu.tests.test_profile_edit_connect_card import _make_member


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DashboardHistoryLoadingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.user = _make_member("history-loading@example.com")
        self.client.force_login(self.user)

    def _register(self, title, *, days=0, status="confirmed", **event_fields):
        event = _make_event(title, days_from_now=days)
        if event_fields:
            for key, value in event_fields.items():
                setattr(event, key, value)
            event.save(update_fields=event_fields)
        return EventRegistration.objects.create(
            user=self.user, event=event, status=status
        )

    def _dashboard(self):
        with patch("django.utils.timezone.now", return_value=self.now):
            return self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")

    def _measure(self, label):
        self.assertEqual(self._dashboard().status_code, 200)
        with patch.object(
            EventRegistration, "from_db", wraps=EventRegistration.from_db
        ) as registrations, patch.object(
            MeetupEvent, "from_db", wraps=MeetupEvent.from_db
        ) as events, CaptureQueriesContext(
            connection
        ) as queries:
            response = self._dashboard()
        print(
            f"history_reads[{label}] queries={len(queries)} "
            f"registration_rows={registrations.call_count} event_models={events.call_count}"
        )
        self.assertEqual(response.status_code, 200)
        return response, registrations.call_count, events.call_count

    def test_thousand_closed_rows_keep_counts_without_loading_history(self):
        past = self.now - timedelta(days=60)
        events = MeetupEvent.objects.bulk_create(
            [
                MeetupEvent(
                    title=f"Closed history {index}",
                    description="History",
                    event_type="mixer",
                    date_time=past - timedelta(days=index),
                    location="Luxembourg",
                    address="1 Test St",
                    max_participants=20,
                    duration_minutes=120,
                    registration_deadline=past - timedelta(days=index + 1),
                    is_published=True,
                )
                for index in range(1000)
            ]
        )
        EventRegistration.objects.bulk_create(
            [
                EventRegistration(
                    user=self.user,
                    event=event,
                    status="attended" if index % 2 else "no_show",
                )
                for index, event in enumerate(events)
            ]
        )
        statuses = [
            "confirmed",
            "pending",
            "applied",
            "waitlist",
            "pending",
            "confirmed",
            "applied",
            "confirmed",
        ]
        upcoming = [
            self._register(f"Current {index}", days=index + 1, status=status)
            for index, status in enumerate(statuses)
        ]
        long_window = self._register(
            "Long connection window",
            days=-365,
            status="attended",
            connection_window_hours=24 * 400,
        )
        quiz = self._register(
            "Live quiz",
            status="attended",
            event_type="quiz_night",
            date_time=self.now - timedelta(minutes=30),
            duration_minutes=180,
        )
        response, registration_rows, event_models = self._measure("1000_closed")
        self.assertEqual(response.context["attended_count"], 502)
        self.assertEqual(response.context["upcoming_count"], 9)
        self.assertEqual(response.context["upcoming_events_count"], 5)
        self.assertEqual(response.context["next_registration"].pk, quiz.pk)
        self.assertEqual(
            [r.pk for r in response.context["pending_payment_registrations"]],
            [upcoming[1].pk, upcoming[4].pk],
        )
        actions = response.context["post_event_actions"]
        self.assertEqual(
            [a["event"].pk for a in actions], [quiz.event_id, long_window.event_id]
        )
        self.assertTrue(actions[0]["show_quiz"])
        self.assertTrue(actions[1]["show_my_crush"])
        self.assertNotContains(response, "Closed history")
        self.assertLessEqual(registration_rows, 10)
        self.assertLessEqual(event_models, 19)

    def test_upcoming_rows_are_shared_but_dashboard_count_is_not_capped_at_five(self):
        upcoming = [
            self._register(f"Upcoming {index}", days=index + 1) for index in range(7)
        ]
        response, registration_rows, _ = self._measure("seven_upcoming")
        self.assertEqual(response.context["upcoming_count"], 7)
        self.assertEqual(response.context["upcoming_events_count"], 5)
        self.assertEqual(
            [r.pk for r in response.context["upcoming_events"]],
            [r.pk for r in upcoming[:5]],
        )
        self.assertEqual(response.context["next_registration"].pk, upcoming[0].pk)
        self.assertTrue(response.context["prompt_push_eligible"])
        self.assertEqual(registration_rows, 7)

    def test_seat_end_is_inclusive_and_preserves_seven_day_event(self):
        ongoing = self._register(
            "Long live event",
            date_time=self.now - timedelta(days=7),
            duration_minutes=7 * 24 * 60,
            status="pending",
        )
        response = self._dashboard()
        self.assertEqual(response.context["upcoming_count"], 1)
        self.assertEqual(response.context["next_registration"].pk, ongoing.pk)
        self.assertFalse(response.context["next_registration"].can_cancel)
        ongoing.event.date_time -= timedelta(microseconds=1)
        ongoing.event.save(update_fields=["date_time"])
        response = self._dashboard()
        self.assertEqual(response.context["upcoming_count"], 0)
        self.assertEqual(response.context["upcoming_events_count"], 0)
        self.assertIsNone(response.context["next_registration"])
        self.assertEqual(response.context["pending_payment_registrations"], [])

    def test_seven_day_event_keeps_recap_eight_days_after_start(self):
        registration = self._register(
            "Long event recap",
            date_time=self.now - timedelta(days=8),
            duration_minutes=7 * 24 * 60,
            status="attended",
            connection_window_hours=0,
        )
        with patch(
            "crush_lu.services.event_lobby.lobby_cta", return_value="enter_recap"
        ):
            response = self._dashboard()
        actions = response.context["post_event_actions"]
        self.assertEqual([a["event"].pk for a in actions], [registration.event_id])
        self.assertEqual(actions[0]["lobby_cta"], "enter_recap")
        self.assertEqual(response.context["upcoming_count"], 0)

    def test_connection_deadline_is_inclusive_even_with_a_year_long_window(self):
        registration = self._register(
            "Year-long My Crush",
            status="attended",
            connection_window_hours=24 * 365,
            date_time=self.now - timedelta(days=365, minutes=120),
        )
        response = self._dashboard()
        actions = response.context["post_event_actions"]
        self.assertEqual([a["event"].pk for a in actions], [registration.event_id])
        self.assertTrue(actions[0]["show_my_crush"])
        self.assertEqual(response.context["attended_count"], 1)
        registration.event.date_time -= timedelta(microseconds=1)
        registration.event.save(update_fields=["date_time"])
        with patch.object(
            EventRegistration, "from_db", wraps=EventRegistration.from_db
        ) as registrations:
            response = self._dashboard()
        self.assertEqual(response.context["post_event_actions"], [])
        self.assertEqual(response.context["attended_count"], 1)
        self.assertEqual(registrations.call_count, 0)

    def test_quiz_deadline_is_inclusive_after_the_lobby_window_closes(self):
        registration = self._register(
            "Quiz boundary",
            status="attended",
            event_type="quiz_night",
            connection_window_hours=0,
            date_time=self.now - timedelta(days=2, minutes=120),
        )
        response = self._dashboard()
        actions = response.context["post_event_actions"]
        self.assertEqual([a["event"].pk for a in actions], [registration.event_id])
        self.assertTrue(actions[0]["show_quiz"])
        self.assertIsNone(actions[0]["lobby_cta"])
        registration.event.date_time -= timedelta(microseconds=1)
        registration.event.save(update_fields=["date_time"])
        with patch.object(
            EventRegistration, "from_db", wraps=EventRegistration.from_db
        ) as registrations:
            response = self._dashboard()
        self.assertEqual(response.context["post_event_actions"], [])
        self.assertEqual(registrations.call_count, 0)

    def test_lobby_gate_still_decides_whether_a_recent_candidate_has_an_action(self):
        registration = self._register(
            "Recent lobby",
            status="attended",
            days=-1,
            connection_window_hours=0,
        )
        for cta in (None, "promo_only", "enter_recap"):
            with self.subTest(cta=cta), patch(
                "crush_lu.services.event_lobby.lobby_cta", return_value=cta
            ):
                response = self._dashboard()
                actions = response.context["post_event_actions"]
                if cta == "enter_recap":
                    self.assertEqual(
                        [a["event"].pk for a in actions], [registration.event_id]
                    )
                else:
                    self.assertEqual(actions, [])
                    self.assertNotContains(response, "Recent lobby")

    def test_pulled_rows_and_other_members_do_not_hydrate_or_inflate_counts(self):
        self._register("Cancelled upcoming", days=2, is_cancelled=True)
        self._register(
            "Cancelled attendance", days=-1, status="attended", is_cancelled=True
        )
        self._register(
            "Hidden attendance", days=-1, status="attended", is_published=False
        )
        other = _make_member("other-history@example.com")
        event = _make_event("Another member's event", days_from_now=-1)
        EventRegistration.objects.create(user=other, event=event, status="attended")
        with patch.object(
            EventRegistration, "from_db", wraps=EventRegistration.from_db
        ) as registrations:
            response = self._dashboard()
        self.assertEqual(response.context["attended_count"], 0)
        self.assertEqual(response.context["upcoming_count"], 0)
        self.assertEqual(response.context["post_event_actions"], [])
        self.assertTrue(response.context["prompt_push_eligible"])
        self.assertEqual(registrations.call_count, 0)

    def _request(self):
        request = RequestFactory().get("/en/dashboard/", HTTP_HOST="crush.lu")
        request.user = self.user
        request.session = {}
        return request

    def test_non_dashboard_nav_loads_only_upcoming_and_keeps_exists_check(self):
        self._register("Current seat", days=1)
        self._register(
            "Old active window",
            days=-100,
            status="attended",
            connection_window_hours=24 * 200,
        )
        with patch.object(
            EventRegistration, "from_db", wraps=EventRegistration.from_db
        ) as registrations, CaptureQueriesContext(connection) as queries:
            context = crush_user_context(self._request())
        self.assertEqual(context["upcoming_events_count"], 1)
        self.assertEqual(registrations.call_count, 1)
        self.assertTrue(context["prompt_push_eligible"])
        self.assertFalse(
            any(
                'FROM "crush_lu_eventregistration"' in query["sql"]
                and "COUNT(" in query["sql"]
                for query in queries
            )
        )

    def test_request_invalidation_discards_counts_and_current_candidates_together(self):
        request = self._request()
        before = member_context_data(request)
        self.assertEqual(before.dashboard_registrations, [])
        self.assertEqual(before.upcoming_registrations, [])
        self.assertEqual(before.registration_counts["attended"], 0)
        self.assertFalse(before.has_booked)
        registration = self._register("New attended seat", days=1, status="attended")
        invalidate_member_context_data(request)
        after = member_context_data(request)
        self.assertIsNot(after, before)
        self.assertEqual(
            [r.pk for r in after.dashboard_registrations], [registration.pk]
        )
        self.assertEqual(
            [r.pk for r in after.upcoming_registrations], [registration.pk]
        )
        self.assertEqual(after.registration_counts["attended"], 1)
        self.assertTrue(after.has_booked)

    def test_deadline_query_compiles_as_native_postgres_intervals_without_connecting(
        self,
    ):
        request = self._request()
        data = member_context_data(request)
        postgres = PostgreSQLWrapper(
            {
                **connection.settings_dict,
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "compile_only",
            },
            alias="history_compile_only",
        )
        postgres.pg_version = 160000
        with patch.object(
            postgres,
            "ensure_connection",
            side_effect=AssertionError("compilation must not connect"),
        ) as connect:
            sql, params = (
                data._dashboard_registration_queryset()
                .query.get_compiler(connection=postgres)
                .as_sql()
            )
        connect.assert_not_called()
        self.assertIn("::integer", sql)
        self.assertIn(" * %s", sql)
        self.assertIn(timedelta(minutes=1), params)
        self.assertIn(timedelta(hours=1), params)
        self.assertIn(timedelta(days=2), params)
        self.assertNotIn("django_format_dtdelta", sql)

    def test_standalone_nav_fetches_five_without_truncating_later_dashboard(self):
        upcoming = [
            self._register(f"Many future {index}", days=index + 1)
            for index in range(40)
        ]
        request = self._request()
        with patch.object(
            EventRegistration, "from_db", wraps=EventRegistration.from_db
        ) as registrations, patch.object(
            MeetupEvent, "from_db", wraps=MeetupEvent.from_db
        ) as events, CaptureQueriesContext(
            connection
        ) as queries:
            context = crush_user_context(request)
        print(
            f"history_reads[standalone_nav_40] queries={len(queries)} "
            f"registration_rows={registrations.call_count} event_models={events.call_count}"
        )
        self.assertEqual(registrations.call_count, 5)
        self.assertEqual(events.call_count, 5)
        self.assertEqual(context["upcoming_events_count"], 5)
        self.assertEqual(
            [r.pk for r in context["upcoming_events"]], [r.pk for r in upcoming[:5]]
        )
        data = member_context_data(request)
        self.assertEqual(len(data.dashboard_registrations), 40)
        self.assertEqual(len(data.upcoming_registrations), 40)
        self.assertEqual(len(data.navigation_registrations), 5)
