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
        momentumThreshold: 0.5
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
        const isDirty = this.dirtyRegions.size > 0;
        
        if (!isDirty) return;

        // Clear and setup transform
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.ctx.save();
        this.ctx.scale(zoom, zoom);
        this.ctx.translate(offsetX * this.pixelSize, offsetY * this.pixelSize);

        // Draw background
        this.ctx.fillStyle = '#ffffff';
        this.ctx.fillRect(0, 0, this.width * this.pixelSize, this.height * this.pixelSize);

        // Draw pixels
        for (const key in this.pixels) {
            const [x, y] = key.split(',').map(Number);
            this.ctx.fillStyle = this.pixels[key].color;
            this.ctx.fillRect(
                x * this.pixelSize,
                y * this.pixelSize,
                this.pixelSize,
                this.pixelSize
            );
        }

        // Draw grid if needed
        if (showGrid && zoom > PixelWarConfig.canvas.gridThreshold) {
            this.drawGrid();
        }

        this.ctx.restore();
        this.dirtyRegions.clear();
    }

    drawGrid() {
        this.ctx.strokeStyle = 'rgba(200, 200, 200, 0.3)';
        this.ctx.lineWidth = 0.5;

        for (let x = 0; x <= this.width; x++) {
            this.ctx.beginPath();
            this.ctx.moveTo(x * this.pixelSize, 0);
            this.ctx.lineTo(x * this.pixelSize, this.height * this.pixelSize);
            this.ctx.stroke();
        }

        for (let y = 0; y <= this.height; y++) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, y * this.pixelSize);
            this.ctx.lineTo(this.width * this.pixelSize, y * this.pixelSize);
            this.ctx.stroke();
        }
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
        
        if (e.touches.length === 1) {
            const touch = e.touches[0];
            const touchData = this.touches.get(touch.identifier);
            
            if (touchData) {
                touchData.currentX = touch.clientX;
                touchData.currentY = touch.clientY;
                
                const moveDistance = Math.sqrt(
                    Math.pow(touch.clientX - touchData.startX, 2) +
                    Math.pow(touch.clientY - touchData.startY, 2)
                );
                
                if (moveDistance > 5) {
                    this.dispatchEvent(new CustomEvent('touchdrag', {
                        detail: {
                            deltaX: touch.clientX - touchData.currentX,
                            deltaY: touch.clientY - touchData.currentY
                        }
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

    show(message, type = 'info', duration = 3000) {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        notification.style.cssText = `
            padding: 12px 20px;
            border-radius: 4px;
            color: white;
            font-size: 14px;
            animation: slideIn 0.3s ease;
            cursor: pointer;
        `;

        // Set background color based on type
        const colors = {
            success: '#4caf50',
            error: '#f44336',
            warning: '#ff9800',
            info: '#2196f3'
        };
        notification.style.backgroundColor = colors[type] || colors.info;

        notification.addEventListener('click', () => {
            this.remove(notification);
        });

        this.container.appendChild(notification);

        setTimeout(() => {
            this.remove(notification);
        }, duration);
    }

    remove(notification) {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => {
            if (notification.parentElement) {
                notification.remove();
            }
        }, 300);
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
        this.velocityX = 0;
        this.velocityY = 0;
        this.selectedColor = '#000000';

        // Animation
        this.animationFrame = null;
        this.updateInterval = null;

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

        this.inputHandler.addEventListener('dragend', () => {
            if (Math.abs(this.velocityX) > 1 || Math.abs(this.velocityY) > 1) {
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
        const viewportWidth = rect.width / (pixelSize * this.zoom);
        const viewportHeight = rect.height / (pixelSize * this.zoom);
        
        this.targetOffsetX = this.constrainOffset(this.targetOffsetX, this.config.width, viewportWidth);
        this.targetOffsetY = this.constrainOffset(this.targetOffsetY, this.config.height, viewportHeight);
    }

    constrainOffset(offset, gridSize, viewportSize) {
        if (viewportSize >= gridSize) {
            return (viewportSize - gridSize) / 2;
        }
        
        const minOffset = -(gridSize - viewportSize);
        const maxOffset = 0;
        
        return Math.max(minOffset, Math.min(maxOffset, offset));
    }

    adjustZoom(delta, centerX = null, centerY = null) {
        const oldZoom = this.zoom;
        this.zoom = Math.max(
            PixelWarConfig.canvas.minZoom,
            Math.min(PixelWarConfig.canvas.maxZoom, this.zoom + delta)
        );
        
        if (this.zoom !== oldZoom && centerX !== null && centerY !== null) {
            // Zoom toward point
            const coords = this.screenToCanvas(centerX, centerY);
            const zoomRatio = this.zoom / oldZoom;
            
            this.offsetX = (coords.x + this.offsetX) * zoomRatio - coords.x;
            this.offsetY = (coords.y + this.offsetY) * zoomRatio - coords.y;
            
            this.targetOffsetX = this.offsetX;
            this.targetOffsetY = this.offsetY;
        }
        
        this.constrainOffsets();
        this.render();
    }

    resetView() {
        this.zoom = PixelWarConfig.canvas.defaultZoom;
        this.offsetX = 0;
        this.offsetY = 0;
        this.targetOffsetX = 0;
        this.targetOffsetY = 0;
        this.render();
    }

    startAnimation() {
        if (!this.animationFrame) {
            this.animate();
        }
    }

    animate() {
        // Smooth interpolation
        this.offsetX += (this.targetOffsetX - this.offsetX) * PixelWarConfig.animation.smoothness;
        this.offsetY += (this.targetOffsetY - this.offsetY) * PixelWarConfig.animation.smoothness;
        
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

// Export for module usage (commented out for regular script usage)
// If you want to use this as a module, uncomment the line below:
// export { PixelWar, PixelWarConfig, PixelWarAPI, CanvasRenderer, InputHandler, RateLimiter, NotificationManager };