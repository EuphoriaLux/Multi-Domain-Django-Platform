"""``ConnectWeeklyRequestAdmin`` must not offer a status edit.

Every status transition in ``services.connect_cycle`` carries a dependent
write (chat row, permanent pair exclusion, ``responded_at``); a value typed
into the change form performs none of them. Prod request #4 (2026-09-17) was
``declined`` by hand with ``responded_at`` NULL and no exclusion row. These
tests pin the form as read-only for ``status`` and check that the one staff
transition offered — the ``sync_request_state`` action — writes the exclusion
the service layer guarantees.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models.crush_connect_cycle import (
    ConnectPairExclusion,
    ConnectWeeklyRequest,
    ConnectWeekSession,
)

User = get_user_model()

CHANGE_URL = "crush_admin:crush_lu_connectweeklyrequest_change"
CHANGELIST_URL = "crush_admin:crush_lu_connectweeklyrequest_changelist"


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ConnectWeeklyRequestAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        cls.superuser = User.objects.create_superuser(
            username="req_admin", email="req_admin@test.lu", password="x"
        )
        cls.requester = User.objects.create_user(
            username="req_sender", email="req_sender@test.lu", password="x"
        )
        cls.recipient = User.objects.create_user(
            username="req_recipient", email="req_recipient@test.lu", password="x"
        )
        cls.session = ConnectWeekSession.objects.create(user=cls.requester)

    def setUp(self):
        self.client.force_login(self.superuser)
        self.weekly_request = ConnectWeeklyRequest.objects.create(
            session=self.session,
            requester=self.requester,
            recipient=self.recipient,
        )

    def _change_form_post(self, **overrides):
        data = {
            "session": self.session.pk,
            "requester": self.requester.pk,
            "recipient": self.recipient.pk,
            "target_card": "",
            "message": self.weekly_request.message,
            "_save": "Save",
        }
        data.update(overrides)
        return self.client.post(
            reverse(CHANGE_URL, args=[self.weekly_request.pk]), data
        )

    def _run_sync_action(self):
        return self.client.post(
            reverse(CHANGELIST_URL),
            {
                "action": "sync_request_state_action",
                "_selected_action": [self.weekly_request.pk],
            },
        )

    def test_status_is_not_an_editable_form_field(self):
        response = self.client.get(reverse(CHANGE_URL, args=[self.weekly_request.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("status", response.context["adminform"].form.fields)

    def test_posted_status_is_ignored_and_no_side_effects_are_faked(self):
        response = self._change_form_post(status=ConnectWeeklyRequest.Status.DECLINED)
        self.assertEqual(response.status_code, 302)
        self.weekly_request.refresh_from_db()
        self.assertEqual(self.weekly_request.status, ConnectWeeklyRequest.Status.PENDING)
        self.assertIsNone(self.weekly_request.responded_at)
        self.assertFalse(
            ConnectPairExclusion.are_excluded(self.requester, self.recipient)
        )

    def test_sync_action_expires_overdue_request_with_exclusion(self):
        ConnectWeeklyRequest.objects.filter(pk=self.weekly_request.pk).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )
        response = self._run_sync_action()
        self.assertEqual(response.status_code, 302)
        self.weekly_request.refresh_from_db()
        self.assertEqual(self.weekly_request.status, ConnectWeeklyRequest.Status.EXPIRED)
        user_a, user_b = ConnectPairExclusion.get_canonical_pair(
            self.requester, self.recipient
        )
        exclusion = ConnectPairExclusion.objects.get(user_a=user_a, user_b=user_b)
        self.assertEqual(exclusion.reason, ConnectPairExclusion.Reason.REQUEST_EXPIRED)

    def test_sync_action_leaves_a_live_request_alone(self):
        response = self._run_sync_action()
        self.assertEqual(response.status_code, 302)
        self.weekly_request.refresh_from_db()
        self.assertEqual(self.weekly_request.status, ConnectWeeklyRequest.Status.PENDING)
        self.assertFalse(
            ConnectPairExclusion.are_excluded(self.requester, self.recipient)
        )
