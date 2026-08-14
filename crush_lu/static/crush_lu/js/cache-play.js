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
 * Mobile resilience: browsers pause (and sometimes silently kill) a
 * geolocation watch when the screen locks or the tab is backgrounded,
 * while orientation events resume on their own — the classic symptom is
 * a compass that keeps moving over a frozen distance readout. Hence the
 * screen wake lock, the watch-restart watchdog, the visibilitychange
 * restart, and the abort timeout on position POSTs.
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
            accuracyM: null,
            arrived: false,
            unlocked: false,
            compassNeedsPermission: false,
            hasAbsoluteHeading: false,
            // Continuous (never wrapped at 360°) rotation values for the
            // CSS-transitioned arrow/cone — see _updateArrowRotation().
            arrowRotation: null,
            _coneRotation: null,
            // Relative-alpha fallback bookkeeping: its zero point is
            // arbitrary, the GPS travel course learns the correction.
            _sawWebkit: false,
            _sawRelative: false,
            _lastRelativeRaw: null,
            _relativeOffset: null,
            watchId: null,
            lastFixAt: 0,
            watchdogTimer: null,
            wakeLock: null,
            lastPostAt: 0,
            postBackoffUntil: 0,
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
                this.audioUrl = root.dataset.audioUrl || "/static/crush_lu/audio/Crush-Love-Sound.mp3";
                this.msgs = {
                    gpsDenied: root.dataset.msgGpsDenied || "Location permission denied",
                    gpsWaiting: root.dataset.msgGpsWaiting || "Waiting for GPS…",
                    gpsAccuracy: root.dataset.msgGpsAccuracy || "GPS accuracy: ±{n} m",
                    gpsLost: root.dataset.msgGpsLost || "GPS signal lost — searching…",
                };
                try {
                    this.completedStations = JSON.parse(root.dataset.completedStations || "[]");
                } catch (e) {
                    this.completedStations = [];
                }

                if (this.huntStatus === "live") {
                    this.startWatching();
                    this.startGpsWatchdog();
                    this.requestWakeLock();
                    this.connectWebSocket();
                    this.startPolling();
                }
                var self = this;
                root.addEventListener("htmx:afterSwap", function () {
                    if (document.getElementById("cache-celebration")) {
                        self.suspendNavigation();
                    }
                });
                // Screen unlock / return from another app: the browser may
                // have silently dropped both the geolocation watch and the
                // wake lock while the page was hidden — rebuild them the
                // moment the page is visible again.
                this._onVisibility = function () {
                    if (document.visibilityState !== "visible") return;
                    if (self.huntStatus !== "live" || self.suspended || self.reloading) return;
                    self.requestWakeLock();
                    if (self.watchId !== null && !self.gpsDenied) {
                        self.restartWatching();
                    }
                };
                document.addEventListener("visibilitychange", this._onVisibility);
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
                this.preloadAudio();

                if (document.getElementById("cache-map") && window.L) {
                    this.initMap();
                }
                if (this.navMode === "compass") {
                    this.listenToCompass();
                }
            },

            destroy: function () {
                this.stopWatching();
                if (this.watchdogTimer !== null) {
                    clearInterval(this.watchdogTimer);
                    this.watchdogTimer = null;
                }
                if (this._onVisibility) {
                    document.removeEventListener("visibilitychange", this._onVisibility);
                }
                this.releaseWakeLock();
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
                var rotation = this.arrowRotation !== null
                    ? this.arrowRotation
                    : this.bearing - (this.heading || 0);
                return "transform: rotate(" + rotation + "deg)";
            },

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
                // Grace period so the watchdog doesn't restart a watch that
                // is still acquiring its first fix.
                this.lastFixAt = Date.now();
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

            restartWatching: function () {
                this.stopWatching();
                this.startWatching();
            },

            // A mobile geolocation watch can silently stop delivering fixes
            // (screen lock, app switch, OS GPS hiccup) and never recovers by
            // itself — tearing it down and re-registering does. 25 s without
            // a fix means stalled, not just slow: a healthy watch reports at
            // least every few seconds outdoors.
            startGpsWatchdog: function () {
                var self = this;
                if (this.watchdogTimer !== null) return;
                this.watchdogTimer = setInterval(function () {
                    if (self.suspended || self.reloading || self.gpsDenied) return;
                    if (self.watchId === null) return;
                    if (Date.now() - self.lastFixAt > 25000) {
                        self.gpsStatus = self.msgs.gpsLost;
                        self.restartWatching();
                    }
                }, 10000);
            },

            // Keep the screen awake while navigating: the screen locking is
            // what pauses the geolocation watch in the first place (browsers
            // stop geolocation for hidden pages — the reason app-based hunt
            // games exist). Best-effort: needs https and a visible page;
            // browsers without the API simply skip it.
            requestWakeLock: function () {
                var self = this;
                if (!navigator.wakeLock || document.visibilityState !== "visible") return;
                try {
                    navigator.wakeLock.request("screen").then(function (lock) {
                        self.wakeLock = lock;
                    }).catch(function () {});
                } catch (e) {}
            },

            releaseWakeLock: function () {
                if (!this.wakeLock) return;
                try {
                    var res = this.wakeLock.release();
                    if (res && res.catch) res.catch(function () {});
                } catch (e) {}
                this.wakeLock = null;
            },

            onPosition: function (pos) {
                var lat = pos.coords.latitude;
                var lng = pos.coords.longitude;
                var accuracy = pos.coords.accuracy || 0;
                this.accuracyM = accuracy;
                this.lastFixAt = Date.now();

                this.updateSelfMarker(lat, lng, accuracy);
                this.gpsStatus = this.msgs.gpsAccuracy.replace("{n}", Math.round(accuracy));

                // Travel course from the fix itself: available in every
                // browser without sensor permissions, and immune to
                // magnetometer calibration — feeds the heading pipeline
                // whenever the player is actually walking (>1 m/s).
                var course = pos.coords.heading;
                var speed = pos.coords.speed;
                if (typeof course === "number" && isFinite(course)
                    && typeof speed === "number" && speed > 1) {
                    this.onGpsCourse(course);
                }

                var now = Date.now();
                if (this.posting || now < this.postBackoffUntil || now - this.lastPostAt < 3000) return;
                this.lastPostAt = now;
                this.postPosition(lat, lng, accuracy);
            },

            postPosition: function (lat, lng, accuracy) {
                var self = this;
                this.posting = true;
                // A request stalled by a network handover must not wedge
                // `posting` forever (it gates every future POST) — abort it
                // and let the next fix retry.
                var controller = window.AbortController ? new AbortController() : null;
                var abortTimer = controller
                    ? setTimeout(function () {
                        try { controller.abort(); } catch (e) {}
                    }, 10000)
                    : null;
                var finish = function () {
                    self.posting = false;
                    if (abortTimer !== null) clearTimeout(abortTimer);
                };
                fetch(this.positionUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": this.getCsrfToken(),
                    },
                    body: JSON.stringify({ lat: lat, lng: lng, accuracy: accuracy }),
                    signal: controller ? controller.signal : undefined,
                })
                    .then(function (r) {
                        if (r.status === 403 || r.status === 429) {
                            return r.json()
                                .catch(function () { return null; })
                                .then(function (err) {
                                    if (self.suspended) return null;
                                    var code = err && err.error;
                                    if (code === "finished" || code === "not_live" || code === "no_team") {
                                        self.stopAndReload();
                                    } else {
                                        // Transient refusal (rate limit
                                        // burst, CSRF hiccup): back off but
                                        // keep the watch alive — stopping it
                                        // here is permanent and turns one bad
                                        // response into a dead distance
                                        // readout under a live compass.
                                        self.postBackoffUntil = Date.now() + 30000;
                                    }
                                    return null;
                                });
                        }
                        return r.ok ? r.json() : null;
                    })
                    .then(function (data) {
                        finish();
                        if (!data || !data.ok || self.suspended) return;
                        if (typeof data.distance_m === "number") self.distanceM = data.distance_m;
                        if (typeof data.bearing === "number") {
                            self.bearing = data.bearing;
                            self._updateArrowRotation();
                        }
                        if (self.needsGps && (data.arrived || data.unlocked)) {
                            self.celebrateArrival();
                        }
                    })
                    .catch(function () { finish(); });
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
                var soundDurationMs = this.playGpsChime() || 2200;
                if (navigator.vibrate) {
                    try { navigator.vibrate([150, 100, 150]); } catch (e) {}
                }
                var self = this;
                var reduce = window.matchMedia
                    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
                var delayMs = reduce
                    ? Math.max(1200, Math.ceil(soundDurationMs))
                    : Math.max(2200, Math.ceil(soundDurationMs + 300));
                setTimeout(function () {
                    if (self.suspended) return;
                    window.location.reload();
                }, delayMs);
            },

            preloadAudio: function () {
                if (window._crushMp3Buffer || window._crushMp3Loading) return;
                window._crushMp3Loading = true;
                try {
                    var AudioCtx = window.AudioContext || window.webkitAudioContext;
                    if (!AudioCtx) return;
                    if (!window._crushAudioCtx) {
                        window._crushAudioCtx = new AudioCtx();
                    }
                    var ctx = window._crushAudioCtx;
                    var url = this.audioUrl || "/static/crush_lu/audio/Crush-Love-Sound.mp3";
                    fetch(url)
                        .then(function (res) { return res.arrayBuffer(); })
                        .then(function (buf) {
                            var res = ctx.decodeAudioData(
                                buf,
                                function (decoded) {
                                    window._crushMp3Buffer = decoded;
                                },
                                function () {
                                    window._crushMp3Loading = false;
                                }
                            );
                            if (res && typeof res.then === "function") {
                                res.then(function (decoded) {
                                    window._crushMp3Buffer = decoded;
                                }).catch(function () {
                                    window._crushMp3Loading = false;
                                });
                            }
                        })
                        .catch(function () { window._crushMp3Loading = false; });
                } catch (e) {
                    window._crushMp3Loading = false;
                }
            },

            playGpsChime: function () {
                try {
                    var AudioCtx = window.AudioContext || window.webkitAudioContext;
                    if (window._crushMp3Buffer && AudioCtx) {
                        if (!window._crushAudioCtx) window._crushAudioCtx = new AudioCtx();
                        var ctx = window._crushAudioCtx;
                        if (ctx.state === "suspended") {
                            ctx.resume();
                        }
                        var src = ctx.createBufferSource();
                        var gain = ctx.createGain();
                        gain.gain.value = 0.8;
                        src.buffer = window._crushMp3Buffer;
                        src.connect(gain);
                        gain.connect(ctx.destination);
                        src.start(0);
                        return Math.ceil(src.buffer.duration * 1000);
                    }
                } catch (e) {}

                var self = this;
                try {
                    var url = this.audioUrl || "/static/crush_lu/audio/Crush-Love-Sound.mp3";
                    var audio = new Audio(url);
                    audio.volume = 0.8;
                    var playPromise = audio.play();
                    if (playPromise !== undefined) {
                        playPromise.catch(function () {
                            self.playSynthGpsChime();
                        });
                    }
                    return 11500;
                } catch (e) {
                    this.playSynthGpsChime();
                    return 800;
                }
            },

            _getRomanticReverb: function (ctx) {
                try {
                    if (ctx._crushReverb) return ctx._crushReverb;
                    var delay = ctx.createDelay();
                    var feedback = ctx.createGain();
                    var filter = ctx.createBiquadFilter();
                    delay.delayTime.setValueAtTime(0.16, ctx.currentTime);
                    feedback.gain.setValueAtTime(0.32, ctx.currentTime);
                    filter.type = "lowpass";
                    filter.frequency.setValueAtTime(1400, ctx.currentTime);
                    delay.connect(filter);
                    filter.connect(feedback);
                    feedback.connect(delay);
                    delay.connect(ctx.destination);
                    ctx._crushReverb = delay;
                    return ctx._crushReverb;
                } catch (e) {
                    return null;
                }
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
                    var reverb = this._getRomanticReverb(ctx);
                    if (reverb) reverb.connect(ctx.destination);

                    // 1. Romantic Heartbeat Sub-Pulse (lub-dub)
                    [0, 0.13].forEach(function (delayTime) {
                        var o = ctx.createOscillator();
                        var g = ctx.createGain();
                        var t = now + delayTime;
                        o.type = "sine";
                        o.frequency.setValueAtTime(95, t);
                        o.frequency.exponentialRampToValueAtTime(42, t + 0.11);
                        g.gain.setValueAtTime(0.45, t);
                        g.gain.exponentialRampToValueAtTime(0.001, t + 0.11);
                        o.connect(g);
                        g.connect(ctx.destination);
                        o.start(t);
                        o.stop(t + 0.11);
                    });

                    // 2. Romantic C Major 11th "Warm Embrace" Swell: C4, G4, B4, D5, F#5, G5, B5, D6
                    var notes = [261.63, 392.00, 493.88, 587.33, 739.99, 783.99, 987.77, 1174.66];
                    notes.forEach(function (freq, idx) {
                        var o1 = ctx.createOscillator();
                        var o2 = ctx.createOscillator();
                        var lfo = ctx.createOscillator();
                        var lfoGain = ctx.createGain();
                        var g = ctx.createGain();
                        var p = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
                        var t = now + 0.22 + (idx * 0.095);

                        // 5.2Hz romantic vibrato
                        lfo.frequency.setValueAtTime(5.2, t);
                        lfoGain.gain.setValueAtTime(freq * 0.006, t);
                        lfo.connect(lfoGain);
                        lfoGain.connect(o1.frequency);

                        o1.type = "sine";
                        o2.type = "triangle";
                        o1.frequency.setValueAtTime(freq, t);
                        o2.frequency.setValueAtTime(freq * 1.0045, t);
                        g.gain.setValueAtTime(0.24, t);
                        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.85);

                        o1.connect(g);
                        o2.connect(g);
                        var target = p || ctx.destination;
                        g.connect(target);
                        if (reverb) g.connect(reverb);
                        if (p) {
                            p.pan.setValueAtTime((idx % 2 === 0 ? -1 : 1) * 0.35, t);
                            p.connect(ctx.destination);
                        }

                        lfo.start(t);
                        o1.start(t);
                        o2.start(t);
                        lfo.stop(t + 0.85);
                        o1.stop(t + 0.85);
                        o2.stop(t + 0.85);
                    });
                    if (navigator.vibrate) {
                        try { navigator.vibrate([100, 90, 180]); } catch (e) {}
                    }
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
                    var reverb = this._getRomanticReverb(ctx);
                    if (reverb) reverb.connect(ctx.destination);

                    // Romantic F Major 13th "Cupid's Dream" Harp Sweep: F4, A4, C5, E5, G5, B5, D6, E6
                    var freqs = [349.23, 440.00, 523.25, 659.25, 783.99, 987.77, 1174.66, 1318.51];
                    freqs.forEach(function (f, idx) {
                        var o1 = ctx.createOscillator();
                        var o2 = ctx.createOscillator();
                        var lfo = ctx.createOscillator();
                        var lfoGain = ctx.createGain();
                        var g = ctx.createGain();
                        var p = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
                        var t = now + (idx * 0.058);

                        lfo.frequency.setValueAtTime(5.8, t);
                        lfoGain.gain.setValueAtTime(f * 0.005, t);
                        lfo.connect(lfoGain);
                        lfoGain.connect(o1.frequency);

                        o1.type = "sine";
                        o2.type = "sine";
                        o1.frequency.setValueAtTime(f, t);
                        o2.frequency.setValueAtTime(f * 1.005, t);
                        g.gain.setValueAtTime(0.28, t);
                        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.75);

                        o1.connect(g);
                        o2.connect(g);
                        var target = p || ctx.destination;
                        g.connect(target);
                        if (reverb) g.connect(reverb);
                        if (p) {
                            p.pan.setValueAtTime((idx / (freqs.length - 1) - 0.5) * 0.7, t);
                            p.connect(ctx.destination);
                        }

                        lfo.start(t);
                        o1.start(t);
                        o2.start(t);
                        lfo.stop(t + 0.75);
                        o1.stop(t + 0.75);
                        o2.stop(t + 0.75);
                    });
                    if (navigator.vibrate) {
                        try { navigator.vibrate([70, 50, 140]); } catch (e) {}
                    }
                } catch (e) {}
            },

            playSynthVictorySound: function () {
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
                    var reverb = this._getRomanticReverb(ctx);
                    if (reverb) reverb.connect(ctx.destination);

                    // Triumphant A Major 9th "Eternal Love" Fanfare: A3, E4, A4, C#5, E5, G#5, B5, E6, G#6
                    var chord = [220.00, 329.63, 440.00, 554.37, 659.25, 830.61, 987.77, 1318.51, 1661.22];
                    chord.forEach(function (f, idx) {
                        var o1 = ctx.createOscillator();
                        var o2 = ctx.createOscillator();
                        var lfo = ctx.createOscillator();
                        var lfoGain = ctx.createGain();
                        var g = ctx.createGain();
                        var p = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
                        var t = now + (idx * 0.075);

                        lfo.frequency.setValueAtTime(4.8, t);
                        lfoGain.gain.setValueAtTime(f * 0.004, t);
                        lfo.connect(lfoGain);
                        lfoGain.connect(o1.frequency);

                        o1.type = "sine";
                        o2.type = "triangle";
                        o1.frequency.setValueAtTime(f, t);
                        o2.frequency.setValueAtTime(f * 1.004, t);
                        g.gain.setValueAtTime(0.24, t);
                        g.gain.exponentialRampToValueAtTime(0.0001, t + 1.1);

                        o1.connect(g);
                        o2.connect(g);
                        var target = p || ctx.destination;
                        g.connect(target);
                        if (reverb) g.connect(reverb);
                        if (p) {
                            p.pan.setValueAtTime((idx % 2 === 0 ? -0.45 : 0.45), t);
                            p.connect(ctx.destination);
                        }

                        lfo.start(t);
                        o1.start(t);
                        o2.start(t);
                        lfo.stop(t + 1.1);
                        o1.stop(t + 1.1);
                        o2.stop(t + 1.1);
                    });
                    if (navigator.vibrate) {
                        try { navigator.vibrate([120, 80, 120, 80, 260]); } catch (e) {}
                    }
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
                this.playSynthVictorySound();
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
                if (typeof window === "undefined") return;
                if (this._compassAttached) return;
                this._compassAttached = true;
                var self = this;

                var getScreenAngle = function () {
                    // `null` until someone simulates an angle from the console
                    // (window.setSimulatedScreenAngle). It must not default to a
                    // number: this branch is checked first, so a numeric default
                    // shadows the real orientation below and rotation
                    // compensation never runs for an actual player.
                    if (typeof self.simulatedScreenAngle === "number") {
                        return self.simulatedScreenAngle;
                    }
                    if (window.screen && window.screen.orientation && typeof window.screen.orientation.angle === "number") {
                        return window.screen.orientation.angle;
                    }
                    if (typeof window.orientation === "number") {
                        return window.orientation;
                    }
                    return 0;
                };
                // onGpsCourse (outside this closure) needs the live screen
                // angle to calibrate the relative-alpha offset consistently.
                self._getScreenAngle = getScreenAngle;

                var lastRawHeading = 0;
                var updateHeading = function (rawDeg) {
                    if (typeof rawDeg === "number" && !isNaN(rawDeg)) {
                        lastRawHeading = rawDeg;
                    }
                    var screenAngle = getScreenAngle();
                    self._applyHeading(lastRawHeading + screenAngle);
                };

                self._updateHeading = updateHeading;
                self.setSimulatedScreenAngle = function (angle) {
                    self.simulatedScreenAngle = angle;
                    updateHeading(lastRawHeading);
                };
                self.setDebugHeading = function (deg) {
                    updateHeading(deg);
                };

                // Developer testing helpers for HTTP localhost where Chrome blocks real hardware sensors
                window.setDebugHeading = function (deg) {
                    updateHeading(deg);
                };
                window.setSimulatedScreenAngle = function (angle) {
                    if (self.setSimulatedScreenAngle) self.setSimulatedScreenAngle(angle);
                };

                window.addEventListener("orientationchange", function () {
                    updateHeading(lastRawHeading);
                });
                if (window.screen && window.screen.orientation) {
                    try {
                        window.screen.orientation.addEventListener("change", function () {
                            updateHeading(lastRawHeading);
                        });
                    } catch (e) {}
                }

                window.addEventListener("keydown", function (e) {
                    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
                    var current = lastRawHeading || 0;
                    if (e.key === "ArrowLeft") {
                        updateHeading(current - 15);
                    } else if (e.key === "ArrowRight") {
                        updateHeading(current + 15);
                    }
                });

                // Near-vertical guard for alpha-based headings: 360 − alpha
                // is the compass direction of the device's top edge projected
                // onto the ground. Around vertical (beta ≈ 90°) that
                // projection is numerically unstable, and past vertical it
                // flips a full 180° — the "arrow points backwards when I lift
                // the phone" effect. Hold the last heading there; the GPS
                // course keeps correcting while walking. (iOS's
                // webkitCompassHeading is tilt-compensated by the OS and
                // doesn't need this.)
                var betaUnstable = function (e) {
                    return typeof e.beta === "number" && e.beta > 70 && e.beta < 110;
                };

                var handler = function (e) {
                    if (typeof e.webkitCompassHeading === "number" && !isNaN(e.webkitCompassHeading)) {
                        self._sawWebkit = true;
                        updateHeading(e.webkitCompassHeading);
                    } else if (!self.hasAbsoluteHeading && e.alpha !== null && e.alpha !== undefined) {
                        if (betaUnstable(e)) return;
                        // Relative alpha has an arbitrary zero (whichever way
                        // the device pointed on page load), so on its own the
                        // arrow can be off by anything up to 180° — apply the
                        // offset learned from the GPS course (onGpsCourse).
                        var raw = 360 - e.alpha;
                        self._sawRelative = true;
                        self._lastRelativeRaw = raw;
                        updateHeading(self._relativeOffset === null ? raw : raw + self._relativeOffset);
                    }
                };

                try { window.addEventListener("deviceorientation", handler, true); } catch (err) {}
                try {
                    window.addEventListener("deviceorientationabsolute", function (e) {
                        if (e.alpha !== null && e.alpha !== undefined) {
                            self.hasAbsoluteHeading = true;
                            if (betaUnstable(e)) return;
                            updateHeading(360 - e.alpha);
                        }
                    }, true);
                } catch (err) {}

                // Support Web Sensor API AbsoluteOrientationSensor (used in modern Chrome DevTools emulation)
                if (typeof AbsoluteOrientationSensor !== "undefined") {
                    try {
                        var sensor = new AbsoluteOrientationSensor({ frequency: 10 });
                        sensor.addEventListener("reading", function () {
                            var q = sensor.quaternion;
                            if (q) {
                                var alpha = Math.atan2(2 * (q[3] * q[2] + q[0] * q[1]), 1 - 2 * (q[1] * q[1] + q[2] * q[2]));
                                var deg = alpha * (180 / Math.PI);
                                self.hasAbsoluteHeading = true;
                                updateHeading(360 - deg);
                            }
                        });
                        sensor.start();
                    } catch (e) {}
                }
            },

            // --- Heading pipeline (shared by compass events + GPS course) ---

            // Smooth toward the new heading along the shortest arc: raw
            // sensor headings jitter by several degrees per event and wrap
            // at 0/360, and feeding them straight into the CSS transition
            // makes the arrow tremble and occasionally spin the long way
            // around.
            _applyHeading: function (deg) {
                deg = ((deg % 360) + 360) % 360;
                if (typeof this.heading !== "number" || isNaN(this.heading)) {
                    this.heading = deg;
                } else {
                    var delta = ((deg - this.heading + 540) % 360) - 180;
                    this.heading = (((this.heading + delta * 0.35) % 360) + 360) % 360;
                }
                if (this._coneRotation === null) {
                    this._coneRotation = this.heading;
                } else {
                    var current = ((this._coneRotation % 360) + 360) % 360;
                    this._coneRotation += ((this.heading - current + 540) % 360) - 180;
                }
                this._updateArrowRotation();
                this.updateSelfMarker(this.currentLat, this.currentLng, this.accuracyM || 10);
            },

            // The stored rotation accumulates past ±360° on purpose: CSS
            // transitions animate through the numeric difference, so jumping
            // from 359° to 1° would visibly swing the arrow 358° the wrong
            // way — instead we always add the shortest signed arc.
            _updateArrowRotation: function () {
                if (this.bearing === null) return;
                var target = this.bearing - (this.heading || 0);
                if (this.arrowRotation === null) {
                    // First value: shortest arc from the untransformed 0°
                    // so the initial transition doesn't do a near-full spin.
                    this.arrowRotation = ((target % 360) + 540) % 360 - 180;
                    return;
                }
                var current = ((this.arrowRotation % 360) + 360) % 360;
                var normalized = ((target % 360) + 360) % 360;
                this.arrowRotation += ((normalized - current + 540) % 360) - 180;
            },

            // Direction of travel from the GPS fix (see onPosition). With a
            // trustworthy compass source it is ignored; with only the
            // relative-alpha fallback it learns that source's unknown offset;
            // with no orientation events at all it becomes the heading.
            onGpsCourse: function (course) {
                if (this._sawWebkit || this.hasAbsoluteHeading) return;
                if (this._sawRelative) {
                    if (typeof this._lastRelativeRaw === "number") {
                        // The offset is added to the raw (pre-screen-angle)
                        // heading in the orientation handler, and
                        // updateHeading() adds the screen angle afterwards —
                        // so the screen angle must be subtracted while
                        // learning it, or a landscape device ends up a
                        // quarter-turn off after calibration.
                        var screenAngle = this._getScreenAngle ? this._getScreenAngle() : 0;
                        this._relativeOffset =
                            ((course - screenAngle - this._lastRelativeRaw + 540) % 360) - 180;
                    }
                    return;
                }
                this._applyHeading(course);
            },

            // --- Leaflet map (map navigation mode only) ---

            _isValidCoord: function (lat, lng) {
                return typeof lat === "number" && typeof lng === "number" && !isNaN(lat) && !isNaN(lng);
            },

            // Only ever set from the console via window.setSimulatedScreenAngle.
            // Must stay null by default — see getScreenAngle().
            simulatedScreenAngle: null,
            isFullscreenMap: false,

            // --- Display helpers ---

            toggleFullscreenMap: function () {
                this.isFullscreenMap = !this.isFullscreenMap;
                var container = document.getElementById("cache-map-container");
                if (!container) return;
                if (this.isFullscreenMap) {
                    container.classList.add("crush-fullscreen-active");
                } else {
                    container.classList.remove("crush-fullscreen-active");
                }
                var self = this;
                setTimeout(function () {
                    if (self.map) {
                        self.map.invalidateSize();
                        self.recenterMap();
                    }
                }, 150);
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
                    attributionControl: false,
                }).setView(center, 16);

                L.control.zoom({ position: "bottomright" }).addTo(this.map);
                L.control.attribution({ prefix: false }).addTo(this.map);

                var isDark = document.documentElement.classList.contains("dark");
                var tileUrl = isDark
                    ? "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                    : "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png";

                L.tileLayer(tileUrl, {
                    maxZoom: 19,
                    subdomains: "abcd",
                    detectRetina: true,
                    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
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

                // Custom Recenter button control inside Leaflet top-left bar
                var RecenterControl = L.Control.extend({
                    options: { position: "topleft" },
                    onAdd: function () {
                        var container = L.DomUtil.create("div", "leaflet-bar leaflet-control");
                        var btn = L.DomUtil.create("a", "", container);
                        btn.href = "#";
                        btn.title = "Recenter map";
                        btn.innerHTML = "🎯";
                        btn.style.cssText = "font-size: 15px; display: flex; align-items: center; justify-content: center; text-decoration: none; width: 34px; height: 34px; line-height: 34px; background: rgba(255,255,255,0.95); color: #1f2937; font-weight: bold; border-radius: 8px; cursor: pointer;";
                        L.DomEvent.on(btn, "click", function (e) {
                            L.DomEvent.stopPropagation(e);
                            L.DomEvent.preventDefault(e);
                            self.recenterMap();
                        });
                        return container;
                    }
                });
                this.map.addControl(new RecenterControl());

                // Custom Fullscreen button control inside Leaflet top-right bar
                var FullscreenControl = L.Control.extend({
                    options: { position: "topright" },
                    onAdd: function () {
                        var container = L.DomUtil.create("div", "leaflet-bar leaflet-control");
                        var btn = L.DomUtil.create("a", "", container);
                        btn.href = "#";
                        btn.title = "Toggle Fullscreen Map";
                        btn.innerHTML = self.isFullscreenMap ? "✖" : "⛶";
                        btn.style.cssText = "font-size: 16px; display: flex; align-items: center; justify-content: center; text-decoration: none; width: 34px; height: 34px; line-height: 34px; background: rgba(255,255,255,0.95); color: #1f2937; font-weight: bold; border-radius: 8px; cursor: pointer;";
                        L.DomEvent.on(btn, "click", function (e) {
                            L.DomEvent.stopPropagation(e);
                            L.DomEvent.preventDefault(e);
                            self.toggleFullscreenMap();
                            btn.innerHTML = self.isFullscreenMap ? "✖" : "⛶";
                        });
                        return container;
                    }
                });
                this.map.addControl(new FullscreenControl());
                this.attachCompass();
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
                var isRealFix = this._isValidCoord(lat, lng);
                if (isRealFix) {
                    this.currentLat = lat;
                    this.currentLng = lng;
                }
                if (!this._isValidCoord(this.currentLat, this.currentLng)) {
                    return;
                }
                var validLat = this.currentLat;
                var validLng = this.currentLng;
                if (!this.map || !window.L) return;
                var firstFix = !this.selfMarker;
                // Continuous rotation (never wrapped) so the cone's CSS
                // transition can't spin the long way around at north.
                var headingAngle = this._coneRotation !== null
                    ? this._coneRotation
                    : (typeof this.heading === "number" && !isNaN(this.heading) ? this.heading : 0);

                if (!this.selfMarker) {
                    var selfHtml = '<div style="width:24px; height:24px; box-sizing:border-box; border-radius:50%; background:linear-gradient(135deg, #8b5cf6, #ec4899); border:3px solid #ffffff; box-shadow:0 0 12px rgba(139,92,246,0.9); position:relative;" class="crush-player-pulse">' +
                        '<svg class="self-heading-cone" style="position:absolute; top:-15px; left:-3px; width:24px; height:36px; overflow:visible; transition:transform 0.15s linear; transform: rotate(' + headingAngle + 'deg); transform-origin: 12px 24px;" viewBox="0 0 24 36">' +
                        '<polygon points="12,0 7,12 17,12" fill="#ec4899" opacity="0.95"/>' +
                        '</svg>' +
                        '</div>';
                    var selfIcon = L.divIcon({
                        className: "crush-self-marker",
                        html: selfHtml,
                        iconSize: [24, 24],
                        iconAnchor: [12, 12],
                    });
                    this.selfMarker = L.marker([validLat, validLng], { icon: selfIcon, zIndexOffset: 1000 }).addTo(this.map);

                    this.accuracyCircle = L.circle([validLat, validLng], {
                        radius: accuracy || 10,
                        color: "#3b82f6",
                        weight: 1,
                        fillOpacity: 0.08,
                    }).addTo(this.map);
                } else {
                    this.selfMarker.setLatLng([validLat, validLng]);
                    this.accuracyCircle.setLatLng([validLat, validLng]);
                    this.accuracyCircle.setRadius(accuracy || 10);

                    var el = this.selfMarker.getElement();
                    var cone = el ? el.querySelector(".self-heading-cone") : null;
                    if (cone) {
                        cone.style.transform = "rotate(" + headingAngle + "deg)";
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
                    this.ws.onopen = function () {
                        self.wsConnected = true;
                        self.wsRetry = 0;
                    };
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
                        if (self.wsConnected && self.wsRetry < 5) {
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
