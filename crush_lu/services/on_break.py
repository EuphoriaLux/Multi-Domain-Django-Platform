"""Coach-facing warnings about members who are taking a break.

8-13 product answer: inviting an on-break member to a private/VIP event is
allowed, but the coach is told (warn, not block).
"""

from django.contrib import messages
from django.db.models import Q
from django.utils.translation import gettext as _

from crush_lu.models import CrushProfile


def warn_if_inviting_on_break(request, *, emails=(), users=()):
    """Add one non-blocking warning naming invitees who are on a break.

    ``emails`` match members case-insensitively; ``users`` are User objects.
    Returns the names warned about (empty when nobody is on a break).
    """
    match = Q(user__in=[u.pk for u in users])
    for email in {e.strip() for e in emails if e and e.strip()}:
        match |= Q(user__email__iexact=email)
    names = [
        p.user.get_full_name() or p.user.email
        for p in CrushProfile.objects.filter(match, on_break_at__isnull=False)
        .select_related("user")
        .order_by("user__email")
    ]
    if names:
        messages.warning(
            request,
            _(
                "Heads-up: these members are taking a break from Crush.lu "
                "right now: %(names)s. You can still invite them."
            )
            % {"names": ", ".join(names)},
        )
    return names
