/**
 * VinsDeLux Journey Step Animations
 * Custom JavaScript animations for each journey step
 */

class JourneyStepAnimations {
    constructor() {
        this.animations = new Map();
        this.canvases = new Map();
        this.animationFrames = new Map();
        this.isAnimating = false;
        this.lastFrameTime = 0;
        this.targetFPS = 60;
        this.frameInterval = 1000 / this.targetFPS;
        this.performanceMode = this.detectPerformanceMode();
        this.init();
    }

    detectPerformanceMode() {
        // Detect if we should use reduced animation quality for performance
        const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
        const isLowPower = navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2;
        const hasLowMemory = navigator.deviceMemory && navigator.deviceMemory <= 2;
        
        return isMobile || isLowPower || hasLowMemory;
    }

    init() {
        this.replaceImagesWithAnimations();
        this.startAnimations();
        console.log('🎬 Journey step animations initialized');
    }

    replaceImagesWithAnimations() {
        const imageContainers = document.querySelectorAll('.card-image-container');
        
        imageContainers.forEach((container, index) => {
            // Clear existing content
            container.innerHTML = '';
            
            // Create canvas for animation
            const canvas = document.createElement('canvas');
            canvas.className = 'journey-animation-canvas';
            canvas.style.cssText = `
                width: 100%;
                height: 100%;
                border-radius: 12px;
                background: transparent;
            `;
            
            container.appendChild(canvas);
            
            // Set canvas size
            const rect = container.getBoundingClientRect();
            canvas.width = rect.width * window.devicePixelRatio || 400;
            canvas.height = rect.height * window.devicePixelRatio || 200;
            canvas.style.width = rect.width + 'px';
            canvas.style.height = rect.height + 'px';
            
            const ctx = canvas.getContext('2d');
            ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
            
            this.canvases.set(index, { canvas, ctx, container });
            
            // Initialize specific animation based on step
            this.initStepAnimation(index, ctx, canvas);
        });
    }

    initStepAnimation(stepIndex, ctx, canvas) {
        const width = canvas.width / (window.devicePixelRatio || 1);
        const height = canvas.height / (window.devicePixelRatio || 1);
        
        switch (stepIndex) {
            case 0: // Plot Selection - Vineyard Animation
                this.animations.set(stepIndex, new VineyardAnimation(ctx, width, height));
                break;
            case 1: // Personalize Your Wine - Customization Animation
                this.animations.set(stepIndex, new CustomizationAnimation(ctx, width, height));
                break;
            case 2: // Follow Production - Monitoring Animation
                this.animations.set(stepIndex, new MonitoringAnimation(ctx, width, height));
                break;
            case 3: // Receive and Taste - Delivery Animation
                this.animations.set(stepIndex, new DeliveryAnimation(ctx, width, height));
                break;
            case 4: // Create Legacy - Legacy Animation
                this.animations.set(stepIndex, new LegacyAnimation(ctx, width, height));
                break;
        }
    }

    startAnimations() {
        this.isAnimating = true;
        this.animate();
    }

    stopAnimations() {
        this.isAnimating = false;
        this.animationFrames.forEach(frame => cancelAnimationFrame(frame));
        this.animationFrames.clear();
    }

    animate(currentTime = 0) {
        if (!this.isAnimating) return;

        // Frame rate limiting for performance
        const deltaTime = currentTime - this.lastFrameTime;
        
        if (deltaTime >= this.frameInterval || this.lastFrameTime === 0) {
            this.animations.forEach((animation, index) => {
                if (animation && typeof animation.update === 'function') {
                    // Check if the card is visible before animating
                    const canvasData = this.canvases.get(index);
                    if (canvasData && this.isElementVisible(canvasData.container)) {
                        // Pass performance mode to animations
                        animation.performanceMode = this.performanceMode;
                        animation.update(deltaTime);
                        animation.draw();
                    }
                }
            });
            
            this.lastFrameTime = currentTime;
        }

        requestAnimationFrame((time) => this.animate(time));
    }

    // Check if element is visible in viewport
    isElementVisible(element) {
        if (!element) return false;
        
        const rect = element.getBoundingClientRect();
        const viewHeight = Math.max(document.documentElement.clientHeight, window.innerHeight);
        const viewWidth = Math.max(document.documentElement.clientWidth, window.innerWidth);
        
        return (
            rect.bottom >= 0 &&
            rect.right >= 0 &&
            rect.top <= viewHeight &&
            rect.left <= viewWidth
        );
    }

    // Handle resize
    handleResize() {
        this.canvases.forEach(({ canvas, container }, index) => {
            const rect = container.getBoundingClientRect();
            canvas.width = rect.width * window.devicePixelRatio || 400;
            canvas.height = rect.height * window.devicePixelRatio || 200;
            canvas.style.width = rect.width + 'px';
            canvas.style.height = rect.height + 'px';
            
            const ctx = canvas.getContext('2d');
            ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
            
            this.initStepAnimation(index, ctx, canvas);
        });
    }
}

// Step 1: Vineyard Animation - Rolling hills with growing vines
class VineyardAnimation {
    constructor(ctx, width, height) {
        this.ctx = ctx;
        this.width = width;
        this.height = height;
        this.time = 0;
        this.vines = [];
        this.hills = [];
        this.sun = { x: width * 0.8, y: height * 0.2, radius: 20 };
        
        this.initHills();
        this.initVines();
    }

    initHills() {
        // Create rolling hills with VinsDeLux colors
        const hillColors = [
            'rgba(45, 24, 16, 0.6)', // wine-deep with transparency
            'rgba(114, 47, 55, 0.7)', // wine-primary with transparency  
            'rgba(212, 175, 55, 0.4)' // wine-accent with transparency
        ];
        
        for (let i = 0; i < 3; i++) {
            this.hills.push({
                points: this.generateHillPoints(i),
                color: hillColors[i],
                offset: i * 20
            });
        }
    }

    generateHillPoints(layer) {
        const points = [];
        const segments = 8;
        for (let i = 0; i <= segments; i++) {
            const x = (i / segments) * this.width;
            const baseHeight = this.height * (0.6 + layer * 0.1);
            const variation = Math.sin((i / segments) * Math.PI * 3) * 30;
            points.push({ x, y: baseHeight + variation });
        }
        return points;
    }

    initVines() {
        // Create vine rows
        const rows = 4;
        const vinesPerRow = 6;
        
        for (let row = 0; row < rows; row++) {
            for (let col = 0; col < vinesPerRow; col++) {
                this.vines.push({
                    x: (col / (vinesPerRow - 1)) * this.width * 0.8 + this.width * 0.1,
                    y: this.height * (0.5 + row * 0.1),
                    height: 0,
                    maxHeight: 20 + Math.random() * 10,
                    growth: Math.random() * 0.5 + 0.5,
                    phase: Math.random() * Math.PI * 2
                });
            }
        }
    }

    update(deltaTime = 16) {
        this.time += this.performanceMode ? 0.01 : 0.02;
        
        // Update vine growth (less frequently in performance mode)
        if (!this.performanceMode || Math.floor(this.time * 10) % 2 === 0) {
            this.vines.forEach(vine => {
                const growthTarget = vine.maxHeight * (0.5 + 0.5 * Math.sin(this.time * vine.growth + vine.phase));
                vine.height += (growthTarget - vine.height) * 0.05;
            });
        }
    }

    draw() {
        this.ctx.clearRect(0, 0, this.width, this.height);
        
        // Draw sky gradient with VinsDeLux colors (simplified in performance mode)
        if (this.performanceMode) {
            this.ctx.fillStyle = '#2d1810'; // wine-deep
            this.ctx.fillRect(0, 0, this.width, this.height);
        } else {
            const skyGradient = this.ctx.createLinearGradient(0, 0, 0, this.height);
            skyGradient.addColorStop(0, '#1a1a2e'); // card-bg
            skyGradient.addColorStop(0.6, '#2d1810'); // wine-deep
            skyGradient.addColorStop(1, '#722f37'); // wine-primary
            this.ctx.fillStyle = skyGradient;
            this.ctx.fillRect(0, 0, this.width, this.height);
        }
        
        // Draw sun with wine-accent colors
        const sunGradient = this.ctx.createRadialGradient(
            this.sun.x, this.sun.y, 0,
            this.sun.x, this.sun.y, this.sun.radius
        );
        sunGradient.addColorStop(0, '#d4af37'); // wine-accent
        sunGradient.addColorStop(0.7, '#ffd700'); // wine-gold
        sunGradient.addColorStop(1, 'rgba(212, 175, 55, 0.6)'); // wine-accent with transparency
        this.ctx.fillStyle = sunGradient;
        this.ctx.beginPath();
        this.ctx.arc(this.sun.x, this.sun.y, this.sun.radius, 0, Math.PI * 2);
        this.ctx.fill();
        
        // Add subtle glow around sun
        if (!this.performanceMode) {
            const glowGradient = this.ctx.createRadialGradient(
                this.sun.x, this.sun.y, this.sun.radius,
                this.sun.x, this.sun.y, this.sun.radius * 2
            );
            glowGradient.addColorStop(0, 'rgba(212, 175, 55, 0.3)');
            glowGradient.addColorStop(1, 'rgba(212, 175, 55, 0)');
            this.ctx.fillStyle = glowGradient;
            this.ctx.beginPath();
            this.ctx.arc(this.sun.x, this.sun.y, this.sun.radius * 2, 0, Math.PI * 2);
            this.ctx.fill();
        }
        
        // Draw hills
        this.hills.forEach(hill => {
            this.ctx.fillStyle = hill.color;
            this.ctx.beginPath();
            this.ctx.moveTo(0, this.height);
            
            hill.points.forEach((point, index) => {
                const y = point.y + Math.sin(this.time + index * 0.5) * 2;
                if (index === 0) {
                    this.ctx.lineTo(point.x, y);
                } else {
                    this.ctx.lineTo(point.x, y);
                }
            });
            
            this.ctx.lineTo(this.width, this.height);
            this.ctx.closePath();
            this.ctx.fill();
        });
        
        // Draw vines
        this.vines.forEach(vine => {
            // Vine stems with wine-deep color
            this.ctx.strokeStyle = '#2d1810'; // wine-deep
            this.ctx.lineWidth = 2;
            this.ctx.beginPath();
            this.ctx.moveTo(vine.x, vine.y);
            this.ctx.lineTo(vine.x, vine.y - vine.height);
            this.ctx.stroke();
            
            // Draw leaves with wine theme colors
            if (vine.height > 5) {
                const leaves = Math.floor(vine.height / 5);
                for (let i = 0; i < leaves; i++) {
                    const leafY = vine.y - (i + 1) * 5;
                    const leafSize = 3 + Math.sin(this.time * 2 + vine.phase) * 1;
                    
                    // Gradient leaves for more sophisticated look
                    const leafGradient = this.ctx.createRadialGradient(
                        vine.x - 4, leafY, 0,
                        vine.x - 4, leafY, leafSize
                    );
                    leafGradient.addColorStop(0, '#d4af37'); // wine-accent center
                    leafGradient.addColorStop(0.7, '#722f37'); // wine-primary
                    leafGradient.addColorStop(1, '#2d1810'); // wine-deep edges
                    
                    this.ctx.fillStyle = leafGradient;
                    this.ctx.beginPath();
                    this.ctx.ellipse(vine.x - 4, leafY, leafSize, leafSize * 0.7, 0, 0, Math.PI * 2);
                    this.ctx.fill();
                    
                    this.ctx.beginPath();
                    this.ctx.ellipse(vine.x + 4, leafY, leafSize, leafSize * 0.7, 0, 0, Math.PI * 2);
                    this.ctx.fill();
                    
                    // Add small wine berries on mature vines
                    if (vine.height > 15 && i % 2 === 0) {
                        this.ctx.fillStyle = '#722f37'; // wine-primary for berries
                        this.ctx.beginPath();
                        this.ctx.arc(vine.x, leafY - 2, 1.5, 0, Math.PI * 2);
                        this.ctx.fill();
                    }
                }
            }
        });
    }
}

// Step 2: Customization Animation - Wine glass filling with color selection
class CustomizationAnimation {
    constructor(ctx, width, height) {
        this.ctx = ctx;
        this.width = width;
        this.height = height;
        this.time = 0;
        this.wineLevel = 0;
        this.targetWineLevel = 0.7;
        this.colorPalette = [
            '#722f37', // wine-primary
            '#2d1810', // wine-deep  
            '#d4af37', // wine-accent (for white/golden wines)
            '#e6b3ba', // wine-rose (for rosé wines)
            '#8B4513', // Sophisticated brown for aged wines
            '#4A148C'  // Deep purple for premium vintages
        ];
        this.currentColorIndex = 0;
        this.colorTransition = 0;
        this.bubbles = [];
        this.initBubbles();
    }

    initBubbles() {
        for (let i = 0; i < 12; i++) {
            this.bubbles.push({
                x: this.width * 0.4 + Math.random() * this.width * 0.2,
                y: this.height,
                radius: Math.random() * 3 + 1,
                speed: Math.random() * 2 + 1,
                opacity: Math.random() * 0.5 + 0.3,
                phase: Math.random() * Math.PI * 2
            });
        }
    }

    update() {
        this.time += 0.03;
        
        // Wine level animation
        this.wineLevel += (this.targetWineLevel - this.wineLevel) * 0.02;
        
        // Color cycling
        this.colorTransition += 0.01;
        if (this.colorTransition >= 1) {
            this.colorTransition = 0;
            this.currentColorIndex = (this.currentColorIndex + 1) % this.colorPalette.length;
        }
        
        // Update bubbles
        this.bubbles.forEach(bubble => {
            bubble.y -= bubble.speed;
            bubble.x += Math.sin(this.time * 2 + bubble.phase) * 0.5;
            
            if (bubble.y < this.height * 0.3) {
                bubble.y = this.height;
                bubble.x = this.width * 0.4 + Math.random() * this.width * 0.2;
            }
        });
    }

    draw() {
        this.ctx.clearRect(0, 0, this.width, this.height);
        
        // Draw sophisticated background with VinsDeLux colors
        const bgGradient = this.ctx.createLinearGradient(0, 0, 0, this.height);
        bgGradient.addColorStop(0, '#1a1a2e'); // card-bg 
        bgGradient.addColorStop(0.5, '#2d1810'); // wine-deep
        bgGradient.addColorStop(1, '#0a0a0f'); // dark-bg
        this.ctx.fillStyle = bgGradient;
        this.ctx.fillRect(0, 0, this.width, this.height);
        
        // Add subtle texture overlay
        if (!this.performanceMode) {
            this.ctx.fillStyle = 'rgba(212, 175, 55, 0.05)'; // wine-accent subtle overlay
            this.ctx.fillRect(0, 0, this.width, this.height);
        }
        
        // Draw wine glass outline with elegant styling
        const centerX = this.width / 2;
        const glassTop = this.height * 0.2;
        const glassBottom = this.height * 0.8;
        const glassWidth = this.width * 0.3;
        
        // Glass outline with wine-accent color
        this.ctx.strokeStyle = 'rgba(212, 175, 55, 0.9)'; // wine-accent
        this.ctx.lineWidth = 3;
        this.ctx.beginPath();
        
        // Glass bowl
        this.ctx.moveTo(centerX - glassWidth/2, glassTop);
        this.ctx.quadraticCurveTo(centerX - glassWidth/2, glassBottom - 20, centerX - 10, glassBottom - 20);
        this.ctx.lineTo(centerX + 10, glassBottom - 20);
        this.ctx.quadraticCurveTo(centerX + glassWidth/2, glassBottom - 20, centerX + glassWidth/2, glassTop);
        
        // Glass stem
        this.ctx.moveTo(centerX - 10, glassBottom - 20);
        this.ctx.lineTo(centerX - 10, glassBottom);
        this.ctx.lineTo(centerX + 10, glassBottom);
        this.ctx.lineTo(centerX + 10, glassBottom - 20);
        
        this.ctx.stroke();
        
        // Draw wine with color transition
        if (this.wineLevel > 0) {
            const currentColor = this.colorPalette[this.currentColorIndex];
            const nextColor = this.colorPalette[(this.currentColorIndex + 1) % this.colorPalette.length];
            
            const wineGradient = this.ctx.createLinearGradient(0, glassBottom - 20, 0, glassTop);
            wineGradient.addColorStop(0, this.interpolateColor(currentColor, nextColor, this.colorTransition));
            wineGradient.addColorStop(1, this.interpolateColor(currentColor, nextColor, this.colorTransition, 0.7));
            
            this.ctx.fillStyle = wineGradient;
            this.ctx.beginPath();
            
            const wineHeight = (glassBottom - glassTop - 20) * this.wineLevel;
            const wineTop = glassBottom - 20 - wineHeight;
            const wineWidthTop = (glassWidth/2 - 10) * (this.wineLevel * 0.8 + 0.2);
            
            this.ctx.moveTo(centerX - 10, glassBottom - 20);
            this.ctx.lineTo(centerX - wineWidthTop, wineTop);
            
            // Wine surface with wave effect
            const segments = 20;
            for (let i = 0; i <= segments; i++) {
                const x = centerX - wineWidthTop + (2 * wineWidthTop * i / segments);
                const wave = Math.sin(this.time * 3 + i * 0.5) * 2;
                this.ctx.lineTo(x, wineTop + wave);
            }
            
            this.ctx.lineTo(centerX + 10, glassBottom - 20);
            this.ctx.closePath();
            this.ctx.fill();
        }
        
        // Draw bubbles with wine-accent highlights
        this.bubbles.forEach(bubble => {
            if (bubble.y < glassBottom - 20 && bubble.y > glassTop) {
                // Create gradient bubble with wine theme
                const bubbleGradient = this.ctx.createRadialGradient(
                    bubble.x, bubble.y, 0,
                    bubble.x, bubble.y, bubble.radius
                );
                bubbleGradient.addColorStop(0, `rgba(212, 175, 55, ${bubble.opacity * 0.8})`); // wine-accent core
                bubbleGradient.addColorStop(0.7, `rgba(255, 255, 255, ${bubble.opacity * 0.6})`); // white highlight
                bubbleGradient.addColorStop(1, `rgba(212, 175, 55, ${bubble.opacity * 0.2})`); // wine-accent edge
                
                this.ctx.fillStyle = bubbleGradient;
                this.ctx.beginPath();
                this.ctx.arc(bubble.x, bubble.y, bubble.radius, 0, Math.PI * 2);
                this.ctx.fill();
            }
        });
        
        // Draw color palette with elegant styling
        const paletteY = this.height * 0.1;
        this.colorPalette.forEach((color, index) => {
            const x = this.width * 0.1 + index * 25;
            const isActive = index === this.currentColorIndex;
            const radius = isActive ? 10 : 7;
            
            // Color swatch with gradient
            const swatchGradient = this.ctx.createRadialGradient(x, paletteY, 0, x, paletteY, radius);
            swatchGradient.addColorStop(0, color);
            swatchGradient.addColorStop(1, this.darkenColor(color, 0.3));
            
            this.ctx.fillStyle = swatchGradient;
            this.ctx.beginPath();
            this.ctx.arc(x, paletteY, radius, 0, Math.PI * 2);
            this.ctx.fill();
            
            // Active indicator with wine-accent
            if (isActive) {
                this.ctx.strokeStyle = '#d4af37'; // wine-accent
                this.ctx.lineWidth = 3;
                this.ctx.stroke();
                
                // Add subtle glow for active swatch
                const glowGradient = this.ctx.createRadialGradient(x, paletteY, radius, x, paletteY, radius + 5);
                glowGradient.addColorStop(0, 'rgba(212, 175, 55, 0.3)');
                glowGradient.addColorStop(1, 'rgba(212, 175, 55, 0)');
                this.ctx.fillStyle = glowGradient;
                this.ctx.beginPath();
                this.ctx.arc(x, paletteY, radius + 5, 0, Math.PI * 2);
                this.ctx.fill();
            } else {
                // Subtle border for inactive swatches
                this.ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
                this.ctx.lineWidth = 1;
                this.ctx.stroke();
            }
        });
    }

    interpolateColor(color1, color2, factor, alpha = 1) {
        const c1 = this.hexToRgb(color1);
        const c2 = this.hexToRgb(color2);
        
        const r = Math.round(c1.r + (c2.r - c1.r) * factor);
        const g = Math.round(c1.g + (c2.g - c1.g) * factor);
        const b = Math.round(c1.b + (c2.b - c1.b) * factor);
        
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    hexToRgb(hex) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        return result ? {
            r: parseInt(result[1], 16),
            g: parseInt(result[2], 16),
            b: parseInt(result[3], 16)
        } : null;
    }
    
    darkenColor(hex, factor) {
        const rgb = this.hexToRgb(hex);
        if (!rgb) return hex;
        
        return `rgb(${Math.floor(rgb.r * (1 - factor))}, ${Math.floor(rgb.g * (1 - factor))}, ${Math.floor(rgb.b * (1 - factor))})`;
    }
}

// Step 3: Monitoring Animation - Dashboard with real-time data
class MonitoringAnimation {
    constructor(ctx, width, height) {
        this.ctx = ctx;
        this.width = width;
        this.height = height;
        this.time = 0;
        this.dataPoints = [];
        this.gauges = [];
        this.notifications = [];
        this.initializeData();
    }

    initializeData() {
        // Initialize data points for graph
        for (let i = 0; i < 50; i++) {
            this.dataPoints.push({
                temperature: 50 + Math.sin(i * 0.1) * 10 + Math.random() * 5,
                humidity: 60 + Math.cos(i * 0.15) * 15 + Math.random() * 5,
                ph: 3.5 + Math.sin(i * 0.08) * 0.3 + Math.random() * 0.1
            });
        }
        
        // Initialize gauges with VinsDeLux theme colors
        this.gauges = [
            { label: 'Temp', value: 0, target: 75, color: '#d4af37', unit: '°C' }, // wine-accent
            { label: 'Humidity', value: 0, target: 65, color: '#e6b3ba', unit: '%' }, // wine-rose
            { label: 'pH', value: 0, target: 3.8, color: '#722f37', unit: '' } // wine-primary
        ];
    }

    update() {
        this.time += 0.02;
        
        // Update gauge values
        this.gauges.forEach(gauge => {
            const variation = Math.sin(this.time * 2) * 5;
            gauge.value += (gauge.target + variation - gauge.value) * 0.1;
        });
        
        // Add new data point
        if (Math.floor(this.time * 10) % 5 === 0) {
            this.dataPoints.shift();
            this.dataPoints.push({
                temperature: 50 + Math.sin(this.time) * 10 + Math.random() * 5,
                humidity: 60 + Math.cos(this.time * 1.2) * 15 + Math.random() * 5,
                ph: 3.5 + Math.sin(this.time * 0.8) * 0.3 + Math.random() * 0.1
            });
        }
        
        // Update notifications
        if (Math.random() < 0.003 && this.notifications.length < 3) {
            const messages = [
                'Temperature optimal',
                'Humidity adjusted',
                'pH levels stable',
                'Growth detected',
                'Quality check passed'
            ];
            this.notifications.push({
                message: messages[Math.floor(Math.random() * messages.length)],
                time: this.time,
                opacity: 1
            });
        }
        
        this.notifications = this.notifications.filter(notif => {
            notif.opacity = Math.max(0, 1 - (this.time - notif.time) * 0.5);
            return notif.opacity > 0;
        });
    }

    draw() {
        this.ctx.clearRect(0, 0, this.width, this.height);
        
        // Draw sophisticated dashboard background
        const bgGradient = this.ctx.createLinearGradient(0, 0, 0, this.height);
        bgGradient.addColorStop(0, '#0a0a0f'); // dark-bg
        bgGradient.addColorStop(0.3, '#1a1a2e'); // card-bg
        bgGradient.addColorStop(0.7, '#2d1810'); // wine-deep
        bgGradient.addColorStop(1, '#0a0a0f'); // dark-bg
        this.ctx.fillStyle = bgGradient;
        this.ctx.fillRect(0, 0, this.width, this.height);
        
        // Draw elegant grid pattern with wine-accent
        this.ctx.strokeStyle = 'rgba(212, 175, 55, 0.1)'; // wine-accent with low opacity
        this.ctx.lineWidth = 1;
        for (let i = 0; i < this.width; i += 20) {
            this.ctx.beginPath();
            this.ctx.moveTo(i, 0);
            this.ctx.lineTo(i, this.height);
            this.ctx.stroke();
        }
        for (let i = 0; i < this.height; i += 20) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, i);
            this.ctx.lineTo(this.width, i);
            this.ctx.stroke();
        }
        
        // Draw data graph with elegant border
        const graphX = 20;
        const graphY = 20;
        const graphWidth = this.width - 140;
        const graphHeight = this.height * 0.6;
        
        // Graph border with wine-accent
        this.ctx.strokeStyle = 'rgba(212, 175, 55, 0.5)'; // wine-accent
        this.ctx.strokeRect(graphX, graphY, graphWidth, graphHeight);
        
        // Draw temperature line with wine-accent
        this.ctx.strokeStyle = '#d4af37'; // wine-accent
        this.ctx.lineWidth = 2;
        this.ctx.beginPath();
        this.dataPoints.forEach((point, index) => {
            const x = graphX + (index / (this.dataPoints.length - 1)) * graphWidth;
            const y = graphY + graphHeight - (point.temperature / 100) * graphHeight;
            if (index === 0) this.ctx.moveTo(x, y);
            else this.ctx.lineTo(x, y);
        });
        this.ctx.stroke();
        
        // Draw humidity line with wine-rose
        this.ctx.strokeStyle = '#e6b3ba'; // wine-rose
        this.ctx.beginPath();
        this.dataPoints.forEach((point, index) => {
            const x = graphX + (index / (this.dataPoints.length - 1)) * graphWidth;
            const y = graphY + graphHeight - (point.humidity / 100) * graphHeight;
            if (index === 0) this.ctx.moveTo(x, y);
            else this.ctx.lineTo(x, y);
        });
        this.ctx.stroke();
        
        // Draw gauges
        const gaugeCenterX = this.width - 60;
        const gaugeStartY = 30;
        const gaugeSpacing = 50;
        
        this.gauges.forEach((gauge, index) => {
            const centerY = gaugeStartY + index * gaugeSpacing;
            const radius = 20;
            
            // Gauge background
            this.ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
            this.ctx.lineWidth = 4;
            this.ctx.beginPath();
            this.ctx.arc(gaugeCenterX, centerY, radius, 0, Math.PI * 2);
            this.ctx.stroke();
            
            // Gauge value
            this.ctx.strokeStyle = gauge.color;
            this.ctx.lineWidth = 4;
            this.ctx.beginPath();
            const angle = (gauge.value / 100) * Math.PI * 2 - Math.PI / 2;
            this.ctx.arc(gaugeCenterX, centerY, radius, -Math.PI / 2, angle);
            this.ctx.stroke();
            
            // Gauge label
            this.ctx.fillStyle = 'white';
            this.ctx.font = '10px Arial';
            this.ctx.textAlign = 'center';
            this.ctx.fillText(gauge.label, gaugeCenterX, centerY - 30);
            this.ctx.fillText(`${gauge.value.toFixed(1)}${gauge.unit}`, gaugeCenterX, centerY + 5);
        });
        
        // Draw notifications with elegant styling
        this.notifications.forEach((notif, index) => {
            // Notification background
            const notifY = this.height - 40 + index * 15;
            this.ctx.fillStyle = `rgba(45, 24, 16, ${notif.opacity * 0.8})`; // wine-deep background
            this.ctx.fillRect(15, notifY - 10, this.width - 30, 12);
            
            // Notification text
            this.ctx.fillStyle = `rgba(212, 175, 55, ${notif.opacity})`; // wine-accent text
            this.ctx.font = '11px Arial';
            this.ctx.textAlign = 'left';
            this.ctx.fillText(notif.message, 20, notifY);
        });
        
        // Draw elegant title with gradient effect
        const titleGradient = this.ctx.createLinearGradient(20, 10, 200, 20);
        titleGradient.addColorStop(0, '#d4af37'); // wine-accent
        titleGradient.addColorStop(1, '#ffd700'); // wine-gold
        this.ctx.fillStyle = titleGradient;
        this.ctx.font = 'bold 14px Arial';
        this.ctx.textAlign = 'left';
        this.ctx.fillText('Wine Production Monitor', 20, 15);
    }
}

// Step 4: Delivery Animation - Package moving with tracking
class DeliveryAnimation {
    constructor(ctx, width, height) {
        this.ctx = ctx;
        this.width = width;
        this.height = height;
        this.time = 0;
        this.truck = {
            x: -100,
            y: this.height * 0.7,
            speed: 2,
            targetX: this.width + 100
        };
        this.package = {
            x: this.width / 2,
            y: this.height * 0.4,
            bounce: 0,
            delivered: false
        };
        this.trackingDots = [];
        this.initTrackingDots();
    }

    initTrackingDots() {
        const stages = ['Packed', 'Shipped', 'In Transit', 'Out for Delivery', 'Delivered'];
        stages.forEach((stage, index) => {
            this.trackingDots.push({
                x: 50 + index * (this.width - 100) / (stages.length - 1),
                y: this.height * 0.2,
                stage,
                active: false,
                progress: 0
            });
        });
    }

    update() {
        this.time += 0.03;
        
        // Update truck position
        this.truck.x += this.truck.speed;
        if (this.truck.x > this.width + 100) {
            this.truck.x = -100;
            this.package.delivered = false;
        }
        
        // Update package delivery
        if (this.truck.x > this.width / 2 - 50 && this.truck.x < this.width / 2 + 50) {
            this.package.delivered = true;
            this.package.bounce = Math.sin(this.time * 8) * 3;
        }
        
        // Update tracking dots
        const truckProgress = Math.max(0, Math.min(1, (this.truck.x + 100) / (this.width + 200)));
        this.trackingDots.forEach((dot, index) => {
            const dotProgress = index / (this.trackingDots.length - 1);
            dot.active = truckProgress > dotProgress;
            dot.progress = Math.max(0, Math.min(1, (truckProgress - dotProgress) * 5));
        });
    }

    draw() {
        this.ctx.clearRect(0, 0, this.width, this.height);
        
        // Draw sophisticated delivery background
        const bgGradient = this.ctx.createLinearGradient(0, 0, 0, this.height);
        bgGradient.addColorStop(0, '#1a1a2e'); // card-bg for sky
        bgGradient.addColorStop(0.6, '#2d1810'); // wine-deep horizon
        bgGradient.addColorStop(1, '#722f37'); // wine-primary ground
        this.ctx.fillStyle = bgGradient;
        this.ctx.fillRect(0, 0, this.width, this.height);
        
        // Draw elegant road
        const roadGradient = this.ctx.createLinearGradient(0, this.height * 0.75, 0, this.height);
        roadGradient.addColorStop(0, '#2d1810'); // wine-deep road surface
        roadGradient.addColorStop(1, '#0a0a0f'); // dark-bg road bottom
        this.ctx.fillStyle = roadGradient;
        this.ctx.fillRect(0, this.height * 0.75, this.width, this.height * 0.25);
        
        // Draw road markings with wine-accent
        this.ctx.fillStyle = '#d4af37'; // wine-accent markings
        const markingOffset = (this.time * 50) % 40;
        for (let i = -markingOffset; i < this.width + 40; i += 40) {
            this.ctx.fillRect(i, this.height * 0.83, 20, 4);
        }
        
        // Draw elegant tracking line
        this.ctx.strokeStyle = 'rgba(212, 175, 55, 0.8)'; // wine-accent tracking line
        this.ctx.lineWidth = 3;
        this.ctx.setLineDash([8, 4]); // More elegant dash pattern
        this.ctx.beginPath();
        this.ctx.moveTo(this.trackingDots[0].x, this.trackingDots[0].y);
        this.trackingDots.forEach(dot => {
            this.ctx.lineTo(dot.x, dot.y);
        });
        this.ctx.stroke();
        this.ctx.setLineDash([]);
        
        // Draw tracking dots
        this.trackingDots.forEach((dot, index) => {
            // Dot background
            this.ctx.fillStyle = dot.active ? '#d4af37' : 'rgba(255, 255, 255, 0.5)';
            this.ctx.beginPath();
            this.ctx.arc(dot.x, dot.y, 8, 0, Math.PI * 2);
            this.ctx.fill();
            
            // Dot border
            this.ctx.strokeStyle = dot.active ? '#b8941f' : 'rgba(255, 255, 255, 0.8)';
            this.ctx.lineWidth = 2;
            this.ctx.stroke();
            
            // Progress ring
            if (dot.progress > 0) {
                this.ctx.strokeStyle = '#00ff00';
                this.ctx.lineWidth = 3;
                this.ctx.beginPath();
                this.ctx.arc(dot.x, dot.y, 12, -Math.PI / 2, -Math.PI / 2 + dot.progress * Math.PI * 2);
                this.ctx.stroke();
            }
            
            // Stage label
            this.ctx.fillStyle = 'black';
            this.ctx.font = '10px Arial';
            this.ctx.textAlign = 'center';
            this.ctx.fillText(dot.stage, dot.x, dot.y + 25);
        });
        
        // Draw truck
        if (this.truck.x > -50 && this.truck.x < this.width + 50) {
            // Truck shadow
            this.ctx.fillStyle = 'rgba(0, 0, 0, 0.2)';
            this.ctx.fillRect(this.truck.x + 5, this.truck.y + 25, 80, 10);
            
            // Truck body with wine theme
            const truckGradient = this.ctx.createLinearGradient(this.truck.x, this.truck.y, this.truck.x, this.truck.y + 30);
            truckGradient.addColorStop(0, '#722f37'); // wine-primary
            truckGradient.addColorStop(1, '#2d1810'); // wine-deep
            this.ctx.fillStyle = truckGradient;
            this.ctx.fillRect(this.truck.x, this.truck.y, 80, 30);
            
            // Truck cab with accent
            this.ctx.fillStyle = '#d4af37'; // wine-accent cab
            this.ctx.fillRect(this.truck.x + 60, this.truck.y - 15, 20, 15);
            
            // Truck wheels
            this.ctx.fillStyle = '#2F4F4F';
            this.ctx.beginPath();
            this.ctx.arc(this.truck.x + 15, this.truck.y + 30, 8, 0, Math.PI * 2);
            this.ctx.fill();
            this.ctx.beginPath();
            this.ctx.arc(this.truck.x + 65, this.truck.y + 30, 8, 0, Math.PI * 2);
            this.ctx.fill();
            
            // Truck logo
            this.ctx.fillStyle = 'white';
            this.ctx.font = 'bold 10px Arial';
            this.ctx.textAlign = 'center';
            this.ctx.fillText('VDL', this.truck.x + 40, this.truck.y + 20);
        }
        
        // Draw package
        const packageY = this.package.y + this.package.bounce;
        
        // Package shadow
        this.ctx.fillStyle = 'rgba(0, 0, 0, 0.2)';
        this.ctx.fillRect(this.package.x - 12, this.package.y + 20, 24, 4);
        
        // Package box with wine theme
        this.ctx.fillStyle = '#2d1810'; // wine-deep box
        this.ctx.fillRect(this.package.x - 10, packageY - 10, 20, 20);
        
        // Package tape with wine-accent
        this.ctx.fillStyle = '#d4af37'; // wine-accent tape
        this.ctx.fillRect(this.package.x - 10, packageY - 2, 20, 4);
        this.ctx.fillRect(this.package.x - 2, packageY - 10, 4, 20);
        
        // Package label
        this.ctx.fillStyle = 'white';
        this.ctx.fillRect(this.package.x - 8, packageY + 2, 16, 6);
        this.ctx.fillStyle = 'black';
        this.ctx.font = '6px Arial';
        this.ctx.textAlign = 'center';
        this.ctx.fillText('VinsDeLux', this.package.x, packageY + 6);
        
        // Elegant delivery status
        if (this.package.delivered) {
            // Create gradient text for delivery status
            const deliveryGradient = this.ctx.createLinearGradient(
                this.width / 2 - 50, this.height * 0.1,
                this.width / 2 + 50, this.height * 0.1
            );
            deliveryGradient.addColorStop(0, '#d4af37'); // wine-accent
            deliveryGradient.addColorStop(0.5, '#ffd700'); // wine-gold  
            deliveryGradient.addColorStop(1, '#d4af37'); // wine-accent
            
            this.ctx.fillStyle = deliveryGradient;
            this.ctx.font = 'bold 18px Arial';
            this.ctx.textAlign = 'center';
            this.ctx.fillText('DELIVERED!', this.width / 2, this.height * 0.1);
            
            // Add subtle glow effect
            this.ctx.shadowColor = 'rgba(212, 175, 55, 0.5)';
            this.ctx.shadowBlur = 10;
            this.ctx.fillText('DELIVERED!', this.width / 2, this.height * 0.1);
            this.ctx.shadowBlur = 0; // Reset shadow
        }
    }
}

// Step 5: Legacy Animation - Family tree with wine bottles
class LegacyAnimation {
    constructor(ctx, width, height) {
        this.ctx = ctx;
        this.width = width;
        this.height = height;
        this.time = 0;
        this.tree = {
            trunk: { x: this.width / 2, y: this.height * 0.9, height: this.height * 0.4 },
            branches: [],
            bottles: []
        };
        this.stars = [];
        this.initTree();
        this.initStars();
    }

    initTree() {
        // Create main branches
        const branchCount = 5;
        for (let i = 0; i < branchCount; i++) {
            const angle = (i / (branchCount - 1) - 0.5) * Math.PI * 0.8;
            const length = 60 + Math.random() * 40;
            this.tree.branches.push({
                startX: this.tree.trunk.x,
                startY: this.tree.trunk.y - this.tree.trunk.height * 0.6,
                angle,
                length,
                bottles: []
            });
        }
        
        // Add bottles to branches
        this.tree.branches.forEach((branch, branchIndex) => {
            const bottleCount = 2 + Math.floor(Math.random() * 3);
            for (let i = 0; i < bottleCount; i++) {
                const progress = (i + 1) / (bottleCount + 1);
                const x = branch.startX + Math.cos(branch.angle) * branch.length * progress;
                const y = branch.startY + Math.sin(branch.angle) * branch.length * progress;
                
                branch.bottles.push({
                    x, y,
                    scale: 0.8 + Math.random() * 0.4,
                    vintage: 2015 + Math.floor(Math.random() * 10),
                    glow: Math.random() * Math.PI * 2,
                    color: this.getRandomWineColor()
                });
            }
        });
    }

    initStars() {
        for (let i = 0; i < 30; i++) {
            this.stars.push({
                x: Math.random() * this.width,
                y: Math.random() * this.height * 0.5,
                size: Math.random() * 2 + 1,
                twinkle: Math.random() * Math.PI * 2,
                speed: Math.random() * 0.02 + 0.01
            });
        }
    }

    getRandomWineColor() {
        const wineColors = [
            '#722f37', // wine-primary
            '#2d1810', // wine-deep
            '#d4af37', // wine-accent (golden wines)
            '#e6b3ba', // wine-rose
            '#8B4513', // aged wine brown
            '#4A148C'  // premium purple
        ];
        return wineColors[Math.floor(Math.random() * wineColors.length)];
    }

    update() {
        this.time += 0.02;
        
        // Update bottle glow
        this.tree.branches.forEach(branch => {
            branch.bottles.forEach(bottle => {
                bottle.glow += 0.05;
            });
        });
        
        // Update stars
        this.stars.forEach(star => {
            star.twinkle += star.speed;
        });
    }

    draw() {
        this.ctx.clearRect(0, 0, this.width, this.height);
        
        // Draw elegant night sky with VinsDeLux colors
        const skyGradient = this.ctx.createLinearGradient(0, 0, 0, this.height);
        skyGradient.addColorStop(0, '#0a0a0f'); // dark-bg top
        skyGradient.addColorStop(0.3, '#1a1a2e'); // card-bg 
        skyGradient.addColorStop(0.7, '#2d1810'); // wine-deep
        skyGradient.addColorStop(1, '#722f37'); // wine-primary horizon
        this.ctx.fillStyle = skyGradient;
        this.ctx.fillRect(0, 0, this.width, this.height);
        
        // Draw stars
        this.stars.forEach(star => {
            const opacity = 0.5 + 0.5 * Math.sin(star.twinkle);
            this.ctx.fillStyle = `rgba(255, 255, 255, ${opacity})`;
            this.ctx.beginPath();
            this.ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
            this.ctx.fill();
            
            // Star sparkle
            if (opacity > 0.8) {
                this.ctx.strokeStyle = `rgba(255, 255, 255, ${opacity * 0.5})`;
                this.ctx.lineWidth = 1;
                this.ctx.beginPath();
                this.ctx.moveTo(star.x - star.size * 2, star.y);
                this.ctx.lineTo(star.x + star.size * 2, star.y);
                this.ctx.moveTo(star.x, star.y - star.size * 2);
                this.ctx.lineTo(star.x, star.y + star.size * 2);
                this.ctx.stroke();
            }
        });
        
        // Draw elegant ground
        const groundGradient = this.ctx.createLinearGradient(0, this.height * 0.85, 0, this.height);
        groundGradient.addColorStop(0, '#2d1810'); // wine-deep
        groundGradient.addColorStop(1, '#0a0a0f'); // dark-bg
        this.ctx.fillStyle = groundGradient;
        this.ctx.fillRect(0, this.height * 0.85, this.width, this.height * 0.15);
        
        // Draw tree trunk with wine theme
        const trunkWidth = 20;
        const trunkGradient = this.ctx.createLinearGradient(
            this.tree.trunk.x - trunkWidth / 2, this.tree.trunk.y - this.tree.trunk.height,
            this.tree.trunk.x + trunkWidth / 2, this.tree.trunk.y - this.tree.trunk.height
        );
        trunkGradient.addColorStop(0, '#2d1810'); // wine-deep
        trunkGradient.addColorStop(0.5, '#722f37'); // wine-primary
        trunkGradient.addColorStop(1, '#2d1810'); // wine-deep
        this.ctx.fillStyle = trunkGradient;
        this.ctx.fillRect(
            this.tree.trunk.x - trunkWidth / 2,
            this.tree.trunk.y - this.tree.trunk.height,
            trunkWidth,
            this.tree.trunk.height
        );
        
        // Draw elegant tree texture
        this.ctx.strokeStyle = '#d4af37'; // wine-accent texture lines
        this.ctx.lineWidth = 1;
        for (let i = 0; i < 5; i++) {
            const y = this.tree.trunk.y - this.tree.trunk.height + i * (this.tree.trunk.height / 5);
            this.ctx.beginPath();
            this.ctx.moveTo(this.tree.trunk.x - trunkWidth / 2, y);
            this.ctx.lineTo(this.tree.trunk.x + trunkWidth / 2, y);
            this.ctx.stroke();
        }
        
        // Draw elegant branches
        this.tree.branches.forEach(branch => {
            // Branch gradient
            const branchGradient = this.ctx.createLinearGradient(
                branch.startX, branch.startY,
                branch.startX + Math.cos(branch.angle) * branch.length,
                branch.startY + Math.sin(branch.angle) * branch.length
            );
            branchGradient.addColorStop(0, '#722f37'); // wine-primary base
            branchGradient.addColorStop(1, '#2d1810'); // wine-deep tips
            
            this.ctx.strokeStyle = branchGradient;
            this.ctx.lineWidth = 6;
            this.ctx.beginPath();
            this.ctx.moveTo(branch.startX, branch.startY);
            this.ctx.lineTo(
                branch.startX + Math.cos(branch.angle) * branch.length,
                branch.startY + Math.sin(branch.angle) * branch.length
            );
            this.ctx.stroke();
            
            // Draw bottles on branches
            branch.bottles.forEach(bottle => {
                const glowIntensity = 0.3 + 0.2 * Math.sin(bottle.glow);
                
                // Bottle glow
                const glowGradient = this.ctx.createRadialGradient(
                    bottle.x, bottle.y, 0,
                    bottle.x, bottle.y, 15 * bottle.scale
                );
                glowGradient.addColorStop(0, `rgba(212, 175, 55, ${glowIntensity})`);
                glowGradient.addColorStop(1, 'rgba(212, 175, 55, 0)');
                this.ctx.fillStyle = glowGradient;
                this.ctx.beginPath();
                this.ctx.arc(bottle.x, bottle.y, 15 * bottle.scale, 0, Math.PI * 2);
                this.ctx.fill();
                
                // Bottle body
                this.ctx.fillStyle = bottle.color;
                const bottleWidth = 6 * bottle.scale;
                const bottleHeight = 20 * bottle.scale;
                this.ctx.fillRect(
                    bottle.x - bottleWidth / 2,
                    bottle.y - bottleHeight / 2,
                    bottleWidth,
                    bottleHeight
                );
                
                // Bottle neck
                this.ctx.fillStyle = '#2F4F2F';
                this.ctx.fillRect(
                    bottle.x - bottleWidth / 4,
                    bottle.y - bottleHeight / 2 - 8 * bottle.scale,
                    bottleWidth / 2,
                    8 * bottle.scale
                );
                
                // Bottle label
                this.ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
                this.ctx.fillRect(
                    bottle.x - bottleWidth / 2 + 1,
                    bottle.y - 4 * bottle.scale,
                    bottleWidth - 2,
                    8 * bottle.scale
                );
                
                // Vintage year
                this.ctx.fillStyle = 'black';
                this.ctx.font = `${6 * bottle.scale}px Arial`;
                this.ctx.textAlign = 'center';
                this.ctx.fillText(bottle.vintage.toString(), bottle.x, bottle.y + 1);
            });
        });
        
        // Draw elegant title with gradient
        const titleGradient = this.ctx.createLinearGradient(
            this.width / 2 - 80, 25,
            this.width / 2 + 80, 35
        );
        titleGradient.addColorStop(0, '#d4af37'); // wine-accent
        titleGradient.addColorStop(0.5, '#ffd700'); // wine-gold
        titleGradient.addColorStop(1, '#d4af37'); // wine-accent
        
        this.ctx.fillStyle = titleGradient;
        this.ctx.font = 'bold 16px Arial';
        this.ctx.textAlign = 'center';
        this.ctx.fillText('Legacy Collection', this.width / 2, 30);
        
        // Draw sophisticated subtitle
        this.ctx.fillStyle = 'rgba(212, 175, 55, 0.8)'; // wine-accent muted
        this.ctx.font = '11px Arial';
        this.ctx.fillText('Preserving Memories Through Wine', this.width / 2, 50);
    }
}

// Initialize animations when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // Wait a bit for the page to fully render
    setTimeout(() => {
        if (document.querySelector('.card-image-container')) {
            window.journeyAnimations = new JourneyStepAnimations();
        }
    }, 1000);
});

// Handle window resize
window.addEventListener('resize', () => {
    if (window.journeyAnimations) {
        setTimeout(() => {
            window.journeyAnimations.handleResize();
        }, 100);
    }
});

console.log('🎬 VinsDeLux Journey Animations loaded');