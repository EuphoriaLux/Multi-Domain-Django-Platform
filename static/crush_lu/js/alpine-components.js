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

    // Navbar component with dropdowns and mobile menu
    Alpine.data('navbar', function() {
        return {
            mobileMenuOpen: false,
            coachToolsOpen: false,
            coachProfileOpen: false,
            eventsOpen: false,
            userMenuOpen: false,

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
            // i18n strings for time formatting (loaded from data attributes)
            i18n: {
                neverUsed: 'Never used',
                justNow: 'Just now',
                lastActive: 'Last active:',
                minutesAgo: 'm ago',
                hoursAgo: 'h ago',
                daysAgo: 'd ago',
                monthsAgo: 'mo ago'
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
            // i18n strings for time formatting (loaded from data attributes)
            i18n: {
                neverUsed: 'Never used',
                justNow: 'Just now',
                lastActive: 'Last active:',
                minutesAgo: 'm ago',
                hoursAgo: 'h ago',
                daysAgo: 'd ago',
                monthsAgo: 'mo ago'
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
                                return sub.unsubscribe().then(function() {
                                    console.log('[CoachPush] Unsubscribed from browser');
                                });
                            } else {
                                console.log('[CoachPush] Keeping browser subscription for user push');
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
                    console.log('[CoachPush] Test:', data.success ? 'sent' : data.error);
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
                // Read initial photo states from data attributes
                for (var i = 1; i <= 3; i++) {
                    var hasImage = el.getAttribute('data-photo-' + i + '-exists') === 'true';
                    var preview = el.getAttribute('data-photo-' + i + '-url') || '';
                    this.photos[i - 1].hasImage = hasImage;
                    this.photos[i - 1].preview = preview;
                }
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
                    var reader = new FileReader();
                    reader.onload = function(e) {
                        self.photos[index].preview = e.target.result;
                        self.photos[index].hasImage = true;
                    };
                    reader.readAsDataURL(file);
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
            get canContinueStep1() { return this.phoneVerified && !this.isSaving; },
            get cannotContinueStep1() { return !this.phoneVerified || this.isSaving; },

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
                var dob = document.querySelector('[name=date_of_birth]');
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
                    reviewDob.textContent = dob ? dob.value || 'Not provided' : 'Not provided';
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
                    reviewLocation.textContent = location ? location.value || 'Not selected' : 'Not selected';
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

            nextStepAndReview: function() {
                this.nextStep();
                this.updateReview();
            },

            // CSRF token helper for AJAX requests
            getCsrfToken: function() {
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

            // Save Step 2 data to backend
            saveStep2: function() {
                var self = this;
                self.isSaving = true;
                self.saveError = '';

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
                        self._highlightRegion(initialValue);
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
                        console.log('intl-tel-input: Instance destroyed on component cleanup');
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
    (function() {
        document.addEventListener('change', function(event) {
            if (event.target.classList.contains('lang-select-auto-submit')) {
                var form = event.target.closest('form');
                if (form) {
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
            },

            close: function() {
                this.isOpen = false;
                // Clear timer on close
                if (this.resendTimer) {
                    clearInterval(this.resendTimer);
                    this.resendTimer = null;
                }
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
                return this.copied ? 'Copied!' : 'Copy Link';
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
                var subject = 'A Magical Journey Awaits You!';
                var body = 'Hi ' + this.recipientName + ',\n\n' +
                          'I created a special "Wonderland of You" journey just for you!\n\n' +
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
                        title: 'A Magical Journey Awaits!',
                        text: 'I created a special Wonderland journey for ' + self.recipientName + '!',
                        url: self.giftUrl
                    }).catch(function(err) {
                        console.log('Share cancelled or failed:', err);
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

});
