/**
 * Pixel War Application Entry Point
 * Coordinates all modules and initializes the application
 */

// Import core modules
import { PixelWarConfig } from './config/pixel-war-config.js';
import { PixelWarAPI, APIError } from './api/pixel-war-api.js';
import { CanvasRenderer } from './rendering/canvas-renderer.js';
import InputHandler from './input/input-handler.js';
import { RateLimiter } from './core/rate-limiter.js';
import NotificationManager from './core/notification-manager.js';
import { PixelWar } from './core/pixel-war.js';

// Import mobile UX modules
import { 
    PixelWarOnboarding, 
    PixelWarModeManager, 
    PixelWarColorManager, 
    PixelWarTouchFeedback, 
    PixelWarNavigation,
    initMobileUX 
} from './mobile/index.js';

// Global application instance
let pixelWarInstance = null;

/**
 * Initialize the Pixel War application
 * @param {HTMLCanvasElement} canvas - The canvas element
 * @param {Object} options - Configuration options
 */
export function initPixelWar(canvas, options = {}) {
    try {
        // Create the main PixelWar instance
        pixelWarInstance = new PixelWar(canvas, options);
        
        // Make it globally accessible for backward compatibility
        window.pixelWar = pixelWarInstance;
        
        // Initialize mobile UX if on mobile device
        initMobileUX();
        
        console.log('✅ Pixel War application initialized');
        return pixelWarInstance;
    } catch (error) {
        console.error('❌ Failed to initialize Pixel War:', error);
        throw error;
    }
}

/**
 * Get the current PixelWar instance
 * @returns {PixelWar|null} The current instance or null if not initialized
 */
export function getPixelWarInstance() {
    return pixelWarInstance;
}

// Debug functions for development (global)
window.resetMobileOnboarding = function() {
    localStorage.removeItem('pixelWarOnboardingCompleted');
    localStorage.removeItem('pixelWarTouchMode');
    localStorage.removeItem('pixelWarRecentColors');
    console.log('🔄 Mobile onboarding reset. Reload page to see onboarding.');
    return 'Onboarding reset complete. Reload the page.';
};

window.forceMobileMode = function() {
    localStorage.setItem('forceMobileMode', 'true');
    location.reload();
};

window.testMobileNavigation = function() {
    if (window.pixelWar) {
        console.log('🧪 Testing mobile navigation...');
        
        // Test each corner
        const corners = ['top-left', 'top-right', 'bottom-left', 'bottom-right', 'center'];
        let index = 0;
        
        const testNext = () => {
            if (index < corners.length) {
                const corner = corners[index];
                console.log(`Moving to ${corner}...`);
                window.pixelWar.navigateToCorner(corner);
                index++;
                setTimeout(testNext, 2000); // Wait 2 seconds between moves
            } else {
                console.log('✅ Navigation test complete!');
            }
        };
        
        testNext();
    } else {
        console.log('❌ PixelWar not initialized');
    }
};

// Auto-initialization if canvas is found and DOM is ready
function autoInit() {
    const canvas = document.getElementById('pixelCanvas');
    if (canvas) {
        // Extract canvas configuration from data attributes or use defaults
        const canvasId = canvas.dataset.canvasId || 1;
        const config = {
            id: parseInt(canvasId),  // Use 'id' instead of 'canvasId' for consistency with original
            canvasId: parseInt(canvasId),  // Keep both for compatibility
            // Use Django config first, fall back to canvas attributes
            ...(window.CANVAS_CONFIG || {}),
            // DEPRECATED: Don't override with canvas HTML dimensions 
            // width: parseInt(canvas.width) || parseInt(canvas.getAttribute('width')) || 200,
            // height: parseInt(canvas.height) || parseInt(canvas.getAttribute('height')) || 200,
            ...(window.pixelWarConfig || {})
        };
        
        initPixelWar(canvas, config);
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoInit);
} else {
    autoInit();
}

// Export all classes for advanced usage
export {
    PixelWar,
    PixelWarConfig,
    PixelWarAPI,
    APIError,
    CanvasRenderer,
    InputHandler,
    RateLimiter,
    NotificationManager,
    PixelWarOnboarding,
    PixelWarModeManager,
    PixelWarColorManager,
    PixelWarTouchFeedback,
    PixelWarNavigation
};