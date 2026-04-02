/**
 * Canvas Renderer Module
 * Handles all canvas rendering operations for the Pixel War application
 */

import { PixelWarConfig } from '../config/pixel-war-config.js';

export class CanvasRenderer {
    constructor(canvas, config) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.config = config;
        this.pixels = {};
        this.dirtyRegions = new Set();
        
        // Offscreen canvas for double buffering
        this.offscreenCanvas = document.createElement('canvas');
        this.offscreenCtx = this.offscreenCanvas.getContext('2d');
    }

    setup(width, height, pixelSize) {
        this.width = width;
        this.height = height;
        this.pixelSize = pixelSize;
        
        this.canvas.width = width * pixelSize;
        this.canvas.height = height * pixelSize;
        this.offscreenCanvas.width = this.canvas.width;
        this.offscreenCanvas.height = this.canvas.height;
    }

    setPixels(pixels) {
        this.pixels = pixels;
        this.markAllDirty();
    }

    updatePixel(x, y, color, placedBy) {
        const key = `${x},${y}`;
        this.pixels[key] = { color, placed_by: placedBy };
        this.markDirty(x, y);
    }

    markDirty(x, y) {
        this.dirtyRegions.add(`${x},${y}`);
    }

    markAllDirty() {
        this.dirtyRegions.clear();
        this.dirtyRegions.add('all');
    }

    render(offsetX, offsetY, zoom, showGrid) {
        // Clear canvas first
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        // Calculate viewport in grid coordinates (pixels in the grid)
        const viewportX = -offsetX;
        const viewportY = -offsetY;
        const viewportWidth = this.canvas.width / (zoom * this.pixelSize);
        const viewportHeight = this.canvas.height / (zoom * this.pixelSize);
        const margin = Math.max(10, Math.ceil(50 / zoom)); // Dynamic margin based on zoom

        // Setup transform
        this.ctx.save();
        this.ctx.scale(zoom, zoom);
        this.ctx.translate(offsetX * this.pixelSize, offsetY * this.pixelSize);

        // Draw the outside/void area background (before pixel map area)
        this.drawVoidBackground(viewportX, viewportY, viewportWidth, viewportHeight, zoom);

        // Draw pixel map background - only within the actual map boundaries (0,0 to width,height)
        this.ctx.fillStyle = '#ffffff';
        const bgStartX = Math.max(0, Math.floor(viewportX - margin));
        const bgStartY = Math.max(0, Math.floor(viewportY - margin));
        const bgEndX = Math.min(this.width, Math.ceil(viewportX + viewportWidth + margin));
        const bgEndY = Math.min(this.height, Math.ceil(viewportY + viewportHeight + margin));
        
        // Only draw background within map bounds - don't draw over void areas
        if (bgEndX > bgStartX && bgEndY > bgStartY) {
            const bgX = bgStartX * this.pixelSize;
            const bgY = bgStartY * this.pixelSize;
            const bgWidth = (bgEndX - bgStartX) * this.pixelSize;
            const bgHeight = (bgEndY - bgStartY) * this.pixelSize;
            
            this.ctx.fillRect(bgX, bgY, bgWidth, bgHeight);
        }
        
        // Draw pixel map border
        this.drawPixelMapBorder(zoom);

        // Calculate pixel range to render
        const startX = Math.max(0, Math.floor(viewportX - margin));
        const endX = Math.min(this.width, Math.ceil(viewportX + viewportWidth + margin));
        const startY = Math.max(0, Math.floor(viewportY - margin));
        const endY = Math.min(this.height, Math.ceil(viewportY + viewportHeight + margin));

        // Batch pixel rendering for better performance
        this.ctx.beginPath();
        for (let x = startX; x < endX; x++) {
            for (let y = startY; y < endY; y++) {
                const key = `${x},${y}`;
                if (this.pixels[key]) {
                    this.ctx.fillStyle = this.pixels[key].color;
                    this.ctx.fillRect(
                        x * this.pixelSize,
                        y * this.pixelSize,
                        this.pixelSize,
                        this.pixelSize
                    );
                }
            }
        }

        // Draw grid if needed - only visible portion
        if (showGrid && zoom > PixelWarConfig.canvas.gridThreshold) {
            this.drawOptimizedGrid(startX, endX, startY, endY, zoom);
        }

        this.ctx.restore();
        this.dirtyRegions.clear();
    }

    drawOptimizedGrid(startX, endX, startY, endY, zoom = 1) {
        // Adjust grid opacity and line width based on zoom level
        const opacity = Math.min(0.5, Math.max(0.1, (zoom - 0.5) * 0.4));
        const lineWidth = Math.max(0.5, Math.min(2, zoom * 0.5));
        
        this.ctx.strokeStyle = `rgba(200, 200, 200, ${opacity})`;
        this.ctx.lineWidth = lineWidth;

        // Draw vertical lines - only visible portion
        this.ctx.beginPath();
        for (let x = startX; x <= endX; x++) {
            const lineX = x * this.pixelSize;
            this.ctx.moveTo(lineX, startY * this.pixelSize);
            this.ctx.lineTo(lineX, endY * this.pixelSize);
        }
        this.ctx.stroke();

        // Draw horizontal lines - only visible portion
        this.ctx.beginPath();
        for (let y = startY; y <= endY; y++) {
            const lineY = y * this.pixelSize;
            this.ctx.moveTo(startX * this.pixelSize, lineY);
            this.ctx.lineTo(endX * this.pixelSize, lineY);
        }
        this.ctx.stroke();
    }

    // Keep old method for compatibility
    drawGrid() {
        this.drawOptimizedGrid(0, this.width, 0, this.height);
    }

    drawVoidBackground(viewportX, viewportY, viewportWidth, viewportHeight, zoom) {
        // Draw a distinct pattern/color for the area outside the pixel map
        const voidAreas = this.calculateVoidAreas(viewportX, viewportY, viewportWidth, viewportHeight);
        
        // Debug void areas
        if (Math.random() < 0.1) {
            console.log('🌫️ Void Areas:', {
                viewport: `${viewportX.toFixed(1)}, ${viewportY.toFixed(1)}, ${viewportWidth.toFixed(1)}x${viewportHeight.toFixed(1)}`,
                areasCount: voidAreas.length,
                areas: voidAreas.map(area => `${area.x.toFixed(1)}, ${area.y.toFixed(1)}, ${area.width.toFixed(1)}x${area.height.toFixed(1)}`)
            });
        }
        
        // Create a subtle checkered pattern for the void area
        const patternSize = Math.max(20, 50 / zoom); // Scale pattern with zoom
        
        this.ctx.fillStyle = '#f0f0f0'; // Light gray base
        
        voidAreas.forEach(area => {
            // Fill base color
            this.ctx.fillRect(area.x * this.pixelSize, area.y * this.pixelSize, 
                             area.width * this.pixelSize, area.height * this.pixelSize);
            
            // Add diagonal stripes pattern
            this.ctx.save();
            this.ctx.fillStyle = '#e8e8e8';
            this.ctx.beginPath();
            
            // Draw diagonal stripes
            for (let x = area.x * this.pixelSize - patternSize; x < (area.x + area.width) * this.pixelSize + patternSize; x += patternSize * 2) {
                for (let y = area.y * this.pixelSize - patternSize; y < (area.y + area.height) * this.pixelSize + patternSize; y += patternSize * 2) {
                    this.ctx.rect(x, y, patternSize, patternSize);
                }
            }
            this.ctx.fill();
            this.ctx.restore();
        });
    }

    calculateVoidAreas(viewportX, viewportY, viewportWidth, viewportHeight) {
        const areas = [];
        
        // Calculate which parts of the viewport are outside the pixel map (0,0 to width,height)
        const viewportRight = viewportX + viewportWidth;
        const viewportBottom = viewportY + viewportHeight;
        
        // Left void area (viewport extends beyond left edge of map)
        if (viewportX < 0) {
            areas.push({
                x: viewportX,
                y: Math.max(viewportY, 0),
                width: Math.min(-viewportX, viewportWidth),
                height: Math.min(viewportHeight, Math.max(0, Math.min(this.height, viewportBottom) - Math.max(0, viewportY)))
            });
        }
        
        // Right void area (viewport extends beyond right edge of map)
        if (viewportRight > this.width) {
            areas.push({
                x: this.width,
                y: Math.max(viewportY, 0),
                width: viewportRight - this.width,
                height: Math.min(viewportHeight, Math.max(0, Math.min(this.height, viewportBottom) - Math.max(0, viewportY)))
            });
        }
        
        // Top void area (viewport extends beyond top edge of map)
        if (viewportY < 0) {
            areas.push({
                x: viewportX,
                y: viewportY,
                width: viewportWidth,
                height: -viewportY
            });
        }
        
        // Bottom void area (viewport extends beyond bottom edge of map)
        if (viewportBottom > this.height) {
            areas.push({
                x: viewportX,
                y: this.height,
                width: viewportWidth,
                height: viewportBottom - this.height
            });
        }
        
        return areas;
    }

    drawPixelMapBorder(zoom) {
        // Draw a clear border around the pixel map
        this.ctx.strokeStyle = '#333333';
        this.ctx.lineWidth = Math.max(2, 4 / zoom); // Scale border with zoom but keep visible
        this.ctx.setLineDash([]);
        
        // Draw border rectangle around the entire pixel map
        this.ctx.strokeRect(0, 0, this.width * this.pixelSize, this.height * this.pixelSize);
        
        // Add a subtle inner shadow effect
        this.ctx.strokeStyle = 'rgba(0, 0, 0, 0.1)';
        this.ctx.lineWidth = Math.max(1, 2 / zoom);
        this.ctx.strokeRect(-this.ctx.lineWidth, -this.ctx.lineWidth, 
                           (this.width * this.pixelSize) + (this.ctx.lineWidth * 2), 
                           (this.height * this.pixelSize) + (this.ctx.lineWidth * 2));
    }

    drawPixelPreview(x, y, color, zoom, offsetX, offsetY) {
        const pixelX = (x + offsetX) * this.pixelSize * zoom;
        const pixelY = (y + offsetY) * this.pixelSize * zoom;
        const size = this.pixelSize * zoom;

        this.ctx.save();
        this.ctx.fillStyle = color;
        this.ctx.globalAlpha = 0.6;
        this.ctx.fillRect(pixelX, pixelY, size, size);
        
        this.ctx.strokeStyle = color;
        this.ctx.lineWidth = 2;
        this.ctx.globalAlpha = 1;
        this.ctx.strokeRect(pixelX - 1, pixelY - 1, size + 2, size + 2);
        this.ctx.restore();
    }
}