"""Practice inbox: mail for playground accounts is kept, not sent (staging).

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

``azureproject.email_utils.send_domain_email`` calls :func:`capture_mail` for
any ``@crush.test`` address before it picks a backend. Mail for a practice or
cast member is stored as a ``PlaygroundMail`` row and removed from the
recipient lists, so the coach can read it on the playground page. Every other
address is untouched. Off staging nothing is captured.
"""

import re
from datetime import timedelta
from email.utils import parseaddr

from django.utils import timezone

from crush_lu.services.staging_playground import playground_enabled

PLAYGROUND_DOMAIN = "@crush.test"
KEEP_PER_MEMBER = 50
KEEP_DAYS = 14
_LINK_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def normalized_address(address):
    return (parseaddr(address)[1] or address or "").strip().lower()


def mentions_playground_domain(addresses):
    """Cheap string check first, so production never touches the database."""
    return any(
        normalized_address(address).endswith(PLAYGROUND_DOMAIN)
        for address in addresses
        if address
    )


def capture_mail(*, subject, body_text, body_html, from_address, addresses):
    """Store a copy for each playground recipient; return their addresses."""
    from django.contrib.auth import get_user_model

    from crush_lu.models import PlaygroundAccount, PlaygroundMail

    if not playground_enabled():
        return set()
    wanted = {
        normalized_address(address)
        for address in addresses
        if address and normalized_address(address).endswith(PLAYGROUND_DOMAIN)
    }
    if not wanted:
        return set()
    users = get_user_model().objects.filter(
        email__in=wanted,
        playground_account__kind__in=(
            PlaygroundAccount.KIND_PRACTICE,
            PlaygroundAccount.KIND_CAST,
        ),
    )
    captured = set()
    for user in users:
        address = user.email.lower()
        PlaygroundMail.objects.create(
            recipient=user,
            to_address=address,
            from_address=(from_address or "")[:254],
            subject=(subject or "")[:255],
            body_text=body_text or "",
            body_html=body_html or "",
        )
        _prune(user)
        captured.add(address)
    return captured


def _prune(user):
    from crush_lu.models import PlaygroundMail

    mails = PlaygroundMail.objects.filter(recipient=user)
    mails.filter(created_at__lt=timezone.now() - timedelta(days=KEEP_DAYS)).delete()
    keep = list(
        mails.order_by("-created_at", "-pk").values_list("pk", flat=True)[
            :KEEP_PER_MEMBER
        ]
    )
    mails.exclude(pk__in=keep).delete()


def recent_mail(user, limit=20):
    """Newest mail for one member, with the links found in the text body."""
    from crush_lu.models import PlaygroundMail

    mails = list(PlaygroundMail.objects.filter(recipient=user)[:limit])
    for mail in mails:
        mail.links = list(dict.fromkeys(_LINK_RE.findall(mail.body_text or "")))[:8]
    return mails
