"""
LuxID OAuth views.

Subclasses the OpenID Connect adapter with a fixed provider_id="luxid"
so allauth uses /accounts/luxid/ URLs instead of /accounts/oidc/luxid/.

Latency notes
-------------
Upstream's callback makes three outbound HTTPS calls before the user is
logged in: the discovery document, the token exchange, and ``/userinfo``.
Production telemetry over 90 days (AppDependencies) measured:

    discovery   224 calls   avg 163ms   p50 116ms   max 3214ms
    userinfo     65 calls   avg 370ms   p50 348ms   p90  496ms

Discovery is fetched twice per login (once at ``/login/`` to build the
authorize URL, once at ``/callback/``), so the two together account for
roughly 700ms of the measured 1.93s average callback. This module removes
both where it is safe to do so: discovery is cached across requests, and
``/userinfo`` is skipped when the id_token already carries the claims we
consume. The token exchange is mandatory and untouched.
"""

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from allauth.socialaccount.providers.oauth2.views import (
    OAuth2CallbackView,
    OAuth2LoginView,
)
from allauth.socialaccount.providers.openid_connect.views import (
    OpenIDConnectOAuth2Adapter,
)
from allauth.utils import build_absolute_uri

logger = logging.getLogger(__name__)


# How long a fetched discovery document stays cached. LuxID's endpoints are
# stable infrastructure — POST Luxembourg announces changes rather than
# rotating them silently — so hours rather than minutes is appropriate. Kept
# overridable so a rotation can be picked up without a redeploy (set the
# setting to 0 to disable caching entirely).
LUXID_DISCOVERY_CACHE_TIMEOUT = getattr(
    settings, "LUXID_DISCOVERY_CACHE_TIMEOUT", 6 * 60 * 60
)

# Claims LuxID always releases for an authenticated subject, per Annex C6 of
# the CIAM agreement. If any of these is missing from the id_token we cannot
# complete a login from it alone and must fall back to /userinfo.
LUXID_CORE_CLAIMS = ("sub", "email", "given_name", "family_name")

# Claims the member controls. LuxID omits these entirely when the user
# declined to share them ("Je préfère ne pas le dire" sends no gender claim
# at all), so their absence is NOT evidence that the id_token is incomplete
# and they can never be required individually. They are used only as a
# positive signal — see _should_fetch_userinfo below.
LUXID_OPTIONAL_CLAIMS = ("birthdate", "gender", "phone_number", "locale")


class LuxIDOAuth2Adapter(OpenIDConnectOAuth2Adapter):
    provider_id = "luxid"

    def __init__(self, request, provider_id=None):
        super().__init__(request, provider_id="luxid")

    def get_callback_url(self, request, app):
        # Override OpenIDConnectOAuth2Adapter which reverses
        # "openid_connect_callback" with provider_id kwargs.
        # Our urls.py registers "luxid_callback" without kwargs.
        callback_url = reverse("luxid_callback")
        protocol = self.redirect_uri_protocol
        return build_absolute_uri(request, callback_url, protocol)

    # ------------------------------------------------------------------
    # Discovery document caching
    # ------------------------------------------------------------------

    @property
    def _discovery_cache_key(self):
        # Key on the resolved server_url, not the provider id: staging and
        # production point the same provider at different LuxID hosts
        # (login-uat.luxid.lu vs login.luxid.lu) and must never share an
        # entry. Hashed because the URL is long and the cache backend has a
        # key-length limit; the digest is a cache key, not a security
        # boundary, so a fast hash is fine.
        server_url = self.get_provider().server_url
        digest = hashlib.sha256(server_url.encode("utf-8")).hexdigest()[:32]
        return f"luxid:oidc-discovery:{digest}"

    @property
    def openid_config(self):
        """Return the OIDC discovery document, cached across requests.

        Upstream memoizes this on the adapter instance only, and a fresh
        adapter is constructed per request — so every ``/login/`` and every
        ``/callback/`` paid a full round trip to LuxID (avg 163ms, worst
        observed 3.2s) just to learn endpoint URLs that have never changed.

        Cache failures are non-fatal by design: any miss, error, or disabled
        timeout falls through to upstream's fetch, so a wedged Redis makes
        login slower rather than broken.
        """
        if hasattr(self, "_openid_config"):
            return self._openid_config

        if LUXID_DISCOVERY_CACHE_TIMEOUT:
            try:
                cached = cache.get(self._discovery_cache_key)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("[LUXID] discovery cache read failed: %s", exc)
                cached = None
            # Guard the shape as well as the presence. A truncated or
            # foreign entry would otherwise surface far away from here as a
            # KeyError on access_token_url, mid-login.
            if isinstance(cached, dict) and cached.get("token_endpoint"):
                self._openid_config = cached
                return self._openid_config

        # Miss (or caching disabled): let upstream fetch and validate. It
        # sets self._openid_config as a side effect, which is why this is
        # not assigned here.
        config = super().openid_config

        if LUXID_DISCOVERY_CACHE_TIMEOUT and isinstance(config, dict):
            try:
                cache.set(
                    self._discovery_cache_key,
                    config,
                    LUXID_DISCOVERY_CACHE_TIMEOUT,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("[LUXID] discovery cache write failed: %s", exc)

        return config

    # ------------------------------------------------------------------
    # Conditional /userinfo fetch
    # ------------------------------------------------------------------

    def _should_fetch_userinfo(self, app, id_token):
        """Decide whether ``/userinfo`` still has to be called.

        Returns True (fetch) unless the decoded id_token demonstrably carries
        everything downstream code reads. The predicate is deliberately
        asymmetric — it only skips the call on positive evidence, and every
        uncertain case fetches, because a missed claim silently degrades a
        member's profile whereas a redundant fetch merely costs 370ms.

        Two conditions must both hold to skip:

        1. Every claim in LUXID_CORE_CLAIMS is present. These are the
           identity claims a login cannot be completed without.
        2. At least one LUXID_OPTIONAL_CLAIMS entry is present. This is the
           load-bearing half. The optional claims are exactly the ones a
           member may withhold, so no individual one can be required — but
           an IdP configured to reserve profile claims for /userinfo would
           release *none* of them into the id_token. Seeing even one proves
           the id_token is carrying the full authorized claim set rather
           than a bare authentication assertion.

        The consequence of (2) is that a member who shared none of the four
        optional claims still triggers a userinfo fetch on every login. That
        is a missed optimization, not a defect, and is preferable to
        inferring completeness from an absence that has two possible causes.
        """
        # An explicit admin setting wins in both directions — this is a
        # government CIAM integration, so the optimization must be
        # switchable from the Django admin without a redeploy.
        configured = (app.settings or {}).get("fetch_userinfo")
        if configured is not None:
            return bool(configured)

        if not id_token:
            return True

        missing = [c for c in LUXID_CORE_CLAIMS if not id_token.get(c)]
        if missing:
            logger.debug(
                "[LUXID] id_token missing core claims %s; fetching userinfo",
                missing,
            )
            return True

        if not any(id_token.get(c) for c in LUXID_OPTIONAL_CLAIMS):
            logger.debug(
                "[LUXID] id_token carries no optional C6 claim; "
                "fetching userinfo to be certain"
            )
            return True

        return False

    def complete_login(self, request, app, token, **kwargs):
        """Build the SocialLogin, skipping ``/userinfo`` when it adds nothing.

        Mirrors upstream's implementation with the order inverted: the
        id_token is decoded *first* so its claims can inform whether the
        userinfo round trip is still needed.

        Reordering is safe for signature verification. Upstream decides
        ``verify_signature = not self.did_fetch_access_token``, and
        ``did_fetch_access_token`` is set by ``get_access_token_data()``,
        which OAuth2CallbackView.dispatch() always runs before calling
        complete_login(). It is therefore already True at this point whether
        the id_token is decoded before or after the userinfo call, and the
        signature check stays skipped for the same reason it was before: the
        token arrived over a direct TLS channel from the IdP.

        The ``{"id_token": ..., "userinfo": ...}`` envelope shape is
        preserved exactly, including omitting the ``userinfo`` key entirely
        when it was not fetched. ``_luxid_claims()``,
        ``extract_email_addresses()`` and allauth's ``_pick_data()`` all fall
        back to the id_token when that key is absent.
        """
        id_token_str = kwargs["response"].get("id_token")

        data = {}
        id_token = None
        if id_token_str:
            id_token = self._decode_id_token(app, id_token_str)
            data["id_token"] = id_token

        if self._should_fetch_userinfo(app, id_token):
            data["userinfo"] = self._fetch_user_info(token.token)
        else:
            logger.info(
                "[LUXID] userinfo fetch skipped; id_token carries the "
                "required claims"
            )

        return self.get_provider().sociallogin_from_response(request, data)


oauth2_login = OAuth2LoginView.adapter_view(LuxIDOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(LuxIDOAuth2Adapter)
