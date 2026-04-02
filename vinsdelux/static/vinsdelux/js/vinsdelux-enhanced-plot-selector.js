/**
 * Enhanced Plot Selector with SVG Map
 * Interactive vineyard plot selection for VinsDelux
 */

class EnhancedPlotSelector {
    constructor() {
        this.selectedPlot = null;
        this.plots = [];
        this.mapScale = 1;
        this.mapTranslateX = 0;
        this.mapTranslateY = 0;
        this.init();
    }
    
    async init() {
        await this.loadAdoptionPlans();
        this.setupEventListeners();
        this.generatePlotGrid();
        this.renderPlotMarkers();
    }
    
    async loadAdoptionPlans() {
        try {
            // Use the API URL passed from Django template
            const url = typeof apiUrl !== 'undefined' ? apiUrl : '/vinsdelux/api/adoption-plans/';
            console.log('Loading adoption plans from:', url);
            
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            console.log('Loaded adoption plans:', data);
            
            // Transform API data to plot format
            this.plots = data.adoption_plans.map((plan, index) => ({
                id: plan.id,
                name: plan.name,
                producer: plan.producer.name,
                region: plan.producer.region || 'Moselle',
                price: plan.price,
                elevation: plan.producer.elevation || 'Not specified',
                soilType: plan.producer.soil_type || 'Not specified',
                sunExposure: plan.producer.sun_exposure || 'Not specified',
                wineType: plan.category || 'Mixed',
                features: this.extractFeatures(plan),
                benefits: this.extractBenefits(plan),
                description: plan.description,
                // Position on map - distribute plots across regions
                mapX: this.calculateMapX(index),
                mapY: this.calculateMapY(index),
                regionZone: this.assignRegionZone(index),
                available: true
            }));
            
        } catch (error) {
            console.error('Failed to load adoption plans:', error);
            // Use sample data as fallback
            this.plots = this.getSamplePlots();
        }
    }
    
    extractFeatures(plan) {
        const features = [];
        if (plan.features.includes_visit) features.push('Vineyard Visit');
        if (plan.features.includes_medallion) features.push('Personalized Medallion');
        if (plan.features.includes_club_membership) features.push('Club Membership');
        return features;
    }
    
    extractBenefits(plan) {
        const benefits = [];
        benefits.push(`${plan.duration_months} months duration`);
        benefits.push(`${plan.coffrets_per_year} coffrets per year`);
        if (plan.visit_details) benefits.push(plan.visit_details);
        if (plan.welcome_kit) benefits.push('Welcome kit included');
        return benefits;
    }
    
    calculateMapX(index) {
        // Distribute plots across all 12 cantons
        const cantons = ['clervaux', 'wiltz', 'vianden', 'diekirch', 'redange', 
                        'mersch', 'echternach', 'grevenmacher', 'capellen', 
                        'luxembourg', 'esch', 'remich'];
        const canton = cantons[index % 12];
        
        // Canton center positions (adjusted for new SVG viewBox)
        const positions = {
            'clervaux': 150,
            'wiltz': 90,
            'vianden': 250,
            'diekirch': 175,
            'redange': 85,
            'mersch': 170,
            'echternach': 315,
            'grevenmacher': 245,
            'capellen': 75,
            'luxembourg': 165,
            'esch': 100,
            'remich': 230
        };
        
        // Add small random offset for variation
        const offset = (Math.random() - 0.5) * 20;
        return positions[canton] + offset;
    }
    
    calculateMapY(index) {
        // Distribute plots across all 12 cantons
        const cantons = ['clervaux', 'wiltz', 'vianden', 'diekirch', 'redange', 
                        'mersch', 'echternach', 'grevenmacher', 'capellen', 
                        'luxembourg', 'esch', 'remich'];
        const canton = cantons[index % 12];
        
        // Canton center Y positions
        const positions = {
            'clervaux': 60,
            'wiltz': 120,
            'vianden': 120,
            'diekirch': 140,
            'redange': 185,
            'mersch': 215,
            'echternach': 205,
            'grevenmacher': 230,
            'capellen': 250,
            'luxembourg': 290,
            'esch': 340,
            'remich': 365
        };
        
        // Add small random offset for variation
        const offset = (Math.random() - 0.5) * 20;
        return positions[canton] + offset;
    }
    
    assignRegionZone(index) {
        const cantons = ['clervaux', 'wiltz', 'vianden', 'diekirch', 'redange', 
                        'mersch', 'echternach', 'grevenmacher', 'capellen', 
                        'luxembourg', 'esch', 'remich'];
        return cantons[index % 12];
    }
    
    getSamplePlots() {
        return [
            {
                id: 1,
                name: 'Domaine Wormeldange Plot A',
                producer: 'Domaine Wormeldange',
                region: 'Canton Grevenmacher',
                price: 2500,
                elevation: '180m',
                soilType: 'Shell limestone',
                sunExposure: 'South-facing',
                wineType: 'Riesling',
                features: ['Vineyard Visit', 'Personalized Medallion', 'Club Membership'],
                benefits: ['12 months duration', '4 coffrets per year', 'Annual tasting event'],
                mapX: 485,
                mapY: 290,
                regionZone: 'grevenmacher',
                available: true
            },
            {
                id: 2,
                name: 'Caves St. Martin Plot B',
                producer: 'Caves St. Martin',
                region: 'Canton Remich',
                price: 1800,
                elevation: '150m',
                soilType: 'Keuper marl',
                sunExposure: 'Southeast-facing',
                wineType: 'Pinot Gris',
                features: ['Vineyard Visit', 'Personalized Medallion'],
                benefits: ['12 months duration', '3 coffrets per year'],
                mapX: 490,
                mapY: 500,
                regionZone: 'remich',
                available: true
            },
            {
                id: 3,
                name: 'Domaine Schengen Plot C',
                producer: 'Domaine Viticole Schengen',
                region: 'Schengen',
                price: 2200,
                elevation: '140m',
                soilType: 'Muschelkalk',
                sunExposure: 'Southwest-facing',
                wineType: 'Auxerrois',
                features: ['Club Membership'],
                benefits: ['12 months duration', '6 coffrets per year', 'Welcome kit included'],
                mapX: 470,
                mapY: 650,
                regionZone: 'schengen',
                available: true
            }
        ];
    }
    
    setupEventListeners() {
        // Map controls
        document.getElementById('zoomIn')?.addEventListener('click', () => this.zoomMap(1.2));
        document.getElementById('zoomOut')?.addEventListener('click', () => this.zoomMap(0.8));
        document.getElementById('resetView')?.addEventListener('click', () => this.resetMap());
        
        // Panel controls
        document.getElementById('closePanel')?.addEventListener('click', () => this.closeDetailPanel());
        document.getElementById('selectPlotBtn')?.addEventListener('click', () => this.selectCurrentPlot());
        
        // Modal controls
        document.getElementById('closeModal')?.addEventListener('click', () => this.closeModal());
        
        // Canton click handlers for all 12 Luxembourg cantons
        const cantons = ['clervaux', 'wiltz', 'vianden', 'diekirch', 'redange', 
                        'mersch', 'echternach', 'grevenmacher', 'capellen', 
                        'luxembourg', 'esch', 'remich'];
        
        cantons.forEach(canton => {
            const element = document.getElementById(`canton-${canton}`);
            if (element) {
                element.addEventListener('click', () => this.selectCanton(canton));
            }
        });
    }
    
    generatePlotGrid() {
        const gridContainer = document.getElementById('plotGrid');
        if (!gridContainer) return;
        
        // Clear existing grid
        gridContainer.innerHTML = '';
        
        // Create grid cells (simplified for demonstration)
        const gridSize = 50;
        const cols = 16;
        const rows = 20;
        
        for (let row = 0; row < rows; row++) {
            for (let col = 0; col < cols; col++) {
                const cell = document.createElement('div');
                cell.className = 'plot-grid-cell';
                cell.style.width = `${gridSize}px`;
                cell.style.height = `${gridSize}px`;
                cell.style.left = `${col * gridSize}px`;
                cell.style.top = `${row * gridSize}px`;
                cell.dataset.row = row;
                cell.dataset.col = col;
                
                // Mark some cells as occupied
                if (this.isCellOccupied(row, col)) {
                    cell.classList.add('occupied');
                }
                
                gridContainer.appendChild(cell);
            }
        }
    }
    
    isCellOccupied(row, col) {
        // Check if a plot exists at this grid position
        return this.plots.some(plot => {
            const plotCol = Math.floor(plot.mapX / 50);
            const plotRow = Math.floor(plot.mapY / 50);
            return plotCol === col && plotRow === row;
        });
    }
    
    renderPlotMarkers() {
        const markersContainer = document.getElementById('plotMarkers');
        if (!markersContainer) return;
        
        // Clear existing markers
        markersContainer.innerHTML = '';
        
        // Create markers for each plot
        this.plots.forEach(plot => {
            const marker = document.createElement('div');
            marker.className = 'plot-marker';
            if (plot.available) {
                marker.classList.add('available');
            }
            marker.style.left = `${plot.mapX}px`;
            marker.style.top = `${plot.mapY}px`;
            marker.dataset.plotId = plot.id;
            
            marker.innerHTML = `
                <i class="fas fa-map-marker-alt"></i>
                <div class="plot-marker-label">${plot.name}</div>
            `;
            
            marker.addEventListener('click', () => this.showPlotDetails(plot));
            marker.addEventListener('mouseenter', (e) => this.showTooltip(e, plot));
            marker.addEventListener('mouseleave', () => this.hideTooltip());
            
            markersContainer.appendChild(marker);
        });
    }
    
    showPlotDetails(plot) {
        this.selectedPlot = plot;
        const panel = document.getElementById('plotDetailPanel');
        if (!panel) return;
        
        // Update panel content
        document.getElementById('plotTitle').textContent = plot.name;
        document.getElementById('plotStatus').textContent = plot.available ? 'Available' : 'Unavailable';
        document.getElementById('producerName').textContent = plot.producer;
        document.getElementById('producerRegion').textContent = plot.region;
        document.getElementById('elevation').textContent = plot.elevation;
        document.getElementById('soilType').textContent = plot.soilType;
        document.getElementById('sunExposure').textContent = plot.sunExposure;
        document.getElementById('wineType').textContent = plot.wineType;
        document.getElementById('plotPrice').textContent = `€${plot.price}`;
        
        // Update features
        const featuresList = document.getElementById('featuresList');
        featuresList.innerHTML = plot.features.map(feature => 
            `<span class="feature-tag">${feature}</span>`
        ).join('');
        
        // Update benefits
        const benefitsList = document.getElementById('planBenefits');
        benefitsList.innerHTML = plot.benefits.map(benefit => 
            `<div class="benefit-item">
                <i class="fas fa-check-circle"></i>
                <span>${benefit}</span>
            </div>`
        ).join('');
        
        // Update producer image (placeholder)
        const producerImage = document.getElementById('producerImage');
        producerImage.src = `/static/vinsdelux/images/vineyard-defaults/vineyard_01.jpg`;
        
        // Show panel
        panel.classList.add('active');
        
        // Highlight selected marker
        document.querySelectorAll('.plot-marker').forEach(marker => {
            marker.classList.remove('selected');
        });
        document.querySelector(`.plot-marker[data-plot-id="${plot.id}"]`)?.classList.add('selected');
    }
    
    closeDetailPanel() {
        const panel = document.getElementById('plotDetailPanel');
        if (panel) {
            panel.classList.remove('active');
        }
        
        // Remove marker selection
        document.querySelectorAll('.plot-marker').forEach(marker => {
            marker.classList.remove('selected');
        });
        
        this.selectedPlot = null;
    }
    
    selectCurrentPlot() {
        if (!this.selectedPlot) return;
        
        // Save selection
        localStorage.setItem('selectedPlot', JSON.stringify(this.selectedPlot));
        
        // Show confirmation
        this.showNotification(`Plot "${this.selectedPlot.name}" has been selected!`);
        
        // Optionally redirect or trigger next step
        setTimeout(() => {
            // window.location.href = '/vinsdelux/journey/personalize-wine/';
        }, 2000);
    }
    
    showTooltip(event, plot) {
        const tooltip = document.getElementById('plotTooltip');
        if (!tooltip) return;
        
        document.getElementById('tooltipTitle').textContent = plot.name;
        document.getElementById('tooltipInfo').textContent = `${plot.region} - €${plot.price}`;
        
        tooltip.style.left = `${event.pageX + 10}px`;
        tooltip.style.top = `${event.pageY - 30}px`;
        tooltip.classList.add('visible');
    }
    
    hideTooltip() {
        const tooltip = document.getElementById('plotTooltip');
        if (tooltip) {
            tooltip.classList.remove('visible');
        }
    }
    
    zoomMap(factor) {
        this.mapScale *= factor;
        this.mapScale = Math.max(0.5, Math.min(2, this.mapScale));
        this.updateMapTransform();
    }
    
    resetMap() {
        this.mapScale = 1;
        this.mapTranslateX = 0;
        this.mapTranslateY = 0;
        this.updateMapTransform();
    }
    
    updateMapTransform() {
        const mapContainer = document.getElementById('mapContainer');
        if (mapContainer) {
            mapContainer.style.transform = `scale(${this.mapScale}) translate(${this.mapTranslateX}px, ${this.mapTranslateY}px)`;
        }
    }
    
    selectCanton(canton) {
        // Show plots for selected canton
        const cantonPlots = this.plots.filter(p => p.regionZone === canton);
        
        if (cantonPlots.length > 0) {
            // Show first plot from this canton
            this.showPlotDetails(cantonPlots[0]);
        } else {
            // Show info that no plots available in this canton
            this.showCantonInfo(canton);
        }
        
        // Highlight the selected canton
        document.querySelectorAll('.canton-group').forEach(g => {
            g.classList.remove('selected');
        });
        document.getElementById(`canton-${canton}`)?.classList.add('selected');
    }
    
    showCantonInfo(canton) {
        // Show information about the canton
        const cantonNames = {
            'clervaux': 'Clervaux',
            'wiltz': 'Wiltz',
            'vianden': 'Vianden',
            'diekirch': 'Diekirch',
            'redange': 'Redange',
            'mersch': 'Mersch',
            'echternach': 'Echternach',
            'grevenmacher': 'Grevenmacher',
            'capellen': 'Capellen',
            'luxembourg': 'Luxembourg',
            'esch': 'Esch-sur-Alzette',
            'remich': 'Remich'
        };
        
        this.showNotification(`Selected canton: ${cantonNames[canton]}. Wine plots coming soon!`);
    }
    
    highlightRegion(region) {
        // Highlight plots in specific region
        document.querySelectorAll('.plot-marker').forEach(marker => {
            const plot = this.plots.find(p => p.id == marker.dataset.plotId);
            if (plot && plot.regionZone === region) {
                marker.classList.add('highlighted');
            } else {
                marker.classList.remove('highlighted');
            }
        });
    }
    
    closeModal() {
        const modal = document.getElementById('producerMapModal');
        if (modal) {
            modal.classList.remove('active');
        }
    }
    
    showNotification(message) {
        const notification = document.createElement('div');
        notification.className = 'notification-toast';
        notification.textContent = message;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #4caf50;
            color: white;
            padding: 15px 25px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            z-index: 1000;
            animation: slideIn 0.3s ease;
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Check if we're on the enhanced plot selector page
    if (document.getElementById('wineRegionMap')) {
        window.enhancedPlotSelector = new EnhancedPlotSelector();
    }
});