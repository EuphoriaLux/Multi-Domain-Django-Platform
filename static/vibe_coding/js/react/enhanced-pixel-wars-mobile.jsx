/**
 * Enhanced Mobile React Pixel Wars with Touch Gestures
 * Optimized for mobile devices with touch interactions
 */

// Use React from global scope (loaded via CDN)
const { useState, useEffect, useCallback, useRef } = React;
const { createRoot } = ReactDOM;

// Mobile-optimized hook for pixel war
const useMobilePixelWar = (canvasConfig) => {
    const [pixels, setPixels] = useState({});
    const [selectedColor, setSelectedColor] = useState('#FF0000');
    const [pixelsRemaining, setPixelsRemaining] = useState(0);
    const [cooldownTime, setCooldownTime] = useState(0);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(null);
    const [touchMode, setTouchMode] = useState('tap'); // 'tap' or 'precision'
    const [recentColors, setRecentColors] = useState([]);
    
    // WebSocket for real-time updates (optional - will work without it)
    const wsRef = useRef(null);
    const [connectionStatus, setConnectionStatus] = useState('offline');

    // Initialize WebSocket connection (optional - game works without it)
    useEffect(() => {
        // Skip WebSocket in development or if not configured
        const skipWebSocket = true; // Set to false when WebSocket server is ready
        
        if (skipWebSocket) {
            console.log('WebSocket disabled - running in offline mode');
            setConnectionStatus('offline');
            return;
        }
        
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/pixel-war/${canvasConfig.id}/`;
        
        try {
            wsRef.current = new WebSocket(wsUrl);
            
            wsRef.current.onopen = () => {
                setConnectionStatus('connected');
                console.log('WebSocket connected');
            };
            
            wsRef.current.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'pixel_update') {
                    const { x, y, color, placed_by } = data.pixel;
                    const key = `${x},${y}`;
                    setPixels(prev => ({
                        ...prev,
                        [key]: { color, placed_by, placed_at: new Date().toISOString() }
                    }));
                }
            };
            
            wsRef.current.onclose = () => {
                setConnectionStatus('disconnected');
                console.log('WebSocket disconnected');
            };
            
            wsRef.current.onerror = (error) => {
                setConnectionStatus('error');
                console.warn('WebSocket connection failed - continuing in offline mode');
            };
        } catch (error) {
            console.warn('WebSocket not available - continuing in offline mode');
            setConnectionStatus('offline');
        }

        return () => {
            if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                wsRef.current.close();
            }
        };
    }, [canvasConfig.id]);

    // Load initial canvas state
    useEffect(() => {
        const loadCanvasState = async () => {
            try {
                // API URLs are outside i18n_patterns, so no language prefix
                const response = await fetch(`/vibe-coding/api/canvas-state/${canvasConfig.id}/`);
                const data = await response.json();
                
                if (data.success) {
                    setPixels(data.pixels || {});
                    const initialPixels = canvasConfig.isAuthenticated 
                        ? canvasConfig.registeredPixelsPerMinute 
                        : canvasConfig.anonymousPixelsPerMinute;
                    console.log('Setting initial pixels remaining:', initialPixels);
                    setPixelsRemaining(initialPixels);
                }
                setIsLoading(false);
            } catch (err) {
                setError('Failed to load canvas state');
                setIsLoading(false);
            }
        };

        loadCanvasState();
    }, [canvasConfig]);

    // Load saved preferences
    useEffect(() => {
        const savedMode = localStorage.getItem('pixelWarTouchMode');
        if (savedMode) setTouchMode(savedMode);
        
        const savedColors = localStorage.getItem('pixelWarRecentColors');
        if (savedColors) {
            try {
                setRecentColors(JSON.parse(savedColors));
            } catch (e) {
                console.warn('Failed to parse saved colors');
            }
        }
    }, []);

    // Save preferences
    useEffect(() => {
        localStorage.setItem('pixelWarTouchMode', touchMode);
    }, [touchMode]);

    useEffect(() => {
        localStorage.setItem('pixelWarRecentColors', JSON.stringify(recentColors));
    }, [recentColors]);

    const addToRecentColors = useCallback((color) => {
        setRecentColors(prev => {
            const filtered = prev.filter(c => c !== color);
            return [color, ...filtered].slice(0, 6);
        });
    }, []);

    // Place pixel with mobile optimizations
    const placePixel = useCallback(async (x, y) => {
        console.log(`Attempting to place pixel at (${x}, ${y})`);
        console.log(`Pixels remaining: ${pixelsRemaining}, Cooldown: ${cooldownTime}`);
        
        if (pixelsRemaining <= 0 || cooldownTime > 0) {
            console.log('Cannot place pixel - no pixels remaining or on cooldown');
            return false;
        }

        try {
            // API URLs are outside i18n_patterns, so no language prefix
            const response = await fetch(`/vibe-coding/api/place-pixel/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]')?.value || '',
                },
                body: JSON.stringify({
                    x: x,
                    y: y,
                    color: selectedColor,
                    canvas_id: canvasConfig.id
                })
            });

            const data = await response.json();
            
            if (data.success) {
                // Update local state immediately for responsive feel
                const key = `${x},${y}`;
                setPixels(prev => ({
                    ...prev,
                    [key]: {
                        color: selectedColor,
                        placed_by: data.pixel.placed_by,
                        placed_at: new Date().toISOString()
                    }
                }));
                
                setPixelsRemaining(data.cooldown_info.pixels_remaining);
                addToRecentColors(selectedColor);
                
                // Add haptic feedback on mobile
                if ('vibrate' in navigator) {
                    navigator.vibrate(50);
                }
                
                return true;
            } else {
                setError(data.error);
                setTimeout(() => setError(null), 3000);
                return false;
            }
        } catch (err) {
            setError('Failed to place pixel');
            setTimeout(() => setError(null), 3000);
            return false;
        }
    }, [selectedColor, pixelsRemaining, cooldownTime, canvasConfig.id, addToRecentColors]);

    return {
        pixels,
        selectedColor,
        setSelectedColor,
        pixelsRemaining,
        cooldownTime,
        isLoading,
        error,
        touchMode,
        setTouchMode,
        recentColors,
        connectionStatus,
        placePixel
    };
};

// Enhanced mobile canvas with touch gestures
const MobilePixelCanvas = ({ canvasConfig, pixels, onPixelClick, selectedColor, touchMode }) => {
    const canvasRef = useRef(null);
    const containerRef = useRef(null);
    const [zoom, setZoom] = useState(1);
    const [pan, setPan] = useState({ x: 0, y: 0 });
    const [previewPixel, setPreviewPixel] = useState(null);
    
    // Touch gesture state
    const [touchState, setTouchState] = useState({
        touches: [],
        lastDistance: 0,
        isPanning: false,
        isZooming: false
    });

    const pixelSize = Math.floor(1000 / canvasConfig.gridWidth);

    // Touch event handlers
    const handleTouchStart = useCallback((e) => {
        const touches = Array.from(e.touches).map(touch => ({
            id: touch.identifier,
            x: touch.clientX,
            y: touch.clientY
        }));
        
        if (touches.length === 2) {
            // Two finger gesture - zoom
            e.preventDefault();
            const distance = Math.sqrt(
                Math.pow(touches[1].x - touches[0].x, 2) +
                Math.pow(touches[1].y - touches[0].y, 2)
            );
            setTouchState(prev => ({ 
                ...prev, 
                touches, 
                lastDistance: distance, 
                isZooming: true,
                isPanning: false 
            }));
        } else if (touches.length === 1) {
            // Single finger - store initial position
            setTouchState(prev => ({ 
                ...prev, 
                touches,
                isZooming: false 
            }));
        }
    }, []);

    const handleTouchMove = useCallback((e) => {
        const touches = Array.from(e.touches).map(touch => ({
            id: touch.identifier,
            x: touch.clientX,
            y: touch.clientY
        }));

        if (touches.length === 2 && touchState.isZooming) {
            // Zoom gesture
            e.preventDefault();
            const distance = Math.sqrt(
                Math.pow(touches[1].x - touches[0].x, 2) +
                Math.pow(touches[1].y - touches[0].y, 2)
            );
            
            if (touchState.lastDistance > 0) {
                const scale = distance / touchState.lastDistance;
                // More responsive zoom with larger range
                setZoom(prev => {
                    const newZoom = prev * scale;
                    return Math.max(0.3, Math.min(5, newZoom));
                });
            }
            setTouchState(prev => ({ ...prev, lastDistance: distance, touches }));
        } else if (touches.length === 1 && touchState.touches.length === 1 && !touchState.isZooming) {
            // Pan gesture - check if user has moved enough to be considered panning
            const deltaX = touches[0].x - touchState.touches[0].x;
            const deltaY = touches[0].y - touchState.touches[0].y;
            const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);
            
            // Start panning if moved more than 5 pixels
            if (distance > 5 || touchState.isPanning) {
                e.preventDefault();
                setPan(prev => ({
                    x: prev.x + deltaX / zoom,
                    y: prev.y + deltaY / zoom
                }));
                setTouchState(prev => ({ ...prev, isPanning: true, touches }));
            }
        }
    }, [touchState, zoom]);

    const handleTouchEnd = useCallback((e) => {
        console.log('Touch end event', { 
            isPanning: touchState.isPanning, 
            isZooming: touchState.isZooming, 
            touchCount: touchState.touches.length 
        });
        
        // Only prevent default if we were zooming or panning
        if (touchState.isZooming || touchState.isPanning) {
            e.preventDefault();
        }
        
        // If it was a tap (not pan or zoom), handle pixel placement
        if (!touchState.isPanning && !touchState.isZooming && touchState.touches.length === 1) {
            const canvas = canvasRef.current;
            const rect = canvas.getBoundingClientRect();
            const touch = touchState.touches[0];
            
            console.log('Processing tap at', touch);
            
            // Convert touch coordinates to canvas coordinates
            // First get position relative to canvas element
            const relativeX = touch.x - rect.left;
            const relativeY = touch.y - rect.top;
            
            // Account for canvas scaling (canvas matches grid size but may be displayed different)
            const canvasScaleX = (canvasConfig.gridWidth * canvasConfig.pixelSize) / rect.width;
            const canvasScaleY = (canvasConfig.gridHeight * canvasConfig.pixelSize) / rect.height;
            
            // Convert to canvas pixel coordinates accounting for zoom and pan
            const canvasX = (relativeX * canvasScaleX) / zoom - pan.x;
            const canvasY = (relativeY * canvasScaleY) / zoom - pan.y;
            
            // Convert to grid coordinates
            const gridX = Math.floor(canvasX / pixelSize);
            const gridY = Math.floor(canvasY / pixelSize);
            
            console.log('Coordinate transform:', {
                touch: { x: touch.x, y: touch.y },
                relative: { x: relativeX, y: relativeY },
                canvas: { x: canvasX, y: canvasY },
                grid: { x: gridX, y: gridY },
                zoom, pan, pixelSize
            });
            
            if (gridX >= 0 && gridX < canvasConfig.gridWidth && 
                gridY >= 0 && gridY < canvasConfig.gridHeight) {
                
                if (touchMode === 'precision') {
                    // In precision mode, just set preview - user will confirm with button
                    setPreviewPixel({ x: gridX, y: gridY });
                    // Haptic feedback for selection
                    if ('vibrate' in navigator) {
                        navigator.vibrate(10);
                    }
                } else {
                    // Tap mode - place immediately
                    onPixelClick(gridX, gridY);
                }
            }
        }
        
        // Reset touch state
        setTouchState({
            touches: [],
            lastDistance: 0,
            isPanning: false,
            isZooming: false
        });
        
        // Clear hover pixel
        setHoverPixel(null);
    }, [touchState, zoom, pan, pixelSize, canvasConfig, touchMode, previewPixel, onPixelClick]);

    // Add hover pixel state
    const [hoverPixel, setHoverPixel] = useState(null);
    
    // Handle touch move for hover effect
    const handleTouchMoveHover = useCallback((e) => {
        if (touchState.isZooming || touchState.isPanning) return;
        
        const canvas = canvasRef.current;
        const rect = canvas.getBoundingClientRect();
        const touch = e.touches[0];
        
        const relativeX = touch.clientX - rect.left;
        const relativeY = touch.clientY - rect.top;
        const canvasScaleX = (canvasConfig.gridWidth * canvasConfig.pixelSize) / rect.width;
        const canvasScaleY = (canvasConfig.gridHeight * canvasConfig.pixelSize) / rect.height;
        
        const canvasX = (relativeX * canvasScaleX) / zoom - pan.x;
        const canvasY = (relativeY * canvasScaleY) / zoom - pan.y;
        
        const gridX = Math.floor(canvasX / pixelSize);
        const gridY = Math.floor(canvasY / pixelSize);
        
        if (gridX >= 0 && gridX < canvasConfig.gridWidth && 
            gridY >= 0 && gridY < canvasConfig.gridHeight) {
            setHoverPixel({ x: gridX, y: gridY });
        } else {
            setHoverPixel(null);
        }
    }, [touchState, zoom, pan, pixelSize, canvasConfig]);
    
    // Draw canvas
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        const { gridWidth, gridHeight } = canvasConfig;
        
        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Apply transform
        ctx.save();
        ctx.scale(zoom, zoom);
        ctx.translate(pan.x, pan.y);
        
        // Draw grid (lighter on mobile for performance)
        ctx.strokeStyle = '#eee';
        ctx.lineWidth = 0.5 / zoom;
        
        // Draw fewer grid lines on high zoom for performance
        const gridStep = zoom > 2 ? 1 : Math.ceil(2 / zoom);
        
        for (let x = 0; x <= gridWidth; x += gridStep) {
            ctx.beginPath();
            ctx.moveTo(x * pixelSize, 0);
            ctx.lineTo(x * pixelSize, gridHeight * pixelSize);
            ctx.stroke();
        }
        
        for (let y = 0; y <= gridHeight; y += gridStep) {
            ctx.beginPath();
            ctx.moveTo(0, y * pixelSize);
            ctx.lineTo(gridWidth * pixelSize, y * pixelSize);
            ctx.stroke();
        }
        
        // Draw pixels
        Object.entries(pixels).forEach(([key, pixel]) => {
            const [x, y] = key.split(',').map(Number);
            ctx.fillStyle = pixel.color;
            ctx.fillRect(x * pixelSize, y * pixelSize, pixelSize, pixelSize);
        });
        
        // Draw hover pixel (shows where tap will place)
        if (hoverPixel && !touchState.isPanning && !touchState.isZooming) {
            ctx.fillStyle = selectedColor;
            ctx.globalAlpha = 0.3;
            ctx.fillRect(hoverPixel.x * pixelSize, hoverPixel.y * pixelSize, pixelSize, pixelSize);
            ctx.globalAlpha = 1;
            
            // Draw border
            ctx.strokeStyle = selectedColor;
            ctx.lineWidth = 1 / zoom;
            ctx.strokeRect(hoverPixel.x * pixelSize, hoverPixel.y * pixelSize, pixelSize, pixelSize);
        }
        
        // Draw preview pixel (in precision mode)
        if (previewPixel) {
            ctx.fillStyle = selectedColor;
            ctx.globalAlpha = 0.7;
            ctx.fillRect(previewPixel.x * pixelSize, previewPixel.y * pixelSize, pixelSize, pixelSize);
            ctx.globalAlpha = 1;
            
            // Draw border for preview
            ctx.strokeStyle = '#333';
            ctx.lineWidth = 2 / zoom;
            ctx.strokeRect(previewPixel.x * pixelSize, previewPixel.y * pixelSize, pixelSize, pixelSize);
        }
        
        ctx.restore();
    }, [pixels, zoom, pan, canvasConfig, pixelSize, previewPixel, selectedColor, hoverPixel, touchState.isPanning, touchState.isZooming]);

    return (
        <div 
            ref={containerRef}
            className="mobile-canvas-container"
            style={{ touchAction: 'none' }}
        >
            <canvas
                ref={canvasRef}
                width={canvasConfig.gridWidth * canvasConfig.pixelSize}
                height={canvasConfig.gridHeight * canvasConfig.pixelSize}
                onTouchStart={handleTouchStart}
                onTouchMove={handleTouchMove}
                onTouchEnd={handleTouchEnd}
                style={{
                    border: '2px solid #333',
                    borderRadius: '8px',
                    maxWidth: '100%',
                    height: 'auto',
                    touchAction: 'none'
                }}
            />
            
            <div className="mobile-canvas-info">
                <div className="zoom-level">Zoom: {Math.round(zoom * 100)}%</div>
                <div className="mode-indicator">
                    Mode: {touchMode === 'tap' ? '⚡ Tap' : '🎯 Precision'}
                </div>
            </div>
            
            {/* Pixel Confirmation Panel for Precision Mode */}
            {previewPixel && touchMode === 'precision' && (
                <div className="pixel-confirmation-panel">
                    <div className="confirmation-info">
                        <span className="pixel-coords">
                            📍 Position: ({previewPixel.x}, {previewPixel.y})
                        </span>
                        <div className="selected-color-preview" 
                             style={{ backgroundColor: selectedColor }}>
                        </div>
                    </div>
                    <div className="confirmation-buttons">
                        <button 
                            className="confirm-btn"
                            onClick={() => {
                                onPixelClick(previewPixel.x, previewPixel.y);
                                setPreviewPixel(null);
                                if ('vibrate' in navigator) {
                                    navigator.vibrate(50);
                                }
                            }}
                        >
                            ✅ Place Pixel
                        </button>
                        <button 
                            className="cancel-btn"
                            onClick={() => {
                                setPreviewPixel(null);
                            }}
                        >
                            ❌ Cancel
                        </button>
                    </div>
                </div>
            )}
            
            {/* Zoom Controls */}
            <div className="mobile-zoom-controls">
                <button 
                    className="zoom-btn"
                    onClick={() => setZoom(prev => Math.min(5, prev * 1.5))}
                >
                    +
                </button>
                <button 
                    className="zoom-btn"
                    onClick={() => setZoom(prev => Math.max(0.3, prev / 1.5))}
                >
                    −
                </button>
                <button 
                    className="zoom-btn reset"
                    onClick={() => {
                        setZoom(1);
                        setPan({ x: 0, y: 0 });
                    }}
                >
                    ⟲
                </button>
            </div>
        </div>
    );
};

// Mobile-optimized color palette
const MobileColorPalette = ({ selectedColor, onColorChange, recentColors, isVisible, onClose }) => {
    const quickColors = [
        '#FF0000', '#FF8000', '#FFFF00', '#80FF00', '#00FF00', '#00FF80',
        '#00FFFF', '#0080FF', '#0000FF', '#8000FF', '#FF00FF', '#FF0080',
        '#000000', '#404040', '#808080', '#C0C0C0', '#FFFFFF', '#8B4513'
    ];
    
    const handleColorSelect = useCallback((color) => {
        onColorChange(color);
        // Haptic feedback
        if ('vibrate' in navigator) {
            navigator.vibrate(10);
        }
        // Auto-close after selection
        setTimeout(() => onClose(), 300);
    }, [onColorChange, onClose]);
    
    const handleBackdropClick = useCallback((e) => {
        // Close if clicking outside the palette content
        if (e.target.classList.contains('mobile-color-palette')) {
            onClose();
        }
    }, [onClose]);

    return (
        <div 
            className={`mobile-color-palette ${isVisible ? 'visible' : ''}`}
            onClick={handleBackdropClick}
        >
            <div className="palette-content">
                <div className="palette-header">
                    <h3>Choose Color</h3>
                    <button 
                        className="close-btn" 
                        onClick={onClose}
                        aria-label="Close color palette"
                    >
                        ×
                    </button>
                </div>
                
                {recentColors.length > 0 && (
                    <div className="recent-colors-section">
                        <h4>Recent Colors</h4>
                        <div className="color-row">
                            {recentColors.map((color, index) => (
                                <button
                                    key={`recent-${index}`}
                                    className={`color-btn ${selectedColor === color ? 'selected' : ''}`}
                                    style={{ backgroundColor: color }}
                                    onClick={() => handleColorSelect(color)}
                                    aria-label={`Recent color ${index + 1}`}
                                />
                            ))}
                        </div>
                    </div>
                )}
                
                <div className="quick-colors-section">
                    <h4>Quick Colors</h4>
                    <div className="color-grid">
                        {quickColors.map(color => (
                            <button
                                key={color}
                                className={`color-btn ${selectedColor === color ? 'selected' : ''}`}
                                style={{ backgroundColor: color }}
                                onClick={() => handleColorSelect(color)}
                                aria-label={`Color ${color}`}
                            />
                        ))}
                    </div>
                </div>
                
                <div className="current-color-display">
                    <h4>Current Selection</h4>
                    <div className="current-color-info">
                        <div 
                            className="current-color-preview"
                            style={{ backgroundColor: selectedColor }}
                        />
                        <span className="current-color-hex">{selectedColor}</span>
                    </div>
                </div>
                
                <div className="custom-color-section">
                    <h4>Custom Color</h4>
                    <div className="custom-color-wrapper">
                        <input
                            type="color"
                            value={selectedColor}
                            onChange={(e) => handleColorSelect(e.target.value)}
                            className="custom-color-picker"
                            aria-label="Custom color picker"
                        />
                        <label>Tap to choose custom color</label>
                    </div>
                </div>
            </div>
        </div>
    );
};

// Mobile game controls
const MobileGameControls = ({ 
    canvasConfig, 
    pixelsRemaining, 
    cooldownTime, 
    selectedColor, 
    touchMode, 
    onTouchModeChange,
    connectionStatus,
    onColorPaletteToggle 
}) => {
    return (
        <div className="mobile-game-controls">
            <div className="status-bar">
                <div className="connection-status">
                    <span className={`status-dot ${connectionStatus}`}></span>
                    {connectionStatus === 'connected' && 'Live'}
                    {connectionStatus === 'offline' && 'Offline'}
                    {connectionStatus === 'disconnected' && 'Reconnecting...'}
                    {connectionStatus === 'error' && 'Offline'}
                </div>
                
                <div className="pixel-counter">
                    <span className="pixels-remaining">{pixelsRemaining}</span>
                    <span className="pixels-label">left</span>
                </div>
            </div>
            
            <div className="control-bar">
                <button 
                    className="color-btn-preview"
                    onClick={onColorPaletteToggle}
                    style={{ backgroundColor: selectedColor }}
                >
                    <span className="btn-label">Color</span>
                </button>
                
                <button 
                    className={`mode-btn ${touchMode}`}
                    onClick={onTouchModeChange}
                    title={touchMode === 'tap' 
                        ? 'Tap Mode: Place pixels instantly with one tap' 
                        : 'Precision Mode: Preview and confirm before placing'}
                >
                    <span className="mode-icon">
                        {touchMode === 'tap' ? '⚡' : '🎯'}
                    </span>
                    <div className="mode-text">
                        <span className="mode-label">
                            {touchMode === 'tap' ? 'Tap' : 'Precision'}
                        </span>
                        <span className="mode-desc">
                            {touchMode === 'tap' ? 'Instant' : 'Preview'}
                        </span>
                    </div>
                </button>
                
                <div className="cooldown-indicator">
                    {cooldownTime > 0 ? `${cooldownTime}s` : 'Ready'}
                </div>
            </div>
        </div>
    );
};

// Main Enhanced Mobile Pixel War App
const EnhancedMobilePixelWarApp = ({ canvasConfig }) => {
    const {
        pixels,
        selectedColor,
        setSelectedColor,
        pixelsRemaining,
        cooldownTime,
        isLoading,
        error,
        touchMode,
        setTouchMode,
        recentColors,
        connectionStatus,
        placePixel
    } = useMobilePixelWar(canvasConfig);

    const [showColorPalette, setShowColorPalette] = useState(false);

    const handleTouchModeToggle = useCallback(() => {
        setTouchMode(prev => prev === 'tap' ? 'precision' : 'tap');
    }, [setTouchMode]);

    if (isLoading) {
        return (
            <div className="mobile-loading">
                <div className="loading-content">
                    <div className="loading-spinner"></div>
                    <h2>Loading Pixel War...</h2>
                </div>
            </div>
        );
    }

    return (
        <div className="enhanced-mobile-pixel-war">
            <MobileGameControls
                canvasConfig={canvasConfig}
                pixelsRemaining={pixelsRemaining}
                cooldownTime={cooldownTime}
                selectedColor={selectedColor}
                touchMode={touchMode}
                onTouchModeChange={handleTouchModeToggle}
                connectionStatus={connectionStatus}
                onColorPaletteToggle={() => setShowColorPalette(true)}
            />

            <MobilePixelCanvas
                canvasConfig={canvasConfig}
                pixels={pixels}
                onPixelClick={placePixel}
                selectedColor={selectedColor}
                touchMode={touchMode}
            />

            <MobileColorPalette
                selectedColor={selectedColor}
                onColorChange={setSelectedColor}
                recentColors={recentColors}
                isVisible={showColorPalette}
                onClose={() => setShowColorPalette(false)}
            />

            {error && (
                <div className="error-toast">
                    {error}
                </div>
            )}
        </div>
    );
};

// Auto-detection and initialization
const initEnhancedMobilePixelWar = (containerId, canvasConfig) => {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`Container ${containerId} not found`);
        return;
    }

    const root = createRoot(container);
    root.render(<EnhancedMobilePixelWarApp canvasConfig={canvasConfig} />);
    
    console.log('✅ Enhanced Mobile Pixel War initialized');
    return root;
};

// Export for global usage
if (typeof window !== 'undefined') {
    window.initEnhancedMobilePixelWar = initEnhancedMobilePixelWar;
    window.EnhancedMobilePixelWarApp = EnhancedMobilePixelWarApp;
    window.MobilePixelCanvas = MobilePixelCanvas;
    window.MobileColorPalette = MobileColorPalette;
    window.MobileGameControls = MobileGameControls;
}