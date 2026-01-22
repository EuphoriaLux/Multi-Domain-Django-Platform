/**
 * Producer Map Connector
 * Connects the existing producer map to the new producer-plot selection system
 */

(function() {
    // Wait for DOM to be ready
    document.addEventListener('DOMContentLoaded', function() {
        console.log('🔗 Initializing Producer Map Connector...');
        
        // Override marker click behavior
        setupProducerMarkerClicks();
        
        // Listen for popup opens to add click handlers
        if (window.map) {
            window.map.on('popupopen', function(e) {
                setupPopupClickHandlers(e.popup);
            });
        }
    });
    
    function setupProducerMarkerClicks() {
        // Wait a bit for map to initialize
        setTimeout(() => {
            if (window.map) {
                // Get all layers
                window.map.eachLayer(function(layer) {
                    if (layer instanceof L.Marker) {
                        // Add click handler to marker
                        layer.on('click', function(e) {
                            const producerData = extractProducerData(layer);
                            if (producerData) {
                                console.log('Producer marker clicked:', producerData);
                                // Don't prevent default - let popup open
                                // But also trigger our event
                                setTimeout(() => {
                                    triggerProducerSelection(producerData);
                                }, 100);
                            }
                        });
                    }
                });
            }
        }, 1000);
    }
    
    function setupPopupClickHandlers(popup) {
        // Add click handler to "View Plots" or similar buttons in popup
        setTimeout(() => {
            const popupContent = popup.getContent();
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = typeof popupContent === 'string' ? popupContent : popupContent.innerHTML;
            
            // Extract producer info from popup
            const producerName = tempDiv.querySelector('h5')?.textContent || 
                               tempDiv.querySelector('h4')?.textContent || 
                               tempDiv.querySelector('h3')?.textContent || 
                               'Unknown Producer';
            
            // Add a "Select This Producer" button if not already there
            if (!tempDiv.querySelector('.select-producer-btn')) {
                const button = document.createElement('button');
                button.className = 'btn btn-primary btn-sm select-producer-btn mt-2';
                button.innerHTML = '<i class="fas fa-wine-bottle"></i> View Available Plots';
                button.onclick = function() {
                    const producerData = {
                        name: producerName,
                        id: producerName.toLowerCase().replace(/\s+/g, '-'),
                        region: tempDiv.querySelector('p')?.textContent || 'Luxembourg',
                        description: 'Premium wine producer with exceptional terroir'
                    };
                    triggerProducerSelection(producerData);
                };
                
                // Add button to popup
                tempDiv.appendChild(button);
                popup.setContent(tempDiv.innerHTML);
            }
        }, 100);
    }
    
    function extractProducerData(marker) {
        // Try to get producer data from marker
        const popup = marker.getPopup();
        if (popup) {
            const content = popup.getContent();
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = typeof content === 'string' ? content : content.innerHTML;
            
            const name = tempDiv.querySelector('h5')?.textContent || 
                        tempDiv.querySelector('h4')?.textContent || 
                        tempDiv.querySelector('h3')?.textContent;
            
            if (name) {
                return {
                    name: name,
                    id: name.toLowerCase().replace(/\s+/g, '-'),
                    region: tempDiv.querySelector('p')?.textContent || 'Luxembourg',
                    description: 'Premium wine producer with exceptional terroir'
                };
            }
        }
        
        return null;
    }
    
    function triggerProducerSelection(producerData) {
        // Dispatch custom event for producer selection
        const event = new CustomEvent('producer:selected', {
            detail: { producer: producerData }
        });
        document.dispatchEvent(event);
        console.log('✅ Producer selection event dispatched:', producerData);
    }
    
    // Also handle any existing adoption plan links
    document.addEventListener('click', function(e) {
        if (e.target.matches('.view-adoption-plans, .select-producer, [data-producer-name]')) {
            e.preventDefault();
            
            const producerName = e.target.dataset.producerName || 
                                e.target.closest('[data-producer-name]')?.dataset.producerName ||
                                e.target.textContent;
            
            if (producerName) {
                const producerData = {
                    name: producerName,
                    id: producerName.toLowerCase().replace(/\s+/g, '-'),
                    region: e.target.dataset.region || 'Luxembourg',
                    description: e.target.dataset.description || 'Premium wine producer'
                };
                
                triggerProducerSelection(producerData);
            }
        }
    });
    
    console.log('✅ Producer Map Connector initialized');
})();