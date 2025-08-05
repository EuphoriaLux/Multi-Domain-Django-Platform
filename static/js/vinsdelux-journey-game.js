/**
 * VinsDelux Interactive Journey Game
 * A comprehensive game system for wine journey experience
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
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.initializeAnimations();
        this.loadSavedProgress();
        this.startGameTimer();
        this.initializeCanvases();
        this.setupKeyboardShortcuts();
        this.initializeDragDrop();
        this.hideLoading();
    }
    
    // Core Navigation
    setupEventListeners() {
        // Navigation buttons
        document.querySelector('.btn-next')?.addEventListener('click', () => this.nextStep());
        document.querySelector('.btn-prev')?.addEventListener('click', () => this.previousStep());
        
        // Step dots
        document.querySelectorAll('.step-dots .dot').forEach(dot => {
            dot.addEventListener('click', (e) => {
                const step = parseInt(e.target.dataset.step);
                if (step <= this.getUnlockedSteps()) {
                    this.goToStep(step);
                }
            });
        });
        
        // Save progress
        document.getElementById('save-progress')?.addEventListener('click', () => this.saveProgress());
        
        // Sound toggle
        document.getElementById('toggle-sound')?.addEventListener('click', () => this.toggleSound());
        
        // Help modal
        document.getElementById('show-help')?.addEventListener('click', () => this.showHelp());
        
        // Step-specific listeners
        this.setupPlotSelection();
        this.setupWineBuilder();
        this.setupProductionTimeline();
        this.setupDeliveryOptions();
        this.setupLegacyBuilder();
    }
    
    // Step Navigation
    nextStep() {
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
    }
    
    previousStep() {
        if (this.currentStep > 1) {
            this.currentStep--;
            this.goToStep(this.currentStep);
        }
    }
    
    goToStep(step) {
        // Hide all steps
        document.querySelectorAll('.journey-step').forEach(section => {
            section.classList.remove('active');
            section.classList.add('hidden');
        });
        
        // Show target step
        const targetStep = document.querySelector(`.journey-step[data-step="${step}"]`);
        if (targetStep) {
            targetStep.classList.remove('hidden');
            setTimeout(() => {
                targetStep.classList.add('active');
                this.animateStepEntry(targetStep);
            }, 50);
        }
        
        // Update navigation
        this.updateNavigation(step);
        this.updateProgress(step);
        this.currentStep = step;
        
        // Play sound effect
        if (this.soundEnabled) {
            this.playSound('step-change');
        }
    }
    
    updateNavigation(step) {
        // Update buttons
        const prevBtn = document.querySelector('.btn-prev');
        const nextBtn = document.querySelector('.btn-next');
        
        prevBtn.disabled = step === 1;
        nextBtn.disabled = step === this.totalSteps;
        
        if (step === this.totalSteps) {
            nextBtn.innerHTML = '<span>Complete</span> <i class="fas fa-check"></i>';
        } else {
            nextBtn.innerHTML = '<span>Next</span> <i class="fas fa-chevron-right"></i>';
        }
        
        // Update dots
        document.querySelectorAll('.step-dots .dot').forEach((dot, index) => {
            dot.classList.toggle('active', index + 1 === step);
            dot.classList.toggle('completed', index + 1 < step);
        });
        
        // Update milestones
        document.querySelectorAll('.milestone').forEach((milestone, index) => {
            if (index + 1 <= step) {
                milestone.dataset.unlocked = 'true';
            }
        });
    }
    
    updateProgress(step) {
        const progress = (step / this.totalSteps) * 100;
        const progressFill = document.querySelector('.progress-fill');
        if (progressFill) {
            progressFill.style.width = `${progress}%`;
            progressFill.dataset.progress = progress;
        }
        
        // Update completion rate
        document.getElementById('completion-rate').textContent = Math.round(progress);
    }
    
    // Step 1: Plot Selection
    setupPlotSelection() {
        this.initializeVineyardMap();
        
        // Plot cards
        document.querySelectorAll('.plot-card').forEach(card => {
            card.addEventListener('click', (e) => {
                const plotId = card.dataset.plotId;
                this.selectPlot(plotId);
            });
        });
        
        // Map controls
        document.querySelector('.map-zoom-in')?.addEventListener('click', () => this.zoomMap(1.2));
        document.querySelector('.map-zoom-out')?.addEventListener('click', () => this.zoomMap(0.8));
        document.querySelector('.map-reset')?.addEventListener('click', () => this.resetMap());
        
        // Plot selection
        document.querySelector('.btn-select-plot')?.addEventListener('click', (e) => {
            const plotId = e.target.dataset.plotId;
            if (plotId) {
                this.confirmPlotSelection(plotId);
            }
        });
    }
    
    initializeVineyardMap() {
        const mapCanvas = document.getElementById('map-canvas');
        if (!mapCanvas) return;
        
        // Create interactive plot markers
        const plots = [
            { id: 1, name: 'Château Heights', x: 30, y: 25, terroir: 'clay' },
            { id: 2, name: 'Valley Reserve', x: 60, y: 45, terroir: 'sandy' },
            { id: 3, name: 'Riverside Terroir', x: 45, y: 70, terroir: 'granite' },
            { id: 4, name: 'Hillside Premium', x: 75, y: 30, terroir: 'clay' },
            { id: 5, name: 'Sunset Vineyard', x: 20, y: 60, terroir: 'sandy' }
        ];
        
        plots.forEach(plot => {
            const marker = document.createElement('div');
            marker.className = 'plot-marker';
            marker.dataset.plotId = plot.id;
            marker.style.left = `${plot.x}%`;
            marker.style.top = `${plot.y}%`;
            marker.innerHTML = `
                <div class="marker-pin">
                    <i class="fas fa-map-pin"></i>
                </div>
                <div class="marker-label">${plot.name}</div>
            `;
            
            marker.addEventListener('click', () => this.showPlotDetails(plot));
            marker.addEventListener('mouseenter', () => this.highlightPlot(plot));
            marker.addEventListener('mouseleave', () => this.unhighlightPlot(plot));
            
            mapCanvas.appendChild(marker);
        });
    }
    
    showPlotDetails(plot) {
        const panel = document.getElementById('plot-details');
        if (!panel) return;
        
        // Update panel content
        document.getElementById('plot-name').textContent = plot.name;
        document.getElementById('plot-size').textContent = `${Math.floor(Math.random() * 5 + 2)} hectares`;
        document.getElementById('plot-elevation').textContent = `${Math.floor(Math.random() * 300 + 100)}m`;
        document.getElementById('plot-exposure').textContent = 'South-facing';
        document.getElementById('plot-soil').textContent = plot.terroir;
        document.getElementById('plot-description').textContent = 
            `This exceptional plot offers unique characteristics perfect for producing premium wines...`;
        
        // Update image
        const plotImage = document.getElementById('plot-image');
        if (plotImage) {
            plotImage.src = `/static/images/plots/plot${plot.id}.jpg`;
            plotImage.alt = plot.name;
        }
        
        // Update selection button
        document.querySelector('.btn-select-plot').dataset.plotId = plot.id;
        
        // Show panel with animation
        panel.classList.add('active');
        this.animatePanel(panel);
    }
    
    selectPlot(plotId) {
        // Remove previous selections
        document.querySelectorAll('.plot-card').forEach(card => {
            card.classList.remove('selected');
        });
        document.querySelectorAll('.plot-marker').forEach(marker => {
            marker.classList.remove('selected');
        });
        
        // Add selection
        document.querySelector(`.plot-card[data-plot-id="${plotId}"]`)?.classList.add('selected');
        document.querySelector(`.plot-marker[data-plot-id="${plotId}"]`)?.classList.add('selected');
        
        // Store selection
        this.journeyData.plot = plotId;
        
        // Award points
        this.awardPoints(50);
        
        // Show details
        const plot = { id: plotId, name: `Plot ${plotId}` };
        this.showPlotDetails(plot);
    }
    
    // Step 2: Wine Builder
    setupWineBuilder() {
        this.initializeGrapeSelector();
        this.initializeBlendChart();
        this.initializeWineProfileChart();
        this.setupTechniques();
        this.setupSommelierChat();
    }
    
    initializeGrapeSelector() {
        const grapeItems = document.querySelectorAll('.grape-item');
        const blendContainer = document.getElementById('blend-container');
        
        grapeItems.forEach(item => {
            item.draggable = true;
            
            item.addEventListener('dragstart', (e) => {
                e.dataTransfer.effectAllowed = 'copy';
                e.dataTransfer.setData('grape', item.dataset.grape);
                item.classList.add('dragging');
            });
            
            item.addEventListener('dragend', () => {
                item.classList.remove('dragging');
            });
            
            // Click to add
            item.addEventListener('click', () => {
                this.addGrapeToBlend(item.dataset.grape);
            });
        });
        
        // Blend container drop zone
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
                const grape = e.dataTransfer.getData('grape');
                this.addGrapeToBlend(grape);
                blendContainer.classList.remove('drag-over');
            });
        }
    }
    
    addGrapeToBlend(grapeType) {
        if (!this.journeyData.blend[grapeType]) {
            this.journeyData.blend[grapeType] = 25;
            this.updateBlendDisplay();
            this.updateBlendChart();
            this.updateWineProfile();
            this.generateTastingNotes();
            this.awardPoints(25);
        }
    }
    
    updateBlendDisplay() {
        const container = document.getElementById('blend-percentages');
        if (!container) return;
        
        container.innerHTML = '';
        
        Object.keys(this.journeyData.blend).forEach(grape => {
            const control = document.createElement('div');
            control.className = 'blend-control';
            control.innerHTML = `
                <label>${this.formatGrapeName(grape)}</label>
                <input type="range" min="0" max="100" value="${this.journeyData.blend[grape]}" 
                       data-grape="${grape}" class="blend-slider">
                <span class="percentage">${this.journeyData.blend[grape]}%</span>
                <button class="remove-grape" data-grape="${grape}">
                    <i class="fas fa-times"></i>
                </button>
            `;
            
            container.appendChild(control);
        });
        
        // Add event listeners
        container.querySelectorAll('.blend-slider').forEach(slider => {
            slider.addEventListener('input', (e) => {
                const grape = e.target.dataset.grape;
                this.journeyData.blend[grape] = parseInt(e.target.value);
                e.target.nextElementSibling.textContent = `${e.target.value}%`;
                this.updateBlendChart();
                this.updateWineProfile();
            });
        });
        
        container.querySelectorAll('.remove-grape').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const grape = e.target.closest('button').dataset.grape;
                delete this.journeyData.blend[grape];
                this.updateBlendDisplay();
                this.updateBlendChart();
                this.updateWineProfile();
            });
        });
    }
    
    initializeBlendChart() {
        const canvas = document.getElementById('blend-chart');
        if (!canvas) return;
        
        const ctx = canvas.getContext('2d');
        this.blendChart = {
            canvas: canvas,
            ctx: ctx,
            width: canvas.width,
            height: canvas.height
        };
        
        this.updateBlendChart();
    }
    
    updateBlendChart() {
        if (!this.blendChart) return;
        
        const { ctx, width, height } = this.blendChart;
        
        // Clear canvas
        ctx.clearRect(0, 0, width, height);
        
        // Draw pie chart
        const centerX = width / 2;
        const centerY = height / 2;
        const radius = Math.min(width, height) / 2 - 20;
        
        let startAngle = -Math.PI / 2;
        const colors = ['#8B0000', '#DC143C', '#B22222', '#CD5C5C'];
        let colorIndex = 0;
        
        Object.entries(this.journeyData.blend).forEach(([grape, percentage]) => {
            const angle = (percentage / 100) * Math.PI * 2;
            
            // Draw slice
            ctx.beginPath();
            ctx.moveTo(centerX, centerY);
            ctx.arc(centerX, centerY, radius, startAngle, startAngle + angle);
            ctx.closePath();
            ctx.fillStyle = colors[colorIndex % colors.length];
            ctx.fill();
            
            // Draw label
            const labelAngle = startAngle + angle / 2;
            const labelX = centerX + Math.cos(labelAngle) * (radius * 0.7);
            const labelY = centerY + Math.sin(labelAngle) * (radius * 0.7);
            
            ctx.fillStyle = '#fff';
            ctx.font = '12px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(`${percentage}%`, labelX, labelY);
            
            startAngle += angle;
            colorIndex++;
        });
    }
    
    initializeWineProfileChart() {
        const canvas = document.getElementById('wine-profile-chart');
        if (!canvas) return;
        
        const ctx = canvas.getContext('2d');
        this.profileChart = {
            canvas: canvas,
            ctx: ctx,
            width: canvas.width,
            height: canvas.height
        };
        
        this.updateWineProfile();
    }
    
    updateWineProfile() {
        if (!this.profileChart) return;
        
        const { ctx, width, height } = this.profileChart;
        
        // Clear canvas
        ctx.clearRect(0, 0, width, height);
        
        // Calculate profile based on blend
        const profile = this.calculateWineProfile();
        
        // Draw radar chart
        const centerX = width / 2;
        const centerY = height / 2;
        const radius = Math.min(width, height) / 2 - 30;
        const attributes = ['Body', 'Tannins', 'Acidity', 'Alcohol', 'Complexity'];
        const angleStep = (Math.PI * 2) / attributes.length;
        
        // Draw grid
        ctx.strokeStyle = '#ddd';
        ctx.lineWidth = 1;
        
        for (let i = 1; i <= 5; i++) {
            ctx.beginPath();
            for (let j = 0; j < attributes.length; j++) {
                const angle = j * angleStep - Math.PI / 2;
                const x = centerX + Math.cos(angle) * (radius * i / 5);
                const y = centerY + Math.sin(angle) * (radius * i / 5);
                
                if (j === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
            }
            ctx.closePath();
            ctx.stroke();
        }
        
        // Draw axes
        attributes.forEach((attr, i) => {
            const angle = i * angleStep - Math.PI / 2;
            ctx.beginPath();
            ctx.moveTo(centerX, centerY);
            ctx.lineTo(
                centerX + Math.cos(angle) * radius,
                centerY + Math.sin(angle) * radius
            );
            ctx.stroke();
            
            // Labels
            const labelX = centerX + Math.cos(angle) * (radius + 20);
            const labelY = centerY + Math.sin(angle) * (radius + 20);
            ctx.fillStyle = '#333';
            ctx.font = '12px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(attr, labelX, labelY);
        });
        
        // Draw profile
        ctx.beginPath();
        ctx.fillStyle = 'rgba(139, 0, 0, 0.3)';
        ctx.strokeStyle = '#8B0000';
        ctx.lineWidth = 2;
        
        attributes.forEach((attr, i) => {
            const angle = i * angleStep - Math.PI / 2;
            const value = profile[attr.toLowerCase()] || 3;
            const x = centerX + Math.cos(angle) * (radius * value / 5);
            const y = centerY + Math.sin(angle) * (radius * value / 5);
            
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
    }
    
    calculateWineProfile() {
        // Simplified profile calculation based on grape blend
        const profile = {
            body: 3,
            tannins: 3,
            acidity: 3,
            alcohol: 3,
            complexity: 3
        };
        
        Object.entries(this.journeyData.blend).forEach(([grape, percentage]) => {
            const factor = percentage / 100;
            
            switch(grape) {
                case 'cabernet-sauvignon':
                    profile.body += factor * 2;
                    profile.tannins += factor * 2;
                    profile.complexity += factor * 1;
                    break;
                case 'merlot':
                    profile.body += factor * 1.5;
                    profile.tannins += factor * 1;
                    profile.alcohol += factor * 0.5;
                    break;
                case 'pinot-noir':
                    profile.acidity += factor * 1.5;
                    profile.complexity += factor * 2;
                    break;
                case 'chardonnay':
                    profile.body += factor * 0.5;
                    profile.acidity += factor * 1;
                    profile.complexity += factor * 1;
                    break;
            }
        });
        
        // Normalize to 1-5 scale
        Object.keys(profile).forEach(key => {
            profile[key] = Math.min(5, Math.max(1, profile[key]));
        });
        
        return profile;
    }
    
    generateTastingNotes() {
        const notes = [];
        
        Object.entries(this.journeyData.blend).forEach(([grape, percentage]) => {
            if (percentage > 30) {
                switch(grape) {
                    case 'cabernet-sauvignon':
                        notes.push('Blackcurrant', 'Cedar', 'Tobacco');
                        break;
                    case 'merlot':
                        notes.push('Plum', 'Chocolate', 'Vanilla');
                        break;
                    case 'pinot-noir':
                        notes.push('Cherry', 'Mushroom', 'Forest floor');
                        break;
                    case 'chardonnay':
                        notes.push('Apple', 'Butter', 'Oak');
                        break;
                }
            }
        });
        
        const container = document.getElementById('tasting-notes');
        if (container) {
            container.innerHTML = notes.map(note => 
                `<span class="note-tag">${note}</span>`
            ).join('');
        }
    }
    
    setupTechniques() {
        // Fermentation options
        document.querySelectorAll('input[name="fermentation"]').forEach(input => {
            input.addEventListener('change', (e) => {
                this.journeyData.techniques.fermentation = e.target.value;
                this.awardPoints(20);
            });
        });
        
        // Aging slider
        const agingSlider = document.getElementById('aging-months');
        if (agingSlider) {
            agingSlider.addEventListener('input', (e) => {
                document.getElementById('aging-value').textContent = e.target.value;
                this.journeyData.techniques.aging = parseInt(e.target.value);
            });
        }
    }
    
    setupSommelierChat() {
        const suggestions = document.querySelectorAll('.suggestion');
        const chatMessages = document.getElementById('sommelier-chat');
        
        suggestions.forEach(btn => {
            btn.addEventListener('click', () => {
                const style = btn.textContent;
                this.addChatMessage('user', `I'd like a ${style} wine.`);
                this.sommelierResponse(style);
            });
        });
    }
    
    addChatMessage(sender, message) {
        const chatContainer = document.getElementById('sommelier-chat');
        if (!chatContainer) return;
        
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}`;
        messageDiv.innerHTML = `<p>${message}</p>`;
        
        chatContainer.appendChild(messageDiv);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }
    
    sommelierResponse(style) {
        let response = '';
        
        if (style.includes('Bold')) {
            response = 'Excellent choice! I recommend a Cabernet Sauvignon-dominant blend with extended oak aging.';
        } else if (style.includes('Light')) {
            response = 'For elegance, consider a Pinot Noir base with minimal oak influence.';
        } else {
            response = 'A balanced blend of Merlot and Cabernet creates a timeless classic.';
        }
        
        setTimeout(() => {
            this.addChatMessage('sommelier', response);
        }, 1000);
    }
    
    // Step 3: Production Timeline
    setupProductionTimeline() {
        const playBtn = document.querySelector('.btn-play-timeline');
        if (playBtn) {
            playBtn.addEventListener('click', () => this.playTimeline());
        }
        
        // Timeline events
        document.querySelectorAll('.timeline-event').forEach(event => {
            event.addEventListener('click', () => {
                this.showEventDetails(event.dataset.stage);
            });
        });
        
        // Start weather updates
        this.startWeatherUpdates();
    }
    
    playTimeline() {
        const events = document.querySelectorAll('.timeline-event');
        let index = 0;
        
        const animateNext = () => {
            if (index < events.length) {
                events[index].classList.add('active');
                this.updateTimelineProgress((index + 1) / events.length * 100);
                
                // Award points for each stage
                this.awardPoints(15);
                
                index++;
                setTimeout(animateNext, 1500);
            }
        };
        
        animateNext();
    }
    
    updateTimelineProgress(percentage) {
        const progress = document.querySelector('.timeline-progress');
        if (progress) {
            progress.style.width = `${percentage}%`;
        }
    }
    
    startWeatherUpdates() {
        // Simulate weather changes
        setInterval(() => {
            const temp = Math.floor(Math.random() * 15 + 15);
            const humidity = Math.floor(Math.random() * 30 + 50);
            const wind = Math.floor(Math.random() * 20 + 5);
            
            document.querySelector('.temperature').textContent = `${temp}°C`;
            document.querySelector('.weather-details').innerHTML = `
                <div class="detail">
                    <i class="fas fa-tint"></i>
                    <span>Humidity: ${humidity}%</span>
                </div>
                <div class="detail">
                    <i class="fas fa-wind"></i>
                    <span>Wind: ${wind} km/h</span>
                </div>
            `;
        }, 10000);
    }
    
    // Step 4: Delivery Options
    setupDeliveryOptions() {
        // Delivery form
        document.querySelectorAll('input[name="delivery-type"]').forEach(input => {
            input.addEventListener('change', (e) => {
                this.journeyData.delivery.type = e.target.value;
                this.updatePackagePreview(e.target.value);
                this.awardPoints(30);
            });
        });
        
        // 3D Package rotation
        this.initializePackage3D();
        
        // Tasting room
        this.initializeTastingRoom();
    }
    
    initializePackage3D() {
        const packageBox = document.querySelector('.package-box');
        if (!packageBox) return;
        
        let rotateX = -20;
        let rotateY = 45;
        
        packageBox.style.transform = `rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
        
        let isDragging = false;
        let startX, startY;
        
        packageBox.addEventListener('mousedown', (e) => {
            isDragging = true;
            startX = e.clientX;
            startY = e.clientY;
        });
        
        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            
            const deltaX = e.clientX - startX;
            const deltaY = e.clientY - startY;
            
            rotateY += deltaX * 0.5;
            rotateX -= deltaY * 0.5;
            
            packageBox.style.transform = `rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
            
            startX = e.clientX;
            startY = e.clientY;
        });
        
        document.addEventListener('mouseup', () => {
            isDragging = false;
        });
    }
    
    initializeTastingRoom() {
        const canvas = document.getElementById('tasting-room-3d');
        if (!canvas) return;
        
        const ctx = canvas.getContext('2d');
        
        // Simple 3D room visualization
        this.drawTastingRoom(ctx, canvas.width, canvas.height);
        
        // Room controls
        document.querySelectorAll('.room-controls .control-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const action = e.currentTarget.dataset.action;
                this.controlTastingRoom(action);
            });
        });
    }
    
    drawTastingRoom(ctx, width, height) {
        // Clear canvas
        ctx.clearRect(0, 0, width, height);
        
        // Draw room background
        const gradient = ctx.createLinearGradient(0, 0, 0, height);
        gradient.addColorStop(0, '#2c1810');
        gradient.addColorStop(1, '#1a0e08');
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, width, height);
        
        // Draw wine rack
        ctx.strokeStyle = '#8B4513';
        ctx.lineWidth = 2;
        
        for (let i = 0; i < 5; i++) {
            for (let j = 0; j < 3; j++) {
                ctx.beginPath();
                ctx.rect(50 + j * 60, 50 + i * 40, 50, 30);
                ctx.stroke();
            }
        }
        
        // Draw tasting table
        ctx.fillStyle = '#654321';
        ctx.fillRect(width / 2 - 100, height - 150, 200, 100);
        
        // Draw wine glasses
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(width / 2 - 30, height - 160);
        ctx.lineTo(width / 2 - 25, height - 180);
        ctx.lineTo(width / 2 - 35, height - 180);
        ctx.closePath();
        ctx.stroke();
    }
    
    // Step 5: Legacy Builder
    setupLegacyBuilder() {
        // Legacy options
        document.querySelectorAll('.btn-select-legacy').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const legacyType = e.target.dataset.legacy;
                this.selectLegacy(legacyType);
            });
        });
        
        // Initialize legacy timeline
        this.initializeLegacyTimeline();
        
        // Complete journey button
        document.querySelector('.btn-complete-journey')?.addEventListener('click', () => {
            this.completeJourney();
        });
        
        // Share button
        document.querySelector('.btn-share-journey')?.addEventListener('click', () => {
            this.shareJourney();
        });
    }
    
    selectLegacy(type) {
        // Remove previous selections
        document.querySelectorAll('.legacy-card').forEach(card => {
            card.classList.remove('selected');
        });
        
        // Add selection
        document.querySelector(`.legacy-card .btn-select-legacy[data-legacy="${type}"]`)
            ?.closest('.legacy-card').classList.add('selected');
        
        this.journeyData.legacy = type;
        this.awardPoints(150);
        
        // Update timeline visualization
        this.updateLegacyTimeline(type);
    }
    
    initializeLegacyTimeline() {
        const canvas = document.getElementById('legacy-timeline-chart');
        if (!canvas) return;
        
        const ctx = canvas.getContext('2d');
        const width = canvas.width;
        const height = canvas.height;
        
        // Draw timeline
        ctx.strokeStyle = '#8B0000';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(50, height / 2);
        ctx.lineTo(width - 50, height / 2);
        ctx.stroke();
        
        // Draw milestones
        const milestones = [2024, 2029, 2034, 2039, 2044];
        const spacing = (width - 100) / (milestones.length - 1);
        
        milestones.forEach((year, index) => {
            const x = 50 + index * spacing;
            
            // Draw milestone marker
            ctx.beginPath();
            ctx.arc(x, height / 2, 8, 0, Math.PI * 2);
            ctx.fillStyle = index === 0 ? '#8B0000' : '#ddd';
            ctx.fill();
            
            // Draw year label
            ctx.fillStyle = '#333';
            ctx.font = '12px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(year, x, height / 2 + 25);
        });
    }
    
    updateLegacyTimeline(type) {
        // Update timeline based on legacy type
        const canvas = document.getElementById('legacy-timeline-chart');
        if (!canvas) return;
        
        // Re-draw with specific legacy milestones
        this.initializeLegacyTimeline();
        
        // Add legacy-specific annotations
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#8B0000';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        
        let title = '';
        switch(type) {
            case 'family':
                title = 'Family Heritage Timeline';
                break;
            case 'investment':
                title = 'Investment Growth Projection';
                break;
            case 'collector':
                title = 'Collection Milestones';
                break;
        }
        
        ctx.fillText(title, canvas.width / 2, 30);
    }
    
    // Completion and Summary
    completeJourney() {
        if (!this.validateAllSteps()) {
            this.showNotification('Please complete all steps before finishing', 'warning');
            return;
        }
        
        // Calculate final score
        const finalScore = this.userScore;
        const timeSpent = this.getTimeSpent();
        
        // Update summary
        document.getElementById('selected-plot-name').textContent = `Plot ${this.journeyData.plot}`;
        document.getElementById('wine-blend-summary').textContent = this.getBlendSummary();
        document.getElementById('total-xp').textContent = finalScore;
        
        // Show completion animation
        this.showCompletionAnimation();
        
        // Award final achievement
        this.unlockAchievement('Journey Master', 'Completed the entire VinsDelux journey', 500);
        
        // Save completion
        this.saveCompletion();
    }
    
    getBlendSummary() {
        const blends = Object.entries(this.journeyData.blend)
            .map(([grape, percentage]) => `${this.formatGrapeName(grape)} ${percentage}%`)
            .join(', ');
        return blends || 'Custom Blend';
    }
    
    shareJourney() {
        const shareData = {
            title: 'My VinsDelux Wine Journey',
            text: `I just created my signature wine at VinsDelux! ${this.getBlendSummary()}`,
            url: window.location.href
        };
        
        if (navigator.share) {
            navigator.share(shareData);
        } else {
            // Fallback to copying to clipboard
            const text = `${shareData.title}\n${shareData.text}\n${shareData.url}`;
            navigator.clipboard.writeText(text);
            this.showNotification('Journey details copied to clipboard!', 'success');
        }
    }
    
    // Validation
    validateCurrentStep() {
        switch(this.currentStep) {
            case 1:
                return this.journeyData.plot !== null;
            case 2:
                return Object.keys(this.journeyData.blend).length > 0;
            case 3:
                return true; // Timeline viewing is optional
            case 4:
                return this.journeyData.delivery.type !== undefined;
            case 5:
                return this.journeyData.legacy !== null;
            default:
                return true;
        }
    }
    
    validateAllSteps() {
        return this.journeyData.plot !== null &&
               Object.keys(this.journeyData.blend).length > 0 &&
               this.journeyData.delivery.type !== undefined &&
               this.journeyData.legacy !== null;
    }
    
    // Gamification
    awardPoints(points) {
        this.userScore += points;
        document.getElementById('user-points').textContent = this.userScore;
        
        // Show points animation
        this.showPointsAnimation(points);
    }
    
    showPointsAnimation(points) {
        const popup = document.createElement('div');
        popup.className = 'points-popup';
        popup.textContent = `+${points} XP`;
        
        document.body.appendChild(popup);
        
        // Position near score display
        const scoreElement = document.getElementById('user-points');
        const rect = scoreElement.getBoundingClientRect();
        popup.style.left = `${rect.left}px`;
        popup.style.top = `${rect.top - 20}px`;
        
        // Animate and remove
        setTimeout(() => {
            popup.classList.add('animate');
        }, 10);
        
        setTimeout(() => {
            popup.remove();
        }, 2000);
    }
    
    checkAchievements() {
        // Check for various achievements
        if (this.userScore >= 100 && !this.hasAchievement('First Steps')) {
            this.unlockAchievement('First Steps', 'Earned 100 points', 50);
        }
        
        if (this.userScore >= 500 && !this.hasAchievement('Wine Enthusiast')) {
            this.unlockAchievement('Wine Enthusiast', 'Earned 500 points', 100);
        }
        
        if (Object.keys(this.journeyData.blend).length >= 3 && !this.hasAchievement('Master Blender')) {
            this.unlockAchievement('Master Blender', 'Created a complex blend', 75);
        }
    }
    
    hasAchievement(name) {
        return this.achievements.includes(name);
    }
    
    unlockAchievement(title, description, points) {
        if (this.hasAchievement(title)) return;
        
        this.achievements.push(title);
        this.awardPoints(points);
        
        // Show achievement popup
        const popup = document.getElementById('achievement-popup');
        if (popup) {
            popup.querySelector('.achievement-title').textContent = title;
            popup.querySelector('.achievement-description').textContent = description;
            popup.querySelector('.achievement-points span').textContent = points;
            
            popup.classList.add('show');
            
            if (this.soundEnabled) {
                this.playSound('achievement');
            }
            
            setTimeout(() => {
                popup.classList.remove('show');
            }, 4000);
        }
    }
    
    // Progress Management
    saveProgress() {
        const progressData = {
            currentStep: this.currentStep,
            score: this.userScore,
            achievements: this.achievements,
            journeyData: this.journeyData,
            timestamp: Date.now()
        };
        
        localStorage.setItem('vinsdelux_journey_progress', JSON.stringify(progressData));
        this.showNotification('Progress saved successfully!', 'success');
    }
    
    loadSavedProgress() {
        const saved = localStorage.getItem('vinsdelux_journey_progress');
        if (saved) {
            try {
                const data = JSON.parse(saved);
                this.currentStep = data.currentStep || 1;
                this.userScore = data.score || 0;
                this.achievements = data.achievements || [];
                this.journeyData = data.journeyData || {
                    plot: null,
                    blend: {},
                    techniques: {},
                    delivery: {},
                    legacy: null
                };
                
                // Update UI
                this.goToStep(this.currentStep);
                document.getElementById('user-points').textContent = this.userScore;
                
                this.showNotification('Previous progress restored', 'info');
            } catch (e) {
                console.error('Failed to load saved progress:', e);
            }
        }
    }
    
    saveCompletion() {
        const completionData = {
            completedAt: Date.now(),
            finalScore: this.userScore,
            timeSpent: this.getTimeSpent(),
            journeyData: this.journeyData
        };
        
        // Save to completions array
        let completions = JSON.parse(localStorage.getItem('vinsdelux_completions') || '[]');
        completions.push(completionData);
        localStorage.setItem('vinsdelux_completions', JSON.stringify(completions));
    }
    
    // Timer
    startGameTimer() {
        setInterval(() => {
            const elapsed = Date.now() - this.startTime;
            const minutes = Math.floor(elapsed / 60000);
            const seconds = Math.floor((elapsed % 60000) / 1000);
            
            document.getElementById('time-spent').textContent = 
                `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }, 1000);
    }
    
    getTimeSpent() {
        return Math.floor((Date.now() - this.startTime) / 1000);
    }
    
    // Animations
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
        elements.forEach((element, index) => {
            setTimeout(() => {
                element.classList.add('active');
            }, index * 100);
        });
    }
    
    animatePanel(panel) {
        panel.style.animation = 'slideInRight 0.3s ease-out';
    }
    
    showCompletionAnimation() {
        // Create confetti effect
        for (let i = 0; i < 50; i++) {
            const confetti = document.createElement('div');
            confetti.className = 'confetti';
            confetti.style.left = `${Math.random() * 100}%`;
            confetti.style.animationDelay = `${Math.random() * 3}s`;
            confetti.style.backgroundColor = ['#8B0000', '#DC143C', '#FFD700'][Math.floor(Math.random() * 3)];
            document.body.appendChild(confetti);
            
            setTimeout(() => confetti.remove(), 5000);
        }
    }
    
    // Canvas Initialization
    initializeCanvases() {
        // Vineyard background canvas
        const vineyardCanvas = document.getElementById('vineyard-canvas');
        if (vineyardCanvas) {
            this.drawVineyardBackground(vineyardCanvas);
        }
    }
    
    drawVineyardBackground(canvas) {
        const ctx = canvas.getContext('2d');
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        
        // Animated particle effect
        const particles = [];
        for (let i = 0; i < 50; i++) {
            particles.push({
                x: Math.random() * canvas.width,
                y: Math.random() * canvas.height,
                size: Math.random() * 3 + 1,
                speedX: Math.random() * 0.5 - 0.25,
                speedY: Math.random() * 0.5 - 0.25,
                opacity: Math.random() * 0.5 + 0.5
            });
        }
        
        const animate = () => {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            
            particles.forEach(particle => {
                particle.x += particle.speedX;
                particle.y += particle.speedY;
                
                if (particle.x < 0 || particle.x > canvas.width) particle.speedX *= -1;
                if (particle.y < 0 || particle.y > canvas.height) particle.speedY *= -1;
                
                ctx.beginPath();
                ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(139, 0, 0, ${particle.opacity})`;
                ctx.fill();
            });
            
            requestAnimationFrame(animate);
        };
        
        animate();
    }
    
    // Drag and Drop
    initializeDragDrop() {
        // Enable drag and drop for various elements
        const draggables = document.querySelectorAll('[draggable="true"]');
        const dropZones = document.querySelectorAll('.drop-zone');
        
        draggables.forEach(draggable => {
            draggable.addEventListener('dragstart', this.handleDragStart.bind(this));
            draggable.addEventListener('dragend', this.handleDragEnd.bind(this));
        });
        
        dropZones.forEach(zone => {
            zone.addEventListener('dragover', this.handleDragOver.bind(this));
            zone.addEventListener('drop', this.handleDrop.bind(this));
            zone.addEventListener('dragleave', this.handleDragLeave.bind(this));
        });
    }
    
    handleDragStart(e) {
        e.target.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
    }
    
    handleDragEnd(e) {
        e.target.classList.remove('dragging');
    }
    
    handleDragOver(e) {
        if (e.preventDefault) {
            e.preventDefault();
        }
        e.dataTransfer.dropEffect = 'move';
        e.target.classList.add('drag-over');
        return false;
    }
    
    handleDragLeave(e) {
        e.target.classList.remove('drag-over');
    }
    
    handleDrop(e) {
        if (e.stopPropagation) {
            e.stopPropagation();
        }
        e.target.classList.remove('drag-over');
        return false;
    }
    
    // Keyboard Shortcuts
    setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            switch(e.key) {
                case 'ArrowRight':
                    if (this.currentStep < this.totalSteps) {
                        this.nextStep();
                    }
                    break;
                case 'ArrowLeft':
                    if (this.currentStep > 1) {
                        this.previousStep();
                    }
                    break;
                case 's':
                case 'S':
                    if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        this.saveProgress();
                    }
                    break;
                case 'h':
                case 'H':
                    this.showHelp();
                    break;
                case 'm':
                case 'M':
                    this.toggleSound();
                    break;
            }
        });
    }
    
    // Utilities
    formatGrapeName(grape) {
        return grape.split('-').map(word => 
            word.charAt(0).toUpperCase() + word.slice(1)
        ).join(' ');
    }
    
    getUnlockedSteps() {
        // Return the highest step that can be accessed
        return Math.max(this.currentStep, 1);
    }
    
    zoomMap(factor) {
        const mapCanvas = document.getElementById('map-canvas');
        if (mapCanvas) {
            const currentScale = parseFloat(mapCanvas.style.transform?.match(/scale\(([\d.]+)\)/)?.[1] || 1);
            const newScale = Math.min(3, Math.max(0.5, currentScale * factor));
            mapCanvas.style.transform = `scale(${newScale})`;
        }
    }
    
    resetMap() {
        const mapCanvas = document.getElementById('map-canvas');
        if (mapCanvas) {
            mapCanvas.style.transform = 'scale(1)';
        }
    }
    
    showHelp() {
        const modal = document.getElementById('help-modal');
        if (modal) {
            // Using Bootstrap modal if available
            if (window.bootstrap && window.bootstrap.Modal) {
                const bsModal = new bootstrap.Modal(modal);
                bsModal.show();
            } else {
                modal.classList.add('show');
                modal.style.display = 'block';
            }
        }
    }
    
    toggleSound() {
        this.soundEnabled = !this.soundEnabled;
        const soundBtn = document.getElementById('toggle-sound');
        if (soundBtn) {
            soundBtn.innerHTML = this.soundEnabled ? 
                '<i class="fas fa-volume-up"></i>' : 
                '<i class="fas fa-volume-mute"></i>';
        }
        
        this.showNotification(
            this.soundEnabled ? 'Sound enabled' : 'Sound disabled',
            'info'
        );
    }
    
    playSound(type) {
        if (!this.soundEnabled) return;
        
        // In a real implementation, you would play actual sound files
        // For now, we'll use the Web Audio API to generate simple tones
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = audioContext.createOscillator();
        const gainNode = audioContext.createGain();
        
        oscillator.connect(gainNode);
        gainNode.connect(audioContext.destination);
        
        switch(type) {
            case 'step-change':
                oscillator.frequency.value = 440;
                gainNode.gain.value = 0.1;
                break;
            case 'achievement':
                oscillator.frequency.value = 880;
                gainNode.gain.value = 0.2;
                break;
            case 'complete':
                oscillator.frequency.value = 660;
                gainNode.gain.value = 0.15;
                break;
            default:
                oscillator.frequency.value = 330;
                gainNode.gain.value = 0.1;
        }
        
        oscillator.start();
        oscillator.stop(audioContext.currentTime + 0.1);
    }
    
    showNotification(message, type = 'info') {
        const notification = document.createElement('div');
        notification.className = `game-notification ${type}`;
        notification.innerHTML = `
            <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'warning' ? 'exclamation-triangle' : 'info-circle'}"></i>
            <span>${message}</span>
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.classList.add('show');
        }, 10);
        
        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }
    
    hideLoading() {
        const loadingOverlay = document.getElementById('loading-overlay');
        if (loadingOverlay) {
            setTimeout(() => {
                loadingOverlay.classList.add('fade-out');
                setTimeout(() => {
                    loadingOverlay.style.display = 'none';
                }, 500);
            }, 1000);
        }
    }
    
    // Additional helper methods for specific features
    highlightPlot(plot) {
        const marker = document.querySelector(`.plot-marker[data-plot-id="${plot.id}"]`);
        if (marker) {
            marker.classList.add('highlighted');
        }
    }
    
    unhighlightPlot(plot) {
        const marker = document.querySelector(`.plot-marker[data-plot-id="${plot.id}"]`);
        if (marker) {
            marker.classList.remove('highlighted');
        }
    }
    
    confirmPlotSelection(plotId) {
        this.journeyData.plot = plotId;
        this.showNotification(`Plot ${plotId} selected successfully!`, 'success');
        
        // Close panel
        const panel = document.getElementById('plot-details');
        if (panel) {
            panel.classList.remove('active');
        }
        
        // Award points
        this.awardPoints(100);
        
        // Check if ready to proceed
        if (this.validateCurrentStep()) {
            setTimeout(() => {
                this.nextStep();
            }, 1500);
        }
    }
    
    showEventDetails(stage) {
        // Show detailed information about production stage
        const event = document.querySelector(`.timeline-event[data-stage="${stage}"]`);
        if (event) {
            event.classList.add('expanded');
            
            // Could open a modal with more details
            this.showNotification(`Viewing ${stage} stage details`, 'info');
        }
    }
    
    updatePackagePreview(deliveryType) {
        const packageBox = document.querySelector('.package-box');
        if (!packageBox) return;
        
        // Update package appearance based on delivery type
        packageBox.className = `package-box ${deliveryType}`;
        
        // Update contents list
        const contentsList = document.querySelector('.contents-list');
        if (contentsList && deliveryType === 'ceremony') {
            // Add premium items for ceremony delivery
            const premiumItem = document.createElement('li');
            premiumItem.innerHTML = '<i class="fas fa-crown"></i> Personal sommelier service';
            contentsList.appendChild(premiumItem);
        }
    }
    
    controlTastingRoom(action) {
        // Handle tasting room controls
        switch(action) {
            case 'rotate-left':
                // Rotate view left
                break;
            case 'rotate-right':
                // Rotate view right
                break;
            case 'zoom-in':
                // Zoom in
                break;
            case 'zoom-out':
                // Zoom out
                break;
        }
        
        // Redraw tasting room
        const canvas = document.getElementById('tasting-room-3d');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            this.drawTastingRoom(ctx, canvas.width, canvas.height);
        }
    }
    
    completeStep(step) {
        // Mark step as completed
        const milestone = document.querySelector(`.milestone[data-step="${step}"]`);
        if (milestone) {
            milestone.classList.add('completed');
        }
        
        // Update progress tracking
        const completedSteps = document.querySelectorAll('.milestone.completed').length;
        const completionPercentage = (completedSteps / this.totalSteps) * 100;
        
        // Update UI
        document.getElementById('completion-rate').textContent = Math.round(completionPercentage);
    }
}

// Initialize game when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.vinsDeluxGame = new VinsDeluxJourneyGame();
});