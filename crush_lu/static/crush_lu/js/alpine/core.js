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
 *
 * Core bundle: shared shell and site-wide components. Feature
 * components live in the sibling entries (coach.js, quiz.js,
 * journey.js, connect.js); pages load them via
 * crush_lu/partials/alpine_bundle.html. Build: `npm run build:js`.
 */

import { makeConfirm, makeTabs, mixin, notifyError } from "./shared.js";

document.addEventListener("alpine:init", function () {
    // Global drawer store for mobile side menu
    Alpine.store("drawer", {
        open: false,
        toggle() {
            this.open = !this.open;
            if (this.open) {
                document.body.style.overflow = "hidden";
            } else {
                document.body.style.overflow = "";
            }
        },
        close() {
            this.open = false;
            document.body.style.overflow = "";
        }
    });

    // Prompt queue (crush_lu/STYLE.md §8): at most one of the cookie sheet,
    // flash messages, the install card and the push prompt at a time, in
    // that order. The cookie sheet (shared core partial, no Alpine) reports
    // itself via `cookie-banner-toggle`; flash messages are never hidden,
    // they only hold install/push back until dismissed or auto-hidden.
    // Install/push wait for DOMContentLoaded, when the sheet has decided,
    // and never show on a first visit: pwa-install.js (deferred, so it has
    // run by then) counts visits in localStorage "crush-pwa-sessions".
    var PROMPT_ORDER = ["cookie", "messages", "install", "push"];
    Alpine.store("prompts", {
        ready: false,
        returning: false,
        cookie: false,
        messages: 0,
        install: false,
        push: false,
        init() {
            var self = this;
            var sheet = document.getElementById("cookie-consent-banner");
            this.cookie = !!sheet && sheet.style.display === "block";
            document.addEventListener("cookie-banner-toggle", function (e) {
                self.cookie = !!(e.detail && e.detail.open);
            });
            function markReady() {
                try {
                    var visits = localStorage.getItem("crush-pwa-sessions");
                    self.returning = (parseInt(visits, 10) || 0) >= 2;
                } catch (e) {
                    self.returning = false; // storage blocked: never nag
                }
                self.ready = true;
            }
            if (document.readyState === "complete") {
                markReady();
            } else {
                document.addEventListener("DOMContentLoaded", markReady);
                window.addEventListener("load", markReady);
            }
        },
        get active() {
            for (var i = 0; i < PROMPT_ORDER.length; i++) {
                var name = PROMPT_ORDER[i];
                if (this[name]) {
                    return i < 2 || (this.ready && this.returning) ? name : null;
                }
            }
            return null;
        },
        isActive(name) {
            return this.active === name;
        },
        set(name, on) {
            this[name] = !!on;
        },
        holdMessage() {
            this.messages += 1;
        },
        releaseMessage() {
            this.messages = Math.max(0, this.messages - 1);
        },
    });

    // Legal pages' floating "Back to contents" pill. It sits in the corner the
    // bottom prompts (cookie sheet, install card, push prompt) occupy, so it
    // steps aside while one of them is the active prompt. Flash messages are
    // not bottom-anchored and never hide it.
    Alpine.data("legalBackToContents", function () {
        return {
            get visible() {
                var active = Alpine.store("prompts").active;
                return active !== "cookie" && active !== "install" && active !== "push";
            },
        };
    });

    // UX Wave 3 · WP5 (finding 3-14) — client-side downscale/re-encode
    // before a profile photo is uploaded. Keeps mobile uploads out of the
    // 4-12MB range the coach review queue was seeing. Uses
    // createImageBitmap({imageOrientation: 'from-image'}) so EXIF rotation
    // is baked into the pixels instead of relying on <img> auto-rotation
    // (which canvas drawImage does not inherit). Falls back to returning
    // the original file untouched if the browser lacks canvas/bitmap
    // support — server-side validation is unchanged either way.
    function resizeImageForUpload(file, maxEdge, quality) {
        maxEdge = maxEdge || 2048;
        quality = quality || 0.85;
        if (!file || typeof file.type !== "string" || file.type.indexOf("image/") !== 0) {
            return Promise.resolve(file);
        }
        if (typeof createImageBitmap !== "function" || typeof document.createElement("canvas").getContext !== "function") {
            return Promise.resolve(file);
        }
        return createImageBitmap(file, { imageOrientation: "from-image" })
            .then(function (bitmap) {
                var scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
                var shortEdge = Math.min(bitmap.width, bitmap.height);
                // The final profile form requires both sides to be at least
                // 200px. Keep an originally valid panorama valid even when
                // that means its long edge remains above the usual target.
                if (shortEdge >= 200 && Math.round(shortEdge * scale) < 200) {
                    scale = Math.min(1, 200 / shortEdge);
                }
                var width = Math.max(1, Math.round(bitmap.width * scale));
                var height = Math.max(1, Math.round(bitmap.height * scale));
                var canvas = document.createElement("canvas");
                canvas.width = width;
                canvas.height = height;
                var ctx = canvas.getContext("2d");
                ctx.drawImage(bitmap, 0, 0, width, height);
                if (bitmap.close) bitmap.close();
                return new Promise(function (resolve) {
                    canvas.toBlob(
                        function (blob) {
                            resolve(blob || file);
                        },
                        "image/jpeg",
                        quality,
                    );
                });
            })
            .then(function (blob) {
                if (!blob || blob === file) return file;
                var name = (file.name || "photo").replace(/\.[^.]+$/, "") + ".jpg";
                try {
                    return new File([blob], name, { type: "image/jpeg" });
                } catch (e) {
                    // Older Safari lacks the File constructor's options form.
                    return blob;
                }
            })
            .catch(function () {
                return file;
            });
    }

    // UX Wave 3 · WP5 (finding 3-14) — the profile wizard's Photos step
    // (`photoUpload`, nested in its own x-data scope inside
    // create_profile.html) resizes then uploads each photo asynchronously;
    // the outer wizard's "Continue" button lives in a different scope and
    // had no way to see that work in flight. A member who picks a large
    // photo and taps Continue immediately used to advance to Review before
    // the upload request was even created — the final page-unload could
    // then abandon it, leaving a preview of a photo that was never saved.
    // Tracked at module scope (same idiom as _photoUploadDeprecationLogged
    // above) so the wizard component can await it without cross-scope
    // Alpine wiring.
    var _pendingPhotoUploads = [];
    var _photoStepAdvancing = false;
    function trackPendingPhotoUpload(promise) {
        _pendingPhotoUploads.push(promise);
        var settle = function () {
            var idx = _pendingPhotoUploads.indexOf(promise);
            if (idx !== -1) _pendingPhotoUploads.splice(idx, 1);
        };
        promise.then(settle, settle);
    }
    function waitForPendingPhotoUploads() {
        if (!_pendingPhotoUploads.length) return Promise.resolve();
        // A selection or removal may be added while the current batch is
        // settling. Drain the live set before advancing the wizard.
        return Promise.all(
            _pendingPhotoUploads.slice().map(function (promise) {
                return promise.then(function () {}, function () {});
            }),
        ).then(waitForPendingPhotoUploads);
    }

    // Push settings cards: navigator.serviceWorker.ready never settles when no
    // worker is registered (failed registration, private mode, DEBUG's
    // sw-unregister), so give up on the "Checking…" spinner after 3 s and
    // offer a Retry. A late answer still settles the card normally.
    var PUSH_CHECK_TIMEOUT_MS = 3000;
    // Platform-aware "blocked" copy (#1064): each OS has its own path back to
    // the notification permission, so one generic sentence misleads. Shared by
    // the member and the coach push cards (compose with mixin(), never spread).
    function makeBlockedPlatform() {
        return {
            blockedPlatform: "desktop",
            get blockedOnIos() {
                return this.blockedPlatform === "ios";
            },
            get blockedOnAndroid() {
                return this.blockedPlatform === "android";
            },
            get blockedOnDesktop() {
                return this.blockedPlatform === "desktop";
            },
            _detectBlockedPlatform: function () {
                // iPadOS Safari reports a desktop "Macintosh" UA; touch points tell it apart.
                var ua = navigator.userAgent || "";
                if (
                    /iPhone|iPad|iPod/.test(ua) ||
                    (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)
                ) {
                    this.blockedPlatform = "ios";
                } else if (/Android/i.test(ua)) {
                    this.blockedPlatform = "android";
                }
            },
        };
    }
    function makePushStatusCheck() {
        return {
            checkTimedOut: false,
            _checkRun: 0,
            // "Not supported" and a browser-level "blocked" are the truthful
            // answers (Retry cannot change either), so both win over Retry.
            get showCheckTimedOut() {
                return (
                    !this.isLoading &&
                    this.checkTimedOut &&
                    this.isSupported &&
                    !this.permissionDenied
                );
            },
            // probe(finish) runs the component's own check and calls finish().
            // An unsupported browser has nothing to probe: settle at once.
            _runStatusCheck: function (probe) {
                var self = this;
                var run = ++this._checkRun;
                this.isLoading = true;
                this.checkTimedOut = false;
                setTimeout(function () {
                    if (run === self._checkRun && self.isLoading) {
                        self.isLoading = false;
                        self.checkTimedOut = true;
                    }
                }, PUSH_CHECK_TIMEOUT_MS);
                var finish = function () {
                    if (run !== self._checkRun) return;
                    self.isLoading = false;
                    self.checkTimedOut = false;
                    self.$nextTick(function () {
                        self._retryDeviceMatch();
                    });
                };
                if (!this.isSupported) return finish();
                probe(finish);
            },
            retryStatusCheck: function () {
                this._checkStatus();
            },
        };
    }

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
                            self.errorMsg = data.error || gettext("Failed to generate ticket.");
                        }
                    })
                    .catch(function (err) {
                        self.loading = false;
                        self.error = true;
                        self.errorMsg = gettext("Network error. Please try again.");
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
                // The tab points at /my-events/ for a member holding seats and
                // at /events/ otherwise, so both paths must light it up.
                return (
                    this.currentPath === "/events/" ||
                    this.currentPath.indexOf("/events/") === 0 ||
                    this.currentPath.indexOf("/my-events/") === 0
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
            get isCrushConnectActive() {
                return this.currentPath.indexOf("/crush-connect/") === 0;
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
            get crushConnectActiveClass() {
                return this.isCrushConnectActive ? "bottom-nav-item-active" : "";
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
            // Unread in-app notifications (not connection requests — those
            // badge the bottom nav's Connections tab).
            notificationCount: 0,
            i18nNotifications: "Notifications",
            i18nUnreadNotifications: "Unread notifications: {count}",

            init: function () {
                // Read page title from meta tag set by {% block mobile_page_title %}
                var meta = document.querySelector('meta[name="mobile-page-title"]');
                this.pageTitle = meta ? meta.getAttribute("content") : "";
                this.notificationCount = parseInt(
                    this.$el.dataset.notificationCount || "0",
                    10,
                );
                if (this.$el.dataset.i18nNotifications) {
                    this.i18nNotifications = this.$el.dataset.i18nNotifications;
                }
                if (this.$el.dataset.i18nUnreadNotifications) {
                    this.i18nUnreadNotifications =
                        this.$el.dataset.i18nUnreadNotifications;
                }
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
            // Same "9+" cap as the desktop notificationBell badge.
            get notificationBadgeText() {
                if (this.notificationCount > 9) return "9+";
                return String(this.notificationCount);
            },
            // The badge is aria-hidden; the count is spoken via the link's label.
            get bellAriaLabel() {
                if (!this.hasNotifications) return this.i18nNotifications;
                return this.i18nUnreadNotifications.replace(
                    "{count}",
                    String(this.notificationCount),
                );
            },

            // Bound via x-on:notif-unread-count.window — the desktop
            // notificationBell re-fetches /api/notifications/ on load and on
            // mark-read, and broadcasts each new unread count.
            syncNotificationCount: function (event) {
                var count = parseInt(event.detail, 10);
                if (!isNaN(count) && count >= 0) {
                    this.notificationCount = count;
                }
            },

            goBack: function () {
                if (navigator.vibrate) {
                    navigator.vibrate(10);
                }

                // Hierarchical back for profile edit sub-sections
                var params = new URLSearchParams(window.location.search);
                if (window.location.pathname.indexOf('/profile/edit/') !== -1 && params.has('section')) {
                    // Navigate to the parent edit profile overview
                    window.location.href = window.location.pathname;
                    return;
                }

                // Fallback to history.back(). If history length is 1 (direct load/no history),
                // fall back to a safe location like dashboard.
                if (window.history.length <= 1) {
                    var prefix = "/en";
                    var match = window.location.pathname.match(/^\/([a-z]{2})\//);
                    if (match) {
                        prefix = "/" + match[1];
                    }
                    window.location.href = prefix + '/dashboard/';
                } else {
                    window.history.back();
                }
            },

            toggleDrawer: function () {
                this.$store.drawer.toggle();
            },
        };
    });

    // Mobile navigation side drawer (partials/mobile_drawer.html). State lives
    // in the global `drawer` store so the top bar's hamburger (a separate
    // component) can toggle it; the CSP build cannot evaluate `$store.…`
    // expressions in templates, so these members wrap the store access.
    Alpine.data("mobileDrawer", function () {
        return {
            get drawerOpen() {
                return this.$store.drawer.open;
            },
            closeDrawer: function () {
                this.$store.drawer.close();
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

    // Event detail description collapse/expand (#4-03)
    Alpine.data("eventDescriptionToggle", function () {
        return {
            expanded: false,
            // The template initially clamps long text so it cannot flash
            // open before Alpine loads. The same gate renders the toggle.
            collapsible: false,
            init: function () {
                this.collapsible = this.$el.dataset.collapsible === "true";
            },
            get collapsed() {
                return !this.expanded;
            },
            // String form for :aria-expanded on the toggle button (#WP6-2):
            // mirrors the getter idiom base.html uses for nav aria-expanded
            // bindings under the CSP-safe Alpine build.
            get expandedAria() {
                return this.expanded ? "true" : "false";
            },
            toggle: function () {
                this.expanded = !this.expanded;
                if (this.collapsible) {
                    this.$refs.description.classList.toggle(
                        "line-clamp-4",
                        !this.expanded,
                    );
                }
            },
        };
    });

    // Event detail mobile sticky CTA bar (#4-03). Mirrors whichever single
    // btn-crush-primary registration anchor is already rendered inside
    // #event-cta-panel, so it can never disagree with the in-page CTA — it
    // reads the same DOM instead of re-deriving eligibility. Stays hidden
    // when that panel has no such anchor (blocked / login states).
    //
    // A registered-but-unpaid member sees "Pay with Card" / "Pay with Crush
    // Credit" as <button class="js-sumup-checkout-detail"> elements instead
    // (they trigger a fetch()-based SumUp checkout, not a navigation), so
    // when no anchor is found this also falls back to that button and, on
    // tap, re-dispatches a click to the real in-panel button so its Alpine
    // checkout handler runs exactly as if the member had tapped it directly.
    Alpine.data("eventStickyCta", function () {
        return {
            visible: false,
            ctaHref: "",
            ctaLabel: "",
            priceText: "",
            factsText: "",
            isPayment: false,
            payMethod: "",
            // Bare getter for the anchor branch's x-show (#WP6 fix): the CSP
            // build only evaluates bare property/method names, not
            // expressions like "!isPayment".
            get isLink() {
                return !this.isPayment;
            },
            init: function () {
                this.priceText = this.$el.dataset.priceLabel || "";
                this.factsText = this.$el.dataset.factsText || "";
                var self = this;
                // #WP6-1: while this bar is visible, push #toast-container's
                // bottom offset above the bar's own rendered height so a
                // toast never renders on top of the price/CTA. Reverts to
                // its normal .toast-above-nav offset when the bar hides.
                //
                // `visible` (Alpine's own x-show flag) only ever changes from
                // the IntersectionObserver below, so it is silent about the
                // `md:hidden` breakpoint: rotating or resizing past 768px
                // while `visible` stays true leaves this offset applied with
                // no bar left to justify it, and resizing back can reuse a
                // stale height (Codex review on #1062). Recompute on resize
                // too, not just on the `visible` watcher, so the offset
                // always matches what CSS is actually showing right now.
                function syncToastOffset() {
                    var toast = document.getElementById("toast-container");
                    if (!toast) {
                        return;
                    }
                    // The bar is md:hidden, so at >=768px offsetHeight reads 0
                    // regardless of `visible` (display:none from the media
                    // query) — never write a bottom offset in that case, or
                    // the desktop toast stack (lg:bottom-auto lg:top-4) picks
                    // up an inline `bottom` it never had.
                    var barHeight = self.visible ? self.$el.offsetHeight : 0;
                    if (barHeight > 0) {
                        toast.style.setProperty(
                            "bottom",
                            "calc(var(--bottom-nav-height) + " +
                                barHeight +
                                "px + env(safe-area-inset-bottom, 0px))",
                        );
                    } else {
                        toast.style.removeProperty("bottom");
                    }
                }
                this.$watch("visible", function () {
                    // This watcher and the x-show effect both react to the
                    // same `visible` change, but x-show always applies its
                    // style mutation on a requestAnimationFrame callback
                    // (even with no x-transition), which runs AFTER a plain
                    // $nextTick's microtask — so offsetHeight below would
                    // still read the pre-toggle 0. Wait two frames instead:
                    // one for x-show's own rAF, one more so the resulting
                    // layout has actually been computed before we read it.
                    requestAnimationFrame(function () {
                        requestAnimationFrame(syncToastOffset);
                    });
                });
                // A plain `resize` listener fires on every pixel during a
                // drag; debounce it so a rotation/resize settles once before
                // reading layout, same cost profile as the watcher above.
                var resizeTimer = null;
                window.addEventListener("resize", function () {
                    if (resizeTimer) {
                        clearTimeout(resizeTimer);
                    }
                    resizeTimer = setTimeout(syncToastOffset, 150);
                });
                var panel = document.getElementById("event-cta-panel");
                if (!panel || !("IntersectionObserver" in window)) {
                    return;
                }
                // A language-blocked member sees the registration (or
                // payment) CTA rendered alongside the language-requirement
                // warning even though event_register rejects them — skip
                // the sticky bar entirely rather than advertise an action
                // that cannot succeed (Codex review on #1062).
                if (panel.querySelector("#event-language-blocked")) {
                    return;
                }
                var anchor = panel.querySelector("a.btn-crush-primary");
                // event_register rejects age-restricted sign-ups without a
                // qualifying profile DOB. Keep a real payment button available
                // for an existing unpaid registration, but never mirror a
                // registration anchor that leads straight to that rejection.
                if (anchor && panel.dataset.ageBlocked === "true") {
                    return;
                }
                var target = anchor;
                if (anchor) {
                    this.ctaHref = anchor.getAttribute("href") || "";
                    this.ctaLabel = (anchor.textContent || "").trim();
                } else {
                    // Prefer the "Pay with Card" button: it's the one payment
                    // option always rendered when a balance is due, whereas
                    // "Pay with Crush Credit" only appears with sufficient
                    // credit — so anchoring on "card" keeps target selection
                    // stable across members.
                    var payButton =
                        panel.querySelector(
                            '.js-sumup-checkout-detail[data-payment-method="card"]',
                        ) || panel.querySelector(".js-sumup-checkout-detail");
                    if (!payButton) {
                        return;
                    }
                    this.isPayment = true;
                    this.payMethod =
                        payButton.getAttribute("data-payment-method") || "card";
                    this.ctaLabel = (payButton.textContent || "").trim();
                    target = payButton;
                }
                // Observe the CTA element itself, not the whole panel. Use
                // the full viewport so the bar hides as soon as any part of
                // the real CTA enters view, including near the bottom edge.
                var observer = new IntersectionObserver(
                    function (entries) {
                        var entry = entries[0];
                        self.visible = !!entry && !entry.isIntersecting;
                    },
                    { rootMargin: "0px" },
                );
                observer.observe(target);
            },
            onCtaClick: function (event) {
                if (!this.isPayment) {
                    return;
                }
                event.preventDefault();
                var panel = document.getElementById("event-cta-panel");
                var real =
                    panel &&
                    panel.querySelector(
                        '.js-sumup-checkout-detail[data-payment-method="' +
                            this.payMethod +
                            '"]',
                    );
                if (real) {
                    real.click();
                }
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
    function makeDismissible() {
        return {
            show: true,
            init: function () {
                this.startAutoDismiss();
            },
            startAutoDismiss: function () {
                // Auto-dismiss only when the banner opts in via data-auto-dismiss
                // (success/info confirmations). Errors and warnings omit the
                // attribute so they persist until the user closes them.
                var delay = parseInt(this.$el.dataset.autoDismiss, 10);
                if (delay > 0) {
                    var self = this;
                    setTimeout(function () {
                        self.show = false;
                    }, delay);
                }
            },
            dismiss: function () {
                this.show = false;
            },
        };
    }
    Alpine.data("dismissible", makeDismissible);

    // base.html's Django flash messages: while one is visible it holds the
    // lower-priority prompts (install, push) in Alpine.store("prompts").
    Alpine.data("flashMessage", function () {
        return mixin(makeDismissible(), {
            init: function () {
                var prompts = Alpine.store("prompts");
                prompts.holdMessage();
                this.$watch("show", function (visible) {
                    if (!visible) prompts.releaseMessage();
                });
                this.startAutoDismiss();
            },
        });
    });

    // Inline connection request form (event attendees page).
    // Suggestion chips carry their text in a data-prefill attribute; clicking
    // one inserts it into the note textarea (x-ref="note"). The CSP build
    // passes the click event to the bare method reference @click="prefill".
    Alpine.data("connectionForm", function () {
        return {
            prefill: function (event) {
                var btn = event && event.currentTarget;
                var text = btn ? btn.getAttribute("data-prefill") : "";
                var ta = this.$refs.note;
                if (!ta || !text) return;
                if (ta.value && ta.value.indexOf(text) !== 0) {
                    ta.value = text + " " + ta.value;
                } else {
                    ta.value = text;
                }
                ta.focus();
                ta.setSelectionRange(ta.value.length, ta.value.length);
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
            // Alpine's CSP-friendly build can't evaluate an inline ternary
            // (`isLoginTab ? 'true' : 'false'`) in x-bind:aria-selected —
            // it silently logs a console warning and never sets the
            // attribute. These return the string directly so the binding
            // stays a bare property name.
            get loginAriaSelected() {
                return this.isLoginTab ? "true" : "false";
            },
            get signupAriaSelected() {
                return this.isSignupTab ? "true" : "false";
            },
            // Roving tabindex: only the active tab sits in the sequential
            // tab order, per the ARIA tabs keyboard pattern. Arrow keys
            // move focus between tabs (handled by onTabKeydown below).
            get loginTabIndex() {
                return this.isLoginTab ? "0" : "-1";
            },
            get signupTabIndex() {
                return this.isSignupTab ? "0" : "-1";
            },
            get loginTabClass() {
                return this.activeTab === "login"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-900 bg-white/50 hover:bg-white/80 dark:text-gray-300 dark:bg-transparent dark:hover:bg-white/10";
            },
            get signupTabClass() {
                return this.activeTab === "signup"
                    ? "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md"
                    : "text-gray-900 bg-white/50 hover:bg-white/80 dark:text-gray-300 dark:bg-transparent dark:hover:bg-white/10";
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
            // ARIA tabs keyboard pattern: Left/Right/Home/End move both
            // selection and focus between the two tabs (there are only
            // ever two, so wrapping toggles). Other keys are left alone.
            onTabKeydown: function (event) {
                var key = event.key;
                if (
                    key !== "ArrowLeft" &&
                    key !== "ArrowRight" &&
                    key !== "Home" &&
                    key !== "End"
                ) {
                    return;
                }
                event.preventDefault();
                var next = this.isLoginTab ? "signup" : "login";
                if (key === "Home") next = "login";
                if (key === "End") next = "signup";
                var nextId = next === "login" ? "auth-tab-login" : "auth-tab-signup";
                var nextEl = document.getElementById(nextId);
                if (!nextEl) return;
                // Dispatch a real click rather than setting activeTab
                // directly: the signup tab also carries a plain
                // addEventListener click handler (funnel analytics in
                // auth.html) that a direct state assignment would bypass,
                // so an arrow-key switch to signup would silently miss
                // the signup_page_viewed event.
                nextEl.click();
                nextEl.focus();
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
        // Composes makeTabs (state + setTab/isTabActive) with the page-specific
        // gradient class getters and showUpcoming/showPast aliases the template
        // already references. Keep the template-facing API stable.
        var activeClass =
            "bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md";
        var inactiveClass =
            "text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80";
        return mixin(makeTabs("upcoming", ["upcoming", "past"]), {
            get isUpcoming() {
                return this.isTabActive("upcoming");
            },
            get isPast() {
                return this.isTabActive("past");
            },
            get upcomingTabClass() {
                return this.isUpcoming ? activeClass : inactiveClass;
            },
            get pastTabClass() {
                return this.isPast ? activeClass : inactiveClass;
            },
            showUpcoming() {
                this.setTab("upcoming");
            },
            showPast() {
                this.setTab("past");
            },
            get upcomingAriaSelected() {
                return this.isUpcoming ? "true" : "false";
            },
            get pastAriaSelected() {
                return this.isPast ? "true" : "false";
            },
            get upcomingTabIndex() {
                return this.isUpcoming ? "0" : "-1";
            },
            get pastTabIndex() {
                return this.isPast ? "0" : "-1";
            },
            // WAI-ARIA APG tab keyboard behavior (round-2 finding): arrow
            // keys move both selection and focus between the two tabs;
            // Home/End jump to the first/last. $refs are set in the
            // template (x-ref="tabUpcoming" / "tabPast") on the same
            // x-data root, so they're reachable from here.
            focusTab(name) {
                this.setTab(name);
                var self = this;
                this.$nextTick(function () {
                    var target =
                        name === "upcoming"
                            ? self.$refs.tabUpcoming
                            : self.$refs.tabPast;
                    if (target) target.focus();
                });
            },
            onTabKeydown(event) {
                if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
                    event.preventDefault();
                    this.focusTab(this.isUpcoming ? "past" : "upcoming");
                } else if (event.key === "Home") {
                    event.preventDefault();
                    this.focusTab("upcoming");
                } else if (event.key === "End") {
                    event.preventDefault();
                    this.focusTab("past");
                }
            },
        });
    });

    // Event type filter (event_list.html, finding 1-13). Shared active-type
    // state lives on an Alpine.store because directives here can't call a
    // method with an argument (STYLE.md §7) — each chip/card instead reads
    // its own value from its element's own data-filter-type/data-event-type
    // attribute in init(), and only ever calls no-arg methods/getters.
    Alpine.store("eventTypeFilter", { active: "all" });

    Alpine.data("eventTypeFilterChip", function () {
        return {
            type: "all",
            init() {
                this.type = this.$el.dataset.filterType || "all";
            },
            activate() {
                Alpine.store("eventTypeFilter").active = this.type;
            },
            get isActive() {
                return Alpine.store("eventTypeFilter").active === this.type;
            },
            get chipClass() {
                return this.isActive
                    ? "bg-crush-purple text-white border-crush-purple"
                    : "bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-200 border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700";
            },
            // Round-2 finding: the chip's selected state was only visible as
            // color, which a screen-reader user activating it can't see.
            get ariaPressed() {
                return this.isActive ? "true" : "false";
            },
        };
    });

    Alpine.data("eventTypeFilterCard", function () {
        return {
            type: "",
            init() {
                this.type = this.$el.dataset.eventType || "";
            },
            get visible() {
                var active = Alpine.store("eventTypeFilter").active;
                return active === "all" || active === this.type;
            },
        };
    });

    // Retired /account/settings/ (8-08): its 301 lands on the account
    // drill-down overview, and the browser re-applies the old #anchor. Send
    // the monolith's anchors to the sub-section that now holds them; an
    // unknown anchor stays on the overview.
    var LEGACY_SETTINGS_ANCHORS = {
        "email-notifications": "notifications",
        "whatsapp-notifications": "notifications",
        "push-notifications": "notifications",
    };
    Alpine.data("legacySettingsAnchor", function () {
        return {
            init: function () {
                var anchor = window.location.hash.slice(1);
                if (!anchor) {
                    return;
                }
                if (!Object.prototype.hasOwnProperty.call(LEGACY_SETTINGS_ANCHORS, anchor)) {
                    history.replaceState(null, "", window.location.pathname + window.location.search);
                    return;
                }
                var params = new URLSearchParams(window.location.search);
                params.set("section", "account");
                params.set("sub", LEGACY_SETTINGS_ANCHORS[anchor]);
                window.location.replace(
                    window.location.pathname + "?" + params.toString() + "#" + anchor
                );
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

    // WhatsApp opt-in: saves the moment the switch flips and confirms with a
    // toast. The surrounding <form> still posts as the no-JS fallback.
    Alpine.data("whatsappPreference", function () {
        return {
            // Tail of the write queue. Rapid flips must reach the server in
            // click order, or an earlier "on" can land after a later "off".
            queue: Promise.resolve(),
            save: function (event) {
                var checkbox = event.target;
                var value = checkbox.checked;
                // $el is the switch inside an event handler, so go via the form.
                var form = checkbox.closest("form");
                var csrf = form.querySelector('input[name="csrfmiddlewaretoken"]');
                var fail = function () {
                    // Only roll back if no later flip has superseded this one.
                    if (checkbox.checked === value) {
                        checkbox.checked = !value;
                    }
                    Alpine.store("toasts").add({
                        type: "error",
                        message: form.getAttribute("data-error-message"),
                    });
                };
                var send = function () {
                    return fetch("/api/email/preferences/", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-CSRFToken": csrf ? csrf.value : "",
                        },
                        body: JSON.stringify({ key: "whatsapp_opt_in", value: value }),
                    })
                        .then(function (response) {
                            return response.json();
                        })
                        .then(function (data) {
                            if (!data.success) {
                                fail();
                                return;
                            }
                            Alpine.store("toasts").add({
                                type: "success",
                                message: form.getAttribute("data-saved-message"),
                            });
                        })
                        .catch(fail);
                };
                this.queue = this.queue.then(send);
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
        return mixin(mixin(makePushStatusCheck(), makeBlockedPlatform()), {
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
                    !this.checkTimedOut &&
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
                return !this.isLoading && !this.checkTimedOut && this.isSubscribed && this.hasSubscriptions;
            },
            get showLoading() {
                return this.isLoading;
            },
            // Button text getters for CSP compatibility (replaces ternary expressions)
            get enableButtonText() {
                return this.isEnabling ? gettext("Enabling...") : gettext("Enable Push Notifications");
            },
            get disableButtonText() {
                return this.isDisabling ? gettext("Disabling...") : gettext("Disable");
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
                this._detectBlockedPlatform();

                // Detect current device endpoint for "This device" badge
                this._detectCurrentEndpoint();

                this._checkStatus();

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
                        self.errorMessage = gettext("An error occurred. Please try again.");
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
                        self.errorMessage = gettext("An error occurred. Please try again.");
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
                        self.errorMessage = gettext("An error occurred. Please try again.");
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

            // Wait for CrushPush, then ask it whether this device is subscribed
            _checkStatus: function () {
                var self = this;
                this._runStatusCheck(function (finish) {
                    self._waitForCrushPush(function () {
                        if (!self.isSupported || !window.CrushPush.isSubscribed) {
                            return finish();
                        }
                        window.CrushPush.isSubscribed()
                            .then(function (subscribed) {
                                self.isSubscribed = subscribed;
                                finish();
                            })
                            .catch(finish);
                    });
                });
            },

            // Wait for CrushPush to be available (handles script load timing)
            _waitForCrushPush: function (callback) {
                var maxAttempts = 20; // 2 seconds max
                var attempts = 0;

                function check() {
                    attempts++;
                    if (window.CrushPush) {
                        callback();
                    } else if (attempts < maxAttempts) {
                        setTimeout(check, 100);
                    }
                    // Otherwise CrushPush never loaded (script blocked or
                    // failed): leave the check pending so the status-check
                    // timeout offers Retry instead of a dead Enable button.
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
                                alert(gettext("Subscription refreshed successfully"));
                                window.location.reload();
                            } else {
                                alert(gettext("Failed to refresh subscription"));
                            }
                        })
                        .catch(function (error) {
                            console.error("Error refreshing:", error);
                            alert(gettext("Error refreshing subscription"));
                        });
                } else {
                    alert(
                        gettext("Push notification system not loaded. Please refresh the page and try again."),
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
        });
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
        return mixin(mixin(makePushStatusCheck(), makeBlockedPlatform()), {
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
                    !this.checkTimedOut &&
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
                return !this.isLoading && !this.checkTimedOut && this.isSubscribed && this.hasSubscriptions;
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
                return this.isEnabling ? gettext("Enabling...") : gettext("Enable Notifications");
            },
            get testButtonText() {
                return this.isSendingTest ? gettext("Sending...") : gettext("Send Test");
            },
            get disableButtonText() {
                return this.isDisabling ? gettext("Disabling...") : gettext("Disable Notifications");
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
                this._detectBlockedPlatform();

                // Detect current device endpoint for "This device" badge
                this._detectCurrentEndpoint();

                this._checkStatus();

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
                        self.errorMessage = gettext("An error occurred. Please try again.");
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

            _checkStatus: function () {
                var self = this;
                this._runStatusCheck(function (finish) {
                    self._waitForServiceWorker(function () {
                        if (self.isSupported && self.subscriptions.length > 0) {
                            self.isSubscribed = true;
                        }
                        finish();
                    });
                });
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
        });
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
    // DEPRECATED — see crush_lu/STYLE.md §7. New photo-upload UI should use
    // the photoPicker component (HTMX, slot-based, no 3-photo hardcoding).
    // photoUpload is preserved for the onboarding wizard while that flow
    // remains out of scope for the visual-system refactor.
    var _photoUploadDeprecationLogged = false;
    Alpine.data("photoUpload", function () {
        if (!_photoUploadDeprecationLogged && typeof console !== "undefined") {
            console.warn(
                "[crush_lu] Alpine.data('photoUpload') is deprecated; use photoPicker for new photo-upload UI.",
            );
            _photoUploadDeprecationLogged = true;
        }
        // Serialize writes per slot: a server-side upload must finish before
        // a later Remove deletes it, and a newer selection follows that delete.
        var slotOperations = [Promise.resolve(), Promise.resolve(), Promise.resolve()];
        return {
            photos: [
                { id: 1, hasImage: false, preview: "", uploadGeneration: 0 },
                { id: 2, hasImage: false, preview: "", uploadGeneration: 0 },
                { id: 3, hasImage: false, preview: "", uploadGeneration: 0 },
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
                // Continue locks the photo step while its writes drain.
                if (_photoStepAdvancing) {
                    event.target.value = "";
                    return;
                }
                var file = event.target.files[0];
                if (file) {
                    var self = this;
                    var photoNumber = index + 1; // Convert 0-indexed to 1-indexed
                    var generation = ++self.photos[index].uploadGeneration;
                    var previousOperation = slotOperations[index];

                    // Show preview immediately unless a later Remove or
                    // selection has already invalidated this FileReader.
                    var reader = new FileReader();
                    reader.onload = function (e) {
                        if (self.photos[index].uploadGeneration !== generation) return;
                        self.photos[index].preview = e.target.result;
                        self.photos[index].hasImage = true;
                    };
                    reader.readAsDataURL(file);

                    // Downscale/re-encode client-side (max ~2048px long edge,
                    // JPEG q=0.85) before the auto-save upload — server
                    // validation is unchanged and still applies to whatever
                    // arrives. Tracked as a pending upload (see
                    // trackPendingPhotoUpload above) so the wizard's
                    // Continue button can wait for it to settle instead of
                    // advancing past a photo that was never saved.
                    var uploadPromise = Promise.all([
                        previousOperation,
                        resizeImageForUpload(file, 2048, 0.85),
                    ]).then(function (ready) {
                        var uploadFile = ready[1];
                        // The slot was removed (or replaced by a newer
                        // selection) while this resize was running — do not
                        // upload a file the member already deleted.
                        if (self.photos[index].uploadGeneration !== generation) {
                            return;
                        }

                        var formData = new FormData();
                        formData.append("photo", uploadFile, uploadFile.name || file.name);
                        formData.append("photo_number", photoNumber);

                        // Get CSRF token
                        var csrfToken = document.querySelector(
                            "[name=csrfmiddlewaretoken]",
                        );
                        if (csrfToken) {
                            formData.append("csrfmiddlewaretoken", csrfToken.value);
                        }

                        return fetch("/api/profile/draft/upload-photo/", {
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
                                // Re-check after the round trip too: a
                                // Remove tapped mid-upload must still win
                                // over this response.
                                if (self.photos[index].uploadGeneration !== generation) {
                                    return;
                                }
                                if (result.success) {
                                    self.photos[index].uploadedUrl = result.photo_url;
                                    // Replace the native file input's
                                    // FileList with the resized copy so a
                                    // final non-JS form submit re-sends the
                                    // already-uploaded resized file instead
                                    // of the original (finding 3-16).
                                    var input = document.getElementById(
                                        "photo" + photoNumber,
                                    );
                                    if (input && typeof DataTransfer !== "undefined") {
                                        try {
                                            var dt = new DataTransfer();
                                            dt.items.add(uploadFile);
                                            input.files = dt.files;
                                        } catch (e) {
                                            // Browser lacks a writable
                                            // DataTransfer/File constructor
                                            // pairing — leave the input as
                                            // the browser set it; the
                                            // resized copy is still saved
                                            // server-side via the draft.
                                        }
                                    }
                                } else {
                                    console.error(
                                        "[PHOTO UPLOAD] ❌ Upload failed:",
                                        result.error,
                                    );
                                    notifyError(
                                        gettext("Photo upload failed: ") + result.error,
                                    );
                                }
                            })
                            .catch(function (err) {
                                console.error("[PHOTO UPLOAD] ❌ Network error:", err);
                                notifyError(
                                    gettext("Photo upload failed. Please try again."),
                                );
                            });
                    });
                    slotOperations[index] = uploadPromise.then(function () {}, function () {});
                    trackPendingPhotoUpload(uploadPromise);
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
                if (_photoStepAdvancing) return;
                var self = this;
                var photoNumber = index + 1;
                var removalGeneration = ++self.photos[index].uploadGeneration;
                var previousOperation = slotOperations[index];

                var clearLocal = function () {
                    self.photos[index].preview = "";
                    self.photos[index].hasImage = false;
                    self.photos[index].uploadedUrl = "";
                    var input = document.getElementById("photo" + photoNumber);
                    if (input) input.value = "";
                };

                // An upload POST may already be running. Its generation
                // check cannot undo a server write, so issue the delete only
                // after that POST settles. Later selections queue behind it.
                var deletePromise = previousOperation.then(function () {
                    var csrfToken = document.querySelector("[name=csrfmiddlewaretoken]");
                    var formData = new FormData();
                    formData.append("photo_number", photoNumber);
                    return fetch("/api/profile/draft/delete-photo/", {
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
                                // A newer file may already have been selected.
                                if (self.photos[index].uploadGeneration === removalGeneration) {
                                    clearLocal();
                                }
                            } else {
                                console.error("[PHOTO REMOVE] ❌ Delete failed:", result.error);
                                notifyError(gettext("Could not remove the photo: ") + result.error);
                            }
                        })
                        .catch(function (err) {
                            console.error("[PHOTO REMOVE] ❌ Network error:", err);
                            notifyError(gettext("Could not remove the photo. Please try again."));
                        });
                });
                slotOperations[index] = deletePromise.then(function () {}, function () {});
                trackPendingPhotoUpload(deletePromise);
            },
        };
    });

    // Profile creation wizard component
    // Reads initial values from data attributes
    Alpine.data("profileWizard", function () {
        return {
            currentStep: 1,
            totalSteps: 4,
            isSubmitting: false,
            phoneVerified: false,
            showErrors: false,
            errors: {},
            isEditing: false,
            step1Valid: false,
            step2Valid: true,

            // Step 1 required fields tracking
            gender: "",
            location: "",
            locationName: "",

            // Step 2 fields tracking

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
                // Location is deliberately absent: optional since fast-track
                // event verification (must match save-step1 API + model).
                return (
                    this.phoneVerified &&
                    this.gender !== "" &&
                    !this.isSaving
                );
            },
            get cannotContinueStep1() {
                return (
                    !this.phoneVerified ||
                    this.gender === "" ||
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

                // Translated auto-save pill copy (same pattern as the step
                // labels above).
                var savedJustNow = el.getAttribute("data-saved-just-now");
                if (savedJustNow) this.savedJustNowLabel = savedJustNow;
                var savedAgo = el.getAttribute("data-saved-ago");
                if (savedAgo) this.savedAgoLabel = savedAgo;
                var savedEarlier = el.getAttribute("data-saved-earlier");
                if (savedEarlier) this.savedEarlierLabel = savedEarlier;

                // Map DB completion_status values → wizard sub-step numbers.
                // Wizard has 4 sub-steps: 1 Basic Info · 2 About You · 3 Photos
                // · 4 Review. step4 is a legacy DB value (old Preferences step)
                // — both step3 and step4 land users on Review.
                var stepMap = {
                    not_started: 1,
                    step1: 2,
                    step2: 3,
                    step3: 4,
                    step4: 4,
                    submitted: 4,
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

                // Browser/Android back gesture support (finding 3-04): stamp
                // the landing step as a history entry (replace, not push —
                // this is the page load, not a navigation) and honour a
                // #step-N deep link within range. popstate then walks the
                // wizard back/forward without re-pushing (would loop).
                try {
                    var hashMatch = /^#step-([1-4])$/.exec(window.location.hash);
                    if (hashMatch) {
                        self.currentStep = parseInt(hashMatch[1], 10);
                    }
                    // Codex review finding: seed history for resumed wizard
                    // steps. A returning user can land directly on step 3 or
                    // 4 (see stepMap above); a bare replaceState only ever
                    // records that landing step, so the very first Back
                    // press has no earlier wizard entry to land on and
                    // leaves the page instead of walking to step 2/3. Push
                    // one entry per preceding step first (this is still the
                    // page load, not a user navigation — pushState here just
                    // backfills the history stack the wizard would have
                    // built had the user clicked through from step 1).
                    // The landing entry itself becomes step 1 (replace), so
                    // one Back past step 1 leaves the wizard. A reload lands
                    // on an entry that already carries wizardStep: re-seeding
                    // it would stack a second set of synthetic entries.
                    var alreadySeeded =
                        history.state && history.state.wizardStep;
                    if (self.currentStep > 1 && !alreadySeeded) {
                        history.replaceState({ wizardStep: 1 }, "", "#step-1");
                        for (var seedStep = 2; seedStep <= self.currentStep; seedStep++) {
                            history.pushState(
                                { wizardStep: seedStep },
                                "",
                                "#step-" + seedStep,
                            );
                        }
                    } else {
                        history.replaceState(
                            { wizardStep: self.currentStep },
                            "",
                            "#step-" + self.currentStep,
                        );
                    }
                } catch (e) {
                    // history API unavailable — steps still work without it.
                }
                window.addEventListener("popstate", function (e) {
                    var step =
                        e.state && e.state.wizardStep
                            ? e.state.wizardStep
                            : self.currentStep;
                    // Review can be reached directly from an edited Event
                    // Identity step via browser Back. Persist those fields
                    // before showing a summary that looks ready to submit.
                    if (self.currentStep === 2 && step === self.totalSteps) {
                        if (self.isSaving) {
                            history.replaceState({ wizardStep: 2 }, "", "#step-2");
                            return;
                        }
                        self.saveStep2().then(function (result) {
                            // A second navigation during the request wins.
                            if (
                                self.currentStep !== 2 ||
                                !history.state ||
                                history.state.wizardStep !== step
                            ) {
                                return;
                            }
                            if (result.success) {
                                self._setStep(step, false);
                            } else {
                                // Keep the editable step and its error visible.
                                history.replaceState({ wizardStep: 2 }, "", "#step-2");
                            }
                        });
                        return;
                    }
                    // A browser Back from edited Photos also reaches Review
                    // directly. Use the same pending-write gate as Continue.
                    if (self.currentStep === 3 && step === self.totalSteps) {
                        if (self.isSaving) {
                            history.replaceState({ wizardStep: 3 }, "", "#step-3");
                            return;
                        }
                        self._completePhotoStep(false);
                        return;
                    }
                    self._setStep(step, false);
                });
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

            // Single choke point for every step transition (finding 3-04):
            // nextStep/prevStep/goToStep/saveAndNextStep1-3 and the popstate
            // handler all route through here so the Android/WebView back
            // gesture always lands on the previous wizard section instead of
            // exiting the page. pushHistory=false is for popstate itself
            // (already a history entry) and the initial render.
            _setStep: function (step, pushHistory) {
                if (step < 1 || step > this.totalSteps) return;
                this.currentStep = step;
                // 3-05 follow-up: landing on Review via the back/forward
                // gesture or a direct goToStep() must refresh the summary,
                // the same way saveAndNextStep3 already does on the forward
                // path — otherwise an edited field can show a stale value.
                if (step === this.totalSteps) {
                    this.updateReview();
                }
                window.scrollTo({ top: 0, behavior: "smooth" });
                try {
                    if (pushHistory) {
                        history.pushState(
                            { wizardStep: step },
                            "",
                            "#step-" + step,
                        );
                    } else {
                        history.replaceState(
                            { wizardStep: step },
                            "",
                            "#step-" + step,
                        );
                    }
                } catch (e) {
                    // history API unavailable (e.g. sandboxed preview) — the
                    // step change above still works, just without deep-linking.
                }
                this.$nextTick(function () {
                    var heading = document.querySelector(
                        '[data-wizard-step="' + step + '"] h3',
                    );
                    if (heading) {
                        heading.setAttribute("tabindex", "-1");
                        heading.focus();
                    }
                });
            },

            nextStep: function () {
                if (this.currentStep < this.totalSteps) {
                    this._setStep(this.currentStep + 1, true);
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
                    this._setStep(this.currentStep - 1, true);
                }
            },

            goToStep: function (step) {
                this._setStep(step, true);
            },

            // CSP-compatible Review-step "Edit" links: reads the target step
            // from the clicked element's own data attribute (same idiom as
            // traitSelector.handleClick) instead of an inline goToStep(n)
            // call, which the CSP build disallows.
            editSection: function () {
                var step = parseInt(this.$el.getAttribute("data-goto-step"), 10);
                if (step) {
                    this.goToStep(step);
                }
            },

            isStepCompleted: function (step) {
                return step < this.currentStep;
            },

            isCurrentStep: function (step) {
                return step === this.currentStep;
            },

            // Refresh the Review-step summary from the live form state.
            // Fallback copy comes from each element's data-empty attribute
            // (translated server-side); elements keep their server-rendered
            // initial content until a fresher client-side value exists.
            updateReview: function () {
                var emptyLabel = function (el) {
                    return el.getAttribute("data-empty") || "";
                };

                var phone = document.querySelector("[name=phone_number]");
                var dobInput = document.querySelector("[name=date_of_birth]");
                var genderEl = document.querySelector("[name=gender]:checked");

                var reviewPhone = this.$refs.reviewPhone;
                var reviewDob = this.$refs.reviewDob;
                var reviewGender = this.$refs.reviewGender;
                var reviewLocation = this.$refs.reviewLocation;
                var reviewInterests = this.$refs.reviewInterests;
                var reviewLanguages = this.$refs.reviewLanguages;
                var reviewPhotos = this.$refs.reviewPhotos;

                if (reviewPhone) {
                    reviewPhone.textContent =
                        (phone && phone.value) || emptyLabel(reviewPhone);
                }
                if (reviewDob) {
                    // Native <input type="date"> value is always YYYY-MM-DD.
                    // Format it for display client-side (no hard-coded copy)
                    // when it's set this session; otherwise keep the
                    // server-rendered value (resume-on-Review case).
                    if (dobInput && dobInput.value) {
                        var dobDate = new Date(dobInput.value + "T00:00:00");
                        reviewDob.textContent = dobDate.toLocaleDateString(
                            document.documentElement.lang || "en",
                            { day: "numeric", month: "short", year: "numeric" },
                        );
                    } else {
                        reviewDob.textContent = emptyLabel(reviewDob);
                    }
                }
                if (reviewGender) {
                    var genderText = "";
                    if (genderEl) {
                        var label = genderEl.nextElementSibling;
                        var genderLabel = label && label.querySelector(".gender-label");
                        genderText = genderLabel ? genderLabel.textContent.trim() : "";
                    }
                    reviewGender.textContent = genderText || emptyLabel(reviewGender);
                }
                if (reviewLocation) {
                    reviewLocation.textContent =
                        this.locationName || emptyLabel(reviewLocation);
                }

                if (reviewInterests) {
                    // Event Identity summary chips: the event vibe (if any) plus
                    // the selected interest labels. Replaces the old bio excerpt.
                    var chips = [];
                    var vibeEl = document.querySelector('[name="event_vibe"]:checked');
                    if (vibeEl && vibeEl.value) {
                        var vibeSpan = vibeEl.nextElementSibling;
                        if (vibeSpan) chips.push(vibeSpan.textContent.trim());
                    }
                    document
                        .querySelectorAll('[name="interests_new"]:checked')
                        .forEach(function (box) {
                            var span = box.nextElementSibling;
                            if (span) chips.push(span.textContent.trim());
                        });

                    reviewInterests.textContent = "";
                    if (chips.length === 0) {
                        var em = document.createElement("span");
                        em.className =
                            "text-muted-fg italic text-sm";
                        em.textContent = emptyLabel(reviewInterests);
                        reviewInterests.appendChild(em);
                    } else {
                        chips.forEach(function (text) {
                            var chip = document.createElement("span");
                            chip.className =
                                "inline-block px-2.5 py-1 rounded-full text-xs font-medium bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300";
                            chip.textContent = text;
                            reviewInterests.appendChild(chip);
                        });
                    }
                }

                if (reviewLanguages) {
                    var checked = document.querySelectorAll(
                        '[name="event_languages"]:checked',
                    );
                    var labels = [];
                    for (var i = 0; i < checked.length; i++) {
                        var span = checked[i].nextElementSibling;
                        if (span) {
                            labels.push(span.textContent.trim().replace(/\s+/g, " "));
                        }
                    }
                    reviewLanguages.textContent = labels.length
                        ? labels.join(" · ")
                        : emptyLabel(reviewLanguages);
                }

                if (reviewPhotos) {
                    // Mirror the step-3 photo previews (server photos + any
                    // picked this session; x-show hides empty slots).
                    var srcs = [];
                    var photoImgs = document.querySelectorAll(
                        '[data-wizard-step="3"] img',
                    );
                    for (var j = 0; j < photoImgs.length; j++) {
                        var src = photoImgs[j].getAttribute("src");
                        var wrapper = photoImgs[j].parentElement;
                        var hidden = wrapper && wrapper.style.display === "none";
                        if (src && !hidden) {
                            srcs.push(src);
                        }
                    }
                    reviewPhotos.innerHTML = "";
                    if (srcs.length) {
                        for (var k = 0; k < srcs.length; k++) {
                            var img = document.createElement("img");
                            img.src = srcs[k];
                            img.alt = "";
                            img.className = "h-14 w-14 rounded-lg object-cover";
                            reviewPhotos.appendChild(img);
                        }
                    } else {
                        // Amber nudge, not a neutral "No photos yet" row — a
                        // profile with no face is a real conversion/safety
                        // cost at an events-first product (finding 3-05).
                        var callout = document.createElement("div");
                        callout.className =
                            "flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 dark:border-amber-700/50 dark:bg-amber-900/20";
                        var nudge = document.createElement("p");
                        nudge.className = "text-sm text-amber-800 dark:text-amber-200";
                        nudge.textContent =
                            reviewPhotos.getAttribute("data-empty-nudge") ||
                            emptyLabel(reviewPhotos);
                        callout.appendChild(nudge);
                        reviewPhotos.appendChild(callout);
                    }
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

            // Collect Step 2 form data (Event Identity, 2026 redesign).
            collectStep2Data: function () {
                var interestsNew = [];
                document
                    .querySelectorAll('[name="interests_new"]:checked')
                    .forEach(function (b) {
                        interestsNew.push(b.value);
                    });

                var askMeAbout = [];
                document
                    .querySelectorAll('[name="ask_me_about"]:checked')
                    .forEach(function (b) {
                        askMeAbout.push(b.value);
                    });

                var vibeEl = document.querySelector('[name="event_vibe"]:checked');
                var qualitiesEl = document.querySelector('[name="qualities_ids"]');
                var defectsEl = document.querySelector('[name="defects_ids"]');

                return {
                    interests_new: interestsNew,
                    ask_me_about: askMeAbout,
                    event_vibe: vibeEl ? vibeEl.value : "",
                    qualities_ids: qualitiesEl ? qualitiesEl.value : "",
                    defects_ids: defectsEl ? defectsEl.value : "",
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
                                gettext("Failed to save. Please try again.");
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
                        self.saveError = gettext("Network error. Please check your connection.");
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
                                gettext("Failed to save. Please try again.");
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
                        self.saveError = gettext("Network error. Please check your connection.");
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
                                gettext("Failed to save. Please try again.");
                            return { success: false, error: self.saveError };
                        }
                    })
                    .catch(function (err) {
                        self.isSaving = false;
                        self.saveError = gettext("Network error. Please check your connection.");
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
                        self._setStep(2, true);
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
                        self._setStep(3, true);
                    }
                });
            },

            // Save Step 3 (Photos) and advance to the Review step. Waits
            // for any in-flight photo resize/upload first (finding 3-14):
            // photoUpload lives in its own nested x-data scope, so without
            // this the wizard had no way to know an upload was still in
            // flight and would advance to Review immediately, leaving a
            // preview of a photo that may never actually get saved.
            saveAndNextStep3: function () {
                this._completePhotoStep(true);
            },
            _completePhotoStep: function (pushHistory) {
                var self = this;
                if (self.isSaving) return;
                self.isSaving = true;
                _photoStepAdvancing = true;

                waitForPendingPhotoUploads().then(function () {
                    return self.saveStep3();
                }).then(function (result) {
                    _photoStepAdvancing = false;
                    // The member may navigate elsewhere while the request
                    // settles; an old completion must not pull them back.
                    if (self.currentStep !== 3) return;
                    if (!pushHistory && (!history.state || history.state.wizardStep !== 4)) return;
                    if (result.success) {
                        self.saveError = "";
                        self._setStep(4, pushHistory);
                    } else if (!pushHistory) {
                        history.replaceState({ wizardStep: 3 }, "", "#step-3");
                    }
                }).catch(function () {
                    _photoStepAdvancing = false;
                    self.isSaving = false;
                    self.saveError = gettext("Failed to save. Please try again.");
                    if (!pushHistory && self.currentStep === 3) {
                        history.replaceState({ wizardStep: 3 }, "", "#step-3");
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
            draftRestored: false,

            // Auto-save pill copy — overridden from data attributes in init()
            // so Django's {% trans %} can translate it (JS stays copy-free).
            savedJustNowLabel: "Saved just now",
            savedAgoLabel: "Saved %s ago",
            savedEarlierLabel: "Saved earlier",

            // CSP-safe getters for auto-save UI
            get showDraftRestored() {
                return this.draftRestored;
            },
            dismissDraftNotice: function () {
                this.draftRestored = false;
            },
            get showAutoSaving() {
                return this.isAutoSaving;
            },
            // The pill flashes for a few seconds after each save instead of
            // sitting in the corner forever (on mobile it overlaps the
            // sticky Continue button). The restored-draft banner answers
            // "did my work survive?" on page load.
            savedPillVisible: false,
            _savedPillTimer: null,
            get showLastSaved() {
                return this.savedPillVisible && this.lastSavedAt !== null;
            },
            _flashSavedPill: function () {
                var self = this;
                this.savedPillVisible = true;
                clearTimeout(this._savedPillTimer);
                this._savedPillTimer = setTimeout(function () {
                    self.savedPillVisible = false;
                }, 4000);
            },
            get lastSavedMessage() {
                if (!this.lastSavedAt) return "";
                var now = new Date();
                var saved = new Date(this.lastSavedAt);
                var diffMs = now - saved;
                var diffSec = Math.floor(diffMs / 1000);

                if (diffSec < 10) return this.savedJustNowLabel;
                if (diffSec < 60) {
                    return this.savedAgoLabel.replace("%s", diffSec + "s");
                }
                var diffMin = Math.floor(diffSec / 60);
                if (diffMin < 60) {
                    return this.savedAgoLabel.replace("%s", diffMin + "m");
                }
                return this.savedEarlierLabel;
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

                            // Show the "we restored your answers" notice only
                            // when actual UNSAVED draft input exists — the
                            // merged blob is non-empty for any returning user.
                            var rawDraft = result.data.draft || {};
                            for (var stepKey in rawDraft) {
                                var stepDraft = rawDraft[stepKey];
                                if (
                                    stepDraft &&
                                    typeof stepDraft === "object" &&
                                    Object.keys(stepDraft).length > 0
                                ) {
                                    self.draftRestored = true;
                                    break;
                                }
                            }

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

                    // NEVER restore a persisted CSRF token (old drafts may
                    // contain one): it would clobber the fresh token and
                    // 403 every save after a re-login.
                    if (key === "csrfmiddlewaretoken") {
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
                            // Tell the cantonMap so the SVG highlight and
                            // selection label reflect the restored region
                            // instead of the placeholder.
                            mapContainer.dispatchEvent(
                                new CustomEvent("location-restore", {
                                    detail: { location: this.draftData.location },
                                }),
                            );
                        } else {
                            this.locationName = this.draftData.location;
                        }
                    }
                }

                // Re-hydrate the Event Identity sub-components: the generic loop
                // above checked their boxes/updated hidden inputs, but those
                // components ran init() before the draft arrived, so their
                // counters, caps and chip visibility are stale until told.
                var eventIdentityEl = form.querySelector('[x-data="eventIdentity"]');
                if (eventIdentityEl) {
                    eventIdentityEl.dispatchEvent(new CustomEvent("event-identity-restore"));
                }
                ["qualities_ids", "defects_ids"].forEach(function (fieldName) {
                    var hidden = form.querySelector('[name="' + fieldName + '"]');
                    var raw = self.draftData[fieldName];
                    if (!hidden || raw === undefined || raw === null) {
                        return;
                    }
                    var container = hidden.closest('[x-data]');
                    if (!container) {
                        return;
                    }
                    var ids = String(raw)
                        .split(",")
                        .map(function (x) {
                            return parseInt(x, 10);
                        })
                        .filter(function (n) {
                            return !isNaN(n);
                        });
                    container.dispatchEvent(
                        new CustomEvent("trait-restore", { detail: { ids: ids } }),
                    );
                });

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

                // Trait chips are <button>s, so they fire no input/change event —
                // traitSelector dispatches "profile-autosave:trigger" instead
                // (the edit card's profileSectionAutosave listens for it, but the
                // wizard has no such component). Without this, a member who picks
                // qualities/defects and leaves before Continue loses them.
                form.addEventListener("profile-autosave:trigger", function () {
                    self.saveDraft();
                });
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
                            self._flashSavedPill();
                        } else {
                            console.error("[DRAFT SAVE] Save failed:", result.error);
                        }
                    })
                    .catch(function (err) {
                        self.isAutoSaving = false;
                        console.error("[DRAFT SAVE] ❌ Network error:", err);
                    });
            },

            // Gather current step form data.
            // Scoped to the ACTIVE step's container ([data-wizard-step="N"]):
            // a full-form snapshot stored under one step key would survive
            // that other step's official save (which only clears its own key)
            // and silently override newer profile data on the next restore.
            gatherCurrentStepData: function () {
                var form = this.$el.querySelector("form");
                if (!form) {
                    console.warn("[GATHER] No form found");
                    return {};
                }

                var container =
                    form.querySelector(
                        '[data-wizard-step="' + this.currentStep + '"]',
                    ) || form;

                var data = {};
                var checkboxGroups = {}; // Track checkbox arrays

                var fields = container.querySelectorAll("input, textarea, select");
                for (var i = 0; i < fields.length; i++) {
                    var field = fields[i];
                    var key = field.name;
                    if (!key) continue;

                    // Skip file inputs (photos) - they're uploaded separately
                    if (field.type === "file") continue;

                    // NEVER persist the CSRF token: it is session-scoped, and
                    // restoring a stale one after re-login would overwrite the
                    // fresh token and 403 every subsequent save.
                    if (key === "csrfmiddlewaretoken") continue;

                    if (field.type === "checkbox") {
                        // Collected per-group below (FormData-style iteration
                        // would overwrite instead of building an array)
                        if (!checkboxGroups[key]) {
                            checkboxGroups[key] = [];
                        }
                        checkboxGroups[key].push(field);
                        continue;
                    }

                    if (field.type === "radio") {
                        if (field.checked) {
                            data[key] = field.value;
                        }
                        continue;
                    }

                    // Only add non-empty values, so a partial save doesn't
                    // clobber another field. EXCEPT the trait CSV hidden inputs:
                    // draft saves are merged server-side (draft_data[step].update),
                    // so omitting an emptied qualities_ids/defects_ids would leave
                    // its stale value behind — deselecting the last chip would then
                    // resurrect the trait on resume. Their empty state must be sent
                    // so the merge overwrites it.
                    if (
                        field.value !== "" ||
                        key === "qualities_ids" ||
                        key === "defects_ids"
                    ) {
                        data[key] = field.value;
                    }
                }

                // Single checkboxes (privacy settings) are stored as boolean;
                // multiple checkboxes with same name (event_languages) as array
                for (var name in checkboxGroups) {
                    var group = checkboxGroups[name];

                    if (group.length === 1) {
                        data[name] = group[0].checked;
                    } else {
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

                // Restore from a draft value written into the hidden input
                // AFTER init (profileWizard.populateFieldsFromDraft) — the
                // map would otherwise show the placeholder while the hidden
                // field already holds a region.
                this.$el.addEventListener("location-restore", function (e) {
                    var regionId = e.detail && e.detail.location;
                    if (!regionId) return;
                    var path = document.getElementById(regionId);
                    var name =
                        (path && path.getAttribute("data-region-name")) || regionId;
                    self.selectRegion(regionId, name);
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
                    if (self.regions.length === 0) {
                        return;
                    }
                    if (self.focusedIndex < 0) {
                        var selectedIndex = self._getSelectedIndex();
                        self.focusedIndex = selectedIndex >= 0 ? selectedIndex : 0;
                    }
                    // blur clears the active descendant but keeps the index, so
                    // re-apply on every focus to restore it when the user tabs back.
                    self._applyFocus();
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
                    // DOM focus stays on the listbox <svg>, so point it at the active
                    // option; otherwise screen readers never announce the new region.
                    var svg = this.$el.querySelector("svg");
                    if (svg) {
                        svg.setAttribute(
                            "aria-activedescendant",
                            region.element.id || region.id
                        );
                    }
                    this.hoverRegion(region.id, region.name);
                }
            },

            _clearFocus: function () {
                var svg = this.$el.querySelector("svg");
                if (!svg) return;
                svg.removeAttribute("aria-activedescendant");
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

                // Restore from a draft value written into the hidden input
                // AFTER init (profileWizard.populateFieldsFromDraft) —
                // without this the picker UI stays on the empty age-range
                // step while the hidden field already holds a date.
                this.$el.addEventListener("dob-restore", function (e) {
                    var value = (e.detail && e.detail.value) || "";
                    if (value) {
                        self._parseInitialDate(value);
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

            _parseInitialDate: function (explicitValue) {
                // Prefer an explicit value (draft restore), then the
                // data-initial-dob attribute on the component root (set by
                // the server template) so we don't depend on when Alpine
                // decides to scan the inner hidden input during $nextTick.
                // Fall back to the hidden input for callers that run after
                // the user picks a date.
                var initialValue =
                    explicitValue || this.$el.getAttribute("data-initial-dob") || "";
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
                        "w-full h-full min-h-[3.5rem] flex items-center justify-center gap-2 px-3 py-3 rounded-xl text-sm font-medium border-2 transition-all duration-200";
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
                            "px-3 py-2 rounded-lg text-sm font-medium border-2 transition-all duration-150";
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
                        "px-3 py-2.5 rounded-lg text-sm font-medium border-2 transition-all duration-150";
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
                            "w-full aspect-square flex items-center justify-center rounded-lg text-sm font-medium border-2 transition-all duration-150";
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
            // The resting (unverified, no failed attempt yet) state reads as a
            // neutral status, not an error — a red "Verification Required"
            // pill before the member has typed anything reads as "you already
            // did something wrong". Only a failed verify attempt earns red.
            get notVerifiedNeutral() {
                return !this.verified && this.failureCount === 0;
            },
            get notVerifiedFailed() {
                return !this.verified && this.failureCount > 0;
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

                // data-keep-iti="true" preserves the flag/dial-code display after
                // verification. Use this on pages without a <form> (e.g. onboarding)
                // where destroying the instance would leave a plain text field.
                this.keepIti = this.$el.getAttribute("data-keep-iti") === "true";

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

                // WhatsApp → SMS fallback: the modal dispatches this when the
                // number isn't on WhatsApp so we run the normal SMS flow (which
                // reuses the open modal, reCAPTCHA, and Firebase send).
                window.addEventListener("start-sms-verification", function (e) {
                    self._doStartVerification(e.detail);
                });

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
                        phoneInput.readOnly = true;
                        if (self.keepIti && self.iti) {
                            // No form submission on this page — keep the iti instance so
                            // the country flag and dial code remain visible after verification.
                            self.iti.setNumber(e.detail);
                        } else {
                            // Set the raw input value to full E.164 number as a safety net
                            phoneInput.value = e.detail;
                            // Destroy intl-tel-input instance - phone is now verified and locked,
                            // we don't want the library stripping the dial code on form submit
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
                        Accept: "application/json",
                        "X-CSRFToken": csrfToken ? csrfToken.value : "",
                    },
                    credentials: "same-origin",
                    body: JSON.stringify({ phone_number: phoneNumber }),
                })
                    .then(function (response) {
                        if (response.status === 429) {
                            return response
                                .json()
                                .catch(function () {
                                    return null;
                                })
                                .then(function (data) {
                                    self.errorMessage =
                                        (data && data.error) ||
                                        gettext(
                                            "Too many attempts. Please wait a few minutes before trying again.",
                                        );
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

            // WhatsApp channel: hand the number to the OTP modal, which owns the
            // send/verify against the api/phone/whatsapp/ endpoints. Unlike SMS
            // there is no Firebase pre-flight or reCAPTCHA — the backend send
            // already rejects in-use numbers (409), so no check-available call.
            startWhatsAppVerification: function () {
                if (this.iti && !this.iti.isValidNumber()) {
                    this.errorMessage = gettext("Please enter a valid phone number");
                    return;
                }
                var phoneNumber = this.iti
                    ? this.iti.getNumber()
                    : document.getElementById(this.phoneInputId).value;
                this.errorMessage = "";
                window.dispatchEvent(
                    new CustomEvent("open-whatsapp-modal", { detail: phoneNumber }),
                );
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
            step: "sending", // sending, code, verifying, success, fallback
            // "sms" (Firebase) or "whatsapp" (Meta). Drives which send/verify
            // path verifyCode()/resendCode() take and the channel UI hints.
            channel: "sms",
            // Full E.164 number — WhatsApp send/verify has no Firebase
            // confirmationResult to carry it, so the modal keeps it for resend.
            phoneNumber: "",
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
            get isWhatsAppChannel() {
                return this.channel === "whatsapp";
            },
            get isSendingStep() {
                return this.step === "sending";
            },
            get isCodeStep() {
                return this.step === "code";
            },
            get isFallbackStep() {
                return this.step === "fallback";
            },
            get canFallbackToSms() {
                // SMS won't help when the number is already on another account.
                return !this.phoneAlreadyInUse;
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
                    self.channel = "sms";
                    self.open();
                });

                // WhatsApp: open the modal and immediately kick off the send.
                window.addEventListener("open-whatsapp-modal", function (e) {
                    self.channel = "whatsapp";
                    self.open();
                    self.sendWhatsApp(e.detail);
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
                    this.error = gettext("Please enter the 6-digit code");
                    return;
                }

                this.step = "verifying";
                this.error = "";

                if (this.channel === "whatsapp") {
                    this.verifyWhatsApp(code);
                    return;
                }

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
                if (this.channel === "whatsapp") {
                    // sendWhatsApp() drives showCodeStep()/fallback on its own.
                    this.sendWhatsApp(this.phoneNumber);
                    return;
                }
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
                                self.error =
                                    result.error || gettext("Failed to resend code");
                                self.step = "code";
                            }
                        });
                }
            },

            // ── WhatsApp OTP channel (Meta delivery, server-side verify) ──────
            getCsrfToken: function () {
                // The SMS instance already carries the freshest token (it gets
                // rotated server-side on POST); fall back to a form input, then
                // the cookie, so the modal works on pages without a <form>.
                if (window.phoneVerification && window.phoneVerification.csrfToken) {
                    return window.phoneVerification.csrfToken;
                }
                var input = document.querySelector(
                    'input[name="csrfmiddlewaretoken"]',
                );
                if (input) {
                    return input.value;
                }
                var match = document.cookie.match(/csrftoken=([^;]+)/);
                return match ? match[1] : "";
            },

            updateCsrf: function (token) {
                if (!token) return;
                if (window.phoneVerification) {
                    window.phoneVerification.csrfToken = token;
                }
                var inputs = document.querySelectorAll(
                    'input[name="csrfmiddlewaretoken"]',
                );
                for (var i = 0; i < inputs.length; i++) {
                    inputs[i].value = token;
                }
            },

            clearOtp: function () {
                this.otp0 = "";
                this.otp1 = "";
                this.otp2 = "";
                this.otp3 = "";
                this.otp4 = "";
                this.otp5 = "";
            },

            sendWhatsApp: function (phone) {
                var self = this;
                this.phoneNumber = phone;
                this.step = "sending";
                this.error = "";
                this.phoneAlreadyInUse = false;
                fetch("/api/phone/whatsapp/send/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "application/json",
                        "X-CSRFToken": this.getCsrfToken(),
                    },
                    credentials: "same-origin",
                    body: JSON.stringify({ phone_number: phone }),
                })
                    .then(function (response) {
                        return response.json().catch(function () {
                            return {};
                        });
                    })
                    .then(function (data) {
                        if (data.success) {
                            if (data.already_verified) {
                                self.step = "success";
                                window.dispatchEvent(
                                    new CustomEvent("phone-verified", {
                                        detail: data.phone_number,
                                    }),
                                );
                                setTimeout(function () {
                                    self.close();
                                }, 2000);
                                return;
                            }
                            self.clearOtp();
                            self.showCodeStep(phone);
                            return;
                        }
                        // Failed send: route everything to the fallback step.
                        // not_on_whatsapp / transient errors offer SMS; an
                        // in-use number doesn't (SMS would fail the same way).
                        self.phoneAlreadyInUse =
                            data.error_code === "phone_already_in_use";
                        self.error =
                            data.error ||
                            gettext(
                                "Could not send the WhatsApp code. Please try again.",
                            );
                        self.step = "fallback";
                    })
                    .catch(function () {
                        self.error = gettext(
                            "Could not send the WhatsApp code. Please try again.",
                        );
                        self.step = "fallback";
                    });
            },

            verifyWhatsApp: function (code) {
                var self = this;
                fetch("/api/phone/whatsapp/verify/", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "application/json",
                        "X-CSRFToken": this.getCsrfToken(),
                    },
                    credentials: "same-origin",
                    body: JSON.stringify({ code: code }),
                })
                    .then(function (response) {
                        return response.json().catch(function () {
                            return {};
                        });
                    })
                    .then(function (data) {
                        self.updateCsrf(data.csrfToken);
                        if (data.success && data.phone_verified) {
                            self.step = "success";
                            window.dispatchEvent(
                                new CustomEvent("phone-verified", {
                                    detail: data.phone_number,
                                }),
                            );
                            setTimeout(function () {
                                self.close();
                            }, 2000);
                        } else if (data.error_code === "phone_already_in_use") {
                            self.phoneAlreadyInUse = true;
                            self.step = "code";
                            self.error = data.error;
                        } else {
                            self.step = "code";
                            self.error =
                                data.error ||
                                gettext("Incorrect code. Please try again.");
                            self.clearOtp();
                        }
                    })
                    .catch(function () {
                        self.step = "code";
                        self.error = gettext("Verification failed. Please try again.");
                        self.clearOtp();
                    });
            },

            switchToSms: function () {
                // Hand off to the SMS flow; the component re-opens this modal in
                // "sms" mode (resetting channel/step) and starts the Firebase send.
                window.dispatchEvent(
                    new CustomEvent("start-sms-verification", {
                        detail: this.phoneNumber,
                    }),
                );
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
            get isInstalledVisible() {
                return this.isInstalled;
            },
            get showFallbackVisible() {
                return this.showFallback;
            },
            // The install offer is the "install" prompt (STYLE.md §8): queued
            // behind the cookie sheet and flash messages, and one at a time
            // with the push prompt. base.html leaves the global install
            // banner out on the dashboard, so this card is the only offer.
            get installOffered() {
                return this.canInstall || this.showInstructions;
            },
            get installQueued() {
                return Alpine.store("prompts").isActive("install");
            },
            get canInstallVisible() {
                return this.canInstall && this.installQueued;
            },
            get showInstructionsVisible() {
                return this.showInstructions && this.installQueued;
            },
            // Whether the dashboard card has anything worth showing at all.
            // Deliberately excludes showFallback: that state (desktop
            // Firefox and other non-Chromium browsers with no install path)
            // used to render an "Open crush.lu in Chrome or Safari" card
            // after a 2s timer — a card that was blank until then and only
            // ever offered advice nobody there could act on. The card just
            // doesn't render for that visitor now; the sibling Membership
            // card expands to the full row instead, through
            // membershipSpanClass below.
            get cardVisible() {
                return this.isInstalled || (this.installOffered && this.installQueued);
            },
            // Bound as a bare name on the Membership card: the CSP build
            // can't evaluate an object literal with an inline negation.
            get membershipSpanClass() {
                return this.cardVisible ? "" : "md:col-span-2";
            },

            init: function () {
                var self = this;
                this.$watch("installOffered", function (on) {
                    Alpine.store("prompts").set("install", on);
                });

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

                // iOS-specific instructions (no beforeinstallprompt on iOS).
                // iPadOS Safari reports a desktop "Macintosh" UA, so detect it
                // the way pwa-install.js does (MacIntel + touch points).
                var isIOS =
                    (/iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream) ||
                    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
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
                    }
                    // A used prompt can't be shown again, so a dismissed
                    // one must not leave a dead Install button behind. The
                    // installOffered watcher then frees the prompts queue.
                    self.canInstall = false;
                    self.deferredPrompt = null;
                });
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

            // Queued behind the cookie sheet and flash messages.
            get visible() {
                return this.show && Alpine.store("prompts").isActive("install");
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
                this.$watch("show", function (on) {
                    Alpine.store("prompts").set("install", on);
                });
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
    // [data-label-<key>, English fallback]; keys match theme_toggle_labels.html.
    var THEME_TOGGLE_LABELS = [
        ["system-dark", "System (Dark)"],
        ["system-light", "System (Light)"],
        ["dark-mode", "Dark Mode"],
        ["light-mode", "Light Mode"],
        ["switch-light-mode", "Switch to light mode"],
        ["switch-dark-mode", "Switch to dark mode"],
        ["dark", "Dark"],
        ["light", "Light"],
        ["switch-light", "Switch to Light"],
        ["switch-dark", "Switch to Dark"],
    ];

    function makeThemeToggle() {
        return {
            currentTheme: "light",
            // "light" | "dark" | "system" (no manual choice saved)
            preference: "system",
            systemPreference: "light",
            // Journey / gift pages are always dark (data-theme-lock on <html>):
            // the toggle is disabled and says why, in the page language.
            lockedLabel: "",

            // Status / aria copy, rendered with {% trans %} as data-label-*
            // attributes by components/theme_toggle_labels.html (English
            // fallback where a mount does not include it).
            labels: {},

            init: function () {
                this.lockedLabel = this.$el.getAttribute("data-locked-label") || "";
                var el = this.$el;
                var labels = {};
                THEME_TOGGLE_LABELS.forEach(function (pair) {
                    labels[pair[0]] = el.getAttribute("data-label-" + pair[0]) || pair[1];
                });
                this.labels = labels;
                // Initialize from themeManager, and stay in step when another
                // toggle (navbar / drawer) or the OS changes the theme.
                this._syncFromManager();
                window.addEventListener(
                    "crush:themechange",
                    this._syncFromManager.bind(this),
                );

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
            get isLocked() {
                return document.documentElement.hasAttribute("data-theme-lock");
            },

            get lockedTitle() {
                return this.isLocked ? this.lockedLabel : null;
            },

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
                if (this.isLocked) {
                    return "bg-gray-700 text-yellow-400 cursor-not-allowed";
                }
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
                if (this.isLocked) {
                    return this.lockedLabel;
                }
                // Read the reactive preference (kept in step by
                // crush:themechange), not localStorage, which Alpine cannot track.
                if (this.preference === "system") {
                    return this.isSystemDark
                        ? this.labels["system-dark"]
                        : this.labels["system-light"];
                }
                return this.isDark ? this.labels["dark-mode"] : this.labels["light-mode"];
            },

            get ariaLabel() {
                if (this.isLocked) {
                    return this.lockedLabel;
                }
                return this.isDark
                    ? this.labels["switch-light-mode"]
                    : this.labels["switch-dark-mode"];
            },

            get themeLabel() {
                if (this.preference === "system") {
                    return this.isSystemDark
                        ? this.labels["system-dark"]
                        : this.labels["system-light"];
                }
                return this.isDark ? this.labels.dark : this.labels.light;
            },

            get themeButtonLabel() {
                return this.isDark
                    ? this.labels["switch-light"]
                    : this.labels["switch-dark"];
            },

            // Methods
            cycleTheme: function () {
                this.toggleTheme();
            },

            toggleTheme: function () {
                if (this.isLocked) {
                    return;
                }
                if (window.themeManager) {
                    window.themeManager.toggleTheme();
                    this.currentTheme = window.themeManager.getTheme();
                }
            },

            setTheme: function (theme) {
                if (this.isLocked) {
                    return;
                }
                if (window.themeManager) {
                    window.themeManager.setTheme(theme);
                    this._syncFromManager();
                }
            },

            useSystemPreference: function () {
                if (this.isLocked) {
                    return;
                }
                if (window.themeManager) {
                    window.themeManager.useSystemTheme();
                    this._syncFromManager();
                }
            },

            _syncFromManager: function () {
                if (window.themeManager) {
                    this.currentTheme = window.themeManager.getTheme();
                    this.preference = window.themeManager.getPreference();
                }
            },
        };
    }

    Alpine.data("themeToggle", makeThemeToggle);

    // Light / Dark / System segmented control (mobile drawer). Same state and
    // theme lock as themeToggle; on a theme-locked page every option is
    // aria-disabled and the locked label explains why.
    Alpine.data("themeChoice", function () {
        var selected =
            "cursor-pointer bg-[var(--color-surface-card)] text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white";
        var idle = "cursor-pointer text-gray-700 dark:text-gray-300";
        // Locked (always-dark) page: no option reads as pressed or clickable.
        var locked = "text-gray-700 dark:text-gray-300 opacity-60 cursor-not-allowed";
        return mixin(makeThemeToggle(), {
            _optionClass: function (chosen) {
                if (this.isLocked) {
                    return locked;
                }
                return chosen ? selected : idle;
            },
            // aria-pressed: a locked (always-dark) page has no pressed option.
            get isLightChosen() {
                return !this.isLocked && this.preference === "light";
            },
            get isDarkChosen() {
                return !this.isLocked && this.preference === "dark";
            },
            get isSystemChosen() {
                return !this.isLocked && this.preference === "system";
            },
            get lightOptionClass() {
                return this._optionClass(this.isLightChosen);
            },
            get darkOptionClass() {
                return this._optionClass(this.isDarkChosen);
            },
            get systemOptionClass() {
                return this._optionClass(this.isSystemChosen);
            },
            chooseLight: function () {
                this.setTheme("light");
            },
            chooseDark: function () {
                this.setTheme("dark");
            },
            chooseSystem: function () {
                this.useSystemPreference();
            },
        });
    });

    // ============================================================
    // Ghost Story Slideshow - auto-playing, no controls
    // (includes/ghost-story-compact.html on profile_submitted.html)
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

    // Event-cancel double-submit guard (event_cancel.html). The first submit
    // enters the confirming state and goes through natively; any further
    // submit is swallowed so a double tap cannot post twice. Replaces an
    // inline onsubmit handler the nonce-based CSP blocked.
    Alpine.data("eventCancelForm", function () {
        return mixin(makeConfirm({ autoSubmit: false }), {
            guardSubmit(event) {
                if (this.isConfirming) {
                    event.preventDefault();
                    return;
                }
                this.request();
            },
            // Back/forward cache restores the page as it was left: re-arm it.
            resetGuard(event) {
                if (event && event.persisted) this.cancelConfirm();
            },
        });
    });

    // Spark confirm inline component (replaces browser confirm dialog)
    Alpine.data("sparkConfirm", function () {
        // Composes makeConfirm with the template-facing API the spark
        // partials expect (isInitial / showConfirm / cancel). autoSubmit
        // is OFF — the spark confirmation panel makes its own HTMX call,
        // it does NOT submit a parent form.
        return mixin(makeConfirm({ autoSubmit: false }), {
            get isInitial() {
                return this.isIdle;
            },
            showConfirm() {
                this.request();
            },
            cancel() {
                this.cancelConfirm();
            },
        });
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

    // Event Poll Voting component
    // CSP-safe: all logic in methods/getters, no inline expressions.
    // The ballot is a real <form> of native radios/checkboxes (name="option_ids")
    // that also works without JS; this component posts it as JSON instead, shows
    // errors inline, and swaps in the server-rendered results on success.
    Alpine.data("eventPollVoting", function () {
        return {
            selectedCount: 0,
            isSubmitting: false,
            hasVoted: false,
            errorMessage: "",

            _textSubmit: gettext("Submit Vote"),
            _textSubmitting: gettext("Submitting..."),
            _textSubmitted: gettext("Vote Submitted"),
            _textError: gettext("Failed to submit vote"),
            _textNetworkError: gettext("Network error. Please try again."),
            _textRateLimited: gettext(
                "Too many attempts. Take a breath and try again in a minute.",
            ),
            _textSuccess: gettext("Thanks, your vote is in!"),

            init() {
                var el = this.$el;
                var keys = [
                    "Submit",
                    "Submitting",
                    "Submitted",
                    "Error",
                    "NetworkError",
                    "RateLimited",
                    "Success",
                ];
                for (var i = 0; i < keys.length; i++) {
                    var value = el.dataset["text" + keys[i]];
                    if (value) this["_text" + keys[i]] = value;
                }
                // A browser may restore a checked option after reload/back.
                this.updateSelection();
            },

            get submitButtonText() {
                if (this.isSubmitting) return this._textSubmitting;
                if (this.hasVoted) return this._textSubmitted;
                return this._textSubmit;
            },

            get isDisabled() {
                return this.isSubmitting || this.hasVoted || this.selectedCount === 0;
            },

            get isBallotShown() {
                return !this.hasVoted;
            },

            updateSelection: function () {
                this.selectedCount = this._selectedIds().length;
                this.errorMessage = "";
            },

            _selectedIds: function () {
                var checked = this.$root.querySelectorAll(
                    'input[name="option_ids"]:checked',
                );
                return Array.from(checked).map(function (input) {
                    return parseInt(input.value, 10);
                });
            },

            submitVote: function () {
                var ids = this._selectedIds();
                if (!ids.length || this.isSubmitting || this.hasVoted) return;

                var self = this;
                var form = this.$root.querySelector("form");
                var token = form.querySelector("[name=csrfmiddlewaretoken]");
                // The vote URL is language-neutral: reply in the page's language.
                var lang = form.querySelector('input[name="lang"]');
                var payload = { option_ids: ids, lang: lang ? lang.value : "" };
                // Optional "I am..." answer, rendered only for voters without a profile gender
                var gender = form.querySelector('input[name="voter_gender"]:checked');
                if (gender) payload.gender = gender.value;

                self.isSubmitting = true;
                self.errorMessage = "";
                fetch(form.action, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "application/json",
                        "X-CSRFToken": token ? token.value : "",
                    },
                    body: JSON.stringify(payload),
                })
                    .then(function (r) {
                        if (r.status === 429) return self._fail(self._textRateLimited);
                        var type = r.headers.get("Content-Type") || "";
                        // A CSRF 403 page or a login redirect is HTML, not JSON.
                        if (type.indexOf("application/json") === -1) {
                            return self._fail(self._textError);
                        }
                        return r.json().then(function (data) {
                            if (r.ok && data.success) return self._succeed(data);
                            self._fail(data.error || self._textError);
                        });
                    })
                    .catch(function () {
                        self._fail(self._textNetworkError);
                    });
            },

            _fail: function (message) {
                this.isSubmitting = false;
                this.errorMessage = message;
            },

            _succeed: function (data) {
                this.isSubmitting = false;
                this.hasVoted = true;
                var results = this.$refs.results;
                if (results && data.results_html) {
                    results.innerHTML = data.results_html;
                    results.focus();
                }
                // The header count sits outside this component; update it from
                // the same response that renders the results.
                var total = document.querySelector("[data-poll-total-votes]");
                if (total && data.total_votes_label) {
                    total.textContent = data.total_votes_label;
                }
                window.dispatchEvent(
                    new CustomEvent("show-toast", {
                        detail: { type: "success", message: this._textSuccess },
                    }),
                );
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
                var self = this;
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
                // Wizard draft restore: data-initial is server-rendered from the
                // saved profile, which is empty for a draft that hasn't hit
                // Continue yet. When the wizard re-hydrates, adopt the persisted
                // ids so the chips (and the x-bound hidden input) match — without
                // this the reactive hiddenValue would overwrite the restored value
                // back to empty.
                this.$el.addEventListener("trait-restore", function (e) {
                    if (e.detail && Array.isArray(e.detail.ids)) {
                        self.selected = e.detail.ids.slice();
                        self._syncAllChips();
                    }
                });
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

                    // Toggle-button semantics for assistive tech (the chips are
                    // <button type="button">, so aria-pressed carries the state).
                    btn.setAttribute("aria-pressed", isSelected ? "true" : "false");

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
                        self.noteError = gettext("Network error. Please try again.");
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
    // Photo editor Back link. With a same-app `next`, Back returns there only
    // once a main photo exists; photo uploads and deletes swap
    // #photo-card-1 over HTMX without re-rendering this link, so keep its
    // href in step after each swap.
    Alpine.data("photoEditorBack", function () {
        return {
            _onSwap: null,
            init: function () {
                var el = this.$el;
                this._onSwap = function (event) {
                    var target = event.detail && event.detail.target;
                    if (!target || target.id !== "photo-card-1") return;
                    var hasPhoto = !!target.querySelector(
                        ".photo-preview-container.has-photo",
                    );
                    el.setAttribute(
                        "href",
                        hasPhoto ? el.dataset.next : el.dataset.fallback,
                    );
                };
                document.body.addEventListener("htmx:afterSwap", this._onSwap);
            },
            destroy: function () {
                document.body.removeEventListener("htmx:afterSwap", this._onSwap);
            },
        };
    });

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
                            self.error = data.error || gettext("Import failed.");
                        }
                    })
                    .catch(function () {
                        self.error = gettext("Network error. Please try again.");
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

    // Notification bell — fetches recent notifications, shows unread count,
    // marks rows read on click. Polls lazily (on open) — no background poll.
    Alpine.data("notificationBell", function () {
        return {
            isOpen: false,
            unreadCount: 0,
            unreadBroadcastWired: false,
            items: [],
            loaded: false,

            get hasUnread() {
                return this.unreadCount > 0;
            },
            get hasItems() {
                return this.items.length > 0;
            },
            get hasNoItems() {
                return this.items.length === 0;
            },
            get badgeText() {
                if (this.unreadCount > 9) return "9+";
                return String(this.unreadCount);
            },

            init: function () {
                var self = this;
                // The CSP Alpine build can't iterate nested item props via x-for,
                // so the list is rendered imperatively whenever items change.
                this.$watch("items", function () {
                    self.renderItems();
                });
                // Seed from the server-rendered count (the same helper the API
                // uses) so this bell and the mobile top bar start equal, then
                // broadcast every change so the mobile badge follows. init runs
                // twice here (x-data auto-init + x-init="init"): wire the
                // broadcast once.
                this.unreadCount = parseInt(
                    this.$el.getAttribute("data-unread-count") || "0",
                    10,
                ) || 0;
                if (!this.unreadBroadcastWired) {
                    this.unreadBroadcastWired = true;
                    this.$watch("unreadCount", function (count) {
                        window.dispatchEvent(
                            new CustomEvent("notif-unread-count", {
                                detail: count,
                            }),
                        );
                    });
                }
                // Fetch unread count on mount so the badge appears immediately
                this.refresh();
            },

            renderItems: function () {
                var list = this.$refs.notiflist;
                if (!list) return;
                var self = this;
                list.replaceChildren();
                this.items.forEach(function (item) {
                    var li = document.createElement("li");
                    li.className =
                        "px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/50";

                    var a = document.createElement("a");
                    a.href = item.link_url || "#";
                    a.className = "block";
                    // Synchronous guard so a rapid double-click can't fire two
                    // mark-read POSTs (which would double-decrement the badge).
                    var marking = false;
                    a.addEventListener("click", function () {
                        if (!item.is_unread || marking) return;
                        marking = true;
                        self.markRead(item.id).catch(function (err) {
                            // Release the lock so a failed request can be retried.
                            marking = false;
                            console.warn("notificationBell.markRead:", err);
                        });
                    });

                    var row = document.createElement("div");
                    row.className = "flex items-start gap-2";

                    if (item.is_unread) {
                        var dot = document.createElement("span");
                        dot.className =
                            "flex-shrink-0 mt-1.5 w-2 h-2 rounded-full bg-crush-purple";
                        row.appendChild(dot);
                    }

                    var content = document.createElement("div");
                    content.className = "flex-1 min-w-0";

                    var title = document.createElement("p");
                    title.className =
                        "text-sm font-medium text-gray-900 dark:text-white mb-0 truncate";
                    title.textContent = item.title || "";
                    content.appendChild(title);

                    var body = document.createElement("p");
                    body.className =
                        "text-xs text-gray-500 dark:text-gray-400 mb-0 line-clamp-2";
                    body.textContent = item.body || "";
                    content.appendChild(body);

                    var time = document.createElement("p");
                    time.className =
                        "text-[11px] text-gray-400 dark:text-gray-500 mt-1";
                    time.textContent = item.relative_time || "";
                    content.appendChild(time);

                    row.appendChild(content);
                    a.appendChild(row);
                    li.appendChild(a);
                    list.appendChild(li);
                });
            },

            toggle: function () {
                this.isOpen = !this.isOpen;
                if (this.isOpen && !this.loaded) {
                    this.refresh();
                }
            },

            close: function () {
                this.isOpen = false;
            },

            refresh: function () {
                var self = this;
                fetch("/api/notifications/", {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                })
                    .then(function (r) {
                        if (!r.ok) throw new Error("fetch failed");
                        return r.json();
                    })
                    .then(function (data) {
                        self.unreadCount = data.unread_count || 0;
                        self.items = (data.items || []).map(function (it) {
                            return Object.assign({}, it, {
                                relative_time: self.formatRelativeTime(it.created_at),
                            });
                        });
                        self.loaded = true;
                    })
                    .catch(function (err) {
                        console.warn("notificationBell.refresh:", err);
                    });
            },

            markRead: function (id) {
                var self = this;
                var token = self.getCsrfToken();
                // Returns the promise so callers can react to failure; rejects on
                // non-ok responses (fetch only rejects on network errors).
                return fetch("/api/notifications/" + id + "/read/", {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { "X-CSRFToken": token, Accept: "application/json" },
                }).then(function (r) {
                    if (!r.ok) throw new Error("mark read failed");
                    // Update local state — server is source of truth on next open
                    self.items = self.items.map(function (it) {
                        if (it.id === id) {
                            return Object.assign({}, it, { is_unread: false });
                        }
                        return it;
                    });
                    self.unreadCount = Math.max(0, self.unreadCount - 1);
                });
            },

            markAllRead: function () {
                var self = this;
                var token = self.getCsrfToken();
                fetch("/api/notifications/mark-all-read/", {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { "X-CSRFToken": token, Accept: "application/json" },
                })
                    .then(function () {
                        self.unreadCount = 0;
                        self.items = self.items.map(function (it) {
                            return Object.assign({}, it, { is_unread: false });
                        });
                    })
                    .catch(function (err) {
                        console.warn("notificationBell.markAllRead:", err);
                    });
            },

            markReadFromEvent: function (event) {
                var id = event.detail;
                this.unreadCount = Math.max(0, this.unreadCount - 1);
                this.items = this.items.map(function (it) {
                    if (it.id === id)
                        return Object.assign({}, it, { is_unread: false });
                    return it;
                });
            },

            formatRelativeTime: function (iso) {
                if (!iso) return "";
                var then = new Date(iso);
                var diffMs = Date.now() - then.getTime();
                var diffMin = Math.floor(diffMs / 60000);
                if (diffMin < 1) return "just now";
                if (diffMin < 60) return diffMin + "m ago";
                var diffHr = Math.floor(diffMin / 60);
                if (diffHr < 24) return diffHr + "h ago";
                var diffDay = Math.floor(diffHr / 24);
                if (diffDay < 7) return diffDay + "d ago";
                return then.toLocaleDateString();
            },

            getCsrfToken: function () {
                var name = "csrftoken=";
                var parts = (document.cookie || "").split(";");
                for (var i = 0; i < parts.length; i++) {
                    var c = parts[i].trim();
                    if (c.indexOf(name) === 0) return c.substring(name.length);
                }
                var hidden = document.querySelector(
                    'input[name="csrfmiddlewaretoken"]',
                );
                return hidden ? hidden.value : "";
            },
        };
    });

    // Event feedback NPS slider — live label as user drags
    Alpine.data("feedbackNpsSlider", function () {
        return {
            score: 8,
            label: "",

            init: function () {
                var self = this;
                var input = this.$el.querySelector('input[type="range"]');
                if (input) {
                    self.score = parseInt(input.value, 10) || 8;
                    self.updateLabel();
                    input.addEventListener("input", function () {
                        self.score = parseInt(input.value, 10) || 0;
                        self.updateLabel();
                    });
                }
            },

            updateLabel: function () {
                if (this.score >= 9) {
                    this.label = "I'd strongly recommend";
                } else if (this.score >= 7) {
                    this.label = "I'd recommend";
                } else if (this.score >= 4) {
                    this.label = "Mixed";
                } else {
                    this.label = "Wouldn't recommend";
                }
            },
        };
    });

    // Event Identity edit section + wizard step 2 (2026 redesign): keeps the
    // interest cap (max 8) and the "Ask me about…" cap (max 3) live, and gates
    // the ask-me-about chips to the currently-selected interests. CSP-friendly:
    // no expressions in markup, all logic here, queries via $root (the methods
    // are invoked from child @change handlers where $el is the input itself).
    Alpine.data("eventIdentity", function () {
        return {
            interestMax: 8,
            askMeMax: 3,
            interestCount: 0,
            askMeCount: 0,

            init: function () {
                var self = this;
                this.interestMax = parseInt(
                    this.$root.getAttribute("data-interest-max") || "8",
                    10,
                );
                this.askMeMax = parseInt(
                    this.$root.getAttribute("data-askme-max") || "3",
                    10,
                );
                this._syncInterests();
                this._syncAskMe();
                // Wizard drafts hydrate asynchronously, after this init has run
                // (profileWizard.populateFieldsFromDraft programmatically checks
                // the boxes without firing change events). Re-sync when it signals
                // so restored counters, caps and "Ask me about" chip visibility
                // are correct — mirrors the dobPicker/cantonMap restore pattern.
                this.$root.addEventListener("event-identity-restore", function () {
                    self._syncInterests();
                    self._syncAskMe();
                });
            },

            onInterestChange: function () {
                // A deselected interest must not stay an "Ask me about" target.
                var selected = {};
                this.$root
                    .querySelectorAll('input[name="interests_new"]')
                    .forEach(function (b) {
                        if (b.checked) selected[b.value] = true;
                    });
                this.$root
                    .querySelectorAll('input[name="ask_me_about"]')
                    .forEach(function (a) {
                        if (!selected[a.value]) a.checked = false;
                    });
                this._syncInterests();
                this._syncAskMe();
            },

            onAskMeChange: function () {
                this._syncAskMe();
            },

            _syncInterests: function () {
                var boxes = this.$root.querySelectorAll(
                    'input[name="interests_new"]',
                );
                var checked = 0;
                var selected = {};
                boxes.forEach(function (b) {
                    if (b.checked) {
                        checked++;
                        selected[b.value] = true;
                    }
                });
                this.interestCount = checked;
                var atCap = checked >= this.interestMax;
                boxes.forEach(function (b) {
                    b.disabled = atCap && !b.checked;
                });
                // Reveal ask-me-about chips only for the selected interests.
                this.$root
                    .querySelectorAll("[data-askme-wrap]")
                    .forEach(function (wrap) {
                        var id = wrap.getAttribute("data-interest-id");
                        if (selected[id]) {
                            wrap.classList.remove("hidden");
                        } else {
                            wrap.classList.add("hidden");
                        }
                    });
                var emptyHint = this.$root.querySelector("[data-askme-empty]");
                if (emptyHint) {
                    if (checked > 0) {
                        emptyHint.classList.add("hidden");
                    } else {
                        emptyHint.classList.remove("hidden");
                    }
                }
            },

            _syncAskMe: function () {
                var boxes = this.$root.querySelectorAll(
                    'input[name="ask_me_about"]',
                );
                var checked = 0;
                boxes.forEach(function (b) {
                    if (b.checked) checked++;
                });
                this.askMeCount = checked;
                var atCap = checked >= this.askMeMax;
                boxes.forEach(function (b) {
                    b.disabled = atCap && !b.checked;
                });
            },
        };
    });

    // Auto-redirect countdown shown on the profile-approved state of profile_submitted.html.
    // Reads the destination URL from data-dashboard-url to stay language-prefix–safe.
    Alpine.data("approvedCountdown", () => ({
        countdown: 5,
        init() {
            const url = this.$el.dataset.dashboardUrl;
            const t = setInterval(() => {
                if (this.countdown <= 1) {
                    clearInterval(t);
                    window.location.href = url;
                } else {
                    this.countdown--;
                }
            }, 1000);
        },
    }));

    // ========================================================================
    // Verify-email resend cooldown (account/verification_sent_crush.html)
    // ========================================================================

    // The server's cooldown is per address (#1059), so a corrected address in
    // "Use a different address" re-enables the button while the countdown for
    // the cooled address keeps running. The page only knows that address as a
    // SHA-256 hash of its trimmed, lower-cased form.
    Alpine.data("resendCooldown", () => ({
        coolingDown: false,
        otherAddress: false,
        cooldownHash: "",
        inputSeq: 0,
        remaining: 0,
        get disabled() {
            return this.coolingDown && !this.otherAddress;
        },
        get enabled() {
            return !this.disabled;
        },
        init() {
            this.cooldownHash = this.$el.dataset.cooldownHash || "";
            const cooldownUntil = parseInt(this.$el.dataset.cooldownUntil, 10) || 0;
            const serverNow = parseInt(this.$el.dataset.serverNow, 10) || 0;
            this.remaining = Math.max(0, cooldownUntil - serverNow);
            if (this.remaining <= 0) {
                return;
            }
            this.coolingDown = true;
            const t = setInterval(() => {
                this.remaining--;
                if (this.remaining <= 0) {
                    clearInterval(t);
                    this.coolingDown = false;
                }
            }, 1000);
        },
        onEmailInput(event) {
            const value = (event.target.value || "").trim().toLowerCase();
            const seq = ++this.inputSeq;
            if (!value) {
                this.otherAddress = false;
                return;
            }
            // Without the hash or SubtleCrypto (plain-http dev hosts) any typed
            // address counts as different; the server still no-ops a repeat.
            if (!this.cooldownHash || !window.crypto || !window.crypto.subtle) {
                this.otherAddress = true;
                return;
            }
            window.crypto.subtle
                .digest("SHA-256", new TextEncoder().encode(value))
                .then((buf) => {
                    if (seq !== this.inputSeq) {
                        return;
                    }
                    const hex = Array.from(new Uint8Array(buf))
                        .map((b) => b.toString(16).padStart(2, "0"))
                        .join("");
                    this.otherAddress = hex !== this.cooldownHash;
                });
        },
    }));

    // Community Supporter donation card (components/support_card.html).
    //
    // Every user-visible string arrives on data- attributes rather than
    // gettext() here: the card's strings were written as {% trans %} in the
    // template and so live in the `django` catalog, while gettext() in this
    // file reads `djangojs`. Passing them in keeps one copy of each string,
    // in the catalog the translators already have.
    //
    // The card is only ever rendered with commerce enabled -- the template
    // withholds this whole half inside a native shell -- so there is no
    // "nothing to wire up" guard to port.
    Alpine.data("donationCard", function () {
        return {
            min: 2,
            max: 500,
            endpoint: "",
            labelSupport: "",
            msgMin: "",
            msgMax: "",
            msgPreparing: "",
            msgUnavailable: "",
            msgFailed: "",

            // The amount shown on the button. Distinct from `activeTier`: a
            // typed amount equal to a preset still leaves every preset
            // unhighlighted, so the two cannot be derived from each other.
            amount: NaN,
            activeTier: null,
            busy: false,
            errorMessage: "",

            init: function () {
                var el = this.$root;
                this.min = parseFloat(el.getAttribute("data-min") || "2");
                this.max = parseFloat(el.getAttribute("data-max") || "500");
                this.endpoint = el.getAttribute("data-endpoint") || "";
                this.labelSupport = el.getAttribute("data-label-support") || "";
                this.msgMin = el.getAttribute("data-msg-min") || "";
                this.msgMax = el.getAttribute("data-msg-max") || "";
                this.msgPreparing = el.getAttribute("data-msg-preparing") || "";
                this.msgUnavailable = el.getAttribute("data-msg-unavailable") || "";
                this.msgFailed = el.getAttribute("data-msg-failed") || "";

                // data-default names the tier the template already rendered
                // active, so this re-asserts the server's state rather than
                // changing it -- no flash, and the card still reads correctly
                // if Alpine never boots.
                this.activeTier = el.getAttribute("data-default");
                this.amount = this.toCents(this.activeTier || "");
                this.syncTiers();
            },

            get hasError() {
                return this.errorMessage !== "";
            },

            get buttonLabel() {
                if (this.busy) {
                    return this.msgPreparing;
                }
                if (!isNaN(this.amount) && this.amount > 0) {
                    // "Support · €10" (#4-10), not "Support the Project (€10.00)":
                    // the full-sentence label wrapped to two lines on a 390px
                    // pill and lost its side padding. Whole euros drop the
                    // ".00" so the common presets stay short; a genuinely
                    // fractional custom amount still shows its cents.
                    var amountText =
                        this.amount % 1 === 0
                            ? this.amount.toFixed(0)
                            : this.amount.toFixed(2);
                    return this.labelSupport + " · €" + amountText;
                }
                return this.labelSupport;
            },

            // Round the decimal the member typed, not its binary float, so this
            // agrees with the server's Decimal ROUND_HALF_UP on every input.
            //
            // toFixed() rounds the float and gets 2.355 wrong (€2.35 shown, €2.36
            // charged). Nudging by Number.EPSILON first looks like it fixes that
            // and does for 2.355 -- but it is still float arithmetic and diverges
            // on 4.6% of half-cent amounts: toCents(10.075) gave 10.07 where the
            // server says 10.08. Digits are the only representation both sides
            // agree on, so parse them.
            //
            // Half-up needs only "is the remainder >= 0.005", which is true
            // exactly when the third decimal digit is 5-9 -- later digits cannot
            // change that, so they are ignored rather than examined.
            toCents: function (value) {
                var text = String(value).trim();
                if (!/^\d+(\.\d*)?$|^\.\d+$/.test(text)) {
                    return NaN;   // scientific notation, signs, junk — let the caller reject it
                }
                var parts = text.split(".");
                var whole = parseInt(parts[0] || "0", 10);
                var frac = parts[1] || "";
                var cents = whole * 100 + parseInt(frac.slice(0, 2).padEnd(2, "0"), 10);
                if (frac.length > 2 && frac.charCodeAt(2) >= 53) {
                    cents += 1;
                }
                return cents / 100;
            },

            selectTier: function () {
                // $el, not $root: the handler sits on the clicked button, which
                // is what carries the amount.
                var btn = this.$el;
                if (this.$refs.custom) this.$refs.custom.value = "";
                this.activeTier = btn.getAttribute("data-amount");
                this.amount = this.toCents(this.activeTier || "");
                this.syncTiers();
            },

            onCustomInput: function () {
                var raw = this.customRaw();
                var cents = this.toCents(raw);
                // Only a usable amount moves the button and clears the presets.
                // Half-typed input ("1", "") leaves the last selection showing;
                // submit() is where a non-empty custom field takes over
                // regardless.
                if (!isNaN(cents) && cents >= this.min) {
                    this.activeTier = null;
                    this.amount = cents;
                    this.syncTiers();
                }
            },

            submit: function () {
                var self = this;
                var final = this.effectiveAmount();

                if (isNaN(final) || final < this.min) {
                    this.errorMessage = this.msgMin;
                    return;
                }

                // Mirrors DONATION_MAX_EUR in views_payments.py. The server is
                // what actually enforces this; checking here only saves a round
                // trip on an obvious slipped decimal point.
                if (final > this.max) {
                    this.errorMessage = this.msgMax;
                    return;
                }

                this.errorMessage = "";
                this.busy = true;

                // The endpoint comes in as an absolute path from the template,
                // like every other checkout call site. Reversing it by name
                // would tie the partial to which urlconf is active and turn a
                // NoReverseMatch into a 500 on the whole page.
                //
                // CSRF comes from the hidden input base.html renders --
                // CSRF_COOKIE_HTTPONLY is on, so there is no cookie to read.
                fetch(this.endpoint, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value || ""
                    },
                    body: JSON.stringify({ amount: final })
                })
                    .then(function (res) { return res.json(); })
                    .then(function (data) {
                        if (data.success && data.widget_url) {
                            window.location.href = data.widget_url;
                        } else {
                            throw new Error(data.error || self.msgUnavailable);
                        }
                    })
                    .catch(function (err) {
                        self.busy = false;
                        // Put the attempted figure back on the button; only an
                        // amount that already passed both bounds gets this far.
                        self.amount = self.toCents(String(final));
                        self.errorMessage = err.message || self.msgFailed;
                    });
            },

            customRaw: function () {
                return this.$refs.custom ? (this.$refs.custom.value || "").trim() : "";
            },

            // A non-empty custom field is authoritative, valid or not. Taking it
            // only when it already passed the minimum meant typing "1" left the
            // amount on the last preset, so the checks in submit() saw a valid
            // €10 and opened a €10 checkout for someone who asked for €1. The
            // input is not in a form, so its `min` attribute never intervenes.
            // Resolved to the cent from the typed digits, so the figure checked,
            // the figure on the button and the figure SumUp charges are one.
            effectiveAmount: function () {
                var custom = this.customRaw();
                return custom !== "" ? this.toCents(custom) : this.amount;
            },

            // Toggles only the classes that differ between the two states, so
            // the shared ones stay where the template put them instead of being
            // restated (and kept in sync) here.
            syncTiers: function () {
                var active = this.activeTier;
                var buttons = this.$root.querySelectorAll("[data-amount]");
                buttons.forEach(function (btn) {
                    var on = btn.getAttribute("data-amount") === active;

                    btn.classList.toggle("border-2", on);
                    btn.classList.toggle("border-pink-500", on);
                    // pink-700: white / pink-100 on pink-500 was 2.7 / 2.2:1
                    btn.classList.toggle("bg-pink-700", on);
                    btn.classList.toggle("shadow-md", on);
                    btn.classList.toggle("border", !on);
                    btn.classList.toggle("border-gray-200", !on);
                    btn.classList.toggle("dark:border-white/20", !on);
                    btn.classList.toggle("bg-gray-50", !on);
                    btn.classList.toggle("dark:bg-white/10", !on);
                    btn.classList.toggle("hover:bg-pink-50", !on);
                    btn.classList.toggle("dark:hover:bg-pink-600/30", !on);

                    var amountSpan = btn.querySelector(".tier-amount");
                    if (amountSpan) {
                        amountSpan.classList.toggle("text-white", on);
                        amountSpan.classList.toggle("text-gray-900", !on);
                        amountSpan.classList.toggle("dark:text-white", !on);
                    }

                    var labelSpan = btn.querySelector(".tier-label");
                    if (labelSpan) {
                        labelSpan.classList.toggle("text-pink-100", on);
                        labelSpan.classList.toggle("text-gray-600", !on);
                        labelSpan.classList.toggle("dark:text-purple-200", !on);
                    }
                });
            },
        };
    });

    // Named component behind the SumUp "Pay with Card" / "Pay with Crush
    // Credit" buttons on event_detail.html, _event_registration_success.html
    // and my_events.html (UX Wave 3 WP8 · finding 4-05). The three templates
    // used to carry byte-identical <script> blocks with a click-delegation
    // listener; a double tap fired two checkouts (no disabled state) and a
    // failure showed an untranslated alert(), which reads as a scam dialog
    // inside the iOS/Android WebView. One Alpine.data component fixes both:
    // isLoading disables the button for the duration of the fetch, and a
    // failure goes to Alpine.store("toasts") (the documented public API in
    // toast-component.js) instead of alert().
    //
    // Config comes in as data-* attributes (CSP build: x-data cannot take
    // arguments), same convention as donationCard above. data-label /
    // data-label-loading are pre-rendered so the resting state matches the
    // server-rendered fallback content exactly, and the loading state names
    // itself instead of just spinning.
    Alpine.data("sumupCheckoutButton", function () {
        return {
            registrationId: "",
            paymentMethod: "card",
            restingLabel: "",
            loadingLabel: "",
            errorMessage: "",
            isLoading: false,

            init: function () {
                var el = this.$el;
                this.registrationId = el.getAttribute("data-sumup-reg-id") || "";
                this.paymentMethod = el.getAttribute("data-payment-method") || "card";
                this.restingLabel = el.getAttribute("data-label") || "";
                this.loadingLabel = el.getAttribute("data-label-loading") || "";
                this.errorMessage = el.getAttribute("data-msg-error") || "";

                // start() leaves isLoading true while navigating to
                // widget_url so the button stays disabled during the
                // redirect. If the browser instead restores this page from
                // the back/forward cache (e.g. the member backs out of the
                // SumUp checkout on mobile Safari/Chrome), that redirect
                // never completes and the button would stay stuck on the
                // loading label until a hard reload. pageshow with
                // event.persisted fires on a bfcache restore (never on a
                // normal load), so clear the stale loading state here.
                var self = this;
                window.addEventListener("pageshow", function (event) {
                    if (event.persisted) {
                        self.isLoading = false;
                    }
                });
            },

            get label() {
                return this.isLoading ? this.loadingLabel : this.restingLabel;
            },

            // Named getter, not "x-show=\"!isLoading\"" in the templates:
            // the CSP build evaluates bare property/method names only.
            get idle() {
                return !this.isLoading;
            },

            // CSRF_COOKIE_HTTPONLY is True, so document.cookie can't see
            // csrftoken. Read the hidden input base.html renders instead
            // (same as HTMX does).
            getCsrfToken: function () {
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                return input ? input.value : "";
            },

            start: function () {
                if (this.isLoading || !this.registrationId) return;
                this.isLoading = true;
                var self = this;
                fetch("/payments/sumup/create-event-checkout/" + this.registrationId + "/", {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": this.getCsrfToken(),
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({ payment_method: this.paymentMethod }),
                })
                    .then(function (res) {
                        return res.json().catch(function () {
                            throw new Error("HTTP " + res.status);
                        });
                    })
                    .then(function (data) {
                        if (data.success && data.widget_url) {
                            // Navigating away — leave isLoading true so the
                            // button stays disabled during the redirect.
                            window.location.href = data.widget_url;
                            return;
                        }
                        self.isLoading = false;
                        Alpine.store("toasts").add({
                            type: "error",
                            message: data.error || self.errorMessage,
                        });
                    })
                    .catch(function () {
                        self.isLoading = false;
                        Alpine.store("toasts").add({
                            type: "error",
                            message: self.errorMessage,
                        });
                    });
            },
        };
    });

    // =========================================================================
    // UX Wave 3 · WP5 (finding 3-13) — screening-call self-booking
    // (crush_lu/templates/crush_lu/book_screening.html)
    // =========================================================================

    // One instance per coach block. Slots are plain radio inputs (styled via
    // Tailwind `peer-checked`, no per-item Alpine expression needed — the CSP
    // build only allows bare method/property names), this component just
    // tracks whether something is picked and mirrors the chosen slot's
    // ISO datetimes into the two hidden fields the form actually submits.
    // Picking a slot then pressing the sticky "Confirm" button is itself the
    // two-step confirmation the finding asked for — no second dialog needed.
    Alpine.data("bookingSlotPicker", function () {
        return {
            selectedStart: "",
            selectedEnd: "",
            selectedLabel: "",
            showAll: false,

            get hasSelection() {
                return !!this.selectedStart;
            },

            // The CSP-friendly Alpine build forbids expressions (e.g.
            // "!hasSelection") inside x-bind, so the negation needs its own
            // bare-name getter — see connectOnboarding.notShowSecondStory
            // for the same pattern elsewhere in this file.
            get hasNoSelection() {
                return !this.hasSelection;
            },

            get isShowAllHidden() {
                return !this.showAll;
            },

            pickSlot: function (event) {
                var el = event.currentTarget || event.target;
                this.selectedStart = el.dataset.start || "";
                this.selectedEnd = el.dataset.end || "";
                this.selectedLabel = el.dataset.label || "";
            },

            toggleShowAll: function () {
                this.showAll = !this.showAll;
            },
        };
    });

    // Cancel-booking confirm, composed from the same makeConfirm mixin
    // sparkConfirm uses — idle "Cancel booking" link, then an inline
    // "Yes, cancel / No, keep it" choice before the form actually submits.
    Alpine.data("bookingCancelConfirm", function () {
        return mixin(makeConfirm(), {
            get isInitial() {
                return this.isIdle;
            },
            showConfirm: function () {
                this.request();
            },
            cancel: function () {
                this.cancelConfirm();
            },
        });
    });
});
