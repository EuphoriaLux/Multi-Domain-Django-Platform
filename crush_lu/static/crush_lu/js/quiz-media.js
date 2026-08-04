/**
 * Shared, CSP-safe renderer for Quiz Night media stimuli.
 *
 * QuizMedia.render(container, media, options) remains backwards compatible.
 * The renderer now owns the common Night Stage frame and local load state in
 * addition to the underlying image/video/audio/embed element.
 */
(function (global) {
    "use strict";

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

    function sanitizeMediaUrl(url, source) {
        if (typeof url !== "string" || !url) return null;
        var u;
        try {
            u = new URL(url, global.location.origin);
        } catch (e) {
            return null;
        }
        var scheme = u.protocol.toLowerCase();
        if (scheme !== "http:" && scheme !== "https:") return null;

        if (source === "external") {
            var host = u.hostname.toLowerCase();
            for (var i = 0; i < EMBED_HOSTS.length; i++) {
                if (host === EMBED_HOSTS[i]) return u.href;
            }
            return null;
        }
        if (u.origin === global.location.origin) return u.href;
        if (scheme === "https:") return u.href;
        if (u.hostname === "127.0.0.1" || u.hostname === "localhost") return u.href;
        return null;
    }

    function clearChildren(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    function clear(container) {
        if (!container) return;
        var playable = container.querySelectorAll("audio, video, iframe");
        for (var i = 0; i < playable.length; i++) {
            var el = playable[i];
            try {
                if (el.pause) el.pause();
                if (el.contentWindow && el.contentWindow.postMessage) {
                    el.contentWindow.postMessage(
                        '{"event":"command","func":"stopVideo","args":""}',
                        "*",
                    );
                }
            } catch (e) {
                /* A cross-origin iframe can reject access while being removed. */
            }
        }
        clearChildren(container);
    }

    function makeStatus(label) {
        var status = document.createElement("span");
        status.className = "quiz-media__status";
        status.setAttribute("role", "status");

        var dot = document.createElement("span");
        dot.className = "quiz-media__status-dot";
        dot.setAttribute("aria-hidden", "true");
        status.appendChild(dot);

        var copy = document.createElement("span");
        copy.textContent = label;
        status.appendChild(copy);
        status._copy = copy;
        return status;
    }

    function renderUnavailable(container, opts) {
        var wrapper = document.createElement("div");
        wrapper.className = "quiz-media quiz-media--error";
        wrapper.setAttribute("data-status", "error");
        var status = makeStatus(opts.unavailableLabel || "Media unavailable");
        wrapper.appendChild(status);
        container.appendChild(wrapper);
    }

    function render(container, media, opts) {
        if (!container) return;
        clear(container);
        if (!media || media.kind === "none" || !media.url) return;

        var o = opts || {};
        var size = o.size === "display" ? "display" : "player";
        var safeUrl = sanitizeMediaUrl(media.url, media.source);
        if (!safeUrl) {
            renderUnavailable(container, o);
            return;
        }

        var description =
            typeof media.description === "string" && media.description.trim()
                ? media.description.trim()
                : "";
        var mediaLabel = description || o.mediaLabel || "Quiz media";
        var loadingLabel = o.loadingLabel || "Loading media";
        var readyLabel = o.readyLabel || "Media ready";

        var wrapper = document.createElement("div");
        wrapper.className =
            "quiz-media quiz-media--" + media.kind + " quiz-media--" + size;
        wrapper.setAttribute("data-status", "loading");
        wrapper.setAttribute("aria-busy", "true");

        var status = makeStatus(loadingLabel);
        if (!o.showReadyStatus) status.classList.add("quiz-media__status--auto-hide");
        wrapper.appendChild(status);

        var node = null;
        if (media.source === "external") {
            node = document.createElement("iframe");
            node.src = safeUrl;
            node.setAttribute("allowfullscreen", "");
            node.setAttribute("allow", "autoplay; encrypted-media; picture-in-picture");
            node.setAttribute("referrerpolicy", "strict-origin-when-cross-origin");
            node.title = mediaLabel;
            if (media.kind === "audio") node.style.minHeight = "9.5rem";
        } else if (media.kind === "image") {
            node = document.createElement("img");
            node.src = safeUrl;
            node.alt = mediaLabel;
        } else if (media.kind === "video") {
            node = document.createElement("video");
            node.src = safeUrl;
            node.setAttribute("controls", "");
            node.setAttribute("playsinline", "");
            node.setAttribute("aria-label", mediaLabel);
        } else if (media.kind === "audio") {
            node = document.createElement("audio");
            node.src = safeUrl;
            node.setAttribute("controls", "");
            node.setAttribute("aria-label", mediaLabel);
        }

        if (!node) {
            renderUnavailable(container, o);
            return;
        }

        var ready = function () {
            wrapper.setAttribute("data-status", "ready");
            wrapper.setAttribute("aria-busy", "false");
            status._copy.textContent = readyLabel;
        };
        var unavailable = function () {
            wrapper.setAttribute("data-status", "error");
            wrapper.setAttribute("aria-busy", "false");
            status.classList.remove("quiz-media__status--auto-hide");
            status._copy.textContent = o.unavailableLabel || "Media unavailable";
        };

        if (node.tagName === "IMG" || node.tagName === "IFRAME") {
            node.addEventListener("load", ready, { once: true });
        } else if (node.tagName === "VIDEO") {
            node.addEventListener("loadeddata", ready, { once: true });
        } else {
            node.addEventListener("loadedmetadata", ready, { once: true });
        }
        node.addEventListener("error", unavailable, { once: true });

        if (media.kind === "audio") {
            var art = document.createElement("div");
            art.className = "quiz-media__audio-art";
            art.setAttribute("aria-hidden", "true");
            art.textContent = "\u266B";

            var body = document.createElement("div");
            body.className = "quiz-media__audio-body";
            var label = document.createElement("p");
            label.className = "quiz-media__audio-label";
            label.textContent = o.audioLabel || "Audio";
            body.appendChild(label);
            body.appendChild(node);

            wrapper.appendChild(art);
            wrapper.appendChild(body);
        } else {
            wrapper.appendChild(node);
        }

        container.appendChild(wrapper);
    }

    global.QuizMedia = { render: render, clear: clear };
})(typeof window !== "undefined" ? window : this);
