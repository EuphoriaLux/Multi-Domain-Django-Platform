/**
 * Crush.lu PWA Install Prompt
 * Provides a custom install button for the Progressive Web App
 */

class PWAInstaller {
    constructor() {
        this.deferredPrompt = null;
        this.installButton = null;
        this.init();
    }

    init() {
        // Listen for the beforeinstallprompt event
        window.addEventListener('beforeinstallprompt', (e) => {
            console.log('[PWA Installer] Install prompt available');
            // Prevent the mini-infobar from appearing on mobile
            e.preventDefault();
            // Stash the event so it can be triggered later
            this.deferredPrompt = e;
            // Show the install button
            this.showInstallButton();
        });

        // Listen for successful installation
        window.addEventListener('appinstalled', () => {
            console.log('[PWA Installer] Crush.lu installed successfully');
            this.hideInstallButton();
            this.deferredPrompt = null;
            this.showInstallSuccess();
        });

        // Check if already installed
        if (window.matchMedia('(display-mode: standalone)').matches) {
            console.log('[PWA Installer] Running as PWA');
            this.hideInstallButton();
        }
    }

    showInstallButton() {
        // Check if user has dismissed the banner before
        if (localStorage.getItem('crush-pwa-install-dismissed')) {
            return;
        }

        // Use the existing banner from the template (translated by Django)
        const banner = document.getElementById('pwa-install-banner');
        this.installButton = document.getElementById('pwa-install-button');

        if (banner && this.installButton) {
            // Show the banner
            banner.style.display = 'block';

            // Add event listeners if not already added
            if (!this.installButton.hasAttribute('data-listeners-added')) {
                this.installButton.addEventListener('click', () => this.handleInstall());
                this.installButton.setAttribute('data-listeners-added', 'true');

                const dismissButton = document.getElementById('pwa-dismiss-button');
                if (dismissButton) {
                    dismissButton.addEventListener('click', () => this.dismissBanner());
                }
            }
        }
    }

    async handleInstall() {
        if (!this.deferredPrompt) {
            console.log('[PWA Installer] No deferred prompt available');
            return;
        }

        console.log('[PWA Installer] Showing install prompt');

        // Show the install prompt
        this.deferredPrompt.prompt();

        // Wait for the user to respond to the prompt
        const { outcome } = await this.deferredPrompt.userChoice;
        console.log(`[PWA Installer] User choice: ${outcome}`);

        if (outcome === 'accepted') {
            console.log('[PWA Installer] User accepted the install prompt');
        } else {
            console.log('[PWA Installer] User dismissed the install prompt');
        }

        // Clear the deferred prompt
        this.deferredPrompt = null;
        this.hideInstallButton();
    }

    hideInstallButton() {
        const banner = document.getElementById('pwa-install-banner');
        if (banner) {
            banner.style.display = 'none';
        }
    }

    dismissBanner() {
        // Remember user dismissed the banner
        localStorage.setItem('crush-pwa-install-dismissed', 'true');
        this.hideInstallButton();
    }

    showInstallSuccess() {
        // Use the existing success banner from the template (translated by Django)
        const successBanner = document.getElementById('pwa-install-success');

        if (successBanner) {
            // Show the success banner
            successBanner.style.display = 'flex';

            // Add close button handler if not already added
            const closeButton = document.getElementById('pwa-success-close');
            if (closeButton && !closeButton.hasAttribute('data-listeners-added')) {
                closeButton.addEventListener('click', function() {
                    successBanner.style.display = 'none';
                });
                closeButton.setAttribute('data-listeners-added', 'true');
            }

            // Auto-hide after 5 seconds
            setTimeout(function() {
                successBanner.style.display = 'none';
            }, 5000);
        }
    }
}

// Initialize the PWA installer
if ('serviceWorker' in navigator) {
    const pwaInstaller = new PWAInstaller();
}

// Add CSS styles
const style = document.createElement('style');
style.textContent = `
    .pwa-install-banner {
        background: var(--gradient-subtle, linear-gradient(135deg, rgba(155, 89, 182, 0.1) 0%, rgba(255, 107, 157, 0.1) 100%));
        border-bottom: 2px solid rgba(155, 89, 182, 0.2);
        animation: slideDown var(--transition-base, 0.3s ease);
    }

    @keyframes slideDown {
        from {
            transform: translateY(-100%);
            opacity: 0;
        }
        to {
            transform: translateY(0);
            opacity: 1;
        }
    }

    .pwa-install-banner .btn-crush-primary {
        background: var(--gradient-primary, linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%));
        border: none;
        color: white;
        font-weight: var(--font-semibold, 600);
        padding: var(--space-2, 0.5rem) var(--space-4, 1rem);
        border-radius: var(--radius-pill, 50px);
    }

    .pwa-install-banner .btn-crush-primary:hover {
        transform: translateY(-2px);
        box-shadow: var(--shadow-purple, 0 5px 10px rgba(155, 89, 182, 0.3));
    }
`;
document.head.appendChild(style);
