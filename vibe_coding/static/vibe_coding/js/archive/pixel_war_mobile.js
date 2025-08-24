class PixelWarMobile {
    constructor(canvasId, config) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.config = config;
        
        // Canvas settings
        this.pixelSize = 10;
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        
        // Touch/Mouse state
        this.isDragging = false;
        this.dragStartX = 0;
        this.dragStartY = 0;
        this.lastTouchDistance = 0;
        this.isTouchDevice = 'ontouchstart' in window;
        
        // Smooth scrolling and momentum
        this.targetOffsetX = 0;
        this.targetOffsetY = 0;
        this.velocityX = 0;
        this.velocityY = 0;
        this.lastTouchX = 0;
        this.lastTouchY = 0;
        this.lastTouchTime = 0;
        this.animationFrame = null;
        this.friction = 0.85; // Lower friction for mobile (more momentum)
        this.smoothness = 0.25; // Higher smoothness for touch
        
        // Game state
        this.selectedColor = '#000000';
        this.pixels = {};
        this.cooldownEndTime = null;
        this.pixelsRemaining = null;
        
        // Preview state
        this.previewX = null;
        this.previewY = null;
        this.previewActive = false;
        this.magnifierCanvas = null;
        this.magnifierCtx = null;
        
        // API configuration
        this.apiBaseUrl = window.location.origin;
        
        // Cooldown settings based on auth
        this.cooldownSeconds = config.isAuthenticated ? config.registeredCooldown : config.anonymousCooldown;
        this.maxPixelsPerMinute = config.isAuthenticated ? config.registeredPixelsPerMinute : config.anonymousPixelsPerMinute;
        
        this.init();
    }
    
    init() {
        this.setupCanvas();
        this.setupMinimap();
        this.setupEventListeners();
        this.setupMobileHelpers();
        this.loadCanvasState();
        this.startUpdateLoop();
        this.loadRecentActivity();
        this.updatePixelsRemaining();
        this.detectColorScheme();
    }
    
    setupMinimap() {
        // Create minimap container for mobile
        const minimapContainer = document.createElement('div');
        minimapContainer.className = 'minimap-container mobile-minimap';
        minimapContainer.innerHTML = `
            <canvas id="minimap" width="100" height="100"></canvas>
            <div class="minimap-viewport"></div>
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
        const scale = Math.min(100 / this.config.width, 100 / this.config.height);
        this.minimapScale = scale;
        this.minimap.width = this.config.width * scale;
        this.minimap.height = this.config.height * scale;
        
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
    
    setupMobileHelpers() {
        // Add auto-zoom on first tap for better precision
        if (this.isTouchDevice && this.zoom === 1) {
            this.autoZoomEnabled = true;
        }
        
        // Add grid overlay toggle button for mobile
        if (this.isTouchDevice) {
            this.addGridToggleButton();
            this.addTouchModeToggle();
        }
        
        // Initialize touch mode preference
        this.touchMode = localStorage.getItem('pixelWarTouchMode') || 'tap';
    }
    
    addGridToggleButton() {
        // Handled by setupMobileControls now
    }
    
    detectColorScheme() {
        // Adapt to user's color scheme preference
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            document.body.classList.add('dark-mode');
        }
    }
    
    setupCanvas() {
        this.canvas.width = this.config.width * this.pixelSize;
        this.canvas.height = this.config.height * this.pixelSize;
        this.drawGrid();
    }
    
    setupEventListeners() {
        // Touch events for mobile
        if (this.isTouchDevice) {
            this.canvas.addEventListener('touchstart', this.handleTouchStart.bind(this), { passive: false });
            this.canvas.addEventListener('touchmove', this.handleTouchMove.bind(this), { passive: false });
            this.canvas.addEventListener('touchend', this.handleTouchEnd.bind(this));
        }
        
        // Mouse events for desktop
        this.canvas.addEventListener('mousedown', this.handleMouseDown.bind(this));
        this.canvas.addEventListener('mousemove', this.handleMouseMove.bind(this));
        this.canvas.addEventListener('mouseup', this.handleMouseUp.bind(this));
        this.canvas.addEventListener('mouseleave', this.handleMouseLeave.bind(this));
        this.canvas.addEventListener('wheel', this.handleWheel.bind(this), { passive: false });
        
        // Color selection (both regular and quick colors)
        document.querySelectorAll('.color-btn, .quick-color').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const color = e.target.dataset.color || e.target.style.backgroundColor;
                this.selectColor(color);
                this.highlightSelectedColor(e.target);
            });
        });
        
        const colorPicker = document.getElementById('colorPicker');
        if (colorPicker) {
            colorPicker.addEventListener('change', (e) => {
                this.selectColor(e.target.value);
                document.querySelectorAll('.color-btn').forEach(btn => btn.classList.remove('selected'));
            });
        }
        
        // Zoom controls with null checks
        const zoomIn = document.getElementById('zoomIn');
        const zoomOut = document.getElementById('zoomOut');
        const zoomReset = document.getElementById('zoomReset');
        
        if (zoomIn) zoomIn.addEventListener('click', () => this.adjustZoom(0.2));
        if (zoomOut) zoomOut.addEventListener('click', () => this.adjustZoom(-0.2));
        if (zoomReset) zoomReset.addEventListener('click', () => this.resetZoom());
        
        // Corner navigation buttons (new mobile grid style)
        document.querySelectorAll('.corner-nav-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const corner = e.currentTarget.dataset.corner;
                this.navigateToCorner(corner);
            });
        });
        
        // Canvas tools
        const gridToggle = document.getElementById('gridToggle');
        const crosshairToggle = document.getElementById('crosshairToggle');
        const minimapToggle = document.getElementById('minimapToggle');
        
        if (gridToggle) gridToggle.addEventListener('click', () => this.toggleGrid());
        if (crosshairToggle) crosshairToggle.addEventListener('click', () => this.toggleCrosshair());
        if (minimapToggle) minimapToggle.addEventListener('click', () => this.toggleMinimap());
        
        // Mobile menu controls
        const mobileMenuToggle = document.getElementById('mobileMenuToggle');
        const mobileMenuOverlay = document.getElementById('mobileMenuOverlay');
        const moreColorsBtn = document.getElementById('moreColorsBtn');
        const placePixelBtn = document.getElementById('placePixelBtn');
        
        if (mobileMenuToggle) mobileMenuToggle.addEventListener('click', () => this.toggleMobileMenu());
        if (mobileMenuOverlay) mobileMenuOverlay.addEventListener('click', () => this.closeMobileMenu());
        if (moreColorsBtn) moreColorsBtn.addEventListener('click', () => this.toggleMobileMenu());
        if (placePixelBtn) placePixelBtn.addEventListener('click', () => this.placePixelFromButton());
        
        // Fullscreen toggle
        const fullscreenBtn = document.getElementById('fullscreenToggle');
        if (fullscreenBtn) {
            fullscreenBtn.addEventListener('click', () => this.toggleFullscreen());
        }
        
        // Keyboard shortcuts
        document.addEventListener('keydown', this.handleKeyboard.bind(this));
    }
    
    handleKeyboard(e) {
        if (e.key === '+' || e.key === '=') this.adjustZoom(0.2);
        if (e.key === '-') this.adjustZoom(-0.2);
        if (e.key === '0') this.resetZoom();
    }
    
    // Touch event handlers
    handleTouchStart(e) {
        e.preventDefault();
        
        if (e.touches.length === 1) {
            // Single touch - prepare for drag or tap
            const touch = e.touches[0];
            this.dragStartX = touch.clientX - this.offsetX * this.pixelSize * this.zoom;
            this.dragStartY = touch.clientY - this.offsetY * this.pixelSize * this.zoom;
            this.isDragging = false; // Will be set to true on move
            
            // Initialize velocity tracking
            this.lastTouchX = touch.clientX;
            this.lastTouchY = touch.clientY;
            this.lastTouchTime = Date.now();
            this.velocityX = 0;
            this.velocityY = 0;
            
            // Store initial touch position for tap detection
            this.touchStartX = touch.clientX;
            this.touchStartY = touch.clientY;
            this.touchStartTime = Date.now();
            
            // Stop any ongoing animation
            if (this.animationFrame) {
                cancelAnimationFrame(this.animationFrame);
                this.animationFrame = null;
            }
            
            // Start long press timer for preview
            this.longPressTimer = setTimeout(() => {
                if (!this.isDragging && e.touches.length === 1) {
                    this.handleLongPress(touch.clientX, touch.clientY);
                }
            }, 400); // 400ms for long press
        } else if (e.touches.length === 2) {
            // Two fingers - prepare for pinch zoom
            this.clearLongPressTimer();
            this.isDragging = false;
            const distance = this.getTouchDistance(e.touches[0], e.touches[1]);
            this.lastTouchDistance = distance;
        }
    }
    
    handleTouchMove(e) {
        e.preventDefault();
        
        if (e.touches.length === 1 && !this.lastTouchDistance) {
            const touch = e.touches[0];
            
            // Check if we've moved enough to consider it a drag (5px threshold)
            const moveDistance = Math.sqrt(
                Math.pow(touch.clientX - this.touchStartX, 2) + 
                Math.pow(touch.clientY - this.touchStartY, 2)
            );
            
            if (moveDistance > 5) {
                // Clear long press timer if dragging
                this.clearLongPressTimer();
                this.isDragging = true;
                
                // Calculate new offsets with boundary constraints
                const rect = this.canvas.getBoundingClientRect();
                const viewportWidth = rect.width / (this.pixelSize * this.zoom);
                const viewportHeight = rect.height / (this.pixelSize * this.zoom);
                
                let newOffsetX = (touch.clientX - this.dragStartX) / (this.pixelSize * this.zoom);
                let newOffsetY = (touch.clientY - this.dragStartY) / (this.pixelSize * this.zoom);
                
                // Apply boundary constraints
                this.targetOffsetX = this.constrainOffset(newOffsetX, this.config.width, viewportWidth);
                this.targetOffsetY = this.constrainOffset(newOffsetY, this.config.height, viewportHeight);
                
                // Calculate velocity for momentum (using time delta for accuracy)
                const currentTime = Date.now();
                const timeDelta = Math.max(1, currentTime - this.lastTouchTime);
                this.velocityX = (touch.clientX - this.lastTouchX) / timeDelta * 16; // Normalize to ~60fps
                this.velocityY = (touch.clientY - this.lastTouchY) / timeDelta * 16;
                
                this.lastTouchX = touch.clientX;
                this.lastTouchY = touch.clientY;
                this.lastTouchTime = currentTime;
                
                // Start smooth animation if not already running
                if (!this.animationFrame) {
                    this.startSmoothScroll();
                }
            }
        } else if (e.touches.length === 2) {
            // Pinch zoom
            this.clearLongPressTimer();
            const distance = this.getTouchDistance(e.touches[0], e.touches[1]);
            if (this.lastTouchDistance) {
                const scale = distance / this.lastTouchDistance;
                this.adjustZoom((scale - 1) * 0.5);
                this.lastTouchDistance = distance;
            }
        }
    }
    
    handleTouchEnd(e) {
        // Clear long press timer
        this.clearLongPressTimer();
        
        // Apply momentum if dragging with velocity
        if (this.isDragging && (Math.abs(this.velocityX) > 2 || Math.abs(this.velocityY) > 2)) {
            this.applyMomentum();
        }
        
        // Check if it was a quick tap (not a drag)
        const tapDuration = Date.now() - this.touchStartTime;
        if (!this.isDragging && tapDuration < 300 && !this.previewActive) {
            const rect = this.canvas.getBoundingClientRect();
            
            // Calculate touch position with improved accuracy
            const touchX = this.touchStartX - rect.left;
            const touchY = this.touchStartY - rect.top;
            
            // Get the pixel coordinates (offsetX and offsetY are negative values)
            let x = Math.floor((touchX / (this.pixelSize * this.zoom)) + (-this.offsetX));
            let y = Math.floor((touchY / (this.pixelSize * this.zoom)) + (-this.offsetY));
            
            // Smart zoom: only auto-zoom if the canvas is at default zoom and pixel is hard to see
            if (this.autoZoomEnabled && this.zoom < 1.5 && this.pixelSize * this.zoom < 15) {
                // Smooth zoom to 2x instead of jarring 2.5x
                this.smoothZoomToPixel(x, y, 2.0);
                
                // Recalculate position after zoom
                x = Math.floor((touchX / (this.pixelSize * this.zoom)) + (-this.offsetX));
                y = Math.floor((touchY / (this.pixelSize * this.zoom)) + (-this.offsetY));
                
                // Disable auto-zoom after first use
                this.autoZoomEnabled = false;
            }
            
            if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
                // Show preview for confirmation
                this.showPixelPreview(x, y);
            }
        }
        
        this.isDragging = false;
        this.lastTouchDistance = 0;
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
        // Vibrate for feedback
        if (navigator.vibrate) {
            navigator.vibrate(50);
        }
        
        const rect = this.canvas.getBoundingClientRect();
        const x = Math.floor((clientX - rect.left) / (this.pixelSize * this.zoom) + (-this.offsetX));
        const y = Math.floor((clientY - rect.top) / (this.pixelSize * this.zoom) + (-this.offsetY));
        
        if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
            // Show enhanced preview with magnifier
            this.showPixelPreview(x, y, true);
        }
    }
    
    smoothZoomToPixel(x, y, targetZoom) {
        const startZoom = this.zoom;
        const startOffsetX = this.offsetX;
        const startOffsetY = this.offsetY;
        
        const duration = 300; // ms
        const startTime = Date.now();
        
        const animate = () => {
            const elapsed = Date.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            
            // Ease-out cubic function for smooth animation
            const easeOut = 1 - Math.pow(1 - progress, 3);
            
            this.zoom = startZoom + (targetZoom - startZoom) * easeOut;
            
            // Calculate viewport size at current zoom
            const rect = this.canvas.getBoundingClientRect();
            const viewportWidth = rect.width / (this.pixelSize * this.zoom);
            const viewportHeight = rect.height / (this.pixelSize * this.zoom);
            
            // Calculate target offset to center the pixel with constraints
            let targetOffsetX = -(x - viewportWidth / 2);
            let targetOffsetY = -(y - viewportHeight / 2);
            
            // Apply constraints
            targetOffsetX = this.constrainOffset(targetOffsetX, this.config.width, viewportWidth);
            targetOffsetY = this.constrainOffset(targetOffsetY, this.config.height, viewportHeight);
            
            // Interpolate to target
            this.offsetX = startOffsetX + (targetOffsetX - startOffsetX) * easeOut;
            this.offsetY = startOffsetY + (targetOffsetY - startOffsetY) * easeOut;
            
            this.redraw();
            
            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                this.showNotification('Tap to select, pinch to zoom', 'info');
            }
        };
        
        requestAnimationFrame(animate);
    }
    
    addTouchModeToggle() {
        const toggleContainer = document.createElement('div');
        toggleContainer.className = 'touch-mode-toggle';
        toggleContainer.innerHTML = `
            <button id="touchModeBtn" class="touch-mode-btn">
                <span class="mode-icon">👆</span>
                <span class="mode-text">Tap Mode</span>
            </button>
        `;
        
        const canvasSection = document.querySelector('.canvas-section');
        if (canvasSection) {
            canvasSection.appendChild(toggleContainer);
            
            const btn = document.getElementById('touchModeBtn');
            btn.addEventListener('click', () => {
                this.touchMode = this.touchMode === 'tap' ? 'precision' : 'tap';
                localStorage.setItem('pixelWarTouchMode', this.touchMode);
                this.updateTouchModeButton();
            });
            
            this.updateTouchModeButton();
        }
    }
    
    updateTouchModeButton() {
        const btn = document.getElementById('touchModeBtn');
        if (btn) {
            const icon = btn.querySelector('.mode-icon');
            const text = btn.querySelector('.mode-text');
            
            if (this.touchMode === 'precision') {
                icon.textContent = '🎯';
                text.textContent = 'Precision Mode';
                btn.classList.add('precision');
            } else {
                icon.textContent = '👆';
                text.textContent = 'Tap Mode';
                btn.classList.remove('precision');
            }
        }
    }
    
    // Mouse event handlers
    handleMouseDown(e) {
        if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
            this.isDragging = true;
            this.dragStartX = e.clientX - this.offsetX * this.pixelSize * this.zoom;
            this.dragStartY = e.clientY - this.offsetY * this.pixelSize * this.zoom;
            this.canvas.style.cursor = 'grabbing';
        }
    }
    
    handleMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const x = Math.floor((e.clientX - rect.left) / (this.pixelSize * this.zoom) + (-this.offsetX));
        const y = Math.floor((e.clientY - rect.top) / (this.pixelSize * this.zoom) + (-this.offsetY));
        
        if (this.isDragging) {
            // Calculate new offsets with boundary constraints
            let newOffsetX = (e.clientX - this.dragStartX) / (this.pixelSize * this.zoom);
            let newOffsetY = (e.clientY - this.dragStartY) / (this.pixelSize * this.zoom);
            
            // Apply boundary constraints
            this.offsetX = this.constrainOffset(newOffsetX, this.config.width, rect.width / (this.pixelSize * this.zoom));
            this.offsetY = this.constrainOffset(newOffsetY, this.config.height, rect.height / (this.pixelSize * this.zoom));
            
            this.redraw();
        } else if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
            this.showPixelTooltip(e, x, y);
        } else {
            this.hidePixelTooltip();
        }
    }
    
    handleMouseUp(e) {
        if (!this.isDragging && e.button === 0) {
            const rect = this.canvas.getBoundingClientRect();
            const x = Math.floor((e.clientX - rect.left) / (this.pixelSize * this.zoom) + (-this.offsetX));
            const y = Math.floor((e.clientY - rect.top) / (this.pixelSize * this.zoom) + (-this.offsetY));
            
            if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
                this.placePixel(x, y);
            }
        }
        this.isDragging = false;
        this.canvas.style.cursor = 'crosshair';
    }
    
    handleMouseLeave() {
        this.isDragging = false;
        this.hidePixelTooltip();
        this.canvas.style.cursor = 'crosshair';
    }
    
    handleWheel(e) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        this.adjustZoom(delta);
    }
    
    // Canvas manipulation
    adjustZoom(delta) {
        const oldZoom = this.zoom;
        this.zoom = Math.max(0.5, Math.min(5, this.zoom + delta));
        
        // Recalculate offsets to keep view centered and within bounds
        const rect = this.canvas.getBoundingClientRect();
        const viewportWidth = rect.width / (this.pixelSize * this.zoom);
        const viewportHeight = rect.height / (this.pixelSize * this.zoom);
        
        // Adjust offsets to maintain center point and apply constraints
        const zoomRatio = this.zoom / oldZoom;
        this.offsetX = this.constrainOffset(this.offsetX * zoomRatio, this.config.width, viewportWidth);
        this.offsetY = this.constrainOffset(this.offsetY * zoomRatio, this.config.height, viewportHeight);
        
        this.redraw();
        this.updateZoomIndicator();
    }
    
    resetZoom() {
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.redraw();
    }
    
    navigateToCorner(corner) {
        const rect = this.canvas.getBoundingClientRect();
        
        // Auto-zoom for better corner precision (except center)
        if (this.zoom < 1.5 && corner !== 'center') {
            this.zoom = 2.0;
            this.updateZoomIndicator();
        } else if (corner === 'center' && this.zoom > 1) {
            this.zoom = 1;
            this.updateZoomIndicator();
        }
        
        // Recalculate viewport dimensions with the current zoom
        const viewportWidth = rect.width / (this.pixelSize * this.zoom);
        const viewportHeight = rect.height / (this.pixelSize * this.zoom);
        
        let targetX, targetY;
        
        switch(corner) {
            case 'top-left':
                targetX = 0;
                targetY = 0;
                break;
            case 'top-right':
                targetX = this.config.width - viewportWidth;
                targetY = 0;
                break;
            case 'bottom-left':
                targetX = 0;
                targetY = this.config.height - viewportHeight;
                break;
            case 'bottom-right':
                targetX = this.config.width - viewportWidth;
                targetY = this.config.height - viewportHeight;
                break;
            case 'center':
                targetX = (this.config.width - viewportWidth) / 2;
                targetY = (this.config.height - viewportHeight) / 2;
                break;
        }
        
        // Ensure we don't go out of bounds
        targetX = Math.max(0, Math.min(targetX, this.config.width - viewportWidth));
        targetY = Math.max(0, Math.min(targetY, this.config.height - viewportHeight));
        
        // Convert to negative offsets (as used by the canvas transform)
        this.targetOffsetX = -targetX;
        this.targetOffsetY = -targetY;
        
        // Start smooth scroll animation
        if (!this.animationFrame) {
            this.startSmoothScroll();
        }
        
        // Provide haptic feedback on mobile
        if (navigator.vibrate) {
            navigator.vibrate(30);
        }
        
        // Show notification
        if (corner !== 'center') {
            this.showNotification(`${corner.replace('-', ' ')} corner`, 'info');
        }
    }
    
    updateZoomIndicator() {
        const indicator = document.getElementById('zoomLevel');
        if (indicator) {
            indicator.textContent = Math.round(this.zoom * 100) + '%';
        }
    }
    
    constrainOffset(offset, gridSize, viewportSize) {
        // If the viewport is larger than the grid, center it
        if (viewportSize >= gridSize) {
            return -(gridSize - viewportSize) / 2;
        }
        
        // Calculate the valid range for offsets
        // offset should be between 0 (showing left/top edge) and -(gridSize - viewportSize) (showing right/bottom edge)
        const minOffset = -(gridSize - viewportSize);
        const maxOffset = 0;
        
        // Constrain to boundaries
        const constrained = Math.max(minOffset, Math.min(maxOffset, offset));
        
        return constrained;
    }
    
    startSmoothScroll() {
        const animate = () => {
            // Smooth interpolation towards target
            this.offsetX += (this.targetOffsetX - this.offsetX) * this.smoothness;
            this.offsetY += (this.targetOffsetY - this.offsetY) * this.smoothness;
            
            // Apply any remaining velocity
            if (!this.isDragging) {
                this.velocityX *= 0.98;
                this.velocityY *= 0.98;
            }
            
            this.redraw();
            
            // Continue animation if still moving
            if (Math.abs(this.targetOffsetX - this.offsetX) > 0.01 || 
                Math.abs(this.targetOffsetY - this.offsetY) > 0.01 ||
                Math.abs(this.velocityX) > 0.1 || Math.abs(this.velocityY) > 0.1) {
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
            let newOffsetX = this.targetOffsetX + this.velocityX / (this.pixelSize * this.zoom);
            let newOffsetY = this.targetOffsetY + this.velocityY / (this.pixelSize * this.zoom);
            
            // Apply constraints with bounce-back effect
            const constrainedX = this.constrainOffset(newOffsetX, this.config.width, rect.width / (this.pixelSize * this.zoom));
            const constrainedY = this.constrainOffset(newOffsetY, this.config.height, rect.height / (this.pixelSize * this.zoom));
            
            // If we hit a boundary, reverse velocity with dampening
            if (Math.abs(constrainedX - newOffsetX) > 0.1) {
                this.velocityX *= -0.3;
            }
            if (Math.abs(constrainedY - newOffsetY) > 0.1) {
                this.velocityY *= -0.3;
            }
            
            this.targetOffsetX = constrainedX;
            this.targetOffsetY = constrainedY;
            
            // Smooth transition
            this.offsetX += (this.targetOffsetX - this.offsetX) * 0.3;
            this.offsetY += (this.targetOffsetY - this.offsetY) * 0.3;
            
            this.redraw();
            
            // Continue momentum until velocity is negligible
            if (Math.abs(this.velocityX) > 0.5 || Math.abs(this.velocityY) > 0.5 ||
                Math.abs(this.targetOffsetX - this.offsetX) > 0.5 ||
                Math.abs(this.targetOffsetY - this.offsetY) > 0.5) {
                this.animationFrame = requestAnimationFrame(animate);
            } else {
                this.animationFrame = null;
                // Snap to final position
                this.offsetX = this.targetOffsetX;
                this.offsetY = this.targetOffsetY;
                this.redraw();
            }
        };
        
        this.animationFrame = requestAnimationFrame(animate);
    }
    
    toggleFullscreen() {
        const container = document.querySelector('.pixel-war-container');
        const btn = document.getElementById('fullscreenToggle');
        
        container.classList.toggle('fullscreen-canvas');
        
        if (container.classList.contains('fullscreen-canvas')) {
            btn.innerHTML = '⛶';
            btn.title = 'Exit fullscreen';
            this.showNotification('Fullscreen mode', 'success');
        } else {
            btn.innerHTML = '⛶';
            btn.title = 'Toggle fullscreen canvas';
            this.showNotification('Normal view', 'success');
        }
        
        // Recalculate canvas size after layout change
        setTimeout(() => {
            this.redraw();
        }, 100);
    }
    
    // Color selection
    selectColor(color) {
        this.selectedColor = color;
        
        // Update all color displays
        const displays = ['selectedColor', 'selectedColorMobile'];
        displays.forEach(id => {
            const elem = document.getElementById(id);
            if (elem) {
                elem.style.backgroundColor = color;
            }
        });
        
        const picker = document.getElementById('colorPicker');
        if (picker) {
            picker.value = color;
        }
        
        // Update quick color selection
        document.querySelectorAll('.quick-color').forEach(btn => {
            const btnColor = btn.dataset.color || this.rgbToHex(btn.style.backgroundColor);
            btn.classList.toggle('selected', this.rgbToHex(btnColor) === this.rgbToHex(color));
        });
    }
    
    rgbToHex(color) {
        if (!color) return '#000000';
        
        // Already hex
        if (color.startsWith('#')) {
            return color.toUpperCase();
        }
        
        // Convert rgb() to hex
        if (color.startsWith('rgb')) {
            const matches = color.match(/\d+/g);
            if (matches && matches.length >= 3) {
                const r = parseInt(matches[0]);
                const g = parseInt(matches[1]);
                const b = parseInt(matches[2]);
                return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('').toUpperCase();
            }
        }
        
        return color;
    }
    
    highlightSelectedColor(element) {
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.classList.remove('selected');
        });
        element.classList.add('selected');
    }
    
    // Cookie helper
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
    
    // API calls
    async placePixel(x, y) {
        // No cooldown check - users can place all remaining pixels immediately
        
        // Check if we have pixels remaining
        if (this.pixelsRemaining === 0) {
            this.showNotification('No pixels left this minute!', 'error');
            this.shakeCanvas();
            return;
        }
        
        // Show placing animation
        this.showPlacingAnimation(x, y);
        
        try {
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
                // Success! Update the pixel
                this.pixels[`${x},${y}`] = {
                    color: data.pixel.color,
                    placed_by: data.pixel.placed_by
                };
                this.drawPixel(x, y, data.pixel.color);
                
                // Update remaining pixels (no cooldown needed)
                if (data.cooldown_info) {
                    // No cooldown between pixels - only track remaining pixels
                    this.pixelsRemaining = data.cooldown_info.pixels_remaining;
                    this.updatePixelsRemaining();
                }
                
                // Success feedback
                this.showNotification(`✅ Pixel placed! (${this.pixelsRemaining} left)`, 'success');
                this.showPixelPlacedEffect(x, y);
                
                // Haptic feedback on mobile
                if (navigator.vibrate) {
                    navigator.vibrate([50, 30, 50]); // Success pattern
                }
            } else if (response.status === 429) {
                // Rate limited
                console.log('Rate limit response:', data);
                
                if (data.limit_info) {
                    if (data.limit_info.placed_this_minute >= data.limit_info.max_per_minute) {
                        // Pixel limit reached
                        const resetTime = Math.ceil(data.cooldown_remaining || 60);
                        this.showNotification(
                            `⏱️ Limit reached! Reset in ${resetTime}s (${data.limit_info.max_per_minute}/min max)`, 
                            'error'
                        );
                        this.pixelsRemaining = 0;
                        this.updatePixelsRemaining();
                        
                        // Show detailed limit info in confirmation dialog
                        this.showLimitReachedDialog(data.limit_info, resetTime);
                    } else {
                        // Cooldown active
                        const cooldownTime = Math.ceil(data.cooldown_remaining || this.cooldownSeconds);
                        this.showNotification(`⏳ Please wait ${cooldownTime}s before placing another pixel`, 'error');
                    }
                    
                    // Show registration prompt for anonymous users
                    if (!data.limit_info.is_registered) {
                        setTimeout(() => {
                            this.showRegistrationPrompt();
                        }, 2000);
                    }
                }
                
                // No cooldown needed - just wait for minute reset
                // Pixels remaining already set to 0 above when limit is reached
                
                // Error haptic feedback
                if (navigator.vibrate) {
                    navigator.vibrate(200); // Long vibration for error
                }
            } else {
                // Other error
                this.showNotification(data.error || '❌ Failed to place pixel', 'error');
            }
        } catch (error) {
            console.error('Error placing pixel:', error);
            this.showNotification('📡 Connection error - try again', 'error');
        }
        
        // Remove placing animation
        this.removePlacingAnimation();
    }
    
    // New mobile-optimized pixel selection methods
    showPixelPreview(x, y, isLongPress = false) {
        // Store selected pixel coordinates
        this.selectedPixelX = x;
        this.selectedPixelY = y;
        
        // Update the UI to show selected pixel
        this.highlightPixel(x, y);
        
        // Update place pixel button
        const placeBtn = document.getElementById('placePixelBtn');
        if (placeBtn && !this.cooldownEndTime) {
            placeBtn.disabled = false;
            placeBtn.querySelector('.btn-text').textContent = `Place at (${x}, ${y})`;
        }
        
        // Show crosshair for precision
        this.showCrosshair(x, y);
        
        // Haptic feedback
        if (navigator.vibrate) {
            navigator.vibrate(20);
        }
        
        // Show notification
        this.showNotification(`Selected (${x}, ${y})`, 'info');
    }
    
    setupMobileControls() {
        this.gridVisible = false;
        this.crosshairVisible = false;
        this.minimapVisible = true;
        this.selectedPixelX = null;
        this.selectedPixelY = null;
    }
    
    toggleGrid() {
        this.gridVisible = !this.gridVisible;
        const btn = document.getElementById('gridToggle');
        if (btn) {
            btn.classList.toggle('active', this.gridVisible);
        }
        this.redraw();
    }
    
    toggleCrosshair() {
        this.crosshairVisible = !this.crosshairVisible;
        const btn = document.getElementById('crosshairToggle');
        const crosshair = document.getElementById('pixelCrosshair');
        if (btn) {
            btn.classList.toggle('active', this.crosshairVisible);
        }
        if (crosshair) {
            crosshair.classList.toggle('active', this.crosshairVisible);
        }
    }
    
    toggleMinimap() {
        this.minimapVisible = !this.minimapVisible;
        const btn = document.getElementById('minimapToggle');
        const minimap = document.querySelector('.minimap-container');
        if (btn) {
            btn.classList.toggle('active', this.minimapVisible);
        }
        if (minimap) {
            minimap.style.display = this.minimapVisible ? 'block' : 'none';
        }
    }
    
    toggleMobileMenu() {
        const sidebar = document.getElementById('controlsSidebar');
        const overlay = document.getElementById('mobileMenuOverlay');
        if (sidebar && overlay) {
            sidebar.classList.toggle('open');
            overlay.classList.toggle('active');
        }
    }
    
    closeMobileMenu() {
        const sidebar = document.getElementById('controlsSidebar');
        const overlay = document.getElementById('mobileMenuOverlay');
        if (sidebar && overlay) {
            sidebar.classList.remove('open');
            overlay.classList.remove('active');
        }
    }
    
    placePixelFromButton() {
        if (this.selectedPixelX !== null && this.selectedPixelY !== null && !this.cooldownEndTime) {
            this.placePixel(this.selectedPixelX, this.selectedPixelY);
        }
    }
    
    showCrosshair(x, y) {
        if (!this.crosshairVisible) return;
        
        const crosshair = document.getElementById('pixelCrosshair');
        if (crosshair) {
            const rect = this.canvas.getBoundingClientRect();
            const pixelX = (x + this.offsetX) * this.pixelSize * this.zoom;
            const pixelY = (y + this.offsetY) * this.pixelSize * this.zoom;
            
            crosshair.style.width = rect.width + 'px';
            crosshair.style.height = rect.height + 'px';
            crosshair.style.transform = `translate(${pixelX}px, ${pixelY}px)`;
        }
    }
    
    showQuickPreview(x, y) {
        // Show a quick visual feedback before placing
        const pixelX = (x + this.offsetX) * this.pixelSize * this.zoom;
        const pixelY = (y + this.offsetY) * this.pixelSize * this.zoom;
        
        // Create pulse effect
        let radius = 0;
        const maxRadius = this.pixelSize * this.zoom * 1.5;
        const animate = () => {
            if (radius < maxRadius) {
                this.redraw();
                this.ctx.save();
                
                // Draw preview pixel
                this.ctx.fillStyle = this.selectedColor;
                this.ctx.globalAlpha = 0.7;
                this.ctx.fillRect(pixelX, pixelY, this.pixelSize * this.zoom, this.pixelSize * this.zoom);
                
                // Draw pulse ring
                this.ctx.strokeStyle = this.selectedColor;
                this.ctx.globalAlpha = 1 - (radius / maxRadius);
                this.ctx.lineWidth = 2;
                this.ctx.beginPath();
                this.ctx.arc(
                    pixelX + (this.pixelSize * this.zoom) / 2,
                    pixelY + (this.pixelSize * this.zoom) / 2,
                    radius,
                    0,
                    2 * Math.PI
                );
                this.ctx.stroke();
                this.ctx.restore();
                
                radius += 3;
                requestAnimationFrame(animate);
            }
        };
        requestAnimationFrame(animate);
    }
    
    createConfirmationUI(x, y) {
        // Remove existing confirmation UI
        const existingUI = document.getElementById('pixelConfirmation');
        if (existingUI) {
            existingUI.remove();
        }
        
        // Create confirmation dialog
        const confirmUI = document.createElement('div');
        confirmUI.id = 'pixelConfirmation';
        confirmUI.className = 'pixel-confirmation';
        confirmUI.innerHTML = `
            <div class="confirmation-header">
                <div class="pixel-coords">Position: (${x}, ${y})</div>
                <div class="pixel-color-preview" style="background-color: ${this.selectedColor}"></div>
            </div>
            <div class="confirmation-buttons">
                <button id="confirmPixel" class="btn-confirm">
                    <span>✓</span>
                    Place Pixel
                </button>
                <button id="cancelPixel" class="btn-cancel">
                    <span>✗</span>
                    Cancel
                </button>
            </div>
            <div class="confirmation-hint">Tap ✓ to confirm or ✗ to reselect</div>
        `;
        
        document.body.appendChild(confirmUI);
        
        // Add event listeners
        document.getElementById('confirmPixel').addEventListener('click', () => {
            this.confirmPixelPlacement();
        });
        
        document.getElementById('cancelPixel').addEventListener('click', () => {
            this.clearPreview();
        });
        
        // Auto-hide after 10 seconds
        setTimeout(() => {
            if (this.previewActive) {
                this.clearPreview();
            }
        }, 10000);
    }
    
    showMagnifier(x, y) {
        // Create magnifier overlay
        const magnifier = document.createElement('div');
        magnifier.id = 'pixelMagnifier';
        magnifier.className = 'pixel-magnifier';
        
        // Create mini canvas for magnified view
        const magCanvas = document.createElement('canvas');
        magCanvas.width = 150;
        magCanvas.height = 150;
        const magCtx = magCanvas.getContext('2d');
        
        // Draw magnified area (5x5 grid around selected pixel)
        const gridSize = 5;
        const magPixelSize = 30;
        
        for (let dy = -2; dy <= 2; dy++) {
            for (let dx = -2; dx <= 2; dx++) {
                const px = x + dx;
                const py = y + dy;
                
                if (px >= 0 && px < this.config.width && py >= 0 && py < this.config.height) {
                    const pixel = this.pixels[`${px},${py}`];
                    magCtx.fillStyle = pixel ? pixel.color : '#ffffff';
                } else {
                    magCtx.fillStyle = '#f0f0f0';
                }
                
                magCtx.fillRect(
                    (dx + 2) * magPixelSize,
                    (dy + 2) * magPixelSize,
                    magPixelSize,
                    magPixelSize
                );
                
                // Draw grid
                magCtx.strokeStyle = '#ddd';
                magCtx.strokeRect(
                    (dx + 2) * magPixelSize,
                    (dy + 2) * magPixelSize,
                    magPixelSize,
                    magPixelSize
                );
            }
        }
        
        // Highlight selected pixel
        magCtx.strokeStyle = this.selectedColor;
        magCtx.lineWidth = 3;
        magCtx.strokeRect(2 * magPixelSize, 2 * magPixelSize, magPixelSize, magPixelSize);
        
        // Add preview of new color
        magCtx.fillStyle = this.selectedColor;
        magCtx.globalAlpha = 0.7;
        magCtx.fillRect(2 * magPixelSize, 2 * magPixelSize, magPixelSize, magPixelSize);
        
        magnifier.appendChild(magCanvas);
        
        // Position magnifier
        const rect = this.canvas.getBoundingClientRect();
        const pixelScreenX = rect.left + (x + this.offsetX) * this.pixelSize * this.zoom;
        const pixelScreenY = rect.top + (y + this.offsetY) * this.pixelSize * this.zoom;
        
        // Position above the selected pixel if possible
        magnifier.style.left = Math.min(Math.max(10, pixelScreenX - 75), window.innerWidth - 160) + 'px';
        magnifier.style.top = Math.max(10, pixelScreenY - 170) + 'px';
        
        document.body.appendChild(magnifier);
    }
    
    drawPreviewOverlay(x, y) {
        this.ctx.save();
        
        // Draw a pulsing border around the selected pixel
        const time = Date.now() / 1000;
        const pulse = Math.sin(time * 4) * 0.3 + 0.7;
        
        const pixelX = (x + this.offsetX) * this.pixelSize * this.zoom;
        const pixelY = (y + this.offsetY) * this.pixelSize * this.zoom;
        const size = this.pixelSize * this.zoom;
        
        // Draw preview color with transparency
        this.ctx.fillStyle = this.selectedColor;
        this.ctx.globalAlpha = 0.6;
        this.ctx.fillRect(pixelX, pixelY, size, size);
        
        // Draw pulsing border
        this.ctx.strokeStyle = this.selectedColor;
        this.ctx.lineWidth = 2;
        this.ctx.globalAlpha = pulse;
        this.ctx.strokeRect(pixelX - 1, pixelY - 1, size + 2, size + 2);
        
        // Draw corner markers for better visibility
        this.ctx.globalAlpha = 1;
        this.ctx.fillStyle = '#000';
        const markerSize = 3;
        
        // Top-left
        this.ctx.fillRect(pixelX - markerSize, pixelY - markerSize, markerSize, markerSize);
        // Top-right
        this.ctx.fillRect(pixelX + size, pixelY - markerSize, markerSize, markerSize);
        // Bottom-left
        this.ctx.fillRect(pixelX - markerSize, pixelY + size, markerSize, markerSize);
        // Bottom-right
        this.ctx.fillRect(pixelX + size, pixelY + size, markerSize, markerSize);
        
        this.ctx.restore();
        
        // Continue animation if preview is active
        if (this.previewActive) {
            requestAnimationFrame(() => {
                if (this.previewActive && this.previewX === x && this.previewY === y) {
                    this.redraw();
                    this.drawPreviewOverlay(x, y);
                }
            });
        }
    }
    
    confirmPixelPlacement() {
        if (this.previewX !== null && this.previewY !== null) {
            this.placePixel(this.previewX, this.previewY);
            this.clearPreview();
        }
    }
    
    clearPreview() {
        this.previewActive = false;
        this.previewX = null;
        this.previewY = null;
        
        // Remove UI elements
        const confirmUI = document.getElementById('pixelConfirmation');
        if (confirmUI) {
            confirmUI.remove();
        }
        
        const magnifier = document.getElementById('pixelMagnifier');
        if (magnifier) {
            magnifier.remove();
        }
        
        // Redraw canvas without preview
        this.redraw();
    }
    
    showLimitReachedDialog(limitInfo, resetTime) {
        const dialog = document.createElement('div');
        dialog.className = 'limit-reached-dialog';
        dialog.innerHTML = `
            <div class="dialog-content">
                <h3>⏱️ Pixel Limit Reached</h3>
                <p>You've placed <strong>${limitInfo.placed_this_minute}/${limitInfo.max_per_minute}</strong> pixels this minute.</p>
                <p>Reset in: <span class="countdown">${resetTime}s</span></p>
                ${!limitInfo.is_registered ? `
                    <div class="upgrade-prompt">
                        <p><strong>Want more pixels?</strong></p>
                        <p>Registered users get:</p>
                        <ul>
                            <li>5 pixels per minute (vs 2)</li>
                            <li>12s cooldown (vs 30s)</li>
                        </ul>
                        <a href="/accounts/signup/" class="btn-register">Join Now - It's Free!</a>
                    </div>
                ` : ''}
                <button onclick="this.parentElement.parentElement.remove()" class="btn-close">OK</button>
            </div>
        `;
        document.body.appendChild(dialog);
        
        // Update countdown
        let timeLeft = resetTime;
        const countdownEl = dialog.querySelector('.countdown');
        const countdownInterval = setInterval(() => {
            timeLeft--;
            if (timeLeft <= 0) {
                clearInterval(countdownInterval);
                dialog.remove();
                this.pixelsRemaining = limitInfo.max_per_minute;
                this.updatePixelsRemaining();
                this.showNotification('✅ Pixel limit reset! You can place again.', 'success');
            } else {
                countdownEl.textContent = `${timeLeft}s`;
            }
        }, 1000);
    }
    
    showRegistrationPrompt() {
        const prompt = document.createElement('div');
        prompt.className = 'registration-prompt-mobile';
        prompt.innerHTML = `
            <div class="prompt-content">
                <button class="close-btn" onclick="this.parentElement.parentElement.remove()">✕</button>
                <h4>💡 Get More Pixels!</h4>
                <div class="benefits">
                    <div class="benefit">
                        <span class="icon">🎨</span>
                        <span>5 pixels/minute</span>
                    </div>
                    <div class="benefit">
                        <span class="icon">⚡</span>
                        <span>Faster cooldown</span>
                    </div>
                </div>
                <a href="/accounts/signup/" class="btn-join">Join Free</a>
            </div>
        `;
        document.body.appendChild(prompt);
        
        // Auto-hide after 8 seconds
        setTimeout(() => {
            if (prompt.parentElement) {
                prompt.remove();
            }
        }, 8000);
    }
    
    // Visual feedback methods
    shakeCanvas() {
        this.canvas.style.animation = 'shake 0.3s';
        setTimeout(() => {
            this.canvas.style.animation = '';
        }, 300);
    }
    
    showPlacingAnimation(x, y) {
        // Add a temporary overlay on the pixel being placed
        const pixelX = (x + this.offsetX) * this.pixelSize * this.zoom;
        const pixelY = (y + this.offsetY) * this.pixelSize * this.zoom;
        
        this.ctx.save();
        this.ctx.fillStyle = this.selectedColor;
        this.ctx.globalAlpha = 0.5;
        this.ctx.fillRect(pixelX, pixelY, this.pixelSize * this.zoom, this.pixelSize * this.zoom);
        this.ctx.restore();
    }
    
    removePlacingAnimation() {
        // Redraw to remove the temporary overlay
        this.redraw();
    }
    
    showPixelPlacedEffect(x, y) {
        // Create a pulse effect at the placed pixel
        const pixelX = (x + this.offsetX) * this.pixelSize * this.zoom;
        const pixelY = (y + this.offsetY) * this.pixelSize * this.zoom;
        
        let radius = 0;
        const maxRadius = this.pixelSize * this.zoom * 2;
        const animate = () => {
            if (radius < maxRadius) {
                this.redraw();
                this.ctx.save();
                this.ctx.strokeStyle = this.selectedColor;
                this.ctx.globalAlpha = 1 - (radius / maxRadius);
                this.ctx.lineWidth = 2;
                this.ctx.beginPath();
                this.ctx.arc(
                    pixelX + (this.pixelSize * this.zoom) / 2,
                    pixelY + (this.pixelSize * this.zoom) / 2,
                    radius,
                    0,
                    2 * Math.PI
                );
                this.ctx.stroke();
                this.ctx.restore();
                radius += 2;
                requestAnimationFrame(animate);
            } else {
                this.redraw();
            }
        };
        requestAnimationFrame(animate);
    }
    
    async loadCanvasState() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/vibe-coding/api/canvas-state/${this.config.id}/`);
            const data = await response.json();
            
            if (data.success) {
                this.pixels = data.pixels;
                this.redraw();
                this.updateMinimap();
            }
        } catch (error) {
            console.error('Error loading canvas:', error);
        }
    }
    
    updateMinimap() {
        if (!this.minimap || !this.minimapCtx) return;
        
        // Clear minimap
        this.minimapCtx.clearRect(0, 0, this.minimap.width, this.minimap.height);
        
        // Draw white background
        this.minimapCtx.fillStyle = '#ffffff';
        this.minimapCtx.fillRect(0, 0, this.minimap.width, this.minimap.height);
        
        // Draw pixels on minimap
        for (const key in this.pixels) {
            const [x, y] = key.split(',').map(Number);
            this.minimapCtx.fillStyle = this.pixels[key].color;
            this.minimapCtx.fillRect(
                x * this.minimapScale,
                y * this.minimapScale,
                Math.ceil(this.minimapScale),
                Math.ceil(this.minimapScale)
            );
        }
        
        // Update viewport indicator
        this.updateMinimapViewport();
    }
    
    updateMinimapViewport() {
        if (!this.minimapViewport) return;
        
        const rect = this.canvas.getBoundingClientRect();
        const viewportWidth = rect.width / (this.pixelSize * this.zoom);
        const viewportHeight = rect.height / (this.pixelSize * this.zoom);
        
        const vpX = Math.max(0, -this.offsetX) * this.minimapScale;
        const vpY = Math.max(0, -this.offsetY) * this.minimapScale;
        const vpWidth = Math.min(viewportWidth, this.config.width) * this.minimapScale;
        const vpHeight = Math.min(viewportHeight, this.config.height) * this.minimapScale;
        
        this.minimapViewport.style.left = vpX + 'px';
        this.minimapViewport.style.top = vpY + 'px';
        this.minimapViewport.style.width = vpWidth + 'px';
        this.minimapViewport.style.height = vpHeight + 'px';
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
            activityList.innerHTML = '<p class="loading">No activity yet</p>';
            return;
        }
        
        activityList.innerHTML = history.map(item => {
            const time = new Date(item.placed_at).toLocaleTimeString();
            return `
                <div class="activity-item">
                    <span class="activity-color" style="background-color: ${item.color}"></span>
                    <span class="activity-user">${item.placed_by}</span>
                    <span class="activity-time">${time}</span>
                </div>
            `;
        }).join('');
    }
    
    // Drawing functions
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
        // Save current context
        this.ctx.save();
        
        // Apply transform for this specific pixel
        this.ctx.scale(this.zoom, this.zoom);
        this.ctx.translate(this.offsetX * this.pixelSize, this.offsetY * this.pixelSize);
        
        // Draw the pixel at its grid position
        this.ctx.fillStyle = color;
        this.ctx.fillRect(x * this.pixelSize, y * this.pixelSize, this.pixelSize, this.pixelSize);
        
        // Restore context
        this.ctx.restore();
    }
    
    redraw() {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        this.ctx.save();
        this.ctx.scale(this.zoom, this.zoom);
        this.ctx.translate(this.offsetX * this.pixelSize, this.offsetY * this.pixelSize);
        
        // White background
        this.ctx.fillStyle = '#ffffff';
        this.ctx.fillRect(0, 0, this.config.width * this.pixelSize, this.config.height * this.pixelSize);
        
        // Draw pixels
        for (const key in this.pixels) {
            const [x, y] = key.split(',').map(Number);
            this.ctx.fillStyle = this.pixels[key].color;
            this.ctx.fillRect(x * this.pixelSize, y * this.pixelSize, this.pixelSize, this.pixelSize);
        }
        
        // Draw grid if enabled or zoomed in
        if (this.gridVisible || this.zoom > 1.5) {
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
        
        // Update minimap viewport position when redrawing
        this.updateMinimapViewport();
    }
    
    // UI functions
    showPixelTooltip(e, x, y) {
        const tooltip = document.getElementById('pixelTooltip');
        const pixel = this.pixels[`${x},${y}`];
        
        if (pixel) {
            tooltip.innerHTML = `
                <strong>(${x}, ${y})</strong><br>
                ${pixel.placed_by}<br>
                <span style="display: inline-block; width: 15px; height: 15px; background-color: ${pixel.color}; border: 1px solid #fff;"></span>
            `;
        } else {
            tooltip.innerHTML = `<strong>(${x}, ${y})</strong><br>Empty`;
        }
        
        tooltip.style.display = 'block';
        
        // Position tooltip to avoid going off-screen
        const rect = tooltip.getBoundingClientRect();
        let left = e.pageX + 10;
        let top = e.pageY + 10;
        
        if (left + rect.width > window.innerWidth) {
            left = e.pageX - rect.width - 10;
        }
        if (top + rect.height > window.innerHeight) {
            top = e.pageY - rect.height - 10;
        }
        
        tooltip.style.left = left + 'px';
        tooltip.style.top = top + 'px';
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

// Initialize PixelWarMobile when script loads
// Check if DOM is already loaded (for dynamically loaded scripts)
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        if (typeof CANVAS_CONFIG !== 'undefined') {
            window.pixelWar = new PixelWarMobile('pixelCanvas', CANVAS_CONFIG);
        }
    });
} else {
    // DOM is already loaded, initialize immediately
    if (typeof CANVAS_CONFIG !== 'undefined') {
        window.pixelWar = new PixelWarMobile('pixelCanvas', CANVAS_CONFIG);
    }
}