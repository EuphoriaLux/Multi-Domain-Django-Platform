(function () {
    "use strict";

    var root = document.querySelector("[data-event-lobby]");
    if (!root) return;

    var banner = document.getElementById("event-lobby-live-banner");
    var stateUrl = root.getAttribute("data-state-url");
    var participantsUrl = root.getAttribute("data-participants-url");
    var eventId = root.getAttribute("data-event-id");
    var baseline = null;
    var reloadTimer = null;
    var reconnectTimer = null;

    function messageFor(reason) {
        if (reason === "participant_joined") {
            return root.getAttribute("data-arrival-message");
        }
        if (reason === "incoming_signal") {
            return root.getAttribute("data-signal-message");
        }
        if (reason === "mutual_revealed") {
            return root.getAttribute("data-mutual-message");
        }
        return root.getAttribute("data-update-message");
    }

    function showBanner(reason) {
        if (!banner) return;
        banner.textContent = messageFor(reason);
        banner.classList.remove("hidden");
    }

    function scheduleReload(reason) {
        if (reloadTimer) return;
        showBanner(reason);
        reloadTimer = window.setTimeout(function () {
            window.location.reload();
        }, 1200);
    }

    function fetchJson(url) {
        return window.fetch(url, {
            credentials: "same-origin",
            cache: "no-store",
            headers: { Accept: "application/json" },
        }).then(function (response) {
            if (!response.ok) throw new Error("Lobby state unavailable");
            return response.json();
        });
    }

    function poll() {
        if (document.hidden || reloadTimer) return;
        Promise.all([fetchJson(stateUrl), fetchJson(participantsUrl)])
            .then(function (payloads) {
                var fingerprint = JSON.stringify(payloads);
                if (baseline !== null && baseline !== fingerprint) {
                    scheduleReload("poll_changed");
                }
                baseline = fingerprint;
            })
            .catch(function () {
                // The server remains authoritative; a later poll or navigation
                // will recover without exposing any extra lobby information.
            });
    }

    function connect() {
        if (!window.WebSocket || reloadTimer) return;
        var scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
        var opened = false;
        var socket = new window.WebSocket(
            scheme + "//" + window.location.host + "/ws/event-lobby/" + eventId + "/",
        );
        socket.onopen = function () {
            opened = true;
        };
        socket.onmessage = function (event) {
            try {
                var payload = JSON.parse(event.data);
                if (payload.type === "event_lobby.refresh") {
                    scheduleReload(payload.reason);
                }
            } catch (_error) {
                // Ignore malformed hints; HTTP polling remains authoritative.
            }
        };
        socket.onclose = function () {
            // WSGI-only local development cannot upgrade WebSockets. One
            // failed handshake is enough; polling remains the fallback.
            if (!opened || reconnectTimer || reloadTimer) return;
            reconnectTimer = window.setTimeout(function () {
                reconnectTimer = null;
                connect();
            }, 5000);
        };
    }

    poll();
    window.setInterval(poll, 15000);
    document.addEventListener("visibilitychange", poll);
    connect();
})();
