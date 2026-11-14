"""Sign in to test.crush.lu with a crush.lu account (coach playground).

Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md

Modelled on the hub.crush.lu bridge (``azureproject/views_spa_auth.py``), with
one difference: staging cannot read production's cache (another Redis DB) or
database, so the code is exchanged server to server.

1. test.crush.lu ``start`` keeps a random ``state`` and a PKCE verifier in the
   session and sends the browser to crush.lu ``authorize``.
2. crush.lu ``authorize`` (the user signs in there if needed) keeps a 60 s
   single-use code in its cache, bound to ``state`` and the PKCE challenge,
   and sends the browser back to the one allowed staging callback URL.
3. test.crush.lu ``callback`` posts ``code`` + verifier to crush.lu
   ``exchange`` with a dedicated shared secret. crush.lu answers with who the
   person is and whether they are an active coach.
4. test.crush.lu creates or refreshes the staging coach account and signs it in.

Rules that keep this safe on production:

* The issuer answers only on the production slot (the playground gate is off),
  only on the host ``crush.lu``, and only with a secret of 32+ characters.
  The consumer answers only on staging.
* The secret is ``COACH_PLAYGROUND_SSO_SECRET``, never ``SECRET_KEY``.
* No email or name ever travels in a URL; only the opaque code and state do.
* Logs carry ids and outcome codes, never an email, a code or a state.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from crush_lu.services.staging_playground import playground_enabled

logger = logging.getLogger(__name__)

AUTHORIZE_PATH = "/api/auth/coach-playground/authorize/"
EXCHANGE_PATH = "/api/auth/coach-playground/exchange/"
ISSUER_HOST = "crush.lu"
AUDIENCE = "test.crush.lu"

CODE_CACHE_PREFIX = "coach_playground_sso:"
CODE_TTL_SECONDS = 60

#: Session key holding the pending sign-in on staging (state, verifier, next).
PENDING_SESSION_KEY = "coach_playground_sso"
#: Session key holding when this staging session last signed in with crush.lu.
SIGNED_IN_AT_SESSION_KEY = "coach_playground_sso_at"
PENDING_MAX_AGE_SECONDS = 10 * 60
#: A coach removed on crush.lu loses the playground within this window.
FRESHNESS_SECONDS = 7 * 24 * 3600

MIN_SECRET_LENGTH = 32
EXCHANGE_TIMEOUT = (3.05, 5)

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
SUB_RE = re.compile(r"^[1-9][0-9]{0,18}$")

ROLE_COACH = "coach"
ROLE_SUPERUSER = "superuser"
ROLE_NONE = "none"


class SSOError(Exception):
    """A sign-in step failed; ``code`` is safe to log and to show."""

    def __init__(self, code, status=400):
        super().__init__(code)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Configuration and gates
# ---------------------------------------------------------------------------


def _secret():
    return getattr(settings, "COACH_PLAYGROUND_SSO_SECRET", "") or ""


def sso_configured():
    return len(_secret()) >= MIN_SECRET_LENGTH


def issuer_base():
    return (getattr(settings, "COACH_PLAYGROUND_SSO_ISSUER", "") or "").rstrip("/")


def callback_url():
    return getattr(settings, "COACH_PLAYGROUND_SSO_CALLBACK_URL", "")


def issuer_enabled(request):
    """crush.lu (production slot) hands out codes; never on staging or DEBUG."""
    host = request.get_host().split(":")[0].lower()
    return sso_configured() and not playground_enabled() and host == ISSUER_HOST


def consumer_enabled():
    """test.crush.lu (staging slot) accepts crush.lu sign-ins."""
    return (
        playground_enabled()
        and sso_configured()
        and issuer_base().startswith("https://")
    )


def is_allowed_return_url(url):
    """Exact (scheme, host, path) match, never a prefix: no open redirect."""
    if not isinstance(url, str) or not url:
        return False
    parsed = urlparse(url)
    if parsed.params or parsed.fragment or parsed.username or parsed.password:
        return False
    allowed = getattr(settings, "COACH_PLAYGROUND_SSO_RETURN_URLS", set())
    return (parsed.scheme, parsed.netloc, parsed.path) in allowed


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _code_key(code):
    return CODE_CACHE_PREFIX + hashlib.sha256(code.encode("ascii")).hexdigest()


#: JWT claim carrying the crush.lu sign-in time of a linked staging coach.
JWT_CLAIM = "pg_sso_at"


def is_linked_coach(user):
    """True for a staging coach account linked to crush.lu."""
    from crush_lu.models import PlaygroundAccount

    if user is None or not getattr(user, "pk", None):
        return False
    return PlaygroundAccount.objects.filter(
        user_id=user.pk, kind=PlaygroundAccount.KIND_COACH
    ).exists()


def freshness_applies(user):
    """The 7-day crush.lu recheck applies to this account on this slot."""
    return consumer_enabled() and is_linked_coach(user)


def timestamp_fresh(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return 0 <= time.time() - value <= FRESHNESS_SECONDS


def signed_in_recently(session):
    """True when the playground may trust this session's coach.

    Applies only once sign-in with crush.lu is configured on staging. Before
    that (and locally under DEBUG), an ordinary staging coach login is enough.
    """
    if not consumer_enabled():
        return True
    return timestamp_fresh(session.get(SIGNED_IN_AT_SESSION_KEY))


# ---------------------------------------------------------------------------
# Issuer (crush.lu, production slot)
# ---------------------------------------------------------------------------


def issue_code(user, state, challenge):
    """Keep a single-use code for ``user`` for 60 s and return it."""
    code = secrets.token_urlsafe(32)
    cache.set(
        _code_key(code),
        {"user_id": user.pk, "state": state, "challenge": challenge},
        timeout=CODE_TTL_SECONDS,
    )
    return code


def redirect_with_code(return_url, code, state):
    """Merge ``code``/``state`` into the return URL, dropping any stale ones."""
    parts = urlparse(return_url)
    preserved = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in ("code", "state")
    ]
    query = urlencode(preserved + [("code", code), ("state", state)])
    return urlunparse(parts._replace(query=query))


def issuer_role(user):
    """``coach``, ``superuser`` or ``none`` for a crush.lu user, checked now."""
    from crush_lu.models import CrushCoach, UserDataConsent

    if user is None or not user.is_active:
        return ROLE_NONE
    if UserDataConsent.objects.filter(user=user, crushlu_banned=True).exists():
        return ROLE_NONE
    if user.is_superuser:
        return ROLE_SUPERUSER
    if CrushCoach.objects.filter(user=user, is_active=True).exists():
        return ROLE_COACH
    return ROLE_NONE


def redeem_code(code, verifier):
    """Spend a code once and return the claims for staging.

    Raises ``SSOError`` (401) for an unknown, spent or PKCE-mismatched code.
    A mismatch still burns the code.
    """
    if not isinstance(code, str) or not TOKEN_RE.match(code):
        raise SSOError("invalid_code", 401)
    if not isinstance(verifier, str) or not TOKEN_RE.match(verifier):
        raise SSOError("invalid_code", 401)
    key = _code_key(code)
    payload = cache.get(key)
    if not isinstance(payload, dict):
        raise SSOError("invalid_code", 401)
    # Atomic single-use claim, as in ExchangeCodeView: only the first wins.
    if not cache.add(f"{key}:consumed", True, timeout=CODE_TTL_SECONDS):
        raise SSOError("invalid_code", 401)
    cache.delete(key)
    if not hmac.compare_digest(pkce_challenge(verifier), payload.get("challenge", "")):
        raise SSOError("invalid_code", 401)

    User = get_user_model()
    user = User.objects.filter(pk=payload.get("user_id")).first()
    role = issuer_role(user)
    claims = {
        "sub": str(payload.get("user_id")),
        "role": role,
        "state": payload.get("state", ""),
        "aud": AUDIENCE,
        "iat": int(time.time()),
    }
    if role != ROLE_NONE:
        from allauth.account.models import EmailAddress

        # Staging matches existing accounts by this email, so it is only
        # vouched for when crush.lu itself has it verified.
        email_verified = bool(user.email) and (
            EmailAddress.objects.filter(
                user=user, email__iexact=user.email, verified=True
            ).exists()
        )
        claims.update(
            {
                "email": user.email,
                "email_verified": email_verified,
                "given_name": user.first_name,
                "family_name": user.last_name,
            }
        )
    logger.info(
        "Coach playground sign-in code redeemed for user %s (role %s)",
        claims["sub"],
        role,
    )
    return claims


def bearer_ok(header_value):
    if not sso_configured() or not isinstance(header_value, str):
        return False
    if not header_value.startswith("Bearer "):
        return False
    return hmac.compare_digest(
        header_value[len("Bearer ") :].encode("utf-8"), _secret().encode("utf-8")
    )


# ---------------------------------------------------------------------------
# Consumer (test.crush.lu, staging slot)
# ---------------------------------------------------------------------------


def begin(session, next_url):
    """Store a pending sign-in in the session; return the authorize URL."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    session[PENDING_SESSION_KEY] = {
        "state": state,
        "verifier": verifier,
        "next": next_url,
        "at": int(time.time()),
    }
    query = urlencode(
        {
            "return": callback_url(),
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{issuer_base()}{AUTHORIZE_PATH}?{query}"


def take_pending(session, state):
    """Pop the pending sign-in (single use) and check ``state`` against it."""
    pending = session.pop(PENDING_SESSION_KEY, None)
    if not isinstance(pending, dict):
        raise SSOError("no_pending_sign_in")
    expected = pending.get("state")
    if not isinstance(expected, str) or not isinstance(state, str):
        raise SSOError("state_mismatch")
    if not hmac.compare_digest(expected.encode("utf-8"), state.encode("utf-8")):
        raise SSOError("state_mismatch")
    started = pending.get("at")
    if not isinstance(started, int) or time.time() - started > PENDING_MAX_AGE_SECONDS:
        raise SSOError("sign_in_expired")
    return pending


def exchange(code, verifier):
    """Ask crush.lu who this is. Server to server; never follows a redirect."""
    import requests

    try:
        response = requests.post(
            f"{issuer_base()}{EXCHANGE_PATH}",
            json={"code": code, "code_verifier": verifier},
            headers={"Authorization": f"Bearer {_secret()}"},
            timeout=EXCHANGE_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException:
        logger.warning("Coach playground sign-in: crush.lu unreachable")
        raise SSOError("issuer_unavailable", 502)
    if response.status_code == 401:
        raise SSOError("code_refused", 400)
    if response.status_code != 200:
        logger.warning(
            "Coach playground sign-in: crush.lu answered %s", response.status_code
        )
        raise SSOError("issuer_unavailable", 502)
    try:
        claims = response.json()
    except ValueError:
        raise SSOError("issuer_unavailable", 502)
    if not isinstance(claims, dict):
        raise SSOError("issuer_unavailable", 502)
    return claims


@dataclass
class ProvisionResult:
    user: object = None
    refused: str = ""


def _clean_name(value):
    return (value or "").strip()[:150] if isinstance(value, str) else ""


def provision_coach(claims, expected_state):
    """Create or refresh the staging coach account for verified claims.

    Returns a ``ProvisionResult`` with ``user`` set, or ``refused`` set to a
    code the page explains. ``role: none`` deactivates a linked staging coach.
    """
    from allauth.account.models import EmailAddress

    from crush_lu.models import CrushCoach, PlaygroundAccount

    if claims.get("aud") != AUDIENCE or claims.get("state") != expected_state:
        raise SSOError("state_mismatch")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not SUB_RE.match(sub):
        raise SSOError("bad_claims")
    production_account_id = int(sub)
    role = claims.get("role")

    link = (
        PlaygroundAccount.objects.filter(
            kind=PlaygroundAccount.KIND_COACH,
            production_account_id=production_account_id,
        )
        .select_related("user")
        .first()
    )

    if role not in (ROLE_COACH, ROLE_SUPERUSER):
        if link is not None:
            coach = CrushCoach.objects.filter(user=link.user, is_active=True).first()
            if coach is not None:
                coach.is_active = False
                # save() (not update()) so the staff signal revokes is_staff.
                coach.save(update_fields=["is_active"])
                logger.info(
                    "Coach playground: staging coach %s deactivated (not a "
                    "coach on crush.lu any more)",
                    link.user_id,
                )
        return ProvisionResult(refused="not_a_coach")

    email = claims.get("email")
    if not isinstance(email, str) or claims.get("email_verified") is not True:
        raise SSOError("bad_claims")
    email = email.strip()
    try:
        validate_email(email)
    except ValidationError:
        raise SSOError("bad_claims")

    User = get_user_model()
    try:
        with transaction.atomic():
            if link is not None:
                user = User.objects.select_for_update().get(pk=link.user_id)
            else:
                user = _match_by_email(User, EmailAddress, email)
                if user is None:
                    user = _create_coach_user(User, production_account_id, email)
                else:
                    user = User.objects.select_for_update().get(pk=user.pk)

            refusal = _refusal_for(user, production_account_id)
            if refusal:
                return ProvisionResult(refused=refusal)

            if user.email.lower() != email.lower():
                if (
                    User.objects.filter(email__iexact=email)
                    .exclude(pk=user.pk)
                    .exists()
                ):
                    return ProvisionResult(refused="email_in_use")
                user.email = email
            user.first_name = _clean_name(claims.get("given_name"))
            user.last_name = _clean_name(claims.get("family_name"))
            user.save(update_fields=["email", "first_name", "last_name"])

            _ensure_primary_email(EmailAddress, user, email)

            coach, created = CrushCoach.objects.get_or_create(
                user=user, defaults={"is_active": True}
            )
            if not created and not coach.is_active:
                coach.is_active = True
                coach.save(update_fields=["is_active"])

            PlaygroundAccount.objects.update_or_create(
                user=user,
                defaults={
                    "kind": PlaygroundAccount.KIND_COACH,
                    "production_account_id": production_account_id,
                },
            )
    except IntegrityError:
        # A second callback for the same coach raced this one.
        return ProvisionResult(refused="try_again")

    logger.info(
        "Coach playground: crush.lu user %s signed in as staging user %s",
        production_account_id,
        user.pk,
    )
    return ProvisionResult(user=user)


def _match_by_email(User, EmailAddress, email):
    verified_user_ids = set(
        EmailAddress.objects.filter(email__iexact=email, verified=True).values_list(
            "user_id", flat=True
        )
    )
    if len(verified_user_ids) > 1:
        raise SSOError("ambiguous_account", 409)
    if verified_user_ids:
        return User.objects.get(pk=verified_user_ids.pop())
    # An account that only *claims* this email (staging signup creates it
    # before the address is confirmed) is never adopted: whoever registered it
    # chose its password, and linking it would hand them coach access.
    if User.objects.filter(email__iexact=email).exists():
        raise SSOError("unverified_account", 409)
    return None


def _create_coach_user(User, production_account_id, email):
    base = f"coach_{production_account_id}"
    username = base
    suffix = 1
    while User.objects.filter(username=username).exists():
        suffix += 1
        username = f"{base}_{suffix}"
    user = User(username=username, email=email)
    user.set_unusable_password()
    user.save()
    return user


def _refusal_for(user, production_account_id):
    from crush_lu.models import PlaygroundAccount

    if not user.is_active:
        return "account_inactive"
    existing = PlaygroundAccount.objects.filter(user=user).first()
    if existing is None:
        return ""
    if existing.kind != PlaygroundAccount.KIND_COACH:
        return "playground_account"
    if existing.production_account_id not in (None, production_account_id):
        return "linked_elsewhere"
    return ""


def _ensure_primary_email(EmailAddress, user, email):
    address = EmailAddress.objects.filter(user=user, email__iexact=email).first()
    # allauth allows one primary address per user: clear the others first.
    others = EmailAddress.objects.filter(user=user, primary=True)
    if address is not None:
        others = others.exclude(pk=address.pk)
    others.update(primary=False)
    if address is None:
        EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
        return
    changed = []
    if not address.verified:
        address.verified = True
        changed.append("verified")
    if not address.primary:
        address.primary = True
        changed.append("primary")
    if changed:
        address.save(update_fields=changed)
