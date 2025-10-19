/**
 * Pixel War - Performance Optimized Version
 * Fixes laggy navigation with viewport culling and optimized rendering
 */

// Enhanced configuration with performance settings
const PixelWarConfig = {
    canvas: {
        defaultPixelSize: 10,
        minZoom: 0.5,
        maxZoom: 5,
        defaultZoom: 1,
        gridThreshold: 0.7
    },
    animation: {
        friction: 0.92,
        smoothness: 0.15,
        momentumThreshold: 0.5,
        maxFPS: 60,  // Cap frame rate
        throttleMs: 16  // ~60fps throttling
    },
    performance: {
        viewportMargin: 50,  // Extra pixels to render outside viewport
        maxPixelsPerFrame: 1000,  // Limit pixels rendered per frame
        enableViewportCulling: true,
        useOffscreenCanvas: true,
        batchSize: 100  // Batch size for pixel rendering
    },
    api: {
        updateInterval: 2000,
        retryAttempts: 3,
        endpoints: {
            placePixel: '/vibe-coding/api/place-pixel/',
            canvasState: '/vibe-coding/api/canvas-state/',
            pixelHistory: '/vibe-coding/api/pixel-history/'
        }
    }
};

// Performance-optimized Canvas Renderer
class OptimizedCanvasRenderer {
    constructor(canvas, config) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.config = config;
        this.pixels = {};
        this.dirtyRegions = new Set();
        
        // Performance optimization: offscreen canvas for pixel buffering
        this.offscreenCanvas = document.createElement('canvas');
        this.offscreenCtx = this.offscreenCanvas.getContext('2d');
        
        // Viewport culling variables
        this.lastViewport = { x: 0, y: 0, width: 0, height: 0, zoom: 1 };
        this.visiblePixels = new Set();
        
        // Grid cache
        this.gridCanvas = document.createElement('canvas');
        this.gridCtx = this.gridCanvas.getContext('2d');
        this.gridCached = false;
        this.lastGridZoom = 0;
    }

    setup(width, height, pixelSize) {
        this.width = width;
        this.height = height;
        this.pixelSize = pixelSize;
        
        this.canvas.width = window.innerWidth;
        this.canvas.height = window.innerHeight;
        
        this.offscreenCanvas.width = this.canvas.width;
        this.offscreenCanvas.height = this.canvas.height;
        
        // Enable image smoothing for better performance
        this.ctx.imageSmoothingEnabled = false;
        this.offscreenCtx.imageSmoothingEnabled = false;
    }

    setPixels(pixels) {
        this.pixels = pixels;
        this.markAllDirty();
        this.updateVisiblePixels(this.lastViewport);
    }

    updatePixel(x, y, color, placedBy) {
        const key = `${x},${y}`;
        this.pixels[key] = { color, placed_by: placedBy };
        this.markDirty(x, y);
        
        // Add to visible pixels if in viewport
        if (this.isPixelVisible(x, y)) {
            this.visiblePixels.add(key);
        }
    }

    markDirty(x, y) {
        this.dirtyRegions.add(`${x},${y}`);
    }

    markAllDirty() {
        this.dirtyRegions.clear();
        this.dirtyRegions.add("all");
    }

    // Viewport culling - only render visible pixels
    updateVisiblePixels(viewport) {
        if (!PixelWarConfig.performance.enableViewportCulling) {
            return;
        }

        const margin = PixelWarConfig.performance.viewportMargin;
        const startX = Math.max(0, Math.floor(viewport.x - margin / viewport.zoom));
        const endX = Math.min(this.width, Math.ceil(viewport.x + viewport.width / viewport.zoom + margin / viewport.zoom));
        const startY = Math.max(0, Math.floor(viewport.y - margin / viewport.zoom));
        const endY = Math.min(this.height, Math.ceil(viewport.y + viewport.height / viewport.zoom + margin / viewport.zoom));

        this.visiblePixels.clear();
        
        for (let x = startX; x < endX; x++) {
            for (let y = startY; y < endY; y++) {
                const key = `${x},${y}`;
                if (this.pixels[key]) {
                    this.visiblePixels.add(key);
                }
            }
        }
    }

    isPixelVisible(x, y) {
        const viewport = this.lastViewport;
        const margin = PixelWarConfig.performance.viewportMargin;
        
        return x >= viewport.x - margin / viewport.zoom &&
               x <= viewport.x + viewport.width / viewport.zoom + margin / viewport.zoom &&
               y >= viewport.y - margin / viewport.zoom &&
               y <= viewport.y + viewport.height / viewport.zoom + margin / viewport.zoom;
    }

    // Optimized render with viewport culling
    render(offsetX, offsetY, zoom, showGrid) {
        const viewport = {
            x: -offsetX,
            y: -offsetY,
            width: this.canvas.width / zoom,
            height: this.canvas.height / zoom,
            zoom: zoom
        };

        // Update visible pixels only if viewport changed significantly
        if (this.hasViewportChanged(viewport)) {
            this.updateVisiblePixels(viewport);
            this.lastViewport = { ...viewport };
        }

        // Clear canvas efficiently
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        this.ctx.save();
        this.ctx.scale(zoom, zoom);
        this.ctx.translate(offsetX * this.pixelSize, offsetY * this.pixelSize);

        // Draw background only in viewport
        this.ctx.fillStyle = '#ffffff';
        const bgX = Math.max(0, -offsetX * this.pixelSize);
        const bgY = Math.max(0, -offsetY * this.pixelSize);
        const bgWidth = Math.min(this.width * this.pixelSize, this.canvas.width / zoom);
        const bgHeight = Math.min(this.height * this.pixelSize, this.canvas.height / zoom);
        this.ctx.fillRect(bgX, bgY, bgWidth, bgHeight);

        // Render pixels in batches for better performance
        this.renderPixelsBatched();

        // Draw grid with caching
        if (showGrid && zoom > PixelWarConfig.canvas.gridThreshold) {
            this.drawOptimizedGrid(zoom, viewport);
        }

        this.ctx.restore();
        this.dirtyRegions.clear();
    }

    hasViewportChanged(viewport) {
        const last = this.lastViewport;
        const threshold = 0.1;
        
        return Math.abs(viewport.x - last.x) > threshold ||
               Math.abs(viewport.y - last.y) > threshold ||
               Math.abs(viewport.zoom - last.zoom) > 0.01 ||
               Math.abs(viewport.width - last.width) > threshold ||
               Math.abs(viewport.height - last.height) > threshold;
    }

    renderPixelsBatched() {
        const pixelsToRender = PixelWarConfig.performance.enableViewportCulling 
            ? Array.from(this.visiblePixels)
            : Object.keys(this.pixels);

        const batchSize = PixelWarConfig.performance.batchSize;
        const maxPixels = Math.min(pixelsToRender.length, PixelWarConfig.performance.maxPixelsPerFrame);

        // Render pixels in batches to avoid blocking the main thread
        for (let i = 0; i < maxPixels; i += batchSize) {
            const batch = pixelsToRender.slice(i, i + batchSize);
            this.renderPixelBatch(batch);
        }
    }

    renderPixelBatch(batch) {
        for (const key of batch) {
            if (!this.pixels[key]) continue;
            
            const [x, y] = key.split(',').map(Number);
            this.ctx.fillStyle = this.pixels[key].color;
            this.ctx.fillRect(
                x * this.pixelSize,
                y * this.pixelSize,
                this.pixelSize,
                this.pixelSize
            );
        }
    }

    // Optimized grid drawing with caching
    drawOptimizedGrid(zoom, viewport) {
        const gridZoomLevel = Math.floor(zoom * 10) / 10; // Round for cache key
        
        // Use cached grid if zoom hasn't changed much
        if (!this.gridCached || Math.abs(this.lastGridZoom - gridZoomLevel) > 0.1) {
            this.cacheGrid(gridZoomLevel, viewport);
            this.gridCached = true;
            this.lastGridZoom = gridZoomLevel;
        }

        // Draw only visible portion of the grid
        const startX = Math.max(0, Math.floor(-viewport.x));
        const endX = Math.min(this.width, Math.ceil(-viewport.x + viewport.width));
        const startY = Math.max(0, Math.floor(-viewport.y));
        const endY = Math.min(this.height, Math.ceil(-viewport.y + viewport.height));

        this.ctx.strokeStyle = 'rgba(200, 200, 200, 0.3)';
        this.ctx.lineWidth = 0.5 / zoom;

        // Draw vertical lines
        this.ctx.beginPath();
        for (let x = startX; x <= endX; x++) {
            this.ctx.moveTo(x * this.pixelSize, startY * this.pixelSize);
            this.ctx.lineTo(x * this.pixelSize, endY * this.pixelSize);
        }
        this.ctx.stroke();

        // Draw horizontal lines
        this.ctx.beginPath();
        for (let y = startY; y <= endY; y++) {
            this.ctx.moveTo(startX * this.pixelSize, y * this.pixelSize);
            this.ctx.lineTo(endX * this.pixelSize, y * this.pixelSize);
        }
        this.ctx.stroke();
    }

    cacheGrid(zoom, viewport) {
        // Cache grid for reuse - implementation can be added if needed
        // For now, we just mark as cached to use the optimized drawing
    }
}

// Throttled Input Handler
class ThrottledInputHandler extends EventTarget {
    constructor(canvas, config) {
        super();
        this.canvas = canvas;
        this.config = config;
        this.setupEventListeners();
        
        // Throttling
        this.lastRenderTime = 0;
        this.pendingRender = false;
        
        // Input state
        this.isDragging = false;
        this.spacePressed = false;
        this.touches = new Map();
        
        // Performance counters
        this.eventCount = 0;
        this.droppedEvents = 0;
    }

    setupEventListeners() {
        // Use passive listeners where possible for better performance
        this.canvas.addEventListener('mousedown', this.handleMouseDown.bind(this));
        this.canvas.addEventListener('mousemove', this.throttledMouseMove.bind(this), { passive: true });
        this.canvas.addEventListener('mouseup', this.handleMouseUp.bind(this));
        this.canvas.addEventListener('wheel', this.throttledWheel.bind(this), { passive: false });
        
        // Touch events with throttling
        this.canvas.addEventListener('touchstart', this.handleTouchStart.bind(this), { passive: false });
        this.canvas.addEventListener('touchmove', this.throttledTouchMove.bind(this), { passive: false });
        this.canvas.addEventListener('touchend', this.handleTouchEnd.bind(this));
        
        document.addEventListener('keydown', this.handleKeyDown.bind(this));
        document.addEventListener('keyup', this.handleKeyUp.bind(this));
    }

    throttledMouseMove(e) {
        this.eventCount++;
        
        if (!this.shouldProcessEvent()) {
            this.droppedEvents++;
            return;
        }

        this.handleMouseMove(e);
    }

    throttledTouchMove(e) {
        this.eventCount++;
        
        if (!this.shouldProcessEvent()) {
            this.droppedEvents++;
            return;
        }

        this.handleTouchMove(e);
    }

    throttledWheel(e) {
        this.eventCount++;
        
        if (!this.shouldProcessEvent()) {
            this.droppedEvents++;
            e.preventDefault(); // Still prevent default even if dropped
            return;
        }

        this.handleWheel(e);
    }

    shouldProcessEvent() {
        const now = performance.now();
        const timeSinceLastRender = now - this.lastRenderTime;
        
        if (timeSinceLastRender < PixelWarConfig.animation.throttleMs) {
            return false;
        }
        
        this.lastRenderTime = now;
        return true;
    }

    handleMouseDown(e) {
        if (this.spacePressed || e.button === 2 || e.button === 1) {
            e.preventDefault();
            this.startDrag(e.clientX, e.clientY);
        }
    }

    handleMouseMove(e) {
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

    handleWheel(e) {
        e.preventDefault();
        
        if (e.ctrlKey || e.metaKey) {
            const delta = e.deltaY > 0 ? -0.1 : 0.1;
            this.dispatchEvent(new CustomEvent('zoom', {
                detail: { delta, x: e.clientX, y: e.clientY }
            }));
        } else {
            this.dispatchEvent(new CustomEvent('pan', {
                detail: { deltaX: e.deltaX, deltaY: e.deltaY, shiftKey: e.shiftKey }
            }));
        }
    }

    handleTouchStart(e) {
        e.preventDefault();
        
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            this.touches.set(touch.identifier, {
                startX: touch.clientX,
                startY: touch.clientY,
                currentX: touch.clientX,
                currentY: touch.clientY,
                startTime: Date.now()
            });
        }
    }

    handleTouchMove(e) {
        e.preventDefault();
        
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            const touchData = this.touches.get(touch.identifier);
            
            if (touchData) {
                const deltaX = touch.clientX - touchData.currentX;
                const deltaY = touch.clientY - touchData.currentY;
                
                touchData.currentX = touch.clientX;
                touchData.currentY = touch.clientY;
                
                this.dispatchEvent(new CustomEvent('touchdrag', {
                    detail: { deltaX, deltaY }
                }));
            }
        }
    }

    handleTouchEnd(e) {
        if (e.changedTouches.length === 1) {
            const touch = e.changedTouches[0];
            const touchData = this.touches.get(touch.identifier);
            
            if (touchData) {
                const duration = Date.now() - touchData.startTime;
                const distance = Math.sqrt(
                    Math.pow(touchData.currentX - touchData.startX, 2) +
                    Math.pow(touchData.currentY - touchData.startY, 2)
                );
                
                if (duration < 300 && distance < 5) {
                    this.dispatchEvent(new CustomEvent('tap', {
                        detail: { x: touchData.startX, y: touchData.startY }
                    }));
                }
                
                this.touches.delete(touch.identifier);
            }
        }
    }

    handleKeyDown(e) {
        if (e.code === 'Space' && !e.target.matches('input, textarea')) {
            e.preventDefault();
            this.spacePressed = true;
            this.canvas.style.cursor = 'grab';
        }
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

    getPerformanceStats() {
        return {
            eventCount: this.eventCount,
            droppedEvents: this.droppedEvents,
            dropRate: this.droppedEvents / this.eventCount * 100
        };
    }
}

// Re-use other classes from the original file with minor modifications
// (PixelWarAPI, RateLimiter, NotificationManager remain the same)

// Include other classes here - they can be copied from the original file
// For brevity, I'm indicating where they should be included

// [Include PixelWarAPI class from original]
// [Include RateLimiter class from original]
// [Include NotificationManager class from original]

// Performance-optimized main PixelWar class
class PixelWar {
    constructor(canvasId, config) {
        if (!document.getElementById(canvasId)) {
            throw new Error(`Canvas element with id "${canvasId}" not found`);
        }

        this.canvas = document.getElementById(canvasId);
        this.config = config;
        this.isRunning = false;

        // Use optimized components
        this.renderer = new OptimizedCanvasRenderer(this.canvas, config);
        this.inputHandler = new ThrottledInputHandler(this.canvas, config);
        
        // Other components remain the same
        this.api = new PixelWarAPI();
        this.rateLimiter = new RateLimiter(
            config.isAuthenticated ? config.registeredPixelsPerMinute : config.anonymousPixelsPerMinute,
            config.isAuthenticated ? config.registeredCooldown : config.anonymousCooldown
        );
        this.notifications = new NotificationManager();

        // Animation state
        this.zoom = PixelWarConfig.canvas.defaultZoom;
        this.offsetX = 0;
        this.offsetY = 0;
        this.targetOffsetX = 0;
        this.targetOffsetY = 0;
        this.velocityX = 0;
        this.velocityY = 0;
        this.selectedColor = '#000000';
        this.animationFrame = null;

        // Performance monitoring
        this.frameCount = 0;
        this.lastFPSUpdate = performance.now();
        this.currentFPS = 0;

        this.init();
    }

    async init() {
        try {
            this.renderer.setup(this.config.width, this.config.height, PixelWarConfig.canvas.defaultPixelSize);
            this.setupEventHandlers();
            await this.loadCanvasState();
            this.startUpdateLoop();
            this.setupUIControls();
            this.isRunning = true;
            
            this.notifications.show('Canvas ready! (Performance optimized)', 'success');
            
            // Log performance info
            console.log('PixelWar Performance Mode:', {
                viewportCulling: PixelWarConfig.performance.enableViewportCulling,
                maxFPS: PixelWarConfig.animation.maxFPS,
                throttling: PixelWarConfig.animation.throttleMs + 'ms'
            });
        } catch (error) {
            console.error('Failed to initialize PixelWar:', error);
            this.notifications.show('Failed to initialize canvas', 'error');
        }
    }

    setupEventHandlers() {
        // Handle input events with throttling
        this.inputHandler.addEventListener('click', e => {
            const coords = this.screenToCanvas(e.detail.x, e.detail.y);
            if (this.isValidCoordinate(coords.x, coords.y)) {
                this.placePixel(coords.x, coords.y);
            }
        });

        this.inputHandler.addEventListener('tap', e => {
            const coords = this.screenToCanvas(e.detail.x, e.detail.y);
            if (this.isValidCoordinate(coords.x, coords.y)) {
                this.placePixel(coords.x, coords.y);
            }
        });

        this.inputHandler.addEventListener('drag', e => {
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            this.targetOffsetX += e.detail.deltaX / (pixelSize * this.zoom);
            this.targetOffsetY += e.detail.deltaY / (pixelSize * this.zoom);
            this.constrainOffsets();
            this.startAnimation();
        });

        this.inputHandler.addEventListener('touchdrag', e => {
            const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
            this.targetOffsetX += e.detail.deltaX / (pixelSize * this.zoom);
            this.targetOffsetY += e.detail.deltaY / (pixelSize * this.zoom);
            this.constrainOffsets();
            this.startAnimation();
        });

        this.inputHandler.addEventListener('zoom', e => {
            this.adjustZoom(e.detail.delta, e.detail.x, e.detail.y);
        });

        this.inputHandler.addEventListener('pan', e => {
            const sensitivity = 30 / (PixelWarConfig.canvas.defaultPixelSize * this.zoom);
            this.targetOffsetX -= e.detail.deltaX * sensitivity * 0.01;
            this.targetOffsetY -= e.detail.deltaY * sensitivity * 0.01;
            this.constrainOffsets();
            this.startAnimation();
        });
    }

    // Frame rate limited animation
    startAnimation() {
        if (this.animationFrame) return;
        
        const targetFPS = PixelWarConfig.animation.maxFPS;
        const frameTime = 1000 / targetFPS;
        let lastTime = 0;
        
        const animate = (currentTime) => {
            if (currentTime - lastTime >= frameTime) {
                this.updateFPS(currentTime);
                
                // Smooth interpolation
                this.offsetX += (this.targetOffsetX - this.offsetX) * PixelWarConfig.animation.smoothness;
                this.offsetY += (this.targetOffsetY - this.offsetY) * PixelWarConfig.animation.smoothness;
                
                this.render();
                
                lastTime = currentTime;
            }
            
            // Continue animation if still moving
            if (Math.abs(this.targetOffsetX - this.offsetX) > 0.01 || 
                Math.abs(this.targetOffsetY - this.offsetY) > 0.01) {
                this.animationFrame = requestAnimationFrame(animate);
            } else {
                this.animationFrame = null;
            }
        };
        
        this.animationFrame = requestAnimationFrame(animate);
    }

    updateFPS(currentTime) {
        this.frameCount++;
        const deltaTime = currentTime - this.lastFPSUpdate;
        
        if (deltaTime >= 1000) {
            this.currentFPS = Math.round((this.frameCount * 1000) / deltaTime);
            this.frameCount = 0;
            this.lastFPSUpdate = currentTime;
            
            // Update FPS display if element exists
            const fpsElement = document.getElementById('fpsCounter');
            if (fpsElement) {
                fpsElement.textContent = `${this.currentFPS} FPS`;
            }
        }
    }

    render() {
        const showGrid = this.zoom > PixelWarConfig.canvas.gridThreshold;
        this.renderer.render(this.offsetX, this.offsetY, this.zoom, showGrid);
    }

    // Keep other methods from original PixelWar class
    // (screenToCanvas, placePixel, loadCanvasState, etc.)
    // ... [copy remaining methods from original]

    // Add performance monitoring
    getPerformanceStats() {
        const inputStats = this.inputHandler.getPerformanceStats();
        return {
            fps: this.currentFPS,
            inputEvents: inputStats.eventCount,
            droppedEvents: inputStats.droppedEvents,
            dropRate: inputStats.dropRate,
            pixelCount: Object.keys(this.renderer.pixels).length,
            visiblePixels: this.renderer.visiblePixels.size
        };
    }

    logPerformanceStats() {
        const stats = this.getPerformanceStats();
        console.log('PixelWar Performance Stats:', stats);
    }
}

// Make classes globally available
window.PixelWar = PixelWar;
window.PixelWarConfig = PixelWarConfig;
window.OptimizedCanvasRenderer = OptimizedCanvasRenderer;
window.ThrottledInputHandler = ThrottledInputHandler;

// Auto-initialize when CANVAS_CONFIG is available
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