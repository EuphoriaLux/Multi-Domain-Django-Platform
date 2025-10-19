/**
 * Pixel War - Refactored and Modular Version
 * A collaborative pixel canvas application
 */

// Configuration module
const PixelWarConfig = {
    canvas: {
        defaultPixelSize: 10,
        minZoom: 0.5,
        maxZoom: 5,
        defaultZoom: 1,
        gridThreshold: 0.7  // Show grid when zoom > this value
    },
    animation: {
        friction: 0.92,
        smoothness: 0.15,
        momentumThreshold: 0.5,
        maxFPS: 60,
        throttleMs: 16  // ~60fps throttling
    },
    api: {
        updateInterval: 2000,
        retryAttempts: 3,
        endpoints: {
            placePixel: '/vibe-coding/api/place-pixel/',
            canvasState: '/vibe-coding/api/canvas-state/',
            pixelHistory: '/vibe-coding/api/pixel-history/'
        }
    },
    notifications: {
        duration: 3000,
        types: {
            SUCCESS: 'success',
            ERROR: 'error',
            INFO: 'info',
            WARNING: 'warning'
        }
    }
};

// API Client module
class PixelWarAPI {
    constructor(baseUrl = '') {
        this.baseUrl = this.normalizeBaseUrl(baseUrl);
        this.csrfToken = this.getCookie('csrftoken');
    }

    normalizeBaseUrl(url) {
        // Fix: Properly handle language prefixes
        const path = window.location.pathname;
        const langPattern = /^\/[a-z]{2}\//;
        
        if (langPattern.test(path)) {
            // Remove language prefix for API calls
            return url || '';
        }
        return url || '';
    }

    getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) {
            return parts.pop().split(';').shift();
        }
        return null;
    }

    async request(url, options = {}) {
        const defaultOptions = {
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.csrfToken
            }
        };

        const mergedOptions = {
            ...defaultOptions,
            ...options,
            headers: {
                ...defaultOptions.headers,
                ...options.headers
            }
        };

        try {
            const response = await fetch(this.baseUrl + url, mergedOptions);
            const data = await response.json();
            
            if (!response.ok) {
                throw new APIError(data.error || 'Request failed', response.status, data);
            }
            
            return data;
        } catch (error) {
            if (error instanceof APIError) {
                throw error;
            }
            throw new APIError('Network error', 0, null);
        }
    }

    async placePixel(x, y, color, canvasId) {
        return this.request(PixelWarConfig.api.endpoints.placePixel, {
            method: 'POST',
            body: JSON.stringify({ x, y, color, canvas_id: canvasId })
        });
    }

    async getCanvasState(canvasId) {
        return this.request(`${PixelWarConfig.api.endpoints.canvasState}${canvasId}/`);
    }

    async getPixelHistory(canvasId, limit = 20) {
        return this.request(`${PixelWarConfig.api.endpoints.pixelHistory}?canvas_id=${canvasId}&limit=${limit}`);
    }
}

// Custom error class
class APIError extends Error {
    constructor(message, status, data) {
        super(message);
        this.status = status;
        this.data = data;
    }
}

// Canvas Renderer module
class CanvasRenderer {
    constructor(canvas, config) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.config = config;
        this.pixels = {};
        this.dirtyRegions = new Set();
        
        // Offscreen canvas for double buffering
        this.offscreenCanvas = document.createElement('canvas');
        this.offscreenCtx = this.offscreenCanvas.getContext('2d');
    }

    setup(width, height, pixelSize) {
        this.width = width;
        this.height = height;
        this.pixelSize = pixelSize;
        
        this.canvas.width = width * pixelSize;
        this.canvas.height = height * pixelSize;
        this.offscreenCanvas.width = this.canvas.width;
        this.offscreenCanvas.height = this.canvas.height;
    }

    setPixels(pixels) {
        this.pixels = pixels;
        this.markAllDirty();
    }

    updatePixel(x, y, color, placedBy) {
        const key = `${x},${y}`;
        this.pixels[key] = { color, placed_by: placedBy };
        this.markDirty(x, y);
    }

    markDirty(x, y) {
        this.dirtyRegions.add(`${x},${y}`);
    }

    markAllDirty() {
        this.dirtyRegions.clear();
        this.dirtyRegions.add('all');
    }

    render(offsetX, offsetY, zoom, showGrid) {
        // Clear canvas first
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        // Calculate viewport in grid coordinates (pixels in the grid)
        const viewportX = -offsetX;
        const viewportY = -offsetY;
        const viewportWidth = this.canvas.width / (zoom * this.pixelSize);
        const viewportHeight = this.canvas.height / (zoom * this.pixelSize);
        const margin = Math.max(10, Math.ceil(50 / zoom)); // Dynamic margin based on zoom

        // Setup transform
        this.ctx.save();
        this.ctx.scale(zoom, zoom);
        this.ctx.translate(offsetX * this.pixelSize, offsetY * this.pixelSize);

        // Draw the outside/void area background (before pixel map area)
        this.drawVoidBackground(viewportX, viewportY, viewportWidth, viewportHeight, zoom);

        // Draw pixel map background - only the actual canvas area
        this.ctx.fillStyle = '#ffffff';
        const bgStartX = Math.max(0, Math.floor(viewportX - margin));
        const bgStartY = Math.max(0, Math.floor(viewportY - margin));
        const bgEndX = Math.min(this.width, Math.ceil(viewportX + viewportWidth + margin));
        const bgEndY = Math.min(this.height, Math.ceil(viewportY + viewportHeight + margin));
        
        const bgX = bgStartX * this.pixelSize;
        const bgY = bgStartY * this.pixelSize;
        const bgWidth = (bgEndX - bgStartX) * this.pixelSize;
        const bgHeight = (bgEndY - bgStartY) * this.pixelSize;
        
        this.ctx.fillRect(bgX, bgY, bgWidth, bgHeight);
        
        // Draw pixel map border
        this.drawPixelMapBorder(zoom);

        // Calculate pixel range to render
        const startX = Math.max(0, Math.floor(viewportX - margin));
        const endX = Math.min(this.width, Math.ceil(viewportX + viewportWidth + margin));
        const startY = Math.max(0, Math.floor(viewportY - margin));
        const endY = Math.min(this.height, Math.ceil(viewportY + viewportHeight + margin));

        // Batch pixel rendering for better performance
        this.ctx.beginPath();
        for (let x = startX; x < endX; x++) {
            for (let y = startY; y < endY; y++) {
                const key = `${x},${y}`;
                if (this.pixels[key]) {
                    this.ctx.fillStyle = this.pixels[key].color;
                    this.ctx.fillRect(
                        x * this.pixelSize,
                        y * this.pixelSize,
                        this.pixelSize,
                        this.pixelSize
                    );
                }
            }
        }

        // Draw grid if needed - only visible portion
        if (showGrid && zoom > PixelWarConfig.canvas.gridThreshold) {
            this.drawOptimizedGrid(startX, endX, startY, endY, zoom);
        }

        this.ctx.restore();
        this.dirtyRegions.clear();
    }

    drawOptimizedGrid(startX, endX, startY, endY, zoom = 1) {
        // Adjust grid opacity and line width based on zoom level
        const opacity = Math.min(0.5, Math.max(0.1, (zoom - 0.5) * 0.4));
        const lineWidth = Math.max(0.5, Math.min(2, zoom * 0.5));
        
        this.ctx.strokeStyle = `rgba(200, 200, 200, ${opacity})`;
        this.ctx.lineWidth = lineWidth;

        // Draw vertical lines - only visible portion
        this.ctx.beginPath();
        for (let x = startX; x <= endX; x++) {
            const lineX = x * this.pixelSize;
            this.ctx.moveTo(lineX, startY * this.pixelSize);
            this.ctx.lineTo(lineX, endY * this.pixelSize);
        }
        this.ctx.stroke();

        // Draw horizontal lines - only visible portion
        this.ctx.beginPath();
        for (let y = startY; y <= endY; y++) {
            const lineY = y * this.pixelSize;
            this.ctx.moveTo(startX * this.pixelSize, lineY);
            this.ctx.lineTo(endX * this.pixelSize, lineY);
        }
        this.ctx.stroke();
    }

    // Keep old method for compatibility
    drawGrid() {
        this.drawOptimizedGrid(0, this.width, 0, this.height);
    }

    drawVoidBackground(viewportX, viewportY, viewportWidth, viewportHeight, zoom) {
        // Draw a distinct pattern/color for the area outside the pixel map
        const voidAreas = this.calculateVoidAreas(viewportX, viewportY, viewportWidth, viewportHeight);
        
        // Create a subtle checkered pattern for the void area
        const patternSize = Math.max(20, 50 / zoom); // Scale pattern with zoom
        
        this.ctx.fillStyle = '#f0f0f0'; // Light gray base
        
        voidAreas.forEach(area => {
            // Fill base color
            this.ctx.fillRect(area.x * this.pixelSize, area.y * this.pixelSize, 
                             area.width * this.pixelSize, area.height * this.pixelSize);
            
            // Add diagonal stripes pattern
            this.ctx.save();
            this.ctx.fillStyle = '#e8e8e8';
            this.ctx.beginPath();
            
            // Draw diagonal stripes
            for (let x = area.x * this.pixelSize - patternSize; x < (area.x + area.width) * this.pixelSize + patternSize; x += patternSize * 2) {
                for (let y = area.y * this.pixelSize - patternSize; y < (area.y + area.height) * this.pixelSize + patternSize; y += patternSize * 2) {
                    this.ctx.rect(x, y, patternSize, patternSize);
                }
            }
            this.ctx.fill();
            this.ctx.restore();
        });
    }

    calculateVoidAreas(viewportX, viewportY, viewportWidth, viewportHeight) {
        const areas = [];
        
        // Calculate which parts of the viewport are outside the pixel map (0,0 to width,height)
        const viewportRight = viewportX + viewportWidth;
        const viewportBottom = viewportY + viewportHeight;
        
        // Left void area (viewport extends beyond left edge of map)
        if (viewportX < 0) {
            areas.push({
                x: viewportX,
                y: Math.max(viewportY, 0),
                width: Math.min(-viewportX, viewportWidth),
                height: Math.min(viewportHeight, Math.max(0, Math.min(this.height, viewportBottom) - Math.max(0, viewportY)))
            });
        }
        
        // Right void area (viewport extends beyond right edge of map)
        if (viewportRight > this.width) {
            areas.push({
                x: this.width,
                y: Math.max(viewportY, 0),
                width: viewportRight - this.width,
                height: Math.min(viewportHeight, Math.max(0, Math.min(this.height, viewportBottom) - Math.max(0, viewportY)))
            });
        }
        
        // Top void area (viewport extends beyond top edge of map)
        if (viewportY < 0) {
            areas.push({
                x: viewportX,
                y: viewportY,
                width: viewportWidth,
                height: -viewportY
            });
        }
        
        // Bottom void area (viewport extends beyond bottom edge of map)
        if (viewportBottom > this.height) {
            areas.push({
                x: viewportX,
                y: this.height,
                width: viewportWidth,
                height: viewportBottom - this.height
            });
        }
        
        return areas;
    }

    drawPixelMapBorder(zoom) {
        // Draw a clear border around the pixel map
        this.ctx.strokeStyle = '#333333';
        this.ctx.lineWidth = Math.max(2, 4 / zoom); // Scale border with zoom but keep visible
        this.ctx.setLineDash([]);
        
        // Draw border rectangle around the entire pixel map
        this.ctx.strokeRect(0, 0, this.width * this.pixelSize, this.height * this.pixelSize);
        
        // Add a subtle inner shadow effect
        this.ctx.strokeStyle = 'rgba(0, 0, 0, 0.1)';
        this.ctx.lineWidth = Math.max(1, 2 / zoom);
        this.ctx.strokeRect(-this.ctx.lineWidth, -this.ctx.lineWidth, 
                           (this.width * this.pixelSize) + (this.ctx.lineWidth * 2), 
                           (this.height * this.pixelSize) + (this.ctx.lineWidth * 2));
    }

    drawPixelPreview(x, y, color, zoom, offsetX, offsetY) {
        const pixelX = (x + offsetX) * this.pixelSize * zoom;
        const pixelY = (y + offsetY) * this.pixelSize * zoom;
        const size = this.pixelSize * zoom;

        this.ctx.save();
        this.ctx.fillStyle = color;
        this.ctx.globalAlpha = 0.6;
        this.ctx.fillRect(pixelX, pixelY, size, size);
        
        this.ctx.strokeStyle = color;
        this.ctx.lineWidth = 2;
        this.ctx.globalAlpha = 1;
        this.ctx.strokeRect(pixelX - 1, pixelY - 1, size + 2, size + 2);
        this.ctx.restore();
    }
}

// Input Handler module
class InputHandler extends EventTarget {
    constructor(canvas, config) {
        super();
        this.canvas = canvas;
        this.config = config;
        this.setupEventListeners();
        
        // State
        this.isDragging = false;
        this.dragStartX = 0;
        this.dragStartY = 0;
        this.lastMouseX = 0;
        this.lastMouseY = 0;
        this.spacePressed = false;
        
        // Touch state
        this.touches = new Map();
        this.lastTouchDistance = 0;
        this.touchMode = localStorage.getItem('pixelWarTouchMode') || 'tap';
        this.longPressTimer = null;
        this.isDragging = false;
        this.touchStartTime = 0;
        this.selectedPixelX = null;
        this.selectedPixelY = null;
        
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
        const isPanMode = this.spacePressed || e.button === 2 || e.button === 1;
        
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
        
        if (this.isDragging) {
            this.updateDrag(e.clientX, e.clientY);
        } else {
            this.dispatchEvent(new CustomEvent('hover', {
                detail: { x: e.clientX, y: e.clientY }
            }));
        }
    }

    handleMouseUp(e) {
        if (this.isDragging) {
            this.endDrag();
        } else if (e.button === 0 && !this.spacePressed) {
            this.dispatchEvent(new CustomEvent('click', {
                detail: { x: e.clientX, y: e.clientY }
            }));
        }
    }

    handleMouseLeave(e) {
        if (this.isDragging) {
            this.endDrag();
        }
        this.dispatchEvent(new CustomEvent('leave'));
    }

    handleWheel(e) {
        e.preventDefault();
        
        if (e.ctrlKey || e.metaKey) {
            // Zoom
            const delta = e.deltaY > 0 ? -0.1 : 0.1;
            this.dispatchEvent(new CustomEvent('zoom', {
                detail: { delta, x: e.clientX, y: e.clientY }
            }));
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
            this.isDragging = false;
            this.touchStartTime = Date.now();
            
            // Clear any existing touch data
            this.touches.clear();
            
            this.touches.set(touch.identifier, {
                startX: touch.clientX,
                startY: touch.clientY,
                currentX: touch.clientX,
                currentY: touch.clientY,
                startTime: Date.now(),
                lastMoveTime: Date.now()
            });
            
            // Start long press timer for precision mode
            if (this.touchMode === 'precision') {
                this.clearLongPressTimer();
                this.longPressTimer = setTimeout(() => {
                    if (!this.isDragging) {
                        this.handleLongPress(touch.clientX, touch.clientY);
                    }
                }, 400); // 400ms for long press
            }
        } else if (e.touches.length === 2) {
            // Multi-touch: clear single touch and start pinch
            this.clearLongPressTimer();
            this.isDragging = false;
            this.touches.clear();
            this.lastTouchDistance = this.getTouchDistance(e.touches[0], e.touches[1]);
        }
    }

    handleTouchMove(e) {
        e.preventDefault();
        
        // Minimal throttling for maximum responsiveness on mobile
        const now = performance.now();
        if (now - this.lastEventTime < 2) { // Aggressive reduction to 2ms for immediate response
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
                
                // Start dragging if moved more than 3px (ultra sensitive threshold)
                if (totalMoveDistance > 3 && !this.isDragging) {
                    this.isDragging = true;
                    this.clearLongPressTimer();
                }
                
                // Send drag events immediately once dragging started (no minimum movement)
                if (this.isDragging && (Math.abs(deltaX) > 0.1 || Math.abs(deltaY) > 0.1)) {
                    // Enhanced mobile sensitivity
                    this.dispatchEvent(new CustomEvent('touchdrag', {
                        detail: { 
                            deltaX: deltaX * 2.5, // Maximum sensitivity for immediate response
                            deltaY: deltaY * 2.5,
                            isMobile: true,
                            totalDistance: totalMoveDistance
                        }
                    }));
                    
                    // Debug logging for touch issues
                    if (Math.random() < 0.01) { // Only log 1% of events to avoid spam
                        console.log('📱 Touch Drag:', {
                            deltaX: deltaX.toFixed(2),
                            deltaY: deltaY.toFixed(2),
                            enhanced: (deltaX * 1.8).toFixed(2) + ',' + (deltaY * 1.8).toFixed(2),
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
                    isDragging: this.isDragging,
                    touchMode: this.touchMode
                });
                
                // Quick tap detection (not a drag) - more lenient thresholds
                if (duration < 500 && distance < 15 && !this.isDragging) {
                    if (this.touchMode === 'tap') {
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
                } else if (this.isDragging) {
                    console.log('🖐️ Drag gesture completed');
                } else {
                    console.log('❓ Touch gesture not recognized:', { duration, distance, isDragging: this.isDragging });
                }
                
                this.touches.delete(touch.identifier);
            }
        }
        
        // Reset dragging state
        this.isDragging = false;
        
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
    
    clearLongPressTimer() {
        if (this.longPressTimer) {
            clearTimeout(this.longPressTimer);
            this.longPressTimer = null;
        }
    }
    
    handleLongPress(clientX, clientY) {
        // Enhanced long press for precision mode
        const rect = this.canvas.getBoundingClientRect();
        const canvasX = clientX - rect.left;
        const canvasY = clientY - rect.top;
        
        // Use the same coordinate calculation as the main app
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        const x = Math.floor((canvasX / (pixelSize * this.zoom)) + (-this.offsetX));
        const y = Math.floor((canvasY / (pixelSize * this.zoom)) + (-this.offsetY));
        
        console.log('🔥 Long press coordinates:', {x, y});
        
        if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
            this.showPixelPreview(x, y, true);
            
            // Haptic feedback for long press
            if (navigator.vibrate) {
                navigator.vibrate([50, 30, 50]); // Double buzz pattern
            }
        }
    }
    
    handlePixelPreview(clientX, clientY) {
        const rect = this.canvas.getBoundingClientRect();
        const canvasX = clientX - rect.left;
        const canvasY = clientY - rect.top;
        
        // Use the same coordinate calculation as the main app
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        const x = Math.floor((canvasX / (pixelSize * this.zoom)) + (-this.offsetX));
        const y = Math.floor((canvasY / (pixelSize * this.zoom)) + (-this.offsetY));
        
        console.log('🎯 Pixel preview coordinates:', {
            clientX, clientY, 
            canvasX, canvasY,
            pixelSize, zoom: this.zoom,
            offsetX: this.offsetX, offsetY: this.offsetY,
            calculatedX: x, calculatedY: y,
            canvasSize: `${this.config.width}x${this.config.height}`
        });
        
        if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
            this.showPixelPreview(x, y, false);
        } else {
            console.log('❌ Coordinates out of bounds:', {x, y});
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
        
        this.dispatchEvent(new CustomEvent('keydown', { detail: { key: e.key } }));
    }

    handleKeyUp(e) {
        if (e.code === 'Space') {
            this.spacePressed = false;
            this.canvas.style.cursor = 'crosshair';
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

// Rate Limiter module
class RateLimiter {
    constructor(maxPixelsPerMinute, cooldownSeconds) {
        this.maxPixelsPerMinute = maxPixelsPerMinute;
        this.cooldownSeconds = cooldownSeconds;
        this.pixelsRemaining = maxPixelsPerMinute;
        this.lastResetTime = Date.now();
        this.lastPlacementTime = 0;
        this.cooldownActive = false;
    }

    canPlace() {
        this.checkReset();
        return this.pixelsRemaining > 0;
    }

    recordPlacement() {
        this.checkReset();
        if (this.pixelsRemaining > 0) {
            this.pixelsRemaining--;
            this.lastPlacementTime = Date.now();
            this.cooldownActive = true;
            return true;
        }
        return false;
    }

    checkReset() {
        const now = Date.now();
        const timeSinceReset = (now - this.lastResetTime) / 1000;
        
        if (timeSinceReset >= 60) {
            this.pixelsRemaining = this.maxPixelsPerMinute;
            this.lastResetTime = now;
        }
    }

    getTimeUntilReset() {
        const now = Date.now();
        const timeSinceReset = (now - this.lastResetTime) / 1000;
        return Math.max(0, 60 - timeSinceReset);
    }

    updateFromServer(cooldownInfo) {
        if (cooldownInfo) {
            this.pixelsRemaining = cooldownInfo.pixels_remaining;
            // Update server-side cooldown status
            if (cooldownInfo.cooldown_remaining > 0) {
                this.cooldownActive = true;
                this.lastPlacementTime = Date.now() - ((this.cooldownSeconds - cooldownInfo.cooldown_remaining) * 1000);
            }
        }
    }

    // Get remaining cooldown time in seconds
    getCooldownRemaining() {
        if (!this.cooldownActive) return 0;
        
        const now = Date.now();
        const timeSincePlacement = (now - this.lastPlacementTime) / 1000;
        const remaining = this.cooldownSeconds - timeSincePlacement;
        
        if (remaining <= 0) {
            this.cooldownActive = false;
            return 0;
        }
        
        return remaining;
    }

    // Check if user can place a pixel (considering both per-minute limit and individual cooldown)
    canPlacePixel() {
        this.checkReset();
        return this.pixelsRemaining > 0 && this.getCooldownRemaining() === 0;
    }
}

// Notification Manager
class NotificationManager {
    constructor() {
        this.container = this.createContainer();
    }

    createContainer() {
        const existing = document.getElementById('notification-container');
        if (existing) return existing;
        
        const container = document.createElement('div');
        container.id = 'notification-container';
        container.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10000;
            display: flex;
            flex-direction: column;
            gap: 10px;
        `;
        document.body.appendChild(container);
        return container;
    }

    show(message, type = 'info', duration = 10000) {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        notification.style.cssText = `
            padding: 12px 20px;
            border-radius: 6px;
            color: white;
            font-size: 14px;
            font-weight: 500;
            margin-bottom: 8px;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            max-width: 320px;
            word-wrap: break-word;
            opacity: 0;
            transform: translateX(100%);
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            border-left: 4px solid rgba(255,255,255,0.3);
        `;
        
        // Add CSS animations to document if not already present
        if (!document.getElementById('notification-styles')) {
            const style = document.createElement('style');
            style.id = 'notification-styles';
            style.textContent = `
                @keyframes notificationSlideIn {
                    from {
                        opacity: 0;
                        transform: translateX(100%);
                    }
                    to {
                        opacity: 1;
                        transform: translateX(0);
                    }
                }
                @keyframes notificationSlideOut {
                    from {
                        opacity: 1;
                        transform: translateX(0);
                    }
                    to {
                        opacity: 0;
                        transform: translateX(100%);
                    }
                }
            `;
            document.head.appendChild(style);
        }

        // Set background color based on type
        const colors = {
            success: '#4caf50',
            error: '#f44336',
            warning: '#ff9800',
            info: '#2196f3'
        };
        notification.style.backgroundColor = colors[type] || colors.info;

        // Double-click to dismiss (prevent accidental dismissal)
        let clickCount = 0;
        notification.addEventListener('click', () => {
            clickCount++;
            if (clickCount === 1) {
                setTimeout(() => clickCount = 0, 1000); // Reset after 1 second
                // Show dismiss hint
                const originalText = notification.textContent;
                notification.textContent = 'Click again to dismiss - ' + originalText;
                setTimeout(() => {
                    if (notification.parentElement) {
                        notification.textContent = originalText;
                    }
                }, 2000);
            } else if (clickCount === 2) {
                this.remove(notification);
            }
        });

        this.container.appendChild(notification);
        
        // Trigger slide-in animation after element is in DOM
        requestAnimationFrame(() => {
            notification.style.opacity = '1';
            notification.style.transform = 'translateX(0)';
        });

        // Auto-remove after duration
        const autoRemoveTimer = setTimeout(() => {
            if (notification.parentElement) {
                this.remove(notification);
            }
        }, duration);
        
        // Store timer for potential early cancellation
        notification.dataset.autoRemoveTimer = autoRemoveTimer;
    }

    remove(notification) {
        if (!notification.parentElement) return; // Already removed
        
        // Cancel auto-remove timer if manually removing
        if (notification.dataset.autoRemoveTimer) {
            clearTimeout(parseInt(notification.dataset.autoRemoveTimer));
        }
        
        // Animate out
        notification.style.opacity = '0';
        notification.style.transform = 'translateX(100%)';
        
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 400); // Match transition duration
    }
}

// Main PixelWar class
class PixelWar {
    constructor(canvasId, config) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) {
            throw new Error(`Canvas element with id "${canvasId}" not found`);
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
            this.targetOffsetX += e.detail.deltaX / (pixelSize * this.zoom);
            this.targetOffsetY += e.detail.deltaY / (pixelSize * this.zoom);
            
            this.velocityX = e.detail.velocityX;
            this.velocityY = e.detail.velocityY;
            
            this.constrainOffsets();
            this.startAnimation();
        });

        // Handle touch drag events (mobile) with enhanced sensitivity
        this.inputHandler.addEventListener('touchdrag', (e) => {
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            
            // Enhanced mobile sensitivity - use the improved delta values
            const deltaX = e.detail.deltaX || 0;
            const deltaY = e.detail.deltaY || 0;
            const isMobile = e.detail.isMobile || false;
            
            // Apply mobile-optimized movement with immediate response
            const movementDivisor = pixelSize * this.zoom;
            this.targetOffsetX += deltaX / movementDivisor;
            this.targetOffsetY += deltaY / movementDivisor;
            
            // Calculate velocity for momentum
            this.velocityX = deltaX / 8; // Faster velocity calculation
            this.velocityY = deltaY / 8;
            
            // Apply constraints immediately for mobile - no delays!
            if (isMobile) {
                this.constrainOffsets(true); // Pass mobile flag
            } else {
                // Delay constraints only for desktop to allow overshoot
                clearTimeout(this.mobileConstraintTimeout);
                this.mobileConstraintTimeout = setTimeout(() => {
                    this.constrainOffsets();
                }, 50); // Reduced delay
            }
            
            // Immediate response for all devices - no animation delays
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
            
            switch(e.detail.key) {
                case 'ArrowUp':
                    this.targetOffsetY += moveSpeed;
                    break;
                case 'ArrowDown':
                    this.targetOffsetY -= moveSpeed;
                    break;
                case 'ArrowLeft':
                    this.targetOffsetX += moveSpeed;
                    break;
                case 'ArrowRight':
                    this.targetOffsetX -= moveSpeed;
                    break;
                case '+':
                case '=':
                    this.adjustZoom(0.2);
                    break;
                case '-':
                    this.adjustZoom(-0.2);
                    break;
                case '0':
                    this.resetView();
                    break;
            }
            
            this.constrainOffsets();
            this.startAnimation();
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

    isValidCoordinate(x, y) {
        return x >= 0 && x < this.config.width && y >= 0 && y < this.config.height;
    }

    // Helper method to calculate effective viewport dimensions consistently
    getEffectiveViewport(forceMobile = false) {
        const rect = this.canvas.getBoundingClientRect();
        const isMobile = forceMobile || /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth < 768;
        
        let effectiveWidth = rect.width;
        let effectiveHeight = rect.height;
        
        // Debug removed to prevent spam
        
        if (isMobile) {
            // Get actual rendered dimensions of mobile UI elements - but be more conservative
            const mobileTopControls = document.querySelector('.mobile-controls-bar');
            const mobileActionBar = document.querySelector('.mobile-action-bar');
            
            let topControlsHeight = 0;
            let bottomControlsHeight = 0;
            
            if (mobileTopControls && getComputedStyle(mobileTopControls).display !== 'none') {
                topControlsHeight = mobileTopControls.offsetHeight;
            }
            if (mobileActionBar && getComputedStyle(mobileActionBar).display !== 'none') {
                bottomControlsHeight = mobileActionBar.offsetHeight;
            }
            
            // Only subtract actual visible UI heights, no arbitrary margins
            effectiveHeight -= (topControlsHeight + bottomControlsHeight);
            
            // DO NOT subtract width - the canvas should use full width
            // effectiveWidth -= 20; // REMOVED - this was causing the centering issue
            
            // Debug removed to prevent spam
        }
        
        return {
            width: Math.max(100, effectiveWidth), // Minimum viable dimensions
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
        
        // Debug logging for offset calculation - reduced frequency
        if (Math.random() < 0.001) { // Log 0.1% of the time to reduce spam
            console.log('🎯 Offset Debug:', {
                zoom: this.zoom.toFixed(3),
                viewportSize: `${viewportWidth.toFixed(1)}x${viewportHeight.toFixed(1)}`,
                mapSize: `${this.config.width}x${this.config.height}`,
                canFitWidth: viewportWidth >= this.config.width,
                canFitHeight: viewportHeight >= this.config.height,
                targetOffsets: `${this.targetOffsetX.toFixed(2)}, ${this.targetOffsetY.toFixed(2)}`,
                currentOffsets: `${this.offsetX.toFixed(2)}, ${this.offsetY.toFixed(2)}`,
                isMobile
            });
        }
    }

    constrainOffset(offset, gridSize, viewportSize, isMobile = false) {
        if (viewportSize >= gridSize) {
            // When zoomed out far enough to see the whole grid, center it properly
            // Let me think through this step by step:
            // 1. Canvas transform: ctx.translate(offsetX * pixelSize, offsetY * pixelSize)
            // 2. viewportX in render = -offsetX (line 179)  
            // 3. To center a small grid in a large viewport, we want the grid to start at position (viewportSize - gridSize) / 2
            // 4. Since viewportX represents where we're "looking" in grid coordinates, and viewportX = -offsetX
            // 5. If we want to look at grid position (viewportSize - gridSize) / 2, then -offsetX = (viewportSize - gridSize) / 2
            // 6. Therefore: offsetX = -(viewportSize - gridSize) / 2
            
            const centeredOffset = -(viewportSize - gridSize) / 2;
            // Only log centering debug occasionally to avoid spam
            if (Math.random() < 0.1) {
                console.log(`🎯 CENTERING: viewport=${viewportSize.toFixed(1)}, grid=${gridSize}, offset=${centeredOffset.toFixed(2)}`);
            }
            return centeredOffset;
        }
        
        // When zoomed in, constrain to grid boundaries
        let minOffset = -(gridSize - viewportSize);
        let maxOffset = 0;
        
        if (isMobile) {
            // Simplified mobile constraints - allow 50% overshoot for easier navigation
            const mobileMargin = viewportSize * 0.5;
            const mobileMinOffset = minOffset - mobileMargin;
            const mobileMaxOffset = maxOffset + mobileMargin;
            
            // Simple clamp with mobile-friendly margins
            return Math.max(mobileMinOffset, Math.min(mobileMaxOffset, offset));
        }
        
        // Desktop: strict constraints
        return Math.max(minOffset, Math.min(maxOffset, offset));
    }

    calculateMinZoom() {
        // Calculate minimum zoom to fit entire map in viewport
        const { width: effectiveWidth, height: effectiveHeight, isMobile } = this.getEffectiveViewport();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        const mapPixelWidth = this.config.width * pixelSize;
        const mapPixelHeight = this.config.height * pixelSize;
        
        const zoomToFitWidth = effectiveWidth / mapPixelWidth;
        const zoomToFitHeight = effectiveHeight / mapPixelHeight;
        
        // Use the smaller zoom to ensure entire map fits, with a small margin for safety
        const calculatedMinZoom = Math.min(zoomToFitWidth, zoomToFitHeight);
        // Add 5% margin to ensure the full map is visible with void area around it
        const finalMinZoom = Math.max(calculatedMinZoom * 0.95, 0.05);
        
        console.log('🔍 Zoom Calculation:', {
            canvasRect: `${document.getElementById('pixelCanvas').getBoundingClientRect().width}x${document.getElementById('pixelCanvas').getBoundingClientRect().height}`,
            effectiveSize: `${effectiveWidth}x${effectiveHeight}`,
            mapSize: `${this.config.width}x${this.config.height}`,
            mapPixelSize: `${mapPixelWidth}x${mapPixelHeight}`,
            zoomToFitWidth: zoomToFitWidth.toFixed(3),
            zoomToFitHeight: zoomToFitHeight.toFixed(3),
            calculatedMinZoom: calculatedMinZoom.toFixed(3),
            finalMinZoom: finalMinZoom.toFixed(3),
            isMobile
        });
        
        // Allow full zoom out to show entire map, minimum constraint is very low
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
        
        this.zoom = Math.max(
            dynamicMinZoom,
            Math.min(PixelWarConfig.canvas.maxZoom, this.zoom + delta)
        );
        
        if (this.zoom !== oldZoom) {
            if (centerX !== null && centerY !== null) {
                // Zoom toward point
                const coords = this.screenToCanvas(centerX, centerY);
                const zoomRatio = this.zoom / oldZoom;
                
                this.offsetX = (coords.x + this.offsetX) * zoomRatio - coords.x;
                this.offsetY = (coords.y + this.offsetY) * zoomRatio - coords.y;
                
                this.targetOffsetX = this.offsetX;
                this.targetOffsetY = this.offsetY;
            }
            
            // Always constrain offsets after zoom change
            this.constrainOffsets();
            
            // Update current offsets to match targets for immediate effect
            this.offsetX = this.targetOffsetX;
            this.offsetY = this.targetOffsetY;
            
            // Update zoom indicator
            this.updateZoomIndicator();
        }
        
        this.render();
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
        
        // Force center the map initially
        this.navigateToCorner('center');
        this.updateZoomIndicator();
    }

    navigateToCorner(corner) {
        const rect = this.canvas.getBoundingClientRect();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        const viewportWidth = rect.width / (pixelSize * this.zoom);
        const viewportHeight = rect.height / (pixelSize * this.zoom);
        
        let targetX, targetY;
        
        switch(corner) {
            case 'top-left':
                targetX = 0;
                targetY = 0;
                break;
            case 'top-right':
                targetX = -(this.config.width - viewportWidth);
                targetY = 0;
                break;
            case 'bottom-left':
                targetX = 0;
                targetY = -(this.config.height - viewportHeight);
                break;
            case 'bottom-right':
                targetX = -(this.config.width - viewportWidth);
                targetY = -(this.config.height - viewportHeight);
                break;
            case 'center':
                targetX = -(this.config.width - viewportWidth) / 2;
                targetY = -(this.config.height - viewportHeight) / 2;
                break;
            default:
                return;
        }
        
        // Smooth animation to corner
        this.targetOffsetX = targetX;
        this.targetOffsetY = targetY;
        this.startAnimation();
        
        this.notifications.show(`Navigating to ${corner} corner`, 'info', 3000);
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
        
        this.render();
        
        // Continue if still moving
        if (Math.abs(this.targetOffsetX - this.offsetX) > 0.01 ||
            Math.abs(this.targetOffsetY - this.offsetY) > 0.01) {
            this.animationFrame = requestAnimationFrame(() => this.animate());
        } else {
            this.animationFrame = null;
        }
    }

    applyMomentum() {
        const animate = () => {
            this.velocityX *= PixelWarConfig.animation.friction;
            this.velocityY *= PixelWarConfig.animation.friction;
            
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            this.targetOffsetX += this.velocityX / (pixelSize * this.zoom);
            this.targetOffsetY += this.velocityY / (pixelSize * this.zoom);
            
            this.constrainOffsets();
            this.offsetX += (this.targetOffsetX - this.offsetX) * 0.2;
            this.offsetY += (this.targetOffsetY - this.offsetY) * 0.2;
            
            this.render();
            
            if (Math.abs(this.velocityX) > PixelWarConfig.animation.momentumThreshold ||
                Math.abs(this.velocityY) > PixelWarConfig.animation.momentumThreshold) {
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

// Add CSS animations
const style = document.createElement('style');
style.textContent = `
    /* Enhanced Cooldown UI Styles */
    .cooldown-progress {
        position: absolute;
        bottom: 0;
        left: 0;
        height: 3px;
        background: linear-gradient(90deg, #4caf50, #8bc34a);
        transition: width 0.1s ease-out;
        border-radius: 0 0 4px 4px;
    }
    
    #cooldownTimer {
        position: relative;
        padding: 8px 12px;
        border-radius: 6px;
        font-weight: 600;
        font-size: 14px;
        transition: all 0.2s ease;
        overflow: hidden;
    }
    
    .pixels-remaining {
        padding: 8px 12px;
        border-radius: 6px;
        transition: all 0.2s ease;
    }
    
    .pixels-remaining.ready {
        background-color: #e8f5e8;
        border: 2px solid #4caf50;
    }
    
    .pixels-remaining.cooldown {
        background-color: #fff3e0;
        border: 2px solid #ff9800;
    }
    
    .pixels-remaining.limit-reached {
        background-color: #ffebee;
        border: 2px solid #ff6b6b;
    }
    
    .pixels-info {
        text-align: center;
    }
    
    .pixels-count {
        font-size: 18px;
        font-weight: 600;
        margin-bottom: 4px;
    }
    
    .pixels-count .current {
        color: #2196f3;
        font-size: 22px;
    }
    
    .pixels-count .separator {
        color: #666;
        margin: 0 2px;
    }
    
    .pixels-count .max {
        color: #666;
    }
    
    .pixels-count .label {
        color: #888;
        font-size: 12px;
        margin-left: 4px;
    }
    
    .reset-timer {
        font-size: 11px;
        color: #666;
        font-style: italic;
    }
    
    /* Mobile Touch Mode Toggle Styles */
    .touch-mode-toggle {
        position: fixed;
        top: 10px;
        right: 10px;
        z-index: 1000;
    }
    
    .touch-mode-btn {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 16px;
        border: none;
        border-radius: 20px;
        background: #4caf50;
        color: white;
        font-weight: 600;
        font-size: 14px;
        cursor: pointer;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        transition: all 0.3s ease;
    }
    
    .touch-mode-btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }
    
    .touch-mode-btn.precision {
        background: #ff9800;
    }
    
    .mode-icon {
        font-size: 16px;
    }
    
    .mode-text {
        font-size: 12px;
        white-space: nowrap;
    }
    
    /* Mobile Pixel Confirmation Modal */
    .pixel-confirmation-mobile {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.8);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 2000;
        animation: fadeIn 0.2s ease;
    }
    
    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }
    
    .confirmation-content {
        background: white;
        border-radius: 16px;
        padding: 24px;
        margin: 20px;
        max-width: 300px;
        text-align: center;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    }
    
    .confirmation-content h4 {
        margin: 0 0 16px 0;
        color: #333;
        font-size: 18px;
    }
    
    .confirmation-content p {
        margin: 8px 0;
        color: #666;
        font-size: 14px;
    }
    
    .confirmation-buttons {
        display: flex;
        gap: 12px;
        margin-top: 20px;
    }
    
    .btn-confirm, .btn-cancel {
        flex: 1;
        padding: 12px 16px;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        font-size: 14px;
        cursor: pointer;
        transition: all 0.2s ease;
    }
    
    .btn-confirm {
        background: #4caf50;
        color: white;
    }
    
    .btn-confirm:hover {
        background: #45a049;
        transform: translateY(-1px);
    }
    
    .btn-cancel {
        background: #f44336;
        color: white;
    }
    
    .btn-cancel:hover {
        background: #da190b;
        transform: translateY(-1px);
    }
    
    /* Hide touch mode toggle on desktop */
    @media (hover: hover) and (pointer: fine) {
        .touch-mode-toggle {
            display: none;
        }
    }

    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }
    
    @keyframes shake {
        0%, 100% { transform: translateX(0); }
        25% { transform: translateX(-5px); }
        75% { transform: translateX(5px); }
    }
`;
document.head.appendChild(style);

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof CANVAS_CONFIG !== 'undefined') {
            window.pixelWar = new PixelWar('pixelCanvas', CANVAS_CONFIG);
        }
    });
} else {
    if (typeof CANVAS_CONFIG !== 'undefined') {
        window.pixelWar = new PixelWar('pixelCanvas', CANVAS_CONFIG);
    }
}

// Make PixelWar available globally for debugging and manual initialization
window.PixelWar = PixelWar;
window.PixelWarConfig = PixelWarConfig;
window.PixelWarAPI = PixelWarAPI;

// Export for module usage (commented out for regular script usage)
// If you want to use this as a module, uncomment the line below:
// export { PixelWar, PixelWarConfig, PixelWarAPI, CanvasRenderer, InputHandler, RateLimiter, NotificationManager };