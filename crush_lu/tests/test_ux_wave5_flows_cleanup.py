"""UX Wave 5 · WP12a flows-cleanup regression tests (R9, R13, R14, 2-11, 3-06).

Literal paths, not reverse(), per AGENTS.md.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from crush_lu.forms import CrushSignupForm
from crush_lu.tests.test_ux_wave3_pay_confirm import PayConfirmTestBase

User = get_user_model()


class PaidDueEventDetailTests(PayConfirmTestBase):
    """R9: one primary pay action, status box up, no duplicate fact cards."""

    def _html(self):
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)
        response = self.client.get(f"/en/events/{self.event.id}/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_no_pay_now_link_in_status_box(self):
        html = self._html()
        self.assertNotIn("Pay now", html)
        self.assertEqual(html.count('data-payment-method="card"'), 1)

    def test_status_box_precedes_description(self):
        html = self._html()
        self.assertIn('id="event-payment-status"', html)
        self.assertLess(
            html.index('id="event-payment-status"'),
            html.index('id="event-description-text"'),
        )
        self.assertEqual(html.count("Payment due"), 1)

    def test_duplicate_fact_cards_dropped(self):
        html = self._html()
        self.assertNotIn(">Date &amp; Time<", html)
        self.assertNotIn(">Price<", html)

    def test_other_states_keep_fact_cards(self):
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = True
        self.registration.save()
        self.client.force_login(self.user)
        html = self.client.get(f"/en/events/{self.event.id}/").content.decode()
        self.assertIn(">Price<", html)


class SignupWithoutConfirmPasswordTests(TestCase):
    """2-11 (owner decision F): no Confirm password on crush.lu."""

    def setUp(self):
        cache.clear()

    def test_form_has_no_password2(self):
        self.assertNotIn("password2", CrushSignupForm().fields)

    def test_page_has_no_confirm_password_field(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/signup/").content.decode()
        self.assertNotIn('id="id_password2"', html)
        self.assertNotIn("Confirm Password", html)

    def test_signup_succeeds_without_password2(self):
        response = Client(HTTP_HOST="crush.lu").post(
            "/en/signup/",
            {
                "first_name": "Nina",
                "email": "nina-w5@example.com",
                "password1": "Str0ng-pass-2026!",
                "crushlu_consent": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email="nina-w5@example.com").exists())


class PhoneContinueIsSolidTests(TestCase):
    """STYLE.md §2: "Continue" is the solid variant, not the hero gradient."""

    def test_continue_link_uses_solid_variant(self):
        from pathlib import Path

        from django.conf import settings

        html = (
            Path(settings.BASE_DIR)
            / "crush_lu"
            / "templates"
            / "crush_lu"
            / "onboarding"
            / "phone.html"
        ).read_text(encoding="utf-8")
        self.assertIn('class="btn-crush-solid w-full"', html)
        self.assertNotIn("btn-crush-primary", html)
