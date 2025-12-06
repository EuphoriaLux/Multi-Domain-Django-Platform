/**
 * Viewport Management System
 * 
 * Handles zoom, pan, and viewport constraints with predictable behavior.
 * Eliminates the complex constraint logic from the original implementation.
 */

import {
  ViewportState,
  ViewportConstraints,
  BoundingBox,
  ViewportChangeEvent,
  ViewportError,
  MobileViewport,
  Logger
} from '../types/index.js';

export class ViewportManager {
  private currentState: ViewportState;
  private targetState: ViewportState;
  private animationId: number | null = null;
  private readonly eventTarget = new EventTarget();

  // Animation constants
  private readonly ANIMATION_SPEED = 0.15;
  private readonly ANIMATION_THRESHOLD = 0.01;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly constraints: ViewportConstraints,
    private readonly logger: Logger,
    initialZoom?: number
  ) {
    // Calculate initial viewport state
    const zoom = initialZoom ?? this.calculateOptimalInitialZoom();
    const bounds = this.calculateBounds(zoom);
    const centerOffsets = this.calculateCenterOffsets(zoom);

    this.currentState = {
      zoom,
      offsetX: centerOffsets.x,
      offsetY: centerOffsets.y,
      bounds
    };

    this.targetState = { ...this.currentState };

    // Listen for canvas size changes
    this.setupResizeHandling();

    this.logger.info('Viewport manager initialized', {
      initialState: this.currentState,
      constraints: this.constraints
    });
  }

  // ===== PUBLIC API =====

  /**
   * Get current viewport state (immutable)
   */
  getCurrentState(): ViewportState {
    return { ...this.currentState };
  }

  /**
   * Get target viewport state (what we're animating towards)
   */
  getTargetState(): ViewportState {
    return { ...this.targetState };
  }

  /**
   * Set zoom level with optional focal point
   */
  setZoom(zoom: number, focalPoint?: { x: number; y: number }): void {
    const clampedZoom = this.clampZoom(zoom);
    
    if (clampedZoom === this.targetState.zoom) {
      return; // No change needed
    }

    const oldState = { ...this.targetState };

    // Calculate new bounds for the zoom level
    const bounds = this.calculateBounds(clampedZoom);

    let offsetX = this.targetState.offsetX;
    let offsetY = this.targetState.offsetY;

    // If focal point provided, adjust offsets to keep it centered
    if (focalPoint) {
      const canvasRect = this.canvas.getBoundingClientRect();
      
      // Calculate focal point in normalized coordinates (0-1)
      const normalizedX = focalPoint.x / canvasRect.width;
      const normalizedY = focalPoint.y / canvasRect.height;

      // Calculate viewport dimensions at old and new zoom
      const oldViewportWidth = canvasRect.width / (this.constraints.pixelSize * this.targetState.zoom);
      const oldViewportHeight = canvasRect.height / (this.constraints.pixelSize * this.targetState.zoom);
      const newViewportWidth = canvasRect.width / (this.constraints.pixelSize * clampedZoom);
      const newViewportHeight = canvasRect.height / (this.constraints.pixelSize * clampedZoom);

      // Calculate world position that should stay under the focal point
      const worldX = this.targetState.offsetX + normalizedX * oldViewportWidth;
      const worldY = this.targetState.offsetY + normalizedY * oldViewportHeight;

      // Calculate new offsets to keep that world position under the focal point
      offsetX = worldX - normalizedX * newViewportWidth;
      offsetY = worldY - normalizedY * newViewportHeight;
    }

    // Apply constraints to offsets
    const constrainedOffsets = this.constrainOffsets(offsetX, offsetY, clampedZoom);

    this.targetState = {
      zoom: clampedZoom,
      offsetX: constrainedOffsets.x,
      offsetY: constrainedOffsets.y,
      bounds
    };

    this.startAnimation();
    this.dispatchViewportChange(oldState, this.targetState, 'user_input');
  }

  /**
   * Set pan offset
   */
  setPan(offsetX: number, offsetY: number): void {
    const oldState = { ...this.targetState };
    const constrainedOffsets = this.constrainOffsets(offsetX, offsetY, this.targetState.zoom);

    this.targetState = {
      ...this.targetState,
      offsetX: constrainedOffsets.x,
      offsetY: constrainedOffsets.y
    };

    this.startAnimation();
    this.dispatchViewportChange(oldState, this.targetState, 'user_input');
  }

  /**
   * Apply delta pan (relative movement)
   */
  applyPanDelta(deltaX: number, deltaY: number): void {
    this.setPan(
      this.targetState.offsetX + deltaX,
      this.targetState.offsetY + deltaY
    );
  }

  /**
   * Apply zoom delta with focal point
   */
  applyZoomDelta(delta: number, focalPoint?: { x: number; y: number }): void {
    this.setZoom(this.targetState.zoom + delta, focalPoint);
  }

  /**
   * Reset to optimal zoom and center position
   */
  reset(): void {
    const zoom = this.calculateOptimalInitialZoom();
    const centerOffsets = this.calculateCenterOffsets(zoom);
    
    const oldState = { ...this.targetState };
    this.targetState = {
      zoom,
      offsetX: centerOffsets.x,
      offsetY: centerOffsets.y,
      bounds: this.calculateBounds(zoom)
    };

    this.startAnimation();
    this.dispatchViewportChange(oldState, this.targetState, 'api_update');
  }

  /**
   * Fit entire grid in viewport
   */
  fitToGrid(): void {
    const zoom = this.calculateMinZoom();
    const centerOffsets = this.calculateCenterOffsets(zoom);

    const oldState = { ...this.targetState };
    this.targetState = {
      zoom,
      offsetX: centerOffsets.x,
      offsetY: centerOffsets.y,
      bounds: this.calculateBounds(zoom)
    };

    this.startAnimation();
    this.dispatchViewportChange(oldState, this.targetState, 'api_update');
  }

  /**
   * Navigate to specific grid coordinates
   */
  navigateToGrid(x: number, y: number): void {
    const canvasRect = this.canvas.getBoundingClientRect();
    const viewportWidth = canvasRect.width / (this.constraints.pixelSize * this.targetState.zoom);
    const viewportHeight = canvasRect.height / (this.constraints.pixelSize * this.targetState.zoom);

    // Center the target coordinate in the viewport
    const offsetX = x - viewportWidth / 2;
    const offsetY = y - viewportHeight / 2;

    this.setPan(offsetX, offsetY);
  }

  /**
   * Check if animation is currently running
   */
  isAnimating(): boolean {
    return this.animationId !== null;
  }

  /**
   * Force immediate update to target state (skip animation)
   */
  forceUpdate(): void {
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
      this.animationId = null;
    }

    this.currentState = { ...this.targetState };
  }

  // ===== COORDINATE HELPERS =====

  /**
   * Get viewport size in grid units at current zoom
   */
  getViewportSizeInGrid(): { width: number; height: number } {
    const canvasRect = this.canvas.getBoundingClientRect();
    return {
      width: canvasRect.width / (this.constraints.pixelSize * this.currentState.zoom),
      height: canvasRect.height / (this.constraints.pixelSize * this.currentState.zoom)
    };
  }

  /**
   * Check if grid coordinate is currently visible
   */
  isGridCoordinateVisible(x: number, y: number): boolean {
    const viewportSize = this.getViewportSizeInGrid();
    
    return (
      x >= this.currentState.offsetX &&
      x < this.currentState.offsetX + viewportSize.width &&
      y >= this.currentState.offsetY &&
      y < this.currentState.offsetY + viewportSize.height
    );
  }

  // ===== PRIVATE METHODS =====

  private setupResizeHandling(): void {
    let resizeTimeout: number | null = null;
    
    const handleResize = () => {
      // Debounce resize events for mobile performance
      if (resizeTimeout) {
        clearTimeout(resizeTimeout);
      }
      
      resizeTimeout = window.setTimeout(() => {
        this.handleViewportChange();
      }, 100); // 100ms debounce
    };
    
    const handleOrientationChange = () => {
      // Orientation changes need special handling on mobile
      setTimeout(() => {
        this.handleViewportChange();
        this.logger.info('Orientation change handled', {
          newSize: {
            width: window.innerWidth,
            height: window.innerHeight
          },
          orientation: window.innerWidth > window.innerHeight ? 'landscape' : 'portrait'
        });
      }, 200); // Wait for browser to settle
    };
    
    const handleVisualViewportChange = () => {
      // Handle Visual Viewport changes (mobile browser chrome)
      if ('visualViewport' in window) {
        this.handleViewportChange();
      }
    };

    window.addEventListener('resize', handleResize);
    window.addEventListener('orientationchange', handleOrientationChange);
    
    // Listen for Visual Viewport changes if supported
    if ('visualViewport' in window && window.visualViewport) {
      window.visualViewport.addEventListener('resize', handleVisualViewportChange);
      window.visualViewport.addEventListener('scroll', handleVisualViewportChange);
    }
  }
  
  handleViewportChange(): void {
    // Recalculate bounds and constraints on viewport change
    const bounds = this.calculateBounds(this.targetState.zoom);
    const constrainedOffsets = this.constrainOffsets(
      this.targetState.offsetX,
      this.targetState.offsetY,
      this.targetState.zoom
    );

    const oldState = { ...this.targetState };
    this.targetState = {
      ...this.targetState,
      offsetX: constrainedOffsets.x,
      offsetY: constrainedOffsets.y,
      bounds
    };

    this.startAnimation();
    this.dispatchViewportChange(oldState, this.targetState, 'constraint');
  }

  private calculateBounds(zoom: number): BoundingBox {
    const canvasRect = this.canvas.getBoundingClientRect();
    
    // Use effective size considering Visual Viewport for mobile
    let effectiveWidth = canvasRect.width;
    let effectiveHeight = canvasRect.height;
    
    if ('visualViewport' in window && window.visualViewport) {
      effectiveWidth = Math.min(window.visualViewport.width, canvasRect.width);
      effectiveHeight = Math.min(window.visualViewport.height, canvasRect.height);
    }
    
    const viewportWidth = effectiveWidth / (this.constraints.pixelSize * zoom);
    const viewportHeight = effectiveHeight / (this.constraints.pixelSize * zoom);

    if (viewportWidth >= this.constraints.gridWidth && viewportHeight >= this.constraints.gridHeight) {
      // Grid fits entirely in viewport - center it with movement allowance
      const centerX = -(viewportWidth - this.constraints.gridWidth) / 2;
      const centerY = -(viewportHeight - this.constraints.gridHeight) / 2;
      
      // Smaller allowance for mobile to reduce accidental panning
      const isMobile = window.innerWidth <= 768 || 'ontouchstart' in window;
      const allowanceMultiplier = isMobile ? 0.05 : 0.1;
      const allowance = Math.min(this.constraints.gridWidth, this.constraints.gridHeight) * allowanceMultiplier;

      return {
        minX: centerX - allowance,
        maxX: centerX + allowance,
        minY: centerY - allowance,
        maxY: centerY + allowance
      };
    } else {
      // Grid is larger than viewport - calculate proper bounds
      // Add small padding to prevent edge-case issues
      const padding = Math.min(viewportWidth, viewportHeight) * 0.02;
      
      return {
        minX: Math.min(-padding, -(this.constraints.gridWidth - viewportWidth + padding)),
        maxX: padding,
        minY: Math.min(-padding, -(this.constraints.gridHeight - viewportHeight + padding)),
        maxY: padding
      };
    }
  }

  private calculateCenterOffsets(zoom: number): { x: number; y: number } {
    const canvasRect = this.canvas.getBoundingClientRect();
    const viewportWidth = canvasRect.width / (this.constraints.pixelSize * zoom);
    const viewportHeight = canvasRect.height / (this.constraints.pixelSize * zoom);

    return {
      x: -(viewportWidth - this.constraints.gridWidth) / 2,
      y: -(viewportHeight - this.constraints.gridHeight) / 2
    };
  }

  private constrainOffsets(offsetX: number, offsetY: number, zoom: number): { x: number; y: number } {
    const bounds = this.calculateBounds(zoom);

    return {
      x: Math.max(bounds.minX, Math.min(bounds.maxX, offsetX)),
      y: Math.max(bounds.minY, Math.min(bounds.maxY, offsetY))
    };
  }

  private calculateMinZoom(): number {
    const canvasRect = this.canvas.getBoundingClientRect();
    
    // For mobile, consider Visual Viewport if available
    let effectiveWidth = canvasRect.width;
    let effectiveHeight = canvasRect.height;
    
    if ('visualViewport' in window && window.visualViewport) {
      effectiveWidth = Math.min(window.visualViewport.width, canvasRect.width);
      effectiveHeight = Math.min(window.visualViewport.height, canvasRect.height);
    }
    
    const zoomToFitWidth = effectiveWidth / (this.constraints.gridWidth * this.constraints.pixelSize);
    const zoomToFitHeight = effectiveHeight / (this.constraints.gridHeight * this.constraints.pixelSize);
    
    const calculatedMinZoom = Math.min(zoomToFitWidth, zoomToFitHeight);
    
    // Apply safety margin and enforce minimum floor
    // Use a larger safety margin for mobile to prevent edge scrolling issues
    const isMobile = window.innerWidth <= 768 || 'ontouchstart' in window;
    const safetyMargin = isMobile ? 0.9 : 0.95;
    const minZoomFloor = 0.05; // Lower floor for very large grids on mobile
    
    const finalMinZoom = Math.max(calculatedMinZoom * safetyMargin, minZoomFloor);
    
    this.logger.debug('Min zoom calculated', {
      canvasSize: { width: canvasRect.width, height: canvasRect.height },
      effectiveSize: { width: effectiveWidth, height: effectiveHeight },
      gridSize: { width: this.constraints.gridWidth, height: this.constraints.gridHeight },
      pixelSize: this.constraints.pixelSize,
      calculatedMinZoom,
      safetyMargin,
      finalMinZoom,
      isMobile
    });
    
    return finalMinZoom;
  }

  private calculateOptimalInitialZoom(): number {
    // Use minimum zoom that fits the entire grid
    return this.calculateMinZoom();
  }

  private clampZoom(zoom: number): number {
    const minZoom = this.calculateMinZoom();
    return Math.max(minZoom, Math.min(this.constraints.maxZoom, zoom));
  }

  private startAnimation(): void {
    if (this.animationId) {
      return; // Animation already running
    }

    const animate = () => {
      // Calculate differences
      const zoomDiff = this.targetState.zoom - this.currentState.zoom;
      const offsetXDiff = this.targetState.offsetX - this.currentState.offsetX;
      const offsetYDiff = this.targetState.offsetY - this.currentState.offsetY;

      // Check if animation should continue
      if (
        Math.abs(zoomDiff) < this.ANIMATION_THRESHOLD &&
        Math.abs(offsetXDiff) < this.ANIMATION_THRESHOLD &&
        Math.abs(offsetYDiff) < this.ANIMATION_THRESHOLD
      ) {
        // Animation complete
        this.currentState = { ...this.targetState };
        this.animationId = null;
        return;
      }

      // Apply smooth interpolation
      this.currentState = {
        zoom: this.currentState.zoom + zoomDiff * this.ANIMATION_SPEED,
        offsetX: this.currentState.offsetX + offsetXDiff * this.ANIMATION_SPEED,
        offsetY: this.currentState.offsetY + offsetYDiff * this.ANIMATION_SPEED,
        bounds: this.targetState.bounds
      };

      // Continue animation
      this.animationId = requestAnimationFrame(animate);
    };

    this.animationId = requestAnimationFrame(animate);
  }

  private dispatchViewportChange(
    oldState: ViewportState,
    newState: ViewportState,
    source: 'user_input' | 'api_update' | 'constraint'
  ): void {
    const event: ViewportChangeEvent = {
      type: 'viewport_change',
      timestamp: Date.now(),
      data: { oldState, newState, source }
    };

    this.eventTarget.dispatchEvent(new CustomEvent('viewport_change', {
      detail: event.data
    }));
  }

  // ===== EVENT HANDLING =====

  addEventListener(type: string, listener: EventListener): void {
    this.eventTarget.addEventListener(type, listener);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.eventTarget.removeEventListener(type, listener);
  }

  // ===== MOBILE UTILITIES =====

  getMobileViewportInfo(): MobileViewport {
    const canvasRect = this.canvas.getBoundingClientRect();
    
    // Use Visual Viewport API if available
    let effectiveWidth = canvasRect.width;
    let effectiveHeight = canvasRect.height;
    
    if (window.visualViewport) {
      effectiveWidth = Math.min(window.visualViewport.width, canvasRect.width);
      effectiveHeight = Math.min(window.visualViewport.height, canvasRect.height);
    }
    
    // Try to get safe area insets from CSS custom properties
    const computedStyle = getComputedStyle(document.documentElement);
    const getSafeAreaInset = (property: string): number => {
      try {
        const value = computedStyle.getPropertyValue(property);
        return value ? parseFloat(value.replace('px', '')) : 0;
      } catch {
        return 0;
      }
    };

    return {
      effectiveWidth,
      effectiveHeight,
      safeAreaInsets: {
        top: getSafeAreaInset('--safe-area-inset-top') || getSafeAreaInset('env(safe-area-inset-top)'),
        bottom: getSafeAreaInset('--safe-area-inset-bottom') || getSafeAreaInset('env(safe-area-inset-bottom)'),
        left: getSafeAreaInset('--safe-area-inset-left') || getSafeAreaInset('env(safe-area-inset-left)'),
        right: getSafeAreaInset('--safe-area-inset-right') || getSafeAreaInset('env(safe-area-inset-right)')
      },
      orientation: window.innerWidth > window.innerHeight ? 'landscape' : 'portrait'
    };
  }

  // ===== DEBUGGING =====

  getDebugInfo(): any {
    const canvasRect = this.canvas.getBoundingClientRect();
    const viewportSize = this.getViewportSizeInGrid();
    const minZoom = this.calculateMinZoom();
    const mobileViewport = this.getMobileViewportInfo();
    
    // Calculate effective viewport considering Visual Viewport
    let effectiveViewport = {
      width: canvasRect.width,
      height: canvasRect.height
    };
    
    if ('visualViewport' in window && window.visualViewport) {
      effectiveViewport = {
        width: Math.min(window.visualViewport.width, canvasRect.width),
        height: Math.min(window.visualViewport.height, canvasRect.height)
      };
    }

    return {
      current: this.currentState,
      target: this.targetState,
      canvas: {
        width: canvasRect.width,
        height: canvasRect.height,
        left: canvasRect.left,
        top: canvasRect.top,
        right: canvasRect.right,
        bottom: canvasRect.bottom
      },
      effectiveViewport,
      viewport: {
        sizeInGrid: viewportSize,
        canFitGrid: {
          width: viewportSize.width >= this.constraints.gridWidth,
          height: viewportSize.height >= this.constraints.gridHeight
        }
      },
      zoom: {
        current: this.currentState.zoom,
        target: this.targetState.zoom,
        min: minZoom,
        max: this.constraints.maxZoom
      },
      mobile: {
        viewportInfo: mobileViewport,
        visualViewport: 'visualViewport' in window && window.visualViewport ? {
          width: window.visualViewport.width,
          height: window.visualViewport.height,
          offsetLeft: window.visualViewport.offsetLeft,
          offsetTop: window.visualViewport.offsetTop,
          pageLeft: window.visualViewport.pageLeft,
          pageTop: window.visualViewport.pageTop,
          scale: window.visualViewport.scale
        } : null,
        devicePixelRatio: window.devicePixelRatio || 1,
        screenSize: {
          width: window.screen.width,
          height: window.screen.height,
          availWidth: window.screen.availWidth,
          availHeight: window.screen.availHeight
        },
        windowSize: {
          innerWidth: window.innerWidth,
          innerHeight: window.innerHeight,
          outerWidth: window.outerWidth,
          outerHeight: window.outerHeight
        },
        isMobile: window.innerWidth <= 768 || 'ontouchstart' in window
      },
      constraints: this.constraints,
      animation: {
        isAnimating: this.isAnimating(),
        animationId: this.animationId
      }
    };
  }

  /**
   * Cleanup method
   */
  destroy(): void {
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
      this.animationId = null;
    }
    
    // Remove event listeners (would need to track them to remove properly)
    // In a real implementation, we'd want to track added listeners
  }
}