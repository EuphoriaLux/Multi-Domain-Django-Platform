"""Resuming the native-app auth handoff after a login inside the auth sheet.

The iOS/Android shells log in through a browser sheet
(ASWebAuthenticationSession / Custom Tab) opened at
``/api/mobile/<platform>/auth/handoff/``. The return trip to the app relies on
the ``?next=`` chain surviving every hop of the login flow; if the user wanders
off the happy path inside the sheet (signup page, language switch, browsing),
``next`` is lost and the sheet strands the user on the website while the app
never receives its ``crushlu://`` callback.

Two mechanisms carry the handoff through, in order of preference:

1. **The OAuth state** (``crush_lu.oauth_statekit``). When a provider login
   starts inside the sheet without a ``next``, the handoff URL is injected into
   the stashed state. That state is database-backed, so it survives the
   cross-browser and replayed-callback paths where the session does not, and it
   is bound to one specific OAuth flow rather than to the browser.

2. **A session flag** (this module), stashed by the handoff before it bounces
   to the login page. This is the fallback for logins with no OAuth state of
   their own — chiefly email/password logins inside the sheet.

The session flag is deliberately weak-tied: on iOS the sheet shares Safari's
cookie jar (``prefersEphemeralWebBrowserSession = false``), so the server
cannot tell "in the sheet" from "in Safari". An abandoned sheet therefore
leaves a flag behind that a later ordinary website login would otherwise pick
up, bouncing that user into the app. Two rules bound that blast radius:

* the flag only *fills in* a missing redirect — an explicit ``next`` always
  wins (see ``MultiDomainAccountAdapter.post_login``), and
* it is consumed the first time it is applied, so one stale flag can affect at
  most one login.
"""

import time
from urllib.parse import urlencode

SESSION_KEY = "mobile_auth_handoff"

# Long enough for a real login inside the sheet (including a slow provider
# dance), short enough that an abandoned sheet stops mattering quickly. See
# the module docstring for why this cannot be tightened by binding instead.
MAX_AGE_SECONDS = 10 * 60

_HANDOFF_PREFIX = "/api/mobile/"
_HANDOFF_SUFFIX_PATH = "/auth/handoff/"


def stash_mobile_handoff(request, platform, redirect_uri):
    """Remember that this browser session is a native-app auth sheet."""
    request.session[SESSION_KEY] = {
        "platform": platform,
        "redirect_uri": redirect_uri,
        "expires": time.time() + MAX_AGE_SECONDS,
    }


def _handoff_url(data):
    platform = data.get("platform", "ios")
    query = urlencode({"redirect_uri": data.get("redirect_uri", "")})
    return f"{_HANDOFF_PREFIX}{platform}{_HANDOFF_SUFFIX_PATH}?{query}"


def peek_mobile_handoff_url(request):
    """Return the handoff URL to resume, or None if no fresh flag is set.

    Does not consume the flag — used where the caller may run more than once
    for the same flow (e.g. the OAuth landing page).
    """
    if not hasattr(request, "session"):
        return None
    data = request.session.get(SESSION_KEY)
    if not isinstance(data, dict):
        return None
    if time.time() > data.get("expires", 0):
        request.session.pop(SESSION_KEY, None)
        return None
    return _handoff_url(data)


def consume_mobile_handoff_url(request):
    """Return the handoff URL to resume and clear the flag.

    Used by ``post_login``: applying a stale flag at most once keeps an
    abandoned auth sheet from repeatedly hijacking later website logins.
    """
    url = peek_mobile_handoff_url(request)
    if url:
        clear_mobile_handoff(request)
    return url


def is_mobile_handoff_path(url):
    """True if ``url`` is one of our own relative handoff endpoints.

    Guards the OAuth-state path: only a URL this module could have produced is
    ever followed, so a crafted ``next`` cannot ride this route.
    """
    if not isinstance(url, str) or not url.startswith(_HANDOFF_PREFIX):
        return False
    path = url.split("?", 1)[0]
    if not path.endswith(_HANDOFF_SUFFIX_PATH):
        return False
    platform = path[len(_HANDOFF_PREFIX) : -len(_HANDOFF_SUFFIX_PATH)]
    return platform in ("ios", "android")


def clear_mobile_handoff(request):
    if hasattr(request, "session"):
        request.session.pop(SESSION_KEY, None)
