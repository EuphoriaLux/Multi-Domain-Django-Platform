/**
 * Animations Class - GSAP-powered smooth animations for VinsDelux
 * Handles page transitions, micro-interactions, and scroll-based effects
 */

class VineyardAnimations {
    constructor(options = {}) {
        this.isGSAPLoaded = typeof gsap !== 'undefined';
        this.options = {
            reducedMotion: this.prefersReducedMotion(),
            duration: options.duration || 0.6,
            ease: options.ease || 'power2.out',
            stagger: options.stagger || 0.1,
            ...options
        };
        
        this.observers = new Map();
        this.timelines = new Map();
        this.scrollTriggers = [];
        
        this.init();
    }
    
    init() {
        if (!this.isGSAPLoaded) {
            console.warn('GSAP not loaded. Using CSS fallbacks for animations.');
        }
        
        this.setupScrollObserver();
        this.initializePageAnimations();
        this.setupParallaxEffects();
        this.setupMicroInteractions();
        
        console.log('VineyardAnimations initialized');
    }
    
    prefersReducedMotion() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    
    setupScrollObserver() {
        if ('IntersectionObserver' in window) {
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        this.animateElementIn(entry.target);
                    }
                });
            }, {
                threshold: 0.1,
                rootMargin: '50px'
            });
            
            this.observers.set('scroll', observer);
            
            // Observe all animatable elements
            this.observeElements();
        }
    }
    
    observeElements() {
        const animatableSelectors = [
            '.plot-card',
            '.producer-info',
            '.wine-feature',
            '.adoption-plan',
            '.hero-content',
            '.stats-item',
            '.testimonial',
            '.fade-in-element'
        ];
        
        animatableSelectors.forEach(selector => {
            document.querySelectorAll(selector).forEach(el => {
                if (!el.dataset.animated) {
                    this.observers.get('scroll')?.observe(el);
                }
            });
        });
    }
    
    animateElementIn(element) {
        if (element.dataset.animated) return;
        element.dataset.animated = 'true';
        
        const animationType = element.dataset.animation || 'fadeInUp';
        
        if (this.options.reducedMotion) {
            element.style.opacity = '1';
            element.style.transform = 'none';
            return;
        }
        
        switch (animationType) {
            case 'fadeInUp':
                this.fadeInUp(element);
                break;
            case 'fadeInLeft':
                this.fadeInLeft(element);
                break;
            case 'fadeInRight':
                this.fadeInRight(element);
                break;
            case 'scaleIn':
                this.scaleIn(element);
                break;
            case 'slideInUp':
                this.slideInUp(element);
                break;
            default:
                this.fadeInUp(element);
        }
    }
    
    fadeInUp(element, options = {}) {
        const config = { ...this.options, ...options };
        
        if (this.isGSAPLoaded) {
            gsap.fromTo(element, 
                {
                    opacity: 0,
                    y: 50,
                    scale: 0.95
                },
                {
                    opacity: 1,
                    y: 0,
                    scale: 1,
                    duration: config.duration,
                    ease: config.ease,
                    delay: config.delay || 0
                }
            );
        } else {
            // CSS fallback
            element.style.transition = `all ${config.duration}s ${config.ease}`;
            element.style.opacity = '1';
            element.style.transform = 'translateY(0) scale(1)';
        }
    }
    
    fadeInLeft(element, options = {}) {
        const config = { ...this.options, ...options };
        
        if (this.isGSAPLoaded) {
            gsap.fromTo(element,
                { opacity: 0, x: -100 },
                {
                    opacity: 1,
                    x: 0,
                    duration: config.duration,
                    ease: config.ease
                }
            );
        } else {
            element.style.transition = `all ${config.duration}s`;
            element.style.opacity = '1';
            element.style.transform = 'translateX(0)';
        }
    }
    
    fadeInRight(element, options = {}) {
        const config = { ...this.options, ...options };
        
        if (this.isGSAPLoaded) {
            gsap.fromTo(element,
                { opacity: 0, x: 100 },
                {
                    opacity: 1,
                    x: 0,
                    duration: config.duration,
                    ease: config.ease
                }
            );
        } else {
            element.style.transition = `all ${config.duration}s`;
            element.style.opacity = '1';
            element.style.transform = 'translateX(0)';
        }
    }
    
    scaleIn(element, options = {}) {
        const config = { ...this.options, ...options };
        
        if (this.isGSAPLoaded) {
            gsap.fromTo(element,
                { opacity: 0, scale: 0.3, rotation: -10 },
                {
                    opacity: 1,
                    scale: 1,
                    rotation: 0,
                    duration: config.duration,
                    ease: 'back.out(1.7)'
                }
            );
        } else {
            element.style.transition = `all ${config.duration}s cubic-bezier(0.175, 0.885, 0.32, 1.275)`;
            element.style.opacity = '1';
            element.style.transform = 'scale(1) rotate(0deg)';
        }
    }
    
    slideInUp(element, options = {}) {
        const config = { ...this.options, ...options };
        
        if (this.isGSAPLoaded) {
            gsap.fromTo(element,
                { y: '100%', opacity: 0 },
                {
                    y: '0%',
                    opacity: 1,
                    duration: config.duration,
                    ease: config.ease
                }
            );
        } else {
            element.style.transition = `all ${config.duration}s`;
            element.style.transform = 'translateY(0)';
            element.style.opacity = '1';
        }
    }
    
    initializePageAnimations() {
        // Hero section animation
        this.animateHeroSection();
        
        // Navigation entrance
        this.animateNavigation();
        
        // Plot cards stagger animation
        this.animatePlotCards();
        
        // Loading states
        this.setupLoadingAnimations();
    }
    
    animateHeroSection() {
        const hero = document.querySelector('.hero-section, .page-title');
        if (!hero || this.options.reducedMotion) return;
        
        if (this.isGSAPLoaded) {
            const tl = gsap.timeline();
            
            tl.fromTo(hero,
                { opacity: 0, scale: 0.8, y: 50 },
                { opacity: 1, scale: 1, y: 0, duration: 1.2, ease: 'power3.out' }
            );
            
            // Animate subtitle if present
            const subtitle = document.querySelector('.page-subtitle');
            if (subtitle) {
                tl.fromTo(subtitle,
                    { opacity: 0, y: 30 },
                    { opacity: 1, y: 0, duration: 0.8, ease: 'power2.out' },
                    '-=0.6'
                );
            }
            
            this.timelines.set('hero', tl);
        } else {
            // CSS fallback
            hero.style.animation = 'fadeInScale 1.2s ease-out forwards';
        }
    }
    
    animateNavigation() {
        const nav = document.querySelector('.navbar, .navigation');
        if (!nav || this.options.reducedMotion) return;
        
        if (this.isGSAPLoaded) {
            gsap.fromTo(nav,
                { y: -100, opacity: 0 },
                { y: 0, opacity: 1, duration: 0.8, ease: 'power2.out', delay: 0.3 }
            );
        } else {
            nav.style.animation = 'slideDown 0.8s ease-out 0.3s both';
        }
    }
    
    animatePlotCards() {
        const cards = document.querySelectorAll('.plot-card, .plot-marker, .canton-group');
        if (cards.length === 0 || this.options.reducedMotion) return;
        
        if (this.isGSAPLoaded) {
            gsap.fromTo(cards,
                { 
                    opacity: 0, 
                    y: 60, 
                    scale: 0.9,
                    rotation: 5
                },
                {
                    opacity: 1,
                    y: 0,
                    scale: 1,
                    rotation: 0,
                    duration: 0.6,
                    ease: 'power2.out',
                    stagger: this.options.stagger,
                    delay: 0.5
                }
            );
        } else {
            // CSS fallback with stagger
            cards.forEach((card, index) => {
                card.style.animation = `fadeInUpStagger 0.6s ease-out ${0.5 + (index * 0.1)}s both`;
            });
        }
    }
    
    setupParallaxEffects() {
        if (this.options.reducedMotion) return;
        
        const parallaxElements = document.querySelectorAll('.parallax-bg, .hero-background');
        
        if (this.isGSAPLoaded && parallaxElements.length > 0) {
            parallaxElements.forEach(element => {
                gsap.to(element, {
                    yPercent: -50,
                    ease: "none",
                    scrollTrigger: {
                        trigger: element,
                        start: "top bottom",
                        end: "bottom top",
                        scrub: true
                    }
                });
            });
        } else if (parallaxElements.length > 0) {
            // Simple parallax with scroll event
            this.setupSimpleParallax(parallaxElements);
        }
    }
    
    setupSimpleParallax(elements) {
        let ticking = false;
        
        const updateParallax = () => {
            const scrolled = window.pageYOffset;
            
            elements.forEach(element => {
                const rate = scrolled * -0.5;
                element.style.transform = `translateY(${rate}px)`;
            });
            
            ticking = false;
        };
        
        const requestUpdate = () => {
            if (!ticking) {
                requestAnimationFrame(updateParallax);
                ticking = true;
            }
        };
        
        window.addEventListener('scroll', requestUpdate);
    }
    
    setupMicroInteractions() {
        this.setupHoverAnimations();
        this.setupClickAnimations();
        this.setupFormAnimations();
    }
    
    setupHoverAnimations() {
        // Plot markers hover effect
        document.addEventListener('mouseenter', (e) => {
            if (e.target.matches('.plot-marker, .canton-group path')) {
                this.animateHoverIn(e.target);
            }
        }, true);
        
        document.addEventListener('mouseleave', (e) => {
            if (e.target.matches('.plot-marker, .canton-group path')) {
                this.animateHoverOut(e.target);
            }
        }, true);
        
        // Button hover effects
        document.addEventListener('mouseenter', (e) => {
            if (e.target.matches('.btn, .select-plot-btn, .control-btn')) {
                this.animateButtonHover(e.target, true);
            }
        }, true);
        
        document.addEventListener('mouseleave', (e) => {
            if (e.target.matches('.btn, .select-plot-btn, .control-btn')) {
                this.animateButtonHover(e.target, false);
            }
        }, true);
    }
    
    animateHoverIn(element) {
        if (this.options.reducedMotion) return;
        
        if (this.isGSAPLoaded) {
            gsap.to(element, {
                scale: 1.1,
                rotation: 2,
                duration: 0.3,
                ease: 'power2.out'
            });
        } else {
            element.style.transition = 'all 0.3s ease';
            element.style.transform = 'scale(1.1) rotate(2deg)';
        }
    }
    
    animateHoverOut(element) {
        if (this.options.reducedMotion) return;
        
        if (this.isGSAPLoaded) {
            gsap.to(element, {
                scale: 1,
                rotation: 0,
                duration: 0.3,
                ease: 'power2.out'
            });
        } else {
            element.style.transform = 'scale(1) rotate(0deg)';
        }
    }
    
    animateButtonHover(button, isEntering) {
        if (this.options.reducedMotion) return;
        
        if (this.isGSAPLoaded) {
            gsap.to(button, {
                y: isEntering ? -3 : 0,
                boxShadow: isEntering 
                    ? '0 8px 25px rgba(114, 47, 55, 0.4)' 
                    : '0 4px 15px rgba(114, 47, 55, 0.3)',
                duration: 0.3,
                ease: 'power2.out'
            });
        } else {
            button.style.transition = 'all 0.3s ease';
            button.style.transform = isEntering ? 'translateY(-3px)' : 'translateY(0)';
            button.style.boxShadow = isEntering 
                ? '0 8px 25px rgba(114, 47, 55, 0.4)' 
                : '0 4px 15px rgba(114, 47, 55, 0.3)';
        }
    }
    
    setupClickAnimations() {
        document.addEventListener('click', (e) => {
            if (e.target.matches('.btn, .plot-marker, .select-plot-btn')) {
                this.animateClick(e.target);
            }
        });
    }
    
    animateClick(element) {
        if (this.options.reducedMotion) return;
        
        if (this.isGSAPLoaded) {
            gsap.fromTo(element,
                { scale: 1 },
                { 
                    scale: 0.95, 
                    duration: 0.1, 
                    ease: 'power2.inOut',
                    yoyo: true,
                    repeat: 1
                }
            );
        } else {
            element.style.transition = 'transform 0.1s ease';
            element.style.transform = 'scale(0.95)';
            setTimeout(() => {
                element.style.transform = 'scale(1)';
            }, 100);
        }
    }
    
    setupFormAnimations() {
        // Animate form inputs on focus
        document.addEventListener('focusin', (e) => {
            if (e.target.matches('input, textarea, select')) {
                this.animateInputFocus(e.target, true);
            }
        });
        
        document.addEventListener('focusout', (e) => {
            if (e.target.matches('input, textarea, select')) {
                this.animateInputFocus(e.target, false);
            }
        });
    }
    
    animateInputFocus(input, isFocused) {
        if (this.options.reducedMotion) return;
        
        const label = input.previousElementSibling || input.parentElement.querySelector('label');
        
        if (this.isGSAPLoaded) {
            gsap.to(input, {
                scale: isFocused ? 1.02 : 1,
                boxShadow: isFocused 
                    ? '0 0 0 3px rgba(212, 175, 55, 0.3)' 
                    : '0 0 0 0px rgba(212, 175, 55, 0)',
                duration: 0.3,
                ease: 'power2.out'
            });
            
            if (label) {
                gsap.to(label, {
                    color: isFocused ? '#D4AF37' : '#666',
                    duration: 0.3
                });
            }
        } else {
            input.style.transition = 'all 0.3s ease';
            input.style.transform = isFocused ? 'scale(1.02)' : 'scale(1)';
            input.style.boxShadow = isFocused 
                ? '0 0 0 3px rgba(212, 175, 55, 0.3)' 
                : 'none';
        }
    }
    
    setupLoadingAnimations() {
        this.createWineGlassLoader();
        this.setupLoadingStates();
    }
    
    createWineGlassLoader() {
        const loaderHTML = `
            <div class="wine-glass-loader" id="wineGlassLoader">
                <div class="wine-glass">
                    <div class="wine-fill"></div>
                    <div class="wine-glass-stem"></div>
                    <div class="wine-glass-base"></div>
                </div>
                <div class="loading-text">Preparing your wine experience...</div>
            </div>
        `;
        
        // Insert loader styles if not already present
        if (!document.querySelector('#wine-loader-styles')) {
            const styles = document.createElement('style');
            styles.id = 'wine-loader-styles';
            styles.textContent = this.getLoaderStyles();
            document.head.appendChild(styles);
        }
        
        return loaderHTML;
    }
    
    showWineGlassLoader(container = document.body) {
        if (document.querySelector('#wineGlassLoader')) return;
        
        const loader = document.createElement('div');
        loader.innerHTML = this.createWineGlassLoader();
        container.appendChild(loader.firstElementChild);
        
        // Animate in
        if (this.isGSAPLoaded) {
            gsap.fromTo('#wineGlassLoader',
                { opacity: 0, scale: 0.8 },
                { opacity: 1, scale: 1, duration: 0.5, ease: 'power2.out' }
            );
        }
    }
    
    hideWineGlassLoader() {
        const loader = document.querySelector('#wineGlassLoader');
        if (!loader) return;
        
        if (this.isGSAPLoaded) {
            gsap.to(loader, {
                opacity: 0,
                scale: 0.8,
                duration: 0.5,
                ease: 'power2.in',
                onComplete: () => loader.remove()
            });
        } else {
            loader.style.transition = 'all 0.5s ease';
            loader.style.opacity = '0';
            setTimeout(() => loader.remove(), 500);
        }
    }
    
    setupLoadingStates() {
        // Show/hide loading states for async operations
        document.addEventListener('fetchStart', () => {
            this.showWineGlassLoader();
        });
        
        document.addEventListener('fetchEnd', () => {
            this.hideWineGlassLoader();
        });
    }
    
    // Smooth scroll utility
    smoothScrollTo(target, options = {}) {
        const element = typeof target === 'string' 
            ? document.querySelector(target) 
            : target;
        
        if (!element) return;
        
        const config = {
            duration: 1,
            ease: 'power2.inOut',
            offsetY: 0,
            ...options
        };
        
        if (this.isGSAPLoaded) {
            gsap.to(window, {
                duration: config.duration,
                scrollTo: {
                    y: element,
                    offsetY: config.offsetY
                },
                ease: config.ease
            });
        } else {
            element.scrollIntoView({ 
                behavior: 'smooth',
                block: 'start'
            });
        }
    }
    
    // Animation timeline management
    createTimeline(name, config = {}) {
        if (!this.isGSAPLoaded) return null;
        
        const tl = gsap.timeline(config);
        this.timelines.set(name, tl);
        return tl;
    }
    
    getTimeline(name) {
        return this.timelines.get(name);
    }
    
    // Utility method to get loader styles
    getLoaderStyles() {
        return `
            .wine-glass-loader {
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(26, 26, 62, 0.95);
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                z-index: 10000;
            }
            
            .wine-glass {
                position: relative;
                width: 60px;
                height: 100px;
                margin-bottom: 20px;
            }
            
            .wine-glass::before {
                content: '';
                position: absolute;
                top: 0;
                left: 50%;
                transform: translateX(-50%);
                width: 50px;
                height: 60px;
                border: 3px solid #D4AF37;
                border-bottom: none;
                border-radius: 0 0 25px 25px;
                background: transparent;
            }
            
            .wine-fill {
                position: absolute;
                bottom: 20px;
                left: 50%;
                transform: translateX(-50%);
                width: 44px;
                height: 0;
                background: linear-gradient(to top, #722F37, #D4AF37);
                border-radius: 0 0 22px 22px;
                animation: fillWine 2s ease-in-out infinite alternate;
            }
            
            .wine-glass-stem {
                position: absolute;
                bottom: 15px;
                left: 50%;
                transform: translateX(-50%);
                width: 3px;
                height: 25px;
                background: #D4AF37;
            }
            
            .wine-glass-base {
                position: absolute;
                bottom: 0;
                left: 50%;
                transform: translateX(-50%);
                width: 30px;
                height: 5px;
                background: #D4AF37;
                border-radius: 15px;
            }
            
            .loading-text {
                color: white;
                font-size: 18px;
                font-weight: 300;
                text-align: center;
                animation: fadeInOut 2s ease-in-out infinite;
            }
            
            @keyframes fillWine {
                0% { height: 0; }
                100% { height: 35px; }
            }
            
            @keyframes fadeInOut {
                0%, 100% { opacity: 0.7; }
                50% { opacity: 1; }
            }
            
            @keyframes fadeInScale {
                from {
                    opacity: 0;
                    transform: scale(0.8) translateY(50px);
                }
                to {
                    opacity: 1;
                    transform: scale(1) translateY(0);
                }
            }
            
            @keyframes slideDown {
                from {
                    opacity: 0;
                    transform: translateY(-100px);
                }
                to {
                    opacity: 1;
                    transform: translateY(0);
                }
            }
            
            @keyframes fadeInUpStagger {
                from {
                    opacity: 0;
                    transform: translateY(60px) scale(0.9) rotate(5deg);
                }
                to {
                    opacity: 1;
                    transform: translateY(0) scale(1) rotate(0deg);
                }
            }
        `;
    }
    
    // Cleanup method
    destroy() {
        // Clear all observers
        this.observers.forEach(observer => observer.disconnect());
        this.observers.clear();
        
        // Kill all GSAP animations
        if (this.isGSAPLoaded) {
            this.timelines.forEach(tl => tl.kill());
            gsap.killTweensOf("*");
        }
        
        // Clear scroll triggers
        this.scrollTriggers.forEach(trigger => trigger.kill());
        this.scrollTriggers = [];
        
        this.timelines.clear();
    }
}

export default VineyardAnimations;