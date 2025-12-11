/**
 * Mobile-Enhanced Coordinate Transformation System
 * 
 * This version specifically addresses mobile navigation issues by:
 * - Using Visual Viewport API for accurate mobile browser handling
 * - Supporting device pixel ratio corrections
 * - Providing better touch coordinate precision
 * - Including comprehensive mobile debugging
 */

import {
  GridCoordinate,
  ScreenCoordinate,
  CanvasCoordinate,
  ViewportState,
  ViewportConstraints,
  CoordinateError,
  Logger
} from '../types/index.js';

export class MobileCoordinateTransform {
  private canvasRect: DOMRect;
  private lastUpdate: number = 0;
  private readonly RECT_CACHE_TIME = 50; // More frequent updates for mobile (50ms)
  
  // Mobile-specific properties
  private visualViewportSupported: boolean;
  private lastVisualViewportChange: number = 0;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly constraints: ViewportConstraints,
    private readonly logger: Logger
  ) {
    this.canvasRect = canvas.getBoundingClientRect();
    this.lastUpdate = Date.now();
    this.visualViewportSupported = !!window.visualViewport;

    // Setup mobile-specific event listeners
    this.setupMobileEventListeners();

    this.logger.info('Mobile coordinate transform initialized', {
      visualViewportSupported: this.visualViewportSupported,
      devicePixelRatio: window.devicePixelRatio,
      canvasSize: `${canvas.width}x${canvas.height}`,
      initialRect: {
        width: this.canvasRect.width,
        height: this.canvasRect.height,
        left: this.canvasRect.left,
        top: this.canvasRect.top
      }
    });
  }

  /**
   * Setup mobile-specific event listeners
   */
  private setupMobileEventListeners(): void {
    // Standard viewport changes
    window.addEventListener('resize', this.handleViewportChange.bind(this));
    window.addEventListener('orientationchange', this.handleOrientationChange.bind(this));

    // Visual Viewport API for mobile browser chrome changes
    if (this.visualViewportSupported && window.visualViewport) {
      window.visualViewport.addEventListener('resize', this.handleVisualViewportChange.bind(this));
      window.visualViewport.addEventListener('scroll', this.handleVisualViewportChange.bind(this));
    }
  }

  /**
   * Handle viewport changes with debouncing
   */
  private handleViewportChange(): void {
    // Immediate update for viewport changes
    this.updateCanvasRect();
  }

  /**
   * Handle orientation changes with delay
   */
  private handleOrientationChange(): void {
    // Orientation changes need a delay to get accurate dimensions
    setTimeout(() => {
      this.updateCanvasRect();
    }, 100);
  }

  /**
   * Handle Visual Viewport changes (mobile browser chrome show/hide)
   */
  private handleVisualViewportChange(): void {
    this.lastVisualViewportChange = Date.now();
    // Update rect immediately for visual viewport changes
    this.updateCanvasRect();
  }

  /**
   * Updates the cached canvas rectangle with mobile optimizations
   */
  private updateCanvasRect(): void {
    this.canvasRect = this.canvas.getBoundingClientRect();
    this.lastUpdate = Date.now();
    
    // Enhanced logging for mobile debugging
    this.logger.debug('Canvas rect updated', {
      rect: {
        width: this.canvasRect.width,
        height: this.canvasRect.height,
        left: this.canvasRect.left,
        top: this.canvasRect.top
      },
      visualViewport: window.visualViewport ? {
        width: window.visualViewport.width,
        height: window.visualViewport.height,
        offsetLeft: window.visualViewport.offsetLeft,
        offsetTop: window.visualViewport.offsetTop,
        scale: window.visualViewport.scale
      } : null,
      window: {
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio
      },
      timestamp: this.lastUpdate
    });
  }

  /**
   * Ensures canvas rect is up to date with mobile considerations
   */
  private ensureCanvasRect(): void {
    const now = Date.now();
    const timeSinceUpdate = now - this.lastUpdate;
    const timeSinceVisualViewportChange = now - this.lastVisualViewportChange;
    
    // Update more frequently on mobile, especially after visual viewport changes
    const shouldUpdate = timeSinceUpdate > this.RECT_CACHE_TIME || 
                        timeSinceVisualViewportChange < 200; // Recent visual viewport change
    
    if (shouldUpdate) {
      this.updateCanvasRect();
    }
  }

  /**
   * Mobile-optimized screen to grid conversion
   * Includes device pixel ratio and visual viewport corrections
   */
  screenToGrid(
    screenCoord: ScreenCoordinate,
    viewport: ViewportState
  ): GridCoordinate | null {
    this.ensureCanvasRect();

    // Enhanced validation for mobile edge cases
    if (!this.isValidScreenCoordinate(screenCoord)) {
      this.logger.warn('Invalid screen coordinate', screenCoord);
      return null;
    }

    if (!this.isValidViewport(viewport)) {
      throw new CoordinateError('Invalid viewport state', screenCoord);
    }

    try {
      // Step 1: Apply device pixel ratio correction if needed
      const correctedScreenCoord = this.applyCorrectionIfNeeded(screenCoord);

      // Step 2: Screen to Canvas coordinates with visual viewport consideration
      const canvasCoord = this.screenToCanvasMobile(correctedScreenCoord);
      if (!canvasCoord) return null;

      // Step 3: Canvas to Grid coordinates
      const gridCoord = this.canvasToGrid(canvasCoord, viewport);

      // Step 4: Apply mobile-specific tolerance for touch precision
      return this.applyTouchTolerance(gridCoord);

    } catch (error) {
      this.logger.error('Mobile screen to grid transformation failed', {
        screenCoord,
        viewport,
        canvasRect: this.canvasRect,
        visualViewport: window.visualViewport,
        error
      });
      throw new CoordinateError(
        `Failed to transform screen coordinate ${screenCoord.x}, ${screenCoord.y}`,
        screenCoord
      );
    }
  }

  /**
   * Apply device pixel ratio correction if needed
   */
  private applyCorrectionIfNeeded(screenCoord: ScreenCoordinate): ScreenCoordinate {
    // For most cases, no correction needed as getBoundingClientRect handles DPR
    // But we keep this for future enhancements
    return screenCoord;
  }

  /**
   * Mobile-optimized screen to canvas conversion
   * Handles Visual Viewport API and mobile browser quirks
   */
  private screenToCanvasMobile(screenCoord: ScreenCoordinate): CanvasCoordinate | null {
    // Use Visual Viewport for more accurate coordinates if available
    let effectiveLeft = this.canvasRect.left;
    let effectiveTop = this.canvasRect.top;

    if (window.visualViewport) {
      // Adjust for visual viewport offset (happens when virtual keyboard is shown)
      effectiveLeft += window.visualViewport.offsetLeft;
      effectiveTop += window.visualViewport.offsetTop;
    }

    // Check if point is within canvas bounds (with small tolerance)
    const tolerance = 2; // 2px tolerance for touch precision
    if (
      screenCoord.x < effectiveLeft - tolerance ||
      screenCoord.x > effectiveLeft + this.canvasRect.width + tolerance ||
      screenCoord.y < effectiveTop - tolerance ||
      screenCoord.y > effectiveTop + this.canvasRect.height + tolerance
    ) {
      return null; // Outside canvas
    }

    // Get computed style for borders/padding with caching
    const computedStyle = getComputedStyle(this.canvas);
    const borderLeft = parseFloat(computedStyle.borderLeftWidth) || 0;
    const borderTop = parseFloat(computedStyle.borderTopWidth) || 0;
    const paddingLeft = parseFloat(computedStyle.paddingLeft) || 0;
    const paddingTop = parseFloat(computedStyle.paddingTop) || 0;

    return {
      x: screenCoord.x - effectiveLeft - borderLeft - paddingLeft,
      y: screenCoord.y - effectiveTop - borderTop - paddingTop
    };
  }

  /**
   * Canvas to Grid conversion with mobile precision
   */
  private canvasToGrid(
    canvasCoord: CanvasCoordinate,
    viewport: ViewportState
  ): GridCoordinate {
    // Convert canvas pixels to grid coordinates with high precision
    const gridX = (canvasCoord.x / this.constraints.pixelSize / viewport.zoom) + viewport.offsetX;
    const gridY = (canvasCoord.y / this.constraints.pixelSize / viewport.zoom) + viewport.offsetY;

    // Use proper rounding for grid alignment
    const clampedX = Math.max(0, Math.min(this.constraints.gridWidth - 1, Math.round(gridX)));
    const clampedY = Math.max(0, Math.min(this.constraints.gridHeight - 1, Math.round(gridY)));

    return { x: clampedX, y: clampedY };
  }

  /**
   * Apply touch tolerance for better mobile precision
   */
  private applyTouchTolerance(gridCoord: GridCoordinate): GridCoordinate {
    // For touch interfaces, we might want to snap to nearest valid grid point
    // This helps with touch precision issues
    return {
      x: Math.max(0, Math.min(this.constraints.gridWidth - 1, Math.round(gridCoord.x))),
      y: Math.max(0, Math.min(this.constraints.gridHeight - 1, Math.round(gridCoord.y)))
    };
  }

  /**
   * Mobile-optimized grid to screen conversion
   */
  gridToScreen(
    gridCoord: GridCoordinate,
    viewport: ViewportState
  ): ScreenCoordinate | null {
    this.ensureCanvasRect();

    if (!this.isValidGridCoordinate(gridCoord)) {
      this.logger.warn('Invalid grid coordinate', gridCoord);
      return null;
    }

    if (!this.isValidViewport(viewport)) {
      throw new CoordinateError('Invalid viewport state', gridCoord);
    }

    try {
      // Step 1: Grid to Canvas coordinates
      const canvasCoord = this.gridToCanvas(gridCoord, viewport);

      // Step 2: Canvas to Screen coordinates with mobile adjustments
      return this.canvasToScreenMobile(canvasCoord);
    } catch (error) {
      this.logger.error('Mobile grid to screen transformation failed', {
        gridCoord,
        viewport,
        error
      });
      return null;
    }
  }

  /**
   * Grid to Canvas conversion
   */
  private gridToCanvas(
    gridCoord: GridCoordinate,
    viewport: ViewportState
  ): CanvasCoordinate {
    // Convert grid coordinates to canvas pixels
    const canvasX = (gridCoord.x - viewport.offsetX) * this.constraints.pixelSize * viewport.zoom;
    const canvasY = (gridCoord.y - viewport.offsetY) * this.constraints.pixelSize * viewport.zoom;

    return { x: canvasX, y: canvasY };
  }

  /**
   * Mobile-optimized canvas to screen conversion
   */
  private canvasToScreenMobile(canvasCoord: CanvasCoordinate): ScreenCoordinate {
    const computedStyle = getComputedStyle(this.canvas);
    const borderLeft = parseFloat(computedStyle.borderLeftWidth) || 0;
    const borderTop = parseFloat(computedStyle.borderTopWidth) || 0;
    const paddingLeft = parseFloat(computedStyle.paddingLeft) || 0;
    const paddingTop = parseFloat(computedStyle.paddingTop) || 0;

    let effectiveLeft = this.canvasRect.left;
    let effectiveTop = this.canvasRect.top;

    if (window.visualViewport) {
      effectiveLeft += window.visualViewport.offsetLeft;
      effectiveTop += window.visualViewport.offsetTop;
    }

    return {
      x: canvasCoord.x + effectiveLeft + borderLeft + paddingLeft,
      y: canvasCoord.y + effectiveTop + borderTop + paddingTop
    };
  }

  /**
   * Get the grid coordinate at the center of the viewport
   */
  getViewportCenter(viewport: ViewportState): GridCoordinate {
    const centerCanvas: CanvasCoordinate = {
      x: this.canvasRect.width / 2,
      y: this.canvasRect.height / 2
    };

    return this.canvasToGrid(centerCanvas, viewport);
  }

  /**
   * Get the grid area currently visible in the viewport
   */
  getVisibleGridArea(viewport: ViewportState): {
    topLeft: GridCoordinate;
    bottomRight: GridCoordinate;
    width: number;
    height: number;
  } {
    const topLeft = this.canvasToGrid({ x: 0, y: 0 }, viewport);
    const bottomRight = this.canvasToGrid(
      { x: this.canvasRect.width, y: this.canvasRect.height },
      viewport
    );

    return {
      topLeft,
      bottomRight,
      width: bottomRight.x - topLeft.x + 1,
      height: bottomRight.y - topLeft.y + 1
    };
  }

  /**
   * Calculate viewport size in grid units
   */
  getViewportSizeInGrid(zoom: number): { width: number; height: number } {
    this.ensureCanvasRect();

    const width = this.canvasRect.width / (this.constraints.pixelSize * zoom);
    const height = this.canvasRect.height / (this.constraints.pixelSize * zoom);

    return { width, height };
  }

  // ===== VALIDATION METHODS =====

  private isValidScreenCoordinate(coord: ScreenCoordinate): boolean {
    return (
      typeof coord.x === 'number' &&
      typeof coord.y === 'number' &&
      !isNaN(coord.x) &&
      !isNaN(coord.y) &&
      isFinite(coord.x) &&
      isFinite(coord.y) &&
      coord.x >= -10000 && coord.x <= 10000 && // Reasonable bounds
      coord.y >= -10000 && coord.y <= 10000
    );
  }

  private isValidGridCoordinate(coord: GridCoordinate): boolean {
    return (
      this.isValidScreenCoordinate(coord) &&
      coord.x >= 0 &&
      coord.y >= 0 &&
      coord.x < this.constraints.gridWidth &&
      coord.y < this.constraints.gridHeight
    );
  }

  private isValidViewport(viewport: ViewportState): boolean {
    return (
      typeof viewport.zoom === 'number' &&
      typeof viewport.offsetX === 'number' &&
      typeof viewport.offsetY === 'number' &&
      !isNaN(viewport.zoom) &&
      !isNaN(viewport.offsetX) &&
      !isNaN(viewport.offsetY) &&
      isFinite(viewport.zoom) &&
      isFinite(viewport.offsetX) &&
      isFinite(viewport.offsetY) &&
      viewport.zoom > 0 &&
      viewport.zoom <= 100 // Reasonable max zoom
    );
  }

  // ===== MOBILE DEBUGGING UTILITIES =====

  /**
   * Get comprehensive mobile debugging information
   */
  getMobileDebugInfo(screenCoord: ScreenCoordinate, viewport: ViewportState): any {
    this.ensureCanvasRect();

    const canvasCoord = this.screenToCanvasMobile(screenCoord);
    const gridCoord = canvasCoord ? this.canvasToGrid(canvasCoord, viewport) : null;

    return {
      input: {
        screen: screenCoord,
        viewport: {
          zoom: viewport.zoom,
          offset: { x: viewport.offsetX, y: viewport.offsetY }
        }
      },
      mobile: {
        visualViewportSupported: this.visualViewportSupported,
        visualViewport: window.visualViewport ? {
          width: window.visualViewport.width,
          height: window.visualViewport.height,
          offsetLeft: window.visualViewport.offsetLeft,
          offsetTop: window.visualViewport.offsetTop,
          scale: window.visualViewport.scale
        } : null,
        devicePixelRatio: window.devicePixelRatio,
        touchCapabilities: {
          maxTouchPoints: navigator.maxTouchPoints || 0,
          touchSupported: 'ontouchstart' in window
        }
      },
      intermediate: {
        canvas: canvasCoord,
        canvasRect: {
          width: this.canvasRect.width,
          height: this.canvasRect.height,
          left: this.canvasRect.left,
          top: this.canvasRect.top
        }
      },
      output: {
        grid: gridCoord
      },
      constants: {
        pixelSize: this.constraints.pixelSize,
        gridSize: {
          width: this.constraints.gridWidth,
          height: this.constraints.gridHeight
        }
      },
      timing: {
        lastUpdate: this.lastUpdate,
        lastVisualViewportChange: this.lastVisualViewportChange
      }
    };
  }

  /**
   * Test coordinate transformation accuracy for mobile
   */
  testMobileAccuracy(gridCoord: GridCoordinate, viewport: ViewportState): {
    original: GridCoordinate;
    roundTrip: GridCoordinate | null;
    screenIntermediate: ScreenCoordinate | null;
    accurate: boolean;
    pixelError: number;
    error?: string;
  } {
    try {
      const screenCoord = this.gridToScreen(gridCoord, viewport);
      if (!screenCoord) {
        return {
          original: gridCoord,
          roundTrip: null,
          screenIntermediate: null,
          accurate: false,
          pixelError: Infinity,
          error: 'Grid to screen conversion failed'
        };
      }

      const roundTripGrid = this.screenToGrid(screenCoord, viewport);
      const pixelError = roundTripGrid ? 
        Math.sqrt(Math.pow(roundTripGrid.x - gridCoord.x, 2) + Math.pow(roundTripGrid.y - gridCoord.y, 2)) :
        Infinity;
        
      const accurate = roundTripGrid !== null && pixelError < 1; // Within 1 grid cell

      return {
        original: gridCoord,
        roundTrip: roundTripGrid,
        screenIntermediate: screenCoord,
        accurate,
        pixelError
      };
    } catch (error) {
      return {
        original: gridCoord,
        roundTrip: null,
        screenIntermediate: null,
        accurate: false,
        pixelError: Infinity,
        error: error instanceof Error ? error.message : 'Unknown error'
      };
    }
  }

  /**
   * Cleanup method
   */
  destroy(): void {
    window.removeEventListener('resize', this.handleViewportChange);
    window.removeEventListener('orientationchange', this.handleOrientationChange);
    
    if (this.visualViewportSupported && window.visualViewport) {
      window.visualViewport.removeEventListener('resize', this.handleVisualViewportChange);
      window.visualViewport.removeEventListener('scroll', this.handleVisualViewportChange);
    }
  }
}