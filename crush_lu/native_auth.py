"""Redeeming a native-app auth code into a WebView session.

The iOS and Android complete endpoints are the same view twice over — they
differ only in which session flag they set and which ``?source=`` they land
on — so the whole of it lives here.

Two things this module exists to get right, both of them lessons from
production:

**A replay is not an attack when the replayer is already the user.** The
redemption step is a GET the WebView *navigates to*, so it sits in WebView
history, in Android's ``setIntent()``-saved launch intent, and on screen when
it fails. Plenty of ordinary things re-run that navigation: a back gesture, a
pull-to-refresh on the error page, or — the long one — an Android activity
recreation, which carries the saved auth intent for the lifetime of the task,
so an automatic dark-theme flip hours after a successful login re-fires a code
that died that morning. Every one of those used to answer a signed-in user
with ``{"success": false, "error": "Invalid or expired authentication code"}``
and no way out. When the session presenting the spent code already belongs to
the user that code was issued for, the code grants nothing that session does
not already hold, so the right answer is the dashboard.

That is also why there is no time limit on that branch. The redirect is not
honouring the code — the code stays spent — it is declining to tear down a
valid session over a duplicate request, and that reasoning does not decay.
The security boundary is the *identity* check, not the clock: an intercepted
code presented from anywhere else arrives unauthenticated, or authenticated as
somebody else, and is refused.

**The three failure causes must be separable.** ``consume()`` used to funnel
"never issued", "already used" and "expired" through one silent ``None``, and
neither view logged anything, so 30 days of telemetry could show that Android
completions failed about half the time but never why. Every outcome here is
logged with its reason.

**What this deliberately does not fix.** Two *genuinely overlapping* deliveries
of the same completion URL both arrive anonymous — the winner's ``Set-Cookie``
has not reached the loser's request yet — so the loser fails the identity check
and gets the failure page even though the login worked. This is not theoretical:
``MainActivity`` has no ``onDestroy``, no ``webView.destroy()`` and no
``stopLoading()``, so an activity recreation starts a second WebView loading the
same ``complete_url`` while the outgoing one still has an uncancelled request in
flight. Serving it would mean
issuing a session to a caller that presented nothing but a spent code, which is
bit-for-bit the state an interceptor is in; the only bindings available on both
requests are the client IP (behind Front Door, the edge's) and the User-Agent
(spoofable, and identical on the same handset). Proof of possession has to come
from the client, which is PKCE. Until then the recovery is the retry link,
which the shells complete in-process. ``reason=replayed_anonymous`` exists to
measure how often this actually happens.

⚠️ If you ever do serve that case, do **not** write to ``request.session`` on
the way: any write marks the session modified, and ``SessionMiddleware`` then
sets a fresh anonymous ``sessionid`` that lands in the same shared cookie jar
milliseconds after the winner's authenticated one and clobbers it — turning a
working login into a broken one. Today's refusal path touches no session key,
which is what keeps it harmless.

See also ``crush_lu/mobile_auth.py``, which handles the *other* half of the
bridge: getting back to the handoff after a login inside the auth sheet.
"""

import logging

from django.conf import settings
from django.contrib.auth import login
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from .mobile_auth import handoff_url
from .models.ios_app import AUTH_CODE_REPLAYED, IOSNativeAuthCode

logger = logging.getLogger(__name__)

# The message is deliberately identical for every refusal. The reason is for
# our logs, not for the caller: telling an attacker "already used" apart from
# "never existed" hands them an oracle, which is why OAuth collapses all of
# these into invalid_grant too.
FAILURE_MESSAGE = "Invalid or expired authentication code"

_PLATFORMS = {
    "ios": {
        "session_flag": "crush_ios_app",
        "source": "ios_app",
        "redirect_uris_setting": "IOS_AUTH_REDIRECT_URIS",
    },
    "android": {
        "session_flag": "crush_android_app",
        "source": "android_app",
        "redirect_uris_setting": "ANDROID_AUTH_REDIRECT_URIS",
    },
}


def _allowed_redirect_uris(platform):
    return list(
        getattr(settings, _PLATFORMS[platform]["redirect_uris_setting"], []) or []
    )


def _retry_redirect_uri(request, platform, record):
    """The redirect_uri to restart the handoff with.

    Getting this wrong strands the user: the local and staging Android builds
    call back on crushlulocal:// and crushlustaging://, so a retry aimed at
    production's crushlu:// either does nothing or wakes the wrong app.

    Prefer the URI this code was issued for. When there is no row to read it
    from — an unknown code — fall back to the one the handoff stamped onto
    complete_url. Both are checked against the allowlist, so neither a stale
    row nor a crafted query can point the retry anywhere but our own callbacks.
    """
    allowed = _allowed_redirect_uris(platform)
    if record is not None and record.redirect_uri in allowed:
        return record.redirect_uri
    requested = request.GET.get("redirect_uri", "")
    if requested in allowed:
        return requested
    return allowed[0] if allowed else ""


def _is_own_replay(request, result):
    """True when the caller is already signed in as the code's own user."""
    return (
        result.reason == AUTH_CODE_REPLAYED
        and request.user.is_authenticated
        and result.record.user_id == request.user.pk
    )


def _failure_response(request, platform, record):
    """One page, one message, whatever the reason.

    The page deliberately does NOT branch on the reason. Showing "timed out"
    for an expired code and "already used" for everything else told a caller
    whether a code had ever existed — the same oracle the uniform JSON body
    above exists to avoid, reintroduced in the half of the response a human
    actually reads. The copy covers both cases instead.
    """
    retry_url = handoff_url(platform, _retry_redirect_uri(request, platform, record))
    # A WebView navigation asks for HTML. Anything else — a real API client,
    # a probe — keeps the JSON body the endpoint has always returned.
    if "text/html" in request.headers.get("Accept", ""):
        return render(
            request,
            "crush_lu/native_auth_failed.html",
            {"retry_url": retry_url},
            status=400,
        )
    return JsonResponse({"success": False, "error": FAILURE_MESSAGE}, status=400)


def complete_native_auth(request, platform, code):
    """Trade a one-time code for a session in the app's WebView."""
    config = _PLATFORMS[platform]
    landing = f"/en/dashboard/?source={config['source']}"
    result = IOSNativeAuthCode.redeem(code)

    if result.user is not None:
        login(request, result.user, backend="django.contrib.auth.backends.ModelBackend")
        request.session[config["session_flag"]] = True
        logger.info(
            "[NATIVE-AUTH] %s complete ok user=%s",
            platform,
            result.user.pk,
        )
        return redirect(landing)

    viewer_id = request.user.pk if request.user.is_authenticated else None
    code_user_id = result.record.user_id if result.record else None

    if _is_own_replay(request, result):
        # Already signed in as this code's owner: the bridge worked, this is
        # the second delivery of the same callback. Send them where the first
        # one would have.
        request.session[config["session_flag"]] = True
        age = timezone.now() - result.record.consumed_at
        logger.info(
            "[NATIVE-AUTH] %s complete replayed by its own session user=%s age=%ss",
            platform,
            viewer_id,
            int(age.total_seconds()),
        )
        return redirect(landing)

    if result.reason == AUTH_CODE_REPLAYED and viewer_id is None:
        # Anonymous, so we cannot tell the two populations apart: this is both
        # what a benign overlapping second delivery looks like (the winner's
        # cookie had not reached this request yet) and what an intercepted
        # callback looks like. Logged distinctly rather than folded into either
        # neighbour, so the interception alarm below stays high-signal and so
        # the frequency of the benign case is answerable from telemetry instead
        # of asserted.
        logger.warning(
            "[NATIVE-AUTH] %s complete refused reason=replayed_anonymous code_user=%s",
            platform,
            code_user_id,
        )
    elif result.reason == AUTH_CODE_REPLAYED and viewer_id != code_user_id:
        # Signed in, but as somebody else, while holding this code. There is no
        # benign reading of that — it is the shape of an intercepted callback
        # (the Android scheme is a private-use one any app can register), so it
        # is worth an alert rather than an info line.
        logger.error(
            "[NATIVE-AUTH] %s spent code presented by another party "
            "code_user=%s viewer=%s",
            platform,
            code_user_id,
            viewer_id,
        )
    else:
        logger.warning(
            "[NATIVE-AUTH] %s complete refused reason=%s code_user=%s viewer=%s",
            platform,
            result.reason,
            code_user_id,
            viewer_id,
        )

    return _failure_response(request, platform, result.record)

