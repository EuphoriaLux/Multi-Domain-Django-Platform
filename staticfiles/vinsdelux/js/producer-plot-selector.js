/**
 * Producer Plot Selector
 * Handles the two-step selection process: Producer -> Plots -> Adoption Plan
 */

class ProducerPlotSelector {
    constructor(options = {}) {
        this.selectedProducer = null;
        this.selectedPlots = [];
        this.maxPlots = options.maxPlots || 3;
        
        this.callbacks = {
            onProducerSelect: options.onProducerSelect || (() => {}),
            onPlotSelect: options.onPlotSelect || (() => {}),
            onSelectionComplete: options.onSelectionComplete || (() => {})
        };
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.createProducerModal();
    }
    
    setupEventListeners() {
        // Listen for producer selection from map
        document.addEventListener('producer:selected', (e) => {
            this.showProducerDetails(e.detail.producer);
        });
        
        // Listen for plot selection
        document.addEventListener('click', (e) => {
            if (e.target.matches('.select-plot-btn')) {
                const plotId = e.target.dataset.plotId;
                this.togglePlotSelection(plotId);
            }
            
            if (e.target.matches('.proceed-with-plots-btn')) {
                this.proceedWithSelectedPlots();
            }
        });
    }
    
    createProducerModal() {
        // Create modal HTML if it doesn't exist
        if (!document.getElementById('producerDetailsModal')) {
            const modalHTML = `
                <div class="modal fade" id="producerDetailsModal" tabindex="-1" aria-hidden="true">
                    <div class="modal-dialog modal-xl modal-dialog-centered">
                        <div class="modal-content">
                            <div class="modal-header">
                                <h4 class="modal-title" id="producerModalTitle">
                                    <i class="fas fa-wine-bottle"></i>
                                    <span id="producer-name"></span>
                                </h4>
                                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                            </div>
                            <div class="modal-body">
                                <div class="row">
                                    <!-- Producer Info Section -->
                                    <div class="col-md-4">
                                        <div class="producer-info-card">
                                            <img id="producer-image" src="" alt="" class="img-fluid rounded mb-3">
                                            <h5>About the Producer</h5>
                                            <p id="producer-description"></p>
                                            
                                            <div class="producer-details">
                                                <div class="detail-item">
                                                    <i class="fas fa-map-marker-alt"></i>
                                                    <span id="producer-region"></span>
                                                </div>
                                                <div class="detail-item">
                                                    <i class="fas fa-mountain"></i>
                                                    <span id="producer-elevation"></span>
                                                </div>
                                                <div class="detail-item">
                                                    <i class="fas fa-layer-group"></i>
                                                    <span id="producer-soil"></span>
                                                </div>
                                                <div class="detail-item">
                                                    <i class="fas fa-sun"></i>
                                                    <span id="producer-sun"></span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    
                                    <!-- Available Plots Section -->
                                    <div class="col-md-8">
                                        <div class="plots-section">
                                            <div class="section-header">
                                                <h5>Available Plots</h5>
                                                <div class="plot-selection-info">
                                                    <span class="selected-count">
                                                        <span id="selected-plot-count">0</span> / ${this.maxPlots} plots selected
                                                    </span>
                                                </div>
                                            </div>
                                            
                                            <div id="producer-plots-grid" class="plots-grid">
                                                <!-- Plots will be loaded here -->
                                            </div>
                                            
                                            <div class="selection-summary mt-4" id="selection-summary" style="display: none;">
                                                <h6>Selected Plots Summary</h6>
                                                <div id="selected-plots-list"></div>
                                                <div class="total-price mt-3">
                                                    <strong>Total:</strong> 
                                                    <span id="total-price">€0</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="modal-footer">
                                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">
                                    <i class="fas fa-arrow-left"></i> Back to Map
                                </button>
                                <button type="button" class="btn btn-primary proceed-with-plots-btn" disabled>
                                    <i class="fas fa-arrow-right"></i> 
                                    Proceed with Selected Plots
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            
            document.body.insertAdjacentHTML('beforeend', modalHTML);
        }
    }
    
    async showProducerDetails(producer) {
        this.selectedProducer = producer;
        this.selectedPlots = [];
        
        // Update modal with producer info
        document.getElementById('producer-name').textContent = producer.name;
        document.getElementById('producer-description').textContent = producer.description || 'Premium wine producer with exceptional terroir.';
        document.getElementById('producer-region').textContent = producer.region || 'Luxembourg';
        document.getElementById('producer-elevation').textContent = producer.elevation || 'Various elevations';
        document.getElementById('producer-soil').textContent = producer.soil_type || 'Mixed soil types';
        document.getElementById('producer-sun').textContent = producer.sun_exposure || 'Optimal exposure';
        
        // Set producer image
        const producerImage = document.getElementById('producer-image');
        if (producer.logo || producer.producer_photo) {
            producerImage.src = producer.logo || producer.producer_photo;
            producerImage.alt = producer.name;
        } else {
            producerImage.src = '/static/images/vineyard-defaults/producer-default.jpg';
        }
        
        // Load producer's plots
        await this.loadProducerPlots(producer.id);
        
        // Show modal
        const modal = new bootstrap.Modal(document.getElementById('producerDetailsModal'));
        modal.show();
        
        // Trigger callback
        this.callbacks.onProducerSelect(producer);
    }
    
    async loadProducerPlots(producerId) {
        const plotsGrid = document.getElementById('producer-plots-grid');
        plotsGrid.innerHTML = '<div class="text-center"><i class="fas fa-spinner fa-spin"></i> Loading plots...</div>';
        
        try {
            // Fetch plots for this producer
            const response = await fetch(`/en/vinsdelux/api/plots/?producer_id=${producerId}`);
            const plots = await response.json();
            
            if (plots && plots.length > 0) {
                this.renderPlots(plots);
            } else {
                plotsGrid.innerHTML = `
                    <div class="no-plots-message">
                        <i class="fas fa-exclamation-circle"></i>
                        <p>No plots currently available from this producer.</p>
                    </div>
                `;
            }
        } catch (error) {
            console.error('Error loading plots:', error);
            // Use sample data if API fails
            this.renderSamplePlots(producerId);
        }
    }
    
    renderPlots(plots) {
        const plotsGrid = document.getElementById('producer-plots-grid');
        
        plotsGrid.innerHTML = plots.map(plot => `
            <div class="plot-card" data-plot-id="${plot.id}">
                <div class="plot-card-header">
                    <h6>${plot.name || `Plot ${plot.plot_identifier}`}</h6>
                    <span class="plot-status ${plot.status}">${plot.status}</span>
                </div>
                <div class="plot-card-body">
                    <div class="plot-details-grid">
                        <div class="plot-detail">
                            <i class="fas fa-ruler-combined"></i>
                            <span>${plot.plot_size || 'N/A'}</span>
                        </div>
                        <div class="plot-detail">
                            <i class="fas fa-mountain"></i>
                            <span>${plot.elevation || 'N/A'}</span>
                        </div>
                        <div class="plot-detail">
                            <i class="fas fa-wine-bottle"></i>
                            <span>${plot.grape_varieties ? plot.grape_varieties[0] : 'Mixed'}</span>
                        </div>
                        <div class="plot-detail">
                            <i class="fas fa-sun"></i>
                            <span>${plot.sun_exposure || 'N/A'}</span>
                        </div>
                    </div>
                    
                    ${plot.wine_profile ? `
                    <div class="wine-profile mt-2">
                        <small class="text-muted">${plot.wine_profile}</small>
                    </div>
                    ` : ''}
                    
                    <div class="plot-price mt-3">
                        <strong>€${plot.base_price || '0'}</strong>
                    </div>
                    
                    <button class="btn btn-sm btn-outline-primary select-plot-btn mt-2" 
                            data-plot-id="${plot.id}"
                            data-plot-price="${plot.base_price || 0}"
                            ${plot.status !== 'available' ? 'disabled' : ''}>
                        <i class="fas fa-check"></i> Select Plot
                    </button>
                </div>
            </div>
        `).join('');
    }
    
    renderSamplePlots(producerId) {
        // Sample plots for demonstration
        const samplePlots = [
            {
                id: `${producerId}-1`,
                name: 'Hillside Reserve',
                plot_identifier: 'PLT-001',
                plot_size: '0.25 hectares',
                elevation: '450m',
                grape_varieties: ['Pinot Noir'],
                sun_exposure: 'South-facing',
                wine_profile: 'Elegant red with cherry notes',
                base_price: 2500,
                status: 'available'
            },
            {
                id: `${producerId}-2`,
                name: 'Valley Premium',
                plot_identifier: 'PLT-002',
                plot_size: '0.30 hectares',
                elevation: '380m',
                grape_varieties: ['Chardonnay'],
                sun_exposure: 'East-facing',
                wine_profile: 'Crisp white with citrus notes',
                base_price: 2200,
                status: 'available'
            },
            {
                id: `${producerId}-3`,
                name: 'Heritage Plot',
                plot_identifier: 'PLT-003',
                plot_size: '0.20 hectares',
                elevation: '420m',
                grape_varieties: ['Riesling'],
                sun_exposure: 'Southeast',
                wine_profile: 'Aromatic white with mineral finish',
                base_price: 2800,
                status: 'available'
            }
        ];
        
        this.renderPlots(samplePlots);
    }
    
    togglePlotSelection(plotId) {
        const plotCard = document.querySelector(`[data-plot-id="${plotId}"]`);
        const button = plotCard.querySelector('.select-plot-btn');
        const price = parseFloat(button.dataset.plotPrice);
        
        const plotIndex = this.selectedPlots.findIndex(p => p.id === plotId);
        
        if (plotIndex > -1) {
            // Deselect plot
            this.selectedPlots.splice(plotIndex, 1);
            plotCard.classList.remove('selected');
            button.innerHTML = '<i class="fas fa-check"></i> Select Plot';
            button.classList.remove('btn-primary');
            button.classList.add('btn-outline-primary');
        } else {
            // Select plot (if under limit)
            if (this.selectedPlots.length >= this.maxPlots) {
                alert(`You can select a maximum of ${this.maxPlots} plots.`);
                return;
            }
            
            this.selectedPlots.push({
                id: plotId,
                price: price,
                card: plotCard
            });
            
            plotCard.classList.add('selected');
            button.innerHTML = '<i class="fas fa-times"></i> Deselect';
            button.classList.remove('btn-outline-primary');
            button.classList.add('btn-primary');
        }
        
        this.updateSelectionSummary();
        this.callbacks.onPlotSelect(this.selectedPlots);
    }
    
    updateSelectionSummary() {
        const count = this.selectedPlots.length;
        document.getElementById('selected-plot-count').textContent = count;
        
        const proceedBtn = document.querySelector('.proceed-with-plots-btn');
        proceedBtn.disabled = count === 0;
        
        const summary = document.getElementById('selection-summary');
        if (count > 0) {
            summary.style.display = 'block';
            
            const total = this.selectedPlots.reduce((sum, plot) => sum + plot.price, 0);
            document.getElementById('total-price').textContent = `€${total.toLocaleString()}`;
            
            const listHTML = this.selectedPlots.map(plot => {
                const plotCard = plot.card;
                const name = plotCard.querySelector('h6').textContent;
                return `
                    <div class="selected-plot-item">
                        <span>${name}</span>
                        <span>€${plot.price.toLocaleString()}</span>
                    </div>
                `;
            }).join('');
            
            document.getElementById('selected-plots-list').innerHTML = listHTML;
        } else {
            summary.style.display = 'none';
        }
    }
    
    proceedWithSelectedPlots() {
        if (this.selectedPlots.length === 0) return;
        
        // Store selection data
        const selectionData = {
            producer: this.selectedProducer,
            plots: this.selectedPlots,
            timestamp: new Date().toISOString()
        };
        
        sessionStorage.setItem('wine_selection', JSON.stringify(selectionData));
        
        // Trigger completion callback
        this.callbacks.onSelectionComplete(selectionData);
        
        // Close modal and proceed to adoption plans
        const modal = bootstrap.Modal.getInstance(document.getElementById('producerDetailsModal'));
        modal.hide();
        
        // Show success message
        this.showSuccessMessage();
        
        // Redirect to the basic plot selector which shows adoption plans
        setTimeout(() => {
            // Store the selected producer and plots for the next page
            const selectionData = {
                producer: this.selectedProducer,
                plots: this.selectedPlots,
                timestamp: new Date().toISOString()
            };
            sessionStorage.setItem('selected_producer_plots', JSON.stringify(selectionData));
            
            // Redirect to the existing plot selector page which shows adoption plans
            window.location.href = '/en/vinsdelux/journey/plot-selection/';
        }, 2000);
    }
    
    showSuccessMessage() {
        const message = document.createElement('div');
        message.className = 'selection-success-message';
        message.innerHTML = `
            <div class="success-content">
                <i class="fas fa-check-circle"></i>
                <h4>Selection Confirmed!</h4>
                <p>You've selected ${this.selectedPlots.length} plot(s) from ${this.selectedProducer.name}</p>
                <p>Proceeding to adoption plans...</p>
            </div>
        `;
        
        message.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background: white;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            z-index: 10000;
            text-align: center;
        `;
        
        document.body.appendChild(message);
    }
}

// Initialize globally
window.ProducerPlotSelector = ProducerPlotSelector;

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    window.producerPlotSelector = new ProducerPlotSelector({
        maxPlots: 3,
        onProducerSelect: (producer) => {
            console.log('Producer selected:', producer);
        },
        onPlotSelect: (plots) => {
            console.log('Plots selected:', plots);
        },
        onSelectionComplete: (data) => {
            console.log('Selection complete:', data);
        }
    });
});

// Add styles
const selectorStyles = `
<style>
.producer-info-card {
    padding: 20px;
    background: #f8f9fa;
    border-radius: 8px;
}

.producer-details {
    margin-top: 20px;
}

.detail-item {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
    font-size: 14px;
}

.detail-item i {
    width: 20px;
    color: #722F37;
}

.plots-section {
    padding: 20px;
}

.section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
}

.plots-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    gap: 20px;
}

.plot-card {
    border: 1px solid #dee2e6;
    border-radius: 8px;
    padding: 15px;
    transition: all 0.3s ease;
}

.plot-card:hover {
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

.plot-card.selected {
    border-color: #D4AF37;
    background: #fffef5;
}

.plot-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 15px;
}

.plot-card-header h6 {
    margin: 0;
    color: #722F37;
}

.plot-status {
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
}

.plot-status.available {
    background: #d4f4dd;
    color: #2e7d32;
}

.plot-details-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-bottom: 10px;
}

.plot-detail {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
}

.plot-detail i {
    color: #6c757d;
    font-size: 10px;
}

.wine-profile {
    padding: 8px;
    background: #f8f9fa;
    border-radius: 4px;
}

.plot-price {
    font-size: 18px;
    color: #722F37;
}

.selection-summary {
    padding: 20px;
    background: #f8f9fa;
    border-radius: 8px;
}

.selected-plot-item {
    display: flex;
    justify-content: space-between;
    padding: 8px 0;
    border-bottom: 1px solid #dee2e6;
}

.no-plots-message {
    text-align: center;
    padding: 40px;
    color: #6c757d;
}

.no-plots-message i {
    font-size: 48px;
    margin-bottom: 20px;
    opacity: 0.5;
}

.selection-success-message {
    animation: fadeIn 0.3s ease;
}

.success-content i {
    font-size: 64px;
    color: #4CAF50;
    margin-bottom: 20px;
}

@keyframes fadeIn {
    from {
        opacity: 0;
        transform: translate(-50%, -40%);
    }
    to {
        opacity: 1;
        transform: translate(-50%, -50%);
    }
}
</style>
`;

document.head.insertAdjacentHTML('beforeend', selectorStyles);