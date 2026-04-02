/**
 * VinsDelux Interactive Journey Game - Fixed Version
 * Addresses common bugs and adds robust error handling
 */

class VinsDeluxJourneyGame {
    constructor() {
        this.currentStep = 1;
        this.totalSteps = 5;
        this.userScore = 0;
        this.achievements = [];
        this.startTime = Date.now();
        this.soundEnabled = true;
        this.journeyData = {
            plot: null,
            blend: {},
            techniques: {},
            delivery: {},
            legacy: null
        };
        
        // Add error boundary
        this.errorCount = 0;
        this.maxErrors = 10;
        
        try {
            this.init();
        } catch (error) {
            console.error('Failed to initialize game:', error);
            this.showErrorMessage('Failed to initialize game. Please refresh the page.');
        }
    }
    
    init() {
        // Check if DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.init());
            return;
        }
        
        this.setupEventListeners();
        this.initializeAnimations();
        this.loadSavedProgress();
        this.startGameTimer();
        this.initializeCanvases();
        this.setupKeyboardShortcuts();
        this.initializeDragDrop();
        this.hideLoading();
        
        // Add global error handler
        window.addEventListener('error', (e) => this.handleGlobalError(e));
        
        // Add image error handler
        this.setupImageErrorHandlers();
    }
    
    handleGlobalError(event) {
        this.errorCount++;
        console.error('Global error:', event.error);
        
        if (this.errorCount > this.maxErrors) {
            this.showErrorMessage('Too many errors occurred. Please refresh the page.');
        }
    }
    
    showErrorMessage(message) {
        const errorDiv = document.createElement('div');
        errorDiv.className = 'error-notification';
        errorDiv.textContent = message;
        errorDiv.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #f44336;
            color: white;
            padding: 15px 20px;
            border-radius: 5px;
            z-index: 10000;
            animation: slideIn 0.3s ease;
        `;
        document.body.appendChild(errorDiv);
        
        setTimeout(() => errorDiv.remove(), 5000);
    }
    
    // Core Navigation with Error Handling
    setupEventListeners() {
        // Use safe event listener attachment
        this.safeAddEventListener('.btn-next', 'click', () => this.nextStep());
        this.safeAddEventListener('.btn-prev', 'click', () => this.previousStep());
        
        // Step dots with error handling
        const dots = document.querySelectorAll('.step-dots .dot');
        dots.forEach(dot => {
            if (dot) {
                dot.addEventListener('click', (e) => {
                    try {
                        const step = parseInt(e.target.dataset.step);
                        if (!isNaN(step) && step <= this.getUnlockedSteps()) {
                            this.goToStep(step);
                        }
                    } catch (error) {
                        console.error('Error in step navigation:', error);
                    }
                });
            }
        });
        
        // Other listeners with safe attachment
        this.safeAddEventListener('#save-progress', 'click', () => this.saveProgress());
        this.safeAddEventListener('#toggle-sound', 'click', () => this.toggleSound());
        this.safeAddEventListener('#show-help', 'click', () => this.showHelp());
        
        // Step-specific listeners with error handling
        try {
            this.setupPlotSelection();
            this.setupWineBuilder();
            this.setupProductionTimeline();
            this.setupDeliveryOptions();
            this.setupLegacyBuilder();
        } catch (error) {
            console.error('Error setting up step listeners:', error);
        }
    }
    
    safeAddEventListener(selector, event, handler) {
        const element = document.querySelector(selector);
        if (element) {
            element.addEventListener(event, (e) => {
                try {
                    handler(e);
                } catch (error) {
                    console.error(`Error in ${event} handler for ${selector}:`, error);
                }
            });
        } else {
            console.warn(`Element not found: ${selector}`);
        }
    }
    
    // Step Navigation with Validation
    nextStep() {
        try {
            if (this.currentStep < this.totalSteps) {
                if (this.validateCurrentStep()) {
                    this.completeStep(this.currentStep);
                    this.currentStep++;
                    this.goToStep(this.currentStep);
                    this.awardPoints(100);
                    this.checkAchievements();
                } else {
                    this.showNotification('Please complete all required fields', 'warning');
                }
            }
        } catch (error) {
            console.error('Error navigating to next step:', error);
            this.showNotification('Navigation error. Please try again.', 'error');
        }
    }
    
    previousStep() {
        try {
            if (this.currentStep > 1) {
                this.currentStep--;
                this.goToStep(this.currentStep);
            }
        } catch (error) {
            console.error('Error navigating to previous step:', error);
        }
    }
    
    goToStep(step) {
        try {
            // Validate step number
            if (step < 1 || step > this.totalSteps) {
                console.error('Invalid step number:', step);
                return;
            }
            
            // Hide all steps
            document.querySelectorAll('.journey-step').forEach(section => {
                if (section) {
                    section.classList.remove('active');
                    section.classList.add('hidden');
                }
            });
            
            // Show target step
            const targetStep = document.querySelector(`.journey-step[data-step="${step}"]`);
            if (targetStep) {
                targetStep.classList.remove('hidden');
                targetStep.classList.add('active');
                this.animateStepEntry(targetStep);
            } else {
                console.error('Step element not found:', step);
            }
            
            this.currentStep = step;
            this.updateProgressIndicators();
            this.updateNavigationButtons();
            
            // Step-specific initialization
            this.initializeStepContent(step);
            
        } catch (error) {
            console.error('Error changing step:', error);
        }
    }
    
    initializeStepContent(step) {
        try {
            switch(step) {
                case 1:
                    this.initializePlotMap();
                    break;
                case 2:
                    this.initializeWineBuilder();
                    break;
                case 3:
                    this.initializeProductionTimeline();
                    break;
                case 4:
                    this.initializeDeliveryOptions();
                    break;
                case 5:
                    this.initializeLegacyBuilder();
                    break;
            }
        } catch (error) {
            console.error(`Error initializing step ${step} content:`, error);
        }
    }
    
    updateNavigationButtons() {
        const prevBtn = document.querySelector('.btn-prev');
        const nextBtn = document.querySelector('.btn-next');
        
        if (prevBtn) {
            prevBtn.disabled = this.currentStep === 1;
            prevBtn.style.opacity = this.currentStep === 1 ? '0.5' : '1';
        }
        
        if (nextBtn) {
            if (this.currentStep === this.totalSteps) {
                nextBtn.textContent = 'Complete Journey';
                nextBtn.classList.add('btn-complete');
            } else {
                nextBtn.textContent = 'Next Step';
                nextBtn.classList.remove('btn-complete');
            }
        }
    }
    
    updateProgressIndicators() {
        // Update step dots
        document.querySelectorAll('.step-dots .dot').forEach((dot, index) => {
            if (dot) {
                dot.classList.toggle('active', index + 1 === this.currentStep);
                dot.classList.toggle('completed', index + 1 < this.currentStep);
            }
        });
        
        // Update milestones
        document.querySelectorAll('.milestone').forEach((milestone, index) => {
            if (milestone) {
                milestone.classList.toggle('active', index + 1 === this.currentStep);
                milestone.classList.toggle('completed', index + 1 < this.currentStep);
            }
        });
        
        // Update progress bar
        const progress = ((this.currentStep - 1) / (this.totalSteps - 1)) * 100;
        const progressFill = document.querySelector('.progress-fill');
        if (progressFill) {
            progressFill.style.width = `${progress}%`;
        }
        
        // Update completion rate
        const completionElement = document.getElementById('completion-rate');
        if (completionElement) {
            completionElement.textContent = Math.round(progress);
        }
    }
    
    // Fixed Plot Selection
    setupPlotSelection() {
        // Plot cards
        document.querySelectorAll('.plot-card').forEach(card => {
            if (card) {
                card.addEventListener('click', (e) => {
                    try {
                        const plotId = e.currentTarget.dataset.plotId;
                        if (plotId) {
                            this.selectPlot(plotId);
                        }
                    } catch (error) {
                        console.error('Error selecting plot:', error);
                    }
                });
            }
        });
        
        // Map controls with safe listeners
        this.safeAddEventListener('.map-zoom-in', 'click', () => this.zoomMap(1.2));
        this.safeAddEventListener('.map-zoom-out', 'click', () => this.zoomMap(0.8));
        this.safeAddEventListener('.map-reset', 'click', () => this.resetMap());
        
        // Plot selection button
        this.safeAddEventListener('.btn-select-plot', 'click', (e) => {
            const plotId = e.target.dataset.plotId;
            if (plotId) {
                this.confirmPlotSelection(plotId);
            } else {
                this.showNotification('Please select a plot first', 'warning');
            }
        });
    }
    
    initializePlotMap() {
        const mapCanvas = document.getElementById('map-canvas');
        if (!mapCanvas) {
            console.warn('Map canvas not found');
            return;
        }
        
        // Check if it's actually a canvas element
        if (mapCanvas.tagName !== 'CANVAS') {
            console.warn('map-canvas is not a canvas element, converting...');
            // Create a canvas element if it's not
            const newCanvas = document.createElement('canvas');
            newCanvas.id = 'map-canvas';
            newCanvas.className = mapCanvas.className;
            newCanvas.width = 800;
            newCanvas.height = 600;
            mapCanvas.parentNode.replaceChild(newCanvas, mapCanvas);
            this.initializePlotMap(); // Retry with new canvas
            return;
        }
        
        const ctx = mapCanvas.getContext('2d');
        if (!ctx) {
            console.error('Could not get canvas context');
            return;
        }
        
        // Draw basic map
        ctx.fillStyle = '#8BC34A';
        ctx.fillRect(0, 0, mapCanvas.width, mapCanvas.height);
        
        // Add plot markers
        const plots = [
            { id: 'plot-1', x: 100, y: 100, name: 'Sunrise Valley' },
            { id: 'plot-2', x: 250, y: 150, name: 'Mountain Ridge' },
            { id: 'plot-3', x: 150, y: 250, name: 'River Bend' }
        ];
        
        plots.forEach(plot => {
            this.drawPlotMarker(ctx, plot);
        });
    }
    
    drawPlotMarker(ctx, plot) {
        ctx.beginPath();
        ctx.arc(plot.x, plot.y, 20, 0, Math.PI * 2);
        ctx.fillStyle = '#722f37';
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.stroke();
        
        // Add label
        ctx.fillStyle = '#333';
        ctx.font = '12px Arial';
        ctx.textAlign = 'center';
        ctx.fillText(plot.name, plot.x, plot.y + 35);
    }
    
    selectPlot(plotId) {
        try {
            // Remove previous selections
            document.querySelectorAll('.plot-card.selected').forEach(card => {
                card.classList.remove('selected');
            });
            document.querySelectorAll('.plot-marker.selected').forEach(marker => {
                marker.classList.remove('selected');
            });
            
            // Add new selection
            const selectedCard = document.querySelector(`.plot-card[data-plot-id="${plotId}"]`);
            const selectedMarker = document.querySelector(`.plot-marker[data-plot-id="${plotId}"]`);
            
            if (selectedCard) selectedCard.classList.add('selected');
            if (selectedMarker) selectedMarker.classList.add('selected');
            
            this.journeyData.plot = plotId;
            this.awardPoints(50);
            this.showPlotDetails(plotId);
            
        } catch (error) {
            console.error('Error in plot selection:', error);
        }
    }
    
    showPlotDetails(plotId) {
        const panel = document.getElementById('plot-details');
        if (!panel) return;
        
        panel.classList.add('active');
        
        // Update details safely
        this.safeUpdateElement('#plot-name', `Plot ${plotId}`);
        this.safeUpdateElement('#plot-size', `${Math.floor(Math.random() * 5 + 2)} hectares`);
        this.safeUpdateElement('#plot-elevation', `${Math.floor(Math.random() * 300 + 100)}m`);
        this.safeUpdateElement('#plot-exposure', 'South-facing');
        this.safeUpdateElement('#plot-soil', 'Clay-limestone');
        
        const selectBtn = document.querySelector('.btn-select-plot');
        if (selectBtn) {
            selectBtn.dataset.plotId = plotId;
        }
    }
    
    safeUpdateElement(selector, value) {
        const element = document.querySelector(selector);
        if (element) {
            element.textContent = value;
        }
    }
    
    // Fixed Wine Builder
    setupWineBuilder() {
        const grapeItems = document.querySelectorAll('.grape-item');
        const blendContainer = document.getElementById('blend-container');
        
        if (!grapeItems.length || !blendContainer) {
            console.warn('Wine builder elements not found');
            return;
        }
        
        grapeItems.forEach(item => {
            if (item) {
                item.addEventListener('dragstart', (e) => {
                    try {
                        e.dataTransfer.effectAllowed = 'copy';
                        e.dataTransfer.setData('grape', e.target.dataset.grape || '');
                        e.target.classList.add('dragging');
                    } catch (error) {
                        console.error('Drag start error:', error);
                    }
                });
                
                item.addEventListener('dragend', (e) => {
                    e.target.classList.remove('dragging');
                });
            }
        });
        
        if (blendContainer) {
            blendContainer.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'copy';
                blendContainer.classList.add('drag-over');
            });
            
            blendContainer.addEventListener('dragleave', () => {
                blendContainer.classList.remove('drag-over');
            });
            
            blendContainer.addEventListener('drop', (e) => {
                e.preventDefault();
                blendContainer.classList.remove('drag-over');
                
                try {
                    const grape = e.dataTransfer.getData('grape');
                    if (grape && !this.journeyData.blend[grape]) {
                        this.addGrapeToBlend(grape);
                    }
                } catch (error) {
                    console.error('Drop error:', error);
                }
            });
        }
    }
    
    initializeWineBuilder() {
        this.updateBlendChart();
        this.updateWineProfile();
        this.generateTastingNotes();
    }
    
    addGrapeToBlend(grape) {
        if (Object.keys(this.journeyData.blend).length >= 5) {
            this.showNotification('Maximum 5 grape varieties allowed', 'warning');
            return;
        }
        
        this.journeyData.blend[grape] = 20;
        this.updateBlendPercentages();
        this.updateBlendChart();
        this.updateWineProfile();
        this.awardPoints(25);
    }
    
    updateBlendPercentages() {
        const container = document.getElementById('blend-percentages');
        if (!container) return;
        
        container.innerHTML = '';
        
        Object.entries(this.journeyData.blend).forEach(([grape, percentage]) => {
            const div = document.createElement('div');
            div.className = 'blend-item';
            div.innerHTML = `
                <span>${grape}</span>
                <input type="range" class="blend-slider" min="0" max="100" 
                       value="${percentage}" data-grape="${grape}">
                <span class="percentage">${percentage}%</span>
                <button class="remove-grape" data-grape="${grape}">×</button>
            `;
            container.appendChild(div);
        });
        
        // Add event listeners to new elements
        container.querySelectorAll('.blend-slider').forEach(slider => {
            slider.addEventListener('input', (e) => {
                const grape = e.target.dataset.grape;
                const value = parseInt(e.target.value);
                this.journeyData.blend[grape] = value;
                e.target.nextElementSibling.textContent = `${value}%`;
                this.updateBlendChart();
            });
        });
        
        container.querySelectorAll('.remove-grape').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const grape = e.target.dataset.grape;
                delete this.journeyData.blend[grape];
                this.updateBlendPercentages();
                this.updateBlendChart();
            });
        });
    }
    
    updateBlendChart() {
        const canvas = document.getElementById('blend-chart');
        if (!canvas) return;
        
        // Ensure it's a canvas element
        if (canvas.tagName !== 'CANVAS') {
            console.warn('blend-chart is not a canvas element');
            return;
        }
        
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        
        const width = canvas.width;
        const height = canvas.height;
        const centerX = width / 2;
        const centerY = height / 2;
        const radius = Math.min(width, height) / 2 - 20;
        
        ctx.clearRect(0, 0, width, height);
        
        const data = Object.entries(this.journeyData.blend);
        if (data.length === 0) return;
        
        let currentAngle = -Math.PI / 2;
        const total = data.reduce((sum, [_, value]) => sum + value, 0);
        
        const colors = ['#722f37', '#d4af37', '#8B4513', '#CD853F', '#DEB887'];
        
        data.forEach(([grape, value], index) => {
            const sliceAngle = (value / total) * 2 * Math.PI;
            
            // Draw slice
            ctx.beginPath();
            ctx.arc(centerX, centerY, radius, currentAngle, currentAngle + sliceAngle);
            ctx.lineTo(centerX, centerY);
            ctx.fillStyle = colors[index % colors.length];
            ctx.fill();
            
            // Draw label
            const labelAngle = currentAngle + sliceAngle / 2;
            const labelX = centerX + Math.cos(labelAngle) * (radius * 0.7);
            const labelY = centerY + Math.sin(labelAngle) * (radius * 0.7);
            
            ctx.fillStyle = '#fff';
            ctx.font = '12px Arial';
            ctx.textAlign = 'center';
            ctx.fillText(`${grape}`, labelX, labelY);
            ctx.fillText(`${Math.round((value/total) * 100)}%`, labelX, labelY + 15);
            
            currentAngle += sliceAngle;
        });
    }
    
    updateWineProfile() {
        const canvas = document.getElementById('wine-profile-chart');
        if (!canvas) return;
        
        // Ensure it's a canvas element
        if (canvas.tagName !== 'CANVAS') {
            console.warn('wine-profile-chart is not a canvas element');
            return;
        }
        
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        
        const width = canvas.width;
        const height = canvas.height;
        
        ctx.clearRect(0, 0, width, height);
        
        // Draw radar chart
        const categories = ['Body', 'Tannins', 'Acidity', 'Sweetness', 'Alcohol'];
        const values = categories.map(() => Math.random() * 100);
        
        const centerX = width / 2;
        const centerY = height / 2;
        const radius = Math.min(width, height) / 2 - 30;
        const angleStep = (Math.PI * 2) / categories.length;
        
        // Draw grid
        for (let i = 1; i <= 5; i++) {
            ctx.beginPath();
            ctx.strokeStyle = 'rgba(0,0,0,0.1)';
            
            for (let j = 0; j < categories.length; j++) {
                const angle = j * angleStep - Math.PI / 2;
                const x = centerX + Math.cos(angle) * (radius * i / 5);
                const y = centerY + Math.sin(angle) * (radius * i / 5);
                
                if (j === 0) {
                    ctx.moveTo(x, y);
                } else {
                    ctx.lineTo(x, y);
                }
            }
            ctx.closePath();
            ctx.stroke();
        }
        
        // Draw data
        ctx.beginPath();
        ctx.fillStyle = 'rgba(114, 47, 55, 0.3)';
        ctx.strokeStyle = '#722f37';
        ctx.lineWidth = 2;
        
        categories.forEach((category, index) => {
            const angle = index * angleStep - Math.PI / 2;
            const value = values[index];
            const x = centerX + Math.cos(angle) * (radius * value / 100);
            const y = centerY + Math.sin(angle) * (radius * value / 100);
            
            if (index === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
            
            // Draw labels
            const labelX = centerX + Math.cos(angle) * (radius + 20);
            const labelY = centerY + Math.sin(angle) * (radius + 20);
            
            ctx.save();
            ctx.fillStyle = '#333';
            ctx.font = '12px Arial';
            ctx.textAlign = 'center';
            ctx.fillText(category, labelX, labelY);
            ctx.restore();
        });
        
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
    }
    
    generateTastingNotes() {
        const container = document.getElementById('tasting-notes');
        if (!container) return;
        
        const notes = [
            'Notes of dark cherry and blackberry',
            'Hints of vanilla and oak',
            'Silky tannins with a long finish',
            'Well-balanced acidity'
        ];
        
        container.innerHTML = notes.map(note => 
            `<div class="note-item">• ${note}</div>`
        ).join('');
    }
    
    // Production Timeline
    setupProductionTimeline() {
        this.safeAddEventListener('.btn-play-timeline', 'click', () => this.playTimeline());
        
        document.querySelectorAll('.timeline-event').forEach(event => {
            if (event) {
                event.addEventListener('click', (e) => {
                    const stage = e.currentTarget.dataset.stage;
                    if (stage) {
                        this.showStageDetails(stage);
                    }
                });
            }
        });
    }
    
    initializeProductionTimeline() {
        this.updateWeatherWidget();
    }
    
    playTimeline() {
        const events = document.querySelectorAll('.timeline-event');
        let currentIndex = 0;
        
        const animateNext = () => {
            if (currentIndex >= events.length) return;
            
            const event = events[currentIndex];
            if (event) {
                event.classList.add('active');
                event.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
            
            currentIndex++;
            if (currentIndex < events.length) {
                setTimeout(animateNext, 800);
            }
        };
        
        // Reset all events
        events.forEach(event => event.classList.remove('active'));
        animateNext();
        
        // Update progress bar
        const progress = document.querySelector('.timeline-progress');
        if (progress) {
            progress.style.transition = `width ${events.length * 0.8}s linear`;
            progress.style.width = '100%';
        }
    }
    
    updateWeatherWidget() {
        const temp = Math.floor(Math.random() * 15 + 15);
        const conditions = ['Sunny', 'Partly Cloudy', 'Clear'];
        const condition = conditions[Math.floor(Math.random() * conditions.length)];
        
        this.safeUpdateElement('.temperature', `${temp}°C`);
        
        const weatherDetails = document.querySelector('.weather-details');
        if (weatherDetails) {
            weatherDetails.innerHTML = `
                <div>Condition: ${condition}</div>
                <div>Humidity: ${Math.floor(Math.random() * 30 + 40)}%</div>
                <div>Wind: ${Math.floor(Math.random() * 10 + 5)} km/h</div>
            `;
        }
    }
    
    // Delivery Options
    setupDeliveryOptions() {
        document.querySelectorAll('input[name="delivery-type"]').forEach(input => {
            if (input) {
                input.addEventListener('change', (e) => {
                    this.journeyData.delivery.type = e.target.value;
                    this.updatePackagePreview(e.target.value);
                    this.awardPoints(30);
                });
            }
        });
    }
    
    initializeDeliveryOptions() {
        this.setup3DPackage();
        this.setupVirtualTastingRoom();
    }
    
    updatePackagePreview(type) {
        const packageBox = document.querySelector('.package-box');
        if (!packageBox) return;
        
        const colors = {
            standard: 'linear-gradient(135deg, #8B4513, #A0522D)',
            express: 'linear-gradient(135deg, #FFD700, #FFA500)',
            ceremony: 'linear-gradient(135deg, #4B0082, #8B008B)'
        };
        
        packageBox.style.background = colors[type] || colors.standard;
        
        // Update contents
        const contents = {
            standard: ['6 Bottles', 'Tasting Notes', 'Certificate'],
            express: ['6 Bottles', 'Tasting Notes', 'Certificate', 'Priority Handling'],
            ceremony: ['6 Bottles', 'Tasting Notes', 'Certificate', 'Sommelier Visit', 'Crystal Glasses']
        };
        
        this.updatePackageContents(contents[type] || contents.standard);
    }
    
    updatePackageContents(items) {
        const contentsList = document.querySelector('.contents-list');
        if (!contentsList) return;
        
        contentsList.innerHTML = items.map(item => 
            `<li class="content-item">${item}</li>`
        ).join('');
    }
    
    setup3DPackage() {
        const packageBox = document.querySelector('.package-box');
        if (!packageBox) return;
        
        let rotationY = 0;
        packageBox.addEventListener('click', () => {
            rotationY += 90;
            packageBox.style.transform = `rotateY(${rotationY}deg)`;
        });
    }
    
    setupVirtualTastingRoom() {
        const canvas = document.getElementById('tasting-room-3d');
        if (!canvas) return;
        
        // Ensure it's a canvas element
        if (canvas.tagName !== 'CANVAS') {
            console.warn('tasting-room-3d is not a canvas element');
            return;
        }
        
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        
        // Draw simple tasting room
        ctx.fillStyle = '#2c1810';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        
        // Add room controls
        document.querySelectorAll('.room-controls .control-btn').forEach(btn => {
            if (btn) {
                btn.addEventListener('click', (e) => {
                    const action = e.target.dataset.action;
                    this.handleRoomControl(action);
                });
            }
        });
    }
    
    handleRoomControl(action) {
        console.log('Room control:', action);
        // Implement room controls
    }
    
    // Legacy Builder
    setupLegacyBuilder() {
        document.querySelectorAll('.btn-select-legacy').forEach(btn => {
            if (btn) {
                btn.addEventListener('click', (e) => {
                    const legacyType = e.target.dataset.legacy;
                    if (legacyType) {
                        this.selectLegacy(legacyType);
                    }
                });
            }
        });
        
        this.safeAddEventListener('.btn-complete-journey', 'click', () => {
            this.completeJourney();
        });
        
        this.safeAddEventListener('.btn-share-journey', 'click', () => {
            this.shareJourney();
        });
    }
    
    initializeLegacyBuilder() {
        this.drawLegacyTimeline();
        this.updateJourneySummary();
    }
    
    selectLegacy(type) {
        document.querySelectorAll('.legacy-card').forEach(card => {
            card.classList.remove('selected');
        });
        
        const selectedCard = document.querySelector(`.legacy-card .btn-select-legacy[data-legacy="${type}"]`);
        if (selectedCard) {
            selectedCard.closest('.legacy-card').classList.add('selected');
        }
        
        this.journeyData.legacy = type;
        this.awardPoints(150);
        this.updateLegacyTimeline(type);
    }
    
    drawLegacyTimeline() {
        const canvas = document.getElementById('legacy-timeline-chart');
        if (!canvas) return;
        
        // Ensure it's a canvas element
        if (canvas.tagName !== 'CANVAS') {
            console.warn('legacy-timeline-chart is not a canvas element');
            return;
        }
        
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Draw timeline
        ctx.strokeStyle = '#722f37';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(50, canvas.height / 2);
        ctx.lineTo(canvas.width - 50, canvas.height / 2);
        ctx.stroke();
        
        // Draw milestones
        const milestones = [2024, 2029, 2034, 2039, 2044];
        const spacing = (canvas.width - 100) / (milestones.length - 1);
        
        milestones.forEach((year, index) => {
            const x = 50 + index * spacing;
            const y = canvas.height / 2;
            
            ctx.beginPath();
            ctx.arc(x, y, 8, 0, Math.PI * 2);
            ctx.fillStyle = '#722f37';
            ctx.fill();
            
            ctx.fillStyle = '#333';
            ctx.font = '12px Arial';
            ctx.textAlign = 'center';
            ctx.fillText(year.toString(), x, y + 30);
        });
    }
    
    updateLegacyTimeline(type) {
        const canvas = document.getElementById('legacy-timeline-chart');
        if (!canvas) return;
        
        // Redraw with selected legacy highlights
        this.drawLegacyTimeline();
        
        // Add legacy-specific elements
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        
        ctx.fillStyle = 'rgba(212, 175, 55, 0.3)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    
    updateJourneySummary() {
        const finalScore = this.userScore + 500; // Bonus for completion
        
        this.safeUpdateElement('#selected-plot-name', `Plot ${this.journeyData.plot || 'Not Selected'}`);
        this.safeUpdateElement('#wine-blend-summary', this.getBlendSummary());
        this.safeUpdateElement('#total-xp', finalScore.toString());
    }
    
    getBlendSummary() {
        const blend = this.journeyData.blend;
        if (!blend || Object.keys(blend).length === 0) {
            return 'No blend selected';
        }
        
        return Object.entries(blend)
            .map(([grape, percentage]) => `${grape} (${percentage}%)`)
            .join(', ');
    }
    
    completeJourney() {
        if (!this.validateJourney()) {
            this.showNotification('Please complete all steps before finishing', 'warning');
            return;
        }
        
        this.awardPoints(1000);
        this.unlockAchievement('Journey Master', 'Completed your wine journey', 500);
        this.saveProgress();
        
        this.showNotification('🎉 Congratulations! Your wine journey is complete!', 'success');
        
        // Trigger confetti
        this.celebrateCompletion();
    }
    
    validateJourney() {
        return this.journeyData.plot && 
               Object.keys(this.journeyData.blend).length > 0 &&
               this.journeyData.delivery.type &&
               this.journeyData.legacy;
    }
    
    celebrateCompletion() {
        // Simple confetti effect
        const colors = ['#722f37', '#d4af37', '#8B4513'];
        
        for (let i = 0; i < 100; i++) {
            const confetti = document.createElement('div');
            confetti.className = 'confetti';
            confetti.style.cssText = `
                position: fixed;
                top: -10px;
                left: ${Math.random() * 100}%;
                width: 10px;
                height: 10px;
                background: ${colors[Math.floor(Math.random() * colors.length)]};
                animation: fall ${Math.random() * 3 + 2}s linear;
                z-index: 10000;
            `;
            document.body.appendChild(confetti);
            
            setTimeout(() => confetti.remove(), 5000);
        }
    }
    
    shareJourney() {
        const shareData = {
            title: 'My VinsDelux Wine Journey',
            text: `I just completed my wine journey with ${this.userScore} points!`,
            url: window.location.href
        };
        
        if (navigator.share) {
            navigator.share(shareData).catch(err => console.error('Share failed:', err));
        } else {
            // Fallback
            this.copyToClipboard(shareData.text + ' ' + shareData.url);
            this.showNotification('Share link copied to clipboard!', 'success');
        }
    }
    
    copyToClipboard(text) {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
    }
    
    // Validation
    validateCurrentStep() {
        switch(this.currentStep) {
            case 1:
                return this.journeyData.plot !== null;
            case 2:
                return Object.keys(this.journeyData.blend).length > 0;
            case 3:
                return true; // Timeline viewing doesn't require validation
            case 4:
                return this.journeyData.delivery.type !== undefined;
            case 5:
                return this.journeyData.legacy !== null;
            default:
                return true;
        }
    }
    
    // Points and Achievements
    awardPoints(points) {
        this.userScore += points;
        this.updateScore();
        this.showFloatingPoints(points);
    }
    
    updateScore() {
        const scoreElement = document.getElementById('user-points');
        if (scoreElement) {
            scoreElement.textContent = this.userScore;
        }
    }
    
    showFloatingPoints(points) {
        const floater = document.createElement('div');
        floater.className = 'floating-points';
        floater.textContent = `+${points}`;
        floater.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            color: #d4af37;
            font-size: 24px;
            font-weight: bold;
            animation: floatUp 2s ease-out forwards;
            z-index: 10000;
            pointer-events: none;
        `;
        document.body.appendChild(floater);
        
        setTimeout(() => floater.remove(), 2000);
    }
    
    checkAchievements() {
        if (this.userScore >= 100 && !this.achievements.includes('first-century')) {
            this.unlockAchievement('First Century', 'Earned 100 points', 50);
        }
        
        if (this.userScore >= 500 && !this.achievements.includes('halfway-there')) {
            this.unlockAchievement('Halfway There', 'Earned 500 points', 100);
        }
        
        if (this.currentStep === 3 && !this.achievements.includes('wine-watcher')) {
            this.unlockAchievement('Wine Watcher', 'Reached production stage', 75);
        }
    }
    
    unlockAchievement(title, description, points) {
        if (this.achievements.includes(title)) return;
        
        this.achievements.push(title);
        this.awardPoints(points);
        
        const popup = document.getElementById('achievement-popup');
        if (popup) {
            const titleEl = popup.querySelector('.achievement-title');
            const descEl = popup.querySelector('.achievement-description');
            const pointsEl = popup.querySelector('.achievement-points span');
            
            if (titleEl) titleEl.textContent = title;
            if (descEl) descEl.textContent = description;
            if (pointsEl) pointsEl.textContent = points;
            
            popup.classList.add('show');
            setTimeout(() => popup.classList.remove('show'), 3000);
        }
    }
    
    // Save/Load
    saveProgress() {
        try {
            const saveData = {
                currentStep: this.currentStep,
                userScore: this.userScore,
                achievements: this.achievements,
                journeyData: this.journeyData,
                timestamp: Date.now()
            };
            
            localStorage.setItem('vinsdelux_journey', JSON.stringify(saveData));
            this.showNotification('Progress saved!', 'success');
        } catch (error) {
            console.error('Failed to save progress:', error);
            this.showNotification('Failed to save progress', 'error');
        }
    }
    
    loadSavedProgress() {
        try {
            const saved = localStorage.getItem('vinsdelux_journey');
            if (saved) {
                const data = JSON.parse(saved);
                
                this.currentStep = data.currentStep || 1;
                this.userScore = data.userScore || 0;
                this.achievements = data.achievements || [];
                this.journeyData = data.journeyData || this.journeyData;
                
                this.updateScore();
                this.goToStep(this.currentStep);
                
                this.showNotification('Progress loaded!', 'success');
            }
        } catch (error) {
            console.error('Failed to load saved progress:', error);
            localStorage.removeItem('vinsdelux_journey');
        }
    }
    
    // Utilities
    getUnlockedSteps() {
        // For now, allow access to all completed steps plus the current one
        return this.currentStep;
    }
    
    completeStep(step) {
        const milestone = document.querySelector(`.milestone[data-step="${step}"]`);
        if (milestone) {
            milestone.classList.add('completed');
        }
        
        const completedSteps = document.querySelectorAll('.milestone.completed').length;
        const completionPercentage = (completedSteps / this.totalSteps) * 100;
        
        const completionElement = document.getElementById('completion-rate');
        if (completionElement) {
            completionElement.textContent = Math.round(completionPercentage);
        }
    }
    
    showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.textContent = message;
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 20px;
            border-radius: 5px;
            z-index: 10000;
            animation: slideIn 0.3s ease;
            background: ${type === 'success' ? '#4caf50' : type === 'warning' ? '#ff9800' : type === 'error' ? '#f44336' : '#2196f3'};
            color: white;
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }
    
    startGameTimer() {
        setInterval(() => {
            const elapsed = Date.now() - this.startTime;
            const minutes = Math.floor(elapsed / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);
            
            const timeElement = document.getElementById('time-spent');
            if (timeElement) {
                timeElement.textContent = `${minutes}:${seconds.toString().padStart(2, '0')}`;
            }
        }, 1000);
    }
    
    hideLoading() {
        const loadingOverlay = document.getElementById('loading-overlay');
        if (loadingOverlay) {
            loadingOverlay.style.display = 'none';
        }
    }
    
    setupImageErrorHandlers() {
        // Handle image loading errors
        document.querySelectorAll('img').forEach(img => {
            img.onerror = function() {
                // Add error class for CSS fallback
                this.classList.add('error');
                
                // Create SVG fallback based on alt text
                const alt = this.alt || 'Image';
                const width = this.width || 100;
                const height = this.height || 100;
                
                // Create inline SVG as fallback
                const svgFallback = `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 ${width} ${height}'%3E%3Crect width='${width}' height='${height}' fill='%23f0f0f0'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' fill='%23999' font-family='Arial' font-size='14'%3E${encodeURIComponent(alt)}%3C/text%3E%3C/svg%3E`;
                
                // Only set src once to avoid infinite loop
                if (!this.dataset.fallbackApplied) {
                    this.dataset.fallbackApplied = 'true';
                    this.src = svgFallback;
                }
            };
            
            // Check if image is already broken
            if (img.complete && img.naturalHeight === 0) {
                img.onerror();
            }
        });
        
        // Re-run when new images are added to DOM
        const observer = new MutationObserver(() => {
            document.querySelectorAll('img:not([data-error-handler])').forEach(img => {
                img.dataset.errorHandler = 'true';
                img.onerror = function() {
                    this.classList.add('error');
                    const alt = this.alt || 'Image';
                    const svgFallback = `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'%3E%3Crect width='200' height='200' fill='%23f0f0f0'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' fill='%23999' font-family='Arial' font-size='14'%3E${encodeURIComponent(alt)}%3C/text%3E%3C/svg%3E`;
                    if (!this.dataset.fallbackApplied) {
                        this.dataset.fallbackApplied = 'true';
                        this.src = svgFallback;
                    }
                };
            });
        });
        
        observer.observe(document.body, { childList: true, subtree: true });
    }
    
    toggleSound() {
        this.soundEnabled = !this.soundEnabled;
        
        const soundBtn = document.getElementById('toggle-sound');
        if (soundBtn) {
            soundBtn.textContent = this.soundEnabled ? '🔊' : '🔇';
        }
    }
    
    showHelp() {
        const modal = document.getElementById('help-modal');
        if (modal) {
            modal.classList.add('show');
        }
    }
    
    // Animation methods
    initializeAnimations() {
        // Intersection observer for scroll animations
        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('animate-in');
                }
            });
        }, { threshold: 0.1 });
        
        document.querySelectorAll('[data-animate]').forEach(element => {
            observer.observe(element);
        });
    }
    
    animateStepEntry(step) {
        const elements = step.querySelectorAll('.animate-in');
        elements.forEach((el, index) => {
            setTimeout(() => {
                el.style.opacity = '1';
                el.style.transform = 'translateY(0)';
            }, index * 100);
        });
    }
    
    // Canvas initialization
    initializeCanvases() {
        this.initializeVineyardCanvas();
    }
    
    initializeVineyardCanvas() {
        const canvas = document.getElementById('vineyard-canvas');
        if (!canvas) return;
        
        // Ensure it's a canvas element
        if (canvas.tagName !== 'CANVAS') {
            console.warn('vineyard-canvas is not a canvas element');
            return;
        }
        
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        
        // Set canvas size
        canvas.width = canvas.offsetWidth;
        canvas.height = canvas.offsetHeight;
        
        // Draw vineyard background
        const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
        gradient.addColorStop(0, '#87CEEB');
        gradient.addColorStop(0.5, '#98FB98');
        gradient.addColorStop(1, '#8B7355');
        
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    
    // Keyboard shortcuts
    setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            try {
                // Arrow keys for navigation
                if (e.key === 'ArrowRight' && !e.ctrlKey && !e.altKey) {
                    this.nextStep();
                } else if (e.key === 'ArrowLeft' && !e.ctrlKey && !e.altKey) {
                    this.previousStep();
                }
                
                // Number keys for direct step navigation
                if (e.key >= '1' && e.key <= '5' && !e.ctrlKey && !e.altKey) {
                    const step = parseInt(e.key);
                    if (step <= this.getUnlockedSteps()) {
                        this.goToStep(step);
                    }
                }
                
                // Ctrl+S to save
                if (e.ctrlKey && e.key === 's') {
                    e.preventDefault();
                    this.saveProgress();
                }
                
                // H for help
                if (e.key === 'h' && !e.ctrlKey && !e.altKey) {
                    this.showHelp();
                }
            } catch (error) {
                console.error('Keyboard shortcut error:', error);
            }
        });
    }
    
    // Drag and drop initialization
    initializeDragDrop() {
        const draggables = document.querySelectorAll('[draggable="true"]');
        const dropZones = document.querySelectorAll('.drop-zone');
        
        draggables.forEach(draggable => {
            draggable.addEventListener('dragstart', (e) => {
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/html', e.target.innerHTML);
                e.target.classList.add('dragging');
            });
            
            draggable.addEventListener('dragend', (e) => {
                e.target.classList.remove('dragging');
            });
        });
        
        dropZones.forEach(zone => {
            zone.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                zone.classList.add('drag-over');
            });
            
            zone.addEventListener('dragleave', () => {
                zone.classList.remove('drag-over');
            });
            
            zone.addEventListener('drop', (e) => {
                e.preventDefault();
                zone.classList.remove('drag-over');
                
                const data = e.dataTransfer.getData('text/html');
                // Handle drop logic here
            });
        });
    }
    
    // Map controls
    zoomMap(factor) {
        const mapCanvas = document.getElementById('map-canvas');
        if (!mapCanvas) return;
        
        const currentTransform = mapCanvas.style.transform || 'scale(1)';
        const currentScale = parseFloat(currentTransform.match(/scale\(([\d.]+)\)/)?.[1] || 1);
        const newScale = Math.max(0.5, Math.min(2, currentScale * factor));
        
        mapCanvas.style.transform = `scale(${newScale})`;
    }
    
    resetMap() {
        const mapCanvas = document.getElementById('map-canvas');
        if (mapCanvas) {
            mapCanvas.style.transform = 'scale(1)';
        }
    }
    
    confirmPlotSelection(plotId) {
        this.journeyData.plot = plotId;
        this.showNotification(`Plot ${plotId} selected!`, 'success');
        this.awardPoints(100);
    }
    
    showStageDetails(stage) {
        const event = document.querySelector(`.timeline-event[data-stage="${stage}"]`);
        if (event) {
            event.classList.add('expanded');
            // Show additional details
        }
    }
}

// Initialize game when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.gameInstance = new VinsDeluxJourneyGame();
    });
} else {
    window.gameInstance = new VinsDeluxJourneyGame();
}

// Add CSS animations
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(100%);
            opacity: 0;
        }
    }
    
    @keyframes floatUp {
        from {
            transform: translate(-50%, -50%);
            opacity: 1;
        }
        to {
            transform: translate(-50%, -150%);
            opacity: 0;
        }
    }
    
    @keyframes fall {
        to {
            transform: translateY(100vh) rotate(360deg);
            opacity: 0;
        }
    }
`;
document.head.appendChild(style);