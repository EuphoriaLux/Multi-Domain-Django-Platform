"""
User-facing peer-safety views: block, unblock, and report another member.

These power the Trust & Safety affordances on the cards that show one member to
another (Sparks received, connection cards). Blocking is enforced symmetrically
by ``services.blocking`` and is silent — the blocked user is never told. Filing a
report drops a record into the admin moderation queue (``UserReportAdmin``).
"""

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .decorators import crush_login_required, ratelimit
from .models import UserBlock, UserReport

logger = logging.getLogger(__name__)
User = get_user_model()


def _back(request, default="crush_lu:crush_connect_sparks_received"):
    """Redirect target after an action — honour a same-host ?next= / referer.

    Only same-host targets are followed; an off-host or malformed value falls
    back to ``default`` (open-redirect guard — same sanitiser the SPA auth flow
    uses, ``azureproject/views_spa_auth.py``).
    """
    candidate = request.POST.get("next") or request.META.get("HTTP_REFERER")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(candidate)
    return redirect(default)


@crush_login_required
@ratelimit(key="user", rate="30/h", method="POST")
@require_POST
def block_user(request, user_id: int):
    """Block another member (symmetric, silent). Idempotent."""
    if user_id == request.user.id:
        messages.error(request, _("You can't block yourself."))
        return _back(request)

    target = get_object_or_404(User, pk=user_id)
    reason = request.POST.get("reason", "")
    valid = {c for c, _label in UserBlock.REASON_CHOICES}
    UserBlock.objects.get_or_create(
        blocker=request.user,
        blocked=target,
        defaults={"reason": reason if reason in valid else ""},
    )
    messages.success(
        request,
        _("You've blocked this member. They won't be able to reach you, and "
          "you won't see each other again."),
    )
    return _back(request)


@crush_login_required
@ratelimit(key="user", rate="30/h", method="POST")
@require_POST
def unblock_user(request, user_id: int):
    """Remove a block the current user previously made."""
    UserBlock.objects.filter(blocker=request.user, blocked_id=user_id).delete()
    messages.success(request, _("Member unblocked."))
    return _back(request, default="crush_lu:blocked_members")


@crush_login_required
@ratelimit(key="user", rate="20/h", method="POST")
@require_POST
def report_user(request, user_id: int):
    """File a report about another member into the moderation queue.

    Optionally also blocks them in the same action (the report form offers a
    "block too" checkbox) — reporting harassment without blocking is rarely
    what a user wants.
    """
    if user_id == request.user.id:
        messages.error(request, _("You can't report yourself."))
        return _back(request)

    target = get_object_or_404(User, pk=user_id)
    reason = request.POST.get("reason", "other")
    valid = {c for c, _label in UserReport.REASON_CHOICES}
    report = UserReport.objects.create(
        reporter=request.user,
        reported_user=target,
        reason=reason if reason in valid else "other",
        details=(request.POST.get("details", "") or "").strip()[:2000],
        source=request.POST.get("source", ""),
        source_id=request.POST.get("source_id") or None,
    )

    # Notify staff/coaches best-effort — must never block the report flow.
    try:
        from .notification_service import notify_report_filed

        notify_report_filed(report)
    except Exception:  # pragma: no cover - notification must never break reporting
        logger.exception("Report-filed notification failed for report %s", report.pk)

    if request.POST.get("also_block"):
        UserBlock.objects.get_or_create(blocker=request.user, blocked=target)

    messages.success(
        request,
        _("Thanks — our team will review this report. You won't hear back "
          "unless we need more detail."),
    )
    return _back(request)


@crush_login_required
def blocked_members(request):
    """List the members the current user has blocked, with an unblock control."""
    blocks = (
        UserBlock.objects.filter(blocker=request.user)
        .select_related("blocked__crushprofile")
        .order_by("-created_at")
    )
    return render(
        request, "crush_lu/moderation/blocked_members.html", {"blocks": blocks}
    )
