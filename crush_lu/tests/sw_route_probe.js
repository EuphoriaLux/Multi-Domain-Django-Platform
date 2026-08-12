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
let backgroundSync = null;

function strategyName(name) {
    return function Strategy(opts) {
        this.__strategy = name;
        this.__opts = opts;
    };
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
    expiration: lenientNamespace(),
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
    caches: { open: async () => ({ match: async () => null, put: async () => {} }) },
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
        if (hit) return route.strategy;
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
];

const results = probes.map((probe) => {
    const request = {
        url: probe.url,
        mode: probe.mode,
        destination: probe.destination,
        method: probe.method || "GET",
        headers: { get: () => "" },
    };
    const early = earlyListenerClaims(request);
    const route = early ? null : matchingRoute(request);
    const claimed = early || route !== null;
    const strategyOk = !probe.mustMatchStrategy || route === probe.mustMatchStrategy;
    return {
        name: probe.name,
        url: probe.url,
        method: request.method,
        claimedByEarlyListener: early,
        matchedRoute: route,
        claimed,
        informational: !!probe.informational,
        mustBeClaimed: probe.informational ? null : probe.mustBeClaimed,
        mustMatchStrategy: probe.mustMatchStrategy || null,
        ok: probe.informational
            ? true
            : claimed === probe.mustBeClaimed && strategyOk,
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
