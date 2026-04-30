/**
 * Alpine.js CSP-Compatible Components for Crush.lu
 *
 * The Alpine.js CSP build cannot interpret inline method calls or complex expressions.
 * All interactive components must be registered with Alpine.data() to work properly.
 *
 * IMPORTANT: The CSP build does NOT support:
 * - Inline JavaScript expressions like @click="count++" or @click="toggle()"
 * - Passing parameters to Alpine.data() from x-data attributes
 *
 * Instead, use data attributes to pass initial values and read them in init().
 */

document.addEventListener("alpine:init", function () {
    // Event ticket "Add to Google Wallet" button component
    Alpine.data("eventTicketButton", function () {
        return {
            loading: false,
            error: false,
            errorMsg: "",

            get isLoading() {
                return this.loading;
            },
            get hasError() {
                return this.error;
            },
            get errorMessage() {
                return this.errorMsg;
            },

            saveToWallet: function () {
                var self = this;
                var regId = this.$el
                    .closest("[data-registration-id]")
                    .getAttribute("data-registration-id");
                if (!regId) return;

                self.loading = true;
                self.error = false;
                self.errorMsg = "";

                fetch("/wallet/google/event-ticket/" + regId + "/jwt/")
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        self.loading = false;
                        if (data.jwt) {
                            window.open(
                                "https://pay.google.com/gp/v/save/" + data.jwt,
                                "_blank",
                            );
                        } else {
                            self.error = true;
                            self.errorMsg = data.error || "Failed to generate ticket.";
                        }
                    })
                    .catch(function (err) {
                        self.loading = false;
                        self.error = true;
                        self.errorMsg = "Network error. Please try again.";
                    });
            },
        };
    });

    // =========================================================================
    // MOBILE NATIVE APP NAVIGATION COMPONENTS
    // =========================================================================

    // Bottom navigation bar - 4 tabs (5 for coaches) fixed at bottom on mobile
    Alpine.data("bottomNav", function () {
        return {
            currentPath: "",
            eventsCount: 0,
            requestsCount: 0,
            screeningCount: 0,

            init: function () {
                // Strip i18n language prefix (e.g. /en/, /de/, /fr/) for route matching
                var path = window.location.pathname;
                var langPrefix = path.match(/^\/[a-z]{2}(?:-[a-z]{2})?\//);
                this.currentPath = langPrefix
                    ? path.substring(langPrefix[0].length - 1)
                    : path;
                this.eventsCount = parseInt(this.$el.dataset.eventsCount || "0", 10);
                this.requestsCount = parseInt(
                    this.$el.dataset.requestsCount || "0",
                    10,
                );
                this.screeningCount = parseInt(
                    this.$el.dataset.screeningCount || "0",
                    10,
                );
            },

            get isHomeActive() {
                return (
                    this.currentPath === "/dashboard/" ||
                    this.currentPath.indexOf("/dashboard/") === 0
                );
            },
            get isEventsActive() {
                return (
                    this.currentPath === "/events/" ||
                    this.currentPath.indexOf("/events/") === 0
                );
            },
            get isConnectionsActive() {
                return (
                    this.currentPath === "/connections/" ||
                    this.currentPath.indexOf("/connections/") === 0
                );
            },
            get isProfileActive() {
                return this.currentPath.indexOf("/profile/") === 0;
            },
            get isCoachActive() {
                return this.currentPath.indexOf("/coach/") === 0;
            },

            get homeActiveClass() {
                return this.isHomeActive ? "bottom-nav-item-active" : "";
            },
            get eventsActiveClass() {
                return this.isEventsActive ? "bottom-nav-item-active" : "";
            },
            get connectionsActiveClass() {
                return this.isConnectionsActive ? "bottom-nav-item-active" : "";
            },
            get profileActiveClass() {
                return this.isProfileActive ? "bottom-nav-item-active" : "";
            },
            get coachActiveClass() {
                return this.isCoachActive ? "bottom-nav-item-active" : "";
            },

            get hasEventsBadge() {
                return this.eventsCount > 0;
            },
            get hasRequestsBadge() {
                return this.requestsCount > 0;
            },
            get hasScreeningBadge() {
                return this.screeningCount > 0;
            },

            handleTap: function () {
                // Haptic feedback (silent no-op where unsupported)
                if (navigator.vibrate) {
                    navigator.vibrate(10);
                }
            },

            scrollToTop: function () {
                window.scrollTo({ top: 0, behavior: "smooth" });
            },
        };
    });

    // Slim mobile top bar - logo or back arrow + title
    Alpine.data("topBarMobile", function () {
        return {
            pageTitle: "",
            notificationCount: 0,

            init: function () {
                // Read page title from meta tag set by {% block mobile_page_title %}
                var meta = document.querySelector('meta[name="mobile-page-title"]');
                this.pageTitle = meta ? meta.getAttribute("content") : "";
                this.notificationCount = parseInt(
                    this.$el.dataset.notificationCount || "0",
                    10,
                );
            },

            get showBackButton() {
                return this.pageTitle !== "";
            },
            get showLogo() {
                return this.pageTitle === "";
            },
            get hasNotifications() {
                return this.notificationCount > 0;
            },

            goBack: function () {
                if (navigator.vibrate) {
                    navigator.vibrate(10);
                }
                window.history.back();
            },
        };
    });

    // Page transition controller - manages slide direction for View Transitions API
    // Works with HTMX globalViewTransitions: true (set in base.html meta config)
    // Sets data-transition attribute on <html> to control CSS animations
    Alpine.data("pageTransition", function () {
        return {
            historyStack: [],

            init: function () {
                var self = this;
                // Record initial page in history stack
                this.historyStack.push(window.location.pathname);

                // Listen for HTMX before-swap to set transition direction
                document.addEventListener("htmx:beforeSwap", function (evt) {
                    var trigger = evt.detail.requestConfig
                        ? evt.detail.requestConfig.triggeringElement
                        : null;
                    if (!trigger) return;

                    // Check data-transition on the triggering element
                    var transitionType = trigger.dataset.transition;

                    if (transitionType === "none") {
                        // Tab switch: no animation
                        document.documentElement.setAttribute(
                            "data-transition",
                            "none",
                        );
                    } else if (transitionType === "slide-back") {
                        // Explicit back navigation
                        document.documentElement.setAttribute(
                            "data-transition",
                            "slide-back",
                        );
                        self.historyStack.pop();
                    } else {
                        // Default: forward slide for drill-down links
                        document.documentElement.setAttribute(
                            "data-transition",
                            "slide-forward",
                        );
                        var href = trigger.getAttribute("href");
                        if (href) {
                            self.historyStack.push(href);
                        }
                    }
                });

                // Listen for browser back/forward button (popstate)
                window.addEventListener("popstate", function () {
                    document.documentElement.setAttribute(
                        "data-transition",
                        "slide-back",
                    );
                });

                // Clean up transition attribute after transition completes
                document.addEventListener("htmx:afterSettle", function () {
                    // Small delay to let the view transition finish
                    setTimeout(function () {
                        document.documentElement.removeAttribute("data-transition");
                    }, 300);
                });
            },
        };
    });

    // =========================================================================
    // SHARED FORM COMPONENTS (Phase 4)
    // =========================================================================

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

            // WebSocket state
            ws: null,
            connected: false,
            reconnectAttempts: 0,
            eventId: 0,

            // Toast state
            toasts: [],
            toastCounter: 0,

            // Deduplication
            processedIds: {},

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
                          (i18n.coach || "Coach") +
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
                        (i18n.unverified || "Unverified Profile") +
                        "</div>" +
                        (coachLine || statusLine
                            ? "<div>" + coachLine + statusLine + "</div>"
                            : "") +
                        "</div>";
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
                    warningHtml;
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
                    self.reconnectAttempts = 0;
                };
                this.ws.onclose = function () {
                    self.connected = false;
                    if (self.reconnectAttempts >= 20) return;
                    var delay = Math.min(
                        1000 * Math.pow(2, self.reconnectAttempts),
                        30000,
                    );
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
                if (this.processedIds[regId]) return;
                this.processedIds[regId] = true;

                this.showProfileToast(data);
                this._updateCheckinUI(data);
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

            // --- Shared UI update logic ---
            _updateCheckinUI: function (data) {
                var regId = data.registration_id;
                if (data.already_checked_in) return;

                // Add to recent checkins list
                this.checkins.unshift({
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

                // Update attended counter
                var counter = document.getElementById("attended-count");
                if (counter) {
                    counter.textContent = parseInt(counter.textContent) + 1;
                }

                // Update table fill display
                if (data.table_number) {
                    var fillEl = document.getElementById(
                        "table-fill-" + data.table_number,
                    );
                    if (fillEl) {
                        fillEl.textContent = parseInt(fillEl.textContent) + 1;
                    }
                }

                // Update manual check-in row
                var row = document.getElementById("manual-reg-" + regId);
                if (row) {
                    var circle = row.querySelector(
                        ".border-gray-300, .border-gray-600",
                    );
                    if (circle) {
                        circle.outerHTML =
                            '<svg class="w-5 h-5 text-green-500 shrink-0" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>';
                    }
                    var btn = row.querySelector(".manual-checkin-btn");
                    if (btn) {
                        var tableBadge = data.table_number
                            ? ' <span class="inline-flex items-center rounded-full bg-crush-purple/10 px-2 py-0.5 text-xs font-medium text-crush-purple dark:text-purple-300">T' +
                              data.table_number +
                              "</span>"
                            : "";
                        var i18n = window._checkinI18n || {};
                        btn.outerHTML =
                            '<div class="flex items-center gap-2"><span class="px-3 py-1.5 text-xs font-medium text-green-600 dark:text-green-400">' +
                            (i18n.checkedIn || "Checked In") +
                            "</span>" +
                            tableBadge +
                            "</div>";
                    }
                }
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
                if (!readerEl) return;
                readerEl.style.display = "block";

                if (typeof Html5Qrcode === "undefined") {
                    self.result = true;
                    self.errorState = true;
                    self.message = "QR scanner library not loaded.";
                    return;
                }

                self.scanner = new Html5Qrcode("qr-reader");
                self.scanner
                    .start(
                        { facingMode: "environment" },
                        { fps: 10, qrbox: { width: 250, height: 250 } },
                        function (decodedText) {
                            self.handleScan(decodedText);
                        },
                        function () {},
                    )
                    .then(function () {
                        self.scannerActive = true;
                    })
                    .catch(function (err) {
                        self.result = true;
                        self.errorState = true;
                        var errStr = String(err);
                        if (
                            errStr.indexOf("NotAllowedError") !== -1 ||
                            errStr.indexOf("Permission") !== -1
                        ) {
                            self.message =
                                "Camera permission denied. Please allow camera access in your browser settings (click the lock icon in the address bar) and try again.";
                        } else if (
                            errStr.indexOf("NotFoundError") !== -1 ||
                            errStr.indexOf("DevicesNotFound") !== -1
                        ) {
                            self.message = "No camera found on this device.";
                        } else if (
                            errStr.indexOf("NotReadableError") !== -1 ||
                            errStr.indexOf("TrackStartError") !== -1
                        ) {
                            self.message =
                                "Camera is in use by another application. Please close other apps using the camera and try again.";
                        } else {
                            self.message = "Could not start camera: " + errStr;
                        }
                        readerEl.style.display = "none";
                    });
            },

            stopScanner: function () {
                var self = this;
                if (self.scanner) {
                    self.scanner
                        .stop()
                        .then(function () {
                            self.scannerActive = false;
                            var readerEl = document.getElementById("qr-reader");
                            if (readerEl) readerEl.style.display = "none";
                        })
                        .catch(function () {
                            self.scannerActive = false;
                        });
                }
            },

            handleScan: function (url) {
                var self = this;
                if (self.scanner) {
                    self.scanner.pause(true);
                }

                // Pre-mark registration as processed to prevent WebSocket duplicate
                // URL format: /api/events/checkin/<reg_id>/<token>/
                var urlMatch = url.match(/\/checkin\/(\d+)\//);
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
                            self._updateCheckinUI(data);
                        } else {
                            self.success = false;
                            self.errorState = true;
                            self.message = data.error || "Check-in failed.";
                        }
                        setTimeout(function () {
                            if (self.scanner && self.scannerActive) {
                                try {
                                    self.scanner.resume();
                                } catch (e) {}
                            }
                        }, 2000);
                    })
                    .catch(function () {
                        self.result = true;
                        self.success = false;
                        self.errorState = true;
                        self.message = "Network error or invalid QR code.";
                        setTimeout(function () {
                            if (self.scanner && self.scannerActive) {
                                try {
                                    self.scanner.resume();
                                } catch (e) {}
                            }
                        }, 2000);
                    });
            },

            // --- Manual check-in ---
            manualCheckin: function (evt) {
                var self = this;
                var btn = evt.currentTarget;
                var url = btn.getAttribute("data-checkin-url");
                var regId = btn.getAttribute("data-reg-id");
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
                            self._updateCheckinUI(data);
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
        };
    });

    // Calendar dropdown component
    Alpine.data("calendarDropdown", function () {
        return {
            open: false,

            get isOpen() {
                return this.open;
            },
            get isClosed() {
                return !this.open;
            },

            toggle() {
                this.open = !this.open;
            },

            close() {
                this.open = false;
            },
        };
    });

    // Navbar component with dropdowns and mobile menu
    Alpine.data("navbar", function () {
        return {
            mobileMenuOpen: false,
            coachToolsOpen: false,
            myCrushOpen: false,
            coachProfileOpen: false,
            eventsOpen: false,
            userMenuOpen: false,

            init: function () {
                // Close all dropdowns on Escape key
                document.addEventListener("keydown", (e) => {
                    if (e.key === "Escape") {
                        this.closeAllDropdowns();
                    }
                });
            },

            // Computed getters for CSP compatibility (avoid inline expressions)
            get mobileMenuClosed() {
                return !this.mobileMenuOpen;
            },
            get mobileMenuAriaExpanded() {
                return this.mobileMenuOpen ? "true" : "false";
            },
            get coachToolsAriaExpanded() {
                return this.coachToolsOpen ? "true" : "false";
            },
            get myCrushAriaExpanded() {
                return this.myCrushOpen ? "true" : "false";
            },
            get coachProfileAriaExpanded() {
                return this.coachProfileOpen ? "true" : "false";
            },
            get eventsAriaExpanded() {
                return this.eventsOpen ? "true" : "false";
            },
            get userMenuAriaExpanded() {
                return this.userMenuOpen ? "true" : "false";
            },

            toggleMobile: function () {
                this.mobileMenuOpen = !this.mobileMenuOpen;
            },
            toggleCoachTools: function () {
                this.coachToolsOpen = !this.coachToolsOpen;
            },
            toggleMyCrush: function () {
                this.myCrushOpen = !this.myCrushOpen;
            },
            toggleCoachProfile: function () {
                this.coachProfileOpen = !this.coachProfileOpen;
            },
            toggleEvents: function () {
                this.eventsOpen = !this.eventsOpen;
            },
            toggleUserMenu: function () {
                this.userMenuOpen = !this.userMenuOpen;
            },
            closeCoachTools: function () {
                this.coachToolsOpen = false;
            },
            closeMyCrush: function () {
                this.myCrushOpen = false;
            },
            closeCoachProfile: function () {
                this.coachProfileOpen = false;
            },
            closeEvents: function () {
                this.eventsOpen = false;
            },
            closeUserMenu: function () {
                this.userMenuOpen = false;
            },
            closeAllDropdowns: function () {
                this.mobileMenuOpen = false;
                this.coachToolsOpen = false;
                this.myCrushOpen = false;
                this.coachProfileOpen = false;
                this.eventsOpen = false;
                this.userMenuOpen = false;
            },
        };
    });

    // Profile progress dropdown for incomplete profiles in navbar
    Alpine.data("profileProgress", function () {
        return {
            isOpen: false,

            // CSP-compatible computed getters
            get isClosed() {
                return !this.isOpen;
            },
            get ariaExpanded() {
                return this.isOpen ? "true" : "false";
            },

            toggle: function () {
                this.isOpen = !this.isOpen;
            },
            close: function () {
                this.isOpen = false;
            },
        };
    });

    // Dismissible alert/message component
    Alpine.data("dismissible", function () {
        return {
            show: true,
            dismiss: function () {
                this.show = false;
            },
        };
    });

    // Tab navigation component (for auth page)
    // Reads initial tab from data-initial-tab attribute
    Alpine.data("tabNav", function () {
        return {
            activeTab: "login",

            // Computed getters for CSP compatibility
            get isLoginTab() {
                return this.activeTab === "login";
            },
            get isSignupTab() {
                return this.activeTab === "signup";
            },
            get loginTabClass() {
                return this.activeTab === "login"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-900 bg-white/50 hover:bg-white/80";
            },
            get signupTabClass() {
                return this.activeTab === "signup"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-900 bg-white/50 hover:bg-white/80";
            },

            init: function () {
                // Read initial tab from data attribute
                var initialTab = this.$el.getAttribute("data-initial-tab");
                if (initialTab) {
                    this.activeTab = initialTab;
                }
            },
            setLogin: function () {
                this.activeTab = "login";
            },
            setSignup: function () {
                this.activeTab = "signup";
            },
        };
    });

    // Event list tabs (upcoming / past)
    // Hero ghost eye-tracker.
    // Finds the two <g class="ghost-eye"> wrappers inside the hero ghost SVG
    // and continuously eases their SVG `transform` attribute toward the cursor
    // via a requestAnimationFrame lerp loop. mousemove only updates the target;
    // a single rAF loop interpolates current → target each frame, which is much
    // smoother than triggering a CSS transition on every mouse event.
    // Disabled on `(any-pointer: coarse)` only setups and on prefers-reduced-motion.
    Alpine.data("ghostEyes", function () {
        return {
            init: function () {
                var fineCursor = window.matchMedia("(any-pointer: fine)").matches;
                var reducedMotion = window.matchMedia(
                    "(prefers-reduced-motion: reduce)",
                ).matches;
                if (!fineCursor || reducedMotion) return;

                var hero = this.$el;
                var eyes = hero.querySelectorAll(".ghost-eye");
                var heart = hero.querySelector(".ghost-heart");
                if (!eyes.length && !heart) return;

                // Heart's resting position in the SVG (matches the original
                // transform="translate(248,120)" on the .ghost-heart element).
                // JS rewrites this transform every frame as base + offset; the
                // heartbeat SMIL on the same element uses additive="sum" so
                // its scale rides on top of our translate without clobbering it.
                var heartBaseX = 248,
                    heartBaseY = 120;

                // Eyes drift subtly; heart drifts much more so it visibly
                // follows the cursor and the eyes appear to chase it.
                var eyeOffset = 11; // SVG user units (~16 CSS px)
                var heartOffset = 130; // SVG user units — heart drifts well outside the ghost silhouette
                var falloffRange = 320; // px from hero center → full deflection
                var eyeEase = 0.22; // pupils react quickly
                var heartEase = 0.08; // heart drifts lazily, like it's drawn along

                var targetUx = 0,
                    targetUy = 0; // unit vector × falloff (shared direction)
                var eyeX = 0,
                    eyeY = 0;
                var heartX = 0,
                    heartY = 0;
                var self = this;

                this._handler = function (event) {
                    var rect = hero.getBoundingClientRect();
                    var cx = rect.left + rect.width / 2;
                    var cy = rect.top + rect.height / 2;
                    var dx = event.clientX - cx;
                    var dy = event.clientY - cy;
                    var dist = Math.sqrt(dx * dx + dy * dy) || 1;
                    var falloff = Math.min(1, dist / falloffRange);
                    targetUx = (dx / dist) * falloff;
                    targetUy = (dy / dist) * falloff;
                };

                var tick = function () {
                    var targetEyeX = targetUx * eyeOffset;
                    var targetEyeY = targetUy * eyeOffset;
                    var targetHeartX = targetUx * heartOffset;
                    var targetHeartY = targetUy * heartOffset;

                    eyeX += (targetEyeX - eyeX) * eyeEase;
                    eyeY += (targetEyeY - eyeY) * eyeEase;
                    heartX += (targetHeartX - heartX) * heartEase;
                    heartY += (targetHeartY - heartY) * heartEase;

                    var eyeT =
                        "translate(" + eyeX.toFixed(2) + " " + eyeY.toFixed(2) + ")";
                    for (var i = 0; i < eyes.length; i++) {
                        eyes[i].setAttribute("transform", eyeT);
                    }
                    if (heart) {
                        heart.setAttribute(
                            "transform",
                            "translate(" +
                                (heartBaseX + heartX).toFixed(2) +
                                " " +
                                (heartBaseY + heartY).toFixed(2) +
                                ")",
                        );
                    }
                    self._raf = requestAnimationFrame(tick);
                };

                window.addEventListener("mousemove", this._handler, {
                    passive: true,
                });
                this._raf = requestAnimationFrame(tick);
            },
            destroy: function () {
                if (this._handler) {
                    window.removeEventListener("mousemove", this._handler);
                }
                if (this._raf) {
                    cancelAnimationFrame(this._raf);
                }
            },
        };
    });

    Alpine.data("eventTabs", function () {
        return {
            activeTab: "upcoming",

            get isUpcoming() {
                return this.activeTab === "upcoming";
            },
            get isPast() {
                return this.activeTab === "past";
            },
            get upcomingTabClass() {
                return this.activeTab === "upcoming"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },
            get pastTabClass() {
                return this.activeTab === "past"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },

            showUpcoming() {
                this.activeTab = "upcoming";
            },
            showPast() {
                this.activeTab = "past";
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

    // Email preferences component (account settings)
    // Reads initial unsubscribe state from data-unsubscribed attribute
    Alpine.data("emailPreferences", function () {
        return {
            unsubscribeAll: false,
            saving: false,
            saveSuccess: false,
            saveError: false,

            get unsubscribeAllClass() {
                return this.unsubscribeAll ? "opacity-50 pointer-events-none" : "";
            },

            get showSaving() {
                return this.saving;
            },

            get showSuccess() {
                return this.saveSuccess;
            },

            get showError() {
                return this.saveError;
            },

            get showIdleMessage() {
                return !this.saving && !this.saveSuccess && !this.saveError;
            },

            init: function () {
                var self = this;
                var unsubscribed = this.$el.getAttribute("data-unsubscribed");
                this.unsubscribeAll = unsubscribed === "true";

                // Event delegation for all email preference toggles
                this.$el.addEventListener("change", function (event) {
                    if (event.target.classList.contains("email-pref-toggle")) {
                        var prefKey = event.target.dataset.prefKey;
                        self.updatePreference(
                            prefKey,
                            event.target.checked,
                            event.target,
                        );
                    }
                });
            },
            toggleUnsubscribe: function () {
                this.unsubscribeAll = !this.unsubscribeAll;
                this.updatePreference("unsubscribed_all", this.unsubscribeAll, null);
            },
            getCsrfToken: function () {
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                var cookie = document.cookie.split("; ").find(function (row) {
                    return row.startsWith("csrftoken=");
                });
                return cookie ? cookie.split("=")[1] : "";
            },
            updatePreference: function (key, value, checkbox) {
                var self = this;
                self.saving = true;
                self.saveSuccess = false;
                self.saveError = false;

                fetch("/api/email/preferences/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify({ key: key, value: value }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        self.saving = false;
                        if (data.success) {
                            self.saveSuccess = true;
                            setTimeout(function () {
                                self.saveSuccess = false;
                            }, 2000);
                        } else {
                            self.saveError = true;
                            // Revert on error
                            if (checkbox) {
                                checkbox.checked = !value;
                            }
                            setTimeout(function () {
                                self.saveError = false;
                            }, 3000);
                        }
                    })
                    .catch(function () {
                        self.saving = false;
                        self.saveError = true;
                        // Revert on network error
                        if (checkbox) {
                            checkbox.checked = !value;
                        }
                        setTimeout(function () {
                            self.saveError = false;
                        }, 3000);
                    });
            },
        };
    });

    Alpine.data("profileSectionAutosave", function () {
        return {
            saveUrl: "",
            section: "",
            saving: false,
            saveSuccess: false,
            saveError: false,
            fieldErrors: {},
            nonFieldErrors: [],
            lastSavedData: {},
            _debounceTimer: null,
            _activeController: null,

            init: function () {
                var self = this;
                this.saveUrl = this.$el.getAttribute("data-save-url") || "";
                this.section = this.$el.getAttribute("data-section") || "";
                this.lastSavedData = this.serializeForm();

                this.$el.addEventListener("input", function (event) {
                    if (!self._shouldHandleTarget(event.target)) {
                        return;
                    }

                    if (self._isDebouncedField(event.target)) {
                        self.scheduleSave(event.target, 900);
                    } else if (event.target.type === "range") {
                        self.scheduleSave(event.target, 250);
                    }
                });

                this.$el.addEventListener("change", function (event) {
                    if (!self._shouldHandleTarget(event.target)) {
                        return;
                    }

                    if (event.target.type === "range") {
                        self.save(event.target);
                        return;
                    }

                    if (!self._isDebouncedField(event.target)) {
                        self.save(event.target);
                    }
                });

                this.$el.addEventListener("profile-autosave:trigger", function () {
                    self.save(null);
                });
            },

            get showSaving() {
                return this.saving;
            },

            get showSuccess() {
                return this.saveSuccess;
            },

            get showError() {
                return this.saveError;
            },

            get showIdleMessage() {
                return !this.saving && !this.saveSuccess && !this.saveError;
            },

            scheduleSave: function (trigger, wait) {
                var self = this;
                clearTimeout(this._debounceTimer);
                this._debounceTimer = setTimeout(function () {
                    self.save(trigger);
                }, wait || 800);
            },

            save: function (trigger) {
                var self = this;
                var payload;

                if (!this.saveUrl || !this.section) {
                    return;
                }

                clearTimeout(this._debounceTimer);

                payload = this.serializeForm();
                payload.section = this.section;

                if (this._activeController) {
                    this._activeController.abort();
                }

                this._activeController = new AbortController();
                this.saving = true;
                this.saveSuccess = false;
                this.saveError = false;

                fetch(this.saveUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": this.getCsrfToken(),
                    },
                    body: JSON.stringify(payload),
                    signal: this._activeController.signal,
                })
                    .then(function (response) {
                        return response.json().then(function (data) {
                            return { ok: response.ok, data: data };
                        });
                    })
                    .then(function (result) {
                        self.saving = false;

                        if (result.ok && result.data.success) {
                            self._activeController = null;
                            self.lastSavedData = self.serializeForm();
                            self.clearErrors();
                            self.saveSuccess = true;
                            setTimeout(function () {
                                self.saveSuccess = false;
                            }, 1800);
                            return;
                        }

                        self._activeController = null;
                        self.saveError = true;
                        self.applyErrors(
                            result.data.errors || {},
                            result.data.non_field_errors || [],
                        );
                        self.revertControlIfNeeded(trigger);
                        setTimeout(function () {
                            self.saveError = false;
                        }, 3000);
                    })
                    .catch(function (error) {
                        if (error && error.name === "AbortError") {
                            return;
                        }

                        self.saving = false;
                        self.saveError = true;
                        self.revertControlIfNeeded(trigger);
                        setTimeout(function () {
                            self.saveError = false;
                        }, 3000);
                    });
            },

            serializeForm: function () {
                var formData = new FormData(this.$el);
                var data = {};
                var checkboxGroups = {};
                var inputs;
                var i;

                formData.forEach(function (value, key) {
                    if (Object.prototype.hasOwnProperty.call(data, key)) {
                        if (!Array.isArray(data[key])) {
                            data[key] = [data[key]];
                        }
                        data[key].push(value);
                    } else {
                        data[key] = value;
                    }
                });

                inputs = this.$el.querySelectorAll("input[type='checkbox'][name]");
                for (i = 0; i < inputs.length; i++) {
                    checkboxGroups[inputs[i].name] =
                        (checkboxGroups[inputs[i].name] || 0) + 1;
                }

                Object.keys(checkboxGroups).forEach(function (name) {
                    if (!Object.prototype.hasOwnProperty.call(data, name)) {
                        data[name] = checkboxGroups[name] > 1 ? [] : false;
                    } else if (checkboxGroups[name] === 1) {
                        data[name] = data[name] === true || data[name] === "on";
                    }
                });

                return data;
            },

            applyErrors: function (fieldErrors, nonFieldErrors) {
                var self = this;
                this.fieldErrors = fieldErrors || {};
                this.nonFieldErrors = nonFieldErrors || [];

                this.$el.querySelectorAll("[data-error-for]").forEach(function (node) {
                    var field = node.getAttribute("data-error-for");
                    var messages = self.fieldErrors[field] || [];
                    // Find or create a dedicated text span to avoid destroying child nodes (e.g. SVG icons)
                    var span = node.querySelector("[data-error-text]");
                    if (!span) {
                        span = document.createElement("span");
                        span.setAttribute("data-error-text", "");
                        node.appendChild(span);
                    }
                    span.textContent = messages.length ? messages[0] : "";
                    node.classList.toggle("hidden", messages.length === 0);
                });

                this.$el
                    .querySelectorAll("[data-non-field-errors]")
                    .forEach(function (node) {
                        node.textContent = "";
                        if (self.nonFieldErrors.length) {
                            self.nonFieldErrors.forEach(function (message) {
                                var p = document.createElement("p");
                                p.textContent = message;
                                node.appendChild(p);
                            });
                            node.classList.remove("hidden");
                        } else {
                            node.classList.add("hidden");
                        }
                    });
            },

            clearErrors: function () {
                this.applyErrors({}, []);
            },

            revertControlIfNeeded: function (trigger) {
                var savedValue;
                var groupValues;
                var radios;

                if (!trigger || !trigger.name) {
                    return;
                }

                savedValue = this.lastSavedData[trigger.name];

                if (trigger.type === "checkbox") {
                    groupValues = Array.isArray(savedValue) ? savedValue : null;

                    if (groupValues) {
                        this.$el
                            .querySelectorAll(
                                "input[type='checkbox'][name='" + trigger.name + "']",
                            )
                            .forEach(function (checkbox) {
                                checkbox.checked =
                                    groupValues.indexOf(checkbox.value) !== -1;
                            });
                        return;
                    }

                    trigger.checked = !!savedValue;
                    return;
                }

                if (trigger.type === "radio") {
                    radios = this.$el.querySelectorAll(
                        "input[type='radio'][name='" + trigger.name + "']",
                    );
                    radios.forEach(function (radio) {
                        radio.checked = radio.value === savedValue;
                    });
                    return;
                }

                if (savedValue !== undefined) {
                    trigger.value = savedValue;
                }
            },

            _shouldHandleTarget: function (target) {
                return (
                    target &&
                    target.form === this.$el &&
                    target.name &&
                    target.type !== "file"
                );
            },

            _isDebouncedField: function (target) {
                return (
                    target.tagName === "TEXTAREA" ||
                    target.type === "text" ||
                    target.type === "tel"
                );
            },

            getCsrfToken: function () {
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                var cookie = document.cookie.split("; ").find(function (row) {
                    return row.startsWith("csrftoken=");
                });
                return cookie ? cookie.split("=")[1] : "";
            },
        };
    });

    // Push notification preferences component (account settings)
    // Handles both enabling push and managing preferences
    // Uses event delegation for CSP compliance - no inline event handlers
    Alpine.data("pushPreferences", function () {
        return {
            subscriptions: [],
            isSupported: false,
            isSubscribed: false,
            isCurrentDeviceSubscribed: false, // TRUE only if THIS device has push enabled
            isEnabling: false,
            isDisabling: false,
            errorMessage: "",
            permissionDenied: false,
            isLoading: true,
            currentEndpoint: null, // For identifying "This device" by endpoint
            currentFingerprint: null, // Stable device fingerprint (fallback for endpoint)
            endpointDetected: false, // Flag to trigger re-render when endpoint is detected
            subscriptionHealth: {}, // Map of subscription ID -> health status
            checkingHealth: false, // True while checking health
            // i18n strings for time formatting (loaded from data attributes in init)
            i18n: {
                neverUsed: "Never used",
                justNow: "Just now",
                lastActive: "Last active:",
                minutesAgo: "m ago",
                hoursAgo: "h ago",
                daysAgo: "d ago",
                monthsAgo: "mo ago",
                checking: "Checking...",
                checkSubscriptionHealth: "Check Subscription Health",
            },

            // Computed getters for CSP compatibility
            get hasSubscriptions() {
                return this.subscriptions.length > 0;
            },
            get noSubscriptions() {
                return this.subscriptions.length === 0;
            },
            get canEnable() {
                return (
                    this.isSupported &&
                    !this.isCurrentDeviceSubscribed &&
                    !this.isEnabling &&
                    !this.permissionDenied
                );
            },
            // Show enable button if current device is NOT subscribed (allows multi-device)
            get showEnableButton() {
                return (
                    !this.isLoading &&
                    this.isSupported &&
                    !this.isCurrentDeviceSubscribed &&
                    !this.permissionDenied
                );
            },
            get showPermissionDenied() {
                return !this.isLoading && this.permissionDenied;
            },
            get showNotSupported() {
                return !this.isLoading && !this.isSupported;
            },
            get showPreferences() {
                return !this.isLoading && this.isSubscribed && this.hasSubscriptions;
            },
            get showLoading() {
                return this.isLoading;
            },
            // Button text getters for CSP compatibility (replaces ternary expressions)
            get enableButtonText() {
                return this.isEnabling ? "Enabling..." : "Enable Push Notifications";
            },
            get disableButtonText() {
                return this.isDisabling ? "Disabling..." : "Disable";
            },
            get showEnablingIcon() {
                return this.isEnabling;
            },
            get showNotEnablingIcon() {
                return !this.isEnabling;
            },
            // Health check state getters (CSP-safe)
            get notCheckingHealth() {
                return !this.checkingHealth;
            },
            get checkHealthButtonText() {
                return this.checkingHealth
                    ? this.i18n.checking || "Checking..."
                    : this.i18n.checkSubscriptionHealth || "Check Subscription Health";
            },

            init: function () {
                var self = this;

                // Load i18n strings from data attributes
                this._loadI18nStrings();

                // Parse initial subscriptions from data attribute
                var data = this.$el.getAttribute("data-subscriptions");
                if (data) {
                    try {
                        this.subscriptions = JSON.parse(data);
                    } catch (e) {
                        console.error("[Push] Failed to parse subscriptions:", e);
                    }
                }

                // Check if push is supported directly (don't rely on CrushPush being loaded)
                // This is the same check as in push-notifications.js
                this.isSupported =
                    "serviceWorker" in navigator && "PushManager" in window;

                // Check if permission was denied
                if ("Notification" in window && Notification.permission === "denied") {
                    this.permissionDenied = true;
                }

                // Detect current device endpoint for "This device" badge
                this._detectCurrentEndpoint();

                // Wait for CrushPush to be available before checking subscription status
                this._waitForCrushPush(function () {
                    // Check current subscription status
                    if (
                        self.isSupported &&
                        window.CrushPush &&
                        window.CrushPush.isSubscribed
                    ) {
                        window.CrushPush.isSubscribed()
                            .then(function (subscribed) {
                                self.isSubscribed = subscribed;
                                self.isLoading = false;
                                // Re-run device detection after DOM is rendered
                                self.$nextTick(function () {
                                    self._retryDeviceMatch();
                                });
                            })
                            .catch(function () {
                                self.isLoading = false;
                                self.$nextTick(function () {
                                    self._retryDeviceMatch();
                                });
                            });
                    } else {
                        self.isLoading = false;
                        self.$nextTick(function () {
                            self._retryDeviceMatch();
                        });
                    }
                });

                // Event delegation for toggle changes
                this.$el.addEventListener("change", function (event) {
                    if (event.target.classList.contains("push-pref-toggle")) {
                        var subId = parseInt(event.target.dataset.subscriptionId);
                        var prefKey = event.target.dataset.prefKey;
                        self.updatePreference(
                            subId,
                            prefKey,
                            event.target.checked,
                            event.target,
                        );
                    }
                });

                // Event delegation for button clicks
                this.$el.addEventListener("click", function (event) {
                    if (event.target.closest(".enable-push-btn")) {
                        self.enablePush();
                    } else if (event.target.closest(".disable-push-btn")) {
                        // Get subscription info from the button's container
                        var btn = event.target.closest(".disable-push-btn");
                        var container = btn.closest("[data-subscription-id]");
                        if (container) {
                            var subscriptionId = container.dataset.subscriptionId;
                            var endpoint = container.dataset.endpoint;
                            // Check if this is the current device
                            if (
                                self.currentEndpoint &&
                                endpoint === self.currentEndpoint
                            ) {
                                self.disablePush(); // Current device - use existing unsubscribe
                            } else {
                                self.disableRemoteSubscription(subscriptionId); // Other device
                            }
                        } else {
                            // Fallback to current device unsubscribe
                            self.disablePush();
                        }
                    }
                });
            },

            // Load i18n strings from data attributes
            _loadI18nStrings: function () {
                var el = this.$el;
                this.i18n.neverUsed =
                    el.getAttribute("data-i18n-never-used") || this.i18n.neverUsed;
                this.i18n.justNow =
                    el.getAttribute("data-i18n-just-now") || this.i18n.justNow;
                this.i18n.lastActive =
                    el.getAttribute("data-i18n-last-active") || this.i18n.lastActive;
                this.i18n.minutesAgo =
                    el.getAttribute("data-i18n-minutes-ago") || this.i18n.minutesAgo;
                this.i18n.hoursAgo =
                    el.getAttribute("data-i18n-hours-ago") || this.i18n.hoursAgo;
                this.i18n.daysAgo =
                    el.getAttribute("data-i18n-days-ago") || this.i18n.daysAgo;
                this.i18n.monthsAgo =
                    el.getAttribute("data-i18n-months-ago") || this.i18n.monthsAgo;
                this.i18n.checking =
                    el.getAttribute("data-i18n-checking") || this.i18n.checking;
                this.i18n.checkSubscriptionHealth =
                    el.getAttribute("data-i18n-check-subscription-health") ||
                    this.i18n.checkSubscriptionHealth;
            },

            // Detect current device's push endpoint and fingerprint for "This device" identification
            // Uses endpoint as primary identifier, fingerprint as fallback
            // Sets isCurrentDeviceSubscribed based on whether THIS device is in the subscriptions list
            _detectCurrentEndpoint: function () {
                var self = this;

                // Generate fingerprint immediately using our own implementation
                // This ensures fingerprint is always available, regardless of CrushPush loading
                self.currentFingerprint = self._generateFingerprint();

                if ("serviceWorker" in navigator) {
                    navigator.serviceWorker.ready
                        .then(function (reg) {
                            return reg.pushManager.getSubscription();
                        })
                        .then(function (sub) {
                            self.currentEndpoint = sub ? sub.endpoint : null;
                            self.endpointDetected = true; // Trigger Alpine reactivity

                            // Strategy 1: Match by endpoint (most accurate when available)
                            if (sub && sub.endpoint) {
                                var subscriptionElements =
                                    self.$el.querySelectorAll("[data-endpoint]");
                                for (var i = 0; i < subscriptionElements.length; i++) {
                                    if (
                                        subscriptionElements[i].dataset.endpoint ===
                                        sub.endpoint
                                    ) {
                                        self.isCurrentDeviceSubscribed = true;
                                        self._showThisDeviceBadge(
                                            subscriptionElements[i],
                                        );
                                        break;
                                    }
                                }
                            }

                            // Strategy 2: Fallback to fingerprint if endpoint didn't match
                            if (
                                !self.isCurrentDeviceSubscribed &&
                                self.currentFingerprint
                            ) {
                                self._matchByFingerprint();
                            }
                        })
                        .catch(function () {
                            self.endpointDetected = true; // Mark as done even on failure
                            // Try fingerprint matching as fallback
                            if (self.currentFingerprint) {
                                self._matchByFingerprint();
                            }
                        });
                } else {
                    self.endpointDetected = true; // No service worker support
                    // Still try fingerprint matching
                    if (self.currentFingerprint) {
                        self._matchByFingerprint();
                    }
                }
            },

            // Match device by fingerprint (fallback when endpoint doesn't match)
            _matchByFingerprint: function () {
                var self = this;
                var subscriptionElements =
                    self.$el.querySelectorAll("[data-fingerprint]");
                for (var i = 0; i < subscriptionElements.length; i++) {
                    if (
                        subscriptionElements[i].dataset.fingerprint ===
                        self.currentFingerprint
                    ) {
                        self.isCurrentDeviceSubscribed = true;
                        self._showThisDeviceBadge(subscriptionElements[i]);
                        break;
                    }
                }
            },

            // Retry device matching after DOM is rendered (called after isLoading becomes false)
            // This handles the race condition where _detectCurrentEndpoint runs before
            // the subscription elements are rendered by Alpine's x-if
            _retryDeviceMatch: function () {
                var self = this;
                if (self.isCurrentDeviceSubscribed) return; // Already matched

                // Try endpoint matching first
                if (self.currentEndpoint) {
                    var subscriptionElements =
                        self.$el.querySelectorAll("[data-endpoint]");
                    for (var i = 0; i < subscriptionElements.length; i++) {
                        if (
                            subscriptionElements[i].dataset.endpoint ===
                            self.currentEndpoint
                        ) {
                            self.isCurrentDeviceSubscribed = true;
                            self._showThisDeviceBadge(subscriptionElements[i]);
                            return;
                        }
                    }
                }

                // Fall back to fingerprint matching
                if (self.currentFingerprint) {
                    self._matchByFingerprint();
                }
            },

            // Show "This device" badge for a subscription container
            _showThisDeviceBadge: function (container) {
                var badges = container.querySelectorAll(".this-device-badge");
                for (var i = 0; i < badges.length; i++) {
                    badges[i].classList.remove("hidden");
                }
            },

            // Generate device fingerprint independently (same algorithm as push-notifications.js)
            // This ensures fingerprint is always available even if CrushPush fails to load
            _generateFingerprint: function () {
                var components = [
                    screen.width,
                    screen.height,
                    window.devicePixelRatio || 1,
                    new Date().getTimezoneOffset(),
                    navigator.language || "",
                    navigator.platform || "",
                    navigator.hardwareConcurrency || 0,
                    navigator.deviceMemory || 0,
                    "ontouchstart" in window ? 1 : 0,
                    screen.colorDepth || 0,
                    this._getCanvasFingerprint(),
                ];
                return this._simpleHash(components.join("|"));
            },

            // Canvas-based fingerprint component (same as push-notifications.js)
            _getCanvasFingerprint: function () {
                try {
                    var canvas = document.createElement("canvas");
                    canvas.width = 200;
                    canvas.height = 50;
                    var ctx = canvas.getContext("2d");
                    ctx.textBaseline = "top";
                    ctx.font = "14px Arial";
                    ctx.fillStyle = "#f60";
                    ctx.fillRect(125, 1, 62, 20);
                    ctx.fillStyle = "#069";
                    ctx.fillText("Crush.lu PWA", 2, 15);
                    ctx.fillStyle = "rgba(102, 204, 0, 0.7)";
                    ctx.fillText("Crush.lu PWA", 4, 17);
                    return canvas.toDataURL().slice(-50);
                } catch (e) {
                    return "no-canvas";
                }
            },

            // Simple hash function (djb2 algorithm, same as push-notifications.js)
            _simpleHash: function (str) {
                var hash = 5381;
                for (var i = 0; i < str.length; i++) {
                    hash = (hash << 5) + hash + str.charCodeAt(i);
                    hash = hash & hash;
                }
                return Math.abs(hash).toString(16).padStart(8, "0");
            },

            // Check if a subscription is from the current device
            isCurrentDevice: function (subscription) {
                // Check by endpoint first, then fingerprint
                if (
                    this.currentEndpoint &&
                    subscription.endpoint === this.currentEndpoint
                ) {
                    return true;
                }
                if (
                    this.currentFingerprint &&
                    subscription.device_fingerprint === this.currentFingerprint
                ) {
                    return true;
                }
                return false;
            },

            // Format relative time for "Last active" display
            formatRelativeTime: function (dateStr) {
                if (!dateStr) return this.i18n.neverUsed;
                var date = new Date(dateStr);
                var now = new Date();
                var diffMs = now - date;
                var diffMins = Math.floor(diffMs / 60000);
                if (diffMins < 1) return this.i18n.justNow;
                if (diffMins < 60) return diffMins + " " + this.i18n.minutesAgo;
                var diffHours = Math.floor(diffMins / 60);
                if (diffHours < 24) return diffHours + " " + this.i18n.hoursAgo;
                var diffDays = Math.floor(diffHours / 24);
                if (diffDays < 30) return diffDays + " " + this.i18n.daysAgo;
                var diffMonths = Math.floor(diffDays / 30);
                return diffMonths + " " + this.i18n.monthsAgo;
            },

            enablePush: function () {
                var self = this;
                if (!this.isSupported || this.isEnabling) return;

                this.isEnabling = true;
                this.errorMessage = "";

                window.CrushPush.subscribe()
                    .then(function (result) {
                        self.isEnabling = false;
                        if (result.success) {
                            self.isSubscribed = true;
                            // Reload page to get fresh subscription data
                            window.location.reload();
                        } else {
                            if (result.error === "Permission denied") {
                                self.permissionDenied = true;
                            } else {
                                self.errorMessage =
                                    result.error || "Failed to enable notifications";
                            }
                        }
                    })
                    .catch(function (err) {
                        self.isEnabling = false;
                        self.errorMessage = "An error occurred. Please try again.";
                        console.error("[Push] Enable error:", err);
                    });
            },

            disablePush: function () {
                var self = this;
                if (!this.isSupported || this.isDisabling) return;

                this.isDisabling = true;

                window.CrushPush.unsubscribe()
                    .then(function (result) {
                        self.isDisabling = false;
                        if (result.success) {
                            self.isSubscribed = false;
                            self.isCurrentDeviceSubscribed = false;
                            self.subscriptions = [];
                            // Reload to update UI
                            window.location.reload();
                        } else {
                            self.errorMessage =
                                result.error || "Failed to disable notifications";
                        }
                    })
                    .catch(function (err) {
                        self.isDisabling = false;
                        self.errorMessage = "An error occurred. Please try again.";
                        console.error("[Push] Disable error:", err);
                    });
            },

            // Disable push subscription for a remote device (not the current device)
            disableRemoteSubscription: function (subscriptionId) {
                var self = this;
                if (this.isDisabling) return;

                this.isDisabling = true;

                fetch("/api/push/delete-subscription/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify({ subscription_id: subscriptionId }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        self.isDisabling = false;
                        if (data.success) {
                            // Reload to update UI
                            window.location.reload();
                        } else {
                            self.errorMessage =
                                data.error || "Failed to disable notifications";
                        }
                    })
                    .catch(function (err) {
                        self.isDisabling = false;
                        self.errorMessage = "An error occurred. Please try again.";
                        console.error("[Push] Remote disable error:", err);
                    });
            },

            updatePreference: function (subscriptionId, prefKey, value, checkbox) {
                var self = this;
                var preferences = {};
                preferences[prefKey] = value;

                fetch("/api/push/preferences/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        subscriptionId: subscriptionId,
                        preferences: preferences,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (!data.success) {
                            // Revert checkbox on error
                            checkbox.checked = !value;
                            console.error(
                                "[Push] Failed to update preference:",
                                data.error,
                            );
                        }
                    })
                    .catch(function (err) {
                        // Revert checkbox on network error
                        checkbox.checked = !value;
                        console.error("[Push] Network error:", err);
                    });
            },

            getCsrfToken: function () {
                // First try hidden form input (works with CSRF_COOKIE_HTTPONLY=True)
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                // Fallback to cookie
                var cookie = document.cookie.split("; ").find(function (row) {
                    return row.startsWith("csrftoken=");
                });
                return cookie ? cookie.split("=")[1] : "";
            },

            // Wait for CrushPush to be available (handles script load timing)
            _waitForCrushPush: function (callback) {
                var self = this;
                var maxAttempts = 20; // 2 seconds max
                var attempts = 0;

                function check() {
                    attempts++;
                    if (window.CrushPush) {
                        callback();
                    } else if (attempts < maxAttempts) {
                        setTimeout(check, 100);
                    } else {
                        // CrushPush never loaded (push not supported or script error)
                        self.isLoading = false;
                    }
                }

                check();
            },

            // Check health of all subscriptions
            checkAllSubscriptionsHealth: function () {
                var self = this;
                self.checkingHealth = true;

                // Initialize subscriptionHealth if not exists
                if (!self.subscriptionHealth) {
                    self.subscriptionHealth = {};
                }

                var promises = self.subscriptions.map(function (sub) {
                    return fetch("/api/push/validate-subscription/", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-CSRFToken": self.getCsrfToken(),
                        },
                        body: JSON.stringify({ endpoint: sub.endpoint }),
                    })
                        .then(function (response) {
                            if (response.ok) {
                                return response.json();
                            }
                            throw new Error("Health check failed");
                        })
                        .then(function (data) {
                            self.subscriptionHealth[sub.id] = {
                                valid: data.valid,
                                warning: data.warning,
                                reason: data.reason,
                                age_days: data.age_days,
                            };
                        })
                        .catch(function (error) {
                            console.error(
                                "Health check failed for subscription",
                                sub.id,
                                error,
                            );
                            self.subscriptionHealth[sub.id] = {
                                valid: false,
                                reason: "check_failed",
                            };
                        });
                });

                Promise.all(promises).then(function () {
                    self.checkingHealth = false;
                    // Force Alpine to update
                    self.$nextTick(function () {
                        // Trigger reactivity
                    });
                });
            },

            // Refresh a specific subscription
            refreshSubscription: function (subscriptionId) {
                var self = this;
                var confirmed = confirm(
                    "Refresh this push notification subscription? You may need to grant permission again.",
                );
                if (!confirmed) return;

                // Use window.CrushPushNotifications.refresh from push-notifications.js
                if (
                    window.CrushPushNotifications &&
                    window.CrushPushNotifications.refresh
                ) {
                    window.CrushPushNotifications.refresh()
                        .then(function (success) {
                            if (success) {
                                // Show success and reload
                                alert("Subscription refreshed successfully");
                                window.location.reload();
                            } else {
                                alert("Failed to refresh subscription");
                            }
                        })
                        .catch(function (error) {
                            console.error("Error refreshing:", error);
                            alert("Error refreshing subscription");
                        });
                } else {
                    alert(
                        "Push notification system not loaded. Please refresh the page and try again.",
                    );
                }
            },

            // Delete a subscription by ID
            deleteSubscription: function (subscriptionId) {
                var self = this;
                var confirmed = confirm(
                    "Remove this subscription? You will stop receiving notifications on this device.",
                );
                if (!confirmed) return;

                self.disableRemoteSubscription(subscriptionId);
            },

            // CSP-safe helper: Check if health status exists for subscription
            hasHealthStatus: function (subscriptionId) {
                return !!this.subscriptionHealth[subscriptionId];
            },

            // CSP-safe helper: Check if subscription is healthy
            isHealthy: function (subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                return health && health.valid && !health.warning;
            },

            // CSP-safe helper: Check if subscription has old_subscription warning
            isOldSubscription: function (subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                return health && health.warning === "old_subscription";
            },

            // CSP-safe helper: Get age text for old subscription
            getAgeText: function (subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                var ageDays = health && health.age_days ? health.age_days : 0;
                return "Subscription is " + ageDays + " days old";
            },

            // CSP-safe helper: Check if subscription has high failure count
            hasHighFailureCount: function (subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                return (
                    health &&
                    health.valid === false &&
                    health.reason === "high_failure_count"
                );
            },

            // CSP-safe helper: Check if subscription is not found
            isNotFound: function (subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                return (
                    health && health.valid === false && health.reason === "not_found"
                );
            },
        };
    });

    // CSP-safe wrapper component for individual subscription health status
    // Creates computed properties for a specific subscription ID
    Alpine.data("subscriptionHealthStatus", function (subscriptionId) {
        return {
            subscriptionId: subscriptionId,

            // Get the parent pushPreferences component
            get parentComponent() {
                return this.$root;
            },

            // CSP-safe computed properties
            get hasStatus() {
                var parent = this.parentComponent;
                return (
                    parent.subscriptionHealth &&
                    !!parent.subscriptionHealth[this.subscriptionId]
                );
            },

            get healthyStatus() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth
                    ? parent.subscriptionHealth[this.subscriptionId]
                    : null;
                return health && health.valid && !health.warning;
            },

            get oldStatus() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth
                    ? parent.subscriptionHealth[this.subscriptionId]
                    : null;
                return health && health.warning === "old_subscription";
            },

            get ageText() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth
                    ? parent.subscriptionHealth[this.subscriptionId]
                    : null;
                var ageDays = health && health.age_days ? health.age_days : 0;
                return "Subscription is " + ageDays + " days old";
            },

            get failureStatus() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth
                    ? parent.subscriptionHealth[this.subscriptionId]
                    : null;
                return (
                    health &&
                    health.valid === false &&
                    health.reason === "high_failure_count"
                );
            },

            get notFoundStatus() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth
                    ? parent.subscriptionHealth[this.subscriptionId]
                    : null;
                return (
                    health && health.valid === false && health.reason === "not_found"
                );
            },

            // Pass-through getters for parent properties (for disable button)
            get isDisabling() {
                var parent = this.parentComponent;
                return parent.isDisabling || false;
            },

            get disableButtonText() {
                var parent = this.parentComponent;
                return parent.disableButtonText || "Disable Notifications";
            },

            // CSP-safe methods
            refreshSubscriptionById: function () {
                var parent = this.parentComponent;
                if (parent.refreshSubscription) {
                    parent.refreshSubscription(this.subscriptionId);
                }
            },

            deleteSubscriptionById: function () {
                var parent = this.parentComponent;
                if (parent.deleteSubscription) {
                    parent.deleteSubscription(this.subscriptionId);
                }
            },
        };
    });

    // Coach push notification preferences component (account settings and coach dashboard)
    // Separate from user push preferences - completely independent system
    Alpine.data("coachPushPreferences", function () {
        return {
            subscriptions: [],
            isSupported: false,
            isSubscribed: false,
            isCurrentDeviceSubscribed: false, // TRUE only if THIS device has push enabled
            isEnabling: false,
            isDisabling: false,
            errorMessage: "",
            permissionDenied: false,
            isLoading: true,
            currentEndpoint: null, // For identifying "This device" by endpoint
            currentFingerprint: null, // Stable device fingerprint (fallback for endpoint)
            endpointDetected: false, // Flag to trigger re-render when endpoint is detected
            // i18n strings for time formatting (loaded from data attributes in init)
            i18n: {
                neverUsed: "Never used",
                justNow: "Just now",
                lastActive: "Last active:",
                minutesAgo: "m ago",
                hoursAgo: "h ago",
                daysAgo: "d ago",
                monthsAgo: "mo ago",
                checking: "Checking...",
                checkSubscriptionHealth: "Check Subscription Health",
            },

            get hasSubscriptions() {
                return this.subscriptions.length > 0;
            },
            // Show enable button only if THIS device isn't subscribed (allows multi-device)
            get showEnableButton() {
                return (
                    !this.isLoading &&
                    this.isSupported &&
                    !this.isCurrentDeviceSubscribed &&
                    !this.permissionDenied
                );
            },
            get showPermissionDenied() {
                return !this.isLoading && this.permissionDenied;
            },
            get showNotSupported() {
                return !this.isLoading && !this.isSupported;
            },
            get showPreferences() {
                return !this.isLoading && this.isSubscribed && this.hasSubscriptions;
            },
            get showLoading() {
                return this.isLoading;
            },
            get showEnablingSpinner() {
                return this.isEnabling;
            },
            get showNotEnablingIcon() {
                return !this.isEnabling;
            },
            get showTestSpinner() {
                return this.isSendingTest;
            },
            get enableButtonText() {
                return this.isEnabling ? "Enabling..." : "Enable Notifications";
            },
            get testButtonText() {
                return this.isSendingTest ? "Sending..." : "Send Test";
            },
            get disableButtonText() {
                return this.isDisabling ? "Disabling..." : "Disable Notifications";
            },
            isSendingTest: false,

            init: function () {
                var self = this;

                // Load i18n strings from data attributes
                this._loadI18nStrings();

                var data = this.$el.getAttribute("data-subscriptions");
                if (data) {
                    try {
                        this.subscriptions = JSON.parse(data);
                    } catch (e) {
                        console.error("[CoachPush] Failed to parse subscriptions:", e);
                    }
                }

                this.isSupported =
                    "serviceWorker" in navigator && "PushManager" in window;
                if ("Notification" in window && Notification.permission === "denied") {
                    this.permissionDenied = true;
                }

                // Detect current device endpoint for "This device" badge
                this._detectCurrentEndpoint();

                this._waitForServiceWorker(function () {
                    if (self.isSupported && self.subscriptions.length > 0) {
                        self.isSubscribed = true;
                    }
                    self.isLoading = false;
                    // Re-run device detection after DOM is rendered
                    self.$nextTick(function () {
                        self._retryDeviceMatch();
                    });
                });

                this.$el.addEventListener("change", function (event) {
                    if (event.target.classList.contains("coach-push-pref-toggle")) {
                        var subId = parseInt(event.target.dataset.subscriptionId);
                        var prefKey = event.target.dataset.prefKey;
                        self.updatePreference(
                            subId,
                            prefKey,
                            event.target.checked,
                            event.target,
                        );
                    }
                });

                this.$el.addEventListener("click", function (event) {
                    if (event.target.closest(".enable-coach-push-btn")) {
                        self.enablePush();
                    } else if (event.target.closest(".disable-coach-push-btn")) {
                        // Identify which subscription to disable
                        var btn = event.target.closest(".disable-coach-push-btn");
                        var container = btn.closest("[data-subscription-id]");
                        if (container) {
                            var subscriptionId = parseInt(
                                container.dataset.subscriptionId,
                            );
                            var endpoint = container.dataset.endpoint;
                            // Check if this is the current device
                            if (
                                self.currentEndpoint &&
                                endpoint === self.currentEndpoint
                            ) {
                                self.disablePush(); // Current device - use existing unsubscribe
                            } else {
                                self.disableRemoteSubscription(subscriptionId); // Other device
                            }
                        } else {
                            self.disablePush(); // Fallback to current device
                        }
                    } else if (event.target.closest(".test-coach-push-btn")) {
                        self.sendTestNotification();
                    }
                });
            },

            // Load i18n strings from data attributes
            _loadI18nStrings: function () {
                var el = this.$el;
                this.i18n.neverUsed =
                    el.getAttribute("data-i18n-never-used") || this.i18n.neverUsed;
                this.i18n.justNow =
                    el.getAttribute("data-i18n-just-now") || this.i18n.justNow;
                this.i18n.lastActive =
                    el.getAttribute("data-i18n-last-active") || this.i18n.lastActive;
                this.i18n.minutesAgo =
                    el.getAttribute("data-i18n-minutes-ago") || this.i18n.minutesAgo;
                this.i18n.hoursAgo =
                    el.getAttribute("data-i18n-hours-ago") || this.i18n.hoursAgo;
                this.i18n.daysAgo =
                    el.getAttribute("data-i18n-days-ago") || this.i18n.daysAgo;
                this.i18n.monthsAgo =
                    el.getAttribute("data-i18n-months-ago") || this.i18n.monthsAgo;
                this.i18n.checking =
                    el.getAttribute("data-i18n-checking") || this.i18n.checking;
                this.i18n.checkSubscriptionHealth =
                    el.getAttribute("data-i18n-check-subscription-health") ||
                    this.i18n.checkSubscriptionHealth;
            },

            // Detect current device's push endpoint and fingerprint for "This device" identification
            // Uses endpoint as primary identifier, fingerprint as fallback
            // Sets isCurrentDeviceSubscribed to true if this device has an active subscription
            _detectCurrentEndpoint: function () {
                var self = this;

                // Generate fingerprint immediately using our own implementation
                // No dependency on CrushPush - works independently
                self.currentFingerprint = self._generateFingerprint();

                if ("serviceWorker" in navigator) {
                    navigator.serviceWorker.ready
                        .then(function (reg) {
                            return reg.pushManager.getSubscription();
                        })
                        .then(function (sub) {
                            self.currentEndpoint = sub ? sub.endpoint : null;
                            self.endpointDetected = true; // Trigger Alpine reactivity

                            // Strategy 1: Match by endpoint (most accurate when available)
                            if (sub && sub.endpoint) {
                                var subscriptionElements =
                                    self.$el.querySelectorAll("[data-endpoint]");
                                for (var i = 0; i < subscriptionElements.length; i++) {
                                    if (
                                        subscriptionElements[i].dataset.endpoint ===
                                        sub.endpoint
                                    ) {
                                        self.isCurrentDeviceSubscribed = true;
                                        self._showThisDeviceBadge(
                                            subscriptionElements[i],
                                        );
                                        break;
                                    }
                                }
                            }

                            // Strategy 2: Fallback to fingerprint if endpoint didn't match
                            if (
                                !self.isCurrentDeviceSubscribed &&
                                self.currentFingerprint
                            ) {
                                self._matchByFingerprint();
                            }
                        })
                        .catch(function () {
                            self.endpointDetected = true; // Mark as done even on failure
                            // Try fingerprint matching as fallback
                            if (self.currentFingerprint) {
                                self._matchByFingerprint();
                            }
                        });
                } else {
                    self.endpointDetected = true; // No service worker support
                    // Still try fingerprint matching
                    if (self.currentFingerprint) {
                        self._matchByFingerprint();
                    }
                }
            },

            // Match device by fingerprint (fallback when endpoint doesn't match)
            _matchByFingerprint: function () {
                var self = this;
                var subscriptionElements =
                    self.$el.querySelectorAll("[data-fingerprint]");
                for (var i = 0; i < subscriptionElements.length; i++) {
                    if (
                        subscriptionElements[i].dataset.fingerprint ===
                        self.currentFingerprint
                    ) {
                        self.isCurrentDeviceSubscribed = true;
                        self._showThisDeviceBadge(subscriptionElements[i]);
                        break;
                    }
                }
            },

            // Retry device matching after DOM is rendered (called after isLoading becomes false)
            // This handles the race condition where _detectCurrentEndpoint runs before
            // the subscription elements are rendered by Alpine's x-if
            _retryDeviceMatch: function () {
                var self = this;
                if (self.isCurrentDeviceSubscribed) return; // Already matched

                // Try endpoint matching first
                if (self.currentEndpoint) {
                    var subscriptionElements =
                        self.$el.querySelectorAll("[data-endpoint]");
                    for (var i = 0; i < subscriptionElements.length; i++) {
                        if (
                            subscriptionElements[i].dataset.endpoint ===
                            self.currentEndpoint
                        ) {
                            self.isCurrentDeviceSubscribed = true;
                            self._showThisDeviceBadge(subscriptionElements[i]);
                            return;
                        }
                    }
                }

                // Fall back to fingerprint matching
                if (self.currentFingerprint) {
                    self._matchByFingerprint();
                }
            },

            // Show "This device" badge for a subscription container
            _showThisDeviceBadge: function (container) {
                var badges = container.querySelectorAll(".this-device-badge");
                for (var i = 0; i < badges.length; i++) {
                    badges[i].classList.remove("hidden");
                }
            },

            // Check if a subscription is from the current device
            isCurrentDevice: function (subscription) {
                // Check by endpoint first, then fingerprint
                if (
                    this.currentEndpoint &&
                    subscription.endpoint === this.currentEndpoint
                ) {
                    return true;
                }
                if (
                    this.currentFingerprint &&
                    subscription.device_fingerprint === this.currentFingerprint
                ) {
                    return true;
                }
                return false;
            },

            // Format relative time for "Last active" display
            formatRelativeTime: function (dateStr) {
                if (!dateStr) return this.i18n.neverUsed;
                var date = new Date(dateStr);
                var now = new Date();
                var diffMs = now - date;
                var diffMins = Math.floor(diffMs / 60000);
                if (diffMins < 1) return this.i18n.justNow;
                if (diffMins < 60) return diffMins + " " + this.i18n.minutesAgo;
                var diffHours = Math.floor(diffMins / 60);
                if (diffHours < 24) return diffHours + " " + this.i18n.hoursAgo;
                var diffDays = Math.floor(diffHours / 24);
                if (diffDays < 30) return diffDays + " " + this.i18n.daysAgo;
                var diffMonths = Math.floor(diffDays / 30);
                return diffMonths + " " + this.i18n.monthsAgo;
            },

            enablePush: function () {
                var self = this;
                if (!this.isSupported || this.isEnabling) return;
                this.isEnabling = true;
                this.errorMessage = "";

                Notification.requestPermission()
                    .then(function (permission) {
                        if (permission !== "granted") {
                            self.isEnabling = false;
                            self.permissionDenied = true;
                            return;
                        }
                        navigator.serviceWorker.ready.then(function (registration) {
                            fetch("/api/coach/push/vapid-public-key/")
                                .then(function (r) {
                                    return r.json();
                                })
                                .then(function (data) {
                                    if (!data.success) throw new Error(data.error);
                                    return registration.pushManager.subscribe({
                                        userVisibleOnly: true,
                                        applicationServerKey:
                                            self._urlBase64ToUint8Array(data.publicKey),
                                    });
                                })
                                .then(function (subscription) {
                                    // Get fingerprint for stable device identification
                                    // Use our own implementation - no dependency on CrushPush
                                    var fingerprint = self._generateFingerprint();
                                    return fetch("/api/coach/push/subscribe/", {
                                        method: "POST",
                                        headers: {
                                            "Content-Type": "application/json",
                                            "X-CSRFToken": self.getCsrfToken(),
                                        },
                                        body: JSON.stringify({
                                            endpoint: subscription.endpoint,
                                            keys: {
                                                p256dh: btoa(
                                                    String.fromCharCode.apply(
                                                        null,
                                                        new Uint8Array(
                                                            subscription.getKey(
                                                                "p256dh",
                                                            ),
                                                        ),
                                                    ),
                                                ),
                                                auth: btoa(
                                                    String.fromCharCode.apply(
                                                        null,
                                                        new Uint8Array(
                                                            subscription.getKey("auth"),
                                                        ),
                                                    ),
                                                ),
                                            },
                                            userAgent: navigator.userAgent,
                                            deviceName: self._getDeviceName(),
                                            deviceFingerprint: fingerprint,
                                        }),
                                    });
                                })
                                .then(function (r) {
                                    return r.json();
                                })
                                .then(function (data) {
                                    self.isEnabling = false;
                                    if (data.success) {
                                        self.isSubscribed = true;
                                        window.location.reload();
                                    } else {
                                        self.errorMessage =
                                            data.error || "Failed to enable";
                                    }
                                })
                                .catch(function (err) {
                                    self.isEnabling = false;
                                    self.errorMessage = "Error occurred";
                                    console.error("[CoachPush]", err);
                                });
                        });
                    })
                    .catch(function () {
                        self.isEnabling = false;
                        self.errorMessage = "Permission denied";
                    });
            },

            disablePush: function () {
                var self = this;
                if (!this.isSupported || this.isDisabling) return;
                this.isDisabling = true;

                navigator.serviceWorker.ready
                    .then(function (reg) {
                        return reg.pushManager.getSubscription();
                    })
                    .then(function (sub) {
                        if (!sub) {
                            self.isDisabling = false;
                            self.isSubscribed = false;
                            self.isCurrentDeviceSubscribed = false;
                            window.location.reload();
                            return;
                        }
                        // Call API first to check if browser subscription should be kept
                        // Include fingerprint so server can check for user subscriptions with different endpoint
                        // Use our own implementation - no dependency on CrushPush
                        var fingerprint = self._generateFingerprint();
                        return fetch("/api/coach/push/unsubscribe/", {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                                "X-CSRFToken": self.getCsrfToken(),
                            },
                            body: JSON.stringify({
                                endpoint: sub.endpoint,
                                deviceFingerprint: fingerprint,
                            }),
                        })
                            .then(function (response) {
                                return response.json();
                            })
                            .then(function (data) {
                                if (data.success) {
                                    // Only unsubscribe browser if no other system needs it
                                    if (!data.keep_browser_subscription) {
                                        return sub.unsubscribe();
                                    }
                                }
                            })
                            .then(function () {
                                self.isDisabling = false;
                                self.isCurrentDeviceSubscribed = false;
                                window.location.reload();
                            });
                    })
                    .catch(function (err) {
                        self.isDisabling = false;
                        self.errorMessage = "Failed";
                        console.error("[CoachPush]", err);
                    });
            },

            // Disable push subscription for a remote device (not the current device)
            disableRemoteSubscription: function (subscriptionId) {
                var self = this;
                if (this.isDisabling) return;

                this.isDisabling = true;

                fetch("/api/coach/push/delete-subscription/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify({ subscription_id: subscriptionId }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        self.isDisabling = false;
                        if (data.success) {
                            // Reload to update UI
                            window.location.reload();
                        } else {
                            self.errorMessage =
                                data.error || "Failed to disable notifications";
                        }
                    })
                    .catch(function (err) {
                        self.isDisabling = false;
                        self.errorMessage = "An error occurred. Please try again.";
                        console.error("[CoachPush] Remote disable error:", err);
                    });
            },

            sendTestNotification: function () {
                var self = this;
                if (this.isSendingTest) return;
                this.isSendingTest = true;
                fetch("/api/coach/push/test/", {
                    method: "POST",
                    headers: { "X-CSRFToken": self.getCsrfToken() },
                })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        self.isSendingTest = false;
                    })
                    .catch(function (err) {
                        self.isSendingTest = false;
                        console.error("[CoachPush] Test failed:", err);
                    });
            },

            updatePreference: function (subId, prefKey, value, checkbox) {
                var self = this;
                var prefs = {};
                prefs[prefKey] = value;
                fetch("/api/coach/push/preferences/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify({ subscriptionId: subId, preferences: prefs }),
                })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        if (!data.success) checkbox.checked = !value;
                    })
                    .catch(function () {
                        checkbox.checked = !value;
                    });
            },

            getCsrfToken: function () {
                // First try hidden form input (works with CSRF_COOKIE_HTTPONLY=True)
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                // Fallback to cookie
                var c = document.cookie.split("; ").find(function (r) {
                    return r.startsWith("csrftoken=");
                });
                return c ? c.split("=")[1] : "";
            },

            _waitForServiceWorker: function (cb) {
                var self = this;
                if ("serviceWorker" in navigator) {
                    navigator.serviceWorker.ready
                        .then(function () {
                            cb();
                        })
                        .catch(function () {
                            self.isLoading = false;
                            cb();
                        });
                } else {
                    self.isLoading = false;
                    cb();
                }
            },

            _urlBase64ToUint8Array: function (base64) {
                var padding = "=".repeat((4 - (base64.length % 4)) % 4);
                var b64 = (base64 + padding).replace(/\-/g, "+").replace(/_/g, "/");
                var raw = window.atob(b64);
                var out = new Uint8Array(raw.length);
                for (var i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
                return out;
            },

            _getDeviceName: function () {
                var ua = navigator.userAgent;
                if (/Android/i.test(ua)) return "Android Chrome";
                if (/iPhone|iPad|iPod/i.test(ua)) return "iPhone Safari";
                if (/Windows/i.test(ua)) return "Windows Desktop";
                if (/Macintosh/i.test(ua)) return "Mac Desktop";
                if (/Linux/i.test(ua)) return "Linux Desktop";
                return "Unknown Device";
            },

            // Generate a stable browser fingerprint from hardware/software characteristics
            // Independent implementation - doesn't require CrushPush to be loaded
            _generateFingerprint: function () {
                var components = [
                    screen.width,
                    screen.height,
                    window.devicePixelRatio || 1,
                    new Date().getTimezoneOffset(),
                    navigator.language || "",
                    navigator.platform || "",
                    navigator.hardwareConcurrency || 0,
                    navigator.deviceMemory || 0,
                    "ontouchstart" in window ? 1 : 0,
                    screen.colorDepth || 0,
                    this._getCanvasFingerprint(),
                ];
                return this._simpleHash(components.join("|"));
            },

            _getCanvasFingerprint: function () {
                try {
                    var canvas = document.createElement("canvas");
                    canvas.width = 200;
                    canvas.height = 50;
                    var ctx = canvas.getContext("2d");
                    ctx.textBaseline = "top";
                    ctx.font = "14px Arial";
                    ctx.fillStyle = "#f60";
                    ctx.fillRect(125, 1, 62, 20);
                    ctx.fillStyle = "#069";
                    ctx.fillText("Crush.lu PWA", 2, 15);
                    ctx.fillStyle = "rgba(102, 204, 0, 0.7)";
                    ctx.fillText("Crush.lu PWA", 4, 17);
                    return canvas.toDataURL().slice(-50);
                } catch (e) {
                    return "no-canvas";
                }
            },

            _simpleHash: function (str) {
                var hash = 5381;
                for (var i = 0; i < str.length; i++) {
                    hash = (hash << 5) + hash + str.charCodeAt(i);
                    hash = hash & hash;
                }
                return Math.abs(hash).toString(16).padStart(8, "0");
            },
        };
    });

    // Decline animation component (connection response)
    // Shows briefly then fades out
    Alpine.data("declineAnimation", function () {
        return {
            show: true,
            init: function () {
                var self = this;
                setTimeout(function () {
                    self.show = false;
                }, 2000);
            },
        };
    });

    // Character counter component
    // Reads initial count from data-initial-count and max from data-max-length
    Alpine.data("charCounter", function () {
        return {
            charCount: 0,
            maxLength: 500,
            init: function () {
                var initialCount = this.$el.getAttribute("data-initial-count");
                var maxLength = this.$el.getAttribute("data-max-length");
                this.charCount = initialCount ? parseInt(initialCount) : 0;
                this.maxLength = maxLength ? parseInt(maxLength) : 500;
            },
            get charDisplay() {
                return this.charCount + "/" + this.maxLength;
            },
            updateCount: function (event) {
                this.charCount = event.target.value.length;
            },
        };
    });

    // Photo upload component for profile photos
    // Reads initial photos from data attributes
    Alpine.data("photoUpload", function () {
        return {
            photos: [
                { id: 1, hasImage: false, preview: "" },
                { id: 2, hasImage: false, preview: "" },
                { id: 3, hasImage: false, preview: "" },
            ],

            // Computed getters for CSP compatibility
            get photo1NoImage() {
                return !this.photos[0].hasImage;
            },
            get photo2NoImage() {
                return !this.photos[1].hasImage;
            },
            get photo3NoImage() {
                return !this.photos[2].hasImage;
            },
            get photo1HasImage() {
                return this.photos[0].hasImage;
            },
            get photo1Preview() {
                return this.photos[0].preview;
            },
            get photo2HasImage() {
                return this.photos[1].hasImage;
            },
            get photo2Preview() {
                return this.photos[1].preview;
            },
            get photo3HasImage() {
                return this.photos[2].hasImage;
            },
            get photo3Preview() {
                return this.photos[2].preview;
            },

            init: function () {
                var el = this.$el;
                var self = this;

                // Read initial photo states from data attributes (from database)
                for (var i = 1; i <= 3; i++) {
                    var hasImage =
                        el.getAttribute("data-photo-" + i + "-exists") === "true";
                    var preview = el.getAttribute("data-photo-" + i + "-url") || "";
                    this.photos[i - 1].hasImage = hasImage;
                    this.photos[i - 1].preview = preview;
                    this.photos[i - 1].uploadedUrl = preview;
                }

                // Also check draft for any newly uploaded photos not yet in database
                fetch("/api/profile/draft/get/")
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (result) {
                        if (result.success && result.data && result.data.merged) {
                            var draft = result.data.merged;

                            // Check each photo URL in draft
                            for (var i = 1; i <= 3; i++) {
                                var photoUrlKey = "photo_" + i + "_url";
                                if (draft[photoUrlKey]) {
                                    self.photos[i - 1].preview = draft[photoUrlKey];
                                    self.photos[i - 1].hasImage = true;
                                    self.photos[i - 1].uploadedUrl = draft[photoUrlKey];
                                }
                            }
                        }
                    })
                    .catch(function (err) {
                        console.error(
                            "[PHOTO UPLOAD] Failed to load draft photos:",
                            err,
                        );
                    });
            },
            handleFile1: function (event) {
                this._handleFileSelect(0, event);
            },
            handleFile2: function (event) {
                this._handleFileSelect(1, event);
            },
            handleFile3: function (event) {
                this._handleFileSelect(2, event);
            },
            _handleFileSelect: function (index, event) {
                var file = event.target.files[0];
                if (file) {
                    var self = this;
                    var photoNumber = index + 1; // Convert 0-indexed to 1-indexed

                    // Show preview immediately
                    var reader = new FileReader();
                    reader.onload = function (e) {
                        self.photos[index].preview = e.target.result;
                        self.photos[index].hasImage = true;
                    };
                    reader.readAsDataURL(file);

                    // Upload to server immediately (auto-save)
                    var formData = new FormData();
                    formData.append("photo", file);
                    formData.append("photo_number", photoNumber);

                    // Get CSRF token
                    var csrfToken = document.querySelector(
                        "[name=csrfmiddlewaretoken]",
                    );
                    if (csrfToken) {
                        formData.append("csrfmiddlewaretoken", csrfToken.value);
                    }

                    fetch("/api/profile/draft/upload-photo/", {
                        method: "POST",
                        headers: {
                            "X-CSRFToken": csrfToken ? csrfToken.value : "",
                        },
                        body: formData,
                    })
                        .then(function (response) {
                            return response.json();
                        })
                        .then(function (result) {
                            if (result.success) {
                                self.photos[index].uploadedUrl = result.photo_url;
                            } else {
                                console.error(
                                    "[PHOTO UPLOAD] ❌ Upload failed:",
                                    result.error,
                                );
                                alert("Photo upload failed: " + result.error);
                            }
                        })
                        .catch(function (err) {
                            console.error("[PHOTO UPLOAD] ❌ Network error:", err);
                            alert("Photo upload failed. Please try again.");
                        });
                }
            },
            removePhoto1: function () {
                this._removePhoto(0);
            },
            removePhoto2: function () {
                this._removePhoto(1);
            },
            removePhoto3: function () {
                this._removePhoto(2);
            },
            _removePhoto: function (index) {
                this.photos[index].preview = "";
                this.photos[index].hasImage = false;
                var input = document.getElementById("photo" + (index + 1));
                if (input) {
                    input.value = "";
                }
            },
        };
    });

    // Profile creation wizard component
    // Reads initial values from data attributes
    Alpine.data("profileWizard", function () {
        return {
            currentStep: 1,
            totalSteps: 5,
            isSubmitting: false,
            phoneVerified: false,
            showErrors: false,
            errors: {},
            isEditing: false,
            step1Valid: false,
            step2Valid: true,
            step4Valid: true, // Preferences always has defaults; validated server-side

            // Step 1 required fields tracking
            gender: "",
            location: "",
            locationName: "",

            // Step 2 fields tracking

            // Date of birth formatted display (from dobPicker)
            dobFormatted: "",

            // Field-specific error messages
            fieldErrors: {},

            // Step saving state
            isSaving: false,
            saveError: "",

            // Computed-like properties for CSP compatibility
            // These avoid function calls in templates
            get step1Completed() {
                return this.currentStep > 1;
            },
            get step2Completed() {
                return this.currentStep > 2;
            },
            get step3Completed() {
                return this.currentStep > 3;
            },
            get step4Completed() {
                return this.currentStep > 4;
            },
            get step5Completed() {
                return this.currentStep > 5;
            },
            // Progress bar for mobile wizard
            get progressBarStyle() {
                var pct = (this.currentStep / this.totalSteps) * 100;
                return "width:" + pct + "%";
            },
            // Step labels and the "Step X of Y — " prefix arrive from the
            // template as data attributes so Django's {% trans %} can
            // translate them (DE/FR). The JS itself never hard-codes copy.
            stepLabels: [],
            stepLabelPrefix: "Step ",
            stepLabelOf: " of ",
            stepLabelSep: " — ",
            get stepLabel() {
                var name = this.stepLabels[this.currentStep - 1] || "";
                return (
                    this.stepLabelPrefix +
                    this.currentStep +
                    this.stepLabelOf +
                    this.totalSteps +
                    this.stepLabelSep +
                    name
                );
            },

            get isStep1() {
                return this.currentStep === 1;
            },
            get isStep2() {
                return this.currentStep === 2;
            },
            get isStep3() {
                return this.currentStep === 3;
            },
            get isStep4() {
                return this.currentStep === 4;
            },
            get isStep5() {
                return this.currentStep === 5;
            },
            get step1NotCompleted() {
                return !this.step1Completed;
            },
            get step2NotCompleted() {
                return !this.step2Completed;
            },
            get step3NotCompleted() {
                return !this.step3Completed;
            },
            get step4NotCompleted() {
                return !this.step4Completed;
            },
            get step5NotCompleted() {
                return !this.step5Completed;
            },
            get notPhoneVerified() {
                return !this.phoneVerified;
            },
            get isNotEditing() {
                return !this.isEditing;
            },
            get isNotSubmitting() {
                return !this.isSubmitting;
            },
            get isSavingStep() {
                return this.isSaving;
            },
            get isNotSaving() {
                return !this.isSaving;
            },
            get hasSaveError() {
                return this.saveError !== "";
            },
            get hasFieldErrors() {
                return Object.keys(this.fieldErrors).length > 0;
            },
            get canContinueStep1() {
                return (
                    this.phoneVerified &&
                    this.gender !== "" &&
                    this.location !== "" &&
                    !this.isSaving
                );
            },
            get cannotContinueStep1() {
                return (
                    !this.phoneVerified ||
                    this.gender === "" ||
                    this.location === "" ||
                    this.isSaving
                );
            },
            get canContinueStep2() {
                return !this.isSaving;
            },
            get cannotContinueStep2() {
                return this.isSaving;
            },
            get hasGenderError() {
                return this.fieldErrors.gender !== undefined;
            },
            get hasLocationError() {
                return this.fieldErrors.location !== undefined;
            },
            get genderErrorMessage() {
                return this.fieldErrors.gender || "";
            },
            get locationErrorMessage() {
                return this.fieldErrors.location || "";
            },

            // Step progress bar classes (avoid ternary expressions in templates)
            get step1CircleClass() {
                return this.currentStep >= 1
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-300";
            },
            get step1TextClass() {
                return this.currentStep >= 1
                    ? "text-purple-600 font-medium"
                    : "text-gray-400";
            },
            get step2CircleClass() {
                return this.currentStep >= 2
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-300";
            },
            get step2TextClass() {
                return this.currentStep >= 2
                    ? "text-purple-600 font-medium"
                    : "text-gray-400";
            },
            get step3CircleClass() {
                return this.currentStep >= 3
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-300";
            },
            get step3TextClass() {
                return this.currentStep >= 3
                    ? "text-purple-600 font-medium"
                    : "text-gray-400";
            },
            get step4CircleClass() {
                return this.currentStep >= 4
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-300";
            },
            get step4TextClass() {
                return this.currentStep >= 4
                    ? "text-purple-600 font-medium"
                    : "text-gray-400";
            },
            get step5CircleClass() {
                return this.currentStep >= 5
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-300";
            },
            get step5TextClass() {
                return this.currentStep >= 5
                    ? "text-purple-600 font-medium"
                    : "text-gray-400";
            },
            get step1ConnectorClass() {
                return this.step1Completed
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-200";
            },
            get step2ConnectorClass() {
                return this.step2Completed
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-200";
            },
            get step3ConnectorClass() {
                return this.step3Completed
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-200";
            },
            get step4ConnectorClass() {
                return this.step4Completed
                    ? "bg-gradient-to-r from-purple-500 to-pink-500"
                    : "bg-gray-200";
            },

            // Step navigation button classes (for breadcrumb quick navigation)
            get step1ButtonClass() {
                return this.isStep1
                    ? "bg-purple-100 text-purple-700 font-medium"
                    : "text-gray-500 hover:text-purple-600 hover:bg-purple-50";
            },
            get step2ButtonClass() {
                return this.isStep2
                    ? "bg-purple-100 text-purple-700 font-medium"
                    : "text-gray-500 hover:text-purple-600 hover:bg-purple-50";
            },
            get step3ButtonClass() {
                return this.isStep3
                    ? "bg-purple-100 text-purple-700 font-medium"
                    : "text-gray-500 hover:text-purple-600 hover:bg-purple-50";
            },
            get step4ButtonClass() {
                return this.isStep4
                    ? "bg-purple-100 text-purple-700 font-medium"
                    : "text-gray-500 hover:text-purple-600 hover:bg-purple-50";
            },
            get step5ButtonClass() {
                return this.isStep5
                    ? "bg-purple-100 text-purple-700 font-medium"
                    : "text-gray-500 hover:text-purple-600 hover:bg-purple-50";
            },

            init: function () {
                // Read initial values from data attributes
                var el = this.$el;
                var initialStep = el.getAttribute("data-initial-step");
                var phoneVerified = el.getAttribute("data-phone-verified");
                var isEditing = el.getAttribute("data-is-editing");

                // Translated step labels are injected by the template as a
                // pipe-separated list so the JS stays copy-free.
                var labelsAttr = el.getAttribute("data-step-labels");
                if (labelsAttr) {
                    this.stepLabels = labelsAttr.split("|");
                }
                var prefix = el.getAttribute("data-step-label-prefix");
                if (prefix) this.stepLabelPrefix = prefix;
                var ofWord = el.getAttribute("data-step-label-of");
                if (ofWord) this.stepLabelOf = ofWord;
                var sep = el.getAttribute("data-step-label-sep");
                if (sep) this.stepLabelSep = sep;

                // Map DB completion_status values → wizard sub-step numbers.
                // Wizard has 5 sub-steps: 1 Basic Info · 2 About You · 3 Photos
                // · 4 Preferences · 5 Review. step4 is a legacy DB value from
                // the old coach-picker wizard and now lands users on Review.
                var stepMap = {
                    not_started: 1,
                    step1: 2,
                    step2: 3,
                    step3: 4,
                    step4: 5,
                    submitted: 5,
                };

                if (initialStep && stepMap[initialStep]) {
                    this.currentStep = stepMap[initialStep];
                } else if (initialStep && !isNaN(parseInt(initialStep))) {
                    this.currentStep = parseInt(initialStep);
                }

                this.phoneVerified = phoneVerified === "true";
                this.isEditing = isEditing === "true";

                // Set up HTMX listener
                var self = this;
                window.addEventListener("htmx:afterRequest", function (event) {
                    if (event.detail.successful) {
                        var trigger = event.detail.xhr.getResponseHeader(
                            "HX-Trigger-After-Swap",
                        );
                        if (trigger === "step-valid") {
                            self.nextStep();
                        }
                    }
                });

                // Listen for phone verification event from nested component
                window.addEventListener("phone-verified", function () {
                    self.phoneVerified = true;
                });

                // Listen for phone unverification (when user clicks Change)
                this.$el.addEventListener("phone-unverified", function () {
                    self.phoneVerified = false;
                });

                // Initialize field values from DOM
                self.initFieldTracking();

                // Listen for custom events from canton map and gender selection
                window.addEventListener("location-selected", function (e) {
                    if (e.detail && e.detail.location) {
                        self.location = e.detail.location;
                        self.locationName = e.detail.name || e.detail.location;
                        self.fieldErrors.location = undefined;
                        self.saveDraft();
                    }
                });

                window.addEventListener("gender-selected", function (e) {
                    if (e.detail && e.detail.gender) {
                        self.gender = e.detail.gender;
                        self.fieldErrors.gender = undefined;
                    }
                });

                // Listen for date of birth selection from dobPicker component
                window.addEventListener("dob-selected", function (e) {
                    if (e.detail && e.detail.formatted) {
                        self.dobFormatted = e.detail.formatted;
                        self.saveDraft();
                    }
                });

                // =========================================================================
                // DRAFT AUTO-SAVE SETUP
                // =========================================================================

                // Load draft data on init (will populate form fields if draft exists)
                self.loadDraft();

                // Setup auto-save listeners
                self.setupAutoSaveListeners();

                // Setup periodic checkpoint every 60s
                self.setupPeriodicCheckpoint();

                // Warn before leaving with unsaved changes
                self.setupUnloadWarning();
            },

            // Initialize field tracking from DOM values
            initFieldTracking: function () {
                var self = this;

                // Read initial gender value
                var genderEl = document.querySelector('[name="gender"]:checked');
                if (genderEl) {
                    self.gender = genderEl.value;
                }

                // Read initial location value and name
                var locationEl = document.getElementById("id_location");
                if (locationEl && locationEl.value) {
                    self.location = locationEl.value;
                    // Try to get the display name from the canton map component's data attribute
                    var cantonMapEl = document.querySelector('[x-data="cantonMap"]');
                    if (cantonMapEl) {
                        self.locationName =
                            cantonMapEl.getAttribute("data-initial-name") ||
                            locationEl.value;
                    } else {
                        self.locationName = locationEl.value;
                    }
                }

                // Set up change listeners for gender radio buttons
                var genderRadios = document.querySelectorAll('[name="gender"]');
                genderRadios.forEach(function (radio) {
                    radio.addEventListener("change", function (e) {
                        self.gender = e.target.value;
                        self.fieldErrors.gender = undefined;
                        // Dispatch event for other components
                        window.dispatchEvent(
                            new CustomEvent("gender-selected", {
                                detail: { gender: e.target.value },
                            }),
                        );
                    });
                });

                // Set up change listener for location (hidden input updated by canton map)
                var locationInput = document.getElementById("id_location");
                if (locationInput) {
                    // Use MutationObserver to detect value changes on hidden input
                    var observer = new MutationObserver(function (mutations) {
                        mutations.forEach(function (mutation) {
                            if (
                                mutation.type === "attributes" &&
                                mutation.attributeName === "value"
                            ) {
                                self.location = locationInput.value;
                                self.fieldErrors.location = undefined;
                            }
                        });
                    });
                    observer.observe(locationInput, { attributes: true });

                    // Also listen for direct changes
                    locationInput.addEventListener("change", function (e) {
                        self.location = e.target.value;
                        self.fieldErrors.location = undefined;
                    });
                }
            },

            nextStep: function () {
                if (this.currentStep < this.totalSteps) {
                    this.currentStep++;
                    window.scrollTo({ top: 0, behavior: "smooth" });
                }
            },

            // CSP-compatible method for conditional next step (requires phone verification)
            nextStepIfVerified: function () {
                if (this.phoneVerified) {
                    this.nextStep();
                }
            },

            prevStep: function () {
                if (this.currentStep > 1) {
                    this.currentStep--;
                    window.scrollTo({ top: 0, behavior: "smooth" });
                }
            },

            goToStep: function (step) {
                if (step >= 1 && step <= this.totalSteps) {
                    this.currentStep = step;
                    window.scrollTo({ top: 0, behavior: "smooth" });
                }
            },

            isStepCompleted: function (step) {
                return step < this.currentStep;
            },

            isCurrentStep: function (step) {
                return step === this.currentStep;
            },

            updateReview: function () {
                var phone = document.querySelector("[name=phone_number]");
                var genderEl = document.querySelector("[name=gender]:checked");
                var location = document.querySelector("[name=location]");

                var reviewPhone = this.$refs.reviewPhone;
                var reviewDob = this.$refs.reviewDob;
                var reviewGender = this.$refs.reviewGender;
                var reviewLocation = this.$refs.reviewLocation;

                if (reviewPhone) {
                    reviewPhone.textContent = phone
                        ? phone.value || "Not provided"
                        : "Not provided";
                }
                if (reviewDob) {
                    // Use formatted date from dobPicker if available
                    reviewDob.textContent = this.dobFormatted || "Not provided";
                }
                if (reviewGender && genderEl) {
                    var label = genderEl.nextElementSibling;
                    if (label) {
                        var genderLabel = label.querySelector(".gender-label");
                        reviewGender.textContent = genderLabel
                            ? genderLabel.textContent
                            : "Not selected";
                    }
                } else if (reviewGender) {
                    reviewGender.textContent = "Not selected";
                }
                if (reviewLocation) {
                    reviewLocation.textContent = this.locationName || "Not selected";
                }
            },

            setSubmitting: function () {
                this.isSubmitting = true;
                // CRITICAL: Before form submission, ensure phone number input has full international number
                // intlTelInput with separateDialCode=true stores only national number in input.value
                // We need to set the full number so Django form receives it correctly
                var phoneInput = document.querySelector('[name="phone_number"]');
                if (phoneInput) {
                    if (window.itiInstance) {
                        // Get full international number from intlTelInput (works for both
                        // readOnly/verified and editable phones)
                        var fullNumber = window.itiInstance.getNumber();
                        if (fullNumber) {
                            phoneInput.value = fullNumber;
                        }
                    }
                    // Safety net: if value still lacks '+' prefix and looks like a
                    // national number, the backend clean_phone_number will handle it
                    // for verified phones by returning the DB value.
                }
            },

            // Handle form submission - only allow on the final step (Review).
            // This prevents Enter key in text inputs from submitting the form prematurely.
            handleFormSubmit: function (e) {
                // Only allow submission when on the final (Review) step.
                if (this.currentStep !== this.totalSteps) {
                    // Prevent form submission on non-final steps
                    return;
                }
                // Prepare form data (phone number formatting)
                this.setSubmitting();
                // Refresh CSRF token before final submit to prevent stale-token errors
                // (form may have been open for 15+ minutes, server may have rotated secrets)
                fetch("/api/csrf-token/", {
                    method: "GET",
                    credentials: "same-origin",
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.csrfToken) {
                            var inputs = document.querySelectorAll(
                                'input[name="csrfmiddlewaretoken"]',
                            );
                            for (var i = 0; i < inputs.length; i++) {
                                inputs[i].value = data.csrfToken;
                            }
                        }
                        var form = document.getElementById("profileForm");
                        if (form) form.submit();
                    })
                    .catch(function () {
                        // If refresh fails, try submitting with existing token
                        var form = document.getElementById("profileForm");
                        if (form) form.submit();
                    });
            },

            nextStepAndReview: function () {
                this.nextStep();
                this.updateReview();
            },

            // Update ALL csrfmiddlewaretoken inputs in the DOM with a fresh token
            updateAllCsrfTokens: function (token) {
                var inputs = document.querySelectorAll(
                    'input[name="csrfmiddlewaretoken"]',
                );
                for (var i = 0; i < inputs.length; i++) {
                    inputs[i].value = token;
                }
            },

            // CSRF token helper for AJAX requests
            // Reads from hidden form input (works with CSRF_COOKIE_HTTPONLY=True)
            getCsrfToken: function () {
                // First try the hidden form input (preferred when CSRF_COOKIE_HTTPONLY=True)
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) {
                    return input.value;
                }
                // Fallback to cookie (if CSRF_COOKIE_HTTPONLY=False)
                var cookie = document.cookie.split("; ").find(function (row) {
                    return row.startsWith("csrftoken=");
                });
                return cookie ? cookie.split("=")[1] : "";
            },

            // Collect Step 1 form data
            collectStep1Data: function () {
                var phoneEl = document.querySelector('[name="phone_number"]');
                var dobEl = document.querySelector('[name="date_of_birth"]');
                var genderEl = document.querySelector('[name="gender"]:checked');
                var locationEl = document.getElementById("id_location");

                // Get phone number: prefer intlTelInput's getNumber() for full international format
                // intlTelInput with separateDialCode=true stores only national number in input.value
                var phoneNumber = "";
                if (window.itiInstance) {
                    phoneNumber = window.itiInstance.getNumber() || "";
                } else if (phoneEl) {
                    phoneNumber = phoneEl.value || "";
                }

                return {
                    phone_number: phoneNumber,
                    date_of_birth: dobEl ? dobEl.value : "",
                    gender: genderEl ? genderEl.value : "",
                    location: locationEl ? locationEl.value : "",
                };
            },

            // Collect Step 2 form data
            collectStep2Data: function () {
                var bioEl = document.querySelector('[name="bio"]');
                var interestsEl = document.querySelector('[name="interests"]');

                return {
                    bio: bioEl ? bioEl.value : "",
                    interests: interestsEl ? interestsEl.value : "",
                };
            },

            // Collect Step 3 form data (privacy settings + event languages - photos handled by HTMX)
            collectStep3Data: function () {
                var showFullName = document.querySelector('[name="show_full_name"]');
                var showExactAge = document.querySelector('[name="show_exact_age"]');

                // Collect checked event language checkboxes
                var langCheckboxes = document.querySelectorAll(
                    '[name="event_languages"]:checked',
                );
                var eventLanguages = [];
                for (var i = 0; i < langCheckboxes.length; i++) {
                    eventLanguages.push(langCheckboxes[i].value);
                }

                return {
                    show_full_name: showFullName ? showFullName.checked : false,
                    show_exact_age: showExactAge ? showExactAge.checked : true,
                    event_languages: eventLanguages,
                };
            },

            // Save Step 1 data to backend
            saveStep1: function () {
                var self = this;
                self.isSaving = true;
                self.saveError = "";
                self.fieldErrors = {};

                var data = self.collectStep1Data();

                return fetch("/api/profile/save-step1/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify(data),
                })
                    .then(function (response) {
                        return response.json().then(function (d) {
                            return { ok: response.ok, data: d };
                        });
                    })
                    .then(function (result) {
                        self.isSaving = false;
                        if (result.ok && result.data.success) {
                            self.fieldErrors = {};
                            if (result.data.csrfToken) {
                                self.updateAllCsrfTokens(result.data.csrfToken);
                            }
                            return { success: true };
                        } else {
                            self.saveError =
                                result.data.error ||
                                "Failed to save. Please try again.";
                            // Handle field-specific errors from backend
                            if (result.data.errors) {
                                self.fieldErrors = result.data.errors;
                            }
                            return {
                                success: false,
                                error: self.saveError,
                                errors: result.data.errors,
                            };
                        }
                    })
                    .catch(function (err) {
                        self.isSaving = false;
                        self.saveError = "Network error. Please check your connection.";
                        return { success: false, error: self.saveError };
                    });
            },

            // Save Step 2 data to backend
            saveStep2: function () {
                var self = this;
                self.isSaving = true;
                self.saveError = "";
                self.fieldErrors = {};

                var data = self.collectStep2Data();

                return fetch("/api/profile/save-step2/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify(data),
                })
                    .then(function (response) {
                        return response.json().then(function (d) {
                            return { ok: response.ok, data: d };
                        });
                    })
                    .then(function (result) {
                        self.isSaving = false;
                        if (result.ok && result.data.success) {
                            self.fieldErrors = {};
                            if (result.data.csrfToken) {
                                self.updateAllCsrfTokens(result.data.csrfToken);
                            }
                            return { success: true };
                        } else {
                            self.saveError =
                                result.data.error ||
                                "Failed to save. Please try again.";
                            // Handle field-specific errors from backend
                            if (result.data.errors) {
                                self.fieldErrors = result.data.errors;
                            }
                            return {
                                success: false,
                                error: self.saveError,
                                errors: result.data.errors,
                            };
                        }
                    })
                    .catch(function (err) {
                        self.isSaving = false;
                        self.saveError = "Network error. Please check your connection.";
                        return { success: false, error: self.saveError };
                    });
            },

            // Save Step 3 data to backend (privacy settings via FormData)
            saveStep3: function () {
                var self = this;
                self.isSaving = true;
                self.saveError = "";

                var data = self.collectStep3Data();

                // Use FormData to match backend expectation
                var formData = new FormData();
                if (data.show_full_name) formData.append("show_full_name", "on");
                if (data.show_exact_age) formData.append("show_exact_age", "on");

                // Append each selected event language
                for (var i = 0; i < data.event_languages.length; i++) {
                    formData.append("event_languages", data.event_languages[i]);
                }

                return fetch("/api/profile/save-step3/", {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: formData,
                })
                    .then(function (response) {
                        return response.json().then(function (d) {
                            return { ok: response.ok, data: d };
                        });
                    })
                    .then(function (result) {
                        self.isSaving = false;
                        if (result.ok && result.data.success) {
                            if (result.data.csrfToken) {
                                self.updateAllCsrfTokens(result.data.csrfToken);
                            }
                            return { success: true };
                        } else {
                            self.saveError =
                                result.data.error ||
                                "Failed to save. Please try again.";
                            return { success: false, error: self.saveError };
                        }
                    })
                    .catch(function (err) {
                        self.isSaving = false;
                        self.saveError = "Network error. Please check your connection.";
                        return { success: false, error: self.saveError };
                    });
            },

            // Save Step 1 and advance if successful
            saveAndNextStep1: function () {
                var self = this;
                if (!self.phoneVerified) return;

                self.saveStep1().then(function (result) {
                    if (result.success) {
                        self.saveError = "";
                        self.currentStep = 2;
                        window.scrollTo({ top: 0, behavior: "smooth" });
                    }
                    // Error is already set in saveStep1
                });
            },

            // Save Step 2 and advance if successful
            saveAndNextStep2: function () {
                var self = this;

                self.saveStep2().then(function (result) {
                    if (result.success) {
                        self.saveError = "";
                        self.currentStep = 3;
                        window.scrollTo({ top: 0, behavior: "smooth" });
                    }
                });
            },

            // Save Step 3 (Photos) and advance to the Preferences step.
            // Coach is never assigned by the user — submissions land in the
            // verification channel; any coach can claim them.
            saveAndNextStep3: function () {
                var self = this;

                self.saveStep3().then(function (result) {
                    if (result.success) {
                        self.saveError = "";
                        self.currentStep = 4;
                        window.scrollTo({ top: 0, behavior: "smooth" });
                    }
                });
            },

            // Collect the Preferences step fields from the DOM.
            collectStep4Data: function () {
                var genderNodes = document.querySelectorAll(
                    "input[name=preferred_genders]:checked",
                );
                var genders = [];
                for (var i = 0; i < genderNodes.length; i++) {
                    genders.push(genderNodes[i].value);
                }
                var minInput = document.querySelector("[name=preferred_age_min]");
                var maxInput = document.querySelector("[name=preferred_age_max]");
                return {
                    preferred_genders: genders,
                    preferred_age_min: minInput ? parseInt(minInput.value, 10) : null,
                    preferred_age_max: maxInput ? parseInt(maxInput.value, 10) : null,
                };
            },

            // Save Step 4 (Preferences) to the backend.
            savePreferences: function () {
                var self = this;
                self.isSaving = true;
                self.saveError = "";
                self.fieldErrors = {};

                var data = self.collectStep4Data();

                return fetch("/api/profile/save-preferences/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: JSON.stringify(data),
                })
                    .then(function (response) {
                        return response.json().then(function (d) {
                            return { ok: response.ok, data: d };
                        });
                    })
                    .then(function (result) {
                        self.isSaving = false;
                        if (result.ok && result.data.success) {
                            self.fieldErrors = {};
                            if (result.data.csrfToken) {
                                self.updateAllCsrfTokens(result.data.csrfToken);
                            }
                            return { success: true };
                        }
                        self.saveError =
                            (result.data && result.data.error) ||
                            "Failed to save preferences. Please try again.";
                        if (result.data && result.data.errors) {
                            self.fieldErrors = result.data.errors;
                        }
                        return {
                            success: false,
                            error: self.saveError,
                            errors: result.data && result.data.errors,
                        };
                    })
                    .catch(function () {
                        self.isSaving = false;
                        self.saveError = "Network error. Please check your connection.";
                        return { success: false, error: self.saveError };
                    });
            },

            // Save Preferences and advance to the Review step.
            saveAndNextStep4: function () {
                var self = this;
                self.savePreferences().then(function (result) {
                    if (result.success) {
                        self.saveError = "";
                        self.currentStep = 5;
                        self.updateReview();
                        window.scrollTo({ top: 0, behavior: "smooth" });
                    }
                });
            },

            // Clear save error (for dismissing error messages)
            clearSaveError: function () {
                this.saveError = "";
            },

            // =========================================================================
            // DRAFT AUTO-SAVE FUNCTIONALITY
            // =========================================================================

            // Draft state
            isDirty: false,
            isAutoSaving: false,
            lastSavedAt: null,
            autoSaveTimer: null,
            draftData: {},

            // CSP-safe getters for auto-save UI
            get showAutoSaving() {
                return this.isAutoSaving;
            },
            get showLastSaved() {
                return this.lastSavedAt !== null;
            },
            get lastSavedMessage() {
                if (!this.lastSavedAt) return "";
                var now = new Date();
                var saved = new Date(this.lastSavedAt);
                var diffMs = now - saved;
                var diffSec = Math.floor(diffMs / 1000);

                if (diffSec < 10) return "Saved just now";
                if (diffSec < 60) return "Saved " + diffSec + "s ago";
                var diffMin = Math.floor(diffSec / 60);
                if (diffMin < 60) return "Saved " + diffMin + "m ago";
                return "Saved earlier";
            },

            // Load draft data on init
            loadDraft: function () {
                var self = this;

                fetch("/api/profile/draft/get/")
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (result) {
                        if (result.success && result.data) {
                            self.draftData = result.data.merged || {};
                            self.lastSavedAt = result.data.last_saved;

                            self.populateFieldsFromDraft();
                        }
                    })
                    .catch(function (err) {
                        console.error("[DRAFT LOAD] Failed to load draft:", err);
                    });
            },

            // Populate form fields from draft data
            populateFieldsFromDraft: function () {
                var self = this;
                var form = this.$el.querySelector("form");

                if (!form) {
                    return;
                }

                for (var key in this.draftData) {
                    var value = this.draftData[key];

                    // CRITICAL: Skip file inputs (photos) - cannot be set programmatically for security
                    if (key === "photo_1" || key === "photo_2" || key === "photo_3") {
                        continue;
                    }

                    // Handle checkbox arrays (like event_languages)
                    if (Array.isArray(value)) {
                        var checkboxes = form.querySelectorAll('[name="' + key + '"]');

                        for (var i = 0; i < checkboxes.length; i++) {
                            var checkbox = checkboxes[i];
                            var shouldCheck = value.indexOf(checkbox.value) !== -1;
                            checkbox.checked = shouldCheck;
                        }
                        continue;
                    }

                    var input = form.querySelector('[name="' + key + '"]');

                    if (input) {
                        if (input.type === "checkbox") {
                            // Handle single checkbox - convert string 'true'/'false' or Python 'True'/'False' to boolean
                            var shouldCheck =
                                value === true ||
                                value === "true" ||
                                value === "1" ||
                                value === "True";
                            input.checked = shouldCheck;
                        } else if (input.type === "radio") {
                            // For radio buttons, find the one with matching value
                            var radios = form.querySelectorAll('[name="' + key + '"]');

                            for (var i = 0; i < radios.length; i++) {
                                if (radios[i].value === value) {
                                    radios[i].checked = true;
                                    break;
                                }
                            }
                        } else if (input.type !== "file") {
                            // Only set value for non-file inputs
                            input.value = value || "";
                        }
                    }
                }

                // Update component state from draft data
                if (this.draftData.phone_number) {
                    this.phoneNumber = this.draftData.phone_number;
                }
                if (this.draftData.date_of_birth) {
                    this.dateOfBirth = this.draftData.date_of_birth;
                    // Format the date for display in review (e.g., "1990-01-15" -> "Jan 15, 1990")
                    try {
                        var dateParts = this.draftData.date_of_birth.split("-");
                        if (dateParts.length === 3) {
                            var dateObj = new Date(
                                dateParts[0],
                                dateParts[1] - 1,
                                dateParts[2],
                            );
                            var months = [
                                "Jan",
                                "Feb",
                                "Mar",
                                "Apr",
                                "May",
                                "Jun",
                                "Jul",
                                "Aug",
                                "Sep",
                                "Oct",
                                "Nov",
                                "Dec",
                            ];
                            this.dobFormatted =
                                months[dateObj.getMonth()] +
                                " " +
                                dateObj.getDate() +
                                ", " +
                                dateObj.getFullYear();
                        }
                    } catch (e) {
                        this.dobFormatted = this.draftData.date_of_birth; // Fallback to raw value
                    }
                }
                if (this.draftData.gender) {
                    this.gender = this.draftData.gender;
                }
                if (this.draftData.location) {
                    this.location = this.draftData.location;
                    // Try to get the display name from the map
                    var locationInput = form.querySelector('[name="location"]');
                    if (locationInput) {
                        var mapContainer = document.querySelector(
                            '[x-data*="cantonMap"]',
                        );
                        if (mapContainer) {
                            var regionPath = document.getElementById(
                                this.draftData.location,
                            );
                            if (regionPath) {
                                this.locationName =
                                    regionPath.getAttribute("data-region-name") ||
                                    this.draftData.location;
                            } else {
                                this.locationName = this.draftData.location;
                            }
                        } else {
                            this.locationName = this.draftData.location;
                        }
                    }
                }

                // Update the review display after populating fields so Step 4 (Review)
                // shows the correct data when the page is refreshed mid-wizard.
                setTimeout(function () {
                    self.updateReview();
                }, 100);
            },

            // Setup auto-save event listeners
            setupAutoSaveListeners: function () {
                var self = this;
                var form = this.$el.querySelector("form");
                if (!form) return;

                // Text inputs and textareas: debounced save (2 seconds after typing stops)
                var textInputs = form.querySelectorAll(
                    'input[type="text"], input[type="tel"], input[type="date"], textarea',
                );
                for (var i = 0; i < textInputs.length; i++) {
                    textInputs[i].addEventListener("input", function () {
                        self.scheduleAutoSave();
                    });
                }

                // Dropdowns, radios, and checkboxes: immediate save
                var immediateInputs = form.querySelectorAll(
                    'select, input[type="radio"], input[type="checkbox"]',
                );
                for (var j = 0; j < immediateInputs.length; j++) {
                    immediateInputs[j].addEventListener("change", function () {
                        self.saveDraft();
                    });
                }
            },

            // Schedule auto-save with debounce (2 seconds)
            scheduleAutoSave: function () {
                clearTimeout(this.autoSaveTimer);
                this.isDirty = true;

                var self = this;
                this.autoSaveTimer = setTimeout(function () {
                    self.saveDraft();
                }, 2000); // 2-second debounce
            },

            // Save current step data to draft (no validation)
            saveDraft: function () {
                var self = this;
                self.isAutoSaving = true;

                var stepData = self.gatherCurrentStepData();

                var payload = {
                    step: self.currentStep,
                    data: stepData,
                };
                var jsonPayload = JSON.stringify(payload);

                fetch("/api/profile/draft/save/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": self.getCsrfToken(),
                    },
                    body: jsonPayload,
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (result) {
                        self.isAutoSaving = false;
                        self.isDirty = false;
                        if (result.success) {
                            self.lastSavedAt = result.saved_at;
                        } else {
                            console.error("[DRAFT SAVE] Save failed:", result.error);
                        }
                    })
                    .catch(function (err) {
                        self.isAutoSaving = false;
                        console.error("[DRAFT SAVE] ❌ Network error:", err);
                    });
            },

            // Gather current step form data
            gatherCurrentStepData: function () {
                var form = this.$el.querySelector("form");
                if (!form) {
                    console.warn("[GATHER] No form found");
                    return {};
                }

                var formData = new FormData(form);
                var data = {};
                var checkboxGroups = {}; // Track checkbox arrays

                // Collect all form data from FormData
                for (var pair of formData.entries()) {
                    var key = pair[0];
                    var value = pair[1];

                    // Skip file inputs (photos) - they're uploaded separately
                    if (key === "photo_1" || key === "photo_2" || key === "photo_3") {
                        continue;
                    }

                    // CRITICAL FIX: Skip checkboxes - they're handled separately below
                    // FormData returns each checked checkbox as a separate entry, which would
                    // overwrite the previous value instead of building an array
                    var field = form.querySelector('[name="' + key + '"]');
                    if (field && field.type === "checkbox") {
                        continue; // Let the checkbox handling code process these
                    }

                    // Only add non-empty values
                    if (value !== "") {
                        data[key] = value;
                    }
                }

                // CRITICAL: Handle checkboxes properly
                // Single checkboxes (privacy settings) are stored as boolean
                // Multiple checkboxes with same name (event_languages) are stored as array
                var checkboxes = form.querySelectorAll('input[type="checkbox"]');

                // First pass: identify checkbox groups (multiple checkboxes with same name)
                for (var i = 0; i < checkboxes.length; i++) {
                    var checkbox = checkboxes[i];
                    if (checkbox.name) {
                        if (!checkboxGroups[checkbox.name]) {
                            checkboxGroups[checkbox.name] = [];
                        }
                        checkboxGroups[checkbox.name].push(checkbox);
                    }
                }

                // Second pass: process each checkbox group
                for (var name in checkboxGroups) {
                    var group = checkboxGroups[name];

                    if (group.length === 1) {
                        // Single checkbox - store as boolean
                        data[name] = group[0].checked;
                    } else {
                        // Multiple checkboxes with same name - store as array of checked values
                        var checkedValues = [];
                        for (var j = 0; j < group.length; j++) {
                            if (group[j].checked) {
                                checkedValues.push(group[j].value);
                            }
                        }
                        data[name] = checkedValues;
                    }
                }

                return data;
            },

            // Get CSRF token from form
            getCsrfToken: function () {
                var token = document.querySelector("[name=csrfmiddlewaretoken]");
                return token ? token.value : "";
            },

            // Setup periodic checkpoint (every 60 seconds if dirty)
            setupPeriodicCheckpoint: function () {
                var self = this;
                setInterval(function () {
                    if (self.isDirty) {
                        self.saveDraft();
                    }
                }, 60000); // 60 seconds
            },

            // Warn before leaving with unsaved changes
            setupUnloadWarning: function () {
                var self = this;
                window.addEventListener("beforeunload", function (e) {
                    if (self.isDirty) {
                        e.preventDefault();
                        e.returnValue = "";
                    }
                });
            },
        };
    });

    // Canton Map component for location selection
    // Interactive SVG map for selecting Luxembourg cantons and border regions
    Alpine.data("cantonMap", function () {
        return {
            // State
            selectedRegion: "",
            selectedRegionName: "",
            hoveredRegion: "",
            hoveredRegionName: "",
            showFallbackDropdown: false,
            focusedIndex: -1,

            // Region data for keyboard navigation
            regions: [],

            // Computed getters for CSP compatibility
            get hasSelection() {
                return this.selectedRegion !== "";
            },
            get noSelection() {
                return this.selectedRegion === "";
            },
            get isHovering() {
                return this.hoveredRegion !== "";
            },
            get selectionLabel() {
                if (this.selectedRegionName) {
                    return this.selectedRegionName;
                }
                return (
                    this.$el.getAttribute("data-placeholder") ||
                    "Click the map to select your region"
                );
            },
            get hoverLabel() {
                return this.hoveredRegionName || "";
            },
            get fallbackDropdownVisible() {
                return this.showFallbackDropdown;
            },
            get selectedClass() {
                return this.hasSelection ? "has-selection" : "";
            },

            init: function () {
                var self = this;

                // Read initial value from data attributes
                var initialValue = this.$el.getAttribute("data-initial-value");
                var initialName = this.$el.getAttribute("data-initial-name");

                if (initialValue && initialValue !== "") {
                    this.selectedRegion = initialValue;
                    this.selectedRegionName = initialName || initialValue;
                    // Highlight the initially selected region
                    this.$nextTick(function () {
                        // Safety check: ensure the function exists before calling
                        if (typeof self._highlightRegion === "function") {
                            self._highlightRegion(initialValue);
                        }
                    });
                }

                // Build regions array for keyboard navigation
                this.$nextTick(function () {
                    self._buildRegionsArray();
                    self._setupEventDelegation();
                    self._setupKeyboardNavigation();
                });

                // Check for reduced motion preference
                if (
                    window.matchMedia &&
                    window.matchMedia("(prefers-reduced-motion: reduce)").matches
                ) {
                    this.$el.classList.add("reduce-motion");
                }
            },

            _buildRegionsArray: function () {
                var svg = this.$el.querySelector("svg");
                if (!svg) return;

                var paths = svg.querySelectorAll("[data-region-id]");
                this.regions = [];

                for (var i = 0; i < paths.length; i++) {
                    this.regions.push({
                        id: paths[i].getAttribute("data-region-id"),
                        name: paths[i].getAttribute("data-region-name"),
                        element: paths[i],
                    });
                }
            },

            _getBorderRegionAtPoint: function (svgX, svgY) {
                // France - South side (narrow bottom strip)
                if (svgY > 850) {
                    return {
                        id: "border-france",
                        name: "France (Thionville/Metz area)",
                    };
                }
                // Belgium - West side (left strip)
                if (svgX < 350) {
                    return { id: "border-belgium", name: "Belgium (Arlon area)" };
                }
                // Germany - East side (right strip)
                if (svgX > 350) {
                    return {
                        id: "border-germany",
                        name: "Germany (Trier/Saarland area)",
                    };
                }
                return null;
            },

            _screenToSVGCoords: function (svg, screenX, screenY) {
                var point = svg.createSVGPoint();
                point.x = screenX;
                point.y = screenY;
                var ctm = svg.getScreenCTM();
                if (ctm) {
                    return point.matrixTransform(ctm.inverse());
                }
                return point;
            },

            _setupEventDelegation: function () {
                var self = this;
                var svg = this.$el.querySelector("svg");
                if (!svg) return;

                // Click handler
                svg.addEventListener("click", function (e) {
                    var path = e.target.closest("[data-region-id]");

                    if (path && path.classList.contains("lux-canton")) {
                        self.selectRegion(
                            path.getAttribute("data-region-id"),
                            path.getAttribute("data-region-name"),
                        );
                        return;
                    }

                    if (path && path.classList.contains("border-region")) {
                        self.selectRegion(
                            path.getAttribute("data-region-id"),
                            path.getAttribute("data-region-name"),
                        );
                        return;
                    }

                    if (
                        e.target.classList.contains("map-background") ||
                        e.target.tagName === "svg"
                    ) {
                        var svgPoint = self._screenToSVGCoords(
                            svg,
                            e.clientX,
                            e.clientY,
                        );
                        var borderRegion = self._getBorderRegionAtPoint(
                            svgPoint.x,
                            svgPoint.y,
                        );
                        if (borderRegion) {
                            self.selectRegion(borderRegion.id, borderRegion.name);
                        }
                    }
                });

                // Mouseover handler
                svg.addEventListener("mouseover", function (e) {
                    var path = e.target.closest("[data-region-id]");
                    if (path) {
                        self.hoverRegion(
                            path.getAttribute("data-region-id"),
                            path.getAttribute("data-region-name"),
                        );
                    }
                });

                // Mouseout handler
                svg.addEventListener("mouseout", function (e) {
                    var path = e.target.closest("[data-region-id]");
                    if (path) {
                        self.clearHover();
                    }
                });

                // Touch events for mobile
                svg.addEventListener(
                    "touchstart",
                    function (e) {
                        var path = e.target.closest("[data-region-id]");
                        if (path) {
                            self.hoverRegion(
                                path.getAttribute("data-region-id"),
                                path.getAttribute("data-region-name"),
                            );
                        }
                    },
                    { passive: true },
                );

                svg.addEventListener("touchend", function (e) {
                    var path = e.target.closest("[data-region-id]");
                    if (path) {
                        self.selectRegion(
                            path.getAttribute("data-region-id"),
                            path.getAttribute("data-region-name"),
                        );
                        self.clearHover();
                    }
                });
            },

            _setupKeyboardNavigation: function () {
                var self = this;
                var svg = this.$el.querySelector("svg");
                if (!svg) return;

                svg.addEventListener("keydown", function (e) {
                    var key = e.key;
                    if (key === "ArrowDown" || key === "ArrowRight") {
                        e.preventDefault();
                        self._navigateNext();
                    } else if (key === "ArrowUp" || key === "ArrowLeft") {
                        e.preventDefault();
                        self._navigatePrevious();
                    } else if (key === "Enter" || key === " ") {
                        e.preventDefault();
                        self._selectFocused();
                    } else if (key === "Home") {
                        e.preventDefault();
                        self._focusFirst();
                    } else if (key === "End") {
                        e.preventDefault();
                        self._focusLast();
                    }
                });

                svg.addEventListener("focus", function () {
                    if (self.focusedIndex < 0 && self.regions.length > 0) {
                        var selectedIndex = self._getSelectedIndex();
                        self.focusedIndex = selectedIndex >= 0 ? selectedIndex : 0;
                        self._applyFocus();
                    }
                });

                svg.addEventListener("blur", function () {
                    self._clearFocus();
                });
            },

            _navigateNext: function () {
                if (this.regions.length === 0) return;
                this.focusedIndex = (this.focusedIndex + 1) % this.regions.length;
                this._applyFocus();
            },

            _navigatePrevious: function () {
                if (this.regions.length === 0) return;
                this.focusedIndex =
                    (this.focusedIndex - 1 + this.regions.length) % this.regions.length;
                this._applyFocus();
            },

            _focusFirst: function () {
                if (this.regions.length === 0) return;
                this.focusedIndex = 0;
                this._applyFocus();
            },

            _focusLast: function () {
                if (this.regions.length === 0) return;
                this.focusedIndex = this.regions.length - 1;
                this._applyFocus();
            },

            _selectFocused: function () {
                if (this.focusedIndex >= 0 && this.focusedIndex < this.regions.length) {
                    var region = this.regions[this.focusedIndex];
                    this.selectRegion(region.id, region.name);
                }
            },

            _getSelectedIndex: function () {
                for (var i = 0; i < this.regions.length; i++) {
                    if (this.regions[i].id === this.selectedRegion) {
                        return i;
                    }
                }
                return -1;
            },

            _applyFocus: function () {
                this._clearFocus();
                if (this.focusedIndex >= 0 && this.focusedIndex < this.regions.length) {
                    var region = this.regions[this.focusedIndex];
                    region.element.classList.add("region-focused");
                    this.hoverRegion(region.id, region.name);
                }
            },

            _clearFocus: function () {
                var svg = this.$el.querySelector("svg");
                if (!svg) return;
                var focusedElements = svg.querySelectorAll(".region-focused");
                for (var i = 0; i < focusedElements.length; i++) {
                    focusedElements[i].classList.remove("region-focused");
                }
            },

            selectRegion: function (regionId, regionName) {
                if (this.selectedRegion) {
                    this._unhighlightRegion(this.selectedRegion);
                }
                this.selectedRegion = regionId;
                this.selectedRegionName = regionName;
                this._highlightRegion(regionId);

                var hiddenInput = document.getElementById("id_location");
                if (hiddenInput) {
                    hiddenInput.value = regionId;
                    hiddenInput.dispatchEvent(new Event("change", { bubbles: true }));
                }

                // Dispatch global event for profileWizard validation tracking
                window.dispatchEvent(
                    new CustomEvent("location-selected", {
                        detail: { location: regionId, name: regionName },
                    }),
                );

                this.$dispatch("region-selected", { id: regionId, name: regionName });
            },

            hoverRegion: function (regionId, regionName) {
                this.hoveredRegion = regionId;
                this.hoveredRegionName = regionName;
                var path = document.getElementById(regionId);
                if (path && !path.classList.contains("region-selected")) {
                    path.classList.add("region-hover");
                }
            },

            clearHover: function () {
                if (this.hoveredRegion) {
                    var prevPath = document.getElementById(this.hoveredRegion);
                    if (prevPath) {
                        prevPath.classList.remove("region-hover");
                    }
                }
                this.hoveredRegion = "";
                this.hoveredRegionName = "";
            },

            toggleFallbackDropdown: function () {
                this.showFallbackDropdown = !this.showFallbackDropdown;
            },

            handleFallbackSelect: function (event) {
                var select = event.target;
                var option = select.options[select.selectedIndex];
                if (option && option.value) {
                    this.selectRegion(option.value, option.text);
                }
            },

            _highlightRegion: function (regionId) {
                var path = document.getElementById(regionId);
                if (path) {
                    path.classList.add("region-selected");
                    path.classList.remove("region-hover");
                    path.setAttribute("aria-selected", "true");
                }
            },

            _unhighlightRegion: function (regionId) {
                var path = document.getElementById(regionId);
                if (path) {
                    path.classList.remove("region-selected");
                    path.setAttribute("aria-selected", "false");
                }
            },
        };
    });

    // Date of Birth Picker component - stepped selection for better UX
    // 4 steps: 1) Age range chips, 2) Year selection, 3) Month selection, 4) Day selection
    // CSP-compatible: Uses DOM manipulation for dynamic content (x-for not CSP-safe)
    // Reads initial value from hidden input with name="date_of_birth"
    Alpine.data("dobPicker", function () {
        return {
            // State
            step: 1, // 1=age range, 2=year, 3=month, 4=day
            selectedAgeRange: "",
            selectedYear: null,
            selectedMonth: null,
            selectedDay: null,
            _translatedMonths: null,
            _clsUnselected:
                "border-gray-200 bg-white dark:bg-gray-800 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:border-purple-300 hover:bg-purple-50 dark:hover:bg-purple-900/30",
            _clsSelected:
                "border-purple-500 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:border-purple-400 dark:text-purple-300",

            // Age range definitions (computed from current year)
            get ageRanges() {
                var y = new Date().getFullYear();
                return [
                    {
                        label: "18-25",
                        emoji: "\u{1F331}",
                        minYear: y - 25,
                        maxYear: y - 18,
                    },
                    {
                        label: "26-35",
                        emoji: "\u{1F33F}",
                        minYear: y - 35,
                        maxYear: y - 26,
                    },
                    {
                        label: "36-45",
                        emoji: "\u{1F333}",
                        minYear: y - 45,
                        maxYear: y - 36,
                    },
                    {
                        label: "46-55",
                        emoji: "\u{1F342}",
                        minYear: y - 55,
                        maxYear: y - 46,
                    },
                    {
                        label: "56-65",
                        emoji: "\u{1F341}",
                        minYear: y - 65,
                        maxYear: y - 56,
                    },
                    {
                        label: "66+",
                        emoji: "\u{1F31F}",
                        minYear: y - 100,
                        maxYear: y - 66,
                    },
                ];
            },

            // CSP-safe computed getters
            get isStep1() {
                return this.step === 1;
            },
            get isStep2() {
                return this.step === 2;
            },
            get isStep3() {
                return this.step === 3;
            },
            get isStep4() {
                return this.step === 4;
            },
            get hasAgeRange() {
                return this.selectedAgeRange !== "";
            },
            get hasYear() {
                return this.selectedYear !== null;
            },
            get hasMonth() {
                return this.selectedMonth !== null;
            },
            get hasDay() {
                return this.selectedDay !== null;
            },
            get isComplete() {
                return this.hasYear && this.hasMonth && this.hasDay;
            },
            get notComplete() {
                return !this.isComplete;
            },

            // Breadcrumb display text getters
            get yearBreadcrumbText() {
                return this.selectedYear ? String(this.selectedYear) : "";
            },
            get monthBreadcrumbText() {
                return this.selectedMonthName || "";
            },
            get dayBreadcrumbText() {
                return this.selectedDay ? String(this.selectedDay) : "";
            },

            get months() {
                if (this._translatedMonths) return this._translatedMonths;
                // Default English month names (will be overridden by data-months attribute)
                return [
                    { num: 1, name: "January" },
                    { num: 2, name: "February" },
                    { num: 3, name: "March" },
                    { num: 4, name: "April" },
                    { num: 5, name: "May" },
                    { num: 6, name: "June" },
                    { num: 7, name: "July" },
                    { num: 8, name: "August" },
                    { num: 9, name: "September" },
                    { num: 10, name: "October" },
                    { num: 11, name: "November" },
                    { num: 12, name: "December" },
                ];
            },

            get isoDate() {
                if (!this.isComplete) return "";
                var m =
                    this.selectedMonth < 10
                        ? "0" + this.selectedMonth
                        : this.selectedMonth;
                var d =
                    this.selectedDay < 10 ? "0" + this.selectedDay : this.selectedDay;
                return this.selectedYear + "-" + m + "-" + d;
            },

            get formattedDate() {
                if (!this.isComplete) return "";
                var monthObj = this._findMonth(this.selectedMonth);
                var monthName = monthObj ? monthObj.name : this.selectedMonth;
                return this.selectedDay + " " + monthName + " " + this.selectedYear;
            },

            get selectedMonthName() {
                if (!this.selectedMonth) return "";
                var monthObj = this._findMonth(this.selectedMonth);
                return monthObj ? monthObj.name : "";
            },

            // Breadcrumb button class getters (with dark mode)
            get step1ButtonClass() {
                return this.isStep1
                    ? "bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-medium"
                    : "text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20";
            },
            get step2ButtonClass() {
                return this.isStep2
                    ? "bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-medium"
                    : "text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20";
            },
            get step3ButtonClass() {
                return this.isStep3
                    ? "bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-medium"
                    : "text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20";
            },
            get step4ButtonClass() {
                return this.isStep4
                    ? "bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-medium"
                    : "text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20";
            },

            // Breadcrumb visibility getters
            get showAgeRangeBreadcrumb() {
                return this.step > 1;
            },
            get showYearBreadcrumb() {
                return this.step > 2;
            },
            get showMonthBreadcrumb() {
                return this.step > 3;
            },

            init: function () {
                var self = this;

                // Load translated month names from data attribute
                var monthsData = this.$el.getAttribute("data-months");
                if (monthsData) {
                    try {
                        this._translatedMonths = JSON.parse(monthsData);
                    } catch (e) {
                        console.warn("dobPicker: Could not parse months data", e);
                    }
                }

                // Restore state from data-initial-dob BEFORE the first render
                // so x-show="isStepN" picks the right step on the first pass.
                // This mutates `this.step` and the selected* fields, so when
                // Alpine evaluates the getters right after init() returns,
                // they already reflect the prefilled value.
                this._parseInitialDate();

                // Render the server-empty containers (age ranges, and if we
                // prefilled, the year/month/day grids too). _parseInitialDate
                // handled the renders for the prefilled path; this covers the
                // blank-profile path where we still need the age chips.
                this.$nextTick(function () {
                    if (!self.selectedAgeRange) {
                        self._renderAgeRanges();
                    }
                });

                // Listen for external resets if needed
                this.$el.addEventListener("dob-reset", function () {
                    self.step = 1;
                    self.selectedAgeRange = "";
                    self.selectedYear = null;
                    self.selectedMonth = null;
                    self.selectedDay = null;
                    self._updateHiddenInput();
                    self._renderAgeRanges();
                });
            },

            _findMonth: function (num) {
                var months = this.months;
                for (var i = 0; i < months.length; i++) {
                    if (months[i].num === num) return months[i];
                }
                return null;
            },

            _getDaysInMonth: function (year, month) {
                // Month is 1-based, Date uses 0-based months
                // Using day 0 of next month gives last day of current month
                return new Date(year, month, 0).getDate();
            },

            _validateSelectedDay: function () {
                if (this.selectedDay && this.selectedMonth && this.selectedYear) {
                    var maxDay = this._getDaysInMonth(
                        this.selectedYear,
                        this.selectedMonth,
                    );
                    if (this.selectedDay > maxDay) {
                        this.selectedDay = null;
                        this._updateCompletionSummary();
                    }
                }
            },

            _parseInitialDate: function () {
                // Prefer the data-initial-dob attribute on the component
                // root (set by the server template) so we don't depend on
                // when Alpine decides to scan the inner hidden input during
                // $nextTick. Fall back to the hidden input for callers that
                // run after the user picks a date.
                var initialValue = this.$el.getAttribute("data-initial-dob") || "";
                if (!initialValue) {
                    var hiddenInput = this.$el.querySelector(
                        'input[name="date_of_birth"]',
                    );
                    initialValue = (hiddenInput && hiddenInput.value) || "";
                }
                if (!initialValue) return;

                var parts = initialValue.split("-");
                if (parts.length !== 3) return;

                var year = parseInt(parts[0], 10);
                var month = parseInt(parts[1], 10);
                var day = parseInt(parts[2], 10);

                if (isNaN(year) || isNaN(month) || isNaN(day)) return;

                // Find matching age range
                var self = this;
                var matchingRange = null;
                for (var i = 0; i < this.ageRanges.length; i++) {
                    var r = this.ageRanges[i];
                    if (year >= r.minYear && year <= r.maxYear) {
                        matchingRange = r;
                        break;
                    }
                }

                if (matchingRange) {
                    this.selectedAgeRange = matchingRange.label;
                    this.selectedYear = year;
                    this.selectedMonth = month;
                    this.selectedDay = day;
                    this.step = 4; // Show completion state
                    // Re-render with initial values
                    this._renderAgeRanges();
                    this._renderYears();
                    this._renderMonths();
                    this._renderDays();
                    this._updateCompletionSummary();
                }
            },

            _updateHiddenInput: function () {
                var hiddenInput = this.$el.querySelector('input[name="date_of_birth"]');
                if (hiddenInput) {
                    hiddenInput.value = this.isoDate;
                    hiddenInput.dispatchEvent(new Event("change", { bubbles: true }));
                }
            },

            _dispatchDateSelected: function () {
                window.dispatchEvent(
                    new CustomEvent("dob-selected", {
                        detail: {
                            iso: this.isoDate,
                            formatted: this.formattedDate,
                            year: this.selectedYear,
                            month: this.selectedMonth,
                            day: this.selectedDay,
                        },
                    }),
                );
            },

            // DOM rendering methods (CSP-safe alternative to x-for)
            _renderAgeRanges: function () {
                var container = this.$el.querySelector("[data-dob-age-ranges]");
                if (!container) return;

                var self = this;
                container.innerHTML = "";

                this.ageRanges.forEach(function (range) {
                    var btn = document.createElement("button");
                    btn.type = "button";
                    btn.className =
                        "w-full h-full min-h-[3.5rem] flex items-center justify-center gap-2 px-3 py-3 rounded-xl text-sm font-medium border-2 transition-all duration-200 hover:scale-105";
                    btn.className +=
                        self.selectedAgeRange === range.label
                            ? " " + self._clsSelected
                            : " " + self._clsUnselected;
                    btn.innerHTML =
                        '<span class="text-xl flex-shrink-0">' +
                        range.emoji +
                        '</span><span class="whitespace-nowrap">' +
                        range.label +
                        "</span>";
                    btn.addEventListener("click", function () {
                        self.selectAgeRange(range.label);
                    });
                    container.appendChild(btn);
                });
            },

            _renderYears: function () {
                var container = this.$el.querySelector("[data-dob-years]");
                if (!container) return;

                var self = this;
                container.innerHTML = "";

                if (!this.selectedAgeRange) return;

                // Find the selected age range
                var range = null;
                for (var i = 0; i < this.ageRanges.length; i++) {
                    if (this.ageRanges[i].label === this.selectedAgeRange) {
                        range = this.ageRanges[i];
                        break;
                    }
                }
                if (!range) return;

                for (var y = range.maxYear; y >= range.minYear; y--) {
                    (function (year) {
                        var btn = document.createElement("button");
                        btn.type = "button";
                        btn.className =
                            "px-3 py-2 rounded-lg text-sm font-medium border-2 transition-all duration-150 hover:scale-105";
                        btn.className +=
                            self.selectedYear === year
                                ? " " + self._clsSelected
                                : " " + self._clsUnselected;
                        btn.textContent = year;
                        btn.addEventListener("click", function () {
                            self.selectYear(year);
                        });
                        container.appendChild(btn);
                    })(y);
                }
            },

            _renderMonths: function () {
                var container = this.$el.querySelector("[data-dob-months]");
                if (!container) return;

                var self = this;
                container.innerHTML = "";

                this.months.forEach(function (month) {
                    var btn = document.createElement("button");
                    btn.type = "button";
                    btn.className =
                        "px-3 py-2.5 rounded-lg text-sm font-medium border-2 transition-all duration-150 hover:scale-105";
                    btn.className +=
                        self.selectedMonth === month.num
                            ? " " + self._clsSelected
                            : " " + self._clsUnselected;
                    btn.textContent = month.name;
                    btn.addEventListener("click", function () {
                        self.selectMonth(month.num);
                    });
                    container.appendChild(btn);
                });
            },

            _renderDays: function () {
                var container = this.$el.querySelector("[data-dob-days]");
                if (!container) return;

                var self = this;
                container.innerHTML = "";

                if (!this.selectedMonth || !this.selectedYear) return;

                var daysInMonth = this._getDaysInMonth(
                    this.selectedYear,
                    this.selectedMonth,
                );

                for (var d = 1; d <= daysInMonth; d++) {
                    (function (day) {
                        var btn = document.createElement("button");
                        btn.type = "button";
                        btn.className =
                            "w-full aspect-square flex items-center justify-center rounded-lg text-sm font-medium border-2 transition-all duration-150 hover:scale-105";
                        btn.className +=
                            self.selectedDay === day
                                ? " " + self._clsSelected
                                : " " + self._clsUnselected;
                        btn.textContent = day;
                        btn.addEventListener("click", function () {
                            self.selectDay(day);
                        });
                        container.appendChild(btn);
                    })(d);
                }
            },

            _updateCompletionSummary: function () {
                var summary = this.$el.querySelector("[data-dob-summary]");
                if (summary) {
                    if (this.isComplete) {
                        summary.classList.remove("hidden");
                        var dateDisplay = summary.querySelector("[data-dob-formatted]");
                        if (dateDisplay) {
                            dateDisplay.textContent = this.formattedDate;
                        }
                    } else {
                        summary.classList.add("hidden");
                    }
                }
            },

            // Selection methods
            selectAgeRange: function (label) {
                this.selectedAgeRange = label;
                this.selectedYear = null;
                this.selectedMonth = null;
                this.selectedDay = null;
                this.step = 2;
                this._updateHiddenInput();
                this._renderAgeRanges();
                this._renderYears();
            },

            selectYear: function (year) {
                this.selectedYear = year;
                this._validateSelectedDay();
                this.selectedMonth = null;
                this.selectedDay = null;
                this.step = 3;
                this._updateHiddenInput();
                this._renderYears();
                this._renderMonths();
            },

            selectMonth: function (month) {
                this.selectedMonth = month;
                this._validateSelectedDay();
                this.selectedDay = null;
                this.step = 4;
                this._updateHiddenInput();
                this._renderMonths();
                this._renderDays();
            },

            selectDay: function (day) {
                this.selectedDay = day;
                this._updateHiddenInput();
                this._renderDays();
                this._updateCompletionSummary();
                this._dispatchDateSelected();
            },

            // Navigation methods for breadcrumb
            goToStep1: function () {
                this.step = 1;
                this._renderAgeRanges();
            },

            goToStep2: function () {
                if (this.hasAgeRange) {
                    this.step = 2;
                    this._renderYears();
                }
            },

            goToStep3: function () {
                if (this.hasYear) {
                    this.step = 3;
                    this._renderMonths();
                }
            },

            goToStep4: function () {
                if (this.hasMonth) {
                    this._validateSelectedDay();
                    this.step = 4;
                    this._renderDays();
                    this._updateCompletionSummary();
                }
            },
        };
    });

    // Phone verification component for the phone input field
    // Used in profile creation - wraps the phone input with verification logic
    // Reads initial state from data attributes: data-verified, data-phone-input-id
    Alpine.data("phoneVerificationComponent", function () {
        return {
            verified: false,
            canVerify: false,
            errorMessage: "",
            iti: null,
            phoneInputId: "",
            failureCount: 0,

            // Computed getters for CSP compatibility
            get notVerified() {
                return !this.verified;
            },
            get showSupportContact() {
                return this.failureCount >= 2;
            },
            get cannotVerify() {
                return !this.canVerify;
            },
            get verifiedValue() {
                return this.verified ? "true" : "false";
            },
            get verifyButtonPulseClass() {
                return this.canVerify ? "animate-pulse-subtle" : "";
            },
            get phoneHintClass() {
                return !this.verified ? "text-purple-600 font-medium" : "text-gray-500";
            },

            init: function () {
                var self = this;

                // Read initial state from data attributes
                var verifiedAttr = this.$el.getAttribute("data-verified");
                this.verified = verifiedAttr === "true";

                this.phoneInputId =
                    this.$el.getAttribute("data-phone-input-id") || "id_phone_number";

                var phoneInput = document.getElementById(this.phoneInputId);
                if (
                    phoneInput &&
                    typeof window.intlTelInput === "function" &&
                    !this.verified
                ) {
                    var prefilledValue = phoneInput.value;
                    phoneInput.value = "";

                    try {
                        this.iti = window.intlTelInput(phoneInput, {
                            initialCountry: "lu",
                            preferredCountries: ["lu", "de", "fr", "be"],
                            onlyCountries: [
                                "lu",
                                "de",
                                "fr",
                                "be",
                                "nl",
                                "ch",
                                "at",
                                "it",
                                "es",
                                "pt",
                                "gb",
                                "ie",
                                "us",
                                "ca",
                                "se",
                                "dz",
                            ],
                            separateDialCode: true,
                            nationalMode: false,
                            formatOnDisplay: true,
                            autoPlaceholder: "aggressive",
                            utilsScript:
                                "https://cdn.jsdelivr.net/npm/intl-tel-input@18.5.3/build/js/utils.js",
                        });

                        window.itiInstance = this.iti;

                        this.iti.promise
                            .then(function () {
                                if (prefilledValue) {
                                    var num = prefilledValue.trim().replace(/\s/g, "");
                                    if (!num.startsWith("+")) {
                                        if (num.startsWith("00"))
                                            num = "+" + num.slice(2);
                                        else num = "+352" + num.replace(/^0+/, "");
                                    }
                                    self.iti.setNumber(num);
                                }
                            })
                            .catch(function (err) {
                                console.warn(
                                    "intl-tel-input: Utils loading error",
                                    err,
                                );
                            });
                    } catch (err) {
                        console.error("intl-tel-input: Initialization error", err);
                    }
                }

                // Listen for verification success event
                window.addEventListener("phone-verified", function (e) {
                    self.verified = true;
                    var phoneInput = document.getElementById(self.phoneInputId);
                    if (phoneInput && e.detail) {
                        if (self.iti) {
                            // Use setNumber() to keep intl-tel-input in sync, then
                            // destroy the instance so it can't strip the dial code later
                            self.iti.setNumber(e.detail);
                        }
                        // Set the raw input value to full E.164 number as a safety net
                        phoneInput.value = e.detail;
                        phoneInput.readOnly = true;
                        // Destroy intl-tel-input instance - phone is now verified and locked,
                        // we don't want the library interfering with the value on form submit
                        if (self.iti) {
                            try {
                                self.iti.destroy();
                            } catch (err) {
                                console.warn(
                                    "intl-tel-input: Could not destroy after verification",
                                    err,
                                );
                            }
                            self.iti = null;
                            window.itiInstance = null;
                        }
                    }
                });
            },

            // Cleanup intl-tel-input when component is destroyed (prevents memory leaks)
            destroy: function () {
                if (this.iti) {
                    try {
                        this.iti.destroy();
                    } catch (e) {
                        console.warn("intl-tel-input: Could not destroy instance", e);
                    }
                    this.iti = null;
                    window.itiInstance = null;
                }
            },

            // CSP-compatible: no event parameter needed, uses this.$el
            onPhoneInput: function () {
                if (this.iti) {
                    this.canVerify =
                        this.iti.isValidNumber() || this.iti.getNumber().length >= 8;
                } else {
                    // Get value from the phone input element
                    var phoneInput = document.getElementById(this.phoneInputId);
                    this.canVerify = phoneInput && phoneInput.value.trim().length >= 6;
                }
                this.errorMessage = "";
            },

            startVerification: function () {
                var self = this;
                if (this.iti && !this.iti.isValidNumber()) {
                    this.errorMessage = "Please enter a valid phone number";
                    return;
                }

                // Get phone number
                var phoneNumber = this.iti
                    ? this.iti.getNumber()
                    : document.getElementById(this.phoneInputId).value;

                // Check if phone is already taken BEFORE sending SMS
                var csrfToken = document.querySelector(
                    'input[name="csrfmiddlewaretoken"]',
                );
                fetch("/api/phone/check-available/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "X-CSRFToken": csrfToken ? csrfToken.value : "",
                    },
                    credentials: "same-origin",
                    body: JSON.stringify({ phone_number: phoneNumber }),
                })
                    .then(function (response) {
                        if (response.status === 429) {
                            return response.json().catch(function () { return null; }).then(function (data) {
                                self.errorMessage =
                                    (data && data.error) ||
                                    gettext("Too many attempts. Please wait a few minutes before trying again.");
                                return null;
                            });
                        }
                        if (!response.ok) {
                            // Non-fatal — proceed; backend will still catch duplicates at verify time
                            self._doStartVerification(phoneNumber);
                            return null;
                        }
                        return response.json();
                    })
                    .then(function (data) {
                        if (data === null) {
                            // 429 already handled, or non-fatal non-OK already dispatched above
                            return;
                        }
                        if (!data.available) {
                            self.errorMessage =
                                data.error || "This phone number is already in use";
                            return;
                        }
                        // Phone is available - open modal and send SMS
                        self._doStartVerification(phoneNumber);
                    })
                    .catch(function () {
                        // Network failure — proceed; backend will still catch duplicates
                        self._doStartVerification(phoneNumber);
                    });
            },

            _doStartVerification: function (phoneNumber) {
                var self = this;

                // Dispatch event to open modal
                window.dispatchEvent(new CustomEvent("open-phone-modal"));

                // Initialize phone verification
                if (window.phoneVerification) {
                    window.phoneVerification
                        .sendVerificationCode(phoneNumber)
                        .then(function (result) {
                            if (result.success) {
                                self.failureCount = 0;
                            } else {
                                self.failureCount =
                                    window.phoneVerification.getFailureCount();
                                self.errorMessage = result.error;
                                // Update modal's failure count and show error there too
                                var modal = document.querySelector(
                                    '[x-data="phoneVerificationModal"]',
                                );
                                if (
                                    modal &&
                                    modal._x_dataStack &&
                                    modal._x_dataStack[0]
                                ) {
                                    var modalData = modal._x_dataStack[0];
                                    modalData.failureCount = self.failureCount;
                                    modalData.error = result.error;
                                    modalData.step = "code";
                                }
                            }
                        });
                }
            },

            resetVerification: function () {
                this.verified = false;
                this.canVerify = false;
                var phoneInput = document.getElementById(this.phoneInputId);
                if (phoneInput) {
                    phoneInput.readOnly = false;
                    phoneInput.value = "";
                    phoneInput.focus();
                }
                // Update parent Alpine state
                this.$dispatch("phone-unverified");
            },
        };
    });

    // Language switcher dropdown component (desktop navbar)
    // CSP-compatible component for changing site language
    Alpine.data("languageSwitcher", function () {
        return {
            langOpen: false,

            // Computed getters for CSP compatibility
            get isOpen() {
                return this.langOpen;
            },
            get isClosed() {
                return !this.langOpen;
            },
            get ariaExpanded() {
                return this.langOpen ? "true" : "false";
            },
            get chevronClass() {
                return this.langOpen ? "rotate-180" : "";
            },

            toggle: function () {
                this.langOpen = !this.langOpen;
            },
            close: function () {
                this.langOpen = false;
            },
        };
    });

    // Auto-submit language select on change (mobile version)
    // Uses event delegation for CSP compliance
    // Also updates the 'next' URL to use the correct language prefix
    (function () {
        document.addEventListener("change", function (event) {
            if (event.target.classList.contains("lang-select-auto-submit")) {
                var form = event.target.closest("form");
                if (form) {
                    // Update the 'next' hidden input with the correct localized URL
                    var nextInput = form.querySelector('input[name="next"]');
                    var currentPath =
                        form.dataset.currentPath || window.location.pathname;
                    var selectedLang = event.target.value;

                    if (nextInput && currentPath) {
                        // Replace language prefix in path (e.g., /en/about/ -> /de/about/)
                        // Pattern matches /xx/ at the start where xx is a 2-letter language code
                        var newPath = currentPath.replace(
                            /^\/[a-z]{2}\//,
                            "/" + selectedLang + "/",
                        );
                        // If path didn't have a language prefix, add one
                        if (
                            newPath === currentPath &&
                            !currentPath.match(/^\/[a-z]{2}\//)
                        ) {
                            newPath = "/" + selectedLang + currentPath;
                        }
                        // Preserve query string (e.g., ?section=account)
                        if (window.location.search) {
                            newPath = newPath + window.location.search;
                        }
                        nextInput.value = newPath;
                    }
                    form.submit();
                }
            }
        });
    })();

    // Phone verification modal component
    // Used in profile creation for SMS verification with Firebase
    // Reads phone input ID from data attribute: data-phone-input-id
    Alpine.data("phoneVerificationModal", function () {
        return {
            isOpen: false,
            step: "sending", // sending, code, verifying, success
            // CSP-compatible: individual properties instead of array (array index access requires eval)
            otp0: "",
            otp1: "",
            otp2: "",
            otp3: "",
            otp4: "",
            otp5: "",
            error: "",
            maskedPhone: "",
            resendCountdown: 60,
            resendTimer: null,
            failureCount: 0,
            phoneAlreadyInUse: false,

            // Computed getters for CSP compatibility
            get showPhoneInUseError() {
                return this.phoneAlreadyInUse;
            },
            get isSendingStep() {
                return this.step === "sending";
            },
            get isCodeStep() {
                return this.step === "code";
            },
            get isVerifyingStep() {
                return this.step === "verifying";
            },
            get isSuccessStep() {
                return this.step === "success";
            },
            get canResend() {
                return this.resendCountdown === 0;
            },
            get cannotResend() {
                return this.resendCountdown > 0;
            },
            get hasError() {
                return Boolean(this.error);
            },
            get showSupportContact() {
                return this.failureCount >= 2;
            },
            // CSP-compatible: getter combines individual OTP fields
            get otpCode() {
                return (
                    this.otp0 +
                    this.otp1 +
                    this.otp2 +
                    this.otp3 +
                    this.otp4 +
                    this.otp5
                );
            },
            get isCodeComplete() {
                return this.otpCode.length === 6;
            },
            get isCodeIncomplete() {
                return this.otpCode.length !== 6;
            },

            // CSP-compatible: combined update + navigation handlers (no $event in template)
            // Each method updates its field and handles focus navigation
            handleOtp0Input: function () {
                var el = this.$refs.otp0;
                this.otp0 = el.value;
                if (el.value.length === 1) {
                    this.$refs.otp1.focus();
                }
            },
            handleOtp1Input: function () {
                var el = this.$refs.otp1;
                this.otp1 = el.value;
                if (el.value.length === 1) {
                    this.$refs.otp2.focus();
                }
            },
            handleOtp2Input: function () {
                var el = this.$refs.otp2;
                this.otp2 = el.value;
                if (el.value.length === 1) {
                    this.$refs.otp3.focus();
                }
            },
            handleOtp3Input: function () {
                var el = this.$refs.otp3;
                this.otp3 = el.value;
                if (el.value.length === 1) {
                    this.$refs.otp4.focus();
                }
            },
            handleOtp4Input: function () {
                var el = this.$refs.otp4;
                this.otp4 = el.value;
                if (el.value.length === 1) {
                    this.$refs.otp5.focus();
                }
            },
            handleOtp5Input: function () {
                var el = this.$refs.otp5;
                this.otp5 = el.value;
                if (el.value.length === 1) {
                    this.verifyCode();
                }
            },

            init: function () {
                // Listen for modal open event
                var self = this;
                window.addEventListener("open-phone-modal", function () {
                    self.open();
                });

                // Keyboard navigation: Escape key closes modal
                document.addEventListener("keydown", function (e) {
                    if (e.key === "Escape" && self.isOpen) {
                        self.close();
                    }
                });
            },

            open: function () {
                // Clear any existing timer before opening (prevents memory leak from reopening)
                if (this.resendTimer) {
                    clearInterval(this.resendTimer);
                    this.resendTimer = null;
                }
                this.isOpen = true;
                this.step = "sending";
                this.error = "";
                this.phoneAlreadyInUse = false;
                // CSP-compatible: reset individual OTP fields
                this.otp0 = "";
                this.otp1 = "";
                this.otp2 = "";
                this.otp3 = "";
                this.otp4 = "";
                this.otp5 = "";
                this.resendCountdown = 0;

                // Prevent body scroll when modal is open
                document.body.style.overflow = "hidden";
            },

            close: function () {
                this.isOpen = false;
                // Clear timer on close
                if (this.resendTimer) {
                    clearInterval(this.resendTimer);
                    this.resendTimer = null;
                }

                // Restore body scroll
                document.body.style.overflow = "";
            },

            showCodeStep: function (phone) {
                this.step = "code";
                this.maskedPhone = phone.slice(0, 7) + "***" + phone.slice(-2);
                this.startResendTimer();
                var self = this;
                this.$nextTick(function () {
                    if (self.$refs && self.$refs.otp0) {
                        self.$refs.otp0.focus();
                    }
                });
            },

            // CSP-compatible: get index from data-index attribute on element
            handleOtpInput: function (event) {
                var el = event.target || this.$el;
                var index = parseInt(el.dataset.index, 10);
                var value = el.value;
                if (value.length === 1 && index < 5) {
                    var nextRef = this.$refs["otp" + (index + 1)];
                    if (nextRef) nextRef.focus();
                }
                if (index === 5 && value.length === 1) {
                    this.verifyCode();
                }
            },

            // CSP-compatible: get index from data-index attribute on element
            handleOtpBackspace: function (event) {
                var el = event.target || this.$el;
                var index = parseInt(el.dataset.index, 10);
                if (!el.value && index > 0) {
                    var prevRef = this.$refs["otp" + (index - 1)];
                    if (prevRef) prevRef.focus();
                }
            },

            // CSP-compatible: no parameter needed
            handleOtpPaste: function (event) {
                event.preventDefault();
                var paste = (event.clipboardData || window.clipboardData).getData(
                    "text",
                );
                var digits = paste.replace(/\D/g, "").slice(0, 6).split("");
                // CSP-compatible: set individual OTP fields
                this.otp0 = digits[0] || "";
                this.otp1 = digits[1] || "";
                this.otp2 = digits[2] || "";
                this.otp3 = digits[3] || "";
                this.otp4 = digits[4] || "";
                this.otp5 = digits[5] || "";
                if (digits.length === 6) this.verifyCode();
            },

            verifyCode: function () {
                var self = this;
                var code = this.otpCode;
                if (code.length !== 6) {
                    this.error = "Please enter the 6-digit code";
                    return;
                }

                this.step = "verifying";
                this.error = "";

                if (window.phoneVerification) {
                    window.phoneVerification.verifyCode(code).then(function (result) {
                        if (result.success) {
                            self.step = "success";
                            // Update phone verification state
                            window.dispatchEvent(
                                new CustomEvent("phone-verified", {
                                    detail: result.phone_number,
                                }),
                            );
                            setTimeout(function () {
                                self.close();
                            }, 2000);
                        } else if (result.error_code === "phone_already_in_use") {
                            self.phoneAlreadyInUse = true;
                            self.step = "code";
                            self.error = result.error;
                        } else {
                            self.step = "code";
                            self.error = result.error;
                            // CSP-compatible: reset individual OTP fields
                            self.otp0 = "";
                            self.otp1 = "";
                            self.otp2 = "";
                            self.otp3 = "";
                            self.otp4 = "";
                            self.otp5 = "";
                        }
                    });
                }
            },

            resendCode: function () {
                var self = this;
                this.step = "sending";
                this.error = "";
                if (window.phoneVerification) {
                    window.phoneVerification
                        .sendVerificationCode()
                        .then(function (result) {
                            if (result.success) {
                                self.failureCount = 0;
                                self.step = "code";
                                self.startResendTimer();
                            } else {
                                self.failureCount =
                                    window.phoneVerification.getFailureCount();
                                self.error = result.error || "Failed to resend code";
                                self.step = "code";
                            }
                        });
                }
            },

            startResendTimer: function () {
                var self = this;
                this.resendCountdown = 60;
                if (this.resendTimer) clearInterval(this.resendTimer);
                this.resendTimer = setInterval(function () {
                    self.resendCountdown--;
                    if (self.resendCountdown <= 0) {
                        clearInterval(self.resendTimer);
                    }
                }, 1000);
            },
        };
    });

    // PWA Install Button component for membership page
    // CSP-compatible with computed getters
    Alpine.data("pwaInstallButton", function () {
        return {
            deferredPrompt: null,
            canInstall: false,
            isInstalled: false,
            showInstructions: false,
            showFallback: false,
            instructions: "",

            // Computed getters for CSP compatibility
            get canInstallVisible() {
                return this.canInstall;
            },
            get isInstalledVisible() {
                return this.isInstalled;
            },
            get showInstructionsVisible() {
                return this.showInstructions;
            },
            get showFallbackVisible() {
                return this.showFallback;
            },

            init: function () {
                var self = this;

                // Check if already installed (standalone mode)
                if (
                    window.matchMedia("(display-mode: standalone)").matches ||
                    window.navigator.standalone === true
                ) {
                    self.isInstalled = true;
                    return;
                }

                // Check CrushPWA if available
                if (window.CrushPWA && window.CrushPWA.isStandalone) {
                    self.isInstalled = true;
                    return;
                }

                // iOS-specific instructions (no beforeinstallprompt on iOS)
                var isIOS =
                    /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
                if (isIOS) {
                    self.showInstructions = true;
                    self.instructions = 'Tap Share, then "Add to Home Screen"';
                    return;
                }

                // Listen for beforeinstallprompt event (Chrome, Edge, Samsung)
                window.addEventListener("beforeinstallprompt", function (e) {
                    e.preventDefault();
                    self.deferredPrompt = e;
                    self.canInstall = true;
                    self.showFallback = false;
                });

                // Listen for appinstalled event
                window.addEventListener("appinstalled", function () {
                    self.isInstalled = true;
                    self.canInstall = false;
                    self.showFallback = false;
                    self.deferredPrompt = null;
                });

                // Show fallback after 2s if no beforeinstallprompt fires
                // (covers desktop Firefox, non-Chromium browsers)
                setTimeout(function () {
                    if (
                        !self.canInstall &&
                        !self.isInstalled &&
                        !self.showInstructions
                    ) {
                        self.showFallback = true;
                    }
                }, 2000);
            },

            install: function () {
                var self = this;
                if (!self.deferredPrompt) return;

                self.deferredPrompt.prompt();
                self.deferredPrompt.userChoice.then(function (result) {
                    if (result.outcome === "accepted") {
                        self.isInstalled = true;
                        self.canInstall = false;
                    }
                    self.deferredPrompt = null;
                });
            },
        };
    });

    // ==========================================================================
    // JOURNEY GIFT COMPONENTS
    // ==========================================================================

    // Gift Sharing Component for success page
    // Handles copy to clipboard, WhatsApp, email, and native share
    Alpine.data("giftShare", function () {
        return {
            giftUrl: "",
            giftCode: "",
            recipientName: "",
            copied: false,
            hasNativeShare: false,

            // Computed getters for CSP compatibility
            get copyButtonText() {
                return this.copied ? gettext("Copied!") : gettext("Copy Link");
            },
            get copyButtonClass() {
                return this.copied
                    ? "share-btn share-btn-copy copied"
                    : "share-btn share-btn-copy";
            },
            get showNativeShare() {
                return this.hasNativeShare;
            },
            get hideNativeShare() {
                return !this.hasNativeShare;
            },

            init: function () {
                // Read data from attributes
                this.giftUrl = this.$el.getAttribute("data-gift-url") || "";
                this.giftCode = this.$el.getAttribute("data-gift-code") || "";
                this.recipientName = this.$el.getAttribute("data-recipient") || "";

                // Check for native share API support
                this.hasNativeShare = typeof navigator.share === "function";
            },

            copyLink: function () {
                var self = this;
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard
                        .writeText(this.giftUrl)
                        .then(function () {
                            self.copied = true;
                            setTimeout(function () {
                                self.copied = false;
                            }, 2000);
                        })
                        .catch(function (err) {
                            console.error("Failed to copy:", err);
                            self.fallbackCopy();
                        });
                } else {
                    self.fallbackCopy();
                }
            },

            fallbackCopy: function () {
                var self = this;
                // Fallback for older browsers
                var textArea = document.createElement("textarea");
                textArea.value = this.giftUrl;
                textArea.style.position = "fixed";
                textArea.style.left = "-9999px";
                document.body.appendChild(textArea);
                textArea.select();
                try {
                    document.execCommand("copy");
                    self.copied = true;
                    setTimeout(function () {
                        self.copied = false;
                    }, 2000);
                } catch (err) {
                    console.error("Fallback copy failed:", err);
                }
                document.body.removeChild(textArea);
            },

            shareWhatsApp: function () {
                var text =
                    "I created a magical Wonderland journey just for you! " +
                    "Scan the QR code or click here to begin: " +
                    this.giftUrl;
                var url = "https://wa.me/?text=" + encodeURIComponent(text);
                window.open(url, "_blank");
            },

            shareEmail: function () {
                var subject = gettext("A Magical Journey Awaits You!");
                var body =
                    "Hi " +
                    this.recipientName +
                    ",\n\n" +
                    gettext(
                        'I created a special "Wonderland of You" journey just for you!',
                    ) +
                    "\n\n" +
                    "Click here to begin your adventure:\n" +
                    this.giftUrl +
                    "\n\n" +
                    "Or use gift code: " +
                    this.giftCode;
                var mailto =
                    "mailto:?subject=" +
                    encodeURIComponent(subject) +
                    "&body=" +
                    encodeURIComponent(body);
                window.location.href = mailto;
            },

            shareNative: function () {
                var self = this;
                if (navigator.share) {
                    navigator
                        .share({
                            title: gettext("A Magical Journey Awaits!"),
                            text:
                                gettext("I created a special Wonderland journey for") +
                                " " +
                                self.recipientName +
                                "!",
                            url: self.giftUrl,
                        })
                        .catch(function () {
                            // Share was cancelled or failed
                        });
                }
            },
        };
    });

    // Gift Create Form Component
    // Multi-step form for creating journey gifts with media uploads
    Alpine.data("giftCreateForm", function () {
        return {
            currentStep: 1,
            // Chapter 1 image state
            chapter1HasFileFlag: false,
            chapter1Preview: "",
            chapter1FileName: "",
            // Audio file state
            audioHasFileFlag: false,
            audioFileName: "",
            audioFileSize: "",
            audioFileType: "",
            audioError: "",
            // Video file state
            videoHasFileFlag: false,
            videoFileName: "",
            videoFileSize: "",
            videoFileType: "",
            videoError: "",

            // Allowed file types
            allowedAudioTypes: [
                "audio/mpeg",
                "audio/mp3",
                "audio/wav",
                "audio/x-wav",
                "audio/mp4",
                "audio/x-m4a",
                "audio/aac",
            ],
            allowedVideoTypes: ["video/mp4", "video/quicktime", "video/x-m4v"],
            allowedAudioExtensions: [".mp3", ".wav", ".m4a", ".aac"],
            allowedVideoExtensions: [".mp4", ".mov", ".m4v"],

            // Computed getters for CSP compatibility
            get stepOneClass() {
                if (this.currentStep === 1) return "active";
                if (this.currentStep > 1) return "completed";
                return "";
            },
            get stepTwoClass() {
                if (this.currentStep === 2) return "active";
                return "";
            },
            get stepOneContentClass() {
                return this.currentStep === 1 ? "active" : "";
            },
            get stepTwoContentClass() {
                return this.currentStep === 2 ? "active" : "";
            },
            get chapter1HasFile() {
                return this.chapter1HasFileFlag ? "has-file" : "";
            },
            get chapter1PreviewClass() {
                return this.chapter1Preview ? "show" : "";
            },
            get audioHasFile() {
                return this.audioHasFileFlag ? "has-file" : "";
            },
            get audioInfoVisible() {
                return this.audioHasFileFlag;
            },
            get audioDefaultVisible() {
                return !this.audioHasFileFlag;
            },
            get audioHasError() {
                return this.audioError !== "";
            },
            get videoHasFile() {
                return this.videoHasFileFlag ? "has-file" : "";
            },
            get videoInfoVisible() {
                return this.videoHasFileFlag;
            },
            get videoDefaultVisible() {
                return !this.videoHasFileFlag;
            },
            get videoHasError() {
                return this.videoError !== "";
            },

            init: function () {
                var self = this;
                // Listen for file changes on chapter1_image
                var ch1Input = document.getElementById("id_chapter1_image");
                if (ch1Input) {
                    ch1Input.addEventListener("change", function (e) {
                        self.handleChapter1FileChange(e);
                    });
                }

                // Listen for audio file changes
                var audioInput = document.getElementById("id_chapter4_audio");
                if (audioInput) {
                    audioInput.addEventListener("change", function (e) {
                        self.handleAudioFileChange(e);
                    });
                }

                // Listen for video file changes
                var videoInput = document.getElementById("id_chapter4_video");
                if (videoInput) {
                    videoInput.addEventListener("change", function (e) {
                        self.handleVideoFileChange(e);
                    });
                }

                // Add visual feedback for all file inputs
                var fileInputs = document.querySelectorAll('input[type="file"]');
                fileInputs.forEach(function (input) {
                    input.addEventListener("change", function (e) {
                        var wrapper = e.target.closest(".file-upload-wrapper");
                        if (wrapper) {
                            if (e.target.files && e.target.files.length > 0) {
                                wrapper.classList.add("has-file");
                            } else {
                                wrapper.classList.remove("has-file");
                            }
                        }
                    });
                });
            },

            formatFileSize: function (bytes) {
                if (bytes === 0) return "0 Bytes";
                var k = 1024;
                var sizes = ["Bytes", "KB", "MB", "GB"];
                var i = Math.floor(Math.log(bytes) / Math.log(k));
                return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
            },

            getFileExtension: function (filename) {
                var ext = filename.slice(((filename.lastIndexOf(".") - 1) >>> 0) + 2);
                return ext ? "." + ext.toLowerCase() : "";
            },

            isValidAudioFile: function (file) {
                var ext = this.getFileExtension(file.name);
                var typeValid = this.allowedAudioTypes.indexOf(file.type) !== -1;
                var extValid = this.allowedAudioExtensions.indexOf(ext) !== -1;
                return typeValid || extValid;
            },

            isValidVideoFile: function (file) {
                var ext = this.getFileExtension(file.name);
                var typeValid = this.allowedVideoTypes.indexOf(file.type) !== -1;
                var extValid = this.allowedVideoExtensions.indexOf(ext) !== -1;
                return typeValid || extValid;
            },

            goToStep2: function () {
                // Basic validation before proceeding
                var recipientName = document.getElementById("id_recipient_name");
                var dateMet = document.getElementById("id_date_first_met");
                var locationMet = document.getElementById("id_location_first_met");

                var isValid = true;

                if (!recipientName.value.trim()) {
                    recipientName.classList.add("is-invalid");
                    isValid = false;
                } else {
                    recipientName.classList.remove("is-invalid");
                }

                if (!dateMet.value) {
                    dateMet.classList.add("is-invalid");
                    isValid = false;
                } else {
                    dateMet.classList.remove("is-invalid");
                }

                if (!locationMet.value.trim()) {
                    locationMet.classList.add("is-invalid");
                    isValid = false;
                } else {
                    locationMet.classList.remove("is-invalid");
                }

                if (isValid) {
                    this.currentStep = 2;
                    window.scrollTo({ top: 0, behavior: "smooth" });
                }
            },

            goToStep1: function () {
                this.currentStep = 1;
                window.scrollTo({ top: 0, behavior: "smooth" });
            },

            handleChapter1FileChange: function (event) {
                var file = event.target.files[0];
                if (file) {
                    this.chapter1HasFileFlag = true;
                    this.chapter1FileName = file.name;

                    // Create preview for images
                    if (file.type.startsWith("image/")) {
                        var reader = new FileReader();
                        var self = this;
                        reader.onload = function (e) {
                            self.chapter1Preview = e.target.result;
                        };
                        reader.readAsDataURL(file);
                    }
                } else {
                    this.chapter1HasFileFlag = false;
                    this.chapter1Preview = "";
                    this.chapter1FileName = "";
                }
            },

            handleAudioFileChange: function (event) {
                var file = event.target.files[0];
                this.audioError = "";

                if (file) {
                    // Validate file type
                    if (!this.isValidAudioFile(file)) {
                        this.audioError =
                            "Invalid audio format. Please use MP3, WAV, or M4A files.";
                        this.audioHasFileFlag = false;
                        this.audioFileName = "";
                        this.audioFileSize = "";
                        this.audioFileType = "";
                        event.target.value = "";
                        return;
                    }

                    // Validate file size (10MB max)
                    if (file.size > 10 * 1024 * 1024) {
                        this.audioError =
                            "Audio file is too large. Maximum size is 10 MB.";
                        this.audioHasFileFlag = false;
                        this.audioFileName = "";
                        this.audioFileSize = "";
                        this.audioFileType = "";
                        event.target.value = "";
                        return;
                    }

                    this.audioHasFileFlag = true;
                    this.audioFileName = file.name;
                    this.audioFileSize = this.formatFileSize(file.size);
                    this.audioFileType = file.type || "audio";
                } else {
                    this.audioHasFileFlag = false;
                    this.audioFileName = "";
                    this.audioFileSize = "";
                    this.audioFileType = "";
                }
            },

            handleVideoFileChange: function (event) {
                var file = event.target.files[0];
                this.videoError = "";

                if (file) {
                    // Validate file type
                    if (!this.isValidVideoFile(file)) {
                        this.videoError =
                            "Invalid video format. Please use MP4 or MOV files.";
                        this.videoHasFileFlag = false;
                        this.videoFileName = "";
                        this.videoFileSize = "";
                        this.videoFileType = "";
                        event.target.value = "";
                        return;
                    }

                    // Validate file size (50MB max)
                    if (file.size > 50 * 1024 * 1024) {
                        this.videoError =
                            "Video file is too large. Maximum size is 50 MB.";
                        this.videoHasFileFlag = false;
                        this.videoFileName = "";
                        this.videoFileSize = "";
                        this.videoFileType = "";
                        event.target.value = "";
                        return;
                    }

                    this.videoHasFileFlag = true;
                    this.videoFileName = file.name;
                    this.videoFileSize = this.formatFileSize(file.size);
                    this.videoFileType = file.type || "video";
                } else {
                    this.videoHasFileFlag = false;
                    this.videoFileName = "";
                    this.videoFileSize = "";
                    this.videoFileType = "";
                }
            },
        };
    });

    // =========================================================================
    // JOURNEY SYSTEM ALPINE COMPONENTS
    // CSP-compatible components for the Wonderland Journey experience
    // =========================================================================

    /**
     * Journey State Manager
     * Handles auto-save of journey progress, time tracking, and state management.
     * Used in journey_base.html
     *
     * Usage:
     * <div x-data="journeyState"
     *      data-save-url="/api/journey/save-state/"
     *      data-initial-time="300"
     *      data-initial-points="150">
     */
    Alpine.data("journeyState", function () {
        return {
            startTime: Date.now(),
            lastSaveTime: Date.now(),
            totalTimeSeconds: 0,
            currentPoints: 0,
            saveUrl: "",
            saveInterval: null,

            init: function () {
                var el = this.$el;
                this.saveUrl = el.dataset.saveUrl || "";
                this.totalTimeSeconds = parseInt(el.dataset.initialTime, 10) || 0;
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                // Start auto-save interval (every 30 seconds)
                var self = this;
                this.saveInterval = setInterval(function () {
                    self.saveState();
                }, 30000);

                // Save on page unload
                window.addEventListener("beforeunload", function () {
                    self.saveStateBeacon();
                });
            },

            saveState: function () {
                if (!this.saveUrl) return;

                var now = Date.now();
                var timeIncrement = Math.floor((now - this.lastSaveTime) / 1000);
                var self = this;

                if (timeIncrement > 0) {
                    fetch(this.saveUrl, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-CSRFToken": CrushUtils.getCsrfToken(),
                        },
                        body: JSON.stringify({
                            time_increment: timeIncrement,
                        }),
                    })
                        .then(function (response) {
                            return response.json();
                        })
                        .then(function (data) {
                            if (data.success) {
                                self.lastSaveTime = now;
                                self.totalTimeSeconds = data.total_time;
                            }
                        })
                        .catch(function (error) {
                            console.error("Error saving state:", error);
                        });
                }
            },

            saveStateBeacon: function () {
                if (!this.saveUrl) return;

                var timeIncrement = Math.floor((Date.now() - this.lastSaveTime) / 1000);
                if (timeIncrement > 0) {
                    var formData = new FormData();
                    formData.append("time_increment", timeIncrement);
                    formData.append("csrfmiddlewaretoken", CrushUtils.getCsrfToken());
                    navigator.sendBeacon(this.saveUrl, formData);
                }
            },

            destroy: function () {
                if (this.saveInterval) {
                    clearInterval(this.saveInterval);
                }
            },
        };
    });

    /**
     * Riddle Challenge Component
     * For riddle-type challenges with text input and hints
     *
     * Usage:
     * <article x-data="riddleChallenge"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-hint-url="/api/journey/hint/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-initial-points="25">
     */
    Alpine.data("riddleChallenge", function () {
        return {
            challengeId: 0,
            answer: "",
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            currentPoints: 0,
            submitUrl: "",
            hintUrl: "",
            chapterUrl: "",
            hintsUsed: [],
            // Date input support
            inputType: "",
            dateFormat: "DD/MM/YYYY",
            dateValue: "",
            isDateInput: false,
            isTextInput: true,
            // CSP-safe: plain data properties instead of getters
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            feedbackClass: "hidden",
            // Hint state properties
            hint1Used: false,
            hint2Used: false,
            hint3Used: false,
            hint1NotUsed: true,
            hint2NotUsed: true,
            hint3NotUsed: true,
            hint1BtnClass: "",
            hint2BtnClass: "",
            hint3BtnClass: "",
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext("Correct!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorDefault: gettext("Not quite right. Try again!"),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.hintUrl = el.dataset.hintUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                // Date input configuration
                this.inputType = el.dataset.inputType || "";
                this.dateFormat = el.dataset.dateFormat || "DD/MM/YYYY";
                this.isDateInput = this.inputType === "date";
                this.isTextInput = !this.isDateInput;

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault)
                    this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("answer", function () {
                    self._updateSubmitState();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });
                this.$watch("hintsUsed", function () {
                    self._updateHintState();
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                var hasAnswer = this.answer.trim().length > 0;
                var canSubmit = hasAnswer && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center";
                } else {
                    this.feedbackClass = "hidden";
                }
            },

            _updateHintState: function () {
                this.hint1Used = this.hintsUsed.indexOf(1) !== -1;
                this.hint2Used = this.hintsUsed.indexOf(2) !== -1;
                this.hint3Used = this.hintsUsed.indexOf(3) !== -1;
                this.hint1NotUsed = !this.hint1Used;
                this.hint2NotUsed = !this.hint2Used;
                this.hint3NotUsed = !this.hint3Used;
                this.hint1BtnClass = this.hint1Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
                this.hint2BtnClass = this.hint2Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
                this.hint3BtnClass = this.hint3Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim(),
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                        } else {
                            self.feedbackType = "error";
                            self.feedback = data.message || self.i18n.errorDefault;
                            self.answer = "";
                            self.isSubmitting = false;
                            self.shakeInput();
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    " " +
                    this.i18n.correct +
                    "</h3>" +
                    '<p class="mb-4">' +
                    (data.success_message || "") +
                    "</p>" +
                    '<p class="font-bold mb-6">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            shakeInput: function () {
                var input = this.$el.querySelector(".journey-input");
                if (input) {
                    input.classList.add("journey-animate-shake");
                    setTimeout(function () {
                        input.classList.remove("journey-animate-shake");
                    }, 500);
                }
            },

            unlockHint: function (hintNum, cost) {
                if (this.hintsUsed.indexOf(hintNum) !== -1) return;

                var self = this;
                fetch(this.hintUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        hint_number: hintNum,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            self.hintsUsed.push(hintNum);
                            self.currentPoints -= cost;

                            // Dispatch event for hint box to show content
                            self.$dispatch("hint-unlocked", {
                                hintNum: hintNum,
                                hintText: data.hint_text,
                            });
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                    });
            },

            isHintUsed: function (hintNum) {
                return this.hintsUsed.indexOf(hintNum) !== -1;
            },

            // CSP-safe: update answer from input event
            updateAnswer: function (event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value;
                } else {
                    var input = this.$el.querySelector("#answerInput");
                    if (input) this.answer = input.value;
                }
            },

            // CSP-safe: wrapper to unlock hint from button click
            // Reads hint number and cost from data attributes
            unlockHintFromButton: function (event) {
                var button = event && event.currentTarget ? event.currentTarget : null;
                if (!button) return;

                var hintNum = parseInt(button.dataset.hintNum, 10) || 0;
                var cost = parseInt(button.dataset.hintCost, 10) || 0;
                if (hintNum > 0) {
                    this.unlockHint(hintNum, cost);
                }
            },

            // CSP-safe: handle keydown to submit on Enter only
            handleKeydown: function (event) {
                if (event && event.key === "Enter") {
                    event.preventDefault();
                    this.submitAnswer();
                }
            },

            // CSP-safe: update answer from date input
            // Formats date according to dateFormat setting (DD/MM/YYYY by default)
            updateDateAnswer: function (event) {
                if (event && event.target) {
                    var dateStr = event.target.value; // YYYY-MM-DD format from date input
                    this.dateValue = dateStr;

                    if (dateStr) {
                        var parts = dateStr.split("-");
                        var year = parts[0];
                        var month = parts[1];
                        var day = parts[2];

                        // Format according to dateFormat
                        // Supports: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, MM/DD/YYYY
                        var format = this.dateFormat.toUpperCase();
                        var separator = "/";
                        if (format.indexOf("-") !== -1) separator = "-";
                        else if (format.indexOf(".") !== -1) separator = ".";

                        if (format.indexOf("MM") < format.indexOf("DD")) {
                            // MM/DD/YYYY format
                            this.answer = month + separator + day + separator + year;
                        } else {
                            // DD/MM/YYYY format (default)
                            this.answer = day + separator + month + separator + year;
                        }
                    } else {
                        this.answer = "";
                    }
                }
            },
        };
    });

    /**
     * Word Scramble Challenge Component
     * For word scramble challenges with shuffle functionality
     *
     * Usage:
     * <article x-data="wordScramble"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-hint-url="/api/journey/hint/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-initial-points="25"
     *          data-scrambled-words="WORD SCRAMBLE TEST">
     */
    Alpine.data("wordScramble", function () {
        return {
            challengeId: 0,
            answer: "",
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            currentPoints: 0,
            submitUrl: "",
            hintUrl: "",
            chapterUrl: "",
            hintsUsed: [],
            scrambledWords: [],
            displayText: "",
            // CSP-safe: plain data properties instead of getters
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            feedbackClass: "hidden",
            // Hint state properties
            hint1Used: false,
            hint2Used: false,
            hint3Used: false,
            hint1NotUsed: true,
            hint2NotUsed: true,
            hint3NotUsed: true,
            hint1BtnClass: "",
            hint2BtnClass: "",
            hint3BtnClass: "",
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext("Correct!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorDefault: gettext("Not quite right. Try again!"),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.hintUrl = el.dataset.hintUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                var scrambled = el.dataset.scrambledWords || "";
                this.scrambledWords = scrambled.split(/\s+/).filter(function (w) {
                    return w.trim();
                });
                this.displayText = this.scrambledWords.join("  •  ");

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault)
                    this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("answer", function () {
                    self._updateSubmitState();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });
                this.$watch("hintsUsed", function () {
                    self._updateHintState();
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                var hasAnswer = this.answer.trim().length > 0;
                var canSubmit = hasAnswer && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center";
                } else {
                    this.feedbackClass = "hidden";
                }
            },

            _updateHintState: function () {
                this.hint1Used = this.hintsUsed.indexOf(1) !== -1;
                this.hint2Used = this.hintsUsed.indexOf(2) !== -1;
                this.hint3Used = this.hintsUsed.indexOf(3) !== -1;
                this.hint1NotUsed = !this.hint1Used;
                this.hint2NotUsed = !this.hint2Used;
                this.hint3NotUsed = !this.hint3Used;
                this.hint1BtnClass = this.hint1Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
                this.hint2BtnClass = this.hint2Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
                this.hint3BtnClass = this.hint3Used
                    ? "opacity-50 cursor-not-allowed"
                    : "";
            },

            shuffleWords: function () {
                var previousOrder = this.scrambledWords.join(" ");
                var newOrder;
                var attempts = 0;

                // Fisher-Yates shuffle for letters within each word
                do {
                    newOrder = this.scrambledWords.map(function (word) {
                        var letters = word.split("");
                        // Fisher-Yates shuffle on letters array
                        for (var i = letters.length - 1; i > 0; i--) {
                            var j = Math.floor(Math.random() * (i + 1));
                            var temp = letters[i];
                            letters[i] = letters[j];
                            letters[j] = temp;
                        }
                        return letters.join("");
                    });
                    // Track previous iteration to avoid duplicate shuffles
                    if (newOrder.join(" ") !== previousOrder) {
                        previousOrder = newOrder.join(" ");
                    }
                    attempts++;
                } while (
                    newOrder.join(" ") === this.scrambledWords.join(" ") &&
                    attempts < 50
                );

                this.scrambledWords = newOrder;
                this.displayText = this.scrambledWords.join("  •  ");
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim(),
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                        } else {
                            self.feedbackType = "error";
                            self.feedback = data.message || self.i18n.errorDefault;
                            self.answer = "";
                            self.isSubmitting = false;
                            self.shakeInput();
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    " " +
                    this.i18n.correct +
                    "</h3>" +
                    '<p class="mb-4">' +
                    (data.success_message || "") +
                    "</p>" +
                    '<p class="font-bold mb-6">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            shakeInput: function () {
                var input = this.$el.querySelector(".journey-input");
                if (input) {
                    input.classList.add("journey-animate-shake");
                    setTimeout(function () {
                        input.classList.remove("journey-animate-shake");
                    }, 500);
                }
            },

            unlockHint: function (hintNum, cost) {
                if (this.hintsUsed.indexOf(hintNum) !== -1) return;

                var self = this;
                fetch(this.hintUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        hint_number: hintNum,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            self.hintsUsed.push(hintNum);
                            self.currentPoints -= cost;
                            self.$dispatch("hint-unlocked", {
                                hintNum: hintNum,
                                hintText: data.hint_text,
                            });
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                    });
            },

            isHintUsed: function (hintNum) {
                return this.hintsUsed.indexOf(hintNum) !== -1;
            },

            // CSP-safe: update answer from input event
            updateAnswer: function (event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value.toUpperCase();
                } else {
                    var input = this.$el.querySelector("#answerInput");
                    if (input) this.answer = input.value.toUpperCase();
                }
            },

            // CSP-safe: wrapper to unlock hint from button click
            unlockHintFromButton: function (event) {
                var button = event && event.currentTarget ? event.currentTarget : null;
                if (!button) return;

                var hintNum = parseInt(button.dataset.hintNum, 10) || 0;
                var cost = parseInt(button.dataset.hintCost, 10) || 0;
                if (hintNum > 0) {
                    this.unlockHint(hintNum, cost);
                }
            },

            // CSP-safe: handle keydown to submit on Enter only
            handleKeydown: function (event) {
                if (event && event.key === "Enter") {
                    event.preventDefault();
                    this.submitAnswer();
                }
            },
        };
    });

    /**
     * Multiple Choice Challenge Component
     * For multiple choice questions with option selection
     *
     * Usage:
     * <article x-data="multipleChoice"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-chapter-number="2">
     */
    Alpine.data("multipleChoice", function () {
        return {
            challengeId: 0,
            selectedOption: null,
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            submitUrl: "",
            chapterUrl: "",
            chapterNumber: 1,
            // CSP-safe: plain data properties instead of getters
            hasSelection: false,
            hasNoSelection: true,
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            showSubmitLabel: false,
            feedbackClass: "hidden mt-6",
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext("Correct!"),
                thankYou: gettext("Thank you for sharing!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorDefault: gettext("Not quite right! Try a different answer."),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nThankYou)
                    this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault)
                    this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("selectedOption", function () {
                    self._updateSubmitState();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                this.hasSelection = this.selectedOption !== null;
                this.hasNoSelection = !this.hasSelection;
                var canSubmit = this.hasSelection && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
                this.showSubmitLabel = this.hasSelection && this.isNotSubmitting;
                this._syncOptionState();
            },

            _syncOptionState: function () {
                var selected = this.selectedOption;
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    var isSelected = selected && card.dataset.optionKey === selected;
                    card.classList.toggle("selected", !!isSelected);
                    card.setAttribute("aria-checked", isSelected ? "true" : "false");
                });
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center mt-6";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center mt-6";
                } else {
                    this.feedbackClass = "hidden mt-6";
                }
            },

            selectOption: function (optionKey, event) {
                var isSameOption = this.selectedOption === optionKey;
                this.clearSelection();

                if (isSameOption) {
                    this.selectedOption = null;
                    if (event && event.currentTarget && event.currentTarget.blur) {
                        event.currentTarget.blur();
                    }
                    return;
                }

                var target = event.currentTarget;
                target.classList.add("selected");
                target.setAttribute("aria-checked", "true");
                this.selectedOption = optionKey;
            },

            isSelected: function (optionKey) {
                return this.selectedOption === optionKey;
            },

            clearSelection: function () {
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    card.classList.remove("selected");
                    card.setAttribute("aria-checked", "false");
                    card.blur();
                });
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.selectedOption,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                        } else {
                            self.feedbackType = "error";
                            self.feedback = self.i18n.errorDefault;
                            self.markIncorrect();
                            self.selectedOption = null;
                            self.isSubmitting = false;

                            // Auto-hide error after 3 seconds
                            setTimeout(function () {
                                if (self.feedbackType === "error") {
                                    self.feedback = "";
                                    self.feedbackType = "";
                                    // Explicitly update feedback state to trigger visual updates
                                    self._updateFeedbackState();
                                }
                            }, 3000);
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                var isChapter2 = this.chapterNumber === 2;
                var iconHtml = isChapter2
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = isChapter2 ? this.i18n.thankYou : this.i18n.correct;

                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    iconHtml +
                    " " +
                    title +
                    "</h3>" +
                    '<div class="personal-message">' +
                    (data.success_message || "") +
                    "</div>" +
                    '<p class="font-bold my-4">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            markIncorrect: function () {
                var selectedCard = this.$el.querySelector(".option-card.selected");
                if (selectedCard) {
                    selectedCard.classList.remove("selected");
                    selectedCard.classList.add("incorrect", "journey-animate-shake");
                    setTimeout(function () {
                        selectedCard.classList.remove(
                            "incorrect",
                            "journey-animate-shake",
                        );
                    }, 1000);
                }
            },

            handleKeypress: function (optionKey, event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: select option from element click
            selectOptionFromElement: function (event) {
                var element = event && event.currentTarget ? event.currentTarget : null;
                if (!element) return;

                var optionKey = element.dataset.optionKey || "";
                if (optionKey) {
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: handle keydown on option cards
            handleOptionKeydown: function (event) {
                if (event && (event.key === "Enter" || event.key === " ")) {
                    event.preventDefault();
                    this.selectOptionFromElement(event);
                }
            },
        };
    });

    /**
     * Timeline Sort Challenge Component
     * For timeline sorting challenges with Sortable.js
     *
     * Usage:
     * <article x-data="timelineSort"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-chapter-url="/journey/chapter/1/">
     */
    Alpine.data("timelineSort", function () {
        return {
            challengeId: 0,
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            submitUrl: "",
            chapterUrl: "",
            sortable: null,
            isTouchDevice: false,
            // CSP-safe: plain data properties instead of getters
            submitBtnDisabled: false,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            showSubmitLabel: false,
            feedbackClass: "hidden mt-6",
            instructionText:
                "Drag and drop the events to arrange them in chronological order",
            // i18n translations (loaded from data attributes)
            i18n: {
                perfect: gettext("Perfect!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorDefault: gettext("Not quite right. Try rearranging the events!"),
                errorGeneric: gettext("An error occurred. Please try again."),
                instructionDesktop: gettext(
                    "Drag and drop the events to arrange them in chronological order",
                ),
                instructionTouch:
                    "Touch and drag the events to arrange them in chronological order",
            },

            init: function () {
                var el = this.$el;
                var self = this;

                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.isTouchDevice =
                    "ontouchstart" in window || navigator.maxTouchPoints > 0;

                // Load i18n translations from data attributes
                if (el.dataset.i18nPerfect) this.i18n.perfect = el.dataset.i18nPerfect;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault)
                    this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;
                if (el.dataset.i18nInstructionDesktop)
                    this.i18n.instructionDesktop = el.dataset.i18nInstructionDesktop;
                if (el.dataset.i18nInstructionTouch)
                    this.i18n.instructionTouch = el.dataset.i18nInstructionTouch;

                // CSP-safe: update instruction text based on device type
                this._updateInstructionText();

                // CSP-safe: use $watch to update derived state
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });

                // Initialize Sortable.js after DOM is ready
                this.$nextTick(function () {
                    self.initSortable();
                    self.shuffleItems();

                    // CSP-safe: Manually bind click handler as fallback
                    // Alpine CSP build sometimes fails to bind @click on dynamically shown elements
                    var submitBtn = el.querySelector("button.journey-btn-primary");
                    if (submitBtn) {
                        submitBtn.addEventListener("click", function (e) {
                            e.preventDefault();
                            self.submitAnswer();
                        });
                    }
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                var canSubmit = !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center mt-6";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center mt-6";
                } else {
                    this.feedbackClass = "hidden mt-6";
                }
            },

            _updateInstructionText: function () {
                this.instructionText = this.isTouchDevice
                    ? this.i18n.instructionTouch
                    : this.i18n.instructionDesktop;
            },

            initSortable: function () {
                var timelineItems = this.$el.querySelector("#timelineItems");
                if (!timelineItems || typeof Sortable === "undefined") return;

                var self = this;
                this.sortable = new Sortable(timelineItems, {
                    animation: 200,
                    easing: "cubic-bezier(1, 0, 0, 1)",
                    ghostClass: "sortable-ghost",
                    chosenClass: "sortable-chosen",
                    dragClass: "sortable-drag",
                    handle: ".timeline-item",
                    forceFallback: false,
                    fallbackTolerance: 3,
                    touchStartThreshold: 5,
                    delay: 0,
                    delayOnTouchOnly: true,
                    onEnd: function () {
                        self.updateNumbers();
                    },
                });
            },

            updateNumbers: function () {
                var items = this.$el.querySelectorAll(".timeline-item");
                items.forEach(function (item, index) {
                    var numberEl = item.querySelector(".timeline-number");
                    if (numberEl) {
                        numberEl.textContent = index + 1;
                    }
                });
            },

            shuffleItems: function () {
                var timelineItems = this.$el.querySelector("#timelineItems");
                if (!timelineItems) return;

                var items = Array.from(timelineItems.children);
                // Fisher-Yates shuffle
                for (var i = items.length - 1; i > 0; i--) {
                    var j = Math.floor(Math.random() * (i + 1));
                    timelineItems.appendChild(items[j]);
                }
                this.updateNumbers();
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var timelineItems = this.$el.querySelector("#timelineItems");
                if (!timelineItems) return;

                var items = timelineItems.querySelectorAll(".timeline-item");
                var order = Array.from(items).map(function (item) {
                    return item.dataset.originalIndex;
                });
                var answer = order.join(",");

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: answer,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                            self.disableSorting();
                        } else {
                            self.feedbackType = "error";
                            self.feedback = self.i18n.errorDefault;
                            self.isSubmitting = false;

                            // Auto-hide error after 3 seconds
                            setTimeout(function () {
                                if (self.feedbackType === "error") {
                                    self.feedback = "";
                                    self.feedbackType = "";
                                    // Explicitly update feedback state to trigger visual updates
                                    self._updateFeedbackState();
                                }
                            }, 3000);
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    " " +
                    this.i18n.perfect +
                    "</h3>" +
                    '<p class="text-lg my-5">' +
                    (data.success_message || "") +
                    "</p>" +
                    '<p class="font-bold my-4">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            disableSorting: function () {
                if (this.sortable) {
                    this.sortable.option("disabled", true);
                }
                var items = this.$el.querySelectorAll(".timeline-item");
                items.forEach(function (item) {
                    item.style.cursor = "default";
                });
            },
        };
    });

    /**
     * Would You Rather Challenge Component
     * For two-option choice questions
     *
     * Usage:
     * <article x-data="wouldYouRather"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-chapter-number="4">
     */
    Alpine.data("wouldYouRather", function () {
        return {
            challengeId: 0,
            selectedOption: null,
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            submitUrl: "",
            chapterUrl: "",
            chapterNumber: 1,
            // CSP-safe: plain data properties instead of getters
            hasSelection: false,
            hasNoSelection: true,
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            showSubmitLabel: false,
            feedbackClass: "hidden mt-6",
            // i18n translations (loaded from data attributes)
            i18n: {
                greatChoice: gettext("Great choice!"),
                thankYou: gettext("Thank you for sharing!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;

                // Load i18n translations from data attributes
                if (el.dataset.i18nGreatChoice)
                    this.i18n.greatChoice = el.dataset.i18nGreatChoice;
                if (el.dataset.i18nThankYou)
                    this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("selectedOption", function () {
                    self._updateSubmitState();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                this.hasSelection = this.selectedOption !== null;
                this.hasNoSelection = !this.hasSelection;
                var canSubmit = this.hasSelection && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
                this.showSubmitLabel = this.hasSelection && this.isNotSubmitting;
                this._syncOptionState();
            },

            _syncOptionState: function () {
                var selected = this.selectedOption;
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    var isSelected = selected && card.dataset.optionKey === selected;
                    card.classList.toggle("selected", !!isSelected);
                    card.setAttribute("aria-checked", isSelected ? "true" : "false");
                });
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center mt-6";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center mt-6";
                } else {
                    this.feedbackClass = "hidden mt-6";
                }
            },

            selectOption: function (optionKey, event) {
                var isSameOption = this.selectedOption === optionKey;
                this.clearSelection();

                if (isSameOption) {
                    this.selectedOption = null;
                    return;
                }

                var target = event.currentTarget;
                target.classList.add("selected");
                target.setAttribute("aria-checked", "true");
                this.selectedOption = optionKey;
            },

            clearSelection: function () {
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    card.classList.remove("selected");
                    card.setAttribute("aria-checked", "false");
                    card.blur();
                });
            },

            isSelected: function (optionKey) {
                return this.selectedOption === optionKey;
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.selectedOption,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                            self.disableOptions();
                        } else {
                            self.isSubmitting = false;
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                var isChapter4 = this.chapterNumber === 4;
                var iconHtml = isChapter4
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = isChapter4 ? this.i18n.thankYou : this.i18n.greatChoice;

                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    iconHtml +
                    " " +
                    title +
                    "</h3>" +
                    '<div class="personal-message">' +
                    (data.success_message || "") +
                    "</div>" +
                    '<p class="font-bold my-4">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            disableOptions: function () {
                var optionsSection = this.$el.querySelector("#optionsSection");
                if (optionsSection) {
                    optionsSection.style.opacity = "0.5";
                }
                var cards = this.$el.querySelectorAll(".option-card");
                cards.forEach(function (card) {
                    card.style.cursor = "default";
                    card.style.pointerEvents = "none";
                });
            },

            handleKeypress: function (optionKey, event) {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: select option from element click
            selectOptionFromElement: function (event) {
                var element = event && event.currentTarget ? event.currentTarget : null;
                if (!element) return;

                var optionKey = element.dataset.optionKey || "";
                if (optionKey) {
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: handle keydown on option cards
            handleOptionKeydown: function (event) {
                if (event && (event.key === "Enter" || event.key === " ")) {
                    event.preventDefault();
                    this.selectOptionFromElement(event);
                }
            },
        };
    });

    /**
     * Open Text Challenge Component
     * For free-form text response challenges
     *
     * Usage:
     * <article x-data="openText"
     *          data-challenge-id="123"
     *          data-submit-url="/api/journey/submit/"
     *          data-chapter-url="/journey/chapter/1/"
     *          data-chapter-number="2"
     *          data-min-length="10"
     *          data-max-length="2000">
     */
    Alpine.data("openText", function () {
        return {
            challengeId: 0,
            answer: "",
            isSubmitting: false,
            feedback: "",
            feedbackType: "",
            feedbackHtml: "",
            submitUrl: "",
            chapterUrl: "",
            chapterNumber: 1,
            minLength: 10,
            maxLength: 2000,
            // CSP-safe: plain data properties instead of getters
            charCount: 0,
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isSubmittingState: false,
            isNotSubmitting: true,
            feedbackClass: "hidden mt-6",
            charCounterClass: "char-counter",
            // i18n translations (loaded from data attributes)
            i18n: {
                thankYou: gettext("Thank you for sharing!"),
                pointsEarned: gettext("Points Earned:"),
                continue: gettext("Continue"),
                errorGeneric: gettext("An error occurred. Please try again."),
            },

            init: function () {
                var el = this.$el;
                var self = this;

                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || "";
                this.chapterUrl = el.dataset.chapterUrl || "";
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;
                this.minLength = parseInt(el.dataset.minLength, 10) || 10;
                this.maxLength = parseInt(el.dataset.maxLength, 10) || 2000;

                // Load i18n translations from data attributes
                if (el.dataset.i18nThankYou)
                    this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned)
                    this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue)
                    this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorGeneric)
                    this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                this.$watch("answer", function () {
                    self._updateSubmitState();
                    self._updateCharCounterClass();
                });
                this.$watch("isSubmitting", function () {
                    self._updateSubmitState();
                });
                this.$watch("feedback", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackHtml", function () {
                    self._updateFeedbackState();
                });
                this.$watch("feedbackType", function () {
                    self._updateFeedbackState();
                });

                // Auto-focus on input
                this.$nextTick(function () {
                    var textInput = self.$el.querySelector("#textInput");
                    if (textInput) {
                        setTimeout(function () {
                            textInput.focus();
                        }, 500);
                    }
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function () {
                this.charCount = this.answer.length;
                var hasMinLength = this.answer.trim().length >= this.minLength;
                var canSubmit = hasMinLength && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isSubmittingState = this.isSubmitting;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function () {
                this.showFeedback = this.feedback !== "" || this.feedbackHtml !== "";
                this.isSuccess = this.feedbackType === "success";
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === "error";
                this.isNotError = !this.isError;
                if (this.feedbackType === "success") {
                    this.feedbackClass = "journey-message-success p-6 text-center mt-6";
                } else if (this.feedbackType === "error") {
                    this.feedbackClass = "journey-message-error p-6 text-center mt-6";
                } else {
                    this.feedbackClass = "hidden mt-6";
                }
            },

            _updateCharCounterClass: function () {
                if (this.charCount > this.maxLength * 0.9) {
                    this.charCounterClass = "char-counter error";
                } else if (this.charCount > this.maxLength * 0.75) {
                    this.charCounterClass = "char-counter warning";
                } else {
                    this.charCounterClass = "char-counter";
                }
            },

            submitAnswer: function () {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = "";
                this.feedbackHtml = "";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim(),
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success && data.is_correct) {
                            self.feedbackType = "success";
                            self.feedbackHtml = self.buildSuccessHtml(data);
                        } else {
                            self.feedbackType = "error";
                            self.feedback = self.i18n.errorGeneric;
                            self.isSubmitting = false;
                        }
                    })
                    .catch(function (error) {
                        console.error("Error:", error);
                        self.feedbackType = "error";
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    });
            },

            buildSuccessHtml: function (data) {
                var chapterNum = this.chapterNumber;
                var isQuestionnaire =
                    chapterNum === 2 || chapterNum === 4 || chapterNum === 5;
                var iconHtml = isQuestionnaire
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = this.i18n.thankYou;

                return (
                    '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    iconHtml +
                    " " +
                    title +
                    "</h3>" +
                    '<div class="personal-message">' +
                    (data.success_message || "") +
                    "</div>" +
                    '<p class="font-bold my-4">🏆 ' +
                    this.i18n.pointsEarned +
                    " " +
                    data.points_earned +
                    "</p>" +
                    '<a href="' +
                    this.chapterUrl +
                    '" class="journey-btn-primary">' +
                    this.i18n.continue +
                    ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    "</a>"
                );
            },

            // CSP-safe: update answer from input event
            updateAnswer: function (event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value;
                } else {
                    var input = this.$el.querySelector("#textInput");
                    if (input) this.answer = input.value;
                }
            },
        };
    });

    /**
     * Hint Box Component
     * For displaying unlocked hints
     *
     * Usage:
     * <div x-data="hintBox" data-hint-num="1" @hint-unlocked.window="showHint($event.detail)">
     */
    Alpine.data("hintBox", function () {
        return {
            hintNum: 0,
            hintText: "",
            isActive: false,
            // CSP-safe: plain data properties instead of getters
            showHint: false,
            hintClass: "journey-hint-box",

            init: function () {
                this.hintNum = parseInt(this.$el.dataset.hintNum, 10) || 0;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch("isActive", function () {
                    self._updateState();
                });
            },

            _updateState: function () {
                this.showHint = this.isActive;
                this.hintClass = this.isActive
                    ? "journey-hint-box active"
                    : "journey-hint-box";
            },

            // CSP-safe: wrapper that receives event object
            handleHintUnlockedEvent: function (event) {
                var detail = event && event.detail ? event.detail : {};
                this.handleHintUnlocked(detail);
            },

            handleHintUnlocked: function (detail) {
                if (detail.hintNum === this.hintNum) {
                    this.hintText = detail.hintText;
                    this.isActive = true;
                }
            },
        };
    });

    // PWA Install Banner component
    // Listens for custom events from pwa-install.js
    Alpine.data("pwaInstallBanner", function () {
        return {
            show: false,
            platform: "other",
            showGuide: false,
            guideStep: 1,

            get isIos() {
                return this.platform === "ios";
            },

            get isStepOne() {
                return this.guideStep === 1;
            },

            get isStepTwo() {
                return this.guideStep === 2;
            },

            get isStepThree() {
                return this.guideStep === 3;
            },

            get isFirstStep() {
                return this.guideStep === 1;
            },

            get isLastStep() {
                return this.guideStep === 3;
            },

            get hasPrevStep() {
                return this.guideStep > 1;
            },

            get hasNextStep() {
                return this.guideStep < 3;
            },

            get stepTwoIndicatorClass() {
                return this.guideStep >= 2
                    ? "bg-purple-500 w-8"
                    : "bg-gray-200 dark:bg-slate-600 w-4";
            },

            get stepThreeIndicatorClass() {
                return this.guideStep >= 3
                    ? "bg-purple-500 w-8"
                    : "bg-gray-200 dark:bg-slate-600 w-4";
            },

            init: function () {
                var self = this;
                // Listen for show/hide events from pwa-install.js
                window.addEventListener("pwa-show-install", function (e) {
                    self.show = true;
                    if (e.detail && e.detail.platform) {
                        self.platform = e.detail.platform;
                    }
                });
                window.addEventListener("pwa-hide-install", function () {
                    self.show = false;
                });
                // iOS guide modal
                window.addEventListener("pwa-show-ios-guide", function () {
                    self.showGuide = true;
                    self.guideStep = 1;
                });
            },

            dismiss: function () {
                this.show = false;
                // Trigger the dismiss handler in pwa-install.js
                window.dispatchEvent(new CustomEvent("pwa-dismiss-install"));
            },

            openGuide: function () {
                this.showGuide = true;
                this.guideStep = 1;
            },

            closeGuide: function () {
                this.showGuide = false;
                this.guideStep = 1;
            },

            nextStep: function () {
                if (this.guideStep < 3) {
                    this.guideStep++;
                }
            },

            prevStep: function () {
                if (this.guideStep > 1) {
                    this.guideStep--;
                }
            },
        };
    });

    // PWA Success Toast component
    // Shows success message after PWA install, auto-hides after 5 seconds
    Alpine.data("pwaSuccessToast", function () {
        return {
            show: false,

            init: function () {
                var self = this;
                window.addEventListener("pwa-install-success", function () {
                    self.showToast();
                });
            },

            showToast: function () {
                var self = this;
                this.show = true;
                // Auto-hide after 5 seconds
                setTimeout(function () {
                    self.show = false;
                }, 5000);
            },

            close: function () {
                this.show = false;
            },
        };
    });

    // Journey Final Response component (Chapter 6 - The Final Question)
    // Handles yes/thinking response submission with loading states
    Alpine.data("finalResponse", function () {
        return {
            isSubmitting: false,
            statusMessage: "",
            statusType: "", // 'info', 'success', 'error'

            // CSP-safe computed getters
            get isNotSubmitting() {
                return !this.isSubmitting;
            },
            get showStatus() {
                return this.statusMessage !== "";
            },
            get statusClass() {
                if (this.statusType === "success") return "journey-status-success";
                if (this.statusType === "error") return "journey-status-error";
                return "journey-status-info";
            },

            init: function () {
                // Read config from data attributes
                this.submitUrl = this.$el.dataset.submitUrl || "";
                this.i18nSubmitting =
                    this.$el.dataset.i18nSubmitting || "Submitting your response...";
                this.i18nSuccess =
                    this.$el.dataset.i18nSuccess || "Thank you for your response!";
                this.i18nError =
                    this.$el.dataset.i18nError ||
                    "An error occurred. Please try again.";
                this.i18nNetworkError =
                    this.$el.dataset.i18nNetworkError ||
                    "Network error. Please check your connection and try again.";
            },

            submitYes: function () {
                this._submit("yes");
            },

            submitThinking: function () {
                this._submit("thinking");
            },

            _submit: function (response) {
                var self = this;

                if (this.isSubmitting) return;
                this.isSubmitting = true;
                this.statusMessage = "\u23F3 " + this.i18nSubmitting;
                this.statusType = "info";

                fetch(this.submitUrl, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": CrushUtils.getCsrfToken(),
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({ response: response }),
                })
                    .then(function (apiResponse) {
                        return apiResponse.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            self.statusMessage =
                                "\u2705 " + self.i18nSuccess + " \uD83D\uDC96";
                            self.statusType = "success";
                            // Reload page after 2 seconds to show the confirmed response
                            setTimeout(function () {
                                window.location.reload();
                            }, 2000);
                        } else {
                            self.statusMessage =
                                "\u26A0\uFE0F " + (data.message || self.i18nError);
                            self.statusType = "error";
                            self.isSubmitting = false;
                        }
                    })
                    .catch(function (error) {
                        console.error("Error submitting final response:", error);
                        self.statusMessage = "\u26A0\uFE0F " + self.i18nNetworkError;
                        self.statusType = "error";
                        self.isSubmitting = false;
                    });
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

    // Photo slideshow component for journey rewards
    // Reads images from data-images JSON attribute
    // Supports keyboard navigation, touch swipe, and auto-play
    Alpine.data("photoSlideshow", function () {
        return {
            images: [],
            currentIndex: 0,
            isLoading: true,
            touchStartX: 0,
            touchEndX: 0,
            autoPlayInterval: null,
            autoPlayEnabled: false,

            // Computed getters for CSP compatibility
            get hasMultipleImages() {
                return this.images.length > 1;
            },
            get hasSingleImage() {
                return this.images.length === 1;
            },
            get hasNoImages() {
                return this.images.length === 0;
            },
            get totalImages() {
                return this.images.length;
            },
            get currentImageNumber() {
                return this.currentIndex + 1;
            },
            get currentImage() {
                return this.images[this.currentIndex] || null;
            },
            get currentImageUrl() {
                var img = this.images[this.currentIndex];
                return img ? img.url : "";
            },
            get canGoNext() {
                return this.currentIndex < this.images.length - 1;
            },
            get canGoPrev() {
                return this.currentIndex > 0;
            },
            get isNotLoading() {
                return !this.isLoading;
            },
            get progressPercent() {
                if (this.images.length <= 1) return 100;
                return ((this.currentIndex + 1) / this.images.length) * 100;
            },

            // Dot navigation helpers - returns array of booleans for each dot
            get dotStates() {
                var self = this;
                return this.images.map(function (_, idx) {
                    return idx === self.currentIndex;
                });
            },

            // Individual dot state getters (for up to 5 images)
            get isDot0Active() {
                return this.currentIndex === 0;
            },
            get isDot1Active() {
                return this.currentIndex === 1;
            },
            get isDot2Active() {
                return this.currentIndex === 2;
            },
            get isDot3Active() {
                return this.currentIndex === 3;
            },
            get isDot4Active() {
                return this.currentIndex === 4;
            },
            get hasDot0() {
                return this.images.length > 0;
            },
            get hasDot1() {
                return this.images.length > 1;
            },
            get hasDot2() {
                return this.images.length > 2;
            },
            get hasDot3() {
                return this.images.length > 3;
            },
            get hasDot4() {
                return this.images.length > 4;
            },

            // CSP-safe dot class getters (avoid ternary in template)
            get dot0ActiveClass() {
                return this.isDot0Active ? "slideshow-dot-active" : "";
            },
            get dot1ActiveClass() {
                return this.isDot1Active ? "slideshow-dot-active" : "";
            },
            get dot2ActiveClass() {
                return this.isDot2Active ? "slideshow-dot-active" : "";
            },
            get dot3ActiveClass() {
                return this.isDot3Active ? "slideshow-dot-active" : "";
            },
            get dot4ActiveClass() {
                return this.isDot4Active ? "slideshow-dot-active" : "";
            },

            // CSP-safe aria-selected getters
            get dot0AriaSelected() {
                return this.isDot0Active ? "true" : "false";
            },
            get dot1AriaSelected() {
                return this.isDot1Active ? "true" : "false";
            },
            get dot2AriaSelected() {
                return this.isDot2Active ? "true" : "false";
            },
            get dot3AriaSelected() {
                return this.isDot3Active ? "true" : "false";
            },
            get dot4AriaSelected() {
                return this.isDot4Active ? "true" : "false";
            },

            init: function () {
                var self = this;

                // Load images from script tag (preferred) or data attribute (fallback)
                var scriptId = this.$el.getAttribute("data-images-from");
                if (scriptId) {
                    // Load from script tag containing JSON (avoids HTML attribute escaping issues)
                    var scriptEl = document.getElementById(scriptId);
                    if (scriptEl) {
                        try {
                            this.images = JSON.parse(scriptEl.textContent);
                        } catch (e) {
                            console.error(
                                "[PhotoSlideshow] Failed to parse images from script tag:",
                                e,
                            );
                            this.images = [];
                        }
                    }
                } else {
                    // Fallback: load from data-images attribute
                    var imagesData = this.$el.getAttribute("data-images");
                    if (imagesData) {
                        try {
                            this.images = JSON.parse(imagesData);
                        } catch (e) {
                            console.error(
                                "[PhotoSlideshow] Failed to parse images:",
                                e,
                            );
                            this.images = [];
                        }
                    }
                }

                // Preload first image
                if (this.images.length > 0) {
                    var img = new Image();
                    img.onload = function () {
                        self.isLoading = false;
                    };
                    img.onerror = function () {
                        self.isLoading = false;
                    };
                    img.src = this.images[0].url;
                } else {
                    this.isLoading = false;
                }

                // Setup keyboard navigation
                document.addEventListener("keydown", function (e) {
                    if (e.key === "ArrowLeft") {
                        self.prev();
                    } else if (e.key === "ArrowRight") {
                        self.next();
                    }
                });

                // Preload all images in background
                this._preloadImages();
            },

            _preloadImages: function () {
                var self = this;
                this.images.forEach(function (imgData, idx) {
                    if (idx === 0) return; // Already loaded
                    var img = new Image();
                    img.src = imgData.url;
                });
            },

            next: function () {
                if (this.currentIndex < this.images.length - 1) {
                    this.currentIndex++;
                } else {
                    // Loop to beginning
                    this.currentIndex = 0;
                }
            },

            prev: function () {
                if (this.currentIndex > 0) {
                    this.currentIndex--;
                } else {
                    // Loop to end
                    this.currentIndex = this.images.length - 1;
                }
            },

            goTo: function (index) {
                if (index >= 0 && index < this.images.length) {
                    this.currentIndex = index;
                }
            },

            // Individual goTo methods for CSP compatibility (avoid inline expressions)
            goToDot0: function () {
                this.goTo(0);
            },
            goToDot1: function () {
                this.goTo(1);
            },
            goToDot2: function () {
                this.goTo(2);
            },
            goToDot3: function () {
                this.goTo(3);
            },
            goToDot4: function () {
                this.goTo(4);
            },

            // Touch event handlers for swipe support
            handleTouchStart: function (event) {
                this.touchStartX = event.touches[0].clientX;
            },

            handleTouchMove: function (event) {
                this.touchEndX = event.touches[0].clientX;
            },

            handleTouchEnd: function () {
                var diff = this.touchStartX - this.touchEndX;
                var threshold = 50; // Minimum swipe distance

                if (Math.abs(diff) > threshold) {
                    if (diff > 0) {
                        // Swiped left - go next
                        this.next();
                    } else {
                        // Swiped right - go prev
                        this.prev();
                    }
                }

                // Reset touch positions
                this.touchStartX = 0;
                this.touchEndX = 0;
            },

            // Auto-play functionality
            toggleAutoPlay: function () {
                var self = this;
                if (this.autoPlayEnabled) {
                    this.stopAutoPlay();
                } else {
                    this.autoPlayEnabled = true;
                    this.autoPlayInterval = setInterval(function () {
                        self.next();
                    }, 3000);
                }
            },

            stopAutoPlay: function () {
                this.autoPlayEnabled = false;
                if (this.autoPlayInterval) {
                    clearInterval(this.autoPlayInterval);
                    this.autoPlayInterval = null;
                }
            },

            // Download current image
            downloadCurrent: function () {
                if (this.currentImage) {
                    var link = document.createElement("a");
                    link.href = this.currentImage.url;
                    link.download = "photo-" + (this.currentIndex + 1) + ".jpg";
                    link.target = "_blank";
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }
            },
        };
    });

    // Letter audio player with autoplay fallback for browser compatibility
    // Most browsers block autoplay without user interaction, so we provide
    // a graceful fallback with a "click to play" prompt
    Alpine.data("letterAudioPlayer", function () {
        return {
            isPlaying: false,
            autoplayBlocked: false,
            audioElement: null,
            hasInteracted: false,

            // Computed getters for CSP compatibility
            get showPlayPrompt() {
                return this.autoplayBlocked && !this.isPlaying;
            },
            get isNotPlaying() {
                return !this.isPlaying;
            },
            get playButtonClass() {
                return this.isPlaying ? "letter-audio-playing" : "letter-audio-paused";
            },

            init: function () {
                var self = this;
                this.audioElement = this.$refs.audio;

                if (this.audioElement) {
                    // Listen for play/pause events
                    this.audioElement.addEventListener("play", function () {
                        self.isPlaying = true;
                        self.autoplayBlocked = false;
                    });

                    this.audioElement.addEventListener("pause", function () {
                        self.isPlaying = false;
                    });

                    this.audioElement.addEventListener("ended", function () {
                        self.isPlaying = false;
                    });

                    // Attempt autoplay
                    this.attemptAutoplay();
                }
            },

            attemptAutoplay: function () {
                var self = this;
                if (!this.audioElement) return;

                // Try to play - browsers may block this
                var playPromise = this.audioElement.play();

                if (playPromise !== undefined) {
                    playPromise
                        .then(function () {
                            // Autoplay succeeded
                            self.isPlaying = true;
                            self.autoplayBlocked = false;
                        })
                        .catch(function (error) {
                            // Autoplay was blocked by browser
                            self.autoplayBlocked = true;
                            self.isPlaying = false;
                        });
                }
            },

            togglePlay: function () {
                if (!this.audioElement) return;

                this.hasInteracted = true;

                if (this.isPlaying) {
                    this.audioElement.pause();
                } else {
                    this.audioElement.play();
                }
            },

            playMusic: function () {
                if (!this.audioElement) return;

                this.hasInteracted = true;
                this.audioElement.play();
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

    // Event Registration Form - Dynamic form behavior with contextual questions
    Alpine.data("eventRegistration", function () {
        return {
            // State
            bringingGuest: false,
            isSubmitting: false,

            // CSP-safe getters for button text visibility
            get showSubmitText() {
                return !this.isSubmitting;
            },

            get showProcessingText() {
                return this.isSubmitting;
            },

            // Actions
            toggleGuest: function () {
                this.bringingGuest = !this.bringingGuest;
            },

            handleSubmit: function (event) {
                this.isSubmitting = true;
                // Let native form submission or HTMX continue
                if (!event.target.getAttribute("hx-post")) {
                    event.target.submit();
                }
            },
        };
    });

    /**
     * Theme Toggle Component
     *
     * Provides dark mode toggle with system preference detection.
     * Integrates with window.themeManager (from theme-manager.js).
     *
     * Features:
     * - Automatic system preference detection
     * - Manual theme toggle
     * - localStorage persistence
     * - Smooth transitions
     */
    Alpine.data("themeToggle", function () {
        return {
            currentTheme: "light",
            systemPreference: "light",

            init: function () {
                // Initialize from themeManager
                if (window.themeManager) {
                    this.currentTheme = window.themeManager.getTheme();
                }

                // Detect system preference
                if (window.matchMedia) {
                    this.systemPreference = window.matchMedia(
                        "(prefers-color-scheme: dark)",
                    ).matches
                        ? "dark"
                        : "light";

                    // Listen for system preference changes
                    var mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
                    var self = this;

                    var handleChange = function (e) {
                        self.systemPreference = e.matches ? "dark" : "light";
                    };

                    // Modern browsers
                    if (mediaQuery.addEventListener) {
                        mediaQuery.addEventListener("change", handleChange);
                    }
                    // Legacy browsers
                    else if (mediaQuery.addListener) {
                        mediaQuery.addListener(handleChange);
                    }
                }
            },

            // Getters for CSP compliance (no inline expressions in templates)
            get isDark() {
                return this.currentTheme === "dark";
            },

            get isLight() {
                return this.currentTheme === "light";
            },

            get isSystemDark() {
                return this.systemPreference === "dark";
            },

            get toggleButtonClass() {
                return this.isDark
                    ? "bg-gray-700 text-yellow-400 hover:bg-gray-600"
                    : "bg-gray-100 text-gray-700 hover:bg-gray-200";
            },

            get sunIconClass() {
                return this.isLight ? "opacity-100" : "opacity-40";
            },

            get moonIconClass() {
                return this.isDark ? "opacity-100" : "opacity-40";
            },

            get statusText() {
                var saved = localStorage.getItem("theme");
                if (!saved) {
                    return this.isSystemDark ? "System (Dark)" : "System (Light)";
                }
                return this.isDark ? "Dark Mode" : "Light Mode";
            },

            get ariaLabel() {
                return this.isDark ? "Switch to light mode" : "Switch to dark mode";
            },

            get themeLabel() {
                var saved = localStorage.getItem("theme");
                if (!saved) {
                    return this.isSystemDark ? "System (Dark)" : "System (Light)";
                }
                return this.isDark ? "Dark" : "Light";
            },

            get themeButtonLabel() {
                return this.isDark ? "Switch to Light" : "Switch to Dark";
            },

            // Methods
            cycleTheme: function () {
                this.toggleTheme();
            },

            toggleTheme: function () {
                if (window.themeManager) {
                    window.themeManager.toggleTheme();
                    this.currentTheme = window.themeManager.getTheme();
                }
            },

            setTheme: function (theme) {
                if (window.themeManager) {
                    window.themeManager.setTheme(theme);
                    this.currentTheme = theme;
                }
            },

            useSystemPreference: function () {
                localStorage.removeItem("theme");
                this.currentTheme = this.systemPreference;
                if (window.themeManager) {
                    window.themeManager.setTheme(this.systemPreference);
                }
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

    // ============================================================
    // Ghost Story Slideshow
    // ============================================================
    Alpine.data("ghostStory", function () {
        return {
            currentScene: 0,
            totalScenes: 6,
            isPaused: false,
            autoAdvanceTimer: null,
            sceneDurations: [4000, 4000, 5000, 5000, 5000, 6000],

            // Scene visibility getters (CSP-safe)
            get isScene0() {
                return this.currentScene === 0;
            },
            get isScene1() {
                return this.currentScene === 1;
            },
            get isScene2() {
                return this.currentScene === 2;
            },
            get isScene3() {
                return this.currentScene === 3;
            },
            get isScene4() {
                return this.currentScene === 4;
            },
            get isScene5() {
                return this.currentScene === 5;
            },

            // Navigation state
            get isFirstScene() {
                return this.currentScene === 0;
            },
            get isLastScene() {
                return this.currentScene === this.totalScenes - 1;
            },
            get isNotFirstScene() {
                return this.currentScene > 0;
            },
            get isNotLastScene() {
                return this.currentScene < this.totalScenes - 1;
            },

            // Dot active class getters
            get dot0Class() {
                return this.currentScene === 0
                    ? "ghost-story-dot ghost-story-dot-active"
                    : "ghost-story-dot";
            },
            get dot1Class() {
                return this.currentScene === 1
                    ? "ghost-story-dot ghost-story-dot-active"
                    : "ghost-story-dot";
            },
            get dot2Class() {
                return this.currentScene === 2
                    ? "ghost-story-dot ghost-story-dot-active"
                    : "ghost-story-dot";
            },
            get dot3Class() {
                return this.currentScene === 3
                    ? "ghost-story-dot ghost-story-dot-active"
                    : "ghost-story-dot";
            },
            get dot4Class() {
                return this.currentScene === 4
                    ? "ghost-story-dot ghost-story-dot-active"
                    : "ghost-story-dot";
            },
            get dot5Class() {
                return this.currentScene === 5
                    ? "ghost-story-dot ghost-story-dot-active"
                    : "ghost-story-dot";
            },

            // Scene container class getters (CSP-safe, no ternary in template)
            get scene0Class() {
                return this.currentScene === 0
                    ? "ghost-story-scene ghost-story-scene-active ghost-story-scene-0"
                    : "ghost-story-scene ghost-story-scene-0";
            },
            get scene1Class() {
                return this.currentScene === 1
                    ? "ghost-story-scene ghost-story-scene-active ghost-story-scene-1"
                    : "ghost-story-scene ghost-story-scene-1";
            },
            get scene2Class() {
                return this.currentScene === 2
                    ? "ghost-story-scene ghost-story-scene-active ghost-story-scene-2"
                    : "ghost-story-scene ghost-story-scene-2";
            },
            get scene3Class() {
                return this.currentScene === 3
                    ? "ghost-story-scene ghost-story-scene-active ghost-story-scene-3"
                    : "ghost-story-scene ghost-story-scene-3";
            },
            get scene4Class() {
                return this.currentScene === 4
                    ? "ghost-story-scene ghost-story-scene-active ghost-story-scene-4"
                    : "ghost-story-scene ghost-story-scene-4";
            },
            get scene5Class() {
                return this.currentScene === 5
                    ? "ghost-story-scene ghost-story-scene-active ghost-story-scene-5"
                    : "ghost-story-scene ghost-story-scene-5";
            },

            // Pause/play icon
            get pauseIcon() {
                return this.isPaused ? "\u25B6" : "\u275A\u275A";
            },
            get pauseLabel() {
                return this.isPaused ? "Play" : "Pause";
            },

            // Scene counter text
            get sceneCounter() {
                return this.currentScene + 1 + " / " + this.totalScenes;
            },

            init: function () {
                var self = this;
                this.startAutoAdvance();

                // Pause on hover
                this.$el.addEventListener("mouseenter", function () {
                    if (!self.isPaused) {
                        self._hoverPaused = true;
                        self.clearTimer();
                    }
                });
                this.$el.addEventListener("mouseleave", function () {
                    if (self._hoverPaused) {
                        self._hoverPaused = false;
                        if (!self.isPaused) {
                            self.startAutoAdvance();
                        }
                    }
                });
            },

            destroy: function () {
                this.clearTimer();
            },

            clearTimer: function () {
                if (this.autoAdvanceTimer) {
                    clearTimeout(this.autoAdvanceTimer);
                    this.autoAdvanceTimer = null;
                }
            },

            startAutoAdvance: function () {
                var self = this;
                this.clearTimer();
                if (this.isPaused) return;
                var duration = this.sceneDurations[this.currentScene] || 5000;
                this.autoAdvanceTimer = setTimeout(function () {
                    self.nextScene();
                }, duration);
            },

            nextScene: function () {
                if (this.currentScene < this.totalScenes - 1) {
                    this.currentScene++;
                } else {
                    this.currentScene = 0;
                }
                this.startAutoAdvance();
            },

            previousScene: function () {
                if (this.currentScene > 0) {
                    this.currentScene--;
                } else {
                    this.currentScene = this.totalScenes - 1;
                }
                this.startAutoAdvance();
            },

            togglePause: function () {
                this.isPaused = !this.isPaused;
                if (this.isPaused) {
                    this.clearTimer();
                } else {
                    this.startAutoAdvance();
                }
            },

            // Individual goToScene methods (CSP-safe, no param passing)
            goToScene0: function () {
                this.currentScene = 0;
                this.startAutoAdvance();
            },
            goToScene1: function () {
                this.currentScene = 1;
                this.startAutoAdvance();
            },
            goToScene2: function () {
                this.currentScene = 2;
                this.startAutoAdvance();
            },
            goToScene3: function () {
                this.currentScene = 3;
                this.startAutoAdvance();
            },
            goToScene4: function () {
                this.currentScene = 4;
                this.startAutoAdvance();
            },
            goToScene5: function () {
                this.currentScene = 5;
                this.startAutoAdvance();
            },
        };
    });

    // =========================================================================
    // Field Validator - Real-time form field validation (CSP-safe)
    // =========================================================================
    // Usage: <div x-data="fieldValidator"
    //             data-required="true"
    //             data-min-length="2"
    //             data-max-length="100"
    //             data-validation-type="text"
    //             data-error-required="This field is required"
    //             data-error-min-length="Must be at least 2 characters"
    //             data-error-max-length="Must be under 100 characters"
    //             data-error-email="Please enter a valid email">
    //   <input type="text" @input="handleInput" @blur="handleBlur" />
    //   <p x-show="hasError" x-text="errorMessage" class="text-red-600 text-sm mt-1"></p>
    // </div>
    Alpine.data("fieldValidator", function () {
        return {
            value: "",
            error: "",
            isValid: true,
            touched: false,
            _debounceTimer: null,

            // Config from data attributes
            _required: false,
            _minLength: 0,
            _maxLength: 0,
            _validationType: "text",
            _errorRequired: "This field is required",
            _errorMinLength: "",
            _errorMaxLength: "",
            _errorEmail: "Please enter a valid email address",

            init: function () {
                var el = this.$el;
                this._required = el.getAttribute("data-required") === "true";
                this._minLength = parseInt(
                    el.getAttribute("data-min-length") || "0",
                    10,
                );
                this._maxLength = parseInt(
                    el.getAttribute("data-max-length") || "0",
                    10,
                );
                this._validationType =
                    el.getAttribute("data-validation-type") || "text";
                this._errorRequired =
                    el.getAttribute("data-error-required") || this._errorRequired;
                this._errorMinLength =
                    el.getAttribute("data-error-min-length") ||
                    "Must be at least " + this._minLength + " characters";
                this._errorMaxLength =
                    el.getAttribute("data-error-max-length") ||
                    "Must be under " + this._maxLength + " characters";
                this._errorEmail =
                    el.getAttribute("data-error-email") || this._errorEmail;
            },

            get hasError() {
                return this.touched && this.error.length > 0;
            },

            get errorMessage() {
                return this.error;
            },

            get fieldClass() {
                if (!this.touched) return "";
                return this.isValid ? "border-green-500" : "border-red-500";
            },

            _validate: function () {
                var val = this.value.trim();

                if (this._required && val.length === 0) {
                    this.error = this._errorRequired;
                    this.isValid = false;
                    return;
                }

                if (
                    val.length > 0 &&
                    this._minLength > 0 &&
                    val.length < this._minLength
                ) {
                    this.error = this._errorMinLength;
                    this.isValid = false;
                    return;
                }

                if (this._maxLength > 0 && val.length > this._maxLength) {
                    this.error = this._errorMaxLength;
                    this.isValid = false;
                    return;
                }

                if (this._validationType === "email" && val.length > 0) {
                    // Basic email pattern check
                    var atIdx = val.indexOf("@");
                    var dotIdx = val.lastIndexOf(".");
                    if (atIdx < 1 || dotIdx < atIdx + 2 || dotIdx >= val.length - 1) {
                        this.error = this._errorEmail;
                        this.isValid = false;
                        return;
                    }
                }

                this.error = "";
                this.isValid = true;
            },

            handleInput: function () {
                var input = this.$el.querySelector("input, textarea, select");
                if (input) {
                    this.value = input.value;
                }
                // Debounce validation on input (500ms)
                var self = this;
                clearTimeout(this._debounceTimer);
                this._debounceTimer = setTimeout(function () {
                    if (self.touched) {
                        self._validate();
                    }
                }, 500);
            },

            handleBlur: function () {
                var input = this.$el.querySelector("input, textarea, select");
                if (input) {
                    this.value = input.value;
                }
                this.touched = true;
                this._validate();
            },
        };
    });

    // =========================================================================
    // CRUSH SPARK COMPONENTS
    // =========================================================================

    /**
     * sparkRequest - Description form with character counter
     * Used on spark_request.html
     */
    Alpine.data("sparkRequest", function () {
        return {
            description: "",
            maxLength: 1000,

            get charCount() {
                return this.description.length;
            },

            get charsRemaining() {
                return this.maxLength - this.description.length;
            },

            get isValid() {
                return this.description.length >= 10;
            },

            get isOverLimit() {
                return this.description.length > this.maxLength;
            },
        };
    });

    // Spark confirm inline component (replaces browser confirm dialog)
    Alpine.data("sparkConfirm", function () {
        return {
            state: "initial",

            get isInitial() {
                return this.state === "initial";
            },
            get isConfirming() {
                return this.state === "confirming";
            },

            showConfirm() {
                this.state = "confirming";
            },

            cancel() {
                this.state = "initial";
            },
        };
    });

    // Voting demo success state component
    Alpine.data("votingDemoSuccess", function () {
        return {
            submitted: false,
            presChoice: "",
            twistChoice: "",

            get isSubmitted() {
                return this.submitted;
            },
            get isNotSubmitted() {
                return !this.submitted;
            },
            get presChoiceText() {
                return this.presChoice;
            },
            get twistChoiceText() {
                return this.twistChoice;
            },

            submitDemo() {
                var presRadio = document.querySelector(
                    'input[name="demo_presentation"]:checked',
                );
                var twistRadio = document.querySelector(
                    'input[name="demo_twist"]:checked',
                );
                if (!presRadio || !twistRadio) return;

                // Get label text for the selected options
                var presLabel =
                    presRadio.closest("label") ||
                    presRadio.parentElement.querySelector("label") ||
                    presRadio.closest(".variant-option-item").querySelector("strong");
                var twistLabel =
                    twistRadio.closest("label") ||
                    twistRadio.parentElement.querySelector("label") ||
                    twistRadio.closest(".variant-option-item").querySelector("strong");

                this.presChoice = presLabel
                    ? presLabel.querySelector("strong").textContent.trim()
                    : presRadio.value;
                this.twistChoice = twistLabel
                    ? twistLabel.querySelector("strong").textContent.trim()
                    : twistRadio.value;
                this.submitted = true;

                // Auto-scroll to results section after 3 seconds
                var self = this;
                setTimeout(function () {
                    var resultsSection = document.getElementById("step-results");
                    if (resultsSection) {
                        resultsSection.scrollIntoView({
                            behavior: "smooth",
                            block: "start",
                        });
                    }
                }, 3000);
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

    // Event Poll Voting component
    // CSP-safe: all logic in methods/getters, no inline expressions.
    // Each option element has data-option-id; methods read it from $el.
    Alpine.data("eventPollVoting", function () {
        return {
            selectedOptions: [],
            isMultiChoice: false,
            isSubmitting: false,
            hasVoted: false,
            pollId: 0,

            _textSubmit: "Submit Vote",
            _textSubmitting: "Submitting...",
            _textSubmitted: "Vote Submitted",
            _textError: "Failed to submit vote",
            _textNetworkError: "Network error. Please try again.",

            init() {
                var el = this.$el;
                this.isMultiChoice = el.getAttribute("data-multi-choice") === "true";
                this.hasVoted = el.getAttribute("data-has-voted") === "true";
                this.pollId = parseInt(el.getAttribute("data-poll-id") || "0", 10);
                this._textSubmit =
                    el.getAttribute("data-text-submit") || this._textSubmit;
                this._textSubmitting =
                    el.getAttribute("data-text-submitting") || this._textSubmitting;
                this._textSubmitted =
                    el.getAttribute("data-text-submitted") || this._textSubmitted;
                this._textError = el.getAttribute("data-text-error") || this._textError;
                this._textNetworkError =
                    el.getAttribute("data-text-network-error") ||
                    this._textNetworkError;
            },

            get canSubmit() {
                return (
                    this.selectedOptions.length > 0 &&
                    !this.isSubmitting &&
                    !this.hasVoted
                );
            },

            get submitButtonText() {
                if (this.isSubmitting) return this._textSubmitting;
                if (this.hasVoted) return this._textSubmitted;
                return this._textSubmit;
            },

            get isDisabled() {
                return this.isSubmitting || this.hasVoted;
            },

            // CSP-safe: reads data-option-id from the element that has x-bind:class
            get optionClass() {
                var el = this.$el;
                var id = parseInt(el.getAttribute("data-option-id") || "0", 10);
                return this.selectedOptions.indexOf(id) !== -1
                    ? "poll-option-selected"
                    : "";
            },

            // CSP-safe: reads data-option-id for the image overlay check circle
            get checkOverlayClass() {
                var el = this.$el;
                var id = parseInt(el.getAttribute("data-option-id") || "0", 10);
                if (this.selectedOptions.indexOf(id) !== -1) {
                    return "poll-option-check-active";
                }
                return "";
            },

            // CSP-safe: reads data-option-id from the checkbox circle element
            get checkboxClass() {
                var el = this.$el;
                var id = parseInt(el.getAttribute("data-option-id") || "0", 10);
                if (this.selectedOptions.indexOf(id) !== -1) {
                    return "border-crush-purple bg-crush-purple dark:border-violet-500 dark:bg-violet-500";
                }
                return "border-gray-300 dark:border-gray-600";
            },

            // CSP-safe: reads data-option-id from the svg element
            get isOptionSelected() {
                var el = this.$el;
                var id = parseInt(el.getAttribute("data-option-id") || "0", 10);
                return this.selectedOptions.indexOf(id) !== -1;
            },

            // CSP-safe: getter for submit button class
            get submitClass() {
                if (this.canSubmit) {
                    return "bg-crush-purple hover:bg-purple-700 dark:bg-violet-600 dark:hover:bg-violet-700";
                }
                return "bg-gray-300 dark:bg-gray-700 cursor-not-allowed";
            },

            // CSP-safe: reads data-option-id from closest [data-option-id] ancestor
            handleOptionClick: function () {
                if (this.hasVoted || this.isSubmitting) return;

                var el = this.$el;
                var id = parseInt(el.getAttribute("data-option-id") || "0", 10);
                if (!id) return;

                var idx = this.selectedOptions.indexOf(id);
                if (idx !== -1) {
                    this.selectedOptions.splice(idx, 1);
                } else {
                    if (!this.isMultiChoice) {
                        this.selectedOptions = [id];
                    } else {
                        this.selectedOptions.push(id);
                    }
                }
            },

            submitVote: function () {
                if (!this.canSubmit) return;

                var self = this;
                self.isSubmitting = true;

                var csrfToken = document.querySelector("[name=csrfmiddlewaretoken]");
                var token = csrfToken ? csrfToken.value : "";

                fetch("/api/polls/" + self.pollId + "/vote/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": token,
                    },
                    body: JSON.stringify({ option_ids: self.selectedOptions }),
                })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        self.isSubmitting = false;
                        if (data.success) {
                            self.hasVoted = true;
                            window.location.reload();
                        } else {
                            alert(data.error || self._textError);
                        }
                    })
                    .catch(function () {
                        self.isSubmitting = false;
                        alert(self._textNetworkError);
                    });
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
                        self.error = "Something went wrong. Please try again.";
                    });
            },
        };
    });

    // SMS Invite Filter - gender filter for coach event SMS invite page
    Alpine.data("smsInviteFilter", function () {
        return {
            activeFilter: "all",

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
                            self.claimError = result.data.error || "Claim failed";
                        }
                    })
                    .catch(function () {
                        self.claimingId = null;
                        self.claimError = "Network error";
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

    // =========================================================================
    // Trait Selector Component (Matching System)
    // =========================================================================
    // CSP-safe chip-based selector for qualities, defects, and sought qualities.
    // Traits are server-rendered as buttons; this component tracks selection state.
    //
    // Usage (container):
    //   <div x-data="traitSelector" data-max="5" data-initial="[1,2,3]">
    //
    // Usage (each chip button, server-rendered via Django {% for %}):
    //   <button type="button" data-trait-id="42" @click="handleClick" ...>
    //
    Alpine.data("traitSelector", function () {
        return {
            selected: [],
            maxItems: 5,

            init: function () {
                this.maxItems = parseInt(this.$el.getAttribute("data-max") || "5", 10);
                var initialStr = this.$el.getAttribute("data-initial");
                if (initialStr) {
                    try {
                        this.selected = JSON.parse(initialStr);
                    } catch (e) {
                        this.selected = [];
                    }
                }
                // Apply initial visual state to all chip buttons
                this._syncAllChips();
            },

            get counterText() {
                return this.selected.length + "/" + this.maxItems;
            },

            get hiddenValue() {
                return this.selected.join(",");
            },

            handleClick: function () {
                // Read trait ID from the clicked button's data attribute
                var btn = this.$el;
                if (!btn.hasAttribute("data-trait-id")) {
                    // Clicked on container, find closest button
                    return;
                }
                var id = parseInt(btn.getAttribute("data-trait-id"), 10);
                var idx = this.selected.indexOf(id);
                if (idx > -1) {
                    this.selected.splice(idx, 1);
                } else if (this.selected.length < this.maxItems) {
                    this.selected.push(id);
                }
                this._syncAllChips();
                this.$nextTick(
                    function () {
                        this.$dispatch("profile-autosave:trigger");
                    }.bind(this),
                );
            },

            _syncAllChips: function () {
                var self = this;
                var container = this.$el.closest("[x-data]");
                if (!container) return;
                var buttons = container.querySelectorAll("[data-trait-id]");
                var isMax = self.selected.length >= self.maxItems;
                var accentColor = container.getAttribute("data-accent") || "purple";
                for (var i = 0; i < buttons.length; i++) {
                    var btn = buttons[i];
                    var traitId = parseInt(btn.getAttribute("data-trait-id"), 10);
                    var isSelected = self.selected.indexOf(traitId) > -1;
                    var isDisabled = isMax && !isSelected;

                    // Remove all state classes
                    btn.classList.remove(
                        "border-purple-500",
                        "bg-purple-100",
                        "dark:bg-purple-900/40",
                        "text-purple-700",
                        "dark:text-purple-300",
                        "border-pink-500",
                        "bg-pink-100",
                        "dark:bg-pink-900/40",
                        "text-pink-700",
                        "dark:text-pink-300",
                        "border-green-500",
                        "bg-green-100",
                        "dark:bg-green-900/40",
                        "text-green-700",
                        "dark:text-green-300",
                        "border-gray-200",
                        "dark:border-gray-600",
                        "dark:border-gray-700",
                        "bg-white",
                        "dark:bg-gray-800",
                        "bg-gray-100",
                        "text-gray-700",
                        "dark:text-gray-200",
                        "text-gray-400",
                        "dark:text-gray-600",
                        "cursor-not-allowed",
                        "hover:border-purple-300",
                        "hover:bg-purple-50",
                        "dark:hover:bg-purple-900/30",
                        "hover:border-pink-300",
                        "hover:bg-pink-50",
                        "dark:hover:bg-pink-900/30",
                        "hover:border-green-300",
                        "hover:bg-green-50",
                        "dark:hover:bg-green-900/30",
                    );

                    if (isSelected) {
                        btn.classList.add(
                            "border-" + accentColor + "-500",
                            "bg-" + accentColor + "-100",
                            "dark:bg-" + accentColor + "-900/40",
                            "text-" + accentColor + "-700",
                            "dark:text-" + accentColor + "-300",
                        );
                    } else if (isDisabled) {
                        btn.classList.add(
                            "border-gray-200",
                            "dark:border-gray-700",
                            "bg-gray-100",
                            "dark:bg-gray-800",
                            "text-gray-400",
                            "dark:text-gray-600",
                            "cursor-not-allowed",
                        );
                    } else {
                        btn.classList.add(
                            "border-gray-200",
                            "dark:border-gray-600",
                            "bg-white",
                            "dark:bg-gray-800",
                            "text-gray-700",
                            "dark:text-gray-200",
                        );
                        btn.classList.add(
                            "hover:border-" + accentColor + "-300",
                            "hover:bg-" + accentColor + "-50",
                            "dark:hover:bg-" + accentColor + "-900/30",
                        );
                    }
                }
            },
        };
    });

    // =========================================================================
    // Astro Toggle Component (Matching System)
    // =========================================================================
    Alpine.data("astroToggle", function () {
        return {
            enabled: true,

            init: function () {
                this.enabled = this.$el.getAttribute("data-initial") === "true";
                this._syncVisual();
            },

            get isEnabled() {
                return this.enabled;
            },

            get isDisabled() {
                return !this.enabled;
            },

            get toggleBgClass() {
                return this.enabled ? "bg-purple-500" : "bg-gray-300 dark:bg-gray-600";
            },

            get toggleKnobClass() {
                return this.enabled ? "translate-x-6" : "translate-x-1";
            },

            toggle: function () {
                this.enabled = !this.enabled;
                this._syncVisual();
                this.$nextTick(
                    function () {
                        this.$dispatch("profile-autosave:trigger");
                    }.bind(this),
                );
            },

            _syncVisual: function () {
                // Sync toggle button visual state
                var toggleBtn = this.$el.querySelector("[data-toggle-btn]");
                var knob = this.$el.querySelector("[data-toggle-knob]");
                if (toggleBtn) {
                    toggleBtn.classList.remove(
                        "bg-purple-500",
                        "bg-gray-300",
                        "dark:bg-gray-600",
                    );
                    if (this.enabled) {
                        toggleBtn.classList.add("bg-purple-500");
                    } else {
                        toggleBtn.classList.add("bg-gray-300", "dark:bg-gray-600");
                    }
                }
                if (knob) {
                    knob.classList.remove("translate-x-6", "translate-x-1");
                    knob.classList.add(
                        this.enabled ? "translate-x-6" : "translate-x-1",
                    );
                }
            },
        };
    });

    // -----------------------------------------------------------------------
    // Quiz Question Form – coach quiz configuration (multilingual)
    // -----------------------------------------------------------------------
    Alpine.data("quizQuestionForm", function () {
        return {
            questionType: "multiple_choice",
            activeLang: "en",
            // Per-language choices arrays
            choicesEn: [],
            choicesDe: [],
            choicesFr: [],
            correctTrueFalse: "true",

            init: function () {
                var el = this.$el;
                this.questionType =
                    el.getAttribute("data-question-type") || "multiple_choice";

                var langs = ["en", "de", "fr"];
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var i = 0; i < langs.length; i++) {
                    var raw = el.getAttribute("data-choices-" + langs[i]);
                    if (raw) {
                        try {
                            this[props[i]] = JSON.parse(raw);
                        } catch (e) {
                            this[props[i]] = [];
                        }
                    }
                }

                if (this.questionType === "multiple_choice") {
                    for (var j = 0; j < props.length; j++) {
                        if (this[props[j]].length === 0) {
                            this[props[j]] = [
                                { text: "", isCorrect: false },
                                { text: "", isCorrect: false },
                            ];
                        }
                    }
                }

                if (this.questionType === "true_false") {
                    var ref = this.choicesEn;
                    var correct = null;
                    for (var k = 0; k < ref.length; k++) {
                        if (ref[k].isCorrect) {
                            correct = ref[k];
                            break;
                        }
                    }
                    this.correctTrueFalse = correct
                        ? correct.text.toLowerCase()
                        : "true";
                }

                this._renderAllChoices();
            },

            // Current language's choices (getter)
            get currentChoices() {
                if (this.activeLang === "de") return this.choicesDe;
                if (this.activeLang === "fr") return this.choicesFr;
                return this.choicesEn;
            },

            get isMultipleChoice() {
                return this.questionType === "multiple_choice";
            },
            get isTrueFalse() {
                return this.questionType === "true_false";
            },
            get isOpenEnded() {
                return this.questionType === "open_ended";
            },

            // Language tab switching
            get isLangEn() {
                return this.activeLang === "en";
            },
            get isLangDe() {
                return this.activeLang === "de";
            },
            get isLangFr() {
                return this.activeLang === "fr";
            },
            setLangEn: function () {
                this.activeLang = "en";
            },
            setLangDe: function () {
                this.activeLang = "de";
            },
            setLangFr: function () {
                this.activeLang = "fr";
            },
            get langEnClass() {
                return this.activeLang === "en"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },
            get langDeClass() {
                return this.activeLang === "de"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },
            get langFrClass() {
                return this.activeLang === "fr"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
            },

            selectType: function () {
                var sel = this.$refs.typeSelect;
                if (!sel) return;
                this.questionType = sel.value;
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                if (this.questionType === "true_false") {
                    for (var i = 0; i < props.length; i++) {
                        this[props[i]] = [
                            { text: "True", isCorrect: true },
                            { text: "False", isCorrect: false },
                        ];
                    }
                    this.correctTrueFalse = "true";
                } else if (this.questionType === "multiple_choice") {
                    for (var j = 0; j < props.length; j++) {
                        if (this[props[j]].length < 2) {
                            this[props[j]] = [
                                { text: "", isCorrect: false },
                                { text: "", isCorrect: false },
                            ];
                        }
                    }
                }
                this._renderAllChoices();
            },

            addChoice: function () {
                this.choicesEn.push({ text: "", isCorrect: false });
                this.choicesDe.push({ text: "", isCorrect: false });
                this.choicesFr.push({ text: "", isCorrect: false });
                this._renderAllChoices();
            },

            removeChoice: function () {
                var btn = this.$el;
                var idx = parseInt(btn.getAttribute("data-index"), 10);
                if (isNaN(idx)) return;
                this.choicesEn.splice(idx, 1);
                this.choicesDe.splice(idx, 1);
                this.choicesFr.splice(idx, 1);
            },

            setCorrect: function () {
                var btn = this.$el;
                var idx = parseInt(btn.getAttribute("data-index"), 10);
                if (isNaN(idx)) return;
                // Sync is_correct across all languages
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var p = 0; p < props.length; p++) {
                    var arr = this[props[p]];
                    for (var i = 0; i < arr.length; i++) {
                        arr[i].isCorrect = i === idx;
                    }
                }
            },

            setTrueFalseTrue: function () {
                this.correctTrueFalse = "true";
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var i = 0; i < props.length; i++) {
                    this[props[i]] = [
                        { text: "True", isCorrect: true },
                        { text: "False", isCorrect: false },
                    ];
                }
                this._renderAllChoices();
            },

            setTrueFalseFalse: function () {
                this.correctTrueFalse = "false";
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var i = 0; i < props.length; i++) {
                    this[props[i]] = [
                        { text: "True", isCorrect: false },
                        { text: "False", isCorrect: true },
                    ];
                }
                this._renderAllChoices();
            },

            updateChoiceText: function () {
                var input = this.$el;
                var idx = parseInt(input.getAttribute("data-index"), 10);
                var choices = this.currentChoices;
                if (isNaN(idx) || !choices[idx]) return;
                choices[idx].text = input.value;
            },

            // --- CSP-safe imperative DOM rendering for choices ---
            _placeholders: {
                en: "Choice text...",
                de: "Antworttext...",
                fr: "Texte du choix...",
            },

            _renderAllChoices: function () {
                this._renderChoices("en");
                this._renderChoices("de");
                this._renderChoices("fr");
            },

            _renderChoices: function (lang) {
                var container = this.$el.querySelector(
                    '[data-choices-container="' + lang + '"]',
                );
                if (!container) return;

                var self = this;
                var propName = "choices" + lang.charAt(0).toUpperCase() + lang.slice(1);
                var choices = this[propName];
                var placeholder = this._placeholders[lang] || "Choice text...";
                container.innerHTML = "";

                for (var i = 0; i < choices.length; i++) {
                    (function (index) {
                        var choice = choices[index];
                        var row = document.createElement("div");
                        row.className = "flex items-center gap-2 mb-2";

                        // Correct-answer button
                        var btn = document.createElement("button");
                        btn.type = "button";
                        btn.className =
                            "shrink-0 w-5 h-5 rounded-full border-2 flex items-center justify-center transition-colors" +
                            (choice.isCorrect
                                ? " border-green-500 bg-green-500"
                                : " border-gray-300 dark:border-gray-600 hover:border-green-400");
                        if (choice.isCorrect) {
                            btn.innerHTML =
                                '<svg class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
                                '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>';
                        }
                        btn.addEventListener("click", function () {
                            self._setCorrectByIndex(index);
                        });
                        row.appendChild(btn);

                        // Text input
                        var input = document.createElement("input");
                        input.type = "text";
                        input.value = choice.text;
                        input.placeholder = placeholder;
                        input.className =
                            "flex-1 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white px-3 py-2 text-sm focus:ring-2 focus:ring-crush-purple focus:border-transparent";
                        input.addEventListener("input", function () {
                            self._updateChoiceTextByIndex(lang, index, input.value);
                        });
                        row.appendChild(input);

                        // Remove button
                        var removeBtn = document.createElement("button");
                        removeBtn.type = "button";
                        removeBtn.className =
                            "shrink-0 p-1.5 text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400 transition-colors";
                        removeBtn.innerHTML =
                            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
                            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>';
                        removeBtn.addEventListener("click", function () {
                            self._removeChoiceByIndex(index);
                        });
                        row.appendChild(removeBtn);

                        container.appendChild(row);
                    })(i);
                }
            },

            _setCorrectByIndex: function (idx) {
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var p = 0; p < props.length; p++) {
                    var arr = this[props[p]];
                    for (var i = 0; i < arr.length; i++) {
                        arr[i].isCorrect = i === idx;
                    }
                }
                this._renderAllChoices();
            },

            _updateChoiceTextByIndex: function (lang, idx, value) {
                var propName = "choices" + lang.charAt(0).toUpperCase() + lang.slice(1);
                var choices = this[propName];
                if (choices[idx]) {
                    choices[idx].text = value;
                }
                // No re-render: only the data model changes, input keeps focus
            },

            _removeChoiceByIndex: function (idx) {
                this.choicesEn.splice(idx, 1);
                this.choicesDe.splice(idx, 1);
                this.choicesFr.splice(idx, 1);
                this._renderAllChoices();
            },

            get choicesJsonEn() {
                return JSON.stringify(this.choicesEn);
            },
            get choicesJsonDe() {
                return JSON.stringify(this.choicesDe);
            },
            get choicesJsonFr() {
                return JSON.stringify(this.choicesFr);
            },

            get trueFalseTrueClass() {
                return this.correctTrueFalse === "true"
                    ? "bg-green-600 text-white border-green-600"
                    : "bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 border-gray-300 dark:border-gray-600";
            },

            get trueFalseFalseClass() {
                return this.correctTrueFalse === "false"
                    ? "bg-red-600 text-white border-red-600"
                    : "bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 border-gray-300 dark:border-gray-600";
            },

            get canSubmit() {
                if (this.questionType === "open_ended") return true;
                if (this.questionType === "true_false") return true;
                // At least one language must have valid choices
                var props = ["choicesEn", "choicesDe", "choicesFr"];
                for (var p = 0; p < props.length; p++) {
                    var arr = this[props[p]];
                    if (arr.length >= 2) {
                        var hasCorrect = false;
                        var allFilled = true;
                        for (var i = 0; i < arr.length; i++) {
                            if (arr[i].isCorrect) hasCorrect = true;
                            if (!arr[i].text || !arr[i].text.trim()) allFilled = false;
                        }
                        if (hasCorrect && allFilled) return true;
                    }
                }
                return false;
            },

            get submitClass() {
                return this.canSubmit
                    ? "bg-crush-purple hover:bg-purple-700 text-white"
                    : "bg-gray-300 dark:bg-gray-700 text-gray-500 cursor-not-allowed";
            },
        };
    });

    // ── Submission Status (profile_submitted.html pending state) ──
    Alpine.data("submissionStatus", function () {
        return {
            // State from server (initialized via data-* attributes)
            status: "",
            queuePosition: 0,
            totalPending: 0,
            hoursWaiting: 0,
            waitStatus: "",
            progressPercent: 0,

            // Note state
            noteText: "",
            noteSending: false,
            noteSent: false,
            noteError: "",

            // UI toggles
            showCallPrep: false,
            showNoteForm: false,

            // Polling
            pollInterval: null,

            init: function () {
                var el = this.$el;
                this.status = el.dataset.status || "pending";
                this.queuePosition = parseInt(el.dataset.queuePosition || "0", 10);
                this.totalPending = parseInt(el.dataset.totalPending || "0", 10);
                this.hoursWaiting = parseFloat(el.dataset.hoursWaiting || "0");
                this.waitStatus = el.dataset.waitStatus || "fresh";
                this.progressPercent = parseInt(el.dataset.progressPercent || "0", 10);
                this.noteSent = el.dataset.hasNote === "true";

                if (this.status === "pending") {
                    this.startPolling();
                }
            },

            startPolling: function () {
                var self = this;
                self.pollInterval = setInterval(function () {
                    self.checkStatus();
                }, 60000);
            },

            checkStatus: function () {
                var self = this;
                fetch("/api/submission/status/", { credentials: "same-origin" })
                    .then(function (r) {
                        return r.json();
                    })
                    .then(function (data) {
                        if (data.status !== self.status) {
                            window.location.reload();
                            return;
                        }
                        self.queuePosition = data.queue_position;
                        self.hoursWaiting = data.hours_waiting;
                        self.waitStatus = data.wait_status;
                    })
                    .catch(function () {
                        // Silently ignore polling errors
                    });
            },

            sendNote: function () {
                if (!this.isNoteValid || this.noteSending) return;
                var self = this;
                self.noteSending = true;
                self.noteError = "";

                var csrfToken = "";
                var csrfEl = document.querySelector('[name="csrfmiddlewaretoken"]');
                if (csrfEl) csrfToken = csrfEl.value;

                fetch("/api/submission/note/", {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken,
                    },
                    body: JSON.stringify({ note: self.noteText }),
                })
                    .then(function (r) {
                        return r.json().then(function (data) {
                            return { ok: r.ok, data: data };
                        });
                    })
                    .then(function (result) {
                        self.noteSending = false;
                        if (result.ok) {
                            self.noteSent = true;
                            self.showNoteForm = false;
                        } else {
                            self.noteError =
                                result.data.error || "Something went wrong";
                        }
                    })
                    .catch(function () {
                        self.noteSending = false;
                        self.noteError = "Network error. Please try again.";
                    });
            },

            toggleCallPrep: function () {
                this.showCallPrep = !this.showCallPrep;
            },

            toggleNoteForm: function () {
                this.showNoteForm = !this.showNoteForm;
            },

            updateNoteText: function (e) {
                this.noteText = e.target.value;
            },

            // CSP-safe getters
            get isPending() {
                return this.status === "pending";
            },
            get isNoteSending() {
                return this.noteSending;
            },
            get isNoteNotSending() {
                return !this.noteSending;
            },
            get isNoteSent() {
                return this.noteSent;
            },
            get isNoteNotSent() {
                return !this.noteSent;
            },
            get hasNoteError() {
                return this.noteError !== "";
            },
            get isNoteValid() {
                return this.noteText.length >= 10 && this.noteText.length <= 500;
            },
            get noteCharCount() {
                return this.noteText.length + "/500";
            },
            get isCallPrepOpen() {
                return this.showCallPrep;
            },
            get isNoteFormOpen() {
                return this.showNoteForm && !this.noteSent;
            },
            get showQueuePosition() {
                return this.queuePosition > 0;
            },
            get waitBadgeClass() {
                if (this.waitStatus === "fresh")
                    return "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300";
                if (this.waitStatus === "normal")
                    return "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300";
                if (this.waitStatus === "extended")
                    return "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300";
                return "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300";
            },
            get waitBadgeText() {
                if (this.waitStatus === "fresh") return "Just submitted";
                if (this.waitStatus === "normal") return "In progress";
                if (this.waitStatus === "extended") return "Taking a bit longer";
                return "Extended wait";
            },
            get noteIconBgClass() {
                return this.noteSent
                    ? "bg-green-100 dark:bg-green-900/30"
                    : "bg-blue-100 dark:bg-blue-900/30";
            },
            get noteButtonClass() {
                return this.isNoteValid && !this.noteSending
                    ? "bg-crush-purple hover:bg-purple-700 text-white"
                    : "bg-gray-300 dark:bg-gray-700 text-gray-500 cursor-not-allowed";
            },

            destroy: function () {
                if (this.pollInterval) clearInterval(this.pollInterval);
            },
        };
    });

    // Photo slot picker popover (profile edit "Photos" section)
    // Reads slot number from data-slot on the root element.
    // Imports a social photo via POST /api/profile/import-social-photo/ and
    // swaps the returned HTML into #photo-card-{slot}.
    Alpine.data("photoPicker", function () {
        return {
            slot: 0,
            isOpen: false,
            pending: false,
            error: "",

            get isClosed() {
                return !this.isOpen;
            },
            get hasError() {
                return this.error.length > 0;
            },
            get errorMessage() {
                return this.error;
            },
            get triggerDisabled() {
                return this.pending;
            },
            get ariaExpanded() {
                return this.isOpen ? "true" : "false";
            },

            init: function () {
                this.slot = parseInt(this.$el.getAttribute("data-slot")) || 0;
            },

            toggle: function () {
                if (this.pending) return;
                this.isOpen = !this.isOpen;
                if (!this.isOpen) this.error = "";
            },

            close: function () {
                this.isOpen = false;
                this.error = "";
            },

            importFromProvider: function (event) {
                var self = this;
                var btn = event.currentTarget;
                var accountId = parseInt(btn.getAttribute("data-account-id"));
                if (!accountId || !this.slot) return;

                self.pending = true;
                self.error = "";

                var csrfEl = document.querySelector("[name=csrfmiddlewaretoken]");
                var csrfToken = csrfEl ? csrfEl.value : "";
                var importUrl = "/api/profile/import-social-photo/";

                fetch(importUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken,
                    },
                    body: JSON.stringify({
                        social_account_id: accountId,
                        photo_slot: self.slot,
                    }),
                })
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        if (data.success) {
                            var targetSlot = data.photo_slot || self.slot;
                            var card = document.getElementById(
                                "photo-card-" + targetSlot,
                            );
                            if (card && data.html) {
                                card.innerHTML = data.html;
                                if (window.htmx) window.htmx.process(card);
                            }
                            self.close();
                        } else {
                            self.error = data.error || "Import failed.";
                        }
                    })
                    .catch(function () {
                        self.error = "Network error. Please try again.";
                    })
                    .finally(function () {
                        self.pending = false;
                    });
            },
        };
    });

    // Onboarding step-2 "Continue" gate.
    // Shows the Continue button once the phone-verified window event fires
    // (dispatched by phoneVerificationComponent on successful SMS verify).
    // Reads initial state from the element's data-initial-verified attribute
    // so a user who already has phone_verified=True sees the button right
    // away without having to re-verify.
    Alpine.data("onboardingPhoneContinue", function () {
        return {
            verified: false,

            get isVerified() {
                return this.verified;
            },
            get isNotVerified() {
                return !this.verified;
            },

            init: function () {
                var self = this;
                var initial = this.$el.getAttribute("data-initial-verified");
                this.verified = initial === "true";
                window.addEventListener("phone-verified", function () {
                    self.verified = true;
                });
            },
        };
    });
});
