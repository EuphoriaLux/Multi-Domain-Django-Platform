/**
 * Service-worker routing probe.
 *
 * Loads sw-workbox.js in a sandbox with a recording `workbox` stub, then
 * reports, for each probe request, whether the service worker would CLAIM it
 * (either via an early `event.respondWith(...)` listener or via a Workbox
 * route) or leave it to the browser.
 *
 * This exists because source-string assertions cannot catch the failure that
 * actually matters: if the SW claims the native auth handoff navigation, its
 * fetch() must follow a 302 to crushlu:// — which fetch() cannot do — and the
 * iOS auth sheet hangs after a successful login.
 *
 * Usage: node sw_route_probe.js <path-to-sw-workbox.js>
 * Emits JSON on stdout.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const swPath = process.argv[2];
const source = fs.readFileSync(swPath, "utf8");

const routes = [];
const fetchListeners = [];
const deletedCaches = [];
let backgroundSync = null;

function strategyName(name) {
    return function Strategy(opts) {
        this.__strategy = name;
        this.__opts = opts;
    };
}

/** The ExpirationPlugin options a caching strategy was built with, if any. */
function expirationOf(handler) {
    const plugins = (handler && handler.__opts && handler.__opts.plugins) || [];
    const plugin = plugins.find((p) => p && p.__expiration);
    return plugin ? plugin.__expiration : null;
}

// Any workbox.<ns>.<Thing> we don't model explicitly becomes a no-op constructor.
function lenientNamespace(extra = {}) {
    return new Proxy(extra, {
        get(target, prop) {
            if (prop in target) return target[prop];
            const fn = function () {};
            fn.prototype = {};
            return fn;
        },
    });
}

const workbox = {
    setConfig: () => {},
    core: lenientNamespace({
        setCacheNameDetails: () => {},
        clientsClaim: () => {},
        skipWaiting: () => {},
    }),
    routing: lenientNamespace({
        // Workbox keeps one router per HTTP method and defaults to GET, so a
        // POST probe must only be matched against POST-registered routes.
        // Ignoring the method here made every probe look like it hit the
        // background-sync route.
        registerRoute: (match, handler, method) => {
            routes.push({
                match,
                strategy: (handler && handler.__strategy) || "unknown",
                // Which cache a caching strategy writes to: two NetworkFirst
                // routes differ only here (e.g. crush-tickets vs crush-pages).
                cacheName: (handler && handler.__opts && handler.__opts.cacheName) || null,
                expiration: expirationOf(handler),
                method: method || "GET",
            });
        },
        setCatchHandler: () => {},
        setDefaultHandler: () => {},
        NavigationRoute: function (handler) {
            this.handler = handler;
        },
    }),
    strategies: lenientNamespace({
        NetworkFirst: strategyName("NetworkFirst"),
        NetworkOnly: strategyName("NetworkOnly"),
        CacheFirst: strategyName("CacheFirst"),
        StaleWhileRevalidate: strategyName("StaleWhileRevalidate"),
        CacheOnly: strategyName("CacheOnly"),
    }),
    precaching: lenientNamespace({
        precacheAndRoute: () => {},
        cleanupOutdatedCaches: () => {},
        createHandlerBoundToURL: () => () => {},
    }),
    expiration: lenientNamespace({
        // Recorded so a probe can see how long a route's cache keeps entries.
        ExpirationPlugin: function (opts) {
            this.__expiration = opts || {};
        },
    }),
    cacheableResponse: lenientNamespace(),
    // Captured rather than stubbed away: the route predicate only governs what
    // ENTERS the queue, so the replay loop has to be probed on its own.
    backgroundSync: lenientNamespace({
        BackgroundSyncPlugin: function (queueName, options) {
            backgroundSync = { queueName, options: options || {} };
        },
    }),
    recipes: lenientNamespace(),
    rangeRequests: lenientNamespace(),
    broadcastUpdate: lenientNamespace(),
};

const self = {
    addEventListener: (type, handler) => {
        if (type === "fetch") fetchListeners.push(handler);
    },
    location: new URL("https://crush.lu/sw-workbox.js"),
    clients: { matchAll: async () => [], claim: async () => {}, openWindow: async () => {} },
    registration: { showNotification: async () => {}, scope: "https://crush.lu/" },
    skipWaiting: () => {},
    caches: {
        open: async () => ({ match: async () => null, put: async () => {} }),
        keys: async () => [],
        // Recorded so a probe can see which caches a request purges.
        delete: async (name) => {
            deletedCaches.push(name);
            return true;
        },
    },
    __WB_DISABLE_DEV_LOGS: true,
};

const sandbox = {
    self,
    workbox,
    location: self.location,
    importScripts: () => {},
    console: { log: () => {}, warn: () => {}, error: () => {}, info: () => {}, debug: () => {} },
    URL,
    Request,
    Response,
    fetch: async () => new Response(""),
    caches: self.caches,
    clients: self.clients,
    setTimeout,
    Date,
};
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: path.basename(swPath) });

/** Does an early fetch listener claim this request via respondWith()? */
function earlyListenerClaims(request) {
    let claimed = false;
    const event = {
        request,
        respondWith: () => {
            claimed = true;
        },
        waitUntil: () => {},
    };
    for (const listener of fetchListeners) {
        try {
            listener(event);
        } catch (e) {
            /* the SW may reference APIs we do not model; ignore */
        }
        if (claimed) break;
    }
    return claimed;
}

/** First Workbox route that matches, mimicking registration-order evaluation. */
function matchingRoute(request) {
    const url = new URL(request.url);
    for (const route of routes) {
        if (route.method !== (request.method || "GET")) continue;
        let hit = false;
        try {
            hit = !!route.match({ url, request, event: { request } });
        } catch (e) {
            hit = false;
        }
        if (hit) return route;
    }
    return null;
}

const probes = [
    {
        name: "ios_handoff_navigation",
        url: "https://crush.lu/api/mobile/ios/auth/handoff/?redirect_uri=crushlu://auth",
        mode: "navigate",
        destination: "document",
        mustBeClaimed: false, // its 302 -> crushlu:// can only be followed by the browser
    },
    {
        name: "android_handoff_navigation",
        url: "https://crush.lu/api/mobile/android/auth/handoff/?redirect_uri=crushlu://auth",
        mode: "navigate",
        destination: "document",
        mustBeClaimed: false,
    },
    {
        // INFORMATIONAL, not asserted. The hard-bypass listener returns early
        // for auth navigations intending "full browser bypass", but the
        // /accounts/ NetworkOnly route claims them anyway. Harmless today —
        // those redirects are all https, which fetch() follows — but it does
        // contradict the stated intent. Pre-existing; out of scope here.
        name: "oauth_callback_navigation",
        url: "https://crush.lu/accounts/google/login/callback/?code=x&state=y",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
    {
        name: "device_register_xhr",
        url: "https://crush.lu/api/mobile/ios/devices/register/",
        mode: "cors",
        destination: "empty",
        mustBeClaimed: true, // fine to claim: no custom-scheme redirect involved
    },
    {
        name: "ordinary_page_navigation",
        url: "https://crush.lu/en/events/",
        mode: "navigate",
        destination: "document",
        mustBeClaimed: true, // proves the probe detects claiming at all
    },
    {
        // A queued admin POST is replayed verbatim for up to 24h, which
        // re-submits the change form's inline rows and duplicates whatever the
        // first submission already wrote. Staging 2026-08-11: a second
        // EventRegistration INSERT for (event 29, user 86) came back as a 500
        // on the unique index.
        name: "crush_admin_form_post",
        url: "https://crush.lu/crush-admin/crush_lu/meetupevent/29/change/",
        method: "POST",
        mode: "same-origin",
        destination: "",
        mustBeClaimed: false,
    },
    {
        // The admin lives at /crush-admin/, which does not contain "/admin" —
        // so it slipped past the authenticated-route list and its pages were
        // cacheable. A cached change form carries stale inline ids.
        name: "crush_admin_page_navigation",
        url: "https://crush.lu/crush-admin/crush_lu/meetupevent/29/change/",
        mode: "navigate",
        destination: "document",
        mustBeClaimed: true,
        mustMatchStrategy: "NetworkOnly",
    },
    {
        // Guards the method-aware probe: ordinary site POSTs must KEEP their
        // background-sync queueing, which is the whole point of the route.
        name: "ordinary_form_post",
        url: "https://crush.lu/en/events/29/register/",
        method: "POST",
        mode: "same-origin",
        destination: "",
        mustBeClaimed: true,
    },
    {
        // The ticket is what an attendee opens at the venue door, often with
        // no signal. It needs its own cache: "crush-pages" is shared with
        // every page and expires after 24h.
        name: "ticket_navigation_en",
        url: "https://crush.lu/en/events/29/ticket/",
        mode: "navigate",
        destination: "document",
        mustBeClaimed: true,
        mustMatchStrategy: "NetworkFirst",
        mustMatchCache: "crush-tickets",
    },
    {
        name: "ticket_navigation_de",
        url: "https://crush.lu/de/events/29/ticket/",
        mode: "navigate",
        destination: "document",
        mustBeClaimed: true,
        mustMatchStrategy: "NetworkFirst",
        mustMatchCache: "crush-tickets",
    },
    {
        name: "ticket_navigation_fr",
        url: "https://crush.lu/fr/events/29/ticket/",
        mode: "navigate",
        destination: "document",
        mustBeClaimed: true,
        mustMatchStrategy: "NetworkFirst",
        mustMatchCache: "crush-tickets",
    },
    {
        // The ticket route must not swallow its neighbours.
        name: "event_detail_navigation",
        url: "https://crush.lu/en/events/29/",
        mode: "navigate",
        destination: "document",
        mustBeClaimed: true,
        mustMatchStrategy: "NetworkFirst",
        mustMatchCache: "crush-pages",
    },
    {
        // Session boundaries purge the offline tickets: the ticket URL is
        // keyed by event, not user, so the next account on the device would
        // otherwise be shown the previous account's QR offline. Asserted on
        // `purgedCaches` by the tests; claiming is informational here (the
        // NetworkOnly auth route claims these, as with the OAuth probe above).
        name: "logout_navigation",
        url: "https://crush.lu/en/logout/",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
    {
        name: "login_navigation",
        url: "https://crush.lu/fr/login/",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
    {
        name: "signup_navigation",
        url: "https://crush.lu/de/signup/",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
    {
        // The native app's WebView redeems its one-time code here and is
        // signed in as that code's user (native_auth.complete_native_auth)
        // without ever visiting a /login page.
        name: "native_auth_complete_navigation",
        url: "https://crush.lu/api/mobile/android/auth/complete/abc123/",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
    {
        // Accepting a guest invitation creates the guest's account and signs
        // it in on this POST (views_invitations.invitation_accept).
        name: "invite_accept_post_navigation",
        url: "https://crush.lu/en/invite/0b6f3c52-8a4e-4f7e-9f7a-2d7c1e4b9a10/accept/",
        method: "POST",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
    {
        // A full account deletion POST calls logout() and redirects home
        // (views_account.gdpr_data_management), never visiting /logout.
        name: "gdpr_delete_post_navigation",
        url: "https://crush.lu/en/account/gdpr/",
        method: "POST",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
    {
        // Same view behind the legacy URL.
        name: "legacy_account_delete_post_navigation",
        url: "https://crush.lu/de/account/delete/",
        method: "POST",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
    {
        // Guards the POST-only rule: opening the GDPR page keeps tickets.
        name: "gdpr_page_navigation",
        url: "https://crush.lu/fr/account/gdpr/",
        mode: "navigate",
        destination: "document",
        informational: true,
    },
];

const results = probes.map((probe) => {
    const request = {
        url: probe.url,
        mode: probe.mode,
        destination: probe.destination,
        method: probe.method || "GET",
        headers: { get: () => "" },
    };
    deletedCaches.length = 0;
    const early = earlyListenerClaims(request);
    const purgedCaches = deletedCaches.slice();
    const route = early ? null : matchingRoute(request);
    const strategy = route ? route.strategy : null;
    const claimed = early || route !== null;
    const strategyOk = !probe.mustMatchStrategy || strategy === probe.mustMatchStrategy;
    const cacheOk = !probe.mustMatchCache || (route && route.cacheName) === probe.mustMatchCache;
    return {
        name: probe.name,
        url: probe.url,
        method: request.method,
        claimedByEarlyListener: early,
        matchedRoute: strategy,
        matchedCacheName: route ? route.cacheName : null,
        matchedExpiration: route ? route.expiration : null,
        purgedCaches,
        claimed,
        informational: !!probe.informational,
        mustBeClaimed: probe.informational ? null : probe.mustBeClaimed,
        mustMatchStrategy: probe.mustMatchStrategy || null,
        mustMatchCache: probe.mustMatchCache || null,
        ok: probe.informational
            ? true
            : claimed === probe.mustBeClaimed && strategyOk && cacheOk,
    };
});

/**
 * Drive the background-sync plugin's onSync over a queue that ALREADY holds
 * these requests, as a store filled by an earlier worker version would.
 * Returns the URLs it actually re-fetched.
 */
async function probeReplay(urls) {
    if (!backgroundSync || typeof backgroundSync.options.onSync !== "function") {
        return { available: false, replayed: [], drained: false };
    }
    const pending = urls.map((url) => ({ request: { url, method: "POST" } }));
    const queue = {
        shiftRequest: async () => pending.shift(),
        unshiftRequest: async (entry) => {
            pending.unshift(entry);
        },
    };
    const replayed = [];
    const realFetch = sandbox.fetch;
    sandbox.fetch = async (request) => {
        replayed.push(request.url);
        return new Response("");
    };
    try {
        await backgroundSync.options.onSync({ queue });
    } finally {
        sandbox.fetch = realFetch;
    }
    // A skipped entry must be shifted OUT, not left behind to try again.
    return { available: true, replayed, drained: pending.length === 0 };
}

(async () => {
    const replay = await probeReplay([
        "https://crush.lu/crush-admin/crush_lu/meetupevent/29/change/",
        "https://crush.lu/en/events/29/register/",
    ]);
    process.stdout.write(
        JSON.stringify({ routeCount: routes.length, results, replay }, null, 2),
    );
})();
