/**
 * Mobile Input Handler
 * 
 * Simplified, reliable touch input handling designed specifically for mobile devices.
 * Eliminates the complex gesture detection that was causing navigation issues.
 */

import {
  TouchInput,
  TouchGesture,
  TouchGestureData,
  TouchMode,
  GestureState,
  ScreenCoordinate,
  InputEvent,
  HapticFeedback,
  Logger
} from '../types/index.js';

export class MobileInputHandler {
  private touches = new Map<number, TouchInput>();
  private gestureState: GestureState = 'idle';
  private touchMode: TouchMode = 'tap';
  private readonly eventTarget = new EventTarget();

  // Gesture detection thresholds
  private readonly TAP_THRESHOLD = 10; // pixels
  private readonly TAP_TIME_LIMIT = 300; // milliseconds
  private readonly LONG_PRESS_TIME = 500; // milliseconds
  private readonly MIN_PINCH_DISTANCE = 30; // pixels
  private readonly PAN_THRESHOLD = 5; // pixels

  // State tracking
  private gestureStartTime = 0;
  private lastGestureCenter: ScreenCoordinate = { x: 0, y: 0 };
  private initialPinchDistance = 0;
  private longPressTimer: number | null = null;

  // Performance optimization
  private lastEventTime = 0;
  private readonly EVENT_THROTTLE = 16; // ~60fps
  
  // Mobile coordinate handling
  private readonly devicePixelRatio = window.devicePixelRatio || 1;
  private readonly visualViewportSupport = 'visualViewport' in window;
  private lastCanvasRect: DOMRect | null = null;
  private rectUpdateTime = 0;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly logger: Logger
  ) {
    this.touchMode = (localStorage.getItem('pixelWarTouchMode') as TouchMode) || 'tap';
    this.setupEventListeners();
    this.logger.info('Mobile input handler initialized', {
      touchMode: this.touchMode,
      canvas: this.canvas.id
    });
  }

  // ===== PUBLIC API =====

  /**
   * Set touch interaction mode
   */
  setTouchMode(mode: TouchMode): void {
    this.touchMode = mode;
    localStorage.setItem('pixelWarTouchMode', mode);
    this.logger.info('Touch mode changed', { mode });
  }

  /**
   * Get current touch mode
   */
  getTouchMode(): TouchMode {
    return this.touchMode;
  }

  /**
   * Get current gesture state
   */
  getGestureState(): GestureState {
    return this.gestureState;
  }

  /**
   * Add event listener for input events
   */
  addEventListener(type: string, listener: EventListener): void {
    this.eventTarget.addEventListener(type, listener);
  }

  /**
   * Remove event listener
   */
  removeEventListener(type: string, listener: EventListener): void {
    this.eventTarget.removeEventListener(type, listener);
  }

  // ===== PRIVATE METHODS =====

  private setupEventListeners(): void {
    // Touch events
    this.canvas.addEventListener('touchstart', this.handleTouchStart.bind(this), { passive: false });
    this.canvas.addEventListener('touchmove', this.handleTouchMove.bind(this), { passive: false });
    this.canvas.addEventListener('touchend', this.handleTouchEnd.bind(this), { passive: false });
    this.canvas.addEventListener('touchcancel', this.handleTouchCancel.bind(this), { passive: false });

    // Mouse events for desktop testing
    this.canvas.addEventListener('mousedown', this.handleMouseDown.bind(this));
    this.canvas.addEventListener('mousemove', this.handleMouseMove.bind(this));
    this.canvas.addEventListener('mouseup', this.handleMouseUp.bind(this));
    this.canvas.addEventListener('wheel', this.handleWheel.bind(this), { passive: false });

    // Context menu prevention
    this.canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  // ===== TOUCH EVENT HANDLERS =====

  private handleTouchStart(e: TouchEvent): void {
    e.preventDefault();
    
    const now = Date.now();
    this.lastEventTime = now;
    
    // Update canvas rect if needed for accurate coordinates
    this.ensureCanvasRect();

    // Add new touches with precise coordinate handling
    for (let i = 0; i < e.changedTouches.length; i++) {
      const touch = e.changedTouches[i];
      const preciseCoords = this.getPreciseCoordinates(touch);
      
      this.touches.set(touch.identifier, {
        id: touch.identifier,
        x: preciseCoords.x,
        y: preciseCoords.y,
        timestamp: now,
        pressure: touch.force || 0.5
      });
      
      this.logger.debug('Touch start with precise coords', {
        touchId: touch.identifier,
        raw: { x: touch.clientX, y: touch.clientY },
        precise: preciseCoords
      });
    }

    this.updateGestureState();
    this.logger.debug('Touch start', {
      touchCount: this.touches.size,
      gestureState: this.gestureState
    });

    // Start gesture timing
    if (this.touches.size === 1) {
      this.gestureStartTime = now;
      this.startLongPressTimer();
    }

    // Handle pinch start
    if (this.touches.size === 2) {
      this.cancelLongPress();
      const touchArray = Array.from(this.touches.values());
      this.initialPinchDistance = this.calculateDistance(touchArray[0], touchArray[1]);
      this.lastGestureCenter = this.calculateCenter(touchArray);
    }
  }

  private handleTouchMove(e: TouchEvent): void {
    e.preventDefault();
    
    const now = Date.now();
    
    // Throttle events for performance
    if (now - this.lastEventTime < this.EVENT_THROTTLE) {
      return;
    }
    this.lastEventTime = now;

    // Update touch positions with precise coordinates
    for (let i = 0; i < e.changedTouches.length; i++) {
      const touch = e.changedTouches[i];
      const existingTouch = this.touches.get(touch.identifier);
      
      if (existingTouch) {
        const preciseCoords = this.getPreciseCoordinates(touch);
        
        this.touches.set(touch.identifier, {
          ...existingTouch,
          x: preciseCoords.x,
          y: preciseCoords.y,
          timestamp: now
        });
      }
    }

    this.processGesture();
  }

  private handleTouchEnd(e: TouchEvent): void {
    e.preventDefault();
    
    const now = Date.now();
    const endedTouches: TouchInput[] = [];

    // Remove ended touches with final precise coordinates
    for (let i = 0; i < e.changedTouches.length; i++) {
      const touch = e.changedTouches[i];
      const existingTouch = this.touches.get(touch.identifier);
      
      if (existingTouch) {
        const preciseCoords = this.getPreciseCoordinates(touch);
        
        endedTouches.push({
          ...existingTouch,
          x: preciseCoords.x,
          y: preciseCoords.y,
          timestamp: now
        });
        this.touches.delete(touch.identifier);
      }
    }

    this.cancelLongPress();

    // Process final gesture
    if (this.touches.size === 0) {
      this.finalizeGesture(endedTouches);
      this.resetGestureState();
    } else {
      this.updateGestureState();
    }

    this.logger.debug('Touch end', {
      remainingTouches: this.touches.size,
      gestureState: this.gestureState
    });
  }

  private handleTouchCancel(e: TouchEvent): void {
    this.logger.debug('Touch cancelled');
    this.touches.clear();
    this.cancelLongPress();
    this.resetGestureState();
  }

  // ===== MOUSE EVENT HANDLERS (for desktop testing) =====

  private handleMouseDown(e: MouseEvent): void {
    // Simulate touch for testing
    this.touches.set(0, {
      id: 0,
      x: e.clientX,
      y: e.clientY,
      timestamp: Date.now()
    });
    
    this.gestureStartTime = Date.now();
    this.updateGestureState();
  }

  private handleMouseMove(e: MouseEvent): void {
    if (this.touches.has(0)) {
      this.touches.set(0, {
        id: 0,
        x: e.clientX,
        y: e.clientY,
        timestamp: Date.now()
      });
      
      this.processGesture();
    }
  }

  private handleMouseUp(e: MouseEvent): void {
    const touch = this.touches.get(0);
    if (touch) {
      this.finalizeGesture([{
        ...touch,
        x: e.clientX,
        y: e.clientY,
        timestamp: Date.now()
      }]);
    }
    
    this.touches.clear();
    this.resetGestureState();
  }

  private handleWheel(e: WheelEvent): void {
    e.preventDefault();
    
    // Convert wheel to zoom gesture
    const zoomDelta = -e.deltaY * 0.001;
    const center: ScreenCoordinate = { x: e.clientX, y: e.clientY };
    
    this.dispatchGesture({
      type: 'pinch',
      startTime: Date.now(),
      duration: 0,
      center,
      data: {
        scale: 1 + zoomDelta,
        scaleCenter: center
      }
    });
  }

  // ===== GESTURE PROCESSING =====

  private updateGestureState(): void {
    const touchCount = this.touches.size;
    
    if (touchCount === 0) {
      this.gestureState = 'idle';
    } else if (touchCount === 1) {
      this.gestureState = 'tap_pending';
    } else if (touchCount === 2) {
      this.gestureState = 'zoom';
    } else {
      this.gestureState = 'pan'; // 3+ fingers = pan
    }
  }

  private resetGestureState(): void {
    this.gestureState = 'idle';
    this.gestureStartTime = 0;
    this.initialPinchDistance = 0;
  }

  private processGesture(): void {
    const now = Date.now();
    const touchArray = Array.from(this.touches.values());
    
    if (touchArray.length === 0) {
      return;
    }

    const currentCenter = this.calculateCenter(touchArray);
    
    if (this.gestureState === 'zoom' && touchArray.length === 2) {
      // Handle pinch/zoom
      const currentDistance = this.calculateDistance(touchArray[0], touchArray[1]);
      const scale = currentDistance / this.initialPinchDistance;
      
      this.dispatchGesture({
        type: 'pinch',
        startTime: this.gestureStartTime,
        duration: now - this.gestureStartTime,
        center: currentCenter,
        data: {
          scale,
          scaleCenter: currentCenter
        }
      });
      
      this.lastGestureCenter = currentCenter;
    } else if (this.gestureState === 'tap_pending' && touchArray.length === 1) {
      // Check if this is a pan gesture
      const touch = touchArray[0];
      const startTouch = this.findInitialTouch(touch.id);
      
      if (startTouch) {
        const distance = this.calculateDistance(touch, startTouch);
        
        if (distance > this.PAN_THRESHOLD) {
          this.gestureState = 'pan';
          this.cancelLongPress();
          
          const deltaX = touch.x - this.lastGestureCenter.x;
          const deltaY = touch.y - this.lastGestureCenter.y;
          
          this.dispatchGesture({
            type: 'pan',
            startTime: this.gestureStartTime,
            duration: now - this.gestureStartTime,
            center: currentCenter,
            data: {
              deltaX,
              deltaY,
              velocityX: deltaX / this.EVENT_THROTTLE,
              velocityY: deltaY / this.EVENT_THROTTLE
            }
          });
        }
      }
      
      this.lastGestureCenter = currentCenter;
    } else if (this.gestureState === 'pan') {
      // Continue pan gesture
      const deltaX = currentCenter.x - this.lastGestureCenter.x;
      const deltaY = currentCenter.y - this.lastGestureCenter.y;
      
      this.dispatchGesture({
        type: 'pan',
        startTime: this.gestureStartTime,
        duration: now - this.gestureStartTime,
        center: currentCenter,
        data: {
          deltaX,
          deltaY,
          velocityX: deltaX / this.EVENT_THROTTLE,
          velocityY: deltaY / this.EVENT_THROTTLE
        }
      });
      
      this.lastGestureCenter = currentCenter;
    }
  }

  private finalizeGesture(endedTouches: TouchInput[]): void {
    const now = Date.now();
    const gestureDuration = now - this.gestureStartTime;
    
    if (this.gestureState === 'tap_pending' && endedTouches.length === 1) {
      const touch = endedTouches[0];
      const startTouch = this.findInitialTouch(touch.id);
      
      if (startTouch) {
        const distance = this.calculateDistance(touch, startTouch);
        const center = { x: touch.x, y: touch.y };
        
        if (distance < this.TAP_THRESHOLD && gestureDuration < this.TAP_TIME_LIMIT) {
          // Valid tap gesture
          this.dispatchGesture({
            type: 'tap',
            startTime: this.gestureStartTime,
            duration: gestureDuration,
            center,
            data: {
              tapCount: 1
            }
          });
          
          this.triggerHaptic('light');
        }
      }
    }
  }

  // ===== HELPER METHODS =====

  /**
   * Get precise coordinates for mobile touch events
   * Handles Visual Viewport API, device pixel ratio, and canvas positioning
   */
  private getPreciseCoordinates(touch: Touch): ScreenCoordinate {
    let x = touch.clientX;
    let y = touch.clientY;
    
    // Apply Visual Viewport offset for mobile browser chrome handling
    if (this.visualViewportSupport && window.visualViewport) {
      x += window.visualViewport.offsetLeft;
      y += window.visualViewport.offsetTop;
    }
    
    // Apply device pixel ratio correction if needed
    // Note: clientX/Y are already in CSS pixels, so we typically don't need DPR correction
    // But we keep track of it for debugging purposes
    
    return { x, y };
  }
  
  /**
   * Ensure canvas rect is up to date for coordinate calculations
   */
  private ensureCanvasRect(): void {
    const now = Date.now();
    if (now - this.rectUpdateTime > 100 || !this.lastCanvasRect) { // 100ms cache
      this.lastCanvasRect = this.canvas.getBoundingClientRect();
      this.rectUpdateTime = now;
      
      this.logger.debug('Canvas rect updated for input', {
        rect: this.lastCanvasRect,
        visualViewport: this.visualViewportSupport && window.visualViewport ? {
          width: window.visualViewport.width,
          height: window.visualViewport.height,
          offsetLeft: window.visualViewport.offsetLeft,
          offsetTop: window.visualViewport.offsetTop
        } : null
      });
    }
  }
  
  /**
   * Validate touch coordinates for mobile edge cases
   */
  private validateTouchCoordinate(coord: ScreenCoordinate): {
    isValid: boolean;
    corrected?: ScreenCoordinate;
    issues: string[];
  } {
    const issues: string[] = [];
    let corrected = { ...coord };
    
    // Check for invalid values
    if (!isFinite(coord.x) || !isFinite(coord.y)) {
      issues.push('Non-finite coordinates');
      return { isValid: false, issues };
    }
    
    // Check for negative coordinates (can happen on some mobile browsers)
    if (coord.x < 0 || coord.y < 0) {
      issues.push('Negative coordinates');
      corrected.x = Math.max(0, corrected.x);
      corrected.y = Math.max(0, corrected.y);
    }
    
    // Check for suspiciously large coordinates
    const maxX = window.screen.width * 2;
    const maxY = window.screen.height * 2;
    
    if (coord.x > maxX || coord.y > maxY) {
      issues.push('Coordinates too large');
      corrected.x = Math.min(corrected.x, maxX);
      corrected.y = Math.min(corrected.y, maxY);
    }
    
    return {
      isValid: issues.length === 0,
      corrected: issues.length > 0 ? corrected : undefined,
      issues
    };
  }

  private calculateDistance(touch1: TouchInput, touch2: TouchInput): number {
    const dx = touch1.x - touch2.x;
    const dy = touch1.y - touch2.y;
    return Math.sqrt(dx * dx + dy * dy);
  }

  private calculateCenter(touches: TouchInput[]): ScreenCoordinate {
    if (touches.length === 0) {
      return { x: 0, y: 0 };
    }
    
    const sum = touches.reduce(
      (acc, touch) => ({ x: acc.x + touch.x, y: acc.y + touch.y }),
      { x: 0, y: 0 }
    );
    
    return {
      x: sum.x / touches.length,
      y: sum.y / touches.length
    };
  }

  private findInitialTouch(id: number): TouchInput | null {
    // In a more complete implementation, we'd track initial touch positions
    // For now, we'll use the current touch as approximation
    return this.touches.get(id) || null;
  }

  private startLongPressTimer(): void {
    this.longPressTimer = window.setTimeout(() => {
      if (this.gestureState === 'tap_pending') {
        const touchArray = Array.from(this.touches.values());
        if (touchArray.length === 1) {
          const center = { x: touchArray[0].x, y: touchArray[0].y };
          
          this.dispatchGesture({
            type: 'long_press',
            startTime: this.gestureStartTime,
            duration: this.LONG_PRESS_TIME,
            center,
            data: {}
          });
          
          this.triggerHaptic('medium');
        }
      }
    }, this.LONG_PRESS_TIME);
  }

  private cancelLongPress(): void {
    if (this.longPressTimer) {
      clearTimeout(this.longPressTimer);
      this.longPressTimer = null;
    }
  }

  private dispatchGesture(gesture: TouchGesture): void {
    const inputEvent: InputEvent = {
      type: 'input',
      timestamp: Date.now(),
      data: {
        gesture
      }
    };

    this.eventTarget.dispatchEvent(new CustomEvent('input', {
      detail: inputEvent.data
    }));

    this.logger.debug('Gesture dispatched', {
      type: gesture.type,
      center: gesture.center,
      duration: gesture.duration
    });
  }

  private triggerHaptic(type: HapticFeedback['type']): void {
    if ('vibrate' in navigator) {
      const patterns = {
        light: 25,
        medium: 50,
        heavy: 100
      };
      
      navigator.vibrate(patterns[type]);
    }
  }

  // ===== PUBLIC UTILITIES =====

  /**
   * Check if device supports haptic feedback
   */
  supportsHaptics(): boolean {
    return 'vibrate' in navigator;
  }

  /**
   * Get comprehensive touch capability information
   */
  getTouchCapabilities(): {
    maxTouchPoints: number;
    touchSupported: boolean;
    pressureSupported: boolean;
    pointerSupported: boolean;
    hoverSupported: boolean;
  } {
    let pressureSupported = false;
    try {
      // Try to detect pressure support safely
      const testEvent = new TouchEvent('touchstart', {
        touches: [{
          identifier: 0,
          target: this.canvas,
          clientX: 0,
          clientY: 0,
          force: 1
        } as any]
      });
      pressureSupported = 'force' in testEvent.touches[0];
    } catch (e) {
      // Fallback: assume no pressure support
      pressureSupported = false;
    }
    
    return {
      maxTouchPoints: navigator.maxTouchPoints || 0,
      touchSupported: 'ontouchstart' in window,
      pressureSupported,
      pointerSupported: 'onpointerdown' in window,
      hoverSupported: window.matchMedia('(hover: hover)').matches
    };
  }

  /**
   * Enhanced debug information for mobile troubleshooting
   */
  getDebugInfo(): any {
    return {
      touches: Array.from(this.touches.values()),
      gestureState: this.gestureState,
      touchMode: this.touchMode,
      capabilities: this.getTouchCapabilities(),
      gestureStartTime: this.gestureStartTime,
      lastGestureCenter: this.lastGestureCenter,
      mobile: {
        devicePixelRatio: this.devicePixelRatio,
        visualViewportSupport: this.visualViewportSupport,
        visualViewport: this.visualViewportSupport && window.visualViewport ? {
          width: window.visualViewport.width,
          height: window.visualViewport.height,
          offsetLeft: window.visualViewport.offsetLeft,
          offsetTop: window.visualViewport.offsetTop,
          pageLeft: window.visualViewport.pageLeft,
          pageTop: window.visualViewport.pageTop,
          scale: window.visualViewport.scale
        } : null,
        canvasRect: this.lastCanvasRect,
        rectUpdateTime: this.rectUpdateTime,
        windowSize: {
          innerWidth: window.innerWidth,
          innerHeight: window.innerHeight,
          outerWidth: window.outerWidth,
          outerHeight: window.outerHeight
        },
        screen: {
          width: window.screen.width,
          height: window.screen.height,
          availWidth: window.screen.availWidth,
          availHeight: window.screen.availHeight,
          orientation: window.screen.orientation?.angle || 'unknown'
        }
      },
      thresholds: {
        TAP_THRESHOLD: this.TAP_THRESHOLD,
        TAP_TIME_LIMIT: this.TAP_TIME_LIMIT,
        LONG_PRESS_TIME: this.LONG_PRESS_TIME,
        MIN_PINCH_DISTANCE: this.MIN_PINCH_DISTANCE,
        PAN_THRESHOLD: this.PAN_THRESHOLD,
        EVENT_THROTTLE: this.EVENT_THROTTLE
      },
      performance: {
        lastEventTime: this.lastEventTime,
        currentTime: Date.now(),
        timeSinceLastEvent: Date.now() - this.lastEventTime
      }
    };
  }

  /**
   * Cleanup method
   */
  destroy(): void {
    this.cancelLongPress();
    
    // Remove event listeners
    this.canvas.removeEventListener('touchstart', this.handleTouchStart);
    this.canvas.removeEventListener('touchmove', this.handleTouchMove);
    this.canvas.removeEventListener('touchend', this.handleTouchEnd);
    this.canvas.removeEventListener('touchcancel', this.handleTouchCancel);
    this.canvas.removeEventListener('mousedown', this.handleMouseDown);
    this.canvas.removeEventListener('mousemove', this.handleMouseMove);
    this.canvas.removeEventListener('mouseup', this.handleMouseUp);
    this.canvas.removeEventListener('wheel', this.handleWheel);
    this.canvas.removeEventListener('contextmenu', (e) => e.preventDefault());
  }
}