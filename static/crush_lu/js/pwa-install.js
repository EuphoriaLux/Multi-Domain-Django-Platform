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
        // Check if we already have an install button
        this.installButton = document.getElementById('pwa-install-button');

        if (!this.installButton) {
            // Create install prompt banner if it doesn't exist
            this.createInstallBanner();
        } else {
            // Just show the existing button
            this.installButton.style.display = 'block';
        }
    }

    createInstallBanner() {
        // Check if user has dismissed the banner before
        if (localStorage.getItem('crush-pwa-install-dismissed')) {
            return;
        }

        const banner = document.createElement('div');
        banner.id = 'pwa-install-banner';
        banner.className = 'pwa-install-banner';
        banner.innerHTML = `
            <div class="container">
                <div class="row align-items-center py-3">
                    <div class="col-auto">
                        <i class="bi bi-phone" style="font-size: 2rem; color: #FF6B9D;"></i>
                    </div>
                    <div class="col">
                        <h6 class="mb-0">Install Crush.lu App</h6>
                        <small class="text-muted">Get quick access and offline features</small>
                    </div>
                    <div class="col-auto">
                        <button id="pwa-install-button" class="btn btn-sm btn-crush-primary me-2">
                            <i class="bi bi-download"></i> Install
                        </button>
                        <button id="pwa-dismiss-button" class="btn btn-sm btn-outline-secondary">
                            <i class="bi bi-x"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;

        // Insert at the top of the page (after navigation)
        const nav = document.querySelector('nav');
        if (nav && nav.nextSibling) {
            nav.parentNode.insertBefore(banner, nav.nextSibling);
        } else {
            document.body.insertBefore(banner, document.body.firstChild);
        }

        // Add event listeners
        this.installButton = document.getElementById('pwa-install-button');
        this.installButton.addEventListener('click', () => this.handleInstall());

        const dismissButton = document.getElementById('pwa-dismiss-button');
        dismissButton.addEventListener('click', () => this.dismissBanner());
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
        // Show a success message
        const successBanner = document.createElement('div');
        successBanner.className = 'alert alert-success alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3';
        successBanner.style.zIndex = '9999';
        successBanner.innerHTML = `
            <i class="bi bi-check-circle"></i>
            <strong>Crush.lu installed!</strong> You can now access it from your home screen.
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        document.body.appendChild(successBanner);

        // Auto-remove after 5 seconds
        setTimeout(() => {
            successBanner.remove();
        }, 5000);
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
        background: linear-gradient(135deg, rgba(155, 89, 182, 0.1) 0%, rgba(255, 107, 157, 0.1) 100%);
        border-bottom: 2px solid rgba(155, 89, 182, 0.2);
        animation: slideDown 0.3s ease-out;
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
        background: linear-gradient(135deg, #9B59B6 0%, #FF6B9D 100%);
        border: none;
        color: white;
        font-weight: 600;
        padding: 0.5rem 1rem;
        border-radius: 50px;
    }

    .pwa-install-banner .btn-crush-primary:hover {
        transform: translateY(-2px);
        box-shadow: 0 5px 10px rgba(155, 89, 182, 0.3);
    }
`;
document.head.appendChild(style);
