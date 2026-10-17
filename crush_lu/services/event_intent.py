"""Remember which event a visitor was looking at when they signed up (audit A11).

A logged-out visitor who clicks "Sign up" on an event page used to lose that
event: signup, mandatory email verification and onboarding all end on fixed
pages. This keeps the event's ID (never a URL, so nothing user-supplied can
become a redirect target) and shows a "continue your registration" banner until
the member registers, dismisses it, or the event stops taking sign-ups.

Where it lives:

- ``session[SESSION_KEY]`` from the moment ``/signup/?event=<id>`` is opened.
  It survives the normal social-login round trip (Lax cookie, DB sessions).
- the cache, keyed by user, from a successful email signup, because the
  verification link is often opened in another browser. ``user_logged_in``
  copies it back into that browser's session (:func:`restore_on_login`).

Known gaps: the OAuth recovery path that rebuilds a fresh session
(``oauth_statekit`` DB fallback) and the native-app sign-in sheet do not carry
it. The banner links to the event page rather than registering anyone: that
page already re-checks publication, invitation, eligibility, capacity and the
deadline and offers the right next step.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)

SESSION_KEY = "pending_event_intent"
MAX_AGE = timedelta(days=7)


def _cache_key(user_id):
    return f"event_intent:{user_id}"


def _accepting_event(event_id):
    from crush_lu.models import MeetupEvent

    try:
        event_id = int(event_id)
    except (TypeError, ValueError):
        return None
    event = MeetupEvent.objects.filter(pk=event_id, is_published=True).first()
    if event is None or not event.is_registration_accepting:
        return None
    return event


def remember_from_request(request):
    """Store ``?event=<id>`` from the signup page when that event is open."""
    event = _accepting_event(request.GET.get("event"))
    if event is None:
        return None
    request.session[SESSION_KEY] = {
        "event_id": event.pk,
        "at": timezone.now().isoformat(),
    }
    return event


def persist_for_user(request, user):
    """Copy the session's intent to the cache for ``user`` (signup success)."""
    intent = request.session.get(SESSION_KEY)
    if intent:
        cache.set(_cache_key(user.pk), intent, int(MAX_AGE.total_seconds()))


def restore_on_login(request, user):
    """Bring a cached intent into this browser's session, once."""
    key = _cache_key(user.pk)
    intent = cache.get(key)
    if intent is None:
        return
    cache.delete(key)
    if SESSION_KEY not in request.session:
        request.session[SESSION_KEY] = intent


def forget(request):
    request.session.pop(SESSION_KEY, None)


def current(request):
    """``{"event": MeetupEvent, "open": bool}`` for the banner, or None.

    No query unless the session holds an intent. A stale intent, an event the
    member already registered for, or one that no longer exists is dropped.
    A closed event is returned once with ``open=False`` (so the banner can say
    why) and then dropped.
    """
    intent = request.session.get(SESSION_KEY)
    if not intent:
        return None
    from crush_lu.models import EventRegistration, MeetupEvent

    at = parse_datetime(intent.get("at") or "")
    if at is None or timezone.now() - at > MAX_AGE:
        forget(request)
        return None
    event = MeetupEvent.objects.filter(
        pk=intent.get("event_id"), is_published=True
    ).first()
    if event is None:
        forget(request)
        return None
    if (
        request.user.is_authenticated
        and EventRegistration.objects.filter(event=event, user=request.user)
        .exclude(status="cancelled")
        .exists()
    ):
        forget(request)
        return None
    is_open = event.is_registration_accepting
    if not is_open:
        forget(request)
    return {"event": event, "open": is_open}
