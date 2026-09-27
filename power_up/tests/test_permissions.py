from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.test import TestCase

from power_up.permissions import POWER_UP_STAFF_GROUP, is_power_up_staff


class IsPowerUpStaffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name=POWER_UP_STAFF_GROUP)
        User = get_user_model()
        cls.member = User.objects.create_user(username="power-up-member")
        cls.ordinary_staff = User.objects.create_user(
            username="unrelated-staff", is_staff=True
        )
        cls.superuser = User.objects.create_superuser(
            username="superuser", email="superuser@example.com", password="test"
        )

    def test_group_member_is_power_up_staff(self):
        self.member.groups.add(self.group)

        self.assertTrue(is_power_up_staff(self.member))

    def test_staff_flag_without_group_is_not_enough(self):
        self.assertFalse(is_power_up_staff(self.ordinary_staff))

    def test_superuser_keeps_access(self):
        self.assertTrue(is_power_up_staff(self.superuser))

    def test_anonymous_user_is_not_power_up_staff(self):
        self.assertFalse(is_power_up_staff(AnonymousUser()))
