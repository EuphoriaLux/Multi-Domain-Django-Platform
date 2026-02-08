/**
 * VinsDeLux Journey Debug Console
 * Provides real-time debugging tools for layout and interaction issues
 */

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

class DebugConsole {
    constructor() {
        this.isVisible = false;
        this.debugElement = null;
        this.highlightedElements = [];
        this.originalStyles = new Map();
        this.measurements = [];
        this.init();
    }

    init() {
        this.createDebugConsole();
        this.bindKeyboardShortcuts();
        this.setupElementInspection();
        console.log('🛠️ Debug Console initialized. Press Ctrl+Shift+D to toggle.');
    }

    createDebugConsole() {
        this.debugElement = document.createElement('div');
        this.debugElement.id = 'vdl-debug-console';
        this.debugElement.style.cssText = `
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            height: 300px;
            background: rgba(0, 0, 0, 0.95);
            color: #00ff00;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            border-top: 2px solid #00ff00;
            z-index: 999999;
            display: none;
            backdrop-filter: blur(10px);
        `;

        this.debugElement.innerHTML = `
            <div style="display: flex; height: 100%;">
                <!-- Control Panel -->
                <div style="flex: 0 0 250px; background: rgba(0, 0, 0, 0.8); border-right: 1px solid #333; padding: 10px; overflow-y: auto;">
                    <h3 style="margin: 0 0 15px 0; color: #d4af37; font-size: 14px;">🛠️ Debug Controls</h3>
                    
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #00ff00; margin: 0 0 8px 0; font-size: 12px;">Layout Tools</h4>
                        <button onclick="debugConsole.highlightContainers()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Highlight Containers</button>
                        <button onclick="debugConsole.showZIndex()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Show Z-Index</button>
                        <button onclick="debugConsole.measureElements()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Measure Elements</button>
                        <button onclick="debugConsole.checkOverlaps()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Check Overlaps</button>
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #00ff00; margin: 0 0 8px 0; font-size: 12px;">Journey Tools</h4>
                        <button onclick="debugConsole.inspectCards()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Inspect Cards</button>
                        <button onclick="debugConsole.testAnimations()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Test Animations</button>
                        <button onclick="debugConsole.checkImages()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Check Images</button>
                        <button onclick="debugConsole.validateStructure()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Validate Structure</button>
                    </div>
                    
                    <div style="margin-bottom: 15px;">
                        <h4 style="color: #00ff00; margin: 0 0 8px 0; font-size: 12px;">Performance</h4>
                        <button onclick="debugConsole.monitorFPS()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Monitor FPS</button>
                        <button onclick="debugConsole.memoryUsage()" style="width: 100%; margin: 2px 0; padding: 5px; background: #333; color: white; border: 1px solid #555; cursor: pointer;">Memory Usage</button>
                    </div>
                    
                    <div>
                        <button onclick="debugConsole.clearHighlights()" style="width: 100%; margin: 5px 0; padding: 8px; background: #d4af37; color: black; border: none; cursor: pointer; font-weight: bold;">Clear All</button>
                        <button onclick="debugConsole.exportReport()" style="width: 100%; margin: 2px 0; padding: 5px; background: #007bff; color: white; border: none; cursor: pointer;">Export Report</button>
                    </div>
                </div>
                
                <!-- Output Panel -->
                <div style="flex: 1; display: flex; flex-direction: column;">
                    <div style="background: rgba(0, 50, 0, 0.3); padding: 8px; border-bottom: 1px solid #333;">
                        <span style="color: #d4af37; font-weight: bold;">Debug Output</span>
                        <button onclick="debugConsole.clearOutput()" style="float: right; background: #333; color: white; border: 1px solid #555; padding: 2px 8px; cursor: pointer;">Clear</button>
                    </div>
                    <div id="debug-output" style="flex: 1; padding: 10px; overflow-y: auto; white-space: pre-wrap; font-size: 11px; line-height: 1.4;"></div>
                </div>
            </div>
        `;

        document.body.appendChild(this.debugElement);
    }

    bindKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            // Ctrl+Shift+D to toggle console
            if (e.ctrlKey && e.shiftKey && e.key === 'D') {
                e.preventDefault();
                this.toggle();
            }
            
            // Ctrl+Shift+H to highlight hovered element
            if (e.ctrlKey && e.shiftKey && e.key === 'H') {
                e.preventDefault();
                this.enableElementHighlighting();
            }
        });
    }

    setupElementInspection() {
        let inspectionMode = false;
        
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && e.key === 'I') {
                e.preventDefault();
                inspectionMode = !inspectionMode;
                this.log(`Element inspection mode: ${inspectionMode ? 'ON' : 'OFF'}`);
                document.body.style.cursor = inspectionMode ? 'crosshair' : '';
            }
        });

        document.addEventListener('click', (e) => {
            if (inspectionMode && e.ctrlKey && e.shiftKey) {
                e.preventDefault();
                e.stopPropagation();
                this.inspectElement(e.target);
            }
        });
    }

    toggle() {
        this.isVisible = !this.isVisible;
        this.debugElement.style.display = this.isVisible ? 'block' : 'none';
        
        if (this.isVisible) {
            this.log('🛠️ Debug Console activated');
            this.log('Available shortcuts:');
            this.log('  Ctrl+Shift+D: Toggle console');
            this.log('  Ctrl+Shift+I: Toggle element inspection');
            this.log('  Ctrl+Shift+H: Highlight element on hover');
        }
    }

    log(message, type = 'info') {
        const output = document.getElementById('debug-output');
        if (!output) return;

        const timestamp = new Date().toLocaleTimeString();
        const prefix = type === 'error' ? '❌' : type === 'warn' ? '⚠️' : type === 'success' ? '✅' : 'ℹ️';
        
        output.innerHTML += `[${escapeHtml(timestamp)}] ${prefix} ${escapeHtml(message)}\n`;
        output.scrollTop = output.scrollHeight;
    }

    clearOutput() {
        const output = document.getElementById('debug-output');
        if (output) {
            output.innerHTML = '';
        }
    }

    clearHighlights() {
        this.highlightedElements.forEach(element => {
            if (this.originalStyles.has(element)) {
                element.style.cssText = this.originalStyles.get(element);
            }
        });
        this.highlightedElements = [];
        this.originalStyles.clear();
        this.log('✅ All highlights cleared');
    }

    highlightElement(element, color = '#ff0000', label = '') {
        if (!element) return;

        this.originalStyles.set(element, element.style.cssText);
        element.style.outline = `2px solid ${color}`;
        element.style.outlineOffset = '2px';
        
        if (label) {
            const labelEl = document.createElement('div');
            labelEl.style.cssText = `
                position: absolute;
                background: ${color};
                color: white;
                padding: 2px 6px;
                font-size: 10px;
                font-weight: bold;
                z-index: 1000000;
                pointer-events: none;
            `;
            labelEl.textContent = label;
            
            const rect = element.getBoundingClientRect();
            labelEl.style.left = rect.left + 'px';
            labelEl.style.top = (rect.top - 20) + 'px';
            
            document.body.appendChild(labelEl);
            
            setTimeout(() => {
                if (labelEl.parentNode) {
                    labelEl.parentNode.removeChild(labelEl);
                }
            }, 3000);
        }
        
        this.highlightedElements.push(element);
    }

    highlightContainers() {
        const containers = document.querySelectorAll('.journey-steps-container, .step-card-container, .step-card, .card-face');
        containers.forEach((container, index) => {
            this.highlightElement(container, '#00ff00', `Container ${index + 1}`);
        });
        this.log(`✅ Highlighted ${containers.length} containers`);
    }

    showZIndex() {
        const elements = document.querySelectorAll('*');
        let zIndexElements = [];
        
        elements.forEach(el => {
            const style = window.getComputedStyle(el);
            const zIndex = style.zIndex;
            
            if (zIndex !== 'auto' && zIndex !== '0') {
                zIndexElements.push({ element: el, zIndex: parseInt(zIndex) });
                this.highlightElement(el, '#ff6600', `z:${zIndex}`);
            }
        });
        
        zIndexElements.sort((a, b) => b.zIndex - a.zIndex);
        this.log(`✅ Found ${zIndexElements.length} elements with z-index:`);
        zIndexElements.forEach(({ element, zIndex }) => {
            this.log(`  z-index ${zIndex}: ${element.tagName}.${element.className}`);
        });
    }

    measureElements() {
        const cards = document.querySelectorAll('.step-card');
        this.log(`📏 Measuring ${cards.length} cards:`);
        
        cards.forEach((card, index) => {
            const rect = card.getBoundingClientRect();
            const style = window.getComputedStyle(card);
            
            this.log(`  Card ${index + 1}:`);
            this.log(`    Position: ${rect.left.toFixed(1)}, ${rect.top.toFixed(1)}`);
            this.log(`    Size: ${rect.width.toFixed(1)} x ${rect.height.toFixed(1)}`);
            this.log(`    Z-index: ${style.zIndex}`);
            this.log(`    Transform: ${style.transform}`);
            
            this.highlightElement(card, '#ffff00', `#${index + 1}`);
        });
    }

    checkOverlaps() {
        const cards = document.querySelectorAll('.step-card');
        const overlaps = [];
        
        for (let i = 0; i < cards.length; i++) {
            for (let j = i + 1; j < cards.length; j++) {
                const rectA = cards[i].getBoundingClientRect();
                const rectB = cards[j].getBoundingClientRect();
                
                if (this.isOverlapping(rectA, rectB)) {
                    overlaps.push({ cardA: i + 1, cardB: j + 1, rectA, rectB });
                    this.highlightElement(cards[i], '#ff0000', `Overlap ${i + 1}`);
                    this.highlightElement(cards[j], '#ff0000', `Overlap ${j + 1}`);
                }
            }
        }
        
        if (overlaps.length === 0) {
            this.log('✅ No overlapping cards detected');
        } else {
            this.log(`❌ Found ${overlaps.length} overlapping pairs:`, 'error');
            overlaps.forEach(({ cardA, cardB }) => {
                this.log(`  Cards ${cardA} and ${cardB} overlap`, 'error');
            });
        }
    }

    inspectCards() {
        const cards = document.querySelectorAll('.step-card');
        this.log(`🎴 Inspecting ${cards.length} journey cards:`);
        
        cards.forEach((card, index) => {
            const rect = card.getBoundingClientRect();
            const style = window.getComputedStyle(card);
            const front = card.querySelector('.card-front');
            const back = card.querySelector('.card-back');
            const image = card.querySelector('.step-image');
            
            this.log(`  Card ${index + 1}:`);
            this.log(`    Visible: ${rect.width > 0 && rect.height > 0}`);
            this.log(`    Flipped: ${card.classList.contains('flipped')}`);
            this.log(`    Front face: ${front ? 'Present' : 'Missing'}`);
            this.log(`    Back face: ${back ? 'Present' : 'Missing'}`);
            
            if (image) {
                this.log(`    Image: ${image.src}`);
                this.log(`    Image loaded: ${image.complete && image.naturalWidth > 0}`);
            } else {
                this.log(`    Image: Missing`, 'warn');
            }
            
            this.highlightElement(card, '#00ffff', `Card ${index + 1}`);
        });
    }

    testAnimations() {
        this.log('🎭 Testing animations...');
        
        const cards = document.querySelectorAll('.step-card');
        let animationCount = 0;
        
        cards.forEach((card, index) => {
            const style = window.getComputedStyle(card);
            
            if (style.transition !== 'all 0s ease 0s' && style.transition !== 'none') {
                animationCount++;
                this.log(`  Card ${index + 1} has transitions: ${style.transition}`);
            }
            
            // Test flip animation
            setTimeout(() => {
                card.classList.add('flipped');
                this.log(`  Flipping card ${index + 1}`);
                
                setTimeout(() => {
                    card.classList.remove('flipped');
                    this.log(`  Resetting card ${index + 1}`);
                }, 1000);
            }, index * 200);
        });
        
        this.log(`✅ Found ${animationCount} animated cards`);
    }

    checkImages() {
        const images = document.querySelectorAll('.step-image');
        this.log(`🖼️ Checking ${images.length} images:`);
        
        images.forEach((img, index) => {
            const container = img.closest('.card-image-container');
            
            this.log(`  Image ${index + 1}:`);
            this.log(`    Source: ${img.src}`);
            this.log(`    Alt text: ${img.alt || 'Missing'}`);
            this.log(`    Loaded: ${img.complete && img.naturalWidth > 0}`);
            this.log(`    Dimensions: ${img.naturalWidth}x${img.naturalHeight}`);
            this.log(`    Display size: ${img.offsetWidth}x${img.offsetHeight}`);
            
            const containerRect = container ? container.getBoundingClientRect() : null;
            if (containerRect) {
                this.log(`    Container: ${containerRect.width.toFixed(1)}x${containerRect.height.toFixed(1)}`);
            }
            
            const color = img.complete && img.naturalWidth > 0 ? '#00ff00' : '#ff0000';
            this.highlightElement(img, color, `Img ${index + 1}`);
        });
    }

    validateStructure() {
        this.log('🏗️ Validating DOM structure...');
        
        const requiredElements = [
            '.futuristic-journey-section',
            '.journey-navigation',
            '.journey-steps-container',
            '.step-card',
            '.card-front',
            '.card-back'
        ];
        
        requiredElements.forEach(selector => {
            const elements = document.querySelectorAll(selector);
            if (elements.length === 0) {
                this.log(`❌ Missing: ${selector}`, 'error');
            } else {
                this.log(`✅ Found ${elements.length}x ${selector}`);
            }
        });
        
        // Check for proper nesting
        const cards = document.querySelectorAll('.step-card');
        cards.forEach((card, index) => {
            const front = card.querySelector('.card-front');
            const back = card.querySelector('.card-back');
            
            if (!front || !back) {
                this.log(`❌ Card ${index + 1} missing face elements`, 'error');
                this.highlightElement(card, '#ff0000', `Error ${index + 1}`);
            }
        });
    }

    monitorFPS() {
        this.log('⚡ Starting FPS monitoring...');
        
        let frameCount = 0;
        let startTime = performance.now();
        let lastTime = startTime;
        
        const monitorFrame = () => {
            frameCount++;
            const currentTime = performance.now();
            
            if (currentTime - startTime >= 1000) {
                const fps = Math.round((frameCount * 1000) / (currentTime - startTime));
                this.log(`FPS: ${fps}`);
                
                frameCount = 0;
                startTime = currentTime;
            }
            
            if (currentTime - lastTime < 5000) { // Monitor for 5 seconds
                requestAnimationFrame(monitorFrame);
            } else {
                this.log('✅ FPS monitoring completed');
            }
            
            lastTime = currentTime;
        };
        
        requestAnimationFrame(monitorFrame);
    }

    memoryUsage() {
        if (performance.memory) {
            const memory = performance.memory;
            this.log('💾 Memory Usage:');
            this.log(`  Used: ${(memory.usedJSHeapSize / 1048576).toFixed(2)} MB`);
            this.log(`  Total: ${(memory.totalJSHeapSize / 1048576).toFixed(2)} MB`);
            this.log(`  Limit: ${(memory.jsHeapSizeLimit / 1048576).toFixed(2)} MB`);
        } else {
            this.log('❌ Memory API not available', 'warn');
        }
    }

    inspectElement(element) {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        
        this.log(`🔍 Inspecting element: ${element.tagName}.${element.className}`);
        this.log(`  Position: ${rect.left.toFixed(1)}, ${rect.top.toFixed(1)}`);
        this.log(`  Size: ${rect.width.toFixed(1)} x ${rect.height.toFixed(1)}`);
        this.log(`  Z-index: ${style.zIndex}`);
        this.log(`  Display: ${style.display}`);
        this.log(`  Position: ${style.position}`);
        this.log(`  Overflow: ${style.overflow}`);
        
        this.highlightElement(element, '#ff00ff', 'Inspected');
    }

    exportReport() {
        const report = {
            timestamp: new Date().toISOString(),
            url: window.location.href,
            userAgent: navigator.userAgent,
            viewport: `${window.innerWidth}x${window.innerHeight}`,
            measurements: this.measurements,
            debugOutput: document.getElementById('debug-output')?.textContent || ''
        };
        
        const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        
        const a = document.createElement('a');
        a.href = url;
        a.download = `vdl-debug-report-${Date.now()}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        this.log('✅ Debug report exported');
    }

    isOverlapping(rect1, rect2) {
        return !(rect1.right < rect2.left || 
                rect2.right < rect1.left || 
                rect1.bottom < rect2.top || 
                rect2.bottom < rect1.top);
    }

    enableElementHighlighting() {
        this.log('✨ Element highlighting enabled (hover to inspect)');
        
        const handleMouseOver = (e) => {
            if (e.target !== this.debugElement && !this.debugElement.contains(e.target)) {
                this.highlightElement(e.target, '#00ffff', 'Hovered');
            }
        };
        
        const handleMouseOut = (e) => {
            // Remove highlight after a delay
            setTimeout(() => {
                const index = this.highlightedElements.indexOf(e.target);
                if (index > -1) {
                    if (this.originalStyles.has(e.target)) {
                        e.target.style.cssText = this.originalStyles.get(e.target);
                    }
                    this.highlightedElements.splice(index, 1);
                    this.originalStyles.delete(e.target);
                }
            }, 1000);
        };
        
        document.addEventListener('mouseover', handleMouseOver);
        document.addEventListener('mouseout', handleMouseOut);
        
        // Remove listeners after 10 seconds
        setTimeout(() => {
            document.removeEventListener('mouseover', handleMouseOver);
            document.removeEventListener('mouseout', handleMouseOut);
            this.log('✅ Element highlighting disabled');
        }, 10000);
    }
}

// Initialize debug console
const debugConsole = new DebugConsole();
window.debugConsole = debugConsole;

console.log('🛠️ VinsDeLux Debug Console loaded');
console.log('   Press Ctrl+Shift+D to toggle debug console');
console.log('   Press Ctrl+Shift+I to toggle element inspection');
console.log('   Press Ctrl+Shift+H to enable element highlighting');