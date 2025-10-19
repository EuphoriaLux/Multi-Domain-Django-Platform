/**
 * Mobile UX Module Index
 * Exports all mobile UX classes and provides initialization
 */

import { PixelWarOnboarding } from './onboarding.js';
import { PixelWarModeManager } from './mode-manager.js';
import { PixelWarColorManager } from './color-manager.js';
import { PixelWarTouchFeedback } from './touch-feedback.js';
import { PixelWarNavigation } from './navigation.js';
import * as MobileUtils from './mobile-utils.js';

// Re-export all classes
export {
    PixelWarOnboarding,
    PixelWarModeManager,
    PixelWarColorManager,
    PixelWarTouchFeedback,
    PixelWarNavigation,
    MobileUtils
};

/**
 * Initialize all mobile UX systems
 * @param {Object} options - Configuration options
 */
export function initMobileUX(options = {}) {
    const {
        forceInit = false,
        skipOnboarding = false,
        enableColorSwipeGesture = true,
        debugMode = false
    } = options;
    
    // Check if we should initialize mobile UX
    const isMobile = forceInit || MobileUtils.isMobileDevice();
    
    if (!isMobile && !forceInit) {
        if (debugMode) {
            console.log('🎮 Skipping mobile UX initialization (not a mobile device)');
        }
        return false;
    }
    
    if (debugMode) {
        console.log('🎮 Initializing Mobile UX Systems...');
    }
    
    try {
        // Initialize touch feedback first (required by other modules)
        PixelWarTouchFeedback.init();
        
        // Initialize onboarding (will show if needed)
        if (!skipOnboarding) {
            PixelWarOnboarding.init();
        }
        
        // Initialize mode manager
        PixelWarModeManager.init();
        
        // Initialize color manager
        PixelWarColorManager.init();
        
        // Setup color swipe gesture if enabled
        if (enableColorSwipeGesture) {
            PixelWarColorManager.setupSwipeGesture();
        }
        
        // Initialize navigation
        PixelWarNavigation.init();
        
        if (debugMode) {
            console.log('✅ Mobile UX Systems initialized successfully');
        }
        
        return true;
    } catch (error) {
        console.error('❌ Failed to initialize mobile UX systems:', error);
        return false;
    }
}

/**
 * Initialize mobile UX with legacy global function name
 * Maintains compatibility with existing code
 */
window.initMobileUX = initMobileUX;

// Make all classes available globally for backwards compatibility
window.PixelWarOnboarding = PixelWarOnboarding;
window.PixelWarModeManager = PixelWarModeManager;
window.PixelWarColorManager = PixelWarColorManager;
window.PixelWarTouchFeedback = PixelWarTouchFeedback;
window.PixelWarNavigation = PixelWarNavigation;

// Also expose utility functions globally for debugging
Object.keys(MobileUtils).forEach(key => {
    window[key] = MobileUtils[key];
});

// Auto-initialize if the document is already loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initMobileUX({ debugMode: true });
    });
} else {
    // Document is already loaded
    setTimeout(() => {
        initMobileUX({ debugMode: true });
    }, 100);
}