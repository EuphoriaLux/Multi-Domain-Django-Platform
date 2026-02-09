/**
 * Phone Verification using Firebase/Google Identity Platform
 *
 * Handles SMS OTP flow and sends ID token to backend for secure verification.
 * Phone numbers are extracted from verified tokens server-side, NOT from client.
 *
 * Usage:
 *   const phoneVerification = new PhoneVerification({
 *       phoneInputId: 'id_phone_number',
 *       verifyButtonId: 'verify-phone-btn',
 *       recaptchaContainerId: 'recaptcha-container',
 *       csrfToken: '{{ csrf_token }}',
 *       onVerified: (phoneNumber) => {
 *           // Handle successful verification
 *       },
 *       onError: (error) => {
 *           // Handle errors
 *       }
 *   });
 */
class PhoneVerification {
    constructor(options) {
        this.phoneInputId = options.phoneInputId;
        this.verifyButtonId = options.verifyButtonId;
        this.recaptchaContainerId = options.recaptchaContainerId;
        this.csrfToken = options.csrfToken;
        this.onVerified = options.onVerified || (() => {});
        this.onError = options.onError || ((err) => console.error(err));
        this.onCodeSent = options.onCodeSent || (() => {});
        this.onStateChange = options.onStateChange || (() => {});
        this.onFirebaseError = options.onFirebaseError || null; // New: callback for Firebase initialization errors

        // Firebase state
        this.confirmationResult = null;
        this.recaptchaVerifier = null;
        this.isInitialized = false;
        this.initializationError = null; // Track initialization errors

        // UI state
        this.state = 'idle'; // idle, sending, code_sent, verifying, verified, error

        // localStorage key for persisting verification state
        this.storageKey = 'crush_phone_verification';

        // Firebase config from options or global window variables (set by Django template)
        // IMPORTANT: No hardcoded defaults - config must be provided via environment variables
        this.firebaseConfig = options.firebaseConfig || {
            apiKey: window.FIREBASE_API_KEY,
            authDomain: window.FIREBASE_AUTH_DOMAIN,
            projectId: window.FIREBASE_PROJECT_ID
        };

        // Validate config is present
        if (!this.firebaseConfig.apiKey || !this.firebaseConfig.projectId) {
            const errorMsg = gettext('Firebase configuration missing. Phone verification is temporarily unavailable.');
            console.error('Firebase configuration missing. Set FIREBASE_API_KEY and FIREBASE_PROJECT_ID environment variables.');
            this.initializationError = errorMsg;
            this.setState('error');
            if (this.onFirebaseError) {
                this.onFirebaseError(errorMsg);
            }
            return;
        }

        // Restore state from localStorage
        this.restoreState();

        this.initFirebase();
    }

    /**
     * Persist verification state to localStorage
     */
    saveState() {
        try {
            const stateData = {
                state: this.state,
                timestamp: Date.now(),
                hasConfirmation: !!this.confirmationResult
            };
            localStorage.setItem(this.storageKey, JSON.stringify(stateData));
        } catch (e) {
            // localStorage not available, silently ignore
            console.warn('Could not save phone verification state to localStorage');
        }
    }

    /**
     * Restore verification state from localStorage
     * Only restore if state was saved within last 10 minutes (OTP validity window)
     */
    restoreState() {
        try {
            const savedData = localStorage.getItem(this.storageKey);
            if (savedData) {
                const parsed = JSON.parse(savedData);
                const ageMinutes = (Date.now() - parsed.timestamp) / 1000 / 60;

                // Only restore if less than 10 minutes old and was in code_sent state
                if (ageMinutes < 10 && parsed.state === 'code_sent') {
                    // Note: We can't restore confirmationResult, user will need to resend
                    // But we can show them a helpful message
                    this.state = 'idle';
                } else {
                    // Expired or completed, clear storage
                    this.clearStoredState();
                }
            }
        } catch (e) {
            // localStorage not available or corrupted data
            console.warn('Could not restore phone verification state');
        }
    }

    /**
     * Clear stored verification state
     */
    clearStoredState() {
        try {
            localStorage.removeItem(this.storageKey);
        } catch (e) {
            // Ignore
        }
    }

    /**
     * Initialize Firebase SDK
     */
    initFirebase() {
        if (typeof firebase === 'undefined') {
            const errorMsg = gettext('Phone verification is temporarily unavailable. Please try again later or contact support.');
            console.error('Firebase SDK not loaded. Include firebase-app-compat.js and firebase-auth-compat.js');
            this.initializationError = errorMsg;
            this.setState('error');
            if (this.onFirebaseError) {
                this.onFirebaseError(errorMsg);
            }
            return;
        }

        try {
            if (!firebase.apps.length) {
                firebase.initializeApp(this.firebaseConfig);
            }

            // Set SMS language BEFORE RecaptchaVerifier is created (per Firebase docs)
            // This ensures both reCAPTCHA display and SMS messages use user's preferred language
            if (window.FIREBASE_LANGUAGE) {
                firebase.auth().languageCode = window.FIREBASE_LANGUAGE;
            }

            this.isInitialized = true;
            this.initializationError = null;
        } catch (error) {
            const errorMsg = gettext('Phone verification service failed to initialize. Please refresh the page or try again later.');
            console.error('Failed to initialize Firebase:', error);
            this.initializationError = errorMsg;
            this.setState('error');
            if (this.onFirebaseError) {
                this.onFirebaseError(errorMsg);
            }
            this.onError(error);
        }
    }

    /**
     * Check if Firebase is ready for use
     */
    isReady() {
        return this.isInitialized && !this.initializationError;
    }

    /**
     * Get initialization error if any
     */
    getInitializationError() {
        return this.initializationError;
    }

    /**
     * Setup invisible reCAPTCHA verifier
     */
    setupRecaptcha() {
        if (!this.isInitialized) {
            throw new Error('Firebase not initialized');
        }

        const container = document.getElementById(this.recaptchaContainerId);
        if (!container) {
            throw new Error(`reCAPTCHA container not found: ${this.recaptchaContainerId}`);
        }

        // Clear any existing verifier
        if (this.recaptchaVerifier) {
            try {
                this.recaptchaVerifier.clear();
            } catch (e) {
                // Ignore clear errors
            }
        }

        this.recaptchaVerifier = new firebase.auth.RecaptchaVerifier(
            this.recaptchaContainerId,
            {
                size: 'invisible',
                callback: () => {
                },
                'expired-callback': () => {
                    this.setupRecaptcha();
                }
            }
        );

        return this.recaptchaVerifier;
    }

    /**
     * Get phone number from input, normalizing format
     */
    getPhoneNumber() {
        const input = document.getElementById(this.phoneInputId);
        if (!input) {
            throw new Error(`Phone input not found: ${this.phoneInputId}`);
        }

        let phone = input.value.trim();

        // Normalize Luxembourg numbers
        if (phone.startsWith('00352')) {
            phone = '+352' + phone.slice(5);
        } else if (phone.startsWith('352') && !phone.startsWith('+')) {
            phone = '+352' + phone.slice(3);
        } else if (!phone.startsWith('+') && phone.length >= 6) {
            // Assume Luxembourg if no country code
            phone = '+352' + phone.replace(/\s+/g, '');
        }

        // Remove spaces and dashes
        phone = phone.replace(/[\s\-\(\)]/g, '');

        return phone;
    }

    /**
     * Send verification code via SMS
     */
    async sendVerificationCode(phoneNumber = null) {
        this.setState('sending');

        try {
            const phone = phoneNumber || this.getPhoneNumber();

            if (!phone || phone.length < 8) {
                throw new Error(gettext('Please enter a valid phone number'));
            }

            if (!this.recaptchaVerifier) {
                this.setupRecaptcha();
            }

            this.confirmationResult = await firebase.auth().signInWithPhoneNumber(
                phone,
                this.recaptchaVerifier
            );

            this.setState('code_sent');
            this.onCodeSent(phone);

            return { success: true, phone };

        } catch (error) {
            // Log full error object for debugging Firebase issues
            console.error('SMS send error:', error);
            console.error('Error details:', {
                code: error.code,
                message: error.message,
                name: error.name,
                stack: error.stack,
                customData: error.customData,
                serverResponse: error.serverResponse,
                fullError: JSON.stringify(error, Object.getOwnPropertyNames(error))
            });

            this.setState('error');
            this.onError(this.formatFirebaseError(error));

            // Reset reCAPTCHA for retry
            try {
                this.setupRecaptcha();
            } catch (e) {
                // Ignore
            }

            return { success: false, error: error.message };
        }
    }

    /**
     * Verify the OTP code entered by user
     */
    async verifyCode(code) {
        if (!this.confirmationResult) {
            const error = gettext('No verification in progress. Please request a new code.');
            this.onError(error);
            return { success: false, error };
        }

        if (!code || code.length !== 6) {
            const error = gettext('Please enter the 6-digit code');
            this.onError(error);
            return { success: false, error };
        }

        this.setState('verifying');

        try {
            const result = await this.confirmationResult.confirm(code);

            // CRITICAL: Get the ID token to send to backend
            const idToken = await result.user.getIdToken();

            // Send token to backend for secure verification
            const response = await this.notifyBackend(idToken);

            // Sign out from Firebase - we only use it for phone verification
            // Not for app login. Keeps things clean.
            await firebase.auth().signOut();

            if (response.success) {
                this.setState('verified');
                this.onVerified(response.phone_number);
                return {
                    success: true,
                    phone_number: response.phone_number
                };
            } else {
                throw new Error(response.error || 'Verification failed');
            }

        } catch (error) {
            console.error('Code verification error:', error);
            this.setState('error');

            // Also sign out on error to clean up state
            try {
                await firebase.auth().signOut();
            } catch (e) {
                // Ignore signout errors
            }

            this.onError(this.formatFirebaseError(error));
            return { success: false, error: error.message };
        }
    }

    /**
     * Notify backend of successful verification
     * Sends ID token (NOT phone number) for secure server-side verification
     */
    async notifyBackend(idToken) {
        const response = await fetch('/api/phone/mark-verified/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.csrfToken
            },
            body: JSON.stringify({ idToken })
        });

        const data = await response.json();

        // Update CSRF token if the server returned a fresh one.
        // This prevents CSRF mismatch when the form is submitted later,
        // since Django may rotate the CSRF cookie on POST requests.
        if (data.csrfToken) {
            this.csrfToken = data.csrfToken;
            var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
            if (input) {
                input.value = data.csrfToken;
            }
        }

        return data;
    }

    /**
     * Check verification status from backend
     */
    async checkStatus() {
        try {
            const response = await fetch('/api/phone/status/', {
                method: 'GET',
                headers: {
                    'X-CSRFToken': this.csrfToken
                }
            });

            return await response.json();
        } catch (error) {
            console.error('Failed to check phone status:', error);
            return { phone_verified: false };
        }
    }

    /**
     * Reset state for new verification attempt
     */
    reset() {
        this.confirmationResult = null;
        this.setState('idle');

        try {
            if (this.recaptchaVerifier) {
                this.recaptchaVerifier.clear();
            }
        } catch (e) {
            // Ignore
        }
        this.recaptchaVerifier = null;
    }

    /**
     * Update internal state and notify listeners
     * Also persists state to localStorage for recovery after page refresh
     */
    setState(newState) {
        this.state = newState;
        this.onStateChange(newState);

        // Persist state changes to localStorage
        if (newState === 'code_sent') {
            this.saveState();
        } else if (newState === 'verified' || newState === 'idle') {
            // Clear stored state on completion or reset
            this.clearStoredState();
        }
    }

    /**
     * Format Firebase errors for user display
     */
    formatFirebaseError(error) {
        const errorMap = {
            'auth/invalid-phone-number': gettext('Invalid phone number format. Please use international format (e.g., +352 XXX XXX)'),
            'auth/too-many-requests': gettext('Too many attempts. Please wait a few minutes and try again.'),
            'auth/captcha-check-failed': gettext('Security verification failed. Please refresh the page and try again.'),
            'auth/invalid-verification-code': gettext('Invalid code. Please check the code and try again.'),
            'auth/code-expired': gettext('Code expired. Please request a new code.'),
            'auth/quota-exceeded': gettext('SMS quota exceeded. Please try again later.'),
            'auth/user-disabled': gettext('This phone number has been disabled. Please contact support.'),
            'auth/operation-not-allowed': gettext('Phone authentication is not enabled. Please contact support.'),
            'auth/error-code:-39': gettext('SMS service temporarily unavailable. Please wait a few minutes and try again.'),
        };

        if (error.code && errorMap[error.code]) {
            return errorMap[error.code];
        }

        // Check for error code -39 in the message (sometimes formatted differently)
        if (error.message && error.message.includes('error-code:-39')) {
            return gettext('SMS service temporarily unavailable. Please wait a few minutes and try again.');
        }

        return error.message || gettext('An error occurred. Please try again.');
    }
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PhoneVerification;
}
