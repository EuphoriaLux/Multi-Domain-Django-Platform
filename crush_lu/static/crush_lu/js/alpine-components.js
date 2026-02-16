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

document.addEventListener('alpine:init', function() {

    // Event ticket "Add to Google Wallet" button component
    Alpine.data('eventTicketButton', function() {
        return {
            loading: false,
            error: false,
            errorMsg: '',

            get isLoading() { return this.loading; },
            get hasError() { return this.error; },
            get errorMessage() { return this.errorMsg; },

            saveToWallet: function() {
                var self = this;
                var regId = this.$el.closest('[data-registration-id]').getAttribute('data-registration-id');
                if (!regId) return;

                self.loading = true;
                self.error = false;
                self.errorMsg = '';

                fetch('/wallet/google/event-ticket/' + regId + '/jwt/')
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        self.loading = false;
                        if (data.jwt) {
                            window.open('https://pay.google.com/gp/v/save/' + data.jwt, '_blank');
                        } else {
                            self.error = true;
                            self.errorMsg = data.error || 'Failed to generate ticket.';
                        }
                    })
                    .catch(function(err) {
                        self.loading = false;
                        self.error = true;
                        self.errorMsg = 'Network error. Please try again.';
                    });
            }
        };
    });

    // Coach check-in scanner component
    Alpine.data('coachCheckin', function() {
        return {
            scannerActive: false,
            scanner: null,
            result: false,
            success: false,
            errorState: false,
            message: '',
            checkins: [],

            get scannerButtonText() { return this.scannerActive ? 'Stop Scanner' : 'Start Scanner'; },
            get hasResult() { return this.result; },
            get isSuccess() { return this.success; },
            get isError() { return this.errorState; },
            get resultMessage() { return this.message; },
            get recentCheckins() { return this.checkins; },
            get recentCheckinCount() { return this.checkins.length; },

            toggleScanner: function() {
                if (this.scannerActive) {
                    this.stopScanner();
                } else {
                    this.startScanner();
                }
            },

            startScanner: function() {
                var self = this;
                var readerEl = document.getElementById('qr-reader');
                if (!readerEl) return;
                readerEl.style.display = 'block';

                if (typeof Html5Qrcode === 'undefined') {
                    self.result = true;
                    self.errorState = true;
                    self.message = 'QR scanner library not loaded.';
                    return;
                }

                self.scanner = new Html5Qrcode('qr-reader');
                self.scanner.start(
                    { facingMode: 'environment' },
                    { fps: 10, qrbox: { width: 250, height: 250 } },
                    function(decodedText) {
                        self.handleScan(decodedText);
                    },
                    function() {}
                ).then(function() {
                    self.scannerActive = true;
                }).catch(function(err) {
                    self.result = true;
                    self.errorState = true;
                    self.message = 'Could not start camera: ' + err;
                    readerEl.style.display = 'none';
                });
            },

            stopScanner: function() {
                var self = this;
                if (self.scanner) {
                    self.scanner.stop().then(function() {
                        self.scannerActive = false;
                        var readerEl = document.getElementById('qr-reader');
                        if (readerEl) readerEl.style.display = 'none';
                    }).catch(function() {
                        self.scannerActive = false;
                    });
                }
            },

            handleScan: function(url) {
                var self = this;
                // Pause scanning while processing
                if (self.scanner) {
                    self.scanner.pause(true);
                }

                // Extract check-in URL from QR code
                // URL format: https://host/api/events/checkin/{reg_id}/{token}/
                fetch(url, { method: 'POST' })
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        self.result = true;
                        if (data.success) {
                            self.success = true;
                            self.errorState = false;
                            self.message = data.message;
                            if (!data.already_checked_in) {
                                self.checkins.unshift({
                                    name: data.attendee_name,
                                    time: new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
                                });
                                // Update counter
                                var counter = document.getElementById('attended-count');
                                if (counter) {
                                    counter.textContent = parseInt(counter.textContent) + 1;
                                }
                            }
                        } else {
                            self.success = false;
                            self.errorState = true;
                            self.message = data.error || 'Check-in failed.';
                        }
                        // Resume scanning after a brief delay
                        setTimeout(function() {
                            if (self.scanner && self.scannerActive) {
                                try { self.scanner.resume(); } catch(e) {}
                            }
                        }, 2000);
                    })
                    .catch(function(err) {
                        self.result = true;
                        self.success = false;
                        self.errorState = true;
                        self.message = 'Network error or invalid QR code.';
                        setTimeout(function() {
                            if (self.scanner && self.scannerActive) {
                                try { self.scanner.resume(); } catch(e) {}
                            }
                        }, 2000);
                    });
            }
        };
    });

    // Calendar dropdown component
    Alpine.data('calendarDropdown', function() {
        return {
            open: false,

            get isOpen() { return this.open; },
            get isClosed() { return !this.open; },

            toggle() {
                this.open = !this.open;
            },

            close() {
                this.open = false;
            }
        };
    });

    // Navbar component with dropdowns and mobile menu
    Alpine.data('navbar', function() {
        return {
            mobileMenuOpen: false,
            coachToolsOpen: false,
            coachProfileOpen: false,
            eventsOpen: false,
            userMenuOpen: false,

            init: function() {
                // Close all dropdowns on Escape key
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape') {
                        this.closeAllDropdowns();
                    }
                });
            },

            // Computed getters for CSP compatibility (avoid inline expressions)
            get mobileMenuClosed() { return !this.mobileMenuOpen; },
            get mobileMenuAriaExpanded() { return this.mobileMenuOpen ? 'true' : 'false'; },
            get coachToolsAriaExpanded() { return this.coachToolsOpen ? 'true' : 'false'; },
            get coachProfileAriaExpanded() { return this.coachProfileOpen ? 'true' : 'false'; },
            get eventsAriaExpanded() { return this.eventsOpen ? 'true' : 'false'; },
            get userMenuAriaExpanded() { return this.userMenuOpen ? 'true' : 'false'; },

            toggleMobile: function() {
                this.mobileMenuOpen = !this.mobileMenuOpen;
            },
            toggleCoachTools: function() {
                this.coachToolsOpen = !this.coachToolsOpen;
            },
            toggleCoachProfile: function() {
                this.coachProfileOpen = !this.coachProfileOpen;
            },
            toggleEvents: function() {
                this.eventsOpen = !this.eventsOpen;
            },
            toggleUserMenu: function() {
                this.userMenuOpen = !this.userMenuOpen;
            },
            closeCoachTools: function() {
                this.coachToolsOpen = false;
            },
            closeCoachProfile: function() {
                this.coachProfileOpen = false;
            },
            closeEvents: function() {
                this.eventsOpen = false;
            },
            closeUserMenu: function() {
                this.userMenuOpen = false;
            },
            closeAllDropdowns: function() {
                this.mobileMenuOpen = false;
                this.coachToolsOpen = false;
                this.coachProfileOpen = false;
                this.eventsOpen = false;
                this.userMenuOpen = false;
            }
        };
    });

    // Profile progress dropdown for incomplete profiles in navbar
    Alpine.data('profileProgress', function() {
        return {
            isOpen: false,

            // CSP-compatible computed getters
            get isClosed() { return !this.isOpen; },
            get ariaExpanded() { return this.isOpen ? 'true' : 'false'; },

            toggle: function() {
                this.isOpen = !this.isOpen;
            },
            close: function() {
                this.isOpen = false;
            }
        };
    });

    // Dismissible alert/message component
    Alpine.data('dismissible', function() {
        return {
            show: true,
            dismiss: function() {
                this.show = false;
            }
        };
    });

    // Tab navigation component (for auth page)
    // Reads initial tab from data-initial-tab attribute
    Alpine.data('tabNav', function() {
        return {
            activeTab: 'login',

            // Computed getters for CSP compatibility
            get isLoginTab() { return this.activeTab === 'login'; },
            get isSignupTab() { return this.activeTab === 'signup'; },
            get loginTabClass() {
                return this.activeTab === 'login'
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md'
                    : 'text-gray-900 bg-white/50 hover:bg-white/80';
            },
            get signupTabClass() {
                return this.activeTab === 'signup'
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md'
                    : 'text-gray-900 bg-white/50 hover:bg-white/80';
            },

            init: function() {
                // Read initial tab from data attribute
                var initialTab = this.$el.getAttribute('data-initial-tab');
                if (initialTab) {
                    this.activeTab = initialTab;
                }
            },
            setLogin: function() {
                this.activeTab = 'login';
            },
            setSignup: function() {
                this.activeTab = 'signup';
            }
        };
    });

    // Screening dashboard row component
    Alpine.data('screeningRow', function() {
        return {
            showCompleteModal: false,
            openModal: function() {
                this.showCompleteModal = true;
            },
            closeModal: function() {
                this.showCompleteModal = false;
            }
        };
    });

    // Completed screening row component (view notes)
    Alpine.data('completedScreeningRow', function() {
        return {
            showNotesModal: false,
            openNotesModal: function() {
                this.showNotesModal = true;
            },
            closeNotesModal: function() {
                this.showNotesModal = false;
            }
        };
    });

    // Invitation row component (reject modal)
    Alpine.data('invitationRow', function() {
        return {
            showRejectModal: false,
            openRejectModal: function() {
                this.showRejectModal = true;
            },
            closeRejectModal: function() {
                this.showRejectModal = false;
            }
        };
    });

    // Email preferences component (account settings)
    // Reads initial unsubscribe state from data-unsubscribed attribute
    Alpine.data('emailPreferences', function() {
        return {
            unsubscribeAll: false,

            // Computed getter for CSP compatibility (replaces inline object expression)
            get unsubscribeAllClass() {
                return this.unsubscribeAll ? 'opacity-50 pointer-events-none' : '';
            },

            init: function() {
                var unsubscribed = this.$el.getAttribute('data-unsubscribed');
                this.unsubscribeAll = unsubscribed === 'true';
            },
            toggleUnsubscribe: function() {
                this.unsubscribeAll = !this.unsubscribeAll;
            }
        };
    });

    // Push notification preferences component (account settings)
    // Handles both enabling push and managing preferences
    // Uses event delegation for CSP compliance - no inline event handlers
    Alpine.data('pushPreferences', function() {
        return {
            subscriptions: [],
            isSupported: false,
            isSubscribed: false,
            isCurrentDeviceSubscribed: false,  // TRUE only if THIS device has push enabled
            isEnabling: false,
            isDisabling: false,
            errorMessage: '',
            permissionDenied: false,
            isLoading: true,
            currentEndpoint: null,  // For identifying "This device" by endpoint
            currentFingerprint: null,  // Stable device fingerprint (fallback for endpoint)
            endpointDetected: false,  // Flag to trigger re-render when endpoint is detected
            subscriptionHealth: {},  // Map of subscription ID -> health status
            checkingHealth: false,  // True while checking health
            // i18n strings for time formatting (loaded from data attributes in init)
            i18n: {
                neverUsed: 'Never used',
                justNow: 'Just now',
                lastActive: 'Last active:',
                minutesAgo: 'm ago',
                hoursAgo: 'h ago',
                daysAgo: 'd ago',
                monthsAgo: 'mo ago',
                checking: 'Checking...',
                checkSubscriptionHealth: 'Check Subscription Health'
            },

            // Computed getters for CSP compatibility
            get hasSubscriptions() { return this.subscriptions.length > 0; },
            get noSubscriptions() { return this.subscriptions.length === 0; },
            get canEnable() { return this.isSupported && !this.isCurrentDeviceSubscribed && !this.isEnabling && !this.permissionDenied; },
            // Show enable button if current device is NOT subscribed (allows multi-device)
            get showEnableButton() { return !this.isLoading && this.isSupported && !this.isCurrentDeviceSubscribed && !this.permissionDenied; },
            get showPermissionDenied() { return !this.isLoading && this.permissionDenied; },
            get showNotSupported() { return !this.isLoading && !this.isSupported; },
            get showPreferences() { return !this.isLoading && this.isSubscribed && this.hasSubscriptions; },
            get showLoading() { return this.isLoading; },
            // Button text getters for CSP compatibility (replaces ternary expressions)
            get enableButtonText() { return this.isEnabling ? 'Enabling...' : 'Enable Push Notifications'; },
            get disableButtonText() { return this.isDisabling ? 'Disabling...' : 'Disable'; },
            get showEnablingIcon() { return this.isEnabling; },
            get showNotEnablingIcon() { return !this.isEnabling; },
            // Health check state getters (CSP-safe)
            get notCheckingHealth() { return !this.checkingHealth; },
            get checkHealthButtonText() { return this.checkingHealth ? this.i18n.checking || 'Checking...' : this.i18n.checkSubscriptionHealth || 'Check Subscription Health'; },

            init: function() {
                var self = this;

                // Load i18n strings from data attributes
                this._loadI18nStrings();

                // Parse initial subscriptions from data attribute
                var data = this.$el.getAttribute('data-subscriptions');
                if (data) {
                    try {
                        this.subscriptions = JSON.parse(data);
                    } catch (e) {
                        console.error('[Push] Failed to parse subscriptions:', e);
                    }
                }

                // Check if push is supported directly (don't rely on CrushPush being loaded)
                // This is the same check as in push-notifications.js
                this.isSupported = 'serviceWorker' in navigator && 'PushManager' in window;

                // Check if permission was denied
                if ('Notification' in window && Notification.permission === 'denied') {
                    this.permissionDenied = true;
                }

                // Detect current device endpoint for "This device" badge
                this._detectCurrentEndpoint();

                // Wait for CrushPush to be available before checking subscription status
                this._waitForCrushPush(function() {
                    // Check current subscription status
                    if (self.isSupported && window.CrushPush && window.CrushPush.isSubscribed) {
                        window.CrushPush.isSubscribed().then(function(subscribed) {
                            self.isSubscribed = subscribed;
                            self.isLoading = false;
                            // Re-run device detection after DOM is rendered
                            self.$nextTick(function() {
                                self._retryDeviceMatch();
                            });
                        }).catch(function() {
                            self.isLoading = false;
                            self.$nextTick(function() {
                                self._retryDeviceMatch();
                            });
                        });
                    } else {
                        self.isLoading = false;
                        self.$nextTick(function() {
                            self._retryDeviceMatch();
                        });
                    }
                });

                // Event delegation for toggle changes
                this.$el.addEventListener('change', function(event) {
                    if (event.target.classList.contains('push-pref-toggle')) {
                        var subId = parseInt(event.target.dataset.subscriptionId);
                        var prefKey = event.target.dataset.prefKey;
                        self.updatePreference(subId, prefKey, event.target.checked, event.target);
                    }
                });

                // Event delegation for button clicks
                this.$el.addEventListener('click', function(event) {
                    if (event.target.closest('.enable-push-btn')) {
                        self.enablePush();
                    } else if (event.target.closest('.disable-push-btn')) {
                        // Get subscription info from the button's container
                        var btn = event.target.closest('.disable-push-btn');
                        var container = btn.closest('[data-subscription-id]');
                        if (container) {
                            var subscriptionId = container.dataset.subscriptionId;
                            var endpoint = container.dataset.endpoint;
                            // Check if this is the current device
                            if (self.currentEndpoint && endpoint === self.currentEndpoint) {
                                self.disablePush();  // Current device - use existing unsubscribe
                            } else {
                                self.disableRemoteSubscription(subscriptionId);  // Other device
                            }
                        } else {
                            // Fallback to current device unsubscribe
                            self.disablePush();
                        }
                    }
                });
            },

            // Load i18n strings from data attributes
            _loadI18nStrings: function() {
                var el = this.$el;
                this.i18n.neverUsed = el.getAttribute('data-i18n-never-used') || this.i18n.neverUsed;
                this.i18n.justNow = el.getAttribute('data-i18n-just-now') || this.i18n.justNow;
                this.i18n.lastActive = el.getAttribute('data-i18n-last-active') || this.i18n.lastActive;
                this.i18n.minutesAgo = el.getAttribute('data-i18n-minutes-ago') || this.i18n.minutesAgo;
                this.i18n.hoursAgo = el.getAttribute('data-i18n-hours-ago') || this.i18n.hoursAgo;
                this.i18n.daysAgo = el.getAttribute('data-i18n-days-ago') || this.i18n.daysAgo;
                this.i18n.monthsAgo = el.getAttribute('data-i18n-months-ago') || this.i18n.monthsAgo;
                this.i18n.checking = el.getAttribute('data-i18n-checking') || this.i18n.checking;
                this.i18n.checkSubscriptionHealth = el.getAttribute('data-i18n-check-subscription-health') || this.i18n.checkSubscriptionHealth;
            },

            // Detect current device's push endpoint and fingerprint for "This device" identification
            // Uses endpoint as primary identifier, fingerprint as fallback
            // Sets isCurrentDeviceSubscribed based on whether THIS device is in the subscriptions list
            _detectCurrentEndpoint: function() {
                var self = this;

                // Generate fingerprint immediately using our own implementation
                // This ensures fingerprint is always available, regardless of CrushPush loading
                self.currentFingerprint = self._generateFingerprint();

                if ('serviceWorker' in navigator) {
                    navigator.serviceWorker.ready.then(function(reg) {
                        return reg.pushManager.getSubscription();
                    }).then(function(sub) {
                        self.currentEndpoint = sub ? sub.endpoint : null;
                        self.endpointDetected = true;  // Trigger Alpine reactivity

                        // Strategy 1: Match by endpoint (most accurate when available)
                        if (sub && sub.endpoint) {
                            var subscriptionElements = self.$el.querySelectorAll('[data-endpoint]');
                            for (var i = 0; i < subscriptionElements.length; i++) {
                                if (subscriptionElements[i].dataset.endpoint === sub.endpoint) {
                                    self.isCurrentDeviceSubscribed = true;
                                    self._showThisDeviceBadge(subscriptionElements[i]);
                                    break;
                                }
                            }
                        }

                        // Strategy 2: Fallback to fingerprint if endpoint didn't match
                        if (!self.isCurrentDeviceSubscribed && self.currentFingerprint) {
                            self._matchByFingerprint();
                        }
                    }).catch(function() {
                        self.endpointDetected = true;  // Mark as done even on failure
                        // Try fingerprint matching as fallback
                        if (self.currentFingerprint) {
                            self._matchByFingerprint();
                        }
                    });
                } else {
                    self.endpointDetected = true;  // No service worker support
                    // Still try fingerprint matching
                    if (self.currentFingerprint) {
                        self._matchByFingerprint();
                    }
                }
            },

            // Match device by fingerprint (fallback when endpoint doesn't match)
            _matchByFingerprint: function() {
                var self = this;
                var subscriptionElements = self.$el.querySelectorAll('[data-fingerprint]');
                for (var i = 0; i < subscriptionElements.length; i++) {
                    if (subscriptionElements[i].dataset.fingerprint === self.currentFingerprint) {
                        self.isCurrentDeviceSubscribed = true;
                        self._showThisDeviceBadge(subscriptionElements[i]);
                        break;
                    }
                }
            },

            // Retry device matching after DOM is rendered (called after isLoading becomes false)
            // This handles the race condition where _detectCurrentEndpoint runs before
            // the subscription elements are rendered by Alpine's x-if
            _retryDeviceMatch: function() {
                var self = this;
                if (self.isCurrentDeviceSubscribed) return;  // Already matched

                // Try endpoint matching first
                if (self.currentEndpoint) {
                    var subscriptionElements = self.$el.querySelectorAll('[data-endpoint]');
                    for (var i = 0; i < subscriptionElements.length; i++) {
                        if (subscriptionElements[i].dataset.endpoint === self.currentEndpoint) {
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
            _showThisDeviceBadge: function(container) {
                var badges = container.querySelectorAll('.this-device-badge');
                for (var i = 0; i < badges.length; i++) {
                    badges[i].classList.remove('hidden');
                }
            },

            // Generate device fingerprint independently (same algorithm as push-notifications.js)
            // This ensures fingerprint is always available even if CrushPush fails to load
            _generateFingerprint: function() {
                var components = [
                    screen.width,
                    screen.height,
                    window.devicePixelRatio || 1,
                    new Date().getTimezoneOffset(),
                    navigator.language || '',
                    navigator.platform || '',
                    navigator.hardwareConcurrency || 0,
                    navigator.deviceMemory || 0,
                    ('ontouchstart' in window) ? 1 : 0,
                    screen.colorDepth || 0,
                    this._getCanvasFingerprint()
                ];
                return this._simpleHash(components.join('|'));
            },

            // Canvas-based fingerprint component (same as push-notifications.js)
            _getCanvasFingerprint: function() {
                try {
                    var canvas = document.createElement('canvas');
                    canvas.width = 200;
                    canvas.height = 50;
                    var ctx = canvas.getContext('2d');
                    ctx.textBaseline = 'top';
                    ctx.font = '14px Arial';
                    ctx.fillStyle = '#f60';
                    ctx.fillRect(125, 1, 62, 20);
                    ctx.fillStyle = '#069';
                    ctx.fillText('Crush.lu PWA', 2, 15);
                    ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
                    ctx.fillText('Crush.lu PWA', 4, 17);
                    return canvas.toDataURL().slice(-50);
                } catch (e) {
                    return 'no-canvas';
                }
            },

            // Simple hash function (djb2 algorithm, same as push-notifications.js)
            _simpleHash: function(str) {
                var hash = 5381;
                for (var i = 0; i < str.length; i++) {
                    hash = ((hash << 5) + hash) + str.charCodeAt(i);
                    hash = hash & hash;
                }
                return Math.abs(hash).toString(16).padStart(8, '0');
            },

            // Check if a subscription is from the current device
            isCurrentDevice: function(subscription) {
                // Check by endpoint first, then fingerprint
                if (this.currentEndpoint && subscription.endpoint === this.currentEndpoint) {
                    return true;
                }
                if (this.currentFingerprint && subscription.device_fingerprint === this.currentFingerprint) {
                    return true;
                }
                return false;
            },

            // Format relative time for "Last active" display
            formatRelativeTime: function(dateStr) {
                if (!dateStr) return this.i18n.neverUsed;
                var date = new Date(dateStr);
                var now = new Date();
                var diffMs = now - date;
                var diffMins = Math.floor(diffMs / 60000);
                if (diffMins < 1) return this.i18n.justNow;
                if (diffMins < 60) return diffMins + ' ' + this.i18n.minutesAgo;
                var diffHours = Math.floor(diffMins / 60);
                if (diffHours < 24) return diffHours + ' ' + this.i18n.hoursAgo;
                var diffDays = Math.floor(diffHours / 24);
                if (diffDays < 30) return diffDays + ' ' + this.i18n.daysAgo;
                var diffMonths = Math.floor(diffDays / 30);
                return diffMonths + ' ' + this.i18n.monthsAgo;
            },

            enablePush: function() {
                var self = this;
                if (!this.isSupported || this.isEnabling) return;

                this.isEnabling = true;
                this.errorMessage = '';

                window.CrushPush.subscribe().then(function(result) {
                    self.isEnabling = false;
                    if (result.success) {
                        self.isSubscribed = true;
                        // Reload page to get fresh subscription data
                        window.location.reload();
                    } else {
                        if (result.error === 'Permission denied') {
                            self.permissionDenied = true;
                        } else {
                            self.errorMessage = result.error || 'Failed to enable notifications';
                        }
                    }
                }).catch(function(err) {
                    self.isEnabling = false;
                    self.errorMessage = 'An error occurred. Please try again.';
                    console.error('[Push] Enable error:', err);
                });
            },

            disablePush: function() {
                var self = this;
                if (!this.isSupported || this.isDisabling) return;

                this.isDisabling = true;

                window.CrushPush.unsubscribe().then(function(result) {
                    self.isDisabling = false;
                    if (result.success) {
                        self.isSubscribed = false;
                        self.isCurrentDeviceSubscribed = false;
                        self.subscriptions = [];
                        // Reload to update UI
                        window.location.reload();
                    } else {
                        self.errorMessage = result.error || 'Failed to disable notifications';
                    }
                }).catch(function(err) {
                    self.isDisabling = false;
                    self.errorMessage = 'An error occurred. Please try again.';
                    console.error('[Push] Disable error:', err);
                });
            },

            // Disable push subscription for a remote device (not the current device)
            disableRemoteSubscription: function(subscriptionId) {
                var self = this;
                if (this.isDisabling) return;

                this.isDisabling = true;

                fetch('/api/push/delete-subscription/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': self.getCsrfToken()
                    },
                    body: JSON.stringify({ subscription_id: subscriptionId })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    self.isDisabling = false;
                    if (data.success) {
                        // Reload to update UI
                        window.location.reload();
                    } else {
                        self.errorMessage = data.error || 'Failed to disable notifications';
                    }
                })
                .catch(function(err) {
                    self.isDisabling = false;
                    self.errorMessage = 'An error occurred. Please try again.';
                    console.error('[Push] Remote disable error:', err);
                });
            },

            updatePreference: function(subscriptionId, prefKey, value, checkbox) {
                var self = this;
                var preferences = {};
                preferences[prefKey] = value;

                fetch('/api/push/preferences/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': self.getCsrfToken()
                    },
                    body: JSON.stringify({
                        subscriptionId: subscriptionId,
                        preferences: preferences
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (!data.success) {
                        // Revert checkbox on error
                        checkbox.checked = !value;
                        console.error('[Push] Failed to update preference:', data.error);
                    }
                })
                .catch(function(err) {
                    // Revert checkbox on network error
                    checkbox.checked = !value;
                    console.error('[Push] Network error:', err);
                });
            },

            getCsrfToken: function() {
                // First try hidden form input (works with CSRF_COOKIE_HTTPONLY=True)
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                // Fallback to cookie
                var cookie = document.cookie.split('; ')
                    .find(function(row) { return row.startsWith('csrftoken='); });
                return cookie ? cookie.split('=')[1] : '';
            },

            // Wait for CrushPush to be available (handles script load timing)
            _waitForCrushPush: function(callback) {
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
            checkAllSubscriptionsHealth: function() {
                var self = this;
                self.checkingHealth = true;

                // Initialize subscriptionHealth if not exists
                if (!self.subscriptionHealth) {
                    self.subscriptionHealth = {};
                }

                var promises = self.subscriptions.map(function(sub) {
                    return fetch('/api/push/validate-subscription/', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': self.getCsrfToken()
                        },
                        body: JSON.stringify({ endpoint: sub.endpoint })
                    })
                    .then(function(response) {
                        if (response.ok) {
                            return response.json();
                        }
                        throw new Error('Health check failed');
                    })
                    .then(function(data) {
                        self.subscriptionHealth[sub.id] = {
                            valid: data.valid,
                            warning: data.warning,
                            reason: data.reason,
                            age_days: data.age_days
                        };
                    })
                    .catch(function(error) {
                        console.error('Health check failed for subscription', sub.id, error);
                        self.subscriptionHealth[sub.id] = {
                            valid: false,
                            reason: 'check_failed'
                        };
                    });
                });

                Promise.all(promises).then(function() {
                    self.checkingHealth = false;
                    // Force Alpine to update
                    self.$nextTick(function() {
                        // Trigger reactivity
                    });
                });
            },

            // Refresh a specific subscription
            refreshSubscription: function(subscriptionId) {
                var self = this;
                var confirmed = confirm('Refresh this push notification subscription? You may need to grant permission again.');
                if (!confirmed) return;

                // Use window.CrushPushNotifications.refresh from push-notifications.js
                if (window.CrushPushNotifications && window.CrushPushNotifications.refresh) {
                    window.CrushPushNotifications.refresh()
                        .then(function(success) {
                            if (success) {
                                // Show success and reload
                                alert('Subscription refreshed successfully');
                                window.location.reload();
                            } else {
                                alert('Failed to refresh subscription');
                            }
                        })
                        .catch(function(error) {
                            console.error('Error refreshing:', error);
                            alert('Error refreshing subscription');
                        });
                } else {
                    alert('Push notification system not loaded. Please refresh the page and try again.');
                }
            },

            // Delete a subscription by ID
            deleteSubscription: function(subscriptionId) {
                var self = this;
                var confirmed = confirm('Remove this subscription? You will stop receiving notifications on this device.');
                if (!confirmed) return;

                self.disableRemoteSubscription(subscriptionId);
            },

            // CSP-safe helper: Check if health status exists for subscription
            hasHealthStatus: function(subscriptionId) {
                return !!this.subscriptionHealth[subscriptionId];
            },

            // CSP-safe helper: Check if subscription is healthy
            isHealthy: function(subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                return health && health.valid && !health.warning;
            },

            // CSP-safe helper: Check if subscription has old_subscription warning
            isOldSubscription: function(subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                return health && health.warning === 'old_subscription';
            },

            // CSP-safe helper: Get age text for old subscription
            getAgeText: function(subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                var ageDays = health && health.age_days ? health.age_days : 0;
                return 'Subscription is ' + ageDays + ' days old';
            },

            // CSP-safe helper: Check if subscription has high failure count
            hasHighFailureCount: function(subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                return health && health.valid === false && health.reason === 'high_failure_count';
            },

            // CSP-safe helper: Check if subscription is not found
            isNotFound: function(subscriptionId) {
                var health = this.subscriptionHealth[subscriptionId];
                return health && health.valid === false && health.reason === 'not_found';
            }
        };
    });

    // CSP-safe wrapper component for individual subscription health status
    // Creates computed properties for a specific subscription ID
    Alpine.data('subscriptionHealthStatus', function(subscriptionId) {
        return {
            subscriptionId: subscriptionId,

            // Get the parent pushPreferences component
            get parentComponent() {
                return this.$root;
            },

            // CSP-safe computed properties
            get hasStatus() {
                var parent = this.parentComponent;
                return parent.subscriptionHealth && !!parent.subscriptionHealth[this.subscriptionId];
            },

            get healthyStatus() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth ? parent.subscriptionHealth[this.subscriptionId] : null;
                return health && health.valid && !health.warning;
            },

            get oldStatus() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth ? parent.subscriptionHealth[this.subscriptionId] : null;
                return health && health.warning === 'old_subscription';
            },

            get ageText() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth ? parent.subscriptionHealth[this.subscriptionId] : null;
                var ageDays = health && health.age_days ? health.age_days : 0;
                return 'Subscription is ' + ageDays + ' days old';
            },

            get failureStatus() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth ? parent.subscriptionHealth[this.subscriptionId] : null;
                return health && health.valid === false && health.reason === 'high_failure_count';
            },

            get notFoundStatus() {
                var parent = this.parentComponent;
                var health = parent.subscriptionHealth ? parent.subscriptionHealth[this.subscriptionId] : null;
                return health && health.valid === false && health.reason === 'not_found';
            },

            // Pass-through getters for parent properties (for disable button)
            get isDisabling() {
                var parent = this.parentComponent;
                return parent.isDisabling || false;
            },

            get disableButtonText() {
                var parent = this.parentComponent;
                return parent.disableButtonText || 'Disable Notifications';
            },

            // CSP-safe methods
            refreshSubscriptionById: function() {
                var parent = this.parentComponent;
                if (parent.refreshSubscription) {
                    parent.refreshSubscription(this.subscriptionId);
                }
            },

            deleteSubscriptionById: function() {
                var parent = this.parentComponent;
                if (parent.deleteSubscription) {
                    parent.deleteSubscription(this.subscriptionId);
                }
            }
        };
    });

    // Coach push notification preferences component (account settings and coach dashboard)
    // Separate from user push preferences - completely independent system
    Alpine.data('coachPushPreferences', function() {
        return {
            subscriptions: [],
            isSupported: false,
            isSubscribed: false,
            isCurrentDeviceSubscribed: false,  // TRUE only if THIS device has push enabled
            isEnabling: false,
            isDisabling: false,
            errorMessage: '',
            permissionDenied: false,
            isLoading: true,
            currentEndpoint: null,  // For identifying "This device" by endpoint
            currentFingerprint: null,  // Stable device fingerprint (fallback for endpoint)
            endpointDetected: false,  // Flag to trigger re-render when endpoint is detected
            // i18n strings for time formatting (loaded from data attributes in init)
            i18n: {
                neverUsed: 'Never used',
                justNow: 'Just now',
                lastActive: 'Last active:',
                minutesAgo: 'm ago',
                hoursAgo: 'h ago',
                daysAgo: 'd ago',
                monthsAgo: 'mo ago',
                checking: 'Checking...',
                checkSubscriptionHealth: 'Check Subscription Health'
            },

            get hasSubscriptions() { return this.subscriptions.length > 0; },
            // Show enable button only if THIS device isn't subscribed (allows multi-device)
            get showEnableButton() { return !this.isLoading && this.isSupported && !this.isCurrentDeviceSubscribed && !this.permissionDenied; },
            get showPermissionDenied() { return !this.isLoading && this.permissionDenied; },
            get showNotSupported() { return !this.isLoading && !this.isSupported; },
            get showPreferences() { return !this.isLoading && this.isSubscribed && this.hasSubscriptions; },
            get showLoading() { return this.isLoading; },
            get showEnablingSpinner() { return this.isEnabling; },
            get showNotEnablingIcon() { return !this.isEnabling; },
            get showTestSpinner() { return this.isSendingTest; },
            get enableButtonText() { return this.isEnabling ? 'Enabling...' : 'Enable Notifications'; },
            get testButtonText() { return this.isSendingTest ? 'Sending...' : 'Send Test'; },
            get disableButtonText() { return this.isDisabling ? 'Disabling...' : 'Disable Notifications'; },
            isSendingTest: false,

            init: function() {
                var self = this;

                // Load i18n strings from data attributes
                this._loadI18nStrings();

                var data = this.$el.getAttribute('data-subscriptions');
                if (data) {
                    try {
                        this.subscriptions = JSON.parse(data);
                    } catch (e) {
                        console.error('[CoachPush] Failed to parse subscriptions:', e);
                    }
                }

                this.isSupported = 'serviceWorker' in navigator && 'PushManager' in window;
                if ('Notification' in window && Notification.permission === 'denied') {
                    this.permissionDenied = true;
                }

                // Detect current device endpoint for "This device" badge
                this._detectCurrentEndpoint();

                this._waitForServiceWorker(function() {
                    if (self.isSupported && self.subscriptions.length > 0) {
                        self.isSubscribed = true;
                    }
                    self.isLoading = false;
                    // Re-run device detection after DOM is rendered
                    self.$nextTick(function() {
                        self._retryDeviceMatch();
                    });
                });

                this.$el.addEventListener('change', function(event) {
                    if (event.target.classList.contains('coach-push-pref-toggle')) {
                        var subId = parseInt(event.target.dataset.subscriptionId);
                        var prefKey = event.target.dataset.prefKey;
                        self.updatePreference(subId, prefKey, event.target.checked, event.target);
                    }
                });

                this.$el.addEventListener('click', function(event) {
                    if (event.target.closest('.enable-coach-push-btn')) {
                        self.enablePush();
                    } else if (event.target.closest('.disable-coach-push-btn')) {
                        // Identify which subscription to disable
                        var btn = event.target.closest('.disable-coach-push-btn');
                        var container = btn.closest('[data-subscription-id]');
                        if (container) {
                            var subscriptionId = parseInt(container.dataset.subscriptionId);
                            var endpoint = container.dataset.endpoint;
                            // Check if this is the current device
                            if (self.currentEndpoint && endpoint === self.currentEndpoint) {
                                self.disablePush();  // Current device - use existing unsubscribe
                            } else {
                                self.disableRemoteSubscription(subscriptionId);  // Other device
                            }
                        } else {
                            self.disablePush();  // Fallback to current device
                        }
                    } else if (event.target.closest('.test-coach-push-btn')) {
                        self.sendTestNotification();
                    }
                });
            },

            // Load i18n strings from data attributes
            _loadI18nStrings: function() {
                var el = this.$el;
                this.i18n.neverUsed = el.getAttribute('data-i18n-never-used') || this.i18n.neverUsed;
                this.i18n.justNow = el.getAttribute('data-i18n-just-now') || this.i18n.justNow;
                this.i18n.lastActive = el.getAttribute('data-i18n-last-active') || this.i18n.lastActive;
                this.i18n.minutesAgo = el.getAttribute('data-i18n-minutes-ago') || this.i18n.minutesAgo;
                this.i18n.hoursAgo = el.getAttribute('data-i18n-hours-ago') || this.i18n.hoursAgo;
                this.i18n.daysAgo = el.getAttribute('data-i18n-days-ago') || this.i18n.daysAgo;
                this.i18n.monthsAgo = el.getAttribute('data-i18n-months-ago') || this.i18n.monthsAgo;
                this.i18n.checking = el.getAttribute('data-i18n-checking') || this.i18n.checking;
                this.i18n.checkSubscriptionHealth = el.getAttribute('data-i18n-check-subscription-health') || this.i18n.checkSubscriptionHealth;
            },

            // Detect current device's push endpoint and fingerprint for "This device" identification
            // Uses endpoint as primary identifier, fingerprint as fallback
            // Sets isCurrentDeviceSubscribed to true if this device has an active subscription
            _detectCurrentEndpoint: function() {
                var self = this;

                // Generate fingerprint immediately using our own implementation
                // No dependency on CrushPush - works independently
                self.currentFingerprint = self._generateFingerprint();

                if ('serviceWorker' in navigator) {
                    navigator.serviceWorker.ready.then(function(reg) {
                        return reg.pushManager.getSubscription();
                    }).then(function(sub) {
                        self.currentEndpoint = sub ? sub.endpoint : null;
                        self.endpointDetected = true;  // Trigger Alpine reactivity

                        // Strategy 1: Match by endpoint (most accurate when available)
                        if (sub && sub.endpoint) {
                            var subscriptionElements = self.$el.querySelectorAll('[data-endpoint]');
                            for (var i = 0; i < subscriptionElements.length; i++) {
                                if (subscriptionElements[i].dataset.endpoint === sub.endpoint) {
                                    self.isCurrentDeviceSubscribed = true;
                                    self._showThisDeviceBadge(subscriptionElements[i]);
                                    break;
                                }
                            }
                        }

                        // Strategy 2: Fallback to fingerprint if endpoint didn't match
                        if (!self.isCurrentDeviceSubscribed && self.currentFingerprint) {
                            self._matchByFingerprint();
                        }
                    }).catch(function() {
                        self.endpointDetected = true;  // Mark as done even on failure
                        // Try fingerprint matching as fallback
                        if (self.currentFingerprint) {
                            self._matchByFingerprint();
                        }
                    });
                } else {
                    self.endpointDetected = true;  // No service worker support
                    // Still try fingerprint matching
                    if (self.currentFingerprint) {
                        self._matchByFingerprint();
                    }
                }
            },

            // Match device by fingerprint (fallback when endpoint doesn't match)
            _matchByFingerprint: function() {
                var self = this;
                var subscriptionElements = self.$el.querySelectorAll('[data-fingerprint]');
                for (var i = 0; i < subscriptionElements.length; i++) {
                    if (subscriptionElements[i].dataset.fingerprint === self.currentFingerprint) {
                        self.isCurrentDeviceSubscribed = true;
                        self._showThisDeviceBadge(subscriptionElements[i]);
                        break;
                    }
                }
            },

            // Retry device matching after DOM is rendered (called after isLoading becomes false)
            // This handles the race condition where _detectCurrentEndpoint runs before
            // the subscription elements are rendered by Alpine's x-if
            _retryDeviceMatch: function() {
                var self = this;
                if (self.isCurrentDeviceSubscribed) return;  // Already matched

                // Try endpoint matching first
                if (self.currentEndpoint) {
                    var subscriptionElements = self.$el.querySelectorAll('[data-endpoint]');
                    for (var i = 0; i < subscriptionElements.length; i++) {
                        if (subscriptionElements[i].dataset.endpoint === self.currentEndpoint) {
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
            _showThisDeviceBadge: function(container) {
                var badges = container.querySelectorAll('.this-device-badge');
                for (var i = 0; i < badges.length; i++) {
                    badges[i].classList.remove('hidden');
                }
            },

            // Check if a subscription is from the current device
            isCurrentDevice: function(subscription) {
                // Check by endpoint first, then fingerprint
                if (this.currentEndpoint && subscription.endpoint === this.currentEndpoint) {
                    return true;
                }
                if (this.currentFingerprint && subscription.device_fingerprint === this.currentFingerprint) {
                    return true;
                }
                return false;
            },

            // Format relative time for "Last active" display
            formatRelativeTime: function(dateStr) {
                if (!dateStr) return this.i18n.neverUsed;
                var date = new Date(dateStr);
                var now = new Date();
                var diffMs = now - date;
                var diffMins = Math.floor(diffMs / 60000);
                if (diffMins < 1) return this.i18n.justNow;
                if (diffMins < 60) return diffMins + ' ' + this.i18n.minutesAgo;
                var diffHours = Math.floor(diffMins / 60);
                if (diffHours < 24) return diffHours + ' ' + this.i18n.hoursAgo;
                var diffDays = Math.floor(diffHours / 24);
                if (diffDays < 30) return diffDays + ' ' + this.i18n.daysAgo;
                var diffMonths = Math.floor(diffDays / 30);
                return diffMonths + ' ' + this.i18n.monthsAgo;
            },

            enablePush: function() {
                var self = this;
                if (!this.isSupported || this.isEnabling) return;
                this.isEnabling = true;
                this.errorMessage = '';

                Notification.requestPermission().then(function(permission) {
                    if (permission !== 'granted') {
                        self.isEnabling = false;
                        self.permissionDenied = true;
                        return;
                    }
                    navigator.serviceWorker.ready.then(function(registration) {
                        fetch('/api/coach/push/vapid-public-key/')
                            .then(function(r) { return r.json(); })
                            .then(function(data) {
                                if (!data.success) throw new Error(data.error);
                                return registration.pushManager.subscribe({
                                    userVisibleOnly: true,
                                    applicationServerKey: self._urlBase64ToUint8Array(data.publicKey)
                                });
                            })
                            .then(function(subscription) {
                                // Get fingerprint for stable device identification
                                // Use our own implementation - no dependency on CrushPush
                                var fingerprint = self._generateFingerprint();
                                return fetch('/api/coach/push/subscribe/', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': self.getCsrfToken() },
                                    body: JSON.stringify({
                                        endpoint: subscription.endpoint,
                                        keys: {
                                            p256dh: btoa(String.fromCharCode.apply(null, new Uint8Array(subscription.getKey('p256dh')))),
                                            auth: btoa(String.fromCharCode.apply(null, new Uint8Array(subscription.getKey('auth'))))
                                        },
                                        userAgent: navigator.userAgent,
                                        deviceName: self._getDeviceName(),
                                        deviceFingerprint: fingerprint
                                    })
                                });
                            })
                            .then(function(r) { return r.json(); })
                            .then(function(data) {
                                self.isEnabling = false;
                                if (data.success) { self.isSubscribed = true; window.location.reload(); }
                                else { self.errorMessage = data.error || 'Failed to enable'; }
                            })
                            .catch(function(err) { self.isEnabling = false; self.errorMessage = 'Error occurred'; console.error('[CoachPush]', err); });
                    });
                }).catch(function() { self.isEnabling = false; self.errorMessage = 'Permission denied'; });
            },

            disablePush: function() {
                var self = this;
                if (!this.isSupported || this.isDisabling) return;
                this.isDisabling = true;

                navigator.serviceWorker.ready.then(function(reg) { return reg.pushManager.getSubscription(); })
                .then(function(sub) {
                    if (!sub) { self.isDisabling = false; self.isSubscribed = false; self.isCurrentDeviceSubscribed = false; window.location.reload(); return; }
                    // Call API first to check if browser subscription should be kept
                    // Include fingerprint so server can check for user subscriptions with different endpoint
                    // Use our own implementation - no dependency on CrushPush
                    var fingerprint = self._generateFingerprint();
                    return fetch('/api/coach/push/unsubscribe/', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': self.getCsrfToken() },
                        body: JSON.stringify({ endpoint: sub.endpoint, deviceFingerprint: fingerprint })
                    })
                    .then(function(response) { return response.json(); })
                    .then(function(data) {
                        if (data.success) {
                            // Only unsubscribe browser if no other system needs it
                            if (!data.keep_browser_subscription) {
                                return sub.unsubscribe();
                            }
                        }
                    })
                    .then(function() { self.isDisabling = false; self.isCurrentDeviceSubscribed = false; window.location.reload(); });
                })
                .catch(function(err) { self.isDisabling = false; self.errorMessage = 'Failed'; console.error('[CoachPush]', err); });
            },

            // Disable push subscription for a remote device (not the current device)
            disableRemoteSubscription: function(subscriptionId) {
                var self = this;
                if (this.isDisabling) return;

                this.isDisabling = true;

                fetch('/api/coach/push/delete-subscription/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': self.getCsrfToken()
                    },
                    body: JSON.stringify({ subscription_id: subscriptionId })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    self.isDisabling = false;
                    if (data.success) {
                        // Reload to update UI
                        window.location.reload();
                    } else {
                        self.errorMessage = data.error || 'Failed to disable notifications';
                    }
                })
                .catch(function(err) {
                    self.isDisabling = false;
                    self.errorMessage = 'An error occurred. Please try again.';
                    console.error('[CoachPush] Remote disable error:', err);
                });
            },

            sendTestNotification: function() {
                var self = this;
                if (this.isSendingTest) return;
                this.isSendingTest = true;
                fetch('/api/coach/push/test/', { method: 'POST', headers: { 'X-CSRFToken': self.getCsrfToken() } })
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    self.isSendingTest = false;
                })
                .catch(function(err) {
                    self.isSendingTest = false;
                    console.error('[CoachPush] Test failed:', err);
                });
            },

            updatePreference: function(subId, prefKey, value, checkbox) {
                var self = this;
                var prefs = {}; prefs[prefKey] = value;
                fetch('/api/coach/push/preferences/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': self.getCsrfToken() },
                    body: JSON.stringify({ subscriptionId: subId, preferences: prefs })
                }).then(function(r) { return r.json(); })
                .then(function(data) { if (!data.success) checkbox.checked = !value; })
                .catch(function() { checkbox.checked = !value; });
            },

            getCsrfToken: function() {
                // First try hidden form input (works with CSRF_COOKIE_HTTPONLY=True)
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) return input.value;
                // Fallback to cookie
                var c = document.cookie.split('; ').find(function(r) { return r.startsWith('csrftoken='); });
                return c ? c.split('=')[1] : '';
            },

            _waitForServiceWorker: function(cb) {
                var self = this;
                if ('serviceWorker' in navigator) {
                    navigator.serviceWorker.ready.then(function() {
                        cb();
                    }).catch(function() {
                        self.isLoading = false;
                        cb();
                    });
                } else {
                    self.isLoading = false;
                    cb();
                }
            },

            _urlBase64ToUint8Array: function(base64) {
                var padding = '='.repeat((4 - base64.length % 4) % 4);
                var b64 = (base64 + padding).replace(/\-/g, '+').replace(/_/g, '/');
                var raw = window.atob(b64);
                var out = new Uint8Array(raw.length);
                for (var i = 0; i < raw.length; ++i) out[i] = raw.charCodeAt(i);
                return out;
            },

            _getDeviceName: function() {
                var ua = navigator.userAgent;
                if (/Android/i.test(ua)) return 'Android Chrome';
                if (/iPhone|iPad|iPod/i.test(ua)) return 'iPhone Safari';
                if (/Windows/i.test(ua)) return 'Windows Desktop';
                if (/Macintosh/i.test(ua)) return 'Mac Desktop';
                if (/Linux/i.test(ua)) return 'Linux Desktop';
                return 'Unknown Device';
            },

            // Generate a stable browser fingerprint from hardware/software characteristics
            // Independent implementation - doesn't require CrushPush to be loaded
            _generateFingerprint: function() {
                var components = [
                    screen.width,
                    screen.height,
                    window.devicePixelRatio || 1,
                    new Date().getTimezoneOffset(),
                    navigator.language || '',
                    navigator.platform || '',
                    navigator.hardwareConcurrency || 0,
                    navigator.deviceMemory || 0,
                    ('ontouchstart' in window) ? 1 : 0,
                    screen.colorDepth || 0,
                    this._getCanvasFingerprint()
                ];
                return this._simpleHash(components.join('|'));
            },

            _getCanvasFingerprint: function() {
                try {
                    var canvas = document.createElement('canvas');
                    canvas.width = 200;
                    canvas.height = 50;
                    var ctx = canvas.getContext('2d');
                    ctx.textBaseline = 'top';
                    ctx.font = '14px Arial';
                    ctx.fillStyle = '#f60';
                    ctx.fillRect(125, 1, 62, 20);
                    ctx.fillStyle = '#069';
                    ctx.fillText('Crush.lu PWA', 2, 15);
                    ctx.fillStyle = 'rgba(102, 204, 0, 0.7)';
                    ctx.fillText('Crush.lu PWA', 4, 17);
                    return canvas.toDataURL().slice(-50);
                } catch (e) {
                    return 'no-canvas';
                }
            },

            _simpleHash: function(str) {
                var hash = 5381;
                for (var i = 0; i < str.length; i++) {
                    hash = ((hash << 5) + hash) + str.charCodeAt(i);
                    hash = hash & hash;
                }
                return Math.abs(hash).toString(16).padStart(8, '0');
            }
        };
    });

    // Decline animation component (connection response)
    // Shows briefly then fades out
    Alpine.data('declineAnimation', function() {
        return {
            show: true,
            init: function() {
                var self = this;
                setTimeout(function() {
                    self.show = false;
                }, 2000);
            }
        };
    });

    // Character counter component
    // Reads initial count from data-initial-count and max from data-max-length
    Alpine.data('charCounter', function() {
        return {
            charCount: 0,
            maxLength: 500,
            init: function() {
                var initialCount = this.$el.getAttribute('data-initial-count');
                var maxLength = this.$el.getAttribute('data-max-length');
                this.charCount = initialCount ? parseInt(initialCount) : 0;
                this.maxLength = maxLength ? parseInt(maxLength) : 500;
            },
            updateCount: function(event) {
                this.charCount = event.target.value.length;
            }
        };
    });

    // Photo upload component for profile photos
    // Reads initial photos from data attributes
    Alpine.data('photoUpload', function() {
        return {
            photos: [
                { id: 1, hasImage: false, preview: '' },
                { id: 2, hasImage: false, preview: '' },
                { id: 3, hasImage: false, preview: '' }
            ],

            // Computed getters for CSP compatibility
            get photo1NoImage() { return !this.photos[0].hasImage; },
            get photo2NoImage() { return !this.photos[1].hasImage; },
            get photo3NoImage() { return !this.photos[2].hasImage; },
            get photo1HasImage() { return this.photos[0].hasImage; },
            get photo1Preview() { return this.photos[0].preview; },
            get photo2HasImage() { return this.photos[1].hasImage; },
            get photo2Preview() { return this.photos[1].preview; },
            get photo3HasImage() { return this.photos[2].hasImage; },
            get photo3Preview() { return this.photos[2].preview; },

            init: function() {
                var el = this.$el;
                var self = this;

                // Read initial photo states from data attributes (from database)
                for (var i = 1; i <= 3; i++) {
                    var hasImage = el.getAttribute('data-photo-' + i + '-exists') === 'true';
                    var preview = el.getAttribute('data-photo-' + i + '-url') || '';
                    this.photos[i - 1].hasImage = hasImage;
                    this.photos[i - 1].preview = preview;
                    this.photos[i - 1].uploadedUrl = preview;
                }

                // Also check draft for any newly uploaded photos not yet in database
                fetch('/api/profile/draft/get/')
                    .then(function(response) { return response.json(); })
                    .then(function(result) {
                        if (result.success && result.data && result.data.merged) {
                            var draft = result.data.merged;

                            // Check each photo URL in draft
                            for (var i = 1; i <= 3; i++) {
                                var photoUrlKey = 'photo_' + i + '_url';
                                if (draft[photoUrlKey]) {
                                    self.photos[i - 1].preview = draft[photoUrlKey];
                                    self.photos[i - 1].hasImage = true;
                                    self.photos[i - 1].uploadedUrl = draft[photoUrlKey];
                                }
                            }
                        }
                    })
                    .catch(function(err) {
                        console.error('[PHOTO UPLOAD] Failed to load draft photos:', err);
                    });
            },
            handleFile1: function(event) {
                this._handleFileSelect(0, event);
            },
            handleFile2: function(event) {
                this._handleFileSelect(1, event);
            },
            handleFile3: function(event) {
                this._handleFileSelect(2, event);
            },
            _handleFileSelect: function(index, event) {
                var file = event.target.files[0];
                if (file) {
                    var self = this;
                    var photoNumber = index + 1; // Convert 0-indexed to 1-indexed

                    // Show preview immediately
                    var reader = new FileReader();
                    reader.onload = function(e) {
                        self.photos[index].preview = e.target.result;
                        self.photos[index].hasImage = true;
                    };
                    reader.readAsDataURL(file);

                    // Upload to server immediately (auto-save)
                    var formData = new FormData();
                    formData.append('photo', file);
                    formData.append('photo_number', photoNumber);

                    // Get CSRF token
                    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
                    if (csrfToken) {
                        formData.append('csrfmiddlewaretoken', csrfToken.value);
                    }

                    fetch('/api/profile/draft/upload-photo/', {
                        method: 'POST',
                        headers: {
                            'X-CSRFToken': csrfToken ? csrfToken.value : ''
                        },
                        body: formData
                    })
                    .then(function(response) {
                        return response.json();
                    })
                    .then(function(result) {
                        if (result.success) {
                            self.photos[index].uploadedUrl = result.photo_url;
                        } else {
                            console.error('[PHOTO UPLOAD] ❌ Upload failed:', result.error);
                            alert('Photo upload failed: ' + result.error);
                        }
                    })
                    .catch(function(err) {
                        console.error('[PHOTO UPLOAD] ❌ Network error:', err);
                        alert('Photo upload failed. Please try again.');
                    });
                }
            },
            removePhoto1: function() {
                this._removePhoto(0);
            },
            removePhoto2: function() {
                this._removePhoto(1);
            },
            removePhoto3: function() {
                this._removePhoto(2);
            },
            _removePhoto: function(index) {
                this.photos[index].preview = '';
                this.photos[index].hasImage = false;
                var input = document.getElementById('photo' + (index + 1));
                if (input) {
                    input.value = '';
                }
            }
        };
    });

    // Profile creation wizard component
    // Reads initial values from data attributes
    Alpine.data('profileWizard', function() {
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
            gender: '',
            location: '',
            locationName: '',

            // Step 2 required fields tracking
            lookingFor: '',

            // Date of birth formatted display (from dobPicker)
            dobFormatted: '',

            // Field-specific error messages
            fieldErrors: {},

            // Step saving state
            isSaving: false,
            saveError: '',

            // Computed-like properties for CSP compatibility
            // These avoid function calls in templates
            get step1Completed() { return this.currentStep > 1; },
            get step2Completed() { return this.currentStep > 2; },
            get step3Completed() { return this.currentStep > 3; },
            get step4Completed() { return this.currentStep > 4; },
            get isStep1() { return this.currentStep === 1; },
            get isStep2() { return this.currentStep === 2; },
            get isStep3() { return this.currentStep === 3; },
            get isStep4() { return this.currentStep === 4; },
            get step1NotCompleted() { return !this.step1Completed; },
            get step2NotCompleted() { return !this.step2Completed; },
            get step3NotCompleted() { return !this.step3Completed; },
            get notPhoneVerified() { return !this.phoneVerified; },
            get isNotEditing() { return !this.isEditing; },
            get isNotSubmitting() { return !this.isSubmitting; },
            get isSavingStep() { return this.isSaving; },
            get isNotSaving() { return !this.isSaving; },
            get hasSaveError() { return this.saveError !== ''; },
            get hasFieldErrors() { return Object.keys(this.fieldErrors).length > 0; },
            get canContinueStep1() { return this.phoneVerified && this.gender !== '' && this.location !== '' && !this.isSaving; },
            get cannotContinueStep1() { return !this.phoneVerified || this.gender === '' || this.location === '' || this.isSaving; },
            get canContinueStep2() { return this.lookingFor !== '' && !this.isSaving; },
            get cannotContinueStep2() { return this.lookingFor === '' || this.isSaving; },
            get hasGenderError() { return this.fieldErrors.gender !== undefined; },
            get hasLocationError() { return this.fieldErrors.location !== undefined; },
            get hasLookingForError() { return this.fieldErrors.looking_for !== undefined; },
            get genderErrorMessage() { return this.fieldErrors.gender || ''; },
            get locationErrorMessage() { return this.fieldErrors.location || ''; },
            get lookingForErrorMessage() { return this.fieldErrors.looking_for || ''; },

            // Step progress bar classes (avoid ternary expressions in templates)
            get step1CircleClass() {
                return this.currentStep >= 1
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-300';
            },
            get step1TextClass() {
                return this.currentStep >= 1
                    ? 'text-purple-600 font-medium'
                    : 'text-gray-400';
            },
            get step2CircleClass() {
                return this.currentStep >= 2
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-300';
            },
            get step2TextClass() {
                return this.currentStep >= 2
                    ? 'text-purple-600 font-medium'
                    : 'text-gray-400';
            },
            get step3CircleClass() {
                return this.currentStep >= 3
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-300';
            },
            get step3TextClass() {
                return this.currentStep >= 3
                    ? 'text-purple-600 font-medium'
                    : 'text-gray-400';
            },
            get step4CircleClass() {
                return this.currentStep >= 4
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-300';
            },
            get step4TextClass() {
                return this.currentStep >= 4
                    ? 'text-purple-600 font-medium'
                    : 'text-gray-400';
            },
            get step1ConnectorClass() {
                return this.step1Completed
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-200';
            },
            get step2ConnectorClass() {
                return this.step2Completed
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-200';
            },
            get step3ConnectorClass() {
                return this.step3Completed
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500'
                    : 'bg-gray-200';
            },

            // Step navigation button classes (for breadcrumb quick navigation)
            get step1ButtonClass() {
                return this.isStep1
                    ? 'bg-purple-100 text-purple-700 font-medium'
                    : 'text-gray-500 hover:text-purple-600 hover:bg-purple-50';
            },
            get step2ButtonClass() {
                return this.isStep2
                    ? 'bg-purple-100 text-purple-700 font-medium'
                    : 'text-gray-500 hover:text-purple-600 hover:bg-purple-50';
            },
            get step3ButtonClass() {
                return this.isStep3
                    ? 'bg-purple-100 text-purple-700 font-medium'
                    : 'text-gray-500 hover:text-purple-600 hover:bg-purple-50';
            },
            get step4ButtonClass() {
                return this.isStep4
                    ? 'bg-purple-100 text-purple-700 font-medium'
                    : 'text-gray-500 hover:text-purple-600 hover:bg-purple-50';
            },

            init: function() {
                // Read initial values from data attributes
                var el = this.$el;
                var initialStep = el.getAttribute('data-initial-step');
                var phoneVerified = el.getAttribute('data-phone-verified');
                var isEditing = el.getAttribute('data-is-editing');

                // Map step names to numbers
                var stepMap = {
                    'not_started': 1,
                    'step1': 2,
                    'step2': 3,
                    'step3': 4,
                    'completed': 4,
                    'submitted': 4
                };

                if (initialStep && stepMap[initialStep]) {
                    this.currentStep = stepMap[initialStep];
                } else if (initialStep && !isNaN(parseInt(initialStep))) {
                    this.currentStep = parseInt(initialStep);
                }

                this.phoneVerified = phoneVerified === 'true';
                this.isEditing = isEditing === 'true';

                // Set up HTMX listener
                var self = this;
                window.addEventListener('htmx:afterRequest', function(event) {
                    if (event.detail.successful) {
                        var trigger = event.detail.xhr.getResponseHeader('HX-Trigger-After-Swap');
                        if (trigger === 'step-valid') {
                            self.nextStep();
                        }
                    }
                });

                // Listen for phone verification event from nested component
                window.addEventListener('phone-verified', function() {
                    self.phoneVerified = true;
                });

                // Listen for phone unverification (when user clicks Change)
                this.$el.addEventListener('phone-unverified', function() {
                    self.phoneVerified = false;
                });

                // Initialize field values from DOM
                self.initFieldTracking();

                // Listen for custom events from canton map and gender selection
                window.addEventListener('location-selected', function(e) {
                    if (e.detail && e.detail.location) {
                        self.location = e.detail.location;
                        self.locationName = e.detail.name || e.detail.location;
                        self.fieldErrors.location = undefined;
                        self.saveDraft();
                    }
                });

                window.addEventListener('gender-selected', function(e) {
                    if (e.detail && e.detail.gender) {
                        self.gender = e.detail.gender;
                        self.fieldErrors.gender = undefined;
                    }
                });

                window.addEventListener('looking-for-selected', function(e) {
                    if (e.detail && e.detail.lookingFor) {
                        self.lookingFor = e.detail.lookingFor;
                        self.fieldErrors.looking_for = undefined;
                    }
                });

                // Listen for date of birth selection from dobPicker component
                window.addEventListener('dob-selected', function(e) {
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
            initFieldTracking: function() {
                var self = this;

                // Read initial gender value
                var genderEl = document.querySelector('[name="gender"]:checked');
                if (genderEl) {
                    self.gender = genderEl.value;
                }

                // Read initial location value and name
                var locationEl = document.getElementById('id_location');
                if (locationEl && locationEl.value) {
                    self.location = locationEl.value;
                    // Try to get the display name from the canton map component's data attribute
                    var cantonMapEl = document.querySelector('[x-data="cantonMap"]');
                    if (cantonMapEl) {
                        self.locationName = cantonMapEl.getAttribute('data-initial-name') || locationEl.value;
                    } else {
                        self.locationName = locationEl.value;
                    }
                }

                // Read initial looking_for value
                var lookingForEl = document.querySelector('[name="looking_for"]:checked');
                if (lookingForEl) {
                    self.lookingFor = lookingForEl.value;
                }

                // Set up change listeners for gender radio buttons
                var genderRadios = document.querySelectorAll('[name="gender"]');
                genderRadios.forEach(function(radio) {
                    radio.addEventListener('change', function(e) {
                        self.gender = e.target.value;
                        self.fieldErrors.gender = undefined;
                        // Dispatch event for other components
                        window.dispatchEvent(new CustomEvent('gender-selected', {
                            detail: { gender: e.target.value }
                        }));
                    });
                });

                // Set up change listener for location (hidden input updated by canton map)
                var locationInput = document.getElementById('id_location');
                if (locationInput) {
                    // Use MutationObserver to detect value changes on hidden input
                    var observer = new MutationObserver(function(mutations) {
                        mutations.forEach(function(mutation) {
                            if (mutation.type === 'attributes' && mutation.attributeName === 'value') {
                                self.location = locationInput.value;
                                self.fieldErrors.location = undefined;
                            }
                        });
                    });
                    observer.observe(locationInput, { attributes: true });

                    // Also listen for direct changes
                    locationInput.addEventListener('change', function(e) {
                        self.location = e.target.value;
                        self.fieldErrors.location = undefined;
                    });
                }

                // Set up change listeners for looking_for radio buttons
                var lookingForRadios = document.querySelectorAll('[name="looking_for"]');
                lookingForRadios.forEach(function(radio) {
                    radio.addEventListener('change', function(e) {
                        self.lookingFor = e.target.value;
                        self.fieldErrors.looking_for = undefined;
                    });
                });
            },

            nextStep: function() {
                if (this.currentStep < this.totalSteps) {
                    this.currentStep++;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            },

            // CSP-compatible method for conditional next step (requires phone verification)
            nextStepIfVerified: function() {
                if (this.phoneVerified) {
                    this.nextStep();
                }
            },

            prevStep: function() {
                if (this.currentStep > 1) {
                    this.currentStep--;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            },

            goToStep: function(step) {
                if (step >= 1 && step <= this.totalSteps) {
                    this.currentStep = step;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            },

            isStepCompleted: function(step) {
                return step < this.currentStep;
            },

            isCurrentStep: function(step) {
                return step === this.currentStep;
            },

            updateReview: function() {
                var phone = document.querySelector('[name=phone_number]');
                var genderEl = document.querySelector('[name=gender]:checked');
                var location = document.querySelector('[name=location]');

                var reviewPhone = this.$refs.reviewPhone;
                var reviewDob = this.$refs.reviewDob;
                var reviewGender = this.$refs.reviewGender;
                var reviewLocation = this.$refs.reviewLocation;

                if (reviewPhone) {
                    reviewPhone.textContent = phone ? phone.value || 'Not provided' : 'Not provided';
                }
                if (reviewDob) {
                    // Use formatted date from dobPicker if available
                    reviewDob.textContent = this.dobFormatted || 'Not provided';
                }
                if (reviewGender && genderEl) {
                    var label = genderEl.nextElementSibling;
                    if (label) {
                        var genderLabel = label.querySelector('.gender-label');
                        reviewGender.textContent = genderLabel ? genderLabel.textContent : 'Not selected';
                    }
                } else if (reviewGender) {
                    reviewGender.textContent = 'Not selected';
                }
                if (reviewLocation) {
                    reviewLocation.textContent = this.locationName || 'Not selected';
                }
            },

            setSubmitting: function() {
                this.isSubmitting = true;
                // CRITICAL: Before form submission, ensure phone number input has full international number
                // intlTelInput with separateDialCode=true stores only national number in input.value
                // We need to set the full number so Django form receives it correctly
                if (window.itiInstance) {
                    var phoneInput = document.querySelector('[name="phone_number"]');
                    if (phoneInput && !phoneInput.readOnly) {
                        // Get full international number from intlTelInput
                        var fullNumber = window.itiInstance.getNumber();
                        if (fullNumber) {
                            phoneInput.value = fullNumber;
                        }
                    }
                }
            },

            // Handle form submission - only allow on final step (Step 4: Review)
            // This prevents Enter key in text inputs from submitting the form prematurely
            handleFormSubmit: function(e) {
                // Only allow submission when on the final step
                if (this.currentStep !== 4) {
                    // Prevent form submission on non-final steps
                    return;
                }
                // Prepare form data (phone number formatting)
                this.setSubmitting();
                // Now manually submit the form
                var form = document.getElementById('profileForm');
                if (form) {
                    form.submit();
                }
            },

            nextStepAndReview: function() {
                this.nextStep();
                this.updateReview();
            },

            // CSRF token helper for AJAX requests
            // Reads from hidden form input (works with CSRF_COOKIE_HTTPONLY=True)
            getCsrfToken: function() {
                // First try the hidden form input (preferred when CSRF_COOKIE_HTTPONLY=True)
                var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
                if (input && input.value) {
                    return input.value;
                }
                // Fallback to cookie (if CSRF_COOKIE_HTTPONLY=False)
                var cookie = document.cookie.split('; ')
                    .find(function(row) { return row.startsWith('csrftoken='); });
                return cookie ? cookie.split('=')[1] : '';
            },

            // Collect Step 1 form data
            collectStep1Data: function() {
                var phoneEl = document.querySelector('[name="phone_number"]');
                var dobEl = document.querySelector('[name="date_of_birth"]');
                var genderEl = document.querySelector('[name="gender"]:checked');
                var locationEl = document.getElementById('id_location');

                // Get phone number: prefer intlTelInput's getNumber() for full international format
                // intlTelInput with separateDialCode=true stores only national number in input.value
                var phoneNumber = '';
                if (window.itiInstance) {
                    phoneNumber = window.itiInstance.getNumber() || '';
                } else if (phoneEl) {
                    phoneNumber = phoneEl.value || '';
                }

                return {
                    phone_number: phoneNumber,
                    date_of_birth: dobEl ? dobEl.value : '',
                    gender: genderEl ? genderEl.value : '',
                    location: locationEl ? locationEl.value : ''
                };
            },

            // Collect Step 2 form data
            collectStep2Data: function() {
                var bioEl = document.querySelector('[name="bio"]');
                var interestsEl = document.querySelector('[name="interests"]');
                var lookingForEl = document.querySelector('[name="looking_for"]:checked');

                return {
                    bio: bioEl ? bioEl.value : '',
                    interests: interestsEl ? interestsEl.value : '',
                    looking_for: lookingForEl ? lookingForEl.value : ''
                };
            },

            // Collect Step 3 form data (privacy settings only - photos handled by HTMX)
            collectStep3Data: function() {
                var showFullName = document.querySelector('[name="show_full_name"]');
                var showExactAge = document.querySelector('[name="show_exact_age"]');
                var blurPhotos = document.querySelector('[name="blur_photos"]');

                return {
                    show_full_name: showFullName ? showFullName.checked : false,
                    show_exact_age: showExactAge ? showExactAge.checked : true,
                    blur_photos: blurPhotos ? blurPhotos.checked : false
                };
            },

            // Save Step 1 data to backend
            saveStep1: function() {
                var self = this;
                self.isSaving = true;
                self.saveError = '';
                self.fieldErrors = {};

                var data = self.collectStep1Data();

                return fetch('/api/profile/save-step1/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': self.getCsrfToken()
                    },
                    body: JSON.stringify(data)
                })
                .then(function(response) {
                    return response.json().then(function(d) {
                        return { ok: response.ok, data: d };
                    });
                })
                .then(function(result) {
                    self.isSaving = false;
                    if (result.ok && result.data.success) {
                        self.fieldErrors = {};
                        return { success: true };
                    } else {
                        self.saveError = result.data.error || 'Failed to save. Please try again.';
                        // Handle field-specific errors from backend
                        if (result.data.errors) {
                            self.fieldErrors = result.data.errors;
                        }
                        return { success: false, error: self.saveError, errors: result.data.errors };
                    }
                })
                .catch(function(err) {
                    self.isSaving = false;
                    self.saveError = 'Network error. Please check your connection.';
                    return { success: false, error: self.saveError };
                });
            },

            // Save Step 2 data to backend
            saveStep2: function() {
                var self = this;
                self.isSaving = true;
                self.saveError = '';
                self.fieldErrors = {};

                var data = self.collectStep2Data();

                return fetch('/api/profile/save-step2/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': self.getCsrfToken()
                    },
                    body: JSON.stringify(data)
                })
                .then(function(response) {
                    return response.json().then(function(d) {
                        return { ok: response.ok, data: d };
                    });
                })
                .then(function(result) {
                    self.isSaving = false;
                    if (result.ok && result.data.success) {
                        self.fieldErrors = {};
                        return { success: true };
                    } else {
                        self.saveError = result.data.error || 'Failed to save. Please try again.';
                        // Handle field-specific errors from backend
                        if (result.data.errors) {
                            self.fieldErrors = result.data.errors;
                        }
                        return { success: false, error: self.saveError, errors: result.data.errors };
                    }
                })
                .catch(function(err) {
                    self.isSaving = false;
                    self.saveError = 'Network error. Please check your connection.';
                    return { success: false, error: self.saveError };
                });
            },

            // Save Step 3 data to backend (privacy settings via FormData)
            saveStep3: function() {
                var self = this;
                self.isSaving = true;
                self.saveError = '';

                var data = self.collectStep3Data();

                // Use FormData to match backend expectation
                var formData = new FormData();
                if (data.show_full_name) formData.append('show_full_name', 'on');
                if (data.show_exact_age) formData.append('show_exact_age', 'on');
                if (data.blur_photos) formData.append('blur_photos', 'on');

                return fetch('/api/profile/save-step3/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': self.getCsrfToken()
                    },
                    body: formData
                })
                .then(function(response) {
                    return response.json().then(function(d) {
                        return { ok: response.ok, data: d };
                    });
                })
                .then(function(result) {
                    self.isSaving = false;
                    if (result.ok && result.data.success) {
                        return { success: true };
                    } else {
                        self.saveError = result.data.error || 'Failed to save. Please try again.';
                        return { success: false, error: self.saveError };
                    }
                })
                .catch(function(err) {
                    self.isSaving = false;
                    self.saveError = 'Network error. Please check your connection.';
                    return { success: false, error: self.saveError };
                });
            },

            // Save Step 1 and advance if successful
            saveAndNextStep1: function() {
                var self = this;
                if (!self.phoneVerified) return;

                self.saveStep1().then(function(result) {
                    if (result.success) {
                        self.saveError = '';
                        self.currentStep = 2;
                        window.scrollTo({ top: 0, behavior: 'smooth' });
                    }
                    // Error is already set in saveStep1
                });
            },

            // Save Step 2 and advance if successful
            saveAndNextStep2: function() {
                var self = this;

                self.saveStep2().then(function(result) {
                    if (result.success) {
                        self.saveError = '';
                        self.currentStep = 3;
                        window.scrollTo({ top: 0, behavior: 'smooth' });
                    }
                });
            },

            // Save Step 3 and advance if successful
            saveAndNextStep3: function() {
                var self = this;

                self.saveStep3().then(function(result) {
                    if (result.success) {
                        self.saveError = '';
                        self.currentStep = 4;
                        self.updateReview();
                        window.scrollTo({ top: 0, behavior: 'smooth' });
                    }
                });
            },

            // Clear save error (for dismissing error messages)
            clearSaveError: function() {
                this.saveError = '';
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
            get showAutoSaving() { return this.isAutoSaving; },
            get showLastSaved() { return this.lastSavedAt !== null; },
            get lastSavedMessage() {
                if (!this.lastSavedAt) return '';
                var now = new Date();
                var saved = new Date(this.lastSavedAt);
                var diffMs = now - saved;
                var diffSec = Math.floor(diffMs / 1000);

                if (diffSec < 10) return 'Saved just now';
                if (diffSec < 60) return 'Saved ' + diffSec + 's ago';
                var diffMin = Math.floor(diffSec / 60);
                if (diffMin < 60) return 'Saved ' + diffMin + 'm ago';
                return 'Saved earlier';
            },

            // Load draft data on init
            loadDraft: function() {
                var self = this;

                fetch('/api/profile/draft/get/')
                    .then(function(response) {
                        return response.json();
                    })
                    .then(function(result) {
                        if (result.success && result.data) {
                            self.draftData = result.data.merged || {};
                            self.lastSavedAt = result.data.last_saved;

                            self.populateFieldsFromDraft();
                        }
                    })
                    .catch(function(err) {
                        console.error('[DRAFT LOAD] Failed to load draft:', err);
                    });
            },

            // Populate form fields from draft data
            populateFieldsFromDraft: function() {
                var self = this;
                var form = this.$el.querySelector('form');

                if (!form) {
                    return;
                }

                for (var key in this.draftData) {
                    var value = this.draftData[key];

                    // CRITICAL: Skip file inputs (photos) - cannot be set programmatically for security
                    if (key === 'photo_1' || key === 'photo_2' || key === 'photo_3') {
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
                        if (input.type === 'checkbox') {
                            // Handle single checkbox - convert string 'true'/'false' or Python 'True'/'False' to boolean
                            var shouldCheck = (value === true || value === 'true' || value === '1' || value === 'True');
                            input.checked = shouldCheck;
                        } else if (input.type === 'radio') {
                            // For radio buttons, find the one with matching value
                            var radios = form.querySelectorAll('[name="' + key + '"]');

                            for (var i = 0; i < radios.length; i++) {
                                if (radios[i].value === value) {
                                    radios[i].checked = true;
                                    break;
                                }
                            }
                        } else if (input.type !== 'file') {
                            // Only set value for non-file inputs
                            input.value = value || '';
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
                        var dateParts = this.draftData.date_of_birth.split('-');
                        if (dateParts.length === 3) {
                            var dateObj = new Date(dateParts[0], dateParts[1] - 1, dateParts[2]);
                            var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                            this.dobFormatted = months[dateObj.getMonth()] + ' ' + dateObj.getDate() + ', ' + dateObj.getFullYear();
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
                        var mapContainer = document.querySelector('[x-data*="cantonMap"]');
                        if (mapContainer) {
                            var regionPath = document.getElementById(this.draftData.location);
                            if (regionPath) {
                                this.locationName = regionPath.getAttribute('data-region-name') || this.draftData.location;
                            } else {
                                this.locationName = this.draftData.location;
                            }
                        } else {
                            this.locationName = this.draftData.location;
                        }
                    }
                }

                // CRITICAL FIX: Update the review display after populating fields
                // This ensures Step 4 review shows the correct data when page is refreshed
                setTimeout(function() {
                    self.updateReview();
                }, 100); // Small delay to ensure DOM is ready

            },

            // Setup auto-save event listeners
            setupAutoSaveListeners: function() {
                var self = this;
                var form = this.$el.querySelector('form');
                if (!form) return;

                // Text inputs and textareas: debounced save (2 seconds after typing stops)
                var textInputs = form.querySelectorAll('input[type="text"], input[type="tel"], input[type="date"], textarea');
                for (var i = 0; i < textInputs.length; i++) {
                    textInputs[i].addEventListener('input', function() {
                        self.scheduleAutoSave();
                    });
                }

                // Dropdowns, radios, and checkboxes: immediate save
                var immediateInputs = form.querySelectorAll('select, input[type="radio"], input[type="checkbox"]');
                for (var j = 0; j < immediateInputs.length; j++) {
                    immediateInputs[j].addEventListener('change', function() {
                        self.saveDraft();
                    });
                }
            },

            // Schedule auto-save with debounce (2 seconds)
            scheduleAutoSave: function() {
                clearTimeout(this.autoSaveTimer);
                this.isDirty = true;

                var self = this;
                this.autoSaveTimer = setTimeout(function() {
                    self.saveDraft();
                }, 2000);  // 2-second debounce
            },

            // Save current step data to draft (no validation)
            saveDraft: function() {
                var self = this;
                self.isAutoSaving = true;

                var stepData = self.gatherCurrentStepData();

                var payload = {
                    step: self.currentStep,
                    data: stepData
                };
                var jsonPayload = JSON.stringify(payload);

                fetch('/api/profile/draft/save/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': self.getCsrfToken()
                    },
                    body: jsonPayload
                })
                .then(function(response) {
                    return response.json();
                })
                .then(function(result) {
                    self.isAutoSaving = false;
                    self.isDirty = false;
                    if (result.success) {
                        self.lastSavedAt = result.saved_at;
                    } else {
                        console.error('[DRAFT SAVE] Save failed:', result.error);
                    }
                })
                .catch(function(err) {
                    self.isAutoSaving = false;
                    console.error('[DRAFT SAVE] ❌ Network error:', err);
                });
            },

            // Gather current step form data
            gatherCurrentStepData: function() {
                var form = this.$el.querySelector('form');
                if (!form) {
                    console.warn('[GATHER] No form found');
                    return {};
                }

                var formData = new FormData(form);
                var data = {};
                var checkboxGroups = {};  // Track checkbox arrays

                // Collect all form data from FormData
                for (var pair of formData.entries()) {
                    var key = pair[0];
                    var value = pair[1];

                    // Skip file inputs (photos) - they're uploaded separately
                    if (key === 'photo_1' || key === 'photo_2' || key === 'photo_3') {
                        continue;
                    }

                    // CRITICAL FIX: Skip checkboxes - they're handled separately below
                    // FormData returns each checked checkbox as a separate entry, which would
                    // overwrite the previous value instead of building an array
                    var field = form.querySelector('[name="' + key + '"]');
                    if (field && field.type === 'checkbox') {
                        continue;  // Let the checkbox handling code process these
                    }

                    // Only add non-empty values
                    if (value !== '') {
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
            getCsrfToken: function() {
                var token = document.querySelector('[name=csrfmiddlewaretoken]');
                return token ? token.value : '';
            },

            // Setup periodic checkpoint (every 60 seconds if dirty)
            setupPeriodicCheckpoint: function() {
                var self = this;
                setInterval(function() {
                    if (self.isDirty) {
                        self.saveDraft();
                    }
                }, 60000);  // 60 seconds
            },

            // Warn before leaving with unsaved changes
            setupUnloadWarning: function() {
                var self = this;
                window.addEventListener('beforeunload', function(e) {
                    if (self.isDirty) {
                        e.preventDefault();
                        e.returnValue = '';
                    }
                });
            }
        };
    });

    // Canton Map component for location selection
    // Interactive SVG map for selecting Luxembourg cantons and border regions
    Alpine.data('cantonMap', function() {
        return {
            // State
            selectedRegion: '',
            selectedRegionName: '',
            hoveredRegion: '',
            hoveredRegionName: '',
            showFallbackDropdown: false,
            focusedIndex: -1,

            // Region data for keyboard navigation
            regions: [],

            // Computed getters for CSP compatibility
            get hasSelection() {
                return this.selectedRegion !== '';
            },
            get noSelection() {
                return this.selectedRegion === '';
            },
            get isHovering() {
                return this.hoveredRegion !== '';
            },
            get selectionLabel() {
                if (this.selectedRegionName) {
                    return this.selectedRegionName;
                }
                return this.$el.getAttribute('data-placeholder') || 'Click the map to select your region';
            },
            get hoverLabel() {
                return this.hoveredRegionName || '';
            },
            get fallbackDropdownVisible() {
                return this.showFallbackDropdown;
            },
            get selectedClass() {
                return this.hasSelection ? 'has-selection' : '';
            },

            init: function() {
                var self = this;

                // Read initial value from data attributes
                var initialValue = this.$el.getAttribute('data-initial-value');
                var initialName = this.$el.getAttribute('data-initial-name');

                if (initialValue && initialValue !== '') {
                    this.selectedRegion = initialValue;
                    this.selectedRegionName = initialName || initialValue;
                    // Highlight the initially selected region
                    this.$nextTick(function() {
                        // Safety check: ensure the function exists before calling
                        if (typeof self._highlightRegion === 'function') {
                            self._highlightRegion(initialValue);
                        }
                    });
                }

                // Build regions array for keyboard navigation
                this.$nextTick(function() {
                    self._buildRegionsArray();
                    self._setupEventDelegation();
                    self._setupKeyboardNavigation();
                });

                // Check for reduced motion preference
                if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
                    this.$el.classList.add('reduce-motion');
                }
            },

            _buildRegionsArray: function() {
                var svg = this.$el.querySelector('svg');
                if (!svg) return;

                var paths = svg.querySelectorAll('[data-region-id]');
                this.regions = [];

                for (var i = 0; i < paths.length; i++) {
                    this.regions.push({
                        id: paths[i].getAttribute('data-region-id'),
                        name: paths[i].getAttribute('data-region-name'),
                        element: paths[i]
                    });
                }
            },

            _getBorderRegionAtPoint: function(svgX, svgY) {
                // France - South side (narrow bottom strip)
                if (svgY > 850) {
                    return { id: 'border-france', name: 'France (Thionville/Metz area)' };
                }
                // Belgium - West side (left strip)
                if (svgX < 350) {
                    return { id: 'border-belgium', name: 'Belgium (Arlon area)' };
                }
                // Germany - East side (right strip)
                if (svgX > 350) {
                    return { id: 'border-germany', name: 'Germany (Trier/Saarland area)' };
                }
                return null;
            },

            _screenToSVGCoords: function(svg, screenX, screenY) {
                var point = svg.createSVGPoint();
                point.x = screenX;
                point.y = screenY;
                var ctm = svg.getScreenCTM();
                if (ctm) {
                    return point.matrixTransform(ctm.inverse());
                }
                return point;
            },

            _setupEventDelegation: function() {
                var self = this;
                var svg = this.$el.querySelector('svg');
                if (!svg) return;

                // Click handler
                svg.addEventListener('click', function(e) {
                    var path = e.target.closest('[data-region-id]');

                    if (path && path.classList.contains('lux-canton')) {
                        self.selectRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                        return;
                    }

                    if (path && path.classList.contains('border-region')) {
                        self.selectRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                        return;
                    }

                    if (e.target.classList.contains('map-background') || e.target.tagName === 'svg') {
                        var svgPoint = self._screenToSVGCoords(svg, e.clientX, e.clientY);
                        var borderRegion = self._getBorderRegionAtPoint(svgPoint.x, svgPoint.y);
                        if (borderRegion) {
                            self.selectRegion(borderRegion.id, borderRegion.name);
                        }
                    }
                });

                // Mouseover handler
                svg.addEventListener('mouseover', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        self.hoverRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                    }
                });

                // Mouseout handler
                svg.addEventListener('mouseout', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        self.clearHover();
                    }
                });

                // Touch events for mobile
                svg.addEventListener('touchstart', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        self.hoverRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                    }
                }, { passive: true });

                svg.addEventListener('touchend', function(e) {
                    var path = e.target.closest('[data-region-id]');
                    if (path) {
                        self.selectRegion(path.getAttribute('data-region-id'), path.getAttribute('data-region-name'));
                        self.clearHover();
                    }
                });
            },

            _setupKeyboardNavigation: function() {
                var self = this;
                var svg = this.$el.querySelector('svg');
                if (!svg) return;

                svg.addEventListener('keydown', function(e) {
                    var key = e.key;
                    if (key === 'ArrowDown' || key === 'ArrowRight') {
                        e.preventDefault();
                        self._navigateNext();
                    } else if (key === 'ArrowUp' || key === 'ArrowLeft') {
                        e.preventDefault();
                        self._navigatePrevious();
                    } else if (key === 'Enter' || key === ' ') {
                        e.preventDefault();
                        self._selectFocused();
                    } else if (key === 'Home') {
                        e.preventDefault();
                        self._focusFirst();
                    } else if (key === 'End') {
                        e.preventDefault();
                        self._focusLast();
                    }
                });

                svg.addEventListener('focus', function() {
                    if (self.focusedIndex < 0 && self.regions.length > 0) {
                        var selectedIndex = self._getSelectedIndex();
                        self.focusedIndex = selectedIndex >= 0 ? selectedIndex : 0;
                        self._applyFocus();
                    }
                });

                svg.addEventListener('blur', function() {
                    self._clearFocus();
                });
            },

            _navigateNext: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = (this.focusedIndex + 1) % this.regions.length;
                this._applyFocus();
            },

            _navigatePrevious: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = (this.focusedIndex - 1 + this.regions.length) % this.regions.length;
                this._applyFocus();
            },

            _focusFirst: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = 0;
                this._applyFocus();
            },

            _focusLast: function() {
                if (this.regions.length === 0) return;
                this.focusedIndex = this.regions.length - 1;
                this._applyFocus();
            },

            _selectFocused: function() {
                if (this.focusedIndex >= 0 && this.focusedIndex < this.regions.length) {
                    var region = this.regions[this.focusedIndex];
                    this.selectRegion(region.id, region.name);
                }
            },

            _getSelectedIndex: function() {
                for (var i = 0; i < this.regions.length; i++) {
                    if (this.regions[i].id === this.selectedRegion) {
                        return i;
                    }
                }
                return -1;
            },

            _applyFocus: function() {
                this._clearFocus();
                if (this.focusedIndex >= 0 && this.focusedIndex < this.regions.length) {
                    var region = this.regions[this.focusedIndex];
                    region.element.classList.add('region-focused');
                    this.hoverRegion(region.id, region.name);
                }
            },

            _clearFocus: function() {
                var svg = this.$el.querySelector('svg');
                if (!svg) return;
                var focusedElements = svg.querySelectorAll('.region-focused');
                for (var i = 0; i < focusedElements.length; i++) {
                    focusedElements[i].classList.remove('region-focused');
                }
            },

            selectRegion: function(regionId, regionName) {
                if (this.selectedRegion) {
                    this._unhighlightRegion(this.selectedRegion);
                }
                this.selectedRegion = regionId;
                this.selectedRegionName = regionName;
                this._highlightRegion(regionId);

                var hiddenInput = document.getElementById('id_location');
                if (hiddenInput) {
                    hiddenInput.value = regionId;
                    hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
                }

                // Dispatch global event for profileWizard validation tracking
                window.dispatchEvent(new CustomEvent('location-selected', {
                    detail: { location: regionId, name: regionName }
                }));

                this.$dispatch('region-selected', { id: regionId, name: regionName });
            },

            hoverRegion: function(regionId, regionName) {
                this.hoveredRegion = regionId;
                this.hoveredRegionName = regionName;
                var path = document.getElementById(regionId);
                if (path && !path.classList.contains('region-selected')) {
                    path.classList.add('region-hover');
                }
            },

            clearHover: function() {
                if (this.hoveredRegion) {
                    var prevPath = document.getElementById(this.hoveredRegion);
                    if (prevPath) {
                        prevPath.classList.remove('region-hover');
                    }
                }
                this.hoveredRegion = '';
                this.hoveredRegionName = '';
            },

            toggleFallbackDropdown: function() {
                this.showFallbackDropdown = !this.showFallbackDropdown;
            },

            handleFallbackSelect: function(event) {
                var select = event.target;
                var option = select.options[select.selectedIndex];
                if (option && option.value) {
                    this.selectRegion(option.value, option.text);
                }
            },

            _highlightRegion: function(regionId) {
                var path = document.getElementById(regionId);
                if (path) {
                    path.classList.add('region-selected');
                    path.classList.remove('region-hover');
                    path.setAttribute('aria-selected', 'true');
                }
            },

            _unhighlightRegion: function(regionId) {
                var path = document.getElementById(regionId);
                if (path) {
                    path.classList.remove('region-selected');
                    path.setAttribute('aria-selected', 'false');
                }
            }
        };
    });

    // Date of Birth Picker component - stepped selection for better UX
    // 4 steps: 1) Age range chips, 2) Year selection, 3) Month selection, 4) Day selection
    // CSP-compatible: Uses DOM manipulation for dynamic content (x-for not CSP-safe)
    // Reads initial value from hidden input with name="date_of_birth"
    Alpine.data('dobPicker', function() {
        return {
            // State
            step: 1,  // 1=age range, 2=year, 3=month, 4=day
            selectedAgeRange: '',
            selectedYear: null,
            selectedMonth: null,
            selectedDay: null,
            _translatedMonths: null,
            _clsUnselected: 'border-gray-200 bg-white dark:bg-gray-800 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:border-purple-300 hover:bg-purple-50 dark:hover:bg-purple-900/30',
            _clsSelected: 'border-purple-500 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:border-purple-400 dark:text-purple-300',

            // Age range definitions (computed from current year)
            get ageRanges() {
                var y = new Date().getFullYear();
                return [
                    { label: '18-25', emoji: '\u{1F331}', minYear: y - 25, maxYear: y - 18 },
                    { label: '26-35', emoji: '\u{1F33F}', minYear: y - 35, maxYear: y - 26 },
                    { label: '36-45', emoji: '\u{1F333}', minYear: y - 45, maxYear: y - 36 },
                    { label: '46-55', emoji: '\u{1F342}', minYear: y - 55, maxYear: y - 46 },
                    { label: '56-65', emoji: '\u{1F341}', minYear: y - 65, maxYear: y - 56 },
                    { label: '66+',   emoji: '\u{1F31F}', minYear: y - 100, maxYear: y - 66 }
                ];
            },

            // CSP-safe computed getters
            get isStep1() { return this.step === 1; },
            get isStep2() { return this.step === 2; },
            get isStep3() { return this.step === 3; },
            get isStep4() { return this.step === 4; },
            get hasAgeRange() { return this.selectedAgeRange !== ''; },
            get hasYear() { return this.selectedYear !== null; },
            get hasMonth() { return this.selectedMonth !== null; },
            get hasDay() { return this.selectedDay !== null; },
            get isComplete() { return this.hasYear && this.hasMonth && this.hasDay; },
            get notComplete() { return !this.isComplete; },

            // Breadcrumb display text getters
            get yearBreadcrumbText() {
                return this.selectedYear ? String(this.selectedYear) : '';
            },
            get monthBreadcrumbText() {
                return this.selectedMonthName || '';
            },
            get dayBreadcrumbText() {
                return this.selectedDay ? String(this.selectedDay) : '';
            },

            get months() {
                if (this._translatedMonths) return this._translatedMonths;
                // Default English month names (will be overridden by data-months attribute)
                return [
                    { num: 1, name: 'January' },
                    { num: 2, name: 'February' },
                    { num: 3, name: 'March' },
                    { num: 4, name: 'April' },
                    { num: 5, name: 'May' },
                    { num: 6, name: 'June' },
                    { num: 7, name: 'July' },
                    { num: 8, name: 'August' },
                    { num: 9, name: 'September' },
                    { num: 10, name: 'October' },
                    { num: 11, name: 'November' },
                    { num: 12, name: 'December' }
                ];
            },

            get isoDate() {
                if (!this.isComplete) return '';
                var m = this.selectedMonth < 10 ? '0' + this.selectedMonth : this.selectedMonth;
                var d = this.selectedDay < 10 ? '0' + this.selectedDay : this.selectedDay;
                return this.selectedYear + '-' + m + '-' + d;
            },

            get formattedDate() {
                if (!this.isComplete) return '';
                var monthObj = this._findMonth(this.selectedMonth);
                var monthName = monthObj ? monthObj.name : this.selectedMonth;
                return this.selectedDay + ' ' + monthName + ' ' + this.selectedYear;
            },

            get selectedMonthName() {
                if (!this.selectedMonth) return '';
                var monthObj = this._findMonth(this.selectedMonth);
                return monthObj ? monthObj.name : '';
            },

            // Breadcrumb button class getters (with dark mode)
            get step1ButtonClass() {
                return this.isStep1
                    ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-medium'
                    : 'text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20';
            },
            get step2ButtonClass() {
                return this.isStep2
                    ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-medium'
                    : 'text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20';
            },
            get step3ButtonClass() {
                return this.isStep3
                    ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-medium'
                    : 'text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20';
            },
            get step4ButtonClass() {
                return this.isStep4
                    ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 font-medium'
                    : 'text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20';
            },

            // Breadcrumb visibility getters
            get showAgeRangeBreadcrumb() { return this.step > 1; },
            get showYearBreadcrumb() { return this.step > 2; },
            get showMonthBreadcrumb() { return this.step > 3; },

            init: function() {
                var self = this;

                // Load translated month names from data attribute
                var monthsData = this.$el.getAttribute('data-months');
                if (monthsData) {
                    try {
                        this._translatedMonths = JSON.parse(monthsData);
                    } catch (e) {
                        console.warn('dobPicker: Could not parse months data', e);
                    }
                }

                // Render initial age range buttons
                this.$nextTick(function() {
                    self._renderAgeRanges();
                    self._parseInitialDate();
                });

                // Listen for external resets if needed
                this.$el.addEventListener('dob-reset', function() {
                    self.step = 1;
                    self.selectedAgeRange = '';
                    self.selectedYear = null;
                    self.selectedMonth = null;
                    self.selectedDay = null;
                    self._updateHiddenInput();
                    self._renderAgeRanges();
                });
            },

            _findMonth: function(num) {
                var months = this.months;
                for (var i = 0; i < months.length; i++) {
                    if (months[i].num === num) return months[i];
                }
                return null;
            },

            _getDaysInMonth: function(year, month) {
                // Month is 1-based, Date uses 0-based months
                // Using day 0 of next month gives last day of current month
                return new Date(year, month, 0).getDate();
            },

            _validateSelectedDay: function() {
                if (this.selectedDay && this.selectedMonth && this.selectedYear) {
                    var maxDay = this._getDaysInMonth(this.selectedYear, this.selectedMonth);
                    if (this.selectedDay > maxDay) {
                        this.selectedDay = null;
                        this._updateCompletionSummary();
                    }
                }
            },

            _parseInitialDate: function() {
                var hiddenInput = this.$el.querySelector('input[name="date_of_birth"]');
                if (!hiddenInput || !hiddenInput.value) return;

                var parts = hiddenInput.value.split('-');
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
                    this.step = 4;  // Show completion state
                    // Re-render with initial values
                    this._renderAgeRanges();
                    this._renderYears();
                    this._renderMonths();
                    this._renderDays();
                    this._updateCompletionSummary();
                }
            },

            _updateHiddenInput: function() {
                var hiddenInput = this.$el.querySelector('input[name="date_of_birth"]');
                if (hiddenInput) {
                    hiddenInput.value = this.isoDate;
                    hiddenInput.dispatchEvent(new Event('change', { bubbles: true }));
                }
            },

            _dispatchDateSelected: function() {
                window.dispatchEvent(new CustomEvent('dob-selected', {
                    detail: {
                        iso: this.isoDate,
                        formatted: this.formattedDate,
                        year: this.selectedYear,
                        month: this.selectedMonth,
                        day: this.selectedDay
                    }
                }));
            },

            // DOM rendering methods (CSP-safe alternative to x-for)
            _renderAgeRanges: function() {
                var container = this.$el.querySelector('[data-dob-age-ranges]');
                if (!container) return;

                var self = this;
                container.innerHTML = '';

                this.ageRanges.forEach(function(range) {
                    var btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'w-full h-full min-h-[3.5rem] flex items-center justify-center gap-2 px-3 py-3 rounded-xl text-sm font-medium border-2 transition-all duration-200 hover:scale-105';
                    btn.className += self.selectedAgeRange === range.label
                        ? ' ' + self._clsSelected
                        : ' ' + self._clsUnselected;
                    btn.innerHTML = '<span class="text-xl flex-shrink-0">' + range.emoji + '</span><span class="whitespace-nowrap">' + range.label + '</span>';
                    btn.addEventListener('click', function() {
                        self.selectAgeRange(range.label);
                    });
                    container.appendChild(btn);
                });
            },

            _renderYears: function() {
                var container = this.$el.querySelector('[data-dob-years]');
                if (!container) return;

                var self = this;
                container.innerHTML = '';

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
                    (function(year) {
                        var btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'px-3 py-2 rounded-lg text-sm font-medium border-2 transition-all duration-150 hover:scale-105';
                        btn.className += self.selectedYear === year
                            ? ' ' + self._clsSelected
                            : ' ' + self._clsUnselected;
                        btn.textContent = year;
                        btn.addEventListener('click', function() {
                            self.selectYear(year);
                        });
                        container.appendChild(btn);
                    })(y);
                }
            },

            _renderMonths: function() {
                var container = this.$el.querySelector('[data-dob-months]');
                if (!container) return;

                var self = this;
                container.innerHTML = '';

                this.months.forEach(function(month) {
                    var btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'px-3 py-2.5 rounded-lg text-sm font-medium border-2 transition-all duration-150 hover:scale-105';
                    btn.className += self.selectedMonth === month.num
                        ? ' ' + self._clsSelected
                        : ' ' + self._clsUnselected;
                    btn.textContent = month.name;
                    btn.addEventListener('click', function() {
                        self.selectMonth(month.num);
                    });
                    container.appendChild(btn);
                });
            },

            _renderDays: function() {
                var container = this.$el.querySelector('[data-dob-days]');
                if (!container) return;

                var self = this;
                container.innerHTML = '';

                if (!this.selectedMonth || !this.selectedYear) return;

                var daysInMonth = this._getDaysInMonth(this.selectedYear, this.selectedMonth);

                for (var d = 1; d <= daysInMonth; d++) {
                    (function(day) {
                        var btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'w-full aspect-square flex items-center justify-center rounded-lg text-sm font-medium border-2 transition-all duration-150 hover:scale-105';
                        btn.className += self.selectedDay === day
                            ? ' ' + self._clsSelected
                            : ' ' + self._clsUnselected;
                        btn.textContent = day;
                        btn.addEventListener('click', function() {
                            self.selectDay(day);
                        });
                        container.appendChild(btn);
                    })(d);
                }
            },

            _updateCompletionSummary: function() {
                var summary = this.$el.querySelector('[data-dob-summary]');
                if (summary) {
                    if (this.isComplete) {
                        summary.classList.remove('hidden');
                        var dateDisplay = summary.querySelector('[data-dob-formatted]');
                        if (dateDisplay) {
                            dateDisplay.textContent = this.formattedDate;
                        }
                    } else {
                        summary.classList.add('hidden');
                    }
                }
            },

            // Selection methods
            selectAgeRange: function(label) {
                this.selectedAgeRange = label;
                this.selectedYear = null;
                this.selectedMonth = null;
                this.selectedDay = null;
                this.step = 2;
                this._updateHiddenInput();
                this._renderAgeRanges();
                this._renderYears();
            },

            selectYear: function(year) {
                this.selectedYear = year;
                this._validateSelectedDay();
                this.selectedMonth = null;
                this.selectedDay = null;
                this.step = 3;
                this._updateHiddenInput();
                this._renderYears();
                this._renderMonths();
            },

            selectMonth: function(month) {
                this.selectedMonth = month;
                this._validateSelectedDay();
                this.selectedDay = null;
                this.step = 4;
                this._updateHiddenInput();
                this._renderMonths();
                this._renderDays();
            },

            selectDay: function(day) {
                this.selectedDay = day;
                this._updateHiddenInput();
                this._renderDays();
                this._updateCompletionSummary();
                this._dispatchDateSelected();
            },

            // Navigation methods for breadcrumb
            goToStep1: function() {
                this.step = 1;
                this._renderAgeRanges();
            },

            goToStep2: function() {
                if (this.hasAgeRange) {
                    this.step = 2;
                    this._renderYears();
                }
            },

            goToStep3: function() {
                if (this.hasYear) {
                    this.step = 3;
                    this._renderMonths();
                }
            },

            goToStep4: function() {
                if (this.hasMonth) {
                    this._validateSelectedDay();
                    this.step = 4;
                    this._renderDays();
                    this._updateCompletionSummary();
                }
            }
        };
    });

    // Phone verification component for the phone input field
    // Used in profile creation - wraps the phone input with verification logic
    // Reads initial state from data attributes: data-verified, data-phone-input-id
    Alpine.data('phoneVerificationComponent', function() {
        return {
            verified: false,
            canVerify: false,
            errorMessage: '',
            iti: null,
            phoneInputId: '',

            // Computed getters for CSP compatibility
            get notVerified() { return !this.verified; },
            get cannotVerify() { return !this.canVerify; },
            get verifiedValue() { return this.verified ? 'true' : 'false'; },
            get verifyButtonPulseClass() { return this.canVerify ? 'animate-pulse-subtle' : ''; },
            get phoneHintClass() { return !this.verified ? 'text-purple-600 font-medium' : 'text-gray-500'; },

            init: function() {
                var self = this;

                // Read initial state from data attributes
                var verifiedAttr = this.$el.getAttribute('data-verified');
                this.verified = verifiedAttr === 'true';

                this.phoneInputId = this.$el.getAttribute('data-phone-input-id') || 'id_phone_number';

                var phoneInput = document.getElementById(this.phoneInputId);
                if (phoneInput && typeof window.intlTelInput === 'function' && !this.verified) {
                    var prefilledValue = phoneInput.value;
                    phoneInput.value = '';

                    try {
                        this.iti = window.intlTelInput(phoneInput, {
                            initialCountry: "lu",
                            preferredCountries: ["lu", "de", "fr", "be"],
                            onlyCountries: ["lu", "de", "fr", "be", "nl", "ch", "at", "it", "es", "pt", "gb", "ie", "us", "ca", "se", "dz"],
                            separateDialCode: true,
                            nationalMode: false,
                            formatOnDisplay: true,
                            autoPlaceholder: "aggressive",
                            utilsScript: "https://cdn.jsdelivr.net/npm/intl-tel-input@18.5.3/build/js/utils.js"
                        });

                        window.itiInstance = this.iti;

                        this.iti.promise.then(function() {
                            if (prefilledValue) {
                                var num = prefilledValue.trim().replace(/\s/g, '');
                                if (!num.startsWith('+')) {
                                    if (num.startsWith('00')) num = '+' + num.slice(2);
                                    else num = '+352' + num.replace(/^0+/, '');
                                }
                                self.iti.setNumber(num);
                            }
                        }).catch(function(err) {
                            console.warn('intl-tel-input: Utils loading error', err);
                        });
                    } catch (err) {
                        console.error('intl-tel-input: Initialization error', err);
                    }
                }

                // Listen for verification success event
                window.addEventListener('phone-verified', function(e) {
                    self.verified = true;
                    var phoneInput = document.getElementById(self.phoneInputId);
                    if (phoneInput && e.detail) {
                        phoneInput.value = e.detail;
                        phoneInput.readOnly = true;
                    }
                });
            },

            // Cleanup intl-tel-input when component is destroyed (prevents memory leaks)
            destroy: function() {
                if (this.iti) {
                    try {
                        this.iti.destroy();
                    } catch (e) {
                        console.warn('intl-tel-input: Could not destroy instance', e);
                    }
                    this.iti = null;
                    window.itiInstance = null;
                }
            },

            // CSP-compatible: no event parameter needed, uses this.$el
            onPhoneInput: function() {
                if (this.iti) {
                    this.canVerify = this.iti.isValidNumber() || this.iti.getNumber().length >= 8;
                } else {
                    // Get value from the phone input element
                    var phoneInput = document.getElementById(this.phoneInputId);
                    this.canVerify = phoneInput && phoneInput.value.trim().length >= 6;
                }
                this.errorMessage = '';
            },

            startVerification: function() {
                var self = this;
                if (this.iti && !this.iti.isValidNumber()) {
                    this.errorMessage = 'Please enter a valid phone number';
                    return;
                }

                // Dispatch event to open modal
                window.dispatchEvent(new CustomEvent('open-phone-modal'));

                // Get phone number
                var phoneNumber = this.iti ? this.iti.getNumber() : document.getElementById(this.phoneInputId).value;

                // Initialize phone verification
                if (window.phoneVerification) {
                    window.phoneVerification.sendVerificationCode(phoneNumber).then(function(result) {
                        if (!result.success) {
                            self.errorMessage = result.error;
                        }
                    });
                }
            },

            resetVerification: function() {
                this.verified = false;
                this.canVerify = false;
                var phoneInput = document.getElementById(this.phoneInputId);
                if (phoneInput) {
                    phoneInput.readOnly = false;
                    phoneInput.value = '';
                    phoneInput.focus();
                }
                // Update parent Alpine state
                this.$dispatch('phone-unverified');
            }
        };
    });

    // Language switcher dropdown component (desktop navbar)
    // CSP-compatible component for changing site language
    Alpine.data('languageSwitcher', function() {
        return {
            langOpen: false,

            // Computed getters for CSP compatibility
            get isOpen() { return this.langOpen; },
            get isClosed() { return !this.langOpen; },
            get ariaExpanded() { return this.langOpen ? 'true' : 'false'; },
            get chevronClass() { return this.langOpen ? 'rotate-180' : ''; },

            toggle: function() {
                this.langOpen = !this.langOpen;
            },
            close: function() {
                this.langOpen = false;
            }
        };
    });

    // Auto-submit language select on change (mobile version)
    // Uses event delegation for CSP compliance
    // Also updates the 'next' URL to use the correct language prefix
    (function() {
        document.addEventListener('change', function(event) {
            if (event.target.classList.contains('lang-select-auto-submit')) {
                var form = event.target.closest('form');
                if (form) {
                    // Update the 'next' hidden input with the correct localized URL
                    var nextInput = form.querySelector('input[name="next"]');
                    var currentPath = form.dataset.currentPath || window.location.pathname;
                    var selectedLang = event.target.value;

                    if (nextInput && currentPath) {
                        // Replace language prefix in path (e.g., /en/about/ -> /de/about/)
                        // Pattern matches /xx/ at the start where xx is a 2-letter language code
                        var newPath = currentPath.replace(/^\/[a-z]{2}\//, '/' + selectedLang + '/');
                        // If path didn't have a language prefix, add one
                        if (newPath === currentPath && !currentPath.match(/^\/[a-z]{2}\//)) {
                            newPath = '/' + selectedLang + currentPath;
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
    Alpine.data('phoneVerificationModal', function() {
        return {
            isOpen: false,
            step: 'sending', // sending, code, verifying, success
            // CSP-compatible: individual properties instead of array (array index access requires eval)
            otp0: '',
            otp1: '',
            otp2: '',
            otp3: '',
            otp4: '',
            otp5: '',
            error: '',
            maskedPhone: '',
            resendCountdown: 60,
            resendTimer: null,

            // Computed getters for CSP compatibility
            get isSendingStep() { return this.step === 'sending'; },
            get isCodeStep() { return this.step === 'code'; },
            get isVerifyingStep() { return this.step === 'verifying'; },
            get isSuccessStep() { return this.step === 'success'; },
            get canResend() { return this.resendCountdown === 0; },
            get cannotResend() { return this.resendCountdown > 0; },
            get hasError() { return Boolean(this.error); },
            // CSP-compatible: getter combines individual OTP fields
            get otpCode() { return this.otp0 + this.otp1 + this.otp2 + this.otp3 + this.otp4 + this.otp5; },
            get isCodeComplete() { return this.otpCode.length === 6; },
            get isCodeIncomplete() { return this.otpCode.length !== 6; },

            // CSP-compatible: combined update + navigation handlers (no $event in template)
            // Each method updates its field and handles focus navigation
            handleOtp0Input: function() {
                var el = this.$refs.otp0;
                this.otp0 = el.value;
                if (el.value.length === 1) { this.$refs.otp1.focus(); }
            },
            handleOtp1Input: function() {
                var el = this.$refs.otp1;
                this.otp1 = el.value;
                if (el.value.length === 1) { this.$refs.otp2.focus(); }
            },
            handleOtp2Input: function() {
                var el = this.$refs.otp2;
                this.otp2 = el.value;
                if (el.value.length === 1) { this.$refs.otp3.focus(); }
            },
            handleOtp3Input: function() {
                var el = this.$refs.otp3;
                this.otp3 = el.value;
                if (el.value.length === 1) { this.$refs.otp4.focus(); }
            },
            handleOtp4Input: function() {
                var el = this.$refs.otp4;
                this.otp4 = el.value;
                if (el.value.length === 1) { this.$refs.otp5.focus(); }
            },
            handleOtp5Input: function() {
                var el = this.$refs.otp5;
                this.otp5 = el.value;
                if (el.value.length === 1) { this.verifyCode(); }
            },

            init: function() {
                // Listen for modal open event
                var self = this;
                window.addEventListener('open-phone-modal', function() {
                    self.open();
                });

                // Keyboard navigation: Escape key closes modal
                document.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape' && self.isOpen) {
                        self.close();
                    }
                });
            },

            open: function() {
                // Clear any existing timer before opening (prevents memory leak from reopening)
                if (this.resendTimer) {
                    clearInterval(this.resendTimer);
                    this.resendTimer = null;
                }
                this.isOpen = true;
                this.step = 'sending';
                this.error = '';
                // CSP-compatible: reset individual OTP fields
                this.otp0 = '';
                this.otp1 = '';
                this.otp2 = '';
                this.otp3 = '';
                this.otp4 = '';
                this.otp5 = '';
                this.resendCountdown = 0;

                // Prevent body scroll when modal is open
                document.body.style.overflow = 'hidden';
            },

            close: function() {
                this.isOpen = false;
                // Clear timer on close
                if (this.resendTimer) {
                    clearInterval(this.resendTimer);
                    this.resendTimer = null;
                }

                // Restore body scroll
                document.body.style.overflow = '';
            },

            showCodeStep: function(phone) {
                this.step = 'code';
                this.maskedPhone = phone.slice(0, 7) + '***' + phone.slice(-2);
                this.startResendTimer();
                var self = this;
                this.$nextTick(function() {
                    if (self.$refs && self.$refs.otp0) {
                        self.$refs.otp0.focus();
                    }
                });
            },

            // CSP-compatible: get index from data-index attribute on element
            handleOtpInput: function(event) {
                var el = event.target || this.$el;
                var index = parseInt(el.dataset.index, 10);
                var value = el.value;
                if (value.length === 1 && index < 5) {
                    var nextRef = this.$refs['otp' + (index + 1)];
                    if (nextRef) nextRef.focus();
                }
                if (index === 5 && value.length === 1) {
                    this.verifyCode();
                }
            },

            // CSP-compatible: get index from data-index attribute on element
            handleOtpBackspace: function(event) {
                var el = event.target || this.$el;
                var index = parseInt(el.dataset.index, 10);
                if (!el.value && index > 0) {
                    var prevRef = this.$refs['otp' + (index - 1)];
                    if (prevRef) prevRef.focus();
                }
            },

            // CSP-compatible: no parameter needed
            handleOtpPaste: function(event) {
                event.preventDefault();
                var paste = (event.clipboardData || window.clipboardData).getData('text');
                var digits = paste.replace(/\D/g, '').slice(0, 6).split('');
                // CSP-compatible: set individual OTP fields
                this.otp0 = digits[0] || '';
                this.otp1 = digits[1] || '';
                this.otp2 = digits[2] || '';
                this.otp3 = digits[3] || '';
                this.otp4 = digits[4] || '';
                this.otp5 = digits[5] || '';
                if (digits.length === 6) this.verifyCode();
            },

            verifyCode: function() {
                var self = this;
                var code = this.otpCode;
                if (code.length !== 6) {
                    this.error = 'Please enter the 6-digit code';
                    return;
                }

                this.step = 'verifying';
                this.error = '';

                if (window.phoneVerification) {
                    window.phoneVerification.verifyCode(code).then(function(result) {
                        if (result.success) {
                            self.step = 'success';
                            // Update phone verification state
                            window.dispatchEvent(new CustomEvent('phone-verified', { detail: result.phoneNumber }));
                            setTimeout(function() { self.close(); }, 2000);
                        } else {
                            self.step = 'code';
                            self.error = result.error;
                            // CSP-compatible: reset individual OTP fields
                            self.otp0 = '';
                            self.otp1 = '';
                            self.otp2 = '';
                            self.otp3 = '';
                            self.otp4 = '';
                            self.otp5 = '';
                        }
                    });
                }
            },

            resendCode: function() {
                var self = this;
                this.step = 'sending';
                if (window.phoneVerification) {
                    window.phoneVerification.sendVerificationCode().then(function() {
                        self.step = 'code';
                        self.startResendTimer();
                    });
                }
            },

            startResendTimer: function() {
                var self = this;
                this.resendCountdown = 60;
                if (this.resendTimer) clearInterval(this.resendTimer);
                this.resendTimer = setInterval(function() {
                    self.resendCountdown--;
                    if (self.resendCountdown <= 0) {
                        clearInterval(self.resendTimer);
                    }
                }, 1000);
            }
        };
    });

    // Standalone phone verification page component
    // Used on /verify-phone/ page for existing users
    // Full-page 3-step flow: phone input → OTP → success
    Alpine.data('standalonePhoneVerification', function() {
        return {
            // State
            step: 'phone', // 'phone', 'otp', 'success'
            phoneNumber: '',
            displayPhone: '',
            verifiedPhone: '',
            error: '',
            isLoading: false,
            iti: null,
            phoneInputId: '',
            nextUrl: '',

            // OTP fields (CSP-compatible individual properties)
            otp0: '', otp1: '', otp2: '', otp3: '', otp4: '', otp5: '',

            // Resend timer
            resendCountdown: 0,
            resendTimer: null,

            // Computed getters for CSP compatibility
            get isPhoneStep() { return this.step === 'phone'; },
            get isOtpStep() { return this.step === 'otp'; },
            get isSuccessStep() { return this.step === 'success'; },
            get hasError() { return this.error !== ''; },
            get isNotLoading() { return !this.isLoading; },
            get canResend() { return this.resendCountdown <= 0; },
            get cannotResend() { return this.resendCountdown > 0; },
            get otpCode() {
                return this.otp0 + this.otp1 + this.otp2 + this.otp3 + this.otp4 + this.otp5;
            },
            get isOtpComplete() { return this.otpCode.length === 6; },

            init: function() {
                var self = this;

                // Read config from data attributes
                this.phoneInputId = this.$el.getAttribute('data-phone-input-id') || 'phone_number';
                this.nextUrl = this.$el.getAttribute('data-next-url') || '';
                var currentPhone = this.$el.getAttribute('data-current-phone') || '';

                // Initialize intl-tel-input after DOM is ready
                this.$nextTick(function() {
                    self.initIntlTelInput(currentPhone);
                });

                // Listen for phone verification success
                window.addEventListener('phone-verified', function(e) {
                    self.verifiedPhone = e.detail;
                    self.step = 'success';
                    self.isLoading = false;
                    self.error = '';
                });
            },

            initIntlTelInput: function(currentPhone) {
                var self = this;
                var phoneInput = document.getElementById(this.phoneInputId);

                if (!phoneInput || typeof window.intlTelInput !== 'function') {
                    return;
                }

                // Clear input before init
                phoneInput.value = '';

                try {
                    this.iti = window.intlTelInput(phoneInput, {
                        initialCountry: "lu",
                        preferredCountries: ["lu", "de", "fr", "be"],
                        onlyCountries: ["lu", "de", "fr", "be", "nl", "ch", "at", "it", "es", "pt", "gb", "ie", "us", "ca", "se", "dz"],
                        separateDialCode: true,
                        nationalMode: false,
                        formatOnDisplay: true,
                        autoPlaceholder: "aggressive",
                        utilsScript: "https://cdn.jsdelivr.net/npm/intl-tel-input@18.5.3/build/js/utils.js"
                    });

                    window.itiInstance = this.iti;

                    // Set pre-filled value after utils load
                    this.iti.promise.then(function() {
                        phoneInput.style.setProperty('padding-left', '110px', 'important');

                        if (currentPhone) {
                            var num = currentPhone.trim().replace(/\s/g, '');
                            if (!num.startsWith('+')) {
                                if (num.startsWith('00')) {
                                    num = '+' + num.slice(2);
                                } else {
                                    num = '+352' + num.replace(/^0+/, '');
                                }
                            }
                            self.iti.setNumber(num);
                        }
                    });
                } catch (err) {
                    console.error('intl-tel-input init error:', err);
                }
            },

            sendCode: function() {
                var self = this;
                this.error = '';

                // Validate phone
                if (this.iti && !this.iti.isValidNumber()) {
                    var errorCode = this.iti.getValidationError();
                    var errorMessages = {
                        0: 'Invalid phone number format',
                        1: 'Invalid country code',
                        2: 'Phone number is too short',
                        3: 'Phone number is too long',
                        4: 'Invalid phone number'
                    };
                    this.error = errorMessages[errorCode] || 'Please enter a valid phone number';
                    return;
                }

                var phoneNumber = this.iti ? this.iti.getNumber() : document.getElementById(this.phoneInputId).value;
                this.displayPhone = phoneNumber;
                this.isLoading = true;

                if (window.phoneVerification) {
                    window.phoneVerification.sendVerificationCode(phoneNumber).then(function(result) {
                        self.isLoading = false;
                        if (result.success) {
                            self.step = 'otp';
                            self.startResendTimer();
                            self.$nextTick(function() {
                                var firstInput = document.getElementById('otp-0');
                                if (firstInput) firstInput.focus();
                            });
                        } else {
                            self.error = result.error || 'Failed to send code';
                        }
                    });
                }
            },

            verifyCode: function() {
                var self = this;
                var code = this.otpCode;

                if (code.length !== 6) {
                    this.error = 'Please enter the 6-digit code';
                    return;
                }

                this.isLoading = true;
                this.error = '';

                if (window.phoneVerification) {
                    window.phoneVerification.verifyCode(code).then(function(result) {
                        if (result.success) {
                            self.verifiedPhone = result.phone_number;
                            self.step = 'success';
                        } else {
                            self.error = result.error || 'Invalid code';
                            self.clearOtp();
                        }
                        self.isLoading = false;
                    });
                }
            },

            resendCode: function() {
                if (this.resendCountdown > 0) return;

                var self = this;
                this.isLoading = true;
                this.error = '';

                if (window.phoneVerification) {
                    window.phoneVerification.sendVerificationCode(this.displayPhone).then(function(result) {
                        self.isLoading = false;
                        if (result.success) {
                            self.startResendTimer();
                        } else {
                            self.error = result.error || 'Failed to resend code';
                        }
                    });
                }
            },

            changePhone: function() {
                this.step = 'phone';
                this.clearOtp();
                this.error = '';
                if (this.resendTimer) {
                    clearInterval(this.resendTimer);
                    this.resendTimer = null;
                }
                this.resendCountdown = 0;

                if (window.phoneVerification) {
                    window.phoneVerification.reset();
                }
            },

            clearOtp: function() {
                this.otp0 = '';
                this.otp1 = '';
                this.otp2 = '';
                this.otp3 = '';
                this.otp4 = '';
                this.otp5 = '';
            },

            // OTP input handlers (CSP-compatible)
            handleOtpInput: function(index, event) {
                var value = event.target.value.replace(/\D/g, '');
                if (value.length > 1) {
                    // Handle paste
                    this.handleOtpPaste(value);
                    return;
                }

                // Set the value
                this['otp' + index] = value;

                // Auto-advance to next field
                if (value && index < 5) {
                    var nextInput = document.getElementById('otp-' + (index + 1));
                    if (nextInput) nextInput.focus();
                }

                // Auto-submit when complete
                if (this.otpCode.length === 6) {
                    this.verifyCode();
                }
            },

            handleOtpKeydown: function(index, event) {
                // Handle backspace - go to previous field
                if (event.key === 'Backspace' && !this['otp' + index] && index > 0) {
                    var prevInput = document.getElementById('otp-' + (index - 1));
                    if (prevInput) {
                        prevInput.focus();
                        this['otp' + (index - 1)] = '';
                    }
                }
            },

            handleOtpPaste: function(pastedValue) {
                var digits = pastedValue.replace(/\D/g, '').slice(0, 6);
                for (var i = 0; i < 6; i++) {
                    this['otp' + i] = digits[i] || '';
                }
                // Focus last filled or first empty
                var focusIndex = Math.min(digits.length, 5);
                var focusInput = document.getElementById('otp-' + focusIndex);
                if (focusInput) focusInput.focus();

                // Auto-submit if complete
                if (digits.length === 6) {
                    this.verifyCode();
                }
            },

            startResendTimer: function() {
                var self = this;
                this.resendCountdown = 60;
                if (this.resendTimer) clearInterval(this.resendTimer);
                this.resendTimer = setInterval(function() {
                    self.resendCountdown--;
                    if (self.resendCountdown <= 0) {
                        clearInterval(self.resendTimer);
                        self.resendTimer = null;
                    }
                }, 1000);
            },

            destroy: function() {
                if (this.resendTimer) {
                    clearInterval(this.resendTimer);
                }
                if (this.iti) {
                    this.iti.destroy();
                }
            }
        };
    });

    // PWA Install Button component for membership page
    // CSP-compatible with computed getters
    Alpine.data('pwaInstallButton', function() {
        return {
            deferredPrompt: null,
            canInstall: false,
            isInstalled: false,
            showInstructions: false,
            instructions: '',

            // Computed getters for CSP compatibility
            get canInstallVisible() { return this.canInstall; },
            get isInstalledVisible() { return this.isInstalled; },
            get showInstructionsVisible() { return this.showInstructions; },

            init: function() {
                var self = this;

                // Check if already installed (standalone mode)
                if (window.matchMedia('(display-mode: standalone)').matches ||
                    window.navigator.standalone === true) {
                    self.isInstalled = true;
                    return;
                }

                // Check CrushPWA if available
                if (window.CrushPWA && window.CrushPWA.isStandalone) {
                    self.isInstalled = true;
                    return;
                }

                // Listen for beforeinstallprompt event (Chrome, Edge, Samsung)
                window.addEventListener('beforeinstallprompt', function(e) {
                    e.preventDefault();
                    self.deferredPrompt = e;
                    self.canInstall = true;
                });

                // Listen for appinstalled event
                window.addEventListener('appinstalled', function() {
                    self.isInstalled = true;
                    self.canInstall = false;
                    self.deferredPrompt = null;
                });

                // iOS-specific instructions (no beforeinstallprompt on iOS)
                var isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
                if (isIOS) {
                    self.showInstructions = true;
                    self.instructions = 'Tap Share, then "Add to Home Screen"';
                }
            },

            install: function() {
                var self = this;
                if (!self.deferredPrompt) return;

                self.deferredPrompt.prompt();
                self.deferredPrompt.userChoice.then(function(result) {
                    if (result.outcome === 'accepted') {
                        self.isInstalled = true;
                        self.canInstall = false;
                    }
                    self.deferredPrompt = null;
                });
            }
        };
    });

    // ==========================================================================
    // JOURNEY GIFT COMPONENTS
    // ==========================================================================

    // Gift Sharing Component for success page
    // Handles copy to clipboard, WhatsApp, email, and native share
    Alpine.data('giftShare', function() {
        return {
            giftUrl: '',
            giftCode: '',
            recipientName: '',
            copied: false,
            hasNativeShare: false,

            // Computed getters for CSP compatibility
            get copyButtonText() {
                return this.copied ? gettext('Copied!') : gettext('Copy Link');
            },
            get copyButtonClass() {
                return this.copied ? 'share-btn share-btn-copy copied' : 'share-btn share-btn-copy';
            },
            get showNativeShare() {
                return this.hasNativeShare;
            },
            get hideNativeShare() {
                return !this.hasNativeShare;
            },

            init: function() {
                // Read data from attributes
                this.giftUrl = this.$el.getAttribute('data-gift-url') || '';
                this.giftCode = this.$el.getAttribute('data-gift-code') || '';
                this.recipientName = this.$el.getAttribute('data-recipient') || '';

                // Check for native share API support
                this.hasNativeShare = typeof navigator.share === 'function';
            },

            copyLink: function() {
                var self = this;
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(this.giftUrl).then(function() {
                        self.copied = true;
                        setTimeout(function() {
                            self.copied = false;
                        }, 2000);
                    }).catch(function(err) {
                        console.error('Failed to copy:', err);
                        self.fallbackCopy();
                    });
                } else {
                    self.fallbackCopy();
                }
            },

            fallbackCopy: function() {
                var self = this;
                // Fallback for older browsers
                var textArea = document.createElement('textarea');
                textArea.value = this.giftUrl;
                textArea.style.position = 'fixed';
                textArea.style.left = '-9999px';
                document.body.appendChild(textArea);
                textArea.select();
                try {
                    document.execCommand('copy');
                    self.copied = true;
                    setTimeout(function() {
                        self.copied = false;
                    }, 2000);
                } catch (err) {
                    console.error('Fallback copy failed:', err);
                }
                document.body.removeChild(textArea);
            },

            shareWhatsApp: function() {
                var text = 'I created a magical Wonderland journey just for you! ' +
                           'Scan the QR code or click here to begin: ' + this.giftUrl;
                var url = 'https://wa.me/?text=' + encodeURIComponent(text);
                window.open(url, '_blank');
            },

            shareEmail: function() {
                var subject = gettext('A Magical Journey Awaits You!');
                var body = 'Hi ' + this.recipientName + ',\n\n' +
                          gettext('I created a special "Wonderland of You" journey just for you!') + '\n\n' +
                          'Click here to begin your adventure:\n' + this.giftUrl + '\n\n' +
                          'Or use gift code: ' + this.giftCode;
                var mailto = 'mailto:?subject=' + encodeURIComponent(subject) +
                            '&body=' + encodeURIComponent(body);
                window.location.href = mailto;
            },

            shareNative: function() {
                var self = this;
                if (navigator.share) {
                    navigator.share({
                        title: gettext('A Magical Journey Awaits!'),
                        text: gettext('I created a special Wonderland journey for') + ' ' + self.recipientName + '!',
                        url: self.giftUrl
                    }).catch(function() {
                        // Share was cancelled or failed
                    });
                }
            }
        };
    });

    // Gift Landing Page Component
    // Handles animated chapter reveal
    Alpine.data('giftLanding', function() {
        return {
            chaptersVisible: false,
            animationComplete: false,

            // Computed getters for CSP compatibility
            get chaptersReady() {
                return this.chaptersVisible;
            },
            get chaptersHidden() {
                return !this.chaptersVisible;
            },
            get animationDone() {
                return this.animationComplete;
            },

            init: function() {
                var self = this;
                // Delay chapter reveal for dramatic effect
                setTimeout(function() {
                    self.chaptersVisible = true;
                }, 800);

                // Mark animation complete after all chapters animate in
                setTimeout(function() {
                    self.animationComplete = true;
                }, 2500);
            }
        };
    });

    // Gift Create Form Component
    // Multi-step form for creating journey gifts with media uploads
    Alpine.data('giftCreateForm', function() {
        return {
            currentStep: 1,
            // Chapter 1 image state
            chapter1HasFileFlag: false,
            chapter1Preview: '',
            chapter1FileName: '',
            // Audio file state
            audioHasFileFlag: false,
            audioFileName: '',
            audioFileSize: '',
            audioFileType: '',
            audioError: '',
            // Video file state
            videoHasFileFlag: false,
            videoFileName: '',
            videoFileSize: '',
            videoFileType: '',
            videoError: '',

            // Allowed file types
            allowedAudioTypes: ['audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/x-m4a', 'audio/aac'],
            allowedVideoTypes: ['video/mp4', 'video/quicktime', 'video/x-m4v'],
            allowedAudioExtensions: ['.mp3', '.wav', '.m4a', '.aac'],
            allowedVideoExtensions: ['.mp4', '.mov', '.m4v'],

            // Computed getters for CSP compatibility
            get stepOneClass() {
                if (this.currentStep === 1) return 'active';
                if (this.currentStep > 1) return 'completed';
                return '';
            },
            get stepTwoClass() {
                if (this.currentStep === 2) return 'active';
                return '';
            },
            get stepOneContentClass() {
                return this.currentStep === 1 ? 'active' : '';
            },
            get stepTwoContentClass() {
                return this.currentStep === 2 ? 'active' : '';
            },
            get chapter1HasFile() {
                return this.chapter1HasFileFlag ? 'has-file' : '';
            },
            get chapter1PreviewClass() {
                return this.chapter1Preview ? 'show' : '';
            },
            get audioHasFile() {
                return this.audioHasFileFlag ? 'has-file' : '';
            },
            get audioInfoVisible() {
                return this.audioHasFileFlag;
            },
            get audioDefaultVisible() {
                return !this.audioHasFileFlag;
            },
            get audioHasError() {
                return this.audioError !== '';
            },
            get videoHasFile() {
                return this.videoHasFileFlag ? 'has-file' : '';
            },
            get videoInfoVisible() {
                return this.videoHasFileFlag;
            },
            get videoDefaultVisible() {
                return !this.videoHasFileFlag;
            },
            get videoHasError() {
                return this.videoError !== '';
            },

            init: function() {
                var self = this;
                // Listen for file changes on chapter1_image
                var ch1Input = document.getElementById('id_chapter1_image');
                if (ch1Input) {
                    ch1Input.addEventListener('change', function(e) {
                        self.handleChapter1FileChange(e);
                    });
                }

                // Listen for audio file changes
                var audioInput = document.getElementById('id_chapter4_audio');
                if (audioInput) {
                    audioInput.addEventListener('change', function(e) {
                        self.handleAudioFileChange(e);
                    });
                }

                // Listen for video file changes
                var videoInput = document.getElementById('id_chapter4_video');
                if (videoInput) {
                    videoInput.addEventListener('change', function(e) {
                        self.handleVideoFileChange(e);
                    });
                }

                // Add visual feedback for all file inputs
                var fileInputs = document.querySelectorAll('input[type="file"]');
                fileInputs.forEach(function(input) {
                    input.addEventListener('change', function(e) {
                        var wrapper = e.target.closest('.file-upload-wrapper');
                        if (wrapper) {
                            if (e.target.files && e.target.files.length > 0) {
                                wrapper.classList.add('has-file');
                            } else {
                                wrapper.classList.remove('has-file');
                            }
                        }
                    });
                });
            },

            formatFileSize: function(bytes) {
                if (bytes === 0) return '0 Bytes';
                var k = 1024;
                var sizes = ['Bytes', 'KB', 'MB', 'GB'];
                var i = Math.floor(Math.log(bytes) / Math.log(k));
                return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
            },

            getFileExtension: function(filename) {
                var ext = filename.slice((filename.lastIndexOf('.') - 1 >>> 0) + 2);
                return ext ? '.' + ext.toLowerCase() : '';
            },

            isValidAudioFile: function(file) {
                var ext = this.getFileExtension(file.name);
                var typeValid = this.allowedAudioTypes.indexOf(file.type) !== -1;
                var extValid = this.allowedAudioExtensions.indexOf(ext) !== -1;
                return typeValid || extValid;
            },

            isValidVideoFile: function(file) {
                var ext = this.getFileExtension(file.name);
                var typeValid = this.allowedVideoTypes.indexOf(file.type) !== -1;
                var extValid = this.allowedVideoExtensions.indexOf(ext) !== -1;
                return typeValid || extValid;
            },

            goToStep2: function() {
                // Basic validation before proceeding
                var recipientName = document.getElementById('id_recipient_name');
                var dateMet = document.getElementById('id_date_first_met');
                var locationMet = document.getElementById('id_location_first_met');

                var isValid = true;

                if (!recipientName.value.trim()) {
                    recipientName.classList.add('is-invalid');
                    isValid = false;
                } else {
                    recipientName.classList.remove('is-invalid');
                }

                if (!dateMet.value) {
                    dateMet.classList.add('is-invalid');
                    isValid = false;
                } else {
                    dateMet.classList.remove('is-invalid');
                }

                if (!locationMet.value.trim()) {
                    locationMet.classList.add('is-invalid');
                    isValid = false;
                } else {
                    locationMet.classList.remove('is-invalid');
                }

                if (isValid) {
                    this.currentStep = 2;
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            },

            goToStep1: function() {
                this.currentStep = 1;
                window.scrollTo({ top: 0, behavior: 'smooth' });
            },

            handleChapter1FileChange: function(event) {
                var file = event.target.files[0];
                if (file) {
                    this.chapter1HasFileFlag = true;
                    this.chapter1FileName = file.name;

                    // Create preview for images
                    if (file.type.startsWith('image/')) {
                        var reader = new FileReader();
                        var self = this;
                        reader.onload = function(e) {
                            self.chapter1Preview = e.target.result;
                        };
                        reader.readAsDataURL(file);
                    }
                } else {
                    this.chapter1HasFileFlag = false;
                    this.chapter1Preview = '';
                    this.chapter1FileName = '';
                }
            },

            handleAudioFileChange: function(event) {
                var file = event.target.files[0];
                this.audioError = '';

                if (file) {
                    // Validate file type
                    if (!this.isValidAudioFile(file)) {
                        this.audioError = 'Invalid audio format. Please use MP3, WAV, or M4A files.';
                        this.audioHasFileFlag = false;
                        this.audioFileName = '';
                        this.audioFileSize = '';
                        this.audioFileType = '';
                        event.target.value = '';
                        return;
                    }

                    // Validate file size (10MB max)
                    if (file.size > 10 * 1024 * 1024) {
                        this.audioError = 'Audio file is too large. Maximum size is 10 MB.';
                        this.audioHasFileFlag = false;
                        this.audioFileName = '';
                        this.audioFileSize = '';
                        this.audioFileType = '';
                        event.target.value = '';
                        return;
                    }

                    this.audioHasFileFlag = true;
                    this.audioFileName = file.name;
                    this.audioFileSize = this.formatFileSize(file.size);
                    this.audioFileType = file.type || 'audio';
                } else {
                    this.audioHasFileFlag = false;
                    this.audioFileName = '';
                    this.audioFileSize = '';
                    this.audioFileType = '';
                }
            },

            handleVideoFileChange: function(event) {
                var file = event.target.files[0];
                this.videoError = '';

                if (file) {
                    // Validate file type
                    if (!this.isValidVideoFile(file)) {
                        this.videoError = 'Invalid video format. Please use MP4 or MOV files.';
                        this.videoHasFileFlag = false;
                        this.videoFileName = '';
                        this.videoFileSize = '';
                        this.videoFileType = '';
                        event.target.value = '';
                        return;
                    }

                    // Validate file size (50MB max)
                    if (file.size > 50 * 1024 * 1024) {
                        this.videoError = 'Video file is too large. Maximum size is 50 MB.';
                        this.videoHasFileFlag = false;
                        this.videoFileName = '';
                        this.videoFileSize = '';
                        this.videoFileType = '';
                        event.target.value = '';
                        return;
                    }

                    this.videoHasFileFlag = true;
                    this.videoFileName = file.name;
                    this.videoFileSize = this.formatFileSize(file.size);
                    this.videoFileType = file.type || 'video';
                } else {
                    this.videoHasFileFlag = false;
                    this.videoFileName = '';
                    this.videoFileSize = '';
                    this.videoFileType = '';
                }
            }
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
    Alpine.data('journeyState', function() {
        return {
            startTime: Date.now(),
            lastSaveTime: Date.now(),
            totalTimeSeconds: 0,
            currentPoints: 0,
            saveUrl: '',
            saveInterval: null,

            init: function() {
                var el = this.$el;
                this.saveUrl = el.dataset.saveUrl || '';
                this.totalTimeSeconds = parseInt(el.dataset.initialTime, 10) || 0;
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                // Start auto-save interval (every 30 seconds)
                var self = this;
                this.saveInterval = setInterval(function() {
                    self.saveState();
                }, 30000);

                // Save on page unload
                window.addEventListener('beforeunload', function() {
                    self.saveStateBeacon();
                });
            },

            saveState: function() {
                if (!this.saveUrl) return;

                var now = Date.now();
                var timeIncrement = Math.floor((now - this.lastSaveTime) / 1000);
                var self = this;

                if (timeIncrement > 0) {
                    fetch(this.saveUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': CrushUtils.getCsrfToken()
                        },
                        body: JSON.stringify({
                            time_increment: timeIncrement
                        })
                    })
                    .then(function(response) { return response.json(); })
                    .then(function(data) {
                        if (data.success) {
                            self.lastSaveTime = now;
                            self.totalTimeSeconds = data.total_time;
                        }
                    })
                    .catch(function(error) {
                        console.error('Error saving state:', error);
                    });
                }
            },

            saveStateBeacon: function() {
                if (!this.saveUrl) return;

                var timeIncrement = Math.floor((Date.now() - this.lastSaveTime) / 1000);
                if (timeIncrement > 0) {
                    var formData = new FormData();
                    formData.append('time_increment', timeIncrement);
                    formData.append('csrfmiddlewaretoken', CrushUtils.getCsrfToken());
                    navigator.sendBeacon(this.saveUrl, formData);
                }
            },

            destroy: function() {
                if (this.saveInterval) {
                    clearInterval(this.saveInterval);
                }
            }
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
    Alpine.data('riddleChallenge', function() {
        return {
            challengeId: 0,
            answer: '',
            isSubmitting: false,
            feedback: '',
            feedbackType: '',
            feedbackHtml: '',
            currentPoints: 0,
            submitUrl: '',
            hintUrl: '',
            chapterUrl: '',
            hintsUsed: [],
            // Date input support
            inputType: '',
            dateFormat: 'DD/MM/YYYY',
            dateValue: '',
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
            feedbackClass: 'hidden',
            // Hint state properties
            hint1Used: false,
            hint2Used: false,
            hint3Used: false,
            hint1NotUsed: true,
            hint2NotUsed: true,
            hint3NotUsed: true,
            hint1BtnClass: '',
            hint2BtnClass: '',
            hint3BtnClass: '',
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext('Correct!'),
                pointsEarned: gettext('Points Earned:'),
                continue: gettext('Continue'),
                errorDefault: gettext('Not quite right. Try again!'),
                errorGeneric: gettext('An error occurred. Please try again.')
            },

            init: function() {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || '';
                this.hintUrl = el.dataset.hintUrl || '';
                this.chapterUrl = el.dataset.chapterUrl || '';
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                // Date input configuration
                this.inputType = el.dataset.inputType || '';
                this.dateFormat = el.dataset.dateFormat || 'DD/MM/YYYY';
                this.isDateInput = this.inputType === 'date';
                this.isTextInput = !this.isDateInput;

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nPointsEarned) this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue) this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault) this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric) this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch('answer', function() { self._updateSubmitState(); });
                this.$watch('isSubmitting', function() { self._updateSubmitState(); });
                this.$watch('feedback', function() { self._updateFeedbackState(); });
                this.$watch('feedbackHtml', function() { self._updateFeedbackState(); });
                this.$watch('feedbackType', function() { self._updateFeedbackState(); });
                this.$watch('hintsUsed', function() { self._updateHintState(); });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function() {
                var hasAnswer = this.answer.trim().length > 0;
                var canSubmit = hasAnswer && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function() {
                this.showFeedback = this.feedback !== '' || this.feedbackHtml !== '';
                this.isSuccess = this.feedbackType === 'success';
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === 'error';
                this.isNotError = !this.isError;
                if (this.feedbackType === 'success') {
                    this.feedbackClass = 'journey-message-success p-6 text-center';
                } else if (this.feedbackType === 'error') {
                    this.feedbackClass = 'journey-message-error p-6 text-center';
                } else {
                    this.feedbackClass = 'hidden';
                }
            },

            _updateHintState: function() {
                this.hint1Used = this.hintsUsed.indexOf(1) !== -1;
                this.hint2Used = this.hintsUsed.indexOf(2) !== -1;
                this.hint3Used = this.hintsUsed.indexOf(3) !== -1;
                this.hint1NotUsed = !this.hint1Used;
                this.hint2NotUsed = !this.hint2Used;
                this.hint3NotUsed = !this.hint3Used;
                this.hint1BtnClass = this.hint1Used ? 'opacity-50 cursor-not-allowed' : '';
                this.hint2BtnClass = this.hint2Used ? 'opacity-50 cursor-not-allowed' : '';
                this.hint3BtnClass = this.hint3Used ? 'opacity-50 cursor-not-allowed' : '';
            },

            submitAnswer: function() {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = '';
                this.feedbackHtml = '';

                fetch(this.submitUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': CrushUtils.getCsrfToken()
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim()
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success && data.is_correct) {
                        self.feedbackType = 'success';
                        self.feedbackHtml = self.buildSuccessHtml(data);
                    } else {
                        self.feedbackType = 'error';
                        self.feedback = data.message || self.i18n.errorDefault;
                        self.answer = '';
                        self.isSubmitting = false;
                        self.shakeInput();
                    }
                })
                .catch(function(error) {
                    console.error('Error:', error);
                    self.feedbackType = 'error';
                    self.feedback = self.i18n.errorGeneric;
                    self.isSubmitting = false;
                });
            },

            buildSuccessHtml: function(data) {
                return '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    ' ' + this.i18n.correct + '</h3>' +
                    '<p class="mb-4">' + (data.success_message || '') + '</p>' +
                    '<p class="font-bold mb-6">🏆 ' + this.i18n.pointsEarned + ' ' + data.points_earned + '</p>' +
                    '<a href="' + this.chapterUrl + '" class="journey-btn-primary">' +
                    this.i18n.continue + ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    '</a>';
            },

            shakeInput: function() {
                var input = this.$el.querySelector('.journey-input');
                if (input) {
                    input.classList.add('journey-animate-shake');
                    setTimeout(function() {
                        input.classList.remove('journey-animate-shake');
                    }, 500);
                }
            },

            unlockHint: function(hintNum, cost) {
                if (this.hintsUsed.indexOf(hintNum) !== -1) return;

                var self = this;
                fetch(this.hintUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': CrushUtils.getCsrfToken()
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        hint_number: hintNum
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success) {
                        self.hintsUsed.push(hintNum);
                        self.currentPoints -= cost;

                        // Dispatch event for hint box to show content
                        self.$dispatch('hint-unlocked', {
                            hintNum: hintNum,
                            hintText: data.hint_text
                        });
                    }
                })
                .catch(function(error) {
                    console.error('Error:', error);
                });
            },

            isHintUsed: function(hintNum) {
                return this.hintsUsed.indexOf(hintNum) !== -1;
            },

            // CSP-safe: update answer from input event
            updateAnswer: function(event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value;
                } else {
                    var input = this.$el.querySelector('#answerInput');
                    if (input) this.answer = input.value;
                }
            },

            // CSP-safe: wrapper to unlock hint from button click
            // Reads hint number and cost from data attributes
            unlockHintFromButton: function(event) {
                var button = event && event.currentTarget ? event.currentTarget : null;
                if (!button) return;

                var hintNum = parseInt(button.dataset.hintNum, 10) || 0;
                var cost = parseInt(button.dataset.hintCost, 10) || 0;
                if (hintNum > 0) {
                    this.unlockHint(hintNum, cost);
                }
            },

            // CSP-safe: handle keydown to submit on Enter only
            handleKeydown: function(event) {
                if (event && event.key === 'Enter') {
                    event.preventDefault();
                    this.submitAnswer();
                }
            },

            // CSP-safe: update answer from date input
            // Formats date according to dateFormat setting (DD/MM/YYYY by default)
            updateDateAnswer: function(event) {
                if (event && event.target) {
                    var dateStr = event.target.value; // YYYY-MM-DD format from date input
                    this.dateValue = dateStr;

                    if (dateStr) {
                        var parts = dateStr.split('-');
                        var year = parts[0];
                        var month = parts[1];
                        var day = parts[2];

                        // Format according to dateFormat
                        // Supports: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, MM/DD/YYYY
                        var format = this.dateFormat.toUpperCase();
                        var separator = '/';
                        if (format.indexOf('-') !== -1) separator = '-';
                        else if (format.indexOf('.') !== -1) separator = '.';

                        if (format.indexOf('MM') < format.indexOf('DD')) {
                            // MM/DD/YYYY format
                            this.answer = month + separator + day + separator + year;
                        } else {
                            // DD/MM/YYYY format (default)
                            this.answer = day + separator + month + separator + year;
                        }
                    } else {
                        this.answer = '';
                    }
                }
            }
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
    Alpine.data('wordScramble', function() {
        return {
            challengeId: 0,
            answer: '',
            isSubmitting: false,
            feedback: '',
            feedbackType: '',
            feedbackHtml: '',
            currentPoints: 0,
            submitUrl: '',
            hintUrl: '',
            chapterUrl: '',
            hintsUsed: [],
            scrambledWords: [],
            displayText: '',
            // CSP-safe: plain data properties instead of getters
            submitBtnDisabled: true,
            showFeedback: false,
            isSuccess: false,
            isNotSuccess: true,
            isError: false,
            isNotError: true,
            isNotSubmitting: true,
            feedbackClass: 'hidden',
            // Hint state properties
            hint1Used: false,
            hint2Used: false,
            hint3Used: false,
            hint1NotUsed: true,
            hint2NotUsed: true,
            hint3NotUsed: true,
            hint1BtnClass: '',
            hint2BtnClass: '',
            hint3BtnClass: '',
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext('Correct!'),
                pointsEarned: gettext('Points Earned:'),
                continue: gettext('Continue'),
                errorDefault: gettext('Not quite right. Try again!'),
                errorGeneric: gettext('An error occurred. Please try again.')
            },

            init: function() {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || '';
                this.hintUrl = el.dataset.hintUrl || '';
                this.chapterUrl = el.dataset.chapterUrl || '';
                this.currentPoints = parseInt(el.dataset.initialPoints, 10) || 0;

                var scrambled = el.dataset.scrambledWords || '';
                this.scrambledWords = scrambled.split(/\s+/).filter(function(w) { return w.trim(); });
                this.displayText = this.scrambledWords.join('  •  ');

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nPointsEarned) this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue) this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault) this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric) this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch('answer', function() { self._updateSubmitState(); });
                this.$watch('isSubmitting', function() { self._updateSubmitState(); });
                this.$watch('feedback', function() { self._updateFeedbackState(); });
                this.$watch('feedbackHtml', function() { self._updateFeedbackState(); });
                this.$watch('feedbackType', function() { self._updateFeedbackState(); });
                this.$watch('hintsUsed', function() { self._updateHintState(); });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function() {
                var hasAnswer = this.answer.trim().length > 0;
                var canSubmit = hasAnswer && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function() {
                this.showFeedback = this.feedback !== '' || this.feedbackHtml !== '';
                this.isSuccess = this.feedbackType === 'success';
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === 'error';
                this.isNotError = !this.isError;
                if (this.feedbackType === 'success') {
                    this.feedbackClass = 'journey-message-success p-6 text-center';
                } else if (this.feedbackType === 'error') {
                    this.feedbackClass = 'journey-message-error p-6 text-center';
                } else {
                    this.feedbackClass = 'hidden';
                }
            },

            _updateHintState: function() {
                this.hint1Used = this.hintsUsed.indexOf(1) !== -1;
                this.hint2Used = this.hintsUsed.indexOf(2) !== -1;
                this.hint3Used = this.hintsUsed.indexOf(3) !== -1;
                this.hint1NotUsed = !this.hint1Used;
                this.hint2NotUsed = !this.hint2Used;
                this.hint3NotUsed = !this.hint3Used;
                this.hint1BtnClass = this.hint1Used ? 'opacity-50 cursor-not-allowed' : '';
                this.hint2BtnClass = this.hint2Used ? 'opacity-50 cursor-not-allowed' : '';
                this.hint3BtnClass = this.hint3Used ? 'opacity-50 cursor-not-allowed' : '';
            },

            shuffleWords: function() {
                var previousOrder = this.scrambledWords.join(' ');
                var newOrder;
                var attempts = 0;

                // Fisher-Yates shuffle for letters within each word
                do {
                    newOrder = this.scrambledWords.map(function(word) {
                        var letters = word.split('');
                        // Fisher-Yates shuffle on letters array
                        for (var i = letters.length - 1; i > 0; i--) {
                            var j = Math.floor(Math.random() * (i + 1));
                            var temp = letters[i];
                            letters[i] = letters[j];
                            letters[j] = temp;
                        }
                        return letters.join('');
                    });
                    // Track previous iteration to avoid duplicate shuffles
                    if (newOrder.join(' ') !== previousOrder) {
                        previousOrder = newOrder.join(' ');
                    }
                    attempts++;
                } while (newOrder.join(' ') === this.scrambledWords.join(' ') && attempts < 50);

                this.scrambledWords = newOrder;
                this.displayText = this.scrambledWords.join('  •  ');
            },

            submitAnswer: function() {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = '';
                this.feedbackHtml = '';

                fetch(this.submitUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': CrushUtils.getCsrfToken()
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim()
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success && data.is_correct) {
                        self.feedbackType = 'success';
                        self.feedbackHtml = self.buildSuccessHtml(data);
                    } else {
                        self.feedbackType = 'error';
                        self.feedback = data.message || self.i18n.errorDefault;
                        self.answer = '';
                        self.isSubmitting = false;
                        self.shakeInput();
                    }
                })
                .catch(function(error) {
                    console.error('Error:', error);
                    self.feedbackType = 'error';
                    self.feedback = self.i18n.errorGeneric;
                    self.isSubmitting = false;
                });
            },

            buildSuccessHtml: function(data) {
                return '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    ' ' + this.i18n.correct + '</h3>' +
                    '<p class="mb-4">' + (data.success_message || '') + '</p>' +
                    '<p class="font-bold mb-6">🏆 ' + this.i18n.pointsEarned + ' ' + data.points_earned + '</p>' +
                    '<a href="' + this.chapterUrl + '" class="journey-btn-primary">' +
                    this.i18n.continue + ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    '</a>';
            },

            shakeInput: function() {
                var input = this.$el.querySelector('.journey-input');
                if (input) {
                    input.classList.add('journey-animate-shake');
                    setTimeout(function() {
                        input.classList.remove('journey-animate-shake');
                    }, 500);
                }
            },

            unlockHint: function(hintNum, cost) {
                if (this.hintsUsed.indexOf(hintNum) !== -1) return;

                var self = this;
                fetch(this.hintUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': CrushUtils.getCsrfToken()
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        hint_number: hintNum
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success) {
                        self.hintsUsed.push(hintNum);
                        self.currentPoints -= cost;
                        self.$dispatch('hint-unlocked', {
                            hintNum: hintNum,
                            hintText: data.hint_text
                        });
                    }
                })
                .catch(function(error) {
                    console.error('Error:', error);
                });
            },

            isHintUsed: function(hintNum) {
                return this.hintsUsed.indexOf(hintNum) !== -1;
            },

            // CSP-safe: update answer from input event
            updateAnswer: function(event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value.toUpperCase();
                } else {
                    var input = this.$el.querySelector('#answerInput');
                    if (input) this.answer = input.value.toUpperCase();
                }
            },

            // CSP-safe: wrapper to unlock hint from button click
            unlockHintFromButton: function(event) {
                var button = event && event.currentTarget ? event.currentTarget : null;
                if (!button) return;

                var hintNum = parseInt(button.dataset.hintNum, 10) || 0;
                var cost = parseInt(button.dataset.hintCost, 10) || 0;
                if (hintNum > 0) {
                    this.unlockHint(hintNum, cost);
                }
            },

            // CSP-safe: handle keydown to submit on Enter only
            handleKeydown: function(event) {
                if (event && event.key === 'Enter') {
                    event.preventDefault();
                    this.submitAnswer();
                }
            }
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
    Alpine.data('multipleChoice', function() {
        return {
            challengeId: 0,
            selectedOption: null,
            isSubmitting: false,
            feedback: '',
            feedbackType: '',
            feedbackHtml: '',
            submitUrl: '',
            chapterUrl: '',
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
            feedbackClass: 'hidden mt-6',
            // i18n translations (loaded from data attributes or gettext)
            i18n: {
                correct: gettext('Correct!'),
                thankYou: gettext('Thank you for sharing!'),
                pointsEarned: gettext('Points Earned:'),
                continue: gettext('Continue'),
                errorDefault: gettext('Not quite right! Try a different answer.'),
                errorGeneric: gettext('An error occurred. Please try again.')
            },

            init: function() {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || '';
                this.chapterUrl = el.dataset.chapterUrl || '';
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;

                // Load i18n translations from data attributes
                if (el.dataset.i18nCorrect) this.i18n.correct = el.dataset.i18nCorrect;
                if (el.dataset.i18nThankYou) this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned) this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue) this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault) this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric) this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch('selectedOption', function() { self._updateSubmitState(); });
                this.$watch('isSubmitting', function() { self._updateSubmitState(); });
                this.$watch('feedback', function() { self._updateFeedbackState(); });
                this.$watch('feedbackHtml', function() { self._updateFeedbackState(); });
                this.$watch('feedbackType', function() { self._updateFeedbackState(); });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function() {
                this.hasSelection = this.selectedOption !== null;
                this.hasNoSelection = !this.hasSelection;
                var canSubmit = this.hasSelection && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
                this.showSubmitLabel = this.hasSelection && this.isNotSubmitting;
                this._syncOptionState();
            },

            _syncOptionState: function() {
                var selected = this.selectedOption;
                var cards = this.$el.querySelectorAll('.option-card');
                cards.forEach(function(card) {
                    var isSelected = selected && card.dataset.optionKey === selected;
                    card.classList.toggle('selected', !!isSelected);
                    card.setAttribute('aria-checked', isSelected ? 'true' : 'false');
                });
            },

            _updateFeedbackState: function() {
                this.showFeedback = this.feedback !== '' || this.feedbackHtml !== '';
                this.isSuccess = this.feedbackType === 'success';
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === 'error';
                this.isNotError = !this.isError;
                if (this.feedbackType === 'success') {
                    this.feedbackClass = 'journey-message-success p-6 text-center mt-6';
                } else if (this.feedbackType === 'error') {
                    this.feedbackClass = 'journey-message-error p-6 text-center mt-6';
                } else {
                    this.feedbackClass = 'hidden mt-6';
                }
            },

            selectOption: function(optionKey, event) {
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
                target.classList.add('selected');
                target.setAttribute('aria-checked', 'true');
                this.selectedOption = optionKey;
            },

            isSelected: function(optionKey) {
                return this.selectedOption === optionKey;
            },

            clearSelection: function() {
                var cards = this.$el.querySelectorAll('.option-card');
                cards.forEach(function(card) {
                    card.classList.remove('selected');
                    card.setAttribute('aria-checked', 'false');
                    card.blur();
                });
            },

            submitAnswer: function() {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = '';
                this.feedbackHtml = '';

                fetch(this.submitUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': CrushUtils.getCsrfToken()
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.selectedOption
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success && data.is_correct) {
                        self.feedbackType = 'success';
                        self.feedbackHtml = self.buildSuccessHtml(data);
                    } else {
                        self.feedbackType = 'error';
                        self.feedback = self.i18n.errorDefault;
                        self.markIncorrect();
                        self.selectedOption = null;
                        self.isSubmitting = false;

                        // Auto-hide error after 3 seconds
                        setTimeout(function() {
                            if (self.feedbackType === 'error') {
                                self.feedback = '';
                                self.feedbackType = '';
                                // Explicitly update feedback state to trigger visual updates
                                self._updateFeedbackState();
                            }
                        }, 3000);
                    }
                })
                .catch(function(error) {
                    console.error('Error:', error);
                    self.feedbackType = 'error';
                    self.feedback = self.i18n.errorGeneric;
                    self.isSubmitting = false;
                });
            },

            buildSuccessHtml: function(data) {
                var isChapter2 = this.chapterNumber === 2;
                var iconHtml = isChapter2
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = isChapter2 ? this.i18n.thankYou : this.i18n.correct;

                return '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' + iconHtml + ' ' + title + '</h3>' +
                    '<div class="personal-message">' + (data.success_message || '') + '</div>' +
                    '<p class="font-bold my-4">🏆 ' + this.i18n.pointsEarned + ' ' + data.points_earned + '</p>' +
                    '<a href="' + this.chapterUrl + '" class="journey-btn-primary">' +
                    this.i18n.continue + ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    '</a>';
            },

            markIncorrect: function() {
                var selectedCard = this.$el.querySelector('.option-card.selected');
                if (selectedCard) {
                    selectedCard.classList.remove('selected');
                    selectedCard.classList.add('incorrect', 'journey-animate-shake');
                    setTimeout(function() {
                        selectedCard.classList.remove('incorrect', 'journey-animate-shake');
                    }, 1000);
                }
            },

            handleKeypress: function(optionKey, event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: select option from element click
            selectOptionFromElement: function(event) {
                var element = event && event.currentTarget ? event.currentTarget : null;
                if (!element) return;

                var optionKey = element.dataset.optionKey || '';
                if (optionKey) {
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: handle keydown on option cards
            handleOptionKeydown: function(event) {
                if (event && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault();
                    this.selectOptionFromElement(event);
                }
            }
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
    Alpine.data('timelineSort', function() {
        return {
            challengeId: 0,
            isSubmitting: false,
            feedback: '',
            feedbackType: '',
            feedbackHtml: '',
            submitUrl: '',
            chapterUrl: '',
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
            feedbackClass: 'hidden mt-6',
            instructionText: 'Drag and drop the events to arrange them in chronological order',
            // i18n translations (loaded from data attributes)
            i18n: {
                perfect: gettext('Perfect!'),
                pointsEarned: gettext('Points Earned:'),
                continue: gettext('Continue'),
                errorDefault: gettext('Not quite right. Try rearranging the events!'),
                errorGeneric: gettext('An error occurred. Please try again.'),
                instructionDesktop: gettext('Drag and drop the events to arrange them in chronological order'),
                instructionTouch: 'Touch and drag the events to arrange them in chronological order'
            },

            init: function() {
                var el = this.$el;
                var self = this;

                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || '';
                this.chapterUrl = el.dataset.chapterUrl || '';
                this.isTouchDevice = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);

                // Load i18n translations from data attributes
                if (el.dataset.i18nPerfect) this.i18n.perfect = el.dataset.i18nPerfect;
                if (el.dataset.i18nPointsEarned) this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue) this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorDefault) this.i18n.errorDefault = el.dataset.i18nErrorDefault;
                if (el.dataset.i18nErrorGeneric) this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;
                if (el.dataset.i18nInstructionDesktop) this.i18n.instructionDesktop = el.dataset.i18nInstructionDesktop;
                if (el.dataset.i18nInstructionTouch) this.i18n.instructionTouch = el.dataset.i18nInstructionTouch;

                // CSP-safe: update instruction text based on device type
                this._updateInstructionText();

                // CSP-safe: use $watch to update derived state
                this.$watch('isSubmitting', function() { self._updateSubmitState(); });
                this.$watch('feedback', function() { self._updateFeedbackState(); });
                this.$watch('feedbackHtml', function() { self._updateFeedbackState(); });
                this.$watch('feedbackType', function() { self._updateFeedbackState(); });

                // Initialize Sortable.js after DOM is ready
                this.$nextTick(function() {
                    self.initSortable();
                    self.shuffleItems();

                    // CSP-safe: Manually bind click handler as fallback
                    // Alpine CSP build sometimes fails to bind @click on dynamically shown elements
                    var submitBtn = el.querySelector('button.journey-btn-primary');
                    if (submitBtn) {
                        submitBtn.addEventListener('click', function(e) {
                            e.preventDefault();
                            self.submitAnswer();
                        });
                    }
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function() {
                var canSubmit = !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function() {
                this.showFeedback = this.feedback !== '' || this.feedbackHtml !== '';
                this.isSuccess = this.feedbackType === 'success';
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === 'error';
                this.isNotError = !this.isError;
                if (this.feedbackType === 'success') {
                    this.feedbackClass = 'journey-message-success p-6 text-center mt-6';
                } else if (this.feedbackType === 'error') {
                    this.feedbackClass = 'journey-message-error p-6 text-center mt-6';
                } else {
                    this.feedbackClass = 'hidden mt-6';
                }
            },

            _updateInstructionText: function() {
                this.instructionText = this.isTouchDevice
                    ? this.i18n.instructionTouch
                    : this.i18n.instructionDesktop;
            },

            initSortable: function() {
                var timelineItems = this.$el.querySelector('#timelineItems');
                if (!timelineItems || typeof Sortable === 'undefined') return;

                var self = this;
                this.sortable = new Sortable(timelineItems, {
                    animation: 200,
                    easing: "cubic-bezier(1, 0, 0, 1)",
                    ghostClass: 'sortable-ghost',
                    chosenClass: 'sortable-chosen',
                    dragClass: 'sortable-drag',
                    handle: '.timeline-item',
                    forceFallback: false,
                    fallbackTolerance: 3,
                    touchStartThreshold: 5,
                    delay: 0,
                    delayOnTouchOnly: true,
                    onEnd: function() {
                        self.updateNumbers();
                    }
                });
            },

            updateNumbers: function() {
                var items = this.$el.querySelectorAll('.timeline-item');
                items.forEach(function(item, index) {
                    var numberEl = item.querySelector('.timeline-number');
                    if (numberEl) {
                        numberEl.textContent = index + 1;
                    }
                });
            },

            shuffleItems: function() {
                var timelineItems = this.$el.querySelector('#timelineItems');
                if (!timelineItems) return;

                var items = Array.from(timelineItems.children);
                // Fisher-Yates shuffle
                for (var i = items.length - 1; i > 0; i--) {
                    var j = Math.floor(Math.random() * (i + 1));
                    timelineItems.appendChild(items[j]);
                }
                this.updateNumbers();
            },

            submitAnswer: function() {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var timelineItems = this.$el.querySelector('#timelineItems');
                if (!timelineItems) return;

                var items = timelineItems.querySelectorAll('.timeline-item');
                var order = Array.from(items).map(function(item) {
                    return item.dataset.originalIndex;
                });
                var answer = order.join(',');

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = '';
                this.feedbackHtml = '';

                fetch(this.submitUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': CrushUtils.getCsrfToken()
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: answer
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success && data.is_correct) {
                        self.feedbackType = 'success';
                        self.feedbackHtml = self.buildSuccessHtml(data);
                        self.disableSorting();
                    } else {
                        self.feedbackType = 'error';
                        self.feedback = self.i18n.errorDefault;
                        self.isSubmitting = false;

                        // Auto-hide error after 3 seconds
                        setTimeout(function() {
                            if (self.feedbackType === 'error') {
                                self.feedback = '';
                                self.feedbackType = '';
                                // Explicitly update feedback state to trigger visual updates
                                self._updateFeedbackState();
                            }
                        }, 3000);
                    }
                })
                .catch(function(error) {
                    console.error('Error:', error);
                    self.feedbackType = 'error';
                    self.feedback = self.i18n.errorGeneric;
                    self.isSubmitting = false;
                });
            },

            buildSuccessHtml: function(data) {
                return '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' +
                    '<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>' +
                    ' ' + this.i18n.perfect + '</h3>' +
                    '<p class="text-lg my-5">' + (data.success_message || '') + '</p>' +
                    '<p class="font-bold my-4">🏆 ' + this.i18n.pointsEarned + ' ' + data.points_earned + '</p>' +
                    '<a href="' + this.chapterUrl + '" class="journey-btn-primary">' +
                    this.i18n.continue + ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    '</a>';
            },

            disableSorting: function() {
                if (this.sortable) {
                    this.sortable.option('disabled', true);
                }
                var items = this.$el.querySelectorAll('.timeline-item');
                items.forEach(function(item) {
                    item.style.cursor = 'default';
                });
            }
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
    Alpine.data('wouldYouRather', function() {
        return {
            challengeId: 0,
            selectedOption: null,
            isSubmitting: false,
            feedback: '',
            feedbackType: '',
            feedbackHtml: '',
            submitUrl: '',
            chapterUrl: '',
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
            feedbackClass: 'hidden mt-6',
            // i18n translations (loaded from data attributes)
            i18n: {
                greatChoice: gettext('Great choice!'),
                thankYou: gettext('Thank you for sharing!'),
                pointsEarned: gettext('Points Earned:'),
                continue: gettext('Continue'),
                errorGeneric: gettext('An error occurred. Please try again.')
            },

            init: function() {
                var el = this.$el;
                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || '';
                this.chapterUrl = el.dataset.chapterUrl || '';
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;

                // Load i18n translations from data attributes
                if (el.dataset.i18nGreatChoice) this.i18n.greatChoice = el.dataset.i18nGreatChoice;
                if (el.dataset.i18nThankYou) this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned) this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue) this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorGeneric) this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch('selectedOption', function() { self._updateSubmitState(); });
                this.$watch('isSubmitting', function() { self._updateSubmitState(); });
                this.$watch('feedback', function() { self._updateFeedbackState(); });
                this.$watch('feedbackHtml', function() { self._updateFeedbackState(); });
                this.$watch('feedbackType', function() { self._updateFeedbackState(); });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function() {
                this.hasSelection = this.selectedOption !== null;
                this.hasNoSelection = !this.hasSelection;
                var canSubmit = this.hasSelection && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isNotSubmitting = !this.isSubmitting;
                this.showSubmitLabel = this.hasSelection && this.isNotSubmitting;
                this._syncOptionState();
            },

            _syncOptionState: function() {
                var selected = this.selectedOption;
                var cards = this.$el.querySelectorAll('.option-card');
                cards.forEach(function(card) {
                    var isSelected = selected && card.dataset.optionKey === selected;
                    card.classList.toggle('selected', !!isSelected);
                    card.setAttribute('aria-checked', isSelected ? 'true' : 'false');
                });
            },

            _updateFeedbackState: function() {
                this.showFeedback = this.feedback !== '' || this.feedbackHtml !== '';
                this.isSuccess = this.feedbackType === 'success';
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === 'error';
                this.isNotError = !this.isError;
                if (this.feedbackType === 'success') {
                    this.feedbackClass = 'journey-message-success p-6 text-center mt-6';
                } else if (this.feedbackType === 'error') {
                    this.feedbackClass = 'journey-message-error p-6 text-center mt-6';
                } else {
                    this.feedbackClass = 'hidden mt-6';
                }
            },

            selectOption: function(optionKey, event) {
                var isSameOption = this.selectedOption === optionKey;
                this.clearSelection();

                if (isSameOption) {
                    this.selectedOption = null;
                    return;
                }

                var target = event.currentTarget;
                target.classList.add('selected');
                target.setAttribute('aria-checked', 'true');
                this.selectedOption = optionKey;
            },

            clearSelection: function() {
                var cards = this.$el.querySelectorAll('.option-card');
                cards.forEach(function(card) {
                    card.classList.remove('selected');
                    card.setAttribute('aria-checked', 'false');
                    card.blur();
                });
            },

            isSelected: function(optionKey) {
                return this.selectedOption === optionKey;
            },

            submitAnswer: function() {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = '';
                this.feedbackHtml = '';

                fetch(this.submitUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': CrushUtils.getCsrfToken()
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.selectedOption
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success && data.is_correct) {
                        self.feedbackType = 'success';
                        self.feedbackHtml = self.buildSuccessHtml(data);
                        self.disableOptions();
                    } else {
                        self.isSubmitting = false;
                    }
                })
                .catch(function(error) {
                    console.error('Error:', error);
                    self.feedbackType = 'error';
                    self.feedback = self.i18n.errorGeneric;
                    self.isSubmitting = false;
                });
            },

            buildSuccessHtml: function(data) {
                var isChapter4 = this.chapterNumber === 4;
                var iconHtml = isChapter4
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = isChapter4 ? this.i18n.thankYou : this.i18n.greatChoice;

                return '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' + iconHtml + ' ' + title + '</h3>' +
                    '<div class="personal-message">' + (data.success_message || '') + '</div>' +
                    '<p class="font-bold my-4">🏆 ' + this.i18n.pointsEarned + ' ' + data.points_earned + '</p>' +
                    '<a href="' + this.chapterUrl + '" class="journey-btn-primary">' +
                    this.i18n.continue + ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    '</a>';
            },

            disableOptions: function() {
                var optionsSection = this.$el.querySelector('#optionsSection');
                if (optionsSection) {
                    optionsSection.style.opacity = '0.5';
                }
                var cards = this.$el.querySelectorAll('.option-card');
                cards.forEach(function(card) {
                    card.style.cursor = 'default';
                    card.style.pointerEvents = 'none';
                });
            },

            handleKeypress: function(optionKey, event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: select option from element click
            selectOptionFromElement: function(event) {
                var element = event && event.currentTarget ? event.currentTarget : null;
                if (!element) return;

                var optionKey = element.dataset.optionKey || '';
                if (optionKey) {
                    this.selectOption(optionKey, event);
                }
            },

            // CSP-safe: handle keydown on option cards
            handleOptionKeydown: function(event) {
                if (event && (event.key === 'Enter' || event.key === ' ')) {
                    event.preventDefault();
                    this.selectOptionFromElement(event);
                }
            }
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
    Alpine.data('openText', function() {
        return {
            challengeId: 0,
            answer: '',
            isSubmitting: false,
            feedback: '',
            feedbackType: '',
            feedbackHtml: '',
            submitUrl: '',
            chapterUrl: '',
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
            feedbackClass: 'hidden mt-6',
            charCounterClass: 'char-counter',
            // i18n translations (loaded from data attributes)
            i18n: {
                thankYou: gettext('Thank you for sharing!'),
                pointsEarned: gettext('Points Earned:'),
                continue: gettext('Continue'),
                errorGeneric: gettext('An error occurred. Please try again.')
            },

            init: function() {
                var el = this.$el;
                var self = this;

                this.challengeId = parseInt(el.dataset.challengeId, 10) || 0;
                this.submitUrl = el.dataset.submitUrl || '';
                this.chapterUrl = el.dataset.chapterUrl || '';
                this.chapterNumber = parseInt(el.dataset.chapterNumber, 10) || 1;
                this.minLength = parseInt(el.dataset.minLength, 10) || 10;
                this.maxLength = parseInt(el.dataset.maxLength, 10) || 2000;

                // Load i18n translations from data attributes
                if (el.dataset.i18nThankYou) this.i18n.thankYou = el.dataset.i18nThankYou;
                if (el.dataset.i18nPointsEarned) this.i18n.pointsEarned = el.dataset.i18nPointsEarned;
                if (el.dataset.i18nContinue) this.i18n.continue = el.dataset.i18nContinue;
                if (el.dataset.i18nErrorGeneric) this.i18n.errorGeneric = el.dataset.i18nErrorGeneric;

                // CSP-safe: use $watch to update derived state
                this.$watch('answer', function() { self._updateSubmitState(); self._updateCharCounterClass(); });
                this.$watch('isSubmitting', function() { self._updateSubmitState(); });
                this.$watch('feedback', function() { self._updateFeedbackState(); });
                this.$watch('feedbackHtml', function() { self._updateFeedbackState(); });
                this.$watch('feedbackType', function() { self._updateFeedbackState(); });

                // Auto-focus on input
                this.$nextTick(function() {
                    var textInput = self.$el.querySelector('#textInput');
                    if (textInput) {
                        setTimeout(function() {
                            textInput.focus();
                        }, 500);
                    }
                });
            },

            // CSP-safe: update derived state manually
            _updateSubmitState: function() {
                this.charCount = this.answer.length;
                var hasMinLength = this.answer.trim().length >= this.minLength;
                var canSubmit = hasMinLength && !this.isSubmitting;
                this.submitBtnDisabled = !canSubmit;
                this.isSubmittingState = this.isSubmitting;
                this.isNotSubmitting = !this.isSubmitting;
            },

            _updateFeedbackState: function() {
                this.showFeedback = this.feedback !== '' || this.feedbackHtml !== '';
                this.isSuccess = this.feedbackType === 'success';
                this.isNotSuccess = !this.isSuccess;
                this.isError = this.feedbackType === 'error';
                this.isNotError = !this.isError;
                if (this.feedbackType === 'success') {
                    this.feedbackClass = 'journey-message-success p-6 text-center mt-6';
                } else if (this.feedbackType === 'error') {
                    this.feedbackClass = 'journey-message-error p-6 text-center mt-6';
                } else {
                    this.feedbackClass = 'hidden mt-6';
                }
            },

            _updateCharCounterClass: function() {
                if (this.charCount > this.maxLength * 0.9) {
                    this.charCounterClass = 'char-counter error';
                } else if (this.charCount > this.maxLength * 0.75) {
                    this.charCounterClass = 'char-counter warning';
                } else {
                    this.charCounterClass = 'char-counter';
                }
            },

            submitAnswer: function() {
                // CSP-safe: check submitBtnDisabled directly instead of getter
                if (this.submitBtnDisabled) return;

                var self = this;
                this.isSubmitting = true;
                this._updateSubmitState();
                this.feedback = '';
                this.feedbackHtml = '';

                fetch(this.submitUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': CrushUtils.getCsrfToken()
                    },
                    body: JSON.stringify({
                        challenge_id: this.challengeId,
                        answer: this.answer.trim()
                    })
                })
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    if (data.success && data.is_correct) {
                        self.feedbackType = 'success';
                        self.feedbackHtml = self.buildSuccessHtml(data);
                    } else {
                        self.feedbackType = 'error';
                        self.feedback = self.i18n.errorGeneric;
                        self.isSubmitting = false;
                    }
                })
                .catch(function(error) {
                    console.error('Error:', error);
                    self.feedbackType = 'error';
                    self.feedback = self.i18n.errorGeneric;
                    self.isSubmitting = false;
                });
            },

            buildSuccessHtml: function(data) {
                var chapterNum = this.chapterNumber;
                var isQuestionnaire = (chapterNum === 2 || chapterNum === 4 || chapterNum === 5);
                var iconHtml = isQuestionnaire
                    ? '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>'
                    : '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
                var title = this.i18n.thankYou;

                return '<h3 class="flex items-center justify-center gap-2 text-lg font-bold mb-3">' + iconHtml + ' ' + title + '</h3>' +
                    '<div class="personal-message">' + (data.success_message || '') + '</div>' +
                    '<p class="font-bold my-4">🏆 ' + this.i18n.pointsEarned + ' ' + data.points_earned + '</p>' +
                    '<a href="' + this.chapterUrl + '" class="journey-btn-primary">' +
                    this.i18n.continue + ' <svg class="w-5 h-5 inline ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>' +
                    '</a>';
            },

            // CSP-safe: update answer from input event
            updateAnswer: function(event) {
                // In CSP mode, get value from event parameter or query DOM
                if (event && event.target) {
                    this.answer = event.target.value;
                } else {
                    var input = this.$el.querySelector('#textInput');
                    if (input) this.answer = input.value;
                }
            }
        };
    });

    /**
     * Hint Box Component
     * For displaying unlocked hints
     *
     * Usage:
     * <div x-data="hintBox" data-hint-num="1" @hint-unlocked.window="showHint($event.detail)">
     */
    Alpine.data('hintBox', function() {
        return {
            hintNum: 0,
            hintText: '',
            isActive: false,
            // CSP-safe: plain data properties instead of getters
            showHint: false,
            hintClass: 'journey-hint-box',

            init: function() {
                this.hintNum = parseInt(this.$el.dataset.hintNum, 10) || 0;

                // CSP-safe: use $watch to update derived state
                var self = this;
                this.$watch('isActive', function() { self._updateState(); });
            },

            _updateState: function() {
                this.showHint = this.isActive;
                this.hintClass = this.isActive ? 'journey-hint-box active' : 'journey-hint-box';
            },

            // CSP-safe: wrapper that receives event object
            handleHintUnlockedEvent: function(event) {
                var detail = event && event.detail ? event.detail : {};
                this.handleHintUnlocked(detail);
            },

            handleHintUnlocked: function(detail) {
                if (detail.hintNum === this.hintNum) {
                    this.hintText = detail.hintText;
                    this.isActive = true;
                }
            }
        };
    });

    // PWA Install Banner component
    // Listens for custom events from pwa-install.js
    Alpine.data('pwaInstallBanner', function() {
        return {
            show: false,

            init: function() {
                var self = this;
                // Listen for show/hide events from pwa-install.js
                window.addEventListener('pwa-show-install', function() {
                    self.show = true;
                });
                window.addEventListener('pwa-hide-install', function() {
                    self.show = false;
                });
            },

            dismiss: function() {
                this.show = false;
                // Trigger the dismiss handler in pwa-install.js
                window.dispatchEvent(new CustomEvent('pwa-dismiss-install'));
            }
        };
    });

    // PWA Success Toast component
    // Shows success message after PWA install, auto-hides after 5 seconds
    Alpine.data('pwaSuccessToast', function() {
        return {
            show: false,

            init: function() {
                var self = this;
                window.addEventListener('pwa-install-success', function() {
                    self.showToast();
                });
            },

            showToast: function() {
                var self = this;
                this.show = true;
                // Auto-hide after 5 seconds
                setTimeout(function() {
                    self.show = false;
                }, 5000);
            },

            close: function() {
                this.show = false;
            }
        };
    });

    // Journey Final Response component (Chapter 6 - The Final Question)
    // Handles yes/thinking response submission with loading states
    Alpine.data('finalResponse', function() {
        return {
            isSubmitting: false,
            statusMessage: '',
            statusType: '',  // 'info', 'success', 'error'

            // CSP-safe computed getters
            get isNotSubmitting() { return !this.isSubmitting; },
            get showStatus() { return this.statusMessage !== ''; },
            get statusClass() {
                if (this.statusType === 'success') return 'journey-status-success';
                if (this.statusType === 'error') return 'journey-status-error';
                return 'journey-status-info';
            },

            init: function() {
                // Read config from data attributes
                this.submitUrl = this.$el.dataset.submitUrl || '';
                this.i18nSubmitting = this.$el.dataset.i18nSubmitting || 'Submitting your response...';
                this.i18nSuccess = this.$el.dataset.i18nSuccess || 'Thank you for your response!';
                this.i18nError = this.$el.dataset.i18nError || 'An error occurred. Please try again.';
                this.i18nNetworkError = this.$el.dataset.i18nNetworkError || 'Network error. Please check your connection and try again.';
            },

            submitYes: function() {
                this._submit('yes');
            },

            submitThinking: function() {
                this._submit('thinking');
            },

            _submit: function(response) {
                var self = this;

                if (this.isSubmitting) return;
                this.isSubmitting = true;
                this.statusMessage = '\u23F3 ' + this.i18nSubmitting;
                this.statusType = 'info';

                fetch(this.submitUrl, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': CrushUtils.getCsrfToken(),
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ response: response })
                })
                .then(function(apiResponse) { return apiResponse.json(); })
                .then(function(data) {
                    if (data.success) {
                        self.statusMessage = '\u2705 ' + self.i18nSuccess + ' \uD83D\uDC96';
                        self.statusType = 'success';
                        // Reload page after 2 seconds to show the confirmed response
                        setTimeout(function() {
                            window.location.reload();
                        }, 2000);
                    } else {
                        self.statusMessage = '\u26A0\uFE0F ' + (data.message || self.i18nError);
                        self.statusType = 'error';
                        self.isSubmitting = false;
                    }
                })
                .catch(function(error) {
                    console.error('Error submitting final response:', error);
                    self.statusMessage = '\u26A0\uFE0F ' + self.i18nNetworkError;
                    self.statusType = 'error';
                    self.isSubmitting = false;
                });
            }
        };
    });

    // =========================================================================
    // Admin Dashboard Components (CSP-compliant)
    // =========================================================================

    // Dashboard Tabs component for organizing analytics sections
    // Reads initial tab from data-initial-tab attribute
    Alpine.data('dashboardTabs', function() {
        return {
            activeTab: 'overview',

            // CSP-compatible computed getters for each tab
            get isOverview() { return this.activeTab === 'overview'; },
            get isUsers() { return this.activeTab === 'users'; },
            get isEvents() { return this.activeTab === 'events'; },
            get isEngagement() { return this.activeTab === 'engagement'; },
            get isTechnical() { return this.activeTab === 'technical'; },

            // Tab active state classes
            get overviewTabClass() { return this.activeTab === 'overview' ? 'active' : ''; },
            get usersTabClass() { return this.activeTab === 'users' ? 'active' : ''; },
            get eventsTabClass() { return this.activeTab === 'events' ? 'active' : ''; },
            get engagementTabClass() { return this.activeTab === 'engagement' ? 'active' : ''; },
            get technicalTabClass() { return this.activeTab === 'technical' ? 'active' : ''; },

            init: function() {
                // Read initial tab from data attribute
                var initialTab = this.$el.getAttribute('data-initial-tab');
                if (initialTab) {
                    this.activeTab = initialTab;
                }
                // Also check URL hash for direct linking
                if (window.location.hash) {
                    var hashTab = window.location.hash.substring(1);
                    if (['overview', 'users', 'events', 'engagement', 'technical'].indexOf(hashTab) !== -1) {
                        this.activeTab = hashTab;
                    }
                }
            },

            setOverview: function() {
                this.activeTab = 'overview';
                history.replaceState(null, '', '#overview');
            },
            setUsers: function() {
                this.activeTab = 'users';
                history.replaceState(null, '', '#users');
            },
            setEvents: function() {
                this.activeTab = 'events';
                history.replaceState(null, '', '#events');
            },
            setEngagement: function() {
                this.activeTab = 'engagement';
                history.replaceState(null, '', '#engagement');
            },
            setTechnical: function() {
                this.activeTab = 'technical';
                history.replaceState(null, '', '#technical');
            }
        };
    });

    // Collapsible model group component for admin index page
    // Reads initial state from data-default-open attribute
    Alpine.data('modelGroup', function() {
        return {
            isOpen: true,

            // CSP-compatible computed getters
            get isClosed() { return !this.isOpen; },
            get toggleClass() { return this.isOpen ? '' : 'collapsed'; },
            get contentClass() { return this.isOpen ? '' : 'collapsed'; },
            get ariaExpanded() { return this.isOpen ? 'true' : 'false'; },

            init: function() {
                // Read initial state from data attribute
                var defaultOpen = this.$el.getAttribute('data-default-open');
                this.isOpen = defaultOpen !== 'false';

                // Restore state from localStorage if available
                var groupId = this.$el.getAttribute('data-group-id');
                if (groupId) {
                    var savedState = localStorage.getItem('admin-group-' + groupId);
                    if (savedState !== null) {
                        this.isOpen = savedState === 'true';
                    }
                }
            },

            toggle: function() {
                this.isOpen = !this.isOpen;
                // Save state to localStorage
                var groupId = this.$el.getAttribute('data-group-id');
                if (groupId) {
                    localStorage.setItem('admin-group-' + groupId, this.isOpen);
                }
            }
        };
    });

    // Action Center component with collapse persistence
    Alpine.data('actionCenter', function() {
        return {
            isCollapsed: false,

            // CSP-compatible computed getters
            get isExpanded() { return !this.isCollapsed; },
            get toggleIcon() { return this.isCollapsed ? '+' : '-'; },
            get contentClass() { return this.isCollapsed ? 'collapsed' : ''; },

            init: function() {
                // Restore state from localStorage
                var savedState = localStorage.getItem('admin-action-center-collapsed');
                if (savedState !== null) {
                    this.isCollapsed = savedState === 'true';
                }
            },

            toggle: function() {
                this.isCollapsed = !this.isCollapsed;
                localStorage.setItem('admin-action-center-collapsed', this.isCollapsed);
            }
        };
    });

    // Today's Focus tabs for index page
    Alpine.data('todaysFocus', function() {
        return {
            activeTab: 'events',

            // CSP-compatible computed getters
            get isEventsTab() { return this.activeTab === 'events'; },
            get isSubmissionsTab() { return this.activeTab === 'submissions'; },
            get isAlertsTab() { return this.activeTab === 'alerts'; },

            get eventsTabClass() { return this.activeTab === 'events' ? 'active' : ''; },
            get submissionsTabClass() { return this.activeTab === 'submissions' ? 'active' : ''; },
            get alertsTabClass() { return this.activeTab === 'alerts' ? 'active' : ''; },

            setEvents: function() {
                this.activeTab = 'events';
            },
            setSubmissions: function() {
                this.activeTab = 'submissions';
            },
            setAlerts: function() {
                this.activeTab = 'alerts';
            }
        };
    });

    // Date filter component for dashboard
    Alpine.data('dateFilter', function() {
        return {
            selectedRange: '30d',

            // CSP-compatible computed getters
            get is7d() { return this.selectedRange === '7d'; },
            get is30d() { return this.selectedRange === '30d'; },
            get is90d() { return this.selectedRange === '90d'; },
            get isAll() { return this.selectedRange === 'all'; },

            init: function() {
                // Read initial value from URL param or data attribute
                var urlParams = new URLSearchParams(window.location.search);
                var rangeParam = urlParams.get('range');
                if (rangeParam) {
                    this.selectedRange = rangeParam;
                } else {
                    var defaultRange = this.$el.getAttribute('data-default-range');
                    if (defaultRange) {
                        this.selectedRange = defaultRange;
                    }
                }
            },

            setRange: function(range) {
                this.selectedRange = range;
            },

            apply: function() {
                // Update URL with new range and reload
                var url = new URL(window.location);
                url.searchParams.set('range', this.selectedRange);
                window.location.href = url.toString();
            }
        };
    });

    // Photo slideshow component for journey rewards
    // Reads images from data-images JSON attribute
    // Supports keyboard navigation, touch swipe, and auto-play
    Alpine.data('photoSlideshow', function() {
        return {
            images: [],
            currentIndex: 0,
            isLoading: true,
            touchStartX: 0,
            touchEndX: 0,
            autoPlayInterval: null,
            autoPlayEnabled: false,

            // Computed getters for CSP compatibility
            get hasMultipleImages() { return this.images.length > 1; },
            get hasSingleImage() { return this.images.length === 1; },
            get hasNoImages() { return this.images.length === 0; },
            get totalImages() { return this.images.length; },
            get currentImageNumber() { return this.currentIndex + 1; },
            get currentImage() { return this.images[this.currentIndex] || null; },
            get currentImageUrl() {
                var img = this.images[this.currentIndex];
                return img ? img.url : '';
            },
            get canGoNext() { return this.currentIndex < this.images.length - 1; },
            get canGoPrev() { return this.currentIndex > 0; },
            get isNotLoading() { return !this.isLoading; },
            get progressPercent() {
                if (this.images.length <= 1) return 100;
                return ((this.currentIndex + 1) / this.images.length) * 100;
            },

            // Dot navigation helpers - returns array of booleans for each dot
            get dotStates() {
                var self = this;
                return this.images.map(function(_, idx) {
                    return idx === self.currentIndex;
                });
            },

            // Individual dot state getters (for up to 5 images)
            get isDot0Active() { return this.currentIndex === 0; },
            get isDot1Active() { return this.currentIndex === 1; },
            get isDot2Active() { return this.currentIndex === 2; },
            get isDot3Active() { return this.currentIndex === 3; },
            get isDot4Active() { return this.currentIndex === 4; },
            get hasDot0() { return this.images.length > 0; },
            get hasDot1() { return this.images.length > 1; },
            get hasDot2() { return this.images.length > 2; },
            get hasDot3() { return this.images.length > 3; },
            get hasDot4() { return this.images.length > 4; },

            // CSP-safe dot class getters (avoid ternary in template)
            get dot0ActiveClass() { return this.isDot0Active ? 'slideshow-dot-active' : ''; },
            get dot1ActiveClass() { return this.isDot1Active ? 'slideshow-dot-active' : ''; },
            get dot2ActiveClass() { return this.isDot2Active ? 'slideshow-dot-active' : ''; },
            get dot3ActiveClass() { return this.isDot3Active ? 'slideshow-dot-active' : ''; },
            get dot4ActiveClass() { return this.isDot4Active ? 'slideshow-dot-active' : ''; },

            // CSP-safe aria-selected getters
            get dot0AriaSelected() { return this.isDot0Active ? 'true' : 'false'; },
            get dot1AriaSelected() { return this.isDot1Active ? 'true' : 'false'; },
            get dot2AriaSelected() { return this.isDot2Active ? 'true' : 'false'; },
            get dot3AriaSelected() { return this.isDot3Active ? 'true' : 'false'; },
            get dot4AriaSelected() { return this.isDot4Active ? 'true' : 'false'; },

            init: function() {
                var self = this;

                // Load images from script tag (preferred) or data attribute (fallback)
                var scriptId = this.$el.getAttribute('data-images-from');
                if (scriptId) {
                    // Load from script tag containing JSON (avoids HTML attribute escaping issues)
                    var scriptEl = document.getElementById(scriptId);
                    if (scriptEl) {
                        try {
                            this.images = JSON.parse(scriptEl.textContent);
                        } catch (e) {
                            console.error('[PhotoSlideshow] Failed to parse images from script tag:', e);
                            this.images = [];
                        }
                    }
                } else {
                    // Fallback: load from data-images attribute
                    var imagesData = this.$el.getAttribute('data-images');
                    if (imagesData) {
                        try {
                            this.images = JSON.parse(imagesData);
                        } catch (e) {
                            console.error('[PhotoSlideshow] Failed to parse images:', e);
                            this.images = [];
                        }
                    }
                }

                // Preload first image
                if (this.images.length > 0) {
                    var img = new Image();
                    img.onload = function() {
                        self.isLoading = false;
                    };
                    img.onerror = function() {
                        self.isLoading = false;
                    };
                    img.src = this.images[0].url;
                } else {
                    this.isLoading = false;
                }

                // Setup keyboard navigation
                document.addEventListener('keydown', function(e) {
                    if (e.key === 'ArrowLeft') {
                        self.prev();
                    } else if (e.key === 'ArrowRight') {
                        self.next();
                    }
                });

                // Preload all images in background
                this._preloadImages();
            },

            _preloadImages: function() {
                var self = this;
                this.images.forEach(function(imgData, idx) {
                    if (idx === 0) return; // Already loaded
                    var img = new Image();
                    img.src = imgData.url;
                });
            },

            next: function() {
                if (this.currentIndex < this.images.length - 1) {
                    this.currentIndex++;
                } else {
                    // Loop to beginning
                    this.currentIndex = 0;
                }
            },

            prev: function() {
                if (this.currentIndex > 0) {
                    this.currentIndex--;
                } else {
                    // Loop to end
                    this.currentIndex = this.images.length - 1;
                }
            },

            goTo: function(index) {
                if (index >= 0 && index < this.images.length) {
                    this.currentIndex = index;
                }
            },

            // Individual goTo methods for CSP compatibility (avoid inline expressions)
            goToDot0: function() { this.goTo(0); },
            goToDot1: function() { this.goTo(1); },
            goToDot2: function() { this.goTo(2); },
            goToDot3: function() { this.goTo(3); },
            goToDot4: function() { this.goTo(4); },

            // Touch event handlers for swipe support
            handleTouchStart: function(event) {
                this.touchStartX = event.touches[0].clientX;
            },

            handleTouchMove: function(event) {
                this.touchEndX = event.touches[0].clientX;
            },

            handleTouchEnd: function() {
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
            toggleAutoPlay: function() {
                var self = this;
                if (this.autoPlayEnabled) {
                    this.stopAutoPlay();
                } else {
                    this.autoPlayEnabled = true;
                    this.autoPlayInterval = setInterval(function() {
                        self.next();
                    }, 3000);
                }
            },

            stopAutoPlay: function() {
                this.autoPlayEnabled = false;
                if (this.autoPlayInterval) {
                    clearInterval(this.autoPlayInterval);
                    this.autoPlayInterval = null;
                }
            },

            // Download current image
            downloadCurrent: function() {
                if (this.currentImage) {
                    var link = document.createElement('a');
                    link.href = this.currentImage.url;
                    link.download = 'photo-' + (this.currentIndex + 1) + '.jpg';
                    link.target = '_blank';
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }
            }
        };
    });

    // Letter audio player with autoplay fallback for browser compatibility
    // Most browsers block autoplay without user interaction, so we provide
    // a graceful fallback with a "click to play" prompt
    Alpine.data('letterAudioPlayer', function() {
        return {
            isPlaying: false,
            autoplayBlocked: false,
            audioElement: null,
            hasInteracted: false,

            // Computed getters for CSP compatibility
            get showPlayPrompt() { return this.autoplayBlocked && !this.isPlaying; },
            get isNotPlaying() { return !this.isPlaying; },
            get playButtonClass() {
                return this.isPlaying ? 'letter-audio-playing' : 'letter-audio-paused';
            },

            init: function() {
                var self = this;
                this.audioElement = this.$refs.audio;

                if (this.audioElement) {
                    // Listen for play/pause events
                    this.audioElement.addEventListener('play', function() {
                        self.isPlaying = true;
                        self.autoplayBlocked = false;
                    });

                    this.audioElement.addEventListener('pause', function() {
                        self.isPlaying = false;
                    });

                    this.audioElement.addEventListener('ended', function() {
                        self.isPlaying = false;
                    });

                    // Attempt autoplay
                    this.attemptAutoplay();
                }
            },

            attemptAutoplay: function() {
                var self = this;
                if (!this.audioElement) return;

                // Try to play - browsers may block this
                var playPromise = this.audioElement.play();

                if (playPromise !== undefined) {
                    playPromise.then(function() {
                        // Autoplay succeeded
                        self.isPlaying = true;
                        self.autoplayBlocked = false;
                    }).catch(function(error) {
                        // Autoplay was blocked by browser
                        self.autoplayBlocked = true;
                        self.isPlaying = false;
                    });
                }
            },

            togglePlay: function() {
                if (!this.audioElement) return;

                this.hasInteracted = true;

                if (this.isPlaying) {
                    this.audioElement.pause();
                } else {
                    this.audioElement.play();
                }
            },

            playMusic: function() {
                if (!this.audioElement) return;

                this.hasInteracted = true;
                this.audioElement.play();
            }
        };
    });

    // Screening Call Guideline Component for Coach Review
    // 7-step accordion with checklist items and notes
    Alpine.data('screeningCallGuideline', function() {
        return {
            // Active accordion section (1-7, 0 = none)
            activeSection: 1,

            // Checklist completion flags
            introductionComplete: false,
            languageConfirmed: false,
            residenceConfirmed: false,
            expectationsDiscussed: false,
            datingPreferenceAsked: false,
            crushMeaningAsked: false,
            questionsAnswered: false,

            // Notes for each section
            residenceNotes: '',
            expectationsNotes: '',
            datingPreferenceValue: '',
            crushMeaningNotes: '',
            questionsNotes: '',
            finalNotes: '',

            // Failed call form visibility
            showFailedCallForm: false,

            // Required steps for validation
            requiredSteps: ['introductionComplete', 'residenceConfirmed', 'datingPreferenceAsked'],

            // CSP-safe computed getters
            get completedCount() {
                var count = 0;
                if (this.introductionComplete) count++;
                if (this.languageConfirmed) count++;
                if (this.residenceConfirmed) count++;
                if (this.expectationsDiscussed) count++;
                if (this.datingPreferenceAsked) count++;
                if (this.crushMeaningAsked) count++;
                if (this.questionsAnswered) count++;
                return count;
            },

            get progressPercent() {
                return Math.round((this.completedCount / 7) * 100);
            },

            get progressWidth() {
                return 'width: ' + this.progressPercent + '%';
            },

            get progressText() {
                return this.progressPercent + '% complete';
            },

            get isValid() {
                return this.introductionComplete && this.residenceConfirmed && this.datingPreferenceAsked;
            },

            get isInvalid() {
                return !this.isValid;
            },

            get submitDisabled() {
                return !this.isValid;
            },

            get submitButtonClass() {
                return this.isValid
                    ? 'bg-green-500 hover:bg-green-600 cursor-pointer'
                    : 'bg-gray-300 cursor-not-allowed';
            },

            get hideFailedCallForm() {
                return !this.showFailedCallForm;
            },

            // Section visibility getters
            get section1Open() { return this.activeSection === 1; },
            get section2Open() { return this.activeSection === 2; },
            get section3Open() { return this.activeSection === 3; },
            get section4Open() { return this.activeSection === 4; },
            get section5Open() { return this.activeSection === 5; },
            get section6Open() { return this.activeSection === 6; },
            get section7Open() { return this.activeSection === 7; },

            // Section header classes (CSP-safe)
            get section1HeaderClass() {
                return this.activeSection === 1
                    ? 'bg-purple-100 border-purple-300'
                    : (this.introductionComplete ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200');
            },
            get section2HeaderClass() {
                return this.activeSection === 2
                    ? 'bg-purple-100 border-purple-300'
                    : (this.languageConfirmed ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200');
            },
            get section3HeaderClass() {
                return this.activeSection === 3
                    ? 'bg-purple-100 border-purple-300'
                    : (this.residenceConfirmed ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200');
            },
            get section4HeaderClass() {
                return this.activeSection === 4
                    ? 'bg-purple-100 border-purple-300'
                    : (this.expectationsDiscussed ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200');
            },
            get section5HeaderClass() {
                return this.activeSection === 5
                    ? 'bg-purple-100 border-purple-300'
                    : (this.datingPreferenceAsked ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200');
            },
            get section6HeaderClass() {
                return this.activeSection === 6
                    ? 'bg-purple-100 border-purple-300'
                    : (this.crushMeaningAsked ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200');
            },
            get section7HeaderClass() {
                return this.activeSection === 7
                    ? 'bg-purple-100 border-purple-300'
                    : (this.questionsAnswered ? 'bg-green-50 border-green-200' : 'bg-gray-50 border-gray-200');
            },

            // Status icon visibility getters
            get section1Complete() { return this.introductionComplete; },
            get section2Complete() { return this.languageConfirmed; },
            get section3Complete() { return this.residenceConfirmed; },
            get section4Complete() { return this.expectationsDiscussed; },
            get section5Complete() { return this.datingPreferenceAsked; },
            get section6Complete() { return this.crushMeaningAsked; },
            get section7Complete() { return this.questionsAnswered; },

            // Required badge visibility
            get section1Required() { return true; },
            get section3Required() { return true; },
            get section5Required() { return true; },

            // Methods
            init: function() {
                // Load existing checklist data if present
                var dataEl = this.$el.querySelector('[data-checklist-initial]');
                if (dataEl) {
                    try {
                        var initial = JSON.parse(dataEl.getAttribute('data-checklist-initial') || '{}');
                        if (initial.introduction_complete) this.introductionComplete = true;
                        if (initial.language_confirmed) this.languageConfirmed = true;
                        if (initial.residence_confirmed) this.residenceConfirmed = true;
                        if (initial.expectations_discussed) this.expectationsDiscussed = true;
                        if (initial.dating_preference_asked) this.datingPreferenceAsked = true;
                        if (initial.crush_meaning_asked) this.crushMeaningAsked = true;
                        if (initial.questions_answered) this.questionsAnswered = true;
                        if (initial.residence_notes) this.residenceNotes = initial.residence_notes;
                        if (initial.expectations_notes) this.expectationsNotes = initial.expectations_notes;
                        if (initial.dating_preference_value) this.datingPreferenceValue = initial.dating_preference_value;
                        if (initial.crush_meaning_notes) this.crushMeaningNotes = initial.crush_meaning_notes;
                        if (initial.questions_notes) this.questionsNotes = initial.questions_notes;
                    } catch (e) {
                        console.warn('Failed to parse initial checklist data', e);
                    }
                }
            },

            toggleSection: function(num) {
                this.activeSection = this.activeSection === num ? 0 : num;
            },

            openSection1: function() { this.toggleSection(1); },
            openSection2: function() { this.toggleSection(2); },
            openSection3: function() { this.toggleSection(3); },
            openSection4: function() { this.toggleSection(4); },
            openSection5: function() { this.toggleSection(5); },
            openSection6: function() { this.toggleSection(6); },
            openSection7: function() { this.toggleSection(7); },

            goToNextSection: function() {
                if (this.activeSection < 7) {
                    this.activeSection = this.activeSection + 1;
                }
            },

            toggleIntroduction: function() {
                this.introductionComplete = !this.introductionComplete;
            },
            toggleLanguage: function() {
                this.languageConfirmed = !this.languageConfirmed;
            },
            toggleResidence: function() {
                this.residenceConfirmed = !this.residenceConfirmed;
            },
            toggleExpectations: function() {
                this.expectationsDiscussed = !this.expectationsDiscussed;
            },
            toggleDatingPreference: function() {
                this.datingPreferenceAsked = !this.datingPreferenceAsked;
            },
            toggleCrushMeaning: function() {
                this.crushMeaningAsked = !this.crushMeaningAsked;
            },
            toggleQuestions: function() {
                this.questionsAnswered = !this.questionsAnswered;
            },
            toggleFailedCallForm: function() {
                this.showFailedCallForm = !this.showFailedCallForm;
            },

            // Input handlers for CSP compliance (x-model not supported)
            updateResidenceNotes: function(event) {
                this.residenceNotes = event.target.value;
            },
            updateExpectationsNotes: function(event) {
                this.expectationsNotes = event.target.value;
            },
            updateCrushMeaningNotes: function(event) {
                this.crushMeaningNotes = event.target.value;
            },
            updateQuestionsNotes: function(event) {
                this.questionsNotes = event.target.value;
            },
            updateFinalNotes: function(event) {
                this.finalNotes = event.target.value;
            },

            setDatingPreferenceOpposite: function() {
                this.datingPreferenceValue = 'opposite_gender';
            },
            setDatingPreferenceSame: function() {
                this.datingPreferenceValue = 'same_gender';
            },
            setDatingPreferenceBoth: function() {
                this.datingPreferenceValue = 'both';
            },

            // Dating preference button classes (CSP-safe)
            get oppositeGenderButtonClass() {
                return this.datingPreferenceValue === 'opposite_gender'
                    ? 'bg-purple-500 text-white border-purple-500'
                    : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50';
            },
            get sameGenderButtonClass() {
                return this.datingPreferenceValue === 'same_gender'
                    ? 'bg-purple-500 text-white border-purple-500'
                    : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50';
            },
            get bothGenderButtonClass() {
                return this.datingPreferenceValue === 'both'
                    ? 'bg-purple-500 text-white border-purple-500'
                    : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50';
            },

            // Serialize checklist data to JSON for form submission (getter for CSP compliance)
            get checklistDataJson() {
                return JSON.stringify({
                    introduction_complete: this.introductionComplete,
                    language_confirmed: this.languageConfirmed,
                    residence_confirmed: this.residenceConfirmed,
                    residence_notes: this.residenceNotes,
                    expectations_discussed: this.expectationsDiscussed,
                    expectations_notes: this.expectationsNotes,
                    dating_preference_asked: this.datingPreferenceAsked,
                    dating_preference_value: this.datingPreferenceValue,
                    crush_meaning_asked: this.crushMeaningAsked,
                    crush_meaning_notes: this.crushMeaningNotes,
                    questions_answered: this.questionsAnswered,
                    questions_notes: this.questionsNotes
                });
            }
        };
    });

    // Email Preview Modal (for coach review page)
    Alpine.data('emailPreviewModal', function() {
        return {
            isOpen: false,
            isLoading: false,

            get modalClasses() {
                return this.isOpen ? 'fixed inset-0 z-50 flex items-center justify-center' : 'hidden';
            },

            get showLoading() {
                return this.isLoading;
            },

            open: function() {
                this.isOpen = true;
                this.isLoading = true;
            },

            close: function() {
                this.isOpen = false;
                this.isLoading = false;
            },

            // CSP-compliant event handlers
            handleCloseClick: function() {
                this.close();
            },

            handleBackdropClick: function() {
                this.close();
            },

            handleEscape: function() {
                if (this.isOpen) {
                    this.close();
                }
            },

            handlePreviewLoaded: function() {
                this.isLoading = false;
            },

            handleSubmitClick: function() {
                this.close();
                this.submitForm();
            },

            submitForm: function() {
                // Find and submit the review form
                var form = document.querySelector('form[method="post"]');
                if (form) {
                    form.submit();
                }
            }
        };
    });

    // Review Tabs Component (for redesigned coach review page)
    Alpine.data('reviewTabs', function() {
        return {
            activeTab: 1, // Default to profile tab
            callCompleted: false,

            // Initialize from data attribute
            init: function() {
                // Check if call is completed (from data attribute)
                var callCompletedAttr = this.$el.getAttribute('data-call-completed');
                if (callCompletedAttr === 'true') {
                    this.callCompleted = true;
                }
            },

            // CSP-compatible computed getters
            get isProfileTab() { return this.activeTab === 1; },
            get isScreeningTab() { return this.activeTab === 2; },
            get isDecisionTab() { return this.activeTab === 3; },

            // For showing profile summary on non-profile tabs
            get showProfileSummary() { return this.activeTab !== 1; },

            get profileTabClass() {
                return this.getTabClasses(1);
            },
            get screeningTabClass() {
                return this.getTabClasses(2);
            },
            get decisionTabClass() {
                return this.getTabClasses(3);
            },

            get showCallWarning() {
                return !this.callCompleted;
            },

            // Methods
            showProfile: function() {
                this.activeTab = 1;
            },
            showScreening: function() {
                this.activeTab = 2;
            },
            showDecision: function() {
                this.activeTab = 3;
            },

            // Tab styling helper
            getTabClasses: function(tabNum) {
                var base = 'px-6 py-3 font-semibold rounded-t-lg transition-all cursor-pointer';
                var active = 'bg-white text-purple-600 border-b-2 border-purple-600';
                var inactive = 'bg-gray-100 text-gray-600 hover:bg-gray-200';
                return this.activeTab === tabNum ? base + ' ' + active : base + ' ' + inactive;
            },

            // Auto-advance workflow when screening call is completed
            completeScreening: function() {
                this.callCompleted = true;
                this.activeTab = 3; // Move to decision tab
            }
        };
    });

    // Guidelines Panel Component (sticky bottom-right panel)
    Alpine.data('guidelinesPanel', function() {
        return {
            isOpen: true,

            get isClosed() {
                return !this.isOpen;
            },

            toggle: function() {
                this.isOpen = !this.isOpen;
            },
            close: function() {
                this.isOpen = false;
            },
            open: function() {
                this.isOpen = true;
            }
        };
    });

    // Profile Accordion Component (for profile tab in coach review)
    Alpine.data('profileAccordion', function() {
        return {
            photoOpen: true,
            basicOpen: false,
            bioOpen: false,
            privacyOpen: false,

            // CSP-safe rotation classes
            get photoOpenRotateClass() {
                return this.photoOpen ? 'rotate-180' : '';
            },
            get basicOpenRotateClass() {
                return this.basicOpen ? 'rotate-180' : '';
            },
            get bioOpenRotateClass() {
                return this.bioOpen ? 'rotate-180' : '';
            },
            get privacyOpenRotateClass() {
                return this.privacyOpen ? 'rotate-180' : '';
            },

            // Toggle methods
            togglePhoto: function() {
                this.photoOpen = !this.photoOpen;
            },
            toggleBasic: function() {
                this.basicOpen = !this.basicOpen;
            },
            toggleBio: function() {
                this.bioOpen = !this.bioOpen;
            },
            togglePrivacy: function() {
                this.privacyOpen = !this.privacyOpen;
            }
        };
    });

    // Event Registration Form - Dynamic form behavior with contextual questions
    Alpine.data('eventRegistration', function() {
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
            toggleGuest: function() {
                this.bringingGuest = !this.bringingGuest;
            },

            handleSubmit: function(event) {
                this.isSubmitting = true;
                // Let native form submission or HTMX continue
                if (!event.target.getAttribute('hx-post')) {
                    event.target.submit();
                }
            }
        };
    });

    // Form Button Component - Standardized loading states for submit buttons
    // Usage: <button x-data="formButton" @click="setLoading" :disabled="isLoading" :class="buttonClass">
    //           <span x-show="!isLoading" x-text="label"></span>
    //           <span x-show="isLoading" class="flex items-center">
    //               <svg class="animate-spin -ml-1 mr-2 h-4 w-4" ...>...</svg>
    //               <span x-text="loadingLabel"></span>
    //           </span>
    //        </button>
    Alpine.data('formButton', function() {
        return {
            isLoading: false,
            label: '',
            loadingLabel: '',
            baseClass: '',

            init: function() {
                // Read attributes from button element
                this.label = this.$el.dataset.label || this.$el.textContent.trim();
                this.loadingLabel = this.$el.dataset.loadingLabel || 'Processing...';
                this.baseClass = this.$el.className;

                // Listen for form submission
                const form = this.$el.closest('form');
                if (form) {
                    form.addEventListener('submit', () => {
                        this.setLoading();
                    });
                }

                // Listen for HTMX request events
                this.$el.addEventListener('htmx:beforeRequest', () => {
                    this.setLoading();
                });
                this.$el.addEventListener('htmx:afterRequest', () => {
                    this.resetLoading();
                });
            },

            // Computed property for button classes
            get buttonClass() {
                return this.baseClass + (this.isLoading ? ' opacity-75 cursor-not-allowed' : '');
            },

            // Computed property for disabled state
            get isDisabled() {
                return this.isLoading;
            },

            // Actions
            setLoading: function() {
                this.isLoading = true;
            },

            resetLoading: function() {
                this.isLoading = false;
            }
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
    Alpine.data('themeToggle', function() {
        return {
            currentTheme: 'light',
            systemPreference: 'light',

            init: function() {
                // Initialize from themeManager
                if (window.themeManager) {
                    this.currentTheme = window.themeManager.getTheme();
                }

                // Detect system preference
                if (window.matchMedia) {
                    this.systemPreference = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';

                    // Listen for system preference changes
                    var mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
                    var self = this;

                    var handleChange = function(e) {
                        self.systemPreference = e.matches ? 'dark' : 'light';
                    };

                    // Modern browsers
                    if (mediaQuery.addEventListener) {
                        mediaQuery.addEventListener('change', handleChange);
                    }
                    // Legacy browsers
                    else if (mediaQuery.addListener) {
                        mediaQuery.addListener(handleChange);
                    }
                }
            },

            // Getters for CSP compliance (no inline expressions in templates)
            get isDark() {
                return this.currentTheme === 'dark';
            },

            get isLight() {
                return this.currentTheme === 'light';
            },

            get isSystemDark() {
                return this.systemPreference === 'dark';
            },

            get toggleButtonClass() {
                return this.isDark
                    ? 'bg-gray-700 text-yellow-400 hover:bg-gray-600'
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200';
            },

            get sunIconClass() {
                return this.isLight ? 'opacity-100' : 'opacity-40';
            },

            get moonIconClass() {
                return this.isDark ? 'opacity-100' : 'opacity-40';
            },

            get statusText() {
                var saved = localStorage.getItem('theme');
                if (!saved) {
                    return this.isSystemDark ? 'System (Dark)' : 'System (Light)';
                }
                return this.isDark ? 'Dark Mode' : 'Light Mode';
            },

            get ariaLabel() {
                return this.isDark ? 'Switch to light mode' : 'Switch to dark mode';
            },

            // Methods
            toggleTheme: function() {
                if (window.themeManager) {
                    window.themeManager.toggleTheme();
                    this.currentTheme = window.themeManager.getTheme();
                }
            },

            setTheme: function(theme) {
                if (window.themeManager) {
                    window.themeManager.setTheme(theme);
                    this.currentTheme = theme;
                }
            },

            useSystemPreference: function() {
                localStorage.removeItem('theme');
                this.currentTheme = this.systemPreference;
                if (window.themeManager) {
                    window.themeManager.setTheme(this.systemPreference);
                }
            }
        };
    });

    // =========================================================================
    // Language Tabs - For multilingual form fields
    // =========================================================================
    Alpine.data('languageTabs', function() {
        return {
            activeLanguage: 'en',

            get isEnglish() { return this.activeLanguage === 'en'; },
            get isGerman() { return this.activeLanguage === 'de'; },
            get isFrench() { return this.activeLanguage === 'fr'; },

            get englishTabClass() {
                return this.activeLanguage === 'en'
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md'
                    : 'text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80';
            },
            get germanTabClass() {
                return this.activeLanguage === 'de'
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md'
                    : 'text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80';
            },
            get frenchTabClass() {
                return this.activeLanguage === 'fr'
                    ? 'bg-gradient-to-r from-purple-500 to-pink-500 text-white shadow-md'
                    : 'text-gray-600 dark:text-gray-400 bg-white/50 dark:bg-gray-700/50 hover:bg-white/80 dark:hover:bg-gray-700/80';
            },

            setEnglish: function() { this.activeLanguage = 'en'; },
            setGerman: function() { this.activeLanguage = 'de'; },
            setFrench: function() { this.activeLanguage = 'fr'; }
        };
    });

    // ============================================================
    // Ghost Story Slideshow
    // ============================================================
    Alpine.data('ghostStory', function() {
        return {
            currentScene: 0,
            totalScenes: 6,
            isPaused: false,
            autoAdvanceTimer: null,
            sceneDurations: [4000, 4000, 5000, 5000, 5000, 6000],

            // Scene visibility getters (CSP-safe)
            get isScene0() { return this.currentScene === 0; },
            get isScene1() { return this.currentScene === 1; },
            get isScene2() { return this.currentScene === 2; },
            get isScene3() { return this.currentScene === 3; },
            get isScene4() { return this.currentScene === 4; },
            get isScene5() { return this.currentScene === 5; },

            // Navigation state
            get isFirstScene() { return this.currentScene === 0; },
            get isLastScene() { return this.currentScene === this.totalScenes - 1; },
            get isNotFirstScene() { return this.currentScene > 0; },
            get isNotLastScene() { return this.currentScene < this.totalScenes - 1; },

            // Dot active class getters
            get dot0Class() {
                return this.currentScene === 0
                    ? 'ghost-story-dot ghost-story-dot-active'
                    : 'ghost-story-dot';
            },
            get dot1Class() {
                return this.currentScene === 1
                    ? 'ghost-story-dot ghost-story-dot-active'
                    : 'ghost-story-dot';
            },
            get dot2Class() {
                return this.currentScene === 2
                    ? 'ghost-story-dot ghost-story-dot-active'
                    : 'ghost-story-dot';
            },
            get dot3Class() {
                return this.currentScene === 3
                    ? 'ghost-story-dot ghost-story-dot-active'
                    : 'ghost-story-dot';
            },
            get dot4Class() {
                return this.currentScene === 4
                    ? 'ghost-story-dot ghost-story-dot-active'
                    : 'ghost-story-dot';
            },
            get dot5Class() {
                return this.currentScene === 5
                    ? 'ghost-story-dot ghost-story-dot-active'
                    : 'ghost-story-dot';
            },

            // Scene container class getters (CSP-safe, no ternary in template)
            get scene0Class() {
                return this.currentScene === 0
                    ? 'ghost-story-scene ghost-story-scene-active ghost-story-scene-0'
                    : 'ghost-story-scene ghost-story-scene-0';
            },
            get scene1Class() {
                return this.currentScene === 1
                    ? 'ghost-story-scene ghost-story-scene-active ghost-story-scene-1'
                    : 'ghost-story-scene ghost-story-scene-1';
            },
            get scene2Class() {
                return this.currentScene === 2
                    ? 'ghost-story-scene ghost-story-scene-active ghost-story-scene-2'
                    : 'ghost-story-scene ghost-story-scene-2';
            },
            get scene3Class() {
                return this.currentScene === 3
                    ? 'ghost-story-scene ghost-story-scene-active ghost-story-scene-3'
                    : 'ghost-story-scene ghost-story-scene-3';
            },
            get scene4Class() {
                return this.currentScene === 4
                    ? 'ghost-story-scene ghost-story-scene-active ghost-story-scene-4'
                    : 'ghost-story-scene ghost-story-scene-4';
            },
            get scene5Class() {
                return this.currentScene === 5
                    ? 'ghost-story-scene ghost-story-scene-active ghost-story-scene-5'
                    : 'ghost-story-scene ghost-story-scene-5';
            },

            // Pause/play icon
            get pauseIcon() { return this.isPaused ? '\u25B6' : '\u275A\u275A'; },
            get pauseLabel() { return this.isPaused ? 'Play' : 'Pause'; },

            // Scene counter text
            get sceneCounter() { return (this.currentScene + 1) + ' / ' + this.totalScenes; },

            init: function() {
                var self = this;
                this.startAutoAdvance();

                // Pause on hover
                this.$el.addEventListener('mouseenter', function() {
                    if (!self.isPaused) {
                        self._hoverPaused = true;
                        self.clearTimer();
                    }
                });
                this.$el.addEventListener('mouseleave', function() {
                    if (self._hoverPaused) {
                        self._hoverPaused = false;
                        if (!self.isPaused) {
                            self.startAutoAdvance();
                        }
                    }
                });
            },

            destroy: function() {
                this.clearTimer();
            },

            clearTimer: function() {
                if (this.autoAdvanceTimer) {
                    clearTimeout(this.autoAdvanceTimer);
                    this.autoAdvanceTimer = null;
                }
            },

            startAutoAdvance: function() {
                var self = this;
                this.clearTimer();
                if (this.isPaused) return;
                var duration = this.sceneDurations[this.currentScene] || 5000;
                this.autoAdvanceTimer = setTimeout(function() {
                    self.nextScene();
                }, duration);
            },

            nextScene: function() {
                if (this.currentScene < this.totalScenes - 1) {
                    this.currentScene++;
                } else {
                    this.currentScene = 0;
                }
                this.startAutoAdvance();
            },

            previousScene: function() {
                if (this.currentScene > 0) {
                    this.currentScene--;
                } else {
                    this.currentScene = this.totalScenes - 1;
                }
                this.startAutoAdvance();
            },

            togglePause: function() {
                this.isPaused = !this.isPaused;
                if (this.isPaused) {
                    this.clearTimer();
                } else {
                    this.startAutoAdvance();
                }
            },

            // Individual goToScene methods (CSP-safe, no param passing)
            goToScene0: function() { this.currentScene = 0; this.startAutoAdvance(); },
            goToScene1: function() { this.currentScene = 1; this.startAutoAdvance(); },
            goToScene2: function() { this.currentScene = 2; this.startAutoAdvance(); },
            goToScene3: function() { this.currentScene = 3; this.startAutoAdvance(); },
            goToScene4: function() { this.currentScene = 4; this.startAutoAdvance(); },
            goToScene5: function() { this.currentScene = 5; this.startAutoAdvance(); }
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
    Alpine.data('fieldValidator', function() {
        return {
            value: '',
            error: '',
            isValid: true,
            touched: false,
            _debounceTimer: null,

            // Config from data attributes
            _required: false,
            _minLength: 0,
            _maxLength: 0,
            _validationType: 'text',
            _errorRequired: 'This field is required',
            _errorMinLength: '',
            _errorMaxLength: '',
            _errorEmail: 'Please enter a valid email address',

            init: function() {
                var el = this.$el;
                this._required = el.getAttribute('data-required') === 'true';
                this._minLength = parseInt(el.getAttribute('data-min-length') || '0', 10);
                this._maxLength = parseInt(el.getAttribute('data-max-length') || '0', 10);
                this._validationType = el.getAttribute('data-validation-type') || 'text';
                this._errorRequired = el.getAttribute('data-error-required') || this._errorRequired;
                this._errorMinLength = el.getAttribute('data-error-min-length') ||
                    ('Must be at least ' + this._minLength + ' characters');
                this._errorMaxLength = el.getAttribute('data-error-max-length') ||
                    ('Must be under ' + this._maxLength + ' characters');
                this._errorEmail = el.getAttribute('data-error-email') || this._errorEmail;
            },

            get hasError() {
                return this.touched && this.error.length > 0;
            },

            get errorMessage() {
                return this.error;
            },

            get fieldClass() {
                if (!this.touched) return '';
                return this.isValid ? 'border-green-500' : 'border-red-500';
            },

            _validate: function() {
                var val = this.value.trim();

                if (this._required && val.length === 0) {
                    this.error = this._errorRequired;
                    this.isValid = false;
                    return;
                }

                if (val.length > 0 && this._minLength > 0 && val.length < this._minLength) {
                    this.error = this._errorMinLength;
                    this.isValid = false;
                    return;
                }

                if (this._maxLength > 0 && val.length > this._maxLength) {
                    this.error = this._errorMaxLength;
                    this.isValid = false;
                    return;
                }

                if (this._validationType === 'email' && val.length > 0) {
                    // Basic email pattern check
                    var atIdx = val.indexOf('@');
                    var dotIdx = val.lastIndexOf('.');
                    if (atIdx < 1 || dotIdx < atIdx + 2 || dotIdx >= val.length - 1) {
                        this.error = this._errorEmail;
                        this.isValid = false;
                        return;
                    }
                }

                this.error = '';
                this.isValid = true;
            },

            handleInput: function() {
                var input = this.$el.querySelector('input, textarea, select');
                if (input) {
                    this.value = input.value;
                }
                // Debounce validation on input (500ms)
                var self = this;
                clearTimeout(this._debounceTimer);
                this._debounceTimer = setTimeout(function() {
                    if (self.touched) {
                        self._validate();
                    }
                }, 500);
            },

            handleBlur: function() {
                var input = this.$el.querySelector('input, textarea, select');
                if (input) {
                    this.value = input.value;
                }
                this.touched = true;
                this._validate();
            }
        };
    });

    // =========================================================================
    // Form Validation Summary - Aggregates field errors at the top of a form
    // =========================================================================
    // Usage: <form x-data="formValidationSummary" @submit="handleSubmit">
    //   <div x-show="hasErrors" role="alert" class="bg-red-50 ...">
    //     <ul>
    //       <template x-for="err in errorList"><li x-text="err"></li></template>
    //     </ul>
    //   </div>
    //   ... fieldValidator fields ...
    //   <button type="submit">Submit</button>
    // </form>
    Alpine.data('formValidationSummary', function() {
        return {
            errors: [],
            submitted: false,

            get hasErrors() {
                return this.submitted && this.errors.length > 0;
            },

            get errorCount() {
                return this.errors.length;
            },

            get errorList() {
                return this.errors;
            },

            _collectErrors: function() {
                var errs = [];
                var validators = this.$el.querySelectorAll('[x-data="fieldValidator"]');
                for (var i = 0; i < validators.length; i++) {
                    var component = validators[i].__x;
                    if (component && component.$data) {
                        // Trigger validation on all fields
                        var input = validators[i].querySelector('input, textarea, select');
                        if (input) {
                            component.$data.value = input.value;
                        }
                        component.$data.touched = true;
                        component.$data._validate();
                        if (component.$data.error) {
                            errs.push(component.$data.error);
                        }
                    }
                }
                return errs;
            },

            handleSubmit: function(e) {
                this.submitted = true;
                this.errors = this._collectErrors();
                if (this.errors.length > 0) {
                    e.preventDefault();
                    // Scroll to the summary
                    var alert = this.$el.querySelector('[role="alert"]');
                    if (alert) {
                        alert.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }
                    // Focus first invalid field
                    var firstInvalid = this.$el.querySelector('[x-data="fieldValidator"] .border-red-500');
                    if (firstInvalid) {
                        firstInvalid.focus();
                    }
                }
            }
        };
    });

    // Section navigation for meeting guide page
    Alpine.data('sectionNav', function() {
        return {
            activeSection: '',
            sections: [],

            init() {
                var self = this;
                var el = this.$el;
                var navItems = el.querySelectorAll('[data-section]');
                self.sections = [];
                navItems.forEach(function(item) {
                    self.sections.push(item.getAttribute('data-section'));
                });

                // Set initial active section
                if (self.sections.length > 0) {
                    self.activeSection = self.sections[0];
                }

                // Intersection observer for active section tracking
                var observer = new IntersectionObserver(function(entries) {
                    entries.forEach(function(entry) {
                        if (entry.isIntersecting) {
                            self.activeSection = entry.target.id;
                        }
                    });
                }, { rootMargin: '-20% 0px -70% 0px' });

                self.sections.forEach(function(id) {
                    var target = document.getElementById(id);
                    if (target) {
                        observer.observe(target);
                    }
                });
            },

            get activeSectionId() {
                return this.activeSection;
            },

            isActive(sectionId) {
                return this.activeSection === sectionId;
            },

            scrollTo(sectionId) {
                var target = document.getElementById(sectionId);
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            }
        };
    });

    // =========================================================================
    // CRUSH SPARK COMPONENTS
    // =========================================================================

    /**
     * sparkRequest - Description form with character counter
     * Used on spark_request.html
     */
    Alpine.data('sparkRequest', function() {
        return {
            description: '',
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
            }
        };
    });

    /**
     * sparkJourneyBuilder - Multi-step media upload for journey creation
     * Used on spark_create_journey.html
     */
    Alpine.data('sparkJourneyBuilder', function() {
        return {
            submitting: false,

            get isSubmitting() {
                return this.submitting;
            },

            submitForm() {
                this.submitting = true;
            }
        };
    });

    /**
     * coachSparkAssign - Searchable attendee list for coach assignment
     * Used on coach_spark_assign.html
     */
    Alpine.data('coachSparkAssign', function() {
        return {
            searchQuery: '',
            selectedUserId: null,

            get hasSelection() {
                return this.selectedUserId !== null;
            },

            selectUser(userId) {
                this.selectedUserId = userId;
            },

            clearSelection() {
                this.selectedUserId = null;
            },

            isSelected(userId) {
                return this.selectedUserId === userId;
            }
        };
    });

    // Spark confirm inline component (replaces browser confirm dialog)
    Alpine.data('sparkConfirm', function() {
        return {
            state: 'initial',

            get isInitial() { return this.state === 'initial'; },
            get isConfirming() { return this.state === 'confirming'; },

            showConfirm() {
                this.state = 'confirming';
            },

            cancel() {
                this.state = 'initial';
            }
        };
    });

});
