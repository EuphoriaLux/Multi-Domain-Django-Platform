/**
 * Crush.lu PWA Update Handler
 * Detects service worker updates and shows a user-friendly update banner
 */

class PWAUpdater {
    constructor() {
        this.registration = null;
        this.updateBanner = null;
        this.init();
    }

    async init() {
        if (!('serviceWorker' in navigator)) {
            console.log('[PWAUpdater] Service workers not supported');
            return;
        }

        try {
            // Wait for service worker to be ready
            this.registration = await navigator.serviceWorker.ready;
            console.log('[PWAUpdater] Service worker ready');

            // Listen for new service worker installations
            this.registration.addEventListener('updatefound', () => {
                console.log('[PWAUpdater] New service worker found');
                const newWorker = this.registration.installing;

                newWorker.addEventListener('statechange', () => {
                    console.log('[PWAUpdater] New worker state:', newWorker.state);

                    // Only show update banner if there's a controller (existing SW)
                    // and the new worker is installed (waiting to activate)
                    if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                        console.log('[PWAUpdater] Update available, showing banner');
                        this.showUpdateBanner();
                    }
                });
            });

            // Listen for controller change (new SW activated)
            navigator.serviceWorker.addEventListener('controllerchange', () => {
                console.log('[PWAUpdater] Controller changed, reloading page');
                window.location.reload();
            });

            // Check if there's already a waiting worker (e.g., from a previous visit)
            if (this.registration.waiting) {
                console.log('[PWAUpdater] Found waiting worker on init');
                this.showUpdateBanner();
            }

        } catch (error) {
            console.error('[PWAUpdater] Initialization error:', error);
        }
    }

    showUpdateBanner() {
        // Don't show duplicate banners
        if (this.updateBanner && document.body.contains(this.updateBanner)) {
            return;
        }

        this.updateBanner = document.createElement('div');
        this.updateBanner.className = 'pwa-update-banner';
        this.updateBanner.setAttribute('role', 'alert');
        this.updateBanner.setAttribute('aria-live', 'polite');
        this.updateBanner.innerHTML = `
            <div class="pwa-update-content">
                <span class="pwa-update-icon">&#x2728;</span>
                <span class="pwa-update-text">A new version of Crush.lu is available!</span>
            </div>
            <div class="pwa-update-actions">
                <button class="pwa-update-btn pwa-update-btn-primary" onclick="window.CrushUpdate.update()">
                    Update Now
                </button>
                <button class="pwa-update-btn pwa-update-btn-secondary" onclick="window.CrushUpdate.dismiss()">
                    Later
                </button>
            </div>
        `;

        // Add styles if not already present
        this.injectStyles();

        // Add to page
        document.body.appendChild(this.updateBanner);

        // Trigger animation
        requestAnimationFrame(() => {
            this.updateBanner.classList.add('pwa-update-banner-visible');
        });
    }

    injectStyles() {
        if (document.getElementById('pwa-update-styles')) {
            return;
        }

        const styles = document.createElement('style');
        styles.id = 'pwa-update-styles';
        styles.textContent = `
            .pwa-update-banner {
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                z-index: 9999;
                background: var(--gradient-primary, linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%));
                color: white;
                padding: var(--space-3, 12px) var(--space-4, 16px);
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: space-between;
                gap: var(--space-3, 12px);
                box-shadow: var(--shadow-purple, 0 4px 12px rgba(155, 89, 182, 0.3));
                transform: translateY(-100%);
                transition: transform var(--transition-base, 0.3s ease);
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            }

            .pwa-update-banner-visible {
                transform: translateY(0);
            }

            .pwa-update-content {
                display: flex;
                align-items: center;
                gap: var(--space-2, 8px);
                flex: 1;
            }

            .pwa-update-icon {
                font-size: var(--text-xl, 1.2em);
            }

            .pwa-update-text {
                font-size: var(--text-sm, 14px);
                font-weight: var(--font-medium, 500);
            }

            .pwa-update-actions {
                display: flex;
                gap: var(--space-2, 8px);
            }

            .pwa-update-btn {
                border: none;
                border-radius: var(--radius-xl, 20px);
                padding: var(--space-2, 8px) var(--space-4, 16px);
                font-size: var(--text-sm, 13px);
                font-weight: var(--font-semibold, 600);
                cursor: pointer;
                transition: all var(--transition-fast, 0.2s ease);
            }

            .pwa-update-btn-primary {
                background: white;
                color: var(--crush-purple, #9B59B6);
            }

            .pwa-update-btn-primary:hover {
                background: var(--crush-light, #f8f8f8);
                transform: scale(1.05);
            }

            .pwa-update-btn-secondary {
                background: rgba(255, 255, 255, 0.2);
                color: white;
            }

            .pwa-update-btn-secondary:hover {
                background: rgba(255, 255, 255, 0.3);
            }

            @media (max-width: 480px) {
                .pwa-update-banner {
                    flex-direction: column;
                    text-align: center;
                }

                .pwa-update-content {
                    justify-content: center;
                }

                .pwa-update-actions {
                    width: 100%;
                    justify-content: center;
                }
            }
        `;
        document.head.appendChild(styles);
    }

    update() {
        console.log('[PWAUpdater] User requested update');

        if (this.registration && this.registration.waiting) {
            // Tell the waiting service worker to activate
            this.registration.waiting.postMessage({ type: 'SKIP_WAITING' });
        }

        // Remove the banner
        this.dismiss();
    }

    dismiss() {
        if (this.updateBanner) {
            this.updateBanner.classList.remove('pwa-update-banner-visible');
            setTimeout(() => {
                this.updateBanner.remove();
                this.updateBanner = null;
            }, 300);
        }
    }
}

// Initialize and expose globally
window.CrushUpdate = new PWAUpdater();
