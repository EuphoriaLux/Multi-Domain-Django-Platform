"""Unsubscribe page must not impersonate the token owner (UXD1-05).

The page is anonymous (the emailed token is the credential). The view used to
pass ``user=email_prefs.user`` in the template context, which beats the auth
context processor, so base.html rendered the owner's logged-in chrome for any
visitor and replaced a different signed-in viewer's identity.

Paths are literal: ``reverse("crush_lu:...")`` builds ``/crush/...`` paths
that 404 under ``HTTP_HOST=crush.lu``.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from crush_lu.models import EmailPreference

User = get_user_model()


class EmailUnsubscribeIdentityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.owner = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="testpass123",
            first_name="Ownerfirst",
            last_name="Ownerlast",
        )
        self.prefs = EmailPreference.get_or_create_for_user(self.owner)
        self.url = f"/en/unsubscribe/{self.prefs.unsubscribe_token}/"

    def test_anonymous_get_greets_owner_but_has_no_member_chrome(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Hi Ownerfirst!", html)
        self.assertNotIn("nav-authenticated", html)
        self.assertNotIn("Ownerlast", html)
        self.assertFalse(response.context["user"].is_authenticated)

    def test_other_logged_in_user_keeps_their_own_identity(self):
        other = User.objects.create_user(
            username="other@example.com",
            email="other@example.com",
            password="testpass123",
            first_name="Otherfirst",
            last_name="Otherlast",
        )
        self.client.force_login(other)

        response = self.client.get(self.url)

        self.assertEqual(response.context["user"].pk, other.pk)
        html = response.content.decode()
        self.assertIn("Otherfirst", html)
        self.assertNotIn("Ownerlast", html)
        # The token owner is still greeted in the page body.
        self.assertIn("Hi Ownerfirst!", html)

    def test_post_unsubscribe_still_works_and_chrome_stays_anonymous(self):
        response = self.client.post(self.url, {"action": "unsubscribe_all"})

        self.assertEqual(response.status_code, 200)
        self.prefs.refresh_from_db()
        self.assertTrue(self.prefs.unsubscribed_all)
        html = response.content.decode()
        self.assertIn("Preferences Updated!", html)
        self.assertNotIn("nav-authenticated", html)
        self.assertNotIn("Ownerlast", html)
        self.assertFalse(response.context["user"].is_authenticated)
