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
            // Try different URL patterns based on the current page URL
            const currentPath = window.location.pathname;
            let apiUrl;
            
            // Check if we're on a path with language prefix
            const pathParts = currentPath.split('/').filter(p => p);
            const hasLangPrefix = pathParts[0] && /^[a-z]{2}$/i.test(pathParts[0]);
            
            if (hasLangPrefix) {
                // Use the existing language prefix
                apiUrl = `/${pathParts[0]}/vinsdelux/api/adoption-plans/`;
            } else if (currentPath.includes('/journey/plot-selection')) {
                // Special case for journey URLs without language prefix
                // Try without language prefix first
                apiUrl = '/vinsdelux/api/adoption-plans/';
            } else {
                // Default to English
                apiUrl = '/en/vinsdelux/api/adoption-plans/';
            }
            
            console.log('Fetching adoption plans from:', apiUrl);
            
            // Try the first URL
            let response = await fetch(apiUrl);
            
            // If that fails with 404, try with /en/ prefix as fallback
            if (response.status === 404 && !apiUrl.includes('/en/')) {
                console.log('First attempt failed, trying with /en/ prefix');
                apiUrl = '/en/vinsdelux/api/adoption-plans/';
                response = await fetch(apiUrl);
            }
            
            // If still failing, try without any prefix
            if (response.status === 404 && apiUrl !== '/vinsdelux/api/adoption-plans/') {
                console.log('Second attempt failed, trying without language prefix');
                apiUrl = '/vinsdelux/api/adoption-plans/';
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
                coffretsPerYear: plan.coffrets_per_year
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
        const container = document.querySelector('.plot-cards-container');
        if (!container) return;
        
        container.innerHTML = '';
        
        this.plots.forEach(plot => {
            const card = document.createElement('div');
            card.className = 'plot-card';
            card.dataset.plotId = plot.id;
            
            card.innerHTML = `
                <div class="card-image">
                    <img src="/static/images/journey/step_01.png" alt="${plot.name}">
                    <div class="card-badge">${plot.region}</div>
                </div>
                <div class="card-content">
                    <h3>${plot.name}</h3>
                    <p class="plot-description">${plot.description}</p>
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
                    <div class="plot-footer">
                        <span class="plot-price">${plot.price}</span>
                        <button class="btn-select-plot">Select Plot</button>
                    </div>
                </div>
            `;
            
            container.appendChild(card);
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
        
        panel.classList.add('active');
        
        const elements = {
            '#plot-name': plot.name,
            '#plot-size': plot.size,
            '#plot-elevation': plot.elevation,
            '#plot-exposure': plot.exposure,
            '#plot-soil': plot.soil,
            '#plot-region': plot.region,
            '#plot-price': plot.price
        };
        
        Object.entries(elements).forEach(([selector, value]) => {
            const element = document.querySelector(selector);
            if (element) element.textContent = value;
        });
        
        // Update features list
        const featuresList = document.getElementById('plot-features-list');
        if (featuresList) {
            featuresList.innerHTML = plot.features
                .map(f => `<li><i class="fas fa-check"></i> ${f}</li>`)
                .join('');
        }
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
        
        // Add compass
        ctx.strokeStyle = '#666';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(50, 50);
        ctx.lineTo(50, 80);
        ctx.moveTo(35, 65);
        ctx.lineTo(65, 65);
        ctx.stroke();
        ctx.fillStyle = '#666';
        ctx.font = 'bold 14px Arial';
        ctx.textAlign = 'center';
        ctx.fillText('N', 50, 45);
        
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
        document.querySelector('.plot-cards-container')?.classList.remove('hidden');
        document.querySelector('.vineyard-map')?.classList.add('hidden');
        
        document.getElementById('grid-view-btn')?.classList.add('active');
        document.getElementById('map-view-btn')?.classList.remove('active');
    }
    
    showMapView() {
        this.mapView = true;
        document.querySelector('.plot-cards-container')?.classList.add('hidden');
        document.querySelector('.vineyard-map')?.classList.remove('hidden');
        
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