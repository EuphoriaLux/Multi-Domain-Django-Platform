"""Views for "Sign in with crush.lu" on test.crush.lu (coach playground).

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md
Logic and safety rules: ``crush_lu/services/playground_sso.py``.

The four routes are language-neutral (``azureproject/urls_crush.py``) so the
staging callback URL can be matched exactly. Each one returns 404 unless its
side of the flow is enabled on this slot.
"""

import json
import logging
import time
from urllib.parse import quote

from django.contrib.auth import login
from django.http import (
    Http404,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from crush_lu.decorators import ratelimit
from crush_lu.services import playground_sso as sso

logger = logging.getLogger(__name__)

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

ERROR_MESSAGES = {
    "no_pending_sign_in": _(
        "This sign-in link has already been used or was started in another "
        "browser. Start again from the login page."
    ),
    "state_mismatch": _(
        "This sign-in link has already been used or was started in another "
        "browser. Start again from the login page."
    ),
    "sign_in_expired": _("The sign-in took too long. Please start again."),
    "invalid_code": _("The sign-in link expired. Please start again."),
    "code_refused": _("The sign-in link expired. Please start again."),
    "issuer_unavailable": _("crush.lu did not answer. Please try again in a minute."),
    "bad_claims": _(
        "crush.lu did not send a usable account. Ask Tom to check your crush.lu "
        "account email."
    ),
    "not_a_coach": _(
        "Your crush.lu account is not an active coach account, so it cannot use "
        "the coach playground on test.crush.lu."
    ),
    "ambiguous_account": _(
        "Several test.crush.lu accounts use your email. Ask Tom to merge them."
    ),
    "unverified_account": _(
        "A test.crush.lu account with your email exists but its email was never "
        "confirmed, so it cannot be linked. Ask Tom to remove it."
    ),
    "account_inactive": _(
        "Your test.crush.lu account is deactivated. Ask Tom to reactivate it."
    ),
    "playground_account": _(
        "Your email belongs to a practice or test account on test.crush.lu. "
        "Ask Tom to check it."
    ),
    "linked_elsewhere": _(
        "Your test.crush.lu account is linked to a different crush.lu account. "
        "Ask Tom to check it."
    ),
    "email_in_use": _(
        "Another test.crush.lu account already uses your crush.lu email. Ask "
        "Tom to check it."
    ),
    "try_again": _("Something went wrong at the same moment. Please try again."),
}


def _error_page(request, code, status):
    logger.info("Coach playground sign-in refused: %s", code)
    message = ERROR_MESSAGES.get(code, ERROR_MESSAGES["try_again"])
    return render(
        request,
        "crush_lu/playground/sso_error.html",
        {"message": message, "error_code": code},
        status=status,
    )


def _no_store(response):
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


# ---------------------------------------------------------------------------
# Issuer: crush.lu (production slot)
# ---------------------------------------------------------------------------


@require_GET
@ratelimit(key="user", rate="20/h", method="GET")
def issuer_authorize(request):
    """Trade a crush.lu session for a single-use code for test.crush.lu."""
    if not sso.issuer_enabled(request):
        raise Http404

    return_url = request.GET.get("return", "")
    state = request.GET.get("state", "")
    challenge = request.GET.get("code_challenge", "")
    if (
        not sso.is_allowed_return_url(return_url)
        or not sso.TOKEN_RE.match(state)
        or not sso.CHALLENGE_RE.match(challenge)
        or request.GET.get("code_challenge_method") != "S256"
    ):
        return HttpResponseBadRequest("Invalid sign-in request.")

    if not request.user.is_authenticated:
        login_url = reverse("account_login")
        next_target = request.get_full_path()
        login_redirect = f"{login_url}?next={quote(next_target, safe='')}"
        if not url_has_allowed_host_and_scheme(
            login_redirect,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return HttpResponseBadRequest("Invalid sign-in request.")
        return _no_store(HttpResponseRedirect(login_redirect))

    # Non-coaches get a code too: it is how staging learns "not a coach any
    # more" and switches that person's staging coach account off. Their claims
    # carry no email or name.
    code = sso.issue_code(request.user, state, challenge)
    return _no_store(
        HttpResponseRedirect(sso.redirect_with_code(return_url, code, state))
    )


@csrf_exempt
@require_POST
@ratelimit(key="ip", rate="30/m", method="POST")
def issuer_exchange(request):
    """Server-to-server: spend a code, answer who it belongs to."""
    if not sso.issuer_enabled(request):
        raise Http404
    if not sso.bearer_ok(request.headers.get("Authorization")):
        return _no_store(JsonResponse({"detail": "unauthorized"}, status=401))
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _no_store(JsonResponse({"detail": "invalid"}, status=400))
    if not isinstance(body, dict):
        return _no_store(JsonResponse({"detail": "invalid"}, status=400))
    try:
        claims = sso.redeem_code(body.get("code"), body.get("code_verifier"))
    except sso.SSOError as exc:
        return _no_store(JsonResponse({"detail": exc.code}, status=exc.status))
    return _no_store(JsonResponse(claims))


# ---------------------------------------------------------------------------
# Consumer: test.crush.lu (staging slot)
# ---------------------------------------------------------------------------


def _safe_next(request, candidate):
    if (
        isinstance(candidate, str)
        and candidate.startswith("/")
        and not candidate.startswith("//")
        and url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return candidate
    return reverse("crush_lu:coach_playground")


@require_GET
def consumer_start(request):
    """Send the browser to crush.lu to sign in."""
    if not sso.consumer_enabled():
        raise Http404
    next_url = _safe_next(request, request.GET.get("next", ""))
    return _no_store(HttpResponseRedirect(sso.begin(request.session, next_url)))


@require_GET
@ratelimit(key="ip", rate="30/10m", method="GET")
def consumer_callback(request):
    """Back from crush.lu: exchange the code, then sign the coach in here."""
    if not sso.consumer_enabled():
        raise Http404
    try:
        pending = sso.take_pending(request.session, request.GET.get("state", ""))
        code = request.GET.get("code", "")
        if not sso.TOKEN_RE.match(code):
            raise sso.SSOError("invalid_code")
        claims = sso.exchange(code, pending["verifier"])
        result = sso.provision_coach(claims, pending["state"])
    except sso.SSOError as exc:
        return _error_page(request, exc.code, exc.status)

    if result.refused:
        return _error_page(request, result.refused, 403)

    login(request, result.user, backend=MODEL_BACKEND)
    # After login(): it flushes the session when the user changes.
    request.session[sso.SIGNED_IN_AT_SESSION_KEY] = int(time.time())
    return _no_store(HttpResponseRedirect(pending["next"]))
