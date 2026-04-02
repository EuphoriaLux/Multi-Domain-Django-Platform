/**
 * Enhanced Color Manager for Pixel War
 * Manages color selection, recent colors, and enhanced color palette
 */

import { triggerHapticFeedback, trackEvent } from './mobile-utils.js';

export class PixelWarColorManager {
    static recentColors = [];
    static maxRecentColors = 6;
    static isVisible = false;
    
    static init() {
        // Load recent colors
        const saved = localStorage.getItem('pixelWarRecentColors');
        if (saved) {
            try {
                this.recentColors = JSON.parse(saved);
            } catch (error) {
                console.warn('Failed to load recent colors:', error);
                this.recentColors = [];
            }
        }
        
        // Setup enhanced color palette events
        this.setupEvents();
        
        // Update recent colors display
        this.updateRecentColorsDisplay();
    }
    
    static setupEvents() {
        // Enhanced color buttons
        document.querySelectorAll('.color-btn-enhanced').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const color = e.target.dataset.color;
                if (color) {
                    this.selectColor(color);
                }
            });
            
            // Add touch feedback class
            btn.classList.add('touch-feedback');
        });
        
        // Recent color buttons (will be added dynamically)
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('recent-color-btn')) {
                const color = e.target.dataset.color;
                if (color) {
                    this.selectColor(color);
                }
            }
        });
    }
    
    static selectColor(color) {
        if (!color) {
            console.warn('No color provided to selectColor');
            return;
        }
        
        // Add to recent colors
        this.addToRecentColors(color);
        
        // Update PixelWar selected color
        if (window.pixelWar) {
            window.pixelWar.selectedColor = color;
            if (typeof window.pixelWar.updateUI === 'function') {
                window.pixelWar.updateUI();
            }
        }
        
        // Visual feedback - update all color buttons
        this.updateColorButtonSelection(color);
        
        // Haptic feedback
        triggerHapticFeedback('light');
        
        // Track color selection
        trackEvent('color_selected', { 
            color: color,
            source: 'enhanced_palette'
        });
        
        // Auto-hide palette after selection
        setTimeout(() => {
            this.hideEnhancedPalette();
        }, 500);
    }
    
    static updateColorButtonSelection(selectedColor) {
        // Remove selected class from all color buttons
        document.querySelectorAll('.color-btn-enhanced, .recent-color-btn').forEach(btn => {
            btn.classList.remove('selected');
            if (btn.dataset.color === selectedColor) {
                btn.classList.add('selected');
            }
        });
    }
    
    static addToRecentColors(color) {
        // Remove if already exists
        this.recentColors = this.recentColors.filter(c => c !== color);
        
        // Add to beginning
        this.recentColors.unshift(color);
        
        // Limit array size
        if (this.recentColors.length > this.maxRecentColors) {
            this.recentColors = this.recentColors.slice(0, this.maxRecentColors);
        }
        
        // Save to localStorage
        try {
            localStorage.setItem('pixelWarRecentColors', JSON.stringify(this.recentColors));
        } catch (error) {
            console.warn('Failed to save recent colors:', error);
        }
        
        // Update display
        this.updateRecentColorsDisplay();
    }
    
    static updateRecentColorsDisplay() {
        const recentColorsList = document.getElementById('recentColorsList');
        if (!recentColorsList) return;
        
        if (this.recentColors.length === 0) {
            recentColorsList.innerHTML = '<p class="no-recent-colors">No recent colors</p>';
            return;
        }
        
        recentColorsList.innerHTML = this.recentColors
            .map(color => `
                <button class="recent-color-btn" 
                        data-color="${color}" 
                        style="background: ${color}${color === '#FFFFFF' ? '; border: 1px solid #ccc' : ''}" 
                        aria-label="Recent color ${color}">
                </button>
            `).join('');
    }
    
    static showEnhancedPalette() {
        const palette = document.getElementById('enhancedColorPalette');
        if (palette) {
            palette.style.display = 'block';
            this.isVisible = true;
            
            // Update recent colors
            this.updateRecentColorsDisplay();
            
            // Track palette open
            trackEvent('enhanced_palette_opened');
        }
    }
    
    static hideEnhancedPalette() {
        const palette = document.getElementById('enhancedColorPalette');
        if (palette) {
            palette.style.display = 'none';
            this.isVisible = false;
        }
    }
    
    static toggleEnhancedPalette() {
        if (this.isVisible) {
            this.hideEnhancedPalette();
        } else {
            this.showEnhancedPalette();
        }
    }
    
    /**
     * Gets the recent colors array
     * @returns {Array<string>} Array of recent color hex codes
     */
    static getRecentColors() {
        return [...this.recentColors];
    }
    
    /**
     * Clears all recent colors
     */
    static clearRecentColors() {
        this.recentColors = [];
        localStorage.removeItem('pixelWarRecentColors');
        this.updateRecentColorsDisplay();
        
        trackEvent('recent_colors_cleared');
    }
    
    /**
     * Gets the currently selected color from PixelWar instance
     * @returns {string|null} Current color or null
     */
    static getCurrentColor() {
        if (window.pixelWar && window.pixelWar.selectedColor) {
            return window.pixelWar.selectedColor;
        }
        return null;
    }
    
    /**
     * Preselects a color in the UI (useful for loading saved colors)
     * @param {string} color - Color hex code to preselect
     */
    static preselectColor(color) {
        if (!color) return;
        
        this.updateColorButtonSelection(color);
        
        // Don't add to recent colors for preselection
        if (window.pixelWar) {
            window.pixelWar.selectedColor = color;
            if (typeof window.pixelWar.updateUI === 'function') {
                window.pixelWar.updateUI();
            }
        }
    }
    
    /**
     * Sets up swipe gesture to show color palette from bottom of screen
     */
    static setupSwipeGesture() {
        let touchStartY = 0;
        let touchStartTime = 0;
        
        document.addEventListener('touchstart', (e) => {
            touchStartY = e.touches[0].clientY;
            touchStartTime = Date.now();
        }, { passive: true });
        
        document.addEventListener('touchmove', (e) => {
            const touchY = e.touches[0].clientY;
            const deltaY = touchY - touchStartY;
            const deltaTime = Date.now() - touchStartTime;
            
            // Swipe up from bottom third of screen to show color palette
            // Must be fast swipe (under 300ms) and significant distance (over 50px)
            if (touchStartY > window.innerHeight * 0.66 && 
                deltaY < -50 && 
                deltaTime < 300) {
                this.showEnhancedPalette();
            }
        }, { passive: true });
    }
}

// Make available globally for HTML onclick handlers
window.PixelWarColorManager = PixelWarColorManager;