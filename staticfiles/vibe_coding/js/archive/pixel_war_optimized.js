// Optimized Pixel War Implementation with WebGL/Canvas hybrid approach
class PixelWarOptimized {
    constructor(canvasId, config) {
        this.canvas = document.getElementById(canvasId);
        this.config = config;
        
        // Try WebGL first, fallback to 2D
        this.ctx = this.canvas.getContext('webgl') || 
                   this.canvas.getContext('experimental-webgl') || 
                   this.canvas.getContext('2d');
        
        this.isWebGL = this.ctx instanceof WebGLRenderingContext;
        
        // Simplified viewport system
        this.viewport = {
            x: 0,
            y: 0,
            width: this.canvas.width,
            height: this.canvas.height,
            scale: 1,
            targetScale: 1,
            minScale: 0.5,
            maxScale: 10
        };
        
        // Pixel data structure with spatial indexing
        this.pixelQuadTree = new QuadTree({
            x: 0,
            y: 0,
            width: config.width,
            height: config.height
        });
        
        // Offscreen canvas for pixel layer
        this.pixelLayer = document.createElement('canvas');
        this.pixelLayer.width = config.width * 10;
        this.pixelLayer.height = config.height * 10;
        this.pixelCtx = this.pixelLayer.getContext('2d');
        
        // Dirty rectangle tracking
        this.dirtyRects = [];
        
        // Touch/mouse handling
        this.input = new InputHandler(this);
        
        // Performance monitoring
        this.frameTime = 0;
        this.lastFrameTime = performance.now();
        
        this.init();
    }
    
    init() {
        if (this.isWebGL) {
            this.initWebGL();
        } else {
            this.init2D();
        }
        
        // Start render loop
        this.render();
    }
    
    initWebGL() {
        // WebGL shader setup for massive performance boost
        const vertexShader = `
            attribute vec2 a_position;
            uniform vec2 u_resolution;
            uniform vec2 u_translation;
            uniform float u_scale;
            
            void main() {
                vec2 position = (a_position + u_translation) * u_scale;
                vec2 clipSpace = ((position / u_resolution) * 2.0) - 1.0;
                gl_Position = vec4(clipSpace * vec2(1, -1), 0, 1);
            }
        `;
        
        const fragmentShader = `
            precision mediump float;
            uniform vec4 u_color;
            
            void main() {
                gl_FragColor = u_color;
            }
        `;
        
        // Compile shaders
        this.program = this.createShaderProgram(vertexShader, fragmentShader);
    }
    
    init2D() {
        // Optimized 2D context settings
        this.ctx.imageSmoothingEnabled = false;
        this.ctx.mozImageSmoothingEnabled = false;
        this.ctx.webkitImageSmoothingEnabled = false;
    }
    
    // Viewport navigation - simplified and fluid
    panTo(x, y, smooth = true) {
        if (smooth) {
            // Smooth interpolation
            const animate = () => {
                this.viewport.x += (x - this.viewport.x) * 0.1;
                this.viewport.y += (y - this.viewport.y) * 0.1;
                
                if (Math.abs(x - this.viewport.x) > 0.5 || 
                    Math.abs(y - this.viewport.y) > 0.5) {
                    requestAnimationFrame(animate);
                }
            };
            animate();
        } else {
            this.viewport.x = x;
            this.viewport.y = y;
        }
    }
    
    // Navigate to corners with proper bounds
    navigateToCorner(corner) {
        const pixelWidth = this.config.width * 10;
        const pixelHeight = this.config.height * 10;
        const viewWidth = this.viewport.width / this.viewport.scale;
        const viewHeight = this.viewport.height / this.viewport.scale;
        
        const positions = {
            'top-left': { x: 0, y: 0 },
            'top-right': { x: pixelWidth - viewWidth, y: 0 },
            'bottom-left': { x: 0, y: pixelHeight - viewHeight },
            'bottom-right': { x: pixelWidth - viewWidth, y: pixelHeight - viewHeight },
            'center': { x: (pixelWidth - viewWidth) / 2, y: (pixelHeight - viewHeight) / 2 }
        };
        
        const target = positions[corner];
        if (target) {
            // Ensure bounds
            target.x = Math.max(0, Math.min(target.x, pixelWidth - viewWidth));
            target.y = Math.max(0, Math.min(target.y, pixelHeight - viewHeight));
            this.panTo(target.x, target.y);
        }
    }
    
    // Optimized zoom with focal point
    zoomAt(x, y, deltaScale) {
        const oldScale = this.viewport.scale;
        const newScale = Math.max(this.viewport.minScale, 
                        Math.min(this.viewport.maxScale, 
                        oldScale * (1 + deltaScale)));
        
        if (newScale !== oldScale) {
            // Adjust viewport to keep point under cursor
            const scaleRatio = newScale / oldScale;
            this.viewport.x = x - (x - this.viewport.x) * scaleRatio;
            this.viewport.y = y - (y - this.viewport.y) * scaleRatio;
            this.viewport.scale = newScale;
            
            // Mark entire viewport as dirty
            this.markDirty(0, 0, this.canvas.width, this.canvas.height);
        }
    }
    
    // Dirty rectangle management
    markDirty(x, y, width, height) {
        this.dirtyRects.push({ x, y, width, height });
    }
    
    // Optimized render with culling
    render() {
        const now = performance.now();
        this.frameTime = now - this.lastFrameTime;
        this.lastFrameTime = now;
        
        // Only redraw if dirty
        if (this.dirtyRects.length > 0) {
            if (this.isWebGL) {
                this.renderWebGL();
            } else {
                this.render2D();
            }
            this.dirtyRects = [];
        }
        
        // Show FPS
        this.renderStats();
        
        requestAnimationFrame(() => this.render());
    }
    
    render2D() {
        const ctx = this.ctx;
        
        // Clear only dirty regions
        this.dirtyRects.forEach(rect => {
            ctx.clearRect(rect.x, rect.y, rect.width, rect.height);
        });
        
        // Calculate visible bounds
        const startX = Math.floor(this.viewport.x / 10);
        const startY = Math.floor(this.viewport.y / 10);
        const endX = Math.ceil((this.viewport.x + this.viewport.width / this.viewport.scale) / 10);
        const endY = Math.ceil((this.viewport.y + this.viewport.height / this.viewport.scale) / 10);
        
        // Only query visible pixels from quadtree
        const visiblePixels = this.pixelQuadTree.query({
            x: startX,
            y: startY,
            width: endX - startX,
            height: endY - startY
        });
        
        // Draw pixel layer
        ctx.save();
        ctx.scale(this.viewport.scale, this.viewport.scale);
        ctx.translate(-this.viewport.x, -this.viewport.y);
        
        // Batch draw pixels
        visiblePixels.forEach(pixel => {
            ctx.fillStyle = pixel.color;
            ctx.fillRect(pixel.x * 10, pixel.y * 10, 10, 10);
        });
        
        // Draw grid if zoomed in
        if (this.viewport.scale > 2) {
            this.drawGrid(ctx, startX, startY, endX, endY);
        }
        
        ctx.restore();
    }
    
    drawGrid(ctx, startX, startY, endX, endY) {
        ctx.strokeStyle = 'rgba(200, 200, 200, 0.3)';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        
        // Vertical lines
        for (let x = startX; x <= endX; x++) {
            ctx.moveTo(x * 10, startY * 10);
            ctx.lineTo(x * 10, endY * 10);
        }
        
        // Horizontal lines
        for (let y = startY; y <= endY; y++) {
            ctx.moveTo(startX * 10, y * 10);
            ctx.lineTo(endX * 10, y * 10);
        }
        
        ctx.stroke();
    }
    
    renderStats() {
        const fps = Math.round(1000 / this.frameTime);
        const ctx = this.ctx;
        ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
        ctx.fillRect(10, 10, 100, 30);
        ctx.fillStyle = '#00ff00';
        ctx.font = '14px monospace';
        ctx.fillText(`FPS: ${fps}`, 20, 30);
    }
    
    // Helper to create WebGL shader program
    createShaderProgram(vertexSource, fragmentSource) {
        const gl = this.ctx;
        
        const vertexShader = gl.createShader(gl.VERTEX_SHADER);
        gl.shaderSource(vertexShader, vertexSource);
        gl.compileShader(vertexShader);
        
        const fragmentShader = gl.createShader(gl.FRAGMENT_SHADER);
        gl.shaderSource(fragmentShader, fragmentSource);
        gl.compileShader(fragmentShader);
        
        const program = gl.createProgram();
        gl.attachShader(program, vertexShader);
        gl.attachShader(program, fragmentShader);
        gl.linkProgram(program);
        
        return program;
    }
}

// Spatial indexing for efficient pixel queries
class QuadTree {
    constructor(bounds, maxObjects = 10, maxLevels = 5, level = 0) {
        this.bounds = bounds;
        this.maxObjects = maxObjects;
        this.maxLevels = maxLevels;
        this.level = level;
        this.objects = [];
        this.nodes = [];
    }
    
    insert(object) {
        if (this.nodes.length > 0) {
            const index = this.getIndex(object);
            if (index !== -1) {
                this.nodes[index].insert(object);
                return;
            }
        }
        
        this.objects.push(object);
        
        if (this.objects.length > this.maxObjects && this.level < this.maxLevels) {
            if (this.nodes.length === 0) {
                this.split();
            }
            
            let i = 0;
            while (i < this.objects.length) {
                const index = this.getIndex(this.objects[i]);
                if (index !== -1) {
                    this.nodes[index].insert(this.objects.splice(i, 1)[0]);
                } else {
                    i++;
                }
            }
        }
    }
    
    query(range) {
        const foundObjects = [];
        
        if (!this.intersects(range)) {
            return foundObjects;
        }
        
        for (const object of this.objects) {
            if (this.contains(range, object)) {
                foundObjects.push(object);
            }
        }
        
        if (this.nodes.length > 0) {
            for (const node of this.nodes) {
                foundObjects.push(...node.query(range));
            }
        }
        
        return foundObjects;
    }
    
    split() {
        const x = this.bounds.x;
        const y = this.bounds.y;
        const halfWidth = this.bounds.width / 2;
        const halfHeight = this.bounds.height / 2;
        
        this.nodes[0] = new QuadTree({
            x: x + halfWidth,
            y: y,
            width: halfWidth,
            height: halfHeight
        }, this.maxObjects, this.maxLevels, this.level + 1);
        
        this.nodes[1] = new QuadTree({
            x: x,
            y: y,
            width: halfWidth,
            height: halfHeight
        }, this.maxObjects, this.maxLevels, this.level + 1);
        
        this.nodes[2] = new QuadTree({
            x: x,
            y: y + halfHeight,
            width: halfWidth,
            height: halfHeight
        }, this.maxObjects, this.maxLevels, this.level + 1);
        
        this.nodes[3] = new QuadTree({
            x: x + halfWidth,
            y: y + halfHeight,
            width: halfWidth,
            height: halfHeight
        }, this.maxObjects, this.maxLevels, this.level + 1);
    }
    
    getIndex(object) {
        const x = this.bounds.x;
        const y = this.bounds.y;
        const halfWidth = this.bounds.width / 2;
        const halfHeight = this.bounds.height / 2;
        
        const inTop = object.y < y + halfHeight;
        const inBottom = object.y >= y + halfHeight;
        const inLeft = object.x < x + halfWidth;
        const inRight = object.x >= x + halfWidth;
        
        if (inTop && inRight) return 0;
        if (inTop && inLeft) return 1;
        if (inBottom && inLeft) return 2;
        if (inBottom && inRight) return 3;
        
        return -1;
    }
    
    intersects(range) {
        return !(
            range.x > this.bounds.x + this.bounds.width ||
            range.x + range.width < this.bounds.x ||
            range.y > this.bounds.y + this.bounds.height ||
            range.y + range.height < this.bounds.y
        );
    }
    
    contains(range, object) {
        return object.x >= range.x &&
               object.x <= range.x + range.width &&
               object.y >= range.y &&
               object.y <= range.y + range.height;
    }
}

// Unified input handler for mouse and touch
class InputHandler {
    constructor(pixelWar) {
        this.pw = pixelWar;
        this.touches = [];
        this.mouseDown = false;
        this.lastX = 0;
        this.lastY = 0;
        
        this.setupEventListeners();
    }
    
    setupEventListeners() {
        const canvas = this.pw.canvas;
        
        // Mouse events
        canvas.addEventListener('mousedown', this.handleStart.bind(this));
        canvas.addEventListener('mousemove', this.handleMove.bind(this));
        canvas.addEventListener('mouseup', this.handleEnd.bind(this));
        canvas.addEventListener('wheel', this.handleWheel.bind(this));
        
        // Touch events
        canvas.addEventListener('touchstart', this.handleStart.bind(this));
        canvas.addEventListener('touchmove', this.handleMove.bind(this));
        canvas.addEventListener('touchend', this.handleEnd.bind(this));
        
        // Prevent default touch behaviors
        canvas.addEventListener('touchstart', e => e.preventDefault());
    }
    
    handleStart(e) {
        if (e.type === 'mousedown') {
            this.mouseDown = true;
            this.lastX = e.clientX;
            this.lastY = e.clientY;
        } else if (e.type === 'touchstart') {
            this.touches = Array.from(e.touches);
            if (this.touches.length === 1) {
                this.lastX = this.touches[0].clientX;
                this.lastY = this.touches[0].clientY;
            }
        }
    }
    
    handleMove(e) {
        if (e.type === 'mousemove' && this.mouseDown) {
            const dx = e.clientX - this.lastX;
            const dy = e.clientY - this.lastY;
            this.pw.viewport.x -= dx / this.pw.viewport.scale;
            this.pw.viewport.y -= dy / this.pw.viewport.scale;
            this.pw.markDirty(0, 0, this.pw.canvas.width, this.pw.canvas.height);
            this.lastX = e.clientX;
            this.lastY = e.clientY;
        } else if (e.type === 'touchmove') {
            const newTouches = Array.from(e.touches);
            
            if (newTouches.length === 1 && this.touches.length === 1) {
                // Pan
                const dx = newTouches[0].clientX - this.lastX;
                const dy = newTouches[0].clientY - this.lastY;
                this.pw.viewport.x -= dx / this.pw.viewport.scale;
                this.pw.viewport.y -= dy / this.pw.viewport.scale;
                this.pw.markDirty(0, 0, this.pw.canvas.width, this.pw.canvas.height);
                this.lastX = newTouches[0].clientX;
                this.lastY = newTouches[0].clientY;
            } else if (newTouches.length === 2 && this.touches.length === 2) {
                // Pinch zoom
                const oldDist = this.getTouchDistance(this.touches);
                const newDist = this.getTouchDistance(newTouches);
                const scale = newDist / oldDist;
                
                const centerX = (newTouches[0].clientX + newTouches[1].clientX) / 2;
                const centerY = (newTouches[0].clientY + newTouches[1].clientY) / 2;
                
                this.pw.zoomAt(centerX, centerY, scale - 1);
            }
            
            this.touches = newTouches;
        }
    }
    
    handleEnd(e) {
        if (e.type === 'mouseup') {
            this.mouseDown = false;
        } else if (e.type === 'touchend') {
            this.touches = Array.from(e.touches);
        }
    }
    
    handleWheel(e) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        this.pw.zoomAt(e.clientX, e.clientY, delta);
    }
    
    getTouchDistance(touches) {
        const dx = touches[0].clientX - touches[1].clientX;
        const dy = touches[0].clientY - touches[1].clientY;
        return Math.sqrt(dx * dx + dy * dy);
    }
}

// Export for use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { PixelWarOptimized, QuadTree, InputHandler };
}