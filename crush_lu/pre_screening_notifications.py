"""Email + push helpers for the pre-screening questionnaire (Phase 5).

These functions are callable directly (e.g. from a management command driven
by an Azure Function timer trigger — see ``azure-functions/contact-sync`` for
the sibling pattern) and from the tasks in ``crush_lu.tasks``.

Scheduling is deliberately NOT wired in this module: the repo has no Celery
beat. The intended deployment pattern is a dedicated Azure Function timer
that POSTs to an admin endpoint or invokes the
``send_pre_screening_invites`` management command on a cron schedule.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from django.utils.translation import gettext as _
from django.utils.translation import override

from .email_helpers import (
    can_send_email,
    get_email_context_with_unsubscribe,
    send_domain_email,
)
from .models import ProfileSubmission
from .utils.i18n import get_user_preferred_language

logger = logging.getLogger(__name__)


# Cache keys used to deduplicate at-most-once sends.
_INVITE_SENT_KEY = "pre_screening_invite_sent:{submission_id}"
_REMINDER_SENT_KEY = "pre_screening_reminder_sent:{submission_id}"
_PUSH_SENT_KEY = "pre_screening_push_sent:{submission_id}"


def _already_sent(cache_key: str) -> bool:
    return bool(cache.get(cache_key))


def _mark_sent(cache_key: str, ttl_seconds: int = 60 * 60 * 24 * 30) -> None:
    cache.set(cache_key, 1, ttl_seconds)


def _pre_screening_url(request=None) -> str:
    """Build the absolute URL for the pre-screening form.

    Uses ``reverse()`` so the language prefix from the current translation
    context is baked into the path — urls_crush serves user pages under
    ``i18n_patterns(..., prefix_default_language=True)``, so the unprefixed
    ``/pre-screening/`` would 404 in the browser. Callers typically wrap this
    in ``translation.override(lang)`` to match the user's preferred language.
    """
    from django.urls import reverse

    path = reverse("crush_lu:pre_screening")
    if request is not None:
        return request.build_absolute_uri(path)
    return "https://crush.lu" + path


class _FakeRequest:
    """Minimal request stand-in used when the sender has no HTTP context
    (management command / background task). Mirrors the ``FakeRequest``
    pattern in ``crush_lu.tasks``.
    """
    def __init__(self, host="crush.lu", secure=True):
        self._host = host
        self._secure = secure
        self.META = {"HTTP_HOST": host, "SERVER_PORT": "443" if secure else "80"}

    def get_host(self):
        return self._host

    def is_secure(self):
        return self._secure

    def get_port(self):
        return "443" if self._secure else "80"

    def build_absolute_uri(self, path):
        proto = "https" if self._secure else "http"
        return f"{proto}://{self._host}{path}"


def send_pre_screening_invite_email(submission: ProfileSubmission,
                                    *, reminder: bool = False,
                                    request=None) -> bool:
    """Send the invite (or reminder) email for a single submission.

    Returns True on send, False on skip (already submitted, unsubscribed, etc.).
    Idempotent via cache keys — safe to invoke from retries.
    """
    if submission.pre_screening_submitted_at is not None:
        return False
    if submission.status != "pending":
        return False
    if submission.review_call_completed or submission.is_paused:
        return False
    user = submission.profile.user
    if not can_send_email(user, "profile_updates"):
        return False

    cache_key = (_REMINDER_SENT_KEY if reminder else _INVITE_SENT_KEY).format(
        submission_id=submission.id
    )
    if _already_sent(cache_key):
        return False

    # All downstream email helpers expect a request-like object for URL
    # generation; supply a stand-in when we're called from a cron context.
    if request is None:
        request = _FakeRequest()

    lang = get_user_preferred_language(user=user, request=request, default="en")
    context = get_email_context_with_unsubscribe(
        user,
        request,
        reminder=reminder,
        pre_screening_url=_pre_screening_url(request),
    )
    # `get_email_context_with_unsubscribe` may already set `user`; only seed
    # if absent.
    context.setdefault("user", user)

    with override(lang):
        subject = (
            _("Your Coach is getting ready — 3 minutes, 13 questions")
            if reminder
            else _("Help your Coach prepare — 3 minutes, 13 questions")
        )
        html_message = render_to_string(
            "crush_lu/emails/pre_screening_invite.html", context
        )
        plain_message = render_to_string(
            "crush_lu/emails/pre_screening_invite.txt", context
        ) or strip_tags(html_message)

    sent = send_domain_email(
        subject=subject,
        message=plain_message,
        html_message=html_message,
        recipient_list=[user.email],
        request=request,
        fail_silently=False,
    )
    if sent:
        _mark_sent(cache_key)
        logger.info(
            "pre_screening.invite_sent",
            extra={"submission_id": submission.id, "reminder": reminder},
        )
    return bool(sent)


def send_pre_screening_user_push(submission: ProfileSubmission) -> bool:
    """Push notification nudging the user to open the pre-screening form."""
    if submission.pre_screening_submitted_at is not None:
        return False
    if submission.status != "pending":
        return False
    if submission.review_call_completed or submission.is_paused:
        return False

    cache_key = _PUSH_SENT_KEY.format(submission_id=submission.id)
    if _already_sent(cache_key):
        return False

    try:
        from django.urls import reverse
        from . import push_notifications

        user = submission.profile.user
        lang = get_user_preferred_language(user=user, default="en")
        with override(lang):
            title = str(_("Meet your Coach better"))
            body = str(_("Answer 13 quick questions before your call"))
            url = reverse("crush_lu:pre_screening")
        result = push_notifications.send_push_notification(
            user=user, title=title, body=body, url=url,
            tag=f"pre-screening-invite-{submission.id}",
        )
        success = bool((result or {}).get("success"))
        if success:
            _mark_sent(cache_key)
            logger.info(
                "pre_screening.user_push_sent",
                extra={"submission_id": submission.id},
            )
        return success
    except Exception as exc:
        logger.exception(
            "pre_screening.user_push_failed",
            extra={"submission_id": submission.id, "error": str(exc)},
        )
        return False


def candidates_for_invite(now=None) -> list[ProfileSubmission]:
    """Submissions that have been pending for >= 1h with no pre-screening yet."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=1)
    return list(
        ProfileSubmission.objects.filter(
            status="pending",
            pre_screening_submitted_at__isnull=True,
            submitted_at__lte=cutoff,
        )
        .select_related("profile__user")
    )


def candidates_for_reminder(now=None) -> list[ProfileSubmission]:
    """Submissions >= 24h old with still no pre-screening — send one reminder."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=24)
    return list(
        ProfileSubmission.objects.filter(
            status="pending",
            pre_screening_submitted_at__isnull=True,
            submitted_at__lte=cutoff,
        )
        .select_related("profile__user")
    )


def candidates_for_push(now=None) -> list[ProfileSubmission]:
    """Submissions >= 4h old with no pre-screening — one push attempt."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=4)
    return list(
        ProfileSubmission.objects.filter(
            status="pending",
            pre_screening_submitted_at__isnull=True,
            submitted_at__lte=cutoff,
        )
        .select_related("profile__user")
    )
