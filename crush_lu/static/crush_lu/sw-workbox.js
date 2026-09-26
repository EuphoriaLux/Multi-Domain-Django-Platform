// Crush.lu Service Worker with Workbox
// Production-ready PWA implementation using local Workbox library
// Version: v34 - A cookie-consent change (a POST to /cookies/accept/ or
//                /cookies/decline/ that reaches this worker) purges "crush-pages":
//                a kept page embeds the consent state and trackers it was
//                rendered with. The offline tickets move to "crush-tickets-v2",
//                which a consent change keeps (a ticket rendered with the
//                consent-flag checks holds back its trackers after a later
//                refusal, while the refusal flag is in the browser; see the
//                consent purge in the fetch listener for when it is not).
//                Activation deletes the old "crush-tickets" (the
//                v32/v33 copies, rendered without those checks) and empties
//                "crush-pages", as every later version's activation will.
//                Consent POSTs are also kept off the background-sync queue: a
//                replay up to 24h later would rewrite the consent flags over a
//                newer choice.
// Version: v33 - Tell the page when a POST really was stored in the background-
//                sync queue ({type: "crush-queued", requestId, url} to the one
//                client that sent it, posted only after the queue write succeeded), so
//                htmx-error-toast.js promises a replay only for a request the
//                worker holds. A failed IndexedDB write gets the plain copy.
//                Answers {type: "crush-capabilities?"} so the page can tell
//                this worker from an older one that queues without saying so,
//                and drains the queue on {type: "crush-drain-queue"} (sent when
//                a page comes back online) since Background Sync is not
//                everywhere.
// Version: v32 - Event tickets (/<lang>/events/<id>/ticket/) get their own
//                NetworkFirst cache so the QR opens offline at the venue door,
//                purged on every navigation that can switch the signed-in
//                account (sign-in/out/up, native-app handoff, guest invite).
// Version: v31 - Keep /crush-admin/ off the background-sync queue and out of the
//                cache. The admin is mounted at /crush-admin/, not /admin/, so
//                every exclusion list written against /admin/ missed it. The
//                queue is also drained through the same list, so entries an
//                older worker already stored are dropped rather than replayed.

// Offline event tickets. Declared up here because the hard-bypass listener
// below purges this cache, and it runs before Workbox is even imported.
// The ticket URL is keyed by event, not by user, so a copy left behind by one
// account would be served offline to the next account on the same device.
const TICKET_CACHE = "crush-tickets-v2";
const TICKET_PATH = /^\/(en|de|fr)\/events\/\d+\/ticket\/$/;

// The ticket cache of the v32/v33 workers. Its copies were rendered before the
// analytics tags and the cookie banner checked the live consent flags, so one
// served at the door would run its trackers whatever the visitor chose since,
// for up to a year. Deleted on activation (and on a session switch, for the
// same account reason as TICKET_CACHE); nothing writes to it any more.
const LEGACY_TICKET_CACHE = "crush-tickets";

// The generic NetworkFirst cache for pages (Strategy 4). Declared up here for
// the same reason: the listener below purges it on a consent change, and
// activation empties it (see the activate listener).
const PAGE_CACHE = "crush-pages";

// A saved cookie-consent choice: django-cookie-consent's accept/decline views,
// mounted unprefixed at /cookies/ on every site (urls_shared.base_patterns),
// the same paths CookieConsentFlagSyncMiddleware rewrites the consent flags
// for. A page kept in PAGE_CACHE embeds the consent state it was rendered with
// (data-consent-state) and the trackers that state allowed, so it must not
// outlive a change of mind. Only POSTs: the banner's GET /cookies/status/
// changes nothing.
const CONSENT_CHANGE_PATH = /\/cookies\/(accept|decline)\/$/;

// Navigations that can put a different account (or none) on this device, and
// so must drop TICKET_CACHE. These are the login()/logout() call sites a
// navigation reaches on crush.lu: the Crush and allauth sign-in, sign-out and
// sign-up pages and the social callbacks (all contain /login, /logout or
// /signup), plus the two that sign an account in under another name.
const SESSION_SWITCH_PATHS = [
    // native_auth.complete_native_auth: the app WebView redeems a one-time code.
    /^\/api\/mobile\/(ios|android)\/auth\/complete\//,
    // views_invitations.invitation_accept: signs the new guest account in.
    /^\/(en|de|fr)\/invite\/[^/]+\/accept\/$/,
];

// views_account.gdpr_data_management: a full account deletion POST calls
// logout() and redirects home, never reaching /logout. Only the POST counts:
// merely opening the GDPR page must not drop an upcoming ticket.
const ACCOUNT_DELETION_POST_PATHS = [/^\/(en|de|fr)\/account\/(gdpr|delete)\/$/];

function isSessionBoundaryNavigation(request, url) {
    if (request.mode !== "navigate") return false;
    const path = url.pathname;
    return (
        path.includes("/login") ||
        path.includes("/logout") ||
        path.includes("/signup") ||
        SESSION_SWITCH_PATHS.some((pattern) => pattern.test(path)) ||
        (request.method === "POST" &&
            ACCOUNT_DELETION_POST_PATHS.some((pattern) => pattern.test(path)))
    );
}

// ============================================================================
// CRITICAL: OAuth Callback Bypass - MUST BE BEFORE WORKBOX
// ============================================================================
// OAuth callbacks must COMPLETELY bypass the service worker's caching logic.
//
// IMPORTANT: Just using `return;` does NOT bypass - it only exits this handler
// but Workbox will still register its own handlers that intercept the request.
//
// Using event.respondWith(fetch(event.request)) ensures this handler "claims"
// the request, preventing any Workbox routes, offline fallbacks, or caching
// from processing it. The SW still responds, but with a direct network fetch.

self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    const acceptHeader = event.request.headers.get("accept") || "";
    const isGoogleWalletSaveUrl =
        (url.hostname === "pay.google.com" && url.pathname.startsWith("/gp/v/save")) ||
        (url.hostname === "wallet.google.com" && url.pathname.startsWith("/save"));

    // TRUE HARD BYPASS: Apple Wallet pkpass downloads & Google Wallet save URLs
    if (
        acceptHeader.includes("application/vnd.apple.pkpass") ||
        isGoogleWalletSaveUrl
    ) {
        event.respondWith(fetch(event.request));
        return;
    }

    // TRUE HARD BYPASS: External CDN resources (cross-origin)
    // These cause "opaque" response errors when cached by service worker
    if (url.origin !== self.location.origin) {
        // Don't intercept cross-origin requests at all - let browser handle them
        return;
    }

    // Switching accounts changes whose ticket this device may show: drop the
    // offline ticket copies (see TICKET_CACHE). waitUntil keeps the worker
    // alive for the delete without claiming the request, so the auth bypass
    // below and the routes further down still apply unchanged.
    if (isSessionBoundaryNavigation(event.request, url)) {
        event.waitUntil(
            Promise.all([
                caches.delete(TICKET_CACHE),
                caches.delete(LEGACY_TICKET_CACHE),
            ]),
        );
    }

    // A consent change drops the kept pages (see CONSENT_CHANGE_PATH), so the
    // next offline or failed navigation cannot serve a copy rendered under the
    // old choice. TICKET_CACHE is deliberately kept: purging it would lose the
    // offline QR at the door. Only workers from v34 on write to it, and v34
    // ships with the consent-flag checks (the analytics tags and the banner),
    // so a kept ticket holds back its own trackers once the visitor refuses,
    // for as long as the refusal flag (cookie_consent_<group>=decline) is in
    // the browser; the copies older workers kept without those checks were
    // dropped on activation (LEGACY_TICKET_CACHE). The flag is the limit. A
    // refusal made through the library's own forms gets it from the server
    // (CookieConsentFlagSyncMiddleware, for a year); one saved in the banner
    // gets it from script, and from the server as well only when the
    // banner's best-effort POST to /cookies/ lands. Safari's seven-day cap on
    // script-written cookies can drop a flag only the script wrote, and a
    // clear of cookies that leaves Cache Storage drops any flag; a ticket
    // kept from before the refusal then tracks again. For the kept pages this
    // purge is a second layer on top of those checks; for the kept tickets
    // the checks are the only one. The library's own forms are navigations
    // and always pass through here; whether a browser routes the banner's
    // keepalive fetch through a worker varies, so nothing relies on this
    // purge alone.
    if (event.request.method === "POST" && CONSENT_CHANGE_PATH.test(url.pathname)) {
        event.waitUntil(caches.delete(PAGE_CACHE));
    }

    // TRUE HARD BYPASS: OAuth and auth-related URLs
    const isAuthUrl =
        url.pathname.startsWith("/accounts/") || // All OAuth/auth routes
        url.pathname.startsWith("/oauth/") || // OAuth landing and callbacks
        url.pathname.includes("/login/callback") || // Explicit callback match
        url.pathname.startsWith("/api/auth/") || // Auth status API
        url.pathname.startsWith("/wallet/") || // Wallet pass endpoints
        url.pathname.includes("/login") || // Login page (incl. /fr/login/, /de/login/)
        url.pathname.includes("/logout") || // Logout page (incl. language prefixes)
        url.pathname.includes("/signup") || // Signup page (incl. language prefixes)
        url.pathname.startsWith("/api/mobile/") || // Native app endpoints - see below
        url.pathname.includes("/api/csrf-token"); // CSRF token refresh endpoint
    // /api/mobile/ MUST bypass: the iOS/Android auth handoff answers with a 302 to
    // crushlu://auth?code=..., and a service worker's fetch() cannot follow a
    // redirect to a non-HTTP scheme - it fails with a network error. If the SW
    // claims that navigation, the browser never navigates to crushlu://, so
    // ASWebAuthenticationSession never sees its callback and the auth sheet hangs
    // on a cached page after a successful login (2026-07-19).
    if (isAuthUrl) {
        // Navigation requests (page loads): let the browser handle them completely.
        // Safari/WebKit may not process Set-Cookie headers (including CSRF cookies)
        // from responses that pass through event.respondWith(fetch()), so we must
        // NOT intercept navigation requests to auth pages.
        if (event.request.mode === "navigate") {
            return; // Full browser bypass - cookies will be processed correctly
        }
        // Non-navigation requests (fetch/XHR): claim the request to prevent
        // Workbox from caching it, but forward directly to the network.
        event.respondWith(fetch(event.request));
        return;
    }
});

// Import Workbox from LOCAL static files (not CDN) to enable offline installation
importScripts("/static/crush_lu/workbox/workbox-sw.js");

// Check if Workbox loaded successfully
if (workbox) {
    // ============================================================================
    // Configuration - MUST BE SET FIRST
    // ============================================================================

    // Configure Workbox to load modules from local static files
    workbox.setConfig({
        debug: location.hostname === "localhost" || location.hostname === "127.0.0.1",
        modulePathPrefix: "/static/crush_lu/workbox/",
    });

    const CACHE_VERSION = "crush-v29-push-subscription-refresh";

    // Set cache name prefix - AFTER setConfig()
    workbox.core.setCacheNameDetails({
        prefix: "crush-lu",
        suffix: CACHE_VERSION,
        precache: "precache",
        runtime: "runtime",
    });

    // ============================================================================
    // Offline Fallback Constants - MUST BE DEFINED BEFORE activate handler
    // ============================================================================
    // These constants are used in the activate handler below, so they must be
    // defined first to avoid "Cannot access before initialization" ReferenceError.

    const OFFLINE_PAGE = "/offline/";
    const OFFLINE_FALLBACK_HTML = `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Offline - Crush.lu</title>
    <style>
        body {
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .container {
            background: white;
            padding: 3rem;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 500px;
            margin: 1rem;
        }
        .icon { font-size: 4rem; margin-bottom: 1rem; }
        h1 { color: #9B59B6; margin: 0 0 1rem 0; }
        p { color: #666; line-height: 1.6; margin-bottom: 2rem; }
        .btn {
            background: linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%);
            color: white;
            border: none;
            padding: 1rem 2rem;
            border-radius: 50px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">📡</div>
        <h1>You're Offline</h1>
        <p>Check your internet connection and try again.</p>
        <button class="btn" onclick="location.reload()">Try Again</button>
    </div>
</body>
</html>`;

    // ============================================================================
    // Cache Cleanup on Activation - Clean up old caches from previous versions
    // ============================================================================

    self.addEventListener("activate", (event) => {
        event.waitUntil(
            (async () => {
                // Clean up old caches (cache names start with 'crush-lu-')
                const cacheNames = await caches.keys();
                await Promise.all(
                    cacheNames
                        .filter(
                            (name) =>
                                name.startsWith("crush-lu-") &&
                                !name.includes(CACHE_VERSION),
                        )
                        .map((name) => caches.delete(name)),
                );

                // The navigation caches carry no version suffix, so the filter
                // above never reaches them. Every worker version starts with
                // an empty PAGE_CACHE: a kept page embeds the consent state and
                // trackers of the code that rendered it (before v34, without
                // the consent-flag checks), and it is only a day's convenience
                // copy, fetched again on the next online visit. The pre-v34
                // tickets go for good (LEGACY_TICKET_CACHE). TICKET_CACHE is
                // kept, or every worker update would lose the offline QR.
                await Promise.all([
                    caches.delete(PAGE_CACHE),
                    caches.delete(LEGACY_TICKET_CACHE),
                ]);

                // Cache the offline page
                const cache = await caches.open(workbox.core.cacheNames.runtime);
                try {
                    const response = await fetch(OFFLINE_PAGE);
                    if (response.ok) {
                        await cache.put(OFFLINE_PAGE, response);
                    } else {
                        throw new Error("Offline page not available");
                    }
                } catch (error) {
                    await cache.put(
                        OFFLINE_PAGE,
                        new Response(OFFLINE_FALLBACK_HTML, {
                            headers: { "Content-Type": "text/html" },
                        }),
                    );
                }
            })(),
        );
    });

    // ============================================================================
    // Precaching - Files to cache on service worker installation
    // ============================================================================

    // Precache essential assets (REMOVED '/' to allow dynamic auth redirect)
    // Expanded for v21 performance optimization
    workbox.precaching.precacheAndRoute([
        // Critical pages
        { url: "/offline/", revision: CACHE_VERSION },

        // CSS (critical for rendering)
        { url: "/static/crush_lu/css/tailwind.css", revision: CACHE_VERSION },

        // Core JavaScript
        { url: "/static/crush_lu/js/page-loading.js", revision: CACHE_VERSION },
        { url: "/static/crush_lu/js/utils.js", revision: CACHE_VERSION },
        { url: "/static/crush_lu/js/pwa-detector.js", revision: CACHE_VERSION },
        { url: "/static/crush_lu/js/sw-register.js", revision: CACHE_VERSION },

        // PWA icons (most commonly used sizes)
        { url: "/static/crush_lu/icons/icon-192x192.png", revision: CACHE_VERSION },
        {
            url: "/static/crush_lu/icons/android-launchericon-512-512.png",
            revision: CACHE_VERSION,
        },
        { url: "/static/crush_lu/icons/ios/180.png", revision: CACHE_VERSION },

        // Favicon
        { url: "/static/crush_lu/crush_favicon.ico", revision: CACHE_VERSION },
    ]);

    // ============================================================================
    // Offline Fallback
    // ============================================================================

    // Set offline page as fallback for navigation requests
    workbox.recipes.offlineFallback({
        pageFallback: OFFLINE_PAGE,
    });

    // CRITICAL: Immediately exclude auth navigations from offline fallback
    // offlineFallback() wraps navigation requests and can interfere with OAuth
    // This route MUST be registered immediately after offlineFallback()
    workbox.routing.registerRoute(
        ({ request, url }) =>
            request.mode === "navigate" &&
            (url.pathname.startsWith("/accounts/") ||
                url.pathname.startsWith("/oauth/") ||
                url.pathname.startsWith("/login") ||
                url.pathname.startsWith("/logout") ||
                url.pathname.startsWith("/wallet/")),
        new workbox.strategies.NetworkOnly(),
    );

    // ============================================================================
    // Caching Strategies
    // ============================================================================

    // Custom plugin to notify clients when server is unreachable
    class ServerUnreachablePlugin {
        async fetchDidFail({ request }) {
            const clients = await self.clients.matchAll({ type: "window" });
            clients.forEach((client) => {
                client.postMessage({
                    type: "SERVER_UNREACHABLE",
                    url: request.url,
                    timestamp: Date.now(),
                });
            });
        }
    }

    // ============================================================================
    // OAuth/Auth Routes - NetworkOnly as backup (primary bypass is in fetch handler above)
    // ============================================================================
    // The fetch event handler above does a HARD BYPASS for /accounts/ routes.
    // This Workbox route is a backup that ensures no caching if something slips through.

    workbox.routing.registerRoute(
        ({ url }) => url.pathname.startsWith("/accounts/"),
        new workbox.strategies.NetworkOnly(),
    );

    // Wallet routes - NetworkOnly to prevent pass caching
    workbox.routing.registerRoute(
        ({ url, request }) =>
            url.pathname.startsWith("/wallet/") ||
            (request.headers.get("accept") || "").includes(
                "application/vnd.apple.pkpass",
            ) ||
            (url.hostname === "pay.google.com" &&
                url.pathname.startsWith("/gp/v/save")) ||
            (url.hostname === "wallet.google.com" && url.pathname.startsWith("/save")),
        new workbox.strategies.NetworkOnly(),
    );

    // Helper function to check if path matches authenticated routes (with i18n support)
    function isAuthenticatedRoute(pathname) {
        const authPaths = [
            "/admin",
            // The Crush admin site is mounted at /crush-admin/ (urls_crush.py),
            // which does NOT contain the substring "/admin" — the character
            // before "admin" is a hyphen. Listing it separately keeps admin
            // pages off every cache: a change form served from cache carries
            // stale inline `id`/INITIAL_FORMS values, and re-posting those
            // rows as new ones is what trips the (event, user) unique index.
            "/crush-admin",
            "/accounts",
            "/coach",
            "/dashboard",
            "/login",
            "/logout",
            "/profile",
            "/connections",
            "/journey",
            "/create-profile",
            "/edit",
            "/signup",
            "/wallet", // Wallet pass endpoints - must never be cached
            "/oauth-complete", // PWA OAuth return handler - must never be cached
            "/oauth/popup-callback", // Popup OAuth callback - must never be cached
            "/oauth/popup-error", // Popup OAuth error - must never be cached
            "/api/auth/status", // Auth status API - must never be cached
            "/api/csrf-token", // CSRF token refresh - must never be cached
        ];

        // Check with and without language prefix (en, fr, de)
        for (const authPath of authPaths) {
            if (pathname.includes(authPath)) {
                return true;
            }
            // Check with language prefixes
            if (pathname.match(new RegExp(`^/(en|fr|de)${authPath}`))) {
                return true;
            }
        }
        return false;
    }

    // Strategy 1: Network Only for authenticated/user-specific pages (MUST BE FIRST after OAuth)
    // This prevents caching of login redirects which cause the black screen issue
    workbox.routing.registerRoute(
        ({ url }) => isAuthenticatedRoute(url.pathname),
        new workbox.strategies.NetworkOnly(),
    );

    // Strategy 2: Network Only for health checks (never cache - used for reconnection detection)
    workbox.routing.registerRoute(
        ({ url }) => url.pathname.startsWith("/healthz"),
        new workbox.strategies.NetworkOnly(),
    );

    // Strategy 3: Network Only for API calls (never cache)
    //
    // EXCEPT native-app handoff NAVIGATIONS. /api/mobile/<platform>/auth/handoff/
    // answers with a 302 to crushlu://auth?code=..., and a service worker's
    // fetch() cannot follow a redirect to a non-HTTP scheme - it fails with a
    // network error. Claiming that navigation means the browser never navigates
    // to crushlu://, so ASWebAuthenticationSession never sees its callback and
    // the native auth sheet hangs on an already-successful login. Leaving it
    // unclaimed hands the redirect back to the browser, which can follow it.
    // Non-navigation /api/mobile/ calls (device registration etc.) are still
    // claimed by the hard-bypass listener above, so they remain uncached.
    workbox.routing.registerRoute(
        ({ url, request }) =>
            url.pathname.startsWith("/api/") &&
            !(
                request.mode === "navigate" &&
                url.pathname.startsWith("/api/mobile/")
            ),
        new workbox.strategies.NetworkOnly(),
    );

    // Strategy 3b: Network First for event tickets, in their own cache.
    // MUST be registered BEFORE Strategy 4, which would otherwise claim these
    // navigations into "crush-pages" — shared with every page, capped at 50
    // entries and 24 hours, so the ticket was usually gone by event night.
    // The QR is server-rendered SVG inside the HTML, so the cached page is a
    // complete, scannable ticket. networkTimeoutSeconds covers venue "lie-fi"
    // (connected, no throughput), where a plain NetworkFirst would hang at the
    // door instead of falling back. Purged whenever the signed-in account can
    // change (isSessionBoundaryNavigation, fetch listener above).
    workbox.routing.registerRoute(
        ({ request, url }) =>
            request.mode === "navigate" && TICKET_PATH.test(url.pathname),
        new workbox.strategies.NetworkFirst({
            cacheName: TICKET_CACHE,
            networkTimeoutSeconds: 5,
            plugins: [
                // maxAgeSeconds counts from the cached copy's Date header,
                // i.e. the last time the ticket was fetched online, not from
                // the event. A ticket opened once at booking and not again
                // until the door must still be served, so this has to outlast
                // any booking-to-event gap; a year bounds retention without
                // guessing one. Copies of past events are harmless: the
                // check-in API enforces its own window. Eviction is LRU and
                // each language is its own URL, so maxEntries must cover every
                // ticket a member could still need (many events x en/de/fr),
                // or an early-booked ticket is evicted before its event.
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 50,
                    maxAgeSeconds: 365 * 24 * 60 * 60,
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [200], // never a login redirect or a 404
                }),
                new ServerUnreachablePlugin(),
            ],
        }),
    );

    // Strategy 4: Network First for HTML pages (always fresh, fallback to cache)
    // IMPORTANT: Explicitly exclude auth paths to prevent any OAuth interference
    workbox.routing.registerRoute(
        ({ request, url }) =>
            request.mode === "navigate" &&
            !url.pathname.startsWith("/accounts/") &&
            !url.pathname.startsWith("/oauth/") &&
            !url.pathname.startsWith("/login") &&
            !url.pathname.startsWith("/logout") &&
            !url.pathname.startsWith("/api/mobile/"),
        new workbox.strategies.NetworkFirst({
            // Purged on a cookie-consent change (fetch listener above).
            cacheName: PAGE_CACHE,
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 50,
                    maxAgeSeconds: 24 * 60 * 60, // 24 hours
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [200], // Only cache successful responses (not redirects!)
                }),
                new ServerUnreachablePlugin(),
            ],
        }),
    );

    // Strategy 5: StaleWhileRevalidate for static assets (CSS, JS)
    // Changed from CacheFirst to allow CSS/JS updates to propagate quickly
    // Same-origin only: caching cross-origin styles/scripts (e.g. the Google
    // Fonts stylesheet) makes the SW fetch() them, which CSP polices under
    // connect-src — hosts absent there, so it would break once CSP is enforced.
    // Mirrors the same-origin guard on the image route below.
    workbox.routing.registerRoute(
        ({ request, url }) =>
            url.origin === self.location.origin &&
            (request.destination === "style" || request.destination === "script"),
        new workbox.strategies.StaleWhileRevalidate({
            cacheName: "crush-static",
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 60,
                    maxAgeSeconds: 7 * 24 * 60 * 60, // 7 days (reduced from 30)
                }),
            ],
        }),
    );

    // Strategy 6a: Icons - StaleWhileRevalidate (update quickly, don't pin for 30 days)
    // MUST be registered BEFORE the general image CacheFirst route
    workbox.routing.registerRoute(
        ({ url }) => url.pathname.startsWith("/static/crush_lu/icons/"),
        new workbox.strategies.StaleWhileRevalidate({
            cacheName: "crush-icons",
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 60,
                    maxAgeSeconds: 24 * 60 * 60, // 1 day - allows icons to update quickly
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [200],
                }),
            ],
        }),
    );

    // Strategy 6b: Cache First for same-origin images only (long cache)
    // External images (Facebook profile pics, etc.) are not cached to avoid CSP connect-src issues
    workbox.routing.registerRoute(
        ({ request, url }) =>
            request.destination === "image" && url.origin === self.location.origin,
        new workbox.strategies.CacheFirst({
            cacheName: "crush-images",
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 100,
                    maxAgeSeconds: 30 * 24 * 60 * 60, // 30 days
                }),
                new workbox.cacheableResponse.CacheableResponsePlugin({
                    statuses: [0, 200],
                }),
            ],
        }),
    );

    // Strategy 7: Stale While Revalidate for fonts
    // Same-origin only: cross-origin font files (e.g. fonts.gstatic.com, pulled
    // in by the Google Fonts stylesheet) would otherwise be SW-fetched here,
    // which CSP polices under connect-src where those hosts are absent — the
    // same reason the style/script route above is scoped. The <link>/@font-face
    // still loads them normally via font-src; the SW just doesn't cache them.
    workbox.routing.registerRoute(
        ({ request, url }) =>
            request.destination === "font" &&
            url.origin === self.location.origin,
        new workbox.strategies.StaleWhileRevalidate({
            cacheName: "crush-fonts",
            plugins: [
                new workbox.expiration.ExpirationPlugin({
                    maxEntries: 20,
                    maxAgeSeconds: 365 * 24 * 60 * 60, // 1 year
                }),
            ],
        }),
    );

    // ============================================================================
    // Offline Fallback Handler
    // ============================================================================
    // Provides graceful fallbacks when requests fail (e.g., offline)

    workbox.routing.setCatchHandler(async ({ event }) => {
        // Return fallback SVG for failed image requests
        if (event.request.destination === "image") {
            const fallbackSvg = `
        <svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">
          <defs>
            <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" style="stop-color:#9B59B6;stop-opacity:0.1"/>
              <stop offset="100%" style="stop-color:#FF6B9D;stop-opacity:0.1"/>
            </linearGradient>
          </defs>
          <rect fill="url(#grad)" width="200" height="200" rx="8"/>
          <text x="100" y="90" text-anchor="middle" fill="#9B59B6" font-family="sans-serif" font-size="14" font-weight="500">
            Image unavailable
          </text>
          <text x="100" y="115" text-anchor="middle" fill="#999" font-family="sans-serif" font-size="12">
            You're offline
          </text>
        </svg>`;

            return new Response(fallbackSvg, {
                headers: { "Content-Type": "image/svg+xml" },
            });
        }

        // For navigation requests that fail, the offlineFallback() handles it
        // For other requests, return an error
        return Response.error();
    });

    // ============================================================================
    // Background Sync (for future offline form submissions)
    // ============================================================================

    // Which POSTs the queue may hold. ONE list with TWO readers: the route
    // below decides what may ENTER the queue, and onSync decides what may
    // LEAVE it. They have to agree. A path excluded only at the entrance still
    // replays out of the IndexedDB store an older worker filled — the queue is
    // named, so it survives the update, and `maxRetentionTime` keeps its
    // contents replayable for 24h after this worker ships.
    //
    // IMPORTANT: auth-related POSTs carry CSRF tokens that can't be replayed.
    //
    // Admin POSTs are excluded for a second reason: a queued admin form is
    // replayed verbatim up to 24h later, which re-submits the whole change
    // form including its inline rows — writing them a second time, with no
    // one watching the tab it answers into. On the MeetupEvent change form
    // that means a second EventRegistration INSERT and
    //   duplicate key ... crush_lu_eventregistration_event_id_user_id_..._uniq
    // which surfaces as a form error if the replay is well separated from the
    // original and as a 500 if the two overlap. Staging hit that 500 on
    // 2026-08-11 (event 29 / user 86); the logs show a duplicate submission
    // but cannot say whether the queue or a double-click produced it. Both
    // mount points are listed: /admin/ is Django's own admin, /crush-admin/
    // is the Crush coach panel (urls_crush.py).
    //
    // Cookie-consent POSTs (/cookies/accept/, /cookies/decline/) are excluded
    // because a replay is a stale choice: CookieConsentFlagSyncMiddleware
    // rewrites the readable consent flags from it, so an acceptance queued
    // while offline and replayed hours later would overwrite a refusal made
    // since, and the consent checks in the pages trust exactly those flags.
    // The banner keeps the choice in its own cookies when the post is lost.
    function isQueueablePost(pathname) {
        return (
            !pathname.startsWith("/api/") &&
            !pathname.startsWith("/admin/") &&
            !pathname.startsWith("/crush-admin/") &&
            !pathname.startsWith("/login") &&
            !pathname.startsWith("/logout") &&
            !pathname.startsWith("/accounts/") &&
            !pathname.startsWith("/signup") &&
            !pathname.startsWith("/cookies/")
        );
    }

    // Replay the queue: the Background Sync handler, the worker-start
    // fallback Workbox uses where the Sync API is missing, and the
    // "crush-drain-queue" message a page sends when it regains connectivity
    // (below) all run this.
    // The one HTTP answer that proves the server did NOT process the
    // request: the @ratelimit decorators answer 429 before any view runs.
    // Such an entry goes back to the front of the queue and the drain
    // stops, like a network failure. Every other status is final, 5xx
    // included: a 500 can come AFTER the mutation committed (the connection
    // message view stores the row before rendering), so replaying it would
    // create and notify the same message twice. The replayed POSTs carry no
    // idempotency key on the server, so an unknown outcome is not retried.
    function replayShouldRetry(response) {
        return response.status === 429;
    }

    // One drain at a time, whatever asked for it (Sync event, worker start,
    // a page's "crush-drain-queue") and whichever worker generation asks:
    // Workbox's no-Sync fallback runs onSync from the Queue constructor in
    // EVERY worker, so an installing worker and the active one would drain
    // the shared IndexedDB queue side by side and replay ordered mutations
    // (two chat messages) in reverse. The Web Locks API is origin-wide, so
    // it serializes across generations; the promise covers this worker.
    let drainInFlight = null;

    function withDrainLock(run) {
        const locks = self.navigator && self.navigator.locks;
        if (locks && typeof locks.request === "function") {
            return locks.request("crush-queue-drain", run);
        }
        return run();
    }

    function drainQueue(queue) {
        if (drainInFlight) return drainInFlight;
        drainInFlight = withDrainLock(async () => {
            let entry;
            while ((entry = await queue.shiftRequest())) {
                // Discard, don't replay: an entry an earlier worker
                // queued predates the exclusions above, and shifting it
                // out without fetching is what actually removes it.
                if (!isQueueablePost(new URL(entry.request.url).pathname)) {
                    continue;
                }
                let response;
                try {
                    // A clone: fetch consumes the body, and unshiftRequest()
                    // serializes the request again on failure. Replaying the
                    // original would make that requeue throw and lose the
                    // entry (it was already shifted out).
                    response = await fetch(entry.request.clone());
                } catch (error) {
                    await queue.unshiftRequest(entry);
                    throw error;
                }
                if (replayShouldRetry(response)) {
                    await queue.unshiftRequest(entry);
                    throw new Error(
                        `Replay of ${entry.request.url} answered ${response.status}; kept for a later drain`,
                    );
                }
            }
        }).finally(() => {
            drainInFlight = null;
        });
        return drainInFlight;
    }

    // Same queue name and store as the BackgroundSyncPlugin this replaces
    // (entries an older worker stored are still drained), held directly so
    // a page can ask for a drain.
    const crushQueue = new workbox.backgroundSync.Queue("crush-queue", {
        maxRetentionTime: 24 * 60, // Retry for up to 24 hours (in minutes)
        onSync: ({ queue }) => drainQueue(queue),
    });
    // What BackgroundSyncPlugin.fetchDidFail does: store the failed request.
    const bgSyncPlugin = {
        fetchDidFail: async ({ request }) => {
            await crushQueue.pushRequest({ request });
        },
    };

    // Runs after bgSyncPlugin's fetchDidFail. Workbox awaits the plugins'
    // fetchDidFail callbacks in order and stops at the first that throws, so
    // this one only runs once the queue write succeeded. The page
    // (htmx-error-toast.js) shows "will sync once online" only for a request
    // it hears about here; otherwise it says "network error". Posted to the
    // ONE client that issued the fetch (event.clientId), never broadcast: two
    // tabs posting the same URL must not consume each other's confirmation.
    // The request id the page put in X-Crush-Request-Id is echoed back so the
    // page matches the ack to that request, not to a URL it may reuse.
    const queuedAckPlugin = {
        fetchDidFail: async ({ request, event }) => {
            const clientId = event && event.clientId;
            const client = clientId ? await self.clients.get(clientId) : null;
            if (!client) return;
            client.postMessage({
                type: "crush-queued",
                requestId: request.headers.get("X-Crush-Request-Id"),
                url: request.url,
                // How the replay is triggered: the Sync API, or the page's
                // "online" drain request plus the worker-start fallback.
                replay: "sync" in self.registration ? "sync" : "drain",
            });
        },
    };

    // Use background sync for POST requests (event registrations, etc.)
    workbox.routing.registerRoute(
        ({ url, request }) =>
            request.method === "POST" && isQueueablePost(url.pathname),
        new workbox.strategies.NetworkOnly({
            plugins: [bgSyncPlugin, queuedAckPlugin],
        }),
        "POST",
    );

    // ============================================================================
    // Push Notifications
    // ============================================================================

    self.addEventListener("push", (event) => {
        if (Notification.permission !== "granted") {
            return;
        }

        let data = {};
        try {
            data = event.data ? event.data.json() : {};
        } catch (e) {
            data = {
                title: "Crush.lu",
                body: event.data ? event.data.text() : "New notification",
            };
        }

        const options = {
            body: data.body || "New notification from Crush.lu",
            icon: "/static/crush_lu/icons/icon-192x192.png",
            badge: "/static/crush_lu/icons/icon-72x72.png",
            vibrate: [200, 100, 200],
            tag: data.tag || "crush-notification",
            data: data.url || "/",
        };

        event.waitUntil(
            self.registration.showNotification(data.title || "Crush.lu", options),
        );
    });

    self.addEventListener("notificationclick", (event) => {
        event.notification.close();
        const urlToOpen = new URL(event.notification.data || "/", self.location.origin);

        event.waitUntil(
            clients
                .matchAll({ type: "window", includeUncontrolled: true })
                .then((clientList) => {
                    // Find any client on same origin and focus/navigate it
                    for (const client of clientList) {
                        const clientUrl = new URL(client.url);
                        if (
                            clientUrl.origin === urlToOpen.origin &&
                            "focus" in client
                        ) {
                            // Navigate existing client to the target URL and focus
                            client.navigate(urlToOpen.href);
                            return client.focus();
                        }
                    }
                    // No existing client found, open new window
                    if (clients.openWindow) {
                        return clients.openWindow(urlToOpen.href);
                    }
                }),
        );
    });

    // ============================================================================
    // Push Subscription Change Handler
    // ============================================================================
    // Fired when browser refreshes/expires the push subscription
    // This is the standard way to handle subscription expiration in 2026

    self.addEventListener("pushsubscriptionchange", (event) => {
        event.waitUntil(
            (async () => {
                try {
                    // Get VAPID public key from server
                    const vapidResponse = await fetch("/api/push/vapid-public-key/");
                    if (!vapidResponse.ok) throw new Error("Failed to get VAPID key");

                    const { publicKey } = await vapidResponse.json();

                    // Re-subscribe with same options
                    const newSubscription =
                        await self.registration.pushManager.subscribe({
                            userVisibleOnly: true,
                            applicationServerKey: urlBase64ToUint8Array(publicKey),
                        });

                    // Send new subscription to server (will match by device fingerprint)
                    const updateResponse = await fetch(
                        "/api/push/refresh-subscription/",
                        {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                            },
                            body: JSON.stringify({
                                oldEndpoint: event.oldSubscription?.endpoint,
                                subscription: {
                                    endpoint: newSubscription.endpoint,
                                    keys: {
                                        p256dh: arrayBufferToBase64(
                                            newSubscription.getKey("p256dh"),
                                        ),
                                        auth: arrayBufferToBase64(
                                            newSubscription.getKey("auth"),
                                        ),
                                    },
                                },
                            }),
                        },
                    );

                    if (!updateResponse.ok) {
                        throw new Error("Failed to update subscription on server");
                    }

                    // Notify all clients that subscription was refreshed
                    const clients = await self.clients.matchAll({ type: "window" });
                    clients.forEach((client) => {
                        client.postMessage({
                            type: "PUSH_SUBSCRIPTION_REFRESHED",
                            timestamp: Date.now(),
                        });
                    });
                } catch (error) {
                    // Log error but don't crash service worker
                    console.error("Failed to handle pushsubscriptionchange:", error);
                }
            })(),
        );
    });

    // Helper function to convert VAPID key
    function urlBase64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
        const base64 = (base64String + padding).replace(/\-/g, "+").replace(/_/g, "/");
        const rawData = atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }

    // Helper to convert ArrayBuffer to Base64
    function arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = "";
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    // ============================================================================
    // Update Handling
    // ============================================================================

    self.addEventListener("message", (event) => {
        if (event.data && event.data.type === "SKIP_WAITING") {
            self.skipWaiting();
        }
        // Capability handshake for htmx-error-toast.js: only a worker that
        // answers this posts "crush-queued" acknowledgements. A page still
        // controlled by an older worker gets no answer and knows not to read
        // a missing acknowledgement as "the request was not queued".
        if (event.data && event.data.type === "crush-capabilities?" && event.source) {
            event.waitUntil(
                (async () => {
                    let queued = null;
                    try {
                        queued = await crushQueue.size();
                    } catch (error) {
                        // unknown: the page keeps its own schedule
                    }
                    event.source.postMessage({
                        type: "crush-capabilities",
                        queuedAck: true,
                        queued,
                    });
                })(),
            );
        }
        // A page asked for a replay (it regained connectivity, or a queue
        // acknowledgement started its retry schedule) rather than waiting
        // for a Sync event that may never come (no Sync API, registration
        // failed) or for the next worker start. Answer with what is left so
        // the page keeps retrying until the queue is empty.
        if (event.data && event.data.type === "crush-drain-queue") {
            event.waitUntil(
                (async () => {
                    try {
                        await drainQueue(crushQueue);
                    } catch (error) {
                        // the failed entry is back in the queue
                    }
                    let remaining = null;
                    try {
                        remaining = await crushQueue.size();
                    } catch (error) {
                        // unknown: the page keeps retrying
                    }
                    if (event.source) {
                        event.source.postMessage({ type: "crush-drained", remaining });
                    }
                })(),
            );
        }
    });
} else {
    // Fallback: Basic service worker without Workbox
    self.addEventListener("fetch", (event) => {
        // Just pass through to network if Workbox failed
        event.respondWith(fetch(event.request));
    });
}
