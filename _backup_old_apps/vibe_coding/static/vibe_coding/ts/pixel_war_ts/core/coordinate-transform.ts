/**
 * Unified Coordinate Transformation System
 * 
 * This class handles all coordinate transformations with high precision
 * and eliminates the multiple transformation bugs from the original JS implementation.
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

export class CoordinateTransform {
  private canvasRect: DOMRect;
  private lastUpdate: number = 0;
  private readonly RECT_CACHE_TIME = 100; // Cache canvas rect for 100ms
  private devicePixelRatio: number = window.devicePixelRatio || 1;
  private visualViewportSupport: boolean = 'visualViewport' in window;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly constraints: ViewportConstraints,
    private readonly logger: Logger
  ) {
    this.canvasRect = canvas.getBoundingClientRect();
    this.lastUpdate = Date.now();

    // Update canvas rect on resize
    window.addEventListener('resize', this.updateCanvasRect.bind(this));
    window.addEventListener('orientationchange', this.updateCanvasRect.bind(this));
  }

  /**
   * Updates the cached canvas rectangle
   * Called automatically on resize/orientation change
   */
  private updateCanvasRect(): void {
    // Get fresh canvas rect
    this.canvasRect = this.canvas.getBoundingClientRect();
    this.devicePixelRatio = window.devicePixelRatio || 1;
    this.lastUpdate = Date.now();
    
    this.logger.debug('Canvas rect updated', {
      rect: this.canvasRect,
      devicePixelRatio: this.devicePixelRatio,
      visualViewportSupport: this.visualViewportSupport,
      timestamp: this.lastUpdate
    });
  }

  /**
   * Ensures canvas rect is up to date
   */
  private ensureCanvasRect(): void {
    const now = Date.now();
    if (now - this.lastUpdate > this.RECT_CACHE_TIME) {
      this.updateCanvasRect();
    }
  }

  /**
   * Converts screen coordinates to grid coordinates
   * This is the main coordinate transformation used for touch/mouse input
   */
  screenToGrid(
    screenCoord: ScreenCoordinate,
    viewport: ViewportState
  ): GridCoordinate | null {
    this.ensureCanvasRect();

    // Validate inputs
    if (!this.isValidScreenCoordinate(screenCoord)) {
      this.logger.warn('Invalid screen coordinate', screenCoord);
      return null;
    }

    if (!this.isValidViewport(viewport)) {
      throw new CoordinateError('Invalid viewport state', screenCoord);
    }

    try {
      // Step 1: Screen to Canvas coordinates
      const canvasCoord = this.screenToCanvas(screenCoord);
      if (!canvasCoord) return null;

      // Step 2: Canvas to Grid coordinates
      return this.canvasToGrid(canvasCoord, viewport);
    } catch (error) {
      this.logger.error('Screen to grid transformation failed', {
        screenCoord,
        viewport,
        error
      });
      throw new CoordinateError(
        `Failed to transform screen coordinate ${screenCoord.x}, ${screenCoord.y}`,
        screenCoord
      );
    }
  }

  /**
   * Converts grid coordinates to screen coordinates
   * Used for rendering and highlighting pixels
   */
  gridToScreen(
    gridCoord: GridCoordinate,
    viewport: ViewportState
  ): ScreenCoordinate | null {
    this.ensureCanvasRect();

    // Validate inputs
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

      // Step 2: Canvas to Screen coordinates
      return this.canvasToScreen(canvasCoord);
    } catch (error) {
      this.logger.error('Grid to screen transformation failed', {
        gridCoord,
        viewport,
        error
      });
      return null;
    }
  }

  /**
   * Screen to Canvas conversion
   * Enhanced for mobile with Visual Viewport API support
   */
  private screenToCanvas(screenCoord: ScreenCoordinate): CanvasCoordinate | null {
    // Get effective screen coordinates considering Visual Viewport
    let effectiveX = screenCoord.x;
    let effectiveY = screenCoord.y;
    
    // Apply Visual Viewport offset if supported (for mobile browser chrome)
    if (this.visualViewportSupport && window.visualViewport) {
      effectiveX = screenCoord.x + window.visualViewport.offsetLeft;
      effectiveY = screenCoord.y + window.visualViewport.offsetTop;
    }

    // Check if point is within canvas bounds with tolerance for touch precision
    const tolerance = 2; // pixels
    if (
      effectiveX < this.canvasRect.left - tolerance ||
      effectiveX > this.canvasRect.right + tolerance ||
      effectiveY < this.canvasRect.top - tolerance ||
      effectiveY > this.canvasRect.bottom + tolerance
    ) {
      this.logger.debug('Touch outside canvas bounds', {
        touch: { x: effectiveX, y: effectiveY },
        bounds: this.canvasRect,
        tolerance
      });
      return null; // Outside canvas
    }

    // Get computed style for borders/padding
    const computedStyle = getComputedStyle(this.canvas);
    const borderLeft = parseFloat(computedStyle.borderLeftWidth) || 0;
    const borderTop = parseFloat(computedStyle.borderTopWidth) || 0;
    const paddingLeft = parseFloat(computedStyle.paddingLeft) || 0;
    const paddingTop = parseFloat(computedStyle.paddingTop) || 0;
    
    // Apply device pixel ratio correction for high-DPI displays
    let canvasX = effectiveX - this.canvasRect.left - borderLeft - paddingLeft;
    let canvasY = effectiveY - this.canvasRect.top - borderTop - paddingTop;
    
    // Clamp to canvas bounds to prevent edge case issues
    canvasX = Math.max(0, Math.min(this.canvasRect.width - borderLeft - paddingLeft, canvasX));
    canvasY = Math.max(0, Math.min(this.canvasRect.height - borderTop - paddingTop, canvasY));

    return { x: canvasX, y: canvasY };
  }

  /**
   * Canvas to Screen conversion
   */
  private canvasToScreen(canvasCoord: CanvasCoordinate): ScreenCoordinate {
    const computedStyle = getComputedStyle(this.canvas);
    const borderLeft = parseFloat(computedStyle.borderLeftWidth) || 0;
    const borderTop = parseFloat(computedStyle.borderTopWidth) || 0;
    const paddingLeft = parseFloat(computedStyle.paddingLeft) || 0;
    const paddingTop = parseFloat(computedStyle.paddingTop) || 0;

    return {
      x: canvasCoord.x + this.canvasRect.left + borderLeft + paddingLeft,
      y: canvasCoord.y + this.canvasRect.top + borderTop + paddingTop
    };
  }

  /**
   * Canvas to Grid conversion
   * This is where zoom and offset are applied
   */
  private canvasToGrid(
    canvasCoord: CanvasCoordinate,
    viewport: ViewportState
  ): GridCoordinate {
    // Convert canvas pixels to grid coordinates
    // Formula: (canvasPixel / pixelSize / zoom) + offset
    const gridX = (canvasCoord.x / this.constraints.pixelSize / viewport.zoom) + viewport.offsetX;
    const gridY = (canvasCoord.y / this.constraints.pixelSize / viewport.zoom) + viewport.offsetY;

    // Round to nearest grid cell and clamp to bounds
    const clampedX = Math.max(0, Math.min(this.constraints.gridWidth - 1, Math.floor(gridX)));
    const clampedY = Math.max(0, Math.min(this.constraints.gridHeight - 1, Math.floor(gridY)));

    return { x: clampedX, y: clampedY };
  }

  /**
   * Grid to Canvas conversion
   */
  private gridToCanvas(
    gridCoord: GridCoordinate,
    viewport: ViewportState
  ): CanvasCoordinate {
    // Convert grid coordinates to canvas pixels
    // Formula: (gridCoord - offset) * pixelSize * zoom
    const canvasX = (gridCoord.x - viewport.offsetX) * this.constraints.pixelSize * viewport.zoom;
    const canvasY = (gridCoord.y - viewport.offsetY) * this.constraints.pixelSize * viewport.zoom;

    return { x: canvasX, y: canvasY };
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
      isFinite(coord.y)
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
      viewport.zoom > 0
    );
  }

  // ===== DEBUGGING UTILITIES =====

  /**
   * Enhanced debug information for mobile coordinate transformations
   */
  getDebugInfo(screenCoord: ScreenCoordinate, viewport: ViewportState): any {
    this.ensureCanvasRect();

    const canvasCoord = this.screenToCanvas(screenCoord);
    const gridCoord = canvasCoord ? this.canvasToGrid(canvasCoord, viewport) : null;
    
    // Get Visual Viewport information if available
    let visualViewport = null;
    if (this.visualViewportSupport && window.visualViewport) {
      visualViewport = {
        width: window.visualViewport.width,
        height: window.visualViewport.height,
        offsetLeft: window.visualViewport.offsetLeft,
        offsetTop: window.visualViewport.offsetTop,
        pageLeft: window.visualViewport.pageLeft,
        pageTop: window.visualViewport.pageTop,
        scale: window.visualViewport.scale
      };
    }

    // Get computed style information
    const computedStyle = getComputedStyle(this.canvas);
    const styleInfo = {
      borderLeft: parseFloat(computedStyle.borderLeftWidth) || 0,
      borderTop: parseFloat(computedStyle.borderTopWidth) || 0,
      paddingLeft: parseFloat(computedStyle.paddingLeft) || 0,
      paddingTop: parseFloat(computedStyle.paddingTop) || 0,
      transform: computedStyle.transform
    };

    return {
      input: {
        screen: screenCoord,
        viewport: {
          zoom: viewport.zoom,
          offset: { x: viewport.offsetX, y: viewport.offsetY }
        }
      },
      intermediate: {
        canvas: canvasCoord,
        canvasRect: {
          width: this.canvasRect.width,
          height: this.canvasRect.height,
          left: this.canvasRect.left,
          top: this.canvasRect.top,
          right: this.canvasRect.right,
          bottom: this.canvasRect.bottom
        }
      },
      output: {
        grid: gridCoord
      },
      mobile: {
        devicePixelRatio: this.devicePixelRatio,
        visualViewportSupported: this.visualViewportSupport,
        visualViewport,
        windowSize: {
          innerWidth: window.innerWidth,
          innerHeight: window.innerHeight,
          outerWidth: window.outerWidth,
          outerHeight: window.outerHeight
        },
        screenSize: {
          width: window.screen.width,
          height: window.screen.height,
          availWidth: window.screen.availWidth,
          availHeight: window.screen.availHeight
        }
      },
      style: styleInfo,
      constants: {
        pixelSize: this.constraints.pixelSize,
        gridSize: {
          width: this.constraints.gridWidth,
          height: this.constraints.gridHeight
        },
        rectCacheTime: this.RECT_CACHE_TIME,
        lastRectUpdate: this.lastUpdate
      }
    };
  }

  /**
   * Test coordinate transformation round-trip accuracy
   */
  testRoundTrip(gridCoord: GridCoordinate, viewport: ViewportState): {
    original: GridCoordinate;
    roundTrip: GridCoordinate | null;
    accurate: boolean;
    error?: string;
  } {
    try {
      const screenCoord = this.gridToScreen(gridCoord, viewport);
      if (!screenCoord) {
        return {
          original: gridCoord,
          roundTrip: null,
          accurate: false,
          error: 'Grid to screen conversion failed'
        };
      }

      const roundTripGrid = this.screenToGrid(screenCoord, viewport);
      const accurate = roundTripGrid !== null &&
        roundTripGrid.x === gridCoord.x &&
        roundTripGrid.y === gridCoord.y;

      return {
        original: gridCoord,
        roundTrip: roundTripGrid,
        accurate
      };
    } catch (error) {
      return {
        original: gridCoord,
        roundTrip: null,
        accurate: false,
        error: error instanceof Error ? error.message : 'Unknown error'
      };
    }
  }

  /**
   * Mobile-specific coordinate validation and correction
   */
  validateMobileCoordinate(screenCoord: ScreenCoordinate): {
    isValid: boolean;
    corrected?: ScreenCoordinate;
    issues: string[];
  } {
    const issues: string[] = [];
    let corrected = { ...screenCoord };
    
    // Check for common mobile issues
    if (screenCoord.x < 0 || screenCoord.y < 0) {
      issues.push('Negative coordinates detected');
      corrected.x = Math.max(0, corrected.x);
      corrected.y = Math.max(0, corrected.y);
    }
    
    // Check if coordinates are suspiciously large
    const maxReasonableX = window.screen.width * 2;
    const maxReasonableY = window.screen.height * 2;
    
    if (screenCoord.x > maxReasonableX || screenCoord.y > maxReasonableY) {
      issues.push('Coordinates suspiciously large');
      corrected.x = Math.min(corrected.x, maxReasonableX);
      corrected.y = Math.min(corrected.y, maxReasonableY);
    }
    
    // Check for NaN or Infinity
    if (!isFinite(screenCoord.x) || !isFinite(screenCoord.y)) {
      issues.push('Non-finite coordinates');
      return { isValid: false, issues };
    }
    
    return {
      isValid: issues.length === 0,
      corrected: issues.length > 0 ? corrected : undefined,
      issues
    };
  }
  
  /**
   * Force canvas rect refresh - useful for mobile orientation changes
   */
  forceRectUpdate(): void {
    this.updateCanvasRect();
  }
  
  /**
   * Get mobile-specific viewport information
   */
  getMobileViewportInfo(): {
    visualViewport: any;
    canvasRect: DOMRect;
    effectiveArea: { width: number; height: number };
    deviceInfo: { devicePixelRatio: number; orientation: string };
  } {
    this.ensureCanvasRect();
    
    let visualViewport = null;
    if (this.visualViewportSupport && window.visualViewport) {
      visualViewport = {
        width: window.visualViewport.width,
        height: window.visualViewport.height,
        offsetLeft: window.visualViewport.offsetLeft,
        offsetTop: window.visualViewport.offsetTop,
        scale: window.visualViewport.scale
      };
    }
    
    const effectiveWidth = visualViewport ? 
      Math.min(visualViewport.width, this.canvasRect.width) : 
      this.canvasRect.width;
    const effectiveHeight = visualViewport ? 
      Math.min(visualViewport.height, this.canvasRect.height) : 
      this.canvasRect.height;
    
    return {
      visualViewport,
      canvasRect: this.canvasRect,
      effectiveArea: {
        width: effectiveWidth,
        height: effectiveHeight
      },
      deviceInfo: {
        devicePixelRatio: this.devicePixelRatio,
        orientation: window.innerWidth > window.innerHeight ? 'landscape' : 'portrait'
      }
    };
  }

  /**
   * Cleanup method
   */
  destroy(): void {
    window.removeEventListener('resize', this.updateCanvasRect);
    window.removeEventListener('orientationchange', this.updateCanvasRect);
  }
}