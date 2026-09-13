"""Conservative classification and persistence of Microsoft 365 NDRs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.utils.dateparse import parse_datetime

from azureproject.email_utils import html_to_plain_text
from crush_lu.models import EmailBounceEvent, EmailSuppression

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
OWN_ADDRESSES = {"noreply@crush.lu", "support@crush.lu"}
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


def classify_bounce(subject: str, body: str) -> BounceClassification:
    """Classify only unambiguous hard/soft failures; ambiguity stays unknown."""
    diagnostic = html_to_plain_text(body)
    searchable = f"{subject}\n{diagnostic}".lower()
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

    return BounceClassification(classification, recipient, diagnostic[:4000])


def process_graph_bounce(message: dict, *, apply: bool = False) -> BounceClassification:
    """Classify a Graph message and optionally persist its deduped result."""
    subject = message.get("subject") or ""
    body = (message.get("body") or {}).get("content") or ""
    result = classify_bounce(subject, body)
    if not apply:
        return result

    source_message_id = message.get("internetMessageId") or message.get("id")
    if not source_message_id:
        raise ValueError("Graph message has no stable id")

    event, created = EmailBounceEvent.objects.get_or_create(
        source_message_id=source_message_id,
        defaults={
            "recipient": result.recipient,
            "classification": result.classification,
            "subject": subject[:998],
            "diagnostic": result.diagnostic,
            "received_at": parse_datetime(message.get("receivedDateTime") or ""),
        },
    )
    if created and result.classification == "hard":
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
