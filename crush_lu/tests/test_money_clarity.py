"""UX Wave 2 · WP5 — what happens to the money, said before it happens.

* The cancel page previews the outcome of the member's own cancellation using
  the very decision ``issue_cancellation_credits`` makes (4-06).
* The SumUp card page names the purchase, links back, and states the same
  cancellation policy (4-13).

Run with: pytest crush_lu/tests/test_money_clarity.py -n 0
"""

import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.payments import PaymentTransaction
from crush_lu.services.credits import CancellationOutcome, cancellation_outcome
from crush_lu.tests.test_crush_credit import FEE_CENTS, CreditFixture


def _cancel_url(event):
    return f"/en/events/{event.pk}/cancel/"


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CancellationPreviewMatchesPolicyTests(CreditFixture):
    """The preview and the real cancellation are one decision."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def _preview_then_cancel(self, registration):
        preview = cancellation_outcome(registration, moment=timezone.now())
        self._cancel(registration.user, registration.event)
        issued = sum(
            c.amount_cents
            for c in CrushCredit.objects.filter(source_registration=registration)
        )
        return preview, issued

    def test_early_cancellation_previews_the_credit_it_issues(self):
        preview, issued = self._preview_then_cancel(self.registration)

        self.assertEqual(preview.kind, CancellationOutcome.CREDIT)
        self.assertEqual(preview.amount_cents, FEE_CENTS)
        self.assertEqual(issued, preview.amount_cents)

    def test_late_cancellation_previews_no_refund_and_issues_none(self):
        event = self._event(hours_away=10, max_participants=5)
        registration = self._paid_registration(event, self._user("late@crush.lu"))

        preview, issued = self._preview_then_cancel(registration)

        self.assertEqual(preview.kind, CancellationOutcome.LATE)
        self.assertEqual(preview.resale_share_cents, FEE_CENTS // 2)
        self.assertEqual(issued, 0)

    def test_unpaid_cancellation_previews_nothing_and_issues_none(self):
        event = self._event(hours_away=100, max_participants=5)
        registration = self._registration(
            event, self._user("unpaid@crush.lu"), status="confirmed"
        )

        preview, issued = self._preview_then_cancel(registration)

        self.assertEqual(preview.kind, CancellationOutcome.NOTHING_PAID)
        self.assertEqual(issued, 0)

    def test_paid_amount_not_current_fee_is_previewed(self):
        """The fee is admin-editable; the member is owed what they paid."""
        event = self._event(hours_away=100, max_participants=5)
        registration = self._paid_registration(
            event, self._user("paid-less@crush.lu"), amount=Decimal("12.00")
        )
        event.registration_fee = Decimal("20.00")
        event.save()

        preview, issued = self._preview_then_cancel(registration)

        self.assertEqual(preview.amount_cents, 1200)
        self.assertEqual(issued, 1200)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CancelPageShowsOutcomeTests(CreditFixture):
    def setUp(self):
        super().setUp()
        cache.clear()

    def _get(self, user, event, path_lang="en"):
        self.client.force_login(user)
        return self.client.get(_cancel_url(event).replace("/en/", f"/{path_lang}/", 1))

    def test_credit_outcome_is_shown_before_the_confirm_button(self):
        response = self._get(self.user, self.event)
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-outcome="credit"')
        self.assertContains(response, "15.50 EUR back as Crush Credit")
        self.assertContains(response, 'data-testid="cancellation-policy-note"')
        self.assertLess(
            html.index('data-testid="cancellation-outcome"'),
            html.index("Yes, Cancel Registration"),
        )

    def test_late_outcome_says_no_refund_and_waitlist(self):
        event = self._event(hours_away=10, max_participants=5)
        user = self._user("late-page@crush.lu")
        self._paid_registration(event, user)

        response = self._get(user, event)

        self.assertContains(response, 'data-outcome="late"')
        self.assertContains(response, "No refund")
        self.assertContains(response, "waitlist")
        self.assertContains(response, "7.75 EUR back as Crush Credit")

    def test_unpaid_outcome_says_nothing_is_due(self):
        event = self._event(hours_away=100, max_participants=5)
        user = self._user("unpaid-page@crush.lu")
        self._registration(event, user, status="confirmed")

        response = self._get(user, event)

        self.assertContains(response, 'data-outcome="nothing_paid"')
        self.assertContains(response, "No payment was recorded")

    def test_amount_is_locale_formatted(self):
        response = self._get(self.user, self.event, path_lang="fr")

        self.assertContains(response, "15,50 EUR")

    def test_buttons_and_csp_safe_submit_guard(self):
        response = self._get(self.user, self.event)
        html = response.content.decode()

        self.assertNotIn("onsubmit=", html)
        self.assertContains(response, 'x-data="eventCancelForm"')
        self.assertRegex(html, r'type="submit"[^>]*class="btn-danger')
        # "Keep my seat" goes back to the event, not the dashboard.
        self.assertRegex(
            html,
            rf'href="/en/events/{self.event.pk}/"[^>]*class="btn-crush-outline',
        )
        self.assertContains(response, "<title>Cancel Registration - Crush.lu")

    def test_submit_guard_component_is_registered(self):
        source = Path(
            settings.BASE_DIR, "crush_lu/static/crush_lu/js/alpine-components.js"
        ).read_text(encoding="utf-8")
        match = re.search(
            r'Alpine\.data\("eventCancelForm".*?\n    \}\);', source, re.S
        )
        self.assertIsNotNone(match)
        self.assertIn("mixin(makeConfirm(", match.group(0))


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class SumUpWidgetOrderSummaryTests(CreditFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.event.address_town = "Differdange"
        self.event.save()
        self.pending = self._registration(
            self._event(hours_away=100, max_participants=5, address_town="Esch"),
            self._user("buyer@crush.lu"),
        )
        self.tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-widget-summary",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_SUMMARY",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.pending.user,
            event_registration=self.pending,
        )

    def _get(self, **headers):
        self.client.force_login(self.pending.user)
        return self.client.get("/payments/sumup/widget/CHK_SUMMARY/", **headers)

    def test_order_summary_names_the_purchase(self):
        response = self._get()
        event = self.pending.event

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="order-summary"')
        self.assertContains(response, event.title)
        self.assertContains(response, "Esch")
        self.assertContains(response, "15.50 EUR")
        self.assertContains(response, 'data-testid="cancellation-policy-note"')

    def test_back_to_event_link(self):
        response = self._get()

        self.assertContains(response, f'href="/en/events/{self.pending.event.pk}/"')
        self.assertContains(response, "Back to event")

    def test_amount_is_locale_formatted(self):
        response = self._get(HTTP_ACCEPT_LANGUAGE="fr")

        self.assertContains(response, "15,50 EUR")
        self.assertContains(response, "Retour à l’événement")

    def test_uses_crush_tokens_not_rose(self):
        response = self._get()

        self.assertNotIn("rose-", response.content.decode())

    def test_non_event_purchase_has_no_event_summary(self):
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-DON-widget-summary",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_DONATION",
            amount=Decimal("5.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.DONATION,
            user=self.pending.user,
        )
        self.client.force_login(self.pending.user)
        response = self.client.get(f"/payments/sumup/widget/{tx.sumup_checkout_id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="order-summary"')
        self.assertNotContains(response, "Back to event")
        self.assertNotContains(response, 'data-testid="cancellation-policy-note"')
