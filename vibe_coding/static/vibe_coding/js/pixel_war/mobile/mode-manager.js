/**
 * Touch Mode Manager for Pixel War
 * Manages switching between tap and precision modes
 */

import { triggerHapticFeedback, trackEvent, isMobileDevice, showNotification, addPulseAnimation } from './mobile-utils.js';

export class PixelWarModeManager {
    static currentMode = 'tap';
    static isVisible = false;
    static helpTimeout = null;
    
    static init() {
        // Load saved mode
        this.currentMode = localStorage.getItem('pixelWarTouchMode') || 'tap';
        
        // Show on mobile devices after onboarding
        const hasCompletedOnboarding = localStorage.getItem('pixelWarOnboardingCompleted') === 'true';
        
        if (isMobileDevice() && hasCompletedOnboarding) {
            setTimeout(() => {
                this.show();
            }, 1000);
        }
        
        // Update mode in PixelWar instance
        if (window.pixelWar) {
            window.pixelWar.touchMode = this.currentMode;
        }
    }
    
    static show() {
        const indicator = document.getElementById('touchModeIndicator');
        if (indicator) {
            indicator.style.display = 'block';
            this.isVisible = true;
            this.updateDisplay();
            
            // Show help tip initially
            setTimeout(() => {
                this.showHelpTip();
            }, 2000);
        }
    }
    
    static hide() {
        const indicator = document.getElementById('touchModeIndicator');
        if (indicator) {
            indicator.style.display = 'none';
            this.isVisible = false;
        }
    }
    
    static toggleMode() {
        this.currentMode = this.currentMode === 'tap' ? 'precision' : 'tap';
        
        // Save mode
        localStorage.setItem('pixelWarTouchMode', this.currentMode);
        
        // Update PixelWar instance
        if (window.pixelWar) {
            window.pixelWar.touchMode = this.currentMode;
        }
        
        // Update display
        this.updateDisplay();
        
        // Show help tip
        this.showHelpTip();
        
        // Haptic feedback
        triggerHapticFeedback('medium');
        
        // Track mode change
        trackEvent('touch_mode_changed', { 
            new_mode: this.currentMode,
            source: 'manual_toggle'
        });
        
        // Show notification
        const message = this.currentMode === 'tap' 
            ? 'Tap Mode: Quick pixel placement ⚡' 
            : 'Precision Mode: Preview before placing 🎯';
        showNotification(message, 'info');
    }
    
    static updateDisplay() {
        const modeIcon = document.getElementById('modeIcon');
        const modeLabel = document.getElementById('modeLabel');
        const modeDescription = document.getElementById('modeDescription');
        const modeHelpText = document.getElementById('modeHelpText');
        
        if (this.currentMode === 'tap') {
            if (modeIcon) modeIcon.textContent = '⚡';
            if (modeLabel) modeLabel.textContent = 'Tap Mode';
            if (modeDescription) modeDescription.textContent = 'Instant placement';
            if (modeHelpText) modeHelpText.textContent = 'Tap anywhere to place pixels instantly!';
        } else {
            if (modeIcon) modeIcon.textContent = '🎯';
            if (modeLabel) modeLabel.textContent = 'Precision Mode';
            if (modeDescription) modeDescription.textContent = 'Preview first';
            if (modeHelpText) modeHelpText.textContent = 'Tap to preview, confirm to place pixels!';
        }
    }
    
    static showHelpTip() {
        const helpTip = document.getElementById('modeHelpTip');
        if (helpTip) {
            helpTip.style.display = 'block';
            
            // Clear existing timeout
            if (this.helpTimeout) {
                clearTimeout(this.helpTimeout);
            }
            
            // Hide after 3 seconds
            this.helpTimeout = setTimeout(() => {
                helpTip.style.display = 'none';
            }, 3000);
        }
    }
    
    static suggestPrecisionMode() {
        // Auto-suggest precision mode after multiple accidental placements
        if (this.currentMode === 'tap') {
            const accidentalPlacements = parseInt(localStorage.getItem('pixelWarAccidentalPlacements') || '0');
            if (accidentalPlacements >= 3) {
                // Show suggestion
                showNotification('Too many accidents? Try Precision Mode! 🎯', 'info');
                
                // Highlight mode toggle
                const switchBtn = document.getElementById('switchModeBtn');
                if (switchBtn) {
                    addPulseAnimation(switchBtn, 1000);
                }
                
                // Track suggestion
                trackEvent('precision_mode_suggested', { 
                    accidental_placements: accidentalPlacements 
                });
                
                // Reset counter
                localStorage.removeItem('pixelWarAccidentalPlacements');
            }
        }
    }
    
    static recordAccidentalPlacement() {
        const current = parseInt(localStorage.getItem('pixelWarAccidentalPlacements') || '0');
        localStorage.setItem('pixelWarAccidentalPlacements', (current + 1).toString());
    }
    
    /**
     * Gets the current touch mode
     * @returns {string} Current mode ('tap' or 'precision')
     */
    static getCurrentMode() {
        return this.currentMode;
    }
    
    /**
     * Sets the touch mode programmatically
     * @param {string} mode - Mode to set ('tap' or 'precision')
     */
    static setMode(mode) {
        if (mode !== 'tap' && mode !== 'precision') {
            console.warn('Invalid mode:', mode);
            return;
        }
        
        if (this.currentMode !== mode) {
            this.currentMode = mode;
            localStorage.setItem('pixelWarTouchMode', this.currentMode);
            
            // Update PixelWar instance
            if (window.pixelWar) {
                window.pixelWar.touchMode = this.currentMode;
            }
            
            this.updateDisplay();
            
            // Track programmatic mode change
            trackEvent('touch_mode_changed', { 
                new_mode: this.currentMode,
                source: 'programmatic'
            });
        }
    }
}

// Make available globally for HTML onclick handlers
window.PixelWarModeManager = PixelWarModeManager;