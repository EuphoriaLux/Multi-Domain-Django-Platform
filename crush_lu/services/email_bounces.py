"""Conservative classification and persistence of Microsoft 365 NDRs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr

from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_datetime

from azureproject.email_utils import get_domain_email_config, html_to_plain_text
from crush_lu.models import EmailBounceEvent, EmailSuppression

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
HARD_MARKERS = (
    "5.1.1",
    "5.1.10",
    "address not found",
    "recipient address rejected",
    "unknown recipient",
    "unknown to address",
    "no such user",
    "does not exist",
    "wasn't found",
    "was not found",
)
SOFT_MARKERS = (
    "4.2.2",
    "5.2.2",
    "mailbox full",
    "quota exceeded",
    "temporarily unavailable",
    "temporary failure",
    "timed out",
    "didn't respond",
    "did not respond",
)


@dataclass(frozen=True)
class BounceClassification:
    classification: str
    recipient: str
    diagnostic: str


def _headers(message: dict) -> dict[str, list[str]]:
    """Normalize Graph headers without discarding duplicate values."""
    result: dict[str, list[str]] = {}
    for header in message.get("internetMessageHeaders") or []:
        name = str(header.get("name", "")).strip().lower()
        if name:
            result.setdefault(name, []).append(str(header.get("value", "")).lower())
    return result


def _configured_owned_addresses() -> set[str]:
    """Return every configured Crush sender/reply mailbox."""
    config = get_domain_email_config(domain="crush.lu")
    values = [
        config.get("DEFAULT_FROM_EMAIL", ""),
        config.get("REPLY_TO_EMAIL", ""),
        getattr(settings, "CRUSH_NEWSLETTER_FROM_EMAIL", ""),
        *(getattr(settings, "CRUSH_EMAIL_BOUNCE_MAILBOXES", ()) or ()),
    ]
    return {
        address.lower()
        for value in values
        if (address := (parseaddr(value)[1] or value).strip())
    }


def is_delivery_report(message: dict) -> bool:
    """Accept only tenant-authenticated Microsoft Exchange delivery reports."""
    headers = _headers(message)

    sender = (
        ((message.get("from") or {}).get("emailAddress") or {}).get("address") or ""
    ).lower()
    local_part, separator, sender_domain = sender.rpartition("@")
    trusted_domains = {
        str(domain).strip().lower()
        for domain in (
            getattr(settings, "CRUSH_EMAIL_BOUNCE_TRUSTED_DOMAINS", ()) or ()
        )
    }
    trusted_sender = (
        bool(separator)
        and local_part.startswith("microsoftexchange")
        and sender_domain in trusted_domains
    )
    auth_as = headers.get("x-ms-exchange-organization-authas", ())
    directionality = headers.get("x-ms-exchange-organization-messagedirectionality", ())
    content_types = headers.get("content-type", ())
    authenticated_internal = auth_as == ["internal"]
    generated_inside_tenant = directionality == ["originating"]
    structured_report = len(content_types) == 1 and (
        "report-type=delivery-status" in content_types[0]
    )
    return (
        trusted_sender
        and authenticated_internal
        and generated_inside_tenant
        and structured_report
    )


def classify_bounce(
    subject: str, body: str, *, owned_addresses=None
) -> BounceClassification:
    """Classify only unambiguous hard/soft failures; ambiguity stays unknown."""
    plain_body = html_to_plain_text(body)
    searchable = f"{subject}\n{plain_body}".lower()
    owned_addresses = {
        address.lower()
        for address in (
            _configured_owned_addresses()
            if owned_addresses is None
            else owned_addresses
        )
    }
    candidates = {
        match.lower()
        for match in EMAIL_RE.findall(searchable)
        if match.lower() not in owned_addresses
    }
    recipient = next(iter(candidates)) if len(candidates) == 1 else ""

    if any(marker in searchable for marker in SOFT_MARKERS):
        classification = "soft"
    elif recipient and any(marker in searchable for marker in HARD_MARKERS):
        classification = "hard"
    else:
        classification = "unknown"

    indicators = sorted(
        {marker for marker in (*HARD_MARKERS, *SOFT_MARKERS) if marker in searchable}
    )
    diagnostic = (
        f"classification={classification}; recipient={recipient or 'unresolved'}; "
        f"indicators={','.join(indicators) or 'none'}"
    )
    return BounceClassification(classification, recipient, diagnostic)


def process_graph_bounce(
    message: dict, *, apply: bool = False, owned_addresses=None
) -> BounceClassification:
    """Classify a Graph message and optionally persist its deduped result."""
    subject = message.get("subject") or ""
    body = (message.get("body") or {}).get("content") or ""
    result = classify_bounce(subject, body, owned_addresses=owned_addresses)
    if not apply:
        return result
    if not is_delivery_report(message):
        raise ValueError("Graph message is not a verified delivery report")

    source_message_id = message.get("internetMessageId") or message.get("id")
    if not source_message_id:
        raise ValueError("Graph message has no stable id")

    with transaction.atomic():
        EmailBounceEvent.objects.update_or_create(
            source_message_id=str(source_message_id)[:512],
            defaults={
                "recipient": result.recipient,
                "classification": result.classification,
                "subject": "Delivery report",
                "diagnostic": result.diagnostic,
                "received_at": parse_datetime(message.get("receivedDateTime") or ""),
            },
        )
        if result.classification == "hard":
            EmailSuppression.objects.update_or_create(
                email=result.recipient,
                defaults={
                    "is_active": True,
                    "reason": "hard_bounce",
                    "source": "graph_ndr",
                    "diagnostic": result.diagnostic,
                },
            )
    return result
