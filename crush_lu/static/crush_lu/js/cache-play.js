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
            gpsDenied: false,
            arrivalCelebrating: false,
            suspended: false,
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
                // A station/hunt-complete celebration swapped into the play
                // region makes this shell's data stale — progress already
                // points at the next station, so the next position POST
                // would report "unlocked" and reload right over the card.
                // Suspend navigation while it's on screen; its Continue
                // link does the reload.
                var self = this;
                root.addEventListener("htmx:afterSwap", function () {
                    if (document.getElementById("cache-celebration")) {
                        self.suspendNavigation();
                    }
                });
                // Pre-unlock AudioContext on first tap anywhere on the page
                var unlockAudio = function () {
                    try {
                        var AudioCtx = window.AudioContext || window.webkitAudioContext;
                        if (AudioCtx) {
                            if (!window._crushAudioCtx) {
                                window._crushAudioCtx = new AudioCtx();
                            } else if (window._crushAudioCtx.state === "suspended") {
                                var res = window._crushAudioCtx.resume();
                                if (res && res.catch) res.catch(function () {});
                            }
                        }
                    } catch (e) {}
                };
                window.addEventListener("pointerdown", unlockAudio, { once: true });
                window.addEventListener("click", unlockAudio, { once: true });

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

            // CSP-build note: x-show only takes property paths, so the
            // negation lives here instead of "!geoSupported" in templates.
            get geoUnsupported() {
                return !this.geoSupported;
            },

            // --- Geolocation ---

            startWatching: function () {
                var self = this;
                if (!navigator.geolocation) {
                    this.geoSupported = false;
                    return;
                }
                this.watchId = navigator.geolocation.watchPosition(
                    function (pos) {
                        self.gpsDenied = false;
                        self.onPosition(pos);
                    },
                    function (err) {
                        self.gpsDenied = err.code === err.PERMISSION_DENIED;
                        self.gpsStatus = self.gpsDenied
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
                                    if (self.suspended) return null;
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
                        if (!data || !data.ok || self.suspended) return;
                        if (typeof data.distance_m === "number") self.distanceM = data.distance_m;
                        if (typeof data.bearing === "number") self.bearing = data.bearing;
                        // Server-side arrival — celebrate for a beat, then
                        // reload so the state machine advances
                        if (self.needsGps && (data.arrived || data.unlocked)) {
                            self.celebrateArrival();
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

            celebrateArrival: function () {
                if (this.reloading || this.suspended) return;
                this.reloading = true;
                this.stopWatching();
                this.arrivalCelebrating = true;
                this.playGpsChime();
                if (navigator.vibrate) {
                    try { navigator.vibrate([150, 100, 150]); } catch (e) {}
                }
                var self = this;
                var reduce = window.matchMedia
                    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
                setTimeout(function () {
                    if (self.suspended) return;
                    window.location.reload();
                }, reduce ? 1200 : 2200);
            },

            playGpsChime: function () {
                this.playSynthGpsChime();
            },

            playSynthGpsChime: function () {
                try {
                    var AudioCtx = window.AudioContext || window.webkitAudioContext;
                    if (!AudioCtx) return;
                    if (!window._crushAudioCtx) {
                        window._crushAudioCtx = new AudioCtx();
                    }
                    var ctx = window._crushAudioCtx;
                    if (ctx.state === "suspended") {
                        ctx.resume();
                    }
                    var now = ctx.currentTime;
                    // E Major triad arpeggio: E5 (659.25Hz), G#5 (830.61Hz), B5 (987.77Hz)
                    var notes = [659.25, 830.61, 987.77];
                    notes.forEach(function (freq, idx) {
                        var o = ctx.createOscillator();
                        var g = ctx.createGain();
                        var t = now + (idx * 0.12);
                        o.type = "sine";
                        o.frequency.setValueAtTime(freq, t);
                        g.gain.setValueAtTime(0.4, t);
                        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.5);
                        o.connect(g);
                        g.connect(ctx.destination);
                        o.start(t);
                        o.stop(t + 0.5);
                    });
                } catch (e) {}
            },

            playSynthQrSound: function () {
                try {
                    var AudioCtx = window.AudioContext || window.webkitAudioContext;
                    if (!AudioCtx) return;
                    if (!window._crushAudioCtx) {
                        window._crushAudioCtx = new AudioCtx();
                    }
                    var ctx = window._crushAudioCtx;
                    if (ctx.state === "suspended") {
                        ctx.resume();
                    }
                    var now = ctx.currentTime;
                    // C Major chord arpeggio: C5 (523.25Hz), E5 (659.25Hz), G5 (783.99Hz), C6 (1046.50Hz)
                    var freqs = [523.25, 659.25, 783.99, 1046.50];
                    freqs.forEach(function (f, idx) {
                        var o = ctx.createOscillator();
                        var g = ctx.createGain();
                        var t = now + (idx * 0.08);
                        o.type = "triangle";
                        o.frequency.setValueAtTime(f, t);
                        g.gain.setValueAtTime(0.35, t);
                        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.4);
                        o.connect(g);
                        g.connect(ctx.destination);
                        o.start(t);
                        o.stop(t + 0.4);
                    });
                } catch (e) {}
            },

            // A station-complete celebration owns the screen: stop GPS
            // posting and cancel any armed arrival reload so the card is
            // actually readable. The status poll stays live so a finished
            // hunt still exits to the results screen.
            suspendNavigation: function () {
                this.suspended = true;
                this.arrivalCelebrating = false;
                this.reloading = false;
                this.stopWatching();
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
                if (typeof window === "undefined" || !window.DeviceOrientationEvent) return;
                var self = this;
                var updateHeading = function (deg) {
                    self.heading = (deg + 360) % 360;
                    if (self.selfMarker && self.currentLat !== null && self.currentLng !== null) {
                        self.updateSelfMarker(self.currentLat, self.currentLng, self.accuracyM || 10);
                    }
                };
                try {
                    window.addEventListener("deviceorientationabsolute", function (e) {
                        if (e.alpha !== null && e.alpha !== undefined) {
                            self.hasAbsoluteHeading = true;
                            updateHeading(360 - e.alpha);
                        }
                    }, true);
                } catch (err) {}

                try {
                    window.addEventListener("deviceorientation", function (e) {
                        if (typeof e.webkitCompassHeading === "number" && !isNaN(e.webkitCompassHeading)) {
                            // iOS Safari
                            updateHeading(e.webkitCompassHeading);
                        } else if (e.alpha !== null && e.alpha !== undefined) {
                            // Android & Chrome DevTools Emulation
                            updateHeading(360 - e.alpha);
                        }
                    }, true);
                } catch (err) {}
            },

            // --- Leaflet map (map navigation mode only) ---

            _isValidCoord: function (lat, lng) {
                return typeof lat === "number" && typeof lng === "number" && !isNaN(lat) && !isNaN(lng);
            },

            initMap: function () {
                var hasTarget = this._isValidCoord(this.targetLat, this.targetLng);
                var center = hasTarget
                    ? [this.targetLat, this.targetLng]
                    : [49.6116, 6.1319]; // Luxembourg City fallback

                this.map = L.map("cache-map", {
                    zoomControl: false,
                    tap: false,
                    touchZoom: true,
                    bounceAtZoom: false,
                }).setView(center, 16);

                L.control.zoom({ position: "bottomright" }).addTo(this.map);

                L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
                    maxZoom: 19,
                    detectRetina: true,
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
                }).addTo(this.map);

                // Draw trail connecting completed stations
                var trailPoints = [];
                var self = this;
                (this.completedStations || []).forEach(function (s) {
                    var sLat = parseFloat(s.lat);
                    var sLng = parseFloat(s.lng);
                    if (!self._isValidCoord(sLat, sLng)) return;
                    trailPoints.push([sLat, sLng]);
                    L.circleMarker([sLat, sLng], {
                        radius: 7,
                        color: "#22c55e",
                        fillColor: "#22c55e",
                        fillOpacity: 0.9,
                        title: "✅ " + s.order + ". " + s.name,
                    }).addTo(self.map);
                });

                if (hasTarget) {
                    trailPoints.push([this.targetLat, this.targetLng]);
                    var targetIcon = L.divIcon({
                        className: "crush-target-marker",
                        html: '<div style="display:flex; align-items:center; justify-content:center; width:38px; height:38px; border-radius:50%; background:linear-gradient(135deg, #8b5cf6, #ec4899); box-shadow:0 4px 12px rgba(139,92,246,0.4); border:2px solid #ffffff; font-size:18px;">📍</div>',
                        iconSize: [38, 38],
                        iconAnchor: [19, 19],
                    });
                    L.marker([this.targetLat, this.targetLng], {
                        icon: targetIcon,
                        title: "Target Station",
                    }).addTo(this.map);

                    if (this.targetRadius) {
                        L.circle([this.targetLat, this.targetLng], {
                            radius: this.targetRadius,
                            color: "#ec4899",
                            fillColor: "#8b5cf6",
                            fillOpacity: 0.18,
                            weight: 2,
                            dashArray: "4, 6",
                        }).addTo(this.map);
                    }
                }

                if (trailPoints.length > 1) {
                    L.polyline(trailPoints, {
                        color: "#8b5cf6",
                        weight: 3,
                        dashArray: "6, 8",
                        opacity: 0.7,
                    }).addTo(this.map);
                }

                this.attachCompass();

                // Custom Recenter button control inside Leaflet top-left bar
                var RecenterControl = L.Control.extend({
                    options: { position: "topleft" },
                    onAdd: function () {
                        var container = L.DomUtil.create("div", "leaflet-bar leaflet-control");
                        var btn = L.DomUtil.create("a", "", container);
                        btn.href = "#";
                        btn.title = "Recenter map";
                        btn.innerHTML = "🎯";
                        btn.style.cssText = "font-size: 15px; display: flex; align-items: center; justify-content: center; text-decoration: none; width: 34px; height: 34px; line-height: 34px; background: rgba(255,255,255,0.95); font-weight: bold; border-radius: 8px;";
                        L.DomEvent.on(btn, "click", function (e) {
                            L.DomEvent.stopPropagation(e);
                            L.DomEvent.preventDefault(e);
                            self.recenterMap();
                        });
                        return container;
                    }
                });
                this.map.addControl(new RecenterControl());
            },

            recenterMap: function () {
                if (!this.map || !window.L) return;
                var points = [];
                if (this._isValidCoord(this.currentLat, this.currentLng)) {
                    points.push([this.currentLat, this.currentLng]);
                }
                if (this._isValidCoord(this.targetLat, this.targetLng)) {
                    points.push([this.targetLat, this.targetLng]);
                }
                if (points.length > 1) {
                    try {
                        var bounds = L.latLngBounds(points);
                        if (bounds.isValid()) {
                            this.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 17, animate: false });
                        }
                    } catch (e) {}
                } else if (points.length === 1) {
                    this.map.setView(points[0], 16);
                }
            },

            updateSelfMarker: function (lat, lng, accuracy) {
                if (!this.map || !window.L || !this._isValidCoord(lat, lng)) return;
                var firstFix = !this.selfMarker;
                var headingAngle = typeof this.heading === "number" && !isNaN(this.heading) ? this.heading : null;

                if (!this.selfMarker) {
                    var selfHtml = '<div style="width:20px; height:20px; border-radius:50%; background:#3b82f6; border:3px solid #ffffff; box-shadow:0 0 10px rgba(59,130,246,0.7); position:relative;">' +
                        '<div class="self-heading-cone" style="position:absolute; top:-12px; left:3px; width:0; height:0; border-left:6px solid transparent; border-right:6px solid transparent; border-bottom:12px solid #3b82f6; opacity:0.85; display:' + (headingAngle !== null ? 'block' : 'none') + '; transform: rotate(' + (headingAngle || 0) + 'deg); transform-origin: 4px 20px;"></div>' +
                        '</div>';
                    var selfIcon = L.divIcon({
                        className: "crush-self-marker",
                        html: selfHtml,
                        iconSize: [20, 20],
                        iconAnchor: [10, 10],
                    });
                    this.selfMarker = L.marker([lat, lng], { icon: selfIcon, zIndexOffset: 1000 }).addTo(this.map);

                    this.accuracyCircle = L.circle([lat, lng], {
                        radius: accuracy || 10,
                        color: "#3b82f6",
                        weight: 1,
                        fillOpacity: 0.08,
                    }).addTo(this.map);
                } else {
                    this.selfMarker.setLatLng([lat, lng]);
                    this.accuracyCircle.setLatLng([lat, lng]);
                    this.accuracyCircle.setRadius(accuracy || 10);

                    var el = this.selfMarker.getElement();
                    var cone = el ? el.querySelector(".self-heading-cone") : null;
                    if (cone) {
                        if (headingAngle !== null) {
                            cone.style.display = "block";
                            cone.style.transform = "rotate(" + headingAngle + "deg)";
                        } else {
                            cone.style.display = "none";
                        }
                    }
                }
                if (firstFix) {
                    this.recenterMap();
                }
            },

            // --- WebSocket: hunt status changes ---

            connectWebSocket: function () {
                var self = this;
                var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                var url = protocol + "//" + window.location.host + "/ws/cache/" + this.huntId + "/";
                try {
                    this.ws = new WebSocket(url);
                    this.ws.onerror = function () {
                        // WSGI dev server without Channels/Redis doesn't serve WebSockets:
                        // HTTP polling fallback (startPolling) handles live status updates instead.
                    };
                    this.ws.onmessage = function (event) {
                        var msg;
                        try { msg = JSON.parse(event.data); } catch (e) { return; }
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
                } catch (e) {}
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

    /**
     * Two-tap hint confirm: a stray thumb must never spend points.
     * armHint reads the hint number from the button's data-hint attribute
     * (the CSP build can't pass arguments in x-on expressions), and the
     * per-hint getters exist because x-show only takes property paths.
     * State resets naturally on every HTMX swap of the hints block.
     */
    Alpine.data("cacheHints", function () {
        return {
            armed: 0,
            armHint: function (event) {
                this.armed = parseInt(event.currentTarget.dataset.hint, 10) || 0;
            },
            disarm: function () {
                this.armed = 0;
            },
            get hint1Idle() { return this.armed !== 1; },
            get hint1Armed() { return this.armed === 1; },
            get hint2Idle() { return this.armed !== 2; },
            get hint2Armed() { return this.armed === 2; },
            get hint3Idle() { return this.armed !== 3; },
            get hint3Armed() { return this.armed === 3; },
        };
    });
});
