"""Session-flag fallback for the native app auth handoff.

The iOS/Android shells log in through a browser sheet
(ASWebAuthenticationSession / Custom Tab) opened at
``/api/mobile/<platform>/auth/handoff/``. The return trip to the app relies on
the ``?next=`` chain surviving every hop of the login flow; if the user wanders
off the happy path inside the sheet (signup page, language switch, browsing),
``next`` is lost and the sheet strands the user on the website while the app
never receives its ``crushlu://`` callback.

To make the flow loss-proof, the handoff stashes a short-lived session flag
before redirecting to login. ``MultiDomainAccountAdapter.post_login`` and the
OAuth landing page then route ANY completed login back to the handoff while
the flag is fresh, regardless of ``next``.
"""

import time
from urllib.parse import urlencode

SESSION_KEY = "mobile_auth_handoff"

# Matches the OAuthState TTL — long enough for a slow OAuth dance, short
# enough that an abandoned sheet cannot hijack a later website login.
MAX_AGE_SECONDS = 15 * 60


def stash_mobile_handoff(request, platform, redirect_uri):
    """Remember that this browser session is a native-app auth sheet."""
    request.session[SESSION_KEY] = {
        "platform": platform,
        "redirect_uri": redirect_uri,
        "expires": time.time() + MAX_AGE_SECONDS,
    }


def peek_mobile_handoff_url(request):
    """Return the handoff URL to resume, or None if no fresh flag is set.

    Does not consume the flag — the handoff view clears it once it issues
    the one-time code, so repeated redirects stay idempotent.
    """
    if not hasattr(request, "session"):
        return None
    data = request.session.get(SESSION_KEY)
    if not isinstance(data, dict):
        return None
    if time.time() > data.get("expires", 0):
        request.session.pop(SESSION_KEY, None)
        return None
    platform = data.get("platform", "ios")
    query = urlencode({"redirect_uri": data.get("redirect_uri", "")})
    return f"/api/mobile/{platform}/auth/handoff/?{query}"


def clear_mobile_handoff(request):
    if hasattr(request, "session"):
        request.session.pop(SESSION_KEY, None)
