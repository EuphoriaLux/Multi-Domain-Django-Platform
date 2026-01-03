/**
 * ImmersiveFeatures Class - Advanced interactive features for VinsDelux
 * Handles seasonal timeline, virtual tours, plot comparison, and photo gallery
 */

class ImmersiveFeatures {
    constructor(options = {}) {
        this.config = {
            debug: options.debug || false,
            enableVirtualTour: options.enableVirtualTour !== false,
            enableSeasonalTimeline: options.enableSeasonalTimeline !== false,
            enablePlotComparison: options.enablePlotComparison !== false,
            enablePhotoGallery: options.enablePhotoGallery !== false,
            ...options
        };
        
        this.state = {
            currentSeason: this.getCurrentSeason(),
            tourActive: false,
            galleryOpen: false,
            comparisonMode: false,
            selectedPlotsForComparison: []
        };
        
        this.seasonalData = this.getSeasonalData();
        this.tourStops = [];
        this.galleryImages = [];
        
        this.init();
    }
    
    init() {
        if (this.config.enableSeasonalTimeline) {
            this.initializeSeasonalTimeline();
        }
        
        if (this.config.enableVirtualTour) {
            this.initializeVirtualTour();
        }
        
        if (this.config.enablePlotComparison) {
            this.initializePlotComparison();
        }
        
        if (this.config.enablePhotoGallery) {
            this.initializePhotoGallery();
        }
        
        this.setupEventListeners();
        this.log('ImmersiveFeatures initialized');
    }
    
    // Seasonal Timeline Implementation
    initializeSeasonalTimeline() {
        const container = document.getElementById('seasonal-timeline');
        if (!container) return;
        
        container.innerHTML = this.createSeasonalTimelineHTML();
        this.setupSeasonalTimelineEvents();
        this.updateSeasonalContent(this.state.currentSeason);
    }
    
    createSeasonalTimelineHTML() {
        return `
            <div class="seasonal-timeline-container">
                <div class="timeline-header">
                    <h3 class="timeline-title">A Year in the Vineyard</h3>
                    <p class="timeline-subtitle">Experience the wine-making journey through the seasons</p>
                </div>
                
                <div class="timeline-slider" id="timelineSlider">
                    <div class="timeline-track">
                        <div class="timeline-progress" id="timelineProgress"></div>
                    </div>
                    <div class="timeline-seasons">
                        ${this.seasonalData.map((season, index) => `
                            <div class="season-marker" data-season="${season.key}" data-index="${index}">
                                <div class="marker-dot">
                                    <i class="fas ${season.icon}"></i>
                                </div>
                                <div class="marker-label">${season.name}</div>
                                <div class="marker-month">${season.months}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
                
                <div class="seasonal-content" id="seasonalContent">
                    <div class="season-visual">
                        <div class="season-image" id="seasonImage"></div>
                        <div class="season-overlay">
                            <div class="season-weather" id="seasonWeather"></div>
                        </div>
                    </div>
                    
                    <div class="season-details">
                        <h4 class="season-name" id="seasonName"></h4>
                        <div class="season-description" id="seasonDescription"></div>
                        
                        <div class="vineyard-activities">
                            <h5>Vineyard Activities</h5>
                            <ul class="activities-list" id="activitiesList"></ul>
                        </div>
                        
                        <div class="wine-process">
                            <h5>Wine Making Process</h5>
                            <div class="process-steps" id="processSteps"></div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }
    
    getSeasonalData() {
        return [
            {
                key: 'spring',
                name: 'Spring Awakening',
                months: 'Mar - May',
                icon: 'fa-seedling',
                description: 'The vineyard awakens from winter dormancy. New shoots emerge, and the cycle of life begins anew.',
                activities: [
                    'Pruning and training vines',
                    'Soil preparation and fertilization',
                    'Pest and disease prevention',
                    'Equipment maintenance'
                ],
                process: [
                    'Budbreak occurs when temperatures rise',
                    'First leaves appear on the vines',
                    'Flowering begins in late spring',
                    'Early shoot positioning'
                ],
                weather: 'Mild temperatures, increasing daylight',
                image: '/static/vinsdelux/images/vineyard-defaults/spring-vineyard.jpg',
                color: '#9CCC65'
            },
            {
                key: 'summer',
                name: 'Summer Growth',
                months: 'Jun - Aug',
                icon: 'fa-sun',
                description: 'The vines flourish under warm sunshine. Grapes develop and begin their journey to maturity.',
                activities: [
                    'Canopy management',
                    'Fruit thinning for quality',
                    'Irrigation management',
                    'Continuous monitoring'
                ],
                process: [
                    'Fruit set occurs after flowering',
                    'Grapes begin to grow and cluster',
                    'Véraison starts (color change)',
                    'Sugar development begins'
                ],
                weather: 'Warm days, mild nights',
                image: '/static/vinsdelux/images/vineyard-defaults/summer-vineyard.jpg',
                color: '#FF9800'
            },
            {
                key: 'autumn',
                name: 'Harvest Time',
                months: 'Sep - Nov',
                icon: 'fa-wine-glass',
                description: 'The culmination of a year of care. Grapes reach perfect ripeness for harvest.',
                activities: [
                    'Harvest planning and timing',
                    'Hand-picking premium grapes',
                    'Quality selection and sorting',
                    'Immediate processing'
                ],
                process: [
                    'Final ripening of grapes',
                    'Sugar and acid balance perfected',
                    'Harvest begins at optimal timing',
                    'Crushing and fermentation starts'
                ],
                weather: 'Crisp mornings, warm afternoons',
                image: '/static/vinsdelux/images/vineyard-defaults/autumn-vineyard.jpg',
                color: '#D4AF37'
            },
            {
                key: 'winter',
                name: 'Winter Rest',
                months: 'Dec - Feb',
                icon: 'fa-snowflake',
                description: 'The vineyard enters dormancy. A time for planning, maintenance, and anticipation.',
                activities: [
                    'Vine dormancy management',
                    'Cellar work and wine aging',
                    'Equipment overhaul',
                    'Planning for next season'
                ],
                process: [
                    'Fermentation continues in cellar',
                    'Wine aging and development',
                    'Blending decisions made',
                    'Bottling of previous vintages'
                ],
                weather: 'Cold temperatures, frost protection',
                image: '/static/vinsdelux/images/vineyard-defaults/winter-vineyard.jpg',
                color: '#42A5F5'
            }
        ];
    }
    
    setupSeasonalTimelineEvents() {
        const markers = document.querySelectorAll('.season-marker');
        const slider = document.getElementById('timelineSlider');
        
        markers.forEach(marker => {
            marker.addEventListener('click', () => {
                const season = marker.dataset.season;
                this.updateSeasonalContent(season);
                this.updateTimelineProgress(parseInt(marker.dataset.index));
            });
            
            marker.addEventListener('mouseenter', () => {
                marker.style.transform = 'scale(1.1)';
            });
            
            marker.addEventListener('mouseleave', () => {
                marker.style.transform = 'scale(1)';
            });
        });
        
        // Auto-advance timeline
        this.startSeasonalAutoplay();
    }
    
    updateSeasonalContent(seasonKey) {
        const season = this.seasonalData.find(s => s.key === seasonKey);
        if (!season) return;
        
        this.state.currentSeason = seasonKey;
        
        // Update visual elements
        const seasonImage = document.getElementById('seasonImage');
        const seasonName = document.getElementById('seasonName');
        const seasonDescription = document.getElementById('seasonDescription');
        const activitiesList = document.getElementById('activitiesList');
        const processSteps = document.getElementById('processSteps');
        const seasonWeather = document.getElementById('seasonWeather');
        
        if (seasonImage) {
            seasonImage.style.backgroundImage = `url(${season.image})`;
            seasonImage.style.borderColor = season.color;
        }
        
        if (seasonName) seasonName.textContent = season.name;
        if (seasonDescription) seasonDescription.textContent = season.description;
        if (seasonWeather) seasonWeather.textContent = season.weather;
        
        if (activitiesList) {
            activitiesList.innerHTML = season.activities.map(activity => 
                `<li><i class="fas fa-check"></i> ${activity}</li>`
            ).join('');
        }
        
        if (processSteps) {
            processSteps.innerHTML = season.process.map((step, index) => 
                `<div class="process-step">
                    <div class="step-number">${index + 1}</div>
                    <div class="step-text">${step}</div>
                </div>`
            ).join('');
        }
        
        // Update marker states
        document.querySelectorAll('.season-marker').forEach(marker => {
            marker.classList.toggle('active', marker.dataset.season === seasonKey);
        });
        
        // Trigger animation
        this.animateSeasonalTransition();
    }
    
    updateTimelineProgress(index) {
        const progress = document.getElementById('timelineProgress');
        if (progress) {
            const percentage = (index / (this.seasonalData.length - 1)) * 100;
            progress.style.width = `${percentage}%`;
        }
    }
    
    startSeasonalAutoplay() {
        setInterval(() => {
            if (this.state.tourActive) return;
            
            const currentIndex = this.seasonalData.findIndex(s => s.key === this.state.currentSeason);
            const nextIndex = (currentIndex + 1) % this.seasonalData.length;
            const nextSeason = this.seasonalData[nextIndex];
            
            this.updateSeasonalContent(nextSeason.key);
            this.updateTimelineProgress(nextIndex);
        }, 10000); // Change season every 10 seconds
    }
    
    animateSeasonalTransition() {
        const content = document.getElementById('seasonalContent');
        if (!content) return;
        
        // Add transition effect
        content.style.opacity = '0.8';
        content.style.transform = 'scale(0.98)';
        
        setTimeout(() => {
            content.style.opacity = '1';
            content.style.transform = 'scale(1)';
        }, 300);
    }
    
    getCurrentSeason() {
        const month = new Date().getMonth();
        if (month >= 2 && month <= 4) return 'spring';
        if (month >= 5 && month <= 7) return 'summer';
        if (month >= 8 && month <= 10) return 'autumn';
        return 'winter';
    }
    
    // Virtual Tour Implementation
    initializeVirtualTour() {
        this.tourStops = [
            {
                id: 'vineyard-overview',
                title: 'Vineyard Overview',
                description: 'Welcome to our prestigious vineyard in Luxembourg\'s wine region.',
                position: [49.5441, 6.3750],
                image: '/static/vinsdelux/images/vineyard-defaults/vineyard_01.jpg',
                duration: 5000
            },
            {
                id: 'premium-plots',
                title: 'Premium Plot Selection',
                description: 'Our finest plots with optimal sun exposure and soil conditions.',
                position: [49.5461, 6.3770],
                image: '/static/vinsdelux/images/vineyard-defaults/vineyard_02.jpg',
                duration: 4000
            },
            {
                id: 'wine-cellar',
                title: 'Wine Cellar',
                description: 'Where tradition meets modern winemaking techniques.',
                position: [49.5421, 6.3730],
                image: '/static/vinsdelux/images/vineyard-defaults/vineyard_03.jpg',
                duration: 6000
            },
            {
                id: 'tasting-room',
                title: 'Tasting Experience',
                description: 'Discover the unique flavors of our terroir.',
                position: [49.5451, 6.3760],
                image: '/static/vinsdelux/images/vineyard-defaults/vineyard_04.jpg',
                duration: 4000
            }
        ];
        
        this.createVirtualTourInterface();
    }
    
    createVirtualTourInterface() {
        const tourHTML = `
            <div class="virtual-tour-overlay" id="virtualTourOverlay">
                <div class="tour-controls">
                    <button class="tour-btn start-tour-btn" id="startTourBtn">
                        <i class="fas fa-play"></i> Start Virtual Tour
                    </button>
                    <button class="tour-btn stop-tour-btn" id="stopTourBtn" style="display: none;">
                        <i class="fas fa-stop"></i> Stop Tour
                    </button>
                </div>
                
                <div class="tour-content" id="tourContent" style="display: none;">
                    <div class="tour-image-container">
                        <img id="tourImage" src="" alt="" class="tour-image">
                        <div class="tour-progress">
                            <div class="progress-bar" id="tourProgress"></div>
                        </div>
                    </div>
                    
                    <div class="tour-info">
                        <h4 id="tourStopTitle"></h4>
                        <p id="tourStopDescription"></p>
                        <div class="tour-navigation">
                            <button class="nav-btn prev-btn" id="prevStopBtn">
                                <i class="fas fa-chevron-left"></i>
                            </button>
                            <span class="stop-counter" id="stopCounter"></span>
                            <button class="nav-btn next-btn" id="nextStopBtn">
                                <i class="fas fa-chevron-right"></i>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', tourHTML);
        this.setupVirtualTourEvents();
    }
    
    setupVirtualTourEvents() {
        const startBtn = document.getElementById('startTourBtn');
        const stopBtn = document.getElementById('stopTourBtn');
        const prevBtn = document.getElementById('prevStopBtn');
        const nextBtn = document.getElementById('nextStopBtn');
        
        startBtn?.addEventListener('click', () => this.startVirtualTour());
        stopBtn?.addEventListener('click', () => this.stopVirtualTour());
        prevBtn?.addEventListener('click', () => this.previousTourStop());
        nextBtn?.addEventListener('click', () => this.nextTourStop());
    }
    
    startVirtualTour() {
        this.state.tourActive = true;
        this.currentTourStop = 0;
        
        document.getElementById('startTourBtn').style.display = 'none';
        document.getElementById('stopTourBtn').style.display = 'block';
        document.getElementById('tourContent').style.display = 'block';
        
        this.showTourStop(0);
    }
    
    stopVirtualTour() {
        this.state.tourActive = false;
        
        document.getElementById('startTourBtn').style.display = 'block';
        document.getElementById('stopTourBtn').style.display = 'none';
        document.getElementById('tourContent').style.display = 'none';
        
        if (this.tourTimer) {
            clearTimeout(this.tourTimer);
        }
    }
    
    showTourStop(index) {
        if (index < 0 || index >= this.tourStops.length) return;
        
        const stop = this.tourStops[index];
        this.currentTourStop = index;
        
        // Update tour content
        document.getElementById('tourImage').src = stop.image;
        document.getElementById('tourStopTitle').textContent = stop.title;
        document.getElementById('tourStopDescription').textContent = stop.description;
        document.getElementById('stopCounter').textContent = `${index + 1} / ${this.tourStops.length}`;
        
        // Start progress animation
        this.animateTourProgress(stop.duration);
        
        // Auto-advance to next stop
        this.tourTimer = setTimeout(() => {
            if (this.state.tourActive) {
                const nextIndex = (index + 1) % this.tourStops.length;
                this.showTourStop(nextIndex);
            }
        }, stop.duration);
    }
    
    animateTourProgress(duration) {
        const progressBar = document.getElementById('tourProgress');
        if (!progressBar) return;
        
        progressBar.style.transition = 'none';
        progressBar.style.width = '0%';
        
        setTimeout(() => {
            progressBar.style.transition = `width ${duration}ms linear`;
            progressBar.style.width = '100%';
        }, 50);
    }
    
    previousTourStop() {
        if (this.tourTimer) clearTimeout(this.tourTimer);
        const prevIndex = this.currentTourStop > 0 ? this.currentTourStop - 1 : this.tourStops.length - 1;
        this.showTourStop(prevIndex);
    }
    
    nextTourStop() {
        if (this.tourTimer) clearTimeout(this.tourTimer);
        const nextIndex = (this.currentTourStop + 1) % this.tourStops.length;
        this.showTourStop(nextIndex);
    }
    
    // Plot Comparison Implementation
    initializePlotComparison() {
        this.createPlotComparisonInterface();
        this.setupPlotComparisonEvents();
    }
    
    createPlotComparisonInterface() {
        const comparisonHTML = `
            <div class="plot-comparison-modal" id="plotComparisonModal">
                <div class="comparison-content">
                    <div class="comparison-header">
                        <h3>Compare Wine Plots</h3>
                        <button class="close-comparison" id="closeComparison">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    
                    <div class="comparison-plots" id="comparisonPlots">
                        <div class="plot-slot" id="plotSlot1">
                            <div class="slot-placeholder">
                                <i class="fas fa-plus"></i>
                                <span>Select first plot</span>
                            </div>
                        </div>
                        
                        <div class="comparison-divider">
                            <span>VS</span>
                        </div>
                        
                        <div class="plot-slot" id="plotSlot2">
                            <div class="slot-placeholder">
                                <i class="fas fa-plus"></i>
                                <span>Select second plot</span>
                            </div>
                        </div>
                    </div>
                    
                    <div class="comparison-table" id="comparisonTable" style="display: none;">
                        <!-- Comparison table will be populated here -->
                    </div>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', comparisonHTML);
    }
    
    setupPlotComparisonEvents() {
        document.getElementById('closeComparison')?.addEventListener('click', () => {
            this.closePlotComparison();
        });
        
        // Listen for plot comparison requests
        document.addEventListener('plot:compare', (e) => {
            this.addPlotToComparison(e.detail.plot);
        });
    }
    
    openPlotComparison() {
        document.getElementById('plotComparisonModal').style.display = 'flex';
        this.state.comparisonMode = true;
    }
    
    closePlotComparison() {
        document.getElementById('plotComparisonModal').style.display = 'none';
        this.state.comparisonMode = false;
        this.clearComparison();
    }
    
    addPlotToComparison(plot) {
        if (this.state.selectedPlotsForComparison.length >= 2) {
            this.clearComparison();
        }
        
        this.state.selectedPlotsForComparison.push(plot);
        this.updateComparisonDisplay();
        
        if (!this.state.comparisonMode) {
            this.openPlotComparison();
        }
    }
    
    updateComparisonDisplay() {
        const plots = this.state.selectedPlotsForComparison;
        
        plots.forEach((plot, index) => {
            const slot = document.getElementById(`plotSlot${index + 1}`);
            if (slot) {
                slot.innerHTML = this.createPlotComparisonCard(plot);
            }
        });
        
        if (plots.length === 2) {
            this.generateComparisonTable(plots[0], plots[1]);
        }
    }
    
    createPlotComparisonCard(plot) {
        return `
            <div class="comparison-plot-card">
                <div class="plot-image">
                    <img src="${this.getPlotImage(plot)}" alt="${plot.name}">
                </div>
                <div class="plot-info">
                    <h4>${plot.name}</h4>
                    <p class="producer">${plot.producer}</p>
                    <p class="region">${plot.region}</p>
                    <p class="price">€${plot.price?.toLocaleString() || 'N/A'}</p>
                </div>
                <button class="remove-from-comparison" onclick="this.closest('.comparison-plot-card').parentElement.innerHTML = '<div class=\\'slot-placeholder\\'><i class=\\'fas fa-plus\\'></i><span>Select plot</span></div>'">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
    }
    
    generateComparisonTable(plot1, plot2) {
        const table = document.getElementById('comparisonTable');
        if (!table) return;
        
        const comparisonData = [
            { label: 'Price', plot1: `€${plot1.price?.toLocaleString() || 'N/A'}`, plot2: `€${plot2.price?.toLocaleString() || 'N/A'}` },
            { label: 'Region', plot1: plot1.region || 'N/A', plot2: plot2.region || 'N/A' },
            { label: 'Wine Type', plot1: plot1.wine_type || plot1.wineType || 'N/A', plot2: plot2.wine_type || plot2.wineType || 'N/A' },
            { label: 'Elevation', plot1: plot1.elevation || 'N/A', plot2: plot2.elevation || 'N/A' },
            { label: 'Soil Type', plot1: plot1.soil_type || plot1.soilType || 'N/A', plot2: plot2.soil_type || plot2.soilType || 'N/A' },
            { label: 'Sun Exposure', plot1: plot1.sun_exposure || plot1.sunExposure || 'N/A', plot2: plot2.sun_exposure || plot2.sunExposure || 'N/A' },
            { label: 'Features', plot1: (plot1.features || []).join(', ') || 'N/A', plot2: (plot2.features || []).join(', ') || 'N/A' }
        ];
        
        table.innerHTML = `
            <table class="comparison-data-table">
                <thead>
                    <tr>
                        <th>Feature</th>
                        <th>${plot1.name}</th>
                        <th>${plot2.name}</th>
                    </tr>
                </thead>
                <tbody>
                    ${comparisonData.map(row => `
                        <tr>
                            <td class="feature-label">${row.label}</td>
                            <td class="plot-value">${row.plot1}</td>
                            <td class="plot-value">${row.plot2}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
        
        table.style.display = 'block';
    }
    
    clearComparison() {
        this.state.selectedPlotsForComparison = [];
        document.getElementById('plotSlot1').innerHTML = '<div class="slot-placeholder"><i class="fas fa-plus"></i><span>Select first plot</span></div>';
        document.getElementById('plotSlot2').innerHTML = '<div class="slot-placeholder"><i class="fas fa-plus"></i><span>Select second plot</span></div>';
        document.getElementById('comparisonTable').style.display = 'none';
    }
    
    // Photo Gallery Implementation
    initializePhotoGallery() {
        this.createPhotoGallery();
        this.setupPhotoGalleryEvents();
    }
    
    createPhotoGallery() {
        // Photo gallery will be triggered by vineyard/plot images
        const galleryHTML = `
            <div class="photo-gallery-lightbox" id="photoGalleryLightbox">
                <div class="lightbox-content">
                    <div class="gallery-header">
                        <h3 id="galleryTitle">Vineyard Gallery</h3>
                        <button class="close-gallery" id="closeGallery">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                    
                    <div class="gallery-main-image">
                        <img id="galleryMainImage" src="" alt="">
                        <div class="image-navigation">
                            <button class="nav-btn prev-image" id="prevImage">
                                <i class="fas fa-chevron-left"></i>
                            </button>
                            <button class="nav-btn next-image" id="nextImage">
                                <i class="fas fa-chevron-right"></i>
                            </button>
                        </div>
                    </div>
                    
                    <div class="gallery-thumbnails" id="galleryThumbnails">
                        <!-- Thumbnails will be populated here -->
                    </div>
                    
                    <div class="image-info" id="imageInfo">
                        <h4 id="imageTitle"></h4>
                        <p id="imageDescription"></p>
                    </div>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', galleryHTML);
    }
    
    setupPhotoGalleryEvents() {
        document.getElementById('closeGallery')?.addEventListener('click', () => {
            this.closePhotoGallery();
        });
        
        document.getElementById('prevImage')?.addEventListener('click', () => {
            this.showPreviousImage();
        });
        
        document.getElementById('nextImage')?.addEventListener('click', () => {
            this.showNextImage();
        });
        
        // Handle image clicks to open gallery
        document.addEventListener('click', (e) => {
            if (e.target.matches('.vineyard-image, .plot-image, .gallery-trigger')) {
                this.openPhotoGallery(e.target);
            }
        });
        
        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (!this.state.galleryOpen) return;
            
            switch (e.key) {
                case 'ArrowLeft':
                    this.showPreviousImage();
                    break;
                case 'ArrowRight':
                    this.showNextImage();
                    break;
                case 'Escape':
                    this.closePhotoGallery();
                    break;
            }
        });
    }
    
    openPhotoGallery(triggerElement) {
        const galleryData = this.getGalleryData(triggerElement);
        this.galleryImages = galleryData.images;
        this.currentImageIndex = galleryData.startIndex || 0;
        
        document.getElementById('galleryTitle').textContent = galleryData.title || 'Vineyard Gallery';
        
        this.populateGalleryThumbnails();
        this.showGalleryImage(this.currentImageIndex);
        
        document.getElementById('photoGalleryLightbox').style.display = 'flex';
        this.state.galleryOpen = true;
    }
    
    closePhotoGallery() {
        document.getElementById('photoGalleryLightbox').style.display = 'none';
        this.state.galleryOpen = false;
    }
    
    getGalleryData(triggerElement) {
        // Default vineyard images
        const defaultImages = [
            {
                src: '/static/vinsdelux/images/vineyard-defaults/vineyard_01.jpg',
                title: 'Vineyard Overview',
                description: 'Panoramic view of our Luxembourg vineyard'
            },
            {
                src: '/static/vinsdelux/images/vineyard-defaults/vineyard_02.jpg',
                title: 'Premium Plots',
                description: 'Our finest wine-producing plots'
            },
            {
                src: '/static/vinsdelux/images/vineyard-defaults/vineyard_03.jpg',
                title: 'Harvest Season',
                description: 'Hand-picking grapes at optimal ripeness'
            },
            {
                src: '/static/vinsdelux/images/vineyard-defaults/vineyard_04.jpg',
                title: 'Wine Cellar',
                description: 'Traditional aging process in oak barrels'
            },
            {
                src: '/static/vinsdelux/images/vineyard-defaults/vineyard_05.jpg',
                title: 'Tasting Room',
                description: 'Experience our wines in elegant surroundings'
            }
        ];
        
        return {
            title: triggerElement.dataset.galleryTitle || 'Vineyard Gallery',
            images: defaultImages,
            startIndex: 0
        };
    }
    
    populateGalleryThumbnails() {
        const container = document.getElementById('galleryThumbnails');
        if (!container) return;
        
        container.innerHTML = this.galleryImages.map((image, index) => `
            <div class="gallery-thumbnail ${index === this.currentImageIndex ? 'active' : ''}" 
                 data-index="${index}"
                 onclick="window.immersiveFeatures?.showGalleryImage(${index})">
                <img src="${image.src}" alt="${image.title}">
            </div>
        `).join('');
    }
    
    showGalleryImage(index) {
        if (index < 0 || index >= this.galleryImages.length) return;
        
        this.currentImageIndex = index;
        const image = this.galleryImages[index];
        
        document.getElementById('galleryMainImage').src = image.src;
        document.getElementById('imageTitle').textContent = image.title;
        document.getElementById('imageDescription').textContent = image.description;
        
        // Update thumbnail active state
        document.querySelectorAll('.gallery-thumbnail').forEach((thumb, i) => {
            thumb.classList.toggle('active', i === index);
        });
    }
    
    showPreviousImage() {
        const prevIndex = this.currentImageIndex > 0 
            ? this.currentImageIndex - 1 
            : this.galleryImages.length - 1;
        this.showGalleryImage(prevIndex);
    }
    
    showNextImage() {
        const nextIndex = (this.currentImageIndex + 1) % this.galleryImages.length;
        this.showGalleryImage(nextIndex);
    }
    
    // Utility methods
    getPlotImage(plot) {
        if (plot.image) return plot.image;
        if (plot.producer_image) return plot.producer_image;
        
        const wineTypes = ['red', 'white', 'rose', 'burgundy', 'bordeaux'];
        const type = plot.wine_type?.toLowerCase() || plot.wineType?.toLowerCase() || 'red';
        const imageType = wineTypes.includes(type) ? type : 'vineyard';
        const imageNum = (plot.id % 5) + 1;
        
        return `/static/vinsdelux/images/vineyard-defaults/${imageType}_0${imageNum}.jpg`;
    }
    
    setupEventListeners() {
        // Global event listeners for immersive features
        document.addEventListener('click', (e) => {
            // Virtual tour trigger
            if (e.target.matches('.virtual-tour-trigger')) {
                if (!this.state.tourActive) {
                    this.startVirtualTour();
                }
            }
            
            // Comparison trigger
            if (e.target.matches('.compare-plot-btn')) {
                const plotData = JSON.parse(e.target.dataset.plot || '{}');
                this.addPlotToComparison(plotData);
            }
            
            // Seasonal timeline trigger
            if (e.target.matches('.seasonal-timeline-trigger')) {
                this.showSeasonalTimeline();
            }
        });
    }
    
    showSeasonalTimeline() {
        const timeline = document.getElementById('seasonal-timeline');
        if (timeline) {
            timeline.scrollIntoView({ behavior: 'smooth' });
        }
    }
    
    log(...args) {
        if (this.config.debug) {
            console.log('[ImmersiveFeatures]', ...args);
        }
    }
    
    // Public API
    getCurrentSeason() {
        return this.state.currentSeason;
    }
    
    isTourActive() {
        return this.state.tourActive;
    }
    
    isGalleryOpen() {
        return this.state.galleryOpen;
    }
    
    isComparisonMode() {
        return this.state.comparisonMode;
    }
    
    destroy() {
        // Clean up timers and event listeners
        if (this.tourTimer) {
            clearTimeout(this.tourTimer);
        }
        
        // Remove created elements
        document.getElementById('virtualTourOverlay')?.remove();
        document.getElementById('plotComparisonModal')?.remove();
        document.getElementById('photoGalleryLightbox')?.remove();
        
        this.log('ImmersiveFeatures destroyed');
    }
}

// CSS styles for immersive features
const immersiveStyles = `
<style>
/* Seasonal Timeline Styles */
.seasonal-timeline-container {
    background: linear-gradient(135deg, #1a1a3e 0%, #2d1b69 100%);
    padding: 40px 20px;
    border-radius: 20px;
    margin: 20px 0;
    color: white;
}

.timeline-header {
    text-align: center;
    margin-bottom: 30px;
}

.timeline-title {
    font-size: 2.5rem;
    color: #D4AF37;
    margin-bottom: 10px;
}

.timeline-slider {
    position: relative;
    margin: 40px 0;
}

.timeline-track {
    height: 4px;
    background: rgba(255,255,255,0.2);
    border-radius: 2px;
    position: relative;
}

.timeline-progress {
    height: 100%;
    background: linear-gradient(90deg, #D4AF37, #722F37);
    border-radius: 2px;
    transition: width 0.5s ease;
}

.timeline-seasons {
    display: flex;
    justify-content: space-between;
    margin-top: 20px;
}

.season-marker {
    display: flex;
    flex-direction: column;
    align-items: center;
    cursor: pointer;
    transition: all 0.3s ease;
}

.season-marker.active .marker-dot {
    background: #D4AF37;
    transform: scale(1.2);
}

.marker-dot {
    width: 50px;
    height: 50px;
    border-radius: 50%;
    background: rgba(255,255,255,0.2);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 10px;
    transition: all 0.3s ease;
}

.marker-dot i {
    font-size: 20px;
    color: white;
}

.seasonal-content {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 40px;
    margin-top: 40px;
    transition: all 0.3s ease;
}

.season-image {
    width: 100%;
    height: 400px;
    background-size: cover;
    background-position: center;
    border-radius: 15px;
    border: 3px solid transparent;
    transition: all 0.3s ease;
}

/* Virtual Tour Styles */
.virtual-tour-overlay {
    position: fixed;
    top: 20px;
    right: 20px;
    z-index: 1000;
    background: rgba(26, 26, 62, 0.95);
    border-radius: 15px;
    padding: 20px;
    color: white;
    max-width: 400px;
}

.tour-controls {
    margin-bottom: 20px;
}

.tour-btn {
    background: linear-gradient(135deg, #722F37 0%, #D4AF37 100%);
    color: white;
    border: none;
    padding: 12px 24px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 16px;
    transition: all 0.3s ease;
}

.tour-btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 20px rgba(114, 47, 55, 0.4);
}

.tour-image-container {
    position: relative;
    margin-bottom: 15px;
}

.tour-image {
    width: 100%;
    height: 200px;
    object-fit: cover;
    border-radius: 10px;
}

.tour-progress {
    position: absolute;
    bottom: 10px;
    left: 10px;
    right: 10px;
    height: 4px;
    background: rgba(255,255,255,0.3);
    border-radius: 2px;
}

.progress-bar {
    height: 100%;
    background: #D4AF37;
    border-radius: 2px;
    width: 0%;
}

/* Plot Comparison Styles */
.plot-comparison-modal {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0,0,0,0.8);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 2000;
}

.comparison-content {
    background: white;
    border-radius: 20px;
    padding: 30px;
    max-width: 900px;
    max-height: 90vh;
    overflow-y: auto;
    width: 90%;
}

.comparison-plots {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 30px;
    align-items: center;
    margin: 30px 0;
}

.plot-slot {
    min-height: 300px;
    border: 2px dashed #ddd;
    border-radius: 15px;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
}

.slot-placeholder {
    text-align: center;
    color: #999;
}

.comparison-plot-card {
    text-align: center;
    padding: 20px;
}

.comparison-plot-card .plot-image img {
    width: 100%;
    height: 150px;
    object-fit: cover;
    border-radius: 10px;
}

/* Photo Gallery Styles */
.photo-gallery-lightbox {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0,0,0,0.95);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 3000;
}

.lightbox-content {
    background: #1a1a3e;
    border-radius: 20px;
    padding: 30px;
    max-width: 90vw;
    max-height: 90vh;
    color: white;
    overflow-y: auto;
}

.gallery-main-image {
    position: relative;
    text-align: center;
    margin: 20px 0;
}

.gallery-main-image img {
    max-width: 100%;
    max-height: 60vh;
    border-radius: 10px;
}

.image-navigation {
    position: absolute;
    top: 50%;
    left: 0;
    right: 0;
    transform: translateY(-50%);
    display: flex;
    justify-content: space-between;
    pointer-events: none;
}

.image-navigation .nav-btn {
    background: rgba(0,0,0,0.7);
    border: none;
    color: white;
    width: 50px;
    height: 50px;
    border-radius: 50%;
    cursor: pointer;
    pointer-events: auto;
    transition: all 0.3s ease;
}

.image-navigation .nav-btn:hover {
    background: rgba(0,0,0,0.9);
    transform: scale(1.1);
}

.gallery-thumbnails {
    display: flex;
    gap: 10px;
    justify-content: center;
    flex-wrap: wrap;
    margin: 20px 0;
}

.gallery-thumbnail {
    width: 80px;
    height: 60px;
    border-radius: 5px;
    overflow: hidden;
    cursor: pointer;
    border: 2px solid transparent;
    transition: all 0.3s ease;
}

.gallery-thumbnail.active {
    border-color: #D4AF37;
}

.gallery-thumbnail img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.gallery-thumbnail:hover {
    transform: scale(1.05);
}

/* Responsive Design */
@media (max-width: 768px) {
    .seasonal-content {
        grid-template-columns: 1fr;
        gap: 20px;
    }
    
    .comparison-plots {
        grid-template-columns: 1fr;
        gap: 20px;
    }
    
    .comparison-divider {
        transform: rotate(90deg);
    }
    
    .virtual-tour-overlay {
        position: fixed;
        bottom: 20px;
        top: auto;
        left: 20px;
        right: 20px;
        max-width: none;
    }
}
</style>
`;

// Inject styles
if (typeof document !== 'undefined') {
    document.head.insertAdjacentHTML('beforeend', immersiveStyles);
}

export default ImmersiveFeatures;