/**
 * Mobile Onboarding System for Pixel War
 * Handles first-time user experience and touch gesture tutorials
 */

import { triggerHapticFeedback, getDeviceType, trackEvent, isMobileDevice, showNotification } from './mobile-utils.js';

export class PixelWarOnboarding {
    static currentStep = 1;
    static totalSteps = 3;
    static isActive = false;
    static startTime = Date.now();
    
    static init() {
        // Check if user has already completed onboarding
        const hasCompletedOnboarding = localStorage.getItem('pixelWarOnboardingCompleted') === 'true';
        
        if (!hasCompletedOnboarding && isMobileDevice()) {
            this.show();
        }
        
        // Setup gesture demo animation
        this.setupGestureDemo();
    }
    
    static show() {
        const overlay = document.getElementById('mobileOnboarding');
        if (overlay) {
            overlay.style.display = 'flex';
            this.isActive = true;
            this.currentStep = 1;
            this.startTime = Date.now();
            this.updateProgress();
            
            // Track onboarding started
            trackEvent('onboarding_started', { device_type: getDeviceType() });
        }
    }
    
    static hide() {
        const overlay = document.getElementById('mobileOnboarding');
        if (overlay) {
            overlay.style.display = 'none';
            this.isActive = false;
        }
    }
    
    static nextStep() {
        if (this.currentStep < this.totalSteps) {
            // Hide current step
            const currentStepEl = document.querySelector(`.onboarding-step[data-step="${this.currentStep}"]`);
            if (currentStepEl) {
                currentStepEl.style.display = 'none';
            }
            
            this.currentStep++;
            
            // Show next step
            const nextStepEl = document.querySelector(`.onboarding-step[data-step="${this.currentStep}"]`);
            if (nextStepEl) {
                nextStepEl.style.display = 'block';
            }
            
            this.updateProgress();
            
            // Track step progression
            trackEvent('onboarding_step_completed', { 
                step: this.currentStep - 1,
                total_steps: this.totalSteps 
            });
            
            // Start gesture demo on step 2
            if (this.currentStep === 2) {
                this.startGestureDemo();
            }
        }
    }
    
    static previousStep() {
        if (this.currentStep > 1) {
            // Hide current step
            const currentStepEl = document.querySelector(`.onboarding-step[data-step="${this.currentStep}"]`);
            if (currentStepEl) {
                currentStepEl.style.display = 'none';
            }
            
            this.currentStep--;
            
            // Show previous step
            const prevStepEl = document.querySelector(`.onboarding-step[data-step="${this.currentStep}"]`);
            if (prevStepEl) {
                prevStepEl.style.display = 'block';
            }
            
            this.updateProgress();
        }
    }
    
    static updateProgress() {
        // Update progress dots
        document.querySelectorAll('.progress-dot').forEach((dot, index) => {
            if (index + 1 === this.currentStep) {
                dot.classList.add('active');
            } else {
                dot.classList.remove('active');
            }
        });
        
        // Update progress text
        const progressText = document.getElementById('progressText');
        if (progressText) {
            progressText.textContent = `Step ${this.currentStep} of ${this.totalSteps}`;
        }
    }
    
    static setupGestureDemo() {
        const gestureItems = document.querySelectorAll('.gesture-item');
        gestureItems.forEach(item => {
            item.addEventListener('click', () => {
                // Remove active from all items
                gestureItems.forEach(gi => gi.classList.remove('active'));
                // Add active to clicked item
                item.classList.add('active');
                
                // Update demo
                const gestureType = item.dataset.gesture;
                this.showGestureDemo(gestureType);
            });
        });
    }
    
    static showGestureDemo(gestureType) {
        const finger = document.getElementById('demoFinger');
        const actionText = document.getElementById('demoActionText');
        
        if (!finger || !actionText) return;
        
        // Reset animation
        finger.style.opacity = '0';
        finger.style.transform = 'translate(0, 0)';
        
        setTimeout(() => {
            finger.style.opacity = '1';
            
            switch(gestureType) {
                case 'tap':
                    finger.style.left = '50%';
                    finger.style.top = '50%';
                    finger.style.transform = 'translate(-50%, -50%) scale(1.2)';
                    actionText.textContent = 'Tap to place pixels';
                    setTimeout(() => {
                        finger.style.transform = 'translate(-50%, -50%) scale(1)';
                    }, 200);
                    break;
                case 'drag':
                    finger.style.left = '20%';
                    finger.style.top = '30%';
                    actionText.textContent = 'Drag to pan around';
                    setTimeout(() => {
                        finger.style.left = '70%';
                        finger.style.top = '60%';
                    }, 300);
                    break;
                case 'pinch':
                    actionText.textContent = 'Pinch to zoom in/out';
                    finger.style.left = '40%';
                    finger.style.top = '50%';
                    // Create second finger effect
                    setTimeout(() => {
                        finger.style.left = '60%';
                        finger.style.transform = 'translate(-50%, -50%) scale(0.8)';
                    }, 300);
                    break;
            }
        }, 100);
    }
    
    static startGestureDemo() {
        // Auto-cycle through gesture demos
        const gestures = ['tap', 'drag', 'pinch'];
        let currentIndex = 0;
        
        const cycleDemo = () => {
            const gestureItems = document.querySelectorAll('.gesture-item');
            gestureItems.forEach(item => item.classList.remove('active'));
            
            const currentGesture = gestures[currentIndex];
            const currentItem = document.querySelector(`[data-gesture="${currentGesture}"]`);
            if (currentItem) {
                currentItem.classList.add('active');
                this.showGestureDemo(currentGesture);
            }
            
            currentIndex = (currentIndex + 1) % gestures.length;
        };
        
        // Start immediately
        cycleDemo();
        
        // Continue cycling every 2 seconds
        const interval = setInterval(cycleDemo, 2000);
        
        // Stop when moving to next step
        setTimeout(() => {
            clearInterval(interval);
        }, 8000);
    }
    
    static selectMode(mode) {
        // Visual feedback for mode selection
        document.querySelectorAll('.mode-card').forEach(card => {
            card.classList.remove('selected');
        });
        const modeCard = document.querySelector(`.mode-card.${mode}-mode`);
        if (modeCard) {
            modeCard.classList.add('selected');
        }
        
        // Store mode preference
        localStorage.setItem('pixelWarTouchMode', mode);
        
        // Track mode selection
        trackEvent('onboarding_mode_selected', { mode: mode });
        
        // Provide haptic feedback if available
        triggerHapticFeedback('light');
    }
    
    static finish() {
        // Mark onboarding as completed
        localStorage.setItem('pixelWarOnboardingCompleted', 'true');
        
        // Track completion
        trackEvent('onboarding_completed', { 
            total_time: Date.now() - this.startTime,
            final_mode: localStorage.getItem('pixelWarTouchMode') || 'tap'
        });
        
        // Hide onboarding
        this.hide();
        
        // Show touch mode indicator (we'll import ModeManager dynamically to avoid circular imports)
        setTimeout(() => {
            if (window.PixelWarModeManager) {
                window.PixelWarModeManager.show();
            }
        }, 500);
        
        // Show welcome notification
        showNotification('Welcome! Start painting pixels! 🎨', 'success');
    }
    
    static skip() {
        // Track skip event
        trackEvent('onboarding_skipped', { 
            step: this.currentStep,
            total_steps: this.totalSteps 
        });
        
        // Set default mode
        if (!localStorage.getItem('pixelWarTouchMode')) {
            localStorage.setItem('pixelWarTouchMode', 'tap');
        }
        
        // Complete onboarding
        this.finish();
    }
}

// Make available globally for HTML onclick handlers
window.PixelWarOnboarding = PixelWarOnboarding;