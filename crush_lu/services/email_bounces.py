"""Conservative classification and persistence of Microsoft 365 NDRs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction
from django.utils.dateparse import parse_datetime

from azureproject.email_utils import html_to_plain_text
from crush_lu.models import EmailBounceEvent, EmailSuppression

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
OWN_ADDRESSES = {"love@crush.lu", "noreply@crush.lu", "support@crush.lu"}
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


def is_delivery_report(message: dict) -> bool:
    """Accept messages carrying strong Microsoft 365/DSN evidence only."""
    headers = {
        str(header.get("name", "")).lower(): str(header.get("value", "")).lower()
        for header in message.get("internetMessageHeaders") or []
    }
    content_type = headers.get("content-type", "")
    if "report-type=delivery-status" in content_type:
        return True

    sender = (
        ((message.get("from") or {}).get("emailAddress") or {}).get("address") or ""
    ).lower()
    local_part = sender.split("@", 1)[0]
    if local_part.startswith("microsoftexchange"):
        return True

    return local_part in {"mailer-daemon", "postmaster"} and headers.get(
        "auto-submitted", ""
    ).startswith("auto-")


def classify_bounce(subject: str, body: str) -> BounceClassification:
    """Classify only unambiguous hard/soft failures; ambiguity stays unknown."""
    plain_body = html_to_plain_text(body)
    searchable = f"{subject}\n{plain_body}".lower()
    candidates = {
        match.lower()
        for match in EMAIL_RE.findall(searchable)
        if match.lower() not in OWN_ADDRESSES
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


def process_graph_bounce(message: dict, *, apply: bool = False) -> BounceClassification:
    """Classify a Graph message and optionally persist its deduped result."""
    subject = message.get("subject") or ""
    body = (message.get("body") or {}).get("content") or ""
    result = classify_bounce(subject, body)
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
