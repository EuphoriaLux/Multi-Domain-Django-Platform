/**
 * FUTURISTIC JOURNEY SECTION JAVASCRIPT
 * Modern interactive features using cutting-edge web APIs
 * 
 * Features:
 * - 3D tilt effects with mouse/touch tracking
 * - Intersection Observer for scroll animations
 * - Performance monitoring with Performance Observer
 * - Particle system with Canvas API
 * - Keyboard navigation with full accessibility
 * - Touch gestures and pointer events
 * - ResizeObserver for responsive handling
 * - Web Animations API for smooth transitions
 */

class FuturisticJourney {
  constructor() {
    this.currentStep = 0;
    this.totalSteps = 0;
    this.isAnimating = false;
    this.touchStartX = 0;
    this.touchStartY = 0;
    this.performanceObserver = null;
    this.intersectionObserver = null;
    this.resizeObserver = null;
    this.particleSystem = null;
    this.audioContext = null;
    this.keyboardShortcuts = new Map();
    
    // Performance monitoring
    this.performanceMetrics = {
      animationFrames: 0,
      averageFPS: 60,
      lastFrameTime: performance.now()
    };
    
    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => this.init());
    } else {
      this.init();
    }
  }
  
  /**
   * Initialize the journey experience
   */
  init() {
    console.log('🚀 Initializing Futuristic Journey Experience');
    
    try {
      this.cacheElements();
      this.setupEventListeners();
      this.initializeObservers();
      this.initializeParticleSystem();
      this.setupKeyboardNavigation();
      this.setupTouchGestures();
      this.initializeAnimations();
      this.setupPerformanceMonitoring();
      this.preloadAssets();
      
      // Hide loading overlay after initialization
      this.hideLoadingOverlay();
      
      console.log('✅ Journey experience initialized successfully');
    } catch (error) {
      console.error('❌ Error initializing journey experience:', error);
      this.fallbackToBasicMode();
    }
  }
  
  /**
   * Cache DOM elements for performance
   */
  cacheElements() {
    this.elements = {
      section: document.querySelector('.futuristic-journey-section'),
      stepsContainer: document.querySelector('.journey-steps-container'),
      steps: document.querySelectorAll('.journey-step'),
      indicators: document.querySelectorAll('.step-indicator'),
      navPrev: document.querySelector('.nav-prev'),
      navNext: document.querySelector('.nav-next'),
      progressBar: document.querySelector('.progress-bar'),
      progressLabel: document.querySelector('.progress-label'),
      currentStepLabel: document.querySelector('.current-step'),
      totalStepsLabel: document.querySelector('.total-steps'),
      canvas: document.getElementById('particles-canvas'),
      loadingOverlay: document.getElementById('journey-loading'),
      keyboardHelp: document.getElementById('keyboard-help'),
      cards: document.querySelectorAll('.step-card'),
      flipButtons: document.querySelectorAll('.flip-card-btn, .enhanced-btn'),
      backButtons: document.querySelectorAll('.back-to-front-btn'),
      ctaButtons: document.querySelectorAll('.cta-primary, .cta-secondary'),
      quickButtons: document.querySelectorAll('.quick-btn'),
      progressRings: document.querySelectorAll('.ring-progress'),
      trustNumbers: document.querySelectorAll('.trust-number'),
      statValues: document.querySelectorAll('.stat-value')
    };
    
    this.totalSteps = this.elements.steps.length;
    if (this.elements.totalStepsLabel) {
      this.elements.totalStepsLabel.textContent = this.totalSteps;
    }
  }
  
  /**
   * Setup event listeners for navigation and interaction
   */
  setupEventListeners() {
    // Navigation buttons
    this.elements.navPrev?.addEventListener('click', () => this.previousStep());
    this.elements.navNext?.addEventListener('click', () => this.nextStep());
    
    // Step indicators
    this.elements.indicators.forEach((indicator, index) => {
      indicator.addEventListener('click', () => this.goToStep(index));
    });
    
    // Card flip buttons
    this.elements.flipButtons.forEach((button, index) => {
      button.addEventListener('click', (e) => {
        e.stopPropagation();
        this.flipCard(index);
      });
    });
    
    // Back to front buttons
    this.elements.backButtons.forEach((button, index) => {
      button.addEventListener('click', (e) => {
        e.stopPropagation();
        this.flipCard(index, false);
      });
    });
    
    // CTA buttons with enhanced feedback
    this.elements.ctaButtons.forEach(button => {
      button.addEventListener('click', (e) => this.handleCTAClick(e));
    });
    
    // Quick action buttons
    this.elements.quickButtons.forEach((button, index) => {
      button.addEventListener('click', (e) => this.handleQuickAction(e, index));
    });
    
    // Window resize handling
    window.addEventListener('resize', this.debounce(() => {
      this.handleResize();
    }, 150));
    
    // Visibility change for performance optimization
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        this.pauseAnimations();
      } else {
        this.resumeAnimations();
      }
    });
    
    // Help button (if exists)
    const helpButton = document.querySelector('.help-button');
    if (helpButton) {
      helpButton.addEventListener('click', () => this.showKeyboardHelp());
    }
    
    // Close help
    const helpClose = document.querySelector('.help-close');
    if (helpClose) {
      helpClose.addEventListener('click', () => this.hideKeyboardHelp());
    }
  }
  
  /**
   * Initialize modern observers for performance and UX
   */
  initializeObservers() {
    // Intersection Observer for scroll animations
    if ('IntersectionObserver' in window) {
      this.intersectionObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            this.animateElementIntoView(entry.target);
          }
        });
      }, {
        rootMargin: '50px',
        threshold: 0.1
      });
      
      // Observe elements with data-animate attribute
      document.querySelectorAll('[data-animate]').forEach(element => {
        this.intersectionObserver.observe(element);
      });
    }
    
    // Resize Observer for responsive handling
    if ('ResizeObserver' in window) {
      let resizeRAF;
      this.resizeObserver = new ResizeObserver(entries => {
        // Debounce using requestAnimationFrame to prevent loop
        if (resizeRAF) window.cancelAnimationFrame(resizeRAF);
        resizeRAF = window.requestAnimationFrame(() => {
          for (let entry of entries) {
            if (entry.target === this.elements.section) {
              this.handleSectionResize(entry);
            }
          }
        });
      });

      if (this.elements.section) {
        this.resizeObserver.observe(this.elements.section);
      }
    }
    
    // Performance Observer for monitoring
    if ('PerformanceObserver' in window) {
      try {
        this.performanceObserver = new PerformanceObserver((list) => {
          const entries = list.getEntries();
          entries.forEach(entry => {
            if (entry.entryType === 'measure') {
              console.log(`Performance: ${entry.name} took ${entry.duration.toFixed(2)}ms`);
            }
          });
        });
        
        this.performanceObserver.observe({ entryTypes: ['measure', 'navigation'] });
      } catch (error) {
        console.warn('Performance Observer not fully supported:', error);
      }
    }
  }
  
  /**
   * Initialize particle system for background effects
   */
  initializeParticleSystem() {
    if (!this.elements.canvas || !this.elements.canvas.getContext) return;
    
    const canvas = this.elements.canvas;
    const ctx = canvas.getContext('2d');
    
    // Set canvas size
    const resizeCanvas = () => {
      const rect = this.elements.section.getBoundingClientRect();
      canvas.width = rect.width;
      canvas.height = rect.height;
    };
    
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
    
    // Particle system
    this.particleSystem = {
      particles: [],
      maxParticles: this.isMobile() ? 50 : 100,
      
      init() {
        for (let i = 0; i < this.maxParticles; i++) {
          this.particles.push(this.createParticle());
        }
      },
      
      createParticle() {
        return {
          x: Math.random() * canvas.width,
          y: Math.random() * canvas.height,
          vx: (Math.random() - 0.5) * 0.5,
          vy: (Math.random() - 0.5) * 0.5,
          size: Math.random() * 2 + 1,
          opacity: Math.random() * 0.5 + 0.2,
          color: this.getRandomColor(),
          life: Math.random() * 100 + 100
        };
      },
      
      getRandomColor() {
        const colors = ['#d4af37', '#00d4ff', '#b084cc', '#39ff14', '#ff10f0'];
        return colors[Math.floor(Math.random() * colors.length)];
      },
      
      update() {
        this.particles.forEach((particle, index) => {
          particle.x += particle.vx;
          particle.y += particle.vy;
          particle.life--;
          
          // Wrap around edges
          if (particle.x < 0) particle.x = canvas.width;
          if (particle.x > canvas.width) particle.x = 0;
          if (particle.y < 0) particle.y = canvas.height;
          if (particle.y > canvas.height) particle.y = 0;
          
          // Respawn particle if life is over
          if (particle.life <= 0) {
            this.particles[index] = this.createParticle();
          }
        });
      },
      
      draw(ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        this.particles.forEach(particle => {
          ctx.save();
          ctx.globalAlpha = particle.opacity;
          ctx.fillStyle = particle.color;
          ctx.shadowBlur = particle.size * 2;
          ctx.shadowColor = particle.color;
          ctx.beginPath();
          ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        });
        
        // Draw connections between nearby particles
        this.drawConnections(ctx);
      },
      
      drawConnections(ctx) {
        for (let i = 0; i < this.particles.length; i++) {
          for (let j = i + 1; j < this.particles.length; j++) {
            const dx = this.particles[i].x - this.particles[j].x;
            const dy = this.particles[i].y - this.particles[j].y;
            const distance = Math.sqrt(dx * dx + dy * dy);
            
            if (distance < 100) {
              const opacity = (100 - distance) / 100 * 0.1;
              ctx.save();
              ctx.globalAlpha = opacity;
              ctx.strokeStyle = '#d4af37';
              ctx.lineWidth = 1;
              ctx.beginPath();
              ctx.moveTo(this.particles[i].x, this.particles[i].y);
              ctx.lineTo(this.particles[j].x, this.particles[j].y);
              ctx.stroke();
              ctx.restore();
            }
          }
        }
      }
    };
    
    this.particleSystem.init();
    this.startParticleAnimation();
  }
  
  /**
   * Start particle animation loop
   */
  startParticleAnimation() {
    if (!this.particleSystem || !this.elements.canvas) return;
    
    const ctx = this.elements.canvas.getContext('2d');
    let lastTime = performance.now();
    
    const animate = (currentTime) => {
      const deltaTime = currentTime - lastTime;
      
      // Limit to 60fps for performance
      if (deltaTime >= 16.67) {
        this.particleSystem.update();
        this.particleSystem.draw(ctx);
        lastTime = currentTime;
        
        // Update FPS counter
        this.updateFPS(deltaTime);
      }
      
      if (!document.hidden) {
        requestAnimationFrame(animate);
      }
    };
    
    requestAnimationFrame(animate);
  }
  
  /**
   * Setup keyboard navigation with accessibility
   */
  setupKeyboardNavigation() {
    // Define keyboard shortcuts
    this.keyboardShortcuts.set('ArrowLeft', () => this.previousStep());
    this.keyboardShortcuts.set('ArrowRight', () => this.nextStep());
    this.keyboardShortcuts.set('Space', (e) => {
      e.preventDefault();
      const activeCard = this.elements.cards[this.currentStep];
      if (activeCard) {
        this.flipCard(this.currentStep);
      }
    });
    this.keyboardShortcuts.set('Enter', (e) => {
      const focusedElement = document.activeElement;
      if (focusedElement && focusedElement.click) {
        focusedElement.click();
      }
    });
    this.keyboardShortcuts.set('Escape', () => {
      this.hideKeyboardHelp();
    });
    this.keyboardShortcuts.set('KeyH', () => {
      this.showKeyboardHelp();
    });
    
    // Add number keys for direct step navigation
    for (let i = 1; i <= 9; i++) {
      this.keyboardShortcuts.set(`Digit${i}`, () => {
        if (i <= this.totalSteps) {
          this.goToStep(i - 1);
        }
      });
    }
    
    // Keyboard event listener
    document.addEventListener('keydown', (e) => {
      const handler = this.keyboardShortcuts.get(e.code);
      if (handler && !this.isInputFocused()) {
        handler(e);
      }
    });
    
    // Focus management
    this.elements.section?.setAttribute('tabindex', '0');
  }
  
  /**
   * Setup touch gestures for mobile interaction
   */
  setupTouchGestures() {
    if (!this.elements.stepsContainer) return;
    
    // Use modern Pointer Events if available
    if ('PointerEvent' in window) {
      this.setupPointerEvents();
    } else {
      this.setupTouchEvents();
    }
  }
  
  /**
   * Setup modern Pointer Events
   */
  setupPointerEvents() {
    let startX = 0;
    let startY = 0;
    let isDragging = false;
    
    this.elements.stepsContainer.addEventListener('pointerdown', (e) => {
      startX = e.clientX;
      startY = e.clientY;
      isDragging = true;
      e.preventDefault();
    });
    
    this.elements.stepsContainer.addEventListener('pointermove', (e) => {
      if (!isDragging) return;
      
      const deltaX = e.clientX - startX;
      const deltaY = e.clientY - startY;
      
      // Only handle horizontal swipes
      if (Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > 10) {
        this.elements.stepsContainer.style.transform = `translateX(${deltaX}px)`;
      }
    });
    
    this.elements.stepsContainer.addEventListener('pointerup', (e) => {
      if (!isDragging) return;
      
      const deltaX = e.clientX - startX;
      const threshold = 100;
      
      this.elements.stepsContainer.style.transform = '';
      
      if (Math.abs(deltaX) > threshold) {
        if (deltaX > 0) {
          this.previousStep();
        } else {
          this.nextStep();
        }
      }
      
      isDragging = false;
    });
  }
  
  /**
   * Setup traditional touch events as fallback
   */
  setupTouchEvents() {
    this.elements.stepsContainer.addEventListener('touchstart', (e) => {
      this.touchStartX = e.touches[0].clientX;
      this.touchStartY = e.touches[0].clientY;
    }, { passive: true });
    
    this.elements.stepsContainer.addEventListener('touchend', (e) => {
      if (!this.touchStartX || !this.touchStartY) return;
      
      const touchEndX = e.changedTouches[0].clientX;
      const touchEndY = e.changedTouches[0].clientY;
      
      const deltaX = touchEndX - this.touchStartX;
      const deltaY = touchEndY - this.touchStartY;
      
      // Only handle horizontal swipes
      if (Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > 50) {
        if (deltaX > 0) {
          this.previousStep();
        } else {
          this.nextStep();
        }
      }
      
      this.touchStartX = 0;
      this.touchStartY = 0;
    }, { passive: true });
  }
  
  /**
   * Initialize 3D tilt effects for cards
   */
  initializeAnimations() {
    // Setup 3D tilt effects
    this.elements.cards.forEach((card, index) => {
      this.setupTiltEffect(card);
    });
    
    // Animate counters
    this.animateCounters();
    
    // Setup hover effects with Web Animations API
    this.setupWebAnimations();
  }
  
  /**
   * Setup 3D tilt effect for cards
   */
  setupTiltEffect(card) {
    if (!card.hasAttribute('data-tilt')) return;
    
    let isHovering = false;
    
    const handleMouseMove = (e) => {
      if (!isHovering) return;
      
      const rect = card.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      
      const deltaX = (e.clientX - centerX) / (rect.width / 2);
      const deltaY = (e.clientY - centerY) / (rect.height / 2);
      
      const rotateX = deltaY * -10; // Max 10 degrees
      const rotateY = deltaX * 10;
      
      card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(1.02, 1.02, 1.02)`;
    };
    
    const handleMouseEnter = () => {
      isHovering = true;
      card.style.transition = 'transform 0.1s ease-out';
    };
    
    const handleMouseLeave = () => {
      isHovering = false;
      card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)';
      card.style.transition = 'transform 0.3s ease-out';
    };
    
    // Only add mouse events on non-touch devices
    if (!this.isTouchDevice()) {
      card.addEventListener('mousemove', handleMouseMove);
      card.addEventListener('mouseenter', handleMouseEnter);
      card.addEventListener('mouseleave', handleMouseLeave);
    }
  }
  
  /**
   * Setup Web Animations API effects
   */
  setupWebAnimations() {
    // Enhanced button hover animations
    this.elements.ctaButtons.forEach(button => {
      if ('animate' in button) {
        button.addEventListener('mouseenter', () => {
          button.animate([
            { transform: 'translateY(0) scale(1)' },
            { transform: 'translateY(-3px) scale(1.05)' }
          ], {
            duration: 200,
            easing: 'cubic-bezier(0.4, 0, 0.2, 1)',
            fill: 'forwards'
          });
        });
        
        button.addEventListener('mouseleave', () => {
          button.animate([
            { transform: 'translateY(-3px) scale(1.05)' },
            { transform: 'translateY(0) scale(1)' }
          ], {
            duration: 200,
            easing: 'cubic-bezier(0.4, 0, 0.2, 1)',
            fill: 'forwards'
          });
        });
      }
    });
  }
  
  /**
   * Navigate to previous step
   */
  previousStep() {
    if (this.currentStep > 0 && !this.isAnimating) {
      this.goToStep(this.currentStep - 1);
    }
  }
  
  /**
   * Navigate to next step
   */
  nextStep() {
    if (this.currentStep < this.totalSteps - 1 && !this.isAnimating) {
      this.goToStep(this.currentStep + 1);
    }
  }
  
  /**
   * Navigate to specific step
   */
  async goToStep(stepIndex) {
    if (stepIndex === this.currentStep || this.isAnimating) return;
    if (stepIndex < 0 || stepIndex >= this.totalSteps) return;
    
    performance.mark('step-transition-start');
    this.isAnimating = true;
    
    try {
      // Update navigation state
      this.updateNavigationState(stepIndex);
      
      // Animate step transition
      await this.animateStepTransition(this.currentStep, stepIndex);
      
      // Update current step
      this.currentStep = stepIndex;
      
      // Update progress
      this.updateProgress();
      
      // Animate counter for current step
      this.animateStepCounter(stepIndex);
      
      // Announce step change for screen readers
      this.announceStepChange();
      
      performance.mark('step-transition-end');
      performance.measure('step-transition', 'step-transition-start', 'step-transition-end');
      
    } catch (error) {
      console.error('Error transitioning to step:', error);
    } finally {
      this.isAnimating = false;
    }
  }
  
  /**
   * Animate step transition with modern CSS animations
   */
  animateStepTransition(fromIndex, toIndex) {
    return new Promise((resolve) => {
      const fromStep = this.elements.steps[fromIndex];
      const toStep = this.elements.steps[toIndex];
      
      if (!fromStep || !toStep) {
        resolve();
        return;
      }
      
      // Determine animation direction
      const direction = toIndex > fromIndex ? 1 : -1;
      
      // Remove active class from current step
      fromStep.classList.remove('active');
      if (direction > 0) {
        fromStep.classList.add('prev');
      }
      
      // Set initial position for new step
      toStep.style.transform = `translateX(${direction * 100}px) rotateY(${direction * 15}deg)`;
      toStep.style.opacity = '0';
      
      // Force reflow
      toStep.offsetHeight;
      
      // Add active class to new step
      toStep.classList.add('active');
      toStep.classList.remove('prev');
      
      // Animate to final position
      toStep.style.transition = 'all 0.6s cubic-bezier(0.16, 1, 0.3, 1)';
      toStep.style.transform = 'translateX(0) rotateY(0deg)';
      toStep.style.opacity = '1';
      
      // Cleanup after animation
      setTimeout(() => {
        fromStep.classList.remove('prev');
        toStep.style.transition = '';
        toStep.style.transform = '';
        toStep.style.opacity = '';
        resolve();
      }, 600);
    });
  }
  
  /**
   * Update navigation state (indicators, buttons)
   */
  updateNavigationState(stepIndex) {
    // Update step indicators
    this.elements.indicators.forEach((indicator, index) => {
      indicator.classList.toggle('active', index === stepIndex);
      indicator.setAttribute('aria-current', index === stepIndex ? 'step' : 'false');
    });
    
    // Update navigation buttons
    if (this.elements.navPrev) {
      this.elements.navPrev.disabled = stepIndex === 0;
    }
    if (this.elements.navNext) {
      this.elements.navNext.disabled = stepIndex === this.totalSteps - 1;
    }
  }
  
  /**
   * Update progress bar and labels
   */
  updateProgress() {
    const progress = ((this.currentStep + 1) / this.totalSteps) * 100;
    
    if (this.elements.progressBar) {
      this.elements.progressBar.style.width = `${progress}%`;
    }
    
    if (this.elements.currentStepLabel) {
      this.elements.currentStepLabel.textContent = this.currentStep + 1;
    }
    
    // Update ARIA attributes
    const progressContainer = document.querySelector('.journey-progress');
    if (progressContainer) {
      progressContainer.setAttribute('aria-valuenow', progress);
    }
  }
  
  /**
   * Flip card to show back content
   */
  flipCard(index, showBack = true) {
    const card = this.elements.cards[index];
    if (!card) return;
    
    if (showBack) {
      card.classList.add('flipped');
      card.setAttribute('aria-expanded', 'true');
    } else {
      card.classList.remove('flipped');
      card.setAttribute('aria-expanded', 'false');
    }
    
    // Haptic feedback if supported
    if ('vibrate' in navigator) {
      navigator.vibrate(50);
    }
  }
  
  /**
   * Animate counters with intersection observer
   */
  animateCounters() {
    const animateCounter = (element) => {
      const target = parseInt(element.dataset.count) || 0;
      const duration = 2000; // 2 seconds
      const startTime = performance.now();
      const startValue = 0;
      
      const updateCounter = (currentTime) => {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        
        // Easing function (easeOutCubic)
        const easeOutCubic = 1 - Math.pow(1 - progress, 3);
        const currentValue = Math.floor(startValue + (target - startValue) * easeOutCubic);
        
        element.textContent = currentValue.toLocaleString();
        
        if (progress < 1) {
          requestAnimationFrame(updateCounter);
        } else {
          element.textContent = target.toLocaleString();
        }
      };
      
      requestAnimationFrame(updateCounter);
    };
    
    // Animate all counter elements when they come into view
    const counterElements = [...this.elements.statValues, ...this.elements.trustNumbers];
    
    if ('IntersectionObserver' in window) {
      const counterObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting && !entry.target.dataset.animated) {
            entry.target.dataset.animated = 'true';
            animateCounter(entry.target);
          }
        });
      }, { threshold: 0.5 });
      
      counterElements.forEach(element => {
        counterObserver.observe(element);
      });
    } else {
      // Fallback for browsers without Intersection Observer
      counterElements.forEach(element => {
        animateCounter(element);
      });
    }
  }
  
  /**
   * Animate single step counter
   */
  animateStepCounter(stepIndex) {
    const step = this.elements.steps[stepIndex];
    if (!step) return;
    
    const counter = step.querySelector('.stat-value');
    if (counter && !counter.dataset.animated) {
      counter.dataset.animated = 'true';
      
      const target = parseInt(counter.dataset.count) || 0;
      const duration = 1000;
      const startTime = performance.now();
      
      const updateCounter = (currentTime) => {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const currentValue = Math.floor(target * progress);
        
        counter.textContent = currentValue;
        
        if (progress < 1) {
          requestAnimationFrame(updateCounter);
        }
      };
      
      requestAnimationFrame(updateCounter);
    }
  }
  
  /**
   * Handle CTA button clicks with enhanced feedback
   */
  handleCTAClick(event) {
    const button = event.currentTarget;
    
    // Create ripple effect
    this.createRippleEffect(button, event);
    
    // Haptic feedback
    if ('vibrate' in navigator) {
      navigator.vibrate([50, 30, 50]);
    }
    
    // Analytics tracking (if available)
    if (typeof gtag === 'function') {
      gtag('event', 'cta_click', {
        'event_category': 'engagement',
        'event_label': button.textContent.trim()
      });
    }
    
    console.log('CTA clicked:', button.textContent);
  }
  
  /**
   * Handle quick action button clicks
   */
  handleQuickAction(event, index) {
    const button = event.currentTarget;
    const actions = ['share', 'favorite'];
    const action = actions[index % actions.length];
    
    // Create pulse effect
    this.createPulseEffect(button);
    
    // Haptic feedback
    if ('vibrate' in navigator) {
      navigator.vibrate(25);
    }
    
    // Handle different actions
    switch (action) {
      case 'share':
        this.handleShare();
        break;
      case 'favorite':
        this.handleFavorite(button);
        break;
    }
    
    // Analytics tracking
    if (typeof gtag === 'function') {
      gtag('event', 'quick_action', {
        'event_category': 'interaction',
        'event_label': action
      });
    }
    
    console.log('Quick action:', action);
  }
  
  /**
   * Handle share functionality
   */
  handleShare() {
    if (navigator.share) {
      navigator.share({
        title: 'VinsDeLux - Parcours Client',
        text: 'Découvrez notre parcours client unique pour l\'adoption de vignes',
        url: window.location.href
      }).catch(err => console.log('Error sharing:', err));
    } else {
      // Fallback to copying URL to clipboard
      navigator.clipboard.writeText(window.location.href).then(() => {
        this.showToast('Lien copié dans le presse-papiers!');
      }).catch(err => {
        console.log('Error copying to clipboard:', err);
      });
    }
  }
  
  /**
   * Handle favorite functionality
   */
  handleFavorite(button) {
    const isFavorited = button.classList.contains('favorited');
    
    if (isFavorited) {
      button.classList.remove('favorited');
      button.style.color = '';
      this.showToast('Retiré des favoris');
    } else {
      button.classList.add('favorited');
      button.style.color = '#ff10f0';
      this.showToast('Ajouté aux favoris');
    }
  }
  
  /**
   * Show toast notification
   */
  showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'toast-notification';
    toast.textContent = message;
    toast.style.cssText = `
      position: fixed;
      bottom: 2rem;
      left: 50%;
      transform: translateX(-50%);
      background: var(--card-bg);
      color: var(--text-primary);
      padding: 1rem 2rem;
      border-radius: 50px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      backdrop-filter: blur(15px);
      z-index: 10000;
      font-size: 0.9rem;
      opacity: 0;
      transition: all 0.3s ease;
    `;
    
    document.body.appendChild(toast);
    
    // Animate in
    requestAnimationFrame(() => {
      toast.style.opacity = '1';
      toast.style.transform = 'translateX(-50%) translateY(0)';
    });
    
    // Remove after delay
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(-50%) translateY(20px)';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }
  
  /**
   * Create pulse effect for buttons
   */
  createPulseEffect(button) {
    const pulse = document.createElement('div');
    pulse.className = 'pulse-effect';
    pulse.style.cssText = `
      position: absolute;
      top: 50%;
      left: 50%;
      width: 0;
      height: 0;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(212, 175, 55, 0.6), transparent);
      transform: translate(-50%, -50%);
      animation: pulse-expand 0.6s ease-out;
      pointer-events: none;
    `;
    
    button.style.position = 'relative';
    button.style.overflow = 'hidden';
    button.appendChild(pulse);
    
    pulse.addEventListener('animationend', () => pulse.remove());
  }
  
  /**
   * Create ripple effect on button click
   */
  createRippleEffect(button, event) {
    const rect = button.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const x = event.clientX - rect.left - size / 2;
    const y = event.clientY - rect.top - size / 2;
    
    const ripple = document.createElement('div');
    ripple.style.cssText = `
      position: absolute;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.3);
      width: ${size}px;
      height: ${size}px;
      left: ${x}px;
      top: ${y}px;
      transform: scale(0);
      animation: ripple 0.6s linear;
      pointer-events: none;
    `;
    
    button.style.position = 'relative';
    button.style.overflow = 'hidden';
    button.appendChild(ripple);
    
    // Remove ripple after animation
    ripple.addEventListener('animationend', () => {
      ripple.remove();
    });
  }
  
  /**
   * Setup performance monitoring
   */
  setupPerformanceMonitoring() {
    let frameCount = 0;
    let lastTime = performance.now();
    
    const measureFPS = () => {
      frameCount++;
      const currentTime = performance.now();
      
      if (currentTime >= lastTime + 1000) {
        this.performanceMetrics.averageFPS = Math.round((frameCount * 1000) / (currentTime - lastTime));
        frameCount = 0;
        lastTime = currentTime;
        
        // Log performance warnings
        if (this.performanceMetrics.averageFPS < 30) {
          console.warn('Low FPS detected:', this.performanceMetrics.averageFPS);
          this.optimizeForPerformance();
        }
      }
      
      requestAnimationFrame(measureFPS);
    };
    
    requestAnimationFrame(measureFPS);
  }
  
  /**
   * Optimize for performance when FPS is low
   */
  optimizeForPerformance() {
    // Reduce particle count
    if (this.particleSystem) {
      this.particleSystem.maxParticles = Math.max(20, this.particleSystem.maxParticles * 0.7);
    }
    
    // Disable some animations
    document.body.classList.add('reduced-animations');
    
    console.log('Performance optimizations applied');
  }
  
  /**
   * Update FPS counter
   */
  updateFPS(deltaTime) {
    this.performanceMetrics.animationFrames++;
    const fps = 1000 / deltaTime;
    this.performanceMetrics.averageFPS = (this.performanceMetrics.averageFPS + fps) / 2;
  }
  
  /**
   * Show keyboard help dialog
   */
  showKeyboardHelp() {
    if (this.elements.keyboardHelp) {
      this.elements.keyboardHelp.setAttribute('aria-hidden', 'false');
      this.elements.keyboardHelp.querySelector('.help-close')?.focus();
    }
  }
  
  /**
   * Hide keyboard help dialog
   */
  hideKeyboardHelp() {
    if (this.elements.keyboardHelp) {
      this.elements.keyboardHelp.setAttribute('aria-hidden', 'true');
    }
  }
  
  /**
   * Animate element into view
   */
  animateElementIntoView(element) {
    const animationType = element.dataset.animate;
    const delay = parseInt(element.dataset.delay) || 0;
    
    setTimeout(() => {
      switch (animationType) {
        case 'fade-up':
          element.style.opacity = '1';
          element.style.transform = 'translateY(0)';
          break;
        case 'fade-in':
          element.style.opacity = '1';
          break;
        case 'slide-left':
          element.style.transform = 'translateX(0)';
          break;
        default:
          element.style.opacity = '1';
      }
    }, delay);
  }
  
  /**
   * Handle section resize
   */
  handleSectionResize(entry) {
    if (this.elements.canvas) {
      const rect = entry.contentRect;
      this.elements.canvas.width = rect.width;
      this.elements.canvas.height = rect.height;
    }
  }
  
  /**
   * Handle window resize
   */
  handleResize() {
    // Recalculate particle system if needed
    if (this.particleSystem && this.elements.canvas) {
      const rect = this.elements.section.getBoundingClientRect();
      this.elements.canvas.width = rect.width;
      this.elements.canvas.height = rect.height;
    }
  }
  
  /**
   * Announce step change for screen readers
   */
  announceStepChange() {
    const announcement = `Step ${this.currentStep + 1} of ${this.totalSteps}`;
    const srOnly = document.createElement('div');
    srOnly.setAttribute('aria-live', 'polite');
    srOnly.setAttribute('aria-atomic', 'true');
    srOnly.className = 'sr-only';
    srOnly.textContent = announcement;
    
    document.body.appendChild(srOnly);
    
    setTimeout(() => {
      document.body.removeChild(srOnly);
    }, 1000);
  }
  
  /**
   * Preload assets for better performance
   */
  async preloadAssets() {
    const images = this.elements.section.querySelectorAll('img[src]');
    const preloadPromises = Array.from(images).map(img => {
      return new Promise((resolve, reject) => {
        const preloadImg = new Image();
        preloadImg.onload = resolve;
        preloadImg.onerror = reject;
        preloadImg.src = img.src;
      });
    });
    
    try {
      await Promise.all(preloadPromises);
      console.log('Assets preloaded successfully');
    } catch (error) {
      console.warn('Some assets failed to preload:', error);
    }
  }
  
  /**
   * Hide loading overlay
   */
  hideLoadingOverlay() {
    if (this.elements.loadingOverlay) {
      this.elements.loadingOverlay.classList.remove('active');
      setTimeout(() => {
        this.elements.loadingOverlay.style.display = 'none';
      }, 300);
    }
  }
  
  /**
   * Pause animations when page is hidden
   */
  pauseAnimations() {
    document.body.classList.add('animations-paused');
  }
  
  /**
   * Resume animations when page is visible
   */
  resumeAnimations() {
    document.body.classList.remove('animations-paused');
    if (this.particleSystem) {
      this.startParticleAnimation();
    }
  }
  
  /**
   * Fallback to basic mode if advanced features fail
   */
  fallbackToBasicMode() {
    console.warn('Falling back to basic mode');
    document.body.classList.add('basic-mode');
    
    // Hide advanced elements
    if (this.elements.canvas) {
      this.elements.canvas.style.display = 'none';
    }
    
    // Setup basic navigation
    this.elements.navPrev?.addEventListener('click', () => this.previousStep());
    this.elements.navNext?.addEventListener('click', () => this.nextStep());
    
    this.hideLoadingOverlay();
  }
  
  /**
   * Utility functions
   */
  debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
      const later = () => {
        clearTimeout(timeout);
        func(...args);
      };
      clearTimeout(timeout);
      timeout = setTimeout(later, wait);
    };
  }
  
  isMobile() {
    return window.innerWidth <= 768 || 'ontouchstart' in window;
  }
  
  isTouchDevice() {
    return 'ontouchstart' in window || navigator.maxTouchPoints > 0;
  }
  
  isInputFocused() {
    const activeElement = document.activeElement;
    return activeElement && (
      activeElement.tagName === 'INPUT' ||
      activeElement.tagName === 'TEXTAREA' ||
      activeElement.contentEditable === 'true'
    );
  }
  
  /**
   * Cleanup when component is destroyed
   */
  destroy() {
    // Disconnect observers
    if (this.intersectionObserver) {
      this.intersectionObserver.disconnect();
    }
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }
    if (this.performanceObserver) {
      this.performanceObserver.disconnect();
    }
    
    // Remove event listeners
    window.removeEventListener('resize', this.handleResize);
    document.removeEventListener('keydown', this.handleKeyDown);
    document.removeEventListener('visibilitychange', this.handleVisibilityChange);
    
    console.log('Futuristic Journey cleaned up');
  }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  // Only initialize if the section exists
  if (document.querySelector('.futuristic-journey-section')) {
    window.futuristicJourney = new FuturisticJourney();
  }
});

// Add animation keyframes
const style = document.createElement('style');
style.textContent = `
  @keyframes ripple {
    to {
      transform: scale(4);
      opacity: 0;
    }
  }
  
  @keyframes pulse-expand {
    to {
      width: 100px;
      height: 100px;
      opacity: 0;
    }
  }
  
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  
  .animations-paused * {
    animation-play-state: paused !important;
  }
  
  .basic-mode .constellation-bg,
  .basic-mode .particles-canvas,
  .basic-mode .gradient-overlay {
    display: none !important;
  }
  
  .reduced-animations *,
  .reduced-animations *::before,
  .reduced-animations *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
`;
document.head.appendChild(style);