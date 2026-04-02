/**
 * Mobile Navigation Manager for Pixel War
 * Handles enhanced navigation controls and feedback for mobile devices
 */

import { showNotification, addPulseAnimation } from './mobile-utils.js';
import { PixelWarTouchFeedback } from './touch-feedback.js';

export class PixelWarNavigation {
    static init() {
        // Setup enhanced navigation for mobile
        this.setupMobileNavigation();
        
        // Setup boundary detection
        this.setupBoundaryDetection();
    }
    
    /**
     * Sets up mobile navigation controls
     */
    static setupMobileNavigation() {
        // Add return to center functionality
        const centerBtn = document.getElementById('centerViewBtn');
        if (centerBtn) {
            centerBtn.addEventListener('click', this.centerView.bind(this));
            PixelWarTouchFeedback.addTouchFeedback(centerBtn, { hapticType: 'medium' });
        }
        
        // Enhanced corner navigation with visual feedback
        document.querySelectorAll('.corner-nav-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const corner = btn.dataset.corner;
                if (corner) {
                    this.navigateToCorner(corner);
                    this.addNavigationFeedback(btn);
                }
            });
            
            // Add touch feedback
            PixelWarTouchFeedback.addTouchFeedback(btn);
        });
        
        // Setup directional navigation if present
        this.setupDirectionalNavigation();
        
        // Setup zoom controls
        this.setupZoomControls();
    }
    
    /**
     * Centers the view on the canvas
     */
    static centerView() {
        if (window.pixelWar) {
            // Use existing navigation method
            if (typeof window.pixelWar.navigateToCorner === 'function') {
                window.pixelWar.navigateToCorner('center');
            } else {
                // Fallback method
                window.pixelWar.offsetX = 0;
                window.pixelWar.offsetY = 0;
                window.pixelWar.render();
            }
            
            // Show notification
            showNotification('Centered view! 🎯', 'info');
            
            // Haptic feedback
            PixelWarTouchFeedback.triggerHapticFeedback('medium');
            
            // Track navigation
            console.log('📊 Navigation: centered_view');
        }
    }
    
    /**
     * Navigates to a specific corner of the canvas
     * @param {string} corner - Corner to navigate to
     */
    static navigateToCorner(corner) {
        if (!window.pixelWar) return;
        
        // Use existing navigation method if available
        if (typeof window.pixelWar.navigateToCorner === 'function') {
            window.pixelWar.navigateToCorner(corner);
        } else {
            // Fallback implementation
            this.fallbackNavigateToCorner(corner);
        }
        
        // Show notification
        const cornerName = corner.replace('-', ' ').replace(/\b\w/g, l => l.toUpperCase());
        showNotification(`Moved to ${cornerName}! 📍`, 'info');
        
        // Track navigation
        console.log('📊 Navigation: corner_navigation', { corner });
    }
    
    /**
     * Fallback navigation implementation
     * @param {string} corner - Corner identifier
     */
    static fallbackNavigateToCorner(corner) {
        const pixelWar = window.pixelWar;
        const rect = pixelWar.canvas.getBoundingClientRect();
        const pixelSize = 10; // PixelWarConfig.canvas.defaultPixelSize
        const viewportWidth = rect.width / (pixelSize * pixelWar.zoom);
        const viewportHeight = rect.height / (pixelSize * pixelWar.zoom);
        
        let targetX, targetY;
        
        switch (corner) {
            case 'top-left':
                targetX = 0;
                targetY = 0;
                break;
            case 'top-right':
                targetX = -(pixelWar.config.width - viewportWidth);
                targetY = 0;
                break;
            case 'bottom-left':
                targetX = 0;
                targetY = -(pixelWar.config.height - viewportHeight);
                break;
            case 'bottom-right':
                targetX = -(pixelWar.config.width - viewportWidth);
                targetY = -(pixelWar.config.height - viewportHeight);
                break;
            case 'center':
            default:
                targetX = -(pixelWar.config.width / 2 - viewportWidth / 2);
                targetY = -(pixelWar.config.height / 2 - viewportHeight / 2);
                break;
        }
        
        // Clamp to valid bounds
        const maxOffsetX = Math.max(0, pixelWar.config.width - viewportWidth);
        const maxOffsetY = Math.max(0, pixelWar.config.height - viewportHeight);
        
        pixelWar.offsetX = Math.max(-maxOffsetX, Math.min(0, targetX));
        pixelWar.offsetY = Math.max(-maxOffsetY, Math.min(0, targetY));
        
        pixelWar.render();
    }
    
    /**
     * Adds visual feedback to navigation button
     * @param {HTMLElement} btn - Button element
     */
    static addNavigationFeedback(btn) {
        // Visual feedback for navigation
        addPulseAnimation(btn, 300);
        
        // Haptic feedback
        PixelWarTouchFeedback.triggerHapticFeedback('light');
    }
    
    /**
     * Sets up directional navigation controls (arrow keys, swipe gestures)
     */
    static setupDirectionalNavigation() {
        // Keyboard arrow key support
        document.addEventListener('keydown', (e) => {
            if (!window.pixelWar) return;
            
            const moveDistance = 50; // pixels
            let moved = false;
            
            switch (e.key) {
                case 'ArrowUp':
                    window.pixelWar.offsetY += moveDistance;
                    moved = true;
                    break;
                case 'ArrowDown':
                    window.pixelWar.offsetY -= moveDistance;
                    moved = true;
                    break;
                case 'ArrowLeft':
                    window.pixelWar.offsetX += moveDistance;
                    moved = true;
                    break;
                case 'ArrowRight':
                    window.pixelWar.offsetX -= moveDistance;
                    moved = true;
                    break;
            }
            
            if (moved) {
                e.preventDefault();
                window.pixelWar.render();
                PixelWarTouchFeedback.triggerHapticFeedback('light');
            }
        });
        
        // Swipe gesture navigation
        document.addEventListener('swipe', (e) => {
            if (!window.pixelWar) return;
            
            const moveDistance = 100;
            const { direction } = e.detail;
            
            switch (direction) {
                case 'up':
                    window.pixelWar.offsetY += moveDistance;
                    break;
                case 'down':
                    window.pixelWar.offsetY -= moveDistance;
                    break;
                case 'left':
                    window.pixelWar.offsetX += moveDistance;
                    break;
                case 'right':
                    window.pixelWar.offsetX -= moveDistance;
                    break;
            }
            
            window.pixelWar.render();
            showNotification(`Swiped ${direction}! 👆`, 'info');
        });
    }
    
    /**
     * Sets up zoom controls with enhanced feedback
     */
    static setupZoomControls() {
        // Zoom in button
        const zoomInBtn = document.getElementById('zoomInBtn') || document.querySelector('.zoom-in-btn');
        if (zoomInBtn) {
            zoomInBtn.addEventListener('click', () => {
                this.zoomIn();
            });
            PixelWarTouchFeedback.addTouchFeedback(zoomInBtn);
        }
        
        // Zoom out button
        const zoomOutBtn = document.getElementById('zoomOutBtn') || document.querySelector('.zoom-out-btn');
        if (zoomOutBtn) {
            zoomOutBtn.addEventListener('click', () => {
                this.zoomOut();
            });
            PixelWarTouchFeedback.addTouchFeedback(zoomOutBtn);
        }
        
        // Zoom to fit button
        const zoomFitBtn = document.getElementById('zoomFitBtn') || document.querySelector('.zoom-fit-btn');
        if (zoomFitBtn) {
            zoomFitBtn.addEventListener('click', () => {
                this.zoomToFit();
            });
            PixelWarTouchFeedback.addTouchFeedback(zoomFitBtn);
        }
    }
    
    /**
     * Zoom in functionality
     */
    static zoomIn() {
        if (window.pixelWar && typeof window.pixelWar.zoomIn === 'function') {
            window.pixelWar.zoomIn();
            showNotification('Zoomed in! 🔍', 'info');
            PixelWarTouchFeedback.triggerHapticFeedback('light');
        }
    }
    
    /**
     * Zoom out functionality
     */
    static zoomOut() {
        if (window.pixelWar && typeof window.pixelWar.zoomOut === 'function') {
            window.pixelWar.zoomOut();
            showNotification('Zoomed out! 🔍', 'info');
            PixelWarTouchFeedback.triggerHapticFeedback('light');
        }
    }
    
    /**
     * Zoom to fit the entire canvas in view
     */
    static zoomToFit() {
        if (!window.pixelWar) return;
        
        if (typeof window.pixelWar.zoomToFit === 'function') {
            window.pixelWar.zoomToFit();
        } else {
            // Fallback implementation
            const minZoom = window.pixelWar.calculateMinZoom ? 
                window.pixelWar.calculateMinZoom() : 0.1;
            window.pixelWar.zoom = minZoom;
            this.centerView();
        }
        
        showNotification('Zoomed to fit! 📏', 'info');
        PixelWarTouchFeedback.triggerHapticFeedback('medium');
    }
    
    /**
     * Sets up boundary detection to prevent over-panning
     */
    static setupBoundaryDetection() {
        // This would typically be integrated with the main pan/zoom logic
        // For now, we'll set up event listeners for custom boundary events
        document.addEventListener('pixelwar:boundary', () => {
            this.addBoundaryFeedback();
        });
    }
    
    /**
     * Provides feedback when user hits canvas boundary
     */
    static addBoundaryFeedback() {
        // Show boundary feedback when user tries to pan beyond limits
        showNotification('You\'ve reached the edge! 🔄', 'warning');
        
        // Strong haptic feedback for boundary
        PixelWarTouchFeedback.triggerHapticFeedback('heavy');
        
        // Visual boundary indicator
        const canvas = document.querySelector('#pixelWarCanvas');
        if (canvas) {
            canvas.classList.add('boundary-hit');
            setTimeout(() => {
                canvas.classList.remove('boundary-hit');
            }, 200);
        }
    }
    
    /**
     * Gets current view position and zoom info
     * @returns {Object} Current view state
     */
    static getViewState() {
        if (!window.pixelWar) return null;
        
        return {
            offsetX: window.pixelWar.offsetX,
            offsetY: window.pixelWar.offsetY,
            zoom: window.pixelWar.zoom,
            canvasWidth: window.pixelWar.canvas.width,
            canvasHeight: window.pixelWar.canvas.height
        };
    }
    
    /**
     * Restores a saved view state
     * @param {Object} viewState - Saved view state
     */
    static restoreViewState(viewState) {
        if (!window.pixelWar || !viewState) return;
        
        window.pixelWar.offsetX = viewState.offsetX || 0;
        window.pixelWar.offsetY = viewState.offsetY || 0;
        window.pixelWar.zoom = viewState.zoom || 1;
        window.pixelWar.render();
        
        showNotification('View restored! 🔄', 'success');
    }
}

// Make available globally for HTML onclick handlers
window.PixelWarNavigation = PixelWarNavigation;