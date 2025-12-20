// Import the PixelWarConfig from the config module
import { PixelWarConfig } from '../config/pixel-war-config.js';

/**
 * InputHandler class - Handles all user input events for the Pixel War application
 * Supports mouse, keyboard, and touch interactions with proper mobile optimization
 */
class InputHandler extends EventTarget {
    constructor(canvas, config) {
        super();
        this.canvas = canvas;
        this.config = config;
        
        // These will be set by the main PixelWar instance
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        
        this.setupEventListeners();
        
        // Unified input state
        this.inputState = {
            isDragging: false,
            dragStartX: 0,
            dragStartY: 0,
            lastMouseX: 0,
            lastMouseY: 0,
            spacePressed: false,
            shiftPressed: false,
            ctrlPressed: false
        };
        
        // Touch state
        this.touchState = {
            touches: new Map(),
            lastTouchDistance: 0,
            touchMode: localStorage.getItem('pixelWarTouchMode') || 'tap',
            longPressTimer: null,
            touchStartTime: 0,
            selectedPixelX: null,
            selectedPixelY: null,
            tapDebounceTimer: null,
            lastTapTime: 0
        };

        // Performance throttling (minimal for mobile responsiveness)
        this.lastEventTime = 0;
        this.throttleDelay = 1; // Minimal throttling for immediate response
    }

    setupEventListeners() {
        // Mouse events
        this.canvas.addEventListener('mousedown', this.handleMouseDown.bind(this));
        this.canvas.addEventListener('mousemove', this.handleMouseMove.bind(this));
        this.canvas.addEventListener('mouseup', this.handleMouseUp.bind(this));
        this.canvas.addEventListener('mouseleave', this.handleMouseLeave.bind(this));
        this.canvas.addEventListener('wheel', this.handleWheel.bind(this), { passive: false });
        this.canvas.addEventListener('contextmenu', e => e.preventDefault());

        // Touch events
        this.canvas.addEventListener('touchstart', this.handleTouchStart.bind(this), { passive: false });
        this.canvas.addEventListener('touchmove', this.handleTouchMove.bind(this), { passive: false });
        this.canvas.addEventListener('touchend', this.handleTouchEnd.bind(this));

        // Keyboard events
        document.addEventListener('keydown', this.handleKeyDown.bind(this));
        document.addEventListener('keyup', this.handleKeyUp.bind(this));
    }

    handleMouseDown(e) {
        const isPanMode = this.inputState.spacePressed || e.button === 2 || e.button === 1;
        
        if (isPanMode) {
            e.preventDefault();
            this.startDrag(e.clientX, e.clientY);
        }
    }

    handleMouseMove(e) {
        // Throttle mouse move events for better performance
        const now = performance.now();
        if (now - this.lastEventTime < this.throttleDelay) {
            return;
        }
        this.lastEventTime = now;
        
        if (this.inputState.isDragging) {
            this.updateDrag(e.clientX, e.clientY);
        } else {
            this.dispatchEvent(new CustomEvent('hover', {
                detail: { x: e.clientX, y: e.clientY }
            }));
        }
    }

    handleMouseUp(e) {
        if (this.inputState.isDragging) {
            this.endDrag();
        } else if (e.button === 0 && !this.inputState.spacePressed) {
            this.dispatchEvent(new CustomEvent('click', {
                detail: { x: e.clientX, y: e.clientY }
            }));
        }
    }

    handleMouseLeave(e) {
        if (this.inputState.isDragging) {
            this.endDrag();
        }
        this.dispatchEvent(new CustomEvent('leave'));
    }

    handleWheel(e) {
        e.preventDefault();
        
        if (e.ctrlKey || e.metaKey) {
            // Zoom with proportional sensitivity based on deltaY magnitude
            // Normalize deltaY to reasonable range (-100 to 100) and scale down
            const normalizedDelta = Math.max(-100, Math.min(100, e.deltaY));
            // Scale to much smaller increments: 1% to 3% max per wheel tick
            const delta = -(normalizedDelta / 100) * 0.02; // Max 2% zoom per wheel tick
            
            // Throttle wheel events to prevent rapid-fire zoom changes
            const now = performance.now();
            if (!this.lastWheelTime || (now - this.lastWheelTime) > 16) { // ~60fps throttling
                this.lastWheelTime = now;
                this.dispatchEvent(new CustomEvent('zoom', {
                    detail: { delta, x: e.clientX, y: e.clientY }
                }));
            }
        } else {
            // Pan
            this.dispatchEvent(new CustomEvent('pan', {
                detail: { deltaX: e.deltaX, deltaY: e.deltaY, shiftKey: e.shiftKey }
            }));
        }
    }

    handleTouchStart(e) {
        e.preventDefault();
        
        // Stop any ongoing momentum
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
        }
        
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            this.inputState.isDragging = false;
            this.touchState.touchStartTime = Date.now();
            
            // Clear any existing touch data
            this.touchState.touches.clear();
            
            this.touchState.touches.set(touch.identifier, {
                startX: touch.clientX,
                startY: touch.clientY,
                currentX: touch.clientX,
                currentY: touch.clientY,
                startTime: Date.now(),
                lastMoveTime: Date.now()
            });
            
            // Start long press timer for precision mode
            if (this.touchState.touchMode === 'precision') {
                this.clearLongPressTimer();
                this.touchState.longPressTimer = setTimeout(() => {
                    if (!this.inputState.isDragging) {
                        this.handleLongPress(touch.clientX, touch.clientY);
                    }
                }, 400); // 400ms for long press
            }
        } else if (e.touches.length === 2) {
            // Multi-touch: clear single touch and start pinch
            this.clearLongPressTimer();
            this.inputState.isDragging = false;
            this.touchState.touches.clear();
            this.touchState.lastTouchDistance = this.getTouchDistance(e.touches[0], e.touches[1]);
        }
    }

    handleTouchMove(e) {
        e.preventDefault();
        
        // Improved throttling for touch responsiveness without drift
        const now = performance.now();
        if (now - this.lastEventTime < 5) { // Balanced 5ms throttling
            return;
        }
        this.lastEventTime = now;
        
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            const touchData = this.touches.get(touch.identifier);
            
            if (touchData) {
                const deltaX = touch.clientX - touchData.currentX;
                const deltaY = touch.clientY - touchData.currentY;
                
                // Update current position
                touchData.currentX = touch.clientX;
                touchData.currentY = touch.clientY;
                
                // Calculate total movement from start
                const totalMoveDistance = Math.sqrt(
                    Math.pow(touch.clientX - touchData.startX, 2) +
                    Math.pow(touch.clientY - touchData.startY, 2)
                );

                // IMPROVED: Increase threshold to 8px to prevent accidental pixel placement
                // This gives users more room for slight finger movement before triggering pan
                if (totalMoveDistance > 8 && !this.inputState.isDragging) {
                    this.inputState.isDragging = true;
                    this.clearLongPressTimer();
                }
                
                // Send drag events immediately once dragging started (no minimum movement)
                if (this.inputState.isDragging && (Math.abs(deltaX) > 0.1 || Math.abs(deltaY) > 0.1)) {
                    // IMPROVED: Reduced sensitivity from 1.5x to 1.2x for better control
                    this.dispatchEvent(new CustomEvent('touchdrag', {
                        detail: {
                            deltaX: deltaX * 1.2, // Better control with reduced sensitivity
                            deltaY: deltaY * 1.2, // Better control with reduced sensitivity
                            isMobile: true,
                            shiftKey: this.inputState.shiftPressed,
                            ctrlKey: this.inputState.ctrlPressed,
                            totalDistance: totalMoveDistance
                        }
                    }));

                    // Debug logging for touch issues
                    if (Math.random() < 0.01) { // Only log 1% of events to avoid spam
                        console.log('📱 Touch Drag:', {
                            deltaX: deltaX.toFixed(2),
                            deltaY: deltaY.toFixed(2),
                            enhanced: (deltaX * 1.2).toFixed(2) + ',' + (deltaY * 1.2).toFixed(2),
                            totalDistance: totalMoveDistance.toFixed(2)
                        });
                    }
                }
            }
        } else if (e.touches.length === 2) {
            this.clearLongPressTimer();
            this.isDragging = false; // Stop single-finger dragging
            
            const distance = this.getTouchDistance(e.touches[0], e.touches[1]);
            
            // Calculate the center point between the two touches
            const centerX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
            const centerY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
            
            if (this.lastTouchDistance) {
                const scale = distance / this.lastTouchDistance;
                this.dispatchEvent(new CustomEvent('pinchzoom', {
                    detail: { 
                        scale,
                        centerX,
                        centerY
                    }
                }));
            }
            this.lastTouchDistance = distance;
        }
    }

    handleTouchEnd(e) {
        this.clearLongPressTimer();
        
        if (e.changedTouches.length === 1) {
            const touch = e.changedTouches[0];
            const touchData = this.touches.get(touch.identifier);
            
            if (touchData) {
                const duration = Date.now() - touchData.startTime;
                const distance = Math.sqrt(
                    Math.pow(touchData.currentX - touchData.startX, 2) +
                    Math.pow(touchData.currentY - touchData.startY, 2)
                );
                
                console.log('📱 Touch End:', {
                    duration: duration + 'ms',
                    distance: distance.toFixed(2) + 'px',
                    isDragging: this.inputState.isDragging,
                    touchMode: this.touchState.touchMode
                });

                // IMPROVED: Better tap detection with stricter thresholds
                // Increased distance threshold from 15px to 20px to be more forgiving
                // Added debounce to prevent accidental double-taps
                const now = Date.now();
                const timeSinceLastTap = now - this.touchState.lastTapTime;

                if (duration < 500 && distance < 20 && !this.inputState.isDragging) {
                    // Add 100ms debounce to prevent accidental rapid taps
                    if (timeSinceLastTap > 100) {
                        this.touchState.lastTapTime = now;

                        if (this.touchState.touchMode === 'tap') {
                            // Direct tap mode - place pixel immediately
                            console.log('🎯 Tap mode - placing pixel directly');
                            this.dispatchEvent(new CustomEvent('tap', {
                                detail: { x: touchData.startX, y: touchData.startY }
                            }));
                        } else {
                            // Precision mode - show preview
                            console.log('🎯 Precision mode - showing preview');
                            this.handlePixelPreview(touchData.startX, touchData.startY);
                        }
                    } else {
                        console.log('⏱️ Tap debounced - too soon after last tap');
                    }
                } else if (this.inputState.isDragging) {
                    console.log('🖐️ Drag gesture completed');
                } else {
                    console.log('❓ Touch gesture not recognized:', { duration, distance, isDragging: this.inputState.isDragging });
                }

                this.touchState.touches.delete(touch.identifier);
            }
        }

        // Reset dragging state
        this.inputState.isDragging = false;
        
        // Reset pinch zoom state
        if (e.touches.length === 0) {
            this.lastTouchDistance = 0;
            this.touches.clear(); // Clear all remaining touch data
        }
    }

    getTouchDistance(touch1, touch2) {
        const dx = touch1.clientX - touch2.clientX;
        const dy = touch1.clientY - touch2.clientY;
        return Math.sqrt(dx * dx + dy * dy);
    }
    
    screenToCanvas(screenX, screenY) {
        const rect = this.canvas.getBoundingClientRect();
        
        // Validate inputs and rect
        if (!rect.width || !rect.height || isNaN(screenX) || isNaN(screenY)) {
            console.warn('❌ Invalid screenToCanvas inputs:', { screenX, screenY, rect });
            return null;
        }
        
        // Account for CSS borders and padding
        const computedStyle = getComputedStyle(this.canvas);
        const borderLeft = parseFloat(computedStyle.borderLeftWidth) || 0;
        const borderTop = parseFloat(computedStyle.borderTopWidth) || 0;
        const paddingLeft = parseFloat(computedStyle.paddingLeft) || 0;
        const paddingTop = parseFloat(computedStyle.paddingTop) || 0;
        
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        // More precise calculation with proper offset handling
        const adjustedX = screenX - rect.left - borderLeft - paddingLeft;
        const adjustedY = screenY - rect.top - borderTop - paddingTop;
        
        // Use Math.round instead of Math.floor for better precision
        const canvasX = Math.round(adjustedX / (pixelSize * this.zoom));
        const canvasY = Math.round(adjustedY / (pixelSize * this.zoom));
        
        // Apply offset and clamp to bounds
        const finalX = Math.max(0, Math.min(this.config.width - 1, canvasX - Math.round(this.offsetX)));
        const finalY = Math.max(0, Math.min(this.config.height - 1, canvasY - Math.round(this.offsetY)));
        
        return {
            x: finalX,
            y: finalY
        };
    }
    
    clearLongPressTimer() {
        if (this.longPressTimer) {
            clearTimeout(this.longPressTimer);
            this.longPressTimer = null;
        }
    }
    
    handleLongPress(clientX, clientY) {
        // Enhanced long press for precision mode - use improved coordinate conversion
        const coords = this.screenToCanvas(clientX, clientY);
        
        if (!coords) {
            console.log('❌ Failed to convert long press coordinates:', {clientX, clientY});
            return;
        }
        
        console.log('🔥 Long press coordinates:', coords);
        
        if (coords.x >= 0 && coords.x < this.config.width && coords.y >= 0 && coords.y < this.config.height) {
            this.showPixelPreview(coords.x, coords.y, true);
            
            // Haptic feedback for long press
            if (navigator.vibrate) {
                navigator.vibrate([50, 30, 50]); // Double buzz pattern
            }
        }
    }
    
    handlePixelPreview(clientX, clientY) {
        // Use improved coordinate conversion
        const coords = this.screenToCanvas(clientX, clientY);
        
        if (!coords) {
            console.log('❌ Failed to convert pixel preview coordinates:', {clientX, clientY});
            return;
        }
        
        console.log('🎯 Pixel preview coordinates:', {
            clientX, clientY, 
            convertedX: coords.x, convertedY: coords.y,
            zoom: this.zoom,
            offsetX: this.offsetX, offsetY: this.offsetY,
            canvasSize: `${this.config.width}x${this.config.height}`
        });
        
        if (coords.x >= 0 && coords.x < this.config.width && coords.y >= 0 && coords.y < this.config.height) {
            this.showPixelPreview(coords.x, coords.y, false);
        } else {
            console.log('❌ Coordinates out of bounds:', {x: coords.x, y: coords.y});
        }
    }
    
    showPixelPreview(x, y, isLongPress = false) {
        // Store selected coordinates
        this.selectedPixelX = x;
        this.selectedPixelY = y;
        
        // Visual feedback - highlight the pixel
        this.highlightPixel(x, y);
        
        // Show confirmation UI
        this.showPixelConfirmation(x, y, isLongPress);
        
        // Light haptic feedback
        if (navigator.vibrate && !isLongPress) {
            navigator.vibrate(25);
        }
    }
    
    highlightPixel(x, y) {
        // Draw highlight overlay on the selected pixel
        const ctx = this.canvas.getContext('2d');
        ctx.save();
        
        const pixelX = (x + this.offsetX) * this.pixelSize * this.zoom;
        const pixelY = (y + this.offsetY) * this.pixelSize * this.zoom;
        const size = this.pixelSize * this.zoom;
        
        // Draw animated selection border
        ctx.strokeStyle = '#FFD700'; // Gold color
        ctx.lineWidth = Math.max(2, this.zoom);
        ctx.setLineDash([5, 5]);
        ctx.strokeRect(pixelX, pixelY, size, size);
        
        // Add semi-transparent overlay
        ctx.fillStyle = 'rgba(255, 215, 0, 0.3)';
        ctx.fillRect(pixelX, pixelY, size, size);
        
        ctx.restore();
        
        // Auto-clear highlight after 3 seconds
        setTimeout(() => {
            this.render();
        }, 3000);
    }
    
    showPixelConfirmation(x, y, isLongPress) {
        console.log('📱 Creating pixel confirmation modal for:', {x, y, selectedColor: this.selectedColor});
        
        // Remove existing confirmation
        const existing = document.getElementById('pixelConfirmation');
        if (existing) {
            existing.remove();
        }
        
        // Create modal element
        const modal = document.createElement('div');
        modal.className = 'pixel-confirmation-mobile';
        modal.id = 'pixelConfirmation';
        modal.style.cssText = `
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            right: 0 !important;
            bottom: 0 !important;
            background: rgba(0, 0, 0, 0.8) !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            z-index: 99999 !important;
            animation: fadeIn 0.2s ease !important;
        `;
        
        modal.innerHTML = `
            <div class="confirmation-content" style="
                background: white !important;
                border-radius: 16px !important;
                padding: 24px !important;
                margin: 20px !important;
                max-width: 300px !important;
                text-align: center !important;
                box-shadow: 0 8px 32px rgba(0,0,0,0.3) !important;
            ">
                <h4 style="margin: 0 0 16px 0; color: #333; font-size: 18px;">🎯 Pixel Selection</h4>
                <p style="margin: 8px 0; color: #666; font-size: 14px;">Position: (${x}, ${y})</p>
                <p style="margin: 8px 0; color: #666; font-size: 14px;">Color: <span style="color: ${this.selectedColor}; font-weight: bold;">${this.selectedColor}</span></p>
                <div class="confirmation-buttons" style="display: flex; gap: 12px; margin-top: 20px;">
                    <button class="btn-confirm" style="
                        flex: 1; padding: 12px 16px; border: none; border-radius: 8px;
                        font-weight: 600; font-size: 14px; cursor: pointer;
                        background: #4caf50; color: white;
                        transition: all 0.2s ease;
                    ">✓ Place Pixel</button>
                    <button class="btn-cancel" style="
                        flex: 1; padding: 12px 16px; border: none; border-radius: 8px;
                        font-weight: 600; font-size: 14px; cursor: pointer;
                        background: #f44336; color: white;
                        transition: all 0.2s ease;
                    ">✕ Cancel</button>
                </div>
            </div>
        `;
        
        // Add event listeners directly to avoid window.pixelWar issues
        const confirmBtn = modal.querySelector('.btn-confirm');
        const cancelBtn = modal.querySelector('.btn-cancel');
        
        confirmBtn.addEventListener('click', () => {
            console.log('✅ Confirm button clicked');
            this.confirmPixelPlacement();
        });
        
        cancelBtn.addEventListener('click', () => {
            console.log('❌ Cancel button clicked');
            this.cancelPixelPreview();
        });
        
        // Add to body
        document.body.appendChild(modal);
        
        // Add touch/click outside to close
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                this.cancelPixelPreview();
            }
        });
        
        console.log('📱 Modal created and added to DOM');
    }
    
    confirmPixelPlacement() {
        console.log('🎯 Confirming pixel placement:', {x: this.selectedPixelX, y: this.selectedPixelY});
        if (this.selectedPixelX !== null && this.selectedPixelY !== null) {
            this.dispatchEvent(new CustomEvent('pixelplace', {
                detail: { 
                    x: this.selectedPixelX, 
                    y: this.selectedPixelY 
                }
            }));
        }
        this.cancelPixelPreview();
    }
    
    cancelPixelPreview() {
        console.log('❌ Canceling pixel preview');
        this.selectedPixelX = null;
        this.selectedPixelY = null;
        
        // Remove confirmation UI
        const confirmation = document.getElementById('pixelConfirmation');
        if (confirmation) {
            confirmation.remove();
            console.log('📱 Modal removed from DOM');
        }
        
        // Re-render to remove highlight
        this.render();
    }

    handleKeyDown(e) {
        if (e.code === 'Space' && !e.target.matches('input, textarea')) {
            e.preventDefault();
            this.spacePressed = true;
            this.canvas.style.cursor = 'grab';
        }
        
        if (e.key === 'Shift') {
            this.shiftPressed = true;
        }
        
        if (e.key === 'Control') {
            e.preventDefault(); // Prevent browser default CTRL behavior
            this.ctrlPressed = true;
        }
        
        this.dispatchEvent(new CustomEvent('keydown', { detail: { key: e.key } }));
    }

    handleKeyUp(e) {
        if (e.code === 'Space') {
            this.spacePressed = false;
            this.canvas.style.cursor = 'crosshair';
        }
        
        if (e.key === 'Shift') {
            this.shiftPressed = false;
        }
        
        if (e.key === 'Control') {
            e.preventDefault(); // Prevent browser default CTRL behavior
            this.ctrlPressed = false;
        }
    }

    startDrag(x, y) {
        this.isDragging = true;
        this.dragStartX = x;
        this.dragStartY = y;
        this.lastMouseX = x;
        this.lastMouseY = y;
        this.canvas.style.cursor = 'grabbing';
        
        this.dispatchEvent(new CustomEvent('dragstart', {
            detail: { x, y }
        }));
    }

    updateDrag(x, y) {
        const deltaX = x - this.lastMouseX;
        const deltaY = y - this.lastMouseY;
        
        this.lastMouseX = x;
        this.lastMouseY = y;
        
        this.dispatchEvent(new CustomEvent('drag', {
            detail: { deltaX, deltaY, velocityX: deltaX, velocityY: deltaY }
        }));
    }

    endDrag() {
        this.isDragging = false;
        this.canvas.style.cursor = 'crosshair';
        
        this.dispatchEvent(new CustomEvent('dragend'));
    }

    destroy() {
        // Clean up event listeners
        this.canvas.removeEventListener('mousedown', this.handleMouseDown);
        this.canvas.removeEventListener('mousemove', this.handleMouseMove);
        this.canvas.removeEventListener('mouseup', this.handleMouseUp);
        this.canvas.removeEventListener('mouseleave', this.handleMouseLeave);
        this.canvas.removeEventListener('wheel', this.handleWheel);
        this.canvas.removeEventListener('touchstart', this.handleTouchStart);
        this.canvas.removeEventListener('touchmove', this.handleTouchMove);
        this.canvas.removeEventListener('touchend', this.handleTouchEnd);
        document.removeEventListener('keydown', this.handleKeyDown);
        document.removeEventListener('keyup', this.handleKeyUp);
    }
}

// Export the InputHandler class as the default export
export default InputHandler;