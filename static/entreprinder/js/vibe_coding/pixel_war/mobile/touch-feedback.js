/**
 * Touch Feedback System for Pixel War
 * Provides visual and haptic feedback for touch interactions
 */

import { triggerHapticFeedback, addElementTouchFeedback } from './mobile-utils.js';

export class PixelWarTouchFeedback {
    static init() {
        // Add touch feedback to all interactive elements
        this.addTouchFeedbackToElements();
        
        // Setup custom feedback handlers
        this.setupCustomHandlers();
    }
    
    /**
     * Adds touch feedback to common interactive elements
     */
    static addTouchFeedbackToElements() {
        const selectors = [
            'button',
            '.color-btn', 
            '.zoom-btn', 
            '.nav-btn', 
            '.tool-btn',
            '.color-btn-enhanced',
            '.recent-color-btn',
            '.corner-nav-btn',
            '.mode-toggle-btn',
            '.onboarding-btn'
        ];
        
        selectors.forEach(selector => {
            document.querySelectorAll(selector).forEach(element => {
                this.addTouchFeedback(element);
            });
        });
    }
    
    /**
     * Adds touch feedback to a specific element
     * @param {HTMLElement} element - Element to add feedback to
     * @param {Object} options - Configuration options
     */
    static addTouchFeedback(element, options = {}) {
        const {
            hapticType = 'light',
            activeClass = 'active',
            duration = 150,
            skipIfAlreadyAdded = true
        } = options;
        
        // Avoid duplicate event listeners
        if (skipIfAlreadyAdded && element.classList.contains('touch-feedback')) {
            return;
        }
        
        element.classList.add('touch-feedback');
        
        const addFeedback = (e) => {
            // Add visual feedback
            element.classList.add(activeClass);
            
            // Add haptic feedback
            triggerHapticFeedback(hapticType);
            
            // Remove visual feedback after duration
            setTimeout(() => {
                element.classList.remove(activeClass);
            }, duration);
        };
        
        // Handle both mouse and touch events
        element.addEventListener('mousedown', addFeedback);
        element.addEventListener('touchstart', addFeedback, { passive: true });
    }
    
    /**
     * Setup custom feedback handlers for specific interactions
     */
    static setupCustomHandlers() {
        // Canvas pixel placement feedback
        this.setupCanvasFeedback();
        
        // Long press feedback
        this.setupLongPressFeedback();
        
        // Swipe feedback
        this.setupSwipeFeedback();
    }
    
    /**
     * Setup feedback for canvas pixel placement
     */
    static setupCanvasFeedback() {
        const canvas = document.querySelector('#pixelWarCanvas');
        if (!canvas) return;
        
        let touchTimeout = null;
        
        const handleTouchStart = (e) => {
            // Light haptic for touch start
            triggerHapticFeedback('light');
            
            // Setup timeout for long press
            touchTimeout = setTimeout(() => {
                // Medium haptic for long press
                triggerHapticFeedback('medium');
            }, 500);
        };
        
        const handleTouchEnd = () => {
            // Clear long press timeout
            if (touchTimeout) {
                clearTimeout(touchTimeout);
                touchTimeout = null;
            }
        };
        
        canvas.addEventListener('touchstart', handleTouchStart, { passive: true });
        canvas.addEventListener('touchend', handleTouchEnd, { passive: true });
        canvas.addEventListener('touchcancel', handleTouchEnd, { passive: true });
    }
    
    /**
     * Setup long press feedback for elements with data-long-press
     */
    static setupLongPressFeedback() {
        document.querySelectorAll('[data-long-press]').forEach(element => {
            let longPressTimeout = null;
            let longPressTriggered = false;
            
            const startLongPress = () => {
                longPressTriggered = false;
                longPressTimeout = setTimeout(() => {
                    longPressTriggered = true;
                    triggerHapticFeedback('heavy');
                    
                    // Trigger long press event
                    const event = new CustomEvent('longpress', { 
                        detail: { element } 
                    });
                    element.dispatchEvent(event);
                }, 500);
            };
            
            const endLongPress = () => {
                if (longPressTimeout) {
                    clearTimeout(longPressTimeout);
                    longPressTimeout = null;
                }
                return longPressTriggered;
            };
            
            element.addEventListener('touchstart', startLongPress, { passive: true });
            element.addEventListener('touchend', endLongPress, { passive: true });
            element.addEventListener('touchcancel', endLongPress, { passive: true });
            element.addEventListener('touchmove', endLongPress, { passive: true });
        });
    }
    
    /**
     * Setup swipe feedback for gesture recognition
     */
    static setupSwipeFeedback() {
        let startX, startY, startTime;
        
        const handleTouchStart = (e) => {
            const touch = e.touches[0];
            startX = touch.clientX;
            startY = touch.clientY;
            startTime = Date.now();
        };
        
        const handleTouchEnd = (e) => {
            if (!startX || !startY) return;
            
            const touch = e.changedTouches[0];
            const deltaX = touch.clientX - startX;
            const deltaY = touch.clientY - startY;
            const deltaTime = Date.now() - startTime;
            
            // Only consider as swipe if fast enough (under 300ms) and significant distance
            if (deltaTime > 300 || (Math.abs(deltaX) < 50 && Math.abs(deltaY) < 50)) {
                return;
            }
            
            let swipeDirection = null;
            
            // Determine swipe direction
            if (Math.abs(deltaX) > Math.abs(deltaY)) {
                // Horizontal swipe
                swipeDirection = deltaX > 0 ? 'right' : 'left';
            } else {
                // Vertical swipe
                swipeDirection = deltaY > 0 ? 'down' : 'up';
            }
            
            // Provide feedback for recognized swipe
            if (swipeDirection) {
                triggerHapticFeedback('light');
                
                // Dispatch swipe event
                const swipeEvent = new CustomEvent('swipe', {
                    detail: {
                        direction: swipeDirection,
                        deltaX,
                        deltaY,
                        duration: deltaTime
                    }
                });
                document.dispatchEvent(swipeEvent);
            }
            
            // Reset
            startX = startY = startTime = null;
        };
        
        document.addEventListener('touchstart', handleTouchStart, { passive: true });
        document.addEventListener('touchend', handleTouchEnd, { passive: true });
    }
    
    /**
     * Provides success feedback (visual + haptic)
     * @param {HTMLElement} element - Optional element to animate
     */
    static showSuccessFeedback(element = null) {
        triggerHapticFeedback('medium');
        
        if (element) {
            element.classList.add('success-feedback');
            setTimeout(() => {
                element.classList.remove('success-feedback');
            }, 300);
        }
    }
    
    /**
     * Provides error feedback (visual + haptic)
     * @param {HTMLElement} element - Optional element to animate
     */
    static showErrorFeedback(element = null) {
        triggerHapticFeedback('heavy');
        
        if (element) {
            element.classList.add('error-feedback');
            setTimeout(() => {
                element.classList.remove('error-feedback');
            }, 300);
        }
    }
    
    /**
     * Provides boundary feedback when user reaches canvas limits
     */
    static showBoundaryFeedback() {
        triggerHapticFeedback('heavy');
        
        // Add temporary boundary indicator if it exists
        const indicator = document.querySelector('.boundary-indicator');
        if (indicator) {
            indicator.classList.add('active');
            setTimeout(() => {
                indicator.classList.remove('active');
            }, 500);
        }
    }
    
    /**
     * Manually trigger haptic feedback (exposed method)
     * @param {string} type - Type of feedback ('light', 'medium', 'heavy')
     */
    static triggerHapticFeedback(type = 'light') {
        return triggerHapticFeedback(type);
    }
    
    /**
     * Remove touch feedback from an element
     * @param {HTMLElement} element - Element to remove feedback from
     */
    static removeTouchFeedback(element) {
        element.classList.remove('touch-feedback', 'active');
        
        // Remove event listeners by cloning the element (drastic but effective)
        const newElement = element.cloneNode(true);
        element.parentNode.replaceChild(newElement, element);
        
        return newElement;
    }
}

// Make available globally for HTML onclick handlers
window.PixelWarTouchFeedback = PixelWarTouchFeedback;