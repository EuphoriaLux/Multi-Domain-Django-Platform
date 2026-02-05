/**
 * Toast Notification Component for Crush.lu
 * CSP-compliant Alpine.js component for user feedback
 *
 * Usage:
 * Alpine.store('toasts').add({ type: 'success', message: 'Profile saved!' })
 * Or via event: window.dispatchEvent(new CustomEvent('show-toast', { detail: { type: 'success', message: 'Done!' }}))
 */

document.addEventListener('alpine:init', () => {
    // Global toast store for managing multiple toasts
    Alpine.store('toasts', {
        items: [],
        maxVisible: 3,
        defaultDuration: 5000,

        add(toast) {
            const id = Date.now() + Math.random();
            const newToast = {
                id,
                type: toast.type || 'info', // success, error, info, warning
                message: toast.message || '',
                duration: toast.duration || this.defaultDuration,
                dismissible: toast.dismissible !== false
            };

            this.items.push(newToast);

            // Limit visible toasts
            if (this.items.length > this.maxVisible) {
                this.items.shift();
            }

            // Auto-dismiss if duration > 0
            if (newToast.duration > 0) {
                setTimeout(() => {
                    this.remove(id);
                }, newToast.duration);
            }

            return id;
        },

        remove(id) {
            const index = this.items.findIndex(toast => toast.id === id);
            if (index > -1) {
                this.items.splice(index, 1);
            }
        },

        clear() {
            this.items = [];
        }
    });

    // Toast container component
    Alpine.data('toastContainer', () => ({
        get toasts() {
            return Alpine.store('toasts').items;
        },

        init() {
            // Listen for global toast events
            window.addEventListener('show-toast', (event) => {
                if (event.detail) {
                    Alpine.store('toasts').add(event.detail);
                }
            });

            // Listen for HTMX events
            document.body.addEventListener('htmx:afterRequest', (event) => {
                const response = event.detail.xhr;

                // Check for HX-Trigger header with toast data
                const triggerHeader = response.getResponseHeader('HX-Trigger');
                if (triggerHeader) {
                    try {
                        const triggers = JSON.parse(triggerHeader);
                        if (triggers.showToast) {
                            Alpine.store('toasts').add(triggers.showToast);
                        }
                    } catch (e) {
                        // Silent fail if not JSON
                    }
                }
            });
        }
    }));

    // Individual toast component
    Alpine.data('toast', (toastData) => ({
        toast: toastData,
        isVisible: false,
        isRemoving: false,

        init() {
            // Animate in
            requestAnimationFrame(() => {
                this.isVisible = true;
            });
        },

        get typeStyles() {
            const styles = {
                success: 'bg-green-50 border-green-200 text-green-800',
                error: 'bg-red-50 border-red-200 text-red-800',
                warning: 'bg-yellow-50 border-yellow-200 text-yellow-800',
                info: 'bg-blue-50 border-blue-200 text-blue-800'
            };
            return styles[this.toast.type] || styles.info;
        },

        get iconPath() {
            const icons = {
                success: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
                error: 'M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z',
                warning: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z',
                info: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
            };
            return icons[this.toast.type] || icons.info;
        },

        get iconColor() {
            const colors = {
                success: 'text-green-400',
                error: 'text-red-400',
                warning: 'text-yellow-400',
                info: 'text-blue-400'
            };
            return colors[this.toast.type] || colors.info;
        },

        dismiss() {
            this.isRemoving = true;
            setTimeout(() => {
                Alpine.store('toasts').remove(this.toast.id);
            }, 300); // Match animation duration
        }
    }));
});
