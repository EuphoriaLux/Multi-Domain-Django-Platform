/**
 * VinsDelux Enhanced Plot Selector
 * Interactive vineyard plot selection system
 */

class VinsDeluxPlotSelector {
    constructor() {
        this.selectedPlot = null;
        this.plots = [
            {
                id: 'plot-1',
                name: 'Château Heights',
                region: 'Bordeaux',
                size: '2.5 hectares',
                elevation: '250m',
                soil: 'Clay-limestone',
                exposure: 'South-facing',
                grapes: ['Cabernet Sauvignon', 'Merlot'],
                price: '€25,000/year',
                available: true,
                coordinates: { x: 150, y: 200 },
                description: 'Premium hillside location with exceptional drainage and sun exposure. Perfect for bold, structured wines.',
                features: ['Organic certified', 'Old vines (40+ years)', 'Traditional cultivation'],
                image: 'journey/step_01.png',
                rating: 4.8,
                reviews: 127
            },
            {
                id: 'plot-2',
                name: 'Valley Reserve',
                region: 'Burgundy',
                size: '1.8 hectares',
                elevation: '180m',
                soil: 'Limestone-marl',
                exposure: 'Southeast-facing',
                grapes: ['Pinot Noir', 'Chardonnay'],
                price: '€35,000/year',
                available: true,
                coordinates: { x: 350, y: 150 },
                description: 'Exclusive valley plot with ideal microclimate for elegant, refined wines. Historic vineyard site.',
                features: ['Biodynamic', 'Single vineyard', 'Grand Cru potential'],
                image: 'journey/step_01.png',
                rating: 4.9,
                reviews: 89
            },
            {
                id: 'plot-3',
                name: 'Riverside Terroir',
                region: 'Rhône Valley',
                size: '3.2 hectares',
                elevation: '120m',
                soil: 'Alluvial gravel',
                exposure: 'South-southwest',
                grapes: ['Syrah', 'Grenache', 'Mourvèdre'],
                price: '€18,000/year',
                available: true,
                coordinates: { x: 250, y: 350 },
                description: 'Riverside location with unique terroir. Produces concentrated, aromatic wines with distinctive character.',
                features: ['Sustainable farming', 'Modern irrigation', 'Young vines potential'],
                image: 'journey/step_01.png',
                rating: 4.6,
                reviews: 156
            },
            {
                id: 'plot-4',
                name: 'Mountain Ridge',
                region: 'Alsace',
                size: '1.2 hectares',
                elevation: '380m',
                soil: 'Granite-schist',
                exposure: 'East-facing',
                grapes: ['Riesling', 'Gewürztraminer'],
                price: '€22,000/year',
                available: false,
                coordinates: { x: 450, y: 280 },
                description: 'High-altitude plot with cool climate ideal for aromatic white wines. Stunning views and unique minerality.',
                features: ['Mountain terroir', 'Cool climate', 'Mineral-rich soil'],
                image: 'journey/step_01.png',
                rating: 4.7,
                reviews: 93
            },
            {
                id: 'plot-5',
                name: 'Sunset Vineyard',
                region: 'Champagne',
                size: '2.0 hectares',
                elevation: '200m',
                soil: 'Chalk',
                exposure: 'North-facing',
                grapes: ['Chardonnay', 'Pinot Noir', 'Pinot Meunier'],
                price: '€45,000/year',
                available: true,
                coordinates: { x: 180, y: 320 },
                description: 'Premier champagne terroir with deep chalk soils. Perfect for creating exceptional sparkling wines.',
                features: ['Champagne AOC', 'Chalk caves access', 'Premier Cru'],
                image: 'journey/step_01.png',
                rating: 5.0,
                reviews: 64
            }
        ];
        
        this.mapScale = 1;
        this.mapOffset = { x: 0, y: 0 };
        this.isDragging = false;
        this.dragStart = { x: 0, y: 0 };
        
        this.init();
    }
    
    init() {
        this.setupMap();
        this.renderPlotCards();
        this.setupEventListeners();
        this.setupFilters();
        this.initializeComparison();
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
        this.drawPlotMarkers(ctx);
        
        // Add interactivity
        mapCanvas.addEventListener('click', (e) => this.handleMapClick(e));
        mapCanvas.addEventListener('mousemove', (e) => this.handleMapHover(e));
        mapCanvas.addEventListener('wheel', (e) => this.handleMapZoom(e));
        
        // Touch support for mobile
        mapCanvas.addEventListener('touchstart', (e) => this.handleTouchStart(e));
        mapCanvas.addEventListener('touchmove', (e) => this.handleTouchMove(e));
        mapCanvas.addEventListener('touchend', (e) => this.handleTouchEnd(e));
    }
    
    drawMap(ctx) {
        // Clear canvas
        ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
        
        // Save state
        ctx.save();
        
        // Apply transformations
        ctx.translate(this.mapOffset.x, this.mapOffset.y);
        ctx.scale(this.mapScale, this.mapScale);
        
        // Draw background gradient (sky to ground)
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
        
        // Draw regions
        this.drawRegions(ctx);
        
        // Restore state
        ctx.restore();
    }
    
    drawRegions(ctx) {
        const regions = [
            { name: 'Bordeaux', x: 100, y: 150, width: 150, height: 120, color: 'rgba(114, 47, 55, 0.2)' },
            { name: 'Burgundy', x: 300, y: 100, width: 140, height: 110, color: 'rgba(139, 69, 19, 0.2)' },
            { name: 'Rhône', x: 200, y: 300, width: 130, height: 100, color: 'rgba(160, 82, 45, 0.2)' },
            { name: 'Alsace', x: 400, y: 230, width: 120, height: 100, color: 'rgba(218, 165, 32, 0.2)' },
            { name: 'Champagne', x: 130, y: 270, width: 140, height: 110, color: 'rgba(212, 175, 55, 0.2)' }
        ];
        
        regions.forEach(region => {
            // Draw region area
            ctx.fillStyle = region.color;
            ctx.fillRect(region.x, region.y, region.width, region.height);
            
            // Draw region border
            ctx.strokeStyle = region.color.replace('0.2', '0.5');
            ctx.strokeRect(region.x, region.y, region.width, region.height);
            
            // Draw region label
            ctx.fillStyle = '#333';
            ctx.font = 'bold 14px Arial';
            ctx.textAlign = 'center';
            ctx.fillText(region.name, region.x + region.width/2, region.y - 10);
        });
    }
    
    drawPlotMarkers(ctx) {
        ctx.save();
        ctx.translate(this.mapOffset.x, this.mapOffset.y);
        ctx.scale(this.mapScale, this.mapScale);
        
        this.plots.forEach(plot => {
            const isSelected = this.selectedPlot === plot.id;
            const x = plot.coordinates.x;
            const y = plot.coordinates.y;
            
            // Draw marker shadow
            ctx.beginPath();
            ctx.arc(x + 2, y + 2, 18, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(0, 0, 0, 0.2)';
            ctx.fill();
            
            // Draw marker
            ctx.beginPath();
            ctx.arc(x, y, isSelected ? 22 : 18, 0, Math.PI * 2);
            
            if (!plot.available) {
                ctx.fillStyle = '#999';
            } else if (isSelected) {
                ctx.fillStyle = '#d4af37';
            } else {
                ctx.fillStyle = '#722f37';
            }
            ctx.fill();
            
            // Draw marker border
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = isSelected ? 3 : 2;
            ctx.stroke();
            
            // Draw plot number
            ctx.fillStyle = '#fff';
            ctx.font = 'bold 12px Arial';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(plot.id.split('-')[1], x, y);
            
            // Draw plot name label
            if (isSelected || this.mapScale > 1.2) {
                ctx.fillStyle = '#333';
                ctx.font = 'bold 11px Arial';
                ctx.fillText(plot.name, x, y + 30);
            }
            
            // Draw availability indicator
            if (!plot.available) {
                ctx.beginPath();
                ctx.moveTo(x - 10, y - 10);
                ctx.lineTo(x + 10, y + 10);
                ctx.moveTo(x + 10, y - 10);
                ctx.lineTo(x - 10, y + 10);
                ctx.strokeStyle = '#ff0000';
                ctx.lineWidth = 2;
                ctx.stroke();
            }
        });
        
        ctx.restore();
    }
    
    handleMapClick(e) {
        const rect = e.target.getBoundingClientRect();
        const x = (e.clientX - rect.left - this.mapOffset.x) / this.mapScale;
        const y = (e.clientY - rect.top - this.mapOffset.y) / this.mapScale;
        
        // Check if click is on a plot marker
        const clickedPlot = this.plots.find(plot => {
            const distance = Math.sqrt(
                Math.pow(x - plot.coordinates.x, 2) + 
                Math.pow(y - plot.coordinates.y, 2)
            );
            return distance < 25;
        });
        
        if (clickedPlot && clickedPlot.available) {
            this.selectPlot(clickedPlot.id);
        }
    }
    
    handleMapHover(e) {
        const rect = e.target.getBoundingClientRect();
        const x = (e.clientX - rect.left - this.mapOffset.x) / this.mapScale;
        const y = (e.clientY - rect.top - this.mapOffset.y) / this.mapScale;
        
        // Check if hovering over a plot
        const hoveredPlot = this.plots.find(plot => {
            const distance = Math.sqrt(
                Math.pow(x - plot.coordinates.x, 2) + 
                Math.pow(y - plot.coordinates.y, 2)
            );
            return distance < 25;
        });
        
        if (hoveredPlot) {
            e.target.style.cursor = hoveredPlot.available ? 'pointer' : 'not-allowed';
            this.showPlotTooltip(hoveredPlot, e.clientX, e.clientY);
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
            this.drawPlotMarkers(ctx);
        }
    }
    
    renderPlotCards() {
        const container = document.querySelector('.plot-cards-container');
        if (!container) return;
        
        container.innerHTML = '';
        
        this.plots.forEach(plot => {
            const card = this.createPlotCard(plot);
            container.appendChild(card);
        });
    }
    
    createPlotCard(plot) {
        const card = document.createElement('div');
        card.className = `plot-card ${!plot.available ? 'unavailable' : ''} ${this.selectedPlot === plot.id ? 'selected' : ''}`;
        card.dataset.plotId = plot.id;
        
        card.innerHTML = `
            <div class="plot-card-header">
                <div class="plot-availability ${plot.available ? 'available' : 'unavailable'}">
                    ${plot.available ? 'Available' : 'Reserved'}
                </div>
                <div class="plot-rating">
                    <span class="stars">${'★'.repeat(Math.floor(plot.rating))}</span>
                    <span class="rating-value">${plot.rating}</span>
                    <span class="reviews">(${plot.reviews})</span>
                </div>
            </div>
            
            <div class="plot-card-image">
                <img src="/static/images/${plot.image}" alt="${plot.name}" 
                     onerror="this.style.background='linear-gradient(135deg, #8BC34A, #689F38)'">
                <div class="plot-region-badge">${plot.region}</div>
            </div>
            
            <div class="plot-card-body">
                <h3 class="plot-name">${plot.name}</h3>
                <p class="plot-description">${plot.description}</p>
                
                <div class="plot-details">
                    <div class="detail-item">
                        <i class="fas fa-ruler"></i>
                        <span>${plot.size}</span>
                    </div>
                    <div class="detail-item">
                        <i class="fas fa-mountain"></i>
                        <span>${plot.elevation}</span>
                    </div>
                    <div class="detail-item">
                        <i class="fas fa-compass"></i>
                        <span>${plot.exposure}</span>
                    </div>
                    <div class="detail-item">
                        <i class="fas fa-layer-group"></i>
                        <span>${plot.soil}</span>
                    </div>
                </div>
                
                <div class="plot-grapes">
                    ${plot.grapes.map(grape => `<span class="grape-tag">${grape}</span>`).join('')}
                </div>
                
                <div class="plot-features">
                    ${plot.features.map(feature => `
                        <div class="feature-badge">
                            <i class="fas fa-check-circle"></i>
                            ${feature}
                        </div>
                    `).join('')}
                </div>
                
                <div class="plot-card-footer">
                    <div class="plot-price">
                        <span class="price-label">Annual Lease</span>
                        <span class="price-value">${plot.price}</span>
                    </div>
                    <div class="plot-actions">
                        <button class="btn-compare" data-plot-id="${plot.id}" ${!plot.available ? 'disabled' : ''}>
                            <i class="fas fa-balance-scale"></i>
                            Compare
                        </button>
                        <button class="btn-select-plot" data-plot-id="${plot.id}" ${!plot.available ? 'disabled' : ''}>
                            ${this.selectedPlot === plot.id ? 'Selected ✓' : 'Select Plot'}
                        </button>
                    </div>
                </div>
            </div>
        `;
        
        // Add event listeners
        const selectBtn = card.querySelector('.btn-select-plot');
        if (selectBtn && plot.available) {
            selectBtn.addEventListener('click', () => this.selectPlot(plot.id));
        }
        
        const compareBtn = card.querySelector('.btn-compare');
        if (compareBtn && plot.available) {
            compareBtn.addEventListener('click', () => this.addToComparison(plot.id));
        }
        
        // Add hover effect
        card.addEventListener('mouseenter', () => this.highlightMapPlot(plot.id));
        card.addEventListener('mouseleave', () => this.unhighlightMapPlot(plot.id));
        
        return card;
    }
    
    selectPlot(plotId) {
        const plot = this.plots.find(p => p.id === plotId);
        if (!plot || !plot.available) return;
        
        this.selectedPlot = plotId;
        
        // Update cards
        document.querySelectorAll('.plot-card').forEach(card => {
            card.classList.toggle('selected', card.dataset.plotId === plotId);
            const btn = card.querySelector('.btn-select-plot');
            if (btn) {
                btn.textContent = card.dataset.plotId === plotId ? 'Selected ✓' : 'Select Plot';
            }
        });
        
        // Update map
        this.redrawMap();
        
        // Show detailed panel
        this.showPlotDetails(plot);
        
        // Trigger selection event
        this.onPlotSelected(plot);
    }
    
    showPlotDetails(plot) {
        const panel = document.getElementById('plot-details-panel');
        if (!panel) return;
        
        panel.innerHTML = `
            <div class="plot-details-header">
                <h2>${plot.name}</h2>
                <span class="region-badge">${plot.region}</span>
            </div>
            
            <div class="plot-details-content">
                <div class="details-section">
                    <h3>Terroir Characteristics</h3>
                    <div class="characteristics-grid">
                        <div class="characteristic">
                            <label>Soil Type:</label>
                            <span>${plot.soil}</span>
                        </div>
                        <div class="characteristic">
                            <label>Elevation:</label>
                            <span>${plot.elevation}</span>
                        </div>
                        <div class="characteristic">
                            <label>Exposure:</label>
                            <span>${plot.exposure}</span>
                        </div>
                        <div class="characteristic">
                            <label>Size:</label>
                            <span>${plot.size}</span>
                        </div>
                    </div>
                </div>
                
                <div class="details-section">
                    <h3>Grape Varieties</h3>
                    <div class="grape-varieties">
                        ${plot.grapes.map(grape => `
                            <div class="grape-variety">
                                <div class="grape-icon"></div>
                                <span>${grape}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
                
                <div class="details-section">
                    <h3>Special Features</h3>
                    <ul class="features-list">
                        ${plot.features.map(feature => `
                            <li><i class="fas fa-star"></i> ${feature}</li>
                        `).join('')}
                    </ul>
                </div>
                
                <div class="details-section">
                    <h3>Investment Details</h3>
                    <div class="investment-info">
                        <div class="investment-item">
                            <span class="label">Annual Lease:</span>
                            <span class="value">${plot.price}</span>
                        </div>
                        <div class="investment-item">
                            <span class="label">Estimated Yield:</span>
                            <span class="value">${Math.floor(plot.size.split(' ')[0] * 4000)} bottles/year</span>
                        </div>
                        <div class="investment-item">
                            <span class="label">ROI Potential:</span>
                            <span class="value">12-18% annually</span>
                        </div>
                    </div>
                </div>
                
                <button class="btn-confirm-selection" onclick="plotSelector.confirmSelection()">
                    Confirm Selection & Continue
                </button>
            </div>
        `;
        
        panel.classList.add('active');
    }
    
    setupFilters() {
        const filterContainer = document.createElement('div');
        filterContainer.className = 'plot-filters';
        filterContainer.innerHTML = `
            <div class="filter-group">
                <label>Region:</label>
                <select id="filter-region">
                    <option value="">All Regions</option>
                    <option value="Bordeaux">Bordeaux</option>
                    <option value="Burgundy">Burgundy</option>
                    <option value="Rhône Valley">Rhône Valley</option>
                    <option value="Alsace">Alsace</option>
                    <option value="Champagne">Champagne</option>
                </select>
            </div>
            
            <div class="filter-group">
                <label>Price Range:</label>
                <select id="filter-price">
                    <option value="">All Prices</option>
                    <option value="0-20000">Under €20,000</option>
                    <option value="20000-30000">€20,000 - €30,000</option>
                    <option value="30000-50000">€30,000 - €50,000</option>
                    <option value="50000+">Above €50,000</option>
                </select>
            </div>
            
            <div class="filter-group">
                <label>Size:</label>
                <select id="filter-size">
                    <option value="">All Sizes</option>
                    <option value="0-1.5">Under 1.5 hectares</option>
                    <option value="1.5-2.5">1.5 - 2.5 hectares</option>
                    <option value="2.5+">Over 2.5 hectares</option>
                </select>
            </div>
            
            <div class="filter-group">
                <label>Features:</label>
                <div class="feature-checkboxes">
                    <label><input type="checkbox" value="organic"> Organic</label>
                    <label><input type="checkbox" value="biodynamic"> Biodynamic</label>
                    <label><input type="checkbox" value="old-vines"> Old Vines</label>
                </div>
            </div>
            
            <button class="btn-reset-filters">Reset Filters</button>
        `;
        
        const mapContainer = document.querySelector('.map-container');
        if (mapContainer) {
            mapContainer.parentNode.insertBefore(filterContainer, mapContainer);
        }
        
        // Add filter event listeners
        filterContainer.addEventListener('change', () => this.applyFilters());
        filterContainer.querySelector('.btn-reset-filters').addEventListener('click', () => this.resetFilters());
    }
    
    applyFilters() {
        // Implementation for filtering plots
        console.log('Applying filters...');
    }
    
    resetFilters() {
        document.querySelectorAll('.plot-filters select').forEach(select => select.value = '');
        document.querySelectorAll('.plot-filters input[type="checkbox"]').forEach(cb => cb.checked = false);
        this.renderPlotCards();
    }
    
    initializeComparison() {
        this.comparisonPlots = [];
        this.createComparisonPanel();
    }
    
    createComparisonPanel() {
        const panel = document.createElement('div');
        panel.className = 'comparison-panel';
        panel.id = 'comparison-panel';
        panel.innerHTML = `
            <div class="comparison-header">
                <h3>Compare Plots</h3>
                <button class="btn-close-comparison">×</button>
            </div>
            <div class="comparison-content">
                <p>Select up to 3 plots to compare</p>
            </div>
        `;
        document.body.appendChild(panel);
    }
    
    addToComparison(plotId) {
        if (this.comparisonPlots.includes(plotId)) {
            this.comparisonPlots = this.comparisonPlots.filter(id => id !== plotId);
        } else if (this.comparisonPlots.length < 3) {
            this.comparisonPlots.push(plotId);
        } else {
            alert('You can compare up to 3 plots at a time');
            return;
        }
        
        this.updateComparisonPanel();
    }
    
    updateComparisonPanel() {
        const panel = document.getElementById('comparison-panel');
        if (!panel) return;
        
        if (this.comparisonPlots.length > 0) {
            panel.classList.add('active');
            // Update comparison content
        } else {
            panel.classList.remove('active');
        }
    }
    
    showPlotTooltip(plot, x, y) {
        let tooltip = document.getElementById('plot-tooltip');
        if (!tooltip) {
            tooltip = document.createElement('div');
            tooltip.id = 'plot-tooltip';
            tooltip.className = 'plot-tooltip';
            document.body.appendChild(tooltip);
        }
        
        tooltip.innerHTML = `
            <strong>${plot.name}</strong><br>
            ${plot.region}<br>
            ${plot.size}<br>
            ${plot.price}<br>
            ${plot.available ? '✓ Available' : '✗ Reserved'}
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
    
    highlightMapPlot(plotId) {
        // Redraw with highlighted plot
        this.highlightedPlot = plotId;
        this.redrawMap();
    }
    
    unhighlightMapPlot(plotId) {
        this.highlightedPlot = null;
        this.redrawMap();
    }
    
    setupEventListeners() {
        // Map controls
        document.querySelector('.map-zoom-in')?.addEventListener('click', () => this.zoomMap(1.2));
        document.querySelector('.map-zoom-out')?.addEventListener('click', () => this.zoomMap(0.8));
        document.querySelector('.map-reset')?.addEventListener('click', () => this.resetMap());
        
        // Touch support
        this.setupTouchSupport();
    }
    
    setupTouchSupport() {
        const canvas = document.getElementById('map-canvas');
        if (!canvas) return;
        
        let touchStartDistance = 0;
        
        canvas.addEventListener('touchstart', (e) => {
            if (e.touches.length === 2) {
                touchStartDistance = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY
                );
            }
        });
        
        canvas.addEventListener('touchmove', (e) => {
            if (e.touches.length === 2) {
                e.preventDefault();
                const currentDistance = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY
                );
                
                const scale = currentDistance / touchStartDistance;
                this.zoomMap(scale > 1 ? 1.05 : 0.95);
                touchStartDistance = currentDistance;
            }
        });
    }
    
    handleTouchStart(e) {
        if (e.touches.length === 1) {
            this.isDragging = true;
            this.dragStart = {
                x: e.touches[0].clientX - this.mapOffset.x,
                y: e.touches[0].clientY - this.mapOffset.y
            };
        }
    }
    
    handleTouchMove(e) {
        if (this.isDragging && e.touches.length === 1) {
            e.preventDefault();
            this.mapOffset = {
                x: e.touches[0].clientX - this.dragStart.x,
                y: e.touches[0].clientY - this.dragStart.y
            };
            this.redrawMap();
        }
    }
    
    handleTouchEnd(e) {
        this.isDragging = false;
    }
    
    confirmSelection() {
        if (!this.selectedPlot) {
            alert('Please select a plot first');
            return;
        }
        
        const plot = this.plots.find(p => p.id === this.selectedPlot);
        console.log('Plot confirmed:', plot);
        
        // Save selection
        localStorage.setItem('selected_plot', JSON.stringify(plot));
        
        // Trigger next step
        if (window.gameInstance) {
            window.gameInstance.journeyData.plot = this.selectedPlot;
            window.gameInstance.nextStep();
        }
    }
    
    onPlotSelected(plot) {
        // Custom event for plot selection
        const event = new CustomEvent('plotSelected', { detail: plot });
        window.dispatchEvent(event);
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.plotSelector = new VinsDeluxPlotSelector();
    });
} else {
    window.plotSelector = new VinsDeluxPlotSelector();
}