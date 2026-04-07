/**
 * Crush.lu PWA Install Prompt
 * Provides a custom install button for the Progressive Web App
 */

class PWAInstaller {
    constructor() {
        this.deferredPrompt = null;
        this.installButton = null;
        this.handleDismissEvent = this.dismissBanner.bind(this);
        this.platform = this.detectPlatform();
        this.init();
    }

    detectPlatform() {
        var ua = navigator.userAgent || "";
        if (/iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)) {
            return "ios";
        }
        return "other";
    }

    init() {
        // Listen once for dismiss event emitted by Alpine component
        window.addEventListener("pwa-dismiss-install", this.handleDismissEvent);

        // Check if already installed (standalone mode)
        if (window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true) {
            this.hideInstallButton();
            return;
        }

        // Listen for the beforeinstallprompt event (Android/Chrome)
        window.addEventListener("beforeinstallprompt", (e) => {
            // Prevent the mini-infobar from appearing on mobile
            e.preventDefault();
            // Stash the event so it can be triggered later
            this.deferredPrompt = e;
            // Show the install button
            this.showInstallButton();
        });

        // Listen for successful installation
        window.addEventListener("appinstalled", () => {
            this.hideInstallButton();
            this.deferredPrompt = null;
            this.showInstallSuccess();
        });

        // On iOS, beforeinstallprompt never fires — show banner with guide
        if (this.platform === "ios") {
            this.showInstallButton();
        }
    }

    showInstallButton() {
        // Check if user has dismissed the banner (re-show after 30 days)
        var dismissed = localStorage.getItem("crush-pwa-install-dismissed");
        if (dismissed) {
            var dismissedAt = parseInt(dismissed, 10);
            var thirtyDaysMs = 30 * 24 * 60 * 60 * 1000;
            if (!isNaN(dismissedAt) && Date.now() - dismissedAt < thirtyDaysMs) {
                return;
            }
            // Expired or legacy 'true' value — remove and continue showing
            localStorage.removeItem("crush-pwa-install-dismissed");
        }

        // Dispatch event to show banner with platform info (Alpine.js handles visibility)
        window.dispatchEvent(new CustomEvent("pwa-show-install", {
            detail: { platform: this.platform }
        }));

        // Set up install button listener (Android uses native prompt, iOS opens guide)
        this.installButton = document.getElementById("pwa-install-button");
        if (this.installButton && !this.installButton.__pwaInstallBound) {
            this.installButton.addEventListener("click", () => this.handleInstall());
            this.installButton.__pwaInstallBound = true;
        }
    }

    async handleInstall() {
        // On iOS, open the instruction guide instead
        if (this.platform === "ios") {
            window.dispatchEvent(new CustomEvent("pwa-show-ios-guide"));
            return;
        }

        if (!this.deferredPrompt) {
            return;
        }

        // Show the install prompt
        this.deferredPrompt.prompt();

        // Wait for the user to respond to the prompt
        await this.deferredPrompt.userChoice;

        // Clear the deferred prompt
        this.deferredPrompt = null;
        this.hideInstallButton();
    }

    hideInstallButton() {
        // Dispatch event to hide banner (Alpine.js handles visibility)
        window.dispatchEvent(new CustomEvent("pwa-hide-install"));
    }

    dismissBanner() {
        // Remember user dismissed the banner
        localStorage.setItem("crush-pwa-install-dismissed", Date.now().toString());
        this.hideInstallButton();
    }

    showInstallSuccess() {
        // Dispatch event to show success toast (Alpine.js handles visibility and auto-hide)
        window.dispatchEvent(new CustomEvent("pwa-install-success"));
    }
}

// Initialize the PWA installer
if ("serviceWorker" in navigator) {
    const pwaInstaller = new PWAInstaller();
}

// Add CSS styles
const style = document.createElement("style");
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
