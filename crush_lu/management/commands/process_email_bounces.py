"""Inspect Microsoft 365 delivery reports and optionally suppress hard bounces."""

from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from azureproject.email_utils import get_domain_email_config
from azureproject.graph_email_backend import GraphEmailBackend
from crush_lu.models import EmailBounceEvent
from crush_lu.services.email_bounces import is_delivery_report, process_graph_bounce


def _mailbox_addresses(config):
    """Return configured sender mailboxes once, preserving configured order."""
    configured = getattr(settings, "CRUSH_EMAIL_BOUNCE_MAILBOXES", None) or [
        config["DEFAULT_FROM_EMAIL"],
        getattr(settings, "CRUSH_NEWSLETTER_FROM_EMAIL", "love@crush.lu"),
    ]
    addresses = []
    for value in configured:
        address = (parseaddr(value)[1] or value).strip().lower()
        if address and address not in addresses:
            addresses.append(address)
    return addresses


def _message_identity(message):
    identity = message.get("internetMessageId") or message.get("id")
    return str(identity)[:512] if identity else ""


def _mailbox_folder(mailbox, mailbox_count):
    """Resolve mailbox-specific custom folders without reusing Graph IDs."""
    folder_map = getattr(settings, "CRUSH_EMAIL_BOUNCE_FOLDERS", {}) or {}
    folder = folder_map.get(mailbox.lower())
    if folder:
        return folder

    default = getattr(settings, "CRUSH_EMAIL_BOUNCE_FOLDER", "inbox")
    if mailbox_count > 1 and default.lower() != "inbox":
        raise CommandError(
            "Custom Graph folder IDs are mailbox-specific; configure every "
            "CRUSH_EMAIL_BOUNCE_FOLDERS mailbox mapping."
        )
    return default


def _get_mailbox_messages(
    *, mailbox, folder, token, since, limit, excluded_message_ids=None
):
    """Return new verified NDRs, following pages until the cap is satisfied."""
    endpoint = (
        "https://graph.microsoft.com/v1.0/users/"
        f"{quote(mailbox, safe='')}/mailFolders/{quote(folder, safe='')}/messages"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.body-content-type="text"',
    }
    params = {
        "$filter": f"receivedDateTime ge {since}",
        "$orderby": "receivedDateTime desc",
        "$select": (
            "id,internetMessageId,subject,body,receivedDateTime,from,"
            "internetMessageHeaders"
        ),
        "$top": str(min(limit, 100)),
    }
    messages = []
    ignored = 0
    excluded_message_ids = set(excluded_message_ids or ())
    while endpoint and len(messages) < limit:
        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=30,
        )
        if response.status_code != 200:
            raise CommandError(
                f"Graph mailbox read failed for {mailbox} "
                f"({response.status_code}): {response.text[:500]}"
            )
        payload = response.json()
        for message in payload.get("value") or []:
            identity = _message_identity(message)
            if not identity or identity in excluded_message_ids:
                continue
            if not is_delivery_report(message):
                ignored += 1
                continue
            messages.append(message)
            if len(messages) == limit:
                break
        endpoint = payload.get("@odata.nextLink")
        params = None
    return messages, ignored


class Command(BaseCommand):
    help = (
        "Dry-run classification of Crush.lu NDRs; use --apply to persist hard bounces."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        if apply_changes and not getattr(
            settings, "CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED", False
        ):
            raise CommandError(
                "Set CRUSH_EMAIL_BOUNCE_PROCESSING_ENABLED=true before using --apply."
            )
        if not getattr(settings, "CRUSH_EMAIL_BOUNCE_TRUSTED_DOMAINS", None):
            raise CommandError(
                "Set CRUSH_EMAIL_BOUNCE_TRUSTED_DOMAINS to the exact Microsoft "
                "365 tenant NDR sender domain."
            )
        if options["days"] < 1 or not 1 <= options["limit"] <= 500:
            raise CommandError("--days must be positive and --limit must be 1..500")

        config = get_domain_email_config(domain="crush.lu")
        mailboxes = _mailbox_addresses(config)
        folders = {
            mailbox: _mailbox_folder(mailbox, len(mailboxes)) for mailbox in mailboxes
        }
        backend = GraphEmailBackend(
            tenant_id=config["GRAPH_TENANT_ID"],
            client_id=config["GRAPH_CLIENT_ID"],
            client_secret=config["GRAPH_CLIENT_SECRET"],
            from_email=config["DEFAULT_FROM_EMAIL"],
        )
        token = backend.get_access_token()
        since = (timezone.now() - timedelta(days=options["days"])).isoformat()
        processed_ids = set(
            EmailBounceEvent.objects.values_list("source_message_id", flat=True)
        )
        candidates = []
        ignored = 0
        for mailbox in mailboxes:
            mailbox_messages, mailbox_ignored = _get_mailbox_messages(
                mailbox=mailbox,
                folder=folders[mailbox],
                token=token,
                since=since,
                limit=options["limit"],
                excluded_message_ids=processed_ids,
            )
            candidates.extend(mailbox_messages)
            ignored += mailbox_ignored

        candidates.sort(
            key=lambda message: message.get("receivedDateTime") or "", reverse=True
        )
        messages = []
        seen = set()
        for message in candidates:
            identity = _message_identity(message)
            if identity in seen:
                continue
            seen.add(identity)
            messages.append(message)
            if len(messages) == options["limit"]:
                break

        counts = {"hard": 0, "soft": 0, "unknown": 0, "ignored": ignored}
        for message in messages:
            result = process_graph_bounce(message, apply=apply_changes)
            counts[result.classification] += 1

        mode = "APPLIED" if apply_changes else "DRY RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: hard={counts['hard']} soft={counts['soft']} "
                f"unknown={counts['unknown']} ignored={counts['ignored']}"
            )
        )
