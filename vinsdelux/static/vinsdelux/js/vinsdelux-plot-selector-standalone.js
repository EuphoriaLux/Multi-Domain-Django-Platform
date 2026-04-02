/**
 * VinsDelux Standalone Plot Selector
 * Simplified version focused only on plot selection without game elements
 */

class StandalonePlotSelector {
    constructor() {
        this.selectedPlot = null;
        this.plots = [];
        this.mapView = false;
        this.init();
    }
    
    init() {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.init());
            return;
        }
        
        this.loadPlots();
        this.setupEventListeners();
        this.initializeMap();
    }
    
    async loadPlots() {
        try {
            // Detect if we're on vinsdelux.com domain (production)
            const hostname = window.location.hostname;
            // Use exact match instead of substring check for security
            const isVinsDeluxDomain = hostname === 'vinsdelux.com' || hostname === 'www.vinsdelux.com';
            const currentPath = window.location.pathname;
            let apiUrl;
            
            if (isVinsDeluxDomain) {
                // On vinsdelux.com, the API is at the root (no /vinsdelux prefix needed)
                apiUrl = '/api/adoption-plans/';
                console.log('Detected vinsdelux.com domain, using root API path');
            } else {
                // For local development or other domains
                const pathParts = currentPath.split('/').filter(p => p);
                const hasLangPrefix = pathParts[0] && /^[a-z]{2}$/i.test(pathParts[0]);
                
                if (hasLangPrefix) {
                    // Use the existing language prefix
                    apiUrl = `/${pathParts[0]}/vinsdelux/api/adoption-plans/`;
                } else {
                    // Try without language prefix first
                    apiUrl = '/vinsdelux/api/adoption-plans/';
                }
            }
            
            console.log('Fetching adoption plans from:', apiUrl);
            
            // Try the primary URL
            let response = await fetch(apiUrl);
            
            // If on vinsdelux.com and still getting 404, try with vinsdelux prefix as fallback
            if (isVinsDeluxDomain && response.status === 404) {
                console.log('First attempt failed, trying with /vinsdelux prefix');
                apiUrl = '/vinsdelux/api/adoption-plans/';
                response = await fetch(apiUrl);
            }
            
            // If not on vinsdelux domain and failing, try other patterns
            if (!isVinsDeluxDomain && response.status === 404) {
                console.log('First attempt failed, trying with /en/ prefix');
                apiUrl = '/en/vinsdelux/api/adoption-plans/';
                response = await fetch(apiUrl);
            }
            
            // Check if response is OK
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            // Check content type
            const contentType = response.headers.get("content-type");
            if (!contentType || !contentType.includes("application/json")) {
                throw new Error("Response is not JSON!");
            }
            
            const data = await response.json();
            
            // Check if data has the expected structure
            if (!data.adoption_plans || !Array.isArray(data.adoption_plans)) {
                console.error('Invalid data structure:', data);
                throw new Error('Invalid data structure received from API');
            }
            
            // Transform API data to plot format
            this.plots = data.adoption_plans.map(plan => ({
                id: `plot-${plan.id}`,
                planId: plan.id,
                name: plan.name,
                producerName: plan.producer.name,
                region: plan.producer.region || 'France',
                size: plan.producer.vineyard_size || 'Not specified',
                elevation: plan.producer.elevation || 'Not specified',
                soil: plan.producer.soil_type || 'Not specified',
                exposure: plan.producer.sun_exposure || 'Not specified',
                price: `€${plan.price}/year`,
                features: this.buildFeaturesList(plan),
                vineyardFeatures: plan.producer.vineyard_features || [],
                description: plan.description || 'Exceptional wine adoption opportunity.',
                x: plan.producer.map_x || 50,
                y: plan.producer.map_y || 50,
                includesVisit: plan.features.includes_visit,
                includesMedallion: plan.features.includes_medallion,
                includesClub: plan.features.includes_club_membership,
                visitDetails: plan.visit_details,
                welcomeKit: plan.welcome_kit,
                durationMonths: plan.duration_months,
                coffretsPerYear: plan.coffrets_per_year,
                // Add wine and coffret information
                wineTypes: plan.producer.wine_types || [],
                grapeVarieties: plan.producer.grape_varieties || [],
                coffret: plan.coffret || null,
                availableCoffrets: plan.available_coffrets || [],
                producerDescription: plan.producer.description || '',
                category: plan.category || 'Mixed',
                // Add images data
                images: plan.images || [],
                primaryImage: this.getPrimaryImage(plan),
                imageUrl: plan.image_url // Fallback to main image
            }));
            
            this.renderPlotCards();
            this.renderMapMarkers();
            
            // Hide loading spinner
            this.hideLoadingSpinner();
            
        } catch (error) {
            console.error('Failed to load adoption plans:', error);
            
            // Fallback to sample data for demonstration
            this.plots = this.getSamplePlots();
            
            // Show warning message about using sample data
            this.showWarningMessage('Unable to load live data. Showing sample adoption plans for demonstration.');
            
            // Still render the sample plots
            this.renderPlotCards();
            this.renderMapMarkers();
            
            // Hide loading spinner even on error
            this.hideLoadingSpinner();
        }
    }
    
    buildFeaturesList(plan) {
        const features = [];
        if (plan.features.includes_visit) features.push('Vineyard Visit');
        if (plan.features.includes_medallion) features.push('Personalized Medallion');
        if (plan.features.includes_club_membership) features.push('Club Membership');
        
        // Add vineyard features if available
        if (plan.producer.vineyard_features && plan.producer.vineyard_features.length > 0) {
            features.push(...plan.producer.vineyard_features);
        }
        
        return features;
    }
    
    getPrimaryImage(plan) {
        // Check for uploaded images first (for initial display)
        if (plan.images && plan.images.length > 0) {
            // Find primary uploaded image
            const primaryImg = plan.images.find(img => img.is_primary && !img.is_default);
            if (primaryImg && primaryImg.url) {
                return primaryImg.url;
            }
            // Return first uploaded image if no primary
            const firstUploaded = plan.images.find(img => !img.is_default);
            if (firstUploaded && firstUploaded.url) {
                return firstUploaded.url;
            }
        }
        
        // Fallback to image_url if exists
        if (plan.image_url) {
            return plan.image_url;
        }
        
        // Otherwise use default vineyard image (Azure Blob in production)
        const vineyardDefaultsUrl = document.body.dataset.vineyardDefaultsUrl || '/static/vinsdelux/images/vineyard-defaults/';
        return `${vineyardDefaultsUrl}vineyard_01.jpg`;
    }

    getRotatingImages(plot) {
        const images = [];
        const vineyardDefaultsUrl = document.body.dataset.vineyardDefaultsUrl || '/static/vinsdelux/images/vineyard-defaults/';

        // Check if plot has uploaded images
        if (plot.images && plot.images.length > 0) {
            // Filter out default images and get uploaded ones
            const uploadedImages = plot.images.filter(img => !img.is_default && img.url);

            if (uploadedImages.length > 0) {
                // Use uploaded images, repeat them if less than 5
                for (let i = 0; i < 5; i++) {
                    const img = uploadedImages[i % uploadedImages.length];
                    images.push(img.url);
                }
                return images;
            }
        }

        // If no uploaded images, use the 5 default vineyard placeholders
        for (let i = 1; i <= 5; i++) {
            images.push(`${vineyardDefaultsUrl}vineyard_0${i}.jpg`);
        }

        return images;
    }
    
    determineImageSet(plot) {
        // Always use the vineyard image set since there are no different regions/categories
        return 'vineyard';
    }
    
    showNoDataMessage() {
        const container = document.querySelector('.plot-cards-container');
        if (container) {
            container.innerHTML = '<div class="no-data-message">No adoption plans available at this time.</div>';
        }
    }
    
    showWarningMessage(message) {
        const container = document.querySelector('.plot-cards-container');
        if (container) {
            const warningDiv = document.createElement('div');
            warningDiv.className = 'alert alert-warning mb-3';
            warningDiv.style.cssText = 'padding: 15px; background: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; color: #856404;';
            warningDiv.innerHTML = `<i class="fas fa-exclamation-triangle"></i> ${message}`;
            container.parentNode.insertBefore(warningDiv, container);
        }
    }
    
    getSamplePlots() {
        // Return sample data for demonstration when API fails
        return [
            {
                id: 'plot-sample-1',
                planId: 1,
                name: 'Château Margaux Heritage',
                producerName: 'Château Margaux',
                region: 'Bordeaux',
                size: '2 hectares',
                elevation: '250m',
                soil: 'Clay-limestone',
                exposure: 'South-facing',
                price: '€2500/year',
                features: ['Vineyard Visit', 'Personalized Medallion', 'Club Membership'],
                vineyardFeatures: ['Organic certified', 'Historic vineyard'],
                description: 'Experience the legendary Château Margaux with this exclusive adoption plan.',
                x: 30,
                y: 40,
                includesVisit: true,
                includesMedallion: true,
                includesClub: true,
                visitDetails: 'Annual private tour and tasting',
                welcomeKit: 'Premium welcome kit with estate history book',
                durationMonths: 12,
                coffretsPerYear: 4
            },
            {
                id: 'plot-sample-2',
                planId: 2,
                name: 'Burgundy Premier Cru',
                producerName: 'Domaine de la Romanée',
                region: 'Burgundy',
                size: '1.5 hectares',
                elevation: '350m',
                soil: 'Limestone',
                exposure: 'East-facing',
                price: '€1800/year',
                features: ['Vineyard Visit', 'Personalized Medallion'],
                vineyardFeatures: ['Biodynamic', 'Premier Cru'],
                description: 'Own a piece of Burgundy\'s finest Premier Cru vineyard.',
                x: 60,
                y: 30,
                includesVisit: true,
                includesMedallion: true,
                includesClub: false,
                visitDetails: 'Bi-annual visits with winemaker',
                welcomeKit: 'Exclusive Burgundy wine accessories',
                durationMonths: 12,
                coffretsPerYear: 3
            },
            {
                id: 'plot-sample-3',
                planId: 3,
                name: 'Tuscany Hills Reserve',
                producerName: 'Villa Antinori',
                region: 'Tuscany',
                size: '3 hectares',
                elevation: '400m',
                soil: 'Galestro',
                exposure: 'Southwest-facing',
                price: '€2200/year',
                features: ['Vineyard Visit', 'Club Membership'],
                vineyardFeatures: ['DOCG certified', 'Ancient vines'],
                description: 'Discover the magic of Tuscan winemaking with this premium adoption.',
                x: 70,
                y: 60,
                includesVisit: true,
                includesMedallion: false,
                includesClub: true,
                visitDetails: 'Quarterly visits with harvest participation',
                welcomeKit: 'Italian wine culture collection',
                durationMonths: 12,
                coffretsPerYear: 6
            }
        ];
    }
    
    hideLoadingSpinner() {
        const spinner = document.getElementById('loading-spinner');
        if (spinner) {
            spinner.classList.remove('active');
            spinner.style.display = 'none';
        }
    }
    
    setupEventListeners() {
        // Plot card selection
        document.addEventListener('click', (e) => {
            if (e.target.closest('.plot-card')) {
                const card = e.target.closest('.plot-card');
                const plotId = card.dataset.plotId;
                this.selectPlot(plotId);
            }
            
            if (e.target.closest('.plot-marker')) {
                const marker = e.target.closest('.plot-marker');
                const plotId = marker.dataset.plotId;
                this.selectPlot(plotId);
            }
            
            // Close details panel
            if (e.target.closest('#close-details-panel')) {
                this.closeDetailsPanel();
            }
        });
        
        // View toggle
        const gridViewBtn = document.getElementById('grid-view-btn');
        const mapViewBtn = document.getElementById('map-view-btn');
        
        if (gridViewBtn) {
            gridViewBtn.addEventListener('click', () => this.showGridView());
        }
        
        if (mapViewBtn) {
            mapViewBtn.addEventListener('click', () => this.showMapView());
        }
        
        // Map controls
        const zoomInBtn = document.querySelector('.map-zoom-in');
        const zoomOutBtn = document.querySelector('.map-zoom-out');
        const resetBtn = document.querySelector('.map-reset');
        
        if (zoomInBtn) zoomInBtn.addEventListener('click', () => this.zoomMap(1.2));
        if (zoomOutBtn) zoomOutBtn.addEventListener('click', () => this.zoomMap(0.8));
        if (resetBtn) resetBtn.addEventListener('click', () => this.resetMap());
        
        // Confirm selection button
        const confirmBtn = document.getElementById('confirm-selection');
        if (confirmBtn) {
            confirmBtn.addEventListener('click', () => this.confirmSelection());
        }
    }
    
    renderPlotCards() {
        // Use plots-grid as the main container for cards
        const container = document.getElementById('plots-grid');
        if (!container) {
            console.error('Could not find plots-grid container');
            return;
        }
        
        container.innerHTML = '';
        
        this.plots.forEach((plot, index) => {
            const card = document.createElement('div');
            card.className = 'plot-card';
            card.dataset.plotId = plot.id;
            card.style.setProperty('--index', index + 1);
            
            // Get images for this plot
            const images = this.getRotatingImages(plot);
            
            // For variety, start each card at a different image in the rotation
            let startingIndex = 0;
            let primaryImage = plot.primaryImage;
            
            if (!primaryImage && images.length > 0) {
                // Calculate starting index based on card position
                startingIndex = index % images.length;
                primaryImage = images[startingIndex];
            }
            const vineyardFallback = document.body.dataset.vineyardDefaultsUrl || '/static/vinsdelux/images/vineyard-defaults/';
            primaryImage = primaryImage || `${vineyardFallback}vineyard_01.jpg`;
            
            // Create image carousel container
            const imageCarouselHtml = images.length > 1 ? `
                <div class="image-indicators" style="position: absolute; bottom: 10px; left: 50%; transform: translateX(-50%); display: flex; gap: 5px; z-index: 5;">
                    ${images.map((_, idx) => `
                        <span class="indicator" data-index="${idx}" style="width: 8px; height: 8px; border-radius: 50%; background: rgba(255,255,255,${idx === 0 ? '1' : '0.5'}); cursor: pointer; transition: all 0.3s;"></span>
                    `).join('')}
                </div>
            ` : '';
            
            card.innerHTML = `
                <div class="card-image" style="position: relative; height: 180px; overflow: hidden; background: linear-gradient(135deg, #8BC34A, #689F38);">
                    <div class="image-carousel" data-images='${JSON.stringify(images)}' data-current="0">
                        <img src="${primaryImage}" alt="${plot.name}" style="width: 100%; height: 100%; object-fit: cover; transition: opacity 0.5s;" onerror="this.src='/static/vinsdelux/images/journey/step_01.png'">
                    </div>
                    ${imageCarouselHtml}
                    <div class="card-badge" style="position: absolute; top: 12px; right: 12px; background: rgba(255,255,255,0.95); padding: 6px 12px; border-radius: 20px; font-size: 0.875rem; font-weight: 600; color: #495057;">
                        ${plot.region}
                    </div>
                </div>
                <div class="card-content">
                    <h3 style="font-size: 1.25rem; margin-bottom: 0.75rem; color: #2c3e50;">${plot.name}</h3>
                    <p class="plot-description" style="color: #6c757d; line-height: 1.5; margin-bottom: 1rem; flex: 1;">${plot.description}</p>
                    <div class="plot-features">
                        <div class="feature-item">
                            <i class="fas fa-mountain"></i>
                            <span>${plot.elevation} elevation</span>
                        </div>
                        <div class="feature-item">
                            <i class="fas fa-expand"></i>
                            <span>${plot.size}</span>
                        </div>
                        <div class="feature-item">
                            <i class="fas fa-compass"></i>
                            <span>${plot.exposure}</span>
                        </div>
                    </div>
                    <div class="plot-highlights">
                        ${plot.features.map(f => `<span class="highlight-tag">${f}</span>`).join('')}
                    </div>
                    <div class="plot-footer" style="display: flex; justify-content: space-between; align-items: center; padding-top: 1rem; margin-top: auto; border-top: 1px solid #e9ecef;">
                        <span class="plot-price" style="font-size: 1.5rem; font-weight: 700; color: #722f37;">${plot.price}</span>
                        <button class="btn-select-plot" style="padding: 0.625rem 1.25rem; background: #722f37; color: white; border: none; border-radius: 8px; cursor: pointer; transition: all 0.3s; font-weight: 500;" 
                                onmouseover="this.style.background='#5a242a'" 
                                onmouseout="this.style.background='#722f37'">
                            Select Plot
                        </button>
                    </div>
                </div>
            `;
            
            container.appendChild(card);
            
            // Add carousel functionality if multiple images
            if (images.length > 1) {
                // Pass the starting index so each card starts at a different image
                this.setupImageCarousel(card, images, startingIndex);
            }
        });
    }
    
    setupImageCarousel(card, images, startingIndex = 0) {
        const carousel = card.querySelector('.image-carousel');
        const img = carousel.querySelector('img');
        const indicators = card.querySelectorAll('.indicator');
        let currentIndex = startingIndex;
        let interval;
        
        // Set the initial indicator to match starting index
        indicators.forEach((ind, idx) => {
            ind.style.background = `rgba(255,255,255,${idx === startingIndex ? '1' : '0.5'})`;
        });
        
        // Function to change image
        const changeImage = (index) => {
            currentIndex = index % images.length;
            img.style.opacity = '0';
            setTimeout(() => {
                img.src = images[currentIndex];
                img.style.opacity = '1';
            }, 200);
            
            // Update indicators
            indicators.forEach((ind, idx) => {
                ind.style.background = `rgba(255,255,255,${idx === currentIndex ? '1' : '0.5'})`;
            });
            
            carousel.dataset.current = currentIndex;
        };
        
        // Auto-rotate images
        const startRotation = () => {
            interval = setInterval(() => {
                changeImage(currentIndex + 1);
            }, 3000); // Change every 3 seconds
        };
        
        const stopRotation = () => {
            if (interval) clearInterval(interval);
        };
        
        // Start rotation on hover
        card.addEventListener('mouseenter', startRotation);
        card.addEventListener('mouseleave', stopRotation);
        
        // Click on indicators
        indicators.forEach((indicator, index) => {
            indicator.addEventListener('click', (e) => {
                e.stopPropagation();
                stopRotation();
                changeImage(index);
            });
        });
    }
    
    renderMapMarkers() {
        const mapContainer = document.getElementById('map-markers');
        const canvas = document.getElementById('map-canvas');
        if (!mapContainer || !canvas) return;
        
        mapContainer.innerHTML = '';
        mapContainer.style.pointerEvents = 'auto'; // Enable click events
        
        // Define Luxembourg wine region positions (relative to canvas size)
        const regionPositions = [
            { region: 'Grevenmacher', x: 55, y: 33 },    // 440/800, 200/600
            { region: 'Remich', x: 50, y: 47 },          // 400/800, 280/600
            { region: 'Schengen', x: 45, y: 63 },        // 360/800, 380/600
            { region: 'Wormeldange', x: 52.5, y: 40 },   // 420/800, 240/600
            { region: 'Stadtbredimus', x: 47.5, y: 53 }  // 380/800, 320/600
        ];
        
        this.plots.forEach((plot, index) => {
            // Assign plot to a region position (cycle through if more plots than regions)
            const position = regionPositions[index % regionPositions.length];
            
            // Add some random offset to avoid exact overlap
            const offsetX = (Math.random() - 0.5) * 3; // ±1.5% offset
            const offsetY = (Math.random() - 0.5) * 3;
            
            const marker = document.createElement('div');
            marker.className = 'plot-marker';
            marker.dataset.plotId = plot.id;
            marker.style.cssText = `
                position: absolute;
                left: ${position.x + offsetX}%;
                top: ${position.y + offsetY}%;
                transform: translate(-50%, -50%);
                cursor: pointer;
                pointer-events: auto;
                z-index: 10;
            `;
            
            marker.innerHTML = `
                <div class="marker-pin">
                    <i class="fas fa-map-marker-alt"></i>
                </div>
                <div class="marker-label">${plot.name}</div>
                <div class="marker-tooltip">
                    <h4>${plot.name}</h4>
                    <p>${plot.region}</p>
                    <p class="price">${plot.price}</p>
                </div>
            `;
            
            mapContainer.appendChild(marker);
        });
    }
    
    selectPlot(plotId) {
        // Remove previous selections
        document.querySelectorAll('.plot-card.selected').forEach(card => {
            card.classList.remove('selected');
        });
        document.querySelectorAll('.plot-marker.selected').forEach(marker => {
            marker.classList.remove('selected');
        });
        
        // Add new selection
        const selectedCard = document.querySelector(`.plot-card[data-plot-id="${plotId}"]`);
        const selectedMarker = document.querySelector(`.plot-marker[data-plot-id="${plotId}"]`);
        
        if (selectedCard) selectedCard.classList.add('selected');
        if (selectedMarker) selectedMarker.classList.add('selected');
        
        this.selectedPlot = this.plots.find(p => p.id === plotId);
        this.showPlotDetails(this.selectedPlot);
        
        // Enable confirm button
        const confirmBtn = document.getElementById('confirm-selection');
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.classList.add('active');
        }
    }
    
    showPlotDetails(plot) {
        const panel = document.getElementById('plot-details');
        if (!panel) return;
        
        // Smooth animation
        requestAnimationFrame(() => {
            panel.classList.add('active');
        });
        
        // Update producer information
        const producerElements = {
            '#producer-name': plot.producerName,
            '#plot-region': plot.region,
            '#plot-size': plot.size,
            '#plot-elevation': plot.elevation,
            '#plot-soil': plot.soil,
            '#plot-price': plot.price
        };
        
        Object.entries(producerElements).forEach(([selector, value]) => {
            const element = document.querySelector(selector);
            if (element) element.textContent = value || '-';
        });
        
        // Update adoption plan details
        const durationEl = document.getElementById('plan-duration');
        const deliveriesEl = document.getElementById('plan-deliveries');
        if (durationEl) durationEl.textContent = `${plot.durationMonths || 12} months`;
        if (deliveriesEl) deliveriesEl.textContent = `${plot.coffretsPerYear || 4} coffrets`;
        
        // Display wine types
        const wineTypesContainer = document.getElementById('wine-types-container');
        if (wineTypesContainer) {
            const wineTypes = this.getWineTypesForPlot(plot);
            wineTypesContainer.innerHTML = `
                <div style="display: flex; flex-wrap: wrap; gap: 10px;">
                    ${wineTypes.map(type => `
                        <div style="background: linear-gradient(135deg, #722f37, #8e3a42); color: white; padding: 8px 16px; border-radius: 20px; font-size: 0.9rem;">
                            <i class="fas fa-wine-glass-alt"></i> ${type}
                        </div>
                    `).join('')}
                </div>
                ${wineTypes.length === 0 ? '<p style="color: #6c757d;">Wine types will be confirmed upon selection</p>' : ''}
            `;
        }
        
        // Display coffret options
        const coffretContainer = document.getElementById('coffret-options');
        if (coffretContainer) {
            const coffretOptions = this.getCoffretOptionsForPlot(plot);
            coffretContainer.innerHTML = `
                <div style="display: grid; gap: 15px;">
                    ${coffretOptions.map((coffret, index) => `
                        <div class="coffret-option" data-coffret-id="${index}">
                            <div style="display: flex; justify-content: space-between; align-items: start;">
                                <div style="flex: 1;">
                                    <h5 style="margin: 0 0 8px 0; color: #2c3e50; font-size: 1.1rem;">
                                        ${coffret.name}
                                    </h5>
                                    <p style="margin: 0 0 10px 0; color: #6c757d; font-size: 0.9rem; line-height: 1.4;">
                                        ${coffret.description}
                                    </p>
                                    <div style="display: flex; align-items: center; gap: 15px;">
                                        <span style="display: inline-flex; align-items: center; gap: 5px; color: #722f37;">
                                            <i class="fas fa-wine-bottle"></i>
                                            <strong>${coffret.bottles}</strong> bottles
                                        </span>
                                        ${coffret.price ? `
                                            <span style="color: #d4af37; font-weight: 600;">
                                                ${coffret.price}
                                            </span>
                                        ` : ''}
                                    </div>
                                </div>
                                <div style="display: flex; align-items: center; justify-content: center; width: 40px; height: 40px;">
                                    <div class="coffret-selector" style="width: 24px; height: 24px; border: 2px solid #dee2e6; border-radius: 50%; display: flex; align-items: center; justify-content: center; transition: all 0.3s;">
                                        <div style="width: 12px; height: 12px; border-radius: 50%; background: #d4af37; opacity: 0; transition: all 0.3s;"></div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    `).join('')}
                </div>
                ${coffretOptions.length === 0 ? '<p style="color: #6c757d; text-align: center; padding: 20px;">Coffret options will be available after selection</p>' : ''}
            `;
            
            // Add click handlers for coffret selection
            coffretContainer.querySelectorAll('.coffret-option').forEach(option => {
                option.addEventListener('click', (e) => {
                    // Remove previous selection
                    coffretContainer.querySelectorAll('.coffret-option').forEach(opt => {
                        opt.classList.remove('selected');
                        const selector = opt.querySelector('.coffret-selector div');
                        if (selector) selector.style.opacity = '0';
                    });
                    
                    // Add selection to clicked option
                    option.classList.add('selected');
                    const selector = option.querySelector('.coffret-selector div');
                    if (selector) selector.style.opacity = '1';
                    
                    // Store selected coffret
                    this.selectedCoffret = coffretOptions[parseInt(option.dataset.coffretId)];
                });
            });
            
            // Auto-select first coffret if only one available
            if (coffretOptions.length === 1) {
                const firstOption = coffretContainer.querySelector('.coffret-option');
                if (firstOption) firstOption.click();
            }
        }
        
        // Update features list with better styling
        const featuresList = document.getElementById('plot-features-list');
        if (featuresList) {
            featuresList.innerHTML = plot.features
                .map(f => `
                    <li style="padding: 8px 0; display: flex; align-items: center; gap: 10px;">
                        <i class="fas fa-check-circle" style="color: #4caf50;"></i>
                        <span>${f}</span>
                    </li>
                `)
                .join('');
        }
    }
    
    // Helper method to get wine types for a plot
    getWineTypesForPlot(plot) {
        // First check if we have wine types from the API
        if (plot.wineTypes && plot.wineTypes.length > 0) {
            return plot.wineTypes;
        }
        
        // Check grape varieties as fallback
        if (plot.grapeVarieties && plot.grapeVarieties.length > 0) {
            return plot.grapeVarieties;
        }
        
        // Use category information
        if (plot.category) {
            if (plot.category.toLowerCase().includes('red')) {
                return ['Red Wine Selection'];
            } else if (plot.category.toLowerCase().includes('white')) {
                return ['White Wine Selection'];
            } else if (plot.category.toLowerCase().includes('rosé')) {
                return ['Rosé Wine Selection'];
            }
        }
        
        // Default wine types based on region
        const wineTypesByRegion = {
            'Bordeaux': ['Merlot', 'Cabernet Sauvignon', 'Cabernet Franc'],
            'Burgundy': ['Pinot Noir', 'Chardonnay'],
            'Tuscany': ['Sangiovese', 'Merlot', 'Cabernet Sauvignon'],
            'Luxembourg': ['Riesling', 'Pinot Gris', 'Auxerrois'],
            'France': ['Mixed Red', 'Mixed White']
        };
        
        return wineTypesByRegion[plot.region] || ['Premium Wine Selection'];
    }
    
    // Helper method to get coffret options for a plot
    getCoffretOptionsForPlot(plot) {
        // First check if we have available coffrets from the API
        if (plot.availableCoffrets && plot.availableCoffrets.length > 0) {
            return plot.availableCoffrets.map(coffret => ({
                name: coffret.name || 'Wine Collection',
                description: coffret.description || `${coffret.bottles_count || 6} selected wines`,
                bottles: coffret.bottles_count || 6,
                price: coffret.price ? `€${coffret.price}` : null
            }));
        }
        
        // Check if there's a single coffret associated
        if (plot.coffret) {
            return [{
                name: plot.coffret.name || 'Selected Coffret',
                description: plot.coffret.description || `${plot.coffret.bottles_count || 6} carefully selected wines`,
                bottles: plot.coffret.bottles_count || 6,
                price: plot.coffret.price ? `€${plot.coffret.price}` : null
            }];
        }
        
        // Default coffret options based on deliveries per year
        const coffretsPerYear = plot.coffretsPerYear || 4;
        const defaultOptions = [];
        
        if (coffretsPerYear >= 2) {
            defaultOptions.push({
                name: 'Discovery Collection',
                description: '3 red wines + 3 white wines',
                bottles: 6
            });
        }
        
        if (coffretsPerYear >= 4) {
            defaultOptions.push({
                name: 'Premium Selection',
                description: '6 grand cru wines',
                bottles: 6
            });
        }
        
        if (coffretsPerYear >= 6) {
            defaultOptions.push({
                name: 'Connoisseur Choice',
                description: '12 carefully selected wines',
                bottles: 12
            });
        }
        
        return defaultOptions.length > 0 ? defaultOptions : [
            {
                name: 'Standard Collection',
                description: `${coffretsPerYear} deliveries of selected wines`,
                bottles: 6
            }
        ];
    }
    
    initializeMap() {
        const canvas = document.getElementById('map-canvas');
        if (!canvas) return;
        
        const ctx = canvas.getContext('2d');
        
        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Draw background
        ctx.fillStyle = '#f5f3f0';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        
        // Draw Luxembourg outline (simplified shape)
        ctx.strokeStyle = '#8b7766';
        ctx.lineWidth = 3;
        ctx.fillStyle = '#e8e0d5';
        
        // Luxembourg simplified outline
        ctx.beginPath();
        ctx.moveTo(350, 50);  // Top
        ctx.lineTo(450, 80);  // Top-right
        ctx.lineTo(480, 150); // Right upper
        ctx.lineTo(470, 250); // Right middle
        ctx.lineTo(450, 350); // Right lower
        ctx.lineTo(420, 420); // Bottom-right
        ctx.lineTo(350, 450); // Bottom
        ctx.lineTo(280, 430); // Bottom-left
        ctx.lineTo(250, 350); // Left lower
        ctx.lineTo(240, 250); // Left middle
        ctx.lineTo(260, 150); // Left upper
        ctx.lineTo(300, 80);  // Top-left
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        
        // Draw Moselle River (Luxembourg's wine region along the river)
        ctx.strokeStyle = '#4682b4';
        ctx.lineWidth = 4;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(470, 150); // Start from east
        ctx.quadraticCurveTo(450, 180, 430, 200);
        ctx.quadraticCurveTo(410, 230, 400, 260);
        ctx.quadraticCurveTo(390, 290, 380, 320);
        ctx.quadraticCurveTo(370, 350, 350, 380);
        ctx.quadraticCurveTo(330, 400, 310, 420);
        ctx.stroke();
        
        // Draw wine regions along the Moselle
        const wineRegions = [
            { name: 'Grevenmacher', x: 440, y: 200, radius: 25 },
            { name: 'Remich', x: 400, y: 280, radius: 30 },
            { name: 'Schengen', x: 360, y: 380, radius: 25 },
            { name: 'Wormeldange', x: 420, y: 240, radius: 20 },
            { name: 'Stadtbredimus', x: 380, y: 320, radius: 20 }
        ];
        
        // Draw wine region areas
        ctx.fillStyle = 'rgba(139, 195, 74, 0.2)';
        ctx.strokeStyle = '#689f38';
        ctx.lineWidth = 2;
        
        wineRegions.forEach(region => {
            ctx.beginPath();
            ctx.arc(region.x, region.y, region.radius, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
        });
        
        // Add region labels
        ctx.fillStyle = '#2d4a2b';
        ctx.font = 'bold 12px Arial';
        ctx.textAlign = 'center';
        
        wineRegions.forEach(region => {
            ctx.fillText(region.name, region.x, region.y - region.radius - 5);
        });
        
        // Add title
        ctx.fillStyle = '#722f37';
        ctx.font = 'bold 18px Arial';
        ctx.textAlign = 'center';
        ctx.fillText('Luxembourg Wine Regions - Moselle Valley', canvas.width / 2, 30);
        
        // Add compass rose
        const compassX = 60;
        const compassY = 60;
        ctx.save();
        ctx.translate(compassX, compassY);
        
        // Outer circle
        ctx.beginPath();
        ctx.arc(0, 0, 25, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
        ctx.fill();
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 2;
        ctx.stroke();
        
        // North arrow
        ctx.beginPath();
        ctx.moveTo(0, -20);
        ctx.lineTo(-5, -5);
        ctx.lineTo(0, -10);
        ctx.lineTo(5, -5);
        ctx.closePath();
        ctx.fillStyle = '#722f37';
        ctx.fill();
        
        // Other directions
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(0, 20);
        ctx.lineTo(0, 10);
        ctx.moveTo(-20, 0);
        ctx.lineTo(-10, 0);
        ctx.moveTo(20, 0);
        ctx.lineTo(10, 0);
        ctx.stroke();
        
        // Labels
        ctx.fillStyle = '#333';
        ctx.font = 'bold 12px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('N', 0, -30);
        
        ctx.restore();
        
        // Add legend
        ctx.fillStyle = '#f8f8f8';
        ctx.fillRect(20, canvas.height - 120, 150, 100);
        ctx.strokeStyle = '#ccc';
        ctx.lineWidth = 1;
        ctx.strokeRect(20, canvas.height - 120, 150, 100);
        
        ctx.fillStyle = '#333';
        ctx.font = 'bold 12px Arial';
        ctx.textAlign = 'left';
        ctx.fillText('Legend:', 30, canvas.height - 100);
        
        // River legend
        ctx.strokeStyle = '#4682b4';
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(30, canvas.height - 80);
        ctx.lineTo(60, canvas.height - 80);
        ctx.stroke();
        ctx.fillStyle = '#333';
        ctx.font = '11px Arial';
        ctx.fillText('Moselle River', 70, canvas.height - 76);
        
        // Wine region legend
        ctx.fillStyle = 'rgba(139, 195, 74, 0.3)';
        ctx.strokeStyle = '#689f38';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(45, canvas.height - 55, 10, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = '#333';
        ctx.fillText('Wine Region', 70, canvas.height - 51);
        
        // Vineyard plot legend
        ctx.fillStyle = '#d4af37';
        ctx.beginPath();
        ctx.arc(45, canvas.height - 30, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#333';
        ctx.fillText('Vineyard Plot', 70, canvas.height - 26);
    }
    
    showGridView() {
        this.mapView = false;
        const plotsGrid = document.getElementById('plots-grid');
        const mapView = document.getElementById('map-view');
        
        if (plotsGrid) plotsGrid.style.display = 'grid';  // Use 'grid' instead of 'block'
        if (mapView) mapView.style.display = 'none';
        
        document.getElementById('grid-view-btn')?.classList.add('active');
        document.getElementById('map-view-btn')?.classList.remove('active');
    }
    
    showMapView() {
        this.mapView = true;
        const plotsGrid = document.getElementById('plots-grid');
        const mapView = document.getElementById('map-view');
        
        if (plotsGrid) plotsGrid.style.display = 'none';
        if (mapView) {
            mapView.style.display = 'block';
            // Re-initialize map when showing
            setTimeout(() => this.initializeMap(), 100);
        }
        
        document.getElementById('map-view-btn')?.classList.add('active');
        document.getElementById('grid-view-btn')?.classList.remove('active');
    }
    
    zoomMap(factor) {
        const canvas = document.getElementById('map-canvas');
        if (!canvas) return;
        
        canvas.style.transform = `scale(${factor})`;
    }
    
    resetMap() {
        const canvas = document.getElementById('map-canvas');
        if (!canvas) return;
        
        canvas.style.transform = 'scale(1)';
    }
    
    confirmSelection() {
        if (!this.selectedPlot) return;
        
        // Save selection to localStorage
        localStorage.setItem('selectedPlot', JSON.stringify(this.selectedPlot));
        
        // Show confirmation message
        this.showNotification(`Plot "${this.selectedPlot.name}" selected successfully!`);
        
        // Optionally redirect to next step
        setTimeout(() => {
            // window.location.href = '/vinsdelux/journey/personalize-wine/';
        }, 2000);
    }
    
    showNotification(message) {
        const notification = document.createElement('div');
        notification.className = 'selection-notification';
        notification.textContent = message;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #4caf50;
            color: white;
            padding: 15px 25px;
            border-radius: 5px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            z-index: 1000;
            animation: slideIn 0.3s ease;
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => notification.remove(), 3000);
    }
    
    closeDetailsPanel() {
        const panel = document.getElementById('plot-details');
        if (panel) {
            panel.classList.remove('active');
        }
        
        // Enable scrolling on body again
        document.body.style.overflow = '';
        
        // Also deselect any selected plots
        document.querySelectorAll('.plot-card.selected').forEach(card => {
            card.classList.remove('selected');
        });
        document.querySelectorAll('.plot-marker.selected').forEach(marker => {
            marker.classList.remove('selected');
        });
        
        this.selectedPlot = null;
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.plotSelector = new StandalonePlotSelector();
});