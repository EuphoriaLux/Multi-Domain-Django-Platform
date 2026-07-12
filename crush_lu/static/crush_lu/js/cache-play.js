/**
 * Crush Cache — player navigation component.
 *
 * Watches the device position, posts fixes to the server (which alone
 * decides "arrived"), renders the distance / compass readout, and keeps
 * an optional Leaflet map (map navigation mode) in sync. When the server
 * reports the current station as arrived/unlocked, the page reloads so
 * the server-rendered state machine advances.
 *
 * Uses the native WebSocket API for hunt status changes — no libraries.
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
            watchId: null,
            lastPostAt: 0,
            posting: false,
            map: null,
            selfMarker: null,
            accuracyCircle: null,
            ws: null,
            wsRetry: 0,

            init: function () {
                var root = this.$el;
                this.positionUrl = root.dataset.positionUrl;
                this.huntId = root.dataset.huntId;
                this.huntStatus = root.dataset.huntStatus;
                this.navMode = root.dataset.navMode;
                this.needsGps = root.dataset.needsGps === "true";
                this.unlocked = root.dataset.unlocked === "true";
                this.targetLat = root.dataset.targetLat ? parseFloat(root.dataset.targetLat) : null;
                this.targetLng = root.dataset.targetLng ? parseFloat(root.dataset.targetLng) : null;
                this.targetRadius = root.dataset.targetRadius ? parseInt(root.dataset.targetRadius, 10) : null;
                try {
                    this.completedStations = JSON.parse(root.dataset.completedStations || "[]");
                } catch (e) {
                    this.completedStations = [];
                }

                if (this.huntStatus === "live") {
                    this.startWatching();
                    this.connectWebSocket();
                }
                if (document.getElementById("cache-map") && window.L) {
                    this.initMap();
                }
                if (this.navMode === "compass") {
                    this.listenToCompass();
                }
            },

            destroy: function () {
                if (this.watchId !== null && navigator.geolocation) {
                    navigator.geolocation.clearWatch(this.watchId);
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
                            ? "Location permission denied"
                            : "Waiting for GPS…";
                    },
                    { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 }
                );
            },

            onPosition: function (pos) {
                var lat = pos.coords.latitude;
                var lng = pos.coords.longitude;
                var accuracy = pos.coords.accuracy || 0;

                this.updateSelfMarker(lat, lng, accuracy);
                this.gpsStatus = "GPS accuracy: ±" + Math.round(accuracy) + " m";

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
                    .then(function (r) { return r.ok ? r.json() : null; })
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

            // --- Compass (device orientation) ---

            listenToCompass: function () {
                var self = this;
                if (!window.DeviceOrientationEvent) return;
                window.addEventListener("deviceorientationabsolute", function (e) {
                    if (e.alpha !== null) self.heading = 360 - e.alpha;
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
                    if (msg.type === "status" && msg.data && msg.data.status !== self.huntStatus) {
                        window.location.reload();
                    }
                };
                this.ws.onclose = function () {
                    if (self.wsRetry < 5) {
                        self.wsRetry += 1;
                        setTimeout(function () { self.connectWebSocket(); }, 2000 * self.wsRetry);
                    }
                };
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
