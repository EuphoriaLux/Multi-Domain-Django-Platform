/**
 * Luxury Map Interactions - Premium User Experience Layer
 * Sophisticated interactions and animations for VinsDelux plot selection
 */

class LuxuryMapInteractions {
    constructor() {
        this.initializeInteractions();
        this.setupMobileGestures();
        this.initializeAccessibility();
        this.setupPerformanceMonitoring();
    }

    initializeInteractions() {
        // Premium hover effects for map elements
        this.setupMapHoverEffects();
        
        // Smooth scroll animations
        this.setupSmoothScrolling();
        
        // Selection animations
        this.setupSelectionAnimations();
        
        // Loading states
        this.setupLoadingStates();
        
        // Parallax effects for desktop
        if (window.innerWidth > 1200) {
            this.setupParallaxEffects();
        }
    }

    setupMapHoverEffects() {
        // Custom cursor for map area
        const mapContainer = document.getElementById('vineyard-map');
        if (!mapContainer) return;

        // Wine glass cursor on hover
        mapContainer.style.cursor = `url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><path fill="%23D4AF37" d="M16 2c-3 0-5 2-5 5 0 4 5 8 5 12v7h-3v2h8v-2h-3v-7c0-4 5-8 5-12 0-3-2-5-5-5z"/></svg>'), auto`;

        // Hover ripple effect
        mapContainer.addEventListener('mousemove', (e) => {
            if (!this.throttle) {
                this.createRipple(e.clientX, e.clientY);
                this.throttle = true;
                setTimeout(() => this.throttle = false, 100);
            }
        });
    }

    createRipple(x, y) {
        const ripple = document.createElement('div');
        ripple.className = 'vdl-ripple';
        ripple.style.cssText = `
            position: fixed;
            left: ${x}px;
            top: ${y}px;
            width: 20px;
            height: 20px;
            background: radial-gradient(circle, rgba(212, 175, 55, 0.3) 0%, transparent 70%);
            border-radius: 50%;
            transform: translate(-50%, -50%) scale(0);
            pointer-events: none;
            z-index: 9999;
            animation: rippleExpand 0.6s ease-out;
        `;
        
        document.body.appendChild(ripple);
        setTimeout(() => ripple.remove(), 600);
    }

    setupSmoothScrolling() {
        // Smooth scroll to sections
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', (e) => {
                e.preventDefault();
                const target = document.querySelector(anchor.getAttribute('href'));
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                    
                    // Add arrival animation
                    target.classList.add('vdl-arrival-highlight');
                    setTimeout(() => target.classList.remove('vdl-arrival-highlight'), 2000);
                }
            });
        });
    }

    setupSelectionAnimations() {
        // Enhance plot selection with luxury animations
        document.addEventListener('plotSelected', (e) => {
            this.animateSelection(e.detail);
        });

        document.addEventListener('plotDeselected', (e) => {
            this.animateDeselection(e.detail);
        });
    }

    animateSelection(plotData) {
        // Create selection burst effect
        const burst = document.createElement('div');
        burst.className = 'vdl-selection-burst';
        burst.innerHTML = `
            <svg viewBox="0 0 100 100" style="width: 200px; height: 200px;">
                <circle cx="50" cy="50" r="45" fill="none" stroke="url(#goldGradient)" stroke-width="2" opacity="0">
                    <animate attributeName="r" from="20" to="45" dur="0.5s" />
                    <animate attributeName="opacity" from="1" to="0" dur="0.5s" />
                </circle>
                <defs>
                    <linearGradient id="goldGradient">
                        <stop offset="0%" stop-color="#D4AF37" />
                        <stop offset="100%" stop-color="#B8860B" />
                    </linearGradient>
                </defs>
            </svg>
        `;
        
        // Position burst at plot location
        burst.style.cssText = `
            position: absolute;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            pointer-events: none;
            z-index: 1000;
        `;
        
        document.getElementById('vineyard-map')?.appendChild(burst);
        setTimeout(() => burst.remove(), 500);

        // Animate sidebar update
        this.animateSidebarUpdate();
        
        // Play subtle sound effect (if enabled)
        this.playSelectionSound();
    }

    animateDeselection(plotData) {
        // Gentle fade out animation
        const fadeCircle = document.createElement('div');
        fadeCircle.style.cssText = `
            position: absolute;
            width: 60px;
            height: 60px;
            border: 2px solid #722F37;
            border-radius: 50%;
            opacity: 1;
            transform: scale(1);
            animation: fadeOut 0.4s ease-out;
            pointer-events: none;
        `;
        
        document.getElementById('vineyard-map')?.appendChild(fadeCircle);
        setTimeout(() => fadeCircle.remove(), 400);
    }

    animateSidebarUpdate() {
        const sidebar = document.querySelector('.vdl-sidebar');
        if (!sidebar) return;

        sidebar.classList.add('vdl-updating');
        setTimeout(() => sidebar.classList.remove('vdl-updating'), 300);

        // Animate counter
        const counter = document.getElementById('selected-count');
        if (counter) {
            counter.classList.add('vdl-counter-pulse');
            setTimeout(() => counter.classList.remove('vdl-counter-pulse'), 400);
        }
    }

    setupLoadingStates() {
        // Premium loading animation
        const createLoader = () => {
            const loader = document.createElement('div');
            loader.className = 'vdl-premium-loader';
            loader.innerHTML = `
                <div class="vdl-wine-pour">
                    <svg viewBox="0 0 100 150" style="width: 60px; height: 90px;">
                        <path d="M40 20 Q40 40 35 60 L35 120 L65 120 L65 60 Q60 40 60 20 Z" 
                              fill="none" stroke="#722F37" stroke-width="2"/>
                        <path d="M35 120 Q35 100 50 100 Q65 100 65 120" 
                              fill="#722F37" opacity="0">
                            <animate attributeName="opacity" from="0" to="1" dur="2s" repeatCount="indefinite"/>
                            <animateTransform attributeName="transform" type="translate" 
                                            from="0 0" to="0 -60" dur="2s" repeatCount="indefinite"/>
                        </path>
                    </svg>
                    <p style="margin-top: 10px; color: #722F37; font-size: 14px;">Loading premium content...</p>
                </div>
            `;
            return loader;
        };

        // Override default loading states
        window.showPremiumLoader = () => {
            const loader = createLoader();
            document.body.appendChild(loader);
            return loader;
        };

        window.hidePremiumLoader = (loader) => {
            if (loader) {
                loader.classList.add('fade-out');
                setTimeout(() => loader.remove(), 300);
            }
        };
    }

    setupMobileGestures() {
        if (!this.isTouchDevice()) return;

        const sidebar = document.querySelector('.vdl-sidebar');
        if (!sidebar) return;

        let startY = 0;
        let currentY = 0;
        let isDragging = false;

        // Swipe to expand/collapse sidebar
        sidebar.addEventListener('touchstart', (e) => {
            startY = e.touches[0].clientY;
            isDragging = true;
            sidebar.style.transition = 'none';
        });

        sidebar.addEventListener('touchmove', (e) => {
            if (!isDragging) return;
            
            currentY = e.touches[0].clientY;
            const deltaY = currentY - startY;
            
            // Apply transform based on swipe
            if (deltaY > 0) {
                sidebar.style.transform = `translateY(${Math.min(deltaY, 200)}px)`;
            }
        });

        sidebar.addEventListener('touchend', (e) => {
            isDragging = false;
            sidebar.style.transition = '';
            
            const deltaY = currentY - startY;
            
            // Determine if should expand or collapse
            if (deltaY > 100) {
                sidebar.classList.remove('expanded');
            } else if (deltaY < -100) {
                sidebar.classList.add('expanded');
            }
            
            sidebar.style.transform = '';
            startY = 0;
            currentY = 0;
        });

        // Long press for context menu
        let longPressTimer;
        
        document.querySelectorAll('.vdl-cart-item').forEach(item => {
            item.addEventListener('touchstart', (e) => {
                longPressTimer = setTimeout(() => {
                    this.showContextMenu(e.touches[0].clientX, e.touches[0].clientY, item);
                    // Haptic feedback if available
                    if (navigator.vibrate) {
                        navigator.vibrate(50);
                    }
                }, 500);
            });

            item.addEventListener('touchend', () => {
                clearTimeout(longPressTimer);
            });

            item.addEventListener('touchmove', () => {
                clearTimeout(longPressTimer);
            });
        });
    }

    showContextMenu(x, y, item) {
        // Remove existing context menu
        document.querySelector('.vdl-context-menu')?.remove();

        const menu = document.createElement('div');
        menu.className = 'vdl-context-menu';
        menu.style.cssText = `
            position: fixed;
            left: ${x}px;
            top: ${y}px;
            background: white;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
            padding: 8px;
            z-index: 9999;
            min-width: 150px;
            animation: contextMenuAppear 0.2s ease-out;
        `;
        
        menu.innerHTML = `
            <div class="vdl-context-item" onclick="viewPlotDetails()">View Details</div>
            <div class="vdl-context-item" onclick="sharePlot()">Share</div>
            <div class="vdl-context-item" onclick="removePlot()">Remove</div>
        `;
        
        document.body.appendChild(menu);
        
        // Remove on next tap
        setTimeout(() => {
            document.addEventListener('touchstart', () => menu.remove(), { once: true });
        }, 100);
    }

    setupParallaxEffects() {
        const hero = document.querySelector('.vdl-hero');
        const mapSection = document.querySelector('.vdl-map-section');
        
        window.addEventListener('scroll', () => {
            if (!this.scrollThrottle) {
                const scrolled = window.pageYOffset;
                
                // Parallax hero background
                if (hero) {
                    hero.style.transform = `translateY(${scrolled * 0.5}px)`;
                }
                
                // Subtle map section parallax
                if (mapSection && scrolled > mapSection.offsetTop - window.innerHeight) {
                    const offset = (scrolled - (mapSection.offsetTop - window.innerHeight)) * 0.1;
                    mapSection.style.transform = `translateY(${-offset}px)`;
                }
                
                this.scrollThrottle = true;
                requestAnimationFrame(() => this.scrollThrottle = false);
            }
        });
    }

    initializeAccessibility() {
        // Enhanced keyboard navigation
        const plots = document.querySelectorAll('[data-plot-id]');
        let currentIndex = 0;

        document.addEventListener('keydown', (e) => {
            if (!plots.length) return;

            switch(e.key) {
                case 'ArrowLeft':
                    currentIndex = Math.max(0, currentIndex - 1);
                    this.focusPlot(plots[currentIndex]);
                    break;
                case 'ArrowRight':
                    currentIndex = Math.min(plots.length - 1, currentIndex + 1);
                    this.focusPlot(plots[currentIndex]);
                    break;
                case 'Enter':
                case ' ':
                    if (plots[currentIndex]) {
                        plots[currentIndex].click();
                        e.preventDefault();
                    }
                    break;
            }
        });

        // Screen reader announcements
        this.announcer = document.createElement('div');
        this.announcer.className = 'sr-only';
        this.announcer.setAttribute('aria-live', 'polite');
        this.announcer.setAttribute('aria-atomic', 'true');
        document.body.appendChild(this.announcer);
    }

    focusPlot(plot) {
        if (!plot) return;
        
        // Remove previous focus
        document.querySelectorAll('.vdl-keyboard-focus').forEach(el => {
            el.classList.remove('vdl-keyboard-focus');
        });
        
        // Add focus to current plot
        plot.classList.add('vdl-keyboard-focus');
        plot.focus();
        
        // Announce to screen reader
        this.announce(`Plot ${plot.dataset.plotName || plot.dataset.plotId} selected`);
    }

    announce(message) {
        if (this.announcer) {
            this.announcer.textContent = message;
            // Clear after announcement
            setTimeout(() => {
                this.announcer.textContent = '';
            }, 1000);
        }
    }

    setupPerformanceMonitoring() {
        // Monitor interaction performance
        if ('PerformanceObserver' in window) {
            const observer = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (entry.duration > 100) {
                        console.warn('Slow interaction detected:', entry.name, entry.duration);
                    }
                }
            });
            
            observer.observe({ entryTypes: ['measure'] });
        }
    }

    playSelectionSound() {
        // Optional: Play subtle sound on selection
        if (this.soundEnabled && 'Audio' in window) {
            const audio = new Audio('data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdJivrJBhNjVgodDbq2EcBj+a2/LDciUFLIHO8tiJNwgZaLvt559NEAxQp+PwtmMcBjiR1/LMeSwFJHfH8N2QQAoUXrTp66hVFApGn+DyvmwhBCl+zPDTgjMGHmS/7+OZURE');
            audio.volume = 0.1;
            audio.play().catch(() => {}); // Ignore if autoplay is blocked
        }
    }

    isTouchDevice() {
        return 'ontouchstart' in window || navigator.maxTouchPoints > 0;
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.luxuryMapInteractions = new LuxuryMapInteractions();
    });
} else {
    window.luxuryMapInteractions = new LuxuryMapInteractions();
}

// Add CSS animations dynamically
const style = document.createElement('style');
style.textContent = `
    @keyframes rippleExpand {
        to {
            transform: translate(-50%, -50%) scale(4);
            opacity: 0;
        }
    }
    
    @keyframes fadeOut {
        to {
            opacity: 0;
            transform: scale(1.5);
        }
    }
    
    @keyframes contextMenuAppear {
        from {
            opacity: 0;
            transform: scale(0.9);
        }
        to {
            opacity: 1;
            transform: scale(1);
        }
    }
    
    .vdl-arrival-highlight {
        animation: highlightPulse 2s ease-out;
    }
    
    @keyframes highlightPulse {
        0%, 100% { background-color: transparent; }
        50% { background-color: rgba(212, 175, 55, 0.1); }
    }
    
    .vdl-updating {
        animation: sidebarGlow 0.3s ease-out;
    }
    
    @keyframes sidebarGlow {
        0%, 100% { box-shadow: 0 10px 40px rgba(114, 47, 55, 0.08); }
        50% { box-shadow: 0 10px 40px rgba(212, 175, 55, 0.2); }
    }
    
    .vdl-counter-pulse {
        animation: counterPulse 0.4s ease-out;
    }
    
    @keyframes counterPulse {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.2); color: #D4AF37; }
    }
    
    .vdl-keyboard-focus {
        outline: 3px solid #D4AF37 !important;
        outline-offset: 4px;
    }
    
    .vdl-premium-loader {
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        z-index: 9999;
        background: rgba(255, 255, 255, 0.95);
        padding: 40px;
        border-radius: 20px;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
        text-align: center;
    }
    
    .vdl-premium-loader.fade-out {
        animation: loaderFadeOut 0.3s ease-out forwards;
    }
    
    @keyframes loaderFadeOut {
        to {
            opacity: 0;
            transform: translate(-50%, -50%) scale(0.9);
        }
    }
    
    .vdl-context-menu {
        font-family: 'Source Sans Pro', sans-serif;
    }
    
    .vdl-context-item {
        padding: 12px 16px;
        cursor: pointer;
        border-radius: 8px;
        transition: background 0.2s;
    }
    
    .vdl-context-item:hover {
        background: #F5F1E8;
    }
`;
document.head.appendChild(style);