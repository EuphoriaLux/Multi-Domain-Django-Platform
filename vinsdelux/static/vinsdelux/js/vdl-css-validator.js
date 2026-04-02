/**
 * VinsDeLux Journey CSS Validator
 * Validates CSS properties, layout issues, and visual consistency
 */

class CSSValidator {
    constructor() {
        this.issues = [];
        this.warnings = [];
        this.validations = 0;
    }

    /**
     * Run all CSS validations
     */
    async validateAll() {
        console.log('🎨 Running CSS Validation Suite...');
        
        this.validateCardOverlapping();
        this.validateZIndexHierarchy();
        this.validateImageSizing();
        this.validateResponsiveBreakpoints();
        this.validateColorContrast();
        this.validateAnimationPerformance();
        this.validateLayoutStability();
        this.validateAccessibilityColors();
        
        this.displayResults();
        return this.issues.length === 0;
    }

    /**
     * Check for card overlapping issues
     */
    validateCardOverlapping() {
        console.log('🔍 Validating card overlapping...');
        
        const cards = document.querySelectorAll('.step-card');
        const cardBounds = [];
        
        cards.forEach((card, index) => {
            const rect = card.getBoundingClientRect();
            const style = window.getComputedStyle(card);
            const zIndex = parseInt(style.zIndex) || 0;
            
            cardBounds.push({
                index,
                rect,
                zIndex,
                element: card
            });
        });

        // Check for overlapping with same z-index
        for (let i = 0; i < cardBounds.length; i++) {
            for (let j = i + 1; j < cardBounds.length; j++) {
                const cardA = cardBounds[i];
                const cardB = cardBounds[j];
                
                if (this.isOverlapping(cardA.rect, cardB.rect)) {
                    if (cardA.zIndex === cardB.zIndex) {
                        this.addIssue(
                            'Card Overlapping',
                            `Cards ${i + 1} and ${j + 1} overlap with same z-index (${cardA.zIndex})`
                        );
                    } else {
                        this.addWarning(
                            'Intentional Overlap',
                            `Cards ${i + 1} and ${j + 1} overlap with different z-index`
                        );
                    }
                }
            }
        }

        this.validations++;
    }

    /**
     * Validate z-index hierarchy
     */
    validateZIndexHierarchy() {
        console.log('📏 Validating z-index hierarchy...');
        
        const elements = document.querySelectorAll('*');
        const zIndexMap = new Map();
        
        elements.forEach(el => {
            const style = window.getComputedStyle(el);
            const zIndex = style.zIndex;
            
            if (zIndex !== 'auto' && zIndex !== '0') {
                const value = parseInt(zIndex);
                if (!zIndexMap.has(value)) {
                    zIndexMap.set(value, []);
                }
                zIndexMap.get(value).push(el);
            }
        });

        // Check for reasonable z-index values
        zIndexMap.forEach((elements, zIndex) => {
            if (zIndex > 9999) {
                this.addWarning(
                    'High Z-Index',
                    `Z-index value ${zIndex} is unusually high (${elements.length} elements)`
                );
            }
            
            if (elements.length > 10) {
                this.addWarning(
                    'Z-Index Conflict',
                    `Too many elements (${elements.length}) share z-index ${zIndex}`
                );
            }
        });

        this.validations++;
    }

    /**
     * Validate image sizing and aspect ratios
     */
    validateImageSizing() {
        console.log('🖼️ Validating image sizing...');
        
        const images = document.querySelectorAll('.step-image');
        const imageContainers = document.querySelectorAll('.card-image-container');
        
        images.forEach((img, index) => {
            const rect = img.getBoundingClientRect();
            const style = window.getComputedStyle(img);
            
            // Check for proper sizing
            if (rect.width === 0 || rect.height === 0) {
                this.addIssue(
                    'Image Size',
                    `Image ${index + 1} has zero dimensions`
                );
            }
            
            // Check object-fit property
            if (style.objectFit === 'initial' || !style.objectFit) {
                this.addWarning(
                    'Image Fit',
                    `Image ${index + 1} may not scale properly without object-fit`
                );
            }
        });

        imageContainers.forEach((container, index) => {
            const rect = container.getBoundingClientRect();
            const style = window.getComputedStyle(container);
            
            if (rect.height < 100) {
                this.addWarning(
                    'Container Height',
                    `Image container ${index + 1} is very short (${rect.height}px)`
                );
            }
        });

        this.validations++;
    }

    /**
     * Validate responsive breakpoints
     */
    validateResponsiveBreakpoints() {
        console.log('📱 Validating responsive breakpoints...');
        
        const viewportWidth = window.innerWidth;
        const breakpoints = [576, 768, 992, 1200];
        const currentBreakpoint = breakpoints.find(bp => viewportWidth <= bp) || 'xl';
        
        // Test critical elements at current breakpoint
        const criticalElements = [
            '.futuristic-journey-section',
            '.step-card',
            '.journey-navigation',
            '.card-image-container'
        ];

        criticalElements.forEach(selector => {
            const element = document.querySelector(selector);
            if (element) {
                const rect = element.getBoundingClientRect();
                const style = window.getComputedStyle(element);
                
                // Check for overflow
                if (rect.width > viewportWidth) {
                    this.addIssue(
                        'Responsive Overflow',
                        `${selector} overflows viewport at ${viewportWidth}px width`
                    );
                }
                
                // Check for minimum touch targets on mobile
                if (viewportWidth <= 768 && element.tagName === 'BUTTON') {
                    if (rect.width < 44 || rect.height < 44) {
                        this.addIssue(
                            'Touch Target Size',
                            `${selector} is too small for mobile (${rect.width}x${rect.height}px)`
                        );
                    }
                }
            }
        });

        this.validations++;
    }

    /**
     * Validate color contrast ratios
     */
    validateColorContrast() {
        console.log('🌈 Validating color contrast...');
        
        const textElements = document.querySelectorAll('h1, h2, h3, h4, h5, h6, p, span, button, .btn-text, .stat-label');
        
        textElements.forEach((element, index) => {
            const style = window.getComputedStyle(element);
            const color = this.parseColor(style.color);
            const backgroundColor = this.parseColor(style.backgroundColor);
            
            if (color && backgroundColor) {
                const contrast = this.calculateContrast(color, backgroundColor);
                
                if (contrast < 4.5) {
                    this.addWarning(
                        'Color Contrast',
                        `Text element ${index + 1} has low contrast ratio (${contrast.toFixed(2)}:1)`
                    );
                }
            }
        });

        this.validations++;
    }

    /**
     * Validate animation performance
     */
    validateAnimationPerformance() {
        console.log('⚡ Validating animation performance...');
        
        const animatedElements = document.querySelectorAll('[style*="transition"], [style*="animation"], .step-card, .progress-ring');
        
        animatedElements.forEach((element, index) => {
            const style = window.getComputedStyle(element);
            
            // Check for GPU-accelerated properties
            const transform = style.transform;
            const willChange = style.willChange;
            
            if (transform === 'none' && willChange === 'auto') {
                this.addWarning(
                    'Animation Performance',
                    `Element ${index + 1} may benefit from transform or will-change optimization`
                );
            }
            
            // Check for expensive properties in animations
            const transition = style.transition;
            if (transition.includes('height') || transition.includes('width')) {
                this.addWarning(
                    'Expensive Animation',
                    `Element ${index + 1} animates layout properties (height/width)`
                );
            }
        });

        this.validations++;
    }

    /**
     * Validate layout stability
     */
    validateLayoutStability() {
        console.log('📐 Validating layout stability...');
        
        const containers = document.querySelectorAll('.journey-steps-container, .step-card-container');
        
        containers.forEach((container, index) => {
            const style = window.getComputedStyle(container);
            
            // Check for explicit dimensions
            if (style.height === 'auto' && style.minHeight === '0px') {
                this.addWarning(
                    'Layout Stability',
                    `Container ${index + 1} has no height constraints (may cause layout shift)`
                );
            }
            
            // Check for position stability
            if (style.position === 'absolute' && (style.top === 'auto' || style.left === 'auto')) {
                this.addWarning(
                    'Position Stability',
                    `Absolutely positioned element ${index + 1} has auto positioning`
                );
            }
        });

        this.validations++;
    }

    /**
     * Validate accessibility-related colors
     */
    validateAccessibilityColors() {
        console.log('♿ Validating accessibility colors...');
        
        const focusableElements = document.querySelectorAll('button, [tabindex], input, select, textarea, a[href]');
        
        focusableElements.forEach((element, index) => {
            const style = window.getComputedStyle(element);
            
            // Check for focus indicators
            const outlineStyle = style.outline;
            const outlineWidth = style.outlineWidth;
            
            if (outlineStyle === 'none' || outlineWidth === '0px') {
                // Check if there's a custom focus style
                element.focus();
                const focusStyle = window.getComputedStyle(element);
                element.blur();
                
                if (focusStyle.boxShadow === 'none' && focusStyle.border === style.border) {
                    this.addWarning(
                        'Focus Indicator',
                        `Focusable element ${index + 1} has no visible focus indicator`
                    );
                }
            }
        });

        this.validations++;
    }

    /**
     * Utility methods
     */
    isOverlapping(rect1, rect2) {
        return !(rect1.right < rect2.left || 
                rect2.right < rect1.left || 
                rect1.bottom < rect2.top || 
                rect2.bottom < rect1.top);
    }

    parseColor(colorStr) {
        if (!colorStr || colorStr === 'rgba(0, 0, 0, 0)' || colorStr === 'transparent') {
            return null;
        }
        
        const rgb = colorStr.match(/\d+/g);
        if (rgb) {
            return {
                r: parseInt(rgb[0]),
                g: parseInt(rgb[1]),
                b: parseInt(rgb[2])
            };
        }
        return null;
    }

    calculateContrast(color1, color2) {
        const l1 = this.getLuminance(color1);
        const l2 = this.getLuminance(color2);
        
        return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    }

    getLuminance(color) {
        const rsRGB = color.r / 255;
        const gsRGB = color.g / 255;
        const bsRGB = color.b / 255;

        const r = rsRGB <= 0.03928 ? rsRGB / 12.92 : Math.pow((rsRGB + 0.055) / 1.055, 2.4);
        const g = gsRGB <= 0.03928 ? gsRGB / 12.92 : Math.pow((gsRGB + 0.055) / 1.055, 2.4);
        const b = bsRGB <= 0.03928 ? bsRGB / 12.92 : Math.pow((bsRGB + 0.055) / 1.055, 2.4);

        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }

    addIssue(category, description) {
        this.issues.push({ category, description, severity: 'error' });
        console.error(`❌ ${category}: ${description}`);
    }

    addWarning(category, description) {
        this.warnings.push({ category, description, severity: 'warning' });
        console.warn(`⚠️ ${category}: ${description}`);
    }

    /**
     * Display validation results
     */
    displayResults() {
        console.log('\n🎨 CSS VALIDATION RESULTS');
        console.log('==========================');
        console.log(`🔍 Validations Run: ${this.validations}`);
        console.log(`❌ Issues Found: ${this.issues.length}`);
        console.log(`⚠️ Warnings: ${this.warnings.length}`);
        
        if (this.issues.length === 0) {
            console.log('✅ No critical CSS issues found!');
        } else {
            console.log('\n❌ CRITICAL ISSUES:');
            this.issues.forEach(issue => {
                console.log(`   • ${issue.category}: ${issue.description}`);
            });
        }
        
        if (this.warnings.length > 0) {
            console.log('\n⚠️ WARNINGS:');
            this.warnings.forEach(warning => {
                console.log(`   • ${warning.category}: ${warning.description}`);
            });
        }

        this.createVisualReport();
    }

    /**
     * Create visual CSS validation report
     */
    createVisualReport() {
        const existingReport = document.getElementById('css-validation-report');
        if (existingReport) {
            existingReport.remove();
        }

        const report = document.createElement('div');
        report.id = 'css-validation-report';
        report.style.cssText = `
            position: fixed;
            top: 20px;
            left: 20px;
            width: 400px;
            max-height: 600px;
            background: rgba(20, 20, 40, 0.95);
            color: white;
            padding: 20px;
            border-radius: 10px;
            font-family: monospace;
            font-size: 12px;
            overflow-y: auto;
            z-index: 10001;
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.2);
        `;

        const issueCount = this.issues.length;
        const warningCount = this.warnings.length;
        const statusColor = issueCount === 0 ? '#4CAF50' : '#F44336';

        report.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h3 style="margin: 0; color: #d4af37;">🎨 CSS Validation</h3>
                <button onclick="this.parentElement.parentElement.remove()" style="background: none; border: none; color: white; cursor: pointer; font-size: 18px;">×</button>
            </div>
            
            <div style="background: rgba(255, 255, 255, 0.1); padding: 10px; border-radius: 5px; margin-bottom: 15px;">
                <div style="color: ${statusColor}; font-weight: bold; margin-bottom: 5px;">
                    Status: ${issueCount === 0 ? 'PASSED' : 'ISSUES FOUND'}
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span style="color: #F44336;">❌ ${issueCount} Issues</span>
                    <span style="color: #FF9800;">⚠️ ${warningCount} Warnings</span>
                </div>
            </div>

            ${issueCount > 0 ? `
                <div style="margin-bottom: 15px;">
                    <h4 style="color: #F44336; margin-bottom: 10px;">❌ Critical Issues:</h4>
                    ${this.issues.map(issue => `
                        <div style="margin-bottom: 8px; padding: 5px; background: rgba(244, 67, 54, 0.1); border-radius: 3px; border-left: 3px solid #F44336;">
                            <div style="font-weight: bold; color: #F44336;">${issue.category}</div>
                            <div style="font-size: 11px; margin-top: 2px;">${issue.description}</div>
                        </div>
                    `).join('')}
                </div>
            ` : ''}

            ${warningCount > 0 ? `
                <div>
                    <h4 style="color: #FF9800; margin-bottom: 10px;">⚠️ Warnings:</h4>
                    <div style="max-height: 300px; overflow-y: auto;">
                        ${this.warnings.map(warning => `
                            <div style="margin-bottom: 8px; padding: 5px; background: rgba(255, 152, 0, 0.1); border-radius: 3px; border-left: 3px solid #FF9800;">
                                <div style="font-weight: bold; color: #FF9800;">${warning.category}</div>
                                <div style="font-size: 11px; margin-top: 2px;">${warning.description}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            ` : ''}
        `;

        document.body.appendChild(report);

        // Auto-hide after 45 seconds if no issues
        if (issueCount === 0) {
            setTimeout(() => {
                if (report.parentElement) {
                    report.remove();
                }
            }, 45000);
        }
    }
}

// Global CSS validator function
window.validateCSS = async function() {
    const validator = new CSSValidator();
    return await validator.validateAll();
};

console.log('🎨 CSS Validator loaded. Run validation with: validateCSS()');