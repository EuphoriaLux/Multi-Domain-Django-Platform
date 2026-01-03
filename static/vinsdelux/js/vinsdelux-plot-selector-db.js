/**
 * VinsDelux Plot Selector - Database Integration Version
 * Uses Django adoption plans from database
 */

class VinsDeluxPlotSelectorDB {
    constructor(plotData = []) {
        this.selectedPlan = null;
        this.adoptionPlans = plotData; // Data from Django backend
        this.mapScale = 1;
        this.mapOffset = { x: 0, y: 0 };
        this.isDragging = false;
        this.dragStart = { x: 0, y: 0 };
        
        this.init();
    }
    
    init() {
        if (this.adoptionPlans.length > 0) {
            this.renderPlotCards();
            this.setupMap();
            this.setupEventListeners();
            this.setupFilters();
        } else {
            this.loadAdoptionPlans();
        }
    }
    
    async loadAdoptionPlans() {
        try {
            // Get the current language prefix from URL
            const pathParts = window.location.pathname.split('/');
            const langPrefix = pathParts[1] && pathParts[1].length === 2 ? `/${pathParts[1]}` : '';
            
            const response = await fetch(`${langPrefix}/vinsdelux/api/adoption-plans/`);
            const data = await response.json();
            this.adoptionPlans = data.adoption_plans;
            this.renderPlotCards();
            this.setupMap();
            this.setupEventListeners();
            this.setupFilters();
        } catch (error) {
            console.error('Failed to load adoption plans:', error);
            this.showError('Failed to load vineyard plots. Please refresh the page.');
        }
    }
    
    renderPlotCards() {
        const container = document.querySelector('.plot-cards-container');
        if (!container) return;
        
        container.innerHTML = '';
        
        if (this.adoptionPlans.length === 0) {
            container.innerHTML = '<p class="no-plots-message">No adoption plans available at this time.</p>';
            return;
        }
        
        this.adoptionPlans.forEach(plan => {
            const card = this.createPlanCard(plan);
            container.appendChild(card);
        });
    }
    
    createPlanCard(plan) {
        const card = document.createElement('div');
        card.className = `plot-card adoption-plan-card ${this.selectedPlan === plan.id ? 'selected' : ''}`;
        card.dataset.planId = plan.id || plan.plan_id;
        
        // Format price
        const price = typeof plan.price === 'string' ? plan.price : `€${plan.price}`;
        
        // Extract features
        const features = [];
        if (plan.includes_visit) features.push('Vineyard Visit');
        if (plan.includes_medallion) features.push('Personalized Medallion');
        if (plan.includes_club_membership) features.push('Club Membership');
        
        card.innerHTML = `
            <div class="plot-card-header">
                <div class="plot-availability available">
                    Available
                </div>
                <div class="producer-badge">
                    ${plan.producer_name || 'Unknown Producer'}
                </div>
            </div>
            
            <div class="plot-card-image">
                ${plan.main_image ? 
                    `<img src="${plan.main_image}" alt="${plan.name}" onerror="this.src='/static/images/journey/step_01.png'">` :
                    `<div class="image-placeholder" style="background: linear-gradient(135deg, #8BC34A, #689F38); height: 200px; display: flex; align-items: center; justify-content: center; color: white; font-size: 18px;">
                        <i class="fas fa-wine-bottle" style="font-size: 48px;"></i>
                    </div>`
                }
                <div class="plot-region-badge">${plan.region || 'France'}</div>
            </div>
            
            <div class="plot-card-body">
                <h3 class="plot-name">${plan.name}</h3>
                <p class="plot-description">${plan.description || 'Experience the finest wine adoption plan'}</p>
                
                <div class="plan-details">
                    <div class="detail-item">
                        <i class="fas fa-clock"></i>
                        <span>${plan.duration_months || 12} months</span>
                    </div>
                    <div class="detail-item">
                        <i class="fas fa-wine-bottle"></i>
                        <span>${plan.coffrets_per_year || 1} coffrets/year</span>
                    </div>
                    ${plan.coffret_name ? `
                    <div class="detail-item">
                        <i class="fas fa-box"></i>
                        <span>${plan.coffret_name}</span>
                    </div>` : ''}
                    ${plan.category ? `
                    <div class="detail-item">
                        <i class="fas fa-tag"></i>
                        <span>${plan.category}</span>
                    </div>` : ''}
                </div>
                
                ${features.length > 0 ? `
                <div class="plot-features">
                    ${features.map(feature => `
                        <div class="feature-badge">
                            <i class="fas fa-check-circle"></i>
                            ${feature}
                        </div>
                    `).join('')}
                </div>` : ''}
                
                ${plan.visit_details ? `
                <div class="visit-details">
                    <i class="fas fa-info-circle"></i>
                    <small>${plan.visit_details}</small>
                </div>` : ''}
                
                <div class="plot-card-footer">
                    <div class="plot-price">
                        <span class="price-label">Adoption Plan</span>
                        <span class="price-value">${price}</span>
                    </div>
                    <div class="plot-actions">
                        <button class="btn-view-details" data-plan-id="${plan.id || plan.plan_id}">
                            <i class="fas fa-eye"></i>
                            Details
                        </button>
                        <button class="btn-select-plan" data-plan-id="${plan.id || plan.plan_id}">
                            ${this.selectedPlan === (plan.id || plan.plan_id) ? 'Selected ✓' : 'Select Plan'}
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        // Add event listeners
        const selectBtn = card.querySelector('.btn-select-plan');
        if (selectBtn) {
            selectBtn.addEventListener('click', () => this.selectPlan(plan.id || plan.plan_id));
        }
        
        const detailsBtn = card.querySelector('.btn-view-details');
        if (detailsBtn) {
            detailsBtn.addEventListener('click', () => this.showPlanDetails(plan));
        }
        
        return card;
    }
    
    setupMap() {
        const mapCanvas = document.getElementById('map-canvas');
        if (!mapCanvas || mapCanvas.tagName !== 'CANVAS') return;
        
        const ctx = mapCanvas.getContext('2d');
        if (!ctx) return;
        
        // Set canvas size
        mapCanvas.width = 800;
        mapCanvas.height = 600;
        
        this.drawMap(ctx);
        this.drawPlanMarkers(ctx);
        
        // Add interactivity
        mapCanvas.addEventListener('click', (e) => this.handleMapClick(e));
        mapCanvas.addEventListener('mousemove', (e) => this.handleMapHover(e));
        mapCanvas.addEventListener('wheel', (e) => this.handleMapZoom(e));
    }
    
    drawMap(ctx) {
        ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
        ctx.save();
        
        ctx.translate(this.mapOffset.x, this.mapOffset.y);
        ctx.scale(this.mapScale, this.mapScale);
        
        // Draw background
        const gradient = ctx.createLinearGradient(0, 0, 0, 600);
        gradient.addColorStop(0, '#87CEEB');
        gradient.addColorStop(0.3, '#98D8E8');
        gradient.addColorStop(0.6, '#90EE90');
        gradient.addColorStop(1, '#8B7355');
        
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, 800, 600);
        
        // Draw vineyard rows
        ctx.strokeStyle = '#6B8E23';
        ctx.lineWidth = 2;
        for (let y = 300; y < 600; y += 30) {
            ctx.beginPath();
            ctx.moveTo(0, y);
            for (let x = 0; x < 800; x += 20) {
                ctx.lineTo(x + 10, y - 5);
                ctx.lineTo(x + 20, y);
            }
            ctx.stroke();
        }
        
        // Draw producer regions if we have them
        this.drawProducerRegions(ctx);
        
        ctx.restore();
    }
    
    drawProducerRegions(ctx) {
        // Group plans by producer/region
        const regionMap = new Map();
        
        this.adoptionPlans.forEach(plan => {
            const region = plan.region || plan.producer_name || 'Unknown';
            if (!regionMap.has(region)) {
                regionMap.set(region, []);
            }
            regionMap.get(region).push(plan);
        });
        
        // Draw regions
        let regionIndex = 0;
        const colors = ['rgba(114, 47, 55, 0.2)', 'rgba(139, 69, 19, 0.2)', 'rgba(160, 82, 45, 0.2)', 'rgba(218, 165, 32, 0.2)', 'rgba(212, 175, 55, 0.2)'];
        
        regionMap.forEach((plans, regionName) => {
            const x = 100 + (regionIndex % 3) * 250;
            const y = 100 + Math.floor(regionIndex / 3) * 200;
            const width = 200;
            const height = 150;
            const color = colors[regionIndex % colors.length];
            
            // Draw region area
            ctx.fillStyle = color;
            ctx.fillRect(x, y, width, height);
            
            // Draw region border
            ctx.strokeStyle = color.replace('0.2', '0.5');
            ctx.strokeRect(x, y, width, height);
            
            // Draw region label
            ctx.fillStyle = '#333';
            ctx.font = 'bold 14px Arial';
            ctx.textAlign = 'center';
            ctx.fillText(regionName, x + width/2, y - 10);
            
            regionIndex++;
        });
    }
    
    drawPlanMarkers(ctx) {
        ctx.save();
        ctx.translate(this.mapOffset.x, this.mapOffset.y);
        ctx.scale(this.mapScale, this.mapScale);
        
        // Position markers based on producer regions
        const regionPositions = new Map();
        let positionIndex = 0;
        
        this.adoptionPlans.forEach((plan, index) => {
            const region = plan.region || plan.producer_name || 'Unknown';
            
            if (!regionPositions.has(region)) {
                regionPositions.set(region, {
                    x: 200 + (positionIndex % 3) * 250,
                    y: 175 + Math.floor(positionIndex / 3) * 200,
                    count: 0
                });
                positionIndex++;
            }
            
            const regionPos = regionPositions.get(region);
            const x = regionPos.x + (regionPos.count % 3) * 60 - 60;
            const y = regionPos.y + Math.floor(regionPos.count / 3) * 60;
            regionPos.count++;
            
            const isSelected = this.selectedPlan === (plan.id || plan.plan_id);
            
            // Draw marker shadow
            ctx.beginPath();
            ctx.arc(x + 2, y + 2, 18, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(0, 0, 0, 0.2)';
            ctx.fill();
            
            // Draw marker
            ctx.beginPath();
            ctx.arc(x, y, isSelected ? 22 : 18, 0, Math.PI * 2);
            ctx.fillStyle = isSelected ? '#d4af37' : '#722f37';
            ctx.fill();
            
            // Draw marker border
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = isSelected ? 3 : 2;
            ctx.stroke();
            
            // Draw plot number
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 10px Arial';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(`${index + 1}`, x, y);
            
            // Store position for click detection
            plan._mapX = x;
            plan._mapY = y;
        });
        
        ctx.restore();
    }
    
    handleMapClick(e) {
        const rect = e.target.getBoundingClientRect();
        const x = (e.clientX - rect.left - this.mapOffset.x) / this.mapScale;
        const y = (e.clientY - rect.top - this.mapOffset.y) / this.mapScale;
        
        // Check if click is on a plan marker
        const clickedPlan = this.adoptionPlans.find(plan => {
            if (!plan._mapX || !plan._mapY) return false;
            const distance = Math.sqrt(
                Math.pow(x - plan._mapX, 2) + 
                Math.pow(y - plan._mapY, 2)
            );
            return distance < 25;
        });
        
        if (clickedPlan) {
            this.selectPlan(clickedPlan.id || clickedPlan.plan_id);
        }
    }
    
    handleMapHover(e) {
        const rect = e.target.getBoundingClientRect();
        const x = (e.clientX - rect.left - this.mapOffset.x) / this.mapScale;
        const y = (e.clientY - rect.top - this.mapOffset.y) / this.mapScale;
        
        const hoveredPlan = this.adoptionPlans.find(plan => {
            if (!plan._mapX || !plan._mapY) return false;
            const distance = Math.sqrt(
                Math.pow(x - plan._mapX, 2) + 
                Math.pow(y - plan._mapY, 2)
            );
            return distance < 25;
        });
        
        if (hoveredPlan) {
            e.target.style.cursor = 'pointer';
            this.showPlanTooltip(hoveredPlan, e.clientX, e.clientY);
        } else {
            e.target.style.cursor = 'default';
            this.hideTooltip();
        }
    }
    
    handleMapZoom(e) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        this.zoomMap(delta);
    }
    
    zoomMap(factor) {
        const newScale = Math.max(0.5, Math.min(3, this.mapScale * factor));
        this.mapScale = newScale;
        this.redrawMap();
    }
    
    resetMap() {
        this.mapScale = 1;
        this.mapOffset = { x: 0, y: 0 };
        this.redrawMap();
    }
    
    redrawMap() {
        const canvas = document.getElementById('map-canvas');
        if (canvas && canvas.getContext) {
            const ctx = canvas.getContext('2d');
            this.drawMap(ctx);
            this.drawPlanMarkers(ctx);
        }
    }
    
    selectPlan(planId) {
        const plan = this.adoptionPlans.find(p => (p.id || p.plan_id) === planId);
        if (!plan) return;
        
        this.selectedPlan = planId;
        
        // Update cards
        document.querySelectorAll('.plot-card').forEach(card => {
            const cardPlanId = card.dataset.planId;
            card.classList.toggle('selected', cardPlanId == planId);
            const btn = card.querySelector('.btn-select-plan');
            if (btn) {
                btn.textContent = cardPlanId == planId ? 'Selected ✓' : 'Select Plan';
            }
        });
        
        // Update map
        this.redrawMap();
        
        // Show detailed panel
        this.showPlanDetails(plan);
        
        // Trigger selection event
        this.onPlanSelected(plan);
    }
    
    showPlanDetails(plan) {
        const panel = document.getElementById('plot-details-panel');
        if (!panel) return;
        
        const features = [];
        if (plan.includes_visit) features.push({ icon: 'fa-map-marked-alt', text: 'Vineyard Visit Included' });
        if (plan.includes_medallion) features.push({ icon: 'fa-medal', text: 'Personalized Medallion' });
        if (plan.includes_club_membership) features.push({ icon: 'fa-users', text: 'Club Membership' });
        
        panel.innerHTML = `
            <div class="plot-details-header">
                <button class="close-panel" onclick="document.getElementById('plot-details-panel').classList.remove('active')">×</button>
                <h2>${plan.name}</h2>
                <span class="region-badge">${plan.region || 'France'}</span>
            </div>
            
            <div class="plot-details-content">
                <div class="producer-info">
                    <h3>Producer</h3>
                    <p>${plan.producer_name || 'Traditional Vineyard'}</p>
                </div>
                
                <div class="details-section">
                    <h3>Plan Details</h3>
                    <div class="characteristics-grid">
                        <div class="characteristic">
                            <label>Duration:</label>
                            <span>${plan.duration_months || 12} months</span>
                        </div>
                        <div class="characteristic">
                            <label>Coffrets per year:</label>
                            <span>${plan.coffrets_per_year || 1}</span>
                        </div>
                        ${plan.coffret_name ? `
                        <div class="characteristic">
                            <label>Coffret:</label>
                            <span>${plan.coffret_name}</span>
                        </div>` : ''}
                        ${plan.category ? `
                        <div class="characteristic">
                            <label>Wine Type:</label>
                            <span>${plan.category}</span>
                        </div>` : ''}
                    </div>
                </div>
                
                <div class="details-section">
                    <h3>Description</h3>
                    <p>${plan.full_description || plan.description || 'Experience the finest wine adoption plan with exclusive benefits.'}</p>
                </div>
                
                ${features.length > 0 ? `
                <div class="details-section">
                    <h3>Included Features</h3>
                    <ul class="features-list">
                        ${features.map(feature => `
                            <li><i class="fas ${feature.icon}"></i> ${feature.text}</li>
                        `).join('')}
                    </ul>
                </div>` : ''}
                
                ${plan.visit_details ? `
                <div class="details-section">
                    <h3>Visit Details</h3>
                    <p>${plan.visit_details}</p>
                </div>` : ''}
                
                ${plan.welcome_kit_description ? `
                <div class="details-section">
                    <h3>Welcome Kit</h3>
                    <p>${plan.welcome_kit_description}</p>
                </div>` : ''}
                
                <div class="details-section">
                    <h3>Investment</h3>
                    <div class="investment-info">
                        <div class="investment-item">
                            <span class="label">Plan Price:</span>
                            <span class="value">€${plan.price}</span>
                        </div>
                        ${plan.avant_premiere_price ? `
                        <div class="investment-item">
                            <span class="label">Avant-Première Option:</span>
                            <span class="value">${plan.avant_premiere_price}</span>
                        </div>` : ''}
                    </div>
                </div>
                
                <button class="btn-confirm-selection" onclick="plotSelectorDB.confirmSelection()">
                    Confirm Selection & Continue
                </button>
            </div>
        `;
        
        panel.classList.add('active');
    }
    
    showPlanTooltip(plan, x, y) {
        let tooltip = document.getElementById('plot-tooltip');
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = 'plot-tooltip';
            tooltip.className = 'plot-tooltip';
            document.body.appendChild(tooltip);
        }
        
        const price = typeof plan.price === 'string' ? plan.price : `€${plan.price}`;
        
        tooltip.innerHTML = `
            <strong>${plan.name}</strong><br>
            ${plan.producer_name || 'Producer'}<br>
            ${plan.region || 'France'}<br>
            ${plan.duration_months || 12} months<br>
            ${price}
        `;
        
        tooltip.style.left = x + 10 + 'px';
        tooltip.style.top = y + 10 + 'px';
        tooltip.style.display = 'block';
    }
    
    hideTooltip() {
        const tooltip = document.getElementById('plot-tooltip');
        if (tooltip) {
            tooltip.style.display = 'none';
        }
    }
    
    setupFilters() {
        // Get unique regions from adoption plans
        const regions = [...new Set(this.adoptionPlans.map(p => p.region || p.producer_name).filter(Boolean))];
        
        const filterContainer = document.createElement('div');
        filterContainer.className = 'plot-filters';
        filterContainer.innerHTML = `
            <div class="filter-group">
                <label>Producer/Region:</label>
                <select id="filter-region">
                    <option value="">All Regions</option>
                    ${regions.map(region => `<option value="${region}">${region}</option>`).join('')}
                </select>
            </div>
            
            <div class="filter-group">
                <label>Wine Type:</label>
                <select id="filter-category">
                    <option value="">All Types</option>
                    <option value="Red Wine">Red Wine</option>
                    <option value="White Wine">White Wine</option>
                    <option value="Rosé">Rosé</option>
                    <option value="Sparkling Wine">Sparkling Wine</option>
                </select>
            </div>
            
            <div class="filter-group">
                <label>Features:</label>
                <div class="feature-checkboxes">
                    <label><input type="checkbox" value="visit"> Vineyard Visit</label>
                    <label><input type="checkbox" value="medallion"> Medallion</label>
                    <label><input type="checkbox" value="club"> Club Membership</label>
                </div>
            </div>
            
            <button class="btn-reset-filters">Reset Filters</button>
        `;
        
        const mapContainer = document.querySelector('.map-container');
        if (mapContainer && !document.querySelector('.plot-filters')) {
            mapContainer.parentNode.insertBefore(filterContainer, mapContainer);
        }
        
        // Add filter event listeners
        filterContainer.addEventListener('change', () => this.applyFilters());
        filterContainer.querySelector('.btn-reset-filters')?.addEventListener('click', () => this.resetFilters());
    }
    
    applyFilters() {
        const region = document.getElementById('filter-region')?.value;
        const category = document.getElementById('filter-category')?.value;
        const visitChecked = document.querySelector('input[value="visit"]')?.checked;
        const medallionChecked = document.querySelector('input[value="medallion"]')?.checked;
        const clubChecked = document.querySelector('input[value="club"]')?.checked;
        
        // Filter plans
        const filteredPlans = this.adoptionPlans.filter(plan => {
            if (region && plan.region !== region && plan.producer_name !== region) return false;
            if (category && plan.category !== category) return false;
            if (visitChecked && !plan.includes_visit) return false;
            if (medallionChecked && !plan.includes_medallion) return false;
            if (clubChecked && !plan.includes_club_membership) return false;
            return true;
        });
        
        // Update display
        const container = document.querySelector('.plot-cards-container');
        if (container) {
            container.innerHTML = '';
            filteredPlans.forEach(plan => {
                const card = this.createPlanCard(plan);
                container.appendChild(card);
            });
        }
    }
    
    resetFilters() {
        document.querySelectorAll('.plot-filters select').forEach(select => select.value = '');
        document.querySelectorAll('.plot-filters input[type="checkbox"]').forEach(cb => cb.checked = false);
        this.renderPlotCards();
    }
    
    setupEventListeners() {
        // Map controls
        document.querySelector('.map-zoom-in')?.addEventListener('click', () => this.zoomMap(1.2));
        document.querySelector('.map-zoom-out')?.addEventListener('click', () => this.zoomMap(0.8));
        document.querySelector('.map-reset')?.addEventListener('click', () => this.resetMap());
    }
    
    confirmSelection() {
        if (!this.selectedPlan) {
            alert('Please select an adoption plan first');
            return;
        }
        
        const plan = this.adoptionPlans.find(p => (p.id || p.plan_id) === this.selectedPlan);
        console.log('Adoption plan confirmed:', plan);
        
        // Save selection
        localStorage.setItem('selected_adoption_plan', JSON.stringify(plan));
        
        // Trigger next step if game instance exists
        if (window.gameInstance) {
            window.gameInstance.journeyData.plot = this.selectedPlan;
            window.gameInstance.journeyData.adoptionPlan = plan;
            window.gameInstance.nextStep();
        }
        
        // Close panel
        document.getElementById('plot-details-panel')?.classList.remove('active');
    }
    
    onPlanSelected(plan) {
        // Custom event for plan selection
        const event = new CustomEvent('adoptionPlanSelected', { detail: plan });
        window.dispatchEvent(event);
    }
    
    showError(message) {
        const container = document.querySelector('.plot-cards-container');
        if (container) {
            container.innerHTML = `<div class="error-message">${message}</div>`;
        }
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        // Check if we have plot data from Django
        if (window.plotData) {
            window.plotSelectorDB = new VinsDeluxPlotSelectorDB(window.plotData);
        } else {
            window.plotSelectorDB = new VinsDeluxPlotSelectorDB();
        }
    });
} else {
    if (window.plotData) {
        window.plotSelectorDB = new VinsDeluxPlotSelectorDB(window.plotData);
    } else {
        window.plotSelectorDB = new VinsDeluxPlotSelectorDB();
    }
}