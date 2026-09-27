"""UX Wave 2 · WP5 — what happens to the money, said before it happens.

* The cancel page previews the outcome of the member's own cancellation using
  the very decision ``issue_cancellation_credits`` makes (4-06).
* The SumUp card page names the purchase, links back, and states the same
  cancellation policy (4-13).

Run with: pytest crush_lu/tests/test_money_clarity.py -n 0
"""

import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import EventRegistration
from crush_lu.models.payments import PaymentTransaction
from crush_lu.services.credits import (
    CancellationOutcome,
    cancellation_outcome,
    issue_credit,
)
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

    def test_late_outcome_says_no_refund_and_seat_released(self):
        event = self._event(hours_away=10, max_participants=5)
        user = self._user("late-page@crush.lu")
        self._paid_registration(event, user)

        response = self._get(user, event)

        self.assertContains(response, 'data-outcome="late"')
        self.assertContains(response, "No refund")
        # Promotion only runs when the event accepts it; never promise a waitlist.
        self.assertContains(response, "Your seat is released for someone else")
        self.assertNotContains(response, "waitlist")
        self.assertContains(response, "7.75 EUR back as Crush Credit")

    def test_late_legacy_payment_promises_no_resale_share(self):
        """A fee-fallback payment carries no resale claim, so none is promised."""
        event = self._event(hours_away=10, max_participants=5)
        user = self._user("legacy-late@crush.lu")
        EventRegistration.objects.create(
            event=event,
            user=user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )

        response = self._get(user, event)

        self.assertContains(response, 'data-outcome="late"')
        self.assertContains(response, "No refund")
        self.assertContains(response, "Your seat is released for someone else.")
        self.assertNotContains(response, "back as Crush Credit")

    def _credit_paid_seat(self, event, user, *, redeemed=True):
        """A seat paid with Crush Credit; ``redeemed=False`` is a legacy CREDIT
        payment whose payload records no redemptions."""
        registration = EventRegistration.objects.create(
            event=event,
            user=user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )
        raw_response = {"paid_with": "crush_credit"}
        if redeemed:
            credit = issue_credit(user, FEE_CENTS, CrushCredit.Reason.GOODWILL)
            raw_response["redemptions"] = [
                {"credit_id": credit.pk, "amount_cents": FEE_CENTS}
            ]
        PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-{registration.pk}-credit",
            provider=PaymentTransaction.Provider.CREDIT,
            amount=event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=user,
            event_registration=registration,
            raw_response=raw_response,
        )
        return registration

    def test_credit_funded_seat_names_the_original_expiry(self):
        """Restored tranches keep their clocks; never promise they are all usable."""
        event = self._event(hours_away=100, max_participants=5)
        user = self._user("credit-paid@crush.lu")
        self._credit_paid_seat(event, user)

        response = self._get(user, event)

        self.assertContains(response, 'data-outcome="credit"')
        self.assertContains(response, "15.50 EUR back as Crush Credit")
        self.assertContains(response, "original expiry dates")
        self.assertNotContains(response, "ready to use on any Crush.lu event")

    def test_credit_funded_seat_copy_is_translated(self):
        event = self._event(hours_away=100, max_participants=5)
        user = self._user("credit-paid-de@crush.lu")
        self._credit_paid_seat(event, user)

        self.assertContains(
            self._get(user, event, path_lang="de"), "ursprünglichen Ablaufdaten"
        )
        self.assertContains(
            self._get(user, event, path_lang="fr"), "dates d’expiration d’origine"
        )

    def test_legacy_credit_payment_without_redemptions_promises_fresh_credit(self):
        """Without recorded tranches the issuer mints one fresh credit, so the
        preview must not talk about original expiry dates."""
        event = self._event(hours_away=100, max_participants=5)
        user = self._user("legacy-credit@crush.lu")
        registration = self._credit_paid_seat(event, user, redeemed=False)

        self.assertFalse(cancellation_outcome(registration).restores_credit)
        response = self._get(user, event)

        self.assertContains(response, 'data-outcome="credit"')
        self.assertContains(response, "ready to use on any Crush.lu event")
        self.assertNotContains(response, "original expiry dates")

    def test_organiser_cancelled_event_shows_no_member_preview(self):
        """The organiser remedy is owed, not the member one the preview would show."""
        self.event.is_cancelled = True
        self.event.save()

        response = self._get(self.user, self.event)

        self.assertRedirects(
            response, f"/en/events/{self.event.pk}/", fetch_redirect_response=False
        )

    def test_started_event_shows_no_preview(self):
        event = self._event(hours_away=-1, max_participants=5)
        user = self._user("started@crush.lu")
        self._paid_registration(event, user)

        response = self._get(user, event)

        self.assertRedirects(
            response, f"/en/events/{event.pk}/", fetch_redirect_response=False
        )

    def test_already_cancelled_registration_shows_no_preview(self):
        event = self._event(hours_away=10, max_participants=5)
        user = self._user("again@crush.lu")
        self._registration(event, user, status="cancelled")

        response = self._get(user, event)

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(f"/events/{event.pk}/cancel", response.url)

    @override_settings(CRUSH_CREDIT_RESALE_SHARE_PERCENT=30)
    def test_policy_note_share_follows_the_setting(self):
        response = self._get(self.user, self.event)

        self.assertContains(response, "you get 30% back as Crush Credit")
        self.assertNotContains(response, "you get 50% back")

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
class CancelConfirmsThePreviewedOutcomeTests(CreditFixture):
    """A stale preview never turns into a different money outcome silently."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def test_outcome_changed_since_preview_asks_again(self):
        # The page was opened while full credit applied; the deadline has
        # passed by the time the member confirms.
        event = self._event(hours_away=10, max_participants=5)
        user = self._user("stale-preview@crush.lu")
        registration = self._paid_registration(event, user)
        self.client.force_login(user)

        response = self.client.post(_cancel_url(event), {"previewed_outcome": "credit"})

        self.assertRedirects(
            response, _cancel_url(event), fetch_redirect_response=False
        )
        registration.refresh_from_db()
        self.assertNotEqual(registration.status, "cancelled")
        self.assertFalse(
            CrushCredit.objects.filter(source_registration=registration).exists()
        )

    def test_matching_preview_cancels(self):
        self.client.force_login(self.user)

        response = self.client.post(
            _cancel_url(self.event), {"previewed_outcome": "credit"}
        )

        self.assertRedirects(response, "/en/dashboard/", fetch_redirect_response=False)
        self.assertTrue(
            CrushCredit.objects.filter(source_registration__event=self.event).exists()
        )

    def test_page_carries_the_previewed_outcome(self):
        self.client.force_login(self.user)

        response = self.client.get(_cancel_url(self.event))

        self.assertContains(
            response, '<input type="hidden" name="previewed_outcome" value="credit">'
        )


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

    def test_late_checkout_note_names_no_past_deadline(self):
        """Inside the late window the full-credit deadline has already passed."""
        late = self._registration(
            self._event(hours_away=10, max_participants=5),
            self._user("late-buyer@crush.lu"),
        )
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-widget-late",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_LATE",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=late.user,
            event_registration=late,
        )
        self.client.force_login(late.user)
        response = self.client.get("/payments/sumup/widget/CHK_LATE/")

        self.assertContains(response, 'data-testid="cancellation-policy-note"')
        self.assertContains(response, "The event starts in less than 48 hours")
        self.assertNotContains(response, "Cancel before")

    def test_german_date_reads_um_not_bei(self):
        response = self._get(HTTP_ACCEPT_LANGUAGE="de")
        summary = re.search(
            r'data-testid="order-summary".*?</p>\s*</div>',
            response.content.decode(),
            re.S,
        ).group(0)

        self.assertIn(" um ", summary)
        self.assertNotIn(" bei ", summary)

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

    def test_organiser_cancelled_event_quotes_no_member_terms(self):
        """A capture after an organiser cancellation gets the organiser remedy."""
        event = self.pending.event
        event.is_cancelled = True
        event.save()

        response = self._get()

        self.assertContains(response, 'data-testid="order-summary"')
        self.assertNotContains(response, 'data-testid="cancellation-policy-note"')

    def test_already_cancelled_registration_quotes_no_member_terms(self):
        """A capture after the member cancelled is settled at ``cancelled_at``.

        Today's deadline would not be the one applied, so quote none.
        """
        self.pending.status = "cancelled"
        self.pending.save()

        response = self._get()

        self.assertContains(response, 'data-testid="order-summary"')
        self.assertNotContains(response, 'data-testid="cancellation-policy-note"')

    def test_started_event_quotes_no_member_terms(self):
        """Member cancellation closes at the start, so a checkout opened later
        must not quote a cancellation policy the member can no longer use."""
        event = self.pending.event
        event.date_time = timezone.now() - timedelta(hours=1)
        event.save()

        response = self._get()

        self.assertContains(response, 'data-testid="order-summary"')
        self.assertNotContains(response, 'data-testid="cancellation-policy-note"')
