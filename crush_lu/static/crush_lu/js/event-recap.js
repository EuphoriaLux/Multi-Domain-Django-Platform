/**
 * Crush Connect Event Lobby — 48-hour recap component (spec §7.7).
 *
 * The recap is a photo grid where members confirm who they actually met.
 * Confirmations are anonymous and irreversible; a reciprocal pair creates a
 * permanent "People I've Met" entry and reveals the first name. The live
 * WebSocket is closed at event end (§7.6), so the recap relies on a 20 s
 * polling fallback against the state API for the incoming-confirmation
 * counter and reveals.
 *
 * Privacy (§13): payloads carry opaque handles and — only for authorized
 * reveals (live mutuals, permanent encounters) — first names. The recap grid
 * never receives another participant's user id or pre-reveal name.
 */
document.addEventListener("alpine:init", function () {
    Alpine.data("eventRecap", function () {
        return {
            phase: "recap",
            secondsToClose: 0,
            incomingConfirmations: 0,
            closed: false,
            confirmOpen: false,
            confirmHandle: null,
            confirmPhotoUrl: "",
            confirmName: "",
            sending: false,
            banner: "",
            reveal: "",
            pollTimer: null,
            tickTimer: null,
            bannerTimer: null,

            init: function () {
                var root = this.$el;
                this.stateUrl = root.dataset.stateUrl;
                this.confirmUrl = root.dataset.confirmUrl;
                this.peopleUrl = root.dataset.peopleUrl;
                this.phase = root.dataset.phase || "recap";
                this.secondsToClose = parseInt(root.dataset.secondsToClose || "0", 10);
                this.incomingConfirmations = parseInt(root.dataset.incomingConfirmations || "0", 10);
                this.msgs = {
                    confirmed: root.dataset.msgConfirmed || "Confirmed. They only find out if they confirm you too.",
                    duplicate: root.dataset.msgDuplicate || "You already confirmed you met this person.",
                    alreadyMet: root.dataset.msgAlreadyMet || "You've already met this person.",
                    encounter: root.dataset.msgEncounter || "{name} was added to People I've Met.",
                    closed: root.dataset.msgClosed || "The recap window has closed.",
                    metLabel: root.dataset.msgMetLabel || "You've already met",
                };
                if (this.phase === "recap") {
                    this.startPolling();
                    this.startCountdown();
                } else {
                    this.closed = true;
                }
                this.bindGrid();
            },

            destroy: function () {
                if (this.pollTimer !== null) clearInterval(this.pollTimer);
                if (this.tickTimer !== null) clearInterval(this.tickTimer);
            },

            get countdownDisplay() {
                var s = Math.max(0, this.secondsToClose);
                var h = Math.floor(s / 3600);
                var m = Math.floor((s % 3600) / 60);
                var hh = h;
                var mm = (m < 10 ? "0" : "") + m;
                return hh + "h " + mm + "m";
            },

            get hasIncoming() {
                return this.incomingConfirmations > 0;
            },

            get notClosed() {
                return !this.closed;
            },

            get hasBanner() {
                return this.banner !== "";
            },

            get hasReveal() {
                return this.reveal !== "";
            },

            bindGrid: function () {
                var self = this;
                var grid = document.getElementById("recap-grid");
                if (!grid) return;
                grid.addEventListener("click", function (evt) {
                    var tile = evt.target.closest("button[data-handle]");
                    if (!tile) return;
                    if (tile.dataset.alreadyMet === "true") {
                        self.showBanner(self.msgs.metLabel);
                        return;
                    }
                    if (tile.disabled) return;
                    self.openConfirm(tile.dataset.handle, tile.dataset.photoUrl, tile.dataset.firstName || "");
                });
            },

            openConfirm: function (handle, photoUrl, firstName) {
                if (this.closed) return;
                this.confirmHandle = handle;
                this.confirmPhotoUrl = photoUrl || "";
                this.confirmName = firstName;
                this.confirmOpen = true;
            },

            closeConfirm: function () {
                this.confirmOpen = false;
                this.confirmHandle = null;
            },

            confirmMeeting: function () {
                var self = this;
                if (this.sending || !this.confirmHandle) return;
                this.sending = true;
                fetch(this.confirmUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "X-CSRFToken": this.getCsrfToken(),
                    },
                    body: JSON.stringify({ handle: this.confirmHandle }),
                })
                    .then(function (r) { return r.json().catch(function () { return null; }); })
                    .then(function (data) {
                        self.sending = false;
                        self.closeConfirm();
                        if (!data) return;
                        if (!data.ok) {
                            self.refetch();
                            return;
                        }
                        if (data.result === "confirmed") {
                            self.showBanner(self.msgs.confirmed);
                        } else if (data.result === "duplicate") {
                            self.showBanner(self.msgs.duplicate);
                        } else if (data.result === "already_met") {
                            self.showBanner(self.msgs.alreadyMet);
                        } else if (data.result === "phase_closed") {
                            self.markClosed();
                        } else if (data.result === "encounter") {
                            self.announceReveal(data.first_name);
                        }
                        self.refetch();
                    })
                    .catch(function () {
                        self.sending = false;
                        self.closeConfirm();
                    });
            },

            refetch: function () {
                var self = this;
                fetch(this.stateUrl, { headers: { "Accept": "application/json" } })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (data) {
                        if (!data || !data.ok) return;
                        self.applyState(data);
                    })
                    .catch(function () {});
            },

            applyState: function (data) {
                var state = data.state || {};
                this.secondsToClose = state.seconds_to_recap_close || 0;
                this.incomingConfirmations = state.incoming_confirmations || 0;
                if (state.phase && state.phase !== "recap") {
                    this.markClosed();
                    return;
                }
                this.renderRoster(data.roster || []);
            },

            renderRoster: function (roster) {
                var grid = document.getElementById("recap-grid");
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
                    "recap-tile relative aspect-square overflow-hidden rounded-2xl ring-1 ring-gray-200 dark:ring-slate-700 shadow-crush-sm focus:outline-none focus:ring-2 focus:ring-crush-purple transition-transform active:scale-[0.97]";
                var img = document.createElement("img");
                img.src = entry.photo_url;
                img.alt = "";
                img.loading = "lazy";
                img.className = "w-full h-full object-cover";
                tile.appendChild(img);
                if (entry.already_met) {
                    tile.dataset.alreadyMet = "true";
                    tile.dataset.firstName = entry.first_name || "";
                    tile.appendChild(this.buildBadge(this.msgs.metLabel, "met"));
                    tile.setAttribute("aria-label", entry.first_name || this.msgs.metLabel);
                } else if (entry.is_live_mutual) {
                    tile.dataset.firstName = entry.first_name || "";
                    tile.appendChild(this.buildBadge(entry.first_name || "", "mutual"));
                    tile.setAttribute("aria-label", entry.first_name || "");
                    if (entry.confirmed) tile.appendChild(this.buildConfirmedDot());
                } else {
                    tile.setAttribute("aria-label", "Confirm you met this participant");
                    if (entry.confirmed) tile.appendChild(this.buildConfirmedDot());
                }
                return tile;
            },

            buildBadge: function (text, kind) {
                var badge = document.createElement("span");
                if (kind === "met") {
                    badge.className = "absolute inset-x-0 bottom-0 bg-black/60 px-2 py-1 text-left text-xs font-medium text-white";
                } else {
                    badge.className = "absolute inset-x-0 bottom-0 bg-gradient-to-t from-crush-purple/90 to-transparent px-2 pb-1.5 pt-6 text-left text-xs font-semibold text-white";
                }
                badge.textContent = text;
                return badge;
            },

            buildConfirmedDot: function () {
                var dot = document.createElement("span");
                dot.className = "absolute top-1.5 right-1.5 w-3 h-3 rounded-full bg-crush-pink ring-2 ring-white dark:ring-slate-800";
                dot.setAttribute("aria-hidden", "true");
                return dot;
            },

            showBanner: function (text) {
                var self = this;
                this.banner = text;
                if (this.bannerTimer !== null) clearTimeout(this.bannerTimer);
                this.bannerTimer = setTimeout(function () { self.banner = ""; }, 6000);
            },

            announceReveal: function (firstName) {
                this.reveal = this.msgs.encounter.replace("{name}", firstName || "");
            },

            dismissReveal: function () {
                this.reveal = "";
            },

            markClosed: function () {
                this.closed = true;
                this.destroy();
            },

            startCountdown: function () {
                var self = this;
                this.tickTimer = setInterval(function () {
                    if (self.secondsToClose > 0) self.secondsToClose -= 1;
                    if (self.secondsToClose <= 0) self.markClosed();
                }, 60000);
            },

            startPolling: function () {
                var self = this;
                if (!this.stateUrl) return;
                this.pollTimer = setInterval(function () { self.refetch(); }, 20000);
            },

            getCsrfToken: function () {
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
