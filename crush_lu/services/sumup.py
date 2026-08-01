"""
SumUp REST API client integration for online payments, tokenization, and webhooks.
https://developer.sumup.com/
"""

import logging
import requests
from typing import Any, Dict, Optional
from django.conf import settings

logger = logging.getLogger(__name__)


class SumUpError(Exception):
    """Base exception for SumUp API errors."""
    pass


class SumUpClient:
    """
    Wrapper for SumUp Online Payments REST API.
    Handles Checkouts, Customers (Card Tokenization), and Payment Verification.
    """

    BASE_URL = "https://api.sumup.com"

    def __init__(self, api_key: Optional[str] = None, merchant_code: Optional[str] = None):
        raw_key = api_key or getattr(settings, "SUMUP_API_KEY", "") or ""
        self.api_key = raw_key.strip().strip("'").strip('"')
        raw_code = merchant_code or getattr(settings, "SUMUP_MERCHANT_CODE", "") or ""
        self.merchant_code = raw_code.strip().strip("'").strip('"')

    def _get_headers(self) -> Dict[str, str]:
        if not self.api_key:
            # Fail here rather than sending "Bearer " and letting SumUp answer
            # a generic 401 — that error is indistinguishable from a revoked or
            # mistyped key and sends you hunting in the wrong place.
            raise SumUpError(
                "SUMUP_API_KEY is not configured — set it in the environment "
                "(App Service application settings) and restart."
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def create_checkout(
        self,
        amount: float,
        currency: str,
        checkout_reference: str,
        pay_to_email: Optional[str] = None,
        description: Optional[str] = None,
        return_url: Optional[str] = None,
        customer_id: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Creates a SumUp payment checkout session.
        Endpoint: POST /v0.1/checkouts

        :param amount: Amount to charge (e.g. 15.00)
        :param currency: ISO currency code (e.g. "EUR")
        :param checkout_reference: Unique reference string in our system (e.g. "CRUSH-EVT-123")
        :param pay_to_email: SumUp merchant email (defaults to merchant setup if omitted)
        :param description: Human-readable transaction description
        :param return_url: User redirect URL after completing checkout widget
        :param customer_id: SumUp Customer ID for saved card/tokenization
        :param purpose: Set to "SETUP_RECURRING_PAYMENT" for tokenizing card for subscriptions
        """
        url = f"{self.BASE_URL}/v0.1/checkouts"
        
        payload: Dict[str, Any] = {
            "amount": round(float(amount), 2),
            "currency": currency.upper(),
            "checkout_reference": checkout_reference,
        }

        merchant_code = self.merchant_code or getattr(settings, "SUMUP_MERCHANT_CODE", "")
        if merchant_code:
            payload["merchant_code"] = merchant_code
        elif pay_to_email or getattr(settings, "SUMUP_PAY_TO_EMAIL", None):
            payload["pay_to_email"] = pay_to_email or getattr(settings, "SUMUP_PAY_TO_EMAIL")

        if description:
            payload["description"] = description
        if return_url:
            payload["return_url"] = return_url
        if customer_id:
            payload["customer_id"] = customer_id
        if purpose:
            payload["purpose"] = purpose

        try:
            response = requests.post(url, json=payload, headers=self._get_headers(), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.error("SumUp create_checkout failed: %s", exc, exc_info=True)
            err_msg = str(exc)
            if hasattr(exc, 'response') and exc.response is not None:
                try:
                    err_data = exc.response.json()
                    err_msg = err_data.get("message") or err_data.get("error_message") or str(err_data)
                except Exception:
                    err_msg = exc.response.text or str(exc)
            raise SumUpError(f"SumUp Checkout Creation Error: {err_msg}") from exc

    def get_checkout(self, checkout_id: str) -> Dict[str, Any]:
        """
        Fetches checkout details to verify payment status.
        Endpoint: GET /v0.1/checkouts/{id}
        """
        url = f"{self.BASE_URL}/v0.1/checkouts/{checkout_id}"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.error("SumUp get_checkout failed for ID %s: %s", checkout_id, exc)
            raise SumUpError(f"Failed to fetch SumUp checkout {checkout_id}") from exc

    def create_customer(self, customer_id: str, email: str, name: Optional[str] = None) -> Dict[str, Any]:
        """
        Registers a customer record on SumUp for card tokenization.
        Endpoint: POST /v0.1/customers
        """
        url = f"{self.BASE_URL}/v0.1/customers"
        payload = {
            "customer_id": customer_id,
            "personal_details": {
                "email": email,
            }
        }
        if name:
            payload["personal_details"]["first_name"] = name

        try:
            response = requests.post(url, json=payload, headers=self._get_headers(), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.error("SumUp create_customer failed: %s", exc)
            raise SumUpError(f"Failed to create SumUp customer {customer_id}") from exc

    def charge_recurring(
        self,
        customer_id: str,
        token: str,
        amount: float,
        currency: str,
        checkout_reference: str,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Charges a previously tokenized card for subscription renewal.
        Endpoint: POST /v0.1/checkouts
        """
        url = f"{self.BASE_URL}/v0.1/checkouts"
        payload: Dict[str, Any] = {
            "amount": round(float(amount), 2),
            "currency": currency.upper(),
            "checkout_reference": checkout_reference,
            "customer_id": customer_id,
            "token": token,
        }
        if description:
            payload["description"] = description
        if self.merchant_code:
            payload["merchant_code"] = self.merchant_code

        try:
            response = requests.post(url, json=payload, headers=self._get_headers(), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.error("SumUp charge_recurring failed for customer %s: %s", customer_id, exc)
            raise SumUpError(f"Failed to charge recurring payment for customer {customer_id}") from exc
