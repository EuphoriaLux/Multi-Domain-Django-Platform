/**
 * quiz-media.js — shared, CSP-safe renderer for Quiz Night media stimuli.
 *
 * The Quiz Night templates all run under the Alpine.js CSP build, so media is
 * built imperatively with document.createElement (no x-for, no inline bindings).
 * Mirrors the _avatarNode URL-sanitization pattern in quiz-display.js: every
 * URL assigned to an element attribute is reduced to either itself (validated)
 * or null via a single sanitizeMediaUrl() helper, and only the sanitized local
 * is ever assigned to .src. This is recognised by CodeQL as a sanitizer for
 * js/xss + js/client-side-unvalidated-url-redirection.
 *
 * Exposes a single global `QuizMedia` with:
 *  - render(container, media, opts): fill *container* (cleared first)
 *  - clear(container): tear down any rendered media, halting playback
 *
 * media = { kind: "none"|"image"|"video"|"audio",
 *           url:  str|null,
 *           source: "upload"|"external"|null,
 *           description: str }   // optional alt text / accessible label
 * opts  = { size: "player"|"display" } — sizing variant
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
        "vimeo.com",
        "open.spotify.com",
        "w.soundcloud.com",
        "soundcloud.com",
    ];

    /**
     * Return *url* if it is safe to assign for the given media source, else null.
     *
     * - external: must be http(s) and on the embed allowlist
     * - upload:   must be http(s); same-origin or https (Azure Blob in prod)
     *
     * Returning the validated URL (not a boolean) keeps the sanitized value on
     * the same data-flow line as the eventual `.src` assignment, which is the
     * shape CodeQL recognises as a sanitizer.
     */
    function sanitizeMediaUrl(url, source) {
        if (typeof url !== "string" || !url) return null;
        var u;
        try {
            u = new URL(url, global.location.origin);
        } catch (e) {
            return null;
        }
        if (u.protocol !== "http:" && u.protocol !== "https:") return null;
        if (source === "external") {
            var host = u.hostname.toLowerCase();
            for (var i = 0; i < EMBED_HOSTS.length; i++) {
                if (host === EMBED_HOSTS[i]) return url;
            }
            return null;
        }
        // upload: same-origin (dev /media/) always allowed, otherwise require https.
        if (u.origin === global.location.origin) return url;
        return u.protocol === "https:" ? url : null;
    }

    function clearChildren(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    /**
     * Tear down rendered media: pausing/removing the node halts any in-flight
     * audio/video/iframe playback so it cannot bleed into other screens.
     */
    function clear(container) {
        if (!container) return;
        var kids = container.childNodes;
        for (var i = 0; i < kids.length; i++) {
            var el = kids[i];
            try {
                if (el.pause) el.pause();
                if (el.contentWindow && el.contentWindow.postMessage) {
                    // Best-effort stop for YouTube/Vimeo embeds.
                    el.contentWindow.postMessage('{"event":"command","func":"stopVideo","args":""}', "*");
                }
            } catch (e) {
                /* cross-origin iframe — ignore */
            }
        }
        clearChildren(container);
    }

    function render(container, media, opts) {
        if (!container) return;
        clear(container);
        if (!media || media.kind === "none" || !media.url) return;

        var o = opts || {};
        var isDisplay = o.size === "display";
        // Sanitize first; a null safeUrl means we render nothing.
        var safeUrl = sanitizeMediaUrl(media.url, media.source);
        if (!safeUrl) return;

        var description =
            typeof media.description === "string" && media.description.trim()
                ? media.description.trim()
                : "";

        var node = null;

        if (media.source === "external") {
            node = document.createElement("iframe");
            node.src = safeUrl;
            node.setAttribute("allowfullscreen", "");
            node.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture");
            node.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
            node.title = description;
            node.className = isDisplay
                ? "w-full rounded-2xl bg-black"
                : "w-full rounded-xl bg-black";
            node.style.aspectRatio = media.kind === "audio" ? "auto" : "16 / 9";
            node.style.border = "0";
            node.style.minHeight = media.kind === "audio" ? "152px" : "";
        } else if (media.kind === "image") {
            node = document.createElement("img");
            node.src = safeUrl;
            // Empty alt = decorative ONLY when no description is provided; when
            // the stimulus is the question (e.g. "what city is this?"), an
            // author-provided description is used so screen readers get it.
            node.alt = description;
            node.className = isDisplay
                ? "w-full max-h-[55vh] object-contain rounded-2xl bg-black/40"
                : "w-full max-h-72 object-contain rounded-xl bg-black/40";
        } else if (media.kind === "video") {
            node = document.createElement("video");
            node.src = safeUrl;
            node.setAttribute("controls", "");
            node.setAttribute("playsinline", "");
            node.className = isDisplay
                ? "w-full max-h-[55vh] rounded-2xl bg-black"
                : "w-full max-h-72 rounded-xl bg-black";
        } else if (media.kind === "audio") {
            node = document.createElement("audio");
            node.src = safeUrl;
            node.setAttribute("controls", "");
            node.setAttribute("aria-label", description);
            node.className = "w-full";
        }

        if (node) container.appendChild(node);
    }

    global.QuizMedia = { render: render, clear: clear };
})(typeof window !== "undefined" ? window : this);
