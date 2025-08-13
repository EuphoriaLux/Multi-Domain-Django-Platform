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
    
    setupEventListeners() {
        this.canvas.addEventListener('click', this.handleCanvasClick.bind(this));
        this.canvas.addEventListener('mousemove', this.handleMouseMove.bind(this));
        this.canvas.addEventListener('mousedown', this.handleMouseDown.bind(this));
        this.canvas.addEventListener('mouseup', this.handleMouseUp.bind(this));
        this.canvas.addEventListener('mouseleave', this.handleMouseLeave.bind(this));
        this.canvas.addEventListener('wheel', this.handleWheel.bind(this));
        
        document.querySelectorAll('.color-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.selectColor(e.target.dataset.color);
                this.highlightSelectedColor(e.target);
            });
        });
        
        document.getElementById('colorPicker').addEventListener('change', (e) => {
            this.selectColor(e.target.value);
        });
        
        document.getElementById('zoomIn').addEventListener('click', () => this.adjustZoom(0.2));
        document.getElementById('zoomOut').addEventListener('click', () => this.adjustZoom(-0.2));
        document.getElementById('zoomReset').addEventListener('click', () => this.resetZoom());
    }
    
    handleCanvasClick(e) {
        if (this.isDragging) return;
        
        const rect = this.canvas.getBoundingClientRect();
        const x = Math.floor((e.clientX - rect.left) / (this.pixelSize * this.zoom) - this.offsetX);
        const y = Math.floor((e.clientY - rect.top) / (this.pixelSize * this.zoom) - this.offsetY);
        
        if (x >= 0 && x < this.config.width && y >= 0 && y < this.config.height) {
            this.placePixel(x, y);
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
    
    handleMouseDown(e) {
        if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
            this.isDragging = true;
            this.dragStartX = e.clientX - this.offsetX * this.pixelSize * this.zoom;
            this.dragStartY = e.clientY - this.offsetY * this.pixelSize * this.zoom;
            this.canvas.style.cursor = 'grabbing';
        }
    }
    
    handleMouseUp(e) {
        this.isDragging = false;
        this.canvas.style.cursor = 'crosshair';
    }
    
    handleMouseLeave(e) {
        this.isDragging = false;
        this.hidePixelTooltip();
        this.canvas.style.cursor = 'crosshair';
    }
    
    handleWheel(e) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        this.adjustZoom(delta);
    }
    
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
        if (this.cooldownEndTime && Date.now() < this.cooldownEndTime) {
            this.showNotification('Please wait for cooldown!', 'error');
            return;
        }
        
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
                
                // Update cooldown and pixels remaining
                if (data.cooldown_info) {
                    this.startCooldown(data.cooldown_info.cooldown_seconds);
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
                }
                this.startCooldown(data.cooldown_remaining);
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

document.addEventListener('DOMContentLoaded', () => {
    if (typeof CANVAS_CONFIG !== 'undefined') {
        const pixelWar = new PixelWar('pixelCanvas', CANVAS_CONFIG);
    }
});