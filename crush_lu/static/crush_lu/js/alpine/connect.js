/**
 * Alpine.js CSP components for Crush.lu — connect bundle.
 *
 * Crush Connect: onboarding wizard, waitlist and weekly cycle pages.
 * Loaded with crush_lu/partials/alpine_bundle.html, before Alpine starts.
 * Build: `npm run build:js`.
 */

// ---- In-app video call (real-person check) ------------------------------
//
// Two-person WebRTC call behind the `videoCall` component. Media goes browser
// to browser, or through the TURN relay; the server only relays the call
// set-up over crush_lu/consumers_video_call.py and never sees the video.
// Simultaneous offers are settled with the W3C "perfect negotiation"
// pattern: each tab picks a random id, and the lower id is the polite side.
// The RTCPeerConnection, streams and socket live in a closure, not on the
// Alpine data object: Alpine's reactive proxy breaks native objects.
// Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md (D12)

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 15000];
// A paired call still not connected by then gets one ICE restart (fresh
// network paths), and is reported as failed at the timeout.
const ICE_RESTART_AFTER_MS = 10000;
const CONNECT_TIMEOUT_MS = 30000;
// consumers_video_call.CLOSE_REPLACED: a newer socket took this person's role.
const CLOSE_REPLACED = 4000;
const ACTIVE = ["waiting", "connecting", "connected", "reconnecting"];

function randomId() {
    const bytes = new Uint8Array(8);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function makeVideoCall() {
    const call = {
        config: null,
        iceUrl: "",
        labels: {},
        myId: randomId(),
        peerId: null,
        polite: false,
        pc: null,
        socket: null,
        stream: null,
        facingMode: "user",
        makingOffer: false,
        ignoreOffer: false,
        tracksAdded: false,
        // Signals are applied strictly in arrival order, and candidates that
        // arrive before the description they belong to wait here for it.
        signalQueue: Promise.resolve(),
        pendingCandidates: [],
        ended: true,
        reconnectAttempt: 0,
        iceServers: [],
        iceTransportPolicy: "relay",
        reconnectTimer: null,
        connectTimer: null,
        restartTimer: null,
    };

    return {
        status: "idle",
        microphoneOn: true,
        cameraOn: true,
        canSwitchCamera: false,

        init() {
            const configEl = document.getElementById(this.$el.dataset.configId);
            call.config = JSON.parse(configEl.textContent);
            const data = this.$el.dataset;
            // Read here: in a click handler $el is the button, not this root.
            call.iceUrl = data.iceUrl;
            call.labels = {
                idle: data.labelIdle,
                starting: data.labelStarting,
                waiting: data.labelWaiting,
                connecting: data.labelConnecting,
                connected: data.labelConnected,
                reconnecting: data.labelReconnecting,
                ended: data.labelEnded,
                failed: data.labelFailed,
                denied: data.labelDenied,
                replaced: data.labelReplaced,
                mute: data.labelMute,
                unmute: data.labelUnmute,
                cameraOff: data.labelCameraOff,
                cameraOn: data.labelCameraOn,
            };
            this._onPageHide = () => this.hangUp();
            window.addEventListener("pagehide", this._onPageHide);
        },

        destroy() {
            window.removeEventListener("pagehide", this._onPageHide);
            this.hangUp();
        },

        get statusLabel() {
            return call.labels[this.status] || "";
        },
        get canStart() {
            return !ACTIVE.includes(this.status) && this.status !== "starting";
        },
        get inCall() {
            return ACTIVE.includes(this.status);
        },
        get statusClass() {
            const problem = this.status === "failed" || this.status === "denied";
            return problem ? "rounded-lg bg-red-700/90 px-3 py-2" : "";
        },
        get showSwitchCamera() {
            return this.inCall && this.canSwitchCamera;
        },
        get microphoneLabel() {
            return this.microphoneOn ? call.labels.mute : call.labels.unmute;
        },
        get cameraLabel() {
            return this.cameraOn ? call.labels.cameraOff : call.labels.cameraOn;
        },

        async start() {
            this.status = "starting";
            // Fresh relay credentials for this call, before the camera turns on.
            const ice = await this._fetchIceServers();
            if (!ice) {
                this.status = "failed";
                return;
            }
            call.iceServers = ice.iceServers;
            call.iceTransportPolicy = ice.iceTransportPolicy;
            try {
                call.stream = await navigator.mediaDevices.getUserMedia({
                    audio: true,
                    video: { facingMode: call.facingMode },
                });
            } catch (error) {
                this.status =
                    error && error.name === "NotAllowedError" ? "denied" : "failed";
                return;
            }
            this.microphoneOn = true;
            this.cameraOn = true;
            this.$refs.local.srcObject = call.stream;
            this.canSwitchCamera = await this._hasSeveralCameras();
            call.ended = false;
            call.reconnectAttempt = 0;
            this.status = "waiting";
            this._openSocket();
        },

        toggleMicrophone() {
            this.microphoneOn = !this.microphoneOn;
            this._setTracksEnabled("audio", this.microphoneOn);
        },

        toggleCamera() {
            this.cameraOn = !this.cameraOn;
            this._setTracksEnabled("video", this.cameraOn);
        },

        async switchCamera() {
            if (!call.stream) {
                return;
            }
            const facingMode = call.facingMode === "user" ? "environment" : "user";
            let fresh;
            try {
                fresh = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: facingMode },
                });
            } catch (error) {
                return;
            }
            const track = fresh.getVideoTracks()[0];
            track.enabled = this.cameraOn;
            if (call.pc) {
                const sender = call.pc
                    .getSenders()
                    .find((s) => s.track && s.track.kind === "video");
                if (sender) {
                    await sender.replaceTrack(track);
                }
            }
            call.stream.getVideoTracks().forEach((old) => {
                call.stream.removeTrack(old);
                old.stop();
            });
            call.stream.addTrack(track);
            call.facingMode = facingMode;
            this.$refs.local.srcObject = call.stream;
        },

        hangUp() {
            if (call.ended) {
                return;
            }
            this._send({ type: "bye", id: call.myId });
            this._stop("ended");
        },

        _stop(status) {
            call.ended = true;
            this._closePeer();
            clearTimeout(call.reconnectTimer);
            if (call.socket) {
                call.socket.onclose = null;
                call.socket.close();
                call.socket = null;
            }
            if (call.stream) {
                call.stream.getTracks().forEach((t) => t.stop());
                call.stream = null;
            }
            this.$refs.local.srcObject = null;
            this.status = status;
        },

        // ---- signalling socket ----

        _openSocket() {
            const scheme = window.location.protocol === "https:" ? "wss://" : "ws://";
            const socket = new WebSocket(
                scheme + window.location.host + call.config.socketPath
            );
            call.socket = socket;
            socket.onopen = () => {
                call.reconnectAttempt = 0;
                this._send({ type: "hello", id: call.myId });
            };
            socket.onmessage = (event) => {
                let message;
                try {
                    message = JSON.parse(event.data);
                } catch (error) {
                    return;
                }
                call.signalQueue = call.signalQueue.then(() =>
                    this._handleSignal(message)
                );
            };
            socket.onclose = (event) => {
                if (call.ended) {
                    return;
                }
                if (event.code === CLOSE_REPLACED) {
                    // Another tab or a reload of this person took the call.
                    this._stop("replaced");
                    return;
                }
                // The media keeps flowing without the socket; only the call
                // set-up needs it, so reconnect quietly in the background.
                const delay =
                    RECONNECT_DELAYS_MS[
                        Math.min(call.reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)
                    ];
                call.reconnectAttempt += 1;
                call.reconnectTimer = setTimeout(() => this._openSocket(), delay);
            };
        },

        _send(message) {
            if (call.socket && call.socket.readyState === WebSocket.OPEN) {
                call.socket.send(JSON.stringify(message));
            }
        },

        async _handleSignal(message) {
            if (!message || message.id === call.myId) {
                return;
            }
            if (message.type === "hello") {
                this._pairWith(message.id);
                return;
            }
            if (message.type === "bye") {
                if (message.id === call.peerId) {
                    this._peerLeft();
                }
                return;
            }
            if (message.id !== call.peerId || !call.pc) {
                return;
            }
            try {
                if (message.type === "offer" || message.type === "answer") {
                    await this._onDescription(message.description);
                } else if (message.type === "candidate") {
                    await this._onCandidate(message.candidate);
                }
            } catch (error) {
                // A stale or garbled message from an earlier negotiation round.
            }
        },

        _pairWith(peerId) {
            if (peerId === call.peerId && call.pc) {
                return;
            }
            // The server keeps one socket per person, so a new id means the
            // other person reloaded or moved to another tab: follow them.
            const isNew = peerId !== call.peerId;
            this._closePeer();
            call.peerId = peerId;
            call.polite = call.myId < peerId;
            if (isNew) {
                // Make sure the other side knows our id too.
                this._send({ type: "hello", id: call.myId });
            }
            this._createPeer();
        },

        _createPeer() {
            const pc = new RTCPeerConnection({
                iceServers: call.iceServers,
                iceTransportPolicy: call.iceTransportPolicy,
            });
            call.pc = pc;
            call.makingOffer = false;
            call.ignoreOffer = false;
            call.pendingCandidates = [];
            pc.onnegotiationneeded = async () => {
                try {
                    call.makingOffer = true;
                    await pc.setLocalDescription();
                    this._send({
                        type: "offer",
                        id: call.myId,
                        description: pc.localDescription,
                    });
                } catch (error) {
                    // Renegotiation retries on the next negotiationneeded.
                } finally {
                    call.makingOffer = false;
                }
            };
            pc.onicecandidate = (event) => {
                this._send({ type: "candidate", id: call.myId, candidate: event.candidate });
            };
            pc.ontrack = (event) => {
                const remote = this.$refs.remote;
                if (remote.srcObject !== event.streams[0]) {
                    remote.srcObject = event.streams[0];
                    remote.play().catch(() => {});
                }
            };
            pc.onconnectionstatechange = () => this._onConnectionState(pc);
            // Only the impolite side offers: the polite side adds its tracks
            // when it answers. Two simultaneous offers would make one side
            // roll back and re-gather its network paths, which can stall.
            call.tracksAdded = false;
            if (!call.polite) {
                this._addTracks(pc);
            }
            this.status = "connecting";
            clearTimeout(call.connectTimer);
            clearTimeout(call.restartTimer);
            call.restartTimer = setTimeout(() => {
                // The impolite side only, so the two do not restart at once.
                if (call.pc === pc && pc.connectionState !== "connected" && !call.polite) {
                    pc.restartIce();
                }
            }, ICE_RESTART_AFTER_MS);
            call.connectTimer = setTimeout(() => {
                if (call.pc === pc && pc.connectionState !== "connected") {
                    this.status = "failed";
                }
            }, CONNECT_TIMEOUT_MS);
        },

        async _onDescription(description) {
            const pc = call.pc;
            const offerCollision =
                description.type === "offer" &&
                (call.makingOffer || pc.signalingState !== "stable");
            call.ignoreOffer = !call.polite && offerCollision;
            if (call.ignoreOffer) {
                return;
            }
            await pc.setRemoteDescription(description);
            if (description.type === "offer") {
                // Attached to the offer's transceivers, so the answer sends.
                this._addTracks(pc);
                await pc.setLocalDescription();
                this._send({ type: "answer", id: call.myId, description: pc.localDescription });
            }
            const pending = call.pendingCandidates;
            call.pendingCandidates = [];
            for (const candidate of pending) {
                await this._addCandidate(candidate);
            }
        },

        _addTracks(pc) {
            if (call.tracksAdded) {
                return;
            }
            call.tracksAdded = true;
            call.stream.getTracks().forEach((track) => pc.addTrack(track, call.stream));
        },

        async _onCandidate(candidate) {
            if (!candidate) {
                return;
            }
            if (!call.pc.remoteDescription) {
                // Its description is still on its way (or was an ignored
                // colliding offer whose network paths stay valid).
                call.pendingCandidates.push(candidate);
                return;
            }
            await this._addCandidate(candidate);
        },

        async _addCandidate(candidate) {
            try {
                await call.pc.addIceCandidate(candidate);
            } catch (error) {
                // A candidate from an abandoned negotiation round.
            }
        },

        _onConnectionState(pc) {
            if (call.pc !== pc || call.ended) {
                return;
            }
            const state = pc.connectionState;
            if (state === "connected") {
                clearTimeout(call.connectTimer);
                clearTimeout(call.restartTimer);
                this.status = "connected";
            } else if (state === "disconnected" || state === "failed") {
                this.status = "reconnecting";
                // In case the other side announced itself while our socket
                // was reconnecting, announce again so it can pair with us.
                this._send({ type: "hello", id: call.myId });
                if (state === "failed") {
                    // Usually a network switch (Wi-Fi to mobile data).
                    pc.restartIce();
                }
            }
        },

        _peerLeft() {
            this._closePeer();
            call.peerId = null;
            if (!call.ended) {
                this.status = "waiting";
            }
        },

        _closePeer() {
            clearTimeout(call.connectTimer);
            clearTimeout(call.restartTimer);
            if (call.pc) {
                call.pc.onnegotiationneeded = null;
                call.pc.onicecandidate = null;
                call.pc.ontrack = null;
                call.pc.onconnectionstatechange = null;
                call.pc.close();
                call.pc = null;
            }
            if (this.$refs.remote) {
                this.$refs.remote.srcObject = null;
            }
        },

        _setTracksEnabled(kind, enabled) {
            if (!call.stream) {
                return;
            }
            call.stream.getTracks().forEach((track) => {
                if (track.kind === kind) {
                    track.enabled = enabled;
                }
            });
        },

        async _fetchIceServers() {
            const token = document.querySelector("[name=csrfmiddlewaretoken]");
            const meta = document.querySelector('meta[name="csrf-token"]');
            try {
                const response = await fetch(call.iceUrl, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "X-CSRFToken": token
                            ? token.value
                            : meta
                              ? meta.getAttribute("content")
                              : "",
                    },
                });
                if (!response.ok) {
                    return null;
                }
                return await response.json();
            } catch (error) {
                return null;
            }
        },

        async _hasSeveralCameras() {
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                return devices.filter((d) => d.kind === "videoinput").length > 1;
            } catch (error) {
                return false;
            }
        },
    };
}

document.addEventListener("alpine:init", function () {
    // In-app video call (real-person check); coach pages load this bundle too.
    Alpine.data("videoCall", makeVideoCall);

    // Age range dual-handle slider for "Your Ideal Crush" preferences
    Alpine.data("ageRangeSlider", function () {
        return {
            minAge: 18,
            maxAge: 99,
            absoluteMin: 18,
            absoluteMax: 99,
            labelAnyAge: "Any age",

            init: function () {
                // Read translated label from data attribute
                var label = this.$el.getAttribute("data-label-any-age");
                if (label) {
                    this.labelAnyAge = label;
                }
                // Read initial values from data attributes set by Django template
                var initMin = this.$el.getAttribute("data-initial-min");
                var initMax = this.$el.getAttribute("data-initial-max");
                if (initMin) {
                    this.minAge = parseInt(initMin, 10) || this.absoluteMin;
                }
                if (initMax) {
                    this.maxAge = parseInt(initMax, 10) || this.absoluteMax;
                }
            },

            get rangeLabel() {
                if (
                    this.minAge === this.absoluteMin &&
                    this.maxAge === this.absoluteMax
                ) {
                    return this.labelAnyAge;
                }
                return this.minAge + " – " + this.maxAge;
            },

            get isDefaultRange() {
                return (
                    this.minAge === this.absoluteMin && this.maxAge === this.absoluteMax
                );
            },

            get notDefaultRange() {
                return !this.isDefaultRange;
            },

            get trackStyle() {
                var range = this.absoluteMax - this.absoluteMin;
                var left = ((this.minAge - this.absoluteMin) / range) * 100;
                var right = ((this.absoluteMax - this.maxAge) / range) * 100;
                return "--range-left:" + left + "%;--range-right:" + right + "%";
            },

            get minBubbleStyle() {
                var pct =
                    ((this.minAge - this.absoluteMin) /
                        (this.absoluteMax - this.absoluteMin)) *
                    100;
                return "left:" + pct + "%";
            },

            get maxBubbleStyle() {
                var pct =
                    ((this.maxAge - this.absoluteMin) /
                        (this.absoluteMax - this.absoluteMin)) *
                    100;
                return "left:" + pct + "%";
            },

            updateMin: function (event) {
                var val = parseInt(event.target.value, 10);
                if (val >= this.maxAge) {
                    val = this.maxAge - 1;
                    event.target.value = val;
                }
                if (val < this.absoluteMin) {
                    val = this.absoluteMin;
                    event.target.value = val;
                }
                this.minAge = val;
                this._syncHiddenInputs();
            },

            updateMax: function (event) {
                var val = parseInt(event.target.value, 10);
                if (val <= this.minAge) {
                    val = this.minAge + 1;
                    event.target.value = val;
                }
                if (val > this.absoluteMax) {
                    val = this.absoluteMax;
                    event.target.value = val;
                }
                this.maxAge = val;
                this._syncHiddenInputs();
            },

            resetRange: function () {
                this.minAge = this.absoluteMin;
                this.maxAge = this.absoluteMax;
                this._syncHiddenInputs();
            },

            _syncHiddenInputs: function () {
                var root = this.$el;
                var minInput = root.querySelector("#id_preferred_age_min");
                var maxInput = root.querySelector("#id_preferred_age_max");
                if (minInput) {
                    minInput.value = this.minAge;
                    minInput.setAttribute("value", this.minAge);
                }
                if (maxInput) {
                    maxInput.value = this.maxAge;
                    maxInput.setAttribute("value", this.maxAge);
                }
            },
        };
    });

    // Single-thumb height slider for Crush Connect "life" step. Optional field:
    // "engaged" tracks whether a real cm value is submitted; the "prefer not to
    // say" pill disengages without losing the thumb position. Only the hidden
    // input carries name="height_cm" (server-rendered so no-JS keeps the value).
    Alpine.data("heightSlider", function () {
        return {
            height: 170,
            engaged: false,
            absoluteMin: 120,
            absoluteMax: 230,
            unit: "cm",
            labelNoAnswer: "—",

            init: function () {
                var el = this.$el;
                var min = parseInt(el.getAttribute("data-min"), 10);
                var max = parseInt(el.getAttribute("data-max"), 10);
                var def = parseInt(el.getAttribute("data-default"), 10);
                if (min) {
                    this.absoluteMin = min;
                }
                if (max) {
                    this.absoluteMax = max;
                }
                var unit = el.getAttribute("data-unit");
                if (unit) {
                    this.unit = unit;
                }
                var label = el.getAttribute("data-label-no-answer");
                if (label) {
                    this.labelNoAnswer = label;
                }
                var initial = parseInt(el.getAttribute("data-initial"), 10);
                if (!isNaN(initial)) {
                    this.height = this._clamp(initial);
                    this.engaged = true;
                } else {
                    this.height = def || 170;
                    this.engaged = false;
                }
            },

            get isEngaged() {
                return this.engaged;
            },

            get valueLabel() {
                return this.engaged
                    ? this.height + " " + this.unit
                    : this.labelNoAnswer;
            },

            get bubbleLabel() {
                return this.height + " " + this.unit;
            },

            get trackStyle() {
                var pct =
                    ((this.height - this.absoluteMin) /
                        (this.absoluteMax - this.absoluteMin)) *
                    100;
                return "--range-left:0%;--range-right:" + (100 - pct) + "%";
            },

            get bubbleStyle() {
                var pct =
                    ((this.height - this.absoluteMin) /
                        (this.absoluteMax - this.absoluteMin)) *
                    100;
                return "left:" + pct + "%";
            },

            get sliderWrapClass() {
                return this.engaged ? "" : "opacity-40";
            },

            get noAnswerClass() {
                return this.engaged
                    ? "border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:border-crush-purple/50"
                    : "border-crush-purple bg-crush-purple/10 text-crush-purple";
            },

            get sliderValue() {
                return String(this.height);
            },

            get submitValue() {
                return this.engaged ? String(this.height) : "";
            },

            engage: function () {
                this.engaged = true;
            },

            update: function (event) {
                var val = parseInt(event.target.value, 10);
                if (isNaN(val)) {
                    return;
                }
                this.height = this._clamp(val);
                this.engaged = true;
            },

            clearHeight: function () {
                this.engaged = false;
            },

            _clamp: function (v) {
                return Math.min(this.absoluteMax, Math.max(this.absoluteMin, v));
            },
        };
    });

    // Crush Connect waitlist join button
    Alpine.data("crushConnectWaitlist", function () {
        return {
            onWaitlist: false,
            position: 0,
            total: 0,
            loading: false,
            error: "",

            init: function () {
                var el = this.$el.closest("[data-on-waitlist]");
                if (el) {
                    this.onWaitlist = el.getAttribute("data-on-waitlist") === "true";
                    this.position = parseInt(
                        el.getAttribute("data-position") || "0",
                        10,
                    );
                    this.total = parseInt(el.getAttribute("data-total") || "0", 10);
                }
            },

            get isJoined() {
                return this.onWaitlist;
            },
            // Bare getter for the pre-join UI: the CSP build can't evaluate
            // "!isJoined" inline, so x-show needs a named negation.
            get notJoined() {
                return !this.onWaitlist;
            },
            get isLoading() {
                return this.loading;
            },
            get hasError() {
                return this.error !== "";
            },
            get errorMessage() {
                return this.error;
            },

            get buttonClass() {
                if (this.onWaitlist) return "bg-white/20 cursor-default";
                return "bg-white text-teal-700 hover:bg-gray-100 shadow-lg";
            },

            get buttonText() {
                if (this.loading) return "...";
                if (this.onWaitlist) return "\u2713 On Waitlist";
                return "Join Waitlist";
            },

            get positionText() {
                if (!this.onWaitlist) return "";
                return "#" + this.position + " of " + this.total;
            },

            joinWaitlist: function () {
                if (this.onWaitlist || this.loading) return;
                var self = this;
                self.loading = true;
                self.error = "";

                var csrfToken = document.querySelector("[name=csrfmiddlewaretoken]");
                if (!csrfToken) {
                    csrfToken = document.querySelector('meta[name="csrf-token"]');
                }
                var token = csrfToken
                    ? csrfToken.value || csrfToken.getAttribute("content")
                    : "";

                fetch("/api/crush-connect/join/", {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": token,
                        "Content-Type": "application/json",
                    },
                    credentials: "same-origin",
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        self.loading = false;
                        self.onWaitlist = true;
                        self.position = data.position;
                        self.total = data.total;
                    })
                    .catch(function () {
                        self.loading = false;
                        self.error = gettext("Something went wrong. Please try again.");
                    });
            },
        };
    });

    // Crush Connect 4-step onboarding wizard.
    // Crush Connect onboarding — per-step selection helpers only. Step
    // navigation is now server-side (one URL per step), so this just powers the
    // step-3 interest 8-cap counter and the step-7 second-story toggle.
    Alpine.data("connectOnboarding", function () {
        return {
            interestMax: 8,
            interestCount: 0,
            storyCount: 0,
            _showSecondStory: false,

            get showSecondStory() {
                return this._showSecondStory;
            },
            get notShowSecondStory() {
                return !this._showSecondStory;
            },

            init: function () {
                // Interests (step 3): count pre-checked boxes and enforce the
                // cap by toggling `disabled` imperatively. The CSP-friendly
                // Alpine build forbids expressions (e.g. "atCap && !checked")
                // inside x-bind:disabled, so the cap is handled here in JS.
                this._syncInterests();
                // Story answer (step 7): seed the character counter from the
                // server-rendered value.
                var ta = this.$root.querySelector('textarea[name="story_answer"]');
                this.storyCount = ta ? ta.value.length : 0;
                this._showSecondStory =
                    this.$root.getAttribute("data-show-second-story") === "true";
            },

            onInterestChange: function () {
                this._syncInterests();
            },

            _syncInterests: function () {
                // $root, not $el: also invoked via @change on the checkboxes,
                // where $el is the checkbox itself and the queries find nothing.
                var boxes = this.$root.querySelectorAll('input[name="interests"]');
                var checked = 0;
                boxes.forEach(function (b) {
                    if (b.checked) checked++;
                });
                this.interestCount = checked;
                var atCap = checked >= this.interestMax;
                boxes.forEach(function (b) {
                    b.disabled = atCap && !b.checked;
                });
            },

            onStoryInput: function () {
                var ta = this.$root.querySelector('textarea[name="story_answer"]');
                this.storyCount = ta ? ta.value.length : 0;
            },

            toggleSecondStory: function () {
                this._showSecondStory = !this._showSecondStory;
            },
        };
    });

    // Step 7 "Read-the-Photo" pick step: count how many questions have a Yes/No
    // truth chosen (skip = value ""). The form validates "exactly 3"
    // server-side; JS only keeps the counter and selected row styling live.
    Alpine.data("connectGatePicker", function () {
        return {
            max: 3,
            count: 0,
            init: function () {
                this._sync();
            },
            onPick: function () {
                this._sync();
            },
            _sync: function () {
                // $root, not $el: invoked via @change on child radios, where
                // $el is the radio itself and the queries find nothing.
                var radios = this.$root.querySelectorAll('input[type="radio"][data-gate]');
                var picked = {};
                radios.forEach(function (r) {
                    if (r.checked && r.value) picked[r.name] = true;
                });
                this.count = Object.keys(picked).length;

                var rows = this.$root.querySelectorAll("[data-gate-row]");
                rows.forEach(function (row) {
                    var selected = false;
                    row.querySelectorAll('input[type="radio"][data-gate]').forEach(function (r) {
                        if (r.checked && r.value) selected = true;
                    });
                    row.classList.toggle("border-crush-purple", selected);
                    row.classList.toggle("bg-crush-purple/5", selected);
                });
            },
        };
    });

    // "Read-the-Photo" answer gate on a Drop card / compose page: enable the
    // submit button only once all 3 questions have a Yes/No guess. Radios work
    // without JS; the view also enforces "all three answered" server-side.
    Alpine.data("connectGateCard", function () {
        return {
            init: function () {
                this._sync();
            },
            onPick: function () {
                this._sync();
            },
            _sync: function () {
                // $root, not $el: invoked via @change on child radios, where
                // $el is the radio itself and the queries find nothing.
                var radios = this.$root.querySelectorAll('input[type="radio"][data-gate]');
                var answered = {};
                radios.forEach(function (r) {
                    if (r.checked) answered[r.name] = true;
                });
                var names = {};
                radios.forEach(function (r) {
                    names[r.name] = true;
                });
                var total = Object.keys(names).length;
                var done = Object.keys(answered).length;
                var submit = this.$root.querySelector("[data-gate-submit]");
                if (submit) submit.disabled = total === 0 || done < total;
            },
        };
    });

    // Connect Cycle temp chat: venue picker mode toggle (partner venue vs a
    // custom name/address). Mirrors connectGateCard's idiom — a bare-name
    // @change handler that re-queries the checked radio via $root, then
    // directly toggles the two field groups' visibility, rather than
    // reactive x-show state (CSP build can't pass args to methods, so
    // there's nothing to bind x-show to except a bare getter anyway; a
    // direct DOM toggle keeps this consistent with connectGateCard).
    Alpine.data("connectVenuePicker", function () {
        return {
            init: function () {
                this._sync();
            },
            onModeChange: function () {
                this._sync();
            },
            _sync: function () {
                var checked = this.$root.querySelector('input[name="venue_mode"]:checked');
                var mode = checked ? checked.value : "partner";
                var partnerFields = this.$root.querySelector("[data-venue-partner-fields]");
                var customFields = this.$root.querySelector("[data-venue-custom-fields]");
                if (partnerFields) partnerFields.classList.toggle("hidden", mode !== "partner");
                if (customFields) customFields.classList.toggle("hidden", mode !== "custom");
            },
        };
    });

    // Connect Week review card: opens/closes the per-card native <dialog>
    // "Choose" confirmation (UX Wave 3 · WP12 / 6-06). Template-local — one
    // dialog per card via $refs, not a shared cross-page sheet/store (that
    // name belongs to Wave 2's forthcoming shared component).
    //
    // Some WebViews (and very old browsers) have <dialog> without
    // HTMLDialogElement.showModal — openDialog() would then silently no-op
    // and the member could never send the request. Fall back to the
    // existing global confirm helper (window.crushConfirm, see
    // confirm-sheet.js), which itself falls back to window.confirm when
    // even that isn't available, and submit the form directly on accept.
    Alpine.data("connectReviewChoice", function () {
        return {
            openDialog: function () {
                var dialog = this.$refs.dialog;
                if (dialog && typeof dialog.showModal === "function") {
                    dialog.showModal();
                    return;
                }
                var message = this.$root.getAttribute("data-confirm-message") || "";
                var form = this.$root.querySelector("form");
                var submitForm = function () {
                    if (!form) return;
                    if (typeof form.requestSubmit === "function") {
                        form.requestSubmit();
                    } else {
                        form.submit();
                    }
                };
                if (typeof window.crushConfirm === "function") {
                    window.crushConfirm(message).then(function (ok) {
                        if (ok) submitForm();
                    });
                } else if (window.confirm(message)) {
                    submitForm();
                }
            },
            closeDialog: function () {
                var dialog = this.$refs.dialog;
                if (dialog && typeof dialog.close === "function") dialog.close();
            },
        };
    });
});
