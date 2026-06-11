"""
Admin and image-processing regression tests.

Covers:
- Every changelist on crush_admin_site renders for a superuser
  (catches invalid list_select_related / list_display references at runtime)
- EventRegistration changelist query count stays flat as rows grow
  (guards the list_select_related N+1 fix)
- New admin registrations (EventFeedback, CallAttempt, UserDataConsent,
  Notification) are present and read-only where intended
- process_uploaded_image rejects oversized files before decoding

Run with: pytest crush_lu/tests/test_admin_smoke.py -v
"""

import io

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from crush_lu.admin import crush_admin_site
from crush_lu.models import (
    CallAttempt,
    EventFeedback,
    EventRegistration,
    MeetupEvent,
    Notification,
    UserDataConsent,
)
from crush_lu.utils.image_processing import (
    MAX_UPLOAD_BYTES,
    process_uploaded_image,
)

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {
    "ROOT_URLCONF": "azureproject.urls_crush",
}


class SiteTestMixin:
    """Mixin to create Site object for tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class AdminChangelistSmokeTests(SiteTestMixin, TestCase):
    """Every registered changelist must render without errors.

    An invalid relation name in list_select_related (or a broken
    list_display callable) only fails at render time, not in
    `manage.py check` — this test catches those regressions.
    """

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="admin_smoke", email="admin@test.lu", password="x"
        )

    def test_all_changelists_render(self):
        self.client.force_login(self.superuser)
        failures = []
        for model in crush_admin_site._registry:
            url = reverse(
                f"{crush_admin_site.name}:"
                f"{model._meta.app_label}_{model._meta.model_name}_changelist"
            )
            response = self.client.get(url)
            if response.status_code != 200:
                failures.append(
                    f"{model.__name__}: HTTP {response.status_code} at {url}"
                )
        self.assertEqual(
            failures, [], "Changelists failed to render:\n" + "\n".join(failures)
        )

    def test_new_models_are_registered(self):
        for model in (EventFeedback, CallAttempt, UserDataConsent, Notification):
            self.assertIn(
                model, crush_admin_site._registry, f"{model.__name__} not registered"
            )

    def test_log_style_admins_disallow_add(self):
        """App-written records must not be creatable by hand in the admin."""
        request = type("Req", (), {"user": self.superuser})()
        for model in (EventFeedback, CallAttempt, UserDataConsent, Notification):
            model_admin = crush_admin_site._registry[model]
            self.assertFalse(
                model_admin.has_add_permission(request),
                f"{model.__name__} admin should not allow manual adds",
            )

    def test_log_style_admins_are_fully_read_only(self):
        """App-written audit records must not be editable field by field.

        Edits where legitimately needed happen on the workflow's own
        surfaces (e.g. CallAttemptInline on ProfileSubmission), never on
        these audit/log changelists.
        """
        for model in (EventFeedback, CallAttempt, UserDataConsent, Notification):
            model_admin = crush_admin_site._registry[model]
            editable_fields = [
                f.name for f in model._meta.fields if f.editable and f.name != "id"
            ]
            for field in editable_fields:
                self.assertIn(
                    field,
                    model_admin.readonly_fields,
                    f"{model.__name__}.{field} should be read-only in the admin",
                )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class EventRegistrationChangelistQueryTests(SiteTestMixin, TestCase):
    """The registration changelist must not run one query per row."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="admin_queries", email="queries@test.lu", password="x"
        )
        cls.event = MeetupEvent.objects.create(
            title="Query Test Event",
            description="Test",
            date_time=timezone.now() + timezone.timedelta(days=7),
            registration_deadline=timezone.now() + timezone.timedelta(days=5),
            location="Test Hall",
            address="1 Test Street",
            canton="luxembourg",
            max_participants=100,
        )

    def _changelist_query_count(self):
        url = reverse(f"{crush_admin_site.name}:crush_lu_eventregistration_changelist")
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_query_count_stays_flat_as_rows_grow(self):
        self.client.force_login(self.superuser)

        for i in range(3):
            user = User.objects.create_user(username=f"reg_user_{i}", password="x")
            EventRegistration.objects.create(
                user=user, event=self.event, status="confirmed"
            )
        queries_small = self._changelist_query_count()

        for i in range(3, 13):
            user = User.objects.create_user(username=f"reg_user_{i}", password="x")
            EventRegistration.objects.create(
                user=user, event=self.event, status="confirmed"
            )
        queries_large = self._changelist_query_count()

        self.assertEqual(
            queries_small,
            queries_large,
            "EventRegistration changelist query count grew with row count "
            f"({queries_small} -> {queries_large}): N+1 regression.",
        )


class ImageUploadSizeTests(TestCase):
    """process_uploaded_image must reject oversized files before decoding."""

    def test_oversized_file_rejected(self):
        oversized = ContentFile(b"x" * (MAX_UPLOAD_BYTES + 1), name="huge.jpg")
        with self.assertRaises(ValidationError):
            process_uploaded_image(oversized)

    def test_normal_image_still_processed(self):
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (300, 300), color="red").save(buffer, format="JPEG")
        upload = ContentFile(buffer.getvalue(), name="ok.jpg")

        processed = process_uploaded_image(upload)

        self.assertEqual(processed.content_type, "image/jpeg")
        self.assertLessEqual(processed.size, MAX_UPLOAD_BYTES)

    def test_size_check_uses_file_size_attribute(self):
        """Files without a .size attribute fall back to seek/tell."""
        raw = io.BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1))
        with self.assertRaises(ValidationError):
            process_uploaded_image(raw)
