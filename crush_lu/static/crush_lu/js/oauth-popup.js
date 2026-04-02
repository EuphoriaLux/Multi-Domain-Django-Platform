/**
 * Crush.lu OAuth Popup Handler
 *
 * Manages popup-based OAuth authentication for better PWA compatibility.
 * Falls back to redirect-based OAuth if popups are blocked.
 *
 * NOTE: django-allauth handles the server-side OAuth flow, but it does not
 *       provide cross-window messaging or popup lifecycle handling. This
 *       helper keeps the parent window in sync by listening for postMessage
 *       events and polling the auth status API as a safety net.
 *
 * Usage:
 *   CrushOAuthPopup.login('facebook', {
 *     onSuccess: function(result) { console.log('Logged in!', result); },
 *     onError: function(error) { console.error('Failed:', error); },
 *     onPopupBlocked: function() { console.log('Popup was blocked'); }
 *   });
 */

(function() {
    'use strict';

    var CrushOAuthPopup = {
        // Configuration
        config: {
            popupWidth: 600,
            popupHeight: 700,
            checkInterval: 500,  // ms between popup closed checks
            timeout: 300000,     // 5 minute timeout
        },

        // State
        popup: null,
        checkTimer: null,
        timeoutTimer: null,
        onSuccess: null,
        onError: null,
        messageHandler: null,
        popupOpenedAt: null,
        lastAuthCheck: 0,

        /**
         * Open OAuth popup for the specified provider
         * @param {string} provider - OAuth provider ('facebook', 'google', etc.)
         * @param {Object} options - Optional callbacks and settings
         */
        login: function(provider, options) {
            var self = this;
            options = options || {};

            // Set default callbacks
            this.onSuccess = options.onSuccess || function() {};
            this.onError = options.onError || function() {};

            // Build OAuth URL with popup indicator
            var oauthUrl = this.buildOAuthUrl(provider);

            // Calculate popup position (centered on screen)
            var left = Math.max(0, (window.screen.width - this.config.popupWidth) / 2);
            var top = Math.max(0, (window.screen.height - this.config.popupHeight) / 2);

            // Open popup
            this.popup = window.open(
                oauthUrl,
                'crush_oauth_popup',
                'width=' + this.config.popupWidth + ',' +
                'height=' + this.config.popupHeight + ',' +
                'left=' + left + ',top=' + top + ',' +
                'scrollbars=yes,status=yes,resizable=yes,toolbar=no,menubar=no'
            );

            // Check if popup was blocked
            if (!this.popup || this.popup.closed || typeof this.popup.closed === 'undefined') {
                if (options.onPopupBlocked) {
                    options.onPopupBlocked();
                } else {
                    // Default fallback: redirect to OAuth directly
                    this.fallbackToRedirect(provider);
                }
                return;
            }

            // Focus the popup
            try {
                this.popup.focus();
            } catch (e) {
                // Ignore focus errors
            }

            // Start listening for postMessage
            this.startListening();

            // Start checking if popup is closed
            this.startPopupCheck();

            // Track when the popup was opened so we can run health-check polls
            this.popupOpenedAt = Date.now();
            this.lastAuthCheck = 0;

            // Set timeout for OAuth completion
            this.timeoutTimer = setTimeout(function() {
                self.handleTimeout();
            }, this.config.timeout);

        },

        /**
         * Build OAuth URL with popup mode indicator
         * @param {string} provider - OAuth provider name
         * @returns {string} - Full OAuth URL
         */
        buildOAuthUrl: function(provider) {
            // Try to get URL from button data attribute first
            var loginBtn = document.querySelector('[data-' + provider + '-url]');
            var url;

            if (loginBtn) {
                url = loginBtn.getAttribute('data-' + provider + '-url');
            } else {
                // Fallback to standard Allauth URL pattern
                url = '/accounts/' + provider + '/login/';
            }

            // Add popup mode parameter (server will store in session)
            var separator = url.indexOf('?') >= 0 ? '&' : '?';
            return url + separator + 'popup=1';
        },

        /**
         * Fall back to redirect-based OAuth
         * @param {string} provider - OAuth provider name
         */
        fallbackToRedirect: function(provider) {
            var loginBtn = document.querySelector('[data-' + provider + '-url]');
            var url;

            if (loginBtn) {
                url = loginBtn.getAttribute('data-' + provider + '-url');
            } else {
                url = '/accounts/' + provider + '/login/';
            }

            window.location.href = url;
        },

        /**
         * Start listening for postMessage from popup
         */
        startListening: function() {
            var self = this;

            // Remove any existing handler
            if (this.messageHandler) {
                window.removeEventListener('message', this.messageHandler);
            }

            // Create new handler
            this.messageHandler = function(event) {
                self.handleMessage(event);
            };

            window.addEventListener('message', this.messageHandler);
        },

        /**
         * Stop listening for postMessage
         */
        stopListening: function() {
            if (this.messageHandler) {
                window.removeEventListener('message', this.messageHandler);
                this.messageHandler = null;
            }
        },

        /**
         * Handle postMessage from popup
         * @param {MessageEvent} event - The message event
         */
        handleMessage: function(event) {
            // Validate origin - must match our domain
            if (event.origin !== window.location.origin) {
                return;
            }

            var data = event.data;
            if (!data || !data.type) return;

            if (data.type === 'CRUSH_OAUTH_COMPLETE') {
                if (data.success) {
                    // Send acknowledgment to popup (optional)
                    if (this.popup && !this.popup.closed) {
                        try {
                            this.popup.postMessage({
                                type: 'CRUSH_OAUTH_ACKNOWLEDGED'
                            }, window.location.origin);
                        } catch (e) {
                            // Ignore errors
                        }
                    }

                    this.onSuccess({
                        hasProfile: data.hasProfile,
                        redirectUrl: data.redirectUrl,
                        userName: data.userName,
                    });

                    // Best effort: close popup immediately if we still have a handle
                    if (this.popup && !this.popup.closed) {
                        try {
                            this.popup.close();
                        } catch (e) { /* ignore */ }
                    }

                    this.cleanup();

                    // Redirect to appropriate page (with security validation)
                    if (data.redirectUrl) {
                        try {
                            // Validate URL is from same origin
                            const redirectUrl = new URL(data.redirectUrl, window.location.origin);

                            // Only allow same-origin redirects
                            if (redirectUrl.origin === window.location.origin) {
                                // Additional validation: check against allowlist of safe paths
                                const allowedPaths = ['/dashboard/', '/events/', '/profile/', '/connections/', '/journey/'];
                                const isSafePath = allowedPaths.some(path => redirectUrl.pathname.startsWith(path));

                                if (isSafePath) {
                                    window.location.href = redirectUrl.href;
                                } else {
                                    console.warn('[OAuth Popup] Redirect path not in allowlist, using fallback');
                                    window.location.href = '/dashboard/';
                                }
                            } else {
                                console.error('[OAuth Popup] Cross-origin redirect blocked');
                                window.location.href = '/dashboard/';
                            }
                        } catch (error) {
                            console.error('[OAuth Popup] Invalid redirect URL:', error);
                            window.location.href = '/dashboard/';
                        }
                    }
                } else {
                    this.cleanup();
                    this.onError({
                        error: data.error || 'unknown',
                        description: data.errorDescription || 'Login failed',
                    });
                }
            } else if (data.type === 'CRUSH_OAUTH_ERROR') {
                this.cleanup();
                this.onError({
                    error: data.error || 'unknown',
                    description: data.errorDescription || 'Authentication failed',
                });
            } else if (data.type === 'CRUSH_OAUTH_RETRY') {
                // Just cleanup - user can click button again
                this.cleanup();
            }
        },

        /**
         * Start checking if popup was closed manually
         */
        startPopupCheck: function() {
            var self = this;

            if (this.checkTimer) {
                clearInterval(this.checkTimer);
            }

            this.checkTimer = setInterval(function() {
                if (self.popup && self.popup.closed) {
                    self.handlePopupClosed();
                    return;
                }

                // Fallback: even if popup stays open (e.g., postMessage missed),
                // periodically check auth status so the parent can redirect.
                var now = Date.now();
                if (self.popup && !self.popup.closed && self.popupOpenedAt) {
                    var timeSinceOpen = now - self.popupOpenedAt;
                    var timeSinceLastCheck = now - self.lastAuthCheck;

                    if (timeSinceOpen > 2000 && timeSinceLastCheck > 2000) {
                        self.lastAuthCheck = now;
                        self.checkAuthStatus({ closePopupOnSuccess: true, fromOpenPopup: true });
                    }
                }
            }, this.config.checkInterval);
        },

        /**
         * Handle popup being closed (user cancelled or completed)
         */
        handlePopupClosed: function() {
            var self = this;
            this.cleanup();

            // Check auth status via API (fallback if postMessage failed)
            // Small delay to ensure session is updated
            setTimeout(function() {
                self.checkAuthStatus();
            }, 500);
        },

        /**
         * Check authentication status via API
         */
        checkAuthStatus: function(options) {
            var self = this;
            options = options || {};

            fetch('/api/auth/status/', {
                method: 'GET',
                credentials: 'same-origin',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json',
                }
            })
            .then(function(response) {
                return response.json();
            })
            .then(function(data) {
                if (data.authenticated) {
                    // Close popup if we still have a handle and were polling while open
                    if (options.closePopupOnSuccess && self.popup && !self.popup.closed) {
                        try {
                            self.popup.close();
                        } catch (e) { /* ignore */ }
                    }

                    self.cleanup();

                    self.onSuccess({
                        hasProfile: data.has_profile,
                        redirectUrl: data.redirect_url,
                        userName: data.user_name,
                    });

                    window.location.href = data.redirect_url;
                } else {
                    // User cancelled or OAuth failed - don't show error, just reset
                }
            })
            .catch(function(error) {
                console.error('[OAuth Popup] Error checking auth status:', error);
            });
        },

        /**
         * Handle timeout
         */
        handleTimeout: function() {
            this.cleanup();

            if (this.popup && !this.popup.closed) {
                try {
                    this.popup.close();
                } catch (e) {
                    // Ignore errors
                }
            }

            this.onError({
                error: 'timeout',
                description: 'Login timed out. Please try again.',
            });
        },

        /**
         * Cleanup timers and listeners
         */
        cleanup: function() {
            if (this.checkTimer) {
                clearInterval(this.checkTimer);
                this.checkTimer = null;
            }
            if (this.timeoutTimer) {
                clearTimeout(this.timeoutTimer);
                this.timeoutTimer = null;
            }
            this.stopListening();
            this.popup = null;
            this.popupOpenedAt = null;
            this.lastAuthCheck = 0;
        },

        /**
         * Check if popups are likely to be blocked
         * (Not 100% reliable, but helps with UX decisions)
         * @returns {boolean}
         */
        arePopupsLikelyBlocked: function() {
            // Safari on iOS always blocks popups not triggered by user action
            var isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
            if (isIOS) return false; // Let it try, iOS handles gracefully

            // Check if browser has popup blocker indicators
            // This is a best-effort check
            return false; // Assume popups work, fallback will handle
        }
    };

    // Expose globally
    window.CrushOAuthPopup = CrushOAuthPopup;
})();
