/**
 * VineyardMap Class - Interactive Leaflet Map for Luxembourg Wine Regions
 * Handles vineyard plot visualization and interaction
 */

class VineyardMap {
    constructor(containerId = 'vineyard-map', options = {}) {
        this.containerId = containerId;
        this.map = null;
        this.plotLayers = [];
        this.selectedPlots = new Set();
        this.plotBoundaries = new Map();
        this.clusterGroup = null;
        
        // Luxembourg coordinates (center of country)
        this.defaultCenter = [49.6116, 6.1319];
        this.defaultZoom = 9;
        
        // Wine region colors
        this.regionColors = {
            'moselle': '#D4AF37',      // Gold for Moselle wines
            'luxembourg': '#722F37',    // Wine red
            'clervaux': '#4FC3F7',     // Light blue
            'wiltz': '#E91E63',        // Pink
            'vianden': '#FF9800',      // Orange
            'diekirch': '#FFA726',     // Light orange
            'redange': '#AB47BC',      // Purple
            'mersch': '#66BB6A',       // Green
            'echternach': '#42A5F5',   // Blue
            'grevenmacher': '#9CCC65', // Light green
            'capellen': '#EF5350',     // Red
            'esch': '#29B6F6',         // Cyan
            'remich': '#CE93D8'        // Light purple
        };
        
        this.options = {
            enableClustering: true,
            maxZoom: 18,
            minZoom: 8,
            hoverColor: '#D4AF37',
            selectedColor: '#722F37',
            ...options
        };
        
        this.init();
    }
    
    async init() {
        try {
            // Check if container exists
            if (!document.getElementById(this.containerId)) {
                console.warn(`VineyardMap: Container ${this.containerId} not found`);
                return;
            }
            
            await this.initializeMap();
            await this.loadPlotBoundaries();
            this.setupEventListeners();
            
            console.log('VineyardMap initialized successfully');
        } catch (error) {
            console.error('Failed to initialize VineyardMap:', error);
        }
    }
    
    async initializeMap() {
        // Initialize Leaflet map
        this.map = L.map(this.containerId, {
            center: this.defaultCenter,
            zoom: this.defaultZoom,
            maxZoom: this.options.maxZoom,
            minZoom: this.options.minZoom,
            zoomControl: true,
            attributionControl: false
        });
        
        // Add custom tile layer with wine-themed styling
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            opacity: 0.7
        }).addTo(this.map);
        
        // Add custom attribution
        L.control.attribution({
            position: 'bottomright',
            prefix: 'VinsDelux'
        }).addTo(this.map);
        
        // Initialize clustering if enabled
        if (this.options.enableClustering && window.L.markerClusterGroup) {
            this.clusterGroup = L.markerClusterGroup({
                maxClusterRadius: 50,
                spiderfyOnMaxZoom: true,
                showCoverageOnHover: false,
                zoomToBoundsOnClick: true
            });
            this.map.addLayer(this.clusterGroup);
        }
    }
    
    async loadPlotBoundaries() {
        try {
            // Load plot data from API
            const response = await fetch('/api/plots/availability/');
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const plotData = await response.json();
            this.renderPlotBoundaries(plotData.plots || []);
            
        } catch (error) {
            console.error('Failed to load plot boundaries:', error);
            // Fallback to sample data
            this.renderSamplePlots();
        }
    }
    
    renderPlotBoundaries(plots) {
        plots.forEach((plot, index) => {
            this.createPlotBoundary(plot, index);
        });
    }
    
    createPlotBoundary(plot, index) {
        // Generate boundary coordinates for demonstration
        const coords = this.generatePlotBoundary(plot, index);
        const region = plot.region || 'moselle';
        const color = this.regionColors[region.toLowerCase()] || this.regionColors.moselle;
        
        // Create polygon for plot boundary
        const boundary = L.polygon(coords, {
            color: color,
            fillColor: color,
            fillOpacity: 0.3,
            weight: 2,
            opacity: 0.8
        });
        
        // Create custom wine-themed marker
        const markerIcon = this.createWineMarker(color);
        const marker = L.marker([coords[0][0], coords[0][1]], { 
            icon: markerIcon
        });
        
        // Add popup with plot details
        const popupContent = this.createPopupContent(plot);
        boundary.bindPopup(popupContent);
        marker.bindPopup(popupContent);
        
        // Add hover effects
        boundary.on('mouseover', (e) => {
            this.highlightPlot(e.target, plot);
        });
        
        boundary.on('mouseout', (e) => {
            this.unhighlightPlot(e.target, plot);
        });
        
        // Add click handlers
        boundary.on('click', (e) => {
            this.selectPlot(plot, e);
        });
        
        marker.on('click', (e) => {
            this.selectPlot(plot, e);
        });
        
        // Store plot data
        this.plotBoundaries.set(plot.id, {
            boundary,
            marker,
            data: plot,
            coords
        });
        
        // Add to map or cluster group
        if (this.clusterGroup) {
            this.clusterGroup.addLayer(marker);
        } else {
            marker.addTo(this.map);
        }
        
        boundary.addTo(this.map);
        this.plotLayers.push(boundary);
    }
    
    generatePlotBoundary(plot, index) {
        // Generate realistic plot boundaries based on Luxembourg wine regions
        const baseCoords = this.getRegionCoordinates(plot.region, index);
        const size = 0.002; // Approximate plot size in degrees
        
        return [
            [baseCoords[0], baseCoords[1]],
            [baseCoords[0] + size, baseCoords[1]],
            [baseCoords[0] + size, baseCoords[1] + size],
            [baseCoords[0], baseCoords[1] + size],
            [baseCoords[0], baseCoords[1]]
        ];
    }
    
    getRegionCoordinates(region, index) {
        // Real coordinates for Luxembourg wine regions
        const regionCoords = {
            'moselle': [49.5441, 6.3750],      // Moselle Valley
            'remich': [49.5441, 6.3667],       // Remich
            'grevenmacher': [49.6811, 6.4417], // Grevenmacher
            'wormeldange': [49.6097, 6.4031],  // Wormeldange
            'ahn': [49.5919, 6.3478],          // Ahn
            'machtum': [49.6194, 6.3889],      // Machtum
            'luxembourg': [49.6116, 6.1319]    // Luxembourg city area
        };
        
        let baseCoords = regionCoords[region?.toLowerCase()] || regionCoords.moselle;
        
        // Add variation for multiple plots in same region
        const offset = (index % 10) * 0.01;
        const variation = [(Math.random() - 0.5) * 0.02, (Math.random() - 0.5) * 0.02];
        
        return [
            baseCoords[0] + offset + variation[0],
            baseCoords[1] + variation[1]
        ];
    }
    
    createWineMarker(color) {
        // Create custom wine-themed marker icon
        return L.divIcon({
            className: 'wine-plot-marker',
            html: `
                <div class="marker-icon" style="background-color: ${color}; border-color: ${color};">
                    <i class="fas fa-wine-bottle"></i>
                </div>
            `,
            iconSize: [30, 40],
            iconAnchor: [15, 40],
            popupAnchor: [0, -40]
        });
    }
    
    createPopupContent(plot) {
        const features = plot.features ? plot.features.join(', ') : 'Standard features';
        const price = plot.price ? `€${plot.price}` : 'Price on request';
        
        return `
            <div class="plot-popup-content">
                <h4 class="plot-title">${plot.name || 'Wine Plot'}</h4>
                <div class="plot-details">
                    <p><strong>Producer:</strong> ${plot.producer || 'Premium Vineyard'}</p>
                    <p><strong>Region:</strong> ${plot.region || 'Moselle'}</p>
                    <p><strong>Wine Type:</strong> ${plot.wine_type || 'Mixed'}</p>
                    <p><strong>Price:</strong> ${price}</p>
                    <p><strong>Features:</strong> ${features}</p>
                </div>
                <div class="plot-actions">
                    <button class="btn-view-details" data-plot-id="${plot.id}">View Details</button>
                    <button class="btn-select-plot" data-plot-id="${plot.id}">Select Plot</button>
                </div>
            </div>
        `;
    }
    
    highlightPlot(layer, plot) {
        // Gold highlight effect
        layer.setStyle({
            color: this.options.hoverColor,
            fillColor: this.options.hoverColor,
            fillOpacity: 0.6,
            weight: 3
        });
        
        // Bring to front
        layer.bringToFront();
        
        // Trigger custom event
        this.dispatchEvent('plotHover', { plot, layer });
    }
    
    unhighlightPlot(layer, plot) {
        if (!this.selectedPlots.has(plot.id)) {
            const region = plot.region || 'moselle';
            const color = this.regionColors[region.toLowerCase()] || this.regionColors.moselle;
            
            layer.setStyle({
                color: color,
                fillColor: color,
                fillOpacity: 0.3,
                weight: 2
            });
        }
        
        // Trigger custom event
        this.dispatchEvent('plotUnhover', { plot, layer });
    }
    
    selectPlot(plot, event) {
        if (this.selectedPlots.has(plot.id)) {
            this.deselectPlot(plot.id);
        } else {
            this.addPlotToSelection(plot);
        }
    }
    
    addPlotToSelection(plot) {
        this.selectedPlots.add(plot.id);
        
        // Update visual state
        const plotData = this.plotBoundaries.get(plot.id);
        if (plotData) {
            plotData.boundary.setStyle({
                color: this.options.selectedColor,
                fillColor: this.options.selectedColor,
                fillOpacity: 0.7,
                weight: 4
            });
        }
        
        // Trigger custom event
        this.dispatchEvent('plotSelected', { 
            plot, 
            selectedPlots: Array.from(this.selectedPlots)
        });
    }
    
    deselectPlot(plotId) {
        this.selectedPlots.delete(plotId);
        
        // Update visual state
        const plotData = this.plotBoundaries.get(plotId);
        if (plotData) {
            const region = plotData.data.region || 'moselle';
            const color = this.regionColors[region.toLowerCase()] || this.regionColors.moselle;
            
            plotData.boundary.setStyle({
                color: color,
                fillColor: color,
                fillOpacity: 0.3,
                weight: 2
            });
        }
        
        // Trigger custom event
        this.dispatchEvent('plotDeselected', { 
            plotId, 
            selectedPlots: Array.from(this.selectedPlots)
        });
    }
    
    centerOnRegion(region) {
        const coords = this.getRegionCoordinates(region, 0);
        this.map.setView(coords, 12);
    }
    
    fitToBounds() {
        if (this.plotLayers.length > 0) {
            const group = new L.featureGroup(this.plotLayers);
            this.map.fitBounds(group.getBounds().pad(0.1));
        }
    }
    
    getSelectedPlots() {
        return Array.from(this.selectedPlots).map(id => {
            const plotData = this.plotBoundaries.get(id);
            return plotData ? plotData.data : null;
        }).filter(plot => plot !== null);
    }
    
    clearSelection() {
        this.selectedPlots.forEach(plotId => {
            this.deselectPlot(plotId);
        });
        this.selectedPlots.clear();
    }
    
    setupEventListeners() {
        // Handle popup button clicks
        this.map.on('popupopen', (e) => {
            const popup = e.popup.getElement();
            
            // View details button
            const viewBtn = popup.querySelector('.btn-view-details');
            if (viewBtn) {
                viewBtn.addEventListener('click', (event) => {
                    const plotId = event.target.dataset.plotId;
                    const plotData = this.plotBoundaries.get(parseInt(plotId));
                    if (plotData) {
                        this.dispatchEvent('plotDetailsRequested', { plot: plotData.data });
                    }
                });
            }
            
            // Select plot button
            const selectBtn = popup.querySelector('.btn-select-plot');
            if (selectBtn) {
                selectBtn.addEventListener('click', (event) => {
                    const plotId = event.target.dataset.plotId;
                    const plotData = this.plotBoundaries.get(parseInt(plotId));
                    if (plotData) {
                        this.selectPlot(plotData.data);
                    }
                });
            }
        });
    }
    
    renderSamplePlots() {
        // Fallback sample plots for demonstration
        const samplePlots = [
            {
                id: 1,
                name: 'Domaine Wormeldange - Plot A1',
                producer: 'Domaine Wormeldange',
                region: 'Moselle',
                wine_type: 'Riesling',
                price: 2500,
                features: ['Vineyard Visit', 'Personalized Label', 'Club Access']
            },
            {
                id: 2,
                name: 'Caves St. Martin - Terrasse Plot',
                producer: 'Caves St. Martin',
                region: 'Remich',
                wine_type: 'Pinot Gris',
                price: 1800,
                features: ['Harvest Experience', 'Wine Tasting']
            },
            {
                id: 3,
                name: 'Bernard-Massard - Premium Section',
                producer: 'Bernard-Massard',
                region: 'Grevenmacher',
                wine_type: 'Crémant',
                price: 3200,
                features: ['Exclusive Events', 'Cellar Tours', 'Premium Bottles']
            }
        ];
        
        this.renderPlotBoundaries(samplePlots);
    }
    
    dispatchEvent(eventType, detail) {
        const event = new CustomEvent(`vineyardMap:${eventType}`, {
            detail,
            bubbles: true
        });
        document.dispatchEvent(event);
    }
    
    destroy() {
        if (this.map) {
            this.map.remove();
            this.map = null;
        }
        this.plotLayers = [];
        this.plotBoundaries.clear();
        this.selectedPlots.clear();
    }
}

// CSS styles for wine markers
const markerStyles = `
<style>
.wine-plot-marker .marker-icon {
    width: 30px;
    height: 40px;
    border-radius: 50% 50% 50% 0;
    transform: rotate(-45deg);
    border: 3px solid #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
}

.wine-plot-marker .marker-icon i {
    transform: rotate(45deg);
    color: white;
    font-size: 16px;
}

.plot-popup-content {
    min-width: 250px;
}

.plot-popup-content .plot-title {
    margin: 0 0 15px 0;
    color: #722F37;
    font-size: 16px;
    font-weight: 600;
}

.plot-popup-content .plot-details p {
    margin: 5px 0;
    font-size: 14px;
    line-height: 1.4;
}

.plot-popup-content .plot-actions {
    margin-top: 15px;
    display: flex;
    gap: 10px;
}

.plot-popup-content button {
    flex: 1;
    padding: 8px 12px;
    border: none;
    border-radius: 5px;
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.3s ease;
}

.btn-view-details {
    background: #f8f9fa;
    color: #722F37;
    border: 1px solid #722F37;
}

.btn-view-details:hover {
    background: #722F37;
    color: white;
}

.btn-select-plot {
    background: #D4AF37;
    color: white;
}

.btn-select-plot:hover {
    background: #B8941F;
}
</style>
`;

// Inject styles
if (typeof document !== 'undefined') {
    document.head.insertAdjacentHTML('beforeend', markerStyles);
}

// Make VineyardMap available globally for browser use
window.VineyardMap = VineyardMap;