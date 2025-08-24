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
        
        // Performance throttling
        this.lastEventTime = 0;
        this.throttleDelay = PixelWarConfig.animation.throttleMs;
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
        
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            this.touches.set(touch.identifier, {
                startX: touch.clientX,
                startY: touch.clientY,
                currentX: touch.clientX,
                currentY: touch.clientY,
                startTime: Date.now()
            });
        } else if (e.touches.length === 2) {
            this.lastTouchDistance = this.getTouchDistance(e.touches[0], e.touches[1]);
        }
    }

    handleTouchMove(e) {
        e.preventDefault();
        
        // Throttle touch move events for better performance
        const now = performance.now();
        if (now - this.lastEventTime < this.throttleDelay) {
            return;
        }
        this.lastEventTime = now;
        
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            const touchData = this.touches.get(touch.identifier);
            
            if (touchData) {
                const deltaX = touch.clientX - touchData.currentX;
                const deltaY = touch.clientY - touchData.currentY;
                
                touchData.currentX = touch.clientX;
                touchData.currentY = touch.clientY;
                
                const moveDistance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);
                
                if (moveDistance > 2) { // Reduced threshold for better responsiveness
                    this.dispatchEvent(new CustomEvent('touchdrag', {
                        detail: { deltaX, deltaY }
                    }));
                }
            }
        } else if (e.touches.length === 2) {
            const distance = this.getTouchDistance(e.touches[0], e.touches[1]);
            if (this.lastTouchDistance) {
                const scale = distance / this.lastTouchDistance;
                this.dispatchEvent(new CustomEvent('pinchzoom', {
                    detail: { scale }
                }));
                this.lastTouchDistance = distance;
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
        
        if (e.touches.length === 0) {
            this.lastTouchDistance = 0;
        }
    }

    getTouchDistance(touch1, touch2) {
        const dx = touch1.clientX - touch2.clientX;
        const dy = touch1.clientY - touch2.clientY;
        return Math.sqrt(dx * dx + dy * dy);
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
    }

    canPlace() {
        this.checkReset();
        return this.pixelsRemaining > 0;
    }

    recordPlacement() {
        this.checkReset();
        if (this.pixelsRemaining > 0) {
            this.pixelsRemaining--;
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
        }
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
            if (this.isValidCoordinate(coords.x, coords.y)) {
                this.placePixel(coords.x, coords.y);
            }
        });

        this.inputHandler.addEventListener('tap', (e) => {
            const coords = this.screenToCanvas(e.detail.x, e.detail.y);
            if (this.isValidCoordinate(coords.x, coords.y)) {
                this.placePixel(coords.x, coords.y);
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
            
            // Enhanced mobile sensitivity - reduce divisor for faster movement
            const mobileSensitivityMultiplier = 1.5;
            const movementDivisor = (pixelSize * this.zoom) / mobileSensitivityMultiplier;
            
            this.targetOffsetX += e.detail.deltaX / movementDivisor;
            this.targetOffsetY += e.detail.deltaY / movementDivisor;
            
            // Calculate velocity for momentum
            const deltaTime = 16; // Assume 60fps
            this.velocityX = e.detail.deltaX / deltaTime;
            this.velocityY = e.detail.deltaY / deltaTime;
            
            // Delay constraints to allow overshoot for corner access
            clearTimeout(this.mobileConstraintTimeout);
            this.mobileConstraintTimeout = setTimeout(() => {
                this.constrainOffsets();
            }, 100); // 100ms delay
            
            this.startAnimation();
        });

        this.inputHandler.addEventListener('dragend', () => {
            // Clear mobile constraint timeout and apply final constraints
            clearTimeout(this.mobileConstraintTimeout);
            
            if (Math.abs(this.velocityX) > 1 || Math.abs(this.velocityY) > 1) {
                this.applyMomentum();
            } else {
                // Apply gentle constraint when drag ends without momentum
                setTimeout(() => {
                    this.constrainOffsets();
                }, 200); // Brief delay for smooth UX
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
            this.adjustZoom((e.detail.scale - 1) * 0.5);
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
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        const canvasX = (screenX - rect.left) / (pixelSize * this.zoom);
        const canvasY = (screenY - rect.top) / (pixelSize * this.zoom);
        
        return {
            x: Math.floor(canvasX - this.offsetX),
            y: Math.floor(canvasY - this.offsetY)
        };
    }

    isValidCoordinate(x, y) {
        return x >= 0 && x < this.config.width && y >= 0 && y < this.config.height;
    }

    constrainOffsets() {
        const rect = this.canvas.getBoundingClientRect();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        let effectiveWidth = rect.width;
        let effectiveHeight = rect.height;
        
        // Account for mobile UI elements that reduce effective viewport
        const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth < 768;
        
        if (isMobile) {
            // Add padding to account for mobile controls and UI elements
            const mobileTopControls = document.querySelector('.mobile-controls-bar');
            const mobileActionBar = document.querySelector('.mobile-action-bar');
            
            if (mobileTopControls && getComputedStyle(mobileTopControls).display !== 'none') {
                effectiveHeight -= mobileTopControls.offsetHeight;
            }
            if (mobileActionBar && getComputedStyle(mobileActionBar).display !== 'none') {
                effectiveHeight -= mobileActionBar.offsetHeight;
            }
            
            // Add extra margin for safe touch areas
            effectiveWidth -= 40; // 20px on each side
            effectiveHeight -= 60; // 30px top and bottom
        }
        
        const viewportWidth = effectiveWidth / (pixelSize * this.zoom);
        const viewportHeight = effectiveHeight / (pixelSize * this.zoom);
        
        this.targetOffsetX = this.constrainOffset(this.targetOffsetX, this.config.width, viewportWidth, isMobile);
        this.targetOffsetY = this.constrainOffset(this.targetOffsetY, this.config.height, viewportHeight, isMobile);
    }

    constrainOffset(offset, gridSize, viewportSize, isMobile = false) {
        if (viewportSize >= gridSize) {
            // When zoomed out far enough to see the whole grid, center it
            return -(gridSize - viewportSize) / 2;
        }
        
        // When zoomed in, constrain to grid boundaries
        let minOffset = -(gridSize - viewportSize);
        let maxOffset = 0;
        
        if (isMobile) {
            // Use 60% of viewport size for comfortable corner access
            const mobileMargin = viewportSize * 0.6;
            const softMinOffset = minOffset - mobileMargin;
            const softMaxOffset = maxOffset + mobileMargin;
            
            // Allow overshoot but with gentle pullback for extreme values
            const extremeMargin = viewportSize * 1.2; // Even larger for temporary overshoot
            const hardMinOffset = minOffset - extremeMargin;
            const hardMaxOffset = maxOffset + extremeMargin;
            
            if (offset < hardMinOffset || offset > hardMaxOffset) {
                // Hard constraint for extreme overshoot
                return Math.max(hardMinOffset, Math.min(hardMaxOffset, offset));
            } else if (offset < softMinOffset || offset > softMaxOffset) {
                // Gentle pullback toward soft boundaries
                const pullbackStrength = 0.3;
                if (offset < softMinOffset) {
                    return offset + (softMinOffset - offset) * pullbackStrength;
                } else if (offset > softMaxOffset) {
                    return offset + (softMaxOffset - offset) * pullbackStrength;
                }
            }
            
            // Within soft boundaries - no constraint
            return offset;
        }
        
        // Desktop: strict constraints
        return Math.max(minOffset, Math.min(maxOffset, offset));
    }

    calculateMinZoom() {
        // Calculate minimum zoom to fit entire map in viewport
        const rect = this.canvas.getBoundingClientRect();
        const pixelSize = PixelWarConfig.canvas.defaultPixelSize;
        
        let effectiveWidth = rect.width;
        let effectiveHeight = rect.height;
        
        // Account for mobile UI elements
        const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth < 768;
        
        if (isMobile) {
            const mobileTopControls = document.querySelector('.mobile-controls-bar');
            const mobileActionBar = document.querySelector('.mobile-action-bar');
            
            if (mobileTopControls && getComputedStyle(mobileTopControls).display !== 'none') {
                effectiveHeight -= mobileTopControls.offsetHeight;
            }
            if (mobileActionBar && getComputedStyle(mobileActionBar).display !== 'none') {
                effectiveHeight -= mobileActionBar.offsetHeight;
            }
            
            // Add safe area margins
            effectiveWidth -= 40;
            effectiveHeight -= 60;
        }
        
        const mapPixelWidth = this.config.width * pixelSize;
        const mapPixelHeight = this.config.height * pixelSize;
        
        const zoomToFitWidth = effectiveWidth / mapPixelWidth;
        const zoomToFitHeight = effectiveHeight / mapPixelHeight;
        
        // Use the smaller zoom to ensure entire map fits
        const calculatedMinZoom = Math.min(zoomToFitWidth, zoomToFitHeight);
        
        // Don't go below the configured minimum, but also don't allow zooming out beyond full visibility
        return Math.max(calculatedMinZoom, PixelWarConfig.canvas.minZoom * 0.1);
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
        }
        
        this.render();
    }

    initializeZoom() {
        // Set initial zoom, ensuring it fits within the dynamic minimum
        const dynamicMinZoom = this.calculateMinZoom();
        this.zoom = Math.max(PixelWarConfig.canvas.defaultZoom, dynamicMinZoom);
        this.constrainOffsets();
        this.offsetX = this.targetOffsetX;
        this.offsetY = this.targetOffsetY;
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
        this.zoom = Math.max(PixelWarConfig.canvas.defaultZoom, dynamicMinZoom);
        this.offsetX = 0;
        this.offsetY = 0;
        this.targetOffsetX = 0;
        this.targetOffsetY = 0;
        this.constrainOffsets();
        this.offsetX = this.targetOffsetX;
        this.offsetY = this.targetOffsetY;
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
        
        // Smooth interpolation with time-based animation
        const smoothness = Math.min(PixelWarConfig.animation.smoothness, deltaTime / 16);
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
        this.renderer.render(this.offsetX, this.offsetY, this.zoom, showGrid);
    }

    async placePixel(x, y) {
        if (!this.rateLimiter.canPlace()) {
            const timeLeft = Math.ceil(this.rateLimiter.getTimeUntilReset());
            this.notifications.show(`Rate limit reached. Reset in ${timeLeft}s`, 'warning');
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
        const timer = document.getElementById('cooldownTimer');
        const remaining = document.getElementById('pixelsRemaining');
        
        if (timer) {
            timer.textContent = this.rateLimiter.pixelsRemaining > 0 ? 'Ready' : 'Limit reached';
            timer.style.color = this.rateLimiter.pixelsRemaining > 0 ? '#4caf50' : '#ff6b6b';
        }
        
        if (remaining) {
            remaining.textContent = `${this.rateLimiter.pixelsRemaining}/${this.rateLimiter.maxPixelsPerMinute}`;
            remaining.style.color = this.rateLimiter.pixelsRemaining > 0 ? '#4caf50' : '#ff6b6b';
        }
    }

    highlightSelectedColor(element) {
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.classList.remove('selected');
        });
        element.classList.add('selected');
    }

    startUpdateLoop() {
        this.updateInterval = setInterval(async () => {
            await this.loadCanvasState();
            await this.loadRecentActivity();
            this.rateLimiter.checkReset();
            this.updateUI();
        }, PixelWarConfig.api.updateInterval);
    }

    destroy() {
        // Clean up
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
        
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
        }
        
        this.inputHandler.destroy();
        this.isRunning = false;
    }
}

// Add CSS animations
const style = document.createElement('style');
style.textContent = `
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