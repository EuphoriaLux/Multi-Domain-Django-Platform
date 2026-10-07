"""Conservative classification and persistence of Microsoft 365 NDRs."""

from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils.dateparse import parse_datetime

from azureproject.email_utils import get_domain_email_config
from crush_lu.models import EmailBounceEvent, EmailSuppression

HARD_RECIPIENT_STATUSES = {"5.1.1", "5.1.10"}
SOFT_STATUSES = {"4.2.2", "5.2.2"}


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


def has_delivery_report_provenance(message: dict) -> bool:
    """Validate the Exchange-authenticated container before fetching its MIME."""
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
    authenticated_internal = auth_as == ["internal"]
    generated_inside_tenant = directionality == ["originating"]
    return bool(trusted_sender and authenticated_internal and generated_inside_tenant)


def _final_recipient(value: str) -> str:
    """Extract and validate the mailbox from an RFC delivery-status field."""
    _address_type, separator, raw_address = value.partition(";")
    candidate = (raw_address if separator else value).strip().strip("<>")
    address = (parseaddr(candidate)[1] or candidate).strip().lower()
    try:
        validate_email(address)
    except ValidationError:
        return ""
    return address


def _delivery_status_records(message: dict) -> list[dict[str, str]]:
    """Parse recipient records from the MIME message/delivery-status part."""
    raw_mime = message.get("_raw_mime") or b""
    if isinstance(raw_mime, str):
        raw_mime = raw_mime.encode("utf-8", errors="replace")
    if not isinstance(raw_mime, bytes):
        return []
    try:
        mime = BytesParser(policy=policy.default).parsebytes(raw_mime)
    except (TypeError, ValueError):
        return []
    if mime.get_content_type() != "multipart/report":
        return []
    if mime.get_param("report-type", header="content-type") != "delivery-status":
        return []

    records = []
    for part in mime.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        payload = part.get_payload()
        if not isinstance(payload, list):
            continue
        for block in payload:
            recipient = _final_recipient(block.get("Final-Recipient", ""))
            action = (block.get("Action", "") or "").strip().lower()
            status = (block.get("Status", "") or "").strip().split(" ", 1)[0]
            if recipient and action and status:
                records.append(
                    {"recipient": recipient, "action": action, "status": status}
                )
    return records


def is_delivery_report(message: dict) -> bool:
    """Accept only trusted NDR containers with structured recipient records."""
    return has_delivery_report_provenance(message) and bool(
        _delivery_status_records(message)
    )


def classify_bounce(message: dict, *, owned_addresses=None) -> BounceClassification:
    """Classify only structured DSN recipient records; ignore display text."""
    owned_addresses = {
        address.lower()
        for address in (
            _configured_owned_addresses()
            if owned_addresses is None
            else owned_addresses
        )
    }
    records = [
        record
        for record in _delivery_status_records(message)
        if record["action"] == "failed" and record["recipient"] not in owned_addresses
    ]
    recipients = {record["recipient"] for record in records}
    recipient = next(iter(recipients)) if len(recipients) == 1 else ""
    statuses = {
        record["status"] for record in records if record["recipient"] == recipient
    }

    if recipient and statuses and statuses <= HARD_RECIPIENT_STATUSES:
        classification = "hard"
    elif (
        recipient
        and statuses
        and all(
            status.startswith("4.") or status in SOFT_STATUSES for status in statuses
        )
    ):
        classification = "soft"
    else:
        classification = "unknown"

    diagnostic = (
        f"classification={classification}; recipient={recipient or 'unresolved'}; "
        f"statuses={','.join(sorted(statuses)) or 'none'}"
    )
    return BounceClassification(classification, recipient, diagnostic)


def _belongs_to_live_account(address: str, *, lock: bool = False) -> bool:
    """True when ``address`` is a current, active member's own email.

    Fail-closed guard for post-erasure bounces: an erased account keeps no
    recognisable address (its User email is tombstoned and its EmailAddress
    rows are deleted), so a late delivery report for it matches nothing here
    and is stored without personal detail instead of resurrecting the address
    and diagnostic that erasure just blanked.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    if not address:
        return False
    users = get_user_model().objects.filter(
        Q(email__iexact=address) | Q(emailaddress__email__iexact=address),
        is_active=True,
    )
    if lock:
        # Row-lock the matching User(s) for the caller's transaction. Erasure
        # deactivates the User with an UPDATE, so it either waits for this
        # transaction (and its sweep then blanks what we wrote) or has already
        # committed (and we see no live account). No check-to-write window.
        users = users.select_for_update(of=("self",))
    return bool(list(users.values_list("pk", flat=True)[:1]))


def process_graph_bounce(
    message: dict, *, apply: bool = False, owned_addresses=None
) -> BounceClassification:
    """Classify a Graph message and optionally persist its deduped result."""
    result = classify_bounce(message, owned_addresses=owned_addresses)
    if not apply:
        return result
    if not is_delivery_report(message):
        raise ValueError("Graph message is not a verified delivery report")

    source_message_id = message.get("internetMessageId") or message.get("id")
    if not source_message_id:
        raise ValueError("Graph message has no stable id")

    with transaction.atomic():
        # Personal detail (address, diagnostic text) is kept only for a live
        # member, decided UNDER a lock held through the writes below. The
        # suppression row still keys on the address so a hard-bounced mailbox
        # is never mailed again (see _anonymize_email_delivery).
        keep_detail = _belongs_to_live_account(result.recipient, lock=True)
        diagnostic = result.diagnostic if keep_detail else ""
        EmailBounceEvent.objects.update_or_create(
            source_message_id=str(source_message_id)[:512],
            defaults={
                "recipient": result.recipient if keep_detail else "",
                "classification": result.classification,
                "subject": "Delivery report",
                "diagnostic": diagnostic,
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
                    "diagnostic": diagnostic,
                },
            )
    return result
