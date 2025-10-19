/**
 * Producer Selector Fix
 * Direct integration to handle producer selection from map
 */

document.addEventListener('DOMContentLoaded', function() {
    console.log('🔧 Producer Selector Fix initializing...');
    
    // Handle clicks on any producer selection triggers
    document.addEventListener('click', function(e) {
        // Check for various producer selection buttons/links
        if (e.target.matches('.select-producer-btn, .view-adoption-plans, [onclick*="loadAdoptionPlans"]') || 
            e.target.closest('.select-producer-btn, .view-adoption-plans')) {
            
            e.preventDefault();
            e.stopPropagation();
            
            console.log('Producer button clicked:', e.target);
            
            // Extract producer info from the popup or button
            const producerName = extractProducerName(e.target);
            
            if (producerName) {
                console.log('Triggering producer selection for:', producerName);
                showProducerModal(producerName);
            }
        }
    });
    
    function extractProducerName(element) {
        // Try different methods to get producer name
        
        // Method 1: From onclick attribute
        const onclickAttr = element.getAttribute('onclick');
        if (onclickAttr && onclickAttr.includes('loadAdoptionPlans')) {
            const match = onclickAttr.match(/loadAdoptionPlans\(['"]([^'"]+)['"]\)/);
            if (match) return match[1];
        }
        
        // Method 2: From popup content
        const popup = element.closest('.leaflet-popup-content');
        if (popup) {
            const heading = popup.querySelector('h3, h4, h5, h6');
            if (heading) return heading.textContent.trim();
        }
        
        // Method 3: From data attribute
        if (element.dataset.producer) {
            return element.dataset.producer;
        }
        
        // Method 4: From parent elements
        const parent = element.closest('[data-producer]');
        if (parent) return parent.dataset.producer;
        
        return null;
    }
    
    function showProducerModal(producerName) {
        console.log('📍 Showing modal for producer:', producerName);
        
        // Create producer data object
        const producerData = {
            name: producerName,
            id: producerName.toLowerCase().replace(/\s+/g, '-'),
            region: getProducerRegion(producerName),
            description: getProducerDescription(producerName),
            elevation: '350-550m',
            soil_type: 'Clay-limestone',
            sun_exposure: 'South-facing slopes'
        };
        
        // Check if ProducerPlotSelector exists
        if (window.producerPlotSelector && typeof window.producerPlotSelector.showProducerDetails === 'function') {
            console.log('✅ Using ProducerPlotSelector');
            window.producerPlotSelector.showProducerDetails(producerData);
        } else {
            console.log('⚠️ ProducerPlotSelector not found, creating fallback modal');
            createFallbackModal(producerData);
        }
    }
    
    function getProducerRegion(producerName) {
        const regions = {
            'Château Margaux': 'Bordeaux, France',
            'Domaine de la Romanée-Conti': 'Burgundy, France',
            'Antinori': 'Tuscany, Italy',
            'Penfolds': 'Barossa Valley, Australia',
            'Catena Zapata': 'Mendoza, Argentina'
        };
        return regions[producerName] || 'Luxembourg';
    }
    
    function getProducerDescription(producerName) {
        const descriptions = {
            'Château Margaux': 'One of Bordeaux\'s most prestigious estates, producing exceptional wines since 1572.',
            'Domaine de la Romanée-Conti': 'The pinnacle of Burgundy wine, crafting the world\'s most sought-after Pinot Noir.',
            'Antinori': 'Six centuries of Italian winemaking excellence, pioneering Super Tuscans.',
            'Penfolds': 'Australia\'s most iconic wine producer, home of the legendary Grange.',
            'Catena Zapata': 'Argentina\'s leading wine estate, elevating Malbec to world-class status.'
        };
        return descriptions[producerName] || 'Premium wine producer with exceptional terroir and centuries of winemaking tradition.';
    }
    
    function createFallbackModal(producer) {
        // Remove existing modal if any
        const existingModal = document.getElementById('fallbackProducerModal');
        if (existingModal) existingModal.remove();
        
        const modalHTML = `
            <div class="modal fade" id="fallbackProducerModal" tabindex="-1">
                <div class="modal-dialog modal-lg modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header" style="background: linear-gradient(135deg, #722F37, #8B4513);">
                            <h4 class="modal-title text-white">
                                <i class="fas fa-wine-bottle"></i> ${producer.name}
                            </h4>
                            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="row">
                                <div class="col-md-6">
                                    <h5>Producer Information</h5>
                                    <p>${producer.description}</p>
                                    <ul class="list-unstyled">
                                        <li><strong>Region:</strong> ${producer.region}</li>
                                        <li><strong>Elevation:</strong> ${producer.elevation}</li>
                                        <li><strong>Soil Type:</strong> ${producer.soil_type}</li>
                                        <li><strong>Sun Exposure:</strong> ${producer.sun_exposure}</li>
                                    </ul>
                                </div>
                                <div class="col-md-6">
                                    <h5>Available Plots</h5>
                                    <div class="alert alert-info">
                                        <i class="fas fa-info-circle"></i>
                                        <p>This producer has <strong>3-5 premium plots</strong> available for adoption.</p>
                                        <p>Each plot offers unique characteristics and wine profiles.</p>
                                    </div>
                                    <div class="list-group">
                                        <div class="list-group-item">
                                            <h6>Hillside Reserve</h6>
                                            <small>0.25 hectares • €2,500</small>
                                        </div>
                                        <div class="list-group-item">
                                            <h6>Valley Premium</h6>
                                            <small>0.30 hectares • €2,200</small>
                                        </div>
                                        <div class="list-group-item">
                                            <h6>Heritage Plot</h6>
                                            <small>0.20 hectares • €2,800</small>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                            <button type="button" class="btn btn-primary" onclick="proceedToAdoptionPlans('${producer.name}')">
                                View Adoption Plans <i class="fas fa-arrow-right"></i>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        
        // Show the modal
        const modal = new bootstrap.Modal(document.getElementById('fallbackProducerModal'));
        modal.show();
    }
    
    // Global function to proceed to adoption plans
    window.proceedToAdoptionPlans = function(producerName) {
        console.log('Proceeding to adoption plans for:', producerName);
        // Store selection
        sessionStorage.setItem('selectedProducer', producerName);
        // You can redirect or load adoption plans here
        alert(`Loading adoption plans for ${producerName}...`);
    };
    
    // Also fix the existing loadAdoptionPlans function if it exists
    if (typeof window.loadAdoptionPlans === 'undefined') {
        window.loadAdoptionPlans = function(producerName) {
            console.log('loadAdoptionPlans called for:', producerName);
            showProducerModal(producerName);
        };
    } else {
        // Wrap the existing function
        const originalLoadAdoptionPlans = window.loadAdoptionPlans;
        window.loadAdoptionPlans = function(producerName) {
            console.log('Intercepting loadAdoptionPlans for:', producerName);
            showProducerModal(producerName);
            // Still call original if needed
            // originalLoadAdoptionPlans(producerName);
        };
    }
    
    console.log('✅ Producer Selector Fix ready');
});