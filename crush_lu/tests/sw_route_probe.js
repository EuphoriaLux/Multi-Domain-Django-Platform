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
        registerRoute: (match, handler) => {
            routes.push({
                match,
                strategy: (handler && handler.__strategy) || "unknown",
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
    backgroundSync: lenientNamespace(),
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
];

const results = probes.map((probe) => {
    const request = {
        url: probe.url,
        mode: probe.mode,
        destination: probe.destination,
        method: "GET",
        headers: { get: () => "" },
    };
    const early = earlyListenerClaims(request);
    const route = early ? null : matchingRoute(request);
    const claimed = early || route !== null;
    return {
        name: probe.name,
        url: probe.url,
        claimedByEarlyListener: early,
        matchedRoute: route,
        claimed,
        informational: !!probe.informational,
        mustBeClaimed: probe.informational ? null : probe.mustBeClaimed,
        ok: probe.informational ? true : claimed === probe.mustBeClaimed,
    };
});

process.stdout.write(JSON.stringify({ routeCount: routes.length, results }, null, 2));
