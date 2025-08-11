/**
 * PlotSelection Class - Cart Management System for Wine Plot Selection
 * Handles plot cart operations, pricing, and session storage
 */

class PlotSelection {
    constructor(options = {}) {
        this.selectedPlots = new Map();
        this.maxSelections = options.maxSelections || 5;
        this.sessionKey = 'vinsdelux_selected_plots';
        this.apiEndpoint = options.apiEndpoint || '/api/plots/selection/';
        
        this.animations = {
            duration: 300,
            easing: 'cubic-bezier(0.4, 0, 0.2, 1)'
        };
        
        this.callbacks = {
            onPlotAdded: options.onPlotAdded || (() => {}),
            onPlotRemoved: options.onPlotRemoved || (() => {}),
            onSelectionLimitReached: options.onSelectionLimitReached || (() => {}),
            onCartUpdated: options.onCartUpdated || (() => {})
        };
        
        this.init();
    }
    
    init() {
        this.loadFromSession();
        this.setupEventListeners();
        this.updateCartDisplay();
        
        console.log('PlotSelection initialized with', this.selectedPlots.size, 'plots');
    }
    
    setupEventListeners() {
        // Listen for vineyard map events
        document.addEventListener('vineyardMap:plotSelected', (e) => {
            this.addPlot(e.detail.plot);
        });
        
        document.addEventListener('vineyardMap:plotDeselected', (e) => {
            this.removePlot(e.detail.plotId);
        });
        
        // Listen for plot selection from other components
        document.addEventListener('plotSelector:plotSelected', (e) => {
            this.addPlot(e.detail.plot);
        });
        
        // Cart action listeners
        document.addEventListener('click', (e) => {
            if (e.target.matches('.remove-plot-btn')) {
                const plotId = parseInt(e.target.dataset.plotId);
                this.removePlot(plotId);
            }
            
            if (e.target.matches('.clear-cart-btn')) {
                this.clearCart();
            }
            
            if (e.target.matches('.proceed-checkout-btn')) {
                this.proceedToCheckout();
            }
        });
    }
    
    addPlot(plot) {
        if (this.selectedPlots.size >= this.maxSelections) {
            this.handleSelectionLimitReached();
            return false;
        }
        
        if (this.selectedPlots.has(plot.id)) {
            this.showNotification('Plot already in your selection', 'warning');
            return false;
        }
        
        // Add plot with timestamp and additional metadata
        const plotData = {
            ...plot,
            addedAt: new Date().toISOString(),
            quantity: 1 // For future use if partial plot selection is allowed
        };
        
        this.selectedPlots.set(plot.id, plotData);
        
        // Animate addition
        this.animateCartAddition(plot);
        
        // Update storage and display
        this.saveToSession();
        this.updateCartDisplay();
        this.syncWithServer();
        
        // Trigger callbacks
        this.callbacks.onPlotAdded(plotData);
        this.callbacks.onCartUpdated(this.getCartSummary());
        
        this.showNotification(`${plot.name || 'Plot'} added to your selection`, 'success');
        
        return true;
    }
    
    removePlot(plotId) {
        const plot = this.selectedPlots.get(plotId);
        if (!plot) return false;
        
        this.selectedPlots.delete(plotId);
        
        // Animate removal
        this.animateCartRemoval(plotId);
        
        // Update storage and display
        this.saveToSession();
        this.updateCartDisplay();
        this.syncWithServer();
        
        // Trigger callbacks
        this.callbacks.onPlotRemoved(plot);
        this.callbacks.onCartUpdated(this.getCartSummary());
        
        this.showNotification(`${plot.name || 'Plot'} removed from selection`, 'info');
        
        return true;
    }
    
    clearCart() {
        if (this.selectedPlots.size === 0) return;
        
        const confirmation = confirm('Are you sure you want to clear your entire selection?');
        if (!confirmation) return;
        
        this.selectedPlots.clear();
        
        // Update storage and display
        this.saveToSession();
        this.updateCartDisplay();
        this.syncWithServer();
        
        // Trigger callback
        this.callbacks.onCartUpdated(this.getCartSummary());
        
        this.showNotification('Selection cleared', 'success');
    }
    
    updateCartDisplay() {
        this.updateCartBadge();
        this.updateCartSidebar();
        this.updateCartSummary();
        this.updateProceedButton();
    }
    
    updateCartBadge() {
        const badges = document.querySelectorAll('.cart-badge, .selection-count');
        const count = this.selectedPlots.size;
        
        badges.forEach(badge => {
            badge.textContent = count;
            badge.style.display = count > 0 ? 'inline-block' : 'none';
            
            // Add animation for count changes
            if (count > 0) {
                badge.classList.add('animate-pulse');
                setTimeout(() => badge.classList.remove('animate-pulse'), 500);
            }
        });
    }
    
    updateCartSidebar() {
        const sidebar = document.getElementById('cart-sidebar');
        if (!sidebar) return;
        
        const plotList = sidebar.querySelector('.selected-plots-list');
        if (!plotList) return;
        
        if (this.selectedPlots.size === 0) {
            plotList.innerHTML = `
                <div class="empty-cart">
                    <i class="fas fa-wine-glass-empty fa-3x mb-3 text-muted"></i>
                    <p class="text-muted">No plots selected yet</p>
                </div>
            `;
            return;
        }
        
        const plotsHTML = Array.from(this.selectedPlots.values()).map(plot => 
            this.createPlotCartItem(plot)
        ).join('');
        
        plotList.innerHTML = plotsHTML;
    }
    
    createPlotCartItem(plot) {
        const price = plot.price ? `€${plot.price.toLocaleString()}` : 'Price on request';
        const addedDate = new Date(plot.addedAt).toLocaleDateString();
        
        return `
            <div class="plot-cart-item" data-plot-id="${plot.id}">
                <div class="plot-image">
                    <img src="${this.getPlotImage(plot)}" alt="${plot.name}" />
                </div>
                <div class="plot-details">
                    <h5 class="plot-name">${plot.name || 'Wine Plot'}</h5>
                    <p class="plot-producer">${plot.producer || 'Premium Vineyard'}</p>
                    <p class="plot-region">${plot.region || 'Luxembourg'}</p>
                    <small class="text-muted">Added: ${addedDate}</small>
                </div>
                <div class="plot-price">
                    <span class="price-value">${price}</span>
                </div>
                <div class="plot-actions">
                    <button class="remove-plot-btn btn-icon" data-plot-id="${plot.id}" title="Remove plot">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
            </div>
        `;
    }
    
    updateCartSummary() {
        const summaries = document.querySelectorAll('.cart-summary');
        const summary = this.getCartSummary();
        
        summaries.forEach(summaryEl => {
            summaryEl.innerHTML = `
                <div class="summary-item">
                    <span>Selected Plots:</span>
                    <strong>${summary.totalPlots}</strong>
                </div>
                <div class="summary-item">
                    <span>Total Value:</span>
                    <strong>${summary.totalValue}</strong>
                </div>
                ${summary.averagePrice ? `
                <div class="summary-item">
                    <span>Average per Plot:</span>
                    <strong>${summary.averagePrice}</strong>
                </div>` : ''}
            `;
        });
    }
    
    updateProceedButton() {
        const buttons = document.querySelectorAll('.proceed-checkout-btn');
        const canProceed = this.selectedPlots.size > 0;
        
        buttons.forEach(button => {
            button.disabled = !canProceed;
            if (canProceed) {
                button.classList.remove('disabled');
                button.classList.add('pulse-animation');
            } else {
                button.classList.add('disabled');
                button.classList.remove('pulse-animation');
            }
        });
    }
    
    getCartSummary() {
        const plots = Array.from(this.selectedPlots.values());
        const totalPlots = plots.length;
        
        const plotsWithPrice = plots.filter(plot => plot.price && plot.price > 0);
        const totalValue = plotsWithPrice.reduce((sum, plot) => sum + plot.price, 0);
        
        return {
            totalPlots,
            totalValue: totalValue > 0 ? `€${totalValue.toLocaleString()}` : 'Price on request',
            averagePrice: totalValue > 0 && plotsWithPrice.length > 0 
                ? `€${Math.round(totalValue / plotsWithPrice.length).toLocaleString()}`
                : null,
            plots,
            hasValidPricing: plotsWithPrice.length > 0
        };
    }
    
    getPlotImage(plot) {
        // Return appropriate image based on plot data
        if (plot.image) return plot.image;
        if (plot.producer_image) return plot.producer_image;
        
        // Generate image based on region/wine type
        const wineTypes = ['red', 'white', 'rose', 'burgundy', 'bordeaux'];
        const type = plot.wine_type?.toLowerCase() || 'red';
        const imageType = wineTypes.includes(type) ? type : 'vineyard';
        const imageNum = (plot.id % 5) + 1;
        
        return `/static/images/vineyard-defaults/${imageType}_0${imageNum}.jpg`;
    }
    
    animateCartAddition(plot) {
        // Create floating animation for added plot
        const plotElement = document.querySelector(`[data-plot-id="${plot.id}"]`);
        if (!plotElement) return;
        
        const cartIcon = document.querySelector('.cart-icon, .selection-icon');
        if (!cartIcon) return;
        
        // Create floating element
        const floatingElement = plotElement.cloneNode(true);
        floatingElement.classList.add('floating-to-cart');
        floatingElement.style.position = 'fixed';
        floatingElement.style.zIndex = '9999';
        floatingElement.style.pointerEvents = 'none';
        
        const startRect = plotElement.getBoundingClientRect();
        const endRect = cartIcon.getBoundingClientRect();
        
        floatingElement.style.left = startRect.left + 'px';
        floatingElement.style.top = startRect.top + 'px';
        floatingElement.style.width = startRect.width + 'px';
        floatingElement.style.height = startRect.height + 'px';
        
        document.body.appendChild(floatingElement);
        
        // Animate to cart
        setTimeout(() => {
            floatingElement.style.transition = `all ${this.animations.duration}ms ${this.animations.easing}`;
            floatingElement.style.left = endRect.left + 'px';
            floatingElement.style.top = endRect.top + 'px';
            floatingElement.style.transform = 'scale(0.1)';
            floatingElement.style.opacity = '0';
            
            setTimeout(() => {
                floatingElement.remove();
            }, this.animations.duration);
        }, 50);
    }
    
    animateCartRemoval(plotId) {
        const cartItem = document.querySelector(`.plot-cart-item[data-plot-id="${plotId}"]`);
        if (!cartItem) return;
        
        cartItem.style.transition = `all ${this.animations.duration}ms ${this.animations.easing}`;
        cartItem.style.transform = 'translateX(100%)';
        cartItem.style.opacity = '0';
        
        setTimeout(() => {
            cartItem.remove();
        }, this.animations.duration);
    }
    
    handleSelectionLimitReached() {
        this.callbacks.onSelectionLimitReached(this.maxSelections);
        this.showNotification(
            `Maximum ${this.maxSelections} plots allowed. Remove a plot to select another.`,
            'warning',
            5000
        );
    }
    
    async proceedToCheckout() {
        if (this.selectedPlots.size === 0) {
            this.showNotification('Please select at least one plot to proceed', 'warning');
            return;
        }
        
        try {
            // Validate selection
            const isValid = await this.validateSelection();
            if (!isValid) return;
            
            // Store final selection
            const checkoutData = {
                plots: Array.from(this.selectedPlots.values()),
                summary: this.getCartSummary(),
                timestamp: new Date().toISOString()
            };
            
            sessionStorage.setItem('vinsdelux_checkout_data', JSON.stringify(checkoutData));
            
            // Navigate to next step
            window.location.href = '/vinsdelux/journey/personalize-wine/';
            
        } catch (error) {
            console.error('Checkout error:', error);
            this.showNotification('Unable to proceed. Please try again.', 'error');
        }
    }
    
    async validateSelection() {
        try {
            const plotIds = Array.from(this.selectedPlots.keys());
            const response = await fetch('/api/plots/validate/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCsrfToken()
                },
                body: JSON.stringify({ plot_ids: plotIds })
            });
            
            if (!response.ok) {
                throw new Error('Validation failed');
            }
            
            const result = await response.json();
            if (!result.valid) {
                this.showNotification(result.message || 'Some plots are no longer available', 'error');
                return false;
            }
            
            return true;
        } catch (error) {
            console.error('Validation error:', error);
            return true; // Proceed if validation service unavailable
        }
    }
    
    async syncWithServer() {
        try {
            const plotIds = Array.from(this.selectedPlots.keys());
            
            await fetch(this.apiEndpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.getCsrfToken()
                },
                body: JSON.stringify({
                    selected_plots: plotIds,
                    session_id: this.getSessionId()
                })
            });
            
        } catch (error) {
            console.error('Server sync error:', error);
            // Continue silently - local storage is primary
        }
    }
    
    saveToSession() {
        try {
            const data = {
                plots: Object.fromEntries(this.selectedPlots),
                timestamp: new Date().toISOString()
            };
            sessionStorage.setItem(this.sessionKey, JSON.stringify(data));
        } catch (error) {
            console.error('Session storage error:', error);
        }
    }
    
    loadFromSession() {
        try {
            const data = sessionStorage.getItem(this.sessionKey);
            if (data) {
                const parsed = JSON.parse(data);
                this.selectedPlots = new Map(Object.entries(parsed.plots).map(([k, v]) => [parseInt(k), v]));
            }
        } catch (error) {
            console.error('Session load error:', error);
            this.selectedPlots = new Map();
        }
    }
    
    getCsrfToken() {
        const token = document.querySelector('[name=csrfmiddlewaretoken]');
        return token ? token.value : '';
    }
    
    getSessionId() {
        return sessionStorage.getItem('session_id') || 'anonymous';
    }
    
    showNotification(message, type = 'info', duration = 3000) {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `notification toast-notification notification-${type}`;
        
        const icons = {
            success: 'fa-check-circle',
            error: 'fa-exclamation-circle',
            warning: 'fa-exclamation-triangle',
            info: 'fa-info-circle'
        };
        
        notification.innerHTML = `
            <div class="notification-content">
                <i class="fas ${icons[type] || icons.info}"></i>
                <span class="notification-message">${message}</span>
                <button class="notification-close" onclick="this.parentElement.parentElement.remove()">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
        
        // Position and show
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10000;
            min-width: 300px;
            max-width: 500px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
            transform: translateX(100%);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            overflow: hidden;
        `;
        
        // Type-specific styling
        const colors = {
            success: '#4CAF50',
            error: '#F44336',
            warning: '#FF9800',
            info: '#2196F3'
        };
        
        notification.style.borderLeft = `4px solid ${colors[type] || colors.info}`;
        
        document.body.appendChild(notification);
        
        // Animate in
        setTimeout(() => {
            notification.style.transform = 'translateX(0)';
        }, 50);
        
        // Auto remove
        setTimeout(() => {
            notification.style.transform = 'translateX(100%)';
            setTimeout(() => notification.remove(), 300);
        }, duration);
    }
    
    // Public API methods
    getSelectedPlots() {
        return Array.from(this.selectedPlots.values());
    }
    
    getSelectedPlotIds() {
        return Array.from(this.selectedPlots.keys());
    }
    
    hasPlot(plotId) {
        return this.selectedPlots.has(plotId);
    }
    
    getPlotCount() {
        return this.selectedPlots.size;
    }
    
    canAddMorePlots() {
        return this.selectedPlots.size < this.maxSelections;
    }
    
    destroy() {
        // Clean up event listeners and save final state
        this.saveToSession();
        this.selectedPlots.clear();
    }
}

// CSS styles for plot selection
const selectionStyles = `
<style>
.plot-cart-item {
    display: flex;
    align-items: center;
    padding: 15px;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    margin-bottom: 10px;
    background: white;
    transition: all 0.3s ease;
}

.plot-cart-item:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}

.plot-image {
    width: 60px;
    height: 60px;
    border-radius: 8px;
    overflow: hidden;
    margin-right: 15px;
    flex-shrink: 0;
}

.plot-image img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.plot-details {
    flex: 1;
    min-width: 0;
}

.plot-name {
    font-size: 16px;
    font-weight: 600;
    margin: 0 0 5px 0;
    color: #333;
}

.plot-producer, .plot-region {
    font-size: 14px;
    color: #666;
    margin: 2px 0;
}

.plot-price {
    text-align: right;
    margin-right: 15px;
}

.price-value {
    font-size: 18px;
    font-weight: 700;
    color: #722F37;
}

.plot-actions {
    flex-shrink: 0;
}

.remove-plot-btn {
    width: 32px;
    height: 32px;
    border: none;
    border-radius: 50%;
    background: #f5f5f5;
    color: #999;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.3s ease;
}

.remove-plot-btn:hover {
    background: #ff4757;
    color: white;
}

.cart-summary .summary-item {
    display: flex;
    justify-content: space-between;
    padding: 10px 0;
    border-bottom: 1px solid #eee;
}

.cart-summary .summary-item:last-child {
    border-bottom: none;
    font-size: 18px;
    font-weight: 700;
    color: #722F37;
}

.empty-cart {
    text-align: center;
    padding: 40px 20px;
}

.notification-content {
    display: flex;
    align-items: center;
    padding: 15px 20px;
    gap: 12px;
}

.notification-message {
    flex: 1;
    font-size: 14px;
    line-height: 1.4;
}

.notification-close {
    background: none;
    border: none;
    color: #999;
    cursor: pointer;
    padding: 5px;
}

.notification-close:hover {
    color: #333;
}

.animate-pulse {
    animation: pulse 0.5s ease-in-out;
}

@keyframes pulse {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.1); }
}

.pulse-animation {
    animation: pulse-glow 2s infinite;
}

@keyframes pulse-glow {
    0%, 100% {
        box-shadow: 0 4px 15px rgba(114, 47, 55, 0.3);
    }
    50% {
        box-shadow: 0 6px 25px rgba(114, 47, 55, 0.5);
    }
}

.floating-to-cart {
    pointer-events: none;
    z-index: 9999;
}
</style>
`;

// Inject styles
if (typeof document !== 'undefined') {
    document.head.insertAdjacentHTML('beforeend', selectionStyles);
}

// Make PlotSelection available globally for browser use
window.PlotSelection = PlotSelection;