"""Mollie Payments API client (donations).

https://docs.mollie.com/reference/create-payment

Plain ``requests``, like the SumUp client next door, rather than the
mollie-api-python package: two endpoints do not justify a dependency. The
member pays on Mollie's hosted checkout, which offers whatever methods the
Mollie account has enabled (cards, Apple Pay, pay by bank, ...).

Mollie's payment webhook carries only the payment id and is not signed, so a
status is never taken from a request: every caller re-reads the payment here.
"""

import logging
import re
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

from crush_lu.services.sumup import clean_credential

logger = logging.getLogger(__name__)

API_BASE = "https://api.mollie.com/v2"
# Connect and read timeouts. A request blocks the member's checkout click or
# Mollie's webhook delivery, so it must fail fast rather than hang a worker.
TIMEOUT = (3, 10)

PAYMENT_ID_RE = re.compile(r"^tr_[A-Za-z0-9]+$")
CUSTOMER_ID_RE = re.compile(r"^cst_[A-Za-z0-9]+$")
MANDATE_ID_RE = re.compile(r"^mdt_[A-Za-z0-9]+$")

# Recurring payments (Premium): a "first" payment made by the member creates a
# mandate, which later "recurring" payments charge with no checkout.
# https://docs.mollie.com/docs/recurring-payments
SEQUENCE_FIRST = "first"

STATUS_PAID = "paid"
# Final and unpaid: this payment will never be paid, so the row can close.
# open, pending and authorized can still turn into paid.
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"
STATUS_EXPIRED = "expired"
UNPAID_FINAL_STATUSES = (STATUS_FAILED, STATUS_CANCELED, STATUS_EXPIRED)

# Refunds past the point of no return. queued and pending can still be canceled
# (Mollie sends no webhook when they are), and a failed one puts the money back
# on the account. https://docs.mollie.com/docs/refunds
REFUND_STATUSES_RETURNING_MONEY = ("processing", "refunded")

# Mollie has no Luxembourg locales; these are the closest of its own.
_LOCALES = {"en": "en_US", "de": "de_DE", "fr": "fr_FR"}


class MollieError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def mollie_api_key():
    return clean_credential(getattr(settings, "MOLLIE_API_KEY", ""))


def mollie_configured():
    """True when donations should go through Mollie.

    Mollie keys always start test_ or live_; anything else is a paste error
    that would only surface as a 401 on the member's click.
    """
    return mollie_api_key().startswith(("test_", "live_"))


def mollie_locale(language_code):
    return _LOCALES.get((language_code or "")[:2].lower())


def checkout_url(payment):
    """The hosted checkout to send the member to, or None."""
    try:
        href = payment["_links"]["checkout"]["href"]
    except (KeyError, TypeError):
        return None
    return href if isinstance(href, str) and href.startswith("https://") else None


def amount_matches(payment, amount, currency):
    """Whether Mollie's payment is for exactly the amount the row recorded."""
    reported = payment.get("amount") or {}
    try:
        value = Decimal(str(reported.get("value")))
    except InvalidOperation:
        return False
    return reported.get("currency") == currency and value == amount


def _amount_value(amount):
    try:
        return Decimal(str((amount or {}).get("value")))
    except InvalidOperation:
        return Decimal("0")


def money_returned(payment):
    """``(refunded, charged_back)`` so far, from a ``get_payment`` response.

    A refund or chargeback leaves the payment ``paid``, so this is the only
    signal. It adds up the embedded objects rather than reading
    ``amountRefunded`` and ``amountChargedBack``, because the docs do not say
    which refund statuses those totals include, or whether a reversed chargeback
    leaves them. Both lists are simply absent from a payment read without
    ``embed``, which counts as nothing returned.
    """
    embedded = payment.get("_embedded") or {}
    refunded = sum(
        (
            _amount_value(refund.get("amount"))
            for refund in embedded.get("refunds") or []
            if refund.get("status") in REFUND_STATUSES_RETURNING_MONEY
        ),
        Decimal("0"),
    )
    charged_back = sum(
        (
            _amount_value(chargeback.get("amount"))
            for chargeback in embedded.get("chargebacks") or []
            if not chargeback.get("reversedAt")
        ),
        Decimal("0"),
    )
    return refunded, charged_back


def mandate_id_of(payment):
    """The mandate a first payment created, or ""."""
    mandate_id = payment.get("mandateId") or ""
    return mandate_id if MANDATE_ID_RE.match(str(mandate_id)) else ""


def customer_id_of(payment):
    customer_id = payment.get("customerId") or ""
    return customer_id if CUSTOMER_ID_RE.match(str(customer_id)) else ""


class MollieClient:
    def __init__(self, api_key=None):
        self.api_key = clean_credential(
            mollie_api_key() if api_key is None else api_key
        )
        if not self.api_key:
            raise MollieError("MOLLIE_API_KEY is not configured")

    def _request(self, method, path, payload=None):
        try:
            response = requests.request(
                method,
                f"{API_BASE}{path}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept": "application/json",
                },
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise MollieError(f"Mollie request failed: {type(exc).__name__}") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            # Mollie's error body: {"status", "title", "detail", "field"}.
            detail = data.get("detail") or data.get("title") or ""
            field = data.get("field")
            raise MollieError(
                f"Mollie API {response.status_code}: {detail}"
                + (f" (field: {field})" if field else ""),
                status=response.status_code,
            )
        if not isinstance(data, dict):
            raise MollieError("Mollie returned an unexpected response body")
        return data

    def create_payment(
        self,
        *,
        amount,
        description,
        redirect_url,
        webhook_url=None,
        metadata=None,
        locale=None,
        customer_id=None,
        sequence_type=None,
        method=None,
    ):
        """Open a payment and return Mollie's payment object.

        ``amount`` is a Decimal already quantized to the cent; Mollie wants it
        as a string with exactly two decimals. ``customer_id`` with
        ``sequence_type="first"`` asks Mollie to create a mandate from this
        payment; ``method`` limits the checkout to one payment method.
        """
        if customer_id is not None and not CUSTOMER_ID_RE.match(customer_id):
            raise MollieError("Not a Mollie customer id")
        payload = {
            "amount": {"currency": "EUR", "value": f"{amount:.2f}"},
            "description": description[:255],
            "redirectUrl": redirect_url,
        }
        if webhook_url:
            payload["webhookUrl"] = webhook_url
        if metadata:
            payload["metadata"] = metadata
        if locale:
            payload["locale"] = locale
        if customer_id:
            payload["customerId"] = customer_id
        if sequence_type:
            payload["sequenceType"] = sequence_type
        if method:
            payload["method"] = method
        return self._request("POST", "/payments", payload)

    def create_customer(self, *, name=None, email=None, metadata=None):
        """Create the Mollie customer a mandate is attached to.

        Name and email are optional at Mollie; callers leave them out unless
        they have a reason to share them.
        https://docs.mollie.com/reference/create-customer
        """
        payload = {}
        if name:
            payload["name"] = name[:255]
        if email:
            payload["email"] = email
        if metadata:
            payload["metadata"] = metadata
        return self._request("POST", "/customers", payload)

    def list_mandates(self, customer_id):
        """The customer's mandates, newest first as Mollie returns them.

        https://docs.mollie.com/reference/list-mandates
        """
        if not CUSTOMER_ID_RE.match(customer_id or ""):
            raise MollieError("Not a Mollie customer id")
        data = self._request("GET", f"/customers/{customer_id}/mandates")
        mandates = (data.get("_embedded") or {}).get("mandates") or []
        return [mandate for mandate in mandates if isinstance(mandate, dict)]

    def cancel_payment(self, payment_id):
        """Cancel a payment that is still open, so it can never be paid.

        Mollie refuses (422) once a payment is no longer cancelable, which the
        caller treats as "do not open a second one".
        https://docs.mollie.com/reference/cancel-payment
        """
        if not PAYMENT_ID_RE.match(payment_id or ""):
            raise MollieError("Not a Mollie payment id")
        return self._request("DELETE", f"/payments/{payment_id}")

    def ensure_not_payable(self, payment_id):
        """True only when the payment is proven unable to take money.

        Final unpaid statuses count, and an open one is cancelled. A paid one
        (or any error) is False: the caller must not treat the seat as free.
        """
        try:
            status = self.get_payment(payment_id).get("status")
            if status in UNPAID_FINAL_STATUSES:
                return True
            if status in ("open", "pending", "authorized"):
                self.cancel_payment(payment_id)
                return True
        except MollieError:
            return False
        return False

    def get_payment(self, payment_id):
        if not PAYMENT_ID_RE.match(payment_id or ""):
            raise MollieError("Not a Mollie payment id")
        return self._request("GET", f"/payments/{payment_id}?embed=refunds,chargebacks")
