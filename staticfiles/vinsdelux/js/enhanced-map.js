/**
 * Enhanced Map Integration for VinsDelux
 * Premium visualization of wine producers in Luxembourg
 */

(function() {
    'use strict';

    class VinsDeluxEnhancedMap {
        constructor() {
            this.map = null;
            this.markers = [];
            this.selectedProducer = null;
            this.producers = [];
            this.init();
        }

        init() {
            console.log('🗺️ Initializing Enhanced VinsDelux Map...');
            
            // Wait for DOM ready
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', () => this.setupMap());
            } else {
                this.setupMap();
            }
        }

        setupMap() {
            const mapContainer = document.getElementById('vineyard-map');
            if (!mapContainer) {
                console.warn('Map container not found');
                return;
            }

            // Initialize Leaflet map with custom options
            this.map = L.map('vineyard-map', {
                center: [49.5700, 6.3700], // Luxembourg Moselle region
                zoom: 10,
                minZoom: 8,
                maxZoom: 16,
                scrollWheelZoom: false,
                zoomControl: false, // We'll add custom controls
                attributionControl: false
            });

            // Add custom tile layer with wine-friendly colors
            L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                attribution: '© OpenStreetMap contributors © CARTO',
                subdomains: 'abcd',
                maxZoom: 20
            }).addTo(this.map);

            // Add custom zoom controls
            L.control.zoom({
                position: 'topright'
            }).addTo(this.map);

            // Add custom attribution
            L.control.attribution({
                position: 'bottomright',
                prefix: false
            }).addTo(this.map).addAttribution('© VinsDelux | © OpenStreetMap');

            // Enable scroll zoom on focus
            this.setupScrollBehavior();

            // Add map overlay for producer info
            this.addMapOverlay();

            // Load producers
            this.loadProducers();

            // Add custom styling
            this.addCustomStyles();

            // Add map animations
            this.addMapAnimations();
        }

        setupScrollBehavior() {
            this.map.on('focus', () => {
                this.map.scrollWheelZoom.enable();
            });

            this.map.on('blur', () => {
                this.map.scrollWheelZoom.disable();
            });

            // Enable on click
            this.map.on('click', () => {
                this.map.scrollWheelZoom.enable();
            });
        }

        addMapOverlay() {
            const overlay = L.control({position: 'topleft'});
            
            overlay.onAdd = function(map) {
                const div = L.DomUtil.create('div', 'map-info-overlay');
                div.innerHTML = `
                    <div class="map-overlay-content">
                        <h5><i class="fas fa-map-marked-alt"></i> Luxembourg Wine Regions</h5>
                        <p>Select a producer to explore their adoption plans</p>
                        <div id="selected-producer-info"></div>
                    </div>
                `;
                return div;
            };
            
            overlay.addTo(this.map);
        }

        loadProducers() {
            const langCode = document.documentElement.lang || 'en';
            
            // Fetch adoption plans which contain producer info
            fetch(`/${langCode}/vinsdelux/api/adoption-plans/`)
                .then(response => response.json())
                .then(data => {
                    const plans = data.adoption_plans || data.plans || [];
                    
                    // Luxembourg wine regions for each producer
                    const locations = [
                        { lat: 49.6116, lng: 6.1319, area: "Luxembourg City", icon: "🏰" },
                        { lat: 49.5447, lng: 6.3669, area: "Remich", icon: "🍇" },
                        { lat: 49.6803, lng: 6.4417, area: "Grevenmacher", icon: "🍷" },
                        { lat: 49.6114, lng: 6.4003, area: "Wormeldange", icon: "🌿" },
                        { lat: 49.4697, lng: 6.3622, area: "Schengen", icon: "🇪🇺" }
                    ];

                    plans.forEach((plan, index) => {
                        const location = locations[index % locations.length];
                        const producer = plan.producer || {};
                        const producerName = producer.name || plan.name;
                        
                        // Create custom marker for each producer
                        const marker = this.createProducerMarker(
                            location,
                            producerName,
                            producer.region || 'Premium Wine Region',
                            plan,
                            location.icon
                        );
                        
                        this.markers.push(marker);
                        this.producers.push({
                            name: producerName,
                            location: location,
                            plan: plan,
                            marker: marker
                        });
                    });

                    // Add marker clustering for better visualization
                    this.setupMarkerClustering();

                    // Fit map to show all markers
                    if (this.markers.length > 0) {
                        const group = new L.featureGroup(this.markers);
                        this.map.fitBounds(group.getBounds().pad(0.1));
                    }
                })
                .catch(error => {
                    console.error('Error loading producers:', error);
                    this.showFallbackProducers();
                });
        }

        createProducerMarker(location, producerName, region, plan, icon) {
            // Create custom HTML marker
            const markerHtml = `
                <div class="producer-marker-wrapper">
                    <div class="producer-marker" data-producer="${producerName}">
                        <div class="marker-icon">${icon}</div>
                        <div class="marker-pulse"></div>
                    </div>
                    <div class="producer-label">${producerName.split(' ')[0]}</div>
                </div>
            `;

            const customIcon = L.divIcon({
                className: 'custom-producer-marker',
                html: markerHtml,
                iconSize: [80, 80],
                iconAnchor: [40, 60],
                popupAnchor: [0, -50]
            });

            const marker = L.marker([location.lat, location.lng], {
                icon: customIcon,
                riseOnHover: true,
                title: producerName
            });

            // Create rich popup content
            const popupContent = this.createPopupContent(producerName, region, location.area, plan);
            marker.bindPopup(popupContent, {
                maxWidth: 350,
                className: 'producer-popup-enhanced'
            });

            // Add hover effect
            marker.on('mouseover', (e) => {
                const markerEl = e.target.getElement();
                if (markerEl) {
                    markerEl.classList.add('hover');
                }
                this.showProducerPreview(producerName, plan);
            });

            marker.on('mouseout', (e) => {
                const markerEl = e.target.getElement();
                if (markerEl) {
                    markerEl.classList.remove('hover');
                }
            });

            // Handle click for selection
            marker.on('click', () => {
                this.selectProducer(producerName, plan, marker);
            });

            marker.addTo(this.map);
            return marker;
        }

        createPopupContent(producerName, region, luxembourgArea, plan) {
            const price = plan.price || 'Contact us';
            const duration = plan.duration_months || 12;
            const coffrets = plan.coffrets_per_year || 2;
            
            return `
                <div class="enhanced-popup">
                    <div class="popup-header">
                        <h4>${producerName}</h4>
                        <span class="region-badge">${region}</span>
                    </div>
                    <div class="popup-body">
                        <div class="location-info">
                            <i class="fas fa-map-pin"></i> ${luxembourgArea}, Luxembourg
                        </div>
                        <div class="plan-preview">
                            <h6>${plan.name}</h6>
                            <div class="plan-details">
                                <span><i class="fas fa-euro-sign"></i> ${price}</span>
                                <span><i class="fas fa-calendar"></i> ${duration} months</span>
                                <span><i class="fas fa-wine-bottle"></i> ${coffrets} coffrets/year</span>
                            </div>
                        </div>
                        ${plan.description ? `<p class="plan-description">${plan.description}</p>` : ''}
                        <button class="btn btn-primary btn-block select-producer-btn" 
                                data-producer="${producerName}"
                                data-plan-id="${plan.id}">
                            <i class="fas fa-check-circle"></i> Select ${producerName}
                        </button>
                    </div>
                </div>
            `;
        }

        showProducerPreview(producerName, plan) {
            const infoDiv = document.getElementById('selected-producer-info');
            if (infoDiv) {
                infoDiv.innerHTML = `
                    <div class="producer-preview">
                        <strong>${producerName}</strong><br>
                        <small>€${plan.price} - ${plan.duration_months} months</small>
                    </div>
                `;
            }
        }

        selectProducer(producerName, plan, marker) {
            // Remove previous selection
            if (this.selectedProducer) {
                const prevMarkerEl = this.selectedProducer.marker.getElement();
                if (prevMarkerEl) {
                    prevMarkerEl.classList.remove('selected');
                }
            }

            // Mark new selection
            this.selectedProducer = { name: producerName, plan: plan, marker: marker };
            const markerEl = marker.getElement();
            if (markerEl) {
                markerEl.classList.add('selected');
            }

            // Save to session
            sessionStorage.setItem('selectedProducer', producerName);
            sessionStorage.setItem('selectedPlanId', plan.id);

            // Update UI
            this.updateSelectionDisplay(producerName, plan);

            // Trigger adoption plan section
            setTimeout(() => {
                this.showAdoptionPlans(producerName);
            }, 500);
        }

        updateSelectionDisplay(producerName, plan) {
            const display = document.getElementById('selected-producer-display');
            if (display) {
                display.innerHTML = `
                    <div class="alert alert-success fade show">
                        <h6><i class="fas fa-check-circle"></i> Producer Selected</h6>
                        <p class="mb-0"><strong>${producerName}</strong></p>
                        <small>${plan.name} - €${plan.price}</small>
                    </div>
                `;
            }
        }

        showAdoptionPlans(producerName) {
            const section = document.getElementById('adoption-plan-section');
            if (section) {
                section.style.display = 'block';
                section.scrollIntoView({ behavior: 'smooth', block: 'start' });
                
                // Load plans for this producer
                if (window.loadAdoptionPlans) {
                    window.loadAdoptionPlans(producerName);
                }
            }
        }

        setupMarkerClustering() {
            // Optional: Add marker clustering for many producers
            // Useful if you expand to more producers later
        }

        showFallbackProducers() {
            // Fallback data if API fails
            const fallbackProducers = [
                { name: "Château Margaux", lat: 49.6116, lng: 6.1319 },
                { name: "Domaine de la Romanée-Conti", lat: 49.5447, lng: 6.3669 },
                { name: "Penfolds", lat: 49.6803, lng: 6.4417 },
                { name: "Antinori", lat: 49.6114, lng: 6.4003 },
                { name: "Catena Zapata", lat: 49.4697, lng: 6.3622 }
            ];

            fallbackProducers.forEach(producer => {
                L.marker([producer.lat, producer.lng])
                    .bindPopup(`<h5>${producer.name}</h5>`)
                    .addTo(this.map);
            });
        }

        addCustomStyles() {
            const style = document.createElement('style');
            style.textContent = `
                /* Enhanced Map Styles */
                #vineyard-map {
                    border-radius: 12px;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.1);
                }

                .map-info-overlay {
                    background: white;
                    padding: 15px;
                    border-radius: 8px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    min-width: 250px;
                }

                .map-overlay-content h5 {
                    color: #722F37;
                    margin-bottom: 8px;
                    font-size: 16px;
                }

                .map-overlay-content p {
                    color: #666;
                    margin: 0;
                    font-size: 14px;
                }

                .producer-preview {
                    margin-top: 10px;
                    padding: 10px;
                    background: #f8f9fa;
                    border-radius: 6px;
                    border-left: 3px solid #D4AF37;
                }

                /* Custom Marker Styles */
                .custom-producer-marker {
                    z-index: 1000 !important;
                    opacity: 1 !important;
                }

                .producer-marker-wrapper {
                    position: relative;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    opacity: 1;
                }

                .producer-marker {
                    width: 50px;
                    height: 50px;
                    background: linear-gradient(135deg, #722F37 0%, #8B0000 100%);
                    border-radius: 50% 50% 50% 0;
                    transform: rotate(-45deg);
                    border: 3px solid #D4AF37;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    transition: all 0.3s ease;
                    cursor: pointer;
                }

                .marker-icon {
                    transform: rotate(45deg);
                    font-size: 24px;
                }

                .marker-pulse {
                    position: absolute;
                    width: 100%;
                    height: 100%;
                    border-radius: 50% 50% 50% 0;
                    border: 2px solid #D4AF37;
                    animation: pulse 2s ease-out infinite;
                    opacity: 0;
                }

                @keyframes pulse {
                    0% {
                        transform: scale(1) rotate(-45deg);
                        opacity: 1;
                    }
                    100% {
                        transform: scale(1.5) rotate(-45deg);
                        opacity: 0;
                    }
                }

                .producer-marker-wrapper.hover .producer-marker {
                    transform: rotate(-45deg) scale(1.1);
                    box-shadow: 0 6px 20px rgba(0,0,0,0.4);
                }

                .producer-marker-wrapper.selected .producer-marker {
                    background: linear-gradient(135deg, #D4AF37 0%, #B8860B 100%);
                    border-color: #722F37;
                }

                .producer-label {
                    margin-top: 5px;
                    background: white;
                    padding: 2px 8px;
                    border-radius: 4px;
                    font-size: 12px;
                    font-weight: 600;
                    color: #722F37;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    white-space: nowrap;
                }

                /* Enhanced Popup Styles */
                .producer-popup-enhanced .leaflet-popup-content-wrapper {
                    border-radius: 12px;
                    padding: 0;
                    overflow: hidden;
                }

                .enhanced-popup {
                    padding: 0;
                }

                .popup-header {
                    background: linear-gradient(135deg, #722F37 0%, #8B0000 100%);
                    color: white;
                    padding: 15px;
                }

                .popup-header h4 {
                    margin: 0;
                    font-size: 18px;
                    font-weight: 700;
                }

                .region-badge {
                    display: inline-block;
                    background: rgba(255,255,255,0.2);
                    padding: 2px 8px;
                    border-radius: 4px;
                    font-size: 12px;
                    margin-top: 5px;
                }

                .popup-body {
                    padding: 15px;
                }

                .location-info {
                    color: #666;
                    margin-bottom: 15px;
                    font-size: 14px;
                }

                .plan-preview h6 {
                    color: #722F37;
                    margin: 10px 0 8px 0;
                    font-weight: 600;
                }

                .plan-details {
                    display: flex;
                    justify-content: space-between;
                    margin-bottom: 10px;
                    font-size: 13px;
                }

                .plan-details span {
                    color: #666;
                }

                .plan-description {
                    font-size: 13px;
                    color: #666;
                    line-height: 1.4;
                    margin: 10px 0;
                }

                .select-producer-btn {
                    background: #D4AF37;
                    border: none;
                    color: #722F37;
                    font-weight: 600;
                    padding: 10px;
                    border-radius: 6px;
                    transition: all 0.3s ease;
                    width: 100%;
                    margin-top: 10px;
                }

                .select-producer-btn:hover {
                    background: #B8860B;
                    color: white;
                    transform: translateY(-2px);
                    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
                }

                /* Zoom Control Styling */
                .leaflet-control-zoom {
                    border: none !important;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1) !important;
                }

                .leaflet-control-zoom a {
                    background: white !important;
                    color: #722F37 !important;
                    border: none !important;
                    width: 36px !important;
                    height: 36px !important;
                    line-height: 36px !important;
                    font-size: 20px !important;
                    font-weight: normal !important;
                }

                .leaflet-control-zoom a:hover {
                    background: #722F37 !important;
                    color: white !important;
                }
            `;
            document.head.appendChild(style);
        }

        addMapAnimations() {
            // Add drop-in animation styles first
            const animStyle = document.createElement('style');
            animStyle.textContent = `
                @keyframes dropIn {
                    0% {
                        transform: translateY(-50px);
                        opacity: 0;
                    }
                    100% {
                        transform: translateY(0);
                        opacity: 1;
                    }
                }
                
                .leaflet-marker-icon {
                    opacity: 1 !important;
                }
                
                .custom-producer-marker {
                    opacity: 1 !important;
                }
                
                .marker-animated {
                    animation: dropIn 0.5s ease-out forwards;
                    animation-fill-mode: both;
                }
            `;
            document.head.appendChild(animStyle);
            
            // Add subtle animations when map loads (simplified to avoid disappearing)
            this.markers.forEach((marker, index) => {
                setTimeout(() => {
                    const el = marker.getElement();
                    if (el) {
                        // Add animation class instead of inline style
                        el.classList.add('marker-animated');
                        el.style.animationDelay = `${index * 100}ms`;
                    }
                }, 100);
            });
    }

    // Initialize the enhanced map
    window.vinsDeluxEnhancedMap = new VinsDeluxEnhancedMap();

    // Expose loadAdoptionPlans function globally
    window.loadAdoptionPlans = function(producerName) {
        // This function will be called from the enhanced map
        // Use the existing implementation from map-init-simple.js
        const event = new CustomEvent('loadAdoptionPlans', { detail: { producer: producerName } });
        document.dispatchEvent(event);
    };

})();