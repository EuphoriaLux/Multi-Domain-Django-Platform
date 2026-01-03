# Pixel War - Modular JavaScript Architecture

This directory contains the refactored and modularized version of the Pixel War application, split from a single 3,800+ line file into focused modules for better maintainability.

## Directory Structure

```
pixel_war/
├── README.md                 # This documentation
├── pixel-war-app.js         # Main entry point and application coordinator
│
├── config/
│   └── pixel-war-config.js  # Application configuration constants
│
├── api/
│   └── pixel-war-api.js     # API communication layer (PixelWarAPI, APIError)
│
├── rendering/
│   └── canvas-renderer.js   # Canvas rendering and drawing operations
│
├── input/
│   └── input-handler.js     # Mouse, touch, and keyboard event handling
│
├── core/
│   ├── pixel-war.js         # Main application controller
│   ├── rate-limiter.js      # Rate limiting for pixel placement
│   └── notification-manager.js # Toast notifications and user feedback
│
└── mobile/
    ├── index.js              # Mobile module entry point and exports
    ├── README.md             # Mobile module documentation
    ├── mobile-utils.js       # Shared mobile utilities
    ├── onboarding.js         # First-time user tutorial system
    ├── mode-manager.js       # Touch mode management (tap/precision)
    ├── color-manager.js      # Enhanced color palette and recent colors
    ├── touch-feedback.js     # Visual and haptic feedback
    └── navigation.js         # Mobile navigation controls
```

## Module Responsibilities

### Core Application (`pixel-war-app.js`)
- Main entry point for the application
- Coordinates all modules and handles initialization
- Provides backward compatibility for global access
- Auto-detects canvas and initializes the application

### Configuration (`config/`)
- **pixel-war-config.js**: Centralized configuration for canvas, animation, API, and notification settings

### API Layer (`api/`)
- **pixel-war-api.js**: Handles all HTTP communication with the backend
  - `PixelWarAPI`: Main API client class
  - `APIError`: Custom error handling for API operations

### Rendering System (`rendering/`)
- **canvas-renderer.js**: All canvas drawing and pixel management
  - Viewport calculations and optimization
  - Grid rendering and visual effects
  - Double buffering and performance optimization

### Input System (`input/`)
- **input-handler.js**: Comprehensive input event handling
  - Mouse events (click, drag, wheel, hover)
  - Touch events with mobile optimization
  - Keyboard shortcuts and accessibility
  - Pinch-to-zoom and gesture recognition

### Core Logic (`core/`)
- **pixel-war.js**: Main application controller that coordinates all systems
- **rate-limiter.js**: Manages pixel placement rate limits and cooldowns
- **notification-manager.js**: Toast notification system with multiple types

### Mobile UX (`mobile/`)
- **onboarding.js**: Interactive tutorial for first-time mobile users
- **mode-manager.js**: Switch between tap and precision touch modes
- **color-manager.js**: Enhanced color palette with recent colors
- **touch-feedback.js**: Visual and haptic feedback for touch interactions
- **navigation.js**: Mobile-specific navigation controls
- **mobile-utils.js**: Shared utilities for device detection and debugging

## Usage

### Basic Integration
```html
<script type="module">
    import { initPixelWar } from '/static/vibe_coding/js/pixel_war/pixel-war-app.js';
    
    // Initialize with canvas element
    const canvas = document.getElementById('pixelCanvas');
    const pixelWar = initPixelWar(canvas, { canvasId: 1 });
</script>
```

### Advanced Usage
```javascript
import { 
    PixelWar, 
    CanvasRenderer, 
    InputHandler,
    PixelWarConfig 
} from '/static/vibe_coding/js/pixel_war/pixel-war-app.js';

// Create custom configuration
const customConfig = { ...PixelWarConfig, animation: { ...PixelWarConfig.animation, maxFPS: 30 } };

// Manual initialization with custom setup
const renderer = new CanvasRenderer(canvas, customConfig);
const inputHandler = new InputHandler(canvas, customConfig);
const pixelWar = new PixelWar(canvas, { config: customConfig });
```

## Migration from Monolithic Version

The modular version maintains **100% backward compatibility** with the original `pixel_war_refactored.js` file:

- All classes and functionality are preserved exactly
- Global window objects (`window.pixelWar`) are still available
- All HTML onclick handlers continue to work
- Mobile UX classes are globally accessible
- Debug functions remain available

## Benefits of Modular Architecture

1. **Maintainability**: Each file has a single, clear responsibility
2. **Testing**: Individual modules can be unit tested in isolation  
3. **Performance**: Modules can be lazy-loaded when needed
4. **Reusability**: Modules can be reused across different parts of the application
5. **Team Development**: Multiple developers can work on different modules simultaneously
6. **Debugging**: Easier to trace issues to specific modules
7. **Future-Proof**: Ready for TypeScript conversion and build process integration

## Development Workflow

### Adding New Features
1. Identify the appropriate module based on responsibility
2. Add the feature to the relevant module
3. Update exports if the feature needs external access
4. Update the main entry point if needed
5. Test the feature in isolation and integration

### Debugging
1. Use browser dev tools to identify which module contains the issue
2. Add debug logging to the specific module
3. Use the existing debug utility functions in `mobile-utils.js`
4. Leverage module isolation for focused testing

### Performance Monitoring
Each module includes performance considerations:
- Canvas rendering uses viewport culling and dirty region tracking
- Input handling includes throttling and debouncing
- API calls include retry logic and error handling
- Mobile interactions include haptic feedback and animation optimization

## Browser Support

The modular version uses ES6+ features:
- ES6 Modules (import/export)
- Classes and arrow functions
- Template literals and destructuring
- Modern event handling

**Minimum supported browsers:**
- Chrome 61+
- Firefox 60+  
- Safari 10.1+
- Edge 16+

For older browser support, consider using a bundler like Webpack or Vite with appropriate polyfills.