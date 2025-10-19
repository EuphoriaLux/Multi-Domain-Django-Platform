// Enhanced Plot Selector JavaScript

class EnhancedPlotSelector {
    constructor() {
        this.plots = [];
        this.selectedPlot = null;
        this.mapScale = 1;
        this.mapOffset = { x: 0, y: 0 };
        this.isDragging = false;
        this.dragStart = { x: 0, y: 0 };
        
        this.init();
    }

    init() {
        this.setupMapGrid();
        this.loadPlotData();
        this.attachEventListeners();
        this.renderPlotMarkers();
    }

    setupMapGrid() {
        const plotGrid = document.getElementById('plotGrid');
        plotGrid.innerHTML = '';
        
        // Create 12x15 grid (180 cells total)
        for (let row = 0; row < 15; row++) {
            for (let col = 0; col < 12; col++) {
                const cell = document.createElement('div');
                cell.className = 'plot-cell';
                cell.dataset.row = row;
                cell.dataset.col = col;
                cell.dataset.cellId = `cell-${row}-${col}`;
                
                // Add click handler
                cell.addEventListener('click', (e) => this.handleCellClick(e, row, col));
                cell.addEventListener('mouseenter', (e) => this.handleCellHover(e, row, col));
                cell.addEventListener('mouseleave', () => this.hideTooltip());
                
                plotGrid.appendChild(cell);
            }
        }
    }

    loadPlotData() {
        // Sample plot data - in production this would come from Django API
        this.plots = [
            {
                id: 1,
                name: "Remich Grand Cru",
                producer: "Domaine Moselle Excellence",
                region: "Remich",
                gridPosition: { row: 7, col: 6 },
                type: "grape",
                icon: "🍇",
                elevation: "150-200m",
                soilType: "Limestone & Clay",
                sunExposure: "South-East",
                wineType: "Riesling, Pinot Gris",
                price: 2500,
                available: true,
                features: ["Organic", "Biodynamic", "Historic Estate", "Cellar Visit"],
                image: "/static/images/producers/default.jpg",
                vineyardPlots: [
                    { id: "A1", name: "Parcel A1", size: "0.5 ha", grapeVariety: "Riesling" },
                    { id: "A2", name: "Parcel A2", size: "0.3 ha", grapeVariety: "Pinot Gris" },
                    { id: "B1", name: "Parcel B1", size: "0.4 ha", grapeVariety: "Auxerrois" }
                ]
            },
            {
                id: 2,
                name: "Grevenmacher Premier",
                producer: "Cave Cooperative Grevenmacher",
                region: "Grevenmacher",
                gridPosition: { row: 3, col: 7 },
                type: "wine",
                icon: "🍷",
                elevation: "180-220m",
                soilType: "Sandstone",
                sunExposure: "South",
                wineType: "Pinot Noir, Chardonnay",
                price: 1800,
                available: true,
                features: ["Modern Winery", "Restaurant", "Wine School"],
                image: "/static/images/producers/default.jpg",
                vineyardPlots: [
                    { id: "P1", name: "Plot 1", size: "1.2 ha", grapeVariety: "Pinot Noir" },
                    { id: "P2", name: "Plot 2", size: "0.8 ha", grapeVariety: "Chardonnay" }
                ]
            },
            {
                id: 3,
                name: "Schengen Heritage",
                producer: "Château Schengen",
                region: "Schengen",
                gridPosition: { row: 11, col: 5 },
                type: "grape",
                icon: "🍇",
                elevation: "140-180m",
                soilType: "Marl & Limestone",
                sunExposure: "South-West",
                wineType: "Crémant, Elbling",
                price: 3200,
                available: true,
                features: ["UNESCO Heritage", "Museum", "Tasting Room", "Events"],
                image: "/static/images/producers/default.jpg",
                vineyardPlots: [
                    { id: "H1", name: "Heritage Plot", size: "2.0 ha", grapeVariety: "Mixed" },
                    { id: "H2", name: "New Vineyard", size: "1.5 ha", grapeVariety: "Elbling" }
                ]
            },
            {
                id: 4,
                name: "Wormeldange Slopes",
                producer: "Domaine des Coteaux",
                region: "Wormeldange",
                gridPosition: { row: 5, col: 4 },
                type: "wine",
                icon: "🍾",
                elevation: "200-250m",
                soilType: "Schist",
                sunExposure: "South-East",
                wineType: "Riesling, Gewürztraminer",
                price: 2200,
                available: false,
                features: ["Steep Slopes", "Hand Harvest", "Natural Wine"],
                image: "/static/images/producers/default.jpg",
                vineyardPlots: [
                    { id: "S1", name: "Steep Slope A", size: "0.6 ha", grapeVariety: "Riesling" },
                    { id: "S2", name: "Steep Slope B", size: "0.4 ha", grapeVariety: "Gewürztraminer" }
                ]
            },
            {
                id: 5,
                name: "Stadtbredimus Classic",
                producer: "Weingut Stadtbredimus",
                region: "Stadtbredimus",
                gridPosition: { row: 9, col: 8 },
                type: "grape",
                icon: "🍇",
                elevation: "160-190m",
                soilType: "Clay & Loam",
                sunExposure: "South",
                wineType: "Pinot Blanc, Auxerrois",
                price: 1600,
                available: true,
                features: ["Family Estate", "Traditional Methods", "Wine Bar"],
                image: "/static/images/producers/default.jpg",
                vineyardPlots: [
                    { id: "C1", name: "Classic Vineyard", size: "1.8 ha", grapeVariety: "Pinot Blanc" },
                    { id: "C2", name: "Old Vines", size: "0.7 ha", grapeVariety: "Auxerrois" }
                ]
            }
        ];
    }

    renderPlotMarkers() {
        const markersContainer = document.getElementById('plotMarkers');
        markersContainer.innerHTML = '';
        
        this.plots.forEach(plot => {
            const marker = document.createElement('div');
            marker.className = `plot-marker ${plot.type}`;
            marker.innerHTML = plot.icon;
            marker.dataset.plotId = plot.id;
            
            // Calculate position based on grid
            const cellWidth = 100 / 12; // percentage
            const cellHeight = 100 / 15; // percentage
            marker.style.left = `${(plot.gridPosition.col + 0.5) * cellWidth}%`;
            marker.style.top = `${(plot.gridPosition.row + 0.5) * cellHeight}%`;
            
            // Style based on availability
            if (!plot.available) {
                marker.style.opacity = '0.5';
                marker.style.filter = 'grayscale(100%)';
            }
            
            // Add event listeners
            marker.addEventListener('click', (e) => this.handlePlotClick(e, plot));
            marker.addEventListener('mouseenter', (e) => this.showTooltip(e, plot));
            marker.addEventListener('mouseleave', () => this.hideTooltip());
            
            markersContainer.appendChild(marker);
            
            // Also mark the corresponding grid cell as occupied
            const gridCell = document.querySelector(
                `[data-cell-id="cell-${plot.gridPosition.row}-${plot.gridPosition.col}"]`
            );
            if (gridCell) {
                gridCell.classList.add('occupied');
                gridCell.dataset.plotId = plot.id;
            }
        });
    }

    handleCellClick(event, row, col) {
        const cell = event.target;
        const plotId = cell.dataset.plotId;
        
        if (plotId) {
            const plot = this.plots.find(p => p.id == plotId);
            if (plot) {
                this.selectPlot(plot);
            }
        }
    }

    handleCellHover(event, row, col) {
        const cell = event.target;
        const plotId = cell.dataset.plotId;
        
        if (plotId) {
            const plot = this.plots.find(p => p.id == plotId);
            if (plot) {
                this.showTooltip(event, plot);
            }
        }
    }

    handlePlotClick(event, plot) {
        event.stopPropagation();
        this.selectPlot(plot);
    }

    selectPlot(plot) {
        // Remove previous selection
        document.querySelectorAll('.plot-cell.selected, .plot-marker.selected').forEach(el => {
            el.classList.remove('selected');
        });
        
        // Add selection to current plot
        const gridCell = document.querySelector(
            `[data-cell-id="cell-${plot.gridPosition.row}-${plot.gridPosition.col}"]`
        );
        if (gridCell) {
            gridCell.classList.add('selected');
        }
        
        const marker = document.querySelector(`[data-plot-id="${plot.id}"]`);
        if (marker) {
            marker.classList.add('selected');
        }
        
        this.selectedPlot = plot;
        this.showPlotDetails(plot);
    }

    showPlotDetails(plot) {
        const panel = document.getElementById('plotDetailPanel');
        
        // Update panel content
        document.getElementById('plotTitle').textContent = plot.name;
        document.getElementById('plotStatus').textContent = plot.available ? 'Available' : 'Sold Out';
        document.getElementById('plotStatus').className = `plot-status ${plot.available ? '' : 'unavailable'}`;
        
        // Producer info
        document.getElementById('producerName').textContent = plot.producer;
        document.getElementById('producerRegion').textContent = plot.region;
        document.getElementById('producerImage').src = plot.image;
        
        // Plot details
        document.getElementById('elevation').textContent = plot.elevation;
        document.getElementById('soilType').textContent = plot.soilType;
        document.getElementById('sunExposure').textContent = plot.sunExposure;
        document.getElementById('wineType').textContent = plot.wineType;
        
        // Features
        const featuresList = document.getElementById('featuresList');
        featuresList.innerHTML = plot.features.map(feature => 
            `<span class="feature-tag">${feature}</span>`
        ).join('');
        
        // Price
        document.getElementById('plotPrice').textContent = `€${plot.price}`;
        
        // Benefits
        const benefits = [
            { icon: 'fa-wine-bottle', text: `${plot.vineyardPlots.length} vineyard parcels` },
            { icon: 'fa-certificate', text: 'Certificate of adoption' },
            { icon: 'fa-truck', text: 'Annual wine delivery' },
            { icon: 'fa-calendar', text: 'Exclusive events access' }
        ];
        
        document.getElementById('planBenefits').innerHTML = benefits.map(benefit => 
            `<div class="benefit-item">
                <i class="fas ${benefit.icon}"></i>
                <span>${benefit.text}</span>
            </div>`
        ).join('');
        
        // Show panel
        panel.classList.add('active');
        
        // Setup select button
        const selectBtn = document.getElementById('selectPlotBtn');
        selectBtn.disabled = !plot.available;
        selectBtn.textContent = plot.available ? 'Select This Plot' : 'Not Available';
        selectBtn.onclick = () => this.confirmPlotSelection(plot);
    }

    showTooltip(event, plot) {
        const tooltip = document.getElementById('plotTooltip');
        const rect = event.target.getBoundingClientRect();
        
        document.getElementById('tooltipTitle').textContent = plot.name;
        document.getElementById('tooltipInfo').innerHTML = `
            Producer: ${plot.producer}<br>
            Region: ${plot.region}<br>
            Price: €${plot.price}<br>
            Status: ${plot.available ? 'Available' : 'Sold Out'}
        `;
        
        tooltip.style.left = `${rect.left + rect.width / 2}px`;
        tooltip.style.top = `${rect.top - 10}px`;
        tooltip.style.transform = 'translate(-50%, -100%)';
        tooltip.classList.add('active');
    }

    hideTooltip() {
        const tooltip = document.getElementById('plotTooltip');
        tooltip.classList.remove('active');
    }

    confirmPlotSelection(plot) {
        // Store selection in localStorage
        localStorage.setItem('selectedPlot', JSON.stringify(plot));
        
        // Show producer map modal
        this.showProducerMap(plot);
    }

    showProducerMap(plot) {
        const modal = document.getElementById('producerMapModal');
        const vineyardMap = document.getElementById('vineyardMap');
        
        document.getElementById('modalTitle').textContent = `${plot.producer} - Vineyard Map`;
        
        // Clear previous map
        vineyardMap.innerHTML = '';
        
        // Create vineyard plot layout
        plot.vineyardPlots.forEach((vPlot, index) => {
            const plotElement = document.createElement('div');
            plotElement.className = 'vineyard-plot';
            plotElement.textContent = vPlot.id;
            
            // Position plots in a grid layout
            const cols = Math.ceil(Math.sqrt(plot.vineyardPlots.length));
            const row = Math.floor(index / cols);
            const col = index % cols;
            
            plotElement.style.width = `${80 / cols}%`;
            plotElement.style.height = `${80 / Math.ceil(plot.vineyardPlots.length / cols)}%`;
            plotElement.style.left = `${10 + col * (80 / cols)}%`;
            plotElement.style.top = `${10 + row * (80 / Math.ceil(plot.vineyardPlots.length / cols))}%`;
            
            plotElement.addEventListener('click', () => {
                document.querySelectorAll('.vineyard-plot').forEach(p => p.classList.remove('selected'));
                plotElement.classList.add('selected');
                this.showVineyardPlotInfo(vPlot);
            });
            
            vineyardMap.appendChild(plotElement);
        });
        
        // Update vineyard info
        document.getElementById('vineyardDetails').innerHTML = `
            <strong>Total Vineyard Size:</strong> ${plot.vineyardPlots.reduce((sum, p) => {
                const size = parseFloat(p.size);
                return sum + size;
            }, 0).toFixed(1)} ha<br>
            <strong>Main Grape Varieties:</strong> ${[...new Set(plot.vineyardPlots.map(p => p.grapeVariety))].join(', ')}<br>
            <strong>Number of Parcels:</strong> ${plot.vineyardPlots.length}
        `;
        
        modal.classList.add('active');
    }

    showVineyardPlotInfo(vPlot) {
        const details = document.getElementById('vineyardDetails');
        details.innerHTML = `
            <strong>Selected Parcel:</strong> ${vPlot.name}<br>
            <strong>Size:</strong> ${vPlot.size}<br>
            <strong>Grape Variety:</strong> ${vPlot.grapeVariety}<br>
            <strong>Status:</strong> Ready for adoption
        `;
    }

    attachEventListeners() {
        // Zoom controls
        document.getElementById('zoomIn').addEventListener('click', () => this.zoom(1.2));
        document.getElementById('zoomOut').addEventListener('click', () => this.zoom(0.8));
        document.getElementById('resetView').addEventListener('click', () => this.resetView());
        
        // Close panel
        document.getElementById('closePanel').addEventListener('click', () => {
            document.getElementById('plotDetailPanel').classList.remove('active');
            document.querySelectorAll('.plot-cell.selected, .plot-marker.selected').forEach(el => {
                el.classList.remove('selected');
            });
            this.selectedPlot = null;
        });
        
        // Close modal
        document.getElementById('closeModal').addEventListener('click', () => {
            document.getElementById('producerMapModal').classList.remove('active');
        });
        
        // Map dragging
        const mapContainer = document.getElementById('mapContainer');
        mapContainer.addEventListener('mousedown', (e) => this.startDrag(e));
        mapContainer.addEventListener('mousemove', (e) => this.drag(e));
        mapContainer.addEventListener('mouseup', () => this.endDrag());
        mapContainer.addEventListener('mouseleave', () => this.endDrag());
        
        // Touch events for mobile
        mapContainer.addEventListener('touchstart', (e) => this.startDrag(e.touches[0]));
        mapContainer.addEventListener('touchmove', (e) => {
            e.preventDefault();
            this.drag(e.touches[0]);
        });
        mapContainer.addEventListener('touchend', () => this.endDrag());
    }

    zoom(factor) {
        this.mapScale *= factor;
        this.mapScale = Math.max(0.5, Math.min(3, this.mapScale));
        this.updateMapTransform();
    }

    resetView() {
        this.mapScale = 1;
        this.mapOffset = { x: 0, y: 0 };
        this.updateMapTransform();
    }

    startDrag(event) {
        if (event.target.classList.contains('plot-marker') || 
            event.target.classList.contains('plot-cell')) {
            return;
        }
        this.isDragging = true;
        this.dragStart = {
            x: event.clientX - this.mapOffset.x,
            y: event.clientY - this.mapOffset.y
        };
        document.getElementById('mapContainer').style.cursor = 'grabbing';
    }

    drag(event) {
        if (!this.isDragging) return;
        
        this.mapOffset = {
            x: event.clientX - this.dragStart.x,
            y: event.clientY - this.dragStart.y
        };
        this.updateMapTransform();
    }

    endDrag() {
        this.isDragging = false;
        document.getElementById('mapContainer').style.cursor = 'grab';
    }

    updateMapTransform() {
        const map = document.getElementById('wineRegionMap');
        const grid = document.getElementById('plotGrid');
        const markers = document.getElementById('plotMarkers');
        
        const transform = `translate(${this.mapOffset.x}px, ${this.mapOffset.y}px) scale(${this.mapScale})`;
        
        [map, grid, markers].forEach(element => {
            element.style.transform = transform;
            element.style.transformOrigin = 'center center';
        });
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    new EnhancedPlotSelector();
});