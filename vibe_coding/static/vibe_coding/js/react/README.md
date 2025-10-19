# React Pixel Wars Implementation

This directory contains the React-based implementation of the Pixel Wars collaborative art game, designed to work seamlessly with your existing Django backend.

## 🚀 Features

### Desktop React Version (`pixel-wars-react.jsx`)
- **Modern React Architecture**: Built with React 18+ hooks and functional components
- **Canvas-based Rendering**: High-performance HTML5 Canvas with zoom/pan controls
- **Real-time State Management**: Efficient state updates with React hooks
- **Responsive Design**: Works on desktop, tablet, and mobile devices
- **Color Management**: Advanced color palette with custom color support
- **User Authentication**: Integrates with Django's authentication system
- **Rate Limiting**: Respects server-side cooldowns and pixel limits

### Enhanced Mobile Version (`enhanced-pixel-wars-mobile.jsx`)
- **Touch-Optimized**: Built specifically for mobile touch interactions
- **Gesture Support**: Pinch-to-zoom, pan, and precision touch controls
- **Haptic Feedback**: Vibration feedback on supported devices
- **Touch Modes**: 
  - **Tap Mode**: Instant pixel placement for quick drawing
  - **Precision Mode**: Preview-before-place for accurate positioning
- **WebSocket Integration**: Real-time pixel updates from other players
- **Recent Colors**: Smart color history with local storage persistence
- **Offline-Aware**: Graceful handling of connection states

## 🛠 Installation & Setup

### Development Setup (Quick Start)
The current implementation uses CDN-delivered React for quick development:

```html
<!-- Include in your Django template -->
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
```

### Production Setup (Recommended)
For production, compile JSX to regular JavaScript:

1. **Install build dependencies**:
```bash
npm install --save-dev @babel/core @babel/cli @babel/preset-react
```

2. **Create `.babelrc`**:
```json
{
  "presets": ["@babel/preset-react"]
}
```

3. **Build the components**:
```bash
npx babel pixel-wars-react.jsx --out-file pixel-wars-react.js
npx babel enhanced-pixel-wars-mobile.jsx --out-file enhanced-pixel-wars-mobile.js
```

4. **Update templates to use compiled versions**:
```html
<script src="{% static 'vibe_coding/js/react/pixel-wars-react.js' %}"></script>
```

## 📱 Usage

### Basic Integration
```html
<!-- In your Django template -->
<div id="pixel-war-react-root"></div>

<script>
const CANVAS_CONFIG = {
    id: {{ canvas.id }},
    gridWidth: {{ canvas.width }},
    gridHeight: {{ canvas.height }},
    // ... other config
};

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    initReactPixelWar('pixel-war-react-root', CANVAS_CONFIG);
});
</script>
```

### Mobile-Enhanced Version
```html
<div id="mobile-pixel-war-root"></div>

<script>
document.addEventListener('DOMContentLoaded', () => {
    initEnhancedMobilePixelWar('mobile-pixel-war-root', CANVAS_CONFIG);
});
</script>
```

### Auto-Detection Setup
```javascript
// Automatically choose version based on device
document.addEventListener('DOMContentLoaded', () => {
    const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || 
                     window.innerWidth < 768;
    
    if (isMobile) {
        initEnhancedMobilePixelWar('app-root', CANVAS_CONFIG);
    } else {
        initReactPixelWar('app-root', CANVAS_CONFIG);
    }
});
```

## 🏗 Architecture

### Component Structure
```
PixelWarApp (Main App)
├── PixelCanvas (Canvas Rendering)
├── ColorPalette (Color Selection)
├── GameInfo (Stats & Info)
└── Notification (Error Handling)

EnhancedMobilePixelWarApp (Mobile App)
├── MobilePixelCanvas (Touch Canvas)
├── MobileColorPalette (Mobile Palette)
├── MobileGameControls (Touch Controls)
└── Error Handling
```

### Custom Hooks
- **usePixelWar**: Main game state management
- **useMobilePixelWar**: Enhanced mobile state with WebSocket support

### State Management
- **Pixels**: Canvas pixel data (object with coordinates as keys)
- **Selected Color**: Currently active color
- **Pixels Remaining**: Rate limiting state
- **Cooldown**: Timer state for rate limiting
- **Touch Mode**: Mobile interaction mode ('tap' or 'precision')
- **Recent Colors**: User color history

## 🌐 API Integration

### Required Endpoints
The React components expect these Django endpoints:

```python
# views.py
def get_canvas_state(request, canvas_id=None):
    # Return: {'success': True, 'pixels': {}, 'canvas': {}}

def place_pixel(request):
    # POST: {'x': int, 'y': int, 'color': string, 'canvas_id': int}
    # Return: {'success': True, 'pixel': {}, 'cooldown_info': {}}
```

### WebSocket (Optional)
For real-time updates, implement WebSocket endpoint:
```python
# routing.py
websocket_urlpatterns = [
    path('ws/pixel-war/<int:canvas_id>/', PixelWarConsumer.as_asgi()),
]
```

## 📱 Mobile Features

### Touch Gestures
- **Single Tap**: Place pixel (tap mode) or preview (precision mode)
- **Pinch/Zoom**: Zoom in/out on canvas
- **Pan**: Drag to move around canvas
- **Long Press**: Context actions (future feature)

### Performance Optimizations
- **Adaptive Grid**: Fewer grid lines at high zoom levels
- **Touch Action Control**: Prevents browser interference
- **RAF Rendering**: Smooth 60fps canvas updates
- **Memory Management**: Efficient pixel data structures

### Accessibility Features
- **High Contrast Support**: Respects system preferences
- **Reduced Motion**: Disables animations when requested
- **Screen Reader Support**: ARIA labels and roles
- **Keyboard Navigation**: Tab-accessible controls

## 🛠 Customization

### Styling
Modify CSS files:
- `pixel-war-react.css`: Desktop version styles
- `enhanced-pixel-war-mobile.css`: Mobile version styles

### Configuration
Adjust `CANVAS_CONFIG` object:
```javascript
const CANVAS_CONFIG = {
    gridWidth: 100,              // Canvas grid size
    gridHeight: 100,
    pixelSize: 10,               // Pixel size in canvas pixels
    anonymousPixelsPerMinute: 2, // Rate limits
    registeredPixelsPerMinute: 5,
    // ... custom settings
};
```

### Component Customization
React components are modular and can be customized:

```jsx
// Custom color palette
const CustomColorPalette = ({ selectedColor, onColorChange }) => {
    const customColors = ['#FF0000', '#00FF00', '#0000FF'];
    
    return (
        <div className="custom-palette">
            {customColors.map(color => (
                <button 
                    key={color}
                    onClick={() => onColorChange(color)}
                    style={{ backgroundColor: color }}
                />
            ))}
        </div>
    );
};
```

## 🚀 Performance Tips

### Production Optimizations
1. **Bundle React**: Use webpack or similar to bundle dependencies
2. **Minify Code**: Compress JavaScript for faster loading
3. **Enable Gzip**: Compress static files on server
4. **CDN Assets**: Serve static files from CDN
5. **Service Worker**: Cache resources for offline usage

### Runtime Optimizations
1. **Canvas Throttling**: Limit redraw frequency
2. **State Batching**: Batch multiple state updates
3. **Memory Cleanup**: Cleanup event listeners on unmount
4. **WebSocket Reconnection**: Handle connection drops gracefully

## 🐛 Troubleshooting

### Common Issues

**React not loading**
- Check console for CDN errors
- Ensure Babel is loading JSX properly
- Verify no ad blockers are interfering

**Canvas not rendering**
- Check canvas size configuration
- Verify pixel data format matches expected structure
- Ensure container element exists before initialization

**Touch events not working**
- Verify `touch-action: none` is applied
- Check for conflicting event listeners
- Test on actual mobile device (desktop simulation may differ)

**WebSocket connection fails**
- Check WebSocket URL format
- Verify Django channels configuration
- Test with fallback to polling if needed

### Development Tools

**React DevTools**
Install browser extension for component debugging

**Console Commands**
```javascript
// Access global instances
window.pixelWar        // Desktop version
window.mobilePixelWar  // Mobile version

// Debug functions
resetMobileOnboarding()  // Clear saved preferences
forceMobileMode()        // Switch to mobile layout
```

## 📄 License

This React implementation extends the existing Pixel Wars game and follows the same license as the parent project.

## 🤝 Contributing

1. Follow existing code style
2. Test on multiple devices/browsers
3. Update documentation for new features
4. Ensure backward compatibility with Django backend

---

For more information, see the main project documentation or contact the development team.