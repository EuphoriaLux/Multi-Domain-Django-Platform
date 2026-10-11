"""Dashboard/navigation must share reads without sharing state between requests."""

import re
from datetime import timedelta
from unittest.mock import PropertyMock, patch

from django.core.cache import cache
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from crush_lu.context_processors import crush_user_context
from crush_lu.models import (
    ConfirmedEncounter,
    CrushCoach,
    CrushProfile,
    EventConnection,
    EventRegistration,
    ProfileSubmission,
    UserBlock,
)
from crush_lu.services.member_context import member_context_data
from crush_lu.tests.test_dashboard_event_status import _make_event
from crush_lu.tests.test_profile_edit_connect_card import _make_member


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DashboardRequestDataTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = _make_member("request-data@example.com")
        self.profile = self.user.crushprofile
        self.client.force_login(self.user)
        self.factory = RequestFactory()

    def _request(self, user=None):
        request = self.factory.get("/en/dashboard/", HTTP_HOST="crush.lu")
        request.user = user or self.user
        request.session = {}
        return request

    def _dashboard(self):
        return self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")

    def _measure_dashboard(self, label):
        # Warm process/Site caches and the dashboard's existing referral row;
        # the next HTTP request still has its own cold member-data snapshot.
        self.assertEqual(self._dashboard().status_code, 200)
        with CaptureQueriesContext(connection) as queries:
            response = self._dashboard()
        counts = {}
        for model in (CrushProfile, ProfileSubmission, UserBlock, ConfirmedEncounter):
            primary_from = f'FROM "{model._meta.db_table}"'
            counts[model.__name__] = sum(
                primary_from in query["sql"]
                and query["sql"].startswith("SELECT")
                and "COUNT(" not in query["sql"]
                for query in queries
            )
        counts["active_connection_count"] = sum(
            f'FROM "{EventConnection._meta.db_table}"' in query["sql"]
            and "COUNT(" in query["sql"]
            and "coach_reviewing" in query["sql"]
            for query in queries
        )
        print(f"dashboard_reads[{label}] total={len(queries)} shared={counts}")
        self.assertEqual(response.status_code, 200)
        expected = dict.fromkeys(counts, 1)
        # One other profile read remains outside the shared dashboard/nav
        # loader: this bounded change reduces three reads to two.
        expected["CrushProfile"] = 2
        self.assertEqual(counts, expected)
        return response

    def test_ordinary_member_reads_shared_rows_once(self):
        response = self._measure_dashboard("ordinary")
        self.assertEqual(response.context["profile"].pk, self.profile.pk)
        self.assertEqual(response.context["connection_count"], 0)
        self.assertTrue(response.context["nav_has_profile"])
        self.assertIsNone(response.context["submission"])

    def test_coach_member_preserves_submission_and_screening_state(self):
        coach = CrushCoach.objects.create(user=self.user, is_active=True)
        submission = ProfileSubmission.objects.create(
            profile=self.profile, coach=coach, status="pending"
        )
        response = self._measure_dashboard("coach")
        self.assertEqual(response.context["submission"].pk, submission.pk)
        self.assertEqual(response.context["profile_submission"].pk, submission.pk)
        self.assertEqual(response.context["coach"].pk, coach.pk)
        self.assertTrue(response.context["nav_is_active_coach"])
        self.assertEqual(response.context["pending_screening_count"], 1)

    def test_history_does_not_change_shared_read_count_or_event_state(self):
        for index in range(30):
            EventRegistration.objects.create(
                user=self.user,
                event=_make_event(f"History {index}", days_from_now=-100 - index),
                status="attended" if index % 2 else "no_show",
            )
        upcoming = _make_event("Next visible event", days_from_now=5)
        EventRegistration.objects.create(
            user=self.user, event=upcoming, status="confirmed"
        )
        response = self._measure_dashboard("history")
        self.assertEqual(response.context["attended_count"], 15)
        self.assertEqual(response.context["upcoming_count"], 1)
        self.assertEqual(response.context["next_registration"].event_id, upcoming.pk)
        self.assertEqual(response.context["upcoming_events_count"], 1)
        self.assertNotContains(response, "History 1")

    def test_connections_stay_private_and_blocked_pairs_hidden(self):
        event = _make_event("Connections", days_from_now=-10)
        visible = _make_member("visible@example.com")
        blocked = _make_member("blocked@example.com")
        removed = _make_member("removed@example.com")
        private = _make_member("private@example.com")
        pending = _make_member("pending@example.com")
        for other, flow in (
            (visible, EventConnection.FLOW_LEGACY),
            (blocked, EventConnection.FLOW_LEGACY),
            (removed, EventConnection.FLOW_LEGACY),
            (private, EventConnection.FLOW_CRUSH),
        ):
            EventConnection.objects.create(
                event=event,
                requester=other,
                recipient=self.user,
                flow=flow,
                status="coach_reviewing",
            )
        pending_row = EventConnection.objects.create(
            event=event, requester=pending, recipient=self.user, status="pending"
        )
        UserBlock.objects.create(blocker=blocked, blocked=self.user)
        low, high = ConfirmedEncounter.canonical_pair(self.user, removed)
        ConfirmedEncounter.objects.create(
            user_low=low, user_high=high, status="removal_pending"
        )
        response = self._measure_dashboard("blocked")
        self.assertEqual(response.context["connection_count"], 1)
        self.assertEqual(response.context["pending_requests_count"], 1)
        self.assertEqual(
            [row.pk for row in response.context["pending_connection_requests"]],
            [pending_row.pk],
        )

    def test_expired_latest_submission_never_restores_older_revision(self):
        older = ProfileSubmission.objects.create(
            profile=self.profile, status="revision"
        )
        ProfileSubmission.objects.filter(pk=older.pk).update(
            submitted_at=timezone.now() - timedelta(days=2)
        )
        ProfileSubmission.objects.create(profile=self.profile, status="expired")
        response = self._dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["submission"])
        self.assertFalse(any("profile_submission" in ctx for ctx in response.context))
        self.assertFalse(any("profile_needs_action" in ctx for ctx in response.context))

    def test_missing_profile_keeps_redirect_and_safe_navigation(self):
        self.profile.delete()
        response = self._dashboard()
        self.assertRedirects(
            response, "/en/create-profile/", fetch_redirect_response=False
        )
        request = self._request()
        with CaptureQueriesContext(connection) as queries:
            first = crush_user_context(request)
            second = crush_user_context(request)
        self.assertIsNone(first["profile"])
        self.assertIsNone(second["profile"])
        self.assertFalse(first["nav_has_profile"])
        profile_reads = [
            q for q in queries if re.search(r'FROM "crush_lu_crushprofile"', q["sql"])
        ]
        self.assertEqual(len(profile_reads), 1)

    def test_snapshot_is_rebound_when_request_account_changes(self):
        other = _make_member("other-request-data@example.com")
        UserBlock.objects.create(blocker=self.user, blocked=other)
        request = self._request()
        first = member_context_data(request)
        self.assertEqual(first.profile.user_id, self.user.pk)
        self.assertEqual(first.hidden_counterpart_ids, {other.pk})
        request.user = other
        second = member_context_data(request)
        self.assertIsNot(first, second)
        self.assertEqual(second.profile.user_id, other.pk)
        self.assertEqual(second.hidden_counterpart_ids, {self.user.pk})

    def test_new_request_reloads_profile_submission_and_block_changes(self):
        first = crush_user_context(self._request())
        self.assertTrue(first["profile_is_approved"])
        CrushProfile.objects.filter(pk=self.profile.pk).update(
            verification_status="pending", is_approved=False
        )
        submission = ProfileSubmission.objects.create(
            profile=self.profile, status="revision"
        )
        other = _make_member("later-block@example.com")
        event = _make_event("Later connection", days_from_now=-10)
        EventConnection.objects.create(
            event=event, requester=self.user, recipient=other, status="shared"
        )
        before_block = crush_user_context(self._request())
        self.assertEqual(before_block["connection_count"], 1)
        UserBlock.objects.create(blocker=self.user, blocked=other)
        after_block = crush_user_context(self._request())
        self.assertEqual(after_block["connection_count"], 0)
        self.assertFalse(after_block["profile_is_approved"])
        self.assertEqual(after_block["profile_submission"].pk, submission.pk)
        self.assertTrue(after_block["profile_needs_action"])
        self.assertTrue(first["profile_is_approved"])

    def test_failed_lookup_preserves_safe_defaults_and_can_retry(self):
        request = self._request()
        with patch(
            "crush_lu.services.blocking.blocked_user_ids",
            side_effect=RuntimeError("temporary backend failure"),
        ):
            failed = crush_user_context(request)
        self.assertEqual(failed["connection_count"], 0)
        self.assertFalse(failed["nav_has_profile"])
        recovered = crush_user_context(request)
        self.assertTrue(recovered["nav_has_profile"])
        self.assertEqual(recovered["profile"].pk, self.profile.pk)

    def _verify_after_write(self, *, fail):
        CrushProfile.objects.filter(pk=self.profile.pk).update(
            verification_status="pending", is_approved=False
        )
        submission = ProfileSubmission.objects.create(
            profile=self.profile, status="pending"
        )

        def transition(user, profile, current_submission, request):
            self.assertEqual(current_submission.pk, submission.pk)
            CrushProfile.objects.filter(pk=profile.pk).update(
                verification_status="verified",
                is_approved=True,
                verification_method="luxid",
            )
            ProfileSubmission.objects.filter(pk=submission.pk).update(status="approved")
            if fail:
                raise RuntimeError("side effect failed after the write")

        with patch.object(
            CrushProfile,
            "has_luxid_connected",
            new_callable=PropertyMock,
            return_value=True,
        ), patch(
            "crush_lu.signals._execute_luxid_direct_verify", side_effect=transition
        ):
            response = self._dashboard()
            if not fail:
                self.assertRedirects(
                    response, "/en/dashboard/", fetch_redirect_response=False
                )
                response = self._dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["profile"].verification_status, "verified")
        self.assertTrue(response.context["profile_is_approved"])
        self.assertEqual(response.context["submission"].status, "approved")
        self.assertEqual(response.context["profile_submission"].status, "approved")
        self.assertIs(
            response.context["profile"],
            member_context_data(response.wsgi_request).profile,
        )

    def test_partial_verification_failure_reloads_written_state_before_render(self):
        self._verify_after_write(fail=True)

    def test_successful_verification_redirect_keeps_fresh_state(self):
        self._verify_after_write(fail=False)
