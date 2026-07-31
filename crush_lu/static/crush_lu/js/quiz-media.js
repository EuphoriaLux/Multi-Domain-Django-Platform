/**
 * quiz-media.js — shared, CSP-safe renderer for Quiz Night media stimuli.
 *
 * The Quiz Night templates all run under the Alpine.js CSP build, so media is
 * built imperatively with document.createElement (no x-for, no inline bindings).
 * Mirrors the _avatarNode URL-sanitization pattern in quiz-display.js: every
 * URL assigned to an element attribute must pass an allowlist check so a
 * tampered WebSocket payload (e.g. javascript:/data:) can never reach the DOM.
 *
 * Exposes a single global `QuizMedia` with `render(container, media, opts)`.
 *  - container: the DOM node to fill (cleared first)
 *  - media:     { kind: "none"|"image"|"video"|"audio",
 *                 url:  str|null,
 *                 source: "upload"|"external"|null }
 *  - opts:      { size: "player"|"display" } — sizing variant
 *
 * Returns nothing. A "none"/empty media renders nothing (container is cleared).
 */
(function (global) {
    "use strict";

    // Hosts allowed for external embeds. MUST stay in sync with the Python
    // allowlist QUIZ_EMBED_HOSTS in crush_lu/models/quiz.py — the server only
    // ever broadcasts already-normalized embed URLs, but we re-check on the
    // client as defense-in-depth (never trust a WS payload).
    var EMBED_HOSTS = [
        "www.youtube-nocookie.com",
        "youtube.com",
        "www.youtube.com",
        "player.vimeo.com",
        "open.spotify.com",
        "w.soundcloud.com",
        "soundcloud.com",
    ];

    function isAllowedEmbedUrl(url) {
        if (typeof url !== "string" || !url) return false;
        var u;
        try {
            u = new URL(url, global.location.origin);
        } catch (e) {
            return false;
        }
        // Reject anything that isn't http(s) — blocks javascript:/data:.
        if (u.protocol !== "http:" && u.protocol !== "https:") return false;
        return EMBED_HOSTS.indexOf(u.hostname.toLowerCase()) !== -1;
    }

    // Uploaded media is served from the crush-media origin (Azure Blob host or,
    // in dev, the same origin under /media/). Allow both.
    function isAllowedUploadUrl(url) {
        if (typeof url !== "string" || !url) return false;
        var u;
        try {
            u = new URL(url, global.location.origin);
        } catch (e) {
            return false;
        }
        if (u.protocol !== "http:" && u.protocol !== "https:") return false;
        // Same-origin (dev /media/) is always fine.
        if (u.origin === global.location.origin) return true;
        // Otherwise require https in production (Azure Blob).
        return u.protocol === "https:";
    }

    function clear(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    function render(container, media, opts) {
        if (!container) return;
        clear(container);
        if (!media || media.kind === "none" || !media.url) return;

        var o = opts || {};
        var isDisplay = o.size === "display";

        var node = null;

        if (media.source === "external") {
            if (!isAllowedEmbedUrl(media.url)) return;
            node = document.createElement("iframe");
            node.src = media.url;
            node.setAttribute("allowfullscreen", "");
            node.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture");
            node.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
            node.className = isDisplay
                ? "w-full rounded-2xl bg-black"
                : "w-full rounded-xl bg-black";
            node.style.aspectRatio = media.kind === "audio" ? "auto" : "16 / 9";
            node.style.border = "0";
            node.style.minHeight = media.kind === "audio" ? "152px" : "";
        } else if (media.kind === "image") {
            if (!isAllowedUploadUrl(media.url)) return;
            node = document.createElement("img");
            node.src = media.url;
            node.alt = "";
            node.className = isDisplay
                ? "w-full max-h-[55vh] object-contain rounded-2xl bg-black/40"
                : "w-full max-h-72 object-contain rounded-xl bg-black/40";
        } else if (media.kind === "video") {
            if (!isAllowedUploadUrl(media.url)) return;
            node = document.createElement("video");
            node.src = media.url;
            node.setAttribute("controls", "");
            node.setAttribute("playsinline", "");
            node.className = isDisplay
                ? "w-full max-h-[55vh] rounded-2xl bg-black"
                : "w-full max-h-72 rounded-xl bg-black";
        } else if (media.kind === "audio") {
            if (!isAllowedUploadUrl(media.url)) return;
            node = document.createElement("audio");
            node.src = media.url;
            node.setAttribute("controls", "");
            node.className = "w-full";
        }

        if (node) container.appendChild(node);
    }

    global.QuizMedia = { render: render };
})(typeof window !== "undefined" ? window : this);
