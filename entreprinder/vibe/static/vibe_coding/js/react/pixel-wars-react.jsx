/**
 * React-based Pixel Wars Game
 * Modern React implementation with hooks and components
 */

// Use React from global scope (loaded via CDN)
const { useState, useEffect, useCallback, useRef } = React;
const { createRoot } = ReactDOM;

// Utility functions
const API_ENDPOINTS = {
    // API URLs are outside i18n_patterns, so no language prefix
    getCanvasState: (canvasId) => `/vibe-coding/api/canvas-state/${canvasId}/`,
    placePixel: () => `/vibe-coding/api/place-pixel/`,
    getHistory: () => `/vibe-coding/api/pixel-history/`
};

// Custom hooks
const usePixelWar = (canvasConfig) => {
    const [pixels, setPixels] = useState({});
    const [selectedColor, setSelectedColor] = useState('#000000');
    const [pixelsRemaining, setPixelsRemaining] = useState(0);
    const [cooldownTime, setCooldownTime] = useState(0);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(null);

    // Load initial canvas state
    useEffect(() => {
        const loadCanvasState = async () => {
            try {
                const response = await fetch(API_ENDPOINTS.getCanvasState(canvasConfig.id));
                const data = await response.json();
                
                if (data.success) {
                    setPixels(data.pixels);
                    setPixelsRemaining(canvasConfig.isAuthenticated 
                        ? canvasConfig.registeredPixelsPerMinute 
                        : canvasConfig.anonymousPixelsPerMinute
                    );
                }
                setIsLoading(false);
            } catch (err) {
                setError('Failed to load canvas state');
                setIsLoading(false);
            }
        };

        loadCanvasState();
    }, [canvasConfig]);

    // Place pixel function
    const placePixel = useCallback(async (x, y) => {
        if (pixelsRemaining <= 0 || cooldownTime > 0) return;

        try {
            const response = await fetch(API_ENDPOINTS.placePixel(), {
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
                // Update local state
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
                
                // Start cooldown if applicable
                if (data.cooldown_info.cooldown_seconds > 0) {
                    setCooldownTime(data.cooldown_info.cooldown_seconds);
                    const countdown = setInterval(() => {
                        setCooldownTime(prev => {
                            if (prev <= 1) {
                                clearInterval(countdown);
                                return 0;
                            }
                            return prev - 1;
                        });
                    }, 1000);
                }
            } else {
                setError(data.error);
                setTimeout(() => setError(null), 3000);
            }
        } catch (err) {
            setError('Failed to place pixel');
            setTimeout(() => setError(null), 3000);
        }
    }, [selectedColor, pixelsRemaining, cooldownTime, canvasConfig.id]);

    return {
        pixels,
        selectedColor,
        setSelectedColor,
        pixelsRemaining,
        cooldownTime,
        isLoading,
        error,
        placePixel
    };
};

// Canvas component with zoom and pan
const PixelCanvas = ({ canvasConfig, pixels, onPixelClick, selectedColor }) => {
    const canvasRef = useRef(null);
    const [zoom, setZoom] = useState(1);
    const [pan, setPan] = useState({ x: 0, y: 0 });
    const [isDragging, setIsDragging] = useState(false);
    const [lastMousePos, setLastMousePos] = useState({ x: 0, y: 0 });

    const pixelSize = Math.floor(1000 / canvasConfig.gridWidth);

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
        
        // Draw grid
        ctx.strokeStyle = '#ddd';
        ctx.lineWidth = 1 / zoom;
        
        for (let x = 0; x <= gridWidth; x++) {
            ctx.beginPath();
            ctx.moveTo(x * pixelSize, 0);
            ctx.lineTo(x * pixelSize, gridHeight * pixelSize);
            ctx.stroke();
        }
        
        for (let y = 0; y <= gridHeight; y++) {
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
        
        ctx.restore();
    }, [pixels, zoom, pan, canvasConfig, pixelSize]);

    // Handle canvas click
    const handleCanvasClick = useCallback((e) => {
        const canvas = canvasRef.current;
        const rect = canvas.getBoundingClientRect();
        
        const canvasX = (e.clientX - rect.left) / zoom - pan.x;
        const canvasY = (e.clientY - rect.top) / zoom - pan.y;
        
        const gridX = Math.floor(canvasX / pixelSize);
        const gridY = Math.floor(canvasY / pixelSize);
        
        if (gridX >= 0 && gridX < canvasConfig.gridWidth && 
            gridY >= 0 && gridY < canvasConfig.gridHeight) {
            onPixelClick(gridX, gridY);
        }
    }, [zoom, pan, pixelSize, canvasConfig, onPixelClick]);

    // Handle mouse events for panning
    const handleMouseDown = useCallback((e) => {
        if (e.button === 1 || e.ctrlKey) { // Middle mouse or Ctrl+click for panning
            setIsDragging(true);
            setLastMousePos({ x: e.clientX, y: e.clientY });
            e.preventDefault();
        }
    }, []);

    const handleMouseMove = useCallback((e) => {
        if (isDragging) {
            const deltaX = e.clientX - lastMousePos.x;
            const deltaY = e.clientY - lastMousePos.y;
            
            setPan(prev => ({
                x: prev.x + deltaX / zoom,
                y: prev.y + deltaY / zoom
            }));
            
            setLastMousePos({ x: e.clientX, y: e.clientY });
        }
    }, [isDragging, lastMousePos, zoom]);

    const handleMouseUp = useCallback(() => {
        setIsDragging(false);
    }, []);

    // Handle wheel for zooming
    const handleWheel = useCallback((e) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        setZoom(prev => Math.max(0.1, Math.min(5, prev * delta)));
    }, []);

    return (
        <div className="pixel-canvas-container">
            <canvas
                ref={canvasRef}
                width={1000}
                height={1000}
                onClick={handleCanvasClick}
                onMouseDown={handleMouseDown}
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
                onWheel={handleWheel}
                style={{
                    border: '2px solid #333',
                    cursor: isDragging ? 'grabbing' : 'crosshair',
                    maxWidth: '100%',
                    height: 'auto'
                }}
            />
            <div className="canvas-controls">
                <button onClick={() => setZoom(prev => Math.min(5, prev * 1.2))}>
                    Zoom In
                </button>
                <button onClick={() => setZoom(prev => Math.max(0.1, prev / 1.2))}>
                    Zoom Out
                </button>
                <button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>
                    Reset View
                </button>
                <span>Zoom: {Math.round(zoom * 100)}%</span>
            </div>
        </div>
    );
};

// Color palette component
const ColorPalette = ({ selectedColor, onColorChange }) => {
    const defaultColors = [
        '#FF0000', '#FFA500', '#FFFF00', '#00FF00', '#00FFFF', 
        '#0000FF', '#FF00FF', '#800080', '#FFC0CB', '#A52A2A',
        '#808080', '#90EE90', '#FFD700', '#4169E1', '#FFFFFF', '#000000'
    ];

    return (
        <div className="color-palette">
            <h3>Choose Color</h3>
            <div className="color-grid">
                {defaultColors.map(color => (
                    <button
                        key={color}
                        className={`color-btn ${selectedColor === color ? 'selected' : ''}`}
                        style={{ 
                            backgroundColor: color,
                            border: selectedColor === color ? '3px solid #333' : '1px solid #ccc'
                        }}
                        onClick={() => onColorChange(color)}
                        title={color}
                    />
                ))}
            </div>
            <div className="custom-color">
                <label>Custom: </label>
                <input
                    type="color"
                    value={selectedColor}
                    onChange={(e) => onColorChange(e.target.value)}
                />
            </div>
        </div>
    );
};

// Game info component
const GameInfo = ({ canvasConfig, pixelsRemaining, cooldownTime, selectedColor }) => {
    return (
        <div className="game-info">
            <div className="info-card">
                <h3>Canvas Info</h3>
                <p>Size: {canvasConfig.gridWidth} × {canvasConfig.gridHeight}</p>
                <p>Status: <span className="status-active">Active</span></p>
                
                <div className="cooldown-info">
                    <h4>Pixel Limits</h4>
                    {canvasConfig.isAuthenticated ? (
                        <div className="user-tier registered">
                            <span className="tier-badge">Member</span>
                            <strong>{canvasConfig.registeredPixelsPerMinute} pixels/min</strong>
                        </div>
                    ) : (
                        <div className="user-tier anonymous">
                            <span className="tier-badge">Guest</span>
                            <strong>{canvasConfig.anonymousPixelsPerMinute} pixels/min</strong>
                        </div>
                    )}
                </div>
            </div>
            
            <div className="info-card">
                <h3>Your Stats</h3>
                <p>Color: <span 
                    style={{ 
                        display: 'inline-block', 
                        width: '20px', 
                        height: '20px', 
                        backgroundColor: selectedColor,
                        border: '1px solid #ccc',
                        borderRadius: '4px'
                    }}
                /></p>
                <p>Next in: <strong>
                    {cooldownTime > 0 ? `${cooldownTime}s` : 'Ready'}
                </strong></p>
                <p>Remaining: <strong>{pixelsRemaining}</strong></p>
            </div>
        </div>
    );
};

// Notification component
const Notification = ({ error }) => {
    if (!error) return null;
    
    return (
        <div className="notification error" role="alert">
            {error}
        </div>
    );
};

// Main Pixel Wars React App
const PixelWarApp = ({ canvasConfig }) => {
    const {
        pixels,
        selectedColor,
        setSelectedColor,
        pixelsRemaining,
        cooldownTime,
        isLoading,
        error,
        placePixel
    } = usePixelWar(canvasConfig);

    if (isLoading) {
        return (
            <div className="pixel-war-loading">
                <h2>Loading Pixel War...</h2>
                <div className="loading-spinner"></div>
            </div>
        );
    }

    return (
        <div className="pixel-war-react">
            <header className="pixel-war-header">
                <h1>🎨 Lux Pixel War (React)</h1>
                <p className="subtitle">Collaborate or compete - Every pixel counts!</p>
            </header>

            <div className="pixel-war-layout">
                <div className="controls-sidebar">
                    <ColorPalette 
                        selectedColor={selectedColor}
                        onColorChange={setSelectedColor}
                    />
                    <GameInfo
                        canvasConfig={canvasConfig}
                        pixelsRemaining={pixelsRemaining}
                        cooldownTime={cooldownTime}
                        selectedColor={selectedColor}
                    />
                </div>

                <div className="main-content">
                    <PixelCanvas
                        canvasConfig={canvasConfig}
                        pixels={pixels}
                        onPixelClick={placePixel}
                        selectedColor={selectedColor}
                    />
                </div>
            </div>

            <Notification error={error} />
        </div>
    );
};

// Initialize React app
const initReactPixelWar = (containerId, canvasConfig) => {
    const container = document.getElementById(containerId);
    if (!container) {
        console.error(`Container ${containerId} not found`);
        return;
    }

    const root = createRoot(container);
    root.render(<PixelWarApp canvasConfig={canvasConfig} />);
    
    console.log('✅ React Pixel War initialized');
    return root;
};

// Auto-initialization
if (typeof window !== 'undefined') {
    window.initReactPixelWar = initReactPixelWar;
    window.PixelWarApp = PixelWarApp;
    window.PixelCanvas = PixelCanvas;
    window.ColorPalette = ColorPalette;
    window.GameInfo = GameInfo;
    window.Notification = Notification;
    
    // Auto-init if container exists
    document.addEventListener('DOMContentLoaded', () => {
        const container = document.getElementById('pixel-war-react-root');
        if (container && window.CANVAS_CONFIG) {
            initReactPixelWar('pixel-war-react-root', window.CANVAS_CONFIG);
        }
    });
}