class PixelWar {
    constructor(canvasId, config) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.config = config;
        
        this.pixelSize = 10;
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.isDragging = false;
        this.dragStartX = 0;
        this.dragStartY = 0;
        
        // Smooth scrolling properties
        this.targetOffsetX = 0;
        this.targetOffsetY = 0;
        this.velocityX = 0;
        this.velocityY = 0;
        this.lastMouseX = 0;
        this.lastMouseY = 0;
        this.animationFrame = null;
        this.friction = 0.92; // Momentum friction
        this.smoothness = 0.15; // Lerp factor for smooth movement
        
        this.selectedColor = '#000000';
        this.pixels = {};
        this.cooldownEndTime = null;
        this.updateInterval = null;
        this.pixelsRemaining = null;
        
        // Determine base URL for API calls
        this.apiBaseUrl = this.getApiBaseUrl();
        
        // Set cooldown based on authentication
        this.cooldownSeconds = config.isAuthenticated ? config.registeredCooldown : config.anonymousCooldown;
        this.maxPixelsPerMinute = config.isAuthenticated ? config.registeredPixelsPerMinute : config.anonymousPixelsPerMinute;
        
        this.init();
    }
    
    getApiBaseUrl() {
        // Remove language prefix from current path
        const path = window.location.pathname;
        const langPattern = /^\/[a-z]{2}\//;
        if (langPattern.test(path)) {
            // Has language prefix, remove it for API calls
            return '';
        }
        return '';
    }
    
    init() {
        this.setupCanvas();
        this.setupMinimap();
        this.setupEventListeners();
        this.loadCanvasState();
        this.startUpdateLoop();
        this.loadRecentActivity();
        this.updatePixelsRemaining();
    }
    
    setupCanvas() {
        this.canvas.width = this.config.width * this.pixelSize;
        this.canvas.height = this.config.height * this.pixelSize;
        this.drawGrid();
    }
    
    setupMinimap() {
        // Create minimap container
        const minimapContainer = document.createElement('div');
        minimapContainer.className = 'minimap-container';
        minimapContainer.innerHTML = `
            <canvas id="minimap" width="150" height="150"></canvas>
            <div class="minimap-viewport"></div>
            <button class="minimap-toggle" aria-label="Toggle minimap">🗺️</button>
        `;
        
        // Add to canvas wrapper
        const canvasWrapper = document.querySelector('.canvas-wrapper');
        if (canvasWrapper) {
            canvasWrapper.appendChild(minimapContainer);
        }
        
        this.minimap = document.getElementById('minimap');
        this.minimapCtx = this.minimap.getContext('2d');
        this.minimapViewport = document.querySelector('.minimap-viewport');
        
        // Set minimap dimensions
        const scale = Math.min(150 / this.config.width, 150 / this.config.height);
        this.minimapScale = scale;
        this.minimap.width = this.config.width * scale;
        this.minimap.height = this.config.height * scale;
        
        // Minimap toggle
        const toggle = document.querySelector('.minimap-toggle');
        toggle.addEventListener('click', () => {
            minimapContainer.classList.toggle('collapsed');
        });
        
        // Click on minimap to navigate
        this.minimap.addEventListener('click', (e) => {
            const rect = this.minimap.getBoundingClientRect();
            const x = (e.clientX - rect.left) / this.minimapScale;
            const y = (e.clientY - rect.top) / this.minimapScale;
            
            // Center view on clicked position
            const canvasRect = this.canvas.getBoundingClientRect();
            const viewportWidth = canvasRect.width / (this.pixelSize * this.zoom);
            const viewportHeight = canvasRect.height / (this.pixelSize * this.zoom);
            
            this.targetOffsetX = -(x - viewportWidth / 2);
            this.targetOffsetY = -(y - viewportHeight / 2);
            
            // Constrain offsets
            this.targetOffsetX = this.constrainOffset(this.targetOffsetX, this.config.width, viewportWidth);
            this.targetOffsetY = this.constrainOffset(this.targetOffsetY, this.config.height, viewportHeight);
            
            if (!this.animationFrame) {
                this.startSmoothScroll();
            }
        });
    }
    
    setupEventListeners() {
        this.canvas.addEventListener('click', this.handleCanvasClick.bind(this));
        this.canvas.addEventListener('mousemove', this.handleMouseMove.bind(this));
        this.canvas.addEventListener('mousedown', this.handleMouseDown.bind(this));
        this.canvas.addEventListener('mouseup', this.handleMouseUp.bind(this));
        this.canvas.addEventListener('mouseleave', this.handleMouseLeave.bind(this));
        this.canvas.addEventListener('wheel', this.handleWheel.bind(this), { passive: false });
        
        // Prevent context menu on right click
        this.canvas.addEventListener('contextmenu', (e) => e.preventDefault());
        
        // Keyboard controls for better navigation
        this.spacePressed = false;
        document.addEventListener('keydown', (e) => {
            if (e.code === 'Space' && !e.target.matches('input, textarea')) {
                e.preventDefault();
                this.spacePressed = true;
                this.canvas.style.cursor = 'grab';
                // Update hint
                const hint = document.getElementById('panHint');
                if (hint) {
                    hint.innerHTML = '<span class="desktop-hint">🖱️ Click and drag to pan (Space key active)</span>';
                    hint.style.display = 'block';
                }
            }
            // Arrow key navigation
            const moveSpeed = 50 / (this.pixelSize * this.zoom);
            const rect = this.canvas.getBoundingClientRect();
            const viewportWidth = rect.width / (this.pixelSize * this.zoom);
            const viewportHeight = rect.height / (this.pixelSize * this.zoom);
            
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                this.targetOffsetY += moveSpeed;
                this.targetOffsetY = this.constrainOffset(this.targetOffsetY, this.config.height, viewportHeight);
                if (!this.animationFrame) this.startSmoothScroll();
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                this.targetOffsetY -= moveSpeed;
                this.targetOffsetY = this.constrainOffset(this.targetOffsetY, this.config.height, viewportHeight);
                if (!this.animationFrame) this.startSmoothScroll();
            }
            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                this.targetOffsetX += moveSpeed;
                this.targetOffsetX = this.constrainOffset(this.targetOffsetX, this.config.width, viewportWidth);
                if (!this.animationFrame) this.startSmoothScroll();
            }
            if (e.key === 'ArrowRight') {
                e.preventDefault();
                this.targetOffsetX -= moveSpeed;
                this.targetOffsetX = this.constrainOffset(this.targetOffsetX, this.config.width, viewportWidth);
                if (!this.animationFrame) this.startSmoothScroll();
            }
            // Zoom with + and - keys
            if (e.key === '+' || e.key === '=') {
                e.preventDefault();
                this.adjustZoom(0.2);
            }
            if (e.key === '-' || e.key === '_') {
                e.preventDefault();
                this.adjustZoom(-0.2);
            }
        });
        
        document.addEventListener('keyup', (e) => {
            if (e.code === 'Space') {
                this.spacePressed = false;
                this.canvas.style.cursor = 'crosshair';
                // Reset hint
                const hint = document.getElementById('panHint');
                if (hint) {
                    hint.innerHTML = '<span class="desktop-hint">💡 Scroll to pan, Ctrl+Scroll to zoom, Space+drag for pan mode</span><span class="mobile-hint">💡 Drag to pan, pinch to zoom</span>';
                    setTimeout(() => hint.style.display = 'none', 3000);
                }
            }
        });
        
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.selectColor(e.target.dataset.color);
                this.highlightSelectedColor(e.target);
            });
        });
        
        const colorPicker = document.getElementById('colorPicker');
        if (colorPicker) {
            colorPicker.addEventListener('change', (e) => {
                this.selectColor(e.target.value);
            });
        }
        
        // Zoom controls - check for both mobile and desktop IDs
        const zoomIn = document.getElementById('zoomIn') || document.getElementById('zoomInDesktop');
        const zoomOut = document.getElementById('zoomOut') || document.getElementById('zoomOutDesktop');
        const zoomReset = document.getElementById('zoomReset') || document.getElementById('zoomResetDesktop');
        
        if (zoomIn) zoomIn.addEventListener('click', () => this.adjustZoom(0.2));
        if (zoomOut) zoomOut.addEventListener('click', () => this.adjustZoom(-0.2));
        if (zoomReset) zoomReset.addEventListener('click', () => this.resetZoom());
        
        // Corner navigation buttons
        const navTopLeft = document.getElementById('navTopLeft');
        const navTopRight = document.getElementById('navTopRight');
        const navBottomLeft = document.getElementById('navBottomLeft');
        const navBottomRight = document.getElementById('navBottomRight');
        
        if (navTopLeft) navTopLeft.addEventListener('click', () => this.navigateToCorner('top-left'));
        if (navTopRight) navTopRight.addEventListener('click', () => this.navigateToCorner('top-right'));
        if (navBottomLeft) navBottomLeft.addEventListener('click', () => this.navigateToCorner('bottom-left'));
        if (navBottomRight) navBottomRight.addEventListener('click', () => this.navigateToCorner('bottom-right'));
        
        // Fullscreen toggle for desktop
        const fullscreenBtn = document.getElementById('fullscreenToggle');
        if (fullscreenBtn) {
            fullscreenBtn.addEventListener('click', () => this.toggleFullscreen());
        }
    }
    
    handleCanvasClick(e) {
        if (this.isDragging) return;
        
        const rect = this.canvas.getBoundingClientRect();
        // Correct calculation accounting for negative offsets
        const canvasX = (e.clientX - rect.left) / (this.pixelSize * this.zoom);
        const canvasY = (e.clientY - rect.top) / (this.pixelSize * this.zoom);
        const x = Math.floor(canvasX - this.offsetX);
        const y = Math.floor(canvasY - this.offsetY);
        
        if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
            this.placePixel(x, y);
        }
    }
    
    handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        // Correct calculation accounting for negative offsets
        const canvasX = (e.clientX - rect.left) / (this.pixelSize * this.zoom);
        const canvasY = (e.clientY - rect.top) / (this.pixelSize * this.zoom);
        const x = Math.floor(canvasX - this.offsetX);
        const y = Math.floor(canvasY - this.offsetY);
        
        // Update cursor based on mode
        if (this.spacePressed && !this.isDragging) {
            this.canvas.style.cursor = 'grab';
        }
        
        if (this.isDragging) {
            // Calculate new offsets with smoother movement
            const viewportWidth = rect.width / (this.pixelSize * this.zoom);
            const viewportHeight = rect.height / (this.pixelSize * this.zoom);
            
            let newOffsetX = (e.clientX - this.dragStartX) / (this.pixelSize * this.zoom);
            let newOffsetY = (e.clientY - this.dragStartY) / (this.pixelSize * this.zoom);
            
            // Apply boundary constraints
            this.targetOffsetX = this.constrainOffset(newOffsetX, this.config.width, viewportWidth);
            this.targetOffsetY = this.constrainOffset(newOffsetY, this.config.height, viewportHeight);
            
            // Calculate velocity for momentum
            this.velocityX = e.clientX - this.lastMouseX;
            this.velocityY = e.clientY - this.lastMouseY;
            this.lastMouseX = e.clientX;
            this.lastMouseY = e.clientY;
            
            // Start smooth animation if not already running
            if (!this.animationFrame) {
                this.startSmoothScroll();
            }
        } else if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
            this.showPixelTooltip(e, x, y);
            if (!this.spacePressed) {
                this.canvas.style.cursor = 'crosshair';
            }
        } else {
            this.hidePixelTooltip();
            if (!this.spacePressed) {
                this.canvas.style.cursor = 'default';
            }
        }
    }
    
    handleMouseDown(e) {
        // Space bar + left click, middle click, or right click for dragging
        const isPanMode = this.spacePressed || e.button === 2 || e.button === 1;
        
        if (isPanMode) {
            e.preventDefault();
            this.isDragging = true;
            this.dragStartX = e.clientX - this.offsetX * this.pixelSize * this.zoom;
            this.dragStartY = e.clientY - this.offsetY * this.pixelSize * this.zoom;
            this.lastMouseX = e.clientX;
            this.lastMouseY = e.clientY;
            this.velocityX = 0;
            this.velocityY = 0;
            this.canvas.style.cursor = 'grabbing';
            
            // Stop any ongoing momentum
            if (this.animationFrame) {
                cancelAnimationFrame(this.animationFrame);
                this.animationFrame = null;
            }
        }
    }
    
    handleMouseUp(e) {
        if (this.isDragging) {
            this.isDragging = false;
            
            // Apply momentum if there's velocity
            if (Math.abs(this.velocityX) > 1 || Math.abs(this.velocityY) > 1) {
                this.applyMomentum();
            }
        }
        this.canvas.style.cursor = 'crosshair';
    }
    
    handleMouseLeave(e) {
        if (this.isDragging) {
            this.isDragging = false;
            // Apply momentum when mouse leaves while dragging
            if (Math.abs(this.velocityX) > 1 || Math.abs(this.velocityY) > 1) {
                this.applyMomentum();
            }
        }
        this.hidePixelTooltip();
        this.canvas.style.cursor = 'default';
    }
    
    handleWheel(e) {
        e.preventDefault();
        
        const rect = this.canvas.getBoundingClientRect();
        
        // Check if Ctrl/Cmd is held for zoom, otherwise pan
        if (e.ctrlKey || e.metaKey) {
            // Zoom behavior - more sensitive zoom
            const zoomSensitivity = e.shiftKey ? 0.05 : 0.15; // Shift for fine control
            const delta = e.deltaY > 0 ? -zoomSensitivity : zoomSensitivity;
            
            // Calculate mouse position in grid coordinates
            const canvasX = (e.clientX - rect.left) / (this.pixelSize * this.zoom);
            const canvasY = (e.clientY - rect.top) / (this.pixelSize * this.zoom);
            const mouseX = canvasX - this.offsetX;
            const mouseY = canvasY - this.offsetY;
            
            this.adjustZoomAtPoint(delta, mouseX, mouseY);
        } else {
            // Pan behavior with scroll wheel
            const panSpeed = 30 / (this.pixelSize * this.zoom);
            
            if (e.shiftKey) {
                // Horizontal scroll when shift is held
                this.targetOffsetX -= Math.sign(e.deltaY) * panSpeed;
            } else {
                // Normal vertical scroll
                this.targetOffsetX -= e.deltaX * panSpeed * 0.01;
                this.targetOffsetY -= e.deltaY * panSpeed * 0.01;
            }
            
            // Apply proper constraints using the constraint function
            const viewportWidth = rect.width / (this.pixelSize * this.zoom);
            const viewportHeight = rect.height / (this.pixelSize * this.zoom);
            
            this.targetOffsetX = this.constrainOffset(this.targetOffsetX, this.config.width, viewportWidth);
            this.targetOffsetY = this.constrainOffset(this.targetOffsetY, this.config.height, viewportHeight);
            
            // Start smooth animation
            if (!this.animationFrame) {
                this.startSmoothScroll();
            }
        }
    }
    
    adjustZoom(delta) {
        // Get current mouse position or use center
        const rect = this.canvas.getBoundingClientRect();
        const centerX = rect.width / 2 / (this.pixelSize * this.zoom) - this.offsetX;
        const centerY = rect.height / 2 / (this.pixelSize * this.zoom) - this.offsetY;
        
        // Zoom towards center
        this.adjustZoomAtPoint(delta, centerX, centerY);
    }
    
    resetZoom() {
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.redraw();
    }
    
    navigateToCorner(corner) {
        const rect = this.canvas.getBoundingClientRect();
        const viewportWidth = rect.width / (this.pixelSize * this.zoom);
        const viewportHeight = rect.height / (this.pixelSize * this.zoom);
        
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
        }
        
        // Center if viewport is larger than canvas
        if (viewportWidth >= this.config.width) {
            targetX = (viewportWidth - this.config.width) / 2;
        }
        if (viewportHeight >= this.config.height) {
            targetY = (viewportHeight - this.config.height) / 2;
        }
        
        // Ensure we can actually reach the corners
        targetX = Math.min(0, Math.max(-(this.config.width - viewportWidth), targetX));
        targetY = Math.min(0, Math.max(-(this.config.height - viewportHeight), targetY));
        
        // Update targets for smooth scrolling
        this.targetOffsetX = targetX;
        this.targetOffsetY = targetY;
        
        // Smooth animation to corner
        const startX = this.offsetX;
        const startY = this.offsetY;
        const duration = 300;
        const startTime = Date.now();
        
        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            
            // Ease-out animation
            const ease = 1 - Math.pow(1 - progress, 3);
            
            this.offsetX = startX + (targetX - startX) * ease;
            this.offsetY = startY + (targetY - startY) * ease;
            
            this.redraw();
            
            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                // Ensure we're exactly at target
                this.offsetX = targetX;
                this.offsetY = targetY;
                this.redraw();
            }
        };
        
        requestAnimationFrame(animate);
    }
    
    constrainOffset(offset, gridSize, viewportSize) {
        // If the viewport is larger than the grid, center it
        if (viewportSize >= gridSize) {
            return (viewportSize - gridSize) / 2;
        }
        
        // Calculate proper boundaries
        // minOffset allows us to see the rightmost/bottommost edge
        // maxOffset allows us to see the leftmost/topmost edge
        const minOffset = Math.min(0, -(gridSize - viewportSize));
        const maxOffset = 0;
        
        // Clamp the offset to valid range
        return Math.max(minOffset, Math.min(maxOffset, offset));
    }
    
    startSmoothScroll() {
        const animate = () => {
            // Lerp towards target position for smooth movement
            this.offsetX += (this.targetOffsetX - this.offsetX) * this.smoothness;
            this.offsetY += (this.targetOffsetY - this.offsetY) * this.smoothness;
            
            // Redraw canvas
            this.redraw();
            
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
    
    applyMomentum() {
        const animate = () => {
            // Apply friction to velocity
            this.velocityX *= this.friction;
            this.velocityY *= this.friction;
            
            // Update target position based on velocity
            const rect = this.canvas.getBoundingClientRect();
            let newOffsetX = this.offsetX + this.velocityX / (this.pixelSize * this.zoom);
            let newOffsetY = this.offsetY + this.velocityY / (this.pixelSize * this.zoom);
            
            // Apply constraints with proper boundary calculation
            const viewportWidth = rect.width / (this.pixelSize * this.zoom);
            const viewportHeight = rect.height / (this.pixelSize * this.zoom);
            this.targetOffsetX = this.constrainOffset(newOffsetX, this.config.width, viewportWidth);
            this.targetOffsetY = this.constrainOffset(newOffsetY, this.config.height, viewportHeight);
            
            // Smooth transition to target
            this.offsetX += (this.targetOffsetX - this.offsetX) * 0.2;
            this.offsetY += (this.targetOffsetY - this.offsetY) * 0.2;
            
            this.redraw();
            
            // Continue momentum if velocity is significant
            if (Math.abs(this.velocityX) > 0.5 || Math.abs(this.velocityY) > 0.5) {
                this.animationFrame = requestAnimationFrame(animate);
            } else {
                this.animationFrame = null;
            }
        };
        
        this.animationFrame = requestAnimationFrame(animate);
    }
    
    adjustZoomAtPoint(delta, pointX, pointY) {
        const oldZoom = this.zoom;
        this.zoom = Math.max(0.5, Math.min(5, this.zoom + delta));
        
        if (this.zoom !== oldZoom) {
            // Calculate new offset to keep the point under the mouse
            const zoomRatio = this.zoom / oldZoom;
            const rect = this.canvas.getBoundingClientRect();
            
            // Calculate the mouse position in canvas coordinates
            const canvasX = (pointX + this.offsetX);
            const canvasY = (pointY + this.offsetY);
            
            // After zoom, we want the same canvas point to be under the mouse
            // New offset should position the canvas point at the same screen position
            this.offsetX = canvasX * zoomRatio - pointX;
            this.offsetY = canvasY * zoomRatio - pointY;
            
            // Update targets for smooth scrolling
            this.targetOffsetX = this.offsetX;
            this.targetOffsetY = this.offsetY;
            
            // Don't apply constraints immediately - let the user zoom freely
            // Constraints will be applied during panning
            this.redraw();
        }
    }
    
    toggleFullscreen() {
        const container = document.querySelector('.pixel-war-container');
        const btn = document.getElementById('fullscreenToggle');
        
        container.classList.toggle('fullscreen-canvas');
        
        if (container.classList.contains('fullscreen-canvas')) {
            btn.innerHTML = '⛶';
            btn.title = 'Exit fullscreen';
            this.showNotification('Fullscreen mode', 'info');
        } else {
            btn.innerHTML = '⛶';
            btn.title = 'Toggle fullscreen canvas';
            this.showNotification('Normal view', 'info');
        }
        
        // Recalculate canvas size after layout change
        setTimeout(() => {
            this.redraw();
        }, 100);
    }
    
    selectColor(color) {
        this.selectedColor = color;
        document.getElementById('selectedColor').style.backgroundColor = color;
        document.getElementById('colorPicker').value = color;
    }
    
    highlightSelectedColor(element) {
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.classList.remove('selected');
        });
        element.classList.add('selected');
    }
    
    getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    
    async placePixel(x, y) {
        // No cooldown check - users can place all remaining pixels immediately
        
        try {
            // Get CSRF token from cookie
            const csrftoken = this.getCookie('csrftoken');
            
            const response = await fetch(`${this.apiBaseUrl}/vibe-coding/api/place-pixel/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrftoken
                },
                body: JSON.stringify({
                    x: x,
                    y: y,
                    color: this.selectedColor,
                    canvas_id: this.config.id
                })
            });
            
            const data = await response.json();
            
            if (response.ok && data.success) {
                this.pixels[`${x},${y}`] = {
                    color: data.pixel.color,
                    placed_by: data.pixel.placed_by
                };
                this.drawPixel(x, y, data.pixel.color);
                
                // Update pixels remaining (no cooldown needed)
                if (data.cooldown_info) {
                    // No cooldown between pixels - only track remaining pixels
                    this.pixelsRemaining = data.cooldown_info.pixels_remaining;
                    this.updatePixelsRemaining();
                }
                
                this.showNotification('Pixel placed!', 'success');
            } else if (response.status === 429) {
                if (data.limit_info) {
                    const message = data.limit_info.placed_this_minute >= data.limit_info.max_per_minute
                        ? `Pixel limit reached! (${data.limit_info.max_per_minute}/min)`
                        : 'Cooldown active!';
                    this.showNotification(message, 'error');
                    
                    if (!data.limit_info.is_registered) {
                        setTimeout(() => {
                            this.showNotification('Register to get more pixels per minute!', 'info');
                        }, 2000);
                    }
                    // Update pixels remaining to 0 when limit is reached
                    this.pixelsRemaining = 0;
                    this.updatePixelsRemaining();
                }
                // No cooldown needed - just wait for minute reset
            } else {
                this.showNotification(data.error || 'Failed to place pixel', 'error');
            }
        } catch (error) {
            console.error('Error placing pixel:', error);
            this.showNotification('Network error', 'error');
        }
    }
    
    async loadCanvasState() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/vibe-coding/api/canvas-state/${this.config.id}/`);
            const data = await response.json();
            
            if (data.success) {
                this.pixels = data.pixels;
                this.redraw();
            }
        } catch (error) {
            console.error('Error loading canvas state:', error);
        }
    }
    
    async loadRecentActivity() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/vibe-coding/api/pixel-history/?canvas_id=${this.config.id}&limit=20`);
            const data = await response.json();
            
            if (data.success) {
                this.displayActivity(data.history);
            }
        } catch (error) {
            console.error('Error loading activity:', error);
        }
    }
    
    displayActivity(history) {
        const activityList = document.getElementById('activityList');
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
    
    drawGrid() {
        this.ctx.strokeStyle = '#e0e0e0';
        this.ctx.lineWidth = 0.5;
        
        for (let x = 0; x <= this.config.width; x++) {
            this.ctx.beginPath();
            this.ctx.moveTo(x * this.pixelSize, 0);
            this.ctx.lineTo(x * this.pixelSize, this.canvas.height);
            this.ctx.stroke();
        }
        
        for (let y = 0; y <= this.config.height; y++) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, y * this.pixelSize);
            this.ctx.lineTo(this.canvas.width, y * this.pixelSize);
            this.ctx.stroke();
        }
    }
    
    drawPixel(x, y, color) {
        this.ctx.fillStyle = color;
        this.ctx.fillRect(
            (x + this.offsetX) * this.pixelSize * this.zoom,
            (y + this.offsetY) * this.pixelSize * this.zoom,
            this.pixelSize * this.zoom,
            this.pixelSize * this.zoom
        );
    }
    
    redraw() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        this.ctx.save();
        this.ctx.scale(this.zoom, this.zoom);
        this.ctx.translate(this.offsetX * this.pixelSize, this.offsetY * this.pixelSize);
        
        this.ctx.fillStyle = '#ffffff';
        this.ctx.fillRect(0, 0, this.config.width * this.pixelSize, this.config.height * this.pixelSize);
        
        for (const key in this.pixels) {
            const [x, y] = key.split(',').map(Number);
            this.ctx.fillStyle = this.pixels[key].color;
            this.ctx.fillRect(x * this.pixelSize, y * this.pixelSize, this.pixelSize, this.pixelSize);
        }
        
        if (this.zoom > 0.7) {
            this.ctx.strokeStyle = 'rgba(200, 200, 200, 0.3)';
            this.ctx.lineWidth = 0.5;
            for (let x = 0; x <= this.config.width; x++) {
                this.ctx.beginPath();
                this.ctx.moveTo(x * this.pixelSize, 0);
                this.ctx.lineTo(x * this.pixelSize, this.config.height * this.pixelSize);
                this.ctx.stroke();
            }
            for (let y = 0; y <= this.config.height; y++) {
                this.ctx.beginPath();
                this.ctx.moveTo(0, y * this.pixelSize);
                this.ctx.lineTo(this.config.width * this.pixelSize, y * this.pixelSize);
                this.ctx.stroke();
            }
        }
        
        this.ctx.restore();
        
        // Update minimap
        this.updateMinimap();
        
        // Update minimap viewport indicator
        this.updateMinimapViewport();
    }
    
    updateMinimap() {
        if (!this.minimapCtx) return;
        
        // Clear minimap
        this.minimapCtx.clearRect(0, 0, this.minimap.width, this.minimap.height);
        
        // Draw background
        this.minimapCtx.fillStyle = '#f0f0f0';
        this.minimapCtx.fillRect(0, 0, this.minimap.width, this.minimap.height);
        
        // Draw pixels on minimap
        for (let key in this.pixels) {
            const [x, y] = key.split(',').map(Number);
            this.minimapCtx.fillStyle = this.pixels[key].color;
            this.minimapCtx.fillRect(
                x * this.minimapScale,
                y * this.minimapScale,
                Math.ceil(this.minimapScale),
                Math.ceil(this.minimapScale)
            );
        }
        
        // Draw border
        this.minimapCtx.strokeStyle = '#333';
        this.minimapCtx.lineWidth = 1;
        this.minimapCtx.strokeRect(0, 0, this.minimap.width, this.minimap.height);
    }
    
    updateMinimapViewport() {
        if (!this.minimapViewport) return;
        
        const rect = this.canvas.getBoundingClientRect();
        const viewportWidth = rect.width / (this.pixelSize * this.zoom);
        const viewportHeight = rect.height / (this.pixelSize * this.zoom);
        
        // Calculate viewport position on minimap
        const vpX = -this.offsetX * this.minimapScale;
        const vpY = -this.offsetY * this.minimapScale;
        const vpW = viewportWidth * this.minimapScale;
        const vpH = viewportHeight * this.minimapScale;
        
        // Update viewport indicator
        this.minimapViewport.style.left = vpX + 'px';
        this.minimapViewport.style.top = vpY + 'px';
        this.minimapViewport.style.width = vpW + 'px';
        this.minimapViewport.style.height = vpH + 'px';
    }
    
    showPixelTooltip(e, x, y) {
        const tooltip = document.getElementById('pixelTooltip');
        const pixel = this.pixels[`${x},${y}`];
        
        if (pixel) {
            tooltip.innerHTML = `
                <strong>(${x}, ${y})</strong><br>
                By: ${pixel.placed_by}<br>
                <span style="display: inline-block; width: 15px; height: 15px; background-color: ${pixel.color}; border: 1px solid #000;"></span>
            `;
        } else {
            tooltip.innerHTML = `<strong>(${x}, ${y})</strong><br>Empty`;
        }
        
        tooltip.style.display = 'block';
        tooltip.style.left = e.pageX + 10 + 'px';
        tooltip.style.top = e.pageY + 10 + 'px';
    }
    
    hidePixelTooltip() {
        document.getElementById('pixelTooltip').style.display = 'none';
    }
    
    startCooldown(seconds = null) {
        const cooldownTime = (seconds || this.cooldownSeconds) * 1000;
        this.cooldownEndTime = Date.now() + cooldownTime;
        this.updateCooldownDisplay();
    }
    
    updateCooldownDisplay() {
        const timer = document.getElementById('cooldownTimer');
        
        // Always show ready since there's no cooldown between pixels
        // Only the per-minute rate limit applies
        timer.textContent = this.pixelsRemaining > 0 ? 'Ready' : 'Limit reached';
        timer.style.color = this.pixelsRemaining > 0 ? '#4caf50' : '#ff6b6b';
    }
    
    updatePixelsRemaining() {
        const element = document.getElementById('pixelsRemaining');
        if (element) {
            if (this.pixelsRemaining !== null) {
                element.textContent = `${this.pixelsRemaining}/${this.maxPixelsPerMinute}`;
                element.style.color = this.pixelsRemaining > 0 ? '#4caf50' : '#ff6b6b';
            } else {
                element.textContent = `${this.maxPixelsPerMinute}/${this.maxPixelsPerMinute}`;
            }
        }
    }
    
    showNotification(message, type = 'info') {
        const notification = document.getElementById('notification');
        notification.textContent = message;
        notification.className = `notification ${type} show`;
        
        setTimeout(() => {
            notification.classList.remove('show');
        }, 3000);
    }
    
    startUpdateLoop() {
        setInterval(() => {
            this.updateCooldownDisplay();
            this.loadCanvasState();
            this.loadRecentActivity();
        }, 2000);
    }
}

// Initialize PixelWar when script loads
// Check if DOM is already loaded (for dynamically loaded scripts)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof CANVAS_CONFIG !== 'undefined') {
            window.pixelWar = new PixelWar('pixelCanvas', CANVAS_CONFIG);
        }
    });
} else {
    // DOM is already loaded, initialize immediately
    if (typeof CANVAS_CONFIG !== 'undefined') {
        window.pixelWar = new PixelWar('pixelCanvas', CANVAS_CONFIG);
    }
}