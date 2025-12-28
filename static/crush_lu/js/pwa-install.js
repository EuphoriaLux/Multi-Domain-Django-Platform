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
            <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                <div class="flex items-center justify-between py-3 gap-4">
                    <!-- Icon and Text -->
                    <div class="flex items-center gap-3 min-w-0">
                        <div class="flex-shrink-0 w-10 h-10 rounded-full bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center">
                            <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"/>
                            </svg>
                        </div>
                        <div class="min-w-0">
                            <h6 class="font-semibold text-gray-900 text-sm sm:text-base truncate">Install Crush.lu App</h6>
                            <p class="text-xs sm:text-sm text-gray-500 truncate">Quick access & offline features</p>
                        </div>
                    </div>

                    <!-- Buttons -->
                    <div class="flex items-center gap-2 flex-shrink-0">
                        <button id="pwa-install-button" class="inline-flex items-center gap-1.5 px-3 py-1.5 sm:px-4 sm:py-2 text-sm font-semibold text-white bg-gradient-to-r from-purple-600 to-pink-500 rounded-full hover:shadow-lg hover:shadow-purple-500/30 transition-all duration-200 hover:-translate-y-0.5">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
                            </svg>
                            <span class="hidden sm:inline">Install</span>
                        </button>
                        <button id="pwa-dismiss-button" class="p-1.5 sm:p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-full transition-colors" aria-label="Dismiss">
                            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                            </svg>
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
        // Show a success message using Tailwind
        const successBanner = document.createElement('div');
        successBanner.className = 'fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-green-50 border border-green-200 rounded-lg shadow-lg px-4 py-3 flex items-center gap-3 animate-fade-in';
        successBanner.innerHTML = `
            <svg class="w-5 h-5 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            <span class="text-green-800 font-medium text-sm">Crush.lu installed! Access it from your home screen.</span>
            <button type="button" class="pwa-success-close ml-2 text-green-400 hover:text-green-600 transition-colors">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                </svg>
            </button>
        `;
        document.body.appendChild(successBanner);

        // Add close button handler
        successBanner.querySelector('.pwa-success-close').addEventListener('click', function() {
            successBanner.remove();
        });

        // Auto-remove after 5 seconds
        setTimeout(function() {
            if (successBanner.parentNode) {
                successBanner.remove();
            }
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
