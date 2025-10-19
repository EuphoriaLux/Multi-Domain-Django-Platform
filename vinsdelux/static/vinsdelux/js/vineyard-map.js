/**
 * VineyardMap Class - Interactive Leaflet Map for Wine Plot Selection
 * Provides sophisticated map visualization with plot markers, clustering, and real-time updates
 */

class VineyardMap {
    constructor(options = {}) {
        this.mapContainer = options.container || 'vineyard-map';
        this.center = options.center || [49.8153, 6.1296]; // Luxembourg coordinates
        this.zoom = options.zoom || 10;
        this.maxZoom = options.maxZoom || 18;
        this.minZoom = options.minZoom || 8;
        
        this.map = null;
        this.markers = new Map();
        this.markerCluster = null;
        this.selectedPlots = new Set();
        this.plotData = options.plotData || [];
        
        // Custom icon definitions
        this.icons = {
            available: null,
            reserved: null,
            adopted: null,
            selected: null,
            premium: null
        };
        
        // Layer groups
        this.layers = {
            plots: null,
            regions: null,
            vineyards: null,
            terrain: null
        };
        
        // Event callbacks
        this.callbacks = {
            onPlotSelect: options.onPlotSelect || (() => {}),
            onPlotDeselect: options.onPlotDeselect || (() => {}),
            onPlotHover: options.onPlotHover || (() => {}),
            onMapReady: options.onMapReady || (() => {}),
            onRegionChange: options.onRegionChange || (() => {})
        };
        
        this.init();
    }
    
    init() {
        this.createMap();
        this.setupIcons();
        this.setupLayers();
        this.addControls();
        this.loadPlotData();
        this.setupEventListeners();
        
        // Trigger ready callback
        this.callbacks.onMapReady(this.map);
    }
    
    createMap() {
        // Initialize Leaflet map with custom options
        this.map = L.map(this.mapContainer, {
            center: this.center,
            zoom: this.zoom,
            maxZoom: this.maxZoom,
            minZoom: this.minZoom,
            zoomControl: false,
            attributionControl: false
        });
        
        // Add custom tile layers
        this.addBaseLayers();
        
        // Add zoom control to top-right
        L.control.zoom({
            position: 'topright'
        }).addTo(this.map);
        
        // Add custom attribution
        L.control.attribution({
            position: 'bottomright',
            prefix: 'Vins de Lux Vineyards'
        }).addTo(this.map);
        
        // Initialize marker clustering
        this.markerCluster = L.markerClusterGroup({
            showCoverageOnHover: false,
            maxClusterRadius: 50,
            spiderfyOnMaxZoom: true,
            chunkedLoading: true,
            iconCreateFunction: (cluster) => {
                const count = cluster.getChildCount();
                let size = 'small';
                let className = 'marker-cluster-';
                
                if (count > 10) {
                    size = 'large';
                } else if (count > 5) {
                    size = 'medium';
                }
                
                className += size;
                
                return L.divIcon({
                    html: `<div><span>${count}</span></div>`,
                    className: `marker-cluster ${className}`,
                    iconSize: L.point(40, 40)
                });
            }
        });
    }
    
    addBaseLayers() {
        // Terrain layer with elevation
        const terrainLayer = L.tileLayer('https://stamen-tiles-{s}.a.ssl.fastly.net/terrain/{z}/{x}/{y}.png', {
            attribution: 'Map tiles by Stamen Design',
            subdomains: 'abcd',
            minZoom: 0,
            maxZoom: 18
        });
        
        // Satellite layer
        const satelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Imagery © Esri'
        });
        
        // Vineyard-styled custom layer (OpenStreetMap with custom styling)
        const vineyardLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
            attribution: '© OpenStreetMap contributors',
            subdomains: 'abcd',
            maxZoom: 19
        });
        
        // Set default layer
        vineyardLayer.addTo(this.map);
        
        // Add layer control
        const baseLayers = {
            'Vineyard View': vineyardLayer,
            'Terrain': terrainLayer,
            'Satellite': satelliteLayer
        };
        
        L.control.layers(baseLayers, null, {
            position: 'topleft'
        }).addTo(this.map);
    }
    
    setupIcons() {
        // Define custom vineyard plot icons
        const IconBase = L.Icon.extend({
            options: {
                iconSize: [32, 40],
                iconAnchor: [16, 40],
                popupAnchor: [0, -35],
                shadowUrl: '/static/vinsdelux/images/marker-shadow.png',
                shadowSize: [40, 40],
                shadowAnchor: [12, 40]
            }
        });
        
        // Create SVG icons for different plot statuses
        this.icons.available = new IconBase({
            iconUrl: this.createSVGIcon('#D4AF37', 'wine-glass')
        });
        
        this.icons.reserved = new IconBase({
            iconUrl: this.createSVGIcon('#FF9800', 'clock')
        });
        
        this.icons.adopted = new IconBase({
            iconUrl: this.createSVGIcon('#722F37', 'check')
        });
        
        this.icons.selected = new IconBase({
            iconUrl: this.createSVGIcon('#4CAF50', 'star')
        });
        
        this.icons.premium = new IconBase({
            iconUrl: this.createSVGIcon('#9C27B0', 'crown')
        });
    }
    
    createSVGIcon(color, iconType) {
        const icons = {
            'wine-glass': `<path d="M12 2c1.1 0 2 .9 2 2v5c0 2.21-1.79 4-4 4s-4-1.79-4-4V4c0-1.1.9-2 2-2h4m0 11c2.21 0 4-1.79 4-4V4c0-1.1-.9-2-2-2H8c-1.1 0-2 .9-2 2v5c0 2.21 1.79 4 4 4v7H8v2h8v-2h-2v-7z"/>`,
            'clock': `<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67V7z"/>`,
            'check': `<path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/>`,
            'star': `<path d="M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21 12 17.27z"/>`,
            'crown': `<path d="M5 16L3 5l5.5 5L12 4l3.5 6L21 5l-2 11H5zm2.86-2h8.28l.96-5.76-3.65 3.26L12 8.5l-1.45 3-3.65-3.26L7.86 14z"/>`
        };
        
        const svg = `
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="40" viewBox="0 0 32 40">
                <defs>
                    <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
                        <feGaussianBlur in="SourceAlpha" stdDeviation="2"/>
                        <feOffset dx="0" dy="2" result="offsetblur"/>
                        <feComponentTransfer>
                            <feFuncA type="linear" slope="0.3"/>
                        </feComponentTransfer>
                        <feMerge>
                            <feMergeNode/>
                            <feMergeNode in="SourceGraphic"/>
                        </feMerge>
                    </filter>
                </defs>
                <path d="M16 0 C7 0 0 7 0 16 C0 22 4 27 8 32 L16 40 L24 32 C28 27 32 22 32 16 C32 7 25 0 16 0 Z" 
                      fill="${color}" stroke="#fff" stroke-width="2" filter="url(#shadow)"/>
                <g transform="translate(4, 4) scale(1)">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="white">
                        ${icons[iconType] || icons['wine-glass']}
                    </svg>
                </g>
            </svg>
        `;
        
        return 'data:image/svg+xml;base64,' + btoa(svg);
    }
    
    setupLayers() {
        // Create layer groups for organization
        this.layers.plots = L.layerGroup().addTo(this.map);
        this.layers.regions = L.layerGroup();
        this.layers.vineyards = L.layerGroup();
        this.layers.terrain = L.layerGroup();
    }
    
    addControls() {
        // Add custom control for plot filtering
        const FilterControl = L.Control.extend({
            options: {
                position: 'topright'
            },
            
            onAdd: (map) => {
                const container = L.DomUtil.create('div', 'leaflet-bar leaflet-control leaflet-control-custom');
                container.innerHTML = `
                    <div class="map-filter-control">
                        <button class="filter-toggle" title="Filter Plots">
                            <i class="fas fa-filter"></i>
                        </button>
                        <div class="filter-panel" style="display: none;">
                            <h4>Filter Plots</h4>
                            <div class="filter-group">
                                <label>Status</label>
                                <select id="map-filter-status">
                                    <option value="all">All</option>
                                    <option value="available">Available</option>
                                    <option value="reserved">Reserved</option>
                                    <option value="adopted">Adopted</option>
                                </select>
                            </div>
                            <div class="filter-group">
                                <label>Price Range</label>
                                <input type="range" id="map-filter-price" min="0" max="10000" step="100">
                                <span class="price-display">€0 - €10,000</span>
                            </div>
                            <div class="filter-group">
                                <label>Premium Only</label>
                                <input type="checkbox" id="map-filter-premium">
                            </div>
                            <button class="apply-filters">Apply</button>
                        </div>
                    </div>
                `;
                
                // Prevent map interaction when using controls
                L.DomEvent.disableClickPropagation(container);
                L.DomEvent.disableScrollPropagation(container);
                
                // Toggle filter panel
                container.querySelector('.filter-toggle').addEventListener('click', () => {
                    const panel = container.querySelector('.filter-panel');
                    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
                });
                
                // Apply filters
                container.querySelector('.apply-filters').addEventListener('click', () => {
                    this.applyFilters();
                });
                
                return container;
            }
        });
        
        new FilterControl().addTo(this.map);
        
        // Add legend control
        const legend = L.control({position: 'bottomleft'});
        legend.onAdd = (map) => {
            const div = L.DomUtil.create('div', 'map-legend');
            div.innerHTML = `
                <div class="legend-content">
                    <h4>Plot Status</h4>
                    <div class="legend-item">
                        <span class="legend-icon" style="background: #D4AF37;"></span>
                        <span>Available</span>
                    </div>
                    <div class="legend-item">
                        <span class="legend-icon" style="background: #FF9800;"></span>
                        <span>Reserved</span>
                    </div>
                    <div class="legend-item">
                        <span class="legend-icon" style="background: #722F37;"></span>
                        <span>Adopted</span>
                    </div>
                    <div class="legend-item">
                        <span class="legend-icon" style="background: #9C27B0;"></span>
                        <span>Premium</span>
                    </div>
                </div>
            `;
            return div;
        };
        legend.addTo(this.map);
        
        // Add search control
        this.addSearchControl();
    }
    
    addSearchControl() {
        const SearchControl = L.Control.extend({
            options: {
                position: 'topleft'
            },
            
            onAdd: (map) => {
                const container = L.DomUtil.create('div', 'leaflet-control-search');
                container.innerHTML = `
                    <div class="search-container">
                        <input type="text" id="map-search" placeholder="Search plots, producers, regions...">
                        <button class="search-btn"><i class="fas fa-search"></i></button>
                        <div class="search-results" style="display: none;"></div>
                    </div>
                `;
                
                L.DomEvent.disableClickPropagation(container);
                
                const searchInput = container.querySelector('#map-search');
                const searchResults = container.querySelector('.search-results');
                
                searchInput.addEventListener('input', (e) => {
                    this.performSearch(e.target.value, searchResults);
                });
                
                return container;
            }
        });
        
        new SearchControl().addTo(this.map);
    }
    
    loadPlotData() {
        // Load plot data from provided array or fetch from API
        if (this.plotData && this.plotData.length > 0) {
            this.addPlotMarkers(this.plotData);
        } else {
            this.fetchPlotData();
        }
    }
    
    async fetchPlotData() {
        try {
            const response = await fetch('/api/plots/?include_coordinates=true');
            if (!response.ok) throw new Error('Failed to fetch plot data');
            
            const data = await response.json();
            this.plotData = data.results || data;
            this.addPlotMarkers(this.plotData);
        } catch (error) {
            console.error('Error fetching plot data:', error);
            this.showNotification('Unable to load vineyard plots', 'error');
        }
    }
    
    addPlotMarkers(plots) {
        plots.forEach(plot => {
            if (!plot.latitude || !plot.longitude) return;
            
            const marker = this.createPlotMarker(plot);
            this.markers.set(plot.id, marker);
            this.markerCluster.addLayer(marker);
        });
        
        this.map.addLayer(this.markerCluster);
        
        // Fit map to show all markers
        if (plots.length > 0) {
            const group = new L.featureGroup(Array.from(this.markers.values()));
            this.map.fitBounds(group.getBounds().pad(0.1));
        }
    }
    
    createPlotMarker(plot) {
        // Select appropriate icon based on plot status and characteristics
        let icon = this.icons.available;
        
        if (plot.status === 'reserved') {
            icon = this.icons.reserved;
        } else if (plot.status === 'adopted') {
            icon = this.icons.adopted;
        } else if (plot.is_premium) {
            icon = this.icons.premium;
        }
        
        if (this.selectedPlots.has(plot.id)) {
            icon = this.icons.selected;
        }
        
        const marker = L.marker([plot.latitude, plot.longitude], {
            icon: icon,
            title: plot.name || `Plot ${plot.plot_identifier}`,
            riseOnHover: true
        });
        
        // Store plot data with marker
        marker.plotData = plot;
        
        // Create rich popup content
        const popupContent = this.createPopupContent(plot);
        marker.bindPopup(popupContent, {
            maxWidth: 300,
            className: 'plot-popup'
        });
        
        // Add event listeners
        marker.on('click', () => this.handlePlotClick(plot));
        marker.on('mouseover', () => this.handlePlotHover(plot));
        marker.on('popupopen', () => this.handlePopupOpen(plot));
        
        return marker;
    }
    
    createPopupContent(plot) {
        const price = plot.base_price ? `€${plot.base_price.toLocaleString()}` : 'Price on request';
        const statusClass = plot.status === 'available' ? 'status-available' : 
                           plot.status === 'reserved' ? 'status-reserved' : 'status-adopted';
        
        return `
            <div class="plot-popup-content">
                <div class="popup-header">
                    <h4>${plot.name || `Plot ${plot.plot_identifier}`}</h4>
                    <span class="plot-status ${statusClass}">${plot.status}</span>
                </div>
                
                <div class="popup-body">
                    <div class="plot-info">
                        <div class="info-row">
                            <span class="label">Producer:</span>
                            <span class="value">${plot.producer_name || plot.producer?.name || 'Unknown'}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Region:</span>
                            <span class="value">${plot.producer_region || plot.producer?.region || 'Luxembourg'}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Size:</span>
                            <span class="value">${plot.plot_size || 'N/A'}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Elevation:</span>
                            <span class="value">${plot.elevation || 'N/A'}</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Grape Varieties:</span>
                            <span class="value">${this.formatGrapeVarieties(plot.grape_varieties)}</span>
                        </div>
                    </div>
                    
                    ${plot.wine_profile ? `
                    <div class="wine-profile">
                        <p class="profile-label">Wine Profile:</p>
                        <p class="profile-text">${plot.wine_profile}</p>
                    </div>
                    ` : ''}
                    
                    <div class="popup-footer">
                        <div class="price-tag">${price}</div>
                        ${plot.status === 'available' ? `
                        <button class="btn-select-plot" data-plot-id="${plot.id}">
                            ${this.selectedPlots.has(plot.id) ? 'Remove from Selection' : 'Add to Selection'}
                        </button>
                        ` : ''}
                    </div>
                </div>
            </div>
        `;
    }
    
    formatGrapeVarieties(varieties) {
        if (!varieties || varieties.length === 0) return 'Various';
        if (typeof varieties === 'string') return varieties;
        return varieties.slice(0, 3).join(', ') + (varieties.length > 3 ? '...' : '');
    }
    
    handlePlotClick(plot) {
        if (plot.status === 'available') {
            this.togglePlotSelection(plot);
        }
        
        // Trigger callback
        this.callbacks.onPlotSelect(plot);
    }
    
    handlePlotHover(plot) {
        this.callbacks.onPlotHover(plot);
    }
    
    handlePopupOpen(plot) {
        // Add event listener to selection button in popup
        setTimeout(() => {
            const btn = document.querySelector(`.btn-select-plot[data-plot-id="${plot.id}"]`);
            if (btn) {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.togglePlotSelection(plot);
                    
                    // Update button text
                    if (this.selectedPlots.has(plot.id)) {
                        btn.textContent = 'Remove from Selection';
                    } else {
                        btn.textContent = 'Add to Selection';
                    }
                });
            }
        }, 100);
    }
    
    togglePlotSelection(plot) {
        if (this.selectedPlots.has(plot.id)) {
            this.deselectPlot(plot);
        } else {
            this.selectPlot(plot);
        }
    }
    
    selectPlot(plot) {
        this.selectedPlots.add(plot.id);
        
        // Update marker icon
        const marker = this.markers.get(plot.id);
        if (marker) {
            marker.setIcon(this.icons.selected);
        }
        
        // Dispatch event for other components
        document.dispatchEvent(new CustomEvent('vineyardMap:plotSelected', {
            detail: { plot }
        }));
        
        // Trigger callback
        this.callbacks.onPlotSelect(plot);
        
        this.showNotification(`${plot.name || 'Plot'} added to selection`, 'success');
    }
    
    deselectPlot(plot) {
        this.selectedPlots.delete(plot.id);
        
        // Update marker icon
        const marker = this.markers.get(plot.id);
        if (marker) {
            const icon = plot.is_premium ? this.icons.premium : this.icons.available;
            marker.setIcon(icon);
        }
        
        // Dispatch event for other components
        document.dispatchEvent(new CustomEvent('vineyardMap:plotDeselected', {
            detail: { plotId: plot.id }
        }));
        
        // Trigger callback
        this.callbacks.onPlotDeselect(plot);
        
        this.showNotification(`${plot.name || 'Plot'} removed from selection`, 'info');
    }
    
    setupEventListeners() {
        // Listen for external plot selection events
        document.addEventListener('plotSelector:plotSelected', (e) => {
            const plot = e.detail.plot;
            if (!this.selectedPlots.has(plot.id)) {
                this.selectPlot(plot);
            }
            
            // Center map on selected plot
            if (plot.latitude && plot.longitude) {
                this.map.setView([plot.latitude, plot.longitude], 15);
                
                // Open popup
                const marker = this.markers.get(plot.id);
                if (marker) {
                    marker.openPopup();
                }
            }
        });
        
        document.addEventListener('plotSelector:plotDeselected', (e) => {
            const plotId = e.detail.plotId;
            const plot = this.plotData.find(p => p.id === plotId);
            if (plot && this.selectedPlots.has(plotId)) {
                this.deselectPlot(plot);
            }
        });
        
        // Map events
        this.map.on('zoomend', () => {
            this.updateMarkersForZoom();
        });
        
        this.map.on('moveend', () => {
            const bounds = this.map.getBounds();
            this.callbacks.onRegionChange(bounds);
        });
    }
    
    updateMarkersForZoom() {
        const zoom = this.map.getZoom();
        
        // Adjust marker size based on zoom level
        if (zoom > 14) {
            // Show more details at higher zoom
            this.markers.forEach(marker => {
                marker.setOpacity(1);
            });
        } else if (zoom < 10) {
            // Reduce opacity at lower zoom
            this.markers.forEach(marker => {
                marker.setOpacity(0.8);
            });
        }
    }
    
    applyFilters() {
        const status = document.getElementById('map-filter-status').value;
        const maxPrice = document.getElementById('map-filter-price').value;
        const premiumOnly = document.getElementById('map-filter-premium').checked;
        
        // Clear current markers
        this.markerCluster.clearLayers();
        
        // Filter and re-add markers
        this.plotData.forEach(plot => {
            let shouldShow = true;
            
            if (status !== 'all' && plot.status !== status) {
                shouldShow = false;
            }
            
            if (plot.base_price > maxPrice) {
                shouldShow = false;
            }
            
            if (premiumOnly && !plot.is_premium) {
                shouldShow = false;
            }
            
            if (shouldShow) {
                const marker = this.markers.get(plot.id);
                if (marker) {
                    this.markerCluster.addLayer(marker);
                }
            }
        });
        
        this.showNotification('Filters applied', 'success');
    }
    
    performSearch(query, resultsContainer) {
        if (!query || query.length < 2) {
            resultsContainer.style.display = 'none';
            return;
        }
        
        const lowerQuery = query.toLowerCase();
        const results = this.plotData.filter(plot => {
            return (
                plot.name?.toLowerCase().includes(lowerQuery) ||
                plot.plot_identifier?.toLowerCase().includes(lowerQuery) ||
                plot.producer_name?.toLowerCase().includes(lowerQuery) ||
                plot.producer_region?.toLowerCase().includes(lowerQuery) ||
                plot.grape_varieties?.some(v => v.toLowerCase().includes(lowerQuery))
            );
        }).slice(0, 5);
        
        if (results.length > 0) {
            resultsContainer.innerHTML = results.map(plot => `
                <div class="search-result-item" data-plot-id="${plot.id}">
                    <div class="result-name">${plot.name || `Plot ${plot.plot_identifier}`}</div>
                    <div class="result-info">${plot.producer_name} - ${plot.producer_region}</div>
                </div>
            `).join('');
            
            resultsContainer.style.display = 'block';
            
            // Add click handlers
            resultsContainer.querySelectorAll('.search-result-item').forEach(item => {
                item.addEventListener('click', () => {
                    const plotId = parseInt(item.dataset.plotId);
                    const plot = this.plotData.find(p => p.id === plotId);
                    if (plot && plot.latitude && plot.longitude) {
                        this.map.setView([plot.latitude, plot.longitude], 15);
                        const marker = this.markers.get(plotId);
                        if (marker) {
                            marker.openPopup();
                        }
                    }
                    resultsContainer.style.display = 'none';
                    document.getElementById('map-search').value = '';
                });
            });
        } else {
            resultsContainer.innerHTML = '<div class="no-results">No plots found</div>';
            resultsContainer.style.display = 'block';
        }
    }
    
    showNotification(message, type = 'info', duration = 3000) {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `map-notification notification-${type}`;
        notification.textContent = message;
        
        notification.style.cssText = `
            position: absolute;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 1000;
            padding: 12px 24px;
            background: white;
            border-radius: 4px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            font-size: 14px;
            opacity: 0;
            transition: opacity 0.3s ease;
        `;
        
        // Type-specific styling
        const colors = {
            success: '#4CAF50',
            error: '#F44336',
            warning: '#FF9800',
            info: '#2196F3'
        };
        
        notification.style.borderLeft = `4px solid ${colors[type] || colors.info}`;
        
        this.map.getContainer().appendChild(notification);
        
        // Animate in
        setTimeout(() => {
            notification.style.opacity = '1';
        }, 10);
        
        // Auto remove
        setTimeout(() => {
            notification.style.opacity = '0';
            setTimeout(() => notification.remove(), 300);
        }, duration);
    }
    
    // Public API methods
    centerOnPlot(plotId) {
        const plot = this.plotData.find(p => p.id === plotId);
        if (plot && plot.latitude && plot.longitude) {
            this.map.setView([plot.latitude, plot.longitude], 15);
            const marker = this.markers.get(plotId);
            if (marker) {
                marker.openPopup();
            }
        }
    }
    
    getSelectedPlots() {
        return Array.from(this.selectedPlots).map(id => 
            this.plotData.find(p => p.id === id)
        ).filter(Boolean);
    }
    
    clearSelection() {
        this.selectedPlots.forEach(plotId => {
            const plot = this.plotData.find(p => p.id === plotId);
            if (plot) {
                this.deselectPlot(plot);
            }
        });
    }
    
    refreshPlotData() {
        this.fetchPlotData();
    }
    
    destroy() {
        if (this.map) {
            this.map.remove();
        }
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = VineyardMap;
}