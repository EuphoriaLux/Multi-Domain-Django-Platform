/**
 * PixelWar - Main Application Controller
 * 
 * This is the main coordinator class for the Pixel War application.
 * It manages all subsystems including rendering, input handling, API communication,
 * rate limiting, and user interface updates.
 */

import { PixelWarConfig } from '../config/pixel-war-config.js';
import { PixelWarAPI, APIError } from '../api/pixel-war-api.js';
import { CanvasRenderer } from '../rendering/canvas-renderer.js';
import InputHandler from '../input/input-handler.js';
import { RateLimiter } from './rate-limiter.js';
import NotificationManager from './notification-manager.js';

export class PixelWar {
    constructor(canvasOrId, config) {
        // Handle both canvas element and canvas ID
        if (typeof canvasOrId === 'string') {
            this.canvas = document.getElementById(canvasOrId);
            if (!this.canvas) {
                throw new Error(`Canvas element with id "${canvasOrId}" not found`);
            }
        } else if (canvasOrId instanceof HTMLCanvasElement) {
            this.canvas = canvasOrId;
        } else {
            throw new Error('First parameter must be either a canvas element or canvas ID string');
        }

        this.config = config;
        this.isRunning = false;

        // Initialize modules
        this.api = new PixelWarAPI();
        this.renderer = new CanvasRenderer(this.canvas, config);
        this.inputHandler = new InputHandler(this.canvas, config);
        this.rateLimiter = new RateLimiter(
            config.isAuthenticated ? config.registeredPixelsPerMinute : config.anonymousPixelsPerMinute,
            config.isAuthenticated ? config.registeredCooldown : config.anonymousCooldown
        );
        this.notifications = new NotificationManager();
        
        // Sync view state with input handler
        this.syncViewState();

        // Canvas state
        this.zoom = PixelWarConfig.canvas.defaultZoom;
        this.offsetX = 0;
        this.offsetY = 0;
        this.targetOffsetX = 0;
        this.targetOffsetY = 0;
        
        // Mobile optimization
        this.mobileConstraintTimeout = null;
        this.velocityX = 0;
        this.velocityY = 0;
        this.selectedColor = '#000000';

        // Navigation state
        this.isIntentionalNavigation = false;

        // Animation
        this.animationFrame = null;
        this.updateInterval = null;
        this.lastFrameTime = 0;

        this.init();
    }

    async init() {
        try {
            // Setup renderer
            this.renderer.setup(this.config.width, this.config.height, PixelWarConfig.canvas.defaultPixelSize);

            // Setup event listeners
            this.setupEventHandlers();

            // Load initial state
            await this.loadCanvasState();

            // Start update loop
            this.startUpdateLoop();

            // Setup UI controls
            this.setupUIControls();
            
            // Set proper initial zoom based on canvas size
            this.initializeZoom();

            this.isRunning = true;
            this.notifications.show('Canvas ready!', 'success');
        } catch (error) {
            console.error('Failed to initialize PixelWar:', error);
            this.notifications.show('Failed to initialize canvas', 'error');
        }
    }

    setupEventHandlers() {
        // Input events
        this.inputHandler.addEventListener('click', (e) => {
            const coords = this.screenToCanvas(e.detail.x, e.detail.y);
            if (coords && this.isValidCoordinate(coords.x, coords.y)) {
                this.placePixel(coords.x, coords.y);
            }
        });

        this.inputHandler.addEventListener('tap', (e) => {
            const coords = this.screenToCanvas(e.detail.x, e.detail.y);
            if (coords && this.isValidCoordinate(coords.x, coords.y)) {
                this.placePixel(coords.x, coords.y);
            }
        });
        
        // Handle precision mode pixel placement
        this.inputHandler.addEventListener('pixelplace', (e) => {
            if (this.isValidCoordinate(e.detail.x, e.detail.y)) {
                this.placePixel(e.detail.x, e.detail.y);
            }
        });

        this.inputHandler.addEventListener('drag', (e) => {
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            // Fixed coordinate system: invert deltas for intuitive panning
            this.targetOffsetX -= e.detail.deltaX / (pixelSize * this.zoom);
            this.targetOffsetY -= e.detail.deltaY / (pixelSize * this.zoom);
            
            // Normalize velocity for consistent movement feel
            this.velocityX = (e.detail.velocityX || e.detail.deltaX) / (pixelSize * this.zoom);
            this.velocityY = (e.detail.velocityY || e.detail.deltaY) / (pixelSize * this.zoom);
            
            this.constrainOffsets();
            this.startAnimation();
        });

        // Handle touch drag events (mobile) with enhanced sensitivity
        this.inputHandler.addEventListener('touchdrag', (e) => {
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            
            // Consistent movement calculation for better touch response
            const deltaX = e.detail.deltaX || 0;
            const deltaY = e.detail.deltaY || 0;
            const isMobile = e.detail.isMobile || false;
            const shiftKey = e.detail.shiftKey || false;
            const ctrlKey = e.detail.ctrlKey || false;
            
            // Handle CTRL key modifier for zoom (like wheel events)
            if (ctrlKey) {
                // CTRL key: vertical drag triggers zoom
                const zoomDelta = deltaY > 0 ? -0.1 : 0.1;
                
                // Get touch center for zoom focal point
                const rect = this.canvas.getBoundingClientRect();
                const centerX = rect.left + rect.width / 2;
                const centerY = rect.top + rect.height / 2;
                
                this.adjustZoom(zoomDelta, centerX, centerY);
                return; // Skip movement logic when zooming
            } else if (shiftKey) {
                // Shift key: only horizontal movement based on vertical drag direction
                const panSpeed = 30 / (pixelSize * this.zoom);
                this.targetOffsetX -= Math.sign(deltaY) * panSpeed;
            } else {
                // Normal 2D movement - Fixed coordinate system for mobile
                // Invert the deltaX and deltaY to match intuitive pan behavior:
                // When user drags right, content should move left (negative offset)
                // When user drags down, content should move up (negative offset)
                this.targetOffsetX -= deltaX / (pixelSize * this.zoom);
                this.targetOffsetY -= deltaY / (pixelSize * this.zoom);
            }
            
            // Improved velocity calculation based on actual movement
            if (!shiftKey && !ctrlKey) {
                this.velocityX = (e.detail.velocityX || deltaX) / (pixelSize * this.zoom * 5);
                this.velocityY = (e.detail.velocityY || deltaY) / (pixelSize * this.zoom * 5);
            } else if (shiftKey) {
                // For shift mode, only horizontal velocity
                this.velocityX = Math.sign(deltaY) * -30 / (pixelSize * this.zoom * 5);
                this.velocityY = 0;
            }
            // For CTRL mode, no velocity since we're zooming, not panning
            
            // Smoother constraint application
            if (isMobile) {
                // For mobile, apply constraints with soft boundaries
                this.constrainOffsets(true);
            } else {
                // Desktop: minimal delay for better drag feel
                clearTimeout(this.mobileConstraintTimeout);
                this.mobileConstraintTimeout = setTimeout(() => {
                    this.constrainOffsets();
                }, 20); // Faster response
            }
            
            this.startAnimation();
        });

        this.inputHandler.addEventListener('dragend', () => {
            // Clear mobile constraint timeout and apply final constraints
            clearTimeout(this.mobileConstraintTimeout);
            
            // Always apply constraints immediately on drag end
            this.constrainOffsets();
            
            if (Math.abs(this.velocityX) > 2 || Math.abs(this.velocityY) > 2) {
                // Only apply momentum for significant velocity
                this.applyMomentum();
            }
        });

        this.inputHandler.addEventListener('zoom', (e) => {
            this.adjustZoom(e.detail.delta, e.detail.x, e.detail.y);
        });

        this.inputHandler.addEventListener('pan', (e) => {
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            const panSpeed = 30 / (pixelSize * this.zoom);
            
            if (e.detail.shiftKey) {
                this.targetOffsetX -= Math.sign(e.detail.deltaY) * panSpeed;
            } else {
                this.targetOffsetX -= e.detail.deltaX * panSpeed * 0.01;
                this.targetOffsetY -= e.detail.deltaY * panSpeed * 0.01;
            }
            
            this.constrainOffsets();
            this.startAnimation();
        });

        this.inputHandler.addEventListener('pinchzoom', (e) => {
            this.adjustZoom((e.detail.scale - 1) * 0.5, e.detail.centerX, e.detail.centerY);
        });

        this.inputHandler.addEventListener('keydown', (e) => {
            const moveSpeed = 50 / (PixelWarConfig.canvas.defaultPixelSize * this.zoom);
            let keyHandled = false;
            
            switch(e.detail.key) {
                case 'ArrowUp':
                    this.targetOffsetY -= moveSpeed;
                    keyHandled = true;
                    break;
                case 'ArrowDown':
                    this.targetOffsetY += moveSpeed;
                    keyHandled = true;
                    break;
                case 'ArrowLeft':
                    this.targetOffsetX -= moveSpeed;
                    keyHandled = true;
                    break;
                case 'ArrowRight':
                    this.targetOffsetX += moveSpeed;
                    keyHandled = true;
                    break;
                case '+':
                case '=':
                    this.adjustZoom(0.2);
                    keyHandled = true;
                    break;
                case '-':
                    this.adjustZoom(-0.2);
                    keyHandled = true;
                    break;
                case '0':
                    this.resetView();
                    keyHandled = true;
                    break;
            }
            
            // Only update constraints and animation if a movement/zoom key was actually pressed
            if (keyHandled) {
                this.constrainOffsets();
                this.startAnimation();
            }
        });
    }

    setupUIControls() {
        // Color selection
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.selectedColor = e.target.dataset.color;
                this.highlightSelectedColor(e.target);
            });
        });

        const colorPicker = document.getElementById('colorPicker');
        if (colorPicker) {
            colorPicker.addEventListener('change', (e) => {
                this.selectedColor = e.target.value;
            });
        }
        
        // Setup mobile touch mode toggle
        this.setupTouchModeToggle();

        // Zoom controls
        const zoomIn = document.getElementById('zoomIn');
        const zoomOut = document.getElementById('zoomOut');
        const zoomReset = document.getElementById('zoomReset');
        
        if (zoomIn) zoomIn.addEventListener('click', () => this.adjustZoom(0.2));
        if (zoomOut) zoomOut.addEventListener('click', () => this.adjustZoom(-0.2));
        if (zoomReset) zoomReset.addEventListener('click', () => this.resetView());
        
        // Corner navigation buttons for mobile
        const cornerButtons = document.querySelectorAll('[data-corner]');
        cornerButtons.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const corner = e.target.closest('[data-corner]').dataset.corner;
                this.navigateToCorner(corner);
            });
        });
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

    // New method for precise screen-to-canvas conversion (for zoom calculations)
    screenToCanvasPrecise(screenX, screenY) {
        const rect = this.canvas.getBoundingClientRect();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        // No rounding - keep full precision for zoom focal point calculations
        const canvasX = (screenX - rect.left) / (pixelSize * this.zoom);
        const canvasY = (screenY - rect.top) / (pixelSize * this.zoom);
        
        return {
            x: canvasX - this.offsetX,
            y: canvasY - this.offsetY
        };
    }

    isValidCoordinate(x, y) {
        return x >= 0 && x < this.config.width && y >= 0 && y < this.config.height;
    }
    
    // Sync view state (zoom, offsets) with input handler for coordinate calculations
    syncViewState() {
        this.inputHandler.zoom = this.zoom;
        this.inputHandler.offsetX = this.offsetX;
        this.inputHandler.offsetY = this.offsetY;
    }

    // Helper method to calculate effective viewport dimensions consistently
    getEffectiveViewport(forceMobile = false) {
        const rect = this.canvas.getBoundingClientRect();
        const isMobile = forceMobile || /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth < 768;
        
        let effectiveWidth = rect.width;
        let effectiveHeight = rect.height;
        
        if (isMobile) {
            // Use visual viewport API for better mobile browser compatibility (Chrome 61+, Safari 13+)
            if (window.visualViewport) {
                // Visual viewport gives us the actual visible area excluding browser chrome
                // But we should NOT divide by scale - that's for browser zoom, not our canvas zoom
                effectiveWidth = Math.min(window.visualViewport.width, rect.width);
                effectiveHeight = Math.min(window.visualViewport.height, rect.height);
                
                // Fallback to rect dimensions if visualViewport gives unrealistic values
                if (effectiveWidth < 100 || effectiveHeight < 100) {
                    effectiveWidth = rect.width;
                    effectiveHeight = rect.height;
                }
            }
            
            // Get actual rendered dimensions of mobile UI elements
            const mobileTopControls = document.querySelector('.mobile-controls-bar') || 
                                    document.getElementById('mobileTopControls');
            const mobileCanvasControls = document.querySelector('.mobile-canvas-controls');
            const mobileActionBar = document.querySelector('.mobile-action-bar') || 
                                   document.getElementById('mobileActionBar');
            
            let topControlsHeight = 0;
            let bottomControlsHeight = 0;
            
            // Check for various mobile UI elements that might be taking up space
            if (mobileTopControls && getComputedStyle(mobileTopControls).display !== 'none') {
                topControlsHeight = mobileTopControls.offsetHeight;
                console.log('📱 Found mobile top controls:', topControlsHeight + 'px');
            }
            
            if (mobileCanvasControls && getComputedStyle(mobileCanvasControls).display !== 'none') {
                bottomControlsHeight += mobileCanvasControls.offsetHeight;
                console.log('📱 Found mobile canvas controls:', mobileCanvasControls.offsetHeight + 'px');
            }
            
            if (mobileActionBar && getComputedStyle(mobileActionBar).display !== 'none') {
                bottomControlsHeight += mobileActionBar.offsetHeight;
                console.log('📱 Found mobile action bar:', mobileActionBar.offsetHeight + 'px');
            }
            
            // Add extra margin for mobile browser chrome and navigation elements
            const mobileChromeMargin = 60; // Generous margin for mobile browser UI
            
            // Subtract UI heights with extra mobile margin for browser chrome
            effectiveHeight -= (topControlsHeight + bottomControlsHeight + mobileChromeMargin);
            
            console.log('📱 Mobile Viewport Adjustment:', {
                originalHeight: rect.height,
                topControls: topControlsHeight,
                bottomControls: bottomControlsHeight,
                chromeMargin: mobileChromeMargin,
                finalHeight: effectiveHeight
            });
            
            // Debug viewport calculation occasionally to monitor improvements
            if (Math.random() < 0.01) { // Log 1% of the time
                console.log('📱 Mobile Viewport:', {
                    original: `${rect.width}x${rect.height}`,
                    visualViewport: window.visualViewport ? 
                        `${window.visualViewport.width}x${window.visualViewport.height}` : 'N/A',
                    effective: `${effectiveWidth}x${effectiveHeight}`,
                    uiHeights: `top:${topControlsHeight} bottom:${bottomControlsHeight}`
                });
            }
            
            // Ensure we have reasonable dimensions
            effectiveWidth = Math.max(200, effectiveWidth);
            effectiveHeight = Math.max(200, effectiveHeight);
        }
        
        return {
            width: Math.max(100, effectiveWidth),
            height: Math.max(100, effectiveHeight),
            isMobile
        };
    }

    constrainOffsets(forceMobile = false) {
        const { width: effectiveWidth, height: effectiveHeight, isMobile } = this.getEffectiveViewport(forceMobile);
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        const viewportWidth = effectiveWidth / (pixelSize * this.zoom);
        const viewportHeight = effectiveHeight / (pixelSize * this.zoom);
        
        this.targetOffsetX = this.constrainOffset(this.targetOffsetX, this.config.width, viewportWidth, isMobile);
        this.targetOffsetY = this.constrainOffset(this.targetOffsetY, this.config.height, viewportHeight, isMobile);
        
        // Debug logging for offset calculation - temporarily increase frequency to debug movement issues
        if (Math.random() < 0.2) { // Log 20% of the time to debug constraint issues
            console.log('🎯 Constraint Debug:', {
                zoom: this.zoom.toFixed(3),
                viewportSize: `${viewportWidth.toFixed(1)}x${viewportHeight.toFixed(1)}`,
                mapSize: `${this.config.width}x${this.config.height}`,
                canFitWidth: viewportWidth >= this.config.width,
                canFitHeight: viewportHeight >= this.config.height,
                beforeX: this.targetOffsetX.toFixed(2),
                afterX: this.constrainOffset(this.targetOffsetX, this.config.width, viewportWidth, isMobile).toFixed(2),
                beforeY: this.targetOffsetY.toFixed(2),
                afterY: this.constrainOffset(this.targetOffsetY, this.config.height, viewportHeight, isMobile).toFixed(2),
                isMobile
            });
        }
    }

    constrainOffset(offset, gridSize, viewportSize, isMobile = false) {
        // Check if we're at minimum zoom (map should be centered and fixed)
        const minZoom = this.calculateMinZoom();
        const isAtMinimumZoom = this.zoom <= minZoom * 1.05; // Smaller tolerance for smoother transitions
        
        // Allow much more movement during intentional navigation
        const isNavigating = this.isIntentionalNavigation;
        
        // Debug logging for constraint analysis (temporarily enabled)
        const shouldLog = Math.random() < 0.1; // Log 10% of calls
        if (shouldLog) {
            console.log('🔒 Constraint Analysis:', {
                input: offset.toFixed(2),
                gridSize,
                viewportSize: viewportSize.toFixed(1),
                zoom: this.zoom.toFixed(3),
                minZoom: minZoom.toFixed(3),
                isAtMinimumZoom,
                viewportLargerThanGrid: viewportSize >= gridSize,
                isMobile,
                isNavigating
            });
        }
        
        if (viewportSize >= gridSize) {
            // When viewport is larger than grid
            if (isAtMinimumZoom) {
                // At minimum zoom, center the content properly
                const centeredValue = -(viewportSize - gridSize) / 2;
                
                // Simplified movement range - reduce drift-inducing complexity
                let movementRange;
                if (isNavigating) {
                    // During navigation, allow reasonable movement
                    movementRange = isMobile ? 
                        Math.min(gridSize * 0.4, 80) : // Reduced from 80% to prevent drift
                        Math.min(gridSize * 0.3, 60);   // Reduced range for control
                } else {
                    // Normal constraints - tighter control
                    movementRange = isMobile ? 
                        Math.min(gridSize * 0.2, 40) : // Much tighter mobile constraints
                        Math.min(gridSize * 0.15, 30);  // Tighter desktop constraints
                }
                
                const minBound = centeredValue - movementRange;
                const maxBound = centeredValue + movementRange;
                
                if (shouldLog) console.log('🎯 Min zoom bounds:', {
                    centered: centeredValue.toFixed(2),
                    calculation: `-(${viewportSize.toFixed(1)} - ${gridSize}) / 2 = ${centeredValue.toFixed(2)}`,
                    range: movementRange.toFixed(2),
                    bounds: `${minBound.toFixed(2)} to ${maxBound.toFixed(2)}`,
                    currentOffset: offset.toFixed(2),
                    gridCoords: `Grid spans from (0,0) to (${gridSize},${gridSize})`,
                    isMobile,
                    isNavigating
                });
                
                // During navigation, be much more permissive
                if (isNavigating) {
                    // Allow very soft constraints during navigation
                    const rubberBandStrength = 0.8; // Very flexible during navigation
                    if (offset < minBound) {
                        return minBound + (offset - minBound) * rubberBandStrength;
                    } else if (offset > maxBound) {
                        return maxBound + (offset - maxBound) * rubberBandStrength;
                    }
                    return offset; // Allow free movement within bounds
                } else {
                    // Normal rubber band constraints
                    const rubberBandStrength = isMobile ? 0.5 : 0.3;
                    if (offset < minBound) {
                        return minBound + (offset - minBound) * rubberBandStrength;
                    } else if (offset > maxBound) {
                        return maxBound + (offset - maxBound) * rubberBandStrength;
                    }
                    return offset;
                }
            } else {
                // At higher zooms, allow more movement on mobile
                const centeredOffset = -(viewportSize - gridSize) / 2;
                
                let movementRange;
                if (isNavigating) {
                    movementRange = isMobile ? 
                        Math.min(gridSize * 0.6, 80) : // Larger range for navigation
                        Math.min(gridSize * 0.4, 50);
                } else {
                    movementRange = isMobile ? 
                        Math.min(gridSize * 0.4, 60) : // Larger range for mobile
                        Math.min(gridSize * 0.2, 30);
                }
                
                const minOffset = centeredOffset - movementRange;
                const maxOffset = centeredOffset + movementRange;
                
                // Softer constraints for mobile and navigation
                const constraintStrength = isNavigating ? 0.5 : (isMobile ? 0.2 : 0.1);
                if (offset < minOffset) {
                    return minOffset + (offset - minOffset) * constraintStrength;
                } else if (offset > maxOffset) {
                    return maxOffset + (offset - maxOffset) * constraintStrength;
                } else {
                    return offset;
                }
            }
        }
        
        // When zoomed in, constrain to grid boundaries with smooth transitions
        const baseMinOffset = -(gridSize - viewportSize);
        const baseMaxOffset = 0;
        
        // Calculate overshoot allowance - more generous on mobile
        const overshootFactor = isMobile ? 
            Math.max(0.1, Math.min(0.3, 1 / this.zoom)) : // Mobile: 10-30% overshoot
            Math.max(0.05, Math.min(0.2, 1 / this.zoom)); // Desktop: 5-20% overshoot
        const overshoot = viewportSize * overshootFactor;
        
        const minOffset = baseMinOffset - overshoot;
        const maxOffset = baseMaxOffset + overshoot;
        
        // Apply smooth constraint with rubber-band effect
        if (offset < minOffset) {
            const excess = minOffset - offset;
            const dampening = isMobile ? 0.3 : 0.2; // Slightly more lenient on mobile
            return minOffset - excess * dampening;
        } else if (offset > maxOffset) {
            const excess = offset - maxOffset;
            const dampening = isMobile ? 0.3 : 0.2;
            return maxOffset + excess * dampening;
        } else {
            if (shouldLog) console.log('✅ Within bounds, no constraint needed');
            return offset; // Within bounds, no constraint
        }
    }

    calculateMinZoom() {
        // Calculate minimum zoom to fit entire map in viewport
        const { width: effectiveWidth, height: effectiveHeight, isMobile } = this.getEffectiveViewport();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        const mapPixelWidth = this.config.width * pixelSize;
        const mapPixelHeight = this.config.height * pixelSize;
        
        const zoomToFitWidth = effectiveWidth / mapPixelWidth;
        const zoomToFitHeight = effectiveHeight / mapPixelHeight;
        
        const calculatedMinZoom = Math.min(zoomToFitWidth, zoomToFitHeight);
        
        // Mobile needs slightly more margin but not too aggressive to avoid zoom trap
        const safetyMargin = isMobile ? 0.90 : 0.95; // 10% margin for mobile vs 5% for desktop
        const minZoomFloor = isMobile ? 0.05 : 0.05; // Same minimum floor for consistency
        
        const finalMinZoom = Math.max(calculatedMinZoom * safetyMargin, minZoomFloor);
        
        console.log('🔍 Zoom Calculation:', {
            canvasRect: `${document.getElementById('pixelCanvas').getBoundingClientRect().width}x${document.getElementById('pixelCanvas').getBoundingClientRect().height}`,
            effectiveSize: `${effectiveWidth}x${effectiveHeight}`,
            mapSize: `${this.config.width}x${this.config.height}`,
            mapPixelSize: `${mapPixelWidth}x${mapPixelHeight}`,
            zoomToFitWidth: zoomToFitWidth.toFixed(3),
            zoomToFitHeight: zoomToFitHeight.toFixed(3),
            calculatedMinZoom: calculatedMinZoom.toFixed(3),
            safetyMargin: safetyMargin,
            finalMinZoom: finalMinZoom.toFixed(3),
            isMobile
        });
        
        return finalMinZoom;
    }

    updateZoomIndicator() {
        const zoomIndicator = document.getElementById('zoomLevel');
        if (zoomIndicator) {
            zoomIndicator.textContent = Math.round(this.zoom * 100) + '%';
        }
    }

    adjustZoom(delta, centerX = null, centerY = null) {
        const oldZoom = this.zoom;
        const dynamicMinZoom = this.calculateMinZoom();
        
        // Store old target offsets to prevent jumping
        const oldTargetOffsetX = this.targetOffsetX;
        const oldTargetOffsetY = this.targetOffsetY;
        
        this.zoom = Math.max(
            dynamicMinZoom,
            Math.min(PixelWarConfig.canvas.maxZoom, this.zoom + delta)
        );
        
        if (this.zoom !== oldZoom) {
            if (centerX !== null && centerY !== null) {
                // Improved focal point calculation with better validation
                const rect = this.canvas.getBoundingClientRect();
                const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
                
                // Validate screen coordinates are within canvas bounds
                const isValidScreenCoord = centerX >= rect.left && centerX <= rect.right && 
                                         centerY >= rect.top && centerY <= rect.bottom;
                
                if (isValidScreenCoord) {
                    // Calculate normalized position within viewport (0-1)
                    const normalizedX = (centerX - rect.left) / rect.width;
                    const normalizedY = (centerY - rect.top) / rect.height;
                    
                    // Calculate viewport dimensions at old zoom
                    const oldViewportWidth = rect.width / (pixelSize * oldZoom);
                    const oldViewportHeight = rect.height / (pixelSize * oldZoom);
                    
                    // Calculate viewport dimensions at new zoom
                    const newViewportWidth = rect.width / (pixelSize * this.zoom);
                    const newViewportHeight = rect.height / (pixelSize * this.zoom);
                    
                    // Calculate the world position that should stay under the cursor
                    const worldX = oldTargetOffsetX + normalizedX * oldViewportWidth;
                    const worldY = oldTargetOffsetY + normalizedY * oldViewportHeight;
                    
                    // Calculate new offsets to keep that world position under cursor
                    this.targetOffsetX = worldX - normalizedX * newViewportWidth;
                    this.targetOffsetY = worldY - normalizedY * newViewportHeight;
                } else {
                    // Invalid coordinates: maintain current view center
                    const zoomRatio = oldZoom / this.zoom;
                    const rect = this.canvas.getBoundingClientRect();
                    const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
                    
                    const oldViewportWidth = rect.width / (pixelSize * oldZoom);
                    const oldViewportHeight = rect.height / (pixelSize * oldZoom);
                    const newViewportWidth = rect.width / (pixelSize * this.zoom);
                    const newViewportHeight = rect.height / (pixelSize * this.zoom);
                    
                    // Keep center point stable
                    const centerWorldX = oldTargetOffsetX + oldViewportWidth / 2;
                    const centerWorldY = oldTargetOffsetY + oldViewportHeight / 2;
                    
                    this.targetOffsetX = centerWorldX - newViewportWidth / 2;
                    this.targetOffsetY = centerWorldY - newViewportHeight / 2;
                }
            } else {
                // No focal point provided: zoom from current center
                const rect = this.canvas.getBoundingClientRect();
                const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
                
                const oldViewportWidth = rect.width / (pixelSize * oldZoom);
                const oldViewportHeight = rect.height / (pixelSize * oldZoom);
                const newViewportWidth = rect.width / (pixelSize * this.zoom);
                const newViewportHeight = rect.height / (pixelSize * this.zoom);
                
                // Keep center point stable
                const centerWorldX = oldTargetOffsetX + oldViewportWidth / 2;
                const centerWorldY = oldTargetOffsetY + oldViewportHeight / 2;
                
                this.targetOffsetX = centerWorldX - newViewportWidth / 2;
                this.targetOffsetY = centerWorldY - newViewportHeight / 2;
            }
            
            // Apply constraints to new target offsets
            this.constrainOffsets();
            
            // Start smooth zoom animation
            this.startAnimation();
        }
    }

    initializeZoom() {
        // Set initial zoom to ensure entire map is visible
        const dynamicMinZoom = this.calculateMinZoom();
        
        // Use the minimum zoom needed to show the entire map
        // If dynamicMinZoom > 1.0, it means at 100% zoom the map doesn't fit entirely
        this.zoom = dynamicMinZoom;
        
        console.log('🎯 Initial Zoom Setup:', {
            defaultZoom: PixelWarConfig.canvas.defaultZoom,
            calculatedMinZoom: dynamicMinZoom.toFixed(3),
            selectedZoom: this.zoom.toFixed(3),
            reason: this.zoom === dynamicMinZoom ? 'Using calculated min zoom to fit map' : 'Using default zoom'
        });
        
        // Initialize with proper centering for optimal display
        const { width: effectiveWidth, height: effectiveHeight } = this.getEffectiveViewport();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        const viewportWidthInGrid = effectiveWidth / (pixelSize * this.zoom);
        const viewportHeightInGrid = effectiveHeight / (pixelSize * this.zoom);
        
        // Center the map if viewport is larger than the map
        if (viewportWidthInGrid >= this.config.width && viewportHeightInGrid >= this.config.height) {
            this.offsetX = -(viewportWidthInGrid - this.config.width) / 2;
            this.offsetY = -(viewportHeightInGrid - this.config.height) / 2;
        } else {
            // If map is larger than viewport, start at top-left
            this.offsetX = 0;
            this.offsetY = 0;
        }
        
        this.targetOffsetX = this.offsetX;
        this.targetOffsetY = this.offsetY;
        
        console.log('🎯 Initial Position: Centered at', `(${this.offsetX.toFixed(2)}, ${this.offsetY.toFixed(2)})`);
        
        this.updateZoomIndicator();
    }

    navigateToCorner(corner) {
        const { width: effectiveWidth, height: effectiveHeight } = this.getEffectiveViewport();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        const viewportWidthInGrid = effectiveWidth / (pixelSize * this.zoom);
        const viewportHeightInGrid = effectiveHeight / (pixelSize * this.zoom);

        let targetX, targetY;

        console.log('🧭 Navigation Debug:', {
            corner,
            viewportSize: `${viewportWidthInGrid.toFixed(1)}x${viewportHeightInGrid.toFixed(1)}`,
            mapSize: `${this.config.width}x${this.config.height}`,
            currentOffset: `${this.offsetX.toFixed(2)}, ${this.offsetY.toFixed(2)}`
        });

        const isMapSmallerThanView = viewportWidthInGrid >= this.config.width && viewportHeightInGrid >= this.config.height;

        if (isMapSmallerThanView) {
            // If map is smaller than the viewport, always center it
            targetX = -(viewportWidthInGrid - this.config.width) / 2;
            targetY = -(viewportHeightInGrid - this.config.height) / 2;
        } else {
            // If map is larger than the viewport, navigate to corners
            switch(corner) {
                case 'top-left':
                    targetX = 0;
                    targetY = 0;
                    break;
                case 'top-right':
                    targetX = -(this.config.width - viewportWidthInGrid);
                    targetY = 0;
                    break;
                case 'bottom-left':
                    targetX = 0;
                    targetY = -(this.config.height - viewportHeightInGrid);
                    break;
                case 'bottom-right':
                    targetX = -(this.config.width - viewportWidthInGrid);
                    targetY = -(this.config.height - viewportHeightInGrid);
                    break;
                case 'center':
                    targetX = -(this.config.width - viewportWidthInGrid) / 2;
                    targetY = -(this.config.height - viewportHeightInGrid) / 2;
                    break;
                default:
                    return;
            }
        }

        console.log('🎯 Navigation Target:', {
            calculatedTarget: `${targetX.toFixed(2)}, ${targetY.toFixed(2)}`
        });

        // Set navigation flag BEFORE setting targets to prevent constraints from interfering
        this.isIntentionalNavigation = true;
        
        // Set targets directly without immediate constraints
        this.targetOffsetX = targetX;
        this.targetOffsetY = targetY;

        // Use immediate mode for navigation - skip animation delays
        this.offsetX = targetX;
        this.offsetY = targetY;
        
        // Render immediately
        this.render();

        // Reset navigation flag after a reasonable delay
        setTimeout(() => {
            this.isIntentionalNavigation = false;
        }, 500); // Longer delay to ensure navigation completes

        this.notifications.show(`Navigating to ${corner === 'center' ? 'center' : corner + ' corner'}`, 'info', 2000);
    }

    resetView() {
        const dynamicMinZoom = this.calculateMinZoom();
        // Reset to show entire map
        this.zoom = dynamicMinZoom;
        this.offsetX = 0;
        this.offsetY = 0;
        this.targetOffsetX = 0;
        this.targetOffsetY = 0;
        this.constrainOffsets();
        this.offsetX = this.targetOffsetX;
        this.offsetY = this.targetOffsetY;
        this.updateZoomIndicator();
        this.render();
    }

    startAnimation() {
        if (!this.animationFrame) {
            this.animate();
        }
    }
    
    startZoomAnimation() {
        if (!this.zoomAnimationFrame && !this.animationFrame) {
            this.animateZoom();
        }
    }

    animate() {
        const now = performance.now();
        const deltaTime = now - (this.lastFrameTime || now);
        this.lastFrameTime = now;
        
        // Frame rate limiting
        const targetFrameTime = 1000 / PixelWarConfig.animation.maxFPS;
        if (deltaTime < targetFrameTime) {
            this.animationFrame = requestAnimationFrame(() => this.animate());
            return;
        }
        
        // Smooth interpolation with time-based animation - more responsive
        const smoothness = Math.min(0.3, deltaTime / 16); // Increased from 0.15 to 0.3 for faster response
        this.offsetX += (this.targetOffsetX - this.offsetX) * smoothness;
        this.offsetY += (this.targetOffsetY - this.offsetY) * smoothness;
        
        // Handle zoom animation if active
        if (this.targetZoom !== undefined && Math.abs(this.targetZoom - this.zoom) > 0.001) {
            const zoomSmoothness = 0.15; // Slower zoom animation for smoother experience
            this.zoom += (this.targetZoom - this.zoom) * zoomSmoothness;
            this.updateZoomIndicator();
        }
        
        this.render();
        
        // Continue if still moving or zooming
        const stillMoving = Math.abs(this.targetOffsetX - this.offsetX) > 0.01 ||
                           Math.abs(this.targetOffsetY - this.offsetY) > 0.01;
        const stillZooming = this.targetZoom !== undefined && Math.abs(this.targetZoom - this.zoom) > 0.001;
        
        if (stillMoving || stillZooming) {
            this.animationFrame = requestAnimationFrame(() => this.animate());
        } else {
            this.animationFrame = null;
            // Clean up zoom animation state
            if (this.targetZoom !== undefined && Math.abs(this.targetZoom - this.zoom) <= 0.001) {
                this.zoom = this.targetZoom;
                this.targetZoom = undefined;
                this.updateZoomIndicator();
            }
        }
    }

    applyMomentum() {
        const animate = () => {
            // Improved friction with better feel
            const friction = Math.max(0.85, PixelWarConfig.animation.friction);
            this.velocityX *= friction;
            this.velocityY *= friction;
            
            // Apply velocity to movement
            this.targetOffsetX += this.velocityX;
            this.targetOffsetY += this.velocityY;
            
            // Apply constraints with smooth boundaries
            this.constrainOffsets();
            
            // Smoother interpolation for momentum
            const smoothness = 0.3;
            this.offsetX += (this.targetOffsetX - this.offsetX) * smoothness;
            this.offsetY += (this.targetOffsetY - this.offsetY) * smoothness;
            
            this.render();
            
            // Continue momentum with improved threshold
            const threshold = Math.max(0.01, PixelWarConfig.animation.momentumThreshold);
            if (Math.abs(this.velocityX) > threshold || Math.abs(this.velocityY) > threshold) {
                this.animationFrame = requestAnimationFrame(animate);
            } else {
                this.animationFrame = null;
            }
        };
        
        this.animationFrame = requestAnimationFrame(animate);
    }

    render() {
        const showGrid = this.zoom > PixelWarConfig.canvas.gridThreshold;
        
        // Debug rendering values occasionally
        if (Math.random() < 0.001) { // Log 0.1% of renders to avoid spam
            console.log('🖼️ RENDER DEBUG:', {
                zoom: this.zoom.toFixed(3),
                offsetX: this.offsetX.toFixed(2),
                offsetY: this.offsetY.toFixed(2),
                targetOffsetX: this.targetOffsetX.toFixed(2),
                targetOffsetY: this.targetOffsetY.toFixed(2),
                canvasRect: `${this.canvas.getBoundingClientRect().width}x${this.canvas.getBoundingClientRect().height}`,
                mapSize: `${this.config.width}x${this.config.height}`
            });
        }
        
        // Sync view state with input handler for accurate coordinate conversion
        this.syncViewState();
        
        this.renderer.render(this.offsetX, this.offsetY, this.zoom, showGrid);
    }

    async placePixel(x, y) {
        if (!this.rateLimiter.canPlacePixel()) {
            const cooldownRemaining = this.rateLimiter.getCooldownRemaining();
            if (cooldownRemaining > 0) {
                this.notifications.show(`Wait ${Math.ceil(cooldownRemaining)}s before placing another pixel`, 'warning');
            } else {
                const timeLeft = Math.ceil(this.rateLimiter.getTimeUntilReset());
                this.notifications.show(`Rate limit reached. Reset in ${timeLeft}s`, 'warning');
            }
            return;
        }

        try {
            const response = await this.api.placePixel(x, y, this.selectedColor, this.config.id);
            
            if (response.success) {
                this.renderer.updatePixel(x, y, response.pixel.color, response.pixel.placed_by);
                this.rateLimiter.recordPlacement();
                this.rateLimiter.updateFromServer(response.cooldown_info);
                this.render();
                
                const remaining = this.rateLimiter.pixelsRemaining;
                this.notifications.show(`Pixel placed! (${remaining} remaining)`, 'success');
                this.updateUI();
            }
        } catch (error) {
            if (error.status === 429) {
                if (error.data?.limit_info) {
                    const info = error.data.limit_info;
                    this.notifications.show(
                        `Rate limit: ${info.placed_this_minute}/${info.max_per_minute} pixels used`,
                        'error'
                    );
                    this.rateLimiter.pixelsRemaining = 0;
                }
            } else {
                this.notifications.show(error.message || 'Failed to place pixel', 'error');
            }
        }
    }

    async loadCanvasState() {
        try {
            const response = await this.api.getCanvasState(this.config.id);
            
            if (response.success) {
                this.renderer.setPixels(response.pixels);
                this.render();
            }
        } catch (error) {
            console.error('Failed to load canvas state:', error);
            this.notifications.show('Failed to load canvas', 'error');
        }
    }

    async loadRecentActivity() {
        try {
            const response = await this.api.getPixelHistory(this.config.id);
            
            if (response.success) {
                this.displayActivity(response.history);
            }
        } catch (error) {
            console.error('Failed to load activity:', error);
        }
    }

    displayActivity(history) {
        const activityList = document.getElementById('activityList');
        if (!activityList) return;
        
        if (history.length === 0) {
            activityList.innerHTML = '<p>No activity yet</p>';
            return;
        }
        
        activityList.innerHTML = history.map(item => {
            const time = new Date(item.placed_at).toLocaleTimeString();
            return `
                <div class="activity-item">
                    <span class="activity-color" style="background-color: ${item.color}"></span>
                    <span class="activity-user">${item.placed_by}</span>
                    <span class="activity-coords">(${item.x}, ${item.y})</span>
                    <span class="activity-time">${time}</span>
                </div>
            `;
        }).join('');
    }

    updateUI() {
        const cooldownRemaining = this.rateLimiter.getCooldownRemaining();
        const pixelsRemaining = this.rateLimiter.pixelsRemaining;
        const timeUntilReset = this.rateLimiter.getTimeUntilReset();
        const canPlace = this.rateLimiter.canPlacePixel();
        
        // Update cooldown timer
        const timer = document.getElementById('cooldownTimer');
        if (timer) {
            if (cooldownRemaining > 0) {
                const seconds = Math.ceil(cooldownRemaining);
                timer.textContent = `⏱️ Next pixel in ${seconds}s`;
                timer.style.color = '#ff9800'; // Orange for waiting
                
                // Add progress bar if container exists
                const progressBar = timer.parentElement?.querySelector('.cooldown-progress');
                if (progressBar) {
                    const progress = ((this.rateLimiter.cooldownSeconds - cooldownRemaining) / this.rateLimiter.cooldownSeconds) * 100;
                    progressBar.style.width = `${progress}%`;
                }
            } else if (pixelsRemaining > 0) {
                timer.textContent = '✅ Ready to place pixel!';
                timer.style.color = '#4caf50'; // Green for ready
                
                const progressBar = timer.parentElement?.querySelector('.cooldown-progress');
                if (progressBar) {
                    progressBar.style.width = '100%';
                }
            } else {
                const minutes = Math.ceil(timeUntilReset / 60);
                const seconds = Math.ceil(timeUntilReset % 60);
                timer.textContent = `⏳ Limit reached - Reset in ${minutes > 0 ? minutes + 'm ' : ''}${seconds}s`;
                timer.style.color = '#ff6b6b'; // Red for limit reached
            }
        }
        
        // Update pixels remaining counter
        const remaining = document.getElementById('pixelsRemaining');
        if (remaining) {
            remaining.innerHTML = `
                <div class="pixels-info">
                    <div class="pixels-count">
                        <span class="current">${pixelsRemaining}</span>
                        <span class="separator">/</span>
                        <span class="max">${this.rateLimiter.maxPixelsPerMinute}</span>
                        <span class="label">pixels</span>
                    </div>
                    ${
                        timeUntilReset > 0 && pixelsRemaining < this.rateLimiter.maxPixelsPerMinute ?
                        `<div class="reset-timer">Reset in ${Math.ceil(timeUntilReset)}s</div>` :
                        ''
                    }
                </div>
            `;
            remaining.className = `pixels-remaining ${
                canPlace ? 'ready' : 
                cooldownRemaining > 0 ? 'cooldown' : 
                'limit-reached'
            }`;
        }
        
        // Update any place pixel button
        const placeButton = document.getElementById('placePixelBtn');
        if (placeButton) {
            placeButton.disabled = !canPlace;
            if (cooldownRemaining > 0) {
                placeButton.textContent = `Wait ${Math.ceil(cooldownRemaining)}s`;
            } else if (pixelsRemaining > 0) {
                placeButton.textContent = 'Place Pixel';
            } else {
                placeButton.textContent = 'Limit Reached';
            }
        }
    }

    highlightSelectedColor(element) {
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.classList.remove('selected');
        });
        element.classList.add('selected');
    }

    startUpdateLoop() {
        // Update UI more frequently for smooth countdown
        this.uiUpdateInterval = setInterval(() => {
            this.updateUI();
        }, 100); // Update every 100ms for smooth countdown
        
        // Update canvas state less frequently
        this.updateInterval = setInterval(async () => {
            await this.loadCanvasState();
            await this.loadRecentActivity();
            this.rateLimiter.checkReset();
        }, PixelWarConfig.api.updateInterval);
    }
    
    setupTouchModeToggle() {
        // Comprehensive mobile/touch device detection
        const forceShow = localStorage.getItem('forceMobileMode') === 'true';
        const isTouchDevice = 'ontouchstart' in window || 
                            navigator.maxTouchPoints > 0 ||
                            /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) ||
                            window.matchMedia('(max-width: 768px)').matches ||
                            forceShow;
        
        console.log('🔍 Device Detection:', {
            ontouchstart: 'ontouchstart' in window,
            maxTouchPoints: navigator.maxTouchPoints,
            userAgent: navigator.userAgent,
            screenWidth: window.screen.width,
            isMobile: window.matchMedia('(max-width: 768px)').matches,
            forceShow: forceShow,
            isTouchDevice: isTouchDevice
        });
        
        if (!isTouchDevice) {
            console.log('❌ Touch mode toggle hidden - not a touch device');
            return; // Only show on touch devices
        }
        
        console.log('✅ Touch mode toggle enabled - touch device detected');
        
        // Add diagnostic function to window for debugging
        window.debugTouch = () => {
            console.log('📱 Touch Debug Info:', {
                isDragging: this.isDragging,
                touchMode: this.touchMode,
                activeTouches: this.touches.size,
                animationFrame: !!this.animationFrame,
                longPressTimer: !!this.longPressTimer
            });
        };
        
        // Add comprehensive centering debug function
        window.debugCentering = () => {
            const { width: effectiveWidth, height: effectiveHeight, isMobile } = this.getEffectiveViewport();
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            
            const viewportWidth = effectiveWidth / (pixelSize * this.zoom);
            const viewportHeight = effectiveHeight / (pixelSize * this.zoom);
            
            const canFitWidth = viewportWidth >= this.config.width;
            const canFitHeight = viewportHeight >= this.config.height;
            
            const expectedOffsetX = canFitWidth ? -(viewportWidth - this.config.width) / 2 : this.targetOffsetX;
            const expectedOffsetY = canFitHeight ? -(viewportHeight - this.config.height) / 2 : this.targetOffsetY;
            
            console.log('🎯 COMPREHENSIVE CENTERING DEBUG:', {
                viewport: {
                    effective: `${effectiveWidth}x${effectiveHeight}`,
                    inGridUnits: `${viewportWidth.toFixed(2)}x${viewportHeight.toFixed(2)}`,
                    canFit: `width: ${canFitWidth}, height: ${canFitHeight}`
                },
                map: {
                    size: `${this.config.width}x${this.config.height}`,
                    pixelSize: pixelSize
                },
                zoom: {
                    current: this.zoom.toFixed(3),
                    minCalculated: this.calculateMinZoom().toFixed(3)
                },
                offsets: {
                    current: `${this.offsetX.toFixed(2)}, ${this.offsetY.toFixed(2)}`,
                    target: `${this.targetOffsetX.toFixed(2)}, ${this.targetOffsetY.toFixed(2)}`,
                    expected: `${expectedOffsetX.toFixed(2)}, ${expectedOffsetY.toFixed(2)}`,
                    matches: `X: ${Math.abs(this.targetOffsetX - expectedOffsetX) < 0.1}, Y: ${Math.abs(this.targetOffsetY - expectedOffsetY) < 0.1}`
                },
                rendering: {
                    viewportX: (-this.offsetX).toFixed(2),
                    viewportY: (-this.offsetY).toFixed(2),
                    translation: `${(this.offsetX * pixelSize).toFixed(1)}, ${(this.offsetY * pixelSize).toFixed(1)}`
                }
            });
            
            // Test the centering calculation directly
            console.log('🧮 MANUAL CENTERING TEST:');
            if (canFitWidth) {
                const testOffsetX = -(viewportWidth - this.config.width) / 2;
                console.log(`Width centering: viewport=${viewportWidth.toFixed(2)}, grid=${this.config.width}, offset=${testOffsetX.toFixed(2)}`);
            }
            if (canFitHeight) {
                const testOffsetY = -(viewportHeight - this.config.height) / 2;
                console.log(`Height centering: viewport=${viewportHeight.toFixed(2)}, grid=${this.config.height}, offset=${testOffsetY.toFixed(2)}`);
            }
        };
        
        // Add full debug function to understand the issue
        window.fullDebug = () => {
            console.log('🔍 FULL MAP DEBUG:');
            const { width: effectiveWidth, height: effectiveHeight, isMobile } = this.getEffectiveViewport();
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            
            const viewportWidth = effectiveWidth / (pixelSize * this.zoom);
            const viewportHeight = effectiveHeight / (pixelSize * this.zoom);
            
            const dynamicMinZoom = this.calculateMinZoom();
            
            console.log('📐 Viewport Analysis:', {
                canvas: {
                    rect: `${this.canvas.getBoundingClientRect().width}x${this.canvas.getBoundingClientRect().height}`,
                    internal: `${this.canvas.width}x${this.canvas.height}`
                },
                effective: `${effectiveWidth}x${effectiveHeight}`,
                map: `${this.config.width}x${this.config.height}`,
                zoom: {
                    current: this.zoom,
                    calculated: dynamicMinZoom,
                    percentage: Math.round(this.zoom * 100) + '%'
                },
                viewport: {
                    inGridUnits: `${viewportWidth.toFixed(2)}x${viewportHeight.toFixed(2)}`,
                    shouldFitMap: {
                        width: viewportWidth >= this.config.width,
                        height: viewportHeight >= this.config.height
                    }
                },
                offsets: {
                    current: `${this.offsetX.toFixed(2)}, ${this.offsetY.toFixed(2)}`,
                    target: `${this.targetOffsetX.toFixed(2)}, ${this.targetOffsetY.toFixed(2)}`
                }
            });
            
            // Check if viewport can actually fit the map
            if (viewportWidth < this.config.width) {
                console.log('❌ WIDTH PROBLEM: Viewport width ' + viewportWidth.toFixed(2) + ' < map width ' + this.config.width);
                console.log('   Need zoom <= ' + (effectiveWidth / (this.config.width * pixelSize)).toFixed(3));
            }
            if (viewportHeight < this.config.height) {
                console.log('❌ HEIGHT PROBLEM: Viewport height ' + viewportHeight.toFixed(2) + ' < map height ' + this.config.height);
                console.log('   Need zoom <= ' + (effectiveHeight / (this.config.height * pixelSize)).toFixed(3));
            }
        };
        
        // Force correct canvas size
        window.fixCanvasSize = () => {
            console.log('🔧 FIXING CANVAS SIZE');
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            const correctWidth = this.config.width * pixelSize; // Should be 1000
            const correctHeight = this.config.height * pixelSize; // Should be 1000
            
            console.log('Before fix:', {
                canvasRect: `${this.canvas.getBoundingClientRect().width}x${this.canvas.getBoundingClientRect().height}`,
                canvasInternal: `${this.canvas.width}x${this.canvas.height}`,
                shouldBe: `${correctWidth}x${correctHeight}`
            });
            
            // Force correct canvas dimensions
            this.canvas.width = correctWidth;
            this.canvas.height = correctHeight;
            this.canvas.style.width = correctWidth + 'px';
            this.canvas.style.height = correctHeight + 'px';
            
            // Also fix renderer canvas
            this.renderer.canvas.width = correctWidth;
            this.renderer.canvas.height = correctHeight;
            this.renderer.offscreenCanvas.width = correctWidth;
            this.renderer.offscreenCanvas.height = correctHeight;
            
            console.log('After fix:', {
                canvasRect: `${this.canvas.getBoundingClientRect().width}x${this.canvas.getBoundingClientRect().height}`,
                canvasInternal: `${this.canvas.width}x${this.canvas.height}`
            });
            
            // Recalculate zoom and center
            this.initializeZoom();
            this.render();
            console.log('✅ Canvas size fixed and recentered');
        };
        
        // Add manual centering fix function
        window.fixCentering = () => {
            console.log('🔧 MANUAL CENTERING FIX');
            const { width: effectiveWidth, height: effectiveHeight } = this.getEffectiveViewport();
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            
            const viewportWidth = effectiveWidth / (pixelSize * this.zoom);
            const viewportHeight = effectiveHeight / (pixelSize * this.zoom);
            
            // Force correct centering calculation
            if (viewportWidth >= this.config.width) {
                this.targetOffsetX = -(viewportWidth - this.config.width) / 2;
                this.offsetX = this.targetOffsetX;
                console.log(`✅ Width centered: offset=${this.offsetX.toFixed(2)}`);
            }
            
            if (viewportHeight >= this.config.height) {
                this.targetOffsetY = -(viewportHeight - this.config.height) / 2;
                this.offsetY = this.targetOffsetY;
                console.log(`✅ Height centered: offset=${this.offsetY.toFixed(2)}`);
            }
            
            this.render();
            console.log('🎯 Manual centering applied');
        };
        
        // Add zoom debug function
        window.debugZoom = () => {
            const rect = this.canvas.getBoundingClientRect();
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            const viewportWidth = rect.width / (pixelSize * this.zoom);
            const viewportHeight = rect.height / (pixelSize * this.zoom);
            
            console.log('🔍 FULL ZOOM DEBUG:', {
                canvasRect: `${rect.width}x${rect.height}`,
                zoom: this.zoom,
                pixelSize: pixelSize,
                mapSize: `${this.config.width}x${this.config.height}`,
                viewportInGridUnits: `${viewportWidth.toFixed(1)}x${viewportHeight.toFixed(1)}`,
                canFitMap: `width: ${viewportWidth >= this.config.width}, height: ${viewportHeight >= this.config.height}`,
                currentOffsets: `${this.offsetX.toFixed(2)}, ${this.offsetY.toFixed(2)}`,
                targetOffsets: `${this.targetOffsetX.toFixed(2)}, ${this.targetOffsetY.toFixed(2)}`,
                calculatedMinZoom: this.calculateMinZoom(),
                expectedCenterOffsets: {
                    x: viewportWidth >= this.config.width ? (-(viewportWidth - this.config.width) / 2).toFixed(2) : 'N/A',
                    y: viewportHeight >= this.config.height ? (-(viewportHeight - this.config.height) / 2).toFixed(2) : 'N/A'
                }
            });
            
            // Force recalculate constraints
            this.constrainOffsets();
            console.log('After constrainOffsets:', {
                newTargetOffsets: `${this.targetOffsetX.toFixed(2)}, ${this.targetOffsetY.toFixed(2)}`
            });
        };
        
        // Add zoom to fit function for testing
        window.zoomToFit = () => {
            console.log('🎯 ZOOM TO FIT TEST');
            const dynamicMinZoom = this.calculateMinZoom();
            this.zoom = dynamicMinZoom;
            
            console.log('📐 Before constrainOffsets:', {
                zoom: this.zoom,
                offsetX: this.offsetX,
                offsetY: this.offsetY
            });
            
            this.offsetX = 0;
            this.offsetY = 0;
            this.targetOffsetX = 0;
            this.targetOffsetY = 0;
            this.constrainOffsets();
            
            console.log('📐 After constrainOffsets:', {
                targetOffsetX: this.targetOffsetX,
                targetOffsetY: this.targetOffsetY
            });
            
            this.offsetX = this.targetOffsetX;
            this.offsetY = this.targetOffsetY;
            this.updateZoomIndicator();
            this.render();
            console.log('✅ Zoom set to fit entire map, offsets applied');
        };
        
        // Add force center function for testing
        window.forceCenter = () => {
            const rect = this.canvas.getBoundingClientRect();
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            const viewportWidth = rect.width / (pixelSize * this.zoom);
            const viewportHeight = rect.height / (pixelSize * this.zoom);
            
            // Manually calculate and apply center offsets
            if (viewportWidth >= this.config.width) {
                this.targetOffsetX = -(viewportWidth - this.config.width) / 2;
                this.offsetX = this.targetOffsetX;
            }
            if (viewportHeight >= this.config.height) {
                this.targetOffsetY = -(viewportHeight - this.config.height) / 2;
                this.offsetY = this.targetOffsetY;
            }
            
            console.log('🎯 FORCE CENTERED:', {
                appliedOffsets: `${this.offsetX.toFixed(2)}, ${this.offsetY.toFixed(2)}`,
                viewportSize: `${viewportWidth.toFixed(1)}x${viewportHeight.toFixed(1)}`
            });
            
            this.render();
        };
        
        // Create touch mode toggle button
        const toggleContainer = document.createElement('div');
        toggleContainer.className = 'touch-mode-toggle';
        toggleContainer.innerHTML = `
            <button id="touchModeBtn" class="touch-mode-btn">
                <span class="mode-icon">👆</span>
                <span class="mode-text">Tap Mode</span>
            </button>
        `;
        
        // Add to canvas controls area
        const canvasSection = document.querySelector('.canvas-section') || document.querySelector('.pixel-war-container') || document.body;
        canvasSection.appendChild(toggleContainer);
        
        // Set up event listener
        const btn = document.getElementById('touchModeBtn');
        if (btn) {
            btn.addEventListener('click', () => {
                this.inputHandler.touchMode = this.inputHandler.touchMode === 'tap' ? 'precision' : 'tap';
                localStorage.setItem('pixelWarTouchMode', this.inputHandler.touchMode);
                this.updateTouchModeButton();
            });
        }
        
        this.updateTouchModeButton();
    }
    
    updateTouchModeButton() {
        const btn = document.getElementById('touchModeBtn');
        if (btn) {
            const icon = btn.querySelector('.mode-icon');
            const text = btn.querySelector('.mode-text');
            
            if (this.inputHandler.touchMode === 'precision') {
                icon.textContent = '🎯';
                text.textContent = 'Precision Mode';
                btn.classList.add('precision');
                btn.style.backgroundColor = '#ff9800';
                btn.style.color = 'white';
            } else {
                icon.textContent = '👆';
                text.textContent = 'Tap Mode';
                btn.classList.remove('precision');
                btn.style.backgroundColor = '#4caf50';
                btn.style.color = 'white';
            }
        }
    }

    destroy() {
        // Clean up
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
        
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
        }
        
        if (this.uiUpdateInterval) {
            clearInterval(this.uiUpdateInterval);
        }
        
        this.inputHandler.destroy();
        this.isRunning = false;
    }
}