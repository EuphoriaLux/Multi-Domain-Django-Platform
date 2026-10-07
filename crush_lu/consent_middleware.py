"""
Middleware to enforce Crush.lu consent requirements.

Uses a deny-by-default approach: all authenticated Crush.lu requests require
consent unless the path is explicitly exempt. Users without consent are
redirected to the consent confirmation page.

Only active on the Crush.lu domain (checks request.urlconf).
"""

import logging
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

logger = logging.getLogger(__name__)

CRUSH_URLCONF = "azureproject.urls_crush"


class CrushConsentMiddleware:
    """
    Middleware to check if authenticated users have given Crush.lu consent.

    Uses a deny-by-default approach: all authenticated requests on the Crush.lu
    domain require consent UNLESS the path is in EXEMPT_PATHS. This ensures new
    routes are automatically protected without needing to update an allowlist.

    Exempt paths include auth flows, public pages, infrastructure endpoints,
    and the consent page itself (to avoid redirect loops).
    """

    # Paths that DON'T require consent - everything else does.
    # Uses prefix matching: '/about/' matches '/about/', '/about/team/', etc.
    EXEMPT_PATHS = [
        # Auth
        "/login/",
        "/signup/",
        "/logout/",
        "/oauth-complete/",
        # /oauth/ stays exempt (audited for #1217): popup-callback, popup-error
        # and landing are GET-only hops that run straight after provider login,
        # BEFORE consent is recorded (consent_confirm is where a fresh social
        # signup lands next), so gating them would loop the sign-in. They only
        # echo the caller's own first name and redirect target, and the only
        # write is allauth recovery state keyed by single-use callback proof.
        "/oauth/",
        "/accounts/",  # Allauth endpoints
        # Consent & ban flow (must be exempt to avoid redirect loops)
        "/consent/confirm/",
        "/account/banned/",
        # Public/landing pages
        "/about/",
        "/how-it-works/",
        # /membership/ is deliberately NOT exempt (audited for #1217): for a
        # signed-in member it creates their ReferralCode and renders their own
        # premium/recovery state. Anonymous visitors still get the page since
        # the gates only apply to authenticated users.
        "/privacy-policy/",
        "/terms-of-service/",
        "/data-deletion/",
        "/child-safety-standards/",
        "/test-upstair/",
        # Public landing pages
        "/r/",  # Referral redirect
        # /invite/ stays exempt (audited for #1217): both views are token-gated
        # by an unguessable invitation UUID, not by member state. invitation_accept
        # is how a guest account is *created* (anonymous, no consent record yet)
        # and logs that new user in, so a consent gate would break the flow it
        # exists for; it reads and writes nothing belonging to an existing
        # member. A banned/consent-less member who opens one just gets the
        # invitee's own event details. Consent capture for guests is a separate gap.
        "/invite/",
        "/unsubscribe/",
        "/facebook/",  # Data deletion callback
        "/voting-demo/",
        # LuxID mockups
        "/mockup/",
        # Infrastructure
        "/api/",
        "/static/",
        "/media/",
        "/admin/",
        "/crush-admin/",
        "/healthz/",
        "/readyz/",
        "/robots.txt",
        "/sitemap.xml",
        "/favicon.ico",
        "/pwa-debug/",
        "/sw-workbox.js",
        "/manifest.json",
        "/offline/",
        "/csp-report/",
    ]

    # /api/ paths a banned member may still call. Everything else under /api/
    # answers 403 {"error": "banned"} (#1216).
    API_BAN_EXEMPT_PATHS = (
        "/api/admin/",
        "/api/analytics/",
        "/api/mobile/",
        "/api/csrf-token/",
        "/api/push/vapid-public-key/",
        "/api/webhooks/",
        # JWT logout only blacklists the caller's refresh token; a banned client
        # must still be able to end its own session. Other /api/token/ routes
        # stay gated.
        "/api/token/logout/",
    )

    # /api/ paths a member WITHOUT Crush.lu consent may still call (#1217).
    # Everything else under /api/ answers 403 {"code": "consent_required"}.
    # Starts from the ban-exempt set minus the blanket /api/mobile/ prefix
    # (key-authenticated machine endpoints and the CSRF/VAPID bootstrap) and
    # adds only what the routes show must work before consent is recorded, or
    # lets a member withdraw data:
    API_CONSENT_EXEMPT_PATHS = tuple(
        p for p in API_BAN_EXEMPT_PATHS if p != "/api/mobile/"
    ) + (
        # Native-app shell: config and the auth handoff/complete bridge run
        # while signing in (before consent_confirm); unregister is withdrawal.
        # Device list/register/preferences read or persist linked-device
        # metadata, so they stay gated.
        "/api/mobile/ios/config/",
        "/api/mobile/ios/auth/handoff/",
        "/api/mobile/ios/auth/complete/",
        "/api/mobile/ios/devices/unregister/",
        "/api/mobile/android/config/",
        "/api/mobile/android/auth/handoff/",
        "/api/mobile/android/auth/complete/",
        "/api/mobile/android/devices/unregister/",
        # Coach push revocation: the member routes below only delete
        # PushSubscription rows, so a consentless coach needs these to opt out.
        "/api/coach/push/unsubscribe/",
        "/api/coach/push/delete-subscription/",
        # Sign-in bridges: /api/auth/status/ is polled by the OAuth landing page
        # before consent_confirm, /api/auth/spa-callback/ mints the hub SPA code,
        # /api/token/ exchanges credentials or a refresh token.
        "/api/auth/",
        "/api/token/",
        # Push: revocation and read-only routes only (api_push), so a member who
        # withdrew consent can still opt out and see state. Anything that
        # creates, re-enables or edits a subscription (subscribe,
        # refresh-subscription, preferences), plus PWA tracking
        # (mark-pwa-user, pwa/register-installation, which stores a device
        # fingerprint and user agent), stays gated until consent is recorded.
        "/api/push/validate-subscription/",
        "/api/push/unsubscribe/",
        "/api/push/delete-subscription/",
        "/api/push/subscriptions/",
        "/api/push/pwa-status/",
        # Not listed on purpose: /api/phone/* (phone verification). Its pages
        # (/onboarding/phone/) are already consent-gated and consent is recorded
        # at signup, so it never runs pre-consent; it stores a phone number (PII).
        # There is no /api/ consent-recording or account-deletion route: consent
        # is captured by the signup forms and /consent/confirm/, deletion lives
        # under /account/, all outside /api/ and unaffected by this check.
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self.is_on_crush_domain(request):
            path = self._strip_language_prefix(request.path)
            if path.startswith("/api/"):
                # Every identity the request could act as is gated (see
                # _resolve_api_users); resolved lazily, once.
                api_users = None
                if not self._is_api_ban_exempt_path(path):
                    api_users = self._resolve_api_users(request)
                    banned = next((u for u in api_users if self.is_banned(u)), None)
                    if banned is not None:
                        logger.info(
                            "Banned user %s denied API request to %s",
                            banned.id,
                            request.path,
                        )
                        return JsonResponse({"error": "banned"}, status=403)
                # Consent comes after the ban check so a deletion tombstone
                # (banned, consent False) keeps its "banned" answer.
                if not self._is_api_consent_exempt_path(path):
                    if api_users is None:
                        api_users = self._resolve_api_users(request)
                    unconsented = next(
                        (u for u in api_users if not self.has_crushlu_consent(u)),
                        None,
                    )
                    if unconsented is not None:
                        logger.info(
                            "User %s denied API request to %s without Crush.lu consent",
                            unconsented.id,
                            request.path,
                        )
                        return JsonResponse({"code": "consent_required"}, status=403)
                return self.get_response(request)

            # Exempt non-API paths without triggering request.user.is_authenticated
            # and a database connection. This is critical for static, media and CSP.
            if self._is_exempt_path(path):
                return self.get_response(request)

        # A checkout retirement can temporarily pause profile deletion after
        # committing a payment-blocking tombstone.  Let that member resume
        # only the exact deletion endpoints; every payment and product route
        # remains banned, and successful deletion replaces this transient
        # reason with the permanent user-deletion reason.
        if (
            self.is_on_crush_domain(request)
            and request.user.is_authenticated
            and self._is_deletion_retry_path(request.path, request.GET)
        ):
            consent = getattr(request.user, "data_consent", None)
            if (
                consent is not None
                and consent.crushlu_banned
                and consent.crushlu_ban_reason == "deletion_in_progress"
            ):
                return self.get_response(request)

        # Check ban status before anything else (for authenticated Crush.lu users)
        if self.is_on_crush_domain(request) and request.user.is_authenticated:
            if self.is_banned(request.user):
                path = request.path
                # Allow access to banned page, logout, and static assets
                if (
                    not any(
                        path.startswith(p)
                        for p in [
                            "/account/banned/",
                            "/logout/",
                            "/static/",
                            "/media/",
                            "/healthz/",
                            "/readyz/",
                        ]
                    )
                    and "/account/banned/" not in path
                ):
                    logger.info(
                        f"Banned user {request.user.id} redirected from {path} to banned page"
                    )
                    return redirect(
                        reverse("crush_lu:account_banned", urlconf=CRUSH_URLCONF)
                    )

        # Check if consent is required
        if self.requires_consent_check(request):
            # Check if user has consent
            if not self.has_crushlu_consent(request.user):
                logger.info(
                    f"User {request.user.id} attempted to access {request.path} without Crush.lu consent"
                )
                return redirect(
                    reverse("crush_lu:consent_confirm", urlconf=CRUSH_URLCONF)
                )

        response = self.get_response(request)
        return response

    def _is_exempt_path(self, path):
        """
        Check if path is exempt from DB-requiring middleware checks.
        Strips language prefix before matching against EXEMPT_PATHS.
        """
        path = self._strip_language_prefix(path)

        # Check against exempt paths
        for exempt_path in self.EXEMPT_PATHS:
            if path == exempt_path or path.startswith(exempt_path):
                return True

        # Root path is always exempt
        return path == "/"

    def _strip_language_prefix(self, path):
        for lang_prefix in ["/en/", "/fr/", "/de/"]:
            if path.startswith(lang_prefix):
                return "/" + path[len(lang_prefix) :]
        return path

    @staticmethod
    def _resolve_api_users(request):
        """Every member an /api/ request could act as (possibly empty).

        ``request.user`` only reflects the session, but DRF views also accept
        a JWT bearer token, authenticated after middleware runs, and (with no
        session authenticator configured) act as the bearer user even when a
        session is present, while plain Django views act as the session user.
        Gate both so neither can be paired with a clean account to dodge a
        ban or consent check. An invalid token adds nobody; the view answers
        401 as before.
        """
        users = []
        if request.user.is_authenticated:
            users.append(request.user)
        # Not pre-filtered on "Bearer ": SimpleJWT splits the header on any
        # whitespace (e.g. "Bearer\t<token>"), so let it decide what is a token.
        if request.META.get("HTTP_AUTHORIZATION"):
            from rest_framework_simplejwt.authentication import JWTAuthentication

            try:
                result = JWTAuthentication().authenticate(request)
            except Exception:  # noqa: BLE001 - invalid/expired token: view decides
                result = None
            if result and all(result[0].pk != u.pk for u in users):
                users.append(result[0])
        return users

    def _is_api_ban_exempt_path(self, path):
        return self._matches_api_paths(path, self.API_BAN_EXEMPT_PATHS)

    def _is_api_consent_exempt_path(self, path):
        return self._matches_api_paths(path, self.API_CONSENT_EXEMPT_PATHS)

    @staticmethod
    def _matches_api_paths(path, exempt_paths):
        return any(
            path == exempt_path.rstrip("/") or path.startswith(exempt_path)
            for exempt_path in exempt_paths
        )

    def _is_deletion_retry_path(self, path, query=None):
        """Match only self-service routes that can finish a paused erasure.

        That includes the account drill-down overview and its Danger Zone,
        which renders the Delete action for this state (8-08), so the
        drawer's Settings link leads somewhere that can finish the deletion.
        """

        path = self._strip_language_prefix(path)
        if path == "/profile/edit/" and query is not None:
            return query.get("section") == "account" and query.get("sub", "") in (
                "",
                "danger",
            )
        return path in {
            "/account/delete/",
            "/account/delete-profile/",
            "/account/gdpr/",
        }

    def is_on_crush_domain(self, request):
        """Check if request is on the Crush.lu domain."""
        return getattr(request, "urlconf", None) == CRUSH_URLCONF

    def is_banned(self, user):
        """Check if user is banned from Crush.lu."""
        if not hasattr(user, "data_consent"):
            return False
        return user.data_consent.crushlu_banned

    def requires_consent_check(self, request):
        """
        Determine if this request requires consent checking.

        Uses a deny-by-default approach: all paths require consent unless
        explicitly listed in EXEMPT_PATHS. Language prefixes (/en/, /fr/, /de/)
        are stripped before matching.

        Returns True if:
        - Request is on the Crush.lu domain
        - User is authenticated
        - Path is not in EXEMPT_PATHS
        """
        # Only apply on Crush.lu domain
        if getattr(request, "urlconf", None) != CRUSH_URLCONF:
            return False

        # Not authenticated - no check needed
        if not request.user.is_authenticated:
            return False

        path = self._strip_language_prefix(request.path)

        # Check if path is exempt (exact match or prefix match)
        for exempt_path in self.EXEMPT_PATHS:
            if path == exempt_path or path.startswith(exempt_path):
                return False

        # The bare root path '/' is always exempt (landing page)
        if path == "/":
            return False

        # All non-exempt paths require consent for authenticated users
        return True

    def has_crushlu_consent(self, user):
        """
        Check if user has given Crush.lu consent.

        Returns True if:
        - User has UserDataConsent record with crushlu_consent_given=True
        """
        if not hasattr(user, "data_consent"):
            # No consent record - should not happen with signals, but be safe
            logger.warning(f"User {user.id} has no data_consent record")
            return False

        return user.data_consent.crushlu_consent_given
