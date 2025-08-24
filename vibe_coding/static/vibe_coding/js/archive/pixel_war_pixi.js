// PixiJS Implementation - Solves all navigation and performance issues
// Install: npm install pixi.js pixi-viewport hammerjs

import * as PIXI from 'pixi.js';
import { Viewport } from 'pixi-viewport';
import Hammer from 'hammerjs';

class PixelWarPixi {
    constructor(containerId, config) {
        this.config = config;
        this.container = document.getElementById(containerId);
        
        // PixiJS Application with WebGL
        this.app = new PIXI.Application({
            width: window.innerWidth,
            height: window.innerHeight,
            backgroundColor: 0xffffff,
            antialias: false, // Crisp pixels
            resolution: window.devicePixelRatio || 1,
            autoDensity: true
        });
        
        this.container.appendChild(this.app.view);
        
        // Viewport for pan/zoom - SOLVES NAVIGATION ISSUES
        this.viewport = new Viewport({
            screenWidth: window.innerWidth,
            screenHeight: window.innerHeight,
            worldWidth: config.width * 10,
            worldHeight: config.height * 10,
            interaction: this.app.renderer.plugins.interaction
        });
        
        // Configure viewport behaviors
        this.viewport
            .drag()                    // Enable dragging
            .pinch()                   // Enable pinch zoom on mobile
            .wheel()                   // Enable mouse wheel zoom
            .decelerate({              // Smooth momentum scrolling
                friction: 0.95,
                bounce: 0.8
            })
            .clamp({                   // Prevent scrolling outside world
                left: 0,
                right: config.width * 10,
                top: 0,
                bottom: config.height * 10
            })
            .clampZoom({              // Zoom limits
                minScale: 0.5,
                maxScale: 10
            });
        
        this.app.stage.addChild(this.viewport);
        
        // Pixel container for batching
        this.pixelContainer = new PIXI.Container();
        this.viewport.addChild(this.pixelContainer);
        
        // Grid overlay
        this.gridGraphics = new PIXI.Graphics();
        this.viewport.addChild(this.gridGraphics);
        
        // Object pool for pixels (performance)
        this.pixelPool = [];
        this.activePixels = new Map();
        
        // Touch gestures for mobile
        this.setupTouchGestures();
        
        // Corner navigation buttons
        this.setupNavigationButtons();
        
        // Minimap
        this.setupMinimap();
        
        // Initialize
        this.init();
    }
    
    init() {
        // Create pixel sprites pool
        this.createPixelPool(10000);
        
        // Load existing pixels
        this.loadCanvasState();
        
        // Setup real-time updates
        this.setupWebSocket();
        
        // Handle window resize
        window.addEventListener('resize', () => this.onResize());
        
        // Update grid visibility based on zoom
        this.viewport.on('zoomed', () => this.updateGridVisibility());
        
        // Start render loop
        this.app.ticker.add(() => this.update());
    }
    
    createPixelPool(size) {
        // Pre-create pixel sprites for reuse (massive performance boost)
        const pixelTexture = PIXI.Texture.WHITE;
        
        for (let i = 0; i < size; i++) {
            const pixel = new PIXI.Sprite(pixelTexture);
            pixel.width = 10;
            pixel.height = 10;
            pixel.visible = false;
            this.pixelContainer.addChild(pixel);
            this.pixelPool.push(pixel);
        }
    }
    
    placePixel(x, y, color) {
        const key = `${x},${y}`;
        let pixel = this.activePixels.get(key);
        
        if (!pixel && this.pixelPool.length > 0) {
            pixel = this.pixelPool.pop();
            this.activePixels.set(key, pixel);
        }
        
        if (pixel) {
            pixel.x = x * 10;
            pixel.y = y * 10;
            pixel.tint = parseInt(color.replace('#', '0x'));
            pixel.visible = true;
        }
    }
    
    setupTouchGestures() {
        // Hammer.js for better touch handling
        const hammer = new Hammer(this.app.view);
        
        // Enable pinch and pan
        hammer.get('pinch').set({ enable: true });
        hammer.get('pan').set({ direction: Hammer.DIRECTION_ALL });
        
        // Long press for pixel preview
        hammer.on('press', (e) => {
            const point = this.viewport.toWorld(e.center);
            const gridX = Math.floor(point.x / 10);
            const gridY = Math.floor(point.y / 10);
            this.showPixelPreview(gridX, gridY);
        });
        
        // Double tap to zoom
        hammer.on('doubletap', (e) => {
            const point = this.viewport.toWorld(e.center);
            this.viewport.animate({
                scale: this.viewport.scale.x > 2 ? 1 : 3,
                position: point,
                time: 300,
                ease: 'easeInOutSine'
            });
        });
    }
    
    navigateToCorner(corner) {
        // FIXED: Proper corner navigation with animation
        const worldWidth = this.config.width * 10;
        const worldHeight = this.config.height * 10;
        
        const positions = {
            'top-left': { x: 100, y: 100 },
            'top-right': { x: worldWidth - 100, y: 100 },
            'bottom-left': { x: 100, y: worldHeight - 100 },
            'bottom-right': { x: worldWidth - 100, y: worldHeight - 100 },
            'center': { x: worldWidth / 2, y: worldHeight / 2 }
        };
        
        const target = positions[corner];
        if (target) {
            // Smooth animated navigation
            this.viewport.animate({
                position: target,
                scale: 2, // Zoom in slightly for better pixel selection
                time: 500,
                ease: 'easeInOutSine',
                callbackOnComplete: () => {
                    console.log(`Navigated to ${corner}`);
                }
            });
        }
    }
    
    setupNavigationButtons() {
        // Create navigation UI overlay
        const navContainer = document.createElement('div');
        navContainer.className = 'pixi-nav-buttons';
        navContainer.innerHTML = `
            <style>
                .pixi-nav-buttons {
                    position: absolute;
                    top: 10px;
                    right: 10px;
                    display: flex;
                    flex-direction: column;
                    gap: 5px;
                    z-index: 1000;
                }
                .pixi-nav-btn {
                    width: 40px;
                    height: 40px;
                    border: 2px solid #333;
                    background: white;
                    cursor: pointer;
                    font-size: 20px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border-radius: 5px;
                    transition: all 0.2s;
                }
                .pixi-nav-btn:hover {
                    background: #f0f0f0;
                    transform: scale(1.1);
                }
                .pixi-nav-btn:active {
                    transform: scale(0.95);
                }
            </style>
            <button class="pixi-nav-btn" data-corner="top-left" title="Top Left">↖️</button>
            <button class="pixi-nav-btn" data-corner="top-right" title="Top Right">↗️</button>
            <button class="pixi-nav-btn" data-corner="center" title="Center">⊙</button>
            <button class="pixi-nav-btn" data-corner="bottom-left" title="Bottom Left">↙️</button>
            <button class="pixi-nav-btn" data-corner="bottom-right" title="Bottom Right">↘️</button>
        `;
        
        this.container.appendChild(navContainer);
        
        // Add click handlers
        navContainer.querySelectorAll('.pixi-nav-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.navigateToCorner(btn.dataset.corner);
            });
        });
    }
    
    setupMinimap() {
        // Create minimap with PIXI
        const minimapSize = 150;
        const minimapContainer = new PIXI.Container();
        minimapContainer.x = 10;
        minimapContainer.y = window.innerHeight - minimapSize - 10;
        
        // Minimap background
        const minimapBg = new PIXI.Graphics();
        minimapBg.beginFill(0xf0f0f0, 0.8);
        minimapBg.drawRect(0, 0, minimapSize, minimapSize);
        minimapBg.endFill();
        minimapContainer.addChild(minimapBg);
        
        // Minimap pixels
        this.minimapPixels = new PIXI.Container();
        const scale = minimapSize / Math.max(this.config.width, this.config.height);
        this.minimapPixels.scale.set(scale / 10);
        minimapContainer.addChild(this.minimapPixels);
        
        // Viewport indicator
        this.minimapViewport = new PIXI.Graphics();
        minimapContainer.addChild(this.minimapViewport);
        
        // Make minimap interactive
        minimapBg.interactive = true;
        minimapBg.on('pointerdown', (e) => {
            const pos = e.data.getLocalPosition(minimapBg);
            const worldX = (pos.x / minimapSize) * this.config.width * 10;
            const worldY = (pos.y / minimapSize) * this.config.height * 10;
            
            this.viewport.animate({
                position: { x: worldX, y: worldY },
                time: 300,
                ease: 'easeInOutSine'
            });
        });
        
        this.app.stage.addChild(minimapContainer);
        this.minimapContainer = minimapContainer;
    }
    
    updateGridVisibility() {
        // Show grid only when zoomed in
        const showGrid = this.viewport.scale.x > 2;
        
        if (showGrid && !this.gridVisible) {
            this.drawGrid();
            this.gridVisible = true;
        } else if (!showGrid && this.gridVisible) {
            this.gridGraphics.clear();
            this.gridVisible = false;
        }
    }
    
    drawGrid() {
        const g = this.gridGraphics;
        g.clear();
        g.lineStyle(0.5, 0xcccccc, 0.3);
        
        // Only draw visible grid lines (performance optimization)
        const bounds = this.viewport.getVisibleBounds();
        const startX = Math.floor(bounds.x / 10) * 10;
        const endX = Math.ceil(bounds.right / 10) * 10;
        const startY = Math.floor(bounds.y / 10) * 10;
        const endY = Math.ceil(bounds.bottom / 10) * 10;
        
        // Vertical lines
        for (let x = startX; x <= endX; x += 10) {
            g.moveTo(x, startY);
            g.lineTo(x, endY);
        }
        
        // Horizontal lines
        for (let y = startY; y <= endY; y += 10) {
            g.moveTo(startX, y);
            g.lineTo(endX, y);
        }
    }
    
    showPixelPreview(x, y) {
        // Create preview overlay
        const preview = new PIXI.Container();
        
        // Highlight square
        const highlight = new PIXI.Graphics();
        highlight.lineStyle(2, 0xff0000);
        highlight.drawRect(x * 10, y * 10, 10, 10);
        preview.addChild(highlight);
        
        // Info bubble
        const text = new PIXI.Text(`(${x}, ${y})`, {
            fontSize: 14,
            fill: 0x000000,
            backgroundColor: 0xffffff,
            padding: 5
        });
        text.x = x * 10 + 15;
        text.y = y * 10 - 20;
        preview.addChild(text);
        
        this.viewport.addChild(preview);
        
        // Remove after 2 seconds
        setTimeout(() => {
            this.viewport.removeChild(preview);
        }, 2000);
    }
    
    setupWebSocket() {
        // WebSocket for real-time updates (if backend supports it)
        if (this.config.websocketUrl) {
            this.ws = new WebSocket(this.config.websocketUrl);
            
            this.ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'pixel_placed') {
                    this.placePixel(data.x, data.y, data.color);
                    this.addToMinimap(data.x, data.y, data.color);
                }
            };
            
            this.ws.onerror = () => {
                console.log('WebSocket error, falling back to polling');
                this.startPolling();
            };
        } else {
            this.startPolling();
        }
    }
    
    startPolling() {
        // Fallback to polling if WebSocket not available
        setInterval(() => {
            this.loadCanvasState();
        }, 2000);
    }
    
    loadCanvasState() {
        fetch(`/vibe-coding/api/canvas-state/${this.config.canvasId}/`)
            .then(res => res.json())
            .then(data => {
                Object.entries(data.pixels).forEach(([key, pixel]) => {
                    const [x, y] = key.split(',').map(Number);
                    this.placePixel(x, y, pixel.color);
                    this.addToMinimap(x, y, pixel.color);
                });
            });
    }
    
    addToMinimap(x, y, color) {
        // Update minimap
        const pixel = new PIXI.Graphics();
        pixel.beginFill(parseInt(color.replace('#', '0x')));
        pixel.drawRect(x * 10, y * 10, 10, 10);
        pixel.endFill();
        this.minimapPixels.addChild(pixel);
    }
    
    update() {
        // Update minimap viewport indicator
        if (this.minimapViewport) {
            const bounds = this.viewport.getVisibleBounds();
            const scale = 150 / Math.max(this.config.width * 10, this.config.height * 10);
            
            this.minimapViewport.clear();
            this.minimapViewport.lineStyle(2, 0xff0000);
            this.minimapViewport.drawRect(
                bounds.x * scale,
                bounds.y * scale,
                (bounds.right - bounds.x) * scale,
                (bounds.bottom - bounds.y) * scale
            );
        }
    }
    
    onResize() {
        this.app.renderer.resize(window.innerWidth, window.innerHeight);
        this.viewport.resize(window.innerWidth, window.innerHeight);
        
        // Reposition minimap
        if (this.minimapContainer) {
            this.minimapContainer.y = window.innerHeight - 160;
        }
    }
    
    handlePixelClick(e) {
        const point = this.viewport.toWorld(e.data.global);
        const gridX = Math.floor(point.x / 10);
        const gridY = Math.floor(point.y / 10);
        
        // Check bounds
        if (gridX >= 0 && gridX < this.config.width && 
            gridY >= 0 && gridY < this.config.height) {
            
            // Send pixel placement request
            this.sendPixelPlacement(gridX, gridY, this.selectedColor);
        }
    }
    
    sendPixelPlacement(x, y, color) {
        fetch('/vibe-coding/api/place-pixel/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.getCSRFToken()
            },
            body: JSON.stringify({
                canvas_id: this.config.canvasId,
                x: x,
                y: y,
                color: color
            })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                this.placePixel(x, y, color);
                this.addToMinimap(x, y, color);
            } else {
                console.error('Failed to place pixel:', data.message);
            }
        });
    }
    
    getCSRFToken() {
        return document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
    }
}

// Usage
const pixelWar = new PixelWarPixi('canvas-container', {
    width: 100,
    height: 100,
    canvasId: 1,
    websocketUrl: null // Or 'ws://localhost:8000/ws/pixel-war/' if available
});

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = PixelWarPixi;
}