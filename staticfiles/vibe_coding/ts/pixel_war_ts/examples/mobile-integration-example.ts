/**
 * Mobile Integration Example for Enhanced Pixel War Navigation
 * 
 * This example demonstrates how to use the comprehensive mobile navigation fixes
 * and debug tools to create a reliable mobile experience.
 */

import { PixelWarTS } from '../core/pixel-war-ts.js';
import { MobileDebugger, quickMobileDiagnostic } from '../utils/mobile-debug.js';
import { PixelWarConfig } from '../types/index.js';

/**
 * Enhanced Mobile Pixel War Implementation
 */
export class MobilePixelWarApp {
  private pixelWar: PixelWarTS | null = null;
  private debugger: MobileDebugger | null = null;
  private debugOverlay: HTMLElement | null = null;

  constructor(
    private readonly canvasId: string,
    private readonly config: PixelWarConfig
  ) {}

  /**
   * Initialize the mobile-optimized Pixel War application
   */
  async initialize(): Promise<void> {
    const canvas = document.getElementById(this.canvasId) as HTMLCanvasElement;
    if (!canvas) {
      throw new Error(`Canvas with id '${this.canvasId}' not found`);
    }

    // Ensure canvas is properly sized for mobile
    this.setupMobileCanvas(canvas);

    try {
      // Initialize the main application
      this.pixelWar = new PixelWarTS(canvas, this.config);
      
      // Initialize the debugger
      this.debugger = new MobileDebugger(
        this.pixelWar,
        this.pixelWar['logger']
      );

      // Setup mobile-specific event handlers
      this.setupMobileEventHandlers();

      // Run initial diagnostics
      await this.runInitialDiagnostics();

      console.log('✅ Mobile Pixel War initialized successfully');
      
    } catch (error) {
      console.error('❌ Failed to initialize Mobile Pixel War:', error);
      throw error;
    }
  }

  /**
   * Setup canvas for optimal mobile performance
   */
  private setupMobileCanvas(canvas: HTMLCanvasElement): void {
    // Set canvas size based on device characteristics
    const isMobile = window.innerWidth <= 768 || 'ontouchstart' in window;
    const devicePixelRatio = window.devicePixelRatio || 1;
    
    // Calculate optimal canvas size
    let canvasWidth = window.innerWidth;
    let canvasHeight = window.innerHeight;
    
    // Account for mobile browser chrome
    if ('visualViewport' in window && window.visualViewport) {
      canvasWidth = Math.min(window.visualViewport.width, canvasWidth);
      canvasHeight = Math.min(window.visualViewport.height, canvasHeight);
    }
    
    // Set display size (CSS pixels)
    canvas.style.width = `${canvasWidth}px`;
    canvas.style.height = `${canvasHeight}px`;
    
    // Set actual size considering device pixel ratio
    // But cap it to prevent memory issues on high-DPI mobile devices
    const maxDPR = isMobile ? 2 : devicePixelRatio;
    canvas.width = canvasWidth * Math.min(devicePixelRatio, maxDPR);
    canvas.height = canvasHeight * Math.min(devicePixelRatio, maxDPR);
    
    // Ensure canvas is positioned correctly
    canvas.style.position = 'fixed';
    canvas.style.top = '0';
    canvas.style.left = '0';
    canvas.style.touchAction = 'none'; // Prevent default touch behaviors
    
    console.log(`📱 Canvas configured for mobile:`, {
      displaySize: { width: canvasWidth, height: canvasHeight },
      actualSize: { width: canvas.width, height: canvas.height },
      devicePixelRatio,
      effectiveDPR: Math.min(devicePixelRatio, maxDPR),
      isMobile
    });
  }

  /**
   * Setup mobile-specific event handlers
   */
  private setupMobileEventHandlers(): void {
    if (!this.pixelWar) return;

    // Handle orientation changes
    const handleOrientationChange = () => {
      setTimeout(() => {
        if (this.pixelWar) {
          this.pixelWar.refreshMobileViewport();
          console.log('🔄 Orientation changed, viewport refreshed');
        }
      }, 300); // Wait for browser to settle
    };

    // Handle Visual Viewport changes (mobile browser chrome)
    const handleVisualViewportChange = () => {
      if (this.pixelWar) {
        this.pixelWar.refreshMobileViewport();
      }
    };

    // Setup event listeners
    window.addEventListener('orientationchange', handleOrientationChange);
    
    if ('visualViewport' in window && window.visualViewport) {
      window.visualViewport.addEventListener('resize', handleVisualViewportChange);
      window.visualViewport.addEventListener('scroll', handleVisualViewportChange);
    }

    // Handle window resize with debouncing
    let resizeTimeout: number;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimeout);
      resizeTimeout = window.setTimeout(() => {
        if (this.pixelWar) {
          this.pixelWar.refreshMobileViewport();
        }
      }, 150);
    });

    // Handle page visibility changes (mobile app switching)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && this.pixelWar) {
        // Refresh when returning to the app
        setTimeout(() => {
          if (this.pixelWar) {
            this.pixelWar.refreshMobileViewport();
          }
        }, 100);
      }
    });

    console.log('📱 Mobile event handlers configured');
  }

  /**
   * Run initial diagnostics to ensure everything is working
   */
  private async runInitialDiagnostics(): Promise<void> {
    if (!this.debugger || !this.pixelWar) return;

    const diagnostics = await this.debugger.runFullDiagnostics();
    
    console.group('🔍 Initial Mobile Diagnostics');
    console.log(diagnostics.summary);
    
    if (diagnostics.criticalIssues.length > 0) {
      console.warn('Critical issues detected:', diagnostics.criticalIssues);
    }
    
    if (diagnostics.recommendations.length > 0) {
      console.info('Recommendations:', diagnostics.recommendations);
    }
    
    console.groupEnd();

    // Show debug overlay in development
    if (this.isDebugMode()) {
      this.enableDebugMode();
    }
  }

  /**
   * Enable debug mode with visual overlay
   */
  enableDebugMode(): void {
    if (!this.debugger || !this.pixelWar) return;

    const canvas = document.getElementById(this.canvasId) as HTMLCanvasElement;
    this.debugOverlay = this.debugger.createDebugOverlay(canvas);

    // Add debug controls
    this.createDebugControls();
    
    console.log('🛠️ Debug mode enabled');
  }

  /**
   * Disable debug mode
   */
  disableDebugMode(): void {
    if (this.debugOverlay) {
      this.debugOverlay.remove();
      this.debugOverlay = null;
    }

    const debugControls = document.getElementById('mobile-debug-controls');
    if (debugControls) {
      debugControls.remove();
    }

    console.log('🛠️ Debug mode disabled');
  }

  /**
   * Create debug controls for testing
   */
  private createDebugControls(): void {
    const controls = document.createElement('div');
    controls.id = 'mobile-debug-controls';
    controls.style.cssText = `
      position: fixed;
      bottom: 10px;
      left: 10px;
      background: rgba(0, 0, 0, 0.8);
      color: white;
      padding: 10px;
      border-radius: 5px;
      font-family: Arial, sans-serif;
      font-size: 14px;
      z-index: 10001;
    `;

    controls.innerHTML = `
      <div><strong>Debug Controls</strong></div>
      <button id="test-coordinates" style="margin: 2px; padding: 5px;">Test Coordinates</button>
      <button id="test-diagnostics" style="margin: 2px; padding: 5px;">Run Diagnostics</button>
      <button id="reset-view" style="margin: 2px; padding: 5px;">Reset View</button>
      <button id="fit-grid" style="margin: 2px; padding: 5px;">Fit Grid</button>
    `;

    // Add event listeners
    controls.querySelector('#test-coordinates')?.addEventListener('click', () => {
      this.testCoordinates();
    });

    controls.querySelector('#test-diagnostics')?.addEventListener('click', () => {
      this.debugger?.logDiagnostics();
    });

    controls.querySelector('#reset-view')?.addEventListener('click', () => {
      this.pixelWar?.resetView();
    });

    controls.querySelector('#fit-grid')?.addEventListener('click', () => {
      this.pixelWar?.fitToGrid();
    });

    document.body.appendChild(controls);
  }

  /**
   * Test coordinate accuracy with visual feedback
   */
  private testCoordinates(): void {
    if (!this.pixelWar) return;

    const canvas = document.getElementById(this.canvasId) as HTMLCanvasElement;
    const rect = canvas.getBoundingClientRect();
    
    // Test various points
    const testPoints = [
      { x: rect.left + 50, y: rect.top + 50, label: 'Top-Left' },
      { x: rect.left + rect.width - 50, y: rect.top + 50, label: 'Top-Right' },
      { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, label: 'Center' },
      { x: rect.left + 50, y: rect.top + rect.height - 50, label: 'Bottom-Left' },
      { x: rect.left + rect.width - 50, y: rect.top + rect.height - 50, label: 'Bottom-Right' }
    ];

    console.group('🎯 Coordinate Test Results');
    
    testPoints.forEach(point => {
      const debugInfo = this.pixelWar!['coordinateTransform'].getDebugInfo(
        { x: point.x, y: point.y },
        this.pixelWar!.getViewportState()
      );
      
      console.log(`${point.label}:`, debugInfo);
    });
    
    console.groupEnd();

    alert(`Coordinate test completed. Check console for detailed results.`);
  }

  /**
   * Check if debug mode should be enabled
   */
  private isDebugMode(): boolean {
    return (
      window.location.search.includes('debug=true') ||
      localStorage.getItem('pixelwar-debug') === 'true' ||
      window.location.hostname === 'localhost'
    );
  }

  /**
   * Get mobile diagnostics
   */
  getMobileDiagnostics(): any {
    return this.pixelWar?.getMobileDiagnostics();
  }

  /**
   * Force refresh of mobile viewport
   */
  refreshViewport(): void {
    this.pixelWar?.refreshMobileViewport();
  }

  /**
   * Cleanup resources
   */
  destroy(): void {
    this.disableDebugMode();
    this.pixelWar?.destroy();
    this.pixelWar = null;
    this.debugger = null;
  }
}

/**
 * Usage example
 */
export function initializeMobilePixelWar(canvasId: string, config: PixelWarConfig): MobilePixelWarApp {
  const app = new MobilePixelWarApp(canvasId, config);
  
  app.initialize().catch(error => {
    console.error('Failed to initialize mobile Pixel War:', error);
    
    // Show user-friendly error message
    const errorDiv = document.createElement('div');
    errorDiv.style.cssText = `
      position: fixed;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      background: #ff4444;
      color: white;
      padding: 20px;
      border-radius: 10px;
      text-align: center;
      font-family: Arial, sans-serif;
      z-index: 10002;
    `;
    errorDiv.innerHTML = `
      <h3>Initialization Failed</h3>
      <p>There was a problem starting the mobile Pixel War application.</p>
      <p>Please refresh the page and try again.</p>
      <button onclick="this.parentElement.remove()" style="
        background: white;
        color: #ff4444;
        border: none;
        padding: 10px 20px;
        border-radius: 5px;
        cursor: pointer;
        margin-top: 10px;
      ">Close</button>
    `;
    
    document.body.appendChild(errorDiv);
  });
  
  return app;
}

// Export utility function for console debugging
declare global {
  interface Window {
    quickMobileDiagnostic: typeof quickMobileDiagnostic;
  }
}

window.quickMobileDiagnostic = quickMobileDiagnostic;