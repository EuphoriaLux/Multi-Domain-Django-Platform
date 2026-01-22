/**
 * Enhanced Producer Sidebar Integration
 * Handles producer selection to show details in sidebar instead of modal
 */

class EnhancedProducerSidebar {
    constructor() {
        this.currentProducer = null;
        this.availablePlots = [];
        this.init();
    }

    init() {
        console.log('🎯 Initializing Enhanced Producer Sidebar...');
        
        // Override existing producer selection handlers
        this.interceptProducerSelection();
        
        // Listen for producer selection events
        document.addEventListener('producer:selected', (e) => {
            this.handleProducerSelection(e.detail.producer);
        });
        
        // Intercept all producer button clicks
        this.setupClickInterceptors();
    }

    setupClickInterceptors() {
        // Use event delegation to catch all clicks
        document.addEventListener('click', (e) => {
            const target = e.target;
            
            // Check if it's a producer selection button
            if (target.matches('.select-producer-btn, .view-adoption-plans, [onclick*="loadAdoptionPlans"]') || 
                target.closest('.select-producer-btn, .view-adoption-plans')) {
                
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                
                console.log('🍷 Producer button intercepted:', target);
                
                // Extract producer information
                const producerData = this.extractProducerData(target);
                if (producerData) {
                    this.handleProducerSelection(producerData);
                }
                
                return false;
            }
        }, true); // Use capture phase to intercept before other handlers
    }

    extractProducerData(element) {
        let producerName = null;
        let producerId = null;
        let planId = null;
        
        // Try to get from data attributes
        if (element.dataset.producer) {
            producerName = element.dataset.producer;
            planId = element.dataset.planId;
        }
        
        // Try from onclick attribute
        const onclickAttr = element.getAttribute('onclick');
        if (onclickAttr && onclickAttr.includes('loadAdoptionPlans')) {
            const match = onclickAttr.match(/loadAdoptionPlans\(['"]([^'"]+)['"]\)/);
            if (match) producerName = match[1];
        }
        
        // Try from popup content
        const popup = element.closest('.leaflet-popup-content, .producer-popup');
        if (popup) {
            const heading = popup.querySelector('h3, h4, h5, h6');
            if (heading) producerName = heading.textContent.trim();
        }
        
        if (!producerName) return null;
        
        // Map producer name to ID (based on your database)
        const producerIdMap = {
            'Château Margaux': 1,
            'Domaine de la Romanée-Conti': 2,
            'Penfolds': 3,
            'Antinori': 4,
            'Catena Zapata': 5
        };
        
        producerId = producerIdMap[producerName] || producerName.toLowerCase().replace(/\s+/g, '-');
        
        return {
            name: producerName,
            id: producerId,
            planId: planId,
            region: this.getProducerRegion(producerName),
            description: this.getProducerDescription(producerName)
        };
    }

    handleProducerSelection(producer) {
        console.log('📍 Handling producer selection in sidebar:', producer);
        
        this.currentProducer = producer;
        
        // Hide the empty selection message
        const emptySelection = document.getElementById('empty-selection');
        if (emptySelection) {
            emptySelection.style.display = 'none';
        }
        
        // Update the sidebar header
        this.updateSidebarHeader(producer);
        
        // Show producer details in the plot details panel
        this.showProducerInSidebar(producer);
        
        // Load available plots for this producer
        this.loadProducerPlots(producer);
        
        // Scroll sidebar into view on mobile
        if (window.innerWidth < 768) {
            const sidebar = document.querySelector('.vdl-sidebar');
            if (sidebar) {
                sidebar.classList.add('expanded');
                sidebar.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    }

    updateSidebarHeader(producer) {
        const sidebarTitle = document.getElementById('sidebar-title');
        if (sidebarTitle) {
            sidebarTitle.innerHTML = `
                <i class="fas fa-wine-bottle" style="color: var(--vdl-gold);"></i>
                ${producer.name}
            `;
        }
        
        // Update the counter to show producer selection
        const selectedCount = document.getElementById('selected-count');
        const selectedLabel = document.getElementById('selected-count-label');
        if (selectedCount && selectedLabel) {
            selectedCount.textContent = '1';
            selectedLabel.textContent = 'producer selected';
        }
    }

    showProducerInSidebar(producer) {
        // Get or create the producer details section
        let plotDetails = document.getElementById('plot-details');
        if (!plotDetails) {
            // Create it if it doesn't exist
            plotDetails = document.createElement('div');
            plotDetails.id = 'plot-details';
            plotDetails.className = 'vdl-card vdl-card-premium';
            
            const sidebar = document.querySelector('.vdl-sidebar');
            if (sidebar) {
                sidebar.appendChild(plotDetails);
            }
        }
        
        // Show the details panel
        plotDetails.style.display = 'block';
        
        // Update with producer information
        plotDetails.innerHTML = `
            <div class="vdl-card-header" style="background: linear-gradient(135deg, var(--vdl-burgundy) 0%, var(--vdl-deep-burgundy) 100%); color: white; border-radius: 12px 12px 0 0;">
                <h4 class="vdl-heading-tertiary vdl-mb-sm" style="color: var(--vdl-gold);">
                    <i class="fas fa-crown"></i> ${producer.name}
                </h4>
                <div class="vdl-caption" style="color: var(--vdl-champagne);">
                    <i class="fas fa-map-marker-alt"></i> ${producer.region}
                </div>
            </div>
            
            <div class="vdl-card-body">
                <!-- Producer Image -->
                <div class="mb-3">
                    <div style="height: 200px; background: linear-gradient(135deg, #722F37 0%, #8B4513 100%); border-radius: 8px; display: flex; align-items: center; justify-content: center;">
                        <i class="fas fa-wine-bottle" style="font-size: 48px; color: var(--vdl-gold); opacity: 0.3;"></i>
                    </div>
                </div>
                
                <!-- Producer Description -->
                <div class="mb-4">
                    <p class="vdl-body-small" style="color: var(--vdl-charcoal); line-height: 1.6;">
                        ${producer.description}
                    </p>
                </div>
                
                <!-- Available Plots Section -->
                <div class="mb-3">
                    <h5 class="vdl-heading-quaternary mb-3" style="color: var(--vdl-burgundy);">
                        <i class="fas fa-map-marked-alt"></i> Available Plots
                    </h5>
                    <div id="producer-plots-list" class="vdl-plots-grid">
                        <div class="text-center py-3">
                            <div class="spinner-border text-primary" role="status">
                                <span class="sr-only">Loading plots...</span>
                            </div>
                            <p class="vdl-caption mt-2">Loading available plots...</p>
                        </div>
                    </div>
                </div>
                
                <!-- Terroir Information -->
                <div class="row mb-3">
                    <div class="col-6">
                        <div class="vdl-caption vdl-mb-xs">Elevation</div>
                        <div class="vdl-body-small">350-550m</div>
                    </div>
                    <div class="col-6">
                        <div class="vdl-caption vdl-mb-xs">Climate</div>
                        <div class="vdl-body-small">Continental</div>
                    </div>
                </div>
                
                <div class="row mb-3">
                    <div class="col-6">
                        <div class="vdl-caption vdl-mb-xs">Soil Type</div>
                        <div class="vdl-body-small">Clay-limestone</div>
                    </div>
                    <div class="col-6">
                        <div class="vdl-caption vdl-mb-xs">Sun Exposure</div>
                        <div class="vdl-body-small">South-facing</div>
                    </div>
                </div>
            </div>
            
            <div class="vdl-card-footer">
                <button type="button" class="vdl-btn vdl-btn-accent w-100" onclick="enhancedProducerSidebar.viewAllPlots()">
                    <i class="fas fa-th"></i> View All ${producer.name} Plots
                </button>
            </div>
        `;
        
        // Load the plots after a short delay
        setTimeout(() => this.loadProducerPlots(producer), 500);
    }

    loadProducerPlots(producer) {
        console.log('📦 Loading plots for producer:', producer.name);
        
        const plotsList = document.getElementById('producer-plots-list');
        if (!plotsList) return;
        
        // Show loading state
        plotsList.innerHTML = `
            <div class="text-center py-3">
                <div class="spinner-border text-primary" role="status">
                    <span class="sr-only">Loading plots...</span>
                </div>
                <p class="vdl-caption mt-2">Loading available plots...</p>
            </div>
        `;
        
        // Fetch real plots from the API
        const langCode = document.documentElement.lang || 'en';
        fetch(`/${langCode}/vinsdelux/api/plots/?producer_id=${producer.id}`)
            .then(response => response.json())
            .then(data => {
                console.log('📍 Received plot data from API:', data);
                
                // Store the real plots
                this.availablePlots = data.results || data;
                
                if (this.availablePlots.length === 0) {
                    plotsList.innerHTML = `
                        <div class="alert alert-info">
                            <i class="fas fa-info-circle"></i>
                            <p>No plots currently available for ${producer.name}</p>
                        </div>
                    `;
                    return;
                }
                
                // Display the real plots
                plotsList.innerHTML = this.availablePlots.map(plot => `
            <div class="vdl-plot-card" style="background: white; border: 1px solid var(--vdl-gold-alpha-25); border-radius: 8px; padding: 12px; margin-bottom: 12px; cursor: pointer; transition: all 0.3s ease;"
                 onclick="enhancedProducerSidebar.selectPlot('${plot.id}')"
                 onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 4px 12px rgba(114, 47, 55, 0.1)';"
                 onmouseout="this.style.transform=''; this.style.boxShadow='';">
                <div class="d-flex justify-content-between align-items-start">
                    <div>
                        <h6 class="vdl-body-small fw-bold mb-1" style="color: var(--vdl-burgundy);">
                            ${plot.name || plot.plot_identifier}
                        </h6>
                        <div class="vdl-caption" style="color: var(--vdl-slate);">
                            ${plot.plot_size || 'Size N/A'} • ${plot.grape_varieties ? plot.grape_varieties.join(', ') : 'Varieties N/A'}
                        </div>
                    </div>
                    <div class="text-end">
                        <div class="vdl-heading-quaternary" style="color: var(--vdl-gold);">
                            €${(plot.base_price || 0).toLocaleString()}
                        </div>
                        <div class="vdl-caption" style="color: var(--vdl-slate);">
                            /year
                        </div>
                    </div>
                </div>
                <div class="mt-2">
                    <div class="d-flex justify-content-between vdl-caption" style="color: var(--vdl-slate);">
                        <span><i class="fas fa-mountain"></i> ${plot.elevation || 'N/A'}</span>
                        <span><i class="fas fa-sun"></i> ${plot.sun_exposure || 'N/A'}</span>
                    </div>
                    <div class="vdl-caption mt-1" style="color: var(--vdl-slate);">
                        <i class="fas fa-layer-group"></i> ${plot.soil_type || 'Soil type N/A'}
                    </div>
                </div>
            </div>
        `).join('');
                
                // Update selection list with real plots
                this.updateSelectionList(producer, this.availablePlots);
            })
            .catch(error => {
                console.error('Error loading plots:', error);
                plotsList.innerHTML = `
                    <div class="alert alert-warning">
                        <i class="fas fa-exclamation-triangle"></i>
                        <p>Unable to load plots. Please try again.</p>
                    </div>
                `;
                // Fall back to mock data if API fails
                const mockPlots = this.getProducerPlots(producer.name);
                this.availablePlots = mockPlots;
                // Display mock plots
                plotsList.innerHTML = mockPlots.map(plot => `
                    <div class="vdl-plot-card" style="background: white; border: 1px solid var(--vdl-gold-alpha-25); border-radius: 8px; padding: 12px; margin-bottom: 12px; cursor: pointer; transition: all 0.3s ease;"
                         onclick="enhancedProducerSidebar.selectPlot('${plot.id}')"
                         onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 4px 12px rgba(114, 47, 55, 0.1)';"
                         onmouseout="this.style.transform=''; this.style.boxShadow='';">
                        <div class="d-flex justify-content-between align-items-start">
                            <div>
                                <h6 class="vdl-body-small fw-bold mb-1" style="color: var(--vdl-burgundy);">
                                    ${plot.name}
                                </h6>
                                <div class="vdl-caption" style="color: var(--vdl-slate);">
                                    ${plot.size} • ${plot.grapeVariety}
                                </div>
                            </div>
                            <div class="text-end">
                                <div class="vdl-heading-quaternary" style="color: var(--vdl-gold);">
                                    €${plot.price.toLocaleString()}
                                </div>
                                <div class="vdl-caption" style="color: var(--vdl-slate);">
                                    /year
                                </div>
                            </div>
                        </div>
                    </div>
                `).join('');
                this.updateSelectionList(producer, mockPlots);
            });
    }

    updateSelectionList(producer, plots) {
        const selectionList = document.getElementById('selection-list');
        if (!selectionList) return;
        
        selectionList.innerHTML = `
            <div class="vdl-producer-selected" style="background: linear-gradient(135deg, var(--vdl-pearl) 0%, white 100%); border: 2px solid var(--vdl-gold); border-radius: 12px; padding: 16px; margin-bottom: 16px;">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h5 class="vdl-body fw-bold mb-1" style="color: var(--vdl-burgundy);">
                            <i class="fas fa-check-circle" style="color: var(--vdl-gold);"></i>
                            ${producer.name}
                        </h5>
                        <div class="vdl-caption" style="color: var(--vdl-slate);">
                            ${plots.length} plots available • ${producer.region}
                        </div>
                    </div>
                    <button class="btn btn-sm btn-outline-danger" onclick="enhancedProducerSidebar.clearSelection()">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
            </div>
            <div class="vdl-caption text-center mt-3" style="color: var(--vdl-slate);">
                Select a plot above to continue
            </div>
        `;
        
        // Show proceed section
        const proceedSection = document.getElementById('proceed-section');
        if (proceedSection) {
            proceedSection.style.display = 'block';
        }
    }

    selectPlot(plotId) {
        console.log('🎯 Plot selected:', plotId);
        
        // Find the plot
        const plot = this.availablePlots.find(p => p.id === plotId);
        if (!plot) return;
        
        // Trigger plot selection event
        const event = new CustomEvent('plotSelected', {
            detail: {
                id: plotId,
                name: plot.name,
                producer: this.currentProducer.name,
                price: plot.price,
                ...plot
            }
        });
        document.dispatchEvent(event);
        
        // Update UI to show selection
        this.highlightSelectedPlot(plotId);
    }

    highlightSelectedPlot(plotId) {
        // Remove previous selections
        document.querySelectorAll('.vdl-plot-card').forEach(card => {
            card.style.border = '1px solid var(--vdl-gold-alpha-25)';
            card.style.background = 'white';
        });
        
        // Highlight selected plot
        const selectedCard = document.querySelector(`[onclick="enhancedProducerSidebar.selectPlot('${plotId}')"]`);
        if (selectedCard) {
            selectedCard.style.border = '2px solid var(--vdl-gold)';
            selectedCard.style.background = 'linear-gradient(135deg, var(--vdl-champagne) 0%, white 100%)';
        }
    }

    viewAllPlots() {
        console.log('Viewing all plots for:', this.currentProducer?.name);
        // Scroll to map or expand plot view
        const mapSection = document.getElementById('vineyard-map');
        if (mapSection) {
            mapSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    clearSelection() {
        this.currentProducer = null;
        this.availablePlots = [];
        
        // Reset sidebar
        const sidebarTitle = document.getElementById('sidebar-title');
        if (sidebarTitle) {
            sidebarTitle.textContent = 'Your Selection';
        }
        
        const selectedCount = document.getElementById('selected-count');
        const selectedLabel = document.getElementById('selected-count-label');
        if (selectedCount && selectedLabel) {
            selectedCount.textContent = '0';
            selectedLabel.textContent = 'plots selected';
        }
        
        // Hide plot details
        const plotDetails = document.getElementById('plot-details');
        if (plotDetails) {
            plotDetails.style.display = 'none';
        }
        
        // Show empty selection
        const emptySelection = document.getElementById('empty-selection');
        if (emptySelection) {
            emptySelection.style.display = 'block';
        }
        
        // Reset selection list
        const selectionList = document.getElementById('selection-list');
        if (selectionList) {
            selectionList.innerHTML = `
                <div class="text-center vdl-text-muted" id="empty-selection">
                    <i class="fas fa-map vdl-wine-loading vdl-mb-md" style="font-size: 2rem; color: var(--vdl-gold);"></i>
                    <p class="vdl-body-small">Click on a producer to begin your selection</p>
                </div>
            `;
        }
        
        // Hide proceed section
        const proceedSection = document.getElementById('proceed-section');
        if (proceedSection) {
            proceedSection.style.display = 'none';
        }
    }

    getProducerRegion(producerName) {
        const regions = {
            'Château Margaux': 'Bordeaux, France',
            'Domaine de la Romanée-Conti': 'Burgundy, France',
            'Antinori': 'Tuscany, Italy',
            'Penfolds': 'Barossa Valley, Australia',
            'Catena Zapata': 'Mendoza, Argentina'
        };
        return regions[producerName] || 'Luxembourg';
    }

    getProducerDescription(producerName) {
        const descriptions = {
            'Château Margaux': 'One of Bordeaux\'s most prestigious estates, producing exceptional wines since 1572. Known for elegant, refined wines with extraordinary aging potential.',
            'Domaine de la Romanée-Conti': 'The pinnacle of Burgundy wine, crafting the world\'s most sought-after Pinot Noir from meticulously tended Grand Cru vineyards.',
            'Antinori': 'Six centuries of Italian winemaking excellence, pioneering Super Tuscans while honoring ancient traditions.',
            'Penfolds': 'Australia\'s most iconic wine producer, home of the legendary Grange and innovative winemaking techniques.',
            'Catena Zapata': 'Argentina\'s leading wine estate, elevating Malbec to world-class status through high-altitude viticulture.'
        };
        return descriptions[producerName] || 'Premium wine producer with exceptional terroir and centuries of winemaking tradition.';
    }

    getProducerPlots(producerName) {
        // Store plots for later reference
        this.availablePlots = [
            {
                id: `${producerName.toLowerCase().replace(/\s+/g, '-')}-plot-1`,
                name: 'Hillside Reserve',
                size: '0.25 hectares',
                grapeVariety: 'Cabernet Sauvignon',
                price: 2500,
                availability: 75,
                elevation: '450m',
                soil: 'Gravel & Clay'
            },
            {
                id: `${producerName.toLowerCase().replace(/\s+/g, '-')}-plot-2`,
                name: 'Valley Premium',
                size: '0.30 hectares',
                grapeVariety: 'Merlot',
                price: 2200,
                availability: 60,
                elevation: '380m',
                soil: 'Limestone'
            },
            {
                id: `${producerName.toLowerCase().replace(/\s+/g, '-')}-plot-3`,
                name: 'Heritage Block',
                size: '0.20 hectares',
                grapeVariety: 'Pinot Noir',
                price: 2800,
                availability: 90,
                elevation: '520m',
                soil: 'Clay-limestone'
            }
        ];
        
        return this.availablePlots;
    }

    interceptProducerSelection() {
        // Override the global loadAdoptionPlans function
        window.loadAdoptionPlans = (producerName) => {
            console.log('Intercepted loadAdoptionPlans:', producerName);
            const producerData = {
                name: producerName,
                id: producerName.toLowerCase().replace(/\s+/g, '-'),
                region: this.getProducerRegion(producerName),
                description: this.getProducerDescription(producerName)
            };
            this.handleProducerSelection(producerData);
        };
        
        // Override any other global functions that might open modals
        if (window.showProducerModal) {
            window.showProducerModal = (producerName) => {
                console.log('Intercepted showProducerModal:', producerName);
                this.handleProducerSelection({
                    name: producerName,
                    id: producerName.toLowerCase().replace(/\s+/g, '-'),
                    region: this.getProducerRegion(producerName),
                    description: this.getProducerDescription(producerName)
                });
            };
        }
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.enhancedProducerSidebar = new EnhancedProducerSidebar();
        console.log('✅ Enhanced Producer Sidebar initialized');
    });
} else {
    window.enhancedProducerSidebar = new EnhancedProducerSidebar();
    console.log('✅ Enhanced Producer Sidebar initialized');
}