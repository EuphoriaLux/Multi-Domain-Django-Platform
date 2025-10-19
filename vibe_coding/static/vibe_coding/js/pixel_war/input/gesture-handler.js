/**
 * GestureHandler - Modern touch and mouse input handling using @use-gesture/vanilla
 * Provides smooth panning, pinch-to-zoom, and tap interactions with momentum scrolling
 */

import { Gesture } from '@use-gesture/vanilla';
import { PixelWarConfig } from '../config/pixel-war-config.js';

class GestureHandler extends EventTarget {
    constructor(canvas, config) {
        super();
        this.canvas = canvas;
        this.config = config;
        
        // View state that will be managed by PixelWar
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        
        // Animation state for smooth scrolling
        this.animation = {
            targetX: 0,
            targetY: 0,
            targetZoom: 1,
            velocityX: 0,
            velocityY: 0,
            animating: false,
            frameId: null
        };
        
        // Touch mode settings
        this.touchMode = localStorage.getItem('pixelWarTouchMode') || 'tap';
        this.selectedPixel = { x: null, y: null };
        
        // Gesture configuration
        this.gestureConfig = {
            drag: {
                filterTaps: true,
                pointer: { touch: true, mouse: true },
                threshold: 3
            },
            pinch: {
                from: () => this.zoom,
                scaleBounds: { min: 0.5, max: 10 },
                rubberband: true
            },
            wheel: {
                from: () => this.zoom,
                eventOptions: { passive: false }  // Fix passive event listener warning
            }
        };
        
        this.setupGestures();
        this.setupKeyboardEvents();
    }
    
    calculateBounds() {
        const rect = this.canvas.getBoundingClientRect();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        // Calculate viewport dimensions in grid units
        const viewportWidth = rect.width / (pixelSize * this.zoom);
        const viewportHeight = rect.height / (pixelSize * this.zoom);
        
        // Grid dimensions
        const gridWidth = this.config.width;
        const gridHeight = this.config.height;
        
        console.log('📐 calculateBounds debug:', {
            canvasActualSize: `${rect.width}x${rect.height}`,
            canvasAttributeSize: `${this.canvas.width}x${this.canvas.height}`,
            zoom: this.zoom,
            pixelSize,
            viewportInGrid: `${viewportWidth.toFixed(2)}x${viewportHeight.toFixed(2)}`,
            gridSize: `${gridWidth}x${gridHeight}`,
            configDebug: {
                gridWidth: this.config.gridWidth,
                gridHeight: this.config.gridHeight,
                width: this.config.width,
                height: this.config.height
            },
            viewportLargerThanGrid: viewportWidth >= gridWidth && viewportHeight >= gridHeight
        });
        
        if (viewportWidth >= gridWidth && viewportHeight >= gridHeight) {
            // Viewport is larger than grid - center the grid in viewport
            // When viewport is 249.69 and grid is 100, we want to center at (0,0)
            // The grid should be positioned so (0,0) to (100,100) is visible in center
            
            // For proper centering, the offset should position grid center at viewport center
            // Grid center is at (gridWidth/2, gridHeight/2) = (50, 50)
            // Viewport center is at (viewportWidth/2, viewportHeight/2) = (124.84, 124.84)
            // Offset needed: viewport_center - grid_center
            const idealCenterX = (viewportWidth - gridWidth) / 2;
            const idealCenterY = (viewportHeight - gridHeight) / 2;
            
            // Allow small movement around ideal center
            const allowance = Math.min(gridWidth, gridHeight) * 0.1; // 10% allowance
            
            const bounds = {
                left: idealCenterX - allowance,
                right: idealCenterX + allowance,
                top: idealCenterY - allowance,
                bottom: idealCenterY + allowance
            };
            
            console.log('📐 Centered bounds (viewport > grid):', {
                ...bounds,
                idealCenter: `${idealCenterX.toFixed(2)},${idealCenterY.toFixed(2)}`,
                allowance: allowance.toFixed(2)
            });
            return bounds;
        } else {
            // Normal bounds when zoomed in
            const maxX = Math.max(0, gridWidth - viewportWidth);
            const maxY = Math.max(0, gridHeight - viewportHeight);
            
            const bounds = {
                left: -maxX,
                right: 0,
                top: -maxY,
                bottom: 0
            };
            
            console.log('📐 Zoomed bounds (viewport < grid):', bounds);
            return bounds;
        }
    }
    
    setupGestures() {
        const gesture = new Gesture(
            this.canvas,
            {
                onDrag: this.handleDrag.bind(this),
                onDragEnd: this.handleDragEnd.bind(this),
                onPinch: this.handlePinch.bind(this),
                onWheel: this.handleWheel.bind(this),
                onClick: this.handleClick.bind(this),
                onHover: this.handleHover.bind(this)
            },
            this.gestureConfig
        );
        
        this.gesture = gesture;
    }
    
    handleDrag({ offset: [x, y], velocity: [vx, vy], down, tap, first, last, event, delta: [dx, dy] }) {
        // Check if it's a tap
        if (tap) {
            this.handleTap(event);
            return;
        }
        
        // Ignore if it's not a proper drag
        if (!down) return;
        
        if (first) {
            console.log('🖐️ Drag started from:', { offsetX: this.offsetX, offsetY: this.offsetY, zoom: this.zoom });
            this.dispatchEvent(new CustomEvent('dragstart', {
                detail: { x: this.offsetX, y: this.offsetY }
            }));
            this.canvas.style.cursor = 'grabbing';
        }
        
        // Use delta-based movement instead of absolute offset
        if (dx !== 0 || dy !== 0) {
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            
            // Convert screen delta to grid delta (invert for natural panning)
            const gridDeltaX = -dx / (pixelSize * this.zoom);
            const gridDeltaY = -dy / (pixelSize * this.zoom);
            
            const oldOffsetX = this.offsetX;
            const oldOffsetY = this.offsetY;
            
            // Update offsets incrementally
            this.offsetX += gridDeltaX;
            this.offsetY += gridDeltaY;
            
            // Apply bounds constraints
            const bounds = this.calculateBounds();
            this.offsetX = Math.max(bounds.left, Math.min(bounds.right, this.offsetX));
            this.offsetY = Math.max(bounds.top, Math.min(bounds.bottom, this.offsetY));
            
            console.log('🖱️ Drag movement:', {
                screenDelta: `${dx.toFixed(1)},${dy.toFixed(1)}`,
                gridDelta: `${gridDeltaX.toFixed(3)},${gridDeltaY.toFixed(3)}`,
                oldOffset: `${oldOffsetX.toFixed(2)},${oldOffsetY.toFixed(2)}`,
                newOffset: `${this.offsetX.toFixed(2)},${this.offsetY.toFixed(2)}`,
                bounds: bounds
            });
            
            // Dispatch pan event with current offsets
            this.dispatchEvent(new CustomEvent('pan', {
                detail: { 
                    offsetX: this.offsetX, 
                    offsetY: this.offsetY,
                    velocityX: vx,
                    velocityY: vy,
                    isDragging: true
                }
            }));
        }
        
        if (last) {
            console.log('🖐️ Drag ended at:', { offsetX: this.offsetX, offsetY: this.offsetY, velocity: [vx, vy] });
            this.handleDragEnd({ velocity: [vx, vy] });
        }
    }
    
    handleDragEnd({ velocity: [vx, vy] }) {
        this.canvas.style.cursor = 'crosshair';
        
        // Start momentum animation if there's velocity
        if (Math.abs(vx) > 0.5 || Math.abs(vy) > 0.5) {
            this.startMomentumAnimation(vx, vy);
        }
        
        this.dispatchEvent(new CustomEvent('dragend', {
            detail: { velocityX: vx, velocityY: vy }
        }));
    }
    
    handlePinch({ offset: [scale], origin: [ox, oy], first, last, active }) {
        if (first) {
            this.pinchOrigin = { x: ox, y: oy };
            console.log('🤏 Pinch started:', {
                zoom: this.zoom.toFixed(3),
                origin: `${ox.toFixed(1)},${oy.toFixed(1)}`,
                offset: `${this.offsetX.toFixed(2)},${this.offsetY.toFixed(2)}`
            });
        }
        
        // Calculate zoom with proper center point
        const oldZoom = this.zoom;
        const newZoom = Math.max(0.5, Math.min(10, scale));
        
        if (active) {
            // Calculate the zoom center point relative to canvas
            const rect = this.canvas.getBoundingClientRect();
            const centerX = ox - rect.left;
            const centerY = oy - rect.top;
            
            console.log('🤏 Pinch active:', {
                oldZoom: oldZoom.toFixed(3),
                newZoom: newZoom.toFixed(3),
                centerPoint: `${centerX.toFixed(1)},${centerY.toFixed(1)}`,
                offsetBefore: `${this.offsetX.toFixed(2)},${this.offsetY.toFixed(2)}`
            });
            
            // Dispatch zoom event with proper center
            this.dispatchEvent(new CustomEvent('zoom', {
                detail: { 
                    zoom: newZoom,
                    centerX,
                    centerY,
                    isPinch: true
                }
            }));
            
            this.zoom = newZoom;
            console.log('🤏 After pinch offset:', `${this.offsetX.toFixed(2)},${this.offsetY.toFixed(2)}`);
        }
        
        if (last) {
            console.log('🤏 Pinch ended at zoom:', newZoom.toFixed(3));
            this.animation.targetZoom = newZoom;
        }
    }
    
    handleWheel({ offset: [scale], event }) {
        event.preventDefault();
        
        const oldZoom = this.zoom;
        const newZoom = Math.max(0.5, Math.min(10, scale));
        
        // Get mouse position for zoom center
        const rect = this.canvas.getBoundingClientRect();
        const centerX = event.clientX - rect.left;
        const centerY = event.clientY - rect.top;
        
        console.log('🖱️ Wheel zoom:', {
            oldZoom: oldZoom.toFixed(3),
            newZoom: newZoom.toFixed(3),
            centerPoint: `${centerX.toFixed(1)},${centerY.toFixed(1)}`,
            offsetBefore: `${this.offsetX.toFixed(2)},${this.offsetY.toFixed(2)}`
        });
        
        this.dispatchEvent(new CustomEvent('zoom', {
            detail: {
                zoom: newZoom,
                centerX,
                centerY,
                isWheel: true
            }
        }));
        
        this.zoom = newZoom;
        this.animation.targetZoom = newZoom;
        
        console.log('🖱️ After wheel zoom offset:', `${this.offsetX.toFixed(2)},${this.offsetY.toFixed(2)}`);
    }
    
    handleClick({ event, tap }) {
        if (!tap) return;
        
        this.handleTap(event);
    }
    
    handleTap(event) {
        const coords = this.screenToCanvas(event.clientX, event.clientY);
        
        if (!coords) return;
        
        if (this.touchMode === 'tap') {
            // Direct placement mode
            this.dispatchEvent(new CustomEvent('pixelplace', {
                detail: { x: coords.x, y: coords.y }
            }));
            
            // Haptic feedback
            if (navigator.vibrate) {
                navigator.vibrate(25);
            }
        } else {
            // Precision mode - show preview
            this.showPixelPreview(coords.x, coords.y);
        }
    }
    
    handleHover({ xy: [x, y], hovering }) {
        if (!hovering) {
            this.dispatchEvent(new CustomEvent('leave'));
            return;
        }
        
        this.dispatchEvent(new CustomEvent('hover', {
            detail: { x, y }
        }));
    }
    
    startMomentumAnimation(velocityX, velocityY) {
        if (this.animation.frameId) {
            cancelAnimationFrame(this.animation.frameId);
        }
        
        this.animation.animating = true;
        const friction = 0.92; // Friction coefficient for natural deceleration
        const threshold = 0.1; // Stop threshold
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        // Convert screen velocity to grid velocity
        this.animation.velocityX = -velocityX / (pixelSize * this.zoom);
        this.animation.velocityY = -velocityY / (pixelSize * this.zoom);
        
        const animate = () => {
            // Apply friction
            this.animation.velocityX *= friction;
            this.animation.velocityY *= friction;
            
            // Update position incrementally
            this.offsetX += this.animation.velocityX * 0.016; // 60fps timing
            this.offsetY += this.animation.velocityY * 0.016;
            
            // Apply bounds
            const bounds = this.calculateBounds();
            this.offsetX = Math.max(bounds.left, Math.min(bounds.right, this.offsetX));
            this.offsetY = Math.max(bounds.top, Math.min(bounds.bottom, this.offsetY));
            
            // Dispatch pan event
            this.dispatchEvent(new CustomEvent('pan', {
                detail: {
                    offsetX: this.offsetX,
                    offsetY: this.offsetY,
                    velocityX: this.animation.velocityX,
                    velocityY: this.animation.velocityY,
                    isMomentum: true
                }
            }));
            
            // Continue animation if velocity is significant
            if (Math.abs(this.animation.velocityX) > threshold || Math.abs(this.animation.velocityY) > threshold) {
                this.animation.frameId = requestAnimationFrame(animate);
            } else {
                this.animation.animating = false;
                this.animation.velocityX = 0;
                this.animation.velocityY = 0;
            }
        };
        
        animate();
    }
    
    screenToCanvas(screenX, screenY) {
        const rect = this.canvas.getBoundingClientRect();
        
        if (!rect.width || !rect.height || isNaN(screenX) || isNaN(screenY)) {
            return null;
        }
        
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        // Convert screen coordinates to canvas coordinates
        const canvasX = (screenX - rect.left) / (pixelSize * this.zoom) - this.offsetX;
        const canvasY = (screenY - rect.top) / (pixelSize * this.zoom) - this.offsetY;
        
        // Round and clamp to bounds
        const x = Math.floor(Math.max(0, Math.min(this.config.width - 1, canvasX)));
        const y = Math.floor(Math.max(0, Math.min(this.config.height - 1, canvasY)));
        
        return { x, y };
    }
    
    showPixelPreview(x, y) {
        this.selectedPixel = { x, y };
        
        // Create confirmation UI
        const existing = document.getElementById('pixelConfirmation');
        if (existing) existing.remove();
        
        const modal = document.createElement('div');
        modal.id = 'pixelConfirmation';
        modal.className = 'pixel-confirmation-gesture';
        modal.innerHTML = `
            <div class="confirmation-backdrop"></div>
            <div class="confirmation-card">
                <h4>Place Pixel</h4>
                <p>Position: (${x}, ${y})</p>
                <div class="confirmation-actions">
                    <button class="btn-confirm">✓ Confirm</button>
                    <button class="btn-cancel">✕ Cancel</button>
                </div>
            </div>
        `;
        
        // Add styles
        modal.style.cssText = `
            position: fixed;
            inset: 0;
            z-index: 10000;
            display: flex;
            align-items: center;
            justify-content: center;
        `;
        
        const backdrop = modal.querySelector('.confirmation-backdrop');
        backdrop.style.cssText = `
            position: absolute;
            inset: 0;
            background: rgba(0,0,0,0.5);
        `;
        
        const card = modal.querySelector('.confirmation-card');
        card.style.cssText = `
            position: relative;
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            text-align: center;
            min-width: 250px;
        `;
        
        // Add event handlers
        modal.querySelector('.btn-confirm').onclick = () => {
            this.dispatchEvent(new CustomEvent('pixelplace', {
                detail: { x, y }
            }));
            modal.remove();
            
            if (navigator.vibrate) {
                navigator.vibrate(50);
            }
        };
        
        modal.querySelector('.btn-cancel').onclick = () => {
            modal.remove();
        };
        
        backdrop.onclick = () => {
            modal.remove();
        };
        
        document.body.appendChild(modal);
        
        // Light haptic feedback
        if (navigator.vibrate) {
            navigator.vibrate(25);
        }
    }
    
    setupKeyboardEvents() {
        document.addEventListener('keydown', this.handleKeyDown.bind(this));
        document.addEventListener('keyup', this.handleKeyUp.bind(this));
    }
    
    handleKeyDown(e) {
        // Arrow key navigation
        const step = e.shiftKey ? 10 : 1;
        let moved = false;
        
        switch(e.key) {
            case 'ArrowUp':
                e.preventDefault();
                this.offsetY += step;
                moved = true;
                break;
            case 'ArrowDown':
                e.preventDefault();
                this.offsetY -= step;
                moved = true;
                break;
            case 'ArrowLeft':
                e.preventDefault();
                this.offsetX += step;
                moved = true;
                break;
            case 'ArrowRight':
                e.preventDefault();
                this.offsetX -= step;
                moved = true;
                break;
        }
        
        if (moved) {
            const bounds = this.calculateBounds();
            this.offsetX = Math.max(bounds.left, Math.min(bounds.right, this.offsetX));
            this.offsetY = Math.max(bounds.top, Math.min(bounds.bottom, this.offsetY));
            
            this.dispatchEvent(new CustomEvent('pan', {
                detail: {
                    offsetX: this.offsetX,
                    offsetY: this.offsetY,
                    isKeyboard: true
                }
            }));
        }
        
        // Zoom with + and -
        if (e.key === '+' || e.key === '=') {
            e.preventDefault();
            this.zoom = Math.min(10, this.zoom * 1.1);
            this.dispatchEvent(new CustomEvent('zoom', {
                detail: { zoom: this.zoom, isKeyboard: true }
            }));
        } else if (e.key === '-' || e.key === '_') {
            e.preventDefault();
            this.zoom = Math.max(0.5, this.zoom / 1.1);
            this.dispatchEvent(new CustomEvent('zoom', {
                detail: { zoom: this.zoom, isKeyboard: true }
            }));
        }
        
        this.dispatchEvent(new CustomEvent('keydown', { detail: { key: e.key } }));
    }
    
    handleKeyUp(e) {
        this.dispatchEvent(new CustomEvent('keyup', { detail: { key: e.key } }));
    }
    
    setTouchMode(mode) {
        this.touchMode = mode;
        localStorage.setItem('pixelWarTouchMode', mode);
    }
    
    updateViewState(zoom, offsetX, offsetY) {
        // CRITICAL: Prevent NaN propagation 
        if (isNaN(zoom) || isNaN(offsetX) || isNaN(offsetY)) {
            console.warn('🚨 BLOCKED NaN values from PixelWar:', {
                zoom, offsetX, offsetY,
                currentState: { zoom: this.zoom, offsetX: this.offsetX, offsetY: this.offsetY }
            });
            return; // Don't update with NaN values
        }
        
        console.log('🔄 GestureHandler.updateViewState:', {
            oldState: { zoom: this.zoom, offsetX: this.offsetX, offsetY: this.offsetY },
            newState: { zoom, offsetX, offsetY },
            source: 'PixelWar sync'
        });
        
        // Only update if we have valid numbers
        if (zoom > 0 && zoom < 100) { // Reasonable zoom range
            this.zoom = zoom;
        }
        
        // Calculate ideal position for comparison
        const rect = this.canvas.getBoundingClientRect();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        const viewportWidth = rect.width / (pixelSize * this.zoom);
        const viewportHeight = rect.height / (pixelSize * this.zoom);
        const gridWidth = this.config.width;
        const gridHeight = this.config.height;
        
        if (viewportWidth >= gridWidth && viewportHeight >= gridHeight) {
            const idealCenterX = (viewportWidth - gridWidth) / 2;
            const idealCenterY = (viewportHeight - gridHeight) / 2;
            
            // Check if incoming values are reasonable
            const isOffsetReasonable = (
                Math.abs(offsetX - idealCenterX) < gridWidth &&
                Math.abs(offsetY - idealCenterY) < gridHeight
            );
            
            if (isOffsetReasonable) {
                // Accept reasonable offsets from PixelWar
                this.offsetX = offsetX;
                this.offsetY = offsetY;
                console.log('✅ Accepted reasonable offset from PixelWar:', {
                    offsetX: offsetX.toFixed(2),
                    offsetY: offsetY.toFixed(2),
                    idealCenter: `${idealCenterX.toFixed(2)},${idealCenterY.toFixed(2)}`
                });
            } else {
                // Use our ideal center instead of unreasonable values
                this.offsetX = idealCenterX;
                this.offsetY = idealCenterY;
                console.log('🔧 Rejected unreasonable offset, using ideal center:', {
                    rejected: `${offsetX.toFixed(2)},${offsetY.toFixed(2)}`,
                    using: `${idealCenterX.toFixed(2)},${idealCenterY.toFixed(2)}`
                });
                
                // Don't send correction events in a loop - only if we haven't corrected recently
                if (!this.lastCorrectionTime || (Date.now() - this.lastCorrectionTime > 1000)) {
                    this.lastCorrectionTime = Date.now();
                    setTimeout(() => {
                        this.dispatchEvent(new CustomEvent('pan', {
                            detail: { 
                                offsetX: this.offsetX, 
                                offsetY: this.offsetY,
                                isCorrection: true
                            }
                        }));
                    }, 100); // Delay to prevent immediate sync loops
                }
            }
        } else {
            // For zoomed-in view, accept values if they're numbers
            if (typeof offsetX === 'number' && typeof offsetY === 'number') {
                this.offsetX = offsetX;
                this.offsetY = offsetY;
            }
        }
        
        // Update bounds after state change
        const bounds = this.calculateBounds();
        console.log('📐 Updated bounds after sync:', bounds);
    }
    
    destroy() {
        if (this.animation.frameId) {
            cancelAnimationFrame(this.animation.frameId);
        }
        
        if (this.gesture) {
            this.gesture.destroy();
        }
        
        document.removeEventListener('keydown', this.handleKeyDown);
        document.removeEventListener('keyup', this.handleKeyUp);
    }
}

export default GestureHandler;