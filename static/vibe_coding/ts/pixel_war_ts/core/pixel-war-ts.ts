/**
 * Pixel War TypeScript Implementation
 * 
 * Main application class that coordinates all subsystems.
 * Designed for reliable mobile navigation and clean architecture.
 */

import {
  PixelWarConfig,
  ViewportConstraints,
  ViewportState,
  GridCoordinate,
  ScreenCoordinate,
  TouchGesture,
  TouchMode,
  Logger
} from '../types/index.js';

import { MobileCoordinateTransform } from './coordinate-transform-mobile.js';
import { ViewportManager } from './viewport-manager.js';
import { MobileInputHandler } from '../input/mobile-input-handler.js';
import { ConsoleLogger } from '../utils/logger.js';

export class PixelWarTS {
  private readonly logger: Logger;
  private readonly coordinateTransform: MobileCoordinateTransform;
  private readonly viewportManager: ViewportManager;
  private readonly inputHandler: MobileInputHandler;

  // Rendering
  private readonly ctx: CanvasRenderingContext2D;
  private animationId: number | null = null;
  private pixelData = new Map<string, string>(); // "x,y" -> color

  // State
  private selectedColor = '#000000';
  private isDestroyed = false;

  // Performance tracking
  private frameCount = 0;
  private lastFPSTime = Date.now();
  private currentFPS = 0;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly config: PixelWarConfig
  ) {
    // Initialize logger
    this.logger = new ConsoleLogger('PixelWarTS', 'info');

    // Validate canvas
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      throw new Error('Failed to get 2D context from canvas');
    }
    this.ctx = ctx;

    // Create constraints
    const constraints: ViewportConstraints = {
      minZoom: 0.1,
      maxZoom: 10,
      gridWidth: config.gridWidth,
      gridHeight: config.gridHeight,
      pixelSize: config.pixelSize
    };

    // Initialize subsystems
    this.coordinateTransform = new MobileCoordinateTransform(canvas, constraints, this.logger);
    this.viewportManager = new ViewportManager(canvas, constraints, this.logger);
    this.inputHandler = new MobileInputHandler(canvas, this.logger);

    // Setup event listeners
    this.setupEventHandlers();

    // Start rendering loop
    this.startRenderLoop();

    this.logger.info('PixelWarTS initialized', {
      config,
      constraints,
      canvasSize: `${canvas.width}x${canvas.height}`
    });
  }

  // ===== PUBLIC API =====

  /**
   * Set selected color for pixel placement
   */
  setSelectedColor(color: string): void {
    this.selectedColor = color;
    this.logger.debug('Color selected', { color });
  }

  /**
   * Get current selected color
   */
  getSelectedColor(): string {
    return this.selectedColor;
  }

  /**
   * Set touch interaction mode
   */
  setTouchMode(mode: TouchMode): void {
    this.inputHandler.setTouchMode(mode);
  }

  /**
   * Get current touch mode
   */
  getTouchMode(): TouchMode {
    return this.inputHandler.getTouchMode();
  }

  /**
   * Set zoom level
   */
  setZoom(zoom: number): void {
    this.viewportManager.setZoom(zoom);
  }

  /**
   * Apply zoom delta
   */
  adjustZoom(delta: number, focalPoint?: ScreenCoordinate): void {
    this.viewportManager.applyZoomDelta(delta, focalPoint);
  }

  /**
   * Pan to specific offset
   */
  setPan(offsetX: number, offsetY: number): void {
    this.viewportManager.setPan(offsetX, offsetY);
  }

  /**
   * Apply pan delta
   */
  applyPanDelta(deltaX: number, deltaY: number): void {
    this.viewportManager.applyPanDelta(deltaX, deltaY);
  }

  /**
   * Navigate to specific grid coordinates
   */
  navigateToGrid(x: number, y: number): void {
    this.viewportManager.navigateToGrid(x, y);
  }

  /**
   * Reset to initial view
   */
  resetView(): void {
    this.viewportManager.reset();
  }

  /**
   * Fit entire grid in viewport
   */
  fitToGrid(): void {
    this.viewportManager.fitToGrid();
  }

  /**
   * Place pixel at grid coordinates
   */
  async placePixel(x: number, y: number, color?: string): Promise<void> {
    const pixelColor = color || this.selectedColor;
    const key = `${x},${y}`;
    
    // Update local state immediately
    this.pixelData.set(key, pixelColor);
    
    // TODO: Integrate with actual API
    this.logger.info('Pixel placed', { x, y, color: pixelColor });
    
    // Render the change
    this.render();
  }

  /**
   * Update pixel data from server
   */
  updatePixelData(pixels: Array<{ x: number; y: number; color: string }>): void {
    for (const pixel of pixels) {
      const key = `${pixel.x},${pixel.y}`;
      this.pixelData.set(key, pixel.color);
    }
    this.render();
  }

  /**
   * Get current viewport state
   */
  getViewportState(): ViewportState {
    return this.viewportManager.getCurrentState();
  }

  /**
   * Get performance metrics
   */
  getPerformanceMetrics(): {
    fps: number;
    frameCount: number;
    isAnimating: boolean;
  } {
    return {
      fps: this.currentFPS,
      frameCount: this.frameCount,
      isAnimating: this.viewportManager.isAnimating()
    };
  }

  /**
   * Validate screen coordinates for mobile edge cases
   */
  private validateScreenCoordinate(coord: ScreenCoordinate): ScreenCoordinate | null {
    // Check for invalid values
    if (!isFinite(coord.x) || !isFinite(coord.y)) {
      this.logger.warn('Non-finite coordinates detected', coord);
      return null;
    }
    
    // Check for negative coordinates (can happen on some mobile browsers)
    if (coord.x < 0 || coord.y < 0) {
      this.logger.warn('Negative coordinates detected', coord);
      // Attempt to correct
      return {
        x: Math.max(0, coord.x),
        y: Math.max(0, coord.y)
      };
    }
    
    // Check for suspiciously large coordinates
    const maxX = window.screen.width * 2;
    const maxY = window.screen.height * 2;
    
    if (coord.x > maxX || coord.y > maxY) {
      this.logger.warn('Suspiciously large coordinates', coord);
      return {
        x: Math.min(coord.x, maxX),
        y: Math.min(coord.y, maxY)
      };
    }
    
    return coord;
  }
  
  /**
   * Test coordinate transformation accuracy
   */
  testCoordinateAccuracy(testPoints: ScreenCoordinate[]): {
    results: Array<{
      input: ScreenCoordinate;
      grid: GridCoordinate | null;
      roundTrip: ScreenCoordinate | null;
      accurate: boolean;
      error?: string;
    }>;
    summary: {
      total: number;
      successful: number;
      accurate: number;
      averageError: number;
    };
  } {
    const results = [];
    let totalError = 0;
    let successful = 0;
    let accurate = 0;
    
    const viewport = this.viewportManager.getCurrentState();
    
    for (const point of testPoints) {
      try {
        const gridCoord = this.coordinateTransform.screenToGrid(point, viewport);
        let roundTripCoord = null;
        let isAccurate = false;
        
        if (gridCoord) {
          successful++;
          roundTripCoord = this.coordinateTransform.gridToScreen(gridCoord, viewport);
          
          if (roundTripCoord) {
            const errorX = Math.abs(point.x - roundTripCoord.x);
            const errorY = Math.abs(point.y - roundTripCoord.y);
            const error = Math.sqrt(errorX * errorX + errorY * errorY);
            
            totalError += error;
            isAccurate = error < 2; // 2 pixel tolerance
            
            if (isAccurate) accurate++;
          }
        }
        
        results.push({
          input: point,
          grid: gridCoord,
          roundTrip: roundTripCoord,
          accurate: isAccurate
        });
      } catch (error) {
        results.push({
          input: point,
          grid: null,
          roundTrip: null,
          accurate: false,
          error: error instanceof Error ? error.message : 'Unknown error'
        });
      }
    }
    
    return {
      results,
      summary: {
        total: testPoints.length,
        successful,
        accurate,
        averageError: successful > 0 ? totalError / successful : 0
      }
    };
  }
  
  /**
   * Get comprehensive debug information for mobile troubleshooting
   */
  getDebugInfo(): any {
    return {
      viewport: this.viewportManager.getDebugInfo(),
      input: this.inputHandler.getDebugInfo(),
      coordinate: this.coordinateTransform.getMobileViewportInfo(),
      performance: this.getPerformanceMetrics(),
      pixelData: {
        count: this.pixelData.size,
        selectedColor: this.selectedColor,
        samplePixels: Array.from(this.pixelData.entries()).slice(0, 5)
      },
      mobile: {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        touchSupported: 'ontouchstart' in window,
        visualViewportSupported: 'visualViewport' in window,
        devicePixelRatio: window.devicePixelRatio || 1
      },
      canvas: {
        width: this.canvas.width,
        height: this.canvas.height,
        clientWidth: this.canvas.clientWidth,
        clientHeight: this.canvas.clientHeight,
        offsetWidth: this.canvas.offsetWidth,
        offsetHeight: this.canvas.offsetHeight
      },
      config: this.config
    };
  }

  /**
   * Force refresh of mobile viewport calculations
   * Useful after orientation changes or browser chrome changes
   */
  refreshMobileViewport(): void {
    this.coordinateTransform.forceRectUpdate();
    this.viewportManager.handleViewportChange?.();
    this.render();
    
    this.logger.info('Mobile viewport refreshed', {
      viewport: this.viewportManager.getCurrentState(),
      mobileInfo: this.coordinateTransform.getMobileViewportInfo()
    });
  }
  
  /**
   * Get mobile-specific diagnostic information
   */
  getMobileDiagnostics(): {
    coordinateSystem: any;
    viewport: any;
    input: any;
    recommendations: string[];
  } {
    const recommendations: string[] = [];
    const viewport = this.viewportManager.getDebugInfo();
    const input = this.inputHandler.getDebugInfo();
    const coordinate = this.coordinateTransform.getMobileViewportInfo();
    
    // Analyze potential issues
    if (viewport.mobile?.devicePixelRatio > 2) {
      recommendations.push('High DPI display detected - ensure coordinate precision');
    }
    
    if (!input.mobile?.visualViewportSupport) {
      recommendations.push('Visual Viewport API not supported - coordinate accuracy may be reduced');
    }
    
    if (viewport.mobile?.windowSize.innerWidth !== viewport.mobile?.windowSize.outerWidth) {
      recommendations.push('Browser chrome detected - may affect coordinate calculations');
    }
    
    if (input.capabilities?.maxTouchPoints === 0) {
      recommendations.push('Touch not supported - using mouse events for testing');
    }
    
    return {
      coordinateSystem: coordinate,
      viewport: viewport.mobile,
      input: input.mobile,
      recommendations
    };
  }

  /**
   * Cleanup and destroy instance
   */
  destroy(): void {
    if (this.isDestroyed) {
      return;
    }

    this.isDestroyed = true;
    
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
      this.animationId = null;
    }

    this.coordinateTransform.destroy();
    this.viewportManager.destroy();
    this.inputHandler.destroy();

    this.logger.info('PixelWarTS destroyed');
  }

  // ===== PRIVATE METHODS =====

  private setupEventHandlers(): void {
    // Input events
    this.inputHandler.addEventListener('input', (e: CustomEvent) => {
      this.handleInputGesture(e.detail.gesture);
    });

    // Viewport events
    this.viewportManager.addEventListener('viewport_change', () => {
      // Viewport changed, render will be called automatically by animation loop
      // or we can force a render if not animating
      if (!this.viewportManager.isAnimating()) {
        this.render();
      }
    });

    // Window events
    window.addEventListener('beforeunload', () => {
      this.destroy();
    });
  }

  private handleInputGesture(gesture: TouchGesture): void {
    this.logger.debug('Processing gesture', {
      type: gesture.type,
      center: gesture.center,
      duration: gesture.duration
    });

    switch (gesture.type) {
      case 'tap':
        this.handleTapGesture(gesture);
        break;
        
      case 'pan':
        this.handlePanGesture(gesture);
        break;
        
      case 'pinch':
        this.handlePinchGesture(gesture);
        break;
        
      case 'long_press':
        this.handleLongPressGesture(gesture);
        break;
    }
  }

  private handleTapGesture(gesture: TouchGesture): void {
    const screenCoord: ScreenCoordinate = {
      x: gesture.center.x,
      y: gesture.center.y
    };
    
    // Validate screen coordinates before processing
    const validatedCoord = this.validateScreenCoordinate(screenCoord);
    if (!validatedCoord) {
      this.logger.warn('Invalid tap coordinates', screenCoord);
      return;
    }

    const gridCoord = this.coordinateTransform.screenToGrid(
      validatedCoord,
      this.viewportManager.getCurrentState()
    );

    if (!gridCoord) {
      this.logger.debug('Tap outside grid bounds', {
        screenCoord: validatedCoord,
        viewport: this.viewportManager.getCurrentState()
      });
      return;
    }
    
    this.logger.debug('Valid tap detected', {
      screenCoord: validatedCoord,
      gridCoord,
      touchMode: this.inputHandler.getTouchMode()
    });

    if (this.inputHandler.getTouchMode() === 'tap') {
      // Direct placement
      this.placePixel(gridCoord.x, gridCoord.y);
    } else {
      // Precision mode - show confirmation
      this.showPixelConfirmation(gridCoord);
    }
  }

  private handlePanGesture(gesture: TouchGesture): void {
    const deltaX = gesture.data.deltaX || 0;
    const deltaY = gesture.data.deltaY || 0;

    // Convert screen deltas to grid deltas with improved precision
    const viewport = this.viewportManager.getCurrentState();
    
    // Apply device pixel ratio correction for high-DPI displays
    const devicePixelRatio = window.devicePixelRatio || 1;
    const scaledDeltaX = deltaX / devicePixelRatio;
    const scaledDeltaY = deltaY / devicePixelRatio;
    
    // Convert to grid coordinates (negative because pan direction is opposite to viewport movement)
    const gridDeltaX = -scaledDeltaX / (this.config.pixelSize * viewport.zoom);
    const gridDeltaY = -scaledDeltaY / (this.config.pixelSize * viewport.zoom);
    
    // Apply smoothing for very small movements to reduce jitter
    const threshold = 0.001;
    if (Math.abs(gridDeltaX) > threshold || Math.abs(gridDeltaY) > threshold) {
      this.viewportManager.applyPanDelta(gridDeltaX, gridDeltaY);
      
      this.logger.debug('Pan applied', {
        screenDelta: { x: deltaX, y: deltaY },
        scaledDelta: { x: scaledDeltaX, y: scaledDeltaY },
        gridDelta: { x: gridDeltaX, y: gridDeltaY },
        zoom: viewport.zoom,
        pixelSize: this.config.pixelSize
      });
    }
  }

  private handlePinchGesture(gesture: TouchGesture): void {
    const scale = gesture.data.scale || 1;
    const centerPoint = gesture.data.scaleCenter;

    if (centerPoint && isFinite(scale) && scale > 0) {
      const viewport = this.viewportManager.getCurrentState();
      
      // Calculate zoom delta with improved precision and bounds checking
      const currentZoom = viewport.zoom;
      let targetZoom = currentZoom * scale;
      
      // Clamp to reasonable bounds to prevent extreme zoom levels
      const constraints = {
        minZoom: 0.05,
        maxZoom: 50
      };
      targetZoom = Math.max(constraints.minZoom, Math.min(constraints.maxZoom, targetZoom));
      
      const zoomDelta = targetZoom - currentZoom;
      
      // Only apply zoom if the delta is significant
      const zoomThreshold = 0.01;
      if (Math.abs(zoomDelta) > zoomThreshold) {
        // Validate center point coordinates
        const validatedCenter = this.validateScreenCoordinate(centerPoint);
        if (validatedCenter) {
          this.viewportManager.applyZoomDelta(zoomDelta, validatedCenter);
          
          this.logger.debug('Zoom applied', {
            scale,
            currentZoom,
            targetZoom,
            zoomDelta,
            centerPoint: validatedCenter
          });
        }
      }
    }
  }

  private handleLongPressGesture(gesture: TouchGesture): void {
    // Could be used for context menu or special actions
    this.logger.info('Long press detected', { center: gesture.center });
  }

  private showPixelConfirmation(gridCoord: GridCoordinate): void {
    // Create a simple confirmation dialog
    // In a real implementation, this would integrate with the existing UI
    const shouldPlace = confirm(`Place ${this.selectedColor} pixel at (${gridCoord.x}, ${gridCoord.y})?`);
    
    if (shouldPlace) {
      this.placePixel(gridCoord.x, gridCoord.y);
    }
  }

  private startRenderLoop(): void {
    if (this.isDestroyed) {
      return;
    }

    const render = () => {
      if (this.isDestroyed) {
        return;
      }

      this.render();
      this.updatePerformanceMetrics();
      
      this.animationId = requestAnimationFrame(render);
    };

    this.animationId = requestAnimationFrame(render);
  }

  private render(): void {
    const viewport = this.viewportManager.getCurrentState();
    
    // Clear canvas
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    // Get visible area
    const visibleArea = this.coordinateTransform.getVisibleGridArea(viewport);
    
    // Render pixels
    this.renderPixels(viewport, visibleArea);
    
    // Render grid (if zoomed in enough)
    if (viewport.zoom > 2) {
      this.renderGrid(viewport, visibleArea);
    }

    this.frameCount++;
  }

  private renderPixels(viewport: ViewportState, visibleArea: any): void {
    const pixelSize = this.config.pixelSize * viewport.zoom;
    
    // Only render visible pixels for performance
    for (const [key, color] of this.pixelData.entries()) {
      const [xStr, yStr] = key.split(',');
      const x = parseInt(xStr, 10);
      const y = parseInt(yStr, 10);
      
      // Skip if not in visible area
      if (
        x < visibleArea.topLeft.x - 1 ||
        x > visibleArea.bottomRight.x + 1 ||
        y < visibleArea.topLeft.y - 1 ||
        y > visibleArea.bottomRight.y + 1
      ) {
        continue;
      }

      // Calculate screen position
      const screenX = (x - viewport.offsetX) * pixelSize;
      const screenY = (y - viewport.offsetY) * pixelSize;

      // Render pixel
      this.ctx.fillStyle = color;
      this.ctx.fillRect(screenX, screenY, pixelSize, pixelSize);
    }
  }

  private renderGrid(viewport: ViewportState, visibleArea: any): void {
    const pixelSize = this.config.pixelSize * viewport.zoom;
    
    this.ctx.strokeStyle = 'rgba(0, 0, 0, 0.1)';
    this.ctx.lineWidth = 1;
    
    this.ctx.beginPath();
    
    // Vertical lines
    for (let x = visibleArea.topLeft.x; x <= visibleArea.bottomRight.x; x++) {
      const screenX = (x - viewport.offsetX) * pixelSize;
      this.ctx.moveTo(screenX, 0);
      this.ctx.lineTo(screenX, this.canvas.height);
    }
    
    // Horizontal lines
    for (let y = visibleArea.topLeft.y; y <= visibleArea.bottomRight.y; y++) {
      const screenY = (y - viewport.offsetY) * pixelSize;
      this.ctx.moveTo(0, screenY);
      this.ctx.lineTo(this.canvas.width, screenY);
    }
    
    this.ctx.stroke();
  }

  private updatePerformanceMetrics(): void {
    const now = Date.now();
    const deltaTime = now - this.lastFPSTime;
    
    if (deltaTime >= 1000) { // Update every second
      this.currentFPS = Math.round((this.frameCount * 1000) / deltaTime);
      this.frameCount = 0;
      this.lastFPSTime = now;
    }
  }
}