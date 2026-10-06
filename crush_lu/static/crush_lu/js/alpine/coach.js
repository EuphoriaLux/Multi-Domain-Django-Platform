/**
 * Alpine.js CSP components for Crush.lu — coach bundle.
 *
 * Coach tools: the /coach/ pages and the Django-admin Coach Panel
 * (admin/base_site.html).
 * Loaded with crush_lu/partials/alpine_bundle.html, before Alpine starts.
 * Build: `npm run build:js`.
 */

import { makeModal, mixin } from "./shared.js";

document.addEventListener("alpine:init", function () {
    Alpine.data("photoSwipeDeck", function () {
        return mixin(makeModal(false), {
            rootElement: null,
            cards: [],
            totalWaiting: 0,
            activePhotoIndex: 0,
            isSubmitting: false,
            isLoading: false,
            errorMessage: "",
            selectedReason: "unclear_face",
            flagNotes: "",
            lastDecision: null,
            isDragging: false,
            startX: 0,
            startY: 0,
            deltaX: 0,
            deltaY: 0,
            init() {
                this.rootElement = this.$el;
                this.cards = JSON.parse(
                    document.getElementById("photo-review-cards").textContent,
                );
                this.totalWaiting = Number(this.rootElement.dataset.total);
            },
            get currentCard() {
                return this.cards[0] || null;
            },
            get hasCard() {
                return !!this.currentCard;
            },
            get hasError() {
                return !!this.errorMessage;
            },
            get isEmpty() {
                return !this.hasCard && !this.isLoading;
            },
            get actionsDisabled() {
                return this.isSubmitting || this.isLoading || !this.hasCard;
            },
            get undoDisabled() {
                return this.isSubmitting || this.isLoading || !this.lastDecision;
            },
            get cardName() {
                return this.currentCard ? this.currentCard.display_name : "";
            },
            get cardDetails() {
                return this.currentCard
                    ? [
                          this.currentCard.age,
                          this.currentCard.gender,
                          this.currentCard.location,
                      ]
                          .filter(Boolean)
                          .join(" · ")
                    : "";
            },
            get cardStory() {
                return this.currentCard ? this.currentCard.story_text : "";
            },
            get cardGoal() {
                return this.currentCard ? this.currentCard.relationship_goal : "";
            },
            get hasLuxid() {
                return !!(this.currentCard && this.currentCard.is_luxid_verified);
            },
            get hasAttendedEvent() {
                return !!(this.currentCard && this.currentCard.has_attended_event);
            },
            get hasMultiplePhotos() {
                return !!(this.currentCard && this.currentCard.photos.length > 1);
            },
            get isFakeReason() { return this.selectedReason === "fake_profile"; },
            get isInappropriateReason() { return this.selectedReason === "inappropriate"; },
            get isUnclearReason() { return this.selectedReason === "unclear_face"; },
            get isGroupReason() { return this.selectedReason === "group_photo"; },
            get isOtherReason() { return this.selectedReason === "other"; },
            selectFlagReason(event) { this.selectedReason = event.target.value; },
            updateFlagNotes(event) { this.flagNotes = event.target.value; },
            get currentPhotoUrl() {
                return this.currentCard
                    ? this.currentCard.photos[this.activePhotoIndex].url
                    : "";
            },
            get cardTransformStyle() {
                return this.isDragging
                    ? `transform: translate(${this.deltaX}px, ${this.deltaY}px) rotate(${this.deltaX * 0.08}deg)`
                    : "";
            },
            startDrag(event) {
                if (
                    this.actionsDisabled ||
                    this.isModalOpen ||
                    (event.pointerType === "mouse" && event.button !== 0)
                )
                    return;
                this.isDragging = true;
                this.startX = event.clientX;
                this.startY = event.clientY;
                event.currentTarget.setPointerCapture(event.pointerId);
            },
            onDrag(event) {
                if (!this.isDragging) return;
                this.deltaX = event.clientX - this.startX;
                this.deltaY = event.clientY - this.startY;
            },
            endDrag() {
                if (!this.isDragging) return;
                const dx = this.deltaX,
                    dy = this.deltaY;
                this.cancelDrag();
                if (dx > 90) this.approveCurrentCard();
                else if (dx < -90) this.openFlagModal();
                else if (dy < -110 && Math.abs(dx) < 60) this.skipCard();
            },
            cancelDrag() {
                this.isDragging = false;
                this.deltaX = 0;
                this.deltaY = 0;
            },
            nextPhoto() {
                if (this.hasCard)
                    this.activePhotoIndex =
                        (this.activePhotoIndex + 1) % this.currentCard.photos.length;
            },
            prevPhoto() {
                if (this.hasCard)
                    this.activePhotoIndex =
                        (this.activePhotoIndex + this.currentCard.photos.length - 1) %
                        this.currentCard.photos.length;
            },
            handleKeydown(event) {
                if (
                    this.isModalOpen ||
                    this.isSubmitting ||
                    this.isLoading ||
                    event.target.closest(
                        "input, textarea, select, button, a, [contenteditable]",
                    )
                )
                    return;
                const key = event.key.toLowerCase();
                if (key === "u") {
                    event.preventDefault();
                    this.undoLastDecision();
                    return;
                }
                if (!this.hasCard) return;
                if (key === "l" || key === "arrowright") this.approveCurrentCard();
                else if (key === "h" || key === "arrowleft") this.openFlagModal();
                else if (key === "k") this.openRevisionModal();
                else if (key === "arrowup") this.skipCard();
                else if (key === " ") this.nextPhoto();
                else return;
                event.preventDefault();
            },
            skipCard() {
                if (this.actionsDisabled) return;
                this.cards.push(this.cards.shift());
                this.activePhotoIndex = 0;
            },
            openFlagModal() {
                if (this.actionsDisabled) return;
                this.selectedReason = "fake_profile";
                this.flagNotes = "";
                this._modalReturnFocus = document.activeElement;
                this.showModal();
                this.$nextTick(() =>
                    this.rootElement.querySelector('input[value="fake_profile"]').focus(),
                );
            },
            openRevisionModal() {
                this.openFlagModal();
                this.selectedReason = "unclear_face";
                this.$nextTick(() =>
                    this.rootElement.querySelector('input[value="unclear_face"]').focus(),
                );
            },
            closeFlagModal() {
                if (this.isSubmitting || !this.isModalOpen) return;
                this.hideModal();
                if (this._modalReturnFocus) this._modalReturnFocus.focus();
            },
            trapModalFocus(event) {
                const elements = Array.from(
                    event.currentTarget.querySelectorAll("input, textarea, button"),
                ).filter((el) => !el.disabled);
                const first = elements[0],
                    last = elements[elements.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            },
            async _post(url, payload) {
                const response = await fetch(url, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": this.rootElement.querySelector(
                            '[name="csrfmiddlewaretoken"]',
                        ).value,
                    },
                    body: JSON.stringify(payload),
                });
                const data = await response.json();
                if (!response.ok || !data.success)
                    throw new Error(data.error || this.rootElement.dataset.error);
                return data;
            },
            async approveCurrentCard() {
                await this._decide("approved");
            },
            async submitFlagDecision() {
                await this._decide(
                    this.selectedReason === "fake_profile"
                        ? "flagged_fake"
                        : "needs_revision",
                );
            },
            async _decide(decision) {
                if (this.actionsDisabled) return;
                const card = this.currentCard;
                this.isSubmitting = true;
                this.errorMessage = "";
                try {
                    const data = await this._post(this.rootElement.dataset.decideUrl, {
                        profile_id: card.id,
                        photo_key: card.photo_key,
                        decision,
                        reason:
                            decision === "approved"
                                ? "clear_authentic"
                                : this.selectedReason,
                        notes: decision === "approved" ? "" : this.flagNotes,
                    });
                    this.lastDecision = { logId: data.log_id, card };
                    this.cards.shift();
                    this.activePhotoIndex = 0;
                    this.totalWaiting = Math.max(0, this.totalWaiting - 1);
                    this.hideModal();
                    this.$nextTick(() => this.rootElement.focus());
                } catch (error) {
                    this.errorMessage = error.message || this.rootElement.dataset.error;
                } finally {
                    this.isSubmitting = false;
                }
                if (this.cards.length < 5) await this.fetchMoreCards();
            },
            async undoLastDecision() {
                if (this.undoDisabled) return;
                this.isSubmitting = true;
                this.errorMessage = "";
                try {
                    await this._post(this.rootElement.dataset.undoUrl, {
                        log_id: this.lastDecision.logId,
                    });
                    this.cards = this.cards.filter(
                        (card) => card.id !== this.lastDecision.card.id,
                    );
                    this.cards.unshift(this.lastDecision.card);
                    this.lastDecision = null;
                    this.activePhotoIndex = 0;
                    this.totalWaiting++;
                } catch (error) {
                    this.errorMessage = error.message || this.rootElement.dataset.error;
                } finally {
                    this.isSubmitting = false;
                }
            },
            async fetchMoreCards() {
                if (this.isLoading || this.isSubmitting) return;
                this.isLoading = true;
                try {
                    const response = await fetch(this.rootElement.dataset.moreUrl);
                    if (!response.ok) throw new Error(this.rootElement.dataset.error);
                    const data = await response.json();
                    const ids = new Set(this.cards.map((card) => card.id));
                    data.cards.forEach((card) => {
                        if (!ids.has(card.id)) this.cards.push(card);
                    });
                    this.totalWaiting = data.total_waiting;
                } catch (error) {
                    this.errorMessage = error.message || this.rootElement.dataset.error;
                } finally {
                    this.isLoading = false;
                }
            },
        });
    });
    // The only path the coach door scanner may POST a scanned QR to — see
    // coachCheckin._checkinPathFromScan. Group 1 is the registration id.
    var CHECKIN_API_PATH_RE = /^\/api\/events\/checkin\/(\d+)\/[^\/]+\/$/;

    // Coach member-overview verification disclosure.
    //
    // Composed through `mixin`, not Object.assign/spread: `makeModal` exposes
    // `isModalOpen` / `isModalClosed` as getters, and a spread copies their
    // evaluated value once instead of the accessor, silently freezing the
    // panel shut. The template binds the getters rather than `!open` because
    // the CSP build evaluates only bare property and method names.
    Alpine.data("verifyMemberPanel", function () {
        return mixin({}, makeModal(false));
    });

    // Coach check-in scanner component
    Alpine.data("coachCheckin", function () {
        return {
            // Scanner state
            scannerActive: false,
            scanner: null,
            result: false,
            success: false,
            errorState: false,
            message: "",
            checkins: [],
            lastTableNumber: 0,
            lastRole: "",
            torchAvailable: false,
            torchOn: false,
            scanBusy: false,
            startPending: false,

            // Thermal Printer state (PEC 80 / RawBT)
            printerEnabled: localStorage.getItem("crush_printer_enabled") === "true",
            autoPrint: localStorage.getItem("crush_autoprint_enabled") !== "false",

            // WebSocket state
            ws: null,
            connected: false,
            reconnectAttempts: 0,
            stableTimer: null,
            eventId: 0,

            // Toast state
            toasts: [],
            toastCounter: 0,

            // Deduplication
            processedIds: {},

            // Monotonic sequence for _refetchSummary: only the latest fetch
            // may apply its response (out-of-order arrival guard).
            _summarySeq: 0,

            init: function () {
                this.eventId = parseInt(this.$el.getAttribute("data-event-id")) || 0;
                if (this.eventId) {
                    this.connectWebSocket();
                }
            },

            // --- Getters (CSP-safe) ---
            get scannerButtonText() {
                return this.scannerActive ? "Stop Scanner" : "Start Scanner";
            },
            get hasResult() {
                return this.result;
            },
            get isSuccess() {
                return this.success;
            },
            get isError() {
                return this.errorState;
            },
            get resultMessage() {
                return this.message;
            },
            get hasTableAssignment() {
                return this.lastTableNumber > 0;
            },
            get tableAssignmentText() {
                return "Table " + this.lastTableNumber;
            },
            get recentCheckinCount() {
                return this.checkins.length;
            },
            get isConnected() {
                return this.connected;
            },
            get connectionDot() {
                return this.connected ? "bg-green-500" : "bg-gray-400 dark:bg-gray-600";
            },
            get connectionLabel() {
                return this.connected ? "Live" : "Offline";
            },
            get torchIsAvailable() {
                return this.torchAvailable;
            },
            get torchButtonText() {
                var i18n = window._checkinI18n || {};
                return this.torchOn
                    ? i18n.torchOff || "Flash Off"
                    : i18n.torchOn || "Flash On";
            },
            get printerButtonText() {
                var i18n = window._checkinI18n || {};
                return this.printerEnabled
                    ? i18n.printerOn || "Printer: ON"
                    : i18n.printerOff || "Printer: OFF";
            },
            // Alpine runs under the CSP build here (no inline ternaries in
            // directives) — these mirror printerButtonText so :class/:title
            // can bind to a bare getter like every other conditional style
            // in this file, instead of an inline ternary expression.
            get printerButtonClass() {
                return this.printerEnabled
                    ? "bg-purple-50 text-crush-purple border-crush-purple/30 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-600"
                    : "bg-gray-50 text-gray-500 border-gray-200 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700";
            },
            get printerButtonTitle() {
                var i18n = window._checkinI18n || {};
                return this.printerEnabled
                    ? i18n.printerActiveTitle || "Thermal printer active (RawBT)"
                    : i18n.printerDisabledTitle || "Thermal printer disabled";
            },

            // --- HTML helpers (XSS protection) ---
            _esc: function (str) {
                if (!str) return "";
                var div = document.createElement("div");
                div.appendChild(document.createTextNode(str));
                return div.innerHTML;
            },
            _escAttr: function (str) {
                return this._esc(str).replace(/"/g, "&quot;");
            },

            // --- Imperative DOM rendering (CSP-safe) ---
            _renderToastElement: function (t) {
                var container = document.getElementById("checkin-toasts-container");
                if (!container) return;
                var i18n = window._checkinI18n || {};
                var photoHtml;
                if (t.hasPhoto) {
                    photoHtml =
                        '<img src="' +
                        this._escAttr(t.photoUrl) +
                        '" class="w-full h-full object-cover" alt="">';
                } else {
                    photoHtml =
                        '<div class="w-full h-full bg-gradient-to-br from-crush-purple/20 to-crush-pink/20 dark:from-crush-purple/30 dark:to-crush-pink/30 flex items-center justify-center">' +
                        '<svg class="w-6 h-6 text-crush-purple/50 dark:text-crush-pink/50" fill="currentColor" viewBox="0 0 20 20">' +
                        '<path fill-rule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clip-rule="evenodd"/>' +
                        "</svg></div>";
                }
                var approvedHtml = t.isApproved
                    ? ' <span class="text-green-500 text-xs shrink-0" title="' +
                      this._escAttr(i18n.verified || "Verified") +
                      '">&#10003;</span>'
                    : "";
                var tableHtml = t.hasTable
                    ? ' <span class="text-crush-purple dark:text-purple-300 font-medium">' +
                      this._esc(t.tableLabel) +
                      "</span>"
                    : "";
                var locationHtml = t.location
                    ? "<span>\uD83D\uDCCD " + this._esc(t.location) + "</span>"
                    : "";
                var interestsHtml = t.interests
                    ? '<p class="text-xs text-gray-400 dark:text-gray-500 mt-1 truncate">' +
                      this._esc(t.interests) +
                      "</p>"
                    : "";
                // Unverified profile warning banner
                var warningHtml = "";
                if (!t.isApproved) {
                    var coachLine = t.coachName
                        ? '<span class="font-medium">' +
                          this._esc(i18n.coach || "Coach") +
                          ": " +
                          this._esc(t.coachName) +
                          "</span>"
                        : "";
                    var statusLine = t.submissionStatus
                        ? ' <span class="opacity-75">\u00B7 ' +
                          this._esc(t.submissionStatus) +
                          "</span>"
                        : "";
                    warningHtml =
                        '<div class="mt-2 bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700 rounded-lg px-3 py-2 text-xs text-amber-800 dark:text-amber-300">' +
                        '<div class="flex items-center gap-1.5 font-semibold mb-0.5">' +
                        '<svg class="w-3.5 h-3.5 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>' +
                        this._esc(i18n.unverified || "Unverified Profile") +
                        "</div>" +
                        (coachLine || statusLine
                            ? "<div>" + coachLine + statusLine + "</div>"
                            : "") +
                        "</div>";
                }
                // Photo mismatch action on toast for verified profiles
                var rejectBtnHtml = "";
                if (t.isApproved && t.regId && this.eventId) {
                    var rejectUrl =
                        "/api/events/" +
                        this.eventId +
                        "/reject-verification/" +
                        t.regId +
                        "/";
                    rejectBtnHtml =
                        '<div class="mt-2 pt-2 border-t border-gray-100 dark:border-gray-700 flex justify-end">' +
                        '<button type="button" class="toast-reject-btn text-xs text-amber-600 dark:text-amber-400 hover:text-red-600 dark:hover:text-red-400 font-medium flex items-center gap-1 transition-colors" data-reject-url="' +
                        this._escAttr(rejectUrl) +
                        '" data-reg-id="' +
                        this._escAttr(t.regId) +
                        '" data-toast-id="' +
                        this._escAttr(t.id) +
                        '">' +
                        this._esc(i18n.rejectAction || "Photo mismatch") +
                        "</button></div>";
                }
                var div = document.createElement("div");
                div.className =
                    "checkin-toast pointer-events-auto bg-white dark:bg-gray-800 rounded-xl shadow-lg dark:shadow-gray-900/40 border " +
                    (t.isApproved
                        ? "border-gray-200 dark:border-gray-700"
                        : "border-amber-300 dark:border-amber-600") +
                    " p-3";
                div.setAttribute("data-toast-id", t.id);
                div.innerHTML =
                    '<div class="flex items-center gap-3">' +
                    '<div class="w-12 h-12 rounded-full overflow-hidden shrink-0">' +
                    photoHtml +
                    "</div>" +
                    '<div class="min-w-0 flex-1">' +
                    '<div class="flex items-center gap-1.5">' +
                    '<span class="font-semibold text-sm dark:text-white truncate">' +
                    this._esc(t.name) +
                    "</span>" +
                    approvedHtml +
                    "</div>" +
                    '<div class="flex items-center flex-wrap gap-x-2 gap-y-0.5 text-xs text-gray-500 dark:text-gray-400">' +
                    "<span>" +
                    this._esc(t.genderIcon) +
                    "</span>" +
                    "<span>" +
                    this._esc(t.ageDisplay) +
                    "</span>" +
                    locationHtml +
                    tableHtml +
                    "</div>" +
                    interestsHtml +
                    "</div></div>" +
                    warningHtml +
                    rejectBtnHtml;

                var rejectBtn = div.querySelector(".toast-reject-btn");
                if (rejectBtn) {
                    var self = this;
                    rejectBtn.addEventListener("click", function (clickEvent) {
                        self.rejectVerification(clickEvent);
                    });
                }

                container.appendChild(div);
            },

            _renderCheckins: function () {
                var container = document.getElementById("recent-checkins-container");
                if (!container) return;
                var html = "";
                for (var i = 0; i < this.checkins.length; i++) {
                    var c = this.checkins[i];
                    var tableHtml = c.hasTable
                        ? ' <span class="inline-flex items-center rounded-full bg-crush-purple/10 px-2 py-0.5 text-xs font-medium text-crush-purple dark:text-purple-300">' +
                          this._esc(c.tableLabel) +
                          "</span>"
                        : "";
                    html +=
                        '<div class="flex items-center justify-between bg-gray-50 dark:bg-gray-900/50 rounded-lg px-3 py-2 text-sm">' +
                        '<div class="flex items-center gap-2">' +
                        '<span class="font-medium dark:text-white">' +
                        this._esc(c.name) +
                        "</span>" +
                        tableHtml +
                        "</div>" +
                        '<span class="text-gray-400 dark:text-gray-500 text-xs">' +
                        this._esc(c.time) +
                        "</span></div>";
                }
                container.innerHTML = html;
            },

            // --- WebSocket ---
            connectWebSocket: function () {
                var self = this;
                var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                var url =
                    protocol +
                    "//" +
                    window.location.host +
                    "/ws/checkin/" +
                    this.eventId +
                    "/";
                this.ws = new WebSocket(url);
                this.ws.onopen = function () {
                    self.connected = true;
                    // Only count this as healthy once it has SURVIVED a while.
                    // The channel layer can accept a socket and then drop it a
                    // few seconds later (idle blocking-read timeout); resetting
                    // the counter on open alone turned that into a permanent
                    // ~6s reconnect loop that never backed off and never
                    // reached the attempt cap — every open coach phone hammered
                    // the server all evening.
                    clearTimeout(self.stableTimer);
                    self.stableTimer = setTimeout(function () {
                        self.reconnectAttempts = 0;
                    }, 30000);
                };
                this.ws.onclose = function () {
                    self.connected = false;
                    clearTimeout(self.stableTimer);
                    if (self.reconnectAttempts >= 20) return;
                    // Jitter: several coaches open the wizard at once at the
                    // door, and lockstep retries would arrive as one burst.
                    var base = Math.min(
                        1000 * Math.pow(2, self.reconnectAttempts),
                        30000,
                    );
                    var delay = base * (0.8 + Math.random() * 0.4);
                    self.reconnectAttempts++;
                    setTimeout(function () {
                        self.connectWebSocket();
                    }, delay);
                };
                this.ws.onmessage = function (event) {
                    var msg = JSON.parse(event.data);
                    if (msg.type === "checkin.update") {
                        self.handleRemoteCheckin(msg.data);
                    }
                };
            },

            handleRemoteCheckin: function (data) {
                var regId = data.registration_id;
                // Row state and counters run on EVERY path, before the
                // dedupe guard: the guard exists to stop repeat check-in
                // toasts, not state changes. That ordering failure is what
                // made an undo broadcast from another coach read as an
                // arrival here, incrementing attendance for a correction
                // (#710 finding 1).
                this._applyRowState(data.row);
                this._refetchSummary();
                if (data.undone) {
                    // A correction is not an arrival: take the row out of
                    // Recent Check-ins instead of celebrating it.
                    this._removeRecentCheckin(regId);
                    // And clear the dedupe entry — mirroring the acting
                    // coach's local undo path — so a legitimate re-check-in
                    // after the undo isn't swallowed by the guard below.
                    delete this.processedIds[regId];
                    return;
                }
                if (data.rejected) {
                    this._markRowUnverified(regId);
                    return;
                }
                // A verification is a genuinely new event about an ID this page
                // has already seen: the attendee's first (unverified) scan
                // marked the ID processed, so a later rescan that verifies them
                // would be suppressed here and this page would keep showing the
                // amber pill and Verify button until reload. Apply it before
                // the duplicate check, which exists to stop repeat *check-in*
                // toasts, not state changes.
                if (data.auto_verified) {
                    this._markRowVerified(regId);
                }
                if (this.processedIds[regId]) return;
                this.processedIds[regId] = true;
                this.showProfileToast(data);
                // Secondary desk printer: auto-print on remote checkin if printer + autoPrint active
                if (
                    this.printerEnabled &&
                    this.autoPrint &&
                    data.print_payload_base64 &&
                    !data.undone &&
                    !data.rejected
                ) {
                    this.triggerRawBtPrint(data.print_payload_base64);
                }
                // Only a fresh arrival enters Recent Check-ins: a re-scan
                // (already_checked_in) and a verification (no such flag at
                // all) must not.
                if (data.already_checked_in === false) {
                    this._pushRecentCheckin(data);
                }
            },

            // --- Toast management ---
            showProfileToast: function (data) {
                var self = this;
                var id = ++this.toastCounter;
                var profile = data.profile || {};
                var gender = profile.gender || "";
                var genderIcon = "\u26A7";
                if (gender === "M") genderIcon = "\u2642";
                else if (gender === "F") genderIcon = "\u2640";

                var toastObj = {
                    id: id,
                    regId: data.registration_id || 0,
                    name: profile.display_name || data.attendee_name || "",
                    genderIcon: genderIcon,
                    ageDisplay: profile.age_display || "",
                    isApproved: profile.is_approved || false,
                    photoUrl: profile.photo_url || "",
                    hasPhoto: !!profile.photo_url,
                    location: profile.location || "",
                    interests: profile.interests || "",
                    submissionStatus: profile.submission_status || "",
                    coachName: profile.coach_name || "",
                    table: data.table_number || 0,
                    hasTable: !!data.table_number,
                    tableLabel: data.table_number ? "T" + data.table_number : "",
                    alreadyCheckedIn: data.already_checked_in || false,
                };
                this.toasts.push(toastObj);
                this._renderToastElement(toastObj);
                // Unverified profiles stay longer so coach can read the warning
                var duration = toastObj.isApproved ? 5000 : 10000;
                setTimeout(function () {
                    self.dismissToast(id);
                }, duration);
            },

            dismissToast: function (id) {
                this.toasts = this.toasts.filter(function (t) {
                    return t.id !== id;
                });
                var el = document.querySelector('[data-toast-id="' + id + '"]');
                if (el) el.remove();
            },

            // --- Shared row-state applier + summary refetch (#710) ---
            //
            // ONE applier for every path — local fetch callback and
            // WebSocket broadcast alike — fed by the `row` object each
            // action's own response carries. No handler patches its own
            // subset of the DOM any more; that is what drifted across ~20
            // hand-maintained update sites and left the 3-actions-x-2-paths
            // matrix with two broken cells.
            _applyRowState: function (row) {
                if (!row || !row.registration_id) return;
                var regId = row.registration_id;

                if (row.status === "waitlist") {
                    // Undoing a promotion puts the walk-up back where they
                    // came from: out of the confirmed list, into the
                    // waitlist section — which is why that section renders
                    // even while empty. The avatar photo is CARRIED OVER
                    // from the row being removed: no URL is ever read back
                    // from the payload, so nothing response-derived can
                    // reach an img.src.
                    var mainRow = document.getElementById("manual-reg-" + regId);
                    var mainPhoto = this._takeRowPhoto(mainRow);
                    if (mainRow) mainRow.remove();
                    if (
                        !document.getElementById("waitlist-reg-" + regId) &&
                        this._waitlistList()
                    ) {
                        // Appended, so an undone promotion returns the member
                        // to the end of the waitlist rather than the position
                        // they signed up in. Restoring the true FIFO slot
                        // needs registered_at both in the row payload and as
                        // an attribute on the rendered rows (this builder and
                        // the server template) — none of the three carry it.
                        this._waitlistList().appendChild(
                            this._buildWaitlistRow(row, mainPhoto),
                        );
                    }
                } else {
                    // A promotion leaves the waitlist on every path. The
                    // row used to survive it on every page but the acting
                    // coach's, leaving an enabled promote button over an
                    // attended row that answers 409 (#710 finding 2). The
                    // photo rides across the same way as above.
                    var wlRow = document.getElementById("waitlist-reg-" + regId);
                    var wlPhoto = this._takeRowPhoto(wlRow);
                    if (wlRow) wlRow.remove();
                    var rowEl = document.getElementById("manual-reg-" + regId);
                    if (!rowEl) {
                        // The confirmed list never contained this attendee:
                        // build the whole row, undo button included, so a
                        // mistaken promotion is correctable at once (#710
                        // finding 3).
                        var list = this._attendeeList();
                        if (!list) return;
                        rowEl = this._buildConfirmedRow(row, wlPhoto);
                        list.appendChild(rowEl);
                        var empty = document.getElementById("attendee-empty");
                        if (empty) empty.remove();
                    }
                    rowEl.setAttribute("data-attendee-status", row.status);
                    this._applyRowInterior(rowEl, row);
                }
                // The "not arrived" filter re-reads the status attribute,
                // so a row checked in under it leaves the list now rather
                // than at the next keystroke (#710 finding 4).
                if (window._applyAttendeeFilters) window._applyAttendeeFilters();
            },

            _attendeeList: function () {
                return document.getElementById("attendee-list");
            },

            _waitlistList: function () {
                return document.getElementById("waitlist-list");
            },

            // Rewrite everything an action can change on an existing
            // confirmed-list row. Static identity (name, gender, age, avatar
            // image) is only built by _buildConfirmedRow.
            _applyRowInterior: function (rowEl, row) {
                var attended = row.status === "attended";

                // Green overlay tick: add or remove, never toggle, so both
                // the server-rendered and the JS-injected badge come off on
                // undo (#710 finding 7).
                var avatarWrap = rowEl.querySelector("[data-checkin-avatar]");
                if (avatarWrap) {
                    var overlay = avatarWrap.querySelector(
                        ".checkin-overlay-badge",
                    );
                    if (attended && !overlay) {
                        avatarWrap.appendChild(this._buildOverlayBadge());
                    } else if (!attended && overlay) {
                        overlay.remove();
                    }
                }

                // Verification pill — all three variants carry the tag.
                var nameLine = rowEl.querySelector("[data-name-line]");
                var pill = nameLine && nameLine.querySelector("[data-verify-pill]");
                if (pill && pill.parentNode) {
                    pill.parentNode.replaceChild(this._buildVerifyPill(row), pill);
                }

                // Coach line and arrival time are dropped and re-added in
                // order, so every path lands on the same DOM whatever the
                // server-rendered starting point (#710 findings 7 and 9): a
                // walk-in granted a coach by this very scan gets their line
                // now, and an undo takes the tick AND the time back off.
                var oldCoach = rowEl.querySelector("[data-coach-line]");
                if (oldCoach) oldCoach.remove();
                var oldTime = rowEl.querySelector("[data-arrival-time]");
                if (oldTime) oldTime.remove();
                var meta = nameLine ? nameLine.parentNode : null;
                if (meta) {
                    if (row.coach_name || attended) {
                        meta.appendChild(this._buildCoachLine(row, attended));
                    }
                    if (attended && row.checked_in_at) {
                        meta.appendChild(
                            this._buildArrivalTime(row.checked_in_at),
                        );
                    }
                }

                var actions = rowEl.querySelector("[data-row-actions]");
                var newActions = this._buildActions(rowEl, row);
                if (actions) {
                    actions.replaceWith(newActions);
                } else {
                    rowEl.appendChild(newActions);
                }
            },

            _buildOverlayBadge: function () {
                var badge = document.createElement("span");
                badge.className =
                    "checkin-overlay-badge absolute -bottom-0.5 -right-0.5 w-4 h-4 bg-green-500 rounded-full flex items-center justify-center";
                badge.innerHTML =
                    '<svg class="w-2.5 h-2.5 text-white" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg>';
                return badge;
            },

            _buildVerifyPill: function (row) {
                var i18n = window._checkinI18n || {};
                var span = document.createElement("span");
                span.setAttribute("data-verify-pill", "");
                var icon;
                var label;
                if (!row.has_profile) {
                    span.className =
                        "inline-flex items-center gap-0.5 rounded-full bg-red-100 dark:bg-red-900/30 px-1.5 py-0.5 text-xs font-medium text-red-700 dark:text-red-400";
                    icon =
                        '<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M18 10A8 8 0 112 10a8 8 0 0116 0zm-8-4a1 1 0 00-1 1v3a1 1 0 002 0V7a1 1 0 00-1-1zm0 8a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd"/></svg>';
                    label = i18n.noProfile || "No profile";
                } else if (row.is_approved) {
                    span.className =
                        "inline-flex items-center gap-0.5 rounded-full bg-green-100 dark:bg-green-900/30 px-1.5 py-0.5 text-xs font-medium text-green-700 dark:text-green-400";
                    icon =
                        '<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>';
                    label = i18n.verified || "Verified";
                } else {
                    span.className =
                        "inline-flex items-center gap-0.5 rounded-full bg-amber-100 dark:bg-amber-900/30 px-1.5 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400";
                    icon =
                        '<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>';
                    label = i18n.unverifiedPill || "Unverified";
                }
                span.innerHTML = icon + this._esc(label);
                return span;
            },

            _buildCoachLine: function (row, attended) {
                var i18n = window._checkinI18n || {};
                var p = document.createElement("p");
                p.setAttribute("data-coach-line", "");
                if (row.coach_name) {
                    p.className =
                        "text-xs text-crush-purple/70 dark:text-purple-400/70 mt-0.5";
                    p.textContent =
                        (i18n.coach || "Coach") + ": " + row.coach_name;
                } else {
                    p.className = "text-xs text-amber-600 dark:text-amber-400 mt-0.5";
                    p.textContent = i18n.noCoachYet || "No coach yet";
                }
                return p;
            },

            _buildArrivalTime: function (iso) {
                var p = document.createElement("p");
                p.className = "text-xs text-gray-400 dark:text-gray-500 mt-0.5";
                p.setAttribute("data-arrival-time", "");
                p.textContent = this._formatArrivalTime(iso);
                return p;
            },

            _formatArrivalTime: function (iso) {
                var d = new Date(iso);
                if (isNaN(d.getTime())) return "";
                return d.toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                });
            },

            // The action buttons for a row's current state. Built fresh and
            // swapped in whole, so no transition path can leave a stale
            // button behind.
            _buildActions: function (rowEl, row) {
                var self = this;
                var i18n = window._checkinI18n || {};
                var regId = row.registration_id;
                var wrap = document.createElement("div");
                wrap.className = "flex-shrink-0 flex items-center gap-1 sm:gap-2";
                wrap.setAttribute("data-row-actions", "");
                // Verify sits outside the status split, exactly like the
                // server-rendered twin: attended does not imply verified.
                // _auto_verify_on_attendance deliberately declines coach-less
                // self-scans, premium members and photo-less profiles, so the
                // button has to survive the check-in for exactly those rows.
                if (row.has_profile && !row.is_approved) {
                    wrap.appendChild(this._buildVerifyButton(regId));
                }
                if (row.status === "attended") {
                    var checkedSpan = document.createElement("span");
                    checkedSpan.className =
                        "px-2 sm:px-3 py-1.5 text-xs font-medium text-green-600 dark:text-green-400";
                    checkedSpan.textContent = i18n.checkedIn || "Checked In";
                    wrap.appendChild(checkedSpan);
                    if (row.table_number) {
                        var tableBadge = document.createElement("span");
                        // `manual-table-badge` is what a later state change
                        // clears along with the seat — without it an undone
                        // check-in left its "T3" on screen.
                        tableBadge.className =
                            "manual-table-badge inline-flex items-center rounded-full bg-crush-purple/10 px-1.5 sm:px-2 py-0.5 text-xs font-medium text-crush-purple dark:text-purple-300";
                        tableBadge.setAttribute("data-user-id", row.user_id);
                        tableBadge.textContent = "T" + row.table_number;
                        wrap.appendChild(tableBadge);
                    }
                    // The check-in most worth undoing is the one just made,
                    // so the button appears with the row, not on the next
                    // page load. Gated on checked_in_at like the
                    // server-rendered twin: an attendance recorded without a
                    // timestamp (admin-entered, or older than this flow) has
                    // no undo window and the endpoint refuses it — rendering
                    // the button would offer a correction that can only
                    // answer 409.
                    if (row.checked_in_at) {
                        var reprintBtn = this._buildReprintButton(regId);
                        if (reprintBtn) wrap.appendChild(reprintBtn);
                        var undoUrl =
                            rowEl.getAttribute("data-undo-url") ||
                            this._apiUrl("undo-checkin", regId);
                        var undoBtn = this._buildUndoButton(undoUrl, regId);
                        if (undoBtn) wrap.appendChild(undoBtn);
                    }
                } else {
                    var checkinUrl = rowEl.getAttribute("data-checkin-url");
                    if (checkinUrl) {
                        var checkinBtn = document.createElement("button");
                        checkinBtn.type = "button";
                        checkinBtn.className =
                            "manual-checkin-btn btn-crush-solid btn-sm text-white px-2.5 py-1 sm:px-3 sm:py-1.5 text-xs";
                        checkinBtn.setAttribute("data-checkin-url", checkinUrl);
                        checkinBtn.setAttribute("data-reg-id", regId);
                        checkinBtn.textContent = i18n.checkIn || "Check In";
                        checkinBtn.addEventListener("click", function (clickEvent) {
                            self.manualCheckin(clickEvent);
                        });
                        wrap.appendChild(checkinBtn);
                    }
                }
                return wrap;
            },

            // Full row for an attendee the confirmed list never contained —
            // a waitlist walk-up being promoted. Class-identical to the
            // server-rendered twin in coach_event_checkin.html. The photo,
            // when there is one, is the <img> node carried over from the
            // waitlist row this promotion empties.
            _buildConfirmedRow: function (row, photoImg) {
                var el = document.createElement("div");
                el.className = "py-3 flex items-center justify-between gap-3";
                el.id = "manual-reg-" + row.registration_id;
                el.setAttribute("data-attendee-status", row.status);
                // Undo and verify are addressed by id — no signed token
                // involved — so a row that was waitlist-only until now can
                // still carry them.
                el.setAttribute(
                    "data-undo-url",
                    this._apiUrl("undo-checkin", row.registration_id),
                );
                // Check-in IS signed per registration, so unlike the two above
                // it cannot be derived here and has to ride the payload. A row
                // built from scratch — what a recovered `no_show` gets, having
                // none on the page to begin with — would otherwise offer Undo
                // and then have no way back in, stranding that attendee until
                // the coach reloads.
                if (row.checkin_url) {
                    el.setAttribute("data-checkin-url", row.checkin_url);
                }
                // The haystack goes in RAW: setAttribute stores exactly what
                // it is handed (attribute values are never entity-parsed),
                // and the filter reads it back with getAttribute to compare
                // against the coach's literal input. Escaping here made
                // "Smith & Co" searchable only as "smith &amp; co".
                el.setAttribute(
                    "data-attendee-search",
                    row.search || row.display_name || "",
                );

                var left = document.createElement("div");
                left.className = "flex items-center gap-3 min-w-0";

                var avatarWrap = document.createElement("div");
                avatarWrap.className = "relative flex-shrink-0";
                avatarWrap.setAttribute("data-checkin-avatar", "");
                if (photoImg) {
                    photoImg.className = "w-10 h-10 rounded-full object-cover";
                    avatarWrap.appendChild(photoImg);
                } else {
                    var initial = document.createElement("div");
                    initial.className =
                        "w-10 h-10 rounded-full bg-crush-purple/20 flex items-center justify-center text-crush-purple font-semibold text-sm";
                    initial.textContent = (row.display_name || "?")
                        .charAt(0)
                        .toUpperCase();
                    avatarWrap.appendChild(initial);
                }
                left.appendChild(avatarWrap);

                var meta = document.createElement("div");
                meta.className = "min-w-0";
                var nameLine = document.createElement("div");
                nameLine.className =
                    "flex flex-wrap items-center gap-x-2 gap-y-0.5";
                nameLine.setAttribute("data-name-line", "");
                var name = document.createElement("span");
                name.className = "font-medium text-sm dark:text-white truncate";
                name.textContent = row.display_name || "";
                nameLine.appendChild(name);
                var genderIcon = this._genderIcon(row.gender);
                if (genderIcon) {
                    var g = document.createElement("span");
                    g.className = genderIcon.cls + " text-xs";
                    if (genderIcon.title) g.title = genderIcon.title;
                    g.textContent = genderIcon.char;
                    nameLine.appendChild(g);
                }
                if (row.age_display) {
                    var age = document.createElement("span");
                    age.className = "text-xs text-gray-500 dark:text-gray-400";
                    age.textContent = row.age_display;
                    nameLine.appendChild(age);
                }
                nameLine.appendChild(this._buildVerifyPill(row));
                meta.appendChild(nameLine);
                left.appendChild(meta);
                el.appendChild(left);
                return el;
            },

            // Waitlist twin — what an undone promotion returns to. Same
            // classes as the server-rendered rows it sits between.
            _buildWaitlistRow: function (row, photoImg) {
                var self = this;
                var i18n = window._checkinI18n || {};
                var el = document.createElement("div");
                el.className = "py-3 flex items-center justify-between gap-3";
                el.id = "waitlist-reg-" + row.registration_id;

                var left = document.createElement("div");
                left.className = "flex items-center gap-3 min-w-0";
                if (photoImg) {
                    photoImg.className =
                        "w-10 h-10 rounded-full object-cover flex-shrink-0";
                    left.appendChild(photoImg);
                } else {
                    var initial = document.createElement("div");
                    initial.className =
                        "w-10 h-10 rounded-full bg-amber-500/20 flex items-center justify-center text-amber-700 dark:text-amber-400 font-semibold text-sm flex-shrink-0";
                    initial.textContent = (row.display_name || "?")
                        .charAt(0)
                        .toUpperCase();
                    left.appendChild(initial);
                }
                var meta = document.createElement("div");
                meta.className = "min-w-0";
                var name = document.createElement("span");
                name.className = "font-medium text-sm dark:text-white truncate block";
                name.textContent = row.display_name || "";
                meta.appendChild(name);
                var sub = [];
                var genderIcon = this._genderIcon(row.gender);
                if (genderIcon) sub.push(genderIcon.char);
                if (row.age_display) sub.push(row.age_display);
                if (sub.length) {
                    var subSpan = document.createElement("span");
                    subSpan.className = "text-xs text-gray-500 dark:text-gray-400";
                    subSpan.textContent = " " + sub.join(" ");
                    meta.appendChild(subSpan);
                }
                left.appendChild(meta);
                el.appendChild(left);

                var promoteBtn = document.createElement("button");
                promoteBtn.type = "button";
                promoteBtn.className =
                    "waitlist-promote-btn flex-shrink-0 btn-crush-solid btn-sm bg-amber-600 hover:bg-amber-700 focus:ring-amber-500 text-white";
                promoteBtn.setAttribute(
                    "data-promote-url",
                    this._apiUrl("promote", row.registration_id),
                );
                promoteBtn.setAttribute("data-reg-id", row.registration_id);
                promoteBtn.textContent = i18n.promoteAction || "Check in";
                promoteBtn.addEventListener("click", function (clickEvent) {
                    self.promoteFromWaitlist(clickEvent);
                });
                el.appendChild(promoteBtn);
                return el;
            },

            _genderIcon: function (gender) {
                if (gender === "M")
                    return { char: "\u2642", cls: "text-blue-500", title: "Male" };
                if (gender === "F")
                    return { char: "\u2640", cls: "text-pink-500", title: "Female" };
                if (gender === "NB")
                    return {
                        char: "\u26A4",
                        cls: "text-purple-500",
                        title: "Non-binary",
                    };
                if (gender) return { char: "\u26A7", cls: "text-gray-400", title: "" };
                return null;
            },

            // Detach the avatar <img> from a row that is about to be
            // removed, so the photo survives the row transition without its
            // URL ever being read back from an API payload — the src stays
            // whatever the server rendered, which severs the response-body
            // taint CodeQL traces into img.src.
            _takeRowPhoto: function (rowEl) {
                if (!rowEl) return null;
                // The row's one <img>, wherever it lives: confirmed rows
                // wrap it in [data-checkin-avatar] (the overlay badge needs
                // the positioning context), waitlist rows have no wrapper.
                // It is the WAITLIST row this detaches on a promotion, so
                // the narrow selector lost the photo in exactly that
                // direction — the undo direction kept working.
                var img = rowEl.querySelector("img");
                if (img && img.parentNode) img.parentNode.removeChild(img);
                return img || null;
            },

            _apiUrl: function (action, regId) {
                return (
                    "/api/events/" + this.eventId + "/" + action + "/" + regId + "/"
                );
            },

            // --- Summary refetch (#710) ---
            //
            // The counters are never bumped by hand any more: every
            // successful door action refetches this read-only summary, so a
            // path that forgets a tile can only be late, never wrong — and
            // both coaches' pages converge on the server's numbers.
            _refetchSummary: function () {
                var self = this;
                var url = this.$el.getAttribute("data-summary-url");
                if (!url) return;
                // Sequence guard: two door actions close together start two
                // fetches, and the older read can land LAST — without this,
                // it would apply a snapshot that predates the newer action
                // and roll the counters back. Only the most recently started
                // fetch may render.
                var seq = ++this._summarySeq;
                // A failed refetch keeps the current numbers: the door must
                // not blank its counters because a phone lost signal.
                fetch(url, { headers: { Accept: "application/json" } })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (summary) {
                        if (seq !== self._summarySeq) return;
                        if (summary && summary.success) {
                            self._applySummary(summary);
                        }
                    })
                    .catch(function () {});
            },

            _applySummary: function (s) {
                this._setCount("attended-count", s.attended_count);
                this._setCount("outstanding-count", s.outstanding_count);
                this._setCount("expected-count", s.expected_count);
                // Numerator AND denominator, all three buckets — the
                // endpoint always sends them, so a promotion can no longer
                // render a tile as "6 / 5" (#710 finding 5).
                var buckets = [
                    ["f", "F"],
                    ["m", "M"],
                    ["other", "other"],
                ];
                for (var i = 0; i < buckets.length; i++) {
                    var elKey = buckets[i][0];
                    var dataKey = buckets[i][1];
                    this._setCount(
                        "gender-" + elKey + "-count",
                        (s.gender_checked_in || {})[dataKey],
                    );
                    this._setCount(
                        "gender-" + elKey + "-expected",
                        (s.gender_expected || {})[dataKey],
                    );
                }
                // Waitlist badge and section visibility follow the count
                // (#710 finding 6) — including the empty-rendered section an
                // undone promotion has to be able to come back to.
                this._setCount("waitlist-count-badge", s.waitlist_count);
                var card = document.getElementById("waitlist-card");
                if (card) {
                    card.style.display = s.waitlist_count > 0 ? "" : "none";
                }
                var fill = s.table_fill || [];
                for (var t = 0; t < fill.length; t++) {
                    this._setCount(
                        "table-fill-" + fill[t].number,
                        fill[t].count,
                    );
                }
            },

            _setCount: function (id, value) {
                var el = document.getElementById(id);
                if (el && typeof value === "number") el.textContent = value;
            },

            // --- Recent check-ins ---
            _pushRecentCheckin: function (data) {
                this.checkins.unshift({
                    // regId is what lets an undo take its entry back out
                    // (#710 finding 8).
                    regId: data.registration_id,
                    name: data.attendee_name,
                    table: data.table_number || 0,
                    hasTable: !!data.table_number,
                    tableLabel: data.table_number ? "T" + data.table_number : "",
                    time: new Date().toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                    }),
                });
                this._renderCheckins();
            },

            _removeRecentCheckin: function (regId) {
                if (!regId) return;
                for (var i = 0; i < this.checkins.length; i++) {
                    if (this.checkins[i].regId === regId) {
                        this.checkins.splice(i, 1);
                        break;
                    }
                }
                this._renderCheckins();
            },

            _buildUndoButton: function (undoUrl, regId) {
                var self = this;
                var i18n = window._checkinI18n || {};
                if (!undoUrl) return null;
                var undoBtn = document.createElement("button");
                undoBtn.type = "button";
                // Must stay identical to the server-rendered twin in
                // coach_event_checkin.html — a row that came back through undo
                // sits next to rows that never left, and a drifted class list
                // shows up as two differently-shaped buttons in the same list.
                undoBtn.className =
                    "manual-undo-btn btn-link p-1.5 sm:px-2 sm:py-1.5 text-xs font-medium decoration-dotted transition-colors text-gray-500 dark:text-gray-400 hover:text-red-600 dark:hover:text-red-400";
                undoBtn.setAttribute("data-undo-url", undoUrl);
                undoBtn.setAttribute("data-reg-id", regId);
                undoBtn.setAttribute(
                    "title",
                    i18n.undoActionTitle || i18n.undoAction || "Undo check-in",
                );
                undoBtn.textContent = i18n.undoAction || "Undo";
                // Bound directly rather than with x-on — Alpine only wires
                // directives present when it walked the tree.
                undoBtn.addEventListener("click", function (clickEvent) {
                    self.undoCheckin(clickEvent);
                });
                return undoBtn;
            },

            _buildReprintButton: function (regId) {
                var self = this;
                var i18n = window._checkinI18n || {};
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className =
                    "manual-reprint-btn btn-link p-1.5 sm:px-2 sm:py-1.5 text-xs font-medium decoration-dotted transition-colors text-purple-600 dark:text-purple-400 hover:text-purple-800 dark:hover:text-purple-300 inline-flex items-center gap-1";
                btn.setAttribute(
                    "data-print-url",
                    this._apiUrl("print-ticket", regId),
                );
                btn.setAttribute("data-reg-id", regId);
                var label = i18n.printAction || "Print";
                btn.setAttribute("title", i18n.reprintActionTitle || label);
                btn.innerHTML =
                    '<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"/></svg>' +
                    '<span class="hidden sm:inline">' +
                    self._esc(label) +
                    "</span>";
                btn.addEventListener("click", function (clickEvent) {
                    self.reprintTicket(clickEvent);
                });
                return btn;
            },

            _buildVerifyButton: function (regId) {
                var self = this;
                var i18n = window._checkinI18n || {};
                var verifyBtn = document.createElement("button");
                verifyBtn.type = "button";
                // Must stay identical to the server-rendered twin in
                // coach_event_checkin.html — see _buildUndoButton.
                verifyBtn.className =
                    "manual-verify-btn btn-crush-solid btn-sm bg-green-600 hover:bg-green-700 focus:ring-green-500 text-white px-2.5 py-1 sm:px-3 sm:py-1.5 text-xs";
                verifyBtn.setAttribute(
                    "data-verify-url",
                    this._apiUrl("verify", regId),
                );
                verifyBtn.setAttribute("data-reg-id", regId);
                verifyBtn.textContent = i18n.verifyAction || "Verify";
                // Bound directly rather than with x-on — Alpine only wires
                // directives present when it walked the tree.
                verifyBtn.addEventListener("click", function (clickEvent) {
                    self.markVerified(clickEvent);
                });
                return verifyBtn;
            },

            // --- Thermal Printer (RawBT Protocol) ---
            togglePrinter: function () {
                this.printerEnabled = !this.printerEnabled;
                localStorage.setItem(
                    "crush_printer_enabled",
                    this.printerEnabled ? "true" : "false",
                );
            },

            triggerRawBtPrint: function (base64Payload) {
                if (!base64Payload || typeof base64Payload !== "string") return;
                var trimmed = base64Payload.trim();
                if (!/^[A-Za-z0-9+/=]+$/.test(trimmed)) return;

                // Single source of truth for the Android intent fallback —
                // previously rebuilt independently at every call site, which
                // is exactly the class of string PR #872 already had to
                // hand-fix once (wrong URI scheme format).
                var fireIntentFallback = function () {
                    // Re-validated here, right at the navigation, not just
                    // once at function entry: this fires from async
                    // WebSocket callbacks (onerror, a timeout, a caught
                    // exception), so the check must hold at the sink, not
                    // rely on control flow from an earlier point in the
                    // call. The base64 charset excludes ":" and "#", so
                    // `trimmed` can never alter the fixed "intent:" scheme
                    // or inject a second "#Intent;...;end;" extras block —
                    // window.location.href always begins with the literal
                    // "intent:base64," prefix below, never attacker-chosen.
                    if (!/^[A-Za-z0-9+/=]+$/.test(trimmed)) return;
                    window.location.href =
                        "intent:base64," +
                        trimmed +
                        "#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;";
                };

                // 1. Primary: Direct binary stream via local RawBT WebSocket (ws://127.0.0.1:40213)
                try {
                    var binaryString = atob(trimmed);
                    var len = binaryString.length;
                    var bytes = new Uint8Array(len);
                    for (var i = 0; i < len; i++) {
                        bytes[i] = binaryString.charCodeAt(i);
                    }
                    var ws = new WebSocket("ws://127.0.0.1:40213/");
                    ws.binaryType = "arraybuffer";
                    var wsHandled = false;

                    var fallbackTimer = setTimeout(function () {
                        if (!wsHandled && ws.readyState !== 1) {
                            wsHandled = true;
                            try { ws.close(); } catch (e) {}
                            fireIntentFallback();
                        }
                    }, 400);

                    ws.onopen = function () {
                        wsHandled = true;
                        clearTimeout(fallbackTimer);
                        // send() can still throw (e.g. the socket closes in
                        // the instant between onopen firing and send() being
                        // called) — this runs asynchronously, outside the
                        // protection of the outer try/catch below, so it
                        // needs its own guard or the print job is silently
                        // lost with wsHandled already true and no fallback.
                        try {
                            ws.send(bytes.buffer);
                        } catch (e) {
                            fireIntentFallback();
                            return;
                        }
                        setTimeout(function () {
                            try { ws.close(); } catch (e) {}
                        }, 500);
                    };

                    ws.onerror = function () {
                        if (!wsHandled) {
                            wsHandled = true;
                            clearTimeout(fallbackTimer);
                            fireIntentFallback();
                        }
                    };
                } catch (e) {
                    fireIntentFallback();
                }
            },

            testPrint: function () {
                var self = this;
                fetch("/api/events/" + this.eventId + "/test-ticket/")
                    .then(function (r) {
                        if (!r.ok) throw new Error("HTTP " + r.status);
                        return r.json();
                    })
                    .then(function (data) {
                        if (data && data.success && data.print_payload_base64) {
                            self.triggerRawBtPrint(data.print_payload_base64);
                        }
                    })
                    .catch(function (err) {
                        console.warn("Test ticket request failed:", err);
                    });
            },

            reprintTicket: function (evt) {
                var self = this;
                var btn = evt.currentTarget;
                var regId = btn.getAttribute("data-reg-id");
                var url =
                    btn.getAttribute("data-print-url") ||
                    self._apiUrl("print-ticket", regId);
                if (self._missingActionUrl(url)) return;
                fetch(url)
                    .then(function (r) {
                        if (!r.ok) throw new Error("HTTP " + r.status);
                        return r.json();
                    })
                    .then(function (data) {
                        if (data && data.success && data.print_payload_base64) {
                            self.triggerRawBtPrint(data.print_payload_base64);
                        }
                    })
                    .catch(function (err) {
                        console.warn("Reprint ticket request failed:", err);
                    });
            },

            // --- Scanner ---
            toggleScanner: function () {
                if (this.scannerActive) {
                    this.stopScanner();
                } else {
                    this.startScanner();
                }
            },

            startScanner: function () {
                var self = this;
                var readerEl = document.getElementById("qr-reader");
                var videoEl = document.getElementById("qr-video");
                if (!readerEl || !videoEl) return;
                readerEl.style.display = "block";

                if (typeof QrScanner === "undefined") {
                    self.result = true;
                    self.errorState = true;
                    self.message = gettext("QR scanner library not loaded.");
                    return;
                }

                if (!self.scanner) {
                    self.scanner = new QrScanner(
                        videoEl,
                        function (result) {
                            self.handleScan(result.data);
                        },
                        {
                            returnDetailedScanResult: true,
                            preferredCamera: "environment",
                            maxScansPerSecond: 10,
                            highlightScanRegion: true,
                            highlightCodeOutline: true,
                            // Scan the FULL camera frame at a higher working
                            // resolution than the library default (which crops
                            // to a centred square and downscales to 400px) —
                            // dim, small or off-centre codes at the door were
                            // the reason html5-qrcode was replaced.
                            calculateScanRegion: function (video) {
                                var w = video.videoWidth;
                                var h = video.videoHeight;
                                var scale = Math.min(
                                    1,
                                    720 / Math.max(1, Math.min(w, h)),
                                );
                                return {
                                    x: 0,
                                    y: 0,
                                    width: w,
                                    height: h,
                                    downScaledWidth: Math.round(w * scale),
                                    downScaledHeight: Math.round(h * scale),
                                };
                            },
                        },
                    );
                }

                // Tapping Start twice while the first getUserMedia() prompt is
                // still pending makes qr-scanner's second start() resolve at
                // once (it already considers itself active), so without this
                // guard a later denial would leave the button stuck on "Stop
                // Scanner" for a scanner that never started.
                if (self.startPending) return;
                self.startPending = true;

                self.scanner
                    .start()
                    .then(function () {
                        self.startPending = false;
                        self.scannerActive = true;
                        self.scanBusy = false;
                        // Torch is only exposed on supporting devices
                        // (Android Chrome); iOS never reports flash support.
                        self.scanner
                            .hasFlash()
                            .then(function (has) {
                                self.torchAvailable = !!has;
                            })
                            .catch(function () {
                                self.torchAvailable = false;
                            });
                    })
                    .catch(function (err) {
                        self.startPending = false;
                        // A failed start leaves nothing running — clear the
                        // active flag so the button offers Start again rather
                        // than a Stop for a dead scanner.
                        self.scannerActive = false;
                        self.torchAvailable = false;
                        self.torchOn = false;
                        self.result = true;
                        self.errorState = true;
                        var errStr = String(err);
                        if (
                            errStr.indexOf("NotAllowedError") !== -1 ||
                            errStr.indexOf("Permission") !== -1
                        ) {
                            self.message = self._cameraDeniedMessage();
                        } else if (errStr.indexOf("Camera not found") !== -1) {
                            // qr-scanner collapses EVERY getUserMedia failure
                            // (denied, busy, missing) into this one string —
                            // probe the environment to give actionable advice.
                            self.message = gettext("No camera found on this device.");
                            self._diagnoseCameraFailure();
                        } else if (
                            errStr.indexOf("NotFoundError") !== -1 ||
                            errStr.indexOf("DevicesNotFound") !== -1
                        ) {
                            self.message = gettext("No camera found on this device.");
                        } else if (
                            errStr.indexOf("NotReadableError") !== -1 ||
                            errStr.indexOf("TrackStartError") !== -1
                        ) {
                            self.message =
                                gettext("Camera is in use by another application. Please close other apps using the camera and try again.");
                        } else {
                            self.message = gettext("Could not start camera: ") + errStr;
                        }
                        readerEl.style.display = "none";
                    });
            },

            _cameraDeniedMessage: function () {
                return gettext("Camera permission denied. Please allow camera access in your browser settings (click the lock icon in the address bar) and try again.");
            },

            _diagnoseCameraFailure: function () {
                var self = this;
                var fallbackByDevices = function () {
                    // With permission denied, cameras still enumerate (with
                    // blank labels) — so "no devices" is the only state that
                    // truly means no camera.
                    if (
                        typeof QrScanner !== "undefined" &&
                        QrScanner.hasCamera
                    ) {
                        QrScanner.hasCamera().then(function (has) {
                            self.message = has
                                ? self._cameraDeniedMessage()
                                : gettext("No camera found on this device.");
                        });
                    } else {
                        self.message = gettext("No camera found on this device.");
                    }
                };
                if (navigator.permissions && navigator.permissions.query) {
                    navigator.permissions
                        .query({ name: "camera" })
                        .then(function (status) {
                            if (status.state === "denied") {
                                self.message = self._cameraDeniedMessage();
                            } else {
                                fallbackByDevices();
                            }
                        })
                        .catch(fallbackByDevices);
                } else {
                    fallbackByDevices();
                }
            },

            stopScanner: function () {
                var self = this;
                if (!self.scanner) return;
                var scanner = self.scanner;
                // qr-scanner's start() re-enables a torch that was left on
                // (stop() keeps its internal flash flag) — turn it off
                // explicitly so the next Start doesn't surprise-fire the
                // flash while the UI shows it off.
                scanner
                    .turnFlashOff()
                    .catch(function () {})
                    .then(function () {
                        scanner.stop();
                    });
                self.scannerActive = false;
                self.torchAvailable = false;
                self.torchOn = false;
                self.scanBusy = false;
                self.startPending = false;
                var readerEl = document.getElementById("qr-reader");
                if (readerEl) readerEl.style.display = "none";
            },

            toggleTorch: function () {
                var self = this;
                if (!self.scanner) return;
                self.scanner
                    .toggleFlash()
                    .then(function () {
                        self.torchOn = self.scanner.isFlashOn();
                    })
                    .catch(function () {});
            },

            // A scanned QR is untrusted text: whatever the camera decodes
            // lands here — a stranger's QR, a menu, a half-read frame. Only
            // a Crush.lu check-in ticket may reach the network, and only as
            // a path on THIS origin. Before this guard an empty or "?…"/"#…"
            // decode resolved to the door page's own URL and was POSTed
            // there (prod: 403 "CSRF token missing" on
            // /de/coach/events/<id>/checkin/ from iOS Safari), and a foreign
            // QR was POSTed blind to whatever host it named.
            //
            // Returns the path to POST, or null to reject. Hosts compare
            // with one leading "www." dropped: www.crush.lu is served (not
            // redirected) and tickets embed the host they were minted on
            // (views_ticket.py, wallet/*), so a crush.lu ticket must still
            // work at a www.crush.lu door. The signed token is
            // host-independent and the caller fetches the bare path, so no
            // request ever leaves this origin. test.crush.lu stays foreign.
            _checkinPathFromScan: function (text) {
                if (typeof text !== "string" || !text.trim()) return null;
                var parsed;
                try {
                    parsed = new URL(text.trim(), window.location.origin);
                } catch (e) {
                    return null;
                }
                var bareHost = function (host) {
                    return String(host).toLowerCase().replace(/^www\./, "");
                };
                if (
                    parsed.protocol !== window.location.protocol ||
                    parsed.port !== window.location.port ||
                    bareHost(parsed.hostname) !== bareHost(window.location.hostname)
                ) {
                    return null;
                }
                // Mirrors azureproject/urls_crush.py
                // "api/events/checkin/<int:registration_id>/<str:token>/";
                // <str:> is [^/]+ (the Signer token contains colons).
                if (!CHECKIN_API_PATH_RE.test(parsed.pathname)) return null;
                return parsed.pathname;
            },

            // Re-arm the camera 2 s after any scan outcome. Every exit of
            // handleScan must call this, or scanBusy stays true and the door
            // is dead after one bad QR.
            _resumeScanSoon: function () {
                var self = this;
                setTimeout(function () {
                    self.scanBusy = false;
                    if (self.scanner && self.scannerActive) {
                        self.scanner.start().catch(function () {});
                    }
                }, 2000);
            },

            // Door buttons read their endpoint from a data-* attribute. An
            // empty one must never reach fetch(): fetch("") (or a relative
            // "?…") resolves to the page's own URL. Returns true — after
            // telling the coach — when the action has to stop here.
            _missingActionUrl: function (url) {
                if (url && String(url).trim()) return false;
                alert(
                    gettext("This button has no action link. Please reload the page."),
                );
                return true;
            },

            handleScan: function (text) {
                var self = this;
                // qr-scanner keeps decoding ~10x/s while the code is visible;
                // process the first hit and ignore the rest until resume.
                if (self.scanBusy) return;
                self.scanBusy = true;
                if (self.scanner) {
                    self.scanner.pause();
                }

                var url = self._checkinPathFromScan(text);
                if (!url) {
                    self.result = true;
                    self.success = false;
                    self.errorState = true;
                    self.message = gettext("This QR code is not a Crush.lu ticket.");
                    self._resumeScanSoon();
                    return;
                }

                // Pre-mark registration as processed to prevent WebSocket duplicate
                // Path format: /api/events/checkin/<reg_id>/<token>/
                var urlMatch = url.match(CHECKIN_API_PATH_RE);
                if (urlMatch) {
                    self.processedIds[urlMatch[1]] = true;
                }

                fetch(url, { method: "POST" })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        self.result = true;
                        if (data.success) {
                            self.success = true;
                            self.errorState = false;
                            self.lastTableNumber = data.table_number || 0;
                            self.lastRole = data.role || "";
                            self.message = data.table_number
                                ? data.message + " \u2192 Table " + data.table_number
                                : data.message;
                            if (data.registration_id) {
                                self.processedIds[data.registration_id] = true;
                            }
                            self.showProfileToast(data);
                            self._applyRowState(data.row);
                            self._refetchSummary();
                            if (self.printerEnabled && data.print_payload_base64) {
                                self.triggerRawBtPrint(data.print_payload_base64);
                            }
                            // Only a fresh arrival enters Recent Check-ins —
                            // a re-scan of an already-attended badge must not.
                            if (data.already_checked_in === false) {
                                self._pushRecentCheckin(data);
                            }
                        } else {
                            self.success = false;
                            self.errorState = true;
                            self.message = data.error || gettext("Check-in failed.");
                        }
                        self._resumeScanSoon();
                    })
                    .catch(function () {
                        self.result = true;
                        self.success = false;
                        self.errorState = true;
                        self.message = gettext("Network error or invalid QR code.");
                        self._resumeScanSoon();
                    });
            },

            // --- Manual check-in ---
            manualCheckin: function (evt) {
                var self = this;
                var btn = evt.currentTarget;
                var url = btn.getAttribute("data-checkin-url");
                var regId = btn.getAttribute("data-reg-id");
                if (self._missingActionUrl(url)) return;
                btn.disabled = true;
                btn.textContent = "...";

                // Pre-mark as processed to prevent WebSocket duplicate
                if (regId) {
                    self.processedIds[regId] = true;
                }

                fetch(url, { method: "POST" })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            if (data.registration_id) {
                                self.processedIds[data.registration_id] = true;
                            }
                            self.showProfileToast(data);
                            self._applyRowState(data.row);
                            self._refetchSummary();
                            if (self.printerEnabled && data.print_payload_base64) {
                                self.triggerRawBtPrint(data.print_payload_base64);
                            }
                            if (data.already_checked_in === false) {
                                self._pushRecentCheckin(data);
                            }
                        } else {
                            btn.disabled = false;
                            var i18n = window._checkinI18n || {};
                            btn.textContent = i18n.checkIn || "Check In";
                            alert(
                                data.error || i18n.checkinFailed || "Check-in failed",
                            );
                        }
                    })
                    .catch(function () {
                        btn.disabled = false;
                        var i18n = window._checkinI18n || {};
                        btn.textContent = i18n.checkIn || "Check In";
                        alert(i18n.networkError || "Network error");
                    });
            },

            getCsrfToken: function () {
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                var cookie = document.cookie.split("; ").find(function (row) {
                    return row.startsWith("csrftoken=");
                });
                return cookie ? cookie.split("=")[1] : "";
            },

            undoCheckin: function (evt) {
                var self = this;
                var btn = evt.currentTarget;
                var url = btn.getAttribute("data-undo-url");
                var regId = btn.getAttribute("data-reg-id");
                var i18n = window._checkinI18n || {};
                if (self._missingActionUrl(url)) return;
                if (!window.confirm(i18n.undoConfirm || "Undo this check-in?")) {
                    return;
                }
                btn.disabled = true;
                btn.textContent = "...";

                fetch(url, {
                    method: "POST",
                    headers: { "X-CSRFToken": self.getCsrfToken() },
                })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            // Allow a fresh scan of this badge to be processed
                            // again — otherwise the dedupe set silently
                            // swallows the corrected check-in.
                            delete self.processedIds[regId];
                            // One applier, fed by the row the undo response
                            // carries: it moves the row to whatever
                            // `restored_status` says (confirmed list or back
                            // onto the waitlist), clears the tick, the time
                            // and the table badge, and rebuilds the Check In
                            // path.
                            self._applyRowState(data.row);
                            self._removeRecentCheckin(
                                data.registration_id || regId,
                            );
                            self._refetchSummary();
                        } else {
                            btn.disabled = false;
                            btn.textContent = i18n.undoAction || "Undo";
                            alert(data.error || i18n.undoFailed || "Undo failed");
                        }
                    })
                    .catch(function () {
                        btn.disabled = false;
                        btn.textContent = i18n.undoAction || "Undo";
                        alert(i18n.networkError || "Network error");
                    });
            },

            promoteFromWaitlist: function (evt) {
                var self = this;
                var btn = evt.currentTarget;
                var url = btn.getAttribute("data-promote-url");
                var regId = btn.getAttribute("data-reg-id");
                var i18n = window._checkinI18n || {};
                if (self._missingActionUrl(url)) return;
                btn.disabled = true;
                btn.textContent = "...";

                // Pre-mark as processed to prevent a WebSocket duplicate, the
                // same way manualCheckin does.
                if (regId) {
                    self.processedIds[regId] = true;
                }

                fetch(url, {
                    method: "POST",
                    headers: { "X-CSRFToken": self.getCsrfToken() },
                })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            if (data.registration_id) {
                                self.processedIds[data.registration_id] = true;
                            }
                            self.showProfileToast(data);
                            // The applier takes the waitlist row away AND
                            // builds the attended row in the confirmed list —
                            // undo button included, so a mistaken promotion
                            // is correctable at once instead of after a
                            // reload (#710 finding 3).
                            self._applyRowState(data.row);
                            self._refetchSummary();
                            self._pushRecentCheckin(data);
                            if (self.printerEnabled && data.print_payload_base64) {
                                self.triggerRawBtPrint(data.print_payload_base64);
                            }
                        } else {
                            btn.disabled = false;
                            btn.textContent = i18n.checkIn || "Check In";
                            alert(
                                data.error || i18n.checkinFailed || "Check-in failed",
                            );
                        }
                    })
                    .catch(function () {
                        btn.disabled = false;
                        btn.textContent = i18n.checkIn || "Check In";
                        alert(i18n.networkError || "Network error");
                    });
            },

            rejectVerification: function (evt) {
                var self = this;
                var btn = evt.currentTarget;
                var url = btn.getAttribute("data-reject-url");
                var regId = btn.getAttribute("data-reg-id");
                var toastId = btn.getAttribute("data-toast-id");
                var i18n = window._checkinI18n || {};
                if (self._missingActionUrl(url)) return;

                if (
                    !window.confirm(
                        i18n.rejectConfirm ||
                            "Reject verification for this attendee? Their profile will be marked unverified due to photo mismatch.",
                    )
                ) {
                    return;
                }
                btn.disabled = true;
                btn.textContent = "...";

                fetch(url, {
                    method: "POST",
                    headers: { "X-CSRFToken": self.getCsrfToken() },
                })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            self._markRowUnverified(regId);
                            if (toastId) {
                                self.dismissToast(parseInt(toastId, 10));
                            }
                        } else {
                            btn.disabled = false;
                            btn.textContent = i18n.rejectAction || "Photo mismatch";
                            alert(
                                data.error ||
                                    i18n.rejectFailed ||
                                    "Could not reject verification",
                            );
                        }
                    })
                    .catch(function () {
                        btn.disabled = false;
                        btn.textContent = i18n.rejectAction || "Photo mismatch";
                        alert(i18n.networkError || "Network error");
                    });
            },

            markVerified: function (evt) {
                var self = this;
                var btn = evt.currentTarget;
                var url = btn.getAttribute("data-verify-url");
                var regId = btn.getAttribute("data-reg-id");
                var i18n = window._checkinI18n || {};
                if (self._missingActionUrl(url)) return;
                btn.disabled = true;
                btn.textContent = "...";

                fetch(url, {
                    method: "POST",
                    headers: { "X-CSRFToken": self.getCsrfToken() },
                })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            // Same applier as every other action: the row's
                            // pill swaps to verified and the Verify button
                            // leaves with the rebuilt actions.
                            self._applyRowState(data.row);
                        } else {
                            btn.disabled = false;
                            btn.textContent = i18n.verifyAction || "Verify";
                            alert(
                                data.error ||
                                    i18n.verifyFailed ||
                                    "Verification failed",
                            );
                        }
                    })
                    .catch(function () {
                        btn.disabled = false;
                        btn.textContent = i18n.verifyAction || "Verify";
                        alert(i18n.networkError || "Network error");
                    });
            },

            _markRowVerified: function (regId) {
                var self = this;
                var i18n = window._checkinI18n || {};
                var row = document.getElementById("manual-reg-" + regId);
                if (!row) return;
                // Swap the amber "Unverified" pill for a green "Verified" pill.
                var pill = row.querySelector("[data-verify-pill]");
                if (pill) {
                    pill.className =
                        "inline-flex items-center gap-0.5 rounded-full bg-green-100 dark:bg-green-900/30 px-1.5 py-0.5 text-xs font-medium text-green-700 dark:text-green-400";
                    pill.innerHTML =
                        '<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg> ' +
                        (i18n.verified || "Verified");
                }
                // Remove the verify button.
                var btn = row.querySelector(".manual-verify-btn");
                if (btn) btn.remove();

                // Add the Photo Mismatch / reject button if missing
                var actions = row.querySelector("[data-row-actions]");
                if (actions && !actions.querySelector(".manual-reject-btn")) {
                    var rejectUrl =
                        row.getAttribute("data-reject-url") ||
                        "/api/events/" +
                            self.eventId +
                            "/reject-verification/" +
                            regId +
                            "/";
                    var rejectBtn = document.createElement("button");
                    rejectBtn.type = "button";
                    rejectBtn.className =
                        "manual-reject-btn btn-link px-2 py-1.5 text-xs font-medium decoration-dotted transition-colors text-amber-600 dark:text-amber-400 hover:text-red-600 dark:hover:text-red-400";
                    rejectBtn.setAttribute("data-reject-url", rejectUrl);
                    rejectBtn.setAttribute("data-reg-id", regId);
                    rejectBtn.textContent = i18n.rejectAction || "Photo mismatch";
                    rejectBtn.addEventListener("click", function (clickEvent) {
                        self.rejectVerification(clickEvent);
                    });
                    actions.insertBefore(rejectBtn, actions.firstChild);
                }
            },

            _markRowUnverified: function (regId) {
                var self = this;
                var i18n = window._checkinI18n || {};
                var row = document.getElementById("manual-reg-" + regId);
                if (!row) return;
                // Swap the green "Verified" pill for an amber "Unverified" pill.
                var pill = row.querySelector(
                    ".bg-green-100, .dark\\:bg-green-900\\/30, [data-verify-pill]",
                );
                if (pill) {
                    pill.className =
                        "inline-flex items-center gap-0.5 rounded-full bg-amber-100 dark:bg-amber-900/30 px-1.5 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400";
                    pill.setAttribute("data-verify-pill", "");
                    pill.innerHTML =
                        '<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg> ' +
                        (i18n.unverified || "Unverified");
                }
                // Remove the reject button.
                var rejectBtn = row.querySelector(".manual-reject-btn");
                if (rejectBtn) rejectBtn.remove();

                // Restore verify button if missing
                var actions = row.querySelector("[data-row-actions]");
                if (actions && !actions.querySelector(".manual-verify-btn")) {
                    var verifyUrl =
                        row.getAttribute("data-verify-url") ||
                        "/api/events/" +
                            self.eventId +
                            "/verify/" +
                            regId +
                            "/";
                    var verifyBtn = document.createElement("button");
                    verifyBtn.type = "button";
                    verifyBtn.className =
                        "manual-verify-btn btn-crush-solid btn-sm bg-green-600 hover:bg-green-700 focus:ring-green-500 text-white";
                    verifyBtn.setAttribute("data-verify-url", verifyUrl);
                    verifyBtn.setAttribute("data-reg-id", regId);
                    verifyBtn.textContent = i18n.verifyAction || "Verify";
                    verifyBtn.addEventListener("click", function (clickEvent) {
                        self.markVerified(clickEvent);
                    });
                    actions.insertBefore(verifyBtn, actions.firstChild);
                }
            },
        };
    });

    // Invitation row component (reject modal)
    Alpine.data("invitationRow", function () {
        return {
            showRejectModal: false,
            openRejectModal: function () {
                this.showRejectModal = true;
            },
            closeRejectModal: function () {
                this.showRejectModal = false;
            },
        };
    });

    // =========================================================================
    // Admin Dashboard Components (CSP-compliant)
    // =========================================================================

    // Dashboard Tabs component for organizing analytics sections
    // Reads initial tab from data-initial-tab attribute
    Alpine.data("dashboardTabs", function () {
        return {
            activeTab: "overview",

            // CSP-compatible computed getters for each tab
            get isOverview() {
                return this.activeTab === "overview";
            },
            get isUsers() {
                return this.activeTab === "users";
            },
            get isEvents() {
                return this.activeTab === "events";
            },
            get isEngagement() {
                return this.activeTab === "engagement";
            },
            get isTechnical() {
                return this.activeTab === "technical";
            },
            get isGrowth() {
                return this.activeTab === "growth";
            },
            get isNotGrowth() {
                return this.activeTab !== "growth";
            },

            // Tab active state classes
            get overviewTabClass() {
                return this.activeTab === "overview" ? "active" : "";
            },
            get usersTabClass() {
                return this.activeTab === "users" ? "active" : "";
            },
            get eventsTabClass() {
                return this.activeTab === "events" ? "active" : "";
            },
            get engagementTabClass() {
                return this.activeTab === "engagement" ? "active" : "";
            },
            get technicalTabClass() {
                return this.activeTab === "technical" ? "active" : "";
            },
            get growthTabClass() {
                return this.activeTab === "growth" ? "active" : "";
            },

            init: function () {
                // Read initial tab from data attribute
                var initialTab = this.$el.getAttribute("data-initial-tab");
                if (initialTab) {
                    this.activeTab = initialTab;
                }
                // Also check URL hash for direct linking
                if (window.location.hash) {
                    var hashTab = window.location.hash.substring(1);
                    if (
                        [
                            "overview",
                            "users",
                            "events",
                            "engagement",
                            "technical",
                            "growth",
                        ].indexOf(hashTab) !== -1
                    ) {
                        this.activeTab = hashTab;
                    }
                }
                // If growth tab is active on load, notify growthCharts
                if (this.activeTab === "growth") {
                    var self = this;
                    setTimeout(function () {
                        document.dispatchEvent(new CustomEvent("growth-tab-shown"));
                    }, 0);
                }
            },

            setOverview: function () {
                this.activeTab = "overview";
                history.replaceState(null, "", "#overview");
            },
            setUsers: function () {
                this.activeTab = "users";
                history.replaceState(null, "", "#users");
            },
            setEvents: function () {
                this.activeTab = "events";
                history.replaceState(null, "", "#events");
            },
            setEngagement: function () {
                this.activeTab = "engagement";
                history.replaceState(null, "", "#engagement");
            },
            setTechnical: function () {
                this.activeTab = "technical";
                history.replaceState(null, "", "#technical");
            },
            setGrowth: function () {
                this.activeTab = "growth";
                history.replaceState(null, "", "#growth");
                setTimeout(function () {
                    document.dispatchEvent(new CustomEvent("growth-tab-shown"));
                }, 0);
            },
        };
    });

    // Growth Analytics Charts component for admin dashboard
    // Renders Chart.js charts for signup, verification, and cumulative growth trends
    Alpine.data("growthCharts", function () {
        return {
            loading: false,
            error: "",
            range: "30d",
            granularity: "",
            dauData: null,
            signupData: null,
            verificationData: null,
            cumulativeData: null,
            dauChart: null,
            signupChart: null,
            verificationChart: null,
            cumulativeChart: null,
            copied: false,

            get isLoading() {
                return this.loading;
            },
            get hasError() {
                return this.error !== "";
            },
            get errorMessage() {
                return this.error;
            },
            get isCopied() {
                return this.copied;
            },
            get copyButtonText() {
                return this.copied ? "Copied!" : "Copy to Clipboard";
            },

            // Granularity button states
            get isDayGranularity() {
                return this.activeGranularity === "day";
            },
            get isWeekGranularity() {
                return this.activeGranularity === "week";
            },
            get isMonthGranularity() {
                return this.activeGranularity === "month";
            },
            get dayBtnClass() {
                return this.activeGranularity === "day" ? "active" : "";
            },
            get weekBtnClass() {
                return this.activeGranularity === "week" ? "active" : "";
            },
            get monthBtnClass() {
                return this.activeGranularity === "month" ? "active" : "";
            },

            // Range button states
            get is7d() {
                return this.range === "7d";
            },
            get is30d() {
                return this.range === "30d";
            },
            get is90d() {
                return this.range === "90d";
            },
            get isAll() {
                return this.range === "all";
            },
            get range7dClass() {
                return this.range === "7d" ? "active" : "";
            },
            get range30dClass() {
                return this.range === "30d" ? "active" : "";
            },
            get range90dClass() {
                return this.range === "90d" ? "active" : "";
            },
            get rangeAllClass() {
                return this.range === "all" ? "active" : "";
            },

            get activeGranularity() {
                if (this.granularity) return this.granularity;
                if (this.range === "7d" || this.range === "30d") return "day";
                if (this.range === "90d") return "week";
                return "month";
            },

            // Summary getters for template binding
            get signupTotal() {
                return this.signupData ? this.signupData.summary.total_signups : 0;
            },
            get signupApproved() {
                return this.signupData ? this.signupData.summary.total_approved : 0;
            },
            get signupRate() {
                return this.signupData ? this.signupData.summary.approval_rate : 0;
            },
            get signupAvgPerDay() {
                return this.signupData ? this.signupData.summary.avg_per_day : 0;
            },

            get verifyTotal() {
                return this.verificationData
                    ? this.verificationData.summary.total_reviews
                    : 0;
            },
            get verifyApproved() {
                return this.verificationData
                    ? this.verificationData.summary.total_approved
                    : 0;
            },
            get verifyRejected() {
                return this.verificationData
                    ? this.verificationData.summary.total_rejected
                    : 0;
            },
            get verifyRevision() {
                return this.verificationData
                    ? this.verificationData.summary.total_revision
                    : 0;
            },
            get verifyRecontact() {
                return this.verificationData
                    ? this.verificationData.summary.total_recontact
                    : 0;
            },
            get verifyRate() {
                return this.verificationData
                    ? this.verificationData.summary.approval_rate
                    : 0;
            },

            get dauAvg() {
                return this.dauData ? this.dauData.summary.avg_dau : 0;
            },
            get dauMax() {
                return this.dauData ? this.dauData.summary.max_dau : 0;
            },
            get dauMin() {
                return this.dauData ? this.dauData.summary.min_dau : 0;
            },
            get dauTotal() {
                return this.dauData ? this.dauData.summary.total_days : 0;
            },

            manualGranularity: false,

            init: function () {
                var self = this;
                // Listen for tab activation event from dashboardTabs
                document.addEventListener("growth-tab-shown", function () {
                    if (!self.signupData) {
                        self.fetchAllCharts();
                    }
                });
                // Handle case where tab is already visible on page load
                if (self.$el && self.$el.offsetParent !== null) {
                    self.fetchAllCharts();
                }
            },

            setRange7d: function () {
                this.range = "7d";
                if (!this.manualGranularity) this.granularity = "";
                this.fetchAllCharts();
            },
            setRange30d: function () {
                this.range = "30d";
                if (!this.manualGranularity) this.granularity = "";
                this.fetchAllCharts();
            },
            setRange90d: function () {
                this.range = "90d";
                if (!this.manualGranularity) this.granularity = "";
                this.fetchAllCharts();
            },
            setRangeAll: function () {
                this.range = "all";
                if (!this.manualGranularity) this.granularity = "";
                this.fetchAllCharts();
            },

            setGranDay: function () {
                this.granularity = "day";
                this.manualGranularity = true;
                this.fetchAllCharts();
            },
            setGranWeek: function () {
                this.granularity = "week";
                this.manualGranularity = true;
                this.fetchAllCharts();
            },
            setGranMonth: function () {
                this.granularity = "month";
                this.manualGranularity = true;
                this.fetchAllCharts();
            },

            fetchAllCharts: function () {
                var self = this;
                self.loading = true;
                self.error = "";
                var params = "range=" + self.range;
                var gran = self.granularity || "";
                if (gran) params += "&granularity=" + gran;

                Promise.all([
                    fetch("/crush-admin/api/daily-active-users/?" + params).then(
                        function (r) {
                            return r.json();
                        },
                    ),
                    fetch("/crush-admin/api/signup-trend/?" + params).then(
                        function (r) {
                            return r.json();
                        },
                    ),
                    fetch("/crush-admin/api/verification-trend/?" + params).then(
                        function (r) {
                            return r.json();
                        },
                    ),
                    fetch("/crush-admin/api/cumulative-growth/?" + params).then(
                        function (r) {
                            return r.json();
                        },
                    ),
                ])
                    .then(function (results) {
                        self.dauData = results[0];
                        self.signupData = results[1];
                        self.verificationData = results[2];
                        self.cumulativeData = results[3];
                        self.loading = false;
                        // Use double requestAnimationFrame to ensure DOM is painted
                        requestAnimationFrame(function () {
                            requestAnimationFrame(function () {
                                self.renderDauChart();
                                self.renderSignupChart();
                                self.renderVerificationChart();
                                self.renderCumulativeChart();
                            });
                        });
                    })
                    .catch(function (err) {
                        self.error = "Failed to load chart data: " + err.message;
                        self.loading = false;
                    });
            },

            formatLabel: function (label) {
                // Format date label for display
                if (!label) return "";
                var d = new Date(label);
                if (isNaN(d.getTime())) return label;
                var month = d.toLocaleString("en", { month: "short" });
                return month + " " + d.getDate();
            },

            renderDauChart: function () {
                var canvas = document.getElementById("dauChart");
                if (!canvas || !this.dauData || canvas.offsetWidth === 0) return;
                if (this.dauChart) this.dauChart.destroy();
                var self = this;
                var labels = this.dauData.labels.map(function (l) {
                    return self.formatLabel(l);
                });
                this.dauChart = new Chart(canvas, {
                    type: "line",
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: "Active Users",
                                data: this.dauData.active_users,
                                borderColor: "rgb(139, 92, 246)",
                                backgroundColor: "rgba(139, 92, 246, 0.1)",
                                fill: true,
                                tension: 0.3,
                                pointRadius: 3,
                                pointHoverRadius: 5,
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: false,
                        resizeDelay: 100,
                        plugins: { legend: { position: "top" } },
                        scales: {
                            y: { beginAtZero: true, ticks: { stepSize: 1 } },
                        },
                    },
                });
            },

            renderSignupChart: function () {
                var canvas = document.getElementById("signupChart");
                if (!canvas || !this.signupData || canvas.offsetWidth === 0) return;
                if (this.signupChart) this.signupChart.destroy();
                var self = this;
                var labels = this.signupData.labels.map(function (l) {
                    return self.formatLabel(l);
                });
                this.signupChart = new Chart(canvas, {
                    type: "bar",
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: "Signups",
                                data: this.signupData.signups,
                                backgroundColor: "rgba(99, 102, 241, 0.7)",
                                borderColor: "rgb(99, 102, 241)",
                                borderWidth: 1,
                            },
                            {
                                label: "Approved",
                                data: this.signupData.approved,
                                backgroundColor: "rgba(34, 197, 94, 0.7)",
                                borderColor: "rgb(34, 197, 94)",
                                borderWidth: 1,
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: false,
                        resizeDelay: 100,
                        plugins: { legend: { position: "top" } },
                        scales: {
                            y: { beginAtZero: true, ticks: { stepSize: 1 } },
                        },
                    },
                });
            },

            renderVerificationChart: function () {
                var canvas = document.getElementById("verificationChart");
                if (!canvas || !this.verificationData || canvas.offsetWidth === 0)
                    return;
                if (this.verificationChart) this.verificationChart.destroy();
                var self = this;
                var labels = this.verificationData.labels.map(function (l) {
                    return self.formatLabel(l);
                });
                this.verificationChart = new Chart(canvas, {
                    type: "bar",
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: "Approved",
                                data: this.verificationData.approved,
                                backgroundColor: "rgba(34, 197, 94, 0.7)",
                                borderColor: "rgb(34, 197, 94)",
                                borderWidth: 1,
                            },
                            {
                                label: "Rejected",
                                data: this.verificationData.rejected,
                                backgroundColor: "rgba(239, 68, 68, 0.7)",
                                borderColor: "rgb(239, 68, 68)",
                                borderWidth: 1,
                            },
                            {
                                label: "Revision",
                                data: this.verificationData.revision,
                                backgroundColor: "rgba(245, 158, 11, 0.7)",
                                borderColor: "rgb(245, 158, 11)",
                                borderWidth: 1,
                            },
                            {
                                label: "Recontact",
                                data: this.verificationData.recontact,
                                backgroundColor: "rgba(139, 92, 246, 0.7)",
                                borderColor: "rgb(139, 92, 246)",
                                borderWidth: 1,
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: false,
                        resizeDelay: 100,
                        plugins: { legend: { position: "top" } },
                        scales: {
                            x: { stacked: true },
                            y: {
                                stacked: true,
                                beginAtZero: true,
                                ticks: { stepSize: 1 },
                            },
                        },
                    },
                });
            },

            renderCumulativeChart: function () {
                var canvas = document.getElementById("cumulativeChart");
                if (!canvas || !this.cumulativeData || canvas.offsetWidth === 0) return;
                if (this.cumulativeChart) this.cumulativeChart.destroy();
                var self = this;
                var labels = this.cumulativeData.labels.map(function (l) {
                    return self.formatLabel(l);
                });
                this.cumulativeChart = new Chart(canvas, {
                    type: "line",
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: "Total Profiles",
                                data: this.cumulativeData.total_profiles,
                                borderColor: "rgb(139, 92, 246)",
                                backgroundColor: "rgba(139, 92, 246, 0.1)",
                                fill: true,
                                tension: 0.3,
                            },
                            {
                                label: "Total Approved",
                                data: this.cumulativeData.total_approved,
                                borderColor: "rgb(34, 197, 94)",
                                backgroundColor: "rgba(34, 197, 94, 0.1)",
                                fill: true,
                                tension: 0.3,
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: false,
                        resizeDelay: 100,
                        plugins: { legend: { position: "top" } },
                        scales: {
                            y: { beginAtZero: true },
                        },
                    },
                });
            },

            copyToClipboard: function () {
                var self = this;
                var lines = [];
                lines.push("Crush.lu Growth Summary");
                lines.push("Range: " + self.range);
                lines.push("Generated: " + new Date().toISOString().split("T")[0]);
                lines.push("");

                if (self.dauData) {
                    lines.push("=== Daily Active Users ===");
                    lines.push("Avg DAU: " + self.dauData.summary.avg_dau);
                    lines.push("Max DAU: " + self.dauData.summary.max_dau);
                    lines.push("Min DAU: " + self.dauData.summary.min_dau);
                    lines.push("");
                    lines.push("Period\tActive Users");
                    for (var k = 0; k < self.dauData.labels.length; k++) {
                        lines.push(
                            self.dauData.labels[k] +
                                "\t" +
                                self.dauData.active_users[k],
                        );
                    }
                    lines.push("");
                }

                if (self.signupData) {
                    lines.push("=== Signup Trends ===");
                    lines.push(
                        "Total Signups: " + self.signupData.summary.total_signups,
                    );
                    lines.push(
                        "Total Approved: " + self.signupData.summary.total_approved,
                    );
                    lines.push(
                        "Approval Rate: " + self.signupData.summary.approval_rate + "%",
                    );
                    lines.push(
                        "Avg Signups/Day: " + self.signupData.summary.avg_per_day,
                    );
                    lines.push("");
                    lines.push("Period\tSignups\tApproved");
                    for (var i = 0; i < self.signupData.labels.length; i++) {
                        lines.push(
                            self.signupData.labels[i] +
                                "\t" +
                                self.signupData.signups[i] +
                                "\t" +
                                self.signupData.approved[i],
                        );
                    }
                    lines.push("");
                }

                if (self.verificationData) {
                    lines.push("=== Verification Pipeline ===");
                    lines.push(
                        "Total Reviews: " + self.verificationData.summary.total_reviews,
                    );
                    lines.push(
                        "Approved: " + self.verificationData.summary.total_approved,
                    );
                    lines.push(
                        "Rejected: " + self.verificationData.summary.total_rejected,
                    );
                    lines.push(
                        "Revision: " + self.verificationData.summary.total_revision,
                    );
                    lines.push(
                        "Approval Rate: " +
                            self.verificationData.summary.approval_rate +
                            "%",
                    );
                    lines.push("");
                    lines.push("Period\tApproved\tRejected\tRevision");
                    for (var j = 0; j < self.verificationData.labels.length; j++) {
                        lines.push(
                            self.verificationData.labels[j] +
                                "\t" +
                                self.verificationData.approved[j] +
                                "\t" +
                                self.verificationData.rejected[j] +
                                "\t" +
                                self.verificationData.revision[j],
                        );
                    }
                }

                var text = lines.join("\n");
                navigator.clipboard.writeText(text).then(function () {
                    self.copied = true;
                    setTimeout(function () {
                        self.copied = false;
                    }, 2000);
                });
            },
        };
    });

    // Collapsible model group component for admin index page
    // Reads initial state from data-default-open attribute
    Alpine.data("modelGroup", function () {
        return {
            isOpen: true,

            // CSP-compatible computed getters
            get isClosed() {
                return !this.isOpen;
            },
            get toggleClass() {
                return this.isOpen ? "" : "collapsed";
            },
            get contentClass() {
                return this.isOpen ? "" : "collapsed";
            },
            get ariaExpanded() {
                return this.isOpen ? "true" : "false";
            },

            init: function () {
                // Read initial state from data attribute
                var defaultOpen = this.$el.getAttribute("data-default-open");
                this.isOpen = defaultOpen !== "false";

                // Restore state from localStorage if available
                var groupId = this.$el.getAttribute("data-group-id");
                if (groupId) {
                    var savedState = localStorage.getItem("admin-group-" + groupId);
                    if (savedState !== null) {
                        this.isOpen = savedState === "true";
                    }
                }
            },

            toggle: function () {
                this.isOpen = !this.isOpen;
                // Save state to localStorage
                var groupId = this.$el.getAttribute("data-group-id");
                if (groupId) {
                    localStorage.setItem("admin-group-" + groupId, this.isOpen);
                }
            },
        };
    });

    // Action Center component with collapse persistence
    Alpine.data("actionCenter", function () {
        return {
            isCollapsed: false,

            // CSP-compatible computed getters
            get isExpanded() {
                return !this.isCollapsed;
            },
            get toggleIcon() {
                return this.isCollapsed ? "+" : "-";
            },
            get contentClass() {
                return this.isCollapsed ? "collapsed" : "";
            },

            init: function () {
                // Restore state from localStorage
                var savedState = localStorage.getItem("admin-action-center-collapsed");
                if (savedState !== null) {
                    this.isCollapsed = savedState === "true";
                }
            },

            toggle: function () {
                this.isCollapsed = !this.isCollapsed;
                localStorage.setItem("admin-action-center-collapsed", this.isCollapsed);
            },
        };
    });

    // Today's Focus tabs for index page
    Alpine.data("todaysFocus", function () {
        return {
            activeTab: "events",

            // CSP-compatible computed getters
            get isEventsTab() {
                return this.activeTab === "events";
            },
            get isSubmissionsTab() {
                return this.activeTab === "submissions";
            },
            get isAlertsTab() {
                return this.activeTab === "alerts";
            },

            get eventsTabClass() {
                return this.activeTab === "events" ? "active" : "";
            },
            get submissionsTabClass() {
                return this.activeTab === "submissions" ? "active" : "";
            },
            get alertsTabClass() {
                return this.activeTab === "alerts" ? "active" : "";
            },

            setEvents: function () {
                this.activeTab = "events";
            },
            setSubmissions: function () {
                this.activeTab = "submissions";
            },
            setAlerts: function () {
                this.activeTab = "alerts";
            },
        };
    });

    // Date filter component for dashboard
    Alpine.data("dateFilter", function () {
        return {
            selectedRange: "30d",

            // CSP-compatible computed getters
            get is7d() {
                return this.selectedRange === "7d";
            },
            get is30d() {
                return this.selectedRange === "30d";
            },
            get is90d() {
                return this.selectedRange === "90d";
            },
            get isAll() {
                return this.selectedRange === "all";
            },

            init: function () {
                // Read initial value from URL param or data attribute
                var urlParams = new URLSearchParams(window.location.search);
                var rangeParam = urlParams.get("range");
                if (rangeParam) {
                    this.selectedRange = rangeParam;
                } else {
                    var defaultRange = this.$el.getAttribute("data-default-range");
                    if (defaultRange) {
                        this.selectedRange = defaultRange;
                    }
                }
            },

            setRange: function (range) {
                this.selectedRange = range;
            },

            onSelectChange: function () {
                this.selectedRange = this.$refs.rangeSelect.value;
            },

            apply: function () {
                // Update URL with new range and reload
                var url = new URL(window.location);
                url.searchParams.set("range", this.selectedRange);
                window.location.href = url.toString();
            },
        };
    });

    // Screening Call Calibration Component (Phase 4)
    // 3-section, minimal-state flow for Coaches who have the pre-screening data.
    // Section 2 auto-populates a suggested script from the user's what_is_crush answer.
    Alpine.data("screeningCallCalibration", function () {
        return {
            warmIntroComplete: false,
            conceptCalibrationComplete: false,
            conceptNotes: "",
            discretionNotes: "",
            conceptAnswer: "",
            init() {
                // Pre-populate from prior checklist_data if the Coach saved partial state.
                const initialEl = this.$root.querySelector("[data-checklist-initial]");
                if (initialEl && initialEl.dataset.checklistInitial) {
                    try {
                        const prior = JSON.parse(initialEl.dataset.checklistInitial);
                        this.warmIntroComplete = !!prior.warm_intro_complete;
                        this.conceptCalibrationComplete =
                            !!prior.concept_calibration_complete;
                        this.conceptNotes = prior.concept_notes || "";
                        this.discretionNotes = prior.discretion_notes || "";
                    } catch (e) {
                        // Malformed JSON — ignore, start empty.
                    }
                }
                const conceptEl = this.$root.querySelector("[data-concept-answer]");
                if (conceptEl) {
                    this.conceptAnswer = conceptEl.dataset.conceptAnswer || "";
                }
            },
            // CSP-safe input handlers: Alpine's @alpinejs/csp build can't evaluate
            // the assignment expression x-model generates, so we wire each field
            // with :checked / :value + @change / @input + a method that pulls the
            // value off $event.target (same pattern as elsewhere in this file).
            toggleWarmIntro(e) {
                this.warmIntroComplete = !!e.target.checked;
            },
            toggleConceptCalibration(e) {
                this.conceptCalibrationComplete = !!e.target.checked;
            },
            updateConceptNotes(e) {
                this.conceptNotes = e.target.value;
            },
            updateDiscretionNotes(e) {
                this.discretionNotes = e.target.value;
            },

            get completedCount() {
                let n = 0;
                if (this.warmIntroComplete) n++;
                if (this.conceptCalibrationComplete) n++;
                return n;
            },
            get progressWidth() {
                return "width: " + (this.completedCount / 2) * 100 + "%";
            },
            get submitDisabled() {
                return !(this.warmIntroComplete && this.conceptCalibrationComplete);
            },
            get submitButtonClass() {
                return this.submitDisabled
                    ? "bg-gray-300 dark:bg-gray-700 text-gray-500 dark:text-gray-400 cursor-not-allowed"
                    : "bg-crush-purple text-white hover:bg-purple-700";
            },
            get conceptScript() {
                // Auto-populate a gentle calibration script based on the user's
                // pre-screening answer. Strings are English-first; the Coach adapts live.
                switch (this.conceptAnswer) {
                    case "tinder":
                        return "I saw you described Crush.lu as similar to Tinder. Let me share how we're different — we're events-first, and our Coaches introduce people one-on-one. Does that change how you'd like to use the platform?";
                    case "matchmaking":
                        return "You described us as a matchmaking service. We do introduce people, but events are where the magic happens — tell me, how open are you to attending an in-person event in the next month?";
                    case "unsure":
                        return "You mentioned you're still figuring it out — that's perfect, most of our members start there. Let me walk you through how a typical first month looks at Crush.lu, and you can tell me what resonates.";
                    case "events":
                        return "You described us well — events-first is exactly right. Tell me, which event format sounds most exciting to you, and what would make the perfect first event for you?";
                    default:
                        return "Let me explain how Crush.lu works in one minute, then I'll ask you how that lands. We're events-first, Coach-supported, and the online piece is opt-in.";
                }
            },
            get checklistDataJson() {
                // Flat keys compatible with the JSONField — legacy and calibration
                // coexist inside the same column.
                return JSON.stringify({
                    mode: "calibration",
                    warm_intro_complete: this.warmIntroComplete,
                    concept_calibration_complete: this.conceptCalibrationComplete,
                    concept_notes: this.conceptNotes,
                    discretion_notes: this.discretionNotes,
                    concept_answer: this.conceptAnswer,
                });
            },
        };
    });

    // Screening Call Guideline Component for Coach Review
    // 7-step accordion with checklist items and notes
    Alpine.data("screeningCallGuideline", function () {
        return {
            // Active accordion section (1-5, 0 = none)
            activeSection: 1,

            // Checklist completion flags
            introductionComplete: false,
            languageConfirmed: false,
            residenceConfirmed: false,
            crushMeaningAsked: false,
            questionsAnswered: false,

            // Notes for each section
            residenceNotes: "",
            crushMeaningNotes: "",
            questionsNotes: "",
            finalNotes: "",

            // Failed call form visibility
            showFailedCallForm: false,

            // Required steps for validation
            requiredSteps: ["introductionComplete", "residenceConfirmed"],

            // CSP-safe computed getters
            get completedCount() {
                var count = 0;
                if (this.introductionComplete) count++;
                if (this.languageConfirmed) count++;
                if (this.residenceConfirmed) count++;
                if (this.crushMeaningAsked) count++;
                if (this.questionsAnswered) count++;
                return count;
            },

            get progressPercent() {
                return Math.round((this.completedCount / 5) * 100);
            },

            get progressWidth() {
                return "width: " + this.progressPercent + "%";
            },

            get progressText() {
                return this.progressPercent + "% complete";
            },

            get isValid() {
                return this.introductionComplete && this.residenceConfirmed;
            },

            get isInvalid() {
                return !this.isValid;
            },

            get submitDisabled() {
                return !this.isValid;
            },

            get submitButtonClass() {
                return this.isValid
                    ? "bg-green-500 hover:bg-green-600 cursor-pointer"
                    : "bg-gray-300 cursor-not-allowed";
            },

            get hideFailedCallForm() {
                return !this.showFailedCallForm;
            },

            // Section visibility getters
            get section1Open() {
                return this.activeSection === 1;
            },
            get section2Open() {
                return this.activeSection === 2;
            },
            get section3Open() {
                return this.activeSection === 3;
            },
            get section4Open() {
                return this.activeSection === 4;
            },
            get section5Open() {
                return this.activeSection === 5;
            },

            // Section header classes (CSP-safe)
            _sectionHeaderClass: function (num, isComplete) {
                var isDark = document.documentElement.classList.contains("dark");
                if (this.activeSection === num) {
                    return isDark
                        ? "screening-header-active-dark"
                        : "bg-purple-100 border-purple-300";
                }
                if (isComplete) {
                    return isDark
                        ? "screening-header-complete-dark"
                        : "bg-green-50 border-green-200";
                }
                return isDark
                    ? "screening-header-default-dark"
                    : "bg-gray-50 border-gray-200";
            },
            get section1HeaderClass() {
                return this._sectionHeaderClass(1, this.introductionComplete);
            },
            get section2HeaderClass() {
                return this._sectionHeaderClass(2, this.languageConfirmed);
            },
            get section3HeaderClass() {
                return this._sectionHeaderClass(3, this.residenceConfirmed);
            },
            get section4HeaderClass() {
                return this._sectionHeaderClass(4, this.crushMeaningAsked);
            },
            get section5HeaderClass() {
                return this._sectionHeaderClass(5, this.questionsAnswered);
            },

            // Status icon visibility getters
            get section1Complete() {
                return this.introductionComplete;
            },
            get section2Complete() {
                return this.languageConfirmed;
            },
            get section3Complete() {
                return this.residenceConfirmed;
            },
            get section4Complete() {
                return this.crushMeaningAsked;
            },
            get section5Complete() {
                return this.questionsAnswered;
            },

            // Required badge visibility
            get section1Required() {
                return true;
            },
            get section3Required() {
                return true;
            },

            // Methods
            init: function () {
                // Load existing checklist data if present
                var dataEl = this.$el.querySelector("[data-checklist-initial]");
                if (dataEl) {
                    try {
                        var initial = JSON.parse(
                            dataEl.getAttribute("data-checklist-initial") || "{}",
                        );
                        if (initial.introduction_complete)
                            this.introductionComplete = true;
                        if (initial.language_confirmed) this.languageConfirmed = true;
                        if (initial.residence_confirmed) this.residenceConfirmed = true;
                        if (initial.crush_meaning_asked) this.crushMeaningAsked = true;
                        if (initial.questions_answered) this.questionsAnswered = true;
                        if (initial.residence_notes)
                            this.residenceNotes = initial.residence_notes;
                        if (initial.crush_meaning_notes)
                            this.crushMeaningNotes = initial.crush_meaning_notes;
                        if (initial.questions_notes)
                            this.questionsNotes = initial.questions_notes;
                    } catch (e) {
                        console.warn("Failed to parse initial checklist data", e);
                    }
                }
            },

            toggleSection: function (num) {
                this.activeSection = this.activeSection === num ? 0 : num;
            },

            openSection1: function () {
                this.toggleSection(1);
            },
            openSection2: function () {
                this.toggleSection(2);
            },
            openSection3: function () {
                this.toggleSection(3);
            },
            openSection4: function () {
                this.toggleSection(4);
            },
            openSection5: function () {
                this.toggleSection(5);
            },

            goToNextSection: function () {
                if (this.activeSection < 5) {
                    this.activeSection = this.activeSection + 1;
                }
            },

            toggleIntroduction: function () {
                this.introductionComplete = !this.introductionComplete;
            },
            toggleLanguage: function () {
                this.languageConfirmed = !this.languageConfirmed;
            },
            toggleResidence: function () {
                this.residenceConfirmed = !this.residenceConfirmed;
            },
            toggleCrushMeaning: function () {
                this.crushMeaningAsked = !this.crushMeaningAsked;
            },
            toggleQuestions: function () {
                this.questionsAnswered = !this.questionsAnswered;
            },
            toggleFailedCallForm: function () {
                this.showFailedCallForm = !this.showFailedCallForm;
            },

            // Input handlers for CSP compliance (x-model not supported)
            updateResidenceNotes: function (event) {
                this.residenceNotes = event.target.value;
            },
            updateCrushMeaningNotes: function (event) {
                this.crushMeaningNotes = event.target.value;
            },
            updateQuestionsNotes: function (event) {
                this.questionsNotes = event.target.value;
            },
            updateFinalNotes: function (event) {
                this.finalNotes = event.target.value;
            },

            // Serialize checklist data to JSON for form submission (getter for CSP compliance)
            get checklistDataJson() {
                return JSON.stringify({
                    introduction_complete: this.introductionComplete,
                    language_confirmed: this.languageConfirmed,
                    residence_confirmed: this.residenceConfirmed,
                    residence_notes: this.residenceNotes,
                    crush_meaning_asked: this.crushMeaningAsked,
                    crush_meaning_notes: this.crushMeaningNotes,
                    questions_answered: this.questionsAnswered,
                    questions_notes: this.questionsNotes,
                });
            },
        };
    });

    // Email Preview Modal (for coach review page)
    Alpine.data("emailPreviewModal", function () {
        return {
            isOpen: false,
            isLoading: false,

            get modalClasses() {
                return this.isOpen
                    ? "fixed inset-0 z-50 flex items-center justify-center"
                    : "hidden";
            },

            get showLoading() {
                return this.isLoading;
            },

            open: function () {
                this.isOpen = true;
                this.isLoading = true;
            },

            close: function () {
                this.isOpen = false;
                this.isLoading = false;
            },

            // CSP-compliant event handlers
            handleCloseClick: function () {
                this.close();
            },

            handleBackdropClick: function () {
                this.close();
            },

            handleEscape: function () {
                if (this.isOpen) {
                    this.close();
                }
            },

            handlePreviewLoaded: function () {
                this.isLoading = false;
            },

            handleSubmitClick: function () {
                this.close();
                this.submitForm();
            },

            submitForm: function () {
                // Find and submit the review form
                var form = document.querySelector('form[method="post"]');
                if (form) {
                    form.submit();
                }
            },
        };
    });

    // Review Tabs Component (for coach review page - 2 tabs: Screening + Decision)
    Alpine.data("reviewTabs", function () {
        return {
            activeTab: 1, // 1=Screening, 2=Decision
            callCompleted: false,

            init: function () {
                var callCompletedAttr = this.$el.getAttribute("data-call-completed");
                if (callCompletedAttr === "true") {
                    this.callCompleted = true;
                }
            },

            get isScreeningTab() {
                return this.activeTab === 1;
            },
            get isDecisionTab() {
                return this.activeTab === 2;
            },

            get screeningTabClass() {
                return this.getTabClasses(1);
            },
            get decisionTabClass() {
                return this.getTabClasses(2);
            },

            get showCallWarning() {
                return !this.callCompleted;
            },

            showScreening: function () {
                this.activeTab = 1;
            },
            showDecision: function () {
                this.activeTab = 2;
            },

            getTabClasses: function (tabNum) {
                var base =
                    "px-6 py-3 font-semibold rounded-t-lg transition-all cursor-pointer";
                var active =
                    "bg-white dark:bg-gray-800 text-purple-600 dark:text-purple-400 border-b-2 border-purple-600 dark:border-purple-400";
                var inactive =
                    "bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600";
                return this.activeTab === tabNum
                    ? base + " " + active
                    : base + " " + inactive;
            },

            completeScreening: function () {
                this.callCompleted = true;
                this.activeTab = 2;
            },
        };
    });

    // =========================================================================
    // Language Tabs - For multilingual form fields
    // =========================================================================
    Alpine.data("languageTabs", function () {
        return {
            activeLanguage: "en",

            get isEnglish() {
                return this.activeLanguage === "en";
            },
            get isGerman() {
                return this.activeLanguage === "de";
            },
            get isFrench() {
                return this.activeLanguage === "fr";
            },

            get englishTabClass() {
                return this.activeLanguage === "en"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },
            get germanTabClass() {
                return this.activeLanguage === "de"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },
            get frenchTabClass() {
                return this.activeLanguage === "fr"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },

            setEnglish: function () {
                this.activeLanguage = "en";
            },
            setGerman: function () {
                this.activeLanguage = "de";
            },
            setFrench: function () {
                this.activeLanguage = "fr";
            },
        };
    });

    // Auto-submit select: submits the parent form on change
    Alpine.data("confirmAction", function () {
        return {
            confirming: false,
            get isConfirming() {
                return this.confirming;
            },
            get isIdle() {
                return !this.confirming;
            },
            requestConfirm() {
                this.confirming = true;
            },
            cancel() {
                this.confirming = false;
            },
            proceed() {
                this.confirming = false;
                this.$el.closest("form").submit();
            },
        };
    });

    Alpine.data("autoSubmitSelect", function () {
        return {
            submit() {
                this.$el.closest("form").submit();
            },
        };
    });

    // Coach dashboard: collapsible callback list (show first 3, toggle to show all)
    Alpine.data("callbackList", function () {
        return {
            isExpanded: false,
            _showAll: "",
            _showLess: "",
            init() {
                var el = this.$el.closest("[data-count]") || this.$el;
                this._showAll = el.getAttribute("data-text-show-all") || "Show all";
                this._showLess = el.getAttribute("data-text-show-less") || "Show less";
            },
            get toggleLabel() {
                var count = this.$el.closest("[data-count]").getAttribute("data-count");
                return this.isExpanded
                    ? this._showLess
                    : this._showAll + " (" + count + ")";
            },
            toggle() {
                this.isExpanded = !this.isExpanded;
            },
        };
    });

    // SMS Invite Filter - gender filter + last-minute mode for coach event SMS invite page
    Alpine.data("smsInviteFilter", function () {
        return {
            activeFilter: "all",
            isLastMinuteMode: false,

            get isAll() {
                return this.activeFilter === "all";
            },
            get isWomen() {
                return this.activeFilter === "F";
            },
            get isMen() {
                return this.activeFilter === "M";
            },
            get isOther() {
                return this.activeFilter === "other";
            },
            get isLastMinute() {
                return this.isLastMinuteMode;
            },
            get isRegular() {
                return !this.isLastMinuteMode;
            },

            get allButtonClass() {
                return this.isAll
                    ? "bg-crush-purple text-white"
                    : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 border border-gray-200 dark:border-gray-700";
            },
            get womenButtonClass() {
                return this.isWomen
                    ? "bg-pink-600 text-white"
                    : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 border border-gray-200 dark:border-gray-700";
            },
            get menButtonClass() {
                return this.isMen
                    ? "bg-blue-600 text-white"
                    : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 border border-gray-200 dark:border-gray-700";
            },
            get otherButtonClass() {
                return this.isOther
                    ? "bg-purple-600 text-white"
                    : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 border border-gray-200 dark:border-gray-700";
            },
            get lastMinuteButtonClass() {
                return this.isLastMinuteMode
                    ? "bg-red-600 text-white border-red-600"
                    : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-red-50 dark:hover:bg-red-900/20 border-gray-200 dark:border-gray-700";
            },

            filterAll() {
                this.activeFilter = "all";
                this.applyFilter();
            },
            filterWomen() {
                this.activeFilter = "F";
                this.applyFilter();
            },
            filterMen() {
                this.activeFilter = "M";
                this.applyFilter();
            },
            filterOther() {
                this.activeFilter = "other";
                this.applyFilter();
            },
            toggleLastMinute() {
                this.isLastMinuteMode = !this.isLastMinuteMode;
            },

            applyFilter() {
                var filter = this.activeFilter;
                var root = this.$root;
                var rows = root.querySelectorAll("[data-gender]");
                for (var i = 0; i < rows.length; i++) {
                    var gender = rows[i].getAttribute("data-gender");
                    if (filter === "all") {
                        rows[i].style.display = "";
                    } else if (filter === "other") {
                        rows[i].style.display =
                            gender !== "F" && gender !== "M" ? "" : "none";
                    } else {
                        rows[i].style.display = gender === filter ? "" : "none";
                    }
                }
            },
        };
    });

    // =========================================================================
    // Coach Team Stats - claim submissions
    // =========================================================================
    Alpine.data("coachTeamStats", function () {
        return {
            claimingId: null,
            claimError: "",
            claimSuccess: "",

            get hasClaimError() {
                return this.claimError !== "";
            },

            get hasClaimSuccess() {
                return this.claimSuccess !== "";
            },

            claimFromButton: function () {
                var id = parseInt(this.$el.getAttribute("data-submission-id"));
                if (id) {
                    this.claimSubmission(id);
                }
            },

            claimSubmission: function (submissionId) {
                var self = this;
                if (self.claimingId) return;
                self.claimingId = submissionId;
                self.claimError = "";
                self.claimSuccess = "";

                fetch("/api/coach/team/claim/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify({ submission_id: submissionId }),
                })
                    .then(function (r) {
                        return r.json().then(function (d) {
                            return { ok: r.ok, data: d };
                        });
                    })
                    .then(function (result) {
                        self.claimingId = null;
                        if (result.ok && result.data.success) {
                            self.claimSuccess = result.data.message;
                            setTimeout(function () {
                                window.location.reload();
                            }, 1500);
                        } else {
                            self.claimError = result.data.error || gettext("Claim failed");
                        }
                    })
                    .catch(function () {
                        self.claimingId = null;
                        self.claimError = gettext("Network error");
                    });
            },

            getCsrfToken: function () {
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                var cookies = document.cookie.split("; ");
                for (var i = 0; i < cookies.length; i++) {
                    if (cookies[i].indexOf("csrftoken=") === 0) {
                        return cookies[i].substring("csrftoken=".length);
                    }
                }
                return "";
            },
        };
    });

    // Coach intro template picker — fills the textarea (only when empty or
    // confirmed) so the coach can edit before saving.
    Alpine.data("introTemplatePicker", function () {
        return {
            applyTemplate: function (event) {
                var select = event.target;
                var option = select.options[select.selectedIndex];
                if (!option || !option.value) return;
                var body = option.getAttribute("data-body") || "";
                var textarea = document.getElementById("coach_introduction");
                if (!textarea) return;
                var existing = textarea.value.trim();
                if (existing) {
                    var ok = window.confirm(
                        "Replace your current intro with this template?",
                    );
                    if (!ok) {
                        select.value = "";
                        return;
                    }
                }
                textarea.value = body;
                textarea.focus();
                select.value = "";
            },
        };
    });

    // ========================================================================
    // Campaign Dashboard (crush-admin/campaigns/)
    // ========================================================================

    Alpine.data("campaignDashboardTabs", function () {
        return {
            activeTab: "overview",

            get isOverview() { return this.activeTab === "overview"; },
            get isCampaigns() { return this.activeTab === "campaigns"; },
            get isWhatsapp() { return this.activeTab === "whatsapp"; },
            get isReminders() { return this.activeTab === "reminders"; },
            get isSegments() { return this.activeTab === "segments"; },

            get overviewTabClass() { return this.activeTab === "overview" ? "active" : ""; },
            get campaignsTabClass() { return this.activeTab === "campaigns" ? "active" : ""; },
            get whatsappTabClass() { return this.activeTab === "whatsapp" ? "active" : ""; },
            get remindersTabClass() { return this.activeTab === "reminders" ? "active" : ""; },
            get segmentsTabClass() { return this.activeTab === "segments" ? "active" : ""; },

            setOverview() { this.activeTab = "overview"; },
            setCampaigns() { this.activeTab = "campaigns"; },
            setWhatsapp() { this.activeTab = "whatsapp"; },
            setReminders() {
                this.activeTab = "reminders";
                document.dispatchEvent(new CustomEvent("reminders-tab-shown"));
            },
            setSegments() { this.activeTab = "segments"; },
        };
    });

    function campaignRenderChart(canvas, type, payload, palette) {
        if (!canvas || typeof Chart === "undefined") return null;
        var datasets = payload.datasets.map(function (dataset, i) {
            var color = palette[i % palette.length];
            return {
                label: dataset.label,
                data: dataset.data,
                borderColor: color.border,
                backgroundColor: color.bg,
                fill: type === "line",
                tension: 0.3,
            };
        });
        return new Chart(canvas, {
            type: type,
            data: { labels: payload.labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
            },
        });
    }

    var CAMPAIGN_CHART_PALETTE = [
        { border: "rgb(99, 102, 241)", bg: "rgba(99, 102, 241, 0.15)" },   // email
        { border: "rgb(34, 197, 94)", bg: "rgba(34, 197, 94, 0.15)" },     // whatsapp
        { border: "rgb(139, 92, 246)", bg: "rgba(139, 92, 246, 0.15)" },   // push
    ];

    Alpine.data("campaignOverviewChart", function () {
        return {
            chart: null,
            init() {
                var self = this;
                fetch("/crush-admin/api/campaign-overview/")
                    .then(function (resp) { return resp.json(); })
                    .then(function (payload) {
                        var canvas = self.$el.querySelector("canvas");
                        self.chart = campaignRenderChart(
                            canvas, "line", payload, CAMPAIGN_CHART_PALETTE
                        );
                    })
                    .catch(function () { /* chart is decorative */ });
            },
        };
    });

    Alpine.data("remindersFunnelChart", function () {
        return {
            chart: null,
            loaded: false,
            init() {
                var self = this;
                document.addEventListener("reminders-tab-shown", function () {
                    if (self.loaded) return;
                    self.loaded = true;
                    fetch("/crush-admin/api/reminders-funnel/")
                        .then(function (resp) { return resp.json(); })
                        .then(function (payload) {
                            var canvas = self.$el.querySelector("canvas");
                            self.chart = campaignRenderChart(
                                canvas, "bar", payload, CAMPAIGN_CHART_PALETTE
                            );
                        })
                        .catch(function () { /* chart is decorative */ });
                });
            },
        };
    });

    Alpine.data("campaignClicksChart", function () {
        return {
            chart: null,
            init() {
                var self = this;
                var campaignId = self.$el.dataset.campaignId;
                if (!campaignId) return;
                fetch("/crush-admin/api/campaign-clicks/" + campaignId + "/")
                    .then(function (resp) { return resp.json(); })
                    .then(function (payload) {
                        if (!payload.labels.length) return;
                        var canvas = self.$el.querySelector("canvas");
                        self.chart = campaignRenderChart(
                            canvas, "line", payload,
                            [{ border: "rgb(236, 72, 153)", bg: "rgba(236, 72, 153, 0.15)" }]
                        );
                    })
                    .catch(function () { /* chart is decorative */ });
            },
        };
    });

    Alpine.data("campaignComposer", function () {
        return {
            step: 1,
            channels: [],
            sendMode: "draft",

            // Step visibility
            get isStep1() { return this.step === 1; },
            get isStep2() { return this.step === 2; },
            get isStep3() { return this.step === 3; },
            get isStep4() { return this.step === 4; },
            get step1Class() { return this.step === 1 ? "active" : this.step > 1 ? "done" : ""; },
            get step2Class() { return this.step === 2 ? "active" : this.step > 2 ? "done" : ""; },
            get step3Class() { return this.step === 3 ? "active" : this.step > 3 ? "done" : ""; },
            get step4Class() { return this.step === 4 ? "active" : ""; },

            // Channel content sections
            get emailEnabled() { return this.channels.indexOf("email") !== -1; },
            get whatsappEnabled() { return this.channels.indexOf("whatsapp") !== -1; },
            get pushEnabled() { return this.channels.indexOf("push") !== -1; },
            get hasChannels() { return this.channels.length > 0; },
            get noChannels() { return this.channels.length === 0; },

            // Schedule section
            get isScheduleMode() { return this.sendMode === "schedule"; },
            get isSendNow() { return this.sendMode === "now"; },
            get isDraftMode() { return this.sendMode === "draft"; },
            get submitLabel() {
                if (this.sendMode === "now") return "Launch campaign";
                if (this.sendMode === "schedule") return "Schedule campaign";
                return "Save draft";
            },

            next() {
                if (this.step >= 4) return;
                this.step++;
                if (this.step === 3) this.refreshPreview();
            },
            prev() {
                if (this.step > 1) this.step--;
            },
            refreshPreview() {
                // Trigger the HTMX-powered estimate + preview refreshes.
                var form = this.$el.closest("form") || this.$el.querySelector("form");
                if (!form || typeof htmx === "undefined") return;
                var estimateBtn = form.querySelector("[data-estimate-trigger]");
                var previewBtn = form.querySelector("[data-preview-trigger]");
                if (estimateBtn) htmx.trigger(estimateBtn, "refresh-estimate");
                if (previewBtn) htmx.trigger(previewBtn, "refresh-preview");
            },

            // Channel checkboxes and send-mode radios are wired with @change +
            // :checked (bare method/getter refs) instead of x-model, because the
            // Alpine CSP build cannot evaluate x-model's "channels = __placeholder"
            // setter expression (see campaign_composer.html). @change fires only on
            // user interaction, so flipping array membership stays in sync with the
            // native input state.
            toggleEmail() { this._toggleChannel("email"); },
            toggleWhatsapp() { this._toggleChannel("whatsapp"); },
            togglePush() { this._toggleChannel("push"); },
            _toggleChannel(name) {
                var i = this.channels.indexOf(name);
                if (i === -1) this.channels.push(name);
                else this.channels.splice(i, 1);
            },
            selectDraft() { this.sendMode = "draft"; },
            selectSendNow() { this.sendMode = "now"; },
            selectSchedule() { this.sendMode = "schedule"; },
        };
    });
});
