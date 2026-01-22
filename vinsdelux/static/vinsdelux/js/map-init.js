/**
 * Simple Map Initialization for VinsDelux Plot Selection
 * This initializes the Leaflet map without ES6 modules
 */

(function() {
    'use strict';

    // Wait for DOM to be ready
    document.addEventListener('DOMContentLoaded', function() {
        console.log('🗺️ Initializing VinsDelux vineyard map...');
        
        // Check if Leaflet is loaded
        if (typeof L === 'undefined') {
            console.error('❌ Leaflet.js is not loaded');
            return;
        }

        // Find the map container
        const mapContainer = document.getElementById('vineyard-map');
        if (!mapContainer) {
            console.warn('⚠️ Map container not found on this page');
            return;
        }

        // Initialize the map centered on Luxembourg's Moselle wine region
        const map = L.map('vineyard-map', {
            center: [49.5700, 6.3700], // Luxembourg Moselle wine region
            zoom: 11,
            scrollWheelZoom: false, // Disable scroll zoom for better UX
            zoomControl: true
        });

        // Add map tiles (OpenStreetMap)
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 18
        }).addTo(map);

        // Enable scroll zoom when user focuses on map
        map.on('focus', function() {
            map.scrollWheelZoom.enable();
        });

        map.on('blur', function() {
            map.scrollWheelZoom.disable();
        });

        // Add custom styles for luxury appearance
        const luxuryIcon = L.divIcon({
            className: 'luxury-plot-marker',
            html: '<div class="marker-inner"><i class="fas fa-wine-bottle"></i></div>',
            iconSize: [40, 40],
            iconAnchor: [20, 40]
        });

        // Load plot data from API (with language prefix)
        const langCode = document.documentElement.lang || 'en';
        fetch(`/${langCode}/vinsdelux/api/plots/availability/?include_plots=true`)
            .then(response => response.json())
            .then(data => {
                console.log('📍 Loading plot data:', data);
                
                // Add sample plots if API returns data
                if (data.available_plots && Array.isArray(data.available_plots)) {
                    data.available_plots.forEach(plot => {
                        // Check for latitude/longitude fields (API response format)
                        if (plot.latitude && plot.longitude) {
                            const marker = L.marker([plot.latitude, plot.longitude], {
                                icon: luxuryIcon,
                                title: plot.name || 'Vineyard Plot'
                            }).addTo(map);
                            
                            // Add popup with plot details
                            const popupContent = `
                                <div class="plot-popup">
                                    <h5>${plot.name || 'Vineyard Plot'}</h5>
                                    <p><strong>Producer:</strong> ${plot.producer_name || 'Unknown'}</p>
                                    <p><strong>Region:</strong> ${plot.producer_region || 'Luxembourg'}</p>
                                    <p><strong>Size:</strong> ${plot.plot_size || 'N/A'}</p>
                                    <p><strong>Grapes:</strong> ${plot.grape_varieties ? plot.grape_varieties.join(', ') : 'Various'}</p>
                                    <p><strong>Soil:</strong> ${plot.soil_type || 'N/A'}</p>
                                    <p><strong>Price:</strong> €${plot.base_price || 'Contact us'}</p>
                                    ${plot.is_premium ? '<span class="badge bg-warning text-dark">Premium Plot</span>' : ''}
                                    <button class="btn btn-sm btn-primary select-plot-btn mt-2" data-plot-id="${plot.id}">
                                        <i class="fas fa-wine-bottle"></i> Select This Plot
                                    </button>
                                </div>
                            `;
                            marker.bindPopup(popupContent);
                        }
                    });
                } else if (!data.available_plots || data.available_plots.length === 0) {
                    // Add sample markers for demonstration
                    const samplePlots = [
                        { lat: 49.6116, lng: 6.1319, name: "Hillside Premier 1" },
                        { lat: 49.6216, lng: 6.1419, name: "Valley Reserve 2" },
                        { lat: 49.6016, lng: 6.1219, name: "Sunset Terrace 3" },
                        { lat: 49.6316, lng: 6.1519, name: "Heritage Block 4" },
                        { lat: 49.5916, lng: 6.1119, name: "Premium Estate 5" }
                    ];
                    
                    samplePlots.forEach(plot => {
                        const marker = L.marker([plot.lat, plot.lng], {
                            icon: luxuryIcon,
                            title: plot.name
                        }).addTo(map);
                        
                        marker.bindPopup(`
                            <div class="plot-popup">
                                <h5>${plot.name}</h5>
                                <p>Beautiful vineyard plot in Luxembourg</p>
                                <button class="btn btn-sm btn-primary">Select This Plot</button>
                            </div>
                        `);
                    });
                }
            })
            .catch(error => {
                console.error('❌ Error loading plot data:', error);
                
                // Add fallback sample markers
                const fallbackPlots = [
                    { lat: 49.6116, lng: 6.1319, name: "Sample Plot 1" },
                    { lat: 49.6216, lng: 6.1419, name: "Sample Plot 2" },
                    { lat: 49.6016, lng: 6.1219, name: "Sample Plot 3" }
                ];
                
                fallbackPlots.forEach(plot => {
                    L.marker([plot.lat, plot.lng], {
                        icon: luxuryIcon,
                        title: plot.name
                    }).addTo(map).bindPopup(`<h5>${plot.name}</h5>`);
                });
            });

        // Handle plot selection clicks
        document.addEventListener('click', function(e) {
            if (e.target.classList.contains('select-plot-btn')) {
                const plotId = e.target.dataset.plotId;
                console.log('🍷 Plot selected:', plotId);
                
                // Add visual feedback
                e.target.textContent = 'Selected ✓';
                e.target.classList.remove('btn-primary');
                e.target.classList.add('btn-success');
                e.target.disabled = true;
                
                // You can add more selection logic here
                // For example, updating a cart or sending to API
            }
        });

        // Add custom CSS for luxury markers
        const style = document.createElement('style');
        style.textContent = `
            .luxury-plot-marker {
                background: linear-gradient(135deg, #D4AF37 0%, #B8860B 100%);
                border-radius: 50% 50% 50% 0;
                transform: rotate(-45deg);
                border: 2px solid #722F37;
                box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            }
            .luxury-plot-marker .marker-inner {
                transform: rotate(45deg);
                display: flex;
                align-items: center;
                justify-content: center;
                width: 100%;
                height: 100%;
                color: #722F37;
                font-size: 18px;
            }
            .plot-popup {
                min-width: 200px;
            }
            .plot-popup h5 {
                color: #722F37;
                margin-bottom: 10px;
            }
            .plot-popup p {
                margin: 5px 0;
                font-size: 14px;
            }
            .leaflet-popup-content-wrapper {
                border-radius: 8px;
                box-shadow: 0 3px 14px rgba(0,0,0,0.2);
            }
        `;
        document.head.appendChild(style);

        console.log('✅ VinsDelux vineyard map initialized successfully');
        
        // Store map instance globally for debugging
        window.vinsDeluxMap = map;
    });
})();