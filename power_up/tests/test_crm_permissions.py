from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from power_up.admin import power_up_admin_site


class PowerUpCRMAccessTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="power-up.lu")
        self.staff_group, _ = Group.objects.get_or_create(name="power_up_staff")

    def make_user(self, username, *, is_staff=False, group=False):
        user = get_user_model().objects.create_user(
            username=username, password="test-password", is_staff=is_staff
        )
        if group:
            user.groups.add(self.staff_group)
        return user

    def test_power_up_staff_can_access_crm_view(self):
        user = self.make_user("powerup-staff", is_staff=True, group=True)
        self.client.force_login(user)

        response = self.client.get("/crm/")

        self.assertEqual(response.status_code, 200)

    def test_crushcoach_staff_without_power_up_group_is_rejected(self):
        user = self.make_user("crush-coach", is_staff=True)
        self.client.force_login(user)

        response = self.client.get("/crm/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assertFalse(power_up_admin_site.has_permission(SimpleNamespace(user=user)))

    def test_power_up_admin_requires_power_up_group(self):
        user = self.make_user("powerup-admin", is_staff=True, group=True)
        request = SimpleNamespace(user=user)

        self.assertTrue(power_up_admin_site.has_permission(request))
