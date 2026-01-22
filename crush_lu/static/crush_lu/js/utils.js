/**
 * Crush.lu Shared Utilities
 * Common functions for CSRF protection, sanitization, and logging
 */

/**
 * Get CSRF token from cookie (secure method)
 * @returns {string} CSRF token value
 */
function getCsrfToken() {
    // First try hidden input (works with CSRF_COOKIE_HTTPONLY=True)
    const hiddenInput = document.getElementById('csrf-token');
    if (hiddenInput && hiddenInput.value) {
        return hiddenInput.value;
    }
    // Try form input
    const formInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
    if (formInput && formInput.value) {
        return formInput.value;
    }
    // Fallback to cookie (if CSRF_COOKIE_HTTPONLY=False)
    const cookie = document.cookie
        .split('; ')
        .find(row => row.startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1] : '';
}

/**
 * Sanitize HTML to prevent XSS attacks
 * @param {string} str - String to sanitize
 * @returns {string} Sanitized HTML string
 */
function sanitizeHTML(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/**
 * Logger utility - only logs in development environment
 */
const Logger = {
    isDevelopment: window.location.hostname === 'localhost' ||
                   window.location.hostname === '127.0.0.1' ||
                   window.location.hostname.includes('localhost'),

    /**
     * Log informational message (development only)
     */
    log(...args) {
        if (this.isDevelopment) {
            console.log('[CRUSH]', ...args);
        }
    },

    /**
     * Log error message
     * In production, this could send to error tracking service
     */
    error(...args) {
        if (this.isDevelopment) {
            console.error('[CRUSH ERROR]', ...args);
        }
        // TODO: In production, send to error tracking service
        // if (!this.isDevelopment && window.errorTracker) {
        //     window.errorTracker.log(...args);
        // }
    },

    /**
     * Log warning message (development only)
     */
    warn(...args) {
        if (this.isDevelopment) {
            console.warn('[CRUSH WARN]', ...args);
        }
    },

    /**
     * Log info message with timestamp
     */
    info(...args) {
        if (this.isDevelopment) {
            console.info('[CRUSH INFO]', new Date().toISOString(), ...args);
        }
    }
};

/**
 * Show user-friendly error message
 * @param {string} message - Error message to display
 * @param {Element} container - Container element for error message
 */
function showError(message, container) {
    if (!container) {
        Logger.warn('showError called without container element');
        return;
    }

    container.innerHTML = `
        <div class="alert alert-danger alert-dismissible fade show" role="alert">
            <i class="bi bi-exclamation-triangle-fill"></i> ${sanitizeHTML(message)}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
    `;
}

/**
 * Show success message
 * @param {string} message - Success message to display
 * @param {Element} container - Container element for success message
 */
function showSuccess(message, container) {
    if (!container) {
        Logger.warn('showSuccess called without container element');
        return;
    }

    container.innerHTML = `
        <div class="alert alert-success alert-dismissible fade show" role="alert">
            <i class="bi bi-check-circle-fill"></i> ${sanitizeHTML(message)}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
    `;
}

/**
 * Debounce function to limit API calls
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in milliseconds
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Format date for display
 * @param {Date|string} date - Date to format
 * @returns {string} Formatted date string
 */
function formatDate(date) {
    const d = new Date(date);
    const options = {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    };
    return d.toLocaleDateString('en-US', options);
}

// Export utilities to global scope
window.CrushUtils = {
    getCsrfToken,
    sanitizeHTML,
    Logger,
    showError,
    showSuccess,
    debounce,
    formatDate
};

Logger.info('Crush.lu utilities loaded');
