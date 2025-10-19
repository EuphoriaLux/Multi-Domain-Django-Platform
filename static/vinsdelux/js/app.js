/**
 * VinsDelux Main Application - Luxury Wine Plot Selection Experience
 * Orchestrates all components and handles main application logic
 */

class VinsDeluxApp {
    constructor(options = {}) {
        this.config = {
            debug: options.debug || false,
            language: options.language || 'en',
            apiBaseUrl: options.apiBaseUrl || '/api',
            enableAnalytics: options.enableAnalytics !== false,
            maxPlotSelections: options.maxPlotSelections || 5,
            ...options
        };
        
        // Component instances
        this.components = {
            vineyardMap: null,
            plotSelection: null,
            animations: null,
            videoBackground: null
        };
        
        // Application state
        this.state = {
            isInitialized: false,
            currentStep: 'plot-selection',
            selectedPlots: new Set(),
            userProfile: null,
            touchDevice: this.isTouchDevice(),
            loading: false
        };
        
        // Event handlers
        this.eventHandlers = new Map();
        
        this.init();
    }
    
    async init() {
        try {
            this.log('Initializing VinsDelux application...');
            
            // Show loading state
            this.setLoadingState(true);
            
            // Initialize core components
            await this.initializeComponents();
            
            // Setup global event listeners
            this.setupEventListeners();
            
            // Initialize video background
            this.setupVideoBackground();
            
            // Handle multi-language support
            this.setupInternationalization();
            
            // Setup touch gestures for mobile
            if (this.state.touchDevice) {
                this.setupTouchGestures();
            }
            
            // Initialize API communication
            this.setupApiCommunication();
            
            // Setup analytics if enabled
            if (this.config.enableAnalytics) {
                this.setupAnalytics();
            }
            
            // Mark as initialized
            this.state.isInitialized = true;
            
            // Hide loading state
            this.setLoadingState(false);
            
            // Trigger initialization complete event
            this.dispatchEvent('app:initialized', { config: this.config });
            
            this.log('VinsDelux application initialized successfully');
            
        } catch (error) {
            this.handleInitializationError(error);
        }
    }
    
    async initializeComponents() {
        // Initialize animations first
        if (typeof VineyardAnimations !== 'undefined') {
            this.components.animations = new VineyardAnimations({
                reducedMotion: this.prefersReducedMotion(),
                debug: this.config.debug
            });
        }
        
        // Initialize plot selection system
        if (typeof PlotSelection !== 'undefined') {
            this.components.plotSelection = new PlotSelection({
                maxSelections: this.config.maxPlotSelections,
                apiEndpoint: `${this.config.apiBaseUrl}/plots/selection/`,
                onPlotAdded: (plot) => this.handlePlotAdded(plot),
                onPlotRemoved: (plot) => this.handlePlotRemoved(plot),
                onSelectionLimitReached: (limit) => this.handleSelectionLimitReached(limit),
                onCartUpdated: (summary) => this.handleCartUpdated(summary)
            });
        }
        
        // Initialize vineyard map if container exists
        const mapContainer = document.getElementById('vineyard-map');
        if (mapContainer && typeof VineyardMap !== 'undefined') {
            this.components.vineyardMap = new VineyardMap('vineyard-map', {
                enableClustering: true,
                maxZoom: 18,
                hoverColor: '#D4AF37',
                selectedColor: '#722F37'
            });
        }
        
        // Initialize enhanced plot selector if present
        if (document.getElementById('wineRegionMap')) {
            await this.initializeEnhancedPlotSelector();
        }
    }
    
    async initializeEnhancedPlotSelector() {
        // Use existing enhanced plot selector if available
        if (typeof EnhancedPlotSelector !== 'undefined') {
            this.components.enhancedSelector = new EnhancedPlotSelector();
        }
    }
    
    setupEventListeners() {
        // Window events
        window.addEventListener('resize', this.debounce(() => {
            this.handleResize();
        }, 250));
        
        window.addEventListener('orientationchange', () => {
            setTimeout(() => this.handleResize(), 300);
        });
        
        // Navigation events
        document.addEventListener('click', (e) => {
            this.handleNavigation(e);
        });
        
        // Form events
        document.addEventListener('submit', (e) => {
            this.handleFormSubmit(e);
        });
        
        // Custom component events
        document.addEventListener('vineyardMap:plotSelected', (e) => {
            this.handleMapPlotSelected(e.detail);
        });
        
        document.addEventListener('vineyardMap:plotDetailsRequested', (e) => {
            this.showPlotDetails(e.detail.plot);
        });
        
        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            this.handleKeyboardNavigation(e);
        });
        
        // Page visibility changes
        document.addEventListener('visibilitychange', () => {
            this.handleVisibilityChange();
        });
    }
    
    setupVideoBackground() {
        const videoElements = document.querySelectorAll('video[data-background="true"]');
        
        videoElements.forEach(video => {
            // Set optimal playback rate for elegance
            video.playbackRate = 0.8;
            
            // Handle video loading
            video.addEventListener('loadeddata', () => {
                video.style.opacity = '1';
                this.log('Background video loaded');
            });
            
            // Auto-play with user interaction fallback
            const playVideo = () => {
                video.play().catch(err => {
                    this.log('Video autoplay prevented:', err);
                    this.showVideoPlayButton(video);
                });
            };
            
            // Try to play immediately
            playVideo();
            
            // Add play/pause controls
            this.addVideoControls(video);
        });
    }
    
    addVideoControls(video) {
        const controlsHTML = `
            <div class="video-controls">
                <button class="video-control-btn play-pause-btn" title="Play/Pause">
                    <i class="fas fa-pause"></i>
                </button>
                <button class="video-control-btn mute-btn" title="Mute/Unmute">
                    <i class="fas fa-volume-mute"></i>
                </button>
            </div>
        `;
        
        const container = video.parentElement;
        container.insertAdjacentHTML('beforeend', controlsHTML);
        
        const controls = container.querySelector('.video-controls');
        const playPauseBtn = controls.querySelector('.play-pause-btn');
        const muteBtn = controls.querySelector('.mute-btn');
        
        // Play/Pause functionality
        playPauseBtn.addEventListener('click', () => {
            if (video.paused) {
                video.play();
                playPauseBtn.innerHTML = '<i class="fas fa-pause"></i>';
            } else {
                video.pause();
                playPauseBtn.innerHTML = '<i class="fas fa-play"></i>';
            }
        });
        
        // Mute/Unmute functionality
        muteBtn.addEventListener('click', () => {
            video.muted = !video.muted;
            muteBtn.innerHTML = video.muted 
                ? '<i class="fas fa-volume-mute"></i>' 
                : '<i class="fas fa-volume-up"></i>';
        });
    }
    
    showVideoPlayButton(video) {
        const playButton = document.createElement('button');
        playButton.className = 'video-play-overlay';
        playButton.innerHTML = '<i class="fas fa-play fa-3x"></i>';
        playButton.style.cssText = `
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: rgba(0,0,0,0.7);
            border: none;
            border-radius: 50%;
            width: 80px;
            height: 80px;
            color: white;
            cursor: pointer;
            z-index: 100;
        `;
        
        video.parentElement.appendChild(playButton);
        
        playButton.addEventListener('click', () => {
            video.play();
            playButton.remove();
        });
    }
    
    setupInternationalization() {
        // Handle language switching
        document.addEventListener('click', (e) => {
            if (e.target.matches('.language-switcher a')) {
                e.preventDefault();
                this.switchLanguage(e.target.dataset.lang);
            }
        });
        
        // Update text content based on current language
        this.updateLanguageContent();
    }
    
    setupTouchGestures() {
        if (!this.state.touchDevice) return;
        
        let touchStartX = 0;
        let touchStartY = 0;
        
        document.addEventListener('touchstart', (e) => {
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
        });
        
        document.addEventListener('touchend', (e) => {
            const touchEndX = e.changedTouches[0].clientX;
            const touchEndY = e.changedTouches[0].clientY;
            
            const deltaX = touchEndX - touchStartX;
            const deltaY = touchEndY - touchStartY;
            
            // Handle swipe gestures
            if (Math.abs(deltaX) > 50) {
                if (deltaX > 0) {
                    this.handleSwipeRight();
                } else {
                    this.handleSwipeLeft();
                }
            }
        });
        
        // Add touch-friendly styles
        document.body.classList.add('touch-device');
    }
    
    setupApiCommunication() {
        // Setup default headers and error handling
        this.apiConfig = {
            baseURL: this.config.apiBaseUrl,
            timeout: 10000,
            headers: {
                'Content-Type': 'application/json'
            }
        };
        
        // Add CSRF token if available
        const csrfToken = this.getCsrfToken();
        if (csrfToken) {
            this.apiConfig.headers['X-CSRFToken'] = csrfToken;
        }
    }
    
    setupAnalytics() {
        // Track page view
        this.trackEvent('page_view', {
            page: 'plot_selection',
            timestamp: new Date().toISOString()
        });
        
        // Track user interactions
        document.addEventListener('click', (e) => {
            if (e.target.matches('.plot-marker, .select-plot-btn')) {
                this.trackEvent('plot_interaction', {
                    action: 'click',
                    element: e.target.className
                });
            }
        });
    }
    
    // Event handlers
    handlePlotAdded(plot) {
        this.state.selectedPlots.add(plot.id);
        this.trackEvent('plot_added', { plot_id: plot.id, plot_name: plot.name });
        
        // Update map visualization if available
        if (this.components.vineyardMap) {
            // Highlight selected plot on map
            this.components.vineyardMap.addPlotToSelection(plot);
        }
    }
    
    handlePlotRemoved(plot) {
        this.state.selectedPlots.delete(plot.id);
        this.trackEvent('plot_removed', { plot_id: plot.id, plot_name: plot.name });
        
        // Update map visualization
        if (this.components.vineyardMap) {
            this.components.vineyardMap.deselectPlot(plot.id);
        }
    }
    
    handleSelectionLimitReached(limit) {
        this.showNotification(
            `Maximum ${limit} plots allowed. Please remove a plot to select another.`,
            'warning',
            5000
        );
    }
    
    handleCartUpdated(summary) {
        // Update UI elements that show cart summary
        this.dispatchEvent('cart:updated', summary);
    }
    
    handleMapPlotSelected(details) {
        if (this.components.plotSelection) {
            this.components.plotSelection.addPlot(details.plot);
        }
    }
    
    handleNavigation(e) {
        if (e.target.matches('[data-action]')) {
            e.preventDefault();
            const action = e.target.dataset.action;
            
            switch (action) {
                case 'smooth-scroll':
                    this.smoothScrollToTarget(e.target.getAttribute('href'));
                    break;
                case 'show-map':
                    this.showMapView();
                    break;
                case 'show-list':
                    this.showListView();
                    break;
                case 'open-cart':
                    this.openCartSidebar();
                    break;
                case 'proceed-checkout':
                    this.proceedToCheckout();
                    break;
            }
        }
    }
    
    handleFormSubmit(e) {
        const form = e.target;
        
        if (form.matches('.ajax-form')) {
            e.preventDefault();
            this.submitFormAjax(form);
        }
    }
    
    handleKeyboardNavigation(e) {
        switch (e.key) {
            case 'Escape':
                this.closeAllModals();
                break;
            case 'Enter':
                if (e.target.matches('.plot-marker')) {
                    this.selectPlot(e.target);
                }
                break;
        }
    }
    
    handleResize() {
        // Update component layouts
        if (this.components.vineyardMap && this.components.vineyardMap.map) {
            this.components.vineyardMap.map.invalidateSize();
        }
        
        // Update video dimensions
        this.updateVideoBackground();
        
        this.dispatchEvent('app:resize', {
            width: window.innerWidth,
            height: window.innerHeight
        });
    }
    
    handleVisibilityChange() {
        if (document.hidden) {
            // Pause videos when page is hidden
            document.querySelectorAll('video').forEach(video => {
                if (!video.paused) {
                    video.pause();
                    video.dataset.wasPlaying = 'true';
                }
            });
        } else {
            // Resume videos when page becomes visible
            document.querySelectorAll('video').forEach(video => {
                if (video.dataset.wasPlaying === 'true') {
                    video.play();
                    delete video.dataset.wasPlaying;
                }
            });
        }
    }
    
    handleSwipeRight() {
        // Handle right swipe gesture
        this.log('Swipe right detected');
    }
    
    handleSwipeLeft() {
        // Handle left swipe gesture
        this.log('Swipe left detected');
    }
    
    // Utility methods
    smoothScrollToTarget(target) {
        if (this.components.animations) {
            this.components.animations.smoothScrollTo(target);
        } else {
            document.querySelector(target)?.scrollIntoView({ behavior: 'smooth' });
        }
    }
    
    showMapView() {
        document.querySelector('.map-view')?.classList.add('active');
        document.querySelector('.list-view')?.classList.remove('active');
    }
    
    showListView() {
        document.querySelector('.list-view')?.classList.add('active');
        document.querySelector('.map-view')?.classList.remove('active');
    }
    
    openCartSidebar() {
        document.querySelector('.cart-sidebar')?.classList.add('active');
    }
    
    async proceedToCheckout() {
        if (this.components.plotSelection) {
            await this.components.plotSelection.proceedToCheckout();
        }
    }
    
    showPlotDetails(plot) {
        this.dispatchEvent('plot:showDetails', { plot });
    }
    
    closeAllModals() {
        document.querySelectorAll('.modal, .sidebar').forEach(modal => {
            modal.classList.remove('active');
        });
    }
    
    async submitFormAjax(form) {
        try {
            const formData = new FormData(form);
            const response = await this.apiRequest('POST', form.action, formData);
            
            if (response.success) {
                this.showNotification('Form submitted successfully!', 'success');
            } else {
                this.showNotification('Form submission failed. Please try again.', 'error');
            }
        } catch (error) {
            this.log('Form submission error:', error);
            this.showNotification('An error occurred. Please try again.', 'error');
        }
    }
    
    updateVideoBackground() {
        const videos = document.querySelectorAll('video[data-background="true"]');
        videos.forEach(video => {
            const container = video.parentElement;
            const containerRatio = container.offsetWidth / container.offsetHeight;
            const videoRatio = video.videoWidth / video.videoHeight;
            
            if (containerRatio > videoRatio) {
                video.style.width = '100%';
                video.style.height = 'auto';
            } else {
                video.style.width = 'auto';
                video.style.height = '100%';
            }
        });
    }
    
    switchLanguage(language) {
        // Store language preference
        localStorage.setItem('vinsdelux_language', language);
        
        // Update current language
        this.config.language = language;
        
        // Navigate to localized URL
        const currentPath = window.location.pathname;
        const pathSegments = currentPath.split('/');
        
        // Replace language segment
        if (pathSegments[1] && ['en', 'fr', 'de'].includes(pathSegments[1])) {
            pathSegments[1] = language;
        } else {
            pathSegments.splice(1, 0, language);
        }
        
        const newPath = pathSegments.join('/');
        window.location.href = newPath;
    }
    
    updateLanguageContent() {
        // Update elements with data-lang attributes
        document.querySelectorAll('[data-lang]').forEach(element => {
            const langData = JSON.parse(element.dataset.lang);
            if (langData[this.config.language]) {
                element.textContent = langData[this.config.language];
            }
        });
    }
    
    // API communication methods
    async apiRequest(method, endpoint, data = null) {
        const url = endpoint.startsWith('http') ? endpoint : `${this.config.apiBaseUrl}${endpoint}`;
        
        const options = {
            method,
            headers: { ...this.apiConfig.headers }
        };
        
        if (data) {
            if (data instanceof FormData) {
                // Let browser set Content-Type for FormData
                delete options.headers['Content-Type'];
                options.body = data;
            } else {
                options.body = JSON.stringify(data);
            }
        }
        
        try {
            this.dispatchEvent('fetchStart');
            
            const response = await fetch(url, options);
            const result = await response.json();
            
            this.dispatchEvent('fetchEnd');
            
            if (!response.ok) {
                throw new Error(`API Error: ${result.message || response.statusText}`);
            }
            
            return result;
        } catch (error) {
            this.dispatchEvent('fetchEnd');
            throw error;
        }
    }
    
    // Utility methods
    setLoadingState(loading) {
        this.state.loading = loading;
        
        if (loading) {
            if (this.components.animations) {
                this.components.animations.showWineGlassLoader();
            } else {
                document.body.classList.add('loading');
            }
        } else {
            if (this.components.animations) {
                this.components.animations.hideWineGlassLoader();
            } else {
                document.body.classList.remove('loading');
            }
        }
    }
    
    showNotification(message, type = 'info', duration = 3000) {
        if (this.components.plotSelection) {
            this.components.plotSelection.showNotification(message, type, duration);
        } else {
            console.log(`${type.toUpperCase()}: ${message}`);
        }
    }
    
    trackEvent(eventName, data = {}) {
        if (!this.config.enableAnalytics) return;
        
        // Send to analytics service
        this.log('Analytics event:', eventName, data);
        
        // You can integrate with Google Analytics, Mixpanel, etc.
        if (typeof gtag !== 'undefined') {
            gtag('event', eventName, data);
        }
    }
    
    isTouchDevice() {
        return 'ontouchstart' in window || navigator.maxTouchPoints > 0;
    }
    
    prefersReducedMotion() {
        return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    
    getCsrfToken() {
        const token = document.querySelector('[name=csrfmiddlewaretoken]');
        return token ? token.value : '';
    }
    
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }
    
    dispatchEvent(eventType, detail = {}) {
        const event = new CustomEvent(`vinsdelux:${eventType}`, {
            detail,
            bubbles: true
        });
        document.dispatchEvent(event);
    }
    
    log(...args) {
        if (this.config.debug) {
            console.log('[VinsDelux]', ...args);
        }
    }
    
    handleInitializationError(error) {
        console.error('VinsDelux initialization failed:', error);
        
        // Show user-friendly error message
        this.showNotification(
            'Application failed to initialize. Please refresh the page.',
            'error',
            10000
        );
        
        // Track error
        this.trackEvent('initialization_error', {
            error: error.message,
            stack: error.stack
        });
    }
    
    // Public API methods
    getState() {
        return { ...this.state };
    }
    
    getConfig() {
        return { ...this.config };
    }
    
    getComponent(name) {
        return this.components[name];
    }
    
    // Cleanup method
    destroy() {
        // Destroy all components
        Object.values(this.components).forEach(component => {
            if (component && typeof component.destroy === 'function') {
                component.destroy();
            }
        });
        
        // Clear event handlers
        this.eventHandlers.clear();
        
        // Reset state
        this.state.isInitialized = false;
        
        this.log('VinsDelux application destroyed');
    }
}

// Initialize application when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Check if we're on a VinsDelux page
    if (document.body.classList.contains('vinsdelux-page') || 
        document.querySelector('.plot-selector-container') ||
        window.location.pathname.includes('/vinsdelux/')) {
        
        // Get configuration from Django template if available
        const config = {
            debug: window.DEBUG_MODE || false,
            language: document.documentElement.lang || 'en',
            apiBaseUrl: window.API_BASE_URL || '/api',
            enableAnalytics: window.ENABLE_ANALYTICS !== false,
            maxPlotSelections: window.MAX_PLOT_SELECTIONS || 5
        };
        
        // Initialize application
        window.vinsdeluxApp = new VinsDeluxApp(config);
        
        // Make components available globally for debugging
        if (config.debug) {
            window.VineyardMap = typeof VineyardMap !== 'undefined' ? VineyardMap : null;
            window.PlotSelection = typeof PlotSelection !== 'undefined' ? PlotSelection : null;
            window.VineyardAnimations = typeof VineyardAnimations !== 'undefined' ? VineyardAnimations : null;
        }
    }
});

export default VinsDeluxApp;