/**
 * Pixel War Configuration Module
 * Central configuration for the Pixel War application
 */

export const PixelWarConfig = {
    canvas: {
        defaultPixelSize: 10,
        minZoom: 0.5,
        maxZoom: 5,
        defaultZoom: 1,
        gridThreshold: 0.7  // Show grid when zoom > this value
    },
    animation: {
        friction: 0.92,
        smoothness: 0.15,
        momentumThreshold: 0.5,
        maxFPS: 60,
        throttleMs: 16  // ~60fps throttling
    },
    api: {
        updateInterval: 2000,
        retryAttempts: 3,
        endpoints: {
            placePixel: '/vibe-coding/api/place-pixel/',
            canvasState: '/vibe-coding/api/canvas-state/',
            pixelHistory: '/vibe-coding/api/pixel-history/'
        }
    },
    notifications: {
        duration: 3000,
        types: {
            SUCCESS: 'success',
            ERROR: 'error',
            INFO: 'info',
            WARNING: 'warning'
        }
    }
};