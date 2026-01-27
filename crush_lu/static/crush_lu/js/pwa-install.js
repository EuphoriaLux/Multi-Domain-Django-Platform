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
            // Prevent the mini-infobar from appearing on mobile
            e.preventDefault();
            // Stash the event so it can be triggered later
            this.deferredPrompt = e;
            // Show the install button
            this.showInstallButton();
        });

        // Listen for successful installation
        window.addEventListener('appinstalled', () => {
            this.hideInstallButton();
            this.deferredPrompt = null;
            this.showInstallSuccess();
        });

        // Check if already installed
        if (window.matchMedia('(display-mode: standalone)').matches) {
            this.hideInstallButton();
        }
    }

    showInstallButton() {
        // Check if user has dismissed the banner before
        if (localStorage.getItem('crush-pwa-install-dismissed')) {
            return;
        }

        // Dispatch event to show banner (Alpine.js handles visibility)
        window.dispatchEvent(new CustomEvent('pwa-show-install'));

        // Set up install button listener
        this.installButton = document.getElementById('pwa-install-button');
        if (this.installButton && !this.installButton.hasAttribute('data-listeners-added')) {
            this.installButton.addEventListener('click', () => this.handleInstall());
            this.installButton.setAttribute('data-listeners-added', 'true');
        }

        // Listen for dismiss event from Alpine component
        window.addEventListener('pwa-dismiss-install', () => this.dismissBanner());
    }

    async handleInstall() {
        if (!this.deferredPrompt) {
            return;
        }

        // Show the install prompt
        this.deferredPrompt.prompt();

        // Wait for the user to respond to the prompt
        const { outcome } = await this.deferredPrompt.userChoice;

        // Clear the deferred prompt
        this.deferredPrompt = null;
        this.hideInstallButton();
    }

    hideInstallButton() {
        // Dispatch event to hide banner (Alpine.js handles visibility)
        window.dispatchEvent(new CustomEvent('pwa-hide-install'));
    }

    dismissBanner() {
        // Remember user dismissed the banner
        localStorage.setItem('crush-pwa-install-dismissed', 'true');
        this.hideInstallButton();
    }

    showInstallSuccess() {
        // Dispatch event to show success toast (Alpine.js handles visibility and auto-hide)
        window.dispatchEvent(new CustomEvent('pwa-install-success'));
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
