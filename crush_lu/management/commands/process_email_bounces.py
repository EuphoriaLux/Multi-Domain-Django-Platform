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
from crush_lu.services.email_bounces import process_graph_bounce


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
        if options["days"] < 1 or not 1 <= options["limit"] <= 500:
            raise CommandError("--days must be positive and --limit must be 1..500")

        config = get_domain_email_config(domain="crush.lu")
        backend = GraphEmailBackend(
            tenant_id=config["GRAPH_TENANT_ID"],
            client_id=config["GRAPH_CLIENT_ID"],
            client_secret=config["GRAPH_CLIENT_SECRET"],
            from_email=config["DEFAULT_FROM_EMAIL"],
        )
        token = backend.get_access_token()
        since = (timezone.now() - timedelta(days=options["days"])).isoformat()
        mailbox = parseaddr(config["DEFAULT_FROM_EMAIL"])[1] or config[
            "DEFAULT_FROM_EMAIL"
        ]
        endpoint = (
            "https://graph.microsoft.com/v1.0/users/"
            f"{quote(mailbox, safe='')}/mailFolders/deleteditems/messages"
        )
        response = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "$filter": f"receivedDateTime ge {since}",
                "$orderby": "receivedDateTime desc",
                "$select": "id,internetMessageId,subject,body,receivedDateTime",
                "$top": str(options["limit"]),
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise CommandError(
                f"Graph mailbox read failed ({response.status_code}): {response.text[:500]}"
            )

        counts = {"hard": 0, "soft": 0, "unknown": 0}
        for message in response.json().get("value", []):
            result = process_graph_bounce(message, apply=apply_changes)
            counts[result.classification] += 1

        mode = "APPLIED" if apply_changes else "DRY RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: hard={counts['hard']} soft={counts['soft']} "
                f"unknown={counts['unknown']}"
            )
        )
