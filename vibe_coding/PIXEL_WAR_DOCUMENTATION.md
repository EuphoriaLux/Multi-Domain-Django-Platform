# Pixel War Documentation

## Overview
Pixel War is a collaborative canvas application where users can place colored pixels on a shared grid. The system implements rate limiting, user authentication benefits, and real-time updates.

## Architecture

### Backend Components

#### Models (`models.py`)

##### PixelCanvas
- **Purpose**: Represents the main canvas/grid
- **Key Fields**:
  - `width/height`: Canvas dimensions (10-500 pixels)
  - `is_active`: Enable/disable pixel placement
  - Rate limiting configuration:
    - `anonymous_cooldown_seconds`: Cooldown for anonymous users (default: 30s)
    - `registered_cooldown_seconds`: Cooldown for registered users (default: 12s)
    - `registered_pixels_per_minute`: Max pixels/minute for registered (default: 5)
    - `anonymous_pixels_per_minute`: Max pixels/minute for anonymous (default: 2)

##### Pixel
- **Purpose**: Individual pixel on the canvas
- **Key Fields**:
  - `canvas`: Foreign key to PixelCanvas
  - `x, y`: Coordinates
  - `color`: Hex color value
  - `placed_by`: User who placed it (nullable)
  - `placed_at`: Timestamp
- **Constraints**: Unique together on (canvas, x, y)

##### PixelHistory
- **Purpose**: Track all pixel placements for activity feed
- **Key Fields**: Similar to Pixel but without unique constraint
- **Ordering**: Newest first (`-placed_at`)

##### UserPixelCooldown
- **Purpose**: Track rate limiting per user/session
- **Key Fields**:
  - `user`: Registered user (nullable)
  - `session_key`: For anonymous users
  - `pixels_placed_last_minute`: Counter for rate limiting
  - `last_minute_reset`: Timestamp for counter reset
- **Methods**:
  - `get_cooldown_seconds()`: Returns appropriate cooldown based on user type
  - `get_max_pixels_per_minute()`: Returns pixel limit based on user type

##### UserPixelStats
- **Purpose**: Track user statistics
- **Key Fields**:
  - `total_pixels_placed`: Lifetime pixel count
  - `last_pixel_placed`: Last activity timestamp

#### Views (`views.py`)

##### pixel_war (Main View)
- **Route**: `/vibe-coding/pixel-war/`
- **Purpose**: Renders the main canvas page
- **Process**:
  1. Gets or creates canvas with default settings
  2. Retrieves user stats if authenticated
  3. Passes configuration to template

##### place_pixel (API Endpoint)
- **Route**: `/vibe-coding/api/place-pixel/`
- **Method**: POST
- **Rate Limiting Logic**:
  1. Creates/retrieves cooldown record
  2. Checks if minute counter needs reset (60s window)
  3. Verifies pixels_placed < max_per_minute
  4. No per-pixel cooldown (users can place all remaining pixels immediately)
  5. Updates pixel and history
- **Response**: Success with pixel data or 429 rate limit error

##### get_canvas_state
- **Route**: `/vibe-coding/api/canvas-state/{id}/`
- **Purpose**: Returns all pixels on canvas
- **Response**: Canvas metadata + pixel dictionary

##### get_pixel_history
- **Route**: `/vibe-coding/api/pixel-history/`
- **Purpose**: Returns recent pixel placements
- **Parameters**: `canvas_id`, `limit` (default 50)

### Frontend Components

#### JavaScript Classes

##### PixelWar (`pixel_war.js`) - Desktop Version
**Core Properties**:
- Canvas management: `zoom`, `offsetX/Y`, `pixelSize`
- Smooth scrolling: `targetOffsetX/Y`, `velocityX/Y`, `friction`, `smoothness`
- Game state: `selectedColor`, `pixels`, `cooldownEndTime`, `pixelsRemaining`

**Key Methods**:

###### Canvas Manipulation
- `setupCanvas()`: Initialize canvas dimensions
- `redraw()`: Complete canvas redraw with transformations
- `drawPixel(x, y, color)`: Draw single pixel
- `drawGrid()`: Draw grid lines (visible when zoomed)

###### Navigation & Controls
- `handleWheel(e)`: 
  - Ctrl+Scroll: Zoom
  - Normal Scroll: Pan
  - Shift+Scroll: Horizontal pan
- `handleMouseDown/Move/Up()`: 
  - Space+drag or right-click: Pan mode
  - Left-click: Place pixel
- `navigateToCorner(corner)`: Jump to canvas corners
- `constrainOffset(offset, gridSize, viewportSize)`: Boundary checking

###### Smooth Animation System
- `startSmoothScroll()`: Lerp-based smooth movement
- `applyMomentum()`: Physics-based momentum after drag
- `adjustZoomAtPoint(delta, pointX, pointY)`: Zoom toward mouse position

###### API Integration
- `placePixel(x, y)`: 
  - No cooldown check (handled server-side)
  - Sends POST with CSRF token
  - Updates local state on success
- `loadCanvasState()`: Fetch all pixels
- `loadRecentActivity()`: Fetch recent placements

###### Minimap System
- `setupMinimap()`: Create minimap UI
- `updateMinimap()`: Render pixels on minimap
- `updateMinimapViewport()`: Show current view area

##### PixelWarMobile (`pixel_war_mobile.js`) - Mobile Version
**Additional Mobile Features**:

###### Touch Handling
- `handleTouchStart/Move/End()`: 
  - Single finger: Drag to pan or tap to place
  - Two fingers: Pinch to zoom
  - Long press: Enhanced preview
- `getTouchDistance()`: Calculate pinch distance
- `smoothZoomToPixel()`: Auto-zoom animation for precision

###### Mobile UI Enhancements
- `showPixelPreview(x, y)`: Visual feedback before placement
- `createConfirmationUI()`: Confirmation dialog for pixel placement
- `showMagnifier()`: 5x5 grid magnified view
- `showLimitReachedDialog()`: Rate limit information
- `showRegistrationPrompt()`: Encourage registration

###### Mobile-Specific Methods
- `detectColorScheme()`: Dark mode support
- `toggleMobileMenu()`: Slide-out menu
- `placePixelFromButton()`: Place from UI button
- `shakeCanvas()`: Error feedback animation

## Bug Analysis & Issues

### Critical Bugs

1. **Database Constraint Issue** (`models.py:67`)
   - `unique_together = ('user', 'canvas', 'session_key')` causes problems
   - When user=None for anonymous, multiple sessions can't be tracked
   - Fix: Remove user from unique constraint for anonymous users

2. **API URL Construction** (`pixel_war.js:42-51`)
   - `getApiBaseUrl()` always returns empty string
   - Doesn't properly handle language prefixes
   - Fix: Should return proper base URL or handle prefixes correctly

3. **Color Format Inconsistency**
   - RGB vs Hex conversion issues in mobile version
   - `rgbToHex()` doesn't handle all color formats

4. **Coordinate Calculation Issues**
   - Offset calculations inconsistent between desktop/mobile
   - Negative offset handling differs in methods

### Performance Issues

1. **Update Loop Frequency** 
   - Updates every 2 seconds regardless of activity
   - Loads full canvas state repeatedly
   - Fix: Implement WebSocket or differential updates

2. **Redraw Optimization**
   - Full canvas redraw on every frame
   - No dirty rectangle optimization
   - Fix: Only redraw changed areas

3. **Memory Leaks**
   - Animation frames not always cancelled
   - Event listeners not cleaned up
   - Timer intervals persist

### UX Issues

1. **Mobile Precision**
   - Touch targets too small at default zoom
   - Auto-zoom sometimes jarring
   - Preview system could be clearer

2. **Rate Limiting Feedback**
   - Unclear messaging about limits
   - Cooldown vs rate limit confusion
   - Missing visual countdown

3. **Canvas Navigation**
   - Momentum can overshoot boundaries
   - Zoom constraints not enforced properly
   - Minimap click accuracy issues

## Recommended Improvements

### Code Organization

1. **Separate Concerns**
   ```javascript
   // Split into modules
   - CanvasRenderer.js (drawing logic)
   - InputHandler.js (mouse/touch events)
   - APIClient.js (server communication)
   - RateLimiter.js (cooldown management)
   - UIController.js (UI updates)
   ```

2. **Use Modern Patterns**
   - Convert to ES6 modules
   - Use async/await consistently
   - Implement event emitter pattern
   - Add TypeScript for type safety

3. **Configuration Management**
   ```javascript
   const CONFIG = {
     canvas: {
       defaultZoom: 1,
       minZoom: 0.5,
       maxZoom: 5,
       pixelSize: 10
     },
     animation: {
       friction: 0.92,
       smoothness: 0.15
     },
     api: {
       updateInterval: 2000,
       retryAttempts: 3
     }
   };
   ```

### Performance Optimizations

1. **Implement WebSocket**
   ```python
   # Use Django Channels for real-time updates
   class PixelConsumer(AsyncWebsocketConsumer):
       async def pixel_placed(self, event):
           await self.send(json.dumps(event['pixel']))
   ```

2. **Canvas Optimization**
   ```javascript
   // Use offscreen canvas for double buffering
   const offscreenCanvas = document.createElement('canvas');
   const offscreenCtx = offscreenCanvas.getContext('2d');
   
   // Dirty rectangle tracking
   class DirtyRectManager {
       markDirty(x, y, width, height) { /* ... */ }
       redrawDirtyRegions() { /* ... */ }
   }
   ```

3. **Implement Quadtree**
   ```javascript
   // Spatial indexing for pixel lookups
   class Quadtree {
       insert(pixel) { /* ... */ }
       query(bounds) { /* ... */ }
   }
   ```

### Feature Enhancements

1. **Add Undo System**
   ```javascript
   class UndoManager {
       pushState(pixel) { /* ... */ }
       undo() { /* ... */ }
       redo() { /* ... */ }
   }
   ```

2. **Template System**
   ```javascript
   // Allow users to place patterns
   class TemplateManager {
       loadTemplate(url) { /* ... */ }
       previewTemplate(x, y) { /* ... */ }
       placeTemplate() { /* ... */ }
   }
   ```

3. **Collaboration Tools**
   ```javascript
   // Show other users' cursors
   class CollaborationManager {
       broadcastCursor(x, y) { /* ... */ }
       renderPeerCursors() { /* ... */ }
   }
   ```

## Testing Recommendations

### Unit Tests
```python
# test_pixel_placement.py
class TestPixelPlacement(TestCase):
    def test_rate_limiting(self):
        # Test anonymous vs registered limits
        pass
    
    def test_boundary_validation(self):
        # Test coordinate constraints
        pass
    
    def test_cooldown_reset(self):
        # Test minute counter reset
        pass
```

### Frontend Tests
```javascript
// Use Jest for JavaScript testing
describe('PixelWar', () => {
    test('coordinate transformation', () => {
        // Test canvas to grid conversion
    });
    
    test('zoom constraints', () => {
        // Test zoom boundaries
    });
    
    test('rate limit handling', () => {
        // Test 429 response handling
    });
});
```

### Integration Tests
- Selenium tests for user flows
- Load testing for concurrent users
- Mobile device testing matrix

## Security Considerations

1. **Rate Limiting**: Current implementation good but could add IP-based limits
2. **CSRF Protection**: Properly implemented
3. **Input Validation**: Add color format validation
4. **SQL Injection**: Protected by ORM
5. **XSS**: Add HTML escaping for usernames

## Deployment Checklist

- [ ] Configure WebSocket support
- [ ] Set up CDN for static assets
- [ ] Implement monitoring (pixel/minute metrics)
- [ ] Add error tracking (Sentry)
- [ ] Database indexing on frequently queried fields
- [ ] Implement caching strategy
- [ ] Add rate limiting at reverse proxy level