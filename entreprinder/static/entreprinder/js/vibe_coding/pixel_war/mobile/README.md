# Pixel War Mobile UX Modules

This directory contains the extracted mobile UX functionality from the main Pixel War application, organized into modular ES6 classes.

## Module Structure

### `mobile-utils.js`
Shared utility functions used across all mobile modules:
- `triggerHapticFeedback(type)` - Provides haptic feedback
- `getDeviceType()` - Determines device type (mobile/tablet/desktop)
- `isMobileDevice()` - Checks if current device is mobile/touch
- `trackEvent(name, properties)` - Analytics tracking
- `debugMobileViewport()` - Debug viewport issues
- `resetMobileOnboarding()` - Reset onboarding for testing
- And more utility functions...

### `onboarding.js` - PixelWarOnboarding
Handles first-time user experience and touch gesture tutorials:
- Multi-step onboarding flow
- Interactive gesture demonstrations
- Touch mode selection
- Progress tracking and analytics
- Skip and finish functionality

### `mode-manager.js` - PixelWarModeManager
Manages switching between tap and precision touch modes:
- Touch mode toggle (tap/precision)
- Mode persistence in localStorage
- Help tips and visual indicators
- Accidental placement tracking
- Mode suggestions based on user behavior

### `color-manager.js` - PixelWarColorManager
Enhanced color selection and management:
- Recent colors tracking (6 most recent)
- Enhanced color palette display
- Swipe gesture support for palette access
- Color selection with haptic feedback
- Persistent color history

### `touch-feedback.js` - PixelWarTouchFeedback
Provides visual and haptic feedback for touch interactions:
- Automatic touch feedback for UI elements
- Custom feedback types (light/medium/heavy)
- Long press detection
- Swipe gesture recognition
- Success/error feedback animations

### `navigation.js` - PixelWarNavigation
Enhanced navigation controls for mobile:
- Corner navigation (top-left, top-right, etc.)
- Zoom controls with feedback
- Boundary detection and feedback
- Directional navigation (swipe/keyboard)
- View state save/restore

### `index.js`
Main module entry point that:
- Exports all mobile classes
- Provides `initMobileUX()` function
- Sets up global compatibility
- Auto-initializes on page load

## Usage

### As ES6 Modules
```javascript
import { initMobileUX, PixelWarOnboarding, PixelWarModeManager } from './mobile/index.js';

// Initialize all mobile UX systems
initMobileUX({
    forceInit: false,           // Force init even on desktop
    skipOnboarding: false,      // Skip onboarding flow
    enableColorSwipeGesture: true, // Enable swipe-up for color palette
    debugMode: false            // Enable debug logging
});

// Use individual classes
PixelWarOnboarding.show();
PixelWarModeManager.toggleMode();
```

### Legacy Global Usage
The modules maintain backwards compatibility by exposing classes globally:
```javascript
// These are available globally after loading index.js
PixelWarOnboarding.init();
PixelWarModeManager.toggleMode();
PixelWarColorManager.showEnhancedPalette();
PixelWarTouchFeedback.showSuccessFeedback();
PixelWarNavigation.centerView();

// Utility functions are also global
triggerHapticFeedback('medium');
trackEvent('custom_event', { data: 'value' });
```

## Integration with Pixel War

The mobile modules integrate with the main Pixel War application through:

1. **Global `window.pixelWar` object** - Access to main app instance
2. **DOM elements** - Mobile UI elements with specific IDs
3. **localStorage** - Persistent settings and preferences
4. **Custom events** - Communication between modules
5. **CSS classes** - Visual feedback and animations

## Required DOM Elements

For full functionality, the HTML should include these elements:

```html
<!-- Onboarding overlay -->
<div id="mobileOnboarding">
    <div class="onboarding-step" data-step="1">...</div>
    <div class="onboarding-step" data-step="2">...</div>
    <div class="onboarding-step" data-step="3">...</div>
</div>

<!-- Touch mode indicator -->
<div id="touchModeIndicator">
    <span id="modeIcon">⚡</span>
    <span id="modeLabel">Tap Mode</span>
</div>

<!-- Enhanced color palette -->
<div id="enhancedColorPalette">
    <div id="recentColorsList"></div>
    <div class="color-grid">...</div>
</div>

<!-- Navigation controls -->
<button id="centerViewBtn">Center</button>
<button class="corner-nav-btn" data-corner="top-left">↖</button>
<!-- ... more corner buttons ... -->
```

## CSS Requirements

The modules expect these CSS classes for proper visual feedback:

```css
.touch-feedback.active {
    transform: scale(0.95);
    opacity: 0.8;
}

.haptic-pulse {
    animation: pulse 0.3s ease-in-out;
}

.success-feedback {
    animation: success-flash 0.3s ease-in-out;
}

.error-feedback {
    animation: error-shake 0.3s ease-in-out;
}

.boundary-hit {
    animation: boundary-bounce 0.2s ease-in-out;
}
```

## Testing Functions

Several debug functions are available for testing:

```javascript
// Reset onboarding to test flow again
resetMobileOnboarding();

// Force mobile mode on desktop
forceMobileMode();

// Test navigation system
testMobileNavigation();

// Debug viewport issues
debugMobileViewport();
```

## Analytics Integration

The modules support analytics tracking through the `trackEvent()` function. Events tracked include:
- `onboarding_started` - When onboarding begins
- `onboarding_completed` - When onboarding finishes
- `touch_mode_changed` - When user changes touch mode
- `color_selected` - When user selects a color
- `precision_mode_suggested` - When precision mode is suggested

## Browser Compatibility

- **Haptic Feedback**: Requires `navigator.vibrate()` (modern mobile browsers)
- **Touch Events**: Uses standard touch events with passive listeners
- **LocalStorage**: For persistent settings and preferences
- **ES6 Modules**: Requires modern browser with module support

## Performance Considerations

- Event listeners use passive touch events where possible
- Timeouts and intervals are properly cleaned up
- localStorage operations are wrapped in try-catch blocks
- Heavy operations are debounced or throttled
- DOM queries are cached when possible