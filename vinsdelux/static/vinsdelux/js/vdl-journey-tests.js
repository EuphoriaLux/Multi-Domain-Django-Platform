/**
 * VinsDeLux Futuristic Journey - Comprehensive Test Suite
 * Tests for all interactive functionality, animations, and user experience features
 */

class JourneyTestSuite {
    constructor() {
        this.testResults = [];
        this.passedTests = 0;
        this.failedTests = 0;
        this.journey = null;
        this.setupRequired = true;
    }

    /**
     * Initialize test environment
     */
    async init() {
        console.log('🧪 Initializing VinsDeLux Journey Test Suite...');
        
        // Wait for DOM to be ready
        if (document.readyState !== 'complete') {
            await new Promise(resolve => {
                window.addEventListener('load', resolve);
            });
        }

        // Initialize the journey system if available
        if (window.FuturisticJourney) {
            this.journey = new window.FuturisticJourney();
            await this.journey.init();
        }

        this.log('✅ Test environment initialized');
        return this;
    }

    /**
     * Run all tests
     */
    async runAllTests() {
        console.log('🚀 Starting comprehensive test suite...');
        console.time('Test Suite Execution');

        try {
            // Core functionality tests
            await this.testDOMStructure();
            await this.testNavigationControls();
            await this.testCardInteractions();
            await this.testAnimations();
            
            // Enhanced features tests
            await this.testProgressSystem();
            await this.testKeyboardNavigation();
            await this.testMobileOptimizations();
            await this.testAccessibility();
            
            // Visual and UI tests
            await this.testImageLoading();
            await this.testResponsiveDesign();
            await this.testPerformance();
            
            // Advanced functionality tests
            await this.testShareFunctionality();
            await this.testFavoritesSystem();
            await this.testAnalyticsTracking();
            
        } catch (error) {
            this.fail('Critical test failure', error.message);
        }

        console.timeEnd('Test Suite Execution');
        this.displayResults();
    }

    /**
     * Test DOM structure and element presence
     */
    async testDOMStructure() {
        this.log('📋 Testing DOM Structure...');

        // Test main container
        this.assert(
            document.querySelector('.futuristic-journey-section'),
            'Main journey section exists'
        );

        // Test navigation elements
        this.assert(
            document.querySelector('.journey-navigation'),
            'Navigation container exists'
        );

        this.assert(
            document.querySelectorAll('.step-indicator').length > 0,
            'Step indicators are present'
        );

        // Test card structure
        this.assert(
            document.querySelectorAll('.step-card').length > 0,
            'Journey cards are present'
        );

        this.assert(
            document.querySelectorAll('.card-face').length >= 2,
            'Card faces (front/back) are present'
        );

        // Test enhanced elements
        this.assert(
            document.querySelectorAll('.progress-ring').length > 0,
            'Progress rings are present'
        );

        this.assert(
            document.querySelectorAll('.step-status-badge').length > 0,
            'Status badges are present'
        );

        this.log('✅ DOM Structure tests completed');
    }

    /**
     * Test navigation controls functionality
     */
    async testNavigationControls() {
        this.log('🎯 Testing Navigation Controls...');

        const prevBtn = document.querySelector('.nav-prev');
        const nextBtn = document.querySelector('.nav-next');
        const indicators = document.querySelectorAll('.step-indicator');

        // Test button existence
        this.assert(prevBtn && nextBtn, 'Navigation buttons exist');
        
        // Test initial state
        this.assert(
            prevBtn.disabled,
            'Previous button is initially disabled'
        );

        // Test indicator clicks
        if (indicators.length > 1) {
            const secondIndicator = indicators[1];
            this.simulateClick(secondIndicator);
            
            await this.wait(100);
            
            this.assert(
                secondIndicator.classList.contains('active'),
                'Indicator activation works'
            );
        }

        // Test next button functionality
        if (nextBtn && !nextBtn.disabled) {
            this.simulateClick(nextBtn);
            await this.wait(300);
            
            this.assert(
                !prevBtn.disabled,
                'Previous button becomes enabled after navigation'
            );
        }

        this.log('✅ Navigation Controls tests completed');
    }

    /**
     * Test card interactions (flip, hover, etc.)
     */
    async testCardInteractions() {
        this.log('🎴 Testing Card Interactions...');

        const cards = document.querySelectorAll('.step-card');
        const flipButtons = document.querySelectorAll('.flip-card-btn');

        this.assert(cards.length > 0, 'Cards are present for interaction testing');

        if (cards.length > 0 && flipButtons.length > 0) {
            const firstCard = cards[0];
            const firstFlipBtn = flipButtons[0];

            // Test card flip functionality
            this.simulateClick(firstFlipBtn);
            await this.wait(500);
            
            this.assert(
                firstCard.classList.contains('flipped'),
                'Card flips when button is clicked'
            );

            // Test return to front
            const backBtn = firstCard.querySelector('.back-to-front-btn');
            if (backBtn) {
                this.simulateClick(backBtn);
                await this.wait(500);
                
                this.assert(
                    !firstCard.classList.contains('flipped'),
                    'Card returns to front when back button is clicked'
                );
            }

            // Test hover effects (simulate mouseenter/mouseleave)
            this.simulateEvent(firstCard, 'mouseenter');
            await this.wait(100);
            
            // Check if tilt effect is applied (transform property)
            const hasTransform = window.getComputedStyle(firstCard).transform !== 'none';
            this.assert(hasTransform, 'Card tilt effect works on hover');
        }

        this.log('✅ Card Interactions tests completed');
    }

    /**
     * Test animations and transitions
     */
    async testAnimations() {
        this.log('✨ Testing Animations...');

        // Test progress ring animations
        const progressRings = document.querySelectorAll('.ring-progress');
        this.assert(progressRings.length > 0, 'Progress rings are present');

        if (progressRings.length > 0) {
            const ring = progressRings[0];
            const strokeDashoffset = window.getComputedStyle(ring).strokeDashoffset;
            this.assert(
                strokeDashoffset !== 'none',
                'Progress ring has animated stroke-dashoffset'
            );
        }

        // Test particle system
        const canvas = document.querySelector('.particles-canvas');
        if (canvas) {
            this.assert(canvas.width > 0 && canvas.height > 0, 'Particle canvas is properly sized');
        }

        // Test fade-in animations
        const animatedElements = document.querySelectorAll('[data-animate]');
        this.assert(animatedElements.length > 0, 'Animated elements are present');

        this.log('✅ Animation tests completed');
    }

    /**
     * Test progress system functionality
     */
    async testProgressSystem() {
        this.log('📊 Testing Progress System...');

        const progressBar = document.querySelector('.progress-bar');
        const currentStep = document.querySelector('.current-step');
        const totalSteps = document.querySelector('.total-steps');

        this.assert(progressBar, 'Progress bar exists');
        this.assert(currentStep && totalSteps, 'Step counters exist');

        if (currentStep && totalSteps) {
            const current = parseInt(currentStep.textContent);
            const total = parseInt(totalSteps.textContent);
            
            this.assert(current >= 0 && current <= total, 'Step counter values are logical');
            this.assert(total > 0, 'Total steps is a positive number');
        }

        this.log('✅ Progress System tests completed');
    }

    /**
     * Test keyboard navigation
     */
    async testKeyboardNavigation() {
        this.log('⌨️ Testing Keyboard Navigation...');

        const activeCard = document.querySelector('.journey-step.active');
        if (activeCard) {
            activeCard.focus();
            
            // Test arrow key navigation
            this.simulateKeyPress('ArrowRight');
            await this.wait(100);
            
            // Test space key for card flip
            this.simulateKeyPress('Space');
            await this.wait(300);
            
            this.log('Keyboard events simulated successfully');
        }

        // Test keyboard help visibility
        const keyboardHelp = document.querySelector('.keyboard-help');
        this.assert(keyboardHelp, 'Keyboard help element exists');

        this.log('✅ Keyboard Navigation tests completed');
    }

    /**
     * Test mobile optimizations
     */
    async testMobileOptimizations() {
        this.log('📱 Testing Mobile Optimizations...');

        // Test touch target sizes
        const touchTargets = document.querySelectorAll('button, .step-indicator');
        let validTouchTargets = 0;

        touchTargets.forEach(target => {
            const rect = target.getBoundingClientRect();
            if (rect.width >= 44 && rect.height >= 44) {
                validTouchTargets++;
            }
        });

        this.assert(
            validTouchTargets === touchTargets.length,
            `All touch targets meet 44px minimum (${validTouchTargets}/${touchTargets.length})`
        );

        // Test viewport meta tag
        const viewportMeta = document.querySelector('meta[name="viewport"]');
        this.assert(viewportMeta, 'Viewport meta tag exists for mobile optimization');

        this.log('✅ Mobile Optimizations tests completed');
    }

    /**
     * Test accessibility features
     */
    async testAccessibility() {
        this.log('♿ Testing Accessibility...');

        // Test ARIA labels
        const ariaElements = document.querySelectorAll('[aria-label], [aria-labelledby], [role]');
        this.assert(ariaElements.length > 0, 'ARIA attributes are present');

        // Test semantic HTML
        const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6');
        this.assert(headings.length > 0, 'Semantic headings are present');

        // Test focus management
        const focusableElements = document.querySelectorAll('button, [tabindex], input, select, textarea, a[href]');
        this.assert(focusableElements.length > 0, 'Focusable elements are present');

        // Test alt text on images
        const images = document.querySelectorAll('img');
        let imagesWithAlt = 0;
        images.forEach(img => {
            if (img.hasAttribute('alt')) imagesWithAlt++;
        });

        this.assert(
            imagesWithAlt === images.length,
            `All images have alt text (${imagesWithAlt}/${images.length})`
        );

        this.log('✅ Accessibility tests completed');
    }

    /**
     * Test image loading and fallbacks
     */
    async testImageLoading() {
        this.log('🖼️ Testing Image Loading...');

        const images = document.querySelectorAll('.step-image');
        let loadedImages = 0;
        let imagePromises = [];

        images.forEach(img => {
            const promise = new Promise(resolve => {
                if (img.complete) {
                    loadedImages++;
                    resolve(true);
                } else {
                    img.onload = () => {
                        loadedImages++;
                        resolve(true);
                    };
                    img.onerror = () => resolve(false);
                }
            });
            imagePromises.push(promise);
        });

        await Promise.all(imagePromises);

        this.assert(
            loadedImages > 0 || images.length === 0,
            `Images loaded successfully (${loadedImages}/${images.length})`
        );

        // Test fallback styles
        const imageContainers = document.querySelectorAll('.card-image-container');
        imageContainers.forEach(container => {
            const style = window.getComputedStyle(container);
            this.assert(
                style.background && style.background !== 'none',
                'Image containers have fallback backgrounds'
            );
        });

        this.log('✅ Image Loading tests completed');
    }

    /**
     * Test responsive design
     */
    async testResponsiveDesign() {
        this.log('📐 Testing Responsive Design...');

        const container = document.querySelector('.container');
        if (container) {
            const style = window.getComputedStyle(container);
            this.assert(
                style.maxWidth || style.width,
                'Container has responsive width constraints'
            );
        }

        // Test CSS media queries presence
        const stylesheets = Array.from(document.styleSheets);
        let hasMediaQueries = false;

        try {
            stylesheets.forEach(sheet => {
                if (sheet.cssRules) {
                    Array.from(sheet.cssRules).forEach(rule => {
                        if (rule instanceof CSSMediaRule) {
                            hasMediaQueries = true;
                        }
                    });
                }
            });
        } catch (e) {
            // CORS issues with external stylesheets
            this.log('⚠️ Could not check all stylesheets due to CORS');
        }

        this.assert(hasMediaQueries, 'Media queries are present for responsive design');

        this.log('✅ Responsive Design tests completed');
    }

    /**
     * Test performance metrics
     */
    async testPerformance() {
        this.log('⚡ Testing Performance...');

        // Test animation performance
        if (window.performance && window.performance.now) {
            const startTime = performance.now();
            
            // Simulate some animations
            const cards = document.querySelectorAll('.step-card');
            if (cards.length > 0) {
                cards[0].style.transform = 'rotateY(180deg)';
                await this.wait(500);
                cards[0].style.transform = 'rotateY(0deg)';
            }
            
            const endTime = performance.now();
            const animationTime = endTime - startTime;
            
            this.assert(
                animationTime < 1000,
                `Animation performance is acceptable (${animationTime.toFixed(2)}ms)`
            );
        }

        // Test memory usage (basic check)
        if (window.performance && window.performance.memory) {
            const memory = window.performance.memory;
            this.log(`Memory usage: ${(memory.usedJSHeapSize / 1048576).toFixed(2)} MB`);
        }

        // Test frame rate monitoring
        if (this.journey && this.journey.performanceMonitor) {
            const fps = this.journey.performanceMonitor.getAverageFPS();
            this.assert(fps > 30, `Frame rate is acceptable (${fps} FPS)`);
        }

        this.log('✅ Performance tests completed');
    }

    /**
     * Test share functionality
     */
    async testShareFunctionality() {
        this.log('📤 Testing Share Functionality...');

        const shareButtons = document.querySelectorAll('.quick-btn[aria-label*="Partager"], .quick-btn[aria-label*="Share"]');
        
        this.assert(shareButtons.length > 0, 'Share buttons are present');

        if (shareButtons.length > 0) {
            // Test if Web Share API is supported or clipboard fallback exists
            const hasWebShare = 'share' in navigator;
            const hasClipboard = 'clipboard' in navigator;
            
            this.assert(
                hasWebShare || hasClipboard,
                'Browser supports sharing functionality'
            );

            // Simulate share button click (won't actually share in test)
            this.simulateClick(shareButtons[0]);
            await this.wait(100);
        }

        this.log('✅ Share Functionality tests completed');
    }

    /**
     * Test favorites system
     */
    async testFavoritesSystem() {
        this.log('❤️ Testing Favorites System...');

        const favoriteButtons = document.querySelectorAll('.quick-btn[aria-label*="Favoris"], .quick-btn[aria-label*="Favorite"]');
        
        this.assert(favoriteButtons.length > 0, 'Favorite buttons are present');

        if (favoriteButtons.length > 0) {
            const favoriteBtn = favoriteButtons[0];
            const initialState = favoriteBtn.classList.contains('active');
            
            // Test favorite toggle
            this.simulateClick(favoriteBtn);
            await this.wait(100);
            
            const newState = favoriteBtn.classList.contains('active');
            this.assert(
                newState !== initialState,
                'Favorite state toggles correctly'
            );
        }

        this.log('✅ Favorites System tests completed');
    }

    /**
     * Test analytics tracking
     */
    async testAnalyticsTracking() {
        this.log('📈 Testing Analytics Tracking...');

        // Test if analytics events are properly set up
        const trackableElements = document.querySelectorAll('[data-track], [data-event]');
        
        if (trackableElements.length > 0) {
            this.assert(true, `Analytics tracking elements found (${trackableElements.length})`);
        } else {
            this.log('ℹ️ No explicit analytics tracking elements found');
        }

        // Test console event logging (if analytics system logs to console)
        let analyticsEvents = 0;
        const originalLog = console.log;
        
        console.log = (...args) => {
            if (args.some(arg => typeof arg === 'string' && (arg.includes('track') || arg.includes('event')))) {
                analyticsEvents++;
            }
            originalLog.apply(console, args);
        };

        // Trigger some interactions to test analytics
        const buttons = document.querySelectorAll('button');
        if (buttons.length > 0) {
            this.simulateClick(buttons[0]);
            await this.wait(50);
        }

        console.log = originalLog;

        this.log('✅ Analytics Tracking tests completed');
    }

    /**
     * Utility methods for testing
     */
    simulateClick(element) {
        if (element) {
            const event = new MouseEvent('click', {
                bubbles: true,
                cancelable: true,
                view: window
            });
            element.dispatchEvent(event);
        }
    }

    simulateEvent(element, eventType) {
        if (element) {
            const event = new Event(eventType, { bubbles: true });
            element.dispatchEvent(event);
        }
    }

    simulateKeyPress(key) {
        const event = new KeyboardEvent('keydown', {
            key: key,
            bubbles: true
        });
        document.dispatchEvent(event);
    }

    wait(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    assert(condition, message) {
        if (condition) {
            this.pass(message);
        } else {
            this.fail(message);
        }
    }

    pass(message) {
        this.testResults.push({ status: 'PASS', message });
        this.passedTests++;
        console.log(`✅ ${message}`);
    }

    fail(message, error = '') {
        this.testResults.push({ status: 'FAIL', message, error });
        this.failedTests++;
        console.error(`❌ ${message}${error ? ': ' + error : ''}`);
    }

    log(message) {
        console.log(`📋 ${message}`);
    }

    /**
     * Display test results summary
     */
    displayResults() {
        console.log('\n🎯 TEST RESULTS SUMMARY');
        console.log('========================');
        console.log(`✅ Passed: ${this.passedTests}`);
        console.log(`❌ Failed: ${this.failedTests}`);
        console.log(`📊 Total: ${this.testResults.length}`);
        console.log(`📈 Success Rate: ${((this.passedTests / this.testResults.length) * 100).toFixed(1)}%`);

        if (this.failedTests > 0) {
            console.log('\n❌ FAILED TESTS:');
            this.testResults
                .filter(result => result.status === 'FAIL')
                .forEach(result => {
                    console.log(`   • ${result.message}${result.error ? ': ' + result.error : ''}`);
                });
        }

        // Create visual report
        this.createVisualReport();
    }

    /**
     * Create a visual test report in the DOM
     */
    createVisualReport() {
        const existingReport = document.getElementById('test-report');
        if (existingReport) {
            existingReport.remove();
        }

        const report = document.createElement('div');
        report.id = 'test-report';
        report.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            width: 400px;
            max-height: 600px;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 20px;
            border-radius: 10px;
            font-family: monospace;
            font-size: 12px;
            overflow-y: auto;
            z-index: 10000;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.2);
        `;

        const successRate = ((this.passedTests / this.testResults.length) * 100).toFixed(1);
        const statusColor = successRate >= 90 ? '#4CAF50' : successRate >= 70 ? '#FF9800' : '#F44336';

        report.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h3 style="margin: 0; color: #d4af37;">🧪 Test Report</h3>
                <button onclick="this.parentElement.parentElement.remove()" style="background: none; border: none; color: white; cursor: pointer; font-size: 18px;">×</button>
            </div>
            
            <div style="background: rgba(255, 255, 255, 0.1); padding: 10px; border-radius: 5px; margin-bottom: 15px;">
                <div style="color: ${statusColor}; font-weight: bold; margin-bottom: 5px;">
                    Success Rate: ${successRate}%
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: #4CAF50;">✅ ${this.passedTests} Passed</span>
                    <span style="color: #F44336;">❌ ${this.failedTests} Failed</span>
                </div>
            </div>

            <div style="max-height: 400px; overflow-y: auto;">
                ${this.testResults.map(result => `
                    <div style="margin-bottom: 8px; padding: 5px; background: rgba(255, 255, 255, 0.05); border-radius: 3px;">
                        <span style="color: ${result.status === 'PASS' ? '#4CAF50' : '#F44336'};">
                            ${result.status === 'PASS' ? '✅' : '❌'}
                        </span>
                        <span style="margin-left: 8px;">${result.message}</span>
                        ${result.error ? `<div style="color: #FF5722; font-size: 10px; margin-top: 3px; margin-left: 20px;">Error: ${result.error}</div>` : ''}
                    </div>
                `).join('')}
            </div>
        `;

        document.body.appendChild(report);

        // Auto-hide after 30 seconds if all tests pass
        if (this.failedTests === 0) {
            setTimeout(() => {
                if (report.parentElement) {
                    report.remove();
                }
            }, 30000);
        }
    }
}

// Global test runner function
window.runJourneyTests = async function() {
    const testSuite = new JourneyTestSuite();
    await testSuite.init();
    await testSuite.runAllTests();
    return testSuite;
};

// Auto-run tests when page loads (optional, can be commented out)
if (document.readyState === 'complete') {
    // Auto-run after a delay to let everything initialize
    setTimeout(() => {
        console.log('🔄 Auto-running journey tests...');
        window.runJourneyTests();
    }, 2000);
} else {
    window.addEventListener('load', () => {
        setTimeout(() => {
            console.log('🔄 Auto-running journey tests...');
            window.runJourneyTests();
        }, 2000);
    });
}

console.log('🧪 VinsDeLux Journey Test Suite loaded. Run tests manually with: runJourneyTests()');