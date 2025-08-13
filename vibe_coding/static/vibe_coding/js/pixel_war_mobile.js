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
        this.apiBaseUrl = '';
        
        // Cooldown settings based on auth
        this.cooldownSeconds = config.isAuthenticated ? config.registeredCooldown : config.anonymousCooldown;
        this.maxPixelsPerMinute = config.isAuthenticated ? config.registeredPixelsPerMinute : config.anonymousPixelsPerMinute;
        
        this.init();
    }
    
    init() {
        this.setupCanvas();
        this.setupEventListeners();
        this.setupMobileHelpers();
        this.loadCanvasState();
        this.startUpdateLoop();
        this.loadRecentActivity();
        this.updatePixelsRemaining();
        this.detectColorScheme();
    }
    
    setupMobileHelpers() {
        // Add auto-zoom on first tap for better precision
        if (this.isTouchDevice && this.zoom === 1) {
            this.autoZoomEnabled = true;
        }
        
        // Add grid overlay toggle button for mobile
        if (this.isTouchDevice) {
            this.addGridToggleButton();
        }
    }
    
    addGridToggleButton() {
        const button = document.createElement('button');
        button.id = 'gridToggle';
        button.className = 'grid-toggle-btn';
        button.innerHTML = '⊞ Grid';
        button.addEventListener('click', () => {
            this.gridVisible = !this.gridVisible;
            button.classList.toggle('active', this.gridVisible);
            this.redraw();
        });
        
        const canvasSection = document.querySelector('.canvas-section');
        if (canvasSection) {
            canvasSection.appendChild(button);
        }
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
        
        // Color selection
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.selectColor(e.target.dataset.color);
                this.highlightSelectedColor(e.target);
            });
        });
        
        document.getElementById('colorPicker').addEventListener('change', (e) => {
            this.selectColor(e.target.value);
            document.querySelectorAll('.color-btn').forEach(btn => btn.classList.remove('selected'));
        });
        
        // Zoom controls
        document.getElementById('zoomIn').addEventListener('click', () => this.adjustZoom(0.2));
        document.getElementById('zoomOut').addEventListener('click', () => this.adjustZoom(-0.2));
        document.getElementById('zoomReset').addEventListener('click', () => this.resetZoom());
        
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
            
            // Store initial touch position for tap detection
            this.touchStartX = touch.clientX;
            this.touchStartY = touch.clientY;
            this.touchStartTime = Date.now();
        } else if (e.touches.length === 2) {
            // Two fingers - prepare for pinch zoom
            this.isDragging = false;
            const distance = this.getTouchDistance(e.touches[0], e.touches[1]);
            this.lastTouchDistance = distance;
        }
    }
    
    handleTouchMove(e) {
        e.preventDefault();
        
        if (e.touches.length === 1 && !this.lastTouchDistance) {
            // Single touch drag
            const touch = e.touches[0];
            this.isDragging = true;
            this.offsetX = (touch.clientX - this.dragStartX) / (this.pixelSize * this.zoom);
            this.offsetY = (touch.clientY - this.dragStartY) / (this.pixelSize * this.zoom);
            this.redraw();
        } else if (e.touches.length === 2) {
            // Pinch zoom
            const distance = this.getTouchDistance(e.touches[0], e.touches[1]);
            if (this.lastTouchDistance) {
                const scale = distance / this.lastTouchDistance;
                this.adjustZoom((scale - 1) * 0.5);
                this.lastTouchDistance = distance;
            }
        }
    }
    
    handleTouchEnd(e) {
        // Check if it was a tap (not a drag)
        if (!this.isDragging && Date.now() - this.touchStartTime < 300) {
            const rect = this.canvas.getBoundingClientRect();
            let x = Math.floor((this.touchStartX - rect.left) / (this.pixelSize * this.zoom) - this.offsetX);
            let y = Math.floor((this.touchStartY - rect.top) / (this.pixelSize * this.zoom) - this.offsetY);
            
            // Auto-zoom for better precision on first tap
            if (this.autoZoomEnabled && this.zoom < 2) {
                // Calculate center position for zoom
                const centerX = x;
                const centerY = y;
                
                // Zoom in to 2.5x
                this.zoom = 2.5;
                
                // Adjust offset to center on tapped pixel
                this.offsetX = -(centerX - this.config.width / (2 * this.zoom));
                this.offsetY = -(centerY - this.config.height / (2 * this.zoom));
                
                // Redraw with new zoom
                this.redraw();
                
                // Recalculate position with new zoom
                x = Math.floor((this.touchStartX - rect.left) / (this.pixelSize * this.zoom) - this.offsetX);
                y = Math.floor((this.touchStartY - rect.top) / (this.pixelSize * this.zoom) - this.offsetY);
                
                // Disable auto-zoom after first use
                this.autoZoomEnabled = false;
                
                // Show notification about zoom
                this.showNotification('Zoomed in for precision. Pinch to adjust.', 'info');
            }
            
            if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
                // Instead of immediately placing, show preview
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
        const x = Math.floor((e.clientX - rect.left) / (this.pixelSize * this.zoom) - this.offsetX);
        const y = Math.floor((e.clientY - rect.top) / (this.pixelSize * this.zoom) - this.offsetY);
        
        if (this.isDragging) {
            this.offsetX = (e.clientX - this.dragStartX) / (this.pixelSize * this.zoom);
            this.offsetY = (e.clientY - this.dragStartY) / (this.pixelSize * this.zoom);
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
            const x = Math.floor((e.clientX - rect.left) / (this.pixelSize * this.zoom) - this.offsetX);
            const y = Math.floor((e.clientY - rect.top) / (this.pixelSize * this.zoom) - this.offsetY);
            
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
        this.zoom = Math.max(0.5, Math.min(5, this.zoom + delta));
        this.redraw();
    }
    
    resetZoom() {
        this.zoom = 1;
        this.offsetX = 0;
        this.offsetY = 0;
        this.redraw();
    }
    
    // Color selection
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
        // Check if cooldown is active
        if (this.cooldownEndTime && Date.now() < this.cooldownEndTime) {
            const remaining = Math.ceil((this.cooldownEndTime - Date.now()) / 1000);
            this.showNotification(`Wait ${remaining}s to place next pixel`, 'error');
            this.shakeCanvas();
            return;
        }
        
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
                
                // Update cooldown and remaining pixels
                if (data.cooldown_info) {
                    this.startCooldown(data.cooldown_info.cooldown_seconds);
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
                
                // Set cooldown timer
                if (data.cooldown_remaining) {
                    this.startCooldown(data.cooldown_remaining);
                } else if (data.error === 'Cooldown active') {
                    // Fallback if cooldown_remaining is not provided
                    this.startCooldown(this.cooldownSeconds);
                }
                
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
    showPixelPreview(x, y) {
        // Clear any existing preview
        this.clearPreview();
        
        // Store preview coordinates
        this.previewX = x;
        this.previewY = y;
        this.previewActive = true;
        
        // Create confirmation UI
        this.createConfirmationUI(x, y);
        
        // Show magnified preview
        this.showMagnifier(x, y);
        
        // Redraw with preview highlight
        this.redraw();
        
        // Add preview overlay
        this.drawPreviewOverlay(x, y);
        
        // Haptic feedback
        if (navigator.vibrate) {
            navigator.vibrate(30);
        }
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
            }
        } catch (error) {
            console.error('Error loading canvas:', error);
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
        
        // White background
        this.ctx.fillStyle = '#ffffff';
        this.ctx.fillRect(0, 0, this.config.width * this.pixelSize, this.config.height * this.pixelSize);
        
        // Draw pixels
        for (const key in this.pixels) {
            const [x, y] = key.split(',').map(Number);
            this.ctx.fillStyle = this.pixels[key].color;
            this.ctx.fillRect(x * this.pixelSize, y * this.pixelSize, this.pixelSize, this.pixelSize);
        }
        
        // Draw grid if zoomed in
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
        
        if (this.cooldownEndTime && Date.now() < this.cooldownEndTime) {
            const remaining = Math.ceil((this.cooldownEndTime - Date.now()) / 1000);
            timer.textContent = `${remaining}s`;
            timer.style.color = '#ff6b6b';
        } else {
            timer.textContent = 'Ready';
            timer.style.color = '#4caf50';
            this.cooldownEndTime = null;
        }
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

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    if (typeof CANVAS_CONFIG !== 'undefined') {
        const pixelWar = new PixelWarMobile('pixelCanvas', CANVAS_CONFIG);
    }
});