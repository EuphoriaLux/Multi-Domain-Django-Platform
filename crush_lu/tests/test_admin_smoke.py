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
from django.contrib.auth.models import Permission
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
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

    def test_log_style_admins_disallow_add_and_delete(self):
        """App-written records must not be creatable or deletable by hand."""
        request = type("Req", (), {"user": self.superuser})()
        for model in (EventFeedback, CallAttempt, UserDataConsent, Notification):
            model_admin = crush_admin_site._registry[model]
            self.assertFalse(
                model_admin.has_add_permission(request),
                f"{model.__name__} admin should not allow manual adds",
            )
            self.assertFalse(
                model_admin.has_delete_permission(request),
                f"{model.__name__} admin should not allow deletes",
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
class AdminMenuOrganizationTests(SiteTestMixin, TestCase):
    """The crush-admin sidebar must surface every registered model.

    The menu is built from a hardcoded ``custom_order`` dict in
    ``CrushLuAdminSite.get_app_list``; a model registered but absent from that
    dict used to silently vanish from the sidebar (reachable only by URL). The
    auto-catch 'Other' group now backstops that — these tests lock it in, and
    verify the superuser-only groups stay hidden from ordinary coaches.
    """

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="admin_menu", email="menu@test.lu", password="x"
        )
        # A non-superuser coach with full view perms, so any model missing from
        # their menu is the superuser GATE at work, not a permission accident.
        coach = User.objects.create_user(
            username="coach_menu", email="coach@test.lu", password="x", is_staff=True
        )
        coach.user_permissions.set(Permission.objects.all())
        cls.coach_pk = coach.pk

    def _app_list_for(self, user):
        request = RequestFactory().get("/crush-admin/")
        request.user = user
        return crush_admin_site.get_app_list(request)

    @staticmethod
    def _object_names(app_list):
        return {m["object_name"] for app in app_list for m in app["models"]}

    @staticmethod
    def _group_names(app_list):
        return {app["name"] for app in app_list}

    def test_every_menu_worthy_model_survives_the_grouping(self):
        """Our grouped override must surface every model Django's base
        ``get_app_list`` considers menu-worthy.

        The oracle is the un-overridden base implementation: any model it
        returns but our grouping drops is an invisible-model regression. (Models
        registered only for FK navigation — e.g. auth ``User`` — are excluded by
        their own ``has_module_permission`` and so are absent from both sides.)
        """
        from django.contrib.admin import AdminSite

        request = RequestFactory().get("/crush-admin/")
        request.user = self.superuser
        oracle = {
            m["object_name"]
            for app in AdminSite.get_app_list(crush_admin_site, request)
            for m in app["models"]
        }
        shown = self._object_names(self._app_list_for(self.superuser))
        missing = sorted(oracle - shown)
        self.assertEqual(
            missing, [], f"Models dropped from the menu by get_app_list: {missing}"
        )

    def test_crush_connect_group_surfaces_membership(self):
        app_list = self._app_list_for(self.superuser)
        self.assertIn("💞 Crush Connect", self._group_names(app_list))
        self.assertIn("CrushConnectMembership", self._object_names(app_list))

    def test_superuser_only_groups_hidden_from_coach(self):
        # Fresh instance so the permission cache reflects the granted perms.
        coach = User.objects.get(pk=self.coach_pk)
        app_list = self._app_list_for(coach)
        names = self._group_names(app_list)

        # Operational groups (incl. Crush Connect) are visible to coaches.
        self.assertIn("💞 Crush Connect", names)
        # Dev/analytics/audit groups are not.
        for hidden in ("🧬 Matching", "📊 Analytics", "🗒️ Changelog", "🔧 Technical & Debug"):
            self.assertNotIn(hidden, names, f"{hidden} should be superuser-only")

    def test_superuser_sees_the_gated_groups(self):
        names = self._group_names(self._app_list_for(self.superuser))
        for shown in ("🧬 Matching", "📊 Analytics", "🗒️ Changelog", "🔧 Technical & Debug"):
            self.assertIn(shown, names)


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
