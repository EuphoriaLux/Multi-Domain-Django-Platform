/**
 * Crush Cache — player navigation component.
 *
 * Watches the device position, posts fixes to the server (which alone
 * decides "arrived"), renders the distance / compass readout, and keeps
 * an optional Leaflet map (map navigation mode) in sync. When the server
 * reports the current station as arrived/unlocked, the page reloads so
 * the server-rendered state machine advances.
 *
 * Live updates arrive over the native WebSocket API; a 20 s polling
 * fallback against the state API keeps the game moving when the channel
 * layer is down (HTTP gameplay must never depend on Redis).
 *
 * User-facing strings come from data-msg-* attributes on the root
 * element so templates translate them ({% trans %}); the literals here
 * are English fallbacks only.
 */
document.addEventListener("alpine:init", function () {
    Alpine.data("cachePlay", function () {
        return {
            geoSupported: !!navigator.geolocation,
            distanceM: null,
            bearing: null,
            heading: null,
            gpsStatus: "",
            arrived: false,
            unlocked: false,
            compassNeedsPermission: false,
            hasAbsoluteHeading: false,
            watchId: null,
            lastPostAt: 0,
            posting: false,
            reloading: false,
            map: null,
            selfMarker: null,
            accuracyCircle: null,
            ws: null,
            wsRetry: 0,
            pollTimer: null,

            init: function () {
                var root = this.$el;
                this.positionUrl = root.dataset.positionUrl;
                this.stateUrl = root.dataset.stateUrl;
                this.huntId = root.dataset.huntId;
                this.huntStatus = root.dataset.huntStatus;
                this.navMode = root.dataset.navMode;
                this.needsGps = root.dataset.needsGps === "true";
                this.unlocked = root.dataset.unlocked === "true";
                this.targetLat = root.dataset.targetLat ? parseFloat(root.dataset.targetLat) : null;
                this.targetLng = root.dataset.targetLng ? parseFloat(root.dataset.targetLng) : null;
                this.targetRadius = root.dataset.targetRadius ? parseInt(root.dataset.targetRadius, 10) : null;
                this.msgs = {
                    gpsDenied: root.dataset.msgGpsDenied || "Location permission denied",
                    gpsWaiting: root.dataset.msgGpsWaiting || "Waiting for GPS…",
                    gpsAccuracy: root.dataset.msgGpsAccuracy || "GPS accuracy: ±{n} m",
                };
                try {
                    this.completedStations = JSON.parse(root.dataset.completedStations || "[]");
                } catch (e) {
                    this.completedStations = [];
                }

                if (this.huntStatus === "live") {
                    this.startWatching();
                    this.connectWebSocket();
                    this.startPolling();
                }
                if (document.getElementById("cache-map") && window.L) {
                    this.initMap();
                }
                if (this.navMode === "compass") {
                    this.listenToCompass();
                }
            },

            destroy: function () {
                this.stopWatching();
                if (this.pollTimer !== null) {
                    clearInterval(this.pollTimer);
                }
                if (this.ws) {
                    this.ws.onclose = null;
                    this.ws.close();
                }
            },

            // --- Display helpers ---

            get distanceDisplay() {
                if (this.distanceM === null) return "…";
                if (this.distanceM >= 1000) {
                    return (this.distanceM / 1000).toFixed(1) + " km";
                }
                return Math.round(this.distanceM) + " m";
            },

            get arrowStyle() {
                if (this.bearing === null) return "";
                var rotation = this.bearing - (this.heading || 0);
                return "transform: rotate(" + rotation + "deg)";
            },

            // --- Geolocation ---

            startWatching: function () {
                var self = this;
                if (!navigator.geolocation) {
                    this.geoSupported = false;
                    return;
                }
                this.watchId = navigator.geolocation.watchPosition(
                    function (pos) { self.onPosition(pos); },
                    function (err) {
                        self.gpsStatus = err.code === err.PERMISSION_DENIED
                            ? self.msgs.gpsDenied
                            : self.msgs.gpsWaiting;
                    },
                    { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 }
                );
            },

            stopWatching: function () {
                if (this.watchId !== null && navigator.geolocation) {
                    navigator.geolocation.clearWatch(this.watchId);
                    this.watchId = null;
                }
            },

            onPosition: function (pos) {
                var lat = pos.coords.latitude;
                var lng = pos.coords.longitude;
                var accuracy = pos.coords.accuracy || 0;

                this.updateSelfMarker(lat, lng, accuracy);
                this.gpsStatus = this.msgs.gpsAccuracy.replace("{n}", Math.round(accuracy));

                // Throttle server posts to one every 3 seconds
                var now = Date.now();
                if (this.posting || now - this.lastPostAt < 3000) return;
                this.lastPostAt = now;
                this.postPosition(lat, lng, accuracy);
            },

            postPosition: function (lat, lng, accuracy) {
                var self = this;
                this.posting = true;
                fetch(this.positionUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": this.getCsrfToken(),
                    },
                    body: JSON.stringify({ lat: lat, lng: lng, accuracy: accuracy }),
                })
                    .then(function (r) {
                        if (r.status === 403) {
                            return r.json()
                                .catch(function () { return null; })
                                .then(function (err) {
                                    var code = err && err.error;
                                    if (code === "finished" || code === "not_live" || code === "no_team") {
                                        // Hunt or membership state changed under
                                        // us — stop posting, re-render from the
                                        // server (e.g. show the finish screen).
                                        self.stopAndReload();
                                    } else {
                                        // Unknown 403 (e.g. CSRF): stop posting
                                        // but never enter a reload loop.
                                        self.stopWatching();
                                    }
                                    return null;
                                });
                        }
                        return r.ok ? r.json() : null;
                    })
                    .then(function (data) {
                        self.posting = false;
                        if (!data || !data.ok) return;
                        if (typeof data.distance_m === "number") self.distanceM = data.distance_m;
                        if (typeof data.bearing === "number") self.bearing = data.bearing;
                        // Server-side arrival — reload so the state machine advances
                        if (self.needsGps && (data.arrived || data.unlocked)) {
                            window.location.reload();
                        }
                    })
                    .catch(function () { self.posting = false; });
            },

            stopAndReload: function () {
                if (this.reloading) return;
                this.reloading = true;
                this.stopWatching();
                window.location.reload();
            },

            // --- Compass (device orientation) ---

            listenToCompass: function () {
                if (!window.DeviceOrientationEvent) return;
                if (typeof DeviceOrientationEvent.requestPermission === "function") {
                    // iOS: orientation events only flow after a user-gesture
                    // permission grant — surface an "enable compass" button.
                    this.compassNeedsPermission = true;
                    return;
                }
                this.attachCompass();
            },

            enableCompass: function () {
                var self = this;
                if (typeof DeviceOrientationEvent.requestPermission !== "function") {
                    this.compassNeedsPermission = false;
                    this.attachCompass();
                    return;
                }
                DeviceOrientationEvent.requestPermission()
                    .then(function (state) {
                        self.compassNeedsPermission = false;
                        if (state === "granted") self.attachCompass();
                    })
                    .catch(function () { self.compassNeedsPermission = false; });
            },

            attachCompass: function () {
                var self = this;
                window.addEventListener("deviceorientationabsolute", function (e) {
                    if (e.alpha !== null) {
                        self.hasAbsoluteHeading = true;
                        self.heading = 360 - e.alpha;
                    }
                }, true);
                window.addEventListener("deviceorientation", function (e) {
                    if (typeof e.webkitCompassHeading === "number") {
                        // iOS Safari: degrees clockwise from north, ready to use
                        self.heading = e.webkitCompassHeading;
                    } else if (!self.hasAbsoluteHeading && e.absolute && e.alpha !== null) {
                        self.heading = 360 - e.alpha;
                    }
                }, true);
            },

            // --- Leaflet map (map navigation mode only) ---

            initMap: function () {
                var center = this.targetLat !== null
                    ? [this.targetLat, this.targetLng]
                    : [49.6116, 6.1319]; // Luxembourg City fallback

                this.map = L.map("cache-map").setView(center, 16);
                L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
                    maxZoom: 19,
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                }).addTo(this.map);

                if (this.targetLat !== null) {
                    L.marker([this.targetLat, this.targetLng]).addTo(this.map);
                    if (this.targetRadius) {
                        L.circle([this.targetLat, this.targetLng], {
                            radius: this.targetRadius,
                            color: "#8b5cf6",
                            fillOpacity: 0.1,
                        }).addTo(this.map);
                    }
                }

                var self = this;
                (this.completedStations || []).forEach(function (s) {
                    if (s.lat === null || s.lng === null) return;
                    L.circleMarker([s.lat, s.lng], {
                        radius: 7,
                        color: "#22c55e",
                        fillOpacity: 0.8,
                    }).bindPopup("✅ " + s.order + ". " + s.name).addTo(self.map);
                });
            },

            updateSelfMarker: function (lat, lng, accuracy) {
                if (!this.map || !window.L) return;
                if (!this.selfMarker) {
                    this.selfMarker = L.circleMarker([lat, lng], {
                        radius: 8,
                        color: "#3b82f6",
                        fillColor: "#3b82f6",
                        fillOpacity: 0.9,
                    }).addTo(this.map);
                    this.accuracyCircle = L.circle([lat, lng], {
                        radius: accuracy,
                        color: "#3b82f6",
                        weight: 1,
                        fillOpacity: 0.05,
                    }).addTo(this.map);
                } else {
                    this.selfMarker.setLatLng([lat, lng]);
                    this.accuracyCircle.setLatLng([lat, lng]);
                    this.accuracyCircle.setRadius(accuracy);
                }
            },

            // --- WebSocket: hunt status changes ---

            connectWebSocket: function () {
                var self = this;
                var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                var url = protocol + "//" + window.location.host + "/ws/cache/" + this.huntId + "/";
                this.ws = new WebSocket(url);
                this.ws.onmessage = function (event) {
                    var msg;
                    try { msg = JSON.parse(event.data); } catch (e) { return; }
                    // The consumer sends "state" on connect and "status" on
                    // every change; both carry the hunt status. React to
                    // either so a status change that happened between page
                    // render and group subscription still lands.
                    if ((msg.type === "status" || msg.type === "state") && msg.data && msg.data.status && msg.data.status !== self.huntStatus) {
                        self.stopAndReload();
                    }
                };
                this.ws.onclose = function () {
                    if (self.wsRetry < 5) {
                        self.wsRetry += 1;
                        setTimeout(function () { self.connectWebSocket(); }, 2000 * self.wsRetry);
                    }
                };
            },

            // --- Polling fallback: keeps the hunt advancing without Redis ---

            startPolling: function () {
                var self = this;
                if (!this.stateUrl) return;
                this.pollTimer = setInterval(function () {
                    // Safety-net poll. An OPEN socket can still miss a status
                    // event (group_send failed, or the change landed between
                    // page render and group subscription), and QR/no-GPS
                    // screens have no position POST to surface the 403
                    // fallback. So poll regardless of socket health — cheap
                    // on the server, and the only guaranteed exit from a
                    // finished hunt.
                    fetch(self.stateUrl, { headers: { "Accept": "application/json" } })
                        .then(function (r) { return r.ok ? r.json() : null; })
                        .then(function (data) {
                            if (data && data.ok && data.status !== self.huntStatus) {
                                self.stopAndReload();
                            }
                        })
                        .catch(function () {});
                }, 20000);
            },

            getCsrfToken: function () {
                // CSRF_COOKIE_HTTPONLY is on, so the cookie is unreadable —
                // use the hidden input from base.html (same as htmx-csrf.js)
                var input = document.getElementById("csrf-token-input");
                if (input && input.value) return input.value;
                var row = document.cookie.split("; ").find(function (r) {
                    return r.startsWith("csrftoken=");
                });
                return row ? row.split("=")[1] : "";
            },
        };
    });
});
