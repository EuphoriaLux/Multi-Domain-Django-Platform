from django.contrib.admin.sites import AdminSite
from django.test import SimpleTestCase

from crush_lu.admin.crush_connect import ConnectPairExclusionAdmin
from crush_lu.models.crush_connect_cycle import ConnectPairExclusion


class ConnectPairExclusionAdminReadonlyTests(SimpleTestCase):
    def setUp(self):
        self.model_admin = ConnectPairExclusionAdmin(
            ConnectPairExclusion, AdminSite()
        )

    def test_reason_is_editable_when_adding(self):
        self.assertNotIn("reason", self.model_admin.get_readonly_fields(None, None))

    def test_reason_is_readonly_for_saved_rows(self):
        # Prevents an admin edit from downgrading a permanent reason to
        # REQUEST_EXPIRED, which exclude_pair() deliberately refuses.
        fields = self.model_admin.get_readonly_fields(None, ConnectPairExclusion())
        self.assertIn("reason", fields)
        self.assertIn("created_at", fields)
