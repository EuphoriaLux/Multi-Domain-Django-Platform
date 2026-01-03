/**
 * NotificationManager - Handles all notification display and management
 * Extracted from pixel_war_refactored.js (lines 1082-1221)
 * 
 * Provides toast-style notifications with different types and animations.
 * Supports auto-dismiss and double-click to dismiss functionality.
 */
class NotificationManager {
    constructor() {
        this.container = this.createContainer();
    }

    createContainer() {
        const existing = document.getElementById('notification-container');
        if (existing) return existing;
        
        const container = document.createElement('div');
        container.id = 'notification-container';
        container.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10000;
            display: flex;
            flex-direction: column;
            gap: 10px;
        `;
        document.body.appendChild(container);
        return container;
    }

    show(message, type = 'info', duration = 10000) {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        notification.style.cssText = `
            padding: 12px 20px;
            border-radius: 6px;
            color: white;
            font-size: 14px;
            font-weight: 500;
            margin-bottom: 8px;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            max-width: 320px;
            word-wrap: break-word;
            opacity: 0;
            transform: translateX(100%);
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            border-left: 4px solid rgba(255,255,255,0.3);
        `;
        
        // Add CSS animations to document if not already present
        if (!document.getElementById('notification-styles')) {
            const style = document.createElement('style');
            style.id = 'notification-styles';
            style.textContent = `
                @keyframes notificationSlideIn {
                    from {
                        opacity: 0;
                        transform: translateX(100%);
                    }
                    to {
                        opacity: 1;
                        transform: translateX(0);
                    }
                }
                @keyframes notificationSlideOut {
                    from {
                        opacity: 1;
                        transform: translateX(0);
                    }
                    to {
                        opacity: 0;
                        transform: translateX(100%);
                    }
                }
            `;
            document.head.appendChild(style);
        }

        // Set background color based on type
        const colors = {
            success: '#4caf50',
            error: '#f44336',
            warning: '#ff9800',
            info: '#2196f3'
        };
        notification.style.backgroundColor = colors[type] || colors.info;

        // Double-click to dismiss (prevent accidental dismissal)
        let clickCount = 0;
        notification.addEventListener('click', () => {
            clickCount++;
            if (clickCount === 1) {
                setTimeout(() => clickCount = 0, 1000); // Reset after 1 second
                // Show dismiss hint
                const originalText = notification.textContent;
                notification.textContent = 'Click again to dismiss - ' + originalText;
                setTimeout(() => {
                    if (notification.parentElement) {
                        notification.textContent = originalText;
                    }
                }, 2000);
            } else if (clickCount === 2) {
                this.remove(notification);
            }
        });

        this.container.appendChild(notification);
        
        // Trigger slide-in animation after element is in DOM
        requestAnimationFrame(() => {
            notification.style.opacity = '1';
            notification.style.transform = 'translateX(0)';
        });

        // Auto-remove after duration
        const autoRemoveTimer = setTimeout(() => {
            if (notification.parentElement) {
                this.remove(notification);
            }
        }, duration);
        
        // Store timer for potential early cancellation
        notification.dataset.autoRemoveTimer = autoRemoveTimer;
    }

    remove(notification) {
        if (!notification.parentElement) return; // Already removed
        
        // Cancel auto-remove timer if manually removing
        if (notification.dataset.autoRemoveTimer) {
            clearTimeout(parseInt(notification.dataset.autoRemoveTimer));
        }
        
        // Animate out
        notification.style.opacity = '0';
        notification.style.transform = 'translateX(100%)';
        
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 400); // Match transition duration
    }
}

export default NotificationManager;