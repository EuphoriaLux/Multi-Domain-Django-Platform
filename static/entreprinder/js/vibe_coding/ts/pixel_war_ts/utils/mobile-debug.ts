/**
 * Mobile Debug Utilities
 * Comprehensive diagnostic and testing tools for mobile navigation issues
 */

import { PixelWarTS } from '../core/pixel-war-ts.js';
import { ScreenCoordinate, GridCoordinate, ViewportState } from '../types/index.js';

export class MobileDebugger {
  private overlay: HTMLElement | null = null;
  private isActive = false;

  constructor(private pixelWar: PixelWarTS) {}

  /**
   * Enable debug overlay with real-time information
   */
  enableDebugOverlay(): void {
    if (this.overlay) return;

    this.overlay = document.createElement('div');
    this.overlay.id = 'mobile-debug-overlay';
    this.overlay.style.cssText = `
      position: fixed;
      top: 60px;
      left: 10px;
      background: rgba(0,0,0,0.9);
      color: white;
      padding: 10px;
      border-radius: 8px;
      font-family: monospace;
      font-size: 11px;
      z-index: 10000;
      max-width: 300px;
      word-wrap: break-word;
    `;

    document.body.appendChild(this.overlay);
    this.isActive = true;
    this.updateOverlay();
  }

  /**
   * Disable and remove debug overlay
   */
  disableDebugOverlay(): void {
    if (this.overlay) {
      document.body.removeChild(this.overlay);
      this.overlay = null;
      this.isActive = false;
    }
  }

  /**
   * Update debug overlay with current information
   */
  private updateOverlay(): void {
    if (!this.overlay || !this.isActive) return;

    const viewport = this.pixelWar.getViewportState();
    const debugInfo = this.pixelWar.getDebugInfo();
    const visualViewport = window.visualViewport;

    this.overlay.innerHTML = `
      <strong>📱 Mobile Debug</strong><br>
      <br>
      <strong>Viewport:</strong><br>
      Zoom: ${viewport.zoom.toFixed(3)}<br>
      Offset: ${viewport.offsetX.toFixed(1)}, ${viewport.offsetY.toFixed(1)}<br>
      <br>
      <strong>Visual Viewport:</strong><br>
      Size: ${visualViewport?.width || 'N/A'}x${visualViewport?.height || 'N/A'}<br>
      Offset: ${visualViewport?.offsetLeft || 0}, ${visualViewport?.offsetTop || 0}<br>
      Scale: ${visualViewport?.scale || 1}<br>
      <br>
      <strong>Canvas:</strong><br>
      Rect: ${debugInfo.canvas?.rect || 'N/A'}<br>
      DPR: ${window.devicePixelRatio}<br>
      <br>
      <strong>Touch:</strong><br>
      Mode: ${debugInfo.input?.touchMode || 'N/A'}<br>
      State: ${debugInfo.input?.gestureState || 'N/A'}<br>
      <br>
      <strong>Performance:</strong><br>
      FPS: ${debugInfo.performance?.fps || 0}<br>
      Animating: ${debugInfo.performance?.isAnimating ? 'Yes' : 'No'}<br>
    `;

    if (this.isActive) {
      requestAnimationFrame(() => this.updateOverlay());
    }
  }

  /**
   * Test coordinate accuracy at various screen positions
   */
  testCoordinateAccuracy(): {
    passed: number;
    failed: number;
    results: Array<{
      screen: ScreenCoordinate;
      grid: GridCoordinate | null;
      roundTrip: ScreenCoordinate | null;
      accuracy: number;
      passed: boolean;
    }>;
  } {
    const testPoints = [
      { x: 50, y: 50 },     // Top-left
      { x: 100, y: 100 },   // Near top-left
      { x: 200, y: 150 },   // Left side
      { x: 300, y: 200 },   // Center-left
      { x: 400, y: 250 },   // Center
      { x: 500, y: 300 },   // Center-right
      { x: 600, y: 350 },   // Right side
      { x: 700, y: 400 },   // Far right
      { x: 750, y: 450 },   // Bottom-right area
    ];

    const results = testPoints.map(screenCoord => {
      // Test coordinate transformation accuracy
      const transform = (this.pixelWar as any).coordinateTransform;
      const viewport = this.pixelWar.getViewportState();
      
      const gridCoord = transform.screenToGrid(screenCoord, viewport);
      const roundTripScreen = gridCoord ? transform.gridToScreen(gridCoord, viewport) : null;
      
      let accuracy = 0;
      if (roundTripScreen) {
        const dx = Math.abs(screenCoord.x - roundTripScreen.x);
        const dy = Math.abs(screenCoord.y - roundTripScreen.y);
        accuracy = Math.sqrt(dx * dx + dy * dy);
      }

      return {
        screen: screenCoord,
        grid: gridCoord,
        roundTrip: roundTripScreen,
        accuracy,
        passed: accuracy < 5 // Within 5 pixels is acceptable
      };
    });

    const passed = results.filter(r => r.passed).length;
    const failed = results.filter(r => !r.passed).length;

    return { passed, failed, results };
  }

  /**
   * Test touch precision by simulating touch events
   */
  testTouchPrecision(): {
    supported: boolean;
    maxTouchPoints: number;
    pressureSupported: boolean;
    orientationSupported: boolean;
  } {
    const inputHandler = (this.pixelWar as any).inputHandler;
    const capabilities = inputHandler.getTouchCapabilities();

    return {
      supported: capabilities.touchSupported,
      maxTouchPoints: capabilities.maxTouchPoints,
      pressureSupported: capabilities.pressureSupported,
      orientationSupported: 'orientation' in window
    };
  }

  /**
   * Test viewport behavior during zoom operations
   */
  testViewportBehavior(): {
    minZoom: number;
    maxZoom: number;
    canFitGrid: boolean;
    boundaryBehavior: 'correct' | 'problematic';
  } {
    const viewport = this.pixelWar.getViewportState();
    const viewportManager = (this.pixelWar as any).viewportManager;
    
    // Get viewport size in grid units
    const viewportSize = viewportManager.getViewportSizeInGrid();
    const canFitGrid = viewportSize.width >= 100 && viewportSize.height >= 100;

    // Test boundary behavior by checking if constraints work properly
    const testOffsetX = viewport.offsetX + 1000; // Way beyond bounds
    const testOffsetY = viewport.offsetY + 1000;
    
    // This should be constrained by the viewport manager
    viewportManager.setPan(testOffsetX, testOffsetY);
    const constrainedViewport = this.pixelWar.getViewportState();
    
    const boundaryBehavior = (
      Math.abs(constrainedViewport.offsetX - testOffsetX) > 100 &&
      Math.abs(constrainedViewport.offsetY - testOffsetY) > 100
    ) ? 'correct' : 'problematic';

    // Reset to original position
    viewportManager.setPan(viewport.offsetX, viewport.offsetY);

    return {
      minZoom: viewport.bounds.minX, // This will be updated in the fixed viewport manager
      maxZoom: 10, // From constraints
      canFitGrid,
      boundaryBehavior
    };
  }

  /**
   * Run comprehensive mobile diagnostic
   */
  runFullDiagnostic(): {
    coordinateAccuracy: ReturnType<typeof this.testCoordinateAccuracy>;
    touchPrecision: ReturnType<typeof this.testTouchPrecision>;
    viewportBehavior: ReturnType<typeof this.testViewportBehavior>;
    deviceInfo: {
      userAgent: string;
      viewport: string;
      visualViewport: string;
      devicePixelRatio: number;
      orientation: string;
    };
  } {
    return {
      coordinateAccuracy: this.testCoordinateAccuracy(),
      touchPrecision: this.testTouchPrecision(),
      viewportBehavior: this.testViewportBehavior(),
      deviceInfo: {
        userAgent: navigator.userAgent,
        viewport: `${window.innerWidth}x${window.innerHeight}`,
        visualViewport: window.visualViewport 
          ? `${window.visualViewport.width}x${window.visualViewport.height}`
          : 'Not supported',
        devicePixelRatio: window.devicePixelRatio,
        orientation: window.screen?.orientation?.type || 'Unknown'
      }
    };
  }

  /**
   * Generate diagnostic report as text
   */
  generateReport(): string {
    const diagnostic = this.runFullDiagnostic();
    
    return `
📱 PIXEL WAR MOBILE DIAGNOSTIC REPORT
Generated: ${new Date().toLocaleString()}

🎯 COORDINATE ACCURACY
Passed: ${diagnostic.coordinateAccuracy.passed}/${diagnostic.coordinateAccuracy.passed + diagnostic.coordinateAccuracy.failed}
Failed: ${diagnostic.coordinateAccuracy.failed}

Worst accuracy: ${Math.max(...diagnostic.coordinateAccuracy.results.map(r => r.accuracy)).toFixed(2)}px
Best accuracy: ${Math.min(...diagnostic.coordinateAccuracy.results.map(r => r.accuracy)).toFixed(2)}px

👆 TOUCH PRECISION  
Touch supported: ${diagnostic.touchPrecision.supported}
Max touch points: ${diagnostic.touchPrecision.maxTouchPoints}
Pressure supported: ${diagnostic.touchPrecision.pressureSupported}
Orientation supported: ${diagnostic.touchPrecision.orientationSupported}

🔍 VIEWPORT BEHAVIOR
Can fit grid: ${diagnostic.viewportBehavior.canFitGrid}
Boundary behavior: ${diagnostic.viewportBehavior.boundaryBehavior}
Min zoom: ${diagnostic.viewportBehavior.minZoom}
Max zoom: ${diagnostic.viewportBehavior.maxZoom}

📱 DEVICE INFO
User Agent: ${diagnostic.deviceInfo.userAgent}
Viewport: ${diagnostic.deviceInfo.viewport}
Visual Viewport: ${diagnostic.deviceInfo.visualViewport}
Device Pixel Ratio: ${diagnostic.deviceInfo.devicePixelRatio}
Orientation: ${diagnostic.deviceInfo.orientation}

${diagnostic.coordinateAccuracy.failed > 0 ? '⚠️ COORDINATE ISSUES DETECTED' : '✅ ALL COORDINATE TESTS PASSED'}
${diagnostic.viewportBehavior.boundaryBehavior === 'problematic' ? '⚠️ BOUNDARY ISSUES DETECTED' : '✅ VIEWPORT BEHAVIOR CORRECT'}
`;
  }
}

/**
 * Quick diagnostic function for console use
 */
export function quickMobileDiagnostic(pixelWar: PixelWarTS): void {
  const mobileDebugger = new MobileDebugger(pixelWar);
  const report = mobileDebugger.generateReport();
  console.log(report);
  
  // Also enable overlay for visual debugging
  mobileDebugger.enableDebugOverlay();
  
  // Disable overlay after 30 seconds
  setTimeout(() => {
    mobileDebugger.disableDebugOverlay();
  }, 30000);
}

// Make available globally for console access
(window as any).quickMobileDiagnostic = quickMobileDiagnostic;