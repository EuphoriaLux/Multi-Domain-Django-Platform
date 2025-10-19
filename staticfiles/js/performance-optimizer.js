/**
 * VinsDelux Performance Optimization Module
 * Implements luxury-grade performance optimizations including:
 * - Lazy loading with intersection observer
 * - Resource hints and preloading
 * - Image optimization with WebP support
 * - Code splitting and dynamic imports
 * - Performance monitoring
 */

class VinsDeluxPerformanceOptimizer {
    constructor() {
        this.intersectionObserver = null;
        this.performanceMetrics = {};
        this.lazyLoadElements = new Set();
        this.webpSupported = this.checkWebPSupport();
        this.isReducedMotion = this.checkReducedMotion();
        
        this.init();
    }
    
    init() {
        // Initialize performance monitoring
        this.initPerformanceMonitoring();
        
        // Setup intersection observer for lazy loading
        this.setupIntersectionObserver();
        
        // Optimize images
        this.optimizeImages();
        
        // Setup resource hints
        this.setupResourceHints();
        
        // Initialize code splitting
        this.initCodeSplitting();
        
        // Setup error handling
        this.setupErrorHandling();
        
        console.log('🍷 VinsDelux Performance Optimizer initialized');
    }
    
    /**
     * Performance Monitoring
     */
    initPerformanceMonitoring() {
        // Core Web Vitals monitoring
        this.measureCoreWebVitals();
        
        // Custom performance metrics
        this.startTime = performance.now();
        
        // Navigation timing
        window.addEventListener('load', () => {
            this.measurePageLoad();
        });
        
        // User interaction metrics
        this.trackUserInteractions();
    }
    
    measureCoreWebVitals() {
        // Largest Contentful Paint (LCP)
        if ('PerformanceObserver' in window) {
            try {
                const lcpObserver = new PerformanceObserver((list) => {
                    const entries = list.getEntries();
                    const lastEntry = entries[entries.length - 1];
                    this.performanceMetrics.lcp = lastEntry.startTime;
                    
                    // Report if LCP is poor (> 2.5s)
                    if (lastEntry.startTime > 2500) {
                        console.warn(`🐌 Poor LCP: ${lastEntry.startTime.toFixed(0)}ms`);
                    }
                });
                lcpObserver.observe({entryTypes: ['largest-contentful-paint']});
            } catch (e) {
                console.warn('LCP monitoring not supported:', e);
            }
            
            // First Input Delay (FID)
            try {
                const fidObserver = new PerformanceObserver((list) => {
                    const entries = list.getEntries();
                    entries.forEach((entry) => {
                        this.performanceMetrics.fid = entry.processingStart - entry.startTime;
                        
                        // Report if FID is poor (> 100ms)
                        if (entry.processingStart - entry.startTime > 100) {
                            console.warn(`🐌 Poor FID: ${(entry.processingStart - entry.startTime).toFixed(0)}ms`);
                        }
                    });
                });
                fidObserver.observe({entryTypes: ['first-input']});
            } catch (e) {
                console.warn('FID monitoring not supported:', e);
            }
            
            // Cumulative Layout Shift (CLS)
            try {
                let clsValue = 0;
                const clsObserver = new PerformanceObserver((list) => {
                    const entries = list.getEntries();
                    entries.forEach((entry) => {
                        if (!entry.hadRecentInput) {
                            clsValue += entry.value;
                        }
                    });
                    this.performanceMetrics.cls = clsValue;
                    
                    // Report if CLS is poor (> 0.1)
                    if (clsValue > 0.1) {
                        console.warn(`🐌 Poor CLS: ${clsValue.toFixed(3)}`);
                    }
                });
                clsObserver.observe({entryTypes: ['layout-shift']});
            } catch (e) {
                console.warn('CLS monitoring not supported:', e);
            }
        }
    }
    
    measurePageLoad() {
        if ('performance' in window && 'getEntriesByType' in performance) {
            const navigationTiming = performance.getEntriesByType('navigation')[0];
            
            this.performanceMetrics.ttfb = navigationTiming.responseStart - navigationTiming.requestStart;
            this.performanceMetrics.domContentLoaded = navigationTiming.domContentLoadedEventEnd - navigationTiming.navigationStart;
            this.performanceMetrics.loadComplete = navigationTiming.loadEventEnd - navigationTiming.navigationStart;
            
            console.log('📊 Performance Metrics:', {
                'Time to First Byte': `${this.performanceMetrics.ttfb.toFixed(0)}ms`,
                'DOM Content Loaded': `${this.performanceMetrics.domContentLoaded.toFixed(0)}ms`,
                'Load Complete': `${this.performanceMetrics.loadComplete.toFixed(0)}ms`
            });
            
            // Send to analytics if available
            this.sendPerformanceMetrics();
        }
    }
    
    trackUserInteractions() {
        let interactionCount = 0;
        const trackInteraction = (event) => {
            interactionCount++;
            this.performanceMetrics.interactions = interactionCount;
        };
        
        ['click', 'keydown', 'scroll'].forEach(eventType => {
            document.addEventListener(eventType, trackInteraction, { passive: true });
        });
    }
    
    /**
     * Lazy Loading Implementation
     */
    setupIntersectionObserver() {
        if ('IntersectionObserver' in window) {
            this.intersectionObserver = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        this.loadElement(entry.target);
                        this.intersectionObserver.unobserve(entry.target);
                    }
                });
            }, {
                rootMargin: '50px 0px',
                threshold: 0.1
            });
            
            // Observe all lazy-loadable elements
            this.observeLazyElements();
        } else {
            // Fallback for browsers without IntersectionObserver
            this.loadAllElements();
        }
    }
    
    observeLazyElements() {
        // Images with data-src
        document.querySelectorAll('img[data-src], picture[data-src]').forEach(img => {
            this.intersectionObserver.observe(img);
            this.lazyLoadElements.add(img);
        });
        
        // Background images with data-bg
        document.querySelectorAll('[data-bg]').forEach(el => {
            this.intersectionObserver.observe(el);
            this.lazyLoadElements.add(el);
        });
        
        // Iframes
        document.querySelectorAll('iframe[data-src]').forEach(iframe => {
            this.intersectionObserver.observe(iframe);
            this.lazyLoadElements.add(iframe);
        });
        
        // Map tiles and complex components
        document.querySelectorAll('[data-lazy-component]').forEach(component => {
            this.intersectionObserver.observe(component);
            this.lazyLoadElements.add(component);
        });
    }
    
    loadElement(element) {
        const elementType = element.tagName.toLowerCase();
        
        switch (elementType) {
            case 'img':
                this.loadImage(element);
                break;
            case 'iframe':
                this.loadIframe(element);
                break;
            default:
                this.loadGenericElement(element);
        }
    }
    
    loadImage(img) {
        const src = img.dataset.src;
        const srcset = img.dataset.srcset;
        
        if (src || srcset) {
            // Create a new image to preload
            const imageLoader = new Image();
            
            imageLoader.onload = () => {
                // Apply progressive loading effect
                this.applyImageTransition(img);
                
                if (srcset) img.srcset = srcset;
                if (src) img.src = src;
                
                img.classList.add('loaded');
                img.classList.remove('loading');
            };
            
            imageLoader.onerror = () => {
                img.classList.add('error');
                img.classList.remove('loading');
                console.warn('Failed to load image:', src);
            };
            
            // Start loading
            img.classList.add('loading');
            if (srcset) imageLoader.srcset = srcset;
            if (src) imageLoader.src = src;
        }\n        \n        // Handle background images\n        if (img.dataset.bg) {\n            img.style.backgroundImage = `url(${img.dataset.bg})`;\n            img.classList.add('loaded');\n        }\n    }\n    \n    loadIframe(iframe) {\n        const src = iframe.dataset.src;\n        if (src) {\n            iframe.src = src;\n            iframe.classList.add('loaded');\n        }\n    }\n    \n    loadGenericElement(element) {\n        // Handle data-bg background images\n        if (element.dataset.bg) {\n            element.style.backgroundImage = `url(${element.dataset.bg})`;\n            element.classList.add('bg-loaded');\n        }\n        \n        // Handle lazy components\n        if (element.dataset.lazyComponent) {\n            this.loadLazyComponent(element);\n        }\n    }\n    \n    async loadLazyComponent(element) {\n        const componentType = element.dataset.lazyComponent;\n        \n        try {\n            switch (componentType) {\n                case 'map':\n                    await this.loadMapComponent(element);\n                    break;\n                case 'chart':\n                    await this.loadChartComponent(element);\n                    break;\n                case 'video':\n                    await this.loadVideoComponent(element);\n                    break;\n                default:\n                    console.warn('Unknown lazy component type:', componentType);\n            }\n        } catch (error) {\n            console.error('Failed to load lazy component:', componentType, error);\n        }\n    }\n    \n    applyImageTransition(img) {\n        if (!this.isReducedMotion) {\n            img.style.opacity = '0';\n            img.style.transition = 'opacity 0.3s ease-in-out';\n            \n            // Fade in\n            requestAnimationFrame(() => {\n                img.style.opacity = '1';\n            });\n        }\n    }\n    \n    loadAllElements() {\n        // Fallback: load all elements immediately\n        this.lazyLoadElements.forEach(element => {\n            this.loadElement(element);\n        });\n    }\n    \n    /**\n     * Image Optimization\n     */\n    optimizeImages() {\n        // Replace images with WebP versions if supported\n        if (this.webpSupported) {\n            this.replaceWithWebP();\n        }\n        \n        // Add responsive image loading\n        this.setupResponsiveImages();\n        \n        // Preload critical images\n        this.preloadCriticalImages();\n    }\n    \n    checkWebPSupport() {\n        return new Promise((resolve) => {\n            const webP = new Image();\n            webP.onload = webP.onerror = () => {\n                resolve(webP.height === 2);\n            };\n            webP.src = 'data:image/webp;base64,UklGRjoAAABXRUJQVlA4IC4AAACyAgCdASoCAAIALmk0mk0iIiIiIgBoSygABc6WWgAA/veff/0PP8bA//LwYAAA';\n        }).then(supported => {\n            this.webpSupported = supported;\n            return supported;\n        }).catch(() => false);\n    }\n    \n    replaceWithWebP() {\n        document.querySelectorAll('img[src], img[data-src]').forEach(img => {\n            const src = img.src || img.dataset.src;\n            if (src && (src.includes('.jpg') || src.includes('.jpeg') || src.includes('.png'))) {\n                const webpSrc = src.replace(/\\.(jpg|jpeg|png)$/i, '.webp');\n                \n                // Check if WebP version exists\n                this.checkImageExists(webpSrc).then(exists => {\n                    if (exists) {\n                        if (img.dataset.src) {\n                            img.dataset.src = webpSrc;\n                        } else {\n                            img.src = webpSrc;\n                        }\n                    }\n                });\n            }\n        });\n    }\n    \n    checkImageExists(src) {\n        return new Promise((resolve) => {\n            const img = new Image();\n            img.onload = () => resolve(true);\n            img.onerror = () => resolve(false);\n            img.src = src;\n        });\n    }\n    \n    setupResponsiveImages() {\n        // Add srcset for responsive images if not already present\n        document.querySelectorAll('img:not([srcset])').forEach(img => {\n            const src = img.src || img.dataset.src;\n            if (src && !src.includes('data:')) {\n                // Generate responsive srcset\n                const baseSrc = src.replace(/\\.[^.]+$/, '');\n                const ext = src.split('.').pop();\n                \n                const srcset = [\n                    `${baseSrc}-400w.${ext} 400w`,\n                    `${baseSrc}-800w.${ext} 800w`,\n                    `${baseSrc}-1200w.${ext} 1200w`,\n                    `${src} 1600w`\n                ].join(', ');\n                \n                if (img.dataset.src) {\n                    img.dataset.srcset = srcset;\n                } else {\n                    img.srcset = srcset;\n                }\n                \n                img.sizes = '(max-width: 400px) 400px, (max-width: 800px) 800px, (max-width: 1200px) 1200px, 1600px';\n            }\n        });\n    }\n    \n    preloadCriticalImages() {\n        // Preload above-the-fold images\n        const criticalImages = document.querySelectorAll('.hero img, .above-fold img');\n        \n        criticalImages.forEach(img => {\n            const src = img.src || img.dataset.src;\n            if (src) {\n                const link = document.createElement('link');\n                link.rel = 'preload';\n                link.as = 'image';\n                link.href = src;\n                document.head.appendChild(link);\n            }\n        });\n    }\n    \n    /**\n     * Resource Hints\n     */\n    setupResourceHints() {\n        // DNS prefetch for external domains\n        const externalDomains = [\n            'fonts.googleapis.com',\n            'fonts.gstatic.com',\n            'cdn.jsdelivr.net',\n            'unpkg.com',\n            'api.mapbox.com'\n        ];\n        \n        externalDomains.forEach(domain => {\n            this.addResourceHint('dns-prefetch', `//${domain}`);\n        });\n        \n        // Preconnect to critical external resources\n        this.addResourceHint('preconnect', 'https://fonts.googleapis.com');\n        this.addResourceHint('preconnect', 'https://fonts.gstatic.com', 'crossorigin');\n        \n        // Prefetch likely next pages\n        this.prefetchLikelyPages();\n    }\n    \n    addResourceHint(rel, href, crossorigin = null) {\n        const link = document.createElement('link');\n        link.rel = rel;\n        link.href = href;\n        if (crossorigin) link.crossOrigin = crossorigin;\n        document.head.appendChild(link);\n    }\n    \n    prefetchLikelyPages() {\n        // Prefetch adoption plan pages on plot selection page\n        if (window.location.pathname.includes('plot-selection')) {\n            const adoptionPlanLinks = document.querySelectorAll('a[href*=\"adoption-plan\"]');\n            adoptionPlanLinks.forEach(link => {\n                this.addResourceHint('prefetch', link.href);\n            });\n        }\n    }\n    \n    /**\n     * Code Splitting and Dynamic Imports\n     */\n    initCodeSplitting() {\n        // Load non-critical JavaScript modules dynamically\n        this.loadModule('analytics', () => this.initAnalytics());\n        this.loadModule('maps', () => this.initMaps());\n        this.loadModule('animations', () => this.initAnimations());\n    }\n    \n    async loadModule(moduleName, callback) {\n        try {\n            switch (moduleName) {\n                case 'analytics':\n                    // Load analytics only if user consents\n                    if (this.hasAnalyticsConsent()) {\n                        const { default: Analytics } = await import('./modules/analytics.js');\n                        callback(Analytics);\n                    }\n                    break;\n                    \n                case 'maps':\n                    // Load maps only when needed\n                    if (document.querySelector('#vineyard-map, .map-container')) {\n                        const { default: Maps } = await import('./modules/maps.js');\n                        callback(Maps);\n                    }\n                    break;\n                    \n                case 'animations':\n                    // Load animations only if not reduced motion\n                    if (!this.isReducedMotion) {\n                        const { default: Animations } = await import('./modules/animations.js');\n                        callback(Animations);\n                    }\n                    break;\n            }\n        } catch (error) {\n            console.warn(`Failed to load ${moduleName} module:`, error);\n        }\n    }\n    \n    /**\n     * Error Handling\n     */\n    setupErrorHandling() {\n        // Global error handler\n        window.addEventListener('error', (event) => {\n            this.handleError('JavaScript Error', event.error, {\n                filename: event.filename,\n                lineno: event.lineno,\n                colno: event.colno\n            });\n        });\n        \n        // Unhandled promise rejections\n        window.addEventListener('unhandledrejection', (event) => {\n            this.handleError('Unhandled Promise Rejection', event.reason);\n        });\n        \n        // Network errors\n        this.setupNetworkErrorHandling();\n    }\n    \n    handleError(type, error, details = {}) {\n        console.error(`🚨 ${type}:`, error, details);\n        \n        // Send to monitoring service (if available)\n        if (window.errorTracking) {\n            window.errorTracking.captureException(error, {\n                tags: { type },\n                extra: details\n            });\n        }\n        \n        // Show user-friendly error message for critical errors\n        if (type === 'Critical Error') {\n            this.showErrorMessage('Something went wrong. Please refresh the page.');\n        }\n    }\n    \n    setupNetworkErrorHandling() {\n        // Monitor failed API requests\n        const originalFetch = window.fetch;\n        window.fetch = async (...args) => {\n            try {\n                const response = await originalFetch(...args);\n                if (!response.ok) {\n                    this.handleNetworkError(response.status, args[0]);\n                }\n                return response;\n            } catch (error) {\n                this.handleNetworkError(0, args[0], error);\n                throw error;\n            }\n        };\n    }\n    \n    handleNetworkError(status, url, error = null) {\n        console.warn(`🌐 Network Error ${status}:`, url, error);\n        \n        // Show appropriate error message\n        if (status === 0 || status >= 500) {\n            this.showErrorMessage('Network connection error. Please check your internet connection.');\n        } else if (status === 404) {\n            this.showErrorMessage('The requested resource was not found.');\n        } else if (status >= 400) {\n            this.showErrorMessage('There was a problem with your request.');\n        }\n    }\n    \n    showErrorMessage(message, type = 'error') {\n        // Create elegant error toast\n        const errorToast = document.createElement('div');\n        errorToast.className = `vdl-error-toast vdl-error-${type}`;\n        errorToast.innerHTML = `\n            <div class=\"vdl-error-content\">\n                <i class=\"fas fa-wine-glass-alt vdl-error-icon\"></i>\n                <span class=\"vdl-error-message\">${message}</span>\n                <button class=\"vdl-error-close\" onclick=\"this.parentElement.parentElement.remove()\">\n                    <i class=\"fas fa-times\"></i>\n                </button>\n            </div>\n        `;\n        \n        document.body.appendChild(errorToast);\n        \n        // Auto-remove after 5 seconds\n        setTimeout(() => {\n            if (errorToast.parentElement) {\n                errorToast.remove();\n            }\n        }, 5000);\n    }\n    \n    /**\n     * Utility Methods\n     */\n    checkReducedMotion() {\n        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;\n    }\n    \n    hasAnalyticsConsent() {\n        // Check for analytics consent (GDPR compliance)\n        return localStorage.getItem('analytics-consent') === 'true';\n    }\n    \n    sendPerformanceMetrics() {\n        // Send performance metrics to analytics\n        if (window.gtag) {\n            window.gtag('event', 'performance_metrics', {\n                'time_to_first_byte': Math.round(this.performanceMetrics.ttfb || 0),\n                'dom_content_loaded': Math.round(this.performanceMetrics.domContentLoaded || 0),\n                'load_complete': Math.round(this.performanceMetrics.loadComplete || 0),\n                'largest_contentful_paint': Math.round(this.performanceMetrics.lcp || 0),\n                'first_input_delay': Math.round(this.performanceMetrics.fid || 0),\n                'cumulative_layout_shift': Math.round((this.performanceMetrics.cls || 0) * 1000) / 1000\n            });\n        }\n    }\n    \n    /**\n     * Map Component Lazy Loading\n     */\n    async loadMapComponent(element) {\n        // Load Leaflet only when map is needed\n        if (!window.L) {\n            await this.loadScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js');\n        }\n        \n        // Initialize map\n        const mapId = element.id || 'vineyard-map';\n        const map = L.map(mapId).setView([46.1591, 6.1444], 13);\n        \n        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {\n            attribution: '© OpenStreetMap contributors'\n        }).addTo(map);\n        \n        element.classList.add('loaded');\n    }\n    \n    async loadScript(src) {\n        return new Promise((resolve, reject) => {\n            const script = document.createElement('script');\n            script.src = src;\n            script.onload = resolve;\n            script.onerror = reject;\n            document.head.appendChild(script);\n        });\n    }\n    \n    /**\n     * Public API\n     */\n    getPerformanceMetrics() {\n        return { ...this.performanceMetrics };\n    }\n    \n    refreshLazyElements() {\n        this.observeLazyElements();\n    }\n    \n    preloadImage(src) {\n        return new Promise((resolve, reject) => {\n            const img = new Image();\n            img.onload = resolve;\n            img.onerror = reject;\n            img.src = src;\n        });\n    }\n}\n\n// Initialize when DOM is ready\nif (document.readyState === 'loading') {\n    document.addEventListener('DOMContentLoaded', () => {\n        window.vinsDeluxOptimizer = new VinsDeluxPerformanceOptimizer();\n    });\n} else {\n    window.vinsDeluxOptimizer = new VinsDeluxPerformanceOptimizer();\n}\n\n// Export for module systems\nif (typeof module !== 'undefined' && module.exports) {\n    module.exports = VinsDeluxPerformanceOptimizer;\n}"