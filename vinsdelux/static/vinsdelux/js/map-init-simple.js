/**
 * Simple Map Initialization - Shows existing producers as plots
 * No new data creation - just display what's in the database
 */

(function() {
    'use strict';

    document.addEventListener('DOMContentLoaded', function() {
        // Skip if enhanced map is already handling initialization
        if (window.VinsDeluxEnhancedMap) {
            console.log('📍 Enhanced map active, simple map functions only');
            // Don't initialize map, just set up the adoption plan loading functions
            setupAdoptionPlanHandlers();
            return;
        }
        
        console.log('🗺️ Initializing producer map...');
        
        if (typeof L === 'undefined') {
            console.error('❌ Leaflet.js is not loaded');
            return;
        }

        const mapContainer = document.getElementById('vineyard-map');
        if (!mapContainer) {
            console.warn('⚠️ Map container not found');
            return;
        }

        // Check if map is already initialized
        if (mapContainer._leaflet_id) {
            console.log('✓ Map already initialized');
            setupAdoptionPlanHandlers();
            return;
        }

        // Initialize map centered on Luxembourg
        const map = L.map('vineyard-map', {
            center: [49.6116, 6.1319], // Luxembourg center
            zoom: 9,
            scrollWheelZoom: false
        });

        // Add map tiles
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors',
            maxZoom: 18
        }).addTo(map);

        // Enable scroll zoom on focus
        map.on('focus', function() {
            map.scrollWheelZoom.enable();
        });
        map.on('blur', function() {
            map.scrollWheelZoom.disable();
        });

        // Custom wine bottle icon
        const wineIcon = L.divIcon({
            className: 'wine-producer-marker',
            html: '<div class="marker-inner"><i class="fas fa-wine-bottle"></i></div>',
            iconSize: [40, 40],
            iconAnchor: [20, 40]
        });

        // Fetch existing producers and coffrets from the API
        const langCode = document.documentElement.lang || 'en';
        
        // First, let's try to get producers directly
        fetch(`/${langCode}/vinsdelux/api/producers/`)
            .then(response => {
                if (!response.ok) {
                    // If no producer API, use the adoption plans API as fallback
                    return fetch(`/${langCode}/vinsdelux/api/adoption-plans/`);
                }
                return response;
            })
            .then(response => response.json())
            .then(data => {
                console.log('📍 Loading producer data:', data);
                
                // Luxembourg wine region coordinates for each producer
                const luxembourgLocations = [
                    { lat: 49.6116, lng: 6.1319, area: "Luxembourg City" },      // Château Margaux
                    { lat: 49.5447, lng: 6.3669, area: "Remich" },              // Romanée-Conti
                    { lat: 49.6803, lng: 6.4417, area: "Grevenmacher" },        // Penfolds
                    { lat: 49.6114, lng: 6.4003, area: "Wormeldange" },         // Antinori
                    { lat: 49.4697, lng: 6.3622, area: "Schengen" }             // Catena Zapata
                ];

                // If we have adoption plans data, use it to show producers
                if (data.adoption_plans && Array.isArray(data.adoption_plans)) {
                    data.plans = data.adoption_plans; // Normalize the data structure
                }
                
                if (data.plans && Array.isArray(data.plans)) {
                    data.plans.forEach((plan, index) => {
                        const location = luxembourgLocations[index % luxembourgLocations.length];
                        
                        const marker = L.marker([location.lat, location.lng], {
                            icon: wineIcon,
                            title: plan.producer_name || plan.name
                        }).addTo(map);

                        const producerName = plan.producer ? plan.producer.name : (plan.producer_name || plan.name);
                        const popupContent = `
                            <div class="producer-popup">
                                <h5>${producerName}</h5>
                                <p><strong>Origin:</strong> ${plan.producer ? plan.producer.region : 'Premium Wine Region'}</p>
                                <p><strong>Location:</strong> ${location.area}, Luxembourg</p>
                                <p><strong>Adoption Plan:</strong> ${plan.name}</p>
                                <p><strong>Price:</strong> €${plan.price}</p>
                                <p><strong>Duration:</strong> ${plan.duration_months} months</p>
                                ${plan.description ? `<p>${plan.description}</p>` : ''}
                                <button class="btn btn-sm btn-primary select-producer-btn" 
                                        data-producer="${producerName}"
                                        data-plan-id="${plan.id}">
                                    <i class="fas fa-wine-glass"></i> Select This Producer
                                </button>
                            </div>
                        `;
                        marker.bindPopup(popupContent);
                    });
                } else {
                    // Fallback: Show the 5 known producers directly
                    const producers = [
                        { name: "Château Margaux", region: "Bordeaux, France", specialty: "Cabernet Sauvignon blends" },
                        { name: "Domaine de la Romanée-Conti", region: "Burgundy, France", specialty: "Pinot Noir" },
                        { name: "Penfolds", region: "South Australia", specialty: "Shiraz" },
                        { name: "Antinori", region: "Tuscany, Italy", specialty: "Sangiovese" },
                        { name: "Catena Zapata", region: "Mendoza, Argentina", specialty: "Malbec" }
                    ];

                    producers.forEach((producer, index) => {
                        const location = luxembourgLocations[index];
                        
                        const marker = L.marker([location.lat, location.lng], {
                            icon: wineIcon,
                            title: producer.name
                        }).addTo(map);

                        const popupContent = `
                            <div class="producer-popup">
                                <h5>${producer.name}</h5>
                                <p><strong>Origin:</strong> ${producer.region}</p>
                                <p><strong>Luxembourg Location:</strong> ${location.area}</p>
                                <p><strong>Specialty:</strong> ${producer.specialty}</p>
                                <button class="btn btn-sm btn-primary select-producer-btn" 
                                        data-producer="${producer.name}">
                                    <i class="fas fa-wine-glass"></i> Select This Producer
                                </button>
                            </div>
                        `;
                        marker.bindPopup(popupContent);
                    });
                }
            })
            .catch(error => {
                console.error('❌ Error loading data:', error);
                
                // Show the 5 producers even if API fails
                const producers = [
                    { name: "Château Margaux", lat: 49.6116, lng: 6.1319 },
                    { name: "Domaine de la Romanée-Conti", lat: 49.5447, lng: 6.3669 },
                    { name: "Penfolds", lat: 49.6803, lng: 6.4417 },
                    { name: "Antinori", lat: 49.6114, lng: 6.4003 },
                    { name: "Catena Zapata", lat: 49.4697, lng: 6.3622 }
                ];

                producers.forEach(producer => {
                    L.marker([producer.lat, producer.lng], {
                        icon: wineIcon,
                        title: producer.name
                    }).addTo(map).bindPopup(`<h5>${producer.name}</h5>`);
                });
            });

        // Handle producer selection
        document.addEventListener('click', function(e) {
            if (e.target.classList.contains('select-producer-btn')) {
                const producer = e.target.dataset.producer;
                const planId = e.target.dataset.planId;
                console.log('🍷 Producer selected:', producer);
                
                // Update button state
                e.target.textContent = 'Selected ✓';
                e.target.classList.remove('btn-primary');
                e.target.classList.add('btn-success');
                e.target.disabled = true;
                
                // Save selection to session storage
                sessionStorage.setItem('selectedProducer', producer);
                if (planId) {
                    sessionStorage.setItem('selectedPlanId', planId);
                }
                
                // Update the sidebar to show selected producer
                const selectionDisplay = document.getElementById('selected-producer-display');
                if (selectionDisplay) {
                    selectionDisplay.innerHTML = `
                        <div class="alert alert-success">
                            <h6><i class="fas fa-check-circle"></i> Producer Selected</h6>
                            <p><strong>${producer}</strong></p>
                        </div>
                    `;
                }
                
                // Show adoption plan section after a short delay
                setTimeout(function() {
                    // Scroll to adoption plan section
                    const adoptionSection = document.getElementById('adoption-plan-section');
                    if (adoptionSection) {
                        adoptionSection.style.display = 'block';
                        adoptionSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
                        
                        // Load adoption plans for this producer
                        loadAdoptionPlans(producer);
                    }
                    
                    // Update progress indicator
                    updateProgress(2); // Move to step 2
                }, 500);
            }
        });
        
        // Function to load adoption plans
        function loadAdoptionPlans(producerName) {
            const planContainer = document.getElementById('adoption-plans-container');
            if (!planContainer) return;
            
            // Fetch adoption plans from API
            const langCode = document.documentElement.lang || 'en';
            fetch(`/${langCode}/vinsdelux/api/adoption-plans/`)
                .then(response => response.json())
                .then(data => {
                    // Handle both data.plans and data.adoption_plans formats
                    const plans = data.plans || data.adoption_plans;
                    if (plans) {
                        // Filter plans for selected producer ONLY
                        console.log('Looking for plans for producer:', producerName);
                        const relevantPlans = plans.filter(plan => {
                            // Check if plan has producer info and match by name
                            if (plan.producer && plan.producer.name) {
                                // Handle special characters in names (like Château)
                                const planProducerName = plan.producer.name;
                                console.log('Comparing:', planProducerName, 'with', producerName);
                                return planProducerName === producerName || 
                                       planProducerName.includes(producerName) ||
                                       producerName.includes(planProducerName);
                            }
                            // Fallback to producer_name field
                            if (plan.producer_name) {
                                return plan.producer_name === producerName;
                            }
                            return false;
                        });
                        console.log('Found', relevantPlans.length, 'plans');
                        
                        if (relevantPlans.length === 0) {
                            // If no specific plan found, show a message
                            planContainer.innerHTML = `
                                <div class="alert alert-info">
                                    <h5>No specific adoption plan found for ${producerName}</h5>
                                    <p>Please contact us for custom adoption plans with this producer.</p>
                                </div>
                            `;
                            return;
                        }
                        
                        // If only one plan (expected), center it
                        const centerClass = relevantPlans.length === 1 ? 'justify-content-center' : '';
                        let plansHTML = `<div class="row ${centerClass}">`;
                        relevantPlans.forEach(plan => {
                            const coffrets = plan.coffrets_per_year || plan.bottles_per_year || 2;
                            plansHTML += `
                                <div class="col-md-6 mb-4">
                                    <div class="card adoption-plan-card h-100">
                                        <div class="card-body">
                                            <h5 class="card-title">${plan.name}</h5>
                                            <p class="card-text">${plan.description || 'Premium wine adoption plan'}</p>
                                            <ul class="list-unstyled">
                                                <li><strong>Duration:</strong> ${plan.duration_months} months</li>
                                                <li><strong>Price:</strong> €${plan.price}</li>
                                                <li><strong>Coffrets/Year:</strong> ${coffrets} wine collections</li>
                                                ${plan.producer ? `<li><strong>Producer:</strong> ${plan.producer.name}</li>` : ''}
                                                ${plan.producer && plan.producer.region ? `<li><strong>Region:</strong> ${plan.producer.region}</li>` : ''}
                                            </ul>
                                            ${plan.features && plan.features.includes_visit ? '<p class="text-success"><i class="fas fa-check"></i> Includes vineyard visit</p>' : ''}
                                            ${plan.features && plan.features.includes_medallion ? '<p class="text-success"><i class="fas fa-check"></i> Personalized medallion</p>' : ''}
                                            <button class="btn btn-primary select-plan-btn mt-3" 
                                                    data-plan-id="${plan.id}"
                                                    data-plan-name="${plan.name}">
                                                <i class="fas fa-wine-bottle"></i> Select This Plan
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            `;
                        });
                        plansHTML += '</div>';
                        planContainer.innerHTML = plansHTML;
                    }
                })
                .catch(error => {
                    console.error('Error loading adoption plans:', error);
                    planContainer.innerHTML = '<p>Error loading adoption plans. Please try again.</p>';
                });
        }
        
        // Function to update progress indicator
        function updateProgress(step) {
            const steps = document.querySelectorAll('.progress-step');
            steps.forEach((stepEl, index) => {
                if (index < step) {
                    stepEl.classList.add('completed');
                    stepEl.classList.remove('active');
                } else if (index === step - 1) {
                    stepEl.classList.add('active');
                } else {
                    stepEl.classList.remove('completed', 'active');
                }
            });
        }

        // Add custom CSS for markers
        const style = document.createElement('style');
        style.textContent = `
            .wine-producer-marker {
                background: linear-gradient(135deg, #722F37 0%, #8B0000 100%);
                border-radius: 50% 50% 50% 0;
                transform: rotate(-45deg);
                border: 2px solid #D4AF37;
                box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            }
            .wine-producer-marker .marker-inner {
                transform: rotate(45deg);
                display: flex;
                align-items: center;
                justify-content: center;
                width: 100%;
                height: 100%;
                color: #D4AF37;
                font-size: 18px;
            }
            .producer-popup {
                min-width: 250px;
            }
            .producer-popup h5 {
                color: #722F37;
                margin-bottom: 10px;
                font-weight: bold;
            }
        `;
        document.head.appendChild(style);

        console.log('✅ Producer map initialized');
        window.producerMap = map;
        
        // Set up adoption plan handlers
        setupAdoptionPlanHandlers();
    });
    
    // Separate function for adoption plan handlers
    function setupAdoptionPlanHandlers() {
        // Listen for custom event from enhanced map
        document.addEventListener('loadAdoptionPlans', function(e) {
            const producerName = e.detail.producer;
            loadAdoptionPlans(producerName);
        });
        
        // Make loadAdoptionPlans globally accessible
        window.loadAdoptionPlans = loadAdoptionPlans;
    }
})();