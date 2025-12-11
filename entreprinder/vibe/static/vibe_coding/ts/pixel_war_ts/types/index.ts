/**
 * Core TypeScript types for Pixel War
 * Provides type safety and clearer interfaces
 */

// ===== COORDINATE SYSTEM =====
export interface GridCoordinate {
  readonly x: number;
  readonly y: number;
}

export interface ScreenCoordinate {
  readonly x: number;
  readonly y: number;
}

export interface CanvasCoordinate {
  readonly x: number;
  readonly y: number;
}

// ===== VIEWPORT MANAGEMENT =====
export interface ViewportState {
  readonly zoom: number;
  readonly offsetX: number;
  readonly offsetY: number;
  readonly bounds: BoundingBox;
}

export interface BoundingBox {
  readonly minX: number;
  readonly maxX: number;
  readonly minY: number;
  readonly maxY: number;
}

export interface ViewportConstraints {
  readonly minZoom: number;
  readonly maxZoom: number;
  readonly gridWidth: number;
  readonly gridHeight: number;
  readonly pixelSize: number;
}

// ===== INPUT HANDLING =====
export interface TouchInput {
  readonly id: number;
  readonly x: number;
  readonly y: number;
  readonly timestamp: number;
  readonly pressure?: number;
}

export interface TouchGesture {
  readonly type: 'tap' | 'pan' | 'pinch' | 'long_press';
  readonly startTime: number;
  readonly duration: number;
  readonly center: ScreenCoordinate;
  readonly data: TouchGestureData;
}

export interface TouchGestureData {
  // For pan gestures
  readonly deltaX?: number;
  readonly deltaY?: number;
  readonly velocityX?: number;
  readonly velocityY?: number;
  
  // For pinch gestures
  readonly scale?: number;
  readonly scaleCenter?: ScreenCoordinate;
  
  // For tap gestures
  readonly tapCount?: number;
}

export type TouchMode = 'tap' | 'precision';

export type GestureState = 'idle' | 'pan' | 'zoom' | 'tap_pending';

// ===== RENDERING =====
export interface PixelData {
  readonly x: number;
  readonly y: number;
  readonly color: string;
  readonly placedBy?: string;
  readonly timestamp?: number;
}

export interface RenderOptions {
  readonly showGrid: boolean;
  readonly showCrosshair: boolean;
  readonly highlightPixel?: GridCoordinate;
}

// ===== API INTEGRATION =====
export interface PixelWarConfig {
  readonly canvasId: number;
  readonly gridWidth: number;
  readonly gridHeight: number;
  readonly pixelSize: number;
  readonly isAuthenticated: boolean;
  readonly cooldownSeconds: number;
  readonly pixelsPerMinute: number;
}

export interface APIResponse<T = any> {
  readonly success: boolean;
  readonly data?: T;
  readonly message?: string;
  readonly error?: string;
}

export interface PixelPlacementResponse {
  readonly pixel: PixelData;
  readonly cooldownInfo: {
    readonly remaining: number;
    readonly nextPixelAt: number;
    readonly pixelsRemaining: number;
  };
}

// ===== EVENT SYSTEM =====
export interface PixelWarEvent {
  readonly type: string;
  readonly timestamp: number;
  readonly data: any;
}

export interface ViewportChangeEvent extends PixelWarEvent {
  readonly type: 'viewport_change';
  readonly data: {
    readonly oldState: ViewportState;
    readonly newState: ViewportState;
    readonly source: 'user_input' | 'api_update' | 'constraint';
  };
}

export interface PixelPlaceEvent extends PixelWarEvent {
  readonly type: 'pixel_place';
  readonly data: {
    readonly coordinate: GridCoordinate;
    readonly color: string;
    readonly source: 'user' | 'api';
  };
}

export interface InputEvent extends PixelWarEvent {
  readonly type: 'input';
  readonly data: {
    readonly gesture: TouchGesture;
    readonly coordinate?: GridCoordinate;
  };
}

// ===== UTILITY TYPES =====
export type Immutable<T> = {
  readonly [P in keyof T]: T[P] extends object ? Immutable<T[P]> : T[P];
};

export interface Logger {
  readonly debug: (message: string, data?: any) => void;
  readonly info: (message: string, data?: any) => void;
  readonly warn: (message: string, data?: any) => void;
  readonly error: (message: string, data?: any) => void;
}

export interface Performance {
  readonly startTime: number;
  readonly frameCount: number;
  readonly averageFPS: number;
  readonly lastFrameTime: number;
}

// ===== MOBILE SPECIFIC =====
export interface MobileViewport {
  readonly effectiveWidth: number;
  readonly effectiveHeight: number;
  readonly safeAreaInsets: {
    readonly top: number;
    readonly bottom: number;
    readonly left: number;
    readonly right: number;
  };
  readonly orientation: 'portrait' | 'landscape';
}

export interface HapticFeedback {
  readonly type: 'light' | 'medium' | 'heavy';
  readonly supported: boolean;
}

// ===== ERROR HANDLING =====
export class PixelWarError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly context?: any
  ) {
    super(message);
    this.name = 'PixelWarError';
  }
}

export class CoordinateError extends PixelWarError {
  constructor(message: string, public readonly coordinate: ScreenCoordinate | GridCoordinate) {
    super(message, 'COORDINATE_ERROR', coordinate);
  }
}

export class ViewportError extends PixelWarError {
  constructor(message: string, public readonly viewport: ViewportState) {
    super(message, 'VIEWPORT_ERROR', viewport);
  }
}