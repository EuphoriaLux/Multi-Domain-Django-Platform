/**
 * Crush Connect Event Lobby — live photo grid component (spec §7.2–§7.5).
 *
 * The server is authoritative: this component renders whatever the state API
 * returns and treats WebSocket messages purely as refetch hints (§11.1). A
 * 15 s polling fallback keeps the lobby correct when the channel layer is
 * down. Roster tiles are re-rendered imperatively (CSP Alpine build — no
 * x-for over fetched arrays).
 *
 * Privacy contract (§13): the payloads this component touches contain opaque
 * handles and — only for the viewer's own mutual reveals — first names. It
 * never receives another participant's user id or pre-mutual name, so it can
 * never leak one. User-facing strings come from data-msg-* attributes
 * ({% trans %} in the template); literals here are English fallbacks only.
 */
document.addEventListener("alpine:init", function () {
    Alpine.data("eventLobby", function () {
        return {
            phase: "live",
            secondsToEnd: 0,
            signalsRemaining: 0,
            signalsTotal: 3,
            incomingCount: 0,
            ended: false,
            confirmOpen: false,
            confirmHandle: null,
            confirmPhotoUrl: "",
            sending: false,
            banner: "",
            reveal: "",
            ws: null,
            wsRetry: 0,
            pollTimer: null,
            tickTimer: null,
            bannerTimer: null,

            init: function () {
                var root = this.$el;
                this.stateUrl = root.dataset.stateUrl;
                this.signalUrl = root.dataset.signalUrl;
                this.wsPath = root.dataset.wsPath;
                this.phase = root.dataset.phase || "live";
                this.secondsToEnd = parseInt(root.dataset.secondsToEnd || "0", 10);
                this.signalsRemaining = parseInt(root.dataset.signalsRemaining || "0", 10);
                this.signalsTotal = parseInt(root.dataset.signalsTotal || "3", 10);
                this.incomingCount = parseInt(root.dataset.incomingCount || "0", 10);
                this.msgs = {
                    arrived: root.dataset.msgArrived || "Someone new arrived.",
                    joined: root.dataset.msgJoined || "Someone new joined the Event Lobby.",
                    sent: root.dataset.msgSent || "Signal sent. They won't know it was you unless it's mutual.",
                    duplicate: root.dataset.msgDuplicate || "You already sent this person a signal.",
                    quota: root.dataset.msgQuota || "You've used all three signals for this event.",
                    mutual: root.dataset.msgMutual || "You and {name} would like to meet. Say hello now.",
                    alreadyMet: root.dataset.msgAlreadyMet || "You've already met this person.",
                    ended: root.dataset.msgEnded || "The live lobby has ended.",
                    tileLabel: root.dataset.msgTileLabel || "Select participant photo",
                    sentBadge: root.dataset.msgSentBadge || "Signal sent",
                    metBadge: root.dataset.msgMetBadge || "You've already met",
                };

                if (this.phase === "live") {
                    this.connectWebSocket();
                    this.startPolling();
                    this.startCountdown();
                } else {
                    this.ended = true;
                }
                this.bindGrid();
            },

            destroy: function () {
                if (this.pollTimer !== null) clearInterval(this.pollTimer);
                if (this.tickTimer !== null) clearInterval(this.tickTimer);
                if (this.ws) {
                    this.ws.onclose = null;
                    this.ws.close();
                }
            },

            // --- Header displays -------------------------------------------

            get countdownDisplay() {
                var s = Math.max(0, this.secondsToEnd);
                var h = Math.floor(s / 3600);
                var m = Math.floor((s % 3600) / 60);
                var sec = s % 60;
                var mm = (m < 10 ? "0" : "") + m;
                var ss = (sec < 10 ? "0" : "") + sec;
                return h > 0 ? h + ":" + mm + ":" + ss : mm + ":" + ss;
            },

            get signalsDisplay() {
                return this.signalsRemaining + " / " + this.signalsTotal;
            },

            // Named getters keep the three static signal indicators compatible
            // with Alpine's CSP build (no x-for or inline ternary expressions).
            get firstSignalUsed() {
                return this.signalsTotal - this.signalsRemaining >= 1;
            },

            get secondSignalUsed() {
                return this.signalsTotal - this.signalsRemaining >= 2;
            },

            get thirdSignalUsed() {
                return this.signalsTotal - this.signalsRemaining >= 3;
            },

            get firstSignalAvailable() {
                return this.signalsRemaining >= 1;
            },

            get secondSignalAvailable() {
                return this.signalsRemaining >= 2;
            },

            get thirdSignalAvailable() {
                return this.signalsRemaining >= 3;
            },

            get hasIncoming() {
                return this.incomingCount > 0;
            },

            get notEnded() {
                return !this.ended;
            },

            // CSP-build note: x-show takes property paths only, so the
            // send-button's idle label binds this getter rather than
            // "!sending" (which Alpine's CSP build cannot evaluate).
            get notSending() {
                return !this.sending;
            },

            // --- Grid interaction ------------------------------------------

            bindGrid: function () {
                var self = this;
                var grid = document.getElementById("lobby-grid");
                if (!grid) return;
                grid.addEventListener("click", function (evt) {
                    var tile = evt.target.closest("button[data-handle]");
                    if (!tile || tile.disabled) return;
                    self.openConfirm(tile.dataset.handle, tile.dataset.photoUrl);
                });
            },

            openConfirm: function (handle, photoUrl) {
                if (this.ended || this.signalsRemaining <= 0) {
                    if (this.signalsRemaining <= 0) this.showBanner(this.msgs.quota);
                    return;
                }
                this.confirmHandle = handle;
                this.confirmPhotoUrl = photoUrl || "";
                this.confirmOpen = true;
            },

            closeConfirm: function () {
                this.confirmOpen = false;
                this.confirmHandle = null;
            },

            confirmSignal: function () {
                var self = this;
                if (this.sending || !this.confirmHandle) return;
                this.sending = true;
                fetch(this.signalUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "X-CSRFToken": this.getCsrfToken(),
                    },
                    body: JSON.stringify({ handle: this.confirmHandle }),
                })
                    .then(function (r) {
                        return r.json().catch(function () { return null; });
                    })
                    .then(function (data) {
                        self.sending = false;
                        self.closeConfirm();
                        if (!data) return;
                        if (!data.ok) {
                            // Authorization/state changed under us — re-sync.
                            self.refetch();
                            return;
                        }
                        if (typeof data.signals_remaining === "number") {
                            self.signalsRemaining = data.signals_remaining;
                        }
                        if (data.result === "sent") {
                            self.showBanner(self.msgs.sent);
                        } else if (data.result === "duplicate") {
                            self.showBanner(self.msgs.duplicate);
                        } else if (data.result === "quota_exhausted") {
                            self.showBanner(self.msgs.quota);
                        } else if (data.result === "already_met") {
                            self.showBanner(self.msgs.alreadyMet);
                        } else if (data.result === "phase_closed") {
                            self.markEnded();
                        } else if (data.result === "mutual") {
                            self.announceReveal(data.first_name);
                        }
                        self.refetch();
                    })
                    .catch(function () {
                        self.sending = false;
                        self.closeConfirm();
                    });
            },

            // --- Authoritative state ---------------------------------------

            refetch: function () {
                var self = this;
                fetch(this.stateUrl, { headers: { "Accept": "application/json" } })
                    .then(function (r) { return self.readStateResponse(r); })
                    .then(function (data) {
                        if (!data || !data.ok) return;
                        self.applyState(data);
                    })
                    .catch(function () {});
            },

            readStateResponse: function (response) {
                if (response.ok) return response.json();
                if ([401, 403, 404].indexOf(response.status) !== -1) {
                    this.revokeAccess();
                }
                return null;
            },

            revokeAccess: function () {
                // Eligibility is evaluated on every state request. Purge all
                // identity-bearing DOM and component state immediately when
                // the server withdraws access; a stale open page must not
                // retain roster photos or mutual names.
                this.secondsToEnd = 0;
                this.signalsRemaining = 0;
                this.incomingCount = 0;
                this.sending = false;
                this.closeConfirm();
                this.confirmPhotoUrl = "";
                this.banner = "";
                this.reveal = "";
                this.renderRoster([]);
                this.renderMutuals([]);
                this.markEnded();
            },

            applyState: function (data) {
                var state = data.state || {};
                this.secondsToEnd = state.seconds_to_end || 0;
                this.signalsRemaining = state.signals_remaining || 0;
                this.signalsTotal = state.signals_total || this.signalsTotal;
                this.incomingCount = state.incoming_count || 0;
                if (state.phase && state.phase !== "live") {
                    this.markEnded();
                    return;
                }
                this.renderRoster(data.roster || []);
                this.renderMutuals(data.mutuals || []);
            },

            renderRoster: function (roster) {
                var grid = document.getElementById("lobby-grid");
                if (!grid) return;
                grid.textContent = "";
                for (var i = 0; i < roster.length; i++) {
                    grid.appendChild(this.buildTile(roster[i]));
                }
            },

            buildTile: function (entry) {
                var tile = document.createElement("button");
                tile.type = "button";
                tile.dataset.handle = entry.handle;
                tile.dataset.photoUrl = entry.photo_url;
                tile.className =
                    "lobby-tile relative aspect-square overflow-hidden rounded-2xl ring-1 ring-gray-200 dark:ring-slate-700 shadow-crush-sm focus:outline-none focus:ring-2 focus:ring-crush-purple transition-transform active:scale-[0.97]";
                // §14: generic accessible name pre-reveal — never the first name.
                tile.setAttribute("aria-label", this.msgs.tileLabel);
                var img = document.createElement("img");
                img.src = entry.photo_url;
                img.alt = "";
                img.loading = "lazy";
                img.className = "w-full h-full object-cover";
                tile.appendChild(img);
                if (entry.already_met) {
                    tile.disabled = true;
                    tile.appendChild(this.buildTileBadge(this.msgs.metBadge, false));
                    tile.setAttribute("aria-label", entry.first_name || this.msgs.tileLabel);
                } else if (entry.is_mutual) {
                    tile.disabled = true;
                    tile.appendChild(this.buildTileBadge(entry.first_name || "", true));
                    tile.setAttribute("aria-label", entry.first_name || this.msgs.tileLabel);
                } else if (entry.signalled) {
                    tile.disabled = true;
                    tile.appendChild(this.buildTileBadge(this.msgs.sentBadge, false));
                }
                return tile;
            },

            buildTileBadge: function (text, isMutual) {
                var badge = document.createElement("span");
                badge.className = isMutual
                    ? "absolute inset-x-0 bottom-0 bg-gradient-to-t from-crush-purple/90 to-transparent px-2 pb-1.5 pt-6 text-left text-xs font-semibold text-white"
                    : "absolute inset-x-0 bottom-0 bg-black/50 px-2 py-1 text-left text-xs font-medium text-white";
                badge.textContent = text;
                return badge;
            },

            renderMutuals: function (mutuals) {
                var wrap = document.getElementById("lobby-mutuals");
                var list = document.getElementById("lobby-mutuals-list");
                if (!wrap || !list) return;
                wrap.classList.toggle("hidden", mutuals.length === 0);
                list.textContent = "";
                for (var i = 0; i < mutuals.length; i++) {
                    var m = mutuals[i];
                    var item = document.createElement("div");
                    item.className =
                        "flex items-center gap-3 rounded-2xl ring-1 ring-crush-purple/30 bg-white dark:bg-slate-800 p-3 shadow-crush-sm";
                    var img = document.createElement("img");
                    img.src = m.photo_url;
                    img.alt = "";
                    img.className = "w-12 h-12 rounded-xl object-cover";
                    var name = document.createElement("p");
                    name.className = "font-semibold text-gray-900 dark:text-white mb-0";
                    name.textContent = m.first_name;
                    item.appendChild(img);
                    item.appendChild(name);
                    list.appendChild(item);
                }
            },

            // --- Banners / reveal announcements ----------------------------

            showBanner: function (text) {
                var self = this;
                this.banner = text;
                if (this.bannerTimer !== null) clearTimeout(this.bannerTimer);
                this.bannerTimer = setTimeout(function () { self.banner = ""; }, 6000);
            },

            get hasBanner() {
                return this.banner !== "";
            },

            announceReveal: function (firstName) {
                // §14: announced through the aria-live region in the template.
                this.reveal = this.msgs.mutual.replace("{name}", firstName || "");
            },

            get hasReveal() {
                return this.reveal !== "";
            },

            dismissReveal: function () {
                this.reveal = "";
            },

            markEnded: function () {
                // §7.6: reject-at-end is server-side; this only flips the UI.
                this.ended = true;
                this.destroy();
            },

            // --- Countdown -------------------------------------------------

            startCountdown: function () {
                var self = this;
                this.tickTimer = setInterval(function () {
                    if (self.secondsToEnd > 0) {
                        self.secondsToEnd -= 1;
                    }
                    if (self.secondsToEnd <= 0) {
                        self.markEnded();
                    }
                }, 1000);
            },

            // --- Realtime: WS hints + polling safety net -------------------

            connectWebSocket: function () {
                var self = this;
                if (!this.wsPath || !window.WebSocket) return;
                var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                this.ws = new WebSocket(protocol + "//" + window.location.host + this.wsPath);
                this.ws.onmessage = function (event) {
                    var msg;
                    try { msg = JSON.parse(event.data); } catch (e) { return; }
                    if (msg.type === "joined") {
                        self.showBanner(msg.data && msg.data.onboarded ? self.msgs.joined : self.msgs.arrived);
                        self.refetch();
                    } else if (msg.type === "counter") {
                        if (msg.data && typeof msg.data.incoming_count === "number") {
                            self.incomingCount = msg.data.incoming_count;
                        }
                    } else if (msg.type === "mutual") {
                        // Identity never travels over the socket — fetch the
                        // authoritative state and announce the newest reveal.
                        fetch(self.stateUrl, { headers: { "Accept": "application/json" } })
                            .then(function (r) { return self.readStateResponse(r); })
                            .then(function (data) {
                                if (!data || !data.ok) return;
                                self.applyState(data);
                                var mutuals = data.mutuals || [];
                                if (mutuals.length > 0) {
                                    self.announceReveal(mutuals[0].first_name);
                                }
                            })
                            .catch(function () {});
                    } else if (msg.type === "phase") {
                        self.markEnded();
                    }
                };
                this.ws.onclose = function () {
                    if (self.wsRetry < 5 && !self.ended) {
                        self.wsRetry += 1;
                        setTimeout(function () { self.connectWebSocket(); }, 2000 * self.wsRetry);
                    }
                };
            },

            startPolling: function () {
                var self = this;
                if (!this.stateUrl) return;
                // Safety-net poll regardless of socket health (§11.1 —
                // correctness must never depend on Redis).
                this.pollTimer = setInterval(function () { self.refetch(); }, 15000);
            },

            getCsrfToken: function () {
                // CSRF_COOKIE_HTTPONLY is on — use the hidden input from
                // base.html (same as cache-play.js / htmx-csrf.js).
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
