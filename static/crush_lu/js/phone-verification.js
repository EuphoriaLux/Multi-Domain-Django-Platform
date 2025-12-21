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

        // Firebase state
        this.confirmationResult = null;
        this.recaptchaVerifier = null;
        this.isInitialized = false;

        // UI state
        this.state = 'idle'; // idle, sending, code_sent, verifying, verified, error

        // Firebase config from global or defaults
        this.firebaseConfig = options.firebaseConfig || {
            apiKey: window.FIREBASE_API_KEY || "***REDACTED***",
            authDomain: window.FIREBASE_AUTH_DOMAIN || "***REDACTED***",
            projectId: window.FIREBASE_PROJECT_ID || "***REDACTED***"
        };

        this.initFirebase();
    }

    /**
     * Initialize Firebase SDK
     */
    initFirebase() {
        if (typeof firebase === 'undefined') {
            console.error('Firebase SDK not loaded. Include firebase-app-compat.js and firebase-auth-compat.js');
            return;
        }

        try {
            if (!firebase.apps.length) {
                firebase.initializeApp(this.firebaseConfig);
                console.log('Firebase initialized');
            }
            this.isInitialized = true;
        } catch (error) {
            console.error('Failed to initialize Firebase:', error);
            this.onError(error);
        }
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
                callback: (response) => {
                    console.log('reCAPTCHA verified');
                },
                'expired-callback': () => {
                    console.log('reCAPTCHA expired, resetting');
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
                throw new Error('Please enter a valid phone number');
            }

            if (!this.recaptchaVerifier) {
                this.setupRecaptcha();
            }

            console.log('Sending verification code to:', phone.slice(0, 7) + '***');

            this.confirmationResult = await firebase.auth().signInWithPhoneNumber(
                phone,
                this.recaptchaVerifier
            );

            this.setState('code_sent');
            this.onCodeSent(phone);

            return { success: true, phone };

        } catch (error) {
            console.error('SMS send error:', error);
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
            const error = 'No verification in progress. Please request a new code.';
            this.onError(error);
            return { success: false, error };
        }

        if (!code || code.length !== 6) {
            const error = 'Please enter the 6-digit code';
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

        return await response.json();
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
     */
    setState(newState) {
        this.state = newState;
        this.onStateChange(newState);
    }

    /**
     * Format Firebase errors for user display
     */
    formatFirebaseError(error) {
        const errorMap = {
            'auth/invalid-phone-number': 'Invalid phone number format. Please use international format (e.g., +352 XXX XXX)',
            'auth/too-many-requests': 'Too many attempts. Please wait a few minutes and try again.',
            'auth/captcha-check-failed': 'Security verification failed. Please refresh the page and try again.',
            'auth/invalid-verification-code': 'Invalid code. Please check the code and try again.',
            'auth/code-expired': 'Code expired. Please request a new code.',
            'auth/quota-exceeded': 'SMS quota exceeded. Please try again later.',
            'auth/user-disabled': 'This phone number has been disabled. Please contact support.',
            'auth/operation-not-allowed': 'Phone authentication is not enabled. Please contact support.',
        };

        if (error.code && errorMap[error.code]) {
            return errorMap[error.code];
        }

        return error.message || 'An error occurred. Please try again.';
    }
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PhoneVerification;
}
