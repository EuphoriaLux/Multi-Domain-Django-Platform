/**
 * Pixel War TypeScript Application Entry Point
 * 
 * This file serves as the bridge between the Django template and the TypeScript implementation.
 * It maintains compatibility with the existing template while providing the new TS functionality.
 */

import { PixelWarTS } from './core/pixel-war-ts.js';
import { PixelWarConfig } from './types/index.js';
import { quickMobileDiagnostic } from './utils/mobile-debug.js';

// Global interface for window object
declare global {
  interface Window {
    pixelWarTS: PixelWarTS | null;
    CANVAS_CONFIG: any;
    // Debug functions for development
    debugPixelWar: () => void;
    quickMobileDiagnostic: (pixelWar: PixelWarTS) => void;
    switchToTS: () => void;
    switchToJS: () => void;
  }
}

class PixelWarTSApp {
  private pixelWar: PixelWarTS | null = null;
  private isInitialized = false;

  constructor() {
    // Wait for DOM and config to be ready
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', this.init.bind(this));
    } else {
      this.init();
    }
  }

  private init(): void {
    try {
      // Get canvas element
      const canvas = document.getElementById('pixelCanvas') as HTMLCanvasElement;
      if (!canvas) {
        console.error('❌ Canvas element not found');
        return;
      }

      // Get config from Django template
      const config = this.parseConfig();
      if (!config) {
        console.error('❌ Failed to parse CANVAS_CONFIG');
        return;
      }

      // Initialize TypeScript implementation
      this.pixelWar = new PixelWarTS(canvas, config);
      
      // Setup UI integration
      this.setupUIIntegration();
      
      // Setup debugging tools
      this.setupDebugging();
      
      // Expose globally
      window.pixelWarTS = this.pixelWar;
      window.quickMobileDiagnostic = quickMobileDiagnostic;
      
      this.isInitialized = true;
      console.log('✅ PixelWarTS initialized successfully');

    } catch (error) {
      console.error('❌ Failed to initialize PixelWarTS:', error);
    }
  }

  private parseConfig(): PixelWarConfig | null {
    if (!window.CANVAS_CONFIG) {
      console.error('CANVAS_CONFIG not found on window');
      return null;
    }

    const config = window.CANVAS_CONFIG;

    return {
      canvasId: config.id || 1,
      gridWidth: config.gridWidth || config.width || 100,
      gridHeight: config.gridHeight || config.height || 100,
      pixelSize: config.pixelSize || 10,
      isAuthenticated: config.isAuthenticated || false,
      cooldownSeconds: config.isAuthenticated 
        ? config.registeredCooldown || 5 
        : config.anonymousCooldown || 30,
      pixelsPerMinute: config.isAuthenticated 
        ? config.registeredPixelsPerMinute || 5 
        : config.anonymousPixelsPerMinute || 2
    };
  }

  private setupUIIntegration(): void {
    if (!this.pixelWar) return;

    // Color selection integration
    document.querySelectorAll('.color-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const target = e.target as HTMLElement;
        const color = target.dataset.color;
        if (color && this.pixelWar) {
          this.pixelWar.setSelectedColor(color);
          this.highlightSelectedColor(target);
        }
      });
    });

    // Color picker integration
    const colorPicker = document.getElementById('colorPicker') as HTMLInputElement;
    if (colorPicker) {
      colorPicker.addEventListener('change', (e) => {
        const target = e.target as HTMLInputElement;
        if (this.pixelWar) {
          this.pixelWar.setSelectedColor(target.value);
        }
      });
    }

    // Zoom controls integration
    const zoomIn = document.getElementById('zoomIn');
    const zoomOut = document.getElementById('zoomOut');
    const zoomReset = document.getElementById('zoomReset');
    
    if (zoomIn) {
      zoomIn.addEventListener('click', () => {
        if (this.pixelWar) {
          this.pixelWar.adjustZoom(0.2);
        }
      });
    }
    
    if (zoomOut) {
      zoomOut.addEventListener('click', () => {
        if (this.pixelWar) {
          this.pixelWar.adjustZoom(-0.2);
        }
      });
    }
    
    if (zoomReset) {
      zoomReset.addEventListener('click', () => {
        if (this.pixelWar) {
          this.pixelWar.resetView();
        }
      });
    }

    // Mobile touch mode toggle
    this.setupTouchModeToggle();

    // Update zoom indicator
    this.setupZoomIndicator();
  }

  private setupTouchModeToggle(): void {
    if (!this.pixelWar) return;

    // Check if we should show touch mode controls
    const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || 
                     window.innerWidth < 768;

    if (!isMobile) return;

    // Find existing touch mode button or create one
    let touchModeBtn = document.getElementById('touchModeBtn') as HTMLButtonElement;
    
    if (!touchModeBtn) {
      // Create touch mode toggle
      const container = document.createElement('div');
      container.className = 'touch-mode-toggle-ts';
      container.innerHTML = `
        <button id="touchModeBtn" class="touch-mode-btn-ts">
          <span class="mode-icon">👆</span>
          <span class="mode-text">Tap Mode</span>
        </button>
      `;

      // Add to controls area
      const controlsArea = document.querySelector('.mobile-canvas-controls') || 
                          document.querySelector('.canvas-section') || 
                          document.body;
      controlsArea.appendChild(container);
      
      touchModeBtn = container.querySelector('#touchModeBtn') as HTMLButtonElement;
    }

    if (touchModeBtn) {
      touchModeBtn.addEventListener('click', () => {
        if (this.pixelWar) {
          const currentMode = this.pixelWar.getTouchMode();
          const newMode = currentMode === 'tap' ? 'precision' : 'tap';
          this.pixelWar.setTouchMode(newMode);
          this.updateTouchModeButton(newMode);
        }
      });

      // Set initial state
      this.updateTouchModeButton(this.pixelWar.getTouchMode());
    }
  }

  private updateTouchModeButton(mode: 'tap' | 'precision'): void {
    const btn = document.getElementById('touchModeBtn');
    if (!btn) return;

    const icon = btn.querySelector('.mode-icon');
    const text = btn.querySelector('.mode-text');

    if (mode === 'precision') {
      if (icon) icon.textContent = '🎯';
      if (text) text.textContent = 'Precision Mode';
      btn.className = 'touch-mode-btn-ts precision';
    } else {
      if (icon) icon.textContent = '👆';
      if (text) text.textContent = 'Tap Mode';
      btn.className = 'touch-mode-btn-ts tap';
    }
  }

  private setupZoomIndicator(): void {
    if (!this.pixelWar) return;

    const updateZoom = () => {
      const zoomIndicator = document.getElementById('zoomLevel');
      if (zoomIndicator && this.pixelWar) {
        const viewport = this.pixelWar.getViewportState();
        zoomIndicator.textContent = Math.round(viewport.zoom * 100) + '%';
      }
    };

    // Update zoom indicator periodically
    setInterval(updateZoom, 100);
    updateZoom();
  }

  private highlightSelectedColor(element: HTMLElement): void {
    // Remove previous selection
    document.querySelectorAll('.color-btn').forEach(btn => {
      btn.classList.remove('selected');
    });
    
    // Highlight new selection
    element.classList.add('selected');
  }

  private setupDebugging(): void {
    if (!this.pixelWar) return;

    // Global debug function
    window.debugPixelWar = () => {
      if (this.pixelWar) {
        const debugInfo = this.pixelWar.getDebugInfo();
        console.log('🔍 PixelWarTS Debug Info:', debugInfo);
        return debugInfo;
      }
    };

    // Enhanced mobile debugging
    const enableMobileDebugging = () => {
      if (this.pixelWar) {
        console.log('🔧 Mobile debugging enabled. Use:');
        console.log('  - quickMobileDiagnostic(window.pixelWarTS) for full diagnostic');
        console.log('  - Press M key to toggle mobile debug overlay');
        
        // Add keyboard shortcut for mobile debug
        document.addEventListener('keydown', (e) => {
          if (e.key === 'm' || e.key === 'M') {
            quickMobileDiagnostic(this.pixelWar!);
          }
        });
      }
    };

    // Enable mobile debugging on mobile devices
    const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth < 768;
    if (isMobile) {
      enableMobileDebugging();
    }

    // Function to switch between implementations
    window.switchToTS = () => {
      // Hide JS version, show TS version
      console.log('🔄 Switched to TypeScript implementation');
      // This could disable the old JS version if both were running
    };

    window.switchToJS = () => {
      console.log('🔄 Switching back to JavaScript implementation would require page reload');
      console.log('💡 Reload the page to use the original JS version');
    };

    // Performance monitoring
    setInterval(() => {
      if (this.pixelWar) {
        const metrics = this.pixelWar.getPerformanceMetrics();
        if (metrics.fps < 30) {
          console.warn('⚠️ Low FPS detected:', metrics.fps);
        }
      }
    }, 5000);
  }

  // Public API for external integration
  public getPixelWar(): PixelWarTS | null {
    return this.pixelWar;
  }

  public isReady(): boolean {
    return this.isInitialized && this.pixelWar !== null;
  }

  public destroy(): void {
    if (this.pixelWar) {
      this.pixelWar.destroy();
      this.pixelWar = null;
    }
    
    window.pixelWarTS = null;
    this.isInitialized = false;
  }
}

// Initialize the application
const app = new PixelWarTSApp();

// Export for use in other modules if needed
export default app;